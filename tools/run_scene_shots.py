# -*- coding: utf-8 -*-
"""按场景 id 批量出「预览图」原图（v6.19.0）。

## 它解决什么

`tools/make_scene_thumbs.py` 负责把**已有的图**裁成预览图；这个脚本负责
把**还没有图**的场景补拍出来。两者首尾相接：

    run_scene_shots.py  →  output/图片/Anima/场景预览/<id>_00001_.png + jsonl
    make_scene_thumbs.py --logs <jsonl>  →  presets/scene_thumbs/<id>.webp

## 为什么不用现成的两个脚本

  · `anima-wardrobe-batch/scripts/batch_scenes.py` —— 能钉场景 id，但**没有
    角色参数**，角色只能吃模板里的；
  · `.../scripts/batch_roles.py` —— 有 `--roles`，但只能按档位分布抽场景，
    **不能指定某一条**；
  · 我要的是「指定角色 × 精确场景 id × 按画幅分尺寸」，两个都不全。

## 关键机制（与面板「一键套用」同一条路径）

钉场景 = 往 `weights_json` 写：
    fields.n_canon        = 3        （字段必现）
    options.n_canon       = {目标: 3, 其余全部: 0}
  ⚠️ `options.n_canon` 必须把**全部 781 个 id + 14 条法典**都显式写 0，
     只写目标那一条是不够的 —— 没出现的条目会走默认权重，照样被抽中。

## 角色口径（用户 2026-11-13 定）

    sfw / pose / near  → 薇尔莉特·伊芙加登（violet_evergarden）
    weapon             → 按场景自带 `work` 字段，配**同作品人气最高的角色**
    art                → 不给角色（人物极小，巨构道具主导）

## 画幅

    portrait  → 720×1280（9:16）
    landscape → 1280×720（16:9）
    both      → 720×1280（竖，场景页默认竖排）
    art 组走 29B 模板，固定 1280×720

## 用法

    PY="S:/AI_workV5/python_embeded/python.exe"

    $PY tools/run_scene_shots.py --missing S:/技能/_scene_missing_20261113.json --dry
    $PY tools/run_scene_shots.py --missing ... --groups sfw --limit 3      # 样张
    $PY tools/run_scene_shots.py --missing ... --groups sfw,pose,near       # 一条线
    $PY tools/run_scene_shots.py --missing ... --groups art --out-root ...

    --only-missing  跳过已经出过图的（断点续跑）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
HOST = os.environ.get("COMFY_HOST", "http://127.0.0.1:8188")

TPL_12B = "C:/Users/VGBFC/Desktop/V6-Anima_miaomiaoHarem_anima12-10步.json"
TPL_29B = "C:/Users/VGBFC/Desktop/V6-Anima-艺术构图_哑光人物_29B_16x9_1108.json"

NODE_PROMPT = "1150"          # AnimaV6RandomPrompt
NODE_SIZE_12B = "1116"        # XB_ImageParamsMaster
NODE_SAVE_12B = "1039"
NODE_SEED_12B = "1006"

DEFAULT_ROLE = "violet_evergarden"
LEVEL_KEY = {0: "sfw", 1: "suggestive", 2: "explicit", 3: "hardcore", 4: "extreme"}
SIZES = {"portrait": (720, 1280), "landscape": (1280, 720), "both": (720, 1280)}

# 按组决定走哪条模板：art 有独立的 29B 16:9 工作流，其余走 12B。
GROUP_TPL = {"art": "29b"}


def _get(path, timeout=30):
    return json.loads(urllib.request.urlopen(HOST + path, timeout=timeout).read())


def _post(path, body, timeout=60):
    data = json.dumps(body).encode()
    req = urllib.request.Request(HOST + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def wait_done(prompt_id, timeout=600):
    """轮询 /history 直到这个 prompt 出结果。返回 (ok, detail)。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = _get(f"/history/{prompt_id}", timeout=20)
        except Exception:
            h = {}
        if prompt_id in h:
            entry = h[prompt_id]
            st = (entry.get("status") or {})
            if st.get("status_str") == "error" or st.get("completed") is False:
                return False, json.dumps(st, ensure_ascii=False)[:300]
            outs = entry.get("outputs") or {}
            names = []
            for _nid, o in outs.items():
                for im in (o.get("images") or []):
                    names.append((im.get("subfolder", ""), im.get("filename", "")))
            return True, names
        time.sleep(2)
    return False, "超时"


def build_weights(base: dict, scene_id: str, all_ids) -> str:
    """把 n_canon 钉到 scene_id。base 是模板里原有的 weights_json（三层）。"""
    w = base
    w.setdefault("fields", {})["n_canon"] = 3
    bag = {k: 0 for k in (w.get("options") or {}).get("n_canon", {})}
    for sid in all_ids:          # ⚠️ 全部显式清零，漏掉的会走默认权重
        bag[sid] = 0
    bag[scene_id] = 3
    w.setdefault("options", {})["n_canon"] = bag
    return json.dumps(w, ensure_ascii=False)


