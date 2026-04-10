"""
EVE Market History — Metabase Dashboard Setup
Run once to create all questions and a dashboard automatically.

The dashboard has a "Days" filter (default 30) — type 30, 60, or 90 to switch periods.

Usage:
    python setup_metabase.py
"""

import requests
import getpass
import sys
import uuid

METABASE_URL = "http://192.168.250.106:5546"
DATABASE_ID = 2

# Shared dashboard filter UUID — links the "Days" filter to all cards
FILTER_UUID = str(uuid.uuid4())


def isk_fmt(*col_names):
    """Metabase column_settings to display ISK values in billions."""
    return {
        "column_settings": {
            f'["name","{col}"]': {
                "number_style": "decimal",
                "decimals": 2,
                "scale": 1e-9,
                "suffix": " B",
            }
            for col in col_names
        }
    }


def scalar_isk_fmt():
    """Metabase viz settings for a scalar ISK card displayed in billions."""
    return {
        "number_style": "decimal",
        "decimals": 2,
        "scale": 1e-9,
        "suffix": " B ISK",
    }


def days_tag():
    """Return a fresh template-tag definition for the {{days}} variable."""
    return {
        "id": str(uuid.uuid4()),
        "name": "days",
        "display-name": "Days",
        "type": "number",
        "default": 30,
        "required": True,
    }


# ---------------------------------------------------------------------------
# Card definitions
# Each SQL uses {{days}} so the dashboard filter controls the time window.
# ---------------------------------------------------------------------------

