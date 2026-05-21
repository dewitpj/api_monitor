import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from config import POLLER_ID
from utils import to_text, sha256_string

logger = logging.getLogger("robust_poller")

def save_result(conn, target_id, info, headers_text, body_bytes):
    http_code = info.get('http_code')
    body_text = to_text(body_bytes) or ''
    headers_text = headers_text or ''
    curl_info_json = json.dumps(info, default=str)

    with conn.cursor() as cur:
        sql = ("INSERT INTO monitor_logs "
               "(target_id, poller_id, polled_at, http_code, curl_info, response_headers, response_headers_sha, response_body, response_body_sha, ip_version, ip_index, ip_address) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
        cur.execute(sql,
            (target_id, POLLER_ID, datetime.now(ZoneInfo("Africa/Johannesburg")).strftime('%Y-%m-%d %H:%M:%S'),
             http_code, curl_info_json, headers_text, sha256_string(headers_text), body_text[:1024], sha256_string(body_text),
             info.get('ip_version'), info.get('ip_index'), info.get('ip_address')
            )
        )