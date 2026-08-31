"""Analyze local photos concurrently, with four requests at most in flight.

Run from the directory containing your .env:
    python examples/analysis/concurrent.py lunch.jpg dinner.jpg
Each photo makes one potentially billable request; retries are disabled here.
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from januaryai import AsyncJanuary, JanuaryError


async def analyze_photos(client: AsyncJanuary, images: list[Path]) -> list[object]:
    semaphore = asyncio.Semaphore(4)
    user = client.for_user("january-photo-example", end_user_timezone="UTC")

    async def analyze(path: Path) -> object:
        async with semaphore:
            return await user.food_analysis.analyze_photo(image=path)

    return await asyncio.gather(*(analyze(path) for path in images), return_exceptions=True)


async def main() -> int:
    if len(sys.argv) < 2:
        print("Pass one or more local photo paths.", file=sys.stderr)
        return 2
    load_dotenv(Path.cwd() / ".env", override=False)
    async with AsyncJanuary(max_retries=0) as client:
        results = await analyze_photos(client, [Path(value) for value in sys.argv[1:]])
    for index, result in enumerate(results, 1):
        if isinstance(result, BaseException):
            print(f"Photo {index}: failed ({type(result).__name__})")
        else:
            print(f"Photo {index}: analyzed")
    return int(any(isinstance(result, BaseException) for result in results))


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except JanuaryError:
        print(
            "Check JANUARY_API_KEY and your connection; no credentials were printed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
