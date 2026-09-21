# -*- coding: utf-8 -*-
"""test_assistant.py —— ai_assistant 纯函数单测 + 多多.py 集成冒烟测试（离屏）"""
import os
import sys
import re
import time
import tempfile
import math
import importlib.util

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

import ai_assistant as ai

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


# ============ 1. 纯函数单测 ============
t, a = ai.parse_action("好的喵~ [action: hop]")
check("parse_action 提取动作", t == "好的喵~" and a == "hop", f"{t!r},{a}")
t, a = ai.parse_action("没有标签")
check("parse_action 无标签", t == "没有标签" and a is None, f"{a}")

check("resolve_site b站", ai.resolve_site("帮我打开b站") == "https://www.bilibili.com")
check("resolve_site 直链", ai.resolve_site("打开 https://example.com/a b") == "https://example.com/a")
check("resolve_site 无", ai.resolve_site("今天天气") is None)
check("resolve_app 记事本", ai.resolve_app("打开记事本") == "notepad.exe")
check("resolve_app 无", ai.resolve_app("找文件") is None)
check("search_query 搜索", ai.search_query("搜索 小猫图片") == "小猫图片")
check("search_query 无", ai.search_query("你好") is None)
check("now_text 格式", re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", ai.now_text()) is not None)

# find_files：临时目录
tmp = tempfile.mkdtemp(prefix="dupetscan_")
try:
    open(os.path.join(tmp, "猫咪年度报告.txt"), "w").close()
    sub = os.path.join(tmp, "docs")
    os.makedirs(sub)
    open(os.path.join(sub, "报告模板.docx"), "w").close()
    open(os.path.join(sub, "无关文件.txt"), "w").close()
    got = ai.find_files("报告", roots=[tmp])
    check("find_files 命中2个且排序", len(got) == 2 and got[0].endswith("报告模板.docx"),
          [ai.short_path(p) for p in got])
    check("find_files 无结果", ai.find_files("不存在的词zzz", roots=[tmp]) == [])
    check("short_path 压缩", ai.short_path(os.path.join(tmp, "docs", "a.txt")) == "docs/a.txt")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

client = ai.LLMClient()
client.cfg["api_key"] = ""
check("LLM 未配置时禁用", not client.configured)

# ---------- 打开方式解析 ----------
open_with_cases = [("用记事本打开第1个", "notepad"), ("用画图打开", "mspaint"),
                   ("用浏览器打开第2个", "@browser"), ("换个方式打开第1个", "@picker"),
                   ("选择打开方式", "@picker"), ("用vscode打开它", "code"),
                   ("打开第1个", None), ("帮我打开B站", None)]
for phrase, want in open_with_cases:
    got = ai.parse_open_target(phrase)
    got_exe = got[1] if got else None
    check(f"parse_open_target {phrase}", got_exe == want, f"got={got} want={want}")
check("程序中文名", ai.open_with_cn("notepad") == "记事本" and ai.open_with_cn("xxx") == "xxx")

# ---------- 序号解析（打开第N个） ----------
open_cases = [("打开第2个", 2), ("打开第二个", 2), ("打开 3", 3), ("第2个文件夹", 2),
              ("打开第十二个", 12), ("打开它", None), ("帮我打开B站", None), ("打开一下", None)]
for phrase, want in open_cases:
    got = ai.parse_open_index(phrase)
    check(f"parse_open_index {phrase}", got == want, f"got={got} want={want}")

# ============ 2. 多多.py 集成冒烟 ============
SAVE = os.path.join(HERE, "pet_data.json")
orig = open(SAVE, "rb").read() if os.path.exists(SAVE) else None

from PyQt6.QtWidgets import QApplication

spec = importlib.util.spec_from_file_location("kitten", os.path.join(HERE, "多多.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["kitten"] = mod
spec.loader.exec_module(mod)

app = QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


try:
    # 打桩：避免真的打开浏览器/应用
    opened_urls = []
    opened_cmds = []
    mod.webbrowser.open = lambda url: opened_urls.append(url)
    real_startfile = os.startfile
    os.startfile = lambda cmd: opened_cmds.append(cmd)

    pet = mod.PetCat()
    pet.show()
    pet.behavior_timer.stop()
    pet.brain.llm.cfg["api_key"] = ""   # 集成测试固定走本地逻辑

    check("app starts idle", pet.state == "idle", pet.state)

    pet.handle_user_message("现在几点")
    pump(80)
    check("时间指令", "时间" in pet.bubble.label.text(), pet.bubble.label.text()[:24])

    pet.handle_user_message("帮我打开B站")
    pump(80)
    check("打开B站", opened_urls and "bilibili.com" in opened_urls[-1], str(opened_urls[-1:]))

    pet.handle_user_message("找文件 pet_data")
    pump(80)
    check("找文件 pet_data", "pet_data" in pet.bubble.label.text(), pet.bubble.label.text()[:40])

    pet.handle_user_message("陪我散步")
    pump(80)
    check("散步指令 start-only", pet.pacing, pet.state)
    pet.handle_user_message("陪我再走一圈")
    pump(80)
    check("散步中再下指令不打断", pet.pacing, pet.state)
    pet.handle_user_message("站住")
    pump(80)
    check("停下指令生效", not pet.pacing and pet.state == "idle", f"pace={pet.pacing} st={pet.state}")
    pet.handle_user_message("醒来")
    pump(80)
    check("非睡觉唤醒给确认", "没在睡觉" in pet.bubble.label.text(), pet.bubble.label.text()[:20])
    pet.handle_user_message("去睡觉")
    pump(300)
    check("睡觉指令生效", pet.state == "sleep", pet.state)
    pet.handle_user_message("醒来")
    pump(3200)          # 起身片段约 2.1 秒（真实素材）
    check("醒来指令生效", pet.state == "idle", pet.state)
    pet.affection = 60
    pet.handle_user_message("喂我小鱼干")
    pump(220)
    check("喂食指令生效（动作型本地意图）",
          pet.state == "eat" and pet.affection == 75, f"st={pet.state} aff={pet.affection}")
    pet.change_state("idle", "loop")

    # 动作型本地意图（没有台词也要执行，不能被当成"没听懂"）
    pet.handle_user_message("系统状态")
    pump(1500)
    check("动作型意图·系统状态不被丢弃",
          ("电量" in pet.bubble.label.text() or "内存" in pet.bubble.label.text()),
          pet.bubble.label.text()[:30])

    QApplication.clipboard().setText("多多你好呀")
    pump(30)
    pet.handle_user_message("剪贴板念一下")
    pump(200)
    check("动作型意图·剪贴板朗读不被丢弃",
          "多多你好呀" in pet.bubble.label.text(), pet.bubble.label.text()[:30])

    pet.handle_user_message("你好可爱")
    pump(80)
    check("夸夸指令+动作", "喜欢" in pet.bubble.label.text(), pet.bubble.label.text()[:20])

    rep, act, pending = pet.brain.process_input("帮我写一段自我介绍")
    check("未配置key走本地兜底", pending is False and rep is not None and act is None,
          f"pending={pending}")

    # 模拟大模型异步回复送达（含动作）
    pet._on_llm_reply("喵~ 我最喜欢主人啦！[action: hop]", "hop")
    pump(120)
    check("LLM回复送达并触发动作", "喜欢" in pet.bubble.label.text(), pet.bubble.label.text()[:24])

    # 大模型请求的工具派发（白名单）
    before = len(opened_urls)
    pet._on_llm_reply("这个我帮主人搜一下～", "", ("search", "猫咪"))
    pump(150)
    check("LLM工具派发·搜索",
          len(opened_urls) == before + 1 and "baidu.com" in opened_urls[-1]
          and ("搜" in pet.bubble.label.text() or "主人" in pet.bubble.label.text()),
          str(opened_urls[-1:]))

    pet._on_llm_reply("嗯嗯～", "", ("find", "pet_data"))
    pump(300)
    check("LLM工具派发·找文件", "pet_data" in pet.bubble.label.text(), pet.bubble.label.text()[:32])

    # 未知工具必须被忽略（安全白名单）
    before = len(opened_urls)
    pet._on_llm_reply("好呀～", "", ("rm_rf", "/"))
    pump(120)
    check("未知工具被忽略", len(opened_urls) == before and pet.state in
          ("idle", "walk", "eat", "sleep", "wake", "yawn", "stretch", "turn", "pounce", "knead", "hop"),
          f"{len(opened_urls)} {pet.state}")

    # ---- 打开搜索到的文件 / 文件夹 ----
    before = len(opened_cmds)
    pet.handle_user_message("找文件 pet_data")
    pump(300)
    check("找文件后记住结果", len(pet._last_found) >= 1, str(pet._last_found[:2]))
    check("找文件提示可打开", "打开第1个" in pet.bubble.label.text(), pet.bubble.label.text()[-20:])

    pet.handle_user_message("打开第1个")
    pump(200)
    check("打开第1个走系统默认程序",
          len(opened_cmds) == before + 1 and "pet_data" in str(opened_cmds[-1]),
          str(opened_cmds[-1:]))

    pet.handle_user_message("打开它")
    pump(200)
    check("打开它 = 第一个结果",
          len(opened_cmds) == before + 2 and "pet_data" in str(opened_cmds[-1]), str(opened_cmds[-1:]))

    pet.handle_user_message("打开第9个")
    pump(200)
    check("序号越界给友好提示",
          len(opened_cmds) == before + 2 and ("只找到" in pet.bubble.label.text()
                                              or "哪个" in pet.bubble.label.text()),
          pet.bubble.label.text()[:26])

    real_popen = mod.subprocess.Popen
    popen_calls = []
    mod.subprocess.Popen = lambda *a, **k: (popen_calls.append(a) or None)
    try:
        pet.handle_user_message("打开第1个所在文件夹")
        pump(200)
        check("打开所在文件夹调 explorer /select",
              popen_calls and popen_calls[0][0][:1] == ["explorer"] and "/select," in popen_calls[0][0],
              str(popen_calls[:1]))
    finally:
        mod.subprocess.Popen = real_popen

    saved_found = pet._last_found
    pet._last_found = []
    pet.handle_user_message("打开第2个")
    pump(150)
    check("没搜过就打开给提示", "还没" in pet.bubble.label.text() or "先" in pet.bubble.label.text(),
          pet.bubble.label.text()[:24])
    pet._last_found = saved_found

    # ---- 语音音色指令 ----
    said = []
    import pet_tools as _pt
    real_preview = _pt.SPEAKER.preview
    real_next = _pt.SPEAKER.next_voice
    _pt.SPEAKER.preview = lambda text="": (said.append(("preview", text)) or True)
    _pt.SPEAKER.next_voice = lambda: (said.append(("next", "")) or ("测试音色", True))
    try:
        pet.handle_user_message("试听一下")
        pump(150)
        check("试听指令触发试听", any(k == "preview" for k, _ in said), str(said))
        check("试听时说明当前音色",
              "sapi" in pet.bubble.label.text().lower() or "edge" in pet.bubble.label.text().lower(),
              pet.bubble.label.text()[:40])
        pet.handle_user_message("换个声音")
        pump(150)
        check("换音色指令生效", any(k == "next" for k, _ in said) and "测试音色" in pet.bubble.label.text(),
              pet.bubble.label.text()[:30])
    finally:
        _pt.SPEAKER.preview = real_preview
        _pt.SPEAKER.next_voice = real_next

    # ---- 一致性：每个本地意图都必须有对应处理器（防止"有意图没处理"的静默失效）----
    import inspect
    do_action_src = inspect.getsource(mod.PetCat._do_action)
    do_command_src = inspect.getsource(mod.PetCat._do_command)
    handled_actions = set(re.findall(r'action\s*==\s*"([a-z_]+)"', do_action_src))
    for group in re.findall(r'action\s+in\s+\(([^)]*)\)', do_action_src):
        handled_actions |= set(re.findall(r'"([a-z_]+)"', group))
    handled_commands = set(re.findall(r'cmd\s*==\s*"([a-z_]+)"', do_command_src))

    intent_phrases = ["声音大一点", "静音", "试听一下", "换个声音", "截个屏", "系统状态",
                      "念一下剪贴板", "把剪贴板存下来", "取消提醒", "打开第1个",
                      "打开第1个所在文件夹", "翻译这段", "解释一下这段", "去睡觉", "醒来",
                      "打哈欠", "伸个懒腰", "踩奶", "扑过来", "转个圈", "喂我小鱼干",
                      "陪我散步", "站住", "开启语音", "关闭语音", "25分钟后提醒我喝水"]
    missing, seen = [], set()
    for phrase in intent_phrases:
        _reply, act = pet.brain._handle_local(phrase)
        if act is None:
            continue
        if isinstance(act, tuple):
            seen.add(act[0])
            if act[0] not in handled_commands:
                missing.append(f"{phrase}→{act[0]}(指令)")
        else:
            seen.add(act)
            if act not in handled_actions:
                missing.append(f"{phrase}→{act}")
    check("本地意图都有处理器", not missing and len(seen) >= 15,
          f"missing={missing} 覆盖={len(seen)}")

    # ---- 情绪语音：状态推断 + 大模型 mood 透传 ----
    moods = {}
    real_say = _pt.SPEAKER.say

    def _capture_say(text, dry_run=False, mood=None):
        moods["last"] = mood
        return True

    _pt.SPEAKER.say = _capture_say
    try:
        pet.voice_on = True
        pet.change_state("eat", "action_and_return")
        pet.speak("吧唧吧唧")
        check("吃东西时是开心语气", moods.get("last") == "happy", str(moods))
        pet.change_state("sleep", "loop")
        pet.speak("哈欠…")
        check("睡觉时是困倦语气", moods.get("last") == "sleepy", str(moods))
        pet.change_state("knead", "loop")
        pet.speak("呼噜呼噜")
        check("踩奶时是撒娇语气", moods.get("last") == "cozy", str(moods))
        pet.change_state("idle", "loop")
        pet.speak("平常一句话")
        check("发呆时是平常语气", moods.get("last") == "normal", str(moods))
        pet.speak("我错了喵…", mood="sorry")
        check("显式情绪可覆盖", moods.get("last") == "sorry", str(moods))
        pet._on_llm_reply("主人回来啦！", "", None, "excited")
        check("大模型情绪透传到朗读", moods.get("last") == "excited", str(moods))
    finally:
        _pt.SPEAKER.say = real_say
        pet.voice_on = False
        pet.change_state("idle", "loop")

    pet.voice_on = True
    try:
        check("情绪演示有内容且不崩", pet.mood_showcase() is None)
        check("情绪演示列出了各种语气", "困倦" in pet.bubble.label.text(),
              pet.bubble.label.text()[:34])
    finally:
        pet.voice_on = False

    pet.mood_showcase()          # 语音关着时应给提示，而不是静默什么都不做
    check("语音关着时演示给提示", "开启语音" in pet.bubble.label.text(), pet.bubble.label.text()[:30])

    # ---- 打开方式：指定程序 / 选择框 / 找不到程序 ----
    real_popen2 = mod.subprocess.Popen
    real_which = mod.shutil.which
    launched = []
    mod.subprocess.Popen = lambda *a, **k: (launched.append(a[0] if a else None) or None)
    try:
        pet.handle_user_message("用记事本打开第1个")
        pump(200)
        check("指定程序打开",
              bool(launched) and "notepad" in str(launched[-1]).lower()
              and "pet_data" in str(launched[-1]), str(launched[-1:]))
        pet.handle_user_message("换个方式打开第1个")
        pump(200)
        check("换个方式→Windows 打开方式选择框",
              bool(launched) and "OpenAs_RunDLL" in str(launched[-1]), str(launched[-1:]))
        mod.shutil.which = lambda name: None            # 模拟"没装这个程序"
        pet.handle_user_message("用photoshop打开第1个")
        pump(200)
        check("找不到程序时给提示且不乱开",
              len(launched) == 2 and "找不到" in pet.bubble.label.text(), pet.bubble.label.text()[:30])
    finally:
        mod.subprocess.Popen = real_popen2
        mod.shutil.which = real_which

    # ---- A/B 新功能：提醒持久化 / 可点链接 / 工具链 / 看家 / 拖拽 / 自启 ----
    import app_health as health

    # 1) 提醒持久化
    pet._reminders = []
    pet.add_reminder(600, "喝口水")
    check("提醒立刻落盘", any(r.get("msg") == "喝口水" for r in pet.pet_data.get("reminders", [])),
          str(pet.pet_data.get("reminders")))
    pet._reminders = []
    pet.pet_data["reminders"] = [{"due": time.time() + 600, "msg": "喝口水"}]
    check("重启后能恢复提醒", pet.restore_state() == 1 and len(pet._reminders) == 1,
          str(pet._reminders))
    pet._reminders = []
    pet.pet_data["reminders"] = [{"due": time.time() - 10, "msg": "早就该提醒了"}]
    check("过期提醒不恢复", pet.restore_state() == 0 and not pet._reminders, str(pet._reminders))
    pet.cancel_reminders()

    # 2) 搜索结果做成可点链接
    pet._found_bubble(saved_found[:1])
    html = pet.bubble.label.text()
    check("结果气泡带可点链接",
          "file:///" in html and "duoduo://folder/1" in html and "duoduo://app/" in html,
          html[:60])
    opened_before = len(opened_cmds)
    pet._on_bubble_link("file:///" + saved_found[0].replace("\\", "/"))
    pump(150)
    check("点文件名即打开", len(opened_cmds) == opened_before + 1, str(opened_cmds[-1:]))
    pet._on_bubble_link("file:///不存在/的/文件.txt")
    pump(100)
    check("链接指向的文件没了→给提示", "移走" in pet.bubble.label.text(), pet.bubble.label.text()[:24])

    # 3) 多步工具链：工具结果回灌给大模型再问一轮
    chain = []

    def _chain_chat(text, name="多多", affection=0):
        chain.append(text)
        return "喵~ 已经帮你打开第一个啦" if len(chain) > 1 else "我找找看喵[tool: find pet_data]"

    pet.brain.llm.cfg["api_key"] = "test-key"
    asked = []

    def _sync_ask(text, name, affection, cb):
        asked.append(text)
        cb("喵~ 已经帮你打开第一个啦", None, None, None)      # 同步回调，验证接线

    real_ask = pet.brain.llm.ask_async
    pet.brain.llm.ask_async = _sync_ask
    try:
        pet._tool_rounds = 0
        pet._on_llm_reply("我找找看喵", "", ("find", "pet_data"))
        pump(800)
        check("工具链追加了一轮", len(asked) == 1 and pet._tool_rounds == 1,
              f"ask次数={len(asked)} 轮数={pet._tool_rounds}")
        check("追加轮带上了工具结果与指令",
              bool(asked) and "pet_data" in asked[0] and "工具" in asked[0], str(asked[:1])[:80])
        pump(400)
        check("工具链最终回话进气泡", "打开第一个" in pet.bubble.label.text(), pet.bubble.label.text()[:30])
        # 超过轮数上限就不再追加（防死循环）
        pet._tool_rounds = 0
        pet._on_llm_reply("再找一次", "", ("find", "pet_data"))
        pump(200)
        pet._on_llm_reply("再来", "", ("find", "pet_data"))
        pump(200)
        check("工具链最多追加一轮", len(asked) <= 3, f"ask次数={len(asked)}")
    finally:
        pet.brain.llm.ask_async = real_ask
        pet.brain.llm.cfg["api_key"] = ""

    # 4) 看家模式：离开→睡、回来→打招呼、久坐→提醒
    real_idle = health.idle_seconds
    try:
        health.idle_seconds = lambda: 600            # 主人离开
        pet._can_sleep = lambda: True
        pet.focus_mode = True
        pet._was_away = False
        pet.change_state("idle", "loop")
        pet._focus_tick()
        check("离开后自动去睡", pet.state == "sleep" and pet._was_away, pet.state)
        health.idle_seconds = lambda: 2              # 回来了
        pet._focus_tick()
        check("回来会打招呼", "回来" in pet.bubble.label.text() and not pet._was_away,
              pet.bubble.label.text()[:24])
        health.idle_seconds = lambda: 5              # 一直在忙
        pet._was_away = False
        pet._busy_since = time.time() - 4000
        pet._last_break_ts = 0
        pet._focus_tick()
        check("久坐会提醒休息", "休息" in pet.bubble.label.text() or "动动" in pet.bubble.label.text(),
              pet.bubble.label.text()[:24])
    finally:
        health.idle_seconds = real_idle
        pet.focus_mode = False
        pet.change_state("idle", "loop")

    # 5) 拖拽文件：读内容交给大模型
    import tempfile as _tempfile
    fd, txt = _tempfile.mkstemp(suffix=".md", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# 测试文档\n这里是多多的拖拽测试内容。")
    prompts2 = []

    def _drop_chat(text, name="多多", affection=0):
        prompts2.append(text)
        return "喵~ 这是一份测试文档，共 2 行"

    pet.brain.llm.cfg["api_key"] = "test-key"
    pet.brain.llm.chat = _drop_chat
    try:
        pet.handle_dropped_files([txt])
        pump(1000)
        check("拖拽文件读到内容", prompts2 and "拖拽测试内容" in prompts2[0], str(prompts2[:1])[:60])
        check("拖拽结果进气泡", "测试文档" in pet.bubble.label.text(), pet.bubble.label.text()[:24])
        check("拖拽后记住这个文件", (pet._file_context or {}).get("text", "").find("拖拽测试内容") >= 0,
              str((pet._file_context or {}).get("name")))
        # 追问：下一次提问要带上文件内容
        prompts2.clear()
        pet.handle_user_message("这份文档讲了什么")
        pump(900)
        check("追问会带上文件内容",
              prompts2 and "拖拽测试内容" in prompts2[-1] and "主人说" in prompts2[-1],
              str(prompts2[-1:])[:70])
        check("能问它记的是哪个文件",
              "文件名" in (pet.brain._handle_local("看我拖的文件")[0] or "")
              or ".md" in (pet.brain._handle_local("看我拖的文件")[0] or ""),
              str(pet.brain._handle_local("看我拖的文件")))
        # 忘掉之后不再带上
        reply, _act = pet.brain._handle_local("忘掉这个文件")
        check("可以做«忘掉这个文件»", pet._file_context is None and "忘掉" in (reply or ""), str(reply))
        prompts2.clear()
        pet.handle_user_message("随便聊两句")
        pump(900)
        check("忘掉后不再带上文件内容",
              prompts2 and "拖拽测试内容" not in prompts2[-1], str(prompts2[-1:])[:60])
    finally:
        pet.brain.llm.__dict__.pop("chat", None)
        pet.brain.llm.cfg["api_key"] = ""
        try:
            os.remove(txt)
        except OSError:
            pass

    # 6) 开机自启（打桩，不真的写启动文件夹）
    real_set = health.autostart_set
    calls = []
    health.autostart_set = lambda enable, script=None: (calls.append(enable) or True)
    try:
        pet.toggle_autostart()
        pump(100)
        check("开机自启开关被调用", calls == [True] and "开机" in pet.bubble.label.text(),
              f"{calls} {pet.bubble.label.text()[:20]}")
    finally:
        health.autostart_set = real_set

    # ---- 日程：每天/每周 + 到点三重提醒 + 重复续期 ----
    rep, act = pet.brain._handle_local("每天18:30叫我下班")
    check("日程指令被识别", act and act[0] == "schedule" and act[1]["kind"] == "daily",
          str(act))
    check("番茄钟仍走旧的一次性提醒",
          (pet.brain._handle_local("番茄钟") or (None, None))[1] == ("remind", 1500, "番茄钟结束，休息一下吧"),
          str(pet.brain._handle_local("番茄钟")))
    pet._reminders = []
    msg = pet.add_schedule({"kind": "once", "delay": 1, "msg": "喝水", "at": None, "weekdays": None})
    check("一次性日程确认文案", "1秒后" in msg and "喝水" in msg, msg)
    check("日程写入存档", any(r.get("msg") == "喝水" for r in pet.pet_data.get("reminders", [])),
          str(pet.pet_data.get("reminders"))[:60])
    pump(1600)
    check("到点提醒会响（气泡）", "时间到" in pet.bubble.label.text(), pet.bubble.label.text()[:20])
    check("响过就从待办里移除", not pet._reminders, str(pet._reminders))

    # 重复日程：到点后自动排下一次
    pet._reminders = []
    pet.add_schedule({"kind": "daily", "delay": 0, "msg": "下班", "at": (18, 30), "weekdays": None})
    tok = pet._reminders[0][0]
    pet._fire_schedule(tok)
    check("重复日程会自动续期", len(pet._reminders) == 1
          and pet._reminders[0][3]["kind"] == "daily", str(pet._reminders))
    pet.cancel_reminders()

    # ---- 全屏避让：全屏时躲起来、退出全屏回来、手动隐藏不被强行拉出 ----
    real_fs = health.fullscreen_active
    try:
        check("全屏避让默认开", pet.avoid_fullscreen)
        pet.show()
        health.fullscreen_active = lambda: True
        pet._fullscreen_tick()
        check("全屏时自动躲起来", not pet.isVisible() and pet._hidden_by_fullscreen)
        health.fullscreen_active = lambda: False
        pet._fullscreen_tick()
        check("退出全屏后自己回来", pet.isVisible() and not pet._hidden_by_fullscreen)
        pet.hide()
        pet._hidden_by_fullscreen = False          # 模拟"主人自己藏的"
        pet._fullscreen_tick()
        check("主人自己藏的不被强行拉出", not pet.isVisible())
        pet.show()
        check("避让开关可关", "全屏" in pet.set_avoid_fullscreen(False))
        pet.set_avoid_fullscreen(True)
    finally:
        health.fullscreen_active = real_fs

    # ---- 气泡自适应：长内容不被裁 + 可滚动 + 悬停不消失 ----
    class _Evt:
        def accept(self):
            pass

    pet.bubble.show_message("喵", 4000)
    pump(60)
    short_w = pet.bubble.width()
    long_text = "很长的内容，用来测试气泡会不会装不下。" * 60
    pet.bubble.show_message(long_text, 4000)
    pump(60)
    screen = QApplication.primaryScreen().availableGeometry()
    check("长文本气泡限高（不超出屏幕）",
          pet.bubble.height() <= int(screen.height() * 0.6),
          f"h={pet.bubble.height()} 屏幕={screen.height()}")
    check("长文本气泡会变宽", pet.bubble.width() > short_w,
          f"{short_w} -> {pet.bubble.width()}")
    check("超出部分能滚动看到",
          pet.bubble.scroll.verticalScrollBar().maximum() > 0,
          f"可滚动 {pet.bubble.scroll.verticalScrollBar().maximum()}px")
    check("全文没被截断", pet.bubble.label.text() == long_text,
          f"标签字数={len(pet.bubble.label.text())} 原文={len(long_text)}")
    pet.bubble.show_message("悬停测试", 1500)
    pet.bubble.enterEvent(_Evt())
    check("鼠标停在气泡上时不消失", not pet.bubble.hide_timer.isActive())
    pet.bubble.leaveEvent(_Evt())
    check("移开后恢复自动消失", pet.bubble.hide_timer.isActive())
    pet.bubble.fade_out()
    pump(120)

    # ---- 剪贴板历史：轮询记录 + 按条处理 ----
    pet.clip_history.clear()
    pet._clip_seen = ""
    for txt in ("历史第一条", "历史第二条", "历史第二条", "历史第三条"):
        QApplication.clipboard().setText(txt)
        pet._poll_clipboard()
        pump(40)
    check("剪贴板历史记录并去重", len(pet.clip_history) == 3, str(pet.clip_history.texts()))
    check("剪贴板历史指令", pet.brain._handle_local("剪贴板历史")[1] == "clip_history")
    check("按条指令不会被打开文件抢走",
          pet.brain._handle_local("用第2条翻译")[1] == ("clip_item", 2, "translate"),
          str(pet.brain._handle_local("用第2条翻译")))
    check("打开第N个仍然正常", pet.brain._handle_local("打开第1个")[1] == ("open_found", 1),
          str(pet.brain._handle_local("打开第1个")))
    pet.show_clip_history()
    pump(80)
    check("历史能列在气泡里", "最近复制的" in pet.bubble.label.text(), pet.bubble.label.text()[:30])
    pet.use_clip_item(9, "translate")
    pump(60)
    check("超过条数给提示", "只记了" in pet.bubble.label.text(), pet.bubble.label.text()[:24])

    # ---- 程序化眨眼 / 呼吸随机化 ----
    check("眼睛自动定位到两只", len(pet._eye_spots) == 2, str(pet._eye_spots))
    check("眼睛半径合理（不会撑成半张脸）",
          all(6 <= r <= 30 for _x, _y, r in pet._eye_spots), str(pet._eye_spots))
    check("眨眼的左右位置与猫脸匹配",
          abs(pet._eye_spots[0][0] - pet._eye_spots[1][0]) < 200
          and abs(pet._eye_spots[0][1] - pet._eye_spots[1][1]) < 20,
          str(pet._eye_spots))
    pet.change_state("idle", "loop")
    pet.hop_active = False           # 清掉前面测试留下的物理状态
    pet.hover = 0.0
    pet.pacing = False
    pet._dragging = False
    pet._blink_until = 0
    pet._next_blink = 0
    pet._tick_blink(time.time())
    check("会安排下一次眨眼", pet._next_blink > 0, str(pet._next_blink))
    pet._next_blink = time.time() - 1
    pet._tick_blink(time.time())
    check("到点就会闭眼", pet._blink_until > time.time())
    pet.change_state("walk", "loop")
    pet._tick_blink(time.time())
    check("非发呆姿势不眨眼", pet._blink_until == 0)
    pet.change_state("idle", "loop")
    amp_before = pet._breath_amp
    pet.breathe_phase = 2 * math.pi          # 强制走完一个周期 → 换新参数
    pet._on_fx_tick()
    check("呼吸参数会随机变化",
          0.004 <= pet._breath_amp <= 0.03 and pet.breathe_phase < 2 * math.pi,
          f"{amp_before:.4f} -> {pet._breath_amp:.4f}")

    # ---- 文件删除：确认门槛、只删自己找到的、取消可撤回 ----
    real_recycle = tools.send_to_recycle_bin
    deleted_batches = []
    try:
        def _fake_recycle(paths, dry_run=False):
            deleted_batches.append(list(paths))
            return len(paths), []

        tools.send_to_recycle_bin = _fake_recycle
        dd = tempfile.mkdtemp()
        f1 = os.path.join(dd, "甲.txt")
        f2 = os.path.join(dd, "乙.txt")
        for p in (f1, f2):
            with open(p, "w", encoding="utf-8") as fp:
                fp.write("x")
        pet._last_found = [f1, f2]
        pet._pending_delete = None

        r = pet.brain._handle_local("删掉第1个")
        check("删除要走确认流程", r[1] == ("delete", {"targets": [1], "all": False}), str(r))
        pet.delete_from_found(r[1][1])
        check("确认前一个字节都不动", not deleted_batches and os.path.exists(f1), str(deleted_batches))
        check("待删清单已记录", pet._pending_delete == [f1], str(pet._pending_delete))
        check("气泡列出待删文件并等确认",
              "确认" in pet.bubble.label.text() and "甲.txt" in pet.bubble.label.text(),
              pet.bubble.label.text()[:50])

        pet._do_action("cancel_delete")
        check("取消后清单清空且不删", pet._pending_delete is None and not deleted_batches)

        pet.delete_from_found({"targets": [1], "all": False})
        pet._do_action("confirm_delete")
        check("确认后才真正删除", deleted_batches == [[f1]], str(deleted_batches))
        check("只删指定的那一个", os.path.exists(f2) is True)
        check("删完提示可还原", "回收站" in pet.bubble.label.text(), pet.bubble.label.text()[:40])

        pet._pending_delete = None
        pet._last_found = [os.path.join(os.getcwd(), "多多.py")]
        pet.delete_from_found({"targets": [1], "all": False})
        check("程序目录里的文件拒删",
              pet._pending_delete is None and "不能删" in pet.bubble.label.text(),
              pet.bubble.label.text()[:40])

        pet._last_found = []
        pet.delete_from_found({"targets": [1], "all": False})
        check("没找过文件就提示先找", "找文件" in pet.bubble.label.text(), pet.bubble.label.text()[:30])
        pet._last_found = [f2]
        pet.move_found(9, "桌面")
        check("移动序号越界给提示", "没有第" in pet.bubble.label.text(), pet.bubble.label.text()[:20])
        shutil.rmtree(dd, ignore_errors=True)
    finally:
        tools.send_to_recycle_bin = real_recycle
        pet._pending_delete = None
        pet._last_found = []

    # ---- 右键菜单与全局热键（新入口可发现性）----
    def _all_labels(menu):
        """把菜单连同子菜单里的所有文字摊平，便于断言。"""
        out = []
        for a in menu.actions():
            out.append(a.text())
            if a.menu():
                out.extend(_all_labels(a.menu()))
        return out

    menu = pet.buildContextMenu()
    labels = _all_labels(menu)
    top = [a.text() for a in menu.actions()]
    check("菜单按功能分组", all(any(g in t for t in top) for g in ("互动", "剪贴板", "工具", "设置")),
          " | ".join(top))
    actionable_top = [t for t in top if t.strip() and not t.startswith("💡")]
    check("原来的单命令不再挂在顶层",
          not any(k in t for t in actionable_top
                  for k in ("喂食", "摸摸", "截个屏", "系统状态", "翻译", "总结", "音量")),
          " | ".join(actionable_top))
    check("菜单含翻译/总结剪贴板（在剪贴板分组里）",
          any("翻译" in s and "Ctrl+Alt+C" in s for s in labels)
          and any("总结" in s and "Ctrl+Alt+Z" in s for s in labels),
          " | ".join(labels)[:80])
    check("音色相关菜单项已移除",
          not any(k in s for s in labels for k in ("试听", "音色", "换个声音", "情绪语音演示")),
          " | ".join(s for s in labels if "音" in s or "试听" in s))
    check("菜单仍保留语音播报开关", any("语音播报" in s for s in labels), " | ".join(labels)[:60])
    check("菜单含最近文件入口", any("最近找到的文件" in s for s in labels), " | ".join(labels)[:80])
    vol_menu = [a.menu() for a in menu.actions() if a.menu() and "工具" in a.text()]
    vol_items = []
    for m in vol_menu:
        vol_items += _all_labels(m)
    check("工具组里有音量三项",
          all(any(x in s for s in vol_items) for x in ("大一点", "小一点", "静音")),
          " | ".join(v for v in vol_items if "一点" in v or "静音" in v))

    hot_id = dict((n, i) for n, i, _vk in mod.PetCat.HOTKEYS)
    check("热键表含三个快捷键",
          len(mod.PetCat.HOTKEYS) == 3 and {"chat", "clip_translate", "clip_summary"} == set(hot_id),
          str(sorted(hot_id)))
    pet._hotkeys = {hot_id["chat"]: "chat", hot_id["clip_translate"]: "clip_translate",
                    hot_id["clip_summary"]: "clip_summary"}
    QApplication.clipboard().setText("Hotkey test text.")
    pump(30)
    pet._on_hotkey(hot_id["clip_translate"])
    pump(150)
    check("热键·翻译剪贴板有响应", "key" in pet.bubble.label.text().lower()
          or "密钥" in pet.bubble.label.text() or "剪贴板" in pet.bubble.label.text(),
          pet.bubble.label.text()[:30])
    pet._on_hotkey(999999)          # 未注册的 id 不应崩溃
    pump(60)
    check("热键·未知id安全", True)

    # ---- 音量指令（打桩，不真的按键）----
    import pet_tools as _tools

    real_volume = _tools.volume
    vol_calls = []
    _tools.volume = lambda action, steps=None, dry_run=False: (
        vol_calls.append(action) or _tools.VOLUME_STEPS)
    try:
        pet.handle_user_message("声音大一点")
        pump(80)
        check("音量·调大指令", vol_calls == ["up"] and "音量" in pet.bubble.label.text(),
              f"{vol_calls} {pet.bubble.label.text()[:20]}")
        pet.handle_user_message("静音")
        pump(80)
        check("音量·静音指令", vol_calls == ["up", "mute"], str(vol_calls))
        pet.handle_user_message("太吵了")
        pump(80)
        check("音量·调小指令", vol_calls == ["up", "mute", "down"], str(vol_calls))
    finally:
        _tools.volume = real_volume

    # ---- 剪贴板 AI（打桩大模型）----
    sent_prompts = []
    pet.brain.llm.cfg["api_key"] = ""          # 先测未配置的提示
    QApplication.clipboard().setText("The quick brown fox jumps over the lazy dog.")
    pump(30)
    pet.handle_user_message("翻译一下这段")
    pump(120)
    check("剪贴板·未配置key有提示",
          "key" in pet.bubble.label.text().lower() or "密钥" in pet.bubble.label.text(),
          pet.bubble.label.text()[:30])

    pet.brain.llm.cfg["api_key"] = "test-key"

    def _clip_chat(text, name="多多", affection=0):
        sent_prompts.append(text)
        return "喵～译文：敏捷的棕色狐狸跳过了懒狗"

    pet.brain.llm.chat = _clip_chat
    try:
        pet.handle_user_message("翻译一下这段")
        pump(1200)
        check("剪贴板·翻译请求带原文与任务",
              sent_prompts and "翻译" in sent_prompts[0] and "quick brown fox" in sent_prompts[0],
              str(sent_prompts[:1])[:60])
        check("剪贴板·译文回到气泡", "敏捷" in pet.bubble.label.text(), pet.bubble.label.text()[:30])
        pet.handle_user_message("总结一下这段")
        pump(1200)
        check("剪贴板·总结走另一套提示词",
              len(sent_prompts) == 2 and "总结" in sent_prompts[1], str(sent_prompts[1:])[:60])
    finally:
        pet.brain.llm.__dict__.pop("chat", None)
        pet.brain.llm.cfg["api_key"] = ""

    QApplication.clipboard().setText("")
    pump(30)
    pet.handle_user_message("翻译这段")
    pump(150)
    check("剪贴板·为空时提醒", "空" in pet.bubble.label.text(), pet.bubble.label.text()[:24])

    # 待定流程：先"思考中"，异步回调后替换为正式回复
    # 注意用纯聊天问题：本地意图（找文件/打开站点…）优先级高于大模型，不会走待定
    pet.brain.llm.cfg["api_key"] = "test-key"

    def _slow_chat(text, name="多多", affection=0):
        time.sleep(0.5)          # 放慢，才能观察到"思考中"这一帧
        return "喵～星星眨眼睛是大气在动呀"

    pet.brain.llm.chat = _slow_chat
    try:
        pet.handle_user_message("给我讲讲星星为什么会眨眼睛")
        pump(60)
        thinking = pet.bubble.label.text()
        check("LLM待定提示先出现", ("想" in thinking or "🤔" in thinking) and "星星" not in thinking,
              thinking[:24])
        pump(1200)
        check("LLM回调替换气泡", "星星" in pet.bubble.label.text()
              and "想" not in pet.bubble.label.text(), pet.bubble.label.text()[:40])
    finally:
        pet.brain.llm.__dict__.pop("chat", None)   # 恢复真实 chat 方法
        pet.brain.llm.cfg["api_key"] = ""
finally:
    os.startfile = real_startfile
    if orig is not None:
        open(SAVE, "wb").write(orig)

# ============ 3. 大模型客户端（离线打桩，不联网） ============
import json as _json

net = ai.LLMClient()
net.cfg["api_key"] = "test-key"
net.cfg["history_turns"] = 2
sent = []


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _reply(content):
    return _FakeResp(_json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8"))


real_urlopen = ai.urllib.request.urlopen
try:
    ai.urllib.request.urlopen = lambda req, timeout=0: (
        sent.append(_json.loads(req.data.decode("utf-8"))) or _reply("喵~好的[tool: search 天气]"))

    r1 = net.chat("你好呀", name="多多", affection=60)
    check("LLM请求含system开场", sent[0]["messages"][0]["role"] == "system"
          and "多多" in sent[0]["messages"][0]["content"], sent[0]["messages"][0]["content"][:30])
    check("LLM请求带用户原话", sent[0]["messages"][-1] == {"role": "user", "content": "你好呀"},
          str(sent[0]["messages"][-1]))
    check("LLM解析工具标签", ai.parse_tags(r1)[2] == ("search", "天气"), str(ai.parse_tags(r1)))

    # 情绪标签：[mood: xxx] 会被剥掉并单独返回
    t4, a4, tool4, m4 = ai.parse_tags("喵！我好开心呀[mood: happy][action: hop]")
    check("mood 标签解析", m4 == "happy" and a4 == "hop" and "[mood" not in t4 and "[action" not in t4,
          f"{t4!r} {a4} {m4}")
    check("无 mood 标签时为 None", ai.parse_tags("普通一句话")[3] is None)
    check("mood 大小写不敏感", ai.parse_tags("喵[mood: SLEEPY]")[3] == "sleepy")

    net.chat("再聊一句")
    check("LLM多轮记忆带上文",
          any(m["role"] == "assistant" for m in sent[1]["messages"]), str(len(sent[1]["messages"])))
    check("LLM历史窗口被裁剪", len(net.history) <= 4, str(len(net.history)))
    net.reset_history()
    check("LLM可清空记忆", net.history == [])

    # 网络异常 → 友好提示（不抛异常、不带原始堆栈）
    got = {}


    def _timeout(req, timeout=0):
        raise TimeoutError("timed out")


    ai.urllib.request.urlopen = _timeout
    net.ask_async("喂喂", "多多", 50,
                  lambda t, a, tool, mood=None: got.update(text=t, action=a, tool=tool, mood=mood))
    for _ in range(120):
        if got:
            break
        time.sleep(0.02)
    check("LLM超时给友好提示", bool(got.get("text")) and not got.get("tool")
          and any(k in got["text"] for k in ("超时", "慢", "网络", "再")), str(got))

    # 401 → 提示检查密钥
    import urllib.error


    def _unauth(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)


    ai.urllib.request.urlopen = _unauth
    got.clear()
    net.ask_async("喂喂", "多多", 50,
                  lambda t, a, tool, mood=None: got.update(text=t, action=a, tool=tool, mood=mood))
    for _ in range(120):
        if got:
            break
        time.sleep(0.02)
    check("LLM密钥错误提示", "密钥" in got.get("text", "") or "api" in got.get("text", "").lower(),
          str(got))
finally:
    ai.urllib.request.urlopen = real_urlopen

# ============ 4. 死代理兜底（VPN 关掉后仍有直连重试） ============
net2 = ai.LLMClient()
net2.cfg["api_key"] = "test-key"
calls = {"urlopen": 0, "opener": 0}


class _Resp:
    def read(self):
        return _json.dumps({"choices": [{"message": {"content": "喵~ 直连成功"}}]}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _dead_proxy(req, timeout=0):
    calls["urlopen"] += 1
    raise urllib.error.URLError(ConnectionRefusedError("proxy refused connection"))


class _Opener:
    def open(self, req, timeout=0):
        calls["opener"] += 1
        return _Resp()


real_urlopen2 = ai.urllib.request.urlopen
real_build = ai.urllib.request.build_opener
try:
    ai.urllib.request.urlopen = _dead_proxy
    ai.urllib.request.build_opener = lambda *a, **k: _Opener()
    got = net2.chat("在吗")
    check("代理死掉时自动直连重试",
          calls == {"urlopen": 1, "opener": 1} and "直连成功" in got, f"{calls} got={got}")

    # 服务器真的有回应（401）时不该重试
    def _unauth(req, timeout=0):
        calls["urlopen"] += 1
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    calls.update(urlopen=0, opener=0)
    ai.urllib.request.urlopen = _unauth
    try:
        net2.chat("在吗")
        check("401 不触发代理重试", False, "竟然没抛异常")
    except urllib.error.HTTPError:
        check("401 不触发代理重试", calls["opener"] == 0, str(calls))
finally:
    ai.urllib.request.urlopen = real_urlopen2
    ai.urllib.request.build_opener = real_build

print("=" * 46)
failed = [n for n, ok in results if not ok]
print(f"PASS {len(results) - len(failed)}/{len(results)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
