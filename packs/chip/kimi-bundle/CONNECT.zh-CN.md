# 接入已安装的 Kimi Code CLI

[English](CONNECT.md)

Linux amd64 上已经安装了官方**原生 Kimi Code 2.1.1**，可以通过独立接入脚本添加
Chip Domain Pack：

```sh
wget -O connect-kimi-chip.sh https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/kimi-chip-v0.1.0-preview.1/connect-kimi-chip.sh
bash connect-kimi-chip.sh
```

接入会保留当前 Kimi 可执行文件、模型配置、登录凭据、会话历史和自动更新设置。
脚本安装私有 Python 和 Chip 运行时，将 `chip` 服务合并到原生 `mcp.json`，并添加
原生 `chip-design` Skill。它与[独立安装包](README.zh-CN.md)复用同一个固定校验和的
发行归档；归档虽然包含官方 Kimi，但接入脚本不会执行其中的 Kimi，也不会替换已有 CLI。

这个版本只验收了原生 Kimi Code **2.1.1**。接入脚本会检查已有 CLI，拒绝其他版本，
包括配置布局不同的旧 Python CLI，不会自行升级 Kimi。接入后如果官方 CLI 更新，
用于固定环境的轨迹采集前也需要重新验收该版本。

## 继续使用已有命令和数据目录

默认接入官方原生 CLI 的 **`~/.kimi-code`**。如果平时通过 `KIMI_CODE_HOME` 指定目录，
接入和后续启动也要使用同一变量。这里与独立安装包的隔离目录不同。

```sh
# CLI 不在 PATH 时，直接选择已有可执行文件。
bash connect-kimi-chip.sh --kimi-bin /absolute/path/to/kimi

# 复用自定义原生目录和已安装的完整 Chip 镜像。
KIMI_CODE_HOME=/absolute/path/to/native-home \
  bash connect-kimi-chip.sh --image YOUR_EXISTING_CHIP_IMAGE

# 只注册 MCP 和 Skill，暂不准备 EDA 镜像。
bash connect-kimi-chip.sh --skip-image
```

请使用拥有现有 Kimi 安装的用户运行。已有命令如果叫 `kimicode`，可以传
`--kimi-bin /absolute/path/to/kimicode`，但仍需是已验收的原生版本。
`--prefix /absolute/directory` 指定私有运行时目录。重复接入保留其他 MCP 服务和用户
设置；发现被修改或不属于本脚本管理的 `chip` 条目、Skill 资源时会报告冲突，不会覆盖。
接入收据 `$KIMI_CODE_HOME/chip-pack-connect.json` 记录源代码版本、接入时观测到的
原生 CLI 版本、镜像标识和受管理资源的哈希。

在支持的 Ubuntu/Debian 主机上，公开入口复用独立安装包的主机准备逻辑，补齐缺少的
基础工具和 Docker。默认构建固定配方的完整 EDA 镜像；传 `--image` 则检查已有镜像。
`--skip-image` 仅接通 MCP 和 Skill，不代表已经具备 EDA 执行环境。如果新增 Docker
组权限需要临时桥接，只通过 Chip MCP 自身的环境变量传递辅助程序。后续仍直接运行
原有 Kimi 命令。

## 在原生 CLI 中检查和使用

重启 Kimi 并创建新会话：通过配置文件添加的 MCP 不会注册进已经打开的会话。
在交互 CLI 输入：

```text
/mcp
/skill:chip-design
```

`/mcp` 应显示 `chip` 已连接；Skill 会说明直接调用的原生 Chip MCP 工具和 EDA 流程。
项目内 `.kimi-code/mcp.json` 中同名的 `chip` 会覆盖用户级条目。如果使用
`--skills-dir`，该参数会替换原生 Skill 自动发现目录，需要显式包含原生 home 的
Skill 目录。

配置好原有模型后，在工程目录直接运行：

```sh
kimi -p '使用 chip-design 检查当前工程，运行所需 RTL 验证，并报告实际证据。' --output-format stream-json
kimi --continue -p '从已有工程和证据继续。' --output-format stream-json
kimi session list --json
kimi export SESSION_ID -o chip-trajectory.zip --no-include-global-log
```

如果选择了其他路径，将 `kimi` 替换为相应可执行文件。`-p` 是原生自动批量模式，不能
与 `--auto` 或 `--yolo` 组合。会话记录和导出均由 Kimi 原生管理。接入不增加 Agent
loop、任务总时限或步数限制，也不修改原生重复调用检测和工具、资源级截止时间。
采集训练数据时，将工程及其 `.eda` 证据与会话导出、接入收据一起保留。

接入包不包含模型凭据、客户工程、PDK 或预先生成的工程结论。这个独立采集入口使用
Pack 原始 MCP；接入 Harness 的受保护动作仍由 Harness Runtime 管理。
