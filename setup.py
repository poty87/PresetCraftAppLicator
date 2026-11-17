"""Build dell'MSI di PresetCraftAppLicator con cx_Freeze (l'installazione guidata lo incorpora: vedi build.ps1).

    .venv\\Scripts\\python.exe setup.py bdist_msi

L'MSI contiene Python, Tk, sv_ttk e Pillow: sul PC di destinazione non serve installare altro.
Si installa per l'utente corrente (niente permessi di amministratore), di default in
%LOCALAPPDATA%\\Programs\\PresetCraftAppLicator. Proprietà utili per un'installazione senza interfaccia:
    msiexec /i PresetCraftAppLicator-x.y.z.msi /qn PCA_LANGUAGE=en PCA_DESKTOP=0 TARGETDIR="D:\\Apps\\PresetCraftAppLicator"

Aggiornamenti: tutte le versioni condividono UPGRADE_CODE. Un MSI più nuovo toglie quello installato
(anche se ha la stessa versione); uno più vecchio si rifiuta di partire invece di declassare l'app.
"""

import sys
from pathlib import Path

from cx_Freeze import Executable, setup
from cx_Freeze.command.bdist_msi import bdist_msi

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
from translations import LANGUAGES  # noqa: E402
from version import APP_NAME, EXE, REG_KEY, UPGRADE_CODE, VERSION  # noqa: E402

ICON = str(ROOT / "assets" / "icon.ico")
DESCRIPTION = "Gives LightCraft photos the camera profile of the style they were shot with"
DESKTOP_COMPONENT_GUID = "{14CF8F66-FF6E-4CF3-AAA0-2CDC2BA7D7B3}"  # fisso: identifica il collegamento tra le versioni
ARP_COMPONENT_GUID = "{6B0E2D51-3C7A-4F0B-9E62-8A1D5C47F3B9}"  # fisso: la voce di "App installate" della guida
ARP_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
APP_FILE = EXE  # chiave dell'eseguibile nella tabella File (cx_Freeze usa il nome del file)

shortcuts = [
    # (Shortcut, Directory_, Name, Component_, Target, Arguments, Description, Hotkey, Icon_, IconIndex, ShowCmd, WkDir)
    ("StartMenuShortcut", "ProgramMenuFolder", f"PRESETCR|{APP_NAME}", "TARGETDIR", f"[TARGETDIR]{EXE}", None,
     DESCRIPTION, None, None, None, None, "TARGETDIR"),
    ("DesktopShortcut", "DesktopFolder", f"PRESETCR|{APP_NAME}", "DesktopShortcutComp", f"[TARGETDIR]{EXE}", None,
     DESCRIPTION, None, None, None, None, "TARGETDIR"),
]


def run_sql(db, sql):
    view = db.OpenView(sql)
    view.Execute(None)
    view.Close()


