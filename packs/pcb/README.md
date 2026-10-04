# PCB Bench MCP / Skill bridge

固定的外部后端是 `Zhiman-BJ/PCB-bench@f64692bcfdd74ac6d95051df1adac598cf21105a`。
注册 88 个命名 PCB 工具和一个受限 Python 工具；共享 Registry 使用 `pcb.bench.*` canonical ID。
设计 Skill ID 是 `pcb.design.e2e`，会话加载完整 `pcb-design-e2e`，包括 9 个 references 和 constraints asset。
`domain_tool_call` 接受 `arguments` 对象；若模型的嵌套工具参数把布尔值或数组错误序列化，可改用
`argumentsJson` 字符串提交严格 JSON 对象。两种输入互斥，解析后仍按原生 schema 与 Broker Scope 校验。

此目录只提供本仓库自己的桥接环境与安装说明，不携带上游私有源码、Skill 正文、任务或参考答案。
来源与再分发范围见 [PROVENANCE.md](PROVENANCE.md)；接入范围及验证见 [PCB MCP 接入](../../doc/pcb-mcp-integration.md)。

## 准备

在用户有权访问的固定 PCB-bench checkout 外，准备本目录的 Python 3.13 gateway 环境：

```sh
cd domain-packs/pcb
uv sync --frozen --no-dev --python 3.13
export INDUSTRIAL_HARNESS_PCB_BENCH_DIR=/absolute/PCB-bench
```

checkout 的 `pcb-agent` 与 `skills/pcb-design-e2e` 共 110 个源/资源文件都须匹配注册摘要。
缺失、修改、额外文件或 symlink 明确拒绝；`__pycache__`/`.pyc` 不属于源文件。
不自动 clone、下载资源或修改用户全局 Kimi 配置。

原生执行要求 Docker 中已具备镜像：

```text
sha256:fcf2cc3e465e0314049a7ce1a6dc16605113af8a515fcec2c22e1d8a5a61ecb2
```

这是上游记录的 Docker image ID，**不是 registry 下载地址**。桥接使用 `--pull never`；
不会下载或用另一个镜像静默代替它。须通过已授权的镜像分发方式准备该镜像。
当前 recipe/锁声明 KiCad CLI、pcbnew 10.0.6 与四个 KiCad/CAD 库发行包 `10.0.6~ubuntu24.04.1`。

可选应用环境变量：

- `INDUSTRIAL_HARNESS_PCB_GATEWAY_PYTHON`：绝对路径，替换 gateway Python；必须有固定 MCP SDK 1.29.1。
- `INDUSTRIAL_HARNESS_PCB_DOCKER`：绝对 Docker executable 路径；默认从 PATH 查找 `docker`。
- `INDUSTRIAL_HARNESS_PCB_REQUIREMENTS`：候选工程之外的不可变公开需求文件；固定内容 SHA-256 后只读挂载。

这些是可信应用启动环境，不从 Project 文件或模型参数接受启动命令。
原生 KiCad/solver Python 使用镜像中的系统环境；gateway 的 Python/MCP 依赖不覆盖它。

## 本地开发镜像（诊断用途）

若拿不到上述固定镜像，可以用有权访问的固定 PCB-bench checkout 作为 build context，
在本机制作一个含 KiCad 10.0.6、系统 Python、ngspice、Java 25 与固定 FreeRouting JAR 的开发镜像：

```sh
docker build --platform linux/amd64 \
  -f /absolute/industrial-agent-harness/domain-packs/pcb/Dockerfile.local-dev \
  -t pcb-bench-local-dev /absolute/PCB-bench
export INDUSTRIAL_HARNESS_PCB_DEV_IMAGE_ID="$(docker image inspect pcb-bench-local-dev --format '{{.Id}}')"
```

只接受不可变的 `sha256:` image ID，不接受浮动 tag；它会写入候选工程的运行时身份记录。
原有源码摘要、89 个工具 schema、KiCad CLI/pcbnew 和四个 KiCad 包版本检查仍在容器启动时执行。
该镜像没有上游固定镜像的完整原生回归和实验资格凭据，不能替代正式 PCB-bench 评分镜像。
Mac 上运行 `linux/amd64` 采用仿真，首次并行冷启动可能较慢。
若本机网络代理使容器无法验证 Ubuntu/Launchpad 的 TLS 证书，可仅在本地诊断构建时传入
`--build-arg PCB_DEV_INSECURE_APT_TLS=1`；APT 包索引签名与 FreeRouting SHA-256 仍会校验。

## 使用

```sh
node apps/cli/src/main.cjs run --project-dir /absolute/candidate --domain pcb --task 'pcb mcp 检查工程状态' --scope-only
node apps/cli/src/main.cjs run --project-dir /absolute/candidate --domain pcb --task 'pcb mcp 检查工程状态' --approval approve
node apps/cli/src/main.cjs run --project-dir /absolute/candidate --domain pcb --task 'pcb mcp 检查工程状态' --disable-mcp pcb-bench.tools --disable-skill pcb.design.e2e --scope-only
```

真实 Agent 还需原有模型配置、API key、固定 Kimi CLI 与审批策略。
使用专用 **candidate 工程目录**：上游 Runtime 在容器里分配 controller/tool ownership，
初始化 `.pcb` 与 controller-owned `session`，保护动作记录，并修改候选文件的权限/ownership。
不把整个 bench checkout、开发仓库或凭据目录作为 candidate。
工具使用上游单板 `board.kicad_sch`/`board.kicad_pcb`/`spec.json` 约定；本次不自动迁移任意文件名、多板或旧项目。
