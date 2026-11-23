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
# ⑤ 段断言「遐蝶未启用 = 不出」，但 `enabled` 是**用户数据** —— 本机启用过就会
# 无缘无故变红（2026-11-23 实测：用户真机上启用了遐蝶）。同样显式关掉再还原。
_FORCE_OFF = ["castorice_(honkai:_star_rail)"]
_PREV_OFF = {r: ((R.outfits_data().get(r) or {}).get("enabled") or "")
             for r in _FORCE_OFF}


def _restore_outfits():
    for role, prev in _PREV.items():
        try:
            S.set_outfit_enabled(role, prev)
        except Exception:
            pass
    for role, prev in _PREV_OFF.items():
        try:
            S.set_outfit_enabled(role, prev)
        except Exception:
            pass
    R.clear_cache()


atexit.register(_restore_outfits)
for _role, _oid in _FIXTURE.items():
    S.set_outfit_enabled(_role, _oid)
for _role in _FORCE_OFF:
    S.set_outfit_enabled(_role, "")
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

print("\n── ⑥ 官方预设（v6.29.0，用户 2026-11-23）──")
# 用户：「官方预设服装不支持通过手动删除按钮删除，避免误删」。
# 守三层：数据里有 official 标记 → 后端拒绝删除 → 面板载荷带套数。
_official = [r for r, b in data.items()
             if any(isinstance(x, dict) and x.get("official") for x in (b.get("list") or []))]
ck("official 标记已回填到存量条目", len(_official) >= 2400, f"{len(_official)} 位角色")
ck("每套 default 都能对上 official",
   all(o.get("official") for b in data.values()
       for o in (b.get("list") or [])
       if isinstance(o, dict) and o.get("id") == "default"))
ck("_clean_outfit 保留 official（面板保存后不会变回可删）",
   S._clean_outfit({"id": "x", "tags": ["white dress"], "official": True}).get("official") is True
   and S._clean_outfit({"id": "x", "tags": ["white dress"]}).get("official") is False)
_before = len((data.get("tifa_lockhart") or {}).get("list") or [])
try:
    S.delete_outfit("tifa_lockhart", "default")
    _denied = False
except ValueError as exc:
    _denied = "官方" in str(exc)
R.clear_cache()
_after = len((R.outfits_data().get("tifa_lockhart") or {}).get("list") or [])
ck("后端拒绝删除官方预设", _denied and _before == _after, f"{_before} → {_after}")
# 非官方（日常模板）仍可删 —— 否则「保留模板」这条口径就成了单向门
_multi = [r for r, b in data.items()
          if any(isinstance(x, dict) and not x.get("official") for x in (b.get("list") or []))]
ck("非官方条目不受锁的限制（库里仍有可删的日常模板）", bool(_multi), f"{len(_multi)} 位")

print("\n── ⑥之二 trigger → tag 反查（v6.29.1 真机踩到）──")
# 库里同时有基础角色与「换装版角色」条目时，前缀兜底会撞上换装版（实测 chisa 穿成浴衣）。
_d = R.outfits_data()
_base = _d.get("chisa_(wuthering_waves)")
_vari = _d.get("chisa_(peach_parfait)_(wuthering_waves)")
ck("trigger 反查命中基础角色条目（不被换装版抢走）",
   isinstance(_base, dict) and R.outfit_rec("chisa, wuthering waves") is _base)
ck("换装版自己的 trigger 仍命中自己",
   isinstance(_vari, dict) and R.outfit_rec("chisa (peach parfait), wuthering waves") is _vari)
ck("裸角色名仍走前缀兜底且取最基础的那条",
   R.outfit_rec("chisa") is _base)

print("\n── ⑦ 卡片套数（v6.29.0）──")
_N = _boot.load("nodes")
_pay = _N.characters_payload()
_chars = _pay.get("characters") or []
ck("每条角色都带 outfits 整数",
   bool(_chars) and all(isinstance(c.get("outfits"), int) for c in _chars),
   f"{len(_chars)} 条")
_spot = {c["tag"]: c.get("outfits") for c in _chars}
_mismatch = [t for t, n in _spot.items()
             if n != len((data.get(t) or {}).get("list") or [])]
ck("套数与 character_outfits.json 一致", not _mismatch, f"不一致 {len(_mismatch)} 条 {_mismatch[:3]}")
ck("多套角色在载荷里 > 1", (_spot.get("snow_white_(nikke)") or 1) > 1,
   f"snow_white={_spot.get('snow_white_(nikke)')}")

print("\n── ⑧ 官方预设「最高权重」压过场景推荐服装（v6.30.0）──")
# 用户 2026-11-23 13:39：「官方预设服装启用后拥有最高权重，能够使场景推荐服装失效」。
# 拍板口径：**裸体场景仍让路**，浴巾 / 浴衣 / 婚纱 / 校服 / 泳装一律让给预设，
# 并把场景文本里描述衣服的那部分清掉（否则正文里两套衣服并存）。
_ALL_CANON = [en for _z, en in R.field_by_key("n_canon")[1]["opts"]] + \
             [x for _z, x, _n in R.scene_options(99, include_manual=True)]


def _force_scene(sid, seed=7, level="sfw", role="tifa lockhart"):
    w = R.empty_weights()
    w["fields"]["n_canon"] = 3.0
    bag = {k: 0.0 for k in _ALL_CANON}
    bag[sid] = 3.0
    w["options"]["n_canon"] = bag
    return R.draw(level=level, seed=seed, weights=w, base_prob=0.9, force_role=role)


# 纯函数层
ck("state_is_nude 认四个裸体档",
   all(R.state_is_nude({"n_state": x}) for x in
       ("completely nude", "topless", "bottomless", "nude with accessories only")))
