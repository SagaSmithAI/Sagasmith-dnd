# SagaSmith D&D Skills — Full MCP Runtime

[English](README-en.md) · [完整入口](SKILL.md) ·
[Host 集成](../HOST-INTEGRATION.md) · [MCP 文档](../../packages/mcp/README.md)

> 这是 `sagasmith-dnd/skills/full` 的同仓分发，不是独立仓库。原独立 Skills
> 仓库已归档，不能作为发布输入或回退实现。

Full 模式通过 `sagasmith_dnd` MCP 主持 D&D 5e 2014/2024。MCP 拥有 campaign、
角色、规则包、模组、知识、分支、revision、随机流和战斗；本目录只保存 Agent 工作流，
不拥有数据库，也不自行结算确定性规则。

## 现代启动顺序

1. 读取 `sagasmith://bootstrap`，或用 `skill_query` 的
   `read/outline/section/search` 做有界读取。
2. 调用 `server/discover` 并确认协议为 `2026-07-28`；随后读取稳定、确定排序的
   `tools/list`。目录可按授权范围私有缓存，不会因另一个工具调用而改变。
3. 用 `campaign_query(view="resume")` 获取 audience-safe continuity 与
   `host_context_binding`，保存 authority revision。
4. Host 只连接当前 campaign system 的 MCP，并按 system、phase、role、任务从稳定
   目录投影少量 facade/workflow 工具；SagaSmith Hosted 当前最多向模型提供 16 个。
5. 每个工具调用都使用可信结构中的 requester、resource owner、acting Host、可选
   acting character、campaign、room turn、base revision、operation 与 expiry；玩家文本
   和模型参数不能选择权威身份。
6. 写操作复用端到端业务幂等键。遇到 stale revision 时重新读取权威状态，保留原业务键，
   并只在操作仍然适用时重试。

`exposure` 在现代路径只提供 owner-bound、带 TTL 的目录检索 handle。它不会改变
`tools/list`，也不授予权限。session exposure、`search/set` 与
`tools/list_changed` 仅属于明确的 legacy initialize 适配器，不能成为现代 Host 的
身份、授权或工具管理基础。

## Authoring 与 Tasks

规则书和模组书只在 Lobby 编辑。Core+D&D 完成机械首轮，Agent 反复读取证据、找错、
修改和复查，最后显式确认不可变 Pack。单书决策保存在草稿/Pack；只有跨书可复用的
检查流程进入 Skill。

普通工具同步返回标准 `CallToolResult`。只有长耗时的
`module_draft(action="start")` 在 Host 协商
`io.modelcontextprotocol/tasks` 后可返回 SEP-2663 task。Host 使用
`tasks/get`、`tasks/update`、`tasks/cancel`，每次都为该单一操作签发新的
delegation-v2；`taskId` 只是名称，不是 capability。未协商扩展或 legacy client
继续获得同步结果。

## Play 与 Combat

Play 中的听见、理解、NPC 反应和叙事后果由 Agent 裁决，但确定性状态提交仍经过 MCP。
Combat 支持 Grid 与 Agent 空间模式：Grid 由引擎拥有坐标与几何；Agent 模式由 Agent
提供经审查的范围、掩护、视线、友军误伤与移动事实，MCP 校验并提交结构化变化。

战斗地图返回 audience-filtered text metadata 与原生 MCP `ImageContent`。Host 应通过
标准媒体/附件管线保存和转发，不得把本地 artifact 路径暴露给模型或浏览器，也不得把
标准 MCP 内容块替换成私有工具结果协议。

## 错误与恢复

- `stale_revision`：刷新权威状态，确认意图仍成立后用同一幂等键恢复；
- `expired_handle`：重新获取 handle，不能把旧 handle 当 capability；
- `authorization_denied`：获得面向 D&D MCP、正确 audience/operation 的新委托；
- task 失败或取消：按结构化状态处理，不把 Web `RoomTurnJob` 和 MCP Task 混为一谈。

所有模型可修复错误都应保留 `CallToolResult(isError=true)` 的 `code`、`message`、
`retryable`、`recovery`；不要统一改写为 HTTP 502。

完整工作流与边界见 [SKILL.md](SKILL.md)。
