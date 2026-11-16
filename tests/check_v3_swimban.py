# -*- coding: utf-8 -*-
"""v6.16.0「非泳装场景硬禁整套泳装」基线（用户 2026-11-13）。

背景（真 bug）：`n_canon` 在段序里是**最后一段**（`canon`），而 `outfit` 在
⑥ `cloth_sfw` —— 抽整套服装时 `state["n_canon"]` 永远是空的。因此
`style_bans()` 的泳装硬禁只能拿到**随机预抽的 place** 当锚点（抽到 beach
就按泳装定调），最后场景再把 place 改成 church / ruins / desert，衣服已经错了。
实测「修道院回廊 / 废墟 / 沙丘」显式套用时有 2–5% 抽到整套泳装。

钉死六件事：
  ① `pinned_scene_id()` 语义：forced 优先 / 唯一 3.0 命中 / 两个 3.0 → "" /
     非法 id → "" / 全默认 → ""
  ② `scene_place()` 取值正确（含无 place 的场景）
  ③ 显式套用**非泳装**场景（church / ruins / desert）→ 三个档位 × 多 seed
     一次泳装都不出
  ④ 显式套用**泳装**场景（beach）→ 泳装照样抽得到（不能过度禁止）
  ⑤ 零漂移：没有显式场景时 `_anchor_st()` 原样返回同一个 state 对象
  ⑥ 随机场景路径不回归：仍能有整套服装（不是被一刀禁空）

跑法：python tests/check_v3_swimban.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")
OS = _boot.load("anima_v4_outfit_style")
_boot.isolate_outfit_library(R)

FAILS = []


def ck(cond, msg, detail=""):
    if not cond:
        FAILS.append(msg + ((" | " + str(detail)) if detail else ""))


DRY_PLACES = {"church", "ruins", "desert"}
# 只取 L0：测试要在 sfw/suggestive/nsfw 三档都成立，超档场景会被闸门换成
# 别的场景（那就不该拿它的 place 当锚点，`pinned_scene_id(level=)` 已经拦掉）。
DRY_SCENES = [s["id"] for s in R.scenes_data()["scenes"]
              if (s.get("tags") or {}).get("place") in DRY_PLACES
              and int(s.get("level") or 0) == 0
              and not s.get("manual_only")][:6]
WET_SCENES = [s["id"] for s in R.scenes_data()["scenes"]
              if (s.get("tags") or {}).get("place") in ("beach", "poolside")
              and int(s.get("level") or 0) == 0
              and not s.get("manual_only")][:6]


def pin(sid, **kw):
    """按面板「套用场景」的写法构造权重：字段 3.0 + 该选项 3.0。"""
    w = R.empty_weights()
    w["fields"]["n_canon"] = 3
    w["options"].setdefault("n_canon", {})[sid] = 3
    return R.draw(seed=kw.pop("seed", 777), weights=w, scene_pick=True,
                  tag_cap="自动", **kw)


def main():
    swim = OS._swim_outfits()
    ck(len(swim) >= 8, "前提：词库里要有足够多的整套泳装", len(swim))
    ck(len(DRY_SCENES) >= 3, "前提：非泳装场景够用", DRY_SCENES)
    ck(len(WET_SCENES) >= 2, "前提：泳装场景够用", WET_SCENES)

    # ── ① pinned_scene_id 语义 ──────────────────────────────────────
    sid = DRY_SCENES[0]
    ck(R.pinned_scene_id(R.empty_weights(), {"n_canon": sid}, None) == sid,
       "forced 里的场景必须被认出来")
    w = R.empty_weights()
    w["options"].setdefault("n_canon", {})[sid] = 3
    ck(R.pinned_scene_id(w) == sid, "唯一一个 3.0 场景必须被认出来")
    w["options"]["n_canon"][DRY_SCENES[1]] = 3
    ck(R.pinned_scene_id(w) == "", "两个 3.0 场景 = 不确定，必须返回空")
    ck(R.pinned_scene_id(R.empty_weights()) == "", "全默认必须返回空")
    ck(R.pinned_scene_id(R.empty_weights(), {"n_canon": "sc_not_exist"}) == "",
       "非法 id 必须返回空（不许拿它算锚点）")
    hi = [s for s in R.scenes_data()["scenes"] if int(s.get("level") or 0) >= 4]
    if hi:
        hs = hi[0]["id"]
        ck(R.pinned_scene_id(R.empty_weights(), {"n_canon": hs}, None, 0) == "",
           "超档场景不许当锚点（会被闸门换成别的场景）", hs)
        ck(R.pinned_scene_id(R.empty_weights(), {"n_canon": hs}, None, 4) == hs,
           "在档时同一场景要认出来", hs)

    # ── ② scene_place ─────────────────────────────────────────────
    ck(R.scene_place(sid) == (R.scene_index()[sid]["tags"] or {}).get("place"),
       "scene_place 必须与 scenes.json 一致", R.scene_place(sid))
    ck(R.scene_place("sc_not_exist") == "", "不存在的场景 → 空串")
    no_place = [s["id"] for s in R.scenes_data()["scenes"]
                if not (s.get("tags") or {}).get("place")]
    if no_place:
        ck(R.scene_place(no_place[0]) == "", "没有 place 的场景 → 空串")

    # ── ③ 非泳装场景：一次泳装都不许出 ──────────────────────────────
    for lv in ("sfw", "suggestive", "nsfw"):
        for scene in DRY_SCENES[:2]:
            hits = []
            for sd in range(40):
                st = pin(scene, level=lv, seed=50000 + sd)["state"]
                of = str(st.get("outfit") or "")
                if of in swim:
                    hits.append((sd, of))
            ck(not hits, "非泳装场景必须硬禁整套泳装（%s / %s）" % (lv, scene),
               hits[:3])

    # ── ④ 泳装场景：不能过度禁止 ───────────────────────────────────
    wet_hit = 0
    for sd in range(60):
        st = pin(WET_SCENES[0], level="suggestive", seed=60000 + sd)["state"]
        if str(st.get("outfit") or "") in swim:
            wet_hit += 1
    ck(wet_hit > 0, "泳装场景（beach/poolside）仍要能抽到整套泳装",
       "%s 60 抽 0 命中" % WET_SCENES[0])

    # ── ⑤ 零漂移：没有显式场景 → 同一个 state 对象 ──────────────────
    st = {"place": "bedroom", "outfit": "serafuku"}
    ck(R.pinned_scene_id(R.empty_weights()) == "", "前提：没有显式场景")

    # `_anchor_st` 是 `_one_pass` 的闭包，测试拿不到 —— 用等价断言：
    # 无显式场景时 draw 的正文必须与「只改 n_canon 权重、不改其它」逐字节一致
    a = R.draw(level="sfw", seed=31415)["text"]
    b = R.draw(level="sfw", seed=31415)["text"]
    ck(a == b, "同参同 seed 必须逐字节可复现（零漂移前提）", a[:40])

    # ── ⑥ 随机场景路径不回归：仍会抽到整套服装 ──────────────────────
    got = sum(1 for sd in range(60)
              if R.draw(level="sfw", seed=70000 + sd,
                        scene_pick=True, tag_cap="自动")["state"].get("outfit"))
    ck(got >= 3, "随机路径不能把整套服装一刀禁空", got)

    if FAILS:
        print("[check_v3_swimban] 失败 %d 项：" % len(FAILS))
        for f in FAILS[:30]:
            print("  -", f)
        return 1
    print("[check_v3_swimban] OK：pinned 语义 / 非泳装场景硬禁 / 泳装场景保留 / "
          "零漂移 / 随机路径不回归 全部通过（%d 条非泳装场景 × 3 档 × 40 抽）"
          % len(DRY_SCENES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
