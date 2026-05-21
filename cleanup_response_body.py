#!/usr/bin/env python3
import pymysql
import hashlib
from datetime import datetime, timedelta
import config

TABLE_NAME = "monitor_logs"  # hardcoded table name

def sha1_hash(data: str) -> str:
    """Return the SHA1 hash of a string."""
    return hashlib.sha1(data.encode('utf-8')).hexdigest()

def main():
    # Connect to MySQL using config.MYSQL
    conn = pymysql.connect(
        host=config.MYSQL['host'],
        port=config.MYSQL.get('port', 3306),
        user=config.MYSQL['user'],
        password=config.MYSQL['password'],
        database=config.MYSQL['db'],
        charset=config.MYSQL.get('charset', 'utf8mb4'),
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        with conn.cursor() as cur:
            # Determine cutoff date (7 days ago)
            cutoff_date = datetime.now() - timedelta(days=7)
            print(f"[INFO] Deleting response bodies older than {cutoff_date:%Y-%m-%d %H:%M:%S}")

            # Select rows older than 7 days with a response_body
            cur.execute(f"""
                SELECT id, response_body
                FROM `{TABLE_NAME}`
                WHERE created_at < %s
                  AND response_body IS NOT NULL LIMIT 10080
            """, (cutoff_date,))
            rows = cur.fetchall()

            print(f"[INFO] Found {len(rows)} rows to process")

            updated = 0
            for row in rows:
                rid = row['id']
                body = row['response_body']
                sha = sha1_hash(body) if body else None

                # Update response_body_sha and clear the body
                cur.execute(f"""
                    UPDATE `{TABLE_NAME}`
                    SET response_body_sha = %s,
                        response_body = NULL
                    WHERE id = %s
                """, (sha, rid))
                conn.commit()  # commit after each row
                updated += 1
                print(f"[INFO] Processed row {updated} (id={rid})")

            print(f"[DONE] Processed total {updated} records")

    finally:
        conn.close()
        print("[INFO] Database connection closed")

if __name__ == "__main__":
    main()

