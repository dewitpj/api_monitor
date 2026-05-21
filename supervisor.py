import time
import threading
import logging
from database import get_db_conn, fetch_targets
from redis_utils import get_redis
from worker import TargetWorker
from connectivity import connectivity_loop
from config import CHECK_NEW_TARGETS_INTERVAL, WORKER_RESTART_BACKOFF_BASE, WORKER_RESTART_MAX_BACKOFF, WORKER_RESTART_WINDOW, WORKER_MAX_RESTARTS_IN_WINDOW

logger = logging.getLogger("robust_poller")

# Global state
threads_lock = threading.Lock()
active_workers = {}  # tid -> TargetWorker

class Supervisor(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(name="supervisor", daemon=True)
        self.stop_event = stop_event

    def run(self):
        logger.info("Supervisor started")
        # maintain restart history per tid for throttling
        restart_history = {}  # tid -> list of unix timestamps

        # initial DB/redis and start workers
        try:
            db = get_db_conn()
            rconn = get_redis()
        except Exception:
            logger.exception("Supervisor failed to connect to DB/Redis on startup")
            # try again later
            db = None
            rconn = None

        # start connectivity loop
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

                active_target_ids = set()
                if db:
                    targets = fetch_targets(db)
                    active_target_ids = {t.get('id') for t in targets}
                    with threads_lock:
                        for t in targets:
                            tid = t.get('id')
                            worker = active_workers.get(tid)
                            if worker and worker.is_alive():
                                continue

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
                                if self.stop_event.wait(backoff):
                                    break

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
                        if not worker.is_alive() or tid not in active_target_ids:
                            if worker.is_alive():
                                try:
                                    worker.stop()
                                except Exception:
                                    pass
                            logger.info("Removing worker %s from active_workers", tid)
                            del active_workers[tid]

                if self.stop_event.wait(CHECK_NEW_TARGETS_INTERVAL):
                    break

            except Exception:
                logger.exception("Supervisor loop exception; continuing")
                time.sleep(5)

        # stop connectivity thread
        conn_stop.set()
        logger.info("Supervisor exiting")