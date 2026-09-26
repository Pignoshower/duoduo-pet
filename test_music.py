# -*- coding: utf-8 -*-
"""
test_music.py —— 音乐功能回归（不联网、不出声、不弹窗）
覆盖：
  1. 本地指令解析：放歌 / 点歌名 / 切歌 / 暂停继续 / 循环随机 / 状态，且不抢别的指令
  2. 曲库：只认音乐扩展名、按名排序、有缓存、按歌手或歌名模糊搜
  3. 播放器状态机（dry_run 记录 MCI 命令）：open/play/setaudio、切歌回绕、单曲循环、
     暂停继续、停止、放完自动下一首、打不开的格式给人话
  4. 说话时压低音乐、说完恢复
  5. 系统媒体键映射（遥控网易云/QQ音乐/Spotify）
  6. 与 多多.py 的接线：本地意图 → ("music", …)，普通闲聊不会被抢
"""
import os
import sys
import tempfile
import importlib.util

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import pet_tools as tools

# 测试里不要读到这台机器真实的 ~/Music，保证结果确定
tools.MUSIC_DIRS_DEFAULT = ()

spec = importlib.util.spec_from_file_location("kitten", os.path.join(HERE, "多多.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["kitten"] = mod
spec.loader.exec_module(mod)

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication
app = QApplication([])

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)) if (extra and not cond) else ""))


# ============ 1. 指令解析 ============
cases = [("放首歌", "play"), ("随便放首歌", "play"), ("来首歌", "play"), ("听歌", "play"),
         ("放音乐", "play"), ("下一首", "next"), ("换一首", "next"), ("切歌", "next"),
         ("上一首", "prev"), ("暂停音乐", "pause"), ("暂停播放", "pause"),
         ("继续播放", "resume"), ("接着放歌", "resume"),
         ("别放了", "stop"), ("关掉音乐", "stop"), ("停止播放", "stop"),
         ("单曲循环", "repeat_one"), ("列表循环", "repeat_all"), ("随机播放", "shuffle_on"),
         ("在放什么", "now"), ("现在放的是什么", "now"), ("打开音乐文件夹", "folder")]
bad = [(t, tools.parse_music_request(t)) for t, want in cases
       if (tools.parse_music_request(t) or {}).get("action") != want]
check(f"{len(cases)} 条口头指令都能识别", not bad, bad)

q_cases = [("我想听山丘", "山丘"), ("放一首周杰伦的歌", "周杰伦"),
           ("播放 晴天", "晴天"), ("放一下夜的第七章", "夜的第七章")]
q_bad = [(t, tools.parse_music_request(t)) for t, want in q_cases
         if (tools.parse_music_request(t) or {}).get("query") != want]
check("点歌名能解析出查询词", not q_bad, q_bad)

others = ["今天天气不错", "打开B站", "找文件 报告", "站住", "别说话", "把第1个移动到桌面",
          "别吵我", "25分钟后提醒我喝水", "删掉第2个"]
stolen = [(t, tools.parse_music_request(t)) for t in others if tools.parse_music_request(t)]
check("不会抢别的指令", not stolen, stolen)

check("“随便放首歌”= 随机挑一首",
      (tools.parse_music_request("随便放首歌") or {}).get("shuffle") is True)
check("“随机播放”= 打开随机模式",
      (tools.parse_music_request("随机播放") or {}).get("action") == "shuffle_on")

check("没在放歌时“暂停”不算音乐指令", tools.parse_music_request("暂停") is None)
check("正在放歌时“暂停”才算",
      (tools.parse_music_request("暂停", playing=True) or {}).get("action") == "pause")
check("“继续”同理", tools.parse_music_request("继续") is None
      and (tools.parse_music_request("继续", playing=True) or {}).get("action") == "resume")

# ============ 2. 曲库 ============
tmp = tempfile.mkdtemp(prefix="duoduo_music_")
os.makedirs(os.path.join(tmp, "子目录"), exist_ok=True)
for rel in ("周杰伦 - 晴天.mp3", "B - 告白气球.MP3", "readme.txt",
            os.path.join("子目录", "C - 夜的第七章.flac")):
    with open(os.path.join(tmp, rel), "w", encoding="utf-8") as f:
        f.write("不是真音频；dry_run 下播放器不会去解码")

