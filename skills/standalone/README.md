# SagaSmith D&D Skills — Standalone

**无 MCP、零第三方依赖的便携模式。** 它使用 Python 标准库、随附参考和 `portable.py`，适合快速规则查阅、演示或无法运行 Full MCP 的环境。

```powershell
cd standalone
python portable.py doctor
python portable.py campaign start --name "Campaign" --edition 2024
```

加载 [`SKILL.md`](SKILL.md)。文件状态默认位于 `~/.sagasmith/`。

## 与 Full 的差异

- 没有 MCP 会话 exposure、认证 principal 或 role enforcement；
- 没有 Core 的 actor knowledge、branch revision 与 rule receipt 保证；
- 不导入 PDF，不使用 FTS5/ChromaDB，不编译扩展规则包；
- 战斗与状态能力是便携子集，不能声称与 Full 结构化引擎等价；
- portable 的 memory 使用本地稳定 fact key 与追加式 revision log，但没有 Full
  Runtime 的事务、ActorKnowledge、分支隔离和权限模型；
- portable 写入不会出现在 Full campaign 中。

如果 Full MCP 不可用，Agent 必须向用户说明这些差异后再切换。随附 SRD/翻译遵循各自 NOTICE 与许可。
