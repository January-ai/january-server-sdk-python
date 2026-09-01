# pyright: reportUnnecessaryIsInstance=false
"""Local serving calculations shared by synchronous and asynchronous SDK users."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .models import (
    FoodId,
    FoodLogInputFood,
    FoodSearchItem,
    NutrientAmount,
    NutritionFacts,
    ServingId,
    ServingOption,
)

FoodPortionErrorCode = Literal[
    "no_servings",
    "serving_not_found",
    "invalid_serving",
    "invalid_quantity",
]


class FoodPortionError(ValueError):
    """Invalid serving selection or quantity, with a stable client-SDK error code."""

    def __init__(self, code: FoodPortionErrorCode) -> None:
        super().__init__(f"Invalid food portion: {code}")
        self.code: FoodPortionErrorCode = code


def _positive_finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _scale_nutrition(nutrition: NutritionFacts, scale: float) -> NutritionFacts:
    amounts: dict[str, NutrientAmount] = {}
    # The generated model owns the nutrient vocabulary. Only present fields change.
    for name in NutritionFacts.model_fields:
        if name not in nutrition.model_fields_set:
            continue
        amount = getattr(nutrition, name)
        if isinstance(amount, NutrientAmount):
            amounts[name] = amount.model_copy(deep=True, update={"value": amount.value * scale})
    return nutrition.model_copy(deep=True, update=amounts)


@dataclass(frozen=True, slots=True, repr=False)
class FoodPortion:
    """A selected serving with locally scaled nutrition; performs no HTTP requests."""

    food_id: FoodId
    serving: ServingOption
    quantity: float
    nutrition: NutritionFacts
    total_weight_grams: float | None
    glycemic_index: float | None
    glycemic_load: float | None
    selection: FoodLogInputFood

    @classmethod
    def from_food(
        cls,
        food: FoodSearchItem,
        *,
        serving_id: ServingId | None = None,
        quantity: float | None = None,
    ) -> FoodPortion:
        """Choose an exact serving ID, or first primary/first serving, and scale it."""
        if not food.servings:
            raise FoodPortionError("no_servings")
        if serving_id is None:
            selected = next(
                (serving for serving in food.servings if serving.is_primary), food.servings[0]
            )
        else:
            selected = next(
                (serving for serving in food.servings if serving.id == serving_id), None
            )
        if selected is None:
            raise FoodPortionError("serving_not_found")
        if (
            selected.id is None
            or not _positive_finite(selected.quantity)
            or not _positive_finite(selected.scaling_factor)
        ):
            raise FoodPortionError("invalid_serving")
        assert selected.quantity is not None
        assert selected.scaling_factor is not None
        requested = selected.quantity if quantity is None else quantity
        if not _positive_finite(requested) or requested > 10_000:
            raise FoodPortionError("invalid_quantity")
        scale = requested * selected.scaling_factor / selected.quantity
        return cls(
            food_id=food.id,
            serving=selected.model_copy(deep=True),
            quantity=requested,
            nutrition=_scale_nutrition(food.nutrients, scale),
            total_weight_grams=(
                None
                if selected.weight_grams is None
                else selected.weight_grams * requested / selected.quantity
            ),
            glycemic_index=food.glycemic_index,
            glycemic_load=None if food.glycemic_load is None else food.glycemic_load * scale,
            selection=FoodLogInputFood(food_id=food.id, serving_id=selected.id, quantity=requested),
        )
