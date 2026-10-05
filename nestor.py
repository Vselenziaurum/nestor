# -*- coding: utf-8 -*-
"""NESTOR: лаунчер для игрока, nestor.exe (выпуск на Nexus, 05.10.2026, НЕСТОР.md).

Решения автора 05.10.2026: русский и английский; программа сама качает движок и
модель; руки чутья явные; nestor.exe это лаунчер: «должен быть лаунчер с простой
настройкой и проверками, там же запуск игры», с выбором модели по видеопамяти и
ответом на вопрос «что будет, если 9B не взлетит».

Окно (двойной щелчок по nestor.exe):
  Проверки, у каждой отметка и кнопка исправления: игра найдена (библиотеки Steam
  или своя папка), шесть файлов мода в ~mods, видеокарта и совет по модели, движок
  и модель скачаны (качается только недостающее), проверка модели без игры (сколько
  видеопамяти заняла, сколько останется игре, скорость), итог прошлой игры (не
  поднялась, была запасная, думала медленно). Режим окна игры не проверяется: окно F4
  встаёт и поверх полноэкранной игры (автор 05.10.2026).
  Настройки: язык; модель 9B или 4B (обе остаются на диске, переход в один щелчок);
  в «Ещё»: движок CUDA или Vulkan, строка для параметров запуска Steam, папка
  памяти, журнал.
  «Играть»: игра через Steam (или exe не из Steam). Пока окно открыто и всё
  скачано, чутьё ждёт игру, как бы её ни запустили, и окно показывает, что с ним.

Запуск из Steam (параметры запуска "<папка>\\nestor.exe" %command%): всё готово и
мод в игре: окна нет, чутьё и игра; иначе открывается лаунчер, «Играть» продолжает
команду Steam, а с выходом из игры окно закрывается само.

Если модель не поднялась рядом с игрой: мост берёт запасную (4B, если скачана) или
пишет на экране игры, что делать, а лаунчер показывает итог прошлой игры с кнопкой
исправления (bridge.serve, report).

Всё лежит в папке программы: engine\\, models\\, memory\\ (память чутья), nestor.json
(настройки, итоги проверки и прошлой игры), nestor.log (журнал). В интернет
программа ходит только за движком и моделью.
"""
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import types
import urllib.request
import zipfile

VERSION = "0.2.0-alpha"
FROZEN = getattr(sys, "frozen", False)
# Оконная сборка PyInstaller без консоли: stdout и stderr равны None, а мост пишет
# журнал и через print, и sys.stdout.reconfigure при импорте. Заглушка до импорта моста.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CFG = os.path.join(BASE, "nestor.json")
ENGINE = os.path.join(BASE, "engine")
MODELS = os.path.join(BASE, "models")
MEMORY = os.path.join(BASE, "memory")
LOGFILE = os.path.join(BASE, "nestor.log")
GAME_SHIPPING = "Stalker2-Win64-Shipping.exe"
APPID = "1643320"                                   # S.T.A.L.K.E.R. 2 в Steam
AUTHOR, DONATE = "Vselenziaurum", "https://boosty.to/vselenziaurum/donate"   # подпись в окне и донат
GAME_NEED_MB = 12 * 1024                            # игра на высоких настройках: 12-13 ГБ (замер 04.10.2026, 3090)
MOD_FILES = ["NESTORStalker2-Windows-%s.%s" % (c, e)
             for c in ("NewContent", "OverrideContent") for e in ("pak", "ucas", "utoc")]
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

GH = "https://github.com/ggml-org/llama.cpp/releases/download/b11403/"
HF = "https://huggingface.co/lmstudio-community/%s/resolve/main/%s"
FILES = {
    "cuda": [(GH + "llama-b11403-bin-win-cuda-12.4-x64.zip", "llama-b11403-bin-win-cuda-12.4-x64.zip", 264164568,
              "b2e2ab3239556e8ede50f11b25572d29021b9b3142f45b2f3ffee90e952a25fb"),
             (GH + "cudart-llama-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip", 391443627,
              "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6")],
    "vulkan": [(GH + "llama-b11403-bin-win-vulkan-x64.zip", "llama-b11403-bin-win-vulkan-x64.zip", 33308159,
                "739c59aaa66f22ff2984b4894b42943ebe2e7fcc216ef6c9d0cf14e3da20943d")],
    "9b": (HF % ("Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf"), "Qwen3.5-9B-Q4_K_M.gguf", 5627044256,
           "cd76ec205963b3b33350093e6904d9de16c4e666fd104e1f632d25c7f15f2a13"),
    "4b": (HF % ("Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf"), "Qwen3.5-4B-Q4_K_M.gguf", 2707513696,
           "25082a7dd3776cc3c741c6347d3bd04523f05796607b3fbc32fa3a25dfa1418c"),
}

