import requests
import pymysql
import os
from datetime import datetime

# Debugging: Print all environment variables
print("📌 Environment Variables Loaded in Python:")
for key, value in os.environ.items():
    print(f"{key}={value}")

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
    """Fetch a new access token using the refresh token."""
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
    """Retrieve market transactions from the ESI API."""
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
    """Convert ISO 8601 datetime format to MySQL datetime format."""
    try:
        return datetime.strptime(iso_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None  

def save_to_mariadb(transactions):
    """Save transaction data into MariaDB."""
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)
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

    for tx in transactions:
        transaction_date = convert_datetime(tx["date"])

        sql = """
            INSERT INTO market_transactions (
                transaction_id, date, type_id, type_name, unit_price, quantity,
                client_id, location_id, is_buy_order
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE date=date
        """
        cursor.execute(sql, (
            tx["transaction_id"],
            transaction_date,  
            tx["type_id"],
            tx.get("type_name", "Unknown"),  
            tx["unit_price"],
            tx["quantity"],
            tx["client_id"],
            tx["location_id"],
            tx["is_buy"]
        ))

    conn.commit()
    cursor.close()
    conn.close()

if __name__ == "__main__":
    for character in CHARACTERS:
        if not character["CLIENT_ID"]:  # Skip if env vars are not set
            print(f"⚠️ Skipping character due to missing environment variables.")
            continue

        print(f"🔄 Refreshing access token for {character['CHARACTER_ID']}...")
        access_token = get_access_token(character["CLIENT_ID"], character["CLIENT_SECRET"], character["REFRESH_TOKEN"])
        
        print(f"📥 Fetching transactions for {character['CHARACTER_ID']} from EVE API...")
        transactions = fetch_transactions(access_token, character["CHARACTER_ID"])
        
        if transactions:
            print(f"💾 Storing {len(transactions)} transactions for {character['CHARACTER_ID']} in MariaDB...")
            save_to_mariadb(transactions)
            print(f"✅ Data for {character['CHARACTER_ID']} successfully saved!")
        else:
            print(f"⚠️ No new transactions found for {character['CHARACTER_ID']}.")
