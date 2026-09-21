# -*- coding: utf-8 -*-
"""
app_health.py —— 运行保障：日志、单实例、开机自启、配置自检、空闲检测
=====================================================================
这里放的都是"跟桌宠业务无关、但让它稳"的东西，独立成模块便于单测：
  1. setup_logging()      滚动日志到 %TEMP%\\duoduo.log + 全局异常钩子
  2. SingleInstance       单实例互斥（命名互斥体，跨进程可靠，不依赖 Qt）
  3. autostart_*          开机自启（启动文件夹快捷方式，用 pythonw 无窗口运行）
  4. check_config()       配置自检，返回给主人看的问题列表
  5. idle_seconds()       Windows 空闲时长（GetLastInputInfo），用于看家模式
"""
import ctypes
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

LOG_PATH = os.path.join(os.environ.get("TEMP", "."), "duoduo.log")
SHORTCUT_NAME = "多多桌宠.lnk"


def app_dir():
    """
    程序所在目录（配置/存档/帧素材都放这儿）。
    **打包成 exe 后** __file__ 指向临时解包目录，必须改用 exe 自己的目录，
    否则 config.json / pet_data.json / frames_opt 都会找错地方（甚至写进临时目录被清掉）。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------------
# 1. 日志
# ----------------------------------------------------------------------
def setup_logging(level=logging.INFO):
    """滚动日志（1MB × 3）到 %TEMP%\\duoduo.log，并挂上全局异常钩子。"""
    logger = logging.getLogger("duoduo")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    try:
        h = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(h)
    except Exception:
        logger.addHandler(logging.NullHandler())

    def _hook(exc_type, exc, tb):
        logger.error("未捕获异常", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
    return logger


def log(msg, level=logging.INFO):
    logging.getLogger("duoduo").log(level, msg)


# ----------------------------------------------------------------------
# 2. 单实例
# ----------------------------------------------------------------------
class SingleInstance:
    """命名互斥体实现的单实例锁（不需要 Qt）。

    用法：
        lock = SingleInstance("DuoduoPet")
        if not lock.acquire():   # 已经有实例在跑
            print(lock.notify_existing())   # 给旧实例发个信号（可选）
            sys.exit(0)
    """

    def __init__(self, name="DuoduoPet", ipc_port=None):
        self.name = "Global\\" + name
        self.handle = None
        self.acquired = False

    def acquire(self):
        if os.name != "nt":
            self.acquired = True
            return True
        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.windll.kernel32
        self.handle = kernel32.CreateMutexW(None, False, self.name)
        self.acquired = bool(self.handle) and kernel32.GetLastError() != ERROR_ALREADY_EXISTS
        return self.acquired

    def release(self):
        if self.handle and os.name == "nt":
            try:
                ctypes.windll.kernel32.ReleaseMutex(self.handle)
                ctypes.windll.kernel32.CloseHandle(self.handle)
            except Exception:
                pass
        self.handle = None
        self.acquired = False

    # ---- 唤起已有实例：用一个本地 socket 通知（有就发，没有就算了） ----
    @staticmethod
    def notify_existing(port=45871, timeout=0.6):
        """通知已在运行的实例"把猫显示出来"；成功返回 True。"""
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
                s.sendall(b"show\n")
            return True
        except Exception:
            return False


# ----------------------------------------------------------------------
# 3. 开机自启
# ----------------------------------------------------------------------
def startup_dir():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs\Startup")


def autostart_path():
    return os.path.join(startup_dir(), SHORTCUT_NAME)


def autostart_enabled():
    return os.path.exists(autostart_path())


def autostart_set(enable, script_path=None):
    """
    打开/关闭开机自启（启动文件夹里的快捷方式）。
    用 pythonw.exe（无控制台窗口）运行脚本；成功返回 True。
    """
    path = autostart_path()
    if not enable:
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except Exception:
            return False
    script = script_path or os.path.abspath(sys.argv[0])
    if str(script).lower().endswith(".exe"):
        target, args = str(script), ""            # 打包版：直接指向 exe 自己
    else:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        target, args = pythonw, f'"{script}"'
    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$sc = $ws.CreateShortcut('{path}');"
        f"$sc.TargetPath = '{target}';"
        f"$sc.Arguments = '{args}';"
        f"$sc.WorkingDirectory = '{os.path.dirname(script)}';"
        "$sc.Description = '多多桌宠';"
        "$sc.Save()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return os.path.exists(path)
    except Exception:
        return False


# ----------------------------------------------------------------------
# 4. 配置自检
# ----------------------------------------------------------------------
def check_config(cfg):
    """返回 [问题说明]；空列表表示没问题。"""
    problems = []
    if not isinstance(cfg, dict):
        return ["config.json 不是合法的 JSON 对象"]
    if not str(cfg.get("api_key") or "").strip():
        problems.append("没填 api_key（现在只能听懂本地指令，不会聊天）")
    base = str(cfg.get("api_base") or "")
    if base and not base.startswith(("http://", "https://")):
        problems.append(f"api_base 看起来不是网址：{base}")
    try:
        if float(cfg.get("timeout", 30)) <= 0:
            problems.append("timeout 必须是正数")
    except (TypeError, ValueError):
        problems.append("timeout 不是数字")
    try:
        if int(cfg.get("history_turns", 6)) < 0:
            problems.append("history_turns 不能是负数")
    except (TypeError, ValueError):
        problems.append("history_turns 不是整数")

    tts = cfg.get("tts") or {}
    engine = str(tts.get("engine", "auto")).lower()
    if engine not in ("auto", "edge", "onecore", "sapi", "powershell"):
        problems.append(f"tts.engine 取值不认识：{engine}（可选 auto/edge/onecore/sapi/powershell）")
    if engine in ("edge", "auto"):
        voice = str(tts.get("voice") or "")
        if voice and not voice.startswith("zh-"):
            problems.append(f"tts.voice 不是中文音色名：{voice}")
    proxy = tts.get("edge_proxy", "auto")
    if proxy not in ("auto", "", None) and "://" not in str(proxy):
        problems.append(f"tts.edge_proxy 需要是 auto 或 http://host:port：{proxy}")
    return problems


# ----------------------------------------------------------------------
# 5. 空闲检测（看家模式用）
# ----------------------------------------------------------------------
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    """距离主人最后一次操作键盘/鼠标过了多少秒（非 Windows 返回 0）。"""
    if os.name != "nt":
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


# ----------------------------------------------------------------------
# 6. 全屏检测（全屏应用自动避让用）
# ----------------------------------------------------------------------
def covers(win_rect, mon_rect, tol=2):
    """窗口矩形是否盖满显示器矩形（允许 tol 像素误差）。纯函数，便于测试。"""
    if not win_rect or not mon_rect:
        return False
    wx0, wy0, wx1, wy1 = win_rect
    mx0, my0, mx1, my1 = mon_rect
    return (wx0 <= mx0 + tol and wy0 <= my0 + tol
            and wx1 >= mx1 - tol and wy1 >= my1 - tol)


def fullscreen_active():
    """前台窗口是否全屏（桌面/任务栏不算）。非 Windows 返回 False。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd):
            return False
        # 桌面/任务栏/开始菜单之类不算"全屏应用"
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"):
            return False
        win = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(win)):
            return False
        mon = user32.MonitorFromWindow(hwnd, 2)      # 2 = MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return False
        if not covers((win.left, win.top, win.right, win.bottom),
                      (info.rcMonitor.left, info.rcMonitor.top,
                       info.rcMonitor.right, info.rcMonitor.bottom)):
            return False
        # 光"盖满显示器"还不够：最大化窗口在某些环境下矩形也等于显示器，
        # 所以再加一条——要么它盖住了任务栏区域（真全屏），要么它是无边框窗口（游戏/视频）。
        # 判据收紧：必须盖住**任务栏区域**才算全屏。
        # 之前还允许"无标题栏窗口"，结果 VS Code / Chrome 应用模式这种无边框最大化窗口
        # 被误判成全屏，小猫会在主人正常干活时自己藏起来——误判比漏判讨厌得多。
        tol = 2
        over_taskbar = (win.bottom > info.rcWork.bottom + tol
                        or win.top < info.rcWork.top - tol
                        or win.left < info.rcWork.left - tol
                        or win.right > info.rcWork.right + tol)
        return bool(over_taskbar)
    except Exception:
        return False


