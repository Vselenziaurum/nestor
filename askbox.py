# -*- coding: utf-8 -*-
"""NESTOR: окно «сказать про себя» поверх игры по горячей клавише (04.10.2026).

Автор: «а ему можно писать?» -> окно на F4. Клавиша глобальная (RegisterHotKey):
пока мост работает, игра F4 не получает (у QuestBeacons F4 перечитывает Lua, это
только щуп).

Чат (автор 05.10.2026: «чат нужно сделать в левом верхнем углу», «ф4 показывает чат
и не убирает, снова ф4 убирает»): окно в левом верхнем углу, F4 открывает и
закрывает, Enter отправляет и окно остаётся, своя реплика сразу в истории с «…»,
пока чутьё думает; Esc закрывает. Закрыли: фокус возвращается окну игры.

Полноэкранный режим работает (проверено автором 05.10.2026, игра на DX12): окно
встаёт поверх игры. Экран на миг моргает, когда фокус уходит к окну и возвращается
к игре, поэтому чат не закрывается после каждой реплики. Прежняя запись «из
эксклюзивного полноэкранного Windows её свернёт» была догадкой, не замером.

Устройство: поток hotkey держит RegisterHotKey и цикл GetMessage (WM_HOTKEY
приходит в очередь того потока, который регистрировал); поток окна держит Tk
(все вызовы Tk из одного потока) и раз в 50 мс смотрит флаг клавиши.

История (04.10.2026, автор: «как ты видишь эти строки? Я их не вижу»): реплика
висит на экране 8 с, остальное было только в журнале моста. Теперь над полем
ввода последние реплики чутья и разговор со временем (add_history из моста).
"""
import collections
import ctypes
import math
import queue
import threading
import time
from ctypes import wintypes

import lang as L

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_F4 = 0x73
GAME_EXE = "stalker2-win64-shipping.exe"
HISTORY = 8                 # строк истории (вопрос и ответ это две)
WRAP = 60                   # знаков в строке истории для расчёта высоты
BG, FG, MUTED, YOU = "#14171c", "#e8e6e1", "#7f8d99", "#9fb0bf"


def game_window():
    """Главное окно игры: видимое окно процесса Stalker2-Win64-Shipping.exe."""
    found = []
    psapi = ctypes.windll.psapi

    def cb(hwnd, _l):
        if not u32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x0410, False, pid.value)          # QUERY_INFORMATION | VM_READ
        if h:
            buf = ctypes.create_unicode_buffer(260)
            if psapi.GetModuleBaseNameW(h, None, buf, 260) and buf.value.lower() == GAME_EXE:
                found.append(hwnd)
            k32.CloseHandle(h)
        return True

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    u32.EnumWindows(proto(cb), 0)
    return found[0] if found else None


