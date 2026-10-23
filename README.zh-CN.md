# Industrial Domain Packs

> 仅用于验收的候选 B：Chip 证据字段刻意不兼容。禁止合入 main 或部署 prod。

开发边界：[已接受的架构契约 IH-ARCH-001](docs/architecture-contract.md)。

[English](README.md)

领域实现统一维护在这个公开仓库。本地与远端通过完整 Git commit 固定同一版本，包含执行代码、MCP、Skills、依赖锁、工具定义、验证器和镜像配方。

| Pack | 已迁入内容 | 当前边界 |
| --- | --- | --- |
| Chip | EDA 0.6.1、25 个 MCP 工具、Core RTL 适配器、Skills、完整 EDA 与 rtl-cpu 镜像配方 | CPU 体验服务开放 chip.rtl.verify；其余 EDA 工具仍按本地依赖使用 |
| Godot | 类型化场景修改、真实 StateProvider、原生导入／回读／逐帧独立验证器 | macOS Apple Silicon，Godot 4.7.2；有限结构任务 |
| PCB | 公开 KiCad 类型化修改、真实 StateProvider、DRC 与原生几何验证器 | macOS Apple Silicon，KiCad 10.0.6；矩形安装板任务 |
| FreeCAD | 公开 PR29 已提交的 runtime、参数化模型校验器、原生代码、Skill | 沿用 macOS arm64 验收记录；Linux/Windows 未验收 |
| CUDA | Compiler / Evaluator 两个远程 MCP、源码客户端、canonical 验证器与优化 Skill | 固定 RTX 4090 AXPBY；私有原生执行依赖保持外置，见[验收记录](packs/cuda/QUALIFICATION.md) |
| CAD guidance | AutoCAD macOS、ezdxf、intent-loop Skills | 仅 Skills，没有远端执行 profile |

导入来自 Harness 提交 371b011b41417d5cb0c29bc9a0fd4c8bfa4bf75e 和 FreeCAD PR29 提交 62510b22b47803e0841825ec1c2b34fae48b3b15。逐文件来源与哈希见 [迁移记录](provenance/domain-migration.json)。本地仍在修改的工作文件不纳入本次迁移。Pack 子目录 README 与 harness-pack.json 保留原仓库／bootstrap 的安装说明，其中可能引用 Harness 专属脚本或先前计划；当前状态以本页、pack.json 和验收记录为准，旧消费端元数据待 Harness 迁移时再生成。

remote 直接依赖 npm 包名 @zhiman-bj/industrial-domain-packs 对应的 Git 提交。它读取 Pack 提供的 descriptor、验证器、执行入口、证据导出器和 Dockerfile，不再维护独立 Chip runner。共享 worker 在原生执行前检查 runtime 身份和输入快照完整性；完整 Pack 的内容校验在沙箱外完成。身份随能力快照和任务保存。

Chip 本地和沙箱使用同一套 EDA semantic.prepare / semantic.observe；本地和远端的 canonical 验证器都引用 runtime/verifier.cjs。旧 Harness 安装不会自动切换，Harness 消费端后续单独迁移。

开发、真实 MCP 检查、镜像构建和 validate.sh 使用见英文 README。源码修改后先暂存经过检查的 packs/ 和 lib/ 文件，再执行 npm run lock 更新内容身份。消费者固定经过审查的 Pack 提交，沙箱固定所选 profile 的不可变镜像；构建源码与发布提交的关系见[发布身份](docs/release-format.md)。源码 CI 不构建或发布工具镜像。

