# -*- coding: utf-8 -*-
"""NESTOR: память чутья о Скифе между играми (04.10.2026, НЕСТОР.md).

Автор: «каждый раз с нуля, друга, который о тебе знает, не сделать». Модель сама
ничего не помнит; память лежит у игрока на диске, мост подмешивает её в каждый
вопрос и дописывает:

  память.md        знакомство (что Скиф сказал о себе), прожитое (строка дневника
                   за игру), отношения (сколько вместе); обычный текст, игрок
                   правит и стирает его сам, строки под заголовками со знаком «-»;
  состояние.json   счётчики и места, где Скифа убивали или едва не убили
                   (дежавю: чутьё живёт вне сохранений и помнит их после загрузки).

Своя память у каждого профиля Steam (HKCU\\Software\\Valve\\Steam\\ActiveProcess,
ActiveUser): папка <каталог>/steam-<номер>.
"""
import json
import math
import os
import re
import time

SECTIONS = ("Знакомство", "Прожитое", "Отношения")       # внутренние ключи разделов
# Язык файла памяти (выпуск на Nexus, 05.10.2026): английская память в memory.md и
# state.json, русская по-прежнему в память.md и состояние.json.
TEXTS = {
    "ru": {"md": "память.md", "js": "состояние.json",
           "head": "# Память чутья о Скифе",
           "note": "Файл ведёт NESTOR. Править и стирать можно что угодно: строки под заголовками со знаком «-».",
           "sec": {"Знакомство": "Знакомство", "Прожитое": "Прожитое", "Отношения": "Отношения"},
           "together": "Вместе с %s: игр %d, разговоров %d, смертей %d.", "together_mark": "Вместе с ",
           "p_together": "Вместе с %s: игр %d, разговоров %d, смертей Скифа %d.",
           "p_acq": "Знакомство:", "p_lived": "Прожитое вместе:", "p_rel": "Отношения:",
           "empty": "Пока пусто: вы только знакомитесь."},
    "en": {"md": "memory.md", "js": "state.json",
           "head": "# The gut's memory of Skif",
           "note": "NESTOR keeps this file. Edit or delete anything: lines under the headings start with \"-\".",
           "sec": {"Знакомство": "Acquaintance", "Прожитое": "Lived through", "Отношения": "Relationship"},
           "together": "Together since %s: games %d, talks %d, deaths %d.", "together_mark": "Together since ",
           "p_together": "Together since %s: games %d, talks %d, Skif's deaths %d.",
           "p_acq": "Acquaintance:", "p_lived": "Lived through together:", "p_rel": "Relationship:",
           "empty": "Empty so far: you are just getting acquainted."},
}
MAX_FACTS = 30            # знакомство: столько строк держать
DIARY_KEEP = 15           # прожитое: больше строк, и старые сжимаются в одну
MERGE_CM = 3000           # два места ближе 30 м это одно место
MERGE_DZ_CM = 1500


def profile_id():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess")
        v, _t = winreg.QueryValueEx(k, "ActiveUser")
        if v:
            return "steam-%d" % v
    except OSError:
        pass
    return "default"


def today():
    return time.strftime("%d.%m.%Y")


def _norm(s):
    return re.sub(r"[^\w ]+", "", s.lower().replace("ё", "е")).strip()


