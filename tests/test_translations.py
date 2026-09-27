"""Every action field and exception has an English and a German text.

hassfest checks this strictly only for core integrations.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import string

import pytest
import yaml

COMPONENT = Path(__file__).parents[1] / "custom_components" / "matchday"
SERVICES = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
USED_KEYS = set(
    re.findall(
        r'translation_key="(\w+)"',
        (COMPONENT / "services.py").read_text(encoding="utf-8"),
    )
)


def load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name", ["strings.json", "translations/en.json", "translations/de.json"]
)
def test_actions_and_exceptions_translated(name: str) -> None:
    strings = load(name)
    assert set(strings["services"]) == set(SERVICES)
    for service, schema in SERVICES.items():
        texts = strings["services"][service]
        assert texts["name"] and texts["description"]
        assert set(texts["fields"]) == set(schema["fields"])
        assert all(f["name"] and f["description"] for f in texts["fields"].values())
    assert set(strings["exceptions"]) == USED_KEYS
    english = load("translations/en.json")["exceptions"]
    for key, text in strings["exceptions"].items():
        fields = {f for _, f, _, _ in string.Formatter().parse(text["message"]) if f}
        expected = {
            f for _, f, _, _ in string.Formatter().parse(english[key]["message"]) if f
        }
        assert fields == expected, key


def test_service_icons() -> None:
    assert set(load("icons.json")["services"]) == set(SERVICES)


def test_strings_equal_english() -> None:
    assert load("strings.json") == load("translations/en.json")
