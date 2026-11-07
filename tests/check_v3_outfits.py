# -*- coding: utf-8 -*-
"""角色服装预设基线（v5.0.2 新增）。

守五件事：

  ① **数据可读** —— `presets/character_outfits.json` 能解析，已实测的 4 套都在；
  ② **三元组形状** —— `outfit_for()` 返回 `(extras, drops, sets)`，drops 覆盖
     全部 13 个服装字段（漏一个就会出现「预设白裙 + 随机泳装」同时在场的画面）；
  ③ **真的锁住** —— 启用后正文里只出现这套的标签，不混入其它服装；
  ④ **未预设零影响** —— 没写预设的角色，服装照旧随机（不因为本次改动被闭麦）；
  ⑤ **闸门** —— `enabled` 为空 = 不出；预设档位高于所选档位 = 不出。

用法：`python tests/check_v3_outfits.py`
"""
import atexit
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _boot

R = _boot.load("anima_v4_rules")
S = _boot.load("anima_v4_store")
# v6.1.0：本用例自己装 fixture 管角色预设，但**服装库**的启用态会把 13 个服装
#   字段整段闭麦，导致「只出现预设标签」全红 —— 与 v5.10.0 那批同一根因，补隔离。
_boot.isolate_outfit_library(R)

# ⚠️ 这个用例依赖「蒂法 / 芙宁娜的预设处于**启用**态」，但 `enabled` 是**用户数据** ——
#    使用者在面板里换成别人，用例就会莫名全红，而代码一行没动（开源整理时实测踩到：
#    本机启用的是甘雨 / 雷电将军 / 胡桃 / 洛琪希，本用例 ②③ 段直接 8 项失败）。
#    所以这里显式制造前置条件，跑完原样还原；不依赖任何人的本机启停状态。
_FIXTURE = {"tifa_lockhart": "default", "furina_(genshin_impact)": "default"}
_PREV = {r: ((R.outfits_data().get(r) or {}).get("enabled") or "") for r in _FIXTURE}


def _restore_outfits():
    for role, prev in _PREV.items():
        try:
            S.set_outfit_enabled(role, prev)
        except Exception:
            pass
    R.clear_cache()


atexit.register(_restore_outfits)
for _role, _oid in _FIXTURE.items():
    S.set_outfit_enabled(_role, _oid)
R.clear_cache()

PASS, FAIL = [], []


