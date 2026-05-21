import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

from connectivity import is_online
from database import get_db_conn, fetch_targets, fetch_target
from redis_utils import get_redis
from supervisor import active_workers
from config import REMOTE_MONITOR_API_TOKEN

logger = logging.getLogger("robust_poller")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class RemoteMonitorHandler(BaseHTTPRequestHandler):
    server_version = "RemoteMonitor/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        logger.info("HTTP %s - %s", self.address_string(), format % args)

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status=status)

    def authenticate(self):
        token = REMOTE_MONITOR_API_TOKEN
        if not token:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header != f"Bearer {token}":
            self.send_error_json(401, "Unauthorized")
            return False
        return True

    def do_GET(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "":
            return self.send_json({"status": "ok", "message": "Remote monitor API"})
        if path == "/health":
            return self.send_json({"status": "ok"})
        if path == "/status":
            return self.handle_status()
        if path == "/targets":
            return self.handle_targets()
        if path.startswith("/targets/"):
            return self.handle_target_detail(path)
        if path.startswith("/latest/"):
            return self.handle_latest(path)

        self.send_error_json(404, "Endpoint not found")

    def handle_status(self):
        data = {
            "online": is_online(),
            "active_workers": len(active_workers),
        }
        return self.send_json(data)

    def handle_targets(self):
        db = None
        try:
            db = get_db_conn()
            rows = fetch_targets(db)
            targets = [
                {
                    "id": t.get("id"),
                    "url": t.get("url"),
                    "active": t.get("active"),
                    "check_interval": t.get("check_interval"),
                    "auth_url": t.get("auth_url"),
                }
                for t in rows
            ]
            return self.send_json({"targets": targets})
        except Exception:
            logger.exception("Failed to load target list")
            return self.send_error_json(500, "Failed to load target list")
        finally:
            if db:
                db.close()

    def handle_target_detail(self, path):
        tid = path.split("/", 2)[2]
        if not tid.isdigit():
            return self.send_error_json(400, "Invalid target ID")

        db = None
        try:
            db = get_db_conn()
            target = fetch_target(db, int(tid))
            if not target:
                return self.send_error_json(404, "Target not found")
            return self.send_json({"target": target})
        except Exception:
            logger.exception("Failed to load target %s", tid)
            return self.send_error_json(500, "Failed to load target")
        finally:
            if db:
                db.close()

    def handle_latest(self, path):
        tid = path.split("/", 2)[2]
        if not tid.isdigit():
            return self.send_error_json(400, "Invalid target ID")
        redis_conn = None
        try:
            redis_conn = get_redis()
            payload = redis_conn.get(f"api_monitor:latest:{tid}")
            if not payload:
                return self.send_error_json(404, "Latest monitor result not found")
            return self.send_json({"latest": json.loads(payload)})
        except Exception:
            logger.exception("Failed to load latest result for %s", tid)
            return self.send_error_json(500, "Failed to load latest result")
        finally:
            if redis_conn:
                try:
                    redis_conn.close()
                except Exception:
                    pass


class RemoteMonitorServer:
    def __init__(self, host, port):
        self.server = ThreadedHTTPServer((host, port), RemoteMonitorHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            logger.exception("Failed to stop remote monitor HTTP server")
        if self.thread.is_alive():
            self.thread.join(timeout=3)
