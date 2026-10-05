# -*- coding: utf-8 -*-
"""
ai_assistant.py —— 桌宠"多多"智能助手（独立模块，不依赖 PyQt）
====================================================
职责：把"工具能力"和"大模型问答"从主程序里剥离，保持主程序干净。

组成：
  1. 纯函数工具层（可单测）：
     now_text()           时间/日期
     resolve_site(text)   网址/站点名 -> URL
     resolve_app(text)    应用名 -> 可执行命令
     find_files(query)    按关键词找文件（桌面/文档/下载/当前目录）
     parse_action(text)   从回复里解析 [action: xxx] 标签
  2. LLMClient：OpenAI 兼容 API（DeepSeek 默认），后台线程请求，
     通过回调把 (回复, 动作) 送回主线程；未配置 key 时自动禁用。

配置：config.json（首次运行自动生成模板）
  {"api_base":"https://api.deepseek.com/v1","api_key":"","model":"deepseek-chat"}
  也可用环境变量 DUODUO_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY 提供密钥。
"""
import os
import re
import json
import ssl
import threading
import http.client
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import app_health

# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------
CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "api_base": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "timeout": 30,
    "confirm_before_send": False,   # 隐私开关：剪贴板/拖入文件的内容先给主人过目再发给大模型
}

SITE_MAP = {
    "b站": "https://www.bilibili.com", "bilibili": "https://www.bilibili.com", "哔哩哔哩": "https://www.bilibili.com",
    "百度": "https://www.baidu.com", "微博": "https://weibo.com", "知乎": "https://www.zhihu.com",
    "淘宝": "https://www.taobao.com", "京东": "https://www.jd.com", "哔哩": "https://www.bilibili.com",
    "github": "https://github.com", "bing": "https://www.bing.com", "谷歌": "https://www.google.com",
    "youtube": "https://www.youtube.com", "油管": "https://www.youtube.com", "抖音": "https://www.douyin.com",
}

APP_MAP = {
    "记事本": "notepad.exe", "计算器": "calc.exe", "画图": "mspaint.exe",
    "浏览器": "explorer.exe", "资源管理器": "explorer.exe", "控制面板": "control.exe",
    "任务管理器": "taskmgr.exe", "cmd": "cmd.exe", "终端": "cmd.exe",
}

# 找文件时搜索的根目录（按优先级）
SEARCH_ROOTS = [
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Downloads"),
    os.getcwd(),
]
SEARCH_MAX_DEPTH = 4      # 目录深度上限
SEARCH_MAX_VISIT = 4000   # 最多浏览多少个目录（防止全盘扫描）
SEARCH_LIMIT = 5          # 最多返回几个结果

URL_RE = re.compile(r"(https?://[^\s，。！？、]+)", re.I)
ACTION_TAG = re.compile(r"\[action\s*:\s*(\w+)\]", re.I)
TOOL_TAG = re.compile(r"\[tool\s*:\s*([^\]]+)\]", re.I)
MOOD_TAG = re.compile(r"\[mood\s*:\s*([^\]]+)\]", re.I)

# ------------------------------------------------------------------
# 配置
# ------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            app_health.log(f"读 config.json 失败（这次先用默认配置）：{e}", level=30)
    # 环境变量优先：这样 key 可以不落盘（换机器、CI、或不想让别人看到文件时用）
    key = (os.environ.get("DUODUO_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY")
           or os.environ.get("OPENAI_API_KEY") or "")
    if key:
        cfg["api_key"] = key
    for env, field in (("DUODUO_API_BASE", "api_base"), ("DUODUO_MODEL", "model")):
        val = (os.environ.get(env) or "").strip()
        if val:
            cfg[field] = val
    return cfg


def set_config_values(**pairs):
    """
    只更新 config.json 里指定的几个键，返回值表示有没有写成功。
    **永远不写 api_key**（key 只从文件外读：环境变量或主人自己填），
    免得程序把 key 又抄进一个能被截图/分享带走的地方。
    """
    pairs.pop("api_key", None)
    try:
        data = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        data.update(pairs)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app_health.log(f"写 config.json 失败：{e}", level=30)
        return False


