# 安装引导 — SagaSmith D&D

Agent 请按此流程安装。

安装入口：
`https://github.com/SagaSmithAI/sagasmith-dnd/tree/main/skills`

## 前置检查

```powershell
sagasmith-dnd doctor --json 2>nul
```

如果返回 `{"status":"ok"}` → ✅ **已安装**，直接加载 skill。

加载哪个 SKILL.md 由 Agent 根据环境自动选择。

## 基础文字版（推荐起点）

```bash
pip install sagasmith-dnd
sagasmith-dnd doctor --json
```

SKILL.md：`https://github.com/SagaSmithAI/sagasmith-dnd/tree/main/skills/full`
→ 加载 `full/SKILL.md`

基础版支持 SQLite、FTS、Markdown/text 和普通文字团，不安装 PDF、Pillow、
OpenCV、ChromaDB、Sentence Transformers 或 Torch。

按需添加能力：

```bash
pip install "sagasmith-dnd[documents]"  # PDF 解析
pip install "sagasmith-dnd[images]"     # 角色图提取
pip install "sagasmith-dnd[dense]"      # embedding + vector
pip install "sagasmith-dnd[all]"        # Domain 全部可选能力
```

扫描件 OCR 由 `sagasmith-dnd-mcp[ocr]` 提供，不属于 Domain 基础安装。

## Standalone 轻量版

如果当前环境无法安装 Python 包（无 pip、无 Python 3.11+）：

SKILL.md：`https://github.com/SagaSmithAI/sagasmith-dnd/tree/main/skills/standalone`
→ 从 `standalone/` 目录操作，加载 `standalone/SKILL.md`、使用 `standalone/portable.py`

使用 Python 标准库，数据存 `~/.sagasmith/`。不支持 PDF 导入、FTS5、ChromaDB。
需要 PDF 时请用户先转为 Markdown。