# ----------------------------------------------------------------------
# 看家/专注模式的判定（纯函数，便于测试）
# ----------------------------------------------------------------------
FOCUS_DEFAULTS = {
    "away_seconds": 300,      # 离开多久算"没人"→ 去睡觉
    "back_seconds": 20,       # 回来后多少秒内打招呼
    "work_seconds": 3000,     # 连续工作 50 分钟 → 提醒休息
    "break_cooldown": 3600,   # 提醒间隔至少 1 小时
}


def focus_decision(idle, busy_for, last_break_age, was_away=False, cfg=None):
    """
    看家模式判定，返回 "away" / "back" / "rest" / None。
      idle           当前空闲秒数
      busy_for       连续"正在用电脑"的秒数
      last_break_age 距上次休息提醒过了多少秒（没提醒过给一个很大的数）
      was_away       上一轮是不是处于"离开"状态（回来只在离开之后才算）
    """
    c = dict(FOCUS_DEFAULTS)
    c.update({k: v for k, v in (cfg or {}).items() if isinstance(v, (int, float))})
    if idle >= c["away_seconds"]:
        return "away"
    if was_away and idle <= c["back_seconds"]:
        return "back"
    if busy_for >= c["work_seconds"] and last_break_age >= c["break_cooldown"]:
        return "rest"
    return None


def now_ts():
    return time.time()
