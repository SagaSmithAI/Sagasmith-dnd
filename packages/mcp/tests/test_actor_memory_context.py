from copy import deepcopy

import pytest

from sagasmith_dnd_mcp.actor_memory import select_actor_memory_context


def _knowledge(
    index: int,
    *,
    proposition: str | None = None,
    subject_ref: str = "",
    confidence: int = 1,
    salience: int = 1,
    source_event_id: str | None = None,
) -> dict:
    return {
        "id": f"knowledge-{index:02d}",
        "revision_id": f"revision-{index:02d}",
        "actor_id": "actor-npc",
        "knowledge_key": f"knowledge-key-{index:02d}",
        "proposition": proposition or f"Unrelated background detail {index}.",
        "subject_ref": subject_ref,
        "confidence": confidence,
        "salience": salience,
        "source_event_id": source_event_id,
    }


def _event(
    index: int,
    *,
    summary: str | None = None,
    scene_id: str = "",
    actor_id: str = "",
) -> dict:
    payload = {"scene_id": scene_id} if scene_id else {}
    participants = [{"actor_id": actor_id, "role": "listener"}] if actor_id else []
    return {
        "id": f"event-{index:02d}",
        "sequence": index,
        "summary": summary or f"Routine event {index}.",
        "retrieval_text": summary or f"Routine event {index}.",
        "payload": payload,
        "participants": participants,
    }


def test_empty_query_does_not_fall_back_to_key_order_after_eight_items() -> None:
    values = [_knowledge(index) for index in range(12)]
    values.append(
        _knowledge(
            99,
            proposition="The old oath remains unfulfilled.",
            confidence=5,
            salience=5,
            source_event_id="event-99",
        )
    )
    events = [_event(99, summary="The oath was renewed at dawn.")]

    complete = select_actor_memory_context(
        actor_state=[],
        actor_knowledge=values,
        events=events,
        budget_chars=100_000,
    )
    target = next(item for item in complete.semantic if item.record["id"] == "knowledge-99")
    bounded = select_actor_memory_context(
        actor_state=[],
        actor_knowledge=values,
        events=events,
        query="",
        budget_chars=target.cost_chars,
    )

    assert bounded.semantic[0].record["id"] == "knowledge-99"
    assert bounded.semantic[0].signals == {
        "exact_ref_matches": 0,
        "query": 0,
        "recency": 100,
        "confidence": 5,
        "salience": 5,
    }
    assert bounded.diagnostics["candidate_count"] == 14
    assert bounded.diagnostics["omitted_for_budget"] >= 12


def test_current_interlocutor_and_scene_refs_outrank_unrelated_noise() -> None:
    knowledge = [_knowledge(index, confidence=5, salience=5) for index in range(10)]
    knowledge.append(
        _knowledge(
            20,
            proposition="Mira carries the brass observatory key.",
            subject_ref="actor:mira",
        )
    )
    events = [_event(index) for index in range(1, 8)]
    events.append(
        _event(
            20,
            summary="A bell rang in the flooded observatory.",
            scene_id="flooded-observatory",
        )
    )

    context = select_actor_memory_context(
        actor_state=[],
        actor_knowledge=knowledge,
        events=events,
        current_refs=("actor:mira", "scene:flooded-observatory"),
        budget_chars=100_000,
    )

    first_two = context.diagnostics["selection_order"][:2]
    assert {item["basis_ref"] for item in first_two} == {
        "knowledge:knowledge-20:revision-20",
        "event:event-20",
    }
    assert all(item["signals"]["exact_ref_matches"] == 1 for item in first_two)


def test_tracks_share_one_selector_without_proposing_pc_intent() -> None:
    actor_state = {
        "id": "actor-pc",
        "revision": 7,
        "name": "Mira",
        "character_type": "pc",
        "profile": {"pronouns": "she/her"},
        "state_facts": [
            {
                "id": "fact-goal",
                "revision_id": "fact-revision",
                "kind": "actor_state",
                "fact_key": "actor:mira:goal:observatory",
                "subject_ref": "actor:actor-pc",
                "predicate": "goal",
                "content": "Recover the lost observatory charts.",
                "importance": 5,
            }
        ],
    }
    context = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=[_knowledge(1, proposition="The west stairs are flooded.")],
        events=[_event(1, summary="Mira entered the observatory.")],
        query="observatory",
        budget_chars=100_000,
    )

    assert [item.source for item in context.identity] == ["actor_state"]
    assert [item.source for item in context.motivational] == ["actor_state_fact"]
    assert [item.source for item in context.semantic] == ["actor_knowledge"]
    assert [item.source for item in context.episodic] == ["event"]
    assert "intent" not in str(context.as_dict()).casefold()


