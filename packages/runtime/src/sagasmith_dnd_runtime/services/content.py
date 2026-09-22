"""Content application operations with explicit shared services."""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal, Mapping

from .. import application_support as _support


class ContentService:
    def verified_content_authority_ids(
        self, sheet: Mapping[str, Any], *, character_id: str | None
    ) -> frozenset[str]:
        """Verify server-issued content capabilities before privileged derivation."""

        return _support._verified_content_authority_ids(
            sheet,
            character_id=character_id,
            secret=self.content_authority_secret,
        )

    def import_page_revisions(self, job: Any) -> list[dict[str, Any]]:
        revisions = dict(job.inspection or {}).get("page_revisions", [])
        if not isinstance(revisions, list):
            raise RuntimeError("import inspection page_revisions must be an array")
        return [_support.deepcopy(dict(item)) for item in revisions if isinstance(item, dict)]

    def effective_rule_context_from(
        self,
        effective: Any,
        profile: Any,
        *,
        facts: dict[str, Any] | None = None,
    ) -> Any:
        """Compile a fetched lock without introducing a second state read."""

        value = _support.asdict(effective)
        enabled_pack_ids = {str(item["pack_id"]) for item in effective.lock}
        native = [
            {
                "id": provider.id,
                "abi_version": provider.abi_version,
                "pack_id": provider.pack_id,
                "mechanics": provider.mechanics(),
            }
            for provider in self.native_rule_providers.values()
            if provider.pack_id in enabled_pack_ids
        ]
        if native:
            value["mechanics"] = [
                *list(value["mechanics"]),
                *(mechanic for provider in native for mechanic in provider["mechanics"]),
            ]
            value["fingerprint"] = _support.json_sha256(
                {"base": effective.fingerprint, "native": native}
            )
        try:
            context = _support.resolution_context(value, facts=facts)
        except ValueError as error:
            raise _support.RulesetUnavailableError(
                f"campaign rule composition is unavailable: {error}"
            ) from error
        expected_core = dict((profile.options if profile else {}).get("_core_rule_pack_lock") or {})
        if not expected_core:
            raise _support.RulesetUnavailableError(
                "campaign has no locked built-in core rule pack; "
                "the DM must explicitly set the campaign rule profile"
            )
        if expected_core != {
            "id": context.core_pack.id,
            "version": context.core_pack.version,
            "fingerprint": context.core_pack.fingerprint,
        }:
            raise _support.RulesetUnavailableError(
                "locked built-in core rule pack is unavailable; "
                "runtime upgrade needs explicit relock"
            )
        identity = dict((profile.options if profile else {}).get("_implementation_identity") or {})
        try:
            _support.require_compatible_build(identity)
        except ValueError as error:
            raise _support.RulesetUnavailableError(str(error)) from error
        return context

    def effective_rule_context(
        self,
        campaign_id: str,
        *,
        facts: dict[str, Any] | None = None,
        branch_id: str | None = None,
    ) -> Any:
        """Compile the exact current branch lock for one pure runtime call."""
        effective = self.rule_packs.effective_ruleset(campaign_id, branch_id=branch_id)
        return self.effective_rule_context_from(
            effective,
            self.rule_profiles.get(campaign_id),
            facts=facts,
        )

    def campaign_rules_edition(self, campaign_id: str) -> str:
        """Read the sole campaign-edition authority."""
        profile = self.rule_profiles.get(campaign_id)
        if profile is not None and str(profile.edition).strip():
            return _support.normalize_dnd_edition(profile.edition)
        raise _support.RulesetUnavailableError("campaign has no authoritative rule profile")

    def statblock_content_kind(self, edition: str) -> str:
        """Return the review kind bound to one authoritative D&D edition."""

        normalized = _support.normalize_dnd_edition(edition)
        return f"dnd5e_{normalized}_statblock"

    def effective_ruleset_view_from(self, effective: Any, profile: Any) -> dict[str, Any]:
        """Render one already-resolved lock, including its built-in core."""

        context = self.effective_rule_context_from(effective, profile)
        value = _support.asdict(effective)
        value["extension_fingerprint"] = value["fingerprint"]
        value["fingerprint"] = context.fingerprint
        value["core_pack"] = {
            "id": context.core_pack.id,
            "version": context.core_pack.version,
            "edition": context.core_pack.edition,
            "fingerprint": context.core_pack.fingerprint,
        }
        return value

    def effective_ruleset_view(
        self, campaign_id: str, *, branch_id: str | None = None
    ) -> dict[str, Any]:
        effective = self.rule_packs.effective_ruleset(campaign_id, branch_id=branch_id)
        return self.effective_ruleset_view_from(effective, self.rule_profiles.get(campaign_id))

    def ensure_core_content_pack(self) -> None:
        """Install the structured SRD catalog once; availability is edition-based."""
        if not self.config.dnd_skills_dir.exists():
            return
        try:
            existing = self.rule_packs.get_version(
                _support.CORE_CONTENT_PACK_ID, _support.CORE_CONTENT_PACK_VERSION
            )
            if existing.status == "installed":
                return
        except LookupError:
            pass
        manifest, artifacts = _support.build_srd2014_content(self.config.dnd_skills_dir)
        if not artifacts:
            return
        source_definition_checksum = _support.content_definition_checksum(
            manifest=manifest, artifacts=artifacts, mechanics=[]
        )
        manifest, native_errors = self.bind_native_mechanic_contract(
            manifest,
            artifacts,
            [],
        )
        result = self.rule_packs.save_draft(
            manifest=manifest,
            artifacts=artifacts,
            provenance={
                "source": "bundled-srd2014",
                "structured": True,
                "content_definition": {
                    "source_definition_checksum": source_definition_checksum,
                    "definition_checksum": _support.content_definition_checksum(
                        manifest=manifest, artifacts=artifacts, mechanics=[]
                    ),
                },
            },
            additional_errors=native_errors,
        )
        if result.status == "validated":
            self.rule_packs.install(
                _support.CORE_CONTENT_PACK_ID, _support.CORE_CONTENT_PACK_VERSION
            )

    def ensure_standard2014_content_pack(self) -> None:
        """Install mechanics-only standard cards that are not part of the SRD."""

        try:
            existing = self.rule_packs.get_version(
                _support.STANDARD_2014_CONTENT_PACK_ID,
                _support.STANDARD_2014_CONTENT_PACK_VERSION,
            )
            if existing.status == "installed":
                return
        except LookupError:
            pass
        manifest, artifacts = _support.build_standard2014_content()
        manifest, native_errors = self.bind_native_mechanic_contract(
            manifest,
            artifacts,
            [],
        )
        result = self.rule_packs.save_draft(
            manifest=manifest,
            artifacts=artifacts,
            provenance={
                "source": "built-in-standard2014-mechanics",
                "structured": True,
                "copyrighted_prose_embedded": False,
            },
            additional_errors=native_errors,
        )
        if result.status == "validated":
            self.rule_packs.install(
                _support.STANDARD_2014_CONTENT_PACK_ID,
                _support.STANDARD_2014_CONTENT_PACK_VERSION,
            )

    def ensure_core2024_content_pack(self) -> None:
        """Install the source-linked SRD 5.2.1 catalog independently of 2014."""

        if not self.config.dnd_skills_dir.exists():
            return
        try:
            existing = self.rule_packs.get_version(
                _support.CORE_2024_CONTENT_PACK_ID,
                _support.CORE_2024_CONTENT_PACK_VERSION,
            )
            if existing.status == "installed":
                return
        except LookupError:
            pass
        manifest, artifacts = _support.build_srd2024_content(self.config.dnd_skills_dir)
        if not artifacts:
            return
        manifest, native_errors = self.bind_native_mechanic_contract(
            manifest,
            artifacts,
            [],
        )
        result = self.rule_packs.save_draft(
            manifest=manifest,
            artifacts=artifacts,
            provenance={"source": "bundled-srd2024", "structured": True},
            additional_errors=native_errors,
        )
        if result.status == "validated":
            self.rule_packs.install(
                _support.CORE_2024_CONTENT_PACK_ID,
                _support.CORE_2024_CONTENT_PACK_VERSION,
            )

    def ensure_actor_content_pack(
        self,
        pack_id: str,
        version: str,
        builder: Callable[[_support.Path], list[dict[str, Any]]],
        source: str,
        *,
        title: str,
        edition: str,
    ) -> None:
        """Install bundled actor-card.v3 values as one preset package."""

        if not self.config.dnd_skills_dir.exists():
            return
        try:
            existing = self.rule_packs.get_version(pack_id, version)
            if existing.status == "installed":
                return
        except LookupError:
            pass
        actors = builder(self.config.dnd_skills_dir)
        if not actors:
            return
        package, _blobs = _support.build_preset_content_package(
            package_id=pack_id,
            version=version,
            system_id=_support.DND5E.id,
            title=title,
            cards=actors,
            metadata={
                "title": title,
                "edition": edition,
                "distribution": "shareable",
                "license": "CC-BY-4.0",
                "attribution": (
                    "Includes material from the Dungeons & Dragons System Reference "
                    "Document by Wizards of the Coast LLC, licensed under CC-BY-4.0."
                ),
                "content_kinds": ["npc", "monster"],
            },
        )
        manifest, artifacts = _support.content_actor_catalog_definition(package)
        manifest["id"] = pack_id
        manifest["namespace"] = pack_id
        manifest["editions"] = [edition]
        result = self.rule_packs.save_draft(
            manifest=manifest,
            artifacts=artifacts,
            provenance={
                "source": source,
                "structured": True,
                "content_package_checksum": package["checksum"],
                "license": package["metadata"].get("license"),
                "attribution": package["metadata"].get("attribution"),
            },
        )
        if result.status == "validated":
            self.rule_packs.install(pack_id, version)

    def rule_content_descriptor(
        self,
        pack_id: str,
        version: str,
        *,
        metadata: dict[str, Any] | None = None,
        _dependency_stack: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, Any]:
        """Export one rule definition with stable source locators for a v2 Pack."""

        identity = (pack_id, version)
        if identity in _dependency_stack:
            chain = " -> ".join(
                f"{item_id}@{item_version}"
                for item_id, item_version in (*_dependency_stack, identity)
            )
            raise ValueError(f"rule-pack dependency cycle is not portable: {chain}")
        dependency_stack = (*_dependency_stack, identity)
        pack = self.rule_packs.get_version(pack_id, version)
        provenance = self.rule_packs.provenance(pack_id, version)
        definition: dict[str, Any] = {
            "artifacts": [_support.deepcopy(item) for item in pack.artifacts],
            "mechanics": [_support.deepcopy(item) for item in pack.mechanics],
            "provenance": _support.deepcopy(provenance),
        }
        definition["artifacts"] = _support._strip_artifact_authoring_state(definition["artifacts"])
        definition["provenance"].pop("import_job_id", None)
        source_ids: set[str] = set()
        chunk_ids: set[str] = set()
        uuid_pattern = _support.re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        rule_source_chunk_pattern = _support.re.compile(
            rf"rule-source-chunk:({uuid_pattern.pattern})"
        )

        def collect(item: Any, *, field: str = "") -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    if key == "source_id" and isinstance(child, str):
                        source_ids.add(child)
                    elif key == "chunk_id" and isinstance(child, str):
                        chunk_ids.add(child)
                    collect(child, field=key)
                return
            if isinstance(item, list):
                for child in item:
                    collect(child, field=field)
                return
            if isinstance(item, str):
                if field in {"rule_refs", "source_chunk_ids"}:
                    chunk_ids.update(uuid_pattern.findall(item))
                chunk_ids.update(rule_source_chunk_pattern.findall(item))

        collect(definition)
        for chunk_id in sorted(chunk_ids):
            try:
                source_ids.add(str(self.rules.expand(chunk_id)["source"]["id"]))
            except (LookupError, _support.NoResultFound) as error:
                raise ValueError(
                    f"rule pack references unavailable source chunk {chunk_id}"
                ) from error

        source_exports: list[tuple[str, dict[str, Any]]] = []
        source_key_by_id: dict[str, str] = {}
        chunk_key_by_id: dict[str, str] = {}
        for source_id in source_ids:
            try:
                source = self.rules.export_indexed_source(source_id)
            except LookupError as error:
                raise ValueError(
                    f"rule pack references unavailable rule source {source_id}"
                ) from error
            source_exports.append((source_id, source))
        portable_sources: list[dict[str, Any]] = []
        for source_id, source in sorted(source_exports, key=lambda item: item[1]["source_key"]):
            portable_sources.append(source)
            source_key_by_id[source_id] = source["source_key"]
            portable_chunks = {
                (section["ordinal"], chunk["ordinal"]): chunk
                for section in source["sections"]
                for chunk in section["chunks"]
            }
            for local_chunk in self.rules.source_chunks(source_id):
                portable_chunk = portable_chunks.get(
                    (
                        local_chunk["section_ordinal"],
                        local_chunk["ordinal"],
                    )
                )
                if portable_chunk is None or portable_chunk["content"] != local_chunk["content"]:
                    raise ValueError("rule source changed while building its portable package")
                chunk_key_by_id[local_chunk["id"]] = portable_chunk["key"]

        def make_portable(item: Any) -> Any:
            if isinstance(item, list):
                return [make_portable(child) for child in item]
            if isinstance(item, str):
                if item in chunk_key_by_id:
                    return chunk_key_by_id[item]
                result = item
                for local_id, stable_key in chunk_key_by_id.items():
                    result = result.replace(f"#chunk:{local_id}", f"#chunk:{stable_key}")
                    result = result.replace(
                        f"rule-source-chunk:{local_id}",
                        f"rule-source-chunk:{stable_key}",
                    )
                return result
            if not isinstance(item, dict):
                return _support.deepcopy(item)
            result = {
                key: make_portable(child)
                for key, child in item.items()
                if key not in {"source_id", "chunk_id"}
            }
            source_id = item.get("source_id")
            if source_id is not None:
                source_key = source_key_by_id.get(str(source_id))
                if source_key is None:
                    raise ValueError(f"rule pack source_id is not portable: {source_id}")
                if result.get("source_key") not in {None, source_key}:
                    raise ValueError("rule citation source_id/source_key mismatch")
                result["source_key"] = source_key
            chunk_id = item.get("chunk_id")
            if chunk_id is not None:
                chunk_key = chunk_key_by_id.get(str(chunk_id))
                if chunk_key is None:
                    raise ValueError(f"rule pack chunk_id is not portable: {chunk_id}")
                result["chunk_key"] = chunk_key
            return result

        portable_definition = self.refresh_portable_resolution_plans(
            self.canonicalize_portable_evidence(make_portable(definition))
        )
        portable_definition["artifacts"] = _support._strip_artifact_authoring_state(
            list(portable_definition["artifacts"])
        )
        semantic_validation = _support.audit_release_semantic_validation(
            list(portable_definition["artifacts"]),
            settled_mechanic_ids=_support._rule_payload_settled_mechanic_ids(
                {
                    "manifest": pack.manifest,
                    "mechanics": portable_definition["mechanics"],
                }
            ),
        )
        portable_manifest = _support.deepcopy(pack.manifest)
        portable_manifest["resolution_policy"] = "compiled_or_agent"
        portable_manifest["semantic_validation"] = _support.deepcopy(semantic_validation)
        package_dependencies: list[dict[str, Any]] = []
        pinned_dependencies: list[dict[str, Any]] = []
        for dependency in portable_manifest.get("dependencies", []):
            if not isinstance(dependency, dict) or not dependency.get("version"):
                raise ValueError("rule Pack dependencies must pin a version")
            dependency_id = str(dependency.get("id") or "")
            dependency_version = str(dependency.get("version") or "")
            dependency_pack = self.rule_packs.get_version(dependency_id, dependency_version)
            dependency_provenance = self.rule_packs.provenance(dependency_id, dependency_version)
            content_dependency = dict(dependency_provenance.get("content_definition") or {})
            stored_definition_checksum = str(content_dependency.get("definition_checksum") or "")
            dependency_descriptor = self.rule_content_descriptor(
                dependency_id,
                dependency_version,
                _dependency_stack=dependency_stack,
            )
            dependency_definition_checksum = _support.content_definition_checksum(
                manifest=dependency_descriptor["manifest"],
                artifacts=dependency_descriptor["artifacts"],
                mechanics=dependency_descriptor["mechanics"],
            )
            if stored_definition_checksum:
                dependency_definition_checksum = stored_definition_checksum
            expected_checksum = str(dependency.get("checksum") or "")
            accepted_existing_checksums = {
                dependency_pack.checksum,
                dependency_definition_checksum,
                str(content_dependency.get("definition_checksum") or ""),
            }
            if expected_checksum and expected_checksum not in accepted_existing_checksums:
                raise ValueError(
                    f"rule dependency checksum mismatch for {dependency_id}@{dependency_version}"
                )
            pinned = {
                "id": dependency_id,
                "version": dependency_version,
                "checksum": dependency_definition_checksum,
            }
            pinned_dependencies.append(pinned)
            package_dependencies.append(
                {
                    "kind": "core_rules",
                    "id": dependency_id,
                    "version": dependency_version,
                    "checksum": dependency_definition_checksum,
                    "optional": False,
                }
            )
        portable_manifest["dependencies"] = pinned_dependencies
        requested_metadata = _support.deepcopy(metadata or {})
        declared_license = str(
            requested_metadata.get("license")
            or provenance.get("license")
            or portable_manifest.get("license")
            or ""
        ).strip()
        package_metadata = {
            "title": str(portable_manifest.get("title") or pack_id),
            "license": declared_license or "user-supplied",
            "attribution": str(
                provenance.get("attribution") or portable_manifest.get("attribution") or ""
            ),
            "distribution": "private",
            **requested_metadata,
        }
        distribution = str(package_metadata.get("distribution") or "")
        if distribution not in {"private", "shareable"}:
            raise ValueError(
                "portable rule pack metadata.distribution must be private or shareable"
            )
        if distribution == "shareable" and (
            not declared_license
            or not str(package_metadata.get("license") or "").strip()
            or not str(package_metadata.get("attribution") or "").strip()
        ):
            raise ValueError("shareable rule packs require explicit license and attribution")
        return {
            "id": pack_id,
            "version": version,
            "system_id": _support.DND5E.id,
            "manifest": portable_manifest,
            "artifacts": portable_definition["artifacts"],
            "mechanics": portable_definition["mechanics"],
            "provenance": portable_definition["provenance"],
            "sources": portable_sources,
            "metadata": package_metadata,
            "dependencies": package_dependencies,
        }

    def store_content_rules_package(
        self,
        package: dict[str, Any],
        blobs: dict[str, bytes],
        *,
        import_campaign_id: str | None,
    ) -> dict[str, Any]:
        """Store one verified rules archive without activating it for a campaign."""

        value = _support.validate_dnd_content_package(package)
        if value["system_id"] != _support.DND5E.id or value["kind"] not in {
            "addon",
            "core_rules",
            "preset",
        }:
            raise ValueError("content package must be a dnd5e addon, core_rules, or preset")
        _support._validate_reserved_official_package_identity(value)
        managed_archive = self.storage.write_content_archive(value, blobs)
        dependency_rebinds = _support.official_expansion_dependency_rebinds()
        component_equivalence: list[dict[str, str]] = []

        assets = {str(item["asset_key"]): item for item in value["assets"]}
        source_map: dict[str, str] = {}
        chunk_map: dict[str, str] = {}
        source_results = []
        embedder, _vectors = self.storage.dense_components()
        for source in value["sources"]:
            asset = assets[str(source["normalized_document_asset_key"])]
            imported = self.rules.import_content_source(
                source,
                blobs[str(asset["checksum"])],
                system_id=_support.DND5E.id,
                embedder=embedder,
            )
            source_map[str(source["source_key"])] = str(imported["source_id"])
            chunk_map.update({str(key): str(item) for key, item in imported["chunk_map"].items()})
            source_results.append(
                {
                    key: _support.deepcopy(item)
                    for key, item in imported.items()
                    if key != "chunk_map"
                }
            )

        def localize(item: Any) -> Any:
            if isinstance(item, list):
                return [localize(child) for child in item]
            if isinstance(item, str):
                if item in chunk_map:
                    return chunk_map[item]
                result = item
                for stable_key, local_id in chunk_map.items():
                    result = result.replace(f"#chunk:{stable_key}", f"#chunk:{local_id}")
                return result
            if not isinstance(item, dict):
                return _support.deepcopy(item)
            result = {
                key: localize(child)
                for key, child in item.items()
                if key not in {"source_key", "chunk_key", "rule_definition_id"}
            }
            source_key = item.get("source_key")
            if source_key is not None:
                result["source_key"] = str(source_key)
                if str(source_key) in source_map:
                    result["source_id"] = source_map[str(source_key)]
            chunk_key = item.get("chunk_key")
            if chunk_key is not None:
                if str(chunk_key) not in chunk_map:
                    raise ValueError(f"content citation references unknown chunk {chunk_key}")
                result["chunk_id"] = chunk_map[str(chunk_key)]
            return result

        definitions = list(value["content"].get("rule_definitions") or [])
        pending = {str(item["id"]): dict(item) for item in definitions}
        component_results = []
        while pending:
            progressed = False
            for definition_id, definition in list(pending.items()):
                manifest = _support.deepcopy(dict(definition["manifest"]))
                applied_rebinds = []
                for rebind in _support.matching_official_expansion_dependency_rebinds(
                    dependency_rebinds,
                    package_id=str(value["id"]),
                    definition_id=definition_id,
                ):
                    matches = [
                        item
                        for item in manifest.get("dependencies") or []
                        if isinstance(item, dict)
                        and str(item.get("id") or "") == rebind["dependency_id"]
                        and str(item.get("version") or "") == rebind["dependency_version"]
                        and str(item.get("checksum") or "") == rebind["source_checksum"]
                    ]
                    if len(matches) != 1:
                        raise ValueError(
                            f"official dependency rebind no longer matches {definition_id}"
                        )
                    runtime_checksum = str(rebind["runtime_checksum"])
                    runtime_version = str(rebind["runtime_version"])
                    matches[0]["version"] = runtime_version
                    try:
                        runtime_dependency = self.rule_packs.get_version(
                            str(rebind["dependency_id"]),
                            runtime_version,
                        )
                    except LookupError:
                        runtime_dependency = None
                    if runtime_dependency is not None:
                        dependency_provenance = self.rule_packs.provenance(
                            runtime_dependency.pack_id,
                            runtime_dependency.version,
                        )
                        definition_provenance = dict(
                            dependency_provenance.get("content_definition") or {}
                        )
                        if definition_provenance.get(
                            "source_definition_checksum"
                        ) == runtime_checksum and definition_provenance.get("definition_checksum"):
                            runtime_checksum = str(definition_provenance["definition_checksum"])
                    matches[0]["checksum"] = runtime_checksum
                    applied_rebinds.append(
                        {**_support.deepcopy(rebind), "resolved_runtime_checksum": runtime_checksum}
                    )
                dependencies_ready = True
                for dependency in manifest.get("dependencies") or []:
                    try:
                        dependency_row = self.rule_packs.get_version(
                            str(dependency["id"]), str(dependency["version"])
                        )
                    except LookupError:
                        dependencies_ready = False
                        break
                    if dependency_row.status != "installed":
                        dependencies_ready = False
                        break
                if not dependencies_ready:
                    continue
                portable_artifacts = [
                    {
                        key: _support.deepcopy(child)
                        for key, child in item.items()
                        if key != "rule_definition_id"
                    }
                    for item in value["content"].get("artifacts") or []
                    if str(item.get("rule_definition_id") or "") == definition_id
                ]
                portable_mechanics = [
                    {
                        key: _support.deepcopy(child)
                        for key, child in item.items()
                        if key != "rule_definition_id"
                    }
                    for item in value["content"].get("mechanics") or []
                    if str(item.get("rule_definition_id") or "") == definition_id
                ]
                runtime_definition_checksum = (
                    _support.content_definition_checksum(
                        manifest=manifest,
                        artifacts=portable_artifacts,
                        mechanics=portable_mechanics,
                    )
                    if applied_rebinds
                    else str(definition["definition_checksum"])
                )
                try:
                    existing = self.rule_packs.get_version(
                        definition_id, str(definition["version"])
                    )
                except LookupError:
                    existing = None
                if existing is not None and existing.status == "installed":
                    provenance = self.rule_packs.provenance(
                        definition_id, str(definition["version"])
                    )
                    recorded = str(
                        dict(provenance.get("content_definition") or {}).get("definition_checksum")
                        or ""
                    )
                    recorded_source = str(
                        dict(provenance.get("content_definition") or {}).get(
                            "source_definition_checksum"
                        )
                        or ""
                    )
                    if not recorded:
                        recorded = _support.content_definition_checksum(
                            manifest=existing.manifest,
                            artifacts=existing.artifacts,
                            mechanics=existing.mechanics,
                        )
                    if not _support.installed_official_definition_matches(
                        source_checksum=str(definition["definition_checksum"]),
                        runtime_checksum=runtime_definition_checksum,
                        recorded_checksum=recorded,
                        recorded_source_checksum=recorded_source,
                    ):
                        raise ValueError(f"content rule definition conflict: {definition_id}")
                else:
                    artifacts = [localize(item) for item in portable_artifacts]
                    mechanics = [localize(item) for item in portable_mechanics]
                    localized_artifacts = _support._strip_artifact_authoring_state(
                        self.refresh_portable_resolution_plans(artifacts)
                    )
                    localized_mechanics = _support._strip_artifact_authoring_state(
                        self.refresh_portable_resolution_plans(mechanics)
                    )
                    draft = self.save_rule_pack_draft(
                        manifest=manifest,
                        artifacts=localized_artifacts,
                        mechanics=localized_mechanics,
                        provenance={
                            "source": f"content-package:{value['id']}",
                            "structured": True,
                            "content_definition": {
                                "package_id": value["id"],
                                "package_version": value["version"],
                                "package_checksum": value["checksum"],
                                "definition_checksum": runtime_definition_checksum,
                                "source_definition_checksum": definition["definition_checksum"],
                            },
                            "official_dependency_rebinds": applied_rebinds,
                            "content_package_kind": value["kind"],
                            "content_package_id": value["id"],
                            "content_package_version": value["version"],
                            "content_package_checksum": value["checksum"],
                            "content_archive_artifact": managed_archive["artifact"],
                            "license": value["metadata"].get("license"),
                            "attribution": value["metadata"].get("attribution"),
                        },
                    )
                    if draft["status"] != "validated":
                        raise ValueError(f"content rule definition was rejected: {definition_id}")
                    self.rule_packs.install(definition_id, str(definition["version"]))
                if applied_rebinds:
                    component_equivalence.append(
                        {
                            "kind": "rule_pack",
                            "component_id": definition_id,
                            "component_version": str(definition["version"]),
                            "checksum": str(definition["definition_checksum"]),
                            "basis": "built-in official dependency rebind",
                            "proof_checksum": runtime_definition_checksum,
                        }
                    )
                component_results.append(
                    {
                        "kind": "rule_pack",
                        "id": definition_id,
                        "version": definition["version"],
                        "checksum": definition["definition_checksum"],
                        "status": "stored",
                    }
                )
                del pending[definition_id]
                progressed = True
            if not progressed:
                raise ValueError(
                    "content rule dependencies are unavailable or cyclic: "
                    + ", ".join(sorted(pending))
                )

        actor_result = None
        if value["actors"]:
            actor_manifest, actor_artifacts = _support.content_actor_catalog_definition(value)
            for artifact in actor_artifacts:
                card = dict(artifact.get("card") or {})
                content_actor = card.get("content_actor")
                if isinstance(content_actor, dict):
                    card["content_actor"] = self.runtime_actor_with_portrait(
                        content_actor,
                        value,
                        blobs,
                    )
                    artifact["card"] = card
            try:
                actor_pack = self.rule_packs.get_version(
                    actor_manifest["id"], actor_manifest["version"]
                )
            except LookupError:
                actor_pack = self.rule_packs.save_draft(
                    manifest=actor_manifest,
                    artifacts=actor_artifacts,
                    provenance={
                        "source": f"content-package:{value['id']}",
                        "structured": True,
                        "content_package_checksum": value["checksum"],
                        "content_package_kind": value["kind"],
                        "content_package_id": value["id"],
                        "content_package_version": value["version"],
                        "content_package_editions": list(
                            value["manifest"].get("editions")
                            or (
                                [value["metadata"]["edition"]]
                                if value["metadata"].get("edition")
                                else []
                            )
                        ),
                        "content_archive_artifact": managed_archive["artifact"],
                        "license": value["metadata"].get("license"),
                        "attribution": value["metadata"].get("attribution"),
                    },
                )
            if actor_pack.status == "validated":
                actor_pack = self.rule_packs.install(
                    actor_manifest["id"], actor_manifest["version"]
                )
            if actor_pack.status != "installed":
                raise ValueError(
                    "content actor catalog is not installable: "
                    + "; ".join(
                        str(item) for item in dict(actor_pack.validation_report).get("errors", [])
                    )
                )
            actor_result = {
                "id": actor_manifest["id"],
                "version": actor_manifest["version"],
                "actors": len(actor_artifacts),
                "status": "stored",
            }

        addon_result = None
        if value["kind"] == "addon":
            imported_addon = self.addons.import_package(
                value,
                provenance={
                    "import_campaign_id": import_campaign_id,
                    "content_archive_artifact": managed_archive["artifact"],
                },
            )
            for verification in component_equivalence:
                self.addons.record_component_equivalence(
                    imported_addon.addon_id,
                    imported_addon.version,
                    **verification,
                )
            installed_addon = _support.asdict(
                self.addons.install(imported_addon.addon_id, imported_addon.version)
            )
            addon_result = {**installed_addon, "status": "stored"}
        response = {
            "package": {
                "kind": value["kind"],
                "id": value["id"],
                "version": value["version"],
                "checksum": value["checksum"],
            },
            "sources": source_results,
            "components": component_results,
            "actor_catalog": actor_result,
            "addon": addon_result,
            "artifact": managed_archive,
            "stored": True,
            "activated": False,
        }
        return response

    def verified_reserved_official_rule_definition(
        self,
        pack_id: str,
        version: str,
    ) -> dict[str, Any] | None:
        """Prove an installed reserved definition came from its locked managed archive."""

        owner = _support._reserved_official_definition_owners().get(str(pack_id))
        if owner is None:
            return None
        try:
            installed = self.rule_packs.get_version(pack_id, version)
            if installed.status != "installed":
                raise ValueError("reserved official rule definition is not installed")
            provenance = self.rule_packs.provenance(pack_id, version)
            content_definition = dict(provenance.get("content_definition") or {})
            archive_artifact = str(provenance.get("content_archive_artifact") or "")
            if not archive_artifact:
                raise ValueError("reserved official rule definition has no managed archive")
            archive, _blobs = self.storage.read_content_archive(artifact=archive_artifact)
            archive = _support.validate_dnd_content_package(archive)
            _support._validate_reserved_official_package_identity(archive)
            if str(archive["id"]) != owner:
                raise ValueError("reserved official rule definition archive owner mismatch")
            definition = next(
                (
                    dict(item)
                    for item in archive["content"].get("rule_definitions") or []
                    if str(item.get("id") or "") == pack_id
                    and str(item.get("version") or "") == version
                ),
                None,
            )
            if definition is None:
                raise ValueError("reserved official rule definition is absent from its archive")
            # The stored Pack is a localized runtime representation: source/chunk
            # IDs are local, authoring attestations are stripped, and the save
            # boundary adds runtime resolution metadata.  Rebuild that expected
            # representation from the locked archive before comparing it.  A
            # checksum over ``installed`` alone would reject every valid import;
            # trusting the recorded provenance alone would permit tampering.
            expected_manifest = _support.deepcopy(dict(definition["manifest"]))
            expected_artifacts = [
                _support.deepcopy(item)
                for item in archive["content"].get("artifacts") or []
                if str(item.get("rule_definition_id") or "") == pack_id
            ]
            expected_mechanics = [
                _support.deepcopy(item)
                for item in archive["content"].get("mechanics") or []
                if str(item.get("rule_definition_id") or "") == pack_id
            ]
            for rebind in _support.matching_official_expansion_dependency_rebinds(
                _support.official_expansion_dependency_rebinds(),
                package_id=str(archive["id"]),
                definition_id=pack_id,
            ):
                matches = [
                    item
                    for item in expected_manifest.get("dependencies") or []
                    if isinstance(item, dict)
                    and str(item.get("id") or "") == rebind["dependency_id"]
                    and str(item.get("version") or "") == rebind["dependency_version"]
                    and str(item.get("checksum") or "") == rebind["source_checksum"]
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "official dependency rebind no longer matches reserved definition"
                    )
                matches[0]["version"] = str(rebind["runtime_version"])
                matches[0]["checksum"] = str(rebind["runtime_checksum"])
                try:
                    runtime_dependency = self.rule_packs.get_version(
                        str(rebind["dependency_id"]), str(rebind["runtime_version"])
                    )
                except LookupError:
                    runtime_dependency = None
                if runtime_dependency is not None:
                    dependency_provenance = dict(
                        self.rule_packs.provenance(
                            runtime_dependency.pack_id,
                            runtime_dependency.version,
                        ).get("content_definition")
                        or {}
                    )
                    if dependency_provenance.get("source_definition_checksum") == rebind[
                        "runtime_checksum"
                    ] and dependency_provenance.get("definition_checksum"):
                        matches[0]["checksum"] = str(dependency_provenance["definition_checksum"])
            # Import provenance records this pre-save portable representation:
            # rebinds have been applied, but export dependency normalization,
            # authoring stripping, and runtime metadata have not yet happened.
            recorded_expected_checksum = _support.content_definition_checksum(
                manifest=expected_manifest,
                artifacts=expected_artifacts,
                mechanics=expected_mechanics,
            )
            persisted_expected_dependencies = _support.deepcopy(
                expected_manifest.get("dependencies") or []
            )
            # The portable export pins each installed dependency to its runtime
            # definition checksum (the same canonical value used by
            # rule_content_descriptor), rather than the archive rebind's source
            # proof checksum.
            for dependency in expected_manifest.get("dependencies") or []:
                if not isinstance(dependency, dict):
                    continue
                try:
                    dependency_pack = self.rule_packs.get_version(
                        str(dependency["id"]), str(dependency["version"])
                    )
                except (KeyError, LookupError):
                    continue
                dependency_content = dict(
                    self.rule_packs.provenance(
                        dependency_pack.pack_id,
                        dependency_pack.version,
                    ).get("content_definition")
                    or {}
                )
                if dependency_content.get("definition_checksum"):
                    dependency["checksum"] = str(dependency_content["definition_checksum"])
                else:
                    dependency_descriptor = self.rule_content_descriptor(
                        dependency_pack.pack_id,
                        dependency_pack.version,
                    )
                    dependency["checksum"] = _support.content_definition_checksum(
                        manifest=dependency_descriptor["manifest"],
                        artifacts=dependency_descriptor["artifacts"],
                        mechanics=dependency_descriptor["mechanics"],
                    )
            # Export sorts order-insensitive evidence, including multi-citation
            # variant backgrounds. Rebuild the identical canonical ordering
            # without dropping or trusting any installed source evidence.
            expected_artifacts = self.canonicalize_portable_evidence(expected_artifacts)
            expected_mechanics = self.canonicalize_portable_evidence(expected_mechanics)
            expected_artifacts = _support._strip_artifact_authoring_state(expected_artifacts)
            expected_artifacts = self.refresh_portable_resolution_plans(expected_artifacts)
            expected_mechanics = self.refresh_portable_resolution_plans(expected_mechanics)
            expected_manifest, native_errors = self.bind_native_mechanic_contract(
                expected_manifest,
                expected_artifacts,
                expected_mechanics,
            )
            if native_errors:
                raise ValueError("reserved definition failed native contract binding")
            expected_manifest["resolution_policy"] = "compiled_or_agent"
            expected_manifest["semantic_validation"] = _support.audit_release_semantic_validation(
                expected_artifacts,
                settled_mechanic_ids=_support._rule_payload_settled_mechanic_ids(
                    {"manifest": expected_manifest, "mechanics": expected_mechanics}
                ),
            )
            installed_descriptor = self.rule_content_descriptor(pack_id, version)
            expected_checksum = _support.content_definition_checksum(
                manifest=expected_manifest,
                artifacts=expected_artifacts,
                mechanics=expected_mechanics,
            )
            installed_checksum = _support.content_definition_checksum(
                manifest=installed_descriptor["manifest"],
                artifacts=installed_descriptor["artifacts"],
                mechanics=installed_descriptor["mechanics"],
            )
            local_expected_semantic_validation = _support.audit_release_semantic_validation(
                list(installed.artifacts),
                settled_mechanic_ids=_support._rule_payload_settled_mechanic_ids(
                    {"manifest": installed.manifest, "mechanics": installed.mechanics}
                ),
            )
            recorded_runtime_checksum = str(content_definition.get("definition_checksum") or "")
            recorded_source_checksum = str(
                content_definition.get("source_definition_checksum") or ""
            )
            valid = (
                str(content_definition.get("package_id") or "") == str(archive["id"])
                and str(content_definition.get("package_version") or "") == str(archive["version"])
                and str(content_definition.get("package_checksum") or "")
                == str(archive["checksum"])
                and recorded_runtime_checksum == recorded_expected_checksum
                and installed_checksum == expected_checksum
                and recorded_source_checksum == str(definition["definition_checksum"])
                # Compare persisted, security-relevant manifest fields before
                # descriptor export, which normalizes resolution metadata and
                # could otherwise hide tampering.
                and all(
                    installed.manifest.get(key) == expected_manifest.get(key)
                    for key in ("resolution_policy",)
                )
                and installed.manifest.get("dependencies") == persisted_expected_dependencies
                and installed.manifest.get("semantic_validation")
                == local_expected_semantic_validation
            )
            if not valid:
                raise ValueError(
                    "reserved official rule definition does not match its managed archive "
                    f"(recorded={recorded_runtime_checksum}, "
                    f"expected_recorded={recorded_expected_checksum}, "
                    f"installed={installed_checksum}, expected={expected_checksum})"
                )
            return {
                "package": archive,
                "definition": definition,
                "provenance": provenance,
                "runtime_definition_checksum": installed_checksum,
                "runtime_artifacts": _support.deepcopy(installed.artifacts),
            }
        except _support.RulesetUnavailableError:
            raise
        except Exception as error:
            raise _support.RulesetUnavailableError(
                f"{pack_id}@{version} requires its immutable official content archive"
            ) from error

    def import_content_rules_package(
        self,
        campaign_id: str,
        package: dict[str, Any],
        blobs: dict[str, bytes],
        *,
        principal_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Install one unified addon/core-rules/preset archive through campaign authority."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_facade_phase(campaign_id, "content_pack(import)", _support.PROFILE_LOBBY)
        value = _support.validate_dnd_content_package(package)
        request = {"operation": "import_content", "package_checksum": value["checksum"]}
        scope = f"content-package-import:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request)
        if replay is not None:
            return replay
        response = self.store_content_rules_package(
            value,
            blobs,
            import_campaign_id=campaign_id,
        )
        return self.remember_idempotent(
            scope, idempotency_key, request, response, campaign_id=campaign_id
        )

    def ensure_official_expansion_content_packs(self) -> dict[str, Any]:
        """Mount locked official 2014 expansions from an authorized local library."""

        library = self.config.official_content_library
        catalog = _support.official_expansion_catalog()
        support_catalog = _support.official_expansion_support_catalog()
        if library is None:
            return {
                "configured": False,
                "installed": 0,
                "available": len(catalog),
                "support_installed": 0,
                "support_available": len(support_catalog),
                "packages": [],
            }
        support_archives = _support.resolve_official_expansion_support_archives(library)
        archives = (*support_archives, *_support.resolve_official_expansion_archives(library))
        results = []
        for archive in archives:
            try:
                existing = self.addons.get_version(archive.id, archive.version)
            except LookupError:
                existing = None
            if existing is not None:
                if existing.checksum != archive.checksum:
                    raise ValueError(
                        f"official expansion version conflicts with built-in registry: "
                        f"{archive.id}@{archive.version}"
                    )
                if existing.status != "installed":
                    raise ValueError(
                        f"official expansion is only partially installed: "
                        f"{archive.id}@{archive.version}"
                    )
                results.append(
                    {
                        "id": archive.id,
                        "version": archive.version,
                        "checksum": archive.checksum,
                        "status": "stored",
                        "reused": True,
                        "role": archive.role,
                    }
                )
                continue
            if _support.file_sha256(archive.path) != archive.archive_sha256:
                raise ValueError(
                    f"official expansion archive changed after verification: {archive.id}"
                )
            package, blobs = self.storage.read_official_content_archive(archive.path)
            if (
                package.get("id") != archive.id
                or package.get("version") != archive.version
                or package.get("checksum") != archive.checksum
            ):
                raise ValueError(
                    f"official expansion archive identity changed after verification: {archive.id}"
                )
            with self.storage.database.transaction():
                stored = self.store_content_rules_package(
                    package,
                    blobs,
                    import_campaign_id=None,
                )
            results.append(
                {
                    **dict(stored["package"]),
                    "status": "stored",
                    "reused": False,
                    "role": archive.role,
                }
            )
        return {
            "configured": True,
            "installed": sum(item.get("role") != "official_core_dependency" for item in results),
            "available": len(catalog),
            "support_installed": sum(
                item.get("role") == "official_core_dependency" for item in results
            ),
            "support_available": len(support_catalog),
            "packages": results,
        }

    def checked_rule_facts(self, value: dict[str, Any] | None) -> dict[str, Any]:
        facts = dict(value or {})
        # Save classification is derived by the native source executor. In
        # particular, a caller must not suppress species traits by labelling
        # an effect save as concentration or declaring an empty condition set.
        reserved = {
            "actor_id",
            "kind",
            "ability",
            "dc",
            "save_against_poison",
            "save_source_kind",
            "save_effect_conditions",
            "save_purpose",
        } & facts.keys()
        if reserved:
            raise ValueError("rule_facts cannot override: " + ", ".join(sorted(reserved)))
        if len(facts) > 32 or len(repr(facts)) > 8192:
            raise ValueError("rule_facts exceed the safe settlement limit")
        if any(not isinstance(key, str) or not key.strip() for key in facts):
            raise ValueError("rule_facts keys must be non-empty strings")
        return facts

    def bundled_rule_seed_status(self) -> dict[str, Any]:
        root = self.config.dnd_skills_dir / "full" / "skills" / "dnd-dm" / "srd"
        if not root.is_dir():
            return {
                "status": "unavailable",
                "complete": False,
                "expected_sources": 0,
                "indexed_sources": 0,
                "missing_source_keys": [],
                "stale_source_keys": [],
                "corpus": None,
                "required_path": str(root),
            }
        catalog = _support.build_bundled_rule_sources(root)
        inventory = _support.bundled_rule_corpus_inventory(root, catalog)
        existing = {
            str(item["source_key"]): item
            for item in self.rules.sources(system_id=_support.DND5E.id)
        }
        expected = {source.source_key: source for source in catalog}
        missing = sorted(set(expected) - set(existing))
        stale = sorted(
            source_key
            for source_key, source in expected.items()
            if source_key in existing
            and str(existing[source_key].get("checksum") or "") != source.checksum
        )
        return {
            "status": "ready" if not missing and not stale else "incomplete",
            "complete": not missing and not stale,
            "expected_sources": len(expected),
            "indexed_sources": len(expected) - len(missing) - len(stale),
            "missing_source_keys": missing,
            "stale_source_keys": stale,
            "corpus": inventory,
        }

    def seed_bundled_rules(self, *, max_files: int | None = None) -> dict[str, Any]:
        """Idempotently index every bundled SRD partition without silent truncation."""

        root = self.config.dnd_skills_dir / "full" / "skills" / "dnd-dm" / "srd"
        if not root.is_dir():
            return self.bundled_rule_seed_status()
        catalog = list(_support.build_bundled_rule_sources(root))
        if max_files is not None:
            if (
                isinstance(max_files, bool)
                or not isinstance(max_files, int)
                or not 1 <= max_files <= len(catalog)
            ):
                raise ValueError(f"max_files must be an integer from 1 through {len(catalog)}")
            catalog = catalog[:max_files]
        seeded = 0
        skipped = 0
        for source in catalog:
            result = self.rules.ingest(
                system_id=_support.DND5E.id,
                source_key=source.source_key,
                title=source.title,
                content=source.content,
                locale=source.locale,
                edition=source.edition,
                version=source.version,
                publication_id=source.publication_id,
                authority="core",
                metadata=source.metadata(),
            )
            skipped += int(result.skipped)
            seeded += int(not result.skipped)
        coverage = self.bundled_rule_seed_status()
        complete = coverage["complete"]
        return {
            **coverage,
            "status": "ready" if complete else "partial",
            "seeded": seeded,
            "skipped": skipped,
            "requested_sources": len(catalog),
        }

    def is_canonical_standard_rule_source(self, source: dict[str, Any]) -> bool:
        """Identify rule corpora whose printed mechanics must be engine-owned."""

        publication_id = (
            str(source.get("publication_id") or "")
            .strip()
            .casefold()
            .replace("-", "")
            .replace("_", "")
        )
        return publication_id in {
            "srd",
            "srd2014",
            "mm2014",
            "monstermanual2014",
            "phb2014",
            "playershandbook2014",
            "dmg2014",
            "dungeonmastersguide2014",
        }

    def party_public_map_asset_content(
        self,
        campaign_id: str,
        encounter: Mapping[str, Any],
    ) -> bytes | None:
        """Resolve only an explicitly reviewed Pack asset; any defect is decorative fallback."""

        try:
            battle_map = dict(encounter.get("battle_map") or {})
            bounds = dict(battle_map.get("bounds") or {})
            asset_ref = _support.normalize_party_public_map_asset(
                battle_map.get("party_public_map_asset"),
                width_cells=int(bounds.get("width_cells", 0) or 0),
                height_cells=int(bounds.get("height_cells", 0) or 0),
            )
            source = dict(battle_map.get("source") or {})
            module_id = str(source.get("module_id") or "")
            receipt = dict(battle_map.get("authority_receipt") or {})
            if not module_id or receipt.get("kind") != "content_pack_template":
                return None
            module_assets = self.modules.list_assets(campaign_id, module_id)
            archive_matches = [
                item
                for item in module_assets
                if str(dict(item.get("metadata") or {}).get("asset_kind") or "")
                == "content_package_archive"
            ]
            if len(archive_matches) != 1:
                return None
            archive_metadata = dict(archive_matches[0].get("metadata") or {})
            if str(archive_metadata.get("content_package_checksum") or "") != str(
                receipt.get("package_checksum") or ""
            ):
                return None
            matches = [
                item
                for item in module_assets
                if str(dict(item.get("metadata") or {}).get("content_asset_key") or "")
                == asset_ref["asset_key"]
            ]
            if len(matches) != 1:
                return None
            asset = dict(matches[0])
            if (
                str(asset.get("checksum") or "") != asset_ref["checksum"]
                or str(asset.get("media_type") or "").casefold() != asset_ref["media_type"]
            ):
                return None
            source_path = _support.Path(str(asset.get("source_path") or "")).resolve()
            module_asset_root = self.config.module_assets_dir.resolve()
            if not source_path.is_file() or not source_path.is_relative_to(module_asset_root):
                return None
            if _support.file_sha256(source_path) != asset_ref["checksum"]:
                return None
            return source_path.read_bytes()
        except (_support.BattleMapError, LookupError, OSError, RuntimeError, TypeError, ValueError):
            return None

    def require_import_job(self, campaign_id: str, job_id: str, kind: str | None = None) -> Any:
        job = self.import_jobs.get(job_id)
        if job.campaign_id != campaign_id:
            raise LookupError(job_id)
        if kind is not None and job.kind != kind:
            raise ValueError(f"import job is not a {kind} job")
        return job

    def import_candidate_view(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Name the resolver for a live candidate review without changing stored evidence."""

        value = _support.deepcopy(dict(candidate))
        if str(value.get("review_status") or "") not in {"pending", "needs_revision"}:
            return value
        requirement = value.get("ruling_requirement")
        if not isinstance(requirement, dict):
            requirement = _support._ruling_requirement(
                "Review the extracted candidate against its exact source evidence.",
                "source_or_scene_fact",
            )
            value["ruling_requirement"] = requirement
        return value

    def import_job_view(self, job: Any) -> dict[str, Any]:
        """Expose ordinary import review as Agent-owned and preserve source exceptions."""

        value = _support.asdict(job)
        candidates = [
            self.import_candidate_view(item)
            for item in value.get("candidates", [])
            if isinstance(item, dict)
        ]
        value["candidates"] = candidates
        if candidates:
            issues = [
                _support.deepcopy(issue)
                for candidate in candidates
                for issue in candidate.get("draft_issues") or []
                if isinstance(issue, dict)
            ]
            value["draft_workspace"] = {
                "status": (
                    "finalized" if str(value.get("state") or "") != "review_required" else "editing"
                ),
                "candidate_revision": int(value.get("revision") or 0),
                "issue_count": len(issues),
                "blocker_count": sum(
                    1 for issue in issues if str(issue.get("severity") or "") == "blocker"
                ),
                "issues": issues,
            }
        if str(value.get("state") or "") != "review_required":
            return value
        requirements = [
            _support.deepcopy(item["ruling_requirement"])
            for item in candidates
            if isinstance(item.get("ruling_requirement"), dict)
        ]
        ruling_kind = _support._pending_result_ruling_kind(
            {
                "status": "pending_ruling",
                "ruling_requirements": requirements,
            },
            fallback="source_or_scene_fact",
        )
        value["review_resolution"] = _support._ruling_resolution_for_kind(ruling_kind)
        value["review_requirements"] = requirements
        return value

    def unresolved_content_solution(
        self,
        source_card: dict[str, Any],
        *,
        source_card_id: str,
        source_card_kind: str,
        character_revision: int,
    ) -> dict[str, Any]:
        """Route standard gaps to engine work and prepared custom text to Agent ruling."""

        if str(source_card.get("pack_id") or "") in {
            _support.CORE_CONTENT_PACK_ID,
            _support.CORE_2024_CONTENT_PACK_ID,
            _support.STANDARD_2014_CONTENT_PACK_ID,
        }:
            return {
                "status": "engine_implementation_required",
                "source_card_id": source_card_id,
                "source_card_kind": source_card_kind,
                "required_action": "implement_standard_mechanic",
                "character_revision": character_revision,
            }
        return {
            "status": "content_authoring_required",
            "source_card_id": source_card_id,
            "source_card_kind": source_card_kind,
            "required_action": "compile_and_persist_source_bound_resolution",
            "first_use_compilation_required": True,
            "character_revision": character_revision,
        }

    def validate_authored_content_plan(
        self,
        campaign_id: str,
        raw_plan: Any,
        *,
        source_card: dict[str, Any],
        source_card_id: str,
        source_card_kind: str,
    ) -> Any:
        """Validate one build-time custom recipe against exact managed evidence."""

        try:
            compiled = _support.compile_resolution_plan(raw_plan)
        except _support.ResolutionPlanCompilationError as error:
            raise _support.CombatEngineError(
                f"authored resolution plan is invalid: {error}"
            ) from error
        if (
            compiled.schema_version != 2
            or compiled.source_card_id != source_card_id
            or compiled.source_card_kind != source_card_kind
        ):
            raise _support.CombatEngineError(
                "authored solutions require a schema v2 plan for the exact source card"
            )
        evidence_texts = self.source_card_evidence_texts(source_card)
        if not evidence_texts:
            raise _support.CombatEngineError(
                "authored solution requires recorded original effect text"
            )
        relevant_citation = False
        for index, citation in enumerate(compiled.citations):
            source_ref = citation["source_ref"]
            try:
                if set(source_ref) == {"chunk_id"}:
                    chunk_id = str(source_ref["chunk_id"] or "")
                    canonical_citation = self.rules.citation(chunk_id)
                    expanded = self.rules.expand(chunk_id)
                    rule_source = self.rules.source(
                        str(dict(expanded.get("source") or {}).get("id") or "")
                    )
                    if (
                        citation["source"] != canonical_citation["source"]
                        or str(rule_source.get("system_id") or "") != _support.DND5E.id
                        or str(rule_source.get("edition") or "")
                        != self.campaign_rules_edition(campaign_id)
                        or rule_source.get("active") is not True
                    ):
                        raise ValueError(
                            "resolution plan rule citation does not match the active campaign rules"
                        )
                    excerpt = _support.clean_source_evidence_text(citation["source_excerpt"])
                    chunk_content = _support._normalize_source_evidence_text(
                        dict(expanded.get("chunk") or {}).get("content")
                    )
                    if not 10 <= len(excerpt) <= 4000 or excerpt.casefold() not in chunk_content:
                        raise ValueError(
                            "resolution plan source_excerpt is not present in its cited rule chunk"
                        )
                elif _support.MANAGED_MODULE_SOURCE_FIELDS & set(source_ref):
                    _normalized, _source, expanded = self.managed_module_source_ref(
                        campaign_id,
                        source_ref,
                        require_exact=True,
                        require_active_module=True,
                    )
                    assert expanded is not None
                    excerpt = self.managed_module_source_excerpt(
                        expanded,
                        citation["source_excerpt"],
                        field=(f"resolution_plan.citations[{index}].source_excerpt"),
                        minimum_length=10,
                        maximum_length=4000,
                    )
                else:
                    raise ValueError(
                        "resolution plan citation must identify one exact module or rule chunk"
                    )
            except (LookupError, ValueError) as error:
                raise _support.CombatEngineError(str(error)) from error
            normalized_excerpt = _support._normalize_source_evidence_text(excerpt)
            if any(
                normalized_excerpt in evidence or evidence in normalized_excerpt
                for evidence in evidence_texts
            ):
                relevant_citation = True
        if not relevant_citation:
            raise _support.CombatEngineError(
                "authored resolution plan must cite the exact recorded card effect"
            )
        _support._semantic_plan_save_facts(source_card, compiled)
        return compiled

    def sheet_with_content_solution(
        self,
        sheet: dict[str, Any],
        *,
        source_card_id: str,
        source_card_kind: str,
        compiled_plan: Any,
        solution: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach one compiled recipe to exactly one durable source card."""

        updated = _support.deepcopy(sheet)
        collections = {
            "activity": ("activities",),
            "feature": ("features", "feats"),
            "monster_action": ("activities",),
            "spell": ("spells",),
            "trait": ("features",),
        }.get(source_card_kind)
        if source_card_kind == "item":
            matches = [
                item
                for item in dict(updated.get("inventory") or {}).get(
                    "items",
                    [],
                )
                if isinstance(item, dict) and str(item.get("id") or "") == source_card_id
            ]
        elif collections is not None:
            matches = [
                item
                for collection in collections
                for item in dict(updated.get("content") or {}).get(
                    collection,
                    [],
                )
                if isinstance(item, dict) and str(item.get("id") or "") == source_card_id
            ]
        else:
            raise _support.CombatEngineError("unsupported character source card kind")
        if len(matches) != 1:
            raise _support.CombatEngineError(
                "source card must resolve exactly once before solution storage"
            )
        matches[0]["resolution_plan"] = _support.resolution_plan_template(compiled_plan)
        matches[0]["resolution_solution"] = _support.deepcopy(solution)
        return _support.validate_character_sheet(updated)

    def source_participant_rules(
        self,
        campaign_id: str,
        scene_id: str | None,
        character: Any,
        config_entry: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
        """Normalize scene conditions and legacy state for entering combatants."""

        actor_id_value = str(character.id)
        condition_records: list[dict[str, Any]] = []
        sheet = _support.deepcopy(character.sheet)
        for raw_condition in config_entry.get("source_conditions") or []:
            allowed_condition_fields = {
                "condition",
                "source_ref",
                "source_excerpt",
                "duration",
            }
            unknown_condition_fields = set(raw_condition) - allowed_condition_fields
            if unknown_condition_fields:
                raise ValueError(
                    f"unsupported source condition fields: {sorted(unknown_condition_fields)}"
                )
            condition = str(raw_condition.get("condition") or "").strip().casefold()
            duration = str(raw_condition.get("duration") or "encounter").strip().casefold()
            excerpt = str(raw_condition.get("source_excerpt") or "").strip()
            source_ref = raw_condition.get("source_ref")
            if condition not in _support.STANDARD_BINARY_CONDITION_IDS:
                raise ValueError(
                    f"unsupported source-declared combat condition: {condition or '<empty>'}"
                )
            if duration != "encounter":
                raise ValueError("source-declared combat condition duration must be encounter")
            if scene_id is None:
                raise ValueError("source-declared combat conditions require an encounter scene_id")
            _, normalized_source_ref, expanded = self.managed_module_source_ref(
                campaign_id,
                source_ref,
                require_exact=True,
                expected_scene_id=scene_id,
            )
            if normalized_source_ref is None or expanded is None:
                raise AssertionError("exact source conditions always resolve to a managed chunk")
            normalized_excerpt = self.managed_module_source_excerpt(
                expanded,
                excerpt,
                field="source condition source_excerpt",
            )
            existing_conditions = _support.condition_ids(sheet.get("conditions"))
            _support.apply_condition_change(sheet, condition_id=condition, add=True)
            applied_conditions = _support.condition_ids(sheet.get("conditions"))
            added_by_encounter = (
                condition not in existing_conditions and condition in applied_conditions
            )
            if added_by_encounter:
                _support.end_concentration_for_incapacitating_conditions(sheet)
            condition_records.append(
                {
                    "actor_id": actor_id_value,
                    "condition": condition,
                    "duration": duration,
                    "source_ref": normalized_source_ref,
                    "source_excerpt": normalized_excerpt,
                    "added_by_encounter": added_by_encounter,
                    "resisted_by_immunity": condition not in applied_conditions,
                    "active": True,
                }
            )
        needs_held_drop = False
        if sheet.get("edition") == "2014":
            _support.end_concentration_for_incapacitating_conditions(sheet)
            if "unconscious" in _support.condition_ids(sheet.get("conditions")):
                _support.apply_condition_change(sheet, condition_id="prone", add=True)
                needs_held_drop = bool(_support.held_item_roots(sheet))
        return (
            [],
            condition_records,
            sheet if sheet != character.sheet or needs_held_drop else None,
        )

    def _dependent_actor_materialization(
        self,
        campaign_id: str,
        artifact: dict[str, Any],
        requirement: dict[str, Any],
        numeric_parameters: Mapping[str, int],
        *,
        template_variant: str | None,
    ) -> dict[str, Any]:
        """Rebuild one dependent card using the same two-pass path as creation."""
        source_text, source_refs = self.dependent_actor_source_text(artifact)
        preview_text, _ = _support.materialize_parameterized_statblock_source(
            source_text,
            requirement,
            numeric_parameters=numeric_parameters,
            self_ability_modifiers={},
            template_variant=template_variant,
            allow_self_modifier_placeholders=True,
        )
        edition = self.campaign_rules_edition(campaign_id)
        source_key = (
            f"rule-pack:{artifact['_pack_id']}@{artifact['_pack_version']}"
            f"#artifact:{artifact['id']}"
        )
        preview = self.parse_edition_statblock(
            preview_text,
            edition=edition,
            source_key=source_key,
            rule_refs=source_refs,
        )
        self_modifiers = dict(
            self.derive_character_sheet(preview.sheet).get("ability_modifiers") or {}
        )
        rendered_text, _ = _support.materialize_parameterized_statblock_source(
            source_text,
            requirement,
            numeric_parameters=numeric_parameters,
            self_ability_modifiers=self_modifiers,
            template_variant=template_variant,
        )
        parsed = self.parse_edition_statblock(
            rendered_text,
            edition=edition,
            source_key=source_key,
            rule_refs=source_refs,
        )
        hydrated_sheet, spell_warnings = self.hydrate_statblock_spellcasting(
            campaign_id,
            parsed,
            source_key=source_key,
            rule_refs=source_refs,
        )
        self.require_standard_statblock_engine_support(
            hydrated_sheet,
            None,
            statblock_warnings=(),
            spell_warnings=spell_warnings,
        )
        sheet = _support.apply_dependent_actor_template_variant(
            hydrated_sheet,
            requirement,
            template_variant=template_variant,
        )
        sheet = _support.validate_character_sheet(
            self.finalize_actor_sheet_rulings(sheet, campaign_id)
        )
        scaled = _support.materialize_dependent_actor_owner_scaling(
            sheet,
            numeric_parameters,
            relation_key=str(
                dict(requirement.get("owner_binding") or {}).get("relation_key") or ""
            ),
            reviewed_expression_hash=str(
                dict(requirement.get("solution") or {}).get("reviewed_expression_hash") or ""
            ),
        )
        if (
            str(dict(requirement.get("owner_binding") or {}).get("relation_key") or "")
            == _support.STEEL_DEFENDER_RELATION_KEY
        ):
            scaled = _support.bind_steel_defender_runtime_mechanics(scaled)
        return _support.validate_character_sheet(scaled)

    def rule_seed_status(
        self,
        campaign_id: str | None = None,
        edition: str | None = None,
        query: str = "",
        limit: int = 100,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Return a bounded inventory of indexed D&D rule sources."""

        if campaign_id is not None:
            self.access.require_campaign(
                campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
            )
            campaign_edition = self.campaign_rules_edition(campaign_id)
            if edition is not None and _support.normalize_dnd_edition(edition) != campaign_edition:
                raise ValueError("edition does not match the campaign rule profile")
            edition = campaign_edition
        elif edition is not None:
            edition = _support.normalize_dnd_edition(edition)
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        normalized_query = str(query or "").strip().casefold()
        sources = self.rules.sources(system_id=_support.DND5E.id, edition=edition)
        if normalized_query:
            sources = [
                item
                for item in sources
                if normalized_query
                in " ".join(
                    str(item.get(field) or "")
                    for field in (
                        "title",
                        "source_key",
                        "publication_id",
                        "authority",
                    )
                ).casefold()
            ]
        return {
            "sources": sources[:limit],
            "source_count": len(sources),
            "edition": edition,
            "query": str(query or "").strip(),
            "auto_seed": self.config.auto_seed_rules,
            "coverage": self.bundled_rule_seed_status(),
        }

    def rule_seed_bundled(self, max_files: int | None = None) -> dict[str, Any]:
        """Idempotently index the complete bundled SRD corpus."""
        return self.seed_bundled_rules(max_files=max_files)

    def import_job_get(
        self,
        campaign_id: str,
        job_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Read the durable evidence, review state, and result for one lobby import."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return self.import_job_view(self.require_import_job(campaign_id, job_id))

    def import_job_list(
        self,
        campaign_id: str,
        kind: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        """List rulebook or module imports, newest first, without reading local files."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return [
            self.import_job_view(item) for item in self.import_jobs.list(campaign_id, kind=kind)
        ]

    def rule_import_job_create(
        self,
        campaign_id: str,
        artifact: str,
        source_key: str,
        title: str,
        edition: str,
        locale: str = "en",
        publication_id: str = "",
        version: str = "",
        authority: str = "supplement",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a reviewable rulebook import job for an already staged artifact."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        edition = _support.normalize_dnd_edition(edition)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for an import job")
        payload = {
            "artifact": artifact,
            "source_key": source_key,
            "title": title,
            "edition": edition,
            "locale": locale,
            "publication_id": publication_id,
            "version": version,
            "authority": authority,
        }
        scope = f"import-job-create:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        self.storage.artifact_rulebook_path(artifact)
        job = self.import_jobs.create(
            campaign_id=campaign_id,
            kind="rulebook",
            artifact=artifact,
            artifact_checksum=self.storage.rulebook_checksum(artifact),
            payload=payload,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {"job": self.import_job_view(result)},
            ),
        )
        return {"job": self.import_job_view(job)}

    def rule_import_job_inspect(
        self,
        campaign_id: str,
        job_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Normalize a staged rulebook and persist the parser report before indexing it."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for import inspection")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        payload = {"job_id": job_id, "operation": "inspect"}
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        inspection = self.rules.inspect_path(
            self.storage.artifact_rulebook_path(job.artifact),
            **self.rule_document_options(
                job.artifact_checksum,
                self.import_page_revisions(job),
            ),
        )
        updated = self.import_jobs.record_inspection(
            job_id,
            inspection,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "inspection": inspection,
                },
            ),
        )
        return {"job": self.import_job_view(updated), "inspection": inspection}

    def rule_import_job_ingest(
        self,
        campaign_id: str,
        job_id: str,
        acknowledge_warnings: bool = False,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Index an inspected rulebook, retaining its source id for candidate citations."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for rulebook indexing")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        payload = {
            "job_id": job_id,
            "operation": "ingest",
            "acknowledge_warnings": acknowledge_warnings,
        }
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        if job.state not in {"inspected", "failed"}:
            raise ValueError("rule import job must be inspected before indexing")
        warnings = list(dict(job.inspection or {}).get("warnings") or [])
        if warnings and not acknowledge_warnings:
            raise _support.NeedsRulingError(
                "rule import inspection has warnings; the Agent acting as DM must "
                "review them before setting acknowledge_warnings=true",
                missing=("rule_import_warning_acknowledgement",),
                ruling_kind="source_or_scene_fact",
            )
        values = dict(job.payload)
        embedder, vectors = self.storage.dense_components()
        result = self.rules.ingest_path(
            system_id=_support.DND5E.id,
            path=self.storage.artifact_rulebook_path(job.artifact),
            source_key=str(values["source_key"]),
            title=str(values["title"]),
            locale=str(values.get("locale") or "en"),
            edition=str(values["edition"]),
            publication_id=str(values.get("publication_id") or ""),
            version=str(values.get("version") or ""),
            authority=str(values.get("authority") or "supplement"),
            embedder=embedder,
            vector_store=vectors,
            **self.rule_document_options(
                job.artifact_checksum,
                self.import_page_revisions(job),
            ),
        )
        source = self.rules.source(result.source_id)
        updated = self.import_jobs.record_result(
            job_id,
            {"ingest": _support.asdict(result), "source": source},
            state="extracted",
            source_id=result.source_id,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda updated_job: {
                    "job": self.import_job_view(updated_job),
                    "source": source,
                    **_support.asdict(result),
                },
            ),
        )
        return {"job": self.import_job_view(updated), "source": source, **_support.asdict(result)}

    def validate_rule_candidate_execution_evidence(
        self,
        job: Any,
        decisions: list[dict[str, Any]],
    ) -> None:
        """Prove reviewed plans and clauses quote their exact indexed chunks."""

        if job.kind != "rulebook" or not job.source_id:
            return
        candidates = {
            str(candidate.get("id") or ""): dict(candidate) for candidate in job.candidates
        }
        chunks = {
            str(chunk.get("id") or ""): dict(chunk)
            for chunk in self.rules.source_chunks(job.source_id)
        }
        for decision in decisions:
            if decision.get("review_status") != "accepted":
                continue
            candidate_id = str(decision.get("id") or "")
            candidate = candidates.get(candidate_id)
            if candidate is None:
                continue
            artifact = dict(
                decision.get("artifact")
                if "artifact" in decision
                else candidate.get("artifact") or {}
            )
            card = dict(artifact.get("card") or {})
            raw_plan = artifact.get(
                "resolution_plan",
                card.get("resolution_plan"),
            )
            raw_plans = artifact.get(
                "resolution_plans",
                card.get("resolution_plans"),
            )
            plans = [raw_plan] if raw_plan is not None else list(raw_plans or [])
            citations: list[tuple[str, Any]] = [
                (
                    f"candidate {candidate_id} resolution plan",
                    citation,
                )
                for plan in plans
                if isinstance(plan, dict)
                for citation in list(plan.get("citations") or [])
            ]
            raw_clauses = artifact.get(
                "rule_clauses",
                card.get("rule_clauses"),
            )
            citations.extend(
                (
                    f"candidate {candidate_id} rule clause {str(clause.get('id') or '')}",
                    citation,
                )
                for clause in list(raw_clauses or [])
                if isinstance(clause, dict)
                for citation in list(clause.get("source_citations") or [])
            )
            allowed_chunks = {str(chunk_id) for chunk_id in candidate.get("source_chunk_ids") or []}
            for field, citation in citations:
                if not isinstance(citation, dict):
                    raise ValueError(f"{field} citation must be an object")
                source_ref = citation.get("source_ref")
                if not isinstance(source_ref, dict):
                    raise ValueError(f"{field} citation needs source_ref")
                chunk_id = str(source_ref.get("chunk_id") or "")
                if chunk_id not in allowed_chunks or chunk_id not in chunks:
                    raise ValueError(
                        f"{field} citation must use one of the candidate's indexed chunks"
                    )
                canonical = self.rules.citation(
                    chunk_id,
                    source_id=job.source_id,
                )
                if str(citation.get("source") or "") != str(canonical["source"]):
                    raise ValueError(f"{field} citation source does not match its indexed chunk")
                for key in (
                    "source_id",
                    "source_key",
                    "source_checksum",
                ):
                    if key in source_ref and str(source_ref[key]) != str(canonical[key]):
                        raise ValueError(f"{field} citation {key} does not match its indexed chunk")
                excerpt = _support._normalize_source_evidence_text(citation.get("source_excerpt"))
                content = _support._normalize_source_evidence_text(chunks[chunk_id].get("content"))
                if len(excerpt) < 10 or excerpt not in content:
                    raise ValueError(
                        f"{field} source_excerpt is not exact text from its indexed chunk"
                    )

    def rule_content_candidates_extract(
        self,
        campaign_id: str,
        job_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Extract conservative source-linked D&D content for Agent-as-DM review."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for candidate extraction")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        if not job.source_id:
            raise ValueError("rule import job must be indexed before candidate extraction")
        payload = {"job_id": job_id, "operation": "extract_candidates"}
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        source = self.rules.source(job.source_id)
        source_chunks = self.rules.source_chunks(job.source_id)
        inventory = _support.extract_content_inventory(
            source_chunks,
            source_title=str(source.get("title") or ""),
        )
        candidates = list(inventory.pop("candidates"))
        recovery = dict(dict(job.result or {}).get("statblock_catalog_recovery") or {})
        complete_pages = {int(item) for item in recovery.get("complete_pages") or []}
        page_chunks: dict[int, list[str]] = {}
        for chunk in source_chunks:
            start = chunk.get("page_start")
            end = chunk.get("page_end")
            if isinstance(start, int) and isinstance(end, int):
                for page_number in range(start, end + 1):
                    page_chunks.setdefault(page_number, []).append(str(chunk["id"]))
        recovered_candidates: list[dict[str, Any]] = []
        recovered_chunk_ids: set[str] = set()
        source_reviews = _support._select_preferred_statblock_reviews(
            list(dict(job.result or {}).get("statblock_reviews") or [])
        )
        for review in source_reviews:
            review = dict(review)
            page_number = int(review.get("page_number") or 0)
            content = str(review.get("normalized_content") or "").strip()
            checksum = _support.hashlib.sha256(content.encode()).hexdigest()
            if not content or checksum != str(review.get("normalized_content_sha256") or ""):
                raise ValueError("recovered statblock review checksum is stale")
            parsed = _support.parse_2014_statblock_template_preview(
                content,
                source_key=f"rule-review:{review['id']}",
            )
            chunk_ids = list(dict.fromkeys(page_chunks.get(page_number, [])))
            if not chunk_ids:
                raise ValueError("recovered statblock page has no indexed source chunks")
            candidate_id = (
                "candidate:"
                + _support.hashlib.sha256(f"statblock-review\0{checksum}".encode()).hexdigest()[:20]
            )
            recovered_chunk_ids.update(chunk_ids)
            recovered_candidates.append(
                {
                    "id": candidate_id,
                    "kind": "statblock",
                    "name": parsed.name,
                    "source_chunk_ids": chunk_ids,
                    "source_heading_path": [parsed.name],
                    "page_start": page_number,
                    "page_end": page_number,
                    "extraction_confidence": "reviewed",
                    "extraction_signals": [
                        "positioned page layout",
                        str(review.get("confidence") or "reviewed source"),
                    ],
                    "review_status": "pending",
                    "mechanical_scope": "mechanical",
                    "application_state": "catalog_only",
                    "execution_state": "review_ready",
                    "artifact": {
                        "kind": "statblock",
                        "application_state": "catalog_only",
                        "mechanical_scope": "mechanical",
                        "card": {
                            "name": parsed.name,
                            "normalized_content": content,
                            "review_evidence": {
                                "page_number": page_number,
                                "asset_checksum": review.get("asset_checksum"),
                                "image_checksum": review.get("image_checksum"),
                                "normalized_content_sha256": checksum,
                                "confidence": review.get("confidence"),
                            },
                        },
                    },
                }
            )
        if recovered_candidates:
            candidates = _support._project_recovered_statblock_candidates(
                candidates,
                recovered_candidates,
                complete_pages=complete_pages,
            )
            inventory["candidate_count"] = len(candidates)
            counts: dict[str, int] = {}
            for candidate in candidates:
                kind = str(candidate["kind"])
                counts[kind] = counts.get(kind, 0) + 1
            inventory["candidate_counts"] = dict(sorted(counts.items()))
            for item in inventory.get("ledger") or []:
                if str(item.get("chunk_id") or "") in recovered_chunk_ids:
                    item["disposition"] = "structured_entity"
            inventory["claimed_chunk_count"] = sum(
                1
                for item in inventory.get("ledger") or []
                if item.get("disposition") == "structured_entity"
            )
            inventory["descriptive_chunk_count"] = sum(
                1
                for item in inventory.get("ledger") or []
                if item.get("disposition") == "descriptive_context"
            )
            inventory["unresolved_mechanical_chunks"] = [
                item
                for item in inventory.get("unresolved_mechanical_chunks") or []
                if str(item.get("chunk_id") or "") not in recovered_chunk_ids
            ]
            inventory["unresolved_mechanical_count"] = len(
                inventory["unresolved_mechanical_chunks"]
            )
            inventory["recovered_statblock_count"] = len(recovered_candidates)
            inventory["recovery_complete_pages"] = sorted(complete_pages)
        for candidate in candidates:
            candidate["source_citations"] = [
                self.rules.citation(chunk_id, source_id=job.source_id)
                for chunk_id in candidate["source_chunk_ids"]
            ]
        updated = self.import_jobs.set_candidates(
            job_id,
            candidates,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "candidates": [self.import_candidate_view(item) for item in result.candidates],
                    "inventory": inventory,
                },
            ),
        )
        candidate_views = [self.import_candidate_view(item) for item in updated.candidates]
        return {
            "job": self.import_job_view(updated),
            "candidates": candidate_views,
            "inventory": inventory,
        }

    def rule_content_candidates_augment(
        self,
        campaign_id: str,
        job_id: str,
        additions: list[dict[str, Any]],
        rationale: str,
        principal_id: str,
        expected_revision: int | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Add entities missed by layout extraction without accepting invented source text."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        normalized_rationale = " ".join(str(rationale).split())
        if not normalized_rationale or len(normalized_rationale) > 2000:
            raise ValueError("catalog augmentation rationale is required and limited to 2000 chars")
        if not isinstance(additions, list) or not 1 <= len(additions) <= 100:
            raise ValueError("catalog augmentation requires 1 to 100 additions")
        job = self.require_import_job(campaign_id, job_id)
        if job.kind != "rulebook" or not job.source_id:
            raise ValueError("catalog augmentation requires an indexed rulebook source")
        payload = {
            "job_id": job_id,
            "operation": "augment_catalog",
            "additions": _support.deepcopy(additions),
            "rationale": normalized_rationale,
        }
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        if idempotency_key:
            replay = self.replay_idempotent(scope, idempotency_key, payload)
            if replay is not None:
                return replay
        if job.state not in {"extracted", "review_required"}:
            raise ValueError("catalog augmentation must happen before candidate approval")
        if expected_revision is not None and job.revision != expected_revision:
            raise ValueError(
                f"import job revision conflict: expected {expected_revision}, found {job.revision}"
            )

        available_chunks = {
            str(chunk.get("id") or ""): dict(chunk)
            for chunk in self.rules.source_chunks(job.source_id)
            if str(chunk.get("id") or "")
        }
        supported_kinds = {
            "activity",
            "background",
            "class",
            "feat",
            "feature",
            "item",
            "species",
            "spell",
            "statblock",
            "subclass",
        }
        forbidden_card_keys = {
            "catalog_review",
            "mechanic_refs",
            "resolution_plan",
            "resolution_plans",
            "runtime_contract",
            "selection_contract",
            "semantic_resolution",
        }

        def reject_executable_fields(value: Any, *, path: str = "card") -> None:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    reject_executable_fields(item, path=f"{path}[{index}]")
                return
            if not isinstance(value, dict):
                return
            for key, item in value.items():
                if str(key) in forbidden_card_keys:
                    raise ValueError(f"{path}.{key} is assigned only by reviewed server contracts")
                reject_executable_fields(item, path=f"{path}.{key}")

        candidates = [_support.deepcopy(item) for item in job.candidates]
        added_ids: list[str] = []
        replaced_ids: list[str] = []
        for index, raw_addition in enumerate(additions):
            if not isinstance(raw_addition, dict):
                raise ValueError(f"additions[{index}] must be an object")
            unknown = set(raw_addition) - {
                "kind",
                "name",
                "source_chunk_ids",
                "source_spans",
                "card",
                "note",
                "replace_existing",
            }
            if unknown:
                raise ValueError(f"additions[{index}] has unsupported fields: {sorted(unknown)}")
            kind = str(raw_addition.get("kind") or "").strip().casefold()
            name = " ".join(str(raw_addition.get("name") or "").split())
            if kind not in supported_kinds:
                raise ValueError(f"additions[{index}].kind is not supported")
            if not name or len(name) > 200:
                raise ValueError(f"additions[{index}].name is required and limited to 200 chars")
            raw_chunk_ids = raw_addition.get("source_chunk_ids") or []
            raw_spans = raw_addition.get("source_spans") or []
            if not isinstance(raw_chunk_ids, list) or len(raw_chunk_ids) > 32:
                raise ValueError(
                    f"additions[{index}].source_chunk_ids allows at most 32 source chunks"
                )
            if not isinstance(raw_spans, list) or len(raw_spans) > 32:
                raise ValueError(f"additions[{index}].source_spans allows at most 32 source spans")
            if not raw_chunk_ids and not raw_spans:
                raise ValueError(f"additions[{index}] requires source_chunk_ids or source_spans")
            chunk_ids = list(dict.fromkeys(str(item).strip() for item in raw_chunk_ids))
            if any(not item or item not in available_chunks for item in chunk_ids):
                raise ValueError(
                    f"additions[{index}] references a chunk outside the indexed source"
                )
            source_spans: list[dict[str, Any]] = []
            span_texts: list[str] = []
            for span_index, raw_span in enumerate(raw_spans):
                if not isinstance(raw_span, dict):
                    raise ValueError(
                        f"additions[{index}].source_spans[{span_index}] must be an object"
                    )
                span_unknown = set(raw_span) - {"source_chunk_id", "start", "end", "checksum"}
                if span_unknown:
                    raise ValueError(
                        f"additions[{index}].source_spans[{span_index}] has unsupported "
                        f"fields: {sorted(span_unknown)}"
                    )
                chunk_id = str(raw_span.get("source_chunk_id") or "").strip()
                if chunk_id not in available_chunks:
                    raise ValueError(
                        f"additions[{index}] references a span outside the indexed source"
                    )
                start = raw_span.get("start")
                end = raw_span.get("end")
                if (
                    isinstance(start, bool)
                    or not isinstance(start, int)
                    or isinstance(end, bool)
                    or not isinstance(end, int)
                ):
                    raise ValueError(
                        f"additions[{index}].source_spans[{span_index}] needs integer bounds"
                    )
                chunk_text = str(available_chunks[chunk_id].get("content") or "")
                if not 0 <= start < end <= len(chunk_text):
                    raise ValueError(
                        f"additions[{index}].source_spans[{span_index}] bounds are invalid"
                    )
                span_text = chunk_text[start:end]
                checksum = _support.hashlib.sha256(span_text.encode("utf-8")).hexdigest()
                if str(raw_span.get("checksum") or "") != checksum:
                    raise ValueError(
                        f"additions[{index}].source_spans[{span_index}] checksum mismatch"
                    )
                source_spans.append(
                    {
                        "source_chunk_id": chunk_id,
                        "start": start,
                        "end": end,
                        "checksum": checksum,
                    }
                )
                span_texts.append(span_text)
                chunk_ids.append(chunk_id)
            chunk_ids = list(dict.fromkeys(chunk_ids))
            identity_key = (
                kind,
                "".join(character for character in name.casefold() if character.isalnum()),
            )
            replace_existing = raw_addition.get("replace_existing", False)
            if not isinstance(replace_existing, bool):
                raise ValueError(f"additions[{index}].replace_existing must be a boolean")
            matching_existing = [
                item
                for item in candidates
                if (
                    str(item.get("kind") or "").casefold(),
                    "".join(
                        character
                        for character in str(item.get("name") or "").casefold()
                        if character.isalnum()
                    ),
                )
                == identity_key
            ]
            referenced_chunks = set(chunk_ids)
            matching_existing = [
                item
                for item in matching_existing
                if referenced_chunks.intersection(
                    str(chunk_id) for chunk_id in item.get("source_chunk_ids", [])
                )
            ]
            if matching_existing and not replace_existing:
                raise ValueError(
                    f"additions[{index}] duplicates an existing candidate; revise it during review"
                )
            if replace_existing:
                if len(matching_existing) != 1:
                    raise ValueError(
                        f"additions[{index}].replace_existing requires exactly one existing "
                        "candidate with the same kind and name"
                    )
                replaced = matching_existing[0]
                if replaced.get("agent_catalog_addition"):
                    raise ValueError(
                        f"additions[{index}].replace_existing cannot replace another "
                        "source-bound addition"
                    )
                candidates.remove(replaced)
                replaced_ids.append(str(replaced.get("id") or ""))
            raw_card = raw_addition.get("card") or {}
            if not isinstance(raw_card, dict):
                raise ValueError(f"additions[{index}].card must be an object")
            if len(_support.json.dumps(raw_card, ensure_ascii=False)) > 64000:
                raise ValueError(f"additions[{index}].card exceeds 64000 serialized chars")
            card = _support.deepcopy(raw_card)
            reject_executable_fields(card)
            source_chunks = [available_chunks[item] for item in chunk_ids]
            source_text = "\n\n".join(
                text.strip() for text in span_texts if text.strip()
            ) or "\n\n".join(
                str(chunk.get("content") or "").strip()
                for chunk in source_chunks
                if str(chunk.get("content") or "").strip()
            )
            if not source_text:
                raise ValueError(f"additions[{index}] source chunks contain no indexed text")
            heading_leaves = [
                str((chunk.get("heading_path") or [""])[-1])
                for chunk in source_chunks
                if chunk.get("heading_path")
            ]
            identity_evidence = " ".join(
                [
                    " ".join(heading_leaves),
                    *(
                        str(part)
                        for chunk in source_chunks
                        for part in (chunk.get("heading_path") or [])
                    ),
                    source_text,
                ]
            )
            ocr_identity_evidence = None
            if not _support._catalog_identity_is_evidenced(name, identity_evidence):
                candidate_pages = sorted(
                    {
                        int(page)
                        for chunk in source_chunks
                        for page in (chunk.get("page_start"), chunk.get("page_end"))
                        if isinstance(page, int) and page > 0
                    }
                )[:8]
                ocr_identity_evidence = self.local_rule_catalog_identity_evidence(
                    self.storage.artifact_rulebook_path(job.artifact),
                    candidate_pages,
                    name=name,
                )
            if (
                not _support._catalog_identity_is_evidenced(name, identity_evidence)
                and ocr_identity_evidence is None
            ):
                raise ValueError(
                    f"additions[{index}].name is not evidenced by the referenced source chunks"
                )
            card["name"] = name
            card["description"] = source_text[:24000]
            identity = _support.json.dumps(
                {
                    "source_id": job.source_id,
                    "kind": kind,
                    "name": name.casefold(),
                    "source_chunk_ids": sorted(chunk_ids),
                    "source_spans": source_spans,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            candidate_id = (
                "candidate:agent:"
                + _support.hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            )
            pages_start = [
                int(chunk["page_start"])
                for chunk in source_chunks
                if isinstance(chunk.get("page_start"), int)
            ]
            pages_end = [
                int(chunk["page_end"])
                for chunk in source_chunks
                if isinstance(chunk.get("page_end"), int)
            ]
            candidate = {
                "id": candidate_id,
                "kind": kind,
                "name": name,
                "source_chunk_ids": chunk_ids,
                "source_spans": source_spans,
                "source_heading_path": list(source_chunks[0].get("heading_path") or []),
                "page_start": min(pages_start) if pages_start else None,
                "page_end": max(pages_end) if pages_end else None,
                "extraction_confidence": "agent_review_required",
                "extraction_signals": ["source-bound agent catalog addition"],
                "review_status": "pending",
                "mechanical_scope": "review_required",
                "application_state": "catalog_only",
                "execution_state": "agent_resolution_required",
                "agent_catalog_addition": {
                    "principal_id": principal_id,
                    "rationale": normalized_rationale,
                    "note": " ".join(str(raw_addition.get("note") or "").split())[:2000],
                    "replaced_candidate_id": (
                        str(matching_existing[0].get("id") or "") if replace_existing else ""
                    ),
                    "identity_evidence": (
                        {"mode": "local_ocr", **ocr_identity_evidence}
                        if ocr_identity_evidence is not None
                        else {"mode": "indexed_text"}
                    ),
                },
                "artifact": {
                    "kind": kind,
                    "application_state": "catalog_only",
                    "mechanical_scope": "review_required",
                    "card": card,
                },
                "source_citations": [
                    self.rules.citation(chunk_id, source_id=job.source_id) for chunk_id in chunk_ids
                ],
            }
            candidates.append(candidate)
            added_ids.append(candidate_id)

        idempotency_write = (
            _support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "candidates": [self.import_candidate_view(item) for item in result.candidates],
                    "added_candidate_ids": added_ids,
                    "replaced_candidate_ids": replaced_ids,
                },
            )
            if idempotency_key
            else None
        )
        updated = self.import_jobs.set_candidates(
            job_id,
            candidates,
            idempotency_key=idempotency_key,
            idempotency_write=idempotency_write,
        )
        return {
            "job": self.import_job_view(updated),
            "candidates": [self.import_candidate_view(item) for item in updated.candidates],
            "added_candidate_ids": added_ids,
            "replaced_candidate_ids": replaced_ids,
        }

    def import_job_review_candidates(
        self,
        campaign_id: str,
        job_id: str,
        decisions: list[dict[str, Any]],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
        operation: str = "edit",
    ) -> dict[str, Any]:
        """Save Agent-owned candidate decisions while the rulebook Pack is editable."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= 500:
            raise ValueError("candidate editing requires 1 to 500 decisions")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        if job.state not in {"extracted", "review_required"}:
            raise ValueError("only an editable rulebook draft accepts candidate decisions")
        candidates_by_id = {
            str(candidate.get("id") or ""): _support.deepcopy(candidate)
            for candidate in job.candidates
        }
        normalized_decisions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw_decision in enumerate(decisions):
            if not isinstance(raw_decision, dict):
                raise ValueError(f"decisions[{index}] must be an object")
            candidate_id = str(raw_decision.get("id") or "").strip()
            if candidate_id not in candidates_by_id:
                raise ValueError(f"unknown candidate: {candidate_id}")
            if candidate_id in seen:
                raise ValueError(f"duplicate candidate decision: {candidate_id}")
            seen.add(candidate_id)
            current = candidates_by_id[candidate_id]
            status = str(
                raw_decision.get("review_status", current.get("review_status") or "pending")
            )
            if status not in {"pending", "accepted", "rejected", "needs_revision"}:
                raise ValueError(
                    "review_status must be pending, accepted, rejected, or needs_revision"
                )
            decision: dict[str, Any] = {
                "id": candidate_id,
                "review_status": status,
                "editor": principal_id,
                "operation": operation,
            }
            for field in ("artifact", "note", "disposition"):
                if field in raw_decision:
                    decision[field] = _support.deepcopy(raw_decision[field])
            proposed = {**current, "review_status": status}
            if "artifact" in decision:
                if not isinstance(decision["artifact"], dict):
                    raise ValueError("candidate artifact must be an object")
                proposed["artifact"] = _support.deepcopy(decision["artifact"])
            if status == "accepted" and not isinstance(proposed.get("artifact"), dict):
                raise ValueError(f"candidate {candidate_id} needs an artifact before inclusion")
            decision["draft_issues"] = _support.candidate_draft_issues(proposed)
            normalized_decisions.append(decision)

        self.validate_rule_candidate_execution_evidence(job, normalized_decisions)
        payload = {
            "job_id": job_id,
            "operation": operation,
            "decisions": normalized_decisions,
        }
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        if idempotency_key:
            replay = self.replay_idempotent(scope, idempotency_key, payload)
            if replay is not None:
                return replay
        idempotency_write = (
            _support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "candidates": [self.import_candidate_view(item) for item in result.candidates],
                },
            )
            if idempotency_key
            else None
        )
        updated = self.import_jobs.review_candidates(
            job_id,
            normalized_decisions,
            idempotency_key=idempotency_key,
            idempotency_write=idempotency_write,
        )
        return {
            "job": self.import_job_view(updated),
            "candidates": [self.import_candidate_view(item) for item in updated.candidates],
        }

    def import_job_finalize_candidates(
        self,
        campaign_id: str,
        job_id: str,
        note: str,
        principal_id: str,
        expected_revision: int | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Freeze one fully edited rulebook catalog and only then allow compilation."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_revision is None or not idempotency_key:
            raise ValueError("expected_revision and idempotency_key are required for finalization")
        normalized_note = " ".join(str(note).split())
        if not 10 <= len(normalized_note) <= 2000:
            raise ValueError("finalization note must contain 10 to 2000 characters")
        payload = {
            "job_id": job_id,
            "operation": "finalize",
            "note": normalized_note,
            "expected_revision": expected_revision,
        }
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        if job.state not in {"extracted", "review_required"} or (
            job.state == "extracted" and bool(job.candidates)
        ):
            raise ValueError("only an editable rulebook draft may be finalized")
        if not job.source_id:
            raise ValueError("rulebook candidates need an indexed source before finalization")
        source = self.rules.source(job.source_id)
        finalized_candidates: list[dict[str, Any]] = []
        for candidate in job.candidates:
            value = _support.deepcopy(candidate)
            status = str(value.get("review_status") or "")
            if status not in {"accepted", "rejected"}:
                value["review_status"] = "rejected"
                value["disposition"] = "exclude"
                value["review_note"] = (
                    str(value.get("review_note") or "").strip()
                    or "Excluded by explicit Agent finalization without inclusion"
                )
            value["draft_issues"] = _support.candidate_draft_issues(value)
            blockers = [
                issue
                for issue in value["draft_issues"]
                if str(issue.get("severity") or "") == "blocker"
            ]
            if blockers:
                raise ValueError(
                    f"candidate {value.get('id')} has deterministic blockers: "
                    + "; ".join(str(issue.get("message") or "") for issue in blockers)
                )
            finalized_candidates.append(value)
        self.validate_rule_candidate_execution_evidence(
            job,
            [
                {
                    "id": candidate["id"],
                    "review_status": candidate["review_status"],
                    "artifact": candidate.get("artifact"),
                }
                for candidate in finalized_candidates
            ],
        )
        updated = self.import_jobs.finalize_candidate_review(
            job_id,
            candidates=finalized_candidates,
            confirmation={
                "confirmed_by": principal_id,
                "method": "agent",
                "note": normalized_note,
                "source_id": job.source_id,
                "source_checksum": str(source.get("checksum") or ""),
                "parser_profile": job.parser_profile,
                "parser_version": job.parser_version,
            },
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "candidates": [self.import_candidate_view(item) for item in result.candidates],
                },
            ),
        )
        return {
            "job": self.import_job_view(updated),
            "candidates": [self.import_candidate_view(item) for item in updated.candidates],
        }

    def campaign_rule_source_ids(self, campaign_id: str) -> set[str]:
        """Resolve the indexed sources visible to this campaign and branch."""

        profile = self.rule_profiles.get(campaign_id)
        if profile is None:
            raise _support.RulesetUnavailableError("campaign has no authoritative rule profile")
        bundled_root = self.config.dnd_skills_dir / "full" / "skills" / "dnd-dm" / "srd"
        bundled_identities = (
            {
                (source.source_key, source.checksum)
                for source in _support.build_bundled_rule_sources(bundled_root)
            }
            if bundled_root.is_dir()
            else set()
        )
        allowed = {
            str(source["id"])
            for source in self.rules.sources(system_id=_support.DND5E.id)
            if (str(source.get("source_key") or ""), str(source.get("checksum") or ""))
            in bundled_identities
            and str(source.get("edition") or "") == str(profile.edition)
        }

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                source_id = value.get("source_id")
                if isinstance(source_id, str) and source_id:
                    allowed.add(source_id)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        effective = self.rule_packs.effective_ruleset(campaign_id)
        for locked in effective.lock:
            version = self.rule_packs.get_version(
                str(locked["pack_id"]),
                str(locked["version"]),
            )
            collect(_support.asdict(version))

        if self.authoritative_phase(campaign_id) == _support.PROFILE_LOBBY:
            allowed.update(
                str(job.source_id)
                for job in self.import_jobs.list(campaign_id, kind="rulebook")
                if job.source_id
            )
        return allowed

    def rule_search(
        self,
        campaign_id: str,
        query: str,
        filters: Annotated[
            dict[str, Any] | None,
            _support.Field(
                description=(
                    "Optional exact evidence-backed filters: edition, locale, publications, "
                    "source_ids, source_keys, or positive page. Omit for the first lookup; "
                    "an empty object means unfiltered."
                )
            ),
        ] = None,
        top_k: int = 8,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Search rules visible to the campaign; first lookup needs only id and query."""
        self.access.require_campaign(campaign_id, principal_id)
        if not str(query or "").strip():
            raise ValueError("rule_search query is required")
        if not 1 <= top_k <= 200:
            raise ValueError("rule_search top_k must be between 1 and 200")
        filter_data = dict(filters or {})
        allowed_filter_fields = {
            "edition",
            "locale",
            "publications",
            "source_ids",
            "source_keys",
            "page",
        }
        if unknown_fields := sorted(set(filter_data) - allowed_filter_fields):
            raise ValueError(
                "rule_search filters contain unsupported fields: " + ", ".join(unknown_fields)
            )

        def optional_text(field_name: str) -> str | None:
            value = filter_data.get(field_name)
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"rule_search filters.{field_name} must be a non-empty string")
            return value.strip()

        def optional_text_list(field_name: str) -> list[str] | None:
            value = filter_data.get(field_name)
            if value is None:
                return None
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                raise ValueError(
                    f"rule_search filters.{field_name} must be a non-empty string list"
                )
            return [item.strip() for item in value]

        edition = optional_text("edition")
        locale = optional_text("locale")
        if locale is None:
            profile = self.rule_profiles.get(campaign_id)
            if profile is None:
                raise _support.RulesetUnavailableError("campaign has no authoritative rule profile")
            locale = str(profile.locale)
        publications = optional_text_list("publications")
        source_ids = optional_text_list("source_ids")
        source_keys = optional_text_list("source_keys")
        page_value = filter_data.get("page")
        if page_value is not None and (
            not isinstance(page_value, int) or isinstance(page_value, bool) or page_value < 1
        ):
            raise ValueError("rule_search filters.page must be a positive integer")
        page = int(page_value) if page_value is not None else None
        allowed_source_ids = self.campaign_rule_source_ids(campaign_id)
        allowed_sources = {
            str(source["id"]): source
            for source in self.rules.sources(system_id=_support.DND5E.id)
            if str(source["id"]) in allowed_source_ids
        }
        if source_ids:
            requested = {str(item) for item in source_ids}
            if unknown := sorted(requested - allowed_source_ids):
                raise ValueError(
                    "rule_search source_ids are outside the current campaign ruleset: "
                    + ", ".join(unknown)
                )
            allowed_source_ids &= requested
        allowed_publications = {
            str(source.get("publication_id") or "")
            for source in allowed_sources.values()
            if str(source.get("publication_id") or "")
        }
        if publications:
            requested_publications = {str(item) for item in publications}
            if unknown := sorted(requested_publications - allowed_publications):
                raise ValueError(
                    "rule_search publications are outside the current campaign ruleset: "
                    + ", ".join(unknown)
                    + "; omit publications unless exact source evidence supplies one"
                )
        allowed_source_keys = {
            str(source.get("source_key") or "")
            for source in allowed_sources.values()
            if str(source.get("source_key") or "")
        }
        if source_keys:
            requested_source_keys = {str(item) for item in source_keys}
            if unknown := sorted(requested_source_keys - allowed_source_keys):
                raise ValueError(
                    "rule_search source_keys are outside the current campaign ruleset: "
                    + ", ".join(unknown)
                    + "; omit source_keys unless exact source evidence supplies one"
                )
        if not allowed_source_ids:
            return _support._facade_result(
                "search",
                [],
                page={
                    "limit": top_k,
                    "returned": 0,
                    "has_more": False,
                    "next_cursor": None,
                    "total_count": 0,
                },
            )
        embedder, vectors = self.storage.dense_components()
        hits = self.rules.search(
            system_id=_support.DND5E.id,
            query=query,
            query_hints=_support.DND5E_QUERY_HINTS,
            edition=edition,
            locale=locale,
            publications=publications,
            source_ids=sorted(allowed_source_ids),
            source_keys=source_keys,
            top_k=100,
            embedder=embedder,
            vector_store=vectors,
        )
        values = [_support.asdict(hit) for hit in hits]
        if page is not None:
            values = [
                item
                for item in values
                if int(dict(item.get("metadata") or {}).get("page_start") or 0)
                <= page
                <= int(dict(item.get("metadata") or {}).get("page_end") or 0)
            ]
        values, pagination = _support._bounded_page(
            values,
            scope=(
                f"rule_search:{campaign_id}:{principal_id}:{query}:{_support.json_sha256(filter_data)}"
            ),
            limit=top_k,
            cursor=cursor,
        )
        return _support._facade_result("search", values, page=pagination)

    def rule_expand(
        self,
        campaign_id: str,
        chunk_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Read one indexed chunk only when its source belongs to this campaign ruleset."""
        self.access.require_campaign(campaign_id, principal_id)
        expanded = self.rules.expand(chunk_id)
        source_id = str(dict(expanded.get("source") or {}).get("id") or "")
        if source_id not in self.campaign_rule_source_ids(campaign_id):
            raise PermissionError("rule chunk is outside the current campaign ruleset")
        return expanded

    def submit_import_text_review(
        self,
        campaign_id: str,
        job_id: str,
        page_number: int,
        base_text_sha256: str,
        replacements: list[dict[str, str]],
        rationale: str,
        evidence_basis: Literal["cross_text", "agent_context", "rendered_page"],
        rendered_image_checksum: str | None,
        review_method: Literal["agent", "human"],
        principal_id: str,
        expected_revision: int | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Persist one bounded Agent/human transcript repair and rerun inspection."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for text review")
        job = self.require_import_job(campaign_id, job_id)
        request_payload = {
            "job_id": job_id,
            "operation": "review_text",
            "page_number": page_number,
            "base_text_sha256": base_text_sha256,
            "replacements": _support.deepcopy(replacements),
            "rationale": rationale,
            "evidence_basis": evidence_basis,
            "rendered_image_checksum": rendered_image_checksum,
            "review_method": review_method,
        }
        scope_key = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope_key, idempotency_key, request_payload)
        if replay is not None:
            return replay
        if job.state != "inspected" and not (job.kind == "module" and job.state == "imported"):
            raise ValueError("text review requires an inspected, not-yet-ingested import job")
        if expected_revision is not None and expected_revision != job.revision:
            raise ValueError(
                f"import job revision conflict: expected {expected_revision}, found {job.revision}"
            )
        if evidence_basis not in {"cross_text", "agent_context", "rendered_page"}:
            raise ValueError("evidence_basis is invalid")
        if review_method not in {"agent", "human"}:
            raise ValueError("review_method must be agent or human")
        if not isinstance(replacements, list):
            raise ValueError("replacements must be an array")
        normalized_replacements: list[dict[str, str]] = []
        for index, replacement in enumerate(replacements):
            if not isinstance(replacement, dict) or set(replacement) != {"old", "new"}:
                raise ValueError(f"replacements[{index}] must contain only old and new")
            normalized_replacements.append(
                {"old": str(replacement["old"]), "new": str(replacement["new"])}
            )
        page_review_count = sum(
            int(item.get("page_number") or 0) == page_number
            for item in self.import_page_revisions(job)
        )
        if page_review_count >= 8:
            raise ValueError("this physical page already has eight transcription reviews")
        # Only cross-text review needs fresh independent OCR transcripts at
        # submission time.  Agent-context review is bounded by the current
        # normalized-page hash and edit distance, while rendered-page review is
        # bound to the immutable image checksum below.  Avoiding an otherwise
        # unused two-model OCR pass makes Agent-authored spelling/heading repair
        # cheap enough to use throughout large rulebooks and modules.
        evidence = self.staged_transcription_evidence(
            job,
            page_number,
            include_ocr=evidence_basis == "cross_text",
        )
        actual_base = str(dict(evidence["normalized"])["text_sha256"])
        if str(base_text_sha256) != actual_base:
            raise ValueError("base_text_sha256 does not match the current normalized page")
        normalized_page_text = str(dict(evidence["normalized"])["text"])
        empty_anchor_count = sum(not replacement["old"] for replacement in normalized_replacements)
        if empty_anchor_count:
            if (
                evidence_basis != "rendered_page"
                or normalized_page_text.strip()
                or len(normalized_replacements) != 1
                or empty_anchor_count != 1
            ):
                raise ValueError(
                    "an empty replacement anchor is only valid for one rendered-page "
                    "recovery of a wholly empty normalized page"
                )
        evidence_texts = [
            str(dict(evidence["native_text"])["text"]),
            *[
                str(dict(item).get("text") or "")
                for item in dict(evidence["ocr"]).get("variants", [])
                if item.get("available")
            ],
        ]
        if evidence_basis == "rendered_page":
            rendered = _support.render_pdf_page(
                self.storage.artifact_rulebook_path(job.artifact)
                if job.kind == "rulebook"
                else self.storage.artifact_module_path(job.artifact),
                page_number,
                scale=1.5,
            )
            if rendered_image_checksum != rendered.checksum:
                raise ValueError("rendered page checksum does not match review evidence")
        for index, replacement in enumerate(normalized_replacements):
            old = replacement["old"]
            new = replacement["new"]
            if not new or old == new:
                raise ValueError(f"replacements[{index}] is invalid")
            supporting_sources = sum(
                bool(new and new.casefold() in text.casefold()) for text in evidence_texts
            )
            if evidence_basis == "cross_text" and supporting_sources < 2:
                raise ValueError(
                    f"replacements[{index}].new requires agreement from two text sources"
                )
            if evidence_basis == "agent_context":
                if _support.re.findall(r"\d+", old) != _support.re.findall(
                    r"\d+", new
                ) or _support._transcription_numeric_semantics(
                    old
                ) != _support._transcription_numeric_semantics(new):
                    raise ValueError("agent_context cannot alter numeric evidence")
                old_key = _support._compact_transcription_key(old)
                new_key = _support._compact_transcription_key(new)
                edit_limit = max(2, int(max(len(old_key), len(new_key)) * 0.2))
                if (
                    not old_key
                    or not new_key
                    or _support._bounded_edit_distance(old_key, new_key, limit=edit_limit)
                    > edit_limit
                ):
                    raise ValueError(
                        f"replacements[{index}] exceeds bounded agent_context correction"
                    )
        review_evidence = {
            "basis": evidence_basis,
            "normalized_text_sha256": actual_base,
            "native_text_sha256": dict(evidence["native_text"])["text_sha256"],
            "ocr_variants": [
                {key: item[key] for key in ("model", "profile", "text_sha256")}
                for item in dict(evidence["ocr"]).get("variants", [])
                if item.get("available")
            ],
            **(
                {"rendered_image_checksum": rendered_image_checksum}
                if evidence_basis == "rendered_page"
                else {}
            ),
        }
        revision = {
            "source_checksum": job.artifact_checksum,
            "page_number": page_number,
            "base_text_sha256": actual_base,
            "replacements": _support.deepcopy(normalized_replacements),
            "reviewer": principal_id,
            "review_method": review_method,
            "rationale": rationale,
            "evidence": review_evidence,
        }
        revisions = [*self.import_page_revisions(job), revision]
        if job.kind == "rulebook":
            inspection = self.rules.inspect_path(
                self.storage.artifact_rulebook_path(job.artifact),
                **self.rule_document_options(job.artifact_checksum, revisions),
            )
        else:
            inspection = self.modules.preview_path(
                self.storage.artifact_module_path(job.artifact),
                parser=_support.MarkdownModuleParser(profile=_support.DndModuleProfile()),
                **self.module_document_options(job.artifact_checksum, revisions),
            )
        inspection["page_revisions"] = _support.deepcopy(revisions)
        updated = self.import_jobs.record_inspection(
            job_id,
            inspection,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope_key,
                payload=request_payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "review": revision,
                    "inspection": inspection,
                },
            ),
        )
        return {
            "job": self.import_job_view(updated),
            "review": revision,
            "inspection": inspection,
        }

    def local_rule_catalog_identity_evidence(
        self,
        source_path: str | _support.Path,
        page_numbers: list[int],
        *,
        name: str,
    ) -> dict[str, Any] | None:
        """Recover a failed catalog identity with bounded local OCR model fallback."""

        if not self.config.rule_ocr_enabled or not page_numbers:
            return None
        preferred = self.storage.rule_ocr_provider()
        if preferred is None:
            return None
        preferred_model = str(getattr(preferred, "model_type", self.config.rule_ocr_model))
        models = [preferred_model, "small" if preferred_model == "medium" else "medium"]
        for model in models:
            observations: list[dict[str, Any]] = []
            for page_number in page_numbers:
                layout = self.cached_rapidocr_layout(
                    source_path,
                    page_number,
                    scale=self.config.rule_ocr_scale,
                    preferred_provider=(preferred if model == preferred_model else None),
                    model_type=model,
                )
                page_text, _used_columns = _support.ocr_layout_text(layout)
                provider_profile = (
                    preferred.cache_profile
                    if model == preferred_model
                    else _support.RapidOcrProvider(
                        scale=self.config.rule_ocr_scale,
                        model_type=model,
                        cache_dir=self.config.ocr_page_cache_dir,
                    ).cache_profile
                )
                observations.append(
                    {
                        "provider": "rapidocr",
                        "profile": provider_profile,
                        "model": model,
                        "scale": self.config.rule_ocr_scale,
                        "page_number": page_number,
                        "text_sha256": _support.hashlib.sha256(
                            page_text.encode("utf-8")
                        ).hexdigest(),
                        "text": page_text,
                    }
                )
            selected = _support._select_catalog_ocr_identity_evidence(name, observations)
            if selected is not None:
                return selected
        return None

    def rule_statblock_catalog_recover(
        self,
        campaign_id: str,
        job_id: str,
        page_numbers: list[int] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Enumerate and recover every structurally proven statblock page."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise ValueError("layout statblock catalog recovery currently supports D&D 2014")
        if not idempotency_key:
            raise ValueError("idempotency_key is required for statblock catalog recovery")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        if not job.source_id:
            raise ValueError("rule import job must be indexed before catalog recovery")
        source_path = self.storage.artifact_rulebook_path(job.artifact)
        if source_path.suffix.casefold() != ".pdf":
            raise ValueError("statblock catalog recovery requires a staged PDF")
        chunks = self.rules.source_chunks(job.source_id)
        source = self.rules.source(job.source_id)
        inventory = _support.extract_content_inventory(
            chunks,
            source_title=str(source.get("title") or ""),
        )
        catalog_statblocks = [
            dict(item) for item in inventory["candidates"] if item.get("kind") == "statblock"
        ]
        chunks_by_id = {
            str(chunk.get("id") or ""): str(chunk.get("content") or "") for chunk in chunks
        }
        usable_catalog_ids: set[str] = set()
        usable_catalog_names: list[str] = []
        usable_catalog_counts_by_page: dict[int, int] = {}
        for candidate in catalog_statblocks:
            card = dict(dict(candidate.get("artifact") or {}).get("card") or {})
            normalized = str(
                card.get("normalized_content") or candidate.get("normalized_content") or ""
            ).strip()
            raw_source = "\n\n".join(
                chunks_by_id.get(str(chunk_id), "").strip()
                for chunk_id in candidate.get("source_chunk_ids") or []
                if chunks_by_id.get(str(chunk_id), "").strip()
            )
            probe = normalized or (
                f"# {str(candidate.get('name') or '').strip()}\n\n{raw_source}"
                if raw_source
                else ""
            )
            template_requirement = _support.parameterized_statblock_requirements(probe)
            usable = False
            if template_requirement is not None:
                try:
                    _support.parse_2014_statblock_template_preview(
                        probe,
                        source_key=str(candidate.get("id") or "catalog-statblock"),
                        rule_refs=[],
                    )
                except (_support.StatblockImportError, ValueError):
                    pass
                else:
                    usable = True
            if normalized and raw_source and not usable:
                try:
                    normalized_card = _support.parse_2014_statblock(
                        normalized,
                        source_key=str(candidate.get("id") or "catalog-statblock"),
                        rule_refs=[],
                    )
                    source_card = _support.parse_2014_statblock(
                        (f"# {str(candidate.get('name') or '').strip()}\n\n{raw_source}"),
                        source_key=(f"{str(candidate.get('id') or 'catalog-statblock')}:source"),
                        rule_refs=[],
                    )
                except (_support.StatblockImportError, ValueError):
                    pass
                else:
                    usable = _support._statblock_mechanical_identity(
                        normalized_card
                    ) == _support._statblock_mechanical_identity(source_card)
            elif raw_source and not usable:
                try:
                    _support.parse_2014_statblock(
                        (f"# {str(candidate.get('name') or '').strip()}\n\n{raw_source}"),
                        source_key=(f"{str(candidate.get('id') or 'catalog-statblock')}:source"),
                        rule_refs=[],
                    )
                except (_support.StatblockImportError, ValueError):
                    pass
                else:
                    usable = True
            if not usable:
                continue
            usable_catalog_ids.add(str(candidate.get("id") or ""))
            usable_catalog_names.append(str(candidate.get("name") or ""))
            start = candidate.get("page_start")
            end = candidate.get("page_end")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
            ):
                for page_number in range(start, end + 1):
                    usable_catalog_counts_by_page[page_number] = (
                        usable_catalog_counts_by_page.get(page_number, 0) + 1
                    )

        def usable_catalog_name(name: str) -> bool:
            return any(
                _support._bounded_ocr_heading_equivalent(name, candidate_name)
                for candidate_name in usable_catalog_names
            )

        page_count = int(dict(job.inspection or {}).get("page_count", 0) or 0)
        if page_count < 1:
            raise RuntimeError("rule import inspection has no page count")
        index_hints = {
            "entry_count": 0,
            "page_offset": None,
            "offset_support": 0,
            "by_page": {},
        }
        source_text_hints = _support._source_statblock_recovery_hints(chunks)
        source_text_hints["by_page"] = {
            page: [name for name in names if not usable_catalog_name(name)]
            for page, names in source_text_hints["by_page"].items()
            if any(not usable_catalog_name(name) for name in names)
        }
        source_text_hints["entry_count"] = sum(
            len(names) for names in source_text_hints["by_page"].values()
        )
        if page_numbers is None:
            page_text: dict[int, list[str]] = {}
            for chunk in chunks:
                start = chunk.get("page_start")
                end = chunk.get("page_end")
                if (
                    isinstance(start, int)
                    and not isinstance(start, bool)
                    and isinstance(end, int)
                    and not isinstance(end, bool)
                ):
                    for page_number in range(start, min(end, start + 2) + 1):
                        page_text.setdefault(page_number, []).append(
                            " ".join(
                                [
                                    *[str(item) for item in chunk.get("heading_path") or []],
                                    str(chunk.get("content") or ""),
                                ]
                            )
                        )
            selected_pages = []
            for page_number, values in page_text.items():
                text = " ".join(values)
                folded = text.casefold()
                source_path_key = _support.compact_ascii_key(text)
                appendix_creature_page = bool(
                    "appendixdcreaturestatistics" in source_path_key
                    and _support.re.search(
                        r"(?i)\b(?:tiny|small|medium|large|huge|gargantuan)\b",
                        text,
                    )
                    and any(
                        marker in source_path_key
                        for marker in (
                            "armorclass",
                            "armarclass",
                            "hitpoints",
                            "hilpoints",
                            "speed",
                        )
                    )
                )
                if appendix_creature_page or (
                    all(label in folded for label in ("armor class", "hit points", "speed"))
                    and _support.re.search(
                        r"(?i)\b(?:tiny|small|medium|large|huge|gargantuan)\s+"
                        r".{1,160}?\s+armor\s+class\b",
                        text,
                    )
                ):
                    selected_pages.append(page_number)
            index_hints = _support._statblock_index_recovery_hints(
                chunks,
                catalog_statblocks,
                page_count=page_count,
            )
            index_hints["by_page"] = {
                page: [name for name in names if not usable_catalog_name(name)]
                for page, names in index_hints["by_page"].items()
                if any(not usable_catalog_name(name) for name in names)
            }
            selected_pages.extend(index_hints["by_page"])
            selected_pages.extend(source_text_hints["by_page"])
        else:
            if (
                not isinstance(page_numbers, list)
                or not page_numbers
                or len(page_numbers) > 500
                or any(
                    isinstance(item, bool) or not isinstance(item, int) or item < 1
                    for item in page_numbers
                )
                or len(page_numbers) != len(set(page_numbers))
            ):
                raise ValueError("page_numbers must contain 1 to 500 unique positive integers")
            selected_pages = list(page_numbers)
        selected_pages = sorted(set(selected_pages))
        payload = {
            "job_id": job_id,
            "operation": "recover_statblocks",
            "page_numbers": selected_pages,
            "statblock_index_hints": index_hints["by_page"],
            "source_text_hints": source_text_hints["by_page"],
        }
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay

        recovered_items: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        indexed_fallbacks: list[dict[str, Any]] = []
        complete_pages: list[int] = []
        indexed_review_keys: set[tuple[str, int]] = set()
        indexed_pages: set[int] = set()

        # A catalog recovery can legitimately be interrupted after one or more
        # source reviews commit but before the enclosing batch receipt commits.
        # Resume from those immutable reviews instead of attempting the same
        # domain write again.  Besides making long OCR jobs restartable, this
        # keeps a prior successful review from being misreported as an Agent
        # semantic-fill failure merely because its idempotency record exists.
        persisted_reviews = [
            dict(item)
            for item in dict(job.result or {}).get("statblock_reviews") or []
            if isinstance(item, dict)
        ]

        def persisted_review_response(
            *,
            page_number: int,
            name: str,
            review_modes: set[str],
        ) -> dict[str, Any] | None:
            matches: list[dict[str, Any]] = []
            for review in persisted_reviews:
                if (
                    int(review.get("page_number") or 0) != page_number
                    or str(review.get("review_mode") or "visual") not in review_modes
                ):
                    continue
                content = str(review.get("normalized_content") or "").strip()
                if _support.hashlib.sha256(content.encode()).hexdigest() != str(
                    review.get("normalized_content_sha256") or ""
                ):
                    continue
                heading = _support.re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", content)
                if heading is None or not _support._bounded_ocr_heading_equivalent(
                    name, heading.group(1)
                ):
                    continue
                matches.append(review)
            selected = _support._select_preferred_statblock_reviews(matches)
            if len(selected) != 1:
                return None
            review = selected[0]
            content = str(review["normalized_content"])
            parsed = _support.parse_2014_statblock_template_preview(
                content,
                source_key=f"rule-review:{review['id']}",
                rule_refs=[],
            )
            agent_fill = review.get("agent_statblock_fill")
            requirements = (
                self.require_standard_statblock_engine_support(
                    parsed.sheet,
                    agent_fill,
                    statblock_warnings=parsed.warnings,
                )
                if self.is_canonical_standard_rule_source(source)
                else self.statblock_agent_fill_requirements(parsed.sheet)
            )
            status = str(review.get("agent_fill_status") or "")
            if status not in {"complete", "pending", "not_required"}:
                status = (
                    "complete"
                    if agent_fill is not None
                    else ("pending" if requirements["required"] else "not_required")
                )
            validation = {
                "challenge_rating": parsed.challenge_rating,
                "experience_points": parsed.experience_points,
                **self.statblock_settlement(parsed.warnings),
                "agent_fill": agent_fill,
                "agent_fill_status": status,
                "requires_agent_fill": status == "pending",
                "resolved_warnings": [],
                "agent_fill_requirements": requirements,
                "resumed_from_persisted_review": True,
            }
            return {"review": review, "validation": validation}

        for candidate in catalog_statblocks:
            if str(candidate.get("id") or "") in usable_catalog_ids:
                continue
            start = candidate.get("page_start")
            end = candidate.get("page_end")
            normalized = str(candidate.get("normalized_content") or "").strip()
            if (
                candidate.get("execution_state") != "review_ready"
                or not normalized
                or isinstance(start, bool)
                or not isinstance(start, int)
                or isinstance(end, bool)
                or not isinstance(end, int)
                or not set(range(start, end + 1)).intersection(selected_pages)
            ):
                continue
            name = str(candidate.get("name") or "").strip()
            review_key = _support.hashlib.sha256(
                f"{idempotency_key}\0indexed\0{candidate['id']}\0{normalized}".encode()
            ).hexdigest()[:24]
            try:
                reviewed = persisted_review_response(
                    page_number=start,
                    name=name,
                    review_modes={"indexed_text"},
                )
                if reviewed is None:
                    reviewed = self.rule_statblock_review(
                        campaign_id,
                        job_id,
                        start,
                        normalized,
                        (
                            "Deterministic D&D statblock normalization recovered a "
                            "complete card from checksum-bound indexed source chunks."
                        ),
                        principal_id=principal_id,
                        idempotency_key=f"catalog-indexed-review-{review_key}",
                        review_mode="indexed_text",
                        evidence_chunk_ids=list(candidate.get("source_chunk_ids") or []),
                    )
            except (ValueError, _support.StatblockImportError) as error:
                # A text-normalized card can still contain column bleed or a
                # falsely extended heading scope. Retain the indexed failure,
                # but let the page-layout path independently recover the same
                # name instead of suppressing it until runtime.
                indexed_fallbacks.append(
                    {
                        "page_number": start,
                        "page_end": end,
                        "name": name,
                        "error": str(error),
                        "required_resolution": (
                            "engine_implementation"
                            if self.is_canonical_standard_rule_source(source)
                            else "agent_review"
                        ),
                        "catalog_candidate_id": candidate["id"],
                    }
                )
                continue
            indexed_review_keys.add((_support.compact_ascii_key(name), start))
            indexed_pages.update(range(start, end + 1))
            recovered_items.append(
                {
                    "page_number": start,
                    "page_end": end,
                    "name": name,
                    "discovery_provider": "indexed-source",
                    "recovery_mode": "indexed_text",
                    "review_id": reviewed["review"]["id"],
                    "agent_fill_status": reviewed["validation"]["agent_fill_status"],
                    "requires_agent_fill": reviewed["validation"]["requires_agent_fill"],
                    "agent_fill_requirements": reviewed["validation"]["agent_fill_requirements"],
                    "validation": reviewed["validation"],
                }
            )

        text_provider = _support.PdfTextLayoutProvider()
        text_layouts = (
            {
                item.page_number: item
                for item in text_provider.extract_layout(
                    source_path,
                    page_numbers=selected_pages,
                )
            }
            if selected_pages
            else {}
        )
        ocr_provider = self.storage.rule_ocr_provider()
        for page_number in selected_pages:
            text_layout = text_layouts[page_number]
            text_layout_value = text_layout.as_dict()
            text_layout_blocks = list(text_layout_value.get("blocks") or [])
            text_discoveries = _support.discover_2014_statblock_names_from_layout(
                text_layout_value,
                minimum_confidence=0.5,
            )
            discovery_items = _support._merge_statblock_discoveries(
                [
                    item
                    for item in text_discoveries
                    if not usable_catalog_name(str(item.get("name") or ""))
                ],
                primary_provider=text_provider.name,
                secondary=[],
                secondary_provider="",
            )
            # A positioned text layer normally supplies both identity discovery
            # and exact column geometry. Some decorated titles or identity lines
            # are image-font glyphs, however, and a two-card page can expose only
            # one of them. Run OCR only when structural cards remain unpaired;
            # individual text recoveries still fall back to OCR below.
            if (
                ocr_provider is not None
                and not index_hints["by_page"].get(page_number)
                and _support._statblock_ocr_discovery_needed(
                    [item for item, _provider_name in discovery_items],
                    layout_blocks=text_layout_blocks,
                    usable_catalog_count=usable_catalog_counts_by_page.get(page_number, 0),
                )
            ):
                ocr_layout = self.cached_rapidocr_layout(
                    source_path,
                    page_number,
                    scale=float(ocr_provider.scale),
                    preferred_provider=ocr_provider,
                )
                ocr_discoveries = _support.discover_2014_statblock_names_from_layout(
                    ocr_layout.as_dict(),
                    minimum_confidence=0.5,
                )
                discovery_items = _support._merge_statblock_discoveries(
                    [item for item, _provider_name in discovery_items],
                    primary_provider=text_provider.name,
                    secondary=[
                        item
                        for item in ocr_discoveries
                        if not usable_catalog_name(str(item.get("name") or ""))
                    ],
                    secondary_provider=ocr_provider.name,
                )
            for indexed_name in index_hints["by_page"].get(page_number, []):
                if any(
                    _support._bounded_ocr_heading_equivalent(indexed_name, str(item["name"]))
                    for item, _provider in discovery_items
                ):
                    continue
                discovery_items.append(
                    (
                        {"name": indexed_name, "page_number": page_number},
                        "printed-statblock-index",
                    )
                )
            source_hint_additions = _support._source_statblock_hint_additions(
                [item for item, _provider_name in discovery_items],
                source_text_hints["by_page"].get(page_number, []),
                layout_blocks=text_layout_blocks,
            )
            for source_name in source_hint_additions:
                if any(
                    _support._bounded_ocr_heading_equivalent(source_name, str(item["name"]))
                    for item, _provider in discovery_items
                ):
                    continue
                discovery_items.append(
                    (
                        {"name": source_name, "page_number": page_number},
                        "indexed-statblock-core",
                    )
                )
            if not discovery_items:
                if (
                    page_number in indexed_pages or page_number in usable_catalog_counts_by_page
                ) and not any(
                    int(item.get("page_number") or 0) == page_number for item in failures
                ):
                    complete_pages.append(page_number)
                continue
            page_failures = sum(
                1 for item in failures if int(item.get("page_number") or 0) == page_number
            )
            for discovery, discovery_provider in discovery_items:
                name = str(discovery["name"])
                name_key = _support.compact_ascii_key(name)
                if (name_key, page_number) in indexed_review_keys:
                    continue
                recovery_mode = "layout_text"
                try:
                    reviewed = persisted_review_response(
                        page_number=page_number,
                        name=name,
                        review_modes={"layout_text", "layout_ocr"},
                    )
                    if reviewed is None:
                        recovery = _support.recover_2014_statblock_from_ocr(
                            text_layout_value,
                            name=name,
                            minimum_confidence=0.5,
                        )
                        observation = (
                            "Embedded PDF character coordinates recovered a structurally "
                            "bounded statblock from its exact source page."
                        )
                    else:
                        recovery_mode = str(reviewed["review"]["review_mode"])
                except _support.StatblockImportError as text_error:
                    if ocr_provider is None:
                        failures.append(
                            {
                                "page_number": page_number,
                                "name": name,
                                "error": str(text_error),
                                "required_resolution": "agent_review",
                            }
                        )
                        page_failures += 1
                        continue
                    try:
                        recovered_result = self.recover_pdf_statblock_layout(
                            source_path=source_path,
                            target_name=name,
                            candidate_pages=[page_number],
                            provider=ocr_provider,
                        )
                    except (RuntimeError, _support.StatblockImportError) as error:
                        failures.append(
                            {
                                "page_number": page_number,
                                "name": name,
                                "error": str(error),
                                "required_resolution": "agent_review",
                            }
                        )
                        page_failures += 1
                        continue
                    recovery = dict(recovered_result["recovery"])
                    observation = str(recovered_result["observation"])
                    recovery_mode = "layout_ocr"
                if reviewed is None:
                    content = str(recovery["normalized_content"])
                    recovery_key = _support.hashlib.sha256(
                        f"{idempotency_key}\0{page_number}\0{name}\0{content}".encode()
                    ).hexdigest()[:24]
                    try:
                        reviewed = self.rule_statblock_review(
                            campaign_id,
                            job_id,
                            page_number,
                            content,
                            observation,
                            principal_id=principal_id,
                            idempotency_key=f"catalog-review-{recovery_key}",
                            review_mode=recovery_mode,
                        )
                    except (ValueError, _support.StatblockImportError) as error:
                        failures.append(
                            {
                                "page_number": page_number,
                                "name": name,
                                "error": str(error),
                                "required_resolution": "agent_semantic_fill",
                            }
                        )
                        page_failures += 1
                        continue
                    persisted_reviews.append(dict(reviewed["review"]))
                if reviewed is None:
                    failures.append(
                        {
                            "page_number": page_number,
                            "name": name,
                            "error": "statblock review did not produce a resumable result",
                            "required_resolution": "agent_semantic_fill",
                        }
                    )
                    page_failures += 1
                    continue
                recovered_items.append(
                    {
                        "page_number": page_number,
                        "name": name,
                        "discovery_provider": discovery_provider,
                        "recovery_mode": recovery_mode,
                        "review_id": reviewed["review"]["id"],
                        "agent_fill_status": reviewed["validation"]["agent_fill_status"],
                        "requires_agent_fill": reviewed["validation"]["requires_agent_fill"],
                        "agent_fill_requirements": reviewed["validation"][
                            "agent_fill_requirements"
                        ],
                        "validation": reviewed["validation"],
                    }
                )
            recovered_page_keys = {
                (
                    _support.compact_ascii_key(str(item.get("name") or "")),
                    int(item.get("page_number") or 0),
                )
                for item in recovered_items
            }
            unresolved_page_fallbacks = [
                item
                for item in indexed_fallbacks
                if int(item.get("page_number") or 0) == page_number
                and (
                    _support.compact_ascii_key(str(item.get("name") or "")),
                    page_number,
                )
                not in recovered_page_keys
            ]
            if page_failures == 0 and not unresolved_page_fallbacks:
                complete_pages.append(page_number)

        recovered_keys = {
            (
                _support.compact_ascii_key(str(item.get("name") or "")),
                int(item.get("page_number") or 0),
            )
            for item in recovered_items
        }
        unresolved_indexed_fallbacks = [
            item
            for item in indexed_fallbacks
            if (
                _support.compact_ascii_key(str(item.get("name") or "")),
                int(item.get("page_number") or 0),
            )
            not in recovered_keys
        ]
        recovery_summary = {
            "schema_version": 1,
            "statblock_index": {
                key: value for key, value in index_hints.items() if key != "by_page"
            },
            "source_text_hints": {
                "entry_count": source_text_hints["entry_count"],
                "pages": sorted(source_text_hints["by_page"]),
            },
            "selected_pages": selected_pages,
            "complete_pages": sorted(set(complete_pages)),
            "recovered": recovered_items,
            "failures": failures,
            "indexed_fallbacks": indexed_fallbacks,
            "unresolved_indexed_fallbacks": unresolved_indexed_fallbacks,
            "catalog_statblocks": len(catalog_statblocks),
            "indexed_text_reviews": sum(
                1 for item in recovered_items if item["recovery_mode"] == "indexed_text"
            ),
            "agent_review_required": sum(
                1 for item in recovered_items if item["requires_agent_fill"]
            ),
            "status": (
                "complete"
                if not failures and not unresolved_indexed_fallbacks
                else "review_required"
            ),
        }
        current = self.require_import_job(campaign_id, job_id, "rulebook")
        result_value = {
            **dict(current.result or {}),
            "statblock_catalog_recovery": recovery_summary,
        }
        updated = self.import_jobs.record_result(
            job_id,
            result_value,
            state=current.state,
            source_id=current.source_id,
            expected_revision=current.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    **recovery_summary,
                },
            ),
        )
        return {"job": self.import_job_view(updated), **recovery_summary}

    def rule_statblock_review(
        self,
        campaign_id: str,
        job_id: str,
        page_number: int,
        normalized_content: str,
        observation: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
        review_mode: Literal[
            "visual", "agent_text", "indexed_text", "layout_text", "layout_ocr"
        ] = "visual",
        evidence_chunk_ids: list[str] | None = None,
        agent_fill: dict[str, Any] | None = None,
        derived_from_review_id: str | None = None,
        evidence_exclusions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Retain a reviewed transcription and Agent fill bound to rulebook evidence."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for a rule statblock review")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        if not job.source_id:
            raise ValueError("rule import job must be indexed before statblock review")
        source = self.rules.source(job.source_id)
        campaign_edition = self.campaign_rules_edition(campaign_id)
        source_edition = _support.normalize_dnd_edition(str(source.get("edition") or ""))
        if source_edition != campaign_edition:
            raise ValueError(
                "reviewed rule statblocks require matching campaign and source editions"
            )
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValueError("page_number must be a positive integer")
        content = str(normalized_content or "").strip()
        if not content or len(content) > 100_000:
            raise ValueError("normalized_content must contain 1 to 100000 characters")
        if derived_from_review_id is not None:
            parent_id = str(derived_from_review_id).strip()
            reviews = list(dict(job.result or {}).get("statblock_reviews") or [])
            parent_matches = [
                dict(item) for item in reviews if str(item.get("id") or "") == parent_id
            ]
            if len(parent_matches) != 1:
                raise ValueError(
                    "derived_from_review_id does not identify one review on this import job"
                )
            parent = parent_matches[0]
            if (
                int(parent.get("page_number") or 0) != page_number
                or str(parent.get("source_id") or "") != str(job.source_id)
                or str(parent.get("asset_checksum") or "") != str(job.artifact_checksum)
            ):
                raise ValueError(
                    "derived statblock review must retain the same source, asset, and page"
                )
            ancestors = {parent_id}
            cursor = parent
            while cursor.get("derived_from_review_id"):
                ancestor_id = str(cursor["derived_from_review_id"])
                if ancestor_id in ancestors:
                    raise ValueError("statblock review derivation contains a cycle")
                ancestors.add(ancestor_id)
                matches = [
                    dict(item) for item in reviews if str(item.get("id") or "") == ancestor_id
                ]
                if len(matches) != 1:
                    raise ValueError("statblock review derivation ancestor is missing")
                cursor = matches[0]
            derived_from_review_id = parent_id
        reviewed_observation = str(observation or "").strip()
        if not 8 <= len(reviewed_observation) <= 2_000:
            raise ValueError("observation must contain 8 to 2000 characters")
        if review_mode not in {
            "visual",
            "agent_text",
            "indexed_text",
            "layout_text",
            "layout_ocr",
        }:
            raise ValueError(
                "review_mode must be visual, agent_text, indexed_text, layout_text, or layout_ocr"
            )
        if review_mode not in {"agent_text", "indexed_text"} and evidence_chunk_ids not in (
            None,
            [],
        ):
            raise ValueError(f"{review_mode} statblock review does not accept evidence_chunk_ids")
        if review_mode != "agent_text" and evidence_exclusions not in (None, []):
            raise ValueError(f"{review_mode} statblock review does not accept evidence_exclusions")
        payload = {
            "job_id": job_id,
            "operation": "review_statblock",
            "page_number": page_number,
            "normalized_content": content,
            "observation": reviewed_observation,
        }
        if review_mode != "visual":
            payload["review_mode"] = review_mode
        if review_mode in {"agent_text", "indexed_text"}:
            payload["evidence_chunk_ids"] = evidence_chunk_ids
        if review_mode == "agent_text":
            payload["evidence_exclusions"] = evidence_exclusions
        if agent_fill is not None:
            payload["agent_fill"] = agent_fill
        if derived_from_review_id is not None:
            payload["derived_from_review_id"] = derived_from_review_id
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        source_path = self.storage.artifact_rulebook_path(job.artifact)
        if source_path.suffix.casefold() != ".pdf":
            raise ValueError("rule statblock review requires a staged PDF")
        rendered = _support._render_immutable_pdf_page(
            source_path,
            page_number,
            scale=1.5,
            source_checksum=job.artifact_checksum,
        )
        review_identity = (
            f"{job.id}:{page_number}:{_support.hashlib.sha256(content.encode('utf-8')).hexdigest()}"
        )
        if review_mode != "visual":
            review_identity = f"{review_identity}:{review_mode}"
        if review_mode in {"agent_text", "indexed_text"}:
            evidence_identity = ",".join(str(item) for item in evidence_chunk_ids or [])
            review_identity = f"{review_identity}:{evidence_identity}"
        if review_mode == "agent_text":
            exclusions_identity = _support.canonical_json(evidence_exclusions)
            review_identity = f"{review_identity}:evidence-exclusions:{exclusions_identity}"
        if agent_fill is not None:
            fill_identity = _support.canonical_json(agent_fill)
            review_identity = f"{review_identity}:agent-fill:{fill_identity}"
        if derived_from_review_id is not None:
            review_identity = f"{review_identity}:derived-from:{derived_from_review_id}"
        review_digest = _support.hashlib.sha256(review_identity.encode()).hexdigest()
        review_id = f"rule-statblock-review:{review_digest[:24]}"
        review_rule_refs = [
            f"rule-source:{job.source_id}",
            f"rule-source-page:{job.source_id}:{page_number}",
            f"rule-review:{review_id}",
        ]
        parsed = (
            _support.parse_2014_statblock_template_preview(
                content,
                source_key=f"rule-review:{review_id}",
                rule_refs=review_rule_refs,
            )
            if campaign_edition == "2014"
            else self.parse_edition_statblock(
                content,
                edition=campaign_edition,
                source_key=f"rule-review:{review_id}",
                rule_refs=review_rule_refs,
            )
        )
        if self.is_canonical_standard_rule_source(source):
            agent_fill_requirements = self.require_standard_statblock_engine_support(
                parsed.sheet,
                agent_fill,
                statblock_warnings=parsed.warnings,
            )
        else:
            agent_fill_requirements = self.statblock_agent_fill_requirements(parsed.sheet)
            # Persist the evidence-bound transcription before asking an Agent to
            # interpret source-specific semantics.  This is the immutable base
            # review consumed by rulebook_draft(edit, operation=statblock_review).
            # A caller may also submit the fill atomically when it already has one.
            if agent_fill is not None:
                self.require_complete_statblock_agent_fill(parsed.sheet, agent_fill)
        agent_fill_evidence = self.reviewed_statblock_fill_evidence(
            campaign_id,
            agent_fill,
            rule_source_id=str(job.source_id),
            page_number=page_number,
        )
        filled = (
            _support.apply_reviewed_statblock_fill(parsed.sheet, agent_fill)
            if agent_fill is not None
            else None
        )
        if review_mode == "agent_text":
            text_evidence, validated_evidence_exclusions = (
                self.validate_agent_text_statblock_review(
                    source_id=job.source_id,
                    page_number=page_number,
                    content=content,
                    parsed=parsed,
                    evidence_chunk_ids=evidence_chunk_ids,
                    evidence_exclusions=evidence_exclusions,
                )
            )
        elif review_mode == "indexed_text":
            text_evidence = self.validate_indexed_statblock_review(
                source_id=job.source_id,
                page_number=page_number,
                content=content,
                parsed=parsed,
                evidence_chunk_ids=evidence_chunk_ids,
            )
            validated_evidence_exclusions = []
        else:
            text_evidence, validated_evidence_exclusions = [], []
        review = {
            "id": review_id,
            "campaign_id": campaign_id,
            "job_id": job_id,
            "source_id": job.source_id,
            "source_key": source["source_key"],
            "source_checksum": source["checksum"],
            "edition": campaign_edition,
            "artifact": job.artifact,
            "asset_checksum": job.artifact_checksum,
            "page_number": page_number,
            "page_count": rendered.page_count,
            "image_checksum": rendered.checksum,
            "normalized_content": content,
            "normalized_content_sha256": _support.hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest(),
            "observation": reviewed_observation,
            "review_mode": review_mode,
            "confidence": {
                "agent_text": "reviewed_text",
                "indexed_text": "verified_indexed_text",
                "visual": "reviewed_image",
                "layout_text": "verified_pdf_text_layout",
                "layout_ocr": "corroborated_layout_ocr",
            }[review_mode],
            "evidence_chunk_ids": [item["id"] for item in text_evidence],
            "text_evidence": text_evidence,
            "evidence_exclusions": validated_evidence_exclusions,
            "agent_statblock_fill": (filled or {}).get("fill"),
            "agent_statblock_fill_evidence": agent_fill_evidence,
            "agent_fill_status": (
                "complete"
                if filled is not None
                else "pending"
                if agent_fill_requirements["required"]
                else "not_required"
            ),
            "derived_from_review_id": derived_from_review_id,
        }
        reviews = list(dict(job.result or {}).get("statblock_reviews") or [])
        reviews = [item for item in reviews if str(item.get("id") or "") != review_id]
        reviews.append(review)
        resolved_warnings = set((filled or {}).get("resolved_warnings") or [])
        retained_warnings = [
            warning for warning in parsed.warnings if warning not in resolved_warnings
        ]
        retained_warnings = list(
            dict.fromkeys(
                [
                    *retained_warnings,
                    *((filled or {}).get("added_warnings") or []),
                ]
            )
        )
        validation = {
            "challenge_rating": parsed.challenge_rating,
            "experience_points": parsed.experience_points,
            **self.statblock_settlement(retained_warnings),
            "agent_fill": (filled or {}).get("fill"),
            "agent_fill_status": review["agent_fill_status"],
            "requires_agent_fill": review["agent_fill_status"] == "pending",
            "resolved_warnings": sorted(resolved_warnings),
            "agent_fill_requirements": agent_fill_requirements,
        }
        updated = self.import_jobs.record_result(
            job_id,
            {**dict(job.result or {}), "statblock_reviews": reviews},
            state=job.state,
            source_id=job.source_id,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "review": review,
                    "validation": validation,
                },
            ),
        )
        return {
            "job": self.import_job_view(updated),
            "review": review,
            "validation": validation,
        }

    def rule_statblock_review_from_base(
        self,
        campaign_id: str,
        job_id: str,
        base_review_id: str,
        observation: str,
        agent_fill: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add Agent semantic fill without retranscribing immutable reviewed text."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        review_id = str(base_review_id or "").strip()
        reviews = list(dict(job.result or {}).get("statblock_reviews") or [])
        matches = [item for item in reviews if str(item.get("id") or "") == review_id]
        if len(matches) != 1:
            raise ValueError("base rule statblock review does not belong to the import job")
        base_review = dict(matches[0])
        if str(base_review.get("source_id") or "") != str(job.source_id or ""):
            raise ValueError("base rule statblock review source no longer matches its import job")
        if str(base_review.get("asset_checksum") or "") != str(job.artifact_checksum or ""):
            raise ValueError("base rule statblock review artifact checksum is stale")
        content = str(base_review.get("normalized_content") or "")
        if _support.hashlib.sha256(content.encode("utf-8")).hexdigest() != str(
            base_review.get("normalized_content_sha256") or ""
        ):
            raise ValueError("base rule statblock review content checksum is invalid")
        if not isinstance(agent_fill, dict) or not agent_fill:
            raise ValueError("agent_fill must be a non-empty object")
        review_mode = str(base_review.get("review_mode") or "visual")
        if review_mode not in {
            "visual",
            "agent_text",
            "indexed_text",
            "layout_text",
            "layout_ocr",
        }:
            raise ValueError("base rule statblock review mode is unsupported")
        evidence_chunk_ids = (
            [str(item) for item in base_review.get("evidence_chunk_ids") or []]
            if review_mode in {"agent_text", "indexed_text"}
            else None
        )
        return self.rule_statblock_review(
            campaign_id,
            job_id,
            base_review.get("page_number"),
            content,
            observation,
            principal_id,
            idempotency_key,
            review_mode,
            evidence_chunk_ids,
            agent_fill,
            review_id,
        )

    def rule_import_job_compile(
        self,
        campaign_id: str,
        job_id: str,
        manifest: dict[str, Any],
        mechanics: list[dict[str, Any]] | None = None,
        provenance: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Compile an explicitly finalized candidate catalog."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for pack compilation")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        pack_id = str(manifest.get("id") or "").strip()
        if not pack_id:
            raise ValueError("manifest.id is required")
        _support._validate_unreserved_rule_definition_identity(pack_id)
        payload = {
            "job_id": job_id,
            "operation": "compile",
            "manifest": manifest,
            "mechanics": mechanics or [],
            "provenance": provenance or {},
        }
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        if not job.source_id:
            raise ValueError("rule import job must be indexed before pack compilation")
        if job.state not in {"reviewed", "compiled", "validated", "failed"}:
            raise ValueError("content candidates must be explicitly finalized before compilation")
        artifacts = _support.compiled_artifacts_from_candidates(
            job.candidates,
            pack_id=pack_id,
            require_review_contracts=True,
        )
        draft = self.rule_pack_draft_from_source(
            source_id=job.source_id,
            manifest=manifest,
            artifacts=artifacts,
            mechanics=mechanics,
            provenance={**dict(provenance or {}), "import_job_id": job_id},
        )
        state = "compiled" if draft["status"] == "validated" else "failed"
        updated = self.import_jobs.record_validation(
            job_id,
            {"draft": draft, "accepted_artifact_count": len(artifacts)},
            state=state,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "draft": draft,
                },
            ),
        )
        return {"job": self.import_job_view(updated), "draft": draft}

    def rule_pack_install(self, pack_id: str, version: str) -> dict[str, Any]:
        """Install one validated immutable version without enabling it for a campaign."""
        _support._validate_unreserved_rule_definition_identity(pack_id)
        return _support.asdict(self.rule_packs.install(pack_id, version))

    def rule_pack_list(self, pack_id: str | None = None) -> list[dict[str, Any]]:
        """List draft, rejected, validated, and installed rule-pack versions."""
        return [_support.asdict(item) for item in self.rule_packs.list_versions(pack_id)]

    def rule_pack_inspect(self, pack_id: str, version: str) -> dict[str, Any]:
        """Inspect an exact draft or installed version, including validation evidence."""
        return _support.asdict(self.rule_packs.get_version(pack_id, version))

    def rule_pack_remove(self, pack_id: str, version: str) -> dict[str, Any]:
        """Remove an unreferenced version; any branch lock makes removal fail closed."""
        self.rule_packs.remove_version(pack_id, version)
        return {"status": "removed", "pack_id": pack_id, "version": version}

    def campaign_rule_profile_get(
        self, campaign_id: str, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> dict[str, Any] | None:
        """Read the campaign edition/publication profile and exact branch-local pack lock."""
        self.access.require_campaign(campaign_id, principal_id)
        profile = self.rule_profiles.get(campaign_id)
        campaign = self.campaigns.get(campaign_id)
        available_core_pack = (
            _support.asdict(_support.get_core_rule_pack(profile.edition)) if profile else None
        )
        try:
            effective = self.effective_ruleset_view(campaign_id)
            effective_error = None
        except _support.RulePackError as error:
            effective = None
            effective_error = str(error)
        return {
            "profile": _support.asdict(profile) if profile else None,
            "activations": [
                _support.asdict(item) for item in self.rule_packs.activations(campaign_id)
            ],
            "effective": effective,
            "effective_error": effective_error,
            "available_core_pack": available_core_pack,
            "available_official_expansions": list(
                _support.official_expansion_catalog(profile.edition if profile else None)
            ),
            "official_expansion_mount": {
                key: _support.deepcopy(value)
                for key, value in self.official_expansion_mount.items()
                if key != "packages"
            },
            "campaign_revision": campaign.revision,
        }

    def campaign_rule_profile_set(
        self,
        campaign_id: str,
        edition: str,
        locale: str = "en",
        publications: list[str] | None = None,
        options: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set non-executable edition/publication metadata outside active combat."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        edition = _support.normalize_dnd_edition(edition)
        payload = {
            "edition": edition,
            "locale": locale,
            "publications": publications or [],
            "options": options or {},
        }
        scope = f"campaign-rule-profile:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        self.rule_packs.assert_edition_compatible(campaign_id, edition)
        self.rule_profiles.set(
            campaign_id,
            edition=edition,
            locale=locale,
            publications=publications,
            options=self.profile_options_with_core_lock(edition, options),
            expected_campaign_revision=expected_revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "profile": _support.asdict(result["profile"]),
                    "campaign_revision": result["campaign_revision"],
                },
            ),
        )
        committed = self.idempotency.lookup(scope, str(idempotency_key), payload)
        assert committed is not None and committed.response is not None
        return committed.response

    def campaign_rule_pack_set(
        self,
        campaign_id: str,
        pack_id: str,
        version: str,
        enabled: bool = True,
        options: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly pin and enable/disable an installed pack on one campaign branch."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "pack_id": pack_id,
            "version": version,
            "enabled": enabled,
            "options": options or {},
            "branch_id": resolved_branch_id,
        }
        if enabled:
            self.verified_reserved_official_rule_definition(pack_id, version)
        scope = f"campaign-rule-pack-set:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        profile = self.rule_profiles.get(campaign_id)
        if enabled:
            if profile is None:
                raise _support.RulesetUnavailableError(
                    "campaign must lock an edition before enabling a rule pack"
                )
            installed_pack = self.rule_packs.get_version(pack_id, version)
            self.validate_active_native_mechanic_contract(
                installed_pack.manifest,
                edition=profile.edition,
            )

        def activation_response(result: dict[str, Any]) -> dict[str, Any]:
            return {
                "activation": _support.asdict(result["activation"]),
                "effective": self.effective_ruleset_view_from(
                    result["effective"],
                    profile,
                ),
                "campaign_revision": int(result["campaign_revision"]),
            }

        self.rule_packs.set_activation(
            campaign_id,
            pack_id=pack_id,
            version=version,
            enabled=enabled,
            options=options,
            branch_id=resolved_branch_id,
            expected_campaign_revision=expected_revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=activation_response,
            ),
        )
        committed = self.idempotency.lookup(scope, str(idempotency_key), payload)
        assert committed is not None and committed.response is not None
        return committed.response

    def campaign_rule_pack_remove(
        self,
        campaign_id: str,
        pack_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Remove a future branch-local activation while preserving historical receipts."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {"pack_id": pack_id, "branch_id": resolved_branch_id}
        scope = f"campaign-rule-pack-remove:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        profile = self.rule_profiles.get(campaign_id)

        def removal_response(result: dict[str, Any]) -> dict[str, Any]:
            return {
                "effective": self.effective_ruleset_view_from(
                    result["effective"],
                    profile,
                ),
                "campaign_revision": int(result["campaign_revision"]),
            }

        self.rule_packs.remove_activation(
            campaign_id,
            pack_id,
            branch_id=resolved_branch_id,
            expected_campaign_revision=expected_revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=removal_response,
            ),
        )
        committed = self.idempotency.lookup(scope, str(idempotency_key), payload)
        assert committed is not None and committed.response is not None
        return committed.response

    def campaign_rules_explain(
        self,
        campaign_id: str,
        event: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        """Explain the exact lock, fingerprint, and source-cited mechanics used for settlement."""
        self.access.require_campaign(campaign_id, principal_id)
        effective = self.rule_packs.effective_ruleset(campaign_id, branch_id=branch_id)
        context = self.effective_rule_context(campaign_id, branch_id=branch_id)
        mechanics = [
            _support.asdict(item)
            for item in context.mechanics
            if event is None or item.event == event
        ]
        return {
            "campaign_id": campaign_id,
            "branch_id": effective.branch_id,
            "fingerprint": context.fingerprint,
            "core_pack": {
                "id": context.core_pack.id,
                "version": context.core_pack.version,
                "edition": context.core_pack.edition,
                "fingerprint": context.core_pack.fingerprint,
            },
            "core_boundaries": [_support.asdict(item) for item in context.core_pack.boundaries],
            "lock": list(effective.lock),
            "mechanics": mechanics,
            "coverage": sorted({item.event for item in context.mechanics}),
        }

    def campaign_rule_receipts(
        self,
        campaign_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        mechanic_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read immutable historical rule evidence for committed settlements."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        resolved_branch_id = self.readable_branch(campaign_id, branch_id, principal_id)
        return [
            _support.asdict(item)
            for item in self.rule_receipts.list(
                campaign_id,
                branch_id=resolved_branch_id,
                mechanic_id=mechanic_id,
                limit=limit,
            )
        ]

    def available_content_artifacts(
        self, campaign_id: str, *, kind: str | None = None, branch_id: str | None = None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        profile = self.rule_profiles.get(campaign_id)
        values: list[tuple[str, str, dict[str, Any]]] = []
        if profile and profile.edition == "2014":
            try:
                core = self.rule_packs.get_version(
                    _support.CORE_CONTENT_PACK_ID, _support.CORE_CONTENT_PACK_VERSION
                )
            except LookupError:
                # A headless server may intentionally run without the bundled
                # skill repository. Enabled user packs must remain usable.
                core = None
            if core is not None:
                values.extend((core.pack_id, core.version, dict(item)) for item in core.artifacts)
            try:
                standard = self.rule_packs.get_version(
                    _support.STANDARD_2014_CONTENT_PACK_ID,
                    _support.STANDARD_2014_CONTENT_PACK_VERSION,
                )
            except LookupError:
                standard = None
            if standard is not None:
                values.extend(
                    (standard.pack_id, standard.version, dict(item)) for item in standard.artifacts
                )
            try:
                presets2014 = self.rule_packs.get_version(
                    _support.SRD2014_PRESET_PACK_ID,
                    _support.SRD2014_PRESET_PACK_VERSION,
                )
            except LookupError:
                presets2014 = None
            if presets2014 is not None:
                values.extend(
                    (presets2014.pack_id, presets2014.version, dict(item))
                    for item in presets2014.artifacts
                )
        elif profile and profile.edition == "2024":
            try:
                core2024 = self.rule_packs.get_version(
                    _support.CORE_2024_CONTENT_PACK_ID,
                    _support.CORE_2024_CONTENT_PACK_VERSION,
                )
            except LookupError:
                core2024 = None
            if core2024 is not None:
                values.extend(
                    (core2024.pack_id, core2024.version, dict(item)) for item in core2024.artifacts
                )
            try:
                presets2024 = self.rule_packs.get_version(
                    _support.SRD2024_PRESET_PACK_ID,
                    _support.SRD2024_PRESET_PACK_VERSION,
                )
            except LookupError:
                presets2024 = None
            if presets2024 is not None:
                values.extend(
                    (presets2024.pack_id, presets2024.version, dict(item))
                    for item in presets2024.artifacts
                )
        for activation in self.rule_packs.activations(campaign_id, branch_id=branch_id):
            if not activation.enabled:
                continue
            pack = self.rule_packs.get_version(activation.pack_id, activation.version)
            values.extend((pack.pack_id, pack.version, dict(item)) for item in pack.artifacts)
        unique: list[tuple[str, str, dict[str, Any]]] = []
        seen: set[tuple[str, str, str]] = set()
        for pack_id, version, artifact in values:
            identity = (pack_id, version, str(artifact.get("id") or ""))
            if identity in seen:
                continue
            seen.add(identity)
            if kind is None or artifact.get("kind") == kind:
                unique.append((pack_id, version, artifact))
        return unique

    def source_scoped_content_matches(
        self,
        matches: list[tuple[str, str, dict[str, Any]]],
        *,
        source_pack_id: str,
        source_pack_version: str,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """Resolve a referenced card inside the granting pack's dependency scope.

        A source pack's own reprint is authoritative.  Otherwise, an explicitly
        pinned non-core dependency is authoritative over globally available
        bundled content and unrelated active addons.  Multiple matches inside
        the same declared scope remain ambiguous and fail closed at the caller.
        """

        local = [
            item for item in matches if item[0] == source_pack_id and item[1] == source_pack_version
        ]
        if local:
            return local
        try:
            source_pack = self.rule_packs.get_version(
                source_pack_id,
                source_pack_version,
            )
        except LookupError:
            return matches
        dependencies = {
            (
                str(item.get("id") or ""),
                str(item.get("version") or ""),
            )
            for item in source_pack.manifest.get("dependencies", [])
            if isinstance(item, dict)
            and str(item.get("id") or "")
            and str(item.get("version") or "")
        }
        dependency_matches = [item for item in matches if (item[0], item[1]) in dependencies]
        built_in_pack_ids = {
            _support.CORE_CONTENT_PACK_ID,
            _support.CORE_2024_CONTENT_PACK_ID,
            _support.STANDARD_2014_CONTENT_PACK_ID,
            _support.SRD2014_PRESET_PACK_ID,
            _support.SRD2024_PRESET_PACK_ID,
        }
        reviewed_dependency_matches = [
            item for item in dependency_matches if item[0] not in built_in_pack_ids
        ]
        return reviewed_dependency_matches or dependency_matches or matches

    def selected_progression_content_source(
        self,
        sheet: dict[str, Any],
        candidates: list[tuple[str, str, dict[str, Any]]],
        *,
        class_name: str,
        subclass_name: str = "",
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Resolve recorded class/subclass identity, never infer a printing from a name."""
        kind = "subclass" if subclass_name else "class"
        selected: dict[tuple[str, str, str], tuple[str, str, dict[str, Any]]] = {}
        by_identity = {
            (pack_id, version, str(artifact.get("id") or "")): (pack_id, version, artifact)
            for pack_id, version, artifact in candidates
        }
        for record in sheet.get("content", {}).get("selections", []):
            if record.get("kind") != kind:
                continue
            identity = (
                str(record.get("pack_id") or ""),
                str(record.get("pack_version") or ""),
                str(record.get("artifact_id") or ""),
            )
            source = by_identity.get(identity)
            if source is None or source[2].get("kind") != kind:
                raise _support.RulesetUnavailableError("selected progression source is unavailable")
            source = (
                source[0],
                source[1],
                self.reviewed_official_runtime_artifact(source[0], source[1], source[2]),
            )
            source_card = dict(source[2].get("card") or {})
            source_class = str(source_card.get("class_name" if subclass_name else "name") or "")
            if source_class.casefold() != class_name.casefold():
                continue
            if subclass_name and str(source_card.get("name") or "").casefold() != (
                subclass_name.casefold()
            ):
                continue
            selected[identity] = source
        if len(selected) > 1:
            raise _support.RulesetUnavailableError("selected progression source is ambiguous")
        return next(iter(selected.values()), None)

    def content_runtime_context(
        self,
        pack_id: str,
        version: str,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        """Return bounded source and settlement context for an external Agent."""

        catalog_review = dict(artifact.get("catalog_review") or {})
        context = {
            "artifact_id": str(artifact.get("id") or ""),
            "kind": str(artifact.get("kind") or ""),
            "pack_id": pack_id,
            "pack_version": version,
            "content_hash": _support.content_fingerprint(artifact),
            "catalog_review_hash": str(catalog_review.get("reviewed_content_hash") or ""),
            "application_state": str(artifact.get("application_state") or "selection_ready"),
            "execution_state": str(artifact.get("execution_state") or ""),
            "semantic_resolution": _support.deepcopy(
                dict(artifact.get("semantic_resolution") or {})
            ),
            "selection_contract": _support.deepcopy(dict(artifact.get("selection_contract") or {})),
            "card": _support.deepcopy(dict(artifact.get("card") or {})),
            "rule_clauses": _support.deepcopy(list(artifact.get("rule_clauses") or [])),
            "resolution_plan": _support.deepcopy(artifact.get("resolution_plan")),
            "resolution_plans": _support.deepcopy(list(artifact.get("resolution_plans") or [])),
            "rule_refs": list(artifact.get("rule_refs") or []),
            "source_citations": _support.deepcopy(list(artifact.get("source_citations") or [])),
        }
        item_profile = _support.official_item_profile(pack_id, artifact)
        if item_profile is not None:
            context["executable_item_profile"] = _support.deepcopy(item_profile)
        return context

    def materialize_species_features(
        self,
        sheet: dict[str, Any],
        *,
        features: Any,
        species_name: str,
        species_artifact_id: str,
        pack_id: str,
        pack_version: str,
        rule_refs: list[str],
        mechanic_refs: list[str],
        maximum_level: int,
        choices: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Append only currently unlocked embedded species features."""

        if not isinstance(features, list):
            raise ValueError("species features must be an array")
        present_ids = {
            str(item.get("id") or "") for item in sheet.get("content", {}).get("features", [])
        }
        unlocked: list[dict[str, Any]] = []
        for index, raw_feature in enumerate(features):
            if not isinstance(raw_feature, dict):
                raise ValueError(f"species features[{index}] must be an object")
            minimum_level = raw_feature.get("minimum_level", 1)
            if (
                isinstance(minimum_level, bool)
                or not isinstance(minimum_level, int)
                or not 1 <= minimum_level <= 20
            ):
                raise ValueError(
                    f"species features[{index}].minimum_level must be an integer from 1 to 20"
                )
            if minimum_level > maximum_level:
                continue
            feature_card = _support.deepcopy(raw_feature)
            feature_card.pop("minimum_level", None)
            feature_name = " ".join(str(feature_card.get("name") or "").split())
            if not feature_name:
                raise ValueError(f"species features[{index}].name must not be empty")
            feature_id = str(feature_card.get("id") or "").strip() or (
                f"{species_artifact_id}.feature.{_support.ascii_slug(feature_name)}"
            )
            if feature_id in present_ids:
                continue
            feature_card.update(
                id=feature_id,
                name=feature_name,
                source_key=str(feature_card.get("source_key") or species_name),
                pack_id=pack_id,
                pack_version=pack_version,
                rule_refs=list(
                    dict.fromkeys(
                        [
                            *list(feature_card.get("rule_refs") or []),
                            *list(rule_refs),
                        ]
                    )
                ),
                mechanic_refs=list(
                    feature_card.get("mechanic_refs")
                    if "mechanic_refs" in feature_card
                    else mechanic_refs
                ),
            )
            feature_card.setdefault("activation", {"type": "passive"})
            if choices and any(choices.values()):
                feature_card["choices"] = {
                    **_support.deepcopy(choices),
                    **dict(feature_card.get("choices") or {}),
                }
            sheet["content"]["features"].append(feature_card)
            present_ids.add(feature_id)
            unlocked.append(
                {
                    "artifact_id": feature_id,
                    "name": feature_name,
                    "minimum_level": minimum_level,
                    "source_species": species_name,
                }
            )
        return unlocked

    def level_advancement_content_context(
        self,
        campaign_id: str,
        sheet: dict[str, Any],
        *,
        class_name: str,
        new_level: int,
        branch_id: str,
        include_overdue_grants: bool = False,
    ) -> dict[str, Any]:
        """Resolve source-bound per-level modifiers and post-level catalog work."""
        candidates = self.available_content_artifacts(campaign_id, branch_id=branch_id)

        def exact_recorded_artifact(record: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
            artifact_id = str(record.get("artifact_id") or record.get("id") or "")
            pack_id = str(record.get("pack_id") or "")
            version = str(record.get("pack_version") or "")
            if not artifact_id or not pack_id or not version:
                raise ValueError(
                    "level-affecting content must record artifact id, pack id, and pack version"
                )
            try:
                pack = self.rule_packs.get_version(pack_id, version)
            except LookupError as error:
                raise _support.RulesetUnavailableError(
                    f"recorded content pack is unavailable: {pack_id}@{version}"
                ) from error
            artifact = next(
                (item for item in pack.artifacts if str(item.get("id") or "") == artifact_id),
                None,
            )
            if artifact is None and artifact_id in {
                f"{background_id}.feature.watchers-eye"
                for background_id in _support.SCAG_WATCHERS_EYE_BACKGROUND_IDS
            }:
                # This native narrative feature is derived from its background,
                # not stored as a top-level artifact. Reuse execution's exact
                # archive/selection checks and never trust sheet HP grants.
                _, binding = self.executable_watchers_eye_feature(
                    sheet,
                    artifact_id,
                    campaign_id=campaign_id,
                    branch_id=branch_id,
                )
                artifact = {
                    "id": artifact_id,
                    "kind": "feature",
                    "card": self.watchers_eye_feature_card(binding),
                }
            if artifact is None:
                for parent in pack.artifacts:
                    parent_id = str(parent.get("id") or "").strip()
                    embedded_features = dict(
                        dict(parent.get("card") or {}).get("grants") or {}
                    ).get("features", [])
                    for raw_feature in embedded_features:
                        feature = dict(raw_feature)
                        feature_name = " ".join(str(feature.get("name") or "").split())
                        embedded_id = str(feature.get("id") or "").strip() or (
                            f"{parent_id}.feature.{_support.ascii_slug(feature_name)}"
                        )
                        if embedded_id == artifact_id:
                            artifact = {
                                "id": artifact_id,
                                "kind": "feature",
                                "card": {**feature, "id": embedded_id},
                            }
                            break
                    if artifact is not None:
                        break
            if artifact is None:
                raise _support.RulesetUnavailableError(
                    f"recorded artifact is unavailable: {artifact_id} in {pack_id}@{version}"
                )
            return pack_id, version, dict(artifact)

        hp_per_level_bonus = 0
        hp_bonus_sources: list[dict[str, Any]] = []
        for selection in sheet.get("content", {}).get("selections", []):
            artifact_id = str(selection.get("artifact_id") or "")
            if not artifact_id:
                continue
            pack_id, version, artifact = exact_recorded_artifact(selection)
            card = dict(artifact.get("card") or {})
            grants = dict(card.get("grants") or {})
            amount = int(grants.get("hp_per_level", 0) or 0)
            if amount:
                hp_per_level_bonus += amount
                hp_bonus_sources.append(
                    {
                        "artifact_id": artifact_id,
                        "pack_id": pack_id,
                        "pack_version": version,
                        "amount": amount,
                        "scope": "character_level",
                    }
                )
        feature_records = list(sheet.get("content", {}).get("features", []))
        present_features = {
            str(item.get("id") or ""): item for item in feature_records if item.get("id")
        }
        for feature in feature_records:
            artifact_id = str(feature.get("id") or "")
            if not artifact_id or not feature.get("pack_id") or not feature.get("pack_version"):
                continue
            pack_id, version, artifact = exact_recorded_artifact(feature)
            card = dict(artifact.get("card") or {})
            grants = dict(card.get("mechanical_grants") or {})
            amount = int(grants.get("hp_per_level", 0) or 0)
            if str(card.get("class_name") or "").casefold() == class_name.casefold():
                amount += int(grants.get("hp_per_class_level", 0) or 0)
            if amount:
                hp_per_level_bonus += amount
                hp_bonus_sources.append(
                    {
                        "artifact_id": artifact_id,
                        "pack_id": pack_id,
                        "pack_version": version,
                        "amount": amount,
                        "scope": "class_level",
                    }
                )

        target_class = next(
            item
            for item in sheet["progression"]["classes"]
            if str(item.get("name") or "").casefold() == class_name.casefold()
        )
        subclass_name = str(target_class.get("subclass") or "")
        feature_options: list[dict[str, Any]] = []
        subclass_options: list[dict[str, Any]] = []
        # Only reuse verification inside this read-only operation, never across
        # requests or after a write. A full archive need not be rebuilt per feature.
        feature_sources: dict[tuple[str, str], tuple[str, str, dict[str, Any]] | None] = {}
        for pack_id, version, artifact in candidates:
            if str(artifact.get("application_state") or "selection_ready") != "selection_ready":
                continue
            artifact_id = str(artifact.get("id") or "")
            card = dict(artifact.get("card") or {})
            kind = str(artifact.get("kind") or "")
            declared_class = str(card.get("class_name") or "")
            minimum_level = int(card.get("minimum_level", 1) or 1)
            if declared_class.casefold() != class_name.casefold() or minimum_level > new_level:
                continue
            repeatable_levels = {
                int(value)
                for value in card.get("repeatable_selection_levels", [])
                if int(value) > 0
            }
            recorded_grant_levels = {
                int(item.get("level", 0) or 0)
                for item in present_features.get(artifact_id, {}).get("advancement_grants", [])
            }
            repeat_due = new_level in repeatable_levels and new_level not in recorded_grant_levels
            due_grant_level = new_level if repeat_due else None
            if include_overdue_grants and repeatable_levels:
                outstanding_levels = sorted(
                    level
                    for level in {minimum_level, *repeatable_levels}
                    if level <= new_level and level not in recorded_grant_levels
                )
                due_grant_level = next(iter(outstanding_levels), None)
                repeat_due = due_grant_level is not None
            if kind == "feature" and (artifact_id not in present_features or repeat_due):
                if str(card.get("feature_subtype") or "") == "selectable_option":
                    continue
                if str(card.get("name") or "").casefold() == "unarmored defense" and any(
                    str(item.get("name") or "").casefold() == "unarmored defense"
                    for item in feature_records
                ):
                    continue
                declared_subclass = str(card.get("subclass_name") or "")
                if declared_subclass and declared_subclass.casefold() != subclass_name.casefold():
                    continue
                source_matches = self.progression_feature_source_matches(
                    sheet,
                    candidates,
                    artifact,
                    source_cache=feature_sources,
                )
                if not any(
                    item[0] == pack_id
                    and item[1] == version
                    and str(item[2].get("id") or "") == artifact_id
                    for item in source_matches
                ):
                    continue
                requirements_by_level = dict(card.get("selection_requirements_by_level") or {})
                selection_requirements = _support.deepcopy(
                    dict(
                        requirements_by_level.get(str(due_grant_level or new_level))
                        or card.get("selection_requirements")
                        or {}
                    )
                )
                feature_options.append(
                    {
                        "artifact_id": artifact_id,
                        "name": str(card.get("name") or artifact_id),
                        "minimum_level": minimum_level,
                        "class_name": declared_class,
                        "subclass_name": declared_subclass,
                        "selection_requirements": selection_requirements,
                        "grant_level": due_grant_level,
                        "pack_id": pack_id,
                        "pack_version": version,
                        "rule_refs": list(artifact.get("rule_refs") or []),
                        "_provides_resources": list(
                            dict(dict(card.get("mechanical_grants") or {}).get("resources") or {})
                        ),
                        "_requires_resource": str(card.get("resource_key") or ""),
                    }
                )
            if kind == "subclass" and not subclass_name:
                subclass_options.append(
                    {
                        "artifact_id": artifact_id,
                        "name": str(card.get("name") or artifact_id),
                        "minimum_level": minimum_level,
                        "pack_id": pack_id,
                        "pack_version": version,
                        "rule_refs": list(artifact.get("rule_refs") or []),
                    }
                )
        ordered_features: list[dict[str, Any]] = []
        pending_features = sorted(
            feature_options,
            key=lambda item: (
                item["minimum_level"],
                item["name"],
                item["artifact_id"],
            ),
        )
        provided_resources = {str(key) for key in dict(sheet.get("resources") or {})}
        while pending_features:
            pending_providers = {
                str(resource_key)
                for item in pending_features
                for resource_key in item.get("_provides_resources", [])
            }
            ready_index = next(
                (
                    index
                    for index, item in enumerate(pending_features)
                    if not str(item.get("_requires_resource") or "")
                    or str(item["_requires_resource"]) in provided_resources
                    or str(item["_requires_resource"]) not in pending_providers
                ),
                0,
            )
            selected = pending_features.pop(ready_index)
            provided_resources.update(
                str(value) for value in selected.pop("_provides_resources", [])
            )
            selected.pop("_requires_resource", None)
            ordered_features.append(selected)
        return {
            "hp_per_level_bonus": hp_per_level_bonus,
            "hp_bonus_sources": hp_bonus_sources,
            "feature_options": ordered_features,
            "subclass_options": sorted(
                subclass_options, key=lambda item: (item["name"], item["artifact_id"])
            ),
        }

    def content_catalog_list(
        self,
        campaign_id: str,
        kind: str | None = None,
        query: str = "",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        branch_id: str | None = None,
        include_context: bool = False,
    ) -> list[dict[str, Any]]:
        """List core and enabled-extension character options from one uniform catalog."""
        self.access.require_campaign(campaign_id, principal_id)
        resolved_branch_id = self.readable_branch(campaign_id, branch_id, principal_id)
        lowered = query.casefold().strip()
        if include_context and not lowered:
            raise ValueError("include_context requires an exact artifact id query")
        result = []
        catalog_candidates = self.available_content_artifacts(
            campaign_id, kind=kind, branch_id=resolved_branch_id
        )
        for pack_id, version, artifact in catalog_candidates:
            card = dict(artifact.get("card") or {})
            name = str(card.get("name") or artifact["id"])
            if include_context and lowered != str(artifact["id"]).casefold():
                continue
            if (
                lowered
                and lowered not in name.casefold()
                and lowered not in str(artifact["id"]).casefold()
            ):
                continue
            artifact_kind = str(artifact.get("kind") or "")
            selection_requirements: dict[str, Any] = {}
            if artifact_kind == "spell":
                selection_requirements = {
                    "fields": ["source_class", "method"],
                    "level": int(card.get("level", 0) or 0),
                    "eligible_classes": list(card.get("classes") or []),
                    "methods": ["known"] if int(card.get("level", 0) or 0) == 0 else [
                        "known",
                        "spellbook",
                        "spellbook_copy",
                        "class_prepared",
                    ],
                    "spellbook_copy_fields": [
                        "source_owner",
                        "source_item_id",
                        "payment_owner",
                        "payment",
                    ],
                }
            elif artifact_kind == "class":
                definition = dict(card.get("class_definition") or {})
                skill_count = int(definition.get("skill_choice_count", 0) or 0)
                tool_count = int(definition.get("tool_choice_count", 0) or 0)
                fields = []
                if skill_count:
                    fields.append("skills")
                if tool_count:
                    fields.append("tools")
                starting_equipment = definition.get("starting_equipment")
                if starting_equipment is not None:
                    starting_equipment = _support.normalize_starting_equipment_contract(
                        starting_equipment
                    )
                    fields.append("starting_equipment")
                selection_requirements = {
                    "fields": fields,
                    "skill_choice_count": skill_count,
                    "skill_options": list(definition.get("skill_options") or []),
                    "tool_choice_count": tool_count,
                    "tool_options": list(definition.get("tool_options") or []),
                    "duplicate_replacement_fields": [
                        "skill_replacements",
                        "tool_replacements",
                    ],
                    "skill_replacement_options": sorted(_support.SKILL_ABILITIES),
                    "tool_replacement_options": sorted(
                        _support._reviewed_tool_options(catalog_candidates).values(),
                        key=str.casefold,
                    ),
                    **(
                        {"starting_equipment": starting_equipment}
                        if starting_equipment is not None
                        else {}
                    ),
                }
            elif artifact_kind == "subclass":
                subclass_card_requirements = dict(card.get("selection_requirements") or {})
                subclass_fields = ["target_class_name"]
                if subclass_card_requirements.get("field"):
                    subclass_fields.append(str(subclass_card_requirements["field"]))
                if str(artifact.get("id") or "") == _support.SCAG_BLADE_SINGING_SUBCLASS_ID:
                    subclass_fields.append("species_prerequisite_override")
                selection_requirements = {
                    "fields": list(dict.fromkeys(subclass_fields)),
                    "class_name": str(card.get("class_name") or ""),
                    "minimum_level": int(card.get("minimum_level", 1) or 1),
                    **(
                        {"selection_contract": subclass_card_requirements}
                        if subclass_card_requirements
                        else {}
                    ),
                }
            elif artifact_kind == "background":
                grants = dict(card.get("background_grants") or {})
                choices = dict(grants.get("choices") or {})
                ability_options = list(choices.get("ability_score_options") or [])
                skill_choice_count = int(choices.get("skill_choice_count", 0) or 0)
                tool_choice_count = int(choices.get("tool_choice_count", 0) or 0)
                fields = []
                if choices.get("language_count"):
                    fields.append("languages")
                if ability_options:
                    fields.append("ability_score_increases")
                if skill_choice_count:
                    fields.append("skills")
                if tool_choice_count:
                    fields.append("tools")
                equipment_packages = _support.deepcopy(
                    dict(choices.get("equipment_packages") or {})
                )
                if equipment_packages:
                    fields.append("equipment_package")
                if choices.get("origin_feat_name") == "Magic Initiate":
                    fields.append("origin_feat_selection")
                campaign = self.campaigns.get(campaign_id)
                language_catalog = _support._campaign_language_options(
                    dict(campaign.settings or {})
                )
                tool_replacement_options = sorted(
                    _support._reviewed_tool_options(catalog_candidates).values(), key=str.casefold
                )
                selection_requirements = {
                    "fields": fields,
                    "language_count": int(choices.get("language_count", 0) or 0),
                    "language_options": list(choices.get("language_options") or []),
                    "allow_any_language": choices.get("allow_any_language") is True,
                    "fixed_languages": list(grants.get("languages") or []),
                    "spell_list_expansion": list(grants.get("spell_list_expansion") or []),
                    "skill_proficiencies": list(
                        card.get("skill_proficiencies") or grants.get("skills") or []
                    ),
                    "skill_choice_count": skill_choice_count,
                    "skill_options": list(choices.get("skill_options") or []),
                    "ability_score_options": ability_options,
                    "allowed_ability_score_distributions": _support.deepcopy(
                        list(choices.get("allowed_ability_score_distributions") or [])
                    ),
                    "maximum_ability_score": int(choices.get("maximum_ability_score", 20) or 20),
                    "tool_choice_count": tool_choice_count,
                    "tool_options": list(choices.get("tool_options") or []),
                    "tool_option_groups": _support.deepcopy(
                        list(choices.get("tool_option_groups") or [])
                    ),
                    "fixed_tools": list(grants.get("tools") or []),
                    "fixed_equipment": _support.deepcopy(dict(grants.get("equipment") or {})),
                    "equipment_package_options": sorted(equipment_packages),
                    "equipment_packages": equipment_packages,
                    "origin_feat_name": str(choices.get("origin_feat_name") or ""),
                    "origin_feat_preset": _support.deepcopy(
                        dict(choices.get("origin_feat_preset") or {})
                    ),
                    "duplicate_replacement_fields": [
                        "skill_replacements",
                        "tool_replacements",
                    ],
                    "skill_replacement_options": sorted(_support.SKILL_ABILITIES),
                    "tool_replacement_options": tool_replacement_options,
                    "allowed_language_catalog": sorted(language_catalog.values(), key=str.casefold),
                    "restricted_languages_requiring_dm_authorization": sorted(
                        _support.PHB2014_RESTRICTED_LANGUAGES, key=str.casefold
                    ),
                    "customizable": self.campaign_rules_edition(campaign_id) == "2014",
                    "customization_fields": [
                        "custom_name",
                        "skills",
                        "skill_replacements",
                        "tools",
                        "tool_replacements",
                        "languages",
                        "language_authorization",
                        "custom_feature_artifact_id",
                        "equipment_mode",
                        "equipment_package",
                    ],
                    "custom_contract": {
                        "skill_count": 2,
                        "combined_tool_or_language_count": 2,
                        "equipment_modes": ["source", "starting_coin"],
                        "equipment_modes_are_mutually_exclusive": True,
                    },
                }
            elif artifact_kind == "feat":
                requirements = _support.deepcopy(dict(card.get("selection_requirements") or {}))
                selection_requirements = {
                    "fields": [requirements["field"]] if requirements.get("field") else [],
                    "prerequisites": _support.deepcopy(list(card.get("prerequisites") or [])),
                    "category": str(card.get("category") or ""),
                    "repeatable": bool(card.get("repeatable", False)),
                    **requirements,
                }
            elif artifact_kind == "feature":
                requirements = _support.deepcopy(dict(card.get("selection_requirements") or {}))
                grants = _support.deepcopy(dict(card.get("mechanical_grants") or {}))
                replacement_options = _support.deepcopy(
                    dict(grants.get("tool_proficiency_replacement_options") or {})
                )
                fields = [requirements["field"]] if requirements.get("field") else []
                if replacement_options:
                    fields.append("tool_replacements")
                selection_requirements = {
                    "fields": fields,
                    "class_name": str(card.get("class_name") or ""),
                    "subclass_name": str(card.get("subclass_name") or ""),
                    "minimum_level": int(card.get("minimum_level", 1) or 1),
                    "feature_subtype": str(card.get("feature_subtype") or ""),
                    "unlock_levels": [int(value) for value in card.get("unlock_levels", [])],
                    "repeatable_selection_levels": [
                        int(value) for value in card.get("repeatable_selection_levels", [])
                    ],
                    "selection_requirements_by_level": _support.deepcopy(
                        dict(card.get("selection_requirements_by_level") or {})
                    ),
                    "tool_proficiency_replacement_options": replacement_options,
                    **requirements,
                }
            elif artifact_kind == "species":
                grants = dict(card.get("grants") or {})
                fields = []
                if int(grants.get("language_choice_count", 0) or 0):
                    fields.append("languages")
                if int(grants.get("skill_choice_count", 0) or 0):
                    fields.append("skills")
                if int(grants.get("tool_choice_count", 0) or 0):
                    fields.append("tools")
                if list(grants.get("proficiency_choice_groups") or []):
                    fields.append("proficiency_choices")
                if list(grants.get("narrative_choice_groups") or []):
                    fields.append("feature_choices")
                if int(grants.get("tool_expertise_choice_count", 0) or 0):
                    fields.append("tool_expertise")
                if list(grants.get("size_options") or []):
                    fields.append("size")
                if int(dict(grants.get("ability_choice") or {}).get("count", 0) or 0):
                    fields.append("abilities")
                if grants.get("cantrip_choice"):
                    fields.append("cantrip_artifact_id")
                if grants.get("feat_choice"):
                    fields.append("feat_selection")
                if grants.get("damage_affinity_choice"):
                    fields.append("damage_affinity")
                selection_requirements = {
                    "fields": fields,
                    "base_species": str(card.get("base_species") or card.get("name") or ""),
                    "language_count": int(grants.get("language_choice_count", 0) or 0),
                    "language_options": list(grants.get("language_options") or []),
                    "allow_any_language": grants.get("allow_any_language") is True,
                    "skill_count": int(grants.get("skill_choice_count", 0) or 0),
                    "skill_options": list(grants.get("skill_options") or []),
                    "allow_any_skill": grants.get("allow_any_skill") is True,
                    "tool_count": int(grants.get("tool_choice_count", 0) or 0),
                    "tool_options": list(
                        grants.get("tool_options") or grants.get("tool_choices") or []
                    ),
                    "proficiency_choice_groups": _support.deepcopy(
                        list(grants.get("proficiency_choice_groups") or [])
                    ),
                    "narrative_choice_groups": _support.deepcopy(
                        list(grants.get("narrative_choice_groups") or [])
                    ),
                    "tool_expertise_count": int(grants.get("tool_expertise_choice_count", 0) or 0),
                    "tool_expertise_options": list(grants.get("tool_expertise_options") or []),
                    "allow_any_proficient_tool_expertise": grants.get(
                        "allow_any_proficient_tool_expertise"
                    )
                    is True,
                    "size_options": list(grants.get("size_options") or []),
                    "ability_choice": _support.deepcopy(dict(grants.get("ability_choice") or {})),
                    "cantrip_choice": _support.deepcopy(grants.get("cantrip_choice")),
                    "feat_choice": _support.deepcopy(grants.get("feat_choice")),
                    "damage_affinity_choice": _support.deepcopy(
                        grants.get("damage_affinity_choice")
                    ),
                }
            elif artifact_kind == "statblock":
                dependent_template = _support.deepcopy(
                    dict(card.get("dependent_actor_template") or {})
                )
                if dependent_template:
                    solution = dict(dependent_template.get("solution") or {})
                    runtime_errors = _support.dependent_actor_template_solution_errors(
                        dependent_template
                    )
                    try:
                        _support.dependent_actor_lifecycle_policy(dependent_template)
                    except ValueError as exc:
                        runtime_errors.append(str(exc))
                    selection_requirements = {
                        "fields": [
                            "owner_character_id",
                            "name",
                            "character_type",
                            "player_name",
                            "summary",
                            "notes",
                            *(
                                ["owner_class_name"]
                                if "owner_class_level"
                                in set(solution.get("numeric_parameters") or [])
                                else []
                            ),
                            *(
                                ["casting_slot_level"]
                                if "casting_slot_level"
                                in set(solution.get("numeric_parameters") or [])
                                else []
                            ),
                            *(["template_variant"] if solution.get("variant_options") else []),
                        ],
                        "creation_tool": "addon_actor_instantiate",
                        "source_statblock_name": name,
                        "source_resolution": "reviewed_addon_artifact",
                        "normalization_authority": "engine",
                        "runtime_ready": not runtime_errors,
                        "dependent_actor_template": dependent_template,
                    }
                else:
                    selection_requirements = {
                        "fields": [
                            "source_id",
                            "chunk_ids",
                            "source_statblock_name",
                        ],
                        "creation_tool": "character_create_from",
                        "creation_mode": "statblock",
                        "source_statblock_name": name,
                        "source_resolution": "source_citations",
                        "build_time_actor_card_required": (
                            str(artifact.get("application_state") or "") == "catalog_only"
                        ),
                        "normalization_authority": "engine",
                    }
            entry = {
                "id": artifact["id"],
                "kind": artifact_kind,
                "name": name,
                "pack_id": pack_id,
                "pack_version": version,
                "rule_refs": list(artifact.get("rule_refs") or []),
                "mechanic_refs": list(artifact.get("mechanic_refs") or []),
                "source_citations": _support.deepcopy(list(artifact.get("source_citations") or [])),
                "selection_requirements": selection_requirements,
                "application_state": str(artifact.get("application_state") or "selection_ready"),
            }
            if include_context:
                entry["runtime_context"] = self.content_runtime_context(
                    pack_id,
                    version,
                    self.reviewed_official_runtime_artifact(pack_id, version, artifact),
                )
            result.append(entry)
        if include_context and len(result) != 1:
            raise LookupError(
                "exact content artifact is not available for this campaign; "
                "include_context=true requires query to be a full artifact id. "
                "Search the name with include_context=false first, then reuse a returned id."
            )
        return sorted(
            result,
            key=lambda item: (str(item["kind"]), str(item["name"]), str(item["id"])),
        )

    def _character_content_apply_v1(
        self,
        character_id: str,
        artifact_id: str,
        selection: dict[str, Any] | None = None,
        grant: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply a catalog option when its structured card has a safe character target."""
        current = self.characters.get(character_id)
        self.require_character_control(current, principal_id)
        self.require_outside_active_combat(current, "content selection")
        if current.campaign_id is None:
            raise ValueError("content selection requires a campaign-bound character")
        if expected_revision is None or not idempotency_key:
            raise ValueError(
                "expected_revision and idempotency_key are required for content selection"
            )
        campaign = self.campaigns.get(current.campaign_id)
        candidates = self.available_content_artifacts(current.campaign_id)
        matches = [item for item in candidates if item[2].get("id") == artifact_id]
        if not matches:
            raise LookupError("content artifact is not available for this campaign")
        if len(matches) != 1:
            raise _support.RulesetUnavailableError(
                "content artifact identity is ambiguous across active rule packs"
            )
        match = matches[0]
        pack_id, version, artifact = match
        artifact = self.reviewed_official_runtime_artifact(pack_id, version, artifact)
        match = (pack_id, version, artifact)
        application_state = str(artifact.get("application_state") or "selection_ready")
        if application_state != "selection_ready":
            return {
                **_support._ruling_status(
                    "pending_ruling",
                    "missing_or_conflicting_source_review",
                ),
                "reason": (
                    "catalog artifact is source-linked but not selection-ready; "
                    "complete reviewer validation before applying it to an actor"
                ),
            }
        kind = str(artifact.get("kind") or "")
        card = _support.deepcopy(dict(artifact.get("card") or {}))
        selection = _support.deepcopy(selection or {})
        if _support.TORTLE_NATURAL_ARMOR_AUTHORITY_KEY in selection:
            raise ValueError("official expansion authority is server-managed")
        if _support.BACKGROUND_AUTHORITY_SELECTION_KEY in selection:
            raise ValueError("background selection authority is server-managed")
        if _support.CLASS_EQUIPMENT_AUTHORITY_KEY in selection:
            raise ValueError("class starting-equipment authority is server-managed")
        item_profile = _support.official_item_profile(pack_id, artifact)
        if _support.is_bound_official_item_id(pack_id, artifact_id) and item_profile is None:
            return {
                **_support._ruling_status(
                    "pending_ruling",
                    "missing_or_conflicting_source_review",
                ),
                "reason": (
                    "official item identity is reserved for a reviewed executable profile, "
                    "but its current content fingerprint is not approved"
                ),
            }
        if item_profile is not None:
            qualification = str(item_profile.get("qualification") or "")
            progression = dict(current.sheet.get("progression") or {})
            species_tokens = {
                token
                for token in _support.re.findall(
                    r"[a-z0-9_]+",
                    str(progression.get("species") or "").casefold(),
                )
            }
            anatomy = dict(dict(current.sheet.get("traits") or {}).get("anatomy") or {})
            if qualification == "warforged" and "warforged" not in species_tokens:
                raise ValueError("Armblade requires a Warforged character")
            if qualification == "missing_hand_or_arm" and not (
                int(anatomy.get("functional_arms", 2) or 0) < 2
                or int(anatomy.get("functional_hands", 2) or 0) < 2
            ):
                raise ValueError("Arcane Propulsion Arm requires a character missing a hand or arm")
        armblade_base_template: dict[str, Any] | None = None
        armblade_base_source: dict[str, Any] | None = None
        special_item_selection = item_profile is not None and artifact_id == _support.ARMBLADE_ID
        if special_item_selection:
            if set(selection) != {"base_weapon_artifact_id"}:
                return {
                    "status": "pending_choice",
                    "reason": (
                        "Armblade requires one reviewed one-handed melee weapon "
                        "to define its source-bound base profile"
                    ),
                    "selection_requirements": {
                        "base_weapon_artifact_id": {
                            "kind": "item",
                            "pack_id": _support.CORE_CONTENT_PACK_ID,
                            "artifact_kind": "weapon",
                            "attack_type": "melee",
                            "two_handed": False,
                        }
                    },
                }
            base_weapon_artifact_id = str(selection.get("base_weapon_artifact_id") or "")
            base_matches = [
                item
                for item in candidates
                if item[2].get("id") == base_weapon_artifact_id and item[2].get("kind") == "item"
            ]
            if len(base_matches) != 1:
                raise ValueError("Armblade base weapon must name one active weapon artifact")
            base_pack_id, base_version, base_artifact = base_matches[0]
            base_artifact = self.reviewed_official_runtime_artifact(
                base_pack_id, base_version, base_artifact
            )
            if base_pack_id != _support.CORE_CONTENT_PACK_ID:
                raise ValueError(
                    "Armblade base weapon must be an active SRD 2014 core weapon artifact"
                )
            if str(base_artifact.get("application_state") or "selection_ready") != (
                "selection_ready"
            ):
                raise _support.RulesetUnavailableError(
                    "Armblade base weapon must be a selection-ready reviewed artifact"
                )
            if base_artifact.get("selection_contract") is not None:
                base_contract_errors = _support.selection_input_errors(base_artifact, {})
                if base_contract_errors:
                    raise _support.RulesetUnavailableError(
                        "Armblade base weapon has no reviewed executable selection contract"
                    )
            base_card = dict(base_artifact.get("card") or {})
            base_template = base_card.get("inventory_template")
            base_mechanics = dict(dict(base_template or {}).get("mechanics") or {})
            base_properties = {
                str(value).strip().casefold() for value in base_mechanics.get("properties", [])
            }
            if (
                not isinstance(base_template, dict)
                or base_template.get("kind") != "weapon"
                or base_mechanics.get("attack_type") != "melee"
                or "two-handed" in base_properties
                or "two_handed" in base_properties
            ):
                raise ValueError(
                    "Armblade base weapon must be an executable one-handed melee weapon"
                )
            armblade_base_template = _support.deepcopy(base_template)
            armblade_base_source = {
                "artifact_id": base_weapon_artifact_id,
                "pack_id": base_pack_id,
                "pack_version": base_version,
                "content_hash": _support.content_fingerprint(base_artifact),
                "catalog_review_hash": str(
                    dict(base_artifact.get("catalog_review") or {}).get("reviewed_content_hash")
                    or ""
                ),
            }
        contract_required = artifact.get("selection_contract") is not None
        if contract_required and not special_item_selection:
            contract_errors = _support.selection_input_errors(artifact, selection)
            if contract_errors:
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": (
                        "catalog artifact does not have an exact, reviewed selection "
                        "contract for this request"
                    ),
                    "errors": contract_errors,
                }
        runtime_context = self.content_runtime_context(pack_id, version, artifact)
        if armblade_base_source is not None:
            executable_item_profile = dict(runtime_context.get("executable_item_profile") or {})
            executable_item_profile["base_weapon_source"] = _support.deepcopy(armblade_base_source)
            runtime_context["executable_item_profile"] = executable_item_profile
        tortle_archive_verification = (
            self.verified_reserved_official_rule_definition(pack_id, version)
            if artifact_id == _support.TORTLE_NATURAL_ARMOR_ARTIFACT_ID
            else None
        )
        tortle_natural_armor_authority = _support._verified_tortle_natural_armor_authority(
            pack_id=pack_id,
            pack_version=version,
            artifact_id=artifact_id,
            provenance=self.rule_packs.provenance(pack_id, version),
            archive_definition_verified=tortle_archive_verification is not None,
        )
        if tortle_natural_armor_authority is not None:
            authority_id = _support.uuid4().hex
            tortle_natural_armor_authority = {
                **tortle_natural_armor_authority,
                "authority_id": authority_id,
                "authorization": _support.sign_receipt(
                    {
                        "schema_version": 1,
                        "purpose": "official_content_authority",
                        "character_id": current.id,
                        "artifact_id": _support.TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
                        "package_id": _support.TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                        "package_version": _support.TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                        "package_checksum": _support.TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM,
                        "authority_id": authority_id,
                    },
                    self.content_authority_secret,
                ),
            }
        content_receipt: dict[str, Any] = {
            "ruleset_fingerprint": self.effective_rule_context(current.campaign_id).fingerprint,
            "mechanic_id": "dnd5e.character.content.apply.v1",
            "event": "character.content.apply",
            "artifact_id": artifact_id,
            "character_id": current.id,
            "pack_id": pack_id,
            "pack_version": version,
            "artifact_content_hash": _support.content_fingerprint(artifact),
            "selection": _support.deepcopy(selection),
            "rule_refs": list(artifact.get("rule_refs") or []),
        }
        if armblade_base_source is not None:
            content_receipt["base_weapon_source"] = _support.deepcopy(armblade_base_source)
        if item_profile is not None:
            content_receipt["reviewed_content_hash"] = str(item_profile["reviewed_content_hash"])
        raw_selection_contract = artifact.get("selection_contract")
        if isinstance(raw_selection_contract, dict) and not _support.selection_contract_errors(
            artifact
        ):
            selection_contract = dict(raw_selection_contract)
            if selection_contract.get("status") == "ready":
                content_receipt["mechanic_id"] = str(selection_contract["materializer"])
                content_receipt["reviewed_content_hash"] = str(
                    selection_contract["reviewed_content_hash"]
                )
        if tortle_natural_armor_authority is not None:
            content_receipt["content_authority_id"] = str(
                tortle_natural_armor_authority["authority_id"]
            )
        sheet = _support.deepcopy(current.sheet)
        replacing_selection: dict[str, Any] | None = None
        existing_selection: dict[str, Any] | None = None
        materialization_before: dict[str, Any] | None = None
        if kind in {"background", "species"}:
            selection_kind_records = [
                item
                for item in sheet["content"]["selections"]
                if str(item.get("kind") or "").casefold() == kind
            ]
            if len(selection_kind_records) > 1:
                raise ValueError(f"authoritative {kind} state requires one selection receipt")
            if selection_kind_records:
                existing_record = selection_kind_records[0]
                if selection.get("replace_existing") is False:
                    raise ValueError(f"character already has a different {kind}")
                existing_selection = existing_record
        phase = self.authoritative_phase(current.campaign_id)
        spellbook_copy: dict[str, Any] | None = None
        subclass_spell_grants: list[dict[str, Any]] = []
        feature_spell_grants: list[dict[str, Any]] = []
        species_feature_grants: list[dict[str, Any]] = []
        class_materialization: dict[str, Any] | None = None
        replacing_feature_selection = False
        spell_replacement: dict[str, Any] | None = None
        requested_method = str(selection.get("method") or "").strip().casefold()
        operation = (
            "character.spellbook.copy"
            if requested_method == "spellbook_copy"
            else "character.content.apply"
        )
        branch_id = self.require_current_branch(current.campaign_id, None)
        play_grant: dict[str, Any] | None = None
        if phase == _support.PROFILE_PLAY and requested_method != "spellbook_copy":
            if kind not in {"activity", "feat", "feature", "item", "spell"}:
                raise _support.CombatEngineError(
                    f"{kind or 'this content'} cannot be granted during play"
                )
            if not self.is_dm(current.campaign_id, principal_id):
                raise PermissionError("play-time content grants require the campaign DM")
            if not isinstance(grant, dict) or set(grant) != {"kind", "reason", "source_ref"}:
                raise _support.CombatEngineError(
                    "play-time content grants require exactly kind, reason, and source_ref"
                )
            grant_kind = str(grant.get("kind") or "").strip().casefold().replace("-", "_")
            reason = " ".join(str(grant.get("reason") or "").split())
            if grant_kind not in {"story_reward", "training", "module_reward"}:
                raise _support.CombatEngineError(
                    "play-time grant kind must be story_reward, training, or module_reward"
                )
            if not reason or len(reason) > 1000:
                raise _support.CombatEngineError(
                    "play-time grant reason must contain 1 to 1000 characters"
                )
            source_ref_value = grant.get("source_ref")
            artifact_rule_refs = {
                str(reference).strip()
                for reference in artifact.get("rule_refs") or []
                if str(reference).strip()
            }
            normalized_source_ref = str(source_ref_value or "").strip()
            if normalized_source_ref not in artifact_rule_refs:
                normalized_source_ref = self.advancement_source_ref(
                    current.campaign_id,
                    source_ref_value,
                    branch_id=branch_id,
                )
            play_grant = {
                "kind": grant_kind,
                "reason": reason,
                "source_ref": normalized_source_ref,
                "authorized_by": principal_id,
            }
        elif grant is not None:
            raise _support.CombatEngineError(
                "grant authorization is used only for non-spellbook content during play"
            )
        request_payload = {
            "operation": operation,
            "character_id": current.id,
            "artifact_id": artifact_id,
            "pack_id": pack_id,
            "version": version,
            "selection": _support.deepcopy(selection),
            **(
                {"grant": _support.deepcopy(play_grant)}
                if operation != "character.spellbook.copy"
                else {}
            ),
        }
        replay = self.replay_idempotent(
            f"character-write:{current.campaign_id}:{branch_id}:{principal_id}:{current.id}",
            idempotency_key,
            request_payload,
        )
        if replay is not None:
            return replay
        if current.revision != expected_revision:
            raise ValueError(
                "character revision conflict: "
                f"expected {expected_revision}, found {current.revision}"
            )
        if kind in {"background", "species"}:
            if existing_selection is not None:
                if str(existing_selection.get("artifact_id") or "") == artifact_id:
                    raise ValueError(f"content {kind} is already present: {artifact_id}")
                if kind == "background":
                    _support._require_authoritative_background_state(
                        sheet,
                        character_id=current.id,
                        secret=self.content_authority_secret,
                    )
                elif kind == "species":
                    _support._require_authoritative_species_state(
                        sheet,
                        character_id=current.id,
                        secret=self.content_authority_secret,
                    )
                _support._remove_content_projection(sheet, existing_selection, kind=kind)
                sheet["content"]["selections"] = [
                    item
                    for item in sheet["content"]["selections"]
                    if item is not existing_selection
                ]
                replacing_selection = existing_selection
            materialization_before = _support.deepcopy(sheet)
        provenance = {
            "id": artifact_id,
            "pack_id": pack_id,
            "pack_version": version,
            "rule_refs": list(artifact.get("rule_refs") or []),
            "mechanic_refs": list(artifact.get("mechanic_refs") or []),
        }

        def materialize_feature_spell(
            spell_match: tuple[str, str, dict[str, Any]],
            *,
            method: str,
            source_key: str,
            source_type: str = "feature",
            at_will: bool = False,
            allow_existing: bool = False,
        ) -> dict[str, Any]:
            spell_pack_id, spell_version, spell_artifact = spell_match
            spell_id = str(spell_artifact["id"])
            spell_card = next(
                (
                    item
                    for item in sheet["content"]["spells"]
                    if str(item.get("id") or "") == spell_id
                ),
                None,
            )
            if spell_card is not None and not allow_existing:
                raise ValueError("feature spell choice is already present")
            created = spell_card is None
            if spell_card is None:
                spell_card = _support._character_spell_card(dict(spell_artifact.get("card") or {}))
                spell_card["grant"] = {
                    "source_type": source_type,
                    "source_key": source_key,
                    "method": method,
                }
                spell_card.update(
                    id=spell_id,
                    pack_id=spell_pack_id,
                    pack_version=spell_version,
                    rule_refs=list(spell_artifact.get("rule_refs") or []),
                    mechanic_refs=list(spell_artifact.get("mechanic_refs") or []),
                )
                sheet["content"]["spells"].append(spell_card)
            access = spell_card.setdefault("access", {})
            if method in {"known", "mystic_arcanum"}:
                access["known"] = True
            if created:
                access["prepared"] = False
            if at_will:
                at_will_source = f"{method}:{source_key}"
                access["at_will_sources"] = list(
                    dict.fromkeys(
                        [
                            *list(access.get("at_will_sources") or []),
                            at_will_source,
                        ]
                    )
                )
                access["at_will"] = True
            feature_spell_grants.append(
                {
                    "artifact_id": spell_id,
                    "name": str(spell_card.get("name") or spell_id),
                    "method": method,
                    "source_key": source_key,
                    "at_will": at_will,
                    "created": created,
                }
            )
            return spell_card

        def apply_ability_score_increases(
            requirements: dict[str, Any],
            raw_increases: Any,
            *,
            source: str,
        ) -> dict[str, int]:
            if not isinstance(raw_increases, dict) or not raw_increases:
                raise ValueError(f"{source} ability_score_increases must be a non-empty object")
            normalized: dict[str, int] = {}
            allowed_abilities = {
                str(item).casefold() for item in requirements.get("ability_options", [])
            }
            for ability, amount in raw_increases.items():
                normalized_ability = str(ability).strip().casefold()
                if normalized_ability not in sheet["abilities"]:
                    raise ValueError(f"{source} names an unknown ability")
                if allowed_abilities and normalized_ability not in allowed_abilities:
                    raise ValueError(f"{source} names an ineligible ability")
                if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                    raise ValueError(f"{source} increases must be positive integers")
                normalized[normalized_ability] = amount
            distribution = sorted(normalized.values(), reverse=True)
            allowed = [
                sorted([int(amount) for amount in option], reverse=True)
                for option in requirements.get("allowed_distributions", [])
            ]
            if distribution not in allowed:
                raise ValueError(f"{source} increases do not match an allowed distribution")
            maximum_score = int(requirements.get("maximum_score", 20) or 20)
            previous_constitution = int(sheet["abilities"]["constitution"]["score"])
            for ability, amount in normalized.items():
                old_score = int(sheet["abilities"][ability]["score"])
                if old_score + amount > maximum_score:
                    raise ValueError(f"{source} exceeds its maximum ability score")
                sheet["abilities"][ability]["score"] = old_score + amount
            updated = _support.apply_constitution_score_hit_point_change(
                sheet,
                previous_score=previous_constitution,
                new_score=int(sheet["abilities"]["constitution"]["score"]),
                source=source,
            )
            sheet.clear()
            sheet.update(updated)
            return normalized

        def content_spell_match(
            grant: dict[str, Any],
            *,
            artifact_id: str | None = None,
        ) -> tuple[str, str, dict[str, Any]]:
            eligible_classes = {str(item).casefold() for item in grant.get("eligible_classes", [])}
            expected_level = int(grant.get("level", -1))
            expected_name = str(grant.get("name") or "").strip().casefold()
            matches = []
            for candidate in candidates:
                candidate_artifact = candidate[2]
                if candidate_artifact.get("kind") != "spell":
                    continue
                candidate_card = dict(candidate_artifact.get("card") or {})
                if artifact_id and str(candidate_artifact.get("id") or "") != artifact_id:
                    continue
                if expected_name and str(candidate_card.get("name") or "").casefold() != (
                    expected_name
                ):
                    continue
                if int(candidate_card.get("level", -1)) != expected_level:
                    continue
                candidate_classes = {
                    str(item).casefold() for item in candidate_card.get("classes", [])
                }
                if not eligible_classes.intersection(candidate_classes):
                    continue
                matches.append(candidate)
            matches = self.source_scoped_content_matches(
                matches,
                source_pack_id=pack_id,
                source_pack_version=version,
            )
            if len(matches) != 1:
                reference = artifact_id or str(grant.get("name") or "spell")
                raise _support.RulesetUnavailableError(
                    f"content spell grant {reference!r} must resolve to exactly one active artifact"
                )
            return matches[0]

        def materialize_content_spell(
            spell_match: tuple[str, str, dict[str, Any]],
            grant: dict[str, Any],
            *,
            source_type: str,
            source_key: str,
            resource_discriminator: str,
        ) -> dict[str, Any]:
            method = str(grant.get("method") or "")
            at_will = method == "at_will"
            spell_card = materialize_feature_spell(
                spell_match,
                method=method,
                source_type=source_type,
                source_key=source_key,
                at_will=at_will,
                allow_existing=True,
            )
            spell_id = str(spell_match[2].get("id") or "")
            free_casts = int(grant.get("free_casts", 0) or 0)
            if at_will and free_casts:
                raise ValueError("an at-will content spell cannot have free casts")
            resource_key = ""
            if free_casts:
                resource_group = str(grant.get("resource_group") or "").strip()
                resource_key = (
                    f"content_spell:{artifact_id}:group:{resource_group.casefold()}"
                    if resource_group
                    else f"content_spell:{artifact_id}:{resource_discriminator}:{spell_id}"
                )
                resource = {
                    "label": (
                        f"{source_key}: {resource_group}"
                        if resource_group
                        else f"{source_key}: {spell_card.get('name') or spell_id}"
                    ),
                    "value": free_casts,
                    "max": free_casts,
                    "recovers_on": str(grant.get("recovers_on") or ""),
                    "source_key": source_key,
                }
                existing_resource = sheet["resources"].get(resource_key)
                if existing_resource is not None and existing_resource != resource:
                    raise ValueError("feat spell resource conflicts with an existing resource")
                if existing_resource is None:
                    sheet["resources"][resource_key] = resource
            casting_source = {
                "source_key": source_key,
                "method": method,
                "spellcasting_ability": str(grant.get("spellcasting_ability") or "").casefold(),
                "resource_key": resource_key or None,
                "allow_slot_cast": grant.get("allow_slot_cast") is True,
                "minimum_level": int(grant.get("minimum_level", 1) or 1),
                "ritual_only": grant.get("ritual_only") is True,
            }
            casting_overrides = _support.deepcopy(dict(grant.get("casting_overrides") or {}))
            if casting_overrides:
                casting_source["casting_overrides"] = casting_overrides
            access = spell_card.setdefault("access", {})
            current_sources = list(access.get("feature_casting_sources") or [])
            if any(
                str(item.get("source_key") or "").casefold() == source_key.casefold()
                for item in current_sources
                if isinstance(item, dict)
            ):
                raise ValueError("feat spell casting source is already present")
            access["feature_casting_sources"] = [*current_sources, casting_source]
            return {
                "artifact_id": spell_id,
                "name": str(spell_card.get("name") or spell_id),
                **casting_source,
            }

        def resolve_spell_list_expansion(
            declared: Any,
            *,
            source_label: str,
        ) -> tuple[list[dict[str, str]], str | None]:
            if not isinstance(declared, list):
                raise _support.RulesetUnavailableError(
                    f"{source_label} spell-list expansion is not executable"
                )
            resolved: list[dict[str, str]] = []
            for raw_spell_name in declared:
                spell_name = str(raw_spell_name).strip()
                if not spell_name:
                    raise _support.RulesetUnavailableError(
                        f"{source_label} spell-list expansion contains an empty spell"
                    )
                matches = [
                    item
                    for item in candidates
                    if item[2].get("kind") == "spell"
                    and str(dict(item[2].get("card") or {}).get("name") or "").casefold()
                    == spell_name.casefold()
                ]
                matches = self.source_scoped_content_matches(
                    matches,
                    source_pack_id=pack_id,
                    source_pack_version=version,
                )
                if len(matches) != 1:
                    return [], spell_name
                spell_pack_id, spell_pack_version, spell_artifact = matches[0]
                resolved.append(
                    {
                        "artifact_id": str(spell_artifact["id"]),
                        "name": str(
                            dict(spell_artifact.get("card") or {}).get("name") or spell_name
                        ),
                        "pack_id": spell_pack_id,
                        "pack_version": spell_pack_version,
                    }
                )
            return resolved, None

        def materialize_feat(
            feat_match: tuple[str, str, dict[str, Any]],
            feat_selection: dict[str, Any],
            *,
            source: str,
        ) -> dict[str, Any]:
            feat_pack_id, feat_version, feat_artifact = feat_match
            feat_id = str(feat_artifact["id"])
            feat_card = _support.deepcopy(dict(feat_artifact.get("card") or {}))
            repeatable = bool(feat_card.get("repeatable", False))
            if not repeatable and any(
                str(item.get("id") or "") == feat_id for item in sheet["content"]["feats"]
            ):
                raise ValueError("selected feat is already present and is not repeatable")
            for prerequisite in feat_card.get("prerequisites", []):
                prerequisite_kind = str(prerequisite.get("kind") or "")
                if prerequisite_kind == "level_minimum":
                    if int(sheet["progression"]["level"]) < int(
                        prerequisite.get("minimum", 0) or 0
                    ):
                        raise ValueError("feat level prerequisite is not met")
                elif prerequisite_kind == "ability_any_minimum":
                    minimum = int(prerequisite.get("minimum", 0) or 0)
                    if not any(
                        int(sheet["abilities"].get(str(ability), {}).get("score", 0) or 0)
                        >= minimum
                        for ability in prerequisite.get("abilities", [])
                    ):
                        raise ValueError("feat ability prerequisite is not met")
                elif prerequisite_kind == "ability_minimum":
                    ability = str(prerequisite.get("ability") or "").casefold()
                    if int(sheet["abilities"].get(ability, {}).get("score", 0) or 0) < int(
                        prerequisite.get("minimum", 0) or 0
                    ):
                        raise ValueError(f"feat prerequisite is not met: {ability}")
                elif prerequisite_kind == "feature_required":
                    required_feature = str(prerequisite.get("feature") or "").casefold()
                    has_feature = required_feature == source.casefold() or any(
                        required_feature == str(item.get("name") or "").casefold()
                        for item in sheet["content"]["features"]
                    )
                    if required_feature == "spellcasting":
                        has_feature = has_feature or bool(
                            str(sheet.get("spellcasting", {}).get("ability") or "")
                        )
                    if not has_feature:
                        raise ValueError("feat feature prerequisite is not met")
                elif prerequisite_kind == "feature_forbidden":
                    forbidden_feature = str(prerequisite.get("feature") or "").casefold()
                    if not forbidden_feature:
                        raise ValueError("feat forbidden feature prerequisite is empty")
                    if any(
                        forbidden_feature in str(item.get("name") or "").casefold()
                        for item in sheet["content"]["features"]
                    ):
                        raise ValueError("feat forbidden feature prerequisite is present")
                elif prerequisite_kind == "species_required":
                    allowed_species = {
                        str(item).casefold() for item in prerequisite.get("species", [])
                    }
                    if str(sheet["progression"].get("species") or "").casefold() not in (
                        allowed_species
                    ):
                        raise ValueError("feat species prerequisite is not met")
                elif prerequisite_kind == "size_required":
                    allowed_sizes = {str(item).casefold() for item in prerequisite.get("sizes", [])}
                    if str(sheet["traits"].get("size") or "").casefold() not in (allowed_sizes):
                        raise ValueError("feat size prerequisite is not met")
                elif prerequisite_kind == "species_or_size":
                    allowed_species = {
                        str(item).casefold() for item in prerequisite.get("species", [])
                    }
                    allowed_sizes = {str(item).casefold() for item in prerequisite.get("sizes", [])}
                    has_species = (
                        str(sheet["progression"].get("species") or "").casefold() in allowed_species
                    )
                    has_size = str(sheet["traits"].get("size") or "").casefold() in allowed_sizes
                    if not (has_species or has_size):
                        raise ValueError("feat species-or-size prerequisite is not met")
                else:
                    raise _support.RulesetUnavailableError(
                        "feat prerequisite requires Agent-as-DM source review"
                    )
            requirements = dict(feat_card.get("selection_requirements") or {})
            recorded_choices = _support.deepcopy(feat_selection)
            if requirements:
                requirement_kind = str(requirements.get("kind") or "")
                choice_field = str(requirements.get("field") or "")
                unsupported = set(feat_selection) - {choice_field}
                if unsupported:
                    raise ValueError(
                        f"feat selection has unsupported fields: {sorted(unsupported)}"
                    )
                if requirement_kind == "ability_score_increase":
                    normalized_increases = apply_ability_score_increases(
                        requirements,
                        feat_selection.get(choice_field),
                        source=f"{source}: {feat_card.get('name') or feat_id}",
                    )
                    recorded_choices[choice_field] = normalized_increases
                elif requirement_kind == "magic_initiate":
                    magic_choice = feat_selection.get(choice_field)
                    if not isinstance(magic_choice, dict):
                        raise ValueError("Magic Initiate choice must be a structured object")
                    expected_fields = {
                        "source_class",
                        "spellcasting_ability",
                        "cantrip_artifact_ids",
                        "level_1_spell_artifact_id",
                    }
                    if set(magic_choice) != expected_fields:
                        raise ValueError(
                            "Magic Initiate requires source_class, spellcasting_ability, "
                            "two cantrip_artifact_ids, and level_1_spell_artifact_id"
                        )
                    source_class = str(magic_choice["source_class"]).strip().title()
                    source_options = {
                        str(item).casefold()
                        for item in requirements.get("source_class_options", [])
                    }
                    if source_class.casefold() not in source_options:
                        raise ValueError("Magic Initiate source_class is not allowed")
                    prior_source_classes = {
                        str(
                            dict(item.get("choices") or {})
                            .get("magic_initiate", {})
                            .get("source_class")
                            or ""
                        ).casefold()
                        for item in sheet["content"]["feats"]
                        if str(item.get("id") or "") == feat_id
                    }
                    if source_class.casefold() in prior_source_classes:
                        raise ValueError(
                            "Magic Initiate must use a different spell list when repeated"
                        )
                    spellcasting_ability = str(magic_choice["spellcasting_ability"]).casefold()
                    ability_options = {
                        str(item).casefold()
                        for item in requirements.get("spellcasting_ability_options", [])
                    }
                    if spellcasting_ability not in ability_options:
                        raise ValueError("Magic Initiate spellcasting ability is not allowed")
                    cantrip_ids = _support._validated_distinct_choices(
                        magic_choice["cantrip_artifact_ids"],
                        count=int(requirements.get("cantrip_count", 2) or 2),
                        label="Magic Initiate cantrips",
                    )
                    spell_ids = [
                        *cantrip_ids,
                        str(magic_choice["level_1_spell_artifact_id"]),
                    ]
                    spell_matches: list[tuple[str, str, dict[str, Any]]] = []
                    for index, spell_id in enumerate(spell_ids):
                        spell_match = next(
                            (
                                item
                                for item in candidates
                                if str(item[2].get("id") or "") == spell_id
                                and item[2].get("kind") == "spell"
                            ),
                            None,
                        )
                        if spell_match is None:
                            raise ValueError("Magic Initiate spell artifact is unavailable")
                        spell_card = dict(spell_match[2].get("card") or {})
                        expected_level = 0 if index < len(cantrip_ids) else 1
                        if (
                            source_class.casefold()
                            not in {str(item).casefold() for item in spell_card.get("classes", [])}
                            or int(spell_card.get("level", -1)) != expected_level
                        ):
                            raise ValueError(
                                "Magic Initiate spell does not match its source list and level"
                            )
                        spell_matches.append(spell_match)
                    for spell_match in spell_matches[: len(cantrip_ids)]:
                        materialize_feature_spell(
                            spell_match,
                            method="known",
                            source_key=f"Magic Initiate ({source_class})",
                            allow_existing=True,
                        )
                    granted_spell = materialize_feature_spell(
                        spell_matches[-1],
                        method="class_prepared",
                        source_key=f"Magic Initiate ({source_class})",
                        allow_existing=True,
                    )
                    granted_spell.setdefault("access", {})["always_prepared"] = True
                    granted_spell["access"]["prepared"] = True
                    free_cast_key = f"magic_initiate:{source_class.casefold()}:{spell_ids[-1]}"
                    sheet["resources"][free_cast_key] = {
                        "label": (
                            f"Magic Initiate ({source_class}): "
                            f"{granted_spell.get('name') or spell_ids[-1]}"
                        ),
                        "value": 1,
                        "max": 1,
                        "recovers_on": "long_rest",
                        "source_key": f"Magic Initiate ({source_class})",
                    }
                    recorded_choices[choice_field] = _support.deepcopy(magic_choice)
                elif requirement_kind == "proficiency_grants":
                    raw_proficiencies = feat_selection.get(choice_field)
                    if not isinstance(raw_proficiencies, list) or len(raw_proficiencies) != int(
                        requirements.get("count", 0) or 0
                    ):
                        raise ValueError("Skilled requires exactly three proficiency choices")
                    skill_options = {
                        str(item).casefold() for item in requirements.get("skill_options", [])
                    }
                    tool_options = {
                        str(item).casefold(): str(item)
                        for item in requirements.get("tool_options", [])
                    }
                    normalized_proficiencies: list[dict[str, str]] = []
                    seen_proficiencies: set[tuple[str, str]] = set()
                    for raw_proficiency in raw_proficiencies:
                        if not isinstance(raw_proficiency, dict) or set(raw_proficiency) != {
                            "kind",
                            "name",
                        }:
                            raise ValueError("Skilled proficiency choices require kind and name")
                        proficiency_kind = str(raw_proficiency["kind"]).casefold()
                        proficiency_name = str(raw_proficiency["name"]).strip()
                        key = (proficiency_kind, proficiency_name.casefold())
                        if key in seen_proficiencies:
                            raise ValueError("Skilled proficiency choices must be distinct")
                        seen_proficiencies.add(key)
                        if proficiency_kind == "skill":
                            skill_key = proficiency_name.casefold()
                            if skill_key not in skill_options or skill_key not in sheet["skills"]:
                                raise ValueError("Skilled names an unavailable skill")
                            if sheet["skills"][skill_key]["proficiency"] != "none":
                                raise ValueError("Skilled skill is already proficient")
                            sheet["skills"][skill_key]["proficiency"] = "proficient"
                            normalized_name = skill_key
                        elif proficiency_kind == "tool":
                            if proficiency_name.casefold() not in tool_options:
                                raise ValueError("Skilled names an unavailable tool")
                            normalized_name = tool_options[proficiency_name.casefold()]
                            if normalized_name.casefold() in {
                                str(item).casefold()
                                for item in sheet["traits"]["proficiencies"]["tools"]
                            }:
                                raise ValueError("Skilled tool is already proficient")
                            sheet["traits"]["proficiencies"]["tools"].append(normalized_name)
                        else:
                            raise ValueError("Skilled choice kind must be skill or tool")
                        normalized_proficiencies.append(
                            {"kind": proficiency_kind, "name": normalized_name}
                        )
                    recorded_choices[choice_field] = normalized_proficiencies
                elif requirement_kind == "proficiency_groups":
                    recorded_choices[choice_field] = (
                        _support._materialize_feature_proficiency_groups(
                            sheet,
                            value=feat_selection.get(choice_field),
                            groups=requirements.get("groups"),
                        )
                    )
                elif requirement_kind == "spell_grants":
                    raw_choices = feat_selection.get(choice_field)
                    if not isinstance(raw_choices, dict):
                        raise ValueError("feat spell choices must be an object")
                    groups = list(requirements.get("groups") or [])
                    group_ids = {str(item.get("id") or "") for item in groups}
                    if set(raw_choices) != group_ids:
                        raise ValueError(
                            "feat spell choices must provide exactly the reviewed groups"
                        )
                    normalized_spell_choices: dict[str, list[str]] = {}
                    selected_spell_ids: set[str] = set()
                    for raw_group in groups:
                        group = dict(raw_group)
                        group_id = str(group.get("id") or "")
                        spell_ids = _support._validated_distinct_choices(
                            raw_choices.get(group_id),
                            count=int(group.get("count", 0) or 0),
                            label=f"feat spell group {group_id}",
                        )
                        for spell_id in spell_ids:
                            if spell_id in selected_spell_ids:
                                raise ValueError("feat spell choices must be distinct")
                            selected_spell_ids.add(spell_id)
                            spell_match = content_spell_match(group, artifact_id=spell_id)
                            materialize_content_spell(
                                spell_match,
                                group,
                                source_type="feat",
                                source_key=f"{source}: {feat_card.get('name') or feat_id}",
                                resource_discriminator=group_id,
                            )
                        normalized_spell_choices[group_id] = spell_ids
                    recorded_choices[choice_field] = normalized_spell_choices
                else:
                    raise _support.RulesetUnavailableError(
                        "feat selection requirements are not executable"
                    )
            elif feat_selection:
                raise ValueError("selected feat does not accept structured choices")
            mechanical_grants = dict(feat_card.get("mechanical_grants") or {})
            fixed_increases = dict(mechanical_grants.get("ability_score_increases") or {})
            if fixed_increases:
                apply_ability_score_increases(
                    {
                        "allowed_distributions": [sorted(fixed_increases.values(), reverse=True)],
                        "ability_options": list(fixed_increases),
                        "maximum_score": int(
                            mechanical_grants.get("maximum_ability_score", 20) or 20
                        ),
                    },
                    fixed_increases,
                    source=f"{source}: {feat_card.get('name') or feat_id}",
                )
            for field, target in (
                ("languages", sheet["traits"]["languages"]),
                (
                    "tool_proficiencies",
                    sheet["traits"]["proficiencies"]["tools"],
                ),
                (
                    "weapon_proficiencies",
                    sheet["traits"]["proficiencies"]["weapons"],
                ),
            ):
                for value in mechanical_grants.get(field, []):
                    if str(value).casefold() not in {str(item).casefold() for item in target}:
                        target.append(value)
            fixed_spell_grants = []
            for index, raw_grant in enumerate(mechanical_grants.get("spell_grants", [])):
                grant = dict(raw_grant)
                fixed_spell_grants.append(
                    materialize_content_spell(
                        content_spell_match(grant),
                        grant,
                        source_type="feat",
                        source_key=f"{source}: {feat_card.get('name') or feat_id}",
                        resource_discriminator=f"fixed-{index}",
                    )
                )
            if fixed_spell_grants:
                recorded_choices["fixed_spell_grants"] = fixed_spell_grants
            for metadata_key in (
                "category",
                "prerequisites",
                "repeatable",
                "selection_requirements",
                "mechanical_grants",
            ):
                feat_card.pop(metadata_key, None)
            if recorded_choices:
                feat_card["choices"] = {
                    **dict(feat_card.get("choices") or {}),
                    **recorded_choices,
                    "grant_source": source,
                }
            else:
                feat_card["choices"] = {
                    **dict(feat_card.get("choices") or {}),
                    "grant_source": source,
                }
            feat_card.update(
                id=feat_id,
                pack_id=feat_pack_id,
                pack_version=feat_version,
                rule_refs=list(feat_artifact.get("rule_refs") or []),
                mechanic_refs=list(feat_artifact.get("mechanic_refs") or []),
            )
            sheet["content"]["feats"].append(feat_card)
            return feat_card

        if kind == "class":
            if phase != _support.PROFILE_LOBBY:
                raise _support.CombatEngineError(
                    "base-class selection is available only during lobby setup"
                )
            supported_choices = {"skills", "tools", "skill_replacements", "tool_replacements"}
            class_definition = card.get("class_definition")
            equipment_contract = (
                class_definition.get("starting_equipment")
                if isinstance(class_definition, dict)
                else None
            )
            if equipment_contract is not None:
                equipment_contract = _support.normalize_starting_equipment_contract(
                    equipment_contract
                )
                supported_choices.add("starting_equipment")
            unsupported_choices = set(selection) - supported_choices
            if unsupported_choices:
                raise ValueError(
                    "unsupported base-class selection fields: "
                    + ", ".join(sorted(unsupported_choices))
                )
            if not isinstance(class_definition, dict):
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": "base class has no reviewed class_definition",
                }
            equipment_selection = selection.get("starting_equipment")
            if equipment_contract is not None and equipment_selection is None:
                return {
                    "status": "pending_choice",
                    "reason": "base class requires its reviewed starting-equipment choice",
                    "starting_equipment": _support.deepcopy(equipment_contract),
                }
            raw_skills = selection.get("skills")
            if not isinstance(raw_skills, list):
                return {
                    "status": "pending_choice",
                    "reason": "base class requires its reviewed skill choices",
                }
            tool_choice_count = int(class_definition.get("tool_choice_count", 0) or 0)
            raw_tools = selection.get("tools", [])
            if tool_choice_count and not isinstance(selection.get("tools"), list):
                return {
                    "status": "pending_choice",
                    "reason": "base class requires its reviewed tool choices",
                }
            if not isinstance(raw_tools, list):
                raise ValueError("base class tools selection must be an array")
            try:
                class_result = _support.initialize_base_class(
                    sheet,
                    class_name=str(card.get("name") or artifact_id),
                    class_definition=class_definition,
                    skill_choices=raw_skills,
                    tool_choices=raw_tools,
                    skill_replacements=selection.get("skill_replacements"),
                    tool_replacements=selection.get("tool_replacements"),
                    tool_replacement_options=list(
                        _support._reviewed_tool_options(candidates).values()
                    ),
                    source=f"{pack_id}@{version}:{artifact_id}",
                )
            except _support.CombatEngineError as error:
                if "proficiency replacements are required for:" in str(error):
                    return {
                        "status": "pending_choice",
                        "reason": str(error),
                    }
                raise ValueError(str(error)) from error
            sheet = class_result.pop("sheet")
            if equipment_contract is not None:
                if not isinstance(equipment_selection, dict):
                    raise ValueError("starting_equipment must be an object")
                equipment_selection = _support.normalize_starting_equipment_selection(
                    equipment_contract, equipment_selection
                )
                if equipment_selection["mode"] == "gold":
                    stream = _support.active_random_stream()
                    if stream is None:
                        stream = _support.CampaignRandomStream.from_campaign_state(
                            current.campaign_id,
                            campaign.state,
                            operation="character_content_apply",
                            idempotency_key=idempotency_key,
                            campaign_revision=campaign.revision,
                        )
                        with _support.use_random_stream(stream):
                            return self.character_content_apply_impl(
                                character_id,
                                artifact_id,
                                selection,
                                grant,
                                principal_id,
                                expected_revision,
                                idempotency_key,
                            )
                    random_state = _support.validate_random_stream_state(
                        dict(campaign.state or {}).get("random_stream")
                        or _support.initial_random_stream(f"sagasmith-dnd:{current.campaign_id}")
                    )
                    if (
                        stream.campaign_id != current.campaign_id
                        or stream.campaign_revision != campaign.revision
                        or stream.seed != random_state["seed"]
                        or stream.start_position != random_state["position"]
                    ):
                        raise _support.CombatEngineError(
                            "starting equipment requires the current campaign random snapshot"
                        )
                templates = {}
                item_sources = {}
                if equipment_selection.get("mode") == "equipment":
                    required_item_ids = {
                        item["artifact_id"] for item in equipment_contract["items"]
                    }
                    raw_choices = equipment_selection.get("choices", {})
                    if not isinstance(raw_choices, dict):
                        raise ValueError("starting_equipment choices must be an object")
                    for choices in raw_choices.values():
                        if not isinstance(choices, list) or any(
                            not isinstance(item, str) for item in choices
                        ):
                            raise ValueError("starting_equipment choices must be arrays of ids")
                        required_item_ids.update(choices)
                    for item_id in sorted(required_item_ids):
                        item_matches = [
                            item
                            for item in candidates
                            if item[2].get("kind") == "item" and item[2].get("id") == item_id
                        ]
                        if len(item_matches) != 1:
                            raise _support.RulesetUnavailableError(
                                "starting equipment requires one exact active item artifact"
                            )
                        item_pack, item_version, item_artifact = item_matches[0]
                        item_artifact = self.reviewed_official_runtime_artifact(
                            item_pack, item_version, item_artifact
                        )
                        if str(
                            item_artifact.get("application_state") or "selection_ready"
                        ) != "selection_ready" or (
                            item_artifact.get("selection_contract") is not None
                            and _support.selection_input_errors(item_artifact, {})
                        ):
                            raise _support.RulesetUnavailableError(
                                "starting equipment item has no reviewed selection contract"
                            )
                        # Finalized non-official runtime definitions deliberately
                        # omit draft review records. Validate their item shape;
                        # reserved official definitions require archive review above.
                        _support.selection_schema_for_artifact(item_artifact)
                        template = dict(item_artifact.get("card") or {}).get("inventory_template")
                        if not isinstance(template, dict):
                            raise _support.RulesetUnavailableError(
                                "starting equipment item has no template"
                            )
                        templates[item_id] = _support.deepcopy(template)
                        item_sources[item_id] = {
                            "pack_id": item_pack,
                            "pack_version": item_version,
                            "content_hash": _support.content_fingerprint(item_artifact),
                        }
                if (
                    equipment_selection.get("mode") == "gold"
                    and dict(equipment_contract.get("gold_alternative") or {}).get(
                        "replaces_background_equipment"
                    )
                    is True
                    and sheet["progression"].get("background")
                ):
                    # Only reclaim the exact, still-held starting award. A spent,
                    # transferred or changed award cannot erase unrelated property.
                    _support._require_authoritative_background_state(
                        sheet, character_id=current.id, secret=self.content_authority_secret
                    )
                    grants = sheet["progression"]["background_grants"]
                    award = grants["choices"].get("starting_equipment_award")
                    if not isinstance(award, dict):
                        raise ValueError("background starting-equipment award is not recorded")
                    awarded_items = list(award.get("items") or [])
                    award_ids = {item["id"] for item in awarded_items}
                    if set(grants["equipment_item_ids"]) != award_ids:
                        raise ValueError("background starting equipment is not an exact award")
                    held = {item["id"]: item for item in sheet["inventory"]["items"]}
                    if any(held.get(item["id"]) != item for item in awarded_items):
                        raise ValueError(
                            "background starting equipment has changed or left custody"
                        )
                    grants["equipment_item_ids"] = []
                    sheet["inventory"]["items"] = [
                        item for item in sheet["inventory"]["items"] if item["id"] not in award_ids
                    ]
                    for slot, item_id in sheet["inventory"]["equipment_slots"].items():
                        if item_id in award_ids:
                            sheet["inventory"]["equipment_slots"][slot] = None
                    for denomination, amount in award.get("wallet", {}).items():
                        if amount:
                            sheet = _support.adjust_wallet(sheet, denomination, -amount)
                    grants = sheet["progression"]["background_grants"]
                    grants["choices"]["equipment_mode"] = "class_starting_gold"
                    grants["choices"]["starting_equipment_award"] = {"items": [], "wallet": {}}
                    background_record = next(
                        item
                        for item in sheet["content"]["selections"]
                        if item["kind"] == "background"
                    )
                    background_record["selection"]["equipment_mode"] = "class_starting_gold"
                    authority_id = _support.uuid4().hex
                    background_record["selection"][_support.BACKGROUND_AUTHORITY_SELECTION_KEY] = {
                        "authority_id": authority_id,
                        "authorization": _support.sign_receipt(
                            _support._background_authority_payload(
                                sheet,
                                background_record,
                                character_id=current.id,
                                authority_id=authority_id,
                            ),
                            self.content_authority_secret,
                        ),
                    }
                    materialization = dict(background_record.get("selection") or {}).get(
                        _support.BACKGROUND_MATERIALIZATION_KEY
                    )
                    if isinstance(materialization, dict):
                        materialization = _support.deepcopy(materialization)
                        materialization["after"] = _support.json.dumps(
                            _support._content_projection_snapshot(sheet, "background"),
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        background_record["selection"][_support.BACKGROUND_MATERIALIZATION_KEY] = (
                            materialization
                        )
                        authority_id = _support.uuid4().hex
                        background_record["selection"][
                            _support.BACKGROUND_AUTHORITY_SELECTION_KEY
                        ] = {
                            "authority_id": authority_id,
                            "authorization": _support.sign_receipt(
                                _support._background_authority_payload(
                                    sheet,
                                    background_record,
                                    character_id=current.id,
                                    authority_id=authority_id,
                                ),
                                self.content_authority_secret,
                            ),
                        }
                equipment_result = _support.apply_starting_equipment(
                    sheet,
                    contract=equipment_contract,
                    selection=equipment_selection,
                    item_templates=templates,
                    source_key=f"{pack_id}@{version}:{artifact_id}",
                    rng=_support.active_random_stream(),
                )
                sheet = equipment_result.pop("sheet")
                equipment_result["item_sources"] = item_sources
                class_result["starting_equipment"] = equipment_result
            selection = {
                "skills": list(class_result.get("skill_proficiency_choices") or []),
                "tools": list(class_result.get("tool_proficiency_choices") or []),
            }
            if equipment_contract is not None:
                selection["starting_equipment"] = _support.deepcopy(equipment_result["selection"])
                selection["starting_equipment_result"] = _support.deepcopy(equipment_result)
            if class_result.get("skill_proficiency_replacements"):
                selection["skill_replacements"] = _support.deepcopy(
                    dict(class_result["skill_proficiency_replacements"])
                )
            if class_result.get("tool_proficiency_replacements"):
                selection["tool_replacements"] = _support.deepcopy(
                    dict(class_result["tool_proficiency_replacements"])
                )
            class_materialization = class_result
        elif kind == "spell":
            if any(item.get("id") == artifact_id for item in sheet["content"]["spells"]):
                raise ValueError("content spell is already present")
            try:
                source_class = _support.validate_spell_grant(
                    sheet,
                    card,
                    source_class=selection.get("source_class"),
                    artifact_id=artifact_id,
                )
            except _support.CombatEngineError as error:
                reason = str(error)
                if reason in {
                    "spell selection requires a recorded class",
                    "multiclass spell selection requires source_class",
                }:
                    return {"status": "pending_choice", "reason": reason}
                if reason == "spell artifact has no structured class-list eligibility":
                    return {
                        **_support._ruling_status(
                            "pending_ruling",
                            "missing_or_conflicting_source_review",
                        ),
                        "reason": reason,
                    }
                raise ValueError(reason) from error
            level = int(card.get("level", 0) or 0)
            preparation_mode = str(
                sheet.get("spellcasting", {}).get("preparation", {}).get("mode") or "known"
            )
            method = str(selection.get("method") or "").strip().casefold()
            if not method:
                method = (
                    "known"
                    if level == 0 or preparation_mode == "known"
                    else ("spellbook" if preparation_mode == "spellbook" else "class_prepared")
                )
            if method not in {"known", "spellbook", "spellbook_copy", "class_prepared"}:
                raise ValueError(
                    "spell selection method must be known, spellbook, spellbook_copy, "
                    "or class_prepared"
                )
            if level == 0 and method != "known":
                raise ValueError("cantrips must be selected as known spells")
            replacement_id = selection.get("replace_existing")
            if replacement_id is not None:
                if not isinstance(replacement_id, str) or not replacement_id.strip():
                    raise ValueError("spell replace_existing must be a non-empty spell id")
                replacement_id = replacement_id.strip()
                try:
                    edition = _support.normalize_dnd_edition(sheet.get("edition"))
                except ValueError as error:
                    raise ValueError(str(error)) from error
                if edition != "2014":
                    raise ValueError("spell replacement is available only for 2014 known casters")
                if method != "known" or source_class not in {
                    "bard",
                    "ranger",
                    "sorcerer",
                    "warlock",
                }:
                    raise ValueError(
                        "spell replacement requires a 2014 Bard, Ranger, Sorcerer, "
                        "or Warlock known spell"
                    )
                if level == 0:
                    raise ValueError("spell replacement cannot replace a cantrip")
                existing_spell = next(
                    (
                        item
                        for item in sheet["content"]["spells"]
                        if str(item.get("id") or "") == replacement_id
                    ),
                    None,
                )
                if existing_spell is None:
                    raise ValueError("spell replace_existing must reference an existing spell")
                existing_grant = dict(existing_spell.get("grant") or {})
                existing_access = dict(existing_spell.get("access") or {})
                if (
                    existing_grant.get("source_type") != "class"
                    or str(existing_grant.get("source_key") or "").casefold() != source_class
                    or existing_grant.get("method") != "known"
                    or existing_access.get("known") is not True
                    or int(existing_spell.get("level", 0) or 0) == 0
                ):
                    raise ValueError(
                        "spell replace_existing must reference an existing known spell "
                        "from the same class"
                    )
                spell_replacement = {
                    "removed_spell_id": replacement_id,
                    "removed_spell_name": str(existing_spell.get("name") or replacement_id),
                    "added_spell_id": artifact_id,
                    "added_spell_name": str(card.get("name") or artifact_id),
                    "source_class": source_class,
                }
            if method in {"spellbook", "spellbook_copy"} and preparation_mode != "spellbook":
                raise ValueError("only a spellbook caster can select a spellbook grant")
            if method == "class_prepared" and preparation_mode != "prepared":
                raise ValueError("class_prepared requires prepared-caster configuration")
            if method == "known" and level > 0 and preparation_mode != "known":
                raise ValueError(
                    "this caster records level 1+ spells as prepared or spellbook grants"
                )
            if method == "known":
                spell_status = _support.profile_spell_selection_status(
                    sheet, class_name=source_class
                )
                if spell_status is not None:
                    choice_kind = "cantrips" if level == 0 else "leveled_spells"
                    choice_status = spell_status[choice_kind]
                    if (
                        choice_status["present"] >= choice_status["required"]
                        and replacement_id is None
                    ):
                        raise ValueError(
                            f"{source_class} {choice_kind} selection exceeds the reviewed "
                            f"class-level limit of {choice_status['required']}"
                        )
            if method == "spellbook_copy":
                if source_class != "wizard":
                    raise ValueError("only wizard spells can be copied into this spellbook")
                if phase != _support.PROFILE_PLAY:
                    raise _support.CombatEngineError(
                        "spellbook copying is available only during play"
                    )
                spellbook_copy = {"level": level}
            elif phase != _support.PROFILE_LOBBY and play_grant is None:
                raise _support.CombatEngineError(
                    "content grants belong to lobby setup or level advancement; "
                    "only source-bound spellbook_copy is legal during play"
                )
            spell_card = _support._character_spell_card(card)
            spell_card["grant"] = {
                "source_type": "class",
                "source_key": source_class,
                "method": method,
            }
            spell_card.setdefault("access", {})["known"] = method == "known"
            spell_card["access"]["prepared"] = False
            if method in {"spellbook", "spellbook_copy"}:
                spellbook = sheet["spellcasting"]["spellbook"]
                if not spellbook.get("enabled"):
                    raise ValueError("spellbook grant requires spellcasting.spellbook.enabled")
                spellbook["spell_ids"] = [
                    *list(spellbook.get("spell_ids") or []),
                    artifact_id,
                ]
            # Eligibility and retrieval text belong to the catalog artifact.
            # The actor card stores only the character spell schema, selected
            # grant source, and exact pack provenance.
            spell_card.update(provenance)
            if spell_replacement is not None:
                removed_id = spell_replacement["removed_spell_id"]
                sheet["content"]["spells"] = [
                    item
                    for item in sheet["content"]["spells"]
                    if str(item.get("id") or "") != removed_id
                ]
                preparation = sheet.get("spellcasting", {}).get("preparation", {})
                preparation["selected_spell_ids"] = [
                    item for item in preparation.get("selected_spell_ids", []) if item != removed_id
                ]
                spellbook = sheet.get("spellcasting", {}).get("spellbook", {})
                spellbook["spell_ids"] = [
                    item for item in spellbook.get("spell_ids", []) if item != removed_id
                ]
            sheet["content"]["spells"].append(spell_card)
        elif kind == "feat":
            materialize_feat(
                match,
                selection,
                source="direct feat selection",
            )
        elif kind == "subclass":
            classes = list(sheet["progression"]["classes"])
            if not classes:
                return {
                    "status": "pending_choice",
                    "reason": "choose a base class before selecting a subclass",
                }
            declared_class = str(card.get("class_name") or "").strip()
            target_class = str(selection.get("target_class_name") or declared_class).strip()
            if not target_class:
                return {
                    "status": "pending_choice",
                    "reason": "subclass artifact needs class_name or target_class_name",
                }
            if declared_class and target_class.casefold() != declared_class.casefold():
                raise ValueError("subclass does not belong to target_class_name")
            target = next(
                (
                    item
                    for item in classes
                    if str(item.get("name") or "").casefold() == target_class.casefold()
                ),
                None,
            )
            if target is None:
                raise ValueError("subclass target class is not on this actor card")
            minimum_level = int(card.get("minimum_level", 1) or 1)
            if int(target.get("level", 0) or 0) < minimum_level:
                raise ValueError(
                    f"{target_class} must reach level {minimum_level} for this subclass"
                )
            bladesinging = artifact_id == _support.SCAG_BLADE_SINGING_SUBCLASS_ID
            if bladesinging:
                # SCAG 594 is a real prerequisite.  A non-elf selection is
                # legal only through a fresh DM decision recorded in the
                # source selection receipt; caller supplied signatures are
                # intentionally ignored.
                species_text = str(sheet["progression"].get("species") or "").casefold()
                species_tokens = set(_support.re.findall(r"[a-z0-9]+", species_text))
                elf_species = "elf" in species_tokens or (
                    "half" in species_tokens and "elf" in species_tokens
                )
                prior_selection = next(
                    (
                        dict(item.get("selection") or {})
                        for item in sheet["content"].get("selections", [])
                        if item.get("kind") == "subclass"
                        and str(item.get("artifact_id") or "") == artifact_id
                        and str(item.get("pack_id") or "") == pack_id
                        and str(item.get("pack_version") or "") == version
                    ),
                    {},
                )
                override = selection.get("species_prerequisite_override")
                override_supplied = override is not None
                if override is None and isinstance(
                    prior_selection.get("species_prerequisite_override"), dict
                ):
                    # Reapplying a subclass during a later level-up must carry
                    # forward the already settled DM exception rather than
                    # demanding a second ruling from the player.
                    override = _support.deepcopy(prior_selection["species_prerequisite_override"])
                    selection["species_prerequisite_override"] = _support.deepcopy(override)
                if override is not None:
                    if override_supplied and not self.is_dm(current.campaign_id, principal_id):
                        raise PermissionError("SCAG species overrides require the campaign DM")
                    if not isinstance(override, dict):
                        raise ValueError("SCAG species override requires only a reason")
                    if override_supplied and set(override) != {"reason"}:
                        raise ValueError("SCAG species override requires only a reason")
                    reason = " ".join(str(override.get("reason") or "").split())
                    if not 10 <= len(reason) <= 500:
                        raise ValueError(
                            "SCAG species override reason must be 10 to 500 characters"
                        )
                    if override_supplied:
                        authority_id = _support.uuid4().hex
                        selection["species_prerequisite_override"] = {
                            "authority_id": authority_id,
                            "reason": reason,
                            "authorization": _support.sign_receipt(
                                {
                                    "schema_version": 1,
                                    "purpose": "scag_bladesinging_species_override",
                                    "character_id": current.id,
                                    "artifact_id": artifact_id,
                                    "pack_id": pack_id,
                                    "pack_version": version,
                                    "authority_id": authority_id,
                                    "reason": reason,
                                },
                                self.content_authority_secret,
                            ),
                        }
                elif not elf_species:
                    raise ValueError(
                        "SCAG Bladesinging requires Elf or Half-Elf, or a specific DM override"
                    )
                all_feature_matches: dict[str, tuple[str, str, dict[str, Any]]] = {}
                for feature_id in sorted(_support.SCAG_BLADE_SINGING_FEATURE_IDS):
                    options = [
                        item
                        for item in candidates
                        if item[2].get("kind") == "feature"
                        and str(item[2].get("id") or "") == feature_id
                        and item[0] == pack_id
                        and item[1] == version
                    ]
                    if len(options) == 1:
                        all_feature_matches[feature_id] = options[0]
                if set(all_feature_matches) != _support.SCAG_BLADE_SINGING_FEATURE_IDS:
                    missing = sorted(
                        _support.SCAG_BLADE_SINGING_FEATURE_IDS - set(all_feature_matches)
                    )
                    raise _support.RulesetUnavailableError(
                        "SCAG Bladesinging is missing reviewed feature cards: " + ", ".join(missing)
                    )
                target_level = int(target.get("level", 0) or 0)
                feature_matches = {
                    feature_id: feature_match
                    for feature_id, feature_match in all_feature_matches.items()
                    if int(dict(feature_match[2].get("card") or {}).get("minimum_level", 1) or 1)
                    <= target_level
                }
                # Materialize the reviewed feature cards as executable
                # character content.  The runtime adds the errata clauses
                # below, while retaining the archive provenance on every card.
                for feature_id, feature_match in feature_matches.items():
                    feature_card = _support.deepcopy(dict(feature_match[2].get("card") or {}))
                    feature_card.update(
                        id=feature_id,
                        pack_id=feature_match[0],
                        pack_version=feature_match[1],
                        rule_refs=list(feature_match[2].get("rule_refs") or []),
                        mechanic_refs=list(feature_match[2].get("mechanic_refs") or []),
                        source_key=f"{pack_id}@{version}:{feature_id}",
                    )
                    for metadata_key in (
                        "class_name",
                        "subclass_name",
                        "feature_subtype",
                        "minimum_level",
                        "unlock_levels",
                        "repeatable_selection_levels",
                        "selection_requirements",
                        "selection_requirements_by_level",
                        "mechanical_grants",
                        "choice_metadata",
                        "resource_scaling",
                    ):
                        feature_card.pop(metadata_key, None)
                    if feature_id == _support.SCAG_RULE_PACK_ID + ".feature.bladesong":
                        feature_card["activation"] = {"type": "bonus_action"}
                        feature_card["resource_key"] = "scag_bladesong"
                    if feature_id == _support.SCAG_RULE_PACK_ID + ".feature.extra-attack":
                        feature_card["attack_scaling"] = {
                            "class_name": target_class,
                            "attacks_per_action_by_level": {"6": 2},
                        }
                    existing_feature = next(
                        (
                            item
                            for item in sheet["content"]["features"]
                            if item.get("id") == feature_id
                        ),
                        None,
                    )
                    if existing_feature is None:
                        sheet["content"]["features"].append(feature_card)
                    elif existing_feature.get("pack_version") != version:
                        raise ValueError(
                            "SCAG feature is already materialized from another version"
                        )
                training_id = _support.SCAG_RULE_PACK_ID + ".feature.training-in-war-and-song"
                if training_id in feature_matches:
                    training_card = dict(feature_matches[training_id][2].get("card") or {})
                    training_requirements = dict(training_card.get("selection_requirements") or {})
                    if str(training_requirements.get("kind") or "") != "proficiency_grants":
                        raise _support.RulesetUnavailableError(
                            "SCAG Training in War and Song has no executable choice contract"
                        )
                    training_value = selection.get("war_and_song_training")
                    existing_training = next(
                        (
                            item
                            for item in sheet["content"]["features"]
                            if item.get("id") == training_id
                        ),
                        None,
                    )
                    reuse_training = training_value is None and existing_training is not None
                    if training_value is None:
                        training_value = dict(
                            dict(existing_training or {}).get("choices") or {}
                        ).get("war_and_song_training")
                    if training_value is None:
                        raise ValueError(
                            "Bladesinging requires one reviewed one-handed melee weapon choice"
                        )
                    if reuse_training:
                        # Reapplying a subclass during level-up must validate and
                        # preserve the prior choice without granting the same
                        # proficiency a second time.
                        validation_sheet = _support.deepcopy(sheet)
                        if isinstance(training_value, dict):
                            for raw_group in training_requirements.get("groups") or []:
                                if not isinstance(raw_group, dict):
                                    continue
                                group_id = str(raw_group.get("id") or "")
                                selected = training_value.get(group_id)
                                kind = str(raw_group.get("kind") or "").casefold()
                                if not isinstance(selected, list):
                                    continue
                                if kind == "weapon":
                                    target_proficiencies = validation_sheet["traits"][
                                        "proficiencies"
                                    ]["weapons"]
                                elif kind == "tool":
                                    target_proficiencies = validation_sheet["traits"][
                                        "proficiencies"
                                    ]["tools"]
                                elif kind == "language":
                                    target_proficiencies = validation_sheet["traits"]["languages"]
                                else:
                                    continue
                                selected_keys = {str(item).casefold() for item in selected}
                                target_proficiencies[:] = [
                                    item
                                    for item in target_proficiencies
                                    if str(item).casefold() not in selected_keys
                                ]
                        normalized_training = _support._materialize_feature_proficiency_groups(
                            validation_sheet,
                            value=training_value,
                            groups=training_requirements.get("groups"),
                        )
                    else:
                        normalized_training = _support._materialize_feature_proficiency_groups(
                            sheet,
                            value=training_value,
                            groups=training_requirements.get("groups"),
                        )

                    for feature in sheet["content"]["features"]:
                        if feature.get("id") == training_id:
                            feature["choices"] = {
                                "war_and_song_training": normalized_training,
                            }
                            break
                    if sheet["skills"]["performance"]["proficiency"] == "none":
                        sheet["skills"]["performance"]["proficiency"] = "proficient"
                    for armor_name in ("light armor",):
                        if armor_name not in sheet["traits"]["proficiencies"]["armor"]:
                            sheet["traits"]["proficiencies"]["armor"].append(armor_name)
                bladesong_id = _support.SCAG_RULE_PACK_ID + ".feature.bladesong"
                if bladesong_id in feature_matches:
                    bladesong_artifact = feature_matches[bladesong_id][2]
                    source_grants = dict(
                        dict(bladesong_artifact.get("card") or {}).get("mechanical_grants") or {}
                    )
                    source_resources = dict(source_grants.get("resources") or {})
                    if len(source_resources) != 1:
                        raise _support.RulesetUnavailableError(
                            "SCAG Bladesong must declare exactly one reviewed resource grant"
                        )
                    source_resource_key, raw_source_resource = next(iter(source_resources.items()))
                    if not isinstance(raw_source_resource, dict):
                        raise _support.RulesetUnavailableError(
                            "SCAG Bladesong resource grant is not a reviewed object"
                        )
                    try:
                        source_maximum = int(raw_source_resource.get("max", 0) or 0)
                        source_value = int(raw_source_resource.get("value", source_maximum) or 0)
                    except (TypeError, ValueError) as error:
                        raise _support.RulesetUnavailableError(
                            "SCAG Bladesong resource grant has invalid capacity"
                        ) from error
                    source_recovery = str(raw_source_resource.get("recovers_on") or "").strip()
                    if (
                        not str(source_resource_key).strip()
                        or source_maximum < 1
                        or source_value < 0
                        or source_value > source_maximum
                        or source_recovery not in {"short_rest", "long_rest", "none"}
                    ):
                        raise _support.RulesetUnavailableError(
                            "SCAG Bladesong resource grant is not an executable reviewed profile"
                        )
                    resource = sheet["resources"].get("scag_bladesong")
                    if resource is None:
                        sheet["resources"]["scag_bladesong"] = {
                            "label": str(raw_source_resource.get("label") or "Bladesong"),
                            "value": source_value,
                            "max": source_maximum,
                            "recovers_on": source_recovery,
                            "source_key": f"{pack_id}@{version}:{bladesong_id}",
                        }
                    else:
                        if int(resource.get("max", 0) or 0) != source_maximum:
                            resource["max"] = source_maximum
                            resource["value"] = min(
                                int(resource.get("value", 0) or 0), source_maximum
                            )
                        resource["recovers_on"] = source_recovery
                        resource["source_key"] = f"{pack_id}@{version}:{bladesong_id}"
                extra_id = _support.SCAG_RULE_PACK_ID + ".feature.extra-attack"
                if extra_id in feature_matches and target_level >= 6:
                    sheet["combat"]["attacks_per_action"] = max(
                        2, int(sheet["combat"].get("attacks_per_action", 1))
                    )
            existing_subclass = str(target.get("subclass") or "")
            if existing_subclass and existing_subclass != str(card.get("name") or artifact_id):
                raise ValueError("target class already has a different subclass")
            if existing_subclass:
                selected_source = self.selected_progression_content_source(
                    sheet,
                    candidates,
                    class_name=target_class,
                    subclass_name=existing_subclass,
                )
                if selected_source is not None and (
                    selected_source[0] != pack_id
                    or selected_source[1] != version
                    or str(selected_source[2].get("id") or "") != artifact_id
                ):
                    raise ValueError("target class already has a source-bound subclass")
            target["subclass"] = str(card.get("name") or artifact_id)
            sheet["progression"]["classes"] = classes
            resolved_expansion, unresolved_spell_name = resolve_spell_list_expansion(
                card.get("spell_list_expansion", []),
                source_label="subclass",
            )
            if unresolved_spell_name is not None:
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": (
                        "subclass spell-list expansion needs one exact active "
                        f"spell artifact: {unresolved_spell_name}"
                    ),
                }
            source_class_name = str(target.get("name") or target_class)
            subclass_grants = sheet["progression"].setdefault(
                "subclass_grants", {"spell_list_expansion": []}
            )
            existing_expansion = list(subclass_grants.get("spell_list_expansion") or [])
            expansion_by_identity = {
                (
                    str(item.get("source_class") or "").casefold(),
                    str(item.get("artifact_id") or ""),
                ): item
                for item in existing_expansion
                if isinstance(item, dict)
            }
            for item in resolved_expansion:
                bound_item = {**item, "source_class": source_class_name}
                expansion_by_identity[(source_class_name.casefold(), str(item["artifact_id"]))] = (
                    bound_item
                )
            subclass_grants["spell_list_expansion"] = list(expansion_by_identity.values())
            always_prepared_spell_ids: list[str] = []
            for spell_grant in _support._subclass_spell_grants(card):
                if int(spell_grant.get("minimum_level", 1) or 1) > int(target.get("level", 0) or 0):
                    continue
                method = str(spell_grant.get("method") or "always_prepared")
                spell_name = str(spell_grant.get("name") or "").strip()
                spell_matches = self.source_scoped_content_matches(
                    [
                        item
                        for item in candidates
                        if item[2].get("kind") == "spell"
                        and str(dict(item[2].get("card") or {}).get("name") or "").casefold()
                        == spell_name.casefold()
                    ],
                    source_pack_id=pack_id,
                    source_pack_version=version,
                )
                if len(spell_matches) != 1:
                    return {
                        **_support._ruling_status(
                            "pending_ruling",
                            "missing_or_conflicting_source_review",
                        ),
                        "reason": (
                            f"subclass spell is not available in the active catalog: {spell_name}"
                        ),
                    }
                spell_match = spell_matches[0]
                spell_pack_id, spell_version, spell_artifact = spell_match
                spell_id = str(spell_artifact["id"])
                if method == "always_prepared":
                    always_prepared_spell_ids.append(spell_id)
                spell_card = next(
                    (item for item in sheet["content"]["spells"] if item.get("id") == spell_id),
                    None,
                )
                if spell_card is None:
                    spell_card = _support._character_spell_card(
                        dict(spell_artifact.get("card") or {})
                    )
                    sheet["content"]["spells"].append(spell_card)
                spell_card["grant"] = {
                    "source_type": "subclass",
                    "source_key": str(card.get("name") or artifact_id),
                    "method": ("class_prepared" if method == "always_prepared" else method),
                }
                access = spell_card.setdefault("access", {})
                access["known"] = method == "known"
                access["prepared"] = method == "always_prepared"
                access["always_prepared"] = method == "always_prepared"
                if method == "spellbook":
                    spellbook = sheet["spellcasting"]["spellbook"]
                    if not spellbook.get("enabled"):
                        raise _support.RulesetUnavailableError(
                            "subclass spellbook grant requires an enabled spellbook"
                        )
                    if spell_id not in spellbook.get("spell_ids", []):
                        spellbook["spell_ids"].append(spell_id)
                spell_card.update(
                    id=spell_id,
                    pack_id=spell_pack_id,
                    pack_version=spell_version,
                    rule_refs=list(spell_artifact.get("rule_refs") or []),
                    mechanic_refs=list(spell_artifact.get("mechanic_refs") or []),
                )
            if always_prepared_spell_ids:
                preparation = sheet["spellcasting"]["preparation"]
                preparation["selected_spell_ids"] = [
                    item
                    for item in preparation.get("selected_spell_ids", [])
                    if item not in set(always_prepared_spell_ids)
                ]
        elif kind == "background":
            existing_background = str(sheet["progression"].get("background") or "")
            base_background = str(card.get("name") or artifact_id)
            custom_name = str(selection.get("custom_name") or "").strip()
            custom_skills_raw = selection.get("skills")
            if custom_name and self.campaign_rules_edition(current.campaign_id) != "2014":
                raise ValueError("the PHB custom-background contract is available only in 2014")
            grants = dict(card.get("background_grants") or {})
            watchers_eye_binding = self.trusted_watchers_eye_binding(
                current.campaign_id,
                branch_id,
                pack_id,
                version,
                artifact,
            )
            if (
                artifact_id in _support.SCAG_WATCHERS_EYE_BACKGROUND_IDS
                and str(grants.get("feature") or "") == _support.WATCHERS_EYE_FEATURE_NAME
                and watchers_eye_binding is None
            ):
                raise _support.RulesetUnavailableError(
                    "Watcher's Eye requires the exact source-reviewed official SCAG archive"
                )
            fixed_equipment = _support.deepcopy(dict(grants.pop("equipment", {}) or {}))
            grant_skills = [str(item).strip().casefold() for item in grants.pop("skills", [])]
            card_skills = [
                str(item).strip().casefold() for item in card.get("skill_proficiencies", [])
            ]
            if grant_skills and card_skills and set(grant_skills) != set(card_skills):
                raise _support.RulesetUnavailableError(
                    "background skill grants conflict with skill_proficiencies"
                )
            declared_skills = card_skills or grant_skills
            requirements = dict(grants.get("choices") or {})
            skill_choice_count = int(requirements.get("skill_choice_count", 0) or 0)
            if custom_name and custom_skills_raw is None:
                return {
                    "status": "pending_choice",
                    "reason": (
                        "custom background requires custom_name and exactly two skill choices"
                    ),
                }
            if not custom_name and skill_choice_count and custom_skills_raw is None:
                return {
                    "status": "pending_choice",
                    "reason": (f"background requires exactly {skill_choice_count} skill choices"),
                }
            if not custom_name and not skill_choice_count and custom_skills_raw is not None:
                raise ValueError(
                    "background skills require custom_name unless the reviewed card "
                    "declares skill choices"
                )
            selected_background = custom_name or base_background
            if (
                existing_background
                and existing_background != selected_background
                and replacing_selection is None
            ):
                raise ValueError("character already has a different background")
            raw_languages = selection.get("languages", [])
            raw_tools = selection.get("tools", [])
            if custom_name:
                if not isinstance(raw_languages, list) or not isinstance(raw_tools, list):
                    raise ValueError("custom background languages and tools must be arrays")
                if len(raw_languages) + len(raw_tools) != 2:
                    return {
                        "status": "pending_choice",
                        "reason": (
                            "custom background requires a total of exactly two tool "
                            "proficiencies or languages"
                        ),
                    }
                language_count = len(raw_languages)
                fixed_languages: list[str] = []
            else:
                language_count = int(requirements.get("language_count", 0) or 0)
                fixed_languages = list(grants.get("languages") or [])
                if (
                    not isinstance(raw_languages, list)
                    or len(raw_languages) != language_count
                    or any(not str(item).strip() for item in raw_languages)
                ):
                    return {
                        "status": "pending_choice",
                        "reason": f"background requires exactly {language_count} language choices",
                    }
                language_options = {
                    str(item).casefold()
                    for item in requirements.get("language_options", [])
                    if str(item).strip()
                }
                if (
                    language_options
                    and requirements.get("allow_any_language") is not True
                    and any(str(item).casefold() not in language_options for item in raw_languages)
                ):
                    raise ValueError("background language is not one of the source options")
            selected_languages, all_languages, language_authorization_receipt = (
                _support._validated_background_languages(
                    raw_languages,
                    count=language_count,
                    fixed=fixed_languages,
                    existing=sheet["traits"]["languages"],
                    campaign_id=current.campaign_id,
                    campaign_revision=campaign.revision,
                    campaign_settings=dict(campaign.settings or {}),
                    authorization=selection.get("language_authorization"),
                    principal_id=principal_id,
                    principal_is_dm=self.is_dm(current.campaign_id, principal_id),
                )
            )
            ability_options = [
                str(item).casefold() for item in requirements.get("ability_score_options", [])
            ]
            selected_ability_increases: dict[str, int] = {}
            if custom_name and ability_options:
                raise ValueError("2014 custom backgrounds cannot replace ability score grants")
            if ability_options:
                selected_ability_increases = apply_ability_score_increases(
                    {
                        "ability_options": ability_options,
                        "allowed_distributions": list(
                            requirements.get("allowed_ability_score_distributions") or []
                        ),
                        "maximum_score": int(requirements.get("maximum_ability_score", 20) or 20),
                    },
                    selection.get("ability_score_increases"),
                    source=f"{base_background} background",
                )
            reviewed_tool_options = _support._reviewed_tool_options(candidates)
            if custom_name:
                selected_tools = _support._validated_distinct_choices(
                    raw_tools,
                    count=len(raw_tools),
                    label="custom background tool",
                )
                if any(item.casefold() not in reviewed_tool_options for item in selected_tools):
                    raise ValueError("custom background tool is not in the reviewed tool catalog")
                source_tools = [reviewed_tool_options[item.casefold()] for item in selected_tools]
            else:
                tool_choice_count = int(requirements.get("tool_choice_count", 0) or 0)
                selected_tools, source_tools = _support._validated_additive_choices(
                    raw_tools,
                    count=tool_choice_count,
                    label="background tool",
                    fixed=grants.get("tools") or [],
                    options=requirements.get("tool_options") or [],
                    allow_fixed_duplicates=True,
                )
                raw_tool_groups = requirements.get("tool_option_groups") or []
                if raw_tool_groups:
                    _support._validate_group_limited_choices(
                        selected_tools,
                        groups=raw_tool_groups,
                        label="background tool",
                    )
            existing_tools = {
                str(item).casefold() for item in sheet["traits"]["proficiencies"]["tools"]
            }
            try:
                all_tools, normalized_tool_replacements = _support._resolve_duplicate_proficiencies(
                    source_tools,
                    existing_values=existing_tools,
                    replacements=selection.get("tool_replacements"),
                    options=reviewed_tool_options,
                    kind="tool",
                )
            except _support._PendingProficiencyReplacementsError as error:
                return {"status": "pending_choice", "reason": str(error)}
            effective_selected_tools = all_tools[len(source_tools) - len(selected_tools) :]
            selected_skill_choices: list[str]
            if custom_name:
                if not isinstance(custom_skills_raw, list):
                    raise ValueError("custom background skills must be an array")
                selected_skills = [str(item).strip().casefold() for item in custom_skills_raw]
                if len(selected_skills) != 2 or any(not item for item in selected_skills):
                    return {
                        "status": "pending_choice",
                        "reason": "custom background requires exactly two skill choices",
                    }
                if len(set(selected_skills)) != 2:
                    raise ValueError("custom background skill choices must be distinct")
                unknown_skills = [
                    skill for skill in selected_skills if skill not in sheet["skills"]
                ]
                if unknown_skills:
                    raise ValueError(
                        "custom background references unknown skills: " + ", ".join(unknown_skills)
                    )
                selected_skill_choices = list(selected_skills)
            else:
                selected_skill_choices, selected_skills = _support._validated_additive_choices(
                    custom_skills_raw,
                    count=skill_choice_count,
                    label="background skill",
                    fixed=declared_skills,
                    options=requirements.get("skill_options") or [],
                    allow_fixed_duplicates=True,
                )
                selected_skill_choices = [skill.casefold() for skill in selected_skill_choices]
                selected_skills = [skill.casefold() for skill in selected_skills]
                unknown_skills = [
                    skill for skill in selected_skills if skill not in sheet["skills"]
                ]
                if unknown_skills:
                    raise ValueError(
                        "background references unknown skills: " + ", ".join(unknown_skills)
                    )
            skill_options = {skill.casefold(): skill.casefold() for skill in sheet["skills"]}
            existing_skills = {
                skill.casefold()
                for skill, state in sheet["skills"].items()
                if str(state.get("proficiency") or "none") != "none"
            }
            try:
                effective_skills, normalized_skill_replacements = (
                    _support._resolve_duplicate_proficiencies(
                        selected_skills,
                        existing_values=existing_skills,
                        replacements=selection.get("skill_replacements"),
                        options=skill_options,
                        kind="skill",
                    )
                )
            except _support._PendingProficiencyReplacementsError as error:
                return {"status": "pending_choice", "reason": str(error)}

            feature_source_artifact_id = artifact_id
            feature_source_identity = {
                "artifact_id": artifact_id,
                "pack_id": pack_id,
                "pack_version": version,
                "content_hash": _support.content_fingerprint(artifact),
            }
            custom_feature_artifact_id = str(
                selection.get("custom_feature_artifact_id") or ""
            ).strip()
            if custom_feature_artifact_id:
                if not custom_name:
                    raise ValueError("feature replacement requires a custom background")
                feature_candidates = [
                    item
                    for item in candidates
                    if item[2].get("kind") == "background"
                    and str(item[2].get("id") or "") == custom_feature_artifact_id
                ]
                if len(feature_candidates) != 1:
                    raise _support.RulesetUnavailableError(
                        "custom background feature artifact is unavailable or ambiguous"
                    )
                feature_matches = self.source_scoped_content_matches(
                    feature_candidates,
                    source_pack_id=pack_id,
                    source_pack_version=version,
                )
                if len(feature_matches) != 1:
                    raise _support.RulesetUnavailableError(
                        "custom background feature artifact is unavailable or ambiguous"
                    )
                feature_match = feature_matches[0]
                feature_application_state = str(
                    feature_match[2].get("application_state") or "selection_ready"
                )
                if feature_application_state != "selection_ready":
                    raise _support.RulesetUnavailableError(
                        "custom background feature artifact is not selection-ready"
                    )
                feature_contract = feature_match[2].get("selection_contract")
                if isinstance(feature_contract, dict) and _support.selection_contract_errors(
                    feature_match[2]
                ):
                    raise _support.RulesetUnavailableError(
                        "custom background feature artifact has an invalid reviewed contract"
                    )
                feature_card = dict(feature_match[2].get("card") or {})
                feature_grants = dict(feature_card.get("background_grants") or {})
                replacement_feature = str(feature_grants.get("feature") or "").strip()
                if not replacement_feature:
                    raise _support.RulesetUnavailableError(
                        "custom background feature artifact has no reviewed feature"
                    )
                grants["feature"] = replacement_feature
                feature_source_artifact_id = custom_feature_artifact_id
                feature_source_identity = {
                    "artifact_id": custom_feature_artifact_id,
                    "pack_id": feature_match[0],
                    "pack_version": feature_match[1],
                    "content_hash": _support.content_fingerprint(feature_match[2]),
                }
            equipment_packages = dict(requirements.get("equipment_packages") or {})
            equipment_mode = str(selection.get("equipment_mode") or "").strip().casefold()
            _support._require_authoritative_class_equipment(
                sheet, character_id=current.id, secret=self.content_authority_secret
            )
            class_gold_excludes_equipment = _support._class_gold_replaces_background(sheet)
            if class_gold_excludes_equipment:
                if (
                    equipment_mode not in {"", "starting_coin"}
                    or selection.get("equipment_package")
                    or selection.get("equipment_item_ids") not in (None, [])
                ):
                    raise ValueError("class starting gold cannot stack background equipment")
                equipment_mode = "class_starting_gold"
                fixed_equipment = {}
                equipment_packages = {}
            elif custom_name:
                if equipment_mode not in {"source", "starting_coin"}:
                    return {
                        "status": "pending_choice",
                        "reason": (
                            "custom background requires equipment_mode source or starting_coin"
                        ),
                    }
                if equipment_mode == "starting_coin":
                    if selection.get("equipment_package") or selection.get(
                        "equipment_item_ids"
                    ) not in (None, []):
                        raise ValueError(
                            "custom background starting_coin cannot stack source equipment"
                        )
                    fixed_equipment = {}
                    equipment_packages = {}
                elif not fixed_equipment and not equipment_packages:
                    return {
                        **_support._ruling_status(
                            "pending_ruling",
                            "missing_or_conflicting_source_review",
                        ),
                        "reason": "source background equipment is not structurally executable",
                    }
            elif equipment_mode:
                raise ValueError("equipment_mode is accepted only for a custom background")
            else:
                equipment_mode = "source"
            selected_equipment_package = (
                str(selection.get("equipment_package") or "").strip().upper()
            )
            equipment_item_ids: list[str] = []
            equipment_award_wallet: dict[str, int] = {}
            if equipment_packages:
                if not selected_equipment_package:
                    return {
                        "status": "pending_choice",
                        "reason": "background requires equipment package A or B",
                    }
                package = equipment_packages.get(selected_equipment_package)
                if not isinstance(package, dict):
                    raise ValueError("background equipment_package is not A or B")
                if fixed_equipment:
                    fixed_wallet = dict(fixed_equipment.get("wallet") or {})
                    selected_wallet = dict(package.get("wallet") or {})
                    package = {
                        "items": [
                            *_support.deepcopy(list(fixed_equipment.get("items") or [])),
                            *_support.deepcopy(list(package.get("items") or [])),
                        ],
                        "wallet": {
                            denomination: int(fixed_wallet.get(denomination, 0) or 0)
                            + int(selected_wallet.get(denomination, 0) or 0)
                            for denomination in set(fixed_wallet) | set(selected_wallet)
                        },
                    }
            elif fixed_equipment:
                if selected_equipment_package:
                    raise ValueError("background has no selectable equipment package")
                selected_equipment_package = "__FIXED__"
                package = fixed_equipment
                equipment_packages = {selected_equipment_package: package}
            if equipment_packages:
                if selection.get("equipment_item_ids") not in (None, []):
                    raise ValueError(
                        "structured background equipment cannot use caller-supplied item ids"
                    )
                raw_items = package.get("items") or []
                if not isinstance(raw_items, list):
                    raise _support.RulesetUnavailableError(
                        "background equipment package items are not executable"
                    )
                for raw_item in raw_items:
                    if not isinstance(raw_item, dict):
                        raise _support.RulesetUnavailableError(
                            "background equipment package item is not structured"
                        )
                    embedded_template = raw_item.get("inventory_template")
                    item_sources = [
                        bool(str(raw_item.get("artifact_id") or "").strip()),
                        raw_item.get("selected_tool") is True,
                        isinstance(embedded_template, dict),
                    ]
                    if sum(item_sources) != 1:
                        raise _support.RulesetUnavailableError(
                            "background equipment package item needs exactly one reviewed source"
                        )
                    inventory_template: dict[str, Any]
                    if isinstance(embedded_template, dict):
                        inventory_template = _support.deepcopy(embedded_template)
                    elif raw_item.get("selected_tool") is True:
                        if len(effective_selected_tools) != 1:
                            raise _support.RulesetUnavailableError(
                                "background equipment package requires its selected tool"
                            )
                        item_match = next(
                            (
                                item
                                for item in candidates
                                if item[2].get("kind") == "item"
                                and str(
                                    dict(item[2].get("card") or {}).get("name") or ""
                                ).casefold()
                                == effective_selected_tools[0].casefold()
                            ),
                            None,
                        )
                    else:
                        requested_item_id = str(raw_item.get("artifact_id") or "")
                        item_match = next(
                            (
                                item
                                for item in candidates
                                if item[2].get("kind") == "item"
                                and str(item[2].get("id") or "") == requested_item_id
                            ),
                            None,
                        )
                    if not isinstance(embedded_template, dict):
                        if item_match is None:
                            raise _support.RulesetUnavailableError(
                                "background equipment item is absent from the active catalog"
                            )
                        item_artifact = item_match[2]
                        inventory_template = _support.deepcopy(
                            dict(
                                dict(item_artifact.get("card") or {}).get("inventory_template")
                                or {}
                            )
                        )
                        if not inventory_template:
                            raise _support.RulesetUnavailableError(
                                "background equipment item has no executable inventory template"
                            )
                    quantity = raw_item.get("quantity", 1)
                    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                        raise _support.RulesetUnavailableError(
                            "background equipment quantity is invalid"
                        )
                    inventory_template["quantity"] = quantity
                    display_name = str(raw_item.get("display_name") or "").strip()
                    if display_name:
                        inventory_template["name"] = display_name
                    sheet, item_id = _support.add_inventory_item(sheet, inventory_template)
                    equipment_item_ids.append(item_id)
                wallet = package.get("wallet") or {}
                if not isinstance(wallet, dict):
                    raise _support.RulesetUnavailableError(
                        "background equipment package wallet is not executable"
                    )
                for denomination, amount in wallet.items():
                    normalized_denomination = str(denomination).casefold()
                    if normalized_denomination not in _support.DENOMINATIONS:
                        raise _support.RulesetUnavailableError(
                            "background equipment package has an unknown denomination"
                        )
                    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                        raise _support.RulesetUnavailableError(
                            "background equipment package currency is invalid"
                        )
                    equipment_award_wallet[normalized_denomination] = amount
                    if amount:
                        sheet = _support.adjust_wallet(sheet, normalized_denomination, amount)
            else:
                equipment_item_ids_raw = selection.get("equipment_item_ids", [])
                if custom_name and equipment_item_ids_raw not in (None, []):
                    raise ValueError(
                        "custom background cannot use caller-supplied equipment item ids"
                    )
                if not isinstance(equipment_item_ids_raw, list):
                    raise ValueError("background equipment_item_ids must be an array")
                equipment_item_ids = [str(item).strip() for item in equipment_item_ids_raw]
                if any(not item for item in equipment_item_ids):
                    raise ValueError("background equipment item ids must not be empty")
                if len(equipment_item_ids) != len(set(equipment_item_ids)):
                    raise ValueError("background equipment item ids must be distinct")
                inventory_item_ids = {str(item["id"]) for item in sheet["inventory"]["items"]}
                missing_equipment = [
                    item_id for item_id in equipment_item_ids if item_id not in inventory_item_ids
                ]
                if missing_equipment:
                    raise ValueError(
                        "background equipment references unknown inventory items: "
                        + ", ".join(missing_equipment)
                    )
            resolved_expansion, unresolved_spell_name = resolve_spell_list_expansion(
                grants.get("spell_list_expansion", []),
                source_label="background",
            )
            if unresolved_spell_name is not None:
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": (
                        "background spell-list expansion needs one exact active "
                        f"spell artifact: {unresolved_spell_name}"
                    ),
                }
            sheet["progression"]["background"] = selected_background
            grants["languages"] = all_languages
            grants["spell_list_expansion"] = resolved_expansion
            grants["tools"] = all_tools
            grants["equipment_item_ids"] = equipment_item_ids
            grants["choices"] = {
                **requirements,
                "base_background": base_background,
                "customized": bool(custom_name),
                "selected_skills": selected_skills,
                "selected_skill_choices": selected_skill_choices,
                "effective_skills": effective_skills,
                "skill_replacements": normalized_skill_replacements,
                "ability_score_increases": selected_ability_increases,
                "selected_tools": selected_tools,
                "effective_tools": all_tools,
                "tool_replacements": normalized_tool_replacements,
                "selected_languages": selected_languages,
                "language_authorization": language_authorization_receipt,
                "feature_source_artifact_id": feature_source_artifact_id,
                "feature_source": _support.deepcopy(feature_source_identity),
                "equipment_mode": equipment_mode,
                "starting_equipment_award": {
                    "items": [
                        _support.deepcopy(item)
                        for item in sheet["inventory"]["items"]
                        if equipment_packages and item["id"] in equipment_item_ids
                    ],
                    "wallet": equipment_award_wallet,
                },
                "selected_equipment_package": (
                    "" if selected_equipment_package == "__FIXED__" else selected_equipment_package
                ),
            }
            sheet["progression"]["background_grants"] = {
                **sheet["progression"]["background_grants"],
                **grants,
            }
            sheet["traits"]["languages"] = list(
                dict.fromkeys([*sheet["traits"]["languages"], *all_languages])
            )
            sheet["traits"]["proficiencies"]["tools"] = list(
                dict.fromkeys(
                    [
                        *sheet["traits"]["proficiencies"]["tools"],
                        *list(grants.get("tools") or []),
                    ]
                )
            )
            for skill_key in effective_skills:
                if skill_key not in sheet["skills"]:
                    raise ValueError(f"background references an unknown skill: {skill_key}")
                sheet["skills"][skill_key]["proficiency"] = "proficient"
            if watchers_eye_binding is not None:
                feature_card = self.watchers_eye_feature_card(watchers_eye_binding)
                if any(
                    str(item.get("id") or "") == feature_card["id"]
                    for item in sheet["content"]["features"]
                ):
                    raise ValueError("Watcher's Eye is already present")
                sheet["content"]["features"].append(feature_card)
            origin_feat_name = str(requirements.get("origin_feat_name") or "")
            if origin_feat_name:
                origin_feat_match = next(
                    (
                        item
                        for item in candidates
                        if item[2].get("kind") == "feat"
                        and str(dict(item[2].get("card") or {}).get("name") or "").casefold()
                        == origin_feat_name.casefold()
                    ),
                    None,
                )
                if origin_feat_match is None:
                    raise _support.RulesetUnavailableError(
                        "background origin feat is absent from the active catalog"
                    )
                origin_feat_selection = selection.get("origin_feat_selection") or {}
                if not isinstance(origin_feat_selection, dict):
                    raise ValueError("background origin_feat_selection must be an object")
                if origin_feat_name == "Magic Initiate":
                    origin_preset = dict(requirements.get("origin_feat_preset") or {})
                    if not str(origin_preset.get("source_class") or "").strip():
                        raise _support.RulesetUnavailableError(
                            "background Magic Initiate is missing its source class"
                        )
                    materialize_feat(
                        origin_feat_match,
                        {
                            "magic_initiate": {
                                **origin_preset,
                                **origin_feat_selection,
                            }
                        },
                        source=f"{base_background} background",
                    )
                else:
                    if origin_feat_selection:
                        raise ValueError("this background origin feat does not accept a selection")
                    materialize_feat(
                        origin_feat_match,
                        {},
                        source=f"{base_background} background",
                    )
            selection = {
                "custom_name": custom_name,
                "skills": list(selected_skill_choices),
                "languages": list(selected_languages),
                "tools": list(selected_tools),
                "skill_replacements": _support.deepcopy(normalized_skill_replacements),
                "tool_replacements": _support.deepcopy(normalized_tool_replacements),
                "custom_feature_artifact_id": custom_feature_artifact_id,
                "equipment_mode": equipment_mode,
                "equipment_package": (
                    "" if selected_equipment_package == "__FIXED__" else selected_equipment_package
                ),
                "equipment_item_ids": list(equipment_item_ids),
                "ability_score_increases": _support.deepcopy(selected_ability_increases),
                "origin_feat_selection": _support.deepcopy(
                    selection.get("origin_feat_selection") or {}
                ),
                "language_authorization": _support.deepcopy(language_authorization_receipt),
            }
        elif kind == "species":
            selected_species = str(card.get("name") or artifact_id)
            constitution_score_before = int(sheet["abilities"]["constitution"]["score"])
            base_species = str(card.get("base_species") or selected_species)
            existing_species = str(sheet["progression"].get("species") or "")
            if (
                existing_species
                and existing_species.casefold()
                not in {
                    selected_species.casefold(),
                    base_species.casefold(),
                }
                and replacing_selection is None
            ):
                raise ValueError("character already has a different species")
            if any(
                item.get("artifact_id") == artifact_id for item in sheet["content"]["selections"]
            ):
                raise ValueError("content species is already present")
            grants = dict(card.get("grants") or {})
            if grants.get("unresolved"):
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": "species has unresolved structured grants",
                    "missing": list(grants.get("unresolved") or []),
                }
            selected_languages, all_languages = _support._validated_additive_choices(
                selection.get("languages"),
                count=int(grants.get("language_choice_count", 0) or 0),
                label="species language",
                fixed=grants.get("languages") or [],
                options=grants.get("language_options") or [],
                allow_unlisted=grants.get("allow_any_language") is True,
            )
            selected_skills_raw, all_skills_raw = _support._validated_additive_choices(
                selection.get("skills"),
                count=int(grants.get("skill_choice_count", 0) or 0),
                label="species skill",
                fixed=grants.get("skill_proficiencies") or [],
                options=grants.get("skill_options") or [],
                allow_unlisted=grants.get("allow_any_skill") is True,
            )
            selected_skills = [item.casefold().replace(" ", "_") for item in selected_skills_raw]
            all_skills = [item.casefold().replace(" ", "_") for item in all_skills_raw]
            for skill in all_skills:
                if skill not in sheet["skills"]:
                    raise ValueError(f"species references an unknown skill: {skill}")
            selected_tools, all_tools = _support._validated_additive_choices(
                selection.get("tools"),
                count=int(grants.get("tool_choice_count", 0) or 0),
                label="species tool",
                fixed=grants.get("tool_proficiencies") or [],
                options=(grants.get("tool_options") or grants.get("tool_choices") or []),
            )
            proficiency_choices = _support._validated_species_proficiency_choices(
                selection.get("proficiency_choices"),
                groups=grants.get("proficiency_choice_groups", []),
            )
            narrative_choices = _support._validated_narrative_choices(
                selection.get("feature_choices"),
                groups=grants.get("narrative_choice_groups", []),
            )
            feat_requirement = dict(grants.get("feat_choice") or {})
            raw_species_feat = selection.get("feat_selection")
            species_feat_match: tuple[str, str, dict[str, Any]] | None = None
            species_feat_selection: dict[str, Any] = {}
            if feat_requirement:
                if not isinstance(raw_species_feat, dict):
                    return {
                        "status": "pending_choice",
                        "reason": "species requires one reviewed feat selection",
                    }
                if set(raw_species_feat) != {"artifact_id", "selection"}:
                    raise ValueError("species feat_selection requires artifact_id and selection")
                species_feat_id = str(raw_species_feat.get("artifact_id") or "").strip()
                raw_nested_selection = raw_species_feat.get("selection")
                if not species_feat_id or not isinstance(raw_nested_selection, dict):
                    raise ValueError(
                        "species feat_selection requires an artifact id and object selection"
                    )
                feat_matches = [
                    item
                    for item in candidates
                    if item[2].get("kind") == "feat"
                    and str(item[2].get("id") or "") == species_feat_id
                ]
                if len(feat_matches) != 1:
                    raise _support.RulesetUnavailableError(
                        "species feat must resolve to exactly one active feat artifact"
                    )
                species_feat_match = feat_matches[0]
                allowed_categories = {
                    str(item).strip().casefold()
                    for item in feat_requirement.get("allowed_categories", [])
                    if str(item).strip()
                }
                feat_category = (
                    str(dict(species_feat_match[2].get("card") or {}).get("category") or "")
                    .strip()
                    .casefold()
                )
                if allowed_categories and feat_category not in allowed_categories:
                    raise ValueError("species feat category is not allowed")
                species_feat_selection = _support.deepcopy(raw_nested_selection)
            elif raw_species_feat is not None:
                raise ValueError("species does not grant a feat choice")
            affinity_requirement = dict(grants.get("damage_affinity_choice") or {})
            raw_damage_affinity = selection.get("damage_affinity")
            selected_affinity: dict[str, Any] | None = None
            if affinity_requirement:
                affinity_id = str(raw_damage_affinity or "").strip().casefold()
                if not affinity_id:
                    return {
                        "status": "pending_choice",
                        "reason": "species requires one reviewed damage affinity",
                    }
                affinity_matches = [
                    dict(option)
                    for option in affinity_requirement.get("options", [])
                    if str(dict(option).get("id") or "").strip().casefold() == affinity_id
                ]
                if len(affinity_matches) != 1:
                    raise ValueError("species damage_affinity is not a reviewed option")
                selected_affinity = affinity_matches[0]
            elif raw_damage_affinity is not None:
                raise ValueError("species does not accept a damage affinity")
            grouped = [option for choices in proficiency_choices.values() for option in choices]
            grouped_languages = [
                option["name"] for option in grouped if option["kind"] == "language"
            ]
            grouped_skills = [
                option["name"].casefold().replace(" ", "_")
                for option in grouped
                if option["kind"] == "skill"
            ]
            grouped_tools = [option["name"] for option in grouped if option["kind"] == "tool"]
            grouped_weapons = [option["name"] for option in grouped if option["kind"] == "weapon"]
            for existing, additions, label in (
                (all_languages, grouped_languages, "language"),
                (all_skills, grouped_skills, "skill"),
                (all_tools, grouped_tools, "tool"),
                (list(grants.get("weapon_proficiencies") or []), grouped_weapons, "weapon"),
            ):
                if {str(item).casefold() for item in existing}.intersection(
                    str(item).casefold() for item in additions
                ):
                    raise ValueError(f"species proficiency choice cannot duplicate a fixed {label}")
            all_languages.extend(grouped_languages)
            all_skills.extend(grouped_skills)
            all_tools.extend(grouped_tools)
            all_weapons = [
                *list(grants.get("weapon_proficiencies") or []),
                *grouped_weapons,
            ]
            all_armor = list(grants.get("armor_proficiencies") or [])
            for skill in grouped_skills:
                if skill not in sheet["skills"]:
                    raise ValueError(f"species references an unknown skill: {skill}")
            selected_tool_expertise = _support._validated_distinct_choices(
                selection.get("tool_expertise"),
                count=int(grants.get("tool_expertise_choice_count", 0) or 0),
                label="species tool expertise",
            )
            known_tool_map = {
                str(item).casefold(): str(item)
                for item in [
                    *sheet["traits"]["proficiencies"]["tools"],
                    *all_tools,
                ]
            }
            expertise_options = {
                str(item).casefold() for item in grants.get("tool_expertise_options", [])
            }
            if expertise_options and any(
                item.casefold() not in expertise_options for item in selected_tool_expertise
            ):
                raise ValueError("species tool expertise is not one of the allowed options")
            if (
                selected_tool_expertise
                and not expertise_options
                and grants.get("allow_any_proficient_tool_expertise") is not True
            ):
                raise _support.RulesetUnavailableError(
                    "species tool expertise needs reviewed options"
                )
            if any(item.casefold() not in known_tool_map for item in selected_tool_expertise):
                raise ValueError("species tool expertise requires tool proficiency")
            selected_tool_expertise = [
                known_tool_map[item.casefold()] for item in selected_tool_expertise
            ]
            size_options = {
                str(item).strip().casefold(): str(item).strip().casefold()
                for item in grants.get("size_options", [])
                if str(item).strip()
            }
            selected_size = str(selection.get("size") or "").strip().casefold()
            if size_options:
                if not selected_size:
                    return {
                        "status": "pending_choice",
                        "reason": "species requires a size choice",
                    }
                if selected_size not in size_options:
                    raise ValueError("species size is not one of the allowed options")
            elif selected_size:
                raise ValueError("species does not accept a size choice")
            ability_choice = dict(grants.get("ability_choice") or {})
            selected_abilities = _support._validated_species_ability_choices(
                selection.get("abilities"),
                requirement=ability_choice,
                valid_abilities=sheet["abilities"],
            )
            values_include_grants = bool(selection.get("values_include_species_grants", False))
            abilities_include_grants = bool(
                selection.get("ability_scores_include_species_grants", values_include_grants)
            )
            hp_includes_grants = bool(
                selection.get("hit_points_include_species_grants", values_include_grants)
            )
            if not abilities_include_grants:
                increases = dict(grants.get("ability_score_increases") or {})
                decreases = dict(grants.get("ability_score_decreases") or {})
                for ability in selected_abilities:
                    increases[ability] = int(increases.get(ability, 0)) + int(
                        ability_choice.get("amount", 0) or 0
                    )
                for ability, amount in increases.items():
                    sheet["abilities"][ability]["score"] = int(
                        sheet["abilities"][ability]["score"]
                    ) + int(amount)
                for ability, amount in decreases.items():
                    sheet["abilities"][ability]["score"] = int(
                        sheet["abilities"][ability]["score"]
                    ) - int(amount)
                sheet = _support.apply_constitution_score_hit_point_change(
                    sheet,
                    previous_score=constitution_score_before,
                    new_score=int(sheet["abilities"]["constitution"]["score"]),
                    source=f"{selected_species}: Constitution ability score adjustment",
                    adjust_current=True,
                )
            if not hp_includes_grants:
                hp_per_level = int(grants.get("hp_per_level", 0) or 0)
                if hp_per_level:
                    features = [
                        item for item in grants.get("features", []) if isinstance(item, dict)
                    ]
                    hp_feature = next(
                        (
                            item
                            for item in features
                            if "hit point" in str(item.get("description") or "").casefold()
                            or "tough" in str(item.get("name") or "").casefold()
                        ),
                        None,
                    )
                    feature_name = str((hp_feature or {}).get("name") or "").strip()
                    hp_source = (
                        f"{selected_species}: {feature_name}"
                        if feature_name
                        else f"{selected_species}: per-level hit-point grant"
                    )
                    sheet = _support.apply_per_level_hit_point_bonus(
                        sheet,
                        amount=hp_per_level,
                        source=hp_source,
                        adjust_current=True,
                    )
            if grants.get("size") or selected_size:
                sheet["traits"]["size"] = str(grants.get("size") or selected_size)
            if int(grants.get("walk_speed", 0) or 0):
                sheet["combat"]["speed"]["walk"] = int(grants["walk_speed"])
            if int(grants.get("fly_speed", 0) or 0):
                sheet["combat"]["speed"]["fly"] = int(grants["fly_speed"])
            if int(grants.get("swim_speed", 0) or 0):
                sheet["combat"]["speed"]["swim"] = int(grants["swim_speed"])
            if int(grants.get("darkvision_ft", 0) or 0):
                sheet["traits"]["senses"]["darkvision"] = int(grants["darkvision_ft"])
            natural_armor_base = int(grants.get("natural_armor_base", 0) or 0)
            if natural_armor_base:
                includes_dexterity = grants.get("natural_armor_includes_dexterity", True)
                armor_change = (
                    {
                        "path": "combat.ac.unarmored_base",
                        "mode": "override",
                        "value": natural_armor_base,
                    }
                    if includes_dexterity
                    else {
                        "path": "combat.ac.unarmored_formula",
                        "mode": "override",
                        "value": {
                            "base": natural_armor_base,
                            "ability": None,
                            "allows_shield": True,
                            "includes_dexterity": False,
                        },
                    }
                )
                sheet, _ = _support.add_effect(
                    sheet,
                    {
                        "name": f"{selected_species} Natural Armor",
                        "kind": "feature",
                        "source": artifact_id,
                        "duration": {"period": "manual", "remaining": 0},
                        "changes": [armor_change],
                        "description": (
                            "Species alternate AC calculation while not wearing armor; "
                            "a shield applies normally."
                        ),
                    },
                )
            sheet["traits"]["languages"] = list(
                dict.fromkeys(
                    [
                        *sheet["traits"]["languages"],
                        *all_languages,
                    ]
                )
            )
            for skill in all_skills:
                _support._apply_fixed_skill_proficiency(sheet, skill, source="species")
            proficiencies = sheet["traits"]["proficiencies"]
            proficiencies["armor"] = list(dict.fromkeys([*proficiencies["armor"], *all_armor]))
            proficiencies["weapons"] = list(
                dict.fromkeys([*proficiencies["weapons"], *all_weapons])
            )
            proficiencies["tools"] = list(
                dict.fromkeys(
                    [
                        *proficiencies["tools"],
                        *all_tools,
                    ]
                )
            )
            proficiencies["tool_expertise"] = list(
                dict.fromkeys([*proficiencies["tool_expertise"], *selected_tool_expertise])
            )
            for natural_weapon in grants.get("natural_weapons") or []:
                weapon = dict(natural_weapon)
                weapon_name = str(weapon["name"]).strip()
                weapon_identity = _support.hashlib.sha256(
                    f"{artifact_id}\0{weapon_name.casefold()}".encode("utf-8")
                ).hexdigest()[:16]
                intrinsic_attack = {
                    "id": f"species-natural-weapon-{weapon_identity}",
                    "name": weapon_name,
                    "attack_ability": str(weapon["attack_ability"]).casefold(),
                    "damage_formula": str(weapon["damage_formula"]).casefold(),
                    "damage_type": str(weapon["damage_type"]).casefold(),
                    "reach_ft": int(weapon.get("reach_ft", 5)),
                    "source": {
                        "artifact_id": artifact_id,
                        "pack_id": pack_id,
                        "pack_version": version,
                        "rule_refs": list(artifact.get("rule_refs") or []),
                    },
                }
                if any(
                    str(item.get("id") or "") == intrinsic_attack["id"]
                    for item in sheet["traits"]["intrinsic_attacks"]
                ):
                    raise ValueError("species intrinsic attack is already present")
                sheet["traits"]["intrinsic_attacks"].append(intrinsic_attack)
            sheet["traits"]["resistances"] = list(
                dict.fromkeys(
                    [
                        *sheet["traits"]["resistances"],
                        *list(grants.get("resistances") or []),
                        *(
                            [str(selected_affinity["damage_type"]).casefold()]
                            if selected_affinity is not None
                            and affinity_requirement.get("resistance") is True
                            else []
                        ),
                    ]
                )
            )
            sheet["traits"]["immunities"] = list(
                dict.fromkeys(
                    [*sheet["traits"]["immunities"], *list(grants.get("immunities") or [])]
                )
            )
            sheet["traits"]["condition_immunities"] = list(
                dict.fromkeys(
                    [
                        *sheet["traits"]["condition_immunities"],
                        *list(grants.get("condition_immunities") or []),
                    ]
                )
            )
            cantrip_id = str(selection.get("cantrip_artifact_id") or "")
            cantrip_requirement = dict(grants.get("cantrip_choice") or {})
            if cantrip_requirement:
                class_name = str(cantrip_requirement.get("class") or "")
                default_ability = {
                    "artificer": "intelligence",
                    "bard": "charisma",
                    "cleric": "wisdom",
                    "druid": "wisdom",
                    "sorcerer": "charisma",
                    "warlock": "charisma",
                    "wizard": "intelligence",
                }.get(class_name.casefold(), "")
                normalized_cantrip_grant = {
                    "level": int(cantrip_requirement.get("level", 0) or 0),
                    "eligible_classes": [class_name],
                    "method": str(cantrip_requirement.get("method") or "known"),
                    "spellcasting_ability": str(
                        cantrip_requirement.get("spellcasting_ability") or default_ability
                    ).casefold(),
                    "free_casts": int(cantrip_requirement.get("free_casts", 0) or 0),
                    "recovers_on": cantrip_requirement.get("recovers_on"),
                    "allow_slot_cast": cantrip_requirement.get("allow_slot_cast") is True,
                    "minimum_level": int(cantrip_requirement.get("minimum_level", 1) or 1),
                    "ritual_only": cantrip_requirement.get("ritual_only") is True,
                }
                materialize_content_spell(
                    content_spell_match(
                        normalized_cantrip_grant,
                        artifact_id=cantrip_id,
                    ),
                    normalized_cantrip_grant,
                    source_type="species",
                    source_key=selected_species,
                    resource_discriminator="selected-cantrip",
                )
            elif cantrip_id:
                raise ValueError("species does not grant a cantrip choice")
            fixed_species_spell_grants = []
            for index, raw_grant in enumerate(grants.get("spell_grants", [])):
                spell_grant = dict(raw_grant)
                fixed_species_spell_grants.append(
                    materialize_content_spell(
                        content_spell_match(spell_grant),
                        spell_grant,
                        source_type="species",
                        source_key=selected_species,
                        resource_discriminator=f"fixed-{index}",
                    )
                )
            affinity_activity_id = ""
            if selected_affinity is not None:
                activity_template = dict(affinity_requirement.get("activity") or {})
                activity_name = str(activity_template.get("name") or "").strip()
                affinity_activity_id = (
                    f"{artifact_id}.activity."
                    f"{_support.ascii_slug(str(activity_template.get('id') or activity_name))}"
                )
                if any(
                    str(item.get("id") or "") == affinity_activity_id
                    for item in sheet["content"]["activities"]
                ):
                    raise ValueError("species damage-affinity activity is already present")
                area = _support.deepcopy(dict(selected_affinity.get("area") or {}))
                shape = str(area.get("shape") or "").casefold()
                damage_type = str(selected_affinity.get("damage_type") or "").casefold()
                source_excerpt = (
                    f"{activity_name}: {shape} {damage_type} damage; save DC equals "
                    "8 + Constitution modifier + proficiency bonus; half damage on success."
                )
                uses = dict(activity_template.get("uses") or {})
                activity = {
                    "id": affinity_activity_id,
                    "name": activity_name,
                    "source_key": selected_species,
                    "description": source_excerpt,
                    "uses": {
                        "label": activity_name,
                        "value": int(uses.get("max", 0) or 0),
                        "max": int(uses.get("max", 0) or 0),
                        "recovers_on": str(uses.get("recovers_on") or "none"),
                        "source_key": selected_species,
                        "slot_level": 0,
                        "unlimited": False,
                    },
                    "resource_key": "",
                    "activation": {"type": "action", "cost": 1, "trigger": ""},
                    "scaling": [],
                    "resource_scaling": {},
                    "attack_scaling": {},
                    "choices": {
                        "standard_resolution": {
                            "kind": "area_save_damage",
                            "origin": {"kind": "self"},
                            "area": area,
                            "targets": "each_creature",
                            "save_ability": str(
                                selected_affinity.get("save_ability") or ""
                            ).casefold(),
                            "save_dc_formula": _support.deepcopy(
                                dict(activity_template.get("save_dc") or {})
                            ),
                            "damage_formula_by_level": _support.deepcopy(
                                dict(activity_template.get("damage_by_level") or {})
                            ),
                            "damage_type": damage_type,
                            "half_on_success": True,
                            "save_source_kind": "nonmagical_effect",
                            "source_excerpt": source_excerpt,
                        }
                    },
                    "advancement_grants": [],
                    "pack_id": pack_id,
                    "pack_version": version,
                    "rule_refs": list(artifact.get("rule_refs") or []),
                    "mechanic_refs": [_support.CORE_DRAGONBORN_BREATH_MECHANIC_ID],
                    "ruling_requirements": [],
                }
                sheet["content"]["activities"].append(activity)
            for raw_resource_key, raw_resource in dict(grants.get("resources") or {}).items():
                resource_key = str(raw_resource_key).strip()
                if not resource_key:
                    raise ValueError("species resource grant has an empty key")
                resource = _support.deepcopy(dict(raw_resource))
                resource["source_key"] = str(resource.get("source_key") or selected_species)
                existing = sheet["resources"].get(resource_key)
                if existing is not None and existing != resource:
                    raise ValueError(
                        f"species resource grant conflicts with existing resource: {resource_key}"
                    )
                if existing is None:
                    sheet["resources"][resource_key] = resource
            feature_choices = {
                "languages": selected_languages,
                "skills": selected_skills,
                "tools": selected_tools,
                "proficiency_choices": proficiency_choices,
                "feature_choices": narrative_choices,
                "tool_expertise": selected_tool_expertise,
                "abilities": selected_abilities,
                "size": selected_size,
                "cantrip_artifact_id": cantrip_id,
                "feat_selection": _support.deepcopy(raw_species_feat),
                "damage_affinity": (
                    str(selected_affinity.get("id") or "") if selected_affinity is not None else ""
                ),
                "damage_affinity_activity_id": affinity_activity_id,
                "fixed_spell_grants": fixed_species_spell_grants,
            }
            species_feature_grants = self.materialize_species_features(
                sheet,
                features=grants.get("features", []),
                species_name=selected_species,
                species_artifact_id=artifact_id,
                pack_id=pack_id,
                pack_version=version,
                rule_refs=list(artifact.get("rule_refs") or []),
                mechanic_refs=list(artifact.get("mechanic_refs") or []),
                maximum_level=int(sheet.get("progression", {}).get("level", 0) or 0),
                choices=feature_choices,
            )
            resolved_expansion, unresolved_spell_name = resolve_spell_list_expansion(
                grants.get("spell_list_expansion", []),
                source_label="species",
            )
            if unresolved_spell_name is not None:
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": (
                        "species spell-list expansion needs one exact active "
                        f"spell artifact: {unresolved_spell_name}"
                    ),
                }
            sheet["progression"]["species_grants"] = {
                "spell_list_expansion": resolved_expansion,
            }
            sheet["progression"]["species"] = selected_species
            if species_feat_match is not None:
                materialize_feat(
                    species_feat_match,
                    species_feat_selection,
                    source=f"{selected_species} species",
                )
        elif kind == "item":
            if selection and not special_item_selection:
                raise ValueError("item content selection does not accept input fields")
            if any(
                item.get("artifact_id") == artifact_id for item in sheet["content"]["selections"]
            ):
                raise ValueError("content item is already present")
            inventory_template = card.get("inventory_template")
            if item_profile is None and not isinstance(inventory_template, dict):
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": "item has no reviewed inventory_template",
                }
            item_template = (
                _support.materialize_official_item_template(
                    pack_id,
                    artifact,
                    base_weapon_template=armblade_base_template,
                    pack_version=version,
                )
                if item_profile is not None
                else _support.deepcopy(inventory_template)
            )
            if item_profile is not None and item_template is None:
                return {
                    **_support._ruling_status(
                        "pending_ruling",
                        "missing_or_conflicting_source_review",
                    ),
                    "reason": "official item has no complete reviewed weapon materialization",
                }
            item_template["source_key"] = str(
                item_template.get("source_key") or f"{pack_id}@{version}:{artifact_id}"
            )
            sheet, inventory_item_id = _support.add_inventory_item(sheet, item_template)
            recorded_selection = {
                "inventory_item_id": inventory_item_id,
                "artifact_content_hash": _support.content_fingerprint(artifact),
            }
            if item_profile is not None:
                recorded_selection["reviewed_content_hash"] = str(
                    item_profile["reviewed_content_hash"]
                )
                recorded_selection["materialized_item_hash"] = (
                    _support.materialized_item_binding_hash(
                        next(
                            item
                            for item in sheet["inventory"]["items"]
                            if item["id"] == inventory_item_id
                        )
                    )
                )
            if armblade_base_source is not None:
                recorded_selection["base_weapon_source"] = _support.deepcopy(armblade_base_source)
            sheet["content"]["selections"].append(
                {
                    "artifact_id": artifact_id,
                    "kind": kind,
                    "name": str(card.get("name") or artifact_id),
                    "pack_id": pack_id,
                    "pack_version": version,
                    "rule_refs": list(artifact.get("rule_refs") or []),
                    "mechanic_refs": list(artifact.get("mechanic_refs") or []),
                    "selection": recorded_selection,
                }
            )
        elif kind in {"feature", "activity"}:
            if kind == "feature" and str(card.get("class_name") or "").strip():
                source_matches = self.progression_feature_source_matches(
                    sheet, candidates, artifact
                )
                if not any(
                    item[0] == pack_id
                    and item[1] == version
                    and str(item[2].get("id") or "") == artifact_id
                    for item in source_matches
                ):
                    raise ValueError("feature does not belong to the selected progression source")
            if kind == "activity" and selection:
                raise ValueError("activity content selection does not accept input fields")
            if kind == "feature" and str(card.get("feature_subtype") or "") == (
                "selectable_option"
            ):
                raise ValueError(
                    "selectable feature options must be granted by their parent feature"
                )
            section = "features" if kind == "feature" else "activities"
            existing_content = next(
                (item for item in sheet["content"][section] if item.get("id") == artifact_id),
                None,
            )
            grant_level = int(selection.get("grant_level", 0) or 0)
            repeatable_levels = {
                int(value)
                for value in card.get("repeatable_selection_levels", [])
                if int(value) > 0
            }
            if kind == "feature" and not grant_level and repeatable_levels:
                grant_level = int(card.get("minimum_level", 1) or 1)
            initial_requirements = dict(
                dict(card.get("selection_requirements_by_level") or {}).get(str(grant_level))
                or card.get("selection_requirements")
                or {}
            )
            replacement_study_minutes = int(
                initial_requirements.get("replacement_study_minutes", 0) or 0
            )
            replacing_feature_selection = bool(
                existing_content is not None
                and kind == "feature"
                and replacement_study_minutes
                and selection.get("replace_existing") is True
            )
            if existing_content is not None and (
                not replacing_feature_selection
                and (kind != "feature" or grant_level not in repeatable_levels)
            ):
                raise ValueError(f"content {kind} is already present")
            # Later choice unlocks need not include the initial feature level.
            # Permit that first grant only; never turn it into a repeatable grant.
            initial_feature_grant = (
                kind == "feature"
                and existing_content is None
                and bool(repeatable_levels)
                and (
                    str(card.get("class_name") or "").strip()
                    or str(card.get("species_name") or "").strip()
                )
                and grant_level == int(card.get("minimum_level", 1) or 1)
            )
            if grant_level and grant_level not in repeatable_levels and not initial_feature_grant:
                raise ValueError("feature grant_level is not a repeatable selection level")
            if existing_content is not None and any(
                int(item.get("level", 0) or 0) == grant_level
                for item in existing_content.get("advancement_grants", [])
            ):
                raise ValueError("feature selection is already recorded for this level")
            if kind == "feature":
                declared_class = str(card.get("class_name") or "").strip()
                declared_subclass = str(card.get("subclass_name") or "").strip()
                declared_species = str(card.get("species_name") or "").strip()
                minimum_level = int(card.get("minimum_level", 1) or 1)
                target = next(
                    (
                        item
                        for item in sheet["progression"]["classes"]
                        if str(item.get("name") or "").casefold() == declared_class.casefold()
                    ),
                    None,
                )
                if declared_class and target is None:
                    raise ValueError("feature class is not on this actor card")
                if (
                    declared_species
                    and str(sheet["progression"].get("species") or "").casefold()
                    != declared_species.casefold()
                ):
                    raise ValueError("feature species is not on this actor card")
                if target is not None and int(target.get("level", 0) or 0) < minimum_level:
                    raise ValueError(
                        f"{declared_class} must reach level {minimum_level} for this feature"
                    )
                if target is not None and grant_level > int(target.get("level", 0) or 0):
                    raise ValueError("feature grant_level exceeds the actor's class level")
                if (
                    declared_species
                    and int(sheet["progression"].get("level", 0) or 0) < minimum_level
                ):
                    raise ValueError(
                        f"{declared_species} must reach level {minimum_level} for this feature"
                    )
                if declared_species and grant_level > int(
                    sheet["progression"].get("level", 0) or 0
                ):
                    raise ValueError("feature grant_level exceeds the actor's character level")
                if declared_subclass and (
                    target is None
                    or str(target.get("subclass") or "").casefold() != declared_subclass.casefold()
                ):
                    raise ValueError("feature subclass is not selected on this actor card")
                requirements = initial_requirements
                requirements = _support._feature_requirements_with_active_extensions(
                    requirements,
                    selector_card=card,
                    candidates=candidates,
                )
                selection_kind = str(requirements.get("kind") or "")
                unsupported_requirement_fields = (
                    set(requirements) - _support.SUPPORTED_FEATURE_SELECTION_REQUIREMENT_FIELDS
                )
                if unsupported_requirement_fields:
                    raise _support.RulesetUnavailableError(
                        "feature selection requirements are not executable: "
                        f"{sorted(unsupported_requirement_fields)}"
                    )
                unsupported_prerequisite_fields = {
                    field
                    for prerequisite in dict(
                        requirements.get("option_prerequisites") or {}
                    ).values()
                    for field in dict(prerequisite)
                    if field not in _support.SUPPORTED_FEATURE_OPTION_PREREQUISITE_FIELDS
                }
                if unsupported_prerequisite_fields:
                    raise _support.RulesetUnavailableError(
                        "feature option prerequisites are not executable: "
                        f"{sorted(unsupported_prerequisite_fields)}"
                    )
                if selection_kind not in _support.SUPPORTED_FEATURE_SELECTION_KINDS:
                    raise _support.RulesetUnavailableError(
                        f"feature selection kind is not executable: {selection_kind}"
                    )
                choice_field = str(requirements.get("field") or "")
                allowed_selection_fields = {"grant_level"}
                if choice_field:
                    allowed_selection_fields.add(choice_field)
                if dict(card.get("mechanical_grants") or {}).get(
                    "tool_proficiency_replacement_options"
                ):
                    allowed_selection_fields.add("tool_replacements")
                if int(dict(card.get("mechanical_grants") or {}).get("hp_per_class_level", 0) or 0):
                    allowed_selection_fields.add("initial_setup_full_hp")
                if replacing_feature_selection:
                    allowed_selection_fields.update(
                        {
                            "replace_existing",
                            "study_started_elapsed_ticks",
                        }
                    )
                unsupported_selection_fields = set(selection) - allowed_selection_fields
                if unsupported_selection_fields:
                    raise ValueError(
                        "feature selection has unsupported fields: "
                        f"{sorted(unsupported_selection_fields)}"
                    )
                if replacing_feature_selection:
                    if phase != _support.PROFILE_PLAY:
                        raise _support.CombatEngineError(
                            "feature selection replacement is available only during play"
                        )
                    study_started_ticks = selection.get("study_started_elapsed_ticks")
                    if isinstance(study_started_ticks, bool) or not isinstance(
                        study_started_ticks, int
                    ):
                        raise _support.CombatEngineError(
                            "feature replacement requires study_started_elapsed_ticks"
                        )
                    current_elapsed = dict(dict(campaign.state or {}).get("game_time") or {}).get(
                        "elapsed_ticks"
                    )
                    if isinstance(current_elapsed, bool) or not isinstance(current_elapsed, int):
                        raise _support.CombatEngineError(
                            "feature replacement requires the campaign game timeline"
                        )
                    if (
                        study_started_ticks < 0
                        or current_elapsed - study_started_ticks
                        < replacement_study_minutes * _support.TICKS_PER_MINUTE
                    ):
                        raise _support.CombatEngineError(
                            "feature replacement has not completed its required "
                            f"{replacement_study_minutes} minutes of study"
                        )
                if choice_field:
                    if requirements.get("kind") == "ability_score_increase":
                        selected_increases = selection.get(choice_field)
                        if not isinstance(selected_increases, dict) or not selected_increases:
                            raise ValueError(
                                "feature ability_score_increases must be a non-empty object"
                            )
                        normalized_increases: dict[str, int] = {}
                        ability_options = {
                            str(item).casefold() for item in requirements.get("ability_options", [])
                        }
                        for ability, amount in selected_increases.items():
                            normalized_ability = str(ability).strip().casefold()
                            if normalized_ability not in sheet["abilities"]:
                                raise ValueError(
                                    "feature ability score increase names an unknown ability"
                                )
                            if ability_options and normalized_ability not in ability_options:
                                raise ValueError(
                                    "feature ability score increase names an ineligible ability"
                                )
                            if (
                                isinstance(amount, bool)
                                or not isinstance(amount, int)
                                or amount <= 0
                            ):
                                raise ValueError(
                                    "feature ability score increases must be positive integers"
                                )
                            normalized_increases[normalized_ability] = amount
                        distribution = sorted(normalized_increases.values(), reverse=True)
                        allowed = [
                            sorted(
                                [int(amount) for amount in distribution_option],
                                reverse=True,
                            )
                            for distribution_option in requirements.get("allowed_distributions", [])
                        ]
                        if distribution not in allowed:
                            raise ValueError(
                                "feature ability score increases do not match an allowed "
                                "distribution"
                            )
                        maximum_score = int(requirements.get("maximum_score", 20) or 20)
                        previous_constitution = int(sheet["abilities"]["constitution"]["score"])
                        for ability, amount in normalized_increases.items():
                            old_score = int(sheet["abilities"][ability]["score"])
                            if old_score + amount > maximum_score:
                                raise ValueError(
                                    "feature ability score increase exceeds its maximum"
                                )
                            sheet["abilities"][ability]["score"] = old_score + amount
                        sheet = _support.apply_constitution_score_hit_point_change(
                            sheet,
                            previous_score=previous_constitution,
                            new_score=int(sheet["abilities"]["constitution"]["score"]),
                            source=(
                                f"{declared_class} level {grant_level}: Ability Score Improvement"
                            ),
                        )
                    elif requirements.get("kind") == "favored_enemy":
                        favored_enemy = selection.get(choice_field)
                        if not isinstance(favored_enemy, dict):
                            raise ValueError("feature favored_enemy must be a structured object")
                        unexpected_enemy_fields = set(favored_enemy) - {
                            "creature_type",
                            "humanoid_races",
                            "enemy_speaks_language",
                            "language",
                        }
                        if unexpected_enemy_fields:
                            raise ValueError(
                                "feature favored_enemy has unsupported fields: "
                                f"{sorted(unexpected_enemy_fields)}"
                            )
                        creature_type = str(favored_enemy.get("creature_type") or "").strip()
                        humanoid_races = _support._validated_distinct_choices(
                            favored_enemy.get("humanoid_races"),
                            count=(
                                int(requirements.get("humanoid_race_count", 2) or 2)
                                if creature_type.casefold() == "humanoid"
                                else 0
                            ),
                            label="favored enemy humanoid races",
                        )
                        creature_options = {
                            str(item).casefold()
                            for item in requirements.get("creature_type_options", [])
                        }
                        if creature_type.casefold() == "humanoid":
                            if not humanoid_races:
                                raise ValueError("humanoid favored enemy requires two races")
                        elif creature_type.casefold() not in creature_options:
                            raise ValueError("favored enemy creature_type is not an allowed option")
                        language = str(favored_enemy.get("language") or "").strip()
                        speaks_language = favored_enemy.get("enemy_speaks_language")
                        if requirements.get("language_if_spoken") and not isinstance(
                            speaks_language, bool
                        ):
                            raise ValueError(
                                "favored enemy must explicitly record whether it speaks a language"
                            )
                        if speaks_language and not language:
                            raise ValueError("favored enemy requires its associated language")
                        if not speaks_language and language:
                            raise ValueError(
                                "favored enemy language must be empty when the enemy speaks none"
                            )
                        if language:
                            known_languages = {
                                str(item).casefold() for item in sheet["traits"]["languages"]
                            }
                            if language.casefold() in known_languages:
                                raise ValueError("favored enemy language is already known")
                            sheet["traits"]["languages"].append(language)
                        normalized_enemy = (
                            "humanoid:"
                            + ",".join(sorted(item.casefold() for item in humanoid_races))
                            if creature_type.casefold() == "humanoid"
                            else creature_type.casefold()
                        )
                        prior_enemies: set[str] = set()
                        if existing_content is not None:
                            prior_choices = [
                                dict(existing_content.get("choices") or {}),
                                *[
                                    dict(item.get("choices") or {})
                                    for item in existing_content.get("advancement_grants", [])
                                ],
                            ]
                            for prior in prior_choices:
                                value = prior.get(choice_field)
                                if not isinstance(value, dict):
                                    continue
                                prior_type = str(value.get("creature_type") or "").casefold()
                                if prior_type == "humanoid":
                                    prior_races = [
                                        str(item).casefold()
                                        for item in value.get("humanoid_races", [])
                                    ]
                                    prior_enemies.add("humanoid:" + ",".join(sorted(prior_races)))
                                elif prior_type:
                                    prior_enemies.add(prior_type)
                        if normalized_enemy in prior_enemies:
                            raise ValueError("favored enemy choice was already selected")
                    elif requirements.get("kind") == "eldritch_invocations_2024":
                        raw_invocations = selection.get(choice_field)
                        required_count = int(requirements.get("count", 0) or 0)
                        if (
                            not isinstance(raw_invocations, list)
                            or len(raw_invocations) != required_count
                        ):
                            raise ValueError(
                                f"Eldritch Invocations requires exactly {required_count} choices"
                            )
                        option_names = {
                            str(item).casefold(): str(item)
                            for item in requirements.get("options", [])
                        }
                        option_ids = {
                            str(key): str(value)
                            for key, value in dict(
                                requirements.get("option_artifact_ids") or {}
                            ).items()
                        }
                        repeatable_options = {
                            str(item).casefold()
                            for item in requirements.get("repeatable_options", [])
                        }
                        prerequisite_map = dict(requirements.get("option_prerequisites") or {})
                        existing_invocations = [
                            item
                            for item in sheet["content"]["features"]
                            if str(item.get("source_key") or "") == "Eldritch Invocation"
                        ]
                        selected_keys: set[tuple[str, str]] = set()
                        normalized_invocations: list[dict[str, Any]] = []
                        batch_invocation_names = {
                            str(item.get("option") or "").casefold()
                            for item in raw_invocations
                            if isinstance(item, dict)
                        }
                        at_will_spells = {
                            "Armor of Shadows": "Mage Armor",
                            "Ascendant Step": "Levitate",
                            "Mask of Many Faces": "Disguise Self",
                            "Master of Myriad Forms": "Alter Self",
                            "Misty Visions": "Silent Image",
                            "Otherworldly Leap": "Jump",
                            "Pact of the Chain": "Find Familiar",
                            "Visions of Distant Realms": "Arcane Eye",
                            "Whispers of the Grave": "Speak with Dead",
                        }
                        for raw_invocation in raw_invocations:
                            if (
                                not isinstance(raw_invocation, dict)
                                or "option" not in raw_invocation
                            ):
                                raise ValueError("each Eldritch Invocation choice requires option")
                            unsupported_invocation_fields = set(raw_invocation) - {
                                "option",
                                "target_artifact_id",
                                "option_selection",
                            }
                            if unsupported_invocation_fields:
                                raise ValueError(
                                    "Eldritch Invocation choice has unsupported fields: "
                                    f"{sorted(unsupported_invocation_fields)}"
                                )
                            option_key = str(raw_invocation["option"]).casefold()
                            if option_key not in option_names:
                                raise ValueError("Eldritch Invocation option is unavailable")
                            option = option_names[option_key]
                            prerequisite = dict(prerequisite_map.get(option) or {})
                            if target is None or int(target.get("level", 0) or 0) < int(
                                prerequisite.get("minimum_level", 1) or 1
                            ):
                                raise ValueError(
                                    "Eldritch Invocation level prerequisite is not met"
                                )
                            required_invocation = str(
                                prerequisite.get("required_invocation") or ""
                            ).casefold()
                            known_invocation_names = (
                                {
                                    str(item.get("name") or "").casefold()
                                    for item in [*existing_invocations]
                                }
                                | batch_invocation_names
                                | {
                                    str(item.get("option") or "").casefold()
                                    for item in normalized_invocations
                                }
                            )
                            if (
                                required_invocation
                                and required_invocation not in known_invocation_names
                            ):
                                raise ValueError(
                                    "Eldritch Invocation prerequisite invocation is not known"
                                )
                            target_artifact_id = str(raw_invocation.get("target_artifact_id") or "")
                            repeatable = option_key in repeatable_options
                            if repeatable and not target_artifact_id:
                                raise ValueError(
                                    "repeatable Eldritch Invocation requires target_artifact_id"
                                )
                            if not repeatable and target_artifact_id:
                                raise ValueError(
                                    "non-repeatable Eldritch Invocation has no target artifact"
                                )
                            selection_key = (option_key, target_artifact_id)
                            prior_keys = {
                                (
                                    str(item.get("name") or "").casefold(),
                                    str(
                                        dict(item.get("choices") or {}).get("target_artifact_id")
                                        or ""
                                    ),
                                )
                                for item in existing_invocations
                            }
                            if selection_key in selected_keys or selection_key in prior_keys:
                                raise ValueError("Eldritch Invocation choice is already known")
                            if not repeatable and any(
                                option_key == key[0] for key in prior_keys | selected_keys
                            ):
                                raise ValueError(
                                    "non-repeatable Eldritch Invocation is already known"
                                )
                            option_selection = raw_invocation.get("option_selection") or {}
                            if not isinstance(option_selection, dict):
                                raise ValueError(
                                    "Eldritch Invocation option_selection must be an object"
                                )
                            if option == "Lessons of the First Ones":
                                feat_match = next(
                                    (
                                        item
                                        for item in candidates
                                        if item[2].get("kind") == "feat"
                                        and str(item[2].get("id") or "") == target_artifact_id
                                    ),
                                    None,
                                )
                                if (
                                    feat_match is None
                                    or str(
                                        dict(feat_match[2].get("card") or {}).get("category") or ""
                                    ).casefold()
                                    != "origin"
                                ):
                                    raise ValueError(
                                        "Lessons of the First Ones requires an Origin feat"
                                    )
                                materialize_feat(
                                    feat_match,
                                    option_selection,
                                    source="Lessons of the First Ones",
                                )
                            elif repeatable:
                                if option_selection:
                                    raise ValueError(
                                        "targeted blast invocation has no option_selection"
                                    )
                                spell_match = next(
                                    (
                                        item
                                        for item in candidates
                                        if item[2].get("kind") == "spell"
                                        and str(item[2].get("id") or "") == target_artifact_id
                                    ),
                                    None,
                                )
                                if spell_match is None:
                                    raise ValueError(
                                        "targeted Eldritch Invocation spell is unavailable"
                                    )
                                spell_card = dict(spell_match[2].get("card") or {})
                                actor_spell = next(
                                    (
                                        item
                                        for item in sheet["content"]["spells"]
                                        if str(item.get("id") or "") == target_artifact_id
                                    ),
                                    None,
                                )
                                if (
                                    int(spell_card.get("level", -1)) != 0
                                    or "warlock"
                                    not in {
                                        str(item).casefold()
                                        for item in spell_card.get("classes", [])
                                    }
                                    or actor_spell is None
                                    or not bool(dict(actor_spell.get("access") or {}).get("known"))
                                ):
                                    raise ValueError(
                                        "targeted invocation requires a known Warlock cantrip"
                                    )
                            elif option_selection:
                                raise ValueError(
                                    "Eldritch Invocation option does not accept option_selection"
                                )
                            option_artifact_id = option_ids[option]
                            option_match = next(
                                item
                                for item in candidates
                                if str(item[2].get("id") or "") == option_artifact_id
                            )
                            invocation_card = _support.deepcopy(
                                dict(option_match[2].get("card") or {})
                            )
                            for metadata_key in (
                                "class_name",
                                "feature_subtype",
                                "minimum_level",
                                "prerequisite_text",
                                "repeatable",
                            ):
                                invocation_card.pop(metadata_key, None)
                            invocation_card["source_key"] = "Eldritch Invocation"
                            invocation_card["choices"] = {
                                "target_artifact_id": target_artifact_id,
                                "option_selection": _support.deepcopy(option_selection),
                            }
                            invocation_card.update(
                                id=option_artifact_id,
                                pack_id=option_match[0],
                                pack_version=option_match[1],
                                rule_refs=list(option_match[2].get("rule_refs") or []),
                                mechanic_refs=list(option_match[2].get("mechanic_refs") or []),
                            )
                            sheet["content"]["features"].append(invocation_card)
                            spell_name = at_will_spells.get(option)
                            if spell_name:
                                spell_matches = self.source_scoped_content_matches(
                                    [
                                        item
                                        for item in candidates
                                        if item[2].get("kind") == "spell"
                                        and str(
                                            dict(item[2].get("card") or {}).get("name") or ""
                                        ).casefold()
                                        == spell_name.casefold()
                                    ],
                                    source_pack_id=pack_id,
                                    source_pack_version=version,
                                )
                                if len(spell_matches) != 1:
                                    raise _support.RulesetUnavailableError(
                                        f"invocation spell is unavailable: {spell_name}"
                                    )
                                materialize_feature_spell(
                                    spell_matches[0],
                                    method="eldritch_invocation",
                                    source_key=option,
                                    at_will=True,
                                    allow_existing=True,
                                )
                            selected_keys.add(selection_key)
                            normalized_invocations.append(
                                {
                                    "option": option,
                                    "target_artifact_id": target_artifact_id,
                                    "option_selection": _support.deepcopy(option_selection),
                                }
                            )
                        selection[choice_field] = normalized_invocations
                    elif requirements.get("kind") == "feature_grants":
                        selected_options = _support._validated_distinct_choices(
                            selection.get(choice_field),
                            count=int(requirements.get("count", 0) or 0),
                            label=f"feature {choice_field}",
                        )
                        option_names = {
                            str(item).casefold(): str(item)
                            for item in requirements.get("options", [])
                        }
                        option_ids = {
                            str(key): str(value)
                            for key, value in dict(
                                requirements.get("option_artifact_ids") or {}
                            ).items()
                        }
                        if set(option_ids) != set(option_names.values()):
                            raise _support.RulesetUnavailableError(
                                "feature grant options need an exact artifact id mapping"
                            )
                        option_subtype = str(
                            requirements.get("option_subtype") or "selectable_option"
                        )
                        prerequisite_map = dict(requirements.get("option_prerequisites") or {})
                        existing_ids = {
                            str(item.get("id") or "") for item in sheet["content"]["features"]
                        }
                        normalized_options: list[str] = []
                        for selected_option in selected_options:
                            option_key = selected_option.casefold()
                            if option_key not in option_names:
                                raise ValueError("feature grant option is unavailable")
                            option_name = option_names[option_key]
                            option_id = option_ids[option_name]
                            if option_id in existing_ids:
                                raise ValueError("feature grant option is already known")
                            prerequisite = dict(prerequisite_map.get(option_name) or {})
                            if target is None or int(target.get("level", 0) or 0) < int(
                                prerequisite.get("minimum_level", 1) or 1
                            ):
                                raise ValueError(
                                    "feature grant option level prerequisite is not met"
                                )
                            option_match = next(
                                (
                                    item
                                    for item in candidates
                                    if str(item[2].get("id") or "") == option_id
                                    and item[2].get("kind") == "feature"
                                ),
                                None,
                            )
                            if option_match is None:
                                raise ValueError("feature grant artifact is unavailable")
                            option_card = _support.deepcopy(dict(option_match[2].get("card") or {}))
                            if str(option_card.get("feature_subtype") or "") != (option_subtype):
                                raise ValueError(
                                    "feature grant artifact has the wrong option subtype"
                                )
                            if str(option_card.get("class_name") or "").casefold() != (
                                declared_class.casefold()
                            ):
                                raise ValueError("feature grant artifact belongs to another class")
                            for metadata_key in (
                                "class_name",
                                "subclass_name",
                                "minimum_level",
                                "feature_subtype",
                                "repeatable_selection_levels",
                                "selection_requirements",
                                "selection_requirements_by_level",
                                "mechanical_grants",
                            ):
                                option_card.pop(metadata_key, None)
                            option_card["source_key"] = str(card.get("name") or artifact_id)
                            option_card.update(
                                id=option_id,
                                pack_id=option_match[0],
                                pack_version=option_match[1],
                                rule_refs=list(option_match[2].get("rule_refs") or []),
                                mechanic_refs=list(option_match[2].get("mechanic_refs") or []),
                            )
                            sheet["content"]["features"].append(option_card)
                            existing_ids.add(option_id)
                            normalized_options.append(option_name)
                        selection[choice_field] = normalized_options
                    elif requirements.get("kind") == "feat_grant":
                        raw_feat_choice = selection.get(choice_field)
                        if not isinstance(raw_feat_choice, dict):
                            raise ValueError("feature feat_choice must be a structured object")
                        unsupported_feat_choice = set(raw_feat_choice) - {
                            "artifact_id",
                            "selection",
                        }
                        if unsupported_feat_choice:
                            raise ValueError(
                                "feature feat_choice has unsupported fields: "
                                f"{sorted(unsupported_feat_choice)}"
                            )
                        feat_artifact_id = str(raw_feat_choice.get("artifact_id") or "").strip()
                        feat_match = next(
                            (
                                item
                                for item in candidates
                                if str(item[2].get("id") or "") == feat_artifact_id
                                and item[2].get("kind") == "feat"
                            ),
                            None,
                        )
                        if feat_match is None:
                            raise ValueError("feature feat artifact is unavailable")
                        allowed_categories = {
                            str(item).casefold()
                            for item in requirements.get("allowed_categories", [])
                        }
                        feat_category = str(
                            dict(feat_match[2].get("card") or {}).get("category") or ""
                        ).casefold()
                        if allowed_categories and feat_category not in allowed_categories:
                            raise ValueError("feature feat category is not allowed")
                        feat_selection = raw_feat_choice.get("selection") or {}
                        if not isinstance(feat_selection, dict):
                            raise ValueError("feature feat_choice.selection must be an object")
                        materialize_feat(
                            feat_match,
                            feat_selection,
                            source=str(card.get("name") or artifact_id),
                        )
                    elif requirements.get("kind") == "language_grant":
                        language = str(selection.get(choice_field) or "").strip()
                        allowed_languages = {
                            str(item).casefold() for item in requirements.get("options", [])
                        }
                        if not language or language.casefold() not in allowed_languages:
                            raise ValueError("feature language is not an allowed option")
                        known_languages = {
                            str(item).casefold() for item in sheet["traits"]["languages"]
                        }
                        if language.casefold() in known_languages:
                            raise ValueError("feature language is already known")
                        sheet["traits"]["languages"].append(language)
                    elif requirements.get("kind") == "proficiency_grants":
                        selection[choice_field] = _support._materialize_feature_proficiency_groups(
                            sheet,
                            value=selection.get(choice_field),
                            groups=requirements.get("groups"),
                        )
                    elif requirements.get("kind") in {
                        "known_spell_grants",
                        "mystic_arcanum",
                        "spell_mastery",
                        "signature_spells",
                    }:
                        spell_ids = _support._validated_distinct_choices(
                            selection.get(choice_field),
                            count=int(requirements.get("count", 1) or 1),
                            label=f"feature {choice_field}",
                        )
                        spell_matches = []
                        for spell_id in spell_ids:
                            spell_match = next(
                                (
                                    item
                                    for item in candidates
                                    if str(item[2].get("id") or "") == spell_id
                                    and item[2].get("kind") == "spell"
                                ),
                                None,
                            )
                            if spell_match is None:
                                raise ValueError("feature spell artifact is unavailable")
                            spell_matches.append(spell_match)
                        spell_levels = [
                            int(dict(item[2].get("card") or {}).get("level", 0) or 0)
                            for item in spell_matches
                        ]
                        allowed_casting_times = {
                            str(item).casefold() for item in requirements.get("casting_times", [])
                        }
                        if allowed_casting_times and any(
                            str(
                                dict(
                                    dict(spell_match[2].get("card") or {}).get("definition") or {}
                                ).get("casting_time")
                                or ""
                            ).casefold()
                            not in allowed_casting_times
                            for spell_match in spell_matches
                        ):
                            raise ValueError("feature spell does not have an allowed casting time")
                        allowed_schools = {
                            str(item).casefold() for item in requirements.get("schools", [])
                        }
                        if allowed_schools and any(
                            str(
                                dict(
                                    dict(spell_match[2].get("card") or {}).get("definition") or {}
                                ).get("school")
                                or ""
                            ).casefold()
                            not in allowed_schools
                            for spell_match in spell_matches
                        ):
                            raise ValueError("feature spell is not from an allowed school")
                        eligible_class = str(requirements.get("eligible_class") or "").casefold()
                        if eligible_class and eligible_class != "any":
                            for spell_match in spell_matches:
                                eligible_classes = {
                                    str(item).casefold()
                                    for item in dict(spell_match[2].get("card") or {}).get(
                                        "classes", []
                                    )
                                }
                                if eligible_class not in eligible_classes:
                                    raise ValueError(
                                        "feature spell is not on the required class list"
                                    )
                        eligible_classes = {
                            str(item).casefold()
                            for item in requirements.get("eligible_classes", [])
                        }
                        if eligible_classes:
                            for spell_match in spell_matches:
                                spell_classes = {
                                    str(item).casefold()
                                    for item in dict(spell_match[2].get("card") or {}).get(
                                        "classes", []
                                    )
                                }
                                if not eligible_classes.intersection(spell_classes):
                                    raise ValueError(
                                        "feature spell is not on an eligible class list"
                                    )
                        feature_kind = str(requirements.get("kind") or "")
                        if feature_kind == "known_spell_grants":
                            required_spell_levels = sorted(
                                int(value)
                                for value in requirements.get("required_spell_levels", [])
                            )
                            if required_spell_levels and sorted(spell_levels) != (
                                required_spell_levels
                            ):
                                raise ValueError(
                                    "feature spells do not match the required spell levels"
                                )
                            maximum_spell_level = max(
                                [
                                    int(level)
                                    for level, resource in dict(
                                        sheet["spellcasting"].get("spell_slots") or {}
                                    ).items()
                                    if int(dict(resource).get("max", 0) or 0) > 0
                                ]
                                + [
                                    int(
                                        dict(sheet["spellcasting"].get("pact_magic") or {}).get(
                                            "slot_level", 0
                                        )
                                        or 0
                                    )
                                ]
                            )
                            if any(level > maximum_spell_level for level in spell_levels):
                                raise ValueError(
                                    "feature spell exceeds the actor's available spell level"
                                )
                            declared_maximum_value = requirements.get("maximum_spell_level", 0)
                            if declared_maximum_value == "available_slots":
                                declared_maximum = maximum_spell_level
                            elif isinstance(declared_maximum_value, bool) or not isinstance(
                                declared_maximum_value, int
                            ):
                                raise _support.RulesetUnavailableError(
                                    "feature maximum spell level expression is not executable"
                                )
                            else:
                                declared_maximum = declared_maximum_value
                            if declared_maximum and any(
                                level > declared_maximum for level in spell_levels
                            ):
                                raise ValueError(
                                    "feature spell exceeds the feature's maximum spell level"
                                )
                            source_class = str(
                                requirements.get("source_class") or declared_class
                            ).title()
                            grant_method = str(
                                requirements.get("grant_method")
                                or (
                                    "class_prepared"
                                    if requirements.get("always_prepared")
                                    else "known"
                                )
                            )
                            if grant_method not in {"known", "class_prepared", "spellbook"}:
                                raise _support.RulesetUnavailableError(
                                    "feature spell grant method is not executable"
                                )
                            for spell_match in spell_matches:
                                spell_card = materialize_feature_spell(
                                    spell_match,
                                    method=grant_method,
                                    source_key=source_class,
                                )
                                if grant_method == "spellbook":
                                    spell_id = str(spell_match[2]["id"])
                                    if (
                                        spell_id
                                        not in sheet["spellcasting"]["spellbook"]["spell_ids"]
                                    ):
                                        sheet["spellcasting"]["spellbook"]["spell_ids"].append(
                                            spell_id
                                        )
                                if requirements.get("always_prepared"):
                                    spell_card.setdefault("access", {})["always_prepared"] = True
                                    spell_card["access"]["prepared"] = True
                        elif feature_kind == "mystic_arcanum":
                            required_level = int(requirements.get("spell_level", 0) or 0)
                            if spell_levels != [required_level]:
                                raise ValueError("Mystic Arcanum requires the exact spell level")
                            spell_card = materialize_feature_spell(
                                spell_matches[0],
                                method="mystic_arcanum",
                                source_key="Warlock",
                            )
                            resource_key = f"mystic_arcanum:{spell_matches[0][2]['id']}"
                            sheet["resources"][resource_key] = {
                                "label": (
                                    f"Mystic Arcanum: {spell_card.get('name') or spell_ids[0]}"
                                ),
                                "value": 1,
                                "max": 1,
                                "recovers_on": "long_rest",
                                "source_key": "Warlock",
                            }
                        elif feature_kind == "spell_mastery":
                            required_levels = sorted(
                                int(value)
                                for value in requirements.get("required_spell_levels", [])
                            )
                            if sorted(spell_levels) != required_levels:
                                raise ValueError(
                                    "Spell Mastery requires one 1st-level and one 2nd-level spell"
                                )
                            spellbook_ids = set(
                                sheet["spellcasting"]["spellbook"].get("spell_ids", [])
                            )
                            if not set(spell_ids).issubset(spellbook_ids):
                                raise ValueError("Spell Mastery choices must be in the spellbook")
                            if replacing_feature_selection:
                                previous_spell_ids = [
                                    str(item)
                                    for item in dict(existing_content.get("choices") or {}).get(
                                        choice_field, []
                                    )
                                ]
                                if set(previous_spell_ids) == set(spell_ids):
                                    raise ValueError(
                                        "Spell Mastery replacement must exchange "
                                        "at least one selected spell"
                                    )
                                mastery_source = "spell_mastery:Wizard"
                                for previous_spell_id in set(previous_spell_ids) - set(spell_ids):
                                    previous_spell = next(
                                        (
                                            item
                                            for item in sheet["content"]["spells"]
                                            if str(item.get("id") or "") == previous_spell_id
                                        ),
                                        None,
                                    )
                                    if previous_spell is None:
                                        raise ValueError(
                                            "recorded Spell Mastery choice is "
                                            "missing from the actor card"
                                        )
                                    previous_access = previous_spell.setdefault("access", {})
                                    sources = [
                                        str(item)
                                        for item in previous_access.get("at_will_sources", [])
                                        if str(item) != mastery_source
                                    ]
                                    previous_access["at_will_sources"] = sources
                                    if sources:
                                        previous_access["at_will"] = True
                                    elif (
                                        str(
                                            dict(previous_spell.get("grant") or {}).get("method")
                                            or ""
                                        )
                                        != "eldritch_invocation"
                                    ):
                                        previous_access["at_will"] = False
                            for spell_match in spell_matches:
                                materialize_feature_spell(
                                    spell_match,
                                    method="spell_mastery",
                                    source_key="Wizard",
                                    at_will=True,
                                    allow_existing=True,
                                )
                        else:
                            required_levels = sorted(
                                int(value)
                                for value in requirements.get("required_spell_levels", [])
                            )
                            if sorted(spell_levels) != required_levels:
                                raise ValueError("Signature Spells requires two 3rd-level spells")
                            spellbook_ids = set(
                                sheet["spellcasting"]["spellbook"].get("spell_ids", [])
                            )
                            if not set(spell_ids).issubset(spellbook_ids):
                                raise ValueError("Signature Spell choices must be in the spellbook")
                            for spell_match in spell_matches:
                                spell_card = materialize_feature_spell(
                                    spell_match,
                                    method="signature_spells",
                                    source_key="Wizard",
                                    allow_existing=True,
                                )
                                access = spell_card.setdefault("access", {})
                                access["always_prepared"] = True
                                access["prepared"] = True
                                spell_id = str(spell_match[2]["id"])
                                sheet["resources"][f"signature_spell:{spell_id}"] = {
                                    "label": (
                                        f"Signature Spell: {spell_card.get('name') or spell_id}"
                                    ),
                                    "value": 1,
                                    "max": 1,
                                    "unlimited": False,
                                    "recovers_on": "short_rest",
                                    "source_key": "Wizard",
                                }
                    elif requirements.get("kind") == "bonus_cantrip":
                        spell_artifact_id = str(selection.get(choice_field) or "").strip()
                        spell_match = next(
                            (
                                item
                                for item in candidates
                                if str(item[2].get("id") or "") == spell_artifact_id
                            ),
                            None,
                        )
                        if spell_match is None:
                            raise ValueError("bonus cantrip spell_artifact_id is unavailable")
                        spell_pack_id, spell_version, spell_artifact = spell_match
                        spell_card = _support.deepcopy(dict(spell_artifact.get("card") or {}))
                        eligible_class = str(
                            requirements.get("eligible_class") or declared_class
                        ).casefold()
                        if (
                            spell_artifact.get("kind") != "spell"
                            or int(spell_card.get("level", -1))
                            != int(requirements.get("spell_level", 0) or 0)
                            or eligible_class
                            not in {str(item).casefold() for item in spell_card.get("classes", [])}
                        ):
                            raise ValueError("bonus cantrip does not meet its class and level rule")
                        if any(
                            item.get("id") == spell_artifact_id
                            for item in sheet["content"]["spells"]
                        ):
                            raise ValueError("bonus cantrip is already present")
                        spell_card = _support._character_spell_card(spell_card)
                        spell_card["grant"] = {
                            "source_type": "subclass",
                            "source_key": declared_subclass or declared_class,
                            "method": "known",
                        }
                        spell_card.setdefault("access", {})["known"] = True
                        spell_card["access"]["prepared"] = False
                        spell_card.update(
                            id=spell_artifact_id,
                            pack_id=spell_pack_id,
                            pack_version=spell_version,
                            rule_refs=list(spell_artifact.get("rule_refs") or []),
                            mechanic_refs=list(spell_artifact.get("mechanic_refs") or []),
                        )
                        sheet["content"]["spells"].append(spell_card)
                    else:
                        selected = selection.get(choice_field)
                        if int(requirements.get("count", 1) or 1) == 1 and not isinstance(
                            selected, list
                        ):
                            selected_values = [str(selected or "").strip()]
                        else:
                            selected_values = _support._validated_distinct_choices(
                                selected,
                                count=int(requirements.get("count", 1) or 1),
                                label=f"feature {choice_field}",
                            )
                        if any(not item for item in selected_values):
                            raise ValueError(f"feature {choice_field} choice is required")
                        options = {str(item).casefold() for item in requirements.get("options", [])}
                        if options and any(
                            item.casefold() not in options for item in selected_values
                        ):
                            raise ValueError("feature choice is not one of the allowed options")
                        if requirements.get("requires_new_choice"):
                            uniqueness_scope = str(
                                requirements.get("choice_uniqueness_scope") or artifact_id
                            )
                            prior_values: set[str] = set()
                            for prior_feature in sheet["content"]["features"]:
                                prior_choice_sets = [
                                    dict(prior_feature.get("choices") or {}),
                                    *[
                                        dict(item.get("choices") or {})
                                        for item in prior_feature.get("advancement_grants", [])
                                    ],
                                ]
                                for prior_choices in prior_choice_sets:
                                    recorded_scope = str(
                                        prior_choices.get("_choice_uniqueness_scope") or ""
                                    )
                                    if recorded_scope:
                                        same_scope = recorded_scope == uniqueness_scope
                                    elif uniqueness_scope == "fighting_style":
                                        same_scope = str(
                                            prior_feature.get("name") or ""
                                        ).casefold() in {
                                            "fighting style",
                                            "additional fighting style",
                                        }
                                    else:
                                        same_scope = (
                                            str(prior_feature.get("id") or "") == artifact_id
                                        )
                                    if not same_scope:
                                        continue
                                    prior_value = prior_choices.get(choice_field)
                                    if isinstance(prior_value, list):
                                        prior_values.update(
                                            str(item).casefold() for item in prior_value
                                        )
                                    elif prior_value is not None:
                                        prior_values.add(str(prior_value).casefold())
                            if any(item.casefold() in prior_values for item in selected_values):
                                raise ValueError("feature choice was already selected")
                        option_prerequisites = dict(requirements.get("option_prerequisites") or {})
                        at_will_spells = dict(requirements.get("at_will_spells") or {})
                        for selected_value in selected_values:
                            prerequisite = dict(option_prerequisites.get(selected_value) or {})
                            if target is not None and int(target.get("level", 0) or 0) < int(
                                prerequisite.get("minimum_level", 0) or 0
                            ):
                                raise ValueError(
                                    "eldritch invocation level prerequisite is not met"
                                )
                            required_pact = str(prerequisite.get("required_pact_boon") or "")
                            if required_pact and not any(
                                required_pact.casefold()
                                == str(
                                    dict(feature.get("choices") or {}).get("option") or ""
                                ).casefold()
                                for feature in sheet["content"]["features"]
                            ):
                                raise ValueError("eldritch invocation pact prerequisite is not met")
                            required_cantrip = str(prerequisite.get("required_cantrip") or "")
                            if required_cantrip and not any(
                                int(spell.get("level", -1)) == 0
                                and str(spell.get("name") or "").casefold()
                                == required_cantrip.casefold()
                                and bool(dict(spell.get("access") or {}).get("known"))
                                for spell in sheet["content"]["spells"]
                            ):
                                raise ValueError(
                                    "eldritch invocation cantrip prerequisite is not met"
                                )
                            at_will_spell = str(at_will_spells.get(selected_value) or "")
                            if at_will_spell:
                                at_will_matches = self.source_scoped_content_matches(
                                    [
                                        item
                                        for item in candidates
                                        if item[2].get("kind") == "spell"
                                        and str(
                                            dict(item[2].get("card") or {}).get("name") or ""
                                        ).casefold()
                                        == at_will_spell.casefold()
                                    ],
                                    source_pack_id=pack_id,
                                    source_pack_version=version,
                                )
                                if len(at_will_matches) != 1:
                                    raise _support.RulesetUnavailableError(
                                        "eldritch invocation at-will spell is "
                                        f"unavailable: {at_will_spell}"
                                    )
                                materialize_feature_spell(
                                    at_will_matches[0],
                                    method="eldritch_invocation",
                                    source_key=selected_value,
                                    at_will=True,
                                    allow_existing=True,
                                )
                            option_name = next(
                                (
                                    item
                                    for item in requirements.get("options", [])
                                    if str(item).casefold() == selected_value.casefold()
                                ),
                                selected_value,
                            )
                            option_artifact_id = str(
                                dict(requirements.get("option_artifact_ids") or {}).get(
                                    str(option_name)
                                )
                                or ""
                            )
                            if option_artifact_id and not any(
                                str(item.get("id") or "") == option_artifact_id
                                for item in sheet["content"]["features"]
                            ):
                                option_match = next(
                                    (
                                        item
                                        for item in candidates
                                        if str(item[2].get("id") or "") == option_artifact_id
                                        and item[2].get("kind") == "feature"
                                    ),
                                    None,
                                )
                                if option_match is None:
                                    raise _support.RulesetUnavailableError(
                                        "feature option extension artifact is unavailable"
                                    )
                                option_card = _support.deepcopy(
                                    dict(option_match[2].get("card") or {})
                                )
                                if str(option_card.get("feature_subtype") or "") != (
                                    "selectable_option"
                                ):
                                    raise _support.RulesetUnavailableError(
                                        "feature option extension has the wrong subtype"
                                    )
                                for metadata_key in (
                                    "at_will_spell",
                                    "class_name",
                                    "extends_feature",
                                    "feature_subtype",
                                    "mechanical_grants",
                                    "minimum_level",
                                    "required_cantrip",
                                    "required_invocation",
                                    "required_pact_boon",
                                    "repeatable_selection_levels",
                                    "selection_requirements",
                                    "selection_requirements_by_level",
                                    "subclass_name",
                                ):
                                    option_card.pop(metadata_key, None)
                                option_card["source_key"] = str(card.get("name") or artifact_id)
                                option_card.update(
                                    id=option_artifact_id,
                                    pack_id=option_match[0],
                                    pack_version=option_match[1],
                                    rule_refs=list(option_match[2].get("rule_refs") or []),
                                    mechanic_refs=list(option_match[2].get("mechanic_refs") or []),
                                )
                                sheet["content"]["features"].append(option_card)
                        if requirements.get("requires_existing_proficiency"):
                            for item in selected_values:
                                skill = sheet["skills"].get(item.casefold())
                                if requirements.get("skills_only") and skill is None:
                                    raise ValueError(
                                        "feature expertise choice must be an existing skill"
                                    )
                                tool_key = item.casefold()
                                tool_known = tool_key in {
                                    value.casefold()
                                    for value in sheet["traits"]["proficiencies"]["tools"]
                                }
                                tool_expertise = {
                                    value.casefold()
                                    for value in sheet["traits"]["proficiencies"]["tool_expertise"]
                                }
                                if not tool_known and (
                                    skill is None or skill.get("proficiency") == "none"
                                ):
                                    raise ValueError(
                                        "feature expertise choice requires an existing proficiency"
                                    )
                                if requirements.get("requires_new_expertise") and (
                                    (skill is not None and skill.get("proficiency") == "expertise")
                                    or (tool_known and tool_key in tool_expertise)
                                ):
                                    raise ValueError(
                                        "feature expertise choice already has expertise"
                                    )
                                if skill is not None:
                                    skill["proficiency"] = "expertise"
                                elif tool_known:
                                    sheet["traits"]["proficiencies"]["tool_expertise"].append(item)
                        if requirements.get("grants_skill_proficiency"):
                            for item in selected_values:
                                skill = sheet["skills"].get(item.casefold())
                                if skill is None:
                                    raise ValueError(
                                        "feature proficiency choice must be an existing skill"
                                    )
                                if (
                                    requirements.get("requires_untrained_skill")
                                    and skill.get("proficiency") != "none"
                                ):
                                    raise ValueError(
                                        "feature proficiency choice must be an untrained skill"
                                    )
                                skill["proficiency"] = "proficient"
                        for flag, target_values, label in (
                            (
                                "grants_language_proficiency",
                                sheet["traits"]["languages"],
                                "language",
                            ),
                            (
                                "grants_tool_proficiency",
                                sheet["traits"]["proficiencies"]["tools"],
                                "tool",
                            ),
                            (
                                "grants_weapon_proficiency",
                                sheet["traits"]["proficiencies"]["weapons"],
                                "weapon",
                            ),
                        ):
                            if not requirements.get(flag):
                                continue
                            _support._append_selected_proficiencies(
                                selected_values,
                                target=target_values,
                                label=label,
                            )
                mechanical_grants = dict(card.get("mechanical_grants") or {})
                unsupported_grants = (
                    set(mechanical_grants) - _support.SUPPORTED_FEATURE_MECHANICAL_GRANTS
                )
                if unsupported_grants:
                    raise _support.RulesetUnavailableError(
                        "feature mechanical grants are not executable: "
                        f"{sorted(unsupported_grants)}"
                    )
                hp_per_class_level = int(mechanical_grants.get("hp_per_class_level", 0) or 0)
                if hp_per_class_level:
                    initial_setup_full_hp = selection.get("initial_setup_full_hp", False)
                    if not isinstance(initial_setup_full_hp, bool):
                        raise ValueError("initial_setup_full_hp must be a boolean")
                    if initial_setup_full_hp:
                        hp_before_grant = dict(sheet.get("combat", {}).get("hp") or {})
                        if phase != _support.PROFILE_LOBBY:
                            raise _support.CombatEngineError(
                                "initial_setup_full_hp is available only in lobby setup"
                            )
                        if int(hp_before_grant.get("value", 0) or 0) != int(
                            hp_before_grant.get("max", 0) or 0
                        ):
                            raise _support.CombatEngineError(
                                "initial_setup_full_hp requires a character at "
                                "its pre-grant hit point maximum"
                            )
                    source_class = str(card.get("class_name") or "").strip()
                    source_level = next(
                        (
                            int(item.get("level", 0) or 0)
                            for item in sheet["progression"]["classes"]
                            if str(item.get("name") or "").casefold() == source_class.casefold()
                        ),
                        0,
                    )
                    if not source_class or source_level < 1:
                        raise ValueError(
                            "feature per-class-level hit points require its recorded class"
                        )
                    sheet = _support.apply_per_level_hit_point_bonus(
                        sheet,
                        amount=hp_per_class_level,
                        source=f"{card.get('name')}: {source_class} class levels",
                        adjust_current=initial_setup_full_hp,
                    )
                unarmored_base = int(mechanical_grants.get("unarmored_base", 0) or 0)
                if unarmored_base:
                    sheet, _ = _support.add_effect(
                        sheet,
                        {
                            "name": str(card.get("name") or "Unarmored AC"),
                            "kind": "feature",
                            "source": artifact_id,
                            "duration": {"period": "manual", "remaining": 0},
                            "changes": [
                                {
                                    "path": "combat.ac.unarmored_base",
                                    "mode": "override",
                                    "value": unarmored_base,
                                }
                            ],
                            "description": (
                                "Class feature alternate AC calculation while not wearing armor."
                            ),
                        },
                    )
                unarmored_formula = dict(mechanical_grants.get("unarmored_formula") or {})
                if unarmored_formula:
                    if any(
                        change.get("path") == "combat.ac.unarmored_formula"
                        for effect in sheet.get("effects", [])
                        if bool(effect.get("active", True))
                        for change in effect.get("changes", [])
                    ):
                        raise ValueError(
                            "a multiclass character that already has Unarmored "
                            "Defense cannot gain it again"
                        )
                    sheet, _ = _support.add_effect(
                        sheet,
                        {
                            "name": str(card.get("name") or "Unarmored Defense"),
                            "kind": "feature",
                            "source": artifact_id,
                            "duration": {"period": "manual", "remaining": 0},
                            "changes": [
                                {
                                    "path": "combat.ac.unarmored_formula",
                                    "mode": "override",
                                    "value": unarmored_formula,
                                }
                            ],
                            "description": (
                                "Class feature alternate AC calculation while "
                                "its armor and shield conditions are met."
                            ),
                        },
                    )
                armor = sheet["traits"]["proficiencies"]["armor"]
                for proficiency in mechanical_grants.get("armor_proficiencies") or []:
                    if str(proficiency).casefold() not in {str(item).casefold() for item in armor}:
                        armor.append(proficiency)
                weapons = sheet["traits"]["proficiencies"]["weapons"]
                for proficiency in mechanical_grants.get("weapon_proficiencies") or []:
                    if str(proficiency).casefold() not in {
                        str(item).casefold() for item in weapons
                    }:
                        weapons.append(proficiency)
                tools = sheet["traits"]["proficiencies"]["tools"]
                fixed_tools = [
                    str(value).strip()
                    for value in mechanical_grants.get("tool_proficiencies") or []
                    if str(value).strip()
                ]
                replacement_options = dict(
                    mechanical_grants.get("tool_proficiency_replacement_options") or {}
                )
                fixed_tool_map = {value.casefold(): value for value in fixed_tools}
                if set(str(key).casefold() for key in replacement_options) - set(fixed_tool_map):
                    raise _support.RulesetUnavailableError(
                        "feature tool replacement options must reference a fixed tool grant"
                    )
                known_tools = {str(item).casefold() for item in tools}
                duplicate_tools = {key for key in fixed_tool_map if key in known_tools}
                raw_replacements = selection.get("tool_replacements") or {}
                if not isinstance(raw_replacements, dict):
                    raise ValueError("feature tool_replacements must be an object")
                supplied_replacements = {
                    str(key).casefold(): str(value).strip()
                    for key, value in raw_replacements.items()
                }
                if set(supplied_replacements) != duplicate_tools:
                    raise ValueError(
                        "feature tool_replacements must replace exactly the already-known "
                        "fixed tool grants"
                    )
                selected_replacements: list[str] = []
                for key, replacement in supplied_replacements.items():
                    options = {
                        str(value).casefold(): str(value)
                        for value in replacement_options.get(fixed_tool_map[key], [])
                    }
                    if replacement.casefold() not in options:
                        raise ValueError(
                            "feature tool replacement is not one of the reviewed options"
                        )
                    normalized_replacement = options[replacement.casefold()]
                    if normalized_replacement.casefold() in known_tools:
                        raise ValueError("feature tool replacement is already proficient")
                    if normalized_replacement.casefold() in {
                        value.casefold() for value in selected_replacements
                    }:
                        raise ValueError("feature tool replacements must be distinct")
                    selected_replacements.append(normalized_replacement)
                for proficiency in [
                    *(fixed_tool_map[key] for key in fixed_tool_map if key not in duplicate_tools),
                    *selected_replacements,
                ]:
                    if proficiency.casefold() not in known_tools:
                        tools.append(proficiency)
                        known_tools.add(proficiency.casefold())
                for proficiency in mechanical_grants.get("skill_proficiencies") or []:
                    skill_key = str(proficiency).casefold().replace(" ", "_")
                    if skill_key not in sheet["skills"]:
                        raise ValueError(f"feature references an unknown skill: {proficiency}")
                    sheet["skills"][skill_key]["proficiency"] = "proficient"
                _support._apply_skill_proficiency_or_expertise(
                    sheet,
                    mechanical_grants.get("skill_proficiency_or_expertise") or [],
                )
                tool_expertise_all = mechanical_grants.get("tool_expertise_all", False)
                if not isinstance(tool_expertise_all, bool):
                    raise ValueError("feature tool_expertise_all must be a boolean")
                if tool_expertise_all:
                    sheet["traits"]["proficiencies"]["tool_expertise_all"] = True
                    expertise = sheet["traits"]["proficiencies"]["tool_expertise"]
                    known_expertise = {str(item).casefold() for item in expertise}
                    for proficiency in tools:
                        if str(proficiency).casefold() not in known_expertise:
                            expertise.append(proficiency)
                            known_expertise.add(str(proficiency).casefold())
                languages = sheet["traits"]["languages"]
                for language in mechanical_grants.get("languages") or []:
                    if str(language).casefold() not in {str(item).casefold() for item in languages}:
                        languages.append(language)
                for field in ("immunities", "condition_immunities"):
                    target_values = sheet["traits"][field]
                    for value in mechanical_grants.get(field) or []:
                        normalized_value = str(value).strip()
                        if not normalized_value:
                            raise ValueError(f"feature {field} grant contains an empty value")
                        if normalized_value.casefold() not in {
                            str(item).casefold() for item in target_values
                        }:
                            target_values.append(normalized_value)
                immune_effect_kinds: set[str] = set()
                if "disease" in {
                    str(item).strip().casefold()
                    for item in mechanical_grants.get("condition_immunities") or []
                }:
                    immune_effect_kinds.update({"disease", "nonmagical_disease"})
                if "poison" in {
                    str(item).strip().casefold()
                    for item in mechanical_grants.get("immunities") or []
                } or "poisoned" in {
                    str(item).strip().casefold()
                    for item in mechanical_grants.get("condition_immunities") or []
                }:
                    immune_effect_kinds.update({"poison", "poisoned"})
                for existing_effect in sheet.get("effects", []):
                    existing_kind = (
                        str(existing_effect.get("kind") or "").strip().casefold().replace("-", "_")
                    )
                    if (
                        not existing_effect.get("active", False)
                        or existing_kind not in immune_effect_kinds
                    ):
                        continue
                    existing_effect["active"] = False
                    existing_effect["ended_reason"] = "neutralized_by_feature_immunity"
                    _support.reconcile_ended_effect_conditions(
                        sheet,
                        ended_effects=[existing_effect],
                    )
                conditional_immunities = dict(
                    mechanical_grants.get("conditional_condition_immunities") or {}
                )
                if conditional_immunities:
                    normalized_conditional: dict[str, list[str]] = {}
                    for raw_condition, raw_sources in conditional_immunities.items():
                        condition = (
                            str(raw_condition)
                            .strip()
                            .casefold()
                            .replace("-", "_")
                            .replace(" ", "_")
                        )
                        if not condition:
                            raise ValueError(
                                "feature conditional condition immunity has an empty condition"
                            )
                        if not isinstance(raw_sources, list) or not raw_sources:
                            raise ValueError(
                                "feature conditional condition immunity sources must be "
                                "a non-empty array"
                            )
                        sources = [str(item).strip().casefold() for item in raw_sources]
                        if any(not item for item in sources) or set(sources) - {
                            "elemental",
                            "fey",
                        }:
                            raise _support.RulesetUnavailableError(
                                "feature conditional condition immunity has an "
                                "unsupported source type"
                            )
                        normalized_conditional[condition] = list(dict.fromkeys(sources))
                    existing_conditional = dict(
                        dict(card.get("choices") or {}).get("_conditional_condition_immunities")
                        or {}
                    )
                    if existing_conditional and existing_conditional != normalized_conditional:
                        raise ValueError(
                            "feature conditional condition immunity conflicts with existing state"
                        )
                    card.setdefault("choices", {})["_conditional_condition_immunities"] = (
                        normalized_conditional
                    )
                for resource_key, resource in dict(
                    mechanical_grants.get("resources") or {}
                ).items():
                    normalized_key = str(resource_key).strip()
                    if not normalized_key:
                        raise ValueError("feature resource grant has an empty key")
                    existing = sheet["resources"].get(normalized_key)
                    if (
                        existing is not None
                        and existing != resource
                        and normalized_key != "channel_divinity"
                    ):
                        raise ValueError(
                            f"feature resource grant conflicts with existing resource: "
                            f"{normalized_key}"
                        )
                    if existing is None:
                        sheet["resources"][normalized_key] = _support.deepcopy(dict(resource))
                resource_key = str(card.get("resource_key") or "")
                if resource_key and resource_key not in sheet["resources"]:
                    raise ValueError(
                        f"feature requires an unapplied shared resource: {resource_key}"
                    )
                for metadata_key in (
                    "class_name",
                    "subclass_name",
                    "feature_subtype",
                    "minimum_level",
                    "unlock_levels",
                    "repeatable_selection_levels",
                    "selection_requirements",
                    "selection_requirements_by_level",
                    "mechanical_grants",
                    "choice_metadata",
                    "always_prepared_spell_options",
                ):
                    card.pop(metadata_key, None)
                recorded_selection = {
                    key: value
                    for key, value in selection.items()
                    if key
                    not in {
                        "grant_level",
                        "replace_existing",
                        "study_started_elapsed_ticks",
                    }
                }
                grant_record = {
                    "level": grant_level,
                    "choices": _support.deepcopy(recorded_selection),
                    "pack_id": pack_id,
                    "pack_version": version,
                    "rule_refs": list(artifact.get("rule_refs") or []),
                }
                if replacing_feature_selection:
                    current_record = next(
                        item for item in sheet["content"][section] if item.get("id") == artifact_id
                    )
                    previous_choices = dict(current_record.get("choices") or {})
                    replacement_history = list(previous_choices.get("_replacement_history") or [])
                    current_elapsed = int(
                        dict(dict(campaign.state or {}).get("game_time") or {})["elapsed_ticks"]
                    )
                    study_started_ticks = selection.get("study_started_elapsed_ticks")
                    replacement_history.append(
                        {
                            "previous_spell_artifact_ids": list(
                                previous_choices.get(choice_field) or []
                            ),
                            "study_started_elapsed_ticks": int(study_started_ticks),
                            "study_completed_elapsed_ticks": current_elapsed,
                            "study_minutes": (current_elapsed - int(study_started_ticks))
                            // _support.TICKS_PER_MINUTE,
                        }
                    )
                    current_record["choices"] = {
                        **recorded_selection,
                        "_replacement_history": replacement_history,
                    }
                elif existing_content is not None:
                    current_record = next(
                        item for item in sheet["content"][section] if item.get("id") == artifact_id
                    )
                    current_record.setdefault("advancement_grants", []).append(grant_record)
                else:
                    if recorded_selection:
                        card["choices"] = {
                            **dict(card.get("choices") or {}),
                            **recorded_selection,
                        }
                    if grant_level:
                        card["advancement_grants"] = [grant_record]
                    card.update(provenance)
                    sheet["content"][section].append(card)
                if artifact.get("kind") == "feature" and dict(artifact.get("card") or {}).get(
                    "always_prepared_spell_options"
                ):
                    subclass_spell_grants = self.refresh_level_unlocked_subclass_spells(
                        current.campaign_id,
                        sheet,
                        class_name=declared_class,
                        branch_id=branch_id,
                    )
            else:
                card.update(provenance)
                sheet["content"][section].append(card)
        else:
            return {
                **_support._ruling_status("pending_ruling", "agent_dm_adjudication"),
                "reason": (f"{kind} is catalogued but needs an Agent-as-DM reviewed application"),
            }
        if kind in {"class", "subclass", "background", "species"}:
            if any(
                item.get("artifact_id") == artifact_id for item in sheet["content"]["selections"]
            ):
                raise ValueError("content selection is already present")
            recorded_selection = _support.deepcopy(selection)
            if kind in {"background", "species"}:
                if materialization_before is None:
                    raise ValueError(f"{kind} materialization receipt has no source snapshot")
                recorded_selection[
                    _support.BACKGROUND_MATERIALIZATION_KEY
                    if kind == "background"
                    else _support.SPECIES_MATERIALIZATION_KEY
                ] = _support._content_projection_receipt(
                    materialization_before,
                    sheet,
                    kind=kind,
                )
            if tortle_natural_armor_authority is not None:
                recorded_selection[_support.TORTLE_NATURAL_ARMOR_AUTHORITY_KEY] = _support.deepcopy(
                    tortle_natural_armor_authority
                )
            selection_record = {
                "artifact_id": artifact_id,
                "kind": kind,
                "name": str(card.get("name") or artifact_id),
                "pack_id": pack_id,
                "pack_version": version,
                "rule_refs": list(artifact.get("rule_refs") or []),
                "mechanic_refs": list(artifact.get("mechanic_refs") or []),
                "selection": recorded_selection,
            }
            if kind == "background":
                if _support.BACKGROUND_AUTHORITY_SELECTION_KEY in selection_record["selection"]:
                    raise ValueError("background selection authority is server-managed")
                authority_id = _support.uuid4().hex
                authority_payload = _support._background_authority_payload(
                    sheet,
                    selection_record,
                    character_id=current.id,
                    authority_id=authority_id,
                )
                selection_record["selection"][_support.BACKGROUND_AUTHORITY_SELECTION_KEY] = {
                    "authority_id": authority_id,
                    "authorization": _support.sign_receipt(
                        authority_payload,
                        self.content_authority_secret,
                    ),
                }
                selection = _support.deepcopy(selection_record["selection"])
            elif kind == "species":
                if _support.SPECIES_AUTHORITY_SELECTION_KEY in selection_record["selection"]:
                    raise ValueError("species selection authority is server-managed")
                authority_id = _support.uuid4().hex
                selection_record["selection"][_support.SPECIES_AUTHORITY_SELECTION_KEY] = {
                    "authority_id": authority_id,
                    "authorization": _support.sign_receipt(
                        _support._species_authority_payload(
                            sheet,
                            selection_record,
                            character_id=current.id,
                            authority_id=authority_id,
                        ),
                        self.content_authority_secret,
                    ),
                }
                selection = _support.deepcopy(selection_record["selection"])
            elif kind == "class" and "starting_equipment_result" in recorded_selection:
                selection_record["selection"][_support.CLASS_EQUIPMENT_AUTHORITY_KEY] = (
                    _support.sign_receipt(
                        _support._class_equipment_authority_payload(
                            selection_record, character_id=current.id
                        ),
                        self.content_authority_secret,
                    )
                )
            sheet["content"]["selections"].append(selection_record)
            if content_receipt is not None:
                content_receipt["selection"] = _support.deepcopy(selection_record["selection"])
        resource_sync = _support.synchronize_class_feature_resources(sheet)
        sheet = resource_sync["sheet"]
        if spellbook_copy is not None:
            return self.settle_spellbook_copy(
                current=current,
                sheet=sheet,
                artifact_id=artifact_id,
                pack_id=pack_id,
                version=version,
                level=int(spellbook_copy["level"]),
                school=str(card.get("definition", {}).get("school") or card.get("school") or ""),
                selection=selection,
                principal_id=principal_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                content_context=runtime_context,
                content_receipt=content_receipt,
            )
        if (
            phase != _support.PROFILE_LOBBY
            and not replacing_feature_selection
            and spellbook_copy is None
            and play_grant is None
        ):
            raise _support.CombatEngineError(
                "content grants belong to lobby setup or level advancement"
            )
        response_extra = {
            **({"subclass_spell_grants": subclass_spell_grants} if subclass_spell_grants else {}),
            **({"feature_spell_grants": feature_spell_grants} if feature_spell_grants else {}),
            **(
                {"species_feature_grants": species_feature_grants} if species_feature_grants else {}
            ),
            **(
                {"class_materialization": class_materialization}
                if class_materialization is not None
                else {}
            ),
            **({"spell_replacement": spell_replacement} if spell_replacement is not None else {}),
            "content_context": runtime_context,
            **({"play_grant": play_grant} if play_grant is not None else {}),
            **(
                {"rule_receipts": [_support.deepcopy(content_receipt)]}
                if content_receipt is not None
                else {}
            ),
        }
        # Choosing a subclass after leveling unlocks features absent from the
        # earlier advancement response. Recompute from this pending sheet, after
        # recording its exact source selection, and persist the result together
        # with the mutation so an idempotent replay returns the same work list.
        follow_up_class = ""
        if kind == "class":
            follow_up_class = str(card.get("name") or "")
        elif kind in {"subclass", "feature"}:
            follow_up_class = str(artifact.get("card", {}).get("class_name") or "")
        elif kind == "spell":
            follow_up_class = source_class
        if phase == _support.PROFILE_LOBBY and follow_up_class:
            selected_class = next(
                (
                    item
                    for item in sheet["progression"]["classes"]
                    if str(item.get("name") or "").casefold() == follow_up_class.casefold()
                ),
                None,
            )
            if selected_class is not None:
                response_extra["follow_up"] = self.current_class_feature_follow_up(
                    current.campaign_id,
                    sheet,
                    class_name=follow_up_class,
                    branch_id=branch_id,
                )
                spell_status = _support.profile_spell_selection_status(
                    sheet, class_name=follow_up_class
                )
                if spell_status is not None:
                    response_extra["spell_selection"] = spell_status
        equipment_stream = _support.active_random_stream()
        if (
            class_materialization is not None
            and class_materialization.get("starting_equipment", {}).get("roll") is not None
            and equipment_stream is not None
        ):
            response_extra["random_stream_receipt"] = equipment_stream.receipt()
        return self.update_sheet(
            character_id,
            sheet,
            operation="character.content.apply",
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload={
                key: _support.deepcopy(value)
                for key, value in request_payload.items()
                if key not in {"operation", "character_id"}
            },
            response_extra=response_extra,
            flatten_response_extra=True,
            rule_receipts=([content_receipt] if content_receipt is not None else None),
            expected_campaign_revision=campaign.revision,
        )

    def _content_pack_actor_presets(
        self,
        payload: dict[str, Any] | None,
        principal_id: str,
    ) -> Any:
        data = self.facade_payload(payload)
        edition = _support.normalize_dnd_edition(str(self.required(data, "edition")))
        cards = (
            _support.build_srd2014_preset_actors(self.config.dnd_skills_dir)
            if edition == "2014"
            else _support.build_srd2024_preset_actors(self.config.dnd_skills_dir)
        )
        if not cards:
            raise ValueError("bundled D&D actor presets are unavailable")
        artifact_id = str(data.get("artifact_id") or "").strip()
        if artifact_id:
            matches = [card for card in cards if card["id"] == artifact_id]
            if len(matches) != 1:
                raise ValueError("artifact_id is not present in the actor preset pack")
            cards = [_support.validate_dnd_content_actor(matches[0])]
        package_id = (
            _support.SRD2014_PRESET_PACK_ID
            if edition == "2014"
            else _support.SRD2024_PRESET_PACK_ID
        )
        package_version = (
            _support.SRD2014_PRESET_PACK_VERSION
            if edition == "2014"
            else _support.SRD2024_PRESET_PACK_VERSION
        )
        title = (
            "D&D 5e SRD 5.1 Actor Presets"
            if edition == "2014"
            else "D&D 5e SRD 5.2.1 Actor Presets"
        )
        package, package_blobs = _support.build_preset_content_package(
            package_id=package_id,
            version=package_version,
            system_id=_support.DND5E.id,
            title=title,
            cards=cards,
            metadata={
                "title": title,
                "edition": edition,
                "distribution": "shareable",
                "license": "CC-BY-4.0",
                "attribution": (
                    "Includes material from the Dungeons & Dragons System Reference "
                    "Document by Wizards of the Coast LLC, licensed under CC-BY-4.0."
                ),
            },
        )
        artifact = self.storage.write_content_archive(package, package_blobs)
        if artifact_id:
            result = {
                "card": package["actors"][0],
                "artifact": artifact,
            }
        else:
            result = {
                "package": {
                    "id": package["id"],
                    "version": package["version"],
                    "checksum": package["checksum"],
                    "cards": len(package["actors"]),
                    "metadata": _support.deepcopy(package["metadata"]),
                },
                "artifact": artifact,
                **({"content_package": package} if data.get("include_package") is True else {}),
            }
        return result

    def _content_pack_actor_preset_list(
        self,
        payload: dict[str, Any] | None,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        """List installed preset catalogs without materializing their large archives."""

        data = self.facade_payload(payload)
        campaign_id = str(self.required(data, "campaign_id"))
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        edition = _support.normalize_dnd_edition(str(self.required(data, "edition")))
        result: list[dict[str, Any]] = []
        for item in self.rule_packs.list_versions():
            manifest = dict(item.manifest or {})
            content_kinds = {str(value) for value in manifest.get("content_kinds") or []}
            if item.status != "installed" or "actor_card" not in content_kinds:
                continue
            provenance = self.rule_packs.provenance(item.pack_id, item.version)
            source = str(provenance.get("source") or "")
            editions = {
                str(value)
                for value in (
                    manifest.get("editions") or provenance.get("content_package_editions") or []
                )
            }
            if not editions and source.startswith("bundled-srd2014"):
                editions = {"2014"}
            elif not editions and source.startswith("bundled-srd2024"):
                editions = {"2024"}
            if editions and edition not in editions:
                continue
            package_kind = str(provenance.get("content_package_kind") or "")
            if package_kind != "preset" and not source.startswith("bundled-srd"):
                continue
            package_id = str(provenance.get("content_package_id") or item.pack_id)
            result.append(
                {
                    "pack_id": package_id,
                    "local_ref": item.pack_id,
                    "version": str(provenance.get("content_package_version") or item.version),
                    "checksum": str(provenance.get("content_package_checksum") or item.checksum),
                    "status": "stored",
                    "actors": len(item.artifacts),
                    "artifact": provenance.get("content_archive_artifact"),
                    "manifest": {
                        **manifest,
                        "id": package_id,
                        "editions": sorted(editions),
                        "version": str(provenance.get("content_package_version") or item.version),
                    },
                    "metadata": {
                        "license": provenance.get("license"),
                        "attribution": provenance.get("attribution"),
                    },
                }
            )
        return sorted(result, key=lambda value: (value["pack_id"], value["version"]))

    def _content_pack_actor_preset_detail(
        self,
        payload: dict[str, Any] | None,
        principal_id: str,
    ) -> Any:
        """Read an installed preset from its authoritative finalized archive."""

        data = self.facade_payload(payload)
        pack_id = str(data.get("pack_id") or "").strip()
        version = str(data.get("version") or "").strip()
        if not pack_id:
            return self._content_pack_actor_presets(data, principal_id)
        matches = [
            item
            for item in self._content_pack_actor_preset_list(data, principal_id)
            if pack_id in {str(item["pack_id"]), str(item["local_ref"])}
            and (not version or version == str(item["version"]))
        ]
        if len(matches) != 1:
            raise LookupError(pack_id)
        match = matches[0]
        archive_name = str(match.get("artifact") or "")
        if not archive_name:
            # Bundled SRD catalogs are reproducible and deliberately do not persist an archive.
            return self._content_pack_actor_presets(data, principal_id)
        package, blobs = self.storage.read_content_archive(artifact=archive_name)
        if package.get("kind") != "preset":
            raise ValueError("installed actor catalog archive is not a preset Pack")
        artifact_id = str(data.get("artifact_id") or "").strip()
        if artifact_id:
            cards = [item for item in package["actors"] if item["id"] == artifact_id]
            if len(cards) != 1:
                raise LookupError(artifact_id)
            return {
                "card": cards[0],
                "artifact": self.storage.write_content_archive(package, blobs),
            }
        return {
            "package": {
                "id": package["id"],
                "version": package["version"],
                "checksum": package["checksum"],
                "cards": len(package["actors"]),
                "metadata": _support.deepcopy(package["metadata"]),
            },
            "artifact": self.storage.write_content_archive(package, blobs),
            **({"content_package": package} if data.get("include_package") is True else {}),
        }

    def _campaign_official_addon_catalog(
        self,
        campaign_id: str,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
        """Return edition-visible built-ins and every governed official addon id."""

        profile = self.rule_profiles.get(campaign_id)
        edition = str(profile.edition) if profile is not None else ""
        all_official = {
            str(item["id"]): dict(item) for item in _support.official_expansion_catalog()
        }
        all_support = {
            str(item["id"]): dict(item) for item in _support.official_expansion_support_catalog()
        }
        official = (
            {str(item["id"]): dict(item) for item in _support.official_expansion_catalog(edition)}
            if edition
            else {}
        )
        support = (
            {
                package_id: item
                for package_id, item in all_support.items()
                if edition in {str(value) for value in item.get("editions") or []}
            }
            if edition
            else {}
        )
        return official, support, {*all_official, *all_support}

    def _require_campaign_addon_visible(
        self,
        campaign_id: str,
        addon_id: str,
    ) -> None:
        official, support, governed_ids = self._campaign_official_addon_catalog(campaign_id)
        if addon_id in governed_ids and addon_id not in official and addon_id not in support:
            raise LookupError(addon_id)

    def _content_pack_addons(
        self,
        payload: dict[str, Any] | None,
        principal_id: str,
    ) -> Any:
        data = self.facade_payload(payload)
        campaign_id = str(data["campaign_id"])
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        versions = self.addons.list_versions(
            str(data["addon_id"]) if data.get("addon_id") else None
        )
        active = {
            item.addon_id: _support.asdict(item)
            for item in self.addons.activations(
                campaign_id,
                branch_id=(str(data["branch_id"]) if data.get("branch_id") else None),
            )
        }
        official, support, governed_ids = self._campaign_official_addon_catalog(campaign_id)
        result = [
            {
                **_support.asdict(item),
                "status": "stored" if item.status == "installed" else item.status,
                "activation": active.get(item.addon_id),
                "built_in_official_expansion": item.addon_id in official,
                "built_in_official_core_support": item.addon_id in support,
                "publication_id": str(
                    dict(official.get(item.addon_id) or {}).get("publication_id") or ""
                ),
                "classification": str(
                    dict(official.get(item.addon_id) or support.get(item.addon_id) or {}).get(
                        "classification"
                    )
                    or ""
                ),
                "editions": list(
                    dict(official.get(item.addon_id) or support.get(item.addon_id) or {}).get(
                        "editions"
                    )
                    or []
                ),
            }
            for item in versions
            if item.addon_id not in governed_ids
            or item.addon_id in official
            or item.addon_id in support
        ]
        return result

    def _content_pack_addon(
        self,
        payload: dict[str, Any] | None,
        principal_id: str,
    ) -> Any:
        data = self.facade_payload(payload)
        campaign_id = str(data["campaign_id"])
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        addon_id = str(data["addon_id"])
        version = str(data["version"])
        self._require_campaign_addon_visible(campaign_id, addon_id)
        info = self.addons.get_version(addon_id, version)
        package = self.addons.get_package(addon_id, version)
        archive_name = str(info.provenance.get("content_archive_artifact") or "")
        if not archive_name:
            raise LookupError("installed addon has no unified content archive")
        archived_package, archived_blobs = self.storage.read_content_archive(artifact=archive_name)
        if archived_package != package:
            raise ValueError("installed addon descriptor differs from its content archive")
        component_status = self.addons.component_status(addon_id, version)
        result = {
            "addon": {
                **_support.asdict(info),
                "status": "stored" if info.status == "installed" else info.status,
            },
            "components": [
                {
                    **item,
                    "status": "stored" if item.get("status") == "installed" else item.get("status"),
                }
                for item in component_status
            ],
            "artifact": self.storage.write_content_archive(package, archived_blobs),
            **({"package": package} if data.get("include_package") is True else {}),
        }
        return result

    def _content_pack_export_rule(
        self,
        payload: dict[str, Any] | None,
        principal_id: str,
    ) -> Any:
        data = self.facade_payload(payload)
        self.access.require_campaign(
            str(data["campaign_id"]),
            principal_id,
            roles=_support.CAMPAIGN_DM_ROLES,
        )
        self.require_facade_phase(
            str(data["campaign_id"]),
            "content_pack(export:rule)",
            _support.PROFILE_LOBBY,
        )
        pack_id = str(data["pack_id"])
        version = str(data["version"])
        provenance = self.rule_packs.provenance(pack_id, version)
        archive_name = str(provenance.get("content_archive_artifact") or "")
        if archive_name:
            package, blobs = self.storage.read_content_archive(artifact=archive_name)
            if package.get("kind") != "core_rules":
                raise ValueError("installed rule Pack archive is not a core_rules Pack")
            return {
                "artifact": self.storage.write_content_archive(package, blobs),
                "summary": {
                    "artifacts": len(package["content"]["artifacts"]),
                    "mechanics": len(package["content"]["mechanics"]),
                    "sources": len(package["sources"]),
                    "dependencies": len(package["dependencies"]),
                    "distribution": package["metadata"]["distribution"],
                },
                **({"package": package} if data.get("include_package") is True else {}),
            }
        rule_descriptor = self.rule_content_descriptor(
            pack_id,
            version,
            metadata=dict(data.get("metadata") or {}),
        )
        component_manifest = dict(rule_descriptor["manifest"])
        package, package_blobs = _support.build_rule_content_package(
            package_id=str(rule_descriptor["id"]),
            version=str(rule_descriptor["version"]),
            system_id=_support.DND5E.id,
            manifest={
                **component_manifest,
                "classification": "official_core",
                "activation": {
                    "rule_policy": "branch",
                    "preset_policy": "none",
                    "module_policy": "none",
                },
            },
            rule_descriptors=[rule_descriptor],
            metadata={
                "distribution": "private",
                "license": "user-supplied",
                "attribution": "User supplied source",
                **dict(data.get("metadata") or {}),
            },
            kind="core_rules",
        )
        artifact = self.storage.write_content_archive(package, package_blobs)
        result = {
            "artifact": artifact,
            "summary": {
                "artifacts": len(package["content"]["artifacts"]),
                "mechanics": len(package["content"]["mechanics"]),
                "sources": len(package["sources"]),
                "dependencies": len(package["dependencies"]),
                "distribution": package["metadata"]["distribution"],
            },
            **({"package": package} if data.get("include_package") is True else {}),
        }
        return result

    def campaign_addon_set(
        self,
        campaign_id: str,
        addon_id: str,
        version: str,
        *,
        enabled: bool,
        options: dict[str, Any] | None,
        principal_id: str,
        branch_id: str | None,
        expected_revision: int | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Enable or disable one installed addon through the unified Pack facade."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_facade_phase(campaign_id, "content_pack(activate)", _support.PROFILE_LOBBY)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for addon activation")
        request = {
            "operation": "set_addon",
            "addon_id": addon_id,
            "version": version,
            "enabled": enabled,
            "options": dict(options or {}),
            "branch_id": branch_id,
            "expected_revision": expected_revision,
        }
        if enabled:
            _support._validate_reserved_official_package_identity(
                self.addons.get_package(addon_id, version)
            )
        scope = f"addon-activation:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request)
        if replay is not None:
            return replay
        effective_revision = expected_revision
        if enabled:
            requirements = self.addons.activation_requirements(
                campaign_id,
                addon_id=addon_id,
                version=version,
                branch_id=branch_id,
            )
            if effective_revision is None:
                effective_revision = int(requirements["campaign_revision"])
        activation = self.addons.set_activation(
            campaign_id,
            addon_id=addon_id,
            version=version,
            enabled=enabled,
            options=dict(options or {}),
            branch_id=branch_id,
            expected_campaign_revision=effective_revision,
        )
        response = {
            "activation": _support.asdict(activation),
            "effective_ruleset": self.effective_ruleset_view(
                campaign_id,
                branch_id=branch_id,
            ),
        }
        return self.remember_idempotent(
            scope,
            idempotency_key,
            request,
            response,
            campaign_id=campaign_id,
        )

    def content_pack(
        self,
        action: Literal[
            "list",
            "get",
            "import",
            "export",
            "activate",
            "deactivate",
            "remove",
        ],
        payload: dict[str, Any] | None = None,
        principal_id: Annotated[
            str, _support.Field(title="Principal")
        ] = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: Annotated[int | None, _support.Field(title="Revision")] = None,
        idempotency_key: Annotated[str | None, _support.Field(title="Request Key")] = None,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Inspect and manage finalized core_rules, addon, module, or preset Packs.

        Every action needs payload={campaign_id, kind, ...}. Import uses exactly
        one source_path or artifact. Module get/activate uses the local module_id
        returned by import/list, not the archive pack_id. Mutations require Lobby,
        the current campaign expected_revision and a stable idempotency_key.
        Preset Packs are usable immediately after import; do not activate them.
        Preset list/get also require payload.edition; for an exact stored preset,
        get uses pack_id and version returned by list. Versions are not interchangeable.
        """

        data = self.facade_payload(payload)
        campaign_id = str(self.required(data, "campaign_id"))
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if action not in {"list", "get"}:
            self.require_facade_phase(
                campaign_id, f"content_pack({action})", _support.PROFILE_LOBBY
            )
        kind = str(self.required(data, "kind"))
        routing_kinds = {"core_rules", "addon", "module", "preset"}
        if kind not in routing_kinds:
            raise ValueError("payload.kind must be core_rules, addon, module, or preset")
        if action == "list":
            if kind == "core_rules":
                result = []
                for item in self.rule_pack_list(data.get("pack_id")):
                    if "actor_card" in {
                        str(value)
                        for value in dict(item.get("manifest") or {}).get("content_kinds") or []
                    }:
                        continue
                    provenance = self.rule_packs.provenance(
                        str(item["pack_id"]), str(item["version"])
                    )
                    package_kind = str(provenance.get("content_package_kind") or "")
                    if package_kind and package_kind != "core_rules":
                        continue
                    result.append(
                        {
                            **item,
                            "pack_id": str(provenance.get("content_package_id") or item["pack_id"]),
                            "local_ref": str(item["pack_id"]),
                            "version": str(
                                provenance.get("content_package_version") or item["version"]
                            ),
                            "checksum": str(
                                provenance.get("content_package_checksum") or item["checksum"]
                            ),
                            "status": (
                                "stored"
                                if item.get("status") == "installed"
                                else item.get("status")
                            ),
                        }
                    )
            elif kind == "addon":
                result = self._content_pack_addons(data, principal_id)
            elif kind == "module":
                result = [
                    item
                    for item in self.modules.list(campaign_id, include_retired=True)
                    if str(item.get("parser_profile") or "") == "content-package"
                ]
            else:
                result = (
                    self._content_pack_actor_presets(data, principal_id)
                    if data.get("include_package") is True
                    else self._content_pack_actor_preset_list(data, principal_id)
                )
            if isinstance(result, list):
                result, page = _support._bounded_page(
                    result,
                    scope=f"content_pack:list:{campaign_id}:{kind}:{principal_id}",
                    query=query or str(data.get("query") or ""),
                    limit=data.get("limit", limit),
                    cursor=cursor or data.get("cursor"),
                    offset=data.get("offset", 0),
                )
                return self.facade_result(action, result, page=page)
            # include_package=True intentionally returns one complete, bounded
            # preset artifact for import and is not a catalog listing.
            return self.facade_result(action, result)

        if action == "get":
            archive_inputs = [
                name for name in ("artifact", "source_path") if data.get(name) is not None
            ]
            if archive_inputs:
                if len(archive_inputs) != 1:
                    raise ValueError(
                        "provide exactly one of payload.artifact or payload.source_path"
                    )
                package, _blobs = self.storage.read_content_archive(
                    artifact=(str(data["artifact"]) if archive_inputs[0] == "artifact" else None),
                    source_path=(
                        data.get("source_path") if archive_inputs[0] == "source_path" else None
                    ),
                )
                if str(package.get("kind") or "") != kind:
                    raise ValueError("payload.kind does not match the finalized Pack archive")
                if kind == "addon":
                    self._require_campaign_addon_visible(
                        campaign_id,
                        str(package.get("id") or ""),
                    )
                result = package
            elif kind == "core_rules":
                pack_id = str(self.required(data, "pack_id"))
                version = str(self.required(data, "version"))
                inspected = self.rule_pack_inspect(pack_id, version)
                provenance = self.rule_packs.provenance(pack_id, version)
                archive_name = str(provenance.get("content_archive_artifact") or "")
                if data.get("include_package") is True and archive_name:
                    package, blobs = self.storage.read_content_archive(artifact=archive_name)
                    if package.get("kind") != "core_rules":
                        raise ValueError("installed rule Pack archive is not a core_rules Pack")
                    result = {
                        "rule_pack": {
                            **inspected,
                            "status": (
                                "stored"
                                if inspected.get("status") == "installed"
                                else inspected.get("status")
                            ),
                        },
                        "artifact": self.storage.write_content_archive(package, blobs),
                        "package": package,
                    }
                else:
                    result = {
                        **inspected,
                        "status": (
                            "stored"
                            if inspected.get("status") == "installed"
                            else inspected.get("status")
                        ),
                    }
            elif kind == "addon":
                result = self._content_pack_addon(data, principal_id)
            elif kind == "module":
                module_id = str(self.required(data, "module_id"))
                matches = [
                    item
                    for item in self.modules.list(campaign_id, include_retired=True)
                    if str(item.get("id") or item.get("module_id") or "") == module_id
                    and str(item.get("parser_profile") or "") == "content-package"
                ]
                if len(matches) != 1:
                    raise LookupError(module_id)
                archived = self._content_pack_module_archive(campaign_id, module_id)
                if data.get("include_package") is True and archived is not None:
                    package, _blobs, artifact = archived
                    result = {
                        "module": matches[0],
                        "artifact": artifact,
                        "package": package,
                    }
                else:
                    result = matches[0]
            else:
                result = self._content_pack_actor_preset_detail(data, principal_id)
            return self.facade_result(action, result)

        if action == "import":
            if not idempotency_key:
                raise ValueError("idempotency_key is required for Pack import")
            choices = [name for name in ("artifact", "source_path") if data.get(name) is not None]
            if len(choices) != 1:
                raise ValueError("provide exactly one of payload.artifact or payload.source_path")
            request = {
                "action": action,
                "payload": _support.deepcopy(data),
            }
            scope = f"content-pack-import:{campaign_id}:{principal_id}:{kind}"
            replay = self.replay_idempotent(scope, idempotency_key, request)
            if replay is not None:
                return replay
            package, blobs = self.storage.read_content_archive(
                artifact=(str(data["artifact"]) if choices[0] == "artifact" else None),
                source_path=(data.get("source_path") if choices[0] == "source_path" else None),
            )
            accepted_archive_kinds = {
                "module": {"module"},
                "addon": {"addon"},
                "preset": {"preset"},
                "core_rules": {"core_rules"},
            }
            package_kind = str(package.get("kind") or "")
            if package_kind not in accepted_archive_kinds[kind]:
                raise ValueError(
                    f"payload.kind {kind} does not match archive kind {package_kind or '<missing>'}"
                )
            with self.storage.database.transaction():
                if kind == "module":
                    result = self.import_content_module_package(
                        campaign_id,
                        package,
                        blobs,
                        principal_id=principal_id,
                        idempotency_key=idempotency_key,
                        activate=False,
                        progress_remaps=data.get("progress_remaps"),
                    )
                else:
                    result = self.import_content_rules_package(
                        campaign_id,
                        package,
                        blobs,
                        principal_id=principal_id,
                        idempotency_key=idempotency_key,
                    )
            response = self.facade_result(action, result)
            return self.remember_idempotent(
                scope,
                idempotency_key,
                request,
                response,
                campaign_id=campaign_id,
            )

        if action == "export":
            if kind == "core_rules":
                result = self._content_pack_export_rule(data, principal_id)
            elif kind == "module":
                module_id = str(self.required(data, "module_id"))
                archived = self._content_pack_module_archive(campaign_id, module_id)
                if archived is not None:
                    package, _blobs, artifact = archived
                    result = {
                        **artifact,
                        "summary": {
                            "scenes": len(package["content"]["scene_atlas"]),
                            "actors": len(package["actors"]),
                            "assets": len(package["assets"]),
                        },
                        **({"package": package} if data.get("include_package") is True else {}),
                    }
                else:
                    result = self.export_module_pack(campaign_id, data, principal_id)
            elif kind == "addon":
                result = self._content_pack_addon(data, principal_id)
            else:
                result = self._content_pack_actor_preset_detail(data, principal_id)
            return self.facade_result(action, result)

        if action in {"activate", "deactivate"}:
            if kind == "core_rules":
                if action == "activate":
                    result = self.campaign_rule_pack_set(
                        campaign_id,
                        str(self.required(data, "pack_id")),
                        str(self.required(data, "version")),
                        self.facade_bool(data, "enabled", default=True),
                        dict(data.get("options") or {}),
                        principal_id,
                        data.get("branch_id"),
                        expected_revision,
                        idempotency_key,
                    )
                else:
                    result = self.campaign_rule_pack_remove(
                        campaign_id,
                        str(self.required(data, "pack_id")),
                        principal_id,
                        data.get("branch_id"),
                        expected_revision,
                        idempotency_key,
                    )
            elif kind == "module" and action == "activate":
                if not idempotency_key:
                    raise ValueError("idempotency_key is required for module activation")
                module_id = str(self.required(data, "module_id"))
                module = next(
                    (
                        item
                        for item in self.modules.list(campaign_id, include_retired=True)
                        if str(item.get("id") or item.get("module_id") or "") == module_id
                    ),
                    None,
                )
                if module is None:
                    raise LookupError(module_id)
                if str(module.get("parser_profile") or "") != "content-package":
                    raise ValueError(
                        "module activation requires a module imported from a finalized Pack "
                        "artifact"
                    )
                scene_map = {
                    str(item.get("stable_key") or ""): str(item.get("scene_id") or "")
                    for item in self.modules.scene_index(campaign_id, module_id=module_id)
                }
                normalized_remaps: list[dict[str, str]] = []
                remap_targets: dict[str, str] = {}
                raw_remaps = data.get("progress_remaps") or []
                if not isinstance(raw_remaps, list):
                    raise ValueError("payload.progress_remaps must be an array")
                for index, raw in enumerate(raw_remaps):
                    if not isinstance(raw, dict) or set(raw) != {
                        "from_scene_id",
                        "to_scene_key",
                        "reason",
                    }:
                        raise ValueError(
                            f"progress_remaps[{index}] requires exactly from_scene_id, "
                            "to_scene_key, and reason"
                        )
                    source_scene_id = str(raw.get("from_scene_id") or "").strip()
                    target_scene_key = str(raw.get("to_scene_key") or "").strip()
                    reason = str(raw.get("reason") or "").strip()
                    if (
                        not source_scene_id
                        or not target_scene_key
                        or target_scene_key not in scene_map
                        or not reason
                        or len(reason) > 1000
                        or source_scene_id in remap_targets
                    ):
                        raise ValueError(f"progress_remaps[{index}] is not a valid Agent remap")
                    target_scene_id = scene_map[target_scene_key]
                    remap_targets[source_scene_id] = target_scene_id
                    normalized_remaps.append(
                        {
                            "from_scene_id": source_scene_id,
                            "to_scene_key": target_scene_key,
                            "to_scene_id": target_scene_id,
                            "reason": reason,
                            "resolver": "agent",
                        }
                    )
                activation_payload = {
                    "operation": "activate_module_pack",
                    "module_id": module_id,
                    "progress_remaps": normalized_remaps,
                }
                activation_scope = f"content-module-activation:{campaign_id}"
                replay = self.idempotency.lookup(
                    activation_scope,
                    idempotency_key,
                    activation_payload,
                )
                activation = (
                    dict(replay.response or {}).get("activation")
                    if replay is not None
                    else self.modules.activate_candidate(
                        campaign_id,
                        module_id,
                        progress_remaps=remap_targets,
                        idempotency_key=idempotency_key,
                        idempotency_write=_support.IdempotencyWrite(
                            scope=activation_scope,
                            payload=activation_payload,
                            response=lambda value: {
                                "activation": {
                                    **dict(value),
                                    "progress_remap_rulings": normalized_remaps,
                                }
                            },
                        ),
                    )
                )
                if replay is None:
                    activation = {
                        **dict(activation),
                        "progress_remap_rulings": normalized_remaps,
                    }
                result = {"activation": activation}
            elif kind == "addon":
                result = self.campaign_addon_set(
                    campaign_id,
                    str(self.required(data, "addon_id")),
                    str(self.required(data, "version")),
                    enabled=action == "activate",
                    options=dict(data.get("options") or {}),
                    principal_id=principal_id,
                    branch_id=data.get("branch_id"),
                    expected_revision=expected_revision,
                    idempotency_key=idempotency_key,
                )
            else:
                raise ValueError("this Pack kind does not support the requested activation action")
            return self.facade_result(action, result)

        if kind in {"core_rules", "preset"}:
            result = self.rule_pack_remove(
                str(self.required(data, "pack_id")), str(self.required(data, "version"))
            )
        elif kind == "addon":
            addon_id = str(self.required(data, "addon_id"))
            version = str(self.required(data, "version"))
            self.addons.remove_version(addon_id, version)
            result = {"status": "removed", "addon_id": addon_id, "version": version}
        else:
            module_id = str(self.required(data, "module_id"))
            module = next(
                (
                    item
                    for item in self.modules.list(campaign_id, include_retired=True)
                    if str(item.get("id") or item.get("module_id") or "") == module_id
                    and str(item.get("parser_profile") or "") == "content-package"
                ),
                None,
            )
            if module is None:
                raise LookupError(module_id)
            if bool(module.get("active")):
                raise ValueError("an active module Pack cannot be removed")
            playthrough = dict(
                self.campaigns.get(campaign_id).state.get("playthrough_manifest") or {}
            )
            if module_id in {str(item) for item in playthrough.get("module_ids") or []}:
                raise ValueError(
                    "a module Pack referenced by the playthrough manifest cannot be removed"
                )
            self.modules.delete(campaign_id, module_id)
            result = {"status": "removed", "module_id": module_id}
        return self.facade_result(action, result)

    def campaign_rules(
        self,
        campaign_id: str,
        action: Literal[
            "get_profile",
            "set_profile",
            "core_relock",
            "explain",
            "receipts",
        ],
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Read or change the base rule profile; use content_pack for Pack changes.

        get_profile needs no payload. set_profile uses {edition, locale?,
        publications?, options?}. core_relock uses {expected_core_fingerprint,
        expected_head_snapshot_id, reason}: read the exact old fingerprint from
        get_profile, create/verify a current checkpoint, and pass its head ID,
        current branch and campaign revision. Only relock after reviewing an
        actual runtime upgrade; it is not required to equip items or use presets.
        explain accepts {event?}; receipts accepts {mechanic_id?, limit?}.
        """
        data = self.facade_payload(payload)
        if action == "get_profile":
            result = self.campaign_rule_profile_get(campaign_id, principal_id)
        elif action == "set_profile":
            result = self.campaign_rule_profile_set(
                campaign_id,
                self.required(data, "edition"),
                data.get("locale", "en"),
                data.get("publications"),
                data.get("options"),
                principal_id,
                expected_revision,
                idempotency_key,
            )
        elif action == "core_relock":
            data = self.facade_payload(payload)
            result = self.campaign_core_relock(
                campaign_id,
                self.required(data, "expected_core_fingerprint"),
                self.required(data, "reason"),
                principal_id,
                branch_id,
                expected_revision,
                self.required(data, "expected_head_snapshot_id"),
                idempotency_key,
            )
        elif action == "explain":
            result = self.campaign_rules_explain(
                campaign_id, data.get("event"), principal_id, branch_id
            )
        else:
            result = self.campaign_rule_receipts(
                campaign_id,
                principal_id,
                branch_id,
                data.get("mechanic_id"),
                data.get("limit", 100),
            )
        return self.facade_result(action, result)

    def addon_actor_instantiate(
        self,
        campaign_id: str,
        artifact_id: str,
        owner_character_id: str,
        name: str | None = None,
        character_type: Literal["npc", "monster"] = "monster",
        player_name: str | None = None,
        summary: str = "",
        notes: dict[str, Any] | None = None,
        owner_class_name: str | None = None,
        casting_slot_level: int | None = None,
        template_variant: str | None = None,
        participant_config: dict[str, Any] | None = None,
        replace_existing: _support.StrictBool = False,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Instantiate one enabled, reviewed addon actor template without evaluating prose."""
        replace_existing = _support._strict_boolean(replace_existing, "replace_existing")
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for addon actor instantiation")
        addon_request = {
            "artifact_id": artifact_id,
            "owner_character_id": owner_character_id,
            "name": name,
            "character_type": character_type,
            "player_name": player_name,
            "summary": summary,
            "notes": _support.deepcopy(notes),
            "owner_class_name": owner_class_name,
            "casting_slot_level": casting_slot_level,
            "template_variant": template_variant,
            "participant_config": _support.deepcopy(participant_config),
            "replace_existing": replace_existing,
            "expected_revision": expected_revision,
            "branch_id": branch_id,
        }
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        # Bind the implicit current branch into the retry digest.  A request
        # with branch_id=None means "the branch checked out now", not a
        # branch-independent operation.
        addon_request["branch_id"] = resolved_branch_id
        owner = self.characters.get(owner_character_id)
        if owner.campaign_id != campaign_id:
            raise ValueError("dependent actor owner belongs to another campaign")
        available_artifacts = self.available_content_artifacts(
            campaign_id, branch_id=resolved_branch_id
        )
        matches = [
            (pack_id, version, artifact)
            for pack_id, version, artifact in available_artifacts
            if str(artifact.get("id") or "") == artifact_id
        ]
        if len(matches) != 1:
            raise LookupError("dependent actor template is not uniquely available in this campaign")
        pack_id, version, artifact = matches[0]
        if str(artifact.get("kind") or "") != "statblock":
            raise ValueError("addon actor instantiation requires a statblock artifact")
        card = _support.deepcopy(dict(artifact.get("card") or {}))
        requirement = _support.deepcopy(dict(card.get("dependent_actor_template") or {}))
        solution_errors = _support.dependent_actor_template_solution_errors(requirement)
        if solution_errors:
            raise ValueError(
                "dependent actor template is not runtime-ready: " + "; ".join(solution_errors)
            )
        owner_binding = _support.dependent_actor_owner_binding(requirement)
        lifecycle_policy = _support.dependent_actor_lifecycle_policy(requirement)
        dependent_actor_authorization: dict[str, Any] | None = None
        if owner_binding is not None:
            feature_matches = [
                feature_artifact
                for feature_pack_id, feature_version, feature_artifact in available_artifacts
                if feature_pack_id == pack_id
                and feature_version == version
                and str(feature_artifact.get("id") or "") == owner_binding["feature_artifact_id"]
                and str(feature_artifact.get("kind") or "") == "feature"
            ]
            if len(feature_matches) != 1:
                raise _support.RulesetUnavailableError(
                    "dependent actor bound feature is not uniquely available in its source pack"
                )
            feature_artifact = self.reviewed_official_runtime_artifact(
                pack_id, version, feature_matches[0]
            )
            feature_contract = dict(feature_artifact.get("selection_contract") or {})
            reviewed_content_hash = str(feature_contract.get("reviewed_content_hash") or "")
            receipt_fields = {
                "event": "character.content.apply",
                "artifact_id": owner_binding["feature_artifact_id"],
                "character_id": owner.id,
                "pack_id": pack_id,
                "pack_version": version,
                "artifact_content_hash": _support.content_fingerprint(feature_artifact),
            }
            if reviewed_content_hash:
                receipt_fields["reviewed_content_hash"] = reviewed_content_hash
            sheet_features = [
                feature
                for feature in dict(owner.sheet.get("content") or {}).get("features", [])
                if isinstance(feature, Mapping)
                and str(feature.get("id") or "") == owner_binding["feature_artifact_id"]
                and str(feature.get("pack_id") or "") == pack_id
                and str(feature.get("pack_version") or "") == version
            ]
            if (
                len(sheet_features) != 1
                or (reviewed_content_hash and len(reviewed_content_hash) != 64)
                or not self.rule_receipts.has_applied_receipt(
                    campaign_id,
                    event="character.content.apply",
                    receipt_fields=receipt_fields,
                    branch_id=resolved_branch_id,
                )
            ):
                raise ValueError(
                    "dependent actor owner lacks the exact applied feature entitlement"
                )
            dependent_actor_authorization = {
                "owner_character_id": owner.id,
                "owner_character_revision": owner.revision,
                "feature_artifact_id": owner_binding["feature_artifact_id"],
                "source_pack_id": pack_id,
                "source_pack_version": version,
                "receipt_event": "character.content.apply",
                "receipt_fields": receipt_fields,
                "branch_id": resolved_branch_id,
            }
        # Core stores actor lifecycle idempotency at campaign/principal scope.
        # The resolved branch is part of ``addon_request``, so another branch
        # conflicts instead of replaying this response.
        lifecycle_scope = f"actor-lifecycle:{campaign_id}:{principal_id}"
        # Replay only after the current branch and (when source-bound) the
        # current applied entitlement have been verified.  This prevents a
        # successful response from surviving a receipt undo.
        replay = self.idempotency.lookup(lifecycle_scope, idempotency_key, addon_request)
        if replay is not None and replay.response is not None:
            response = dict(replay.response)
            return {
                "character": self.character_view(
                    _support.CharacterInfo(**dict(response["character"]))
                ),
                "content_receipt": _support.deepcopy(response["content_receipt"]),
                "actor_knowledge_imported": False,
                "combat": _support.deepcopy(response.get("combat")),
                "replaced_actor_id": response.get("replaced_actor_id"),
                "mutation_group_id": str(response["mutation_group_id"]),
                "next": None,
            }
        source_text, source_refs = self.dependent_actor_source_text(artifact)
        numeric_parameters, selected_class_name = self.dependent_actor_numeric_parameters(
            owner=owner,
            requirement=requirement,
            owner_class_name=str(owner_class_name or ""),
            casting_slot_level=casting_slot_level,
        )
        normalized_template_variant = (
            str(template_variant).strip().casefold().replace(" ", "_")
            if template_variant is not None
            else None
        )
        preview_text, _ = _support.materialize_parameterized_statblock_source(
            source_text,
            requirement,
            numeric_parameters=numeric_parameters,
            self_ability_modifiers={},
            template_variant=template_variant,
            allow_self_modifier_placeholders=True,
        )
        edition = self.campaign_rules_edition(campaign_id)
        source_key = f"rule-pack:{pack_id}@{version}#artifact:{artifact_id}"
        preview = self.parse_edition_statblock(
            preview_text,
            edition=edition,
            source_key=source_key,
            rule_refs=source_refs,
            name=str(name or "").strip() or None,
        )
        self_modifiers = dict(
            self.derive_character_sheet(preview.sheet).get("ability_modifiers") or {}
        )
        rendered_text, resolved_fields = _support.materialize_parameterized_statblock_source(
            source_text,
            requirement,
            numeric_parameters=numeric_parameters,
            self_ability_modifiers=self_modifiers,
            template_variant=template_variant,
        )
        parsed = self.parse_edition_statblock(
            rendered_text,
            edition=edition,
            source_key=source_key,
            rule_refs=source_refs,
            name=str(name or "").strip() or None,
        )
        hydrated_sheet, spell_warnings = self.hydrate_statblock_spellcasting(
            campaign_id,
            parsed,
            source_key=source_key,
            rule_refs=source_refs,
        )
        self.require_standard_statblock_engine_support(
            hydrated_sheet,
            None,
            statblock_warnings=(),
            spell_warnings=spell_warnings,
        )
        sheet = _support.apply_dependent_actor_template_variant(
            hydrated_sheet,
            requirement,
            template_variant=template_variant,
        )
        sheet = self.finalize_actor_sheet_rulings(sheet, campaign_id)
        if owner_binding is not None:
            sheet = _support.materialize_dependent_actor_owner_scaling(
                sheet,
                numeric_parameters,
                relation_key=owner_binding["relation_key"],
                reviewed_expression_hash=str(
                    dict(requirement.get("solution") or {}).get("reviewed_expression_hash") or ""
                ),
            )
            if owner_binding["relation_key"] == _support.STEEL_DEFENDER_RELATION_KEY:
                sheet = _support.bind_steel_defender_runtime_mechanics(sheet)
        self.require_engine_owned_character_state(sheet)
        _support._reject_new_intrinsic_attack_provenance(sheet)
        _support._require_authoritative_background_state(
            sheet,
            character_id=None,
            secret=self.content_authority_secret,
        )
        _support._require_authoritative_species_state(
            sheet,
            character_id=None,
            secret=self.content_authority_secret,
        )
        if character_type not in _support.NON_PLAYER_CHARACTER_TYPES:
            raise ValueError("addon actor templates create only npc or monster actors")
        actor_id = str(
            _support.uuid5(
                _support.NAMESPACE_URL,
                f"sagasmith-dnd:addon-actor:{campaign_id}:{principal_id}:{idempotency_key}",
            )
        )
        requested_name = str(name or "").strip()
        actor_name = requested_name or (
            f"{parsed.name} ({owner.name}; {actor_id[:8]})"
            if replace_existing
            else f"{parsed.name} ({owner.name})"
        )
        notes_value = _support.deepcopy(notes or _support.default_character_notes())
        profile = notes_value.setdefault("profile", {})
        receipt = {
            "event": "addon.actor.instantiate",
            "ruleset_fingerprint": self.effective_rule_context(campaign_id).fingerprint,
            "artifact_id": artifact_id,
            "pack_id": pack_id,
            "pack_version": version,
            "owner_character_id": owner.id,
            "owner_character_revision": owner.revision,
            "owner_class_name": selected_class_name,
            "casting_slot_level": casting_slot_level,
            "template_variant": (
                str(template_variant).strip().casefold().replace(" ", "_")
                if template_variant is not None
                else None
            ),
            "numeric_parameters": numeric_parameters,
            "resolved_fields": resolved_fields,
            "reviewed_expression_hash": str(
                dict(requirement["solution"])["reviewed_expression_hash"]
            ),
            "source_refs": source_refs,
        }
        relation_campaign = self.campaigns.get(campaign_id)
        existing_relations = _support.validate_dependent_actor_relations(
            dict(relation_campaign.state or {}).get("dependent_actor_relations", [])
        )
        active_relations = (
            []
            if owner_binding is None
            else [
                relation
                for relation in existing_relations
                if relation["owner_character_id"] == owner.id
                and relation["relation_key"] == owner_binding["relation_key"]
                and relation["status"] == "active"
            ]
        )
        if replace_existing and owner_binding is None:
            raise ValueError("replace_existing requires a source-bound dependent actor")
        if len(active_relations) > 1:
            raise ValueError("dependent actor owner has ambiguous active bound actors")
        if not replace_existing and active_relations:
            raise ValueError("dependent actor owner already has an active bound actor")
        if replace_existing and len(active_relations) != 1:
            raise ValueError("dependent actor replacement requires exactly one active bound actor")
        replacement_actor = None
        replacement_rest_tick: int | None = None
        if replace_existing:
            if self.authoritative_phase(campaign_id) == _support.PROFILE_COMBAT:
                raise _support.CombatEngineError(
                    "dependent actor replacement must occur at the end of a long rest"
                )
            if expected_revision is None or expected_revision != relation_campaign.revision:
                raise ValueError(
                    "dependent actor replacement requires the current campaign expected_revision"
                )
            rest_history = dict(dict(owner.sheet.get("combat") or {}).get("rest_history") or {})
            replacement_rest_tick = rest_history.get("last_long_rest_elapsed_ticks")
            if (
                isinstance(replacement_rest_tick, bool)
                or not isinstance(replacement_rest_tick, int)
                or replacement_rest_tick < 0
                or rest_history.get("last_rest_type") != "long_rest"
                or rest_history.get("last_rest_completed_elapsed_ticks") != replacement_rest_tick
                or int(
                    dict(relation_campaign.state or {})
                    .get("game_time", {})
                    .get("elapsed_ticks", -1)
                )
                != replacement_rest_tick
            ):
                raise ValueError(
                    "dependent actor replacement must use the owner's just-completed long rest"
                )
            inventory_items = list(dict(owner.sheet.get("inventory") or {}).get("items") or [])
            has_smiths_tools = any(
                isinstance(item, Mapping)
                and int(item.get("quantity", 0) or 0) > 0
                and str(item.get("name") or "")
                .replace("’", "'")
                .replace("‘", "'")
                .strip()
                .casefold()
                in {"smith's tools", "smiths tools"}
                for item in inventory_items
            )
            if not has_smiths_tools:
                raise ValueError(
                    "dependent actor replacement requires smith's tools with the owner"
                )
            if active_relations[0].get("created_long_rest_elapsed_ticks") == replacement_rest_tick:
                raise ValueError("this long rest has already created the active dependent actor")
            replacement_actor = self.characters.get(active_relations[0]["dependent_actor_id"])
            if replacement_actor.campaign_id != campaign_id:
                raise ValueError("dependent actor replacement target belongs to another campaign")
            if (
                active_relations[0]["source_artifact_id"] != artifact_id
                or active_relations[0]["source_pack_id"] != pack_id
                or active_relations[0]["source_pack_version"] != version
            ):
                raise ValueError(
                    "dependent actor replacement must use the same source-bound template"
                )
            self._dependent_actor_refresh_receipt(
                active_relations[0],
                {
                    **dict(artifact),
                    "_pack_id": pack_id,
                    "_pack_version": version,
                },
                requirement,
                campaign_id,
            )
            existing_binding = dict(active_relations[0]["template_binding"])
            expected_existing_binding = {
                "owner_class_name": selected_class_name,
                "casting_slot_level": casting_slot_level,
                "template_variant": normalized_template_variant,
                "numeric_parameters": _support.deepcopy(numeric_parameters),
                "reviewed_expression_hash": str(
                    dict(requirement["solution"])["reviewed_expression_hash"]
                ),
            }
            if {
                key: existing_binding[key] for key in expected_existing_binding
            } != expected_existing_binding:
                raise ValueError(
                    "dependent actor replacement binding is stale or conflicts with the owner"
                )
        if (
            owner_binding is not None
            and any(
                relation["owner_character_id"] == owner.id
                and relation["relation_key"] == owner_binding["relation_key"]
                and relation["status"] == "active"
                for relation in existing_relations
            )
            and not replace_existing
        ):
            raise ValueError("dependent actor owner already has an active bound actor")
        receipt.update(
            {
                "replace_existing": replace_existing,
                "replaced_actor_id": (
                    replacement_actor.id if replacement_actor is not None else None
                ),
                "created_long_rest_elapsed_ticks": replacement_rest_tick,
            }
        )
        template_binding = {
            "owner_class_name": selected_class_name,
            "casting_slot_level": casting_slot_level,
            "template_variant": normalized_template_variant,
            "numeric_parameters": _support.deepcopy(numeric_parameters),
            "reviewed_expression_hash": str(
                dict(requirement["solution"])["reviewed_expression_hash"]
            ),
            **({"lifecycle_policy": lifecycle_policy} if lifecycle_policy is not None else {}),
        }
        template_binding["authorization"] = _support.sign_receipt(
            {
                "schema_version": 1,
                "purpose": "dependent_actor_template",
                "campaign_id": campaign_id,
                "owner_character_id": owner.id,
                "dependent_actor_id": actor_id,
                "relation_key": (
                    owner_binding["relation_key"] if owner_binding is not None else ""
                ),
                "source_artifact_id": artifact_id,
                "source_pack_id": pack_id,
                "source_pack_version": version,
                **_support.deepcopy(template_binding),
            },
            self.content_authority_secret,
        )
        provenance = "sagasmith:addon-actor-template:" + _support.canonical_json(receipt)
        existing_dm_notes = str(profile.get("dm_notes") or "").strip()
        profile["dm_notes"] = "\n".join(value for value in (existing_dm_notes, provenance) if value)
        campaign_state: dict[str, Any] | None = None
        combat_result: dict[str, Any] | None = None
        actor_sheet = sheet
        relation_base_revision = relation_campaign.revision
        if self.authoritative_phase(campaign_id) == _support.PROFILE_COMBAT:
            if expected_revision is None:
                raise ValueError("expected_revision is required during Combat")
            campaign, encounter = self.active_encounter(campaign_id)
            relation_base_revision = campaign.revision
            self.require_no_blocking_pending(encounter)
            if campaign.revision != expected_revision:
                raise ValueError(
                    "campaign revision conflict: "
                    f"expected {expected_revision}, found {campaign.revision}"
                )
            config_value = dict(participant_config or {})
            allowed = {
                "token_id",
                "position",
                "hidden",
                "visible_to_actor_ids",
                "disposition",
                "reach_ft",
                "can_share_space",
                "surprised",
                "death_saves",
                "initiative",
                "tie_breaker",
                "join_round",
                "source_conditions",
                "deployment_zone_id",
            }
            if unknown := sorted(set(config_value) - allowed):
                raise ValueError(f"unsupported participant config fields: {unknown}")
            visible_to = config_value.get("visible_to_actor_ids")
            encounter_actor_ids = {
                str(item.get("actor_id") or "") for item in encounter.get("combatants", [])
            } | {actor_id}
            if visible_to is not None and (
                not isinstance(visible_to, list)
                or any(str(item) not in encounter_actor_ids for item in visible_to)
            ):
                raise ValueError(
                    "visible_to_actor_ids must contain only current or joining participant IDs"
                )
            battle_map = encounter.get("battle_map")
            if isinstance(battle_map, dict):
                _support.validate_position(battle_map, config_value.get("position"))
            join_round = config_value.pop("join_round", None)
            if join_round is not None and (
                isinstance(join_round, bool)
                or not isinstance(join_round, int)
                or join_round <= int(encounter.get("round", 1) or 1)
            ):
                raise ValueError("join_round must be an integer after the current combat round")
            provisional = _support.CharacterInfo(
                id=actor_id,
                system_id=_support.DND5E.id,
                campaign_id=campaign_id,
                template_id=None,
                character_type=character_type,
                name=actor_name,
                player_name=player_name,
                summary=str(summary or parsed.summary),
                sheet=_support.deepcopy(actor_sheet),
                notes=_support.deepcopy(notes_value),
                revision=1,
            )
            _, joining_conditions, joining_sheet = self.source_participant_rules(
                campaign_id,
                str(encounter.get("scene_id") or "") or None,
                provisional,
                config_value,
            )
            config_value.pop("source_conditions", None)
            if joining_sheet is not None:
                actor_sheet = _support.validate_character_sheet(
                    joining_sheet, rules=self.effective_rule_context(campaign_id)
                )
                provisional = _support.replace(provisional, sheet=_support.deepcopy(actor_sheet))
            actor_snapshot = self.character_view(provisional)
            actor_snapshot.update(config_value)
            if (
                owner_binding is not None
                and owner_binding["relation_key"] == _support.STEEL_DEFENDER_RELATION_KEY
                and template_binding["reviewed_expression_hash"]
                == _support.STEEL_DEFENDER_REVIEWED_EXPRESSION_HASH
            ):
                actor_snapshot["dependent_turn"] = {
                    "kind": _support.STEEL_DEFENDER_TURN_KIND,
                    "owner_actor_id": owner.id,
                    "source_artifact_id": artifact_id,
                    "source_pack_id": pack_id,
                    "source_pack_version": version,
                    "reviewed_expression_hash": template_binding["reviewed_expression_hash"],
                }
            next_encounter = _support.queue_combatant(
                encounter,
                actor_snapshot,
                joins_round=join_round,
            )
            if joining_conditions:
                next_encounter["source_conditions"] = [
                    *list(next_encounter.get("source_conditions") or []),
                    *joining_conditions,
                ]
            campaign_state = _support.validate_party_state(
                {**dict(campaign.state or {}), "combat": next_encounter}
            )
            combat_result = {
                "status": "committed",
                "combat": next_encounter,
                "campaign_revision": campaign.revision + 1,
            }
        lifecycle_expected_revision = expected_revision
        if owner_binding is not None:
            relation_state = _support.deepcopy(
                campaign_state
                if campaign_state is not None
                else dict(relation_campaign.state or {})
            )
            # Re-read from the exact state being submitted. During Combat that
            # snapshot may be newer than the earlier fast-path relation check;
            # never overwrite a concurrently committed relation with the old
            # list merely because the caller supplied the newer revision.
            submitted_relations = _support.validate_dependent_actor_relations(
                relation_state.get("dependent_actor_relations", [])
            )
            submitted_active = [
                relation
                for relation in submitted_relations
                if relation["owner_character_id"] == owner.id
                and relation["relation_key"] == owner_binding["relation_key"]
                and relation["status"] == "active"
            ]
            if len(submitted_active) > 1:
                raise ValueError("dependent actor owner has ambiguous active bound actors")
            if submitted_active and not replace_existing:
                raise ValueError("dependent actor owner already has an active bound actor")
            if replace_existing and (
                len(submitted_active) != 1
                or submitted_active[0]["dependent_actor_id"] != replacement_actor.id
            ):
                raise ValueError("dependent actor replacement target changed before commit")
            if replace_existing:
                replacement_death_tick = int(dict(relation_state["game_time"])["elapsed_ticks"])
                submitted_relations = [
                    {
                        **relation,
                        "status": "replaced",
                        "death_elapsed_ticks": replacement_death_tick,
                        "revival_started_elapsed_ticks": None,
                        "revival_completes_elapsed_ticks": None,
                    }
                    if relation["dependent_actor_id"] == replacement_actor.id
                    else relation
                    for relation in submitted_relations
                ]
            if any(
                relation["owner_character_id"] == owner.id
                and relation["relation_key"] == owner_binding["relation_key"]
                and relation["status"] == "active"
                for relation in submitted_relations
            ):
                raise ValueError("dependent actor owner already has an active bound actor")
            relation_state["dependent_actor_relations"] = [
                *submitted_relations,
                {
                    "owner_character_id": owner.id,
                    "dependent_actor_id": actor_id,
                    "relation_key": owner_binding["relation_key"],
                    "source_artifact_id": artifact_id,
                    "source_pack_id": pack_id,
                    "source_pack_version": version,
                    "status": "active",
                    "created_campaign_revision": relation_base_revision + 1,
                    "created_long_rest_elapsed_ticks": replacement_rest_tick,
                    "death_elapsed_ticks": None,
                    "revival_started_elapsed_ticks": None,
                    "revival_completes_elapsed_ticks": None,
                    "template_binding": _support.deepcopy(template_binding),
                },
            ]
            campaign_state = _support.validate_party_state(relation_state)
            if lifecycle_expected_revision is None:
                lifecycle_expected_revision = relation_campaign.revision
        _support._reject_new_tortle_natural_armor_provenance(actor_sheet)
        _support._reject_new_scag_bladesong_state(actor_sheet)
        created = self.actor_lifecycle.create(
            campaign_id,
            system_id=_support.DND5E.id,
            name=actor_name,
            character_type=character_type,
            player_name=player_name,
            summary=str(summary or parsed.summary),
            sheet=actor_sheet,
            notes=notes_value,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            initial_grants=(_support.InitialActorGrant(principal_id),),
            campaign_state=campaign_state,
            expected_campaign_revision=lifecycle_expected_revision,
            operation="addon.actor.instantiate",
            actor=principal_id,
            branch_id=resolved_branch_id,
            actor_id=actor_id,
            idempotency_payload=addon_request,
            dependent_actor_authorization=dependent_actor_authorization,
            dependent_actor_replacement=(
                {
                    "character_id": replacement_actor.id,
                    "expected_revision": replacement_actor.revision,
                }
                if replacement_actor is not None
                else None
            ),
            response_extra={
                "content_receipt": receipt,
                "actor_knowledge_imported": False,
                "combat": combat_result,
                "replaced_actor_id": (
                    replacement_actor.id if replacement_actor is not None else None
                ),
                "next": None,
            },
        )
        character = self.character_view(created.character)
        return {
            "character": character,
            "content_receipt": receipt,
            "actor_knowledge_imported": False,
            "combat": combat_result,
            "replaced_actor_id": (replacement_actor.id if replacement_actor is not None else None),
            "mutation_group_id": created.mutation_group_id,
            "next": None,
        }

    def _character_content_apply_v2(
        self,
        character_id: str,
        artifact_id: str,
        selection: dict[str, Any] | None = None,
        grant: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply an exact active class/species/background/item/spell/feature artifact.

        Requires the actor's expected_revision and idempotency_key. Read the
        artifact's selection_contract and pass choices in selection. Omit grant
        in Lobby: grant is Play authorization, not an item quantity. For item
        quantity/equipment choices follow the returned catalog selection schema.
        """
        return self.facade_result(
            "apply",
            self.character_content_apply_impl(
                character_id,
                artifact_id,
                selection,
                grant,
                principal_id,
                expected_revision,
                idempotency_key,
            ),
        )

    def content_solution(
        self,
        campaign_id: str,
        action: Literal["query", "compile"],
        actor_id: str,
        source_card_id: str,
        source_card_kind: Literal[
            "activity",
            "feature",
            "item",
            "monster_action",
            "spell",
            "trait",
        ],
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Inspect or author one source-bound custom-content solution at first use."""

        self.access.require_campaign(
            campaign_id,
            principal_id,
            roles=_support.CAMPAIGN_DM_ROLES,
        )
        actor = self.require_campaign_actor(campaign_id, actor_id)
        source_card = self.character_source_card(
            actor.sheet,
            source_card_id,
            source_card_kind,
        )
        if action == "query":
            self.facade_payload(payload)
            return {
                "status": (
                    "compiled"
                    if isinstance(source_card.get("resolution_solution"), dict)
                    else "missing"
                ),
                "actor_id": actor_id,
                "source_card_id": source_card_id,
                "source_card_kind": source_card_kind,
                "resolution_plan": _support.deepcopy(source_card.get("resolution_plan")),
                "resolution_solution": _support.deepcopy(source_card.get("resolution_solution")),
            }
        data = self.facade_payload(payload)
        if expected_revision is None or not idempotency_key:
            raise ValueError(
                "expected_revision and idempotency_key are required "
                "for content solution compilation"
            )
        write_payload = {
            "source_card_id": source_card_id,
            "source_card_kind": source_card_kind,
            "resolution_plan": _support.deepcopy(data["resolution_plan"]),
            "agent_ruling": _support.deepcopy(data["agent_ruling"]),
        }
        current_branch_id = self.require_current_branch(campaign_id, None)
        write_scope = f"character-write:{campaign_id}:{current_branch_id}:{principal_id}:{actor.id}"
        replay = self.replay_idempotent(
            write_scope,
            idempotency_key,
            {
                "operation": "character.content_solution.compile",
                "character_id": actor.id,
                **write_payload,
            },
        )
        if replay is not None:
            return self.facade_result(action, replay)
        if str(source_card.get("pack_id") or "") in {
            _support.CORE_CONTENT_PACK_ID,
            _support.CORE_2024_CONTENT_PACK_ID,
            _support.STANDARD_2014_CONTENT_PACK_ID,
        } or self.source_card_has_executable_mechanic(
            campaign_id,
            source_card,
        ):
            raise _support.CombatEngineError(
                "standard or already executable rule content must use its "
                "locked engine implementation, not an Agent-compiled solution"
            )
        if isinstance(source_card.get("resolution_plan"), dict) or isinstance(
            source_card.get("resolution_solution"), dict
        ):
            raise _support.CombatEngineError("source card already has a compiled solution")
        compiled_plan = self.validate_authored_content_plan(
            campaign_id,
            self.required(data, "resolution_plan"),
            source_card=source_card,
            source_card_id=source_card_id,
            source_card_kind=source_card_kind,
        )
        application_seed = _support.canonical_json(
            {
                "actor_id": actor_id,
                "source_card_id": source_card_id,
                "source_card_kind": source_card_kind,
                "character_revision": actor.revision,
            }
        )
        application_id = (
            f"content:{source_card_kind}:"
            f"{_support.hashlib.sha256(application_seed.encode()).hexdigest()[:24]}"
        )
        try:
            solution = _support.build_content_solution(
                compiled_plan,
                source_card=source_card,
                application_id=application_id,
                agent_ruling=self.required(data, "agent_ruling"),
            )
        except _support.ContentSolutionError as error:
            raise _support.CombatEngineError(
                f"source-bound Agent compilation is invalid: {error}"
            ) from error
        next_sheet = self.sheet_with_content_solution(
            actor.sheet,
            source_card_id=source_card_id,
            source_card_kind=source_card_kind,
            compiled_plan=compiled_plan,
            solution=solution,
        )
        next_sheet = self.finalize_actor_sheet_rulings(next_sheet, campaign_id)
        response = self.update_character(
            actor,
            operation="character.content_solution.compile",
            sheet=next_sheet,
            principal_id=principal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            payload=write_payload,
            response_extra={
                "status": "compiled",
                "solution": solution,
                "resolution_plan_contract": _support.resolution_plan_contract(compiled_plan),
            },
        )
        return self.facade_result(action, response)
