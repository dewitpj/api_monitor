import socket
import pycurl
import io
import json
import time
import logging
from urllib.parse import urlparse
from config import USER_AGENT, CURL_TIMEOUT, CURL_CONN_TIMEOUT
from utils import to_text

logger = logging.getLogger("robust_poller")

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
    "filetime": pycurl.INFO_FILETIME,
}

def parse_hostname(url):
    if not url:
        return None
    if '://' not in url:
        url = 'http://' + url
    parsed = urlparse(url)
    return parsed.hostname


def resolve_target_ips(target):
    hostname = parse_hostname(target.get('url'))
    if not hostname:
        logger.debug("Invalid target URL for DNS resolution: %s", target.get('url'))
        return []

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except Exception as e:
        logger.debug("DNS resolution failed for %s: %s", hostname, e)
        return []

    ips = []
    for family, _, _, _, sockaddr in addrinfo:
        ip = sockaddr[0]
        if family == socket.AF_INET:
            ips.append((ip, 4))
        elif family == socket.AF_INET6:
            ips.append((ip, 6))
    return list(dict.fromkeys(ips))

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

def perform_request_curl_ip(target, ip, ip_version, ip_index, extra_headers=None):
    header_buffer = []
    body_buffer = io.BytesIO()
    c = pycurl.Curl()
    url = target['url']
    c.setopt(pycurl.URL, url)
    c.setopt(pycurl.USERAGENT, USER_AGENT)
    c.setopt(pycurl.FOLLOWLOCATION, True)
    c.setopt(pycurl.MAXREDIRS, 3)
    c.setopt(pycurl.CONNECTTIMEOUT, CURL_CONN_TIMEOUT)
    c.setopt(pycurl.TIMEOUT, CURL_TIMEOUT)
    c.setopt(pycurl.WRITEFUNCTION, body_buffer.write)

    hostname = parse_hostname(url)
    if hostname:
        c.setopt(pycurl.RESOLVE, [f"{hostname}:443:{ip}", f"{hostname}:80:{ip}"])

    def header_func(header_line):
        header_buffer.append(header_line.decode('utf-8', 'replace'))
    c.setopt(pycurl.HEADERFUNCTION, header_func)

    method = (target.get('http_method') or 'GET').upper()
    if method == 'POST':
        c.setopt(pycurl.POST, 1)
        if target.get('post_body') is not None:
            b = target['post_body']
            if isinstance(b, str):
                b = b.encode('utf-8')
            c.setopt(pycurl.POSTFIELDS, b)
    elif method in ('PUT', 'DELETE'):
        c.setopt(pycurl.CUSTOMREQUEST, method)
        if target.get('post_body') is not None:
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
                logger.debug("%s caused %s", name, e)

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