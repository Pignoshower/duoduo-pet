# -*- coding: utf-8 -*-
"""
pet_tools.py —— 桌宠的"系统能力"工具层（不依赖 PyQt，便于单元测试）
=====================================================================
包含三块能力：

1. Speaker：语音播报（TTS）
   优先 pyttsx3 → win32com(SAPI) → PowerShell System.Speech（零依赖兜底）。
   全部异步执行，不阻塞界面；支持停止；支持 dry_run（测试用，不出声）。

2. system_status()：电量 / 内存 / CPU / 网络
   优先 psutil，没装则用 PowerShell(CIM) 兜底，返回统一字典。

3. parse_reminder()：把"25分钟后提醒我喝水"解析成 (秒数, 事项)
   支持 秒/分钟/小时，以及"番茄钟"（默认 25 分钟）。
"""
import ctypes
import json
import os
import queue as _queue
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

# =====================================================================
# 0. 语音配置（从 config.json 的 "tts" 段读取，缺省值在下面）
# =====================================================================
def _app_dir():
    """程序目录：打包成 exe 后用 exe 所在目录（__file__ 会指向临时解包目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_app_dir(), "config.json")

DEFAULT_TTS_CONFIG = {
    "engine": "auto",                    # auto | edge | onecore | sapi | powershell
    "voice": "zh-CN-XiaoyiNeural",       # edge 神经语音（活泼少女音）；可换 XiaoxiaoNeural 等
    "rate": "+8%",                       # edge 语速
    "pitch": "+10Hz",                    # edge 音调（越高越可爱，注意别太高会发尖）
    "volume": "+0%",
    "edge_proxy": "auto",                # auto=自动用系统代理（实测直连 11s / 代理 1s）；也可填 URL 或空串禁用
    "onecore_voice": "Yaoyao",           # OneCore 音色（按名字模糊匹配，如 Yaoyao/Xiaoxiao/Huihui）
    "onecore_pitch": "+30%",             # OneCore 音调（SSML prosody，实测真的生效）
    "onecore_rate": "1.2",               # OneCore 语速倍率（1.0 = 原速）
    "sapi_pitch": 20,                    # SAPI 音调抬高百分比（变调重采样实现）
    "sapi_speed": 1,                     # SAPI 语速档位（-10~10）
    "cache": 80,                         # 语音磁盘缓存条数
}

# OneCore（WinRT）音色中文名：装了"自然语音"后 Xiaoxiao/Xiaoyi 等会出现在这里
ONECORE_CN = {
    "Xiaoxiao": "晓晓（自然女声）",
    "Xiaoyi": "晓伊（自然少女音）",
    "Xiaoshuang": "小双（自然童声）",
    "Yunxi": "云希（自然少年音）",
    "Yunjian": "云健（自然男声）",
    "Yaoyao": "瑶瑶（活泼女声）",
    "Huihui": "慧慧（女声）",
    "Kangkang": "康康（男声）",
}

# 情绪 → 语调偏移（叠加在基础配置上；三个引擎都能换算）
#   rate  : 语速偏移（百分点，edge 用 %，onecore 换算成倍率，sapi 换算成档位）
#   pitch : 音调偏移（edge 用 Hz，onecore 用百分点，sapi 换算成变调比例）
MOODS = {
    "normal":  {"rate": 0,   "pitch": 0,   "cn": "平常"},
    "happy":   {"rate": 6,   "pitch": 12,  "cn": "开心"},
    "excited": {"rate": 12,  "pitch": 18,  "cn": "兴奋"},
    "cozy":    {"rate": -6,  "pitch": -2,  "cn": "撒娇"},
    "sleepy":  {"rate": -12, "pitch": -8,  "cn": "困倦"},
    "alert":   {"rate": 8,   "pitch": 6,   "cn": "提醒"},
    "sorry":   {"rate": -8,  "pitch": -6,  "cn": "委屈"},
    "proud":   {"rate": 4,   "pitch": 8,   "cn": "得意"},
}
MOOD_ALIAS = {
    "开心": "happy", "高兴": "happy", "兴奋": "excited", "激动": "excited",
    "撒娇": "cozy", "温柔": "cozy", "困": "sleepy", "困倦": "sleepy", " sleepy": "sleepy",
    "提醒": "alert", "得意": "proud", "骄傲": "proud", "委屈": "sorry", "道歉": "sorry",
}

VOICE_CN = {
    "zh-CN-XiaoyiNeural": "晓伊（活泼少女音）",
    "zh-CN-XiaoxiaoNeural": "晓晓（温柔女声）",
    "zh-CN-YunxiNeural": "云希（少年音）",
    "zh-CN-YunjianNeural": "云健（男声）",
    "zh-CN-YunxiaNeural": "云夏（童声）",
    "zh-CN-YunyangNeural": "云扬（播报男声）",
    "zh-CN-liaoning-XiaobeiNeural": "晓北（东北口音）",
    "zh-CN-shaanxi-XiaoniNeural": "晓妮（陕西口音）",
}


def _voice_cn(name):
    """音色名的中文说明（没有就原样返回）。"""
    return VOICE_CN.get(name, name)


def _num(text, default=0):
    """从 "+8%" / "+10Hz" / 1.2 这类值里取出数字。"""
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text))
    return float(m.group()) if m else float(default)


def _pct(value):
    """把数字格式化成带符号的百分数：-4 → '-4%'。"""
    return f"{int(round(value)):+d}%"


def _hz(value):
    """把数字格式化成带符号的 Hz：12 → '+12Hz'。"""
    return f"{int(round(value)):+d}Hz"


def mood_info(mood):
    """把情绪名（支持中文别名）规范成 MOODS 里的键。"""
    key = str(mood or "normal").strip().lower()
    key = MOOD_ALIAS.get(str(mood or "").strip(), MOOD_ALIAS.get(key, key))
    return key if key in MOODS else "normal"


def mood_cn(mood):
    return MOODS[mood_info(mood)]["cn"]


def load_tts_config(path=CONFIG_PATH):
    """读 config.json 的 tts 段；读不到就用默认值。"""
    cfg = dict(DEFAULT_TTS_CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        tts = raw.get("tts") or {}
        cfg.update({k: v for k, v in tts.items() if v is not None})
    except Exception:
        pass
    return cfg


# =====================================================================
# 1. 语音播报
# =====================================================================
class Speaker:
    """
    语音播报器：按"最像人 → 最不挑环境"的顺序挑引擎，走【常驻朗读线程 + 队列】。

    引擎优先级：
      1. edge  —— 微软 Edge 神经网络语音（edge-tts），最自然、可指定"童声/少女音"，
                  需要联网与 `pip install edge-tts`；合成的 mp3 用 Windows MCI 播放（零额外依赖），
                  并且带磁盘缓存（同一句话第二次秒出，不重复联网）。
      2. sapi  —— Windows 自带的 SAPI5（如 Microsoft Huihui），会自动挑中文女声，
                  并用 SSML 把音调抬高、语速略快，听起来更年轻可爱。
      3. powershell —— 零依赖兜底（System.Speech）。
    """

    # 中文女声优先级（越靠前越"可爱/年轻"）
    SAPI_PREFER = ("xiaoxiao", "xiaoyi", "xiaoshuang", "huihui", "yaoyao",
                   "kangkang", "chinese", "zh-cn", "zh_cn")
    # Edge 神经语音候选（服务端实际存在的；主人可在 config.json 里改 tts.voice）
    # 注意：zh-CN-XiaoshuangNeural 在服务端并不存在（会报 NoAudioReceived），别写进候选
    EDGE_CUTE = ("zh-CN-XiaoyiNeural",              # 晓伊：活泼少女音（默认）
                 "zh-CN-XiaoxiaoNeural",            # 晓晓：温柔女声
                 "zh-CN-YunxiNeural",               # 云希：少年音
                 "zh-CN-liaoning-XiaobeiNeural",    # 晓北：东北口音，很好玩
                 "zh-CN-shaanxi-XiaoniNeural")      # 晓妮：陕西口音

    def __init__(self, config=None):
        self._lock = threading.Lock()
        self._queue = _queue.Queue()
        self._worker = None
        self._proc = None          # powershell 兜底时当前进程
        self._mci_alias = None     # edge 播放时的 MCI 别名
        self.cfg = dict(DEFAULT_TTS_CONFIG)
        if config:
            self.cfg.update({k: v for k, v in config.items() if v is not None})
        self._engine = self._detect()
        self._edge_ok = None       # 首次成功后记住 edge 可用
        self._onecore_cache = None  # OneCore 音色列表缓存
        self._oc_proc = None        # 常驻 OneCore 合成进程
        self._oc_queue = None
        self._oc_lock = threading.Lock()
        self._playing_wav = None
        self.enabled = False       # 默认关闭，由主程序/用户指令开启

    # ---- 引擎探测 ----
    def _detect(self):
        import importlib.util
        want = str(self.cfg.get("engine", "auto")).lower()
        if want in ("edge", "auto") and importlib.util.find_spec("edge_tts") is not None:
            return "edge"
        if want in ("onecore", "auto") and os.name == "nt":
            # Windows 10 起都带 OneCore 语音栈；真失败了会在朗读时自动降级
            return "onecore"
        if want in ("sapi", "auto") and importlib.util.find_spec("win32com") is not None:
            return "sapi"
        if importlib.util.find_spec("pyttsx3") is not None:
            return "pyttsx3"
        if os.name == "nt":
            return "powershell"
        return "none"

    @property
    def engine(self):
        return self._engine

    def available(self):
        return self._engine != "none"

    def label(self):
        """给主人看的引擎/音色说明。"""
        if self._engine == "edge":
            return f"edge·{_voice_cn(self.cfg.get('voice', self.EDGE_CUTE[0]))}"
        if self._engine == "onecore":
            return f"onecore·{self.onecore_voice_name()}"
        if self._engine == "sapi":
            return f"sapi·{self._sapi_label()}"
        return self._engine

    def _sapi_label(self):
        """SAPI 当前/候选音色名（懒查询一次并缓存）。"""
        name = getattr(self, "_sapi_name", None)
        if name:
            return name
        try:
            import win32com.client
            v = win32com.client.Dispatch("SAPI.SpVoice")
            rows = [t.GetDescription() for t in v.GetVoices()]
            for key in self.SAPI_PREFER:
                for r in rows:
                    if key in r.lower():
                        self._sapi_name = r
                        return r
            if rows:
                self._sapi_name = rows[0]
                return rows[0]
        except Exception:
            pass
        return "系统音色"

    # ---- 对外接口 ----
    def say(self, text, dry_run=False, mood=None):
        """把文本排入朗读队列（异步）。dry_run=True 时只返回能否朗读。mood 决定语调。"""
        text = (text or "").strip()
        if not text or not self.available():
            return False
        if dry_run:
            return True
        self._ensure_worker()
        self._queue.put((text, mood_info(mood)))
        return True

    # ---- 情绪 → 语调 ----
    def prosody(self, mood=None):
        """返回 (rate_str, pitch_str) 之类的引擎相关参数；各引擎自己换算。"""
        return MOODS[mood_info(mood)]

    def edge_prosody(self, mood=None):
        """edge 用 "+8%" / "+10Hz" 形式。"""
        m = self.prosody(mood)
        rate = _num(self.cfg.get("rate", "+8%")) + m["rate"]
        pitch = _num(self.cfg.get("pitch", "+10Hz")) + m["pitch"]
        return _pct(rate), _hz(pitch)

    def onecore_prosody(self, mood=None):
        """onecore 用 SSML："+30%" / 倍率 "1.2"。"""
        m = self.prosody(mood)
        rate_mult = max(0.5, min(2.0, _num(self.cfg.get("onecore_rate", "1.2"), 1.2)
                                * (1 + m["rate"] / 100.0)))
        pitch = _num(self.cfg.get("onecore_pitch", "+30%")) + m["pitch"]
        return f"{rate_mult:.2f}", _pct(pitch)

    def sapi_prosody(self, mood=None):
        """sapi 是"变调重采样 + 档位"，返回 (音调百分比, 语速档位)。"""
        m = self.prosody(mood)
        pitch = _num(self.cfg.get("sapi_pitch", 20)) + m["pitch"]
        speed = _num(self.cfg.get("sapi_speed", 1)) + round(m["rate"] / 20.0)
        return max(-40, pitch), int(max(-10, min(10, speed)))

    def stop(self):
        """清空待读内容，并尽力打断当前朗读。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break
        try:
            if self._engine == "sapi" and self._voice is not None:
                self._voice.Speak("", 3)      # 3 = 异步 + 清空当前
        except Exception:
            pass
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
            self._mci_stop()
            if getattr(self, "_playing_wav", None):       # SAPI 变调播放中
                try:
                    import winsound
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass

    # ---- 常驻朗读线程 ----
    def _ensure_worker(self):
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self):
        self._init_engine()
        while True:
            item = self._queue.get()
            if item is None:
                break
            if isinstance(item, tuple):
                text, mood = item
            else:                              # 兼容旧调用
                text, mood = item, "normal"
            try:
                self._speak_once(text, mood)
            except Exception:
                pass

    @staticmethod
    def _com_init():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

    def _init_engine(self):
        self._com_init()
        self._voice = None
        if self._engine == "sapi":
            try:
                import win32com.client
                self._voice = win32com.client.Dispatch("SAPI.SpVoice")
                self._sapi_name = self._pick_sapi_voice()
            except Exception:
                self._engine = "powershell"
        elif self._engine == "pyttsx3":
            try:
                import pyttsx3
                self._voice = pyttsx3.init()
                self._voice.setProperty("rate", 185)
            except Exception:
                self._engine = "powershell"

    # ---------- SAPI ----------
    def _pick_sapi_voice(self):
        """在已装 SAPI 音色里挑一个最合适的中文女声。"""
        try:
            tokens = list(self._voice.GetVoices())
        except Exception:
            return "默认音色"
        best, best_score, best_name = None, -1, "默认音色"
        for t in tokens:
            name = ""
            try:
                name = t.GetDescription()
            except Exception:
                continue
            low = name.lower()
            score = 0
            for i, key in enumerate(self.SAPI_PREFER):
                if key in low:
                    score = len(self.SAPI_PREFER) - i
                    break
            if score and best_score < score:
                best, best_score, best_name = t, score, name
        if best is not None:
            try:
                self._voice.Voice = best
            except Exception:
                pass
        return best_name

    def _speak_sapi(self, text, mood="normal"):
        """用 SAPI 朗读；把音调抬高一点，更接近小猫的可爱感。

        注意：实测 Microsoft Huihui Desktop **会忽略 SSML 的 <pitch> 标签**（0Hz 变化），
        所以这里走"合成到 wav → 重采样变调"的路线（真正改音高），
        同时先让 SAPI 放慢一点，抵消变调带来的加速。
        情绪通过 (音调百分比, 语速档位) 叠加，见 sapi_prosody()。
        """
        pitch, speed = self.sapi_prosody(mood)
        if pitch and self._shift_available():
            try:
                self._speak_sapi_shifted(text, pitch, speed)
                return
            except Exception:
                pass                       # 变调失败就退回普通朗读，绝不哑掉
        if self._voice is None:
            import win32com.client
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._sapi_name = self._pick_sapi_voice()
        try:
            self._voice.Rate = max(-10, min(10, int(speed)))
        except Exception:
            pass
        self._voice.Speak(text)            # 同步朗读；stop() 可打断

    @staticmethod
    def _shift_available():
        import importlib.util
        return (importlib.util.find_spec("numpy") is not None
                and importlib.util.find_spec("winsound") is not None
                and os.name == "nt")

    def _speak_sapi_shifted(self, text, pitch, speed):
        """合成到 wav → 重采样抬高音高 → winsound 播放（可用 stop() 打断）。"""
        import tempfile
        import wave
        import numpy as np
        import winsound
        import win32com.client

        factor = max(1.0, 1.0 + pitch / 100.0)
        rate = max(-10, min(10, speed - int(round((factor - 1.0) * 12))))
        raw_path = os.path.join(tempfile.gettempdir(), f"duoduo_sapi_{os.getpid()}.wav")
        out_path = raw_path.replace(".wav", "_shift.wav")

        if self._voice is None:
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._sapi_name = self._pick_sapi_voice()
        voice = self._voice
        try:
            voice.Rate = rate
        except Exception:
            pass
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(raw_path, 3)                    # 3 = SSFMCreateForWrite
        old_out = None
        try:
            old_out = voice.AudioOutputStream
        except Exception:
            pass
        try:
            voice.AudioOutputStream = stream
            voice.Speak(text, 0)                    # 同步写到文件
        finally:
            try:
                stream.Close()
            except Exception:
                pass
            if old_out is not None:
                try:
                    voice.AudioOutputStream = old_out
                except Exception:
                    pass

        with wave.open(raw_path, "rb") as w:
            params = w.getparams()
            frames = np.frombuffer(w.readframes(params.nframes), dtype=np.int16)
        if params.nchannels > 1:                    # 多声道逐声道变调
            frames = frames.reshape(-1, params.nchannels)
        n = len(frames)
        src = np.arange(n, dtype=np.float64)
        dst = np.arange(0, n, factor)
        if frames.ndim == 1:
            out = np.interp(dst, src, frames.astype(np.float64))
        else:
            out = np.stack([np.interp(dst, src, frames[:, c].astype(np.float64))
                            for c in range(frames.shape[1])], axis=1).ravel()
        with wave.open(out_path, "wb") as w:
            w.setnchannels(params.nchannels)
            w.setsampwidth(params.sampwidth)
            w.setframerate(params.framerate)
            w.writeframes(np.clip(out, -32768, 32767).astype(np.int16).tobytes())

        with self._lock:
            self._playing_wav = out_path
        try:
            winsound.PlaySound(out_path, winsound.SND_FILENAME)     # 阻塞
        finally:
            with self._lock:
                self._playing_wav = None
            for p in (raw_path, out_path):
                try:
                    os.remove(p)
                except Exception:
                    pass

    # ---------- Edge 神经语音 ----------
    def _cache_path(self, text, voice, tag=""):
        import hashlib
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "duoduo_tts")
        os.makedirs(d, exist_ok=True)
        h = hashlib.md5(f"{voice}|{tag}|{text}".encode("utf-8")).hexdigest()
        return os.path.join(d, h + ".mp3")

    @staticmethod
    def _trim_cache(keep=None):
        """限制缓存数量，避免临时目录越积越多。"""
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "duoduo_tts")
        if not os.path.isdir(d):
            return
        files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".mp3")]
        if keep is None:
            keep = int(DEFAULT_TTS_CONFIG.get("cache", 80))
        if len(files) <= keep:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for p in files[:len(files) - keep]:
            try:
                os.remove(p)
            except Exception:
                pass

    def _synth_edge(self, text, path, voice=None, mood=None):
        """edge-tts 合成语音到 mp3（同步等待完成）；mood 决定语速/音调。"""
        import asyncio
        import edge_tts

        voice = voice or self.cfg.get("voice") or self.EDGE_CUTE[0]
        rate, pitch = self.edge_prosody(mood)

        async def _go(proxy):
            kwargs = {}
            if proxy:
                kwargs["proxy"] = proxy
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch,
                                        volume=str(self.cfg.get("volume", "+0%")),
                                        **kwargs)
            await comm.save(path)

        proxy = self._edge_proxy()
        try:
            asyncio.run(_go(proxy))
        except Exception:
            if not proxy:
                raise
            # 代理不可用（比如主人的 VPN 关掉了）→ 退回直连，慢但能出声
            asyncio.run(_go(None))

    def _edge_proxy(self):
        """
        edge-tts 走的是 aiohttp，**不会**读 Windows 的 IE 代理设置，
        但实测差别巨大：本机直连 ~11s、走系统代理 ~1s。
        所以这里读注册表里的代理（auto 模式），拿不到就直连。
        """
        mode = self.cfg.get("edge_proxy", "auto")
        if mode in (None, "", False):
            return None
        if mode != "auto":
            return str(mode)
        if os.name != "nt":
            return None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
                if not winreg.QueryValueEx(k, "ProxyEnable")[0]:
                    return None
                server = winreg.QueryValueEx(k, "ProxyServer")[0]
            server = str(server).split(";")[0].strip()
            if not server:
                return None
            return server if "://" in server else "http://" + server
        except Exception:
            return None

    def _mci_play(self, path):
        """用 Windows MCI 播放 mp3（winsound 只认 wav，MCI 不需要额外依赖）。"""
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = "duoduo" + str(abs(hash(path)) % 100000)
        with self._lock:
            self._mci_alias = alias
        try:
            mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
            mci(f'play {alias} wait', None, 0, None)
        finally:
            try:
                mci(f'close {alias}', None, 0, None)
            except Exception:
                pass
            with self._lock:
                if self._mci_alias == alias:
                    self._mci_alias = None

    def _mci_stop(self):
        alias = self._mci_alias
        if not alias:
            return
        try:
            import ctypes
            ctypes.windll.winmm.mciSendStringW(f'stop {alias}', None, 0, None)
        except Exception:
            pass

    def _speak_edge(self, text, mood="normal"):
        rate, pitch = self.edge_prosody(mood)
        voice = self.cfg.get("voice")
        path = self._cache_path(text, voice, f"{rate}|{pitch}")
        if not os.path.exists(path):
            try:
                self._synth_edge(text, path, mood=mood)
            except Exception:
                # 配错音色（服务端没有）时，自动换一个候选，别让主人没声音
                last = None
                for cand in self.EDGE_CUTE:
                    if cand == voice:
                        continue
                    try:
                        self.cfg["voice"] = cand
                        path = self._cache_path(text, cand, f"{rate}|{pitch}")
                        self._synth_edge(text, path, voice=cand, mood=mood)
                        break
                    except Exception as e:
                        last = e
                        path = None
                if not path:
                    self.cfg["voice"] = self.EDGE_CUTE[0]
                    raise last or RuntimeError("edge 合成失败")
            self._trim_cache()
        self._mci_play(path)

    # ---------- OneCore / WinRT 语音 ----------
    # 系统里其实有两套语音栈：SAPI5（只有少数音色）和 OneCore（系统设置/讲述人用的那套，
    # 含 Yaoyao、Kangkang 以及装了"自然语音"后的 Xiaoxiao/Xiaoyi 等）。
    # SAPI5 不枚举用户级音色，所以"桥接注册表"行不通；这里直接用 WinRT 合成到 wav，
    # 不需要管理员、不需要装包，而且 SSML 的 prosody 音调是【真生效】的（实测 +30% → 实测 +20%）。
    # 为了省掉每次 ~1 秒的 PowerShell 启动开销，开一个【常驻合成进程】，用 stdin 下命令。
    ONECORE_DAEMON = r'''
$ErrorActionPreference = "Stop"
# 管道默认走系统 ANSI(GBK)，中文会乱 → 显式 UTF-8（Python 侧也是 UTF-8）
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
[void][Windows.Media.SpeechSynthesis.SpeechSynthesisStream,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.DataReader,Windows.Storage.Streams,ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    return $t.Result
}
$all = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
[Console]::Out.WriteLine("READY|" + $all.Count)
[Console]::Out.Flush()
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    if ($line -eq "EXIT") { break }
    if ($line -eq "LIST") {
        foreach ($v in $all) { [Console]::Out.WriteLine("VOICE|" + $v.DisplayName + "|" + $v.Language + "|" + $v.Gender) }
        [Console]::Out.WriteLine("END")
        [Console]::Out.Flush()
        continue
    }
    $parts = $line.Split("`t", 6)
    if ($parts[0] -ne "SAY" -or $parts.Count -lt 6) { [Console]::Out.WriteLine("ERR|bad command"); [Console]::Out.Flush(); continue }
    $out = $parts[1]; $want = $parts[2]; $pitch = $parts[3]; $rate = $parts[4]; $text = $parts[5]
    try {
        $voice = $all | Where-Object { $_.Language -like "zh*" -and $_.DisplayName -like ("*" + $want + "*") } | Select-Object -First 1
        if (-not $voice) { $voice = $all | Where-Object { $_.Language -like "zh*" } | Select-Object -First 1 }
        if ($voice) { $synth.Voice = $voice }
        $ssml = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN"><prosody pitch="' +
                $pitch + '" rate="' + $rate + '">' + $text + '</prosody></speak>'
        $stream = Await ($synth.SynthesizeSsmlToStreamAsync($ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
        $size = [uint32]$stream.Size
        $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
        Await ($reader.LoadAsync($size)) ([uint32]) | Out-Null
        $bytes = New-Object byte[] $size
        $reader.ReadBytes($bytes)
        $reader.Dispose()
        [System.IO.File]::WriteAllBytes($out, $bytes)
        [Console]::Out.WriteLine("OK|" + $synth.Voice.DisplayName + "|" + $size)
    } catch {
        $msg = $_.Exception.Message
        if ($_.Exception.InnerException) { $msg = $msg + " / " + $_.Exception.InnerException.Message }
        [Console]::Out.WriteLine("ERR|" + $msg)
    }
    [Console]::Out.Flush()
}
'''

    def _ps5(self):
        """Windows PowerShell 5.1 的路径（WinRT 投影只有 5.1 支持，pwsh 不行）。"""
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")

    @staticmethod
    def _cache_dir():
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "duoduo_tts")
        os.makedirs(d, exist_ok=True)
        return d

    # ---- 常驻合成进程 ----
    def _oc_ensure(self):
        """确保常驻合成进程在跑；起不来就抛异常（调用方降级到 SAPI）。"""
        if self._oc_proc is not None and self._oc_proc.poll() is None:
            return
        script = os.path.join(self._cache_dir(), "onecore_daemon.ps1")
        if not os.path.exists(script):
            with open(script, "w", encoding="utf-8") as f:
                f.write(self.ONECORE_DAEMON)
        self._oc_queue = _queue.Queue()
        self._oc_proc = subprocess.Popen(
            [self._ps5(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._oc_reader, args=(self._oc_proc,), daemon=True).start()
        ready = self._oc_read(timeout=30)
        if not ready or not str(ready).startswith("READY"):
            self._oc_proc = None
            raise RuntimeError(f"OneCore 合成进程启动失败：{ready}")

    def _oc_reader(self, proc):
        try:
            for raw in proc.stdout:
                self._oc_queue.put(raw.decode("utf-8", "ignore").strip())
        except Exception:
            pass
        self._oc_queue.put(None)          # 进程结束

    def _oc_read(self, timeout=20):
        try:
            return self._oc_queue.get(timeout=timeout)
        except _queue.Empty:
            return None

    def _oc_write(self, line):
        """给常驻进程写一条命令（不等待回应）。"""
        with self._oc_lock:
            self._oc_ensure()
            try:
                self._oc_proc.stdin.write((line + "\n").encode("utf-8"))
                self._oc_proc.stdin.flush()
            except Exception:
                self._oc_proc = None
                raise

    def _oc_send(self, line, timeout=25):
        """给常驻进程下一条命令并等它的一行回应。"""
        self._oc_write(line)
        return self._oc_read(timeout)

    @staticmethod
    def _safe_text(text):
        """去掉会破坏逐行协议的控制字符（制表符/换行）。"""
        return re.sub(r"[\t\r\n]+", " ", str(text)).strip()

    @staticmethod
    def _parse_voice_lines(lines):
        """把 LIST 的 "VOICE|名字|语言|性别" 行解析成 [(名字, 语言, 性别)]。"""
        rows = []
        for line in lines or []:
            if not str(line).startswith("VOICE|"):
                continue
            _, name, lang, gender = (str(line).split("|") + ["", "", ""])[:4]
            rows.append((name.strip(), lang.strip(), gender.strip()))
        return rows

    def onecore_voices(self):
        """列出 OneCore 里的音色：[(显示名, 语言, 性别)]，结果缓存。"""
        if self._onecore_cache:
            return self._onecore_cache
        lines = []
        try:
            self._oc_write("LIST")
            while True:
                line = self._oc_read(timeout=10)
                if not line or line == "END":
                    break
                lines.append(line)
        except Exception:
            lines = []
        self._onecore_cache = self._parse_voice_lines(lines)
        return self._onecore_cache

    def onecore_candidates(self):
        """换音色时用的候选：优先"中文女声"（更像小猫），只有一个女声就放宽到全部中文音色。"""
        zh = [v for v in self.onecore_voices() if v[1].lower().startswith("zh")]
        females = [v for v in zh if v[2].lower().startswith("female")]
        return females if len(females) >= 2 else zh

    def onecore_voice_name(self):
        """当前配置命中的 OneCore 音色显示名（找不到就取中文女声）。"""
        want = str(self.cfg.get("onecore_voice", "Yaoyao"))
        voices = self.onecore_voices()
        zh = [v for v in voices if v[1].lower().startswith("zh")]
        for v in zh:
            if want.lower() in v[0].lower():
                return v[0]
        for v in zh:
            if v[2].lower().startswith("female"):
                return v[0]
        return zh[0][0] if zh else want

    def _speak_onecore(self, text, mood="normal"):
        """WinRT 合成到 wav（带磁盘缓存）→ winsound 播放；情绪走 SSML prosody。"""
        import hashlib
        import winsound
        voice = self.onecore_voice_name()
        rate, pitch = self.onecore_prosody(mood)
        key = hashlib.md5(f"oc|{voice}|{pitch}|{rate}|{text}".encode("utf-8")).hexdigest()
        path = os.path.join(self._cache_dir(), key + ".wav")
        if not os.path.exists(path) or os.path.getsize(path) < 200:
            reply = self._oc_send(f"SAY\t{path}\t{voice}\t{pitch}\t{rate}\t" + self._safe_text(text))
            if not reply or not str(reply).startswith("OK"):
                raise RuntimeError(f"OneCore 合成失败：{reply}")
            self._trim_cache()
        with self._lock:
            self._playing_wav = path
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME)      # 阻塞播放，stop() 可打断
        finally:
            with self._lock:
                self._playing_wav = None

    # ---------- 分发 ----------
    def _speak_once(self, text, mood="normal"):
        if self._engine == "edge":
            try:
                self._speak_edge(text, mood)
                self._edge_ok = True
                return
            except Exception:
                # 断网/超时 → 降级到本机引擎，并把结果记住，免得每句都白等一次
                self._edge_ok = False
                self._engine = "onecore" if os.name == "nt" else "powershell"
        if self._engine == "onecore":
            try:
                self._speak_onecore(text, mood)
                return
            except Exception:
                self._engine = "sapi" if self._has_sapi() else "powershell"
        if self._engine == "pyttsx3" and self._voice is not None:
            self._voice.say(text)
            self._voice.runAndWait()
        elif self._engine == "sapi":
            self._speak_sapi(text, mood)
        else:
            self._speak_powershell(text)

    @staticmethod
    def _has_sapi():
        import importlib.util
        return importlib.util.find_spec("win32com") is not None

    # ---------- 试听 / 换音色 ----------
    def preview(self, text="你好呀，我是多多，喵~", mood="cozy"):
        """试听当前音色（默认用撒娇语气，最像小猫）。"""
        return self.say(text, mood=mood)

    def next_voice(self):
        """
        在当前引擎可用的音色里换到下一个，返回 (音色名, 试听是否成功)。
        主动换音色时主人就是想要新声音，所以即使当前是关闭状态也播一句。
        """
        if self._engine == "edge":
            cands = list(self.EDGE_CUTE)
            cur = self.cfg.get("voice")
            idx = (cands.index(cur) + 1) % len(cands) if cur in cands else 0
            self.cfg["voice"] = cands[idx]
            return cands[idx], self.say(f"换成这个声音啦，我是{_voice_cn(cands[idx])}，喵~")
        if self._engine == "onecore":
            cands = self.onecore_candidates()
            if not cands:
                return self.label(), False
            names = [v[0] for v in cands]
            cur = self.onecore_voice_name()
            idx = (names.index(cur) + 1) % len(names) if cur in names else 0
            short = names[idx].replace("Microsoft ", "")
            self.cfg["onecore_voice"] = short
            cn = next((v for k, v in ONECORE_CN.items() if k.lower() in short.lower()), short)
            return names[idx], self.say(f"换成{cn}啦，好听吗喵~")
        # SAPI：在已装音色里轮换
        self._ensure_worker()
        rows = []
        try:
            import win32com.client
            v = win32com.client.Dispatch("SAPI.SpVoice")
            rows = [t.GetDescription() for t in v.GetVoices()]
        except Exception:
            rows = []
        if not rows:
            return self.label(), False
        cur = getattr(self, "_sapi_name", None)
        idx = (rows.index(cur) + 1) % len(rows) if cur in rows else 0
        with self._lock:
            if self._voice is not None:
                try:
                    self._voice.Voice = self._voice.GetVoices().Item(idx)
                    self._sapi_name = rows[idx]
                except Exception:
                    pass
        return rows[idx], self.say("换成这个声音啦，喵~")

    def _speak_powershell(self, text, rate=0):
        """零依赖兜底：PowerShell 的 System.Speech（文本走 stdin，避免引号问题）。"""
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Rate = {max(-10, min(10, rate))};"
            "$t = [Console]::In.ReadToEnd();"
            "if ($t) { $s.Speak($t) }"
        )
        with self._lock:
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        try:
            self._proc.communicate(text.encode("utf-8"), timeout=60)
        except Exception:
            pass
        finally:
            with self._lock:
                self._proc = None


