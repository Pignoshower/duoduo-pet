# -*- coding: utf-8 -*-
"""
test_stream.py —— 提速相关的回归（不联网、不起窗口）
覆盖：
  1. 连接复用：两次提问只建一条连接、warmup 只建连接不发请求、连接坏了自动重连一次
  2. 代理环境自动退回 urllib（行为与老版本一致）
  3. 流式：SSE 增量拼装、on_delta 顺序、4xx 当错误抛、出错丢脏连接
  4. 工具回灌那一轮：不带历史、只给 60 token、不写进对话记忆
  5. 分句朗读：句子切分稳定、标签摘干净、首句先念、总字数有上限
"""
import os
import sys
import json
import time
import importlib.util
import urllib.error

os.environ["QT_QPA_PLATFORM"] = "offscreen"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import ai_assistant as ai

ai._system_proxy_enabled = lambda: False        # 本机没开系统代理，测试里显式钉住

spec = importlib.util.spec_from_file_location("kitten", os.path.join(HERE, "多多.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["kitten"] = mod
spec.loader.exec_module(mod)
PetCat = mod.PetCat

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)) if (extra and not cond) else ""))


def wait_for(fn, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def _reply_body(content, mt=8):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


class OkResp:
    """非流式响应（带 status，模拟 http.client）。"""

    status, reason = 200, "OK"

    def __init__(self, content="喵~ 好的"):
        self._b = _reply_body(content)

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SseResp:
    """流式响应：逐行产出 data: {...}。"""

    status, reason = 200, "OK"

    def __init__(self, pieces, status=200):
        self.status = status
        self.lines = []
        for p in pieces:
            self.lines.append(("data: " + json.dumps(
                {"choices": [{"delta": {"content": p}}]}, ensure_ascii=False) + "\n").encode("utf-8"))
        self.lines.append(b"data: [DONE]\n")
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def close(self):
        self.closed = True


def new_client(**cfg):
    c = ai.LLMClient()
    c.cfg["api_key"] = "test-key"
    c.cfg.update(cfg)
    return c


# ============ 1. 纯函数：标签剥离与分句 ============
check("完整标签会被摘掉", ai.strip_stream_tags("喵~[action: hop]好呀") == "喵~好呀")
check("半个标签先藏起来", ai.strip_stream_tags("喵[act") == "喵")
check("普通方括号内容不受影响", ai.strip_stream_tags("编号[1]要留着") == "编号[1]要留着")

s = "这是一句完整的话，用来测试切句。第二句也够长了，继续往下说。"
out, rest = ai.take_sentences(s)
check("按句号切出完整句", out and out[0] == "这是一句完整的话，用来测试切句。", out)
check("剩下的尾巴留着", out and "".join(out) + rest == s, (out, rest))

prefix, _ = ai.take_sentences(s[:20])
check("切分是稳定的：前缀的句子是全量的前缀", out[:len(prefix)] == prefix, (prefix, out))

long_no_stop = "今天要做的事情真的很多很多" * 3 + "，" + "后面还有很多内容要处理" * 3
mid, _ = ai.take_sentences(long_no_stop)
check("等不到句号就在逗号处断", mid and mid[0].endswith("，") and len(mid[0]) >= 12, mid)

tail, rest2 = ai.take_sentences("很短的一句", flush=True)
check("flush 时把尾巴交出来", tail == ["很短的一句"] and rest2 == "")

# ============ 2. 流式请求：拼装与顺序 ============
net = new_client()
seen_bodies = []


def fake_stream(body, timeout):
    seen_bodies.append(json.loads(body.decode("utf-8")))
    return SseResp(["喵~", "好的呀。", "我记住啦。"])


net._pool_post = fake_stream
got = []
full = net.chat_stream("在吗", "多多", 50, got.append)
check("流式拼出全文", full == "喵~好的呀。我记住啦。", full)
check("增量按顺序回调", got == ["喵~", "好的呀。", "我记住啦。"], got)
check("请求体带 stream=True", seen_bodies[0].get("stream") is True, seen_bodies[0].keys())
check("流式回复照样进记忆",
      net.history[-2:] == [{"role": "user", "content": "在吗"},
                           {"role": "assistant", "content": full}], net.history)

err = new_client()
err._pool_post = lambda body, timeout: SseResp([], status=401)
raised = ""
try:
    err.chat_stream("在吗", "多多", 50, None)
except Exception as e:
    raised = str(e)
check("4xx 会当错误抛出（不再静默）", "401" in raised, raised)
check("错误提示是猫话", "钥匙" in err._friendly_error(
    urllib.error.HTTPError("", 401, "Unauthorized", None, None)))

# 传输层出错：退回 urllib，不抛给上层；warmup 失败也不影响使用
broken = new_client()


class BoomPool:
    def post(self, *a):
        raise OSError("boom")

    def warmup(self, *a):
        raise OSError("boom")

    def drop(self):
        pass


broken._pool = BoomPool()
check("连接层出错 → 这次退回 urllib（返回 None）", broken._pool_post(b"{}", 5) is None)
check("warmup 失败不影响使用", broken.warmup() is False)

# 流式读到一半出错：脏连接必须丢掉
dropped2 = []


class HalfResp(SseResp):
    def __iter__(self):
        yield ("data: " + json.dumps({"choices": [{"delta": {"content": "喵"}}]}) + "\n").encode()
        raise OSError("connection reset")


class HalfPool:
    def post(self, *a):
        return HalfResp([])

    def drop(self):
        dropped2.append(1)


half = new_client()
half._pool = HalfPool()
try:
    half.chat_stream("在吗", "多多", 50, None)
except Exception:
    pass
check("流式中途出错会丢掉脏连接", dropped2 == [1], dropped2)

# ============ 3. 连接复用 / warmup / 重连 ============
class FakeConn:
    def __init__(self, key):
        self.key = key
        self.requests = []
        self.connected = False
        self.closed = False
        self.fail_first = False
        self.sock = None
        self.timeout = None

    def connect(self):
        self.connected = True

    def request(self, method, path, body, headers):
        if self.fail_first:
            self.fail_first = False
            raise OSError("stale keep-alive")
        self.requests.append((method, path))

    def getresponse(self):
        return OkResp()

    def close(self):
        self.closed = True


made = []
real_connect = ai._HTTPPool._connect


def fake_connect(key, timeout):
    c = FakeConn(key)
    made.append(c)
    return c


ai._HTTPPool._connect = staticmethod(fake_connect)
try:
    p = new_client()
    p.chat("第一句")
    p.chat("第二句")
    check("两次提问只建一条连接（省掉二次握手）", len(made) == 1, len(made))
    check("同一条连接发了两次请求", len(made[0].requests) == 2, made[0].requests)
    check("请求路径是 /chat/completions（不是 api_base 本身）",
          all(p.endswith("/chat/completions") for _m, p in made[0].requests), made[0].requests)

    p.warmup()
    check("warmup 只建连接、不发请求", made[0].connected and len(made[0].requests) == 2)

    made.clear()
    q = new_client()
    conn1 = q._pool._ensure_locked(q._pool._split(q._endpoint()), 5)
    conn1.fail_first = True                 # 连接被服务端悄悄关了
    resp = q._pool.post(q._endpoint(), b"{}", q._headers(), 5)
    check("连接坏了会自动重连一次并成功", len(made) == 2 and isinstance(resp, OkResp), len(made))
    check("旧连接被关掉", made[0].closed is True, made[0].closed)
finally:
    ai._HTTPPool._connect = real_connect

# ============ 4. 代理环境退回 urllib ============
ai._system_proxy_enabled = lambda: True
calls = []
real_urlopen = ai.urllib.request.urlopen
ai.urllib.request.urlopen = lambda req, timeout=0: (calls.append(1) or OkResp("喵~ 直连成功"))
try:
    z = new_client()
    check("代理开着时不使用复用连接", z._pool_post(b"{}", 5) is None)
    check("代理环境下照常拿到回复", "直连成功" in z.chat("在吗"), calls)
    check("这一轮确实走了 urllib", calls == [1], calls)
finally:
    ai.urllib.request.urlopen = real_urlopen
    ai._system_proxy_enabled = lambda: False

# ============ 5. 工具回灌那一轮：小请求、不污染记忆 ============
n6 = new_client(max_tokens=300)
n6.history = [{"role": "user", "content": "旧话题"}] * 6
captured = {}
n6._chat_once = lambda body, timeout: (captured.update(json.loads(body.decode("utf-8"))) or "喵~ 好的")
done = []
n6.ask_note_async("（系统提示）工具 find 的结果是 pet_data.json", "多多", 50,
                  lambda *a: done.append(a))
check("回灌请求跑完了", wait_for(lambda: done), done)
check("回灌那轮不带历史", len(captured.get("messages", [])) == 2, captured.get("messages"))
check("回灌那轮只给 60 token", captured.get("max_tokens") == 60, captured.get("max_tokens"))
check("回灌不写进对话记忆", n6.history == [{"role": "user", "content": "旧话题"}] * 6)
check("回灌也能带回动作标签", done and done[0][0] == "喵~ 好的", done)

# ============ 6. 界面侧：边到边显示 + 整句先念 ============
class FakeBubble:
    def __init__(self):
        self.text = ""
        self.calls = 0

    def show_message(self, text, duration=4000):
        self.text = text
        self.calls += 1


class FakeSpeaker:
    def __init__(self):
        self.said = []
        self.enabled = True

    def say(self, text, mood=None, **kw):
        self.said.append((text, mood))


class FakeLLM:
    def __init__(self):
        self.cfg = {"confirm_before_send": False}
        self.configured = True
        self.notes = []

    def ask_note_async(self, note, name, affection, cb):
        self.notes.append(note)


class FakePet:
    speak = PetCat.speak
    _reset_stream = PetCat._reset_stream
    _on_llm_delta = PetCat._on_llm_delta
    _speak_stream_sentences = PetCat._speak_stream_sentences
    _on_llm_reply = PetCat._on_llm_reply
    STREAM_SHOW_MS = PetCat.STREAM_SHOW_MS
    STREAM_SPOKEN_MAX = PetCat.STREAM_SPOKEN_MAX

    def __init__(self):
        self.bubble = FakeBubble()
        self.speaker = FakeSpeaker()
        self.voice_on = True
        self.state = "idle"
        self.cat_name = "多多"
        self.affection = 50
        self._speak_seq = 0
        self._tool_rounds = 0
        self.brain = type("B", (), {"llm": FakeLLM()})()
        self.actions = []
        self.tool_speaks = False
        self._reset_stream()

    def update_ui_positions(self):
        pass

    def _is_quiet_now(self):
        return False

    def _infer_mood(self):
        return "normal"

    def _do_action(self, action):
        self.actions.append(action)

    def _run_tool(self, tool):
        if self.tool_speaks:
            self.speak("工具自己已经报过了")
        return "工具结果"


pet = FakePet()
pet._on_llm_delta("喵~")
check("第一个增量就进气泡", pet.bubble.text == "喵~", pet.bubble.text)
check("流式状态已打开", pet._stream_on is True)
pet._on_llm_delta("[action: hop]")
check("标签不会漏进气泡", pet.bubble.text == "喵~", pet.bubble.text)
pet._on_llm_delta("今天天气不错，我陪你写代码吧。")
check("成句后立刻交给语音（不用等整句生成完）",
      pet.speaker.said and pet.speaker.said[0][0].startswith("喵~"), pet.speaker.said)
check("提早念的那句音色参数没变（只传 mood）",
      pet.speaker.said and pet.speaker.said[0][1] in ("normal", "cozy"), pet.speaker.said)
# 真实调用链里 _on_llm_reply 收到的是 parse_tags 之后的干净文本（标签已单独交给 action 参数）
pet._on_llm_reply("喵~今天天气不错，我陪你写代码吧。", "hop")
check("收尾后气泡是定稿文本", pet.bubble.text == "喵~今天天气不错，我陪你写代码吧。", pet.bubble.text)
check("收尾后流式状态清空", pet._stream_on is False and pet._stream_buf == "")
check("收尾不会把整句再念一遍（总字数不超上限）",
      sum(len(t) for t, _ in pet.speaker.said) <= pet.STREAM_SPOKEN_MAX + 10,
      sum(len(t) for t, _ in pet.speaker.said))
check("动作照常触发", pet.actions == ["hop"], pet.actions)

long_pet = FakePet()
for piece in ("一" * 30 + "。") * 6:
    long_pet._on_llm_delta(piece)
check("语音总字数有上限（和原来 [:120] 一致）",
      sum(len(t) for t, _ in long_pet.speaker.said) <= long_pet.STREAM_SPOKEN_MAX,
      sum(len(t) for t, _ in long_pet.speaker.said))
long_pet._on_llm_reply("收尾", None)

# 工具自己报过了 → 不再多问一轮大模型（省一次往返）
p1 = FakePet()
p1.tool_speaks = True
p1._on_llm_reply("我看看", None, ("screenshot", ""))
check("工具自己冒泡过 → 不再追加大模型那一轮", p1.brain.llm.notes == [], p1.brain.llm.notes)

p2 = FakePet()
p2.tool_speaks = False
p2._on_llm_reply("我找找", None, ("find", "pet_data"))
check("工具没冒泡（只列了清单）→ 仍然回灌一轮", len(p2.brain.llm.notes) == 1, p2.brain.llm.notes)
check("回灌轮数有上限", p2._tool_rounds == 1)

p3 = FakePet()
p3._tool_rounds = 1
p3._on_llm_reply("再来", None, ("find", "x"))
check("超过轮数上限就不再加", p3.brain.llm.notes == [], p3.brain.llm.notes)

print("=" * 46)
failed = [n for n, ok in results if not ok]
print(f"PASS {len(results) - len(failed)}/{len(results)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
