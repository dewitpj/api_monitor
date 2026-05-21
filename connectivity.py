import subprocess
import logging
import threading

logger = logging.getLogger("robust_poller")

ONLINE_EVENT = threading.Event()
IS_ONLINE = False

def check_connectivity():
    """Return True if we can ping either 1.1.1.1 or 8.8.8.8, else False."""
    for ip in ["1.1.1.1", "8.8.8.8"]:
        try:
            result = subprocess.run([
                "ping", "-c", "1", "-W", "2", ip
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False

def is_online():
    return ONLINE_EVENT.is_set()


def _set_online(value):
    global IS_ONLINE
    IS_ONLINE = bool(value)
    if IS_ONLINE:
        ONLINE_EVENT.set()
    else:
        ONLINE_EVENT.clear()


def connectivity_loop(stop_event, check_interval=15):
    while not stop_event.is_set():
        online = check_connectivity()
        _set_online(online)
        logger.debug("Connectivity check: %s", "ONLINE" if online else "OFFLINE")
        stop_event.wait(check_interval)