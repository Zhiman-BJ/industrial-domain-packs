# EDA Harness：工具选择与调用指南

使用持久化目标、设计状态和验证证据推进 EDA 任务。先恢复上下文，再检查配置和执行作业，最后检查当前工作区的验收结果。

## 选择下一步

| 情况 | 调用与后续动作 |
|---|---|
| 新会话或上下文丢失 | 调用 `get_operational_context`，读取目标、当前状态、活动运行和阻塞诊断；有活动运行时先查询，不要重复提交 |
| 不清楚可执行什么 | 用 `list_actions` 获取项目 action ID，用 `tool_capabilities` 获取 EDA 操作、后端及参数 Schema |
| 修改了 RTL、约束或规则 | 调用 `workspace_status` 检查过期结果，再对目标调用 `preflight` |
| 需要执行完整依赖链 | 调用 `run_until`，保存返回的 run_id，再用 `get_run` 查询 |
| 只需执行一个 action，且依赖已有有效结果 | 调用 `run_action`，再用 `get_run` 查询 |
| 执行结束 | 分别检查执行状态与验收结果；用诊断定位阻塞，用指标和产物核对证据 |
| 需要比较实验 | 用 `compare_states` 比较指标，再用 `record_decision` 保存实验事实 |

## 调用约定

- 未声明的顶层参数会被拒绝。`get_artifacts` 按 state_id/type 查询，不支持 run_id 参数。
- MCP `tools/list` 是可调用工具和输入 Schema 的依据。签名中的 `None` 参数可省略或传 `null`；其他必填参数按 Schema 提供。
- `get_tool_guide(tool=None)` 返回本指南、可用工具清单和只读模式标识。指定 tool 时附带该工具的实际描述与 inputSchema；不可用工具会报错。
- 读取 `eda://manual/tools` 可获取同一份指南。手册、`check_environment`、`tool_capabilities` 与 `viewer_capabilities` 无需项目配置；其他业务调用通过显式 project_path 定位项目（initialize_project 用于创建配置）。
- 将 EDA 操作参数写入项目配置，使其进入输入快照。使用 `tool_capabilities` 提供的参数 Schema；不要将这些参数直接传给 `run_action`。
- 使用 `list_actions` 返回的 action ID。`run_until` 也支持 `post_route_sta` 和 `final_verify` 目标。
- 共 25 个工具；只读模式移除下表中 9 个修改接口，保留 16 个查询接口。不要尝试调用被移除的工具；MCP 只读模式不约束客户端自己的 Shell/Edit 权限。

## 工具速查

### 发现能力与恢复上下文

| 接口及参数 | 用途与返回含义 | 产生写入或启动副作用 |
|---|---|---|
| `get_tool_guide(tool=None)` | 获取本指南、可用工具和指定工具的实时输入 Schema | 否 |
| `tool_capabilities()` | 返回结构化 EDA operation、后端、参数 Schema 和规则契约 | 否 |
| `inspect_project()` | 等同 `get_operational_context("brief")`，返回项目、工作区、目标、运行和验收摘要 | 否 |
| `get_operational_context(detail="standard")` | 恢复持久化上下文；detail 支持 brief / standard / deep，详见下文 | 否 |
| `workspace_status()` | 对当前输入计算哈希，返回工作区状态、有效结果与下游过期情况；不依赖编辑 Hook | 否 |
| `list_actions()` | 返回项目可用 action 列表、依赖及配置就绪信息 | 否 |

### 检查配置与执行作业

| 接口及参数 | 用途与返回含义 | 产生写入或启动副作用 |
|---|---|---|
| `get_server_info()` | 返回当前 MCP 会话的版本、提交证据、进程信息和生效路径；无需项目 | 否 |

| `check_environment(target=None, image=None, probe_capabilities=false, project_path=None)` | 检查环境就绪，返回分项状态、故障代码和修复建议；无需项目配置。指定目标才检查输入和所需工具；查看器可选，不证明验收通过 | 否 |
| `initialize_project(top, name?, image?, local=false)` | 默认检查 Docker/镜像后创建项目配置；本机查看器可选 | 是 |

