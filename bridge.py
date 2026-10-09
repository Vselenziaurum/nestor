# -*- coding: utf-8 -*-
"""NESTOR, мост мод <-> модель (первая версия, 04.10.2026, НЕСТОР.md).

Мод (BP_NSEntry) раз в секунду пишет слот NS_Out (класс /NESTOR/BP_NSBox:
State строка, Seq число) в %LOCALAPPDATA%\\Stalker2\\Saved\\SaveGames и читает
NS_In (Reply строка, ReplySeq число). Новый ответ мод запоминает и следующей
записью отдаёт эхом: State = "seq=<N>|got=<ReplySeq>:<Reply>". По эху мост
видит, что круг замкнулся, без вывода на экран.

Формат слота GVAS (З-110): заголовок (версия 3, UE4 522, UE5 1013, движок
5.5.4, 93 пользовательские версии, имя класса), байт управления сериализацией,
теги свойств до "None", хвост. Тег при UE5 >= 1012 (полное имя типа):
  имя FString, тип [FString + int32 число вложенных] на каждый узел,
  размер int32, флаги uint8, [индекс int32], [GUID 16], [расширения uint8], значение.
Заголовок для NS_In берётся из NS_Out, который пишет сама игра: версии
совпадают байт в байт. Строки FString: длина > 0 латиница с нулём в конце,
длина < 0 UTF-16 (по-русски движок пишет так).

Запуск:
  python bridge.py --model none        канал без модели: «проверка связи N»
  python bridge.py                     ответы модели 9B (llama-server на 8081)
  python bridge.py --every 8 --rounds 5
"""
import argparse
import json
import math
import os
import re
import struct
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions as _actions  # noqa: E402  руки чутья (0.0.5)
import lang as L  # noqa: E402  русский и английский (выпуск на Nexus)

sys.stdout.reconfigure(encoding="utf-8")
SAVES = os.path.join(os.environ["LOCALAPPDATA"], "Stalker2", "Saved", "SaveGames")
OUT = os.path.join(SAVES, "NS_Out.sav")
IN = os.path.join(SAVES, "NS_In.sav")
# журнал по умолчанию рядом с файлом; nestor.exe ставит свой (nestor.log в папке программы)
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.log")


# ---------------------------------------------------------------- GVAS

def rd_fstring(b, o):
    n, = struct.unpack_from("<i", b, o)
    o += 4
    if n == 0:
        return "", o
    if n > 0:
        return b[o:o + n - 1].decode("latin-1"), o + n
    n = -n
    return b[o:o + 2 * (n - 1)].decode("utf-16-le"), o + 2 * n


