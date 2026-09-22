"""2014 Working Together: card-derived eligibility before one aided check."""

from __future__ import annotations

from typing import Any

from .abilities import ABILITY_NAMES, SKILL_ABILITIES
from .character_schema import effective_ability_modifier
from .combat_engine import CombatEngineError, NeedsRulingError, actor_sheet, resolve_actor_check
from .conditions import INCAPACITATING_STATE_IDS
from .engine import proficiency_bonus
from .rule_engine import ResolutionContext, core_receipts

BOUNDARY_ID = "dnd5e.core.check.working_together"


def _key(value: str) -> str:
    return " ".join(value.strip().casefold().replace("’", "'").replace("_", " ").split())


def resolve_working_together(
    actors: list[dict[str, Any]], *, ability: str, dc: int, task: dict[str, Any],
    leader_id: str | None = None, skill_ability: str | None = None, tool: str | None = None,
    rules_by_actor_id: dict[str, ResolutionContext] | None = None, rng: Any = None,
) -> dict[str, Any]:
    """The DM classifies a sourced task; no caller may supply actor eligibility."""
    identifiers = [actor.get("id") for actor in actors]
    if (len(actors) < 2 or any(not isinstance(i, str) or not i for i in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise CombatEngineError("working together requires distinct leader/helper actor cards")
    if ability not in {*ABILITY_NAMES, *SKILL_ABILITIES}:
        raise CombatEngineError("working together requires a known ability or skill")
    if skill_ability is not None and (
        ability not in SKILL_ABILITIES or skill_ability not in ABILITY_NAMES
    ):
        raise CombatEngineError("skill_ability requires a skill and valid ability")
    if tool is not None and (not isinstance(tool, str) or not tool.strip()
                             or ability not in ABILITY_NAMES):
        raise CombatEngineError("tool checks require one tool and a base ability")
    if type(dc) is not int or not 0 <= dc <= 100:
        raise CombatEngineError("working together requires an integer dc from 0 to 100")
    if task.get("productive") is not True:
        raise CombatEngineError("the sourced task must permit productive collaboration")
    requirements = task.get("requirements")
    if not isinstance(requirements, dict) or set(requirements) != {"tools", "skills", "features"}:
        raise CombatEngineError("task requirements must explicitly list tools, skills and features")
    for kind, entries in requirements.items():
        if (not isinstance(entries, list)
                or any(not isinstance(item, str) or not item.strip() for item in entries)
                or len(set(entries)) != len(entries)):
            raise CombatEngineError(f"task requirements.{kind} must be distinct nonempty strings")
    if set(requirements["skills"]) - set(SKILL_ABILITIES):
        raise CombatEngineError("task requirements contain unknown skills")
    for flag in ("relies_on_sight", "relies_on_hearing"):
        if flag in task and type(task[flag]) is not bool:
            raise CombatEngineError(f"task {flag} must be a boolean")
    selected_ability = skill_ability or SKILL_ABILITIES.get(ability, ability)
    cards = {}
    modifiers = {}
    for actor in actors:
        identifier = actor["id"]
        sheet = actor_sheet(actor)
        cards[identifier] = sheet
        if sheet["edition"] != "2014":
            raise CombatEngineError("working together is a source-reviewed 2014 procedure")
        conditions = set(sheet["conditions"])
        if conditions & INCAPACITATING_STATE_IDS:
            raise CombatEngineError(f"{identifier} cannot attempt this task while incapacitated")
        for condition, reliance in (("blinded", "relies_on_sight"),
                                    ("deafened", "relies_on_hearing")):
            if condition in conditions:
                if reliance not in task:
                    raise NeedsRulingError(
                        "working together requires sensory task facts", missing=(reliance,),
                        ruling_kind="source_or_scene_fact",
                    )
                if task[reliance]:
                    raise CombatEngineError(f"{identifier} cannot attempt this sensory task alone")
        proficiencies = sheet["traits"]["proficiencies"]
        owned_tools = {_key(item) for item in proficiencies["tools"]}
        if {_key(item) for item in requirements["tools"]} - owned_tools:
            raise CombatEngineError(f"{identifier} lacks required tool proficiency")
        if any(sheet["skills"][skill]["proficiency"] not in {"proficient", "expertise"}
               for skill in requirements["skills"]):
            raise CombatEngineError(f"{identifier} lacks required skill proficiency")
        if set(requirements["features"]) - {item["id"] for item in sheet["content"]["features"]}:
            raise CombatEngineError(f"{identifier} lacks a required source feature")
        modifiers[identifier] = effective_ability_modifier(sheet, selected_ability)
    if leader_id is None:
        highest = max(modifiers.values())
        candidates = [identifier for identifier in identifiers if modifiers[identifier] == highest]
        if len(candidates) != 1:
            raise NeedsRulingError(
                "choose leader_id to resolve the highest ability modifier tie: "
                + ", ".join(candidates), missing=("leader_id",),
                ruling_kind="player_owned_choice" if all(
                    actor.get("character_type", "pc") == "pc"
                    for actor in actors if actor["id"] in candidates
                ) else "agent_dm_adjudication",
            )
        leader_id = candidates[0]
    if leader_id not in cards:
        raise CombatEngineError("leader_id must identify one of the participating actors")
    leader = next(actor for actor in actors if actor["id"] == leader_id)
    proficiencies = cards[leader_id]["traits"]["proficiencies"]
    tool_proficient = tool is not None and _key(tool) in {
        _key(item) for item in proficiencies["tools"]
    }
    tool_expertise = tool_proficient and (
        proficiencies["tool_expertise_all"]
        or _key(tool) in {_key(item) for item in proficiencies["tool_expertise"]}
    )
    rules = (rules_by_actor_id or {}).get(leader_id)
    result = resolve_actor_check(
        leader, kind="check", ability=ability, skill_ability=skill_ability,
        dc=dc, advantage=True, proficient=tool_proficient,
        bonus=proficiency_bonus(cards[leader_id]["progression"]["level"]) if tool_expertise else 0,
        rules=rules, rng=rng,
    )
    result["rule_receipts"] = [*result["rule_receipts"], *core_receipts(
        rules, [BOUNDARY_ID], "check.working_together",
    )]
    return {
        "leader_id": leader_id, "helper_ids": [i for i in identifiers if i != leader_id],
        "ability": ability, "skill_ability": skill_ability, "tool": tool,
        "ability_modifiers": modifiers, "advantage_source": "working_together",
        "check": result, "rule_receipts": result["rule_receipts"],
    }
