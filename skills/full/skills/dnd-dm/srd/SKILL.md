---
name: dnd-rule-sources
description: "Use the bundled D&D 5e 2014/2024 SRD corpora through the reviewed SagaSmith MCP rule-import and retrieval workflow."
---

# D&D Rule Sources

This Skill carries three optional corpora:

- `references/`: SRD 5.2.1 / 2024 English.
- `references-2014-en/`: SRD 5.1 / 2014 English.
- `references-2014-zh/`: SRD 5.1 / 2014 Chinese convenience translation.

Runtime ingestion is a reviewed lobby workflow. Do not call a retired direct
ingestion surface:

Call `rulebook_draft(action="start")` with a source path inside the configured rule
import roots and set `source_key`, `title`, `edition`, `locale`, and
`publication_id` (`srd-5.2.1`, `srd-5.1`, or `srd-5.1-zh`). Then call
`rulebook_draft(action="get")` and `rulebook_draft(action="start")` with the same
job. Use `rule_search` and `rule_expand` during play. Candidate extraction,
compilation, installation, and activation are only for a reviewed executable
extension pack; ordinary SRD evidence stops after ingest.

The MCP's default rule-import allowlist includes this Skill's `srd/` directory
when the Skill repository is configured. An external Host must configure the
same path explicitly if it relocates the corpus.

The campaign rule profile controls search isolation. Do not search both editions
unless the user asks for a comparison. For a consequential 2014 ruling, use the
English corpus as the final reference when the Chinese translation is ambiguous.
The bundled content catalog and statblock parser are also edition-pinned. Use
`dnd5e_2014_statblock` only with 2014 and `dnd5e_2024_statblock` only with 2024.
Bounded layout-OCR recovery currently supports 2014 only; 2024 requires complete
indexed text or literal review by an image-capable Agent. Never borrow a 2014
artifact id, formula, recovery rule, or citation for a 2024 settlement merely
because both editions use the same internal function.
