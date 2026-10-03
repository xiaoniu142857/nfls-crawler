#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NFLSOJ (nflsoi.cc) 命令行工具。

功能：
  * 使用浏览器 Cookie / Token / 用户名密码登录
  * 爬取全部比赛以及每场比赛的题目名称列表
  * 把任意一题标记为 未完成 / 正在做 / 已完成
  * 以美观的终端界面统计做题情况

依赖：仅使用 Python 标准库（Python 3.8+）。

站点后端说明（通过分析前端 bundle 得到）：
  * 站点是 SPA，数据全部来自 JSON REST API：<base>/api/...
  * 鉴权方式为 `Authorization: Bearer <token>`（token 由 /api/auth/login 返回，
    网页端把它存在 localStorage 的 "session-swr" 里）。
  * GET  /api/auth/getSessionInfo                  -> {userMeta, isManager, ...}
  * POST /api/auth/login  {username,password}      -> {token, username} | {error}
  * GET  /api/contest/getContestList?skipCount&takeCount&keyword&returnCount
                                                  -> {contests:[...], count, managers}
  * GET  /api/contest/getContest?contestId&problemInfos&description&announcements
                                                  -> {meta:{name,...},
                                                      problemInfos:[{meta:{id,title},
                                                                     displayOrder,tags,...}],
                                                      permissions, ...}
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

__version__ = "1.0.0"

DEFAULT_API = "https://nflsoi.cc:20035"
DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".nflsoj")
CONTEST_PAGE_SIZE = 20          # 与站点 serverPreference.pagination.contestList 保持一致

# ---------------------------------------------------------------------------
# 状态定义
# ---------------------------------------------------------------------------
STATE_TODO, STATE_DOING, STATE_DONE = "todo", "doing", "done"
STATE_ORDER = [STATE_TODO, STATE_DOING, STATE_DONE]
STATE_LABEL = {STATE_TODO: "未完成", STATE_DOING: "正在做", STATE_DONE: "已完成"}
STATE_COLOR = {STATE_TODO: "grey", STATE_DOING: "yellow", STATE_DONE: "green"}

# 用户输入 -> 内部状态（None 表示清除标记）
STATE_ALIASES = {
    "todo": STATE_TODO, "0": STATE_TODO, "未完成": STATE_TODO, "未做": STATE_TODO,
    "没做": STATE_TODO, "new": STATE_TODO,
    "none": None, "clear": None, "reset": None, "取消": None, "rm": None,
    "doing": STATE_DOING, "1": STATE_DOING, "正在做": STATE_DOING, "在做": STATE_DOING,
    "进行中": STATE_DOING, "inprogress": STATE_DOING, "wip": STATE_DOING,
    "done": STATE_DONE, "2": STATE_DONE, "已完成": STATE_DONE, "完成": STATE_DONE,
    "已做": STATE_DONE, "ac": STATE_DONE, "ok": STATE_DONE,
}


