# SagaSmith D&D Skills

[官网](https://sagasmithai.github.io) · [平台总览](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md) · [D&D MCP](https://github.com/SagaSmithAI/SagaSmith-dnd-mcp) · [D&D runtime](https://github.com/SagaSmithAI/Sagasmith-dnd) · [托管服务](https://github.com/SagaSmithAI/SagaSmith-service) · [内容目录](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

**让兼容 SKILL.md 的 Agent 学会完整主持 D&D 5e 2014/2024。** 本仓库保存主持策略、工具使用契约、规则参考和 workspace 模板；不拥有数据库，也不自行实现规则结算。

> Skills 决定 Agent 应该怎么做；MCP 决定它此刻能做什么；规则引擎决定机械结果。

## 两种模式

| 模式 | 入口 | 能力 | 适用场景 |
|---|---|---|---|
| **Full MCP Runtime** | [`full/SKILL.md`](full/SKILL.md) | 持久化战役、actor knowledge、规则包、PDF/模组导入、结构化战斗、分支 Snapshot | 实团与 Agent 集成 |
| **Standalone** | [`standalone/SKILL.md`](standalone/SKILL.md) | 标准库便携脚本、随附 SRD、文件状态 | 临时查阅、演示、无 MCP 环境 |

Standalone 不是 Full 的离线数据库替身。切换到 portable 后必须明确告知用户：写入不会进入 MCP 战役，且缺少规则包、权限、actor knowledge 与结构化战斗的完整保证。

## Full 模式如何工作

```mermaid
flowchart LR
    A[Agent] --> S[D&D Skills]
    S --> E[MCP exposure<br/>lobby · play · combat]
    E --> M[SagaSmith D&D MCP]
    M --> R[D&D runtime + Core]
```

Full 模式首先调用 `exposure(action="open")` 建立会话，再用 `search/set` 按任务改变原生工具列表。MCP 端根据战役阶段、principal、role、campaign 和 TTL 控制工具；Skill 不复制工具白名单，也不允许模型越过 exposure 直接写状态。

### 可分享内容

- PC、NPC、怪物统一使用包内 `sagasmith.actor-card.v3`；导入会创建新身份，不复制 actor knowledge，角色图也不会进入 snapshot。
- 2014/2024 SRD 怪物与 NPC 以默认 preset pack 随运行时提供，不依赖 Host 中的怪物名称硬编码。
- 规则书构建必须在发布前固定每张卡的 resolution；显式动作变体拆成独立卡，OCR 只允许结构可证的恢复，不把补义延迟到首次触发。
- 结构化模组可打包 Scene Atlas、原始索引、地图/资产、审核内容、NPC/怪物/预设 PC 卡及稳定关联；战役进度、记忆、分支与 Snapshot 不混入内容包。
- 核心规则、Addon、模组与预设均导出为携带完整来源、稳定引用和内容寻址资产的 `.sagasmith-pack`；规则安装与战役启用保持独立。
- 完整流程由 Lobby 的 `content.packs` Skill group 按需加载；详情见 [Full MCP contract](full/references/mcp-contract.md#unified-core-rule-addon-module-and-preset-packages)。

### Lobby：游戏外准备

- 建团、成员与权限、分支、Snapshot；
- 车卡、装备、法术准备与初始资源；
- 导入规则书，编译/安装扩展规则包并锁定 campaign profile；
- 生成模组或从白名单暂存 PDF/Markdown/text，经 Core 结构化后检查 scene/spatial index 与 revision diff；
- 导入/导出统一角色卡、SRD 预设包、结构化模组包与扩展规则包；
- 初始化 campaign memory 与每个 PC/NPC 各自的 actor knowledge。
- 命名 NPC 可使用签名、零工具、无子会话持久化的隔离演绎回合；机械结算与最终写入仍由公开 MCP 事务负责。

### Play：非战斗带团

- 读取 continuity context 与当前场景；
- 只向调用者展示允许的角色、场景和知识；
- 处理非战斗检定、资源、调查、社交与场景进度；
- 在重大决定点记录事件、更新知识并创建 Snapshot；
- 确认进入遭遇后才调用 `combat_start`。

### Combat：结构化战斗

- 读取可见战斗状态、合法选项和临时 combat map；
- 先 preflight，再处理目标/距离/反应/选择窗口，最后提交攻击、法术或活动；
- 自动结算确定性机械；意图、视线、掩护、规则冲突和叙事后果交给 Agent/GM；
- 禁止用普通 character write 绕过 action economy；结束战斗后自动回到 play 工具面。

## Skill 组成

| Skill | 职责 |
|---|---|
| `dnd-dm` | 实际主持、规则检索与裁决、场景推进、战斗、隐藏信息边界 |
| `dnd-campaign-manager` | lobby 生命周期、角色/权限、规则/模组导入、分支与 Snapshot |
| `module-generator`（由 MCP 暴露） | 生成结构化、可检查、可导入的冒险 artifact |

Full 包还包含[长线叙事架构](full/references/long-form-narrative-architecture.md)、
MCP contract、workflow、rule boundary、combat 与内容导入参考，以及
`SOUL.md` / `IDENTITY.md` / memory workspace 模板。

## 安装

推荐先启动 [SagaSmith-dnd-mcp](https://github.com/SagaSmithAI/SagaSmith-dnd-mcp)，再让 Agent 加载本仓库 `full/SKILL.md`。MCP 也可将同一 Skill 作为 resources/prompts 提供给不直接安装仓库的 Host。

```text
用户：安装 https://github.com/SagaSmithAI/SagaSmith-dnd-skills
Agent：检测 D&D MCP → 可用则加载 full → 不可用时询问是否明确切换 standalone
```

## 随附规则参考

| Corpus | Edition | Locale | 说明 |
|---|---|---|---|
| SRD 5.2.1 | 2024 | English | 对应 CC 许可的核心参考 |
| SRD 5.1 | 2014 | English | 旧版参考 |
| SRD 5.1 | 2014 | zh-CN | 便利翻译，保留来源署名 |

参考 Markdown 用于查阅与 seed；战役真正使用的规则由 MCP campaign rule profile 和精确 rule-pack lock 决定。

## 许可

Skill 代码与原创文档使用 Apache-2.0。SRD 与翻译内容遵循各自 NOTICE/许可；商业规则书不会随本仓库分发。
