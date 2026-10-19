#!/usr/bin/env python3
"""hosts-opt: 拉取多个 GitHub hosts 源，本地 TLS 测速，生成并可选写入系统 hosts。

只用 Python 标准库，Python 3.10+。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

IS_FROZEN = bool(getattr(sys, "frozen", False))
if IS_FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = APP_DIR

ROOT = APP_DIR
DEFAULT_CONFIG = APP_DIR / "config.json"
if not DEFAULT_CONFIG.exists():
    DEFAULT_CONFIG = BUNDLE_DIR / "config.json"
DIST_DIR = APP_DIR / "dist"
CACHE_DIR = APP_DIR / "cache"
LOG_DIR = APP_DIR / "logs"
BACKUP_DIR = APP_DIR / "backups"
LOCK_PATH = APP_DIR / ".hosts-opt.lock"
LOCK_STALE_SECONDS = 900

RUNTIME_FILES = (
    "config.json",
    "install-task.ps1",
    "uninstall-task.ps1",
    "run-hidden.vbs",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)


def ensure_runtime_files() -> None:
    """首次运行 onefile EXE 时，把随包资源复制到 EXE 同目录，便于用户编辑和计划任务使用。"""
    global DEFAULT_CONFIG
    if BUNDLE_DIR == APP_DIR:
        return
    for name in RUNTIME_FILES:
        source = BUNDLE_DIR / name
        target = APP_DIR / name
        if source.exists() and not target.exists():
            try:
                shutil.copy2(source, target)
            except OSError:
                pass
    app_config = APP_DIR / "config.json"
    if app_config.exists():
        DEFAULT_CONFIG = app_config

BEGIN_MARK = "# >>> hosts-opt managed block begin >>>"
END_MARK = "# <<< hosts-opt managed block end <<<"
MANAGED_RE = re.compile(
    re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK), re.DOTALL
)
IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
LOG = logging.getLogger("hosts-opt")
PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)

DEFAULTS = {
    "sources": [],
    "ip_pools": [],
    "doh": [],
    "domains": [],
    "port": 443,
    "fetch_timeout": 15,
    "doh_timeout": 4,
    "connect_timeout": 2.0,
    "samples": 3,
    "concurrency": 24,
    "max_candidates_per_domain": 15,
    "doh_min_candidates": 2,
    "enable_doh": True,
    "enable_ipv6": False,
    "use_system_hosts": True,
    "keep_previous_on_failure": True,
    "allow_untested_fallback": True,
    "sticky_previous": True,
    "switch_margin_ratio": 0.25,
    "switch_margin_ms": 15,
    "hosts_path_windows": r"C:\Windows\System32\drivers\etc\hosts",
    "hosts_path_posix": "/etc/hosts",
    "backup_keep": 10,
    "log_max_bytes": 1048576,
    "log_backup_count": 3,
}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _read_text(path: Path) -> str:
    try:
        with open(
            path, "r", encoding="utf-8-sig", errors="replace", newline=""
        ) as handle:
            return handle.read()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        LOG.debug("读取 %s 失败: %s", path, exc)
        return ""


def _read_text_strict(path: Path) -> str:
    try:
        with open(
            path, "r", encoding="utf-8-sig", errors="replace", newline=""
        ) as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def _with_newlines(text: str, newline: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text if newline == "\n" else text.replace("\n", newline)


def _hosts_newline() -> str:
    return "\r\n" if IS_WINDOWS else "\n"


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "source"


def setup_logging(cfg: dict, verbose: bool) -> None:
    LOG.setLevel(logging.DEBUG)
    LOG.propagate = False
    for handler in list(LOG.handlers):
        LOG.removeHandler(handler)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / "hosts_opt.log",
        maxBytes=int(cfg.get("log_max_bytes", 1048576)),
        backupCount=int(cfg.get("log_backup_count", 3)),
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    LOG.addHandler(file_handler)

    # pythonw.exe 下 sys.stdout 为 None，只写日志文件。
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.DEBUG if verbose else logging.INFO)
        console.setFormatter(fmt)
        LOG.addHandler(console)


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    cfg = {**DEFAULTS, **data}

    domains = cfg.get("domains")
    if not isinstance(domains, list) or not domains:
        raise ValueError("config.json 的 domains 不能为空")
    cleaned = []
    for item in domains:
        host = str(item).strip().lower().rstrip(".")
        if host and host not in cleaned:
            cleaned.append(host)
    cfg["domains"] = cleaned

    sources = cfg.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("config.json 的 sources 不能为空")
    for source in sources:
        if not isinstance(source, dict) or not source.get("url"):
            raise ValueError("每个 source 都需要 url 字段")
        source.setdefault("name", source["url"])

    pools = cfg.get("ip_pools") or []
    if not isinstance(pools, list):
        raise ValueError("config.json 的 ip_pools 必须是数组")
    normalized_pools = []
    for pool in pools:
        if not isinstance(pool, dict):
            raise ValueError("每个 ip_pool 必须是对象")
        if not pool.get("url") and not pool.get("ips"):
            raise ValueError("每个 ip_pool 需要 url 或 ips 字段")
        pool = dict(pool)
        pool.setdefault("name", pool.get("url") or "static-pool")
        pool.setdefault("enabled", True)
        pool.setdefault("max_candidates", 20)
        pool.setdefault("ips", [])
        if not isinstance(pool["ips"], list):
            raise ValueError(f"ip_pool {pool['name']} 的 ips 必须是数组")
        pool_domains = pool.get("domains")
        if not isinstance(pool_domains, list) or not pool_domains:
            raise ValueError(f"ip_pool {pool['name']} 的 domains 不能为空")
        cleaned_pool_domains = []
        for item in pool_domains:
            host = str(item).strip().lower().rstrip(".")
            if host and host not in cleaned_pool_domains:
                cleaned_pool_domains.append(host)
        if not cleaned_pool_domains:
            raise ValueError(f"ip_pool {pool['name']} 的 domains 不能为空")
        pool["domains"] = cleaned_pool_domains
        normalized_pools.append(pool)
    cfg["ip_pools"] = normalized_pools
    return cfg


def http_get(url: str, timeout: float, headers: dict | None = None) -> str:
    def open_url(use_proxy: bool) -> str:
        if use_proxy:
            opener = urllib.request.build_opener()
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            url, headers={"User-Agent": "hosts-opt/1.0", **(headers or {})}
        )
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    try:
        return open_url(use_proxy=True)
    except Exception:
        # 环境里配置了代理但代理不可用时，尝试直连一次。
        if not any(os.environ.get(key) for key in PROXY_ENV_KEYS):
            raise
        LOG.debug("经代理请求失败，尝试直连: %s", url)
        return open_url(use_proxy=False)


# --------------------------------------------------------------------------
# 解析与候选收集
# --------------------------------------------------------------------------

def parse_hosts_text(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if not text:
        return entries
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            ip = ipaddress.ip_address(parts[0])
        except ValueError:
            continue
        for host in parts[1:]:
            host = host.lower().rstrip(".")
            if host:
                entries.append((str(ip), host))
    return entries


@dataclass
class Candidate:
    ip: str
    origins: set[str] = field(default_factory=set)


def add_candidate(
    candidates: dict[str, dict[str, Candidate]],
    host: str,
    ip: str,
    origin: str,
    cfg: dict,
) -> None:
    if host not in candidates:
        return
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return
    if address.version == 6 and not cfg.get("enable_ipv6", False):
        return
    if not address.is_global:
        return
    canonical = str(address)
    candidate = candidates[host].setdefault(canonical, Candidate(canonical))
    candidate.origins.add(origin)


@dataclass
class SourceResult:
    name: str
    text: str | None
    status: str
    error: str | None = None


def fetch_sources(cfg: dict, offline: bool) -> dict[str, SourceResult]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_one(source: dict) -> SourceResult:
        name = str(source.get("name") or source.get("url"))
        url = str(source.get("url") or "")
        cache_file = CACHE_DIR / f"source-{_slug(name)}.txt"

        if not url:
            return SourceResult(name, None, "INVALID", "缺少 URL")
        if offline:
            if cache_file.exists():
                return SourceResult(name, _read_text(cache_file), "CACHE", None)
            return SourceResult(name, None, "MISSING", "离线模式且无缓存")

        try:
            text = http_get(url, float(cfg.get("fetch_timeout", 15)))
            if len(parse_hosts_text(text)) < 5:
                raise ValueError("响应不是有效的 hosts 内容")
            _write_text(cache_file, text)
            return SourceResult(name, text, "ONLINE", None)
        except Exception as exc:
            if cache_file.exists():
                LOG.warning("源 %s 拉取失败，使用旧缓存: %s", name, exc)
                return SourceResult(name, _read_text(cache_file), "STALE-CACHE", str(exc))
            return SourceResult(name, None, "FAILED", str(exc))

    sources = cfg["sources"]
    workers = max(1, min(8, len(sources)))
    if offline:
        results = [fetch_one(source) for source in sources]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(fetch_one, sources))
    return {result.name: result for result in results}


@dataclass
class PoolResult:
    name: str
    ips: list[str]
    status: str
    error: str | None = None


def parse_ip_list(text: str, allow_ipv6: bool = False) -> list[str]:
    """解析纯 IP 列表，兼容每行带 #地区 标记的格式。"""
    ips: list[str] = []
    if not text:
        return ips
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        token = line.split()[0].rstrip(",").strip()
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if address.version == 6 and not allow_ipv6:
            continue
        if not address.is_global:
            continue
        canonical = str(address)
        if canonical not in ips:
            ips.append(canonical)
    return ips


