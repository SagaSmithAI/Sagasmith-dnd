from pathlib import Path

import pytest

from sagasmith_dnd.content_import import _merge_species_grants
from sagasmith_dnd.core_content import build_srd2014_content


@pytest.mark.parametrize(
    "species,name,kind,source_file",
    [
        ("Dwarf", "Dwarven Resilience", "dwarven_resilience", "Dwarf.md"),
        ("Hill Dwarf", "Dwarven Resilience", "dwarven_resilience", "Dwarf.md"),
        ("Elf", "Fey Ancestry", "fey_ancestry", "Elf.md"),
        ("High Elf", "Fey Ancestry", "fey_ancestry", "Elf.md"),
        ("Half-Elf", "Fey Ancestry", "fey_ancestry", "Half-Elf.md"),
        ("Gnome", "Gnome Cunning", "gnome_cunning", "Gnome.md"),
        ("Rock Gnome", "Gnome Cunning", "gnome_cunning", "Gnome.md"),
        ("Halfling", "Brave", "halfling_brave", "Halfling.md"),
        ("Lightfoot", "Brave", "halfling_brave", "Halfling.md"),
    ],
)
def test_conditional_save_traits_are_source_bound_in_real_species_cards(
    species, name, kind, source_file
):
    _, artifacts = build_srd2014_content(Path(__file__).resolve().parents[3] / "skills")
    artifact = next(
        item for item in artifacts if item["kind"] == "species" and item["card"]["name"] == species
    )
    grants = artifact["card"]["grants"]
    feature = next(item for item in grants["features"] if item["name"] == name)
    mechanic = f"dnd5e.core.save.{kind}"
    assert feature["mechanic_refs"] == [mechanic]
    assert mechanic in artifact["mechanic_refs"]
    assert any(source_file in ref for ref in artifact["rule_refs"])
    trait = feature["choices"]["source_trait"]
    assert trait["kind"] == kind
    assert trait["automatic"] is True
    assert trait["source_excerpt"] == feature["description"]
    assert "advantage" in trait["source_excerpt"]
    assert trait.get("magical_sleep_immunity", False) is (kind == "fey_ancestry")
    if kind == "dwarven_resilience":
        assert "poison" in grants["resistances"]

    # Replacing this named trait must remove its executable feature, without
    # erasing unrelated species grants. This is the existing import merger.
    replaced = _merge_species_grants(grants, {}, replaced_base_traits={name.casefold()})
    assert all(item["name"] != name for item in replaced["features"])
    assert replaced["languages"] == grants["languages"]
    if kind == "dwarven_resilience":
        assert "poison" not in replaced["resistances"]
