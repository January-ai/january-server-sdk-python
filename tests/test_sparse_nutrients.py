"""Contract-driven offline nutrient-presence regressions; never loads .env."""
import asyncio
from copy import deepcopy
import inspect

import pytest
from pydantic import BaseModel

from januaryai import AsyncJanuary, January, JanuaryResponseError
from installed_consumer import FIXTURES, arguments, assert_request, local_service, method


CASES = FIXTURES["nutrientResponses"]
OPERATIONS = {fixture["operationId"]: fixture for fixture in FIXTURES["operations"]}


def at_path(value, path):
    """Navigate either serialized wire dictionaries or generated model objects."""
    for segment in path:
        if isinstance(value, BaseModel):
            name = next(name for name, field in type(value).model_fields.items()
                        if (field.alias or name) == segment)
            value = getattr(value, name)
        else:
            value = value[segment]
    return value


def assert_nutrient_presence(result, case):
    serialized = result.model_dump(by_alias=True, exclude_unset=True)
    expected = case["expectedNutrients"]
    assert serialized == case["response"]["body"]
    for path in case["nutrientPaths"]:
        nutrient_model = at_path(result, path)
        nutrient_map = at_path(serialized, path)
        assert isinstance(nutrient_model, BaseModel)
        assert nutrient_map == expected
        assert nutrient_model.model_dump(by_alias=True, exclude_unset=True) == expected
        present_wire_names = {
            type(nutrient_model).model_fields[name].alias or name
            for name in nutrient_model.model_fields_set
        }
        assert present_wire_names == set(expected)
        for name, field in type(nutrient_model).model_fields.items():
            wire_name = field.alias or name
            if wire_name not in expected:
                assert name not in nutrient_model.model_fields_set
                assert wire_name not in nutrient_map
        for wire_name, amount in expected.items():
            amount_model = at_path(nutrient_model, [wire_name])
            assert amount_model.model_fields_set == {"value", "unit"}
            if amount["value"] == 0:
                assert nutrient_map[wire_name]["value"] == 0
                assert amount_model.value == 0


async def call(fn, **kwargs):
    result = fn(**kwargs)
    return await result if inspect.isawaitable(result) else result


def scenario(async_mode, service, body):
    async def run():
        client = (AsyncJanuary if async_mode else January)(
            secret_key="sk-local-fixture", base_url=service["url"])
        try:
            await body(client)
        finally:
            await call(client.close)
    asyncio.run(run())


@pytest.mark.parametrize("case", CASES, ids=lambda case:case["name"])
@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_shared_nutrient_response_presence(case, async_mode):
    operation = OPERATIONS[case["operationId"]]
    with local_service() as service:
        service["response"] = case["response"]

        async def run(client):
            if case["valid"]:
                result = await call(method(client, operation), **arguments(operation))
                assert_nutrient_presence(result, case)
                response = result.response
                assert response is not None
                assert response.status_code == case["response"]["status"]
                assert response.request_id == {
                    k.lower():v for k,v in case["response"]["headers"].items()
                }["x-request-id"]
            else:
                with pytest.raises(JanuaryResponseError) as caught:
                    await call(method(client, operation), **arguments(operation))
                error = caught.value
                assert error.status_code == case["response"]["status"]
                expected_id = {
                    k.lower():v for k,v in case["response"]["headers"].items()
                }["x-request-id"]
                assert error.request_id == expected_id
                assert error.response.request_id == expected_id
            assert len(service["requests"]) == 1
            assert_request(service["requests"][0], operation)

        scenario(async_mode, service, run)


@pytest.mark.parametrize("case", [case for case in CASES
                                 if case["operationId"] == "scanFoodPhoto" and case["valid"]],
                         ids=lambda case:case["name"])
@pytest.mark.parametrize("async_mode", [False, True], ids=["sync", "async"])
def test_parsed_detection_correction_preserves_omitted_nutrients(case, async_mode):
    photo_operation = OPERATIONS["scanFoodPhoto"]
    correction_operation = OPERATIONS["correctPhotoScan"]
    correction_case = next(candidate for candidate in CASES
                           if candidate["operationId"] == "correctPhotoScan"
                           and candidate["valid"]
                           and candidate["expectedNutrients"] == case["expectedNutrients"])
    with local_service() as service:
        service["response"] = case["response"]

        async def run(client):
            photo = await call(method(client, photo_operation), **arguments(photo_operation))
            assert len(service["requests"]) == 1
            assert_request(service["requests"][0], photo_operation)
            assert_nutrient_presence(photo, case)
            assert photo.detections

            request = arguments(correction_operation)
            # Pass actual parsed models back to the SDK, not reconstructed dictionaries.
            request["detections"] = photo.detections
            if photo.meal_name is not None:
                request["meal_name"] = photo.meal_name
            else:
                request.pop("meal_name", None)

            expected_request = deepcopy(correction_operation)
            expected_request["request"]["body"]["detections"] = photo.model_dump(
                by_alias=True, exclude_unset=True)["detections"]
            if photo.meal_name is not None:
                expected_request["request"]["body"]["meal_name"] = photo.meal_name
            else:
                expected_request["request"]["body"].pop("meal_name", None)

            service["response"] = correction_case["response"]
            corrected = await call(client.food_analysis.correct, **request)
            assert len(service["requests"]) == 2  # Exactly one photo and one correction call.
            assert_request(service["requests"][1], expected_request)
            for detection in service["requests"][1]["body"]["detections"]:
                assert detection["food"]["nutrients"] == case["expectedNutrients"]
            assert_nutrient_presence(corrected, correction_case)

        scenario(async_mode, service, run)