# 全局单例（主程序直接用它）
SPEAKER = Speaker(load_tts_config())


# =====================================================================
# 2. 系统状态
# =====================================================================
def _powershell(command, timeout=6):
    """执行一条 PowerShell 命令并返回 stdout 文本（失败返回空串）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _net_ok(timeout=0.6):
    for host in (("223.5.5.5", 53), ("1.1.1.1", 53)):
        try:
            socket.create_connection(host, timeout=timeout).close()
            return True
        except Exception:
            continue
    return False


def system_status():
    """
    返回统一字典：
      battery   : 电量百分比(int) 或 None（无电池）
      charging  : 是否在充电 (bool)
      mem_used  : 内存占用百分比(int) 或 None
      cpu       : CPU 占用百分比(int) 或 None
      net       : 是否联网 (bool)
    优先 psutil，缺失则用 PowerShell 兜底；任何一项拿不到就是 None，不会抛异常。
    """
    st = {"battery": None, "charging": False, "mem_used": None, "cpu": None, "net": None}

    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat is not None:
            st["battery"] = int(bat.percent)
            st["charging"] = bool(bat.power_plugged)
        st["mem_used"] = int(psutil.virtual_memory().percent)
        st["cpu"] = int(psutil.cpu_percent(interval=None))
    except Exception:
        # PowerShell 兜底
        bat = _powershell("(Get-CimInstance Win32_Battery | Select-Object -First 1).EstimatedChargeRemaining")
        m = re.search(r"\d+", bat or "")
        if m:
            st["battery"] = int(m.group())
            st["charging"] = bool(_powershell(
                "(Get-CimInstance Win32_Battery | Select-Object -First 1).BatteryStatus") in ("2", "6", "7", "8", "9"))
        mem = _powershell(
            "$o=Get-CimInstance Win32_OperatingSystem;"
            "[math]::Round(100*($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/$o.TotalVisibleMemorySize)")
        m2 = re.search(r"\d+", mem or "")
        if m2:
            st["mem_used"] = int(m2.group())

    st["net"] = _net_ok()
    return st


def status_text(st):
    """把 system_status() 的结果拼成一句可爱的话（供气泡显示）。"""
    parts = []
    if st.get("battery") is not None:
        parts.append(f"电量 {st['battery']}%" + ("（充电中）" if st.get("charging") else ""))
    if st.get("mem_used") is not None:
        parts.append(f"内存占用 {st['mem_used']}%")
    if st.get("cpu") is not None:
        parts.append(f"CPU {st['cpu']}%")
    if st.get("net") is not None:
        parts.append("网络正常" if st["net"] else "好像没联网")
    return "喵~ 报告：" + "，".join(parts) if parts else "喵…这个我读不到呢"


# =====================================================================
# 3. 提醒解析
# =====================================================================
_UNIT_SECONDS = {"秒": 1, "s": 1, "分钟": 60, "分": 60, "min": 60,
                 "小时": 3600, "时": 3600, "h": 3600, "点": 3600}
_REMIND_PAT = re.compile(r"(\d+(?:\.\d+)?)\s*(秒钟|秒|分钟|分|小时|时|点|min|s|h)", re.I)


def parse_reminder(text):
    """
    解析提醒指令，返回 (延迟秒数, 事项文本)；不是提醒指令则返回 None。
    例：
      "25分钟后提醒我喝水"      -> (1500, "喝水")
      "1小时之后叫我开会"        -> (3600, "开会")
      "10秒后提醒我"            -> (10, "时间到了")
      "番茄钟"                  -> (1500, "番茄钟结束，休息一下吧")
      "番茄钟45分钟"            -> (2700, "番茄钟结束，休息一下吧")
    只认"提醒/叫我/叫一下/提示"这类字眼，避免把普通句子误判成闹钟。
    """
    if not text:
        return None
    text = text.strip()

    # 番茄钟
    if "番茄钟" in text or "番茄" in text:
        m = _REMIND_PAT.search(text)
        minutes = float(m.group(1)) if m else 25.0
        unit = m.group(2) if m else "分钟"
        secs = int(minutes * _UNIT_SECONDS.get(unit, 60))
        return (max(5, secs), "番茄钟结束，休息一下吧")

    if not re.search(r"(提醒|叫我|叫一下|叫下|提示我|提醒我)", text):
        return None
    m = _REMIND_PAT.search(text)
    if not m:
        return None
    secs = int(float(m.group(1)) * _UNIT_SECONDS.get(m.group(2), 60))
    secs = max(5, secs)
    # 事项：去掉时间部分与"提醒我/叫我"等词
    msg = _REMIND_PAT.sub("", text, count=1)
    msg = re.sub(r"(帮我|记得|到时候|之后|后|提醒我|提醒|叫我一下|叫我|叫一下|叫下|提示我)", "", msg)
    msg = msg.strip(" ，。！？,.!?的")
    return (secs, msg or "时间到了")


# =====================================================================
# 5. 剪贴板历史（只在内存里，不落盘——剪贴板可能有密码，写了文件不安全）
# =====================================================================
class ClipboardHistory:
    """最近 N 条剪贴板内容；连续重复的不会重复记。"""

    def __init__(self, limit=10):
        self.limit = max(1, int(limit))
        self.items = []              # [{"text": str, "ts": float}] 新的在前

    def push(self, text):
        """记录一条；空文本/与最新一条相同则忽略。返回是否记录了新内容。"""
        text = (text or "").strip()
        if not text:
            return False
        if self.items and self.items[0]["text"] == text:
            return False
        self.items.insert(0, {"text": text, "ts": time.time()})
        del self.items[self.limit:]
        return True

    def get(self, index):
        """取第 index 条（1 起算，1 = 最近一条）；越界返回 None。"""
        try:
            i = int(index) - 1
        except (TypeError, ValueError):
            return None
        if 0 <= i < len(self.items):
            return self.items[i]["text"]
        return None

    def texts(self):
        return [it["text"] for it in self.items]

    def brief(self, index, width=24):
        t = self.get(index)
        if t is None:
            return None
        one = " ".join(t.split())
        return one if len(one) <= width else one[:width] + "…"

    def clear(self):
        self.items = []

    def __len__(self):
        return len(self.items)


# 解析"用第3条翻译"这类指令：返回 (序号, 动作词) 或 None
_CLIP_ITEM_PAT = re.compile(r"第\s*([0-9]+|[一二三四五六七八九十]{1,3})\s*(?:条|个)")
_CLIP_ACTIONS = (("翻译", "translate"), ("总结", "summary"), ("摘要", "summary"),
                 ("概括", "summary"), ("解释", "explain"), ("润色", "polish"),
                 ("回复", "reply"), ("回信", "reply"), ("念", "read"), ("读", "read"),
                 ("存", "save"))


def parse_clipboard_item(text):
    """从"用第2条翻译""第3条存到桌面"里解析出 (序号, 动作)。"""
    if not text:
        return None
    m = _CLIP_ITEM_PAT.search(text)
    if not m:
        return None
    tok = m.group(1)
    if tok.isdigit():
        idx = int(tok)
    else:
        idx = _cn_to_int(tok)
    if not idx:
        return None
    action = "translate"
    for key, act in _CLIP_ACTIONS:
        if key in text:
            action = act
            break
    return (idx, action)


# =====================================================================
# 6. 文件删除（送进回收站，可还原）与保护规则
# =====================================================================
MAX_DELETE_BATCH = 20        # 一次最多删多少个，防"全删了"式误操作


def protected_roots():
    """系统目录 + 多多自己的程序目录，一律不碰。"""
    roots = []
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "windir"):
        v = os.environ.get(var)
        if v:
            roots.append(os.path.abspath(v))
    try:
        import app_health
        roots.append(app_health.app_dir())      # 程序目录（含素材、配置、存档）
    except Exception:
        pass
    return [r for r in roots if r]


def is_protected(path):
    """路径是否落在禁止删除的范围内。"""
    try:
        p = os.path.abspath(path)
    except Exception:
        return True
    for root in protected_roots():
        try:
            if os.path.commonpath([p, root]) == root:
                return True
        except ValueError:
            continue
    return False


def can_delete(path):
    """返回 (能否删, 原因)。只允许删存在的普通文件。"""
    if not path or not os.path.exists(path):
        return False, "文件不存在"
    if os.path.isdir(path):
        return False, "这是文件夹，我不删文件夹"
    if is_protected(path):
        return False, "它在系统目录或我的程序目录里，太危险了"
    return True, ""


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]


def send_to_recycle_bin(paths, dry_run=False):
    """
    把文件送进**回收站**（可还原，不是永久删除）。返回 (成功数, 失败说明)。
    接口失败时抛异常由调用方提示，绝不退化成 os.remove。
    """
    paths = [os.path.abspath(p) for p in (paths or [])]
    if not paths:
        return 0, ["没有要删的文件"]
    if dry_run:
        return len(paths), []
    if os.name != "nt":
        raise RuntimeError("只有 Windows 支持送回收站")
    FO_DELETE = 3
    FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 0x40, 0x10, 0x4, 0x400
    op = _SHFILEOPSTRUCTW(None, FO_DELETE, "\0".join(paths) + "\0\0", None,
                          FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                          0, None, None)
    ret = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    failed = [] if ret == 0 else [f"资源管理器返回错误码 {ret}"]
    if op.fAnyOperationsAborted:
        failed.append("操作被中断")
    done = len([p for p in paths if not os.path.exists(p)])
    return done, failed


def parse_delete_request(text):
    """
    解析删除意图，返回 {"targets": [序号...], "all": bool} 或 None。
      "删掉第2个" / "删除第1个和第3个" / "把找到的都删了"
    序号指"多多最近找出来的文件"，不接受任意路径——安全底线。
    """
    if not text:
        return None
    t = text.strip()
    if not any(w in t for w in ("删掉", "删除", "删了", "清理掉")):
        return None
    if any(w in t for w in ("都删", "全删", "全部删", "都清理", "全清理", "都删掉")):
        return {"targets": [], "all": True}
    idxs = []
    for mm in re.finditer(r"第\s*([0-9]+|[一二三四五六七八九十]{1,3})\s*(?:个|条)", t):
        tok = mm.group(1)
        n = int(tok) if tok.isdigit() else _cn_to_int(tok)
        if n and n not in idxs:
            idxs.append(n)
    if not idxs:
        m = re.search(r"(?:删掉|删除|删了)\s*([0-9]+)(?:\s*个)?", t)
        if m:
            idxs.append(int(m.group(1)))
    if not idxs:
        return None
    return {"targets": idxs[:MAX_DELETE_BATCH], "all": False}


CLEANUP_TARGETS = {
    # 关键词 → (目录, 文件名通配, 说明)；只清多多自己生成、带专属前缀的文件
    "截图": ("~/Desktop", "多多截图_*.png", "多多截的图"),
    "剪贴板": ("~/Desktop", "剪贴板_*.txt", "从剪贴板存下来的文本"),
    "语音缓存": ("%TEMP%/duoduo_tts", "*", "语音合成的临时缓存"),
}


def parse_cleanup_request(text):
    """解析"清理多多自己产生的东西"（截图 / 剪贴板文本 / 语音缓存）。"""
    if not text or not any(w in text for w in ("清理", "清除", "清空", "打扫", "清掉")):
        return None
    for key, (folder, pattern, desc) in CLEANUP_TARGETS.items():
        if key in text:
            return {"key": key, "folder": folder, "pattern": pattern, "desc": desc}
    if any(w in text for w in ("垃圾", "临时文件", "你的东西", "你自己")):
        return {"key": "语音缓存", "folder": "%TEMP%/duoduo_tts", "pattern": "*",
                "desc": "语音合成的临时缓存"}
    return None


def collect_cleanup_files(spec):
    """按清理规则列出实际存在的文件（绝对路径）。"""
    import fnmatch
    folder = os.path.expandvars(os.path.expanduser(spec["folder"]))
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        if spec["pattern"] != "*" and not fnmatch.fnmatch(name, spec["pattern"]):
            continue
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            out.append(p)
    return out[:MAX_DELETE_BATCH * 5]


# =====================================================================
# 6.5 敏感操作：打开控制台 / 删除指定路径 / 运行命令（一律先确认）
# =====================================================================
CONSOLE_ALIASES = (
    (("管理员", "以管理员", "提权", "admin"), {"shell": "powershell", "admin": True}),
    (("powershell", "power shell"), {"shell": "powershell", "admin": False}),
    (("控制台", "命令提示符", "cmd", "dos", "终端", "小黑框", "命令行"), {"shell": "cmd", "admin": False}),
)
CONSOLE_WORDS = ("控制台", "命令提示符", "cmd", "powershell", "power shell", "终端", "小黑框",
                 "命令行", "管理员")

# 这些即使主人确认过也不执行——破坏面太大且不可逆
DANGEROUS_PATTERNS = (
    "format ", "diskpart", "shutdown", "mkfs", "cipher /w",
    "del /f /s", "del /s /q", "rd /s", "rmdir /s", "rm -rf",
    "reg delete", "reg add hklm", "net user", "net localgroup",
    "bcdedit", "vssadmin delete", "takeown /f", "icacls /reset",
    "remove-item -recurse -force c:", "stop-computer", "restart-computer",
)


def command_is_dangerous(cmd):
    """是否属于"绝对不执行"的破坏性命令。返回 (是否危险, 命中规则)。"""
    low = (cmd or "").lower().replace("\\", "/")
    for pat in DANGEROUS_PATTERNS:
        if pat in low:
            return True, pat.strip()
    return False, ""


def parse_console_request(text):
    """解析"打开控制台 / PowerShell / 管理员命令行"。返回 {"shell","admin"} 或 None。"""
    if not text:
        return None
    low = text.lower()
    if not any(w in low for w in CONSOLE_WORDS):
        return None
    # 放宽：只要提到控制台类关键词就认（不再强制要求"打开"这个动词），
    # 因为这些都属于"要不要开"的询问，走的是确认闸门，误认代价很小。
    if not any(w in low for w in ("打开", "开个", "开一个", "启动", "来一个", "开下", "弹出",
                                  "给我", "来", "开", "cmd", "控制台", "终端", "命令行",
                                  "powershell", "命令提示符", "小黑框")):
        return None
    for keys, spec in CONSOLE_ALIASES:
        if any(k in low for k in keys):
            return dict(spec)
    return {"shell": "cmd", "admin": False}


# 正斜杠也要认：用户很可能写成 D:/x/y.txt
_PATH_PAT = re.compile(
    r"""(?:"([^"]+)"|'([^']+)'|([A-Za-z]:[\\/][^\s，。；;]+|\\\\[^\s，。；;]+|~[\\/][^\s，。；;]+|\.{1,2}[\\/][^\s，。；;]+))""")