| `preflight(target)` | 检查输入引用、依赖、后端支持与 runtime 可用性；不提交作业，也不保证任意 PDK 可用 | 否 |
| `run_action(action, force=False)` | 冻结输入并异步提交一个 action；要求依赖已有有效结果；返回 `{run_id, status}` | 是 |
| `run_until(target, force=False)` | 使用同一冻结快照提交目标依赖 DAG；返回 `{run_id, status}` | 是 |
| `get_run(run_id)` | 返回运行记录，包括执行状态、设计状态、步骤和证据 | 否 |
| `cancel_run(run_id)` | 请求取消；worker 终止进程/容器并保留历史 | 是 |
| `recover_runs()` | 将 worker 已不存在的运行标记失败，返回 recovered；不重新连接或续跑远端作业 | 是 |

### 读取证据与比较结果

| 接口及参数 | 用途与返回含义 | 产生写入或启动副作用 |
|---|---|---|
| `get_diagnostics(state_id=None, category=None)` | 返回诊断列表，可按状态和类别筛选 | 否 |
| `get_metrics(state_id=None)` | 返回带单位和证据的归一化指标列表 | 否 |
| `get_artifacts(state_id=None, type=None)` | 返回按状态/类型筛选的内容寻址产物列表 | 否 |
| `read_artifact(artifact_id, offset=0, limit=16000)` | 分页读取产物，返回 artifact、offset、text、next_offset；二进制返回导出提示 | 否 |
| `compare_states(state_ids)` | 返回 `{states, metrics}`，比较状态间指标及单位兼容的差值 | 否 |

### 管理验收目标与实验

| 接口及参数 | 用途与返回含义 | 产生写入或启动副作用 |
|---|---|---|
| `create_goal(description, constraints, required_verification=None, baseline_state_id=None)` | 持久化验收目标、约束、必需验证项与基线 | 是 |
| `record_decision(base_state_id, result_state_id, change_summary, outcome)` | 记录实验事实；result_state_id 可为 null，outcome 为 KEEP / REJECT / INCONCLUSIVE | 是 |
| `checkout_state(state_id, force=False)` | 恢复状态对应源文件；脏工作区默认拒绝，force 时先保存恢复快照 | 是 |

### 本机产物展示

| 接口及参数 | 用途与返回含义 | 产生写入或启动副作用 |
|---|---|---|
| `viewer_capabilities()` | 检查 MCP 所在电脑上的查看器、格式、路径与可用性；无需项目配置 | 否 |
| `open_viewer(artifact_id, viewer=None, companion_ids=None, state_id=None, signals=None, time_range=None, technology=None, top=None, launch=True)` | 校验并导出产物副本，生成启动命令，可选启动本机 GUI | 是 |

## 恢复上下文

默认使用 `get_operational_context(detail="standard")`；只需摘要时使用 `inspect_project()`，它等同 brief。需要更多证据索引时使用 deep。

三个级别都返回 `project_id`、`current_state`、`stage`、`working_copy`、`active_goal`、`baseline_state`、`design_status`、`acceptance`、`active_runs`、`last_run`，以及阻塞/执行诊断。

| detail | 每类诊断摘要条数上限 | 额外内容 |
|---|---|---|
| brief | 3 | 基本上下文 |
| standard | 5 | 最近状态转换、实验记录、可用 action |
| deep | 15 | 同上，并附最多 50 个产物 |

## 提交与等待

`run_action` 和 `run_until` 返回 `{run_id, status}` 表示作业已提交，不表示验证通过。保存 run_id，按适合工具运行时长的间隔调用 `get_run`；会话关闭不会自动取消已提交运行。

`run_until` 使用同一冻结快照执行依赖 DAG；`run_action` 要求依赖已有有效结果。`force=True` 绕过缓存要求重跑，仅在确实需要时使用。

以下示例展示独立的 MCP tools/call 请求；查询运行时用提交响应中的真实 run_id 替换占位符：

```json
[
  {"name":"get_operational_context","arguments":{"detail":"standard"}},
  {"name":"preflight","arguments":{"target":"final_verify"}},
  {"name":"run_until","arguments":{"target":"final_verify","force":false}},
  {"name":"get_run","arguments":{"run_id":"<返回的 run_id>"}},
  {"name":"get_diagnostics","arguments":{}}
]
```

先检查每次响应，再决定是否继续；这不是不加判断的批量执行序列。

