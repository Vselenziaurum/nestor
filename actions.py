# -*- coding: utf-8 -*-
"""NESTOR: руки чутья (04.10.2026, НЕСТОР.md).

Автор: «ии должен менять погоду, спавнить ресурсы, всё, на что хватит
воображения игрока. просто не все узнают про это». Модель на просьбу Скифа в F4
отвечает репликой и списком действий по схеме (schema()); здесь действие
превращается в консольные X-команды игры (Х-1..Х-3, Х-20), мод 0.0.5 исполняет
их через ExecuteConsoleCommand (как SpareMag). Модель не пишет команды сама:
только вид действия, что и сколько; имена, пределы и форма команды здесь.

Внутренние ключи действий русские (ACTIONS); английский выпуск (lang.py,
ACTIONS_EN) показывает модели английские имена, мост переводит назад. Просьбы
разбираются на обоих языках; итог сделанного пишется на языке игрока.

Проверено в игре 05.10.2026 (НЕСТОР.md): предметы, купоны, существа, призраки
людей, погода, время (только свет), выброс, перенос вперёд (сантиметры), «мир»
(радиус в сантиметрах). Убраны: скорость времени, промотка, бессмертие.
"""
import json
import math
import os
import re
import time

import lang as L

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPHS = os.path.join(HERE, "..", "zonekit", "graphs")

# ---------------------------------------------------------------- словари (оба языка)

# порядок важен: длинное раньше короткого («слепой пёс» раньше «пёс»)
CREATURES = [("слеп", "Blinddog"), ("псевдогигант", "Pseudogiant"), ("гигант", "Pseudogiant"),
             ("псевдособак", "PseudoDog"), ("псевдопс", "PseudoDog"), ("псевдопёс", "PseudoDog"),
             ("кабан", "Boar"), ("плот", "Flesh"), ("снорк", "Snork"), ("кровосос", "Bloodsucker"),
             ("химер", "Chimera"), ("контролёр", "Controller"), ("контролер", "Controller"),
             ("бюрер", "Burer"), ("олен", "Deer"), ("баюн", "Bayun"), ("кот", "Bayun"),
             ("собак", "PseudoDog"), ("пёс", "PseudoDog"), ("пес", "PseudoDog"),
             ("blind dog", "Blinddog"), ("blinddog", "Blinddog"), ("pseudogiant", "Pseudogiant"),
             ("pseudo giant", "Pseudogiant"), ("giant", "Pseudogiant"), ("pseudodog", "PseudoDog"),
             ("pseudo dog", "PseudoDog"), ("pseudo-dog", "PseudoDog"), ("boar", "Boar"), ("flesh", "Flesh"),
             ("snork", "Snork"), ("bloodsucker", "Bloodsucker"), ("chimera", "Chimera"),
             ("controller", "Controller"), ("burer", "Burer"), ("deer", "Deer"), ("bayun", "Bayun"),
             ("cat", "Bayun"), ("dog", "PseudoDog")]
FACTIONS = [("бандит", "Bandit"), ("долг", "Duty"), ("свобод", "Freedom"), ("монолит", "Monolith"),
            ("наёмник", "Mercenaries"), ("наемник", "Mercenaries"), ("военн", "Militaries"),
            ("вояк", "Militaries"), ("одиноч", "Neutral"), ("сталкер", "Neutral"), ("полдн", "Noon"),
            ("полдень", "Noon"), ("искр", "Spark"), ("варт", "Varta"), ("учён", "Scientists"),
            ("учен", "Scientists"), ("корпус", "Corpus"), ("копател", "Digger"),
            ("bandit", "Bandit"), ("duty", "Duty"), ("freedom", "Freedom"), ("monolith", "Monolith"),
            ("merc", "Mercenaries"), ("military", "Militaries"), ("soldier", "Militaries"), ("loner", "Neutral"),
            ("stalker", "Neutral"), ("noon", "Noon"), ("spark", "Spark"), ("ward", "Varta"), ("varta", "Varta"),
            ("scientist", "Scientists"), ("corpus", "Corpus"), ("digger", "Digger")]
ROLES = [("снайпер", "Sniper"), ("экзо", "Heavy"), ("тяжёл", "Heavy"), ("тяжел", "Heavy"),
         ("пулемёт", "Heavy"), ("штурм", "Stormtrooper"), ("разведч", "Recon"), ("ближн", "CloseCombat"),
         ("sniper", "Sniper"), ("exo", "Heavy"), ("heavy", "Heavy"), ("machine gun", "Heavy"),
         ("assault", "Stormtrooper"), ("stormtrooper", "Stormtrooper"), ("scout", "Recon"), ("recon", "Recon"),
         ("melee", "CloseCombat"), ("close combat", "CloseCombat")]
RANKS = [("новичок", 0), ("новичк", 0), ("опытн", 1), ("ветеран", 2), ("мастер", 3),
         ("novice", 0), ("rookie", 0), ("newbie", 0), ("experienced", 1), ("veteran", 2), ("master", 3)]
WEATHER = [("гроз", 6), ("ливень", 5), ("морос", 4), ("дождик", 4), ("лёгкий дождь", 4), ("легкий дождь", 4),
           ("небольшой дождь", 4), ("дожд", 5), ("шторм", 3), ("бур", 3), ("ветр", 3), ("туман", 2),
           ("облач", 1), ("пасмурн", 1), ("ясн", 0), ("солн", 0), ("чист", 0),
           ("thunder", 6), ("downpour", 5), ("heavy rain", 5), ("drizzle", 4), ("light rain", 4), ("rain", 5),
           ("storm", 3), ("wind", 3), ("fog", 2), ("mist", 2), ("cloud", 1), ("overcast", 1), ("clear", 0),
           ("sun", 0)]
