import subprocess
import sys


def test_atomic_receipt_guard_runs_without_loading_mcp() -> None:
    program = '''
import sys
from sagasmith_dnd_runtime.random_state import (
    RandomStateMutationService, bind_idempotency_request,
)
assert not any(name == "mcp" or name.startswith("mcp.") for name in sys.modules)
assert not any(name.startswith("sagasmith_dnd_mcp") for name in sys.modules)
bind_idempotency_request("campaign", "branch", "request", {"operation": "test"})
service = RandomStateMutationService.__new__(RandomStateMutationService)
service.database = object()
try:
    service.replace("campaign", branch_id="branch", idempotency_key="request")
except RuntimeError as error:
    assert "exact replay response" in str(error)
else:
    raise AssertionError("missing receipt must fail before any write")
'''
    subprocess.run([sys.executable, "-c", program], check=True, timeout=30)