## 判断验收与保存实验

执行 `SUCCESS` 不等于验收 `PASS`。执行成功时设计仍可能为 `BLOCKED` 或 `INCOMPLETE`。依据当前工作区和当前目标的 acceptance 判断是否完成，不将历史状态的通过结果直接用于修改后的输入。

`create_goal` 的 constraints 形如 `{"timing.setup.wns":{"op":">=","value":0,"unit":"ns"}}`；required_verification 使用 action ID 列表。面积增长等相对指标要求选择已有对应测量值的 baseline。

用 `record_decision` 保存改动摘要、基准/结果状态和 KEEP / REJECT / INCONCLUSIVE 结论。记录实验事实，不记录私有推理。恢复源文件前检查工作区；`checkout_state(force=True)` 会保存恢复快照并覆盖工作区，只有确实要替换现有修改时才使用。

## 读取诊断和产物

先读取 `get_diagnostics` 的紧凑诊断，再按证据 ID 查询 `get_artifacts` 和 `read_artifact`。指标比较使用 `get_metrics` / `compare_states`，保留单位信息。

`read_artifact` 的 offset 和 limit 按字节计：offset ≥ 0，limit 为 1–64000，默认 16000。根据 next_offset 继续读取，null 表示结束。切片无法解码为 UTF-8 时返回二进制提示；跨多字节字符边界也可能触发该提示。完整产物可通过 CLI `export-artifact` 导出。

以下资源返回 JSON 文本：

| URI | 内容 |
|---|---|
| `eda://project/{project_path}/state/{state_id}` | 不可变 Design State |
| `eda://project/{project_path}/run/{run_id}` | 运行记录 |
| `eda://project/{project_path}/artifact/{artifact_id}` | 默认分页产物内容 |
| `eda://project/{project_path}/report/{artifact_id}` | 默认分页报告内容 |
| `eda://project/{project_path}/diagnostics/{state_id}` | 状态诊断列表 |

## 处理错误

| 情况 | 处理方式 |
|---|---|
| 缺少 eda.yaml | 仍可读取手册和能力；根据实际设计初始化配置后再调用业务工具 |
| 参数错误、未知 ID、缺少输入或依赖 | 按 Schema、list_actions 和 preflight 修正；不要把调用错误当成设计验证失败 |
| 作业仍在运行 | 继续查询 get_run；需要停止时调用 cancel_run |
| worker 丢失 | recover_runs 将失去 worker 的运行标记失败，再按需重新提交；它不恢复远端作业或断点续跑 |
| 验收 BLOCKED | 查询诊断及关联产物，针对失败证据修改设计或配置 |
| 验收 INCOMPLETE | 补齐缺失证据或不兼容单位，不宣称通过 |
| 脏工作区 checkout 被拒绝 | 先保存修改，再决定是否替换工作区 |
| 缺少 Docker、EDA 工具或 PDK | 先调用 `check_environment(target)`，按 next_step 补齐依赖；插件安装不包含工具镜像或 PDK |

MCP 调用错误通过 isError 表达；作业执行状态和设计验收状态需要分别检查。preflight 就绪也不保证任意工具版本、PDK 或设计都能执行成功。

## 打开波形、版图和报告

先用 `viewer_capabilities` 检查本机软件，再通过 `get_artifacts` 选择 artifact_id。查看器必须安装在 MCP 所在电脑上，Docker 中的工具可用性不能替代本机查看器检查。

| 查看器 | 主产物 | 配套输入 |
|---|---|---|
| Yosys show/viz | netlist.json 类型的 Yosys JSON | 项目 runtime 中的 Yosys/Graphviz；默认 yosys_show，yosys_viz 为数据流 |
| netlistsvg | netlist.json 类型的 Yosys JSON | top 选择模块；render_only=true 导出 SVG，无需打开浏览器 |
| GTKWave | VCD、FST | 可选被动 .gtkw 会话；signals 追加信号，time_range 使用波形时间单位 |
| KLayout | GDS、OAS | 无 |
| KLayout | .lyrdb、.lvsdb、.l2n | companion_ids 指定对应 GDS/OAS；Netgen 文本报告不能作为 LVS 数据库 |
| Magic | MAG、GDS | 显式 technology 工艺名或 .tech 路径；GDS 还需 top；层次 MAG 需配套子单元 |
| OpenROAD GUI | ODB、DEF | DEF 需按读取顺序提供 LEF；ODB 需与本机版本兼容 |

