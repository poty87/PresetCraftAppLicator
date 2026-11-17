"""Aspetto comune di app e installazione guidata: tema scuro, caratteri, finestre di dialogo."""

import ctypes
import os
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

import sv_ttk

from i18n import t

# colori del banner di stato e delle icone delle finestre: (sfondo, accento)
LEVELS = {
    "info": ("#1d2633", "#60a5fa"),
    "ok": ("#16261d", "#4ade80"),
    "warn": ("#2c2416", "#fbbf24"),
    "error": ("#2d1a1c", "#f87171"),
}
GLYPHS = {"info": "i", "ok": "✓", "warn": "!", "error": "✕"}
MUTED = "#9aa0a6"
TEXT = "#e8eaed"
ACCENT = "#60a5fa"


def resource(*parts):
    """Percorso di un file distribuito con il programma (sorgente, cx_Freeze o PyInstaller)."""
    if hasattr(sys, "_MEIPASS"):  # PyInstaller, file unico
        base = sys._MEIPASS
    elif getattr(sys, "frozen", False):  # cx_Freeze
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def dark_title_bar(window):
    """Barra del titolo scura su Windows 10/11."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (nuovo e vecchio numero)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError):
        pass


def set_icon(window):
    try:
        window.iconbitmap(resource("assets", "icon.ico"))
    except tk.TclError:
        pass


def setup_theme(root):
    """Tema scuro, caratteri e stili condivisi; i caratteri restano come attributi di root (f_title, f_body…)."""
    sv_ttk.set_theme("dark")
    family = "Segoe UI Variable Text" if "Segoe UI Variable Text" in tkfont.families() else "Segoe UI"
    root.f_title = tkfont.Font(family=family, size=18, weight="bold")
    root.f_body = tkfont.Font(family=family, size=10)
    root.f_small = tkfont.Font(family=family, size=9)
    root.f_strong = tkfont.Font(family=family, size=11, weight="bold")
    root.f_stat = tkfont.Font(family=family, size=20, weight="bold")
    root.f_link = tkfont.Font(family=family, size=9, underline=True)
    st = ttk.Style(root)
    st.configure("Muted.TLabel", foreground=MUTED, font=root.f_small)
    st.configure("Title.TLabel", font=root.f_title)
    st.configure("Strong.TLabel", font=root.f_strong)
    st.configure("Body.TLabel", font=root.f_body)
    st.configure("Stat.TLabel", font=root.f_stat)
    st.configure("Treeview", rowheight=30, font=root.f_body)
    # margine interno: senza, i titoli partono dal primo pixel della colonna
    st.configure("Treeview.Heading", font=root.f_small, padding=(10, 5, 8, 5))
    st.configure("Link.TLabel", foreground=ACCENT, font=root.f_link)
    # l'elenco che si apre dai menu a tendina è un Listbox Tk: va allineato a mano ai caratteri dell'app
    root.option_add("*TCombobox*Listbox.font", root.f_body)


class Scrollbar(tk.Canvas):
    """Barra di scorrimento ben visibile sul tema scuro (quella di sv_ttk è grigio scuro su scuro).

    Si usa come ttk.Scrollbar: command=widget.yview / xview, e il widget le passa set(first, last).
    Il cursore si trascina, un clic sul binario scorre di una pagina; autohide la nasconde quando tutto è visibile.
    """

    TRACK, THUMB, HOVER, DRAG = "#2b2b2b", "#878c91", "#a6abaf", "#c3c7cb"
    SIZE = 14

    def __init__(self, master, orient="vertical", command=None, autohide=False):
        vertical = orient == "vertical"
        super().__init__(master, highlightthickness=0, bd=0, bg=self.TRACK, cursor="arrow",
                         width=self.SIZE if vertical else 1, height=1 if vertical else self.SIZE)
        self.vertical, self.command, self.autohide = vertical, command, autohide
        self.first, self.last = 0.0, 1.0
        self.hover = self.dragging = False
        self.grab_offset = 0
        self.shown = True
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<MouseWheel>", self._wheel)

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        if self.autohide:
            whole = self.first <= 0 and self.last >= 1
            if whole and self.shown:
                self.grid_remove()
                self.shown = False
            elif not whole and not self.shown:
                self.grid()
                self.shown = True
        self.redraw()

    def _length(self):
        return max(1, self.winfo_height() if self.vertical else self.winfo_width())

    def _thumb(self):
        length = self._length()
        start, end = self.first * length, self.last * length
        if end - start < 28:  # cursore mai troppo piccolo da afferrare
            middle = (start + end) / 2
            start, end = max(0, middle - 14), min(length, middle + 14)
        return start, end

    def redraw(self):
        self.delete("all")
        if self.first <= 0 and self.last >= 1 and not self.autohide:
            color = "#3a3a3a"  # niente da scorrere: cursore discreto ma presente
        else:
            color = self.DRAG if self.dragging else self.HOVER if self.hover else self.THUMB
        start, end = self._thumb()
        pad, w = 3, self.SIZE - 6
        if self.vertical:
            self.create_line(self.SIZE / 2, start + pad + w / 2, self.SIZE / 2, end - pad - w / 2, width=w,
                             fill=color, capstyle="round")
        else:
            self.create_line(start + pad + w / 2, self.SIZE / 2, end - pad - w / 2, self.SIZE / 2, width=w,
                             fill=color, capstyle="round")

    def _pos(self, event):
        return event.y if self.vertical else event.x

    def _hover(self, on):
        self.hover = on
        self.redraw()

    def _press(self, event):
        if not self.command:
            return
        start, end = self._thumb()
        pos = self._pos(event)
        if start <= pos <= end:
            self.dragging = True
            self.grab_offset = pos - start
            self.redraw()
        else:
            self.command("scroll", -1 if pos < start else 1, "pages")

    def _drag(self, event):
        if self.dragging and self.command:
            self.command("moveto", max(0.0, (self._pos(event) - self.grab_offset) / self._length()))

    def _release(self, _event):
        self.dragging = False
        self.redraw()

    def _wheel(self, event):
        if self.command:
            self.command("scroll", -3 if event.delta > 0 else 3, "units")


def attach_scrollbars(tree):
    """Barra verticale sempre visibile e orizzontale solo se le colonne non ci stanno; il tree va creato in un
    contenitore tutto suo, perché qui si dispone con grid."""
    parent = tree.master
    vsb = Scrollbar(parent, "vertical", tree.yview)
    hsb = Scrollbar(parent, "horizontal", tree.xview, autohide=True)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns", padx=(4, 0))
    hsb.grid(row=1, column=0, sticky="ew", pady=(4, 0))
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    return vsb, hsb


def center_on(window, master):
    window.update_idletasks()
    x = master.winfo_rootx() + (master.winfo_width() - window.winfo_reqwidth()) // 2
    y = master.winfo_rooty() + (master.winfo_height() - window.winfo_reqheight()) // 3
    window.geometry(f"+{max(0, x)}+{max(0, y)}")


class Dialog(tk.Toplevel):
    """Finestra modale con il tema dell'app, al posto delle messagebox di sistema.

    buttons: [(chiave del risultato, testo, principale?)]; Esc e la chiusura restituiscono None.
    """

    def __init__(self, master, title, heading, body, level="info", buttons=None, detail=None, default=None):
        super().__init__(master)
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        set_icon(self)
        self.result = None
        root = master._root()  # la finestra principale ha i caratteri; si centra su chi apre la finestra
        frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        frame.pack(fill="both", expand=True)

        bg, accent = LEVELS[level]
        top = ttk.Frame(frame)
        top.pack(fill="x")
        badge = tk.Canvas(top, width=40, height=40, highlightthickness=0, bd=0,
                          bg=ttk.Style(self).lookup("TFrame", "background") or "#1c1c1c")
        badge.create_oval(2, 2, 38, 38, fill=bg, outline=accent, width=2)
        badge.create_text(20, 20, text=GLYPHS[level], fill=accent, font=root.f_strong)
        badge.pack(side="left", anchor="n")
        texts = ttk.Frame(top)
        texts.pack(side="left", fill="x", expand=True, padx=(14, 0))
        ttk.Label(texts, text=heading, style="Strong.TLabel", wraplength=440, justify="left").pack(anchor="w")
        if body:
            ttk.Label(texts, text=body, wraplength=440, justify="left", style="Body.TLabel").pack(anchor="w", pady=(6, 0))
        if detail:
            lines = sum(max(1, -(-len(line) // 58)) for line in detail.split("\n"))  # righe a capo comprese
            box = tk.Text(texts, height=min(10, lines), width=58, wrap="char", relief="flat",
                          bg="#141414", fg=TEXT, insertbackground=TEXT, font=("Consolas", 9), padx=8, pady=6)
            box.insert("1.0", detail)
            box.configure(state="disabled")
            box.pack(anchor="w", pady=(10, 0), fill="x")

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(20, 0))
        buttons = buttons or [("ok", t("btn_ok"), True)]
        focus = None
        for key, text, primary in reversed(buttons):
            b = ttk.Button(row, text=text, style="Accent.TButton" if primary else "TButton",
                           command=lambda k=key: self.close(k))
            b.pack(side="right", padx=(8, 0))
            b.bind("<Return>", lambda e, k=key: self.close(k))
            if key == (default or buttons[-1][0]):
                focus = b
        self.bind("<Escape>", lambda e: self.close(None))
        self.protocol("WM_DELETE_WINDOW", lambda: self.close(None))

        center_on(self, master)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()
        (focus or self).focus_set()

    def close(self, result):
        self.result = result
        self.grab_release()
        parent = self.master
        self.destroy()
        if isinstance(parent, tk.Toplevel) and parent.winfo_exists():
            parent.grab_set()  # torna modale la finestra da cui era stata aperta (es. Impostazioni)

    @classmethod
    def ask(cls, master, *args, **kwargs):
        d = cls(master, *args, **kwargs)
        master.wait_window(d)
        return d.result
