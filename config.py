import os

# config.py - set your config here
MYSQL = {
    'host': os.getenv('API_MONITOR_MYSQL_HOST', '127.0.0.1'),
    'port': int(os.getenv('API_MONITOR_MYSQL_PORT', '3306')),
    'user': os.getenv('API_MONITOR_MYSQL_USER', 'api_monitor'),
    'password': os.getenv('API_MONITOR_MYSQL_PASSWORD', '9LL3nT6GpZrqxZJY'),
    'db': os.getenv('API_MONITOR_MYSQL_DB', 'api_monitor'),
    'charset': os.getenv('API_MONITOR_MYSQL_CHARSET', 'utf8mb4'),
}

REDIS = {
    'host': os.getenv('API_MONITOR_REDIS_HOST', '127.0.0.1'),
    'port': int(os.getenv('API_MONITOR_REDIS_PORT', '6379')),
    'db': int(os.getenv('API_MONITOR_REDIS_DB', '0')),
}

USER_AGENT = os.getenv('API_MONITOR_USER_AGENT', 'api-monitor/1.0')
POLLER_ID = os.getenv('API_MONITOR_POLLER_ID', '0')

# CLI/Supervisor settings
CLI_LISTEN_ADDR = os.getenv('API_MONITOR_CLI_LISTEN_ADDR', '0.0.0.0')
CLI_LISTEN_PORT = int(os.getenv('API_MONITOR_CLI_LISTEN_PORT', '4408'))

CHECK_NEW_TARGETS_INTERVAL = int(os.getenv('API_MONITOR_CHECK_NEW_TARGETS_INTERVAL', '60'))
CONNECTIVITY_CHECK_INTERVAL = int(os.getenv('API_MONITOR_CONNECTIVITY_CHECK_INTERVAL', '15'))
WORKER_RESTART_BACKOFF_BASE = float(os.getenv('API_MONITOR_WORKER_RESTART_BACKOFF_BASE', '2'))
WORKER_RESTART_MAX_BACKOFF = int(os.getenv('API_MONITOR_WORKER_RESTART_MAX_BACKOFF', '300'))
WORKER_RESTART_WINDOW = int(os.getenv('API_MONITOR_WORKER_RESTART_WINDOW', '3600'))
WORKER_MAX_RESTARTS_IN_WINDOW = int(os.getenv('API_MONITOR_WORKER_MAX_RESTARTS_IN_WINDOW', '10'))

CURL_TIMEOUT = int(os.getenv('API_MONITOR_CURL_TIMEOUT', '45'))
CURL_CONN_TIMEOUT = int(os.getenv('API_MONITOR_CURL_CONN_TIMEOUT', '10'))

REMOTE_MONITOR_ENABLED = os.getenv('API_MONITOR_REMOTE_MONITOR_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
REMOTE_MONITOR_HOST = os.getenv('API_MONITOR_REMOTE_MONITOR_HOST', '0.0.0.0')
REMOTE_MONITOR_PORT = int(os.getenv('API_MONITOR_REMOTE_MONITOR_PORT', '5500'))
REMOTE_MONITOR_API_TOKEN = os.getenv('API_MONITOR_REMOTE_MONITOR_API_TOKEN', '')
