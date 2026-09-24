from types import SimpleNamespace

import pytest
from sagasmith_dnd.content_validation import build_catalog_review
from sagasmith_dnd_runtime.services.diseases import DiseasesService


def _artifact(
    *,
    status: str = "approved",
    artifact_id: str = "campaign.diseases.cackle_fever",
) -> dict:
    artifact = {
        "id": artifact_id,
        "kind": "disease_variant",
        "card": {"name": "Campaign Cackle Fever"},
        "source_citations": [
            {
                "source": "campaign-source:disease-appendix",
                "source_ref": {"section": "cackle_fever"},
                "source_excerpt": "Reviewed campaign Cackle Fever rule.",
            }
        ],
        "disease_variant": {
            "schema_version": 1,
            "edition": "2014",
            "disease_id": "cackle_fever",
            "save_dcs": {"infection": 12, "laughter": 12, "recovery": 12, "spread": 9},
            "incubation": {"die": "1d6", "unit": "hour"},
            "eligible_creature_types": ["humanoid", "beast"],
            "symptoms": {"laughter_duration_ticks": 7},
        },
    }
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            {
                "role": "dm",
                "reviewer": "campaign-dm",
                "method": "human",
                "checks": {
                    "identity": True,
                    "classification": True,
                    "entry_boundary": True,
                    "references": True,
                },
                "notes": "Reviewed against the campaign source.",
            }
        ],
        status=status,
    )
    return artifact


class _FakeRulePacks:
    def __init__(
        self,
        artifact: dict,
        *,
        locked_checksum: str | None = None,
        status: str = "installed",
    ):
        self.artifact = artifact
        self.checksum = "b" * 64
        self.locked_checksum = locked_checksum or self.checksum
        self.status = status
        self.requested_branch = None

    def effective_ruleset(self, campaign_id: str, *, branch_id: str | None = None):
        self.requested_branch = branch_id
        return SimpleNamespace(
            edition="2014",
            lock=(
                {
                    "pack_id": "campaign.diseases",
                    "version": "1.0.0",
                    "checksum": self.locked_checksum,
                },
            ),
        )

    def get_version(self, pack_id: str, version: str):
        assert (pack_id, version) == ("campaign.diseases", "1.0.0")
        return SimpleNamespace(
            status=self.status,
            checksum=self.checksum,
            artifacts=(self.artifact,),
        )


def _service(rule_packs: _FakeRulePacks) -> DiseasesService:
    service = object.__new__(DiseasesService)
    service.rule_packs = rule_packs
    return service


def test_campaign_variant_resolves_only_from_exact_active_branch_lock():
    packs = _FakeRulePacks(_artifact())
    service = _service(packs)

    profile = service.campaign_disease_profile("campaign-1", "cackle_fever", branch_id="branch-2")

    assert packs.requested_branch == "branch-2"
    assert profile["save_dcs"]["spread"] == 9
    assert profile["incubation"] == {"die": "1d6", "unit": "hour"}
    assert profile["eligible_creature_types"] == ["humanoid", "beast"]
    assert profile["variant_receipt"]["pack_checksum"] == packs.checksum


@pytest.mark.parametrize(
    ("artifact", "locked_checksum", "status", "message"),
    [
        (_artifact(status="needs_review"), None, "installed", "approved content review"),
        (_artifact(), "c" * 64, "installed", "exact installed lock"),
        (_artifact(), None, "validated", "exact installed lock"),
    ],
)
def test_campaign_variant_rejects_unreviewed_or_noninstalled_pack(
    artifact, locked_checksum, status, message
):
    service = _service(
        _FakeRulePacks(artifact, locked_checksum=locked_checksum, status=status)
    )
    with pytest.raises(ValueError, match=message):
        service.campaign_disease_profile("campaign-1", "cackle_fever", branch_id="main")


def test_campaign_variant_rejects_tampered_review_content_and_multiple_definitions():
    tampered = _artifact()
    tampered["disease_variant"]["save_dcs"]["spread"] = 8
    with pytest.raises(ValueError, match="review is stale"):
        _service(_FakeRulePacks(tampered)).campaign_disease_profile("campaign-1", "cackle_fever")

    duplicate = _artifact(artifact_id="campaign.diseases.cackle_fever_copy")
    packs = _FakeRulePacks(_artifact())
    packs.get_version = lambda pack_id, version: SimpleNamespace(
        status="installed",
        checksum=packs.checksum,
        artifacts=(packs.artifact, duplicate),
    )
    with pytest.raises(ValueError, match="multiple active campaign variants"):
        _service(packs).campaign_disease_profile("campaign-1", "cackle_fever")


def test_campaign_variant_rejects_non_2014_branch():
    packs = _FakeRulePacks(_artifact())
    packs.effective_ruleset = lambda campaign_id, *, branch_id=None: SimpleNamespace(
        edition="2024", lock=()
    )
    with pytest.raises(ValueError, match="require a 2014 campaign"):
        _service(packs).campaign_disease_profile("campaign-1", "cackle_fever")