def _merge_ips(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for ip in group:
            if ip not in merged:
                merged.append(ip)
    return merged


def fetch_pools(
    pools: list[dict], cfg: dict, offline: bool
) -> dict[str, PoolResult]:
    if not pools:
        return {}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    allow_ipv6 = bool(cfg.get("enable_ipv6", False))

    def fetch_one(pool: dict) -> PoolResult:
        name = str(pool.get("name") or pool.get("url") or "static-pool")
        url = str(pool.get("url") or "")
        static_ips = parse_ip_list(
            "\n".join(str(item) for item in pool.get("ips", [])),
            allow_ipv6,
        )
        cache_file = CACHE_DIR / f"pool-{_slug(name)}.txt"

        if not url:
            if static_ips:
                return PoolResult(name, static_ips, "STATIC", None)
            return PoolResult(name, [], "INVALID", "缺少 URL 或 ips")

        if offline:
            cached_ips: list[str] = []
            if cache_file.exists():
                cached_ips = parse_ip_list(_read_text(cache_file), allow_ipv6)
            merged = _merge_ips(cached_ips, static_ips)
            if merged:
                return PoolResult(
                    name,
                    merged,
                    "CACHE" if cached_ips else "STATIC",
                    None,
                )
            return PoolResult(name, [], "MISSING", "离线模式且无缓存")

        try:
            text = http_get(url, float(cfg.get("fetch_timeout", 15)))
            ips = parse_ip_list(text, allow_ipv6)
            if not ips:
                raise ValueError("未解析到有效 IP")
            _write_text(cache_file, text)
            return PoolResult(name, _merge_ips(ips, static_ips), "ONLINE", None)
        except Exception as exc:
            cached_ips = []
            if cache_file.exists():
                cached_ips = parse_ip_list(_read_text(cache_file), allow_ipv6)
            merged = _merge_ips(cached_ips, static_ips)
            if merged:
                LOG.warning(
                    "IP 池 %s 拉取失败，使用旧缓存/静态 IP: %s", name, exc
                )
                return PoolResult(
                    name,
                    merged,
                    "STALE-CACHE" if cached_ips else "STATIC",
                    str(exc),
                )
            return PoolResult(name, [], "FAILED", str(exc))

    workers = max(1, min(8, len(pools)))
    if offline:
        results = [fetch_one(pool) for pool in pools]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(fetch_one, pools))
    return {result.name: result for result in results}