def role_for_work(work: str, by_work: dict) -> str:
    """weapon 组：按作品挑人气最高的角色。作品不在库里就退回薇尔莉特。"""
    if not work or work == "general":
        return DEFAULT_ROLE
    lst = by_work.get(work) or []
    if not lst:
        return DEFAULT_ROLE
    return max(lst, key=lambda c: c.get("pop", 0)).get("tag", DEFAULT_ROLE)


def main(argv=None):
    ap = argparse.ArgumentParser(description="按场景 id 出预览图原图")
    ap.add_argument("--missing", default="",
                    help="缺图清单 json（make_scene_thumbs --missing-out）")
    ap.add_argument("--all", action="store_true",
                    help="不读清单，直接跑库里全部场景（排除 nsfw）。"
                         "「全库重拍」用这个 —— 缺图清单只认盘上没有的，"
                         "而重拍是要把「有图但想换」的也重来。")
    ap.add_argument("--groups", default="sfw,pose,near,art,weapon",
                    help="只跑这些组，逗号分隔")
    ap.add_argument("--role", default=DEFAULT_ROLE, help="sfw/pose/near 用的角色 tag")
    ap.add_argument("--out-root", default="图片/Anima/场景预览",
                    help="相对 ComfyUI/output 的输出目录")
    ap.add_argument("--force-tpl", default="", choices=["", "12b", "29b"],
                    help="强制走某一套模板（默认按组：art→29b，其余 12b）。"
                         "特写这种要严格取景的组，12B 不服从 extreme close-up，可强制 29b")
    ap.add_argument("--tpl-12b", default=TPL_12B)
    ap.add_argument("--tpl-29b", default=TPL_29B)
    ap.add_argument("--scenes", default="",
                    help="只跑这些场景 id（逗号分隔）。返工单张用 —— 配合 --seed-base 换种子")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-missing", action="store_true", help="跳过已出过的")
    ap.add_argument("--steps", default="", help="覆盖采样步数（调试用，如 8）")
    ap.add_argument("--seed-base", type=int, default=20261113)
    ap.add_argument("--log", default="")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    groups = {g.strip() for g in args.groups.split(",") if g.strip()}

    # 场景数据统一从接口取（画幅/level 都以接口为准）
    scenes = _get("/anima-wardrobe-v6/scenes")["scenes"]
    all_ids = [s["id"] for s in scenes]
    aspect_of = {s["id"]: (s.get("aspect") or "both") for s in scenes}

    if args.all:
        # 全库模式：接口场景直接转清单格式。
        # ⚠️ **只跑 L0**（用户 2026-11-13：NSFW L1 及以上一概不出预览图）。
        #    判据用 level 不用 group —— 擦边内容散落在各组的 L1+ 里，
        #    按 group 卡会多跑 113 张没人会看到的图。
        plans = [{"id": s["id"], "zh": s.get("zh", ""), "group": s.get("group", ""),
                  "theme": s.get("theme", ""), "level": int(s.get("level", 0) or 0),
                  "aspect": aspect_of.get(s["id"], "both"),
                  "work": s.get("work", ""), "weapon": s.get("weapon", "")}
                 for s in scenes if int(s.get("level", 0) or 0) == 0]
    else:
        if not args.missing:
            print("要么给 --missing 清单，要么加 --all")
            return 2
        plans = json.load(open(args.missing, encoding="utf-8"))["scenes"]

    todo = [p for p in plans if p["group"] in groups]
    if args.only_missing:
        todo = [p for p in todo if not os.path.isfile(
            os.path.join("S:/AI_workV5/ComfyUI/output",
                         args.out_root.replace("/", os.sep),
                         f"{p['id']}_00001_.png"))]
    # 单条返工：按 id 白名单过滤（在 --limit 之前，免得 limit 把它切掉）
    if args.scenes:
        want = {x.strip() for x in args.scenes.split(",") if x.strip()}
        missing = want - {p["id"] for p in todo}
        if missing:
            print(f"⚠️ 这些 id 不在本次计划里：{sorted(missing)}")
        todo = [p for p in todo if p["id"] in want]
    if args.limit:
        todo = todo[: args.limit]

    # 全部场景 id（钉场景时要把它们全写 0）—— 上面已从接口取过，这里只补注释：
    # ⚠️ 画幅必须用 `aspect_of`（接口的合并值），不能用缺图清单里的 `aspect`：
    #    清单读的是 scenes.json 里那个键，而绝大多数场景的推荐画幅记在旁挂的
    #    anima_v4_scene_aspects.json（`R.scene_aspect()` 才合并）。实测清单里
    #    214 条是默认值 both，照它跑会把横构图场景全出成竖图。

    # 角色库按作品分组（weapon 用）
    chars = _get("/anima-wardrobe-v6/characters")
    by_work = {}
    for c in (chars.get("characters") or []):
        by_work.setdefault(c.get("work") or "", []).append(c)

    print(f"清单 {len(plans)} 条 → 选中 {len(todo)} 条（组：{'/'.join(sorted(groups))}）")
    if not todo:
        return 0

    tpl_cache = {}
    logf = open(args.log, "a", encoding="utf-8") if args.log else None
    ok = fail = 0
    t0 = time.time()

    for i, p in enumerate(todo, 1):
        sid = p["id"]
        g = p["group"]
        which = args.force_tpl or GROUP_TPL.get(g, "12b")
        if which not in tpl_cache:
            tpl_cache[which] = json.load(open(
                args.tpl_29b if which == "29b" else args.tpl_12b, encoding="utf-8"))
        g6 = json.loads(json.dumps(tpl_cache[which]))     # 深拷贝，别污染缓存

        level = LEVEL_KEY.get(int(p.get("level", 0) or 0), "sfw")
        # art 组模板里节点 id 不同：按 class_type 找，别写死
        # ⚠️ 不能只认 V6 类名：29B 的 art 模板里注册的仍是旧别名
        #    `AnimaV5RandomPrompt`（同一套节点、参数键完全一致）。
        #    只认 V6 会 StopIteration，而且是在跑到 art 第一条时才炸。
        npk = next(k for k, v in g6.items()
                   if v["class_type"] in ("AnimaV6RandomPrompt",
                                          "AnimaV5RandomPrompt",
                                          "AnimaV4RandomPrompt"))
        prompt_node = g6[npk]["inputs"]

        # 角色：**只有 art 组不给**（人物极小、巨构道具主导），
        # 其余组一律给 —— 包括走 29B 的特写返工。原来按 `which == "12b"`
        # 判断，导致 29B 跑 near 时 role 为空、随机抽了个银发墨镜女
        # （实测：钉"脚趾蜷起"出的不是薇尔莉特）。
        role = ""
        if not (which == "29b" and g == "art"):
            role = args.role if g != "weapon" else role_for_work(p.get("work", ""), by_work)
        seed = args.seed_base + i

        prompt_node["nsfw_level"] = level
        prompt_node["seed"] = seed
        prompt_node["selected_roles"] = role
        prompt_node["force_role"] = role
        prompt_node["role_works"] = ""
        prompt_node["role_fav_only"] = False
        # ⚠️ 铁律（2026-11-13 又踩一次）：工作流里 `pick_pool_mode` 非空
        #    + `pick_pool_ids` 有值时，`draw()` 会**先**落
        #    `forced["n_canon"]`，把 weights 里钉的场景整个顶掉。
        #    29B 的 art 模板正是 `pick_pool_mode="art"` + 20 个 sc_art_* id，
        #    不清空的话出的是池里随机一条（实测：钉"霓虹巨拱"，出的是星空流星），
        #    而且因为池子是 art、画面看着像 art，很容易误判成"钉生效了"。
        prompt_node["pick_pool_mode"] = ""
        prompt_node["pick_pool_ids"] = ""
        base_w = json.loads(prompt_node.get("weights_json") or "{}")
        prompt_node["weights_json"] = build_weights(base_w, sid, all_ids)

        # 尺寸：**按 class_type 找**，两套模板的节点 id 不同（12B 是 1116、
        # 29B 是 9），写死 id 换模板就静默失效（尺寸还是模板里那个）。
        asp = aspect_of.get(sid, "both")
        wd, ht = SIZES.get(asp, SIZES["both"])
        for _k, _v in g6.items():
            if _v["class_type"] == "XB_ImageParamsMaster":
                _v["inputs"]["width"] = wd
                _v["inputs"]["height"] = ht
                _v["inputs"]["aspect_ratio"] = "9:16" if ht > wd else "16:9"
        if args.steps:
            for k, v in g6.items():
                if "KSampler" in v["class_type"]:
                    v["inputs"]["steps"] = int(args.steps)

        # 种子 + 输出前缀
        for k, v in g6.items():
            if v["class_type"].startswith("Seed") or "KSampler" in v["class_type"]:
                if "seed" in v["inputs"]:
                    v["inputs"]["seed"] = seed + 500000
            if v["class_type"] == "SaveImage":
                v["inputs"]["filename_prefix"] = f"{args.out_root}/{sid}"

        if args.dry:
            print(f"  [{i}/{len(todo)}] {sid}  {g}  {level}  role={role or '-'}  "
                  f"tpl={which}  aspect={aspect_of.get(sid,'both')}")
            continue

        try:
            r = _post("/prompt", {"prompt": g6})
            pid = r.get("prompt_id")
            good, detail = wait_done(pid)
            if good:
                ok += 1
                rec = {"scene": sid, "zh": p.get("zh", ""), "group": g,
                       "level": p.get("level", 0), "role": role, "seed": seed,
                       "prompt_id": pid, "files": detail,
                       "subfolder": args.out_root, "file": f"{sid}_00001_.png"}
                if logf:
                    logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    logf.flush()
                print(f"  [{i}/{len(todo)}] OK  {sid}  ({role or 'art'})  "
                      f"{time.time()-t0:.0f}s")
            else:
                fail += 1
                print(f"  [{i}/{len(todo)}] FAIL {sid}  {detail}")
        except Exception as exc:
            fail += 1
            print(f"  [{i}/{len(todo)}] ERR  {sid}  {exc}")

    if logf:
        logf.close()
    if not args.dry:
        print(f"\n完成：成功 {ok} / 失败 {fail}，耗时 {(time.time()-t0)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