WEATHER_NAMES = {0: ("ясно", "clear"), 1: ("облачно", "cloudy"), 2: ("туман", "fog"), 3: ("шторм", "storm"),
                 4: ("морось", "drizzle"), 5: ("дождь", "rain"), 6: ("гроза", "thunderstorm")}
TIMES = [("полночь", (0, 0)), ("полдень", (12, 0)), ("рассвет", (5, 30)), ("утр", (8, 0)), ("день", (12, 0)),
         ("днём", (12, 0)), ("вечер", (19, 0)), ("закат", (20, 0)), ("сумерк", (20, 30)), ("ноч", (1, 0)),
         ("midnight", (0, 0)), ("noon", (12, 0)), ("midday", (12, 0)), ("dawn", (5, 30)), ("sunrise", (5, 30)),
         ("morning", (8, 0)), ("day", (12, 0)), ("evening", (19, 0)), ("sunset", (20, 0)), ("dusk", (20, 30)),
         ("night", (1, 0))]

CREATURE_NAMES = {"Bloodsucker": ("кровосос", "bloodsucker"), "Boar": ("кабан", "boar"), "Flesh": ("плоть", "flesh"),
                  "Snork": ("снорк", "snork"), "Chimera": ("химера", "chimera"),
                  "Pseudogiant": ("псевдогигант", "pseudogiant"), "Controller": ("контролёр", "controller"),
                  "Burer": ("бюрер", "burer"), "PseudoDog": ("псевдособака", "pseudodog"),
                  "Blinddog": ("слепой пёс", "blind dog"), "Deer": ("олень", "deer"), "Bayun": ("баюн", "bayun")}
FACTION_NAMES = {"Bandit": ("бандит", "bandit"), "Duty": ("долговец", "Duty soldier"),
                 "Freedom": ("свободовец", "Freedom fighter"), "Monolith": ("монолитовец", "Monolith fighter"),
                 "Mercenaries": ("наёмник", "mercenary"), "Militaries": ("военный", "soldier"),
                 "Neutral": ("одиночка", "loner"), "Noon": ("боец Полдня", "Noon fighter"),
                 "Spark": ("боец Искры", "Spark fighter"), "Varta": ("боец Варты", "Ward soldier"),
                 "Scientists": ("учёный", "scientist"), "Corpus": ("боец Корпуса", "Corpus soldier"),
                 "Digger": ("копатель", "digger")}

LIMITS = {"дать": 50, "патроны": 300, "деньги": 1000000, "существо": 6, "призрак": 6, "вперёд": 100,
          "подмога": 3}

# «лучшие/военные аптечки» = армейские (автор 05.10.2026 07:19: «лучшие это военные, а
# ты дал обычные»); английский: Army Medkit, Scientific Medkit
SYNONYMS = [(r"\b(лучш|военн|армейск)\w*\s+аптеч\w*", "армейская аптечка"),
            (r"\bнаучн\w*\s+аптеч\w*", "научная аптечка"),
            (r"\bвоенн\w*", "армейский"),
            (r"\b(best|military|army)\s+(medkits?|first\s+aid\s+kits?)", "army medkit"),
            (r"\bscien\w*\s+(medkits?|first\s+aid\s+kits?)", "scientific medkit"),
            (r"\bfirst\s+aid\s+kits?", "medkit")]

_ITEMS = {}
_WEAPONS = None


def items(lang=None):
    lang = lang or L.LANG
    if lang not in _ITEMS:
        try:
            _ITEMS[lang] = json.load(open(os.path.join(HERE, "items_%s.json" % lang), encoding="utf-8"))
        except OSError:
            _ITEMS[lang] = {}
    return _ITEMS[lang]


def weapons():
    global _WEAPONS
    if _WEAPONS is None:
        _WEAPONS = {"weapons": {}, "ammo": {}}
        # в nestor.exe таблица лежит рядом с модулями, в дереве проекта у SpareMag
        for p in (os.path.join(HERE, "sparemag_weapons.json"), os.path.join(GRAPHS, "sparemag_weapons.json")):
            try:
                _WEAPONS = json.load(open(p, encoding="utf-8"))
                break
            except OSError:
                continue
    return _WEAPONS


def norm(s):
    return re.sub(r"[^\w ]+", " ", (s or "").lower().replace("ё", "е")).split()


def has_word(text, word):
    """Слово или основа с начала слова: «today» не «day», «forward» не «ward»."""
    return re.search(r"(?<!\w)" + re.escape(word.replace("ё", "е")), text) is not None


def _find_in(d, w):
    for sid, name in d.items():
        if sid.lower() == w.lower() or name.lower().replace("ё", "е") == w.lower().replace("ё", "е"):
            return sid
    words = [x[:4] if len(x) > 4 else x for x in norm(w) if len(x) > 1]
    words = [x for x in words if x not in ("мне", "дай", "пару", "штук", "немн", "give", "some", "the", "pls")]
    if not words:
        return None
    best = None
    for sid, name in d.items():
        nn = " ".join(norm(name))
        if all(x in nn for x in words):
            if best is None or len(name) < len(d[best]):
                best = sid
    return best