def _doh_worker(domain: str, cfg: dict) -> list[tuple[str, str, str]]:
    providers = cfg.get("doh") or []
    timeout = float(cfg.get("doh_timeout", 4))
    ipv6 = bool(cfg.get("enable_ipv6", False))
    qtype = "AAAA" if ipv6 else "A"
    answer_type = 28 if ipv6 else 1
    found: list[tuple[str, str, str]] = []

    for provider in providers:
        separator = "&" if "?" in provider else "?"
        url = f"{provider}{separator}" + urllib.parse.urlencode(
            {"name": domain, "type": qtype}
        )
        try:
            payload = http_get(url, timeout, {"Accept": "application/dns-json"})
            data = json.loads(payload)
            for answer in data.get("Answer") or []:
                if answer.get("type") != answer_type:
                    continue
                value = str(answer.get("data", "")).strip()
                if value:
                    provider_name = urllib.parse.urlsplit(provider).hostname or provider
                    found.append((domain, value, f"DoH:{provider_name}"))
        except Exception as exc:
            LOG.debug("DoH %s @ %s 失败: %s", domain, provider, exc)
    return found


def run_doh(domains: list[str], cfg: dict) -> list[tuple[str, str, str]]:
    if not domains:
        return []
    results: list[tuple[str, str, str]] = []
    workers = max(1, min(8, len(domains)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_doh_worker, domain, cfg) for domain in domains]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.extend(future.result())
            except Exception:
                LOG.debug("DoH 任务异常", exc_info=True)
    return results


def candidate_cache_path() -> Path:
    return CACHE_DIR / "candidates.json"


def load_candidate_cache() -> dict:
    try:
        data = json.loads(candidate_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        LOG.debug("读取候选缓存失败: %s", exc)
        return {}


def save_candidate_cache(candidates: dict[str, dict[str, Candidate]]) -> None:
    try:
        data: dict[str, dict[str, list[str]]] = {}
        for domain, items in candidates.items():
            entries: dict[str, list[str]] = {}
            for ip, candidate in items.items():
                # 只持久化源站/DoH 候选；previous/system/cache 每轮会重新读取。
                persistent = sorted(
                    origin
                    for origin in candidate.origins
                    if origin not in {"previous", "system", "cache"}
                )
                if persistent:
                    entries[ip] = persistent
            if entries:
                data[domain] = entries
        _write_text(
            candidate_cache_path(), json.dumps(data, ensure_ascii=False, indent=2)
        )
    except Exception as exc:
        LOG.warning("写入候选缓存失败: %s", exc)


def managed_entries(text: str) -> dict[str, str]:
    match = MANAGED_RE.search(text)
    if not match:
        return {}
    entries: dict[str, str] = {}
    for ip, host in parse_hosts_text(match.group(0)):
        entries.setdefault(host, ip)
    return entries


# --------------------------------------------------------------------------
# 本地 TLS 测速
# --------------------------------------------------------------------------

@dataclass
class Probe:
    domain: str
    ip: str
    ok: bool
    median_ms: float | None
    min_ms: float | None
    successes: int
    attempts: int
    errors: list[str] = field(default_factory=list)


_TLS_CONTEXT: ssl.SSLContext | None = None


def tls_context() -> ssl.SSLContext:
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        _TLS_CONTEXT = context
    return _TLS_CONTEXT


def probe_one(domain: str, ip: str, cfg: dict) -> Probe:
    port = int(cfg.get("port", 443))
    timeout = float(cfg.get("connect_timeout", 2.0))
    samples = max(1, int(cfg.get("samples", 3)))
    times: list[float] = []
    errors: list[str] = []
    attempts = 0

    for index in range(samples):
        attempts += 1
        started = time.perf_counter()
        try:
            with socket.create_connection((ip, port), timeout=timeout) as raw:
                raw.settimeout(timeout)
                with tls_context().wrap_socket(raw, server_hostname=domain):
                    pass
            times.append((time.perf_counter() - started) * 1000.0)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempts - len(times) >= 2:
                break
        if index + 1 < samples:
            time.sleep(0.05)

    if not times:
        return Probe(domain, ip, False, None, None, 0, attempts, errors)
    return Probe(
        domain=domain,
        ip=ip,
        ok=True,
        median_ms=statistics.median(times),
        min_ms=min(times),
        successes=len(times),
        attempts=attempts,
        errors=errors,
    )


def probe_rank(probe: Probe) -> tuple:
    """优先选全部采样成功的 IP，其次成功次数多、延迟低。"""
    return (
        0 if probe.successes == probe.attempts else 1,
        -probe.successes,
        probe.median_ms if probe.median_ms is not None else float("inf"),
        probe.min_ms if probe.min_ms is not None else float("inf"),
    )


def run_probes(
    candidates: dict[str, list[Candidate]], cfg: dict
) -> dict[str, list[Probe]]:
    tasks = [
        (domain, candidate.ip)
        for domain, items in candidates.items()
        for candidate in items
    ]
    results: dict[str, list[Probe]] = {domain: [] for domain in candidates}
    if not tasks:
        return results

    workers = max(1, min(int(cfg.get("concurrency", 24)), len(tasks)))
    finished = 0
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(probe_one, domain, ip, cfg): domain
            for domain, ip in tasks
        }
        for future in concurrent.futures.as_completed(futures):
            finished += 1
            try:
                probe = future.result()
            except Exception as exc:
                LOG.debug("测速任务异常: %s", exc)
                continue
            results[probe.domain].append(probe)
            if finished % 25 == 0:
                LOG.info("测速进度 %d/%d", finished, len(tasks))

    for domain in results:
        results[domain].sort(key=probe_rank)
    LOG.info("测速完成，用时 %.1f 秒", time.perf_counter() - started)
    return results