mp = tools.MusicPlayer(cfg={"dirs": [tmp], "volume": 80, "duck_when_speaking": True}, dry_run=True)
lib = mp.library()
check("曲库只认音乐扩展名（含子目录、大小写不敏感）", len(lib) == 3, lib)
check("曲库按文件名排序", lib == ["B - 告白气球", "C - 夜的第七章", "周杰伦 - 晴天"], lib)
check("曲库有缓存（第二次不重扫）", mp._scan() is mp._scan())
check("按歌手搜到", [n for n, _p in mp.search("周杰伦")] == ["周杰伦 - 晴天"], mp.search("周杰伦"))
check("按歌名搜到", bool(mp.search("晴天")) and mp.search("晴天")[0][0] == "周杰伦 - 晴天")
check("搜不到就返回空", mp.search("绝对不存在的歌xyz") == [])
check("配置的目录优先", tools.music_dirs({"dirs": [tmp]}) == [tmp])
check("目录都不存在时没有可扫目录",
      tools.MusicPlayer(cfg={"dirs": [os.path.join(tmp, "不存在")]}, dry_run=True).dirs == [])

# ============ 3. 播放器状态机（dry_run 记录 MCI 命令）============
ok, msg = mp.play(query="晴天")
check("点歌成功并回话", ok and "晴天" in msg, msg)
check("发了 open / play / setaudio",
      any(c.startswith('open "') for c in mp.commands)
      and f"play {mp.ALIAS}" in mp.commands
      and any(c.startswith("setaudio") for c in mp.commands), mp.commands[:4])
check("现在处于播放中", mp.active and mp.mode() == "playing")
check("状态文案带歌名", "晴天" in mp.now_text(), mp.now_text())

first_title = mp.title
ok, msg = mp.next()
check("下一首能换歌", ok and mp.title != first_title, (msg, mp.title))
ok, msg = mp.prev()
check("上一首回到刚才那首", ok and mp.title == first_title, (msg, mp.title))
mp._index = 0
mp.prev()
check("第一首再往上会回绕到最后一首", mp.title == lib[-1], (mp.title, lib[-1]))

ok, msg = mp.pause()
check("暂停", ok and mp.mode() == "paused" and "停一下" in msg, msg)
ok, msg = mp.resume()
check("继续", ok and mp.mode() == "playing", msg)
ok, msg = mp.stop()
check("别放了会 close 掉", ok and not mp.active
      and any(c.startswith("close") for c in mp.commands), (msg, mp.commands[-3:]))
check("没在放歌时停止会给提示", mp.stop()[0] is False)


class DonePlayer(tools.MusicPlayer):
    """模拟"这首放完了"：MCI 报 stopped。"""

    def mode(self):
        return "stopped"


auto = DonePlayer(cfg={"dirs": [tmp]}, dry_run=True)
auto.play()
finished = auto.tick()
check("一首放完会自动下一首", bool(finished) and auto.title != finished, (finished, auto.title))

loop = DonePlayer(cfg={"dirs": [tmp]}, dry_run=True)
loop.play()
only = loop.title
loop.repeat = "one"
loop.tick()
check("单曲循环时放完是重放这一首", loop.title == only, (only, loop.title))

broken = tools.MusicPlayer(cfg={"dirs": [tmp]}, dry_run=True)
broken._mci = lambda cmd: (broken.commands.append(cmd), False)[1]
ok, msg = broken.play()
check("打不开的格式给人话提示", (not ok) and "打不开" in msg, msg)

empty = tools.MusicPlayer(cfg={"dirs": [os.path.join(tmp, "不存在")]}, dry_run=True)
ok, msg = empty.play()
check("一首歌都没有时给人话提示", (not ok) and "没找到" in msg, msg)

# ============ 4. 说话时压低音乐 ============
mpv = tools.MusicPlayer(cfg={"dirs": [tmp], "volume": 80}, dry_run=True)
mpv.play()
mpv.commands.clear()
check("说话时压低到 18%", mpv.duck(True) and mpv.commands[-1].endswith("to 180"), mpv.commands)
check("已经压低时不重复发命令", mpv.duck(True) is False)
check("说完恢复到 80%", mpv.duck(False) and mpv.commands[-1].endswith("to 800"), mpv.commands)
check("没在放歌时 duck 不动手",
      tools.MusicPlayer(cfg={"dirs": [tmp]}, dry_run=True).duck(True) is False)
