"""Deterministic catalog from store/seed.json (the P0 store)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from .models import Product

SEED_PATH = Path(__file__).with_name("seed.json")


def _cents(amount: float | str) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))


class MockCatalog:
    name = "mock"

    def __init__(self, seed_path: Path | None = None) -> None:
        self._seed_path = seed_path or SEED_PATH
        self.merchant = "freshmart"
        self._products: dict[str, Product] = {}
        self._load()

    def _load(self) -> None:
        seed = json.loads(self._seed_path.read_text())
        self.merchant = seed.get("merchant", "freshmart")
        currency = seed.get("currency", "USD")
        self._products = {}
        for raw in seed["products"]:
            product = Product(
                sku=raw["sku"],
                name=raw["name"],
                price_cents=_cents(raw["price"]),
                currency=raw.get("currency", currency),
                category=raw.get("category", "general"),
                merchant=self.merchant,
                description=raw.get("description", ""),
                tags=tuple(raw.get("tags", ())),
                image_url=raw.get("image_url"),
            )
            self._products[product.sku.upper()] = product

    @staticmethod
    def _score(product: Product, tokens: list[str]) -> int:
        name = product.name.lower()
        tags = " ".join(product.tags).lower()
        description = product.description.lower()
        score = 0
        for token in tokens:
            if token in product.sku.lower():
                score += 4
            if token in name:
                score += 3
            if token in tags or token in product.category.lower():
                score += 2
            if token in description:
                score += 1
        return score

    async def search_products(self, query: str, category: str | None = None, limit: int = 8) -> list[Product]:
        tokens = [t for t in query.lower().replace(",", " ").split() if len(t) > 1]
        candidates = list(self._products.values())
        if category:
            candidates = [p for p in candidates if p.category.lower() == category.lower()]
        scored = [(self._score(p, tokens), p) for p in candidates]
        hits = [(s, p) for s, p in scored if s > 0] if tokens else [(0, p) for p in candidates]
        hits.sort(key=lambda sp: (-sp[0], sp[1].price_cents))
        return [p for _, p in hits[:limit]]

    async def get_product(self, sku: str) -> Product | None:
        return self._products.get(sku.upper())

    async def reset(self) -> None:
        self._load()
