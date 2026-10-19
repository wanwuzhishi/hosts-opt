# Third-Party Notices

The hosts-opt source code is licensed under the MIT License (see `LICENSE`).

hosts-opt fetches hosts data, IP lists and status information from third-party
projects and services at runtime. The repository does not bundle their data
(`cache/`, `dist/`, `logs/` and `backups/` are ignored). When you run the tool,
the data you retrieve remains subject to the upstream project's license and
terms. Please review and support those projects.

## Data sources and services

| Project / service | How hosts-opt uses it | Upstream license | Link |
|---|---|---|---|
| GitHub520 | Runtime GitHub hosts source | README states CC BY-NC-ND 4.0; GitHub API reports no LICENSE file. Verify upstream before redistribution. | https://github.com/521xueweihan/GitHub520 |
| ineo6/hosts | Runtime GitHub hosts source | MIT | https://github.com/ineo6/hosts |
| maxiaof/github-hosts | Runtime GitHub hosts source | No license detected by GitHub API (NOASSERTION); check the repository. | https://github.com/maxiaof/github-hosts |
| ymyuuu/IPDB | Runtime Cloudflare `bestcf` IP list (optional pool, enabled by default) | MIT | https://github.com/ymyuuu/IPDB |
| NotionFaster | Optional third-party TCP reverse proxy for Notion domains. Disabled by default. | No public repository or license was found. It is a service, not bundled code. | https://notionfaster.org/ |
| Vercel official documentation | Reference for Vercel behavior and limitations | Documentation, not bundled code | https://vercel.com/kb/guide/accessing-vercel-hosted-sites-from-mainland-china |
| enhanced-FaaS-in-China | Reference for a third-party Vercel CNAME acceleration option | MIT | https://github.com/xingpingcn/enhanced-FaaS-in-China |
| CloudflareSpeedTest | Referenced as an optional throughput testing tool; no code is copied or bundled | GPL-3.0 | https://github.com/XIU2/CloudflareSpeedTest |

## Important notes

- **GitHub520**: its README describes CC BY-NC-ND 4.0 terms. hosts-opt only
  requests its published hosts file at runtime and does not redistribute it.
  Users are responsible for complying with the upstream license.
- **maxiaof/github-hosts**: no license was detected. If you plan to redistribute
  its data, check the repository first or disable that source in `config.json`.
- **NotionFaster**: enabling the optional `NotionFaster-proxy` pool sends
  Notion-related TLS connections through third-party servers. The project states
  that it forwards TCP/TLS without holding Notion's private key and cannot
  decrypt the traffic, but it can still observe connection metadata and offers
  no availability guarantee. Only enable it if you trust the service. Consider
  supporting the project: https://ifdian.net/a/notion-proxy-cn
- **Vercel**: Vercel does not provide preferred ingress IPs or China nodes.
  hosts-opt's Vercel pool uses verified Anycast addresses plus local TLS
  measurements; it is a best-effort optimization, not an official solution.

## Build tooling

The Windows executable is built with PyInstaller. PyInstaller is licensed under
GPL-2.0-or-later with a special exception for its bootloader; see
https://pyinstaller.org/en/stable/license.html. Python and Tk/Tcl are covered by
the Python Software Foundation License and the Tcl/Tk license respectively.

If an attribution is missing or incorrect, please open an issue in this
repository.
