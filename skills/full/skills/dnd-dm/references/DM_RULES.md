# DM 运行规则

## 战役启动与权威

1. 先列出活动战役并明确选择，不以名称、槽位或聊天历史猜测。
2. 读取战役、规则档案、当前模组/场景、近期事件和分支有效记忆。
3. 当前战役的 edition、locale 和 publication 是规则边界；不得静默混用
   2014/2024。规则问题先 search，再 expand 一个条目。
4. SQL Runtime 是状态权威，规则库是规则权威，当前模组原文是剧情事实权威。
   对话摘要只用于导航，不能覆盖它们。

## 每轮流程

1. 明确由哪个角色做什么；含糊时先追问。
2. 只描述角色能感知的信息，不提前暴露房间、反转、NPC 动机或附录。
3. 判断是否真的需要检定：必须同时存在不确定性、成功与失败的意义、规则允许且
   角色有能力尝试。自动成功和不可能行动不掷骰。
4. 读取行动者、目标和有关 NPC/怪物的完整角色卡与环境状态，确定能力、熟练、优势/
   劣势和 DC，再调用当前 exposure 中对应的公开 MCP 工具。`character_query(view="list")`
   只用于定位 ID；裁决前必须使用 `character_query(view="get")` 读取当前角色卡。
5. 骰子结果确定后叙事；不得先写结局再倒推骰子。
6. 一次行动造成的 HP、资源、条件、位置、线索、NPC、怪物与世界变化要在同一轮完整
   写入。个人物品、钱包、装备、准备法术、效果和资源使用 `character` v2 子命令；
   共享物资使用 `party` 子命令，不能手改 JSON 或创建另一份简化角色卡。
7. 场景结算以一次 `memory_change(action="commit")` 原子追加 event、稳定键 CampaignMemory 事实和
   仅属于实际知情 actor 的 ActorKnowledge。NPC 的重要对话结果不能写入已弃用的
   `actor_knowledge_change(action="add")`，也不能把一个 NPC 的认知复制给其他角色。
8. 新出现、获得、失去或转移的每件实物都必须是有稳定 `id`、名称和简短描述的 inventory
   item；钥匙、任务物、宝石和怪物掉落也不例外。物品归属或数量变化不能只写 event。
9. 公用 PC/NPC/怪物模板不是活跃游戏状态。战役只读取和修改其 campaign instance；任意
   模板可通过 `character_create_from(mode="template")` 实例化。PC 车卡使用
   `character_create_from(mode="build")` 创建模板和实例。

准备法术列表必须按版本和职业处理。车卡或升级阶段用
`character_spell_prepare(mode="replace_all")` 一次提交完整列表；游戏中只能把完整
`prepared_spell_ids` 随 `campaign_change(action="party_rest")` 中对应成员的长休选择一起原子提交，禁止连续切换单个法术来
绕过替换数量。2024 牧师/德鲁伊/法师长休可替换任意项，圣武士/游侠只可替换一项，
吟游诗人/术士/邪术师只在获得本职业等级时替换一项；2014 吟游诗人/游侠/术士/
邪术师使用已知法术，不建立准备列表。始终准备的法术不占名额，戏法不进入 1 环以上
准备列表，法师只能从法术书选择，多职业按每个法术的 `grant.source_key` 和对应职业
等级判断数量及最高环阶。

## 战斗

- 开战时确定参与者、位置、突袭与先攻；只使用角色实际拥有的能力和资源。
- 每回合依序处理回合开始效果、移动、动作、附赠动作、反应与回合结束效果。
- 攻击先判命中，再计算伤害、抗性/免疫、集中与濒死；不得让叙事绕过引擎结果。
- 角色仍有可用动作时不要擅自结束其回合。NPC 只能依据其已知信息行动。
- 战斗结束后统一结算条件、死亡、掉落、消耗、经验/里程碑和世界后果并保存。
- 对每个仍相关的 NPC/怪物保留完整 HP、临时 HP、资源、条件、效果、装备、物品和
  `notes.profile.summary`；击败、离场或死亡也是卡上的状态/叙事变化，而不是只写一行 event。
