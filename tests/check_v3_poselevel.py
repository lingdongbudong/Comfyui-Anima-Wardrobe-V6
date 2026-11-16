# -*- coding: utf-8 -*-
"""姿势库按档内容包基线（v6.12.0）。

守六件事：

  ① **到位** —— 姿势场景（库里全是 L0/L1）手动套用到 L1–L4，`real_level`
     必须等于所选档。改前实测：L3 补出 `n_pos=upright straddle`（与
     `pose_base=sitting` 打架）、L4 只补 `n_orgasm=squirting`（画面还穿着衣服）。

  ② **不越档** —— 补进去的每个值都必须过 `_field_ok_under`（`option_level <= lv`）。

  ③ **姿势不被动** —— `pose_base` 保持场景原值；内容包**不补 `n_pos`**
     （通用保底的第一顺位就是 `n_pos`，那正是"坐着 + 跨骑"的来源）。

  ④ **文案同步** —— 追加句必须进正文；`n_state` 被服装库整套闸门挤掉时，
     追加句要换成"衣物推到一边"那版，不能还写"最后一件也褪了"。

  ⑤ **SFW 零漂移** —— lv=0 一次都不触发。

  ⑥ **不误伤** —— 非 pose 组（nsfw）仍走通用保底，报告里不出现"姿势档位适配"。

跑法：python tests/check_v3_poselevel.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")

PASS, FAIL = [], []


def ck(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK' if ok else '!!'}] {name}" + (f"  {note}" if note and not ok else ""))


def weights_for(sid):
    """只让这一条场景有概率被抽中（其余 n_canon 选项权重 0）。"""
    w = R.empty_weights()
    w["fields"]["n_canon"] = 3.0
    ids = [en for _z, en in R.field_by_key("n_canon")[1]["opts"]]
    ids += [x for _z, x, _n in R.scene_options(99, True)]
    w["options"]["n_canon"] = {k: 0.0 for k in ids}
    w["options"]["n_canon"][sid] = 3.0
    return w


def pose_samples(n=6):
    pose = [s for s in R.scene_list() if s.get("group") == "pose"]
    # 取 L0 与 L1 各若干，覆盖 manual_only 与非 manual_only
    out = [s for s in pose if s.get("manual_only")][: n // 2]
    out += [s for s in pose if not s.get("manual_only")][: n - len(out)]
    return out


PACK_KEYS = {"pose_risque", "n_state", "n_exposure", "n_orgasm",
             "n_theme", "n_act", "n_cum", "n_body", "n_fabric",
             "cloth_state_sfw"}
NL_NUDE = "the last of her clothes is gone"
NL_ASIDE = "her clothes are shoved aside"
NL_LACE = "the lace is pulled aside"


def main():
    samples = pose_samples(6)
    ck("姿势库里有样本可测", len(samples) >= 4, str(len(samples)))

    print("\n── ① 到位：L1–L4 每档都真的到该档 ──")
    miss = []
    pack_hit = 0
    for sc in samples:
        sid = sc["id"]
        for lv in (1, 2, 3, 4):
            for seed in (11, 777, 99999):
                r = R.draw(level=lv, seed=seed, weights=weights_for(sid),
                           base_prob=0.9)
                real = int(r["report"].get("real_level") or 0)
                if real != lv:
                    miss.append((sid, lv, real))
                if any("姿势档位适配" in str(n) for n in r["notes"]):
                    pack_hit += 1
    ck(f"姿势场景 6 条 × 4 档 × 3 seed = {6 * 4 * 3} 抽，real_level 全部等于所选档",
       not miss, str(miss[:5]))
    ck("内容包确实被触发过（否则本项测的是空转）", pack_hit > 0, str(pack_hit))

    print("\n── ② 不越档 + ③ 姿势不被动 ──")
    over, moved, pos_added = [], [], []
    for sc in samples:
        sid = sc["id"]
        base_pose = (sc.get("tags") or {}).get("pose_base")
        for lv in (2, 3, 4):
            r = R.draw(level=lv, seed=777, weights=weights_for(sid), base_prob=0.9)
            st = r["state"]
            for k, v in st.items():
                if str(k).startswith("_") or not v:
                    continue
                if int(R.option_level(k, v)) > lv:
                    over.append((sid, lv, k, v))
            if st.get("pose_base") != base_pose:
                moved.append((sid, lv, base_pose, st.get("pose_base")))
            for n in r["notes"]:
                if "姿势档位适配" in str(n) and "n_pos" in str(n):
                    pos_added.append((sid, lv, n))
    ck("补进去的字段全部不越档", not over, str(over[:4]))
    ck("pose_base 保持场景原值（姿势不被动）", not moved, str(moved[:4]))
    ck("内容包不补 n_pos（避免与 pose_base 打架）", not pos_added, str(pos_added[:3]))

    print("\n── ④ 文案同步 ──")
    bad_txt, bad_alt = [], []
    for sc in samples:
        sid = sc["id"]
        for seed in (11, 777, 99999):
            r = R.draw(level=4, seed=seed, weights=weights_for(sid), base_prob=0.9)
            st, txt = r["state"], r["text"].lower()
            if not any("姿势档位适配" in str(n) for n in r["notes"]):
                continue
            if st.get("n_state") == "completely nude":
                if NL_NUDE not in txt:
                    bad_txt.append((sid, seed, "nude 但没写褪衣句"))
                if "white bikini" in txt or "swimsuit" in txt:
                    bad_txt.append((sid, seed, "全裸与泳装同现"))
            elif st.get("n_theme") == "collar and leash":
                if NL_LACE not in txt:
                    bad_txt.append((sid, seed, "情趣走向但没写 lace 句"))
            elif not st.get("n_state"):
                if NL_ASIDE not in txt:
                    bad_alt.append((sid, seed, "n_state 让位但文案没换"))
    ck("全裸时正文写了褪衣句，且不同时出现泳装", not bad_txt, str(bad_txt[:4]))
    ck("n_state 让位给服装库时，追加句换成「衣物推到一边」版",
       not bad_alt, str(bad_alt[:4]))

    print("\n── ④b 双人升级 / 道具分支 ──")
    act_bad, tri_bad, solo_bad, prop_bad = [], [], [], []
    for sc in samples:
        sid = sc["id"]
        base = (sc.get("tags") or {}).get("pose_base")
        expect_act = R.POSE_ACT_MAP.get(str(base or ""))
        for lv in (3, 4):
            r = R.draw(level=lv, seed=777, weights=weights_for(sid), base_prob=0.9)
            st, txt = r["state"], r["text"].lower()
            if not any("姿势档位适配" in str(n) for n in r["notes"]):
                continue
            if expect_act:
                if st.get("count") != "1girl, 1boy, hetero":
                    tri_bad.append((sid, lv, "人数没升双人", st.get("count")))
                if st.get("n_act") != expect_act:
                    act_bad.append((sid, lv, expect_act, st.get("n_act")))
                if "faceless male" not in txt:
                    tri_bad.append((sid, lv, "缺 faceless male"))
            else:
                if st.get("count") != "1girl, solo" or st.get("n_act"):
                    solo_bad.append((sid, lv, st.get("count"), st.get("n_act")))
            if not (st.get("n_theme") or "").strip():
                prop_bad.append((sid, lv, "L3/L4 没写道具"))
    ck("有合理双人动作的姿势：L3/L4 人数升双人 + 动作与 pose_base 匹配",
       not tri_bad and not act_bad, str((tri_bad + act_bad)[:4]))
    ck("无合理双人动作的姿势：保持单人、不写 n_act", not solo_bad, str(solo_bad[:4]))
    ck("L3/L4 都带了情趣道具", not prop_bad, str(prop_bad[:4]))

    print("\n── ④c L2：情趣内衣 + 透视 ──")
    l2_bad = []
    for sc in samples:
        sid = sc["id"]
        r = R.draw(level=2, seed=777, weights=weights_for(sid), base_prob=0.9)
        st = r["state"]
        if not any("姿势档位适配" in str(n) for n in r["notes"]):
            continue
        if st.get("n_fabric") != "lingerie" or st.get("n_state") != "see-through":
            l2_bad.append((sid, st.get("n_fabric"), st.get("n_state")))
    ck("L2 写入了 lingerie + see-through", not l2_bad, str(l2_bad[:4]))

    print("\n── ④d 整套服装必须让位（L2–L4）──")
    # 实测 176 张里 137 张出现「日常整套 + 情趣内衣/全裸」，甚至
    # 「completely nude + trench coat」—— 服装库整套闸门 weight 3.0 赢过了内容包。
    cloth_bad = []
    for sc in samples:
        sid = sc["id"]
        keys = set((sc.get("tags") or {}).keys())
        for lv in (2, 3, 4):
            r = R.draw(level=lv, seed=777, weights=weights_for(sid), base_prob=0.9)
            st = r["state"]
            if not any("姿势档位适配" in str(n) for n in r["notes"]):
                continue
            for k in ("outfit", "top", "bottom"):
                if k in keys:
                    continue
                if str(st.get(k) or "").strip():
                    cloth_bad.append((sid, lv, k, st.get(k)))
    ck("L2–L4 抽里 outfit / top / bottom 一律被清空（情趣内衣让位生效）",
       not cloth_bad, str(cloth_bad[:4]))

    print("\n── ⑤ SFW 零漂移 ──")
    drift = []
    for sc in samples:
        for seed in range(20):
            r = R.draw(level=0, seed=seed, weights=weights_for(sc["id"]),
                       base_prob=0.9)
            if any("姿势档位适配" in str(n) for n in r["notes"]):
                drift.append((sc["id"], seed))
    ck("lv=0 一次都不触发内容包", not drift, str(drift[:4]))

    print("\n── ⑤b 随机填充必须让位给内容包 ──")
    # 实测（v6.12.0 真机 24 抽）：`n_orgasm` 被随机填成 `after sex` 后，
    # 旧实现"state 有值就跳过" → L4 只补到 `completely nude`(L2)+`vibrator`(L3)，
    # 整条其实停在 L3，而追加句却写着"潮吹"。所以随机值必须被覆盖。
    lazy = []
    for sc in samples:
        sid = sc["id"]
        scene_keys = set((sc.get("tags") or {}).keys())
        for seed in (11, 777, 99999):
            r = R.draw(level=4, seed=seed, weights=weights_for(sid), base_prob=0.9)
            st = r["state"]
            if not any("姿势档位适配" in str(n) for n in r["notes"]):
                continue
            if "n_orgasm" not in scene_keys and st.get("n_orgasm") != "squirting":
                lazy.append((sid, seed, st.get("n_orgasm")))
            if "n_state" not in scene_keys and st.get("n_state") \
                    and st.get("n_state") != "completely nude":
                lazy.append((sid, seed, st.get("n_state")))
    ck("L4 抽里 n_orgasm 一律被覆盖为 squirting（随机填充不占位）",
       not lazy, str(lazy[:4]))

    print("\n── ⑥ 不误伤其它组 ──")
    nsfw = [s for s in R.scene_list() if s.get("group") == "nsfw"
            and int(s.get("level", 0)) == 3][:2]
    wrong = []
    for sc in nsfw:
        for seed in (11, 777):
            r = R.draw(level=4, seed=seed, weights=weights_for(sc["id"]),
                       base_prob=0.9)
            if any("姿势档位适配" in str(n) for n in r["notes"]):
                wrong.append((sc["id"], seed))
    ck("nsfw 组仍走通用保底，不出现姿势包", not wrong, str(wrong[:4]))

    print("\n── ⑦ F11 不报红（追加句是整句，不是超长标签）──")
    f11 = []
    for sc in samples:
        r = R.draw(level=4, seed=777, weights=weights_for(sc["id"]), base_prob=0.9)
        if not any("姿势档位适配" in str(n) for n in r["notes"]):
            continue
        _st, rep = R.check_text(r["tags"], 4)
        bad = [x for x in rep["F"] if x["id"] == "F11" and not x["ok"]]
        if bad:
            f11.append((sc["id"], bad[0].get("note")))
    ck("追加整句不被 F11 判成超长未登记标签", not f11, str(f11[:3]))

    print("\n── ⑧ 全闭麦场景：尊重闭麦，报告写明低于所选档 ──")
    # `sc_pose_skirt_lift_disdain` 把 n_state / n_exposure / n_orgasm / pose_risque
    # 全闭麦（18 个字段），这是作者刻意保持"只掀裙、不脱"的构图 ——
    # 内容包不许硬塞，也不许静默降级：必须停在本档 + 报告写"无可用载体"。
    mute = [s for s in R.scene_list() if s.get("group") == "pose"
            and {"n_state", "n_exposure", "n_orgasm"} <= set(s.get("drop_fields") or [])]
    ck("库里存在全闭麦的姿势场景（否则本项测不到）", bool(mute), str(len(mute)))
    silent, no_note = [], []
    for sc in mute[:3]:
        for lv in (2, 4):
            r = R.draw(level=lv, seed=777, weights=weights_for(sc["id"]), base_prob=0.9)
            real = int(r["report"].get("real_level") or 0)
            if real >= lv:
                silent.append((sc["id"], lv, real))
            if not any("无可用载体" in str(n) for n in r["notes"]):
                no_note.append((sc["id"], lv))
    ck("闭麦场景不会被内容包硬塞到高档", not silent, str(silent[:4]))
    ck("报告写明「无可用载体 —— 本张会低于所选档」", not no_note, str(no_note[:4]))

    print("\n── ⑨ L1 也必须真触发（不是靠随机填充蒙过去）──")
    # L1 只有 `pose_risque` 一个载体，且随机填充经常先占位 —— 所以必须确认
    # 内容包把它**覆盖**成 `arched back` 并追加了那句话，而不是"档位数字够了"。
    l1_bad, l1_skip = [], []
    for sc in samples:
        sid = sc["id"]
        keys = set((sc.get("tags") or {}).keys())
        drops = set(sc.get("drop_fields") or [])
        if "pose_risque" in keys or "pose_risque" in drops:
            l1_skip.append(sid)
            continue
        for seed in (11, 777, 99999):
            r = R.draw(level=1, seed=seed, weights=weights_for(sid), base_prob=0.9)
            st, txt = r["state"], r["text"].lower()
            if st.get("pose_risque") != "arched back":
                l1_bad.append((sid, seed, st.get("pose_risque")))
            if "her back arches a little" not in txt:
                l1_bad.append((sid, seed, "缺追加句"))
    ck(f"L1 抽里 pose_risque 一律被覆盖为 arched back 且追加句进正文"
       f"（跳过闭麦/写死的 {len(l1_skip)} 条）", not l1_bad, str(l1_bad[:4]))

    print("\n" + "-" * 72)
    print(f"[check_v3_poselevel] 通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败用例：" + " · ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
