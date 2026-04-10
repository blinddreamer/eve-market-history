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


def resolve_type_names(type_ids):
    """
    Batch-resolve type_ids to item names via ESI /universe/names/.
    ESI accepts up to 1000 IDs per request.
    Returns a dict: {type_id: name}
    """
    if not type_ids:
        return {}

    names = {}
    ids = list(set(type_ids))

    for i in range(0, len(ids), 1000):
        batch = ids[i:i + 1000]
        try:
            response = requests.post(
                "https://esi.evetech.net/latest/universe/names/",
                json=batch,
                timeout=30
            )
            if response.status_code == 200:
                for entry in response.json():
                    if entry.get("category") == "inventory_type":
                        names[entry["id"]] = entry["name"]
            else:
                log.warning(f"ESI /universe/names/ returned {response.status_code} for batch of {len(batch)} IDs")
        except Exception:
            log.exception(f"Failed to resolve type names for batch starting at index {i}")

    log.info(f"Resolved {len(names)}/{len(ids)} item names from ESI.")
    return names


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

    # Resolve item names from type_ids via ESI
    type_ids = [tx["type_id"] for tx in transactions if "type_id" in tx]
    type_names = resolve_type_names(type_ids)

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
                type_id = tx["type_id"]
                values.append((
                    tx["transaction_id"],
                    character_id,
                    convert_datetime(tx["date"]),
                    type_id,
                    type_names.get(type_id, f"Unknown ({type_id})"),
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


def ensure_contracts_tables(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            contract_id           BIGINT PRIMARY KEY,
            character_id          BIGINT,
            issuer_id             BIGINT,
            issuer_corporation_id BIGINT,
            assignee_id           BIGINT,
            acceptor_id           BIGINT,
            start_location_id     BIGINT,
            end_location_id       BIGINT,
            type                  VARCHAR(50),
            status                VARCHAR(50),
            title                 VARCHAR(255),
            for_corporation       BOOLEAN,
            availability          VARCHAR(50),
            date_issued           DATETIME,
            date_expired          DATETIME,
            date_accepted         DATETIME,
            date_completed        DATETIME,
            days_to_complete      INT,
            price                 DOUBLE,
            reward                DOUBLE,
            collateral            DOUBLE,
            buyout                DOUBLE,
            volume                DOUBLE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contract_items (
            record_id    BIGINT PRIMARY KEY,
            contract_id  BIGINT,
            type_id      INT,
            type_name    VARCHAR(255),
            quantity     INT,
            raw_quantity INT,
            is_included  BOOLEAN,
            is_singleton BOOLEAN
        )
    """)


def fetch_contracts(access_token, character_id):
    """Fetch all personal contracts for a character, handling X-Pages pagination."""
    base_url = f"https://esi.evetech.net/latest/characters/{character_id}/contracts/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    all_contracts = []
    page = 1

    while True:
        response = requests.get(base_url, headers=headers, params={"page": page}, timeout=30)

        if response.status_code == 420:
            retry_after = int(response.headers.get("X-ESI-Error-Limit-Reset", 60))
            log.warning(f"ESI error limit hit fetching contracts for {character_id}. Waiting {retry_after}s...")
            time.sleep(retry_after)
            continue

        if response.status_code != 200:
            raise Exception(f"ESI returned HTTP {response.status_code}: {response.text}")

        page_data = response.json()
        if not page_data:
            break

        all_contracts.extend(page_data)

        total_pages = int(response.headers.get("X-Pages", 1))
        if page >= total_pages:
            break
        page += 1

    log.info(f"Fetched {len(all_contracts)} contracts for character {character_id}.")
    return all_contracts


def fetch_contract_items(access_token, character_id, contract_id):
    """Fetch items for a single contract. Returns empty list for courier/no-item contracts."""
    url = f"https://esi.evetech.net/latest/characters/{character_id}/contracts/{contract_id}/items/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code == 404:
        return []  # Courier contracts have no items endpoint

    if response.status_code == 420:
        retry_after = int(response.headers.get("X-ESI-Error-Limit-Reset", 60))
        log.warning(f"ESI rate limit hit fetching items for contract {contract_id}. Waiting {retry_after}s...")
        time.sleep(retry_after)
        return fetch_contract_items(access_token, character_id, contract_id)

    if response.status_code != 200:
        log.warning(f"Could not fetch items for contract {contract_id}: HTTP {response.status_code}")
        return []

    return response.json()


def save_contracts_to_mariadb(contracts, character_id, access_token):
    if not contracts:
        return

    log.info(f"Saving {len(contracts)} contracts for character {character_id}...")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        ensure_contracts_tables(cursor)

        contract_sql = """
            INSERT INTO contracts (
                contract_id, character_id, issuer_id, issuer_corporation_id,
                assignee_id, acceptor_id, start_location_id, end_location_id,
                type, status, title, for_corporation, availability,
                date_issued, date_expired, date_accepted, date_completed,
                days_to_complete, price, reward, collateral, buyout, volume
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            ON DUPLICATE KEY UPDATE
                status         = VALUES(status),
                date_accepted  = VALUES(date_accepted),
                date_completed = VALUES(date_completed),
                acceptor_id    = VALUES(acceptor_id)
        """

        # Find which contract_ids are new so we only fetch items for those
        existing_ids = set()
        if contracts:
            ids_placeholder = ",".join(["%s"] * len(contracts))
            cursor.execute(
                f"SELECT contract_id FROM contracts WHERE contract_id IN ({ids_placeholder})",
                [c["contract_id"] for c in contracts]
            )
            existing_ids = {row[0] for row in cursor.fetchall()}

        values = []
        skipped = 0
        for c in contracts:
            try:
                values.append((
                    c["contract_id"],
                    character_id,
                    c["issuer_id"],
                    c["issuer_corporation_id"],
                    c.get("assignee_id"),
                    c.get("acceptor_id"),
                    c.get("start_location_id"),
                    c.get("end_location_id"),
                    c.get("type"),
                    c.get("status"),
                    c.get("title", ""),
                    c.get("for_corporation", False),
                    c.get("availability"),
                    convert_datetime(c["date_issued"]),
                    convert_datetime(c["date_expired"]) if c.get("date_expired") else None,
                    convert_datetime(c["date_accepted"]) if c.get("date_accepted") else None,
                    convert_datetime(c["date_completed"]) if c.get("date_completed") else None,
                    c.get("days_to_complete"),
                    c.get("price", 0),
                    c.get("reward", 0),
                    c.get("collateral", 0),
                    c.get("buyout", 0),
                    c.get("volume", 0),
                ))
            except KeyError as e:
                log.warning(f"Skipping contract {c.get('contract_id', '?')}: missing field {e}")
                skipped += 1

        if values:
            cursor.executemany(contract_sql, values)
            conn.commit()
            log.info(
                f"Saved {len(values)} contracts for character {character_id}"
                + (f" ({skipped} skipped)" if skipped else "")
            )

        # Fetch and save items only for new item_exchange / auction contracts
        new_contracts = [
            c for c in contracts
            if c["contract_id"] not in existing_ids
            and c.get("type") in ("item_exchange", "auction")
        ]

        if new_contracts:
            log.info(f"Fetching items for {len(new_contracts)} new contracts...")
            all_items = []
            for c in new_contracts:
                items = fetch_contract_items(access_token, character_id, c["contract_id"])
                for item in items:
                    item["contract_id"] = c["contract_id"]
                all_items.extend(items)
                time.sleep(0.1)  # be polite to ESI

            if all_items:
                # Resolve type names
                type_ids = [i["type_id"] for i in all_items if "type_id" in i]
                type_names = resolve_type_names(type_ids)

                item_sql = """
                    INSERT IGNORE INTO contract_items (
                        record_id, contract_id, type_id, type_name,
                        quantity, raw_quantity, is_included, is_singleton
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """
                item_values = []
                for item in all_items:
                    try:
                        type_id = item["type_id"]
                        item_values.append((
                            item["record_id"],
                            item["contract_id"],
                            type_id,
                            type_names.get(type_id, f"Unknown ({type_id})"),
                            item.get("quantity", 1),
                            item.get("raw_quantity"),
                            item.get("is_included", True),
                            item.get("is_singleton", False),
                        ))
                    except KeyError as e:
                        log.warning(f"Skipping contract item: missing field {e}")

                if item_values:
                    cursor.executemany(item_sql, item_values)
                    conn.commit()
                    log.info(f"Saved {len(item_values)} contract items.")

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

                contracts = fetch_contracts(access_token, char_id)
                if contracts:
                    save_contracts_to_mariadb(contracts, char_id, access_token)
                else:
                    log.warning(f"No contracts returned for character {char_id}.")
            except Exception:
                log.exception(f"Error processing character {char_id}")

        next_run = datetime.fromtimestamp(time.time() + FETCH_INTERVAL_HOURS * 3600)
        log.info(f"Cycle complete. Next run at {next_run} (in {FETCH_INTERVAL_HOURS}h).")
        time.sleep(FETCH_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    run_fetcher()
