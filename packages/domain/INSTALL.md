# 安装指南 — SagaSmith D&D

推荐的 Agent/Hosted 入口是仓内 [D&D MCP](../mcp/README.md) 与
[Full Skills](../../skills/full/SKILL.md)。只有规则引擎开发、诊断或便携脚本才直接安装
Domain CLI。

## 前置条件

- Python 3.11 或更新版本；
- 对当前环境可写的独立数据目录；
- 需要升级现有数据库时，先停止写入并创建一致性备份。

## Domain CLI

```bash
pip install sagasmith-dnd
sagasmith-dnd doctor --json
sagasmith-dnd --help
```

按需安装可选能力：

```bash
pip install "sagasmith-dnd[documents]"  # PDF 解析
pip install "sagasmith-dnd[images]"     # 角色图提取
pip install "sagasmith-dnd[dense]"      # embedding + vector
pip install "sagasmith-dnd[all]"        # Domain 全部可选能力
```

扫描件 OCR 由 `sagasmith-dnd-mcp[ocr]` 提供，不属于 Domain 基础安装。

## Full MCP Runtime

```bash
pip install sagasmith-dnd-mcp
sagasmith-dnd-mcp
```

默认是 stdio。共享或 Hosted 环境使用 Streamable HTTP，并按 MCP README 配置
per-request delegation-v2；不要用浏览器 token、模型 principal 或连接 session 代替授权。

## Standalone 便携模式

如果无法安装 Full MCP，可加载
[`skills/standalone/SKILL.md`](../../skills/standalone/SKILL.md)，在
`skills/standalone` 中运行：

```powershell
python portable.py doctor
python portable.py campaign start --name "Campaign" --edition 2024
```

Standalone 使用 Python 标准库和本地文件状态；它没有 MCP 授权、revision、
ActorKnowledge、完整结构化战斗、PDF/OCR 或规则包保证。portable 写入不会进入 Full
campaign，Agent 必须在切换前明确告知用户这些差异。

## 数据库升级

```bash
sagasmith-dnd database upgrade --json
```

先停止所有写入并备份数据库。当前格式没有 downgrade；回滚必须恢复匹配的数据库、
Core/D&D 版本与 component lock。