def find_item(what):
    """Имя или SID предмета -> (SID, имя на языке игрока) или None. Сначала таблица языка
    игрока, потом другая (английский игрок напишет и русское имя, и наоборот)."""
    w = (what or "").strip()
    if not w:
        return None
    for pat, rep in SYNONYMS:
        w = re.sub(pat, rep, w, flags=re.I)
    for lang in (L.LANG, "en" if L.LANG == "ru" else "ru"):
        sid = _find_in(items(lang), w)
        if sid:
            return sid, items().get(sid) or items(lang).get(sid) or sid
    return None


def ammo_for(weapon_sid):
    """SID оружия в руках -> SID обычных патронов его калибра (таблица SpareMag)."""
    wd = weapons()
    w = wd.get("weapons", {}).get(weapon_sid or "")
    if not w:
        return None
    lets = wd.get("ammo", {}).get(w.get("cal", ""), {})
    return lets.get("D") or next(iter(lets.values()), None)


def creature_sid(what):
    """«кровосос»/«bloodsucker» -> Bloodsucker; «бандит-снайпер» -> GeneralNPC_Bandit_Sniper;
    «зомби военный»/«zombie soldier» -> GeneralZombie_Militaries_Stormtrooper. -> (SID, ранг) или None."""
    w = (what or "").lower().replace("ё", "е")
    rank = 1
    for word, r in RANKS:
        if has_word(w, word):
            rank = r
    zombie = has_word(w, "зомби") or has_word(w, "zombie")
    for word, fac in FACTIONS:
        if has_word(w, word):
            role = next((r for k, r in ROLES if has_word(w, k)), "Stormtrooper")
            return ("GeneralZombie_%s_%s" if zombie else "GeneralNPC_%s_%s") % (fac, role), rank
    for word, sid in CREATURES:
        if has_word(w, word):
            return sid, rank
    if zombie:
        return "GeneralZombie_Neutral_Stormtrooper", rank
    return None


def creature_label(sid):
    """SID -> имя на языке игрока: «кровосос», «бандит (снайпер)», «зомби одиночка», «призрак бандит»."""
    i = 1 if L.en() else 0
    if sid in CREATURE_NAMES:
        return CREATURE_NAMES[sid][i]
    m = re.match(r"(GeneralNPC|GeneralZombie|PsyNPC)_([A-Za-z]+)_([A-Za-z]+)", sid or "")
    if m:
        fac = FACTION_NAMES.get(m.group(2), (m.group(2), m.group(2)))[i]
        if m.group(1) == "GeneralZombie":
            return ("зомби %s", "zombie %s")[i] % fac
        if m.group(1) == "PsyNPC":
            return ("призрак: %s", "phantom %s")[i] % fac
        return fac
    return sid


PSY_SIDS = ["PsyNPC_Bandit_CloseCombat", "PsyNPC_Bandit_Recon", "PsyNPC_Bandit_Stormtrooper",
            "PsyNPC_Mercenaries_CloseCombat", "PsyNPC_Mercenaries_Recon", "PsyNPC_Mercenaries_Stormtrooper",
            "PsyNPC_Monolith_CloseCombat", "PsyNPC_Monolith_Recon", "PsyNPC_Monolith_Stormtrooper",
            "PsyNPC_Neutral_CloseCombat", "PsyNPC_Neutral_Recon", "PsyNPC_Neutral_StormTrooper",
            "PsyNPC_Varta_CloseCombat", "PsyNPC_Varta_Recon", "PsyNPC_Varta_Stormtrooper"]


def psy_sid(sid):
    """GeneralNPC_<фракция>_<роль> -> PsyNPC того же, если есть; иначе фантом одиночки."""
    m = re.match(r"General(?:NPC|Zombie)_([A-Za-z]+)_([A-Za-z]+)", sid or "")
    if m:
        for p in PSY_SIDS:
            if p.lower() == ("PsyNPC_%s_%s" % m.groups()).lower():
                return p
        for p in PSY_SIDS:
            if p.startswith("PsyNPC_%s_" % m.group(1)):
                return p
    return "PsyNPC_Neutral_StormTrooper"


def weather_code(what):
    w = (what or "").lower().replace("ё", "е")
    for word, code in WEATHER:
        if has_word(w, word):
            return code
    return None


def time_of(what):
    w = (what or "").lower().replace("ё", "е")
    m = re.search(r"(\d{1,2})\s*[:.ч]\s*(\d{2})?", w)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", w)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" else 0)
        return h, 0
    for word, hm in TIMES:
        if has_word(w, word):
            return hm
    return None


# ---------------------------------------------------------------- действия

