# SagaSmith Module Pack Builder

[官网](https://sagasmithai.github.io) · [平台总览](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md) · [托管服务](https://github.com/SagaSmithAI/SagaSmith-service) · [内容目录](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

这是一个面向当前 sagasmith.content-package v2 的 AI 原生模组构建 Skill。

它是“系统感知”的，而不是把某个系统的字段伪装成通用 schema。目前明确
支持：

- D&D 5e：system_id 为 dnd5e，支持 2014/2024；
- 克苏鲁的呼唤 7 版：system_id 为 coc7e，支持 Classic/Pulp。

authoring campaign 决定系统；Core 负责可移植 Package 边界；system package
负责确定性解析、规则与验证；MCP 负责权威草稿状态、授权、修订、幂等和不可
变归档；Agent/Skill 负责来源解释、语义审阅、修复和最终确认。

## 当前流程

~~~text
单一规范来源
  -> module_draft(start)
  -> module_draft(get/evidence/edit)
  -> module_draft(finalize)
  -> content_pack(get)
  -> 可选 content_pack(import/activate)
~~~

默认交付物是已构建、不可变的 Pack。安装和激活是额外的显式操作。

## 核心约束

- 不手工构造最终 descriptor、checksum、scene atlas 或 actor card。
- 不伪造 source receipt、dependency checksum、actor identity 或 scene key。
- 单本书的解释和修复留在该草稿的证据与审计记录中。
- Campaign 状态、权限、进度、知识、随机流、快照和分支不可进入可移植 Pack。
- 私有和商业来源默认只保留在本地，除非用户明确授权合法分发。

## 验证

~~~powershell
python scripts/validate_skill.py .
~~~

本仓库使用 Apache License 2.0；来源模组和生成的 Pack 保留各自的许可证与
分发限制。
