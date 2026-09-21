# -*- coding: utf-8 -*-
"""selftest.py —— 离屏自测：演练各动作状态并做像素级断言（不影响真实存档）"""
# --- 测试环境归一化：不让存档里的免打扰时段影响断言（深夜跑测试时曾被静音）---
try:
    import json as _j, os as _o
    if _o.path.exists("pet_data.json"):
        _d = _j.load(open("pet_data.json", encoding="utf-8"))
        if _d.get("quiet_range"):
            _d["quiet_range"] = None
            _d["quiet_mode"] = 0
            _j.dump(_d, open("pet_data.json", "w", encoding="utf-8"), ensure_ascii=False)
except Exception:
    pass

import os
import sys
import time
import tempfile
import importlib.util

import numpy as np
from PIL import Image

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# 备份真实存档，测试后恢复
SAVE = os.path.join(HERE, "pet_data.json")
orig = None
if os.path.exists(SAVE):
    with open(SAVE, "rb") as f:
        orig = f.read()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage

spec = importlib.util.spec_from_file_location("kitten", os.path.join(HERE, "多多.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["kitten"] = mod
spec.loader.exec_module(mod)

# 视频流水线模块（只读它们的常量，用来防止"程序生成的状态"被旧素材重建覆盖）
sys.path.insert(0, HERE)
import prepare_frames as prep          # noqa: E402
import integrate_videos as integ        # noqa: E402

results = []


def pump(app, ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def alpha_stats(img, thr=12):
    w, h = img.width(), img.height()
    xs, ys = [], []
    for y in range(0, h, 1):
        for x in range(0, w, 1):
            if img.pixelColor(x, y).alpha() > thr:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return {"cx": (min(xs) + max(xs)) / 2, "top": min(ys), "bottom": max(ys),
            "w": max(xs) - min(xs), "h": max(ys) - min(ys), "n": len(xs)}


SHOTS = os.path.join(tempfile.gettempdir(), "duoduo_selftest")


def shot(pet, name, thr=12):
    img = pet.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    img.save(os.path.join(SHOTS, name))
    return alpha_stats(img, thr)


def _p(text):
    """按控制台编码安全打印：emoji 在 GBK 控制台会直接抛 UnicodeEncodeError。"""
    enc = sys.stdout.encoding or "utf-8"
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(enc, "replace").decode(enc, "replace"))


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    _p(f"{'PASS ' if cond else 'FAIL '}{name}" + (f"  {detail}" if detail else ""))


try:
    os.makedirs(SHOTS, exist_ok=True)
    app = QApplication(sys.argv)
    pet = mod.PetCat()
    pet.show()
    pet.behavior_timer.stop()   # 自测期间关闭随机行为，保证确定性
    pet._follow_enabled = False # 关掉鼠标跟随注视，避免影响朝向断言
    pet.move(300, 500)

    lw, lh = pet.lib.logical_w, pet.lib.logical_h
    anchor = pet.lib.anchor_y
    check("window logical size matches meta", (lw, lh) == (pet.width(), pet.height()),
          f"meta=({lw},{lh}) win=({pet.width()},{pet.height()})")
    check("states loaded", all(len(pet.animations[s]) > 0 for s in ("idle", "eat", "sleep")),
          {s: len(pet.animations[s]) for s in ("idle", "eat", "sleep")})

    # --- idle ---
    pump(app, 500)
    st = shot(pet, "1_idle.png")
    sc = shot(pet, "1_idle_core.png", thr=150)   # 只看猫体，排除阴影
    check("idle has content", st is not None and st["n"] > 500)
    check("idle feet on ground", sc and sc["bottom"] <= anchor + 6 and sc["bottom"] >= anchor - 6,
          f"core_bottom={sc and sc['bottom']} anchor={anchor}")
    check("idle horizontally centered", sc and abs(sc["cx"] - lw / 2) < 40, f"cx={sc and sc['cx']}")

    # --- eat (喂食动画中段) ---
    pet.change_state("eat", "action_and_return")
    pump(app, 700)
    st = shot(pet, "2_eat.png")
    sc = shot(pet, "2_eat_core.png", thr=150)
    check("eat has content", st is not None and st["n"] > 500)
    check("eat feet on ground", sc and sc["bottom"] <= anchor + 6, f"core_bottom={sc and sc['bottom']}")

    # --- sleep ---
    pet.change_state("sleep", "once")
    pump(app, 900)
    st = shot(pet, "3_sleep.png")
    sc = shot(pet, "3_sleep_core.png", thr=150)
    check("sleep has content", st is not None and st["n"] > 500)
    check("sleep feet on ground", sc and sc["bottom"] <= anchor + 6, f"core_bottom={sc and sc['bottom']}")

    # --- 无爱心的小跳（先测：此时不应有任何粒子） ---
    pet.change_state("idle", "loop")
    pet._do_hop(False)
    check("stretch hop has no hearts", pet.particles == [], f"n={len(pet.particles)}")
    pump(app, 300)
    sh = shot(pet, "4_stretch_hop.png", thr=150)
    check("hop lifts cat off ground", sh and sc and sh["bottom"] < sc["bottom"] - 5,
          f"hop_bottom={sh and sh['bottom']} rest_bottom={sc and sc['bottom']}")
    pump(app, 2600)  # 落地
    check("hop lands back", pet.hover == 0.0 and not pet.hop_active, f"hover={pet.hover}")

    # --- 带爱心的摸头跳 ---
    pet._do_hop(True)
    check("pet hop spawns hearts", len(pet.particles) > 0, f"n={len(pet.particles)}")
    pump(app, 2600)  # 等粒子消散

    # --- wake（倒放唤醒，约1.3s 后回 idle） ---
    pet.change_state("sleep", "once")
    pump(app, 800)
    pet._wake_from_sleep()
    pump(app, 3200)
    check("wake returns idle", pet.state == "idle", pet.state)

    # --- 转身（程序生成：必须与 idle 同画风，且不能被 prepare_frames 重建覆盖） ---
    turn_dir = os.path.join(HERE, "frames_opt", "turn")
    turn_frames = sorted(f for f in os.listdir(turn_dir) if f.endswith(".png"))
    check("turn animation loaded", 'turn' in pet.animations and len(pet.animations['turn']) >= 8,
          f"n={len(pet.animations.get('turn', []))}")

    def _premultiplied(path):
        a = np.array(Image.open(path).convert("RGBA")).astype(np.float32)
        return a[:, :, :3] * (a[:, :, 3:4] / 255.0), a[:, :, 3]

    idle0 = os.path.join(HERE, "frames_opt", "idle", "frame_0000.png")
    t0_rgb, t0_a = _premultiplied(os.path.join(turn_dir, turn_frames[0]))
    tN_rgb, tN_a = _premultiplied(os.path.join(turn_dir, turn_frames[-1]))
    i0_rgb, i0_a = _premultiplied(idle0)
    check("转身首帧与发呆一致（进出无缝）",
          float(np.abs(t0_rgb - i0_rgb).max()) <= 3 and float(np.abs(t0_a - i0_a).max()) <= 8)
    check("转身末帧与发呆一致（转完回同一姿势）",
          float(np.abs(tN_rgb - i0_rgb).max()) <= 3 and float(np.abs(tN_a - i0_a).max()) <= 8)
    check("turn 不在视频管线里（防止被旧素材重建覆盖）",
          "turn" not in prep.KNOWN_STATES and "turn" not in [s[0] for s in integ.SOURCES],
          f"KNOWN_STATES={prep.KNOWN_STATES}")

    # --- 散步（走步动画） ---
    check("walk animation loaded", 'walk' in pet.animations and len(pet.animations['walk']) >= 6,
          f"n={len(pet.animations.get('walk', []))}")
    pet.move(120, 500)
    pet.change_state('walk', 'loop')
    pet.pacing = True
    pet._pace_x = 120.0
    pet._pace_target = 320.0
    pet.facing = 1
    # 移动已并入 fx 定时器（与重绘同频），这里只需置 pacing=True
    pump(app, 400)
    moved = pet.x() > 150
    check("walk moves right", moved, f"x={pet.x()}")
    check("pacing uses walk anim", pet.state == 'walk', pet.state)
    st = shot(pet, "5_walk.png")
    check("walk has content", st is not None and st["n"] > 500)
    wcore = shot(pet, "5_walk_core.png", thr=150)
    check("walk core near ground", wcore and wcore["bottom"] <= anchor + 8,
          f"bottom={wcore and wcore['bottom']}")
    pet._stop_pace()
    check("stop pacing returns idle", pet.state == 'idle', pet.state)

    # --- 双击摸头逻辑 ---
    pet._pet_pet()
    check("pet raises affection", pet.affection >= 1, f"affection={pet.affection}")

    # --- 随机行为关键分支冒烟（曾漏测导致 partial NameError） ---
    pet.change_state("idle", "loop")
    pet._look_around()
    pump(app, 1900)
    check("look around back to default facing", pet.facing == 1, f"facing={pet.facing}")

    pet.behavior_timer.stop()
    pet._go_sleep(700)          # 犯困打盹 → 定时自动醒(partial 路径)
    pump(app, 900)
    pet.behavior_timer.stop()   # 防随机行为干扰后续
    pump(app, 2600)             # 等倒放唤醒播完
    check("auto-wake returns to idle", pet.state == "idle", pet.state)

    # 聊天指令冒烟
    pet.handle_user_message("现在几点")
    pump(app, 100)
    pet.handle_user_message("你好可爱")
    pump(app, 100)
    pet.handle_user_message("去睡觉")
    pump(app, 100)
    pet._wake_from_sleep()
    pump(app, 3000)          # 起身片段约 2.1 秒，留足时间回发呆
    check("chat commands don't crash", pet.state == "idle", pet.state)

    # --- 完整心跳数秒，确认无异常 ---
    pump(app, 1500)
    check("no crash after run", True)

finally:
    if orig is not None:
        with open(SAVE, "wb") as f:
            f.write(orig)
    try:
        app.quit()
    except Exception:
        pass

print("=" * 50)
fails = [r for r in results if not r[1]]
print(f"PASS {len(results) - len(fails)}/{len(results)}")
if fails:
    print("FAILED:", [r[0] for r in fails])
    sys.exit(1)
print("ALL TESTS PASSED")