@dataclass
class Selection:
    domain: str
    ip: str
    kind: str
    median_ms: float | None
    successes: int
    attempts: int
    origins: list[str]


def choose_ip(
    domain: str,
    items: list[Candidate],
    probes: list[Probe],
    previous: dict[str, str],
    cfg: dict,
) -> Selection | None:
    usable = sorted(
        (probe for probe in probes if probe.ok),
        key=probe_rank,
    )
    if usable:
        best = usable[0]
        previous_ip = previous.get(domain)
        if cfg.get("sticky_previous", True) and previous_ip:
            previous_probe = next(
                (probe for probe in usable if probe.ip == previous_ip), None
            )
            if previous_probe and previous_probe.successes >= min(
                2, previous_probe.attempts
            ):
                margin = best.median_ms * (
                    1 + float(cfg.get("switch_margin_ratio", 0.25))
                ) + float(cfg.get("switch_margin_ms", 15))
                if previous_probe.median_ms <= margin:
                    best = previous_probe
        origins: list[str] = []
        for candidate in items:
            if candidate.ip == best.ip:
                origins = sorted(candidate.origins)
                break
        return Selection(
            domain=domain,
            ip=best.ip,
            kind="measured",
            median_ms=best.median_ms,
            successes=best.successes,
            attempts=best.attempts,
            origins=origins or ["source"],
        )

    if cfg.get("keep_previous_on_failure", True) and domain in previous:
        return Selection(
            domain=domain,
            ip=previous[domain],
            kind="previous",
            median_ms=None,
            successes=0,
            attempts=0,
            origins=["previous"],
        )

    if cfg.get("allow_untested_fallback", True) and items:
        return Selection(
            domain=domain,
            ip=items[0].ip,
            kind="untested",
            median_ms=None,
            successes=0,
            attempts=0,
            origins=sorted(items[0].origins),
        )
    return None


# --------------------------------------------------------------------------
# 生成与写入 hosts
# --------------------------------------------------------------------------

def build_block(selections: dict[str, Selection], cfg: dict, generated_at: str) -> str:
    lines = [
        BEGIN_MARK,
        f"# generated {generated_at} by hosts-opt (local TLS benchmark)",
    ]
    for domain in cfg["domains"]:
        selection = selections.get(domain)
        if not selection:
            continue
        if selection.kind == "measured" and selection.median_ms is not None:
            origins = ",".join(selection.origins)
            lines.append(
                f"# {domain} -> {selection.median_ms:.1f}ms "
                f"({selection.successes}/{selection.attempts} ok; {origins})"
            )
        else:
            lines.append(f"# {domain} -> kept {selection.kind} ip (no successful probe)")
        lines.append(f"{selection.ip:<17} {domain}")
    lines.append(END_MARK)
    return "\n".join(lines) + "\n"


def replace_managed_block(text: str, block_text: str, newline: str) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    inside = False
    replaced = False

    for line in lines:
        stripped = line.strip()
        if not inside and stripped == BEGIN_MARK:
            if not replaced:
                output.append(block_text)
                replaced = True
            inside = True
            continue
        if inside:
            if stripped == END_MARK:
                inside = False
            continue
        output.append(line)

    if inside:
        raise ValueError("hosts 中的托管块缺少结束标记，已中止以避免损坏文件")

    if not replaced:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += newline
        if output and output[-1].strip():
            output.append(newline)
        output.append(block_text)
    return "".join(output)


def strip_managed_block(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    inside = False
    found = False

    for line in lines:
        stripped = line.strip()
        if not inside and stripped == BEGIN_MARK:
            inside = True
            found = True
            continue
        if inside:
            if stripped == END_MARK:
                inside = False
            continue
        output.append(line)

    if inside:
        raise ValueError("hosts 中的托管块缺少结束标记，已中止以避免损坏文件")
    result = "".join(output)
    newline = "\r\n" if "\r\n" in result else "\n"
    result = re.sub(r"(?:\r?\n){3,}", newline + newline, result)
    return result, found


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def default_hosts_path(cfg: dict) -> Path:
    raw = cfg.get("hosts_path_windows") if IS_WINDOWS else cfg.get("hosts_path_posix")
    return Path(raw)


def resolve_hosts_path(cfg: dict, override: Path | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return default_hosts_path(cfg)


def is_system_hosts(path: Path, cfg: dict) -> bool:
    return os.path.normcase(os.path.abspath(str(path))) == os.path.normcase(
        os.path.abspath(str(default_hosts_path(cfg)))
    )


@dataclass
class BackupInfo:
    path: Path
    modified: str
    size: int
    is_original: bool
    mtime: float


def original_backup_path(path: Path) -> Path:
    return BACKUP_DIR / f"{path.name}.original.bak"


_BACKUP_STAMP_RE = re.compile(r"\.(\d{8})-(\d{6})(?:-\d+)?\.bak$")


def _backup_created_at(path: Path) -> float:
    """优先用文件名里的备份时间；原始备份没有时间戳时退回文件 mtime。"""
    match = _BACKUP_STAMP_RE.search(path.name)
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)}-{match.group(2)}", "%Y%m%d-%H%M%S"
            ).timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _ensure_original_backup(path: Path) -> Path | None:
    original = original_backup_path(path)
    if original.exists():
        return original
    if not path.exists():
        return None
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        candidates = sorted(
            (
                item
                for item in BACKUP_DIR.glob(f"{path.name}.*.bak")
                if item.name != original.name
            ),
            key=lambda item: _backup_created_at(item),
        )
        source = candidates[0] if candidates else path
        if BEGIN_MARK in _read_text(source):
            LOG.warning(
                "原始备份来源已包含托管块，可能不是真正的初始 hosts: %s", source
            )
        shutil.copy2(source, original)
        return original
    except OSError as exc:
        LOG.warning("创建原始 hosts 备份失败: %s", exc)
        return None


