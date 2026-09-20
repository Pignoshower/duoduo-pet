# -*- coding: utf-8 -*-
"""
多多.py —— 桌宠"多多"主程序（满血加强版 v5）
====================================================
动作素材（frames_opt/，共 10 个状态，全部 42ms/帧 = 原视频 24fps 节奏）
  idle    坐姿发呆（呼吸/眨眼）      walk    原地走路（窗口平移实现位移）
  eat     低头吃东西                sleep   侧躺睡觉（呼吸循环）
  wake    睡醒起身（一次性）          knead   踩奶撒娇
  yawn    打哈欠（一次性）            stretch 伸懒腰（一次性）
  turn    转身（一次性）              pounce  前扑（一次性）

关键设计
  · 帧库按需加载 + 限量 LRU 缓存（FrameStore）：2x 高清素材下内存也始终可控
  · 对齐由 prepare_frames.py 完成：躯干带水平锁定 + 脚底接地线，稳定无抖动
  · 边缘质量：预乘 alpha 缩放 + 透明度吸附 + 脸上塌坑用原始像素修补
  · 无残影：每帧显式清屏（半透明窗口）、移动与重绘同一定时器、不使用溶解过渡
  · 用户指令优先：聊天/菜单触发的动作会打断随机行为并给出气泡反馈

系统能力（见 pet_tools.py）
  · 语音播报 TTS（可开关）· 定时提醒 / 番茄钟 · 剪贴板朗读与保存
  · 一键截图到桌面 · 系统状态（电量/内存/CPU/网络）· 全局快捷键 Ctrl+Alt+D
  · 鼠标跟随注视 · 拖拽到屏幕边缘自动吸附
"""
import sys
import os
import re
import json
import math
import random
import shutil
import subprocess
import webbrowser
import time
import threading
from collections import OrderedDict
from functools import partial
from urllib.parse import quote
from datetime import datetime

import ai_assistant as ai
import pet_tools as tools
import app_health

MAX_TOOL_ROUNDS = 1      # 工具结果回灌给大模型的追加轮数上限（防死循环）

from PyQt6.QtWidgets import (QApplication, QWidget, QMenu, QLabel,
                             QVBoxLayout, QFrame, QLineEdit, QPushButton, QHBoxLayout,
                             QSystemTrayIcon, QGraphicsOpacityEffect, QScrollArea)
from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer, pyqtSignal, QPropertyAnimation
from PyQt6.QtGui import (QPixmap, QAction, QFont, QPainter, QColor, QPainterPath, QIcon,
                         QMouseEvent, QPen, QImage)

