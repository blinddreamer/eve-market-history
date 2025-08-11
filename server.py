import requests
import pymysql
import os
import time
from datetime import datetime
import sys
import traceback

print("🐍 Script import started...")  # DEBUG 0

# Load character details from environment variables
CHARACTERS = [
    {
        "CLIENT_ID": os.getenv("CLIENT_ID_1"),
        "CLIENT_SECRET": os.getenv("CLIENT_SECRET_1"),
        "REFRESH_TOKEN": os.getenv("REFRESH_TOKEN_1"),
        "CHARACTER_ID": os.getenv("CHARACTER_ID_1")
    },
    {
        "CLIENT_ID": os.getenv("CLIENT_ID_2"),
        "CLIENT_SECRET": os.getenv("CLIENT_SECRET_2"),
        "REFRESH_TOKEN": os.getenv("REFRESH_TOKEN_2"),
        "CHARACTER_ID": os.getenv("CHARACTER_ID_2")
    }
]

# MariaDB Connection Details
DB_HOST = os.getenv("DB_HOST", "borg")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "123")
DB_NAME = os.getenv("DB_NAME", "eve")


def get_access_token(client_id, client_secret, refresh_token):
    print(f"🔑 Getting access token for {client_id}...")  # DEBUG
    url = "https://login.eveonline.com/v2/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }

    response = requests.post(url, data=data)
    tokens = response.json()

    if "access_token" in tokens:
        return tokens["access_token"]
    else:
        raise Exception(f"Failed to refresh token: {tokens}")


def fetch_transactions(access_token, character_id):
    print(f"🌐 Fetching transactions for char {character_id}...")  # DEBUG
    url = f"https://esi.evetech.net/latest/characters/{character_id}/wallet/transactions/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to fetch transactions: {response.text}")


def convert_datetime(iso_date):
    try:
        return datetime.strptime(iso_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def save_to_mariadb(transactions):
    print(f"💾 Saving {len(transactions)} transactions...")  # DEBUG
    attempts = 3
    for attempt in range(attempts):
        try:
            conn = pymysql.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                connect_timeout=10
            )
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_transactions (
                    transaction_id BIGINT PRIMARY KEY,
                    date DATETIME,
                    type_id INT,
                    type_name VARCHAR(255),
                    unit_price FLOAT,
                    quantity INT,
                    client_id BIGINT,
                    location_id BIGINT,
                    is_buy_order BOOLEAN
                )
            """)

            sql = """
                INSERT INTO market_transactions (
                    transaction_id, date, type_id, type_name, unit_price, quantity,
                    client_id, location_id, is_buy_order
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    date = VALUES(date),
                    type_id = VALUES(type_id),
                    type_name = VALUES(type_name),
                    unit_price = VALUES(unit_price),
                    quantity = VALUES(quantity),
                    client_id = VALUES(client_id),
                    location_id = VALUES(location_id),
                    is_buy_order = VALUES(is_buy_order)
            """

            values = [
                (
                    tx["transaction_id"],
                    convert_datetime(tx["date"]),
                    tx["type_id"],
                    tx.get("type_name", "Unknown"),
                    tx["unit_price"],
                    tx["quantity"],
                    tx["client_id"],
                    tx["location_id"],
                    tx["is_buy"]
                )
                for tx in transactions
            ]

            if values:
                cursor.executemany(sql, values)
                print(f"✅ {len(values)} transactions processed.")

            conn.commit()
            cursor.close()
            conn.close()
            return

        except pymysql.err.OperationalError as e:
            print(f"⚠️ DB connection lost (attempt {attempt+1}/{attempts}): {e}")
            if attempt < attempts - 1:
                time.sleep(3)
            else:
                raise


def run_fetcher():
    print(f"🚀 Fetcher started at {datetime.now()} — first run starting now.")  # DEBUG start

    while True:
        print(f"⏳ Starting transaction fetch at {datetime.now()}...")

        for character in CHARACTERS:
            if not character["CLIENT_ID"]:
                print(f"⚠️ Skipping character due to missing environment variables.")
                continue

            try:
                access_token = get_access_token(character["CLIENT_ID"], character["CLIENT_SECRET"], character["REFRESH_TOKEN"])
                transactions = fetch_transactions(access_token, character["CHARACTER_ID"])
                if transactions:
                    save_to_mariadb(transactions)
                else:
                    print(f"⚠️ No new transactions for {character['CHARACTER_ID']}.")
            except Exception as e:
                print(f"❌ Error processing character {character['CHARACTER_ID']}: {e}")
                traceback.print_exc(file=sys.stdout)

        for remaining in range(86400, 0, -3600):
            print(f"🕒 Next run in {remaining // 3600} hour(s)...")
            time.sleep(3600)


if __name__ == "__main__":
    print("📢 Script running directly, calling run_fetcher()")  # DEBUG
    run_fetcher()
