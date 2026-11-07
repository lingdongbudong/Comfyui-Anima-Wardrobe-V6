# -*- coding: utf-8 -*-
"""全局服装库基线（v5.1.0 新增）。

守六件事：

  ① **数据可读** —— `presets/outfit_library.json` 能解析，110 套 / 10 类（各类 >= 10）；
  ② **派生档位与内容一致** —— `level >= max(字段准入档, 标签档位)`，
     且 drop 覆盖全部 13 个服装字段（漏一个就会出现「预设女仆装 + 随机泳装」）；
  ③ **未启用零影响** —— `library_outfit()` 返回空三元组，`effective_outfit()`
     与 `outfit_for()` 逐字符相同（不启用就不该改变任何随机结果）；
  ④ **启用后真的锁住** —— 正文里出现这套的标签，且档位不够时一条都不出；
  ⑤ **角色预设优先** —— 角色自己启用了预设时，服装库不得顶掉它；
  ⑥ **写操作安全** —— 新增撞 id 自动改名、删除会清 active、写坏 id 报错。

⚠️ 本用例**全程在临时副本上跑**：`LIBRARY_FILE` / `_LIBRARY_PATH` 被指到
   tempp 目录，跑完还原。用户真实库里的 `active` 一个字符都不会动
   （否则跑一次测试就把人家正在用的整套衣服换了）。

用法：`python tests/check_v3_outfitlib.py`
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _boot

R = _boot.load("anima_v4_rules")
S = _boot.load("anima_v4_store")

REAL = S.LIBRARY_FILE
_REAL_ACTIVE_BEFORE = json.load(open(REAL, encoding="utf-8"))["_meta"].get("active") or ""
# v5.5.0：袜子/鞋子的覆盖项同样是**用户状态**，测试也得原样还回去
_REAL_OV_BEFORE = dict(
    (json.load(open(REAL, encoding="utf-8"))["_meta"].get("overrides") or {}))
# v5.9.0：覆盖场景推荐服装的开关也是用户状态
_REAL_SCENE_OV_BEFORE = bool(
    (json.load(open(REAL, encoding="utf-8"))["_meta"] or {}).get("scene_override"))
_TMPDIR = tempfile.mkdtemp(prefix="av5-outfitlib-")
TMP = os.path.join(_TMPDIR, "outfit_library.json")
shutil.copy(REAL, TMP)
S.LIBRARY_FILE = TMP
R._LIBRARY_PATH = TMP
R.clear_cache()


def _restore():
    S.LIBRARY_FILE = REAL
    R._LIBRARY_PATH = REAL
    R.clear_cache()
    try:
        shutil.rmtree(_TMPDIR, ignore_errors=True)
    except Exception:
        pass


atexit.register(_restore)

PASS, FAIL = [], []


def ck(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK' if ok else '!!'}] {name}" + (f"  {detail}" if detail else ""))


print("── ① 数据层 ──")
r = subprocess.run([sys.executable, os.path.join(PLUGIN, "tools",
                                                 "build_outfit_library.py"),
                    "--check"],
                   capture_output=True, text=True, cwd=PLUGIN)
ck("build_outfit_library --check 通过（派生档位与源表一致）",
   r.returncode == 0, (r.stdout or r.stderr)[-160:])

d = S.library_payload()
items = d["outfits"]
cats = d["categories"]
ck("服装库非空", bool(items), f"{len(items)} 套")
# v6.3.0：分类数改为**动态**（曾写死 10，加第 11 类就红）。
# 真正要守的是「分类表与条目实际用到的 cat 一一对应」+ 已知分类不丢。
ck("分类表与条目实际分类一一对应（含丝袜穿搭 / NSFW服装）",
   {c["id"] for c in cats} == {o.get("cat") for o in items}
   and {"hosiery", "nsfw_outfit"} <= {c["id"] for c in cats},
   str([c["id"] for c in cats]))
# v6.5.1：分类 id 改名 + 显示名去掉 R18 图标（🔞）
ck("情趣服饰已改名 NSFW服装（显示名不含 R18 图标）",
   any(c["id"] == "nsfw_outfit" and c.get("zh") == "NSFW服装" for c in cats),
   str([(c["id"], c.get("zh")) for c in cats]))
ck("历史分类 id nsfw 归一到 nsfw_outfit",
   S.normalize_cat("nsfw") == "nsfw_outfit"
   and S.normalize_cat("NSFW") == "nsfw_outfit"
   and S.normalize_cat("casual") == "casual")
# 还要验证「真的走过 library_payload()」，而不只是纯函数对：
# 在临时副本上把分类 id / 一条 cat 改回老值，看返回给面板的数据是否归一。
_alias = json.load(open(TMP, encoding="utf-8"))
for _x in _alias["outfits"]:
    if _x.get("cat") == "nsfw_outfit":
        _x["cat"] = "nsfw"
        break
for _c in _alias["_meta"]["categories"]:
    if _c.get("id") == "nsfw_outfit":
        _c["id"] = "nsfw"
        break
with open(TMP, "w", encoding="utf-8") as _fh:
    json.dump(_alias, _fh, ensure_ascii=False)
_d2 = S.library_payload()
ck("library_payload 真的把老 cat/id 归一（老工作流不失效）",
   all(x.get("cat") != "nsfw" for x in _d2["outfits"])
   and any(c["id"] == "nsfw_outfit" for c in _d2["categories"]),
   str([c["id"] for c in _d2["categories"]]))
shutil.copy(REAL, TMP)   # 立刻还原临时副本，别影响后面的写操作用例
_each = {c["id"]: len([o for o in items if o.get("cat") == c["id"]]) for c in cats}
ck("每类 >= 10 套", all(v >= 10 for v in _each.values()), str(_each))
ck("丝袜穿搭 12 套（v5.2.0）", _each.get("hosiery") == 12, str(_each.get("hosiery")))
_hos = [o for o in items if o.get("cat") == "hosiery"]
ck("丝袜系全部含亮丝星点袜",
   all(any("glittery" in t for t in (o.get("tags") or [])) for o in _hos),
   str([o["id"] for o in _hos
        if not any("glittery" in t for t in (o.get("tags") or []))][:3]))
_COLORS = ("black", "white", "pale pink", "lavender", "pale burgundy")
_cov = {c for c in _COLORS
        if any(c in t for o in _hos for t in (o.get("tags") or []))}
ck("五色都用到（黑/白/淡粉/薰衣草/酒红）", len(_cov) == 5, str(sorted(_cov)))
_FORM = {"pantyhose": 0, "thighhighs": 0, "garter": 0}
for o in _hos:
    for t in (o.get("tags") or []):
        if "glittery pantyhose" in t:
            _FORM["pantyhose"] += 1
        elif "glittery thighhighs" in t:
            _FORM["thighhighs"] += 1
    if any(t == "garter straps" for t in (o.get("tags") or [])):
        _FORM["garter"] += 1
ck("三种形态都用到（连裤 / 过膝 / 吊带）", all(v > 0 for v in _FORM.values()),
   str(_FORM))
ck("id 唯一", len({o["id"] for o in items}) == len(items))
ck("每套 tags >= 3 条（<3 条实测必翻车）",
   all(len(o.get("tags") or []) >= 3 for o in items),
   str([o["id"] for o in items if len(o.get("tags") or []) < 3][:3]))
ck("每套都带中文名 / 分类 / 说明",
   all(str(o.get("zh") or "").strip() and o.get("cat")
       and str(o.get("note") or "").strip() for o in items),
   str([o["id"] for o in items if not str(o.get("zh") or "").strip()][:3]))

print("\n── ② 档位与闭麦 ──")
_bad_lv, _bad_drop, _bad_set = [], [], []
for o in items:
    need = 0
    for k, v in (o.get("fields") or {}).items():
        need = max(need, int(R.option_level(k, v)))
    for t in (o.get("tags") or []):
        need = max(need, int(R.tag_level(t)))
    lv = int(o.get("level") or 0)
    if lv < need or lv > 4:
        _bad_lv.append((o["id"], lv, need))
    if not set(R.OUTFIT_FIELDS) <= set(o.get("drop") or []):
        _bad_drop.append(o["id"])
    for k, v in (o.get("fields") or {}).items():
        _sec, fld = R.field_by_key(k)
        if not fld or v not in [en for _zh, en in fld.get("opts", [])]:
            _bad_set.append((o["id"], k, v))
ck("level 不低于内容准入档（LLM 少了就是闸门漏）", not _bad_lv, str(_bad_lv[:3]))
ck("drop 覆盖全部 13 个服装字段", not _bad_drop, str(_bad_drop[:3]))
ck("fields 的键与取值都在词库里", not _bad_set, str(_bad_set[:3]))
ck("档位分布合理（至少 50 套全档可用）",
   len([o for o in items if int(o.get("level") or 0) == 0]) >= 50,
   str(len([o for o in items if int(o.get("level") or 0) == 0])))

print("\n── ③ 未启用 = 零影响 ──")
S.set_library_active("")
R.clear_cache()
ck("未启用时 library_outfit() 是空三元组",
   R.library_outfit(0) == ([], [], {}), str(R.library_outfit(0)))
_same = True
for role in ("hatsune miku", "tifa lockhart", "asuna_(sao)"):
    if R.effective_outfit(role, 0) != R.outfit_for(role, 0):
        _same = False
ck("未启用时 effective_outfit == outfit_for（逐字符相同）", _same)

print("\n── ④ 启用后真的锁住 ──")
S.set_library_active("cp_maid")
R.clear_cache()
ex, dr, st = R.library_outfit(0)
ck("启用女仆装 → extra 带这套的词", "black maid dress" in (ex or []), str(ex))
ck("启用后 13 个服装字段全部闭麦", set(R.OUTFIT_FIELDS) <= set(dr),
   str(sorted(set(R.OUTFIT_FIELDS) - set(dr))))
_txt = R.draw(level="sfw", seed=5, force_role="hatsune miku")["text"]
ck("正文里出现女仆装标签（真的写进去了）", "black maid dress" in _txt, _txt[:90])
_sets_ok = all(k in set(R.OUTFIT_FIELDS) for k in (st or {}))
ck("强设的字段都属于服装字段", _sets_ok, str(st))

print("\n── ⑤ 档位闸门 ──")
S.set_library_active("sw_micro_bikini")     # L3
R.clear_cache()
_lv3 = int([o for o in S.library_payload()["outfits"]
            if o["id"] == "sw_micro_bikini"][0]["level"])
ck("L3 的套装在 SFW 档一条都不出",
   _lv3 >= 3 and R.library_outfit(0) == ([], [], {}), f"level={_lv3}")
ck("同一套在 L3 档可用", bool(R.library_outfit(3)[0]), str(R.library_outfit(3)[0]))

print("\n── ⑥ 角色预设优先（用户拍板：角色 > 服装库）──")
_role = "tifa_lockhart"
_prev = (R.outfits_data().get(_role) or {}).get("enabled") or ""
if not _prev:
    S.set_outfit_enabled(_role, "default")
    R.clear_cache()
S.set_library_active("cp_maid")
R.clear_cache()
_r_ex, _r_dr, _r_st = R.effective_outfit("tifa lockhart", 0)
ck("角色启用预设时，生效的是角色预设而不是服装库",
   "white tank top" in _r_ex and "black maid dress" not in _r_ex, str(_r_ex[:3]))
_txt2 = R.draw(level="sfw", seed=5, force_role="tifa lockhart")["text"]
ck("正文不会同时出现两套衣服",
   "white tank top" in _txt2 and "black maid dress" not in _txt2, _txt2[:100])
S.set_outfit_enabled(_role, _prev)
R.clear_cache()

print("\n── ⑦ 写操作安全 ──")
_free = S.library_payload()
_old_n = _free["count"]
_rec, _items = S.save_library_outfit(
    {"id": "test_tmp_suit", "zh": "测试套装", "cat": "casual", "level": 0,
     "tags": ["white shirt", "black skirt", "white sneakers"],
     "fields": {"top": "shirt", "bottom": "skirt"}}, new=True)
ck("新增一套 → 条数 +1", len(_items) == _old_n + 1, f"{len(_items)}")
_rec2, _items2 = S.save_library_outfit(
    {"id": "test_tmp_suit", "zh": "测试套装 2", "cat": "casual",
     "tags": ["white shirt", "black skirt", "white sneakers"],
     "fields": {"top": "shirt"}}, new=True)
ck("同 id 新增自动改名（不覆盖）",
   _rec2["id"] == "test_tmp_suit_2" and len(_items2) == _old_n + 2,
   f"{_rec2['id']} / {len(_items2)}")
try:
    S.save_library_outfit({"id": "bad_cat", "zh": "x", "cat": "nope",
                           "tags": ["white shirt"]}, new=True)
    _bad = False
except ValueError:
    _bad = True
ck("非法分类被拒绝（不静默写进去）", _bad)
S.set_library_active("test_tmp_suit")
ck("启用测试套", S.library_payload()["active"] == "test_tmp_suit")
_left = S.delete_library_outfit("test_tmp_suit")
ck("删除条数正确", len(_left) == _old_n + 1, f"{len(_left)}")
try:
    S.set_library_active("no_such_outfit")
    _bad2 = False
except ValueError:
    _bad2 = True
ck("启用不存在的 id 报错", _bad2)

print("\n── ⑨ 袜子 / 鞋子覆盖（v5.5.0）──")
# ⚠️ v5.5.1：先在**临时副本**上把覆盖项清空。用户完全可能在面板上设了袜子/鞋子
#    覆盖（v5.5.0 的功能），那会让"套装自带的袜子/鞋在 extras 里"这条基线直接失败 ——
#    这不是 bug，是测试把"用户状态一定是空的"当成了前提。
S.set_library_override("socks", "")
S.set_library_override("shoes", "")
R.clear_cache()
# 这一套本身带 `black glittery pantyhose` + `pointed toe high heels`，正合适做对拍
S.set_library_active("hs_star_ol_grey")
R.clear_cache()
_b_ex, _b_dr, _b_st = R.library_outfit(0)
ck("未设覆盖时，套装自带的袜子/鞋在 extras 里",
   any("pantyhose" in t for t in _b_ex) and any("high heels" in t for t in _b_ex),
   str(_b_ex))

S.set_library_override("socks", "black pantyhose")
R.clear_cache()
_o_ex, _o_dr, _o_st = R.library_outfit(0)
ck("覆盖袜子：套装那双被从 extras 剔除",
   not [t for t in _o_ex if "pantyhose" in t or "thighhigh" in t], str(_o_ex))
ck("覆盖袜子：字段闭麦 + 写死新值",
   "socks" in _o_dr and _o_st.get("socks") == "black pantyhose",
   f"drop_has={'socks' in _o_dr} sets={_o_st}")
ck("覆盖袜子：其余标签原样保留（只换一件，不动整体）",
   [t for t in _o_ex if "heels" in t] and "grey blazer" in _o_ex, str(_o_ex))

S.set_library_override("shoes", "black boots")
R.clear_cache()
_o2_ex, _o2_dr, _o2_st = R.library_outfit(0)
ck("覆盖鞋子：套装那双被剔除", not [t for t in _o2_ex if "heel" in t], str(_o2_ex))
ck("覆盖鞋子：n_shoes_keep 一并闭麦（否则 NSFW 档会出现两双鞋）",
   "n_shoes_keep" in _o2_dr)
ck("覆盖鞋子：字段写死", _o2_st.get("shoes") == "black boots", str(_o2_st))

# 「不穿」= 词库真实选项，不是空着
S.set_library_override("socks", "barefoot")
R.clear_cache()
_o3_ex, _o3_dr, _o3_st = R.library_outfit(0)
ck("选「不穿」写 barefoot（不是空值）", _o3_st.get("socks") == "barefoot",
   str(_o3_st.get("socks")))

# 档位不够：整套不生效，覆盖也不能"半生效"
S.set_library_active("sw_micro_bikini")     # L3
R.clear_cache()
_o4 = R.library_outfit(0)
ck("档位不够时整套不生效，覆盖也不生效（不能变成「衣服随机、袜鞋指定」）",
   _o4 == ([], [], {}), str(_o4))

# 未知字段要被拒
_bad = False
try:
    S.set_library_override("top", "shirt")
except ValueError:
    _bad = True
ck("覆盖项字段校验：未知字段报错", _bad)

# 复位
S.set_library_override("socks", "")
S.set_library_override("shoes", "")
S.set_library_active("")
R.clear_cache()
ck("清空覆盖 + 停用后回到空三元组（零漂移）",
   R.library_outfit(0) == ([], [], {}))
ck("覆盖项清空后是空串（不是 None）",
   all(v == "" for v in S.library_overrides().values()),
   str(S.library_overrides()))

print("\n── v5.9.0：服装库 ↔ 场景推荐服装的让路开关 ──")
# 前置：启用 L0 女仆装 + 一个袜子覆盖项，用来验证「整套让路」时也一并失效
S.set_library_active("cp_maid")
S.set_library_override("socks", "white thighhighs")
S.set_library_scene_override(False)
R.clear_cache()
_maid = R.library_outfit(0)
ck("前置：女仆装生效且袜子覆盖项生效",
   "black maid dress" in _maid[0]
   and _maid[2].get("socks") == "white thighhighs", str(_maid[2]))
ck("默认关闭 + 场景无推荐服装 → 服装库照常生效",
   R.effective_outfit("hatsune miku", 0, False) == _maid)
ck("关闭 + 场景带推荐服装 → 服装库整套让路（空三元组）",
   R.effective_outfit("hatsune miku", 0, True) == ([], [], {}))
ck("让路时袜子/鞋子覆盖项也不生效（不能出现半套状态）",
   R.effective_outfit("hatsune miku", 0, True) == ([], [], {}))
S.set_library_scene_override(True)
R.clear_cache()
ck("开启 + 场景带推荐服装 → 服装库强制覆盖（回到 v5.8.0 行为）",
   R.effective_outfit("hatsune miku", 0, True) == _maid)
ck("开启 + 场景无推荐服装 → 仍是服装库",
   R.effective_outfit("hatsune miku", 0, False) == _maid)
S.set_library_scene_override(False)
R.clear_cache()

# 角色预设层不受影响（用户拍板：开关只管服装库这一层）
_rprev = (R.outfits_data().get("tifa_lockhart") or {}).get("enabled") or ""
if not _rprev:
    S.set_outfit_enabled("tifa_lockhart", "default")
    R.clear_cache()
ck("让路开关不影响角色预设（角色仍是最高优先级）",
   "white tank top" in R.effective_outfit("tifa lockhart", 0, True)[0])
S.set_outfit_enabled("tifa_lockhart", _rprev)
R.clear_cache()

# 端到端：强制抽到指定场景，看正文/state 里到底是谁的服装生效
_ALL_CANON = [en for _z, en in R.field_by_key("n_canon")[1]["opts"]] + \
             [x for _z, x, _n in R.scene_options(99, include_manual=True)]


def _force(sid, seed=7):
    w = R.empty_weights()
    w["fields"]["n_canon"] = 3.0
    bag = {k: 0.0 for k in _ALL_CANON}
    bag[sid] = 3.0
    w["options"]["n_canon"] = bag
    return R.draw(level="sfw", seed=seed, weights=w, base_prob=0.9,
                  force_role="hatsune miku")


# sc_shrine_walk 写死 outfit=yukata；sc_library_quiet 一个衣物字段都没有
_st_a = _force("sc_shrine_walk")
_st_b = _force("sc_library_quiet")
ck("端到端：带推荐服装的场景 + 开关关闭 → yukata 生效、女仆装不出",
   _st_a["state"].get("outfit") == "yukata"
   and "black maid dress" not in _st_a["text"],
   str(_st_a["state"].get("outfit")))
ck("端到端：没有推荐服装的场景 + 开关关闭 → 服装库照常出",
   "black maid dress" in _st_b["text"], _st_b["text"][:90])
S.set_library_scene_override(True)
R.clear_cache()
_st_c = _force("sc_shrine_walk")
ck("端到端：开关开启 → 女仆装覆盖场景的 yukata",
   "black maid dress" in _st_c["text"]
   and _st_c["state"].get("outfit") != "yukata",
   str(_st_c["state"].get("outfit")))

# 零漂移：服装库没启用时，开关开/关与旧版逐字节一致
S.set_library_active("")
R.clear_cache()
_base_on = _force("sc_shrine_walk")["text"]
S.set_library_scene_override(False)
R.clear_cache()
_base_off = _force("sc_shrine_walk")["text"]
ck("零漂移：服装库未启用时开关不影响正文（逐字节一致）",
   _base_on == _base_off)

# 复位（全程在临时副本上，但保持与其它段同样的纪律）
S.set_library_scene_override(False)
S.set_library_override("socks", "")
S.set_library_override("shoes", "")
S.set_library_active("")
R.clear_cache()

print("\n── v5.9.0：节点缓存指纹 IS_CHANGED（真机出图发现的坑）──")
_N = _boot.load("nodes")
_cls = _N.AnimaV4RandomPrompt
_fp0 = _cls.IS_CHANGED(seed=123)
ck("状态不变时指纹稳定（ComfyUI 缓存仍然有效）",
   _fp0 == _cls.IS_CHANGED(seed=123))
ck("指纹里保留 seed", "seed:123" in str(_fp0), str(_fp0)[:70])
S.set_library_scene_override(True)
R.clear_cache()
_fp1 = _cls.IS_CHANGED(seed=123)
ck("改「覆盖场景推荐服装」开关 → 指纹变化（强制节点重跑）", _fp1 != _fp0)
S.set_library_scene_override(False)
R.clear_cache()
S.set_library_active("cp_maid")
R.clear_cache()
_fp2 = _cls.IS_CHANGED(seed=123)
ck("改服装库 active → 指纹变化", _fp2 != _fp1)
S.set_library_active("")
R.clear_cache()
S.set_library_override("socks", "white thighhighs")
R.clear_cache()
_fp3 = _cls.IS_CHANGED(seed=123)
ck("改袜子 / 鞋子覆盖项 → 指纹变化", _fp3 != _fp2)
S.set_library_override("socks", "")
R.clear_cache()
_fp4 = _cls.IS_CHANGED(seed=123)
ck("状态复位后指纹再次稳定", _fp4 == _cls.IS_CHANGED(seed=123))
ck("seed<0 时每次都变（旧行为保留）",
   _cls.IS_CHANGED(seed=-1) != _cls.IS_CHANGED(seed=-1))
_t0 = R.draw(level="sfw", seed=5, force_role="hatsune miku")["text"]
S.set_library_scene_override(True)
R.clear_cache()
_t1 = R.draw(level="sfw", seed=5, force_role="hatsune miku")["text"]
S.set_library_scene_override(False)
R.clear_cache()
ck("指纹只决定是否重跑，不进正文（同 seed 逐字节一致）", _t0 == _t1)

print("\n── ⑧ 真实库未被测试污染 ──")
_raw = json.load(open(REAL, encoding="utf-8"))
_now_active = str((_raw.get("_meta") or {}).get("active") or "")
ck("真实文件的 active 与开工时完全一致（测试全程在临时副本上）",
   _now_active == _REAL_ACTIVE_BEFORE,
   f"before={_REAL_ACTIVE_BEFORE!r} now={_now_active!r}")
if _now_active != _REAL_ACTIVE_BEFORE:
    # 兜底：万一哪天补丁失效，别把用户的启用态改掉
    S.set_library_active(_REAL_ACTIVE_BEFORE)
_raw_ov = dict((_raw.get("_meta") or {}).get("overrides") or {})
ck("真实文件的袜子/鞋覆盖项与开工时一致（测试全程在临时副本上）",
   _raw_ov == _REAL_OV_BEFORE, f"before={_REAL_OV_BEFORE!r} now={_raw_ov!r}")
if _raw_ov != _REAL_OV_BEFORE:
    for _k, _v in (_REAL_OV_BEFORE or {}).items():
        S.set_library_override(_k, _v)
_raw_sc = bool((_raw.get("_meta") or {}).get("scene_override"))
ck("真实文件的覆盖场景开关与开工时一致（测试全程在临时副本上）",
   _raw_sc == _REAL_SCENE_OV_BEFORE,
   f"before={_REAL_SCENE_OV_BEFORE!r} now={_raw_sc!r}")
ck("真实文件套数与库一致（未被测试改写）",
   len(_raw.get("outfits") or []) == len(items),
   f'{len(_raw.get("outfits") or [])} vs {len(items)}')

print(f"\n[check_v3_outfitlib] 通过 {len(PASS)} 项，失败 {len(FAIL)} 项"
      + (f"：{FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
