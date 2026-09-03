#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 fthuu
"""GitHub Traffic 采集工具的公共模块。

设计约束：
    * 只使用 Python 标准库（无需 pip install），Python >= 3.8 即可运行。
    * 所有函数都是纯函数或幂等 IO，方便被 collect_traffic.py / seed_from_csv.py 复用。

提供能力：
    * 结构化日志（带 UTC 时间戳）
    * JSON 文件的读取 / 原子写入
    * 按「日期」去重合并时间序列（同一天以新数据为准）
    * GitHub REST API 请求（含超时、重试、可读的错误信息）
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

SCHEMA_VERSION = 1
API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "github-traffic-collector"

#: 模块级开关，collect_traffic.py 的 --verbose 会把它打开
VERBOSE = False

#: 仓库根目录 = scripts/ 的上一级
REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #

def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    """返回 ISO8601 UTC 字符串，例如 YYYY-MM-DDTHH:MM:SSZ。"""
    return (dt or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(level: str, msg: str) -> None:
    print(f"[{iso()}] [{level:<5}] {msg}", flush=True)


def debug(msg: str) -> None:
    if VERBOSE:
        _emit("DEBUG", msg)


def info(msg: str) -> None:
    _emit("INFO", msg)


def warn(msg: str) -> None:
    _emit("WARN", msg)


def error(msg: str) -> None:
    _emit("ERROR", msg)


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #

class GitHubError(Exception):
    """GitHub API 调用失败（鉴权、限流、服务端错误等）。"""


class RepoError(Exception):
    """单个仓库无法采集（不存在 / 无权限 / 已私有化），不应中断其他仓库。"""


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

def ts_to_date(value: Optional[str]) -> Optional[str]:
    """把 '2026-08-20T00:00:00Z' / '2026-08-20' 归一化成 '2026-08-20'。

    无法解析时返回 None（调用方负责跳过并告警）。
    """
    if not value:
        return None
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    parts = text.split("-")
    if len(parts) != 3:
        return None
    year, month, day = parts
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    try:
        datetime(int(year), int(month), int(day))
    except ValueError:
        return None
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def date_to_ts(date_str: str) -> str:
    """'2026-08-20' -> '2026-08-20T00:00:00Z'（GitHub 的时间戳就是 UTC 零点）。"""
    return f"{date_str}T00:00:00Z"


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# JSON 读写
# --------------------------------------------------------------------------- #

def load_json(path: Path, default: Any) -> Tuple[Any, bool]:
    """读取 JSON 文件。

    返回 (数据, 文件是否存在且解析成功)。文件不存在 / 内容为空 / 解析失败
    都返回 default，并打印一条 WARNING，保证脚本不会因为历史文件损坏而崩溃。
    """
    path = Path(path)
    if not path.exists():
        debug(f"文件不存在，使用默认值：{path}")
        return default, False
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            warn(f"文件为空，使用默认值：{path}")
            return default, False
        return json.loads(text), True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        warn(f"读取 {path} 失败（{type(exc).__name__}: {exc}），本次将重建该文件")
        return default, False


def save_json(path: Path, data: Any, *, sort_series: bool = True) -> None:
    """原子写入 JSON（先写临时文件再 replace），避免中断时留下半截文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sort_series:
        data = _sort_series(data)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    debug(f"已写入 {path}")


def _sort_series(data: Any) -> Any:
    """按 timestamp 排序 history 里的 views / clones 序列，保证 diff 稳定。"""
    if not isinstance(data, dict):
        return data
    repos = data.get("repos")
    if isinstance(repos, dict):
        for entry in repos.values():
            if not isinstance(entry, dict):
                continue
            for key in ("views", "clones"):
                series = entry.get(key)
                if isinstance(series, list):
                    entry[key] = sorted(
                        series,
                        key=lambda p: ts_to_date(p.get("timestamp")) or "",
                    )
    return data


# --------------------------------------------------------------------------- #
# 合并逻辑（幂等的核心）
# --------------------------------------------------------------------------- #

def merge_series(
    existing: Optional[Sequence[dict]],
    incoming: Optional[Sequence[dict]],
    *,
    overwrite: bool = True,
) -> Tuple[List[dict], Dict[str, Any]]:
    """按日期合并两个时间序列。

    参数：
        existing: 历史数据
        incoming: 本次从 API 拉取的数据
        overwrite: True 表示同一天以 incoming 为准（线上采集）；
                   False 表示保留 existing（CSV 回填时避免覆盖 API 数据）

    返回：
        (合并后并按日期升序排列的列表, 统计信息)
        统计信息包含 added / updated / skipped / unchanged。
    """
    by_date: Dict[str, dict] = {}
    dropped = 0

    for point in existing or []:
        if not isinstance(point, dict):
            dropped += 1
            continue
        date = ts_to_date(point.get("timestamp"))
        if not date:
            dropped += 1
            continue
        by_date[date] = {
            "timestamp": date_to_ts(date),
            "count": as_int(point.get("count")),
            "uniques": as_int(point.get("uniques")),
        }

    stats: Dict[str, Any] = {"added": [], "updated": [], "skipped": [], "unchanged": 0, "dropped": dropped}

    for point in incoming or []:
        if not isinstance(point, dict):
            stats["dropped"] += 1
            continue
        date = ts_to_date(point.get("timestamp"))
        if not date:
            warn(f"无法解析 timestamp，已跳过该条数据：{point!r}")
            stats["dropped"] += 1
            continue
        candidate = {
            "timestamp": date_to_ts(date),
            "count": as_int(point.get("count")),
            "uniques": as_int(point.get("uniques")),
        }

        old = by_date.get(date)
        if old is None:
            by_date[date] = candidate
            stats["added"].append(date)
        elif old == candidate:
            stats["unchanged"] += 1
        elif overwrite:
            by_date[date] = candidate
            stats["updated"].append(date)
        else:
            stats["skipped"].append(date)

    merged = [by_date[d] for d in sorted(by_date)]
    return merged, stats


