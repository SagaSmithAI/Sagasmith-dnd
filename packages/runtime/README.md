# D&D Runtime

Protocol-independent application services between the deterministic domain and
transport adapters. Runtime may depend on `sagasmith-core` and `sagasmith-dnd`;
it must not import MCP, HTTP, gateway, or Host code.

## Application boundary

`RandomStateMutationService` owns the existing atomic state/random/replay-receipt
commit and cross-actor inventory custody validation. MCP's `random_state` module
re-exports the same service for compatibility. This preserves one implementation
and one transaction boundary.

`application.py` composes services and registers public operations. Handlers live
in `services/`: campaigns, characters, combat, attacks, spells, inventory,
continuity, content, authoring, presentation and shared transaction support.
They share explicit per-runtime service state, not a factory closure or executable
source loader. `application_support.py` holds common imports and pure helpers.
`operations.py` owns the typed registry and trusted command scope.
Authenticated MCP calls and direct application calls share `command_scope`.
MCP adapts neutral results, resources, prompts and protocol-specific tasks.

`contracts.py` publishes and enforces action-specific combat payloads before
handler execution. `result_contracts.py` describes shared metadata and the
required check, roll, attack, spell and combat facade result envelopes, including
pending/error variants. Domain-specific declarations stay with the existing
engine validators. Generate operation references from this registry; do not
maintain a second tool catalog in a Skill.

The Host constructs `RequestIdentity` after authentication; model-authored JSON
cannot override it. The legacy unauthenticated in-process MCP API is retained for
compatibility and must not be exposed as a hosted authorization boundary.

```python
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.operations import RequestIdentity

runtime = create_runtime(config)
try:
    result = await runtime.execute("campaign_create", {
        "name": "The expedition", "edition": "2014", "idempotency_key": "create-1"
    }, context=RequestIdentity("system:local"))
finally:
    runtime.close()
```

Rule profiles lock an implementation build digest separately from content
fingerprints. Mismatched profiles require a checkpointed `core_relock`, including
implementation-only upgrades. Historical inspection and stored exact responses
remain available. A new implementation never silently recomputes an old snapshot.
Retain matching release artifacts for executable history.

Workflow publication captures visible files, including binary dependencies, then
atomically publishes a reproducible content-addressed ZIP. Generate/check operation
references with `python -m sagasmith_dnd_runtime.publish --check`; publish captured
bytes with `python -m sagasmith_dnd_runtime.publish --bundle dist`.

## Validation

Run `uv run --no-sync pytest packages/runtime/tests` for a fresh-process check
that the receipt guard executes without importing MCP. Existing MCP random-stream
tests verify the same service's end-to-end persistence and replay behavior.

## 中文

Runtime 持有鉴权后的应用命令、权限、版本检查、结算、持久化等待状态、随机进度
和精确重放，只依赖 Core 与 Domain。Host 构造可信的 `RequestIdentity`，模型参数
不能覆盖它。真实 MCP 调用与直接应用调用共享 `command_scope`。
旧的无鉴权进程内 MCP API 仅为兼容保留，不能作为托管服务的权限边界。

`application.py` 只组装服务和注册操作，处理器按职责位于 `services/`，共享状态
属于各自 Runtime 实例；不使用巨型闭包或动态源码执行。通用帮助函数位于
`application_support.py`。`contracts.py` 共用动作参数的 schema 与执行前校验，
`result_contracts.py` 定义高频结果、等待状态和错误外层结构。领域声明仍由既有
规则引擎验证，Skill 的操作参考从注册表生成。类型化注册表与命令边界位于
`operations.py`；MCP 适配中立结果和协议任务。规则配置单独锁定实现摘要，
升级通过检查点和显式 `core_relock` 完成；历史快照不会用新实现静默重算。
工作流包由不可变快照生成，包含二进制依赖，以内容寻址 ZIP 原子发布。
上面的示例演示直接调用；Validation 命令验证无 MCP 导入和随机提交重放。
