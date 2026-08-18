# SagaSmith D&D Skills — Full MCP Runtime

Full 模式通过 `sagasmith_dnd` MCP 运行 D&D 5e 2014/2024。状态、规则包、模组、
角色、知识、分支与战斗均由 MCP 持有；本目录只保存 Agent 工作流。

启动时读取 `sagasmith://bootstrap`，调用 `storage_status`、
`server_capabilities` 与 `campaign_query(view="resume")`。随后调用
`exposure(action="open")`，用 `search` 找工具、用 `set` 修改原生工具列表，并在
`tools/list_changed` 后刷新 schema。可见性只以当前原生列表为准。

规则书和模组书只在 Lobby 编辑：Core+D&D 完成机械首轮，Agent 反复读取证据、找错、
修改、复查，最后显式确认并定稿为不可变 Pack。单书决策保存在草稿/Pack；只有跨书可复用
的检查流程进入 Skill。

Play 中的听见、理解与 NPC 反应由 Agent 裁决。Combat 同时支持 Grid 与 Agent 空间模式；
Agent 模式由 Agent 判断范围、命中条件、阻挡、友军误伤与移动合法性，MCP 只验证和提交
结构化事实及权威状态变化。

完整入口见 [SKILL.md](SKILL.md)。