class AppMsi(bdist_msi):
    """L'MSI di cx_Freeze con: pagina per la lingua, collegamento sul Desktop facoltativo, niente declassamenti."""

    def add_config(self):
        super().add_config()
        from msilib import add_data

        db = self.db
        add_data(db, "Property", [("PCA_LANGUAGE", "auto"), ("PCA_DESKTOP", "1"), ("PCA_WIZARD", "0"),
                                  ("ARPNOMODIFY", "1"), ("ARPNOREPAIR", "1")])
        run_sql(db, "UPDATE `Property` SET `Value`='TARGETDIR;REINSTALLMODE;PCA_LANGUAGE;PCA_DESKTOP;PCA_WIZARD;"
                    "REMOVEOLDVERSION;NEWERVERSIONDETECTED' WHERE `Property`='SecureCustomProperties'")

        # Installata dall'installazione guidata (PCA_WIZARD=1): la voce di Windows Installer in Impostazioni › App
        # resta nascosta e al suo posto c'è questa, che apre la disinstallazione guidata. La voce è un componente
        # dell'MSI, quindi sparisce con la disinstallazione in ogni caso.
        add_data(db, "Component", [("WizardArpComp", ARP_COMPONENT_GUID, "TARGETDIR", 4, 'PCA_WIZARD="1"', "ArpName")])
        add_data(db, "FeatureComponents", [("default", "WizardArpComp")])
        add_data(db, "Registry", [
            ("ArpName", 1, ARP_KEY, "DisplayName", APP_NAME, "WizardArpComp"),
            ("ArpVersion", 1, ARP_KEY, "DisplayVersion", "[ProductVersion]", "WizardArpComp"),
            ("ArpPublisher", 1, ARP_KEY, "Publisher", "poty87", "WizardArpComp"),
            ("ArpIcon", 1, ARP_KEY, "DisplayIcon", f"[#{APP_FILE}]", "WizardArpComp"),
            ("ArpLocation", 1, ARP_KEY, "InstallLocation", "[TARGETDIR]", "WizardArpComp"),
            ("ArpUninstall", 1, ARP_KEY, "UninstallString", f'"[#{APP_FILE}]" --uninstall', "WizardArpComp"),
            ("ArpUrl", 1, ARP_KEY, "URLInfoAbout", f"https://github.com/poty87/{APP_NAME}", "WizardArpComp"),
            ("ArpNoModify", 1, ARP_KEY, "NoModify", "#1", "WizardArpComp"),
            ("ArpNoRepair", 1, ARP_KEY, "NoRepair", "#1", "WizardArpComp"),
        ])
        # Importazione automatica (processo in background + attività pianificata), comunque si disinstalli:
        # - disinstallazione vera: il processo si ferma e l'attività pianificata viene tolta;
        # - aggiornamento (la versione vecchia viene rimossa prima): il processo si ferma soltanto…
        # - …e la versione nuova, finita l'installazione, lo fa ripartire se l'attività c'è.
        # 18 = eseguibile installato dal pacchetto; +1024 = differita (prima di RemoveFiles); +64 = esito ignorato,
        # +128 = senza aspettarlo
        add_data(db, "CustomAction", [
            ("A_HIDE_ARP", 51, "ARPSYSTEMCOMPONENT", "1"),
            ("A_WATCH_STOP", 18 + 1024 + 64, APP_FILE, "--msi-stop"),
            ("A_WATCH_REMOVE", 18 + 1024 + 64, APP_FILE, "--msi-remove"),
            ("A_WATCH_START", 18 + 64 + 128, APP_FILE, "--msi-start"),
        ])
        add_data(db, "InstallExecuteSequence", [
            ("A_HIDE_ARP", 'PCA_WIZARD="1"', 1002),
            ("A_WATCH_STOP", 'REMOVE~="ALL" AND UPGRADINGPRODUCTCODE', 1510),
            ("A_WATCH_REMOVE", 'REMOVE~="ALL" AND NOT UPGRADINGPRODUCTCODE', 1511),
            ("A_WATCH_START", 'NOT REMOVE~="ALL"', 6650),
        ])
        # lingua scelta: la legge l'app finché l'utente non ne sceglie un'altra nelle Impostazioni
        choices = [("auto", "Automatic (Windows language)")] + list(LANGUAGES.items())
        add_data(db, "ComboBox", [("PCA_LANGUAGE", i + 1, code, label) for i, (code, label) in enumerate(choices)])
        add_data(db, "Registry", [
            ("RegLanguage", 1, REG_KEY, "Language", "[PCA_LANGUAGE]", "TARGETDIR"),
            ("RegDesktop", 1, REG_KEY, "DesktopShortcut", "#1", "DesktopShortcutComp"),
        ])
        # collegamento sul Desktop in un componente a parte, installato solo se PCA_DESKTOP=1
        add_data(db, "Component", [("DesktopShortcutComp", DESKTOP_COMPONENT_GUID, "TARGETDIR", 4,
                                    'PCA_DESKTOP="1"', "RegDesktop")])
        add_data(db, "FeatureComponents", [("default", "DesktopShortcutComp")])
        # la cartella di installazione finisce in Impostazioni › App e serve all'installazione guidata per gli aggiornamenti
        add_data(db, "CustomAction", [("A_SET_ARP_LOCATION", 51, "ARPINSTALLLOCATION", "[TARGETDIR]")])
        add_data(db, "InstallExecuteSequence", [("A_SET_ARP_LOCATION", None, 1001)])
        add_data(db, "InstallUISequence", [("LanguageDlg", "not Installed", 1235)])
        add_data(db, "LaunchCondition", [("NOT NEWERVERSIONDETECTED OR Installed",
                                          f"A newer version of {APP_NAME} is already installed.")])
        # le versioni installate vanno cercate prima delle LaunchConditions (100), che altrimenti non vedrebbero
        # NEWERVERSIONDETECTED: nelle tabelle standard di msilib FindRelatedProducts sta a 200
        for table in ("InstallExecuteSequence", "InstallUISequence"):
            run_sql(db, f"UPDATE `{table}` SET `Sequence`=25 WHERE `Action`='FindRelatedProducts'")

    def add_upgrade_config(self, sversion):
        from msilib import add_data

        add_data(self.db, "Upgrade", [
            # versioni più vecchie e stessa versione (un'altra build): vengono rimosse prima di installare
            (self.upgrade_code, None, sversion, None, 512 | 1, None, "REMOVEOLDVERSION"),
            # versioni più nuove: solo rilevate, e la LaunchCondition ferma l'installazione
            (self.upgrade_code, sversion, None, None, 2, None, "NEWERVERSIONDETECTED"),
        ])

    def add_ui(self):
        super().add_ui()
        from cx_Freeze.command._pydialog import PyDialog

        dialog = PyDialog(self.db, "LanguageDlg", x=self.x, y=self.y, w=self.width, h=self.height, attr=self.modal,
                          title=self.title, first="LangCombo", default="Next", cancel="Cancel")
        dialog.title("Language · Lingua · Sprache · Idioma · Langue")
        dialog.text("Hint", 15, 60, 330, 50, 3,
                    f"Choose the language of {APP_NAME}. You can change it later in Settings.\r\n\r\n"
                    f"Scegli la lingua di {APP_NAME}: potrai cambiarla in seguito dalle Impostazioni.")
        # 0x20000 = ComboList: si sceglie solo dall'elenco
        dialog.control("LangCombo", "ComboBox", 15, 120, 220, 16, 3 | 0x20000, "PCA_LANGUAGE", None, "Next", None)
        # i controlli col Tab devono formare un giro chiuso (errore 2810): "Indietro" è disattivo e ne resta fuori
        dialog.backbutton("< Back", None, active=False)
        button = dialog.nextbutton("Next >", "Cancel")
        button.event("EndDialog", "Return")
        button = dialog.cancelbutton("Cancel", "LangCombo")
        button.event("SpawnDialog", "CancelDlg")


