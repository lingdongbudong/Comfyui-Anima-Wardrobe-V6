# -*- coding: utf-8 -*-
"""角色面板图批量生成（沿用用户指定的 V5 工作流）。

工作流：`V5-Anima_miaomiaoHarem_anima12-8步.json`（用户 2026-11-04 指定）
  · UNET `Anima/miaomiaoHarem_anima12`（= 他口中的「12B」）
  · LoRA konyaKarasue 0.30 + kinokohime 0.60 + turbo 1.00 + kaminarimon 1.00
  · 10 步 / cfg 1 / er_sde / beta / 608×608 1:1
  · 正向 `masterpiece, best quality, score_9, score_8, highres, absurdres,
    anime screenshot, official art` + `@4x0style`
  · 负向排除 `simple background`（要背景，不要纯白）

只改三件事（其余一律不动）：
  ① `1145.selected_roles` → 当前角色 tag
  ② `scene_pick` → False（面板图不该带场景）
  ③ `shot_size` 权重 3 → `upper body`（用户要半身胸像；原样跑会抽到脸部特写）
     `count` 权重 3 → `1girl, solo`
  另加 `extra_body = front view, looking at viewer`（引擎没有「正面」这个 viewpot 选项）

跑法：
    python gen_roles_wf.py bench        # 3 张验证构图
    python gen_roles_wf.py run 50       # 前 50 个（跳过已有，可反复跑）
    python gen_roles_wf.py run          # 全量（断点续跑）
    python gen_roles_wf.py thumb        # 只补缩略图
"""
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8188"
_HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(_HERE)
# 工作流模板：用环境变量 ANIMA_WF_JSON 指定你自己那份（改了参数直接生效），
# 不指定就用仓库里归档的副本。
WF_PATH = os.environ.get("ANIMA_WF_JSON") or os.path.normpath(os.path.join(
    PLUGIN, "workflows", "Anima_V6_角色面板_miaomiaoHarem_anima12_8步.json"))
PRESETS = os.path.join(PLUGIN, "presets")
THUMBS = os.path.join(PRESETS, "thumbs")
# 出图落到 ComfyUI 的 output 目录下（插件目录 → custom_nodes → ComfyUI 根）
OUT_ROOT = os.environ.get("ANIMA_OUT_ROOT") or os.path.normpath(os.path.join(
    os.path.dirname(PLUGIN), "..", "output", "图片", "角色1比1图片"))
LOGP = os.path.join(OUT_ROOT, "_run.log")

# 固定种子：同角色重跑逐字节一致，便于回溯
SEED_ROLE = 20261104      # 1145（外貌/服装抽取）
SEED_SAMPLE = 20261104    # 1104（采样）

# 底模覆盖（空串 = 沿用工作流里的 miaomiaoHarem_anima12）
# 用环境变量切：ROLE_UNET=Anima\\anima29B_v10_bf16.safetensors
UNET_OVERRIDE = os.environ.get("ROLE_UNET", "").strip()

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def safe(name, fallback="unknown"):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "")).strip(" .")
    return s or fallback


def assign_out_names(roles):
    """给每个角色定唯一文件名。

    ⚠️ 中文名会撞车：库里有 141 组（如「初音未来」对应 `hatsune_miku` 与
       `hatsune_miku_(append)`、「阿尔托莉雅·潘德拉贡」对应 3 个 saber），
       共 164 个角色。原来只用中文名当文件名，后跑的直接把前面的覆盖掉 ——
       实测 2402 个角色只落盘 2238 张。
       撞车的追加 tag slug 做后缀，其余保持纯中文名（可读优先）。
    """
    import collections
    base = {id(r): safe(r["name"], safe(r["tag"])) for r in roles}
    cnt = collections.Counter(base.values())
    for r in roles:
        b = base[id(r)]
        if cnt[b] > 1:
            tail = re.sub(r"[^a-z0-9_]+", "_", str(r["tag"]).lower()).strip("_")[:80]
            r["out_name"] = f"{b}_{tail}"
        else:
            r["out_name"] = b
    return roles


