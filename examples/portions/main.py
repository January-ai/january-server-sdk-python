"""Offline example: synthetic food only, no client, credentials, or HTTP."""

import json

from januaryai import FoodPortion
from januaryai.models import FoodSearchItem, NutrientAmount, NutritionFacts, ServingOption

food = FoodSearchItem(
    id="42",
    type="generic",
    name="Example food",
    brand_name=None,
    image_url=None,
    barcode=None,
    nutrients=NutritionFacts(
        calories=NutrientAmount(value=100, unit="cal"),
        protein=NutrientAmount(value=10, unit="g"),
        added_sugars=NutrientAmount(value=0, unit="g"),
    ),
    glycemic_index=50,
    glycemic_load=8,
    servings=[
        ServingOption(
            id="1", quantity=1, unit="slice", scaling_factor=1, weight_grams=50, is_primary=True
        ),
        ServingOption(
            id="2", quantity=2, unit="pieces", scaling_factor=3, weight_grams=120, is_primary=False
        ),
    ],
)
portion = FoodPortion.from_food(food, serving_id="2", quantity=4)
print(
    json.dumps(
        {
            "food_id": portion.food_id,
            "quantity": portion.quantity,
            "nutrition": portion.nutrition.model_dump(by_alias=True, exclude_unset=True),
            "total_weight_grams": portion.total_weight_grams,
            "glycemic_index": portion.glycemic_index,
            "glycemic_load": portion.glycemic_load,
            "selection": portion.selection.model_dump(by_alias=True, exclude_unset=True),
        },
        indent=2,
    )
)