def list_backups(path: Path) -> list[BackupInfo]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    original = original_backup_path(path)
    items: list[BackupInfo] = []
    for item in BACKUP_DIR.glob(f"{path.name}.*.bak"):
        if not item.is_file():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        created = _backup_created_at(item)
        items.append(
            BackupInfo(
                path=item,
                modified=datetime.fromtimestamp(created).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                size=stat.st_size,
                is_original=item.name == original.name,
                mtime=created,
            )
        )
    items.sort(key=lambda info: (not info.is_original, -info.mtime))
    return items


def backup_hosts(path: Path, keep: int) -> Path | None:
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_original_backup(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = BACKUP_DIR / f"{path.name}.{stamp}.bak"
    counter = 1
    while destination.exists():
        destination = BACKUP_DIR / f"{path.name}.{stamp}-{counter}.bak"
        counter += 1
    try:
        shutil.copy2(path, destination)
    except OSError as exc:
        LOG.warning("备份 hosts 失败: %s", exc)
        return None

    original = original_backup_path(path)
    backups = sorted(
        (
            item
            for item in BACKUP_DIR.glob(f"{path.name}.*.bak")
            if item.name != original.name
        ),
        key=lambda item: _backup_created_at(item),
        reverse=True,
    )
    for old in backups[max(1, keep):]:
        try:
            old.unlink()
        except OSError:
            pass
    return destination


def restore_backup(path: Path, backup_path: Path, cfg: dict) -> Path | None:
    if is_system_hosts(path, cfg) and not is_admin():
        raise PermissionError("恢复系统 hosts 需要管理员权限，请用管理员终端运行")

    backup_root = BACKUP_DIR.resolve()
    try:
        backup_path = backup_path.resolve()
    except OSError as exc:
        raise ValueError(f"无法解析备份路径: {exc}") from exc
    if not backup_path.is_file():
        raise FileNotFoundError(f"备份文件不存在: {backup_path}")
    if backup_root not in backup_path.parents:
        raise ValueError("备份文件必须位于 backups 目录")
    data = backup_path.read_bytes()

    current_backup = None
    if path.exists():
        current_backup = backup_hosts(path, int(cfg.get("backup_keep", 10)))
        if current_backup is None:
            raise PermissionError("无法备份当前 hosts，已中止回退")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        raise PermissionError(f"写入 {path} 失败: {exc}") from exc
    if is_system_hosts(path, cfg):
        flush_dns()
    return current_backup


def apply_block(path: Path, block: str, cfg: dict) -> bool:
    if is_system_hosts(path, cfg) and not is_admin():
        raise PermissionError("写入系统 hosts 需要管理员权限，请用管理员终端运行")

    try:
        original = _read_text_strict(path)
    except OSError as exc:
        raise PermissionError(f"无法读取 {path}: {exc}") from exc

    newline = _hosts_newline()
    new_content = replace_managed_block(
        original, _with_newlines(block, newline), newline
    )
    if new_content == original:
        return False

    if path.exists():
        backup = backup_hosts(path, int(cfg.get("backup_keep", 10)))
        if backup:
            LOG.info("已备份原 hosts 到 %s", backup)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_content)
    return True


def flush_dns() -> None:
    if IS_WINDOWS:
        commands = [["ipconfig", "/flushdns"]]
    elif IS_MACOS:
        commands = [["killall", "-HUP", "mDNSResponder"]]
    else:
        commands = [
            ["resolvectl", "flush-caches"],
            ["systemd-resolve", "--flush-caches"],
            ["service", "nscd", "restart"],
        ]

    for command in commands:
        try:
            kwargs = {"capture_output": True, "timeout": 20}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(command, **kwargs)
            if result.returncode == 0:
                LOG.info("已刷新 DNS 缓存: %s", " ".join(command))
                return
        except (OSError, subprocess.SubprocessError):
            continue
    LOG.warning("未能自动刷新 DNS 缓存，请手动刷新后重试")


# --------------------------------------------------------------------------
# 命令实现
# --------------------------------------------------------------------------

@contextmanager
def update_lock():
    """同一时间只允许一个实例拉取/测速/写入，避免 GUI 与计划任务互相覆盖。"""
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(f"{os.getpid()}\n")
            break
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
            except OSError:
                age = 0
            if age > LOCK_STALE_SECONDS:
                try:
                    LOCK_PATH.unlink()
                except OSError:
                    pass
                continue
            raise RuntimeError(
                f"另一个 hosts-opt 实例正在运行（锁文件: {LOCK_PATH}）"
            )
    try:
        yield
    finally:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


def with_update_lock(func):
    @wraps(func)
    def wrapper(args: argparse.Namespace, cfg: dict) -> int:
        try:
            context = update_lock()
            context.__enter__()
        except RuntimeError as exc:
            LOG.error("%s", exc)
            return 3
        try:
            return func(args, cfg)
        finally:
            context.__exit__(None, None, None)

    return wrapper


