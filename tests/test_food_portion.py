import asyncio
import inspect
from dataclasses import FrozenInstanceError

import pytest
from installed_consumer import FIXTURES, arguments, local_service

from januaryai import AsyncJanuary, FoodPortion, FoodPortionError, January
from januaryai.models import (
    FoodLogInputFood,
    FoodSearchItem,
    NutrientAmount,
    NutritionFacts,
    ServingOption,
)

NUTRIENTS = (
    "calories",
    "protein",
    "carbohydrates",
    "net_carbohydrates",
    "total_fat",
    "trans_fat",
    "saturated_fat",
    "fiber",
    "total_sugars",
    "added_sugars",
    "cholesterol",
    "calcium",
    "iron",
    "potassium",
    "sodium",
    "vitamin_d",
)


def example_food():
    return FoodSearchItem(
        id=42,
        name="Test food",
        glycemic_index=50,
        glycemic_load=8,
        nutrients=NutritionFacts(
            calories=NutrientAmount(value=100, unit="cal"),
            protein=NutrientAmount(value=10, unit="g"),
        ),
        servings=[
            ServingOption(
                id=1, quantity=1, unit="slice", scaling_factor=1, weight_grams=50, is_primary=True
            ),
            ServingOption(
                id=2,
                quantity=2,
                unit="pieces",
                scaling_factor=3,
                weight_grams=120,
                is_primary=False,
            ),
        ],
    )


def test_primary_default_and_request_ready_selection():
    portion = FoodPortion.from_food(example_food())
    assert portion.food_id == 42
    assert portion.serving.id == 1
    assert portion.quantity == 1
    assert portion.nutrition.calories is not None
    assert portion.nutrition.calories.value == 100
    assert portion.total_weight_grams == 50
    assert isinstance(portion.selection, FoodLogInputFood)
    assert portion.selection.model_dump(by_alias=True, exclude_unset=True) == {
        "id": 42,
        "serving": {"id": 1, "quantity": 1},
    }


def test_alternate_serving_matches_node_client_scaling():
    portion = FoodPortion.from_food(example_food(), serving_id=2, quantity=4)
    assert portion.nutrition.calories is not None
    assert portion.nutrition.calories.value == 600
    assert portion.nutrition.protein is not None
    assert portion.nutrition.protein.value == 60
    assert portion.total_weight_grams == 240
    assert portion.glycemic_index == 50
    assert portion.glycemic_load == 48
    assert portion.selection.serving.id == 2
    assert portion.selection.serving.quantity == 4


def test_gram_serving_matches_ios_client_scaling():
    food = FoodSearchItem(
        id=70381819,
        name="banana",
        glycemic_index=51,
        glycemic_load=12,
        nutrients=NutritionFacts.model_validate(
            {
                "calories": {"value": 105.02, "unit": "cal"},
                "protein": {"value": 1.2862, "unit": "g"},
                "carbohydrates": {"value": 26.9512, "unit": "g"},
                "potassium": {"value": 422, "unit": "mg"},
            }
        ),
        servings=[
            ServingOption(
                id=2,
                quantity=100,
                unit="g",
                scaling_factor=0.8474576271,
                weight_grams=100,
                is_primary=False,
            )
        ],
    )
    portion = FoodPortion.from_food(food, serving_id=2, quantity=200)
    assert portion.nutrition.calories is not None
    assert portion.nutrition.calories.value == pytest.approx(178, abs=0.001)
    assert portion.nutrition.protein is not None
    assert portion.nutrition.protein.value == pytest.approx(2.18, abs=0.001)
    assert portion.nutrition.carbohydrates is not None
    assert portion.nutrition.carbohydrates.value == pytest.approx(45.68, abs=0.001)
    assert portion.nutrition.potassium is not None
    assert portion.nutrition.potassium.value == pytest.approx(715.254, abs=0.001)
    assert portion.nutrition.potassium is not None
    assert portion.nutrition.potassium.unit == "mg"
    assert portion.total_weight_grams == 200
    assert portion.glycemic_index == 51
    assert portion.glycemic_load == pytest.approx(20.3389, abs=0.001)


