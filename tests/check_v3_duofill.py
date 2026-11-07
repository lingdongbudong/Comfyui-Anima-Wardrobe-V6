# -*- coding: utf-8 -*-
"""v6.6.0 双女自动补位基线（用户 2026-11-07「避免画面出来两个相同角色」）。

钉死七件事：
  ① `pick_duo_partner` 语义：同作品优先 / 排除自身 / 排除已入列 / 小 IP 回退全库
  ② 双女场景 + 只选 1 位角色 → 正文里出现**两个不同**角色，人数仍是场景钉的 2girls
  ③ **不改写人数语义**：补位后 count 不许被抬成 `2girls, yuri`（L0 双女场景不许被升级）
  ④ 单人场景 / 已选 2 位 / 不指定角色 → 一条都不补（不该动的别动）
  ⑤ 关闭开关 = 零漂移：`duo_role_fill=False` 时补位之前的角色串与女角色数不变
  ⑥ 同 rng 下**单人场景**开关 on/off 逐字节一致（补位只吃双女那一条分支的 rng）
  ⑦ 节点层：`optional` 末尾有 `duo_role_fill`（默认 True）+ IS_CHANGED 指纹含它

跑法：python tests/check_v3_duofill.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")
# 服装库启用态会覆盖 13 个服装字段 —— 一律走临时副本隔离，真实状态零改动。
_boot.isolate_outfit_library(R)

FAILS = []


def ck(cond, msg, detail=""):
    if not cond:
        FAILS.append(msg + ((" | " + str(detail)) if detail else ""))


def _w(preset_id):
    """造一份「必现某条场景」的权重表。"""
    w = R.empty_weights()
    w["fields"]["n_canon"] = 3.0
    w["options"]["n_canon"] = {preset_id: 3.0}
    return w


def _duo_scenes():
    return [s for s in R.scenes_data().get("scenes", [])
            if str(s.get("count_lock") or "").startswith("2girls")]


def _one_char_work():
    """找一个只有 1 位女性角色的作品，返回它的 trigger。"""
    import collections
    data = R.characters_data()
    cnt = collections.Counter(c.get("work") for c in data.get("characters", []))
    for c in data.get("characters", []):
        if cnt.get(c.get("work")) == 1 and c.get("trigger"):
            return c["trigger"]
    return ""


def main():
    import random

    # ── ① pick_duo_partner 语义 ───────────────────────────────────
    base = "texas, arknights"            # 方舟：同作品另有大量女性
    rng = random.Random(1234)
    got, mode = R.pick_duo_partner(rng, 4, base)
    ck(mode == "same_work", "同作品有第二人时必须 same_work", mode)
    ck(got and got.strip().lower() != base.lower(), "补位不能是她自己", got)
    ck(R.role_work_index().get(got.strip().lower()) == "arknights",
       "same_work 补到的必须同作品", got)
    for _ in range(20):
        g, m = R.pick_duo_partner(random.Random(_), 4, base)
        ck(m == "same_work" and R.role_work_index().get(g.strip().lower()) == "arknights",
           "同作品路径 20 次里必须每次都落在 arknights", g)
    # 排除已入列（两人阵容）
    other = "utage, arknights"
    for s in range(30):
        g, _m = R.pick_duo_partner(random.Random(s), 4, base, exclude=[base, other])
        ck(g.strip().lower() != other.lower(), "exclude 里的角色不许再被选中", g)
    # 小 IP → 回退全库
    solo_work = _one_char_work()
    ck(bool(solo_work), "角色库里必须有单角色作品（否则这条用例没意义）")
    if solo_work:
        g, m = R.pick_duo_partner(random.Random(7), 4, solo_work)
        ck(m == "cross_work" and g, "单角色作品必须回退全库（cross_work）", (g, m))
        ck(g.strip().lower() != solo_work.lower(), "回退也不能重复她自己", g)

    # ── ②③ 双女场景 + 只选 1 位 ──────────────────────────────────
    duo = _duo_scenes()
    ck(len(duo) == 23, "双女场景应为 23 条", len(duo))
    l0_duo = [s for s in duo if int(s.get("level", 0)) == 0]
    ck(bool(l0_duo), "必须存在 L0 双女场景（用来验证不被升级成 yuri）")
    sc0 = (l0_duo or duo)[0]
    for seed in (11, 22, 33):
        r = R.draw(level="sfw", seed=seed,
                   weights=_w(sc0["id"]),
                   selected_roles=[base], scene_pick=True)
        roles = [t.strip().lower() for t in str(r["state"].get("role") or "").split(",")]
        ck(len(R.role_output_tags(r["state"])) == 2,
           "双女场景 + 1 位角色必须补成 2 位", (seed, r["state"].get("role")))
        ck(roles.index(base.split(",")[0].strip()) >= 0,
           "用户点的那位必须还在", r["state"].get("role"))
        ck(any("补位" in n for n in r["notes"]),
           "报告里必须明说补了位（不许静默）", r["notes"][:4])
        ck(str(r["state"].get("count") or "").startswith("2girls"),
           "人数必须仍是场景钉的 2girls", r["state"].get("count"))
        ck("yuri" not in str(r["state"].get("count") or ""),
           "L0 双女场景不许被补位逻辑升成 yuri", r["state"].get("count"))
        # 正文里两个角色 tag 必须都在
        txt = str(r["text"]).lower()
        for t in R.role_output_tags(r["state"]):
            for part in str(t).split(","):
                part = part.strip().lower()
                if part:
                    ck(part in txt, "补到的角色 tag 必须进正文", part)

    # ── ④ 不该补的三种情形 ────────────────────────────────────────
    solo_sc = next(s for s in R.scenes_data().get("scenes", [])
                   if str(s.get("count_lock") or "") == "1girl, solo")
    r = R.draw(level="sfw", seed=11, weights=_w(solo_sc["id"]),
               selected_roles=[base], scene_pick=True)
    ck(len(R.role_output_tags(r["state"])) == 1,
       "单人场景不许补位", r["state"].get("role"))
    ck(not any("补位" in n for n in r["notes"]),
       "单人场景不该出现补位报告", [n for n in r["notes"] if "补位" in n])

    r2 = R.draw(level="sfw", seed=11, weights=_w(sc0["id"]),
                selected_roles=[base, other], scene_pick=True)
    ck(len(R.role_output_tags(r2["state"])) == 2,
       "已经选了 2 位时不许再补第 3 位", r2["state"].get("role"))

    r3 = R.draw(level="sfw", seed=11, weights=_w(sc0["id"]),
                selected_roles=[base], role_pick=False, scene_pick=True)
    ck(not str(r3["state"].get("role") or "").strip(),
       "「不指定角色」时不许补位", r3["state"].get("role"))

    # ── ⑤ 关闭开关 = 不补（零漂移口径）────────────────────────────
    off = R.draw(level="sfw", seed=11, weights=_w(sc0["id"]),
                 selected_roles=[base], scene_pick=True, duo_role_fill=False)
    ck(len(R.role_output_tags(off["state"])) == 1,
       "关闭开关时不许补位", off["state"].get("role"))
    ck(str(off["state"].get("count") or "").startswith("2girls"),
       "关闭开关时人数仍是 2girls（行为与 v6.5.2 一致）", off["state"].get("count"))
    ck(not any("补位" in n for n in off["notes"]), "关闭时不该有补位报告")

    # ── ⑥ 单人场景下 on/off 逐字节一致（补位不吃无关分支的 rng）───
    for seed in (5, 17, 29):
        a = R.draw(level="sfw", seed=seed, weights=_w(solo_sc["id"]),
                   selected_roles=[base], scene_pick=True)
        b = R.draw(level="sfw", seed=seed, weights=_w(solo_sc["id"]),
                   selected_roles=[base], scene_pick=True, duo_role_fill=False)
        ck(a["text"] == b["text"] and a["state"] == b["state"],
           "单人场景下开关 on/off 必须逐字节一致（不许偷偷吃 rng）", seed)

    # ── ⑦ 节点层 ─────────────────────────────────────────────────
    ND = _boot.load("nodes")
    cls = ND.AnimaV4RandomPrompt
    opt = cls.INPUT_TYPES().get("optional") or {}
    # v6.7.0：后面又追加了勾选池两个控件 —— "末尾"会一直往后长，
    # 断言改成"紧跟干爽三件套"，顺序契约照样守住。
    _opt = list(opt)
    ck("duo_role_fill" in _opt
       and _opt.index("duo_role_fill") == _opt.index("dry_skin_tags") + 1,
       "duo_role_fill 必须紧跟干爽三件套（控件顺序是契约）", _opt)
    ck(opt["duo_role_fill"][1].get("default") is True,
       "默认必须是开（用户 2026-11-07 拍板）")
    f_on = cls.IS_CHANGED(seed=424242)
    f_off = cls.IS_CHANGED(seed=424242, duo_role_fill=False)
    ck(f_on != f_off and "duofill:1" in f_on and "duofill:0" in f_off,
       "IS_CHANGED 必须把双女补位开关并进指纹", (f_on[-40:], f_off[-40:]))

    # ── ⑧ v6.6.1 审查修复：服装整句「一位也要写」+ 点名项不被多人清空 ──
    # ① `_duo_join(need=1)`：双人里只有一位启用预设时，她的官方服装必须写出来。
    #    以前 `<2 句就返回空` → 整句消失 + 共享服装字段不清 → 两人穿随机服装。
    ck(R._duo_join(["texas wears collared shirt."], need=1) != "",
       "need=1 时一位也要出整句（v6.6.1 修的就是这条）")
    ck(R._duo_join(["texas wears collared shirt."]) == "",
       "_duo_join 默认门槛仍是 2（外貌 / 官方配件的既有语义不许动）")
    ck(R._duo_join(["a has x.", "b has y."], need=1)
       == R._duo_join(["a has x.", "b has y."]),
       "两人时 need=1 与 need=2 输出必须一致（零漂移）")
    _one = R.duo_outfit_text(["texas, arknights"], 4, False)
    ck(_one == "" or "wears" in _one,
       "单条调用要么空、要么是合规的「谁穿什么」整句", _one[:60])
    # ② 点名（3.0）的 n_act 在多人下**保留**并写进报告
    _wf = _w(sc0["id"])
    _wf["options"]["n_act"] = {"fingering": 3.0}
    _r = R.draw(level="explicit", seed=5, weights=_wf,
                selected_roles=[base], scene_pick=True)
    ck(len(R.role_output_tags(_r["state"])) == 2,
       "该用例前提：确实补成了 2 位", _r["state"].get("role"))
    ck(str(_r["state"].get("n_act") or "") == "fingering",
       "点名 3.0 的 n_act 不许被「多人清空」吃掉",
       (str(_r["state"].get("n_act")), [n for n in _r["notes"] if "冲突" in n]))
    ck(any("显式优先" in n for n in _r["notes"]),
       "保留点名项必须写进报告（不许静默）",
       [n for n in _r["notes"] if "冲突" in n])
    # ③ 库外自定义角色 → 报告措辞要区分「不在库里」与「作品只有她一位」
    _rc = R.draw(level="sfw", seed=7, weights=_w(sc0["id"]),
                 force_role="my custom oc, original", scene_pick=True)
    ck(any("不在角色库里" in n for n in _rc["notes"]),
       "库外角色必须报「不在角色库」，不能谎称作品里只有她一位",
       [n for n in _rc["notes"] if "补位" in n])
    # ④ pick_duo_partner 的三种 mode
    _g, _m = R.pick_duo_partner(random.Random(1), 4, "my custom oc, original")
    ck(_m == "unknown_work", "库外角色 mode 必须是 unknown_work", _m)

    # ── ⑩ v6.6.2：人数判据 / 随机池约束 ─────────────────────────
    # ① 关掉补位时「1 位角色 + 2girls」也要清多人字段（以前按角色数判，漏）
    _r1 = R.draw(level="sfw", seed=5, weights=_w(sc0["id"]),
                 selected_roles=[base], scene_pick=True, duo_role_fill=False)
    ck(len(R.role_output_tags(_r1["state"])) == 1,
       "前提：关补位时确实只有 1 位角色", _r1["state"].get("role"))
    ck(bool(_r1["state"].get("_cleared_for_multi")),
       "1 位角色 + 2girls 也要按「画面 2 人」清多人字段（v6.6.2 判据修正）",
       _r1["state"].get("count"))
    # 单人场景不许误清（girl_count=1）
    _solo = R.draw(level="sfw", seed=5,
                   weights=_w(next(s["id"] for s in R.scenes_data()["scenes"]
                                   if str(s.get("count_lock") or "") == "1girl, solo")),
                   selected_roles=[base], scene_pick=True)
    ck(not _solo["state"].get("_cleared_for_multi"),
       "单人场景不许触发多人清空", _solo["state"].get("count"))
    # ② 补位尊重「🔁 随机池」（role_works）—— 池内没人同作品就取池内其他
    for _ws, _slug in ((("genshin_impact",), "genshin_impact"),
                       (("zenless_zone_zero",), "zenless_zone_zero")):
        _p, _pm = R.pick_duo_partner(random.Random(3), 4, base, works=list(_ws))
        ck(R.role_work_index().get(_p.strip().lower()) == _slug,
           "补位必须落在随机池的作品内（%s）" % _slug, _p)
    _p0, _ = R.pick_duo_partner(random.Random(3), 4, base)
    ck(R.role_work_index().get(_p0.strip().lower()) == "arknights",
       "没设随机池时仍优先同作品（既有语义）", _p0)

    # ── ⑨ v6.6.1 端到端：双人里**只有一位**启用服装预设 → 正文必须有 wears 句 ──
    # 这是审查里最贵的一条：预设被静默丢弃、随机服装顶上来，而备注还说"已按人绑定"。
    S = _boot.load("anima_v4_store")
    TIFA = "tifa lockhart, final fantasy"
    SAGA = "saga, arknights"
    _prev = ((R.outfits_data().get("tifa_lockhart") or {}).get("enabled") or "")
    try:
        S.set_outfit_enabled("tifa_lockhart", "default")
        R.clear_cache()
        _rd = R.draw(level="sfw", seed=13, weights=_w(sc0["id"]),
                     selected_roles=[TIFA], scene_pick=True)
        _tx = str(_rd["text"])
        ck(len(R.role_output_tags(_rd["state"])) == 2,
           "端到端前提：确实补成了 2 位", _rd["state"].get("role"))
        ck("tifa" in _tx.lower() and "wears" in _tx,
           "双人里唯一启用预设的人必须写成「谁穿什么」整句",
           [t for t in _tx.split(",") if "wears" in t][:1])
        _ti = R.outfit_item(TIFA) or {}
        _hit = [t for t in (_ti.get("tags") or [])[:3] if str(t).lower() in _tx.lower()]
        ck(len(_hit) >= 2, "她预设里的标签必须真的进正文", _hit)
        ck(any("服装按人绑定" in n for n in _rd["notes"]),
           "报告要写明服装按人绑定", [n for n in _rd["notes"] if "服装" in n][:2])
        # 同一颗种子、关掉补位（单人）→ 走单角色闭麦通道，仍然只有她
        _rs = R.draw(level="sfw", seed=13, weights=_w(sc0["id"]),
                     selected_roles=[TIFA], scene_pick=True, duo_role_fill=False)
        ck("单人态仍走「已锁定服装」通道（没被 need=1 改坏）",
           any("已锁定服装" in n for n in _rs["notes"]),
           [n for n in _rs["notes"] if "服装" in n][:2])
        # ⑨-b v6.6.1：场景**点名闭麦衣物**（全裸）时，预设必须让路 ——
        # 否则正文同时写 `white tank top / black skirt` 和 `completely nude`。
        for _tag, _fl in (("单人", False), ("双人", True)):
            _rn = R.draw(level=3, seed=8300,
                         forced={"n_canon": "sc_near_nsfw_cunnilingus"},
                         selected_roles=[TIFA], duo_role_fill=_fl)
            _tn = str(_rn["text"]).lower()
            ck("%s：全裸场景里不许再写她的官方服装" % _tag,
               not any(w in _tn for w in ("white tank top", "black skirt", "suspenders")),
               [w for w in ("white tank top", "black skirt", "suspenders") if w in _tn])
            ck("%s：让路必须写进报告" % _tag,
               any("让路" in n for n in _rn["notes"]),
               [n for n in _rn["notes"] if "服装" in n][:2])
    finally:
        try:
            S.set_outfit_enabled("tifa_lockhart", _prev)
            R.clear_cache()
        except Exception:
            pass

    if FAILS:
        print("[check_v3_duofill] 失败 %d 项：" % len(FAILS))
        for f in FAILS[:30]:
            print("  -", f)
        return 1
    print("[check_v3_duofill] OK：同作品优先 / 小 IP 回退 / 不乱补 / "
          "不改写人数 / 关闭零漂移 / 节点指纹 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