def flat_dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Memory:
    def __init__(self, root, profile=None, lang="ru"):
        self.t = TEXTS.get(lang, TEXTS["ru"])
        self.dir = os.path.join(root, profile or profile_id())
        os.makedirs(self.dir, exist_ok=True)
        self.md_path = os.path.join(self.dir, self.t["md"])
        self.js_path = os.path.join(self.dir, self.t["js"])
        self.sections = {name: [] for name in SECTIONS}
        self.extra = []                       # чужие разделы игрока: сохраняются как есть
        self.state = {"first": None, "sessions": 0, "talks": 0, "deaths": 0, "next_id": 1, "places": []}
        self.load()

    # ------------------------------------------------------------ файлы
    def load(self):
        if os.path.exists(self.js_path):
            with open(self.js_path, encoding="utf-8") as f:
                self.state.update(json.load(f))
        if not os.path.exists(self.md_path):
            return
        cur = None
        back = {v: k for k, v in self.t["sec"].items()}
        for line in open(self.md_path, encoding="utf-8").read().splitlines():
            m = re.match(r"^##\s+(.+?)\s*$", line)
            if m:
                cur = back.get(m.group(1), m.group(1))
                if cur not in self.sections:
                    self.extra.append([line])
                continue
            if cur is None:
                continue
            if cur in self.sections:
                if line.startswith("- "):
                    self.sections[cur].append(line[2:].strip())
            elif self.extra:
                self.extra[-1].append(line)
        # первая строка «Отношений» считается из счётчиков, её не хранить
        self.sections["Отношения"] = [l for l in self.sections["Отношения"]
                                      if not l.startswith(self.t["together_mark"])]

    def save(self):
        st = self.state
        rel = [self.t["together"] % (st["first"] or today(), st["sessions"], st["talks"], st["deaths"])]
        out = [self.t["head"], "", self.t["note"]]
        for name in SECTIONS:
            lines = rel + self.sections[name] if name == "Отношения" else self.sections[name]
            out += ["", "## " + self.t["sec"][name]] + ["- " + l for l in lines]
        for block in self.extra:
            out += [""] + [l for l in block if l.strip() or l is not block[-1]]
        tmp = self.md_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out).rstrip("\n") + "\n")
        os.replace(tmp, self.md_path)
        tmp = self.js_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.js_path)

    # ------------------------------------------------------------ игра и счётчики
    def start_session(self, game_pid=None):
        """Новая игра +1. Перезапуск моста посреди той же игры (тот же процесс игры)
        не считается: 04-05.10.2026 перезапуски дали «игр 5» при трёх играх."""
        if not self.state["first"]:
            self.state["first"] = today()
        if game_pid is None or self.state.get("game_pid") != game_pid:
            self.state["sessions"] += 1
        self.state["game_pid"] = game_pid
        self.save()

    def count_talk(self):
        self.state["talks"] += 1
        self.save()

    # ------------------------------------------------------------ знакомство
    def facts(self):
        return list(self.sections["Знакомство"])

    def apply_facts(self, adds, drops):
        """adds: новые строки; drops: строки, которые Скиф попросил забыть.
        -> (добавлено, забыто)."""
        known = {_norm(f) for f in self.sections["Знакомство"]}
        added, dropped = [], []
        for d in drops:
            nd = set(_norm(d).split())
            keep = []
            for f in self.sections["Знакомство"]:
                nf = set(_norm(f).split())
                if nf and nd and len(nf & nd) >= max(1, int(0.6 * min(len(nf), len(nd)))):
                    dropped.append(f)
                else:
                    keep.append(f)
            self.sections["Знакомство"] = keep
        for a in adds:
            a = a.strip().rstrip(".") + "."
            if len(a) < 4 or _norm(a) in known:
                continue
            self.sections["Знакомство"].append(a)
            known.add(_norm(a))
            added.append(a)
        self.sections["Знакомство"] = self.sections["Знакомство"][-MAX_FACTS:]
        if added or dropped:
            self.save()
        return added, dropped

    # ------------------------------------------------------------ прожитое
    def add_diary(self, line):
        line = line.strip()
        if line:
            self.sections["Прожитое"].append("%s: %s" % (today(), line))
            self.save()

    def diary_overflow(self):
        """Старые строки дневника, которые пора сжать в одну, или []."""
        d = self.sections["Прожитое"]
        return d[:len(d) - DIARY_KEEP + 5] if len(d) > DIARY_KEEP else []

    def replace_old_diary(self, old, merged):
        d = self.sections["Прожитое"]
        if d[:len(old)] == old and merged.strip():
            first = old[0].split(":", 1)[0]
            last = old[-1].split(":", 1)[0]
            self.sections["Прожитое"] = ["%s..%s: %s" % (first, last, merged.strip())] + d[len(old):]
            self.save()

    # ------------------------------------------------------------ для модели
    def prompt_text(self, limit=1800):
        f = self.sections["Знакомство"][-20:]
        d = self.sections["Прожитое"][-8:]
        st = self.state
        parts = []
        if f:
            parts.append(self.t["p_acq"] + "\n" + "\n".join("- " + x for x in f))
        if d:
            parts.append(self.t["p_lived"] + "\n" + "\n".join("- " + x for x in d))
        rel = [self.t["p_together"] % (st["first"] or today(), st["sessions"], st["talks"], st["deaths"])]
        rel += self.sections["Отношения"]
        parts.append(self.t["p_rel"] + "\n" + "\n".join("- " + x for x in rel))
        text = "\n".join(parts)
        if not f and not d and st["talks"] == 0:
            text = self.t["empty"] + "\n" + text
        return text[-limit:]

    # ------------------------------------------------------------ свои звери
    def pets(self):
        return set(self.state.get("pets", []))

    def add_pet(self, kind):
        pets = self.state.setdefault("pets", [])
        if kind in pets:
            return False
        pets.append(kind)
        self.save()
        return True

    # ------------------------------------------------------------ дежавю: опасные места
    def record_place(self, kind, pos, who, cause=""):
        """kind: «смерть» или «рана» (едва не убили). Место ближе 30 м сливается."""
        now = today()
        if kind == "смерть":
            self.state["deaths"] += 1
        for p in self.state["places"]:
            if flat_dist(p["pos"], pos) <= MERGE_CM and abs(p["pos"][2] - pos[2]) <= MERGE_DZ_CM:
                p["n"] += 1
                p["last"] = now
                p["t"] = time.time()
                if kind == "смерть":
                    p["kind"] = "смерть"
                    p["deaths"] = p.get("deaths", 0) + 1
                if who:
                    p["who"] = who
                if cause:
                    p["cause"] = cause
                self.save()
                return p
        p = {"id": self.state["next_id"], "pos": [round(c) for c in pos], "kind": kind, "who": who or "",
             "cause": cause or "", "n": 1, "deaths": 1 if kind == "смерть" else 0, "first": now, "last": now,
             "t": time.time()}
        self.state["next_id"] += 1
        self.state["places"].append(p)
        self.save()
        return p

    def near_places(self, pos, radius_cm, dz_cm=None):
        out = []
        for p in self.state["places"]:
            d = flat_dist(p["pos"], pos)
            if d <= radius_cm and (dz_cm is None or abs(p["pos"][2] - pos[2]) <= dz_cm):
                out.append((d, p))
        out.sort(key=lambda x: x[0])
        return out
