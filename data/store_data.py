"""
data/store_data.py
DEPRECATED - all data has moved to data/json/*.json files.
This file now re-exports from data.loader so any code that still
imports from here continues to work without changes.
"""
from data.loader import (
    products   as _products,
    cat_breeds as _cat_breeds,
    dog_breeds as _dog_breeds,
    kitten_food as _kitten_food,
    policies   as _policies,
)

PRODUCTS     = _products()
CAT_BREEDS   = _cat_breeds()
DOG_BREEDS   = _dog_breeds()
KITTEN_FOOD  = _kitten_food()
RETURN_POLICY = _policies()["return_policy"]
