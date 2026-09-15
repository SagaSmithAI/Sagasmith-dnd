# Design audit: 0.3.0 acceptance

Source: the user-selected **审计并重构设计** conversation
(`6aa8a2ea-bae4-83ec-ad27-a9d912ff92b9`), including the D&D and Core audits.

## Delivered changes

| Boundary | Implementation | Regression evidence |
| --- | --- | --- |
| Gateway and UI | Full structured rejection/recovery envelope; bounded admission and deadlines; cancelled requests skipped; no automatic replay after dispatch; independent browser startup; modern discovery and explicit legacy initialization | Gateway fault injection, Streamable HTTP, UI API tests |
| Application authority | Independent Runtime owns authorization, optimistic revisions, idempotency, random/state receipts, settlement and persisted continuations. MCP authenticates protocol requests and adapts the common operation registry. | Runtime lifecycle without MCP; auth, random-stream, stable-recovery and continuation suites |
| Domain execution | Registered native equipment, activity, dependent and stance handlers; immutable resolution instructions shared by mechanics and semantic plans; explicit conflicts and dependency order; instruction receipts | Domain suite, IR shadow settlement, combat/semantic continuation tests |
| Implementation identity | Canonical source digest independent of rule-pack fingerprint; profiles, snapshots and random receipts retain implementation identity; incompatible execution requires checkpointed upgrade | Build digest, implementation-only migration, checkpoint/relock and branch conversion tests |
| Workflows | Small bootstrap, task references and Host adapters; schemas/policies generated from actual operations; complete visible dependency bytes captured immutably; atomic, content-addressed reproducible ZIP publication | Generated contract check, immutable workflow tests, portable Skill tests |
| Shared Core | Explicit transaction ownership, rollback-only nesting and savepoints; strict mutation commit; branch-owned receipts; monotonic restore revisions and current grants; frozen migrations; bounded scoped retrieval; independent leased vector outbox; parser contracts; persistent nonce replay defense | Core execution, migration, snapshot, retrieval, outbox and nonce tests |

Native content calls the shared combat primitives. Both authoring forms use the
same instruction boundary while retaining their respective sheet and encounter
state adapters. Historical MCP Python imports remain compatibility aliases; new
application integrations use `sagasmith_dnd_runtime`.

## Verification

Python 3.12.13 on Windows:

- Core: 405 passed, 3 skipped.
- Domain: final full-suite result recorded in the release notes.
- Runtime: 6 passed, including implementation-only migration and replay.
- Gateway fault injection: 8 passed. Auth and real Streamable HTTP tests passed.
- UI: 10 passed; type checking and production build passed.
- Python lint, generated operation contract, Skill validation and portable
  ingestion checks passed.
- Core, Domain, Runtime and MCP wheel/source distributions built. An isolated
  environment installed the wheels and created a campaign through Runtime
  without importing MCP.

The MCP full-suite and hosted CI results are recorded in the release notes after
completion. Focused test counts overlap the full suites and are not additive.
The shadow test compares deterministic settlement with the legacy sheet adapter;
it does not claim production traffic or paid-model acceptance.

## Upgrade

1. Stop writers and retain a consistent database/home backup with its old wheels.
2. Install matching Core, Domain, Runtime and MCP 0.3.0 artifacts. Database opening
   applies historical migrations, including receipt ownership and nonce storage.
3. Inspect existing campaign profiles and create a verified checkpoint before
   explicitly adopting the current implementation through `campaign_rules`
   `core_relock`. Old snapshots require the explicit conversion branch operation.
4. Preserve original idempotency keys when recovering an unknown dispatch. Never
   retry a write under a new key. Current grants remain authoritative after restore.
5. Configure and canary the actual Host transport before routing hosted users.

Rollback restores matching old artifacts and the pre-upgrade backup together;
schema downgrade is not a substitute for recovering an old execution environment.
Published source artifacts contain no commercial rulebooks, private saves or
deployment credentials. GitHub release publication is distinct from hosted rollout
and package-index publication.

## 中文

0.3.0 将战役权限、修订检查、幂等、随机收据和持久续程从 MCP 移入独立 Runtime。
MCP 负责协议认证与结果转换；Domain 使用注册内容处理器和共用执行指令。
工作流文档来自操作注册表，完整依赖被封装为不可变、按内容寻址的 ZIP。
Core 同时修复事务归属、回滚传播、分支收据、存档恢复、历史迁移、检索和向量队列。

升级前必须保留匹配旧版本的完整备份。实现代码变化即使不改变规则指纹，也要求
显式检查点迁移；恢复存档不会恢复旧权限，也不会倒退实体修订号。未知写入结果
只能使用原幂等键恢复。发布验收结果见发布说明；线上 Host 部署和 PyPI 发布不由
本地测试或 GitHub 发布结果代替。
