# -*- coding: utf-8 -*-
"""
test_repo_health.py —— 仓库卫生与稳定性回归（不联网、不起窗口）
覆盖：
  1. 分发相关文件齐不齐（requirements / LICENSE / 素材许可 / CI）
  2. .gitignore 有没有把 ASCII 名的那份 exe 挡住（它曾被误提交，撑大 .git）
  3. 后台任务池 run_async：线程数恒定、任务异常不会拖垮整个池子
  4. 版本号常量存在且格式正确（启动日志靠它认版本）
"""
import os
import re
import sys
import time
import glob
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

import app_health

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)) if (extra and not cond) else ""))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def exists(path):
    return os.path.exists(os.path.join(HERE, path))


# ------------------------------------------------------------ 1. 分发文件
check("requirements.txt 存在", exists("requirements.txt"))
if exists("requirements.txt"):
    req = read("requirements.txt")
    check("requirements.txt 含 PyQt6", "PyQt6" in req)
    check("requirements.txt 标明可选依赖", "可选" in req)

check("requirements-dev.txt 存在", exists("requirements-dev.txt"))
if exists("requirements-dev.txt"):
    dev = read("requirements-dev.txt")
    check("开发依赖含 numpy / Pillow / pyinstaller",
          all(k in dev for k in ("numpy", "Pillow", "pyinstaller")))

check("LICENSE 存在且是 MIT", exists("LICENSE") and read("LICENSE").startswith("MIT License"))
check("素材许可文件存在且写明非商用",
      exists("LICENSE-ASSETS.md") and "非商用" in read("LICENSE-ASSETS.md"))
check("素材许可声明只约束素材、不影响代码",
      exists("LICENSE-ASSETS.md") and "MIT" in read("LICENSE-ASSETS.md"))

wf = os.path.join(".github", "workflows", "tests.yml")
check("CI workflow 存在", exists(wf))
if exists(wf):
    y = read(wf)
    check("CI 跑 test_assistant / test_tools", "test_assistant.py" in y and "test_tools.py" in y)
    check("CI 用离屏模式", "offscreen" in y)
    check("CI 说明了为什么跳过素材测试", "frames_opt" in y)

check("README 提到素材许可", "LICENSE-ASSETS" in read("README.md"))
check("使用说明提到环境变量 key", "DUODUO_API_KEY" in read("使用说明.md"))

# ------------------------------------------------------- 2. .gitignore 漏网
ignore = read(".gitignore")
check(".gitignore 挡住 ASCII 名的 exe", "/duoduo.exe" in ignore)
check(".gitignore 仍挡住中文名的 exe", "多多.exe" in ignore)

# --------------------------------------------------------- 3. 后台任务池
done = []
lock = threading.Lock()


def make_job(i):
    def job():
        time.sleep(0.01)
        with lock:
            done.append(i)
    return job


for i in range(8):
    app_health.run_async(make_job(i))
deadline = time.time() + 10
while len(done) < 8 and time.time() < deadline:
    time.sleep(0.02)
check("8 个任务全部执行完", len(done) == 8, done)

workers = [t for t in threading.enumerate() if t.name.startswith("duoduo-task-")]
check(f"常驻线程数固定为 {app_health._TASK_WORKERS} 个（不是每次新起）",
      len(workers) == app_health._TASK_WORKERS, len(workers))
check("这些线程都是守护线程（不会拖住退出）", all(t.daemon for t in workers))

app_health.run_async(lambda: 1 / 0)          # 故意抛异常：池子必须活下来
after = []
app_health.run_async(lambda: after.append(1))
deadline = time.time() + 5
while not after and time.time() < deadline:
    time.sleep(0.02)
check("任务抛异常后池子照常工作", after == [1])

# ------------------------------------------------------------- 4. 版本号
check("APP_VERSION 存在", isinstance(getattr(app_health, "APP_VERSION", None), str))
check("APP_VERSION 形如 x.y.z", re.fullmatch(r"\d+\.\d+\.\d+", app_health.APP_VERSION or "") is not None,
      getattr(app_health, "APP_VERSION", None))
src = read("多多.py")
check("启动日志打印版本号", "app_health.APP_VERSION" in src)
check("启动日志不再只靠文件时间当版本", "多多 v{" in src)

print("=" * 46)
failed = [n for n, ok in results if not ok]
print(f"PASS {len(results) - len(failed)}/{len(results)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL TESTS PASSED")
