import asyncio

import pytest

from scripts import regression_official_expansions as driver


@pytest.mark.parametrize(
    ("options", "count", "allow_any", "expected"),
    [
        ([], 2, True, ["Dwarvish", "Elvish"]),
        (["Elvish"], 2, True, ["Elvish", "Dwarvish"]),
        (["Giant"], 1, False, ["Giant"]),
    ],
)
def test_official_regression_fallback_uses_ordinary_language_choices(
    monkeypatch, options, count, allow_any, expected
):
    async def catalog(_server, name, arguments):
        assert name == "character_query"
        assert arguments == {
            "view": "catalog",
            "payload": {"campaign_id": "campaign", "query": "background"},
        }
        return [
            {
                "id": "background",
                "selection_requirements": {
                    "language_count": count,
                    "language_options": options,
                    "allow_any_language": allow_any,
                },
            }
        ]

    monkeypatch.setattr(driver, "_call", catalog)
    selection = asyncio.run(driver._catalog_selection(None, "campaign", "background"))
    assert selection == {"languages": expected}