def parse_path_request(text):
    """从话里取出显式路径（支持引号、"D:\\..."、"~\\..."、".\\..."）。"""
    if not text:
        return []
    out = []
    for m in _PATH_PAT.finditer(text):
        p = next((g for g in m.groups() if g), "").strip().rstrip("。，,;；")
        if p and p not in out:
            out.append(p)
    return out


def parse_path_delete_request(text):
    """"删除 D:\\x\\y.txt"这类指定路径。返回 {"paths": [...]} 或 None。

    带空格的路径没法靠正则切干净，所以这里用"从锚点往后逐步缩短、取最长的已存在路径"
    的办法补全——`删除 C:\\a\\我的 报告 终稿.docx` 也能认出完整路径。
    """
    if not text or not any(w in text for w in ("删掉", "删除", "删了", "清理掉", "移除", "不要了",
                                                "清理", "清掉", "删除文件", "删掉文件")):
        return None
    cands = []
    for m in _PATH_PAT.finditer(text):
        raw = text[m.start():m.start() + 300].split("\n")[0]
        found = None
        for cut in range(len(raw), 1, -1):
            cand = raw[:cut].strip().rstrip("。，,;；")
            if not cand:
                continue
            full = os.path.expandvars(os.path.expanduser(cand))
            if os.path.exists(full):
                found = full
                break
        if found is None:
            # 都不存在：退回到正则截出来的那一段，让 can_delete 回"文件不存在"
            short = next((g for g in m.groups() if g), "").strip().rstrip("。，,;；")
            if short:
                found = os.path.expandvars(os.path.expanduser(short))
        if found and found not in cands:
            cands.append(found)
    if not cands:
        return None
    return {"paths": cands}


