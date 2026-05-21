#!/usr/bin/env python3
"""
Robust poller with supervised worker threads and a socket-based cmd2 CLI.

- Each target runs in a TargetWorker thread.
- Supervisor monitors workers and restarts them if they die.
- Restart throttling/backoff to avoid tight crash loops.
- Improved logging and safer DB/Redis handling.
- Socket CLI (default 127.0.0.1:5555) providing show/configure/reload commands.
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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
import importlib

import pycurl
import pymysql
import redis
import requests

# cmd2 for CLI
from cmd2 import Cmd, with_argparser
import argparse

# config import (module reloaded on reload-config)
import config as _config_module
from config import MYSQL, REDIS, USER_AGENT, POLLER_ID, CLI_LISTEN_ADDR, CLI_LISTEN_PORT

# ==============================
# CONFIG (defaults can be overridden in config.py)
# ==============================
DEBUG = True
VERBOSE = DEBUG
CHECK_NEW_TARGETS_INTERVAL = getattr(_config_module, "CHECK_NEW_TARGETS_INTERVAL", 60)   # how often to poll DB for new targets
CURL_TIMEOUT = getattr(_config_module, "CURL_TIMEOUT", 45)
CURL_CONN_TIMEOUT = getattr(_config_module, "CURL_CONN_TIMEOUT", 10)
CONNECTIVITY_CHECK_INTERVAL = getattr(_config_module, "CONNECTIVITY_CHECK_INTERVAL", 5)
WORKER_RESTART_BACKOFF_BASE = getattr(_config_module, "WORKER_RESTART_BACKOFF_BASE", 2)
WORKER_RESTART_MAX_BACKOFF = getattr(_config_module, "WORKER_RESTART_MAX_BACKOFF", 300)
WORKER_RESTART_WINDOW = getattr(_config_module, "WORKER_RESTART_WINDOW", 3600)
WORKER_MAX_RESTARTS_IN_WINDOW = getattr(_config_module, "WORKER_MAX_RESTARTS_IN_WINDOW", 10)

CLI_LISTEN_ADDR = getattr(_config_module, "CLI_LISTEN_ADDR", "127.0.0.1")
CLI_LISTEN_PORT = getattr(_config_module, "CLI_LISTEN_PORT", 5555)

# ==============================

# ---- GLOBAL STATE ----
IS_ONLINE = False
threads_lock = threading.Lock()
active_workers = {}  # tid -> TargetWorker
supervisor_ref = None   # tuple(stop_event, supervisor_thread)
cli_server_ref = None   # hold server thread to allow stopping
shutting_down = False

# --- logging setup ---
logger = logging.getLogger("robust_poller")
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)

info_options = {
    "response_code": pycurl.RESPONSE_CODE,
    "namelookup_time": pycurl.NAMELOOKUP_TIME,
    "connect_time": pycurl.CONNECT_TIME,
    "appconnect_time": pycurl.APPCONNECT_TIME,
    "pretransfer_time": pycurl.PRETRANSFER_TIME,
    "starttransfer_time": pycurl.STARTTRANSFER_TIME,
    "redirect_time": pycurl.REDIRECT_TIME,
    "redirect_count": pycurl.REDIRECT_COUNT,
    "redirect_url": pycurl.REDIRECT_URL,
    "size_upload": pycurl.SIZE_UPLOAD,
    "size_download": pycurl.SIZE_DOWNLOAD,
    "speed_upload": pycurl.SPEED_UPLOAD,
    "speed_download": pycurl.SPEED_DOWNLOAD,
    "header_size": pycurl.HEADER_SIZE,
    "request_size": pycurl.REQUEST_SIZE,
    "ssl_verifyresult": pycurl.SSL_VERIFYRESULT,
    "ssl_engines": pycurl.OPT_CERTINFO,
    "content_length_download": pycurl.CONTENT_LENGTH_DOWNLOAD,
    "content_length_upload": pycurl.CONTENT_LENGTH_UPLOAD,
    "content_type": pycurl.CONTENT_TYPE,
    "primary_ip": pycurl.PRIMARY_IP,
    "primary_port": pycurl.PRIMARY_PORT,
    "local_ip": pycurl.LOCAL_IP,
    "local_port": pycurl.LOCAL_PORT,
    "num_connects": pycurl.NUM_CONNECTS,
    "os_errno": pycurl.OS_ERRNO,
    "ftp_entry_path": pycurl.FTP_ENTRY_PATH,
    "certinfo": pycurl.OPT_CERTINFO,
    "filetime": pycurl.INFO_FILETIME,
}

def debug_log(*args):
    if DEBUG:
        logger.debug(" ".join(str(a) for a in args))

def to_text(b):
    if b is None:
        return None
    if isinstance(b, str):
        return b
    return b.decode("utf-8", errors="replace")

def get_db_conn():
    # Create a new connection each call to avoid using stale connections inside threads.
    logger.debug("Connecting to MySQL: %s", MYSQL.get('host'))
    return pymysql.connect(
        host=MYSQL['host'],
        port=MYSQL.get('port', 3306),
        user=MYSQL['user'],
        password=MYSQL['password'],
        db=MYSQL['db'],
        charset=MYSQL.get('charset', 'utf8mb4'),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def get_redis():
    logger.debug("Connecting to Redis: %s", REDIS.get('host'))
    return redis.Redis(
        host=REDIS['host'],
        port=REDIS['port'],
        db=REDIS.get('db', 0),
        decode_responses=True
    )

# DB helpers (unchanged)
def fetch_target(conn, tid):
    with conn.cursor() as cur:
        sql = "SELECT * FROM targets WHERE id=%s"
        logger.debug("{%s} Executing SQL: %s", tid, sql)
        cur.execute(sql, (tid,))
        return cur.fetchone()

def get_extra_headers(conn, tid):
    with conn.cursor() as cur:
        sql = "SELECT CONCAT(`name`,': ',`value`) as header FROM extra_headers WHERE target_id=%s"
        logger.debug("{%s} Executing SQL: %s", tid, sql)
        cur.execute(sql, (tid,))
        return cur.fetchall()

def fetch_targets(conn):
    with conn.cursor() as cur:
        sql = "SELECT * FROM targets WHERE active=1"
        logger.debug("Executing SQL: %s", sql)
        cur.execute(sql)
        return cur.fetchall()

def fetch_target_token(conn, tid):
    with conn.cursor() as cur:
        sql = ("SELECT * FROM tokens WHERE target_id=%s and token!="" and token IS NOT NULL "
               "ORDER BY `tokens`.`expiry` DESC LIMIT 1")
        logger.debug("{%s} Executing SQL: %s", tid, sql)
        cur.execute(sql, (tid,))
        return cur.fetchone()

def fetch_api_keys(db, target_id):
    cur = db.cursor()
    sql = "SELECT name, value FROM api_keys WHERE target_id = %s"
    logger.debug("Executing SQL: %s", sql)
    cur.execute(sql, (target_id,))
    rows = cur.fetchall()
    cur.close()
    keys = {r['name']: r['value'] for r in rows}
    return keys

# token helpers (unchanged)
def get_bearer_token(auth_url, keys, payload_type='form', check_method='curl', verbose=False):
    if 'grant_type' not in keys:
        keys['grant_type'] = 'client_credentials'

    payload = keys.copy()
    if payload_type == 'json':
        headers = {'Content-Type': 'application/json'}
    else:
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        if check_method.lower() == 'requests':
            if payload_type == 'json':
                resp = requests.post(auth_url, json=payload, headers=headers, timeout=CURL_TIMEOUT)
            else:
                resp = requests.post(auth_url, data=payload, headers=headers, timeout=CURL_TIMEOUT)

            if resp.status_code != 200:
                logger.debug("Auth server returned status %s", resp.status_code)
                return None
            data = resp.json()
            return [data.get('access_token') or data.get('token'), data.get('expires_in') or 86400]
        else:
            curl = pycurl.Curl()
            buf = io.BytesIO()
            curl.setopt(pycurl.URL, auth_url)
            curl.setopt(pycurl.USERAGENT, USER_AGENT)
            curl.setopt(pycurl.WRITEFUNCTION, buf.write)
            curl.setopt(pycurl.TIMEOUT, CURL_TIMEOUT)
            if payload_type == 'json':
                curl.setopt(curl.HTTPHEADER, ['Content-Type: application/json'])
                curl.setopt(curl.POSTFIELDS, json.dumps(payload))
            else:
                curl.setopt(curl.HTTPHEADER, ['Content-Type: application/x-www-form-urlencoded'])
                curl.setopt(curl.POSTFIELDS, urlencode(payload))
            curl.perform()
            status_code = curl.getinfo(pycurl.RESPONSE_CODE)
            curl.close()
            if status_code != 200:
                logger.debug("Auth curl returned status %s", status_code)
                return None
            body = buf.getvalue().decode('utf-8')
            data = json.loads(body)
            return [data.get('access_token') or data.get('token'), data.get('expires_in') or 86400]
    except Exception:
        logger.exception("Failed to get bearer token")
        return None

def get_target_token(conn, target):
    token = ""
    tid = target.get('id')
    with conn.cursor() as cur:
        sql = ("SELECT token FROM tokens WHERE target_id=%s and `expiry`>NOW() "
               "ORDER BY `tokens`.`expiry` DESC LIMIT 1")
        logger.debug("Executing SQL: %s", sql)
        cur.execute(sql, (tid,))
        db_token = cur.fetchone()

    if not db_token:
        token = ""
    else:
        token = db_token.get('token') or ""

    if token:
        logger.debug("We got a valid token from the DB for target %s", tid)
        return token

    logger.debug("No valid token in DB for target %s, attempting to fetch new one", tid)
    if target.get('auth_url'):
        keys = fetch_api_keys(conn, tid)
        if not keys:
            logger.debug("No API keys configured for target %s", tid)
            return ""
        got = get_bearer_token(
            target['auth_url'], keys,
            payload_type=target.get('auth_payload_type', 'form'),
            check_method=target.get('check_method', 'curl'),
            verbose=False
        )
        if not got:
            return ""
        token, expires = got
        try:
            sql = "INSERT INTO tokens (`token`,`target_id`,`expiry`) VALUES (%s,%s,FROM_UNIXTIME(%s))"
            with conn.cursor() as cur:
                cur.execute(sql, (token, tid, int(time.time()) + int(expires)))
        except Exception:
            logger.exception("Failed to write token to DB for target %s", tid)
        return token

    logger.debug("Target %s has no auth_url; assuming no auth required", tid)
    return ""

# ---- DNS resolution with restrictions ----
def resolve_target_ips(target):
    hostname = target['url'].split('/')[2]  # crude but works for http(s)://host/...
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except Exception as e:
        logger.debug("DNS resolution failed for %s: %s", hostname, e)
        return []

    ips = []
    for family, _, _, _, sockaddr in addrinfo:
        ip = sockaddr[0]
        if family == socket.AF_INET:
            ips.append((ip, 4))
        elif family == socket.AF_INET6:
            ips.append((ip, 6))
    return list(dict.fromkeys(ips))  # deduplicate

def filter_ips(target, ips):
    result = []
    for ip, version in ips:
        if target.get("check_all_ips"):
            result.append((ip, version))
        elif version == 4 and target.get("check_ipv4"):
            result.append((ip, version))
        elif version == 6 and target.get("check_ipv6"):
            result.append((ip, version))
    return result

# ---- Stats aggregator (thread-safe) ----
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.total_requests = 0
        self.total_bytes_download = 0
        self.total_bytes_upload = 0
        # per-target: tid -> {requests, bytes_download, bytes_upload}
        self.per_target = {}

    def add(self, tid, info):
        # info expected to contain 'size_download' and 'size_upload' or 'size_download' keys
        with self.lock:
            self.total_requests += 1
            sd = int(info.get('size_download') or info.get('size_download', 0) or 0)
            su = int(info.get('size_upload') or info.get('size_upload', 0) or 0)
            self.total_bytes_download += sd
            self.total_bytes_upload += su
            rec = self.per_target.setdefault(tid, {'requests': 0, 'bytes_download': 0, 'bytes_upload': 0})
            rec['requests'] += 1
            rec['bytes_download'] += sd
            rec['bytes_upload'] += su

    def snapshot(self):
        with self.lock:
            return {
                'total_requests': self.total_requests,
                'total_bytes_download': self.total_bytes_download,
                'total_bytes_upload': self.total_bytes_upload,
                'per_target': {k: v.copy() for k, v in self.per_target.items()}
            }

STATS = Stats()

# ---- Perform request (curl-based) ----
def perform_request_curl_ip(target, ip, ip_version, ip_index, extra_headers=None):
    header_buffer = []
    body_buffer = io.BytesIO()
    c = pycurl.Curl()
    url = target['url']
    c.setopt(pycurl.URL, url.encode('utf-8'))
    c.setopt(pycurl.USERAGENT, USER_AGENT)
    c.setopt(pycurl.FOLLOWLOCATION, True)
    c.setopt(pycurl.MAXREDIRS, 3)
    c.setopt(pycurl.CONNECTTIMEOUT, CURL_CONN_TIMEOUT)
    c.setopt(pycurl.TIMEOUT, CURL_TIMEOUT)
    c.setopt(pycurl.WRITEFUNCTION, body_buffer.write)

    # Force connection to a specific IP
    hostname = url.split('/')[2]
    c.setopt(pycurl.RESOLVE, [f"{hostname}:443:{ip}", f"{hostname}:80:{ip}"])

    def header_func(header_line):
        header_buffer.append(header_line.decode('utf-8', 'replace'))
    c.setopt(pycurl.HEADERFUNCTION, header_func)

    method = (target.get('http_method') or 'GET').upper()
    if method == 'POST':
        c.setopt(pycurl.POST, 1)
        if target.get('post_body'):
            b = target['post_body']
            if isinstance(b, str):
                b = b.encode('utf-8')
            c.setopt(pycurl.POSTFIELDS, b)
        else:
            c.setopt(pycurl.POSTFIELDS, "{}")
    elif method in ('PUT', 'DELETE'):
        c.setopt(pycurl.CUSTOMREQUEST, method)
        if target.get('post_body'):
            b = target['post_body']
            if isinstance(b, str):
                b = b.encode('utf-8')
            c.setopt(pycurl.POSTFIELDS, b)

    headers = []
    if target.get('headers'):
        try:
            hdict = json.loads(target['headers'])
            for k, v in hdict.items():
                headers.append(f"{k}: {v}")
        except Exception:
            for ln in (target['headers'] or '').splitlines():
                ln = ln.strip()
                if ln:
                    headers.append(ln)
    if extra_headers:
        headers.extend(extra_headers)
    if headers:
        c.setopt(pycurl.HTTPHEADER, headers)

    start = time.time()
    try:
        c.perform()
        end = time.time()
        info = {
            'http_code': c.getinfo(pycurl.RESPONSE_CODE),
            'total_time': c.getinfo(pycurl.TOTAL_TIME),
            'effective_url': c.getinfo(pycurl.EFFECTIVE_URL),
            'ip_version': ip_version,
            'ip_index': ip_index,
            'ip_address': ip
        }
        for name, option in info_options.items():
            try:
                info[name] = c.getinfo(option)
            except Exception as e:
                logger.debug("%s caused %s", name, e)

    except pycurl.error as e:
        end = time.time()
        errno, errstr = e.args
        info = {
            'http_code': None,
            'error_errno': errno,
            'error_str': str(errstr),
            'total_time': end - start,
            'ip_version': ip_version,
            'ip_index': ip_index,
            'ip_address': ip
        }
    finally:
        c.close()

    resp_body = body_buffer.getvalue()
    resp_headers = ''.join(header_buffer)
    return info, resp_headers, resp_body

# ---- Save results ----
def save_result(conn, target_id, info, headers_text, body_bytes):
    http_code = info.get('http_code')
    body_text = to_text(body_bytes)
    curl_info_json = json.dumps(info, default=str)

    with conn.cursor() as cur:
        sql = ("INSERT INTO monitor_logs "
               "(target_id, poller_id, polled_at, http_code, curl_info, response_headers, response_body, ip_version, ip_index, ip_address) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
        cur.execute(sql,
            (target_id, POLLER_ID, datetime.now(ZoneInfo("Africa/Johannesburg")).strftime('%Y-%m-%d %H:%M:%S'),
             http_code, curl_info_json, headers_text, body_text,
             info.get('ip_version'), info.get('ip_index'), info.get('ip_address'))
        )

def write_latest_redis(rconn, target_id, info, headers_text, body_bytes):
    body_text = to_text(body_bytes)
    rec = {
        'target_id': target_id,
        'poller_id': POLLER_ID,
        'polled_at': datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat() + 'Z',
        'http_code': info.get('http_code'),
        'total_time': info.get('total_time'),
        'curl_info': info,
        'response_headers': headers_text,
        'response_body_snippet': body_text[:4096]
    }
    try:
        rconn.set(f"api_monitor:latest:{target_id}", json.dumps(rec))
    except Exception:
        logger.exception("Failed to write latest to redis for %s", target_id)

    # update aggregated stats
    try:
        STATS.add(target_id, info)
    except Exception:
        logger.exception("Failed to update aggregated stats for %s", target_id)


# ---- Connectivity check ----
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

def connectivity_loop(stop_event):
    global IS_ONLINE
    while not stop_event.is_set():
        online = check_connectivity()
        IS_ONLINE = online
        logger.debug("Connectivity check: %s", "ONLINE" if online else "OFFLINE")
        stop_event.wait(CONNECTIVITY_CHECK_INTERVAL)

# ---- Target worker class ----
class TargetWorker(threading.Thread):
    """Thread that runs checks for a single target.
    The thread will attempt to recover from exceptions inside its loop.
    If the whole thread function exits unexpectedly, the supervisor will restart it.
    """

    def __init__(self, target, rconn, *args, **kwargs):
        name = f"target-{target.get('id')}"
        super().__init__(name=name, daemon=True, *args, **kwargs)
        self.target = target.copy()
        self.tid = target.get('id')
        self.rconn = rconn
        self._stop_event = threading.Event()
        # for restart throttling
        self.restart_times = []
        self.last_heartbeat = datetime.now(timezone.utc)
        self.exception = None

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def heartbeat(self):
        self.last_heartbeat = datetime.now(timezone.utc)

    def run(self):
        logger.info("Worker started for target %s", self.tid)
        try:
            self._run_loop()
        except Exception:
            self.exception = traceback.format_exc()
            logger.exception("Unhandled exception in worker %s", self.tid)
            # Let the thread exit; supervisor will restart

    def _run_loop(self):
        db = None
        try:
            db = get_db_conn()
        except Exception:
            logger.exception("Failed to obtain DB connection for worker %s", self.tid)
        interval = self.target.get('check_interval', 60) or 60
        while not self.stopped():
            try:
                if db is None:
                    try:
                        db = get_db_conn()
                    except Exception:
                        logger.exception("DB connect failed inside worker %s; will retry later", self.tid)

                if db:
                    fresh = fetch_target(db, self.tid)
                    if fresh:
                        self.target.update(fresh)
                        interval = self.target.get('check_interval', 60) or 60

                if self.target.get('active') != 1:
                    logger.info("Target %s marked inactive; worker sleeping", self.tid)
                else:
                    if not IS_ONLINE:
                        info = {"http_code": -2, "total_time": 0, "error_str": "no connectivity", "ip_version": 4, "ip_index": 0, "ip_address": None}
                        if db:
                            save_result(db, self.tid, info, "", b"")
                        write_latest_redis(self.rconn, self.tid, info, "", b"")
                    else:
                        extra_headers = []
                        if self.target.get('auth_url'):
                            token = None
                            try:
                                token = get_target_token(db, self.target)
                            except Exception:
                                logger.exception("Error while getting token for %s", self.tid)
                            if token:
                                extra_headers.append(f"Authorization: Bearer {token}")

                        try:
                            for eh in get_extra_headers(db, self.tid):
                                extra_headers.append(eh["header"])
                        except Exception:
                            logger.exception("Failed to load extra headers for %s", self.tid)

                        ips = resolve_target_ips(self.target)
                        ips = filter_ips(self.target, ips)

                        if not ips:
                            info = {"http_code": -3, "total_time": 0, "error_str": "No IP address found within our restrictions",
                                    "ip_version": 4, "ip_index": 0, "ip_address": None}
                            if db:
                                save_result(db, self.tid, info, "", b"")
                            write_latest_redis(self.rconn, self.tid, info, "", b"")
                        else:
                            for idx, (ip, ver) in enumerate(ips):
                                info, headers_text, body_bytes = perform_request_curl_ip(self.target, ip, ver, idx, extra_headers=extra_headers)
                                if db:
                                    save_result(db, self.tid, info, headers_text, body_bytes)
                                write_latest_redis(self.rconn, self.tid, info, headers_text, body_bytes)

                self.heartbeat()

            except Exception:
                logger.exception("Worker %s caught exception in loop; continuing", self.tid)

            wait = max(1, int(interval))
            for _ in range(wait):
                if self.stopped():
                    break
                time.sleep(1)

        try:
            if db:
                db.close()
        except Exception:
            logger.debug("Failed to close db for worker %s", self.tid)

        logger.info("Worker exiting for target %s", self.tid)

# ---- Supervisor that ensures worker threads are running ----
class Supervisor(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(name="supervisor", daemon=True)
        self.stop_event = stop_event

    def run(self):
        logger.info("Supervisor started")
        restart_history = {}  # tid -> list of unix timestamps

        try:
            db = get_db_conn()
            rconn = get_redis()
        except Exception:
            logger.exception("Supervisor failed to connect to DB/Redis on startup")
            db = None
            rconn = None

        conn_stop = threading.Event()
        conn_thread = threading.Thread(target=connectivity_loop, args=(conn_stop,), name="connectivity", daemon=True)
        conn_thread.start()

        while not self.stop_event.is_set():
            try:
                if db is None:
                    try:
                        db = get_db_conn()
                    except Exception:
                        logger.exception("Supervisor DB connect failed; retrying")

                if rconn is None:
                    try:
                        rconn = get_redis()
                    except Exception:
                        logger.exception("Supervisor redis connect failed; retrying")

                if db:
                    targets = fetch_targets(db)
                    with threads_lock:
                        for t in targets:
                            tid = t.get('id')
                            if tid not in active_workers or not active_workers[tid].is_alive():
                                now = time.time()
                                hist = restart_history.get(tid, [])
                                hist = [ts for ts in hist if ts > now - WORKER_RESTART_WINDOW]
                                if len(hist) >= WORKER_MAX_RESTARTS_IN_WINDOW:
                                    logger.warning("Too many restarts for %s in window; skipping restart", tid)
                                    restart_history[tid] = hist
                                    continue

                                backoff = WORKER_RESTART_BACKOFF_BASE ** len(hist)
                                backoff = min(backoff, WORKER_RESTART_MAX_BACKOFF)
                                if hist:
                                    logger.info("Backoff %s seconds before restarting worker %s", backoff, tid)
                                    time.sleep(backoff)

                                try:
                                    worker = TargetWorker(t, rconn)
                                    worker.start()
                                    active_workers[tid] = worker
                                    hist.append(now)
                                    restart_history[tid] = hist
                                    logger.info("Started worker for %s", tid)
                                except Exception:
                                    logger.exception("Failed to start worker for %s", tid)

                with threads_lock:
                    for tid, worker in list(active_workers.items()):
                        if not worker.is_alive():
                            logger.warning("Worker %s is not alive; removing from active_workers", tid)
                            try:
                                worker.stop()
                            except Exception:
                                pass
                            del active_workers[tid]

                for _ in range(CHECK_NEW_TARGETS_INTERVAL):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception:
                logger.exception("Supervisor loop exception; continuing")
                time.sleep(5)

        conn_stop.set()
        logger.info("Supervisor exiting")

# ---- CLI: cmd2-based with auto-abbrev and socket support ----
def get_worker_snapshot():
    """Return snapshot of current workers for CLI display"""
    out = {}
    with threads_lock:
        for tid, w in active_workers.items():
            try:
                out[tid] = {
                    'alive': w.is_alive(),
                    'last_heartbeat': w.last_heartbeat.isoformat() if getattr(w, 'last_heartbeat', None) else None,
                    'exception': getattr(w, 'exception', None)
                }
            except Exception:
                out[tid] = {'alive': False, 'last_heartbeat': None, 'exception': 'error'}
    return out

# Custom CLI class with auto-alias behavior and access to runtime state
class PollerCLI(Cmd):
    prompt = "Router> "
    intro = "Welcome to the Poller CLI. Type ? or help for commands.\n"
    use_rawinput = False  # when we drive via socket we set this and provide file-like stdin/out

    def __init__(self, stdin=None, stdout=None):
        super().__init__(persistent_history_file=None)
        if stdin is not None:
            # cmd2 uses self.stdin/self.stdout when use_rawinput=False
            self.stdin = stdin
        if stdout is not None:
            self.stdout = stdout

        # optional: allow toggling auto alias
        self.auto_alias = True

    # Auto-abbrev override
    def onecmd(self, line):
        cmd, arg, rest = self.parseline(line)
        if not cmd:
            return super().onecmd(line)
        # list of commands we implement (without 'do_')
        commands = [n[3:] for n in dir(self) if n.startswith("do_")]
        if cmd in commands:
            return super().onecmd(line)
        if self.auto_alias:
            matches = [c for c in commands if c.startswith(cmd)]
            if len(matches) == 1:
                # replace only the first token
                parts = line.split(' ', 1)
                if len(parts) == 1:
                    new_line = matches[0]
                else:
                    new_line = matches[0] + ' ' + parts[1]
                return super().onecmd(new_line)
            elif len(matches) > 1:
                self.perror(f"Ambiguous command '{cmd}': {', '.join(matches)}")
                return False
            else:
                self.perror(f"Unknown command '{cmd}'")
                return False
        else:
            return super().onecmd(line)

    # -----------------------
    # show command with argparse
    # -----------------------
    show_parser = argparse.ArgumentParser()
    show_parser.add_argument("topic", nargs="?", help="Topic to show: version|stats|workers|targets|latest")
    show_parser.add_argument("arg", nargs="?", help="Optional argument for topic, e.g., target id")

    @with_argparser(show_parser)
    def do_show(self, args):
        """Show information: show [version|stats|workers|targets|latest <tid>]"""
        topic = (args.topic or "").lower()
        if topic in ("version", "ver", "v", ""):
            self.poutput(f"RouterOS Poller v1.0\nCompiled: {datetime.now(ZoneInfo('Africa/Johannesburg')).date()}")
        elif topic in ("stats", "st"):
            snap = STATS.snapshot()
            self.poutput(f"Total requests: {snap['total_requests']}")
            self.poutput(f"Total bytes_download: {snap['total_bytes_download']}")
            self.poutput(f"Total bytes_upload: {snap['total_bytes_upload']}")
            if snap['per_target']:
                self.poutput("Requests per-target:")
                for tid, rec in sorted(snap['per_target'].items()):
                    self.poutput(f"  {tid}: requests={rec['requests']} bytes_down={rec['bytes_download']} bytes_up={rec['bytes_upload']}")
        elif topic in ("workers", "wk"):
            snap = get_worker_snapshot()
            for tid, info in sorted(snap.items()):
                self.poutput(f"tid={tid} alive={info['alive']} last_heartbeat={info['last_heartbeat']} exception={info['exception']}")
        elif topic in ("targets",):
            # simple DB-backed list of active targets
            try:
                db = get_db_conn()
                with db.cursor() as cur:
                    cur.execute("SELECT id,name,url,active FROM targets")
                    rows = cur.fetchall()
                    for r in rows:
                        self.poutput(f"{r['id']}: {r.get('name')} {r.get('url')} active={r.get('active')}")
                db.close()
            except Exception:
                self.perror("Failed to fetch targets from DB")
        elif topic in ("latest",):
            tid = args.arg
            if not tid:
                self.perror("Usage: show latest <target_id>")
                return
            try:
                r = get_redis()
                key = f"api_monitor:latest:{tid}"
                val = r.get(key)
                if not val:
                    self.poutput("No latest record found")
                else:
                    try:
                        data = json.loads(val)
                        self.poutput(json.dumps(data, indent=2))
                    except Exception:
                        self.poutput(val)
            except Exception:
                self.perror("Failed to fetch latest from redis")
        else:
            self.poutput("Unknown show topic. Valid: version, stats, workers, targets, latest <tid>")

    def do_configure(self, arg):
        """Enter configuration mode (interactive)"""
        self.poutput("Entering configuration mode...")
        # spawn a nested simple prompt using a new PollerConfig instance
        cfg = PollerConfig(parent=self)
        cfg.cmdloop()
        self.poutput("Leaving configuration mode...")

    def do_reload_config(self, arg):
        """Reload config.py and restart supervisor (reload-config)"""
        try:
            reload_config_and_restart_supervisor()
            self.poutput("Reloaded config and restarted supervisor.")
        except Exception as e:
            self.perror(f"Failed to reload config: {e}")

    def do_show_config(self, arg):
        """Show currently loaded configuration"""
        try:
            self.poutput("Config (loaded):")
            self.poutput(f"MYSQL host: {MYSQL.get('host')}")
            self.poutput(f"REDIS host: {REDIS.get('host')}")
            self.poutput(f"USER_AGENT: {USER_AGENT}")
            self.poutput(f"POLLER_ID: {POLLER_ID}")
            self.poutput(f"CLI listening on: {CLI_LISTEN_ADDR}:{CLI_LISTEN_PORT}")
        except Exception:
            self.perror("Failed to show config")

    def do_quit(self, arg):
        """Quit CLI session (does not stop the daemon)"""
        self.poutput("Bye.")
        return True

    def do_exit(self, arg):
        """Exit CLI session"""
        return self.do_quit(arg)

    def do_shutdown(self, arg):
        """Shutdown the poller daemon (ask for confirmation)"""
        # To avoid accidental shutdown via telnet, require 'yes'
        if arg.strip().lower() != "yes":
            self.poutput("To shutdown the daemon, run: shutdown yes")
            return
        self.poutput("Shutting down poller daemon...")
        # trigger global shutdown
        threading.Thread(target=shutdown_daemon, name="cli-shutdown-task", daemon=True).start()
        return True

# Very small config sub-mode example
class PollerConfig(Cmd):
    prompt = "Router(config)# "

    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent

    def do_set(self, arg):
        """Set CLI runtime options. Example: set auto_alias off|on"""
        parts = arg.split()
        if not parts:
            self.poutput("Usage: set <option> <value>")
            return
        opt = parts[0]
        val = parts[1] if len(parts) > 1 else None
        if opt == "auto_alias":
            if val in ("on", "true", "1"):
                self.parent.auto_alias = True
                self.poutput("auto_alias enabled")
            elif val in ("off", "false", "0"):
                self.parent.auto_alias = False
                self.poutput("auto_alias disabled")
            else:
                self.poutput("Use: set auto_alias on|off")
        else:
            self.poutput("Unknown option")

    def do_exit(self, arg):
        return True

# ---- Supervisor / config reload control helpers ----
def start_supervisor():
    global supervisor_ref
    stop_event = threading.Event()
    sup = Supervisor(stop_event)
    sup.start()
    supervisor_ref = (stop_event, sup)
    logger.info("Supervisor started (ref created)")

def stop_supervisor():
    global supervisor_ref
    if supervisor_ref is None:
        return
    stop_event, sup = supervisor_ref
    stop_event.set()
    logger.info("Waiting for supervisor to stop...")
    sup.join(timeout=5)
    supervisor_ref = None
    logger.info("Supervisor stopped")

def reload_config_and_restart_supervisor():
    """Reload config.py and update global names, then restart supervisor thread"""
    global _config_module, MYSQL, REDIS, USER_AGENT, POLLER_ID, CLI_LISTEN_ADDR, CLI_LISTEN_PORT
    logger.info("Reloading config.py")
    try:
        _config_module = importlib.reload(_config_module)
        # rebind commonly used names
        MYSQL = getattr(_config_module, "MYSQL")
        REDIS = getattr(_config_module, "REDIS")
        USER_AGENT = getattr(_config_module, "USER_AGENT")
        POLLER_ID = getattr(_config_module, "POLLER_ID")
        CLI_LISTEN_ADDR = getattr(_config_module, "CLI_LISTEN_ADDR", CLI_LISTEN_ADDR)
        CLI_LISTEN_PORT = getattr(_config_module, "CLI_LISTEN_PORT", CLI_LISTEN_PORT)
        logger.info("Config reloaded: MYSQL host=%s REDIS host=%s", MYSQL.get('host'), REDIS.get('host'))
    except Exception:
        logger.exception("Failed to reload config.py")
        raise

    # restart supervisor cleanly
    stop_supervisor()
    start_supervisor()
    # if CLI server address changed, user should restart the daemon to change listen binding
    logger.info("Supervisor restarted after config reload")

# ---- CLI socket server ----
def cli_client_handler(conn, addr):
    """Handle a single CLI client over socket; create PollerCLI with file-like streams."""
    logger.info("CLI client connected %s", addr)
    # use text-mode file objects with line buffering
    rfile = conn.makefile('r', encoding='utf-8', newline='\n')
    wfile = conn.makefile('w', encoding='utf-8', newline='\n')

    cli = PollerCLI(stdin=rfile, stdout=wfile)
    try:
        cli.cmdloop()
    except Exception:
        logger.exception("CLI session error for %s", addr)
    finally:
        try:
            rfile.close()
        except Exception:
            pass
        try:
            wfile.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        logger.info("CLI client disconnected %s", addr)

def cli_server_thread(stop_event):
    """Listen on CLI_LISTEN_ADDR:CLI_LISTEN_PORT and spawn client handlers"""
    global CLI_LISTEN_ADDR, CLI_LISTEN_PORT
    addr = CLI_LISTEN_ADDR
    port = CLI_LISTEN_PORT
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((addr, port))
    s.listen(5)
    logger.info("CLI listening on %s:%s", addr, port)
    s.settimeout(1.0)
    try:
        while not stop_event.is_set():
            try:
                conn, client = s.accept()
                t = threading.Thread(target=cli_client_handler, args=(conn, client), name=f"cli-{client[0]}:{client[1]}", daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception:
                logger.exception("Error accepting CLI connection")
                time.sleep(0.5)
    finally:
        s.close()
        logger.info("CLI server thread exiting")

def start_cli_server():
    global cli_server_ref
    stop_event = threading.Event()
    t = threading.Thread(target=cli_server_thread, args=(stop_event,), name="cli-server", daemon=True)
    t.start()
    cli_server_ref = (stop_event, t)
    logger.info("CLI server started")

def stop_cli_server():
    global cli_server_ref
    if cli_server_ref is None:
        return
    stop_event, t = cli_server_ref
    stop_event.set()
    t.join(timeout=2)
    cli_server_ref = None
    logger.info("CLI server stopped")

# ---- Shutdown handling ----
def shutdown_daemon():
    global shutting_down
    shutting_down = True
    logger.info("Daemon shutdown requested")
    # stop CLI server
    try:
        stop_cli_server()
    except Exception:
        logger.exception("Failed to stop CLI server cleanly")
    # stop supervisor and workers
    try:
        stop_supervisor()
    except Exception:
        logger.exception("Failed to stop supervisor cleanly")
    # stop workers
    with threads_lock:
        for w in active_workers.values():
            try:
                w.stop()
            except Exception:
                pass
    # give threads a moment to exit
    time.sleep(1)
    logger.info("Shutdown complete")
    # exit process
    try:
        sys.exit(0)
    except SystemExit:
        raise

# ---- main loop ----
def main_loop():
    global CLI_LISTEN_ADDR, CLI_LISTEN_PORT
    # start supervisor
    start_supervisor()
    # start CLI server
    start_cli_server()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received - shutting down")
        shutdown_daemon()

if __name__ == "__main__":
    main_loop()
