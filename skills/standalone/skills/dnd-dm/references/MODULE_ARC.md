# 模组运行结构

- 静态章节、场景、房间和检索向量不进入存档。
- 当前场景、房间、探索度、线索和世界变化进入存档。
- 章节转换前记录事件并创建快照。
- 恢复快照会创建分支，不改变模组静态原文。

```powershell
sagasmith-dnd event add --campaign <id> --type chapter --summary "<transition>" --json
sagasmith-dnd save create --campaign <id> --label "<chapter transition>" --json
```

准备每章时维护关键节点、可选入口、失败推进、事件依赖、等级/里程碑和跨章节 NPC
命运。玩家偏离预期时让世界按既有事实响应并通过线索自然引导，不强制传送或篡改已经
发生的选择。静态章节与场景不进入 Snapshot；当前进度、线索、NPC/派系状态及玩家造成
的世界变化必须进入。
