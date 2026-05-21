import pymysql
import logging
from config import MYSQL

logger = logging.getLogger("robust_poller")

def get_db_conn():
    # Create a new connection each call to avoid using stale connections inside threads.
    logger.debug("Connecting to MySQL: %s", MYSQL.get('host'))
    return pymysql.connect(
        host=MYSQL['host'],
        port=MYSQL.get('port', 3306),
        user=MYSQL['user'],
        password=MYSQL['password'],
        db=MYSQL['db'],
        charset=MYSQL.get('charset', 'utf8mb4'),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def fetch_target(conn, tid):
    with conn.cursor() as cur:
        sql = "SELECT * FROM targets WHERE id=%s"
        logger.debug("{%s} Executing SQL: %s", tid, sql)
        cur.execute(sql, (tid,))
        return cur.fetchone()

def get_extra_headers(conn, tid):
    with conn.cursor() as cur:
        sql = "SELECT CONCAT(`name`,': ',`value`) as header FROM extra_headers WHERE target_id=%s"
        logger.debug("{%s} Executing SQL: %s", tid, sql)
        cur.execute(sql, (tid,))
        return cur.fetchall()

def fetch_targets(conn):
    with conn.cursor() as cur:
        sql = "SELECT * FROM targets WHERE active=1"
        logger.debug("Executing SQL: %s", sql)
        cur.execute(sql)
        return cur.fetchall()

def fetch_target_token(conn, tid):
    with conn.cursor() as cur:
        sql = ("SELECT * FROM tokens WHERE target_id=%s and token!="" and token IS NOT NULL "
               "ORDER BY `tokens`.`expiry` DESC LIMIT 1")
        logger.debug("{%s} Executing SQL: %s", tid, sql)
        cur.execute(sql, (tid,))
        return cur.fetchone()

def fetch_api_keys(db, target_id):
    cur = db.cursor()
    sql = "SELECT name, value FROM api_keys WHERE target_id = %s"
    logger.debug("Executing SQL: %s", sql)
    cur.execute(sql, (target_id,))
    rows = cur.fetchall()
    cur.close()
    keys = {r['name']: r['value'] for r in rows}
    return keys