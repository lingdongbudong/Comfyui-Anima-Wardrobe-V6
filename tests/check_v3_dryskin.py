# -*- coding: utf-8 -*-
"""v6.5.0 皮肤干爽过滤基线（用户 2026-11-07「可以直接选择关闭致水珠词条」）。

钉死八件事：
  ① 词表形态：小写单 tag、无逗号、无重复、5 组齐全
  ② ban_set 语义：空 / all = 全部，指定组 = 该组，未知组忽略
  ③ 基础剔除：顺序不变、非目标词零误伤
  ④ 流泪白名单：含泪/哭泣/泪痕时放行 `glistening skin`，其余照禁
  ⑤ **零漂移**：关闭态 text / tags / cn_summary / state / report.drawn 逐字节一致
  ⑥ 开启态多 seed：目标词一个都不出现
  ⑦ 全通道：extra_body（最易漏的 tail 通道）里手塞全部目标词也被剔除
  ⑧ 开启态 state / 抽取结果不变 —— 证明只在成品输出层删 tag，不吃 rng

跑法：python tests/check_v3_dryskin.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")
# 用户可能正在面板启用服装库（整套闸门会覆盖 13 个服装字段），
# 一律走临时副本隔离，真实状态文件零改动。
_boot.isolate_outfit_library(R)

FAILS = []


def ck(cond, msg, detail=""):
    if not cond:
        FAILS.append(msg + ((" | " + str(detail)) if detail else ""))


def main():
    # ── ① 词表形态 ────────────────────────────────────────────────
    groups = R.SKIN_MOISTURE_GROUPS
    ck(set(groups) == {"skin", "weather", "hair", "cloth", "light"},
       "分组 id 应为 5 个", sorted(groups))
    all_tags = []
    for gid, (zh, tags) in groups.items():
        ck(bool(zh), "分组缺中文名", gid)
        ck(bool(tags), "分组词表为空", gid)
        for t in tags:
            ck(t == t.strip().lower(), "词条必须小写且无首尾空格", t)
            ck("," not in t, "词条必须是 collect() 拍平后的单 tag（不能带逗号）", t)
            all_tags.append(t)
    ck(len(all_tags) == len(set(all_tags)), "词表内有重复词条")
    # 星点袜已被 40 张真机对照排除，绝不能混进来
    ck(not any("glittery" in t or "pantyhose" in t for t in all_tags),
       "词表不应包含星点袜/珠光连裤袜（只作用于袜面，不影响皮肤）")
    # v6.5.1：面板明细要常驻显示中文，映射必须覆盖每一条、且与词表同序
    ck(set(R.SKIN_MOISTURE_ZH) == set(all_tags),
       "中文名映射必须覆盖每一条英文 tag",
       sorted(set(all_tags) - set(R.SKIN_MOISTURE_ZH)))
    for gid, (_gzh, _gtags) in groups.items():
        it = R.skin_moisture_items(gid)
        ck([en for en, _ in it] == list(_gtags),
           "明细顺序必须与词表一致", gid)
        ck(all(z and z != en for en, z in it),
           "每条词条都要有中文名（不能退回英文）", gid)

    # ── ② ban_set 语义 ────────────────────────────────────────────
    full = set(all_tags)
    ck(R.skin_moisture_ban_set("") == full, "空 groups 应等于全部组")
    ck(R.skin_moisture_ban_set(None) == full, "None groups 应等于全部组")
    ck(R.skin_moisture_ban_set("all") == full, "all 应等于全部组")
    skin_only = R.skin_moisture_ban_set("skin")
    ck(skin_only == set(groups["skin"][1]), "指定组只取该组", skin_only)
    ck("rain" not in skin_only, "指定 skin 组不应带上天气组的 rain")
    ck(R.skin_moisture_ban_set("skin,not_exist") == skin_only,
       "未知组应被静默忽略")
    # ── v6.5.2 逐条模式：only_tags 优先于 groups，空值回落组语义 ──
    ck(R.skin_moisture_ban_set("", "all") == full,
       "only_tags=all 应等于全部词条")
    ck(R.skin_moisture_ban_set("", "none") == set(),
       "only_tags=none 应为显式空集（不能回落组语义）")
    ck(R.skin_moisture_ban_set("", "glistening skin, wet hair")
       == {"glistening skin", "wet hair"},
       "逐条列表只关选中的两条")
    ck(R.skin_moisture_ban_set("skin", "rain") == {"rain"},
       "逐条模式必须优先于 groups")
    ck(R.skin_moisture_ban_set("", "not_a_real_tag") == set(),
       "词表外的 tag 必须被静默忽略，不能误伤任意标签")
    ck(R.skin_moisture_ban_set("skin") == skin_only,
       "空 only_tags 必须回落组语义（老工作流零回归）")

    # ── ③ 基础剔除 ────────────────────────────────────────────────
    sample = ["1girl", "solo", "glistening skin", "wet hair",
              "rain", "caustics", "sweat", "wet clothes",
              "silver hair", "micro bikini", "white thighhighs"]
    out, removed = R.strip_skin_moisture(sample)
    ck(out == ["1girl", "solo", "silver hair", "micro bikini",
               "white thighhighs"], "基础剔除结果不对（顺序或误伤）", out)
    ck(set(removed) == {"glistening skin", "wet hair", "rain",
                        "caustics", "sweat", "wet clothes"},
       "被剔除项不对", removed)
    ck(sample == ["1girl", "solo", "glistening skin", "wet hair",
                  "rain", "caustics", "sweat", "wet clothes",
                  "silver hair", "micro bikini", "white thighhighs"],
       "不得原地修改入参列表")
    out2, _ = R.strip_skin_moisture(sample, "skin")
    ck("glistening skin" not in out2 and "rain" in out2 and "wet hair" in out2,
       "分组过滤应只禁选中的组", out2)
    out3, rem3 = R.strip_skin_moisture(sample, "", "rain")
    ck("rain" not in out3 and "glistening skin" in out3 and "wet hair" in out3,
       "逐条模式应只剔除 rain", out3)
    ck(rem3 == ["rain"], "逐条模式被剔除项应只有 rain", rem3)
    out4, _ = R.strip_skin_moisture(sample, "", "none")
    ck(out4 == sample, "only_tags=none 应一条都不剔除", out4)

    # ── ④ 流泪白名单 ──────────────────────────────────────────────
    for marker in ("tears", "crying", "tear streak", "damp cheek",
                   "glistening eyes", "watery eyes", "catchlight"):
        t = ["1girl", marker, "glistening skin", "wet hair", "rain"]
        o, _ = R.strip_skin_moisture(t)
        ck("glistening skin" in o, "流泪时应放行湿润光泽肌", marker)
        ck("wet hair" not in o and "rain" not in o,
           "流泪白名单只能放行 glistening skin", marker)
    o, _ = R.strip_skin_moisture(["1girl", "glistening skin", "wet hair"])
    ck("glistening skin" not in o, "无流泪标记时不得放行")

    # ── ⑤ 零漂移（关闭态一次 rng 都不吃）──────────────────────────
    for s in (1, 7, 42, 2026, 1587454997, 493743497):
        a = R.draw(level="suggestive", seed=s)
        b = R.draw(level="suggestive", seed=s, dry_skin_filter=False,
                   dry_skin_groups="all")
        for k in ("text", "tags", "cn_summary", "state", "level",
                  "level_num", "level_label", "notes", "dropped"):
            ck(a[k] == b[k], "零漂移：关闭态字段变了", (s, k))
        ck(a["report"]["drawn"] == b["report"]["drawn"],
           "零漂移：抽取结果(report.drawn)变了", s)

    # ── ⑥ 开启态多 seed 不出现目标词 ──────────────────────────────
    for lv in ("sfw", "suggestive", "explicit"):
        for i in range(24):
            sd = 30000 + i * 7919
            r = R.draw(level=lv, seed=sd, dry_skin_filter=True)
            tags = [str(t).strip().lower() for t in r["tags"]]
            tears = any(m in tags for m in R.SKIN_TEAR_MARKERS)
            bad = [t for t in tags
                   if t in full and not (tears and t in R.SKIN_TEAR_KEEP)]
            ck(not bad, "开启态仍出现目标词", (lv, sd, bad))
            ck(r["text"] == ", ".join(r["tags"]),
               "text 必须与过滤后的 tags 一致", (lv, sd))

    # ── ⑦ 全通道：extra_body 手塞全部目标词 ───────────────────────
    injected = ", ".join(sorted(full))
    r = R.draw(level="suggestive", seed=999001, extra_body=injected,
               dry_skin_filter=True)
    tags = [str(t).strip().lower() for t in r["tags"]]
    ck(not [t for t in tags if t in full], "extra_body 通道漏过滤",
       [t for t in tags if t in full])
    r0 = R.draw(level="suggestive", seed=999001, extra_body=injected)
    ck(any(t in full for t in [str(x).lower() for x in r0["tags"]]),
       "对照组（不过滤）里目标词应确实存在，否则本用例是假通过")
    ck("干爽过滤" in " ".join(r["report"]["notes"]),
       "过滤生效时报告里应留痕", r["report"]["notes"][-2:])

    # ── ⑧ 开启态不改抽取过程 ──────────────────────────────────────
    for s in (101, 202, 303):
        a = R.draw(level="suggestive", seed=s)
        b = R.draw(level="suggestive", seed=s, dry_skin_filter=True)
        ck(a["state"] == b["state"], "开启态 state 变了（不该碰抽取）", s)
        ck(a["report"]["drawn"] == b["report"]["drawn"],
           "开启态 report.drawn 变了（不该吃 rng）", s)

    # ── ⑨ 节点层端到端（INPUT_TYPES / IS_CHANGED / generate）───────
    # nodes.py 只 import 相对模块，裸 Python 也能加载，不必起 8188。
    ND = _boot.load("nodes")
    cls = ND.AnimaV4RandomPrompt
    ipt = cls.INPUT_TYPES()
    req = ipt["required"]
    opt = ipt.get("optional") or {}
    keys = list(req)
    ck(keys[-2:] == ["role_pick", "scene_pick"],
       "required 末尾必须仍是老控件（新控件不得插进 required）", keys[-4:])
    ck(all(k not in req for k in
           ("dry_skin_filter", "dry_skin_groups", "dry_skin_tags",
            "duo_role_fill")),
       "新控件绝不能放 required（老 API 工作流会整张输出被丢弃）")
    # v6.6.0：双女自动补位追加在干爽三件套之后；v6.7.0 又追加了勾选池两个控件。
    # 断言改成「干爽三件套必须连续留在 optional 里、且顺序不变」——
    # 后面再追加新控件不该让这条红（控件顺序是契约，但"末尾"会一直往后长）。
    _opt = list(opt)
    ck(_opt[:3] == ["dry_skin_filter", "dry_skin_groups", "dry_skin_tags"],
       "干爽三控件必须依次排在最前（且顺序不变）", _opt)
    ck("duo_role_fill" in _opt
       and _opt.index("duo_role_fill") == _opt.index("dry_skin_tags") + 1,
       "duo_role_fill 必须紧跟在干爽三件套之后（控件顺序是契约）", _opt)
    ck(opt["dry_skin_filter"][1].get("default") is False,
       "总开关默认必须 False（零漂移）")
    ck(opt["dry_skin_groups"][1].get("default") == "",
       "分组默认必须空串（= 全部组）")
    ck(opt["dry_skin_tags"][1].get("default") == "",
       "逐条默认必须空串（= 回落组语义，零回归）")
    fp_off = cls.IS_CHANGED(seed=424242, dry_skin_filter=False,
                            dry_skin_groups="")
    fp_on = cls.IS_CHANGED(seed=424242, dry_skin_filter=True,
                           dry_skin_groups="skin")
    ck(fp_off != fp_on and "dry:1:skin" in fp_on,
       "IS_CHANGED 必须把开关状态并进指纹", (fp_off, fp_on))
    fp_tag = cls.IS_CHANGED(seed=424242, dry_skin_filter=True,
                            dry_skin_groups="", dry_skin_tags="rain")
    ck(fp_tag != fp_on and "drytags:rain" in fp_tag,
       "IS_CHANGED 必须把逐条列表并进指纹", fp_tag)
    kw = dict(nsfw_level="suggestive", seed=424242, tag_cap="自动",
              base_prob=0.65, disabled_sections="", lock_fields="",
              force_role="", weights_json="", extra_body=injected)
    off = cls().generate(**kw)[0]
    on = cls().generate(**dict(kw, dry_skin_filter=True))[0]
    ck(any(t.strip().lower() in full for t in off.split(",")),
       "节点层关闭态应保留 extra_body 里的目标词")
    ck(not [t for t in on.split(",") if t.strip().lower() in full],
       "节点层开启态必须剔除全部目标词", on[-160:])
    one = cls().generate(**dict(kw, dry_skin_filter=True,
                                dry_skin_tags="glistening skin"))[0]
    ck("glistening skin" not in one and "wet hair" in one,
       "节点层逐条模式只关指定词条", one[-160:])
    _cg = ND.contract_payload()["dry_skin_groups"]
    ck(_cg[0]["id"] == "skin", "/contract 必须导出分组真源")
    ck(all(len(g["items"]) == len(g["tags"]) for g in _cg),
       "/contract 的 items 必须与 tags 一一对应")
    ck(_cg[0]["items"][0]["zh"] == "湿润光泽肌",
       "/contract 明细必须带中文名")

    # ── ⑩ v6.5.2：draw 逐条模式 + 空值零漂移 ──────────────────────
    r1 = R.draw(level="suggestive", seed=99001, extra_body=injected,
                dry_skin_filter=True, dry_skin_tags="glistening skin")
    t1 = [str(t).strip().lower() for t in r1["tags"]]
    ck("glistening skin" not in t1,
       "draw 逐条模式应剔除指定词条")
    ck(sum(1 for t in t1 if t in full and t != "glistening skin") >= 5,
       "draw 逐条模式不应误删其它湿润词条")
    a0 = R.draw(level="suggestive", seed=777)
    b0 = R.draw(level="suggestive", seed=777, dry_skin_tags="")
    ck(a0["text"] == b0["text"] and a0["state"] == b0["state"],
       "dry_skin_tags 空串必须零漂移（回落组语义）")

    if FAILS:
        print("[check_v3_dryskin] 失败 %d 项：" % len(FAILS))
        for f in FAILS[:30]:
            print("  -", f)
        return 1
    print("[check_v3_dryskin] OK：词表 / 分组 / 白名单 / 零漂移 / "
          "多 seed 过滤 / 全通道 / 抽取不变 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
