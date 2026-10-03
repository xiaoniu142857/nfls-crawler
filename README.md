# nflsoj — NFLSOJ 命令行工具

> 爬取 [NFLSOJ](https://nflsoi.cc)（`nflsoi.cc`）的比赛列表与每场比赛的题目名称列表，在本地存档、
> 标记做题进度，并以美观的终端界面统计做题情况。

`nflsoj.py` 是一个**单文件、零第三方依赖**的 Python CLI 工具。它直接调用 NFLSOJ 的 JSON REST API
（`<base>/api/...`），把数据缓存到本地，让你无需打开浏览器即可浏览比赛、题单并管理自己的做题状态。

- 版本：`1.0.1`
- 许可证：[GPL-3.0](LICENSE)
- 依赖：仅 Python 标准库（**Python 3.8+**），无需 `pip install`

---

## 功能特性

- **多种登录方式**：浏览器 Cookie / `session-swr`、Bearer Token、用户名 + 密码；`token` 可从 Cookies、
  `localStorage` 的 JSON、`Authorization` 头等任意文本中自动提取。
- **爬取全部比赛**：分页拉取站点全部比赛（含未开始 / 进行中 / 已结束），并并发抓取每场比赛的题目名称列表。
- **做题状态管理**：把任意一题标记为 `未完成 / 正在做 / 已完成`，可循环切换或一键清除。
- **进度统计**：整体完成度进度条、按比赛维度统计、最近标记记录。
- **数据导出**：支持 Markdown / CSV / JSON 三种格式导出做题记录。
- **终端体验**：自动适配彩色输出、宽字符（中/日/韩）对齐、表格式渲染，并可在老终端退化为纯 ASCII。
- **健壮性**：自动重试 5xx 请求、超时/证书/网络错误的友好提示、数据原子写入本地。

---

## 环境要求

- **Python 3.8 或更高版本**（已在 Python 3.14 上验证运行）。
- 支持 Windows / macOS / Linux。Windows 下会自动开启控制台 ANSI 彩色支持。
- 无需安装任何第三方库，见 [`requirements.txt`](requirements.txt)。

---

## 安装

克隆仓库后即可直接使用，脚本自带可执行头（`#!/usr/bin/env python3`）：

```bash
git clone <本仓库地址> nfls-crawler
cd nfls-crawler

# 方式一：直接用 Python 运行
python nflsoj.py --help

# 方式二：赋予可执行权限后直接运行（Linux / macOS）
chmod +x nflsoj.py
./nflsoj.py --help
```

可选：为方便日常使用，可创建命令别名或软链接：

```bash
# Linux / macOS
alias nflsoj='python /path/to/nfls-crawler/nflsoj.py'

# Windows PowerShell
Set-Alias nflsoj "python C:\path\to\nfls-crawler\nflsoj.py"
```

> 下文所有示例均以 `nflsoj` 代指 `python nflsoj.py`。

---

## 快速开始

```bash
nflsoj login                 # 1. 登录（交互式，推荐粘贴浏览器 Cookie / Token）
nflsoj sync                  # 2. 爬取全部比赛 + 每场比赛的题目列表（写入本地缓存）
nflsoj contests              # 3. 查看比赛列表与完成进度
nflsoj problems 12           # 4. 查看 12 号比赛的题目名称列表
nflsoj mark 12 3 done        # 5. 把 12 号比赛第 3 题标记为“已完成”
nflsoj stats                 # 6. 查看做题情况统计
```

不带任何子命令直接运行（`nflsoj`）会显示一个**概览面板**：登录状态、总完成度进度条、
以及常用命令提示。

---

## 登录

支持三种方式。登录成功后，凭据保存在本地配置文件（见 [数据存储](#数据存储)）。

**1) 交互式（推荐）** —— 直接运行 `nflsoj login`，会提示你选择登录方式：

```
nflsoj login
```

- 选择 1：粘贴 **Token / Cookie / `session-swr`**。脚本会自动从任意文本中提取 token
  （支持裸 token、`session-swr` 的 JSON、完整 Cookie 串、`token=xxx` 键值对、`Bearer ...`）。
- 选择 2：输入**用户名 + 密码**（密码输入时不回显）。

> 获取 Token/Cookie 的方法：网页登录后按 `F12` → `Application` → `Local Storage` →
> 复制 `session-swr` 的值；或从 `Network` 面板复制请求头中的 `Cookie` / `Authorization`。

**2) 命令行参数**

```bash
nflsoj login --token "eyJhbGciOi..."
nflsoj login --cookie "session-swr=..."
nflsoj login -u 用户名 -p 密码
```

**3) 环境变量**