check("可以关掉说话压低",
      tools.MusicPlayer(cfg={"dirs": [tmp], "duck_when_speaking": False},
                        dry_run=True).duck_when_speaking is False)
check("调音量会同步给 MCI",
      mpv.set_volume(30) == 30 and mpv.commands[-1].endswith("to 300"), mpv.commands[-1])

# ============ 5. 系统媒体键 ============
check("下一首媒体键", tools.media_key("next", dry_run=True) == 0xB0)
check("播放暂停媒体键", tools.media_key("play_pause", dry_run=True) == 0xB3)
check("上一首媒体键", tools.media_key("prev", dry_run=True) == 0xB1)
check("停止媒体键", tools.media_key("stop", dry_run=True) == 0xB2)
check("认不出的动作返回 None", tools.media_key("xxx", dry_run=True) is None)

# ============ 6. 与 多多.py 的接线 ============
class FakeMusic:
    active = False
    dirs = [tmp]


class FakePetObj:
    pass


fake = FakePetObj()
fake.music = FakeMusic()
fake._file_context = None
brain = mod.AIBrain(fake)
rep, cmd = brain._handle_local("放首歌")
check("本地意图识别放歌", cmd == ("music", {"action": "play"}), cmd)
rep2, cmd2 = brain._handle_local("我想听晴天")
check("本地意图识别点歌", cmd2 == ("music", {"action": "play_query", "query": "晴天"}), cmd2)
rep3, cmd3 = brain._handle_local("今天天气不错")
check("普通闲聊不会被音乐指令抢走", cmd3 is None, cmd3)


class FakePet(QObject):
    """把 PetCat 的音乐方法绑到一个假猫上（QTimer 需要 QObject 父对象，所以继承 QObject）。"""

    do_music = mod.PetCat.do_music
    _open_music_folder = mod.PetCat._open_music_folder
    _ensure_music_timer = mod.PetCat._ensure_music_timer
    _music_tick = mod.PetCat._music_tick
    _duck_music = mod.PetCat._duck_music
    _unduck = mod.PetCat._unduck

    def __init__(self, music):
        super().__init__()
        self.music = music
        self._music_timer = None
        self._duck_seq = 0
        self.spoken = []

    def speak(self, text, duration=4500, mood=None):
        self.spoken.append(text)


opened_dirs = []
_real_startfile = getattr(os, "startfile", None)
os.startfile = lambda p: opened_dirs.append(p)
try:
    pet = FakePet(tools.MusicPlayer(cfg={"dirs": [tmp]}, dry_run=True))
    play_msg = pet.do_music({"action": "play"})
    check("菜单里放歌会回话", "正在放" in play_msg, play_msg)
    check("放歌后自动下一首的定时器启动了",
          pet._music_timer is not None and pet._music_timer.isActive())
    check("暂停 / 继续 / 循环 / 随机 / 状态都有回话",
          all(pet.do_music({"action": a}) for a in ("pause", "resume", "repeat_one", "repeat_all",
                                                    "shuffle_on", "now")))
    folder_msg = pet.do_music({"action": "folder"})
    check("打开音乐文件夹", bool(folder_msg) and opened_dirs == [tmp], (folder_msg, opened_dirs))
    check("别放了之后定时器停下",
          pet.do_music({"action": "stop"}) and not pet._music_timer.isActive())
    stop_msg = pet.do_music({"action": "stop"})
    check("没在放歌时停止有提示", "没在放" in stop_msg, stop_msg)
    weird_msg = pet.do_music({"action": "???"})
    check("不认识的音乐动作也不炸", "不认识" in weird_msg, weird_msg)
finally:
    if _real_startfile is not None:
        os.startfile = _real_startfile

speaking = FakePet(tools.MusicPlayer(cfg={"dirs": [tmp], "volume": 50}, dry_run=True))
speaking.music.play()
speaking.music.commands.clear()
speaking._duck_music(20)
check("猫说话时音乐被压低",
      speaking.music.commands and speaking.music.commands[-1].endswith("to 180"),
      speaking.music.commands)
speaking._unduck(speaking._duck_seq)
check("说完恢复（时长按字数估）",
      speaking.music.commands[-1].endswith("to 500"), speaking.music.commands[-1])

print("=" * 46)
failed = [n for n, ok in results if not ok]
print(f"PASS {len(results) - len(failed)}/{len(results)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