ACTIONS = {
    "дать": "дать предмет: what имя предмета (аптечка, бинт, батон, водка, артефакт, оружие, броня), n сколько",
    "патроны": "патроны к оружию в руках Скифа, n сколько",
    "деньги": "дать купоны, n сколько",
    "погода": "сменить погоду: what ясно, облачно, туман, шторм, морось, дождь, гроза или «как было»",
    "время": "поставить время суток (свет неба): what часы «23:00» или утро, день, вечер, ночь",
    "выброс": "what «начать» или «остановить» выброс",
    "существо": "родить существо перед Скифом: what вид (кровосос, кабан, зомби, бандит, долговец...), n сколько",
    "призрак": "пси-фантом человека (морок) перед Скифом: what кто (бандит, наёмник, монолитовец, сталкер, боец "
               "Варты), n сколько",
    "починить": "починить оружие в руках",
    "мир": "люди вокруг: what «друзья» (не тронут) или «враги»",
    "сон": "уложить Скифа спать, только если он сам просит поспать",
    # «тайники» убраны 05.10.2026: после XRegenerateItemsInStashes тайник у Скифа остался
    # пустым (автор, дважды), а чутьё говорило «Тайники полны»
    "вперёд": "перенести Скифа вперёд по взгляду, n метров",
    # 0.2 (автор 05.10.2026: «всё хочу попробовать»; пробы Х-30, Х-31)
    "лечение": "вылечить Скифа сразу, без аптечки: what здоровье, кровь, радиация, голод, сон, силы или «всё»",
    "замедление": "замедлить время на несколько секунд: обострённое чутьё в бою",
    "скорость": "Скиф бегает быстрее одну минуту",
    "пси": "пси-удар по существу из списка «рядом»: what сбить, ранить, напугать (всех вокруг), стравить (его с "
           "соседом), поднять (труп в зомби); n номер существа (№), у «поднять» номер трупа",
    "подмога": "позвать сталкеров-одиночек на помощь Скифу, n сколько (до 3)",
}

# лечение эффектами расходников (Х-31): слова, эффект, что вышло по-русски и по-английски;
# здоровье последним: оно и по умолчанию
HEAL = [(r"кров|бинт|bleed|bandage", "BandageBleeding4", "кровь остановлена", "bleeding stopped"),
        (r"радиац|антирад|\brad", "Antirad4", "радиация выведена", "radiation flushed"),
        (r"голод|ед[аы]\b|сыт|накорм|поесть|жрат|hunger|food|\beat\b|fed\b", "FreshBreadSatiety3", "сытость",
         "fed"),
        (r"сон|сонлив|бодр|устал|sleep|tired|awake", "EnergeticSleepiness", "бодрость", "wide awake"),
        (r"выносл|\bсил|дыхан|stamina|breath|energy", "EnergeticStamina", "силы", "stamina"),
        (r"здоров|леч|ран[аыу]|ранен|health|heal|wound|\bhp\b", "MedkitHealing3", "здоровье", "health")]
SPEED_X = 1.5               # Х-31: 1.6 автор назвал «быстрее»; 1.5 мягче
SPEED_S = 60.0
# Действия с отложенным обратным шагом: (через сколько секунд, команда). Замедление
# держит свой мод (Х-30: мод в замедлении читает команды раз в ~6 с реального), так что
# «0» через 3 с дойдёт через 6-9 с.
FOLLOW = {"замедление": (3.0, "XSetXRayMode 0"), "скорость": (SPEED_S, "XSetPlayerSpeedMultiplier 1.0")}

# пси-удар: вид по словам, откат и цена (Х-31: +10 пси, спадает за 10 с)
PSY_KINDS = [("поднять", r"подн|воскрес|ожив|зомби|raise|resurrect|zombie|undead"),
             ("стравить", r"страв|натрав|друг\s+на\s+друга|против|turn|against|each\s+other|set\s+them"),
             ("напугать", r"напуг|пугн|отпуг|прогон|отгон|scare|frighten|drive|chase"),
             ("ранить", r"ран|wound|cripple"),
             ("сбить", r"сб[ие]|повал|толкн|удар|knock|push|down|hit|strike")]
PSY_COOLDOWN = 20.0
PSY_COST = "XApplyEffectOnPlayer PSYAdd5PointsInsta"
_PSY_T = [0.0]
HELP_SID = "GeneralNPC_Neutral_Stormtrooper"


def schema(catalog):
    """Схема ответа: сначала действия, потом реплика. Грамматика llama-server идёт по
    порядку полей, и реплика пишется уже после выбора действий (журнал 05.10.2026
    07:15-07:19: при порядке «реплика, действия» модель писала «Кабан спит спереди»)."""
    return {
        "type": "object",
        "properties": {
            "actions": {"type": "array", "maxItems": 3, "items": {
                "type": "object",
                "properties": {"do": {"type": "string", "enum": sorted(catalog)},
                               "what": {"type": "string"},
                               "n": {"type": "number"}},
                "required": ["do", "what", "n"]}},
            "reply": {"type": "string"},
        },
        "required": ["actions", "reply"],
    }


ACTION_SCHEMA = schema(ACTIONS)

# Действия с заметными последствиями: только если в словах Скифа есть их слово.
# Журнал 05.10.2026 07:16: на «заспавни зомби» модель выбрала «сон» и уложила Скифа
# спать («Зомби нет, но зову псевдособаку отдыхать»).
GUARDS = {"сон": r"спат|сон\b|сна\b|поспа|усн|отдох|выспа|ночлег|sleep|nap\b|rest\b",
          "выброс": r"выброс|emission|blowout",
          "вперёд": r"перенес|телепорт|вперёд|вперед|прыгн|перемест|teleport|forward|move\s+me|jump",
          "мир": r"друз|враг|мир\b|отношен|помири|дружб|войн|friend|enem|peace|hostile|relation|truce",
          "время": r"ноч|утр|день|днём|вечер|рассвет|закат|полдн|полноч|сумерк|время|\d{1,2}\s*[:.ч]|night|morning|"
                   r"\bday\b|evening|dawn|dusk|sunset|sunrise|noon|midnight|\btime\b|o'?clock|\d\s*(am|pm)\b",
          "погода": r"погод|дожд|гроз|туман|ясн|солн|облач|пасмурн|шторм|бур|ветр|морос|ливен|ливн|weather|rain|storm|"
                    r"thunder|fog|mist|clear|\bsun|cloud|drizzle|wind|overcast|downpour",
          # 0.2: руки на людей, время и тело только по прямому слову Скифа
          "пси": r"сб[ие]|повал|толкн|удар|ран[иья]|напуг|пугн|отпуг|прогон|отгон|страв|натрав|подн|воскрес|ожив|"
                 r"зомби|knock|push|hit|strike|wound|scare|frighten|drive|chase|turn|against|raise|resurrect|zombie",
          "замедление": r"замедл|медлен|останов\w*\s+врем|время\s+стоп|слоу|slow|bullet\s*time|freeze\s+time",
          "скорость": r"быстр|скорост|ускор|бега|faster|speed|haste|quick",
          "подмога": r"подмог|помощ|помоги|подкреп|союзник|своих|отряд|backup|reinforc|help|allies|squad|buddies",
          "лечение": r"леч|здоров|кров|бинт|радиац|антирад|голод|ед[аы]\b|сыт|накорм|бодр|устал|сонлив|\bсил|выносл|"
                     r"heal|health|bleed|bandage|\brad|hunger|food|\beat\b|tired|sleepy|stamina|energy|patch"}

