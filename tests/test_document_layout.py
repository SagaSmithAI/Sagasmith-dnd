from sagasmith_core.documents import GENERIC_DOCUMENT_LAYOUT_PROFILE

from sagasmith_dnd.document_layout import DND5E_DOCUMENT_LAYOUT_PROFILE


def test_dnd_layout_excludes_rule_fields_and_attack_lines_only_in_system_profile() -> None:
    examples = (
        "Casting Time: 1 action",
        "Range: 150 feet",
        "Club. Melee Weapon Attack: +2 to hit",
    )

    assert all(DND5E_DOCUMENT_LAYOUT_PROFILE.excludes_visual_heading(value) for value in examples)
    assert not any(
        GENERIC_DOCUMENT_LAYOUT_PROFILE.excludes_visual_heading(value) for value in examples
    )
    assert DND5E_DOCUMENT_LAYOUT_PROFILE.excludes_repeated_margin("Casting Time: 1 action")
    assert not GENERIC_DOCUMENT_LAYOUT_PROFILE.excludes_repeated_margin("Casting Time: 1 action")
