"""Keep the public README aligned with the package and generated API surface."""

import ast
import inspect
import json
import re
import tomllib
from pathlib import Path

from januaryai import January

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def anchors(source):
    return {
        re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        for title in re.findall(r"^#{1,6} (.+)$", source, re.M)
    }


def test_badges_point_to_our_actual_package_workflow_and_metadata():
    assert f"https://img.shields.io/pypi/v/{PROJECT['name']}.svg" in README
    assert f"https://pypi.org/project/{PROJECT['name']}/" in README
    assert (
        "https://github.com/January-ai/january-server-sdk-python/actions/workflows/ci.yml/badge.svg?branch=main"
        in README
    )
    assert (ROOT / ".github/workflows/ci.yml").is_file()
    minimum = PROJECT["requires-python"].removeprefix(">=")
    assert f"https://img.shields.io/badge/python-{minimum}%2B-blue.svg" in README
    assert f"[![License: {PROJECT['license']}]" in README
    assert "[changelog](CHANGELOG.md)" in README
    assert "badge/CI-passing" not in README
    assert "january_ai" not in README and "January-ai/python-sdk" not in README


def test_dependency_constraints_match_the_package():
    section = README.split("| Dependency |", 1)[1].split("</details>", 1)[0]
    rows = dict(re.findall(r"\| `([^`]+)` \| `([^`]+)` \|", section))
    expected = {}
    for dependency in PROJECT["dependencies"]:
        match = re.fullmatch(r"([\w-]+)(.*)", dependency)
        assert match is not None
        expected[match.group(1)] = match.group(2)
    assert rows == expected
    assert "python-dotenv" not in rows


def test_all_20_documented_calls_and_returns_match_the_contract():
    section = README.split("### All 20 operations at a glance\n", 1)[1].split("### End users", 1)[0]
    rows = re.findall(r"^\| `([^`]+)` \| [^|]+ \| `([^`]+)` \|$", section, re.M)
    surface = json.loads((ROOT / "sdk-surface.json").read_text(encoding="utf-8"))["operations"]
    descriptors = json.loads((ROOT / "src/januaryai/_contract.json").read_text(encoding="utf-8"))[
        "operations"
    ]
    expected = {
        (
            f"user.{operation['resource']}.{operation['method']}"
            if operation["resource"]
            else f"client.{operation['method']}"
        ): descriptors[operation["operationId"]]["responseType"]
        for operation in surface
    }
    seen = {}
    with January(api_key="sk-readme-offline") as client:
        user = client.for_user("readme-user", end_user_timezone="UTC")
        for call, response in rows:
            expression = ast.parse(call, mode="eval").body
            assert isinstance(expression, ast.Call) and not expression.args
            name = ast.unparse(expression.func)
            root, *members = name.split(".")
            target = {"client": client, "user": user}[root]
            for member in members:
                target = getattr(target, member)
            inspect.signature(target).bind(
                **{
                    keyword.arg: Ellipsis
                    for keyword in expression.keywords
                    if keyword.arg is not None
                }
            )
            assert name not in seen
            seen[name] = response
    assert len(rows) == 20 and seen == expected


def test_contents_and_local_links_resolve():
    contents = README.split("## Contents\n", 1)[1].split("\n## ", 1)[0]
    toc = re.findall(r"\]\(#([^)]+)\)", contents)
    assert len(toc) >= 10 and set(toc) <= anchors(README)
    for target in re.findall(r"\]\(([^)]+)\)", README):
        if target.startswith(("https://", "mailto:", "#")):
            continue
        path, _, anchor = target.partition("#")
        file = ROOT / path
        assert file.is_file(), target
        if anchor:
            assert anchor in anchors(file.read_text(encoding="utf-8")), target


def test_quickstart_stays_short_and_matches_the_executable_example():
    section = README.split("## Quick start\n", 1)[1].split("## Detailed setup and credentials", 1)[
        0
    ]
    match = re.search(r"```python\n(.*?)```", section, re.S)
    assert match is not None
    code = match.group(1)
    assert code == (ROOT / "examples/quickstart/minimal.py").read_text(encoding="utf-8")
    assert len(code.splitlines()) <= 20
