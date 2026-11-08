"""Saved shipping addresses: ship_to_id -> buyer contact and destination, kept server-side.

The agent (and the model) only ever handle ids such as `addr_home`; the personal data stays in
this process and goes straight to Shopify's checkout. Lookup order:

    1. the file named by SHOPIFY_ADDRESS_BOOK
    2. store/address_book.json            (gitignored: put real test data here)
    3. store/address_book.example.json    (placeholder data, committed)

Entry fields: email or phone_number (contact), first_name, last_name, street_address,
extended_address?, address_locality, address_region, postal_code, address_country (ISO alpha-2).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .backend import StoreToolError

HERE = Path(__file__).resolve().parent
DESTINATION_FIELDS = (
    "first_name",
    "last_name",
    "phone_number",
    "street_address",
    "extended_address",
    "address_locality",
    "address_region",
    "postal_code",
    "address_country",
)
REQUIRED = ("first_name", "last_name", "street_address", "address_locality", "postal_code", "address_country")


def default_path() -> Path:
    explicit = os.environ.get("SHOPIFY_ADDRESS_BOOK")
    if explicit:
        return Path(explicit).expanduser()
    local = HERE / "address_book.json"
    return local if local.is_file() else HERE / "address_book.example.json"


class AddressBook:
    def __init__(self, entries: dict[str, dict], source: str = "") -> None:
        self._entries = entries
        self.source = source

    @classmethod
    def load(cls, path: Path | None = None) -> "AddressBook":
        path = path or default_path()
        data = json.loads(path.read_text())
        entries = {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
        return cls(entries, source=str(path))

    def ids(self) -> list[str]:
        return sorted(self._entries)

    def buyer_and_destination(self, ship_to_id: str) -> tuple[dict, dict]:
        entry = self._entries.get(ship_to_id)
        if entry is None:
            raise StoreToolError("unknown_address", f"no saved address {ship_to_id}")
        missing = [f for f in REQUIRED if not entry.get(f)]
        if missing or not (entry.get("email") or entry.get("phone_number")):
            raise StoreToolError(
                "incomplete_address",
                f"saved address {ship_to_id} is missing {', '.join(missing) or 'an email or phone_number'}",
            )
        buyer = {"email": entry.get("email"), "phone_number": entry.get("phone_number")}
        destination = {"id": ship_to_id, **{f: entry[f] for f in DESTINATION_FIELDS if entry.get(f)}}
        return buyer, destination