NUMBERS = {"один": 1, "одного": 1, "одну": 1, "одна": 1, "пару": 2, "два": 2, "две": 2, "двух": 2, "три": 3, "трёх": 3,
           "трех": 3, "четыре": 4, "четырёх": 4, "пять": 5, "пяти": 5, "шесть": 6, "семь": 7, "восемь": 8,
           "девять": 9, "десять": 10, "дюжину": 12, "двадцать": 20, "тридцать": 30, "сто": 100,
           "one": 1, "couple": 2, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "dozen": 12, "twenty": 20, "thirty": 30, "hundred": 100}
SPAWN_VERB = re.compile(r"\b(за)?спавн\w*|\bсозда\w*|\bроди\b|\bвызови\b|\bпризови\b|\bнатрави\b|"
                        r"\bspawn\w*|\bsummon\w*|\bcreate\b|\bconjure\b", re.I)
GIVE_VERB = re.compile(r"\b(дай|дайте|выдай|подкинь|подкини|насыпь|отсыпь|хочу|нужн\w*|(за)?спавн\w*|"
                       r"give|gimme|hand|want|need|spawn)\b", re.I)


def number_in(text):
    m = re.search(r"\b(\d{1,4})\b", text or "")
    if m:
        return int(m.group(1))
    for w in norm(text):
        if w in NUMBERS:
            return NUMBERS[w]
    return None


def rule_actions(question):
    """Очевидные просьбы правилом, если модель промолчала (журнал 07:16: «заспавни
    кровососа» -> «Кровососа нет»): «заспавни/создай X» с существом X, «дай Y» с
    предметом Y. -> список действий в виде модели (внутренние ключи)."""
    q = question or ""
    out = []
    if SPAWN_VERB.search(q) and creature_sid(q):
        kind = "призрак" if re.search(r"призрак|фантом|морок|phantom|ghost", q, re.I) else "существо"
        out.append({"do": kind, "what": q, "n": number_in(q) or 1})
    elif GIVE_VERB.search(q):
        if re.search(r"купон|деньг|денег|деньжат|бабл|бабк|coupon|money|cash", q, re.I):
            # прогон 05.10.2026: «подкинь купонов 500» -> правило дало ещё 50 предметов
            # moneyCard (имя с «купон»): деньги это действие «деньги», не предмет
            out.append({"do": "деньги", "what": "", "n": number_in(q) or 1000})
        elif re.search(r"патрон|ammo|bullets|rounds", q, re.I):
            # без калибра и коробки: патроны к стволу в руках («give me some ammo» иначе
            # находило предмет «Large RP-74M Ammo Box»); с калибром решает модель
            if not re.search(r"\d+[.,x×х]\d+|\.\d{3}|калибр|gauge|caliber|коробк|box", q, re.I):
                out.append({"do": "патроны", "what": "", "n": number_in(q) or 60})
        else:
            it = find_item(strip_request(q))
            if it:
                out.append({"do": "дать", "what": it[1], "n": number_in(q) or 1})
    return out


def strip_request(q):
    """«заспавни лучшие аптечки 10 штук» -> «лучшие аптечки»; «give me 3 medkits» -> «medkits»."""
    w = GIVE_VERB.sub(" ", q or "")
    w = re.sub(r"\b\d+\b|\bштук\w*|\bмне\b|\bещё\b|\bеще\b|\bпожалуйста\b|\bme\b|\bplease\b|\bsome\b|\ba\b|\ban\b|"
               r"\bthe\b|\bpieces?\b|\bmore\b", " ", w, flags=re.I)
    for k in NUMBERS:
        w = re.sub(r"\b%s\b" % k, " ", w, flags=re.I)
    return w.strip(" ,.!?")


def spawn_points(st, count, dist_cm=2200.0, turn=0.0):
    """Дуга перед Скифом (как spawnSuckerBatch в QuestBeacons): лицом к нему; turn 180 за спиной."""
    pos, yaw = st.get("pos_cm"), st.get("yaw")
    if pos is None or yaw is None:
        return []
    out = []
    for i in range(count):
        spread = (-40 + 80 * i / (count - 1)) if count > 1 else 0.0
        a = math.radians(yaw + turn + spread)
        out.append((pos[0] + math.cos(a) * dist_cm, pos[1] + math.sin(a) * dist_cm, pos[2] + 50.0,
                    (yaw + turn + spread + 180.0) % 360.0))
    return out