@with_update_lock
def cmd_update(args: argparse.Namespace, cfg: dict) -> int:
    if args.no_doh:
        cfg["enable_doh"] = False

    base_domain_count = len(cfg["domains"])
    enabled_overrides = {
        str(name) for name in getattr(args, "enable_pool", []) or []
    }
    active_pools = []
    if not getattr(args, "no_pools", False):
        known_pool_names = {
            str(pool.get("name") or "") for pool in cfg.get("ip_pools", [])
        }
        for pool in cfg.get("ip_pools", []):
            name = str(pool.get("name") or "")
            if pool.get("enabled", True) or name in enabled_overrides:
                active_pools.append(pool)
        for name in sorted(enabled_overrides - known_pool_names):
            LOG.warning("未找到要启用的 IP 池: %s", name)
    effective_domains = list(cfg["domains"])
    for pool in active_pools:
        for domain in pool["domains"]:
            if domain not in effective_domains:
                effective_domains.append(domain)
    cfg = {**cfg, "domains": effective_domains}

    hosts_path = resolve_hosts_path(cfg, args.hosts_path)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    LOG.info("hosts-opt 开始: %s", generated_at)
    LOG.info("目标 hosts: %s", hosts_path)

    sources = fetch_sources(cfg, args.offline)
    for name, result in sources.items():
        detail = f" ({result.error})" if result.error else ""
        LOG.info("源 %-12s %s%s", name, result.status, detail)

    if active_pools:
        pool_results = fetch_pools(active_pools, cfg, args.offline)
        for name, result in pool_results.items():
            detail = f" ({result.error})" if result.error else ""
            LOG.info(
                "IP 池 %-10s %s（%d 个候选）%s",
                name,
                result.status,
                len(result.ips),
                detail,
            )
        if len(cfg["domains"]) > base_domain_count:
            LOG.info(
                "IP 池域名: %d 个", len(cfg["domains"]) - base_domain_count
            )
    else:
        pool_results = {}
        LOG.info("IP 池: 未启用")

    candidates: dict[str, dict[str, Candidate]] = {
        domain: {} for domain in cfg["domains"]
    }

    for source in cfg["sources"]:
        result = sources.get(str(source.get("name") or source.get("url")))
        if not result or not result.text:
            continue
        for ip, host in parse_hosts_text(result.text):
            add_candidate(candidates, host, ip, result.name, cfg)

    for pool in active_pools:
        result = pool_results.get(str(pool["name"]))
        if not result or not result.ips:
            continue
        limit = max(1, int(pool.get("max_candidates", 20)))
        origin = f"pool:{result.name}"
        for domain in pool["domains"]:
            for ip in result.ips[:limit]:
                add_candidate(candidates, domain, ip, origin, cfg)

    existing_text = _read_text(hosts_path)
    previous = managed_entries(existing_text)
    for host, ip in previous.items():
        add_candidate(candidates, host, ip, "previous", cfg)

    if cfg.get("use_system_hosts", True):
        for ip, host in parse_hosts_text(existing_text):
            add_candidate(candidates, host, ip, "system", cfg)

    for host, ips in load_candidate_cache().items():
        if not isinstance(ips, dict):
            continue
        for ip, stored_origins in ips.items():
            add_candidate(candidates, host, ip, "cache", cfg)
            if isinstance(stored_origins, list):
                for origin in stored_origins:
                    if isinstance(origin, str):
                        add_candidate(candidates, host, ip, origin, cfg)

    if cfg.get("enable_doh", True) and not args.offline:
        pool_domains = {
            domain for pool in active_pools for domain in pool["domains"]
        }
        need_doh = [
            domain
            for domain in cfg["domains"]
            if domain in pool_domains
            or len(candidates[domain]) < int(cfg.get("doh_min_candidates", 2))
        ]
        if need_doh:
            for host, ip, origin in run_doh(need_doh, cfg):
                add_candidate(candidates, host, ip, origin, cfg)

    limit = max(1, int(cfg.get("max_candidates_per_domain", 10)))
    selected_candidates: dict[str, list[Candidate]] = {}
    for domain in cfg["domains"]:
        items = list(candidates[domain].values())[:limit]
        selected_candidates[domain] = items
        if not items:
            LOG.warning("域名 %s 没有可用候选 IP", domain)

    save_candidate_cache(candidates)

    total = sum(len(items) for items in selected_candidates.values())
    LOG.info(
        "共 %d 个候选 IP，开始本地 TLS 测速（并发 %s，每 IP %s 次采样）",
        total,
        cfg.get("concurrency", 24),
        cfg.get("samples", 3),
    )
    probes = run_probes(selected_candidates, cfg)

    selections: dict[str, Selection] = {}
    for domain in cfg["domains"]:
        selection = choose_ip(
            domain,
            selected_candidates[domain],
            probes.get(domain, []),
            previous,
            cfg,
        )
        if selection:
            selections[domain] = selection
        else:
            LOG.warning("域名 %s 测速全部失败且无旧 IP，跳过", domain)

    if args.verbose:
        for domain in cfg["domains"]:
            LOG.info("[%s] 候选:", domain)
            for probe in probes.get(domain, [])[: max(1, args.top)]:
                if probe.ok:
                    LOG.info(
                        "    %-16s %8.1f ms  %d/%d 成功  %s",
                        probe.ip,
                        probe.median_ms,
                        probe.successes,
                        probe.attempts,
                        " | ".join(probe.errors[:1]),
                    )
                else:
                    LOG.info(
                        "    %-16s %8s    0/%d 成功  %s",
                        probe.ip,
                        "FAIL",
                        probe.attempts,
                        " | ".join(probe.errors[:1]),
                    )

    if not selections:
        LOG.error("没有任何可用条目，保留现有 hosts 不变")
        return 1

    block = build_block(selections, cfg, generated_at)
    _write_text(DIST_DIR / "hosts.generated", _with_newlines(block, _hosts_newline()))
    LOG.info("已生成 %s（%d 个域名）", DIST_DIR / "hosts.generated", len(selections))

    applied = False
    apply_error: str | None = None
    apply_exit = 0
    selected_map = {domain: selection.ip for domain, selection in selections.items()}
    if args.apply:
        try:
            if selected_map == previous:
                applied = True
                LOG.info("IP 与现有托管块一致，跳过写入")
            else:
                changed = apply_block(hosts_path, block, cfg)
                applied = True
                if changed:
                    if is_system_hosts(hosts_path, cfg):
                        flush_dns()
                    LOG.info("已写入 %s", hosts_path)
                else:
                    LOG.info("hosts 内容无变化，未写入")
        except PermissionError as exc:
            apply_error = str(exc)
            apply_exit = 2
            LOG.error("写入失败: %s", exc)
        except Exception as exc:
            apply_error = str(exc)
            apply_exit = 1
            LOG.error("写入失败: %s", exc)
    else:
        LOG.info("当前为预览模式，未写入 hosts；加 --apply 并在管理员终端运行即可应用")

    pool_report = {}
    for pool in active_pools:
        result = pool_results.get(str(pool["name"]))
        pool_report[str(pool["name"])] = {
            "domains": pool["domains"],
            "status": result.status if result else "MISSING",
            "ip_count": len(result.ips) if result else 0,
            "error": result.error if result else None,
        }

    report = {
        "generated_at": generated_at,
        "hosts_path": str(hosts_path),
        "applied": applied,
        "apply_error": apply_error,
        "sources": {
            name: {"status": result.status, "error": result.error}
            for name, result in sources.items()
        },
        "pools": pool_report,
        "candidates": {
            domain: [
                {"ip": candidate.ip, "origins": sorted(candidate.origins)}
                for candidate in items
            ]
            for domain, items in selected_candidates.items()
        },
        "probes": {
            domain: [asdict(probe) for probe in items]
            for domain, items in probes.items()
        },
        "selected": {
            domain: asdict(selection) for domain, selection in selections.items()
        },
    }
    _write_text(
        DIST_DIR / "report.json", json.dumps(report, ensure_ascii=False, indent=2)
    )

    LOG.info("")
    LOG.info("%-32s %-16s %10s %8s  %s", "domain", "ip", "median", "ok", "source")
    for domain in cfg["domains"]:
        selection = selections.get(domain)
        if not selection:
            LOG.info("%-32s %-16s %10s %8s  %s", domain, "-", "-", "-", "skipped")
            continue
        median = (
            f"{selection.median_ms:.1f}ms"
            if selection.median_ms is not None
            else selection.kind
        )
        ok = (
            f"{selection.successes}/{selection.attempts}"
            if selection.attempts
            else "-"
        )
        LOG.info(
            "%-32s %-16s %10s %8s  %s",
            domain,
            selection.ip,
            median,
            ok,
            ",".join(selection.origins),
        )

    if not applied:
        LOG.info("")
        if getattr(sys, "frozen", False):
            LOG.info("应用命令: hosts-opt.exe --cli update --apply")
        else:
            LOG.info(
                "应用命令（管理员终端）: python \"%s\" update --apply",
                Path(__file__).resolve(),
            )
    return apply_exit