def test_budget_preserves_each_represented_track_when_the_floor_fits() -> None:
    noisy_identity = [
        {
            "id": f"identity-{index}",
            "kind": "identity",
            "content": f"Urgent current hero identity {index}",
            "salience": 5,
            "updated_at": f"2026-08-30T00:{index:02d}:00Z",
        }
        for index in range(10)
    ]
    actor_state = [
        *noisy_identity,
        {
            "id": "goal",
            "kind": "goal",
            "predicate": "goal",
            "content": "Protect the witness.",
            "importance": 1,
        },
    ]
    knowledge = [_knowledge(1, proposition="The east door uses a brass key.")]
    events = [_event(1, summary="The witness renewed an old promise.")]
    complete = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=knowledge,
        events=events,
        query="urgent current hero identity",
        budget_chars=100_000,
    )
    floor_budget = sum(
        next(
            item["cost_chars"]
            for item in complete.diagnostics["selection_order"]
            if item["track"] == track
        )
        for track in ("identity", "motivational", "semantic", "episodic")
    )

    bounded = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=knowledge,
        events=events,
        query="urgent current hero identity",
        budget_chars=floor_budget,
    )

    assert bounded.diagnostics["track_selected"] == {
        "identity": 1,
        "motivational": 1,
        "semantic": 1,
        "episodic": 1,
    }
    assert bounded.diagnostics["used_chars"] <= floor_budget


def test_budget_is_strict_and_smaller_lower_ranked_items_can_still_fit() -> None:
    oversized = _knowledge(
        1,
        proposition="relevant " * 1_000,
        subject_ref="scene:current",
        confidence=5,
        salience=5,
    )
    compact = _knowledge(2, proposition="A compact usable memory.")
    complete = select_actor_memory_context(
        actor_state=[],
        actor_knowledge=[oversized, compact],
        events=[],
        current_refs=("scene:current",),
        budget_chars=100_000,
    )
    compact_cost = next(
        item.cost_chars for item in complete.semantic if item.record["id"] == "knowledge-02"
    )

    bounded = select_actor_memory_context(
        actor_state=[],
        actor_knowledge=[oversized, compact],
        events=[],
        current_refs=("scene:current",),
        budget_chars=compact_cost,
    )

    assert [item.record["id"] for item in bounded.semantic] == ["knowledge-02"]
    assert bounded.diagnostics["used_chars"] == compact_cost
    assert bounded.diagnostics["used_chars"] <= bounded.diagnostics["budget_chars"]
    assert bounded.diagnostics["omitted_for_budget"] == 1
    with pytest.raises(ValueError, match="must not be negative"):
        select_actor_memory_context(
            actor_state=[], actor_knowledge=[], events=[], budget_chars=-1
        )


def test_selection_and_deduplication_are_deterministic_across_input_order() -> None:
    older = _knowledge(1, proposition="The bridge is watched.", source_event_id="event-01")
    newer = {
        **older,
        "id": "knowledge-newer-id",
        "revision_id": "revision-newer",
        "proposition": "The bridge is no longer watched.",
        "source_event_id": "event-09",
    }
    same_content = {
        **_knowledge(3, proposition="The bridge is no longer watched."),
        "source_event_id": "event-08",
    }
    knowledge = [older, newer, same_content, _knowledge(4)]
    events = [_event(1), _event(8), _event(9)]
    arguments = {
        "actor_state": [],
        "current_refs": (),
        "query": "bridge watched",
        "budget_chars": 100_000,
    }

    forward = select_actor_memory_context(
        **arguments,
        actor_knowledge=deepcopy(knowledge),
        events=deepcopy(events),
    )
    reverse = select_actor_memory_context(
        **arguments,
        actor_knowledge=list(reversed(deepcopy(knowledge))),
        events=list(reversed(deepcopy(events))),
    )

    assert forward.as_dict() == reverse.as_dict()
    selected_ids = {item.record["id"] for item in forward.semantic}
    assert "knowledge-newer-id" in selected_ids
    assert "knowledge-01" not in selected_ids
    assert "knowledge-03" not in selected_ids
    assert forward.diagnostics["duplicates_dropped"] == 2