def _say(ru, en_):
    return en_ if L.en() else ru


def build(action, st):
    """Одно действие (внутренний ключ) -> (команды, что вышло словами) или ([], почему нет)."""
    do = action.get("do", "")
    what = str(action.get("what", "") or "")
    try:
        n = float(action.get("n", 1) or 1)
    except (TypeError, ValueError):
        n = 1.0
    me = (st or {}).get("me") or {}
    lim = LIMITS.get(do)
    cnt = max(1, min(int(round(n)), lim)) if lim else max(1, int(round(n)))
    if do == "дать":
        it = find_item(what)
        if not it:
            return [], _say("не нашёл предмет «%s»", "couldn't find the item \"%s\"") % what
        return ["XCreateItemInInventoryByID %s 0 %d 1" % (it[0], cnt)], "%s ×%d" % (it[1], cnt)
    if do == "патроны":
        sid = ammo_for(me.get("w"))
        if not sid and not me.get("w"):
            # журнал 05.10.2026 11:04: «дай патроны на оружие, которое у меня есть» с пустыми руками;
            # рюкзака чутьё не видит, патроны только к стволу в руках или по калибру
            return [], _say("руки пустые: возьми ствол в руки или назови калибр",
                            "your hands are empty: take a gun in hand or name the caliber")
        if not sid:
            return [], _say("в руках нет оружия с известным калибром", "no gun with a known caliber in hand")
        return ["XCreateItemInInventoryByID %s 0 %d 1" % (sid, cnt)], "%s ×%d" % (items().get(sid, sid), cnt)
    if do == "деньги":
        return ["XAddMoneyToPlayer %d" % cnt], _say("%d купонов", "%d coupons") % cnt
    if do == "погода":
        if re.search(r"как было|обычн|верни|сним|as it was|normal|back|reset", what.lower()):
            return ["XSetWeatherLocked 0"], _say("погода снова своя", "weather back to normal")
        code = weather_code(what)
        if code is None:
            return [], _say("не понял погоду «%s»", "didn't get the weather \"%s\"") % what
        return ["XForceWeather %d" % code, "XSetWeatherLocked 1"], \
            _say("погода: %s", "weather: %s") % WEATHER_NAMES[code][1 if L.en() else 0]
    if do == "время":
        hm = time_of(what)
        if not hm:
            return [], _say("не понял время «%s»", "didn't get the time \"%s\"") % what
        # только свет: XSetGameTime двигал часы вперёд, голод за 05.10.2026 08:17 прыгнул с 20
        # до 73 (здоровье пошло вниз); XSetWeatherTime голод не трогает (25,1 -> 25,2)
        return ["XSetWeatherTime %d %d 0" % hm], _say("время %02d:%02d", "time %02d:%02d") % hm
    # «промотать» (XSkipTimeHours) убрана 05.10.2026: 15 минут подняли голод с 72,7 до 100,
    # здоровье пошло вниз по 4 единицы за несколько секунд.
    if do == "выброс":
        if re.search(r"стоп|останов|прекрат|убер|хват|конч|stop|end|cancel|halt", what.lower()):
            return ["XStopEmission"], _say("выброс остановлен", "emission stopped")
        return ["XStartEmission"], _say("выброс начат", "emission started")
    if do in ("существо", "призрак"):
        cs = creature_sid(what)
        if not cs:
            return [], _say("не понял, кого родить: «%s»", "didn't get what to spawn: \"%s\"") % what
        sid, rank = cs
        if do == "призрак":
            # XSpawnPsyNPC (1 и 0) 05.10.2026 ничего не родил ближе 50 м за 15 с; фантомы
            # есть прототипами PsyNPC_<фракция>_<роль> (GeneralNPCObjPrototypes.cfg, только
            # люди), XSpawnObjBySID PsyNPC_Neutral_StormTrooper родил «одиночку» в 22 м.
            sid = psy_sid(sid)
        pts = spawn_points(st, cnt)
        if not pts:
            return [], _say("не знаю, где Скиф", "don't know where Skif is")
        # высоту мод 0.0.6 сам опускает на землю под точкой (nestor.spec.py, автор 05.10.2026:
        # «кабан висел в воздухе»): слова команды и их порядок не менять, мод их читает
        return ["XSpawnObjBySID %s %d 0 %.0f %.0f %.0f 0 %.0f 0" % (sid, rank, x, y, z, yw) for x, y, z, yw in pts], \
            _say("%s ×%d в 22 м перед Скифом", "%s ×%d, 22 m ahead of Skif") % (creature_label(sid), cnt)
    if do == "починить":
        # журнал 05.10.2026 11:46-11:47: «почини пистолет» с пустыми руками -> «Пистолет исправен»,
        # автор: «пистолет не починен». XRepairCurrentWeapon чинит только ствол в руках, броню нет.
        w = me.get("w")
        if not w:
            return [], _say("чиню только оружие в руках: возьми его в руки",
                            "I can only repair the gun in your hands: take it in hand")
        return ["XRepairCurrentWeapon"], _say("починено: %s", "repaired: %s") % items().get(w, w)
    # «бессмертие» убрано 05.10.2026: XSetGodMode 1 и XSetGodModeByUID 0 1 урон не держат
    # (XDealDamage 0 5: 95 -> 90 и 44,2 -> 39,2).
    if do == "мир":
        enemy = bool(re.search(r"враг|войн|напад|enem|hostile|war", what.lower()))
        return ["XSetRelationsInRadius 5000 %d" % (-1000 if enemy else 1000)], \
            _say("люди в 50 м: %s", "people within 50 m: %s") % (
                _say("враги", "enemies") if enemy else _say("друзья", "friends"))
    if do == "сон":
        return ["XStartSleep"], _say("сон", "sleep")
    # «скорость времени» (XSetTimeSpeed) убрана 05.10.2026: проба ×10 подняла голод в 126
    # раз, через 15 с здоровье 70 -> 0, Скиф умер (журнал моста 08:07:30-31).
    if do == "вперёд":
        return ["XTeleportPlayerInForwardDirection %d 0" % (cnt * 100)], _say("вперёд на %d м", "forward %d m") % cnt
    if do == "лечение":
        w = (what + " " + str(action.get("_q", ""))).lower()
        if re.search(r"вс[её]\b|полност|целиком|\ball\b|everything|\bfull", w):
            picks = HEAL
        else:
            picks = [h for h in HEAL if re.search(h[0], w, re.I)] or [HEAL[-1]]
        return ["XApplyEffectOnPlayer %s" % h[1] for h in picks], ", ".join(_say(h[2], h[3]) for h in picks)
    if do == "замедление":
        return ["XSetXRayMode 1"], _say("время замедлено на несколько секунд", "time slowed for a few seconds")
    if do == "скорость":
        return ["XSetPlayerSpeedMultiplier %.1f" % SPEED_X], _say("Скиф быстрее на минуту",
                                                                  "Skif is faster for a minute")
    if do == "пси":
        return psy_strike(what, int(round(n)), st, str(action.get("_q", "")), action.get("_human"))
    if do == "подмога":
        pts = spawn_points(st, cnt, 1200.0, 180.0)
        if not pts:
            return [], _say("не знаю, где Скиф", "don't know where Skif is")
        return ["XSpawnObjBySID %s 2 0 %.0f %.0f %.0f 0 %.0f 0" % (HELP_SID, x, y, z, yw) for x, y, z, yw in pts], \
            _say("подмога: одиночки ×%d идут со спины", "backup: %d loners coming from behind") % cnt
    return [], _say("нет такого действия «%s»", "no such action \"%s\"") % do