def load_roles():
    with open(os.path.join(PRESETS, "anima_v4_characters.json"), "r",
              encoding="utf-8") as fh:
        base = json.load(fh)
    work_zh = {(w.get("en") or "").lower(): (w.get("zh") or w.get("en") or "")
               for w in (base.get("works") or [])}
    out = []
    for c in base.get("characters") or []:
        work = (c.get("work") or "").strip()
        out.append({
            "tag": c.get("tag") or "",
            "name": c.get("zh") or c.get("name") or c.get("tag") or "",
            "work": work,
            "work_zh": work_zh.get(work.lower()) or work or "unknown",
        })
    up = os.path.join(PRESETS, "user_characters.json")
    if os.path.exists(up):
        with open(up, "r", encoding="utf-8") as fh:
            u = json.load(fh)
        seen = {r["tag"] for r in out}
        for c in (u.get("custom") or []):
            if not c.get("tag") or c["tag"] in seen:
                continue
            work = (c.get("work") or "").strip()
            out.append({
                "tag": c["tag"],
                "name": c.get("zh") or c.get("name") or c["tag"],
                "work": work,
                "work_zh": work_zh.get(work.lower()) or work or "unknown",
            })
    return out


# 面板图要关掉的字段（用户工作流里这些仍是默认权重 1.0，会随机乱入）
# 实测没关的时候出过：`maid headdress`（发饰）、`from side, from behind`（侧后视角）、
# `balcony, candle, starry sky`（场景）、`blurry`（景深）—— 角色卡全被污染。
OFF_FIELDS = [
    "viewpoint", "dof", "device",              # 相机：视角/景深/手持
    "place", "prop", "weather", "comp", "texture_fx",   # 场景
    "light_fx",                                 # 光照
    "pose_base", "arms_hands", "action", "pose_risque",  # 动作
    "hair_acc",                                 # 发饰（会盖掉角色特征）
    "emotion", "mouth",                         # 表情保持中性
    "n_canon", "n_extra", "nl",                 # 场景搭配 / 自然语言块
    "skin_detail", "leg", "nails",              # 细节项
]


def make_wf(tpl, role_tag, prefix):
    """在工作流模板上打补丁。浅拷贝够用：只改 1145 / 1104 / 1039 / 1006。"""
    import copy
    wf = copy.deepcopy(tpl)
    # 底模可切换：默认沿用工作流的 miaomiaoHarem_anima12；
    # 实测同参数下 anima29B_v10_bf16 的角色特征还原明显更强（狼耳/尾巴/发型）
    # 且速度一样（608×608 + 10 步 ≈ 2.5s），所以批量跑用 29B。
    if UNET_OVERRIDE:
        wf["1033"]["inputs"]["unet_name"] = UNET_OVERRIDE
    ins = wf["1145"]["inputs"]
    ins["selected_roles"] = role_tag
    ins["role_pick"] = True
    ins["scene_pick"] = False
    ins["extra_body"] = "front view, looking at viewer"
    ins["seed"] = SEED_ROLE
    try:
        w = json.loads(ins.get("weights_json") or "{}")
    except Exception:
        w = {}
    w.setdefault("fields", {})
    w.setdefault("options", {})
    for k in OFF_FIELDS:
        w["fields"][k] = 0
    w["options"]["shot_size"] = {"upper body": 3}
    w["options"]["count"] = {"1girl, solo": 3}
    ins["weights_json"] = json.dumps(w, ensure_ascii=False)
    wf["1104"]["inputs"]["seed"] = SEED_SAMPLE
    wf["1006"]["inputs"]["seed"] = SEED_SAMPLE
    wf["1039"]["inputs"]["filename_prefix"] = prefix
    return wf


