import json
import time
import logging
import pycurl
import io
import requests
from urllib.parse import urlencode
from config import USER_AGENT, CURL_TIMEOUT
from database import fetch_api_keys

logger = logging.getLogger("robust_poller")

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