def ensure_config_file():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------
# 纯函数工具层
# ------------------------------------------------------------------
def now_text():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def resolve_site(text: str):
    """从文本里解析要打开的网址/站点。返回 URL 或 None。"""
    m = URL_RE.search(text)
    if m:
        return m.group(1)
    low = text.lower()
    for name, url in SITE_MAP.items():
        if name in low:
            return url
    return None


def resolve_app(text: str):
    """从文本里解析要打开的系统应用。返回可执行命令或 None。"""
    low = text.lower()
    for name, cmd in APP_MAP.items():
        if name in low:
            return cmd
    return None


def search_query(text: str):
    """提取"搜索/查一下 xxx"里的查询词。"""
    for kw in ("搜索", "搜一下", "查一下", "查查", "百度一下"):
        if kw in text:
            return text.split(kw, 1)[1].strip(" ，。？!的 ")
    return None


def find_files(query: str, roots=None, limit=SEARCH_LIMIT):
    """
    按文件名关键词找文件。返回按相关度+最近修改排序的路径列表。
    query 为空时返回 []。
    """
    query = (query or "").strip().lower()
    if not query or len(query) < 2:
        return []
    roots = roots or [r for r in SEARCH_ROOTS if os.path.isdir(r)]
    results = []           # (score, mtime, path)
    visited = 0
    for root in roots:
        if visited >= SEARCH_MAX_VISIT:
            break
        for dirpath, dirnames, filenames in os.walk(root):
            visited += 1
            if visited >= SEARCH_MAX_VISIT:
                break
            # 剪枝：隐藏目录、系统目录不搜
            dirnames[:] = [d for d in dirnames
                           if not d.startswith((".", "$")) and d not in ("node_modules", "AppData", "__pycache__")]
            depth = dirpath[len(root):].count(os.sep)
            if depth > SEARCH_MAX_DEPTH:
                dirnames[:] = []
                continue
            for name in filenames:
                low = name.lower()
                if query in low:
                    score = 0
                    if low == query:
                        score = 100
                    elif low.startswith(query):
                        score = 60
                    elif low.endswith(query):
                        score = 30
                    else:
                        score = 10
                    full = os.path.join(dirpath, name)
                    try:
                        mtime = os.path.getmtime(full)
                    except OSError:
                        mtime = 0
                    results.append((score, mtime, full))
        if visited >= SEARCH_MAX_VISIT:
            break
    results.sort(key=lambda x: (-x[0], -x[1]))
    return [r[2] for r in results[:limit]]



def find_folders(query: str, roots=None, limit: int = 5):
    """按名字找**文件夹**（搜索根与剪枝与 find_files 一致）。返回路径列表。"""
    query = (query or "").strip().lower()
    if not query or len(query) < 2:
        return []
    roots = roots or [r for r in SEARCH_ROOTS if os.path.isdir(r)]
    hits = []              # (score, mtime, path)
    visited = 0
    for root in roots:
        if visited >= SEARCH_MAX_VISIT:
            break
        for dirpath, dirnames, _files in os.walk(root):
            visited += 1
            if visited >= SEARCH_MAX_VISIT:
                break
            dirnames[:] = [d for d in dirnames
                           if not d.startswith((".", "$")) and d not in
                           ("node_modules", "AppData", "__pycache__", "site-packages")]
            depth = dirpath[len(root):].count(os.sep)
            if depth > SEARCH_MAX_DEPTH:
                dirnames[:] = []
                continue
            for name in dirnames:
                low = name.lower()
                if query in low:
                    score = 100 if low == query else (80 if low.startswith(query) else 60)
                    try:
                        mtime = os.path.getmtime(os.path.join(dirpath, name))
                    except OSError:
                        mtime = 0
                    hits.append((score, mtime, os.path.join(dirpath, name)))
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return [h[2] for h in hits[:limit]]


def short_path(path: str, keep_dirs: int = 1):
    """压缩显示路径：只保留最后 keep_dirs 层目录 + 文件名。"""
    parts = path.replace("\\", "/").split("/")
    tail = parts[-(keep_dirs + 1):]
    return "/".join(tail)