def test_scales_all_16_generated_nutrients_and_preserves_units():
    assert set(NUTRIENTS) == set(NutritionFacts.model_fields)
    raw = {
        name: {"value": index + 0.5, "unit": f"unit-{index}"}
        for index, name in enumerate(NUTRIENTS)
    }
    food = example_food().model_copy(update={"nutrients": NutritionFacts.model_validate(raw)})
    nutrition = FoodPortion.from_food(food, serving_id=2, quantity=4).nutrition
    assert nutrition.model_fields_set == set(NUTRIENTS)
    assert nutrition.model_dump(by_alias=True, exclude_unset=True) == {
        name: {"value": amount["value"] * 6, "unit": amount["unit"]} for name, amount in raw.items()
    }


def test_sparse_zero_and_empty_nutrients_keep_presence():
    food = example_food().model_copy(
        update={"nutrients": NutritionFacts(protein=NutrientAmount(value=0, unit="g"))}
    )
    portion = FoodPortion.from_food(food, quantity=2)
    assert portion.nutrition.model_fields_set == {"protein"}
    assert portion.nutrition.model_dump(by_alias=True, exclude_unset=True) == {
        "protein": {"value": 0, "unit": "g"}
    }
    empty = FoodPortion.from_food(food.model_copy(update={"nutrients": NutritionFacts()}))
    assert empty.nutrition.model_fields_set == set()
    assert empty.nutrition.model_dump(by_alias=True, exclude_unset=True) == {}


def test_existing_explicit_none_and_missing_weight_glycemic_values_are_preserved():
    food = FoodSearchItem(
        id=42,
        name="Test",
        nutrients=NutritionFacts(protein=None),
        servings=[
            ServingOption(
                id=1,
                quantity=2,
                unit="pieces",
                scaling_factor=1,
                weight_grams=None,
                is_primary=True,
            )
        ],
    )
    portion = FoodPortion.from_food(food)
    assert portion.quantity == 2
    assert portion.total_weight_grams is None
    assert portion.glycemic_index is None
    assert portion.glycemic_load is None
    assert portion.nutrition.model_fields_set == {"protein"}
    assert portion.nutrition.model_dump(exclude_unset=True) == {"protein": None}
    zero = FoodPortion.from_food(food.model_copy(update={"glycemic_index": 0, "glycemic_load": 0}))
    assert zero.glycemic_index == 0
    assert zero.glycemic_load == 0


def test_defaults_to_selected_serving_quantity_and_first_primary_or_first():
    food = example_food()
    alternate = FoodPortion.from_food(food, serving_id=2)
    assert alternate.quantity == 2
    assert alternate.nutrition.calories is not None
    assert alternate.nutrition.calories.value == 300
    assert alternate.total_weight_grams == 120
    both_primary = [
        serving.model_copy(update={"is_primary": True}) for serving in reversed(food.servings)
    ]
    assert FoodPortion.from_food(food.model_copy(update={"servings": both_primary})).serving.id == 2
    no_primary = [
        serving.model_copy(update={"is_primary": False}) for serving in reversed(food.servings)
    ]
    assert FoodPortion.from_food(food.model_copy(update={"servings": no_primary})).serving.id == 2
    later_primary = list(reversed(food.servings))
    assert (
        FoodPortion.from_food(food.model_copy(update={"servings": later_primary})).serving.id == 1
    )


@pytest.mark.parametrize(
    "quantity", [0, -1, 10_001, float("nan"), float("inf"), -float("inf"), True, "2"]
)
def test_invalid_quantity_has_native_stable_error(quantity):
    with pytest.raises(FoodPortionError) as caught:
        FoodPortion.from_food(example_food(), quantity=quantity)
    assert isinstance(caught.value, ValueError)
    assert caught.value.code == "invalid_quantity"


