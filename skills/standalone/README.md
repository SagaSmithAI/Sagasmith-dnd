# SagaSmith D&D Skills — Standalone

[Full MCP Runtime](../full/README.md) · [Standalone Skill](SKILL.md)

> 这是 `sagasmith-dnd/skills/standalone` 的便携子集，不是旧独立 Skills
> 仓库，也不是 Full Runtime 的离线数据库或安全兼容层。

Standalone 使用 Python 标准库、随附 SRD 参考与 `portable.py`，适合快速规则查阅、
演示或无法运行 MCP 的环境。

```powershell
cd skills/standalone
python portable.py doctor
python portable.py campaign start --name "Campaign" --edition 2024
```

加载 [`SKILL.md`](SKILL.md)。文件状态默认位于 `~/.sagasmith/`。

## 与 Full Runtime 的差异

- 没有 MCP `server/discover`、per-request delegation、role/phase enforcement、
  revision 或幂等事务；
- 没有 Core ActorKnowledge、branch isolation、authority receipt、随机流收据或
  audience-safe server projection；
- 不支持 MCP Tasks、PDF/OCR、FTS5、ChromaDB、扩展规则包编译或 Hosted 媒体结果；
- 战斗与状态能力只是便携子集，不能宣称与 Full 结构化引擎等价；
- portable memory 使用本地稳定 fact key 与追加式 revision log，但没有 Full
  Runtime 的事务和权限保证；
- portable 写入不会进入 Full campaign，也不能用作 Web projection/outbox 来源。

Standalone 没有 MCP session exposure；旧版 session exposure 也不是它的替代授权模型。
如果 Full MCP 不可用，Agent 必须先向用户说明这些差异，再明确切换到 Standalone。

随附 SRD/翻译遵循各自 NOTICE 与许可。不要把非 SRD 商业内容提交到仓库或公开分发。
