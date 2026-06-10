"""FastAPI app serving random product/sales data from SQLite.

This is the "backend of record" — the MCP server talks to it over HTTP,
and the agent never touches it directly. Keeping the data behind a real
HTTP API (rather than letting the MCP server read SQLite directly) makes
the PoC mirror a realistic deployment where the data service and the MCP
adapter are separate concerns.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query

from database import cursor, init_db
from models import Product, SalesSummary, TopProduct
from seed import seed

app = FastAPI(
    title="Demo Data API",
    description="Serves random product and sales data from SQLite.",
    version="1.0.0",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Seed automatically on first boot if the DB is empty (handy in containers).
    if os.environ.get("AUTO_SEED", "1") == "1":
        with cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM products")
            if cur.fetchone()["n"] == 0:
                seed()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/products", response_model=list[Product])
def list_products(
    category: str | None = Query(default=None, description="Filter by category"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Product]:
    with cursor() as cur:
        if category:
            cur.execute(
                "SELECT * FROM products WHERE category = ? ORDER BY id LIMIT ?",
                (category, limit),
            )
        else:
            cur.execute("SELECT * FROM products ORDER BY id LIMIT ?", (limit,))
        return [Product(**dict(row)) for row in cur.fetchall()]


@app.get("/products/search", response_model=list[Product])
def search_products(
    q: str | None = Query(default=None, description="Substring match on name"),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Product]:
    clauses: list[str] = []
    params: list[object] = []
    if q:
        clauses.append("name LIKE ?")
        params.append(f"%{q}%")
    if min_price is not None:
        clauses.append("price >= ?")
        params.append(min_price)
    if max_price is not None:
        clauses.append("price <= ?")
        params.append(max_price)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with cursor() as cur:
        cur.execute(f"SELECT * FROM products {where} ORDER BY price LIMIT ?", params)
        return [Product(**dict(row)) for row in cur.fetchall()]


@app.get("/products/{product_id}", response_model=Product)
def get_product(product_id: int) -> Product:
    with cursor() as cur:
        cur.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return Product(**dict(row))


@app.get("/sales/summary", response_model=SalesSummary)
def sales_summary() -> SalesSummary:
    with cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(quantity), 0) AS units, "
            "COALESCE(SUM(revenue), 0) AS rev FROM sales"
        )
        row = cur.fetchone()
    return SalesSummary(
        total_sales=row["n"],
        total_units=row["units"],
        total_revenue=round(row["rev"], 2),
    )


@app.get("/sales/top", response_model=list[TopProduct])
def top_products(limit: int = Query(default=5, ge=1, le=50)) -> list[TopProduct]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.name, p.category,
                   SUM(s.quantity) AS units_sold,
                   SUM(s.revenue)  AS revenue
            FROM sales s
            JOIN products p ON p.id = s.product_id
            GROUP BY p.id
            ORDER BY revenue DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            TopProduct(
                id=row["id"],
                name=row["name"],
                category=row["category"],
                units_sold=row["units_sold"],
                revenue=round(row["revenue"], 2),
            )
            for row in cur.fetchall()
        ]
