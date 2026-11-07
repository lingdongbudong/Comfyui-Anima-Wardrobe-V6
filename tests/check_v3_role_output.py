# -*- coding: utf-8 -*-
"""正文角色 tag 基线（v5.0.11 重写，口径以 Anima 官方文档为准）。

官方要求（circlestone-labs/Anima 模型卡 + Anima-2.9B 作者说明）：
  · 「Use lowercase for tags, and **spaces instead of underscores**.
     Score tags are the only tags that use underscores.」
  · 「**Don't use underscore except for score tags.**」
  · 顺序 `[quality/meta/year/safety] [1girl] [character] [series] [artist] [general]`
  · 「Characters should (think "**must**") be **follow by their series/copyrights**」
  · 官方示例：`1girl, oomuro sakurako, yuru yuri, @nnn yryr, smile, ...`

所以正文里角色与作品是**两个独立 tag**（`texas, arknights`），
不是 danbooru 原始的 `texas_(arknights)`（下划线被官方明确禁止）。

守八件事：
  ① 映射表可用（schema @2、条数够）；
  ② 标准写法 `texas, arknights`；
  ③ **零下划线 / 零大写**（官方硬规定）；
  ④ 回退安全（表外 trigger 不抛错；空映射退回旧行为）；
  ⑤ 槽位顺序：count 之后、appearance 之前；
  ⑥ 角色与作品相邻且顺序为「角色 → 作品」；
  ⑦ 形态角色：角色名保留括号（`byleth (female)`），作品另起一个 tag；
  ⑧ 作品名必须真的存在（不能出现「角色, 空」或角色名当作品）。

跑法：`python tests/check_v3_role_output.py`
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v4_rules")

FAILS = []
PLUGIN = os.path.dirname(HERE)
MAP_PATH = os.path.join(PLUGIN, "presets", "char_output_tags.json")
TOTAL = [0]


def ck(name, cond, note=""):
    TOTAL[0] += 1
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [!!] {name}  {note}")
        FAILS.append(name)


def main():
    print("── ① 映射表 ──")
    if not os.path.exists(MAP_PATH):
        ck("映射表存在", False, MAP_PATH)
        print(f"\n失败 {len(FAILS)} / {TOTAL[0]}")
        return 1
    with open(MAP_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    tags = raw.get("tags") or {}
    ck("映射表可读且条数 > 2000", len(tags) > 2000, f"实际 {len(tags)}")
    ck("_meta.schema = @2（Anima 官方写法）",
       (raw.get("_meta") or {}).get("schema") == "anima-v5/char-output-tags@2",
       str((raw.get("_meta") or {}).get("schema")))
    ck("_meta 写明官方 spec",
       "spaces instead of underscores" in str((raw.get("_meta") or {}).get("spec", "")))

    R._CACHE.pop("char_output_tags", None)
    R._CACHE.pop("char_output_tag_set", None)
    m = R.char_output_tag_map()
    ck("引擎读到的条数与文件一致", len(m) == len(tags),
       f"引擎 {len(m)} vs 文件 {len(tags)}")

    print("\n── ② 标准写法 ──")
    hit = [(t, r["out"]) for t, r in tags.items() if t.startswith("texas,")][:1]
    if hit:
        trig, want = hit[0]
        ck(f"`{trig}` → `{want}`", want == "texas, arknights", want)
        ck("role_output_tag 与映射一致",
           R.role_output_tag(trig) == want, R.role_output_tag(trig))
    else:
        ck("库里有 texas", False, "未找到 texas 的 trigger")

    st = R.draw(level="sfw", seed=42, selected_roles=["texas_(arknights)"])
    body = st["tags"]
    ck("正文含 `texas` 独立 tag", "texas" in body, str(body[:6]))
    ck("正文含 `arknights` 独立 tag", "arknights" in body, str(body[:6]))
    if "texas" in body and "arknights" in body:
        ck("作品紧跟角色之后（官方 [character] [series] 顺序）",
           body.index("arknights") == body.index("texas") + 1,
           f"角色@{body.index('texas')} 作品@{body.index('arknights')}")
    else:
        ck("作品紧跟角色之后（官方 [character] [series] 顺序）", False, str(body[:6]))

    print("\n── ③ 官方规范：无下划线 / 无大写 ──")
    all_out = [v["out"] for v in tags.values()]
    bad_us = [o for o in all_out if "_" in o]
    bad_cap = [o for o in all_out if re.search(r"[A-Z]", o)]
    ck("全表零下划线", not bad_us, f"{len(bad_us)} 条：{bad_us[:3]}")
    ck("全表零大写", not bad_cap, f"{len(bad_cap)} 条：{bad_cap[:3]}")
    ck("正文里角色段零下划线",
       not any("_" in t for t in body[:8]), str(body[:8]))

    print("\n── ④ 零漂移（空映射 = 旧行为）──")
    saved = dict(m)
    R._CACHE["char_output_tags"] = {}
    R._CACHE.pop("char_output_tag_set", None)
    try:
        st2 = R.draw(level="sfw", seed=42, selected_roles=["texas_(arknights)"])
        # 旧逻辑：不重名 → 只写角色名，作品名被丢掉（这正是官方说的错法）
        ck("空映射时退回旧行为（只剩裸角色名）",
           "texas" in st2["tags"] and "arknights" not in st2["tags"],
           str(st2["tags"][:6]))
        st3 = R.draw(level="sfw", seed=42, selected_roles=["fubuki_(kancolle)"])
        ck("空映射时重名角色仍带作品（旧逻辑自带）",
           "fubuki" in st3["tags"] and "kantai collection" in st3["tags"],
           str(st3["tags"][:6]))
    finally:
        R._CACHE["char_output_tags"] = saved
        R._CACHE.pop("char_output_tag_set", None)

    print("\n── ⑤ 回退安全 ──")
    got = R.role_output_tag("some unknown trigger, unknown work")
    ck("表外 trigger 不抛错且返回字符串", isinstance(got, str) and bool(got), got)
    ck("空 trigger 返回空串", R.role_output_tag("") == "")

    print("\n── ⑥ 槽位顺序与双人 ──")
    st4 = R.draw(level="sfw", seed=42,
                 selected_roles=["texas_(arknights)", "ganyu_(genshin_impact)"])
    b4 = st4["tags"]
    ck("角色在 count 之后", b4[0] in ("1girl", "2girls", "multiple girls"), b4[0])
    ck("两位角色 → 角色与作品各出现 1 次",
       b4.count("texas") == 1 and b4.count("arknights") == 1
       and b4.count("ganyu") == 1 and b4.count("genshin impact") == 1,
       str(b4[:8]))
    ck("角色段都在前 8 位内（未被挤到尾部）",
       all(t in b4[:8] for t in ("texas", "arknights", "ganyu", "genshin impact")),
       str(b4[:8]))

    print("\n── ⑦ 形态角色 ──")
    form = [(t, r["out"]) for t, r in tags.items()
            if re.search(r"\)\s*,", r.get("out", ""))]
    ck("存在「角色名带括号 + 作品独立」的条目", len(form) > 0, "0 条")
    if form:
        trig, want = form[0]
        ck(f"`{trig}` → `{want}`", "(" in want.split(",")[0], want)
        ck("形态括号在角色段、作品在逗号后",
           want.count(",") == 1 and "(" in want.split(",")[0], want)

    print("\n── ⑧ 作品名完整性 ──")
    empty_work = [o for o in all_out if o.rstrip().endswith(",")]
    ck("没有「角色, 空」的条目", not empty_work, str(empty_work[:3]))
    same_work = [o for o in all_out
                 if o.count(",") == 1
                 and o.split(",")[0].strip() == o.split(",")[1].strip()]
    ck("没有角色名与作品名相同的条目", not same_work, str(same_work[:3]))
    no_work = [o for o in all_out if "," not in o]
    ck("缺作品的条目不超 10 条", len(no_work) <= 10,
       f"{len(no_work)} 条：{no_work[:5]}")

    print(f"\n失败 {len(FAILS)} / {TOTAL[0]}")
    if FAILS:
        for f in FAILS:
            print(f"   ✗ {f}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
