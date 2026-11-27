"""Dati del prodotto condivisi da app, installazione guidata e build."""

APP_NAME = "PresetCraftAppLicator"
VERSION = "1.34.3"
REPO = "poty87/PresetCraftAppLicator"
EXE = APP_NAME + ".exe"
# non cambiarlo mai: è ciò che permette a una nuova versione di sostituire quella installata
UPGRADE_CODE = "{C944A313-A353-4C19-82E8-4DB2EC7ACE2D}"
REG_KEY = r"Software\PresetCraftAppLicator"  # scelte fatte durante l'installazione (lingua, collegamento sul Desktop)


def parse_version(text):
    """"v1.10.2" → (1, 10, 2); parti non numeriche contano 0."""
    parts = []
    for p in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))
