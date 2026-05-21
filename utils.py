import hashlib
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("robust_poller")

def sha256_string(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()

def to_text(b):
    if b is None:
        return None
    if isinstance(b, str):
        return b
    return b.decode("utf-8", errors="replace")

def debug_log(*args):
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logger.debug(" ".join(str(a) for a in args))