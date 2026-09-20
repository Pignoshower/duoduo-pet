# -*- coding: utf-8 -*-
"""
pipeline.py —— 素材流水线一键跑（新素材来了不用记顺序）
====================================================
顺序很关键，漏一步就会出事故（今天踩过的坑都在这里）：
  1. integrate_videos      视频 → 抠图帧（含画风体检告警）
  2. fill_gaps --apply     修"被误补成不透明的背景白块"（腿间/体尾之间）
  3. fix_white_patches     再清一遍白块/白雾/白点（含烘焙会重新吸附的边缘）
  4. round_eye_corners     眼角尖刺收圆
  5. prepare_frames        对齐烘焙 → frames_opt（会保留程序生成的 turn）
  6. make_turn             转圈动画必须**在烘焙之后**重做（它读的是 frames_opt/idle）
  7. visual_regression     和基线比，任何退化立刻点名

用法：
  python pipeline.py                 # 全流程（先自动存一份快照）
  python pipeline.py --skip-extract  # 素材没变，只重跑后面的修帧+烘焙
  python pipeline.py --dry           # 只打印将要执行的步骤
  python pipeline.py --accept        # 末尾把当前状态接受为新基线（--update）
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def _p(text):
    enc = sys.stdout.encoding or "utf-8"
    try:
        print(text)
    except UnicodeEncodeError:
        print(str(text).encode(enc, "replace").decode(enc, "replace"))


def steps(skip_extract=False, accept=False):
    out = []
    if not skip_extract:
        out.append(("抠图（视频→帧 + 画风体检）", ["integrate_videos.py"]))
    out += [
        ("修白块（被误补的背景白）", ["fill_gaps.py", "--mode", "auto"]),
        ("清白块/白雾/白点", ["fix_white_patches.py", "--apply"]),
        ("眼角尖刺收圆", ["round_eye_corners.py"]),
        ("对齐烘焙", ["prepare_frames.py"]),
        ("重做转圈动画（必须在烘焙后）", ["make_turn.py", "--frames", "24"]),
        ("视觉回归比对", ["visual_regression.py"] + (["--update"] if accept else [])),
    ]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-extract", action="store_true", help="跳过抠图（素材没变时用）")
    ap.add_argument("--dry", action="store_true", help="只打印计划")
    ap.add_argument("--accept", action="store_true", help="把结果接受为新基线")
    ap.add_argument("--no-snapshot", action="store_true", help="跑之前不存快照")
    args = ap.parse_args()

    plan = steps(args.skip_extract, args.accept)
    _p("流水线计划：")
    for i, (label, cmd) in enumerate(plan, 1):
        _p(f"  {i}. {label:<28} python {' '.join(cmd)}")
    if args.dry:
        return 0

    if not args.no_snapshot:
        try:
            import snapshot as snap
            info = snap.save("流水线前的自动快照")
            _p(f"\n[快照] {info['file']}（{info['mb']} MB）——跑坏了可以 snapshot restore latest 退回")
        except Exception as e:
            _p(f"[快照] 跳过：{e}")

    for i, (label, cmd) in enumerate(plan, 1):
        _p(f"\n=== [{i}/{len(plan)}] {label} ===")
        proc = subprocess.run([PY, "-u"] + cmd, cwd=HERE)
        if proc.returncode != 0:
            _p(f"\n✗ 第 {i} 步「{label}」失败（退出码 {proc.returncode}），已停下。")
            _p("  修好之后可以： python pipeline.py --skip-extract 接着跑后面的步骤")
            return proc.returncode
    _p("\n✓ 流水线全部完成。重启多多即可看到新帧。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
