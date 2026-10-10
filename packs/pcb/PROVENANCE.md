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

## Bench surface alignment (2026-10-10)

决定（维护方拍板）：本 Pack 的声明层与 PCB-bench 的模型侧 Skill + 工具面保持一致。

- 上游 pin 前移：commit `f64692bc…` → `8c35250e32392f75920570e9e34839b06ad401c4`（runtime release `requirement-copper-20261008-r3`）。快照落盘
  `runtime/bench-upstream.json`：112 个上游文件 SHA-256（legacy 为 110；新增 `pcb-agent/tools/copper_resistance.py`、`task_obligations.py`）、
  `toolSchemaSha256`（`c8d4bf0a…`，与上游 `config/runtime-current.json` 一致）、镜像 pin（`sha256:2d404978…` / `pcb-feedback:20261008-coverage-copper-r3`）、
  KiCad 版本三元组与 89 个会话工具清单（88 canonical + `run_python`）。
- Skill 分发范围变更：`skills/pcb-design-e2e` 由 14 行公共改写版替换为上游 11 文件原版（SKILL.md + 9 references + 1 asset），
  作为 Pack 资源分发。原公共改写版移至新 Skill `pcb-kicad-native`，继续服务 macOS 本地有界编辑 profile。
  上游工具实现（`pcb-agent/**`）与评测器仍然只在已授权的外部 checkout 加载，不随包分发；分发许可依据维护方 2026-10-10 决定，
  针对自有私有仓库内部分发范围。
- 声明层：`harness-pack.json` 注册 89 个 `pcb.bench.*` 工具（名称/摘要取自上游 docstring，风险与 verification 沿用 legacy 清单）
  与 7 个 stage capability（core/components/schematic/pcb/verification/delivery/operate）。
- 执行层分两步：本变更（PR1）只注册占位 descriptor，调用时抛出带前置条件说明的 setup 错误；
  容器网关接线（bridge 通电、docker 派发、r10 冒烟）在后续 PR 完成。工具 schema 不在本包复制，
  运行期由网关对活体服务指纹校验（`toolSchemaSha256`）。

## 容器派发接线（2026-10-10，PR2）

- `runtime/bench-gateway.cjs`：每个绑定的 project 维持一个网关进程（`bridge/pcb-gateway.py`，MCP stdio），
  网关进程持有 pinned Linux 容器的生命周期；本模块只做 JSON-RPC，不直接调用 docker。
  调用串行化（上游 controller 对 /workspace 持排他锁）、空闲 15 分钟回收、分页响应在 256 KiB
  预算内重组为单一观测文档。
- bridge 消费快照形状的 provider 视图（`bridgeProvider`）：`sourceFiles`/`resourceRoots`/`directoryEnv`/
  `pythonEnv`/`mcpVersion(1.29.1)`/`allowedToolIds(89)` 取自 `runtime/bench-upstream.json` 与包内锁，
  不复用 manifest provider 的 runtime 盘点。policy.tools 必须恰好 89 条——网关启动时与容器活体工具集
  按名比对，多一条即拒绝。
- mutating bench 工具声明 `effect:inputs`（容器内工具直接改写挂载的工程），verification 统一为
  `pcb.bench.receipts.v1`（not_run：receipts 在容器会话内，执行不等于验收）。
- 前置校验先于任何网关启动：授权 checkout、Docker 镜像、网关 venv；缺一即抛带指引的 setup 错误。
  环境旋钮：`INDUSTRIAL_HARNESS_PCB_DOCKER`、`_REQUIREMENTS`（须在可写工程之外）、
  `_DEV_IMAGE_ID`、`_IMAGE_INPUT=1`（投递 view_design 渲染）。

## Actor 源码入包（2026-10-10，chip 式集成，用户拍板）

- 决定：pack 对标 chip 集成模式——actor 源码（`pcb-agent/**`，101 个文件，2.0 MiB）随包分发，
  运行时容器镜像由包内源码构建（`Dockerfile.local-dev`，构建上下文即 pack 目录）。
  pack 自包含；`INDUSTRIAL_HARNESS_PCB_BENCH_DIR` 降级为可选覆盖（评测时对齐全量上游 checkout）。
- 快照 `runtime/bench-upstream.json` 的 112 个哈希现同时承担 vendored 文件的完整性校验
  （bridge 启动时逐文件核对包内资源）；分发范围仍为自有私有仓库内部分发（见上方 2026-10-10 决定）。
- 派发自检：缺镜像/缺 venv 在任何进程启动前报出带构建指引的 setup 错误。
