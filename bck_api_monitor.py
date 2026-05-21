#!/usr/bin/env python3
import sys, io, json, time, traceback, threading, subprocess, socket
import pycurl, pymysql, redis, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from config import MYSQL, REDIS, USER_AGENT, POLLER_ID

# ==============================
# CONFIG
# ==============================
DEBUG = True
VERBOSE = DEBUG
CHECK_NEW_TARGETS_INTERVAL = 60   # how often to poll DB for new targets
CURL_TIMEOUT = 45
CURL_CONN_TIMEOUT = 10
CONNECTIVITY_CHECK_INTERVAL = 5   # how often to run online check (seconds)
# ==============================

# ---- GLOBAL STATE ----
IS_ONLINE = False
threads_lock = threading.Lock()
active_threads = {}

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
    "ssl_engines": pycurl.SSL_ENGINES,
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
        print("[DEBUG]", *args)

def to_text(b):
    if b is None:
        return None
    if isinstance(b, str):
        return b
    return b.decode('utf-8', errors='replace')

def get_db_conn():
    debug_log("Connecting to MySQL:", MYSQL)
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
    debug_log("Connecting to Redis:", REDIS)
    return redis.Redis(
        host=REDIS['host'],
        port=REDIS['port'],
        db=REDIS.get('db', 0),
        decode_responses=True
    )

def fetch_target(conn,tid):
    with conn.cursor() as cur:
        sql = f"SELECT * FROM targets WHERE id={tid}"
        debug_log("{"+str (tid)+"} Executing SQL:", sql)
        cur.execute(sql)
        return cur.fetchone()

def get_extra_headers(conn,tid):
    with conn.cursor() as cur:
        sql = f"SELECT CONCAT(`name`,': ',`value`) as header FROM extra_headers WHERE target_id={tid}"
        debug_log("{"+str (tid)+"} Executing SQL:", sql)
        cur.execute(sql)
        return cur.fetchall()

def fetch_targets(conn):
    with conn.cursor() as cur:
        sql = "SELECT * FROM targets WHERE active=1"
        debug_log("Executing SQL:", sql)
        cur.execute(sql)
        return cur.fetchall()

def fetch_target_token(conn,tid):
    with conn.cursor() as cur:
        sql= f"SELECT * FROM tokens WHERE target_id={tid} and token!=\"\" and token!=NULL ORDER BY `tokens`.`expiry` DESC LIMIT 1"
        debug_log("{"+str (tid)+"} Executing SQL:", sql)
        cur.execute(sql)
        return cur.fetchone()

def fetch_api_keys(db, target_id):
    cur = db.cursor()
    sql = f"SELECT name, value FROM api_keys WHERE target_id = {target_id}"
    debug_log("Executing SQL:", sql)
    cur.execute (sql)
    rows = cur.fetchall()
    cur.close()
    keys = {}
    for row in rows:
        keys[row['name']] = row['value']
    return keys

def get_bearer_token(auth_url, keys, payload_type='form', check_method='curl', verbose=False):
    if 'grant_type' not in keys:
        keys['grant_type'] = 'client_credentials'

    payload = keys
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
                return None
            data = resp.json()
            return [data.get('access_token') or data.get('token'),data.get ("expires_in") or 86400]
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
                return None
            body = buf.getvalue().decode('utf-8')
            data = json.loads(body)
            return [data.get('access_token') or data.get('token'),data.get ("expires_in") or 86400]
    except Exception:
        traceback.print_exc()
        return None

def get_target_token(conn,target):
    token=""
    with conn.cursor() as cur:
        sql=f"SELECT token FROM tokens WHERE target_id={target.get('id')} and `expiry`>NOW() ORDER BY `tokens`.`expiry` DESC LIMIT 1"
        debug_log ("Executing SQL:",sql)
        cur.execute (sql)
        db_token=cur.fetchone()

    if db_token==None:
        token=""
    else:
        token=db_token ["token"] or ""

    if token!="":
        debug_log ("We got a valid token from the DB, using that")
        return token
    else:
        debug_log ("We either had no token in the DB, or it's expired, grabbing a new one")
        if target.get('auth_url'):
            keys = fetch_api_keys(conn, target.get("id"))
            if (len (keys)==0):
                return ""
            token,expires = get_bearer_token(
                target['auth_url'],
                keys,
                payload_type=target.get('auth_payload_type', 'form'),
                check_method=target.get('check_method', 'curl'),
                verbose=False
            )
            sql=f"INSERT into tokens set `token`=\"{token}\",`target_id`={target.get('id')},`expiry`=FROM_UNIXTIME({time.time()+expires})"
            with conn.cursor() as cur:
                debug_log("Executing SQL:", sql)
                cur.execute (sql)
            return token
        else:
            debug_log ("No token and not auth_url, assuming this endpoint doesn't need auth")
            return ""

