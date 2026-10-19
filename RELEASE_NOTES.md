# hosts-opt v1.0.0

Windows x64 的 hosts 优化器，首个公开版本。

## 功能

- 多源 GitHub hosts：GitHub520、ineo6/hosts、maxiaof/github-hosts，源失败自动用缓存。
- 本地 TLS 测速：对每个（域名，IP）做 TCP + TLS 握手（SNI + 证书校验），优先选全部采样成功的 IP。
- Cloudflare 优选：从 IPDB `bestcf` 拉取官方边缘候选 IP，对 4 个 Cloudflare 域名逐个测速。
- Vercel 优选：内置已验证的 Vercel Anycast IP，对 `vercel.com` / `www.vercel.com` 测速，并与 DoH 结果比较。
- NotionFaster（可选，默认关闭）：4 个第三方 TCP 反代节点，支持 8 个 Notion 域名；启用后与直连 IP 一起比较。
- GUI 连通性页签：显示当前已生效域名的 IP、TLS 延迟和连通状态，支持一键检测，应用后自动检测。
- 原始 hosts 备份与一键回退：首次修改前永久保留 `hosts.original.bak`，每次写入前自动备份，支持回退到原始或任意备份。
- 安全写入：只替换托管块，不碰 hosts 其他内容；标记不完整时中止；运行期间加文件锁。
- Windows 计划任务：支持 1–24 小时间隔，发布版通过 `run-hidden.vbs` 静默运行。

## 安装

1. 下载 `hosts-opt-1.0.0-windows-x64.zip`。
2. 解压到固定目录，双击 `hosts-opt.exe`。
3. 无需安装 Python。
4. 写入系统 hosts 需要管理员权限：点击“应用 hosts”后按提示提权。

建议保留解压目录中的 `config.json`、`install-task.ps1`、`uninstall-task.ps1`、`run-hidden.vbs` 和文档。

## 快速开始

1. 打开 GUI，点击“拉取并测速”预览。
2. 在“选中结果”“连通性”“候选明细”中检查域名、IP、延迟和来源。
3. 点击“应用 hosts”，按提示以管理员身份重启并应用。
4. 需要自动刷新时，在界面上设置间隔并点击“安装任务”。

## 连通性与备份

- “连通性”页签：显示当前系统 hosts 托管块（或最近预览）中每个域名的 IP、TLS 延迟、正常/不稳定/失败状态；启动时若存在托管块会自动检测。
- “备份/回退”页签：查看原始备份和自动备份，支持“立即备份”“回退到原始”“回退到选中”。回退前会先备份当前 hosts。
- `restore` 只移除本工具写入的托管块；`rollback` 是用备份完整覆盖 hosts。

## 第三方与致谢

本项目依赖或参考了这些公开项目，感谢维护者：

- [GitHub520](https://github.com/521xueweihan/GitHub520) — GitHub hosts 源（README 标注 CC BY-NC-ND 4.0，运行时拉取，不重新分发）
- [ineo6/hosts](https://github.com/ineo6/hosts) — MIT
- [maxiaof/github-hosts](https://github.com/maxiaof/github-hosts) — 运行时 hosts 源，上游未声明明确许可证
- [ymyuuu/IPDB](https://github.com/ymyuuu/IPDB) — MIT，Cloudflare `bestcf`
- [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) — GPL-3.0，仅文档引用
- [xingpingcn/enhanced-FaaS-in-China](https://github.com/xingpingcn/enhanced-FaaS-in-China) — MIT，Vercel CNAME 备选方案
- [NotionFaster](https://notionfaster.org/) — 可选第三方 Notion 反代，默认关闭；可以支持作者：https://ifdian.net/a/notion-proxy-cn
- [Vercel 官方文档](https://vercel.com/kb/guide/accessing-vercel-hosted-sites-from-mainland-china) — 大陆访问与代理说明
- [PyInstaller](https://pyinstaller.org/) — 构建 EXE

完整声明见仓库中的 `THIRD_PARTY_NOTICES.md`。

## 注意

- EXE 未签名，Windows SmartScreen 可能提示未知发布者；可核对下方 SHA256，或从源码构建。
- NotionFaster 是第三方 TCP 反代，默认关闭；启用后 Notion 相关连接会经过第三方服务器。
- `*.vercel.app` 无法用 hosts 通配；Vercel 模式收益通常小于 Cloudflare。
- hosts 只能优化域名解析，无法绕过带宽限制、丢包或代理/VPN 的影响。

## SHA256

```
39B734C4FC9128D5BE0FA3DF6D7B63C977355E02C744700C57ADA7AC20203D24  hosts-opt-1.0.0-windows-x64.zip
```