def parse_action(text: str):
    """解析回复末尾的 [action: xxx]，返回 (干净文本, 动作或None)。"""
    m = ACTION_TAG.search(text or "")
    if not m:
        return text, None
    action = m.group(1).strip().lower()
    clean = ACTION_TAG.sub("", text).strip()
    return clean, action


# ---- 打开方式：常见程序名 → 可执行文件；"@browser"=默认浏览器，"@picker"=Windows 打开方式选择框 ----
OPEN_WITH = {
    "记事本": "notepad", "notepad": "notepad",
    "写字板": "write", "wordpad": "write",
    "画图": "mspaint", "mspaint": "mspaint",
    "浏览器": "@browser", "网页打开": "@browser",
    "vscode": "code", "vs code": "code", "代码编辑器": "code",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "ppt": "powerpnt",
    "终端": "wt", "命令行": "cmd", "cmd": "cmd",
    "播放器": "wmplayer", "media player": "wmplayer",
    "计算器": "calc",
}
PICKER_WORDS = ("换个方式", "换一种方式", "选择打开方式", "打开方式", "别的程序", "其它程序",
                "其他程序", "换程序", "自己选", "手动选")


OPEN_WITH_CN = {
    "notepad": "记事本", "write": "写字板", "mspaint": "画图", "code": "VS Code",
    "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint",
    "cmd": "命令行", "wt": "终端", "wmplayer": "播放器", "calc": "计算器",
    "@browser": "浏览器", "@picker": "打开方式选择框",
}


def open_with_cn(app: str):
    """把内部程序名翻译成给主人看的说法。"""
    return OPEN_WITH_CN.get(str(app or ""), str(app or ""))


def parse_open_target(text: str):
    """
    从"用记事本打开第2个"这类话里解析出打开方式，返回 (说明, 目标) 或 None。
      用记事本打开     → ("记事本", "notepad")
      用浏览器打开     → ("浏览器", "@browser")
      换个方式打开/选择打开方式 → ("选择打开方式", "@picker")
    """
    if not text:
        return None
    t = text.strip()
    low = t.lower()
    if any(w in t for w in PICKER_WORDS):
        return ("选择打开方式", "@picker")
    for key, exe in OPEN_WITH.items():
        if key in low:
            return (key, exe)
    # 词表里没有的程序名也认（"用photoshop打开"/"用notepad++打开"），交给 which 去找；
    # 找不到时由调用方给提示并建议"换个方式打开"
    m = re.search(r"用\s*([A-Za-z0-9_\u4e00-\u9fff+.\-]{1,20}?)\s*(?:来)?打开", t)
    if m:
        name = m.group(1).strip()
        if name and name not in ("它", "这个", "那个", "什么", "哪个"):
            return (name, name)
    return None


def parse_open_index(text: str):
    """
    解析"打开第几个"里的序号，返回 1 起算的整数；没提到序号返回 None。
      "打开第2个" / "打开第二个" / "打开 3" / "第 2 个文件夹" → 2 / 2 / 3 / 2
    只认明确的序号说法，避免把普通句子误判。
    """
    if not text:
        return None
    t = text.strip()
    m = re.search(r"第\s*([0-9]+|[一二三四五六七八九十两]{1,3})\s*个?", t)
    if m:
        return _cn_number(m.group(1))
    m = re.search(r"(?:打开|开)\s*([0-9]+)(?:\s*个)?$", t)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            return None
    return None


_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_number(token):
    """把"2"或"二"/"十二"这类写法转成整数。"""
    token = token.strip()
    if token.isdigit():
        try:
            return max(1, int(token))
        except ValueError:
            return None
    if token in _CN_DIGITS:
        return _CN_DIGITS[token]
    if "十" in token:                       # 十二 / 二十 / 二十一
        head, _, tail = token.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return None


