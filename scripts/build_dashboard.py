#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 fthuu
"""把 data/ 下的 JSON 渲染成一个自包含的单文件 HTML 看板。

设计目标：
    * 双击即开 —— 数据内联进 HTML，不需要本地服务器、不需要联网
    * 零依赖 —— 不引用任何 CDN 的 JS/CSS，图表用手写 SVG
    * 离线可用 —— 生成的 HTML 可以随便拷贝、发邮件、丢到任何地方
    * 多仓库 —— 顶部可多选仓库，支持「合并视图」看总量、「对比视图」看差异

用法：
    python scripts/build_dashboard.py
    python scripts/build_dashboard.py --out traffic-dashboard.html --open

在 GitHub Actions 里，本脚本在采集之后运行，产出的 HTML 会一起提交回仓库。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import info, load_json, warn  # noqa: E402

DEFAULT_OUT = common.REPO_ROOT / "traffic-dashboard.html"

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GitHub Traffic 看板</title>
<style>
  :root {
    --bg: #f5f6f8;
    --card: #ffffff;
    --border: #e4e7ec;
    --text: #111827;
    --muted: #6b7280;
    --faint: #9ca3af;
    --views: #3b82f6;
    --clones: #10b981;
    --accent: #4f46e5;
    --shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 1px 3px rgba(16, 24, 40, .06);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 28px 20px 64px; }

  header { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  h1 { font-size: 21px; font-weight: 650; margin: 0; letter-spacing: -.01em; }
  .sub { color: var(--muted); font-size: 13px; }
  .sub code { background: #eef0f3; padding: 1px 6px; border-radius: 4px; font-size: 12px; }

  .btn {
    font: inherit; color: var(--text); background: var(--card);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 5px 11px; cursor: pointer; outline: none; font-size: 12.5px;
  }
  .btn:hover { border-color: #cbd2da; background: #fafbfc; }
  .btn.sm { padding: 3px 11px; font-size: 12.5px; line-height: 1.5; }

  .chips { display: flex; flex-wrap: wrap; gap: 6px; max-height: 132px; overflow-y: auto; flex: 1; min-width: 240px; }
  .chip {
    border: 1px solid var(--border); background: var(--card); border-radius: 999px;
    padding: 3px 11px; font-size: 12.5px; cursor: pointer; color: var(--muted);
    user-select: none; white-space: nowrap; transition: background .12s, border-color .12s;
  }
  .chip:hover { border-color: #cbd2da; }
  .chip.on { background: #eef2ff; border-color: #c7d2fe; color: #3730a3; font-weight: 600; }
  .chip .n { color: var(--faint); font-weight: 400; font-size: 11.5px; margin-left: 4px; }

  .bar { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 16px; flex-wrap: wrap; }
  .bar .side { display: flex; gap: 6px; align-items: center; }
  .view-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; box-shadow: var(--shadow); }
  .card .k { color: var(--muted); font-size: 12px; display: flex; align-items: center; gap: 6px; }
  .card .v { font-size: 25px; font-weight: 640; letter-spacing: -.02em; margin-top: 3px; font-variant-numeric: tabular-nums; }
  .card .d { color: var(--faint); font-size: 11px; margin-top: 2px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }

  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px 18px; box-shadow: var(--shadow); margin-bottom: 16px; }
  .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 6px; }
  .panel h2 { font-size: 15px; font-weight: 620; margin: 0; }
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .seg button { font: inherit; background: var(--card); border: 0; border-left: 1px solid var(--border);
                padding: 5px 12px; cursor: pointer; color: var(--muted); font-size: 12.5px; }
  .seg button:first-child { border-left: 0; }
  .seg button.on { background: #eef2ff; color: #3730a3; font-weight: 600; }
  .seg button:hover:not(.on) { background: #f7f8fa; }

  .legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12.5px; color: var(--muted); margin: 6px 0 2px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .legend b { color: var(--text); font-variant-numeric: tabular-nums; font-weight: 600; }

  #chart { position: relative; width: 100%; height: 300px; }
  #chart.tall { height: 340px; }
  #chart svg { display: block; }
  .grid line { stroke: #eef0f3; stroke-width: 1; }
  .axis { fill: var(--faint); font-size: 11px; }
  .line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .tip {
    position: absolute; pointer-events: none; background: rgba(17, 24, 39, .94); color: #fff;
    border-radius: 8px; padding: 7px 10px; font-size: 12px; line-height: 1.55; white-space: nowrap;
    transform: translate(-50%, -120%); opacity: 0; transition: opacity .1s; z-index: 5;
    font-variant-numeric: tabular-nums; box-shadow: 0 4px 12px rgba(0,0,0,.18);
    max-height: 260px; overflow: hidden;
  }
  .tip.on { opacity: 1; }
  .tip .t { color: #b6c2d3; font-size: 11px; margin-bottom: 3px; }
  .tip .r { display: flex; align-items: center; gap: 6px; }
  .tip .r i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex: none; }
  .tip .r em { color: #8b97a8; font-style: normal; font-size: 11px; }

  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 780px) { .cols { grid-template-columns: 1fr; } }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #f1f3f5; }
  th { color: var(--muted); font-weight: 550; font-size: 12px; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tbody tr:last-child td { border-bottom: 0; }
  th.sortable { cursor: pointer; user-select: none; }
  th.sortable:hover { color: var(--text); }
  th.sortable.sorted::after { content: " ▾"; font-size: 10px; }
  th.sortable.sorted.asc::after { content: " ▴"; }
  .bar-cell { height: 6px; border-radius: 3px; background: var(--views); min-width: 2px; }
  .bar-cell.c { background: var(--clones); }
  .name { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .scroll { max-height: 400px; overflow-y: auto; margin-top: 4px; }
  .empty { color: var(--faint); font-size: 13px; padding: 18px 0; text-align: center; line-height: 1.8; }
  footer { color: var(--faint); font-size: 12px; text-align: center; margin-top: 26px; line-height: 1.8; }
  .note { color: var(--muted); font-size: 12px; margin-top: 8px; }

  /* 实时刷新（PAT 直连 GitHub，token 仅存本地浏览器） */
  .live { margin-bottom: 14px; }
  .liveBar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .liveStatus { font-size: 12.5px; color: var(--muted); padding: 3px 11px; border: 1px solid var(--border); border-radius: 999px; background: var(--card); }
  .liveStatus.ok { color: #047857; border-color: #a7f3d0; background: #ecfdf5; }
  .liveStatus.warn { color: #b45309; border-color: #fde68a; background: #fffbeb; }
  .liveStatus.loading { color: #4338ca; border-color: #c7d2fe; background: #eef2ff; }
  .livePanel { display: none; width: 100%; margin-top: 8px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); box-shadow: var(--shadow); }
  .livePanel.open { display: block; }
  .livePanel label { display: block; font-size: 12.5px; color: var(--muted); margin-bottom: 6px; }
  .livePanel input { width: 100%; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; outline: none; }
  .livePanel input:focus { border-color: var(--accent); }
  .liveRow { display: flex; gap: 8px; margin-top: 10px; }
  .liveHint { margin-top: 8px; font-size: 11.5px; color: var(--faint); line-height: 1.6; }
  .liveStatus.inline { color: #b45309; border-color: #fde68a; background: #fffbeb; }
  /* 齿轮设置按钮：正方形、图标居中 */
  .btn.sm.icon { padding: 3px 8px; display: inline-flex; align-items: center; justify-content: center; line-height: 1; }
  .btn.sm.icon svg { display: block; }
  /* 语言切换按钮：推到 header 右侧 */
  .btn.sm.lang { margin-left: auto; min-width: 44px; font-weight: 600; letter-spacing: .3px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1 data-i18n="header_title">GitHub Traffic 看板</h1>
    <span class="sub" id="meta"></span>
    <button class="btn sm lang" id="langBtn" title="Switch to English">EN</button>
  </header>

  <div class="live">
    <div class="liveBar">
      <span class="liveStatus" id="liveStatus">内联数据</span>
      <button class="btn sm" id="liveBtn" data-i18n="btn_live">实时刷新</button>
      <button class="btn sm icon" id="gearBtn" title="设置 GitHub 用户名 / PAT" aria-label="设置"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg></button>
    </div>
    <div class="livePanel" id="livePanel">
      <label data-i18n-html="1" data-i18n="set_user_label">GitHub 用户名或主页（如 <code>your-username</code> 或 <code>https://github.com/your-username</code>）</label>
      <input type="text" id="userInput" data-i18n-ph="set_user_ph" placeholder="your-username" autocomplete="off" spellcheck="false">
      <label data-i18n="set_token_label">GitHub PAT（读取 Traffic 必需，仅存本浏览器 localStorage，只发给 api.github.com）</label>
      <input type="password" id="tokenInput" data-i18n-ph="set_token_ph" placeholder="ghp_xxx 或 github_pat_xxx" autocomplete="off" spellcheck="false">
      <div class="liveRow">
        <button class="btn sm" id="tokenSave" data-i18n="btn_save">保存并刷新</button>
        <button class="btn sm" id="tokenClear" data-i18n="btn_clear">清除</button>
      </div>
      <div class="liveHint" data-i18n-html="1" data-i18n="hint_live">填入<strong>用户名 + 你的 PAT</strong> 后，看板会列出该用户名下全部仓库（按 owner 过滤、分页拉取，含私有仓库需 PAT 有权限），并实时抓取每个仓库的 Traffic（views / clones / 来源 / 路径）合并渲染，仓库芯片也会动态刷新成本次发现的全集。Traffic 接口<strong>强制要求 PAT</strong>（Fine-grained 需 Administration: read；Classic 需 repo scope）——仅凭用户名无法读取流量数据。PAT 不写入 HTML、不发往任何第三方；公共电脑用完点「清除」。</div>
    </div>
  </div>

  <div class="bar">
    <div class="chips" id="repoChips"></div>
    <div class="side">
      <button class="btn sm" id="selAll" data-i18n="btn_selAll">全选</button>
      <button class="btn sm" id="selNone" data-i18n="btn_selNone">清空</button>
      <span class="sub" id="selCount"></span>
    </div>
  </div>

  <div class="view-bar">
    <div class="seg" id="view">
      <button data-v="combined" data-i18n="view_combined">合并视图</button>
      <button data-v="compare" data-i18n="view_compare">对比视图</button>
    </div>
    <div class="seg" id="metric">
      <button data-m="views">Views</button>
      <button data-m="clones">Clones</button>
    </div>
    <span class="sub" id="viewHint"></span>
  </div>

  <div class="cards" id="cards"></div>

  <section class="panel">
    <div class="panel-head">
      <h2 id="chartTitle" data-i18n="chart_title_combined">每日 Views / Clones</h2>
      <div class="seg" id="range">
        <button data-r="7" data-i18n="r_7">7 天</button>
        <button data-r="30" data-i18n="r_30">30 天</button>
        <button data-r="90" data-i18n="r_90">90 天</button>
        <button data-r="0" data-i18n="r_0">全部</button>
      </div>
    </div>
    <div class="legend" id="legend"></div>
    <div id="chart"><div class="tip" id="tip"></div></div>
    <div class="note" id="chartNote"></div>
  </section>

  <div class="cols">
    <section class="panel">
      <div class="panel-head"><h2 data-i18n="section_refs">流量来源 Top 10</h2><span class="sub" id="refMeta"></span></div>
      <div id="refs"></div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2 data-i18n="section_paths">热门路径 Top 10</h2><span class="sub" id="pathMeta"></span></div>
      <div id="paths"></div>
    </section>
  </div>

  <section class="panel">
    <div class="panel-head">
      <h2 id="tableTitle" data-i18n="table_title_daily">每日明细</h2>
      <div class="seg" id="tableRange">
        <button data-r="14" data-i18n="tr_14">最近 14 天</button>
        <button data-r="30" data-i18n="r_30">30 天</button>
        <button data-r="0" data-i18n="r_0">全部</button>
      </div>
    </div>
    <div class="scroll"><table id="table"></table></div>
  </section>

  <footer id="footer"></footer>
</div>

<script>window.TRAFFIC_DATA = __TRAFFIC_DATA__;</script>
<script>
(function () {
  "use strict";
  var DATA = window.TRAFFIC_DATA || { repos: {} };
  var ALL = Object.keys(DATA.repos || {});
  var PALETTE = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444",
                 "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1"];
  var COMPARE_LIMIT = 10;   // 对比视图最多画几条线
  var liveOK = false;       // 当前展示的数据是否已来自实时拉取（而非内联种子）

  var state = {
    selected: ALL.slice(),
    view: "combined",
    metric: "views",
    range: 30,
    tableRange: 30,
    sortKey: "vc",
    sortDir: -1
  };

  var $ = function (id) { return document.getElementById(id); };

  /* ---------- 国际化（中文 / English） ---------- */
  var LS_LANG = "gh_traffic_dashboard_lang_v1";
  var LANG = "zh";
  try { var _l = localStorage.getItem(LS_LANG); if (_l === "zh" || _l === "en") LANG = _l; } catch (e) {}
  var I18N = {
    zh: {
      page_title: "GitHub Traffic 看板",
      header_title: "GitHub Traffic 看板",
      btn_live: "实时刷新",
      set_user_label: "GitHub 用户名或主页（如 <code>your-username</code> 或 <code>https://github.com/your-username</code>）",
      set_user_ph: "your-username",
      set_token_label: "GitHub PAT（读取 Traffic 必需，仅存本浏览器 localStorage，只发给 api.github.com）",
      set_token_ph: "ghp_xxx 或 github_pat_xxx",
      btn_save: "保存并刷新",
      btn_clear: "清除",
      hint_live: '填入<strong>用户名 + 你的 PAT</strong> 后，看板会列出该用户名下全部仓库（按 owner 过滤、分页拉取，含私有仓库需 PAT 有权限），并实时抓取每个仓库的 Traffic（views / clones / 来源 / 路径）合并渲染，仓库芯片也会动态刷新成本次发现的全集。Traffic 接口<strong>强制要求 PAT</strong>（Fine-grained 需 Administration: read；Classic 需 repo scope）——仅凭用户名无法读取流量数据。PAT 不写入 HTML、不发往任何第三方；公共电脑用完点「清除」。',
      btn_selAll: "全选",
      btn_selNone: "清空",
      view_combined: "合并视图",
      view_compare: "对比视图",
      r_7: "7 天",
      r_30: "30 天",
      r_90: "90 天",
      r_0: "全部",
      tr_14: "最近 14 天",
      section_refs: "流量来源 Top 10",
      section_paths: "热门路径 Top 10",
      chart_title_combined: "每日 Views / Clones",
      table_title_daily: "每日明细",
      chips_none: "暂无仓库数据",
      chip_days: "{n}天",
      sel_count: "已选 {a} / {b} 个仓库",
      card_nodata_k: "尚未加载数据",
      card_nodata_d: "点齿轮设置 GitHub 用户名与 PAT 后刷新",
      card_noselect_k: "未选择仓库",
      card_noselect_d: "请在上方至少选择一个仓库",
      card_total_views: "累计 Views",
      card_total_clones: "累计 Clones",
      card_last_views: "最近一天 Views",
      card_last_clones: "最近一天 Clones",
      card_avg_views: "近 30 天日均 Views",
      card_uniq_visitors: "独立访客合计 {n}",
      card_uniq_cloners: "独立克隆合计 {n}",
      card_detail_unique: "{date} · 独立 {n}",
      card_avg_note: "按合并序列计算",
      chart_empty_nodata: "尚未加载数据<br>点齿轮设置 GitHub 用户名与 PAT 并刷新",
      chart_empty_norepo: "所选仓库暂无数据<br>先在 GitHub Actions 手动运行一次 Collect GitHub Traffic",
      chart_title_compare: "各仓库 {m} 对比",
      legend_days: "共 {n} 天",
      legend_top: "仅显示 Top {n}，另有 {m} 个仓库未绘制",
      hint_compare: "每个仓库一条线，按总量取 Top {n}；下方表格可点表头排序。",
      hint_combined: "所选仓库按天相加，看总量趋势。",
      note_discontinued: "对比视图下，某仓库缺失的日期会断开，不按 0 处理。",
      note_combined: "合并视图把所选仓库按天相加；「独立访客」是各仓库之和，跨仓库不去重，仅作参考。",
      note_partial: "当天数据尚未走完（每天只采集一次），末位数值偏低，次日回看才准确。",
      snap_meta: "快照 {date}（{n} 仓聚合）",
      snap_nodata: "尚未加载数据",
      snap_noselect: "未选择仓库",
      snap_empty: "暂无数据<br>首次采集后由 Actions 自动生成",
      snap_referrer: "来源",
      snap_path: "路径",
      snap_count: "次数",
      snap_unique: "独立",
      table_nodata: "尚未加载数据",
      table_noselect: "未选择仓库",
      table_empty: "暂无数据",
      th_date: "日期",
      th_unique_visitors: "独立访客",
      th_unique_cloners: "独立克隆",
      table_title_repos: "各仓库汇总",
      table_title_daily_full: "每日明细（所选仓库合并）",
      col_repo: "仓库",
      col_total_views: "累计 Views",
      col_uniq_visitors: "独立访客",
      col_total_clones: "累计 Clones",
      col_uniq_cloners: "独立克隆",
      col_v30: "近30天 Views",
      col_c30: "近30天 Clones",
      col_days: "天数",
      referrer_direct: "直接访问",
      unique: "独立",
      meta_nodata: "尚未加载数据",
      meta_latest: "数据最新到 {d}",
      meta_repos: "共 {n} 个仓库",
      status_nodata: "尚未加载数据 · 点齿轮设置 GitHub 用户名与 PAT 后刷新",
      status_live: "实时数据 · {n} 个仓库 · 最新 {d}",
      status_seed: "内联种子 · 最新 {d}（点「实时刷新」获取最新）",
      err_401: "PAT 无效/已过期/被吊销，请在 GitHub 重新生成（Settings → Developer settings → PAT）",
      err_403: "PAT 无权限读 Traffic：Classic 需勾选 repo scope；Fine-grained 需对该仓库授权 Administration: read，并确认已在「Repository access」中加入这两个仓库；限流请稍后重试",
      err_404: "接口或仓库不存在（检查用户名拼写）",
      err_user_empty: "请填写 GitHub 用户名",
      err_pat_empty: "请填写 PAT（Traffic 接口强制要求）",
      status_listing: "正在列出 {u} 的仓库…",
      status_norepo: "未找到 {u} 名下的仓库（或无权限）",
      status_fetching: "正在抓取 {n} 个仓库的流量…",
      status_refreshed_fail: "已刷新（{ok}/{total}），失败：{list}",
      status_list_fail: "列出仓库失败：{msg}",
      err_no_creds: "请先点齿轮 ⚙ 设置 GitHub 用户名与 PAT",
      footer: "数据由 GitHub Actions 每日自动采集 · 重新采集后本文件会被自动更新<br>GitHub 官方只保留最近 14 天，本看板的历史由 <code>data/traffic-history.json</code> 累积保存"
    },
    en: {
      page_title: "GitHub Traffic Dashboard",
      header_title: "GitHub Traffic Dashboard",
      btn_live: "Refresh",
      set_user_label: 'GitHub username or profile (e.g. <code>your-username</code> or <code>https://github.com/your-username</code>)',
      set_user_ph: "your-username",
      set_token_label: "GitHub PAT (required to read Traffic; stored only in this browser's localStorage and sent only to api.github.com)",
      set_token_ph: "ghp_xxx or github_pat_xxx",
      btn_save: "Save & Refresh",
      btn_clear: "Clear",
      hint_live: 'After entering your <strong>username + PAT</strong>, the dashboard lists all repositories under that user (owner-filtered, paginated; private repos need PAT access), then live-fetches each repo\'s Traffic (views / clones / referrers / paths) and merges them, and refreshes repo chips to the full set discovered this run. The Traffic API <strong>requires a PAT</strong> (Fine-grained needs Administration: read; Classic needs repo scope) — username alone cannot read traffic. The PAT is never written into the HTML or sent to any third party; click "Clear" when done on a shared computer.',
      btn_selAll: "Select all",
      btn_selNone: "Clear",
      view_combined: "Combined",
      view_compare: "Compare",
      r_7: "7 days",
      r_30: "30 days",
      r_90: "90 days",
      r_0: "All",
      tr_14: "Last 14 days",
      section_refs: "Top 10 Traffic Referrers",
      section_paths: "Top 10 Popular Paths",
      chart_title_combined: "Daily Views / Clones",
      table_title_daily: "Daily detail",
      chips_none: "No repository data",
      chip_days: "{n}d",
      sel_count: "Selected {a} / {b} repos",
      card_nodata_k: "No data loaded",
      card_nodata_d: "Set username & PAT via the gear, then refresh",
      card_noselect_k: "No repository selected",
      card_noselect_d: "Select at least one repository above",
      card_total_views: "Total Views",
      card_total_clones: "Total Clones",
      card_last_views: "Views (last day)",
      card_last_clones: "Clones (last day)",
      card_avg_views: "Avg daily Views (30d)",
      card_uniq_visitors: "Unique visitors: {n}",
      card_uniq_cloners: "Unique cloners: {n}",
      card_detail_unique: "{date} · unique {n}",
      card_avg_note: "From combined series",
      chart_empty_nodata: "No data loaded<br>Set username & PAT via the gear, then refresh",
      chart_empty_norepo: "No data for selected repos<br>Run Collect GitHub Traffic once in GitHub Actions first",
      chart_title_compare: "Per-repo {m} comparison",
      legend_days: "{n} days total",
      legend_top: "Showing Top {n}; {m} more repos not drawn",
      hint_compare: "One line per repo, Top {n} by total; click table headers to sort.",
      hint_combined: "Selected repos summed by day, showing the total trend.",
      note_discontinued: "In compare view, missing days for a repo are left blank (not treated as 0).",
      note_combined: "Combined view sums selected repos by day; \"unique visitors\" is the sum across repos (not de-duplicated across repos) and is for reference only.",
      note_partial: "Today's data may be incomplete (collected once per day); the latest value runs low and is accurate when viewed the next day.",
      snap_meta: "Snapshot {date} ({n} repos)",
      snap_nodata: "No data loaded",
      snap_noselect: "No repository selected",
      snap_empty: "No data<br>Generated automatically after the first collection",
      snap_referrer: "Referrer",
      snap_path: "Path",
      snap_count: "Count",
      snap_unique: "Unique",
      table_nodata: "No data loaded",
      table_noselect: "No repository selected",
      table_empty: "No data",
      th_date: "Date",
      th_unique_visitors: "Unique visitors",
      th_unique_cloners: "Unique cloners",
      table_title_repos: "Per-repo summary",
      table_title_daily_full: "Daily detail (selected repos combined)",
      col_repo: "Repo",
      col_total_views: "Total Views",
      col_uniq_visitors: "Unique visitors",
      col_total_clones: "Total Clones",
      col_uniq_cloners: "Unique cloners",
      col_v30: "Views (30d)",
      col_c30: "Clones (30d)",
      col_days: "Days",
      referrer_direct: "Direct",
      unique: "unique",
      meta_nodata: "No data loaded",
      meta_latest: "Data up to {d}",
      meta_repos: "{n} repos",
      status_nodata: "No data loaded · set username & PAT via the gear, then refresh",
      status_live: "Live · {n} repos · latest {d}",
      status_seed: "Inline seed · latest {d} (click Refresh for the latest)",
      err_401: "PAT invalid / expired / revoked — regenerate it on GitHub (Settings → Developer settings → PAT)",
      err_403: "PAT has no permission to read Traffic: Classic needs the repo scope; Fine-grained needs Administration: read for this repo, and the repo must be added under Repository access; if rate-limited, retry later",
      err_404: "Endpoint or repo not found (check the username spelling)",
      err_user_empty: "Enter your GitHub username",
      err_pat_empty: "Enter a PAT (required by the Traffic API)",
      status_listing: "Listing {u}'s repositories…",
      status_norepo: "No repositories found under {u} (or no access)",
      status_fetching: "Fetching traffic for {n} repos…",
      status_refreshed_fail: "Refreshed ({ok}/{total}), failed: {list}",
      status_list_fail: "Failed to list repos: {msg}",
      err_no_creds: "Click the gear ⚙ to set your GitHub username & PAT first",
      footer: "Collected daily by GitHub Actions · this file updates after each run<br>GitHub keeps only the last 14 days; this dashboard accumulates history in <code>data/traffic-history.json</code>"
    }
  };
  function i18n(key, params) {
    var dict = (I18N[LANG] && I18N[LANG].hasOwnProperty(key)) ? I18N[LANG] : I18N.zh;
    var s = dict.hasOwnProperty(key) ? dict[key] : key;
    if (params) { for (var _k in params) { if (params.hasOwnProperty(_k)) s = s.split("{" + _k + "}").join(String(params[_k])); } }
    return s;
  }
  function applyStaticI18n() {
    // 非浏览器环境（如 node 测试 stub）可能没有 querySelectorAll，安全跳过静态文案替换
    if (typeof document.querySelectorAll === "function") {
      var nodes = document.querySelectorAll("[data-i18n]");
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i], key = el.getAttribute("data-i18n");
        if (el.hasAttribute("data-i18n-html")) el.innerHTML = i18n(key); else el.textContent = i18n(key);
      }
      var phs = document.querySelectorAll("[data-i18n-ph]");
      for (var j = 0; j < phs.length; j++) phs[j].setAttribute("placeholder", i18n(phs[j].getAttribute("data-i18n-ph")));
    }
    document.title = i18n("page_title");
    var lb = $("langBtn");
    if (lb) { lb.textContent = (LANG === "zh") ? "EN" : "中文"; lb.title = (LANG === "zh") ? "Switch to English" : "切换到中文"; }
  }
  function applyI18n() { applyStaticI18n(); renderMeta(); renderSource(); renderChips(); renderAll(); $("footer").innerHTML = i18n('footer'); }
  function setLang(l) { if (l !== "zh" && l !== "en") return; LANG = l; try { localStorage.setItem(LS_LANG, l); } catch (e) {} applyI18n(); }

  var fmt = function (n) { return (n || 0).toLocaleString("en-US"); };
  var fullDate = function (ts) { return (ts || "").slice(0, 10); };   // YYYY-MM-DD
  var shortDate = function (d) { return (d || "").slice(5); };        // MM-DD
  // 跨年时轴标签补两位年份（YY-MM-DD），否则相邻两年的 12-30 会都显示成 "12-30"。
  // 用两位年份而非四位，是为了在窄屏上避免标签重叠（轴最多约 7 个标签）。
  var axisDate = function (d, crossYear) { return crossYear ? String(d).slice(2) : shortDate(d); };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function niceMax(v) {
    if (!v || v <= 0) return 5;
    var exp = Math.floor(Math.log10(v)), base = Math.pow(10, exp), n = v / base, m;
    if (n <= 1) m = 1; else if (n <= 2) m = 2; else if (n <= 2.5) m = 2.5;
    else if (n <= 5) m = 5; else m = 10;
    return m * base;
  }
  function slice(arr, r) { return (!arr ? [] : (r > 0 ? arr.slice(-r) : arr.slice())); }
  function sum(arr, f) {
    return (arr || []).reduce(function (s, p) { return s + (p[f] || 0); }, 0);
  }
  function byDate(points) {
    var m = {};
    (points || []).forEach(function (p) { m[fullDate(p.timestamp)] = p; });
    return m;
  }

  /* ---------- 数据聚合 ---------- */
  function seriesOf(repo, key) { return ((DATA.repos[repo] || {})[key]) || []; }

  // 把「选中仓库」的某个指标按日期相加，得到一条合并序列
  function combinedSeries(key) {
    var map = {};
    state.selected.forEach(function (r) {
      seriesOf(r, key).forEach(function (p) {
        var d = fullDate(p.timestamp);
        if (!map[d]) map[d] = { date: d, count: 0, uniques: 0 };
        map[d].count += (p.count || 0);
        map[d].uniques += (p.uniques || 0);
      });
    });
    return Object.keys(map).sort().map(function (d) { return map[d]; });
  }

  function repoTotal(repo, key, field) { return sum(seriesOf(repo, key), field); }

  /* ---------- 顶部仓库选择 ---------- */
  function renderChips() {
    var box = $("repoChips");
    if (!ALL.length) { box.innerHTML = '<span class="sub">' + i18n('chips_none') + '</span>'; return; }
    box.innerHTML = ALL.map(function (r) {
      var on = state.selected.indexOf(r) >= 0 ? " on" : "";
      var n = seriesOf(r, "views").length;
      return '<span class="chip' + on + '" data-r="' + escapeHtml(r) + '">' +
             escapeHtml(r) + '<span class="n">' + i18n('chip_days', { n: n }) + '</span></span>';
    }).join("");
    box.querySelectorAll(".chip").forEach(function (c) {
      c.addEventListener("click", function () {
        var r = c.dataset.r, i = state.selected.indexOf(r);
        if (i >= 0) state.selected.splice(i, 1); else state.selected.push(r);
        renderChips(); renderAll();
      });
    });
    $("selCount").textContent = i18n('sel_count', { a: state.selected.length, b: ALL.length });
  }

  /* ---------- 卡片 ---------- */
  function renderCards() {
    if (!ALL.length) {
      $("cards").innerHTML = '<div class="card"><div class="k">' + i18n('card_nodata_k') + '</div>' +
        '<div class="v">—</div><div class="d">' + i18n('card_nodata_d') + '</div></div>';
      return;
    }
    if (!state.selected.length) {
      $("cards").innerHTML = '<div class="card"><div class="k">' + i18n('card_noselect_k') + '</div>' +
        '<div class="v">—</div><div class="d">' + i18n('card_noselect_d') + '</div></div>';
      return;
    }
    var sel = state.selected;
    var vc = sel.reduce(function (s, r) { return s + repoTotal(r, "views", "count"); }, 0);
    var vu = sel.reduce(function (s, r) { return s + repoTotal(r, "views", "uniques"); }, 0);
    var cc = sel.reduce(function (s, r) { return s + repoTotal(r, "clones", "count"); }, 0);
    var cu = sel.reduce(function (s, r) { return s + repoTotal(r, "clones", "uniques"); }, 0);

    var merged = combinedSeries("views");
    var last = merged[merged.length - 1] || {};
    var lastC = (combinedSeries("clones").slice(-1)[0]) || {};
    var recent = slice(merged, 30);
    var avg = recent.length ? Math.round(sum(recent, "count") / recent.length) : 0;

    var cards = [
      { k: i18n('card_total_views'), v: fmt(vc), d: i18n('card_uniq_visitors', { n: fmt(vu) }), c: "var(--views)" },
      { k: i18n('card_total_clones'), v: fmt(cc), d: i18n('card_uniq_cloners', { n: fmt(cu) }), c: "var(--clones)" },
      { k: i18n('card_last_views'), v: fmt(last.count), d: i18n('card_detail_unique', { date: (last.date || "—"), n: fmt(last.uniques) }), c: "var(--views)" },
      { k: i18n('card_last_clones'), v: fmt(lastC.count), d: i18n('card_detail_unique', { date: (lastC.date || "—"), n: fmt(lastC.uniques) }), c: "var(--clones)" },
      { k: i18n('card_avg_views'), v: fmt(avg), d: i18n('card_avg_note'), c: "#8b5cf6" }
    ];
    $("cards").innerHTML = cards.map(function (c) {
      return '<div class="card"><div class="k"><span class="dot" style="background:' + c.c + '"></span>' +
             escapeHtml(c.k) + '</div><div class="v">' + c.v + '</div><div class="d">' + escapeHtml(c.d) + '</div></div>';
    }).join("");
  }

  /* ---------- 图表 ---------- */
  // series: [{name, color, points:[{date,count,uniques}], fill:bool}]
  function drawChart(series) {
    var host = $("chart"), tip = null;

    var dates = {};
    series.forEach(function (s) { s.points.forEach(function (p) { dates[p.date] = 1; }); });
    var axis = Object.keys(dates).sort();
    if (!axis.length) {
      // 用 innerHTML 整体替换（而非 insertAdjacentHTML 前插），避免空提示框无限累加
      if (!ALL.length) host.innerHTML = '<div class="empty">' + i18n('chart_empty_nodata') + '</div>';
      else host.innerHTML = '<div class="empty">' + i18n('chart_empty_norepo') + '</div>';
      return null;
    }

    var maxV = 1;
    series.forEach(function (s) {
      s.points.forEach(function (p) { if (p.count > maxV) maxV = p.count; });
    });
    var maxY = niceMax(maxV);

    var w = Math.max(320, host.clientWidth || 900), h = host.classList.contains("tall") ? 340 : 300;
    var padL = 46, padR = 14, padT = 14, padB = 26;
    var iw = w - padL - padR, ih = h - padT - padB, n = axis.length;
    var X = function (i) { return n === 1 ? padL + iw / 2 : padL + (i * iw) / (n - 1); };
    var Y = function (v) { return padT + (1 - v / maxY) * ih; };

    var svg = '<svg width="100%" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">';
    svg += '<defs>';
    series.forEach(function (s, i) {
      if (!s.fill) return;
      svg += '<linearGradient id="g' + i + '" x1="0" y1="0" x2="0" y2="1">' +
             '<stop offset="0%" stop-color="' + s.color + '" stop-opacity=".18"/>' +
             '<stop offset="100%" stop-color="' + s.color + '" stop-opacity="0"/></linearGradient>';
    });
    svg += '</defs><g class="grid">';
    for (var t = 0; t <= 4; t++) {
      var vy = padT + (t * ih) / 4;
      svg += '<line x1="' + padL + '" y1="' + vy + '" x2="' + (w - padR) + '" y2="' + vy + '"/>';
    }
    svg += '</g>';
    for (var g = 0; g <= 4; g++) {
      svg += '<text class="axis" x="' + (padL - 8) + '" y="' + (padT + (g * ih) / 4 + 4) +
             '" text-anchor="end">' + fmt(Math.round((maxY * (4 - g)) / 4)) + '</text>';
    }
    var step = Math.max(1, Math.ceil(n / 6));
    // 轴范围跨年时给每个标签补上两位年份，避免同一组 MM-DD 分不清是哪一年
    var crossYear = String(axis[0]).slice(0, 4) !== String(axis[n - 1]).slice(0, 4);
    for (var i2 = 0; i2 < n; i2 += step) {
      svg += '<text class="axis" x="' + X(i2) + '" y="' + (h - 8) + '" text-anchor="middle">' +
             escapeHtml(axisDate(axis[i2], crossYear)) + '</text>';
    }

    series.forEach(function (s, si) {
      var map = {};
      s.points.forEach(function (p) { map[p.date] = p; });
      var d = "", area = "", drawing = false;
      for (var k = 0; k < n; k++) {
        var pt = map[axis[k]];
        if (!pt) { drawing = false; continue; }   // 缺数据的日期断开，不画到 0 造成假象
        var cmd = (drawing ? "L" : "M") + X(k).toFixed(1) + " " + Y(pt.count).toFixed(1) + " ";
        d += cmd;
        if (s.fill) area += cmd;
        drawing = true;
      }
      if (s.fill && area) {
        var first = X(0).toFixed(1), lastX = X(n - 1).toFixed(1);
        svg += '<path d="' + area + "L" + lastX + " " + Y(0).toFixed(1) +
               " L" + first + " " + Y(0).toFixed(1) + ' Z" fill="url(#g' + si + ')"/>';
      }
      svg += '<path class="line" d="' + d + '" stroke="' + s.color + '"/>';
    });

    svg += '<g id="cursor" style="opacity:0"><line y1="' + padT + '" y2="' + (padT + ih) +
           '" stroke="#c3c9d2" stroke-dasharray="3 3"/>' +
           series.map(function () {
             return '<circle r="3.5" fill="#fff" stroke-width="2"/>';
           }).join("") + '</g>' +
           '<rect id="hit" x="' + padL + '" y="' + padT + '" width="' + iw + '" height="' + ih + '" fill="transparent"/>';
    svg += '</svg>';
    // 每次重绘都把 svg 与提示框整体替换，并重建 #tip（否则旧 svg / 空提示会堆积）
    host.innerHTML = '<div class="tip" id="tip"></div>' + svg;

    var svgEl = host.querySelector("svg");
    var tip = host.querySelector("#tip");
    var cursor = svgEl.querySelector("#cursor");
    var hit = svgEl.querySelector("#hit");
    var marks = cursor.querySelectorAll("circle");
    var line = cursor.querySelector("line");

    function move(ev) {
      var box = svgEl.getBoundingClientRect(), scale = w / box.width;
      var mx = (ev.clientX - box.left) * scale;
      var idx = Math.round(((mx - padL) / (iw || 1)) * (n - 1));
      idx = Math.max(0, Math.min(n - 1, idx));
      cursor.setAttribute("style", "opacity:1");
      line.setAttribute("x1", X(idx)); line.setAttribute("x2", X(idx));

      var rows = "", top = padT + ih;
      series.forEach(function (s, si) {
        var map = {};
        s.points.forEach(function (p) { map[p.date] = p; });
        var pt = map[axis[idx]];
        if (pt) {
          marks[si].setAttribute("cx", X(idx));
          marks[si].setAttribute("cy", Y(pt.count));
          marks[si].setAttribute("stroke", s.color);
          marks[si].setAttribute("style", "opacity:1");
          if (Y(pt.count) < top) top = Y(pt.count);
          rows += '<div class="r"><i style="background:' + s.color + '"></i>' +
                  escapeHtml(s.name) + ' <b>' + fmt(pt.count) + '</b><em>/ ' + fmt(pt.uniques) + ' ' + i18n('unique') + '</em></div>';
        } else if (marks[si]) {
          marks[si].setAttribute("style", "opacity:0");
        }
      });
      tip.innerHTML = '<div class="t">' + axis[idx] + '</div>' + rows;
      tip.classList.add("on");
      tip.style.left = Math.max(80, Math.min(box.width - 80, X(idx) / scale)) + "px";
      tip.style.top = Math.max(50, top / scale) + "px";
    }
    hit.addEventListener("mousemove", move);
    hit.addEventListener("mouseleave", function () {
      tip.classList.remove("on");
      cursor.setAttribute("style", "opacity:0");
    });
    return { dates: axis, series: series };
  }

  function buildChartSeries() {
    var sel = state.selected;
    if (!sel.length) return { series: [], truncated: 0 };

    if (state.view === "compare") {
      var key = state.metric;
      var ranked = sel.slice().sort(function (a, b) {
        return sum(seriesOf(b, key), "count") - sum(seriesOf(a, key), "count");
      });
      var top = ranked.slice(0, COMPARE_LIMIT);
      return {
        series: top.map(function (r, i) {
          return {
            name: r,
            color: PALETTE[i % PALETTE.length],
            points: seriesOf(r, key).map(function (p) {
              return { date: fullDate(p.timestamp), count: p.count || 0, uniques: p.uniques || 0 };
            }),
            fill: false
          };
        }),
        truncated: ranked.length - top.length
      };
    }

    return {
      series: [
        { name: "Views", color: "#3b82f6", points: combinedSeries("views"), fill: true },
        { name: "Clones", color: "#10b981", points: combinedSeries("clones"), fill: false }
      ],
      truncated: 0
    };
  }

  function renderChart() {
    var host = $("chart");
    host.classList.toggle("tall", state.view === "compare");

    var built = buildChartSeries();
    if (state.view === "compare") {
      $("chartTitle").textContent = i18n('chart_title_compare', { m: (state.metric === "views" ? "Views" : "Clones") });
      $("metric").style.display = "";
    } else {
      $("chartTitle").textContent = i18n('chart_title_combined');
      $("metric").style.display = "none";
    }

    var rangeSlice = function (pts) {
      return state.range > 0 ? pts.slice(-state.range) : pts;
    };
    var series = built.series.map(function (s) {
      return { name: s.name, color: s.color, points: rangeSlice(s.points), fill: s.fill };
    });

    var drawn = drawChart(series);

    var legend = series.map(function (s) {
      return '<span><span class="dot" style="background:' + s.color + '"></span> ' +
             escapeHtml(s.name) + ' <b>' + fmt(sum(s.points, "count")) + '</b></span>';
    }).join("");
    if (drawn) legend += '<span>' + i18n('legend_days', { n: drawn.dates.length }) + '</span>';
    if (built.truncated > 0) {
      legend += '<span style="color:#d97706">' + i18n('legend_top', { n: COMPARE_LIMIT, m: built.truncated }) +
                '</span>';
    }
    $("legend").innerHTML = legend;

    var notes = [];
    if (state.view === "compare") {
      notes.push(i18n('note_discontinued'));
    } else if (state.selected.length > 1) {
      notes.push(i18n('note_combined'));
    }
    var merged = combinedSeries("views");
    if (merged.length && merged[merged.length - 1].date === new Date().toISOString().slice(0, 10)) {
      notes.push(i18n('note_partial'));
    }
    $("chartNote").textContent = notes.join(" ");
  }

  /* ---------- 快照（多仓库聚合） ---------- */
  function aggregateSnapshot(store, keyOf) {
    var map = {}, metas = [];
    state.selected.forEach(function (r) {
      var e = store && store.repos && store.repos[r];
      var snaps = (e && e.snapshots) || [];
      if (!snaps.length) return;
      var last = snaps[snaps.length - 1];
      metas.push((last.captured_at || "").slice(0, 10));
      (last.items || []).forEach(function (it) {
        var k = keyOf(it);
        if (!map[k]) map[k] = { name: k, title: it.title || "", count: 0, uniques: 0 };
        map[k].count += (it.count || 0);
        map[k].uniques += (it.uniques || 0);
      });
    });
    var items = Object.keys(map).map(function (k) { return map[k]; })
      .sort(function (a, b) { return b.count - a.count; }).slice(0, 10);
    var latestDate = metas.length ? metas.slice().sort().pop() : "";
    return { items: items, meta: metas.length ? i18n('snap_meta', { date: latestDate, n: metas.length }) : "" };
  }

  function renderSnapshot(el, metaEl, store, label, keyOf, colorClass) {
    if (!ALL.length) {
      el.innerHTML = '<div class="empty">' + i18n('snap_nodata') + '</div>'; metaEl.textContent = ""; return;
    }
    if (!state.selected.length) {
      el.innerHTML = '<div class="empty">' + i18n('snap_noselect') + '</div>'; metaEl.textContent = ""; return;
    }
    var agg = aggregateSnapshot(store, keyOf);
    metaEl.textContent = agg.meta;
    if (!agg.items.length) {
      el.innerHTML = '<div class="empty">' + i18n('snap_empty') + '</div>';
      return;
    }
    var max = Math.max.apply(null, agg.items.map(function (i) { return i.count; }).concat([1]));
    el.innerHTML = '<table><thead><tr><th>' + label + '</th><th class="num">' + i18n('snap_count') + '</th>' +
      '<th class="num">' + i18n('snap_unique') + '</th><th style="width:32%"></th></tr></thead><tbody>' +
      agg.items.map(function (it) {
        var title = it.title ? ' title="' + escapeHtml(it.title) + '"' : "";
        return '<tr><td class="name"' + title + '>' + escapeHtml(it.name) + '</td>' +
               '<td class="num">' + fmt(it.count) + '</td>' +
               '<td class="num">' + fmt(it.uniques) + '</td>' +
               '<td><div class="bar-cell ' + colorClass + '" style="width:' +
               Math.max(2, (it.count / max) * 100) + '%"></div></td></tr>';
      }).join("") + '</tbody></table>';
  }

  /* ---------- 表格 ---------- */
  function renderDailyTable() {
    if (!ALL.length) { $("table").innerHTML = '<tbody><tr><td class="empty">' + i18n('table_nodata') + '</td></tr></tbody>'; return; }
    if (!state.selected.length) { $("table").innerHTML = '<tbody><tr><td class="empty">' + i18n('table_noselect') + '</td></tr></tbody>'; return; }
    var views = slice(combinedSeries("views"), state.tableRange).slice().reverse();
    var clones = byDate(combinedSeries("clones"));
    if (!views.length) { $("table").innerHTML = '<tbody><tr><td class="empty">' + i18n('table_empty') + '</td></tr></tbody>'; return; }
    var maxV = Math.max.apply(null, views.map(function (p) { return p.count; }).concat([1]));
    $("table").innerHTML =
      '<thead><tr><th>' + i18n('th_date') + '</th><th class="num">Views</th><th class="num">' + i18n('th_unique_visitors') + '</th>' +
      '<th class="num">Clones</th><th class="num">' + i18n('th_unique_cloners') + '</th><th style="width:22%"></th></tr></thead><tbody>' +
      views.map(function (p) {
        var c = clones[p.date] || {};
        return '<tr><td>' + p.date + '</td><td class="num">' + fmt(p.count) + '</td>' +
          '<td class="num">' + fmt(p.uniques) + '</td><td class="num">' + fmt(c.count) + '</td>' +
          '<td class="num">' + fmt(c.uniques) + '</td>' +
          '<td><div class="bar-cell" style="width:' + Math.max(2, (p.count / maxV) * 100) + '%"></div></td></tr>';
      }).join("") + '</tbody></table>';
  }

  function renderReposTable() {
    if (!ALL.length) { $("table").innerHTML = '<tbody><tr><td class="empty">' + i18n('table_nodata') + '</td></tr></tbody>'; return; }
    if (!state.selected.length) { $("table").innerHTML = '<tbody><tr><td class="empty">' + i18n('table_noselect') + '</td></tr></tbody>'; return; }
    var rows = state.selected.map(function (r) {
      var v = seriesOf(r, "views"), c = seriesOf(r, "clones");
      return {
        repo: r,
        vc: sum(v, "count"), vu: sum(v, "uniques"),
        cc: sum(c, "count"), cu: sum(c, "uniques"),
        v30: sum(slice(v, 30), "count"), c30: sum(slice(c, 30), "count"),
        days: v.length
      };
    });
    var k = state.sortKey, dir = state.sortDir;
    rows.sort(function (a, b) {
      var x = a[k], y = b[k];
      if (typeof x === "string") return dir * x.localeCompare(y);
      return dir * (x - y);
    });

    var cols = [
      { k: "repo", tk: "col_repo", num: false },
      { k: "vc", tk: "col_total_views", num: true },
      { k: "vu", tk: "col_uniq_visitors", num: true },
      { k: "cc", tk: "col_total_clones", num: true },
      { k: "cu", tk: "col_uniq_cloners", num: true },
      { k: "v30", tk: "col_v30", num: true },
      { k: "c30", tk: "col_c30", num: true },
      { k: "days", tk: "col_days", num: true }
    ];
    var maxV = Math.max.apply(null, rows.map(function (r) { return r.vc; }).concat([1]));
    $("table").innerHTML = '<thead><tr>' + cols.map(function (c) {
      var cls = "sortable" + (c.num ? " num" : "") + (c.k === k ? " sorted" + (dir === 1 ? " asc" : "") : "");
      return '<th class="' + cls + '" data-k="' + c.k + '">' + i18n(c.tk) + '</th>';
    }).join("") + '</tr></thead><tbody>' + rows.map(function (r) {
      return '<tr><td class="name" title="' + escapeHtml(r.repo) + '">' + escapeHtml(r.repo) + '</td>' +
        '<td class="num">' + fmt(r.vc) + '</td><td class="num">' + fmt(r.vu) + '</td>' +
        '<td class="num">' + fmt(r.cc) + '</td><td class="num">' + fmt(r.cu) + '</td>' +
        '<td class="num">' + fmt(r.v30) + '</td><td class="num">' + fmt(r.c30) + '</td>' +
        '<td class="num">' + fmt(r.days) + '</td></tr>';
    }).join("") + '</tbody></table>';

    $("table").querySelectorAll("th.sortable").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.dataset.k;
        if (state.sortKey === key) state.sortDir = -state.sortDir;
        else { state.sortKey = key; state.sortDir = key === "repo" ? 1 : -1; }
        renderReposTable();
      });
    });
  }

  function renderTable() {
    if (state.view === "compare") {
      $("tableTitle").textContent = i18n('table_title_repos');
      renderReposTable();
    } else {
      $("tableTitle").textContent = i18n('table_title_daily_full');
      renderDailyTable();
    }
  }

  /* ---------- 分段控件 ---------- */
  function bindSeg(id, attr, onPick, asNumber) {
    var box = $(id);
    box.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        box.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
        var v = b.getAttribute(attr);
        onPick(asNumber ? Number(v) : v);
      });
    });
  }
  function syncSeg(id, attr, value) {
    $(id).querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("on", String(b.getAttribute(attr)) === String(value));
    });
  }

  /* ---------- 汇总渲染 ---------- */
  function renderAll() {
    renderCards();
    renderChart();
    renderSnapshot($("refs"), $("refMeta"), DATA.referrers, i18n('snap_referrer'),
      function (it) { return it.referrer === "None" ? i18n('referrer_direct') : (it.referrer || "—"); }, "");
    renderSnapshot($("paths"), $("pathMeta"), DATA.paths, i18n('snap_path'),
      function (it) { return it.path || "—"; }, "c");
    renderTable();
    var hint = state.view === "compare"
      ? i18n('hint_compare', { n: COMPARE_LIMIT })
      : i18n('hint_combined');
    $("viewHint").textContent = hint;
  }

  function renderMeta() {
    if (!ALL.length) { $("meta").textContent = i18n('meta_nodata'); return; }
    var parts = [];
    var d = latestDate();
    if (d) parts.push(i18n('meta_latest', { d: d }));
    parts.push(i18n('meta_repos', { n: ALL.length }));
    $("meta").textContent = parts.join(" · ");
  }

  /* ---------- 初始化 ---------- */
  renderMeta();
  renderChips();
  bindSeg("view", "data-v", function (v) { state.view = v; renderAll(); }, false);
  bindSeg("metric", "data-m", function (m) { state.metric = m; renderChart(); }, false);
  bindSeg("range", "data-r", function (r) { state.range = r; renderChart(); }, true);
  bindSeg("tableRange", "data-r", function (r) { state.tableRange = r; renderTable(); }, true);
  syncSeg("view", "data-v", state.view);
  syncSeg("metric", "data-m", state.metric);
  syncSeg("range", "data-r", state.range);
  syncSeg("tableRange", "data-r", state.tableRange);

  $("selAll").addEventListener("click", function () { state.selected = ALL.slice(); renderChips(); renderAll(); });
  $("selNone").addEventListener("click", function () { state.selected = []; renderChips(); renderAll(); });
  if ($("langBtn")) $("langBtn").addEventListener("click", function () { setLang(LANG === "zh" ? "en" : "zh"); });

  renderAll();

  applyI18n();  // 应用已保存的语言偏好（含静态文案、动态重渲染与页脚）

  var rt;
  window.addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(renderChart, 150);
  });

  /* ---------- 实时刷新（用户名 + PAT 直连 GitHub，凭据仅存本地浏览器） ---------- */
  var LS_USER = "gh_traffic_dashboard_user_v1";
  var LS_KEY = "gh_traffic_dashboard_token_v1";
  var API = "https://api.github.com";

  function getLS(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } }
  function setLS(k, v) { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch (e) {} }
  function getUsername() { return getLS(LS_USER); }
  function setUsername(v) { setLS(LS_USER, v); }
  function getToken() { return getLS(LS_KEY); }
  function setToken(v) { setLS(LS_KEY, v); }

  function normalizeUser(s) {
    s = String(s || "").trim();
    s = s.replace(/^https?:\/\//i, "").replace(/^github\.com\//i, "").replace(/\/+$/, "");
    s = s.replace(/^@/, "").split("/")[0].split("?")[0];
    return s;
  }

  function setLiveStatus(text, kind) {
    var el = $("liveStatus");
    if (el) { el.textContent = text; el.className = "liveStatus" + (kind ? " " + kind : ""); }
  }

  // 所有仓库 views/clones 中的最新一天（用于直观判断数据是否最新）
  function latestDate() {
    var max = "";
    (DATA.repos || {}) && Object.keys(DATA.repos).forEach(function (repo) {
      ["views", "clones"].forEach(function (k) {
        (DATA.repos[repo][k] || []).forEach(function (p) {
          var d = dateKey(p.timestamp);
          if (d > max) max = d;
        });
      });
    });
    return max;
  }

  // 单一状态行：实时 / 内联 / 空。显示来源 + 仓库数 + 数据最新日期（不显示刷新操作时间）
  function renderSource() {
    var el = $("liveStatus");
    if (!el) return;
    var d = latestDate();
    if (!ALL.length && !liveOK) {
      el.className = "liveStatus inline";
      el.textContent = i18n('status_nodata');
      return;
    }
    if (liveOK) {
      el.className = "liveStatus ok";
      el.textContent = i18n('status_live', { n: ALL.length, d: (d || "—") });
    } else {
      el.className = "liveStatus inline";
      el.textContent = i18n('status_seed', { d: (d || "—") });
    }
  }

  function friendlyErr(e) {
    var m = (e && e.message) ? String(e.message) : String(e || "错误");
    if (/Bad credentials/i.test(m) || /\b401\b/.test(m)) return i18n('err_401');
    if (/Resource not accessible|personal access token|Must have|403|rate limit/i.test(m))
      return i18n('err_403');
    if (/Not Found|404/i.test(m)) return i18n('err_404');
    return m;
  }

  function ghCall(path, token) {
    var headers = { Accept: "application/vnd.github+json" };
    if (token) headers.Authorization = "Bearer " + token;
    return fetch(API + path, { headers: headers }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j && j.message ? j.message : ("HTTP " + r.status));
        var link = (r.headers && r.headers.get) ? r.headers.get("Link") : null;
        return { json: j, link: link };
      });
    });
  }

  function nextLink(link) {
    if (!link) return null;
    var m = /<([^>]+)>;\s*rel="next"/.exec(link);
    return m ? m[1] : null;
  }

  function dateKey(ts) { return String(ts).slice(0, 10); }
  function mergeDaily(existing, fresh) {
    var map = {};
    (existing || []).forEach(function (p) {
      map[dateKey(p.timestamp)] = { timestamp: p.timestamp, count: p.count || 0, uniques: p.uniques || 0 };
    });
    (fresh || []).forEach(function (p) {
      map[dateKey(p.timestamp)] = { timestamp: p.timestamp, count: p.count || 0, uniques: p.uniques || 0 };
    });
    return Object.keys(map).sort().map(function (k) { return map[k]; });
  }
  function setSnapshot(bucket, repo, items) {
    if (!bucket.repos) bucket.repos = {};
    if (!bucket.repos[repo]) bucket.repos[repo] = { snapshots: [] };
    bucket.repos[repo].snapshots = [{ captured_at: new Date().toISOString(), items: items }];
  }

  function listRepos(user, token) {
    var out = [];
    var path = "/users/" + encodeURIComponent(user) + "/repos?type=owner&per_page=100&sort=pushed";
    function page(p) {
      return ghCall(p, token).then(function (res) {
        (res.json || []).forEach(function (r) {
          if (r && r.owner && r.name) out.push(r.owner.login + "/" + r.name);
        });
        var nx = nextLink(res.link);
        return nx ? page(nx) : out;
      });
    }
    return page(path);
  }

  function refreshLive(user, token) {
    user = normalizeUser(user);
    if (!user) { setLiveStatus(i18n('err_user_empty'), "warn"); return; }
    if (!token) { setLiveStatus(i18n('err_pat_empty'), "warn"); return; }
    setLiveStatus(i18n('status_listing', { u: user }), "loading");
    listRepos(user, token).then(function (repos) {
      if (!repos.length) { setLiveStatus(i18n('status_norepo', { u: user }), "warn"); return; }
      setLiveStatus(i18n('status_fetching', { n: repos.length }), "loading");
      var failed = [];
      var tasks = repos.map(function (repo) {
        var parts = repo.split("/");
        var base = "/repos/" + encodeURIComponent(parts[0]) + "/" + encodeURIComponent(parts[1]) + "/traffic/";
        // 注意：每个 traffic 接口的错误都要带上，不能静默吞掉（否则 403 无权限会被误报成「刷新成功」）
        return Promise.all([
          ghCall(base + "views", token).then(function (r) { return ["views", r.json.views, null]; }).catch(function (e) { return ["views", null, e]; }),
          ghCall(base + "clones", token).then(function (r) { return ["clones", r.json.clones, null]; }).catch(function (e) { return ["clones", null, e]; }),
          ghCall(base + "popular/referrers", token).then(function (r) { return ["referrers", r.json, null]; }).catch(function (e) { return ["referrers", null, e]; }),
          ghCall(base + "popular/paths", token).then(function (r) { return ["paths", r.json, null]; }).catch(function (e) { return ["paths", null, e]; })
        ]).then(function (pairs) {
          if (!DATA.repos) DATA.repos = {};
          var entry = DATA.repos[repo] = DATA.repos[repo] || { views: [], clones: [] };
          var errs = [];
          pairs.forEach(function (pr) {
            var kind = pr[0], val = pr[1], err = pr[2];
            if (err) { errs.push(kind + ": " + friendlyErr(err)); return; }
            if (!val) return;
            if (kind === "views" || kind === "clones") {
              entry[kind] = mergeDaily(entry[kind], val);
            } else if (kind === "referrers") {
              setSnapshot(DATA.referrers, repo, val.map(function (it) {
                return { referrer: it.referrer, count: it.count || 0, uniques: it.uniques || 0 };
              }));
            } else if (kind === "paths") {
              setSnapshot(DATA.paths, repo, val.map(function (it) {
                return { path: it.path, title: it.title, count: it.count || 0, uniques: it.uniques || 0 };
              }));
            }
          });
          // 四个接口全部失败（最常见：PAT 缺 traffic 权限 → 403）才判为整仓失败
          if (errs.length === pairs.length) failed.push(repo + "（" + errs[0] + (errs.length > 1 ? " 等" : "") + "）");
        }).catch(function (err) {
          failed.push(repo + "（" + friendlyErr(err) + "）");
        });
      });
      return Promise.all(tasks).then(function () {
        ALL = Object.keys(DATA.repos);
        state.selected = ALL.slice();
        DATA.last_updated = new Date().toISOString();
        // 只有至少一个仓库真正拿到数据，才标记为「实时」；否则徽标保持内联/错误态
        liveOK = (repos.length > 0 && failed.length < repos.length);
        renderChips(); renderMeta(); renderAll();
        if (failed.length) setLiveStatus(i18n('status_refreshed_fail', { ok: (repos.length - failed.length), total: repos.length, list: failed.join("、") }), "warn");
        else renderSource();
      });
    }).catch(function (err) {
      setLiveStatus(i18n('status_list_fail', { msg: friendlyErr(err) }), "warn");
    });
  }

  function applyRefresh() {
    var u = ($("userInput") && $("userInput").value) || "";
    var t = ($("tokenInput") && $("tokenInput").value) || "";
    if (!u.trim()) { setLiveStatus(i18n('err_user_empty'), "warn"); return; }
    if (!t.trim()) { setLiveStatus(i18n('err_pat_empty'), "warn"); return; }
    setUsername(u.trim());
    setToken(t.trim());
    refreshLive(u, t);
  }

  // 实时刷新：纯刷新，直接读 localStorage 里已存的 username + PAT
  if ($("liveBtn")) $("liveBtn").addEventListener("click", function () {
    var u = getUsername(), t = getToken();
    if (!u || !t) {
      setLiveStatus(i18n('err_no_creds'), "warn");
      var panel = $("livePanel");
      if (panel) panel.classList.add("open");
      var ui = $("userInput"); if (ui) ui.focus();
      return;
    }
    refreshLive(u, t);
  });

  // 齿轮：呼出 GitHub 用户名 / PAT 设置面板（不再由实时刷新按钮承担）
  if ($("gearBtn")) $("gearBtn").addEventListener("click", function () {
    var panel = $("livePanel");
    if (!panel) return;
    panel.classList.toggle("open");
    if (panel.classList.contains("open")) {
      var ui = $("userInput"); if (ui && !ui.value) ui.value = getUsername();
      var ti = $("tokenInput"); if (ti && !ti.value) ti.value = getToken();
      if (ui) ui.focus();
    }
  });
  if ($("tokenSave")) $("tokenSave").addEventListener("click", applyRefresh);
  if ($("tokenClear")) $("tokenClear").addEventListener("click", function () {
    setUsername(""); setToken("");
    var ui = $("userInput"); if (ui) ui.value = "";
    var ti = $("tokenInput"); if (ti) ti.value = "";
    liveOK = false; renderSource();
  });

  // 打开时若已保存用户名 + token，自动列出并抓取；内联数据始终作为即时兜底
  var su = getUsername(), st = getToken();
  if (su && st) refreshLive(su, st);
  renderSource();
})();
</script>
</body>
</html>
"""


