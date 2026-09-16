"""Food-log summary request serialization for both grouping modes, over a mock transport."""

from copy import deepcopy
from datetime import date

import httpx
from installed_consumer import FIXTURES

from januaryai import January, models

KEY = "sk-summary-synthetic-0011223344556677"
FIXTURE = next(f for f in FIXTURES["operations"] if f["operationId"] == "getFoodLogSummary")


def test_weekly_summary_serializes_grouping_and_week_start() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = deepcopy(FIXTURE["response"]["body"])
        body.update(
            group_by="week",
            week_start="sunday",
            timezone="America/Chicago",
            start_date="2026-09-06",
            end_date="2026-09-19",
            buckets=[
                {**body["buckets"][0], "start_date": "2026-09-06", "end_date": "2026-09-12"},
                {**body["buckets"][1], "start_date": "2026-09-13", "end_date": "2026-09-19"},
            ],
        )
        return httpx.Response(200, json=body)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        January(api_key=KEY, http_client=http, max_retries=0) as client,
    ):
        summary = client.for_user("weekly-user").food_logs.get_summary(
            start_date=date(2026, 9, 6),
            end_date="2026-09-19",
            timezone="America/Chicago",
            group_by="week",
            week_start="sunday",
        )

    request = captured[0]
    assert request.url.path == "/v1.2/food-logs/summary"
    assert dict(request.url.params) == {
        "start_date": "2026-09-06",
        "end_date": "2026-09-19",
        "timezone": "America/Chicago",
        "group_by": "week",
        "week_start": "sunday",
    }
    assert request.headers["January-End-User-ID"] == "weekly-user"
    assert isinstance(summary, models.FoodLogSummary)
    assert summary.group_by == "week" and summary.week_start == "sunday"
    assert [b.start_date for b in summary.buckets] == [date(2026, 9, 6), date(2026, 9, 13)]


def test_daily_summary_omits_week_start_from_the_query() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FIXTURE["response"]["body"])

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        January(api_key=KEY, http_client=http, max_retries=0) as client,
    ):
        client.for_user("daily-user").food_logs.get_summary(
            start_date="2026-09-13", end_date="2026-09-15", timezone="UTC", group_by="day"
        )

    params = dict(captured[0].url.params)
    assert params["group_by"] == "day" and "week_start" not in params
