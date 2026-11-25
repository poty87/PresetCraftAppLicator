"""Avvio dell'importazione automatica all'accesso a Windows, con un'attività dell'Utilità di pianificazione.

L'attività gira con l'utente collegato e senza privilegi di amministratore; non ha limite di durata (quello
predefinito di 72 ore fermerebbe il processo in background).
"""

import os
import subprocess
import sys

TASK = "PresetCraftAppLicator - automatic import"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def command():
    """(programma, argomenti) che avviano il processo in background."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--watch"
    # dai sorgenti: pythonw (senza finestra) con lo script principale
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presetcraft.py")
    return (w if os.path.exists(w) else exe), f'"{script}" --watch'


def _powershell(script):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                        script], capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[0] if (r.stderr or r.stdout).strip()
                           else f"powershell {r.returncode}")
    return r.stdout


def user():
    domain, name = os.environ.get("USERDOMAIN"), os.environ.get("USERNAME")
    return f"{domain}\\{name}" if domain else name


def enable(task=TASK, start=True):
    """Crea (o aggiorna) l'attività e, se richiesto, la avvia subito senza aspettare il prossimo accesso."""
    exe, args = command()
    script = f"""
$ErrorActionPreference = 'Stop'
$a = New-ScheduledTaskAction -Execute {_ps_quote(exe)} -Argument {_ps_quote(args)}
$t = New-ScheduledTaskTrigger -AtLogOn -User {_ps_quote(user())}
$t.Delay = 'PT30S'
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
     -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$p = New-ScheduledTaskPrincipal -UserId {_ps_quote(user())} -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName {_ps_quote(task)} -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null
"""
    if start:
        script += f"Start-ScheduledTask -TaskName {_ps_quote(task)}\n"
    _powershell(script)


def disable(task=TASK):
    """Toglie l'attività (se non c'è, non fa niente). Il processo già avviato non si interrompe qui, perché potrebbe
    essere a metà di un'importazione: legge le impostazioni a ogni giro e si chiude da solo."""
    if is_enabled(task):
        r = subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or f"schtasks {r.returncode}")


def is_enabled(task=TASK):
    r = subprocess.run(["schtasks", "/Query", "/TN", task], capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode == 0


# ---- per l'installazione guidata: il processo in background è lo stesso .exe da sostituire

MUTEX = "Local\\PresetCraftAppLicator-watch"  # come in watcher.py
STOP_EVENT = "Local\\PresetCraftAppLicator-watch-stop"


def stop_running(timeout=120):
    """Chiede al processo in background di chiudersi e lo aspetta. True se era in esecuzione."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.OpenMutexW.restype = k32.OpenEventW.restype = ctypes.c_void_p
    mutex = k32.OpenMutexW(0x00100000, False, MUTEX)  # SYNCHRONIZE
    if not mutex:
        return False
    try:
        event = k32.OpenEventW(0x0002, False, STOP_EVENT)  # EVENT_MODIFY_STATE
        if event:
            k32.SetEvent(ctypes.c_void_p(event))
            k32.CloseHandle(ctypes.c_void_p(event))
        # il mutex si libera (abbandonato) quando il processo esce
        if k32.WaitForSingleObject(ctypes.c_void_p(mutex), int(timeout * 1000)) in (0, 0x80):
            k32.ReleaseMutex(ctypes.c_void_p(mutex))
    finally:
        k32.CloseHandle(ctypes.c_void_p(mutex))
    return True


def running():
    """True se il processo in background è in esecuzione (ne possiede il mutex)."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.OpenMutexW.restype = ctypes.c_void_p
    h = k32.OpenMutexW(0x00100000, False, MUTEX)  # SYNCHRONIZE
    if not h:
        return False
    k32.CloseHandle(ctypes.c_void_p(h))
    return True


def ensure_running(task=TASK):
    """All'apertura dell'app: se l'importazione automatica è attiva ma il processo in background non c'è (chiuso
    dall'icona, o fermato da un errore), lo riaccende. True se l'ha riacceso."""
    if running() or not is_enabled(task):
        return False
    subprocess.run(["schtasks", "/Run", "/TN", task], capture_output=True, creationflags=NO_WINDOW)
    return True


def start_now(task=TASK):
    """Riavvia il processo in background dopo un aggiornamento, se l'importazione automatica è attiva."""
    if is_enabled(task):
        subprocess.run(["schtasks", "/Run", "/TN", task], capture_output=True, creationflags=NO_WINDOW)
