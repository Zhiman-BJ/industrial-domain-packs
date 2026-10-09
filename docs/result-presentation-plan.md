# 全领域成果声明与扩展计划

2026-10-09。状态：**接入准备／声明草案，不是全领域已上线声明**。

本清单核对 owner `224798c16a7f53f6b4418078419ac248a336a201` 和 Harness `f8dff905de14890bbe4010d0215613e74aac3210`。当前 owner 有 **5 个 domain、6 个 Pack**：`chip`、`pcb`、`godot`、`cad`、`cuda`；`cad` 同时由 FreeCAD 和二维 CAD guidance 提供能力。Harness 当前固定的 owner `e6556e41cd34aa87b32d64391b48084ce7003f29` 只有前四个 domain，尚未消费 CUDA。

[机器可读声明样例](result-presentation-examples.json)与本页共同作为统一审阅入口。样例不是运行时配置，不会启用工具或自动预览；`file` 中的变量和全零 hash 都是占位符。实现时仍由各 Pack 的真实生产路径使用 Harness `ToolPresentationSchema` 输出动态声明，不复制一套契约，也不把领域规则放进 Harness。

## 1. 共同声明规则

沿用已有 presentation schema `'1'`：`groups[].primary` 为主产物局部标识，`preview` 为可选只读预览入口，`attachments` 为实际导出／报告，`companions` 为读取主成果所需的文件。`supersedesInput` 指向明确被替代的输入版本，`checks` 指向实际受检输入。项目、请求、Action 和正式 Artifact ID 均由 Harness 绑定。

- 工具首先列出确实产生、可读且内容身份确定的 `artifacts`，每份输出使用唯一 `localId`；声明只能引用本次真实输出。
- 主成果由领域操作语义决定，不按最后文件、标题、后缀或目录扫描排序。文件后缀只用于 Viewer 格式匹配。
- 日志、诊断、性能报告不自动替换设计／场景／候选源码。显式用户选择可突出任一已登记成果。
- 失败／取消保留真实部分输出，不声明成功替代；缺少验证显示 `not_run` 或实际证据状态，不根据退出码补成功。
- 动态生成声明时过滤未产生的可选文件。样例展示完整成功路径，不要求失败路径制造占位文件。
- `preview` 只提供候选。Viewer Registry、插件显式 opt-in、内容校验和前台一次性策略继续独立决定是否自动打开。
- 全领域接入包含每个现有 domain 的真实生产链路；旧 EDA/MCP 的宽工具面逐操作迁移，不能据此宣称所有操作或新平台已验收。

## 2. 按领域的声明

| Domain / Pack | 主成果及预览入口 | 附件与检查 | 当前状态 |
| --- | --- | --- | --- |
| CAD / FreeCAD | `model.FCStd`；现有 Viewer 根据准确清单读取 BREP | STEP/STL、build/readback、日志；BREP/preview/recipe/sketch 为 companions | 已实现，保持现有生产行为 |
| CAD / 二维图纸 | 实际交付的 DXF 或 DWG 快照；初期不声明不存在的图形预览 | 实际 PDF 导出、规格、回读／差异报告；全部条件存在 | Skill-only，需增加受控生产／读回入口 |
| PCB / KiCad | 编辑结果 `work/board.kicad_pcb` 快照，使用现有 KiCad Viewer | before/input manifest、请求、日志；验证使用 DRC/readback 报告 | 待增加 localId、声明及版本关联 |
| Godot | 编辑后的 `after.tscn` 快照，使用现有只读场景结构 Viewer | before、准确资源快照；验证使用 readback/frames 及 native 日志 | 待增加 localId、声明及版本关联；不冒充 Web 游戏导出 |
| Chip / RTL | 真实仿真波形；无波形时只显示实际报告／日志 | simulation report、native completion、工具日志 | canonical RTL 路径待接；完整 EDA 分阶段迁移 |
| CUDA | 候选源码清单及准确源码快照，使用文档／源码查看 | compile/verification/profile receipt、取消证据；baseline 独立 | 当前只有 receipt；需补源码快照及 ticket 关联 |

### CAD：FreeCAD

生产声明在 [`packs/freecad/runtime/presentation.cjs`](../packs/freecad/runtime/presentation.cjs)，由 [`runtime/index.cjs`](../packs/freecad/runtime/index.cjs) 的 build/edit/inspect/export 返回路径调用。