@with_update_lock
def cmd_backup(args: argparse.Namespace, cfg: dict) -> int:
    hosts_path = resolve_hosts_path(cfg, args.hosts_path)
    if not hosts_path.exists():
        LOG.error("hosts 文件不存在: %s", hosts_path)
        return 1
    backup = backup_hosts(hosts_path, int(cfg.get("backup_keep", 10)))
    if not backup:
        LOG.error("备份失败")
        return 1
    original = original_backup_path(hosts_path)
    LOG.info("已备份 %s 到 %s", hosts_path, backup)
    if original.exists():
        LOG.info("原始备份: %s", original)
    return 0


@with_update_lock
def cmd_rollback(args: argparse.Namespace, cfg: dict) -> int:
    hosts_path = resolve_hosts_path(cfg, args.hosts_path)
    if getattr(args, "list", False):
        backups = list_backups(hosts_path)
        if not backups:
            LOG.info("没有备份文件")
            return 0
        for item in backups:
            kind = "原始" if item.is_original else "自动"
            LOG.info(
                "%s  %s  %8.1f KB  %s",
                kind,
                item.modified,
                item.size / 1024,
                item.path,
            )
        return 0

    backup_path = getattr(args, "backup", None)
    if backup_path is None:
        backup_path = _ensure_original_backup(hosts_path)
        if backup_path is None:
            LOG.error("没有可用的原始备份，请先运行 backup 或指定 --backup")
            return 1
    backup_path = Path(backup_path)
    if not backup_path.exists():
        LOG.error("备份文件不存在: %s", backup_path)
        return 1

    try:
        current = restore_backup(hosts_path, backup_path, cfg)
    except PermissionError as exc:
        LOG.error("%s", exc)
        return 2
    except Exception as exc:
        LOG.error("回退失败: %s", exc)
        return 1

    LOG.info("已从备份恢复 %s", backup_path)
    if current:
        LOG.info("恢复前的 hosts 已备份到 %s", current)
    return 0


