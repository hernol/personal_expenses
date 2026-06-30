from __future__ import annotations

from datetime import date
from enum import StrEnum
from pydantic import BaseModel, Field


class Currency(StrEnum):
    ARS = 'ARS'
    USD = 'USD'


class Transaction(BaseModel):
    date: date
    merchant: str
    amount: float
    currency: Currency
    installment: str | None = None
    voucher: str | None = None
    raw: str | None = None


class Statement(BaseModel):
    card_brand: str
    # Last 4 digits of the card number, when the PDF/TXT exposes them (masked or unmasked).
    # Example: "3095".
    card_last4: str | None = None
    closing_date: date | None = None
    due_date: date | None = None
    total_to_pay_ars: float | None = None
    usd_balance: float | None = None
    transactions: list[Transaction] = Field(default_factory=list)


class CategoryItem(BaseModel):
    provider: str
    merchant: str
    amount: float
    currency: Currency
    date: date
    confidence: float = 1.0


class CategoryReport(BaseModel):
    name: str
    provider_count: int
    total_ars: float = 0
    total_usd: float = 0
    items: list[CategoryItem] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    summary: dict
    categories: list[CategoryReport]
    recurring_candidates: list[CategoryItem]
    recommendations: list[str]
