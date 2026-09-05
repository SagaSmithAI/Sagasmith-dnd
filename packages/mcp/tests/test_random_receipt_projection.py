"""Protocol receipt attachment must not change an already persisted response."""

import json
from copy import deepcopy

import pytest
from mcp.types import CallToolResult, TextContent
from sagasmith_core.idempotency import _public_response, _stored_response

from sagasmith_dnd_mcp.server import RequestScopedMCPServer


@pytest.mark.parametrize("legacy_tuple", [False, True])
@pytest.mark.parametrize("placement", ["top", "nested", "both", "missing", "flat"])
def test_attach_preserves_persisted_receipt_location(legacy_tuple, placement):
    receipt = {"draw_count": 1, "position_before": 0, "position_after": 1}
    payload = {"result": {"hit": True}} if placement != "flat" else {"hit": True}
    if placement in {"top", "both"}:
        payload["random_stream_receipt"] = deepcopy(receipt)
    if placement in {"nested", "both"}:
        payload["result"]["random_stream_receipt"] = deepcopy(receipt)
    original = deepcopy(payload)
    content = [TextContent(type="text", text=json.dumps(payload))]
    result = (content, payload) if legacy_tuple else CallToolResult(
        content=content, structured_content=payload,
    )

    attached = RequestScopedMCPServer._attach_random_receipt(result, receipt)
    actual_content, actual = attached if legacy_tuple else (
        attached.content, attached.structured_content,
    )
    expected = deepcopy(original)
    if placement == "missing":
        expected["result"]["random_stream_receipt"] = receipt
    elif placement == "flat":
        expected["random_stream_receipt"] = receipt
    assert actual == expected
    assert json.loads(actual_content[0].text) == expected
    if placement in {"top", "nested", "both"}:
        assert actual_content == content
    assert payload == original
    # No new draw on an idempotent replay: do not synthesize a new receipt.
    assert RequestScopedMCPServer._attach_random_receipt(attached, None) == attached


@pytest.mark.parametrize("legacy_tuple", [False, True])
def test_compressed_replay_json_mirror_is_byte_stable(legacy_tuple):
    payload = {"z": "compressible fixture " * 5000, "a": {"z": 1, "a": 2}}
    stored = _stored_response(payload)
    assert stored["_sagasmith_encoding"] == "sagasmith.idempotency.response+zlib.v1"
    replay = _public_response(stored)
    assert replay == payload and list(replay) != list(payload)
    narrative = TextContent(type="text", text="Leave this narrative unchanged.")
    unrelated = TextContent(type="text", text='{"separate": "document"}')
    outputs = []
    for value in (payload, replay):
        content = [TextContent(type="text", text=json.dumps(value, indent=2)),
                   narrative, unrelated]
        result = (content, value) if legacy_tuple else CallToolResult(
            content=content, structured_content=value, _meta={"fixture": "preserved"},
        )
        original = deepcopy(result)
        normalized = RequestScopedMCPServer._canonicalize_structured_text(result)
        actual_content, actual = normalized if legacy_tuple else (
            normalized.content, normalized.structured_content,
        )
        assert result == original
        assert actual == payload
        assert json.loads(actual_content[0].text) == actual
        assert actual_content[1:] == [narrative, unrelated]
        if not legacy_tuple:
            assert normalized.meta == result.meta
        outputs.append(normalized)
    assert outputs[0] == outputs[1]
