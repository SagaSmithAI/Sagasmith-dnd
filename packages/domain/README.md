# SagaSmith D&D Domain

[中文](README.md) · [English](README-en.md) · [安装指南](INSTALL.md) ·
[MCP](../mcp/README.md) · [SagaSmith Web](https://github.com/SagaSmithAI/SagaSmith-Web)

> 当前源码位于 `sagasmith-dnd/packages/domain`。原拆分仓库已归档，不能作为发布输入。

`sagasmith-dnd` 是 SagaSmith 的 D&D 5e 2014/2024 确定性规则与内容运行时。它通过
`sagasmith.systems` 注册 `dnd5e` 系统插件，为同仓 MCP 提供可测试的角色、法术、活动、
规则包、内容包、空间与战斗机制。

Domain 不拥有 Agent 身份或 Hosted 授权，也不面向浏览器直接提供权威写入：Skills 教
Agent 如何主持，MCP 负责 campaign/actor/role/phase/revision/idempotency 权威，Domain
只把已经确定的规则输入结算成可验证结果。

## 能力边界

- 2014/2024 版本化核心规则包、campaign rule profile、mechanic IR 与来源锁定；
- D&D sheet、派生属性、装备、负重、资源、熟练项、法术准备与专注；
- 标准数组、购点、掷骰及版本化角色创建内容；
- 先攻、回合、动作经济、攻击/豁免/伤害、抗性/免疫、倒地、死亡豁免、反应与选择窗口；
- Grid 模式的引擎坐标/几何，以及 Agent 空间模式的结构化事实校验；
- 非战斗检定、休息、资源、追逐与常见角色活动；
- `.sagasmith-pack` schema-v2 内容包及 `sagasmith.actor-card.v3`；
- 规则书、模组、角色卡、SRD 预设与来源/许可/校验和验证。

引擎只自动结算规则输入已经明确的机械部分。行动意图、目标、缺失的视线/掩护/距离、
可选规则冲突、homebrew、NPC 决策和叙事后果必须由 Agent/GM 提供证据或裁决；MCP 通过
preflight、choice window 和 ruling-required 结果保持这条边界。

## 安装与 CLI

需要 Python 3.11+：

```bash
pip install sagasmith-dnd
sagasmith-dnd doctor --json
sagasmith-dnd --help
```

基础包只包含文本运行时，不隐式安装 PDF、图片、embedding、向量库或 Torch：

| Extra | 用途 |
|---|---|
| `documents` | PDF 文档解析 |
| `images` | PyMuPDF + Pillow 角色图提取 |
| `embedding` | Sentence Transformers 嵌入 |
| `vector` | ChromaDB 向量存储 |
| `dense` | `embedding` + `vector` |
| `all` | Domain 的全部可选能力 |

```bash
pip install "sagasmith-dnd[documents]"
pip install "sagasmith-dnd[images]"
pip install "sagasmith-dnd[dense]"
```

扫描件 OCR 属于 `sagasmith-dnd-mcp[ocr]`，不是 Domain 基础依赖。Agent/Hosted
集成应使用同仓 [D&D MCP](../mcp/README.md)，不要让模型拼 CLI 或直接修改数据库。

## 数据升级与回滚

```bash
sagasmith-dnd database upgrade --json
```

运行升级前停止所有写入并创建一致性备份。当前 Snapshot schema v8 将完整状态文档保存为
独立、有界、带 checksum 的 `zlib-1` 记录；没有原地 downgrade。回滚必须恢复整套数据库、
Core/D&D 版本和 component lock，不能只降级 Python wheel。

## 内容、角色卡与记忆

`core_rules`、`addon`、`module`、`preset` 统一使用
`sagasmith.content-package` v2 / `.sagasmith-pack`，但保持不同的安装和激活权限。导入端
验证 descriptor、依赖、source/blob/asset hash、D&D 语义与许可元数据；导入不等于激活。

PC、NPC、怪物统一使用 `sagasmith.actor-card.v3`。角色图只引用包内
content-addressed asset，不进入运行时角色或 Snapshot。内置 SRD 目录和 preset 构建有精确
覆盖测试；商业规则书原文不随 Apache-2.0 软件包分发，公开再分发必须有可验证授权。

运行时内置一份仅含元数据的兼容性锁，覆盖 SagaSmith Content Library 当前十个
`official_supplement` / `official_legacy` D&D addon，共 2,007 个 artifact，其中包括
1 个扩展职业和 77 个子职；锁文件不包含任何商业规则原文。导入或发布验收前，可验证已授权的
本地内容库检出：

```bash
sagasmith-dnd content verify-official-expansions \
  --path /path/to/SagaSmith-dnd-content-library --json
```

验证会检查不可变 archive 身份、当前 D&D Pack 语义、声明的内容总数，以及全部 1,134 个
`selection_ready` materializer 契约。该命令不下载、不复制内容，也不授予任何许可。

客观世界事实进入 CampaignMemory 并使用稳定 `fact_key`；PC/NPC 的知识、信念、谣言与
误解进入 ActorKnowledge。角色 notes 和 Agent workspace 不是权威 campaign memory。

## 开发

从仓库根目录执行：

```bash
uv sync --all-packages --all-extras
uv run --package sagasmith-dnd pytest packages/domain/tests
uv run ruff check packages/domain
```

如只在本目录开发，也可使用：

```bash
pip install -e ".[all,dev]"
pytest --cov
ruff check .
```

原创代码使用 Apache-2.0；SRD 派生内容、翻译和第三方来源遵循各自 NOTICE/许可。