- build/edit/export：`primary = model.FCStd`；成功且预览清单存在时 `preview = model.FCStd`。STEP/STL 和报告属于 attachments；BREP、清单、草图、recipe 属于 companions。
- edit：仅在成功时，按原模型的真实路径和 hash 声明 `supersedesInput`。失败的局部产物不替代上一成功版本。
- inspect：报告为单独组；`checks` 指向实际模型输入，报告不会成为模型的新版本。
- 保留独立报告组，宿主可以按显式附件关系折叠；选中报告时仍能提升展示。
- 现有 owner 3 项声明单测及 Harness 原生／桌面验收继续作为回归。样例 `freecad-build`、`freecad-edit`、`freecad-inspect`。

### CAD：二维 DXF／AutoCAD DWG

当前 [`packs/cad/pack.json`](../packs/cad/pack.json) 明确为 `skill-only`，无 Runtime provider。Skill 的规格→图纸→回读→交付指导不能自动登记 GUI/Shell 产生的文件。

- DXF 路线：主成果为受控工具确实产出的 DXF 快照；PDF、规格与差异报告仅在本次确实产生时作为附件。图形 preview 暂留空，原生文件与源文本入口仍可用。
- DWG 路线：主成果为确实保存的 DWG 快照；DXF/PDF 等导出需显式生产关系，不凭同名配对。没有受支持的只读 DWG Viewer 时只提供文件入口。
- 待实现的领域入口必须经 Runtime 捕获输入、输出、原生回读和失败。不得新增“Agent 任意给路径就登记为成功成果”的收尾工具。
- 如果先接入只读导入／检查，标题与状态必须说明这是已存在图纸的快照；不能追认之前 GUI/Shell 编辑为一条已受保护的工业修改。
- 工具 ID、边界化输入及独立验证需在实施时明确；本草案不虚构现有 `cad.dxf.*` 工具。样例 `cad-dxf-delivery`、`cad-dwg-delivery`。

### PCB：KiCad

当前 [`packs/pcb/runtime/index.cjs`](../packs/pcb/runtime/index.cjs) 只有 `pcb.kicad.edit`／`pcb.kicad.verify`，限定矩形边界与现有封装移动。

- edit：主成果来自 `work/board.kicad_pcb`，不是运行后再次读取可变工程文件；把 before 快照、`inputs.json`、`work/request.json`、`edit.json`／`edit.log` 作为附件。编辑结果保持现有 `not_run`，DRC 需独立运行。
- verify：以 `work/drc.json` 形成检查报告组，附 `work/readback.json`、native receipts/logs，按真实受检板的路径和 hash 关联模型版本。
- 本次没有生产原理图、Gerber、钻孔、STEP 等文件，声明中不补这些导出。未来对应工具产生它们后再扩展。
- 需要给 `support.outputs()` 的实际输出赋稳定局部标识，并补充下节的“快照与工程路径”关联；不按文件名猜相同板版本。
- 现有 KiCad Viewer 初期保留手动入口，真实文件→Registry→Viewer 验收后才启用自动预览。样例 `pcb-edit`、`pcb-verify`。

### Godot

当前 [`packs/godot/runtime/index.cjs`](../packs/godot/runtime/index.cjs) 只有 `godot.scene.edit`／`godot.scene.verify`，限定场景变换、可见性与 BoxMesh 大小。

- edit：`after.tscn` 为主成果，`before.tscn` 为明确附件；成功且只读结构解析可用时声明场景 preview。完整运行目录及 `.godot` 缓存不作为交付物。
- verify：`work/readback.json` 为报告主入口，附 `work/frames.json`、import/readback/frames 日志和 receipts；准确关联受检场景，不替换场景成果。
- 如果场景引用脚本、贴图、资源或 project.godot，预览读取必须引用同一版本的已登记快照；缺失／变化明确降级，不能偷偷读取工程中的新文件。资源闭包只依据真实声明和引用，不扫描整个工程来挑主成果。
- 现有生产工具没有导出 HTML/WASM/PCK，不能声明“可玩游戏”预览。今后 Web export 入口需单独接 Runtime、产物清单和授权边界。
- 场景结构 Viewer 初期保持手动入口；Web 执行内容不随成果展示扩大权限。样例 `godot-edit`、`godot-verify`。

