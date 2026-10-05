# -*- coding: utf-8 -*-
"""NESTOR: движок модели для игрока (llama-server из своей папки, 05.10.2026).

На машине автора мост поднимает llama-server из комплекта LM Studio (measure.py:
пути LM Studio и PATH на папку vendor с библиотеками CUDA). У игрока nestor.exe
кладёт официальную сборку llama.cpp в <папка NESTOR>\\engine (CUDA 12.4 для
NVIDIA или Vulkan для остальных видеокарт), модель в <папка NESTOR>\\models, и
мост поднимает сервер отсюда. Флаги те же, что проверены 04.10.2026: контекст
8192, один слот, все слои на видеокарте, без скрытых рассуждений.
"""
import os
import subprocess
import time
import urllib.request

PORT = 8081


def clean_env(extra_path=()):
    """Окружение для чужих программ без следов PyInstaller (_PYI_*, TCL/TK_LIBRARY в
    папку _internal); extra_path в начало PATH."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI_", "_MEI")) and k.upper() not in ("TCL_LIBRARY", "TK_LIBRARY")}
    if extra_path:
        env["PATH"] = os.pathsep.join(list(extra_path) + [env.get("PATH", "")])
    return env


def popen(args, **kw):
    """subprocess.Popen без пути поиска DLL лаунчера. Загрузчик PyInstaller ставит
    SetDllDirectory на свою папку _internal, а дочерний процесс его наследует (проверено
    05.10.2026): игра, запущенная nestor.exe по строке Steam, брала из _internal
    VCRUNTIME140 и api-ms-win-crt вместо системных и падала на старте (0xc0000005).
    На время запуска путь сбрасывается и возвращается."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(32768)
        had = k.GetDllDirectoryW(32768, buf)
        k.SetDllDirectoryW(None)
    except Exception:
        return subprocess.Popen(args, **kw)
    try:
        return subprocess.Popen(args, **kw)
    finally:
        if had:
            k.SetDllDirectoryW(buf.value)


def start(server, model, logdir, dll_dirs=(), port=PORT, timeout=240):
    """Поднять llama-server; вернуть процесс, когда /health ответит."""
    args = [server, "-m", model, "-c", "8192", "-np", "1", "-ngl", "99", "-fa", "on",
            "--host", "127.0.0.1", "--port", str(port), "--jinja", "--reasoning-budget", "0"]
    log = open(os.path.join(logdir, "engine.log"), "w", encoding="utf-8")
    proc = popen(args, cwd=os.path.dirname(server), env=clean_env(dll_dirs), stdout=log, stderr=subprocess.STDOUT,
                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited with code %s (see engine.log)" % proc.returncode)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            pass
        time.sleep(1)
    proc.kill()
    raise RuntimeError("llama-server did not start in %d s (see engine.log)" % timeout)


def problem(logdir):
    """Почему llama-server не поднялся, по хвосту engine.log: memory (видеопамять),
    driver (драйвер не подходит сборке CUDA) или other. Нужно мосту (сообщение в игре)
    и лаунчеру (проверка модели без игры)."""
    try:
        with open(os.path.join(logdir, "engine.log"), encoding="utf-8", errors="replace") as f:
            tail = f.read()[-8000:].lower()
    except OSError:
        return "other"
    if any(w in tail for w in ("out of memory", "failed to allocate", "cudamalloc failed", "unable to allocate",
                               "erroroutofdevicememory", "outofdevicememory")):
        return "memory"
    if any(w in tail for w in ("driver version is insufficient", "no cuda-capable device", "cuda driver version",
                               "ggml_cuda_init: failed")):
        return "driver"
    return "other"


def stop(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