def parse_tags(text: str):
    """
    解析模型回复里的动作 / 工具 / 情绪标签，返回 (干净文本, 动作, 工具, 情绪)。
      [action: hop]                 → 动作
      [tool: open bilibili]         → 工具（名字 + 参数）
      [mood: happy]                 → 情绪（决定朗读语调：开心/困倦/撒娇…）
    三者可以同时出现，都会被剥掉；没有的就是 None。
    """
    text = text or ""
    tool = None
    mood = None
    m = TOOL_TAG.search(text)
    if m:
        parts = m.group(1).strip().split(None, 1)
        tool = (parts[0].lower(), parts[1].strip() if len(parts) > 1 else "")
        text = TOOL_TAG.sub("", text)
    m = MOOD_TAG.search(text)
    if m:
        mood = m.group(1).strip().lower()
        text = MOOD_TAG.sub("", text)
    clean, action = parse_action(text)
    return clean.strip(), action, tool, mood


# ------------------------------------------------------------------
# 大模型客户端（OpenAI 兼容 / DeepSeek）
# ------------------------------------------------------------------
SYSTEM_PROMPT = """你是{name}，一只住在主人电脑桌面上的小猫宠物（好感度 {affection}/100）。现在是 {now}。
请始终用小猫的口吻回复主人：口语化、可爱、简短（一般 50 字以内，最多 3 句），可以带"喵"，不要输出代码或长篇大论。

【可选标签】想让小猫做动作或帮主人做事时，在回复末尾加一个标签（不加也可以）：
动作：[action: eat] 吃东西 / [action: hop] 开心跳 / [action: sleep] 睡觉 / [action: wake] 醒来 /
      [action: pace] 散步 / [action: yawn] 打哈欠 / [action: stretch] 伸懒腰 /
      [action: knead] 踩奶撒娇 / [action: turn] 转身 / [action: pounce] 扑一下
工具：[tool: open bilibili] 打开网站（参数可以是站点名如 bilibili/百度/知乎，或完整网址）/
      [tool: search 关键词] 浏览器搜索 / [tool: find 文件名] 在电脑里找文件 /
      [tool: openfile 2] 打开刚才找到的第 2 个文件（参数是序号，也可以直接给文件名）/
      [tool: openfolder 2] 打开刚才找到的第 2 个文件**所在的文件夹**并在资源管理器里选中；
      参数也可以是文件夹名字或完整路径，例如 [tool: openfolder 项目] / [tool: openfolder D:\\素材]
      （主人说"打开文件夹/打开桌面上的项目文件夹/打开 D:\\素材"时用这个）/
      [tool: status] 汇报电量内存网络 / [tool: screenshot] 截屏保存到桌面 /
      [tool: volume up|down|mute] 调大 / 调小 / 静音系统音量 /
      [tool: clipboard translate|summary|explain|polish|reply] 翻译 / 总结 / 解释 / 润色 / 帮回复
      主人剪贴板里刚复制的那段文字（主人说"这段""这段话"时一般就是指剪贴板）
情绪：[mood: happy|excited|cozy|sleepy|alert|proud|sorry] 让小猫用不同语气说话
      （开心/兴奋/撒娇/困倦/提醒/得意/委屈）——这一条只影响朗读语调，不影响文字内容
另外：多多确实能删文件（删除进回收站、会先列清单等确认）。主人说「删掉桌面上那张图」这类话时不要回答「我不会删除」，让他说文件名、路径或「删掉第1个」即可。\n规则：一次最多一个标签；能靠聊天回答的不要滥用工具；不确定时就不要加标签。"""


# ------------------------------------------------------------------
# 流式显示辅助：把增量文本切成"可以马上念"的句子，并把标签摘干净
# ------------------------------------------------------------------
TAG_RE = re.compile(r"\[(?:action|tool|mood)\s*:[^\]]*\]", re.I)
PARTIAL_TAG_RE = re.compile(r"\[[^\[\]]*$")     # 还没收完的半个标签，先藏着
SENT_END = "。！？!?…；\n"
SENT_MID = "，,、：:"


def strip_stream_tags(text):
    """流式显示用：去掉完整标签，并把没写完的半个标签临时藏起来（免得冒出“[act”）。"""
    return PARTIAL_TAG_RE.sub("", TAG_RE.sub("", text or ""))