def psy_kind(what, q=""):
    for text in (what, q):
        for kind, pat in PSY_KINDS:
            if re.search(pat, (text or "").lower()):
                return kind
    return "сбить"


def kind_label(q):
    """Вид из слов Скифа как имя агента в обстановке: «бандит», «кабан», «одиночка»."""
    cs = creature_sid(q)
    if not cs:
        return None
    if cs[0] in CREATURE_NAMES:
        return CREATURE_NAMES[cs[0]][0]
    m = re.match(r"General(?:NPC|Zombie)_([A-Za-z]+)_", cs[0])
    return FACTION_NAMES.get(m.group(1), ("",))[0] if m else None


def pick_target(pool, n, q):
    """Цель из списка с номерами: «целится» в словах Скифа, потом вид из его слов (номер
    модели, если вид совпал), потом номер модели, потом целящийся, насторожённый, ближний."""
    live = [a for a in pool if a.get("uid")]
    if not live:
        return None
    if re.search(r"целит|прицел|\baim", q or "", re.I):
        aiming = [a for a in live if a.get("aim")]
        if aiming:
            return aiming[0]
    by_n = pool[n - 1] if 1 <= n <= len(pool) and pool[n - 1].get("uid") else None
    label = kind_label(q)
    if label:
        same = [a for a in live if a["who"].endswith(label)]
        if same:
            return by_n if by_n in same else same[0]
    if by_n:
        return by_n
    for key in ("aim", "threat"):
        hot = [a for a in live if a.get(key)]
        if hot:
            return hot[0]
    return live[0]


