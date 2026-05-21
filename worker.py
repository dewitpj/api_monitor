import time
import traceback
import threading
import logging
from datetime import datetime, timezone
from database import get_db_conn, fetch_target, get_extra_headers
from auth import get_target_token
from network import resolve_target_ips, filter_ips, perform_request_curl_ip
from results import save_result
from redis_utils import write_latest_redis
from connectivity import is_online

logger = logging.getLogger("robust_poller")

class TargetWorker(threading.Thread):
    """Thread that runs checks for a single target.
    The thread will attempt to recover from exceptions inside its loop.
    If the whole thread function exits unexpectedly, the supervisor will restart it.
    """

    def __init__(self, target, rconn, *args, **kwargs):
        name = f"target-{target.get('id')}"
        super().__init__(name=name, daemon=True, *args, **kwargs)
        self.target = target.copy()  # copy since we may fetch fresh target data from DB
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
        # new DB connection per worker
        db = None
        try:
            db = get_db_conn()
        except Exception:
            logger.exception("Failed to obtain DB connection for worker %s", self.tid)
            # continue; we'll try again inside the loop

        interval = max(1, int(self.target.get('check_interval', 60) or 60))
        while not self.stopped():
            try:
                # refresh target from DB - keep pattern of checking active flag
                if db is None:
                    try:
                        db = get_db_conn()
                    except Exception:
                        logger.exception("DB connect failed inside worker %s; will retry later", self.tid)

                if db:
                    fresh = fetch_target(db, self.tid)
                    if fresh:
                        self.target.update(fresh)
                        interval = max(1, int(self.target.get('check_interval', 60) or 60))
                    else:
                        logger.info("Target %s removed from DB; worker exiting", self.tid)
                        break

                if self.target.get('active') != 1:
                    logger.info("Target %s marked inactive; worker sleeping", self.tid)
                else:
                    if not is_online():
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

                        # load configured extra headers from DB
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

                # heartbeat and sleep
                self.heartbeat()

            except Exception:
                logger.exception("Worker %s caught exception in loop; continuing", self.tid)

            # sleep with stop-awareness
            # cap interval to a minimum of 1 second to avoid busy loops
            wait = max(1, int(interval))
            for _ in range(wait):
                if self.stopped():
                    break
                time.sleep(1)

        # cleanup db
        try:
            if db:
                db.close()
        except Exception:
            logger.debug("Failed to close db for worker %s", self.tid)

        logger.info("Worker exiting for target %s", self.tid)