### Chip

先接 [`packs/chip/runtime/index.cjs`](../packs/chip/runtime/index.cjs) 的 `chip.rtl.verify`，及相同 owner 的 `rtl-cpu` sandbox profile。

- 本地 [`runtime/bridge.py`](../packs/chip/runtime/bridge.py) 从 native step 的显式 Artifact type/identity 读取 CAS 内容，目前返回无扩展名 blob 路径。需要按生产者的实际 output type/name，写入有适当格式名称的不可变输出快照，并保留内容摘要及原 native step 来源；不能按 CAS 数组最后一项选波形。
- simulate：每个明确的仿真 step 单独声明波形组；有 VCD 时波形为主入口，simulation report 与工具日志为附件。无波形／编译失败时保留实际报告和日志，不能合成波形。环境检查没有文件则不新增虚构成果。
- `rtl-cpu` 已有固定 `tool.log`、`report.json`、`wave.vcd` 出口，声明应在 owner native/profile 返回中随输出传播；remote adapter/exporter 必须保留这些局部标识。需要协调实际远程消费者后才能声称远程成果路径完成。
- Waveform Viewer 当前没有自动预览 opt-in。完成真实 VCD/FST/GHW 文件→Registry→Viewer、缩放与失败验收后再改变该策略。

完整 EDA 已有生产语义，但较宽 MCP 路径不能自动视为 canonical Runtime 接入。后续按 [`semantic.output_types()`](../packs/chip/eda-harness/src/eda_harness/plugins/semantic.py) 和 [`advanced.output_types()`](../packs/chip/eda-harness/src/eda_harness/plugins/advanced.py) 逐操作登记：

| 操作族 | 声明的主成果 | 附件／检查与 Viewer 范围 |
| --- | --- | --- |
| simulate / waveform | 明确仿真 step 的 VCD/FST | completion/signals/session/log；Waveform Viewer |
| synthesize / elaborate / check_netlist | Yosys JSON；门级 Verilog 为附件 | netlist/reference reports；Netlist Viewer，检查不等于等价性证明 |
| floorplan / place / cts / route / repair / physical utilities | 实际 ODB/DEF 交付物 | netlist/SDC/SPEF、operation/repair/database reports；ODB/DEF 不冒充现有 GDS Viewer |
| streamout | 实际 GDS | streamout report；GDS/OAS Viewer 需真实受支持格式 |
| extract / prepare_lvs_reference | 实际提取／参考网表 | extraction/reference reports；文本入口，不冒充 LVS 通过 |
| DRC / LVS / equivalence / checks | 对应实际检查报告 | native evidence、受检输入；不替换布局／网表 |
| STA / power | timing/setup/hold/TNS/endpoints 或 power 报告 | 约束、日志与输入关系；文档查看 |
| export_design | 每个明确要求的交付物或显式同组格式关系 | 原生 database report；不随输出顺序任意选一个 |
| lint / preflight / environment | 实际日志／报告；无文件则空组 | 不制造设计成果或工程通过状态 |

样例 `chip-rtl`、`chip-netlist`、`chip-streamout`。后两项是宽 EDA 路径的准备样例，未接通 canonical Runtime。

### CUDA

当前 [`packs/cuda/runtime/index.cjs`](../packs/cuda/runtime/index.cjs) 的 compile/baseline/verify/profile 只把真实 `receipt.json` 登记为 Artifact，取消时保存 `cancellation.json`；只读 compiler/evaluator check 没有文件。[`protocol.cjs`](../packs/cuda/runtime/protocol.cjs) 明确允许 `model_new.py` 与 `kernels/` 中有限源码，不能把远端 `.so`、ticket ID 或临时工作目录伪装成可下载产物。

