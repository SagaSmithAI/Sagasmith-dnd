# 角色创建与升级

## 创建流程

1. 读取战役 edition、locale、可用 publication 和可选规则。
2. 确认玩家概念、等级及是否使用预设、标准数组、购点或掷骰。所有随机结果通过 CLI。
3. 按版本依次选择物种/种族、职业、背景、属性分配、熟练项、语言、工具、装备、
   职业特定选项与法术。每一步只展示该规则档案允许的选项。
4. 计算熟练加值、豁免、技能、AC、HP、速度、攻击、法术 DC/攻击和资源上限。
5. 补充姓名、外观、动机、关系、信念及玩家名。
6. 展示完整草稿和来源；玩家最终确认前不得创建正式角色。
7. 创建后重新读取并验证派生值、资源上限和 campaign/player 绑定。

新玩家使用逐步对话；熟悉规则的玩家可一次提交草稿，但仍须逐项验证并最终确认。
本 standalone 目录只保存未验证的 JSON 草稿，不能充当 Runtime 的完整角色卡。需要完整
PC、NPC、怪物卡、物品/钱包、装备槽位与 AC 派生、准备法术、效果或 NPC 事件记忆时，必须
切换 Full Runtime，并按 `full/references/character-schema-v2.md` 创建 `sheet v2` / `notes v2`。

## 升级

从当前角色增量升级，不得重建并覆盖既有选择。依次处理 HP、职业/子职能力、熟练加值、
技能与豁免、专长或属性提升、法术与法术位、装备资源及所有派生值。验证后提交，记录
升级事件并创建 Snapshot。

```powershell
sagasmith-dnd character create --campaign <id> --name "<name>" --type pc --player "<player>" --sheet '<json>' --json
sagasmith-dnd character list --campaign <id> --json
sagasmith-dnd character show --id <character-id> --json
```

Portable 模式不校验角色字段，也不提供 Runtime 的 `character inventory|wallet|equipment|spell|effect|memory|resource`
子命令。不得把其草稿宣称为已按规则验证的完整角色卡。