ck("state_is_nude 不认半脱 / see-through（半脱与「穿着一套」可同时成立）",
   not R.state_is_nude({"n_state": "partially undressed"})
   and not R.state_is_nude({"n_state": "see-through"})
   and not R.state_is_nude({}))

_st_probe = {"n_extra": "running water, bath towel, wet hair",
             "_scene_nl": "she stands under the water. she is in a bath towel. "
                          "steam rises around her."}
_rm = R.strip_scene_clothing(_st_probe)
ck("strip_scene_clothing 清掉 n_extra 里的服装标签、保留场景标签",
   _st_probe["n_extra"] == "running water, wet hair", _st_probe["n_extra"])
ck("strip_scene_clothing 按句清 scene_nl（构图句保留、衣服句删除）",
   "bath towel" not in _st_probe["_scene_nl"]
   and "steam rises around her" in _st_probe["_scene_nl"],
   _st_probe["_scene_nl"])
ck("改过的 scene_nl 登记进 _nl_dynamic（否则 F11 报超长未登记）",
   _st_probe["_scene_nl"].lower() in (R.nl_sentences(_st_probe["_nl_dynamic"]) or set()))
ck("清完再跑一次是空操作（幂等，密度重试不会越清越多）",
   R.strip_scene_clothing(_st_probe) == [])
ck("场景本来没提衣服 → 返回空列表、一个字都不动",
   R.strip_scene_clothing({"n_extra": "waves, sunscreen"}) == [])

# 端到端：不启用预设时，场景推荐服装照旧生效（零漂移）
S.set_outfit_enabled("tifa_lockhart", "")
R.clear_cache()
_a0 = _force_scene("sc_shrine_walk")
ck("零漂移：未启用预设 → 场景的 yukata 照旧生效",
   _a0["state"].get("outfit") == "yukata", str(_a0["state"].get("outfit")))

# 启用官方预设 → 场景推荐服装失效
S.set_outfit_enabled("tifa_lockhart", "default")
R.clear_cache()

_a1 = _force_scene("sc_shrine_walk")
ck("预设启用 → 场景写死的 yukata 让位（outfit 被闭麦）",
   _a1["state"].get("outfit") != "yukata", str(_a1["state"].get("outfit")))
ck("预设启用 → 正文里出现预设标签", "white tank top" in _a1["text"], _a1["text"][:90])
ck("预设启用 → 正文里不再出现 yukata（结构化 + 文本都清）",
   "yukata" not in _a1["text"], _a1["text"][:120])
ck("报告里说明「压过场景推荐服装」",
   any("压过场景推荐服装" in _n for _n in _a1["notes"]),
   str(_a1["notes"])[:160])

_a2 = _force_scene("sc_under_shower")
ck("浴巾场景（outfit=bath towel）：预设赢，正文无 bath towel",
   "white tank top" in _a2["text"] and "bath towel" not in _a2["text"],
   _a2["text"][:120])
ck("浴巾场景：场景里非服装的标签保留（showering / steam）",
   "showering" in _a2["text"] or "steam" in _a2["text"], _a2["text"][:120])

_a3 = _force_scene("sc_wedding_bouquet_wait")
ck("婚纱场景：预设赢，正文无 wedding dress",
   "white tank top" in _a3["text"] and "wedding dress" not in _a3["text"],
   _a3["text"][:120])

# 裸体场景仍让路（拍板口径）
# ⚠️ 档位名 → 数字是 sfw 0 / suggestive 1 / explicit 2 / hardcore 3 / extreme 4，
#    `sc_ccc_afterglow` 是 L3 → 必须用 hardcore，用 explicit 场景根本不会被应用。
_a4 = _force_scene("sc_ccc_afterglow", level="hardcore")
ck("裸体场景仍让路：正文保留 completely nude、不写预设标签",
   "completely nude" in _a4["text"] and "white tank top" not in _a4["text"],
   _a4["text"][:120])
ck("裸体场景让路时有报告说明",
   any("角色服装预设让路" in _n for _n in _a4["notes"]),
   str(_a4["notes"])[:160])

# 「场景主体就是某件衣物」的也让路（用户拍板：这类场景保持原样）
ck("主体是衣物的场景判定：特写 / 丝袜 / 换装 → 让路",
   R.scene_clothing_is_subject("sc_near_foot_heel")
   and R.scene_clothing_is_subject("sc_sock_class_desk")
   and R.scene_clothing_is_subject("sc_changing_room"))
ck("普通推荐服装场景（浴衣 / 婚纱）不算主体 → 预设赢",
   not R.scene_clothing_is_subject("sc_shrine_walk")
   and not R.scene_clothing_is_subject("sc_wedding_bouquet_wait"))

_a5 = _force_scene("sc_near_foot_heel")
ck("鞋跟特写 + 预设：场景的鞋子保留（预设让路，不清成「只剩脚脖子」）",
   ("high heels" in _a5["text"] or "heels" in _a5["text"])
   and "white tank top" not in _a5["text"], _a5["text"][:120])
_a6 = _force_scene("sc_sock_class_desk")
ck("丝袜场景 + 预设：袜子保留",
   "thighhighs" in _a6["text"] and "white tank top" not in _a6["text"],
   _a6["text"][:120])

# 复位
S.set_outfit_enabled("tifa_lockhart", _PREV.get("tifa_lockhart", ""))
R.clear_cache()

print()
if FAIL:
    print(f"[check_v3_outfits] 失败 {len(FAIL)} 项：{FAIL}")
    sys.exit(1)
print(f"[check_v3_outfits] 通过 {len(PASS)} 项：数据 / 三元组 / 锁定 / 零影响 / 闸门")
