#!/usr/bin/env python3
"""
Robust poller with supervised worker threads.
- Each target runs in a TargetWorker thread.
- Supervisor monitors workers and restarts them if they die.
- Restart throttling/backoff to avoid tight crash loops.
- Improved logging and safer DB/Redis handling.
"""

import sys
import io
import json
import time
import traceback
import threading
import subprocess
import socket
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import pycurl
import pymysql
import redis
import requests

from config import MYSQL, REDIS, USER_AGENT, POLLER_ID

from utils import *
from database import *
from redis_utils import *
from auth import *
from network import *
from results import *
from connectivity import *
from worker import *
from supervisor import *

# Import globals
from connectivity import IS_ONLINE
from supervisor import threads_lock, active_workers

# ==============================
# CONFIG
# ==============================
DEBUG = True
VERBOSE = DEBUG
CHECK_NEW_TARGETS_INTERVAL = 60   # how often to poll DB for new targets
CURL_TIMEOUT = 45
CURL_CONN_TIMEOUT = 10
CONNECTIVITY_CHECK_INTERVAL = 15   # how often to run online check (seconds)
WORKER_RESTART_BACKOFF_BASE = 2   # seconds
WORKER_RESTART_MAX_BACKOFF = 300  # seconds
WORKER_RESTART_WINDOW = 3600      # seconds - window to count restarts
WORKER_MAX_RESTARTS_IN_WINDOW = 10
# ==============================

# --- logging setup ---
logger = logging.getLogger("robust_poller")
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)


def main_loop():
    stop_event = threading.Event()
    supervisor = Supervisor(stop_event)
    supervisor.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received - shutting down")
        stop_event.set()
        # ask workers to stop
        with threads_lock:
            for w in active_workers.values():
                try:
                    w.stop()
                except Exception:
                    pass
        # give threads a moment to exit
        time.sleep(2)


if __name__ == "__main__":
    main_loop()