# ---------------------------------------------------------------------------
# 终端渲染
# ---------------------------------------------------------------------------
def enable_windows_ansi() -> None:
    """在 Windows 控制台启用 ANSI 转义序列。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VT_PROCESSING
    except Exception:
        pass


class Console:
    """极简但足够漂亮的终端输出层（表格 / 进度条 / 彩色文字）。"""

    CODES = {
        "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "underline": "\033[4m",
        "black": "\033[30m", "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
        "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m", "white": "\033[37m",
        "grey": "\033[90m", "bred": "\033[91m", "bgreen": "\033[92m", "byellow": "\033[93m",
        "bblue": "\033[94m", "bmagenta": "\033[95m", "bcyan": "\033[96m",
    }

    UNICODE_GLYPHS = {
        "tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
        "lt": "├", "rt": "┤", "tt": "┬", "bt": "┴", "cross": "┼",
        "bar_full": "█", "bar_empty": "░",
        "todo": "○", "doing": "◐", "done": "✔", "dot": "·", "arrow": "→",
    }
    ASCII_GLYPHS = {
        "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
        "lt": "+", "rt": "+", "tt": "+", "bt": "+", "cross": "+",
        "bar_full": "#", "bar_empty": ".", "todo": "[ ]",
        "doing": "[~]", "done": "[x]", "dot": ".", "arrow": "->",
    }

    def __init__(self, color: bool = True, ascii_only: bool = False, stream=None):
        self.stream = stream or sys.stdout
        self.glyph = dict(self.ASCII_GLYPHS if ascii_only else self.UNICODE_GLYPHS)
        self.color = bool(color) and not ascii_only
        try:
            self.isatty = bool(self.stream.isatty())
        except Exception:
            self.isatty = False
        try:
            self.width = max(64, min(os.get_terminal_size().columns, 150))
        except Exception:
            self.width = 100

    # -- 基础 -------------------------------------------------------------
    def paint(self, text, color=None, bold=False):
        if not self.color or not text:
            return text
        prefix = ""
        if bold:
            prefix += self.CODES["bold"]
        if color:
            prefix += self.CODES.get(color, "")
        return prefix + text + self.CODES["reset"] if prefix else text

    def out(self, text: str = "") -> None:
        try:
            print(text, file=self.stream)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode("ascii"), file=self.stream)

    def write(self, text: str) -> None:
        try:
            self.stream.write(text)
        except UnicodeEncodeError:
            self.stream.write(text.encode("ascii", "replace").decode("ascii"))
        try:
            self.stream.flush()
        except Exception:
            pass

    # -- 常用块 -----------------------------------------------------------
    def title(self, text: str) -> None:
        g = self.glyph
        inner = " " + text + " "
        pad = max(0, min(self.width, 90) - len_display(inner) - 2)
        self.out(self.paint(g["tl"] + g["h"] + inner + g["h"] * pad + g["tr"], "bcyan", bold=True))

    def subtitle(self, text: str) -> None:
        self.out(self.paint(text, "bblue", bold=True))

    def muted(self, text: str) -> None:
        self.out(self.paint(text, "grey"))

    def ok(self, text: str) -> None:
        self.out(self.paint("  " + self.glyph["done"] + " " + text, "green", bold=True))

    def info(self, text: str) -> None:
        self.out(self.paint("  " + self.glyph["arrow"] + " " + text, "cyan"))

    def warn(self, text: str) -> None:
        self.out(self.paint("  ! " + text, "yellow", bold=True))

    def error(self, text: str) -> None:
        self.out(self.paint("  x " + text, "bred", bold=True))

    def kv(self, key: str, value: str, key_width: int = 12, value_color=None) -> None:
        self.out("  %s %s" % (pad_display(self.paint(key, "grey"), key_width),
                              self.paint(str(value), value_color)))

    def rule(self, char=None) -> None:
        self.out(self.paint((char or self.glyph["h"]) * min(self.width, 90), "grey"))

    # -- 表格 -------------------------------------------------------------
    def table(self, headers, rows, aligns=None, max_width=None) -> None:
        """headers: list[str]；rows: list[list[tuple(text, color?, bold?)]]。"""
        g = self.glyph
        if not headers:
            return
        cols = len(headers)
        aligns = aligns or ["left"] * cols
        max_width = max_width or self.width
        widths = [len_display(h) for h in headers]
        for row in rows:
            for i in range(cols):
                text = row[i][0] if i < len(row) else ""
                widths[i] = max(widths[i], len_display(text))
        budget = max_width - (3 * (cols - 1)) - 2
        if sum(widths) > budget:
            excess = sum(widths) - budget
            for i in sorted(range(cols), key=lambda k: widths[k], reverse=True):
                if excess <= 0:
                    break
                take = min(excess, max(0, widths[i] - 12))
                widths[i] -= take
                excess -= take

        def render(cells, color_all=None, bold=False):
            parts = []
            for i in range(cols):
                cell = cells[i] if i < len(cells) else ("",)
                text = truncate(cell[0] if cell else "", widths[i])
                padded = pad_display(text, widths[i], aligns[i])
                if len(cell) > 1 and cell[1]:
                    padded = self.paint(padded, cell[1], len(cell) > 2 and bool(cell[2]))
                elif color_all:
                    padded = self.paint(padded, color_all, bold)
                parts.append(padded)
            return g["v"] + " " + (" " + g["v"] + " ").join(parts) + " " + g["v"]

        self.out(self.paint(g["tl"] + g["tt"].join(g["h"] * (w + 2) for w in widths) + g["tr"], "grey"))
        self.out(render([(h,) for h in headers], color_all="bcyan", bold=True))
        self.out(self.paint(g["lt"] + g["cross"].join(g["h"] * (w + 2) for w in widths) + g["rt"], "grey"))
        for row in rows:
            self.out(render(row))
        self.out(self.paint(g["bl"] + g["bt"].join(g["h"] * (w + 2) for w in widths) + g["br"], "grey"))

    # -- 进度条 -----------------------------------------------------------
    def bar(self, done: int, total: int, width: int = 24, color: str = "bgreen") -> str:
        g = self.glyph
        if total <= 0:
            return self.paint(g["bar_empty"] * width, "grey")
        ratio = max(0.0, min(1.0, done / float(total)))
        filled = int(round(ratio * width))
        return (self.paint(g["bar_full"] * filled, color) +
                self.paint(g["bar_empty"] * (width - filled), "grey"))

    def state_glyph(self, state) -> str:
        key = state if state in STATE_ORDER else STATE_TODO
        return self.paint(self.glyph[key], STATE_COLOR.get(key, "grey"), bold=(key == STATE_DONE))


# ---------------------------------------------------------------------------
# 文本宽度工具（正确处理中日韩全角字符）
# ---------------------------------------------------------------------------
def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def len_display(text: str) -> int:
    return sum(char_width(c) for c in text)


def truncate(text: str, width: int) -> str:
    if len_display(text) <= width:
        return text
    out, acc = [], 0
    for ch in text:
        w = char_width(ch)
        if acc + w > width - 1:
            break
        out.append(ch)
        acc += w
    return "".join(out) + "…"


def pad_display(text: str, width: int, align: str = "left") -> str:
    space = max(0, width - len_display(text))
    if align == "right":
        return " " * space + text
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    return text + " " * space


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def parse_time(text):
    """把站点返回的时间解析为 (epoch_seconds, datetime) 或 (None, None)。"""
    import datetime as _dt

    if not text:
        return None, None
    if isinstance(text, (int, float)):
        ts = float(text)
        if ts > 1e12:
            ts /= 1000.0
        return ts, _dt.datetime.fromtimestamp(ts)
    raw = str(text).strip()
    try:
        dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            ts = dt.timestamp()
            return ts, _dt.datetime.fromtimestamp(ts)
        ts = dt.replace(tzinfo=_dt.timezone.utc).timestamp()
        return ts, _dt.datetime.fromtimestamp(ts)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            ts = _dt.datetime.strptime(raw, fmt).replace(tzinfo=_dt.timezone.utc).timestamp()
            return ts, _dt.datetime.fromtimestamp(ts)
        except Exception:
            continue
    return None, None


def fmt_time(text) -> str:
    _, dt = parse_time(text)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else (str(text) if text else "-")


def contest_phase(start, end):
    now = time.time()
    start_ts, _ = parse_time(start)
    end_ts, _ = parse_time(end)
    if start_ts and now < start_ts:
        return "not_started"
    if end_ts and now > end_ts:
        return "ended"
    return "running"


PHASE_LABEL = {"not_started": "未开始", "running": "进行中", "ended": "已结束"}
PHASE_COLOR = {"not_started": "blue", "running": "bgreen", "ended": "grey"}


def human_age(ts) -> str:
    if not ts:
        return "-"
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return "%d 分钟前" % (delta // 60)
    if delta < 86400:
        return "%d 小时前" % (delta // 3600)
    if delta < 86400 * 30:
        return "%d 天前" % (delta // 86400)
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# ---------------------------------------------------------------------------
# API 客户端
# ---------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, message: str, status=None, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.hint = hint


STATUS_HINT = {
    400: "请求参数有误（站点 API 可能已升级，请检查工具版本）",
    401: "登录状态无效或已过期，请重新执行 login 命令",
    403: "没有权限访问该资源（账号可能未激活或被限制）",
    404: "资源不存在",
    429: "请求过于频繁，请稍后再试",
    500: "服务器内部错误",
    502: "网关错误（站点可能在维护）",
    503: "服务暂时不可用",
    504: "网关超时",
}


def report_error(console: Console, err: "ApiError") -> int:
    console.error(err.message)
    if err.hint:
        console.muted("      " + err.hint)
    return 1


def extract_token(raw: str) -> str:
    """从用户粘贴的任意内容中提取 token。

    支持：裸 token、localStorage 中的 session-swr JSON、完整 Cookie 串、
          `token=xxx` 形式的键值对。
    """
    if not raw:
        return ""
    text = raw.strip().strip('"').strip("'").strip()
    if not text:
        return ""

    def from_json(blob):
        try:
            data = json.loads(blob)
        except Exception:
            return ""
        if isinstance(data, dict):
            for key in ("token", "Token", "accessToken", "access_token", "jwt", "value"):
                if isinstance(data.get(key), str) and data[key].strip():
                    return data[key].strip()
            inner = data.get("sessionInfo")
            if isinstance(inner, dict) and isinstance(inner.get("token"), str):
                return inner["token"].strip()
        if isinstance(data, str):
            return data.strip()
        return ""

    if text.startswith("{") and text.endswith("}"):
        token = from_json(text)
        if token:
            return token

    # Cookie 串：k=v; k=v
    if "=" in text and (";" in text or re.match(r"^[A-Za-z0-9_\-\.]+=", text)):
        for part in re.split(r"[;\n]", text):
            piece = part.strip()
            if not piece or "=" not in piece:
                continue
            name, _, value = piece.partition("=")
            name = name.strip()
            value = urllib.parse.unquote(value.strip())
            if value.startswith("{"):
                token = from_json(value)
                if token:
                    return token
            if name.lower() in ("session-swr", "token", "access_token", "authorization", "jwt"):
                value = re.sub(r"^Bearer\s+", "", value, flags=re.I)
                if value:
                    return value

    text = re.sub(r"^Bearer\s+", "", text, flags=re.I)
    return text if re.match(r"^[A-Za-z0-9_\-\.%+/=:~]+$", text) else ""


class Client:
    """NFLSOJ JSON API 客户端。"""

    def __init__(self, base_url: str = DEFAULT_API, token: str = "", cookie: str = "",
                 timeout: int = 30, insecure: bool = False, retries: int = 2, debug: bool = False):
        self.base_url = base_url.rstrip("/")
        self.token = token or ""
        self.cookie = cookie or ""
        self.timeout = timeout
        self.retries = max(0, retries)
        self.debug = debug
        self.ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()

    # -- 底层 -------------------------------------------------------------
    def request(self, method: str, path: str, query=None, body=None):
        url = self.base_url + path
        if query:
            clean = []
            for key, value in query.items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    value = "true" if value else "false"
                clean.append((key, value))
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        headers = {
            "Accept": "application/json",
            "User-Agent": "nflsoj-cli/%s (python-urllib)" % __version__,
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.cookie:
            headers["Cookie"] = self.cookie

        last_error = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                if self.debug:
                    sys.stderr.write("[debug] %s %s\n" % (method, url))
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                    payload = resp.read().decode("utf-8", "replace")
                    if self.debug:
                        sys.stderr.write("[debug] <- %s %s\n" % (resp.status, payload[:300]))
                    if resp.status not in (200, 201):
                        raise ApiError("服务器返回 HTTP %s" % resp.status, resp.status)
                    try:
                        return json.loads(payload) if payload else {}
                    except ValueError:
                        raise ApiError("服务器返回了非 JSON 数据", resp.status, payload[:200])
            except ApiError:
                raise
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")
                except Exception:
                    pass
                if self.debug:
                    sys.stderr.write("[debug] <- HTTP %s %s\n" % (exc.code, detail[:300]))
                message = "HTTP %s" % exc.code
                hint = STATUS_HINT.get(exc.code, "")
                try:
                    parsed = json.loads(detail)
                    if parsed.get("message"):
                        message = parsed["message"]
                    if exc.code == 403 and "not activated" in str(message).lower():
                        message, hint = "账号未激活", "请先到站点激活账号后再使用"
                except Exception:
                    pass
                if exc.code in (500, 502, 503, 504) and attempt < self.retries:
                    last_error = ApiError(message, exc.code, hint)
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise ApiError(message, exc.code, hint)
            except Exception as exc:
                last_error = ApiError("网络错误：%s" % exc, None, "请检查网络连接或代理设置")
                if attempt < self.retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise last_error
        raise last_error or ApiError("未知错误")

    # -- 具体接口 ---------------------------------------------------------
    def session_info(self):
        return self.request("GET", "/api/auth/getSessionInfo")

    def current_user(self):
        """返回已登录用户信息，未登录返回 None。"""
        return self.session_info().get("userMeta") or None

    def login(self, username: str, password: str):
        return self.request("POST", "/api/auth/login",
                            body={"username": username, "password": password})

    def contest_list(self, skip=0, take=CONTEST_PAGE_SIZE, keyword=None, created_by=None):
        return self.request("GET", "/api/contest/getContestList", query={
            "skipCount": int(skip),
            "takeCount": int(take),
            "keyword": keyword or None,
            "returnCount": True,
            "createdBy": created_by or None,
        })

    def contest_detail(self, contest_id, with_problems=True):
        return self.request("GET", "/api/contest/getContest", query={
            "contestId": int(contest_id),
            "description": False,
            "problemInfos": bool(with_problems),
            "announcements": False,
        })


# ---------------------------------------------------------------------------
# 数据规整
# ---------------------------------------------------------------------------
def _first_of(raw: dict, *keys):
    for key in keys:
        if raw.get(key) not in (None, ""):
            return raw[key]
    return None


def normalize_contest(raw: dict) -> dict:
    """把 getContestList / getContest 返回的比赛规整成统一结构（兼容多种字段名）。"""
    groups = []
    for item in raw.get("groups") or []:
        if isinstance(item, dict):
            groups.append(item.get("name") or "")
        elif item:
            groups.append(str(item))
    creator = raw.get("creator") or raw.get("owner") or raw.get("createdBy")
    if isinstance(creator, dict):
        creator = creator.get("username") or creator.get("name")
    pid = raw.get("id")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        pass
    return {
        "id": pid,
        "name": _first_of(raw, "name", "title") or "",
        "mode": _first_of(raw, "mode", "rule") or "CUSTOM",
        "startTime": _first_of(raw, "startTime", "beginAt", "start_time"),
        "endTime": _first_of(raw, "endTime", "endAt", "end_time"),
        "groups": [g for g in groups if g],
        "creator": creator or "",
    }


def extract_problems(detail: dict) -> list:
    """从 getContest(problemInfos=True) 的响应中提取题目列表。"""
    infos = detail.get("problemInfos")
    problems = []
    if isinstance(infos, list):
        for item in infos:
            if not isinstance(item, dict):
                continue
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            pid = meta.get("id", item.get("problemId"))
            title = meta.get("title") or item.get("title") or (("题目 %s" % pid) if pid else "题目")
            try:
                order = int(item.get("displayOrder"))
            except (TypeError, ValueError):
                order = len(problems) + 1
            tags = []
            for tag in item.get("tags") or []:
                if isinstance(tag, dict) and tag.get("name"):
                    tags.append(tag["name"])
                elif isinstance(tag, str):
                    tags.append(tag)
            problems.append({
                "problemId": pid,
                "displayOrder": order,
                "title": title,
                "tags": tags,
                "submitted": bool(item.get("bestSubmission")),
            })
    if not problems:
        # 兼容只返回 {displayOrder: problemId} 映射的其他格式
        raw_map = detail.get("problems")
        if isinstance(raw_map, dict):
            for key, value in raw_map.items():
                try:
                    order = int(key)
                except (TypeError, ValueError):
                    order = len(problems) + 1
                problems.append({"problemId": value, "displayOrder": order,
                                 "title": "题目 %s" % value, "tags": [], "submitted": False})
    problems.sort(key=lambda p: p["displayOrder"])
    return problems


# ---------------------------------------------------------------------------
# 本地存储
# ---------------------------------------------------------------------------
class Store:
    """保存登录凭据与做题标记（全部为 JSON 文件）。"""

    CONFIG_VERSION = 1
    PROGRESS_VERSION = 1

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        self.data_dir = data_dir
        self.config_path = os.path.join(data_dir, "config.json")
        self.progress_path = os.path.join(data_dir, "progress.json")
        self.config = {}
        self.progress = {}

    # -- 文件读写 ---------------------------------------------------------
    def _ensure_dir(self) -> None:
        if not os.path.isdir(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)

    @staticmethod
    def _read_json(path, default):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else default
        except Exception:
            return default

    def _write_json(self, path, payload) -> None:
        self._ensure_dir()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        if os.name != "nt":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def load(self) -> None:
        self.config = self._read_json(self.config_path, {})
        self.progress = self._read_json(self.progress_path, {})

    def save_config(self) -> None:
        self.config["version"] = self.CONFIG_VERSION
        self._write_json(self.config_path, self.config)

    def save_progress(self) -> None:
        self.progress["version"] = self.PROGRESS_VERSION
        self._write_json(self.progress_path, self.progress)

    # -- 凭据 -------------------------------------------------------------
    @property
    def api_base(self) -> str:
        return self.config.get("baseUrl") or DEFAULT_API

    def set_credentials(self, token: str = "", cookie: str = "", username: str = "",
                        base_url: str = "") -> None:
        if base_url:
            self.config["baseUrl"] = base_url
        self.config["token"] = token or ""
        self.config["cookie"] = cookie or ""
        self.config["username"] = username or ""

    def clear_credentials(self) -> None:
        self.config["token"] = ""
        self.config["cookie"] = ""
        self.config["username"] = ""

    def client(self, **overrides) -> Client:
        options = {
            "base_url": self.api_base,
            "token": self.config.get("token") or "",
            "cookie": self.config.get("cookie") or "",
        }
        options.update({k: v for k, v in overrides.items() if v is not None})
        return Client(**options)

    # -- 做题记录 ---------------------------------------------------------
    def user_data(self, username: str) -> dict:
        users = self.progress.setdefault("users", {})
        return users.setdefault(username, {"contests": {}})

    def contest_entry(self, username: str, contest_id):
        return self.user_data(username)["contests"].get(str(contest_id))

    def upsert_contest(self, username: str, contest: dict, problems=None) -> dict:
        """合并比赛信息，并保留已有题目的标记状态。"""
        contests = self.user_data(username)["contests"]
        key = str(contest.get("id"))
        entry = contests.get(key)
        if entry is None:
            entry = {"id": contest.get("id"), "name": ""}
            contests[key] = entry
        for field in ("name", "mode", "startTime", "endTime", "creator", "groups"):
            if contest.get(field) not in (None, "", []):
                entry[field] = contest[field]
        entry.setdefault("problems", [])

        if problems is not None:
            previous = {str(p.get("problemId")): p for p in entry["problems"]}
            merged = []
            for item in problems:
                old = previous.get(str(item.get("problemId")))
                record = dict(item)
                if old and old.get("state"):
                    record["state"] = old["state"]
                    record["stateUpdatedAt"] = old.get("stateUpdatedAt")
                if old and old.get("submitted"):
                    record["submitted"] = True
                merged.append(record)
            entry["problems"] = merged
            entry["syncedAt"] = int(time.time())
        return entry

    def set_state(self, username: str, contest_id, problem_id, state) -> dict:
        entry = self.contest_entry(username, contest_id)
        if not entry:
            raise KeyError("contest %s not found" % contest_id)
        for problem in entry.get("problems", []):
            if str(problem.get("problemId")) == str(problem_id):
                if state is None:
                    problem.pop("state", None)
                    problem.pop("stateUpdatedAt", None)
                else:
                    problem["state"] = state
                    problem["stateUpdatedAt"] = int(time.time())
                return problem
        raise KeyError("problem %s not found" % problem_id)

    def all_contests(self, username: str) -> list:
        contests = self.user_data(username)["contests"]
        return sorted(contests.values(), key=lambda c: (c.get("startTime") or ""), reverse=True)


# ---------------------------------------------------------------------------
# 业务逻辑：拉取比赛 / 同步
# ---------------------------------------------------------------------------
def require_login(store: Store, console: Console) -> str:
    """确认已登录并返回用户名。"""
    if not store.api_base:
        raise ApiError("尚未配置站点地址")
    try:
        user = store.client().current_user()
    except ApiError:
        raise
    if not user:
        raise ApiError("尚未登录或登录状态已失效", 401, "请先运行：nflsoj login")
    username = str(user.get("username") or user.get("id") or "unknown")
    if store.config.get("username") != username:
        store.config["username"] = username
        store.save_config()
    return username


def fetch_all_contests(client: Client, keyword=None, page_size=CONTEST_PAGE_SIZE, on_page=None) -> list:
    """分页拉取全部比赛。"""
    contests, skip, total, pages = [], 0, None, 0
    while True:
        data = client.contest_list(skip=skip, take=page_size, keyword=keyword)
        items = data.get("contests") or []
        if total is None and isinstance(data.get("count"), int):
            total = data["count"]
        contests.extend(normalize_contest(item) for item in items)
        skip += len(items)
        pages += 1
        if on_page:
            on_page(len(contests), total)
        if not items or len(items) < page_size or pages > 500:
            break
        if total is not None and len(contests) >= total:
            break
    return contests


def fetch_contest_detail(client: Client, contest_id) -> tuple:
    """返回 (比赛信息, 题目列表)。"""
    detail = client.contest_detail(contest_id, with_problems=True)
    meta = detail.get("meta") if isinstance(detail.get("meta"), dict) else {}
    contest = normalize_contest(meta)
    contest["id"] = contest.get("id") or contest_id
    if not contest.get("name"):
        contest["name"] = "比赛 %s" % contest_id
    return contest, extract_problems(detail)


def sync_contests(store: Store, username: str, console: Console, client: Client,
                  keyword=None, only_ids=None, workers: int = 6, with_problems: bool = True,
                  force: bool = False) -> dict:
    """拉取比赛列表（以及每场比赛的题目），写入本地缓存。"""
    live = console.isatty

    def on_page(loaded, total):
        if live:
            console.write("\r  %s 正在获取比赛列表… %d/%s   " %
                          (console.glyph["arrow"], loaded, total if total else "?"))

    try:
        contests = fetch_all_contests(client, keyword=keyword, on_page=on_page)
    finally:
        if live:
            console.write("\r" + " " * 60 + "\r")
    if only_ids:
        wanted = {str(i) for i in only_ids}
        contests = [c for c in contests if str(c.get("id")) in wanted]
    console.info("共获取到 %d 场比赛" % len(contests))

    stats = {"contests": 0, "problems": 0, "failed": []}
    targets = []
    for contest in contests:
        entry = store.contest_entry(username, contest.get("id"))
        if with_problems and not force and entry and entry.get("syncedAt"):
            store.upsert_contest(username, contest, None)
            stats["contests"] += 1
            stats["problems"] += len(entry.get("problems") or [])
            continue
        targets.append(contest)

    if with_problems and targets:
        total = len(targets)
        done = 0

        def work(contest):
            return fetch_contest_detail(client, contest.get("id"))

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(work, contest): contest for contest in targets}
            for future in as_completed(futures):
                contest = futures[future]
                done += 1
                if live:
                    console.write("\r  %s 正在同步题目列表… %d/%d   %s   " %
                                  (console.glyph["arrow"], done, total,
                                   truncate(contest.get("name") or "", 22)))
                try:
                    meta, problems = future.result()
                except ApiError as exc:
                    stats["failed"].append((contest.get("id"), contest.get("name"), exc.message))
                    continue
                except Exception as exc:  # noqa: BLE001
                    stats["failed"].append((contest.get("id"), contest.get("name"), str(exc)))
                    continue
                store.upsert_contest(username, meta, problems)
                stats["contests"] += 1
                stats["problems"] += len(problems)
        if live:
            console.write("\r" + " " * 78 + "\r")
    else:
        for contest in contests:
            store.upsert_contest(username, contest, None)
            stats["contests"] += 1

    store.save_progress()
    return stats


# ---------------------------------------------------------------------------
# 查找辅助
# ---------------------------------------------------------------------------
def resolve_contest(store: Store, username: str, spec):
    """按 ID 或名称（不区分大小写的子串）查找本地缓存的比赛。"""
    contests = store.all_contests(username)
    text = str(spec).strip()
    for contest in contests:
        if str(contest.get("id")) == text:
            return contest, []
    matches = [c for c in contests if text.lower() in (c.get("name") or "").lower()]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def resolve_problem(entry: dict, spec):
    """按题号(displayOrder) 或 题目 ID 查找题目。"""
    problems = entry.get("problems") or []
    text = str(spec).strip()
    for problem in problems:
        if str(problem.get("displayOrder")) == text:
            return problem
    for problem in problems:
        if str(problem.get("problemId")) == text:
            return problem
    return None


def contest_link(base_url: str, contest_id, display_order=None) -> str:
    url = "%s/c/%s" % (base_url.rstrip("/"), contest_id)
    if display_order is not None:
        url += "/p/%s" % display_order
    return url


def print_contest_header(console: Console, entry: dict) -> None:
    name = entry.get("name") or ("比赛 %s" % entry.get("id"))
    console.title("%s  ·  #%s" % (name, entry.get("id")))
    phase = contest_phase(entry.get("startTime"), entry.get("endTime"))
    bits = [
        console.paint(PHASE_LABEL.get(phase, "?"), PHASE_COLOR.get(phase, "grey"), bold=True),
        "模式 " + str(entry.get("mode") or "-"),
        "%s ~ %s" % (fmt_time(entry.get("startTime")), fmt_time(entry.get("endTime"))),
    ]
    if entry.get("creator"):
        bits.append("创建者 " + str(entry["creator"]))
    if entry.get("groups"):
        bits.append("分组 " + ", ".join(entry["groups"]))
    console.out("  " + console.paint(" | ", "grey").join(bits))


# ---------------------------------------------------------------------------
# 命令：login / logout / whoami / config
# ---------------------------------------------------------------------------
def _verify_credentials(client: Client, console: Console) -> str:
    try:
        user = client.current_user()
    except ApiError as exc:
        raise ApiError("无法连接站点：%s" % exc.message, exc.status, exc.hint)
    if not user:
        raise ApiError("凭据无效：站点未识别为已登录状态", 401,
                       "请确认复制的是最新的 token/Cookie（网页登录后打开 F12 → Application → "
                       "Local Storage → session-swr 复制整段值）")
    return str(user.get("username") or user.get("id") or "unknown")


def cmd_logout(args, store: Store, console: Console) -> int:
    store.clear_credentials()
    store.save_config()
    console.ok("已清除本地登录凭据")
    console.muted("      做题标记仍保存在 %s" % store.progress_path)
    return 0


def cmd_whoami(args, store: Store, console: Console) -> int:
    try:
        info = store.client().session_info()
    except ApiError as exc:
        return report_error(console, exc)
    user = info.get("userMeta")
    server_pref = info.get("serverPreference") or {}
    version = info.get("serverVersion") or {}
    console.title("当前登录状态")
    if not user:
        console.warn("未登录（匿名访问）")
        console.muted("      运行 `nflsoj login` 后即可拉取比赛与题目")
    else:
        console.kv("用户名", str(user.get("username")), value_color="bgreen")
        console.kv("用户 ID", str(user.get("id")))
        console.kv("管理员", "是" if info.get("isManager") else "否")
        if user.get("joinedGroupsCount") is not None:
            console.kv("已加入小组", str(user.get("joinedGroupsCount")))
    console.rule()
    console.kv("站点", server_pref.get("siteName") or "-")
    console.kv("地址", store.api_base)
    console.kv("服务端版本", "%s (%s)" % (version.get("hash", "-"), str(version.get("date", "-"))[:10]))
    console.kv("数据目录", store.data_dir)
    return 0


def cmd_config(args, store: Store, console: Console) -> int:
    changed = False
    if args.set_api:
        store.config["baseUrl"] = args.set_api.rstrip("/")
        changed = True
    if args.clear:
        store.clear_credentials()
        changed = True
    if changed:
        store.save_config()
        console.ok("配置已更新")
    console.title("配置")
    console.kv("站点地址", store.api_base)
    console.kv("配置文件", store.config_path)
    console.kv("记录文件", store.progress_path)
    token = store.config.get("token") or ""
    console.kv("Token", (token[:8] + "…" + token[-6:]) if len(token) > 20 else (token or "(未设置)"))
    console.kv("Cookie", "已保存" if store.config.get("cookie") else "(未设置)")
    console.kv("记录账号", store.config.get("username") or "(未设置)")
    return 0


def cmd_login(args, store: Store, console: Console) -> int:
    base_url = (args.api or store.api_base or DEFAULT_API).rstrip("/")
    token = args.token or os.environ.get("NFLSOJ_TOKEN", "") or ""
    cookie = args.cookie or ""
    username = args.username or ""
    password = args.password or ""

    if token:
        token = extract_token(token) or token

    if not (token or cookie or username):
        console.title("登录 NFLSOJ")
        console.muted("站点：%s" % base_url)
        console.out()
        console.out("  1) 粘贴 Token / Cookie（推荐）")
        console.out("  2) 使用用户名 + 密码登录")
        console.out()
        try:
            choice = (input("请选择登录方式 [1/2]（默认 1）：").strip() or "1")
        except (EOFError, KeyboardInterrupt):
            console.error("已取消")
            return 1
        if choice.startswith("2"):
            try:
                username = input("用户名：").strip()
                import getpass

                password = getpass.getpass("密码（输入时不显示）：")
            except (EOFError, KeyboardInterrupt):
                console.error("已取消")
                return 1
        else:
            console.muted("提示：网页登录后按 F12 → Application → Local Storage → 复制 session-swr 的值；")
            console.muted("      或从 Network 面板复制请求头中的 Cookie / Authorization。")
            try:
                raw = input("在此粘贴 Token / Cookie / session-swr 内容：").strip()
            except (EOFError, KeyboardInterrupt):
                console.error("已取消")
                return 1
            token = extract_token(raw) or ""
            if not token and raw:
                cookie = raw
            if not token and not cookie:
                console.error("无法从输入中识别出登录凭据")
                return 1

    client = Client(base_url=base_url, token=token, cookie=cookie,
                    insecure=args.insecure, timeout=args.timeout, debug=args.debug)

    try:
        if username and not token and not cookie:
            console.info("正在使用用户名密码登录…")
            result = client.login(username, password)
            if result.get("error"):
                console.error("登录失败：%s" % result["error"])
                return 1
            token = result.get("token") or ""
            if not token:
                console.error("登录失败：服务器未返回 token")
                return 1
            client.token = token
            username = result.get("username") or username
        console.info("正在校验登录状态…")
        who = _verify_credentials(client, console)
    except ApiError as exc:
        return report_error(console, exc)

    store.load()
    store.set_credentials(token=token, cookie=cookie, username=who, base_url=base_url)
    store.save_config()
    console.out()
    console.ok("登录成功，欢迎回来，%s ！" % who)
    console.kv("数据目录", store.data_dir)
    console.kv("配置文件", store.config_path)
    console.muted("      下一步：运行 `nflsoj sync` 拉取全部比赛与题目列表")
    return 0


# ---------------------------------------------------------------------------
# 命令：sync / contests
# ---------------------------------------------------------------------------
def cmd_sync(args, store: Store, console: Console) -> int:
    try:
        username = require_login(store, console)
    except ApiError as exc:
        return report_error(console, exc)
    client = store.client(timeout=args.timeout,
                          insecure=True if args.insecure else None,
                          debug=True if args.debug else None)
    console.title("同步比赛与题目列表")
    console.kv("账号", username)
    console.kv("站点", store.api_base)
    if args.keyword:
        console.kv("关键词", args.keyword)
    console.out()
    try:
        stats = sync_contests(store, username, console, client,
                              keyword=args.keyword, only_ids=args.contest,
                              workers=args.workers, with_problems=not args.no_problems,
                              force=args.force)
    except ApiError as exc:
        return report_error(console, exc)
    console.out()
    console.ok("同步完成：%d 场比赛 / %d 道题目" % (stats["contests"], stats["problems"]))
    if stats["failed"]:
        console.warn("%d 场比赛的题目拉取失败：" % len(stats["failed"]))
        rows = [[(str(cid), "grey"), (name or "", ), (msg, "bred")] for cid, name, msg in stats["failed"][:10]]
        console.table(["比赛 ID", "比赛名称", "错误"], rows)
    console.muted("      可用 `nflsoj contests` 查看比赛列表，`nflsoj problems <比赛ID>` 查看题目。")
    return 0


def cmd_contests(args, store: Store, console: Console) -> int:
    try:
        username = require_login(store, console)
    except ApiError as exc:
        return report_error(console, exc)

    if args.live:
        client = store.client(timeout=args.timeout,
                              insecure=True if args.insecure else None,
                              debug=True if args.debug else None)
        console.title("比赛列表（实时）")
        try:
            contests = fetch_all_contests(client, keyword=args.keyword)
        except ApiError as exc:
            return report_error(console, exc)
        for contest in contests:
            store.upsert_contest(username, contest, None)
        store.save_progress()
    else:
        contests = store.all_contests(username)
        if not contests:
            console.warn("本地还没有比赛数据，正在自动同步一次…")
            client = store.client(timeout=args.timeout)
            try:
                contests = fetch_all_contests(client, keyword=args.keyword)
                for contest in contests:
                    store.upsert_contest(username, contest, None)
                store.save_progress()
            except ApiError as exc:
                return report_error(console, exc)

    keyword = (args.keyword or "").lower()
    if keyword:
        contests = [c for c in contests
                    if keyword in (c.get("name") or "").lower() or keyword == str(c.get("id"))]
    if args.phase:
        contests = [c for c in contests
                    if contest_phase(c.get("startTime"), c.get("endTime")) == args.phase]
    if args.ids:
        wanted = {str(i) for i in args.ids}
        contests = [c for c in contests if str(c.get("id")) in wanted]

    console.title("比赛列表 · 共 %d 场" % len(contests))
    console.kv("账号", username)
    console.kv("数据来源", "实时请求" if args.live else "本地缓存（--live 可刷新）")
    console.out()
    if not contests:
        console.warn("没有匹配的比赛")
        return 0

    rows = []
    total_done = total_problems = 0
    for contest in contests[: args.limit]:
        phase = contest_phase(contest.get("startTime"), contest.get("endTime"))
        problems = contest.get("problems") or []
        if problems:
            done = sum(1 for p in problems if p.get("state") == STATE_DONE)
            total_done += done
            total_problems += len(problems)
            prog = "%s %d/%d" % (console.bar(done, len(problems), width=8), done, len(problems))
        else:
            prog = console.paint("未同步", "grey")
        rows.append([
            (PHASE_LABEL.get(phase, "?"), PHASE_COLOR.get(phase, "grey")),
            (str(contest.get("id")), "grey"),
            (contest.get("name") or "",),
            (str(contest.get("mode") or "-"), "magenta"),
            (fmt_time(contest.get("startTime")), "grey"),
            (fmt_time(contest.get("endTime")), "grey"),
            (prog,),
        ])
    console.table(["状态", "ID", "比赛名称", "模式", "开始时间", "结束时间", "已完成"],
                  rows, aligns=["center", "right", "left", "center", "left", "left", "left"])
    if len(contests) > args.limit:
        console.muted("      仅显示前 %d 场，可用 --limit 调整" % args.limit)
    if total_problems:
        console.out()
        console.out("  %s %d/%d 题已完成  ·  %s" % (
            console.paint("总计", "bcyan", bold=True),
            total_done, total_problems,
            console.bar(total_done, total_problems, width=30)))
    return 0


# ---------------------------------------------------------------------------
# 命令：problems / mark
# ---------------------------------------------------------------------------
def _load_contest_entry(store: Store, username: str, spec, console: Console, refresh=False):
    """找到本地比赛记录；必要时向服务器补拉题目列表。返回 (entry, error_code)。"""
    contest, matches = resolve_contest(store, username, spec)
    if contest is None:
        if matches:
            console.error("匹配到多场比赛，请使用更精确的名称或直接使用 ID：")
            console.table(["ID", "名称"],
                          [[(str(c.get("id")), "grey"), (c.get("name") or "",)] for c in matches[:15]])
        else:
            console.error("找不到比赛：%s" % spec)
            console.muted("      请先运行 `nflsoj sync` 或 `nflsoj contests --live`")
        return None, 1
    entry = store.contest_entry(username, contest.get("id"))
    if entry is None:
        entry = store.upsert_contest(username, contest, None)
    if refresh or not (entry.get("problems") or []):
        try:
            meta, problems = fetch_contest_detail(store.client(), contest.get("id"))
            entry = store.upsert_contest(username, meta, problems)
            store.save_progress()
        except ApiError as exc:
            if not entry.get("problems"):
                report_error(console, exc)
                return None, 1
            console.warn("刷新题目列表失败：%s（继续使用本地缓存）" % exc.message)
    return entry, 0


def cmd_problems(args, store: Store, console: Console) -> int:
    try:
        username = require_login(store, console)
    except ApiError as exc:
        return report_error(console, exc)

    entry, code = _load_contest_entry(store, username, args.contest, console, refresh=args.refresh)
    if entry is None:
        return code

    problems = list(entry.get("problems") or [])
    if args.state:
        wanted = {(STATE_ALIASES.get(s.lower(), s.lower())) for s in args.state}
        problems = [p for p in problems if (p.get("state") or STATE_TODO) in wanted]

    if args.json:
        console.out(json.dumps({"contest": entry, "problems": problems},
                               ensure_ascii=False, indent=2))
        return 0

    print_contest_header(console, entry)
    console.out()
    if not problems:
        console.warn("该比赛没有题目（或已被 --state 过滤掉）")
        if entry.get("syncedAt"):
            console.muted("      上次同步：%s" % human_age(entry.get("syncedAt")))
        return 0

    rows = []
    counts = {STATE_TODO: 0, STATE_DOING: 0, STATE_DONE: 0}
    for problem in problems:
        state = problem.get("state") or STATE_TODO
        counts[state] = counts.get(state, 0) + 1
        note = ""
        if problem.get("submitted"):
            note = "服务端已提交"
        if problem.get("tags") and args.tags:
            note = (note + " #" + " #".join(problem["tags"])).strip()
        rows.append([
            (console.state_glyph(state),),
            (STATE_LABEL.get(state, state), STATE_COLOR.get(state, "grey")),
            (str(problem.get("displayOrder", "")), "grey"),
            (problem.get("title") or "",),
            (str(problem.get("problemId", "")), "grey"),
            (note, "bcyan" if note.startswith("服务端") else "grey"),
            (human_age(problem.get("stateUpdatedAt")), "grey"),
        ])
    console.table(["", "标记", "题号", "题目名称", "题目 ID", "备注", "更新"],
                  rows, aligns=["center", "center", "right", "left", "right", "left", "left"])

    total = len(problems)
    console.out()
    console.out("  %s %d/%d 已完成  ·  %s" % (
        console.paint("本题单", "bcyan", bold=True), counts[STATE_DONE], total,
        console.bar(counts[STATE_DONE], total, width=28)))
    legend = "  ".join("%s %s" % (console.state_glyph(s), STATE_LABEL[s]) for s in STATE_ORDER)
    console.out("  " + legend)
    console.muted("      标记示例：nflsoj mark %s 1 done" % entry.get("id"))
    console.muted("      网页地址：%s" % contest_link(store.api_base, entry.get("id")))
    if args.open:
        url = contest_link(store.api_base, entry.get("id"))
        console.info("正在打开 %s" % url)
        webbrowser.open(url)
    return 0


def cmd_mark(args, store: Store, console: Console) -> int:
    try:
        username = require_login(store, console)
    except ApiError as exc:
        return report_error(console, exc)

    entry, code = _load_contest_entry(store, username, args.contest, console, refresh=args.refresh)
    if entry is None:
        return code

    problem = resolve_problem(entry, args.problem)
    if problem is None:
        console.error("找不到题目：%s" % args.problem)
        console.muted("      可用 `nflsoj problems %s` 查看题号列表" % entry.get("id"))
        return 1

    if args.clear:
        new_state = None
    elif args.state:
        key = str(args.state).strip().lower()
        if key not in STATE_ALIASES:
            console.error("无法识别的状态：%s" % args.state)
            console.muted("      可用状态：todo / doing / done（或 未完成 / 正在做 / 已完成）")
            return 1
        new_state = STATE_ALIASES[key]
    else:
        current = problem.get("state") or STATE_TODO
        new_state = STATE_ORDER[(STATE_ORDER.index(current) + 1) % len(STATE_ORDER)]

    try:
        store.set_state(username, entry.get("id"), problem.get("problemId"), new_state)
    except KeyError as exc:
        console.error("保存失败：%s" % exc)
        return 1
    store.save_progress()

    label = STATE_LABEL[new_state] if new_state else "未标记"
    console.title("更新做题标记")
    console.kv("比赛", "%s (#%s)" % (entry.get("name") or "", entry.get("id")))
    console.kv("题目", "%s. %s" % (problem.get("displayOrder"), problem.get("title") or ""))
    console.kv("状态", "%s %s" % (console.state_glyph(new_state), label),
               value_color=STATE_COLOR.get(new_state or STATE_TODO, "grey"))
    console.ok("已保存到 %s" % store.progress_path)

    problems = entry.get("problems") or []
    done = sum(1 for p in problems if p.get("state") == STATE_DONE)
    if problems:
        console.out("  %s %d/%d  ·  %s" % (
            console.paint("本场比赛进度", "bcyan"), done, len(problems),
            console.bar(done, len(problems), width=28)))
    return 0


# ---------------------------------------------------------------------------
# 命令：stats
# ---------------------------------------------------------------------------
def collect_stats(contests) -> dict:
    overall = {"contests": len(contests), "problems": 0,
               STATE_TODO: 0, STATE_DOING: 0, STATE_DONE: 0, "submitted": 0, "synced": 0}
    per_contest = []
    recent = []
    for contest in contests:
        problems = contest.get("problems") or []
        row = {"contest": contest, "total": len(problems),
               STATE_TODO: 0, STATE_DOING: 0, STATE_DONE: 0, "submitted": 0}
        for problem in problems:
            state = problem.get("state") or STATE_TODO
            row[state] = row.get(state, 0) + 1
            overall[state] = overall.get(state, 0) + 1
            if problem.get("submitted"):
                row["submitted"] += 1
                overall["submitted"] += 1
            if problem.get("stateUpdatedAt"):
                recent.append((problem["stateUpdatedAt"], contest, problem))
        overall["problems"] += row["total"]
        if problems:
            overall["synced"] += 1
        per_contest.append(row)
    recent.sort(key=lambda item: item[0], reverse=True)
    return {"overall": overall, "per_contest": per_contest, "recent": recent}


def cmd_stats(args, store: Store, console: Console) -> int:
    try:
        username = require_login(store, console)
    except ApiError as exc:
        return report_error(console, exc)

    contests = store.all_contests(username)
    if args.contest:
        picked = []
        for spec in args.contest:
            contest, _ = resolve_contest(store, username, spec)
            if contest:
                picked.append(contest)
        contests = picked
    if args.keyword:
        needle = args.keyword.lower()
        contests = [c for c in contests if needle in (c.get("name") or "").lower()]
    if args.phase:
        contests = [c for c in contests if contest_phase(c.get("startTime"), c.get("endTime")) == args.phase]

    stats = collect_stats(contests)
    if args.json:
        console.out(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0

    overall = stats["overall"]
    total = overall["problems"]
    done = overall[STATE_DONE]
    doing = overall[STATE_DOING]
    todo = overall[STATE_TODO]

    console.title("做题情况统计 · %s" % username)
    console.kv("比赛数量", "%d 场（已同步题目 %d 场）" % (overall["contests"], overall["synced"]))
    console.kv("题目总数", "%d 题" % total)
    console.out()
    if total == 0:
        console.warn("还没有题目数据，先运行 `nflsoj sync` 同步吧")
        return 0

    rate = done / float(total) * 100
    console.out("  %s  %s  %s" % (
        console.paint("总完成度", "bcyan", bold=True),
        console.bar(done, total, width=40),
        console.paint("%.1f%%" % rate, "bgreen", bold=True)))
    console.out()
    blocks = [
        (STATE_DONE, done), (STATE_DOING, doing), (STATE_TODO, todo),
    ]
    for state, count in blocks:
        pct = count / float(total) * 100
        console.out("  %s %s %-6s %5d 题  %6.1f%%" % (
            console.state_glyph(state),
            console.paint(STATE_LABEL[state], STATE_COLOR.get(state, "grey"), bold=True),
            "", count, pct))
    if overall["submitted"]:
        console.out()
        console.out("  %s 服务端检测到你已提交过 %d 题（仅供参考，可能因权限不完整）" % (
            console.paint("i", "bcyan"), overall["submitted"]))
    console.out()

    rows = []
    ranked = sorted(stats["per_contest"], key=lambda r: r["total"], reverse=True)
    for row in ranked[: args.limit]:
        contest = row["contest"]
        phase = contest_phase(contest.get("startTime"), contest.get("endTime"))
        if row["total"]:
            prog = "%s %d/%d" % (console.bar(row[STATE_DONE], row["total"], width=12),
                                 row[STATE_DONE], row["total"])
        else:
            prog = console.paint("未同步", "grey")
        rows.append([
            (PHASE_LABEL.get(phase, "?"), PHASE_COLOR.get(phase, "grey")),
            (str(contest.get("id")), "grey"),
            (contest.get("name") or "",),
            (str(row["total"]),),
            (str(row[STATE_DONE]), "bgreen" if row[STATE_DONE] else "grey"),
            (str(row[STATE_DOING]), "yellow" if row[STATE_DOING] else "grey"),
            (str(row[STATE_TODO]), "grey"),
            (prog,),
        ])
    if rows:
        console.subtitle("按比赛统计")
        console.table(["状态", "ID", "比赛名称", "题目", "已完成", "正在做", "未完成", "进度"],
                      rows, aligns=["center", "right", "left", "right", "right", "right", "right", "left"])
        if len(ranked) > args.limit:
            console.muted("      仅显示前 %d 场，可用 --limit 调整" % args.limit)

    if stats["recent"] and not args.no_recent:
        console.out()
        console.subtitle("最近标记")
        recent_rows = []
        for ts, contest, problem in stats["recent"][: args.recent]:
            recent_rows.append([
                (STATE_LABEL.get(problem.get("state"), "-"), STATE_COLOR.get(problem.get("state"), "grey")),
                (human_age(ts), "grey"),
                ((contest.get("name") or "")[:22],),
                ("%s. %s" % (problem.get("displayOrder"), problem.get("title") or ""),),
            ])
        console.table(["状态", "时间", "比赛", "题目"], recent_rows,
                      aligns=["center", "left", "left", "left"])
    return 0


# ---------------------------------------------------------------------------
# 命令：open / export
# ---------------------------------------------------------------------------
def cmd_open(args, store: Store, console: Console) -> int:
    try:
        username = require_login(store, console)
    except ApiError as exc:
        return report_error(console, exc)
    contest, matches = resolve_contest(store, username, args.contest)
    if contest is None:
        if matches:
            console.error("匹配到多场比赛，请使用 ID：")
            console.table(["ID", "名称"],
                          [[(str(c.get("id")), "grey"), (c.get("name") or "",)] for c in matches[:15]])
        else:
            console.error("找不到比赛：%s" % args.contest)
        return 1

    display_order = None
    if args.problem:
        entry = store.contest_entry(username, contest.get("id")) or {}
        problem = resolve_problem(entry, args.problem)
        if problem is None:
            console.error("找不到题目：%s" % args.problem)
            return 1
        display_order = problem.get("displayOrder")

    url = contest_link(store.api_base, contest.get("id"), display_order)
    console.info(url)
    if not args.print_only:
        webbrowser.open(url)
    return 0


def iter_rows(contests):
    for contest in contests:
        for problem in contest.get("problems") or []:
            state = problem.get("state") or STATE_TODO
            yield {
                "contest_id": contest.get("id"),
                "contest_name": contest.get("name") or "",
                "display_order": problem.get("displayOrder"),
                "problem_id": problem.get("problemId"),
                "problem_title": problem.get("title") or "",
                "state": state,
                "state_label": STATE_LABEL.get(state, state),
                "updated_at": problem.get("stateUpdatedAt") or "",
            }


def cmd_export(args, store: Store, console: Console) -> int:
    try:
        username = require_login(store, console)
    except ApiError as exc:
        return report_error(console, exc)

    contests = store.all_contests(username)
    if args.contest:
        picked = []
        for spec in args.contest:
            contest, _ = resolve_contest(store, username, spec)
            if contest:
                picked.append(contest)
        contests = picked

    fmt = args.format
    buffer = io.StringIO()
    if fmt == "json":
        payload = {"user": username, "exportedAt": int(time.time()),
                   "contests": [{"id": c.get("id"), "name": c.get("name"),
                                 "problems": c.get("problems") or []} for c in contests]}
        buffer.write(json.dumps(payload, ensure_ascii=False, indent=2))
    elif fmt == "csv":
        writer = csv.DictWriter(buffer, fieldnames=[
            "contest_id", "contest_name", "display_order", "problem_id",
            "problem_title", "state", "state_label", "updated_at"])
        writer.writeheader()
        for row in iter_rows(contests):
            writer.writerow(row)
    else:  # markdown
        buffer.write("# NFLSOJ 做题记录 · %s\n\n" % username)
        buffer.write("导出时间：%s\n\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        for contest in contests:
            problems = contest.get("problems") or []
            done = sum(1 for p in problems if p.get("state") == STATE_DONE)
            buffer.write("## %s (#%s) — %d/%d\n\n" % (
                contest.get("name") or "", contest.get("id"), done, len(problems)))
            buffer.write("| 题号 | 题目 | 状态 | 题目 ID |\n|---|---|---|---|\n")
            for problem in problems:
                state = problem.get("state") or STATE_TODO
                buffer.write("| %s | %s | %s | %s |\n" % (
                    problem.get("displayOrder"), problem.get("title") or "",
                    STATE_LABEL.get(state, state), problem.get("problemId")))
            buffer.write("\n")

    text = buffer.getvalue()
    if args.output and args.output != "-":
        path = os.path.abspath(args.output)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        console.ok("已导出到 %s" % path)
    else:
        console.out(text.rstrip())
    return 0


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
EPILOG = """示例：
  nflsoj login                                 # 交互式登录（粘贴 Token/Cookie）
  nflsoj login --cookie "session-swr=..."      # 用浏览器 Cookie 登录
  nflsoj login -u 用户名 -p 密码                # 用用户名密码登录

  nflsoj sync                                  # 爬取全部比赛 + 每场比赛题目列表
  nflsoj contests                              # 查看比赛列表（含完成进度）
  nflsoj problems 12                           # 查看 12 号比赛的题目名称列表
  nflsoj problems "月考" --state todo           # 只看未完成的题目

  nflsoj mark 12 3 done                        # 把 12 号比赛第 3 题标记为已完成
  nflsoj mark 12 3 doing                       # 标记为正在做
  nflsoj mark 12 3 todo                        # 标记为未完成
  nflsoj mark 12 3                             # 不带状态：未完成->正在做->已完成 循环
  nflsoj mark 12 3 --clear                     # 清除标记

  nflsoj stats                                 # 美观的做题情况统计
  nflsoj stats --contest 12                    # 只统计某一场
  nflsoj open 12 3                             # 浏览器打开题目页

