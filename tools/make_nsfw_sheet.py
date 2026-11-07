# -*- coding: utf-8 -*-
"""把 NSFW 服装验收图拼成总览（每格标 中文名 + 档位）。"""
import glob, json, os, re
from PIL import Image, ImageDraw, ImageFont

SRC = r"S:/AI_workV5/ComfyUI/output/图片/NSFW服装验收"
LIB = r"S:/AI_workV5/ComfyUI/custom_nodes/Comfyui-Anima-Wardrobe-V6/presets/outfit_library.json"
lib = {o["id"]: o for o in json.load(open(LIB, encoding="utf-8"))["outfits"]}

files = {}
for p in glob.glob(os.path.join(SRC, "*.png")):
    m = re.match(r"(ns_[a-z0-9_]+?)_(\d{5})_\.png$", os.path.basename(p))
    if not m: continue
    k, n = m.group(1), int(m.group(2))
    if k not in files or n > files[k][0]:
        files[k] = (n, p)

items = sorted(files.items(), key=lambda kv: (lib.get(kv[0], {}).get("level", 9), kv[0]))
print(f"拼 {len(items)} 套")
COLS, CW, CH, LAB = 5, 300, 420, 34
rows = (len(items) + COLS - 1) // COLS
sheet = Image.new("RGB", (COLS*CW, rows*(CH+LAB)), "white")
dr = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
except Exception:
    font = ImageFont.load_default()
for i, (k, (n, p)) in enumerate(items):
    o = lib.get(k, {})
    im = Image.open(p).convert("RGB"); im.thumbnail((CW-4, CH-4))
    x = (i % COLS)*CW + (CW-im.width)//2
    y = (i // COLS)*(CH+LAB)
    sheet.paste(im, (x, y))
    dr.text(((i % COLS)*CW+3, y+CH+3), f"L{o.get('level','?')} {o.get('zh',k)}", fill="black", font=font)
    dr.text(((i % COLS)*CW+3, y+CH+19), k, fill="#666", font=font)
out = os.path.join(SRC, "_sheet_nsfw.png")
sheet.save(out)
print("->", out, sheet.size)
