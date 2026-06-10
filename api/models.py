"""Pydantic response models for the API."""
from __future__ import annotations

from pydantic import BaseModel


class Product(BaseModel):
    id: int
    name: str
    category: str
    price: float
    stock: int


class SalesSummary(BaseModel):
    total_sales: int
    total_units: int
    total_revenue: float


class TopProduct(BaseModel):
    id: int
    name: str
    category: str
    units_sold: int
    revenue: float
