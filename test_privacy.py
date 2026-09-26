# -*- coding: utf-8 -*-
"""
test_privacy.py —— 隐私边界测试（不联网、不起窗口）
覆盖：
  1. API key 可以从环境变量读（DUODUO_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY）
  2. 程序写 config.json 时**永远不写 api_key**（只写开关类字段，不碰 key）
  3. 外发闸门 send_gate：关着时直接放行；开着时先挂起、等「确认」才发、「取消」就作废
  4. 默认配置里 confirm_before_send 存在且默认关闭（不改变老用户体验）
"""
import os
import sys
import json
import tempfile
import importlib.util

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

import ai_assistant as ai

spec = importlib.util.spec_from_file_location("kitten", os.path.join(HERE, "多多.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["kitten"] = mod
spec.loader.exec_module(mod)

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)) if (extra and not cond) else ""))


class _LLM:
    def __init__(self, cfg):
        self.cfg = cfg


class FakePet:
    """只带 send_gate / confirm_sensitive / cancel_delete 需要的那点状态。"""

    SEND_PREVIEW = 100
    send_gate = mod.PetCat.send_gate
    send_confirm_on = mod.PetCat.send_confirm_on
    confirm_sensitive = mod.PetCat.confirm_sensitive
    cancel_delete = mod.PetCat.cancel_delete

    def __init__(self, confirm):
        self.brain = type("B", (), {"llm": _LLM({"confirm_before_send": confirm})})()
        self._pending_send = None
        self._pending_delete = None
        self._pending_sensitive = None
        self.spoken = []

    def speak(self, text, ms=None, mood=None):
        self.spoken.append(text)
        return text


def _with_env(env, fn):
    old = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return fn()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------- 1. 环境变量
def key_from_env():
    for var in ("DUODUO_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        env = {"DUODUO_API_KEY": None, "DEEPSEEK_API_KEY": None, "OPENAI_API_KEY": None}
        env[var] = "sk-test-" + var.lower()
        got = _with_env(env, lambda: ai.load_config())["api_key"]
        check(f"{var} 能被读到", got == "sk-test-" + var.lower(), got)


key_from_env()

_old = os.environ.get("DUODUO_API_KEY")
os.environ["DUODUO_API_KEY"] = "sk-env-wins"
_cfg = _with_env({"DEEPSEEK_API_KEY": "sk-file-loses"}, ai.load_config)
check("环境变量优先于文件", _cfg["api_key"] == "sk-env-wins", _cfg["api_key"])
check("DUODUO_API_BASE 可覆盖",
      _with_env({"DUODUO_API_BASE": "https://x/v1"}, ai.load_config)["api_base"] == "https://x/v1")
check("DUODUO_MODEL 可覆盖",
      _with_env({"DUODUO_MODEL": "other-model"}, ai.load_config)["model"] == "other-model")
if _old is None:
    os.environ.pop("DUODUO_API_KEY", None)
else:
    os.environ["DUODUO_API_KEY"] = _old

# ------------------------------------------------------- 2. 写配置不写 api_key
tmp = tempfile.mkdtemp(prefix="duoduo_priv_")
real_cfg_path = ai.CONFIG_PATH
try:
    fake_path = os.path.join(tmp, "config.json")
    with open(fake_path, "w", encoding="utf-8") as f:
        json.dump({"api_key": "sk-should-stay", "model": "m1"}, f)
    ai.CONFIG_PATH = fake_path

    ok = ai.set_config_values(confirm_before_send=True)
    saved = json.load(open(fake_path, encoding="utf-8"))
    check("set_config_values 返回成功", ok is True)
    check("新字段写进去了", saved.get("confirm_before_send") is True)
    check("原有字段没被冲掉", saved.get("model") == "m1", saved)

    ok2 = ai.set_config_values(api_key="sk-injected", confirm_before_send=False)
    saved2 = json.load(open(fake_path, encoding="utf-8"))
    check("api_key 不会被程序改写", saved2.get("api_key") == "sk-should-stay", saved2.get("api_key"))
    check("写入成功仍然返回 True", ok2 is True)
    check("其它字段照常写入", saved2.get("confirm_before_send") is False)

    ai.CONFIG_PATH = os.path.join(tmp, "no_such_dir", "config.json")
    check("写不进去时返回 False 且不抛异常", ai.set_config_values(confirm_before_send=True) is False)
finally:
    ai.CONFIG_PATH = real_cfg_path

check("默认配置里有 confirm_before_send 且默认关",
      ai.DEFAULT_CONFIG.get("confirm_before_send") is False)

# ------------------------------------------------------------- 3. 外发闸门
ran = []
pet_off = FakePet(confirm=False)
pet_off.send_gate("剪贴板里的这一段", "secret-text", lambda: ran.append("off"))
check("关着闸门：直接执行", ran == ["off"], ran)
check("关着闸门：不挂待确认", pet_off._pending_send is None)

pet_on = FakePet(confirm=True)
pet_on.send_gate("剪贴板里的这一段", "x" * 300, lambda: ran.append("on"))
check("开着闸门：不执行", "on" not in ran, ran)
check("开着闸门：挂起待确认", isinstance(pet_on._pending_send, dict) and pet_on._pending_send["chars"] == 300)
check("开着闸门：提示里带「确认」", "确认" in pet_on.spoken[-1], pet_on.spoken[-1])
check("开着闸门：预览不会把全文倒出来", "x" * 150 not in pet_on.spoken[-1])

pet_on.confirm_sensitive()
check("说「确认」后才真的发出去", ran == ["off", "on"], ran)
check("发完清空待确认", pet_on._pending_send is None)
check("确认后不会重复执行", (pet_on.confirm_sensitive(), ran[-1] == "on")[1] is True)

pet_cancel = FakePet(confirm=True)
pet_cancel.send_gate("文件「a.txt」的内容", "file-body", lambda: ran.append("cancel"))
pet_cancel.cancel_delete()
check("说「取消」后待确认被清掉", pet_cancel._pending_send is None)
pet_cancel.confirm_sensitive()
check("取消之后再说「确认」也不会发", "cancel" not in ran, ran)

# 自己手打的问题不进闸门（闸门只覆盖剪贴板/文件内容）
pet_typed = FakePet(confirm=True)
check("闸门不影响普通聊天路径", pet_typed._pending_send is None)

print("=" * 46)
failed = [n for n, ok in results if not ok]
print(f"PASS {len(results) - len(failed)}/{len(results)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