```bash
# 通过环境变量提供 token（等价于 --token）
export NFLSOJ_TOKEN="eyJhbGciOi..."     # Windows: set / $env:
nflsoj login
```

登录成功后可用以下命令查看 / 清除登录状态：

```bash
nflsoj whoami     # 显示当前登录用户、站点信息、服务端版本
nflsoj logout     # 清除本地登录凭据（做题标记仍保留）
```

---

## 全局选项

以下选项适用于所有子命令，也适用于不带子命令的概览面板（如 `nflsoj --api ... `）：

| 选项 | 说明 |
|---|---|
| `--data-dir PATH` | 数据目录（默认 `~/.nflsoj`） |
| `--api URL` | 站点 API 地址（默认 `https://nflsoi.cc:20035`） |
| `--timeout N` | HTTP 超时秒数（默认 `30`） |
| `--insecure` | 跳过 HTTPS 证书校验 |
| `--debug` | 打印 HTTP 请求 / 响应细节 |
| `--no-color` | 禁用彩色输出 |
| `--ascii` | 使用纯 ASCII 字符（兼容老终端） |
| `--version` | 显示版本号（顶层选项） |
| `-h, --help` | 显示帮助 |

> 环境变量：设置 `NO_COLOR` 可全局禁用彩色；输出重定向到非 TTY 时也会自动禁用彩色。
>
> 全局选项既可写在子命令之前（`nflsoj --api URL sync`），也可写在子命令之后（`nflsoj sync --api URL`）。
> `--api` 只覆盖本次运行使用的地址，只有 `login` / `config --set-api` 会把地址写入配置文件。

---

## 命令参考

命令总览（括号内为别名）：

| 命令 | 别名 | 作用 |
|---|---|---|
| `login` | | 登录（Cookie / Token / 用户名密码） |
| `logout` | | 清除本地登录凭据 |
| `whoami` | | 显示当前登录状态 |
| `config` | | 查看 / 修改本地配置 |
| `sync` | | 爬取全部比赛与题目列表 |
| `contests` | `ls` | 列出比赛（含完成进度） |
| `problems` | `ps` | 列出某场比赛的题目 |
| `mark` | `m` | 标记题目状态 |
| `stats` | `st` | 做题情况统计 |
| `open` | `o` | 在浏览器中打开比赛 / 题目 |
| `export` | | 导出做题记录（md / csv / json） |

### `sync` — 爬取比赛与题目

```bash
nflsoj sync                         # 爬取全部比赛 + 每场比赛题目
nflsoj sync --keyword 月考          # 只同步名称含“月考”的比赛
nflsoj sync --contest 12 34         # 只同步指定 ID 的比赛
nflsoj sync --workers 8             # 并发线程数（默认 6）
nflsoj sync --no-problems           # 只同步比赛列表，不拉题目
nflsoj sync --force                 # 强制重新拉取已有比赛的题目
```

| 选项 | 说明 |
|---|---|
| `--keyword TEXT` | 只同步名称包含该关键词的比赛 |
| `--contest ID [ID ...]` | 只同步指定 ID 的比赛 |
| `--workers N` | 并发线程数（默认 `6`） |
| `--no-problems` | 只同步比赛列表，不拉取题目 |
| `--force` | 强制重新拉取已有比赛的题目 |

> 默认情况下，已同步过题目的比赛会被跳过（增量同步）；加 `--force` 强制刷新。
> 对 `getContestList` 分页拉取全部比赛，再并发请求每场比赛详情以获取题目。

### `contests` / `ls` — 比赛列表

```bash
nflsoj contests                     # 读取本地缓存
nflsoj contests --live              # 直接请求服务器（刷新并写回缓存）
nflsoj contests --keyword 寒假      # 按名称过滤
nflsoj contests --phase running     # 按状态过滤：not_started / running / ended
nflsoj contests --ids 12 34         # 只显示指定 ID
nflsoj contests --limit 100         # 最多显示多少场（默认 50）
```

| 选项 | 说明 |
|---|---|
| `--live` | 直接请求服务器，不读缓存 |
| `--keyword TEXT` | 按名称过滤 |
| `--phase {...}` | 按状态过滤：`not_started` / `running` / `ended` |
| `--ids ID [ID ...]` | 只显示指定 ID |
| `--limit N` | 最多显示多少场（默认 `50`） |

表格列：状态 / ID / 名称 / 模式 / 开始时间 / 结束时间 / 已完成进度。若本地无数据会自动同步一次。

### `problems` / `ps` — 题目列表

