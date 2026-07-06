# ⚔️ SagaSmith D&D

[中文](README.md) | [English](README-en.md)

**D&D 5e 2014/2024 运行时** — 为 `sagasmith-core` 提供 D&D 系统插件与便携 JSON CLI。

> *"规则书为经文，骰子为审判官。"*

`sagasmith-dnd` 是一个轻量 Python 包，在 `sagasmith-core` 之上注册 `dnd5e` 系统 profile。它不绑定 Agent 平台——Agent 平台通过 `SagaSmith-dnd-skills` 调用同一 `sagasmith-dnd --json` CLI。

---

## 生态

| 仓库 | 定位 |
|------|------|
| ⚔️ **sagasmith-dnd**（本仓库） | D&D 5e 系统插件 + CLI |
| 🏗️ [sagasmith-core](https://github.com/dajiaohuang/sagasmith-core) | 通用引擎 — DB、文档、RAG |
| 🎲 [SagaSmith-agent](https://github.com/dajiaohuang/SagaSmith-agent) | 完整 AI DM 运行时 |
| 📦 [SagaSmith-dnd-skills](https://github.com/dajiaohuang/SagaSmith-dnd-skills) | D&D Agent Skill 定义 |
| ✍️ [SagaSmith-module-gen-skills](https://github.com/dajiaohuang/SagaSmith-module-gen-skills) | 冒险模组生成器 |

---

## 功能

- 🎲 **规则引擎** — 基于 `sagasmith-core` 检索，混合搜索（精确 + FTS + 语义），BGE-M3 Dense 嵌入
- ⚔️ **战斗** — 真实的 d20 掷骰、先攻、命中/伤害、豁免、暴击、先攻追踪、XP 计算
- 🏛️ **战役管理** — 创建、角色绑定、规则集绑定、模组绑定
- 👤 **角色** — D&D 5e 2014/2024 双版属性面板、职业、种族、法术位追踪
- 📖 **模组** — PDF/Markdown 导入、结构感知解析、场景索引、中英双语场景合并
- 🧩 **场景进度** — 作用域式追踪（`party` / `group:<id>` / `player:<id>`），从 party 透明继承
- 💾 **Snapshot** — DAG 存档/读档/校验、分支感知记忆、recap 生成
- 🗂️ **事件与记忆** — 发现事件日志、修订式战役记忆、自然语言查询

---

## 快速开始

```bash
# 安装
pip install "sagasmith-dnd[documents]"

# 检查运行时健康状况
sagasmith-dnd doctor --json

# 创建战役
sagasmith-dnd campaign start --name "深渊之门" --edition 2024 --locale zh --json

# 导入规则
sagasmith-dnd rules ingest --path ./srd/2024 --edition 2024 --locale en --json

# 导入模组
sagasmith-dnd module ingest --campaign <id> --path ./module.pdf --json

# 查询当前场景
sagasmith-dnd module current --campaign <id> --scope party --json

# 更新进度
sagasmith-dnd module set-progress --campaign <id> --scope party --scene <scene-id> --progress 50 --room "A1. 地窖" --state '{"visited_rooms":["A1"]}' --json

# 保存战役
sagasmith-dnd save create --campaign <id> --label "进入地城前" --json
```

---

## D&D Profile 场景解析

`DndModuleProfile` 在 `scene_boundaries()` 中实现了 D&D 专属的场景分割逻辑：

- **层级自动检测** — H2 默认作为场景；当 H3 数量 >= H2 × 5 时改用 H3
- **前言提取** — 章节标题与首个场景之间的内容作为独立 scene
- **子节与房间** — 场景下一级标题为 subsection，再下一级为 `room`
- **双语合并** — 相邻的中英双标题（如 `酒馆` / `Tavern`）自动合并为同一场景
- **标签分类** — 根据标题关键词自动标记 `combat` / `exploration` / `dungeon` / `social` / `transition`

---

## 安装 extras

| Extra | 用途 |
|-------|------|
| `dense` | sentence-transformers + ChromaDB 向量检索 |
| `documents` | PDF 解析 |
| `all` | 全部 extras |

Dense 检索是可选的，缺失时自动降级为精确/词法搜索。

---

## 贡献

```bash
pip install -e ".[all,dev]"
pytest --cov
ruff check .
```

---

## 致谢

- D&D 5e SRD 5.2.1 © Wizards of the Coast，以 [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) 授权使用
- [SagiriWWW/DND.SRD.zh-CN](https://github.com/SagiriWWW/DND.SRD.zh-CN) — D&D 5e SRD 5.1 中文翻译

---

## 许可证

MIT
