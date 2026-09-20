# -*- coding: utf-8 -*-
"""test_app_smoke.py —— 多多.py 全功能动态冒烟测试（离屏）
覆盖：启动/帧库、随机行为全分支、拖拽物理与眩晕衰减、托盘、气泡淡出、
      动作指令、聊天指令、系统能力（提醒/语音/截图/剪贴板/状态/跟随/吸附）、存档。
目的：抓"方法缺失/运行期异常"这类静态检查查不出的问题。
"""
import os
import re
import sys
import time
import importlib.util

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPointF

spec = importlib.util.spec_from_file_location("kitten", os.path.join(HERE, "多多.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["kitten"] = mod
spec.loader.exec_module(mod)

SAVE = os.path.join(HERE, "pet_data.json")
orig = open(SAVE, "rb").read() if os.path.exists(SAVE) else None

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


class FakeMouse:
    def __init__(self, x, y, buttons=Qt.MouseButton.LeftButton):
        self._p = QPointF(x, y)
        self._b = buttons

    def button(self):
        return Qt.MouseButton.LeftButton

    def buttons(self):
        return self._b

    def globalPosition(self):
        return self._p

    def accept(self):
        pass


class FakeDT:
    """可控的 datetime 替身（只用到 .now().hour）"""
    hour = 12

    @classmethod
    def now(cls):
        return cls


app = QApplication([])


def pump(ms):
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


try:
    opened = []
    mod.webbrowser.open = lambda url: opened.append(url)

    real_random = mod.random.random
    real_datetime = mod.datetime
    mod.datetime = FakeDT

    pet = mod.PetCat()
    pet.show()
    pet.behavior_timer.stop()
    pet.brain.llm.cfg["api_key"] = ""     # 集成测试固定走本地逻辑
    pet._follow_enabled = False           # 关掉鼠标跟随，保证朝向类断言确定

    lw, lh = pet.lib.logical_w, pet.lib.logical_h
    check("启动/帧库", pet.state == "idle" and all(pet.animations.get(s) for s in ("idle", "eat", "sleep", "walk")),
          f"{ {s: len(v) for s, v in pet.animations.items()} }")
    check("画布尺寸一致", (pet.width(), pet.height()) == (lw, lh), f"{pet.width()}x{pet.height()} vs {lw}x{lh}")

    # ---------- 0. 渲染架构（防"残影"回归） ----------
    check("半透明窗口禁用系统背景(显式清屏)", pet.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground))
    check("移动与重绘同一定时器(无独立 walk_timer)", not hasattr(pet, "walk_timer") and hasattr(pet, "_advance_walk"))
    pet.pacing = True
    pet._pace_x, pet._pace_target, pet.facing = 100.0, 400.0, 1
    x0 = pet.x()
    pump(100)                                  # 仅靠 fx 定时器推进
    moved_by_fx = pet.x() != x0
    pet._stop_pace()
    pet.change_state("idle", "loop")
    check("散步位移由 fx 定时器驱动", moved_by_fx, f"x {x0} -> {pet.x()}")

    # ---------- 1. 缺失方法回归 ----------
    check("_look_around 存在", hasattr(pet, "_look_around"))
    pet._look_around()
    pump(1900)
    check("张望后朝向复位", pet.facing == 1, f"facing={pet.facing}")

    # ---------- 2. 随机行为全分支 ----------
    def fire(r, hour, label):
        mod.random.random = lambda: r
        FakeDT.hour = hour
        try:
            pet._on_behavior_fire()
            pet.behavior_timer.stop()
            pet.pacing = False
            if pet.state != "idle":
                pet.change_state("idle", "loop")
            return True, ""
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    branches = [(0.05, 2, "深夜彩蛋"), (0.15, 12, "饭点彩蛋"), (0.30, 15, "散步"),
                (0.50, 15, "张望"), (0.62, 15, "伸懒腰跳"), (0.80, 15, "自言自语"),
                (0.95, 15, "打盹")]
    ok_all, detail = True, ""
    for r, hour, label in branches:
        ok, d = fire(r, hour, label)
        if not ok:
            ok_all, detail = False, f"{label} -> {d}"
            break
        pump(60)
    check("随机行为7个分支无异常", ok_all, detail)
    mod.random.random = real_random

    # ---------- 3. 拖拽物理 / 眩晕 ----------
    try:
        pet.mousePressEvent(FakeMouse(500, 400))
        dragging = pet._dragging
        for i in range(8):   # 快速拖动 → 触发眩晕
            pet.mouseMoveEvent(FakeMouse(500 + i * 120, 400))
            time.sleep(0.004)
        dizzy = pet._dizzy
        pet.mouseReleaseEvent(FakeMouse(1200, 400))
        released = not pet._dragging
        pump(2500)           # 等眩晕衰减
        check("拖拽/眩晕/释放", dragging and dizzy and released and not pet._dizzy and pet.hover >= 0,
              f"drag={dragging} dizzy={dizzy} released={released} now_dizzy={pet._dizzy} hover={pet.hover:.1f}")
    except Exception as e:
        check("拖拽/眩晕/释放", False, f"{type(e).__name__}: {e}")

    # ---------- 4. 气泡淡出 ----------
    pet.speak("测试气泡")
    pump(120)
    vis = pet.bubble.isVisible()
    pet.bubble.fade_out()
    pump(500)
    check("气泡显示→淡出隐藏", vis and (not pet.bubble.isVisible() or pet.bubble.opacity_effect.opacity() <= 0.01),
          f"vis={pet.bubble.isVisible()} op={pet.bubble.opacity_effect.opacity():.2f}")

    # ---------- 5. 托盘 ----------
    try:
        has_tray = hasattr(pet, "tray_icon")
        pet.toggle_visibility()
        hidden = not pet.isVisible()
        pet.toggle_visibility()
        check("托盘/显示隐藏切换", has_tray and hidden and pet.isVisible(),
              f"tray={has_tray} hidden={hidden}")
    except Exception as e:
        check("托盘/显示隐藏切换", False, f"{type(e).__name__}: {e}")

    # ---------- 6. 动作指令 ----------
    ok_all, detail = True, ""
    for act in ("hop", "eat", "pace", "stop", "sleep", "wake", "feed",
                "yawn", "stretch", "knead", "turn", "pounce"):
        try:
            pet._do_action(act)
            pump(120)
        except Exception as e:
            ok_all, detail = False, f"{act}: {type(e).__name__}: {e}"
            break
    pet.change_state("idle", "loop")
    check("动作指令全通(含新动作)", ok_all, detail)

    # ---------- 6b. 吃饭收尾：不留残影、不卡死 ----------
    pet.change_state("idle", "loop")
    pet.affection = 50
    pet.feed_cat()
    n_eat = len(pet.animations["eat"])
    check("吃饭结尾无残影(定格1帧,不溶解)", len(pet.seq) == n_eat + 1, f"seq={len(pet.seq)} eat={n_eat}")
    pump(5200)                                       # 等吃完
    check("吃饭后回到发呆(不卡住)", pet.state == "idle" and pet.frame_mode == "loop",
          f"state={pet.state} mode={pet.frame_mode}")

    # ---------- 6b2. 好感度满也能喂 ----------
    pet.affection = 100
    pet.feed_cat()
    pump(120)
    check("满好感也能喂食", pet.state == "eat" and pet.affection == 100,
          f"state={pet.state} aff={pet.affection}")
    pump(4500)

    # ---------- 6c. 睡醒用真实起身片段 / 散步停下软着陆 ----------
    pet._go_sleep()
    pump(400)
    slept_state = pet.state
    pet._wake_from_sleep()
    waking_state = pet.state                     # 有 wake 素材时应进入 wake 状态
    pump(3000)                                   # 等起身片段播完
    check("睡觉→起身片段→发呆",
          slept_state == "sleep" and waking_state in ("wake", "idle") and pet.state == "idle",
          f"sleep={slept_state} wake={waking_state} now={pet.state}")
    # 走→停（确定性设置目标，避免随机导致测试抖动）
    pet.change_state("walk", "loop")
    pet.pacing = True
    pet._pace_x = float(pet.x())
    pet._pace_target = pet.x() + 300
    pet.facing = 1
    pump(200)
    pet._stop_pace()
    stop_bridge = pet.frame_mode == "seq"
    pump(500)
    check("散步停下有软着陆且回到发呆", stop_bridge and pet.state == "idle" and not pet.pacing,
          f"bridge={stop_bridge} state={pet.state}")

    # ---------- 6d. 行为真实感：睡眠冷却 + 排期间隔 ----------
    pet._go_sleep()                       # 记录一次睡眠时间
    pump(200)
    slept = not pet._can_sleep()
    pet.change_state("idle", "loop")
    pet._schedule_behavior()
    interval = pet.behavior_timer.interval()
    pet.behavior_timer.stop()
    check("睡眠有冷却(不会醒了又睡)", slept, f"can_sleep={pet._can_sleep()}")
    check("随机间隔真实(≥20秒)", interval >= 20000, f"interval={interval}ms")

    # ---------- 7. 聊天指令 ----------
    dialogs = ["现在几点", "打开B站", "找文件 pet_data", "陪我散步", "站住",
               "去睡觉", "醒来", "喂我小鱼干", "你好可爱"]
    ok_all, detail = True, ""
    for d in dialogs:
        try:
            pet.handle_user_message(d)
            pump(80)
            pet._stop_pace()
            if pet.state != "idle":
                pet.change_state("idle", "loop")
        except Exception as e:
            ok_all, detail = False, f"{d}: {type(e).__name__}: {e}"
            break
    check("聊天指令全通", ok_all, detail)
    check("打开B站生效", any("bilibili" in u for u in opened), str(opened[-1:]))

    # ---------- 8. 系统能力（语音/提醒/截图/剪贴板/状态/跟随/吸附） ----------
    # 定时提醒：登记 → 到点触发 → 取消
    try:
        pet._do_action(("remind", 1, "喝水"))
        pump(80)
        registered = len(pet._reminders) == 1 and "提醒" in pet.bubble.label.text()
        pet.bubble.label.setText("")
        pump(1600)                                    # 等提醒到点
        fired = "时间到" in pet.bubble.label.text()
        pet._do_action("cancel_remind")
        pump(80)
        check("定时提醒：登记 + 到点触发 + 取消",
              registered and fired and not pet._reminders,
              f"registered={registered} fired={fired}")
    except Exception as e:
        check("定时提醒：登记 + 到点触发 + 取消", False, f"{type(e).__name__}: {e}")

    # 语音开关（不实际发声：只切状态）
    try:
        pet._do_action(("voice", True))
        pump(80)
        on_msg = pet.bubble.label.text()
        pet._do_action(("voice", False))
        pump(80)
        check("语音开关：可开可关", (not pet.voice_on) and len(on_msg) > 3,
              f"voice_on={pet.voice_on} msg={on_msg[:24]}")
    except Exception as e:
        check("语音开关：可开可关", False, f"{type(e).__name__}: {e}")

    # 截图（离屏环境可能拿不到屏幕，允许失败但必须给出提示且不崩；测试产生的文件自动删除）
    try:
        pet._do_action("screenshot")
        pump(120)
        msg = pet.bubble.label.text()
        made = re.search(r"多多截图_[\d_]+\.png", msg)
        if made:                                   # 测试不留垃圾文件
            for d in (os.path.join(os.path.expanduser("~"), "Desktop"), os.path.expanduser("~")):
                p = os.path.join(d, made.group())
                if os.path.exists(p):
                    os.remove(p)
        check("截图指令有反馈", "截" in msg, msg[:30])
    except Exception as e:
        check("截图指令有反馈", False, f"{type(e).__name__}: {e}")

    # 剪贴板朗读
    try:
        QApplication.clipboard().setText("多多测试文本")
        pet._do_action("clip_read")
        pump(120)
        check("剪贴板朗读", "多多测试文本" in pet.bubble.label.text(), pet.bubble.label.text()[:40])
    except Exception as e:
        check("剪贴板朗读", False, f"{type(e).__name__}: {e}")

    # 系统状态（子线程取数，回来后走 llm_reply 信号；先清掉提醒避免串台）
    try:
        pet._do_action("cancel_remind")
        pet._do_action("status")
        pet.bubble.label.setText("")
        pump(9000)
        txt = pet.bubble.label.text()
        check("系统状态有反馈",
              any(k in txt for k in ("电量", "内存", "网络", "CPU", "读不到")), txt[:40])
    except Exception as e:
        check("系统状态有反馈", False, f"{type(e).__name__}: {e}")

    # 鼠标跟随（临时打开，验证不崩且会改朝向）
    try:
        pet._follow_enabled = True
        pet._follow_tick = 19
        pet.state = "idle"
        pet._follow_cursor()
        check("鼠标跟随注视不崩", pet.facing in (1, -1), f"facing={pet.facing}")
        pet._follow_enabled = False
    except Exception as e:
        check("鼠标跟随注视不崩", False, f"{type(e).__name__}: {e}")

    # 屏幕边缘吸附
    try:
        wa = QApplication.primaryScreen().availableGeometry()
        pet.move(wa.left() + 5, wa.top() + 200)
        pet._snap_to_edge()
        check("靠边自动吸附", pet.x() == wa.left(), f"x={pet.x()} left={wa.left()}")
    except Exception as e:
        check("靠边自动吸附", False, f"{type(e).__name__}: {e}")

    # 全局热键：注册幂等（重复调用不能打乱分发表，否则热键会全部失效）
    try:
        pet.initHotkey()
        before = dict(pet._hotkeys)
        pet.initHotkey()
        after = dict(pet._hotkeys)
        check("热键注册幂等", before == after and (not after or pet._hotkey_ok),
              f"{before} -> {after} ok={pet._hotkey_ok}")
        if after:
            check("热键·分发到输入框", callable(pet._on_hotkey) and pet._on_hotkey(max(after)) is None)
        else:
            # 热键被别的程序（或主人正在运行的多多）占用时注册不上，属正常情况
            check("热键·分发到输入框", True, "（本次热键被占用，跳过实际分发）")
    except Exception as e:
        check("热键注册幂等", False, f"{type(e).__name__}: {e}")

    # ---------- 9. 存档 ----------
    try:
        pet._save_now()
        check("存档写入", os.path.exists(SAVE))
    except Exception as e:
        check("存档写入", False, f"{type(e).__name__}: {e}")

finally:
    mod.random.random = real_random if 'real_random' in dir() else mod.random.random
    mod.datetime = real_datetime if 'real_datetime' in dir() else mod.datetime
    if orig is not None:
        open(SAVE, "wb").write(orig)

print("=" * 50)
failed = [n for n, ok in results if not ok]
print(f"PASS {len(results) - len(failed)}/{len(results)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
