# 安装引导 — SagaSmith D&D

Agent 请按此流程安装。

安装入口：
`https://github.com/SagaSmithAI/SagaSmith-dnd-skills`

## 前置检查

```powershell
sagasmith-dnd doctor --json 2>nul
```

如果返回 `{"status":"ok"}` → ✅ **已安装**，直接加载 skill。

加载哪个 SKILL.md 由 Agent 根据环境自动选择。

## 完整版（推荐）

```bash
pip install "sagasmith-dnd[documents]"
sagasmith-dnd doctor --json
```

SKILL.md：`https://github.com/SagaSmithAI/SagaSmith-dnd-skills/tree/main/full`
→ 加载 `full/SKILL.md`

建议装 dense 依赖：
```bash
pip install "sagasmith-dnd[all]"
```

## Standalone 轻量版

如果当前环境无法安装 Python 包（无 pip、无 Python 3.11+）：

SKILL.md：`https://github.com/SagaSmithAI/SagaSmith-dnd-skills/tree/main/standalone`
→ 从 `standalone/` 目录操作，加载 `standalone/SKILL.md`、使用 `standalone/portable.py`

使用 Python 标准库，数据存 `~/.sagasmith/`。不支持 PDF 导入、FTS5、ChromaDB。
需要 PDF 时请用户先转为 Markdown。