def build_payload(data_dir: Path) -> Dict[str, Any]:
    history, _ = load_json(data_dir / "traffic-history.json", {"schema_version": 1, "last_updated": None, "repos": {}})
    referrers, _ = load_json(data_dir / "referrers-history.json", {"repos": {}})
    paths, _ = load_json(data_dir / "paths-history.json", {"repos": {}})

    if not isinstance(history, dict) or not isinstance(history.get("repos"), dict):
        history = {"schema_version": 1, "last_updated": None, "repos": {}}

    return {
        "schema_version": history.get("schema_version", 1),
        "last_updated": history.get("last_updated"),
        "repos": history.get("repos", {}),
        "referrers": referrers if isinstance(referrers, dict) else {"repos": {}},
        "paths": paths if isinstance(paths, dict) else {"repos": {}},
    }


def render(data_dir: Path, out_path: Path, empty: bool = False) -> Dict[str, Any]:
    payload = build_payload(data_dir)
    # 开源发布用：--empty 不嵌入任何真实流量数据，生成的模板打开即空白，
    # 用户输入用户名 + PAT 点刷新后才拉取实时数据（同时避免泄露个人仓库流量）。
    if empty:
        payload = {
            "schema_version": payload.get("schema_version", 1),
            "last_updated": None,
            "repos": {},
            "referrers": {"repos": {}},
            "paths": {"repos": {}},
        }
    # 内联 JSON 时闭合标签需要转义，否则会提前结束 <script>
    inline = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("</", "<\\/")
        .replace("<!--", "<\\!--")
    )
    html = TEMPLATE.replace("__TRAFFIC_DATA__", inline)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8", newline="\n")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成自包含的单文件 Traffic 看板 HTML。")
    parser.add_argument("--data-dir", default=str(common.REPO_ROOT / "data"), help="数据目录")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 HTML 路径")
    parser.add_argument("--empty", action="store_true", dest="empty",
                        help="生成不包含任何内联流量数据的空白模板（开源发布用），需用户填 PAT 后刷新才出数据")
    parser.add_argument("--open", action="store_true", dest="open_it", help="生成后用默认浏览器打开")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    out_path = Path(args.out)

    # --empty 是开源/模板构建，本来就不需要本地数据文件，不告警以免误导使用者
    if not args.empty and not (data_dir / "traffic-history.json").exists():
        warn(f"未找到 {data_dir / 'traffic-history.json'}，将生成空看板。")

    payload = render(data_dir, out_path, empty=args.empty)
    repos = list(payload["repos"].keys())
    size_kb = out_path.stat().st_size / 1024
    info(f"已生成 {out_path}（{size_kb:.1f} KB）")
    info(f"包含 {len(repos)} 个仓库" + (f"：{', '.join(repos[:5])}" + (" ..." if len(repos) > 5 else "") if repos else "（无数据）"))
    if payload["last_updated"]:
        info(f"数据时间：{payload['last_updated']}")

    if args.open_it:
        try:
            webbrowser.open(out_path.resolve().as_uri())
        except Exception:  # pragma: no cover
            subprocess.run(["start", "", str(out_path)], shell=True, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