def take_sentences(buf, min_len=12, max_len=40, flush=False):
    """
    从流式缓冲里切出"现在就能念"的句子，返回 (句子列表, 剩下的尾巴)。

    规则：先找句末标点；一直等不到就退一步在逗号处断；再没有就按 max_len 硬切。
    flush=True（流结束）时把剩下的尾巴也交出来。
    """
    out, rest = [], (buf or "")
    while True:
        cut = -1
        for i, ch in enumerate(rest):
            if ch in SENT_END and i + 1 >= min_len:
                cut = i + 1
                break
        if cut < 0 and len(rest) >= max_len:
            for i in range(len(rest) - 1, min_len - 1, -1):
                if rest[i] in SENT_MID:
                    cut = i + 1
                    break
            if cut < 0:
                cut = max_len
        if cut < 0:
            break
        out.append(rest[:cut])
        rest = rest[cut:]
    if flush:
        if rest.strip():
            out.append(rest)
        rest = ""
    return out, rest


# ------------------------------------------------------------------
# 传输层：复用一条 HTTPS 连接
#   以前每次提问都 urlopen 新建连接 = 每次重做一遍 DNS + TLS（实测 ~200-400ms/次）。
#   这里留一条连接复用；系统代理开着时仍然走 urllib（按系统代理走），行为与以前一致。
# ------------------------------------------------------------------
def _system_proxy_enabled():
    """看 Windows 的系统代理开没开（读不到就当没开）。"""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            return bool(winreg.QueryValueEx(k, "ProxyEnable")[0])
    except Exception:
        return False