@with_update_lock
def cmd_restore(args: argparse.Namespace, cfg: dict) -> int:
    hosts_path = resolve_hosts_path(cfg, args.hosts_path)
    try:
        original = _read_text_strict(hosts_path)
    except OSError as exc:
        LOG.error("无法读取 %s: %s", hosts_path, exc)
        return 1

    if is_system_hosts(hosts_path, cfg) and not is_admin():
        LOG.error("恢复系统 hosts 需要管理员权限，请用管理员终端运行")
        return 2

    new_content, found = strip_managed_block(original)
    if not found:
        LOG.info("未发现 hosts-opt 托管块，无需恢复")
        return 0

    backup = backup_hosts(hosts_path, int(cfg.get("backup_keep", 10)))
    with open(hosts_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_content)
    if is_system_hosts(hosts_path, cfg):
        flush_dns()
    LOG.info("已移除托管块%s", f"，原文件备份: {backup}" if backup else "")
    return 0


def cmd_status(args: argparse.Namespace, cfg: dict) -> int:
    target = resolve_hosts_path(cfg, args.hosts_path)
    system_path = default_hosts_path(cfg)
    LOG.info("系统 hosts: %s", system_path)
    LOG.info("检查目标: %s", target)
    LOG.info("管理员权限: %s", "是" if is_admin() else "否")

    pools = [pool for pool in cfg.get("ip_pools", []) if pool.get("enabled", True)]
    if pools:
        LOG.info(
            "IP 池: %s",
            "、".join(
                f"{pool['name']}（{len(pool['domains'])} 个域名）"
                for pool in pools
            ),
        )
    else:
        LOG.info("IP 池: 未配置或已禁用")
    disabled_pools = [
        pool for pool in cfg.get("ip_pools", []) if not pool.get("enabled", True)
    ]
    for pool in disabled_pools:
        LOG.info(
            "IP 池: %s（默认关闭，%d 个域名）",
            pool["name"],
            len(pool["domains"]),
        )

    text = _read_text(target)
    entries = managed_entries(text)
    if BEGIN_MARK in text and END_MARK in text:
        LOG.info("托管块: 存在，%d 条", len(entries))
        header = re.search(r"^# generated .*$", text, re.MULTILINE)
        if header:
            LOG.info("块信息: %s", header.group(0))
    elif BEGIN_MARK in text:
        LOG.warning("托管块: 开始标记存在但缺少结束标记")
    else:
        LOG.info("托管块: 不存在")

    generated = DIST_DIR / "hosts.generated"
    if generated.exists():
        stat = generated.stat()
        LOG.info(
            "生成文件: %s（%s，%.1f KB）",
            generated,
            datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            stat.st_size / 1024,
        )
    else:
        LOG.info("生成文件: 尚未生成")

    original = original_backup_path(target)
    backups = list_backups(target)
    LOG.info(
        "备份: %d 份%s",
        len(backups),
        f"，原始备份: {original}" if original.exists() else "，原始备份未创建",
    )

    if IS_WINDOWS:
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", "HostsOpt"],
                capture_output=True,
                timeout=10,
            )
            LOG.info("定时任务 HostsOpt: %s", "已安装" if result.returncode == 0 else "未安装")
        except (OSError, subprocess.SubprocessError):
            LOG.info("定时任务 HostsOpt: 无法查询")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="配置文件路径"
    )
    common.add_argument(
        "--hosts-path",
        type=Path,
        default=None,
        help="覆盖目标 hosts 路径（测试用；默认系统 hosts）",
    )
    common.add_argument(
        "-v", "--verbose", action="store_true", help="输出每个域名的候选测速详情"
    )

    parser = argparse.ArgumentParser(
        description="拉取 GitHub hosts 源，本地 TLS 测速，生成并可选写入系统 hosts"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    update = subparsers.add_parser(
        "update", parents=[common], help="拉取、测速并生成 hosts"
    )
    update.add_argument(
        "--apply", action="store_true", help="写入目标 hosts（系统 hosts 需要管理员）"
    )
    update.add_argument(
        "--offline", action="store_true", help="只用缓存源/IP 池，不发拉取/DoH 请求"
    )
    update.add_argument("--no-doh", action="store_true", help="禁用 DoH 候选")
    update.add_argument(
        "--no-pools",
        action="store_true",
        help="禁用 IP 池候选（默认包含 Cloudflare / Vercel 优选）",
    )
    update.add_argument(
        "--enable-pool",
        action="append",
        default=[],
        metavar="NAME",
        help="临时启用配置中 disabled 的 IP 池（可重复）",
    )
    update.add_argument(
        "--top", type=int, default=3, help="verbose 时每个域名展示的候选数"
    )
    update.set_defaults(func=cmd_update)

    restore = subparsers.add_parser(
        "restore",
        parents=[common],
        help="移除由本工具写入的托管块（保留 hosts 其他内容）",
    )
    restore.set_defaults(func=cmd_restore)

    backup = subparsers.add_parser(
        "backup", parents=[common], help="立即备份当前 hosts，并保留原始备份"
    )
    backup.set_defaults(func=cmd_backup)

    rollback = subparsers.add_parser(
        "rollback",
        parents=[common],
        help="用备份完整覆盖 hosts（默认使用原始备份）",
    )
    rollback.add_argument(
        "--list", action="store_true", help="列出可用备份，不执行回退"
    )
    rollback.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="指定 backups 目录中的备份文件；默认使用原始备份",
    )
    rollback.set_defaults(func=cmd_rollback)

    status = subparsers.add_parser(
        "status", parents=[common], help="查看 hosts、生成文件和定时任务状态"
    )
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_runtime_files()
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    setup_logging(cfg, args.verbose)
    try:
        return int(args.func(args, cfg))
    except KeyboardInterrupt:
        LOG.warning("已中断")
        return 130
    except Exception as exc:
        LOG.exception("执行失败: %s", exc)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure") and not sys.stderr.isatty():
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
