# -*- coding: utf-8 -*-
"""角色 tag 试跑：验证 Anima 是否真的认识某个角色 tag。

为什么要这一步（2026-11-07）：
  用户要加《明日方舟：终末地》16 人与鬼灭 7 人。终末地是 2026 年新作，
  safebooru 被 Cloudflare 拦住、danbooru 在 503 维护，只有 yande.re（与 danbooru
  同源 tag 库，type=4 = character）能确认 12 个 tag 存在。但「tag 存在」不等于
  「模型认识」—— 模型认不出时出图是一张路人脸，而且不报错。唯一判据是出图看。

⚠️ 第一版踩的坑（29B 那 4 张作废）：
  把一个 weights_json = 全空 交给插件 → 正文变成「角色 tag + 一长串随机字段」
  （实测抽到 `close-up, selfie, symmetry, race queen, ruins`），画面被构图字段毁掉，
  根本没法判断角色像不像。
  所以本版改成**最小变量**：直接写死 CLIPTextEncode 的正文，完全绕过插件随机，
  正文只有「1girl, solo, 角色tag, 全身站立, 纯色背景」。像 → 模型认识；
  不像 / 画成路人 → 模型不认识（12B 尤其容易硬编通用脸）。

默认工作流用用户指定的 12B（桌面 V5-Anima_miaomiaoHarem_anima12-16步-1080.json）。

用法：
    python tools/trial_character_roles.py                      # 默认 4 个代表
    python tools/trial_character_roles.py --roles a b c
    python tools/trial_character_roles.py --workflow 29b       # 换回 29B
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8188"
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)

# 12B 工作流：用环境变量 ANIMA_WF_12B 指定；不指定就用仓库里归档的 8 步版
WF_12B = os.environ.get("ANIMA_WF_12B") or os.path.join(
    PLUGIN, "workflows", "Anima_V6_角色面板_miaomiaoHarem_anima12_8步.json")
WF_29B = os.path.join(PLUGIN, "workflows", "Anima-艺术构图_哑光人物_29B_16x9_api.json")

# ⚠️ 本机有 http_proxy 环境变量（工具链代理）；访问 127.0.0.1 必须绕开。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DEFAULT_ROLES = [
    ("perlica_(arknights)", "佩丽卡"),
    ("chen_qianyu", "陈千语"),
    ("kochou_kanae", "胡蝶香奈惠"),
    ("daki_(kimetsu_no_yaiba)", "堕姬"),
]

# 最小正文模板：只留角色 + 构图，避免任何随机字段污染判断。
TPL = ("1girl, solo, {tag}, full body, standing, arms at sides, "
       "looking at viewer, white background, anime coloring, clean lineart")


def http_json(path, obj=None, timeout=60):
    if obj is None:
        req = urllib.request.Request(BASE + path)
    else:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read())


def nodes_of(d, cls):
    return [k for k, v in d.items() if v.get("class_type") == cls]


def build_graph(wf_path, tag, seed):
    d = json.load(open(wf_path, encoding="utf-8"))

    # ① 找正向提示词节点：优先 CR Text 的下游 CLIPTextEncode（1004→1035），
    #    29B 那套则是 7 Prompt(LoraManager)→8。这里统一处理：
    #    把**正向** CLIPTextEncode 的 text 直接写成模板（断开上游连线）。
    pos = None
    for k in nodes_of(d, "CLIPTextEncode"):
        ins = d[k]["inputs"]
        t = ins.get("text")
        # 连到负词的是列表且上游文本里含 worst quality —— 用 8 号负词做锚点排除
        if isinstance(t, list):
            src = str(t[0])
            up = d.get(src, {})
            if up.get("class_type") == "Prompt (LoraManager)":
                continue          # 这是 CR Text 的封装，正向
            if up.get("class_type") == "CR Text":
                continue
        pos = k
        break
    # 更稳的锚点：29B 工作流 8 号是负词（正文含 worst quality）
    for k in nodes_of(d, "CLIPTextEncode"):
        txt = d[k]["inputs"].get("text")
        if isinstance(txt, str) and "worst quality" in txt:
            continue
        pos = k
    if pos is None:
        raise RuntimeError("找不到正向 CLIPTextEncode")

    d[pos]["inputs"]["text"] = TPL.format(tag=tag)

    # ② 种子：所有带 seed 输入的采样器/种子节点都换
    for k, v in d.items():
        ins = v.get("inputs", {})
        if "seed" in ins and isinstance(ins["seed"], int):
            ins["seed"] = seed + (int(k) % 97 if str(k).isdigit() else 0)

    # ③ 输出前缀
    for k in nodes_of(d, "SaveImage"):
        safe = tag.replace("(", "_").replace(")", "")
        d[k]["inputs"]["filename_prefix"] = f"图片/角色试跑/{safe}_"
    return d


def submit(graph):
    r = http_json("/prompt", {"prompt": graph, "client_id": "trial-roles"})
    if "prompt_id" not in r:
        raise RuntimeError(f"提交失败: {r}")
    return r["prompt_id"]


def wait(pid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = http_json(f"/history/{pid}")
        if pid in h and h[pid].get("status", {}).get("completed"):
            return h[pid]
        st = h.get(pid, {}).get("status", {})
        if st.get("status_str") == "error":
            raise RuntimeError(f"执行出错: {str(st)[:300]}")
        time.sleep(2)
    raise TimeoutError(f"{pid} 超时")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roles", nargs="*")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workflow", choices=["12b", "29b"], default="12b")
    args = ap.parse_args()

    wf = WF_12B if args.workflow == "12b" else WF_29B
    if not os.path.exists(wf):
        print(f"工作流不存在: {wf}")
        return 2
    roles = [(r, r) for r in args.roles] if args.roles else DEFAULT_ROLES

    try:
        h = http_json("/anima-wardrobe-v6/health")
        print(f"服务在线 · v{h.get('version')} · 工作流 {os.path.basename(wf)}")
    except Exception as e:
        print(f"服务不可用: {e}")
        return 2

    out = {}
    for tag, zh in roles:
        sd = args.seed or (910000 + int(hashlib.md5(tag.encode()).hexdigest()[:6], 16) % 80000)
        t0 = time.time()
        try:
            pid = submit(build_graph(wf, tag, sd))
            res = wait(pid)
            imgs = [os.path.join(im.get("subfolder", ""), im.get("filename", ""))
                    for node in res.get("outputs", {}).values()
                    for im in node.get("images", [])]
            out[tag] = imgs
            print(f"[OK] {zh:8s} {tag:34s} seed={sd} {time.time()-t0:.1f}s -> {imgs}")
        except Exception as e:
            print(f"[FAIL] {zh:8s} {tag:34s} {e}")
    print("\n汇总:", json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
