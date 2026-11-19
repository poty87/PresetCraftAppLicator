"""Pannello di scelta del motore IA (installarlo o no, modello, cartella, spazio): lo usano l'installazione guidata
e la finestra "Motore IA" dell'app."""

import os
import tkinter as tk
from tkinter import filedialog, ttk

import ai
from i18n import t


class AiChoice(ttk.Frame):
    """Scelte: installare il motore o no, quale modello (quello consigliato per la GPU è il primo), in quale cartella.
    `allow_none`: c'è anche "Non installare" (installazione guidata)."""

    def __init__(self, master, folder=None, model=None, allow_none=True, install=True, wrap=540, describe=None):
        super().__init__(master)
        self.gpu = ai.detect_gpu()
        self.recommended = ai.recommended_model(self.gpu)
        self.describer_name = ai.recommended_describer(self.gpu)
        self.wrap = wrap
        gpu_text = t("ai_gpu", name=self.gpu[0], gb=f"{self.gpu[1] / 1024:.0f}") if self.gpu else t("ai_no_gpu")
        ttk.Label(self, text=gpu_text, style="Muted.TLabel", wraplength=wrap, justify="left").pack(anchor="w")
        self.install = tk.BooleanVar(value=install)
        if allow_none:
            ttk.Radiobutton(self, text=t("ai_opt_none"), variable=self.install, value=False,
                            command=self.refresh).pack(anchor="w", pady=(10, 0))
            ttk.Radiobutton(self, text=t("ai_opt_install"), variable=self.install, value=True,
                            command=self.refresh).pack(anchor="w", pady=(4, 0))
        self.box = ttk.Frame(self)
        self.box.pack(fill="x", padx=(24 if allow_none else 0, 0), pady=(8, 0))
        row = ttk.Frame(self.box)
        row.pack(fill="x")
        ttk.Label(row, text=t("ai_model"), style="Body.TLabel").pack(side="left")
        self.models = list(ai.MODELS)
        self.combo = ttk.Combobox(row, state="readonly", width=60,
                                  values=[self.model_label(m) for m in self.models])
        self.combo.current(self.models.index(model if model in ai.MODELS else self.recommended))
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.combo.pack(side="left", padx=(10, 0))
        row = ttk.Frame(self.box)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=t("ai_folder"), style="Body.TLabel").pack(side="left")
        self.folder = tk.StringVar(value=folder or ai.DEFAULT_DIR)
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=(10, 8))
        ttk.Button(row, text=t("btn_browse"), command=self.browse).pack(side="left")
        # descrizioni delle foto (facoltative): solo con una GPU NVIDIA abbastanza grande
        self.describe = tk.BooleanVar(value=bool(self.describer_name) if describe is None else describe)
        if self.describer_name:
            ttk.Checkbutton(self.box, text=t("ai_desc_opt", gb=f"{ai.DESCRIBERS[self.describer_name]['download']:.0f}"),
                            variable=self.describe, command=self.refresh).pack(anchor="w", pady=(10, 0))
            ttk.Label(self.box, text=t("ai_desc_hint"), style="Muted.TLabel", wraplength=wrap, justify="left").pack(
                anchor="w", padx=(24, 0))
        else:
            ttk.Label(self.box, text=t("ai_desc_none"), style="Muted.TLabel", wraplength=wrap, justify="left").pack(
                anchor="w", pady=(10, 0))
        self.space = ttk.Label(self.box, style="Muted.TLabel", wraplength=wrap, justify="left")
        self.space.pack(anchor="w", pady=(6, 0))
        self.note = ttk.Label(self.box, text=t("ai_note"), style="Muted.TLabel", wraplength=wrap, justify="left")
        self.note.pack(anchor="w", pady=(8, 0))
        self.folder.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def model_label(self, m):
        name = {"metaclip2-giant": t("ai_m_giant"), "metaclip2-huge": t("ai_m_huge"),
                "metaclip2-b16": t("ai_m_small")}[m]
        return name + ("  ·  " + t("ai_recommended") if m == self.recommended else "")

    def model(self):
        return self.models[self.combo.current()]

    def browse(self):
        start = self.folder.get() if os.path.isdir(os.path.dirname(self.folder.get())) else None
        path = filedialog.askdirectory(parent=self, title=t("ai_folder"), initialdir=start)
        if path:
            path = os.path.normpath(path)
            if os.path.basename(path).lower() not in ("ia", "ai") and os.listdir(path):
                path = os.path.join(path, "PresetCraftAppLicator IA")  # mai i file del motore sparsi in una cartella piena
            self.folder.set(path)

    def describer(self):
        return self.describer_name if self.describer_name and self.describe.get() else None

    def needed(self):
        return ai.space_needed_gb(self.model(), self.gpu, self.describer())

    def enough_space(self):
        return ai.free_space_gb(self.folder.get()) >= self.needed()

    def refresh(self):
        on = bool(self.install.get())
        for w in self.box.winfo_children():
            for c in (w.winfo_children() or [w]):
                try:
                    c.state(["!disabled"] if on else ["disabled"])
                except (tk.TclError, AttributeError):
                    pass
        self.combo.state(["!disabled", "readonly"] if on else ["disabled"])
        free = ai.free_space_gb(self.folder.get())
        text = t("ai_space", need=f"{self.needed():.0f}", free=f"{free:.0f}")
        if on and free < self.needed():
            text += "  " + t("ai_space_low")
        self.space.configure(text=text)

    def valid_folder(self):
        f = self.folder.get().strip()
        return bool(f) and os.path.isabs(f)
