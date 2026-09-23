"""API-only setup for tests that need symptomatic Sight Rot."""

from __future__ import annotations

from typing import Any, Callable

DISEASE_SOURCE = "bundled:srd2014/08_Gamemastering/Diseases.md"


async def install_symptomatic_sight_rot(
    call: Callable[..., Any], campaign_id: str, actor_id: str, *, key: str,
    member_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Use disease exposure, game time and long rest APIs to create a legal instance."""
    async def invoke(name: str, arguments: dict[str, Any]) -> Any:
        response = await call(name, arguments)
        return response.get("result", response) if isinstance(response, dict) else response

    campaign = await invoke("campaign_query", {
        "view": "get", "payload": {"campaign_id": campaign_id},
    })
    actor = await invoke("character_query", {
        "view": "get", "payload": {"character_id": actor_id},
    })
    for attempt in range(20):
        result = await invoke("character_disease_exposure", {
            "campaign_id": campaign_id,
            "actor_id": actor_id,
            "disease_id": "sight_rot",
            "exposure_kind": "tainted_water",
            "exposure_source_id": f"scene:{key}:tainted-water",
            "exposure_source_ref": DISEASE_SOURCE,
            "expected_revision": campaign["revision"],
            "expected_actor_revision": actor["revision"],
            "idempotency_key": f"{key}-exposure-{attempt}",
        })
        if result["status"] == "infected":
            break
        if result["status"] != "saved":
            raise AssertionError(f"expected a valid exposure result, got {result['status']!r}")
        campaign = await invoke("campaign_query", {
            "view": "get", "payload": {"campaign_id": campaign_id},
        })
        actor = await invoke("character_query", {
            "view": "get", "payload": {"character_id": actor_id},
        })
    else:
        raise AssertionError("seeded exposure attempts did not infect the Sight Rot target")

    campaign = await invoke("campaign_query", {
        "view": "get", "payload": {"campaign_id": campaign_id},
    })
    elapsed = int(campaign["state"]["game_time"]["elapsed_ticks"])
    clock_result = await invoke("campaign_change", {
        "campaign_id": campaign_id,
        "action": "clock_advance",
        "payload": {
            "period": "hour", "count": 16, "expected_elapsed_ticks": elapsed + 9600,
        },
        "expected_revision": campaign["revision"],
        "idempotency_key": f"{key}-incubation-clock",
    })
    campaign = await invoke("campaign_query", {
        "view": "get", "payload": {"campaign_id": campaign_id},
    })
    participants = []
    for member_id in member_ids or [actor_id]:
        member = await invoke("character_query", {
            "view": "get", "payload": {"character_id": member_id},
        })
        participants.append({
            "character_id": member_id,
            "expected_revision": member["revision"],
            "survival_intake": {
                "food_lb": 1, "water_gallons": 1, "hot_weather": False,
            },
        })
    rest_result = await invoke("campaign_change", {
        "campaign_id": campaign_id,
        "action": "party_rest",
        "payload": {
            "rest_type": "long_rest",
            "duration_minutes": 480,
            "members": participants,
        },
        "expected_revision": campaign["revision"],
        "idempotency_key": f"{key}-symptom-rest",
    })
    actor = await invoke("character_query", {
        "view": "get", "payload": {"character_id": actor_id},
    })
    final_campaign = await invoke("campaign_query", {
        "view": "get", "payload": {"campaign_id": campaign_id},
    })
    effect = next(
        item for item in actor["sheet"]["effects"]
        if item.get("kind") == "disease_state"
        and item.get("source") == DISEASE_SOURCE
        and dict(item.get("metadata") or {}).get("disease_state", {}).get("disease_id")
        == "sight_rot"
    )
    state = effect["metadata"]["disease_state"]
    if effect.get("active") is not True or state.get("symptomatic") is not True:
        raise AssertionError(
            "the reviewed disease operations did not produce symptomatic Sight Rot: "
            f"state={state!r}, game_time={final_campaign['state'].get('game_time')!r}, "
            f"clock_result={clock_result!r}, rest_result={rest_result!r}"
        )
    return actor