@pytest.mark.parametrize("field", ["quantity", "scaling_factor"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf"), True])
def test_invalid_selected_serving_has_stable_error(field, value):
    food = example_food()
    # model_copy deliberately allows invalid parsed data to reach the utility guard.
    selected = food.servings[0].model_copy(update={field: value})
    with pytest.raises(FoodPortionError) as caught:
        FoodPortion.from_food(food.model_copy(update={"servings": [selected]}))
    assert caught.value.code == "invalid_serving"


def test_empty_missing_serving_and_validation_precedence():
    food = example_food()
    with pytest.raises(FoodPortionError) as caught:
        FoodPortion.from_food(food.model_copy(update={"servings": []}), serving_id=999, quantity=0)
    assert caught.value.code == "no_servings"
    with pytest.raises(FoodPortionError) as caught:
        FoodPortion.from_food(food, serving_id=999, quantity=0)
    assert caught.value.code == "serving_not_found"
    invalid = food.servings[0].model_copy(update={"quantity": 0})
    with pytest.raises(FoodPortionError) as caught:
        FoodPortion.from_food(food.model_copy(update={"servings": [invalid]}), quantity=0)
    assert caught.value.code == "invalid_serving"
    over_limit = food.servings[0].model_copy(update={"quantity": 10_001})
    with pytest.raises(FoodPortionError) as caught:
        FoodPortion.from_food(food.model_copy(update={"servings": [over_limit]}))
    assert caught.value.code == "invalid_quantity"


def test_quantity_boundary_and_fraction():
    assert FoodPortion.from_food(example_food(), quantity=10_000).quantity == 10_000
    calories = FoodPortion.from_food(example_food(), quantity=0.25).nutrition.calories
    assert calories is not None and calories.value == 25


def test_does_not_mutate_or_share_mutable_model_state():
    food = example_food()
    before = food.model_dump(by_alias=True, exclude_unset=True)
    before_fields = set(food.nutrients.model_fields_set)
    portion = FoodPortion.from_food(food, quantity=2)
    assert food.model_dump(by_alias=True, exclude_unset=True) == before
    assert food.nutrients.model_fields_set == before_fields
    assert portion.serving is not food.servings[0]
    assert portion.nutrition is not food.nutrients
    assert portion.nutrition.calories is not food.nutrients.calories
    portion.nutrition.model_fields_set.clear()
    portion.serving.model_fields_set.clear()
    assert food.nutrients.model_fields_set == before_fields
    assert food.model_dump(by_alias=True, exclude_unset=True) == before
    with pytest.raises(FrozenInstanceError):
        portion.quantity = 99  # pyright: ignore[reportAttributeAccessIssue] -- intentionally test frozen dataclass


@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_portion_selection_works_in_sdk_log_and_glucose_requests(async_mode):
    portion = FoodPortion.from_food(example_food(), serving_id=2, quantity=4)
    assert not inspect.isawaitable(portion)
    with local_service() as service:

        async def run():
            client = (AsyncJanuary if async_mode else January)(
                secret_key="sk-local-fixture", base_url=service["url"]
            )

            async def call(fn, **kwargs):
                value = fn(**kwargs)
                return await value if inspect.isawaitable(value) else value

            try:
                user = client.for_user("offline-portion-user")
                fixture = next(
                    f for f in FIXTURES["operations"] if f["operationId"] == "createFoodLog"
                )
                service["response"] = fixture["response"]
                await call(user.food_logs.create, foods=[portion.selection])
                fixture = next(
                    f for f in FIXTURES["operations"] if f["operationId"] == "predictGlucose"
                )
                service["response"] = fixture["response"]
                params = arguments(fixture)
                params["foods"] = [portion.selection]
                await call(user.glucose.predict, **params)
            finally:
                await call(client.close)

        asyncio.run(run())
        assert len(service["requests"]) == 2
        assert all(
            request["body"]["foods"] == [{"id": 42, "serving": {"id": 2, "quantity": 4}}]
            for request in service["requests"]
        )
