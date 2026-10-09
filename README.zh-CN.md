# Industrial Domain Packs

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

remote 直接依赖 npm 包名 @zhiman-bj/industrial-domain-packs 对应的 Git 提交。它读取 Pack 提供的 descriptor、验证器、执行入口、证据导出器和 Dockerfile，不再维护独立 Chip runner。沙箱执行前必须与协调器核对内容身份；身份随能力快照和任务保存。

Chip 本地和沙箱使用同一套 EDA semantic.prepare / semantic.observe；本地和远端的 canonical 验证器都引用 runtime/verifier.cjs。旧 Harness 安装不会自动切换，Harness 消费端后续单独迁移。

开发、真实 MCP 检查、镜像构建和 validate.sh 使用见英文 README。源码修改后先暂存经过检查的 packs/ 和 lib/ 文件，再执行 npm run lock 更新内容身份。消费者和沙箱必须从同一固定版本构建。源码 CI 不构建或发布工具镜像。

[验收记录](docs/migration-validation.md) 区分代码迁入、原生工具执行和远端支持。source-only archive 可覆盖所有六个 Pack，但不是可直接使用的执行镜像；镜像构建使用完整固定版本的包。

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
