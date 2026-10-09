"""
Watchdog for the CryptoOracle backend — restarts it ONLY when it has crashed.

Why this exists
---------------
The process chain is Windows -> python main.py -> FastAPI -> APScheduler ->
scan loop. Nothing watches it. Five silent crashes between 08-27 and 09-14,
none with a traceback — the log simply stops mid-scan. Cost measured on
2026-09-15: 47.7 lost session hours over three weeks, 83.3% uptime. The worst
single one (Mon 09-14 12:14 -> Tue 09-15 03:47) went unnoticed for 15.6 hours.

This does NOT make money. At the current expectancy more uptime means faster
bleeding. Its value is that a bot which dies mid-sample makes every
measurement untrustworthy: the 09-11 trail changes had 7 trades of evidence
because the sample kept fragmenting.

The safety rule
---------------
Two failure modes are acted on, and nothing else:

    situation                          meaning                  action
    ---------------------------------  -----------------------  ---------
    (port dead, no answer)             process crashed          RESTART
    running, last_scan > 15 min old    scan loop stalled        KILL+RESTART
    "Manual stop"                      user pressed stop        leave
    "Daily loss limit hit: ..."        safety halt              leave
    "SURVIVAL drawdown halt: ..."      equity protection        leave
    "Daily trade limit hit: ..."       cap reached              leave
    running, scanning normally         healthy                  leave
    port open, /status silent          ambiguous                leave

A watchdog that fights the drawdown halt and puts the bot back to work
mid-bleed is worse than no watchdog. So if the server answers at all, this
exits without touching anything — even if the bot is idle.

Kill switch: create a file named `watchdog.disabled` next to this script and
it stops acting. No code change, no scheduler edit.

Usage
-----
    venv\\Scripts\\python.exe watchdog.py            # check once, act if dead
    venv\\Scripts\\python.exe watchdog.py --dry-run  # report only, never act
    venv\\Scripts\\python.exe watchdog.py --status   # what would it do now?

Exit codes: 0 = healthy or intentionally stopped, 2 = restarted, 3 = restart
failed, 4 = disabled by kill switch.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8000
API = f"http://127.0.0.1:{PORT}"
PYTHON = os.path.join(HERE, "venv", "Scripts", "python.exe")
MAIN = os.path.join(HERE, "main.py")
SETTINGS = os.path.join(HERE, "features", "trading", "scalp_settings_saved.json")
DISABLE_FILE = os.path.join(HERE, "watchdog.disabled")
LOG = os.path.join(HERE, "watchdog.log")
STATE = os.path.join(HERE, "watchdog_state.json")
# Chemins complets : le PATH système de ce PC a perdu C:\Windows\System32
# (constaté le 2026-09-25) — "netstat"/"taskkill" seuls y sont introuvables.
_SYS32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
NETSTAT = os.path.join(_SYS32, "netstat.exe")
TASKKILL = os.path.join(_SYS32, "taskkill.exe")

# Reasons that mean "someone decided this on purpose" — never override them.
INTENTIONAL = ("manual stop", "daily loss limit", "survival drawdown halt",
               "daily trade limit")

BOOT_WAIT = 45        # seconds to wait for uvicorn to bind the port
POLL = 3

# ── Détection de BLOCAGE (stall) — ajouté le 2026-09-17 ─────────────────────
# Deuxième mode de panne, observé le 2026-09-16 07:57 -> 11:29 : le backend
# reste VIVANT et le port répond, mais la boucle de scan s'arrête. Trace dans
# le .err :
#     Run time of job "auto_scan (... next run at 2026-09-16 11:30:13)"
#     was missed by 0:02:03
#
# 3h30 sans aucun scan, et le watchdog d'origine n'aurait rien vu : sa seule
# règle était "le port répond -> ne touche à rien". Il couvrait les crashs
# (09-14, 09-16 13:05) mais pas les blocages.
#
# last_scan avance toutes les 3 min MÊME en zone morte et le week-end (la
# boucle tourne, ce sont les symboles qui sont sautés), donc un last_scan
# figé est un signal non ambigu : il ne peut pas venir d'une session fermée.
STALL_MINUTES = 15        # 5 cycles de scan manqués (le scan tourne toutes les 3 min)
BOOT_GRACE_MINUTES = 6    # un backend qui vient de démarrer n'a pas encore scanné
STALL_COOLDOWN_MINUTES = 25   # anti-boucle : un redémarrage sur blocage TUE un
                              # process vivant, donc jamais deux coup sur coup


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass          # logging must never be the thing that breaks the restart


def _age_minutes(stamp):
    """Minutes since an ISO timestamp from /status, or None if unusable."""
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def diagnose_stall(st):
    """Is the scan loop stuck? Returns (stalled: bool, explanation: str).

    Only meaningful when the bot claims to be RUNNING — a stopped bot is not
    expected to scan, and restarting it would override a deliberate stop.
    """
    if not st.get("running"):
        return False, "not running"
    scan_age = _age_minutes(st.get("last_scan"))
    if scan_age is None:
        # Never scanned this session. Normal for a few minutes after boot;
        # a problem if the session has been up a while.
        sess_age = _age_minutes(st.get("session_start"))
        if sess_age is not None and sess_age > BOOT_GRACE_MINUTES:
            return True, (f"no scan recorded and the session has been up "
                          f"{sess_age:.0f} min")
        return False, "just started, no scan yet"
    if scan_age > STALL_MINUTES:
        return True, f"last scan was {scan_age:.0f} min ago (limit {STALL_MINUTES})"
    return False, f"last scan {scan_age:.1f} min ago"


def _read_state():
    try:
        with open(STATE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(d):
    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
    except OSError:
        pass


def pid_on_port(port=PORT):
    """PID listening on `port`, via netstat (psutil is not installed here)."""
    # PAS de text=True : netstat écrit dans la page de code OEM (850/437 ici),
    # que le décodeur locale de Python (cp1252) refuse — il lève
    # UnicodeDecodeError DANS un thread de lecture, subprocess renvoie alors
    # stdout=None et l'appelant explose sur None.splitlines(). On lit donc des
    # octets et on décode nous-mêmes en tolérant les caractères invalides :
    # seuls les chiffres du port et du PID nous intéressent.
    try:
        raw = subprocess.run([NETSTAT, "-ano", "-p", "TCP"],
                             capture_output=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if not raw:
        return None
    out = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING":
            local = parts[1]
            if local.rsplit(":", 1)[-1] == str(port):
                try:
                    return int(parts[4])
                except ValueError:
                    continue
    return None


def kill_backend():
    """Kill whatever holds the port, so a stalled instance can be replaced.

    Open positions are NOT at risk from this: the stop loss lives on the
    broker's server, not in this process. What is lost is trail management for
    the ~20 s of the restart — which is strictly better than the hours of no
    management that a stall already causes.
    """
    pid = pid_on_port()
    if pid is None:
        log("could not identify the process on the port - not killing anything")
        return False
    log(f"killing stalled backend pid {pid}")
    try:
        subprocess.run([TASKKILL, "/F", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"taskkill failed: {e}")
        return False
    for _ in range(10):
        time.sleep(1)
        if not port_is_open():
            log("port released")
            return True
    log("port still held after kill")
    return False


def port_is_open(timeout=2.0):
    """True if anything is listening. This is the ONLY crash signal used."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def get_status(timeout=8):
    try:
        r = requests.get(f"{API}/trading/scalping/status", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def start_backend():
    """Launch main.py detached, with its own dated log files.

    PRÉCONDITION : le port doit être libre. main.py met ~10-15 s à charger les
    modèles avant d'atteindre sa propre garde anti-doublon, donc si on lance
    alors que le port est déjà pris, l'enfant est encore vivant pendant que le
    port répond — tenu par l'AUTRE process — et on conclurait à tort au succès.
    On refuse donc d'emblée : "le port est ouvert" n'est une preuve de succès
    que si on l'a vu fermé juste avant.
    """
    if port_is_open():
        log("refusing to spawn - something is already listening on "
            f"{PORT} (a second instance would flip the MT5 account)")
        return False

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(HERE, f"backend_8000_{stamp}.log")
    err = out + ".err"
    log(f"starting backend -> {os.path.basename(out)}")
    # DETACHED_PROCESS so the server outlives this short-lived watchdog run;
    # without it the child dies with the scheduled task.
    flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    # ... mais DETACHED ne sort pas du "job" Windows de la tâche planifiée :
    # le 2026-09-25 un backend lancé par la tâche est mort sans trace 9 min
    # après (17:27 -> 17:36), le Planificateur tuant le job de l'instance.
    # CREATE_BREAKAWAY_FROM_JOB le détache du job ; si le job l'interdit,
    # Windows refuse (OSError) et on relance comme avant.
    BREAKAWAY = 0x01000000               # CREATE_BREAKAWAY_FROM_JOB
    with open(out, "ab") as fo, open(err, "ab") as fe:
        try:
            proc = subprocess.Popen([PYTHON, MAIN], cwd=HERE, stdout=fo, stderr=fe,
                                    stdin=subprocess.DEVNULL,
                                    creationflags=flags | BREAKAWAY, close_fds=True)
            log("launched outside the scheduled task's job (breakaway)")
        except OSError as e:
            log(f"breakaway refused ({e}) - launching inside the job")
            proc = subprocess.Popen([PYTHON, MAIN], cwd=HERE, stdout=fo, stderr=fe,
                                    stdin=subprocess.DEVNULL, creationflags=flags,
                                    close_fds=True)

    # Le port ouvert ne suffit PAS comme preuve de succès : main.py refuse de
    # démarrer si quelque chose écoute déjà (garde anti-doublon, sinon deux
    # instances tradent en parallèle et font basculer le compte MT5). Dans ce
    # cas l'enfant meurt aussitôt alors que le port reste ouvert — tenu par
    # l'autre process. Vérifié le 2026-09-15 : start_backend() renvoyait True
    # en ayant lancé un enfant mort. On exige donc les DEUX conditions :
    # l'enfant vit ET le port répond.
    waited = 0
    while waited < BOOT_WAIT:
        time.sleep(POLL)
        waited += POLL
        rc = proc.poll()
        if rc is not None:
            log(f"child exited with code {rc} after {waited}s - see "
                f"{os.path.basename(out)} (port busy? already running?)")
            return False
        if port_is_open():
            log(f"port {PORT} listening after {waited}s (pid {proc.pid})")
            return True
    log(f"port {PORT} still dead after {BOOT_WAIT}s - child pid {proc.pid} "
        f"alive but not serving")
    return False


def start_session():
    """Start the scalping loop using the settings already saved on disk.

    Deliberately reuses the persisted file rather than inventing defaults —
    the saved file is the user's tuned configuration (lot sizes, per-symbol
    settings, expiry). Inventing settings here would silently trade a
    different strategy than the one being measured.
    """
    try:
        with open(SETTINGS, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        log(f"cannot read saved settings ({e}) - backend is up but session NOT "
            f"started; start it from the app")
        return False
    try:
        # source=watchdog : reprise après crash. Le backend conserve alors son
        # état de risque persistant (pic d'equity, fenêtre de perte
        # journalière) au lieu de le traiter comme un démarrage depuis l'app.
        r = requests.post(f"{API}/trading/scalping/start",
                          params={"source": "watchdog"}, json=cfg, timeout=25)
        if r.status_code == 200 and r.json().get("success"):
            log(f"scalping session started "
                f"(symbols={cfg.get('enabled_symbols')}, "
                f"pending_max_age={cfg.get('pending_max_age_minutes')})")
            return True
        log(f"start endpoint returned {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        log(f"start request failed: {e}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would happen, change nothing")
    ap.add_argument("--status", action="store_true",
                    help="same as --dry-run")
    args = ap.parse_args()
    dry = args.dry_run or args.status

    if os.path.exists(DISABLE_FILE):
        log("watchdog.disabled present - standing down")
        return 4

    if port_is_open():
        st = get_status()
        if st is None:
            # Port open but no valid answer. Could be mid-boot or a wedged
            # worker. Not a clean crash signal, so do NOT restart - killing a
            # live process to fix a slow one risks orphaning open positions.
            log("port open but /status did not answer - leaving alone "
                "(not a clean crash signal)")
            return 0
        reason = (st.get("stopped_reason") or "").lower()
        if st.get("running"):
            stalled, why = diagnose_stall(st)
            if not stalled:
                log(f"healthy - {why}, trades_today={st.get('trades_today')}")
                return 0

            # ── the scan loop is stuck though the server still answers ──
            log(f"STALLED: {why}")
            if dry:
                log("dry run: would kill and restart the stalled backend")
                return 0

            # Anti-boucle. Contrairement au cas "port mort", ici on tue un
            # process VIVANT : si le redémarrage ne débloque rien, réessayer
            # toutes les 5 min ferait tourner MT5 en boucle sans jamais laisser
            # le bot trader. Une seule tentative par fenêtre de cooldown.
            state = _read_state()
            last = _age_minutes(state.get("last_stall_restart"))
            if last is not None and last < STALL_COOLDOWN_MINUTES:
                log(f"already restarted for a stall {last:.0f} min ago "
                    f"(cooldown {STALL_COOLDOWN_MINUTES} min) - not retrying; "
                    f"something else is wrong, check the logs")
                return 3

            state["last_stall_restart"] = datetime.now().isoformat()
            _write_state(state)

            if not kill_backend():
                log("could not free the port - leaving the stalled instance "
                    "alone rather than risking two instances")
                return 3
            if not start_backend():
                log("RESTART FAILED after stall")
                return 3
            started = start_session()
            log("stall restart complete" if started else
                "backend up after stall but session not started - check the app")
            return 2 if started else 3
        elif any(k in reason for k in INTENTIONAL):
            log(f"stopped on purpose ({st.get('stopped_reason')}) - leaving alone")
        else:
            # Server alive, bot idle, no reason given. Ambiguous: could be a
            # fresh boot before the app pressed start. Report, do not act.
            log("backend up but session idle with no stopped_reason - "
                "reporting only, not auto-starting")
        return 0

    log(f"NOTHING LISTENING ON {PORT} - backend is down")
    if dry:
        log("dry run: would restart backend + scalping session")
        return 0

    if not start_backend():
        log("RESTART FAILED - backend did not come up")
        return 3
    started = start_session()
    log("restart complete" if started else
        "backend up but session not started - check the app")
    return 2 if started else 3


if __name__ == "__main__":
    sys.exit(main())