def parse_run_request(text):
    """解析"运行 <命令>"，返回命令字符串或 None。"""
    if not text:
        return None
    m = re.search(r"(?:帮我)?\s*(?:运行|执行|跑一下|跑个)\s*[：: ]?\s*(.{2,200})$", text.strip())
    if not m:
        return None
    cmd = m.group(1).strip().strip("。，")
    if not cmd or cmd.startswith(("得", "的", "起来")) or "怎么样" in cmd:
        return None
    return cmd



# =====================================================================
# 6.6 安静模式 / 免打扰时段
# =====================================================================
QUIET_ON_WORDS = ("开启安静", "打开安静", "开启免打扰", "打开免打扰", "别吵", "安静点", "我要专注",
                  "不要说话", "静音模式", "免打扰模式", "安静模式")
QUIET_OFF_WORDS = ("关闭安静", "关闭免打扰", "取消安静", "取消免打扰", "可以说话", "恢复说话",
                   "解除安静", "别安静了")
QUIET_RANGE_PAT = re.compile(
    r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*(?:[:：点时]\s*(\d{1,2}|半)?)?\s*"
    r"(?:到|至|-|~)\s*(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*(?:[:：点时]\s*(\d{1,2}|半)?)?")


def _hm(hour_tok, minute_tok):
    h = _cn_to_int(hour_tok) if hour_tok else None
    if minute_tok == "半":
        m = 30
    else:
        m = _cn_to_int(minute_tok) if minute_tok else 0
    if h is None or m is None or not (0 <= h <= 23) or not (0 <= m <= 59):
        return None
    return (h, m)


