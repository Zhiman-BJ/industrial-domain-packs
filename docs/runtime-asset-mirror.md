# Runtime 资产自有镜像源（runbook）

**问题**：pack 的 `runtimeAssets` 原先直连上游 release CDN（KiCad/Godot/FreeCAD 的 GitHub
release 资产）。上游撤档或国内直连不可达都会让用户在"域安装→准备原生依赖"一步失败。

**原则**：用户只从**组织自有存储**下载重型资产；上游 URL 只在我们拉取一次时使用，并作为
`upstreamUrl` 溯源字段留在声明里。sha256 钉死不变，自有源与上游比特级一致。

## 一次性：上架资产（维护者执行）

1. **取回并校验**（在上游可直连的机器上）：

   ```sh
   node scripts/mirror-runtime-assets.cjs dist/runtime-assets
   ```

   脚本逐个下载所有 pack 声明的 `runtimeAssets`，校验钉住的 sha256，落到
   `dist/runtime-assets/<id>-<version>/<file>` 并生成 `manifest.json`。

2. **上传到组织 Harbor（OCI artifact，blob 即原文件）**：

   ```sh
   oras push --username <robot> --password-stdin --disable-path-validation \
     harbor.pvg2.zhiman.tech/industrial-runtime-assets/<name>:<version> \
     dist/runtime-assets/<id>-<version>/<file>:application/octet-stream
   ```

   Harbor 对公开项目也要求 Bearer token；pack-manager 已支持匿名 token 握手
   （harness PR #123）。`application/octet-stream` 不压缩——blob digest 即文件
   sha256，因此资产 URL 自带校验：

   `https://harbor.pvg2.zhiman.tech/v2/industrial-runtime-assets/<name>/blobs/sha256:<pin>`

   bucket 侧要求：项目 `industrial-runtime-assets` 保持 **public**（匿名 pull）。

3. **验证**（任意网络）：

   ```sh
   curl -fsSL https://<mirror-host>/industrial-runtime-assets/manifest.json
   shasum -a 256 -c <<< "<sha256>  kicad-10.0.6/kicad-unified-universal-10.0.6.dmg"
   ```

## 切换 pack 声明

对 `packs/{pcb,godot,freecad}/harness-pack.json` 的每个 asset：

- `url` → `https://<mirror-host>/industrial-runtime-assets/<id>-<version>/<file>`
- 新增 `upstreamUrl`（原 url，仅溯源）
- `sha256`/`size`/其余字段不动（同一份比特）

随后走标准版本链：pack `version`/`releaseVersion`/consumer.json + content-lock
（`npm run lock`）→ harness 侧三处 package.json pin bump。

## 新增资产 / 升级版本时

- 上游版本升级 = 重新资格化（native 版本钉、verifier、Dockerfile apt pin 都要跟着过一遍），
  不要只改 URL；
- 新资产先在 harness-pack.json 声明（指向上游），再跑 mirror 脚本上架，最后把 url 切到
  镜像源，同一 PR 内完成。

## 已知边界

- 镜像源本身是单点：MinIO 挂了 = 新用户首装失败（已装用户不受影响，资产在本地 pack store）；
  可在 runtimeAssets 引入 fallbackUrls（需要 pack-manager 扩展）作为后续加固。
- KiCad 10.0.6 是全链路行为钉（native.py/verifier/容器 apt pin），与镜像源问题正交。

## 已知事故（2026-10-11，待 Harbor 侧修复）

**KiCad 10.0.6 DMG（1,404,303,659B）在该 Harbor 实例上无法持久化**，证据链：

1. 第一次上传后 blob 出现"元数据在、内容无"的僵尸态：HEAD 返回 200（oras 因此跳过重传），GET 返回 400 DIGEST_INVALID，跨仓库 HEAD 404（存在性按仓库记录），API blob DELETE 返回 400，删除仓库不清除 blob 元数据；
2. 换新仓库重传（绕开僵尸）：blob 上传会话真实执行，最终 `PUT …/blobs/uploads/<id>?digest=…` 返回 **201 Created**（registry 协议此时已完成服务端内容摘要校验），数分钟后同一 blob HEAD 又变 404；
3. 同尺寸/相近尺寸的合成随机文件（1.2GiB 与 1.31GiB）在同一实例上传后均字节级校验通过；godot（177MB）/freecad（624MB）资产正常。

结论（2026-10-12 复核修正）：**实例级存储故障，与文件内容无关**——隔夜重推失败、推进已验证健康的仓库也失败、且昨天验证通过的同尺寸随机文件今天同样被截断（1.31GiB 仅存 382MiB）；手工分块 PATCH（8 块全 202）最终 PUT 仍 400。时间线与"磁盘写满"完全吻合：先写入的 godot/freecad/两个大合成文件全部成功，此后所有 ~1.2GiB+ 写入开始静默截断/丢失；删除仓库不释放 blob 空间（未 GC 的测试残留约 5.2GiB 仍占盘）。已上线的 godot/freecad 复核完好。**处置：Harbor 管理员检查数据卷剩余空间并执行一次垃圾回收（回收已删除仓库的 blob），或扩容后重传 kicad 即可**（复推命令见上）。修复前 KiCad 资产暂留 GitHub 上游 URL（#18 已拆分：godot/freecad 切 Harbor，kicad 待修后切换）。

**Plan B（如 Harbor 短期难修）**：同一 nginx 上加一个静态目录 location（如 `/assets/`）直接伺服 DMG 文件——pack-manager 对纯 https 直链本就无需 token 握手。