- preflight 的 `required_count` 必须是原文、已记录的随机遭遇掷骰或当前分支 DM 事实确定的
  完整分组数量，不能直接填写现有 `actor_ids` 的长度。缺一张必要角色卡就保持未就绪；原文还
  列出其他敌对、增援或可选组时应一并 manifest，不能截短引文来隐藏人数。

## 一致性、审计与并发

- 写入前保留读取到的 revision/state version；冲突时重新读取并重算，不覆盖新状态。
- 角色卡、战役状态、场景进度、事件和记忆必须表达同一事实。
- 每次转移物品或货币后重新读取来源和目的地；涉及队伍仓库时还要读取
  `campaign_query(view="party")`。
- `character_query(view="get")` 返回的 `derived` 只能用于裁定和展示。若
  `unresolved_rules` 非空，先
  检索规则并保留需要 DM 裁定的效果，不得伪造自动计算结果。
- 装备护甲、盾牌或防御性魔法物品时先用 `inventory_change(action="equip")`
  写入正确槽位，再读取
  `derived.armor_class_breakdown`。不得手改 `combat.ac.base` 来模拟装备加值；只有无甲、
  天然护甲或明确手工覆写才使用 `combat.ac.base` / `combat.ac.override`。
- `state_revision(action="undo"|"redo")` 只回退受审计变更，不删除 Snapshot。
  `snapshot_restore` 后不得尝试 `state_revision(action="redo")`
  到被快照放弃的未来；恢复的撤销游标才是当前分支的边界。
- 重要节点创建 Snapshot：开团基线、危险决定前、关键战斗前后、章节转换、升级、
  长休与会话结束。

## Snapshot、Recap 与恢复

- 当前 Snapshot schema 必须含战役元数据、规则档案、精确的模组激活修订集、所有
  PC/NPC/怪物完整卡、队伍背包和钱包、动态世界、场景进度、玩家角色映射、按时间顺序
  的 events、有效 memory 正文，以及当前 undo/redo cursor；不复制规则、模组静态原文
  和 embeddings。恢复时先恢复模组激活修订集，再恢复当前 scene；当前 scene 不得属于
  已退役模组修订。
- Recap 只描述相对上一存档的差量：剧情推进、新角色/地点、事件、后续影响、
  玩家选择和记忆候选；首个存档是基线。
- `restore` 前先 `verify`，说明目标和分支语义。恢复先保护当前状态，再产生新分支，
  绝不描述为覆盖历史。
- 恢复后重新读取战役、所有相关 actor、`campaign_query(view="party")`、场景、events、玩家映射和分支记忆，
  不能沿用聊天缓存。恢复前建立的事件、记忆、物品或角色状态不能继续在叙事中引用。

## 记忆和玩家映射

- event 保存时间顺序；memory 保存跨会话事实。未确认推测不得写成事实。
- 检索必须限定 campaign 和当前分支，不能混入兄弟分支。
- 玩家到 PC 的映射保存在 campaign state；平台可投影到自己的用户档案，但该档案
  不是权威，也不得被整体覆盖。

## 分支连续性与角色认知

- `memory` 是当前分支的世界事实：承诺、身份、线索和关系。它不是任何 NPC 的全知记忆。
- `event` 是不可变编年证据；发生事件不表示每个 PC/NPC 都知道它。
- 角色行动前读取该 actor 在当前分支的 `knowledge`。只给目击者、被告知者、调查成功者或
  受法术影响者写入角色认知；不能把 DM 真相、其他 NPC 的知识或兄弟分支的内容混入。
- `knowledge` 记录 `known`、`belief`、`rumor`、`false_belief`、`forgotten`、`modified` 等主观状态。
  `Modify Memory` 只修订目标 actor 的认知；真实 event 和 campaign memory 仍然保留。
