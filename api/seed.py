"""Seed the SQLite database with random demo data.

Idempotent: running it again wipes and regenerates the rows so the API
always has something to serve. Uses a fixed RNG seed so demos are
reproducible.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from database import cursor, init_db

random.seed(42)

CATEGORIES = ["Electronics", "Books", "Home", "Toys", "Sports", "Grocery"]
ADJECTIVES = ["Smart", "Eco", "Pro", "Mini", "Ultra", "Classic", "Premium", "Basic"]
NOUNS = ["Widget", "Gadget", "Lamp", "Bottle", "Speaker", "Chair", "Ball", "Notebook"]


def random_product_name() -> str:
    return f"{random.choice(ADJECTIVES)} {random.choice(NOUNS)}"


def seed(num_products: int = 30, num_sales: int = 200) -> None:
    init_db()
    with cursor() as cur:
        cur.execute("DELETE FROM sales")
        cur.execute("DELETE FROM products")

        product_ids: list[int] = []
        for _ in range(num_products):
            name = random_product_name()
            category = random.choice(CATEGORIES)
            price = round(random.uniform(5, 500), 2)
            stock = random.randint(0, 250)
            cur.execute(
                "INSERT INTO products (name, category, price, stock) VALUES (?, ?, ?, ?)",
                (name, category, price, stock),
            )
            product_ids.append(cur.lastrowid)

        # Pre-fetch prices to compute revenue per sale.
        cur.execute("SELECT id, price FROM products")
        price_by_id = {row["id"]: row["price"] for row in cur.fetchall()}

        now = datetime.utcnow()
        for _ in range(num_sales):
            pid = random.choice(product_ids)
            qty = random.randint(1, 10)
            sold_at = now - timedelta(days=random.randint(0, 90))
            revenue = round(qty * price_by_id[pid], 2)
            cur.execute(
                "INSERT INTO sales (product_id, quantity, sold_at, revenue) VALUES (?, ?, ?, ?)",
                (pid, qty, sold_at.isoformat(), revenue),
            )

    print(f"Seeded {num_products} products and {num_sales} sales.")


if __name__ == "__main__":
    seed()
