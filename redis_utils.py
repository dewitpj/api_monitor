import redis
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from config import POLLER_ID
from utils import to_text

logger = logging.getLogger("robust_poller")

def get_redis():
    from config import REDIS
    logger.debug("Connecting to Redis: %s", REDIS.get('host'))
    return redis.Redis(
        host=REDIS['host'],
        port=REDIS['port'],
        db=REDIS.get('db', 0),
        decode_responses=True
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