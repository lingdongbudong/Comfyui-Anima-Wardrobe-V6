# -*- coding: utf-8 -*-
"""联机验收：用灵冻本人的工作流出图，验证某角色在指定场景下的提示词与画面。

为什么用桌面那份工作流而不是插件里的 `workflows/*_api.json`：
  桌面那份才是他实际在跑的 —— 模型 `miaomiaoHarem_anima12`（8 步 cfg1）、
  `konyaKarasue` + `kinokohime` 两个 LoRA、`breast_size` / `role_fav_only` /
  `dry_skin_filter` 都是他的真实配置。用别的组合验收，结论对不上他看到的画面。

用法：
    python tools/verify_live.py <seed> <scene_id> <prefix> [role] [breast_size]

例：
    python tools/verify_live.py 292417811 sc_car_ride sparkle_a1
    python tools/verify_live.py 292417811 sc_car_ride sparkle_a2 "" 不指定

前置：8188 在跑，且 `presets/*.json` 的改动已通过
      `POST /anima-wardrobe-v6/outfits/enable`（同值）触发过 clear_cache()，
      否则进程里还是旧快照。

⚠️ 只改三处：`scene_pick→False`、`weights_json` 加 `forced` 场景、输出前缀。
   其余全照抄，避免"验的不是他跑的那套"。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8188"
WF = r"C:\Users\VGBFC\Desktop\V6-Anima_miaomiaoHarem_anima12-8步-1122.json"
PREFIX = "图片/Anima验收/"


def _drain(pid, timeout=600):
    t0 = time.time()
    while True:
        with urllib.request.urlopen(BASE + f"/history/{pid}", timeout=30) as r:
            h = json.loads(r.read().decode("utf-8"))
        if h and pid in h:
            st = h[pid].get("status") or {}
            if st.get("completed") or st.get("status_str") in ("success", "error"):
                imgs = [(nid, im) for nid, out in (h[pid].get("outputs") or {}).items()
                        for im in (out.get("images") or [])]
                return st.get("status_str"), imgs, time.time() - t0
        if time.time() - t0 > timeout:
            return "timeout", [], time.time() - t0
        time.sleep(2)


def run(seed="292417811", scene="sc_car_ride", prefix="sparkle_v", role="", breast=None):
    wf = json.load(open(WF, encoding="utf-8"))
    i = wf["1150"]["inputs"]
    i["scene_pick"] = False
    i["seed"] = str(seed)
    if role:
        i["selected_roles"] = ""
        i["force_role"] = role
    if breast:
        i["breast_size"] = breast
    w = json.loads(i["weights_json"])
    w["forced"] = {"n_canon": scene}
    i["weights_json"] = json.dumps(w, ensure_ascii=False)
    wf["1039"]["inputs"]["filename_prefix"] = PREFIX + prefix

    req = urllib.request.Request(
        BASE + "/prompt",
        data=json.dumps({"prompt": wf, "client_id": "anima-verify"}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    if res.get("node_errors"):
        print("[node_errors]", json.dumps(res["node_errors"], ensure_ascii=False)[:600])
    pid = res["prompt_id"]
    print("[submit]", pid, "| scene:", scene, "| breast:", i.get("breast_size"))
    status, imgs, el = _drain(pid)
    print("[status]", status, "| %.1fs" % el)
    for _, im in imgs:
        print("[IMAGE]", (im.get("subfolder") or "") + "/" + im.get("filename", ""))
    return imgs


if __name__ == "__main__":
    a = sys.argv[1:]
    run(seed=a[0] if len(a) > 0 else "292417811",
        scene=a[1] if len(a) > 1 else "sc_car_ride",
        prefix=a[2] if len(a) > 2 else "sparkle_v",
        role=a[3] if len(a) > 3 else "",
        breast=(a[4] if len(a) > 4 and a[4] else None))