def wr_fstring(s):
    if s == "":
        return struct.pack("<i", 0)
    try:
        raw = s.encode("ascii") + b"\0"
        return struct.pack("<i", len(raw)) + raw
    except UnicodeEncodeError:
        raw = s.encode("utf-16-le") + b"\0\0"
        return struct.pack("<i", -(len(raw) // 2)) + raw


def parse_header(b):
    """-> (смещение конца заголовка, версия UE5, имя класса)."""
    assert b[:4] == b"GVAS", "не GVAS"
    o = 4
    sgv, ue4 = struct.unpack_from("<ii", b, o)
    o += 8
    ue5 = None
    if sgv >= 3:
        ue5, = struct.unpack_from("<i", b, o)
        o += 4
    o += 10                                  # движок: major, minor, patch (uint16), changelist (uint32)
    _branch, o = rd_fstring(b, o)
    _fmt, cnt = struct.unpack_from("<ii", b, o)
    o += 8 + cnt * 20                        # пользовательские версии: GUID 16 + int32
    cls, o = rd_fstring(b, o)
    return o, ue5, cls


def parse_props(b, o, ue5):
    """Теги свойств от смещения o -> ({имя: значение}, смещение после "None")."""
    props = {}
    if ue5 is not None and ue5 >= 1011:
        o += 1                               # байт управления сериализацией (расширения класса)
    while True:
        name, o = rd_fstring(b, o)
        if name == "None":
            return props, o
        if ue5 is not None and ue5 >= 1012:
            types = []
            pending = 1
            while pending:
                t, o = rd_fstring(b, o)
                inner, = struct.unpack_from("<i", b, o)
                o += 4
                types.append(t)
                pending += inner - 1
            size, = struct.unpack_from("<i", b, o)
            o += 4
            flags = b[o]
            o += 1
            if flags & 0x01:
                o += 4
            if flags & 0x02:
                o += 16
            if flags & 0x04:
                o += 1
            typ = types[0]
        else:
            typ, o = rd_fstring(b, o)
            size, _idx = struct.unpack_from("<ii", b, o)
            o += 8
            flags = 0
            has_guid = b[o]
            o += 1 + (16 if has_guid else 0)
        val = b[o:o + size]
        if typ == "IntProperty":
            props[name] = struct.unpack_from("<i", val, 0)[0]
        elif typ == "StrProperty":
            props[name] = rd_fstring(val, 0)[0]
        elif typ == "BoolProperty":
            props[name] = bool(flags & 0x10)
        else:
            props[name] = (typ, val)
        o += size


def tag(name, typ, value_bytes):
    """Тег формата UE5 >= 1012: имя, тип [имя, 0 вложенных], размер, флаги 0, значение."""
    return (wr_fstring(name) + wr_fstring(typ) + struct.pack("<i", 0)
            + struct.pack("<i", len(value_bytes)) + b"\0" + value_bytes)


def build_in(template, reply, reply_seq, cmd=""):
    """Слот NS_In по образцу NS_Out: тот же заголовок и хвост, свои теги.
    cmd: консольные команды через «;» (мод 0.0.5 исполняет их с новым ответом;
    старый мод поля Cmd не знает и пропускает тег)."""
    hend, ue5, cls = parse_header(template)
    assert ue5 is not None and ue5 >= 1012, "формат тегов старше 5.4: %s" % ue5
    _props, pend = parse_props(template, hend, ue5)
    tail = template[pend:]
    body = (b"\0" + tag("Reply", "StrProperty", wr_fstring(reply))
            + tag("ReplySeq", "IntProperty", struct.pack("<i", reply_seq)))
    if cmd:
        body += tag("Cmd", "StrProperty", wr_fstring(cmd))
    return template[:hend] + body + wr_fstring("None") + tail


def read_out():
    b = open(OUT, "rb").read()
    hend, ue5, cls = parse_header(b)
    props, _ = parse_props(b, hend, ue5)
    return b, cls, props


# ---------------------------------------------------------------- обстановка 0.0.3

# Коды фракций в именах мешей тел (ПРАВИЛА.md З-51): SK_ful_<код>_NN; именные меши
# SK_<Фракция><Имя>_Full_Merged со словом фракции в разном написании.
FACTION_CODES = {"ban": "бандит", "dol": "долговец", "svo": "свободовец", "mer": "наёмник", "mil": "военный",
                 "mon": "монолитовец", "sta": "одиночка", "sci": "учёный", "war": "боец Варты",
                 "sel": "гражданский", "tec": "техник", "corp": "боец Корпуса", "spark": "боец Искры",
                 "noon": "боец Полдня"}
FACTION_WORDS = [("bandit", "бандит"), ("merc", "наёмник"), ("military", "военный"), ("militar", "военный"),
                 ("neutral", "одиночка"), ("scien", "учёный"), ("varta", "боец Варты"), ("noon", "боец Полдня"),
                 ("spark", "боец Искры"), ("iskra", "боец Искры"), ("corpus", "боец Корпуса"),
                 ("duty", "долговец"), ("freedom", "свободовец"), ("monolith", "монолитовец")]
MUTANTS = [("boar", "кабан"), ("flesh", "плоть"), ("blinddog", "слепой пёс"), ("snork", "снорк"),
           ("bloodsucker", "кровосос"), ("chimera", "химера"), ("pseudogiant", "псевдогигант"),
           ("pseuvdogiant", "псевдогигант"), ("controller", "контролёр"), ("burer", "бюрер"),
           ("poltergeist", "полтергейст"), ("pseudodog", "псевдособака"), ("psevdodog", "псевдособака"),
           ("deer", "олень"), ("bayun", "баюн"), ("cat", "баюн"), ("rat", "крыса"), ("jerboa", "тушкан"),
           ("zombie", "зомби")]


def who(mesh):
    m = mesh.lower()
    if m.startswith("sk_ful_"):
        code = m[7:].split("_", 1)[0]
        return FACTION_CODES.get(code, "человек")
    for word, name in FACTION_WORDS:
        if word in m:
            return name
    # Именные НПС: SK_<Имя>_Full_Merged. Журнал 05.10.2026 07:05: бармена Терикона
    # (SK_TerriconBartender_Full_Merged) чутьё приняло за мутанта: «Спереди бармен. Не
    # стой». Слова мутантов в именах людей тоже встречаются («rat» в Pirate), поэтому
    # проверка именного меша раньше мутантов.
    if "full_merged" in m or "_full_" in m:
        return "человек"
    for word, name in MUTANTS:
        if word in m:
            return name
    return "существо (" + mesh + ")"


def _xyz(text, keys=("X", "Y", "Z")):
    """'X=1.0 Y=2.0 Z=3.0' (Conv_VectorToString, у поворота P= Y= R=) -> кортеж чисел."""
    v = dict(kv.split("=", 1) for kv in text.split() if "=" in kv)
    return tuple(float(v[k]) for k in keys)


def _num(text, kind=float):
    try:
        return kind(float(text)) if kind is int else kind(text)
    except ValueError:
        return None


# 0.0.4: о самом Скифе (функции Obj/PC/CppMediator, все с настоящим кодом,
# tools-isreal.py 04.10.2026). Шкалы прототипа Player (ObjPrototypes.cfg): здоровье,
# кровотечение, радиация, голод, сонливость до 100; пси копится и спадает по 1 в секунду.
ME_FLOATS = ("hp", "hpm", "rad", "bl", "psy", "sp", "spm", "hu", "sl", "dr", "mo", "inv")
ME_BOOLS = ("em", "fl", "cr", "dl", "lm")


def parse_state(state):
    """'seq=N|pos=X=.. Y=.. Z=..|a=меш,см,цель,угроза,зомби|...|got=K:текст' -> словарь.

    0.0.4 добавил о самом Скифе rot= (поворот взгляда), hp=, hpm= (здоровье и его
    предел), w= (SID предмета в руках), am= (патронов в магазине), rad= (радиация),
    bl= (кровотечение), psy= (пси), sp=, spm= (выносливость), hu= (голод), sl=
    (сонливость), dr= (опьянение), mo= (купоны), inv= (незаметность), em= (выброс),
    fl= (фонарь), cr= (присел), dl= (в диалоге), lm= (хромает), а агенту шестое
    поле: место X=.. Y=.. Z=.. для направления. Старый мод этих полей не пишет:
    me = None, направления нет.
    """
    out = {"seq": None, "v": 0, "pos": None, "pos_cm": None, "yaw": None, "agents": [], "dead": [], "got": None,
           "me": None}
    me = {}
    body = state.split("|got=", 1)
    if len(body) == 2:
        out["got"] = body[1]
    for part in body[0].split("|"):
        key, _, val = part.partition("=")
        try:
            if key == "seq":
                out["seq"] = int(val) if val.lstrip("-").isdigit() else None
            elif key == "v":
                out["v"] = int(val) if val.isdigit() else 0
            elif key == "pos":
                out["pos_cm"] = _xyz(val)
                out["pos"] = tuple(round(c / 100.0) for c in out["pos_cm"])
            elif key == "rot":
                out["yaw"] = _xyz(val, ("Y",))[0]
            elif key in ME_FLOATS:
                me[key] = _num(val)
            elif key == "am":
                me[key] = _num(val, int)
            elif key == "w":
                me[key] = val
            elif key in ME_BOOLS:
                me[key] = val == "true"
            elif key == "a":
                f = val.split(",")
                if len(f) < 5:
                    continue
                a = {"who": ("зомби " if f[4] == "true" else "") + who(f[0]),
                     "m": round(float(f[1]) / 100.0), "aim": f[2] == "true", "threat": f[3] == "true", "loc": None,
                     "uid": None}
                if len(f) >= 6:
                    a["loc"] = _xyz(f[5])
                if len(f) >= 7:                   # 0.0.7: номер существа для рук по цели
                    a["uid"] = _num(f[6], int)
                out["agents"].append(a)
            elif key == "d":
                # 0.0.7: труп ближе 30 м «меш,см,место,номер» (поднять зомби)
                f = val.split(",")
                if len(f) >= 4:
                    out["dead"].append({"who": who(f[0]), "m": round(float(f[1]) / 100.0), "loc": _xyz(f[2]),
                                        "uid": _num(f[3], int)})
        except (KeyError, ValueError):
            continue
    out["me"] = me or None
    out["agents"].sort(key=lambda a: a["m"])
    out["dead"].sort(key=lambda a: a["m"])
    return out


def direction(st, a):
    """Где существо относительно взгляда Скифа: «сзади справа», «спереди, сверху».

    Рыск UE растёт от +X к +Y, то есть по часовой при взгляде сверху: угол
    atan2(dy, dx) минус рыск взгляда больше нуля, значит справа.
    """
    if st.get("yaw") is None or st.get("pos_cm") is None or a.get("loc") is None:
        return None
    dx, dy, dz = (a["loc"][i] - st["pos_cm"][i] for i in range(3))
    ang = (math.degrees(math.atan2(dy, dx)) - st["yaw"] + 180.0) % 360.0 - 180.0
    side = L.tr("right") if ang > 0 else L.tr("left")
    x = abs(ang)
    if x <= 30:
        s = L.tr("front")
    elif x <= 70:
        s = L.tr("front_side", side if not L.en() else side.replace("on the ", ""))
    elif x <= 110:
        s = side
    elif x <= 150:
        s = L.tr("back_side", side if not L.en() else side.replace("on the ", ""))
    else:
        s = L.tr("back")
    flat = math.hypot(dx, dy)
    if dz > 500 and dz > 0.3 * flat:
        s += L.tr("above")
    elif dz < -500 and -dz > 0.3 * flat:
        s += L.tr("below")
    return s


def side_of(st, a):
    """Грубая сторона для узнавания стрелка без номера: front, side, back или ""."""
    if st.get("yaw") is None or st.get("pos_cm") is None or a.get("loc") is None:
        return ""
    dx, dy = a["loc"][0] - st["pos_cm"][0], a["loc"][1] - st["pos_cm"][1]
    x = abs((math.degrees(math.atan2(dy, dx)) - st["yaw"] + 180.0) % 360.0 - 180.0)
    return "front" if x <= 70 else "back" if x > 110 else "side"


def pet_label(a, pets):
    """Свой зверь Скифа: вид, который игрок назвал своим (питомец из ZonePets или
    Project Leash), и этот зверь не целится в Скифа. Дикий того же вида, взявший
    Скифа на прицел, своим не считается."""
    return bool(pets) and a["who"] in pets and not a["aim"]


def describe(st, limit=8, pets=()):
    """Обстановка словами для модели. С номерами существ (мод 0.0.7) каждое помечено №N:
    по нему модель указывает цель пси-удара (actions.psy_strike), трупы своим списком."""
    bodies = ""
    if st.get("dead"):
        bodies = L.tr("bodies", "; ".join(L.tr("num", i) + L.tr("agent", L.name(d["who"]), d["m"])
                                          + ((" " + direction(st, d)) if direction(st, d) else "")
                                          for i, d in enumerate(st["dead"][:4], 1)))
    if not st["agents"]:
        return L.tr("nobody") + bodies
    parts = []
    for i, a in enumerate(st["agents"][:limit], 1):
        name = L.name(a["who"])
        if pet_label(a, pets):
            name += L.tr("pet_dog") if "пёс" in a["who"] or "собака" in a["who"] else L.tr("pet_beast")
        s = (L.tr("num", i) if a.get("uid") else "") + L.tr("agent", name, a["m"])
        d = direction(st, a)
        if d:
            s += " " + d
        if a["aim"]:
            s += L.tr("aiming")
        elif a["threat"]:
            s += L.tr("alert")
        parts.append(s)
    more = len(st["agents"]) - limit
    return L.tr("near") + "; ".join(parts) + (L.tr("more", more) if more > 0 else ".") + bodies


# ---------------------------------------------------------------- сам Скиф (0.0.4)

_ITEMS = {}
# Постоянные предметы рук заведены константами ядра, а не прототипами (А-8): их
# имён в локализации по ключу sid_items_<SID>_name нет.
HAND_WORDS = [("knife", "нож", "knife"), ("bolt", "болт", "bolt"), ("detector", "детектор", "detector"),
              ("binocular", "бинокль", "binoculars"), ("pda", "ПДА", "PDA"), ("grenade", "граната", "grenade"),
              ("medkit", "аптечка", "medkit"), ("bandage", "бинт", "bandage")]


def item_name(sid):
    """SID предмета -> имя на языке выхода (llm/items_<язык>.json из Game_<язык>.locres)."""
    if not sid or sid.lower() == "none":
        return ""
    if L.LANG not in _ITEMS:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "items_%s.json" % L.LANG)
        try:
            _ITEMS[L.LANG] = json.load(open(p, encoding="utf-8"))
        except OSError:
            _ITEMS[L.LANG] = {}
    items = _ITEMS[L.LANG]
    if sid in items:
        return items[sid]
    # варианты оружия (GuardGunPKP_MG, Deluxe_GunAK74_ST) в локализации отдельно не
    # записаны: имя базового по таблице SpareMag (поле setup), иначе без приставки
    base = (_actions.weapons().get("weapons", {}).get(sid) or {}).get("setup")
    for cand in (base, re.sub(r"^(Guard|Deluxe_|Unique_)", "", sid)):
        if cand and cand in items:
            return items[cand]
    low = sid.lower()
    for word, ru, en_ in HAND_WORDS:
        if word in low:
            return en_ if L.en() else ru
    return sid


def is_gun(sid):
    return bool(sid) and sid.lower().startswith(("gun", "dlc01_gun"))


def plural(n, one, few, many):
    return L.plural(n, one, few, many)


def hp_pct(st):
    me = st.get("me") or {}
    if me.get("hp") is None or not me.get("hpm"):
        return None
    return max(0, min(100, round(100.0 * me["hp"] / me["hpm"])))


def me_line(st):
    """Что чутьё знает о самом Скифе; чего мод не дал, о том прямо сказано."""
    me = st.get("me") if st else None
    if not me:
        return L.tr("me_none")
    parts = []
    pct = hp_pct(st)
    if pct is not None:
        parts.append(L.tr("hp", pct))
    sid = me.get("w") or ""
    name = item_name(sid)
    parts.append(L.tr("holding", name) if name else L.tr("empty_hands"))
    if is_gun(sid) and me.get("am") is not None and me["am"] >= 0:
        parts.append(L.tr("mag", me["am"], plural(me["am"], *L.tr("round"))))
    parts += body_words(me)
    if me.get("mo") is not None:
        mo = int(me["mo"])
        parts.append(L.tr("money", mo, plural(mo, *L.tr("coupon"))))
    if me.get("em"):
        parts.append(L.tr("emission_on"))
    return L.tr("me") + ", ".join(parts) + "."


def body_words(me):
    """Состояние тела словами. Пороги предварительные (шкалы до 100 из прототипа
    Player), уточнить по строкам «[скиф]» журнала моста после первого прогона."""
    w = []
    v = me.get
    if (v("bl") or 0) > 0:
        w.append(L.tr("bleed_hard") if v("bl") >= 30 else L.tr("bleed"))
    if (v("rad") or 0) > 1:
        w.append(L.tr("rad_hard") if v("rad") >= 50 else L.tr("rad"))
    if (v("psy") or 0) > 1:
        w.append(L.tr("psy"))
    if v("spm") and v("sp") is not None and v("sp") < 0.2 * v("spm"):
        w.append(L.tr("winded"))
    if (v("hu") or 0) >= 60:
        w.append(L.tr("starving") if v("hu") >= 85 else L.tr("hungry"))
    if (v("sl") or 0) >= 60:
        w.append(L.tr("exhausted") if v("sl") >= 85 else L.tr("sleepy"))
    if (v("dr") or 0) > 1:
        w.append(L.tr("drunk"))
    if v("lm"):
        w.append(L.tr("limp"))
    if v("cr"):
        w.append(L.tr("crouch"))
    if v("fl"):
        w.append(L.tr("torch"))
    if v("dl"):
        w.append(L.tr("talking"))
    return w


# ---------------------------------------------------------------- шестое чувство (0.0.4, только мост)

# Журнал 0.0.3 (04.10.2026, 17:15-17:27): из 18 реплик 12 сказаны «в тишине» или про
# мирных в хабе; модель перечисляла всех и громоздила метафоры («шумит хоровод… гадательной
# книги», «тени подберут твою добычу» шесть раз подряд), прицел псевдособаки назвала
# «наводит порядок». Чувство говорит только по поводу, повод один, тон задан образцами.
# Роль и образцы чутья теперь в lang.py (PROMPTS[язык]["sixth_sys"], ["sixth_shots"]).
BEAST_IN, BEAST_OUT = 25, 35       # м: «появился» ближе 25, «ушёл» дальше 35
BEAST_AGAIN = 60                   # с: тот же вид «появился» снова раньше: не новость (пси-двойники псевдособаки)
# Прицел в перестрелке (журнал автора 08.10.2026 13:39-13:41: шесть «целится» подряд раз в
# 25 с): о новом стрелке сразу, о тех же самых снова не раньше AIM_AGAIN_S, между любыми
# предупреждениями о прицеле не меньше AIM_MIN_S. Стрелок узнаётся по номеру (мод 0.0.7),
# у старого мода по виду и стороне.
AIM_AGAIN_S, AIM_MIN_S = 75, 15
# Разговор F4 (0.2.1): обменов в контексте, окно своих фраз чутья, обменов из прошлой игры
HISTORY_N, SAID_WINDOW_S, PREV_GAME_N = 6, 600, 3
HP_LOW, HP_OK = 30, 50             # %: «тяжело ранен» ниже 30, снова здоров выше 50
RAD_HI, RAD_OK = 30, 10            # радиация (до 100): «растёт» выше 30, спала ниже 10


def far(m):
    return L.tr("very_close") if m <= 10 else (L.tr("close") if m <= 25 else L.tr("nearby"))


def where(st, a):
    d = direction(st, a)
    return L.tr("where2", d, far(a["m"])) if d else L.tr("where", far(a["m"]))


def is_human(name):
    return not any(name == m or name.endswith(" " + m) for _w, m in MUTANTS) and not name.startswith("существо")


def pets_of(mem, memo):
    """Свои звери: из памяти (между играми) и из этой игры (если памяти нет)."""
    return set(mem.get("pets", ())) | (memo.pets() if memo is not None else set())


def sixth_reason(st, mem, now, args, memo=None):
    """-> (повод, ключ) или None. mem: что уже сказано (ключ -> время); memo: память (дежавю)."""
    ag = st["agents"]
    me = st.get("me") or {}

    def fresh(key, gap):
        return now - mem.get(key, -1e9) >= gap

    new_seq = st.get("seq") != mem.get("seq")
    mem["seq"] = st.get("seq")
    if me.get("dl") or mem.get("dead"):   # Скиф говорит с НПС или мёртв: чутьё молчит
        return None
    aim = [a for a in ag if a["aim"]]
    if aim and fresh("any", args.min_gap) and fresh("aim_any", AIM_MIN_S):
        warned = mem.setdefault("aim_warned", {})

        def akey(a):
            return a["uid"] if a.get("uid") is not None else "%s|%s" % (a["who"], side_of(st, a))
        new = [a for a in aim if now - warned.get(akey(a), -1e9) >= AIM_AGAIN_S]
        if new:
            # зашедший со спины раньше ближнего спереди
            a = sorted(new, key=lambda x: (side_of(st, x) != "back", x["m"]))[0]
            for x in aim:                       # одно предупреждение на всех, кто сейчас целится
                warned[akey(x)] = now
            mem["aim_any"] = now
            if is_human(a["who"]):
                return L.tr("r_aim_human", where(st, a)), "aim:" + a["who"]
            # мутант: свой род фраз (aimm), иначе модель брала «мушку» у людей (08.10.2026:
            # «Спереди мушка. Беги» про кабана); зверю этот же вид сейчас не повод
            mem["beast:" + a["who"]] = now
            return L.tr("r_aim_mutant", L.name(a["who"]), where(st, a)), "aimm:" + a["who"]
    # Тяжёлое ранение: один раз на спуск ниже HP_LOW, снова только после HP_OK. Сразу за
    # прицелом: 04.10.2026 21:45:41 здоровье 12 %, а «тяжело ранен» прозвучало в 21:46:19,
    # после дежавю и прицела.
    pct = hp_pct(st)
    if pct is not None:
        if pct < HP_LOW and not mem.get("hp_low") and pct > 0 and fresh("any", args.min_gap):
            mem["hp_low"] = True
            return L.tr("r_hp"), "hp"
        if pct > HP_OK:
            mem["hp_low"] = False
    # Выброс: один раз на начало (мод 0.0.4, CppMediator.IsEmissionActive).
    if me.get("em") and not mem.get("em_on"):
        if fresh("any", args.min_gap):
            mem["em_on"] = True
            return L.tr("r_emission"), "emission"
    elif not me.get("em"):
        mem["em_on"] = False
    # Память: Скиф загрузился после смерти; потом подход к месту, где его убивали.
    if mem.get("after_death") and fresh("any", args.min_gap):
        return L.tr("r_back", L.name(mem.pop("after_death"))), "back"
    if fresh("any", args.min_gap):
        r = deja_reason(st, mem, now, memo)
        if r:
            return r
    # Тело: кровь, пси, радиация. Каждое один раз на начало, снова после спада и не
    # чаще раза в минуту: журнал 04.10.2026 20:34:18 и 20:34:27, кровь от укусов пса
    # то появлялась, то спадала, и «Течёт. Перевяжи.» прозвучало дважды за 9 с.
    for key, on, off, reason in (("bl", 0.0, 0.0, L.tr("r_bl")), ("psy", 1.0, 0.5, L.tr("r_psy")),
                                 ("rad", RAD_HI, RAD_OK, L.tr("r_rad"))):
        val = me.get(key)
        if val is None:
            continue
        flag = "on_" + key
        if val > on and not mem.get(flag) and fresh("any", args.min_gap) and fresh("body:" + key, 60):
            mem[flag] = True
            return reason, "body:" + key
        if val <= off:
            mem[flag] = False
    # Зверь рядом. Журнал 04.10.2026 19:38-19:44: ручная псевдособака (Project Leash) весь
    # запуск держалась в 2-22 м, и чувство раз в минуту кричало «Оно рядом. Вниз!». Теперь
    # про спокойного зверя один раз, когда вид появился ближе BEAST_IN; повтор, только если
    # зверь целится в Скифа или насторожен. Ушёл вид, только когда дальше BEAST_OUT: пёс
    # автора ходит у границы 25 м (19:50-19:52: 22, 26, 25 м) и без зазора «появлялся»
    # заново каждые полминуты.
    # свой зверь Скифа (вид назван игроком, pet_claim) не повод, пока не целится
    pets = pets_of(mem, memo)
    beasts = [a for a in ag if not is_human(a["who"]) and not a["who"].startswith("зомби") and not pet_label(a, pets)]
    prev = mem.get("near_prev", set())
    closest = {}
    for a in beasts:
        closest[a["who"]] = min(closest.get(a["who"], 1e9), a["m"])
    kinds_now = {k for k, m in closest.items() if m <= (BEAST_OUT if k in prev else BEAST_IN)}
    near = [a for a in beasts if a["who"] in kinds_now and a["m"] <= BEAST_OUT]
    if new_seq:
        mem["near_prev"] = kinds_now
        mem["arrived"] = (mem.get("arrived", set()) | (kinds_now - prev)) & kinds_now
    hostile = [a for a in near if (a["aim"] or a["threat"]) and a["m"] <= BEAST_IN]
    pending = [a for a in near if a["who"] in mem.get("arrived", set())]
    pick = None
    if hostile and fresh("beast:" + hostile[0]["who"], 90):
        pick = hostile[0]
    elif pending:
        # Журнал 05.10.2026 10:39-10:40: шесть «пёс сзади» за полторы минуты. Псевдособака
        # плодит пси-двойников, они рождаются и гаснут, и вид «появлялся» заново каждые
        # 10-50 с без всякой паузы. Теперь повторное появление вида раньше BEAST_AGAIN
        # не повод, и в очереди оно не копится.
        for a in pending:
            if fresh("beast:" + a["who"], BEAST_AGAIN):
                pick = a
                break
            mem.setdefault("arrived", set()).discard(a["who"])
    if pick and fresh("any", args.min_gap):
        kind = pick["who"]
        key = "beast:" + kind
        mem.setdefault("arrived", set()).discard(kind)
        n = sum(1 for a in near if a["who"] == kind)
        if n > 1:
            return L.tr("r_beasts", L.name(kind), L.tr("count").get(n, L.tr("many")), where(st, pick)), key
        return L.tr("r_beast", L.name(kind), where(st, pick)), key
    zomb = [a for a in ag if a["who"].startswith("зомби") and a["m"] <= 30]
    if zomb and fresh("zombie", 90) and fresh("any", args.min_gap):
        return L.tr("r_zombie", where(st, zomb[0])), "zombie"
    if not ag and fresh("quiet", args.quiet) and fresh("any", args.quiet):
        return L.tr("r_quiet"), "quiet"
    return None


def _chat(msgs, temperature=0.7, max_tokens=70, timeout=60, schema=None):
    """Запрос к llama-server; длинное и среднее тире автор не терпит: запятой.
    schema: ответ строго по JSON-схеме (как режиссёр в замере, measure.py)."""
    body = {"model": "qwen", "temperature": temperature, "max_tokens": max_tokens, "messages": msgs}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "nestor", "strict": True,
                                                                          "schema": schema}}
    req = urllib.request.Request("http://127.0.0.1:8081/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = (data["choices"][0]["message"].get("content") or "").strip()
    for dash in (" — ", " – ", "—", "–"):
        text = text.replace(dash, ", ")
    return text


def _line(text, limit):
    return text.replace("\n", " ").strip().strip("«»\"' ")[:limit]


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _bare(s):
    return " ".join(re.findall(r"\w+", s.lower().replace("ё", "е")))


def repeats(text, recent):
    """Повтор недавнего: то же предложение в два слова и длиннее или три слова подряд.
    Журнал автора 08.10.2026 13:39-13:41: «живому не место» в шести фразах подряд."""
    old = {_bare(s) for r in recent for s in _sentences(r)}
    grams = set()
    for r in recent:
        w = _bare(r).split()
        grams |= {tuple(w[i:i + 3]) for i in range(len(w) - 2)}
    if any(_bare(s) in old and len(_bare(s).split()) >= 2 and not set(_bare(s).split()) <= DIR_WORDS
           for s in _sentences(text)):
        return True
    w = _bare(text).split()
    return any(tuple(w[i:i + 3]) in grams and not set(w[i:i + 3]) <= DIR_WORDS for i in range(len(w) - 2))


# направления и связки: их повтор не повтор («Сзади справа.» в каждом предупреждении)
DIR_WORDS = {"спереди", "сзади", "слева", "справа", "сверху", "снизу", "рядом", "близко", "совсем", "неподалеку",
             "далеко", "и", "в", "на", "ahead", "behind", "left", "right", "above", "below", "close", "near", "nearby",
             "very", "on", "the", "far", "to", "and", "in"}


def llm_sixth(reason, recent, avoid=()):
    """recent: прошлые фразы того же рода повода (в просьбу не повторять); avoid: все
    недавние фразы для проверки повтора после ответа."""
    msgs = [{"role": "system", "content": L.P("sixth_sys")}]
    for q, a in L.P("sixth_shots"):
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    # recent: прошлые фразы того же рода повода. Общий список 21:45:38 дал «Течёт кровь.
    # Сзади псевдособака, спереди ещё одна»: псов модель взяла из чужих фраз.
    q = reason
    if recent:
        q += L.P("no_repeat") + " / ".join(recent[-3:])
    msgs.append({"role": "user", "content": q})
    seen = list(recent[-3:]) + list(avoid)

    def ask(temp):
        t = _line(_chat(msgs, temp, 30), 120)
        # «Пёс сзади. Коротко.» (журнал 05.10.2026 10:39): модель дописала слово из своей роли
        return re.sub(r"\s*(Коротко|Кратко|Briefly|Short)\.?\s*$", "", t, flags=re.I) or t
    text = ask(0.7)
    if seen and repeats(text, seen):
        text = ask(1.0)
        if repeats(text, seen):
            # повторённое предложение долой, если что-то остаётся (направление важнее хвоста)
            old = {_bare(s) for r in seen for s in _sentences(r)}
            keep = [s for s in _sentences(text) if not (_bare(s) in old and len(_bare(s).split()) >= 2
                                                         and not set(_bare(s).split()) <= DIR_WORDS)]
            text = " ".join(keep) or text
    return text


# ---------------------------------------------------------------- жизнь Скифа и дежавю (память, memory.py)

DEJA_R_CM, DEJA_DZ_CM = 3500, 1500   # дежавю: ближе 35 м к месту, где Скифа убивали, по высоте до 15 м
DEJA_AWAY_CM, DEJA_AGAIN_S = 10000, 300   # снова напомнить: отходил дальше 100 м и прошло 5 мин
DEJA_FRESH_S = 900                   # место этой жизни (без загрузки мира) молчит 15 мин
# Одно дежавю на все места раз в DEJA_ANY_S: журнал автора 08.10.2026, 33 фразы из 95 были
# дежавю, у мест, где он тестирует моды, по три-четыре подряд за полминуты (14:07:34,
# 14:07:50, 14:08:05)
DEJA_ANY_S = 240
NEAR_PCT = 20                        # «едва не убили»: здоровье ниже 20 %
RING_S = 12                          # кто целился за столько секунд до смерти, тот и убийца


def is_dead(st):
    me = st.get("me") or {}
    return me.get("hp") is not None and me["hp"] <= 0.005 * (me.get("hpm") or 100.0)


def cause_of(me):
    if me.get("em"):
        return "выброс"
    if (me.get("rad") or 0) >= 60:
        return "радиация"
    if (me.get("psy") or 0) >= 30:
        return "пси"
    if (me.get("bl") or 0) >= 30:
        return "кровотечение"
    return ""


def killer(mem):
    """Ближний из тех, кто целился в Скифа за последние RING_S с; иначе пусто.
    Без прицела не винить никого: рядом может стоять свой пёс (ZonePets, Leash)."""
    best = None
    for _t, ags in mem.get("ring", []):
        for a in ags:
            if best is None or a["m"] < best["m"]:
                best = a
    return best["who"] if best else ""


def new_session():
    import collections
    return {"t0": time.time(), "aims": collections.Counter(), "beasts": set(), "events": collections.Counter(),
            "deaths": [], "near": [], "weapons": [], "talk": []}


def track_life(st, mem, memo, sess, now):
    """На каждой новой записи мода: смерть, «едва не убили», загрузка мира, смена
    оружия. -> строки для журнала. Счётчики подсистемы после загрузки мира идут с
    нуля (0.0.1): номер записи меньше прежнего значит новый мир."""
    out = []
    seq = st.get("seq")
    if seq is not None and mem.get("life_seq") is not None and seq < mem["life_seq"]:
        out.append("мир загружен заново")
        mem["life_start"] = now
        if mem.get("dead"):
            mem["after_death"] = mem.get("dead_who") or "неизвестно что"
        mem["dead"] = mem["near_done"] = False
        mem["ring"] = []
        mem["deja_said"], mem["deja_away"] = {}, set()
    mem["life_seq"] = seq
    mem["ring"] = [(t, a) for t, a in mem.get("ring", []) if now - t <= RING_S] + \
        [(now, [a for a in st["agents"] if a["aim"]])]
    me = st.get("me") or {}
    sid = me.get("w") or ""
    if is_gun(sid):
        name = item_name(sid)
        if name not in sess["weapons"]:
            sess["weapons"].append(name)
    pct = hp_pct(st)
    if pct is None or st.get("pos_cm") is None:
        return out
    if is_dead(st):
        if not mem.get("dead"):
            who = killer(mem) or cause_of(me)
            p = memo.record_place("смерть", st["pos_cm"], killer(mem), cause_of(me)) if memo else None
            mem["dead"], mem["dead_who"] = True, who
            sess["deaths"].append(who or "неизвестно что")
            out.append("СМЕРТЬ Скифа: %s, место %s" % (who or "неизвестно что", p["id"] if p else "-"))
        return out
    mem["dead"] = False
    if pct < NEAR_PCT and not mem.get("near_done"):
        who = killer(mem) or cause_of(me)
        p = memo.record_place("рана", st["pos_cm"], killer(mem), cause_of(me)) if memo else None
        mem["near_done"] = True
        sess["near"].append(who or "неизвестно что")
        out.append("едва не убили (%d %%): %s, место %s" % (pct, who or "неизвестно что", p["id"] if p else "-"))
    if pct > HP_OK:
        mem["near_done"] = False
    return out


def deja_reason(st, mem, now, memo):
    """Повод «здесь тебя уже убивали»: раз на подход к месту в этой жизни."""
    if memo is None or st.get("pos_cm") is None or now - mem.get("deja_any", -1e9) < DEJA_ANY_S:
        return None
    pos = st["pos_cm"]
    said, away = mem.setdefault("deja_said", {}), mem.setdefault("deja_away", set())
    for p in memo.state["places"]:
        if p["id"] in said and flat(p["pos"], pos) > DEJA_AWAY_CM:
            away.add(p["id"])
    for d, p in memo.near_places(pos, DEJA_R_CM, DEJA_DZ_CM):
        pid = p["id"]
        if pid in said and not (pid in away and now - said[pid] >= DEJA_AGAIN_S):
            continue
        # Дежавю только о прошлой жизни: место записано до последней загрузки мира или
        # давно. Журнал 04.10.2026 21:45:41-47: «едва не убили (12 %)» и через 6 с в той
        # же схватке «Знакомое место. Бандит спереди».
        t = p.get("t", 0)
        if not (mem.get("life_start") is not None and t < mem["life_start"]) and time.time() - t < DEJA_FRESH_S:
            continue
        # все места рядом считаются сказанными: одно дежавю на скопление
        for _d, q in memo.near_places(pos, DEJA_R_CM, DEJA_DZ_CM):
            said[q["id"]] = now
            away.discard(q["id"])
        mem["deja_any"] = now
        who = L.name(p.get("who") or p.get("cause") or "")
        if p["kind"] == "смерть":
            # без числа: «Шестой раз сзади», «Тринадцать кабанов» (журнал 08.10.2026)
            what = L.tr("deja_killed_many") if p.get("deaths", 1) > 1 else L.tr("deja_killed")
        else:
            what = L.tr("deja_near")
        dirw = direction(st, {"loc": p["pos"]})
        whr = L.tr("where2", dirw, far(d / 100.0)) if dirw else L.tr("where", far(d / 100.0))
        return L.tr("r_deja", what, " (%s)" % who if who else "", whr), "deja:%d" % pid
    return None


def flat(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def to_history(box, memo, who, text):
    """Строка в историю окна F4 и в память (последние 8 переживают перезапуск и игры)."""
    if box is not None:
        box.add_history(who, text)
    if memo is not None:
        h = memo.state.setdefault("history", [])
        h.append([time.strftime("%d.%m %H:%M"), who, text])
        del h[:-8]
        memo.save()


def note_reason(sess, key):
    """Сказанный повод -> счётчик для строки дневника."""
    kind, _, who_ = key.partition(":")
    names = {"emission": "ev_emission", "hp": "ev_hp", "zombie": "ev_zombie", "back": "ev_back", "deja": "ev_deja"}
    if kind == "aim":
        sess["aims"][L.name(who_)] += 1
    elif kind in ("beast", "aimm"):
        sess["beasts"].add(L.name(who_))
    elif kind == "body":
        sess["events"][L.tr({"bl": "ev_bl", "psy": "ev_psy", "rad": "ev_rad"}.get(who_, "ev_bl"))] += 1
    elif names.get(kind):
        sess["events"][L.tr(names[kind])] += 1


# ---------------------------------------------------------------- разговор (окно на F4, askbox.py)

# Журнал 04.10.2026 19:47-19:51 (первый разговор автора): на «Кто я?» модель ответила о
# себе («Я тот кто видит...»), о себе сказала «Я твой шестое чувство», на вопрос об
# оружии выдумала «В руках пусто» (мост оружия не знал), на «отключи промпты» вышла из
# роли («Не могу выполнять математические задачи»). Роли названы прямо, образцы
# показывают род, «не чую» и отказ в роли.
# Роль разговора, сила, образцы (ask_sys, ask_powers, ask_shots) теперь в lang.py,
# по-русски дословно как проверено 04-05.10.2026.


PET_WORDS = re.compile(r"питом|ручн|мой\s+п[её]с|моя\s+собак|мой\s+зверь|сво[йяё]\s+(п[её]с|собак|зверь)|"
                       r"п[её]с\s+мой|собака\s+моя|зверь\s+мой|спутник|"
                       r"напарни|компаньон|это\s+сво[йяё]\b|не\s+трогай\s+(его|её|пса|собаку)|"
                       r"\bmy\s+(pet|dog|animal|companion|buddy)|\bpet\b|\bcompanion\b|\bit'?s\s+mine\b|"
                       r"don'?t\s+(shoot|touch|hurt)\s+(it|him|her|the\s+dog)", re.I)
PET_NOT = re.compile(r"\bне\s+(мой|моя|свой|своя|питом)|\bnot\s+(my|mine|a\s+pet)", re.I)


def pet_claim(question, st):
    """Скиф говорит «это мой питомец»: вид ближайшего спокойного зверя ближе 20 м
    или None. Журнал 04.10.2026 20:38: на «это мой питомец» и «я уверяю тебя, это
    мой питомец из другого мода» чутьё отвечало «Нет, это враг», «Это не питомец,
    это угроза»: псевдособака ZonePets в 1-4 м, по данным мода просто зверь."""
    if not st or not PET_WORDS.search(question) or PET_NOT.search(question):
        return None
    calm = [a for a in st["agents"] if not is_human(a["who"]) and not a["who"].startswith("зомби")
            and not a["aim"] and a["m"] <= 20]
    return calm[0]["who"] if calm else None


def ask_summary(st, memo=None, pets=()):
    base = (me_line(st) + " " + describe(st, pets=pets)) if st else L.P("ask_none") + L.tr("unknown_around")
    if memo is not None and st and st.get("pos_cm"):
        for d, p in memo.near_places(st["pos_cm"], 50000)[:2]:          # до 500 м, два ближних
            n = p.get("deaths", 0)
            what = ((L.tr("sum_killed_n", n, plural(n, *L.tr("times"))) if n > 1 else L.tr("sum_killed"))
                    if p["kind"] == "смерть" else L.tr("sum_near"))
            who = L.name(p.get("who") or p.get("cause") or "")
            dirw = direction(st, {"loc": p["pos"]})
            base += L.tr("sum_deja", what, " (%s)" % who if who else "", round(d / 100.0), " " + dirw if dirw else "")
    return base


# Глаголы просьбы. Журнал 05.10.2026 10:38: «успокой собак вокруг» не числилось просьбой,
# модель ответила «Сделал», хотя защита отбросила действие, а память записала просьбу
# фактом «Успокаивает собак вокруг».
_DO_RU = (r"дай|дайте|выдай|насыпь|подкинь|подкини|отсыпь|заспавни|спавни|создай|сделай|поставь|включи|выключи|"
          r"убери|верни|вызови|роди|почини|перенеси|начни|останови|успокой|уйми|убей|прогони|отгони|вылечи|подлечи|"
          r"спаси|помоги|найди|покажи|открой|закрой|принеси|достань|телепортируй|перемести|усыпи|разбуди|накорми|"
          r"позови|призови|приведи|наполни|смени|поменяй|переключи|зажги|потуши|сотри|удали|отмени|прекрати|"
          r"добавь|прибавь|убавь|сними|брось|выкинь|сбей|повали|рани|напугай|отпугни|стравь|натрави|подними|"
          r"воскреси|оживи|замедли|ускорь")
_DO_EN = (r"give|spawn|summon|create|repair|fix|teleport|start|stop|remove|bring|kill|calm|heal|help|find|show|open|"
          r"close|fetch|call|change|switch|refill|put|add|clear|cancel|knock|wound|scare|frighten|raise|"
          r"resurrect|slow|speed|patch")
IMPERATIVE = re.compile(r"\b(" + _DO_RU + r"|хочу|можно|" + _DO_EN + r"|make|set|turn|move|want|need|let)\b", re.I)


ASK_DO = re.compile(r"(дать|сделать|заспавнить|спавнить|создать|поставить|включить|выключить|убрать|"
                    r"вернуть|вызвать|починить|перенести|начать|остановить|can you|could you|will you|would you)",
                    re.I)


def llm_confirm(question, done, failed):
    """Реплика ровно о сделанном, когда действия поправило правило (actions.guard,
    merge_rules) и ответ модели к ним уже не подходит."""
    if failed:
        user = L.P("confirm_user") % (question, "; ".join(done) or L.P("nothing"), "; ".join(failed))
    else:
        user = L.P("confirm_user_ok") % (question, "; ".join(done) or L.P("nothing"))
    shot_q, shot_a = L.P("confirm_shot")
    msgs = [{"role": "system", "content": L.P("confirm_sys")},
            {"role": "user", "content": shot_q}, {"role": "assistant", "content": shot_a},
            {"role": "user", "content": user}]
    return _line(_chat(msgs, 0.6, 40), 160)


def _ask_call(msgs, temperature):
    """Ответ по схеме на языке выхода; имена действий модели -> внутренние ключи."""
    raw = _chat(msgs, temperature, 220, schema=_actions.schema(L.action_catalog(_actions.ACTIONS)))
    try:
        data = json.loads(raw)
        acts = [dict(a, do=L.action_in(a.get("do", ""))) for a in (data.get("actions", []) or [])]
        return data.get("reply", ""), acts
    except (ValueError, AttributeError):
        return raw, []


def _ask_json(reply, acts=()):
    return json.dumps({"actions": [dict(a, do=L.action_out(a.get("do", ""))) for a in acts], "reply": reply},
                      ensure_ascii=False)


def ago(t, now=None):
    s = max(1, int((now or time.time()) - t))
    return L.tr("ago_s", s) if s < 90 else L.tr("ago_m", round(s / 60.0))


def said_text(said, now=None, window=SAID_WINDOW_S, limit=4):
    """Свои недавние фразы чутья для разговора: «где он?» после «Сзади бандит» (автор
    09.10.2026: «модель отвечает ровно на 1 вопрос и не помнит, что было до этого»)."""
    now = now or time.time()
    fresh = [(t, x) for t, x in said if now - t <= window][-limit:]
    if not fresh:
        return ""
    quote = '"%s" (%s)' if L.en() else "«%s» (%s)"
    return " " + L.P("ask_said") % "; ".join(quote % (x, ago(t, now)) for t, x in fresh)


def llm_answer(question, situation, history, memory_text="", powers=True, said=()):
    """-> (вопрос для истории, реплика, действия). С силой (мод 0.0.5) ответ по схеме
    actions.ACTION_SCHEMA; без неё простой текст и роль без силы, как в 0.0.4.
    history: прежние (вопрос, ответ, действия) без сводок и памяти: память только в
    последнем сообщении, сводки устаревают, а контекст 8192 (0.2.1: шесть обменов вместо
    трёх со сводками). said: свои недавние фразы чутья (время, текст)."""
    cat = L.action_catalog(_actions.ACTIONS)
    powers_text = L.P("ask_powers") % "; ".join("%s: %s" % kv for kv in sorted(cat.items()))
    msgs = [{"role": "system", "content": L.P("ask_sys") + (powers_text if powers else "")}]
    wrap = _ask_json if powers else (lambda a, acts=(): a)
    for shot in L.P("ask_shots"):
        mtext, summ, q, a = shot[:4]
        if not powers and (len(shot) > 4 or q == L.P("what_can")):
            continue
        acts = [dict(x, do=L.action_in(x["do"])) for x in shot[4]] if len(shot) > 4 else ()
        msgs += [{"role": "user", "content": L.P("ask_user") % (mtext, summ, q)},
                 {"role": "assistant", "content": wrap(a, acts)}]
    # история с прежними действиями: без них модель видела свои ответы пустыми и
    # повторяла отказ (журнал 05.10.2026 07:16, «Кровососа нет, но псевдособака уснет»)
    for h in history[-HISTORY_N:]:
        q, a = h[0], h[1]
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": wrap(a, h[2] if len(h) > 2 else ())}]
    user = L.P("ask_situation") % (situation + said_text(said), question)
    msgs.append({"role": "user", "content": L.P("ask_memory") % (memory_text or L.P("ask_mem0"), user)})
    user = L.P("ask_prev") % question                  # в историю только вопрос: сводка устареет
    if not powers:
        return user, _line(_chat(msgs, 0.7, 70), 200), []
    reply, acts = _ask_call(msgs, 0.7)
    # Прогон 04.10.2026: «дай патронов» один раз из девяти ушло без действия («Взять
    # некуда, но если попросишь, дам»). Повелительное слово и пустые действия: ещё раз
    # построже.
    if not acts and IMPERATIVE.search(question):
        reply2, acts2 = _ask_call(msgs, 0.3)
        if acts2:
            reply, acts = reply2, acts2
    for dash in (" — ", " – ", "—", "–"):
        reply = reply.replace(dash, ", ")
    return user, _line(reply, 200), acts


# ---------------------------------------------------------------- память: знакомство, дневник (memory.py)

# Роль памяти и образцы (mem_sys, mem_shots) теперь в lang.py.


QUESTION = re.compile(r"\?\s*$|^\s*(кто|что|где|куда|откуда|какой|какая|какое|какие|сколько|как|когда|почему|"
                      r"зачем|чей|чья|есть ли|ты\s+тут|who|what|where|which|how|when|why|whose|is|are|do|does|"
                      r"am|are\s+you\s+there)\b", re.I)


def worth_remembering(question):
    """Разбирать на факты только слова Скифа о себе. Журнал 05.10.2026 06:53: из «дай 3
    аптечки» легло «Просит дать 3 аптечки», а из ответа чутья «Держи дистанцию с
    долговцами» факт «Держит дистанцию с долговцами». Просьбы и вопросы мимо модели;
    «забудь» проходит всегда."""
    q = question.strip().lower()
    if re.search(r"забуд|забыть|не\s+помни|forget|don'?t\s+remember", q):
        return True
    # «хочу» и «want» тут не просьба: «я хочу найти брата» стоит запомнить
    return not (ACTION_VERB.search(question) or QUESTION.search(question))


ACTION_VERB = re.compile(r"\b(" + _DO_RU + r"|" + _DO_EN + r")\b", re.I)


def llm_extract(question, answer, facts):
    """-> (добавить, забыть, сырой ответ). Скупо: температура 0.2, образцы с «НЕТ»."""
    msgs = [{"role": "system", "content": L.P("mem_sys")}]
    for known, q, a, out in L.P("mem_shots"):
        msgs += [{"role": "user", "content": L.P("mem_user") % (known, q, a)},
                 {"role": "assistant", "content": out}]
    known = "\n".join("- " + f for f in facts[-20:]) or L.P("mem_empty")
    msgs.append({"role": "user", "content": L.P("mem_user") % (known, question, answer)})
    raw = _chat(msgs, 0.2, 80)
    adds, drops = [], []
    for line in raw.splitlines():
        line = line.strip().strip("«»\"' ")
        if line.startswith("+"):
            adds.append(line[1:].strip())
        elif line.startswith("-"):
            drops.append(line[1:].strip())
    return adds, drops, raw


def llm_diary(summary):
    msgs = [{"role": "system", "content": L.P("diary_sys")}]
    for shot_q, shot_a in L.P("diary_shots"):
        msgs += [{"role": "user", "content": shot_q}, {"role": "assistant", "content": shot_a}]
    msgs.append({"role": "user", "content": summary})
    # 0.3: при 0.5 9B переносила в строку события из образцов («Скиф попросил ночь», 05.10.2026)
    return _line(_chat(msgs, 0.3, 80), 240)


def llm_compact(lines):
    msgs = [{"role": "system", "content": L.P("compact_sys")},
            {"role": "user", "content": "\n".join("- " + l for l in lines)}]
    return _line(_chat(msgs, 0.3, 90), 300)


def session_summary(sess):
    parts = [L.tr("s_game", round((time.time() - sess["t0"]) / 60.0))]
    if sess["aims"]:
        parts.append(L.tr("s_aims", ", ".join(L.tr("s_aim", *kv) for kv in sess["aims"].most_common(4))))
    if sess["beasts"]:
        parts.append(L.tr("s_beasts", ", ".join(sorted(L.name(x) for x in sess["beasts"]))))
    parts += [L.tr("s_event", *kv) for kv in sess["events"].items()]
    if sess["deaths"]:
        parts.append(L.tr("s_deaths", ", ".join(L.name(x) for x in sess["deaths"])))
    if sess["near"]:
        parts.append(L.tr("s_near", ", ".join(L.name(x) for x in sess["near"])))
    if sess["weapons"]:
        parts.append(L.tr("s_weapons", ", ".join(sess["weapons"])))
    # ответов чутья в сводке нет: летописец пересказывал их выдумки как события игры
    # (дневник 05.10.2026). Из разговоров только сделанное руками по просьбе Скифа, прочее
    # числом: с вопросами и неисполненными просьбами 9B писала «рюкзак проверен»,
    # «плазменную пушку взял» (прогон 05.10.2026 13:10)
    did = [(q, done) for q, done in sess["talk"] if done]
    if did:
        parts.append(L.tr("s_talk", "; ".join(L.tr("s_done", q, ", ".join(done)) for q, done in did[-5:])))
    if len(sess["talk"]) > len(did):
        parts.append(L.tr("s_chat", len(sess["talk"]) - len(did)))
    return " ".join(parts)


def finish_session(memo, sess):
    """При закрытии игры, пока модель ещё поднята: строка дневника и сжатие старых."""
    worth = sess["talk"] or sess["deaths"] or sess["near"] or sess["events"] or sess["aims"] \
        or time.time() - sess["t0"] >= 300
    if not worth:
        log("[память] игра короткая и пустая, дневник не пишу")
        return
    summ = session_summary(sess)
    try:
        line = llm_diary(summ)
        memo.add_diary(line)
        log("[память] дневник: %s | сводка: %s" % (line, summ[:400]))
        old = memo.diary_overflow()
        if old:
            merged = llm_compact(old)
            memo.replace_old_diary(old, merged)
            log("[память] сжал %d строк дневника: %s" % (len(old), merged))
    except Exception as e:
        log("[память] дневник не записан: %s" % e)


# ---------------------------------------------------------------- модель

def llm_say(situation, recent):
    """Реплика голоса Зоны по обстановке; recent: последние фразы, чтобы не повторяться."""
    sys_prompt = ("Ты голос Зоны в игре S.T.A.L.K.E.R. 2: насмешливый, мрачный, знающий. Ответь ОДНОЙ короткой "
                  "фразой по-русски, до 15 слов, обращаясь к Скифу. Говори о том, что происходит вокруг сейчас, "
                  "называй тех, кто рядом, так, как они названы. Без кавычек, без тире, не повторяй прошлые фразы.")
    user = situation
    if recent:
        user += " Твои прошлые фразы (не повторяй): " + " / ".join(recent[-4:])
    body = {"model": "qwen", "temperature": 0.95, "max_tokens": 80,
            "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]}
    req = urllib.request.Request("http://127.0.0.1:8081/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = (data["choices"][0]["message"].get("content") or "").strip().replace("\n", " ").strip("«»\"' ")
    for dash in (" — ", " – ", "—", "–"):
        text = text.replace(dash, ", ")
    return text[:200]


def llm_line(seq, state):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    body = {"model": "qwen", "temperature": 0.9, "max_tokens": 80,
            "messages": [{"role": "system", "content": "Ты голос Зоны в игре S.T.A.L.K.E.R. 2. Ответь ОДНОЙ "
                                                      "короткой фразой по-русски, до 15 слов, по-сталкерски, без кавычек "
                                                      "и без тире."},
                         {"role": "user", "content": "Скиф бродит по Зоне. Обстановка от мода: %s. Скажи ему что-нибудь." % state}]}
    req = urllib.request.Request("http://127.0.0.1:8081/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = (data["choices"][0]["message"].get("content") or "").strip().replace("\n", " ")
    # длинное и среднее тире автор не терпит (маркер нейротекста): запятой
    for dash in (" — ", " – ", "—", "–"):
        text = text.replace(dash, ", ")
    return text[:200]


# ---------------------------------------------------------------- постоянная работа (0.0.3)

GAME_EXE = "Stalker2-Win64-Shipping.exe"


def game_pid():
    """Номер процесса игры или None. Снимок процессов Windows (Toolhelp32), без tasklist:
    05.10.2026 nestor.exe без консоли на каждый вызов tasklist открывал консольное окно,
    и оно моргало раз в 3 с в ожидании игры и 4 раза в секунду в игре. Прежняя ловушка
    tasklist (табличный вывод резал имя до 25 знаков, 04.10.2026) ушла вместе с ним."""
    import ctypes
    from ctypes import wintypes

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ENTRY)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)                 # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    try:
        e = ENTRY()
        e.dwSize = ctypes.sizeof(ENTRY)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == GAME_EXE.lower():
                return int(e.th32ProcessID)
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return None


def game_running():
    return game_pid() is not None


def check_install():
    """Пак NESTOR в игре = свежая сборка кита? 04.10.2026 соседняя сессия, возвращая
    отложенные моды из modpark, положила поверх 0.0.3 старый 0.0.1: мод в игре был без
    окна и без обстановки, а искали это по журналу полчаса. Сверка хешей при старте.
    Только на машине разработчика: пути к киту и игре из paths.py проекта модов; у
    игрока (nestor.exe) и в открытом коде его нет, и сверки нет."""
    import hashlib
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        from paths import KIT, MODS_DIR
    except Exception:
        return
    name = "NESTORStalker2-Windows-OverrideContent.ucas"
    staged = os.path.join(KIT, "Stalker2", "SavedMods", "Staged", "NESTOR", "Windows", "OverrideContent", "Windows",
                          "Stalker2", "Mods", "NESTOR", "Content", "Paks", "Windows", name)
    game = os.path.join(MODS_DIR, name)
    if not (os.path.exists(staged) and os.path.exists(game)):
        log("сверка пака: нет файла (%s)" % ("сборка" if not os.path.exists(staged) else "игра"))
        return
    h = [hashlib.sha256(open(p, "rb").read()).hexdigest()[:16] for p in (game, staged)]
    if h[0] == h[1]:
        log("сверка пака: в игре свежая сборка NESTOR (%s)" % h[0])
    else:
        log("!!! В ИГРЕ НЕ ТА СБОРКА NESTOR: в игре %s, в сборке кита %s. Закрыть игру и положить пак заново." % tuple(h))


def start_engine(args, eng):
    """Поднять модель игрока (nestor.exe); не поднялась: запасная из args.fallbacks
    (4B, если скачана). -> (процесс или None, имя поднятой модели, причина сбоя первой
    или "")."""
    logdir = os.path.dirname(LOG)
    first_why = ""
    for name, path in [(args.model, args.model_path)] + list(getattr(args, "fallbacks", None) or []):
        if not path or not os.path.exists(path):
            continue
        try:
            proc = eng.start(args.server, path, logdir, getattr(args, "dll_dirs", ()) or ())
            return proc, name, first_why
        except Exception as e:
            why = eng.problem(logdir)
            log("модель %s не поднялась: %s (%s)" % (name, e, why))
            first_why = first_why or why
    return None, None, first_why or "other"


def send_in(text, reply_seq, cmd=""):
    """Реплика на экран игры через NS_In по образцу NS_Out."""
    data = build_in(open(OUT, "rb").read(), text, reply_seq, cmd)
    open(IN + ".tmp", "wb").write(data)
    os.replace(IN + ".tmp", IN)


def notice_until_exit(text, t_game):
    """Модели нет: дождаться мира (мод снова пишет NS_Out), один раз показать text на
    экране и ждать выхода из игры. Без этого у игрока окно NESTOR просто исчезало, а
    чутьё молчало без единого слова (до 05.10.2026)."""
    sent = False
    while game_running():
        if not sent and os.path.exists(OUT) and os.path.getmtime(OUT) > t_game:
            time.sleep(5)                     # мир дорисовался, экран загрузки ушёл
            try:
                send_in(text, 1)
                log("в игре показано: " + text)
            except Exception as e:
                log("сообщение в игру не ушло: %s" % e)
            sent = True
        time.sleep(2)


def report(args, **kw):
    """Итог игры для лаунчера nestor.exe (args.report пишет его в nestor.json)."""
    fn = getattr(args, "report", None)
    if fn:
        try:
            fn(dict(kw, when=time.strftime("%d.%m.%Y %H:%M")))
        except Exception as e:
            log("итог игры не записан: %s" % e)


SLOW_S = 6.0
BACKUP_WAIT = 120.0
ALLY_S, ALLY_EVERY, ALLY_FAR = 300.0, 20.0, 15   # 0.2: союзники 5 мин, звать раз в 20 с дальше 15 м                                  # медиана первых трёх реплик чутья дольше: модели тесно
STATUS = ("idle", "")                         # для лаунчера: wait, start, ready, fail, done
STOP = threading.Event()                      # лаунчер сменил настройки: перестать ждать игру


def serve(args):
    """Пока идёт игра: читать обстановку, говорить по событиям, выключиться с игрой.

    Повод сказать: кто-то новый взял Скифа на прицел (не чаще min_gap), рядом
    появился новый вид людей или мутантов (не чаще 2*min_gap), тишина дольше quiet.
    Мод пишет NS_Out только в игровом мире (0.0.3), в меню повода нет.
    """
    global _BOX, STATUS
    log("постоянная работа: модель %s, тишина %.0f с, промежуток %.0f с" % (args.model, args.quiet, args.min_gap))
    t0 = time.time()
    STATUS = ("wait", "")
    # --wait 0: ждать без срока. 05.10.2026 мост с --wait 14400 вышел в 02:09, а автор
    # зашёл в игру утром и остался без чутья.
    while not game_running():
        if STOP.is_set():
            log("лаунчер сменил настройки, ожидание прервано")
            STATUS = ("idle", "")
            return False
        if args.wait > 0 and time.time() - t0 > args.wait:
            log("игра не запустилась за %.0f с, выхожу" % args.wait)
            STATUS = ("idle", "")
            return False
        STOP.wait(3)                          # лаунчер сменил настройки: выйти сразу, не через 3 с
    t_game = time.time()
    check_install()
    log("игра запущена, поднимаю модель")
    STATUS = ("start", args.model)
    used, why = args.model, ""
    if getattr(args, "server", None):
        # у игрока (nestor.exe): своя сборка llama.cpp и модель из папки NESTOR (engine.py)
        import engine as eng
        proc, used, why = start_engine(args, eng)
        stop_engine = eng.stop
        if proc is None:
            key = {"memory": "n_memory_4b" if args.model == "4b" else "n_memory",
                   "driver": "n_driver"}.get(why, "n_other")
            text = L.tr(key, args.model.upper()) if key == "n_memory" else L.tr(key)
            STATUS = ("fail", why)
            report(args, model=args.model, used=None, problem=why)
            notice_until_exit(text, t_game)
            log("игра закрыта (чутьё не поднималось)")
            STATUS = ("done", why)
            return True
    else:
        import measure
        proc = measure.start_server(args.model, 99, None, os.path.dirname(LOG))
        stop_engine = measure.stop_server
    global ENGINE_PROC
    ENGINE_PROC = (proc, stop_engine)                 # nestor.exe гасит движок при выходе из окна
    log("модель готова" + ("" if used == args.model else ": запасная %s вместо %s" % (used, args.model)))
    STATUS = ("ready", used)
    # сообщения игроку на экран, когда мир загрузится: запасная модель, медленная модель
    pending = [] if used == args.model else [L.tr("n_fallback", args.model.upper())]
    times, slow_noted = [], False             # время реплик чутья: тесно ли модели рядом с игрой
    box = None
    if args.ask_key:
        import askbox
        if _BOX is None:
            _BOX = askbox.AskBox(vk=int(args.ask_key, 16),
                                 store=os.path.join(os.path.dirname(LOG), "chat.json")).start()
        else:
            _BOX.start_hotkey()
        log("окно разговора на клавише 0x%s: %s" % (args.ask_key, _BOX.error or "готово"))
        box = None if _BOX.error else _BOX
    memo = None
    if args.memory_dir:
        import memory as nmemory
        memo = nmemory.Memory(args.memory_dir, lang=L.LANG)
        memo.start_session(game_pid())
        log("память: %s (игра %d, разговоров %d, фактов %d, мест дежавю %d)" % (
            memo.dir, memo.state["sessions"], memo.state["talks"], len(memo.facts()), len(memo.state["places"])))
        if box is not None:
            box.prefill(memo.state.get("history", []))
    sess = new_session()
    # (вопрос, ответ, действия); с 0.2.1 начинается с последних обменов прошлой игры
    talk = [(L.P("ask_prev_game") % h[1], h[2], h[3] if len(h) > 3 else [])
            for h in (memo.state.get("talk", []) if memo is not None else [])[-PREV_GAME_N:]]
    said_log = []                             # (время, фраза) своих слов чутья без вопроса
    try:
        if os.path.exists(IN):
            os.remove(IN)
        reply_seq, recent = 0, []
        recent_kind = {}                      # прошлые фразы чутья по роду повода (aim, beast, body...)
        last_give = None                      # (имя, сколько) последней выдачи: для поправки «не те»
        last_mtime, last_said = 0.0, 0.0
        known_aim, known_kinds = 0, set()
        mem = {"quiet": time.time()}          # шестое чувство: что и когда уже сказано
        st = None
        last_raw = 0.0
        later, backup, last_reply = [], None, ""      # 0.2: отложенные команды, подмога, последняя реплика
        allies, last_follow, seen_alive = {}, 0.0, set()   # 0.2: союзники (номер -> до когда), кого видели живым
        while game_running():
            if args.persona == "sixth":
                if os.path.exists(OUT):
                    m = os.path.getmtime(OUT)
                    if m != last_mtime:
                        try:
                            _b, _cls, props = read_out()
                        except Exception:
                            time.sleep(0.2)
                            continue
                        last_mtime = m
                        st = parse_state(props.get("State", ""))
                        seen_alive.update(a["uid"] for a in st["agents"] if a.get("uid"))
                        st["_fresh"] = seen_alive
                        for e in track_life(st, mem, memo, sess, time.time()):
                            log("[жизнь] " + e)
                        # сырые значения о Скифе раз в 30 с: по ним уточнять пороги body_words
                        if st["me"] and time.time() - last_raw >= 30:
                            last_raw = time.time()
                            log("[скиф] " + " ".join("%s=%s" % (k, st["me"][k]) for k in sorted(st["me"]))
                                + " yaw=%s агентов=%d с местом=%d" % (st["yaw"], len(st["agents"]),
                                                                      sum(1 for a in st["agents"] if a["loc"])))
                now = time.time()
                if pending and st is not None and now - last_mtime < 5 and now - mem.get("any", 0) > 9:
                    note = pending.pop(0)
                    reply_seq += 1
                    try:
                        send_in(note, reply_seq)
                        mem["any"] = time.time()
                        to_history(box, memo, "чутьё", note)
                        log("в игре показано: " + note)
                    except Exception as e:
                        log("сообщение в игру не ушло: %s" % e)
                # 0.2: отложенные шаги рук (снять замедление, вернуть скорость), подмога (своим и
                # к Скифу, когда родившиеся одиночки появятся в обстановке с номерами) и союзники:
                # подмогу и поднятых зомби звать к Скифу, если отошли дальше 15 м (проба 05.10.2026:
                # дойдя по XMoveToPlayer, они уходят бродить)
                due = [c for t, c in later if t <= now]
                fresh = []
                if backup and st is not None:
                    fresh = [a["uid"] for a in st["agents"] if a.get("uid") and a["uid"] not in backup["known"]
                             and a.get("loc") and is_human(a["who"])
                             and any(math.hypot(a["loc"][0] - p[0], a["loc"][1] - p[1]) < 1500 for p in backup["pts"])]
                    due += ["XSetRelation %d 0 1000;XMoveToPlayer %d 3" % (u, u) for u in fresh]
                    if now > backup["until"]:
                        backup = None
                if allies and st is not None and now - last_follow >= ALLY_EVERY:
                    last_follow = now
                    allies = {u: t for u, t in allies.items() if t > now}
                    due += ["XMoveToPlayer %d 3" % a["uid"] for a in st["agents"]
                            if a.get("uid") in allies and a["m"] > ALLY_FAR]
                if due and st is not None and not os.path.exists(IN):
                    later = [(t, c) for t, c in later if t > now]
                    if backup:
                        backup["known"].update(fresh)
                    allies.update({u: now + ALLY_S for u in fresh})
                    reply_seq += 1
                    try:
                        send_in(last_reply, reply_seq, ";".join(due))
                        log("[действие] отложенное: %s" % ";".join(due))
                    except Exception as e:
                        log("отложенное не ушло: %s" % e)
                # вопрос из окна F4: отвечать вне очереди, по последней обстановке
                q = None
                if box is not None:
                    try:
                        q = box.questions.get_nowait()
                    except Exception:
                        q = None
                if q and not os.path.exists(OUT):
                    log("вопрос «%s» без ответа: мод ещё не писал обстановку (NS_Out нет)" % q)
                    q = None
                if q:
                    t1 = time.time()
                    kind = pet_claim(q, st)
                    if kind:
                        mem.setdefault("pets", set()).add(kind)
                        if memo is not None:
                            memo.add_pet(kind)
                            memo.apply_facts([L.P("pet_fact") % L.name(kind)], [])
                        log("[память] свой зверь Скифа: %s" % kind)
                    pets = pets_of(mem, memo)
                    try:
                        user, text, acts = llm_answer(q, ask_summary(st, memo, pets), talk,
                                                      memo.prompt_text() if memo else "",
                                                      powers=bool(st) and st.get("v", 0) >= 5, said=said_log)
                    except Exception as e:
                        log("модель не ответила на вопрос: %s" % e)
                        continue
                    # руки чутья (actions.py): действия модели -> X-команды для мода 0.0.5
                    asked = list(acts)
                    if QUESTION.search(q) and not IMPERATIVE.search(q) and not ASK_DO.search(q):
                        acts = []                     # вопрос без просьбы: ничего не делать
                    acts, dropped = _actions.guard(acts, q)
                    if st and st.get("v", 0) >= 5:
                        acts = _actions.merge_rules(acts, q)
                        if not acts:
                            fix = _actions.correction(q, last_give)
                            if fix:
                                acts = [fix]
                    follow = []
                    cmd, done, failed = _actions.build_all(acts, st, question=q, is_human=is_human, follow=follow)
                    later += [(time.time() + d, c) for d, c in follow]
                    for u in re.findall(r"XResurrectNPCAsZombie (\d+)", cmd):
                        allies[int(u)] = time.time() + ALLY_S
                        later.append((time.time() + 4.0, "XSetRelation %s 0 1000" % u))
                    if st and _actions.HELP_SID in cmd:
                        # подмога: новые люди у точек рождения станут своими и пойдут к Скифу.
                        # Проба 05.10.2026 15:08: за 40 с по фракции «одиночка» не нашлось никого,
                        # теперь по точке (ближе 15 м) и 2 минуты
                        pts = [tuple(map(float, m)) for m in re.findall(
                            _actions.HELP_SID + r" \d+ 0 (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)", cmd)]
                        backup = {"until": time.time() + BACKUP_WAIT, "pts": pts,
                                  "known": {a["uid"] for a in st["agents"] if a.get("uid")}}
                    if acts and not done:
                        text = L.tr("fail", failed[0])
                    elif done:
                        # реплика по списку сделанного: журнал 05.10.2026 «Кабан спит спереди»,
                        # «Бессмертие выкл.» при выдаче аптечек, в прогоне «Кабан появился слева»
                        try:
                            text = llm_confirm(q, done, failed) or text
                        except Exception as e:
                            log("реплика о сделанном не удалась: %s" % e)
                    elif dropped and (IMPERATIVE.search(q) or ASK_DO.search(q)):
                        # модель «сделала», а защита отбросила действие: журнал 05.10.2026 10:38
                        # «успокой собак вокруг» -> «Сделал. Они теперь твои друзья», а мир только
                        # для людей, и ничего не сделано
                        try:
                            text = llm_confirm(q, [], [L.tr("cant")]) or L.tr("fail", L.tr("cant"))
                        except Exception as e:
                            log("честная реплика не удалась: %s" % e)
                            text = L.tr("fail", L.tr("cant"))
                    for a in acts:
                        if a.get("do") == "дать":
                            it = _actions.find_item(a.get("what", ""))
                            if it:
                                last_give = (it[1], max(1, int(a.get("n", 1) or 1)))
                    if dropped:
                        log("[действие] отброшено без слова в просьбе: %s" % json.dumps(dropped, ensure_ascii=False))
                    reply_seq += 1
                    data = build_in(open(OUT, "rb").read(), text, reply_seq, cmd)
                    open(IN + ".tmp", "wb").write(data)
                    os.replace(IN + ".tmp", IN)
                    talk = (talk + [(user, text, acts)])[-HISTORY_N:]
                    if memo is not None:
                        memo.state.setdefault("talk", []).append([time.strftime("%d.%m %H:%M"), q, text, acts])
                        del memo.state["talk"][:-HISTORY_N]
                        memo.save()
                    last_reply = text
                    recent = (recent + [text])[-6:]
                    mem["any"] = time.time()      # 8 с показа ответ не перебивать
                    to_history(box, memo, "ты", q)
                    to_history(box, memo, "чутьё", text + (" [%s]" % ", ".join(done) if done else ""))
                    log("[вопрос] «%s» | %s | модель %.1f с | %s" % (q, ask_summary(st, memo, pets)[:260],
                                                                  time.time() - t1, text))
                    if acts:
                        log("[действие] %s | сделано: %s | нет: %s | команды: %s" % (
                            json.dumps(acts, ensure_ascii=False), "; ".join(done) or "-",
                            "; ".join(failed) or "-", cmd or "-"))
                    # ответ уже на экране: теперь разобрать, что запомнить о Скифе
                    if memo is not None:
                        memo.count_talk()
                        sess["talk"].append((q, list(done)))
                        try:
                            adds, drops, raw = llm_extract(q, text, memo.facts()) if worth_remembering(q) \
                                else ([], [], "")
                            added, dropped = memo.apply_facts(adds, drops)
                            if added or dropped:
                                log("[память] запомнил: %s | забыл: %s" % ("; ".join(added) or "-",
                                                                         "; ".join(dropped) or "-"))
                        except Exception as e:
                            log("[память] разбор не удался: %s" % e)
                    continue
                if st is not None and now - last_mtime < 5:
                    r = sixth_reason(st, mem, now, args, memo)
                    if r:
                        reason, key = r
                        t1 = time.time()
                        try:
                            kind = key.split(":")[0]
                            text = llm_sixth(reason, recent_kind.get(kind, []), recent[-4:])
                        except Exception as e:
                            log("модель не ответила: %s" % e)
                            mem["any"] = now
                            continue
                        times.append(time.time() - t1)
                        if len(times) == 3 and not slow_noted and sorted(times)[1] > SLOW_S:
                            # на Windows видеокарта при нехватке памяти не падает, а уводит
                            # модель в общую память: та «работает», но в разы медленнее
                            slow_noted = True
                            pending.append(L.tr("n_slow"))
                            log("модель медленная: реплики %s с" % ", ".join("%.1f" % x for x in times))
                        reply_seq += 1
                        data = build_in(open(OUT, "rb").read(), text, reply_seq)
                        tmp = IN + ".tmp"
                        open(tmp, "wb").write(data)
                        os.replace(tmp, IN)
                        recent = (recent + [text])[-6:]
                        mem[key] = mem["any"] = time.time()
                        note_reason(sess, key)
                        recent_kind[kind] = (recent_kind.get(kind, []) + [text])[-3:]
                        said_log = (said_log + [(time.time(), text)])[-6:]
                        to_history(box, memo, "чутьё", text)
                        log("[%s] %s | %s | модель %.1f с | %s" % (key, reason, describe(st, pets=pets_of(mem, memo))[:120],
                                                                    time.time() - t1, text))
                time.sleep(0.25)
                continue
            if os.path.exists(OUT):
                m = os.path.getmtime(OUT)
                if m != last_mtime:
                    try:
                        _b, _cls, props = read_out()
                    except Exception:
                        time.sleep(0.2)
                        continue
                    last_mtime = m
                    st = parse_state(props.get("State", ""))
            now = time.time()
            if st is not None and now - last_mtime < 5:          # мод в мире и пишет
                aim = sum(a["aim"] for a in st["agents"])
                kinds = {a["who"] for a in st["agents"]}
                known_aim = min(known_aim, aim)                  # отвели прицел: новый прицел снова повод
                reason = None
                if aim > known_aim and now - last_said >= args.min_gap:
                    reason = "на прицеле"
                elif kinds - known_kinds and now - last_said >= 2 * args.min_gap:
                    reason = "новые рядом"
                elif now - last_said >= args.quiet:
                    reason = "тишина"
                if reason:
                    situation = describe(st)
                    t1 = time.time()
                    try:
                        text = llm_say(situation, recent)
                    except Exception as e:
                        log("модель не ответила: %s" % e)
                        last_said = now
                        continue
                    reply_seq += 1
                    data = build_in(open(OUT, "rb").read(), text, reply_seq)
                    tmp = IN + ".tmp"
                    open(tmp, "wb").write(data)
                    os.replace(tmp, IN)
                    recent = (recent + [text])[-6:]
                    last_said, known_aim, known_kinds = time.time(), aim, kinds
                    log("[%s] %s | модель %.1f с | %s" % (reason, situation[:170], time.time() - t1, text))
            time.sleep(0.25)
        log("игра закрыта, выключаюсь")
        if memo is not None:
            finish_session(memo, sess)
    finally:
        if _BOX is not None:
            _BOX.stop_hotkey()
        stop_engine(proc)
        ENGINE_PROC = None
        log("llama-server остановлен")
        median = sorted(times)[len(times) // 2] if times else None
        report(args, model=args.model, used=used, problem="fallback" if used != args.model else
               "slow" if slow_noted else "", why=why, median=round(median, 1) if median else None)
        STATUS = ("done", "")
    return True


_BOX = None                                   # окно F4 одно на всю работу моста (--loop)
ENGINE_PROC = None                            # (процесс движка, как его гасить), пока идёт игра


# ---------------------------------------------------------------- круг

def windows_lang():
    """Язык интерфейса Windows: русский -> ru, любой другой -> en (выбор игрока при
    первом запуске nestor.exe важнее)."""
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return "ru" if lid == 0x19 else "en"
    except Exception:
        return "ru"


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="9b", help="9b, 4b или none (без модели)")
    ap.add_argument("--every", type=float, default=8.0, help="с между ответами")
    ap.add_argument("--rounds", type=int, default=6, help="сколько ответов отправить")
    ap.add_argument("--wait", type=float, default=600.0, help="сколько ждать первый NS_Out (или игру в --serve), с; 0: без срока")
    ap.add_argument("--serve", action="store_true", help="постоянная работа, пока идёт игра (0.0.3)")
    ap.add_argument("--loop", action="store_true", help="--serve: после игры ждать следующий запуск (0.0.4)")
    ap.add_argument("--memory-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory"),
                    help="--serve: каталог памяти чутья (папка на профиль Steam); пусто: без памяти")
    ap.add_argument("--quiet", type=float, default=240.0, help="--serve: в тишине не чаще раза в столько с")
    ap.add_argument("--min-gap", type=float, default=8.0, help="--serve: не чаще раза в столько с")
    ap.add_argument("--persona", default="sixth", choices=("sixth", "zone"),
                    help="--serve: sixth шестое чувство (0.0.4), zone голос Зоны (0.0.3)")
    ap.add_argument("--ask-key", default="73", help="--serve: код VK клавиши окна разговора (73 = F4), пусто: без окна")
    ap.add_argument("--lang", default=None, choices=("ru", "en"), help="язык чутья; по умолчанию язык Windows")
    args = ap.parse_args()
    L.set_lang(args.lang or windows_lang())
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if args.serve:
        # --loop: после закрытия игры ждать следующий запуск (до --wait с), пока
        # лаунчера через Steam нет; без него мост выключается вместе с игрой
        while serve(args) and args.loop:
            log("жду следующий запуск игры (%s)" % ("до %.0f с" % args.wait if args.wait > 0 else "без срока"))
        return

    proc = None
    if args.model != "none":
        import measure
        log("поднимаю llama-server, модель %s" % args.model)
        proc = measure.start_server(args.model, 99, None, os.path.dirname(LOG))
        log("модель готова")
    try:
        if os.path.exists(IN):
            os.remove(IN)
            log("старый NS_In убран")
        log("жду NS_Out от мода: %s" % OUT)
        t0 = time.time()
        while not os.path.exists(OUT) and time.time() - t0 < args.wait:
            time.sleep(0.5)
        if not os.path.exists(OUT):
            log("NS_Out не появился за %.0f с: мод молчит" % args.wait)
            return
        sent = {}                  # номер ответа -> время отправки; None, когда эхо пришло
        seq = 0
        last_mtime = 0
        last_state = ""
        next_send = time.time()
        deadline = None            # после последнего ответа ждём эхо ещё 10 с
        while deadline is None or time.time() < deadline:
            m = os.path.getmtime(OUT)
            if m != last_mtime:
                try:
                    _b, cls, props = read_out()
                except Exception as e:
                    log("NS_Out не прочитан (%s), повтор" % e)
                    time.sleep(0.2)
                    continue
                last_mtime = m
                last_state = props.get("State", "")
                got = last_state.split("|got=", 1)[1].split(":", 1) if "|got=" in last_state else None
                if got and got[0].lstrip("-").isdigit() and sent.get(int(got[0])) is not None:
                    n = int(got[0])
                    log("КРУГ %d замкнут за %.1f с: мод вернул эхом «%s»" % (n, time.time() - sent[n], got[1]))
                    sent[n] = None
                log("мод: класс %s, Seq=%s, State=%s" % (cls.rsplit("/", 1)[-1], props.get("Seq"), last_state[:140]))
            if time.time() >= next_send and seq < args.rounds:
                seq += 1
                if args.model == "none":
                    reply = "Проверка связи %d: Скиф, слышишь Зону?" % seq
                else:
                    t1 = time.time()
                    reply = llm_line(seq, last_state)
                    log("модель ответила за %.1f с" % (time.time() - t1))
                data = build_in(open(OUT, "rb").read(), reply, seq)
                tmp = IN + ".tmp"
                open(tmp, "wb").write(data)
                os.replace(tmp, IN)            # игра не увидит недописанный файл
                sent[seq] = time.time()
                log("ОТВЕТ %d в NS_In (%d байт): %s" % (seq, len(data), reply))
                next_send = time.time() + args.every
                if seq == args.rounds:
                    deadline = time.time() + 10
            if deadline and all(v is None for v in sent.values()):
                break
            time.sleep(0.25)
        log("итог: отправлено %d, замкнуто %d" % (seq, sum(1 for v in sent.values() if v is None)))
    finally:
        if proc:
            import measure
            measure.stop_server(proc)
            log("llama-server остановлен")


if __name__ == "__main__":
    main()
