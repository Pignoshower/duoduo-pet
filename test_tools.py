# -*- coding: utf-8 -*-
"""test_tools.py —— pet_tools 单元测试（纯逻辑，不发声、不依赖界面）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import pet_tools as tools

results = []


def _p(text):
    """按控制台编码安全打印：emoji 在 GBK 控制台会直接抛 UnicodeEncodeError。"""
    enc = sys.stdout.encoding or "utf-8"
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(enc, "replace").decode(enc, "replace"))


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    _p(f"{'PASS ' if cond else 'FAIL '}{name}" + (f"  {detail}" if detail else ""))


# ---------- 提醒解析 ----------
cases = [
    ("25分钟后提醒我喝水", (1500, "喝水")),
    ("1小时之后叫我开会", (3600, "开会")),
    ("10秒后提醒我", (10, "时间到了")),
    ("30分钟后提醒我关火", (1800, "关火")),
    ("番茄钟", (1500, "番茄钟结束，休息一下吧")),
    ("番茄钟45分钟", (2700, "番茄钟结束，休息一下吧")),
]
for text, expect in cases:
    got = tools.parse_reminder(text)
    check(f"提醒解析: {text}", got == expect, f"得到 {got}")

check("非提醒句不误判", tools.parse_reminder("今天天气不错") is None)
check("无时间不误判", tools.parse_reminder("提醒我一下") is None)
check("空字符串安全", tools.parse_reminder("") is None)

# ---------- 时长口语化 ----------
check("human_delay 90", tools.human_delay(90) == "1分30秒", tools.human_delay(90))
check("human_delay 3600", tools.human_delay(3600) == "1小时", tools.human_delay(3600))
check("human_delay 120", tools.human_delay(120) == "2分钟", tools.human_delay(120))
check("human_delay 30", tools.human_delay(30) == "30秒", tools.human_delay(30))

# ---------- 系统状态 ----------
st = tools.system_status()
check("system_status 结构完整",
      isinstance(st, dict) and set(st) == {"battery", "charging", "mem_used", "cpu", "net"},
      str(st))
check("system_status 数值类型合法",
      (st["battery"] is None or 0 <= st["battery"] <= 100)
      and (st["mem_used"] is None or 0 <= st["mem_used"] <= 100)
      and isinstance(st["charging"], bool)
      and (st["net"] is None or isinstance(st["net"], bool)),
      str(st))
text = tools.status_text(st)
check("status_text 有内容", isinstance(text, str) and len(text) > 3, text)
check("status_text 空数据兜底",
      tools.status_text({}) == "喵…这个我读不到呢", tools.status_text({}))

# ---------- 语音（不实际发声） ----------
check("Speaker 可探测引擎", isinstance(tools.SPEAKER.engine, str), tools.SPEAKER.engine)
check("dry_run 不发声", tools.SPEAKER.say("测试", dry_run=True) == tools.SPEAKER.available(),
      f"engine={tools.SPEAKER.engine}")
check("空文本不发声", tools.SPEAKER.say("", dry_run=True) is False)
tools.SPEAKER.stop()      # 不应抛异常

# ---------- 音量（dry_run，不真的按键） ----------
check("volume 上调格数", tools.volume("up", dry_run=True) == tools.VOLUME_STEPS,
      str(tools.volume("up", dry_run=True)))
check("volume 下调格数", tools.volume("down", steps=2, dry_run=True) == 2)
check("volume 静音只按一次", tools.volume("mute", dry_run=True) == 1)
check("volume 未知动作返回0", tools.volume("louder", dry_run=True) == 0)
check("volume 大小写/空格容错", tools.volume(" UP ", dry_run=True) == tools.VOLUME_STEPS)

vol_cases = [
    ("声音大一点", "up"),
    ("能不能小声点", "down"),
    ("静音", "mute"),
    ("别出声了", "mute"),
    ("把音量调大", "up"),
    ("太吵了", "down"),
]
for phrase, want in vol_cases:
    got = tools.volume_reply(phrase)
    check(f"volume_reply {phrase}", got is not None and got[0] == want, str(got))
for phrase in ("今天天气不错", "声音好听的歌", "帮我打开B站"):
    got = tools.volume_reply(phrase)
    check(f"volume_reply 不误触 {phrase}", got is None, str(got))
check("volume_reply 音量状态问句", (tools.volume_reply("声音多大多小") or ("",))[0] == "status",
      str(tools.volume_reply("声音多大多小")))
check("volume_reply 空输入安全", tools.volume_reply("") is None)

# ---------- 语音：配置与音色 ----------
tts_cfg = tools.load_tts_config()
check("tts 配置含全部键",
      set(tools.DEFAULT_TTS_CONFIG) <= set(tts_cfg),
      str(sorted(tts_cfg)))
check("tts 引擎取值合法", tts_cfg.get("engine") in ("auto", "edge", "sapi", "powershell"),
      str(tts_cfg.get("engine")))
check("缺失配置也有默认值", tools.load_tts_config("不存在的路径.json") == tools.DEFAULT_TTS_CONFIG)
check("音色中文名可翻译",
      tools._voice_cn("zh-CN-XiaoyiNeural") == "晓伊（活泼少女音）"
      and tools._voice_cn("未知音色") == "未知音色")
check("label 非空且有引擎名", isinstance(tools.SPEAKER.label(), str)
      and bool(tools.SPEAKER.label()), tools.SPEAKER.label())

# 换音色：把 say 打桩，避免测试时真的出声
said = []
_real_say = tools.SPEAKER.say
tools.SPEAKER.say = lambda text, dry_run=False, mood=None: (said.append((text, mood)) or True)
try:
    name, ok = tools.SPEAKER.next_voice()
    check("next_voice 返回音色名且会试听", isinstance(name, str) and bool(name) and ok and said,
          f"{name} said={said[:1]}")
finally:
    tools.SPEAKER.say = _real_say

# ---------- OneCore 语音管线（不发声、不起进程的纯逻辑部分） ----------
oc_rows = tools.SPEAKER._parse_voice_lines([
    "VOICE|Microsoft Yaoyao|zh-CN|Female",
    "VOICE|Microsoft Kangkang|zh-CN|Male",
    "VOICE|Microsoft Zira|en-US|Female",
    "垃圾行", "END"])
check("OneCore 音色行解析", len(oc_rows) == 3 and oc_rows[0][0] == "Microsoft Yaoyao", str(oc_rows))
check("OneCore 解析容错", tools.SPEAKER._parse_voice_lines(["VOICE|x", "", None])[0] == ("x", "", ""),
      str(tools.SPEAKER._parse_voice_lines(["VOICE|x", "", None])))

saved_cache = tools.SPEAKER._onecore_cache
tools.SPEAKER._onecore_cache = oc_rows + [("Microsoft Huihui", "zh-CN", "Female")]
try:
    names = [v[0] for v in tools.SPEAKER.onecore_candidates()]
    check("有多个女声时只在小猫味女声间循环",
          names == ["Microsoft Yaoyao", "Microsoft Huihui"], str(names))
    tools.SPEAKER._onecore_cache = oc_rows          # 只有 1 个女声 → 放宽到全部中文音色
    check("只有一个女声时放宽到全部中文音色",
          len(tools.SPEAKER.onecore_candidates()) == 2, str(tools.SPEAKER.onecore_candidates()))
    tools.SPEAKER._onecore_cache = oc_rows
    tools.SPEAKER.cfg["onecore_voice"] = "Kangkang"
    check("配置可指定音色", tools.SPEAKER.onecore_voice_name() == "Microsoft Kangkang",
          tools.SPEAKER.onecore_voice_name())
    tools.SPEAKER.cfg["onecore_voice"] = "不存在的音色"
    check("音色名写错时回退女声", tools.SPEAKER.onecore_voice_name() == "Microsoft Yaoyao",
          tools.SPEAKER.onecore_voice_name())
finally:
    tools.SPEAKER._onecore_cache = saved_cache
    tools.SPEAKER.cfg["onecore_voice"] = "Yaoyao"

check("控制字符被清理（逐行协议安全）",
      tools.SPEAKER._safe_text("a\tb\nc\r\n") == "a b c", repr(tools.SPEAKER._safe_text("a\tb\nc\r\n")))
# 两个真实踩过的坑：Split 字段数必须够（否则文本被吞）、管道必须显式 UTF-8（否则中文乱码）
check("OneCore 脚本协议字段数=6", 'Split("`t", 6)' in tools.SPEAKER.ONECORE_DAEMON)
check("OneCore 脚本显式 UTF-8",
      "InputEncoding" in tools.SPEAKER.ONECORE_DAEMON
      and "OutputEncoding" in tools.SPEAKER.ONECORE_DAEMON)
check("OneCore 脚本有 READY 握手", '"READY|"' in tools.SPEAKER.ONECORE_DAEMON)

for want in ("sapi", "onecore"):
    probe = tools.Speaker({"engine": want})
    check(f"engine={want} 时按配置选引擎", probe.engine == want, probe.engine)
check("engine=none 时不崩", tools.Speaker({"engine": "none"}).engine in ("none", "powershell", "pyttsx3"),
      tools.Speaker({"engine": "none"}).engine)

# ---------- edge 音色与代理（真实踩过的坑：写了服务端不存在的音色） ----------
check("edge 候选音色不含无效项",
      "zh-CN-XiaoshuangNeural" not in tools.SPEAKER.EDGE_CUTE
      and all(v.startswith("zh-CN-") for v in tools.SPEAKER.EDGE_CUTE),
      str(tools.SPEAKER.EDGE_CUTE))
check("默认 edge 音色在候选里",
      tools.DEFAULT_TTS_CONFIG["voice"] in tools.SPEAKER.EDGE_CUTE,
      tools.DEFAULT_TTS_CONFIG["voice"])

saved_proxy_cfg = tools.SPEAKER.cfg.get("edge_proxy")
try:
    tools.SPEAKER.cfg["edge_proxy"] = ""
    check("edge_proxy 关掉时返回 None", tools.SPEAKER._edge_proxy() is None,
          str(tools.SPEAKER._edge_proxy()))
    tools.SPEAKER.cfg["edge_proxy"] = "http://127.0.0.1:9999"
    check("edge_proxy 可指定", tools.SPEAKER._edge_proxy() == "http://127.0.0.1:9999",
          str(tools.SPEAKER._edge_proxy()))
    tools.SPEAKER.cfg["edge_proxy"] = "auto"
    auto = tools.SPEAKER._edge_proxy()
    check("edge_proxy auto 结果合法", auto is None or "://" in auto, str(auto))
finally:
    tools.SPEAKER.cfg["edge_proxy"] = saved_proxy_cfg

check("音色中文名含新音色",
      tools._voice_cn("zh-CN-XiaoyiNeural").startswith("晓伊")
      and tools._voice_cn("zh-CN-YunxiaNeural").startswith("云夏"),
      tools._voice_cn("zh-CN-XiaoyiNeural"))

# ---------- 情绪语音（mood → 语调） ----------
check("情绪表覆盖常用语气",
      {"normal", "happy", "excited", "cozy", "sleepy", "alert", "proud", "sorry"}
      <= set(tools.MOODS), str(sorted(tools.MOODS)))
check("中文情绪别名", tools.mood_info("开心") == "happy" and tools.mood_info("困") == "sleepy",
      f"{tools.mood_info('开心')} {tools.mood_info('困')}")
check("未知情绪回退正常", tools.mood_info("乱七八糟") == "normal")
check("情绪中文名", tools.mood_cn("sleepy") == "困倦", tools.mood_cn("sleepy"))

edge_normal = tools.SPEAKER.edge_prosody("normal")
edge_happy = tools.SPEAKER.edge_prosody("happy")
edge_sleepy = tools.SPEAKER.edge_prosody("sleepy")
check("开心比平常更快更高", tools._num(edge_happy[0]) > tools._num(edge_normal[0])
      and tools._num(edge_happy[1]) > tools._num(edge_normal[1]), f"{edge_happy} vs {edge_normal}")
check("困倦比平常更慢更低", tools._num(edge_sleepy[0]) < tools._num(edge_normal[0])
      and tools._num(edge_sleepy[1]) < tools._num(edge_normal[1]), f"{edge_sleepy} vs {edge_normal}")
check("情绪参数格式正确（edge）",
      edge_happy[0].endswith("%") and edge_happy[1].endswith("Hz"), str(edge_happy))
oc_sleepy = tools.SPEAKER.onecore_prosody("sleepy")
check("onecore 情绪换算合法",
      float(oc_sleepy[0]) < 1.2 and oc_sleepy[1].endswith("%"), str(oc_sleepy))
sapi_happy, sapi_sleepy = tools.SPEAKER.sapi_prosody("happy"), tools.SPEAKER.sapi_prosody("sleepy")
check("sapi 情绪换算合法",
      sapi_happy[0] > sapi_sleepy[0] and -10 <= sapi_sleepy[1] <= 10, f"{sapi_happy} {sapi_sleepy}")

# 缓存键必须带上情绪，否则"困倦"的音频会被"开心"复用
p1 = tools.SPEAKER._cache_path("同一句话", "zh-CN-XiaoyiNeural", tools.SPEAKER.edge_prosody("happy")[0])
p2 = tools.SPEAKER._cache_path("同一句话", "zh-CN-XiaoyiNeural", tools.SPEAKER.edge_prosody("sleepy")[0])
check("不同情绪缓存不串音", p1 != p2, f"{p1[-12:]} vs {p2[-12:]}")

# say 会把情绪带进队列
queued = []
_real_put = tools.SPEAKER._queue.put
tools.SPEAKER._ensure_worker = lambda: None
tools.SPEAKER._queue.put = lambda item: queued.append(item)
try:
    ok = tools.SPEAKER.say("测试情绪", mood="happy")
    check("say 带上情绪入队", ok and queued and queued[-1][1] == "happy", str(queued[-1:]))
    tools.SPEAKER.say("默认情绪")
    check("say 默认情绪为 normal", queued[-1][1] == "normal", str(queued[-1:]))
finally:
    tools.SPEAKER._queue.put = _real_put

# ---------- 运行保障 app_health ----------
import app_health as health

check("日志文件在临时目录", health.LOG_PATH.lower().endswith("duoduo.log"), health.LOG_PATH)
check("日志可初始化", health.setup_logging() is not None)
check("空闲时间非负", health.idle_seconds() >= 0)

lock1 = health.SingleInstance("DuoduoTestLock")
lock2 = health.SingleInstance("DuoduoTestLock")
try:
    first = lock1.acquire()
    second = lock2.acquire()
    check("单实例锁生效", first and not second, f"first={first} second={second}")
finally:
    lock1.release()
    lock2.release()

cfg_ok = {"api_key": "x", "api_base": "https://api.deepseek.com/v1", "timeout": 30,
          "history_turns": 6,
          "tts": {"engine": "auto", "voice": "zh-CN-XiaoyiNeural", "edge_proxy": "auto"}}
check("正常配置无告警", health.check_config(cfg_ok) == [], str(health.check_config(cfg_ok)))
bad = health.check_config({"api_key": "", "api_base": "ftp://x", "timeout": "abc",
                           "history_turns": "x",
                           "tts": {"engine": "nope", "voice": "en-US-A",
                                   "edge_proxy": "127.0.0.1:1"}})
check("坏配置逐条报出", len(bad) >= 5, str(bad))
check("配置非字典也不崩", health.check_config(None) != [], str(health.check_config(None)))

check("看家·离开", health.focus_decision(600, 0, 0) == "away")
check("看家·回来（需先离开过）",
      health.focus_decision(3, 100, 0, was_away=True) == "back"
      and health.focus_decision(3, 100, 0, was_away=False) is None)
check("看家·久坐提醒", health.focus_decision(5, 3100, 999999) == "rest")
check("看家·冷却期不重复", health.focus_decision(5, 3100, 10) is None)
check("看家阈值可配置", health.focus_decision(60, 0, 0, cfg={"away_seconds": 30}) == "away")
check("自启快捷方式在启动文件夹", "Startup" in health.autostart_path(), health.autostart_path())

# ---------- 日程解析（每天/每周/工作日/几点） ----------
from datetime import datetime as _dt

_now = _dt(2026, 9, 21, 14, 20)          # 周一 14:20
sched = tools.parse_schedule
check("日程·相对时间", (lambda r: r and r["kind"] == "once" and r["delay"] == 1500
                        and r["msg"] == "喝水")(sched("25分钟后提醒我喝水", now=_now)),
      str(sched("25分钟后提醒我喝水", now=_now)))
check("日程·每天定点", (lambda r: r and r["kind"] == "daily" and r["at"] == (18, 30)
                        and r["msg"] == "下班")(sched("每天18:30叫我下班", now=_now)),
      str(sched("每天18:30叫我下班", now=_now)))
check("日程·每周一", (lambda r: r and r["kind"] == "weekly" and r["weekdays"] == [0]
                      and r["msg"] == "开例会")(sched("每周一9点提醒我开例会", now=_now)),
      str(sched("每周一9点提醒我开例会", now=_now)))
check("日程·一周多天", (lambda r: r and r["weekdays"] == [0, 2, 4]
                        and r["msg"] == "锻炼")(sched("每周一三五 8点 提醒我锻炼", now=_now)),
      str(sched("每周一三五 8点 提醒我锻炼", now=_now)))
check("日程·工作日", (lambda r: r and r["weekdays"] == [0, 1, 2, 3, 4]
                      and r["msg"] == "打卡")(sched("工作日9点提醒我打卡", now=_now)),
      str(sched("工作日9点提醒我打卡", now=_now)))
check("日程·明天几点", (lambda r: r and r["kind"] == "once" and r["at"] == (9, 0)
                        and r["delay"] == 67200)(sched("明天9点叫我起床", now=_now)),
      str(sched("明天9点叫我起床", now=_now)))
check("日程·中文数字与半", (lambda r: r and r["at"] == (9, 30))(sched("每天九点半提醒我喝水", now=_now)),
      str(sched("每天九点半提醒我喝水", now=_now)))
check("日程·普通话不误判", sched("今天天气不错", now=_now) is None)
check("日程·没有触发词不认", sched("18:30 的会议在哪", now=_now) is None)

check("next_due 每天取今天或明天",
      _dt.fromtimestamp(tools.next_due({"kind": "daily", "at": (18, 30)}, _now)).hour == 18,
      str(_dt.fromtimestamp(tools.next_due({"kind": "daily", "at": (18, 30)}, _now))))
check("next_due 每周跳到他日",
      _dt.fromtimestamp(tools.next_due({"kind": "weekly", "at": (8, 0), "weekdays": [2]}, _now))
      .weekday() == 2,
      str(_dt.fromtimestamp(tools.next_due({"kind": "weekly", "at": (8, 0), "weekdays": [2]}, _now))))
check("schedule_text 说人话",
      tools.schedule_text({"kind": "daily", "at": (18, 30)}) == "每天 18:30"
      and tools.schedule_text({"kind": "weekly", "at": (9, 0), "weekdays": [0, 2]}) == "每周一、三 09:00"
      and tools.schedule_text({"kind": "weekly", "at": (9, 0), "weekdays": [0, 1, 2, 3, 4]}) == "每个工作日 09:00",
      tools.schedule_text({"kind": "daily", "at": (18, 30)}))

check("covers 判全屏", health.covers((0, 0, 1920, 1080), (0, 0, 1920, 1080))
      and not health.covers((0, 0, 1900, 1080), (0, 0, 1920, 1080))
      and not health.covers((100, 100, 800, 600), (0, 0, 1920, 1080)))
check("covers 容差内算全屏", health.covers((1, 1, 1919, 1079), (0, 0, 1920, 1080)))
check("covers 空值不崩", health.covers(None, (0, 0, 1, 1)) is False)
check("fullscreen_active 返回布尔", isinstance(health.fullscreen_active(), bool))

# ---------- 素材快照 / 流水线（素材安全网） ----------
import os as _os
import shutil as _shutil
import tempfile as _tempfile

try:                                   # 素材工具属本地文件，仓库里没有 → 缺失时跳过这部分测试
    import snapshot as snap
    import pipeline as pipe
except ImportError:                    # pragma: no cover
    snap = pipe = None

if snap is not None:
    _root = _tempfile.mkdtemp()
_sd = _os.path.join(_root, "快照")
_os.makedirs(_os.path.join(_root, "frames_opt"))
_png = _os.path.join(_root, "frames_opt", "a.png")
with open(_png, "wb") as f:
    f.write(b"A" * 50)
info1 = snap.save("第一份", root=_root, snap_dir=_sd)
with open(_png, "wb") as f:
    f.write(b"B" * 50)
snap.save("第二份", root=_root, snap_dir=_sd)
check("快照能保存", info1["files"] == 1 and info1["file"].endswith(".zip"), str(info1))
check("快照能列出", len(snap.list_snapshots(_sd)) == 2, str(snap.list_snapshots(_sd)))
_chosen, _msg = snap.restore(1, root=_root, snap_dir=_sd)
check("快照回退内容正确", open(_png, "rb").read() == b"A" * 50, _msg)
check("回退前自动再备份一份", len(snap.list_snapshots(_sd)) == 3)
check("快照文件重名防护", len({i["file"] for i in snap.list_snapshots(_sd)}) == 3)
check("快照清理旧份", snap.prune(1, snap_dir=_sd) == 2 and len(snap.list_snapshots(_sd)) == 1)
check("没有快照时回退给提示", snap.restore("latest", root=_root, snap_dir=_os.path.join(_root, "空"))[0] is None)
_shutil.rmtree(_root, ignore_errors=True)

_plan = [cmd for _label, cmd in pipe.steps()]
check("流水线步骤数正确", len(_plan) == 7, str(_plan))
check("流水线含白块修复两步",
      any("fill_gaps.py" in c for c in _plan) and any("fix_white_patches.py" in c for c in _plan),
      str(_plan))
check("转圈排在烘焙之后（顺序关键）",
      _plan.index(["prepare_frames.py"]) < _plan.index(["make_turn.py", "--frames", "24"]), str(_plan))
check("末尾会做视觉回归", _plan[-1][0] == "visual_regression.py", str(_plan[-1]))
check("跳过抠图的模式可用", len(pipe.steps(skip_extract=True)) == 6)
check("接受新基线会带 --update", "--update" in pipe.steps(accept=True)[-1][1])

# ---------- 剪贴板历史 ----------
h = tools.ClipboardHistory(3)
check("历史·记录与去重",
      h.push("a") and not h.push("a") and h.push("b") and len(h) == 2, str(h.texts()))
check("历史·空内容不记", not h.push("") and not h.push("   ") and len(h) == 2)
h.push("c")
h.push("d")
check("历史·超出上限丢最旧", h.texts() == ["d", "c", "b"], str(h.texts()))
check("历史·按序号取（1=最新）", h.get(1) == "d" and h.get(3) == "b" and h.get(9) is None)
check("历史·摘要截断", h.brief(1) == "d" and len(h.brief(1, 1)) <= 2, str(h.brief(1, 1)))
check("历史·可清空", h.clear() or len(h) == 0)
check("历史条目解析·第2条翻译", tools.parse_clipboard_item("用第2条翻译") == (2, "translate"))
check("历史条目解析·第三条总结", tools.parse_clipboard_item("第三条总结一下") == (3, "summary"))
check("历史条目解析·第十二条念一下", tools.parse_clipboard_item("第十二条念一下") == (12, "read"))
check("历史条目解析·默认动作", tools.parse_clipboard_item("第4条") == (4, "translate"))
check("历史条目解析·不误判", tools.parse_clipboard_item("这条件不错") is None)

# ---------- 文件删除：解析、保护规则、回收站（一律 dry_run，不真删）----------
check("删除解析·第2个", tools.parse_delete_request("删掉第2个") == {"targets": [2], "all": False},
      str(tools.parse_delete_request("删掉第2个")))
check("删除解析·多个序号", tools.parse_delete_request("删除第1个和第3个") == {"targets": [1, 3], "all": False},
      str(tools.parse_delete_request("删除第1个和第3个")))
check("删除解析·中文序号", tools.parse_delete_request("删掉第三个") == {"targets": [3], "all": False},
      str(tools.parse_delete_request("删掉第三个")))
check("删除解析·全部", tools.parse_delete_request("把找到的都删了") == {"targets": [], "all": True},
      str(tools.parse_delete_request("把找到的都删了")))
check("删除解析·不误判普通句子", tools.parse_delete_request("这文件不错") is None)
check("删除解析·无删除词不触发", tools.parse_delete_request("第2个文件在哪") is None)

check("保护规则·程序目录拒删", tools.is_protected(os.getcwd()) is True)
check("保护规则·系统目录拒删", tools.is_protected(os.environ.get("SystemRoot", "C:\\Windows")) is True)
_tmpfile = os.path.join(_tempfile.gettempdir(), "duoduo_del_test.txt")
with open(_tmpfile, "w", encoding="utf-8") as _f:
    _f.write("x")
check("保护规则·普通文件可删", tools.can_delete(_tmpfile) == (True, ""))
check("保护规则·文件夹拒绝", tools.can_delete(_tempfile.gettempdir())[0] is False)
check("保护规则·不存在的文件拒绝", tools.can_delete(_tmpfile + ".nope")[0] is False)
check("protected_roots 非空", len(tools.protected_roots()) >= 2, str(tools.protected_roots()[:2]))
check("dry_run 不真删", tools.send_to_recycle_bin([_tmpfile], dry_run=True) == (1, [])
      and os.path.exists(_tmpfile))
check("空清单不报错", tools.send_to_recycle_bin([], dry_run=True) == (0, ["没有要删的文件"]))
check("批量上限存在", tools.MAX_DELETE_BATCH >= 5, str(tools.MAX_DELETE_BATCH))

# 清理规则：只挑多多自己生成的文件
check("清理解析·截图", (tools.parse_cleanup_request("清理一下你的截图") or {}).get("pattern") == "多多截图_*.png")
check("清理解析·剪贴板", (tools.parse_cleanup_request("清理剪贴板文本") or {}).get("pattern") == "剪贴板_*.txt")
check("清理解析·语音缓存", "duoduo_tts" in (tools.parse_cleanup_request("清空语音缓存") or {}).get("folder", ""))
check("清理解析·不误判", tools.parse_cleanup_request("清理一下房间") is None)
_clean_dir = _tempfile.mkdtemp()
for _n in ("多多截图_1.png", "我的照片.png"):
    with open(os.path.join(_clean_dir, _n), "w", encoding="utf-8") as _f:
        _f.write("x")
_files = tools.collect_cleanup_files({"folder": _clean_dir, "pattern": "多多截图_*.png", "desc": "t"})
check("清理只挑自己生成的文件",
      len(_files) == 1 and _files[0].endswith("多多截图_1.png"),
      str([os.path.basename(x) for x in _files]))
check("清理遇到不存在的目录返回空",
      tools.collect_cleanup_files({"folder": os.path.join(_clean_dir, "没有"), "pattern": "*", "desc": "t"}) == [])
os.remove(_tmpfile)
_shutil.rmtree(_clean_dir, ignore_errors=True)

print("=" * 46)
failed = [n for n, ok in results if not ok]
print(f"PASS {len(results) - len(failed)}/{len(results)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
