# -*- coding: utf-8 -*-
"""
build_exe.py —— 把多多打包成单文件 exe（双击就能启动）
====================================================
设计取舍（重要）：
  · **只打包代码，不打包 frames_opt 素材**。素材有 70MB+，塞进 exe 会变成每次启动
    都要解压几十 MB（慢），而且以后你换素材还得重新打包。
    所以 exe 放在项目目录里，运行时读**同目录**的 frames_opt/、config.json、pet_data.json，
    素材随时可换、可继续用流水线脚本修，不用重打包。
  · 用 --onefile + 无控制台窗口；重依赖（cv2/rembg/PIL/numpy）只在素材流水线里用，排除掉，
    exe 才不会白白大几十 MB（sapi 变调那条路会自动降级，不影响 edge 语音）。

用法：
  python build_exe.py            # 打包到项目目录（多多.exe）
  python build_exe.py --clean    # 先清 build/ 与旧 exe 再打包
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "assets", "duoduo.ico")
ENTRY = os.path.join(HERE, "多多.py")
EXE_NAME = "多多"

EXCLUDES = ["cv2", "rembg", "PIL", "numpy", "scipy", "matplotlib", "pandas",
            "tkinter", "pytest", "setuptools", "pip"]


def make_icon():
    """用 idle 首帧生成 exe 图标（免得到处找图）。"""
    src = os.path.join(HERE, "frames_opt", "idle", "frame_0000.png")
    if not os.path.exists(src):
        print("  [提示] 没有 frames_opt/idle 首帧，跳过图标")
        return None
    try:
        from PIL import Image
    except Exception:
        print("  [提示] 没装 Pillow，跳过图标")
        return None
    os.makedirs(os.path.dirname(ICON), exist_ok=True)
    im = Image.open(src).convert("RGBA")
    a = im.getchannel("A")
    bbox = a.getbbox()
    if bbox:
        pad = 8
        im = im.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                      min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad)))
    side = max(im.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)
    canvas.save(ICON, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"  图标 -> {ICON}")
    return ICON


def _p(text):
    """按控制台编码安全打印（GBK 控制台遇到特殊字符会直接抛异常）。"""
    enc = sys.stdout.encoding or "utf-8"
    try:
        print(text)
    except UnicodeEncodeError:
        print(str(text).encode(enc, "replace").decode(enc, "replace"))


def build(clean=False):
    if clean:
        for d in ("build", "dist"):
            p = os.path.join(HERE, d)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
        exe = os.path.join(HERE, EXE_NAME + ".exe")
        if os.path.exists(exe):
            os.remove(exe)
        print("  已清理 build/dist 与旧 exe")
    icon = make_icon()
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
            "--windowed",                      # 不要黑色控制台窗口
            "--name", EXE_NAME,
            "--distpath", HERE,                # exe 直接放在项目目录
            "--workpath", os.path.join(HERE, "build"),
            "--specpath", os.path.join(HERE, "build")]
    if icon:
        args += ["--icon", icon]
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    # 只有 config.json 需要随包（缺失时程序会自己生成默认的）
    cfg = os.path.join(HERE, "config.json")
    if os.path.exists(cfg):
        args += ["--add-data", f"{cfg}{os.pathsep}."]
    args.append(ENTRY)
    print("  执行:", " ".join(args[2:]))
    proc = subprocess.run(args, cwd=HERE, capture_output=True)
    out = (proc.stdout or b"").decode("utf-8", "ignore") + (proc.stderr or b"").decode("utf-8", "ignore")
    tail = [ln for ln in out.splitlines() if ln.strip()][-6:]
    for ln in tail:
        _p("    " + ln)
    exe = os.path.join(HERE, EXE_NAME + ".exe")
    if os.path.exists(exe):
        _p(f"\n打包成功：{exe}  （{os.path.getsize(exe) / 1024 / 1024:.1f} MB）")
        _p("双击即可启动；配置/存档/素材都在同目录，改素材不用重新打包。")
        return 0
    _p("\n打包失败，请看上面的输出")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="先清理再打包")
    a = ap.parse_args()
    sys.exit(build(clean=a.clean))