```bash
nflsoj problems 12                  # 12 号比赛（也支持名称关键词）
nflsoj problems "月考"              # 按名称（不区分大小写子串）匹配
nflsoj problems 12 --refresh        # 强制从服务器刷新题目列表
nflsoj problems 12 --state todo     # 只看未完成（todo / doing / done）
nflsoj problems 12 --tags           # 显示题目标签
nflsoj problems 12 --json           # 以 JSON 输出
nflsoj problems 12 --open           # 在浏览器中打开该比赛
```

| 选项 | 说明 |
|---|---|
| `contest` (位置参数) | 比赛 ID 或名称关键词 |
| `--refresh` | 强制从服务器刷新题目列表 |
| `--state todo/doing/done` | 按状态过滤 |
| `--tags` | 显示题目标签 |
| `--json` | 以 JSON 输出 |
| `--open` | 在浏览器中打开该比赛 |

### `mark` / `m` — 标记状态

```bash
nflsoj mark 12 3 done               # 12 号比赛第 3 题 → 已完成
nflsoj mark 12 3 doing              # 正在做
nflsoj mark 12 3 todo               # 未完成
nflsoj mark 12 3                    # 不带状态：未完成 → 正在做 → 已完成 循环
nflsoj mark 12 3 --clear            # 清除标记
nflsoj mark 12 3 done --refresh     # 先刷新题目列表再标记
```

| 参数 / 选项 | 说明 |
|---|---|
| `contest` (位置参数) | 比赛 ID 或名称关键词 |
| `problem` (位置参数) | 题号（列表中的序号 `displayOrder`）或题目 ID |
| `state` (可选位置参数) | `todo` / `doing` / `done`（省略则在三种状态间循环） |
| `--clear` | 清除标记 |
| `--refresh` | 先刷新题目列表 |

**状态别名**（大小写不敏感）：

| 状态 | 可用别名 |
|---|---|
| 未完成 `todo` | `todo` `0` `未完成` `未做` `没做` `new` |
| 正在做 `doing` | `doing` `1` `正在做` `在做` `进行中` `inprogress` `wip` |
| 已完成 `done` | `done` `2` `已完成` `完成` `已做` `ac` `ok` |
| 清除 | `none` `clear` `reset` `取消` `rm` |

### `stats` / `st` — 做题统计

```bash
nflsoj stats                        # 整体统计
nflsoj stats --contest 12           # 只统计某一场（支持多个）
nflsoj stats --keyword 月考         # 按比赛名称过滤
nflsoj stats --phase ended          # 按状态过滤
nflsoj stats --limit 30             # 表格最多显示多少行（默认 20）
nflsoj stats --recent 15            # 最近标记显示条数（默认 8）
nflsoj stats --no-recent            # 不显示最近标记
nflsoj stats --json                 # 以 JSON 输出
```

| 选项 | 说明 |
|---|---|
| `--contest SPEC [SPEC ...]` | 只统计指定比赛（ID 或名称） |
| `--keyword TEXT` | 按比赛名称过滤 |
| `--phase {...}` | 按状态过滤：`not_started` / `running` / `ended` |
| `--limit N` | 表格最多显示行数（默认 `20`） |
| `--recent N` | 最近标记条数（默认 `8`） |
| `--no-recent` | 不显示最近标记 |
| `--json` | 以 JSON 输出 |

输出包含：整体完成度进度条与百分比、`已完成 / 正在做 / 未完成` 分项计数、
按比赛统计表，以及最近标记记录。

### `open` / `o` — 浏览器打开

```bash
nflsoj open 12                      # 打开 12 号比赛页面
nflsoj open 12 3                    # 打开 12 号比赛第 3 题
nflsoj open 12 --print-only         # 只打印链接，不打开浏览器
```

链接格式为 `<base>/c/<比赛ID>` 或 `<base>/c/<比赛ID>/p/<题号>`。

### `export` — 导出做题记录

```bash
nflsoj export                       # Markdown（默认，打印到屏幕）
nflsoj export --format csv -o out.csv
nflsoj export --format json -o out.json
nflsoj export --contest 12 34 -o partial.md
```

| 选项 | 说明 |
|---|---|
| `--format {md,csv,json}` | 导出格式（默认 `md`） |
| `-o, --output FILE` | 输出文件（默认打印到屏幕；`-` 也代表屏幕） |
| `--contest SPEC [SPEC ...]` | 只导出指定比赛 |

### `config` — 本地配置

```bash
nflsoj config                       # 查看当前配置（地址、配置文件路径、token 摘要等）
nflsoj config --set-api https://nflsoi.cc:20035   # 修改站点 API 地址
nflsoj config --clear               # 清除登录凭据
```

---

## 数据存储

