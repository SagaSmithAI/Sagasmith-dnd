# 模组索引与运行

模组由 Runtime 从 PDF 或 Markdown 建立章节、场景、房间、页码和检索块。

**双模式：** 有 `sagasmith-dnd` 时用下面的 CLI；没有时把每条 `sagasmith-dnd` 换成
`python tools/portable.py` 即可。

场景层级由 D&D profile 解析：

- H2 默认作为场景；当 H3 数量至少为 H2 的五倍时，改用 H3；
- 章节标题与首场景之间的前言建立独立 scene；
- 场景下一级标题记录为 subsection，再下一级记录为 `room`；
- 相邻且无正文的中英双语标题合并为同一 scene；
- scene index 返回 `scene_type`、起止行、页码、headings、subsections、tags 和 keywords。

```powershell
sagasmith-dnd module inspect --path "<path>" --json
sagasmith-dnd module ingest --campaign <id> --path "<path>" --json
sagasmith-dnd module list --campaign <id> --json
sagasmith-dnd module current --campaign <id> --scope <scope> --json
sagasmith-dnd module search --campaign <id> --query "<query>" --limit 5 --json
sagasmith-dnd module expand --chunk <chunk-id> --json
sagasmith-dnd module read-scene --campaign <id> --scene <scene-id> --json
```

`scope` 使用 `party`、`group:<id>` 或 `player:<id>`。不传时为 `party`。个人或分队
尚未建立独立 current scene 时会继承 party scene；一旦用该 scope 写入进度，就独立推进。
同一名玩家同一时刻只能有一个 current scene。运行时只读取行动者 scope 的当前场景。
隐藏房间、反转、NPC 动机和附录内容不得提前泄露。

进度更新：

```powershell
sagasmith-dnd module set-progress --campaign <id> --scope <scope> --scene <scene-id> --progress 50 --room "<room>" --state '<merged-json>' --json
```

导入前先 inspect 和 list，以 source checksum 去重。入库后确认章节、场景、房间、页码和
标题层级；缺失或低置信度结构必须报告，不能凭模型记忆补全出版模组。

## 带团状态机

每次处理玩家行动：

1. 根据行动者选择 scope，调用 `module current`。若返回空，才从 index 选择可进入 scene。
2. 调用 `read-scene` 读取完整当前场景。搜索结果只用于选候选；使用前必须 expand。
3. `intro` 只作铺垫；`combat`/`encounter` 仅在原文触发条件满足时开战；
   `dungeon`/`exploration` 按 subsection 推进；`transition` 先核验离场条件。
4. `subsections.type=room` 表示可独立探索的房间。只把玩家实际进入的房间写入
   `current_room`，不列出未见房间。`keywords` 只用于检索，页码只用于溯源。
5. 先读取当前 `progress.state`，合并新事实后整体写回，不能删除未知键。建议结构：

```json
{
  "visited_rooms": [],
  "discovered": [],
  "defeated": [],
  "triggered": [],
  "keeper_flags": []
}
```

6. 玩家分队时，为每组使用 `group:<id>`；单独行动使用稳定的 `player:<character-id>`。
   公共世界变化写 event/memory，各 scope 的可见信息保留在各自 state。
7. 离开场景时先把旧 scene 标为 `completed` 且进度设为 100，再将新 scene 设为
   `current`。切换章节前创建 Snapshot。

不得把 scene ID、Keeper tags、隐藏 transition 或其他 scope 的私人发现直接说给玩家。
需要给外部工具使用时导出稳定的 scenes JSON 索引。
