# -*- coding: utf-8 -*-
"""三层权重语义 + 健壮性基线。

权重是本插件相对源脚本最大的增强（源脚本只有一个写死的
`Math.random()<0.65`），所以它的语义必须有回归钉住：

  ① 字段权重 0        → 该字段永不出现
  ② 字段权重 3.0（上限）→ 该字段必定出现
  ③ 选项权重 0        → 该选项永不出现
  ④ 选项权重把其它项归零 + 字段权重 3.0 → 结果唯一确定
  ⑤ 非法 / 空 / 缺字段的 JSON → 一律当默认，不抛异常
  ⑥ 引用不存在的段/字段/选项 → **不报错**，但必须给出告警
     （不报错是为了老工作流能跑；给告警是为了用户知道权重没生效）
  ⑦ 段权重 0          → 整段关闭
  ⑧ appearance_follow 关闭后，角色不再覆盖发色/瞳色权重

跑法：python tests/check_v3_weights.py
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")
_boot.isolate_outfit_library(R)   # v5.10.0：服装随机基线不受用户启用态影响

FAILS = []


def ck(name, cond, note=""):
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [!!] {name}  {note}")
        FAILS.append(name)


def main():
    print("── ① 字段权重 0 = 永不出现 ──")
    w = R.parse_weights('{"fields":{"outfit":0,"place":0,"hair_color":0}}')
    bad = []
    for s in range(30):
        st = R.draw(level="suggestive", seed=s, weights=w)["state"]
        for k in ("outfit", "place", "hair_color"):
            if st.get(k):
                bad.append((k, st[k]))
    ck("字段权重 0 关得掉", not bad, str(bad[:3]))

    print("── ② 字段权重 3.0 = 必定出现 ──")
    w = R.parse_weights('{"fields":{"place":3.0}}')
    miss = [s for s in range(30)
            if not R.draw(level="sfw", seed=s, weights=w)["state"].get("place")]
    ck("字段权重 3.0 必现", not miss, f"缺 {len(miss)} 次")

    print("── ③ + ④ 选项权重：唯一确定 ──")
    all_colors = [en for _zh, en in
                  R.field_by_key("hair_color")[1]["opts"]]
    opt = {c: 0 for c in all_colors}
    opt["white hair"] = 1
    w = R.parse_weights(
        '{"fields":{"hair_color":3.0},"options":{"hair_color":'
        + _dumps(opt) + '}}')
    # ⚠️ 必须关掉外貌对齐再测。
    #    v3.2.0 起角色**必定**会被抽到（角色权重已取消 + 未选则从角色库
    #    随机抽 1 位），而外貌对齐会把发色改成那个角色的官方发色 ——
    #    那样测的就不是选项权重了。
    #    这条用例以前是靠 `"role": 0` 把角色关掉来隔离变量的，
    #    角色权重取消后那个办法失效，改用 appearance_follow=False。
    got = {R.draw(level="sfw", seed=s, weights=w,
                  appearance_follow=False)["state"].get("hair_color")
           for s in range(25)}
    ck("其余选项归零 → 结果唯一（关闭外貌对齐）", got == {"white hair"}, str(got))

    print("── ⑤ 非法输入不炸 ──")
    cases = ["", None, "{bad json", "[1,2,3]", '"字符串"', "{}",
             '{"fields":null}', '{"fields":{"x":"abc"}}',
             '{"options":{"hair_color":{"black hair":"NaN"}}}']
    ok = True
    for c in cases:
        try:
            w = R.parse_weights(c)
            R.draw(level="sfw", seed=1, weights=w)
        except Exception as exc:
            ok = False
            print(f"      {c!r} 抛异常：{exc}")
    ck("空/非法/错类型 JSON 全部当默认", ok)

    # base_prob = 0 / 负数 的兜底。
    # ⚠️ 历史脏数据：v3.1.0 的「采用这条」在那一栏为空时写 Number("") === 0，
    #    而 0 意味着**所有可选字段都不抽**，出图只剩必填项。必须救回默认值。
    for bad in (0, -1, 0.0, None, "abc"):
        st = R.draw(level="sfw", seed=3, base_prob=bad)["state"]
        picked = sum(1 for k, v in st.items()
                     if not str(k).startswith("_") and v)
        if picked < 5:
            ck(f"base_prob={bad!r} 兜底成默认值", False,
               f"只抽到 {picked} 个字段")
            break
    else:
        ck("base_prob 为 0/负数/非法时兜底成默认值（不是不抽）", True)

    print("── ⑥ 引用不存在的项：不报错，但要有告警 ──")
    w = R.parse_weights(
        '{"sections":{"nope":2},"fields":{"alsonope":2},'
        '"options":{"hair_color":{"rainbow hair":2}}}')
    warns = R.validate_weights(w)
    ck("给出 3 条告警", len(warns) == 3, str(warns))
    res = R.draw(level="sfw", seed=5, weights=w)
    ck("非法权重不阻断抽取", bool(res["text"]))
    ck("告警进了报告", any("不存在" in n for n in res["notes"]), str(res["notes"][:3]))

    print("── ⑦ 段权重 0 = 整段关闭 ──")
    w = R.parse_weights('{"sections":{"hair":0,"makeup":0}}')
    bad = []
    for s in range(20):
        st = R.draw(level="sfw", seed=s, weights=w)["state"]
        for k in ("hair_len", "hair_color", "hair_curl", "hair_tie",
                  "hair_bangs", "hair_fx", "hair_acc", "makeup", "nails"):
            if st.get(k):
                bad.append(k)
    ck("段关闭彻底", not bad, str(sorted(set(bad))))

    print("── ⑧ 角色外貌对齐开关 ──")
    all_colors = [en for _zh, en in R.field_by_key("hair_color")[1]["opts"]]
    opt = {c: 0 for c in all_colors}
    opt["white hair"] = 1
    base = ('{"fields":{"hair_color":3.0},"options":{"hair_color":'
            + _dumps(opt) + '}}')
    w = R.parse_weights(base)
    got = {R.draw(level="sfw", seed=s, weights=w, appearance_follow=False,
                  force_role="hakurei reimu, touhou")["state"].get("hair_color")
           for s in range(5)}
    ck("关闭后权重说了算", got == {"white hair"}, str(got))
    got2 = {R.draw(level="sfw", seed=s, weights=w, appearance_follow=True,
                   force_role="hakurei reimu, touhou")["state"].get("hair_color")
            for s in range(5)}
    ck("开启后官方发色覆盖", got2 == {"black hair"}, str(got2))

    print("── ⑨ 权重计数（面板 🎯 徽标的数据源）──")
    st = R.weight_stats(R.parse_weights(
        '{"sections":{"hair":0},"fields":{"outfit":2},'
        '"options":{"hair_color":{"black hair":2,"pink hair":0}}}'))
    ck("段1/字段1/选项2 = 4", st["total"] == 4, str(st))

    print("── ⑩ 锁定字段 × 法典场景预设（v3.8.0 修）──")
    # ⚠️ 修之前：apply_preset 只认档位与权重，**不看 locks** ——
    #    于是"锁住 n_act 让它留给上游"会被预设写回去，用户看到的是
    #    "我锁了它还改"。预设是**自动填充**，必须与其他两条自动路径
    #    （角色外貌对齐、按人数清字段）一样尊重权重/锁定。
    # c8 = depth 口交侧视：n_act=deepthroat, n_cum=x-ray, viewpoint=from side
    c8 = R.canon().get("c8") or {}
    # ⚠️ v5.0.10：这里**显式固定人数**。
    #    本节验的是「预设填充 × 锁定」的语义，不该被随机人数干扰 ——
    #    c8 本身没有 count_lock，人数是 count 字段自己抽的；抽到 `1girl, solo`
    #    时 `deepthroat` 会被人数相容剔除（solo 没有伴侣），断言就会假红。
    #    以前靠 seed=11 恰好抽到 `1girl, 1boy, hetero` 才绿，这本身就脆：
    #    v5.0.10 角色 tag 从「名字 + 作品」两个 token 合并成一个，
    #    标签总数 28→27 跨过密度阈值，重抽轮次变化，同一 seed 的人数跟着变了。
    base = dict(level="hardcore", seed=11,
                forced={"n_canon": "c8", "count": "1girl, 1boy, hetero"})
    st_free = R.draw(**base)["state"]
    ck("不锁定时预设照常填充 n_act",
       bool(c8.get("n_act")) and st_free.get("n_act") == c8.get("n_act"),
       str(st_free.get("n_act")))
    locked = {"n_act", "n_cum"}
    st_lock = R.draw(locks=locked, **base)["state"]
    hit = sorted(k for k in locked if st_lock.get(k))
    ck("锁定的字段不被预设写回", not hit, f"被写回：{hit}")
    ck("未锁定的字段照常填充（n_extra）", bool(st_lock.get("n_extra")))
    ck("预设 id 仍然写入（n_canon）", st_lock.get("n_canon") == "c8",
       str(st_lock.get("n_canon")))

    print("── ⑪ 选项权重 3.0 = 该选项必现（v4.1.1）──")
    # 用户现场（`%_00033_.png` / `%_00034_.png`）：发色、头饰、发长三个选项
    # 都点到 3.0，连抽两张一个都没中。三个环节各丢一次：
    #   ① 字段先被 base_prob 门槛跳过 → 选项权重根本没机会生效；
    #   ② 字段抽到了，但 3.0 在 weighted_pick 里只是个 3:1 的乘数
    #      （15 个选项 ≈ 18%，30 个选项 ≈ 10%）；
    #   ③ 选项抽到了，又被 trim_to_cap 的密度裁剪削掉。
    w = R.parse_weights('{"options":{"hair_color":{"purple hair":3},'
                        '"hair_acc":{"maid headdress":3},'
                        '"hair_len":{"pixie cut":3}}}')
    miss = []
    for s in range(40):
        st = R.draw(level="sfw", seed=s, weights=w, role_pick=False)["state"]
        if (st.get("hair_color") != "purple hair"
                or st.get("hair_acc") != "maid headdress"
                or st.get("hair_len") != "pixie cut"):
            miss.append((s, st.get("hair_color"), st.get("hair_acc"),
                         st.get("hair_len")))
    ck("40 抽三项全中（不跳字段 / 不被稀释 / 不被密度裁剪）",
       not miss, str(miss[:2]))

    res = R.draw(level="sfw", seed=1, weights=w, role_pick=False)
    ck("报告写明「按选项权重 3 强制出现」",
       any("强制出现" in n for n in res["notes"]), str(res["notes"])[:160])

    # 同字段多个 3.0 → 只在这几项里出现（用户表达"这几个都行"）
    w2 = R.parse_weights(
        '{"options":{"hair_color":{"purple hair":3,"white hair":3}}}')
    got = {R.draw(level="sfw", seed=s, weights=w2,
                  role_pick=False)["state"].get("hair_color")
           for s in range(20)}
    ck("同字段多个 3.0 → 只在这几项里出现",
       got and got <= {"purple hair", "white hair"}, str(got))

    # 2.9 不该享有 3.0 的特权（阈值是硬门槛，不是"权重越高越优先"）
    pool = R.field_by_key("hair_color")[1]["opts"]
    ck("2.9 不算必现",
       not R.forced_option_pool(
           pool, "hair_color",
           R.parse_weights('{"options":{"hair_color":{"purple hair":2.9}}}')))
    ck("3.0 算必现",
       len(R.forced_option_pool(
           pool, "hair_color",
           R.parse_weights('{"options":{"hair_color":{"purple hair":3}}}'))) == 1)

    # ⚠️ v4.3.0（用户口径 22:12：**不放行，自动调整档位**）——
    #    档位闸门仍是唯一准入点（`eligible_options`），但点名了越档词时，
    #    整张图的档位会被抬到该词的档位再抽；不再是"点了 3.0 却永远不出现"。
    ck("blindfold 确实是 L3 词", R.option_level("n_theme", "blindfold") >= 3,
       str(R.option_level("n_theme", "blindfold")))
    w4 = R.parse_weights('{"options":{"n_theme":{"blindfold":3}}}')
    r4 = R.draw(level="sfw", seed=0, weights=w4, role_pick=False)
    ck("越档 3.0 → 档位自动抬到词条档位，且词条出现",
       r4["level_num"] >= R.option_level("n_theme", "blindfold")
       and "blindfold" in r4["text"],
       f"L{r4['level_num']} / {r4['text'][:80]}")

    # ⚠️ 口径（2026-10-29 用户明确）：这套「选项必现」**只在勾了「不指定角色」时生效**。
    #    有角色时人物特征归角色官方设定（外貌/发型对齐），权重不该硬塞配件。
    hit_role = 0
    for s in range(20):
        st = R.draw(level="sfw", seed=s, weights=w, role_pick=True,
                    force_role="hakurei reimu, touhou")["state"]
        if st.get("hair_acc") == "maid headdress":
            hit_role += 1
    ck("有角色时不强制（3.0 不能给官方角色硬塞配件）", hit_role < 20,
       f"{hit_role}/20 次仍是 maid headdress")

    print("── ⑫ 非人物特征：勾不勾「不指定角色」都生效（v4.2.0）──")
    # 用户现场（`%_00045_/_00046_.png`）：情绪调到 3.0，正文里却是 `smile`。
    # 根因**不是**权重，而是套用的场景预设把 emotion 覆盖了
    # （`sc_pose_wariza` 写死 smile）—— 见下面 ⑬ 的档位规则。
    w_expr = R.parse_weights(
        '{"options":{"emotion":{"crying":3},"nails":{"nail polish":3}}}')
    for _rp in (False, True):
        miss = [s for s in range(20)
                if R.draw(level="sfw", seed=s, weights=w_expr, role_pick=_rp,
                          force_role="" if not _rp else "hakurei reimu, touhou")
                ["state"].get("emotion") != "crying"]
        ck(f"role_pick={_rp}：20 抽情绪全为 crying", not miss, f"缺 {len(miss)}")

    # 人物特征字段反过来：有角色时不强制（官方设定优先）
    ck("人物特征字段仍在有角色时让路（不硬塞头饰）",
       "hair_acc" in R.CHAR_APPEAR_FIELDS
       and "hair_color" in R.CHAR_APPEAR_FIELDS
       and "emotion" not in R.CHAR_APPEAR_FIELDS)

    print("── ⑬ 场景档位决定「表情谁说了算」（v4.2.0）──")
    w_lo = R.parse_weights(
        '{"fields":{"n_canon":3},"options":'
        '{"emotion":{"crying":3},"n_canon":{"sc_pose_wariza":3}}}')
    res = R.draw(level="sfw", seed=11, weights=w_lo, role_pick=False)
    ck("L0 场景：权重 3.0 盖过场景表情（用户点哭泣就该哭）",
       res["state"].get("emotion") == "crying", str(res["state"].get("emotion")))
    ck("L0 场景：报告写「强制出现」",
       any("强制出现" in n for n in res["notes"]), str(res["notes"])[-2:])

    w_hi = R.parse_weights(
        '{"fields":{"n_canon":3},"options":'
        '{"emotion":{"crying":3},"n_canon":{"sc_ccc_missionary":3}}}')
    res = R.draw(level="hardcore", seed=11, weights=w_hi, role_pick=False)
    ck("L3 场景：场景表情优先（crying 让路）",
       res["state"].get("emotion") != "crying" and "crying" not in res["text"],
       str(res["state"].get("emotion")))
    ck("L3 场景：报告如实说明「已让路」（不能骗人）",
       any("让路" in n for n in res["notes"]), str(res["notes"])[-1:])
    ck("L3 场景仍会报其它字段的强制项",
       R.draw(level="hardcore", seed=11, role_pick=False,
              force_role="hakurei reimu, touhou", selected_roles=[],
              weights=w_hi)["state"].get("n_canon") == "sc_ccc_missionary")

    print("── ⑭ 性行为点名：人数下限 + 命中率（v4.2.1）──")
    # 用户口径："未使用场景搭配时，权重必须生效，例如性行为内的权重，即使是 L3~L4"。
    # 修复前的实测：n_act=missionary 1/12、n_pos=boy on top 0/12 ——
    #   ① 人数抽到 solo → 27 个需要伴侣的动作被"人数相容"一次剔光；
    #   ② 抽完后 `ensure_level_floor` 又把人数改成 hetero（L4 保底换动作）；
    #   ③ 自动场景的 `count_lock` / `闭麦·n_pos` 再盖一次。
    for lv, f, opt in (("hardcore", "n_act", "missionary"),
                       ("extreme", "n_act", "mating press"),
                       ("hardcore", "n_cum", "creampie"),
                       ("extreme", "n_cum", "bukkake"),
                       ("hardcore", "n_pos", "boy on top")):
        w = R.parse_weights(
            '{"options":{"%s":{"%s":3}}}' % (f, opt))
        miss = [s for s in range(20)
                if R.draw(level=lv, seed=2000 + s, weights=w,
                          role_pick=False)["state"].get(f) != opt]
        ck(f"{lv} {f}={opt}：20/20", not miss, f"缺 {len(miss)}")

    # bukkake 属于 GROUP_ONLY：人数必须抬到群交，否则会被"人数不符"清掉
    w = R.parse_weights('{"options":{"n_cum":{"bukkake":3}}}')
    st = R.draw(level="extreme", seed=2000, weights=w, role_pick=False)["state"]
    ck("点名 bukkake → 人数抬到群交", R.count_is_group(st.get("count")),
       str(st.get("count")))
    # masturbation 在 solo 白名单里 → 人数下限不会因它而抬；
    # （hardcore 档下保底仍可能自行补人数抬档 —— 那是"高等级保底"的既有职责，
    #   但被点名的 masturbation 本身必须保住，不得被换掉）
    w2 = R.parse_weights('{"options":{"n_act":{"masturbation":3}}}')
    st2 = R.draw(level="hardcore", seed=2000, weights=w2, role_pick=False)["state"]
    ck("点名 masturbation → 点名项保住（不被保底/人数逻辑换掉）",
       st2.get("n_act") == "masturbation", str(st2.get("n_act")))
    # 人数自己也被点名时：人数优先（两条显式指令冲突，报告要说清楚）
    w3 = R.parse_weights(
        '{"options":{"count":{"1girl, solo":3},"n_act":{"missionary":3}}}')
    res = R.draw(level="hardcore", seed=2000, weights=w3, role_pick=False)
    ck("人数与性行为同时点名 → 人数优先且报告说明",
       res["state"].get("count") == "1girl, solo"
       and any("矛盾" in n for n in res["notes"]),
       str(res["state"].get("count")) + " / " + str(res["notes"])[-1:])

    # ⑭之二 角色库点选 + 点名性行为（v4.2.3，用户现场三连）：
    # 选雷电将军 → `apply_selected_roles` 按人数把 count 改写成 `1girl, solo`，
    # 把循环里抬起来的 hetero 又压回去 → 点名的 anilingus 被"人数相容"清掉。
    w_a = R.parse_weights('{"options":{"n_act":{"anilingus":3}}}')
    miss = [s for s in range(12)
            if R.draw(level="hardcore", seed=2120244403 + s, weights=w_a,
                      role_pick=True,
                      selected_roles=["raiden shogun, genshin impact"])
            ["state"].get("n_act") != "anilingus"]
    ck("选角色 + anilingus=3：12/12（角色人数改写不再压垮点名项）", not miss,
       f"缺 {len(miss)}")
    r = R.draw(level="hardcore", seed=1, weights=w_a, role_pick=True,
               selected_roles=["raiden shogun, genshin impact",
                               "ganyu, genshin impact"])
    ck("双女 2 角色 + 异性行为点名 → 不硬抬人数（保持双女，动作让路）",
       r["state"].get("count") == "2girls, yuri"
       and r["state"].get("n_act") != "anilingus",
       str(r["state"].get("count")) + " / " + str(r["state"].get("n_act")))

    print("── ⑮ 穿戴式道具 ↔ 人数（v4.2.2）──")
    # 用户口径："双女性行为使用穿戴式道具，一男一女不使用穿戴式道具"。
    # ① 双女 + 正戏（体位有内容）→ strap-on 必现
    w_y = R.parse_weights(
        '{"options":{"count":{"2girls, yuri":3},"n_pos":{"spread legs":3}}}')
    miss = [s for s in range(10)
            if R.draw(level="hardcore", seed=3000 + s, weights=w_y,
                      role_pick=False)["state"].get("n_theme") not in
            ("strap-on", "sex toys", "dildo")]
    ck("双女正戏 10 抽全是玩具类（strap-on 为主）", not miss, str(miss))
    # ② 一男一女：随机抽到玩具类也会被清（用字段权重 3 让 n_theme 必抽，
    #    20 抽里总会随机到玩具类 —— 清空规则必须每次都接住）
    w_h = R.parse_weights(
        '{"options":{"count":{"1girl, 1boy, hetero":3}},"fields":{"n_theme":3}}')
    toys = {"strap-on", "sex toys", "dildo"}
    bad = [s for s in range(20)
           if toys & set(R.draw(level="hardcore", seed=3000 + s, weights=w_h,
                                role_pick=False)["tags"])]
    ck("一男一女：20 抽无玩具类（随机抽到也会被清）", not bad, str(bad))
    # 纯函数层：一男一女 + 玩具 → 清空；双女正戏 → 补 strap-on
    st_toy = {"count": "1girl, 1boy, hetero", "n_theme": "sex toys",
              "n_act": "missionary"}
    notes = R.apply_count_toy_rule(st_toy, 3, R.empty_weights())
    ck("纯函数：一男一女 + 玩具 → 清空", st_toy.get("n_theme") == "" and notes,
       str(st_toy.get("n_theme")))
    st_y = {"count": "2girls, yuri", "n_pos": "spread legs"}
    notes = R.apply_count_toy_rule(st_y, 3, R.empty_weights())
    ck("纯函数：双女正戏 → 补 strap-on", st_y.get("n_theme") == "strap-on",
       str(st_y.get("n_theme")))
    # ③ 用户点名优先（双女 + 点名 sex toys → 保留点名的那个）
    w_p = R.parse_weights(
        '{"options":{"count":{"2girls, yuri":3},"n_theme":{"sex toys":3}}}')
    r = R.draw(level="hardcore", seed=3000, weights=w_p, role_pick=False)
    ck("双女 + 点名 sex toys → 点名优先", r["state"].get("n_theme") == "sex toys",
       str(r["state"].get("n_theme")))
    # ④ solo 不干预（dildo riding 本来就是 solo 动作）
    w_s = R.parse_weights(
        '{"options":{"count":{"1girl, solo":3},"n_theme":{"dildo":3}}}')
    r = R.draw(level="hardcore", seed=3000, weights=w_s, role_pick=False)
    ck("solo 不干预（dildo 保留）", r["state"].get("n_theme") == "dildo",
       str(r["state"].get("n_theme")))
    # ⑤ 档位闸门优先：strap-on 是 L3 词，L2 的双女不会硬塞
    ck("strap-on 确实是 L3 词", R.option_level("n_theme", "strap-on") >= 3)
    r = R.draw(level="explicit", seed=3000, weights=w_y, role_pick=False)
    ck("L2 双女不出 strap-on（档位闸门优先）",
       "strap-on" not in r["text"], r["text"][:100])

    print("── ⑯ 预抽字段的 3.0 也必现（v4.3.0）──")
    # 六个字段走的是**预抽**路径（_pre_place / _pre_outfit / _pre_hands），
    # 主循环看到 `key in pre` 就直接采用 —— 以前预抽没有 forced 收窄，
    # 这六个字段的 3.0 全部作废（实测 place 8/15、outfit 6/15、pose_risque 9/15）。
    for _k, _opt in (("place", "bedroom"),
                     ("outfit", "school uniform"),
                     ("top", "t-shirt"),
                     ("bottom", "jeans"),
                     ("arms_hands", "hand on own hip"),
                     ("pose_risque", "bent over")):
        _w = R.empty_weights()
        _w["options"][_k] = {_opt: 3.0}
        _miss = [s for s in range(8)
                 if R.draw(level="extreme", seed=4000 + s, weights=_w,
                           role_pick=False)["state"].get(_k) != _opt]
        ck(f"预抽·{_k} 3.0 必现（8 抽）", not _miss, f"缺 {len(_miss)} 次")

    print("── ⑰ 场景抽取：开关决定「有没有」，3.0 才排除（v5.0.1 起）──")
    # v5.0.1（用户 2026-11-01）：SCENE_APPEAR_P 概率抽签已废，「有没有场景」
    # 完全由 `scene_pick` 决定。原来那句「默认权重 20 抽里仍有场景」作废 ——
    # 现在不勾就是 0 条，与权重无关。
    _sc = sum(1 for s in range(20)
              if R.draw(level="hardcore", seed=5000 + s,
                        role_pick=False)["state"].get("n_canon"))
    ck("不勾「随机场景」：默认权重也是 0 场景", _sc == 0, f"场景 {_sc}/20")

    _w = R.empty_weights()
    _sc1 = sum(1 for s in range(20)
               if R.draw(level="hardcore", seed=5000 + s, weights=_w,
                         role_pick=False, scene_pick=True)["state"].get("n_canon"))
    ck("勾上「随机场景」+ 默认权重：20 抽全有场景", _sc1 == 20, f"场景 {_sc1}/20")

    _w = R.empty_weights()
    _w["options"]["n_act"] = {"missionary": 2.0}
    _sc2 = sum(1 for s in range(20)
               if R.draw(level="hardcore", seed=5000 + s, weights=_w,
                         role_pick=False, scene_pick=True)["state"].get("n_canon"))
    ck("中间值 2.0：不排除场景（用户口径：只有 3.0 触发）", _sc2 > 0,
       f"场景 {_sc2}/20")
    # ⚠️ 这一组必须带 scene_pick=True —— 不勾的路径本来就是 0 条场景，
    #    那样测出来的「0」是开关造成的，不是 3.0 排除造成的（假绿）。
    for _k, _opt in (("pose_base", "standing"), ("n_act", "missionary"),
                     ("n_theme", "collar and leash")):
        _w = R.empty_weights()
        _w["options"][_k] = {_opt: 3.0}
        _sc3 = sum(1 for s in range(10)
                   if R.draw(level="hardcore", seed=5000 + s, weights=_w,
                             role_pick=False, scene_pick=True)["state"].get("n_canon"))
        ck(f"{_k} 3.0 → 不自动套场景（即使勾了随机场景）", _sc3 == 0,
           f"场景 {_sc3}/10")

    print("── ⑱ 选项权重 0 = 永不出现（含场景写入路径，v4.3.0）──")
    _w = R.empty_weights()
    _w["options"]["n_act"] = {"missionary": 0.0}
    _hit = [s for s in range(30)
            if "missionary" in
            (R.draw(level="hardcore", seed=6000 + s, weights=_w,
                    role_pick=False)["state"].get("n_act") or "")]
    ck("n_act missionary=0 时 30 抽零出现（场景写死也拦）", not _hit, str(_hit[:5]))

    print("── ⑲ 两个都调过权重的手部项：权重高者赢（v4.3.0）──")
    # ⚠️ 必须挑**真在手部槽位里**的选项（`skirt pull`）。`bent over` 不在
    #    HAND_SLOT，它与叉腰本来就能共存，拿它测等于测了个假冲突。
    _w = R.empty_weights()
    _w["options"]["arms_hands"] = {"hand on own hip": 3.0}
    _w["options"]["pose_risque"] = {"skirt pull": 2.0}
    _arm = _pr = 0
    for s in range(10):
        _st = R.draw(level="hardcore", seed=7000 + s, weights=_w,
                     role_pick=False)["state"]
        _arm += 1 if _st.get("arms_hands") == "hand on own hip" else 0
        _pr += 1 if _st.get("pose_risque") == "skirt pull" else 0
    ck("arms_hands 3.0 压过 pose_risque 2.0", _arm == 10 and _pr == 0,
       f"arm={_arm}/10 pr={_pr}/10")
    _w = R.empty_weights()
    _w["options"]["arms_hands"] = {"hand on own hip": 2.0}
    _w["options"]["pose_risque"] = {"skirt pull": 3.0}
    _arm = _pr = 0
    for s in range(10):
        _st = R.draw(level="hardcore", seed=7000 + s, weights=_w,
                     role_pick=False)["state"]
        _arm += 1 if _st.get("arms_hands") == "hand on own hip" else 0
        _pr += 1 if _st.get("pose_risque") == "skirt pull" else 0
    ck("反向：pose_risque 3.0 压过 arms_hands 2.0", _pr == 10 and _arm == 0,
       f"arm={_arm}/10 pr={_pr}/10")

    print("── ⑳ 越档词 3.0 → 档位自动上调，不是放行（v4.3.0）──")
    _w = R.empty_weights()
    _w["options"]["n_act"] = {"double penetration": 3.0}
    _need = R.option_level("n_act", "double penetration")
    _r = R.draw(level="explicit", seed=8000, weights=_w, role_pick=False)
    ck("explicit + L4 词 → level_num 抬到词条档位",
       _r["level_num"] == _need, f"level={_r['level_num']} need={_need}")
    ck("抬档后该词出现在正文", "double penetration" in _r["text"],
       _r["text"][:120])
    ck("抬档有报告说明", any("自动上调" in n for n in _r["notes"]),
       str(_r["notes"])[:150])
    # 只升不降：extreme 档点 L1 词不该把档位降下来
    _w = R.empty_weights()
    _w["options"]["pose_base"] = {"standing": 3.0}
    _r = R.draw(level="extreme", seed=8000, weights=_w, role_pick=False)
    ck("只升不降（extreme 仍 extreme）", _r["level_num"] == 4,
       str(_r["level_num"]))

    print("── ㉑ 复杂词条的自然语言补充（v4.4.0）──")
    # 用户口径（2026-10-29 23:58）：复杂动作只给 tag 串时模型画不出来，
    # 结尾补一句英文自然语言就能画对。hint 只在该词条被点名 3.0 且抽中时追加。
    ck("term_hints 已加载", bool(R.term_hints().get("n_theme")),
       str(list(R.term_hints().keys())))
    _w = R.empty_weights()
    _w["options"]["n_theme"] = {"gag ball": 3.0}
    _r = R.draw(level="extreme", seed=880037, weights=_w, role_pick=False)
    ck("点名 3.0 → 正文含 hint 句", "ball gag is strapped" in _r["text"],
       _r["text"][-120:])
    ck("hint 不被逗号拆碎（整句保留）",
       "ball gag is strapped inside her open mouth." in _r["text"],
       _r["text"][-160:])
    ck("hint 不触发 rogue / 越档",
       not _r["report"].get("rogue") and not _r["report"].get("over_level"),
       str(_r["report"].get("rogue")) + str(_r["report"].get("over_fields")))
    _r2 = R.draw(level="extreme", seed=880037, weights=R.empty_weights(),
                 role_pick=False)
    ck("没点名时不加 hint（零漂移）", "ball gag" not in _r2["text"],
       _r2["text"][-100:])
    _w = R.empty_weights()
    _w["options"]["n_act"] = {"irrumatio": 3.0}
    _r3 = R.draw(level="hardcore", seed=880038, weights=_w, role_pick=False)
    ck("n_act 的 hint 同样注入",
       "holds her head with both hands" in _r3["text"], _r3["text"][-160:])

    print("── ㉒ 穿戴式 / 扶她 = 双女交互动作（v4.4.1）──")
    # 用户口径（10-30 00:27）：这两条原本只描述"她戴着/她是扶她"，solo 下只能画出
    # "单人扶着腰站着戴道具"。改成双人交互：人数抬到双女 + hint 描述"插入对方"。
    for _t, _frag in (("strap-on", "pushes it into the other woman"),
                      ("futanari", "pushes it into the other woman")):
        _w = R.empty_weights()
        _w["options"]["n_theme"] = {_t: 3.0}
        ck(f"{_t} 的目标人数 = 双女",
           R.forced_sex_count_target(_w) == R.COUNT_YURI,
           str(R.forced_sex_count_target(_w)))
        _r = R.draw(level="extreme", seed=880040, weights=_w, role_pick=False)
        ck(f"{_t} 实际人数被抬到双女",
           R.is_yuri_count(_r["state"].get("count") or ""),
           str(_r["state"].get("count")))
        ck(f"{_t} hint 含交互动作", _frag in _r["text"], _r["text"][-120:])
    # 用户自己点名一男一女 → 人数优先，strap-on 保留（报告里说明矛盾）
    _w = R.parse_weights(
        '{"options":{"count":{"1girl, 1boy, hetero":3.0},'
        '"n_theme":{"strap-on":3.0}}}')
    _r = R.draw(level="extreme", seed=880052, weights=_w, role_pick=False)
    ck("点名一男一女时人数优先（不硬抬双女）",
       "1boy" in (_r["state"].get("count") or ""), str(_r["state"].get("count")))
    ck("点名的 strap-on 仍保留", _r["state"].get("n_theme") == "strap-on",
       str(_r["state"].get("n_theme")))
    # 双女 + strap-on + 正戏：v4.2.2 的补道具规则不该重复动手
    _w = R.parse_weights(
        '{"options":{"count":{"2girls, yuri":3.0},"n_theme":{"strap-on":3.0},'
        '"n_act":{"fingering":3.0}}}')
    _r = R.draw(level="extreme", seed=880053, weights=_w, role_pick=False)
    ck("双女正戏 + 点名 strap-on 不重复补",
       _r["state"].get("n_theme") == "strap-on"
       and R.is_yuri_count(_r["state"].get("count") or ""),
       str(_r["state"].get("n_theme")) + " / " + str(_r["state"].get("count")))

    print("── ㉓ 场景级自然语言 `scene_nl`（v4.4.2）──")
    # 用户口径：场景/特写也补一句自然语言，画面能不能更准。
    # 键名是 `scene_nl` 而不是 `nl` —— 词库已有 `nl` 字段，同名会被当真实字段。
    ck("scene_preset 透传 scene_nl",
       "scene_nl" in R.scene_preset("sc_morning_window"),
       str(list(R.scene_preset("sc_morning_window").keys()))[-80:])
    _w = R.parse_weights('{"options":{"n_canon":{"sc_morning_window":3.0}}}')
    _r = R.draw(level="sfw", seed=770001, weights=_w, role_pick=False)
    ck("L0 场景的 scene_nl 进了正文",
       "wakes up in the morning sunlight" in _r["text"], _r["text"][-120:])
    ck("长句不被 repair 当超档标签剔掉",
       not any("超 0 档" in x for x in (_r["report"].get("repaired") or [])),
       str(_r["report"].get("repaired"))[:150])
    _w2 = R.parse_weights('{"options":{"n_canon":{"sc_car_ride":3.0}}}')
    _r2 = R.draw(level="sfw", seed=770001, weights=_w2, role_pick=False)
    ck("没写 scene_nl 的场景一个字不加",
       "wakes up in the morning sunlight" not in _r2["text"],
       _r2["text"][-100:])

    print("── ㉔ 点名正戏动作/主题 → 闭麦「动态动作」「情绪」（v4.4.6）──")
    for fk, term in (("n_act", "irrumatio"), ("n_theme", "gag ball")):
        _w = R.empty_weights()
        _w["options"][fk] = {term: 3.0}
        _bad = 0
        for s in range(12):
            _st = R.draw(level="extreme", seed=880300 + s, weights=_w,
                         role_pick=False)["state"]
            if (_st.get("action") or _st.get("emotion")):
                _bad += 1
        ck("点名 %s → 不再随机抽 action / emotion" % term, _bad == 0,
           "%d/12 仍有" % _bad)
    # 反向：不点名时一切照旧（零漂移的保底）
    _n = 0
    for s in range(12):
        _st = R.draw(level="extreme", seed=880400 + s,
                        weights=R.empty_weights(), role_pick=False)["state"]
        if (_st.get("action") or _st.get("emotion")):
            _n += 1
    ck("不点名时 action / emotion 照常抽取（零漂移）", _n >= 10,
       "%d/12" % _n)

    print()
    if FAILS:
        print(f"[check_v3_weights] 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("[check_v3_weights] 通过：24 组语义全部符合预期")
    return 0


def _dumps(d):
    import json
    return json.dumps(d, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
