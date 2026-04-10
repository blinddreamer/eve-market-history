import requests
import pymysql
import os
import time
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


def load_characters():
    """Dynamically load characters from env vars: CLIENT_ID_1, CLIENT_ID_2, ..."""
    characters = []
    i = 1
    while True:
        client_id = os.getenv(f"CLIENT_ID_{i}")
        if not client_id:
            break
        characters.append({
            "CLIENT_ID": client_id,
            "CLIENT_SECRET": os.getenv(f"CLIENT_SECRET_{i}"),
            "REFRESH_TOKEN": os.getenv(f"REFRESH_TOKEN_{i}"),
            "CHARACTER_ID": os.getenv(f"CHARACTER_ID_{i}")
        })
        i += 1
    return characters


CHARACTERS = load_characters()

DB_HOST = os.getenv("DB_HOST", "borg")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "123")
DB_NAME = os.getenv("DB_NAME", "eve")

FETCH_INTERVAL_HOURS = int(os.getenv("FETCH_INTERVAL_HOURS", "24"))


def get_access_token(client_id, client_secret, refresh_token):
    url = "https://login.eveonline.com/v2/oauth/token"
    response = requests.post(url, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }, timeout=30)

    if not response.ok:
        raise Exception(f"Token endpoint returned HTTP {response.status_code}: {response.text}")

    tokens = response.json()
    if "access_token" not in tokens:
        raise Exception(f"No access_token in response: {tokens}")
    return tokens["access_token"]


def fetch_transactions(access_token, character_id):
    """
    Fetch all wallet transactions for a character, handling ESI pagination.
    ESI wallet/transactions uses from_id to page through older records.
    """
    base_url = f"https://esi.evetech.net/latest/characters/{character_id}/wallet/transactions/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    all_transactions = []
    from_id = None

    while True:
        params = {}
        if from_id is not None:
            params["from_id"] = from_id

        response = requests.get(base_url, headers=headers, params=params, timeout=30)

        if response.status_code == 420:
            retry_after = int(response.headers.get("X-ESI-Error-Limit-Reset", 60))
            log.warning(f"ESI error limit reached for character {character_id}. Waiting {retry_after}s...")
            time.sleep(retry_after)
            continue

        if response.status_code != 200:
            raise Exception(f"ESI returned HTTP {response.status_code}: {response.text}")

        page_data = response.json()
        if not page_data:
            break

        all_transactions.extend(page_data)

        # ESI returns up to 2500 per page; if we got a full page, fetch older records
        if len(page_data) < 2500:
            break

        from_id = min(tx["transaction_id"] for tx in page_data)
        log.info(f"  Fetched {len(all_transactions)} transactions so far, continuing from id {from_id}...")

    log.info(f"Fetched {len(all_transactions)} total transactions for character {character_id}.")
    return all_transactions


def convert_datetime(iso_date):
    try:
        return datetime.strptime(iso_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        log.warning(f"Could not parse datetime: {iso_date!r}")
        return None


def get_db_connection():
    for attempt in range(1, 4):
        try:
            return pymysql.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                connect_timeout=10
            )
        except pymysql.err.OperationalError as e:
            log.warning(f"DB connection failed (attempt {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(3)
            else:
                raise


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_transactions (
            transaction_id BIGINT PRIMARY KEY,
            character_id BIGINT,
            date DATETIME,
            type_id INT,
            type_name VARCHAR(255),
            unit_price DOUBLE,
            quantity INT,
            client_id BIGINT,
            location_id BIGINT,
            is_buy_order BOOLEAN
        )
    """)
    # Migration: add character_id if this table existed before this column was introduced
    cursor.execute("""
        ALTER TABLE market_transactions
        ADD COLUMN IF NOT EXISTS character_id BIGINT AFTER transaction_id
    """)


def save_to_mariadb(transactions, character_id):
    log.info(f"Saving {len(transactions)} transactions for character {character_id}...")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        ensure_table(cursor)

        sql = """
            INSERT INTO market_transactions (
                transaction_id, character_id, date, type_id, type_name,
                unit_price, quantity, client_id, location_id, is_buy_order
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                character_id  = VALUES(character_id),
                date          = VALUES(date),
                type_id       = VALUES(type_id),
                type_name     = VALUES(type_name),
                unit_price    = VALUES(unit_price),
                quantity      = VALUES(quantity),
                client_id     = VALUES(client_id),
                location_id   = VALUES(location_id),
                is_buy_order  = VALUES(is_buy_order)
        """

        values = []
        skipped = 0
        for tx in transactions:
            try:
                values.append((
                    tx["transaction_id"],
                    character_id,
                    convert_datetime(tx["date"]),
                    tx["type_id"],
                    tx.get("type_name", "Unknown"),
                    tx["unit_price"],
                    tx["quantity"],
                    tx["client_id"],
                    tx["location_id"],
                    tx["is_buy"]
                ))
            except KeyError as e:
                log.warning(f"Skipping transaction {tx.get('transaction_id', '?')}: missing field {e}")
                skipped += 1

        if values:
            cursor.executemany(sql, values)
            conn.commit()
            log.info(
                f"Saved {len(values)} transactions for character {character_id}"
                + (f" ({skipped} skipped due to missing fields)" if skipped else "")
            )
    finally:
        cursor.close()
        conn.close()


def run_fetcher():
    if not CHARACTERS:
        log.error(
            "No characters configured. "
            "Set CLIENT_ID_1, CLIENT_SECRET_1, REFRESH_TOKEN_1, CHARACTER_ID_1 (and _2, _3, ...) env vars."
        )
        return

    log.info(f"Fetcher started — {len(CHARACTERS)} character(s) configured.")

    while True:
        log.info(f"Starting fetch cycle at {datetime.now()}")

        for character in CHARACTERS:
            char_id = character["CHARACTER_ID"]
            try:
                access_token = get_access_token(
                    character["CLIENT_ID"],
                    character["CLIENT_SECRET"],
                    character["REFRESH_TOKEN"]
                )
                transactions = fetch_transactions(access_token, char_id)
                if transactions:
                    save_to_mariadb(transactions, char_id)
                else:
                    log.warning(f"No transactions returned for character {char_id}.")
            except Exception:
                log.exception(f"Error processing character {char_id}")

        next_run = datetime.fromtimestamp(time.time() + FETCH_INTERVAL_HOURS * 3600)
        log.info(f"Cycle complete. Next run at {next_run} (in {FETCH_INTERVAL_HOURS}h).")
        time.sleep(FETCH_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    run_fetcher()