def submit(wf):
    data = json.dumps({"prompt": wf}).encode()
    req = urllib.request.Request(API + "/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    with op.open(req, timeout=40) as rr:
        return json.loads(rr.read())


def wait(pid, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with op.open(API + "/history/" + pid, timeout=15) as rr:
                h = json.loads(rr.read())
        except Exception:
            time.sleep(2)
            continue
        if pid in h:
            return h[pid]
        time.sleep(1.2)
    return None


def thumb_slug(tag):
    """缩略图文件名 = 路由认的 slug。

    ⚠️ `__init__.py` 的 `/thumb/{name}` 只接受 `[a-z0-9_]{1,120}`，
       直接拿 tag 当文件名（`texas_(arknights).webp`）会被判 400、面板读不到。
       正确形态与旧 thumbs 里有效的那批一致：
       `2b_(nier_automata)` → `2b_nier_automata.webp`。
    """
    s = re.sub(r"[^a-z0-9_]+", "_", str(tag or "").lower())
    return re.sub(r"_+", "_", s).strip("_")[:120]


def make_thumb(png, tag):
    try:
        from PIL import Image
    except Exception as e:
        return f"no-PIL: {e}"
    s = thumb_slug(tag)
    if not s:
        return "thumb-err: empty slug"
    try:
        im = Image.open(png).convert("RGB")
        w, h = im.size
        k = 160.0 / max(w, h)
        if k < 1:
            im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        os.makedirs(THUMBS, exist_ok=True)
        im.save(os.path.join(THUMBS, s + ".webp"), "WEBP", quality=88)
        return ""
    except Exception as e:
        return f"thumb-err: {e}"


def log(msg):
    os.makedirs(OUT_ROOT, exist_ok=True)
    with open(LOGP, "a", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg + "\n")


def do_one(tpl, r, force=False):
    work_dir = safe(r["work_zh"], safe(r["work"]))
    name = r.get("out_name") or safe(r["name"], safe(r["tag"]))
    d = os.path.join(OUT_ROOT, work_dir)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, name + ".png")
    if os.path.exists(dst) and not force:
        return "skip", 0.0

    prefix = f"图片/角色1比1图片/{work_dir}/{name}"
    t0 = time.time()
    try:
        res = submit(make_wf(tpl, r["tag"], prefix))
    except urllib.error.HTTPError as e:
        return f"fail:http{e.code} {e.read().decode('utf-8', 'ignore')[:120]}", 0.0
    except Exception as e:
        return f"fail:submit {e}", 0.0
    if "prompt_id" not in res:
        return f"fail:{str(res)[:100]}", 0.0
    h = wait(res["prompt_id"])
    dt = time.time() - t0
    if not h:
        return "fail:timeout", dt
    st = (h.get("status") or {}).get("status_str")
    imgs = []
    for v in (h.get("outputs") or {}).values():
        for im in (v.get("images") or []):
            imgs.append(im.get("filename"))
    if st != "success" or not imgs:
        return f"fail:{st}/no-img", dt
    src = os.path.join(d, imgs[0])
    try:
        if os.path.basename(src) != name + ".png":
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                os.replace(src, dst)
    except Exception as e:
        return f"fail:rename {e}", dt
    err = make_thumb(dst, r["tag"])
    return ("ok" if not err else "ok:" + err), dt


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    with open(WF_PATH, "r", encoding="utf-8") as fh:
        tpl = json.load(fh)
    roles = load_roles()
    assign_out_names(roles)

    if mode == "bench":
        picks = [r for r in roles
                 if r["tag"] in ("texas_(arknights)", "ganyu_(genshin_impact)",
                                 "hatsune_miku", "byleth_(female)_(fire_emblem)")]
        for r in picks:
            st, dt = do_one(tpl, r, force=True)
            print(f"  {r['name']:14s} {st[:12]:12s} {dt:5.1f}s  "
                  f"-> {safe(r['work_zh'])}/{r['out_name']}.png")
        return 0

    if mode == "thumb":
        n = miss = 0
        for r in roles:
            p = os.path.join(OUT_ROOT, safe(r["work_zh"], safe(r["work"])),
                             r["out_name"] + ".png")
            if not os.path.exists(p):
                miss += 1
                continue
            if not make_thumb(p, r["tag"]):
                n += 1
        print(f"缩略图已生成 {n} 张（缺原图 {miss} 个）")
        return 0

    os.makedirs(OUT_ROOT, exist_ok=True)
    if not os.path.exists(THUMBS + ".bak-before-panel"):
        shutil.copytree(THUMBS, THUMBS + ".bak-before-panel")
        print(f"已备份旧缩略图 → {THUMBS}.bak-before-panel")
    log(f"=== 开始 run limit={limit or 'all'} 角色={len(roles)} 工作流=miaomiaoHarem_anima12 ===")
    todo = roles[:limit] if limit else roles
    ok = skip = fail = 0
    fails = []
    t0 = time.time()
    for i, r in enumerate(todo, 1):
        st, dt = do_one(tpl, r)
        if st.startswith("ok"):
            ok += 1
        elif st == "skip":
            skip += 1
        else:
            fail += 1
            fails.append((r["tag"], st[:150]))
        if i % 25 == 0 or i == len(todo):
            el = time.time() - t0
            rate = i / max(el, 1e-6)
            eta = (len(todo) - i) / max(rate, 1e-9) / 3600
            msg = (f"{i}/{len(todo)}  ok={ok} skip={skip} fail={fail}  "
                   f"{el/60:.1f}min  {rate*60:.1f}张/min  剩余 {eta:.1f}h")
            print(msg, flush=True)
            log(msg)
    msg = f"=== 结束 ok={ok} skip={skip} fail={fail} 用时 {(time.time()-t0)/3600:.2f}h ==="
    print(msg)
    log(msg)
    for t, s in fails[:40]:
        log(f"  FAIL {t}  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