def psy_strike(what, n, st, q="", is_human=None):
    """Пси-удар чутья (0.2): по номеру существа из обстановки мода 0.0.7 (номера GetGUID).
    Откат PSY_COOLDOWN, цена: пси Скифа (Х-31)."""
    st = st or {}
    kind = psy_kind(what, q)
    left = PSY_COOLDOWN - (time.time() - _PSY_T[0])
    if left > 0:
        return [], _say("чутьё ещё не собралось, через %d с", "the gut hasn't recovered yet, %d s") % math.ceil(left)
    name = (lambda a: L.name(a["who"]))
    if kind == "напугать":
        cmds = ["XOverrideCombatTacticsInRadius 3000 3"]
        text = _say("все в 30 м отступают", "everyone within 30 m falls back")
    else:
        if st.get("v", 0) < 7:
            return [], _say("для этого нужен мод NESTOR 0.0.7: обнови файлы мода",
                            "this needs NESTOR mod 0.0.7: update the mod files")
        human = is_human or (lambda w: True)
        if kind == "поднять":
            # Номер модели из общего списка трупов (как в сводке); не человек: ближний человек.
            # Встают только свежие (проба 05.10.2026: старые трупы монолитовцев, разложенные игрой,
            # не поднялись, убитые при Скифе встали сразу): сначала тех, кого мост видел живыми.
            # Поднятый сразу друг Скифу (без этого шёл на него), номер у зомби тот же.
            pool = st.get("dead") or []
            fresh = st.get("_fresh") or set()
            ok = (lambda d: d.get("uid") and human(d["who"]))
            t = pool[n - 1] if 1 <= n <= len(pool) and ok(pool[n - 1]) else None
            if t is None or (fresh and t["uid"] not in fresh):
                t = (next((d for d in pool if ok(d) and d["uid"] in fresh), None) or t
                     or next((d for d in pool if ok(d)), None))
            if t is None:
                return [], _say("рядом нет человеческих трупов", "no human bodies nearby")
            cmds = ["XResurrectNPCAsZombie %d" % t["uid"], "XSetRelation %d 0 1000" % t["uid"]]
            text = _say("поднят зомби, свой: %s", "raised as a zombie on your side: %s") % name(t)
        else:
            pool = st.get("agents") or []
            t = pick_target(pool, n, q)
            if t is None:
                return [], _say("рядом никого", "nobody nearby")
            if kind == "сбить":
                cmds, text = ["XKnockDownNpc %d" % t["uid"]], _say("сбит с ног: %s в %d м", "knocked down: %s at %d m") % (
                    name(t), t["m"])
            elif kind == "ранить":
                cmds, text = ["XWoundNpcByUID %d" % t["uid"]], _say("ранен: %s в %d м", "wounded: %s at %d m") % (
                    name(t), t["m"])
            else:
                mates = [a for a in pool if a.get("uid") and a is not t and a.get("loc") and t.get("loc")]
                if not mates:
                    return [], _say("рядом с ним некого стравить", "nobody near him to turn against")
                p = min(mates, key=lambda a: math.dist(a["loc"], t["loc"]))
                # проба 05.10.2026: стравленные дерутся, если стоят рядом и к Скифу нейтральны;
                # враждебные к Скифу бьют его, а не друг друга
                cmds = ["XSetRelation %d %d -1000" % (t["uid"], p["uid"]), "XSetRelation %d %d -1000" % (p["uid"], t["uid"]),
                        "XSetRelation %d 0 0" % t["uid"], "XSetRelation %d 0 0" % p["uid"]]
                text = _say("стравлены: %s и %s", "turned on each other: %s and %s") % (name(t), name(p))
    _PSY_T[0] = time.time()
    return cmds + [PSY_COST], text


def guard(actions, question):
    """Убрать действия, чьего слова нет в просьбе (GUARDS); -> (оставлено, убрано)."""
    keep, drop = [], []
    for a in actions or []:
        pat = GUARDS.get(a.get("do", ""))
        if pat and question and not re.search(pat, question, re.I):
            drop.append(a)
        else:
            keep.append(a)
    return keep, drop


def merge_rules(actions, question):
    """Действия модели плюс очевидные по правилу, если модель такого вида не дала.
    Предмет, найденный правилом по словам самого Скифа, точнее предмета модели:
    журнал 05.10.2026 07:19, «заспавни лучшие аптечки 10 штук» -> модель «аптечка»."""
    rules = rule_actions(question)
    have = {a.get("do") for a in actions}
    picky = any(re.search(pat, question or "", re.I) for pat, _rep in SYNONYMS)   # «лучшие», «военные», «army»
    out = []
    for a in actions:
        r = next((r for r in rules if r["do"] == a.get("do") == "дать"), None) if picky else None
        out.append(dict(a, what=r["what"]) if r and find_item(a.get("what", "")) != find_item(r["what"]) else a)
    for r in rules:
        if r["do"] not in have and not ({"существо", "призрак"} & have and r["do"] in ("существо", "призрак")):
            out.append(r)
    return out


CORRECTION = re.compile(r"а\s+ты\s+(дал|дала)|не\s+т(е|о|у|ой|ак)\b|не\s+такие|друг(ие|ую|ой)|лучшие\s+это|"
                        r"а\s+не\s+обычн|перепута|not\s+(these|those|that|this)|wrong\s+(one|ones|kind)|i\s+meant|"
                        r"the\s+(better|best|military|army)\s+ones?|you\s+gave\s+me", re.I)
_CORR_SKIP = {"а", "ты", "дал", "дала", "это", "не", "те", "то", "такие", "обычные", "обычную", "лучшие", "мне", "другие",
              "not", "these", "those", "that", "this", "wrong", "one", "ones", "kind", "i", "meant", "you", "gave", "me",
              "the", "ordinary", "regular", "normal", "ones", "but"}


def correction(question, last_give):
    """«лучшие это военные, а ты дал обычные» после выдачи «Аптечка»: уточнение из
    слов Скифа плюс главное слово прошлого предмета -> действие или None."""
    if not last_give or not CORRECTION.search(question or ""):
        return None
    name, n = last_give
    head = norm(name)[-1] if norm(name) else ""
    words = [w for w in norm(question) if w not in _CORR_SKIP]
    it = find_item(" ".join(words + [head])) or find_item(" ".join(norm(question) + [head]))
    if it and it[1] != name:
        return {"do": "дать", "what": it[1], "n": n}
    return None


def build_all(actions, st, question="", is_human=None, follow=None):
    """-> (команды через «;», что вышло, что нет). question и is_human нужны пси-удару (цель
    по словам Скифа, люди среди трупов); в follow (список) ложатся отложенные обратные
    шаги сделанного: (через сколько секунд, команда), FOLLOW."""
    cmds, done, failed = [], [], []
    for a in (actions or [])[:3]:
        c, what = build(dict(a, _q=question, _human=is_human), st)
        if c:
            cmds += c
            done.append(what)
            if follow is not None and a.get("do") in FOLLOW:
                follow.append(FOLLOW[a["do"]])
        else:
            failed.append(what)
    return ";".join(cmds), done, failed
