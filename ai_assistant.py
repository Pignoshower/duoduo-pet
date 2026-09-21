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
  也可用环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY 提供密钥。
"""
import os
import re
import json
import threading
import urllib.request
from datetime import datetime

# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------
CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "api_base": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "timeout": 30,
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
        except Exception:
            pass
    # 环境变量优先
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if key:
        cfg["api_key"] = key
    return cfg


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
      [tool: status] 汇报电量内存网络 / [tool: screenshot] 截屏保存到桌面 /
      [tool: volume up|down|mute] 调大 / 调小 / 静音系统音量 /
      [tool: clipboard translate|summary|explain|polish|reply] 翻译 / 总结 / 解释 / 润色 / 帮回复
      主人剪贴板里刚复制的那段文字（主人说"这段""这段话"时一般就是指剪贴板）
情绪：[mood: happy|excited|cozy|sleepy|alert|proud|sorry] 让小猫用不同语气说话
      （开心/兴奋/撒娇/困倦/提醒/得意/委屈）——这一条只影响朗读语调，不影响文字内容
另外：多多确实能删文件（删除进回收站、会先列清单等确认）。主人说「删掉桌面上那张图」这类话时不要回答「我不会删除」，让他说文件名、路径或「删掉第1个」即可。\n规则：一次最多一个标签；能靠聊天回答的不要滥用工具；不确定时就不要加标签。"""


class LLMClient:
    """OpenAI 兼容大模型客户端。未配置 key 时 configured=False，主程序走本地逻辑。"""

    def __init__(self, config_path=CONFIG_PATH):
        self.cfg = load_config()
        self.system = SYSTEM_PROMPT
        self.history = []            # [{"role": "user"/"assistant", "content": ...}]

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

    # ---- 请求 ----
    def chat(self, user_text: str, name: str = "多多", affection: int = 0) -> str:
        """同步请求一次对话（带多轮记忆），返回模型原文。失败抛异常由调用方处理。"""
        messages = [{"role": "system", "content": self.system.format(
            name=name, affection=affection, now=datetime.now().strftime("%m月%d日 %H:%M"))}]
        messages += self.history
        messages.append({"role": "user", "content": user_text})
        body = json.dumps({
            "model": self.cfg.get("model", "deepseek-chat"),
            "messages": messages,
            "temperature": float(self.cfg.get("temperature", 1.1)),
            "max_tokens": int(self.cfg.get("max_tokens", 300)),
        }).encode("utf-8")
        req = urllib.request.Request(
            self.cfg["api_base"].rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg['api_key']}",
            },
        )
        with self._open(req, self.cfg.get("timeout", 30)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        reply = data["choices"][0]["message"]["content"].strip()
        self._remember(user_text, reply)
        return reply

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

        threading.Thread(target=worker, daemon=True).start()