- compile：补保存本次实际提交的候选源码和规范化 source manifest 快照，manifest 为候选组主入口，源码与编译回执为附件；独立回执组仍可选择。编译通过不代表 GPU 正确性已运行。
- baseline：不可变参考模型的 profile receipt 独立成组，不替换候选源码，也不为候选附加通过状态。
- verify/profile：报告主入口为本次 receipt；依据验证过的 project、compiler ticket、source manifest identity 关联原候选组。不能只凭源代码文件名或相似性能数字关联。
- candidate 源码多文件 identity 不能简化为 `model_new.py` 单文件 hash。源码组应保存实际提交清单，并将其字节摘要与远端 receipt 的 `sourceSha256` 严格对应；数据绑定不可随意追加换行改变摘要。
- 新候选保留并列组；除非有明确生产者替代声明，不按“更快”或最后 profile 自动删除其他候选。
- 取消只展示实际取消证据及 cleanup 状态。不得在声明中加入 MCP token 或私有端点；ticketId 保留在原始验证回执中用于身份绑定，不当作二进制下载地址或源码文件。
- JSON/source 可复用现有文档查看。真实编译／GPU评测验收与 fixture MCP 测试分开记录；本准备工作不启动远端任务。样例 `cuda-compile`、`cuda-baseline`、`cuda-verify`、`cuda-profile`、`cuda-cancel`。

### 通用文件（横切能力）

现有 `project.files.apply` 已声明实际修改文件，继续覆盖普通文档／源码，无需引入一个新 domain。不把该文件写入回执当成领域生产工具的工程验证，也不能用它追认 GUI/外部进程的未登记产物。

## 3. 实施前必须解决的共性缺口

1. **快照与可变工程路径的版本关联。** PCB/Godot 的主成果保存在 `.harness-runs/...` 快照，后续工具读取工程中的原路径。现有 `supersedesInput`／`checks` 使用路径+hash 精确匹配，不能直接匹配这两个路径。需要在共享契约／Runtime 中设计显式、内容校验的工作文件↔快照关系；领域 Pack 声明关系，宿主验证并持久化。不得按 hash 独自匹配相同内容的并列设计。样例中 PCB/Godot 的关联字段仅表达目标，在该能力接通前不能声称替代／检查关联生效；保守路径保留独立报告和版本。
2. **多文件候选 identity。** CUDA 保存并验证 source manifest，再以精确 manifest 关联检查。不能将远端回执当成源码交付物。基于当前 schema 的 manifest 方案需通过真实内容绑定和重启测试后采用。
3. **只有 Skill 的生产者。** 二维 CAD 需要 Runtime 生产／读回入口；Skill 文本更新本身不能完成接入。
4. **传输与消费身份。** 给输出补 localId 后，local/native/sandbox/MCP 传输必须保留 presentation。Harness 当前 pin 尚无 CUDA，需要先验收 owner 新版本再统一切换全部部署根和锁文件；不能提前改缓存。
5. **格式能力与自动预览。** 文件角色在 Pack，解析／自动预览能力在 Registry。Waveform、KiCad、Godot 结构等目前未 opt-in，不能只在 Pack 写 preview 就宣称自动打开已完成。

schema 的任何增量必须明确旧消费者如何退回既有 Artifact 入口；若需要新 schema 版本，按 Harness 契约版本规则迁移。以上是普通成果功能设计，不修改 IH-ARCH-001、checker、policy 或 workflow。

## 4. 跨仓实施顺序

| 阶段 | Owner 改动 | Harness／消费者改动 | 完成条件 |
| --- | --- | --- | --- |
| A 声明准备（本次） | 本清单、逐领域样例、产物／缺口核对 | 链接统一清单，记录当前 pin 与覆盖范围 | 样例符合现有 schema，所有引用属于样例输出；不宣称上线 |
| B 共同版本关系 | 配合声明显式工作文件与不可变快照关系 | 通用契约、绑定／收集、持久化、严格歧义与兼容回归 | 路径不同但显式相同版本可关联；同 hash 并列设计不误折叠 |
| C PCB／Godot | outputs 增 localId、生产返回声明、准确资源依赖与 Skill | 固定 owner commit，复用 TaskService；视验收启用只读自动预览 | 两领域 edit→verify→restart→CLI/Desktop→Viewer 实测 |
| D Chip | local CAS 输出命名、step 分组；sandbox 返回／出口声明 | 本地与实际 remote 传输保留字段，复用统一结果接口 | 真实 RTL 成功／断言失败／编译失败／取消／恢复；宽 EDA 按操作追加 |
| E CUDA | 源码/manifest 快照、receipt/localId、ticket 关联、Skill | 统一升级包含 CUDA 的 reviewed owner pin，复用文档 Viewer | 真正 paired MCP 编译／正确性／性能／取消路径；fixture 不替代 GPU 验收 |
| F 二维 CAD | 受控生产／读回入口，声明 DXF/DWG 及实际导出、Skill | 复用通用 API；无图形 Viewer 时保留手动取文件 | 实际 DXF 生成／读取／修改；DWG 只记录真实支持的 GUI 保存或读取路径 |
| G 全领域发布候选 | 一份不可变 owner 身份及 source lock | 全部部署根／锁一致；旧包/历史兼容；发行说明与截图 | 矩阵逐项有实际证据，未验收平台／远端不扩大支持声明 |