# =====================================================================
# [模块 1 & 1.5] 持久化与帧素材库
# =====================================================================
class ConfigManager:
    FILE_PATH = 'pet_data.json'

    @classmethod
    def load_data(cls):
        default_data = {
            "name": "多多",
            "affection": 50,
            "last_pos_x": -1,
            "last_pos_y": -1,
            "facing": 1,
            "voice": 0,          # 语音播报开关（0 关 / 1 开），默认关闭
        }
        if os.path.exists(cls.FILE_PATH):
            try:
                with open(cls.FILE_PATH, 'r', encoding='utf-8') as f:
                    default_data.update(json.load(f))
            except Exception as e:
                print(f"读取存档失败，使用默认设置: {e}")
        return default_data

    @classmethod
    def save_data(cls, data):
        try:
            with open(cls.FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存存档失败: {e}")

def missing_assets_message(root=None):
    """
    检查帧素材是否存在；缺了就返回一段"怎么办"的说明（存在则返回 None）。
    素材体积大（约 70MB）不进 git 仓库，所以 clone 下来的人必须先准备素材，
    否则程序完全起不来。这里给它一个明确的指引，而不是丢一个看不懂的报错。
    """
    root = root or app_health.app_dir()
    for cand in FRAME_CANDIDATES:
        d = os.path.join(root, cand)
        if os.path.isfile(os.path.join(d, "meta.json")) and \
                any(os.path.isdir(os.path.join(d, s)) for s in DEFAULT_TIMINGS):
            return None
    return (
        "缺少动画素材，多多起不来（frames_opt/ 没找到）。\n\n"
        "两种准备方式，任选一种：\n\n"
        "① 下载现成素材（最快）\n"
        f"   到项目的 Releases 页面下载 frames_opt.zip，解压到：\n   {root}\n"
        "   解压后目录结构应为：frames_opt/{idle,walk,eat,...} 与 frames_opt/meta.json\n\n"
        "② 用自己的视频生成\n"
        "   把动画视频放进 视频素材/（命名见 视频生成提示词.md），然后运行：\n"
        "     pip install opencv-python rembg pillow numpy\n"
        "     python pipeline.py\n\n"
        "准备完再启动即可。详细说明见 README.md 与 使用说明.md。"
    )


FRAME_CANDIDATES = ["frames_opt", "frames"]
DEFAULT_TIMINGS = {s: 42 for s in ("idle", "walk", "eat", "sleep", "wake",
                                   "yawn", "stretch", "turn", "pounce", "knead")}

class FrameStore:
    """
    帧库"按需加载 + 限量缓存"容器：
      · 只保存每个状态的文件名清单，真正用到时才读 PNG（10 个状态也不吃内存）；
      · 最多同时驻留 MAX_LOADED_STATES 个状态（LRU 淘汰），
        这样即使按 2x 高清烘焙，运行内存也始终可控。
    对外用法与 dict 一致：frames[state] / frames.get(state) / keys() / items() / in。
    """

    MAX_LOADED_STATES = 3

    def __init__(self, base_dir, files_by_state):
        self._base = base_dir
        self._files = files_by_state          # state -> [文件名]
        self._cache = OrderedDict()           # LRU 缓存

    def __contains__(self, state):
        return state in self._files

    def __iter__(self):
        return iter(self._files)

    def __len__(self):
        return len(self._files)

    def keys(self):
        return self._files.keys()

    def items(self):
        return [(st, self[st]) for st in self._files]

    def get(self, state, default=None):
        return self[state] if state in self._files else default

    def __getitem__(self, state):
        if state not in self._files:
            raise KeyError(state)
        if state in self._cache:
            self._cache.move_to_end(state)
            return self._cache[state]
        d = os.path.join(self._base, state)
        pixs = [QPixmap(os.path.join(d, n)) for n in self._files[state]]
        self._cache[state] = [p for p in pixs if not p.isNull()]
        while len(self._cache) > self.MAX_LOADED_STATES:
            self._cache.popitem(last=False)   # 释放最久未使用的状态
        return self._cache[state]


class FrameLibrary:
    CORE_STATES = ["idle", "eat", "sleep"]

    def __init__(self):
        self.logical_w = 400
        self.logical_h = 290
        self.anchor_y = 278
        self.intervals = dict(DEFAULT_TIMINGS)
        self.frames = FrameStore(".", {})
        self._load()

    def _load(self):
        base = next((c for c in FRAME_CANDIDATES if os.path.isdir(c)), None)
        if not base:
            print("【提示】未找到帧目录。")
            return

        meta = None
        mp = os.path.join(base, "meta.json")
        if os.path.exists(mp):
            try:
                with open(mp, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
            except Exception:
                pass

        if meta:
            self.logical_w = int(meta.get("logical_w", self.logical_w))
            self.logical_h = int(meta.get("logical_h", self.logical_h))
            self.anchor_y = int(meta.get("anchor", [self.logical_w // 2, self.logical_h])[1])

        extra_states = [d for d in sorted(os.listdir(base)) if os.path.isdir(os.path.join(base, d))
                        and d not in self.CORE_STATES and not d.startswith((".", "_"))]
        states_to_load = self.CORE_STATES + extra_states
        tim = (meta or {}).get("state_timings") or {}
        for k in states_to_load:
            self.intervals[k] = int(tim.get(k, DEFAULT_TIMINGS.get(k, 130)))

        # 只登记文件名清单，不加载图片
        files_by_state = {}
        for st in states_to_load:
            d = os.path.join(base, st)
            if not os.path.isdir(d):
                continue
            names = sorted(n for n in os.listdir(d) if n.lower().endswith(".png"))
            if names:
                files_by_state[st] = names
        self.frames = FrameStore(base, files_by_state)

# =====================================================================
# [模块 2] AI 大脑模块
# =====================================================================
class AIBrain:
    def __init__(self, pet_instance):
        self.pet = pet_instance
        self.llm = ai.LLMClient()

    def _handle_local(self, text):
        pet = self.pet
        low = text.lower()

        if "时间" in low or "几点" in low: return f"报告主人！现在时间是：{ai.now_text()} 喵~", None
        if "日期" in low or "几号" in low or "周几" in low: return f"今天是 {ai.now_text()} 喵~", None

        # ---------- 系统能力 ----------
        # 定时提醒 / 番茄钟 / 日程（每天、每周、工作日、几点整）
        sched = tools.parse_schedule(text)
        if sched:
            return None, ("schedule", sched)
        remind = tools.parse_reminder(text)
        if remind:
            secs, msg = remind
            return None, ("remind", secs, msg)
        if any(k in low for k in ("取消提醒", "别提醒了", "取消闹钟")):
            return None, "cancel_remind"

        # 语音播报
        if any(k in low for k in ("开启语音", "打开语音", "说话吧", "开口说话", "开始说话")):
            return None, ("voice", True)
        if any(k in low for k in ("关闭语音", "闭嘴", "别说话", "安静点")):
            return None, ("voice", False)

        # 音量（发系统媒体键）
        if any(w in low for w in tools.VOLUME_WORDS):
            vol = tools.volume_reply(text)
            if vol:
                return vol[1], ("volume", vol[0])

        # 语音音色：试听 / 换一个 / 情绪演示
        if any(k in low for k in ("忘掉这个文件", "忘掉那个文件", "忘掉文件", "不用管这个文件",
                                  "不用记这个文件", "清掉文件上下文")):
            had = getattr(self.pet, "_file_context", None)
            self.pet._file_context = None
            return ("好啦，我把「%s」忘掉了喵~" % had["name"]) if had else "我本来就没什么文件记着呀喵~", None
        if any(k in low for k in ("看我拖的文件", "那个文件说了什么", "刚才那个文件叫什么")):
            ctx = getattr(self.pet, "_file_context", None)
            return (f"我记得的是「{ctx['name']}」，还能接着问我它的细节喵~" if ctx
                    else "喵？主人还没拖文件给我呢"), None
        if any(k in low for k in ("全屏避让", "全屏时躲起来", "全屏自动隐藏", "游戏模式")):
            if any(k in low for k in ("关闭", "关掉", "别", "不要", "取消")):
                return None, ("avoid_fs", False)
            return None, ("avoid_fs", True)
        if any(k in low for k in ("看家模式", "专注模式", "值守模式", "看家")):
            if any(k in low for k in ("关闭", "关掉", "别", "不要", "退出")):
                return None, ("focus", False)
            return None, ("focus", True)
        if any(k in low for k in ("开机自启", "开机启动", "自启动")):
            if any(k in low for k in ("关闭", "关掉", "别", "不要", "取消")):
                return None, ("autostart", False)
            return None, ("autostart", True)
        if any(k in low for k in ("情绪演示", "演示情绪", "演示一下情绪", "情绪语音", "各种语气")):
            return None, "mood_showcase"
        if any(k in low for k in ("试听", "听听你的声音", "你的声音", "什么声音", "用的什么音色")):
            return None, "preview_voice"
        if any(k in low for k in ("换个声音", "换个音色", "换一个声音", "下一个音色", "变声")):
            return None, "next_voice"

        # 剪贴板历史里的第 N 条（要放在"打开第 N 个"之前，否则会被抢走）
        _clip_item = tools.parse_clipboard_item(text)
        if _clip_item and any(k in low for k in ("翻译", "总结", "摘要", "概括", "解释", "润色",
                                                 "回复", "回信", "念", "读", "存", "剪贴板")):
            return None, ("clip_item", _clip_item[0], _clip_item[1])
        # 打开上次搜到的文件 / 所在文件夹（可指定程序："用记事本打开第1个"）
        # 只在"明确指代上一次结果"时才拦（打开它 / 第2个 / 打开这个文件 / 用XX打开…），
        # 免得把"帮我打开B站"这类正常指令抢走。
        open_idx = ai.parse_open_index(text)
        open_tgt = ai.parse_open_target(text)
        wants_prev = (open_idx is not None
                      or any(k in low for k in ("打开它", "打开那个", "开一下它",
                                                "打开这个文件", "打开这个文件夹"))
                      or open_tgt is not None)
        if any(k in low for k in ("文件夹", "目录", "所在位置", "在哪个文件夹")) and wants_prev:
            return None, ("open_found_folder", open_idx or 1)
        if wants_prev:
            if open_tgt is not None:
                return ("好呀，用" + open_tgt[0] + "打开喵~" if open_tgt[1] != "@picker"
                        else "好，我打开选择框，主人自己挑喵~"), \
                       ("open_found", open_idx or 1, open_tgt[1])
            return None, ("open_found", open_idx or 1)

        # 截图
        if any(k in low for k in ("截图", "截个屏", "截屏")):
            return None, "screenshot"

        # 剪贴板（读 / 存 / 交给大模型处理）
        clip_modes = (
            (("翻译", "译成", "英文怎么说"), "translate"),
            (("总结", "摘要", "概括", "要点"), "summary"),
            (("解释", "什么意思", "看懂"), "explain"),
            (("润色", "改通顺", "改写"), "polish"),
            (("回复", "回信", "怎么回"), "reply"),
        )
        # 剪贴板历史
        if any(k in low for k in ("剪贴板历史", "复制历史", "最近复制的")):
            return None, "clip_history"
        if any(k in low for k in ("剪贴板", "剪贴", "复制的内容", "复制的那")):
            for keys, mode in clip_modes:
                if any(k in low for k in keys):
                    return None, ("clipboard", mode)
            if any(k in low for k in ("存", "保存", "记下来")):
                return None, "clip_save"
            return None, "clip_read"
        # "翻译这段话 / 总结一下这段文字" —— 没写"剪贴板"也认，默认读懂剪贴板
        if any(k in low for k in ("这段", "这段话", "这段文字", "这句", "这篇", "选中")):
            if any(k in low for k in ("翻译", "译成")):
                return None, ("clipboard", "translate")
            if any(k in low for k in ("总结", "摘要", "概括", "要点")):
                return None, ("clipboard", "summary")
            if any(k in low for k in ("解释", "什么意思", "看懂")):
                return None, ("clipboard", "explain")
            if any(k in low for k in ("润色", "改通顺", "改写")):
                return None, ("clipboard", "polish")
            if any(k in low for k in ("回复", "回信", "怎么回")):
                return None, ("clipboard", "reply")

        # 系统状态
        if any(k in low for k in ("电量", "电池", "内存", "cpu", "系统状态", "联网", "网络")):
            return None, "status"

        if any(k in low for k in ("找文件", "帮我找", "查找文件", "找一下")):
            for k in ("找文件", "帮我找", "查找文件", "找一下"):
                if k in low:
                    query = low.split(k, 1)[1].strip(" ，。的了！？")
                    break
            found = ai.find_files(query)
            if not found:
                return "喵呜…翻遍桌面和文档都没找到，换个关键词试试？", None
            self.pet.remember_found(found)
            listing = "\n".join(f"{i+1}. {ai.short_path(p)}" for i, p in enumerate(found[:3]))
            return ("喵~ 找到这些：\n" + listing
                    + "\n（“打开第1个”／“换个方式打开第1个”／“用记事本打开第1个”）"), None

        cmd = ai.resolve_app(text)
        if cmd:
            os.startfile(cmd)
            return "遵命！已经帮主人打开啦~", "hop"

        url = ai.resolve_site(text)
        if url:
            webbrowser.open(url)
            return "咻！已经帮主人打开啦！", "eat"
            
        query = ai.search_query(text)
        if query:
            webbrowser.open(f"https://www.baidu.com/s?wd={quote(query)}")
            return f"搜好啦：{query}", None

        if "散步" in low or "走走" in low or "溜达" in low:
            if pet.pacing: return "已经在走啦，走不动了喵~", None
            return "好呀好呀，陪主人走一圈！", "pace"
        if "停" in low or "站住" in low: return "喵？好，那我站住啦~", "stop"
        if "睡觉" in low or "休息" in low: return "哈欠~ 那多多先睡一会儿…Zzz", "sleep"
        if "醒来" in low or "起床" in low:
            if pet.state == "sleep": return "喵呜…被叫醒了！", "wake"
            return "我没在睡觉呀，精神着呢~", None
        if "哈欠" in low or "困" in low: return "哈欠~~~~ 好困喵…", "yawn"
        if "伸懒腰" in low or "拉伸" in low: return "嗯~~~ 伸个懒腰！", "stretch"
        if "踩奶" in low or "撒娇" in low or "按摩" in low: return "呼噜呼噜…踩踩踩~", "knead"
        if "扑" in low or "玩" in low: return "嗷呜！抓住你啦！", "pounce"
        if "转身" in low or "转个圈" in low: return "看我的漂亮转身！", "turn"
        if "喂" in low or "鱼干" in low: return None, "feed"
        if "饿" in low: return "肚肚叫了，快说“喂我小鱼干”！", None
        
        if any(k in low for k in ("乖", "可爱", "摸摸", "亲", "喜欢")):
            if pet.affection < 100: pet.affection += 3
            return "呼噜呼噜… 最喜欢主人啦！（好感+）", "hop"
        if any(k in low for k in ("傻", "笨", "坏")):
            pet.affection = max(0, pet.affection - 10)
            return "喵呜… 主人坏… （好感-）", "sleep"
        return None, None

    def process_input(self, text):
        """本地优先：命中本地意图（哪怕只有动作没有台词）就不再去问大模型。"""
        reply, action = self._handle_local(text)
        if reply is not None or action is not None:
            return reply, action, False
        if self.llm.configured:
            ask = text
            ctx = getattr(self.pet, "_file_context", None)
            if ctx and ctx.get("text"):
                # 把"当前参考文件"带进这次提问，主人就能追问细节；说"忘掉这个文件"可清掉
                ask = (f"（参考文件《{ctx['name']}》的内容如下，可能和主人这句话相关）\n"
                       f"{ctx['text'][:3000]}\n\n主人说：{text}")
            self.llm.ask_async(ask, self.pet.cat_name, self.pet.affection,
                               lambda t, a, tool, mood=None: self.pet.llm_reply.emit(t, a or "", tool, mood or ""))
            return None, None, True
        return f"歪头~ 喵？虽然不懂主人的意思，但 {self.pet.cat_name} 会一直陪着你。", None, False

# =====================================================================
# [模块 3] UI 视觉系统：带淡入淡出的气泡与输入框
# =====================================================================
class ChatBubble(QWidget):
    link_clicked = pyqtSignal(str)      # 点气泡里的链接：file:///… 或 duoduo://…

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)

        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(250)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.frame = QFrame(self)
        self.frame.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 245);
                border: 2px solid #ffb6c1;
                border-radius: 15px;
            }
        """)
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(6, 6, 6, 6)

        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setFont(QFont("Microsoft YaHei", 10))
        self.label.setStyleSheet("color: #333333; padding: 4px; border: none; background: transparent;")
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.label.setOpenExternalLinks(False)          # 链接交给我们自己处理
        self.label.linkActivated.connect(lambda url: self.link_clicked.emit(url))

        # 长文本用滚动区兜住：短内容不出现滚动条，长内容也不会被屏幕裁掉
        self.scroll = QScrollArea(self.frame)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")

        frame_layout.addWidget(self.scroll)
        layout.addWidget(self.frame)

        self.hide_timer = QTimer(self)
        self.hide_timer.timeout.connect(self.fade_out)
        self.fade_anim.finished.connect(self._check_hide)   # 只连一次，避免重复触发
        self._last_duration = 4000

    # ---- 自适应尺寸：按内容长度选宽度，超高则滚动 ----
    def _fit(self, text_len):
        screen = QApplication.primaryScreen().availableGeometry()
        max_w = min(380, screen.width() - 60)
        max_h = int(screen.height() * 0.55)
        if text_len <= 40:
            w = 200
        elif text_len <= 140:
            w = 280
        else:
            w = max_w
        w = min(w, max_w)
        self.label.setFixedWidth(w - 26)
        self.label.adjustSize()
        inner_h = max(28, self.label.sizeHint().height())
        self.scroll.setFixedWidth(w - 18)
        self.scroll.setFixedHeight(min(inner_h, max_h))
        self.adjustSize()

    def show_message(self, text, display_time=4000):
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setText(text)
        self._fit(len(text or ""))
        self._show_fade(display_time)

    def show_rich(self, html, display_time=8000):
        """显示带可点击链接的富文本（搜索结果用）。"""
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setText(html)
        # 富文本要按纯文字量估算宽度（标签不算长度）
        plain_len = len(re.sub(r"<[^>]+>", "", html or ""))
        self._fit(plain_len)
        self._show_fade(display_time)

    def _show_fade(self, display_time):
        self._last_duration = display_time
        self.show()
        self.fade_anim.stop()
        self.fade_anim.setStartValue(self.opacity_effect.opacity())
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.start()
        self.hide_timer.stop()
        self.hide_timer.start(display_time)

    # 鼠标停在气泡上时不消失（方便读完长内容/点链接）
    def enterEvent(self, event):        # noqa: N802 (Qt 命名)
        self.hide_timer.stop()
        event.accept()

    def leaveEvent(self, event):        # noqa: N802 (Qt 命名)
        if self.isVisible():
            self.hide_timer.start(max(4000, self._last_duration))
        event.accept()

    def fade_out(self):
        self.fade_anim.stop()
        self.fade_anim.setStartValue(self.opacity_effect.opacity())
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.start()

    def _check_hide(self):
        if self.opacity_effect.opacity() <= 0.01:
            self.hide()


class CustomInputBox(QWidget):
    message_sent = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(300, 60)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.frame = QFrame(self)
        self.frame.setStyleSheet("""
            QFrame {
                background-color: rgba(240, 248, 255, 240);
                border: 2px solid #87ceeb;
                border-radius: 20px;
            }
        """)
        frame_layout = QHBoxLayout(self.frame)
        frame_layout.setContentsMargins(15, 5, 10, 5)

        self.input_field = QLineEdit(self.frame)
        self.input_field.setPlaceholderText("对小猫说点什么吧...")
        self.input_field.setFont(QFont("Microsoft YaHei", 10))
        self.input_field.setStyleSheet("background: transparent; border: none; color: #333;")
        self.input_field.returnPressed.connect(self.send_and_close)

        self.send_btn = QPushButton("发送", self.frame)
        self.send_btn.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.Bold))
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #87ceeb; color: white;
                border-radius: 12px; padding: 5px 15px; border: none;
            }
            QPushButton:hover { background-color: #5bbde6; }
            QPushButton:pressed { background-color: #3ba2cf; }
        """)
        self.send_btn.clicked.connect(self.send_and_close)

        frame_layout.addWidget(self.input_field)
        frame_layout.addWidget(self.send_btn)
        layout.addWidget(self.frame)

    def show_at(self, pos_x, pos_y):
        self.move(pos_x, pos_y)
        self.input_field.clear()
        self.show()
        self.input_field.setFocus()

    def send_and_close(self):
        text = self.input_field.text().strip()
        if text:
            self.message_sent.emit(text)
        self.hide()

# =====================================================================
# [模块 4] 主应用程序
# =====================================================================
class PetCat(QWidget):
    llm_reply = pyqtSignal(str, str, object, str)   # (文本, 动作, 工具, 情绪)
    
    FX_MS = 30
    WALK_STEP = 3.0
    HOP_HEIGHT = 20
    BREATHE = {"idle": (0.006, 0.34), "eat": (0.003, 0.5), "sleep": (0.007, 0.13), "walk": (0.004, 0.5)}

    def __init__(self):
        super().__init__()
        ai.ensure_config_file()
        self.pet_data = ConfigManager.load_data()
        self.cat_name = self.pet_data['name']
        self.affection = self.pet_data['affection']

        self.brain = AIBrain(self)
        self.lib = FrameLibrary()
        self.animations = self.lib.frames

        self.state = 'idle'
        self.seq = []
        self.idx = 0
        self.frame_mode = 'loop'
        self.on_end = None
        self.facing = 1 if self.pet_data.get('facing', 1) >= 0 else -1

        # 特效与物理强化
        self.hover = 0.0
        self.hop_vy = 0.0
        self.hop_active = False
        self.squash = 1.0        
        self.breathe_phase = 0.0
        self._breath_spd = random.uniform(0.22, 0.42)
        self._breath_amp = random.uniform(0.005, 0.014)
        self._eye_spots = []          # 眼睛位置（眨眼用），启动后自动定位
        self._blink_until = 0.0
        self._next_blink = 0.0
        self.walk_phase = 0.0
        self.particles = []
        
        # 拖拽物理
        self._dragging = False
        self._last_drag_time = 0
        self._drag_speed = 0.0
        self._last_drag_pos = QPointF(0, 0)
        self._dizzy = False
        self._click_times = []

        self.pacing = False
        self._pace_x = 0.0
        self._pace_target = 0
        self._sleep_token = 0
        self._last_sleep_t = 0.0     # 上次睡觉时间（做冷却，避免刚醒又睡）
        self._last_walk_t = 0.0      # 上次散步时间（避免一直在走）

        # 系统能力（语音 / 提醒 / 鼠标跟随）
        self.speaker = tools.SPEAKER
        self.voice_on = bool(self.pet_data.get("voice", 0))
        self._reminders = []         # [(token, 事项)]
        self._remind_token = 0
        self._last_found = []        # 最近一次"找文件"的结果（供"打开第2个"使用）
        self._follow_tick = 0        # 鼠标跟随的节流计数
        self._follow_enabled = True  # 鼠标跟随注视开关（测试里会关掉以保证断言确定）
        self._hotkey_ok = False
        self._hotkeys = {}           # {热键id: 名称}，由 initHotkey 填充

        # 看家/专注模式 + 多步工具链 + 日志
        self.focus_mode = bool(self.pet_data.get("focus_mode", 0))
        self.avoid_fullscreen = bool(self.pet_data.get("avoid_fullscreen", 1))
        self._hidden_by_fullscreen = False
        self._focus_timer = None
        self._fs_timer = None
        self._was_away = False
        self._busy_since = time.time()
        self._last_break_ts = 0.0
        self._tool_rounds = 0        # 大模型工具链轮数（限制 1 轮追加）
        self._last_tool_result = ""
        self._file_context = None    # 拖进来的文件（后续可追问），见 handle_dropped_files
        self.clip_history = tools.ClipboardHistory(10)
        self._clip_seen = ""         # 上一次看到的剪贴板内容（用于轮询）

        self.bubble = ChatBubble()
        self.bubble.link_clicked.connect(self._on_bubble_link)
        self.input_box = CustomInputBox()
        self.input_box.message_sent.connect(self.handle_user_message)
        self.llm_reply.connect(self._on_llm_reply)

        self.initUI()
        self._detect_eyes()          # 定位眼睛（眨眼用）
        self.initTray()
        self.initHotkey()
        self.initTimers()
        # 全屏避让：每 4 秒看一眼前台窗口（玩全屏游戏/看视频时自动躲起来）
        if self.avoid_fullscreen:
            self._fs_timer = QTimer(self)
            self._fs_timer.timeout.connect(self._fullscreen_tick)
            self._fs_timer.start(4000)
        # 剪贴板历史：轮询记录（Qt 没有剪贴板变化事件，只能轮询）
        self._clip_timer = QTimer(self)
        self._clip_timer.timeout.connect(self._poll_clipboard)
        self._clip_timer.start(1800)
        
        self.change_state('idle', 'loop')
        self._schedule_behavior(4000)

    # =============== 【重要修复】增加缺失的 anchor_y 方法 ===============
    def anchor_y(self):
        """返回当前帧库配置的基准脚底 Y 坐标"""
        return float(self.lib.anchor_y)
    # ==================================================================

    def initUI(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)   # 背景由我们每帧自行清屏，杜绝旧像素残留
        self.setAcceptDrops(True)
        self.setFixedSize(self.lib.logical_w, self.lib.logical_h)

        if self.pet_data['last_pos_x'] != -1 and self.pet_data['last_pos_y'] != -1:
            self.move(self.pet_data['last_pos_x'], self.pet_data['last_pos_y'])
        else:
            screen = QApplication.primaryScreen().geometry()
            self.move((screen.width() - self.width()) // 2, int(screen.height() * 0.55))

        self.drag_position = QPoint()
        self._last_mouse_x = 0
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.showContextMenu)
        
    def initTray(self):
        self.tray_icon = QSystemTrayIcon(self)
        try:
            icon_pixmap = self.animations['idle'][0]
            self.tray_icon.setIcon(QIcon(icon_pixmap))
        except:
            pass
        
        tray_menu = QMenu()
        tray_menu.setStyleSheet("QMenu { background-color: white; border-radius: 4px; } QMenu::item { padding: 6px 20px; }")
        
        toggle_action = QAction("显示 / 隐藏小猫", self)
        toggle_action.triggered.connect(self.toggle_visibility)
        tray_menu.addAction(toggle_action)
        
        status_action = QAction("当前状态", self)
        status_action.triggered.connect(self.check_status)
        tray_menu.addAction(status_action)
        
        tray_menu.addSeparator()
        tray_set = QMenu("⚙️ 设置", tray_menu)
        self._autostart_action = QAction(
            ("✅ 开机自启：开" if app_health.autostart_enabled() else "⬜ 开机自启：关"), self)
        self._autostart_action.triggered.connect(self.toggle_autostart)
        tray_set.addAction(self._autostart_action)

        self._focus_action = QAction(
            ("✅ 看家模式：开" if self.focus_mode else "⬜ 看家模式：关"), self)
        self._focus_action.triggered.connect(self.toggle_focus_mode)
        tray_set.addAction(self._focus_action)

        tray_voice = QAction(("🔇 语音播报：关" if not self.voice_on else "🔊 语音播报：开"), self)
        tray_voice.triggered.connect(lambda: self._do_command("voice", None))
        tray_set.addAction(tray_voice)
        tray_menu.addMenu(tray_set)

        tray_menu.addSeparator()
        quit_action = QAction("退出程序", self)
        quit_action.triggered.connect(self.safe_quit)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip(f"{self.cat_name} (桌面宠物)")
        self.tray_icon.show()
        
    def _rebuild_recent_menu(self, menu):
        """"最近找到的文件"子菜单：重建（列表会变，所以每次弹出前刷新）。"""
        menu.clear()
        found = [p for p in getattr(self, "_last_found", []) if os.path.exists(p)]
        if not found:
            empty = QAction("（先跟我说“找文件 XXX”）", self)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        for i, p in enumerate(found, start=1):
            a = QAction(f"{i}. {ai.short_path(p)}", self)
            a.triggered.connect(lambda _=False, idx=i: self.open_found(idx))
            menu.addAction(a)
            b = QAction("    📂 打开位置", self)
            b.triggered.connect(lambda _=False, idx=i: self.open_found_folder(idx))
            menu.addAction(b)

    def toggle_autostart(self):
        """开关开机自启（启动文件夹快捷方式）。"""
        want = not app_health.autostart_enabled()
        ok = app_health.autostart_set(
            want, os.path.join(app_health.app_dir(), "多多.exe")
            if getattr(sys, "frozen", False) else os.path.abspath(__file__))
        if hasattr(self, "_autostart_action"):
            self._autostart_action.setText("✅ 开机自启：开" if app_health.autostart_enabled()
                                           else "⬜ 开机自启：关")
        app_health.log(f"开机自启 -> {want}（成功={ok}）")
        if ok:
            return self.speak("好耶！以后开机我就自己出来啦喵~" if want
                              else "知道啦，以后我不自动出来了", 4000)
        return self.speak("喵…改不动启动文件夹（可能被安全软件拦了）", 5000)

    def toggle_focus_mode(self, enable=None):
        """开关看家/专注模式（托盘菜单与聊天指令共用）。"""
        msg = self.set_focus_mode(enable)
        if hasattr(self, "_focus_action"):
            self._focus_action.setText("✅ 看家模式：开" if self.focus_mode else "⬜ 看家模式：关")
        return self.speak(msg, 6000)

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
            self.bubble.hide()
            self.input_box.hide()
        else:
            self.show()
            self._do_hop(False) 

    def initTimers(self):
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._on_anim_tick)
        self.anim_timer.start(self.lib.intervals.get('idle', 130))
        
        self.fx_timer = QTimer(self)
        self.fx_timer.timeout.connect(self._on_fx_tick)
        self.fx_timer.start(self.FX_MS)

        # 注意：移动窗户已并入 _on_fx_tick（同一个定时器里"先移动再重绘"），
        # 这样窗口位置与画面永远同步，不会出现移动拖尾/残影。
        self.behavior_timer = QTimer(self)
        self.behavior_timer.setSingleShot(True)
        self.behavior_timer.timeout.connect(self._on_behavior_fire)

    def change_state(self, state_name, mode='loop'):
        frames = self.animations.get(state_name)
        if not frames: return
        self.state = state_name
        self.anim_timer.setInterval(self.lib.intervals.get(state_name, 130))

        if mode == 'loop':
            self.seq = frames
            self.frame_mode = 'loop'
            self.on_end = None
        elif mode in ('once', 'play_once_and_stop'):
            self.seq = frames
            self.frame_mode = 'seq'
            self.on_end = 'stay'
        elif mode == 'action_and_return':
            # 动作正向播完 → 末帧停留1帧 → 回发呆（不做溶解，避免残影/粘连）
            self.seq = frames + [frames[-1]]
            self.frame_mode = 'seq'
            self.on_end = 'idle'
        self.idx = 0

    def transition_to(self, state, hold=1):
        """
        从当前画面切到目标状态：保留 hold 帧缓冲 + 轻微压扁(软着陆)，
        刻意不使用溶解过渡——两帧叠加会产生残影/粘连。
        """
        target = self.animations.get(state)
        cur = self.seq[self.idx] if self.seq and self.idx < len(self.seq) else None
        if not target or cur is None:
            self.change_state(state, 'loop')
            return
        self.state = state
        self.anim_timer.setInterval(max(50, self.lib.intervals.get(state, 130)))
        self.seq = [cur] * max(0, hold)
        self.frame_mode = 'seq'
        self.on_end = state          # 缓冲播完 → 进入目标状态的正常循环
        self.idx = 0
        self.squash = 0.96           # 轻微压扁：读作“落座/落地”，而不是硬切

    def _on_anim_tick(self):
        if not self.seq or self.frame_mode == 'freeze': return
        if self.frame_mode == 'loop':
            self.idx = (self.idx + 1) % len(self.seq)
        else:
            if self.idx < len(self.seq) - 1:
                self.idx += 1
            else:
                if self.on_end == 'stay':
                    self.frame_mode = 'freeze'
                    self.idx = len(self.seq) - 1
                elif self.on_end:
                    self.change_state(self.on_end, 'loop')

    # ---- 程序化眨眼（不动素材：在眼睛位置短暂画一条闭眼弧线）----
    def _paint_blink(self, painter, lw, lh):
        """眨眼：在眼睛位置画一道短弧线（跟随缩放/翻转/倾斜，所以不会跑偏）。"""
        if time.time() >= self._blink_until or not self._eye_spots:
            return
        # 只认 idle 首帧的坐标：本方法仅在发呆姿势下生效（_tick_blink 已保证）
        src = self.animations.get('idle', [None])[0]
        if src is None:
            return
        sx = lw / max(1, src.width())
        sy = lh / max(1, src.height())
        pen = QPen(QColor(74, 52, 44, 235))
        pen.setWidthF(max(1.6, 3.0 * min(sx, sy)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for (cx, cy, r) in self._eye_spots:
            x, y, rr = cx * sx, cy * sy, r * sx
            path = QPainterPath()
            path.moveTo(x - rr, y - rr * 0.1)
            path.quadTo(x, y + rr * 0.55, x + rr, y - rr * 0.1)
            painter.drawPath(path)

    def _detect_eyes(self):
        """在 idle 首帧上找两只眼睛的位置（蓝色虹膜），供眨眼用。"""
        try:
            pm = self.animations.get('idle', [None])[0]
            if pm is None:
                return
            img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            lw, lh = img.width(), img.height()
            pts = []
            for y in range(0, lh, 2):
                for x in range(0, lw, 2):
                    c = img.pixelColor(x, y)
                    if c.alpha() < 200:
                        continue
                    if c.blue() - c.red() > 25 and c.blue() > 140:
                        pts.append((x, y))
            if len(pts) < 20:
                return
            xs = sorted(p[0] for p in pts)
            mid = xs[len(xs) // 2]
            left = [p for p in pts if p[0] < mid]
            right = [p for p in pts if p[0] >= mid]
            spots = []
            for group in (left, right):
                if len(group) < 10:
                    continue
                cx = sum(p[0] for p in group) / len(group)
                cy = sum(p[1] for p in group) / len(group)
                # 先剔除离群点，再用"中位距离"估半径：直接用最大跨度会被一个离群点
                # 撑成横跨半张脸的大弧线（实测右眼被算成 52px）
                near = [q for q in group if (q[0] - cx) ** 2 + (q[1] - cy) ** 2 <= 36 ** 2]
                g2 = near if len(near) >= 8 else group
                if g2 is not group:
                    cx = sum(q[0] for q in g2) / len(g2)
                    cy = sum(q[1] for q in g2) / len(g2)
                dists = sorted(((q[0] - cx) ** 2 + (q[1] - cy) ** 2) ** 0.5 for q in g2)
                r = max(7.0, min(26.0, dists[len(dists) // 2] * 1.35))
                spots.append((cx, cy, r))
            self._eye_spots = spots
            app_health.log(f"眨眼定位：找到 {len(spots)} 只眼睛 "
                           + str([(round(s[0], 1), round(s[1], 1), round(s[2], 1)) for s in spots]))
        except Exception as e:
            app_health.log(f"眨眼定位失败：{e}", level=30)
            self._eye_spots = []

    def _tick_blink(self, now):
        """排眨眼：随机 2.5~6.5 秒一次，偶尔连眨两下；只在发呆姿势下眨。"""
        if not self._eye_spots:
            return
        if self.state != 'idle' or self._dragging or self.hop_active or self.pacing or self.hover > 0:
            self._blink_until = 0
            return
        if self._next_blink <= 0:
            self._next_blink = now + random.uniform(2.5, 6.5)
            return
        if now >= self._next_blink:
            self._blink_until = now + 0.09                    # 闭眼约 90ms
            gap = 0.16 if random.random() < 0.25 else random.uniform(2.5, 6.5)
            self._next_blink = now + 0.09 + gap

    def _on_fx_tick(self):
        _amp, _spd = self.BREATHE.get(self.state, (0.005, 0.3))
        # 呼吸随机化：每个周期换一次幅度/速度，偶尔来一次深呼吸——避免"机器式"均匀起伏
        now = time.time()
        if self.state in ('idle', 'sleep'):
            self.breathe_phase += self._breath_spd
            if self.breathe_phase >= 2 * math.pi:
                self.breathe_phase -= 2 * math.pi
                self._breath_spd = random.uniform(0.22, 0.42)
                self._breath_amp = random.uniform(0.005, 0.014)
                if random.random() < 0.18:            # 偶尔一次深呼吸
                    self._breath_amp *= 1.9
        self._tick_blink(now)

        if not self._dragging:
            if self.hop_active:
                self.hover += self.hop_vy
                self.hop_vy -= 0.11          # 重力（与 HOP_HEIGHT 匹配，约0.55s一个来回）
                if self.hover <= 0:
                    self.hover = 0.0
                    self.hop_active = False
                    self.squash = 0.88       # 落地轻压扁（原0.70太夸张，像果冻）
                    self._spawn_particles(2, color=QColor(200, 200, 200, 130), is_heart=False)
            elif self.pacing:
                self.walk_phase += 0.5
                self.hover = 0.0
            else:
                self.hover = 0.0
                
        if self.squash != 1.0:
            diff = 1.0 - self.squash
            self.squash += diff * 0.15
            if abs(diff) < 0.01: self.squash = 1.0

        if self._dizzy and not self._dragging:
            self._drag_speed *= 0.85
            if self._drag_speed < 1.5:
                self._dizzy = False
                self.change_state('idle', 'loop')

        # 散步移动：与重绘同一个定时器（先移动、后 update），彻底避免移动拖尾
        if self.pacing:
            self._advance_walk()

        self._follow_cursor()      # 偶尔朝鼠标方向看一眼

        if self.particles:
            keep = []
            for pt in self.particles:
                pt['age'] += 1
                pt['y'] += pt['vy']
                pt['vy'] += 0.015
                if pt['vy'] > 0: pt['vy'] = max(pt['vy'] * 0.9, 0.0) 
                pt['x'] += pt['vx'] * math.sin(pt['age'] * 0.3 + pt['seed'])
                if pt['age'] < pt['life']: keep.append(pt)
            self.particles = keep
            
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        # 【关键】每帧先把整块画布清成完全透明：
        # 半透明无边框窗口如果不显式清屏，上一帧的像素会从透明区域透出来，形成"残影"
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        lw, lh = self.lib.logical_w, self.lib.logical_h
        feet_x = lw / 2.0
        feet_y = float(self.anchor_y())
        pm = self.seq[self.idx] if self.seq and self.idx < len(self.seq) else None
        if pm is None: return

        air = max(0.2, 1.0 - max(self.hover, 0) / 60.0)
        rx = min(52.0, max(24.0, lw * 0.13)) * air
        ry = min(7.5, rx * 0.14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(40 * air)))
        painter.drawEllipse(QRectF(feet_x - rx, feet_y + 1 - ry, rx * 2, ry * 2))

        amp = self._breath_amp
        breath = math.sin(self.breathe_phase)
        
        drag_stretch_y = 1.0
        drag_stretch_x = 1.0
        if self._dragging:
             stretch_factor = min(0.3, max(-0.3, self._drag_speed * 0.02 * (-1 if self._last_drag_pos.y() < 0 else 1)))
             drag_stretch_y = 1.0 + stretch_factor
             drag_stretch_x = 1.0 - stretch_factor * 0.5
        
        ky = self.squash * (1.0 + amp * breath) * drag_stretch_y
        kx = (1.0 + (1.0 - self.squash) * 0.6) * (1.0 + amp * 0.7 * breath) * drag_stretch_x
        
        lean_deg = math.sin(self.walk_phase) * 1.5 if self.pacing else 0.0
        if self._dizzy or self._dragging:
            # 被甩晕时的轻微摇晃（原±25°@3Hz像抽搐，这里压到±5°、更慢）
            lean_deg += math.sin(time.time() * 7.0) * min(5.0, max(0.0, self._drag_speed)) * self.facing

        painter.save()
        painter.translate(feet_x, feet_y)
        painter.scale(kx, ky)
        if self.facing < 0: painter.scale(-1.0, 1.0)
        painter.rotate(lean_deg)
        painter.translate(-feet_x, -feet_y)
        painter.translate(0, -self.hover)

        painter.drawPixmap(QRectF(0, 0, lw, lh), pm, QRectF(pm.rect()))
        self._paint_blink(painter, lw, lh)
        painter.restore()

        for pt in self.particles:
            k = 1.0 - pt['age'] / pt['life']
            alpha = int(pt['color'].alpha() * k * k)
            c = QColor(pt['color'])
            c.setAlpha(alpha)
            size = pt['size'] * (0.6 + 0.4 * k)
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c)
            if pt['is_heart']:
                path = QPainterPath()
                s = size
                path.addEllipse(QPointF(pt['x'] - 0.34 * s, pt['y'] - 0.24 * s), 0.34*s, 0.34*s)
                path.addEllipse(QPointF(pt['x'] + 0.34 * s, pt['y'] - 0.24 * s), 0.34*s, 0.34*s)
                path.moveTo(pt['x'] - 0.66 * s, pt['y'] - 0.02 * s)
                path.lineTo(pt['x'] + 0.66 * s, pt['y'] - 0.02 * s)
                path.lineTo(pt['x'], pt['y'] + 0.68 * s)
                path.closeSubpath()
                painter.fillPath(path, c)
            else:
                painter.drawEllipse(QPointF(pt['x'], pt['y']), size, size*0.8)

    def _do_hop(self, with_hearts=True):
        if self.hop_active or self._dragging: return
        self.hop_active = True
        self.hop_vy = math.sqrt(2 * 0.15 * self.HOP_HEIGHT)
        if with_hearts:
            self._spawn_particles(random.randint(5, 8), QColor(255, 105, 180, 235), is_heart=True)

    def _spawn_particles(self, n, color, is_heart=True):
        cx, fy = self.lib.logical_w * 0.5, float(self.anchor_y())
        for _ in range(n):
            self.particles.append({
                'x': cx + random.uniform(-26, 26),
                'y': fy - (random.uniform(60, 150) if is_heart else random.uniform(0, 15)) - max(0, self.hover),
                'vx': random.uniform(-0.15, 0.15) if is_heart else random.uniform(-0.4, 0.4),
                'vy': -random.uniform(0.55, 0.85) if is_heart else -random.uniform(0.1, 0.4),
                'seed': random.uniform(0, 6.28),
                'age': 0,
                'life': random.randint(38, 58) if is_heart else random.randint(15, 25),
                'size': random.uniform(5, 11),
                'color': color,
                'is_heart': is_heart
            })

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            now = time.time()
            self._click_times = [t for t in self._click_times if now - t < 0.9]

            # 连续快速点击 4 次才算"欺负它"（原来3次太容易被误判，害得摸头没反应）
            if len(self._click_times) >= 4:
                self.affection = max(0, self.affection - 5)
                self.speak("喵呜！别打我！疼！", 2000)
                self.change_state('sleep', 'play_once_and_stop')
                self._click_times.clear()
                return
            self._click_times.append(now)

            self._dragging = True
            if self.pacing: self._stop_pace()
            self.behavior_timer.stop()

            self._last_mouse_x = self.x()
            self._last_drag_time = time.time()

            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

            self.hover = max(self.hover, 3.0)   # 被拎起来时只轻轻抬一点（原20px会瞬移）
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self.drag_position
            
            now = time.time()
            dt = now - self._last_drag_time
            if dt > 0:
                dx = new_pos.x() - self.x()
                dy = new_pos.y() - self.y()
                self._last_drag_pos = QPointF(dx, dy)
                speed = math.hypot(dx, dy) / (dt * 1000) 
                self._drag_speed = speed
                
                if speed > 6.0 and not self._dizzy:
                    self._dizzy = True
                    self.speak("晕晕晕！别甩啦！@_@", 2000)
                    self.change_state('idle', 'loop') 

            self.move(new_pos)
            self.update()                 # 移动后立即重绘，避免拖动拖尾
            self._last_drag_time = now

            dx = self.x() - self._last_mouse_x
            if abs(dx) > 3: self.facing = 1 if dx > 0 else -1
            self._last_mouse_x = self.x()
            
            self.update_ui_positions()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            # 松手后自然下落（限制初速，避免猛地砸下去）
            self.hop_active = True
            self.hop_vy = max(-1.5, min(0.0, self._last_drag_pos.y() * 0.1))

            self._snap_to_edge()          # 靠边就自动吸附到屏幕边缘
            self._schedule_behavior(2500)
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if len(self._click_times) < 4:
                self._pet_pet()
            event.accept()

    def _pet_pet(self):
        if self.state == 'sleep': self._wake_from_sleep(); return
        if self.affection < 100: self.affection += 1
        self._do_hop(True)
        self.speak(random.choice(["喵呜~（蹭蹭）", "呼噜呼噜…好舒服！", "嘿嘿，最喜欢主人啦"]),
                   2600, mood="cozy")

    def speak_rich(self, html, plain, duration=9000, mood=None):
        """显示带链接的富文本气泡；朗读仍然用纯文本 plain。"""
        self.bubble.show_rich(html, duration)
        self.update_ui_positions()
        if self.voice_on and plain:
            spoken = re.sub(r"[^\w\s\u4e00-\u9fff，。！？、：；（）%.]", "", plain)
            spoken = spoken.replace("\n", "，")[:120]
            if spoken.strip():
                self.speaker.say(spoken, mood=mood or self._infer_mood())

    def _found_bubble(self, found, head="喵~ 找到这些："):
        """把搜索结果做成可点击气泡：文件名=默认程序打开，📂=打开所在文件夹。"""
        rows = []
        for i, p in enumerate(found[:3], start=1):
            uri = "file:///" + os.path.normpath(p).replace("\\", "/")
            rows.append(
                f'{i}. <a href="{uri}" style="color:#c2185b;text-decoration:none">{ai.short_path(p)}</a>'
                f' &nbsp;<a href="duoduo://folder/{i}" style="color:#888;text-decoration:none">📂</a>'
                f'<br><span style="color:#999;font-size:9pt">&nbsp;&nbsp;'
                f'<a href="duoduo://app/notepad/{i}" style="color:#888;text-decoration:none">记事本</a> · '
                f'<a href="duoduo://app/@picker/{i}" style="color:#888;text-decoration:none">其它程序…</a>'
                f'</span>')
        html = (f'<div style="color:#333">{head}<br>' + "<br>".join(rows)
                + '<br><span style="color:#999;font-size:9pt">（点文件名直接打开）</span></div>')
        plain = head + "；".join(f"{i}. {ai.short_path(p)}" for i, p in enumerate(found[:3], start=1))
        self.speak_rich(html, plain, 12000)

    def _on_bubble_link(self, url):
        """处理气泡里被点击的链接。"""
        try:
            if url.startswith("duoduo://folder/"):
                idx = int(url.rsplit("/", 1)[-1])
                self.open_found_folder(idx)
            elif url.startswith("duoduo://app/"):
                _, _, rest = url.partition("duoduo://app/")
                app, _, idx = rest.rpartition("/")
                self.open_found(int(idx or 1), app or None)
            elif url.startswith("file:///"):
                path = url[len("file:///"):].replace("/", os.sep)
                if os.path.exists(path):
                    self.open_path(path)
                else:
                    self.speak("喵…这个文件好像被移走啦", 4000)
        except Exception as e:
            app_health.log(f"链接点击处理失败: {url} {e}", level=40)
            self.speak("喵…点不动这个链接", 4000)

    def open_path(self, path):
        """直接打开某个路径（气泡点击用）。"""
        try:
            os.startfile(path)
            return self.speak(f"打开啦：{ai.short_path(path)}", 3500)
        except Exception as e:
            return self.speak(f"喵…打不开（{e.__class__.__name__}）", 4000)

    def update_ui_positions(self):
        wa = QApplication.primaryScreen().availableGeometry()
        if self.bubble.isVisible():
            bx = self.x() + self.width() - 120
            by = self.y() - self.bubble.height() + 40
            bx = max(wa.left() + 4, min(bx, wa.right() - self.bubble.width() - 4))
            # 上下都要夹住：气泡变高时不能顶出屏幕（否则内容会被裁掉）
            by = max(wa.top() + 4, by)
            if by + self.bubble.height() > wa.bottom() - 4:
                by = max(wa.top() + 4, wa.bottom() - 4 - self.bubble.height())
            self.bubble.move(bx, by)
        if self.input_box.isVisible():
            ix = self.x() - (self.input_box.width() - self.width()) // 2
            iy = self.y() + self.height() + 10
            ix = max(wa.left() + 4, min(ix, wa.right() - self.input_box.width() - 4))
            iy = min(iy, wa.bottom() - self.input_box.height() - 4)
            self.input_box.move(ix, iy)

    # 状态 → 默认情绪（没显式给 mood 时按当前动作/状态推断，说话才有"情绪"）
    STATE_MOOD = {
        "eat": "happy", "hop": "excited", "knead": "cozy", "stretch": "cozy",
        "sleep": "sleepy", "yawn": "sleepy", "wake": "sleepy",
        "pounce": "excited", "turn": "normal", "walk": "normal", "idle": "normal",
    }

    def _infer_mood(self):
        """按当前状态推断情绪；情绪只在有对应动作时变化，平时就是平常语气。"""
        return self.STATE_MOOD.get(self.state, "normal")

    def speak(self, text, duration=4500, mood=None):
        """气泡说话；若开启了语音播报，同时朗读（去掉表情符号，念起来更自然）。

        mood 决定朗读语调（happy/excited/cozy/sleepy/alert/proud/sorry），
        不传就按当前状态推断（吃东西→开心、打哈欠→困倦、踩奶→撒娇…）。
        """
        self.bubble.show_message(text, duration)
        self.update_ui_positions()
        if self.voice_on and text:
            spoken = re.sub(r"[^\w\s\u4e00-\u9fff，。！？、：；（）%.]", "", text)
            spoken = spoken.replace("\n", "，")[:120]
            if spoken.strip():
                self.speaker.say(spoken, mood=mood or self._infer_mood())

    def mood_showcase(self):
        """情绪语音演示：同一类句子用不同情绪念一遍，方便主人挑喜欢的语调。"""
        if not self.voice_on:
            return self.speak("喵…语音播报是关着的，先说“开启语音”我再演示给你听", 6000)
        lines = [("normal", "这是平常的语气，喵。"),
                 ("happy", "主人回来啦，我好开心呀，喵！"),
                 ("excited", "有小鱼干！太棒了太棒了，喵喵！"),
                 ("cozy", "呼噜呼噜…再摸摸我嘛，喵~"),
                 ("sleepy", "哈欠…我好困，想睡一会儿了，喵…"),
                 ("alert", "主人，时间到啦，该喝水了喵！"),
                 ("proud", "你看，我把文件都帮你找好啦，喵~"),
                 ("sorry", "对不起嘛…我不是故意打翻东西的，喵…")]
        self.speaker.stop()                       # 先清掉待读的，免得串在一起
        for mood, text in lines:
            self.speaker.say(text, mood=mood)
        listing = "\n".join(f"{tools.mood_cn(m)}：{t}" for m, t in lines)
        self.bubble.show_message(f"情绪语音演示（共 {len(lines)} 种）：\n{listing}", 16000)
        self.update_ui_positions()
        return None

    # ================= 系统能力：语音 / 提醒 / 截图 / 剪贴板 / 状态 / 快捷键 / 跟随 =================
    def _toggle_voice(self, on=None):
        """开关语音播报；不传参数则切换。"""
        self.voice_on = (not self.voice_on) if on is None else bool(on)
        self.pet_data["voice"] = 1 if self.voice_on else 0
        if self.voice_on:
            if not self.speaker.available():
                self.voice_on = False
                self.pet_data["voice"] = 0
                return "喵…这台电脑上我找不到语音引擎，说不出来呢"
            return "喵~ 我开始说话啦（想安静就跟我说“闭嘴”）"
        self.speaker.stop()
        return "好，那我安静陪你~"

    def take_screenshot(self):
        """截取全屏并保存到桌面。"""
        try:
            screen = QApplication.primaryScreen()
            pix = screen.grabWindow(0)
            if pix.isNull():
                return "喵…这次截不到屏幕呢"
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(desktop):
                desktop = os.path.expanduser("~")
            name = "多多截图_" + time.strftime("%Y%m%d_%H%M%S") + ".png"
            path = os.path.join(desktop, name)
            pix.save(path, "PNG")
            self._do_hop(False)
            return f"咔嚓！截好啦，存在桌面：{name}"
        except Exception as e:
            return f"喵…截图失败了（{e.__class__.__name__}）"

    @staticmethod
    def clipboard_text():
        try:
            return QApplication.clipboard().text() or ""
        except Exception:
            return ""

    def clipboard_read_aloud(self):
        """朗读剪贴板内容（同时显示在气泡里）。"""
        text = self.clipboard_text().strip()
        if not text:
            return "剪贴板是空的呀，先复制点什么吧"
        brief = text[:300]
        return "剪贴板里是：「" + brief + ("…」" if len(text) > 300 else "」")

    def clipboard_save(self):
        """把剪贴板内容存成桌面上的 txt（也支持存历史里的某一条）。"""
        text = getattr(self, "_clip_override", None) or self.clipboard_text()
        if not text.strip():
            return "剪贴板是空的呀"
        try:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(desktop):
                desktop = os.path.expanduser("~")
            name = "剪贴板_" + time.strftime("%Y%m%d_%H%M%S") + ".txt"
            path = os.path.join(desktop, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return f"存好啦：{name}（共 {len(text)} 字）"
        except Exception as e:
            return f"喵…存不下来（{e.__class__.__name__}）"

    def system_status_reply(self):
        """读电量/内存/CPU/网络并汇报（取数放子线程，避免卡界面）。"""
        def worker():
            try:
                st = tools.system_status()
                self.llm_reply.emit(tools.status_text(st), "", None, "")
            except Exception as e:
                self.llm_reply.emit(f"喵…读系统信息失败了（{e.__class__.__name__}）", "", None, "sorry")
        threading.Thread(target=worker, daemon=True).start()
        return None

    # ---- 打开搜索到的文件 / 文件夹 ----
    MAX_OPEN = 5                 # 最多记 5 条，避免"打开第9个"这种没意义的情况

    def remember_found(self, paths):
        """记住最近一次搜索到的文件（供"打开第2个""打开它"使用）。"""
        self._last_found = [p for p in (paths or []) if os.path.exists(p)][:self.MAX_OPEN]
        return self._last_found

    def open_found(self, which=1, app=None):
        """
        打开最近搜索到的第 which 个（1 起算）。
        app 决定"用什么方式打开"：
          None        用系统默认程序（文件夹则用资源管理器）
          "notepad"   用指定程序（记事本/画图/VS Code/Word…）
          "@browser"  用默认浏览器（适合 html/pdf 等）
          "@picker"   弹出 Windows 自带的「打开方式」选择框，主人自己挑程序
        """
        found = getattr(self, "_last_found", [])
        if not found:
            return self.speak("喵？主人还没让我找过文件呢，先跟我说“找文件 XXX”吧", 5000)
        try:
            idx = int(which)
        except Exception:
            idx = 1
        if idx < 1 or idx > len(found):
            listing = "\n".join(f"{i+1}. {ai.short_path(p)}" for i, p in enumerate(found))
            return self.speak(f"喵…只找到 {len(found)} 个，主人是想打开哪个？\n{listing}", 8000)
        target = found[idx - 1]
        shown = ai.short_path(target)
        path = os.path.normpath(target)
        try:
            if app == "@picker":
                # Windows 自带的「打开方式」：rundll32 shell32.dll,OpenAs_RunDLL <文件>
                subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", path])
                return self.speak(f"「{shown}」要交给哪个程序？选择框打开啦", 5000)
            if app == "@browser":
                webbrowser.open("file:///" + path.replace("\\", "/"))
                return self.speak(f"用浏览器打开啦：{shown}", 4000)
            if app:
                exe = shutil.which(app)
                if not exe:
                    return self.speak(
                        f"喵…这台电脑上找不到「{app}」；可以让主人自己选："
                        f"说“换个方式打开第{idx}个”", 8000)
                subprocess.Popen([exe, path])
                return self.speak(f"用 {ai.open_with_cn(app)} 打开啦：{shown}", 4000)
            os.startfile(target)          # 默认程序
            return self.speak(f"打开啦：{shown}", 4000)
        except Exception as e:
            return self.speak(f"喵…打不开（{e.__class__.__name__}）；"
                              f"说“换个方式打开第{idx}个”可以自己挑程序", 6000)

    def open_found_folder(self, which=1):
        """打开"第 which 个搜索结果所在的文件夹"并在资源管理器里选中它。"""
        found = getattr(self, "_last_found", [])
        if not found:
            return self.speak("喵？先跟我说“找文件 XXX”，我找到之后才能打开它的位置", 5000)
        try:
            idx = int(which)
        except Exception:
            idx = 1
        idx = max(1, min(idx, len(found)))
        target = found[idx - 1]
        try:
            folder = target if os.path.isdir(target) else os.path.dirname(target)
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
            return self.speak(f"已经在资源管理器里打开：{ai.short_path(folder)}", 4500)
        except Exception as e:
            return self.speak(f"喵…打不开文件夹（{e.__class__.__name__}）", 4000)

    # ---- 语音音色 ----
    def voice_engine(self):
        """当前语音引擎/音色说明。"""
        label = tools.SPEAKER.label() if hasattr(tools.SPEAKER, "label") else tools.SPEAKER.engine
        tip = "（想更像人的声音：pip install edge-tts 后会自动切到神经语音）"
        if str(label).startswith("edge"):
            tip = ""
        return f"喵～我现在用的是 {label}{tip}"

    def preview_voice(self):
        """试听当前音色（即使语音开关是关的也播一句）。"""
        was = tools.SPEAKER.enabled
        tools.SPEAKER.enabled = True
        ok = tools.SPEAKER.preview(f"你好呀，我是{self.cat_name}，喵~")
        if not was:
            QTimer.singleShot(4000, lambda: setattr(tools.SPEAKER, "enabled", was))
        return self.speak(self.voice_engine() if ok else "喵…这台电脑上没找到可用的语音引擎", 6000)

    def next_voice(self):
        """换到下一个可用音色并试听。"""
        was = tools.SPEAKER.enabled
        tools.SPEAKER.enabled = True
        name, ok = tools.SPEAKER.next_voice()
        if not was:
            QTimer.singleShot(4000, lambda: setattr(tools.SPEAKER, "enabled", was))
        return self.speak(f"换成「{name}」啦" if ok else f"喵…换不了音色（当前 {name}）", 6000)

    # ---- 定时提醒 ----
    def add_reminder(self, seconds, message):
        """安排一个一次性提醒；返回给用户看的确认文案。"""
        self.add_schedule({"kind": "once", "delay": int(seconds), "msg": message,
                           "at": None, "weekdays": None})
        return f"好嘞！{tools.human_delay(seconds)}后提醒你：{message}"

    def add_schedule(self, spec):
        """安排日程（一次性/每天/每周/工作日）并用托盘通知+气泡+语音三重提醒。"""
        self._remind_token += 1
        token = self._remind_token
        due = tools.next_due(spec)
        msg = spec.get("msg") or "时间到了"
        self._reminders.append((token, due, msg, spec))
        delay_ms = max(200, int((due - time.time()) * 1000))
        QTimer.singleShot(delay_ms, partial(self._fire_schedule, token))
        self._save_now()
        when = tools.schedule_text(spec)
        return f"好嘞！{when}提醒你：{msg}"

    def _fire_schedule(self, token):
        """到点：气泡 + 托盘通知 + 语音；重复日程自动续下一次。"""
        item = next((r for r in self._reminders if r[0] == token), None)
        if item is None:
            return
        _tok, _due, msg, spec = item
        self._reminders = [r for r in self._reminders if r[0] != token]
        if not self.isVisible():
            self.show()
        self._do_hop(False)
        self.change_state('idle', 'loop')
        self.speak(f"⏰ 时间到啦！{msg}", 9000, mood="alert")
        if self.voice_on:
            self.speaker.say(f"时间到啦，{msg}", mood="alert")
        try:
            self.tray_icon.showMessage(f"{self.cat_name}提醒", msg,
                                       QSystemTrayIcon.MessageIcon.Information, 10000)
        except Exception as e:
            app_health.log(f"托盘通知失败：{e}", level=30)
        # 重复日程：算出下一次继续排
        if spec and spec.get("kind") in ("daily", "weekly"):
            self.add_schedule(spec)
            app_health.log(f"日程续期：{msg} -> {tools.schedule_text(spec)}")

    def _fire_reminder(self, token, message):
        """兼容旧的一次性提醒（老代码/旧存档）。"""
        self._reminders = [r for r in self._reminders if r[0] != token]
        self.add_schedule({"kind": "once", "delay": 0, "msg": message,
                           "at": None, "weekdays": None})
        self._fire_schedule(self._remind_token)

    def cancel_reminders(self):
        n = len(self._reminders)
        self._remind_token += 1          # 让已排期的回调令牌失效
        self._reminders = []
        return f"已取消 {n} 个提醒" if n else "现在没有待办的提醒呀"

    # ---- 全局快捷键（Ctrl+Alt+D 呼出聊天框 / Ctrl+Alt+C 翻译剪贴板 / Ctrl+Alt+Z 总结剪贴板）----
    # 注意：RegisterHotKey 是全局抢占，所以避开了常见 IDE 快捷键（如 Ctrl+Alt+S）
    HOTKEYS = (("chat", 0x4D44, 0x44),            # VK_D
               ("clip_translate", 0x4D45, 0x43),  # VK_C
               ("clip_summary", 0x4D46, 0x5A))    # VK_Z

    def initHotkey(self):
        """注册全局热键；单个失败不影响其它功能，整体失败也不影响程序。

        幂等：重复调用不会重复注册（同一线程重复注册同一 id 会失败，进而打乱分发表）。
        """
        if os.name != "nt":
            return
        if self._hotkey_ok and self._hotkeys:
            return
        try:
            import ctypes
            self._user32 = ctypes.windll.user32
            MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
            hotkeys = dict(self._hotkeys)
            for name, hk_id, vk in self.HOTKEYS:
                if hotkeys.get(hk_id) == name:
                    continue                      # 之前已经注册成功过
                if self._user32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_ALT, vk):
                    hotkeys[hk_id] = name
            self._hotkeys = hotkeys
            self._hotkey_id = self.HOTKEYS[0][1]       # 兼容旧字段：聊天框热键
            self._hotkey_ok = bool(self._hotkeys)
            if self._hotkeys:
                QApplication.instance().installNativeEventFilter(self)
        except Exception:
            self._hotkey_ok = bool(self._hotkeys)

    def nativeEventFilter(self, event_type, message):     # noqa: N802 (Qt 命名)
        """接收 WM_HOTKEY：按注册表分发（聊天框 / 翻译剪贴板 / 总结剪贴板）。"""
        try:
            if event_type == "windows_generic_MSG":
                import ctypes
                from ctypes import wintypes
                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == 0x0312:
                    self._on_hotkey(msg.wParam)
        except Exception:
            pass
        return False, 0

    def _on_hotkey(self, hk_id):
        """热键动作本体（与 Qt 事件解耦，方便测试直接调用）。"""
        name = getattr(self, "_hotkeys", {}).get(hk_id)
        if name == "chat":
            self.input_box.show_at(self.x() - (self.input_box.width() - self.width()) // 2,
                                   self.y() + self.height() + 10)
        elif name == "clip_translate":
            self.clipboard_ai("translate")
        elif name == "clip_summary":
            self.clipboard_ai("summary")

    # ---- 鼠标跟随注视 / 屏幕边缘吸附 ----
    def _follow_cursor(self):
        """偶尔朝鼠标方向看一眼（避免频繁翻转）。"""
        if not self._follow_enabled or self.pacing or self._dragging or self.state != 'idle':
            return
        self._follow_tick += 1
        if self._follow_tick % 20:        # 约 0.6 秒判断一次
            return
        try:
            from PyQt6.QtGui import QCursor
            cursor_x = QCursor.pos().x()
            center = self.x() + self.width() / 2
            if abs(cursor_x - center) > 90:
                self.facing = 1 if cursor_x > center else -1
        except Exception:
            pass

    def _snap_to_edge(self):
        """拖拽结束后，靠边 40px 内自动吸附到该屏幕边缘。"""
        try:
            wa = QApplication.primaryScreen().availableGeometry()
            margin = 40
            x, y = self.x(), self.y()
            if x - wa.left() < margin:
                x = wa.left()
            elif wa.right() - (x + self.width()) < margin:
                x = wa.right() - self.width()
            if y - wa.top() < margin:
                y = wa.top()
            elif wa.bottom() - (y + self.height()) < margin:
                y = wa.bottom() - self.height()
            if (x, y) != (self.x(), self.y()):
                self.move(int(x), int(y))
                self.update_ui_positions()
        except Exception:
            pass

    def showContextMenu(self, position):
        self.buildContextMenu().exec(self.mapToGlobal(position))

    def buildContextMenu(self):
        """构建右键菜单（按功能分组，与弹出分离，便于测试断言）。

        顶层只留 4 组 + 聊天 + 退出；音色/试听这类"已经定下来"的入口不再放菜单里
        （聊天里说“试听一下/换个声音/情绪演示”仍然有效）。
        """
        context_menu = QMenu(self)
        context_menu.setStyleSheet("QMenu { background: white; border-radius: 5px; } QMenu::item { padding: 8px 25px; } QMenu::item:selected { background: #ffcccc; color: black; }")

        chat_action = QAction("💬 和我聊天（Ctrl+Alt+D）", self)
        chat_action.triggered.connect(lambda: self.input_box.show_at(self.x() - (self.input_box.width() - self.width()) // 2, self.y() + self.height() + 10))
        context_menu.addAction(chat_action)
        context_menu.addSeparator()

        # --- 互动 ---
        play_menu = QMenu("🐾 互动", self)
        for label, slot in (("🐟 喂食小鱼干", self.feed_cat),
                            ("💗 摸摸我", self._pet_pet),
                            ("🚶 陪我散步", lambda: self._do_action("pace")),
                            ("🛑 站住", lambda: self._do_action("stop")),
                            ("😴 睡觉", lambda: self._do_action("sleep")),
                            ("☀️ 叫醒", lambda: self._do_action("wake"))):
            a = QAction(label, self)
            a.triggered.connect(slot)
            play_menu.addAction(a)
        context_menu.addMenu(play_menu)

        # --- 剪贴板 ---
        clip_menu = QMenu("📋 剪贴板", self)
        for label, mode in (("🌐 翻译（Ctrl+Alt+C）", "translate"),
                            ("📝 总结（Ctrl+Alt+Z）", "summary"),
                            ("💡 解释", "explain"),
                            ("✍️ 润色", "polish"),
                            ("↩️ 帮回复", "reply")):
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, mode=mode: self.clipboard_ai(mode))
            clip_menu.addAction(a)
        clip_menu.addSeparator()
        for label, slot in (("🕘 历史", self.show_clip_history),
                            ("🔊 念一遍", lambda: self._do_action("clip_read")),
                            ("💾 存到桌面", lambda: self._do_action("clip_save"))):
            a = QAction(label, self)
            a.triggered.connect(slot)
            clip_menu.addAction(a)
        context_menu.addMenu(clip_menu)

        # --- 工具 ---
        tool_menu = QMenu("🔧 工具", self)
        shot_action = QAction("📸 截个屏", self)
        shot_action.triggered.connect(lambda: self._do_action("screenshot"))
        tool_menu.addAction(shot_action)
        sys_action = QAction("📊 系统状态", self)
        sys_action.triggered.connect(lambda: self._do_action("status"))
        tool_menu.addAction(sys_action)
        vol_menu = QMenu("🔊 音量", self)
        for label, act in (("大一点", "up"), ("小一点", "down"), ("静音", "mute")):
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, act=act: self.do_volume(act))
            vol_menu.addAction(a)
        tool_menu.addMenu(vol_menu)
        tool_menu.addSeparator()
        recent_menu = QMenu("📂 最近找到的文件", self)
        self._rebuild_recent_menu(recent_menu)
        recent_menu.aboutToShow.connect(lambda m=recent_menu: self._rebuild_recent_menu(m))
        tool_menu.addMenu(recent_menu)
        context_menu.addMenu(tool_menu)

        # --- 设置 ---
        set_menu = QMenu("⚙️ 设置", self)
        voice_action = QAction(("🔇 语音播报：关" if not self.voice_on else "🔊 语音播报：开"), self)
        voice_action.triggered.connect(lambda: self._do_command("voice", None))
        set_menu.addAction(voice_action)
        self._focus_action = QAction(("✅ 看家模式：开" if self.focus_mode else "⬜ 看家模式：关"), self)
        self._focus_action.triggered.connect(self.toggle_focus_mode)
        set_menu.addAction(self._focus_action)
        self._autostart_action = QAction(
            ("✅ 开机自启：开" if app_health.autostart_enabled() else "⬜ 开机自启：关"), self)
        self._autostart_action.triggered.connect(self.toggle_autostart)
        set_menu.addAction(self._autostart_action)
        self._fs_action = QAction(
            (("✅ 全屏时自动避让：开" if self.avoid_fullscreen else "⬜ 全屏时自动避让：关")), self)
        self._fs_action.triggered.connect(lambda: self.speak(self.set_avoid_fullscreen()))
        set_menu.addAction(self._fs_action)
        status_action = QAction("❤️ 状态面板", self)
        status_action.triggered.connect(self.check_status)
        set_menu.addAction(status_action)
        context_menu.addMenu(set_menu)

        hint2 = QAction("💡 直接跟我说：\"25分钟后提醒我喝水\" / \"翻译这段\" / \"找文件 报告\"", self)
        hint2.setEnabled(False)
        context_menu.addAction(hint2)
        context_menu.addSeparator()

        quit_action = QAction("❌ 退出程序", self)
        quit_action.triggered.connect(self.safe_quit)
        context_menu.addAction(quit_action)

        return context_menu

    def _do_action(self, action):
        """
        执行大脑下发的动作指令（本地聊天与大模型回复共用）。
        action 可以是字符串，也可以是 ("指令", 参数...) 元组——后者用于带参数的指令。
        """
        if isinstance(action, tuple):
            self._do_command(*action)
            return
        if action == "eat":
            self.change_state('eat', 'action_and_return')
        elif action == "hop":
            self._do_hop(True)
        elif action == "sleep":
            self._go_sleep()
        elif action == "wake":
            self._wake_from_sleep()
        elif action == "pace":
            if not self.pacing:
                self._start_pace()          # 只启动，不做开关切换（用户指令优先）
        elif action == "stop":
            self._stop_pace()
        elif action == "feed":
            self.feed_cat()
        elif action in ("yawn", "stretch", "knead", "turn", "pounce"):
            self._play_action(action)       # 一次性动作片段，播完自动回发呆
        elif action == "preview_voice":
            self.preview_voice()
        elif action == "next_voice":
            self.next_voice()
        elif action == "mood_showcase":
            self.mood_showcase()
        elif action == "screenshot":
            self.speak(self.take_screenshot(), 6000)
        elif action == "clip_read":
            self.speak(self.clipboard_read_aloud(), 8000)
        elif action == "clip_save":
            self.speak(self.clipboard_save(), 6000)
        elif action == "clip_history":
            self.show_clip_history()
        elif action == "status":
            if self.system_status_reply() is None:
                self.speak("喵~ 我看看…", 2000)
        elif action == "cancel_remind":
            self.speak(self.cancel_reminders(), 4000)

    def _do_command(self, cmd, *args):
        """带参数的指令（元组形式）。"""
        if cmd == "remind":
            secs, msg = args
            self.speak(self.add_reminder(secs, msg), 6000)
        elif cmd == "schedule":
            self.speak(self.add_schedule(args[0] if args else {}), 7000)
        elif cmd == "avoid_fs":
            want = args[0] if args else None
            if want is None or want == self.avoid_fullscreen:
                self.speak("全屏避让现在是「%s」" % ("开着" if self.avoid_fullscreen else "关着")
                           + "；想改就说“打开/关闭全屏避让”", 6000)
            else:
                self.speak(self.set_avoid_fullscreen(want), 5000)
                if self._fs_timer is None:
                    self._fs_timer = QTimer(self)
                    self._fs_timer.timeout.connect(self._fullscreen_tick)
                self._fs_timer.start(4000) if self.avoid_fullscreen else self._fs_timer.stop()
                if hasattr(self, "_fs_action"):
                    self._fs_action.setText("✅ 全屏时自动避让：开" if self.avoid_fullscreen
                                            else "⬜ 全屏时自动避让：关")
        elif cmd == "voice":
            self.speak(self._toggle_voice(args[0] if args else None), 4000)
        elif cmd == "volume":
            self.do_volume(args[0] if args else "")
        elif cmd == "clipboard":
            self.clipboard_ai(args[0] if args else "summary")
        elif cmd == "clip_item":
            self.use_clip_item(args[0] if args else 1, args[1] if len(args) > 1 else "translate")
        elif cmd == "open_found":
            which = args[0] if args else 1
            app = args[1] if len(args) > 1 else None
            self.open_found(which, app)
        elif cmd == "open_found_folder":
            self.open_found_folder(args[0] if args else 1)
        elif cmd == "focus":
            self.toggle_focus_mode(args[0] if args else None)
        elif cmd == "autostart":
            want = args[0] if args else None
            if want is not None and want != app_health.autostart_enabled():
                self.toggle_autostart()
            else:
                self.speak("开机自启现在是「%s」" % ("开着" if app_health.autostart_enabled() else "关着")
                           + "；想改就说“打开/关闭开机自启”", 6000)

    def handle_user_message(self, text):
        self._tool_rounds = 0                     # 新一轮对话，重置工具链轮数
        reply, action, pending = self.brain.process_input(text)
        if pending:
            self.speak("🤔 让我想想…", 8000)
            return
        if reply: self.speak(reply, duration=5000)
        self._do_action(action)

    def _on_llm_reply(self, text, action, tool=None, mood=None):
        """大模型异步回复送达（主线程）：先说话，再执行动作 / 工具；mood 决定语调。

        多步工具链：工具执行后如果有"结果文本"，会把它作为补充信息再问大模型一轮，
        让它接着安排（例如 找文件 → 再决定用哪个程序打开）。最多追加 1 轮，避免死循环。
        """
        if text:
            self.speak(text, duration=min(12000, 3000 + len(text) * 120), mood=mood or None)
        self._do_action(action)
        result = self._run_tool(tool) if tool else None
        if result and self._tool_rounds < MAX_TOOL_ROUNDS and self.brain.llm.configured:
            self._tool_rounds += 1
            app_health.log(f"工具链第 {self._tool_rounds} 轮：{tool} -> {result[:80]}")
            note = (f"（系统提示）刚才工具 {tool} 的执行结果是：{result}\n"
                    f"请基于这个结果，用小猫口吻回主人一句（可再带一个 [action:] 或 [tool:] 标签）；"
                    f"如果没有要紧事，就只是一句简短的话，不要重复上面的内容。")
            self.brain.llm.ask_async(note, self.cat_name, self.affection,
                                     lambda t, a, tl, mo=None: self.llm_reply.emit(t, a or "", tl, mo or ""))

    def _run_tool(self, tool):
        """执行大模型请求的工具（白名单，安全可控）。返回给模型看的"结果文本"（没用工具就返回 None）。"""
        if not tool:
            return None
        name, arg = (tool if isinstance(tool, (tuple, list)) else (tool, ""))
        name = str(name).lower()
        arg = (arg or "").strip()
        try:
            if name == "open":
                url = ai.resolve_site(arg) or ai.resolve_site("打开" + arg)
                if url:
                    webbrowser.open(url)
                    msg = f"咻！打开 {url.split('/')[2]} 啦~"
                    self.speak(msg, 4000)
                    return f"已用浏览器打开 {url}"
                webbrowser.open(f"https://www.baidu.com/s?wd={quote(arg)}")
                self.speak(f"没找到「{arg}」这个站，我帮你搜一下~", 4000)
                return f"没有匹配的站点，改为搜索「{arg}」"
            if name == "search":
                webbrowser.open(f"https://www.baidu.com/s?wd={quote(arg)}")
                self.speak(f"搜好啦：{arg}", 4000)
                return f"已在浏览器搜索「{arg}」"
            if name == "find":
                found = ai.find_files(arg)
                self.remember_found(found)
                if found:
                    self._found_bubble(found)
                    return ("找到这些文件：" + "；".join(
                        f"{i}. {p}" for i, p in enumerate(found, start=1))
                        + "（已经记住，可用 openfile 序号打开）")
                self.speak(f"没找到跟「{arg}」有关的文件呢", 4000)
                return f"没有找到与「{arg}」有关的文件"
            if name == "openfile":
                which = int(arg) if str(arg).strip().isdigit() else (ai.parse_open_index(str(arg)) or 1)
                if str(arg).strip().isdigit() or which > 1:
                    self.open_found(which)
                    return f"已打开第 {which} 个搜索结果"
                found = ai.find_files(str(arg))
                if found:
                    self.remember_found(found)
                    self.open_found(1)
                    return f"搜索「{arg}」并打开了第一个：{found[0]}"
                self.speak(f"没找到「{arg}」呢", 4000)
                return f"没有找到名为「{arg}」的文件"
            if name == "status":
                self.system_status_reply()
                return "已经汇报了电量/内存/网络"
            if name == "screenshot":
                msg = self.take_screenshot()
                self.speak(msg, 6000)
                return "已截图并保存到桌面"
            if name == "volume":
                self.do_volume(arg)
                return f"已调整音量（{arg}）"
            if name == "clipboard":
                self.clipboard_ai(arg or "summary")
                return f"已开始处理剪贴板（{arg or 'summary'}）"
        except Exception as e:
            app_health.log(f"工具执行失败 {tool}: {e}", level=40)
            self.speak(f"喵…这件事我没做成（{e.__class__.__name__}）", 4000)
            return f"工具执行出错：{e.__class__.__name__}"
        return None

    # ---- 拖拽文件：拖到猫身上就能处理（由 dropEvent 调用）----
    TEXT_EXT = {".txt", ".md", ".markdown", ".py", ".json", ".csv", ".log", ".ini",
                ".yaml", ".yml", ".html", ".htm", ".js", ".ts", ".css", ".xml", ".bat", ".ps1"}
    MAX_DROP_BYTES = 200_000

    def handle_dropped_files(self, paths):
        """处理拖进来的文件：文本类读内容交给大模型，其它只给路径。"""
        path = paths[0]
        name = os.path.basename(path)
        if len(paths) > 1:
            self.speak(f"喵…一次一个就好啦，先看「{name}」", 4000)
        if os.path.isdir(path):
            try:
                items = os.listdir(path)[:12]
            except Exception:
                items = []
            listing = "、".join(items) or "（空文件夹）"
            return self.speak(f"「{name}」里有：{listing}", 8000)
        ext = os.path.splitext(path)[1].lower()
        if not self.brain.llm.configured:
            return self.speak("喵…还没配置 API key，我读不出文件内容（config.json）", 6000)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if ext in self.TEXT_EXT and size <= self.MAX_DROP_BYTES:
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read(self.MAX_DROP_BYTES)
            except Exception as e:
                return self.speak(f"喵…读不了这个文件（{e.__class__.__name__}）", 4000)
            prompt = (f"主人把文件「{name}」拖给我了。请用中文先用一句话说这是什么，"
                      f"再总结 2~3 个要点；如果是代码就说明它做什么。\n\n内容：\n{content[:6000]}")
            # 记住这个文件：之后主人可以直接追问（"这个函数做什么"），
            # 想清掉说"忘掉这个文件"即可
            self._file_context = {"name": name, "path": path, "text": content[:4000]}
            self._drop_pending = name
            self.speak(f"🤔 我看看「{name}」…", 6000)
            return self.brain.llm.ask_async(
                prompt, self.cat_name, self.affection,
                lambda t, a, tl, mo=None: self.llm_reply.emit(t, a or "", tl, mo or ""))
        self.speak(f"「{name}」是 {ext or '未知类型'} 文件（{size // 1024}KB），"
                   f"我打不开它内部，但可以：说“用记事本打开”或“找出它的位置”", 8000)

    # ---- 剪贴板历史（最近 10 条，只在内存）----
    def _poll_clipboard(self):
        """定时看剪贴板有没有新内容，有就记进历史。"""
        try:
            cur = (self.clipboard_text() or "").strip()
        except Exception:
            return
        if cur and cur != self._clip_seen:
            self._clip_seen = cur
            self.clip_history.push(cur)
            app_health.log(f"剪贴板历史 +1（共 {len(self.clip_history)} 条，{len(cur)} 字）")

    def show_clip_history(self):
        """用气泡列出剪贴板历史。"""
        if not len(self.clip_history):
            return self.speak("喵…我还没看到你复制过东西呢（我在后台盯着剪贴板）", 5000)
        rows = []
        for i in range(1, len(self.clip_history) + 1):
            t = self.clip_history.get(i)
            one = " ".join(t.split())
            head = one if len(one) <= 40 else one[:40] + "…"
            rows.append(f"{i}. {head}")
        return self.speak("最近复制的（说“用第2条翻译”就行）：\n" + "\n".join(rows), 12000)

    def use_clip_item(self, index, action="translate"):
        """对历史里的第 index 条做动作：翻译/总结/解释/润色/回复/念/存桌面。"""
        text = self.clip_history.get(index)
        if text is None:
            n = len(self.clip_history)
            return self.speak(f"喵…我只记了 {n} 条，没有第 {index} 条呀", 5000)
        if action == "read":
            self.bubble.show_message("剪贴板第%d条是：「%s」" % (index, text[:300]), 9000)
            self.update_ui_positions()
            if self.voice_on:
                self.speaker.say(text[:200], mood="cozy")
            return None
        if action == "save":
            self._clip_override = text
            try:
                return self.speak(self.clipboard_save(), 6000)
            finally:
                self._clip_override = None
        return self._clip_now(text, action)

    def _clip_now(self, text, mode="summary"):
        """把一个指定文本交给大模型处理（剪贴板历史用；不影响当前剪贴板）。"""
        modes = {
            "translate": "把下面这段文字翻译成中文；如果原文已经是中文，就翻译成英文。只给译文，不要解释",
            "summary": "用中文把下面这段内容总结成 2~3 句要点，直接给要点，不要客套话",
            "explain": "用中文通俗地解释下面这段内容（术语可以打比方），控制在 3 句以内",
            "polish": "把下面这段文字改写得通顺、得体、没有错别字，直接给改好的版本",
            "reply": "帮我用简短、客气、自然的语气回复下面这条消息，直接给回复内容",
        }
        if mode not in modes:
            mode = "summary"
        if not self.brain.llm.configured:
            return self.speak("喵呜…还没配置 API key，我读不出内容（config.json）", 6000)
        self.speak("🤔 让我看看这一条…", 6000)
        return self.brain.llm.ask_async(
            f"{modes[mode]}：\n\n{text[:1200]}", self.cat_name, self.affection,
            lambda t, a, tl, mo=None: self.llm_reply.emit(t, a or "", tl, mo or ""))

    # ---- 看家 / 专注模式 ----
    def _focus_tick(self):
        """每 20 秒判一次：离开→去睡、回来→打招呼、久坐→提醒休息。"""
        if not self.focus_mode:
            return
        idle = app_health.idle_seconds()
        if idle < 60:
            busy_now = True
        else:
            busy_now = False
        if busy_now:
            if self._was_away:
                self._busy_since = time.time()
        decision = app_health.focus_decision(
            idle, time.time() - self._busy_since,
            time.time() - self._last_break_ts if self._last_break_ts else 9e9,
            was_away=self._was_away)
        if decision == "away":
            if not self._was_away:
                app_health.log("看家模式：主人离开，去睡觉")
            self._was_away = True
            if self.state != 'sleep' and self._can_sleep():
                self._go_sleep()
        elif decision == "back":
            self._was_away = False
            self._busy_since = time.time()
            self.speak(random.choice(["主人回来啦！我等好久了喵~", "喵！欢迎回来~"]), 4000, mood="happy")
            self._do_hop(True)
        elif decision == "rest":
            self._busy_since = time.time()
            self._last_break_ts = time.time()
            self.speak(random.choice(["主人坐了快一小时了，起来动动吧喵~",
                                      "休息一下眼睛好不好？我陪你去倒杯水喵~"]), 7000, mood="alert")
            if self.voice_on:
                self.speaker.say("主人该休息一下啦", mood="alert")
        else:
            self._was_away = False

    def set_avoid_fullscreen(self, enable=None):
        """开关"全屏应用时自动避让"（玩游戏/看视频/演示时不要打扰主人）。"""
        self.avoid_fullscreen = (not self.avoid_fullscreen) if enable is None else bool(enable)
        self.pet_data["avoid_fullscreen"] = 1 if self.avoid_fullscreen else 0
        self._save_now()
        if self.avoid_fullscreen:
            return "好耶！主人全屏玩游戏看视频的时候，我会自己躲起来喵~"
        return "知道啦，以后全屏时我也会陪着你"

    def _fullscreen_tick(self):
        """每 4 秒看一眼前台是不是全屏应用：是就躲起来，退出全屏再回来。"""
        if not self.avoid_fullscreen:
            return
        try:
            full = app_health.fullscreen_active()
        except Exception:
            return
        if full and self.isVisible() and not self._dragging:
            self.hide()
            self.bubble.hide()
            self.input_box.hide()
            self._hidden_by_fullscreen = True
            app_health.log("检测到全屏应用，先躲起来")
        elif not full and getattr(self, "_hidden_by_fullscreen", False):
            self._hidden_by_fullscreen = False
            self.show()
            app_health.log("全屏结束，出来啦")

    def set_focus_mode(self, enable=None):
        """开关看家/专注模式。"""
        self.focus_mode = (not self.focus_mode) if enable is None else bool(enable)
        self.pet_data["focus_mode"] = 1 if self.focus_mode else 0
        self._was_away = False
        self._busy_since = time.time()
        if self.focus_mode:
            if self._focus_timer is None:
                self._focus_timer = QTimer(self)
                self._focus_timer.timeout.connect(self._focus_tick)
            self._focus_timer.start(20000)
            self._save_now()
            return "看家模式开啦：你离开我会去睡，回来我会打招呼，久坐我会提醒你喵~"
        if self._focus_timer:
            self._focus_timer.stop()
        self._save_now()
        return "看家模式关掉啦"

    # ---- 音量（发系统媒体键）----
    def do_volume(self, action):
        """按系统音量键：up / down / mute。返回给主人的话。"""
        action = str(action or "").strip().lower()
        if action in ("大", "大声", "up", "+"):
            action = "up"
        elif action in ("小", "小声", "down", "-"):
            action = "down"
        if action not in ("up", "down", "mute"):
            return self.speak("喵？主人是想让我把声音调大、调小，还是静音呀", 4000)
        times = tools.volume(action)
        if not times:
            return self.speak("喵…调音量失败了，主人用键盘上的音量键试试？", 4000)
        word = {"up": "调大", "down": "调小", "mute": "静音"}[action]
        return self.speak(f"喵～音量已经{word}啦", 3000)

    # ---- 剪贴板（选中文字 → 大模型处理）----
    def clipboard_ai(self, mode="summary"):
        """
        读取剪贴板交给大模型：translate 翻译 / summary 总结 / explain 解释 / polish 润色 / reply 帮回。
        未配置 key 时给提示，剪贴板为空时也提醒，不会静默失败。
        """
        mode = (mode or "summary").strip().lower()
        modes = {
            "translate": "把下面这段文字翻译成中文；如果原文已经是中文，就翻译成英文。只给译文，不要解释",
            "summary": "用中文把下面这段内容总结成 2~3 句要点，直接给要点，不要客套话",
            "explain": "用中文通俗地解释下面这段内容（术语可以打比方），控制在 3 句以内",
            "polish": "把下面这段文字改写得通顺、得体、没有错别字，直接给改好的版本",
            "reply": "帮我用简短、客气、自然的语气回复下面这条消息，直接给回复内容",
        }
        if mode not in modes:
            mode = "summary"
        text = self.clipboard_text().strip()
        if not text:
            return self.speak("喵…剪贴板是空的，主人先复制一段文字再叫我吧", 5000)
        if not self.brain.llm.configured:
            return self.speak("喵呜…还没配置 API key，我读不出剪贴板里的内容（config.json）", 6000)
        snippet = text[:1200]
        prompt = f"{modes[mode]}：\n\n{snippet}"
        self.speak("🤔 让我看看剪贴板…", 6000)
        self.brain.llm.ask_async(prompt, self.cat_name, self.affection,
                                 lambda t, a, tool, mood=None: self.llm_reply.emit(t, a or "", tool, mood or ""))

    def feed_cat(self):
        """喂食：任何时候都能喂（不再因为好感度满而拒绝）；好感度只在未满时增长。"""
        if self.affection < 100:
            self.affection = min(100, self.affection + 15)
            msg = f"吧唧吧唧… 好感度: {self.affection}/100"
        else:
            msg = "吧唧吧唧…虽然好感度满了，但小鱼干还是很好吃的嘛！"
        self.change_state('eat', 'action_and_return')
        self.speak(msg, mood='happy')

    def check_status(self):
        mood = "超级粘人" if self.affection >= 80 else ("心情不错" if self.affection >= 50 else "有点自闭")
        self.speak(f"【{self.cat_name}】\n心情：{mood}\n好感度：{self.affection}/100", 5000)

    def _start_pace(self, short=False):
        if self.pacing: return False
        walk_state = 'walk' if self.animations.get('walk') else 'idle'
        wa = QApplication.primaryScreen().availableGeometry()
        margin = 8
        left, right = wa.left() + margin, wa.right() - self.width() - margin
        if right <= left: return False
        
        x = min(max(self.x(), left), right)
        if short or random.random() < 0.45:
            lo = max(left, x - random.randint(140, 420))
            hi = min(right, x + random.randint(140, 420))
            target = random.choice([lo, hi]) if hi - lo > 100 else random.choice([left, right])
        else:
            target = random.choice([left, right])
            
        if target == x: target = right if x == left else left
        
        y = min(self.y(), wa.bottom() - self.height() - 2)
        self.move(int(x), y)
        self.change_state(walk_state, 'loop')
        self.pacing = True
        self._pace_x = float(x)
        self._pace_target = target
        self.facing = 1 if target > x else -1
        self._last_walk_t = time.time()          # 记录散步时间（用于冷却）
        return True

    def _advance_walk(self):
        """散步位移（由 _on_fx_tick 每帧调用；移动与重绘同频，避免残影拖尾）。"""
        step = self.WALK_STEP * self.FX_MS / 25.0     # 换算成每帧步长，保持原速度
        self._pace_x += step * self.facing
        self.move(int(round(self._pace_x)), self.y())
        self.update_ui_positions()
        if (self.facing > 0 and self._pace_x >= self._pace_target) or \
           (self.facing < 0 and self._pace_x <= self._pace_target):
            self._stop_pace()

    def _stop_pace(self):
        was_pacing = self.pacing
        self.pacing = False
        self.hover = 0.0
        if was_pacing:
            self.transition_to('idle', hold=1)   # 走→站定，软着陆
            self._schedule_behavior()            # 走完继续排下一次随机小动作

    def _go_sleep(self, auto_wake_ms=None):
        if self.state == 'sleep': return
        if self.pacing: self._stop_pace()
        self._sleep_token += 1
        tok = self._sleep_token
        self._last_sleep_t = time.time()        # 记录睡眠时间（用于冷却）
        self._play_action('sleep_fall')         # 有入睡片段就播，否则直接进睡姿
        if auto_wake_ms is not None:
            QTimer.singleShot(auto_wake_ms, partial(self._auto_wake, tok))

    def _auto_wake(self, token):
        if token == self._sleep_token and self.state == 'sleep' and self.isVisible():
            self._wake_from_sleep()
            self._schedule_behavior(random.randint(20000, 40000))   # 醒来后安静待会儿

    def _wake_from_sleep(self):
        if self.state != 'sleep': return
        self._sleep_token += 1
        if self.animations.get('wake'):
            self.change_state('wake', 'action_and_return')   # 真实起身片段
        else:
            self.transition_to('idle', hold=2)
        self.speak("喵呜…睡饱啦，主人好！", 3200, mood="sleepy")

    def _play_action(self, state):
        """播放一次性动作片段（正向播完 + 短暂定格 → 回发呆）。"""
        if state == 'sleep_fall':
            # 入睡：优先用 wake 片段倒着播会失真，这里直接用睡姿循环起手
            self.change_state('sleep', 'loop')
            return
        if not self.animations.get(state):
            return
        self.change_state(state, 'action_and_return')

    def _schedule_behavior(self, delay_ms=None):
        if self._dragging:
            return
        # 真实一点：小猫大部分时间只是安静待着，隔 20~45 秒才做一个小动作
        self.behavior_timer.start(delay_ms if delay_ms is not None else random.randint(20000, 45000))

    def _can_sleep(self):
        """睡眠冷却：刚睡过 2 分钟内不再犯困（避免'睡了醒、醒了又睡'）。"""
        return (time.time() - self._last_sleep_t) > 120

    def _can_walk(self):
        """散步冷却：30 秒内不重复散步。"""
        return (time.time() - self._last_walk_t) > 30

    def _look_around(self):
        """左右张望：几次改变朝向，然后回默认朝向。"""
        for delay, face in ((0, -1), (550, 1), (1100, -1), (1650, 1)):
            QTimer.singleShot(delay, partial(self._set_facing_later, face))
        self._schedule_behavior()

    def _on_behavior_fire(self):
        if self._dragging or self.pacing or not self.isVisible():
            self._schedule_behavior(15000)
            return
        if self.state == 'sleep':
            self._schedule_behavior(15000)
            return
        if self.state != 'idle':
            self._schedule_behavior(10000)
            return

        r = random.random()
        hour = datetime.now().hour
        night = hour >= 23 or hour <= 3
        mealtime = (11 <= hour <= 13) or (17 <= hour <= 19)

        # 深夜：更倾向打盹（但仍有冷却，不会睡了醒、醒了又睡）
        if night and r < 0.30 and self._can_sleep():
            self.speak("夜深啦，主人也早点休息吧…哈欠~", 4000)
            self._go_sleep(random.randint(40000, 90000))
            return
        # 饭点：偶尔喊饿
        if mealtime and r < 0.15:
            self.speak("肚子有点饿了，到饭点了吧！", 4000)
            self._schedule_behavior(30000)
            return

        # 白天行为池（安静为主，动作都来自新素材）
        if r < 0.15 and self._can_walk():
            if self._start_pace():
                return                       # 走完由 _stop_pace 重新排期
        elif r < 0.30:
            self._look_around()
            return
        elif r < 0.42:
            self._play_action('knead')       # 踩奶撒娇
        elif r < 0.52:
            self._play_action('yawn')        # 打哈欠
        elif r < 0.60:
            self._play_action('stretch')     # 伸懒腰
        elif r < 0.66:
            self._play_action('pounce')      # 扑一下
        elif r < 0.70:
            self._play_action('turn')        # 转身
        elif r < 0.92:
            self.speak(random.choice(["喵~", "咕噜咕噜…", "看什么呢，认真工作！",
                                      "呼噜…今天天气真好", "摸摸我嘛~"]), 3000)
        elif r < 0.97 and self._can_sleep():
            self.speak("有点儿困了…眯一会儿 Zzz", 2500)
            self._go_sleep(random.randint(30000, 70000))
            return
        self._schedule_behavior()

    def _set_facing_later(self, face):
        if self.state == 'idle' and not self.pacing:
            self.facing = face

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            file_path = paths[0] if paths else urls[0].toLocalFile()
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                self.speak(f"好漂亮的画！ ({os.path.basename(file_path)})")
                self._do_hop(True)
            elif ext in ['.mp3', '.wav', '.flac']:
                os.startfile(file_path)
                self.speak("喵~ 正在为你放歌！")
            elif ext in self.TEXT_EXT or os.path.isdir(file_path):
                # 文本类/文件夹：交给大模型读内容、总结要点（没配 key 时会自己提示）
                self.handle_dropped_files(paths or [file_path])
            else:
                self.speak(f"这是什么？ {os.path.basename(file_path)}"
                           f"（说“用记事本打开”我可以打开它）", 5000)

    def _save_now(self):
        self.pet_data.update({'affection': self.affection, 'name': self.cat_name, 'last_pos_x': self.x(), 'last_pos_y': self.y(), 'facing': self.facing})
        # 提醒与"最近找到的文件"也一起存，重启后不丢
        now = time.time()
        self.pet_data['reminders'] = [
            {"due": d, "msg": m, "spec": s} for (_t, d, m, s) in self._reminders if d > now]
        self.pet_data['last_found'] = [p for p in self._last_found if os.path.exists(p)]
        ConfigManager.save_data(self.pet_data)

    def restore_state(self):
        """启动时恢复：未到期的提醒（含每天/每周的重复日程）+ 最近找到的文件。"""
        now = time.time()
        restored = 0
        for item in self.pet_data.get('reminders') or []:
            try:
                due, msg = float(item.get('due', 0)), str(item.get('msg', ''))
            except (TypeError, ValueError):
                continue
            spec = item.get('spec') or {"kind": "once", "delay": max(1, int(due - now)),
                                        "msg": msg, "at": None, "weekdays": None}
            if spec.get("kind") in ("daily", "weekly"):
                # 重复日程：不管上次是什么时候，按规则重排下一次
                due = tools.next_due(spec)
            elif due <= now:
                continue
            self._remind_token += 1
            token = self._remind_token
            self._reminders.append((token, due, msg, spec))
            QTimer.singleShot(max(200, int((due - now) * 1000)),
                              partial(self._fire_schedule, token))
            restored += 1
        found = [p for p in (self.pet_data.get('last_found') or []) if os.path.exists(p)]
        if found:
            self._last_found = found[:self.MAX_OPEN]
        if restored:
            app_health.log(f"恢复了 {restored} 个提醒")
            self.speak(f"喵~ 我把之前的 {restored} 个提醒找回来啦", 5000)
        return restored

    def safe_quit(self):
        self._save_now()
        QApplication.quit()

    def closeEvent(self, event):
        self._save_now()
        event.accept()


def _poll_single_instance(server, pet):
    """轮询单实例唤醒端口：收到 show 就把猫显示出来。"""
    try:
        conn, _addr = server.accept()
    except BlockingIOError:
        return
    except Exception:
        return
    try:
        with conn:
            conn.settimeout(0.4)
            try:
                conn.recv(16)
            except Exception:
                pass
    finally:
        if not pet.isVisible():
            pet.show()
        pet.raise_()
        pet._do_hop(False)
        pet.speak("我在这儿呢喵~", 3000, mood="happy")


def startup_guard():
    """启动前的保障：日志 + 工作目录 + 单实例。返回 (是否继续运行, 单实例锁)。"""
    try:
        os.chdir(app_health.app_dir())     # 保证 config/pet_data/frames_opt 都能找到
    except Exception:
        pass
    app_health.setup_logging()
    app_health.log("=== 多多启动 ===" + " ".join(sys.argv[1:])
                   + ("（打包版）" if getattr(sys, "frozen", False) else "（源码版）"))
    lock = app_health.SingleInstance("DuoduoPet")
    if not lock.acquire():
        app_health.log("已有实例在运行，通知它显示并退出")
        app_health.SingleInstance.notify_existing()
        print("多多已经在运行啦，我把它叫出来啦~")
        return False, lock
    return True, lock


if __name__ == '__main__':
    ok, _lock = startup_guard()
    if not ok:
        sys.exit(0)
    app = QApplication(sys.argv)
    QApplication.setQuitOnLastWindowClosed(False)
    # 素材检查：缺素材时给出明确指引（仓库里不带 70MB 素材，必须先准备）
    _missing = missing_assets_message()
    if _missing:
        app_health.log("启动中止：缺少帧素材", level=40)
        try:
            from PyQt6.QtWidgets import QMessageBox
            box = QMessageBox()
            box.setWindowTitle("多多 · 缺少动画素材")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(_missing)
            box.exec()
        except Exception:
            print(_missing)
        sys.exit(2)
    pet = PetCat()
    pet.show()
    # 配置自检：有问题就在气泡里说清楚
    problems = app_health.check_config(ai.load_config())
    if problems:
        app_health.log("配置自检发现问题: " + "；".join(problems), level=30)
        pet.speak("喵…配置文件有点小问题：\n" + "\n".join("• " + p for p in problems), 12000)
    pet.restore_state()
    # 单实例唤醒：收到 "show" 就把猫显示出来
    try:
        import socket as _socket
        _srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        _srv.bind(("127.0.0.1", 45871))
        _srv.listen(1)
        _srv.setblocking(False)
        _tick = QTimer()
        _tick.timeout.connect(lambda: _poll_single_instance(_srv, pet))
        _tick.start(700)
    except Exception as e:
        app_health.log(f"单实例唤醒端口未启用：{e}", level=30)
    sys.exit(app.exec())