class AskBox:
    """Окно Tk живёт всё время работы моста, клавиша только пока идёт игра:
    stop_hotkey() снимает её с закрытием игры (иначе F4 пропадёт в других
    программах, пока мост ждёт следующий запуск), start_hotkey() ставит снова."""

    def __init__(self, vk=VK_F4, title="NESTOR", store=None):
        self.vk = vk
        self.title = title
        self.store = store                     # куда помнить место чата (json), относительно окна игры
        self.questions = queue.Queue()
        self.pressed = threading.Event()
        self.ok = threading.Event()
        self.error = None
        self.hk_thread = None
        self.hk_tid = None
        self.history = collections.deque(maxlen=HISTORY)
        self.hist_lock = threading.Lock()
        self.hist_dirty = threading.Event()
        self.close_req = threading.Event()     # игра закрылась: открытый чат убрать

    def add_history(self, who, text, when=None):
        """who: «ты» (вопрос Скифа) или «чутьё». Зовётся из потока моста."""
        with self.hist_lock:
            self.history.append((when or time.strftime("%H:%M"), who, text))
        self.hist_dirty.set()

    def load_offset(self):
        """Сдвиг чата от левого верхнего угла окна игры, куда его в прошлый раз оттащили."""
        try:
            import json
            d = json.load(open(self.store, encoding="utf-8"))
            return int(d["dx"]), int(d["dy"])
        except Exception:
            return None

    def save_offset(self, off):
        if not self.store:
            return
        try:
            import json
            with open(self.store, "w", encoding="utf-8") as f:
                json.dump({"dx": off[0], "dy": off[1]}, f)
        except OSError:
            pass

    def prefill(self, items):
        """История прошлых игр из памяти: [(когда, кто, текст)], только в пустое окно."""
        with self.hist_lock:
            if not self.history:
                self.history.extend(tuple(x) for x in items[-HISTORY:])
        self.hist_dirty.set()

    def start(self):
        threading.Thread(target=self._tk_loop, daemon=True).start()
        return self.start_hotkey()

    def start_hotkey(self):
        if self.hk_thread is not None and self.hk_thread.is_alive():
            return self
        self.ok.clear()
        self.error = None
        self.hk_thread = threading.Thread(target=self._hotkey_loop, daemon=True)
        self.hk_thread.start()
        self.ok.wait(5)
        return self

    def stop_hotkey(self):
        """WM_QUIT потоку клавиши: GetMessage вернёт 0, поток снимет клавишу сам
        (UnregisterHotKey без окна зовётся из того же потока, что регистрировал)."""
        if self.hk_thread is not None and self.hk_thread.is_alive() and self.hk_tid:
            u32.PostThreadMessageW(self.hk_tid, 0x0012, 0, 0)          # WM_QUIT
            self.hk_thread.join(3)
        self.close_req.set()                                           # открытый чат не оставлять на рабочем столе
        while not self.questions.empty():                              # вопросы без игры не нужны
            self.questions.get_nowait()

    def _hotkey_loop(self):
        self.hk_tid = k32.GetCurrentThreadId()
        if not u32.RegisterHotKey(None, 1, MOD_NOREPEAT, self.vk):
            self.error = "клавиша занята другой программой (RegisterHotKey отказал)"
            self.ok.set()
            return
        self.ok.set()
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.pressed.set()
        u32.UnregisterHotKey(None, 1)

    def _tk_loop(self):
        import tkinter as tk
        root = tk.Tk()
        root.title(self.title)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        frame = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground="#5a6b7a", cursor="fleur")
        frame.pack(fill="both", expand=True)
        head = tk.Label(frame, text=L.tr("box_history"), fg=MUTED, bg=BG, font=("Segoe UI", 10), cursor="fleur")
        head.pack(anchor="w", padx=10, pady=(8, 0))
        hist = tk.Text(frame, font=("Segoe UI", 11), width=1, height=1, bg=BG, fg=FG, relief="flat", wrap="word",
                       highlightthickness=0, borderwidth=0, cursor="fleur", takefocus=0)
        hist.tag_configure("nestor", foreground=FG)
        hist.tag_configure("you", foreground=YOU)
        hist.tag_configure("time", foreground=MUTED)
        hist.pack(fill="x", padx=10, pady=(2, 6))
        hint = tk.Label(frame, text=L.tr("box_prompt"), fg=YOU, bg=BG, font=("Segoe UI", 10), cursor="fleur")
        hint.pack(anchor="w", padx=10, pady=(2, 2))
        entry = tk.Entry(frame, font=("Segoe UI", 14), width=48, bg="#1d2228", fg=FG,
                         insertbackground=FG, relief="flat")
        entry.pack(padx=10, pady=(0, 10), ipady=4)
        root.withdraw()
        state = {"game": None, "shown": False, "pending": [], "drag": None, "offset": self.load_offset()}

        def render():
            """История в поле над вводом; высота по числу строк с переносом. Отправленное,
            на что мост ещё не ответил, в конце с «…» вместо времени."""
            with self.hist_lock:
                items = list(self.history)
            self.hist_dirty.clear()
            # мост кладёт вопрос в историю вместе с ответом: тогда он больше не «в пути»
            said = {text for _t, who, text in items if who == "ты"}
            state["pending"] = [p for p in state["pending"] if p not in said]
            rows = items + [("…", "ты", p) for p in state["pending"]]
            hist.configure(state="normal")
            hist.delete("1.0", "end")
            lines = 0
            if not rows:
                hist.insert("end", L.tr("box_silent"), "time")
                lines = 1
            for i, (t, who, text) in enumerate(rows[-HISTORY:]):
                if i:
                    hist.insert("end", "\n")
                hist.insert("end", t + "  ", "time")
                if who == "ты":
                    hist.insert("end", L.tr("box_you") + text, "you")
                    lines += math.ceil((len(text) + 10) / WRAP)
                else:
                    hist.insert("end", text, "nestor")
                    lines += math.ceil((len(text) + 6) / WRAP)
            hist.configure(state="disabled", height=max(1, min(16, lines)))

        def game_area():
            """Клиентская область окна игры в координатах экрана: (x, y, ширина, высота) или None."""
            g = state["game"] or game_window()
            if not g:
                return None
            pt = wintypes.POINT(0, 0)
            rc = wintypes.RECT()
            if not (u32.ClientToScreen(g, ctypes.byref(pt)) and u32.GetClientRect(g, ctypes.byref(rc))):
                return None
            return pt.x, pt.y, rc.right, rc.bottom

        def place():
            """От окна игры, а не от экрана: в оконном режиме на втором мониторе чат вставал
            в угол основного экрана, далеко от игры (автор 05.10.2026). По умолчанию левый
            верхний угол игры; оттащили мышью: туда же (сдвиг от угла игры в store)."""
            if state["drag"]:
                return                                            # тащат: не перебивать
            root.update_idletasks()
            area = game_area()
            if area:
                gx, gy, gw, gh = area
                dx, dy = state["offset"] or (int(gw * 0.02), int(gh * 0.03))
                root.geometry("+%d+%d" % (gx + dx, gy + dy))
            else:
                sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
                root.geometry("+%d+%d" % (int(sw * 0.02), int(sh * 0.03)))

        def drag_start(e):
            state["drag"] = (e.x_root - root.winfo_x(), e.y_root - root.winfo_y())
            return "break"

        def drag_move(e):
            if state["drag"]:
                ox, oy = state["drag"]
                root.geometry("+%d+%d" % (e.x_root - ox, e.y_root - oy))
            return "break"

        def drag_end(_e):
            if state["drag"]:
                state["drag"] = None
                area = game_area()
                if area:
                    state["offset"] = (root.winfo_x() - area[0], root.winfo_y() - area[1])
                    self.save_offset(state["offset"])
                entry.focus_force()                               # писать дальше без щелчка в поле
            return "break"

        # тащить мышью за всё, кроме поля ввода (автор: «хочется мышью окно подвинуть к игре»)
        for w in (frame, head, hist, hint):
            w.bind("<ButtonPress-1>", drag_start)
            w.bind("<B1-Motion>", drag_move)
            w.bind("<ButtonRelease-1>", drag_end)

        def send(_e=None):
            text = entry.get().strip()
            entry.delete(0, "end")
            if text:
                self.questions.put(text)
                state["pending"].append(text)
                render()
                place()

        def hide(_e=None):
            entry.delete(0, "end")
            root.withdraw()
            state["shown"] = False
            g = state["game"] or game_window()
            if g:
                u32.SetForegroundWindow(g)

        def show():
            state["game"] = game_window()
            render()
            place()
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            hwnd = u32.GetParent(root.winfo_id()) or root.winfo_id()
            u32.SetForegroundWindow(hwnd)
            entry.focus_force()
            state["shown"] = True

        def poll():
            if self.close_req.is_set():
                self.close_req.clear()
                state["pending"] = []
                if state["shown"]:
                    entry.delete(0, "end")
                    root.withdraw()
                    state["shown"] = False
            if self.pressed.is_set():
                self.pressed.clear()
                if state["shown"]:
                    hide()                                        # F4 второй раз: убрать
                else:
                    show()
            elif state["shown"] and self.hist_dirty.is_set():      # пришла реплика, пока окно открыто
                render()
                place()
            root.after(50, poll)

        entry.bind("<Return>", send)
        entry.bind("<Escape>", hide)
        root.after(50, poll)
        root.mainloop()


if __name__ == "__main__":
    # проба без игры: F4 открывает окно, напечатанное выводится сюда
    box = AskBox().start()
    print("окно на F4", "ОШИБКА: " + box.error if box.error else "готово, жду 60 с")
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            print("вопрос:", box.questions.get(timeout=0.5))
        except queue.Empty:
            pass