T = {
    "ru": {
        "title": "NESTOR: шестое чувство для S.T.A.L.K.E.R. 2",
        "intro": ("NESTOR это чутьё Скифа: локальная нейросеть на вашей видеокарте. Ей нужны движок и "
                  "языковая модель, программа скачает их один раз."),
        "checks": "Проверки", "settings": "Настройки",
        "st_ready": "Всё готово. Можно играть.",
        "st_need": "Сначала исправьте то, что отмечено красным.",
        "st_wait": "Всё готово. Чутьё проснётся, когда запустится игра: кнопкой «Играть» или как обычно.",
        "st_start": "Игра запущена, поднимаю модель %s...",
        "st_run": "Чутьё работает, модель %s. Окно можно свернуть, но не закрывать.",
        "st_fail": "Модель не поднялась, в этой игре чутьё молчит. Что делать, написано ниже.",
        "st_busy_dl": "Скачиваю...", "st_busy_test": "Проверяю модель...",
        "c_game_ok": "Игра: %s",
        "c_game_no": "Игра не найдена в библиотеках Steam.",
        "b_game": "Указать папку",
        "c_mod_ok": "Мод в игре: шесть файлов в ~mods.",
        "c_mod_no": "Мода нет в игре: скопируйте шесть файлов NESTORStalker2-Windows-* из архива в папку ~mods.",
        "c_mod_part": "В ~mods только %d из шести файлов мода: скопируйте все шесть из архива.",
        "b_mods": "Открыть ~mods",
        "c_gpu": "Видеокарта: %s, %s ГБ. %s",
        "c_gpu_none": "Видеокарта не определилась: будет движок Vulkan и модель 4B.",
        "adv_9b": "Хватит на 9B.",
        "adv_4b14": "Советую 4B: 9B может не поместиться рядом с игрой.",
        "adv_4b12": "Советую 4B и в игре качество текстур не выше среднего.",
        "adv_low": "Памяти мало: 4B, низкие текстуры, и проверьте модель кнопкой.",
        "adv_other": "Не NVIDIA: движок Vulkan и 4B, это медленнее и пока проба.",
        "c_files_ok": "Движок и модель %s скачаны.",
        "c_files_no": "Нужно скачать %s ГБ: движок llama.cpp с GitHub и модель %s с Hugging Face.",
        "b_download": "Скачать",
        "c_test_none": "Модель %s ещё не проверялась без игры: проверка покажет, сколько видеопамяти останется игре.",
        "c_test_ok": "Модель %s поднялась за %s с, %s токенов в секунду, заняла %s ГБ; игре останется %s ГБ.",
        "c_test_ok_nomem": "Модель %s поднялась за %s с, %s токенов в секунду.",
        "c_test_tight": ("Модель %s заняла %s ГБ, игре останется %s ГБ: мало, на высоких настройках ей нужно "
                         "около 12. Возьмите 4B или снизьте в игре качество текстур."),
        "c_test_tight_4b": ("Модель %s заняла %s ГБ, игре останется %s ГБ: мало, на высоких настройках ей нужно "
                            "около 12. Снизьте в игре качество текстур."),
        "c_test_fail": "Модель %s не поднялась: %s.",
        "why_memory": "видеокарте не хватило памяти",
        "why_driver": "драйвер видеокарты не подошёл движку CUDA, обновите его или возьмите Vulkan",
        "why_other": "движок не запустился, подробности в engine.log",
        "b_test": "Проверить модель", "testing": "Поднимаю модель %s без игры, это до минуты...",
        "c_last_ok": "Прошлая игра (%s): чутьё работало на %s, реплика за %s с.",
        "c_last_fallback": "Прошлая игра (%s): %s не поместилась рядом с игрой, чутьё работало на 4B.",
        "c_last_slow": "Прошлая игра (%s): модель %s думала медленно, %s с на реплику: видеокарте тесно.",
        "c_last_fail": "Прошлая игра (%s): модель %s не поднялась: %s.",
        "b_take4b": "Взять 4B", "b_vulkan": "Взять Vulkan",
        "lang": "Язык чутья:", "model": "Модель:",
        "m9": "9B, умнее (5,6 ГБ)", "m4": "4B, легче (2,7 ГБ)", "rec": ", советую", "have": ", скачана",
        "more": "Ещё", "engine": "Движок:", "e_cuda": "CUDA, для NVIDIA", "e_vulkan": "Vulkan, любая видеокарта, медленнее",
        "steam": "Чтобы NESTOR запускался вместе с игрой из Steam, вставьте в Свойства игры, Параметры запуска:",
        "copy": "Скопировать", "memory": "Папка памяти", "logs": "Журнал",
        "play": "Играть", "playing": "Игра идёт", "quit": "Выход",
        "confirm_quit": "Чутьё сейчас работает в игре. Закрыть NESTOR и выключить его?",
        "game_dir_pick": "Папка S.T.A.L.K.E.R. 2, где лежит Stalker2.exe",
        "game_dir_bad": "В этой папке нет игры: нужна папка, где лежит Stalker2.exe.",
        "launch_fail": "Игра не запустилась: %s",
        "local": "В интернет программа ходит только за движком и моделью.",
        "progress": "Файл %d из %d: %s из %s ГБ, %s МБ/с", "check": "Проверяю файл %s...",
        "extract": "Распаковываю движок...", "done": "Скачано и проверено.",
        "nospace": "Не хватает места на диске: нужно %s ГБ, свободно %s ГБ.",
        "bad_hash": "Файл %s скачался с ошибкой (не совпала контрольная сумма). Попробуйте ещё раз.",
        "net": "Не удалось скачать %s: %s",
        "already": "NESTOR уже запущен: его окно открыто или он работает вместе с игрой.",
    },
    "en": {
        "title": "NESTOR: a sixth sense for S.T.A.L.K.E.R. 2",
        "intro": ("NESTOR is Skif's gut feeling: a local AI on your graphics card. It needs an engine and a "
                  "language model; the program downloads them once."),
        "checks": "Checks", "settings": "Settings",
        "st_ready": "All set. Ready to play.",
        "st_need": "First fix what is marked red.",
        "st_wait": "All set. The gut wakes up when the game starts: with the Play button or as usual.",
        "st_start": "The game is running, starting the %s model...",
        "st_run": "The gut is on, %s model. You can minimize this window, but do not close it.",
        "st_fail": "The model did not start, the gut is silent in this game. See below what to do.",
        "st_busy_dl": "Downloading...", "st_busy_test": "Testing the model...",
        "c_game_ok": "Game: %s",
        "c_game_no": "The game was not found in your Steam libraries.",
        "b_game": "Choose folder",
        "c_mod_ok": "Mod in the game: six files in ~mods.",
        "c_mod_no": "The mod is not in the game: copy the six NESTORStalker2-Windows-* files from the archive into ~mods.",
        "c_mod_part": "Only %d of the six mod files are in ~mods: copy all six from the archive.",
        "b_mods": "Open ~mods",
        "c_gpu": "Graphics card: %s, %s GB. %s",
        "c_gpu_none": "Could not detect the graphics card: the Vulkan engine and the 4B model will be used.",
        "adv_9b": "Enough for 9B.",
        "adv_4b14": "I suggest 4B: 9B may not fit next to the game.",
        "adv_4b12": "I suggest 4B and texture quality no higher than medium in the game.",
        "adv_low": "Low memory: 4B, low textures, and test the model with the button.",
        "adv_other": "Not NVIDIA: Vulkan engine and 4B, slower and still experimental.",
        "c_files_ok": "The engine and the %s model are downloaded.",
        "c_files_no": "%s GB to download: the llama.cpp engine from GitHub and the %s model from Hugging Face.",
        "b_download": "Download",
        "c_test_none": "The %s model has not been tested without the game: the test shows how much video memory the game keeps.",
        "c_test_ok": "The %s model started in %s s, %s tokens per second, took %s GB; the game keeps %s GB.",
        "c_test_ok_nomem": "The %s model started in %s s, %s tokens per second.",
        "c_test_tight": ("The %s model took %s GB, the game keeps %s GB: too little, on high settings it needs "
                         "about 12. Take 4B or lower texture quality in the game."),
        "c_test_tight_4b": ("The %s model took %s GB, the game keeps %s GB: too little, on high settings it needs "
                            "about 12. Lower texture quality in the game."),
        "c_test_fail": "The %s model did not start: %s.",
        "why_memory": "not enough video memory",
        "why_driver": "the graphics driver does not suit the CUDA engine, update it or take Vulkan",
        "why_other": "the engine failed to start, see engine.log",
        "b_test": "Test model", "testing": "Starting the %s model without the game, up to a minute...",
        "c_last_ok": "Last game (%s): the gut ran on %s, %s s per line.",
        "c_last_fallback": "Last game (%s): %s did not fit next to the game, the gut ran on 4B.",
        "c_last_slow": "Last game (%s): the %s model was slow, %s s per line: the graphics card is short on memory.",
        "c_last_fail": "Last game (%s): the %s model did not start: %s.",
        "b_take4b": "Take 4B", "b_vulkan": "Take Vulkan",
        "lang": "Gut language:", "model": "Model:",
        "m9": "9B, smarter (5.6 GB)", "m4": "4B, lighter (2.7 GB)", "rec": ", suggested", "have": ", downloaded",
        "more": "More", "engine": "Engine:", "e_cuda": "CUDA, for NVIDIA", "e_vulkan": "Vulkan, any card, slower",
        "steam": "To start NESTOR together with the game from Steam, paste this in game Properties, Launch Options:",
        "copy": "Copy", "memory": "Memory folder", "logs": "Log",
        "play": "Play", "playing": "In game", "quit": "Quit",
        "confirm_quit": "The gut is running in the game. Close NESTOR and turn it off?",
        "game_dir_pick": "S.T.A.L.K.E.R. 2 folder with Stalker2.exe",
        "game_dir_bad": "No game in this folder: choose the folder with Stalker2.exe.",
        "launch_fail": "The game did not start: %s",
        "local": "The program goes online only for the engine and the model.",
        "progress": "File %d of %d: %s of %s GB, %s MB/s", "check": "Checking %s...",
        "extract": "Unpacking the engine...", "done": "Downloaded and checked.",
        "nospace": "Not enough disk space: need %s GB, free %s GB.",
        "bad_hash": "%s downloaded with errors (checksum mismatch). Please try again.",
        "net": "Could not download %s: %s",
        "already": "NESTOR is already running: its window is open or it runs with the game.",
    },
}