数据存储：%s
""" % DEFAULT_DATA_DIR


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=None, help="数据目录（默认 %s）" % DEFAULT_DATA_DIR)
    common.add_argument("--api", default=None, help="站点 API 地址（默认 %s）" % DEFAULT_API)
    common.add_argument("--timeout", type=int, default=30, help="HTTP 超时秒数（默认 30）")
    common.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验")
    common.add_argument("--debug", action="store_true", help="打印 HTTP 请求细节")
    common.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    common.add_argument("--ascii", action="store_true", help="使用纯 ASCII 字符（兼容老终端）")

    parser = argparse.ArgumentParser(
        prog="nflsoj",
        description="NFLSOJ (nflsoi.cc) 命令行工具 —— 登录、爬取比赛与题目、标记做题状态、统计进度。",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="nflsoj %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("login", parents=[common], help="登录（支持 Cookie / Token / 用户名密码）")
    p.add_argument("--token", help="直接提供 token")
    p.add_argument("--cookie", help="直接提供浏览器 Cookie 串")
    p.add_argument("-u", "--username", help="用户名")
    p.add_argument("-p", "--password", help="密码")
    p.set_defaults(handler=cmd_login)

    p = sub.add_parser("logout", parents=[common], help="清除本地登录凭据")
    p.set_defaults(handler=cmd_logout)

    p = sub.add_parser("whoami", parents=[common], help="显示当前登录状态")
    p.set_defaults(handler=cmd_whoami)

    p = sub.add_parser("config", parents=[common], help="查看 / 修改本地配置")
    p.add_argument("--set-api", help="设置站点 API 地址")
    p.add_argument("--clear", action="store_true", help="清除登录凭据")
    p.set_defaults(handler=cmd_config)

    p = sub.add_parser("sync", parents=[common], help="爬取全部比赛与题目列表")
    p.add_argument("--keyword", help="只同步名称包含该关键词的比赛")
    p.add_argument("--contest", nargs="*", type=int, help="只同步指定 ID 的比赛")
    p.add_argument("--workers", type=int, default=6, help="并发线程数（默认 6）")
    p.add_argument("--no-problems", action="store_true", help="只同步比赛列表，不拉题目")
    p.add_argument("--force", action="store_true", help="强制重新拉取已有比赛的题目")
    p.set_defaults(handler=cmd_sync)

    p = sub.add_parser("contests", parents=[common], aliases=["ls"], help="列出比赛")
    p.add_argument("--live", action="store_true", help="直接请求服务器（不读缓存）")
    p.add_argument("--keyword", help="按名称过滤")
    p.add_argument("--phase", choices=["not_started", "running", "ended"], help="按状态过滤")
    p.add_argument("--ids", nargs="*", type=int, help="只显示指定 ID")
    p.add_argument("--limit", type=int, default=50, help="最多显示多少场（默认 50）")
    p.set_defaults(handler=cmd_contests)

    p = sub.add_parser("problems", parents=[common], aliases=["ps"], help="列出某场比赛的题目")
    p.add_argument("contest", help="比赛 ID 或名称关键词")
    p.add_argument("--refresh", action="store_true", help="强制从服务器刷新题目列表")
    p.add_argument("--state", nargs="*", help="按状态过滤：todo / doing / done")
    p.add_argument("--tags", action="store_true", help="显示题目标签")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.add_argument("--open", action="store_true", help="在浏览器中打开该比赛")
    p.set_defaults(handler=cmd_problems)

    p = sub.add_parser("mark", parents=[common], aliases=["m"], help="标记题目状态")
    p.add_argument("contest", help="比赛 ID 或名称关键词")
    p.add_argument("problem", help="题号（列表中的序号）或题目 ID")
    p.add_argument("state", nargs="?", help="todo / doing / done（省略则在三种状态间循环）")
    p.add_argument("--clear", action="store_true", help="清除标记")
    p.add_argument("--refresh", action="store_true", help="先刷新题目列表")
    p.set_defaults(handler=cmd_mark)

    p = sub.add_parser("stats", parents=[common], aliases=["st"], help="做题情况统计")
    p.add_argument("--contest", nargs="*", help="只统计指定比赛（ID 或名称）")
    p.add_argument("--keyword", help="按比赛名称过滤")
    p.add_argument("--phase", choices=["not_started", "running", "ended"], help="按状态过滤")
    p.add_argument("--limit", type=int, default=20, help="每个表格最多显示多少行（默认 20）")
    p.add_argument("--recent", type=int, default=8, help="最近标记显示条数（默认 8）")
    p.add_argument("--no-recent", action="store_true", help="不显示最近标记")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(handler=cmd_stats)

    p = sub.add_parser("open", parents=[common], aliases=["o"], help="在浏览器中打开比赛 / 题目")
    p.add_argument("contest", help="比赛 ID 或名称关键词")
    p.add_argument("problem", nargs="?", help="题号（可选）")
    p.add_argument("--print-only", action="store_true", help="只打印链接，不打开浏览器")
    p.set_defaults(handler=cmd_open)

    p = sub.add_parser("export", parents=[common], help="导出做题记录")
    p.add_argument("--format", choices=["md", "csv", "json"], default="md", help="导出格式（默认 md）")
    p.add_argument("-o", "--output", help="输出文件（默认打印到屏幕）")
    p.add_argument("--contest", nargs="*", help="只导出指定比赛")
    p.set_defaults(handler=cmd_export)

    return parser


def cmd_dashboard(args, store: Store, console: Console) -> int:
    """不带子命令时显示的概览。"""
    console.title("NFLSOJ 命令行工具  v%s" % __version__)
    user = None
    try:
        user = store.client().current_user()
    except ApiError as exc:
        console.warn("无法连接站点：%s" % exc.message)
    if user:
        console.kv("登录账号", str(user.get("username")), value_color="bgreen")
    else:
        console.kv("登录账号", "未登录", value_color="yellow")
        console.muted("      运行 `nflsoj login` 登录（支持粘贴浏览器 Cookie / Token）")
    console.kv("站点", store.api_base)

    username = store.config.get("username") or (str(user.get("username")) if user else "")
    if username:
        contests = store.all_contests(username)
        if contests:
            stats = collect_stats(contests)["overall"]
            total = stats["problems"]
            console.out()
            if total:
                console.out("  %s  %s  %s" % (
                    console.paint("总完成度", "bcyan", bold=True),
                    console.bar(stats[STATE_DONE], total, width=36),
                    console.paint("%.1f%%" % (stats[STATE_DONE] / float(total) * 100),
                                  "bgreen", bold=True)))
                console.out("  %s %s   %s %s   %s %s" % (
                    console.state_glyph(STATE_DONE), "已完成 %d" % stats[STATE_DONE],
                    console.state_glyph(STATE_DOING), "正在做 %d" % stats[STATE_DOING],
                    console.state_glyph(STATE_TODO), "未完成 %d" % stats[STATE_TODO]))
            else:
                console.muted("      本地有 %d 场比赛记录，但还没有题目数据" % len(contests))
        else:
            console.out()
            console.muted("      还没有本地数据，运行 `nflsoj sync` 爬取全部比赛与题目列表")
    console.out()
    console.subtitle("常用命令")
    console.out("  login      登录（粘贴 Cookie / Token 或用户名密码）")
    console.out("  sync       爬取全部比赛与题目列表")
    console.out("  contests   查看比赛列表与进度")
    console.out("  problems   查看某场比赛的题目名称列表")
    console.out("  mark       标记题目：todo / doing / done")
    console.out("  stats      做题情况统计")
    console.out("  export     导出做题记录（md / csv / json）")
    console.out()
    console.muted("      完整帮助：nflsoj --help    ·   数据目录：%s" % store.data_dir)
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    enable_windows_ansi()

    ascii_only = bool(getattr(args, "ascii", False))
    if not ascii_only:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            "─│┌✔○◐→█░…".encode(encoding)
        except Exception:
            ascii_only = True

    color = not bool(getattr(args, "no_color", False))
    if os.environ.get("NO_COLOR"):
        color = False
    try:
        if not sys.stdout.isatty():
            color = False
    except Exception:
        color = False

    console = Console(color=color, ascii_only=ascii_only)
    data_dir = getattr(args, "data_dir", None) or DEFAULT_DATA_DIR
    store = Store(data_dir)
    store.load()

    handler = getattr(args, "handler", None) or cmd_dashboard
    try:
        return handler(args, store, console)
    except ApiError as exc:
        return report_error(console, exc)
    except KeyboardInterrupt:
        console.out()
        console.warn("已取消")
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())