def parse_quiet_request(text):
    """
    解析安静模式指令，返回 dict 或 None：
      {"enable": True/False}                      # 开关
      {"enable": True, "start": (h,m), "end": (h,m)}  # 带时段，例如"晚上11点到早上7点别吵我"
    """
    if not text:
        return None
    t = text.strip()
    if any(w in t for w in ("吵", "打扰", "安静", "免打扰", "说话")):
        # 抽出两个时刻：优先阿拉伯数字，其次中文数字（比一条大正则稳得多）
        nums = re.findall(r"\d{1,2}", t)
        if len(nums) < 2:
            nums = re.findall(r"[零一二两三四五六七八九十]{1,3}", t)
        if len(nums) >= 2 and any(w in t for w in ("到", "至", "-", "~")):
            a, b = _hm(nums[0], None), _hm(nums[1], None)
            if a and b:
                # "晚上11点"里的 11 要补成 23
                if any(w in t for w in ("下午", "晚上", "傍晚", "今晚")) and a[0] < 12:
                    a = (a[0] + 12, a[1])
                return {"enable": True, "start": a, "end": b}
    if any(w in t for w in QUIET_OFF_WORDS):
        return {"enable": False}
    if any(w in t for w in QUIET_ON_WORDS):
        return {"enable": True}
    return None