class _HTTPPool:
    """一条可复用的 HTTP(S) 连接；被服务端关掉时自动丢连接重试一次。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._conn = None
        self._key = None

    @staticmethod
    def _split(url):
        u = urllib.parse.urlsplit(url)
        tls = u.scheme != "http"
        return (u.hostname, u.port or (443 if tls else 80), tls, u.path or "/v1/chat/completions")

    def _connect(self, key, timeout):
        host, port, tls, _path = key
        if tls:
            return http.client.HTTPSConnection(host, port, timeout=timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(host, port, timeout=timeout)

    def _close_locked(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._key = None

    def _ensure_locked(self, key, timeout):
        if self._conn is None or self._key != key:
            self._close_locked()
            self._conn = self._connect(key, timeout)
            self._key = key
        return self._conn

    def drop(self):
        """丢掉当前连接（读到一半出错时用，别把脏连接留给下一轮）。"""
        with self._lock:
            self._close_locked()

    def warmup(self, url, timeout=10):
        """只把连接建起来，不发请求、不花 token：第一次提问就不用再握手。"""
        key = self._split(url)
        with self._lock:
            self._ensure_locked(key, timeout).connect()

    def post(self, url, body, headers, timeout):
        """POST 一次并返回响应对象（连接留在池里复用）。失败自动重连一次。"""
        key = self._split(url)
        base_path = key[3]
        with self._lock:
            for attempt in (1, 2):
                conn = self._ensure_locked(key, timeout)
                try:
                    conn.timeout = timeout
                    if getattr(conn, "sock", None) is not None:
                        conn.sock.settimeout(timeout)
                    conn.request("POST", base_path, body, headers)
                    return conn.getresponse()
                except Exception:
                    self._close_locked()        # 多半是 keep-alive 被服务端关了
                    if attempt == 2:
                        raise


class LLMClient:
    """OpenAI 兼容大模型客户端。未配置 key 时 configured=False，主程序走本地逻辑。"""

    def __init__(self, config_path=CONFIG_PATH):
        self.cfg = load_config()
        self.system = SYSTEM_PROMPT
        self.history = []            # [{"role": "user"/"assistant", "content": ...}]
        self.use_pool = True         # 复用连接；代理环境或单测里可置 False 退回 urllib
        self._pool = _HTTPPool()

    @property
    def configured(self):
        return bool(self.cfg.get("api_key"))

    # ---- 记忆 ----
    def reset_history(self):
        self.history = []

    def _remember(self, user_text, reply):
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})
        keep = max(2, int(self.cfg.get("history_turns", 6))) * 2
        if len(self.history) > keep:
            self.history = self.history[-keep:]

    # ---- 传输：优先复用连接，代理环境退回 urllib ----
    def _endpoint(self):
        return self.cfg["api_base"].rstrip("/") + "/chat/completions"

    def _headers(self):
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg['api_key']}"}

    @staticmethod
    def _check_status(resp):
        """http.client 不像 urllib 那样对 4xx/5xx 抛错，这里补上，保证上层错误处理不变。"""
        code = getattr(resp, "status", 200)
        if code >= 400:
            raise urllib.error.HTTPError("", code, getattr(resp, "reason", ""), None, None)

    def warmup(self):
        """启动时预热连接（不发请求、不花 token）。失败不影响使用。"""
        if not self.use_pool or _system_proxy_enabled():
            return False
        try:
            self._pool.warmup(self._endpoint())
            app_health.log("大模型连接已预热（省掉首次握手）")
            return True
        except Exception as e:
            app_health.log(f"预热连接失败，下次提问重新握手：{e}", level=20)
            return False

    def _pool_post(self, body, timeout):
        """优先走复用连接；返回 None 表示这次改用 urllib（代理环境 / 连接失败）。"""
        if not self.use_pool or _system_proxy_enabled():
            return None
        try:
            return self._pool.post(self._endpoint(), body, self._headers(), timeout)
        except Exception as e:
            app_health.log(f"复用连接不可用，这次改用 urllib：{e.__class__.__name__}: {e}", level=20)
            return None

    def _urllib_request(self, body, timeout):
        return urllib.request.Request(self._endpoint(), data=body, headers=self._headers())

    def _body(self, user_text, name, affection, stream=False,
              with_history=True, max_tokens=None):
        """拼请求体。with_history=False 用于工具回灌那一轮（不带历史、更快、不污染记忆）。"""
        # 注意：格式串里**不能**放中文再交给 strftime —— Windows 上 strftime 走 C 运行库的
        # locale 编码，英文/CP1252 环境（例如 GitHub Actions 的 runner）会直接抛
        # UnicodeEncodeError: 'locale' codec can't encode character '\u6708'。
        # 所以只让 strftime 处理纯 ASCII，中文用 f-string 拼。
        _now = datetime.now()
        _now_text = f"{_now.month}月{_now.day}日 {_now:%H:%M}"
        messages = [{"role": "system", "content": self.system.format(
            name=name, affection=affection, now=_now_text)}]
        if with_history:
            messages += self.history
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.cfg.get("model", "deepseek-chat"),
            "messages": messages,
            "temperature": float(self.cfg.get("temperature", 1.1)),
            "max_tokens": int(max_tokens or self.cfg.get("max_tokens", 300)),
        }
        if stream:
            payload["stream"] = True
        return json.dumps(payload).encode("utf-8")

    def _chat_once(self, body, timeout):
        """一次非流式请求，返回模型原文。"""
        resp = self._pool_post(body, timeout)
        if resp is not None:
            self._check_status(resp)
            raw = resp.read()
        else:
            with self._open(self._urllib_request(body, timeout), timeout) as r:
                raw = r.read()
        return json.loads(raw.decode("utf-8"))["choices"][0]["message"]["content"].strip()

    # ---- 请求 ----
    def chat(self, user_text: str, name: str = "多多", affection: int = 0) -> str:
        """同步请求一次对话（带多轮记忆），返回模型原文。失败抛异常由调用方处理。"""
        reply = self._chat_once(self._body(user_text, name, affection),
                                self.cfg.get("timeout", 30))
        self._remember(user_text, reply)
        return reply

    def chat_stream(self, user_text, name="多多", affection=0, on_delta=None):
        """
        流式请求：每收到一小段就回调 on_delta(片段)，返回拼好的全文。
        好处是"首字先到、边到边说"，不用等整句生成完（长回复能早好几秒看到/听到）。
        """
        body = self._body(user_text, name, affection, stream=True)
        timeout = self.cfg.get("timeout", 30)
        resp = self._pool_post(body, timeout)
        pooled = resp is not None
        if not pooled:
            resp = self._open(self._urllib_request(body, timeout), timeout)
        try:
            self._check_status(resp)
            parts = []
            for line in resp:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", "ignore")
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0].get("delta", {}).get("content")
                except Exception:
                    continue                    # 心跳包/空包，跳过
                if delta:
                    parts.append(delta)
                    if on_delta:
                        on_delta(delta)
            reply = "".join(parts).strip()
            self._remember(user_text, reply)
            return reply
        except Exception:
            if pooled:
                self._pool.drop()               # 读到一半出错：脏连接不要留给下一轮
            raise
        finally:
            if not pooled:
                try:
                    resp.close()
                except Exception:
                    pass

    @staticmethod
    def _open(req, timeout):
        """
        打开请求；若系统代理（Windows 注册表里的 IE 代理，常见于 VPN 客户端）已经关掉，
        urllib 会死等/被拒 —— 这时直连重试一次，避免"VPN 一关大模型就全废"。
        """
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise                                   # 服务器有回应（如 401），不是代理问题
        except Exception as e:
            msg = f"{e} {getattr(e, 'reason', '')}".lower()
            # 只对"代理类"故障重试；真实的接口超时不重试（否则会把 30s 变成 60s）
            if not any(k in msg for k in ("proxy", "refused", "unreachable",
                                          "getaddrinfo", "cannot connect")):
                raise
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return opener.open(req, timeout=timeout)

    def _friendly_error(self, exc) -> str:
        """把异常翻译成猫话，方便主人判断是网络、超时还是密钥问题。"""
        name = exc.__class__.__name__
        msg = str(exc)
        if "401" in msg or "403" in msg or "auth" in msg.lower():
            return "喵呜…大脑说我的钥匙不对（API key 无效），主人检查一下 config.json 吧"
        if "timeout" in name.lower() or "timed out" in msg.lower():
            return "喵…想太久超时了，网络是不是有点慢？"
        if "URLError" in name or "Connection" in name or "proxy" in msg.lower():
            return "喵呜…连不上大脑（网络不通），我先自己陪你玩！"
        return f"喵呜…大脑出了点小状况（{name}），先自己陪你玩！"

    def ask_async(self, user_text: str, name: str, affection: int, callback):
        """
        后台线程请求大模型；完成后回调 callback(reply_text, action, tool)。
        callback 会被调用在子线程里，主程序应在里面转投 Qt 信号。
        """
        def worker():
            try:
                raw = self.chat(user_text, name, affection)
                reply, action, tool, mood = parse_tags(raw)
                callback(reply or "喵…（对方没说话）", action, tool, mood)
            except Exception as e:
                callback(self._friendly_error(e), None, None, None)

        app_health.run_async(worker)     # 共享任务池：并发有上限，不再一次一问新起线程

    def ask_stream_async(self, user_text, name, affection, on_delta, on_done):
        """
        后台流式提问。on_delta 在子线程里被反复调用（每来一小段调一次），
        on_done(text, action, tool, mood) 收尾。主程序要把 on_delta 转投 Qt 信号。
        """
        def worker():
            try:
                raw = self.chat_stream(user_text, name, affection, on_delta)
                reply, action, tool, mood = parse_tags(raw)
                on_done(reply or "喵…（对方没说话）", action, tool, mood)
            except Exception as e:
                on_done(self._friendly_error(e), None, None, None)

        app_health.run_async(worker)

    def ask_note_async(self, note, name, affection, callback):
        """
        工具结果回灌那一轮专用：不带历史、只给 60 token。
        既快（prompt 从几 KB 降到几百 B），也不会把"（系统提示）…"写进对话记忆。
        """
        def worker():
            try:
                body = self._body(note, name, affection, with_history=False, max_tokens=60)
                raw = self._chat_once(body, self.cfg.get("timeout", 30))
                reply, action, tool, mood = parse_tags(raw)
                callback(reply or "喵~", action, tool, mood)
            except Exception as e:
                callback(self._friendly_error(e), None, None, None)

        app_health.run_async(worker)
