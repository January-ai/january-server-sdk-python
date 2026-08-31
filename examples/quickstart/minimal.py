from pathlib import Path

from dotenv import load_dotenv

from januaryai import January

load_dotenv(Path.cwd() / ".env", override=False)

with January(max_retries=0) as client:
    user = client.for_user("january-quickstart", end_user_timezone="UTC")
    foods = user.foods.search(query="banana")

print(f"Foods returned: {len(foods.items)}")
if foods.items:
    print(f"First food: {foods.items[0].name}")