def in_quiet_hours(now_hm, start, end):
    """纯函数：当前时刻是否落在免打扰时段内（支持跨零点，如 23:00-07:00）。"""
    if not start or not end:
        return False
    cur = now_hm[0] * 60 + now_hm[1]
    s = start[0] * 60 + start[1]
    e = end[0] * 60 + end[1]
    if s == e:
        return False
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e



# =====================================================================
# 6.7 长期记忆（"记住 我周四有例会"）
# =====================================================================
MEMORY_ADD_WORDS = ("记住", "记一下", "帮我记住", "记下来")   # "记着"太松，会误判"记着点，别摔了"
MEMORY_LIST_WORDS = ("我的备忘", "你记得什么", "记住什么了", "备忘录", "还记得什么", "你知道我什么")
MEMORY_FORGET_WORDS = ("忘掉", "忘记", "别记了", "删掉备忘", "清空备忘", "清除备忘", "别记得")
MEMORY_LIMIT = 30


def parse_memory_request(text):
    """
    解析长期记忆指令，返回 dict 或 None：
      {"action": "add", "text": ...}   {"action": "list"}
      {"action": "forget", "index"/"text": ...}   {"action": "clear"}
    """
    if not text:
        return None
    t = text.strip()
    if any(w in t for w in MEMORY_LIST_WORDS):
        return {"action": "list"}
    if any(w in t for w in MEMORY_FORGET_WORDS):
        if any(w in t for w in ("清空", "全部", "所有")):
            return {"action": "clear"}
        nums = re.findall(r"\d{1,2}", t)
        if nums:
            return {"action": "forget", "index": int(nums[0])}
        m = re.search(r"(?:忘掉|忘记|别记了|删掉备忘|别记得)\s*[：: ]?\s*(.{1,40})$", t)
        return {"action": "forget", "text": (m.group(1).strip() if m else "")}
    if any(w in t for w in MEMORY_ADD_WORDS):
        m = re.search(r"(?:帮我记住|记住|记一下|记下来)\s*[：:，,]?\s*(.{2,60})$", t)
        if m:
            body = m.group(1).strip().strip("。！! ")
            if len(body) >= 2:
                return {"action": "add", "text": body}
    return None


