# -*- coding: utf-8 -*-
"""Замер: локальная модель рядом с S.T.A.L.K.E.R. 2 на одной машине.

Вопрос автора (04.10.2026): какая модель уживётся с игрой на RTX 3090, чтобы
игре было комфортно. Скрипт проходит этапы, на каждом пишет датчики:

  кадры игры     RTSS, общая память RTSSSharedMemoryV2 (RTSS должен быть
                 запущен ДО игры или подхватить её; без него столбцы пустые)
  видеопамять    nvidia-smi: занято МиБ, загрузка ядра %
  процессор      GetSystemTimes: загрузка всех ядер %
  модель         время ответа, токены, токены в секунду (llama-server, OpenAI API)

Этапы (можно переопределить --phases):
  base       только игра, модели выгружены
  gpu4idle   4B на видеокарте, молчит (сколько памяти съела)
  gpu4       4B на видеокарте, запросы без перерыва (худший случай)
  cpu4       4B на процессоре, запросы без перерыва
  gpu9       9B на видеокарте, запросы без перерыва
  end        снова только игра (не уплыла ли картина за время замера)

Запросы двух видов, по очереди:
  director   режиссёр: обстановка в JSON (около 700 токенов) -> команда из
             белого списка по схеме JSON (response_format json_schema)
  bark       реплика сталкера по-русски, одна строка

Сервер: llama-server из комплекта LM Studio, порт 8081, контекст 8192, один
слот, рассуждения выключены флагом --reasoning-budget 0 (у службы LM Studio
для того же нужен reasoning_effort "none", память qwen3-reasoning-effort-none).

Запуск:  python measure.py            (все этапы, итог в конце)
         python measure.py --phases base,gpu4 --seconds 30
Пишет в <NESTOR_OUT или папка рядом>\\measure-<время>\\: monitor.csv, llm.csv, summary.txt

Инструмент разработчика, в nestor.exe не входит. Пути к LM Studio от домашней папки
пользователя, модели из NESTOR_MODELS (папка с Qwen3.5-*-GGUF\\*.gguf).
"""
import argparse
import ctypes
import json
import mmap
import os
import statistics
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")

LMSTUDIO = os.path.join(os.path.expanduser("~"), ".lmstudio")
LMS = os.path.join(LMSTUDIO, "bin", "lms.exe")
# Движок: llama-server из комплекта LM Studio (llama.cpp, сборка 2.51.0 под CUDA 12).
# Саму службу LM Studio не берём: она навязывает контекст на всю свободную
# видеопамять (см. load_model ниже). llama-server слушается флагов, и его же
# положим в лаунчер мода.
SERVER = os.path.join(LMSTUDIO, "extensions", "backends", "llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.51.0",
                      "llama-server.exe")
