"""Authoring application operations with explicit shared services."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from .. import application_support as _support


class AuthoringService:
    def rule_document_options(
        self,
        checksum: str | None = None,
        page_revisions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        options = {
            "ocr_provider": self.storage.rule_document_ocr_provider(),
            "document_cache_dir": self.config.normalized_rulebooks_dir,
            "expected_checksum": checksum or None,
            "layout_profile": _support.DND5E_DOCUMENT_LAYOUT_PROFILE,
        }
        if page_revisions is not None:
            options["page_revisions"] = _support.deepcopy(page_revisions)
        return options

    def module_document_options(
        self,
        checksum: str | None = None,
        page_revisions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        options = {
            "ocr_provider": self.storage.module_document_ocr_provider(),
            "document_cache_dir": self.config.normalized_modules_dir,
            "expected_checksum": checksum or None,
            "layout_profile": _support.DND5E_DOCUMENT_LAYOUT_PROFILE,
        }
        if page_revisions is not None:
            options["page_revisions"] = _support.deepcopy(page_revisions)
        return options

    def managed_module_source_ref(
        self,
        campaign_id: str,
        value: str | dict[str, Any],
        *,
        require_exact: bool = False,
        expected_scene_id: str | None = None,
        require_active_module: bool = False,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve every module citation through one canonical indexed-chunk contract."""
        normalized = (
            _support.canonical_json(value) if isinstance(value, dict) else str(value).strip()
        )
        if not normalized or len(normalized) > 8192:
            raise ValueError("source_ref must contain 1 to 8192 characters")
        campaign = self.campaigns.get(campaign_id)
        exact_required = require_exact or isinstance(
            dict(campaign.state or {}).get("playthrough_manifest"), dict
        )
        try:
            source = _support.json.loads(normalized)
        except _support.json.JSONDecodeError as exc:
            raise ValueError("source_ref must be a JSON object") from exc
        if not isinstance(source, dict):
            raise ValueError("source_ref must be a JSON object")
        unknown_fields = sorted(set(source) - _support.EXACT_MODULE_SOURCE_FIELDS)
        if unknown_fields:
            raise ValueError("source_ref has unsupported fields: " + ", ".join(unknown_fields))
        missing_managed = sorted(_support.MANAGED_MODULE_SOURCE_FIELDS - set(source))
        if missing_managed:
            raise ValueError(
                "source_ref requires " + ", ".join(sorted(_support.MANAGED_MODULE_SOURCE_FIELDS))
            )
        if any(
            not str(source.get(field) or "").strip()
            for field in _support.MANAGED_MODULE_SOURCE_FIELDS
        ):
            raise ValueError("source_ref identifiers and content_sha256 must not be empty")
        exact_only_fields = (
            _support.EXACT_MODULE_SOURCE_FIELDS - _support.MANAGED_MODULE_SOURCE_FIELDS
        )
        supplied_exact_fields = exact_only_fields & set(source)
        if exact_required or supplied_exact_fields:
            missing_exact = sorted(exact_only_fields - set(source))
            if missing_exact:
                raise ValueError(
                    "source_ref requires " + ", ".join(sorted(_support.EXACT_MODULE_SOURCE_FIELDS))
                )
            heading_path = source["heading_path"]
            if not isinstance(heading_path, list) or any(
                not isinstance(item, str) or not item.strip() for item in heading_path
            ):
                raise ValueError("source_ref heading_path must be a string list")
        try:
            expanded = self.modules.expand(str(source["chunk_id"]))
        except (LookupError, _support.NoResultFound) as error:
            raise LookupError(
                "source_ref chunk_id is not an active runtime chunk; after Pack "
                "activation call module_search and copy the exact source_ref from "
                "module_expand"
            ) from error
        if str(expanded.get("campaign_id")) != campaign_id:
            raise ValueError("source_ref chunk does not belong to the campaign")
        if str(dict(expanded.get("module") or {}).get("id")) != str(source["module_id"]):
            raise ValueError("source_ref module_id does not match its chunk")
        if str(dict(expanded.get("scene") or {}).get("id")) != str(source["scene_id"]):
            raise ValueError("source_ref scene_id does not match its chunk")
        if expected_scene_id is not None and str(source["scene_id"]) != str(expected_scene_id):
            raise ValueError("source_ref scene_id does not match the requested scene")
        if require_active_module and str(source["module_id"]) not in {
            str(item["id"]) for item in self.modules.list(campaign_id)
        }:
            raise ValueError("source_ref module is not active")
        chunk_sha256 = _support.hashlib.sha256(
            str(expanded.get("content") or "").encode("utf-8")
        ).hexdigest()
        if str(source["content_sha256"]).casefold() != chunk_sha256:
            raise ValueError("source_ref content_sha256 does not match its chunk")
        if exact_required or supplied_exact_fields:
            for field in ("page_start", "page_end"):
                if source[field] != expanded.get(field):
                    raise ValueError(f"source_ref {field} does not match its chunk")
            source_heading_path = _support.canonical_heading_path(source["heading_path"])
            expanded_heading_path = _support.canonical_heading_path(
                expanded.get("heading_path") or []
            )
            if source_heading_path != expanded_heading_path:
                raise ValueError("source_ref heading_path does not match its chunk")
        normalized_source = {
            "module_id": str(source["module_id"]),
            "scene_id": str(source["scene_id"]),
            "chunk_id": str(source["chunk_id"]),
            **(
                {
                    "page_start": source["page_start"],
                    "page_end": source["page_end"],
                    "heading_path": list(_support.canonical_heading_path(source["heading_path"])),
                }
                if exact_required or supplied_exact_fields
                else {}
            ),
            "content_sha256": chunk_sha256,
        }
        return (
            _support.canonical_json(normalized_source),
            normalized_source,
            expanded,
        )

    def managed_module_source_excerpt(
        self,
        expanded: dict[str, Any],
        value: Any,
        *,
        field: str = "source_excerpt",
        minimum_length: int = 1,
        maximum_length: int = 2000,
        allow_ordered_omissions: bool = False,
    ) -> str:
        """Normalize and verify cited text against the already-resolved chunk."""
        display = _support.clean_source_evidence_text(value)
        normalized = display.casefold()
        if not minimum_length <= len(display) <= maximum_length:
            raise ValueError(
                f"{field} must contain {minimum_length} to {maximum_length} characters"
            )
        chunk_content = _support._normalize_source_evidence_text(expanded.get("content"))
        ordered_selection = False
        if allow_ordered_omissions and normalized:
            excerpt_tokens = normalized.split()
            chunk_tokens = iter(chunk_content.split())
            ordered_selection = all(
                any(candidate == token for candidate in chunk_tokens) for token in excerpt_tokens
            )
        if normalized not in chunk_content and not ordered_selection:
            raise ValueError(f"{field} is not present in its cited chunk")
        return display

    def validate_embedded_module_source_refs(
        self,
        campaign_id: str,
        value: Any,
        *,
        field: str,
    ) -> None:
        """Validate every embedded managed module citation through one resolver."""

        if isinstance(value, list):
            for index, item in enumerate(value):
                self.validate_embedded_module_source_refs(
                    campaign_id,
                    item,
                    field=f"{field}[{index}]",
                )
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            item_field = f"{field}.{key}"
            if key in {"source_ref", "source_refs"}:
                candidates = item if key == "source_refs" and isinstance(item, list) else [item]
                for index, candidate in enumerate(candidates):
                    if not isinstance(candidate, dict):
                        continue
                    if not (_support.MANAGED_MODULE_SOURCE_FIELDS & set(candidate)):
                        continue
                    candidate_field = (
                        f"{item_field}[{index}]" if key == "source_refs" else item_field
                    )
                    expected_scene_id = str(value.get("source_scene_id") or "").strip() or None
                    managed_candidate = {
                        name: candidate.get(name)
                        for name in _support.EXACT_MODULE_SOURCE_FIELD_ORDER
                    }
                    try:
                        _normalized, _source, expanded = self.managed_module_source_ref(
                            campaign_id,
                            managed_candidate,
                            require_exact=True,
                            expected_scene_id=expected_scene_id,
                        )
                        excerpt = str(
                            value.get("source_excerpt") or candidate.get("excerpt") or ""
                        ).strip()
                        if excerpt:
                            assert expanded is not None
                            self.managed_module_source_excerpt(
                                expanded,
                                excerpt,
                                field=f"{candidate_field}.excerpt",
                            )
                    except (LookupError, ValueError) as error:
                        raise ValueError(f"{candidate_field}: {error}") from error
                continue
            self.validate_embedded_module_source_refs(
                campaign_id,
                item,
                field=item_field,
            )

    def save_rule_pack_draft(
        self,
        *,
        manifest: dict[str, Any],
        artifacts: list[dict[str, Any]] | None,
        mechanics: list[dict[str, Any]] | None,
        provenance: dict[str, Any] | None,
    ) -> dict[str, Any]:
        artifact_values = list(artifacts or [])
        mechanic_values = list(mechanics or [])
        manifest_value, native_errors = self.bind_native_mechanic_contract(
            manifest,
            artifact_values,
            mechanic_values,
        )
        artifacts_without_review_contracts = [
            {
                key: _support.deepcopy(item)
                for key, item in artifact.items()
                if key not in {"catalog_review", "selection_contract"}
            }
            for artifact in artifact_values
        ]
        compiler_errors = [
            *_support.validate_selection_ready_artifacts(artifacts_without_review_contracts),
            *native_errors,
        ]
        compiler_warnings: list[str] = []
        semantic_validation = _support.audit_release_semantic_validation(
            artifact_values,
            settled_mechanic_ids=_support._rule_payload_settled_mechanic_ids(
                {
                    "manifest": manifest_value,
                    "mechanics": mechanic_values,
                }
            ),
        )
        manifest_value["resolution_policy"] = "compiled_or_agent"
        manifest_value["semantic_validation"] = _support.deepcopy(semantic_validation)
        if not semantic_validation["complete"]:
            compiler_warnings.extend(
                f"Agent runtime resolution required for {item['artifact_id']}: {item['reason']}"
                for item in semantic_validation["unresolved"]
            )
        try:
            _support.compile_mechanics(mechanic_values)
        except _support.RuleCompilationError as error:
            compiler_errors.append(str(error))
        declared_tests = list(manifest_value.get("tests") or [])
        if mechanics and not declared_tests:
            compiler_warnings.append("executable rule pack has no declarative tests")
        elif mechanics and not compiler_errors:
            for test_edition in manifest_value.get("editions") or []:
                report = _support.run_mechanic_tests(
                    mechanics or [],
                    declared_tests,
                    edition=test_edition,
                )
                compiler_errors.extend(
                    f"{test_edition}: {error}"
                    for case in report["cases"]
                    if not case["passed"]
                    for error in case["errors"]
                )
                if report["mechanics_uncovered"]:
                    compiler_warnings.append(
                        f"{test_edition}: declarative tests do not exercise mechanics: "
                        + ", ".join(report["mechanics_uncovered"])
                    )
        result = self.rule_packs.save_draft(
            manifest=manifest_value,
            artifacts=artifact_values,
            mechanics=mechanic_values,
            provenance=provenance,
            additional_errors=compiler_errors,
            additional_warnings=compiler_warnings,
        )
        return _support.asdict(result)

    def import_content_module_package(
        self,
        campaign_id: str,
        package: dict[str, Any],
        blobs: dict[str, bytes],
        *,
        principal_id: str,
        idempotency_key: str,
        activate: bool,
        progress_remaps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Import a unified module through module and character service boundaries."""

        normalized = _support.validate_dnd_content_package(package)
        if normalized["system_id"] != _support.DND5E.id or normalized["kind"] != "module":
            raise ValueError("content package must be a dnd5e module")
        campaign_edition = self.campaign_rules_edition(campaign_id)
        supported_editions = list(
            dict(normalized["content"].get("compatibility") or {}).get("editions") or []
        )
        if supported_editions and campaign_edition not in supported_editions:
            raise ValueError(f"module does not support campaign edition {campaign_edition}")
        validated_actors = [
            _support.validate_dnd_content_actor(actor) for actor in normalized["actors"]
        ]
        for actor in validated_actors:
            self.require_engine_owned_character_state(actor["sheet"])
            _support._reject_new_intrinsic_attack_provenance(actor["sheet"])
            _support._reject_new_tortle_natural_armor_provenance(actor.get("sheet"))
            _support._reject_new_battle_ready_provenance(actor["sheet"])
            _support._reject_new_official_item_provenance(actor["sheet"])
        mismatched = [
            actor["id"]
            for actor in validated_actors
            if actor["sheet"]["edition"] != campaign_edition
        ]
        if mismatched:
            raise ValueError(
                "module actor cards do not match the campaign edition: " + ", ".join(mismatched)
            )
        managed_archive = self.storage.write_content_archive(normalized, blobs)
        assets_by_key = {str(item["asset_key"]): item for item in normalized["assets"]}
        result = self.modules.import_content_package(
            campaign_id,
            normalized,
            blobs,
            activate=False,
            asset_writer=self.storage.store_content_module_asset,
        )
        self.modules.register_asset(
            campaign_id=campaign_id,
            module_id=result["module_id"],
            source_path=str(
                (self.config.content_packages_dir / managed_archive["artifact"]).resolve()
            ),
            media_type="application/vnd.sagasmith.content-package+zip",
            checksum=str(managed_archive["archive_checksum"]),
            metadata={
                "asset_kind": "content_package_archive",
                "content_package_id": normalized["id"],
                "content_package_version": normalized["version"],
                "content_package_checksum": normalized["checksum"],
                "content_archive_artifact": managed_archive["artifact"],
            },
        )
        actor_map: dict[str, str] = {}
        binding_ids = []
        module_key = str(normalized["sources"][0]["source_key"])
        for source_actor in validated_actors:
            actor = self.runtime_actor_with_portrait(source_actor, normalized, blobs)
            bindings = list(actor["bindings"])
            preset_pc = (
                any(str(binding.get("binding_kind") or "") == "preset_pc" for binding in bindings)
                or actor["actor_type"] == "pc"
            )
            character = self.characters.import_content_actor(
                actor,
                campaign_id=None if preset_pc else campaign_id,
                assets_by_key=assets_by_key,
                principal_id=principal_id,
                idempotency_key=f"content-module:{idempotency_key}:{actor['id']}",
            )
            actor_map[actor["id"]] = character.id
            effective_bindings = bindings or [
                {
                    "kind": "module",
                    "module_key": module_key,
                    "binding_kind": "preset_pc" if preset_pc else "cast",
                    "role": "",
                }
            ]
            for binding in effective_bindings:
                scene_key = str(binding.get("scene_key") or "")
                saved = self.modules.bind_actor(
                    campaign_id=campaign_id,
                    module_id=result["module_id"],
                    character_id=character.id,
                    actor_card_id=actor["id"],
                    binding_kind=str(
                        binding.get("binding_kind") or ("preset_pc" if preset_pc else "cast")
                    ),
                    role=str(binding.get("role") or ""),
                    scene_id=result["scene_map"].get(scene_key) if scene_key else None,
                    metadata={
                        **dict(binding.get("metadata") or {}),
                        "content_package_checksum": normalized["checksum"],
                        "content_actor_version": actor["version"],
                        "content_actor_provenance": _support.deepcopy(
                            actor.get("provenance") or {}
                        ),
                        "content_actor_metadata": _support.deepcopy(actor.get("metadata") or {}),
                    },
                )
                binding_ids.append(saved["id"])
        result["actor_map"] = actor_map
        result["actor_binding_ids"] = binding_ids
        result.pop("actors", None)
        result["dependencies"] = list(normalized["dependencies"])
        result["artifact"] = managed_archive
        result["activated"] = False
        if activate:
            normalized_remaps: list[dict[str, str]] = []
            remap_targets: dict[str, str] = {}
            for index, raw in enumerate(progress_remaps or []):
                if not isinstance(raw, dict):
                    raise ValueError(f"progress_remaps[{index}] must be an object")
                unknown = set(raw) - {"from_scene_id", "to_scene_key", "reason"}
                if unknown:
                    raise ValueError(
                        f"progress_remaps[{index}] has unsupported fields: {sorted(unknown)}"
                    )
                source_scene_id = str(raw.get("from_scene_id") or "").strip()
                target_scene_key = str(raw.get("to_scene_key") or "").strip()
                reason = str(raw.get("reason") or "").strip()
                if not source_scene_id or not target_scene_key:
                    raise ValueError(
                        f"progress_remaps[{index}] requires from_scene_id and to_scene_key"
                    )
                if target_scene_key not in result["scene_map"]:
                    raise ValueError(
                        f"progress_remaps[{index}].to_scene_key is not in the finalized Pack"
                    )
                if not reason or len(reason) > 1000:
                    raise ValueError(
                        f"progress_remaps[{index}].reason must contain 1 to 1000 characters"
                    )
                if source_scene_id in remap_targets:
                    raise ValueError("progress_remaps contains duplicate from_scene_id values")
                target_scene_id = str(result["scene_map"][target_scene_key])
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
            activation_scope = f"content-module-activation:{campaign_id}"
            activation_key = f"content-module-activate:{idempotency_key}"
            activation_payload = {
                "checksum": normalized["checksum"],
                "module_id": result["module_id"],
                "operation": "activate",
                "progress_remaps": normalized_remaps,
            }
            activation_replay = self.idempotency.lookup(
                activation_scope,
                activation_key,
                activation_payload,
            )
            result["activation"] = (
                dict(activation_replay.response or {}).get("activation")
                if activation_replay is not None
                else self.modules.activate_candidate(
                    campaign_id,
                    result["module_id"],
                    progress_remaps=remap_targets,
                    idempotency_key=activation_key,
                    idempotency_write=_support.IdempotencyWrite(
                        scope=activation_scope,
                        payload=activation_payload,
                        response=lambda value: {"activation": value},
                    ),
                )
            )
            result["activation"] = {
                **dict(result["activation"] or {}),
                "progress_remap_rulings": normalized_remaps,
            }
            result["activated"] = True
        return result

    def managed_module_asset_bytes(self, source_path: str) -> bytes:
        path = _support.Path(source_path).expanduser().resolve()
        if not path.is_file() or not path.is_relative_to(self.config.artifacts_dir.resolve()):
            raise LookupError("module asset is not available in managed MCP storage")
        return path.read_bytes()

    def managed_module_source_digests(self, value: Any) -> set[str]:
        """Collect stable identities for exact managed module citations."""

        result: set[str] = set()
        if isinstance(value, list):
            for item in value:
                result.update(self.managed_module_source_digests(item))
            return result
        if not isinstance(value, dict):
            return result
        for key, item in value.items():
            if key in {"source_ref", "source_refs"}:
                candidates = item if key == "source_refs" and isinstance(item, list) else [item]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    if not (_support.MANAGED_MODULE_SOURCE_FIELDS & set(candidate)):
                        continue
                    exact = {
                        name: candidate.get(name)
                        for name in _support.EXACT_MODULE_SOURCE_FIELD_ORDER
                    }
                    result.add(
                        _support.hashlib.sha256(
                            _support.canonical_json(exact).encode("utf-8")
                        ).hexdigest()
                    )
                continue
            result.update(self.managed_module_source_digests(item))
        return result

    def player_module_scene_view(
        self,
        campaign_id: str,
        scene: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Project a module scene without arbitrary GM progress metadata."""

        if scene is None:
            return None
        if scene.get("visibility", "restricted") not in _support.PLAYER_MODULE_VISIBILITY_SCOPES:
            return {
                "campaign_id": campaign_id,
                "scene_id": scene.get("scene_id"),
                "redacted": True,
                "content": "[DM-only scene content hidden]",
            }
        projected = _support.deepcopy(scene)
        if isinstance(projected.get("progress"), dict):
            progress = dict(projected["progress"])
            progress.pop("state", None)
            projected["progress"] = progress
        spatial = dict(projected.get("spatial") or {})
        spatial.pop("review", None)
        projected["spatial"] = spatial
        return projected

    def module_draft_handle_view(self, job: Any) -> dict[str, Any]:
        """Return the bounded public identity needed to resume one module draft."""

        value = _support.asdict(job)
        payload = dict(value.get("payload") or {})
        result = dict(value.get("result") or {})
        finalized = dict(result.get("finalized_package") or {})
        pack_draft = dict(result.get("pack_draft") or {})
        return {
            "job_id": str(value.get("id") or ""),
            "state": str(value.get("state") or ""),
            "resumable": not bool(finalized),
            "artifact": str(value.get("artifact") or ""),
            "artifact_checksum": str(value.get("artifact_checksum") or ""),
            "source_key": str(payload.get("source_key") or value.get("artifact") or ""),
            "title": str(payload.get("title") or ""),
            "module_id": str(value.get("module_id") or ""),
            "revision": int(value.get("revision") or 0),
            "created_at": value.get("created_at"),
            "updated_at": value.get("updated_at"),
            "pack_decision_fields": sorted(pack_draft),
            "statblock_review_count": len(result.get("statblock_reviews") or []),
            "finalized_artifact": str(finalized.get("artifact") or ""),
            "finalized_pack_id": str(
                dict(finalized.get("summary") or {}).get("id")
                or dict(finalized.get("summary") or {}).get("package_id")
                or ""
            ),
        }

    def module_import_job_create(
        self,
        campaign_id: str,
        artifact: str,
        title: str | None = None,
        source_key: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a staged module package job before parsing or activating a revision."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for an import job")
        path = self.storage.artifact_module_path(artifact)
        logical_key = str(source_key or artifact).strip()
        payload = {
            "artifact": artifact,
            "title": title or path.stem,
            "source_key": logical_key,
        }
        scope = f"import-job-create:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        document = _support.normalize_document(
            path,
            ocr_provider=self.storage.module_document_ocr_provider(),
            cache_dir=self.config.normalized_modules_dir,
            layout_profile=_support.DND5E_DOCUMENT_LAYOUT_PROFILE,
        )
        document_inspection = _support.inspect_character_document(
            document,
            source_name=path.name,
        )
        if document_inspection["document_kind"] != "unknown":
            raise ValueError(
                "character sheets and ability-score option documents are not modules; "
                "use character_query(view='document')"
            )
        preview = self.modules.preview_path(
            path,
            parser=_support.MarkdownModuleParser(profile=_support.DndModuleProfile()),
            **self.module_document_options(document.checksum),
        )
        job = self.import_jobs.create(
            campaign_id=campaign_id,
            kind="module",
            artifact=artifact,
            artifact_checksum=str(preview.get("checksum") or ""),
            payload=payload,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {"job": self.import_job_view(result)},
            ),
        )
        return {"job": self.import_job_view(job)}

    def module_import_job_inspect(
        self,
        campaign_id: str,
        job_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Persist parser preview, stable scene keys, and space evidence for a module job."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for module inspection")
        job = self.require_import_job(campaign_id, job_id, "module")
        payload = {"job_id": job_id, "operation": "inspect"}
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        preview = self.modules.preview_path(
            self.storage.artifact_module_path(job.artifact),
            parser=_support.MarkdownModuleParser(profile=_support.DndModuleProfile()),
            **self.module_document_options(
                job.artifact_checksum,
                self.import_page_revisions(job),
            ),
        )
        updated = self.import_jobs.record_inspection(
            job_id,
            preview,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "preview": preview,
                },
            ),
        )
        return {"job": self.import_job_view(updated), "preview": preview}

    def module_import_job_validate(
        self,
        campaign_id: str,
        job_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Validate a staged module and preview scene/progress impact before importing it."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for module validation")
        job = self.require_import_job(campaign_id, job_id, "module")
        payload = {"job_id": job_id, "operation": "validate"}
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        if job.state not in {"inspected", "validated", "failed"}:
            raise ValueError("module import job must be inspected before validation")
        preview = dict(job.inspection)
        diff = self.modules.diff_preview(
            campaign_id,
            source_key=str(job.payload.get("source_key") or job.artifact),
            preview=preview,
        )
        diff_ruling_requirements: list[dict[str, Any]] = []
        for impact in diff.get("progress_impact", []):
            if impact.get("action") != "needs_dm_review":
                continue
            reason = (
                "The active module revision removed the scene referenced by "
                f"progress scope {impact.get('scope_id')}; decide its source-backed remap."
            )
            requirement = _support._ruling_requirement(reason, "source_or_scene_fact")
            impact["ruling_requirement"] = requirement
            diff_ruling_requirements.append(
                {
                    "scope_id": impact.get("scope_id"),
                    "scene_id": impact.get("scene_id"),
                    **requirement,
                }
            )
        validation = {
            "valid": bool(preview.get("valid")),
            "errors": list(preview.get("errors") or []),
            "warnings": list(preview.get("warnings") or []),
            "preview": preview,
            "diff": diff,
            "ruling_requirements": diff_ruling_requirements,
        }
        updated = self.import_jobs.record_validation(
            job_id,
            validation,
            state="validated" if validation["valid"] else "failed",
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    "validation": validation,
                },
            ),
        )
        return {"job": self.import_job_view(updated), "validation": validation}

    def module_import_job_import(
        self,
        campaign_id: str,
        job_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a validated module inactive, preserving the current active module."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for module import")
        job = self.require_import_job(campaign_id, job_id, "module")
        payload = {"job_id": job_id, "operation": "import"}
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        if job.state not in {"validated", "imported"} or not job.validation.get("valid"):
            raise ValueError("module import job must pass validation before import")
        values = dict(job.payload)
        embedder, vectors = self.storage.dense_components()
        result = self.modules.ingest_path(
            campaign_id=campaign_id,
            path=self.storage.artifact_module_path(job.artifact),
            source_key=str(values.get("source_key") or job.artifact),
            title=str(values.get("title") or job.artifact),
            parser=_support.MarkdownModuleParser(profile=_support.DndModuleProfile()),
            embedder=embedder,
            vector_store=vectors,
            activate=False,
            logical_source_key=str(values.get("source_key") or job.artifact),
            **self.module_document_options(
                job.artifact_checksum,
                self.import_page_revisions(job),
            ),
        )
        updated = self.import_jobs.record_result(
            job_id,
            {**dict(job.result), "module_draft": _support.asdict(result)},
            state="imported",
            module_id=result.module_id,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda updated_job: {
                    "job": self.import_job_view(updated_job),
                    **_support.asdict(result),
                },
            ),
        )
        return {"job": self.import_job_view(updated), **_support.asdict(result)}

    def module_write(
        self, name: str, content: str, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> dict[str, str]:
        """Write generated Markdown to the managed artifact directory before importing it."""
        if not principal_id:
            raise PermissionError("authenticated caller identity is required for module artifacts")
        path = self.storage.write_module(name, content)
        return {"artifact": path.name, "path": str(path)}

    def module_list(
        self, campaign_id: str, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> list[dict[str, Any]]:
        """List a campaign's imported modules."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        rows = self.modules.list(campaign_id)
        if membership.role in _support.CAMPAIGN_DM_ROLES:
            return rows
        return [
            {key: value for key, value in row.items() if key not in {"source_path", "metadata"}}
            for row in rows
        ]

    def module_index(
        self,
        campaign_id: str,
        module_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        """Return a stable scene index for scene selection and safe progression."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        index = self.modules.scene_index(campaign_id, module_id=module_id)
        if membership.role in _support.CAMPAIGN_DM_ROLES:
            return index
        return [
            item
            for item in index
            if item.get("visibility", "restricted") in _support.PLAYER_MODULE_VISIBILITY_SCOPES
        ]

    def module_expand(
        self, chunk_id: str, principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> dict[str, Any]:
        """Read a complete module chunk after it was selected by search."""
        result = self.modules.expand(chunk_id)
        membership = self.access.require_campaign(result["campaign_id"], principal_id)
        visibility = result.get("scene", {}).get("visibility", "restricted")
        if (
            membership.role in _support.CAMPAIGN_DM_ROLES
            or visibility in _support.PLAYER_MODULE_VISIBILITY_SCOPES
        ):
            return result
        return {
            "chunk_id": result["chunk_id"],
            "campaign_id": result["campaign_id"],
            "redacted": True,
            "content": "[DM-only module content hidden]",
        }

    def module_assets(
        self,
        campaign_id: str,
        module_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return self.modules.list_assets(campaign_id, module_id)

    def module_asset_attach(
        self,
        campaign_id: str,
        module_id: str,
        source_path: str,
        *,
        asset_kind: str,
        scene_id: str | None = None,
        location_key: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Attach an allowlisted image or support document to an imported module."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for module asset attachment")
        kind = str(asset_kind).strip()
        if not kind or len(kind) > 80:
            raise ValueError("asset_kind must be a non-empty string of at most 80 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        metadata_value = dict(metadata or {})
        self.modules.list_assets(campaign_id, module_id)
        if scene_id:
            scene = self.modules.read_scene(campaign_id, scene_id)
            if scene["module_id"] != module_id:
                raise ValueError("scene_id does not belong to module_id")
        staged = self.storage.stage_module_asset(module_id, source_path)
        payload = {
            "module_id": module_id,
            "source_checksum": staged["checksum"],
            "asset_kind": kind,
            "scene_id": scene_id,
            "location_key": location_key,
            "title": title,
            "metadata": metadata_value,
        }
        scope = f"module-asset-attach:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        asset_metadata = {
            **metadata_value,
            "kind": kind,
            "source_name": _support.Path(source_path).name,
        }
        if title:
            asset_metadata["title"] = str(title)
        if scene_id:
            asset_metadata["scene_id"] = scene_id
        if location_key:
            asset_metadata["location_key"] = str(location_key)
        asset = self.modules.register_asset(
            campaign_id=campaign_id,
            module_id=module_id,
            source_path=staged["path"],
            media_type=staged["media_type"],
            checksum=staged["checksum"],
            metadata=asset_metadata,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda value: {
                    "campaign_id": campaign_id,
                    "module_id": module_id,
                    "asset": value,
                    "artifact": {
                        key: item for key, item in staged.items() if key not in {"path", "staged"}
                    },
                },
            ),
        )
        response = {
            "campaign_id": campaign_id,
            "module_id": module_id,
            "asset": asset,
            "artifact": {
                key: value for key, value in staged.items() if key not in {"path", "staged"}
            },
        }
        return response

    def module_pdf_asset(
        self,
        campaign_id: str,
        module_id: str,
        source_asset_id: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the one checksum-bound PDF used by module page workflows."""

        assets = self.modules.list_assets(campaign_id, module_id)
        if source_asset_id:
            source_asset = self.modules.get_asset(campaign_id, source_asset_id)
            if source_asset["module_id"] != module_id:
                raise ValueError("source asset does not belong to module")
        else:
            candidates = [item for item in assets if item["media_type"] == "application/pdf"]
            if len(candidates) != 1:
                raise ValueError("source_asset_id is required unless the module has one PDF asset")
            source_asset = candidates[0]
        if source_asset["media_type"] != "application/pdf":
            raise ValueError("module page workflow requires a PDF source asset")
        if _support.file_sha256(source_asset["source_path"]) != source_asset["checksum"]:
            raise RuntimeError("module PDF no longer matches its imported checksum")
        return source_asset

    def module_statblock_ocr_recover(
        self,
        campaign_id: str,
        module_id: str,
        scene_id: str,
        content_key: str,
        name: str,
        page_number: int,
        source_asset_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
        agent_fill: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Recover and review one module statblock without requiring model vision."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        campaign_edition = self.campaign_rules_edition(campaign_id)
        if campaign_edition != "2014":
            raise ValueError(
                "layout OCR statblock recovery currently supports only D&D 2014; "
                "submit an evidence-bound 2024 transcription through module_draft(edit)"
            )
        if not idempotency_key:
            raise ValueError("idempotency_key is required for OCR statblock recovery")
        target_name = str(name or "").strip()
        if not 2 <= len(target_name) <= 200:
            raise ValueError("name must contain 2 to 200 characters")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValueError("page_number must be a positive integer")
        scene = self.modules.read_scene(campaign_id, scene_id)
        if str(scene.get("module_id") or "") != module_id:
            raise ValueError("OCR statblock recovery scene must belong to the module")
        source_asset = self.module_pdf_asset(campaign_id, module_id, source_asset_id)
        provider = self.storage.module_ocr_provider()
        if provider is None or not hasattr(provider, "extract_layout"):
            raise RuntimeError("layout OCR recovery requires the configured RapidOCR provider")

        request_payload = {
            "module_id": module_id,
            "scene_id": scene_id,
            "content_key": content_key,
            "name": target_name,
            "page_number": page_number,
            "source_asset_id": source_asset["id"],
            "source_asset_checksum": source_asset["checksum"],
            "agent_fill": _support.deepcopy(agent_fill),
        }
        scope = f"module-statblock-ocr-recover:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay

        recovered_result = self.recover_pdf_statblock_layout(
            source_path=source_asset["source_path"],
            target_name=target_name,
            candidate_pages=[page_number],
            provider=provider,
        )
        recovered_page = int(recovered_result["page_number"])
        recovered = dict(recovered_result["recovery"])
        normalized_content = str(recovered["normalized_content"])
        parsed = _support.parse_2014_statblock(
            normalized_content,
            source_key=f"module-review:{module_id}:{content_key}",
            name=None,
        )
        agent_fill_requirements = self.statblock_agent_fill_requirements(parsed.sheet)
        if agent_fill_requirements["required"] and agent_fill is None:
            response = {
                "campaign_id": campaign_id,
                "module_id": module_id,
                "scene_id": scene_id,
                "source_asset_id": source_asset["id"],
                **{key: value for key, value in recovered_result.items() if key != "observation"},
                "review": None,
                "requires_agent_fill": True,
                "validation": {
                    "name": parsed.name,
                    "challenge_rating": parsed.challenge_rating,
                    "experience_points": parsed.experience_points,
                    **self.statblock_settlement(parsed.warnings),
                    "agent_fill": None,
                    "resolved_warnings": [],
                    "agent_fill_requirements": agent_fill_requirements,
                },
            }
            return self.remember_idempotent(
                scope,
                idempotency_key,
                request_payload,
                response,
                campaign_id=campaign_id,
            )
        reviewed = self.module_content_review(
            campaign_id,
            module_id,
            scene_id,
            content_key,
            normalized_content,
            str(recovered_result["observation"]),
            source_asset_id=str(source_asset["id"]),
            page_number=recovered_page,
            content_kind="dnd5e_2014_statblock",
            metadata={
                "text_layout_recovery": {
                    "profile": (
                        f"rapidocr-layout-v{int(dict(recovered['evidence'])['recovery_version'])}"
                    ),
                    "provider": str(recovered_result["provider"]),
                    "corroboration_mode": str(recovered_result["corroboration_mode"]),
                    "corroboration_scales": list(recovered_result["corroboration_scales"]),
                    "text_only": True,
                }
            },
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            agent_fill=agent_fill,
        )
        response = {
            "campaign_id": campaign_id,
            "module_id": module_id,
            "scene_id": scene_id,
            "source_asset_id": source_asset["id"],
            **{key: value for key, value in recovered_result.items() if key != "observation"},
            "requires_agent_fill": False,
            **reviewed,
        }
        return self.remember_idempotent(
            scope,
            idempotency_key,
            request_payload,
            response,
            campaign_id=campaign_id,
        )

    def module_content_review(
        self,
        campaign_id: str,
        module_id: str,
        scene_id: str,
        content_key: str,
        normalized_content: str,
        observation: str,
        source_asset_id: str | None = None,
        page_number: int | None = None,
        source_chunk_ids: list[str] | None = None,
        content_kind: Literal[
            "dnd5e_2014_statblock",
            "dnd5e_2024_statblock",
        ] = "dnd5e_2014_statblock",
        metadata: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
        agent_fill: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate an executable transcription and optional Agent semantic fill."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not idempotency_key:
            raise ValueError("idempotency_key is required for module content review")
        campaign_edition = self.campaign_rules_edition(campaign_id)
        expected_content_kind = self.statblock_content_kind(campaign_edition)
        if content_kind != expected_content_kind:
            raise ValueError(f"content_kind must be {expected_content_kind} for this campaign")
        parsed = self.parse_edition_statblock(
            normalized_content,
            edition=campaign_edition,
            source_key=f"module-review:{module_id}:{content_key}",
            name=None,
        )
        agent_fill_requirements = self.statblock_agent_fill_requirements(parsed.sheet)
        if agent_fill_requirements["required"] and agent_fill is None:
            return {
                "review": None,
                "requires_agent_fill": True,
                "validation": {
                    "name": parsed.name,
                    "challenge_rating": parsed.challenge_rating,
                    "experience_points": parsed.experience_points,
                    **self.statblock_settlement(parsed.warnings),
                    "agent_fill": None,
                    "resolved_warnings": [],
                    "agent_fill_requirements": agent_fill_requirements,
                },
            }
        agent_fill_requirements = self.require_complete_statblock_agent_fill(
            parsed.sheet,
            agent_fill,
        )
        agent_fill_evidence = self.reviewed_statblock_fill_evidence(
            campaign_id,
            agent_fill,
            module_id=module_id,
            page_number=page_number,
        )
        metadata_value = _support.deepcopy(dict(metadata or {}))
        if "agent_statblock_fill" in metadata_value:
            raise ValueError("metadata.agent_statblock_fill is reserved; use payload.agent_fill")
        filled = (
            _support.apply_reviewed_statblock_fill(parsed.sheet, agent_fill)
            if agent_fill is not None
            else None
        )
        if filled is not None:
            metadata_value["agent_statblock_fill"] = filled["fill"]
            metadata_value["agent_statblock_fill_evidence"] = agent_fill_evidence
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
        payload = {
            "module_id": module_id,
            "scene_id": scene_id,
            "content_key": content_key,
            "content_kind": content_kind,
            "normalized_content": normalized_content,
            "source_asset_id": source_asset_id,
            "page_number": page_number,
            "source_chunk_ids": source_chunk_ids,
            "observation": observation,
            "metadata": metadata_value,
            "agent_fill": (filled or {}).get("fill"),
        }
        scope = f"module-content-review:{campaign_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        review = self.modules.review_content(
            campaign_id=campaign_id,
            module_id=module_id,
            scene_id=scene_id,
            content_key=content_key,
            content_kind=content_kind,
            normalized_content=normalized_content,
            source_asset_id=source_asset_id,
            page_number=page_number,
            source_chunk_ids=source_chunk_ids,
            reviewer=principal_id,
            observation=observation,
            metadata=metadata_value,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=payload,
                response=lambda value: {
                    "review": value,
                    "validation": {
                        "name": parsed.name,
                        "challenge_rating": parsed.challenge_rating,
                        "experience_points": parsed.experience_points,
                        **self.statblock_settlement(retained_warnings),
                        "agent_fill": (filled or {}).get("fill"),
                        "resolved_warnings": sorted(resolved_warnings),
                        "agent_fill_requirements": agent_fill_requirements,
                    },
                },
            ),
        )
        response = {
            "review": review,
            "validation": {
                "name": parsed.name,
                "challenge_rating": parsed.challenge_rating,
                "experience_points": parsed.experience_points,
                **self.statblock_settlement(retained_warnings),
                "agent_fill": (filled or {}).get("fill"),
                "resolved_warnings": sorted(resolved_warnings),
                "agent_fill_requirements": agent_fill_requirements,
            },
        }
        return response

    def module_content_candidates(
        self,
        campaign_id: str,
        module_id: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        """Recover review-only statblock candidates from imported text chunks."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        module = next(
            (
                item
                for item in self.modules.list(campaign_id, include_retired=True)
                if str(item.get("id")) == module_id
            ),
            None,
        )
        if module is None:
            raise LookupError(module_id)
        chunks = self.modules.list_chunks(campaign_id, module_id)
        campaign_edition = self.campaign_rules_edition(campaign_id)
        candidates = _support.module_statblock_review_candidates(
            chunks,
            source_title=str(module.get("title") or ""),
        )
        for candidate in candidates:
            local_candidate_id = str(candidate["id"])
            candidate["id"] = (
                "candidate:"
                + _support.hashlib.sha256(
                    f"{module_id}\x1f{local_candidate_id}".encode("utf-8")
                ).hexdigest()[:20]
            )
            candidate["review_tool"] = "module_draft"
            candidate["review_action"] = "edit"
            candidate["review_operation"] = "content"
            candidate["module_id"] = module_id
            candidate["content_kind"] = self.statblock_content_kind(campaign_edition)
            if candidate.get("execution_state") == "review_ready":
                parsed_candidate = self.parse_edition_statblock(
                    str(candidate.get("normalized_content") or ""),
                    edition=campaign_edition,
                    source_key=f"module-candidate:{candidate['id']}",
                    name=None,
                )
                candidate["agent_fill_requirements"] = self.statblock_agent_fill_requirements(
                    parsed_candidate.sheet
                )
            if candidate.get("review_status") == "manual_review_required":
                candidate["ruling_requirement"] = _support._ruling_requirement(
                    str(candidate.get("review_error") or "statblock source needs review"),
                    "missing_or_conflicting_source_review",
                )
            elif candidate.get("execution_state") == "review_ready":
                candidate["ruling_requirement"] = _support._ruling_requirement(
                    "Review the normalized statblock against its exact module chunks.",
                    "source_or_scene_fact",
                )
            if len(candidate.get("source_scene_ids") or []) != 1:
                candidate["execution_state"] = "blocked"
                candidate["review_status"] = "manual_review_required"
                candidate["review_error"] = (
                    "statblock candidate source chunks must belong to one scene"
                )
                candidate["ruling_requirement"] = _support._ruling_requirement(
                    candidate["review_error"],
                    "missing_or_conflicting_source_review",
                )
                continue
            candidate["scene_id"] = candidate["source_scene_ids"][0]
        return candidates

    def module_read_scene(
        self,
        campaign_id: str,
        scene_id: str,
        scope_id: str = "party",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Read one full scene, including its structured rooms and visibility metadata."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        resolved_scope_id = self.readable_scene_scope(campaign_id, scope_id, principal_id)
        result = self.modules.read_scene(campaign_id, scene_id, scope_id=resolved_scope_id)
        if membership.role in _support.CAMPAIGN_DM_ROLES:
            return result
        return self.player_module_scene_view(campaign_id, result)

    def module_scene_preflight(
        self,
        campaign_id: str,
        scene_id: str,
        participant_manifest: dict[str, Any],
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Validate source-grounded combatants and reserves before an encounter starts."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if not isinstance(participant_manifest, dict):
            raise ValueError("participant_manifest must be an object")
        manifest_fields = {"schema_version", "groups", "notes"}
        unknown_manifest = set(participant_manifest) - manifest_fields
        if unknown_manifest:
            raise ValueError(
                f"unsupported participant manifest fields: {sorted(unknown_manifest)}; "
                f"allowed fields: {sorted(manifest_fields)}"
            )
        schema_version = participant_manifest.get("schema_version", 1)
        if schema_version != 1:
            raise ValueError("participant_manifest schema_version must be 1")
        groups = participant_manifest.get("groups")
        if not isinstance(groups, list):
            raise ValueError("participant_manifest.groups must be a list")
        encounter_scene = self.modules.read_scene(campaign_id, scene_id)
        module_id = str(encounter_scene["module_id"])
        normalized_groups: list[dict[str, Any]] = []
        group_keys: set[str] = set()
        used_actor_ids: set[str] = set()
        initial_actor_ids: list[str] = []
        reinforcement_actor_ids: list[str] = []
        optional_actor_ids: list[str] = []

        for index, raw_group in enumerate(groups):
            if not isinstance(raw_group, dict):
                raise ValueError("each participant manifest group must be an object")
            allowed = {
                "key",
                "label",
                "role",
                "required_count",
                "actor_ids",
                "source_scene_id",
                "source_excerpt",
            }
            unknown = set(raw_group) - allowed
            if unknown:
                raise ValueError(
                    f"unsupported participant group fields: {sorted(unknown)}; "
                    f"allowed fields: {sorted(allowed)}"
                )
            key = str(raw_group.get("key") or "").strip()
            if not key or key in group_keys:
                raise ValueError("participant manifest group keys must be non-empty and unique")
            group_keys.add(key)
            role = str(raw_group.get("role") or "").strip()
            if role not in {"combatant", "reinforcement", "optional"}:
                raise ValueError(
                    "participant manifest role must be combatant, reinforcement, or optional"
                )
            required_count = raw_group.get("required_count")
            if (
                isinstance(required_count, bool)
                or not isinstance(required_count, int)
                or required_count < 1
            ):
                raise ValueError("participant group required_count must be a positive integer")
            actor_ids_value = raw_group.get("actor_ids", [])
            if not isinstance(actor_ids_value, list):
                raise ValueError("participant group actor_ids must be a list")
            actor_ids = [str(item).strip() for item in actor_ids_value]
            if any(not item for item in actor_ids) or len(actor_ids) != len(set(actor_ids)):
                raise ValueError("participant group actor_ids must be non-empty and unique")
            overlap = used_actor_ids & set(actor_ids)
            if overlap:
                raise ValueError(
                    "actors cannot appear in multiple participant groups: "
                    + ", ".join(sorted(overlap))
                )
            if len(actor_ids) > required_count:
                raise ValueError(f"participant group {key!r} exceeds required_count")
            used_actor_ids.update(actor_ids)

            source_scene_id = str(raw_group.get("source_scene_id") or scene_id)
            source_scene = (
                encounter_scene
                if source_scene_id == scene_id
                else self.modules.read_scene(campaign_id, source_scene_id)
            )
            if str(source_scene.get("module_id")) != module_id:
                raise ValueError("participant evidence must belong to the encounter module")
            source_excerpt = " ".join(str(raw_group.get("source_excerpt") or "").split()).strip()
            if len(source_excerpt) < 8 or len(source_excerpt) > 4000:
                raise ValueError("participant source_excerpt must contain 8 to 4000 characters")
            normalized_excerpt = _support._normalize_source_evidence_text(source_excerpt)
            normalized_content = _support._normalize_source_evidence_text(
                source_scene.get("content")
            )
            if normalized_excerpt not in normalized_content:
                raise ValueError(
                    f"participant group {key!r} source_excerpt is not present in its scene"
                )

            actor_views = []
            for actor_id in actor_ids:
                actor = self.require_campaign_actor(campaign_id, actor_id)
                actor_views.append(
                    {
                        "id": actor.id,
                        "name": actor.name,
                        "character_type": actor.character_type,
                        "combat_card": self.combat_card_analysis(actor),
                    }
                )
            invalid_actor_ids = [
                str(item["id"])
                for item in actor_views
                if not dict(item.get("combat_card") or {}).get("card_valid", False)
            ]
            missing_count = required_count - len(actor_ids)
            blocking = role != "optional"
            normalized_groups.append(
                {
                    "key": key,
                    "label": str(raw_group.get("label") or key).strip(),
                    "role": role,
                    "required_count": required_count,
                    "actor_ids": actor_ids,
                    "actors": actor_views,
                    "missing_count": missing_count,
                    "invalid_count": len(invalid_actor_ids),
                    "invalid_actor_ids": invalid_actor_ids,
                    "blocking": blocking,
                    "source_scene_id": source_scene_id,
                    "source_excerpt": source_excerpt,
                    "ordinal": index,
                }
            )
            target = (
                initial_actor_ids
                if role == "combatant"
                else reinforcement_actor_ids
                if role == "reinforcement"
                else optional_actor_ids
            )
            target.extend(actor_ids)

        ready = all(
            not item["blocking"] or (item["missing_count"] == 0 and item["invalid_count"] == 0)
            for item in normalized_groups
        )
        normalized_manifest = {
            "schema_version": 1,
            "scene_id": scene_id,
            "module_id": module_id,
            "groups": normalized_groups,
            "notes": str(participant_manifest.get("notes") or "").strip(),
        }
        return {
            **normalized_manifest,
            "checksum": _support.request_hash(normalized_manifest),
            "ready": ready,
            "initial_actor_ids": initial_actor_ids,
            "reinforcement_actor_ids": reinforcement_actor_ids,
            "optional_actor_ids": optional_actor_ids,
        }

    def module_current(
        self,
        campaign_id: str,
        scope_id: str = "party",
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any] | None:
        """Read the current scene for party, group, or player scope with party fallback."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        resolved_scope_id = self.readable_scene_scope(campaign_id, scope_id, principal_id)
        result = self.modules.current_scene(campaign_id, scope_id=resolved_scope_id)
        if result is None or membership.role in _support.CAMPAIGN_DM_ROLES:
            return result
        return self.player_module_scene_view(campaign_id, result)

    def module_progress_index(
        self,
        campaign_id: str,
        scope_id: str = "party",
        module_id: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> list[dict[str, Any]]:
        """Project ordered scene progress without adding another public MCP tool."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        resolved_scope_id = self.readable_scene_scope(campaign_id, scope_id, principal_id)
        result = self.modules.scene_progress_index(
            campaign_id,
            scope_id=resolved_scope_id,
            module_id=module_id,
        )
        if membership.role in _support.CAMPAIGN_DM_ROLES:
            return result
        visible_scene_ids = {
            item["scene_id"] for item in self.module_index(campaign_id, module_id, principal_id)
        }
        return [item for item in result if item["scene_id"] in visible_scene_ids]

    def module_set_progress(
        self,
        campaign_id: str,
        scene_id: str,
        scope_id: str = "party",
        status: str | None = None,
        progress: int | None = None,
        state: dict[str, Any] | None = None,
        current_room: str | None = None,
        current_location_key: str | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_state_version: int | None = None,
        idempotency_key: str | None = None,
        spatial_review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist scoped progress or a source-backed visual atlas review.

        Requires expected_state_version from scene progress (0 for its first
        write), not the campaign revision, and idempotency_key. progress is an
        integer. Record observed play only; a progress write does not resolve an
        encounter, create actors, or prove that a scene objective was achieved.
        Set status="current" when the party actually enters this scene; only
        that status selects module_query(view="current"). "in_progress" records
        unfinished work but does not select the current scene. Read progress to
        recover a known location when no current pointer exists, then select that
        source scene explicitly. Never invent a scene because current is null.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if expected_state_version is None or not idempotency_key:
            raise ValueError(
                "expected_state_version and idempotency_key are required for scene progress"
            )
        self.validate_embedded_module_source_refs(
            campaign_id,
            state or {},
            field="state",
        )
        self.validate_embedded_module_source_refs(
            campaign_id,
            spatial_review or {},
            field="spatial_review",
        )
        branch_id = self.current_branch_id(campaign_id)
        self.require_no_active_npc_conversation(
            campaign_id,
            branch_id=branch_id,
            operation="changing scene progress",
        )
        request_payload = {
            "scene_id": scene_id,
            "scope_id": scope_id,
            "status": status,
            "progress": progress,
            "state": state,
            "current_room": current_room,
            "current_location_key": current_location_key,
            "expected_state_version": expected_state_version,
            "branch_id": branch_id,
            "spatial_review": spatial_review,
        }
        scope = f"module-progress:{campaign_id}:{branch_id}:{principal_id}:{scope_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return replay
        response = self.modules.set_scene_progress(
            campaign_id=campaign_id,
            scene_id=scene_id,
            scope_id=scope_id,
            status=status,
            progress=progress,
            state=state,
            current_room=current_room,
            current_location_key=current_location_key,
            expected_state_version=expected_state_version,
            spatial_review=(
                {
                    **dict(spatial_review),
                    "reviewer": principal_id,
                    "branch_id": branch_id,
                }
                if spatial_review is not None
                else None
            ),
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=lambda result: result,
            ),
        )
        return response

    def module_search(
        self,
        campaign_id: str,
        query: str,
        top_k: int = 8,
        module_ids: list[str] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Search adventure content, optionally scoped to exact active module revisions."""
        membership = self.access.require_campaign(campaign_id, principal_id)
        embedder, vectors = self.storage.dense_components()
        hits = self.modules.search(
            campaign_id=campaign_id,
            query=query,
            query_hints=_support.DND5E_QUERY_HINTS,
            top_k=100,
            module_ids=module_ids,
            embedder=embedder,
            vector_store=vectors,
        )
        if membership.role in _support.CAMPAIGN_DM_ROLES:
            values = [_support.asdict(hit) for hit in hits]
        else:
            values = [
                _support.asdict(hit)
                for hit in hits
                if hit.metadata.get("visibility", "restricted")
                in _support.PLAYER_MODULE_VISIBILITY_SCOPES
            ]
        values, page = _support._bounded_page(
            values,
            scope=(
                f"module_search:{campaign_id}:{principal_id}:{query}:"
                f"{','.join(sorted(module_ids or []))}"
            ),
            limit=top_k,
            cursor=cursor,
        )
        return _support._facade_result("search", values, page=page)

    def rule_document_stage(
        self,
        campaign_id: str,
        source_path: str,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        """Stage an allowlisted PDF/Markdown/text rulebook in MCP-owned storage."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        return self.storage.stage_rulebook(source_path)

    def rule_document_page_render(
        self,
        campaign_id: str,
        job_id: str,
        page_number: int,
        scale: float = 1.5,
        include_ocr_text: bool = True,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> Any:
        """Render one staged rulebook PDF page as checksum-bound Agent review evidence."""
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        source = self.storage.artifact_rulebook_path(job.artifact)
        if source.suffix.casefold() != ".pdf":
            raise ValueError("rulebook page rendering requires a staged PDF")
        rendered = _support.render_pdf_page(source, page_number, scale=scale)
        if rendered.source_checksum != job.artifact_checksum:
            raise RuntimeError("rulebook PDF no longer matches its staged checksum")
        if not isinstance(include_ocr_text, bool):
            raise ValueError("include_ocr_text must be a boolean")
        transcription = self.staged_transcription_evidence(
            job,
            page_number,
            include_ocr=include_ocr_text,
        )
        return [
            {
                "campaign_id": campaign_id,
                "job_id": job_id,
                "artifact": job.artifact,
                "source_checksum": rendered.source_checksum,
                "page_number": rendered.page_number,
                "page_count": rendered.page_count,
                "width": rendered.width,
                "height": rendered.height,
                "scale": rendered.scale,
                "image_checksum": rendered.checksum,
                "ocr": (transcription["ocr"] if include_ocr_text else {"included": False}),
                "transcription": {
                    key: value for key, value in transcription.items() if key != "ocr"
                },
            },
            _support.Image(data=rendered.content, format="png"),
        ]

    def cached_rapidocr_layout(
        self,
        source_path: str | _support.Path,
        page_number: int,
        *,
        scale: float,
        preferred_provider: _support.RapidOcrProvider | None = None,
        model_type: str | None = None,
    ) -> _support.OcrPageLayout:
        """Reuse one rendered/OCR layout across catalog entries on the same page."""

        source = _support.Path(source_path).expanduser().resolve()
        stat = source.stat()
        normalized_scale = round(float(scale), 3)
        normalized_model = str(model_type or getattr(preferred_provider, "model_type", "small"))
        cache_key = (
            str(source),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            normalized_model,
            normalized_scale,
            page_number,
        )
        cached = self.rapidocr_layout_cache.get(cache_key)
        if cached is not None:
            return cached
        provider = preferred_provider
        if (
            provider is None
            or abs(float(provider.scale) - normalized_scale) >= 0.001
            or str(getattr(provider, "model_type", "small")) != normalized_model
        ):
            provider = _support._cached_rapidocr_provider(
                self.rapidocr_providers,
                model_type=normalized_model,
                scale=normalized_scale,
                cache_dir=self.config.ocr_page_cache_dir,
            )
        layout = provider.extract_layout(source, page_numbers=[page_number])[0]
        self.rapidocr_layout_cache[cache_key] = layout
        return layout

    def local_ocr_page_evidence(
        self,
        source_path: str | _support.Path,
        page_number: int,
        *,
        scope: Literal["rulebook", "module"],
    ) -> dict[str, Any]:
        """Return checksum-bound local OCR so visionless Agents can review a page."""

        if scope == "rulebook":
            enabled = self.config.rule_ocr_enabled
            provider = self.storage.rule_ocr_provider()
            scale = self.config.rule_ocr_scale
            model = self.config.rule_ocr_model
        else:
            enabled = self.config.module_ocr_enabled
            provider = self.storage.module_ocr_provider()
            scale = self.config.module_ocr_scale
            model = self.config.module_ocr_model
        if not enabled or provider is None:
            return {
                "included": True,
                "available": False,
                "reason": f"{scope} OCR is disabled",
            }
        variants: list[dict[str, Any]] = []
        for variant_model in self.storage.ocr_model_chain(model):
            variant_provider = (
                provider
                if variant_model == model
                else _support._cached_rapidocr_provider(
                    self.rapidocr_providers,
                    model_type=variant_model,
                    scale=scale,
                    cache_dir=self.config.ocr_page_cache_dir,
                )
            )
            try:
                layout = self.cached_rapidocr_layout(
                    source_path,
                    page_number,
                    scale=scale,
                    preferred_provider=variant_provider,
                    model_type=variant_model,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                variants.append(
                    {
                        "available": False,
                        "provider": variant_provider.name,
                        "profile": variant_provider.cache_profile,
                        "model": variant_model,
                        "scale": scale,
                        "page_number": page_number,
                        "error": str(exc)[:500],
                    }
                )
                continue
            page_text, used_columns = _support.ocr_layout_text(layout)
            confidences = [float(block.confidence) for block in layout.blocks]
            statblock_slots = [
                {key: value for key, value in item.items() if not key.startswith("_")}
                for item in _support.discover_2014_statblock_slots_from_layout(
                    layout.as_dict(),
                    minimum_confidence=0.5,
                )
            ]
            variants.append(
                {
                    "available": True,
                    "provider": variant_provider.name,
                    "profile": variant_provider.cache_profile,
                    "model": variant_model,
                    "scale": scale,
                    "page_number": page_number,
                    "used_column_recovery": used_columns,
                    "block_count": len(layout.blocks),
                    "average_confidence": (
                        sum(confidences) / len(confidences) if confidences else 0.0
                    ),
                    "minimum_confidence": min(confidences) if confidences else 0.0,
                    "statblock_slots": statblock_slots,
                    "text_sha256": _support.hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
                    "text": page_text[:50000],
                    "truncated": len(page_text) > 50000,
                }
            )
        available_variants = [item for item in variants if item["available"]]
        if not available_variants:
            return {
                "included": True,
                "available": False,
                "reason": "all configured local OCR models failed",
                "variants": variants,
            }
        primary = available_variants[0]
        return {
            "included": True,
            "available": True,
            **primary,
            "variants": variants,
        }

    def recover_pdf_statblock_layout(
        self,
        *,
        source_path: str | _support.Path,
        target_name: str,
        candidate_pages: list[int],
        provider: _support.RapidOcrProvider,
        statblock_slot: int | None = None,
        ocr_corrections: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Supply authorized OCR I/O to deterministic D&D recovery."""

        primary_scale = float(getattr(provider, "scale", 2.0))
        preferred_model = str(getattr(provider, "model_type", "small"))

        def load_layout(
            page_number: int,
            scale: float,
            model_type: str,
            use_preferred_provider: bool,
        ) -> dict[str, Any]:
            return self.cached_rapidocr_layout(
                source_path,
                page_number,
                scale=scale,
                preferred_provider=provider if use_preferred_provider else None,
                model_type=model_type,
            ).as_dict()

        return _support.recover_2014_pdf_statblock_layout(
            target_name=target_name,
            candidate_pages=candidate_pages,
            provider_name=provider.name,
            primary_scale=primary_scale,
            preferred_model=preferred_model,
            load_layout=load_layout,
            extract_page_text=lambda page_number: _support.extract_pdf_page_text(
                source_path, page_number
            ),
            statblock_slot=statblock_slot,
            ocr_corrections=ocr_corrections,
        )

    def rule_statblock_ocr_recover(
        self,
        campaign_id: str,
        job_id: str,
        name: str,
        page_number: int | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        idempotency_key: str | None = None,
        agent_fill: dict[str, Any] | None = None,
        statblock_slot: int | None = None,
        ocr_corrections: dict[str, Any] | None = None,
        correction_evidence_basis: Literal["staged_text", "rendered_page"] = "staged_text",
        rendered_image_checksum: str | None = None,
    ) -> dict[str, Any]:
        """Recover and review one statblock through layout OCR for text-only agents."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise ValueError(
                "layout OCR statblock recovery currently supports only D&D 2014; "
                "submit an evidence-bound 2024 transcription through "
                "rulebook_draft(edit, operation='statblock_review')"
            )
        if not idempotency_key:
            raise ValueError("idempotency_key is required for OCR statblock recovery")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        if not job.source_id:
            raise ValueError("rule import job must be indexed before OCR recovery")
        source_path = self.storage.artifact_rulebook_path(job.artifact)
        if source_path.suffix.casefold() != ".pdf":
            raise ValueError("OCR statblock recovery requires a staged PDF")
        target_name = str(name or "").strip()
        if not 2 <= len(target_name) <= 200:
            raise ValueError("name must contain 2 to 200 characters")
        if page_number is not None and (
            isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1
        ):
            raise ValueError("page_number must be a positive integer")
        if statblock_slot is not None and (
            isinstance(statblock_slot, bool)
            or not isinstance(statblock_slot, int)
            or statblock_slot < 1
        ):
            raise ValueError("statblock_slot must be a positive integer")
        if statblock_slot is not None and page_number is None:
            raise ValueError("statblock_slot requires an exact page_number")
        if ocr_corrections is not None and (statblock_slot is None or page_number is None):
            raise ValueError("ocr_corrections require an exact page_number and statblock_slot")
        if correction_evidence_basis not in {"staged_text", "rendered_page"}:
            raise ValueError("correction_evidence_basis is invalid")
        if ocr_corrections is None and (
            correction_evidence_basis != "staged_text" or rendered_image_checksum is not None
        ):
            raise ValueError("correction evidence requires ocr_corrections")
        correction_evidence: dict[str, Any] | None = None
        normalized_ocr_corrections: dict[str, Any] | None = None
        if ocr_corrections is not None:
            allowed_corrections = {"abilities", "text_replacements"}
            if (
                not isinstance(ocr_corrections, dict)
                or not ocr_corrections
                or set(ocr_corrections) - allowed_corrections
            ):
                raise ValueError("ocr_corrections supports only abilities and text_replacements")
            abilities = ocr_corrections.get("abilities")
            if abilities is not None and (not isinstance(abilities, dict) or not abilities):
                raise ValueError("ocr_corrections.abilities must be a non-empty object")
            normalized_abilities: dict[str, str] = {}
            page_fact_key = _support._ocr_fact_key(
                _support.extract_pdf_page_text(source_path, int(page_number))
            )
            if correction_evidence_basis == "rendered_page":
                supplied_checksum = str(rendered_image_checksum or "").strip().lower()
                if _support.re.fullmatch(r"[0-9a-f]{64}", supplied_checksum) is None:
                    raise ValueError(
                        "rendered_page correction evidence requires rendered_image_checksum"
                    )
                rendered = _support.render_pdf_page(source_path, int(page_number), scale=1.5)
                if supplied_checksum != rendered.checksum:
                    raise ValueError(
                        "rendered page checksum does not match statblock correction evidence"
                    )
                correction_evidence = {
                    "basis": "rendered_page",
                    "rendered_image_checksum": supplied_checksum,
                    "page_number": int(page_number),
                }
            else:
                if rendered_image_checksum is not None:
                    raise ValueError(
                        "rendered_image_checksum is valid only for rendered_page evidence"
                    )
                correction_evidence = {
                    "basis": "staged_text",
                    "page_number": int(page_number),
                    "text_sha256": _support.hashlib.sha256(
                        _support.extract_pdf_page_text(source_path, int(page_number)).encode(
                            "utf-8"
                        )
                    ).hexdigest(),
                }
            for raw_ability, raw_value in dict(abilities or {}).items():
                ability = str(raw_ability or "").strip().lower()
                value = " ".join(str(raw_value or "").split())
                if ability not in {"str", "dex", "con", "int", "wis", "cha"}:
                    raise ValueError("ocr_corrections contains an unknown ability")
                if (
                    _support.re.fullmatch(r"(?:[1-9]|[12][0-9]|30) \([+\-][0-9]{1,2}\)", value)
                    is None
                ):
                    raise ValueError(
                        "ocr_corrections ability values must use 'score (+/-modifier)'"
                    )
                if (
                    correction_evidence_basis == "staged_text"
                    and _support._ocr_fact_key(f"{ability.upper()} {value}") not in page_fact_key
                ):
                    raise ValueError(
                        "ocr_corrections ability value is not corroborated by the "
                        f"staged page text: {ability}={value}"
                    )
                normalized_abilities[ability] = value
            text_replacements = ocr_corrections.get("text_replacements")
            if text_replacements is not None and (
                not isinstance(text_replacements, list)
                or not text_replacements
                or len(text_replacements) > 20
            ):
                raise ValueError("ocr_corrections.text_replacements must contain 1 to 20 entries")
            normalized_text_replacements: list[dict[str, str]] = []
            seen_old: set[str] = set()
            for index, raw_replacement in enumerate(text_replacements or []):
                if not isinstance(raw_replacement, dict) or set(raw_replacement) != {
                    "old",
                    "new",
                }:
                    raise ValueError(
                        "ocr_corrections.text_replacements entries require only old and new"
                    )
                old = " ".join(str(raw_replacement.get("old") or "").split())
                new = " ".join(str(raw_replacement.get("new") or "").split())
                if not old or not new or old == new or len(old) > 500 or len(new) > 2000:
                    raise ValueError(
                        "ocr_corrections text replacement is empty, unchanged, or too long"
                    )
                old_key = old.casefold()
                if old_key in seen_old:
                    raise ValueError("ocr_corrections text replacements require unique old text")
                if (
                    correction_evidence_basis == "staged_text"
                    and _support._ocr_fact_key(new) not in page_fact_key
                ):
                    raise ValueError(
                        "ocr_corrections replacement is not corroborated by the staged "
                        f"page text at index {index}"
                    )
                seen_old.add(old_key)
                normalized_text_replacements.append({"old": old, "new": new})
            normalized_ocr_corrections = {
                **({"abilities": normalized_abilities} if normalized_abilities else {}),
                **(
                    {"text_replacements": normalized_text_replacements}
                    if normalized_text_replacements
                    else {}
                ),
            }
            if not normalized_ocr_corrections:
                raise ValueError("ocr_corrections must contain a non-empty correction")
        recovery_request = {
            "operation": "recover_statblock",
            "job_id": job_id,
            "source_id": str(job.source_id),
            "source_checksum": str(job.artifact_checksum),
            "name": target_name,
            "page_number": page_number,
            "statblock_slot": statblock_slot,
            "ocr_corrections": _support.deepcopy(normalized_ocr_corrections),
            "correction_evidence": _support.deepcopy(correction_evidence),
            "agent_fill": _support.deepcopy(agent_fill),
            "recovery_version": _support.OCR_STATBLOCK_RECOVERY_VERSION,
        }
        recovery_scope = f"rule-statblock-ocr:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(
            recovery_scope,
            idempotency_key,
            recovery_request,
        )
        if replay is not None:
            return replay
        provider = self.storage.rule_ocr_provider()
        if provider is None or not hasattr(provider, "extract_layout"):
            raise RuntimeError("layout OCR recovery requires the configured RapidOCR provider")

        chunks = self.rules.source_chunks(job.source_id)
        page_count = int(dict(job.inspection or {}).get("page_count", 0) or 0)
        if page_count < 1:
            raise RuntimeError("rule import inspection has no page count")
        candidate_pages: list[int] = []
        if page_number is not None:
            candidate_pages.append(page_number)
        else:
            target_pattern = _support.re.compile(
                rf"(?i)\b{_support.re.escape(target_name)}\s*,\s*(\d{{1,4}})\b"
            )
            printed_hints = [
                int(match.group(1))
                for chunk in chunks
                for match in target_pattern.finditer(str(chunk.get("content") or ""))
            ]
            if not printed_hints:
                raise ValueError(
                    "OCR recovery could not infer a printed page; provide payload.page_number"
                )
            for hint in printed_hints:
                for offset in (0, 1, -1, 2, -2, 3, -3, 4, -4):
                    candidate = hint + offset
                    if 1 <= candidate <= page_count and candidate not in candidate_pages:
                        candidate_pages.append(candidate)

        recovered_result = self.recover_pdf_statblock_layout(
            source_path=source_path,
            target_name=target_name,
            candidate_pages=candidate_pages,
            provider=provider,
            statblock_slot=statblock_slot,
            ocr_corrections=normalized_ocr_corrections,
        )
        recovered_page = int(recovered_result["page_number"])
        recovered = dict(recovered_result["recovery"])
        if correction_evidence is not None:
            recovered["evidence"] = {
                **dict(recovered.get("evidence") or {}),
                "correction_evidence": _support.deepcopy(correction_evidence),
            }
            recovered_result["recovery"] = recovered
        content = str(recovered["normalized_content"])
        reviewed_observation = str(recovered_result["observation"])
        if statblock_slot is not None:
            reviewed_observation += (
                " Agent named structural statblock slot; the engine retained "
                "the checksum-bound layout mechanics."
            )
        if correction_evidence is not None:
            reviewed_observation += " Agent-authored OCR corrections were bound to " + (
                "the exact rendered page image checksum."
                if correction_evidence_basis == "rendered_page"
                else "the immutable staged page text checksum."
            )
        derived_from_review_id = None
        if statblock_slot is not None:
            all_statblock_reviews = [
                dict(review)
                for review in list(dict(job.result or {}).get("statblock_reviews") or [])
            ]
            existing_same_content = [
                review
                for review in all_statblock_reviews
                if int(review.get("page_number") or 0) == recovered_page
                and str(review.get("source_id") or "") == str(job.source_id)
                and str(review.get("asset_checksum") or "") == str(job.artifact_checksum)
                and str(review.get("normalized_content") or "").strip() == content
                and str(review.get("review_mode") or "") == "layout_ocr"
            ]
            if existing_same_content:
                # A new key may converge on content already reviewed by an
                # older route. Preserve lineage only when all identical
                # reviews agree; otherwise create an unambiguous root review.
                stored_parents = {
                    str(review.get("derived_from_review_id") or "")
                    for review in existing_same_content
                }
                if len(stored_parents) == 1:
                    stored_parent = next(iter(stored_parents))
                    derived_from_review_id = str(stored_parent) if stored_parent else None
            parsed_recovery = _support.parse_2014_statblock_template_preview(
                content,
                source_key=f"slot-recovery:{job.id}:{recovered_page}:{statblock_slot}",
                rule_refs=[],
            )
            recovery_identity = _support._statblock_mechanical_identity(parsed_recovery)
            mechanical_matches: list[dict[str, Any]] = []
            if not existing_same_content:
                for review in _support._select_preferred_statblock_reviews(all_statblock_reviews):
                    if int(review.get("page_number") or 0) != recovered_page:
                        continue
                    prior_content = str(review.get("normalized_content") or "").strip()
                    if not prior_content or prior_content == content:
                        continue
                    try:
                        prior_parsed = _support.parse_2014_statblock_template_preview(
                            prior_content,
                            source_key=str(review.get("id") or "prior-slot-review"),
                            rule_refs=[],
                        )
                    except (_support.StatblockImportError, ValueError):
                        continue
                    if _support._statblock_mechanical_identity(prior_parsed) == recovery_identity:
                        mechanical_matches.append(review)
                if len(mechanical_matches) == 1:
                    derived_from_review_id = str(mechanical_matches[0]["id"])
        review_idempotency_key = (
            "ocr-review-"
            + _support.hashlib.sha256(
                _support.canonical_json(
                    {
                        "outer_key": idempotency_key,
                        "request": recovery_request,
                        "page_number": recovered_page,
                        "normalized_content_sha256": _support.hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                    }
                ).encode("utf-8")
            ).hexdigest()[:32]
        )
        reviewed = self.rule_statblock_review(
            campaign_id,
            job_id,
            recovered_page,
            content,
            reviewed_observation,
            principal_id=principal_id,
            idempotency_key=review_idempotency_key,
            review_mode="layout_ocr",
            agent_fill=agent_fill,
            derived_from_review_id=derived_from_review_id,
        )
        response = {
            "campaign_id": campaign_id,
            "job_id": job_id,
            **{key: value for key, value in recovered_result.items() if key != "observation"},
            **reviewed,
        }
        return self.remember_idempotent(
            recovery_scope,
            idempotency_key,
            recovery_request,
            response,
            campaign_id=campaign_id,
        )

    def rule_pack_draft_from_source(
        self,
        source_id: str,
        manifest: dict[str, Any],
        artifacts: list[dict[str, Any]] | None = None,
        mechanics: list[dict[str, Any]] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Draft a pack whose citations are resolved from imported rule chunks."""
        definition_id = str(manifest.get("id") or "")
        _support._validate_unreserved_rule_definition_identity(definition_id)
        _support._validate_reserved_official_artifact_identities(
            definition_id=definition_id,
            artifacts=(item for item in artifacts or [] if isinstance(item, dict)),
        )
        source = self.rules.source(source_id)
        if source["system_id"] != _support.DND5E.id:
            raise ValueError("rule source is not a D&D source")
        if str(manifest.get("system_id") or "") != _support.DND5E.id:
            raise ValueError("source-bound D&D packs require manifest.system_id=dnd5e")
        editions = [str(item) for item in manifest.get("editions", [])]
        if not editions:
            raise ValueError("source-bound D&D packs must declare at least one edition")
        if str(source.get("edition") or "") not in editions:
            raise ValueError("rule source edition must be declared by the pack manifest")
        bound_mechanics: list[dict[str, Any]] = []
        for mechanic in mechanics or []:
            value = _support.deepcopy(mechanic)
            supplied = list(value.get("citations") or [])
            if not supplied:
                raise ValueError("every executable mechanic requires an imported chunk citation")
            citations: list[dict[str, Any]] = []
            for citation in supplied:
                if not isinstance(citation, dict) or not citation.get("chunk_id"):
                    raise ValueError("source-bound citations require chunk_id")
                resolved = self.rules.citation(str(citation["chunk_id"]), source_id=source_id)
                note = citation.get("note")
                if note:
                    resolved["note"] = str(note)
                citations.append(resolved)
            value["citations"] = citations
            bound_mechanics.append(value)
        source_chunks_by_id = {
            str(chunk.get("id") or ""): str(chunk.get("content") or "")
            for chunk in self.rules.source_chunks(source_id)
        }
        bound_artifacts: list[dict[str, Any]] = []
        for artifact in artifacts or []:
            raw_value = _support.deepcopy(artifact)
            reviewed_catalog = raw_value.pop("catalog_review", None)
            reviewed_selection = raw_value.pop("selection_contract", None)
            chunk_ids = list(raw_value.get("source_chunk_ids", []) or [])
            if not chunk_ids:
                raise ValueError("source-bound artifacts require source_chunk_ids")
            citations = []
            for chunk_id in chunk_ids:
                normalized_chunk_id = str(chunk_id)
                citation = self.rules.citation(normalized_chunk_id, source_id=source_id)
                # Provenance must not depend on whether semantic settlement is
                # supplied by an imported clause or an existing kernel mechanic.
                # Persist a verbatim source excerpt on every artifact citation so
                # portable-addon validation can prove the same source contract for
                # both paths.
                source_excerpt = source_chunks_by_id.get(normalized_chunk_id, "")[:4000]
                if source_excerpt:
                    citation["source_excerpt"] = source_excerpt
                citations.append(citation)
            value = _support.artifact_with_direct_resolution(
                {
                    "id": str(raw_value.get("id") or ""),
                    "kind": str(raw_value.get("kind") or "content"),
                    "name": str(dict(raw_value.get("card") or {}).get("name") or ""),
                    "mechanical_scope": raw_value.get("mechanical_scope"),
                    "source_chunk_ids": chunk_ids,
                    "artifact": raw_value,
                },
                citation_source=str(citations[0]["source"]),
                source_chunks_by_id=source_chunks_by_id,
            )
            value.pop("source_chunk_ids", None)
            value["rule_refs"] = [
                f"{citation['source']}#chunk:{citation['chunk_id']}" for citation in citations
            ]
            value["source_citations"] = citations
            if isinstance(reviewed_catalog, dict) and isinstance(reviewed_selection, dict):
                value["selection_contract"] = _support.build_selection_contract(
                    value,
                    status=str(reviewed_selection.get("status") or ""),
                    references=list(reviewed_selection.get("references") or []),
                    blockers=list(reviewed_selection.get("blockers") or []),
                )
                value["catalog_review"] = _support.build_catalog_review(
                    value,
                    decisions=list(reviewed_catalog.get("decisions") or []),
                    status="approved",
                )
            bound_artifacts.append(value)
        source_metadata = dict(source.get("metadata") or {})
        bound_provenance = {
            **dict(provenance or {}),
            "rule_source": {
                "source_id": source["id"],
                "source_key": source["source_key"],
                "title": source["title"],
                "edition": source["edition"],
                "publication_id": source["publication_id"],
                "normalized_checksum": source["checksum"],
                "source_checksum": source_metadata.get("source_checksum", source["checksum"]),
                "page_count": source_metadata.get("page_count"),
                "warnings": list(source_metadata.get("warnings") or []),
            },
        }
        _support.validate_source_bound_mechanics(bound_mechanics, source_id=source_id)
        return self.save_rule_pack_draft(
            manifest=manifest,
            artifacts=bound_artifacts,
            mechanics=bound_mechanics,
            provenance=bound_provenance,
        )

    def module_generator(self, campaign_id: str, brief: str) -> str:
        """Generate an importable adventure module using the bundled module-generation workflow."""
        return (
            f"Create a D&D module for campaign {campaign_id}. Brief: {brief}\n\n"
            "Read sagasmith://skill/modulegen.root first. Write the resulting Markdown with "
            "module_draft(action='start'), review/edit it, finalize its Pack, then activate "
            "through content_pack."
        )

    def export_module_pack(
        self,
        campaign_id: str,
        data: dict[str, Any],
        principal_id: str,
    ) -> dict[str, Any]:
        """Build one finalized module Pack without exposing an authoring side door."""

        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        module_blobs: dict[str, bytes] = {}
        descriptor = self.modules.export_content_descriptor(
            campaign_id,
            str(self.required(data, "module_id")),
            package_id=str(self.required(data, "pack_id")),
            version=str(data.get("version") or "1.0.0"),
            metadata=dict(data.get("metadata") or {}),
            dependencies=list(data.get("dependencies") or []),
            asset_loader=self.managed_module_asset_bytes,
            blob_sink=module_blobs.__setitem__,
            manifest=(dict(data["manifest"]) if data.get("manifest") else None),
            catalogs=(dict(data["catalogs"]) if data.get("catalogs") else None),
            narrative=(dict(data["narrative"]) if data.get("narrative") else None),
        )
        finalized_actors = []
        for actor_card in descriptor["actors"]:
            card = _support.validate_dnd_content_actor(actor_card)
            finalized_actors.append(
                _support.build_dnd_content_actor(
                    actor_id=card["id"],
                    version=card["version"],
                    actor_type=card["actor_type"],
                    name=card["name"],
                    player_name=card["player_name"],
                    summary=card["summary"],
                    sheet=self.finalize_actor_sheet_rulings(card["sheet"], campaign_id),
                    notes=card["notes"],
                    provenance=card.get("provenance") or {},
                    bindings=card.get("bindings") or [],
                    metadata=card.get("metadata") or {},
                )
            )
        descriptor["actors"] = finalized_actors
        package, module_blobs = _support.build_module_content_package(descriptor, module_blobs)
        artifact = self.storage.write_content_archive(package, module_blobs)
        return {
            **artifact,
            "summary": {
                "scenes": len(package["content"]["scene_atlas"]),
                "assets": len(package["assets"]),
                "content_reviews": len(package["content_reviews"]),
                "actors": len(package["actors"]),
                "catalog_entries": sum(
                    len(items) for items in package["content"]["catalogs"].values()
                ),
                "dossiers": len(package["content"]["narrative"]["dossiers"]),
                "endings": len(package["content"]["narrative"]["endings"]),
            },
            **({"package": package} if data.get("include_package") is True else {}),
        }

    def module_query(
        self,
        campaign_id: str,
        view: Literal[
            "list",
            "index",
            "scene",
            "current",
            "progress",
            "preflight",
            "assets",
            "content",
            "candidates",
            "actors",
        ] = "list",
        payload: dict[str, Any] | None = None,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        query: Annotated[str, _support.Field(max_length=200)] = "",
        limit: Annotated[int, _support.Field(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, _support.Field(max_length=1024)] = None,
    ) -> dict[str, Any]:
        """Read installed module state; campaign_id is a top-level argument.

        payload by view: list {}; index {module_id?}; current {scope_id?};
        scene {scene_id, scope_id?}; progress {module_id?, scope_id?};
        preflight {scene_id, participant_manifest}; assets/candidates {module_id};
        actors {module_id, scene_id?, binding_kind?}; content {review_id} or
        {module_id, content_kind?, content_key?}. content reads materialization
        reviews, not source prose. For prose, read scene, then module_expand
        with its returned chunk_id. Copy the complete returned source_ref
        unchanged for source-bound actions; never reconstruct IDs or checksums.
        List results support top-level query, limit and cursor. scope_id defaults
        to party. A missing current scene is not permission to invent module facts.
        current selects only status="current"; progress includes in_progress
        records. Use module_set_progress(status="current") to select the actual
        current location. index has no chapter_id filter; follow its scene IDs.
        """
        data = self.facade_payload(payload)
        if view == "list":
            result = self.module_list(campaign_id, principal_id)
        elif view == "index":
            result = self.module_index(campaign_id, data.get("module_id"), principal_id)
        elif view == "scene":
            result = self.module_read_scene(
                campaign_id,
                self.required(data, "scene_id"),
                data.get("scope_id", "party"),
                principal_id,
            )
        elif view == "current":
            result = self.module_current(campaign_id, data.get("scope_id", "party"), principal_id)
        elif view == "preflight":
            result = self.module_scene_preflight(
                campaign_id,
                self.required(data, "scene_id"),
                self.required(data, "participant_manifest"),
                principal_id,
            )
        elif view == "assets":
            result = self.module_assets(campaign_id, self.required(data, "module_id"), principal_id)
        elif view == "content":
            self.access.require_campaign(
                campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
            )
            if data.get("review_id"):
                result = self.modules.get_content_review(campaign_id, str(data["review_id"]))
            else:
                result = self.modules.list_content_reviews(
                    campaign_id,
                    self.required(data, "module_id"),
                    content_kind=data.get("content_kind"),
                    content_key=data.get("content_key"),
                )
        elif view == "candidates":
            result = self.module_content_candidates(
                campaign_id,
                self.required(data, "module_id"),
                principal_id,
            )
        elif view == "actors":
            data = self.facade_payload(payload)
            self.access.require_campaign(
                campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
            )
            result = self.modules.list_actor_bindings(
                campaign_id,
                str(self.required(data, "module_id")),
                scene_id=(str(data["scene_id"]) if data.get("scene_id") else None),
                binding_kind=(str(data["binding_kind"]) if data.get("binding_kind") else None),
            )
        else:
            result = self.module_progress_index(
                campaign_id,
                data.get("scope_id", "party"),
                data.get("module_id"),
                principal_id,
            )
        if isinstance(result, list):
            result, page = _support._bounded_page(
                result,
                scope=f"module_query:{campaign_id}:{view}:{principal_id}",
                query=query or str(data.get("query") or ""),
                limit=data.get("limit", limit),
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            return self.facade_result(view, result, page=page)
        return self.facade_result(view, result)

    def _content_pack_module_archive(
        self,
        campaign_id: str,
        module_id: str,
    ) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]] | None:
        matches = [
            item
            for item in self.modules.list_assets(campaign_id, module_id)
            if str(dict(item.get("metadata") or {}).get("asset_kind") or "")
            == "content_package_archive"
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("module has multiple authoritative content Pack archives")
        metadata = dict(matches[0].get("metadata") or {})
        artifact = str(metadata.get("content_archive_artifact") or "")
        if not artifact:
            raise ValueError("module content Pack archive metadata is incomplete")
        package, blobs = self.storage.read_content_archive(artifact=artifact)
        if package.get("kind") != "module":
            raise ValueError("installed module archive is not a module Pack")
        return package, blobs, self.storage.write_content_archive(package, blobs)

    def rulebook_draft(
        self,
        campaign_id: Annotated[str, _support.Field(title="Campaign")],
        action: Literal["start", "get", "evidence", "edit", "finalize"],
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
        """Create, inspect, edit, and finalize one source-bound rulebook draft."""

        self.require_facade_phase(campaign_id, f"rulebook_draft({action})", _support.PROFILE_LOBBY)
        if action == "get":
            data = self.facade_payload(payload)
            if data.get("job_id"):
                job = self.import_job_get(campaign_id, str(data["job_id"]), principal_id)
                source = self.rules.source(str(job["source_id"])) if job.get("source_id") else None
                candidates, page = _support._bounded_page(
                    list(job.get("candidates") or []),
                    scope=(
                        f"rulebook_draft:get:{campaign_id}:{principal_id}:"
                        f"{str(data['job_id'])}:candidates"
                    ),
                    query=query or str(data.get("query") or ""),
                    limit=data.get("limit", limit),
                    cursor=cursor or data.get("cursor"),
                    offset=data.get("offset", 0),
                )
                job = {**job, "candidates": candidates}
                result = {
                    "job": job,
                    "candidates": candidates,
                    "inspection": _support.deepcopy(job.get("inspection")),
                    "source_id": job.get("source_id"),
                    "source": source,
                }
            else:
                jobs, page = _support._bounded_page(
                    self.import_job_list(campaign_id, "rulebook", principal_id),
                    scope=f"rulebook_draft:get:{campaign_id}:{principal_id}:jobs",
                    query=query or str(data.get("query") or ""),
                    limit=data.get("limit", limit),
                    cursor=cursor or data.get("cursor"),
                    offset=data.get("offset", 0),
                )
                result = {"jobs": jobs}
            return self.facade_result(action, result, page=page)

        if action == "start":
            data = self.facade_payload(payload)
            if not idempotency_key:
                raise ValueError("idempotency_key is required to start a rulebook draft")
            acknowledge_warnings = data.get("acknowledge_warnings", False)
            if not isinstance(acknowledge_warnings, bool):
                raise ValueError("payload.acknowledge_warnings must be a boolean")
            staged = self.rule_document_stage(campaign_id, str(data["source_path"]), principal_id)
            created = self.rule_import_job_create(
                campaign_id,
                str(staged["artifact"]),
                str(data["source_key"]),
                str(data["title"]),
                str(data["edition"]),
                str(data.get("locale") or "en"),
                str(data.get("publication_id") or ""),
                str(data.get("version") or ""),
                str(data.get("authority") or "supplement"),
                principal_id,
                f"{idempotency_key}:create",
            )
            job_id = str(dict(created["job"])["id"])
            inspected = self.rule_import_job_inspect(
                campaign_id,
                job_id,
                principal_id,
                f"{idempotency_key}:inspect",
            )
            warnings = list(dict(inspected.get("inspection") or {}).get("warnings") or [])
            if warnings and not acknowledge_warnings:
                return self.facade_result(
                    action,
                    {
                        **staged,
                        **inspected,
                        "status": "source_review_required",
                        "warnings": warnings,
                    },
                )
            indexed = self.rule_import_job_ingest(
                campaign_id,
                job_id,
                acknowledge_warnings,
                principal_id,
                f"{idempotency_key}:index",
            )
            extracted = self.rule_content_candidates_extract(
                campaign_id,
                job_id,
                principal_id,
                f"{idempotency_key}:extract",
            )
            return self.facade_result(
                action,
                {
                    **staged,
                    "source": indexed.get("source"),
                    "inventory": extracted.get("inventory"),
                    "inspection": inspected.get("inspection"),
                    "candidates": extracted.get("candidates"),
                    "job": extracted["job"],
                    "status": "editing",
                },
            )

        data = self.facade_payload(payload)
        job_id = str(self.required(data, "job_id"))
        if action == "evidence":
            data = self.facade_payload(payload)
            kind = str(data["kind"])
            if kind == "page":
                return self.facade_render_result(
                    self.rule_document_page_render(
                        campaign_id,
                        job_id,
                        self.required(data, "page_number"),
                        data.get("scale", 1.5),
                        self.facade_bool(data, "include_ocr_text", default=True),
                        principal_id,
                    )
                )
            if kind != "chunks":
                raise ValueError("payload.kind must be page or chunks")
            job = self.require_import_job(campaign_id, job_id, "rulebook")
            if not job.source_id:
                raise ValueError("rulebook draft has not completed mechanical extraction")
            chunks = list(self.rules.source_chunks(job.source_id))
            page_number = data.get("page_number")
            if page_number is not None:
                chunks = [
                    item
                    for item in chunks
                    if isinstance(item.get("page_start"), int)
                    and isinstance(item.get("page_end"), int)
                    and int(item["page_start"]) <= int(page_number) <= int(item["page_end"])
                ]
            chunks, page = _support._bounded_page(
                chunks,
                scope=f"rulebook_draft:evidence:{campaign_id}:{principal_id}:{job_id}",
                query=query or str(data.get("query") or ""),
                limit=data.get("limit", limit),
                cursor=cursor or data.get("cursor"),
                offset=data.get("offset", 0),
            )
            return self.facade_result(action, chunks, page=page)

        if action == "edit":
            operation = str(self.required(data, "operation"))
            operation_payload = {key: value for key, value in data.items() if key != "operation"}
            if operation == "candidates":
                decisions = self.required(operation_payload, "decisions")
                result = self.import_job_review_candidates(
                    campaign_id,
                    job_id,
                    decisions,
                    principal_id,
                    idempotency_key,
                    "edit",
                )
            elif operation == "catalog":
                result = self.rule_content_candidates_augment(
                    campaign_id,
                    job_id,
                    self.required(operation_payload, "additions"),
                    str(self.required(operation_payload, "rationale")),
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            elif operation == "source_text":
                result = self.submit_import_text_review(
                    campaign_id,
                    job_id,
                    int(self.required(operation_payload, "page_number")),
                    str(self.required(operation_payload, "base_text_sha256")),
                    self.required(operation_payload, "replacements"),
                    str(self.required(operation_payload, "rationale")),
                    self.required(operation_payload, "evidence_basis"),
                    operation_payload.get("rendered_image_checksum"),
                    operation_payload.get("review_method", "agent"),
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            elif operation == "statblock_recovery":
                if operation_payload.get("name"):
                    result = self.rule_statblock_ocr_recover(
                        campaign_id,
                        job_id,
                        str(operation_payload["name"]),
                        operation_payload.get("page_number"),
                        principal_id,
                        idempotency_key,
                        operation_payload.get("agent_fill"),
                        operation_payload.get("statblock_slot"),
                        operation_payload.get("ocr_corrections"),
                        operation_payload.get("correction_evidence_basis", "staged_text"),
                        operation_payload.get("rendered_image_checksum"),
                    )
                else:
                    result = self.rule_statblock_catalog_recover(
                        campaign_id,
                        job_id,
                        operation_payload.get("page_numbers"),
                        principal_id,
                        idempotency_key,
                    )
            elif operation == "statblock_review":
                if operation_payload.get("review_mode") == "indexed_text":
                    raise ValueError(
                        "review_mode indexed_text is reserved for mechanical extraction"
                    )
                if operation_payload.get("base_review_id"):
                    result = self.rule_statblock_review_from_base(
                        campaign_id,
                        job_id,
                        str(operation_payload["base_review_id"]),
                        self.required(operation_payload, "observation"),
                        self.required(operation_payload, "agent_fill"),
                        principal_id,
                        idempotency_key,
                    )
                else:
                    result = self.rule_statblock_review(
                        campaign_id,
                        job_id,
                        int(self.required(operation_payload, "page_number")),
                        self.required(operation_payload, "normalized_content"),
                        self.required(operation_payload, "observation"),
                        principal_id,
                        idempotency_key,
                        operation_payload.get("review_mode", "visual"),
                        operation_payload.get("evidence_chunk_ids"),
                        operation_payload.get("agent_fill"),
                        None,
                        operation_payload.get("evidence_exclusions"),
                    )
            elif operation == "advance":
                if not idempotency_key:
                    raise ValueError("idempotency_key is required to advance a draft")
                acknowledge = operation_payload.get("acknowledge_warnings", False)
                if not isinstance(acknowledge, bool):
                    raise ValueError("payload.acknowledge_warnings must be a boolean")
                job = self.require_import_job(campaign_id, job_id, "rulebook")
                if job.state in {"staged", "failed"}:
                    self.rule_import_job_inspect(
                        campaign_id, job_id, principal_id, f"{idempotency_key}:inspect"
                    )
                    job = self.require_import_job(campaign_id, job_id, "rulebook")
                blocked_result: dict[str, Any] | None = None
                if job.state == "inspected":
                    indexed = self.rule_import_job_ingest(
                        campaign_id,
                        job_id,
                        acknowledge,
                        principal_id,
                        f"{idempotency_key}:index",
                    )
                    job = self.require_import_job(campaign_id, job_id, "rulebook")
                    if indexed.get("committed") is False:
                        blocked_result = indexed
                if blocked_result is not None:
                    result = blocked_result
                    result.setdefault("job", self.import_job_view(job))
                elif job.state == "extracted":
                    result = self.rule_content_candidates_extract(
                        campaign_id,
                        job_id,
                        principal_id,
                        f"{idempotency_key}:extract",
                    )
                else:
                    result = {"job": self.import_job_view(job)}
                job_view = dict(result["job"])
                source_id = job_view.get("source_id")
                result = {
                    **result,
                    "source_id": source_id,
                    "source": self.rules.source(str(source_id)) if source_id else None,
                    "candidates": list(job_view.get("candidates") or []),
                }
            else:
                raise ValueError(
                    "payload.operation must be advance, source_text, statblock_recovery, "
                    "statblock_review, catalog, or candidates"
                )
            return self.facade_result(action, result)

        data = self.facade_payload(payload)
        if not idempotency_key:
            raise ValueError("idempotency_key is required to finalize a rulebook draft")
        confirmation = data.get("confirmation")
        if not isinstance(confirmation, dict) or set(confirmation) != {"confirmed", "note"}:
            raise ValueError("rulebook draft confirmation requires exactly confirmed and note")
        if confirmation.get("confirmed") is not True:
            raise ValueError("the Agent must explicitly confirm rulebook finalization")
        confirmation_note = str(confirmation.get("note") or "").strip()
        if not confirmation_note or len(confirmation_note) > 2000:
            raise ValueError("rulebook draft confirmation.note must contain 1 to 2000 characters")
        manifest = self.required(data, "manifest")
        if not isinstance(manifest, dict):
            raise ValueError("payload.manifest must be an object")
        for field in ("id", "version", "system_id"):
            self.required(manifest, field)
        _support._validate_unreserved_rule_definition_identity(str(manifest["id"]))
        editions = manifest.get("editions")
        if not isinstance(editions, list) or not editions:
            raise ValueError("payload.manifest.editions must be a non-empty array")
        for field in ("mechanics",):
            if data.get(field) is not None and not isinstance(data[field], list):
                raise ValueError(f"payload.{field} must be an array")
        for field in ("metadata", "provenance"):
            if data.get(field) is not None and not isinstance(data[field], dict):
                raise ValueError(f"payload.{field} must be an object")
        job = self.require_import_job(campaign_id, job_id, "rulebook")
        _support._validate_reserved_official_artifact_identities(
            definition_id=str(manifest["id"]),
            artifacts=(self.import_candidate_view(item) for item in job.candidates),
        )
        if dict(job.result or {}).get("finalized_package"):
            raise ValueError("a finalized rulebook draft is immutable")
        review_finalization = dict(dict(job.result or {}).get("review_finalization") or {})
        if job.state in {"extracted", "review_required"}:
            finalized = self.import_job_finalize_candidates(
                campaign_id,
                job_id,
                confirmation_note,
                principal_id,
                expected_revision,
                f"{idempotency_key}:freeze",
            )
        elif job.state in {"reviewed", "failed", "compiled"} and review_finalization:
            if expected_revision is None or expected_revision != job.revision:
                raise ValueError(
                    f"import job revision conflict: expected {expected_revision}, "
                    f"found {job.revision}"
                )
            if str(review_finalization.get("confirmed_by") or "") != principal_id:
                raise ValueError("rulebook draft finalization belongs to another principal")
            if str(review_finalization.get("note") or "") != confirmation_note:
                raise ValueError(
                    "rulebook draft finalization confirmation cannot change after review"
                )
            finalized = {
                "job": self.import_job_view(job),
                "candidates": [self.import_candidate_view(item) for item in job.candidates],
            }
        else:
            raise ValueError("only an editable or resumable rulebook draft may be finalized")
        finalized_job = dict(finalized["job"])
        review_finalization = dict(
            dict(finalized_job.get("result") or {}).get("review_finalization") or {}
        )
        authoring_review = {
            "schema_version": 1,
            "draft_kind": "rulebook",
            "source_checksum": str(review_finalization.get("source_checksum") or ""),
            "parser_profile": str(review_finalization.get("parser_profile") or ""),
            "parser_version": str(review_finalization.get("parser_version") or ""),
            "candidate_set_fingerprint": str(
                review_finalization.get("candidate_set_fingerprint") or ""
            ),
            "candidate_decisions": [
                {
                    "id": str(candidate.get("id") or ""),
                    "review_status": str(candidate.get("review_status") or ""),
                    "disposition": str(
                        candidate.get("disposition")
                        or (
                            "include" if candidate.get("review_status") == "accepted" else "exclude"
                        )
                    ),
                    "original_fingerprint": str(candidate.get("original_fingerprint") or ""),
                    "review_note": str(candidate.get("review_note") or ""),
                    "draft_issues": _support.deepcopy(candidate.get("draft_issues") or []),
                    "edit_history": _support.deepcopy(candidate.get("edit_history") or []),
                }
                for candidate in finalized_job.get("candidates") or []
            ],
        }
        resumed_job = self.require_import_job(campaign_id, job_id, "rulebook")
        if resumed_job.state == "compiled":
            draft = _support.deepcopy(dict(resumed_job.validation or {}).get("draft") or {})
            if str(draft.get("status") or "") != "validated":
                raise ValueError("compiled rulebook draft is missing its validated Pack receipt")
            compiled = {"job": self.import_job_view(resumed_job), "draft": draft}
        else:
            compiled = self.rule_import_job_compile(
                campaign_id,
                job_id,
                manifest,
                data.get("mechanics"),
                data.get("provenance"),
                principal_id,
                f"{idempotency_key}:compile",
            )
        if str(dict(compiled.get("draft") or {}).get("status") or "") != "validated":
            raise ValueError("rulebook draft did not produce a valid immutable Pack")
        pack_id = str(self.required(manifest, "id"))
        version = str(self.required(manifest, "version"))
        stored_pack = self.rule_pack_install(pack_id, version)
        stored = {**dict(stored_pack), "status": "stored"}
        archived = self._content_pack_export_rule(
            {
                "campaign_id": campaign_id,
                "pack_id": pack_id,
                "version": version,
                "metadata": {
                    **dict(data.get("metadata") or {}),
                    "agent_finalization": {
                        "confirmed": True,
                        "reviewer": principal_id,
                        "note": confirmation_note,
                    },
                    "authoring_review": authoring_review,
                },
                "include_package": data.get("include_package") is True,
            },
            principal_id,
        )
        compiled_job = self.require_import_job(campaign_id, job_id, "rulebook")
        finalized_package = {
            "artifact": archived["artifact"],
            "summary": archived["summary"],
            **({"package": archived["package"]} if "package" in archived else {}),
            "confirmation": {
                "confirmed": True,
                "reviewer": principal_id,
                "note": confirmation_note,
            },
        }
        package_record_payload = {
            "operation": "finalize_rulebook_package",
            "job_id": job_id,
            "pack_id": pack_id,
            "version": version,
            "artifact": archived["artifact"],
        }
        updated = self.import_jobs.record_result(
            job_id,
            {**dict(compiled_job.result or {}), "finalized_package": finalized_package},
            state="compiled",
            source_id=compiled_job.source_id,
            expected_revision=compiled_job.revision,
            idempotency_key=f"{idempotency_key}:package",
            idempotency_write=_support.IdempotencyWrite(
                scope=f"import-job:{campaign_id}:{job_id}:{principal_id}",
                payload=package_record_payload,
                response=lambda value: {
                    "job": self.import_job_view(value),
                    **finalized_package,
                },
            ),
        )
        return self.facade_result(
            action,
            {
                "job": self.import_job_view(updated),
                "draft": compiled["draft"],
                "stored": stored,
                **finalized_package,
                "finalization": finalized.get("finalization"),
            },
        )

    def module_draft(
        self,
        campaign_id: Annotated[str, _support.Field(title="Campaign")],
        action: Literal["start", "get", "evidence", "edit", "finalize"],
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
        """Build a D&D Module Pack via start, get, evidence, edit, and finalize.

        The public input schema exposes each action's payload shape. Reuse server-issued
        job/module ids, pass the latest import-job revision on guarded edits, and copy only
        real module_draft(evidence) source_ref receipts into play-profile decisions.
        """

        self.require_facade_phase(campaign_id, f"module_draft({action})", _support.PROFILE_LOBBY)
        if action == "get":
            data = self.facade_payload(payload)
            view = str(data.get("view") or "full")
            if view not in {"full", "package"}:
                raise ValueError("module_draft(get) payload.view must be full or package")
            if view == "package" and not data.get("job_id"):
                raise ValueError("module_draft(get, view=package) requires payload.job_id")
            if data.get("job_id"):
                jobs = [self.import_job_get(campaign_id, str(data["job_id"]), principal_id)]
            else:
                self.access.require_campaign(
                    campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
                )
                jobs, page = _support._bounded_page(
                    [
                        self.module_draft_handle_view(item)
                        for item in self.import_jobs.list(campaign_id, kind="module")
                    ],
                    scope=f"module_draft:get:{campaign_id}:{principal_id}:jobs",
                    query=query or str(data.get("query") or ""),
                    limit=data.get("limit", limit),
                    cursor=cursor or data.get("cursor"),
                    offset=data.get("offset", 0),
                )
                return self.facade_result(
                    action,
                    {
                        "order": "newest_first",
                        "jobs": jobs,
                    },
                    page=page,
                )
            if view == "package":
                job = self.require_import_job(campaign_id, str(data["job_id"]), "module")
                job_result = dict(job.result or {})
                finalized_package = _support.deepcopy(
                    dict(job_result.get("finalized_package") or {})
                )
                finalized_package.pop("package", None)
                return self.facade_result(
                    action,
                    {
                        "job": self.module_draft_handle_view(job),
                        "pack_draft": _support.deepcopy(dict(job_result.get("pack_draft") or {})),
                        "finalized_package": finalized_package,
                    },
                )
            for job_view in jobs:
                job_result = dict(job_view.get("result") or {})
                finalized = dict(job_result.get("finalized_package") or {})
                if finalized:
                    finalized.pop("package", None)
                    job_view["result"] = {
                        **job_result,
                        "finalized_package": finalized,
                    }
            result = {"job": jobs[0]} if data.get("job_id") else {"jobs": jobs}
            return self.facade_result(action, result)

        if action == "start":
            data = self.facade_payload(payload)
            if not idempotency_key:
                raise ValueError("idempotency_key is required to start a module draft")
            source_path = data.get("source_path")
            generated_fields = {"name", "content"}.intersection(data)
            if source_path is not None and generated_fields:
                raise ValueError("start accepts either source_path or name+content, not both")
            if source_path is not None:
                stored = self.storage.stage_module(str(source_path))
                artifact = str(stored["artifact"])
            else:
                stored = self.module_write(
                    str(self.required(data, "name")),
                    str(self.required(data, "content")),
                    principal_id,
                )
                artifact = str(stored["artifact"])
            staged = self.module_import_job_create(
                campaign_id,
                artifact,
                data.get("title"),
                data.get("source_key"),
                principal_id,
                f"{idempotency_key}:create",
            )
            job_id = str(dict(staged["job"])["id"])
            inspected = self.module_import_job_inspect(
                campaign_id, job_id, principal_id, f"{idempotency_key}:inspect"
            )
            validated = self.module_import_job_validate(
                campaign_id, job_id, principal_id, f"{idempotency_key}:validate"
            )
            if not dict(validated.get("validation") or {}).get("valid"):
                return self.facade_result(
                    action,
                    {
                        "job_id": str(dict(validated["job"])["id"]),
                        "job": validated["job"],
                        "inspection": inspected.get("preview"),
                        "validation": validated.get("validation"),
                        "status": "editing",
                    },
                )
            imported = self.module_import_job_import(
                campaign_id, job_id, principal_id, f"{idempotency_key}:import"
            )
            return self.facade_result(
                action,
                {
                    "job_id": str(dict(imported["job"])["id"]),
                    "job": imported["job"],
                    "inspection": inspected.get("preview"),
                    "validation": validated.get("validation"),
                    "module_id": imported.get("module_id"),
                    "status": "editing",
                },
            )

        data = self.facade_payload(payload)
        job_id = str(data.get("job_id") or "")
        if not job_id and data.get("module_id"):
            matching_jobs = [
                item
                for item in self.import_jobs.list(campaign_id, kind="module")
                if item.module_id == str(data["module_id"])
            ]
            if len(matching_jobs) != 1:
                raise ValueError("payload.module_id must resolve to exactly one editable draft")
            job_id = matching_jobs[0].id
        if not job_id:
            raise ValueError("payload.job_id or payload.module_id is required")
        if action == "evidence":
            data = self.facade_payload(payload)
            self.access.require_campaign(
                campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
            )
            job = self.require_import_job(campaign_id, job_id, "module")
            source_key = str(dict(job.payload or {}).get("source_key") or job.artifact)

            def chunk_evidence_receipt(
                item: dict[str, Any],
                *,
                page: int | None = None,
            ) -> dict[str, Any]:
                evidence_page = page if page is not None else item.get("page_start")
                heading_path = [str(value) for value in item.get("heading_path") or []]
                note_subject = " / ".join(heading_path) or str(
                    item.get("scene_title") or "module evidence"
                )
                return {
                    **_support.deepcopy(item),
                    "source_ref": {
                        "source_key": source_key,
                        "page": evidence_page,
                        "chunk_hash": str(item.get("content_hash") or ""),
                        "note": f"Agent-reviewed source evidence: {note_subject}",
                    },
                }

            evidence_kind = str(
                data.get("kind") or ("page" if data.get("page_number") else "chunks")
            )
            if evidence_kind == "chunks":
                if not job.module_id:
                    raise ValueError("module chunk evidence requires a mechanically imported draft")
                chunks = self.modules.list_chunks(
                    campaign_id,
                    job.module_id,
                    scene_id=(str(data["scene_id"]) if data.get("scene_id") else None),
                )
                chunks, page = _support._bounded_page(
                    chunks,
                    scope=f"module_draft:evidence:{campaign_id}:{principal_id}:{job_id}",
                    query=query or str(data.get("query") or ""),
                    limit=data.get("limit", limit),
                    cursor=cursor or data.get("cursor"),
                    offset=data.get("offset", 0),
                )
                return self.facade_result(
                    action,
                    [chunk_evidence_receipt(item) for item in chunks],
                    page=page,
                )
            if evidence_kind != "page":
                raise ValueError("payload.kind must be page or chunks")
            if not data.get("page_number"):
                raise ValueError("payload.page_number is required for page evidence")
            source = self.storage.artifact_module_path(job.artifact)
            if source.suffix.casefold() != ".pdf":
                raise ValueError("module evidence rendering requires a staged PDF")
            rendered = _support.render_pdf_page(
                source,
                data["page_number"],
                scale=data.get("scale", 1.5),
            )
            transcription = self.staged_transcription_evidence(
                job,
                data["page_number"],
                include_ocr=self.facade_bool(data, "include_ocr_text", default=True),
            )
            page_chunks = []
            if job.module_id:
                for item in self.modules.list_chunks(campaign_id, job.module_id):
                    page_start = item.get("page_start")
                    page_end = item.get("page_end")
                    if (
                        isinstance(page_start, int)
                        and isinstance(page_end, int)
                        and page_start <= rendered.page_number <= page_end
                    ):
                        page_chunks.append(chunk_evidence_receipt(item, page=rendered.page_number))
            return self.facade_render_result(
                [
                    {
                        "campaign_id": campaign_id,
                        "job_id": job.id,
                        "artifact": job.artifact,
                        "source_checksum": rendered.source_checksum,
                        "page_number": rendered.page_number,
                        "page_count": rendered.page_count,
                        "width": rendered.width,
                        "height": rendered.height,
                        "scale": rendered.scale,
                        "image_checksum": rendered.checksum,
                        "transcription": transcription,
                        "citation_candidates": page_chunks,
                    },
                    _support.Image(data=rendered.content, format="png"),
                ]
            )

        if action == "edit":
            job = self.require_import_job(campaign_id, job_id, "module")
            if dict(job.result or {}).get("finalized_package"):
                raise ValueError("a finalized module draft is immutable")
            operation = str(self.required(data, "operation"))
            value = {key: item for key, item in data.items() if key != "operation"}
            if operation == "source_text":
                if job.state == "failed":
                    if not idempotency_key:
                        raise ValueError("idempotency_key is required for source editing")
                    self.module_import_job_inspect(
                        campaign_id, job_id, principal_id, f"{idempotency_key}:inspect"
                    )
                result = self.submit_import_text_review(
                    campaign_id,
                    job_id,
                    int(self.required(value, "page_number")),
                    str(self.required(value, "base_text_sha256")),
                    self.required(value, "replacements"),
                    str(self.required(value, "rationale")),
                    self.required(value, "evidence_basis"),
                    value.get("rendered_image_checksum"),
                    value.get("review_method", "agent"),
                    principal_id,
                    expected_revision,
                    idempotency_key,
                )
            elif operation == "content":
                result = self.module_content_review(
                    campaign_id,
                    str(job.module_id or self.required(value, "module_id")),
                    str(self.required(value, "scene_id")),
                    str(self.required(value, "content_key")),
                    self.required(value, "normalized_content"),
                    self.required(value, "observation"),
                    value.get("source_asset_id"),
                    value.get("page_number"),
                    value.get("source_chunk_ids"),
                    value.get(
                        "content_kind",
                        self.statblock_content_kind(self.campaign_rules_edition(campaign_id)),
                    ),
                    value.get("metadata"),
                    principal_id,
                    idempotency_key,
                    value.get("agent_fill"),
                )
            elif operation == "statblock":
                result = self.module_statblock_ocr_recover(
                    campaign_id,
                    str(job.module_id or self.required(value, "module_id")),
                    str(self.required(value, "scene_id")),
                    str(self.required(value, "content_key")),
                    str(self.required(value, "name")),
                    int(self.required(value, "page_number")),
                    value.get("source_asset_id"),
                    principal_id,
                    idempotency_key,
                    value.get("agent_fill"),
                )
            elif operation == "asset":
                result = self.module_asset_attach(
                    campaign_id,
                    str(job.module_id or self.required(value, "module_id")),
                    str(self.required(value, "source_path")),
                    asset_kind=str(self.required(value, "asset_kind")),
                    scene_id=value.get("scene_id"),
                    location_key=value.get("location_key"),
                    title=value.get("title"),
                    metadata=value.get("metadata"),
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                )
            elif operation == "actor":
                result = self.modules.bind_actor(
                    campaign_id=campaign_id,
                    module_id=str(job.module_id or self.required(value, "module_id")),
                    character_id=str(self.required(value, "character_id")),
                    actor_card_id=str(self.required(value, "actor_card_id")),
                    binding_kind=str(self.required(value, "binding_kind")),
                    role=str(value.get("role") or ""),
                    scene_id=(str(value["scene_id"]) if value.get("scene_id") else None),
                    metadata=dict(value.get("metadata") or {}),
                )
            elif operation == "combat_grid":
                self.access.require_campaign(
                    campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES
                )
                self.require_write_contract(expected_revision, idempotency_key)
                if job.state != "imported" or not job.module_id:
                    raise ValueError(
                        "combat-grid edits require a mechanically imported module draft"
                    )
                change = str(self.required(value, "change"))
                if change not in {"upsert", "remove"}:
                    raise ValueError("combat_grid change must be upsert or remove")
                scene_id_value = str(self.required(value, "scene_id"))
                scene = self.modules.read_scene(campaign_id, scene_id_value)
                if str(scene["module_id"]) != str(job.module_id):
                    raise ValueError("combat-grid scene_id must belong to the draft module")
                source_key = str(dict(job.payload or {}).get("source_key") or job.artifact)
                chunk_hashes = {
                    str(item.get("content_hash") or "")
                    for item in self.modules.list_chunks(campaign_id, str(job.module_id))
                    if item.get("content_hash")
                }

                def validate_draft_refs(refs: list[dict[str, Any]]) -> None:
                    if any(str(ref["source_key"]) != source_key for ref in refs):
                        raise ValueError(
                            "combat-grid source_refs must use the current module draft source_key"
                        )
                    missing_hashes = sorted(
                        str(ref["chunk_hash"])
                        for ref in refs
                        if str(ref["chunk_hash"]) not in chunk_hashes
                    )
                    if missing_hashes:
                        raise ValueError(
                            "combat-grid source_refs do not resolve inside draft evidence: "
                            + ", ".join(missing_hashes)
                        )

                if change == "upsert":
                    template = _support.normalize_combat_grid_template(
                        self.required(value, "template"),
                        source_ref_key="chunk_hash",
                    )
                    validate_draft_refs(template["source_refs"])
                    edit_refs = template["source_refs"]
                    template_id = template["id"]
                else:
                    template = None
                    template_id = str(self.required(value, "template_id"))
                    edit_refs = _support.normalize_combat_grid_source_refs(
                        self.required(value, "source_refs"),
                        source_ref_key="chunk_hash",
                    )
                    validate_draft_refs(edit_refs)
                request_payload = {
                    "job_id": job_id,
                    "operation": operation,
                    "change": change,
                    "scene_id": scene_id_value,
                    "template_id": template_id,
                    **({"template": template} if template is not None else {}),
                    "source_refs": edit_refs,
                    "note": str(value.get("note") or "").strip(),
                }
                scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
                replay = self.replay_idempotent(scope, idempotency_key, request_payload)
                if replay is not None:
                    return self.facade_result(action, replay)
                if job.revision != expected_revision:
                    raise ValueError(
                        "import job revision conflict: "
                        f"expected {expected_revision}, found {job.revision}"
                    )
                existing = _support.normalize_combat_grid_templates(
                    list(dict(scene.get("profile_data") or {}).get("combat_grid_templates") or []),
                    source_ref_key="chunk_hash",
                )
                if template is not None:
                    location_keys = {
                        str(item.get("key"))
                        for item in dict(scene.get("spatial") or {}).get("locations", [])
                        if isinstance(item, dict) and item.get("key")
                    }
                    if template["location_key"] not in location_keys:
                        raise ValueError(
                            "combat-grid template location_key must belong to its draft scene"
                        )
                    for other_scene in self.modules.scene_index(
                        campaign_id, module_id=str(job.module_id)
                    ):
                        if str(other_scene["scene_id"]) == scene_id_value:
                            continue
                        other_templates = _support.normalize_combat_grid_templates(
                            list(
                                dict(other_scene.get("profile_data") or {}).get(
                                    "combat_grid_templates"
                                )
                                or []
                            ),
                            source_ref_key="chunk_hash",
                        )
                        if template["id"] in {item["id"] for item in other_templates}:
                            raise ValueError(
                                "combat-grid template ids must be unique across the draft module"
                            )
                    asset_key = template.get("map_asset_key")
                    if asset_key:
                        matches = [
                            item
                            for item in self.modules.list_assets(campaign_id, str(job.module_id))
                            if str(dict(item.get("metadata") or {}).get("content_asset_key") or "")
                            == str(asset_key)
                        ]
                        if len(matches) != 1 or not str(
                            matches[0].get("media_type") or ""
                        ).startswith("image/"):
                            raise ValueError(
                                "map_asset_key must identify one draft image asset by "
                                "metadata.content_asset_key"
                            )
                    public_asset = template.get("party_public_map_asset")
                    if public_asset:
                        if str(dict(public_asset["review"])["reviewer"]) != principal_id:
                            raise PermissionError(
                                "party_public_map_asset review.reviewer must be the "
                                "authenticated DM principal"
                            )
                        public_matches = [
                            item
                            for item in self.modules.list_assets(campaign_id, str(job.module_id))
                            if str(dict(item.get("metadata") or {}).get("content_asset_key") or "")
                            == str(public_asset["asset_key"])
                        ]
                        if len(public_matches) != 1:
                            raise ValueError(
                                "party_public_map_asset.asset_key must identify exactly one "
                                "draft asset"
                            )
                        public_match = public_matches[0]
                        if (
                            str(public_match.get("checksum") or "") != str(public_asset["checksum"])
                            or str(public_match.get("media_type") or "").casefold()
                            != str(public_asset["media_type"]).casefold()
                        ):
                            raise ValueError(
                                "party_public_map_asset checksum and media_type must match the "
                                "reviewed draft asset"
                            )
                        actual_width, actual_height, actual_media_type = _support._image_properties(
                            str(public_match.get("source_path") or "")
                        )
                        if (actual_width, actual_height) != (
                            int(public_asset["width"]),
                            int(public_asset["height"]),
                        ):
                            raise ValueError(
                                "party_public_map_asset dimensions must match the reviewed "
                                "draft asset"
                            )
                        if actual_media_type != str(public_asset["media_type"]).casefold():
                            raise ValueError(
                                "party_public_map_asset media_type must match the reviewed "
                                "image bytes"
                            )
                    by_id = {item["id"]: item for item in existing}
                    by_id[template["id"]] = template
                    templates = _support.normalize_combat_grid_templates(
                        list(by_id.values()), source_ref_key="chunk_hash"
                    )
                else:
                    if template_id not in {item["id"] for item in existing}:
                        raise LookupError(template_id)
                    templates = [item for item in existing if item["id"] != template_id]
                prior_result = dict(job.result or {})
                edit_record = {
                    "revision": job.revision + 1,
                    "editor": principal_id,
                    "operation": "combat_grid",
                    "change": change,
                    "scene_id": scene_id_value,
                    "template_id": template_id,
                    "source_refs": _support.deepcopy(edit_refs),
                    "note": request_payload["note"],
                }
                with self.storage.database.transaction():
                    self.modules.set_scene_profile_field(
                        campaign_id=campaign_id,
                        module_id=str(job.module_id),
                        scene_id=scene_id_value,
                        field="combat_grid_templates",
                        value=templates,
                    )
                    updated = self.import_jobs.record_result(
                        job_id,
                        {
                            **prior_result,
                            "combat_grid_edit_history": [
                                *list(prior_result.get("combat_grid_edit_history") or []),
                                edit_record,
                            ],
                        },
                        state="imported",
                        module_id=job.module_id,
                        expected_revision=expected_revision,
                        idempotency_key=idempotency_key,
                        idempotency_write=_support.IdempotencyWrite(
                            scope=scope,
                            payload=request_payload,
                            response=lambda saved: {
                                "job": self.import_job_view(saved),
                                "scene_id": scene_id_value,
                                "combat_grid_templates": templates,
                            },
                        ),
                    )
                result = {
                    "job": self.import_job_view(updated),
                    "scene_id": scene_id_value,
                    "combat_grid_templates": templates,
                }
            elif operation == "package":
                if job.state != "imported" or not job.module_id:
                    raise ValueError("module Pack decisions require a mechanically imported draft")
                allowed_decisions = {
                    "catalogs",
                    "dependencies",
                    "manifest",
                    "metadata",
                    "narrative",
                    "version",
                }
                unsupported = sorted(set(value) - allowed_decisions - {"job_id", "note"})
                if unsupported:
                    raise ValueError(
                        "module Pack edit has unsupported fields: " + ", ".join(unsupported)
                    )
                decisions = {
                    key: _support.deepcopy(item)
                    for key, item in value.items()
                    if key in allowed_decisions
                }
                if not decisions:
                    raise ValueError("module Pack edit requires at least one decision field")
                _support.validate_module_pack_decisions(decisions)
                prior_result = dict(job.result or {})
                pack_draft = {
                    **dict(prior_result.get("pack_draft") or {}),
                    **decisions,
                }
                edit_record = {
                    "revision": job.revision + 1,
                    "editor": principal_id,
                    "note": str(value.get("note") or "").strip(),
                    "fields": sorted(decisions),
                }
                request_payload = {
                    "job_id": job_id,
                    "operation": operation,
                    "decisions": decisions,
                    "note": edit_record["note"],
                }
                scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
                idempotency_write = (
                    _support.IdempotencyWrite(
                        scope=scope,
                        payload=request_payload,
                        response=lambda saved: {"job": self.import_job_view(saved)},
                    )
                    if idempotency_key
                    else None
                )
                updated = self.import_jobs.record_result(
                    job_id,
                    {
                        **prior_result,
                        "pack_draft": pack_draft,
                        "pack_edit_history": [
                            *list(prior_result.get("pack_edit_history") or []),
                            edit_record,
                        ],
                    },
                    state="imported",
                    module_id=job.module_id,
                    expected_revision=expected_revision,
                    idempotency_key=idempotency_key,
                    idempotency_write=idempotency_write,
                )
                result = {"job": self.import_job_view(updated), "pack_draft": pack_draft}
            elif operation == "advance":
                if not idempotency_key:
                    raise ValueError("idempotency_key is required to advance a module draft")
                if job.state in {"staged", "failed"}:
                    self.module_import_job_inspect(
                        campaign_id, job_id, principal_id, f"{idempotency_key}:inspect"
                    )
                    job = self.require_import_job(campaign_id, job_id, "module")
                if job.state == "inspected":
                    validated = self.module_import_job_validate(
                        campaign_id, job_id, principal_id, f"{idempotency_key}:validate"
                    )
                    if not dict(validated.get("validation") or {}).get("valid"):
                        return self.facade_result(action, validated)
                    job = self.require_import_job(campaign_id, job_id, "module")
                if job.state == "validated":
                    result = self.module_import_job_import(
                        campaign_id, job_id, principal_id, f"{idempotency_key}:import"
                    )
                else:
                    result = {
                        "job": self.import_job_view(job),
                        "inspection": _support.deepcopy(job.inspection),
                        "validation": _support.deepcopy(job.validation),
                        "module_id": job.module_id,
                    }
            else:
                raise ValueError(
                    "payload.operation must be advance, source_text, content, "
                    "statblock, asset, actor, combat_grid, or package"
                )
            return self.facade_result(action, result)

        data = self.facade_payload(payload)
        if not idempotency_key:
            raise ValueError("idempotency_key is required to finalize a module draft")
        job = self.require_import_job(campaign_id, job_id, "module")
        request_payload = {
            "job_id": job_id,
            "operation": "finalize_module_draft",
            **{
                key: _support.deepcopy(value)
                for key, value in data.items()
                if key != "include_package"
            },
        }
        scope = f"import-job:{campaign_id}:{job_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, request_payload)
        if replay is not None:
            return self.facade_result(action, replay)
        if job.state != "imported" or not job.module_id:
            raise ValueError("module draft must complete mechanical import before finalization")
        saved_pack_draft = dict(dict(job.result or {}).get("pack_draft") or {})
        final_data = {**saved_pack_draft, **data}
        _support.validate_module_pack_decisions(
            {
                key: _support.deepcopy(final_data[key])
                for key in (
                    "catalogs",
                    "dependencies",
                    "manifest",
                    "metadata",
                    "narrative",
                    "version",
                )
                if key in final_data
            }
        )
        confirmation = final_data.get("confirmation")
        if not isinstance(confirmation, dict) or set(confirmation) != {"confirmed", "note"}:
            raise ValueError("module draft confirmation requires exactly confirmed and note")
        if confirmation.get("confirmed") is not True:
            raise ValueError("the Agent must explicitly confirm module finalization")
        confirmation_note = str(confirmation.get("note") or "").strip()
        if not confirmation_note or len(confirmation_note) > 2000:
            raise ValueError("module draft confirmation.note must contain 1 to 2000 characters")
        agent_confirmation = {
            "confirmed": True,
            "reviewer": principal_id,
            "note": confirmation_note,
        }
        final_data["metadata"] = {
            **dict(final_data.get("metadata") or {}),
            "agent_finalization": agent_confirmation,
            "authoring_review": {
                "schema_version": 1,
                "draft_kind": "module",
                "draft_revision": int(job.revision),
                "package_edit_history": _support.deepcopy(
                    dict(job.result or {}).get("pack_edit_history") or []
                ),
            },
        }
        include_package = final_data.get("include_package") is True
        packaged = self.export_module_pack(
            campaign_id,
            {
                key: value
                for key, value in final_data.items()
                if key not in {"confirmation", "job_id"}
            }
            | {"module_id": job.module_id, "include_package": include_package},
            principal_id,
        )
        finalized_package = {
            "artifact": packaged["artifact"],
            "summary": packaged["summary"],
            "confirmation": agent_confirmation,
            **({"package": packaged["package"]} if include_package else {}),
        }
        updated = self.import_jobs.record_result(
            job_id,
            {**dict(job.result), "finalized_package": finalized_package},
            state="compiled",
            module_id=job.module_id,
            expected_revision=job.revision,
            idempotency_key=idempotency_key,
            idempotency_write=_support.IdempotencyWrite(
                scope=scope,
                payload=request_payload,
                response=lambda result: {
                    "job": self.import_job_view(result),
                    **finalized_package,
                },
            ),
        )
        return self.facade_result(
            action,
            {"job": self.import_job_view(updated), **finalized_package},
        )