def human_delay(secs):
    """把秒数说成人话：90 -> '1分30秒'"""
    secs = int(secs)
    if secs % 3600 == 0 and secs >= 3600:
        return f"{secs // 3600}小时"
    if secs >= 60:
        return f"{secs // 60}分{secs % 60}秒" if secs % 60 else f"{secs // 60}分钟"
    return f"{secs}秒"


# =====================================================================
# 3.5 日程解析：相对时间 + 每天/每周/工作日 + 明天/今天几点
# =====================================================================
WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
WEEKDAY_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_CN_DIGIT = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_SCHED_TIME = re.compile(r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*[:：点时]\s*"
                         r"(半|\d{1,2}|[零一二两三四五六七八九十]{1,3})?\s*分?")
_SCHED_TRIGGER = ("提醒", "叫我", "叫一下", "叫下", "提示我", "喊我", "通知我")


def _cn_to_int(token):
    """把 '9'/'九'/'十二'/'二十' 转成整数。"""
    token = str(token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CN_DIGIT:
        return _CN_DIGIT[token]
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_DIGIT.get(head, 1) if head else 1
        ones = _CN_DIGIT.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return None


def _clean_msg(text):
    """把时间部分和触发词去掉，得到"事项"。"""
    msg = re.sub(r"\d+(?:\.\d+)?\s*(?:秒|分钟|分|小时|钟头)\s*(?:之?后|以后)?", "", text, count=1)
    msg = _SCHED_TIME.sub("", msg, count=1)
    # 先把"周X/周一三五/每周末/星期X"整段摘掉（要放在"每周"之前，否则会剩一个"一"）
    msg = re.sub(r"(?:每|每个)?\s*(?:周|星期|礼拜)\s*[一二三四五六日天]"
                 r"(?:\s*[、和到至]?\s*(?:周|星期|礼拜)?\s*[一二三四五六日天])*", "", msg)
    msg = re.sub(r"(每天|每日|天天|每星期|每个星期|工作日|明天|后天|今天|今晚|"
                 r"早上|上午|中午|下午|晚上|傍晚)", "", msg)
    for w in _SCHED_TRIGGER + ("我", "一下", "到时候", "记得", "帮我"):
        msg = msg.replace(w, "")
    msg = msg.strip(" ，。！？,.!?的：:、")
    return msg or "时间到了"


def parse_schedule(text, now=None):
    """
    解析日程，返回 dict 或 None（不是日程指令）：
      {"kind": "once"/"daily"/"weekly", "delay": 秒（once 相对时间用）,
       "at": (时, 分)（定点用）, "weekdays": [0..6]（weekly 用）, "msg": 事项}
    支持：
      "25分钟后提醒我喝水"         → once + delay
      "9点半提醒我开会"            → once（今天/明天最近的一次）
      "明天9点叫我起床"            → once（明天 9:00）
      "每天18:30叫我下班"          → daily at 18:30
      "每周一9点提醒我开例会"      → weekly [0] at 9:00
      "每周一三五 8点 提醒我锻炼"  → weekly [0,2,4]
      "工作日9点提醒我打卡"        → weekly [0..4]
    """
    if not text:
        return None
    t = text.strip()
    if not any(w in t for w in _SCHED_TRIGGER) and "提醒" not in t:
        return None
    now = now or datetime.now()

    # 1) 相对时间（X分钟后/小时后/秒后）→ 复用已有的解析
    rel = parse_reminder(t)
    if rel and re.search(r"\d+(?:\.\d+)?\s*(秒|分钟|分|小时|钟头)\s*(之?后|以后)", t):
        secs, _msg = rel
        return {"kind": "once", "delay": secs, "at": None, "weekdays": None, "msg": _clean_msg(t)}

    # 2) 取时刻
    m = _SCHED_TIME.search(t)
    if not m:
        return None
    hour = _cn_to_int(m.group(1))
    minute_tok = m.group(2)
    if minute_tok == "半":
        minute = 30
    else:
        minute = _cn_to_int(minute_tok) if minute_tok else 0
    if hour is None or minute is None:
        return None
    if any(w in t for w in ("下午", "晚上", "傍晚", "今晚")) and hour < 12:
        hour += 12
    if "中午" in t and hour < 12:
        hour = 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    msg = _clean_msg(t)
    weekdays = None
    kind = "once"

    # 3) 工作日 / 每天 / 每周X
    if "工作日" in t:
        kind, weekdays = "weekly", [0, 1, 2, 3, 4]
    elif any(w in t for w in ("每天", "每日", "天天")):
        kind = "daily"
    else:
        days = []
        for mm in re.finditer(r"(?:周|星期|礼拜)\s*([一二三四五六日天]{1,7})", t):
            for ch in mm.group(1):
                d = WEEKDAY_CN.get(ch)
                if d is not None and d not in days:
                    days.append(d)
        if days:
            if any(w in t for w in ("每",)):
                kind, weekdays = "weekly", sorted(days)
            else:
                kind, weekdays = "weekly_once", sorted(days)

    # 4) 明天/后天偏移
    day_shift = 0
    if "后天" in t:
        day_shift = 2
    elif "明天" in t:
        day_shift = 1

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day_shift:
        target += timedelta(days=day_shift)
    if kind == "weekly_once":
        # 下一个匹配的周X
        for i in range(0, 8):
            cand = target + timedelta(days=i)
            if cand.weekday() in weekdays and cand > now:
                target = cand
                break
    elif kind == "once" and not day_shift and target <= now:
        target += timedelta(days=1)          # 今天已过 → 明天同一时间
        if "今天" in t or "今晚" in t:
            target = now + timedelta(minutes=1)   # 说"今天X点"但已过 → 1 分钟后
    if kind == "weekly_once":
        kind = "once"
    return {"kind": kind, "delay": max(1, int((target - now).total_seconds())),
            "at": (hour, minute), "weekdays": weekdays, "msg": msg}


def next_due(spec, now=None):
    """算下一次该响的时间戳（供重复提醒续期）。"""
    now = now or datetime.now()
    if spec.get("kind") == "once":
        return now.timestamp() + float(spec.get("delay") or 60)
    hour, minute = spec.get("at") or (9, 0)
    days = spec.get("weekdays")
    for i in range(0, 9):
        cand = (now + timedelta(days=i)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand <= now:
            continue
        if spec.get("kind") == "daily" or days is None or cand.weekday() in days:
            return cand.timestamp()
    return now.timestamp() + 3600


def schedule_text(spec, now=None):
    """把日程说成人话，用于确认提示。"""
    now = now or datetime.now()
    kind = spec.get("kind")
    hour, minute = spec.get("at") or (0, 0)
    when = f"{hour:02d}:{minute:02d}"
    if kind == "once":
        secs = int(spec.get("delay") or 0)
        return f"{human_delay(secs)}后" if secs < 86400 else f"{when}"
    if spec.get("weekdays") == [0, 1, 2, 3, 4]:
        return f"每个工作日 {when}"
    if kind == "daily":
        return f"每天 {when}"
    chars = "、".join(WEEKDAY_NAME[d][-1] for d in (spec.get("weekdays") or []))
    return f"每周{chars} {when}" if chars else f"每周 {when}"


# =====================================================================
# 4. 系统音量（发系统媒体键，不需要额外依赖）
# =====================================================================
# 虚拟键码：VK_VOLUME_MUTE / DOWN / UP
_VK_VOLUME = {"mute": 0xAD, "down": 0xAE, "up": 0xAF}
VOLUME_STEPS = 5          # 一次"大声一点"按几格（每格约 2%）
VOLUME_WORDS = ("音量", "声音", "静音", "大声", "小声", "响亮", "出声", "吵")


def volume(action, steps=None, dry_run=False):
    """
    调系统音量：action ∈ {"up", "down", "mute"}，返回实际按键次数。
    dry_run=True 时只算次数不真的按键（给测试用）。
    """
    action = str(action or "").strip().lower()
    if action not in _VK_VOLUME:
        return 0
    times = 1 if action == "mute" else max(1, int(steps or VOLUME_STEPS))
    if dry_run:
        return times
    try:
        import ctypes

        user32 = ctypes.windll.user32
        for _ in range(times):
            user32.keybd_event(_VK_VOLUME[action], 0, 0, 0)
            user32.keybd_event(_VK_VOLUME[action], 0, 2, 0)   # KEYEVENTF_KEYUP
            time.sleep(0.03)                                  # 太快会被系统丢掉
        return times
    except Exception:
        return 0


def volume_reply(text):
    """
    把"声音大一点/静音"这类话翻译成音量动作，返回 (action, 回复文本)；不是音量指令则 None。
    只在句子里出现音量相关词、且没有别的意图时生效，避免误触发。
    """
    if not text:
        return None
    t = text.strip()
    if not any(w in t for w in VOLUME_WORDS):
        return None
    if re.search(r"(静音|别出声|不要出声|别吵|闭嘴)", t) or (
            "声音" in t and re.search(r"(关|停|闭|没声)", t)):
        return ("mute", "喵～静音啦，世界安静了")
    if re.search(r"(大声|大点|大一点|调大|提高|增加|响亮|听不清|太小)", t):
        return ("up", "喵！音量调大一点点～")
    if re.search(r"(小声|小点|小一点|调小|降低|减少|安静一点|太吵)", t):
        return ("down", "喵…音量调小一点点～")
    if re.search(r"(音量|声音)", t) and re.search(r"(多少|多大|几格|状态)", t):
        return ("status", "喵～音量我看不到具体数字，但可以帮你按大/按小")
    return None
