"""
data/loader.py
Single entry point for all structured data.

Every other module imports from here — never reads JSON directly.
To update prices, breeds, or policies: edit the JSON files in data/json/.
No Python code needs to change.
"""
import json
from pathlib import Path
from functools import lru_cache
from typing import Union

_JSON = Path(__file__).parent / "json"


def _load(filename: str) -> Union[dict, list]:
    with open(_JSON / filename, encoding="utf-8") as f:
        return json.load(f)


# ── Cached loaders (loaded once at startup, reused on every request) ───────────

@lru_cache(maxsize=1)
def stores() -> list:
    return _load("stores.json")

@lru_cache(maxsize=1)
def products() -> dict:
    return _load("products.json")

@lru_cache(maxsize=1)
def cat_breeds() -> dict:
    return _load("cat_breeds.json")

@lru_cache(maxsize=1)
def dog_breeds() -> dict:
    return _load("dog_breeds.json")

@lru_cache(maxsize=1)
def kitten_food() -> list:
    return _load("kitten_food.json")

@lru_cache(maxsize=1)
def policies() -> dict:
    return _load("policies.json")


# ── Convenience helpers ────────────────────────────────────────────────────────

def get_store_phone(store_name: str) -> str:
    short = store_name.split(" - ")[0].strip()
    for s in stores():
        if s["name"] == short or s["name"] in store_name:
            return s["phone"]
    return stores()[0]["phone"]


def get_product(sku: str) -> Union[dict, None]:
    return products().get(sku.upper())


def get_return_policy(category: str) -> dict:
    """category: 'accessory' | 'food' | 'pet'"""
    return policies()["return_policy"].get(category, {})


def get_delivery_policy(kind: str) -> dict:
    """kind: 'pet' | 'product'"""
    return policies()["delivery_policy"].get(kind, {})


def get_appointment_policy() -> dict:
    return policies()["appointment_policy"]


# ── Prompt-building helpers ────────────────────────────────────────────────────
# These convert JSON data into the plain-English strings injected into prompts.
# Agents call these instead of hardcoding data.

def build_cat_breed_prompt() -> str:
    lines = ["CAT BREEDS AND PRICE RANGES:"]
    for name, b in cat_breeds().items():
        good = ", ".join(b.get("good_for", []))
        lines.append(
            f"{name}: ${b['price_min']}-{b['price_max']}. "
            f"Energy: {b['energy']}. Beginner-friendly: {'yes' if b['beginner_friendly'] else 'no'}. "
            f"Good for: {good}."
        )
    return " ".join(lines)


def build_dog_breed_prompt() -> str:
    lines = ["DOG BREEDS AND PRICE RANGES:"]
    for name, b in dog_breeds().items():
        good = ", ".join(b.get("good_for", []))
        lines.append(
            f"{name}: ${b['price_min']}-{b['price_max']}. "
            f"Energy: {b['energy']}. Beginner-friendly: {'yes' if b['beginner_friendly'] else 'no'}. "
            f"Good for: {good}."
        )
    return " ".join(lines)


def build_kitten_food_prompt() -> str:
    items = [f"{f['name']} ${f['price']:.2f}" for f in kitten_food()]
    return "KITTEN FOOD OPTIONS (recommend only after purchase confirmed): " + ", ".join(items) + "."


def build_product_catalogue_prompt() -> str:
    stock_label = {"in_stock": "In stock", "low_stock": "Low stock", "out_of_stock": "Out of stock"}
    lines = ["PRODUCT CATALOGUE:"]
    for sku, p in products().items():
        stock = stock_label.get(p["stock"], p["stock"])
        qty   = f" ({p['stock_qty']} left)" if p.get("stock_qty") else ""
        lines.append(f"{sku} - {p['name']} - ${p['price']:.2f} - {stock}{qty}.")
    return " ".join(lines)


def build_store_list_prompt() -> str:
    parts = [f"{s['name']} ({s['address']}, {s['phone']})" for s in stores()]
    policy = get_appointment_policy()
    hours  = f"Open Mon–Sun {policy['open_hour']}:00 AM – {policy['close_hour'] - 12}:00 PM."
    return "STORES: " + "; ".join(parts) + ". " + hours


def build_return_policy_prompt() -> str:
    rp = policies()["return_policy"]
    lines = ["RETURN POLICY:"]
    for category, data in rp.items():
        lines.append(f"{category.capitalize()}: {data['detail']}")
    return " ".join(lines)


def build_delivery_policy_prompt() -> str:
    dp = policies()["delivery_policy"]
    pet = dp["pet"]
    prod = dp["product"]
    return (
        f"DELIVERY POLICY: "
        f"Pets — GTA flat fee ${pet['gta_fee']:.0f}; outside GTA calculated by address. "
        f"Products — FREE over ${prod['free_threshold']:.0f} in GTA, otherwise ${prod['standard_fee']:.2f}. "
        f"Estimated delivery: {prod['estimated_days']} business days."
    )
