from datetime import UTC, datetime
from typing import assert_type

from januaryai import (
    AsyncJanuary,
    ClientToken,
    FoodPortion,
    FoodPortionError,
    FoodPortionErrorCode,
    January,
    ResponseMetadata,
    models,
)


def check_portion(food: models.FoodSearchItem, error: FoodPortionError) -> None:
    portion = FoodPortion.from_food(food, serving_id=2, quantity=4)
    assert_type(portion, FoodPortion)
    assert_type(portion.food_id, int)
    assert_type(portion.serving, models.ServingOption)
    assert_type(portion.quantity, float)
    assert_type(portion.nutrition, models.NutritionFacts)
    assert_type(portion.total_weight_grams, float | None)
    assert_type(portion.glycemic_index, float | None)
    assert_type(portion.glycemic_load, float | None)
    assert_type(portion.selection, models.FoodLogInputFood)
    assert_type(error.code, FoodPortionErrorCode)


def check_sync(client: January) -> None:
    # Returned lists and native timestamps must work without casts in user code.
    scan = client.food_analysis.analyze_description(query="eggs")
    assert_type(
        client.food_analysis.correct(detections=scan.detections, user_input="less"), models.FoodScan
    )
    assert_type(
        client.food_logs.list(end_user_id="user", start=datetime.now(UTC), end=datetime.now(UTC)),
        models.ListFoodLogsResponse,
    )
    assert_type(
        client.food_logs.update(end_user_id="user", log_id="log", timestamp_utc=datetime.now(UTC)),
        models.FoodLog,
    )
    assert_type(models.NutritionFacts(), models.NutritionFacts)
    token = client.client_tokens.create(end_user_id="user", scopes=["foods:read"], ttl_seconds=300)
    assert_type(token, ClientToken)
    assert_type(token.token, str)
    assert_type(token.expires_in, int)
    assert_type(client.mint_client_token(end_user_id="user"), models.ClientTokenResponseDto)
    assert_type(client.revoke_client_tokens(end_user_id="user"), ResponseMetadata)
    assert_type(client.credits(), models.CreditsResponseDto)
    user = client.for_user("user")
    assert_type(user.food_analysis.analyze_description(query="eggs"), models.FoodScan)
    assert_type(user.foods.lookup_barcode(upc="049000006346"), models.FoodSearchResults)
    assert_type(
        user.food_logs.create(foods=[{"id": 12, "serving": {"id": 5, "quantity": 1}}]),
        models.FoodLog,
    )
    portion = FoodPortion.from_food(user.foods.get(food_id=42))
    assert_type(user.food_logs.create(foods=[portion.selection]), models.FoodLog)


async def check_async(client: AsyncJanuary) -> None:
    token = await client.client_tokens.create(end_user_id="user")
    assert_type(token, ClientToken)
    assert_type(await client.credits(), models.CreditsResponseDto)
    assert_type(await client.for_user("user").foods.search(query="eggs"), models.FoodSearchResults)
    portion = FoodPortion.from_food(await client.foods.get(food_id=42))
    assert_type(
        await client.for_user("user").food_logs.create(foods=[portion.selection]), models.FoodLog
    )
