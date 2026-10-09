# Kimi Code + 芯片 Domain Pack

[English](README.md)

这是 Chip Pack 用于采集蒸馏轨迹的独立衍生发行。包内直接使用**未经修改的官方
Kimi Code CLI 2.1.1 可执行文件**，配上芯片领域的 25 工具 stdio MCP、原生
`chip-design` Skill、Python 3.13.16 和带哈希锁的依赖。默认安装器准备 Pack 的
完整 Docker 工具配方。运行不依赖 Industrial Agent Harness 应用或编排代码。

## Linux x86_64 安装

```bash
wget -O install-kimi-chip.sh https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/kimi-chip-v0.1.0-preview.1/install-kimi-chip.sh
bash install-kimi-chip.sh
~/.local/bin/kimi-chip login
cd /absolute/path/to/project
~/.local/bin/kimi-chip
```

在支持的 Ubuntu／Debian systemd 主机上，入口脚本会准备缺失的 curl、git、ripgrep
和 Docker Engine；仅系统准备需要 sudo。已有且可访问的 Docker 直接复用。首次
完整工具镜像构建需要下载较大的上游镜像。模型凭据、任务输入及工艺／PDK 许可
由使用者提供，主机交互式查看器可选。

`--image EXISTING_IMAGE` 复用并检查已经准备好的完整 EDA 镜像。
`--skip-image` 明确只装 CLI／MCP，**不代表已准备原生 EDA 执行环境**。
用 `--prefix /absolute/bundle` 与 `--bin-dir /absolute/bin` 指定安装位置。
独立采集 worker 在安装和运行时设置 `KIMI_CODE_HOME=/absolute/data`。
其他发行版先准备 Docker、bash、git、ripgrep、curl、tar、flock，再执行压缩包内
的 `install.sh`。

压缩包已经包含 Kimi、Python 和 MCP 库，安装它不再下载模型、npm 包或 Python
依赖。准备 Docker 工具镜像仍需要联网，除非用 `--image` 指定已有镜像。
工具链就绪不代表已经具备某项工艺的授权 PDK。

## 采集与导出原生轨迹

所有运行参数直接传给原生 CLI：

```bash
cd /absolute/path/to/project
kimi-chip --auto -p "$(cat /absolute/task.txt)" --output-format stream-json > turn.jsonl
kimi-chip session list --json
kimi-chip --continue -p "Continue the same task" --output-format stream-json > continuation.jsonl
kimi-chip export SESSION_ID -o trajectory.zip --no-include-global-log
```

`--auto` 是上游的 Never Ask 模式，无人值守采集时显式选用；否则沿用原生审批。
包本身不加单轮时长或步数上限，上游批量模式默认无上限。项目动作的资源限制和
单个 MCP 请求超时仍生效；长 EDA 任务提交后按 run ID 查询，不让一个 MCP 请求
等待整个计算结束。

默认原生数据目录为 `~/.local/share/kimi-chip`：

- `config.toml`：上游标准模型配置，可原生登录，也可配置 OpenAI 兼容地址和
  `api_key_env`；安装器不写入密钥。
- `mcp.json`：直接启动 `chip` stdio MCP。
- `skills/chip-design/`：领域 Skill 和实际安装的镜像身份。
- `sessions/<workDirKey>/<sessionId>/agents/*/wire.jsonl`：原生会话事件流，含
  请求轨迹与工具列表。
- `chip-bundle-install.json`：固定 Pack commit、上游二进制哈希和镜像 ID。

原生导出 ZIP 保留会话目录。`stream-json` 是实时输出，源轨迹以原生会话／导出
为准。本包不改写消息、拦截工具返回或合成思考记录。数据集同时保存安装记录、
原始任务和工程配置，便于追溯内核、工具和输入版本。通过上游支持的环境变量
关闭自动升级；需要升级时明确更换固定发行包。显式执行原生 `upgrade` 会离开
该固定版本。

项目的 `.kimi-code/skills`、`.agents/skills` 保持原生发现方式；上游也会读取
用户的 `~/.agents/skills`，采集 worker 应明确管理这些目录。入口不注入自定义
Agent profile 或系统提示词。

## 边界与维护

全部维护在 Domain Packs 的 `packs/chip/kimi-bundle/`。`build.py` 只打包已提交、
干净的源码和哈希固定的上游产物；`configure.py` 只写标准 MCP／Skill 配置。
入口最后直接 `exec upstream/kimi "$@"`，不引入 Agent loop、Broker、TaskService、
canonical 记录、额外审批或执行代理。Harness 与远端服务继续各自已有的 Pack 接入。

此预览针对原生 Linux amd64。安装检查连接和工具清单，工程验收仍由真实任务
配置的验证决定。EDA 保留原生 state／run／artifact；中断 Kimi 不一定取消已经
提交的异步 EDA run，需要时显式调用 Pack 的取消工具。此包是轨迹采集发行，
不构成工艺签核资格。

Linux 构建需要 Python >=3.12、curl、git 和 tar：

```bash
python3 packs/chip/kimi-bundle/build.py
```

保留 Kimi、Python、Chip 及依赖许可。原生工具和 PDK 各自遵循其许可。发行包
不包含内部仓库文档、凭据、客户工程和历史运行。
