# -*- coding: utf-8 -*-
"""v6.20.0 真机验收批量出图（2026-11-14）。

一轮跑完三批，共 15 张：
  ① 服装库新增 9 套（逐套 enable → 按 level 设 nsfw_level → 关场景 → 出图）
  ② 词库新增 5 条三段渐变过膝袜（钉 socks 字段与选项权重）
  ③ 新场景 sc_sink_legs_up（钉 n_canon，出图后另跑 make_scene_thumbs 入库）

⚠️ 为什么不用旧的 verify_nsfw_outfits.py：
   · 它指向的 V5「12 步竖屏」工作流已不在盘上；
   · 它只挑 id 以 `ns_` 开头的套装，套不到本批 9 套。

⚠️ 四条铁律：
  1. `weights_json` 必须是完整三层 —— 用 R.empty_weights() 起手，别手写残片；
  2. 钉场景 / 钉选项时，其余候选**全部显式写 0**，否则走默认权重照样被抽中；
  3. `pick_pool_mode` / `pick_pool_ids` 必须清空，否则 draw() 先落
     forced["n_canon"] 把 weights 钉的东西顶掉；
  4. 服装库启用态是服务端全局态、不进节点输入 —— 每套换 seed + 换
     SaveImage 前缀，保证 prompt graph hash 不同，ComfyUI 不会复用旧图。
     finally 必须复位 active=""，否则污染后续所有出图。

用法：
    PY="S:/AI_workV5/python_embeded/python.exe"
    $PY tools/verify_v6201_batch.py --dry          # 只打印计划
    $PY tools/verify_v6201_batch.py                # 全跑
    $PY tools/verify_v6201_batch.py --only ol_l1_tube_velvet
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

BASE = "http://127.0.0.1:8188"
WF = r"C:/Users/VGBFC/Desktop/V6-Anima_miaomiaoHarem_anima12-10步.json"
LIB = os.path.join(PLUGIN, "presets", "outfit_library.json")
OUT_ROOT = "图片/服装验收_v6201b"
OUT_ABS = os.path.join("S:/AI_workV5/ComfyUI/output", OUT_ROOT.replace("/", os.sep))
LOG = os.path.join(OUT_ABS, "verify_v6201.jsonl")

# ⚠️ 不能用 violet_evergarden：她在 character_outfits.json 里
#    enabled="default"（官方服装预设），按项目优先级
#    「角色预设 > 服装库 > 随机」会闭麦 13 个服装字段，
#    服装库与 socks 权重全被顶掉 —— 首轮 15 张实测全是蓝外套白裙。
#    hatsune_miku 的 enabled 为空，不抢服装。
ROLE = "hatsune_miku"
LEVEL_KEY = {0: "sfw", 1: "suggestive", 2: "explicit",
             3: "hardcore", 4: "extreme"}
SCENE_ID = "sc_sink_legs_up"

NEW_OUTFITS = [
    "ol_l1_tube_velvet", "ol_l1_tube_pleated", "ol_l1_tube_bustier",
    "ol_l1_center_lace", "ol_l1_center_placket", "ol_l1_center_velvet",
    "tr_qipao_jade", "tr_qipao_moonwhite", "tr_qipao_wine_stage",
]
NEW_SOCKS = [
    "aqua gradient thighhighs", "black gradient thighhighs",
    "white gradient thighhighs", "purple gradient thighhighs",
    "pink gradient thighhighs",
]

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


def scene_weights():
    """钉住 sc_sink_legs_up：782 个场景 id + 14 条法典全部显式写 0。"""
    w = R.empty_weights()
    ids = [x for _z, x, _n in R.scene_options(99, True)]
    ids += list((R.pools().get("canon") or {}).keys())
    w["fields"]["n_canon"] = 3.0
    w["options"]["n_canon"] = {k: 0.0 for k in ids}
    w["options"]["n_canon"][SCENE_ID] = 3.0
    return w


def socks_weights(target):
    """钉住某一条渐变过膝袜，其余 85 个 socks 选项显式写 0。"""
    w = R.empty_weights()
    _sec, fld = R.field_by_key("socks")
    all_en = [en for _zh, en in fld.get("opts", [])]
    w["fields"]["socks"] = 3.0
    w["fields"]["n_canon"] = 0.0
    w["options"]["socks"] = {en: 0.0 for en in all_en}
    w["options"]["socks"][target] = 3.0
    return w


def outfit_weights():
    """服装验收：关掉 n_canon，别让场景的推荐服装抢戏。"""
    w = R.empty_weights()
    w["fields"]["n_canon"] = 0.0
    return w


def build_prompt(tag, level, seed, weights, landscape=False):
    d = json.load(open(WF, encoding="utf-8"))
    npk = next(k for k, v in d.items()
               if v["class_type"] in ("AnimaV6RandomPrompt",
                                      "AnimaV5RandomPrompt",
                                      "AnimaV4RandomPrompt"))
    p = d[npk]["inputs"]
    p["nsfw_level"] = LEVEL_KEY.get(level, "sfw")
    p["seed"] = seed
    p["selected_roles"] = ROLE
    p["force_role"] = ROLE
    p["role_works"] = ""
    p["role_fav_only"] = False
    p["role_pick"] = True
    p["scene_pick"] = False
    p["pick_pool_mode"] = ""
    p["pick_pool_ids"] = ""
    p["weights_json"] = json.dumps(weights, ensure_ascii=False)
    for k, v in d.items():
        ct = v["class_type"]
        if ct.startswith("Seed") or "KSampler" in ct:
            if "seed" in v["inputs"]:
                v["inputs"]["seed"] = seed + 500000
        if ct == "XB_ImageParamsMaster":
            v["inputs"]["width"] = 1280 if landscape else 720
            v["inputs"]["height"] = 720 if landscape else 1280
            v["inputs"]["aspect_ratio"] = "16:9" if landscape else "9:16"
        if ct == "SaveImage":
            v["inputs"]["filename_prefix"] = "%s/%s" % (OUT_ROOT, tag)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--steps", default="", help="覆盖采样步数（调试用）")
    args = ap.parse_args()

    lib = json.load(open(LIB, encoding="utf-8"))
    by_id = {o["id"]: o for o in lib["outfits"]}

    jobs = []
    for oid in NEW_OUTFITS:
        o = by_id[oid]
        jobs.append({"tag": oid, "kind": "outfit", "level": int(o["level"]),
                     "weights": outfit_weights(),
                     "expect": list(o["tags"])[:3], "zh": o["zh"]})
    for en in NEW_SOCKS:
        tag = "sock_" + en.split()[0]
        jobs.append({"tag": tag, "kind": "sock", "level": 0,
                     "weights": socks_weights(en),
                     "expect": [en], "zh": en})
    jobs.append({"tag": SCENE_ID, "kind": "scene", "level": 0,
                 "weights": scene_weights(), "landscape": True,
                 "expect": ["on bed", "legs extended forward"],
                 "zh": "床沿 · 伸腿微屈"})

    if args.only:
        jobs = [j for j in jobs if j["tag"] == args.only]
    if not jobs:
        print("没有匹配的验收项")
        return 2

    os.makedirs(OUT_ABS, exist_ok=True)
    print("待验收 %d 张（模板 %s）" % (len(jobs), os.path.basename(WF)))
    if args.dry:
        for i, j in enumerate(jobs, 1):
            print("  %02d  %-9s L%d  %-28s %s"
                  % (i, j["kind"], j["level"], j["tag"], j["zh"]))
        return 0

    logf = open(LOG, "a", encoding="utf-8")
    ok = fail = 0
    try:
        for i, j in enumerate(jobs, 1):
            t0 = time.time()
            tag, kind, level = j["tag"], j["kind"], j["level"]
            try:
                if kind == "outfit":
                    req("/anima-wardrobe-v6/outfit-library/enable", {"id": tag})
                else:
                    req("/anima-wardrobe-v6/outfit-library/enable", {"id": ""})
                seed = 880000 + i * 137
                # 出图前先本地复现一次 draw()，确认钉的条目真的进正文。
                # 角色预设抢服装、档位闸门挡整套这类问题会在这一步暴露，
                # 不会像首轮那样白跑 15 张才发现。
                R.clear_cache()
                probe = R.draw(level=LEVEL_KEY.get(level, "sfw"), seed=seed,
                               weights=j["weights"], base_prob=0.65,
                               force_role=ROLE, selected_roles=ROLE,
                               role_pick=True, scene_pick=False)
                ptext = str(probe.get("text") or "")
                miss = [e for e in j["expect"]
                        if e.lower() not in ptext.lower()]
                if miss:
                    raise RuntimeError("预检未命中 %s | 正文 %.160s"
                                       % (miss, ptext))
                g = build_prompt(tag, level, seed, j["weights"],
                                 j.get("landscape", False))
                if args.steps:
                    for _k, v in g.items():
                        if "KSampler" in v["class_type"]:
                            v["inputs"]["steps"] = int(args.steps)
                pid = req("/prompt", {"prompt": g,
                                      "client_id": "verify-v6201"})["prompt_id"]
                res = wait(pid)
                imgs = [os.path.join(n.get("subfolder", ""), n.get("filename", ""))
                        for node in res.get("outputs", {}).values()
                        for n in node.get("images", [])]
                rec = {"tag": tag, "kind": kind, "level": level, "seed": seed,
                       "prompt_id": pid, "files": imgs, "expect": j["expect"],
                       "subfolder": OUT_ROOT,
                       "file": os.path.basename(imgs[0]) if imgs
                       else "%s_00001_.png" % tag}
                # 场景那条同时写成 make_scene_thumbs 认得的格式
                if kind == "scene":
                    rec["scene"] = SCENE_ID
                logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                logf.flush()
                ok += 1
                print("[%02d/%02d] %-9s L%d %-28s %4.0fs -> %s"
                      % (i, len(jobs), kind, level, tag,
                         time.time() - t0, imgs))
            except Exception as exc:
                fail += 1
                print("[%02d/%02d] %-9s L%d %-28s FAIL %s"
                      % (i, len(jobs), kind, level, tag, exc))
    finally:
        req("/anima-wardrobe-v6/outfit-library/enable", {"id": ""})
        cur = req("/anima-wardrobe-v6/outfit-library")
        print("已复位 active=%r" % cur.get("active"))
        logf.close()
    print("\n成功 %d / 失败 %d，日志 %s" % (ok, fail, LOG))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
