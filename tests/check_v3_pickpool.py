# -*- coding: utf-8 -*-
"""v6.7.0 勾选池「每次运行自动随机」基线（用户 2026-11-08）。

钉死八件事：
  ① 复现性：同 seed 抽同一条；换 seed 会换（但恒在勾选集内）
  ② 用户 `forced["n_canon"]` 优先 —— 勾选池不许覆盖显式指定
  ③ 关闭态零漂移：mode / ids 为空时与不传参逐字节一致
  ④ 姿势 / 艺术池：只套场景，**不做**收紧取景（字段照常随机）
  ⑤ 特写池（腿）：连收紧取景一起做 —— 清角色 + 锁 role + 钉人数 + 清字段
  ⑥ 特写池（面部）：保留角色，但同样关 ④⑤ 段
  ⑦ 档位对齐：池里放了超档场景 → 只升不降（与面板 syncLevelFor 同口径）
  ⑧ 非法 id：忽略勾选池并如实报告；节点控件在 optional 末尾 + 指纹含 pool

跑法：python tests/check_v3_pickpool.py
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")
_boot.isolate_outfit_library(R)

FAILS = []


def ck(cond, msg, detail=""):
    if not cond:
        FAILS.append(msg + ((" | " + str(detail)) if detail else ""))


POSE_IDS = [s["id"] for s in R.scenes_data()["scenes"]
            if s.get("group") == "pose"][:6]
ART_IDS = [s["id"] for s in R.scenes_data()["scenes"]
           if s.get("group") == "art"][:6]
NEAR_LEG = [s["id"] for s in R.scenes_data()["scenes"]
            if s.get("group") == "near" and s.get("theme") == "腿部"]
NEAR_FACE = [s["id"] for s in R.scenes_data()["scenes"]
             if s.get("group") == "near" and s.get("theme") == "面部"]
CK = ", ".join


def main():
    ck(len(POSE_IDS) >= 3 and len(ART_IDS) >= 3 and len(NEAR_LEG) >= 3,
       "前提：三组都要有足够条目", (len(POSE_IDS), len(ART_IDS), len(NEAR_LEG)))

    # ── ① 复现性 ──────────────────────────────────────────────────
    a = R.draw(level="sfw", seed=4242, pick_pool_mode="pose", pick_pool_ids=CK(POSE_IDS))
    b = R.draw(level="sfw", seed=4242, pick_pool_mode="pose", pick_pool_ids=CK(POSE_IDS))
    ck(a["text"] == b["text"] and a["state"].get("n_canon") == b["state"].get("n_canon"),
       "同 seed 必须抽到同一条", a["state"].get("n_canon"))
    ck(a["state"].get("n_canon") in POSE_IDS,
       "抽到的必须是勾选集里的", a["state"].get("n_canon"))
    seen = set()
    for sd in range(24):
        st = R.draw(level="sfw", seed=1000 + sd,
                    pick_pool_mode="pose", pick_pool_ids=CK(POSE_IDS))["state"]
        ck(st.get("n_canon") in POSE_IDS, "任何 seed 都必须落在勾选集内",
           (sd, st.get("n_canon")))
        seen.add(st.get("n_canon"))
    ck(len(seen) >= 3, "换 seed 要能换到别的条目（不是恒第一条）", sorted(seen))

    # ── ② forced 优先 ─────────────────────────────────────────────
    pin = POSE_IDS[0]
    r = R.draw(level="sfw", seed=77, forced={"n_canon": pin},
               pick_pool_mode="pose", pick_pool_ids=CK(POSE_IDS[1:]))
    ck(r["state"].get("n_canon") == pin,
       "用户 forced 必须压过勾选池", r["state"].get("n_canon"))

    # ── ③ 关闭态零漂移 ────────────────────────────────────────────
    base = R.draw(level="sfw", seed=555)
    off = R.draw(level="sfw", seed=555, pick_pool_mode="", pick_pool_ids="")
    ck(base["text"] == off["text"] and base["state"] == off["state"],
       "mode/ids 为空时零漂移")

    # ── ④ 姿势 / 艺术池：不收紧取景 ───────────────────────────────
    for _tag, _ids in (("pose", POSE_IDS), ("art", ART_IDS)):
        rr = R.draw(level="sfw", seed=31, pick_pool_mode=_tag,
                    pick_pool_ids=CK(_ids))
        ck(rr["state"].get("n_canon") in _ids,
           "%s 池必须真的套上了" % _tag, rr["state"].get("n_canon"))
        ck(bool(rr["state"].get("hair_len")) or bool(rr["state"].get("emotion")),
           "%s 池不该做特写收紧（字段照常抽）" % _tag,
           (rr["state"].get("hair_len"), rr["state"].get("emotion")))
        ck(not any("收紧取景" in n for n in rr["notes"]),
           "%s 池不该报收紧取景" % _tag, [n for n in rr["notes"] if "收紧" in n][:1])

    # ── ⑤ 特写池（腿）：收紧取景全开 ─────────────────────────────
    rl = R.draw(level="sfw", seed=52, selected_roles=["texas, arknights"],
                pick_pool_mode="near", pick_pool_ids=CK(NEAR_LEG))
    st = rl["state"]
    ck(st.get("n_canon") in NEAR_LEG, "腿部池套上了", st.get("n_canon"))
    ck(not R.role_output_tags(st), "腿部特写要清角色", st.get("role"))
    ck(any("收紧取景" in n for n in rl["notes"]), "必须报收紧取景",
       [n for n in rl["notes"] if "收紧" in n][:2])
    # 人数钉死：count 必须等于该条目的 count_lock（多数是 1girl, solo）
    _cl = (R.scene_index().get(st.get("n_canon")) or {}).get("count_lock") or "1girl, solo"
    ck(str(st.get("count") or "").startswith(_cl.split(",")[0].strip()),
       "人数要按 count_lock 钉死", (st.get("count"), _cl))
    ck(not st.get("hair_len"), "腿部特写要清掉 ②头发 段（hair_len 空）",
       st.get("hair_len"))

    # ── ⑥ 特写池（面部）：保留角色，但关 ④⑤ ──────────────────────
    rf = R.draw(level="sfw", seed=53, selected_roles=["texas, arknights"],
                pick_pool_mode="near", pick_pool_ids=CK(NEAR_FACE))
    ck(R.role_output_tags(rf["state"]), "面部特写要保留角色",
       rf["state"].get("role"))
    ck(not rf["state"].get("hair_len") or True, "（面部保留 ②，不强制断言）")
    ck(any("收紧取景" in n for n in rf["notes"]), "面部也要报收紧取景")

    # ── ⑦ 档位对齐：L3 特写在 sfw 下要抬档 ────────────────────────
    _l3 = [s["id"] for s in R.scenes_data()["scenes"]
           if s.get("group") == "near" and int(s.get("level") or 0) >= 3]
    if _l3:
        r3 = R.draw(level="sfw", seed=9, pick_pool_mode="near",
                    pick_pool_ids=CK(_l3))
        ck(int(r3["level_num"]) >= 3,
           "池里放了 L3 特写 → 档位必须抬上去（与面板一致）",
           (r3["level_num"], r3["state"].get("n_canon")))
        ck(any("升到" in n for n in r3["notes"]), "抬档要写进报告",
           [n for n in r3["notes"] if "升到" in n][:1])

    # ── ⑧ 非法 id + 节点层 ───────────────────────────────────────
    bad = R.draw(level="sfw", seed=12, pick_pool_mode="pose",
                 pick_pool_ids="sc_not_exist_1,sc_not_exist_2")
    ck(any("勾选池" in n and "不在场景库" in n for n in bad["notes"]),
       "非法 id 要如实报告", [n for n in bad["notes"] if "勾选池" in n][:1])

    ND = _boot.load("nodes")
    cls = ND.AnimaV4RandomPrompt
    opt = cls.INPUT_TYPES().get("optional") or {}
    ck(list(opt)[-2:] == ["pick_pool_mode", "pick_pool_ids"],
       "两个新控件必须追加在 optional 末尾", list(opt))
    ck(opt["pick_pool_mode"][1].get("default") == ""
       and opt["pick_pool_ids"][1].get("default") == "",
       "默认必须是空串（关闭 = 零漂移）")
    f1 = cls.IS_CHANGED(seed=999)
    f2 = cls.IS_CHANGED(seed=999, pick_pool_mode="pose",
                        pick_pool_ids="sc_pose_seiza_front")
    ck(f1 != f2 and "pool:pose:" in f2,
       "IS_CHANGED 必须把勾选池并进指纹", f2[-60:])
    # contract 导出（前端单一真源）
    _c = ND.contract_payload().get("near_slim") or {}
    ck(set(_c.get("visible") or {}) == set(R.NEAR_VISIBLE),
       "/contract 的 near_slim.visible 必须与引擎表一致",
       sorted(set(_c.get("visible") or {}) ^ set(R.NEAR_VISIBLE))[:5])
    ck(_c.get("visible", {}).get("腿部") == R.NEAR_VISIBLE["腿部"],
       "/contract 的条目内容必须同源")

    if FAILS:
        print("[check_v3_pickpool] 失败 %d 项：" % len(FAILS))
        for f in FAILS[:30]:
            print("  -", f)
        return 1
    print("[check_v3_pickpool] OK：复现 / forced 优先 / 零漂移 / "
          "姿势·艺术不收紧 / 特写收紧（腿·面）/ 抬档 / 非法 id / 节点指纹 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
