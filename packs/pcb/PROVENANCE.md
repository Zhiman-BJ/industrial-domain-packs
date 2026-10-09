# PCB bridge provenance

- 核对时间：2026-09-29。
- 外部来源：`https://github.com/Zhiman-BJ/PCB-bench`，commit `f64692bcfdd74ac6d95051df1adac598cf21105a`。
- 公开工具 registry：上游 `pcb-agent/tools/workspace.py` 和 `agent_session.py`；服务入口 `kimi_mcp.py`。
- 设计 Skill：上游 `skills/pcb-design-e2e`，11 个实际文件；维护 Skill、任务、评测器和参考答案不属于 actor 资源。
- 当前注册清单只保存工具名称/本仓库摘要、canonical ID、风险、来源身份及 110 个文件 SHA-256。
  工具 schema 从实际服务核对，不在公开包里复制上游实现或完整 schema 文本。
- 此 PR 的 gateway、launch adapter、安装说明、Skill 注册入口及受控测试 fixture 是本仓库实现。
  受控 fixture 不是原生 KiCad 或设计验证器。
- 上游是私有仓库，核对的根目录没有仓库级 LICENSE。这里不授予上游材料的再分发许可。
  本接入在用户已授权访问的外部 checkout 加载原始资源；发布产物不打包该 checkout。
  若后续要 vendor/公开分发原始工具或 Skill，需要分别确认相应许可和来源范围。
- KiCad、标准库、solver 与其各自许可证继续由上游镜像/版本锁负责；本桥接不会重新声明这些第三方许可证。

The public bridge, locks and metadata are now maintained here. See ../../provenance/domain-migration.json for exact imports. The private actor remains external; its registered hashes do not redistribute its source. Preserve ../../licenses/industrial-agent-harness.MIT.

## Public professional runtime contribution (2026-10-08)

The new runtime/, typed tools, Verifiers and examples are original public MIT contributions maintained in this repository. They use separately installed official native executables under their original licenses. The mechanical board and structural scene examples are self-authored source inputs, contain no customer data, private actor code, licensed artwork or third-party assets. Exact maintained source hashes are in content-lock.json and harness-pack.json. Legacy import provenance remains historical evidence; it does not describe the new public acceptance path. See ../../docs/professional-runtime.md for qualified scope and native dependency archive locks.
