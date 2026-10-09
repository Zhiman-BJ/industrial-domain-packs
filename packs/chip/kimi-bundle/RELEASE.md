# Kimi Chip 0.1.0-preview.1

2026-10-09

用于蒸馏轨迹采集的独立 Chip Pack 衍生发行：固定、未经修改的官方 Kimi Code
CLI 2.1.1，直接连接 Chip 0.6.1 的 25 工具 MCP 和原生 `chip-design` Skill。
维护和发布全部在 Domain Packs，不依赖 Harness 核心。

已有原生 Kimi Code 2.1.1 的 Linux amd64 用户可使用 `connect-kimi-chip.sh`，
在现有原生目录合并 Chip MCP 与 Skill，保留 CLI、模型凭据和会话。
新环境使用 `install-kimi-chip.sh` 安装独立的固定版本入口。

包内包含 Linux x86_64 原生 CLI、Python 3.13.16 和带哈希锁的 MCP 依赖，无需
另装 Node／Python。安装入口准备系统依赖与 Pack 的 Docker 工具镜像，也支持
`--image` 复用环境或显式 `--skip-image`。模型凭据与工艺／PDK 由使用者配置。

批量运行、续聊、审批、上下文压缩和 ZIP 导出均使用上游原生行为。保留原生
`agents/*/wire.jsonl`；不增加总时长／步数限制，不改写轨迹。用上游支持的设置
关闭自动升级，安装记录保留二进制哈希、Pack commit 与实际镜像 ID。

发布门禁检查原生二进制身份、实际安装／重复安装、标准 MCP 25 工具、Skill
加载、真实 RTL 仿真成功与断言失败、取消与恢复、原生续聊和轨迹 ZIP，
并对独立安装和已有 CLI 接入分别检查。受控本地模型协议
用于验证接线，不宣称模型设计能力或工艺签核。只有该门禁通过才发布。

```bash
wget -O connect-kimi-chip.sh https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/kimi-chip-v0.1.0-preview.1/connect-kimi-chip.sh
bash connect-kimi-chip.sh
```

新环境：

```bash
wget -O install-kimi-chip.sh https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/kimi-chip-v0.1.0-preview.1/install-kimi-chip.sh
bash install-kimi-chip.sh
~/.local/bin/kimi-chip login
```

Standalone Chip Pack derivative for trajectory collection. Ships the pristine
official Kimi Code CLI 2.1.1, Chip 0.6.1 direct MCP and native Skill, Python 3.13.16
and locked dependencies. Existing native Kimi 2.1.1 users can connect the Pack
without replacing their CLI, model credentials or sessions. Uses upstream sessions, batch mode and ZIP export with
no extra agent orchestration. Linux amd64 only. Publication requires the native
integration gate above; it does not establish model capability or foundry signoff.

See the [English guide](https://github.com/Zhiman-BJ/industrial-domain-packs/blob/kimi-chip-v0.1.0-preview.1/packs/chip/kimi-bundle/README.md) / [中文说明](https://github.com/Zhiman-BJ/industrial-domain-packs/blob/kimi-chip-v0.1.0-preview.1/packs/chip/kimi-bundle/README.zh-CN.md).
