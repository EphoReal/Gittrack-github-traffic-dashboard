#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 fthuu
"""把 GitHub 后台导出的 Traffic CSV 回填进 data/traffic-history.json。

为什么要这一步：
    GitHub 只保留最近 14 天数据，Actions 首次运行能拿到的最早日期也是「今天 - 13 天」。
    更早的历史只能靠你手动从 Insights 页面导出的 CSV 补回来，而且补回来之后就永久保存了。

支持的文件名（GitHub 中文/英文后台导出的默认命名）：
    Total views in last 14 days.csv        -> views.count
    Unique visitors in last 14 days.csv    -> views.uniques
    Clones in last 14 days.csv             -> clones.count
    Unique cloners in last 14 days.csv     -> clones.uniques

CSV 格式（首行是表头，第一列为 MM/DD，第二列为数值）：
    "Category","Total"
    "08/14",0

用法：
    python scripts/seed_from_csv.py --repo your-username/your-repo --year 2026
    python scripts/seed_from_csv.py --csv-dir . --dry-run

注意：
    * 默认 --no-overwrite，即已存在的日期不会被 CSV 覆盖。
      这是为了保证「API 数据 > CSV 回填数据」的优先级。
      首次初始化时文件本来是空的，所以不会有影响；确需覆盖请显式加 --overwrite。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import (  # noqa: E402
    as_int,
    date_to_ts,
    empty_history,
    ensure_repo_entry,
    info,
    iso,
    load_json,
    merge_series,
    save_json,
    warn,
)

DEFAULT_CSV_DIR = common.REPO_ROOT
DEFAULT_DATA_DIR = common.REPO_ROOT / "data"

#: (metric, field) -> 文件名关键字组合
FILE_RULES: List[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]]] = [
    # metric, field, 文件名必须包含的关键字（任一）, 表头第二列候选值
    ("views", "count", ("total views", "views total", "total_views"), ("total", "views", "view")),
    ("views", "uniques", ("unique visitors", "unique visitor", "unique_visitors"), ("unique", "uniques", "visitors")),
    ("clones", "count", ("clones in last", "total clones", "clones total", "total_clones"), ("total", "clones", "clone")),
    ("clones", "uniques", ("unique cloners", "unique_cloners"), ("unique", "uniques", "cloners")),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 GitHub 导出的 Traffic CSV 回填进本地历史 JSON。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv-dir", default=str(DEFAULT_CSV_DIR), help="CSV 所在目录（默认仓库根目录）")
    parser.add_argument("--repo", default="", help="目标仓库 owner/name，缺省时取 config/repos.json 的第一个仓库")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="数据目录")
    parser.add_argument("--year", type=int, default=datetime.now(timezone.utc).year, help="CSV 中 MM/DD 所属的年份")
    parser.add_argument("--overwrite", action="store_true", help="允许 CSV 覆盖已存在的日期（默认不覆盖）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def classify(path: Path, header: List[str]) -> Optional[Tuple[str, str]]:
    """判断某个 CSV 属于哪个指标。先按文件名匹配，再按表头兜底。"""
    lowered = path.name.lower()
    column = (header[1] if len(header) > 1 else "").strip().lower()

    for metric, field, name_keys, header_values in FILE_RULES:
        if any(key in lowered for key in name_keys):
            return metric, field
    for metric, field, _name_keys, header_values in FILE_RULES:
        if column in header_values:
            # 表头兜底时必须靠文件名区分 views / clones
            if metric == "views" and "clone" in lowered:
                continue
            if metric == "clones" and ("view" in lowered or "visitor" in lowered):
                continue
            return metric, field
    return None


def resolve_year(month: int, day: int, year: int) -> int:
    """处理跨年场景：若按 year 解析出的日期比今天晚 30 天以上，则认为它属于上一年。"""
    try:
        candidate = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return year
    if (candidate - datetime.now(timezone.utc)).days > 30:
        return year - 1
    return year


def read_csv(path: Path, year: int) -> Dict[str, int]:
    """读取 CSV，返回 {日期: 数值}。"""
    values: Dict[str, int] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        warn(f"空文件：{path.name}")
        return values

    header = [c.strip() for c in rows[0]]
    for row in rows[1:]:
        if len(row) < 2:
            continue
        raw_date = row[0].strip().strip('"')
        raw_value = row[1].strip().strip('"')
        parts = raw_date.replace("-", "/").split("/")
        if len(parts) != 2:
            warn(f"{path.name}: 无法解析日期 {raw_date!r}，已跳过")
            continue
        try:
            month, day = int(parts[0]), int(parts[1])
        except ValueError:
            warn(f"{path.name}: 无法解析日期 {raw_date!r}，已跳过")
            continue
        real_year = resolve_year(month, day, year)
        try:
            date = datetime(real_year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            warn(f"{path.name}: 非法日期 {raw_date!r}（年 {real_year}），已跳过")
            continue
        values[date] = as_int(raw_value)
    return values


def pick_repo(args: argparse.Namespace) -> str:
    if args.repo:
        text = args.repo.strip()
        for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        text = text.strip("/")
        if text.endswith(".git"):
            text = text[: -len(".git")]
        parts = [p for p in text.split("/") if p]
        if len(parts) < 2:
            raise SystemExit(f"[FATAL] 无法解析仓库标识：{args.repo!r}，应为 owner/name。")
        return "/".join(parts[-2:])

    config_path = common.REPO_ROOT / "config" / "repos.json"
    data, ok = load_json(config_path, {})
    if ok and isinstance(data, dict):
        for item in data.get("repos", []):
            if isinstance(item, dict) and item.get("owner") and item.get("name"):
                return f"{item['owner']}/{item['name']}"
            if isinstance(item, str) and "/" in item:
                return item.strip("/")
    raise SystemExit("[FATAL] 请用 --repo owner/name 指定仓库，或先创建 config/repos.json。")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    common.VERBOSE = args.verbose

    csv_dir = Path(args.csv_dir)
    if not csv_dir.is_dir():
        print(f"[FATAL] CSV 目录不存在：{csv_dir}", file=sys.stderr)
        return 1

    full_name = pick_repo(args)
    info(f"目标仓库：{full_name}")
    info(f"CSV 目录：{csv_dir}")
    info(f"假定年份：{args.year}（跨年会自动纠正）")

    collected: Dict[str, Dict[str, Dict[str, int]]] = {"views": {}, "clones": {}}
    matched_files: List[str] = []

    for path in sorted(csv_dir.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            first_row = next(csv.reader(fh), [])
        classification = classify(path, first_row)
        if not classification:
            common.debug(f"跳过无法识别的 CSV：{path.name}")
            continue
        metric, field = classification
        values = read_csv(path, args.year)
        if not values:
            warn(f"{path.name}: 未读取到有效数据")
            continue
        for date, value in values.items():
            collected[metric].setdefault(date, {})[field] = value
        matched_files.append(f"{path.name} -> {metric}.{field}（{len(values)} 天）")
        info(f"识别 {path.name} 为 {metric}.{field}，读取 {len(values)} 天")

    if not matched_files:
        print(
            f"[FATAL] 在 {csv_dir} 下没有找到可识别的 Traffic CSV。\n"
            "        期望文件名类似：'Total views in last 14 days.csv'、'Unique cloners in last 14 days.csv'",
            file=sys.stderr,
        )
        return 1

    incoming: Dict[str, List[dict]] = {}
    for metric in ("views", "clones"):
        series = []
        for date in sorted(collected[metric]):
            point = collected[metric][date]
            if "count" not in point or "uniques" not in point:
                warn(f"{metric} {date} 缺少 count 或 uniques（只找到 {sorted(point)}），补 0")
            series.append(
                {
                    "timestamp": date_to_ts(date),
                    "count": as_int(point.get("count")),
                    "uniques": as_int(point.get("uniques")),
                }
            )
        incoming[metric] = series
        info(f"{metric}: 整理出 {len(series)} 天待回填数据")

    data_dir = Path(args.data_dir)
    history_path = data_dir / "traffic-history.json"
    history, exists = load_json(history_path, empty_history())
    if not isinstance(history, dict) or "repos" not in history:
        history = empty_history()
    info(f"历史文件{'已存在' if exists else '不存在，将创建'}：{history_path}")

    entry = ensure_repo_entry(history, full_name)
    changed = False
    for metric in ("views", "clones"):
        merged, stats = merge_series(entry.get(metric), incoming[metric], overwrite=args.overwrite)
        entry[metric] = merged
        info(
            f"{metric}: 新增 {len(stats['added'])} 天 / 覆盖 {len(stats['updated'])} 天 / "
            f"保留 {len(stats['skipped'])} 天 / 累计 {len(merged)} 天"
        )
        if stats["added"] or stats["updated"]:
            changed = True

    if entry["views"]:
        info(f"回填后日期范围：{entry['views'][0]['timestamp'][:10]} ~ {entry['views'][-1]['timestamp'][:10]}")

    entry["last_updated"] = iso()
    history["last_updated"] = iso()
    history["schema_version"] = common.SCHEMA_VERSION

    if args.dry_run:
        info("--dry-run：跳过写入")
        print(json.dumps(history, ensure_ascii=False, indent=2)[:4000])
        return 0

    if not changed:
        info("没有新增或更新的日期，跳过写入。")
        return 0

    save_json(history_path, history)
    info(f"已写入 {history_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
