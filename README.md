# hosts-opt

[![Release](https://img.shields.io/github/v/release/wanwuzhishi/hosts-opt)](https://github.com/wanwuzhishi/hosts-opt/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Windows 上的 hosts 优化工具：从多个公开源拉取 GitHub hosts，在本机对候选 IP 做 TCP + TLS 测速，生成并可选写入系统 hosts。内置 Cloudflare、Vercel 优选，可选接入第三方 NotionFaster 反代；提供图形界面、连通性检测、原始 hosts 备份与一键回退、定时刷新。

- 运行时只依赖 Python 标准库（源码模式）
- 图形界面基于 Tkinter
- 发布版提供免 Python 的 Windows x64 EXE

## 下载与安装（Release）

1. 打开 [Releases](https://github.com/wanwuzhishi/hosts-opt/releases)，下载最新的 `hosts-opt-<版本>-windows-x64.zip`。
2. 解压到一个固定目录，双击 `hosts-opt.exe`。
3. 需要写入系统 hosts 时，点击界面里的“应用 hosts”，按提示以管理员身份重启。
4. 需要定时刷新时，在界面里设置小时数并点击“安装任务”。

发布包包含：

```
hosts-opt.exe          图形界面（内置 --cli 核心）
config.json            可编辑配置（源、IP 池、测速参数）
install-task.ps1       安装计划任务
uninstall-task.ps1     卸载计划任务
run-hidden.vbs         计划任务静默运行核心
README.md
LICENSE
THIRD_PARTY_NOTICES.md
```

建议保持这些文件在同一目录。EXE 首次运行也会把随包资源复制到自身目录，但计划任务脚本仍建议保留在原位置。

> 发布 EXE 没有代码签名，Windows SmartScreen 可能提示“未知发布者”。如果你介意，可以按“从源码运行”或“构建 EXE”自行构建；Release 页面会同时提供 ZIP 的 SHA256。

## 功能

- **GitHub hosts**：并发拉取 GitHub520、ineo6/hosts、maxiaof/github-hosts，源失败时自动使用本地缓存。
- **本地测速**：对每个 `(域名, IP)` 做真实 TCP + TLS 握手（带 SNI 和证书校验），优先选择全部采样成功的 IP。
- **Cloudflare 优选**：从 IPDB `bestcf` 拉取 Cloudflare 官方边缘候选 IP，对 4 个 Cloudflare 域名逐个测速。
- **Vercel 优选**：内置一组已验证的 Vercel Anycast IP，对 `vercel.com`、`www.vercel.com` 测速，并与 DoH 解析结果比较。
- **Notion 加速（可选）**：第三方 NotionFaster TCP 反代，默认关闭，需要显式启用。
- **连通性检测**：在 GUI 中显示当前已生效域名的 IP、TLS 延迟和连通状态；应用后自动检测。
- **备份与回退**：首次修改前永久保留原始 hosts；每次写入前自动备份；一键回退到原始或任意备份。
- **定时刷新**：Windows 计划任务，默认每小时运行一次，支持 1–24 小时。
- **安全写入**：只替换带标记的托管块，不碰 hosts 其他内容；标记不完整时中止；运行期间使用文件锁。

## 图形界面

发布版直接运行 `hosts-opt.exe`。源码模式：

```powershell
python hosts_opt_gui.py
```

或双击 `start-gui.cmd`。

界面页签：

- **选中结果**：每个域名最终选中的 IP、延迟、采样成功率和来源。
- **连通性**：显示当前系统 hosts 托管块（或最近预览）中每个域名的 IP、TLS 延迟和连通状态。支持“检测全部”；启动时若存在托管块会自动检测，应用 hosts 后也会自动检测。
- **候选明细**：所有候选 IP 的测速结果、来源和错误信息，双击行可复制 IP。
- **备份/回退**：查看原始备份和自动备份，立即备份，回退到原始或选中备份。
- **运行日志**：实时显示核心命令输出和连通性检测结果。

主操作按钮：

- **拉取并测速**：只预览，不修改系统 hosts。
- **应用 hosts**：重新拉取测速后写入系统 hosts（需要管理员）。
- **移除托管块**：只删除本工具写入的标记块，保留 hosts 其他内容。
- **IP 优选（CF/Vercel）**：默认开启，控制 Cloudflare、Vercel 和 Notion 池的总开关。
- **Notion 代理（第三方）**：默认关闭，勾选后启用 NotionFaster 的 4 个反代节点。
- **安装任务 / 卸载任务**：计划任务管理。
- **以管理员身份重启**：非管理员启动时显示。

## 从源码运行

需要 Python 3.10+（Windows 官方安装包自带 Tkinter），无第三方运行时依赖。

```powershell
git clone https://github.com/wanwuzhishi/hosts-opt.git
cd hosts-opt
python hosts_opt_gui.py
```

或运行 CLI：

```powershell
python hosts_opt.py update --verbose
```

## 命令行用法

先预览（不写系统 hosts，不需要管理员）：

```powershell
python hosts_opt.py update --verbose
```

查看前几个候选的测速结果：

```powershell
python hosts_opt.py update --verbose --top 5
```

生成结果在 `dist/hosts.generated`，测速明细在 `dist/report.json`。满意后，**用管理员身份**打开 PowerShell：

```powershell
python hosts_opt.py update --apply
```

发布版 EXE 的 CLI 模式：

```powershell
.\hosts-opt.exe --cli update --apply
```

工具只会修改 hosts 文件中下面这对标记之间的内容：

```
# >>> hosts-opt managed block begin >>>
...
# <<< hosts-opt managed block end <<<
```

其余内容原样保留；写入前会备份，写系统 hosts 后自动执行 `ipconfig /flushdns`。

### 备份与回退

```powershell
# 立即备份当前 hosts，并创建/保留原始备份
python hosts_opt.py backup

# 列出所有备份
python hosts_opt.py rollback --list

# 回退到原始备份（需要管理员）
python hosts_opt.py rollback

# 回退到指定备份（需要管理员）
python hosts_opt.py rollback --backup .\backups\hosts.20261019-151105.bak

# 只移除本工具写入的托管块，不覆盖其他内容
python hosts_opt.py restore
```

发布版把 `python hosts_opt.py` 换成 `.\hosts-opt.exe --cli` 即可。

备份规则：

- 首次写入系统 hosts 前，会创建 `backups/hosts.original.bak`，永久保留，不会被自动清理。
- 每次写入前自动创建 `backups/hosts.<时间戳>.bak`，默认保留最近 10 份（`backup_keep`）。
- 回退前会先把当前 hosts 再备份一次，所以回退本身也可以撤销。
- `restore` 只移除托管块；`rollback` 是用备份文件完整覆盖 hosts，两者不要混淆。

### 其他命令

```powershell
# 查看 hosts 状态、IP 池、原始备份、生成文件和计划任务
python hosts_opt.py status

# 本次禁用所有 IP 池
python hosts_opt.py update --no-pools

# 本次启用配置中默认关闭的池
python hosts_opt.py update --enable-pool NotionFaster-proxy

# 离线模式：只用缓存的源/IP 池，不做拉取和 DoH
python hosts_opt.py update --offline
```

## 定时刷新

用管理员 PowerShell 安装计划任务，默认每小时运行一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1
```

指定间隔（小时）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -IntervalHours 2
```

发布版会优先注册 `hosts-opt.exe --cli update --apply`，并通过 `run-hidden.vbs` 静默运行；源码模式则使用 `pythonw.exe`。任务以最高权限运行，日志写入 `logs\hosts_opt.log`。安装后会立即触发一次。

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall-task.ps1
```

卸载任务不会删除托管块或备份；需要时再执行 `restore` / `rollback`。

## Cloudflare 优选

默认启用。工具会把 [ymyuuu/IPDB](https://github.com/ymyuuu/IPDB) 的 `bestcf` 列表作为候选 IP 池，对下面 4 个域名逐个做本地 TLS 测速：

- `cloudflare.com`
- `www.cloudflare.com`
- `cdnjs.cloudflare.com`
- `developers.cloudflare.com`

```json
"ip_pools": [
  {
    "name": "IPDB-bestcf",
    "url": "https://ipdb.api.030101.xyz/?type=bestcf",
    "domains": [
      "cloudflare.com",
      "www.cloudflare.com",
      "cdnjs.cloudflare.com",
      "developers.cloudflare.com"
    ],
    "enabled": true,
    "max_candidates": 12
  }
]
```

说明：

- 只使用 `bestcf`（Cloudflare 官方边缘 IP），不要用 `bestproxy`：后者是第三方反代 IP，写进 hosts 通常会证书不匹配。
- 只对 Cloudflare 代理的域名有效。可以用 `curl -I https://域名` 查看响应头里是否有 `Server: cloudflare` 和 `CF-RAY`。
- 榜单 IP 只是候选，最终由本机 TLS 测速决定；不同网络、不同时段选出的 IP 可能不同。
- 工具还会对这些域名查询 DoH，把默认解析路径的 IP 一起拿来比较；如果默认 IP 更快，会选默认 IP。
- 关闭方式：取消 GUI 的“IP 优选（CF/Vercel）”、命令行 `--no-pools`，或把该池 `enabled` 设为 `false`。

## Vercel 优选

Vercel 没有类似 IPDB 的公开优选 IP 库，官方也不提供 ingress IP 列表或优选方案。它使用 Anycast，`vercel.com` 当前会解析到 `64.239.x`、`76.76.21.x`、`66.33.60.x` 等少量地址。工具内置了一组已验证的 Anycast IP（包括官方文档提到的 `76.76.21.21`），对 `vercel.com`、`www.vercel.com` 逐个做本地 TLS 测速，同时用 DoH 查询当前解析结果一起比较。

```json
{
  "name": "Vercel-anycast",
  "domains": ["vercel.com", "www.vercel.com"],
  "ips": [
    "76.76.21.21",
    "76.76.21.22",
    "76.76.21.123",
    "76.76.21.241",
    "64.239.109.1",
    "64.239.109.65",
    "64.239.109.193",
    "64.239.123.193",
    "66.33.60.35",
    "66.33.60.67",
    "66.33.60.193"
  ],
  "enabled": true,
  "max_candidates": 12
}
```

说明：

- 收益比 Cloudflare 小。本机 5 次 TLS 采样中，最优 IP 约 388 ms，默认解析约 436 ms，约快 7–11%；不同网络可能更小，甚至没有提升。
- `vercel.app` 的 apex 和任意 `*.vercel.app` 不能用 hosts 通配，只能加速固定域名。自己的 Vercel 自定义域名可以加进 `domains`，工具会用该域名做 SNI 校验和测速。
- Vercel 官方文档明确说中国大陆没有节点，建议使用自定义域名、静态资源自托管、镜像/双部署；不推荐在前面加代理。Cloudflare 只做 DNS（DNS-only）不会提速，Full (strict) 代理会增加一跳，需要自行测量。
- 如果你加速的是自己部署在 Vercel 的站点（有自定义域名），社区还有 CNAME 分线路方案，例如 [enhanced-FaaS-in-China](https://github.com/xingpingcn/enhanced-FaaS-in-China) 的 `vercel-cname.xingpingcn.top`。它改的是 DNS CNAME，不是 hosts，也不能用于 `vercel.com` 本身；属于第三方服务，使用前请自行评估稳定性和隐私风险。
- 关闭方式：取消 GUI 的“IP 优选（CF/Vercel）”、`--no-pools`，或把该池 `enabled` 设为 `false`。

## Notion 加速（第三方代理，默认关闭）

Notion 官方没有提供优选 IP。[NotionFaster](https://notionfaster.org/) 是一个第三方公益项目，用 TCP 反向代理中转 Notion 流量，并在状态页提供 4 个节点：

- `198.44.178.123`
- `119.28.13.121`
- `101.32.183.34`
- `43.128.3.53`

本项目把它做成一个**默认关闭**的 IP 池 `NotionFaster-proxy`，支持以下域名：

- `notion.so`
- `www.notion.so`
- `app.notion.com`
- `identity.notion.com`
- `img.notionusercontent.com`
- `msgstore-002.app.notion.com`
- `exp.notion.com`
- `audioprocessor.app.notion.com`

不支持的域名：`notion.com`、`notion.site`、`file.notion.so` 以及 Notion 使用的 S3 域名（代理会直接断开 TLS）。

启用方式（三选一）：

1. 图形界面勾选“Notion 代理（第三方）”。
2. 命令行：`python hosts_opt.py update --enable-pool NotionFaster-proxy`（发布版加 `--cli`）。
3. 把 `config.json` 里该池的 `enabled` 改为 `true`。

工作方式：工具会对 4 个代理节点逐个域名做 TLS 校验和测速，同时用 DoH 查询 Notion 的直连 IP 一起比较；只有代理 IP 证书有效且更快时才会选中。实测（2026-10-19，本机 3 次采样）：

| 域名 | 最快代理节点 | 延迟 |
|---|---|---|
| `identity.notion.com` | `43.128.3.53` | 约 32 ms |
| `notion.so` | `119.28.13.121` | 约 76 ms |
| `www.notion.so` | `43.128.3.53` | 约 90 ms |
| `app.notion.com` | `43.128.3.53` | 约 138 ms |
| `img.notionusercontent.com` | `119.28.13.121` | 约 139 ms |

直连 Notion 的 TLS 约 330–380 ms；代理节点之间差异很大，工具会每轮重新选。

注意：

- 这是第三方 TCP 反向代理。项目声称不持有 `notion.so` 私钥、TLS 不解密，但代理仍然能看到连接元数据，且服务可用性没有保证。只在你信任该项目时启用。
- 不要把它当作端到端加密之外的额外保护；敏感内容请自行评估。
- 状态页：<https://status.notionfaster.org/>；项目没有公开 GitHub 仓库，也没有可下载的完整 hosts 文件。
- 项目还提供 DoT/DoH 和向导页，本项目没有接入它的 DNS，避免影响其他域名。

## 工作流程

1. **拉取 GitHub hosts 源**：并发请求 `config.json` 中的三个源，成功结果写入 `cache/`；某个源失败时自动使用旧缓存。
2. **拉取 IP 池**：默认请求 IPDB 的 `bestcf` 列表，并读取内置的 Vercel Anycast IP 列表；NotionFaster 第三方池默认关闭，只有显式启用时才加入。动态源失败时使用缓存，静态 IP 始终可用。
3. **补候选**：对候选不足的域名以及所有 IP 池域名，通过 DoH（默认腾讯 `doh.pub` 和阿里 `dns.alidns.com`）补充 IP。
4. **本地测速**：对每个 `(域名, IP)` 建立 TCP 连接并完成带 SNI 和证书校验的 TLS 握手，默认采样 3 次，取成功样本的中位数。
5. **选优**：优先选择全部采样成功的 IP；延迟接近时保持上一轮 IP，减少来回切换。
6. **写入**：生成托管块，备份原文件，替换块内容，刷新 DNS；如果 IP 映射没变则跳过写入。

## 配置说明

编辑 `config.json` 即可，常用项：

| 配置 | 默认 | 说明 |
|---|---|---|
| `sources` | 3 个源 | 每个源是 `{name, url}`；可增删 |
| `ip_pools` | Cloudflare + Vercel，Notion 默认关闭 | IP 池列表，每个池包含 `url` 或 `ips`、`domains`、`max_candidates`、`enabled`；`--enable-pool NAME` 可临时启用 disabled 池 |
| `domains` | 13 个 | GitHub 相关基础域名；IP 池域名会自动追加 |
| `samples` | `3` | 每个 IP 的采样次数 |
| `concurrency` | `24` | 并发测速数 |
| `connect_timeout` | `2.0` | 单次 TLS 连接超时（秒） |
| `max_candidates_per_domain` | `15` | 每域名最多测多少个候选 IP |
| `doh_min_candidates` | `2` | 候选少于该数时才查 DoH（IP 池域名始终查） |
| `enable_doh` | `true` | 是否启用 DoH 补候选 |
| `enable_ipv6` | `false` | 是否测速并写入 IPv6 |
| `use_system_hosts` | `true` | 把现有 hosts 中的相关条目也作为候选 |
| `keep_previous_on_failure` | `true` | 本次全部测速失败时沿用旧 IP |
| `allow_untested_fallback` | `true` | 没有旧 IP 时允许写入未测速的源 IP |
| `sticky_previous` | `true` | 延迟接近时保持旧 IP |
| `switch_margin_ratio` | `0.25` | 切换阈值：新 IP 需快 25% 以上 |
| `switch_margin_ms` | `15` | 切换阈值附加 15ms 容差 |
| `backup_keep` | `10` | 自动备份保留数量；原始备份不受限制 |

`--offline` 只跳过源/IP 池拉取和 DoH 查询，仍然会做本地 TLS 测速；适合源站临时不可用时使用。

## 构建 EXE

需要 Python 3.10+ 和 PyInstaller（构建依赖，运行时不依赖）：

```powershell
.\build.ps1 -Version 1.0.0
```

脚本会：

1. 安装 `requirements-build.txt` 中的 PyInstaller；
2. 用 PyInstaller `--onefile --console` 构建 `hosts-opt.exe`（GUI 模式启动后自动隐藏控制台）；
3. 把 EXE、配置、脚本、文档打包到 `release/hosts-opt-<版本>/`；
4. 生成 `release/hosts-opt-<版本>-windows-x64.zip` 和对应的 `.sha256`。

只构建、不重复安装依赖：

```powershell
.\build.ps1 -Version 1.0.0 -SkipInstall
```

## 数据来源与致谢

本项目能工作，主要依赖这些公开项目和服务的持续维护，请优先支持它们：

| 项目 / 服务 | 用途 | 许可证 / 说明 |
|---|---|---|
| [521xueweihan/GitHub520](https://github.com/521xueweihan/GitHub520) | GitHub hosts 源 | README 标注 CC BY-NC-ND 4.0；仓库未提供 LICENSE 文件，以源站为准。运行时拉取，不重新分发 |
| [ineo6/hosts](https://github.com/ineo6/hosts) | GitHub hosts 源 | MIT |
| [maxiaof/github-hosts](https://github.com/maxiaof/github-hosts) | GitHub hosts 源 | 未检测到明确许可证；使用/分发前请确认源站条款 |
| [ymyuuu/IPDB](https://github.com/ymyuuu/IPDB) | Cloudflare `bestcf` 优选 IP | MIT；默认 60 分钟更新 |
| [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) | 参考测速工具 | GPL-3.0；仅文档引用，未复制代码 |
| [xingpingcn/enhanced-FaaS-in-China](https://github.com/xingpingcn/enhanced-FaaS-in-China) | Vercel CNAME 分线路备选方案 | MIT；仅文档引用 |
| [NotionFaster](https://notionfaster.org/) | 可选 Notion 第三方反代 | 无公开仓库/许可证；默认关闭，启用即表示信任该服务 |
| [Vercel 官方文档](https://vercel.com/kb/guide/accessing-vercel-hosted-sites-from-mainland-china) | Vercel 大陆访问与代理说明 | 文档引用 |
| [PyInstaller](https://pyinstaller.org/) | 构建 Windows EXE | GPL-2.0-or-later（含 bootloader 例外） |

NotionFaster 是公益项目，如果它帮到了你，可以考虑支持作者：<https://ifdian.net/a/notion-proxy-cn>。

更完整的第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目不提交 `cache/`、`dist/`、`logs/`、`backups/` 中的数据；上游 hosts/IP 数据仍受各自许可证约束。

## 安全设计

- 默认只预览，只有显式 `--apply`（或 GUI 应用按钮）才写系统 hosts。
- 写系统 hosts 前检查管理员权限。
- 只替换带标记的托管块，不碰其他内容。
- 托管块开始标记存在但结束标记缺失时直接中止，不修改文件。
- 首次写入前创建永久原始备份；每次写入前自动备份；回退前再备份当前文件。
- 内容无变化或 IP 映射未变时跳过写入。
- 拉取/测速/写入/备份期间使用 `.hosts-opt.lock` 文件锁，避免 GUI 与计划任务并发操作。
- 备份和生成文件都放在程序目录，便于排查和回滚。

## 已知限制

- **代理模式**：如果浏览器走 HTTP/HTTPS 代理，DNS 通常在代理端解析，hosts 对浏览体验基本无效。TUN/全局 VPN 也可能绕过 hosts。此工具测速走的是本机直连 TCP。
- **Cloudflare 模式**：只适用于 Cloudflare 代理的域名；`bestproxy` 反代 IP 不适合写 hosts；TLS 握手延迟不等于下载速度，大文件场景可配合 CloudflareSpeedTest 测吞吐。
- **Vercel 模式**：只有少量 Anycast IP，收益通常小于 Cloudflare；`*.vercel.app` 无法用 hosts 通配；官方不提供优选 IP，大陆访问的根本方案是自定义域名、镜像或本地部署。
- **Notion 模式**：NotionFaster 是第三方 TCP 反代，默认关闭；只支持上述 8 个域名；代理能看到连接元数据，服务可用性无保证。
- **CDN 波动**：GitHub、Cloudflare、Vercel 和 Notion 的 CDN/代理 IP 延迟会随时间变化，定时任务会周期性重新选优。
- **EXE 签名**：发布 EXE 未签名，SmartScreen 可能提示未知发布者；可以从源码构建。
- **不是万能加速**：hosts 只能解决域名解析到较优 IP 的问题，无法绕过带宽限制、丢包或链路拥塞。

## 常见问题

**写入后没生效？**

执行 `ipconfig /flushdns`，或重启浏览器；Windows 也可能需要重启机器。工具在写系统 hosts 后会自动刷新一次。

**某个域名显示 FAIL 或 skipped？**

看 `dist/report.json` 里该域名的 `probes`。如果所有候选都失败，工具会沿用上一轮托管块中的 IP；没有旧 IP 时跳过该域名。连通性页签会显示当前实际 IP 的失败原因。

**“移除托管块”和“回退到原始”有什么区别？**

“移除托管块”只删除本工具写入的标记块，hosts 其他内容保持不变；“回退到原始”用 `backups/hosts.original.bak` 完整覆盖当前 hosts。回退前会自动备份当前文件。

**找不到原始备份？**

原始备份在首次使用（GUI 启动、点击“立即备份”或执行应用）时创建，位于 `backups/hosts.original.bak`。如果之前已有自动备份，会从最早的自动备份生成。也可以点击“立即备份”手动创建。

**想完全停用？**

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall-task.ps1
python hosts_opt.py restore
```

**EXE 被 SmartScreen 拦截？**

EXE 未签名。可以核对 Release 页面提供的 SHA256，或从源码运行/构建。不要从非官方渠道下载 EXE。

## License

本项目代码采用 MIT License，见 [LICENSE](LICENSE)。第三方数据、服务和构建工具的许可证与使用条款见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