def empty_history() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "last_updated": None,
        "repos": {},
    }


def ensure_repo_entry(history: dict, full_name: str) -> dict:
    """保证 history['repos'][full_name] 存在，返回该仓库节点。"""
    repos = history.setdefault("repos", {})
    if not isinstance(repos, dict):
        history["repos"] = repos = {}
    entry = repos.get(full_name)
    if not isinstance(entry, dict):
        entry = {"last_updated": None, "views": [], "clones": []}
        repos[full_name] = entry
    entry.setdefault("views", [])
    entry.setdefault("clones", [])
    return entry


# --------------------------------------------------------------------------- #
# GitHub API
# --------------------------------------------------------------------------- #

def _headers(token: str) -> Dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }


def api_get(
    path: str,
    token: str,
    *,
    params: Optional[Dict[str, str]] = None,
    max_attempts: int = 3,
    timeout: int = 30,
) -> Any:
    """调用 GitHub REST API 并返回解析后的 JSON（只要 body）。"""
    return api_get_raw(path, token, params=params, max_attempts=max_attempts, timeout=timeout)[0]


def api_get_raw(
    path: str,
    token: str,
    *,
    params: Optional[Dict[str, str]] = None,
    max_attempts: int = 3,
    timeout: int = 30,
) -> Tuple[Any, Dict[str, str]]:
    """调用 GitHub REST API，同时返回 (JSON, 响应头)。

    需要响应头是因为列表类接口依赖 Link 头做分页。

    重试策略：网络错误 / 5xx / 429 / secondary rate limit 会退避重试；
    401 / 403（权限不足）/ 404 直接抛出可读的错误，不浪费重试次数。
    """
    url = API_ROOT + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    last_error: Optional[str] = None
    delay = 2

    for attempt in range(1, max_attempts + 1):
        debug(f"GET {url}（第 {attempt}/{max_attempts} 次）")
        request = urllib.request.Request(url, headers=_headers(token), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                headers = dict(response.headers.items())
            if not raw.strip():
                return None, headers
            return json.loads(raw), headers

        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # pragma: no cover - 读取 body 失败不影响主流程
                body = ""
            message = _extract_message(body) or f"HTTP {status}"
            remaining = exc.headers.get("X-RateLimit-Remaining")
            retry_after = exc.headers.get("Retry-After")

            if status == 401:
                raise GitHubError(
                    "401 Unauthorized：token 无效或已过期。请检查 TRAFFIC_TOKEN secret。"
                ) from exc
            if status == 404:
                raise RepoError(
                    "404 Not Found：仓库不存在、已私有化，或 token 没有该仓库的访问权限。"
                ) from exc
            if status == 403:
                if remaining == "0":
                    raise GitHubError(
                        "403 触发主限流（X-RateLimit-Remaining=0），请稍后重试。"
                    ) from exc
                if "secondary rate limit" in message.lower() or retry_after:
                    wait = int(retry_after) if str(retry_after).isdigit() else delay
                    last_error = f"403 secondary rate limit：{message}"
                    if attempt < max_attempts:
                        warn(f"{last_error}，{wait}s 后重试")
                        time.sleep(wait)
                        delay *= 3
                        continue
                    raise GitHubError(last_error) from exc
                raise GitHubError(
                    f"403 Forbidden：{message}。"
                    "Traffic API 要求 token 对目标仓库具备写权限（fine-grained PAT 需 Administration:read）。"
                ) from exc
            if status in (429,) or 500 <= status < 600:
                last_error = f"HTTP {status}：{message}"
                if attempt < max_attempts:
                    warn(f"{last_error}，{delay}s 后重试")
                    time.sleep(delay)
                    delay *= 3
                    continue
                raise GitHubError(last_error) from exc
            raise GitHubError(f"HTTP {status}：{message}") from exc

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"网络错误：{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                warn(f"{last_error}，{delay}s 后重试")
                time.sleep(delay)
                delay *= 3
                continue
            raise GitHubError(last_error) from exc

        except json.JSONDecodeError as exc:
            raise GitHubError(f"响应不是合法 JSON：{exc}") from exc

    raise GitHubError(last_error or "未知错误")


def has_next_page(headers: Dict[str, str]) -> bool:
    """根据 Link 响应头判断列表接口是否还有下一页。

    GitHub 的分页信息形如：
    Link: <https://api.github.com/user/repos?page=2>; rel="next", <...>; rel="last"
    """
    link = ""
    for key, value in (headers or {}).items():
        if key.lower() == "link":
            link = value
            break
    return 'rel="next"' in link


def _extract_message(body: str) -> str:
    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            return str(payload.get("message", ""))
    except Exception:
        pass
    return body[:200]


def get_token() -> Optional[str]:
    """按优先级获取 token：GITHUB_TOKEN > TRAFFIC_TOKEN > GH_TOKEN。"""
    for name in ("GITHUB_TOKEN", "TRAFFIC_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            debug(f"使用环境变量 {name} 中的 token（长度 {len(value)}）")
            return value
    return None
