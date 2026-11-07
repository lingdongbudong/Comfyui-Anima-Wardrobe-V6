# -*- coding: utf-8 -*-
"""NSFW 服装库逐套出图验收（2026-11-07）。

用户工作流：桌面 `V5-Anima_miaomiaoHarem_anima12-12步竖屏.json`
（1150 提示词节点 / 1104 KSampler 12 步 cfg1 / 1116 800×1424 9:16 / 1039 SaveImage）

做法：
  ① 逐套 `POST /outfit-library/enable {id}` 启用（服装库是**服务端全局态**）
  ② 按该套自己的 level 设 nsfw_level（L0→sfw … L4→extreme）——
     闸门要求 `lv >= level` 否则整套不生效，必须一档一档对齐跑
  ③ 关掉 n_canon（不套场景），免得场景的推荐服装抢戏
  ④ 出图 → 收集 → **finally 复位 active=""**（否则污染后续所有出图）

⚠️ 服装库启用态不进节点输入，ComfyUI 会按输入 hash 复用旧图 —— 所以每套都换 seed，
   并让 weights_json / extra_body 轻微变化（内容不变但 hash 变）。

用法：python tools/verify_nsfw_outfits.py [--only ns_pasties] [--no-reset]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8188"
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
# 工作流：用环境变量 ANIMA_WF_JSON 指定；不指定就用仓库里归档的 8 步版
WF = os.environ.get("ANIMA_WF_JSON") or os.path.join(
    PLUGIN, "workflows", "Anima_V6_角色面板_miaomiaoHarem_anima12_8步.json")
LIB = os.path.join(PLUGIN, "presets", "outfit_library.json")

ROLE = "souryuu_asuka_langley"      # 固定一个角色，只换衣服
LEVEL_KEY = {0: "sfw", 1: "suggestive", 2: "explicit", 3: "hardcore", 4: "extreme"}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def req(path, obj=None, timeout=60):
    if obj is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                   headers={"Content-Type": "application/json"})
    with _opener.open(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def find(d, cls):
    return [k for k, v in d.items() if v.get("class_type") == cls]


def build(tag, level, seed):
    d = json.load(open(WF, encoding="utf-8"))
    # 提示词节点
    pid = None
    for k, v in d.items():
        if str(v.get("class_type", "")).startswith("AnimaV") and "RandomPrompt" in v["class_type"]:
            pid = k
    ins = d[pid]["inputs"]
    ins["selected_roles"] = ROLE
    ins["seed"] = seed
    ins["nsfw_level"] = LEVEL_KEY.get(level, "sfw")
    ins["scene_pick"] = False
    ins["role_pick"] = True
    ins["weights_json"] = json.dumps(
        {"sections": {}, "fields": {"n_canon": 0}, "options": {}}, ensure_ascii=False)
    # 采样器种子
    for k in find(d, "XB_ROCmKSampler"):
        if "seed" in d[k]["inputs"]:
            d[k]["inputs"]["seed"] = seed + 7
    for k in find(d, "Seed (rgthree)"):
        if "seed" in d[k]["inputs"]:
            d[k]["inputs"]["seed"] = seed + 11
    for k in find(d, "SaveImage"):
        d[k]["inputs"]["filename_prefix"] = f"图片/NSFW服装验收/{tag}_"
    return d


def wait(pid, timeout=420):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = req(f"/history/{pid}")
        if pid in h and h[pid].get("status", {}).get("completed"):
            return h[pid]
        time.sleep(2)
    raise TimeoutError(pid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    lib = json.load(open(LIB, encoding="utf-8"))
    todo = [o for o in lib["outfits"]
            if str(o.get("id", "")).startswith("ns_")
            and (not args.only or o["id"] == args.only)]
    print(f"待验收 {len(todo)} 套（工作流 {os.path.basename(WF)}）")

    ok = fail = 0
    results = {}
    try:
        for i, o in enumerate(todo, 1):
            t0 = time.time()
            try:
                req("/anima-wardrobe-v6/outfit-library/enable", {"id": o["id"]})
                seed = 770000 + i * 137
                pid = req("/prompt", {"prompt": build(o["id"], o["level"], seed),
                                      "client_id": "nsfw-verify"})["prompt_id"]
                res = wait(pid)
                imgs = [os.path.join(n.get("subfolder", ""), n.get("filename", ""))
                        for node in res.get("outputs", {}).values()
                        for n in node.get("images", [])]
                results[o["id"]] = imgs
                ok += 1
                print(f"[{i:02d}/{len(todo)}] L{o['level']} {o['zh']:16s} {time.time()-t0:.0f}s -> {imgs}")
            except Exception as e:
                fail += 1
                print(f"[{i:02d}/{len(todo)}] L{o['level']} {o['zh']:16s} FAIL {e}")
    finally:
        if not args.no_reset:
            req("/anima-wardrobe-v6/outfit-library/enable", {"id": ""})
            cur = req("/anima-wardrobe-v6/outfit-library")
            print("已复位 active=", repr(cur.get("active")))
    print(f"\n成功 {ok} / 失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
