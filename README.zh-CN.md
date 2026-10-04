# Industrial Domain Packs

[English](README.md)

领域实现统一维护在这个公开仓库。本地与远端通过完整 Git commit 固定同一版本，包含执行代码、MCP、Skills、依赖锁、工具定义、验证器和镜像配方。

| Pack | 已迁入内容 | 当前边界 |
| --- | --- | --- |
| Chip | EDA 0.6.1、25 个 MCP 工具、Core RTL 适配器、Skills、完整 EDA 与 rtl-cpu 镜像配方 | CPU 体验服务开放 chip.rtl.verify；其余 EDA 工具仍按本地依赖使用 |
| Godot | 场景源码与运行时检查、受限 gateway、游戏 Skills | 需要 Godot 4；尚未完成远端原生验收 |
| PCB | 公开 gateway/controller、元数据、依赖锁、Skills | 私有 PCB-bench actor 继续作为外部依赖，不公开其源码 |
| FreeCAD | 公开 PR29 已提交的 runtime、参数化模型校验器、原生代码、Skill | 沿用 macOS arm64 验收记录；Linux/Windows 未验收 |
| CAD guidance | AutoCAD macOS、ezdxf、intent-loop Skills | 仅 Skills，没有远端执行 profile |

导入来自 Harness 提交 371b011b41417d5cb0c29bc9a0fd4c8bfa4bf75e 和 FreeCAD PR29 提交 62510b22b47803e0841825ec1c2b34fae48b3b15。逐文件来源与哈希见 [迁移记录](provenance/domain-migration.json)。本地仍在修改的工作文件不纳入本次迁移。

remote 直接依赖 npm 包名 @zhiman-bj/industrial-domain-packs 对应的 Git 提交。它读取 Pack 提供的 descriptor、验证器、执行入口、证据导出器和 Dockerfile，不再维护独立 Chip runner。沙箱执行前必须与协调器核对内容身份；身份随能力快照和任务保存。

Chip 本地和沙箱使用同一套 EDA semantic.prepare / semantic.observe；本地和远端的 canonical 验证器都引用 runtime/verifier.cjs。旧 Harness 安装不会自动切换，Harness 消费端后续单独迁移。

开发、真实 MCP 检查、镜像构建和 validate.sh 使用见英文 README。源码修改后先暂存经过检查的 packs/ 和 lib/ 文件，再执行 npm run lock 更新内容身份。消费者和沙箱必须从同一固定版本构建。源码 CI 不构建或发布工具镜像。

[验收记录](docs/migration-validation.md) 区分代码迁入、原生工具执行和远端支持。source-only archive 可覆盖所有五个 Pack，但不是可直接使用的执行镜像；镜像构建使用完整固定版本的包。

本仓库负责领域实现；Harness 继续负责 canonical 合约、Runtime、Broker、Agent、CLI、Desktop、Viewer 框架；私有 remote 仓库负责认证、文件快照、授权、有限调度和沙箱生命周期。工作负载不获得 Docker socket 或 Kubernetes 权限。

原始实现与已导入的 Harness 源码采用 MIT，保留 [第三方与来源说明](THIRD_PARTY_NOTICES.md)。原生工具保留自身许可。仓库不包含客户数据、凭据、私有部署地址、PDK、私有 PCB actor、二进制或历史运行产物。