提供 state_id 时所有产物必须属于该状态；未提供时不会猜测配套产物来源。产物从 CAS 校验后导出到 .eda/viewers/，其副本修改不会改变设计状态或验收。

- `launch=False` 且未启用 render_only：只准备导出和命令，返回 PREPARED；仍有文件写入，不在只读模式提供。
- `VIEWER_UNAVAILABLE`：本机程序不可用，返回导出文件与安装提示。
- `LAUNCH_FAILED`：创建进程失败，查看 error。
- `LAUNCHED`：只确认进程已创建或浏览器接受打开请求，不证明窗口加载成功或验证通过；查看返回的 log 和查看器窗口。

通过 PATH 或 EDA_VIEWER_GTKWAVE、EDA_VIEWER_KLAYOUT、EDA_VIEWER_MAGIC、EDA_VIEWER_OPENROAD、EDA_VIEWER_NETLISTSVG 指定程序路径，配置变化后重启 MCP。GTKWave 不接受进程过滤器等主动会话记录；不保证信号名有效。Magic 不自动读取 .magicrc。OpenROAD 的额外 Liberty/SDC 交互配置尚不支持。

本接口不支持远程桌面投递、持续鼠标操作或 GUI 状态回读。文本报告继续使用 read_artifact。

## 初始化之前的环境检查

无 `eda.yaml` 时也先调用 `check_environment()`。这会检查 MCP 主机的 Docker 服务，并在
默认候选镜像 `eda-harness-tools:dev` 内运行版本清单；也可传 `image="your-image:tag"`。
候选值不代表已经配置的项目。不会自动拉取镜像、安装软件或创建 `.eda`/`eda.yaml`。

成功的 MCP 返回包含 `mcp_server.reachable=true`。`environment_ready` 表示候选 Docker
环境检查是否通过；`project_ready=false`、`PROJECT_MISSING` 和总体 `ready=false` 在初始化前
是预期结果，不等于镜像不可用。`tool_inventory.tools` 为镜像内的软件，`viewers` 是 MCP
主机可选桌面软件，后者缺失不影响前者。不要根据本机 `command -v yosys` 推断镜像状态，
也不要自行把 iverilog 列为必需项。

已有项目按 `eda.yaml` 和目标动作的 runtime 检查，不接受 image 覆盖；初始化后继续调用
`check_environment(target="...")` 检查目标输入和工具。环境检查不代表 PDK、流程或验收通过。
若 Claude 看不到此工具，应报告 MCP 连接/版本问题，不能将 Bash 本机扫描冒充 MCP 检查。
CLI 等价入口：`eda --project /path/to/empty-directory doctor --image eda-harness-tools:dev`。

## 新项目初始化顺序

1. 安装并连接插件/MCP，调用 `get_tool_guide` 确认服务可用；已连接时不需要另装全局 Harness。
2. 调用 `check_environment(image=...)` 检查 Docker 和候选镜像，默认 Docker 执行。
3. 调用 `initialize_project(top="chip", name="design", image="eda-harness-tools:dev")`。
   此工具在显式 project_path 创建 eda.yaml；会重新检查镜像，失败时返回 BLOCKED 且不创建
   配置。不会覆盖现有配置，也不允许直接在 home 目录初始化。切换项目只需在下一次调用中
   传入新的绝对 project_path，不需要重连 MCP。无需再次运行 uvx 获取初始化帮助。
4. 补充设计输入、所需 PDK、动作/流程配置，再调用 `check_environment(target=...)`。
5. 最后单独报告本机查看器；它们缺失只影响本机交互展示，不阻止镜像计算和初始化。

`initialize_project(local=true)` 只用于用户明确选择本机计算的情况，此时本机工具是执行依赖。
`INITIALIZED` 仅表示创建配置，`execution_ready=false` 提醒后续仍需补齐项目。
CLI `eda init --top chip` 使用相同逻辑和默认镜像；可用 `--image` 指定镜像，或显式 `--local`。
初始化工具不在只读 MCP 中提供，不自动安装软件、下载 PDK 或拉取镜像。

