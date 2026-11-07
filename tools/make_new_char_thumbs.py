# -*- coding: utf-8 -*-
"""为新增角色生成 160×160 预览图（2026-11-07）。

用户口径：「角色增加后生成图片补上角色预览图」。
抓图源（safebooru）被 Cloudflare 挡着，所以改用 12B 工作流的试跑图裁切。

⚠️ 尺寸必须 160×160 方形 —— 面板卡片是 aspect-ratio:1/1，非方图会被拉伸变形。
⚠️ 文件名必须等于 characters 里的 `thumb` 字段（slug 化后的 tag），否则面板
   找不到图会退化成首字母色块。

做法：非白包围盒 → 取上部（头顶往下 46%）居中裁方 → 缩放 160 → webp。
"""
import glob
import io
import json
import os
import re
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
THUMBS = os.path.join(PLUGIN, "presets", "thumbs")
SRC_DIR = r"S:/AI_workV5/ComfyUI/output/图片/角色试跑"
SPEC = os.path.join(HERE, "new_characters_20261107.json")
CHARS = os.path.join(PLUGIN, "presets", "anima_v4_characters.json")


def _norm(s):
    """只留字母数字 —— 试跑脚本把括号替换成下划线（会出双下划线），
    这里两边都归一化再比，避免 tamayo__(kimetsu...) 与 tamayo_kimetsu... 对不上。"""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def latest_for(tag):
    """同一 tag 可能跑过多次，取编号最大的那张。"""
    want = _norm(tag)
    best = None
    for p in glob.glob(os.path.join(SRC_DIR, "*.png")):
        base = os.path.basename(p)
        m = re.search(r"_(\d{5})_\.png$", base)
        if not m:
            continue
        if _norm(base[:m.start()]) != want:
            continue
        n = int(m.group(1))
        if best is None or n > best[0]:
            best = (n, p)
    return best[1] if best else None


def crop_square(im):
    """按非白包围盒取上半身，裁成方形。"""
    g = im.convert("L")
    # 220 以上当白底；留一点容差避免描边被吃掉
    mask = g.point(lambda v: 255 if v < 220 else 0)
    bbox = mask.getbbox()
    if not bbox:
        bbox = (0, 0, im.width, im.height)
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    # 取上部 46%（头部 + 胸肩），高度不足时退回全高
    side = int(min(w, h) * 1.0)
    top_h = max(int(h * 0.46), side)
    cy0 = y0
    cy1 = min(y1, cy0 + top_h)
    ch = cy1 - cy0
    side = min(w, ch)
    cx = x0 + (w - side) // 2
    return im.crop((cx, cy0, cx + side, cy0 + side))


def main():
    spec = json.load(io.open(SPEC, encoding="utf-8"))
    chars = {c["tag"]: c for c in json.load(
        io.open(CHARS, encoding="utf-8"))["characters"]}
    ok = miss = 0
    for c in spec["characters"]:
        tag = c["tag"]
        rec = chars.get(tag)
        if not rec:
            print(f"[SKIP] {tag} 不在角色库（先跑 add_characters_batch.py）")
            miss += 1
            continue
        src = latest_for(tag)
        if not src:
            print(f"[MISS] {tag} 没有试跑图")
            miss += 1
            continue
        im = Image.open(src).convert("RGB")
        out = os.path.join(THUMBS, rec["thumb"] + ".webp")
        crop_square(im).resize((160, 160), Image.LANCZOS).save(
            out, "WEBP", quality=88, method=6)
        print(f"[OK] {tag:34s} -> {rec['thumb']}.webp")
        ok += 1
    print(f"\n生成 {ok} 张，跳过 {miss} 张（目录 {THUMBS}）")
    return 0 if miss == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
