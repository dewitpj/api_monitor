# config.py - set your config here
MYSQL = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'api_monitor',
    'password': '9LL3nT6GpZrqxZJY',
    'db': 'api_monitor',
    'charset': 'utf8mb4',
}

REDIS = {
    'host': '127.0.0.1',
    'port': 6379,
    'db': 0,
}

# how many seconds between runs? systemd timer runs every 30s; script polls targets and exits.
USER_AGENT = "api-monitor/1.0"
POLLER_ID = "0"
CLI_LISTEN_ADDR = "0.0.0.0"
CLI_LISTEN_PORT = 4408

CURL_TIMEOUT = 45
CURL_CONN_TIMEOUT = 10