CUDA_DLLS = os.path.join(LMSTUDIO, "extensions", "backends", "vendor", "win-llama-cuda12-vendor-v2")
PORT = 8081
API = "http://127.0.0.1:%d/v1/chat/completions" % PORT
MODELS_DIR = os.environ.get("NESTOR_MODELS", os.path.join(os.path.expanduser("~"), "models", "local-qwen"))
MODELS = {
    "4b": os.path.join(MODELS_DIR, "Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf"),
    "9b": os.path.join(MODELS_DIR, "Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf"),
}
GAME_EXE = "stalker2-win64-shipping.exe"
OUT_ROOT = os.environ.get("NESTOR_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "measure-out"))

# этап: (модель, слоёв на видеокарте, потоков процессора, гонять ли запросы)
#   99 слоёв = вся модель на видеокарте; 0 = всё на процессоре (4 потока из 8,
#   половина ядер i7-9700: игра и так грузит процессор на 3/4)
PHASES = {
    "base": (None, None, None, False),
    "gpu4idle": ("4b", 99, None, False),
    "gpu4": ("4b", 99, None, True),
    "cpu4": ("4b", 0, 4, True),
    "gpu9": ("9b", 99, None, True),
    "end": (None, None, None, False),
}

# ---------------------------------------------------------------- датчики

def rtss_fps(game=GAME_EXE):
    """Кадры игры из общей памяти RTSS: (кадров/с, кадр мс) или (None, None)."""
    try:
        head = mmap.mmap(-1, 36, tagname="RTSSSharedMemoryV2", access=mmap.ACCESS_READ)
    except OSError:
        return None, None
    sig, ver, esize, aoff, acount = struct.unpack_from("<5I", head, 0)
    head.close()
    if sig not in (0x52545353, 0x53535452) or not esize or not acount:
        return None, None
    try:
        mm = mmap.mmap(-1, aoff + esize * acount, tagname="RTSSSharedMemoryV2", access=mmap.ACCESS_READ)
    except OSError:
        return None, None
    try:
        for i in range(acount):
            base = aoff + i * esize
            pid = struct.unpack_from("<I", mm, base)[0]
            if not pid:
                continue
            name = mm[base + 4: base + 264].split(b"\0", 1)[0].decode("mbcs", "replace").lower()
            if not name.endswith(game):
                continue
            t0, t1, frames, ftime = struct.unpack_from("<4I", mm, base + 268)
            fps = 1000.0 * frames / (t1 - t0) if t1 > t0 else None
            return fps, (ftime / 1000.0 if ftime else None)
    finally:
        mm.close()
    return None, None


def gpu_now():
    """Видеопамять (МиБ) и загрузка ядра (%) по nvidia-smi."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        mem, util = [int(x) for x in out.split(",")]
        return mem, util
    except Exception:
        return None, None


class CpuMeter:
    """Загрузка процессора между двумя вызовами, по GetSystemTimes."""

    def __init__(self):
        self.prev = self._times()

    @staticmethod
    def _times():
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        f = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
        return f(idle), f(kern), f(user)

    def read(self):
        cur = self._times()
        di, dk, du = (c - p for c, p in zip(cur, self.prev))
        self.prev = cur
        total = dk + du  # kernel включает idle
        return round(100.0 * (total - di) / total, 1) if total else None


# ---------------------------------------------------------------- модель

def lms(*args, timeout=180):
    r = subprocess.run([LMS, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return (r.stdout + r.stderr).strip()


def start_server(model, ngl, threads, logdir):
    """Поднять llama-server с моделью; вернуть процесс, когда /health ответит.

    Ловушка LM Studio (04.10.2026): служба навязывает длину контекста сама,
    подгоняя её под ВСЮ свободную видеопамять (262144 у Qwen3.5: 4B занимала
    11 ГБ рядом с игрой), и игнорирует её в `lms load -c`, в файле настроек
    модели и в REST. Кэш в обычной памяти (`offload_kv_cache_to_gpu: false`)
    спасает память, но внимание тогда считает процессор: 5 ток/с и 99 %.
    llama-server с `-c 8192 -np 1`: 4B берёт 3 ГБ, рядом с игрой 18-21 ток/с.
    Библиотекам CUDA нужен PATH на папку vendor из комплекта LM Studio.
    """
    args = [SERVER, "-m", MODELS[model], "-c", "8192", "-np", "1", "-ngl", str(ngl), "-fa", "on",
            "--host", "127.0.0.1", "--port", str(PORT), "--jinja", "--reasoning-budget", "0"]
    if threads:
        args += ["-t", str(threads)]
    env = dict(os.environ, PATH=CUDA_DLLS + os.pathsep + os.environ.get("PATH", ""))
    log = open(os.path.join(logdir, "server-%s-ngl%s.log" % (model, ngl)), "w", encoding="utf-8")
    proc = subprocess.Popen(args, cwd=os.path.dirname(SERVER), env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(180):
        if proc.poll() is not None:
            raise RuntimeError("llama-server вышел с кодом %s" % proc.returncode)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            pass
        time.sleep(1)
    proc.kill()
    raise RuntimeError("llama-server не поднялся за 180 с")


def stop_server(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


DIRECTOR_SYS = (
    "Ты режиссёр Зоны в игре S.T.A.L.K.E.R. 2. Тебе дают обстановку вокруг игрока "
    "(Скиф) в JSON. Выбери ОДНО действие из разрешённых, чтобы Зона ощущалась живой, "
    "но не ломай задания и не убивай игрока нечестно. Разрешённые действия: "
    "none (ничего), say (сталкер рядом говорит реплику; text по-русски, до 15 слов), "
    "look_at (НПС оборачивается к игроку), steps_event (шорох шагов рядом с НПС), "
    "relation_up / relation_down (отношение НПС к игроку на шаг), "
    "spawn_squad (отряд выходит к игроку издалека; text: фракция). "
    "target_id: id НПС из обстановки или 0. reason: коротко, зачем."
)

DIRECTOR_SCHEMA = {
    "name": "director_command",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["none", "say", "look_at", "steps_event",
                                                  "relation_up", "relation_down", "spawn_squad"]},
            "target_id": {"type": "integer"},
            "text": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["action", "target_id", "text", "reason"],
        "additionalProperties": False,
    },
}


def director_state(seq):
    """Обстановка размером с настоящую: игрок, десять НПС, последние события."""
    npcs = []
    kinds = [("loners", "none"), ("bandits", "none"), ("bandits", "player"), ("duty", "none"),
             ("scientists", "none"), ("loners", "other"), ("freedom", "none"), ("military", "none"),
             ("mutant:flesh", "other"), ("mutant:blinddog", "player")]
    for i, (fac, tgt) in enumerate(kinds):
        npcs.append({"id": 100 + i, "faction_by_suit": fac, "dist_m": 8 + i * 9 + seq % 5,
                     "target": tgt, "in_threat": tgt != "none", "hp_pct": 100 - i * 7,
                     "rank": ["novice", "experienced", "veteran"][i % 3], "armed": True})
    return {
        "player": {"name": "Скиф", "hp_pct": 64, "radiation": 12, "ammo_main": 27, "ammo_spare": 90,
                   "weapon": "AK-74", "location": "Залесье, окраина", "time": "18:40", "weather": "дождь",
                   "money": 14200, "reputation": {"loners": "friend", "bandits": "enemy", "duty": "neutral"}},
        "nearby": npcs,
        "recent_events": ["игрок убил двух бандитов 2 мин назад", "выброс был 40 мин назад",
                          "сталкер-одиночка просил помочь с собаками", "игрок продал артефакт Медуза"],
        "last_director_actions": ["say(101)", "none", "look_at(103)"],
        "seq": seq,
    }


BARK_SYS = ("Ты озвучиваешь сталкера-одиночку у костра в игре S.T.A.L.K.E.R. 2. "
            "Ответь ОДНОЙ репликой по-русски, до 20 слов, живо и по-сталкерски, без кавычек.")


def ask(model, kind, seq, timeout=120):
    if kind == "director":
        body = {"model": model, "temperature": 0.7, "max_tokens": 160, "reasoning_effort": "none",
                "response_format": {"type": "json_schema", "json_schema": DIRECTOR_SCHEMA},
                "messages": [{"role": "system", "content": DIRECTOR_SYS},
                             {"role": "user", "content": json.dumps(director_state(seq), ensure_ascii=False)}]}
    else:
        body = {"model": model, "temperature": 0.9, "max_tokens": 80, "reasoning_effort": "none",
                "messages": [{"role": "system", "content": BARK_SYS},
                             {"role": "user", "content": "К костру подошёл Скиф, весь в грязи, после стычки "
                                                         "с бандитами. Вечер, дождь. Скажи ему что-нибудь."}]}
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    dt = time.perf_counter() - t0
    u = data.get("usage") or {}
    msg = data["choices"][0]["message"]
    text = (msg.get("content") or "").strip()
    ok = bool(text)
    if kind == "director" and ok:
        try:
            ok = json.loads(text)["action"] in DIRECTOR_SCHEMA["schema"]["properties"]["action"]["enum"]
        except Exception:
            ok = False
    return {"latency_s": round(dt, 3), "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"),
            "tok_s": round(u["completion_tokens"] / dt, 1) if u.get("completion_tokens") else None,
            "ok": ok, "finish": data["choices"][0].get("finish_reason"), "text": text[:200]}


# ---------------------------------------------------------------- прогон

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="base,gpu4idle,gpu4,cpu4,gpu9,end")
    ap.add_argument("--seconds", type=int, default=75, help="длительность этапа с запросами")
    ap.add_argument("--idle-seconds", type=int, default=40, help="длительность этапа без запросов")
    args = ap.parse_args()
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    for p in phases:
        assert p in PHASES, "нет этапа %s" % p

    out = os.path.join(OUT_ROOT, "measure-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    mon_f = open(os.path.join(out, "monitor.csv"), "w", encoding="utf-8", newline="\n")
    mon_f.write("t,phase,fps,frame_ms,vram_mib,gpu_pct,cpu_pct\n")
    llm_f = open(os.path.join(out, "llm.csv"), "w", encoding="utf-8", newline="\n")
    llm_f.write("t,phase,model,kind,latency_s,prompt_tokens,completion_tokens,tok_s,ok,finish,text\n")

    state = {"phase": "prep", "stop": False}
    rows = []
    cpu = CpuMeter()
    t_start = time.time()

    def monitor():
        while not state["stop"]:
            fps, fms = rtss_fps()
            mem, util = gpu_now()
            c = cpu.read()
            row = (round(time.time() - t_start, 1), state["phase"], fps and round(fps, 1),
                   fms and round(fms, 2), mem, util, c)
            rows.append(row)
            mon_f.write(",".join("" if v is None else str(v) for v in row) + "\n")
            mon_f.flush()
            time.sleep(1.0)

    th = threading.Thread(target=monitor, daemon=True)
    th.start()
    print("пишу в", out)
    fps0, _ = rtss_fps()
    print("RTSS видит игру:", "да, %.0f кадров/с" % fps0 if fps0 else "НЕТ (кадров не будет)")
    # служба LM Studio не должна держать модели в видеопамяти во время замера
    print("LM Studio, выгружаю всё:", lms("unload", "--all"))

    llm_rows = []
    ident = "qwen"
    proc = None
    for ph in phases:
        model, ngl, threads, load = PHASES[ph]
        state["phase"] = "switch"
        stop_server(proc)
        proc = None
        if model:
            t0 = time.time()
            proc = start_server(model, ngl, threads, out)
            print("[%s] модель %s, слоёв на видеокарте %s, потоков %s: поднята за %.0f с" % (
                ph, model, ngl, threads or "по умолчанию", time.time() - t0))
            if load:
                ask(ident, "bark", 0)  # прогрев: первый ответ не считаем
        time.sleep(5)
        state["phase"] = ph
        t_end = time.time() + (args.seconds if load else args.idle_seconds)
        seq = 0
        while time.time() < t_end:
            if not load:
                time.sleep(0.5)
                continue
            kind = "director" if seq % 2 == 0 else "bark"
            try:
                r = ask(ident, kind, seq)
            except Exception as e:
                r = {"latency_s": None, "prompt_tokens": None, "completion_tokens": None,
                     "tok_s": None, "ok": False, "finish": "error", "text": str(e)[:200]}
            r.update({"t": round(time.time() - t_start, 1), "phase": ph, "model": model, "kind": kind})
            llm_rows.append(r)
            llm_f.write(",".join(json.dumps(r.get(k), ensure_ascii=False) if k == "text" else ("" if r.get(k) is None else str(r.get(k)))
                                 for k in ("t", "phase", "model", "kind", "latency_s", "prompt_tokens",
                                           "completion_tokens", "tok_s", "ok", "finish", "text")) + "\n")
            llm_f.flush()
            seq += 1
        print("[%s] готово" % ph)
    state["phase"] = "done"
    stop_server(proc)
    time.sleep(1.5)
    state["stop"] = True
    th.join(timeout=3)
    mon_f.close()
    llm_f.close()

    # ---------------------------------------------------------------- итог
    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.median(xs), 1) if xs else None

    def low(xs, q=0.05):
        xs = sorted(x for x in xs if x is not None)
        return round(xs[int(len(xs) * q)], 1) if xs else None

    lines = ["этап | кадры медиана | кадры 5%% худших | видеопамять МиБ | ядро GPU %% | процессор %% | "
             "режиссёр с | реплика с | ток/с | ответов ок".replace("%%", "%")]
    for ph in phases:
        m = [r for r in rows if r[1] == ph]
        l = [r for r in llm_rows if r["phase"] == ph]
        d = [r["latency_s"] for r in l if r["kind"] == "director"]
        b = [r["latency_s"] for r in l if r["kind"] == "bark"]
        lines.append("%s | %s | %s | %s | %s | %s | %s | %s | %s | %s" % (
            ph, med([r[2] for r in m]), low([r[2] for r in m]), med([r[4] for r in m]),
            med([r[5] for r in m]), med([r[6] for r in m]), med(d), med(b),
            med([r["tok_s"] for r in l]), ("%d/%d" % (sum(1 for r in l if r["ok"]), len(l))) if l else ""))
    samples = {}
    for r in llm_rows:
        samples.setdefault((r["phase"], r["kind"]), r["text"])
    lines.append("")
    lines.append("образцы ответов:")
    for (ph, kind), text in samples.items():
        lines.append("  %s/%s: %s" % (ph, kind, text))
    summary = "\n".join(lines)
    open(os.path.join(out, "summary.txt"), "w", encoding="utf-8").write(summary + "\n")
    print()
    print(summary)


if __name__ == "__main__":
    main()
