"""
AFAQ -- READ-ONLY audit of the real `events` table.

Run this on YOUR OWN machine, against your real, configured
`afaq_db` (this sandbox cannot reach it). It uses your existing
config.py / .env, so no new credentials are needed.

SAFETY: every statement in this script is a SELECT. Nothing here
ever writes, updates, deletes, or alters the schema. You can read
every query below before running it.

Usage:
    python audit_events_db.py
    python audit_events_db.py --check-images   # also HEAD-checks each
                                                # image URL (slower,
                                                # needs internet access
                                                # from your machine)
"""

import sys
from urllib.parse import urlparse

import pymysql

from config import Config


def _connect():
    # Config.SQLALCHEMY_DATABASE_URI looks like:
    #   mysql+pymysql://user:pass@host:port/dbname
    parsed = urlparse(
        Config.SQLALCHEMY_DATABASE_URI.replace("mysql+pymysql", "mysql", 1)
    )

    return pymysql.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
    )


def _scalar(cur, sql, params=None):
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return list(row.values())[0] if row else None


def run(check_images=False):
    conn = _connect()

    try:
        with conn.cursor() as cur:
            total = _scalar(cur, "SELECT COUNT(*) FROM events")
            active = _scalar(cur, "SELECT COUNT(*) FROM events WHERE is_active = 1")
            inactive = total - active

            current_or_future = _scalar(
                cur,
                "SELECT COUNT(*) FROM events "
                "WHERE is_active = 1 AND COALESCE(end_date, start_date) >= CURDATE()",
            )

            expired_active = _scalar(
                cur,
                "SELECT COUNT(*) FROM events "
                "WHERE is_active = 1 AND COALESCE(end_date, start_date) < CURDATE()",
            )

            cur.execute(
                "SELECT category, COUNT(*) AS c FROM events "
                "GROUP BY category ORDER BY c DESC"
            )
            categories = cur.fetchall()

            missing_image = _scalar(
                cur, "SELECT COUNT(*) FROM events WHERE image IS NULL OR image = ''"
            )

            cur.execute(
                "SELECT id, title, image FROM events "
                "WHERE image IS NOT NULL AND image != '' "
                "AND image NOT LIKE 'http://%' AND image NOT LIKE 'https://%'"
            )
            malformed_images = cur.fetchall()

            cur.execute(
                "SELECT image, COUNT(*) AS c FROM events "
                "WHERE image IS NOT NULL AND image != '' "
                "GROUP BY image ORDER BY c DESC LIMIT 1000"
            )
            all_images = cur.fetchall()

            missing_start_date = _scalar(
                cur, "SELECT COUNT(*) FROM events WHERE start_date IS NULL"
            )

            missing_time = _scalar(
                cur, "SELECT COUNT(*) FROM events WHERE time IS NULL OR time = ''"
            )

            missing_link = _scalar(
                cur,
                "SELECT COUNT(*) FROM events "
                "WHERE official_link IS NULL OR official_link = ''",
            )

            latest_start = _scalar(cur, "SELECT MAX(start_date) FROM events")
            latest_end = _scalar(cur, "SELECT MAX(end_date) FROM events")
            earliest_start = _scalar(cur, "SELECT MIN(start_date) FROM events")

        from urllib.parse import urlparse as _up

        domain_counts = {}
        for row in all_images:
            host = _up(row["image"]).hostname or "unparsable"
            domain_counts[host] = domain_counts.get(host, 0) + row["c"]

        print("=" * 64)
        print("AFAQ events table -- READ-ONLY audit")
        print("=" * 64)
        print(f"Total events:                {total}")
        print(f"Active:                      {active}")
        print(f"Inactive:                    {inactive}")
        print(f"Active & current/future:     {current_or_future}")
        print(f"Active but already expired:  {expired_active}")
        print(f"Earliest start_date on file: {earliest_start}")
        print(f"Latest start_date on file:   {latest_start}")
        print(f"Latest end_date on file:     {latest_end}")
        print()
        print(f"Missing image:               {missing_image}")
        print(f"Image URL not http(s)://...: {len(malformed_images)}")
        for row in malformed_images[:10]:
            print(f"   - [{row['id']}] {row['title']}: {row['image']!r}")
        print()
        print("Image domains in use:")
        for host, c in sorted(domain_counts.items(), key=lambda kv: -kv[1]):
            print(f"   {host:<30} {c}")
        print()
        print(f"Missing start_date:          {missing_start_date}")
        print(f"Missing/blank time:          {missing_time}")
        print(f"Missing official_link:       {missing_link}")
        print()
        print("By category:")
        for row in categories:
            print(f"   {row['category']:<32} {row['c']}")

        if check_images:
            print()
            print("Checking image reachability (HEAD request, 5s timeout)...")
            try:
                import requests
            except ImportError:
                print("   'requests' is not installed -- skipping. "
                      "Run: pip install requests")
            else:
                broken = []
                for row in all_images:
                    try:
                        resp = requests.head(row["image"], timeout=5, allow_redirects=True)
                        if resp.status_code >= 400:
                            broken.append((row["image"], resp.status_code))
                    except requests.RequestException as exc:
                        broken.append((row["image"], str(exc)))
                print(f"   Checked {len(all_images)} distinct image URLs.")
                print(f"   Broken/unreachable: {len(broken)}")
                for url, reason in broken[:20]:
                    print(f"      - {url} -> {reason}")

        print()
        print("No data was modified. This script only ran SELECT statements.")

    finally:
        conn.close()


if __name__ == "__main__":
    run(check_images="--check-images" in sys.argv)