所有数据默认存放在 `~/.nflsoj/`（Windows 为 `C:\Users\<你>\.nflsoj\`），可通过 `--data-dir` 更改。
均为 JSON 文件，采用“临时文件 + 原子替换”写入；在类 Unix 系统上还会把配置文件权限设为 `0600`。

| 文件 | 内容 |
|---|---|
| `config.json` | 站点地址、Token、Cookie、记录账号等登录凭据 |
| `progress.json` | 按用户保存的比赛缓存与每道题的做题标记（`state`） |

`progress.json` 结构示意：

```jsonc
{
  "version": 1,
  "users": {
    "<用户名>": {
      "contests": {
        "<比赛ID>": {
          "id": 12,
          "name": "比赛名称",
          "mode": "CUSTOM",
          "startTime": "...",
          "endTime": "...",
          "problems": [
            {
              "problemId": 345,
              "displayOrder": 1,
              "title": "题目名称",
              "tags": ["..."],
              "submitted": false,
              "state": "done",          // 本地标记：todo / doing / done
              "stateUpdatedAt": 1700000000
            }
          ],
          "syncedAt": 1700000000
        }
      }
    }
  }
}
```

> `state` 是你**本地手动标记**的状态；`submitted` 是服务器返回的“是否已有提交记录”，
> 两者独立，统计中的“服务端已提交”仅供参考。

---

## 站点 API 说明

工具通过分析站点前端 bundle 得到以下接口（数据全部来自 JSON REST API，鉴权使用
`Authorization: Bearer <token>`，网页端把 token 存在 `localStorage` 的 `session-swr` 中）：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/auth/getSessionInfo` | 返回 `{userMeta, isManager, serverPreference, serverVersion, ...}` |
| `POST` | `/api/auth/login` | 请求体 `{username, password}`，返回 `{token, username}` 或 `{error}` |
| `GET` | `/api/contest/getContestList` | 参数 `skipCount, takeCount, keyword, returnCount`，返回 `{contests, count, managers}` |
| `GET` | `/api/contest/getContest` | 参数 `contestId, problemInfos, description, announcements`，返回 `{meta, problemInfos, permissions, ...}` |

- 默认 API 地址：`https://nflsoi.cc:20035`（可用 `--api` 或 `config --set-api` 修改）。
- 比赛列表分页大小为 `20`（与站点 `serverPreference.pagination.contestList` 保持一致）。
- 对 `500/502/503/504` 会自动重试（默认重试 `2` 次，指数退避）。
- 常见错误码会给出中文提示，例如 `401` 提示重新登录、`403 not activated` 提示账号未激活。

> ⚠️ 站点 API 若升级，字段/接口可能变化。工具做了多字段名兼容（`normalize_contest` /
> `extract_problems`），但仍以实际站点为准。

---

## 常见问题（FAQ）

**Q：提示“尚未登录或登录状态已失效”？**
Token 可能已过期。重新执行 `nflsoj login`，粘贴最新的 `session-swr` / Cookie。

**Q：`problems` / `mark` 提示“找不到比赛”？**
先运行 `nflsoj sync`（或 `nflsoj contests --live`）把比赛同步到本地；比赛名支持不区分大小写的
子串匹配，若匹配到多场请改用比赛 ID。

**Q：终端中文/表格显示乱码或错位？**
加 `--ascii` 使用纯 ASCII 边框；或设置 `--no-color` 关闭颜色。Windows 老终端（非 Windows Terminal）
建议使用 Windows Terminal，脚本已自动尝试开启 ANSI 支持。

**Q：HTTPS 证书报错？**
如果处在代理 / 自签名证书环境，可临时使用 `--insecure` 跳过证书校验（注意安全风险）。

**Q：统计数据与网站不一致？**
统计完全基于**本地标记**（`state`）与本地缓存的题目列表，请先 `sync` 保证题目数据最新。

**Q：想抓取请求细节排查问题？**
加 `--debug` 打印每次 HTTP 请求与响应摘要。

---

## 项目结构

```
nfls-crawler/
├── nflsoj.py         # 单文件 CLI 工具（全部逻辑）
├── requirements.txt  # 依赖清单（本项目仅用标准库，无需安装）
├── README.md
├── LICENSE           # GPL-3.0
└── .gitignore
```

---

## 贡献

欢迎提交 Issue / Pull Request。请保持“零第三方依赖、仅用标准库”的原则，
并尽量与现有代码风格（中文注释、`Console` 渲染、`ApiError` 错误处理）保持一致。

---

## 许可

本项目基于 [GNU General Public License v3.0](LICENSE) 发布。

> 本工具为个人学习与效率工具，请合理控制请求频率、遵守 NFLSOJ 站点的使用条款，
> 因使用本工具产生的任何后果由使用者自行承担。