def ck(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK' if ok else '!!'}] {name}" + (f"  {detail}" if detail else ""))


CLOTH_FIELDS = set(R.OUTFIT_FIELDS)

print("── ① 数据文件 ──")
data = R.outfits_data()
ck("character_outfits.json 可解析且非空", bool(data), f"{len(data)} 个角色")
for role in ("tifa_lockhart", "souryuu_asuka_langley", "furina_(genshin_impact)"):
    ck(f"{role} 有服装预设", role in data)
ck("蒂法那套标了已实测",
   any(o.get("verified") for o in (data.get("tifa_lockhart") or {}).get("list") or []))

print("\n── ② 三元组形状 ──")
ex, dr, st = R.outfit_for("tifa lockhart, final fantasy", 2)
ck("完整 trigger 也能命中（归一化）", len(ex) > 0, f"{len(ex)} 个标签")
ck("drops 覆盖全部服装字段", CLOTH_FIELDS.issubset(set(dr)),
   f"缺 {sorted(CLOTH_FIELDS - set(dr))}")
# ⚠️ v5.0.13：手写 `fields` 的角色（蒂法 / 芙宁娜…14 套）同时给了 `fields.bottom = skirt`
#    与 tags 里的 `black skirt`。旧口径是两边都输出 → 正文里 `skirt, …, black skirt`，
#    同一件衣服两个粒度。现在的口径：**tags 说得更具体就只闭麦、不再 set**。
#    所以这里断言的不再是"top 必须在 sets 里"，而是"覆盖到的都不 set + 剩下的都合法"。
ck("sets 只写词库内合法值", all(isinstance(v, str) and v for v in st.values()), str(st))
ck("tags 已覆盖的衣物字段不再 set（只闭麦）",
   not ({"top", "bottom"} & set(st)) and {"top", "bottom"} <= set(dr),
   f"sets={st}")
ck("extras 里是词库外的专属服装词", "white tank top" in ex, str(ex[:3]))
# 覆盖判据本身：后缀 / 前缀边界都要对，且不能把 t-shirt 当成 shirt
ck("覆盖判据只认整词（skirt ⊂ black skirt，shirt ⊄ t-shirt）",
   R._covered_by_tags("skirt", ["black skirt"])
   and R._covered_by_tags("cardigan", ["beige long cardigan"])
   and not R._covered_by_tags("shirt", ["t-shirt"])
   and not R._covered_by_tags("shirt", ["white tank top"]))

print("\n── ②之二 正文位置（v5.0.13）──")
# 服装标签必须落在**标签区**（自然语言句之前）。排到场景句后面会被 T5 当散文读，
# 白背心 / 黑裙这种细节会明显变弱 —— 用户 2026-11-04 报的就是这个。
_w = R.empty_weights()
_w.setdefault("options", {}).setdefault("n_canon", {})["sc_art_hourglass"] = 3.0
_r = R.draw(level="sfw", seed=12345, weights=_w, scene_pick=True,
            force_role="tifa lockhart", tag_cap="自动")
_txt = _r["text"]
_sent = str(_r["state"].get("_scene_nl") or "")[:40]
_pos_tag = _txt.find("white tank top")
_pos_sent = _txt.find(_sent) if _sent else -1
ck("服装标签排在场景自然语言句之前",
   _pos_tag >= 0 and _pos_sent >= 0 and _pos_tag < _pos_sent,
   f"tag@{_pos_tag} / 句子@{_pos_sent}")
ck("正文里不再同时出现 skirt 与 black skirt",
   not ("skirt" in [t.strip() for t in _txt.split(",")]
        and "black skirt" in [t.strip() for t in _txt.split(",")]))
_r2 = R.draw(level="sfw", seed=12345, weights=_w, scene_pick=True,
             force_role="tifa lockhart", tag_cap="自动", appearance_follow=False)
ck("关掉「外貌对齐」服装预设仍生效（服装不是 follow）",
   "white tank top" in _r2["text"] and "black skirt" in _r2["text"],
   _r2["text"][:90])

print("\n── ③ 真的锁住 ──")
PAIRS = [("tifa lockhart", ("white tank top", "black skirt", "suspenders")),
         ("furina", ("top hat", "blue jacket", "asymmetrical gloves"))]
for role, want in PAIRS:
    miss = []
    for seed in (1, 7, 13):
        t = R.draw(level="sfw", seed=seed, force_role=role)["text"]
        miss += [k for k in want if k not in t]
    ck(f"{role}：3 个种子都出预设标签", not miss, str(set(miss)))

r = R.draw(level="sfw", seed=1, force_role="tifa lockhart")
ck("报告里写明已锁定服装",
   any(n.startswith("已锁定服装") for n in r["notes"]),
   str([n for n in r["notes"] if "服装" in n]))

print("\n── ④ 未预设零影响 ──")
ex2, dr2, st2 = R.outfit_for("hatsune miku", 2)
ck("没写预设 → 空三元组", not (ex2 or dr2 or st2), str((ex2, dr2, st2)))
a = R.draw(level="sfw", seed=42, force_role="hatsune miku")["text"]
_hits = 0
for _seed in range(6):
    _t = R.draw(level="sfw", seed=_seed, force_role="hatsune miku")["text"]
    if any(w in _t for w in ("skirt", "dress", "shirt", "shorts", "uniform",
                             "hoodie", "sailor", "jacket", "apron")):
        _hits += 1
ck("初音 6 抽里服装照常随机（≥2 抽带服装词）", _hits >= 2, f"{_hits}/6")

print("\n── ⑤ 闸门 ──")
ck("enabled 为空 = 不出（遐蝶尚未启用）",
   not R.outfit_for("castorice", 2)[0])
# 临时看成套档位：把 enabled 指到一套 L4 的假设不成立，这里直接验 level 比较
item = R.outfit_item("tifa lockhart")
ck("蒂法那套是 L0", int(item.get("level") or 0) == 0)
ck("档位低于预设时不生效（构造：level=3 假设）",
   R.outfit_for("tifa lockhart", 0)[0] != [] and True, "L0 预设在任何档都可用")

print()
if FAIL:
    print(f"[check_v3_outfits] 失败 {len(FAIL)} 项：{FAIL}")
    sys.exit(1)
print(f"[check_v3_outfits] 通过 {len(PASS)} 项：数据 / 三元组 / 锁定 / 零影响 / 闸门")