# ---------------------------------------------------------------- журнал, настройки

def log(msg):
    line = time.strftime("%d.%m %H:%M:%S ") + msg
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_cfg():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cfg(cfg):
    tmp = CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CFG)


def update_cfg(**kw):
    """Записать поля, перечитав файл: итог игры пишет мост из своего потока, и окно со
    своей старой копией настроек его бы затёрло."""
    cfg = load_cfg()
    cfg.update(kw)
    save_cfg(cfg)
    return cfg


def windows_lang():
    try:
        import ctypes
        return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x19 else "en"
    except Exception:
        return "en"


# ---------------------------------------------------------------- видеокарта и совет

def _nvsmi(fields):
    out = subprocess.run(["nvidia-smi", "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10, creationflags=NOWIN).stdout.strip()
    return [x.strip() for x in out.splitlines()[0].split(",")]


def gpu_info():
    """{name, total_mb, nvidia}: NVIDIA через nvidia-smi, остальные по реестру
    (HardwareInformation.qwMemorySize адаптера). None, если не вышло."""
    try:
        name, total = _nvsmi("name,memory.total")
        return {"name": name, "total_mb": int(float(total)), "nvidia": True}
    except Exception:
        pass
    try:
        import winreg
        best = None
        path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(k, sub) as s:
                        name = winreg.QueryValueEx(s, "DriverDesc")[0]
                        try:
                            mem = winreg.QueryValueEx(s, "HardwareInformation.qwMemorySize")[0]
                        except OSError:
                            mem = winreg.QueryValueEx(s, "HardwareInformation.MemorySize")[0]
                except OSError:
                    continue
                if isinstance(mem, bytes):
                    mem = int.from_bytes(mem[:8], "little")
                if best is None or int(mem) > best[1]:
                    best = (name, int(mem))
        if best and best[1] > 0:
            return {"name": best[0], "total_mb": best[1] // 2 ** 20, "nvidia": False}
    except Exception:
        pass
    return None


def gpu_used_mb():
    try:
        return int(float(_nvsmi("memory.used")[0]))
    except Exception:
        return None


def recommend(g):
    """-> (движок, модель, ключ совета). Игра на высоких настройках берёт 12-13 ГБ, 9B с
    контекстом 8192 около 6 ГБ, 4B около 3,5 ГБ (замеры 04.10.2026 на 3090). Объём
    карты лишь подсказка: правду говорит проверка модели без игры."""
    if not g:
        return "vulkan", "4b", None
    if not g["nvidia"]:
        return "vulkan", "4b", "adv_other"
    gb = g["total_mb"] / 1024
    if gb >= 19.5:
        return "cuda", "9b", "adv_9b"
    if gb >= 13.5:
        return "cuda", "4b", "adv_4b14"
    if gb >= 11.5:
        return "cuda", "4b", "adv_4b12"
    return "cuda", "4b", "adv_low"


# ---------------------------------------------------------------- игра

def steam_root():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return None


def is_game_dir(d):
    return bool(d) and os.path.isdir(os.path.join(d, "Stalker2", "Content", "Paks"))


def game_root(path):
    """Корень игры по любому пути внутри неё (Steam подставляет в %command% exe)."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    for _ in range(6):
        if is_game_dir(d):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return None


def find_game(cfg, game_cmd=()):
    """Папка игры: из команды Steam, из настроек (кнопка «Указать папку») или по
    библиотекам Steam (libraryfolders.vdf и appmanifest_1643320.acf)."""
    exe = next((a for a in game_cmd if a.lower().endswith(".exe")), None)
    if exe and game_root(exe):
        return game_root(exe)
    if is_game_dir(cfg.get("game_dir")):
        return cfg["game_dir"]
    root = steam_root()
    if not root:
        return None
    libs = [root]
    try:
        vdf = open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace").read()
        libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    for lib in libs:
        acf = os.path.join(lib, "steamapps", "appmanifest_%s.acf" % APPID)
        try:
            m = re.search(r'"installdir"\s+"([^"]+)"', open(acf, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
        if m and is_game_dir(os.path.join(lib, "steamapps", "common", m.group(1))):
            return os.path.join(lib, "steamapps", "common", m.group(1))
    return None


def mods_dir(game):
    return os.path.join(game, "Stalker2", "Content", "Paks", "~mods")


def mod_count(game):
    """Сколько из шести файлов мода лежит в ~mods (игра смотрит и вложенные папки)."""
    found = set()
    for _root, _dirs, files in os.walk(mods_dir(game)):
        found.update(f for f in files if f in MOD_FILES)
    return len(found)


def run_game(cmd):
    """Игра дочерним процессом без пути поиска DLL и окружения лаунчера (engine.popen):
    05.10.2026 игра по строке Steam падала на старте, подхватив библиотеки из _internal."""
    import engine
    return engine.popen(cmd, cwd=os.path.dirname(cmd[0]) or None, env=engine.clean_env())


def launch_game(game, game_cmd):
    """Команда Steam как есть; игра из Steam через steam://, иначе Stalker2.exe."""
    if game_cmd:
        run_game(game_cmd)
    elif "steamapps" in game.lower():
        os.startfile("steam://rungameid/" + APPID)
    else:
        run_game([os.path.join(game, "Stalker2.exe")])


# ---------------------------------------------------------------- движок и модель

def plan_files(backend, model):
    files = [(u, n, s, h, "engine") for u, n, s, h in FILES[backend]]
    u, n, s, h = FILES[model]
    return files + [(u, n, s, h, "model")]


def find_server():
    for root, _dirs, names in os.walk(ENGINE):
        if "llama-server.exe" in names:
            return os.path.join(root, "llama-server.exe")
    return None


def engine_stale(files):
    """В engine стоит движок другой сборки или другого вида: метки .ok не из плана
    этой версии (новая версия закрепила другую сборку llama.cpp, или игрок сменил
    CUDA на Vulkan)."""
    want = {n + ".ok" for _u, n, _s, _h, kind in files if kind == "engine"}
    try:
        have = {f for f in os.listdir(ENGINE) if f.endswith(".ok")}
    except OSError:
        return False
    return bool(have - want)


def ready(cfg):
    """Всё на месте: язык и модель выбраны и стоят ровно те файлы, что закреплены в
    ЭТОЙ версии: метки архивов движка без чужих и модель нужного размера. Так
    обновление, сменившее движок или модель, докачивает только их, а не работает
    молча на старом."""
    if not cfg.get("lang") or not cfg.get("model"):
        return False
    if cfg.get("model_path") or cfg.get("server_path"):              # свои пути: отладка
        model = cfg.get("model_path") or os.path.join(MODELS, FILES[cfg["model"]][1])
        server = cfg.get("server_path") or find_server()
        return bool(server and os.path.exists(server) and os.path.exists(model))
    files = plan_files(cfg.get("backend") or recommend(gpu_info())[0], cfg["model"])
    return (not engine_stale(files) and all(have_file(k, n, s) for _u, n, s, _h, k in files)
            and find_server() is not None)


def need_bytes(files):
    """Сколько ещё качать: движок другой сборки уйдёт целиком."""
    stale = engine_stale(files)
    return sum(s for _u, n, s, _h, k in files if (k == "engine" and stale) or not have_file(k, n, s))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def have_file(kind, name, size):
    """Файл уже стоит: модель нужного размера или метка распакованного архива движка."""
    dest = os.path.join(MODELS if kind == "model" else ENGINE, name)
    return (os.path.exists(dest) and os.path.getsize(dest) == size) or os.path.exists(dest + ".ok")


def fetch(url, dest, size, sha, progress, checking=lambda: None):
    """Скачать с докачкой в dest.part, сверить SHA-256, переименовать. progress(получено);
    checking() перед сверкой: у модели 5,6 ГБ она идёт десятки секунд."""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    if have < size:
        req = urllib.request.Request(url, headers={"User-Agent": "NESTOR/" + VERSION,
                                                   "Range": "bytes=%d-" % have} if have else
                                     {"User-Agent": "NESTOR/" + VERSION})
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:              # сервер не умеет докачку: с нуля
                have = 0
            with open(part, "ab" if have else "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    have += len(b)
                    progress(have)
    checking()
    if sha256(part) != sha:
        os.remove(part)
        raise ValueError("checksum")
    os.replace(part, dest)


def clear_old_models():
    """Модели прошлых версий (их нет ни в одной строке FILES) удалить: по 5,6 ГБ впустую.
    Обе нынешние (9B и 4B) остаются: игрок может вернуться к другой."""
    keep = {FILES[k][1] for k in ("9b", "4b")}
    for f in os.listdir(MODELS):
        base = f[:-5] if f.endswith(".part") else f
        if base.endswith(".gguf") and base not in keep:
            try:
                os.remove(os.path.join(MODELS, f))
                log("убрана модель прошлой версии: %s" % f)
            except OSError:
                pass


def download_all(files, ui_q):
    """В отдельном потоке: качает файлы, распаковывает движок. Пишет события в ui_q.
    Движок другой сборки сносится целиком до загрузки: архив новой распаковался бы
    поверх, а оставшиеся библиотеки ggml старой сборки сервер подгружает сам и падает."""
    try:
        if engine_stale(files):
            log("движок сменился, старый убираю: %s" % ", ".join(sorted(os.listdir(ENGINE))[:6]))
            shutil.rmtree(ENGINE, ignore_errors=True)
        os.makedirs(ENGINE, exist_ok=True)
        os.makedirs(MODELS, exist_ok=True)
        for i, (url, name, size, sha, kind) in enumerate(files, 1):
            dest = os.path.join(MODELS if kind == "model" else ENGINE, name)
            if have_file(kind, name, size):
                continue                              # модель на месте или архив движка уже распакован
            t0, last = time.time(), [0.0]

            def progress(got, i=i, size=size, t0=t0):
                if time.time() - last[0] > 0.3:
                    last[0] = time.time()
                    ui_q.put(("progress", i, len(files), got, size, got / max(0.1, time.time() - t0)))
            log("качаю %s" % url)
            try:
                fetch(url, dest, size, sha, progress, lambda name=name: ui_q.put(("check", name)))
            except ValueError:
                log("не совпала контрольная сумма: %s" % name)
                ui_q.put(("error", "bad_hash", name))
                return
            except Exception as e:
                log("не скачалось %s: %r" % (name, e))
                ui_q.put(("error", "net", name, str(e)))
                return
            log("скачано и сверено: %s, %.0f с" % (name, time.time() - t0))
            if kind == "engine":
                ui_q.put(("extract",))
                with zipfile.ZipFile(dest) as z:
                    z.extractall(ENGINE)
                os.remove(dest)                       # распакованному архиву место не нужно
                open(dest + ".ok", "w").close()       # метка: этот архив уже стоит
        clear_old_models()
        log("загрузка закончена")
        ui_q.put(("done",))
    except Exception as e:
        log("загрузка упала: %r" % e)
        ui_q.put(("error", "net", "?", str(e)))


def model_test(cfg, ui_q):
    """Поднять выбранную модель без игры, замерить и погасить: время подъёма, скорость,
    сколько видеопамяти заняла и сколько останется игре. Объём карты только догадка,
    а эта проверка отвечает на вопрос автора «что будет, если 9B не взлетит» до игры."""
    import engine
    key, backend = cfg.get("model", "9b"), cfg.get("backend", "cuda")
    res = {"model": key, "backend": backend, "when": time.strftime("%d.%m.%Y %H:%M")}
    path = cfg.get("model_path") or os.path.join(MODELS, FILES[key][1])
    try:
        before = gpu_used_mb()
        t0 = time.time()
        proc = engine.start(cfg.get("server_path") or find_server(), path, BASE, cfg.get("dll_dirs") or [],
                            timeout=180)
        res["load_s"] = round(time.time() - t0, 1)
        after = gpu_used_mb()
        try:
            body = {"model": "qwen", "temperature": 0.7, "max_tokens": 48,
                    "messages": [{"role": "user", "content": "One short sentence about the Zone."}]}
            req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % engine.PORT,
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode("utf-8"))
            res["tps"] = int(round(d.get("timings", {}).get("predicted_per_second") or 0))
        finally:
            engine.stop(proc)
        g = gpu_info()
        if before is not None and after is not None and g:
            res["model_mb"] = max(0, after - before)
            res["left_mb"] = max(0, g["total_mb"] - after)
        res["ok"] = True
    except Exception as e:
        res.update(ok=False, why=engine.problem(BASE), error=str(e)[:200])
    log("проверка модели: %s" % json.dumps(res, ensure_ascii=False))
    ui_q.put(("test_done", res))


# ---------------------------------------------------------------- чутьё

def save_report(d):
    """Итог игры от моста (bridge.report): лаунчер покажет его строкой «Прошлая игра»."""
    update_cfg(last_game=d)
    log("итог игры: %s" % json.dumps(d, ensure_ascii=False))


def bridge_args(cfg, wait):
    model = cfg.get("model", "9b")
    path = cfg.get("model_path") or os.path.join(MODELS, FILES[model][1])
    # 9B не поднялась рядом с игрой: мост берёт 4B, если та скачана
    fallbacks = []
    if model == "9b" and not cfg.get("model_path") and have_file("model", FILES["4b"][1], FILES["4b"][2]):
        fallbacks.append(("4b", os.path.join(MODELS, FILES["4b"][1])))
    return types.SimpleNamespace(model=model, quiet=240.0, min_gap=8.0, persona="sixth",
                                 ask_key=cfg.get("ask_key", "73"), wait=wait, loop=False, memory_dir=MEMORY,
                                 server=cfg.get("server_path") or find_server(), model_path=path,
                                 dll_dirs=cfg.get("dll_dirs") or [], fallbacks=fallbacks, report=save_report)


def run_bridge(cfg, wait, loop, stop_evt):
    """Чутьё в этом же процессе (bridge.serve), язык из настроек, журнал nestor.log."""
    import lang as L
    L.set_lang(cfg.get("lang", "en"))
    import bridge
    bridge.LOG = LOGFILE
    args = bridge_args(cfg, wait)
    try:
        while not stop_evt.is_set():
            if not bridge.serve(args) or not loop:
                break
    except Exception as e:
        log("чутьё упало: %r" % e)
    finally:
        stop_evt.set()


def bridge_status():
    b = sys.modules.get("bridge")
    return getattr(b, "STATUS", ("idle", "")) if b else ("idle", "")


_MUTEX = None


def first_instance():
    """True, если этот NESTOR первый. Второй появляется так: окно лаунчера открыто, а
    игру запускают из Steam со строкой NESTOR, или двойной щелчок посреди загрузки.
    Два моста делили бы F4, порт модели и слоты обмена, две загрузки писали бы в один .part."""
    global _MUTEX
    try:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = ctypes.c_void_p
        _MUTEX = k.CreateMutexW(None, False, "Local\\NESTOR_single_instance")
        return ctypes.get_last_error() != 183           # ERROR_ALREADY_EXISTS
    except Exception:
        return True


# ---------------------------------------------------------------- окно

MARK = {"ok": ("✓", "#2e8b4f"), "warn": ("!", "#c77700"), "bad": ("✗", "#c0392b"), "info": ("•", "#6b675f")}


class Launcher:
    """Окно nestor.exe: проверки с кнопками исправления, настройки, «Играть»."""

    def __init__(self, game_cmd=()):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.game_cmd = list(game_cmd)
        self.gpu = gpu_info()
        self.rec_backend, self.rec_model, self.advice = recommend(self.gpu)
        cfg = load_cfg()
        cfg = update_cfg(lang=cfg.get("lang") or windows_lang(), model=cfg.get("model") or self.rec_model,
                         backend=cfg.get("backend") or self.rec_backend, version=VERSION)
        self.root = tk.Tk()
        self.root.title("NESTOR " + VERSION)
        self.root.resizable(False, False)
        self.v_lang = tk.StringVar(master=self.root, value=cfg["lang"])
        self.v_model = tk.StringVar(master=self.root, value=cfg["model"])
        self.v_backend = tk.StringVar(master=self.root, value=cfg["backend"])
        self.v_more = tk.BooleanVar(master=self.root, value=False)
        self.q = queue.Queue()
        self.busy = None                      # "download" или "test": кнопки гаснут
        self.msg, self.bar = "", 0.0
        self.w_bar = self.w_msg = None
        self.game = None
        self.bridge_th = None
        self.seen = None                      # последнее показанное состояние моста
        self.was_in_game = False
        log("окно: видеокарта %s, совет %s/%s" % (json.dumps(self.gpu, ensure_ascii=False), self.rec_backend,
                                                  self.rec_model))
        self.frm = ttk.Frame(self.root, padding=14)
        self.frm.pack(fill="both", expand=True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.render()
        self.ensure_bridge()
        self.root.after(400, self.poll)

    # ------------------------------------------------ мелочи
    def t(self, key):
        return T[self.v_lang.get()][key]

    def n(self, x, digits=1):
        s = "%.*f" % (digits, x)
        return s.replace(".", ",") if self.v_lang.get() == "ru" else s

    def in_game(self):
        return bridge_status()[0] in ("start", "ready", "fail")

    def open_path(self, p):
        try:
            if p == MEMORY:
                os.makedirs(MEMORY, exist_ok=True)
            os.startfile(p)
        except OSError as e:
            log("не открылось %s: %s" % (p, e))

    def copy(self, line):
        self.root.clipboard_clear()
        self.root.clipboard_append(line)

    def open_url(self, url):
        """Ссылку открывает браузер игрока по его щелчку; сама программа туда не ходит."""
        import webbrowser
        try:
            webbrowser.open(url)
            log("открыта ссылка: %s" % url)
        except Exception as e:
            log("ссылка не открылась: %s" % e)

    # ------------------------------------------------ чутьё
    def ensure_bridge(self):
        """Пока окно открыто и всё скачано, чутьё ждёт игру, как бы её ни запустили."""
        cfg = load_cfg()
        alive = self.bridge_th is not None and self.bridge_th.is_alive()
        if ready(cfg) and not alive:
            self.bridge_th = threading.Thread(target=run_bridge, args=(cfg, 0, True, threading.Event()), daemon=True)
            self.bridge_th.start()
            log("чутьё ждёт игру: модель %s, язык %s" % (cfg.get("model"), cfg.get("lang")))
        elif not ready(cfg) and alive and not self.in_game():
            self.stop_bridge()

    def stop_bridge(self):
        b = sys.modules.get("bridge")
        if self.bridge_th is not None and self.bridge_th.is_alive() and b is not None:
            b.STOP.set()
            self.bridge_th.join(timeout=10)
            b.STOP.clear()
        self.bridge_th = None

    def changed(self):
        """Сменились язык, модель или движок: мост ждал игру со старыми, перезапустить."""
        if not self.in_game():
            self.stop_bridge()
            self.ensure_bridge()
        self.render()

    # ------------------------------------------------ проверки
    def checks(self, cfg):
        t = self.t
        res = []
        model, backend = cfg.get("model", "9b"), cfg.get("backend", "cuda")
        big = model.upper()
        self.game = find_game(cfg, self.game_cmd)
        if self.game:
            res.append(("ok", t("c_game_ok") % self.game, []))
            n = mod_count(self.game)
            if n == len(MOD_FILES):
                res.append(("ok", t("c_mod_ok"), []))
            else:
                game = self.game
                res.append(("bad", t("c_mod_no") if n == 0 else t("c_mod_part") % n,
                            [(t("b_mods"), lambda: self.open_path(mods_dir(game)))]))
        else:
            res.append(("bad", t("c_game_no"), [(t("b_game"), self.pick_game)]))
        if self.gpu:
            res.append(("info", t("c_gpu") % (self.gpu["name"], self.n(self.gpu["total_mb"] / 1024, 0),
                                              t(self.advice)), []))
        else:
            res.append(("warn", t("c_gpu_none"), []))
        if ready(cfg):
            res.append(("ok", t("c_files_ok") % big, []))
            res.append(self.test_line(cfg, model, backend))
        else:
            need = need_bytes(plan_files(backend, model))
            res.append(("bad", t("c_files_no") % (self.n(need / 1e9), big), [(t("b_download"), self.download)]))
        # Режим окна игры больше не проверяется: окно F4 встаёт и поверх полноэкранной игры
        # (проверено автором 05.10.2026), прежнее требование «без рамки» было догадкой.
        last = self.last_line(cfg, model)
        if last:
            res.append(last)
        return res

    def test_line(self, cfg, model, backend):
        t = self.t
        big = model.upper()
        lt = cfg.get("last_test") or {}
        btn = [(t("b_test"), self.test)]
        if lt.get("model") != model or lt.get("backend") != backend:
            return "info", t("c_test_none") % big, btn
        if not lt.get("ok"):
            fix = []
            if lt.get("why") == "memory" and model == "9b":
                fix.append((t("b_take4b"), lambda: self.take("4b")))
            if lt.get("why") == "driver" and backend == "cuda":
                fix.append((t("b_vulkan"), self.take_vulkan))
            why = lt.get("why") if lt.get("why") in ("memory", "driver") else "other"
            return "bad", t("c_test_fail") % (big, t("why_" + why)), fix + btn
        load, tps = self.n(lt.get("load_s", 0)), str(lt.get("tps", "?"))
        if lt.get("left_mb") is None:
            return "ok", t("c_test_ok_nomem") % (big, load, tps), btn
        used, left = self.n(lt["model_mb"] / 1024), self.n(lt["left_mb"] / 1024)
        if lt["left_mb"] < GAME_NEED_MB:
            if model == "9b":
                return "warn", t("c_test_tight") % (big, used, left), [(t("b_take4b"), lambda: self.take("4b"))] + btn
            return "warn", t("c_test_tight_4b") % (big, used, left), btn
        return "ok", t("c_test_ok") % (big, load, tps, used, left), btn

    def last_line(self, cfg, model):
        t = self.t
        lg = cfg.get("last_game")
        if not lg or lg.get("model") != model:
            return None
        big, when, p = model.upper(), lg.get("when", ""), lg.get("problem", "")
        take4 = [(t("b_take4b"), lambda: self.take("4b"))] if model == "9b" else []
        if p == "":
            if lg.get("median") is None:
                return None
            return "ok", t("c_last_ok") % (when, (lg.get("used") or model).upper(), self.n(lg["median"])), []
        if p == "fallback":
            return "warn", t("c_last_fallback") % (when, big), take4
        if p == "slow":
            return "warn", t("c_last_slow") % (when, big, self.n(lg.get("median") or 0)), take4
        fix = take4 if p == "memory" else []
        if p == "driver" and cfg.get("backend") == "cuda":
            fix = [(t("b_vulkan"), self.take_vulkan)]
        why = p if p in ("memory", "driver") else "other"
        return "bad", t("c_last_fail") % (when, big, t("why_" + why)), fix

    def status(self, checks):
        t = self.t
        s, d = bridge_status()
        if self.busy == "download":
            return t("st_busy_dl"), "#2c5d8a"
        if self.busy == "test":
            return t("st_busy_test"), "#2c5d8a"
        if s == "start":
            return t("st_start") % (d or "").upper(), "#2c5d8a"
        if s == "ready":
            return t("st_run") % (d or "").upper(), "#2e8b4f"
        if s == "fail":
            return t("st_fail"), "#c0392b"
        if any(c[0] == "bad" for c in checks):
            return t("st_need"), "#c0392b"
        if self.bridge_th is not None and self.bridge_th.is_alive():
            return t("st_wait"), "#2e8b4f"
        return t("st_ready"), "#2e8b4f"

    # ------------------------------------------------ окно
    def render(self):
        tk, ttk = self.tk, self.ttk
        for w in self.frm.winfo_children():
            w.destroy()
        cfg = load_cfg()
        checks = self.checks(cfg)
        ing = self.in_game()
        lock = bool(self.busy) or ing
        ttk.Label(self.frm, text=self.t("title"), font=("Segoe UI", 12, "bold")).pack(anchor="w")
        if not ready(cfg):
            ttk.Label(self.frm, text=self.t("intro"), wraplength=620, justify="left").pack(anchor="w", pady=(4, 0))
        text, color = self.status(checks)
        tk.Label(self.frm, text=text, fg=color, font=("Segoe UI", 10, "bold"), wraplength=620,
                 justify="left").pack(anchor="w", pady=(6, 8))
        box = ttk.LabelFrame(self.frm, text=self.t("checks"), padding=(10, 6))
        box.pack(fill="x")
        for level, line, buttons in checks:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            mark, col = MARK[level]
            tk.Label(row, text=mark, fg=col, font=("Segoe UI", 11, "bold"), width=2).pack(side="left", anchor="n")
            for label, cmd in reversed(buttons):
                b = ttk.Button(row, text=label, command=cmd)
                b.pack(side="right", padx=(4, 0), anchor="n")
                if lock:
                    b.state(["disabled"])
            ttk.Label(row, text=line, wraplength=600 - 130 * len(buttons), justify="left").pack(side="left", anchor="w")
        sb = ttk.LabelFrame(self.frm, text=self.t("settings"), padding=(10, 6))
        sb.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(sb)
        row.pack(anchor="w")
        ttk.Label(row, text=self.t("lang")).pack(side="left")
        for val, label in (("ru", "Русский"), ("en", "English")):
            r = ttk.Radiobutton(row, text=label, value=val, variable=self.v_lang, command=self.set_lang)
            r.pack(side="left", padx=(8, 0))
            if lock:
                r.state(["disabled"])
        row = ttk.Frame(sb)
        row.pack(anchor="w", pady=(4, 0))
        ttk.Label(row, text=self.t("model")).pack(side="left")
        for key in ("9b", "4b"):
            have = have_file("model", FILES[key][1], FILES[key][2])
            label = (self.t("m" + key[0]) + (self.t("rec") if key == self.rec_model else "")
                     + (self.t("have") if have else ""))
            r = ttk.Radiobutton(row, text=label, value=key, variable=self.v_model, command=self.set_model)
            r.pack(side="left", padx=(8, 0))
            if lock:
                r.state(["disabled"])
        ttk.Checkbutton(sb, text=self.t("more"), variable=self.v_more, command=self.render).pack(anchor="w", pady=(6, 0))
        if self.v_more.get():
            adv = ttk.Frame(sb)
            adv.pack(fill="x", pady=(2, 0))
            row = ttk.Frame(adv)
            row.pack(anchor="w")
            ttk.Label(row, text=self.t("engine")).pack(side="left")
            for val in ("cuda", "vulkan"):
                r = ttk.Radiobutton(row, text=self.t("e_" + val), value=val, variable=self.v_backend,
                                    command=self.set_backend)
                r.pack(side="left", padx=(8, 0))
                if lock:
                    r.state(["disabled"])
            ttk.Label(adv, text=self.t("steam"), wraplength=600, justify="left").pack(anchor="w", pady=(8, 2))
            line = '"%s" %%command%%' % (sys.executable if FROZEN else os.path.abspath(__file__))
            row = ttk.Frame(adv)
            row.pack(anchor="w")
            ent = ttk.Entry(row, width=70)
            ent.insert(0, line)
            ent.pack(side="left")
            ttk.Button(row, text=self.t("copy"), command=lambda: self.copy(line)).pack(side="left", padx=4)
            row = ttk.Frame(adv)
            row.pack(anchor="w", pady=(6, 0))
            ttk.Button(row, text=self.t("memory"), command=lambda: self.open_path(MEMORY)).pack(side="left")
            ttk.Button(row, text=self.t("logs"), command=lambda: self.open_path(LOGFILE)).pack(side="left", padx=4)
        self.w_bar = self.w_msg = None
        if self.busy:
            self.w_bar = ttk.Progressbar(self.frm, length=620,
                                         mode="determinate" if self.busy == "download" else "indeterminate")
            self.w_bar.pack(pady=(10, 2))
            if self.busy == "download":
                self.w_bar["value"] = self.bar
            else:
                self.w_bar.start(12)
        if self.busy or self.msg:
            self.w_msg = ttk.Label(self.frm, text=self.msg, wraplength=620, justify="left")
            self.w_msg.pack(anchor="w", pady=(4, 0))
        ttk.Label(self.frm, text=self.t("local"), foreground="#6b675f").pack(anchor="w", pady=(8, 0))
        btns = ttk.Frame(self.frm)
        btns.pack(fill="x", pady=(10, 0))
        # автор мода слева снизу, по щелчку страница доната (слово автора 05.10.2026)
        link = tk.Label(btns, text=AUTHOR, fg="#2c5d8a", cursor="hand2", font=("Segoe UI", 10, "underline"))
        link.pack(side="left", anchor="s")
        link.bind("<Button-1>", lambda _e: self.open_url(DONATE))
        ttk.Button(btns, text=self.t("quit"), command=self.quit).pack(side="right")
        play = ttk.Button(btns, text=self.t("playing") if ing else self.t("play"), command=self.play)
        play.pack(side="right", padx=4)
        if ing or self.busy or not self.game or not ready(cfg):
            play.state(["disabled"])

    # ------------------------------------------------ действия
    def set_lang(self):
        update_cfg(lang=self.v_lang.get())
        log("язык чутья: %s" % self.v_lang.get())
        self.changed()

    def set_model(self):
        update_cfg(model=self.v_model.get())
        log("модель: %s" % self.v_model.get())
        self.changed()

    def set_backend(self):
        update_cfg(backend=self.v_backend.get())
        log("движок: %s" % self.v_backend.get())
        self.changed()

    def take(self, key):
        self.v_model.set(key)
        self.set_model()
        if not ready(load_cfg()):
            self.download()

    def take_vulkan(self):
        self.v_backend.set("vulkan")
        self.set_backend()
        if not ready(load_cfg()):
            self.download()

    def download(self):
        from tkinter import messagebox
        cfg = load_cfg()
        files = plan_files(cfg.get("backend", "cuda"), cfg.get("model", "9b"))
        need, free = need_bytes(files) / 1e9, shutil.disk_usage(BASE).free / 1e9
        if free < need + 0.5:
            messagebox.showwarning("NESTOR", self.t("nospace") % (self.n(need + 0.5), self.n(free)), parent=self.root)
            return
        self.busy, self.msg, self.bar = "download", "", 0.0
        threading.Thread(target=download_all, args=(files, self.q), daemon=True).start()
        self.render()

    def test(self):
        cfg = load_cfg()
        self.busy, self.msg = "test", self.t("testing") % cfg.get("model", "9b").upper()
        threading.Thread(target=model_test, args=(cfg, self.q), daemon=True).start()
        self.render()

    def pick_game(self):
        from tkinter import filedialog, messagebox
        d = filedialog.askdirectory(parent=self.root, title=self.t("game_dir_pick"))
        if not d:
            return
        d = os.path.normpath(d)
        if not is_game_dir(d):
            messagebox.showwarning("NESTOR", self.t("game_dir_bad"), parent=self.root)
            return
        update_cfg(game_dir=d)
        log("папка игры указана: %s" % d)
        self.render()

    def play(self):
        from tkinter import messagebox
        try:
            launch_game(self.game, self.game_cmd)
            log("игра запущена кнопкой «Играть»")
        except Exception as e:
            log("игра не запустилась: %r" % e)
            messagebox.showwarning("NESTOR", self.t("launch_fail") % e, parent=self.root)
        self.render()

    def quit(self):
        from tkinter import messagebox
        if self.in_game() and not messagebox.askyesno("NESTOR", self.t("confirm_quit"), parent=self.root):
            return
        self.root.destroy()

    def poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "progress":
                    _, i, n, got, size, speed = ev
                    self.bar = 100.0 * got / size
                    self.msg = self.t("progress") % (i, n, self.n(got / 1e9, 2), self.n(size / 1e9, 2),
                                                     self.n(speed / 1e6))
                    if self.w_bar is not None:
                        self.w_bar["value"] = self.bar
                    if self.w_msg is not None:
                        self.w_msg.config(text=self.msg)
                elif kind in ("check", "extract"):
                    self.msg = self.t("check") % ev[1] if kind == "check" else self.t("extract")
                    if self.w_msg is not None:
                        self.w_msg.config(text=self.msg)
                elif kind == "done":
                    self.busy, self.msg = None, self.t("done")
                    self.ensure_bridge()
                    self.render()
                elif kind == "error":
                    self.busy = None
                    self.msg = (self.t(ev[1]) % ev[2:]) if len(ev) > 2 else self.t(ev[1])
                    self.render()
                elif kind == "test_done":
                    update_cfg(last_test=ev[1])
                    self.busy, self.msg = None, ""
                    self.render()
        except queue.Empty:
            pass
        st = bridge_status()
        if st != self.seen:
            self.seen = st
            ing = st[0] in ("start", "ready", "fail")
            if self.was_in_game and not ing and self.game_cmd:
                # запуск из Steam: игра закрыта, чутьё дописало дневник; окно закрыть,
                # иначе Steam считает игру идущей, пока висит nestor.exe
                self.root.destroy()
                return
            self.was_in_game = ing
            self.render()
        self.root.after(500, self.poll)


# ---------------------------------------------------------------- запуск

def main():
    cfg = load_cfg()
    game_cmd = sys.argv[1:]
    log("NESTOR %s, запуск %s" % (VERSION, ("из Steam: %s" % " ".join(game_cmd)) if game_cmd else "двойным щелчком"))
    try:
        import ctypes
        b = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetDllDirectoryW(32768, b):
            log("путь DLL загрузчика: %s (игре и движку не передаётся, engine.popen)" % b.value)
    except Exception:
        pass
    if not first_instance():
        log("NESTOR уже работает, второй выходит" + (" и запускает игру" if game_cmd else ""))
        if game_cmd:                                  # чутьё уже есть у первого: только игра
            run_game(game_cmd)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, T[cfg.get("lang") or windows_lang()]["already"], "NESTOR", 0x40)
        return
    if cfg.get("lang") and cfg.get("version") != VERSION:
        # обновление поверх старой папки: память, настройки и скачанное остались; что
        # сменилось в FILES, докачает лаунчер (ready). Отметка версии нужна будущим
        # версиям, если память сменит формат.
        log("обновление: была %s, стала %s" % (cfg.get("version") or "?", VERSION))
        cfg = update_cfg(version=VERSION)
    if game_cmd and ready(cfg):
        game = find_game(cfg, game_cmd)
        if game and mod_count(game) == len(MOD_FILES):
            # из Steam, и всё готово: без окна, чутьё и игра
            stop_evt = threading.Event()
            th = threading.Thread(target=run_bridge, args=(cfg, 180, False, stop_evt), daemon=True)
            th.start()
            run_game(game_cmd).wait()
            th.join(timeout=24 * 3600)               # чутьё выходит само, когда закрылась игра
            log("игра закрыта, NESTOR выходит")
            return
    ui = Launcher(game_cmd)
    ui.root.mainloop()
    b = sys.modules.get("bridge")
    if b is not None:
        b.STOP.set()
        eng = getattr(b, "ENGINE_PROC", None)
        if eng:                                      # окно закрыли посреди игры: модель не бросать
            eng[1](eng[0])
    log("окно закрыто, NESTOR выходит")
    os._exit(0)                                      # мост ждёт игру без срока: выйти сразу


if __name__ == "__main__":
    main()
