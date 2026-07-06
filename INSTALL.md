# 安装引导 — SagaSmith D&D

Agent 请按此流程安装。

## 前置检查

```powershell
sagasmith-dnd doctor --json 2>nul
```

如果返回 `{"status":"ok"}` → ✅ **已安装**，直接加载 skill。

## 安装完整运行时

如果未安装，先装 Python 包：

```bash
pip install "sagasmith-dnd[documents]"
```

验证：

```bash
sagasmith-dnd doctor --json
```

完整版 skill 在 `SagaSmith-dnd-skills` 仓库的 **main** 分支：
`https://github.com/SagaSmithAI/SagaSmith-dnd-skills/tree/main`

Agent 加载 `skills/dnd-dm/SKILL.md`。

建议安装 dense 检索依赖以获得更好搜索效果：

```bash
pip install "sagasmith-dnd[all]"
```

## Standalone 轻量模式

如果当前环境无法安装 Python 包（无 pip、无 Python 3.11+），使用 standalone 分支：

`https://github.com/SagaSmithAI/SagaSmith-dnd-skills/tree/standalone`

Agent 直接加载根目录的 `SKILL.md`。

Standalone 模式使用 `tools/portable.py`（纯标准库，零依赖），数据存 `~/.sagasmith/`。
不支持 PDF 导入、FTS5 检索、ChromaDB。需要 PDF 时请用户先转为 Markdown。