- 私人发现保留在 `player:<id>`/`group:<id>` scope，明确分享后才转成 party 信息或世界事实。
- `branch_change(action="create"|"checkout")` 是时间线操作。不得自动合并两条 D&D 世界线；`Wish` 或
  明确改写历史时创建 retcon 分支并记录原因。

## 输出前自检

确认规则版本正确、没有剧透、骰子可追溯、资源与条件已结算、状态写入完整、事件没有
重复、长期事实已进入稳定键 CampaignMemory、主观信息已进入对应 ActorKnowledge，
并在需要时通过同一次 `memory_change(action="commit")` 创建 Snapshot。

公开 MCP 流程见仓库根目录 `references/mcp-contract.md` 和 `references/workflows.md`。
Runtime note: all state, rule, module, memory, and actor operations in Full mode
go through the `sagasmith_dnd` MCP. Use `continuity_context` for player-safe
context, keep `actor_id` explicit for every PC/NPC, and never trust a prompt role
or `player_name` as permission.

## 自动结算与 DM 裁决边界

| 自动结算（输入完整时） | 必须由 DM 建立事实或裁决 |
|---|---|
| 先攻、回合预算、多重攻击次数、移动消耗、2014/2024 力竭对 D20 与速度的修正 | 未记录的地形、碰撞、遮蔽、视线和距离 |
| 角色卡武器命中、优势/劣势抵消、自然 1/20、邻接瘫痪/昏迷目标自动重击 | 创造性动作、Help/Hide/Search 的叙事结果、目标是否可感知 |
| 逐伤害类型的免疫/抗性/易伤、一次来源的临时 HP/HP、0 HP 与巨量伤害 | 限定词不完整的伤害特性，例如“非魔法攻击造成的挥砍伤害” |
| 一次伤害来源对应的一次专注 DC、死亡豁免、治疗、石化抗性 | 不确定的法术/专长文本、任选目标或效果、非确定性恢复 |
| 已记录网格、敌对、触及、可见性与移动模式产生的借机攻击窗口 | 未记录触发器、强迫移动/传送的语义、剧情后果 |
| 2014/2024 突袭差异、反应支付、每回合法术限制 | 先攻同值时玩家/DM 的最终顺序选择 |

战斗 preflight 的 `ready` 只表示必需角色存在且整卡有效，不表示角色当前能行动或每项都可自动结算。逐卡检查
`card_valid`、`hard_blockers`、`state_flags`、`can_take_turn`、
`disabled_capabilities` 和 `available_capabilities`，再读取 `settlement`、
`manual_rulings`、`ruling_requirements`、`automatic_spell_ids` 与
`ruling_spell_ids`。`default_resolver="agent"` 的普通
DM 判定由 Agent 直接思考并用公开工具落实；玩家选择和缺失/冲突来源仍保留各自边界。
同时核对 `default_dm_resolver`、`agent_rulings` 和
`external_source_gaps`：`settlement="source_review_required"` 表示必须修复或
复核来源，不能由 Agent 编造；`mixed` 表示可入场，但列出的普通叙事裁决仍由 Agent
承担。原生工具结果与 facade 顶层必须保留相同的 `ruling_kind`，不得把嵌套的来源复核
误降级成通用 Agent 裁决。
0 HP 或 Dead 只会进入 `state_flags` 并令 `can_take_turn=false`；缺少普通射程、弹药、
完整法术 id 或完整法术施放水合只禁用对应能力，不阻止其他有效角色和能力开战。
地图上缺失通常射程的远程攻击属于来源/卡片缺失；有来源数值时应先在 lobby 修复卡片，
不能由通用判定编造距离。若原文明确用完整的位置限制取代数字射程（例如
“只能以 kobold 正下方的一个目标为目标”），则该攻击保持整卡有效且 `settlement=mixed`，
由 Agent 根据当前场景与临时战斗地图位置判定能否选择；不满足位置时改用合法已记录攻击，
不得反推或编造通用射程。无论装备状态或弹药是否耗尽，角色都可显式使用
`weapon_id: "unarmed-strike"`。战斗结束后的 `combat_query(status)` 是最终历史快照，当前
HP、状态和资源以 `character_query` 为准。

