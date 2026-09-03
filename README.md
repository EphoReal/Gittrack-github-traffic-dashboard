<!-- SPDX-License-Identifier: GPL-3.0-only -->
<!-- Copyright (c) 2026 fthuu -->

# GitHub Traffic 历史看板

GitHub 网页上的 Traffic 只保留最近 **14 天**。本工具帮你把这段数据**永久累积**，生成一个可双击打开的**单文件离线 HTML 看板**。

零依赖（不引 CDN、不需要 Chart.js）、零第三方账号，数据内联进 HTML，`file://` 双击即开。

## 📸 预览

| | |
|:---:|:---:|
| ![主看板](screenshots/pic1.png) | ![流量来源与热门路径 Top 10](screenshots/pic2.png) |
| 主看板：状态徽标、仓库芯片、汇总卡片、Views/Clones 双折线 | 流量来源 Top 10 + 热门路径 Top 10 |
| ![每日明细表](screenshots/pic3.png) | ![实时刷新面板](screenshots/how_to_use.png) |
| 每日明细表（支持 14 天 / 30 天 / 全部） | 实时刷新面板：填用户名 + PAT，存本浏览器 localStorage |

## ✨ 特性

- **⚡ 实时刷新**：填用户名 + PAT，点刷新就地拉取最新 14 天，PAT 只存本浏览器。
- **📈 自动每日采集**：用 GitHub Actions 每天拉一次，历史在私有仓库永久累积。
- **📊 单文件离线**：`traffic-dashboard.html` 数据内联、手写 SVG，双击直开。
- **🔍 多仓库自动发现**：默认列出你名下所有仓库，也可显式指定。
- **🖱️ 双视图切换 / 🌐 中英文 / 📅 跨年不混淆** 等。

## 🚀 使用方式

两种取数方式，**叠加使用，不是二选一**：

| | ① 实时刷新 | ② Actions 自动采集 |
| --- | --- | --- |
| 谁来做 | 浏览器调 GitHub API | GitHub Actions 每天定时跑 |
| 能留存吗 | ❌ 关页即失 | ✅ 永久累积到私有仓库 |
| 需要准备 | 一个 PAT | 一个私有仓库 + 一个 PAT |

> 数据源都是 GitHub Traffic API，它**只返回最近 14 天**。实时刷新看完就扔；
> Actions 把每天的窗口沉淀到文件，是唯一能让历史不断档的方式。

### ① 实时刷新（1 分钟）

1. 打开 `traffic-dashboard.html`（首次空白，属正常）。
2. 点齿轮 ⚙，填用户名与 PAT（Classic 勾 `repo`；Fine-grained 给 `Administration: Read-only`）。

   ![实时刷新面板](screenshots/how_to_use.png)

3. 点「实时刷新」→ 列出仓库 → 拉取 14 天 → 渲染。

> ⚠️ `file://` 下部分浏览器不持久化 localStorage，刷新按钮灰了就用 `python -m http.server` 起服务再开。

### ② Actions 自动采集（推荐）

1. fork / clone 本仓库。
2. 新建**私有**仓库（数据仓库），把 `.github/workflows/collect-traffic.yml.example` 复制进去并改名为 `collect-traffic.yml`，把 `TOOL_REPO` 改成你的工具仓库。
3. 放一份 `config/repos.json`（复制模板再改）。
4. 在数据仓库 Settings → Secrets 新建 `TRAFFIC_TOKEN`（具备目标仓库写权限的 PAT）。
5. 手动跑一次 Actions → Collect GitHub Traffic 验证。

之后每天 UTC 02:00（北京时间 10:00）自动执行。**历史从你第一次采集那天开始**，无固定起点。

### 两者怎么配合

徽标三态：灰（没配凭据）/ 蓝（内联种子，Actions 沉淀的快照）/ 绿（刚实时刷新过）。
打开看板看到的是 Actions 累积的历史；点刷新则把最新 14 天合并进来（不重复计算）。
注意：实时刷新的数据只在内存，**不会写回文件**，想永久留存只能靠 Actions。

## 🌍 中英文切换

点右上角 `EN` / `中文` 切换，偏好存 `localStorage`。

## 🔒 隐私

工具仓库**不含任何真实数据**，可放心公开：`--empty` 生成的 `traffic-dashboard.html` 打开即空白；
`.gitignore` 已拦截 `data/traffic-history.json`、`*.csv`；PAT 只存 GitHub Secrets 或浏览器 localStorage。

**铁律**：公开仓库生成看板**必须带 `--empty`**；累积历史**只放私有仓库**。

## 📜 License

```
SPDX-License-Identifier: GPL-3.0-only
Copyright (c) 2026 fthuu
```

基于 GNU GPL v3.0 only 开源 — 见 [LICENSE](LICENSE)。

## ✍️ 作者

- Xiaohongshu / Rednote：@Epho
- GitHub：https://github.com/fthuu
