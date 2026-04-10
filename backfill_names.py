"""
One-off script: backfills type_name for all rows in market_transactions
where type_name is NULL, 'Unknown', or starts with 'Unknown ('.

Run once after deploying the updated server.py.
"""

import requests
import pymysql
import os
import time
from dotenv import load_dotenv

load_dotenv()

DB_HOST     = os.getenv("DB_HOST", "borg")
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "123")
DB_NAME     = os.getenv("DB_NAME", "eve")


def resolve_type_names(type_ids):
    names = {}
    ids = list(set(type_ids))
    for i in range(0, len(ids), 1000):
        batch = ids[i:i + 1000]
        try:
            r = requests.post(
                "https://esi.evetech.net/latest/universe/names/",
                json=batch,
                timeout=30
            )
            if r.status_code == 200:
                for entry in r.json():
                    if entry.get("category") == "inventory_type":
                        names[entry["id"]] = entry["name"]
            else:
                print(f"  ESI returned {r.status_code} for batch {i//1000 + 1}")
        except Exception as e:
            print(f"  Error resolving batch {i//1000 + 1}: {e}")
        time.sleep(0.2)  # be polite to ESI
    return names


def main():
    print("Connecting to DB...")
    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, connect_timeout=10
    )
    cursor = conn.cursor()

    print("Fetching unknown type_ids...")
    cursor.execute("""
        SELECT DISTINCT type_id
        FROM market_transactions
        WHERE type_name IS NULL
           OR type_name = 'Unknown'
           OR type_name LIKE 'Unknown (%'
    """)
    rows = cursor.fetchall()
    type_ids = [row[0] for row in rows]

    if not type_ids:
        print("Nothing to backfill — all rows already have names.")
        conn.close()
        return

    print(f"Resolving {len(type_ids)} unique type IDs from ESI...")
    names = resolve_type_names(type_ids)
    print(f"Resolved {len(names)} names.")

    print("Updating DB...")
    updated = 0
    for type_id, name in names.items():
        cursor.execute(
            "UPDATE market_transactions SET type_name = %s WHERE type_id = %s",
            (name, type_id)
        )
        updated += cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()
    print(f"Done. Updated {updated} rows.")


if __name__ == "__main__":
    main()