[验收记录](docs/migration-validation.md) 区分代码迁入、原生工具执行和远端支持。source-only archive 可覆盖所有六个 Pack，但不是可直接使用的执行镜像。固定版本的仓库可以作为 Docker 构建上下文，不代表要将其全部复制进镜像；远端镜像内容遵循[打包规范](docs/release-format.md#remote-runtime-image-packaging)。

本仓库负责领域实现；Harness 继续负责 canonical 合约、Runtime、Broker、Agent、CLI、Desktop、Viewer 框架；私有 remote 仓库负责认证、文件快照、授权、有限调度和沙箱生命周期。工作负载不获得 Docker socket 或 Kubernetes 权限。

原始实现与已导入的 Harness 源码采用 MIT，保留 [第三方与来源说明](THIRD_PARTY_NOTICES.md)。原生工具保留自身许可。仓库不包含客户数据、凭据、私有部署地址、PDK、私有 PCB actor、二进制或历史运行产物。

## Harness 消费发行

0.3.0 提供 `consumerMetadata()`、`hostPacks()`、`sourceDirectory()` 和 `skillResource()`，统一维护宿主清单、能力与 Skill 目录及固定源码资源。Harness 从精确固定的包消费这些声明和源码；维护入口只在本仓库。规范 Pack ID 与已有目录一致。已归并 Harness 后续验收的 FreeCAD 与 Chip 修复，PCB 私有 actor 仍是外部依赖。详见 [消费发行](docs/harness-consumer.md)。

0.4.0 新增 CUDA Pack。两个远程 MCP 使用独立身份与凭据，从宿主可信设置接入。本地准备服务前检查 GPU 与 profile，并明确绑定评测卡 UUID；远程客户端无需本机 GPU。详见[部署与客户端配置](packs/cuda/README.md)。

0.4.0 同时提供 [PCB／Godot 专业 Runtime](docs/professional-runtime.md)，集成路径采用 canonical Actions 和独立验证，原有 MCP 仅保留独立诊断。安装后的 Pack 通过 Harness 注入通用执行接口，不依赖开发目录。没有扩大远端、Linux、Windows 或 Intel Mac 支持声明。

0.4.1 为兼容的 Harness Pack Manager 声明官方 KiCad、Godot 和 FreeCAD 托管下载，范围为 macOS Apple Silicon。下载与安装大小、版本探测及 KiCad 的两个可执行文件映射由本仓维护；使用托管安装时无需用户运行命令或配置环境变量。Chip 仍需外部工具链，CUDA 仍需管理员提供远程服务。详见[安装元数据与边界](docs/native-installation.md)。已有公众安装器须更新消费版本和通用安装层，并完成验收，才会获得这些配方。

FreeCAD 现声明模型、对应版本的预览、导出和诊断成果组。检查关联实际输入快照；修改仅在原生生成成功后显式替代输入版本。Harness 将局部输出名绑定到已登记 Artifact。成果选择按需调用，几何验收仍由原有 Verifier 决定。

[全领域成果声明与扩展计划](docs/result-presentation-plan.md)和[契约校验样例](docs/result-presentation-examples.json)覆盖五个领域、六个 Pack。本次是接入准备；其他 Pack 的生产声明、快照版本关联及 CUDA 消费升级仍待实施和验收。

0.5.1 合并托管安装配方与 0.5.0 成果声明。FreeCAD 发行版本 1.1.4-pack.7 保留 pack.6 的完整安装配方和 pack.5 的成果呈现源码，已发布旧版本的内容保持不变。PCB、Godot 版本与平台资格范围不变。消费者须更新不可变版本固定值，并验证合并后的应用。

0.5.2 保留托管安装配方和 FreeCAD 成果声明，同时合入最新 Godot 场景编辑类型校验与工具说明。Godot 发行版本 0.2.2 将已审查的 Runtime 源码与现有托管原生配方组合；其他 Pack 的版本及内容均保持不变。旧发行版本仍保留各自不可变身份。

0.5.3 为 PCB 发行版 0.3.1 补齐 GitHub Release tarball 构建与验证：Linux AMD64
镜像从官方历史构建下载并校验 KiCad 10.0.6，在首次 HTTPS apt 请求前准备根证书，
并在构建时实际读写板文件、检查 DRC。`packs/pcb/validate.sh` 为每次任务启动独立
容器进行验证。宿主网关 Python 及原有原生平台、工程验收边界见 [PCB Pack](packs/pcb/README.md)。

0.5.4 修复远程报告契约迁移造成的本地验证回归：PCB 0.3.2 与 FreeCAD
1.1.4-pack.9 在执行结果中保留已验证的请求期望。PCB 本地插件使用原生板回读与
DRC 证据，远程入口继续强制要求 worker run report 及固定 profile/version。
执行 `node --test tests/pcb-local-runtime.test.cjs tests/freecad-local-runtime.test.cjs`
可回归生产 execute 到 verifier 的完整数据传递，原生进程边界使用 fixture，不能
作为原生资格验收。已有消费者仍需审查并更新固定版本，远程镜像须用同一固定包重建。

0.5.5 将 Linux FreeCAD AppImage 保留在远程镜像配方中，移出桌面托管下载声明。
FreeCAD 发行版本 1.1.4-pack.10 保留本地验证修复及已验证的 macOS 下载，Linux
继续作为独立远程执行 profile。该发行版本必须通过 Harness Pack 归档兼容校验。

## 可选 PCB 模型数据

包版本 0.5.8 / PCB Pack 0.3.4 将官方 KiCad 三维库 **10.0.7** 声明为可选的只读数据，独立于
KiCad 10.0.6 原生应用依赖。随包目录包含固定官方 Git 提交中的全部
**7,252 个原始 STEP 路径**、实际字节数及从原始内容独立计算的 SHA256。
兼容的 Viewer 可以只获取版图实际引用的模型，并应用自身的资源限制。
包内分发元数据与上游许可声明，不打包整个模型库；不会将 WRL 路径改写成
STEP 路径。来源校验、复现方式及边界见 [PCB Pack](packs/pcb/README.md#optional-official-3d-model-data)。

PCB 0.3.4 改用固定提交的官方 GitLab repository-files API，并使用目录 schema 2。
它需要配套新版 Harness 下载器；beta.7 会拒绝该目录，不会把 API 基址误当成 raw 目录。
模型路径、字节数、SHA256 和现有工程执行身份均保持不变。

## 可选的版图 Viewer Python 环境

**包 0.5.7 / Chip 分发版 0.6.3** 声明独立的、仅用于只读版图预览的
CPython **3.12.15** 与 KLayout **0.30.12**。兼容的 Harness 管理器可按固定
URL、SHA-256 和字节数下载官方 Astral PBS **20261013** `install_only` 归档，
并安装对应 macOS ARM64、Linux x64、Linux ARM64 或 Windows x64 的 CPython 3.12
PyPI wheel。源码包只包含声明、来源记录和临时 GDS→PNG 安装探针，不包含原生二进制。
该路径无需系统 Python 或 uv，保留完整私有 Python 树，不搬迁虚拟环境。详见
[固定资产、安装预算和验证边界](packs/chip/README.md#optional-layout-viewer-environment)。

该环境仅准备 Viewer，不准备 RTL 执行，不改变 Chip 原有 Python 3.13、Verilator、
C++ 工具链、其他 EDA/PDK 前提及工程验收边界。macOS ARM64 已运行固定归档、wheel
和 64×64 PNG 探针；其余三个平台只下载并核验了真实资产，不据此声明原生执行支持。

远端发布与消费方可通过 `getRuntimeRelease` 校验 schema-1 运行发布记录，见[发布身份](docs/release-format.md)。该元数据 API 不负责发布 Tool，也不代表执行 profile 已通过资格验收。

先运行 `npm run publish:images` 在本地准备共享镜像，校验 manifest/config 并通过 HEAD 核对每层的 digest 和大小，不完整下载镜像层（Buildx 导出 OCI，regctl 分块上传），所需环境变量和工具见[本地镜像准备](docs/release-format.md#local-image-preparation)。本仓库分支向 main 提 PR 后，Action 自动校验镜像元数据、发布 test Tool，并将最终 lock 写回当前 feature 分支。写回提交会触发普通 PR 检查，不递归发布。

Runtime release lock 由发布 GitHub Action 维护，不要手动修改生成的 image digest、Tool ID 或发布证据。修改发布流程前，请阅读[发布流程与最终 hash 计算顺序](docs/release-format.md#publication-workflow-and-generated-files)。`Publish remote runtime` 在合入 main 后自动发布 prod，feature 分支手动触发发布 test，具体见[腾讯云配置与资格范围](docs/release-format.md#tencent-publishing-action-configuration)。

新增或修改任何 domain 的远端 runtime profile 前，请阅读[统一打包规范](docs/release-format.md#remote-runtime-image-packaging)。 Action 无需填写参数：main 使用 prod，其他分支使用 test；直接查询腾讯云判断 image/Tool 复用。环境配置保存在 `publication/test.cjs` 和 `publication/prod.cjs`，每个文件集中列出各 domain，发布结果和容量预警展示在 Action Summary。

发布流程分为独立的 Check 和按需 Publish job；没有变更的 pack 跳过 Publish，仍会出现在最终汇总中。

面向 main 的 PR 会运行 `Runtime images ready`；管理员需将其设为必须通过且要求分支保持最新的检查。缺少镜像时，需先完成本地准备才能通过。详见[发布格式](docs/release-format.md#pr-image-readiness-gate)。

prod lock 通过机器人 PR 写回受保护的 main，required checks 通过后自动合并；feature 的 test lock 仍直接写回当前 PR 分支。