## 服务身份与重载排查

遇到“仍是旧服务”“没走 wrapper”或查看器路径不生效，先调用 `get_server_info()`。
它在无项目、只读 MCP 下也可用，报告当前会话 ID、包版本、Python/包路径、PID/父 PID、
项目根目录、查看器配置快照，以及实际可用工具。`source_commit` 只取安装元数据的 VCS
证据；源码/普通 wheel 安装可能为 null，不代表版本旧。launcher 路径和插件版本是启动
环境的声明，明确标记为未独立验证。不会返回整个环境、完整命令行或含凭据的源 URL。

`serve.sh` 使用 exec；进程名中没有 wrapper 不能证明未执行 wrapper。不能根据 PID 名称
断定版本，也不能把杀进程视为配置重载。先对比预期版本/项目/路径，运行 `/reload-plugins`
后检查 `/mcp` 与新身份；环境变量变化时从正确环境重新启动 Claude。仍不一致时检查手动
注册与插件注册是否重复。不要默认创建额外 wrapper、编辑插件缓存或杀进程。
会话快照不会因为磁盘上的脚本后来修改而冒充新配置。身份接口缺失时检查工具清单和连接
版本，不反复调用缺失接口。服务身份也不证明 Docker/PDK 就绪，应另调用 check_environment。


## 显式项目定位

插件启动的 MCP 没有默认项目，不采用 PWD、CLAUDE_PROJECT_DIR 或 EDA_PROJECT_ROOT。
服务级 get_server_info、get_tool_guide、tool_capabilities、viewer_capabilities 不依赖项目。
check_environment 不传 project_path 时只检查候选 Docker 环境；不会搜索启动目录。
所有项目工具均支持 project_path（绝对路径），例如：

```json
{"tool":"initialize_project","arguments":{"project_path":"/Users/Shared/design-a","top":"chip"}}
{"tool":"inspect_project","arguments":{"project_path":"/Users/Shared/design-a"}}
{"tool":"get_artifacts","arguments":{"project_path":"/Users/Shared/design-b"}}
```

每次调用独立定位，不维护可被并发调用覆盖的“当前项目”。未给路径返回 PROJECT_REQUIRED；
路径无 eda.yaml 返回 PROJECT_NOT_INITIALIZED 并附目录。先确认目录意图，不要在错误目录
自动创建项目。run/state/artifact ID 只在指定项目中解析，不能自动跨目录搜索。

资源使用 `eda://project/{project_path}/artifact/{artifact_id}` 等模板，project_path 是绝对
路径按 URL 编码后的单个路径段（斜杠编码为 %2F）。get_artifacts 返回项目限定的资源 URI。
CLI 项目命令仍可使用 --project；显式 `eda --project /absolute/project mcp` 可提供兼容默认值，
但每个调用的 project_path 可覆盖它。正式插件不设置此默认值。旧客户端的无路径项目调用
需要改用 project_path；服务身份会报告 default_project_path，空值表示没有默认项目。

## 工具级扩展操作

`tool_capabilities` 返回 28 种语义操作与完整参数 Schema。Yosys 支持 frontend、include、宏与整数顶层参数；新增 elaborate、check_netlist、prepare_lvs_reference。OpenROAD 新增 repair_design、repair_timing、repair_tie_fanout、repair_antennas、connect_power、generate_pdn、insert_tapcells、insert_fillers、export_design、check_constraints、check_design_rules。使用自定义 action 声明依赖，由 run_action/run_until 调用。

修复操作的完成不证明问题已消除，必须对修改后的设计重新布线/提取并运行所需检查。导出网表使用 netlist.physical，约束使用 constraints.sdc。STA 可按 corner 分配独立 action，具名角指标使用 corner.<name>.timing.*。原生 KLayout XML 规则使用 rule_format=macro 和显式变量，LVS 参考模型不能省略或猜测。

执行前可调用 check_environment(target=目标, probe_capabilities=true) 探测原生命令与辅助程序；默认检查不会执行此深度探测。工具存在不表示 PDK 兼容。



## 项目输入与参数引用

