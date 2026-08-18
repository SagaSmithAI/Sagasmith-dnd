# Runtime 数据合同

Full Runtime 只通过 `sagasmith_dnd` MCP 读写状态。Agent 不直接访问 SQLite、
Chroma、artifact 文件或本地 D&D CLI。

## 权威状态

- `Character.sheet`：机械状态；`Character.notes`：角色资料，不保存角色知识。
- `derived`：只读计算结果，不能写回。
- `campaign.state.party.inventory`：队伍共享物品和钱包。
- `memory`：当前分支的世界事实；`event`：时间顺序证据。
- `actor_knowledge`：一个 PC/NPC 自己知道或相信的内容，不是世界真相。
- snapshot restore 创建新分支，不覆盖历史分支。

## 身份与权限

- 每次平台用户映射到稳定 `principal_id`。
- `access_grant(scope="campaign")` 管理 owner、DM、player、observer。
- `access_grant(scope="actor")` 管理某个 PC/NPC 的 control 和 private view 权限。
- `player_name` 只是角色卡字段，不是授权依据。
- 玩家读取使用 `continuity_context`、`module_query(view="scene")` 等带 principal 的过滤接口。

## 写入合同

- 先读取当前 revision，再携带 `expected_revision` 写入。
- 可重试操作必须携带 `idempotency_key`。
- 金币、物品、弹药和跨角色变化必须使用对应 granular MCP tool。
- 跨实体操作属于一个 mutation group，一次 undo/redo 必须整体恢复。
- revision conflict 时重新读取和审查，禁止覆盖新状态。
- 场景结算使用一次 `memory_change(action="commit")` 原子写入 event、稳定键 world facts、
  逐 actor knowledge 和可选 snapshot；更新已有事实或知识时携带各自的
  `expected_revision_id`。

## 规则与模组

- fresh MCP storage 使用 `rule_seed_status` / `rule_seed_bundled` 初始化 bundled SRD。
- 规则查询严格使用 `rule_search` → `rule_expand`。
- 模组导入严格使用 `module_draft(start → evidence/edit → finalize)`，再由 `content_pack(activate, kind="module")` 激活。
- `module_search` 只用于候选选择；叙事前必须通过 scene/chunk 读取。
- scene visibility 使用 Core canonical `public`、`group`、`restricted`；
  `restricted` 内容由服务端过滤。

不属于 Full Runtime 执行路径。
