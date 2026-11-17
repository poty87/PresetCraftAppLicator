"""Scrive src/translations.py: i testi comuni presi da CleanCraftNas più quelli propri (tools/new_texts.py).

    python tools/make_translations.py [cartella di CleanCraftNas]
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(ROOT), "CleanCraftNas")

# chiavi di CleanCraftNas che servono anche qui (finestre comuni, impostazioni, aggiornamenti, installazione)
SHARED_PREFIXES = ("w_", "upd_", "backups_", "backup_", "btn_", "lang_", "cli_")
SHARED = {"library", "no_library", "no_library_hint", "add_library", "remove_library", "remove_library_head",
          "remove_library_body", "refresh", "settings", "col_file", "col_folder", "col_camera", "col_size",
          "col_date", "columns_btn", "col_move_left", "col_move_right", "columns_reset", "col_status",
          "preview_hint", "preview_loading", "preview_none", "taken", "close_hint", "open_log", "st_choose",
          "st_lib_gone", "st_read_error", "st_open_app", "st_open_lock", "not_library", "pick_library",
          "error_title", "about", "menu_explorer", "done_backup", "updates_label"}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    old = load(os.path.join(SOURCE, "src", "translations.py"), "old_translations")
    new = load(os.path.join(ROOT, "tools", "new_texts.py"), "new_texts").NEW
    texts = {}
    for key, value in old.TEXTS.items():
        if key in SHARED or key.startswith(SHARED_PREFIXES):
            texts[key] = {lang: s.replace("CleanCraftNas", "PresetCraftAppLicator").replace("Lightcraft", "LightCraft")
                          for lang, s in value.items()}
    texts.update(new)
    langs = list(old.LANGUAGES)
    missing = [(k, lang) for k, v in texts.items() for lang in langs if not v.get(lang)]
    if missing:
        raise SystemExit(f"missing translations: {missing}")
    lines = ['"""Testi dell\'interfaccia di PresetCraftAppLicator nelle lingue supportate.',
             "",
             "File generato da tools/make_translations.py: i testi nuovi vanno in tools/new_texts.py.",
             '"""', "", "LANGUAGES = {"]
    lines += [f"    {code!r}: {name!r}," for code, name in old.LANGUAGES.items()]
    lines += ["}", "", f"DATE_FORMATS = {old.DATE_FORMATS!r}", "", "TEXTS = {"]
    for key in sorted(texts):
        lines.append(f"    {key!r}: {{")
        lines += [f"        {lang!r}: {texts[key][lang]!r}," for lang in langs]
        lines.append("    },")
    lines.append("}")
    with open(os.path.join(ROOT, "src", "translations.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{len(texts)} texts")


if __name__ == "__main__":
    main()