`tool_capabilities().project_schema` 描述 eda.yaml 的结构，`input_contract` 解释动态校验规则。`inputs` 的合法类别是当前 workflow 所有 action 的 `input_types` 的并集，再加 `config`、`script`；自定义类别需在 `workflow.actions[].input_types` 中声明。默认类别清单见 `input_contract.default_allowed_categories`，它不包含项目自定义扩展。

`list_actions` 的 `input_types` 可直接用作 `inputs.<category>`，`allowed_input_categories` 还包含共享的 config/script。例如 physical.streamout 返回 lef、gds、streamout，故这三个类别都合法，无需猜测或通过失败试探。JSON Schema 无法单独表达依赖当前 workflow 的合法类别集合，项目加载时还会进行语义校验。

必须先在 `inputs` 中用项目相对路径或 glob 捕获文件，再在 `parameters` 中用 `{path: ...}` 引用具体文件。只写 parameters 不会自动捕获文件，也不能绕过类别检查。上游产物使用 `{action: ..., artifact: ...}`，并声明生产者依赖，不必把生成路径放进 inputs。

下面是一份 streamout 配置示例；PDK 文件必须实际存在，默认上游 physical.route 仍需配置并运行。类别名不等于文件扩展名：LYT 文件归入 streamout，GDS 单元库归入 gds。

```yaml
name: streamout-example
top: chip
runtime:
  kind: docker
  image: eda-harness-tools:dev
inputs:
  lef: [pdk/tech.lef, pdk/cells.lef]
  gds: [pdk/cells.gds]
  streamout: [pdk/process.lyt]
actions:
  physical.streamout:
    parameters:
      operation: streamout
      top: chip
      layout: {action: physical.route, artifact: layout.def}
      technology: {path: pdk/process.lyt}
      lefs: [{path: pdk/tech.lef}, {path: pdk/cells.lef}]
      libraries: [{path: pdk/cells.gds}]
      dbu: 0.001
```

随后调用 `preflight(target="physical.streamout", project_path="项目绝对路径")` 检查具体文件引用、依赖和运行环境；它不证明流片或工艺验收通过。

## 网表图查看契约

默认选择 Yosys 原生 `yosys_show`；`yosys_viz` 提供数据流视图，`netlistsvg` 是可选的本机 Node.js 渲染器。调用 `get_artifacts(type="netlist.json", project_path=...)` 选择产物，再调用 `open_viewer(artifact_id=..., top="模块名", render_only=true, project_path=...)`。RENDERED 后 output 为 SVG，原生后端还有 dot_output；不传 render_only 则渲染后请求浏览器打开。launch=false 且 render_only=false 仅准备命令。

Yosys 后端使用项目 eda.yaml 的默认 runtime，不推断历史生产环境；Docker 中运行 Yosys/Graphviz，本机只需浏览器。local runtime 需要 PATH 中有 yosys 和 dot。viewer_capabilities 的这两个后端 available=null 表示需要结合项目探测，不代表缺失；open_viewer 会检查真实工具版本。旧镜像需要用本版本 Dockerfile.tools 重建，新增固定 Graphviz 2.42.2-6ubuntu0.1。禁止把仅列出工具当作环境就绪。

netlistsvg 1.0.2 需要 MCP 主机 Node.js 与 EDA_VIEWER_NETLISTSVG，安装锁定依赖的方法为完整源码目录下 `npm ci --prefix tools/netlist-viewer --ignore-scripts`。默认 Yosys 后端不需要 Node.js。不自动切换后端或安装依赖。

Verilog、普通 JSON 报告和 SPICE 不能直接作为 netlist.json 使用；Verilog 先用已有 Yosys elaborate/synthesize。top 未指定时仅接受唯一 top 标记或唯一模块，有歧义返回候选名。单文件上限 32 MiB、5000 cells/模块，渲染命令最多 60 秒；按模块导出大型设计。netlistsvg 不支持 inout；Yosys 后端不施加此限制。未降低 process/memory 的 JSON 仍需先处理。

每次绘制一个模块，子模块以块显示，可再次选子模块；不是交互式跨层级追踪。RENDER_FAILED 检查日志；LAUNCHED 不证明客户端看到了图。SVG、DOT、原始产物副本和执行记录保留在返回 directory，设计状态与验收不修改。远端 MCP 不自动投递到客户端浏览器。