def build_queries():
    return [

        # ── Scalars ──────────────────────────────────────────────────────────

        {
            "name": "Total ISK Spent (Buys)",
            "sql": """
SELECT SUM(quantity * unit_price) AS isk_spent
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
  AND is_buy_order = 1
            """.strip(),
            "display": "scalar",
            "viz": scalar_isk_fmt(),
            "layout": {"row": 0, "col": 0, "size_x": 6, "size_y": 4},
        },
        {
            "name": "Total ISK Earned (Sells)",
            "sql": """
SELECT SUM(quantity * unit_price) AS isk_earned
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
  AND is_buy_order = 0
            """.strip(),
            "display": "scalar",
            "viz": scalar_isk_fmt(),
            "layout": {"row": 0, "col": 6, "size_x": 6, "size_y": 4},
        },
        {
            "name": "Net Profit",
            "sql": """
SELECT
    SUM(CASE WHEN is_buy_order = 0 THEN quantity * unit_price ELSE 0 END) -
    SUM(CASE WHEN is_buy_order = 1 THEN quantity * unit_price ELSE 0 END) AS net_profit
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
            """.strip(),
            "display": "scalar",
            "viz": scalar_isk_fmt(),
            "layout": {"row": 0, "col": 12, "size_x": 6, "size_y": 4},
        },
        {
            "name": "Total Transactions",
            "sql": """
SELECT COUNT(*) AS total_transactions
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
            """.strip(),
            "display": "scalar",
            "viz": {},
            "layout": {"row": 0, "col": 18, "size_x": 3, "size_y": 4},
        },
        {
            "name": "Avg Daily Profit",
            "sql": """
SELECT ROUND(
    (
        SUM(CASE WHEN is_buy_order = 0 THEN quantity * unit_price ELSE 0 END) -
        SUM(CASE WHEN is_buy_order = 1 THEN quantity * unit_price ELSE 0 END)
    ) / {{days}}, 0
) AS avg_daily_profit
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
            """.strip(),
            "display": "scalar",
            "viz": scalar_isk_fmt(),
            "layout": {"row": 0, "col": 21, "size_x": 3, "size_y": 4},
        },

        # ── Daily Buy / Sell / Profit bar chart ──────────────────────────────

        {
            "name": "Daily ISK Spent vs Earned vs Profit",
            "sql": """
SELECT
    DATE(date) AS day,
    SUM(CASE WHEN is_buy_order = 1 THEN quantity * unit_price ELSE 0 END) AS isk_spent,
    SUM(CASE WHEN is_buy_order = 0 THEN quantity * unit_price ELSE 0 END) AS isk_earned,
    SUM(CASE WHEN is_buy_order = 0 THEN quantity * unit_price ELSE 0 END) -
    SUM(CASE WHEN is_buy_order = 1 THEN quantity * unit_price ELSE 0 END) AS daily_profit
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
GROUP BY DATE(date)
ORDER BY day ASC
            """.strip(),
            "display": "bar",
            "viz": {
                "graph.dimensions": ["day"],
                "graph.metrics": ["isk_spent", "isk_earned", "daily_profit"],
                "graph.x_axis.title_text": "Date",
                "graph.y_axis.title_text": "ISK (Billions)",
                **isk_fmt("isk_spent", "isk_earned", "daily_profit"),
            },
            "layout": {"row": 4, "col": 0, "size_x": 24, "size_y": 9},
        },

        # ── Cumulative profit line ────────────────────────────────────────────

        {
            "name": "Cumulative Profit",
            "sql": """
SELECT
    day,
    SUM(daily_profit) OVER (ORDER BY day ASC) AS cumulative_profit
FROM (
    SELECT
        DATE(date) AS day,
        SUM(CASE WHEN is_buy_order = 0 THEN quantity * unit_price ELSE 0 END) -
        SUM(CASE WHEN is_buy_order = 1 THEN quantity * unit_price ELSE 0 END) AS daily_profit
    FROM market_transactions
    WHERE date >= NOW() - INTERVAL {{days}} DAY
    GROUP BY DATE(date)
) AS daily
ORDER BY day ASC
            """.strip(),
            "display": "line",
            "viz": {
                "graph.dimensions": ["day"],
                "graph.metrics": ["cumulative_profit"],
                "graph.x_axis.title_text": "Date",
                "graph.y_axis.title_text": "ISK (Billions)",
                **isk_fmt("cumulative_profit"),
            },
            "layout": {"row": 13, "col": 0, "size_x": 24, "size_y": 9},
        },

        # ── Top items ─────────────────────────────────────────────────────────

        {
            "name": "Top 10 Items by ISK Earned",
            "sql": """
SELECT
    type_name,
    SUM(quantity * unit_price) AS isk_earned,
    SUM(quantity)              AS units_sold,
    COUNT(*)                   AS sell_orders
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
  AND is_buy_order = 0
GROUP BY type_name
ORDER BY isk_earned DESC
LIMIT 10
            """.strip(),
            "display": "bar",
            "viz": {
                "graph.dimensions": ["type_name"],
                "graph.metrics": ["isk_earned"],
                "graph.x_axis.title_text": "Item",
                "graph.y_axis.title_text": "ISK Earned (Billions)",
                **isk_fmt("isk_earned"),
            },
            "layout": {"row": 22, "col": 0, "size_x": 12, "size_y": 8},
        },
        {
            "name": "Top 10 Items by ISK Spent",
            "sql": """
SELECT
    type_name,
    SUM(quantity * unit_price) AS isk_spent,
    SUM(quantity)              AS units_bought,
    COUNT(*)                   AS buy_orders
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
  AND is_buy_order = 1
GROUP BY type_name
ORDER BY isk_spent DESC
LIMIT 10
            """.strip(),
            "display": "bar",
            "viz": {
                "graph.dimensions": ["type_name"],
                "graph.metrics": ["isk_spent"],
                "graph.x_axis.title_text": "Item",
                "graph.y_axis.title_text": "ISK Spent (Billions)",
                **isk_fmt("isk_spent"),
            },
            "layout": {"row": 22, "col": 12, "size_x": 12, "size_y": 8},
        },

        # ── Activity charts ───────────────────────────────────────────────────

        {
            "name": "Daily Transaction Count",
            "sql": """
SELECT
    DATE(date) AS day,
    COUNT(CASE WHEN is_buy_order = 1 THEN 1 END) AS buy_orders,
    COUNT(CASE WHEN is_buy_order = 0 THEN 1 END) AS sell_orders
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
GROUP BY DATE(date)
ORDER BY day ASC
            """.strip(),
            "display": "line",
            "viz": {
                "graph.dimensions": ["day"],
                "graph.metrics": ["buy_orders", "sell_orders"],
                "graph.x_axis.title_text": "Date",
                "graph.y_axis.title_text": "Number of Orders",
            },
            "layout": {"row": 30, "col": 0, "size_x": 12, "size_y": 8},
        },
        {
            "name": "Most Active Trading Hours",
            "sql": """
SELECT
    HOUR(date) AS hour_of_day,
    COUNT(*)   AS transactions,
    SUM(quantity * unit_price) AS total_isk
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
GROUP BY HOUR(date)
ORDER BY hour_of_day ASC
            """.strip(),
            "display": "bar",
            "viz": {
                "graph.dimensions": ["hour_of_day"],
                "graph.metrics": ["transactions"],
                "graph.x_axis.title_text": "Hour (UTC)",
                "graph.y_axis.title_text": "Number of Transactions",
            },
            "layout": {"row": 30, "col": 12, "size_x": 12, "size_y": 8},
        },

        # ── Volume & margin ───────────────────────────────────────────────────

        {
            "name": "Top 10 Items by Quantity Traded",
            "sql": """
SELECT
    type_name,
    SUM(CASE WHEN is_buy_order = 1 THEN quantity ELSE 0 END) AS bought,
    SUM(CASE WHEN is_buy_order = 0 THEN quantity ELSE 0 END) AS sold,
    SUM(quantity) AS total_volume
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
GROUP BY type_name
ORDER BY total_volume DESC
LIMIT 10
            """.strip(),
            "display": "bar",
            "viz": {
                "graph.dimensions": ["type_name"],
                "graph.metrics": ["bought", "sold"],
                "graph.x_axis.title_text": "Item",
                "graph.y_axis.title_text": "Quantity",
            },
            "layout": {"row": 38, "col": 0, "size_x": 24, "size_y": 8},
        },
        {
            "name": "Avg Unit Price per Item (Sells)",
            "sql": """
SELECT
    type_name,
    ROUND(AVG(unit_price), 2)        AS avg_sell_price,
    ROUND(MIN(unit_price), 2)        AS min_sell_price,
    ROUND(MAX(unit_price), 2)        AS max_sell_price,
    SUM(quantity * unit_price)       AS total_isk,
    SUM(quantity)                    AS total_units
FROM market_transactions
WHERE date >= NOW() - INTERVAL {{days}} DAY
  AND is_buy_order = 0
GROUP BY type_name
HAVING SUM(quantity) > 1
ORDER BY total_isk DESC
LIMIT 15
            """.strip(),
            "display": "table",
            "viz": {},
            "layout": {"row": 46, "col": 0, "size_x": 24, "size_y": 8},
        },
    ]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def get_session(email, password):
    r = requests.post(
        f"{METABASE_URL}/api/session",
        json={"username": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"Login failed: {r.text}")
        sys.exit(1)
    return r.json()["id"]


def create_card(session_token, q, tag):
    headers = {"X-Metabase-Session": session_token}
    payload = {
        "name": q["name"],
        "dataset_query": {
            "type": "native",
            "native": {
                "query": q["sql"],
                "template-tags": {"days": tag},
            },
            "database": DATABASE_ID,
        },
        "display": q["display"],
        "visualization_settings": q["viz"],
    }
    r = requests.post(f"{METABASE_URL}/api/card", json=payload, headers=headers, timeout=15)
    if r.status_code not in (200, 202):
        print(f"  Failed to create card '{q['name']}': {r.text}")
        return None, None
    card_id = r.json()["id"]
    print(f"  Created card [{card_id}]: {q['name']}")
    return card_id, tag["id"]


def create_dashboard(session_token, name):
    headers = {"X-Metabase-Session": session_token}
    dashboard_payload = {
        "name": name,
        "parameters": [
            {
                "id": FILTER_UUID,
                "name": "Days",
                "slug": "days",
                "type": "number/=",
                "default": 30,
            }
        ],
    }
    r = requests.post(f"{METABASE_URL}/api/dashboard", json=dashboard_payload, headers=headers, timeout=15)
    r.raise_for_status()
    dashboard_id = r.json()["id"]
    print(f"Created dashboard [{dashboard_id}]: {name}")
    return dashboard_id


def add_cards_to_dashboard(session_token, dashboard_id, cards):
    """
    cards: list of (card_id, tag_uuid, layout_dict)
    Uses PUT /api/dashboard/:id/cards (Metabase v47+), falls back to POST per card.
    """
    headers = {"X-Metabase-Session": session_token}

    dashcards = [
        {
            "id": -(i + 1),
            "card_id": card_id,
            "row": layout["row"],
            "col": layout["col"],
            "size_x": layout["size_x"],
            "size_y": layout["size_y"],
            "series": [],
            "visualization_settings": {},
            "parameter_mappings": [
                {
                    "parameter_id": FILTER_UUID,
                    "card_id": card_id,
                    "target": ["variable", ["template-tag", "days"]],
                }
            ],
        }
        for i, (card_id, tag_uuid, layout) in enumerate(cards)
        if card_id is not None
    ]

    r = requests.put(
        f"{METABASE_URL}/api/dashboard/{dashboard_id}/cards",
        json={"cards": dashcards},
        headers=headers,
        timeout=15,
    )

    if r.status_code == 200:
        print(f"Added {len(dashcards)} cards to dashboard with filter wired up.")
        return

    # Fallback: POST one at a time (older Metabase)
    print("Falling back to per-card POST...")
    for card_id, tag_uuid, layout in cards:
        if card_id is None:
            continue
        payload = {
            "cardId": card_id,
            **layout,
            "parameter_mappings": [
                {
                    "parameter_id": FILTER_UUID,
                    "card_id": card_id,
                    "target": ["variable", ["template-tag", "days"]],
                }
            ],
        }
        r2 = requests.post(
            f"{METABASE_URL}/api/dashboard/{dashboard_id}/cards",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if r2.status_code not in (200, 202):
            print(f"  Failed to add card {card_id}: {r2.text}")
        else:
            print(f"  Added card {card_id}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== EVE Market History — Metabase Setup ===")
    print(f"Target: {METABASE_URL}  |  Database ID: {DATABASE_ID}\n")

    email = input("Metabase email: ").strip()
    password = getpass.getpass("Metabase password: ")

    print("\nLogging in...")
    session = get_session(email, password)
    print("Login successful.\n")

    QUERIES = build_queries()

    print("Creating questions...")
    tags = [days_tag() for _ in QUERIES]
    results = [create_card(session, q, tag) for q, tag in zip(QUERIES, tags)]

    print("\nCreating dashboard...")
    dashboard_id = create_dashboard(session, "EVE Market Overview")

    print("\nAdding cards to dashboard...")
    add_cards_to_dashboard(
        session,
        dashboard_id,
        [(card_id, tag_uuid, q["layout"]) for (card_id, tag_uuid), q in zip(results, QUERIES)],
    )

    print(f"\nDone! Open your dashboard:")
    print(f"  {METABASE_URL}/dashboard/{dashboard_id}")
    print(f"\nUse the 'Days' filter at the top to switch between 30 / 60 / 90 days.")


if __name__ == "__main__":
    main()
