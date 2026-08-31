# Workflow recipes

Use the README's .env setup first. These are application fragments; user IDs
must come from your authenticated user. Writes create real food logs and calls
may consume credits. Examples disable retries to keep the request count clear.

## Analyze, correct, then log

```python
from pathlib import Path
from dotenv import load_dotenv
from januaryai import January

load_dotenv(Path.cwd() / ".env", override=False)
with January(max_retries=0) as client:
    user = client.for_user("your-authenticated-user", end_user_timezone="UTC")
    analysis = user.food_analysis.analyze_photo(image=Path("lunch.jpg"))
    corrected = user.food_analysis.correct(
        detections=analysis.detections,
        user_input="There was half as much rice",
    )
    selections = [
        {"id": item.food.id, "serving": {
            "id": item.food.servings[0].id,
            "quantity": item.food.servings[0].quantity or 1,
        }}
        for item in corrected.detections
        if item.food.id is not None and item.food.servings
    ]
    if selections:
        log = user.food_logs.create(foods=selections, name="Lunch")
        print(log.id)
```

Unknown foods without a database ID cannot be logged with this endpoint. Empty
detections are a valid analysis result. Keep returned detection models intact
when correcting so additive response fields can survive a server update.

## Search, choose a serving, calculate locally, then log

```python
from pathlib import Path
from dotenv import load_dotenv
from januaryai import FoodPortion, January

load_dotenv(Path.cwd() / ".env", override=False)
with January(max_retries=0) as client:
    user = client.for_user("your-authenticated-user", end_user_timezone="UTC")
    results = user.foods.search(query="Greek yogurt")
    if results.items:
        food = user.foods.get(food_id=results.items[0].id)
        if food.servings:
            serving = next((s for s in food.servings if s.is_primary), food.servings[0])
            portion = FoodPortion.from_food(food, serving_id=serving.id, quantity=2)
            print(portion.nutrition.model_dump(mode="json", exclude_unset=True))
            log = user.food_logs.create(foods=[portion.selection], name="Snack")
```

FoodPortion is local and does not consume credits. The same utility works with
foods returned by AsyncJanuary and does not mutate the source food.

## Concurrent photos and async applications

[concurrent.py](../examples/analysis/concurrent.py) is a complete asyncio example
with four in-flight photos at most, per-photo failures and one request per photo.
Image preparation runs off the event loop. AsyncJanuary also works inside
`trio.run` or an AnyIO task group; its resource signatures are the same.

## Handle exhausted credits separately

```python
from januaryai import CreditLimitExceededError, January, RateLimitError

with January() as client:
    try:
        result = client.foods.search(query="banana")
    except CreditLimitExceededError:
        print("Check your plan and allowance in the developer dashboard.")
    except RateLimitError as error:
        print("Rate limited; retry-after seconds:", error.retry_after)
```

For batch planning, inspect `client.credits()` before starting, but still handle
credit exhaustion during the batch: another process can spend the same balance.