# ---- DNS resolution with restrictions ----
def resolve_target_ips(target):
    hostname = target['url'].split('/')[2]  # crude but works for http(s)://host/...
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except Exception as e:
        debug_log("DNS resolution failed:", e)
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
                debug_log(f"{name} caused {e}")

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
    rconn.set(f"api_monitor:latest:{target_id}", json.dumps(rec))

# ---- Connectivity check ----
def check_connectivity():
    """Return True if we can ping either 1.1.1.1 or 8.8.8.8, else False."""
    for ip in ["1.1.1.1", "8.8.8.8"]:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", ip],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False

def connectivity_loop():
    global IS_ONLINE
    while True:
        online = check_connectivity()
        IS_ONLINE = online
        if online:
            debug_log("Connectivity check: ONLINE")
        else:
            debug_log("Connectivity check: OFFLINE")
        time.sleep(CONNECTIVITY_CHECK_INTERVAL)

# ---- Target runner ----
def run_target_loop(target, rconn):
    db = get_db_conn()
    tid = target['id']
    interval = target.get('check_interval', 60) or 60
    while True:
        if target['active'] == 1:
            try:
                if not IS_ONLINE:
                    info = {"http_code": -2, "total_time": 0, "error_str": "no connectivity", "ip_version": 4, "ip_index": 0, "ip_address": None}
                    save_result(db, tid, info, "", b"")
                    write_latest_redis(rconn, tid, info, "", b"")
                else:
                    extra_headers = []
                    if len (target.get ("auth_url"))>0:
                        token=None
                        debug_log ("We have a auth_url for target "+str (target.get ("id")))
                        try:
                            token = get_target_token(db, target)
                        except Exception as e:
                            debug_log ("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! We had an issue on target "+str (target.get ("id")))
                            debug_log (e)
                        if token:
                            extra_headers.append(f"Authorization: Bearer {token}")
                    
                    for eh in get_extra_headers(db, tid):
                        extra_headers.append(eh["header"])

                    ips = resolve_target_ips(target)
                    ips = filter_ips(target, ips)

                    if not ips:
                        info = {"http_code": -3, "total_time": 0, "error_str": "No IP address found within our restrictions",
                                "ip_version": 4, "ip_index": 0, "ip_address": None}
                        save_result(db, tid, info, "", b"")
                        write_latest_redis(rconn, tid, info, "", b"")
                    else:
                        for idx, (ip, ver) in enumerate(ips):
                            info, headers_text, body_bytes = perform_request_curl_ip(target, ip, ver, idx, extra_headers=extra_headers)
                            save_result(db, tid, info, headers_text, body_bytes)
                            write_latest_redis(rconn, tid, info, headers_text, body_bytes)
            except Exception:
                traceback.print_exc()

        time.sleep(interval)
        target = fetch_target(db, tid)
        interval = target.get('check_interval', 60) or 60

def start_target_thread(target, rconn):
    tid = target['id']
    with threads_lock:
        if tid in active_threads:
            debug_log(f"Thread already running for target {tid}")
            return
        print(f"Starting thread for {target['url']}")
        th = threading.Thread(target=run_target_loop, args=(target, rconn), daemon=True)
        th.start()
        active_threads[tid] = th

def main_loop():
    db = get_db_conn()
    rconn = get_redis()
    threading.Thread(target=connectivity_loop, daemon=True).start()
    targets = fetch_targets(db)
    for t in targets:
        start_target_thread(t, rconn)
    while True:
        time.sleep(CHECK_NEW_TARGETS_INTERVAL)
        try:
            db.ping(reconnect=True)
            new_targets = fetch_targets(db)
            for t in new_targets:
                start_target_thread(t, rconn)
        except Exception:
            traceback.print_exc()

if __name__ == "__main__":
    main_loop()
