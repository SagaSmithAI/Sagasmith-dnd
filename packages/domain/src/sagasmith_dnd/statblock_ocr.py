"""System-owned deterministic selection and corroboration for statblock OCR."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable

from sagasmith_core.text import compact_ascii_key

from sagasmith_dnd.statblocks import StatblockImportError, recover_2014_statblock_from_ocr

LayoutLoader = Callable[[int, float, str, bool], dict[str, Any]]
PageTextLoader = Callable[[int], str]


def ocr_fact_key(value: Any) -> str:
    """Fold harmless OCR diacritics before independent fact comparison."""

    return compact_ascii_key(unicodedata.normalize("NFKD", str(value or "")))


def statblock_critical_fingerprint(value: Any) -> Any:
    """Normalize nested critical facts without discarding optional fields."""

    if isinstance(value, dict):
        return {
            str(key): statblock_critical_fingerprint(item) for key, item in value.items()
        }
    return ocr_fact_key(value)


def matching_statblock_recovery_pair(
    recoveries: list[tuple[float, dict[str, Any]]],
) -> tuple[tuple[float, dict[str, Any]], tuple[float, dict[str, Any]]] | None:
    """Find the earliest two OCR scales agreeing on every critical fact."""

    for index, left in enumerate(recoveries):
        left_fingerprint = statblock_critical_fingerprint(dict(left[1]["critical_facts"]))
        for right in recoveries[index + 1 :]:
            if left_fingerprint == statblock_critical_fingerprint(
                dict(right[1]["critical_facts"])
            ):
                return left, right
    return None


def _corroboration_pairs(critical_facts: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("Identity", str(critical_facts["identity"])),
        ("Armor Class", f"Armor Class {critical_facts['armor_class']}"),
        ("Hit Points", f"Hit Points {critical_facts['hit_points']}"),
        ("Speed", f"Speed {critical_facts['speed']}"),
        ("Challenge", f"Challenge {critical_facts['challenge']}"),
        *[
            (label, f"{label} {value}")
            for label, value in dict(critical_facts["fields"]).items()
        ],
        *[
            (ability.upper(), f"{ability.upper()} {score}")
            for ability, score in dict(critical_facts["abilities"]).items()
        ],
    ]


def recover_2014_pdf_statblock_layout(
    *,
    target_name: str,
    candidate_pages: list[int],
    provider_name: str,
    primary_scale: float,
    preferred_model: str,
    load_layout: LayoutLoader,
    extract_page_text: PageTextLoader,
    statblock_slot: int | None = None,
    ocr_corrections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover and independently corroborate one printed 2014 statblock.

    Rendering, model construction, and caching stay with the caller. Given the
    resulting layouts and embedded page text, selection and mechanical
    agreement are deterministic D&D system behavior.
    """

    selected_scale: float | None = None
    selected_model: str | None = None
    recovered: dict[str, Any] | None = None
    attempted_pages: list[int] = []
    recovery_models = [
        preferred_model,
        "medium" if preferred_model == "small" else "small",
    ]
    recovery_scales = list(
        dict.fromkeys((primary_scale, 2.5, 3.0, 1.5, 3.5, 4.0, 2.0))
    )
    for recovery_model in recovery_models:
        for candidate in candidate_pages:
            if candidate not in attempted_pages:
                attempted_pages.append(candidate)
            for scale in recovery_scales:
                layout = load_layout(
                    candidate,
                    scale,
                    recovery_model,
                    recovery_model == preferred_model and abs(scale - primary_scale) < 0.001,
                )
                try:
                    candidate_recovery = recover_2014_statblock_from_ocr(
                        layout,
                        name=target_name,
                        minimum_confidence=0.5,
                        statblock_slot=statblock_slot,
                        reviewed_ability_scores=dict(ocr_corrections or {}).get("abilities"),
                        reviewed_text_replacements=dict(ocr_corrections or {}).get(
                            "text_replacements"
                        ),
                    )
                except StatblockImportError:
                    continue
                selected_scale = scale
                selected_model = recovery_model
                recovered = candidate_recovery
                break
            if recovered is not None:
                break
        if recovered is not None:
            break
    if recovered is None or selected_scale is None or selected_model is None:
        raise RuntimeError(
            "layout OCR did not find one structurally unambiguous target statblock "
            "on candidate pages " + ", ".join(str(value) for value in attempted_pages)
        )

    evidence = dict(recovered["evidence"])
    recovered_page = int(evidence["page_number"])
    page_text = extract_page_text(recovered_page)
    critical_facts = dict(recovered["critical_facts"])
    identity_key = ocr_fact_key(critical_facts["identity"])
    page_lines = [
        line.strip(" \t#>*_-")
        for line in page_text.splitlines()
        if line.strip(" \t#>*_-")
    ]
    identity_indexes = [
        index for index, line in enumerate(page_lines) if ocr_fact_key(line) == identity_key
    ]
    corroboration_pairs = _corroboration_pairs(critical_facts)
    corroboration_mode = "dual_layout_ocr"
    corroboration_scales = [selected_scale]
    corroboration_models = [selected_model]
    corroborated = [{"field": label, "value": fact} for label, fact in corroboration_pairs]
    if len(identity_indexes) == 1:
        segment_start = identity_indexes[0]
        segment_end = len(page_lines)
        identity_pattern = re.compile(
            r"(?i)^(Tiny|Small|Medium|Large|Huge|Gargantuan)\s+[^,]+,\s*.+$"
        )
        for index in range(segment_start + 1, len(page_lines)):
            if identity_pattern.fullmatch(page_lines[index]):
                segment_end = index
                break
        normalized_segment = ocr_fact_key("\n".join(page_lines[segment_start:segment_end]))
        ability_order = ("str", "dex", "con", "int", "wis", "cha")
        ability_scores = dict(critical_facts["abilities"])
        ordered_ability_table = (
            ocr_fact_key(" ".join(ability.upper() for ability in ability_order))
            in normalized_segment
            and ocr_fact_key(" ".join(str(ability_scores[ability]) for ability in ability_order))
            in normalized_segment
        )
        embedded_mismatches = [
            label
            for label, fact in corroboration_pairs
            if ocr_fact_key(fact) not in normalized_segment
            and not (label.casefold() in ability_order and ordered_ability_table)
        ]
        if not embedded_mismatches:
            corroboration_mode = "embedded_text"

    if corroboration_mode != "embedded_text":
        primary_fingerprint = statblock_critical_fingerprint(critical_facts)
        secondary_scale: float | None = None
        secondary_model: str | None = None
        secondary_failures: list[str] = []
        secondary_recoveries: list[tuple[str, float, dict[str, Any]]] = []
        for recovery_model in recovery_models:
            for candidate_scale in (3.0, 2.5, 1.5, 3.5, 4.0, 2.0):
                if (
                    recovery_model == selected_model
                    and abs(selected_scale - candidate_scale) < 0.01
                ):
                    continue
                label = f"{recovery_model}@{candidate_scale:.1f}"
                try:
                    secondary = recover_2014_statblock_from_ocr(
                        load_layout(
                            recovered_page,
                            candidate_scale,
                            recovery_model,
                            recovery_model == preferred_model
                            and abs(candidate_scale - primary_scale) < 0.001,
                        ),
                        name=target_name,
                        minimum_confidence=0.5,
                        statblock_slot=statblock_slot,
                        reviewed_ability_scores=dict(ocr_corrections or {}).get("abilities"),
                        reviewed_text_replacements=dict(ocr_corrections or {}).get(
                            "text_replacements"
                        ),
                    )
                except StatblockImportError as exc:
                    secondary_failures.append(f"{label}: {exc}")
                    continue
                secondary_recoveries.append((recovery_model, candidate_scale, secondary))
                if primary_fingerprint == statblock_critical_fingerprint(
                    dict(secondary["critical_facts"])
                ):
                    secondary_model = recovery_model
                    secondary_scale = candidate_scale
                    break
                secondary_failures.append(f"{label}: critical facts disagree")
            if secondary_scale is not None:
                break
        if secondary_scale is None:
            matching_pair = next(
                (
                    (left, right)
                    for index, left in enumerate(secondary_recoveries)
                    for right in secondary_recoveries[index + 1 :]
                    if statblock_critical_fingerprint(dict(left[2]["critical_facts"]))
                    == statblock_critical_fingerprint(dict(right[2]["critical_facts"]))
                ),
                None,
            )
            if matching_pair is None:
                raise RuntimeError(
                    "no independent layout OCR model/scale corroborated all critical "
                    "statblock facts; " + "; ".join(secondary_failures)
                )
            (
                (selected_model, selected_scale, recovered),
                (secondary_model, secondary_scale, _corroboration),
            ) = matching_pair
            evidence = dict(recovered["evidence"])
            critical_facts = dict(recovered["critical_facts"])
            corroboration_pairs = _corroboration_pairs(critical_facts)
            corroborated = [
                {"field": label, "value": fact} for label, fact in corroboration_pairs
            ]
            corroboration_scales = [selected_scale]
            corroboration_models = [selected_model]
        corroboration_scales.append(secondary_scale)
        if secondary_model is None:
            raise RuntimeError("independent OCR corroboration has no model identity")
        corroboration_models.append(secondary_model)

    observation = (
        f"Text-only layout OCR v{int(evidence['recovery_version'])} recovered "
        f"{target_name} from PDF page {recovered_page}; heading confidence "
        f"{float(evidence['heading_confidence']):.5f}, minimum core confidence "
        f"{float(evidence['minimum_core_confidence']):.5f}. Independent "
        f"{corroboration_mode.replace('_', ' ')} corroborated identity, Armor Class, "
        "Hit Points, Speed, all six ability scores, and Challenge."
    )
    return {
        "name": target_name,
        "attempted_pages": attempted_pages,
        "page_number": recovered_page,
        "provider": provider_name,
        "ocr_model": selected_model,
        "recovery_scale": selected_scale,
        "corroboration_mode": corroboration_mode,
        "corroboration_scales": corroboration_scales,
        "corroboration_models": corroboration_models,
        "corroborated_facts": corroborated,
        "observation": observation,
        "recovery": recovered,
    }
