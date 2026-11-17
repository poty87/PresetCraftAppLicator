"""Lingua corrente dell'interfaccia e traduzione dei testi."""

import ctypes
import winreg

from translations import LANGUAGES, TEXTS
from version import REG_KEY

LANG = "en"


def t(key, **values):
    text = TEXTS[key].get(LANG) or TEXTS[key]["en"]
    return text.format(**values) if values else text


def set_language(code):
    global LANG
    LANG = resolve_language(code)
    return LANG


def windows_language():
    """Lingua dell'interfaccia di Windows, se è tra quelle supportate."""
    primary = {0x10: "it", 0x09: "en", 0x07: "de", 0x0A: "es", 0x0C: "fr", 0x16: "pt"}
    try:
        return primary.get(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF, "en")
    except (AttributeError, OSError):
        return "en"


def resolve_language(setting):
    """"auto", None o una lingua non supportata diventano la lingua di Windows."""
    return setting if setting in LANGUAGES else windows_language()


def registry_value(name):
    """Valore scritto dall'installazione in HKCU\\Software\\<APP_NAME>, oppure None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