FreeCAD 是各阶段的回归基线。各阶段可分别提交 reviewable PR；源代码接入、桌面源码验收、安装包验收及远端资格分别记录。

## 5. 测试矩阵

下表是待实施的验收要求，不是本次执行结果。

| 层 | 必测行为 | 复用入口 |
| --- | --- | --- |
| Pack 声明 | 成功／部分输出／取消仅引用真实 localId；操作分组；缺少可选附件；日志不换主成果 | owner `tests/freecad-presentation.test.cjs`，新增对应领域声明测试；`tests/professional.test.cjs`／`tests/cuda.test.cjs` |
| 通用绑定 | 未知/重复局部标识、跨 Action、越界、内容变化、companion 不匹配；工作路径↔快照歧义；多文件 manifest | Harness `packages/domain-runtime/tests/presentation.test.cjs`，新增通用正反例 |
| TaskService | 同一请求多操作、持久化/重启、并列/历史选择、晚到/后台、CLI/Desktop 一致；旧包无声明 | `packages/harness-application/tests/results.test.cjs`、`packages/harness-application/tests/task-service.test.cjs` 等实际现存入口 |
| UI | 主成果+辅助报告折叠、步骤来源；失败/变化/选中结果直接可见；窄窗/主题；焦点不被抢走 | `apps/desktop/electron/result-layout.test.cjs`、`task-results-selftest.cjs`、Viewer 自测 |
| FreeCAD | 生成→连续修改→inspect→真实 BREP；准确输入/companion | `tests/integration/task-results-freecad.test.cjs`、`task-results-kimi.test.cjs` |
| PCB/Godot | 连续编辑→独立 DRC／scene 验证→结果关联；缺失依赖/脚本错误/取消/恢复；真实 Viewer | `tests/integration/pcb-godot-runtime.test.cjs`、`pcb-godot-installed.test.cjs`，追加成果集成用例 |
| Chip | 真实 VCD、编译／断言失败、多个 step、CAS→命名快照、取消／恢复；真实 sandbox 字段传递 | `industrial-core-vertical-slice.test.cjs`、`industrial-core-installed-pack.test.cjs` 及实际 remote consumer 门禁 |
| CUDA | 实际源码与 receipt/ticket/manifest 一致；换源拒绝；baseline 与 candidate 分离；GPU correctness/profile/取消 | owner `tests/cuda.test.cjs` 加 native qualification；Harness 追加源码→结果→文档查看 |
| 二维 CAD | 实际文件存在、回读失败、用户改动、导出缺失、无 Viewer 降级；不冒充工程通过 | 新的受控 CAD Runtime/结果集成；Skill-only 情况明确仍未完成 |
| 发行/边界 | Architecture contract、相关集成、source lock、immutable pin、真实 stdio MCP；无新增例外 | 现有两仓门禁；消费者的适用打包／安装门禁 |

## 6. 本次准备的完成状态

- 已核对 5 domain／6 Pack 的生产入口、真实产物种类、Viewer 与当前消费差异。
- 已给出各领域 schema 1 声明样例及逐领域／共性缺口；样例只用于审阅和契约检查。
- 已准备跨仓顺序和验收矩阵；新增实现均未标记完成。
- 未改执行代码、Verifier、Pack 内容锁、当前消费 pin、自动预览白名单或部署状态。

本次准备校验：19 份样例通过现有 Harness `ToolPresentationSchema`，逐项检查局部标识／组 key 唯一、输出引用、生产源码路径，以及五领域／六 Pack 的清单与覆盖一致。owner `npm test` 39 项通过，`npm run lock:check` 通过；Harness 架构契约 11 项及架构套件 24 项通过。这些是声明和现有门禁校验，不是新增领域的原生执行、GPU 或桌面接入验收。
