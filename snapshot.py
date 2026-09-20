# -*- coding: utf-8 -*-
"""
snapshot.py —— 帧素材快照与一键回退（改坏了能退回去）
==================================================
为什么需要：`frames_opt/`（452 帧、70MB+）是整套外观的唯一来源。之前的备份只在
`%TEMP%\\duoduo_frames_backup\\`——**系统清理临时文件就没了**。这里把快照存到项目内的
`快照/` 目录，带时间戳和备注，可列出、可回退。

用法：
  python snapshot.py save "修完白块、眼角收圆"     # 存一份快照
  python snapshot.py list                         # 看有哪些快照
  python snapshot.py restore latest               # 回退到最新一份（回退前会自动再存一份当前状态）
  python snapshot.py restore 2                    # 回退到列表里的第 2 份
  python snapshot.py prune --keep 8               # 只保留最近 8 份
"""
import argparse
import json
import os
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "快照")
INDEX = os.path.join(SNAP_DIR, "index.json")
TARGETS = ["frames_opt"]          # 需要保护的目录（外加 meta 里的基线）
EXTRA_FILES = [os.path.join("frames_opt", "_baseline.json")]


def _index_path(snap_dir):
    return os.path.join(snap_dir, "index.json")


def _load_index(snap_dir=SNAP_DIR):
    p = _index_path(snap_dir)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_index(items, snap_dir=SNAP_DIR):
    os.makedirs(snap_dir, exist_ok=True)
    with open(_index_path(snap_dir), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def list_snapshots(snap_dir=SNAP_DIR):
    return _load_index(snap_dir)


def save(note="", root=HERE, snap_dir=SNAP_DIR):
    """把素材目录打包成 zip 快照，返回快照信息。"""
    os.makedirs(snap_dir, exist_ok=True)
    # 文件名要唯一：同一秒里"回退前的自动备份"和"被回退的那份"不能重名，
    # 否则会把源快照覆盖掉（踩过这个坑）
    stamp = time.strftime("%Y%m%d_%H%M%S")
    seq = len(_load_index(snap_dir)) + 1
    name = f"frames_opt_{stamp}_{seq:03d}.zip"
    path = os.path.join(snap_dir, name)
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for rel in TARGETS + EXTRA_FILES:
            base = os.path.join(root, rel)
            if os.path.isfile(base):
                z.write(base, rel)
                count += 1
                continue
            if not os.path.isdir(base):
                continue
            for dirpath, _dirs, files in os.walk(base):
                for fn in files:
                    if fn.endswith((".png", ".json")):
                        full = os.path.join(dirpath, fn)
                        z.write(full, os.path.relpath(full, root))
                        count += 1
    info = {"file": name, "time": stamp, "note": note, "files": count,
            "mb": round(os.path.getsize(path) / 1024 / 1024, 1)}
    items = _load_index(snap_dir)
    items.append(info)
    _save_index(items, snap_dir)
    return info


def restore(which="latest", root=HERE, snap_dir=SNAP_DIR, keep=1):
    """回退到指定快照（回退前自动把当前状态也存一份，保证可反悔）。"""
    items = _load_index(snap_dir)
    if not items:
        return None, "还没有任何快照，先跑 python snapshot.py save"
    if which in ("latest", None, ""):
        idx = len(items) - 1
    else:
        try:
            idx = int(which) - 1
        except ValueError:
            return None, f"看不懂「{which}」，用序号或 latest"
    if not (0 <= idx < len(items)):
        return None, f"没有第 {which} 份快照（共 {len(items)} 份）"
    chosen = items[idx]
    zpath = os.path.join(snap_dir, chosen["file"])
    if not os.path.exists(zpath):
        return None, f"快照文件不见了：{zpath}"
    safety = save("回退前的自动备份", root=root, snap_dir=snap_dir)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(root)
    return chosen, (f"已回退到 {chosen['time']}（{chosen['note'] or '无备注'}）；"
                    f"回退前的状态已存为 {safety['file']}")


def prune(keep=8, snap_dir=SNAP_DIR):
    """只保留最近 keep 份快照。"""
    items = _load_index(snap_dir)
    if len(items) <= keep:
        return 0
    drop = items[:-keep]
    for it in drop:
        p = os.path.join(snap_dir, it["file"])
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    _save_index(items[-keep:], snap_dir)
    return len(drop)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    p_save = sub.add_parser("save", help="存一份快照")
    p_save.add_argument("note", nargs="*", default=[], help="备注")
    sub.add_parser("list", help="列出快照")
    p_res = sub.add_parser("restore", help="回退")
    p_res.add_argument("which", nargs="?", default="latest", help="序号或 latest")
    p_prune = sub.add_parser("prune", help="清理旧快照")
    p_prune.add_argument("--keep", type=int, default=8)
    args = ap.parse_args()

    cmd = args.cmd or "list"
    if cmd == "save":
        info = save(" ".join(args.note))
        print(f"已存快照：{info['file']}（{info['files']} 个文件，{info['mb']} MB）")
        print(f"位置：{SNAP_DIR}")
    elif cmd == "restore":
        chosen, msg = restore(args.which)
        print(msg)
        if chosen:
            print("提示：回退完建议跑一次 python visual_regression.py 确认状态。")
    elif cmd == "prune":
        n = prune(args.keep)
        print(f"清理了 {n} 份旧快照（保留最近 {args.keep} 份）")
    else:
        items = list_snapshots()
        if not items:
            print("还没有快照。用 python snapshot.py save \"备注\" 存一份。")
        else:
            print(f"共 {len(items)} 份快照（目录：{SNAP_DIR}）")
            for i, it in enumerate(items, start=1):
                print(f"  {i}. {it['time']}  {it['mb']:>6.1f}MB  {it['files']:>4} 文件  {it['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
