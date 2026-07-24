#!/usr/bin/env python3
"""
Robust poller with supervised worker threads.
"""

import logging
import threading
import time

from config import REMOTE_MONITOR_ENABLED, REMOTE_MONITOR_HOST, REMOTE_MONITOR_PORT
from supervisor import Supervisor, threads_lock, active_workers
from remote_monitor import RemoteMonitorServer

logger = logging.getLogger("robust_poller")
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


def main_loop():
    stop_event = threading.Event()
    supervisor = Supervisor(stop_event)
    supervisor.start()

    api_server = None
    if REMOTE_MONITOR_ENABLED:
        try:
            api_server = RemoteMonitorServer(REMOTE_MONITOR_HOST, REMOTE_MONITOR_PORT)
            api_server.start()
            logger.info("Remote monitor API listening on %s:%s", REMOTE_MONITOR_HOST, REMOTE_MONITOR_PORT)
        except Exception:
            logger.exception("Failed to start remote monitor API")
            api_server = None

    try:
        while not stop_event.wait(1):
            pass
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received - shutting down")
        stop_event.set()
    finally:
        if api_server:
            api_server.stop()
            if api_server.thread.is_alive():
                api_server.thread.join(timeout=3)
        with threads_lock:
            for worker in active_workers.values():
                try:
                    worker.stop()
                except Exception:
                    pass
        stop_event.set()
        supervisor.join(timeout=3)
        if supervisor.is_alive():
            logger.warning("Supervisor thread did not stop cleanly")
        time.sleep(2)


if __name__ == "__main__":
    main_loop()
