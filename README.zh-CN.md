# Industrial Domain Packs

[English](README.md)

Industrial Agent Harness 的公共领域代码与 Pack 发布仓库。本地 Docker 与远端 Kubernetes 将消费同一份领域发布，共用工具定义、执行代码、验证器、依赖锁和工具镜像身份。

**当前状态：源码基线已迁入。** 首批采用现有公开、MIT 许可的 Chip 实现。共享远端执行后端、RTL 轻量镜像、不可变运行环境发布和消费者迁移仍待实施。仓库创建不会自动切换现有安装或部署。

## 仓库分工

| 仓库 | 职责 |
| --- | --- |
| **industrial-domain-packs** | 领域 MCP、执行逻辑、canonical 工具与验证适配、Skill、工具镜像配方及 Pack 发布 |
| [industrial-agent-harness](https://github.com/Zhiman-BJ/industrial-agent-harness) | canonical 工业契约、Runtime、Broker、Agent、CLI、Desktop 和 Viewer 框架 |
| industrial-harness-remote | 认证、快照、审批信封、有限队列、沙箱生命周期与传输 |

领域修改在本仓维护，消费者将固定 Pack 发布版本。canonical 工业契约继续由 Harness 拥有。

## Chip 源码

`packs/chip/` 包含 EDA Harness 0.6.1、25 个 stdio MCP 工具、Python 锁文件、EDA Skill、现有 Core RTL adapter 和完整 EDA 工具镜像配方。来源固定到公开 Harness commit `01d8ea2c01bdf3e3be858b1bcee0c29b174ad8c5`，逐文件摘要见 [源码基线记录](provenance/chip-bootstrap.json)。

安装并验证 MCP：

```sh
uv sync --frozen --directory packs/chip/eda-harness
uv run --frozen --directory packs/chip/eda-harness python ../scripts/mcp-smoke.py
```

这个检查验证真实工具发现和服务信息。工程执行还需要准备原生工具和有效项目；受保护 Harness 会话继续通过 canonical Runtime 和受控网关执行。已有完整镜像配方面向 Linux amd64，`rtl-cpu` 轻量镜像和 Kubernetes 后端为后续改造。

提交源码后可运行 `python3 scripts/package-pack.py chip --output dist` 生成源码归档、逐文件版本清单和归档摘要。打包只读取已提交的 Pack 文件，排除未跟踪文件及本地环境；当前不发布或宣称已资格验证的工具镜像。

架构、版本身份和迁移顺序见 [架构](docs/architecture.md)、[发布格式](docs/release-format.md)、[实施路线](docs/migration.md)。原始代码与第三方软件各自保留许可；本仓不包含客户工程、部署凭据、PDK 分发文件、工具二进制或历史执行日志。
