#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 fthuu
"""采集 GitHub Traffic 数据并合并进本地 JSON 历史文件。

用法：
    export GITHUB_TOKEN=ghp_xxx
    python scripts/collect_traffic.py                      # 采集 config/repos.json 里的所有仓库
    python scripts/collect_traffic.py --repos owner/repo    # 临时只采集指定仓库
    python scripts/collect_traffic.py --dry-run             # 只打印，不写文件
    python scripts/collect_traffic.py --no-referrers        # 跳过 referrers / paths 归档

环境变量：
    GITHUB_TOKEN / TRAFFIC_TOKEN / GH_TOKEN   必需，具备目标仓库写权限的 token
    REPO_OWNER + REPO_NAME                    可选，单仓库快捷方式（等价于 --repos）
    REPOS                                     可选，逗号分隔的 owner/name 列表
    DATA_DIR                                  可选，数据目录，默认 <repo_root>/data

退出码：
    0  全部成功（含 --dry-run）
    1  至少一个仓库采集失败，或参数 / token 缺失
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import (  # noqa: E402
    GitHubError,
    RepoError,
    api_get,
    api_get_raw,
    as_int,
    empty_history,
    ensure_repo_entry,
    get_token,
    has_next_page,
    info,
    iso,
    load_json,
    merge_series,
    save_json,
    ts_to_date,
    warn,
)

DEFAULT_CONFIG = common.REPO_ROOT / "config" / "repos.json"
DEFAULT_DATA_DIR = common.REPO_ROOT / "data"
#: 每个仓库最多保留多少份 referrers / paths 快照（≈ 1 年）
DEFAULT_SNAPSHOT_LIMIT = 366
#: auto 模式下仓库发现数量的硬上限，防止误配导致请求风暴
HARD_MAX_REPOS = 500


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="采集 GitHub Traffic 数据并合并进本地 JSON 历史文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="仓库清单配置文件（默认 config/repos.json）")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)), help="数据输出目录")
    parser.add_argument("--repos", default="", help="逗号分隔的仓库列表，支持 owner/name 或完整 URL，优先级高于配置文件")
    parser.add_argument("--dry-run", action="store_true", help="只拉取并打印，不写入任何文件")
    parser.add_argument("--no-referrers", action="store_true", help="跳过 referrers 归档")
    parser.add_argument("--no-paths", action="store_true", help="跳过 popular paths 归档")
    parser.add_argument("--snapshot-limit", type=int, default=DEFAULT_SNAPSHOT_LIMIT, help=f"每个仓库保留的快照份数（默认 {DEFAULT_SNAPSHOT_LIMIT}）")
    parser.add_argument("--mode", choices=("auto", "explicit"), default=None, help="auto=自动发现当前用户的所有仓库；explicit=只用配置文件里的清单（默认取配置文件里的 mode）")
    parser.add_argument("--list-repos", action="store_true", help="只打印将要采集的仓库清单然后退出（不调 Traffic 接口，用于验证过滤规则）")
    parser.add_argument("--fail-fast", action="store_true", help="遇到第一个失败的仓库就退出")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def explicit_repos(data: dict) -> List[str]:
    """从配置文件的 repos 数组里读出显式清单。"""
    repos: List[str] = []
    for item in data.get("repos", []):
        if isinstance(item, str):
            full = normalize_repo(item)
        elif isinstance(item, dict):
            if item.get("enabled") is False:
                info(f"跳过已禁用的仓库：{item.get('owner')}/{item.get('name')}")
                continue
            full = normalize_repo(f"{item.get('owner', '')}/{item.get('name', '')}")
        else:
            continue
        if full:
            repos.append(full)
    return repos


def discover_repos(token: str, data: dict) -> List[str]:
    """调用 /user/repos 自动发现当前用户的仓库，并按配置规则过滤。

    分页用 Link 响应头推进，最多翻到 max_repos 个就停，避免请求风暴。
    """
    auto = data.get("auto") or {}
    affiliation = str(auto.get("affiliation", "owner"))
    sort = str(auto.get("sort", "pushed"))
    include_forks = bool(auto.get("include_forks", False))
    include_archived = bool(auto.get("include_archived", False))
    include_private = bool(auto.get("include_private", True))
    include_disabled = bool(auto.get("include_disabled", False))
    max_repos = min(int(auto.get("max_repos", 100)), HARD_MAX_REPOS)
    include_patterns = [re.compile(p) for p in auto.get("include_patterns", []) or []]
    exclude_patterns = [re.compile(p) for p in auto.get("exclude_patterns", []) or []]

    info(
        f"自动发现仓库（affiliation={affiliation}, sort={sort}, 上限 {max_repos}；"
        f"fork={'含' if include_forks else '排除'}, archived={'含' if include_archived else '排除'}, "
        f"private={'含' if include_private else '排除'}）"
    )

    found: List[str] = []
    page = 1
    while len(found) < max_repos:
        payload, headers = api_get_raw(
            "/user/repos",
            token,
            params={
                "affiliation": affiliation,
                "sort": sort,
                "direction": "desc",
                "per_page": "100",
                "page": str(page),
            },
        )
        items = payload if isinstance(payload, list) else []
        if not items:
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            full = item.get("full_name") or f"{item.get('owner', {}).get('login', '')}/{item.get('name', '')}"
            if item.get("fork") and not include_forks:
                continue
            if item.get("archived") and not include_archived:
                continue
            if item.get("disabled") and not include_disabled:
                continue
            if item.get("private") and not include_private:
                continue
            if include_patterns and not any(p.search(full) for p in include_patterns):
                continue
            if any(p.search(full) for p in exclude_patterns):
                continue
            if full and full not in found:
                found.append(full)
            if len(found) >= max_repos:
                break

        if not has_next_page(headers):
            break
        page += 1

    truncated = len(found) >= max_repos and has_next_page(headers)
    info(f"共发现 {len(found)} 个仓库" + ("（已达上限，其余被忽略）" if truncated else ""))
    return found[:max_repos]


def resolve_repos(args: argparse.Namespace, token: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """确定要采集的仓库列表。

    优先级：--repos / REPOS 环境变量 / REPO_OWNER+REPO_NAME  >  配置文件。
    返回 (仓库列表, 显式清单)，调用方据此判断要不要清空历史里已移除的仓库。
    """
    raw = (args.repos or os.environ.get("REPOS", "")).strip()
    if not raw:
        owner = os.environ.get("REPO_OWNER", "").strip()
        name = os.environ.get("REPO_NAME", "").strip()
        if owner and name:
            raw = f"{owner}/{name}"

    if raw:
        repos = [normalize_repo(item) for item in raw.split(",") if item.strip()]
        return [r for r in repos if r], []

    config_path = Path(args.config)
    data: Dict[str, Any] = {}
    if config_path.exists():
        loaded, ok = load_json(config_path, {})
        if not ok or not isinstance(loaded, dict):
            raise SystemExit(f"[FATAL] 配置文件解析失败：{config_path}")
        data = loaded
    elif args.mode == "auto":
        data = {}  # 没有配置文件时，auto 模式用全部默认规则
    else:
        raise SystemExit(
            f"[FATAL] 未指定仓库，且配置文件不存在：{config_path}\n"
            "        请用 --repos owner/name，或创建 config/repos.json。"
        )

    explicit = explicit_repos(data)
    mode = args.mode or str(data.get("mode", "explicit")).lower()

    if mode == "auto":
        if not token:
            raise SystemExit("[FATAL] auto 模式需要 token 才能列出仓库。")
        discovered = discover_repos(token, data)
        merged: List[str] = []
        for name in discovered + explicit:
            if name and name not in merged:
                merged.append(name)
        if not merged:
            raise SystemExit(
                "[FATAL] auto 模式没有发现任何仓库。请检查 token 权限"
                "（fine-grained PAT 需要 Metadata: read 才能列出仓库），或放宽 config/repos.json 里的过滤规则。"
            )
        return merged, explicit

    if not explicit:
        raise SystemExit(f"[FATAL] 配置文件里没有可用仓库：{config_path}")
    return explicit, explicit


def normalize_repo(value: str) -> Optional[str]:
    """把 'owner/repo'、'https://github.com/owner/repo'、'owner/repo.git' 统一成 'owner/repo'。"""
    text = (value or "").strip()
    if not text:
        return None
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.endswith(".git"):
        text = text[: -len(".git")]
    text = text.strip("/")
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        warn(f"无法解析仓库标识，已跳过：{value!r}")
        return None
    return "/".join(parts[-2:])


# --------------------------------------------------------------------------- #
# 采集
# --------------------------------------------------------------------------- #

def fetch_series(full_name: str, kind: str, token: str) -> List[dict]:
    """拉取 views / clones 的按天序列。kind 取值 'views' 或 'clones'。"""
    payload = api_get(f"/repos/{full_name}/traffic/{kind}", token, params={"per": "day"})
    if not isinstance(payload, dict):
        raise GitHubError(f"{kind} 接口返回了非预期结构：{type(payload).__name__}")
    items = payload.get(kind, [])
    if not isinstance(items, list):
        raise GitHubError(f"{kind} 字段不是数组：{payload!r}")
    total = as_int(payload.get("count"))
    uniques = as_int(payload.get("uniques"))
    info(f"  {kind}: 窗口内共 {total} 次 / {uniques} 个独立访客，返回 {len(items)} 天")
    if not items:
        warn(f"  {kind}: API 返回空数组（仓库可能从未产生该类型数据），本次不写入")
    return items


def fetch_referrers(full_name: str, token: str) -> List[dict]:
    payload = api_get(f"/repos/{full_name}/traffic/popular/referrers", token)
    items = payload if isinstance(payload, list) else []
    info(f"  referrers: {len(items)} 条")
    return [
        {
            "referrer": item.get("referrer", ""),
            "count": as_int(item.get("count")),
            "uniques": as_int(item.get("uniques")),
        }
        for item in items
        if isinstance(item, dict)
    ]


def fetch_paths(full_name: str, token: str) -> List[dict]:
    payload = api_get(f"/repos/{full_name}/traffic/popular/paths", token)
    items = payload if isinstance(payload, list) else []
    info(f"  popular paths: {len(items)} 条")
    return [
        {
            "path": item.get("path", ""),
            "title": item.get("title", ""),
            "count": as_int(item.get("count")),
            "uniques": as_int(item.get("uniques")),
        }
        for item in items
        if isinstance(item, dict)
    ]


def append_snapshot(store: dict, full_name: str, items: List[dict], *, limit: int) -> bool:
    """把一份 14 天聚合快照追加到 store（referrers / paths 专用）。

    返回 True 表示内容有变化。快照按 captured_at 去重：同一天 UTC 只保留最新一份，
    因此一天内多次运行脚本不会产生重复记录。
    """
    repos = store.setdefault("repos", {})
    entry = repos.setdefault(full_name, {"last_updated": None, "snapshots": []})
    entry.setdefault("snapshots", [])

    captured_at = iso()
    snapshot = {"captured_at": captured_at, "items": items}
    snapshots = entry["snapshots"]

    for index, existing in enumerate(snapshots):
        if isinstance(existing, dict) and existing.get("captured_at", "")[:10] == captured_at[:10]:
            if existing.get("items") == items:
                return False
            snapshots[index] = snapshot
            entry["last_updated"] = captured_at
            return True

    snapshots.append(snapshot)
    if len(snapshots) > limit:
        del snapshots[: len(snapshots) - limit]
    entry["last_updated"] = captured_at
    return True


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    common.VERBOSE = args.verbose

    token = get_token()
    if not token:
        print("[FATAL] 缺少 token，请设置 GITHUB_TOKEN（或 TRAFFIC_TOKEN）环境变量。", file=sys.stderr)
        return 1

    try:
        repos, _explicit = resolve_repos(args, token)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.list_repos:
        print(f"将要采集 {len(repos)} 个仓库：")
        for name in repos:
            print(f"  - {name}")
        return 0

    data_dir = Path(args.data_dir)
    history_path = data_dir / "traffic-history.json"
    referrers_path = data_dir / "referrers-history.json"
    paths_path = data_dir / "paths-history.json"

    history, history_exists = load_json(history_path, empty_history())
    if not isinstance(history, dict) or "repos" not in history:
        history = empty_history()
    referrers, _ = load_json(referrers_path, {"schema_version": common.SCHEMA_VERSION, "last_updated": None, "repos": {}})
    paths, _ = load_json(paths_path, {"schema_version": common.SCHEMA_VERSION, "last_updated": None, "repos": {}})

    info(f"开始采集，共 {len(repos)} 个仓库：{', '.join(repos)}")
    info(f"数据目录：{data_dir}（history 文件{'已存在' if history_exists else '不存在，将创建'}）")
    if args.dry_run:
        info("--dry-run 已开启，本次不会写入任何文件")

    changed = False
    failures: List[str] = []
    report: List[Dict[str, Any]] = []

    for full_name in repos:
        info(f"[{full_name}] 拉取 Traffic 数据 ...")
        try:
            entry = ensure_repo_entry(history, full_name)

            views_new = fetch_series(full_name, "views", token)
            clones_new = fetch_series(full_name, "clones", token)

            views_merged, v_stats = merge_series(entry.get("views"), views_new, overwrite=True)
            clones_merged, c_stats = merge_series(entry.get("clones"), clones_new, overwrite=True)
            entry["views"] = views_merged
            entry["clones"] = clones_merged
            entry["last_updated"] = iso()

            if v_stats["added"] or v_stats["updated"] or c_stats["added"] or c_stats["updated"]:
                changed = True

            info(
                f"  views : 新增 {len(v_stats['added'])} 天，更新 {len(v_stats['updated'])} 天，"
                f"无变化 {v_stats['unchanged']} 天，累计 {len(views_merged)} 天"
            )
            info(
                f"  clones: 新增 {len(c_stats['added'])} 天，更新 {len(c_stats['updated'])} 天，"
                f"无变化 {c_stats['unchanged']} 天，累计 {len(clones_merged)} 天"
            )
            if v_stats["dropped"] or c_stats["dropped"]:
                warn(f"  跳过了 {v_stats['dropped'] + c_stats['dropped']} 条无法解析的数据")

            if not args.no_referrers:
                try:
                    if append_snapshot(referrers, full_name, fetch_referrers(full_name, token), limit=args.snapshot_limit):
                        changed = True
                        info("  referrers 快照已更新")
                    else:
                        info("  referrers 快照无变化")
                except (GitHubError, RepoError) as exc:
                    warn(f"  referrers 采集失败（已忽略）：{exc}")

            if not args.no_paths:
                try:
                    if append_snapshot(paths, full_name, fetch_paths(full_name, token), limit=args.snapshot_limit):
                        changed = True
                        info("  popular paths 快照已更新")
                    else:
                        info("  popular paths 快照无变化")
                except (GitHubError, RepoError) as exc:
                    warn(f"  popular paths 采集失败（已忽略）：{exc}")

            report.append(
                {
                    "repo": full_name,
                    "ok": True,
                    "views": views_merged,
                    "clones": clones_merged,
                    "views_added": v_stats["added"],
                    "views_updated": v_stats["updated"],
                    "clones_added": c_stats["added"],
                    "clones_updated": c_stats["updated"],
                }
            )

        except RepoError as exc:
            warn(f"[{full_name}] 采集失败：{exc}")
            failures.append(full_name)
            report.append({"repo": full_name, "ok": False, "error": str(exc)})
            if args.fail_fast:
                return 1
        except GitHubError as exc:
            warn(f"[{full_name}] 采集失败：{exc}")
            failures.append(full_name)
            report.append({"repo": full_name, "ok": False, "error": str(exc)})
            if args.fail_fast:
                return 1

    history["schema_version"] = common.SCHEMA_VERSION
    history["last_updated"] = iso()
    referrers["last_updated"] = iso()
    paths["last_updated"] = iso()

    write_summary(report, args)

    if args.dry_run:
        info("--dry-run：跳过写入。合并结果预览：")
        print(json.dumps(history, ensure_ascii=False, indent=2)[:4000])
    elif changed:
        save_json(history_path, history)
        if not args.no_referrers:
            save_json(referrers_path, referrers)
        if not args.no_paths:
            save_json(paths_path, paths)
        info(f"数据已更新并写入 {data_dir}")
    else:
        info("本次没有产生任何数据变化，跳过写入（保持 git 干净）")

    if failures:
        warn(f"{len(failures)} 个仓库采集失败：{', '.join(failures)}")
        return 1

    info("全部仓库采集完成。")
    return 0


# --------------------------------------------------------------------------- #
# Actions Summary
# --------------------------------------------------------------------------- #

def write_summary(report: List[Dict[str, Any]], args: argparse.Namespace) -> None:
    """在 GitHub Actions 的 Job Summary 里输出一张统计表。"""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    lines: List[str] = []

    ok_report = [r for r in report if r.get("ok")]
    if ok_report:
        lines.append("## GitHub Traffic 采集结果\n")
        lines.append("| 仓库 | 今日 Views | 今日 Unique | 今日 Clones | 今日 Unique | 近 14 天 Views | 近 14 天 Clones | 历史累计天数 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for item in ok_report:
            views: List[dict] = item["views"]
            clones: List[dict] = item["clones"]
            latest = views[-1] if views else {}
            latest_clone = _clone_for(clones, latest.get("timestamp"))
            lines.append(
                "| {repo} | {v_count} | {v_uniq} | {c_count} | {c_uniq} | {v14} | {c14} | {days} |".format(
                    repo=item["repo"],
                    v_count=as_int(latest.get("count")),
                    v_uniq=as_int(latest.get("uniques")),
                    c_count=as_int(latest_clone.get("count")),
                    c_uniq=as_int(latest_clone.get("uniques")),
                    v14=sum(as_int(p.get("count")) for p in views[-14:]),
                    c14=sum(as_int(p.get("count")) for p in clones[-14:]),
                    days=len(views),
                )
            )
        lines.append("")
        for item in ok_report:
            added = sorted(set(item["views_added"]) | set(item["clones_added"]))
            updated = sorted(set(item["views_updated"]) | set(item["clones_updated"]))
            if added:
                lines.append(f"- **{item['repo']}** 新增日期：{', '.join(added)}")
            if updated:
                lines.append(f"- **{item['repo']}** 刷新日期：{', '.join(updated)}")
            if not added and not updated:
                lines.append(f"- **{item['repo']}** 与已有数据完全一致，无变化")

    failed = [r for r in report if not r.get("ok")]
    if failed:
        lines.append("")
        lines.append("## 采集失败\n")
        for item in failed:
            lines.append(f"- `{item['repo']}`：{item.get('error', '未知错误')}")

    if args.dry_run:
        lines.append("")
        lines.append("> 本次为 dry-run，未写入任何文件。")

    text = "\n".join(lines)
    if not text:
        return

    print("\n----- summary -----\n" + text + "\n-------------------\n")

    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError as exc:
            warn(f"写入 GITHUB_STEP_SUMMARY 失败（已忽略）：{exc}")


def _clone_for(clones: List[dict], timestamp: Optional[str]) -> dict:
    if not timestamp:
        return clones[-1] if clones else {}
    wanted = ts_to_date(timestamp)
    for point in clones:
        if ts_to_date(point.get("timestamp")) == wanted:
            return point
    return {}


if __name__ == "__main__":
    sys.exit(main())