setup(
    name=APP_NAME,
    version=VERSION,
    description=DESCRIPTION,
    author="poty87",
    url=f"https://github.com/poty87/{APP_NAME}",
    cmdclass={"bdist_msi": AppMsi},
    options={
        "build_exe": {
            # pystray (icona nell'area di notifica) sceglie il suo modulo per Windows a runtime: va incluso tutto
            "packages": ["sv_ttk", "tkinter", "PIL", "pystray", "six"],
            # pystray importa queue attraverso six.moves, che cx_Freeze non vede
            "includes": ["queue"],
            # profiles: le LUT misurate (tools/measure_styles.py), una cartella per fotocamera
            "include_files": [(ICON, "assets/icon.ico"), (str(ROOT / "assets" / "icon.png"), "assets/icon.png"),
                              (str(ROOT / "assets" / "profiles"), "assets/profiles")],
            # http ed email servono a urllib per il controllo degli aggiornamenti
            "excludes": ["unittest", "pydoc", "xmlrpc"],
            # nei .pyc resta il percorso dei sorgenti: si toglie, così nel pacchetto non finiscono le cartelle del PC
            # su cui si compila
            "replace_paths": [("*", ""), (str(ROOT / "src"), ""), (str(ROOT), "")],
            "include_msvcr": True,
        },
        "bdist_msi": {
            "upgrade_code": UPGRADE_CODE,
            "all_users": False,
            "initial_target_dir": rf"[LocalAppDataFolder]Programs\{APP_NAME}",
            "install_icon": ICON,
            "add_to_path": False,
            "output_name": f"{APP_NAME}-{VERSION}.msi",
            "data": {"Shortcut": shortcuts},
            "summary_data": {"author": "poty87", "comments": f"{APP_NAME} installer"},
        },
    },
    # percorso relativo (build.ps1 parte dalla cartella del progetto): cx_Freeze lo usa nel nome di un componente
    # dell'MSI, e un percorso assoluto ci metterebbe dentro le cartelle del PC su cui si compila
    executables=[Executable("src/presetcraft.py", base="gui", target_name=EXE, icon=ICON)],
)