若引擎在提交前返回 `pending_ruling`、`committed=false`、`missing` 与
`retry_contract`，这不是已经发生的失败动作。按 `default_resolver` 接回控制：
普通地图、观察、时序和场景事实由 Agent 思考后用当前 revision 重试；缺失射程、未水合规则
或不受支持的来源契约保持 `missing_or_conflicting_source_review`，先修复导入/卡片。

`source_excerpt` 必须是同一模组场景中精确的规范化子串，不是模糊搜索提示；不得改写、翻译或
拿另一个同名房间的文字代替。`automatic_spell_ids` 只说明法术效果已有结构化结算，不会消除
法术成分、目标合法性、被动效果或命中附带效果中的 DM 裁决。

模组专属的动机、撤退、欺骗、谈判和叙事后果不得编译成 `if/then` 触发规则。首次使用前，
通过 `memory_change(action="upsert")` 建立 DM-only `context_anchor`，其中只保存实体
`related_refs` 与不可变 chunk 的精确 `source_ref`/`source_excerpt`。裁定前以当前
NPC、scene/location、quest 和关键 item 引用调用 `continuity_context`；Agent 结合返回的
`module_evidence` 与当前 HP、状态、位置、物品归属及已发生事件思考判定，再用公开 MCP
工具执行。标准规则与随机数仍由引擎结算，只保存实际发生的结果，不保存未选择的叙事分支。
DM 原文证据不得直接进入玩家上下文或 ActorKnowledge。

`module_search`/`module_expand` 返回的文档 chunk 可能没有 `scene_id` 或跨越相邻标题；用于 DC、
战斗参与者或地图之前，必须先从 index 选择 scene，再读取 `module_query(view="scene")` 并从该
scene 内容复制证据。PDF 控制字符、软连字符和弯引号的规范化只修复抽取差异，不会让跨 scene
或改写后的文字变成有效证据。

Scorching Ray 一类结构化多次法术攻击只调用一次 `combat_cast_spell`，动作与法术位也只支付
一次。保存返回的 `resolution_id`，再为每次攻击调用一次 `combat_resolve_attack`，把它放入
`action.spell_resolution_id`，且每次写入后刷新 campaign revision。若命中打开 Shield 反应窗，
先由目标结算自己的窗口，再继续下一次攻击。`remaining_attacks` 归零前不得结束施法者回合或
战斗；不得逐射线重复施法、伪装成武器攻击、外部掷伤害或直接修改 HP。

引擎只自动提交可以由规则版本、角色卡和已记录场景事实唯一确定的结果。
其余情况先用 `combat_choice(action="open")` 建立可审计窗口，再用
`combat_choice(action="resolve")` 记录 DM 决定；不得把猜测伪装成数值修正或角色卡事实。
Reaction spell/activity 必须消费属于该 actor 的 pending reaction window。
通用 Ready 不接受法术 payload。准备施法必须使用以下专用流程：

1. 只有施法时间为 Action 的法术可调用 `combat_ready(action="ready_spell")`。此时立刻支付动作与法术位/
   施法资源，声明角色可感知的触发条件，并立刻开始专注；该专注会结束原有专注。
2. 触发事件是否真实发生、是否满足声明条件，由 DM 根据已知场景事实裁定，再调用
   `combat_ready(action="trigger_spell")` 打开该施法者独占的反应窗口。
3. 施法者调用 `combat_ready(action="resolve_spell")`：`release` 消耗反应并释放；`decline`
   不消耗反应，法术继续保持，仍可等待下次符合条件的事件。
4. 专注中断、施法者下回合开始或战斗结束时，尚未释放的法术无效果消散。原本需要专注
   的法术在释放后恢复其正常专注时长；原本不需专注的法术结束临时保持专注。
5. 释放仅表示法术被施放出去，返回 `pending_ruling`。目标是否合法、法术攻击、豁免、
   伤害、范围和叙事后果仍须按对应工具及 DM 裁定结算，不能把触发文本当作自动命中或
   自动生效。
