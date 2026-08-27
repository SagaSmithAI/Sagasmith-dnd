# 战斗地图与空间呈现

本参考描述 SagaSmith D&D 当前的地图契约。权威状态始终位于 D&D MCP；
聊天客户端、浏览器和图片都只是经过受众过滤的呈现层。

## 两种定位模式

`combat_start` 必须为整场遭遇固定一种 `positioning_mode`：

- `grid`：使用五尺方格、临时 battle map 和每个参战者的明确坐标。
- `agent`：不创建地图或坐标，由 Agent 为每个动作提交结构化空间事实。

战斗中不得切换模式。Grid 缺少地图或任一参战者坐标时，应修复输入，
不能由 UI、图片生成器或 Agent 猜测位置。

## Grid 权威来源

Grid 战斗只选择一种地图来源：

1. 优先使用已定稿 Module Pack 中经过审核的 `battle_map_template_id`；或
2. 提交有明确 DM 理由的 bounded `battle_map` override。

两者不得同时提交。D&D Grid 固定为每格五尺。墙体、阻挡、困难地形、
部署区域、位置和地图 revision 都由 MCP 验证并持久化。装饰图片、生成式底图
和客户端画布不能反向产生机械几何。

地图运行期变化只通过 `combat_map_patch` 写入。每次 patch、移动冲突、角色加入、
restore、undo 或 redo 后，都重新查询权威地图与 revision；不要直接修改 Pack 模板。

## Web Workbench

D&D Workbench 只读取 Gateway 返回的受众投影。拖动 Token 只生成移动提案，
最终距离、阻挡、反应窗口和权限仍由 MCP 决定。浏览器不得读取 MCP 数据库、
Pack blob 或本地文件路径，也不得在 live 请求失败时静默切换为 demo 数据。

若受众投影没有公开阵营，Token 必须显示为中性/未知，不能默认当作友军。
私有 HP、conditions、隐藏单位、DM 图层和 source identifiers 不得从 UI 推断或补齐。

## 群聊与玩家共享图片

共享频道只使用原生 MCP 媒体结果：

```text
combat_query(
  view="render",
  payload={"audience_projection": "party_public"},
)
```

Host 原样转发返回的 PNG `ImageContent`，并使用同一响应内的安全 caption 和
`alt_text`。不得从 `caller`、DM 状态、父对话、模组来源或本地 artifact 路径
重新拼装共享图片或文案。私聊同一授权受众时才可使用 `caller` render。

推荐在以下时机发送新图：

- Grid 战斗开始；
- 地图 patch 或 revision 改变；
- 增援进入；
- tactically important move；
- 玩家明确请求查看。

不要在每次普通写入或每个回合机械性重发。图片失败是媒体失败，不影响权威战斗；
继续文字流程并说明附件未发送。Host 不支持图片时，发送服务端返回的安全 caption
和 `alt_text`，不要改用电子表格或自行导出的状态文件。

## 图片与生成式资产

Pack 中的 `map_asset_key` 是受来源、checksum 和内容权利约束的资产引用，
并不自动表示可以向玩家公开。只有被明确审核为 `party_public` 的独立地图资产
才能进入共享图；未标记资产继续视为私有证据或 DM 呈现。

ComfyUI 等工具可以离线制作装饰底图或纹理，但必须满足：

- 模型和输出许可允许目标用途；
- 人工检查后绑定 checksum、media type、alt text、许可和署名；
- 不包含秘密区域、隐藏单位或 DM 注释；
- 不推导墙体、视线、阻挡、距离或坐标；
- 与权威 Grid 不一致时，以 MCP 状态为准。

运行时不得为了生成一次战斗图而调用外部图片模型。战斗渲染应保持可重复、
低延迟，并在离线或群聊附件能力有限时可靠降级。
