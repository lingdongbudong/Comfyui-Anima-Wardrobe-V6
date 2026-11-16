# -*- coding: utf-8 -*-
"""服装库预览图批量出图 + 裁切入库（v6.21.0，v6.25.0 放宽到 L0+L1）。

一轮 120 套（L0 84 + L1 36）：逐套 enable -> 本地预检 -> 出竖屏全身图 -> 直接裁成
`presets/outfit_thumbs/<id>.webp`（长边 440 / q78），前端卡片按 thumb 显示。

用户口径（2026-11-14）：
  · 角色固定雷电将军 raiden_shogun（**无官方服装预设**，不会顶掉服装库）
  · 大胸 = 节点 breast_size 的「大胸」档
  · 竖屏全身照 = 720x1280 + 钉 shot_size=full body
  · v6.21.0 只做 L0；**v6.25.0 起做到 L1**（用户 16:44「服装库 L1 服装补预览图」），
    L2 及以上仍不出图（`S.OUTFIT_THUMB_MAX_LEVEL` 是唯一闸门，别在这里另写一份）

⚠️ 五条铁律（沿用 verify_v6201_batch.py 的血泪）：
  1. weights_json 用 R.empty_weights() 起手，三层齐全；
  2. 钉 shot_size 时其余候选全部显式写 0；
  3. pick_pool_mode / pick_pool_ids 必须清空；
  4. 服装库启用态是服务端全局态、不进节点输入 —— 每套换 seed 与
     SaveImage 前缀保证 hash 不同；finally 复位 active=""。
  5. **出图档位必须跟套的档位走**（`LEVEL_KEY`）：L1 套用 sfw 档会撞
     `_outfit_triplet()` 的档位闸门，整套让路 → 出图是随机服装。

⚠️ 中断续跑：加 --only-missing 会跳过盘上已有 webp 的套装。

用法：
    PY="S:/AI_workV5/python_embeded/python.exe"
    $PY tools/run_outfit_thumbs.py --dry
    $PY tools/run_outfit_thumbs.py                     # 全量（L0+L1）
    $PY tools/run_outfit_thumbs.py --only-missing      # 断点续跑
    $PY tools/run_outfit_thumbs.py --start 25 --limit 25
    $PY tools/run_outfit_thumbs.py --only ol_knit_pleated
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN, "tests"))
import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")
S = _boot.load("anima_v4_store")

BASE = "http://127.0.0.1:8188"
WF = r"C:/Users/VGBFC/Desktop/V6-Anima_miaomiaoHarem_anima12-10步.json"
LIB = os.path.join(PLUGIN, "presets", "outfit_library.json")
THUMB_DIR = os.path.join(PLUGIN, "presets", "outfit_thumbs")
OUT_ROOT = "图片/服装预览_v621"
OUT_ABS = os.path.join("S:/AI_workV5/ComfyUI/output", OUT_ROOT.replace("/", os.sep))
LOG = os.path.join(OUT_ABS, "outfit_thumbs.jsonl")

ROLE = "raiden_shogun"
BREAST = "大胸"
# 出图上限（含）。**必须与 `S.OUTFIT_THUMB_MAX_LEVEL` 一致** —— 后端按那个值发 `thumb`，
# 这里按它决定给谁出图；两边不一致就会出现"有图但前端不发请求"或反之。
MAX_LEVEL = S.OUTFIT_THUMB_MAX_LEVEL
# 套的档位 → 节点的 `nsfw_level`。⚠️ 别一律用 sfw：`_outfit_triplet()` 有档位闸门，
# L1 套在 sfw 档会让整套让路，出图变成随机服装（本地预检也会跟着判红）。
LEVEL_KEY = {0: "sfw", 1: "suggestive", 2: "explicit", 3: "hardcore", 4: "extreme"}
THUMB_LONG_EDGE = 440
THUMB_QUALITY = 78

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def req(path, obj=None, timeout=90):
    if obj is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(
            BASE + path, data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"})
    with _opener.open(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def wait(pid, timeout=420):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = req("/history/%s" % pid)
        if pid in h and h[pid].get("status", {}).get("completed"):
            return h[pid]
        time.sleep(2)
    raise TimeoutError(pid)


def weights():
    """关场景 + 钉 shot_size=full body（其余 5 个候选全写 0）。"""
    w = R.empty_weights()
    w["fields"]["n_canon"] = 0.0
    _sec, fld = R.field_by_key("shot_size")
    all_en = [en for _zh, en in fld.get("opts", [])]
    w["fields"]["shot_size"] = 3.0
    w["options"]["shot_size"] = {en: 0.0 for en in all_en}
    w["options"]["shot_size"]["full body"] = 3.0
    return w


def build_prompt(oid, seed, w, level_key="sfw"):
    d = json.load(open(WF, encoding="utf-8"))
    npk = next(k for k, v in d.items()
               if v["class_type"] in ("AnimaV6RandomPrompt",
                                      "AnimaV5RandomPrompt",
                                      "AnimaV4RandomPrompt"))
    p = d[npk]["inputs"]
    p["nsfw_level"] = level_key
    p["seed"] = seed
    p["selected_roles"] = ROLE
    p["force_role"] = ROLE
    p["role_works"] = ""
    p["role_fav_only"] = False
    p["role_pick"] = True
    p["scene_pick"] = False
    p["breast_size"] = BREAST
    p["pick_pool_mode"] = ""
    p["pick_pool_ids"] = ""
    p["weights_json"] = json.dumps(w, ensure_ascii=False)
    for k, v in d.items():
        ct = v["class_type"]
        if ct.startswith("Seed") or "KSampler" in ct:
            if "seed" in v["inputs"]:
                v["inputs"]["seed"] = seed + 500000
        if ct == "XB_ImageParamsMaster":
            v["inputs"]["width"] = 720
            v["inputs"]["height"] = 1280
            v["inputs"]["aspect_ratio"] = "9:16"
        if ct == "SaveImage":
            v["inputs"]["filename_prefix"] = "%s/%s" % (OUT_ROOT, oid)
    return d


def to_thumb(src, dst):
    """原图 -> 等比不裁剪的 webp（长边 440）。返回字节数。"""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    im.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.LANCZOS)
    im.save(dst, "WEBP", quality=THUMB_QUALITY, method=6)
    return os.path.getsize(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--steps", default="", help="覆盖采样步数（调试用）")
    args = ap.parse_args()

    lib = json.load(open(LIB, encoding="utf-8"))
    todo = [o for o in lib["outfits"] if int(o.get("level") or 0) <= MAX_LEVEL]
    if args.only:
        todo = [o for o in todo if o["id"] == args.only]
    if args.only_missing:
        todo = [o for o in todo if not os.path.isfile(
            os.path.join(THUMB_DIR, "%s.webp" % o["id"]))]
    if args.start:
        todo = todo[args.start:]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("没有待出图的套装")
        return 0

    os.makedirs(OUT_ABS, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    print("待出图 %d 套（角色 %s / %s / 720x1280 全身 / 档位 <= L%d）"
          % (len(todo), ROLE, BREAST, MAX_LEVEL))
    if args.dry:
        for i, o in enumerate(todo, 1):
            print("  %02d  L%d  %-30s %s"
                  % (i, int(o.get("level") or 0), o["id"], o["zh"]))
        return 0

    logf = open(LOG, "a", encoding="utf-8")
    w = weights()
    ok = fail = 0
    t0 = time.time()
    try:
        for i, o in enumerate(todo, 1):
            oid = o["id"]
            one = time.time()
            try:
                req("/anima-wardrobe-v6/outfit-library/enable", {"id": oid})
                seed = 930000 + i * 149
                lvk = LEVEL_KEY.get(int(o.get("level") or 0), "sfw")
                # 预检：确认这套衣服与全身取景真的进了正文
                R.clear_cache()
                probe = R.draw(level=lvk, seed=seed, weights=w,
                               base_prob=0.65, force_role=ROLE,
                               selected_roles=ROLE, role_pick=True,
                               scene_pick=False, breast_size=BREAST)
                ptext = str(probe.get("text") or "")
                expect = [o["tags"][0], "full body"]
                miss = [e for e in expect if e.lower() not in ptext.lower()]
                if miss:
                    raise RuntimeError("预检未命中 %s | 正文 %.140s"
                                       % (miss, ptext))
                g = build_prompt(oid, seed, w, lvk)
                if args.steps:
                    for _k, v in g.items():
                        if "KSampler" in v["class_type"]:
                            v["inputs"]["steps"] = int(args.steps)
                pid = req("/prompt", {"prompt": g,
                                      "client_id": "outfit-thumbs"})["prompt_id"]
                res = wait(pid)
                imgs = [os.path.join(n.get("subfolder", ""), n.get("filename", ""))
                        for node in res.get("outputs", {}).values()
                        for n in node.get("images", [])]
                if not imgs:
                    raise RuntimeError("没有输出图片")
                src = os.path.join("S:/AI_workV5/ComfyUI/output",
                                   imgs[0].replace("/", os.sep))
                dst = os.path.join(THUMB_DIR, "%s.webp" % oid)
                size = to_thumb(src, dst)
                logf.write(json.dumps({
                    "id": oid, "zh": o["zh"], "cat": o["cat"],
                    "level": int(o.get("level") or 0), "nsfw_level": lvk,
                    "seed": seed, "prompt_id": pid, "src": imgs[0],
                    "thumb": "%s.webp" % oid, "bytes": size,
                }, ensure_ascii=False) + "\n")
                logf.flush()
                ok += 1
                print("[%02d/%02d] %-30s L%d %4.0fs -> %d B"
                      % (i, len(todo), oid, int(o.get("level") or 0),
                         time.time() - one, size))
            except Exception as exc:
                fail += 1
                print("[%02d/%02d] %-30s FAIL %s" % (i, len(todo), oid, exc))
    finally:
        req("/anima-wardrobe-v6/outfit-library/enable", {"id": ""})
        cur = req("/anima-wardrobe-v6/outfit-library")
        print("已复位 active=%r" % cur.get("active"))
        logf.close()
    print("\n成功 %d / 失败 %d，耗时 %.1f 分钟，日志 %s"
          % (ok, fail, (time.time() - t0) / 60, LOG))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
