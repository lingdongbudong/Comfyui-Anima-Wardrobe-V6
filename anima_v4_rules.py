# -*- coding: utf-8 -*-
"""ANIMA-Wardrobe-V3 · 随机引擎与规则层

把 ANIMA提示词生成器.html 的「词库 + 随机 + 自检」三件事搬到 Python，
并补上源脚本缺的四块能力：

    1. 五级档位闸门  —— 候选池**先**按档位收敛，再抽，抽完体检回滚。
       源脚本是「先盲抽、抽完 sanitize 打补丁」，高档内容会漏进低档。
    2. 三层权重      —— 段 / 字段 / 选项，逐层可调，0 等于关闭。
       源脚本只有一个写死的 `Math.random()<0.65`。
    3. 确定性种子    —— seed>=0 时逐字节可复现。
       派生种子一律走 zlib.crc32，**不用内置 hash()**（每进程加盐）。
    4. 输出槽位重排  —— 按 ANIMA3 v3.0 规范排序，而不是按界面段序。
       界面段序把「服装状态」排在「场景」之后，进 CLIP 的顺序是错的。

⚠️ 本模块输出**英文 danbooru 标签串**（ANIMA 底模吃这个），中文只用于
   界面与摘要。这与 Krea2 系列「提示词一律中文」的口径不冲突 —— 那是
   中文自然语言模型的规矩，两回事。

⚠️ 词库唯一真源是那份 HTML；档位唯一真源是造型台的 wardrobe_rules.py。
   本文件只做「读 + 组合」，不存任何词条。
"""
from __future__ import annotations

import json
import os
import random
import re
import zlib

from . import anima_v4_nsfw as N
from . import anima_v4_link as L
from . import anima_v4_outfit_style as OS
from .anima_v4_overrides import LEVEL_DEMOTE, raise_
from . import anima_v4_store as STORE

# ============================================================================
# 一、加载词库与档位表
# ============================================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
_POOLS_PATH = os.path.join(_HERE, "presets", "anima_v4_pools.json")
_LEVELS_PATH = os.path.join(_HERE, "presets", "anima_v4_levels.json")
_CHARS_PATH = os.path.join(_HERE, "presets", "anima_v4_characters.json")

_CACHE: dict = {}


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def pools() -> dict:
    """词库（零漂移导出物）。只在首次调用时读盘。"""
    if "pools" not in _CACHE:
        _CACHE["pools"] = _load_json(_POOLS_PATH)
    return _CACHE["pools"]


def levels() -> dict:
    """选项档位表（派生数据，见 tools/classify_v3_levels.py）。"""
    if "levels" not in _CACHE:
        _CACHE["levels"] = _load_json(_LEVELS_PATH)
    return _CACHE["levels"]


def sections() -> list:
    return pools()["sections"]


def section_by_id(sec_id: str):
    for s in sections():
        if s["id"] == sec_id:
            return s
    return None


def field_by_key(key: str):
    """返回 (段落, 字段)；找不到返回 (None, None)。"""
    for s in sections():
        for f in s["fields"]:
            if f["key"] == key:
                return s, f
    return None, None


def field_keys() -> list:
    return [f["key"] for s in sections() for f in s["fields"]]


def section_ids() -> list:
    return [s["id"] for s in sections()]


def canon() -> dict:
    """**全部预设**：法典 14 组（源 HTML）+ 场景搭配库（v3.9.0）。

    返回 `{预设 id: {字段: 值}, ...}`。合并后这张表就是唯一入口 ——
    `apply_preset()` 不认识来源，两条来源走**同一条**填充路径，
    因此自动共享档位 / 权重 / 锁定三道闸门与成品自动修复。

    ⚠️ 结果缓存：它在 `_one_pass` 的热路径上被反复调用（每轮密度自适应都调）。
    """
    if "canon_merged" not in _CACHE:
        m = dict(pools().get("canon", {}) or {})
        for sc in scene_list():
            rec = scene_preset(sc.get("id"))
            if rec:
                m[sc["id"]] = rec
        _CACHE["canon_merged"] = m
    return _CACHE["canon_merged"]


def appear() -> dict:
    return pools().get("appear", {})


def _field_section() -> dict:
    """字段 key → 所属段 id（缓存）。给段下限查表用。"""
    if "field_section" not in _CACHE:
        m = {}
        for s in sections():
            for f in s["fields"]:
                m[f["key"]] = s["id"]
        _CACHE["field_section"] = m
    return _CACHE["field_section"]


def section_floor(sec_id: str) -> int:
    """段级保底下限（见 anima_v4_overrides.SECTION_FLOOR）。"""
    return int(levels().get("section_floor", {}).get(sec_id, 0))


# ============================================================================
# 一之二、角色库（独立文件，不污染零漂移的词库）
# ============================================================================
#
# 为什么角色库要单独成文件：
#   pools.json 是「源 HTML 的零漂移导出物」—— 它必须与那份 HTML 逐字节
#   对应，`check_v3_pools.py` 靠这条对拍守住词库不被偷偷改坏。
#   如果把 2000 条角色写进 role 字段，那份对拍就永久失效了。
#   所以角色库独立成 presets/anima_v4_characters.json，role 字段的候选
#   由 `role_pool()` 现场合并（源 HTML 的 221 条 + 角色库）。
#
# 角色库缺席时自动退回源 HTML 的 221 条 —— 保证任何测试/环境都能跑。

def clear_cache():
    """清掉全部进程级缓存。

    ⚠️⚠️ **任何写用户角色数据的路由成功后都必须调它**，否则"改了不生效、
       新增不出现"：characters / char_index / char_by_trigger / role_options
       全是内存快照，读盘只发生在首次调用。词库与档位表也在这块缓存里，
       一起丢掉只是重新读两个小 JSON，代价可以忽略。
    """
    _CACHE.clear()


def _user_chars() -> dict:
    """用户角色覆盖层（edits + custom）。缓存；写操作后靠 clear_cache 清。"""
    if "user_chars" not in _CACHE:
        from . import anima_v4_store as S
        _CACHE["user_chars"] = S.load_user_characters()
    return _CACHE["user_chars"]


def user_edits() -> dict:
    """{内置 tag: 覆盖项}。详情接口靠它判断"这一项被改过没有"。"""
    return _user_chars().get("edits") or {}


def _chars_base() -> dict:
    """**内置**角色库原文（不含用户覆盖层）。缓存。

    为什么要单独缓存它：合并结果（`characters_data`）已经被用户改动污染，
    而"恢复默认""显示内置原始标签"都需要未污染的原文。
    """
    if "chars_base" not in _CACHE:
        base = {"characters": [], "works": []}
        if os.path.exists(_CHARS_PATH):
            try:
                base = _load_json(_CHARS_PATH)
            except Exception:
                base = {"characters": [], "works": []}
        _CACHE["chars_base"] = base
    return _CACHE["chars_base"]


def builtin_char_index() -> dict:
    """{角色 tag: **内置**记录}。给"内置原始标签"对照用。"""
    if "builtin_char_index" not in _CACHE:
        _CACHE["builtin_char_index"] = {
            c["tag"]: c for c in _chars_base().get("characters", [])
        }
    return _CACHE["builtin_char_index"]


def _work_slug(en: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(en or "").lower()).strip("_")


# 用户自定义作品 → 界面显示用的中文名。没登记的作品直接显示原名。
WORK_ZH_HINT = {
    "fx senshi kurumi-chan": "FX战士久留美",
    # v5.4.2：AI 助手拟人角色的合集。7 位（ChatGPT / Claude / Gemini / Grok /
    # 千问 / GLM / 大肥鱼）的 `work` 统一写 `ai assistant`，靠这一行显示中文；
    # 不加这行的话左侧作品列表会显示英文原名，跟其它作品的中文名不一致。
    "ai assistant": "AI 拟人",
}


def _merge_overlay(base: dict) -> dict:
    """内置角色库 + 用户覆盖层 → 合并结果。

    ⚠️ 三件事必须一起做，少一件前端就会错：
      ① edits 覆盖允许的键（trigger / zh / work / thumb）；
      ② custom 整条追加；
      ③ **重算 works[].count** —— 前端有条断言「点作品 → 卡片数 == count」
         （它正是 v3.1.0「点 IP 作品空列表」线上 bug 的回归用例），
         新增角色会让某个作品多几条，不重算就对不上。
    另外：自定义角色填了库里没有的作品名时给它**补一行作品**，
    否则那位角色在左侧作品列表里根本点不到。
    """
    ov = _user_chars()
    edits = ov.get("edits") or {}
    custom = ov.get("custom") or []

    chars = []
    for c in base.get("characters", []) or []:
        c = dict(c)
        patch = edits.get(str(c.get("tag") or ""))
        if patch:
            # v3.44.4：多带 hair / eyes —— `appearance_for()` 里角色库的
            # `hair`/`eyes` 字段**优先于**官方特征 tags，只能改 tags 时
            # 发色根本改不动（明日香 orange/brown 冲突即此）。
            for k in ("trigger", "zh", "work", "thumb", "hair", "eyes"):
                v = patch.get(k)
                if isinstance(v, str) and v.strip():
                    c[k] = v.strip()
            # ⚠️ 特征标签是**列表**，整条替换（不做并集）——
            #    用户删掉一条标签必须真的删掉，并集会让"删除"永远无效。
            if isinstance(patch.get("tags"), list) and patch["tags"]:
                c["tags"] = [str(t).strip().lower() for t in patch["tags"] if str(t).strip()]
        chars.append(c)
    for c in custom:
        rec = dict(c)
        rec.setdefault("src", "user")
        chars.append(rec)

    works = [dict(w) for w in (base.get("works", []) or [])]
    id_of_work = {}
    for w in works:
        id_of_work.setdefault(w.get("en", ""), w.get("id", ""))
    # ⚠️ v3.44.4：补行要覆盖**所有角色**，不只是 custom。
    #    原实现只遍历 custom，于是"用覆盖层改内置角色的作品名"（例如把玛奇玛从
    #    错误的 goddess of victory: nikke 改回 chainsaw man，而作品表里没有
    #    chainsaw man 以外的新作品行）会让该角色 work_id 为空 →
    #    左侧作品列表点不到它，`check_v3_chars` 的「每个角色都有 work_id」直接红。
    #    口径与 docstring 一致：**指向库里没有的作品名就补一行**，来源不重要。
    for c in chars:
        wk = str(c.get("work") or "").strip()
        if not wk or wk in id_of_work:
            continue
        used = {w.get("id") for w in works}
        sid, i = (_work_slug(wk) or "user_work"), 2
        while sid in used:
            sid = f"{_work_slug(wk) or 'user_work'}_{i}"
            i += 1
        works.append({"id": sid, "en": wk,
                      "zh": WORK_ZH_HINT.get(wk.strip().lower(), wk),
                      "count": 0, "pop": 0, "target": False})
        id_of_work[wk] = sid

    cnt = {}
    for c in chars:
        wid = id_of_work.get(str(c.get("work") or "").strip(), "")
        if wid:
            cnt[wid] = cnt.get(wid, 0) + 1
    for w in works:
        w["count"] = cnt.get(w.get("id"), 0)
    # 与内置产物同一口径：按角色数降序（并列时按英文名，保证确定性）
    works.sort(key=lambda w: (-int(w.get("count") or 0), str(w.get("en") or "")))

    meta = dict(base.get("_meta") or {})
    meta["merged"] = {
        "builtin": len(base.get("characters", []) or []),
        "edits": len(edits),
        "custom": len(custom),
    }
    return {"_meta": meta, "works": works, "characters": chars}


def characters_data() -> dict:
    """内置角色库 + 用户覆盖层（见 _merge_overlay）。

    初版只返回内置文件；v3.4.0 起改为合并结果 —— 三个消费方
    （面板 /characters、role 候选池、外貌对齐）因此自动看到用户的编辑与新增。
    """
    if "characters" not in _CACHE:
        _CACHE["characters"] = _merge_overlay(_chars_base())
    return _CACHE["characters"]


def has_character_library() -> bool:
    return bool(characters_data().get("characters"))


def char_works() -> list:
    return characters_data().get("works", [])


def char_index() -> dict:
    """{角色 tag: 角色记录}。"""
    if "char_index" not in _CACHE:
        _CACHE["char_index"] = {
            c["tag"]: c for c in characters_data().get("characters", [])
        }
    return _CACHE["char_index"]


_ALIAS_PATH = os.path.join(_HERE, "presets", "char_aliases.json")


def char_aliases() -> dict:
    """{旧角色 tag: 新 tag} —— 改名兼容表（`presets/char_aliases.json`）。

    为什么单独一个文件而不是塞进 user_characters.json：那份覆盖层读写时会
    被 `_norm_overlay` / `_save_overlay` 重建成只剩 edits+custom，_meta 里
    放什么都会被下一次保存抹掉（实测踩过）。改名只发生在「并进内置库」这种
    低频动作上，用独立文件最稳。**改完必须重启**（进进程缓存）。
    """
    if "char_aliases" not in _CACHE:
        try:
            doc = _load_json(_ALIAS_PATH)
        except Exception:
            doc = {}
        al = doc.get("aliases") if isinstance(doc, dict) else None
        _CACHE["char_aliases"] = {
            str(k).strip().lower(): str(v).strip().lower()
            for k, v in (al or {}).items() if str(k).strip() and str(v).strip()
        }
    return _CACHE["char_aliases"]


def _role_options() -> list:
    """合并后的角色候选池 [(zh, en, level), ...]。

    · 源 HTML 的 221 条在前（中文名人工核准过、且带 APPEAR 外貌表）
    · 角色库的其余条目接在后面
    · 同 trigger 去重
    """
    if "role_options" in _CACHE:
        return _CACHE["role_options"]

    out, seen = [], set()
    _sec, fld = field_by_key("role")
    for zh, en in (fld.get("opts") if fld else []) or []:
        en = (en or "").strip()
        if not en:
            continue
        key = en.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((zh.strip(), en, int(N.grade_nsfw(
            [t.strip() for t in en.split(",") if t.strip()])["num"])))

    for c in characters_data().get("characters", []):
        trig = (c.get("trigger") or c.get("tag") or "").strip()
        if not trig or trig.lower() in seen:
            continue
        seen.add(trig.lower())
        zh = c.get("zh") or c.get("name") or trig
        out.append((zh, trig, int(N.grade_nsfw(
            [t.strip() for t in trig.split(",") if t.strip()])["num"])))

    _CACHE["role_options"] = out
    return out


def role_pool(lv: int = 99) -> list:
    """按档位过滤后的角色候选池。"""
    return [o for o in _role_options() if o[2] <= lv]


# 「人数构成」→ 该抽几位**女性**角色。
#
# 角色的性别位由 count 字段决定：`1boy` / `multiple boys` 是男方，由通用标签
# 承担（角色库里 2687 位全是女性，没有男性角色可指）。这里只回答"女方要几位"。
#
# ⚠️ 用**标签解析**而不是精确匹配整串 —— count 的选项以后可能增删，
#    精确匹配会静默退化成"总是 1 位"，而且不会报错。
GIRL_COUNT_BY_TAG = {
    "multiple girls": 3,      # 多人群交：3 位（2026-10-03 与用户确认）
    "orgy": 3,
    "2girls": 2,
}


def girl_count_for(count_en: str) -> int:
    """从人数标签推断该抽几位女性角色。"""
    tags = {t.strip().lower()
            for t in str(count_en or "").split(",") if t.strip()}
    for t, n in GIRL_COUNT_BY_TAG.items():
        if t in tags:
            return n
    # 单人 / 一男一女 / 一女多男 / 其他 —— 女方都是 1 位
    return 1


# ============================================================================
# 一之三之三、人数相容性（v3.41.0）
# ============================================================================
#
# 「标签判 L3」≠「画面 L3」。最典型的反例是 `1girl, solo` + `missionary`：
# 一个人做不了一对一正戏，模型只能画成模棱两可的样子 —— 这正是
# 「选了成人/过激，画面却明显不够」的一大来源。实测 L2/L3/L4 的 solo 样本里
# **25–35%** 带着一对一字段。
#
# 本轮只做**能明确判定**的那部分：
#   · 多人（角色数 ≥ 2）时清空一对一字段（见 apply_selected_roles）；
#   · 保底判定时，solo 场景**不拿** `n_act` / `n_cum` 当"已达标"的证据。
# 刻意**不改已经抽出来的内容** —— `fingering` / `dildo riding` 这类自指动作
# 在词库里与"被指交 / 被骑"共用同一个 tag，语义歧义，判错就会误删合法场景
# （场景库 `sc_msb_fingers_bed` 就是 solo + fingering）。留待单独一轮处理。
MULTI_CLEAR_FIELDS = ("n_act", "n_pos", "n_cum")

# 多人下**必须多男**才成立的选项（其余与人数无关，保留）。
MULTI_CLEAR_OPTIONS = {
    "n_theme": frozenset({"threesome", "foursome", "gangbang"}),
}

# solo 场景下**仍然成立**的一对一字段取值（自指类）。判"实际档位"时放行。
SOLO_OK_PARTNER_OPTS = frozenset({"masturbation", "fingering", "dildo riding"})
# 判"实际档位"时需要对人数做相容性检查的字段（n_pos 歧义太大，不参与）
PARTNER_GATED_FIELDS = ("n_act", "n_cum")


def count_has_partner(count_en: str) -> bool:
    """人数组合里有没有"对方"。solo / 双女为 False；群交（orgy）为 True。

    ⚠️ v4.2.1 把 `orgy` 加进来：`multiple girls, orgy` 明明是一群人，
    以前却判成"没有对方"—— 人数相容过滤会把 `bukkake` 这类群交向
    精液表现清空（实测点名 bukkake=3 时 3/20 丢失）。
    双女（`2girls, yuri`）仍为 False：异性行为字段的选项在双女下不成立，
    这是既有的刻意口径（yuri 场景用 `n_pos` / extra 表达）。
    """
    tags = {t.strip().lower()
            for t in str(count_en or "").split(",") if t.strip()}
    if "solo" in tags:
        return False
    return bool(tags & {"1boy", "2boys", "multiple boys", "hetero",
                        "group sex", "orgy"})


def count_satisfies(count_en: str, target: str) -> bool:
    """人数组合是否满足点名性行为所需的人数下限（v4.2.3）。

    ⚠️ 三个目标必须**只有这一处**判：`_one_pass` 的抬升、`apply_preset` 的
       count_lock 冲突、draw 末尾的角色联动复核，全调它。以前三处各写一遍，
       加 `COUNT_YURI` 时漏一处就会出现"抬了又被改回去"。
    """
    if target == COUNT_GROUP_SEX:
        return count_is_group(count_en)
    if target == COUNT_PARTNER:
        return count_has_partner(count_en)
    if target == COUNT_YURI:
        return is_yuri_count(count_en)
    return True


# 只有"三人及以上"才成立的元素。solo 与一男一女写它们都是自相矛盾 ——
# 实测（v3.41.0 首次验收）extreme 档 10 张里有 2 张抽到 `solo + threesome`，
# 其中一部分是**保底锚点**自己补上去的（L4 的 n_theme 只剩 3P/4P/群交/项圈四项）。
GROUP_ONLY_TAGS = frozenset({"threesome", "foursome", "gangbang",
                             "bukkake", "excessive cum", "multiple creampie"})

# ── v4.2.1：点名性行为时的人数下限 ──────────────────────────────────
# solo 下也成立的体位（其余 n_pos 选项默认"需要对方"）
SOLO_OK_POS_OPTS = frozenset({"spread legs", "bound wrists",
                              "bound arms", "head down"})
# 人数抬升的目标组合（都是 count 字段的真实选项原文，写错=静默失效）
COUNT_PARTNER = "1girl, 1boy, hetero"
# v4.4.1：穿戴式 / 扶她本质是**双人动作**（用户口径 10-30 00:27：这两条要
# 改成"更合理的场景交互动作"，交互对象 = 双女）。solo 下抽到它们只能画出
# "单人扶着腰站着戴道具"，没有任何交互 —— 所以点名时必须把人数抬到双女。
COUNT_YURI = "2girls, yuri"
YURI_TOY_TAGS = frozenset({"strap-on", "futanari"})
COUNT_GROUP_SEX = "1girl, multiple boys, group sex"

# ── v4.5.0：中性的「双女」人数写法（不写 yuri）────────────────────────
# 全年龄档不能出现 `yuri`（关系标签自 v4.5.0 起降到 1 档，L0 仍不可用），
# 但「画面里有两个女孩」这件事本身是全年龄内容。
#
# ⚠️ 为什么是光秃秃的 `2girls`，而不是旧写的 `2girls, multiple girls`：
#    `girl_count_for()` 先命中 `multiple girls`（→ 3 位），`is_yuri_count()`
#    也因为「非群交」这条判据把它判成 False —— 也就是说旧串会被下游读成
#    「三个人的群像」，L0 双女（v3.2.0 起的既有路径）因此一直带着一个
#    `multiple girls` 进正文。`2girls` 单独一个标签在两边都判对：女位 2、双女。
#
# ⚠️ 这个串**刻意不进人数随机池**：往 count 字段加第 7 个选项会改变
#    `rng.random() * len(opts)` 的映射，所有未锁人数的抽取都会重排 ——
#    零漂移对拍（check_v3_near ④）会全红。所以它只能作为**场景 count_lock**
#    使用（build_scenes.validate / check_v3_scenes 与这里共用这一个常量）。
#
# ⚠️ 别当成 `2girls, yuri` 的"降级替代"：两者是**按档位分工**的关系，
#    L0 用中性、L1 起写 yuri，判据只有档位一个。
COUNT_DUO_NEUTRAL = "2girls"
# 场景 count_lock 的额外合法值：**不进随机池**，只给场景钉人数用。
# 值 = 该锁的档位（全年龄可用）。
COUNT_LOCK_EXTRA = {COUNT_DUO_NEUTRAL: 0}


def count_lock_level(cl: str) -> int:
    """场景人数锁的档位（build 校验 / 自检 / 引擎写入三处共用）。

    ⚠️ `COUNT_LOCK_EXTRA` 里的中性写法不在 levels.json 中，`option_level`
       会返回 0 —— 那是巧合正确，不是定义。这里显式取值，三处才一个口径。
    """
    cl = str(cl or "").strip()
    if not cl:
        return 0
    if cl in COUNT_LOCK_EXTRA:
        return int(COUNT_LOCK_EXTRA[cl])
    return int(option_level("count", cl))


def count_is_group(count_en: str) -> bool:
    """人数组合是否是"三人及以上"。"""
    tags = {t.strip().lower()
            for t in str(count_en or "").split(",") if t.strip()}
    return bool(tags & {"multiple boys", "multiple girls", "orgy", "group sex"})


# ============================================================================
# 一之三之二、按作品 IP 限定随机角色（v3.10.0）
# ============================================================================
#
# 需求：「选一个作品 IP → 从该 IP 的角色里随机抽 → 每次运行都自动换人」。
#
# ⚠️ 为什么必须落到**引擎**而不是面板：
#    面板的 🎲 按钮把抽到的角色写进 `selected_roles`，那等于"抽一次就固定"
#    （随工作流保存、可复现）—— 它**做不到每次运行自动换**。
#    要"每次换"，抽取必须发生在**运行时**，于是引擎得知道"从哪个 IP 里抽"。
#    节点侧原本没有任何"角色池"控件，所以新增 `role_works`（作品 slug 列表）。
#
# 两条路径并存（优先级见 draw 里的角色段）：
#   ① 抽一次就固定 → 面板 🎲 → 写 `selected_roles`（v3.8.0，保留）
#   ② 每次自动换   → 面板「🔁」→ 写 `role_works`，**不写** selected_roles；
#                    配合 `seed = -1`（默认）每次排队都重抽。


def role_work_index() -> dict:
    """{角色 trigger 小写: 作品 slug}（缓存）。

    ⚠️ 只收**角色库**里的条目（2687 位）—— 与面板的作品筛选**同一口径**
       （面板也是拿 `work_id` 比角色库记录）。源 HTML 里那 221 条若不在
       角色库里（构建时被剔掉的男性 / 非女性判定），就没有作品归属，
       IP 过滤时会被排除；`draw` 会在 note 里把这件事说清楚。
    ⚠️ 键用 trigger 原文（小写）—— 与 `_role_options()` 产出的 `en` 同源，
       这样过滤时是精确匹配，不会因为中英文/别名差异漏掉。
    """
    if "role_work_index" not in _CACHE:
        m = {}
        for c in characters_data().get("characters", []) or []:
            trig = (c.get("trigger") or c.get("tag") or "").strip().lower()
            if trig:
                m[trig] = _work_slug(c.get("work", ""))
        _CACHE["role_work_index"] = m
    return _CACHE["role_work_index"]


def works_table() -> list:
    """作品表（含 slug / 英文名 / 中文名 / 角色数），给解析与面板用。"""
    return characters_data().get("works", []) or []


def resolve_works(raw) -> list:
    """把用户写的作品串解析成 slug 列表。

    接受 **slug**（`zenless_zone_zero`）/ **英文名**（`zenless zone zero`）/
    **中文名**（`绝区零`），大小写无关，分隔符 `|` / `;` / 换行 / 逗号都行。
    认不出来的 token 直接忽略（**不报错** —— 老工作流里可能是别的写法，
    报错会让整张图跑不起来；报告里会提示"认不出来"）。
    """
    if not raw:
        return []
    chunks = re.split(r"[|;\n,，]", str(raw))
    by_key = {}
    for w in works_table():
        for k in (w.get("id"), w.get("en"), w.get("zh")):
            k = str(k or "").strip().lower()
            if k:
                by_key.setdefault(k, w.get("id", ""))
    out = []
    for c in chunks:
        k = c.strip().lower()
        if not k:
            continue
        slug = by_key.get(k) or (k if k in {w.get("id") for w in works_table()} else "")
        if slug and slug not in out:
            out.append(slug)
    return out


def unknown_works(raw) -> list:
    """解析失败的 token（给报告提示用）。"""
    if not raw:
        return []
    known = {(str(w.get("id") or "")).lower() for w in works_table()}
    known |= {(str(w.get("en") or "")).lower() for w in works_table()}
    known |= {(str(w.get("zh") or "")).lower() for w in works_table()}
    return [c.strip() for c in re.split(r"[|;\n,，]", str(raw))
            if c.strip() and c.strip().lower() not in known]


def favorite_role_keys() -> set:
    """收藏（角色 tag）→ 候选池 trigger 的归一化 key 集合。

    收藏存的是**角色 tag**（`roxy_migurdia`），候选池里是 **trigger**
    （`roxy migurdia, mushoku tensei`），两边写法不同，必须桥一下：
      · 库内查得到的用库里的 trigger（`expand_roles` 同一口径）；
      · 查不到的退回 `_normalize_role_token`（`xxx_(作品)` → `xxx, 作品`）；
      · 再补一份"逗号前名字"，兼容源 HTML 里不带作品后缀的短 trigger。

    ⚠️ 不走缓存：收藏是用户数据，面板里刚点的星标要**立刻**影响下一次抽取。
    """
    favs = STORE.list_favorites()
    if not favs:
        return set()
    ci = char_index()
    keys = set()
    for t in favs:
        t = str(t or "").strip()
        if not t:
            continue
        rec = ci.get(t)
        trig = ((rec or {}).get("trigger") or "").strip() \
            or _normalize_role_token(t)
        for k in (trig, _normalize_role_token(t),
                  trig.split(",")[0], t.replace("_", " ")):
            k = (k or "").strip().lower()
            if k:
                keys.add(k)
    return keys


def role_pool_by_works(lv: int, works, favorites_only: bool = False) -> list:
    """档位过滤 **再** 叠加作品过滤（可选再叠加收藏过滤）后的角色候选池。

    v3.38.0：`favorites_only=True` 时只保留 `user_favorites.json` 里的角色。
    两个过滤可叠加（该作品 ∩ 我的收藏）；交集为空时由调用方决定怎么退化。
    """
    pool = list(role_pool(lv))
    if favorites_only:
        fav = favorite_role_keys()
        if not fav:
            return []
        pool = [o for o in pool
                if (o[1].strip().lower() in fav
                    or o[1].split(",")[0].strip().lower() in fav)]
    ws = set(works or ())
    if not ws:
        return pool
    idx = role_work_index()
    return [o for o in pool if idx.get(o[1].strip().lower()) in ws]


def work_pool_sizes(lv: int = 99) -> dict:
    """{作品 slug: 该作品在当前档位下可用的角色数} —— 面板显示用。"""
    out = {}
    for _zh, en, n in role_pool(lv):
        slug = role_work_index().get(en.strip().lower())
        if slug:
            out[slug] = out.get(slug, 0) + 1
    return out


def pick_random_roles(rng, lv: int = 99, n: int = 1, works=None,
                      favorites_only: bool = False) -> list:
    """从角色池随机抽 n 位**互不重复**的女性角色，返回 trigger 列表。

    未选角色时的兜底 —— 保证出图一定有角色（2026-10-03 与用户确认）。

    `works` 给了就只在这些作品的角色里抽（v3.10.0）。
    ⚠️ 池子不够 n 位时**有多少给多少**，不报错也不去别处借 ——
       借了就不是"这个 IP 的角色"了，那正是用户要的东西。调用方负责在
       池子为空时决定兜底（见 draw：回退全局池并写进报告）。

    ⚠️ 均匀随机，**不按人气加权**：候选池 2700+ 条，按人气加权会让冷门角色
       几乎永远出不来，而"什么都可能抽到"才是这个插件随机角色的意义。
    ⚠️ 用调用方传进来的 rng —— 这样 seed >= 0 时整体仍然逐字节可复现。
    ⚠️ 用 `rng.sample` 而不是抽 n 次：**同一位角色不能重复出现**
       （两个 `hakurei reimu` 拼在一起，模型会当成两个人）。
    """
    pool = role_pool_by_works(lv, works, favorites_only=favorites_only)
    if not pool or n <= 0:
        return []
    take = min(int(n), len(pool))
    return [pool[i][1] for i in rng.sample(range(len(pool)), take)]


def pick_random_role(rng, lv: int = 99) -> str:
    """抽 1 位。`pick_random_roles` 的单人快捷方式，保留给旧调用方。"""
    got = pick_random_roles(rng, lv, 1)
    return got[0] if got else ""


# ── v6.6.0：双女自动补位（用户 2026-11-07）────────────────────────────
#
# 场景：角色库**只选了 1 位**，却赶上双女人数（场景 `count_lock` 钉死
# `2girls, yuri`，或「人数构成」自己抽到 2girls）。
# 以前的结果：正文里只有一个角色 tag，模型为了凑够两个人，只能把**同一个人
# 画两遍** —— 两张一模一样的脸并排，既不是用户点的那个场景，也不是用户要的画面。
#
# 修法：按人数把角色位补齐。首选**同作品（同 IP）里的另一位女性**，
# 因为双女场景几乎都是"同世界观的两个熟人"；该 IP 只有她一位女性时
# （287 个作品里 119 个是这种小 IP）**回退全库随机**（用户 2026-11-07 拍板）。
#
# ⚠️ 只在「用户显式选了角色」这条路上补。系统自动随机那条路本来就会按
#    `girl_count_for(count)` 抽够人数（见 draw 的 auto 分支），再补就重复了。
# ⚠️ 必须排除已入列的 trigger：`hakurei reimu` 出现两次，模型照样画成一个人。


def pick_duo_partner(rng, lv: int, base_trigger: str, exclude=(),
                     favorites_only: bool = False, works=None) -> tuple:
    """给双女/多女阵容补一位女性角色。返回 `(trigger, mode)`。

    mode 三态：
      · `"same_work"` —— 同作品内另找到一位女性（首选）；
      · `"cross_work"` —— 该作品只有她一位女性 → 回退全库随机；
      · `""`          —— 池子被排除空了（正常不会发生），调用方保持原样。

    `works`（v6.6.2）是面板「🔁 随机池」解析出来的作品 slug 列表：
    给了就**先收进池内**，池内没有同作品时才用池内其他人 —— 补位不该
    绕过用户设的随机池。

    ⚠️ 用调用方传进来的 rng：seed >= 0 时整体仍逐字节可复现。
    ⚠️ `favorites_only` 只是"优先在收藏里找"，收藏池不够时**自动放开** ——
       否则"仅收藏"一开，小 IP + 冷门收藏就再也抽不出第二个人。
    """
    base = str(base_trigger or "").strip()
    excl = {str(t).strip().lower() for t in (exclude or []) if str(t).strip()}
    if base:
        excl.add(base.lower())
    pool = [o for o in role_pool(lv) if o[1].strip().lower() not in excl]
    if not pool:
        return "", ""
    if favorites_only:
        fav = favorite_role_keys()
        if fav:
            fav_pool = [o for o in pool
                        if o[1].strip().lower() in fav
                        or o[1].split(",")[0].strip().lower() in fav]
            if fav_pool:
                pool = fav_pool
    # v6.6.2：**随机池（role_works）也要管补位**。以前补位只认「她的作品」，
    # 用户把随机池设成 A 作品、手点的角色却属于 B 作品时，补来的第二个人
    # 来自 B —— 与"这批只要 A"的意图相反。现在先收进池内，池内没有同作品
    # 才取池内其他人；池内一个人都没有才回退全库（并写进报告）。
    if works:
        _ws = set(works)
        _idx = role_work_index()
        _in_pool = [o for o in pool
                    if _idx.get(o[1].strip().lower()) in _ws]
        if _in_pool:
            pool = _in_pool
    slug = role_work_index().get(base.lower(), "") if base else ""
    same = [o for o in pool
            if slug and role_work_index().get(o[1].strip().lower()) == slug]
    pick_from = same or pool
    tag = pick_from[rng.sample(range(len(pick_from)), 1)[0]]
    if same:
        return tag[1], "same_work"
    # 两种"回退"要分清（v6.6.1）：作品存在但只有她一位女性，与
    # "这个人根本不在库里（手填的原创角色）"是两件事，报告措辞不一样。
    return tag[1], ("cross_work" if slug else "unknown_work")


def _role_short(trigger: str) -> str:
    """`texas, arknights` → `texas`（报告里点名用，别把作品名重复念一遍）。"""
    return str(trigger or "").split(",")[0].strip()


# ============================================================================
# 一之三、选中角色（面板里点选的角色，抽取概率 100%）
# ============================================================================

ROLE_SEP = re.compile(r"[|;\n]")


def parse_selected_roles(raw) -> list:
    """把控件里的角色串拆成列表。

    ⚠️ 分隔符**只能是 `|` / `;` / 换行，绝不能用逗号**。
       danbooru 的**角色 tag** 里确实没有逗号，但**完整 trigger 有** ——
       `hakurei reimu, touhou` 就是一个角色。用逗号当分隔符会把它拆成
       「hakurei reimu」和「touhou」两个角色，人数标签随即从 2 位变成 4 位、
       误判成群像。这个 bug 被单元测试抓到过一次，别再改回去。
    """
    out = []
    for chunk in ROLE_SEP.split(str(raw or "")):
        t = chunk.strip()
        if t and t not in out:
            out.append(t)
    return out


def _normalize_role_token(t: str) -> str:
    """把 danbooru 风格的角色 tag 归一成本生态通用的 trigger 写法。

        ellen_joe_(zenless_zone_zero) → `ellen joe, zenless zone zero`
        hakurei_reimu                → `hakurei reimu`

    ⚠️ 为什么必须做：角色库里有 2687 条，但用户完全可能手填一个库外的 tag。
       下划线写法（`ellen_joe_(...)`）本身是合法 danbooru tag，模型也认，
       但和本生态（源 HTML / 造型台）的空格写法不一致，而且会撞上自检 F12
       （「标签格式：不应含下划线」）—— 在真机端到端里被抓到过一次。
    """
    t = str(t or "").strip()
    if not t:
        return ""
    m = re.match(r"^(.+?)_\(([^()]+)\)$", t)
    if m:
        name = m.group(1).replace("_", " ").strip()
        work = m.group(2).replace("_", " ").strip()
        return f"{name}, {work}" if name and work else name
    return t.replace("_", " ").strip()


def expand_roles(tokens) -> list:
    """角色 tag → 完整 trigger。

    库里查得到的用库里的 trigger（带核准过的作品名）；
    查不到的**归一化后透传**（允许手写自定义角色，但写法要干净）。

    `user_characters.json._meta.aliases` 里的旧 tag 也认：角色从「自定义」
    并进内置库时 tag 会变（`user_motoha_arai` → `arai_motoha`），而用户
    已保存的工作流里存的是旧 tag —— 没有这张别名表，那些工作流会静默变成
    「查不到 → 透传一条垃圾 tag」。
    """
    idx = char_index()
    aliases = char_aliases()
    out = []
    for t in tokens:
        t = str(t or "").strip()
        if not t:
            continue
        c = idx.get(t) or idx.get(t.lower())
        if not c:
            alt = aliases.get(t) or aliases.get(t.lower())
            if alt:
                c = idx.get(alt) or idx.get(alt.lower())
        trig = (c.get("trigger") if c else "") or _normalize_role_token(t)
        if trig and trig not in out:
            out.append(trig)
    return out


def headcount_tags(n: int, lv: int, current: str = "") -> str:
    """按选中角色数决定人数标签。返回空串 = 保持原样不动。

    ⚠️ 两位女性角色时的关系标签要**看档位**（v4.5.0 放宽到擦边档）：
       `yuri` 在判定表里与接吻同属「百合」分类（2 档）。本插件把
       「关系」与「行为」分开：`count` 字段的双女选项自 v4.5.0 起降到 **1 档**
       （见 anima_v4_overrides.LEVEL_DEMOTE），所以
         · L0（全年龄）→ 中性的 `2girls, multiple girls`（一条 yuri 都不写）
         · L1 起      → `2girls, yuri`
       `kiss` / `french kiss` 这些**行为**词仍在 2 档，不受这条影响 ——
       擦边档不会因为写了 yuri 就凭空出现接吻。

    3 位以上一律按「多人群像」处理，写 `multiple girls`：
    不会写 solo、也不会写 yuri（用户选 3 个人显然不是双人关系）。
    """
    if n <= 0:
        return ""
    if n == 1:
        cur = (current or "").lower()
        single_ok = ("1girl" in cur and "multiple" not in cur
                     and "2girls" not in cur and "1boy" not in cur)
        return "" if single_ok else "1girl, solo"
    if n == 2:
        if lv <= 0:
            return COUNT_DUO_NEUTRAL
        return COUNT_YURI
    return "multiple girls"


# ── 角色名输出：去作品 IP 名（v3.18.6）─────────────────────────────
# 用户需求：正文只写角色名，不再写作品名（`hu tao`，而不是 `hu tao, genshin impact`）。
# ⚠️ 例外是**重名角色**：同名但分属不同作品时（`fubuki` 有 3 部），去掉作品名
#    会被画成另一个同名角色，所以这类保留完整 trigger。
# ⚠️ 只影响**正文**：`state["role"]` 与报告里的 `role_full` 仍是完整 trigger ——
#    外貌对齐（APPEAR 表）与自检 F10 都靠完整 trigger 查表，不能动。

def role_name_works() -> dict:
    """{角色名（小写）: {作品 slug...}} —— 判断是否重名。"""
    if "role_name_works" not in _CACHE:
        idx: dict = {}
        for c in characters_data().get("characters", []):
            trig = str(c.get("trigger") or c.get("tag") or "").strip()
            if not trig:
                continue
            name = trig.split(",")[0].strip().lower()
            work = str(c.get("work") or "").strip().lower()
            if not work:
                parts = [t.strip().lower() for t in trig.split(",")]
                work = parts[1] if len(parts) > 1 else ""
            idx.setdefault(name, set())
            if work:
                idx[name].add(work)
        _CACHE["role_name_works"] = idx
    return _CACHE["role_name_works"]


def role_needs_work(trigger: str) -> bool:
    """这个 trigger 是否必须保留作品名（同名跨作品的角色）。"""
    trig = str(trigger or "").strip()
    if not trig:
        return False
    name = trig.split(",")[0].strip().lower()
    return len(role_name_works().get(name, ())) > 1


def role_output_name(trigger: str) -> str:
    """输出用的角色名：默认只留名字；重名角色保留完整 trigger。"""
    trig = str(trigger or "").strip()
    if not trig:
        return ""
    if role_needs_work(trig):
        return trig
    return trig.split(",")[0].strip()


def strip_role_works(tags, state: dict) -> list:
    """v5.0.11 起**不再剔除作品名**，直接原样返回。

    ⚠️ 这个函数是 v3.18.6「正文只写角色名、不写作品 IP」的实现，也是
       用户「部分人物识别不了」的**真正根因**：
       Anima 官方明确要求「Characters should (think "must" in this case) be
       **follow by their series/copyrights** (think of these like anchors),
       follow by their appearances」—— 把作品名删掉等于抽掉了锚点。

    保留函数是为兼容外部调用点，行为改成直通。正文里的作品名现在由
    `role_output_tags()` 以独立 tag 的形式给出（`texas, arknights`）。
    """
    return tags


def role_output_text(state: dict) -> str:
    """state 里的角色串 → 正文用的版本（重名才带作品名）。"""
    trigs = state.get("_role_triggers")
    if not trigs:
        return str(state.get("role") or "")
    return ", ".join(t for t in (role_output_name(x) for x in trigs) if t)


# ── 正文角色 tag：danbooru 正式写法（v5.0.10）────────────────────────────
# 用户 2026-11-03：
#   「更新角色库所有角色标签格式为 texas\\(arknights\\) 前面是角色，后面是作品。
#     之前的格式为 texas, arknights。部分人物识别不了。」
#
# 实测（seed=42）确认过问题：
#   `texas_(arknights)`         → 正文只写 `texas`（作品名被 role_output_name 丢掉）
#   `fubuki_(kancolle)`         → 正文写 `fubuki, kantai collection`
#   danbooru 模型认的是 `texas_(arknights)`，裸名字与逗号写法都识别不稳。
#
# 口径（用户拍板）：
#   ① **只改输出层** —— 库内 trigger / 收藏 / 服装预设键 / 别名表一律不动，
#      这样旧工作流、旧收藏、2318 条服装预设全不受影响；
#   ② 作品名以 danbooru 官方 API 逐条核验（`works.id` 不可靠：
#      `goddess_of_victory_nikke` 查不到，正式名带冒号）；
#   ③ 括号是形态/职阶的角色输出**形态+作品双层**
#      （`houshou_marine_(1st_costume)_(hololive)`）—— 用户明确选择，
#      这不是合法 danbooru tag，映射表里标 `layered` 以便回溯。
#
# ⚠️ 映射表是**构建产物**（tools/build_char_output_tags.py 生成）。
#    文件不存在 / 条目没命中时**回退旧逻辑**（role_output_name），
#    即"零映射 = 零漂移"，不会因为忘跑构建脚本就出怪 tag。

_CHAR_OUTPUT_PATH = os.path.join(_HERE, "presets", "char_output_tags.json")


def char_output_tag_map() -> dict:
    """{trigger 原文: 正文 tag}（缓存）。读不到就返回空表（= 全走旧逻辑）。"""
    if "char_output_tags" not in _CACHE:
        idx = {}
        try:
            with open(_CHAR_OUTPUT_PATH, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for trig, rec in (d.get("tags") or {}).items():
                out = str((rec or {}).get("out") or "").strip()
                if trig and out:
                    idx[trig] = out
        except Exception:
            idx = {}
        _CACHE["char_output_tags"] = idx
    return _CACHE["char_output_tags"]


def role_output_tag(trigger: str) -> str:
    """单条 trigger → 正文 tag。

    有映射用映射（`texas, arknights` → `texas_(arknights)`）；
    没映射回退旧行为（重名才带作品名、逗号写法），保证增量可控。
    """
    trig = str(trigger or "").strip()
    if not trig:
        return ""
    m = char_output_tag_map()
    hit = m.get(trig) or m.get(trig.lower())
    if hit:
        return hit
    return role_output_name(trig)


def role_output_tags(state: dict) -> list:
    """本轮要写进正文的角色 tag 列表（按 _role_triggers 顺序、去重）。"""
    trigs = state.get("_role_triggers") or []
    out = []
    for t in trigs:
        tag = role_output_tag(t)
        if tag and tag not in out:
            out.append(tag)
    return out


def char_output_tag_set() -> set:
    """正文角色 tag 的**全文集合**（缓存，已按逗号拆开）。

    映射表里每条是 `texas, arknights` 这种「角色 + 作品」两段式，
    这里拆成 `{texas, arknights, ...}` 供自检 / 互斥 keep 使用。
    """
    if "char_output_tag_set" not in _CACHE:
        s = set()
        for v in char_output_tag_map().values():
            for t in str(v or "").split(","):
                t = t.strip().lower()
                if t:
                    s.add(t)
        _CACHE["char_output_tag_set"] = s
    return _CACHE["char_output_tag_set"]


def apply_selected_roles(state: dict, triggers, lv: int,
                         rewrite_count: bool = True) -> list:
    """把选中的角色写进状态。返回说明列表。

    ⚠️ 选中即 100% 抽取 —— **不受权重影响**。用户显式点了角色，
       这一条就该压过「字段权重 0」；权重面板负责的是"想要什么倾向"，
       不是"否决我刚刚点的东西"。真不想用就点「清除已选角色」。

    rewrite_count 的两种用法（v3.2.0 新增）：
      · **True**（用户显式选中）—— 按选中人数改写人数标签。
        选了 2 位就是双女，人数当然得跟着走。
      · **False**（未选角色、由系统随机抽的）—— **不碰人数标签**。
        这时人数是 count 字段自己抽出来的组合（一男一女 / 双女 /
        一女多男 / 多人群交 …），角色只是"填进这个组合的角色位"。
        早先版本在这里无条件改写，把所有组合都压成了单人 —— 那是 bug。
    """
    triggers = [t for t in (triggers or []) if t]
    n = len(triggers)
    if not n:
        return []

    notes = []
    state["role"] = ", ".join(triggers)
    # 正文输出用（去作品名）要按 trigger 边界处理，所以把原始列表留在 state 上。
    # 下划线键不参与 collect / 不写进正文。
    state["_role_triggers"] = list(triggers)

    if rewrite_count:
        head = headcount_tags(n, lv, state.get("count", ""))
        if head:
            state["count"] = head
            notes.append(f"按选中角色数（{n} 位）改写人数标签 → `{head}`")

        if n >= 3:
            notes.append("多人群像模式：已写 `multiple girls`，不再使用双人关系标签")
        elif n == 2 and lv <= 0:
            notes.append("当前档位是全年龄：双人关系用中性的"
                         f" `{COUNT_DUO_NEUTRAL}`（`yuri` 自 v4.5.0 起属擦边档）")
    else:
        notes.append(f"人数组合由「人数构成」决定，已填入 {n} 位角色"
                     "（未改写人数标签）")

    # 多人时，下面这些字段的语义会和人数打架，清掉更安全。
    # ⚠️ 判据是 n（角色数），与 count 无关：
    #    count 是 `multiple girls, orgy` 但只抽到 3 位时同样要清。
    #
    # ⚠️⚠️ v3.41.0 改正一刀切：原来连 `n_orgasm` / `n_fluid` / `n_theme` 一起清，
    #    实测 L3 档的多人样本 **82% 恒落 L2**（占全部 L3 抽取的 24%）——
    #    因为 L3 能用的 count 只有 `双女`（判 L2），而所有 L3 载体都被清光。
    #    真正的判据是「这个选项成不成立」，不是"这个字段是不是单人向"：
    #      · `n_act` / `n_pos` / `n_cum`：一对一正戏，需要唯一的伴侣 → 多人下清空；
    #      · `n_orgasm`（ahegao / 失神 / 潮吹）：**个人状态**，几个人都成立 → 保留；
    #      · `n_fluid`（爱液 / 唾液 / 汗 / 泪）：局部状态，与人数无关 → 保留；
    #      · `n_theme`：只有 `3P` / `4P` / `群交` 是"必须多男"的，其余 17 项
    #        （绳缚 / 蒙眼 / 口塞 / 玩具 / 触手 / 扶她 / 乳夹 …）与人数无关 → 保留。
    #    实测放宽后 L3 达标 59% → 67%（"只清 act/pos/cum"的口径）。
    # v6.6.2：判据从「角色数」改成「画面里实际有几个人」——
    #   `girl_count_for(count)` 才是人数真相。关掉双女补位时会出现
    #   「1 位角色 + `2girls, yuri`」，以前不清空 → 一个女生做一对一异性
    #   正戏，语义自相矛盾；而 `1girl, 1boy` 的 solo 场景 girl_count 仍是 1，
    #   不会被误清（异性正戏在单人双人框里都成立）。
    _people = max(int(n), girl_count_for(state.get("count", "")))
    if _people >= 2:
        # v6.6.1：**显式 > 规则**。用户把某个选项点到 3.0（必现）时，这条
        # 清空会把它静默吃掉 —— 2026-11-07 审查实测：双女场景 + 点名
        # `n_act` 3.0，补位成两人后正文里没有该体位，报告只有一句
        # "已清空一对一正戏字段"。现在点名项**保留**并如实写进报告，
        # 让用户自己判断"两个女生做一对一正戏"成不成立。
        _forced_keys = {f[0] for f in (state.get("_forced_tags") or ())}
        _kept = []
        for k in MULTI_CLEAR_FIELDS:
            if not state.get(k):
                continue
            if k in _forced_keys:
                _kept.append(k)
                continue
            state[k] = ""
        _opt_kept = []
        for k, vals in MULTI_CLEAR_OPTIONS.items():
            if str(state.get(k) or "").strip().lower() not in vals:
                continue
            if k in _forced_keys:
                _opt_kept.append(str(state.get(k) or ""))
                continue
            state[k] = ""
        if (_kept or _opt_kept) and not state.get("_multi_conflict_noticed"):
            state["_multi_conflict_noticed"] = True
            _lab = {"n_act": "性行为", "n_pos": "受方动作", "n_cum": "精液表现"}
            _bits = [_lab.get(k, k) for k in _kept] + _opt_kept
            notes.append("⚠️ 多人（%d 位角色 / 画面 %d 人）与点名项冲突，但按「显式优先」保留：%s"
                         " —— 画面可能画不出这一项，要严格成立请改成单人场景"
                         % (n, _people, "、".join(_bits)))
        if state.get("_cleared_for_multi") is None:
            state["_cleared_for_multi"] = True
            if not (_kept or _opt_kept):
                notes.append("多人模式：已清空一对一正戏字段（体位 / 受方动作 / 精液）")

    return notes


def option_level(field_key: str, en: str) -> int:
    """选项档位 = max(判定表结果, 段下限, 逐选项上抬)。

    ⚠️ 这里**再算一次段下限 / 上抬**，不是重复劳动：
       磁盘上的 anima_v4_levels.json 可能比 overrides 旧（用户改了
       SECTION_FLOOR 或 LEVEL_RAISE 但忘了重跑分类脚本），只信 JSON 会让
       「SFW 档抽出阴蒂」或「SFW 档抽出第一人称下体视角」那两个漏洞复活。
       三层都算，最多是幂等的。

    ⚠️ 上抬只走 `raise_`（只升不降）。**下调（LEVEL_DEMOTE）刻意不在这里再算一次** ——
       下调是"判定表在某个字段里看错了上下文"，只有分类脚本按 (字段, 选项)
       精确落盘才安全；运行时按字段再算一遍会和 `_calibrated_tags()` 的
       "已登记白名单"语义打架（F17 靠白名单放过 wet hair，这里要是把它降成 0，
       白名单就再也解释不清为什么它不在超档名单里）。

    ⚠️ n_canon 特殊：它的"选项"是**预设 id**（c1… / sc_*），档位不在
       levels.json 的 option_levels 里，得走 `canon_level()`。不特判的话
       它恒为 0 —— F15（档位准入）就再也拦不住"被强制指定的超档预设"。
    """
    if field_key == "n_canon":
        return canon_level(en)
    base = int(levels().get("option_levels", {}).get(field_key, {}).get(en, 0))
    sec = _field_section().get(field_key)
    if sec:
        base = max(base, section_floor(sec))
    base, _why = raise_(field_key, en, base)
    return base


def canon_level(preset_id: str) -> int:
    """预设档位：**场景搭配库自带 level 优先**，否则查法典档位表。

    ⚠️ 场景库的 level 由 `tools/build_scenes.py` 按内容派生（max 各标签档位），
       不经过 `SECTION_FLOOR['canon']=2` —— 那条段下限只适用于法典那 14 组
       （它们设计上就是 NSFW 场景）。SFW 场景搭配因此能在全年龄档可用。
    """
    sc = scene_index().get(str(preset_id or ""))
    if sc:
        return int(sc.get("level", 0))
    return int(levels().get("canon_levels", {}).get(preset_id, 0))


def section_max_level(sec_id: str) -> int:
    return int(levels().get("section_max", {}).get(sec_id, 0))


# ============================================================================
# 一之四、场景搭配预设库（v3.9.0）
# ============================================================================
#
# 「一键设置场景搭配」= 把 姿势 / 场景 / 表情 / 动作 的**固定组合**一次套用，
# 套用到的字段不再参与随机。
#
# 为什么独立成文件（而不是继续往源 HTML 的 canon 里塞）：
#   · pools.json 是「源 HTML 的零漂移导出物」，`check_v3_pools.py` 靠逐字节
#     对拍守住它。塞 33 个跨槽位预设进去，那份对拍立刻失效。
#   · 场景库的每条预设**自带档位**（由 tools/build_scenes.py 按内容派生），
#     与"法典那 14 条固定 ≥2 档"是两套来源，混在一起后档位口径会打架。
#   这和角色库独立成 anima_v4_characters.json 是同一个理由。
#
# 与法典场景的关系（**同一套机制，两条来源**）：
#   `canon()` 把两边合并成一张 `{预设 id: {字段: 值}}` 表，
#   `apply_preset()` 完全不用改 —— 场景库的预设与法典预设走**同一条**填充路径，
#   因此自动继承三道闸门（档位 / 权重 / 锁定）与 v3.8.0 的成品自动修复。

_SCENES_PATH = os.path.join(_HERE, "presets", "anima_v4_scenes.json")

# 预设**绝不允许**代填的字段：人数与角色是"地基"。
# 预设抢 count 会让「选了双女却写成单人」这类矛盾复现（v3.3.0 修过一次），
# 抢 role 会让角色库的选择失效。构建脚本会校验这条，这里再兜一层。
SCENE_FORBIDDEN_FIELDS = frozenset({"count", "role"})


def scenes_data() -> dict:
    """场景搭配预设库原文（缺席时返回空壳，功能优雅降级）。"""
    if "scenes" not in _CACHE:
        data = {"_meta": {}, "groups": [], "scenes": []}
        if os.path.exists(_SCENES_PATH):
            try:
                data = _load_json(_SCENES_PATH)
            except Exception:
                data = {"_meta": {}, "groups": [], "scenes": []}
        _CACHE["scenes"] = data
    return _CACHE["scenes"]


def has_scene_library() -> bool:
    return bool(scenes_data().get("scenes"))


def scene_groups() -> list:
    return scenes_data().get("groups", [])


_SCENE_ASPECT_PATH = os.path.join(_HERE, "presets", "anima_v4_scene_aspects.json")


def scene_aspects() -> dict:
    """场景推荐画幅：`{id: {"a": "portrait"|"landscape"|"both", "why": str}}`。

    v5.0.1（用户 2026-11-01）：「场景搭配内场景根据构图合理性增加推荐竖屏 /
    横屏，如果都可以就标注通用；需要在场景 UI 看得到」。

    ⚠️ 数据**不在** `anima_v4_scenes.json` 里 —— 那是 `tools/build_scenes.py`
       的产物，重跑构建会整份覆盖，标在里面的画幅会静默消失。
       这份是 `tools/build_scene_aspects.py` 生成的**旁挂映射**，可手工改。

    文件缺席 / JSON 坏了 → 返回空 dict，调用方一律回退 `both`
    （只影响 UI 徽标与筛选，**绝不影响出图**）。
    """
    if "scene_aspects" not in _CACHE:
        out = {}
        try:
            with open(_SCENE_ASPECT_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            for k, v in (raw.get("aspects") or {}).items():
                a = str((v or {}).get("a") or "").strip().lower()
                if a not in ("portrait", "landscape", "both"):
                    a = "both"
                out[str(k)] = {"a": a, "why": str((v or {}).get("why") or "")}
        except Exception:
            out = {}
        _CACHE["scene_aspects"] = out
    return _CACHE["scene_aspects"]


def scene_aspect(preset_id: str) -> str:
    """单个场景的推荐画幅；没标注的按 `both`（= 竖横都行）。"""
    return (scene_aspects().get(str(preset_id)) or {}).get("a") or "both"


def scene_list() -> list:
    return scenes_data().get("scenes", [])


def scene_index() -> dict:
    """{预设 id: 场景记录}。"""
    if "scene_index" not in _CACHE:
        _CACHE["scene_index"] = {s["id"]: s for s in scene_list()}
    return _CACHE["scene_index"]


def scene_preset(preset_id: str) -> dict:
    """预设 id → apply_preset 能吃的那种 dict（`{字段: 值}` + `n_extra`）。

    ⚠️ 与法典预设同形状 —— 上层（canon / apply_preset）不需要知道
       "这条是法典的还是场景库的"。
    """
    sc = scene_index().get(str(preset_id or ""))
    if not sc:
        return {}
    out = {k: v for k, v in (sc.get("tags") or {}).items()
           if k not in SCENE_FORBIDDEN_FIELDS and v}
    extra = str(sc.get("extra") or "").strip()
    if extra:
        out["n_extra"] = extra
    # v4.4.2：场景级自然语言（可选键 `scene_nl`）。
    # ⚠️ 键名**不能叫 `nl`** —— 词库里已经有 `nl` 字段（⑫ 自然语言补位，
    #    label「自然语言补充(英文短句,置于末尾)」），用同名会被 `field_by_key("nl")`
    #    认成真实字段写进 state["nl"]，实测正文里反而看不到（被密度裁剪削掉），
    #    而且报告里会冒出一条「自然语言补充」的假填充记录。
    nl = str(sc.get("scene_nl") or "").strip()
    if nl:
        out["scene_nl"] = nl
    return out


def scene_says_no_role(preset_id) -> bool:
    """场景是否声明「这一条的画框里没有脸」（`no_role`，v3.32.0）。

    用于第一人称自我视角：观众就是本人在低头看自己，画面里根本不该出现
    角色 tag（`herta` / `siege` …）—— 那会把镜头从"自己的身体"拉回一张脸。

    ⚠️ 只认布尔 `true`。手写 "false" / "0" 这类字符串一律当假：
       v3.11.0 踩过这个坑（布尔开关被 `str()` 化之后永远为真，开关关不掉）。
    """
    rec = scene_index().get(str(preset_id or "")) or {}
    return rec.get("no_role") is True


def scene_random_variants(preset_id: str) -> list:
    """预设的「候选构图变体」，按**池**返回：`[{key, label, items:[{zh, extra}]}]`。

    v3.24.0 只有单池（`random_variants` 是数组，抽一条）；
    v3.27.0 扩成**多池**（`random_variants` 是对象，每个键一个池，各自独立抽一条）——
    因为有些条目需要"姿势随机 × 道具随机"两个**正交**的维度
    （护士查房：4 种姿势 × 3 种医疗道具 = 12 种组合），单池只能写成 12 条全排列。

    两种写法都认：
      · 数组：`[{zh, extra}, ...]`            → 单池（key = "main"）
      · 对象：`{"pose": [...], "tool": [...]}` → 多池（每个键一个池）
    池的值也可以写成 `{"label": "姿势", "items": [...]}`，给面板显示用。

    ⚠️ 没有 `random_variants` 的条目**一次 rng 都不消耗**（直接返回空表）。
    """
    sc = scene_index().get(str(preset_id or ""))
    if not sc:
        return []
    raw = sc.get("random_variants")
    if not raw:
        return []

    def _items(v):
        out = []
        for it in (v or []):
            if not isinstance(it, dict):
                continue
            ex = str(it.get("extra") or "").strip()
            if ex:
                rec = {"zh": str(it.get("zh") or "").strip() or "变体", "extra": ex}
                # v4.4.5：变体也能带自然语言（见 apply_preset 的说明）
                #   `nl`       = 整句，**替换**场景的 `scene_nl`（主维度：姿势/器械/口部形态）
                #   `nl_extra` = 整句，**追加**在场景 `scene_nl` 之后（副维度：道具/手部动作）
                nl = str(it.get("nl") or "").strip()
                if nl:
                    rec["nl"] = nl
                add = str(it.get("nl_extra") or "").strip()
                if add:
                    rec["nl_extra"] = add
                out.append(rec)
        return out

    pools = []
    if isinstance(raw, list):
        items = _items(raw)
        if items:
            pools.append({"key": "main", "label": "", "items": items})
    elif isinstance(raw, dict):
        for k, v in raw.items():
            label, body = "", v
            if isinstance(v, dict) and not isinstance(v.get("items"), type(None)):
                label = str(v.get("label") or "")
                body = v.get("items") or []
            items = _items(body)
            if items:
                pools.append({"key": str(k), "label": label, "items": items})
    return pools


def scene_random_fields(preset_id: str) -> dict:
    """预设的「候选随机字段」：`{字段 key: [候选值, ...]}`（v3.22.0）。

    为什么需要它：`tags` 是**写死一个值**，所以一条特写的表情 / 口部状态
    每次都一样 —— 用户口径是「同一条里也要能随机抽到几种状态与表情」。
    又不愿意为此把条目复制成 N 条（既难维护，又会在面板里刷屏），
    于是给条目一个可选的 `random_fields`：套用时**从候选里抽一个**。

    ⚠️ 只对场景库（`sc_*`）生效，法典预设没有这个键。
    ⚠️ 候选**照样过档位闸门**（`_field_ok_under`）与权重 / 锁定闸门 ——
       与 `tags` 里的写死值走完全相同的准入判定，不是后门。
    """
    sc = scene_index().get(str(preset_id or ""))
    if not sc:
        return {}
    raw = sc.get("random_fields") or {}
    out = {}
    for k, cands in raw.items():
        if k in SCENE_FORBIDDEN_FIELDS:
            continue
        vals = [str(v) for v in (cands or []) if str(v or "").strip()]
        if vals:
            out[str(k)] = vals
    return out


def scene_options(lv: int = 99, include_manual: bool = False) -> list:
    """按档位过滤的场景候选 [(中文名, id, level), ...]。

    ⚠️ v3.18.0：`manual_only` 的预设（「🔍 特写搭配」那 32 条）**默认排除** ——
       它们只允许用户显式套用，不进随机候选池。理由：`n_canon` 的默认字段权重是
       1.0，随机抽取本来就会命中场景预设；把新预设直接塞进同一个池子，
       等于改变现有随机输出的分布（"不得影响原有行为"就成了一句空话）。

    `include_manual=True` 只给两处用：
       · `pools_payload()` 列候选（面板权重树要能看到它们）；
       · `draw()` 里给"已经显式套用（选项权重 ≥ 3.0）"的那一条开一条通道。
    """
    return [(s.get("zh") or s["id"], s["id"], int(s.get("level", 0)))
            for s in scene_list()
            if int(s.get("level", 0)) <= lv
            and (include_manual or not s.get("manual_only"))]


def manual_scene_options(lv: int = 99) -> list:
    """只取 `manual_only` 的预设 [(中文名, id, level), ...]（v3.18.0）。"""
    return [(s.get("zh") or s["id"], s["id"], int(s.get("level", 0)))
            for s in scene_list()
            if s.get("manual_only") and int(s.get("level", 0)) <= lv]


def _canon_pool_for_draw(pool: list, lv: int, weights: dict) -> list:
    """给 `n_canon` 的候选池补上"被显式套用"的 manual_only 预设（v3.18.0）。

    准入条件只有一条：该选项的**权重顶到 3.0**（面板「一键套用」就是这么写的）。
    默认权重 1.0 → 一条都不补 → 随机路径与 v3.17.2 逐字节一致。
    """
    have = {en for _zh, en, _n in pool}
    extra = []
    for zh, sid, n in manual_scene_options(lv):
        if sid in have:
            continue
        if option_weight(weights, "n_canon", sid) >= WEIGHT_MAX:
            extra.append((zh, sid, n))
    return pool + extra if extra else pool


# ── 档位分层抽场景（v3.40.0）───────────────────────────────────────────
#
# 改之前的行为：`n_canon` 的候选是「canon_level <= lv」的**整套并集**、逐条等权。
# 低档条目在数量上压倒性多，于是"选了什么档却抽不到该档场景"——
# 实测（600 抽/档，n_canon 权重顶格）：擦边档 65% 是全年龄场景、成人档 57%、
# 过激档 44%、极端档 42% 都是 L0，而 L4 自己只有 5%。
#
# 本功能把候选按档位**切成两层**（正好 == lv / 低于 lv），用一个**派生骰子**
# 决定本轮走哪一层：
#   · 命中率 p 的那一半 → 只在「== lv」里抽：语义明确，不受池子大小稀释；
#   · 1-p 那一半       → 只在「< lv」里抽：低档兜底，画面不被同一批高档场景锁死。
#
# ⚠️ 为什么不"保留全池再给该档加权"：那样总体命中率 = p + (1-p)×低档占比，
#    擦边档会算出 67% 而不是 50%，用户看到的数字与口径对不上。
SCENE_LEVEL_MATCH_P = 1.0            # 命中率：v3.42.0 起 **1.0 = 锁档**（只在本档里抽）
SCENE_LEVEL_MATCH_P_SMALL = 1.0      # 保留常量位；不再因池子小而降档
SCENE_LEVEL_MIN_POOL = 15            # 「太少」的判据（现在只用于报告文案）
SCENE_ALWAYS = False                 # v3.42.0 的旧开关；**v5.0.1 起不再生效**（保留常量位便于回滚）
SCENE_TARGET_APPEAR = 0.60           # v5.0.1 **已废弃**：概率抽签被明确开关取代
SCENE_APPEAR_P = 0.12                # v5.0.1 **已废弃**：见 draw 里 n_canon 分支的说明
# ⚠️ v5.0.1（用户 2026-11-01）：「取消掉抽到场景搭配的 60% 概率规则」——
#    `draw(scene_pick=...)` 现在是唯一入口：勾上 = 必出，不勾 = 一条都不出。
#    上面三个常量**全部不再被读取**，留着是为了回滚与对照旧文档，
#    不要在别处引用它们（会被 check_v3_scenelevel 的零漂移段当成活的开关）。
# ⚠️ 这个数不能当成"出现率"来读。v3.42.1 起 best-of-8 是**场景优先**排序
#    （先看本轮有没有场景，再看标签数），所以最终出现率 ≈ 1-(1-p)^有效轮数，
#    8 轮下来 p=0.12 → 实测 **58.3–60.8%**（两段不同 seed 各 120 抽，收藏模式）。
#    标定表（150 抽/档 × 4 档，收藏模式）：0.08→44.7 / 0.09→48.0 / 0.10→52.5 / 0.11→55.8 / 0.12→59.4
#    ⚠️ 旧标定（v3.42.0 的 0.67/0.72）已作废：那时只有**第 1 轮**的抽签真正生效，
#      同一批 seed 上设 0.72 只能兑现 50%，这也是 v3.42.1 改排序的原因。
#    想让"出现率 = 100%"，把 `SCENE_ALWAYS=True`（每轮必抽）。
#    改这个数必须重跑 tests/check_v3_scenelevel.py 的 ⑦ 段（它卡出现率）。


def scene_level_match_p(lv: int, exact_n: int) -> float:
    """本档的命中率 = 「抽到场景时，它在不在本档」。

    v3.42.0 取 1.0（**锁档**）：用户要求"抽到的场景搭配等级必须与所选等级相同"，
    因此不再保留低档兜底那一层。想要回一点多样性（10% 低档），把它调成 0.9 即可 ——
    代码里 `lower` 分支仍完整保留，改一个数就回来了。

    ⚠️ 「有没有场景」是另一个开关 `SCENE_APPEAR_P`，两者相乘才是
       「每张图抽到本档场景」的总概率（v3.42.1 起 1.0 × 0.72 ≈ 66%）。
    """
    if lv <= 0:
        return 0.0
    return SCENE_LEVEL_MATCH_P


def scene_level_layered(pool: list, lv: int, weights: dict, coin: float):
    """按档位分层收敛 `n_canon` 的候选池。返回 `(新池, 报告说明)`。

    四种**不干预**的情形（原池原样返回，`coin` 白给）：
      · `lv <= 0`（SFW 档池里本来就全是 L0，分层无意义）；
      · 池子只有一条；
      · 该档或更低档有一侧是空的（分层会把池子清空）；
      · **有显式套用**（选项权重 ≥ 3.0）—— 那是用户在面板里点的，
        优先级高于本功能，不许被分层改写。
    """
    if lv <= 0 or len(pool) <= 1:
        return pool, None
    for _zh, en, _n in pool:
        if option_weight(weights, "n_canon", en) >= WEIGHT_MAX:
            return pool, None
    exact = [it for it in pool if it[2] == lv]
    lower = [it for it in pool if it[2] < lv]
    if not exact or not lower:
        return pool, None
    p = scene_level_match_p(lv, len(exact))
    if coin < p:
        return exact, (f"场景档位匹配：本轮只在 L{lv} 的 "
                       f"{len(exact)} 条里抽（命中率 {p:.0%}）")
    return lower, (f"场景档位匹配：本轮走低档兜底（L0–L{lv - 1} 共 "
                   f"{len(lower)} 条，{1 - p:.0%}）")


# ============================================================================
# 二、档位闸门
# ============================================================================

# 等级 key -> 数字。any（不限）按顶格 4 处理 —— 与造型台的 NUM_OF 同口径。
LEVEL_NUM = {k: min(v, N.NSFW_MAX) for k, v in N.TARGET_NUM.items()}
LEVEL_KEYS = tuple(N.LEVELS)
LEVEL_ZH = dict(N.LEVEL_ZH)


def resolve_level(value, default="suggestive") -> str:
    return N.resolve_level(value, default)


def level_num(value, default="suggestive") -> int:
    return LEVEL_NUM[resolve_level(value, default)]


def level_label(value) -> str:
    key = resolve_level(value)
    if key == "any":
        return "不限"
    return f"{LEVEL_NUM[key]} · {LEVEL_ZH[key]}"


def eligible_options(field_key: str, lv: int) -> list:
    """按档位收敛候选池。返回 [(zh, en, num), ...]。

    ⚠️ 这是**唯一的准入判定点**。权重只能在这个池子里加权，
       永远不能把超档项拉回来 —— 这是「低档绝不越界」的硬保证。

    角色字段是例外：它的候选来自独立的角色库（2000+ 条），
    不走 pools.json 的 opts，所以在这里分流。
    """
    if field_key == "role":
        return list(role_pool(lv))

    _sec, fld = field_by_key(field_key)
    if not fld:
        return []
    opts = list(fld.get("opts", []))
    # ⚠️ n_canon 的候选来自**两处**：源 HTML 的法典 14 组（在 pools.json 里）
    #    + 场景搭配库（独立文件，自己带档位）。这里现场合并 ——
    #    与 role 字段「源 HTML 的 221 条 + 角色库」是同一个套路。
    if field_key == "n_canon":
        opts = [(zh, en) for zh, en in opts] + \
               [(zh, sid) for zh, sid, _lv in scene_options(99)]
    out = []
    for zh, en in opts:
        if field_key == "n_canon":
            n = canon_level(en)
        else:
            n = option_level(field_key, en)
        if n <= lv:
            out.append((zh, en, n))
    return out


def section_floor_level(sec_id: str) -> int:
    """段级保底下限（`section_floor`）—— 段内**最低**档位。"""
    return int(levels().get("section_floor", {}).get(sec_id, 0))


def section_allowed(sec_id: str, lv: int) -> bool:
    """段级闸门：段里**至少有一个** ≤ 目标档的选项才允许。

    ⚠️⚠️ v3.14.0 从 `section_max <= lv` 改成 `section_floor <= lv`。
       原判据对**混合档位的段**会误伤：`pose_sfw` 段本来只有 L0 选项
       （max=0，于是恒允许），v3.14.0 往里加了 L1/L2 的「暗示姿势」后
       max 变成 2 —— 原判据会让 **SFW 档整段消失**（姿势一个都抽不到）。
       换成"段内最低档"后语义才对：段里只要还剩低档选项就该允许，
       至于**具体抽哪一条**由 `eligible_options()` 的逐选项闸门负责。

    注意「允许」不等于「整段开放」—— 逐选项闸门照样生效。
    纯文本段（nl_block）没有标签，一律允许（手写内容由自检兜底）。
    """
    return section_floor_level(sec_id) <= lv


# ============================================================================
# 三、三层权重
# ============================================================================
#
# 语义（写进 README，改动必须同步）：
#   段权重   ：乘在该段所有字段的抽取概率上；= 0 等于整段关闭。
#   字段权重 ：乘在该字段的抽取概率上；= 0 该字段永不出现，
#             = 3.0（上限）**必定出现**；中间值是相对倾向。
#   选项权重 ：在该字段的候选池里做加权随机；= 0 该选项永不出现。
#
# 抽取概率公式（必填字段与"必现"字段例外，见 _one_pass）：
#       p = clamp(基准概率 × 密度系数 × 段权重 × 字段权重, 0, 1)
#
# ⚠️ 为什么字段权重 3.0 要单列成"必现"、而不是让它去乘概率：
#    密度自适应会整体缩放基准概率（标签太多时往下压）。如果 3.0 只是
#    乘数，用户把某字段拉到 3.0 期望"一定要有"，结果被密度一压照样可能
#    抽不到 —— 那权重面板就成了骗人的东西。上限值直接短路成必抽，
#    语义与"0 = 永不"对称，用户也一眼能记住。
#
# ⚠️ 权重**不参与档位判定**。超档项即使权重给到 3.0 也进不来。
#
WEIGHT_MIN = 0.0
WEIGHT_MAX = 3.0
DEFAULT_WEIGHT = 1.0
DEFAULT_BASE_PROB = 0.65          # 源脚本写死的 0.65/0.6 两档，现在可调

# ── 胸部大小等级（v3.17.0，取代 v3.2.0 的巨乳开关）────────────────────
# 界面中文 → figure 字段 danbooru tag。六档 tag 已逐一实证：
#   flat chest 240,190 帖 · small 731,362 · medium 1,233,828 · large 2,299,636
#   · huge 335,231 · **gigantic 16,886** —— ⚠️ `giant_breasts` 在 danbooru
#   是 0 帖且无 wiki/别名的**无效 tag**，极限档必须写 gigantic。
# 「不指定」= None：figure 参与随机、可被角色 APPEAR 对齐覆盖（原开关关闭行为）。
# 指定档位 = 锁定 figure，**最高优先级**（排在角色外貌对齐之后执行，覆盖之）。
BREAST_SIZES = {
    "平胸": "flat chest",
    "小胸": "small breasts",
    "中等": "medium breasts",
    "大胸": "large breasts",
    "巨乳": "huge breasts",
    "爆乳": "gigantic breasts",
}


def empty_weights() -> dict:
    return {"sections": {}, "fields": {}, "options": {}}


def parse_weights(raw) -> dict:
    """把控件里的 JSON 文本解析成权重表。

    契约：**永远不抛异常**。空串 / 非法 JSON / 类型不对 → 全按默认 1.0。
    （源脚本的 `权重细则` 用的是 try/catch 当空表，这里保持同一口径。）
    """
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return empty_weights()
        try:
            data = json.loads(text)
        except Exception:
            return empty_weights()
        if not isinstance(data, dict):
            return empty_weights()

    out = empty_weights()
    for scope in ("sections", "fields", "options"):
        bag = data.get(scope)
        if not isinstance(bag, dict):
            continue
        if scope == "options":
            for fk, sub in bag.items():
                if not isinstance(sub, dict):
                    continue
                clean = {}
                for k, v in sub.items():
                    w = _as_weight(v)
                    if w is not None:
                        clean[str(k).strip().lower()] = w
                if clean:
                    out["options"][str(fk)] = clean
        else:
            for k, v in bag.items():
                w = _as_weight(v)
                if w is not None:
                    out[scope][str(k)] = w
    return out


def enforce_option_mutex(w: dict) -> list:
    """同一字段内**只允许一个「必现」**（选项权重 3.0）—— 原地降级，返回说明。

    v5.0.1 新增（用户 2026-11-01）：
    「在同类型词条互斥的词条类型权重主动选择为 3 时，如果其他词条权重也为 3，
      自动把之前选择的词条权重降为 1 —— 权重 3 是必出，互斥的两条不能都必出。」

    为什么必须在引擎里兜：
      `forced_option_pool()` 会把**所有** 3.0 项收进候选池，然后 `weighted_pick`
      在它们之间抽一个 —— 于是两个 3.0 里必有一个永远出不来，等于「假必现」。
      面板已经做了即时互斥（点第二个 3.0 就把第一个降掉），但这条路挡不住
      三种输入：外部脚本直接拼 weights_json、从旧预设恢复、手改 JSON。

    保留哪一个：**字典里最后出现的那个**（JSON 保序 = 用户最后设置的那个）。
    降级到 1.0 而不是删除键 —— 前端回显与 `dump_weights` 都能如实反映。

    没有冲突时是恒等变换（不碰任何键），所以既有随机路径零漂移。
    """
    notes = []
    for fk, sub in (w.get("options") or {}).items():
        if not isinstance(sub, dict):
            continue
        forced = [k for k, v in sub.items()
                  if isinstance(v, (int, float)) and float(v) >= WEIGHT_MAX]
        if len(forced) < 2:
            continue
        keep = forced[-1]
        _sec, fld = field_by_key(fk)
        lab = (fld or {}).get("lab") or fk
        for k in forced[:-1]:
            sub[k] = DEFAULT_WEIGHT
            notes.append(
                "字段「%s」同类型互斥，只能有一个必现（权重 3）："
                "「%s」已自动从 3.0 降为 1.0，保留最后设置的「%s」"
                % (lab, k, keep))
    return notes


def _as_weight(v):
    """归一成 [0,3] 的浮点；认不出来返回 None（忽略这一条，而不是当 0）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                        # NaN
        return None
    return max(WEIGHT_MIN, min(WEIGHT_MAX, f))


def dump_weights(w: dict) -> str:
    """回写成控件文本。只写非默认项 —— 空表 = 全默认，读起来最干净。"""
    out = {}
    for scope in ("sections", "fields"):
        bag = {k: round(v, 3) for k, v in (w.get(scope) or {}).items()
               if abs(v - DEFAULT_WEIGHT) > 1e-9}
        if bag:
            out[scope] = bag
    opts = {}
    for fk, sub in (w.get("options") or {}).items():
        bag = {k: round(v, 3) for k, v in sub.items()
               if abs(v - DEFAULT_WEIGHT) > 1e-9}
        if bag:
            opts[fk] = bag
    if opts:
        out["options"] = opts
    return json.dumps(out, ensure_ascii=False, indent=1) if out else ""


def section_weight(w: dict, sec_id: str) -> float:
    return float(w["sections"].get(sec_id, DEFAULT_WEIGHT))


def field_weight(w: dict, field_key: str) -> float:
    return float(w["fields"].get(field_key, DEFAULT_WEIGHT))


def option_weight(w: dict, field_key: str, en: str) -> float:
    return float((w["options"].get(field_key) or {}).get(
        str(en).strip().lower(), DEFAULT_WEIGHT))


def forced_option_pool(pool: list, key: str, weights: dict) -> list:
    """池子里**选项权重顶到 WEIGHT_MAX（3.0）** 的那些项（选项级「必现」）。

    v4.1.1 新增。为什么非要有它：3.0 以前只是 `weighted_pick` 里的一个乘数 ——
    15 个选项的字段里 3/(3+14) ≈ 18%，30 个选项只剩 10%。
    用户把「紫色发」「女仆头饰」都点到 3.0，实测连抽两张**一张都没中**
    （`%_00033_.png` / `%_00034_.png`），而文档写的是
    「三层权重：段 / 字段 / 选项，0 = 关闭，3.0 = 必现」——
    三层都得真的必现，只在字段层必现等于骗人。

    返回空列表 = 本字段没有必现项，一切照旧（**零漂移**）。

    ⚠️ 调用方只在 `role_pick=False`（勾了「不指定角色」）时调它 ——
       有角色时人物特征归角色官方设定，选项权重不该硬塞配件
       （见 draw 里字段循环处的长注释）。
    """
    return [it for it in (pool or [])
            if option_weight(weights, key, it[1]) >= WEIGHT_MAX]


# ── 「姿势 / 体位」类字段（v4.3.0）──────────────────────────────────────
# 为什么单独列一份：这些字段是"用户在手工指定这一张的动作"，而自动场景
# 恰恰会往同一批字段里写死值（`tags`）、按人数钉死（`count_lock`）、
# 或者直接闭麦（`drop_fields`）。三者任一发生，用户的权重就白调了 ——
# 实测：`n_act` 的 `missionary` 设 0.5 / 1.0 / 2.0，120 抽的分布**一模一样**
# （同为 4 次、top3 完全相同），因为自动场景把 n_act 写死了。
POSE_WEIGHT_FIELDS = ("pose_base", "arms_hands", "action", "pose_risque",
                      "n_act", "n_pos", "n_orgasm", "n_cum", "n_fluid",
                      "n_theme")


def forced_pose_weight_fields(weights: dict) -> list:
    """哪些「姿势/体位」字段有选项顶到 3.0。空列表 = 一个都没点名。

    v4.3.0：`_block_auto_scene` 的判据从这里来 —— 用户点名了动作，
    本次就不自动套场景（自动场景的人数/闭麦声明会覆盖点名项）。
    """
    out = []
    for k in POSE_WEIGHT_FIELDS:
        _sec, fld = field_by_key(k)
        for _zh, en in ((fld or {}).get("opts") or []):
            if option_weight(weights, k, en) >= WEIGHT_MAX:
                out.append(k)
                break
    return out


def tuned_weight_fields(weights: dict) -> set:
    """被用户**调过权重**（≠1.0 且 > 0）的选项所在的字段集合。

    v4.3.0：这些字段一律「必抽」—— 字段本身被 `base_prob` 以 35% 的概率
    整个跳过时，2.0 与 0.5 的差别会被稀释成噪声，用户看到的是"中间值没用"。
    0 不算在这里：它的语义是"这一项永不出现"，不需要提高字段出现率。

    ⚠️ 只在**有调整**时非空 —— 全默认的工作流拿到空集合，抽取路径与
       v4.2.3 逐字节一致（零漂移口径）。
    """
    out = set()
    for fk, sub in (weights.get("options") or {}).items():
        for v in sub.values():
            if v > 0 and abs(v - DEFAULT_WEIGHT) > 1e-9:
                out.add(fk)
                break
    return out


def forced_pose_level(weights: dict) -> int:
    """点名（3.0）的姿势/体位/主题词条要用的最高档位。0 = 不用抬档。

    v4.3.0（用户口径 22:12：「不放行，自动调整档位」）：
    档位闸门**不拆** —— `eligible_options()` 仍然是唯一准入点，越档词不会
    凭空被拉进 L2 的池子；但既然用户点了它 3.0，就把**整张图的档位**抬到
    该词的档位再抽。这样"3.0 必现"与"低档不越界"两个口径同时成立，
    不会出现"L2 正文里夹一个 L3 词"的混档结果。

    只算 `POSE_WEIGHT_FIELDS`（姿势 4 + 体位 5 + 主题 1）—— 发色/服装的
    3.0 不抬档（用户口径：限这三组）。
    """
    need = 0
    for k in POSE_WEIGHT_FIELDS:
        _sec, fld = field_by_key(k)
        for _zh, en in ((fld or {}).get("opts") or []):
            if option_weight(weights, k, en) >= WEIGHT_MAX:
                need = max(need, option_level(k, en))
    return need


def level_key_for_num(num: int) -> str:
    """数字档位 → key。跳过 `any`（它与 extreme 同为 4，但不是真实档位）。"""
    for k in LEVEL_KEYS:
        if k != "any" and LEVEL_NUM.get(k) == int(num):
            return k
    return ""


def swept_zero_weight(state: dict, weights: dict, protect=()) -> list:
    """把「权重 = 0」的选项从最终 state 里清掉（v4.3.0）。

    这是"0 = 永不出现"的最后一道闸。抽取期 `weighted_pick` 已经把 0 权重
    的项排除了，但**自动写入路径**绕过它：法典场景 `tags` 写死、
    `random_fields` 抽候选、角色外貌/发型对齐、`ensure_level_floor` 保底
    —— 实测 `missionary = 0` 仍有 2/40 出现，全是场景写进去的。

    只扫**用户真的调过**的字段（`weights["options"]` 里出现过的），
    locks / forced 一律跳过（显式指定高于权重）。返回被清掉的字段说明。
    """
    protect = set(protect or ())
    tuned = set((weights.get("options") or {}).keys())
    out = []
    for k in list(state):
        if str(k).startswith("_") or k not in tuned or k in protect:
            continue
        v = state.get(k)
        if not v:
            continue
        if option_weight(weights, k, v) > 0.0:
            continue
        state[k] = ""
        out.append(f"{k}（`{v}` 权重 0，已关闭）")
    return out


def forced_sex_count_target(weights: dict) -> str:
    """点名性行为时，人数至少要抬到哪一档。返回目标人数串（"" = 不用动）。

    v4.2.1。为什么必须有它：没套场景时 `count` 可能抽到 solo，紧接着
    `PARTNER_GATED_FIELDS` 把 27 个需要伴侣的动作全剔掉（报告里那句
    「人数相容：性行为 剔除 27 项」），用户的 3.0 直接作废；更气人的是
    **抽完之后** `ensure_level_floor` 又把人数改成 `1girl, 1boy, hetero`
    —— 最后人数明明是有的，抽的时候却是 solo。所以"要性行为"这条指令
    必须提前翻译成人数下限，在人数相容过滤**之前**生效。

    三档：
      · 点名项里有 `GROUP_ONLY_TAGS`（bukkake / 3P / 群交 / 多重内射 …）
        → `1girl, multiple boys, group sex`
      · 点名 `n_act` / `n_cum` 非 solo 白名单项，或点名了"需要对方"的体位
        → `1girl, 1boy, hetero`
      · 只点了 solo 也成立的（masturbation / spread legs / 绳缚 …）→ ""
    """
    need = ""
    yuri = False
    for k in SEX_CONTENT_FIELDS:
        _sec, fld = field_by_key(k)
        for _zh, en in ((fld or {}).get("opts") or []):
            if option_weight(weights, k, en) < WEIGHT_MAX:
                continue
            e = str(en).strip().lower()
            if e in GROUP_ONLY_TAGS:
                return COUNT_GROUP_SEX
            # v4.4.1：穿戴式 / 扶她 → 双女（见 COUNT_YURI 的说明）
            if k == "n_theme" and e in YURI_TOY_TAGS:
                yuri = True
                continue
            if k in PARTNER_GATED_FIELDS and e not in SOLO_OK_PARTNER_OPTS:
                need = "partner"
            elif k == "n_pos" and e not in SOLO_OK_POS_OPTS:
                need = "partner"
    # 优先级：群交 > 异性（有明确性行为点名时以动作为准） > 双女
    if need == "partner":
        return COUNT_PARTNER
    return COUNT_YURI if yuri else ""


# 点名了性内容（这四个字段任一选项顶到 3.0）就算"用户要正戏"。
# 用于决定"要不要让自动场景抽签让路"（见 draw 里 n_canon 分支的说明）。
SEX_CONTENT_FIELDS = ("n_act", "n_pos", "n_cum", "n_theme")


def has_forced_sex_content(weights: dict) -> bool:
    """四个性内容字段里有没有被点名的选项（权重 ≥ 3.0）。"""
    for k in SEX_CONTENT_FIELDS:
        _sec, fld = field_by_key(k)
        for _zh, en in ((fld or {}).get("opts") or []):
            if option_weight(weights, k, en) >= WEIGHT_MAX:
                return True
    return False


def user_pinned_scene(weights: dict, forced=None, locks=None) -> bool:
    """用户是否**自己点名了场景**（显式套用 / forced / 锁定 n_canon）。

    ⚠️ 不能只看法典 c1..c14 的 opts —— 面板套用场景写的是
    `options.n_canon["sc_xxx"] = 3`，那些 id 不在字段 opts 里，
    必须直接扫权重袋。
    """
    if "n_canon" in (forced or {}):
        return True
    if "n_canon" in set(locks or ()):
        return True
    return any(v >= WEIGHT_MAX
               for v in (weights["options"].get("n_canon") or {}).values())


def weight_stats(w: dict) -> dict:
    """面板用的计数：调过的段 / 字段 / 选项各几项。"""
    def tuned(bag):
        return sum(1 for v in bag.values() if abs(v - DEFAULT_WEIGHT) > 1e-9)
    opt_items = sum(len(sub) for sub in (w.get("options") or {}).values())
    return {
        "sections": tuned(w.get("sections") or {}),
        "fields": tuned(w.get("fields") or {}),
        "options": opt_items,
        "total": tuned(w.get("sections") or {}) + tuned(w.get("fields") or {}) + opt_items,
    }


def validate_weights(w: dict) -> list:
    """权重表里引用了不存在的段/字段/选项 —— 返回告警列表。

    不报错、不阻断：老工作流存的旧 key 不该让整个工作流跑不起来，
    但必须在报告里说出来，否则用户会以为"我调了权重却没生效"。
    """
    bad = []
    known_sec = set(section_ids())
    known_fld = set(field_keys())
    for k in (w.get("sections") or {}):
        if k not in known_sec:
            bad.append(f"段权重引用了不存在的段「{k}」")
    for k in (w.get("fields") or {}):
        if k not in known_fld:
            bad.append(f"字段权重引用了不存在的字段「{k}」")
    for fk, sub in (w.get("options") or {}).items():
        if fk not in known_fld:
            bad.append(f"选项权重引用了不存在的字段「{fk}」")
            continue
        _sec, fld = field_by_key(fk)
        valid = {str(en).strip().lower() for _zh, en in fld.get("opts", [])}
        if fk == "n_canon":
            # ⚠️ v3.18.0 顺手修掉一个**既有**的假告警：n_canon 的候选来自两处
            #    （pools.json 的法典 14 + 场景搭配库），但上面只取了 pools 的 opts，
            #    于是「套用场景搭配」时那条 `sc_*` 会被误报成"不存在的选项"——
            #    实测 v3.17.2 就有（`draw(weights 里带 sc_balcony_night)` 会出这条）。
            #    这里只**减**消息不增消息：非法 id 照样报，合法的 sc_* 不再误报。
            valid |= {sid.strip().lower() for sid in scene_index()}
        for en in sub:
            if en not in valid:
                bad.append(f"选项权重引用了不存在的选项「{fk} / {en}」")
    return bad


# ============================================================================
# 四、加权随机
# ============================================================================


def derive_seed(*parts) -> int:
    """从任意字符串派生 32 位种子。

    ⚠️ 一律用 zlib.crc32，**不用内置 hash()** —— 后者每进程加盐，
       同一个工作流跑两次会得到不同结果，"可复现"就成了空话。
    """
    key = "|".join(str(p) for p in parts)
    return zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF


def weighted_pick(rng: random.Random, items: list, weight_of):
    """加权随机取一项。全 0 权重 → 返回 None（= 本字段不出现）。"""
    if not items:
        return None
    ws = [max(0.0, float(weight_of(it))) for it in items]
    total = sum(ws)
    if total <= 0.0:
        return None
    r = rng.random() * total
    acc = 0.0
    for it, w in zip(items, ws):
        acc += w
        if r <= acc:
            return it
    return items[-1]


def clamp_prob(p: float) -> float:
    return max(0.0, min(1.0, p))


# ============================================================================
# 五、互斥与消解
# ============================================================================

# 把源脚本的 MUTEX_GROUPS（JS 正则字面量）编译成 Python 正则。
# 注意 JS 的 /\b/ 与 Python 一致；源脚本没用到 JS 专有语法，可直接转。
def _compile_mutex() -> list:
    out = []
    for g in pools().get("mutex_groups", []):
        flags = 0
        if "i" in g["a"].get("flags", ""):
            flags |= re.I
        fa = re.compile(g["a"]["source"], flags)
        fb = re.compile(g["b"]["source"], flags)
        out.append((g["id"], fa, fb))
    return out


def apply_mutex(tags: list, keep=()) -> list:
    """A 组命中时剔除 B 组 —— 与源脚本 applyMutex 逐条同构。

    ⚠️ v3.22.2：`keep` = 本轮由场景/预设**显式写入**的标签。
       优先级「显式 > 随机」，所以：
         · B 侧只有随机标签 → 照旧剔 B；
         · B 侧全是显式标签 → **反过来剔 A 侧的随机标签**（原来会把场景写死的光效/服装删掉）；
         · 两侧都含显式标签 → 谁也不动（真矛盾，交给自检 F7 报，不静默删用户的指令）。
       实测踩到的：`G6 雨天/夜晚 × 晴天/晨光/黄昏` 把场景写死的 `morning light`
       删了 7 次；`G1 全裸 × 整套服装` 把场景写死的 `bikini` 删了。
    """
    keep = set(keep or ())
    r = list(tags)
    for _gid, ra, rb in _compile_mutex():
        ha = any(ra.search(t) for t in r)
        hb = any(rb.search(t) for t in r)
        if not (ha and hb):
            continue
        b_hit = [t for t in r if rb.search(t)]
        a_hit = [t for t in r if ra.search(t)]
        b_kept = [t for t in b_hit if t in keep]
        a_kept = [t for t in a_hit if t in keep]
        if b_kept and not a_kept:
            r = [t for t in r if not (ra.search(t) and t not in keep)]
        elif a_kept and b_kept:
            continue
        else:
            r = [t for t in r if not rb.search(t)]
    return r


def sanitize(state: dict, weights: dict = None) -> list:
    """随机后的自相矛盾消解。返回被清掉的字段 key 列表（用于报告）。

    规则搬自源脚本 sanitizeRandom()，并发扬两点：
      · 每条都返回"清掉了什么"，不再静默；
      · 档位相关的冲突（全裸 × 具体服装）交给档位闸门，不在这里重复。

    `weights`（v4.3.0，可选）：用户口径「不同类型词条权重调整后要能共存生效」。
    两个**都调过权重**的字段互斥时，让权重高的那一侧留下 —— 不给参数时
    完全走旧逻辑（按字段优先级 / 场景优先），所以既有随机路径零漂移。
    """
    cleared = []
    # v3.22.2：本轮由场景/预设**显式写入**的字段。优先级「显式 > 随机」——
    # 下面每一条硬编码互斥规则都按这个口径让随机那一侧先退（原来一律让场景退）。
    protected = set(state.get("_preset_fields") or ())
    # ⚠️ v4.3.0：**函数开头就要并进 `_forced_tags`**。以前只在下方「联动矛盾消解」
    #    那一段才并 —— 于是前面的硬编码互斥（如「全裸 × 整套服装」）看不到
    #    用户点名的 3.0 字段，`outfit` 被当作随机值清掉（实测 6/15 丢）。
    protected |= {f[0] for f in (state.get("_forced_tags") or ())}
    # v4.3.0：本轮每个字段值的**选项权重**（1.0 = 没调过）。
    # 只在传了 weights 时计算 —— 冲突裁决与上肢单槽都读它。
    _wmap = {}
    _tuned_keys = set()
    if weights:
        _tuned_keys = set((weights.get("options") or {}).keys())
        for _k in list(state):
            if str(_k).startswith("_") or not state.get(_k):
                continue
            _wmap[_k] = option_weight(weights, _k, state[_k])

    def _conflict_source(fk):
        """谁让 fk 被判成矛盾 —— 逐个把别的字段清掉试一遍（字段数 ~40，值得）。"""
        for ok in list(state):
            if ok == fk or str(ok).startswith("_") or not state.get(ok):
                continue
            saved = state[ok]
            state[ok] = ""
            still = state.get(fk) in L.bans_for(state, fk)
            state[ok] = saved
            if not still:
                return ok
        return None

    def clear(key):
        if state.get(key):
            state[key] = ""
            cleared.append(key)

    def yield_random(fixed_key, random_key, convert=None):
        """固定值 vs 随机值冲突：让**随机**那一侧退，固定值留下。

        v4.3.0：两侧**都是显式**（都在 `protected`：场景写死 + 权重点名）时，
        按**选项权重**裁决 —— 用户点名的 3.0 赢过场景写死的固定值。
        权重相同（都是 1.0）时返回 False，调用方照旧清自己 → 旧行为零漂移。
        """
        if fixed_key in protected and random_key not in protected:
            if convert:
                state[random_key] = convert
                cleared.append(f"{random_key}(改为 {convert})")
            else:
                clear(random_key)
            return True
        if fixed_key in protected and random_key in protected:
            _wf = _wmap.get(fixed_key, 1.0)
            _wr = _wmap.get(random_key, 1.0)
            if _wf > _wr:
                clear(random_key)
                cleared.append(
                    f"{random_key}（让位给权重更高的 {fixed_key} "
                    f"{_wf:g} > {_wr:g}）")
                return True
        return False

    # ── 上肢单槽（v3.39.0）──────────────────────────────────────────
    # 抽取期靠 `L.bans_for()` 做**单向**池过滤，能挡住"随机 × 随机"；
    # 这里兜的是抽取期**看不见**的那批：
    #   · 场景 / 预设用 `tags` 一次写死两个手部字段（如 sc_changing_room_peek
    #     同时写 `pose_risque=skirt pull` 与 `arms_hands=hand in own hair`）；
    #   · `forced` / `locks` 由上游直接给值；
    #   · 法典场景写在抽取循环**之后**（apply_preset 排在主循环末尾）。
    # 判据与抽取期同源（L.HAND_SLOT / L.HAND_PRIORITY），不另立一套口径 ——
    # 两套口径各判各的正是历史上反复出错的根因。
    # ⚠️ 只清值、不重抽：与 drop_fields / ban_tags 同一条纪律，rng 消耗不变。
    _holders = L.hand_slot_state(state)
    # 第三个来源：**场景 `extra` 里的自由短语**。它在 `n_extra` 字段里，
    # 不属于任何姿势字段，但语义等价 —— `sc_yoga_mat` 的 extra 写着
    # `stretching`，随机再抽到 `hand in own hair` 就是两条互斥指令。
    # 处理口径：extra 是场景/用户写死的（显式），让随机抽到的字段退；
    # 若那个字段也是场景写死的，就把 extra 里的重复项删掉（同一动作说两遍）。
    _extra = [t.strip() for t in str(state.get("n_extra") or "").split(",")
              if t.strip()]
    _extra_hand = [t for t in _extra if t in L.HAND_ALL]
    if _extra_hand and _holders:
        for _k in sorted(_holders, key=L.HAND_ORDER.index):
            if _k in protected:
                continue
            state[_k] = ""
            cleared.append(f"{_k}（上肢单槽，让位给 extra 的 "
                           f"{','.join(_extra_hand)}）")
        _left = [k for k in _holders if k in protected]
        if _left:
            _drop = {t for t in _extra_hand
                     if any(t in (L.HAND_SLOT.get(_k) or ()) for _k in _left)}
            if _drop:
                _extra = [t for t in _extra if t not in _drop]
                state["n_extra"] = ", ".join(_extra)
                cleared.append("n_extra（上肢单槽，去掉与 %s 重复的 %s）"
                               % ("、".join(_left), ",".join(sorted(_drop))))
        _holders = L.hand_slot_state(state)
    if len(_holders) > 1:
        # ⚠️ v4.3.0 裁决顺序（用户口径 22:14「权重高者赢」）：
        #    ① 被选项权重 3.0 点名的（`protected`）优先；
        #    ② 其次比**选项权重**—— 两个都调过时，高的留下；
        #    ③ 同权重再按 HAND_ORDER（arms_hands > pose_risque）。
        #    以前一律按 HAND_ORDER，于是 pose_risque 的手部项被点到 3.0 也照样
        #    让位给 arms_hands（实测 15 抽丢 4 次：seed 1005/1008/1010/1012）。
        def _handrank(k):
            return -L.HAND_ORDER.index(k) if k in L.HAND_ORDER else -99

        _win = max(_holders,
                   key=lambda k: (k in protected, _wmap.get(k, 1.0), _handrank(k)))
        for _k in sorted(_holders, key=L.HAND_ORDER.index):
            if _k == _win:
                continue
            state[_k] = ""
            _wb = _wmap.get(_k)
            cleared.append(
                f"{_k}（上肢单槽，让位给 {_win}）"
                if _wb is None or _wb <= 1.0
                else f"{_k}（上肢单槽，权重 {_wb:g} 低于 {_win}，让位）")

    # ── 人数相容（v3.41.2）────────────────────────────────────────
    # 抽取期已经剔过一轮，这里兜的是**抽取期看不见**的：场景 `tags` 写死、
    # `forced` / `locks` 由上游给值、法典场景在循环末尾才写入。
    # ⚠️ 只清 `n_act` / `n_cum` —— `n_pos` 的 `spread legs` / `bound wrists`
    #    在 solo 下完全成立，按字段一刀切会误删（见 PARTNER_GATED_FIELDS 注释）。
    if not count_has_partner(str(state.get("count") or "")):
        for _k in PARTNER_GATED_FIELDS:
            _v = str(state.get(_k) or "").strip()
            if _v and _v.lower() not in SOLO_OK_PARTNER_OPTS:
                state[_k] = ""
                cleared.append(f"{_k}（`{_v}` 需要伴侣，与当前人数不符）")

    # ── 多人专属元素 × 人数（v3.41.0）──────────────────────────────
    # `threesome` / `foursome` / `gangbang` / `bukkake` / 多重内射 只在
    # 三人及以上成立。随机抽到了也要清 —— 否则就是 `1girl, solo` + `threesome`。
    # ⚠️ 第一次验收漏了这条：保底把 L4 的 `n_theme=threesome` 补给了单人
    #    （L4 的 n_theme 只有 3P/4P/群交/项圈四项可挑，前三个都是多人专属）。
    if not count_is_group(str(state.get("count") or "")):
        for _k in ("n_theme", "n_cum"):
            _v = str(state.get(_k) or "").strip().lower()
            if _v in GROUP_ONLY_TAGS:
                state[_k] = ""
                cleared.append(f"{_k}（`{_v}` 是三人以上才成立的元素，"
                               f"与当前人数不符）")

    nude = ("completely nude", "topless", "bottomless",
            "nude with accessories only")
    if state.get("n_state") in nude:
        # 全裸时"整套服装"必须让位；袜/鞋/配饰/材质属于"全裸可保留"，
        # 与造型台的分类口径一致。
        # v3.22.2：但如果服装是场景**显式写的**（如沙滩排球写死 bikini），
        # 让步的应该是随机的 n_state —— 否则场景写了泳装却出全裸。
        if not yield_random("outfit", "n_state"):
            clear("outfit")

    # ⚠️ v3.37.1：真·全裸时**内衣也要让位**。
    #    v3.33.0 记过「completely nude 不会自动清服装」，但当时只处理了 `outfit` 字段；
    #    `n_fabric`（lingerie / corset / babydoll / garter belt / latex …）一直漏在名单外 ——
    #    于是「completely nude + 情趣内衣」会同时进正文，模型只能二选一（场景里表现为
    #    "全裸"与"内衣"两个指令互相抵消，织物质感全靠模型自己猜）。
    #    ⚠️ 只认**真全裸**两档：`topless` / `bottomless` 的另一半本来就该有衣物，
    #       连坐会把「仅上裸 + 蕾丝睡衣」这种合法组合也删掉。
    if state.get("n_state") in ("completely nude", "nude with accessories only"):
        if not yield_random("n_fabric", "n_state"):
            clear("n_fabric")

    # ⚠️ v3.20.0：不能只认裸 `pantyhose` —— 词库现在有 `purple pantyhose`、
    #    `pantyhose, garter belt` 等变体，精确匹配会把它们漏掉，于是
    #    「红色连裤袜 + 赤足」这种物理矛盾能过 sanitize、到自检才炸（实测）。
    if "pantyhose" in (state.get("socks") or "") \
            and state.get("shoes") == "bare feet":
        # v3.22.2：鞋是场景固定值（泳池边写死 bare feet）时，退的是随机袜子
        if not yield_random("shoes", "socks"):
            state["shoes"] = "high heels"
            cleared.append("shoes(改高跟鞋)")

    if state.get("n_theme") == "blindfold":
        if state.get("accessory") == "glasses":
            clear("accessory")
        # ⚠️ 这里修的是源脚本的一个真 bug：
        #    源脚本 sanitizeRandom() 只清「心形瞳 heart-shaped pupils」，
        #    但它的 checkTags() F8 判定里同时把「翻白眼 rolled back eyes」
        #    也算成蒙眼冲突。两边口径不一样，结果就是 蒙眼 + 阿嘿颜能过
        #    sanitize、然后在自检里炸掉。这里把 sanitize 对齐到 F8 的口径。
        orgasm = state.get("n_orgasm") or ""
        if "heart-shaped pupils" in orgasm or "rolled back eyes" in orgasm:
            clear("n_orgasm")

    if "facing away" in (state.get("pose_base") or "") \
            and state.get("eye_gaze") == "looking at viewer":
        clear("eye_gaze")

    if "pov" in (state.get("viewpoint") or "") \
            and state.get("shot_size") == "full body":
        if not yield_random("shot_size", "viewpoint"):
            state["shot_size"] = "cowboy shot"
            cleared.append("shot_size(改七分身)")

    lofi = ("selfie", "webcam footage", "security camera footage")
    if state.get("device") in lofi and state.get("dof") in (
            "focus on face", "depth of field, blurry background"):
        # v3.22.2：景深是场景固定值时退设备（"镜前自照"这类场景就该有景深）
        if not yield_random("dof", "device"):
            clear("dof")

    if state.get("viewpoint") == "from above" \
            and state.get("eye_gaze") == "looking up":
        state["eye_gaze"] = "looking at viewer"
        cleared.append("eye_gaze(改注视镜头)")

    if state.get("viewpoint") == "from below" \
            and state.get("eye_gaze") == "looking down":
        state["eye_gaze"] = "looking at viewer"
        cleared.append("eye_gaze(改注视镜头)")

    # ── 联动矛盾消解（v3.15.0）────────────────────────────────────
    # 联动本身在**抽取时**就过滤了候选池（见 _one_pass 的 bans 分支），
    # 这里兜的是"抽完之后才被改写"的那批：法典 / 场景预设、锁定值、强制值。
    # 实测踩到过两次：
    #   · 某雪地预设写了 emotion=surprised，而 mouth 是按**那时**已抽的旧
    #     情绪选的「咬唇」→ 成品里成了「惊讶 + 咬唇」；
    #   · 场景预设会写 outfit / top →「泳池边 + 皮草大衣」。
    # 顺序上 sanitize 排在 apply_preset 之后，所以能兜住。
    #
    # ⚠️ 遍历 state 里的**全部**字段，不要只列那几个"想到的"——
    #    联动表覆盖 10+ 个字段（服装/姿势/表情/体位/光效），
    #    手写清单漏一个就是一条静默失效的约束。
    # ⚠️ v3.22.2：**场景/预设显式写入的字段优先**。
    #    原来这里一律 clear —— 于是"用户套了场景"和"这一轮随机到的 emotion/place"
    #    打架时，被清掉的总是场景那一侧（实测：口交特写写死 `parted lips`，
    #    本轮随机 emotion=grin → mouth 被清空；靠墙而立 + 随机地点花田 → 姿势没了）。
    #    正确优先级是「显式指令 > 随机」，所以固定值保留，让步的是**随机**那一侧。
    protected = set(state.get("_preset_fields") or ())
    # ⚠️ v4.2.0：选项权重点到 3.0 的字段同样是显式指令（与场景固定值同级）——
    #    冲突时保留它、让另一侧（随机值）退，而不是把它当随机值清掉。
    #    实测：哭泣=3.0 + 场景写死 smile 时，这里会把 crying 当"矛盾随机值"清空。
    protected |= {f[0] for f in (state.get("_forced_tags") or ())}
    for _fk in [k for k in list(state) if not str(k).startswith("_")]:
        _v = state.get(_fk)
        if not _v or _v not in L.bans_for(state, _fk):
            continue
        if _fk not in protected:
            # ⚠️ v4.3.0：**双方都调过权重**时，权重低的那个让位（用户口径 22:14）。
            #    以前一律清掉"被判矛盾"的随机值 —— 于是「情绪 2.0 + 嘴部 1.5」
            #    这种组合里，被清掉的可能恰好是权重更高的那一侧。
            #    只在 rival 也在 weights.options 里时启用：默认全 1.0 的工作流
            #    拿不到这个分支，既有行为零漂移。
            _rival = None
            if _tuned_keys:
                _rival = _conflict_source(_fk)
            if (_rival and _rival in _tuned_keys and _rival not in protected
                    and _wmap.get(_rival, 1.0) < _wmap.get(_fk, 1.0)):
                state[_rival] = ""
                cleared.append(
                    f"{_rival}（让位给权重更高的 {_fk}"
                    f" {_wmap.get(_fk, 1.0):g} > {_wmap.get(_rival, 1.0):g}）")
                continue
            clear(_fk)
            continue
        moved = False
        for _cand in ("place", "prop", "weather", "emotion", "n_act", "n_theme"):
            if _cand == _fk or _cand in protected or not state.get(_cand):
                continue
            _saved = state.get(_cand)
            state[_cand] = ""
            if _v not in L.bans_for(state, _fk):
                cleared.append(f"{_cand}（让位给场景固定值 {_fk}）")
                moved = True
                break
            state[_cand] = _saved
        if not moved:
            cleared.append(f"{_fk}（场景固定值，冲突时保留）")

    return cleared


# ── 穿戴式道具 ↔ 人数（v4.2.2，用户口径）─────────────────────────────
# 「双女性行为使用穿戴式道具，一男一女不使用穿戴式道具」。
# n_theme 里的玩具类选项（真源 = 词库，改动需同步 HTML）：
TOY_THEME_OPTS = frozenset({"strap-on", "dildo", "sex toys"})
TOY_YURI = "strap-on"          # 双女正戏补的穿戴式道具（词库现成选项）


def is_yuri_count(count_en: str) -> bool:
    """双女（2girls / yuri）且不是群交、也没有男性伴侣。"""
    tags = {t.strip().lower()
            for t in str(count_en or "").split(",") if t.strip()}
    if count_is_group(count_en) or count_has_partner(count_en):
        return False
    return bool(tags & {"2girls", "yuri"})


def apply_count_toy_rule(state: dict, lv: int, weights: dict) -> list:
    """人数 ↔ 穿戴式道具联动。返回报告说明（空 = 没动）。

    · **双女 + 正戏进行中** → `n_theme` 补 `strap-on`（穿戴式）——
      两个女性没有对方，画面里"在进行性行为"却没有任何道具，模型只能
      瞎画（拼腿 / 凭空出现男性）。补穿戴式是最稳的表达。
    · **一男一女** → 清掉 `n_theme` 里的玩具类选项（strap-on / dildo /
      sex toys）—— 有真家伙就不该再挂道具。这条**不要求正戏进行中**：
      人数已经是一男一女，画面里再出现穿戴式就是矛盾。
    · solo / 群交不干预；用户显式点名的（`_forced_tags`）优先于本规则；
      档位闸门照常生效（strap-on 是 L3 词，L0~L2 的双女不会硬塞）。
    """
    notes = []
    count = str(state.get("count") or "")
    sex_going = bool(state.get("n_act") or state.get("n_pos"))
    _forced_keys = {f[0] for f in (state.get("_forced_tags") or ())}
    cur = str(state.get("n_theme") or "").strip().lower()

    if is_yuri_count(count):
        if not sex_going:
            return notes                      # 没有正戏，不硬塞道具
        if cur in TOY_THEME_OPTS:
            return notes                      # 已经是玩具类，不用动
        if "n_theme" in _forced_keys:
            return notes                      # 用户点名了主题，主题优先
        if blocked_by_weight(weights, "n_theme"):
            return notes                      # 权重 0 = 用户关掉，硬规则也让路
        if not _field_ok_under("n_theme", TOY_YURI, lv):
            return notes                      # 档位闸门优先（strap-on 是 L3 词）
        state["n_theme"] = TOY_YURI
        # 标记给 trim_to_cap：规则补的道具不参与密度裁剪（见该函数注释）
        state["_toy_rule"] = True
        notes.append("双女正戏 → 补穿戴式道具 `%s`（没有对方，用穿戴式才立得住）"
                     % TOY_YURI)
    elif count_has_partner(count):
        if cur in TOY_THEME_OPTS:
            if "n_theme" in _forced_keys:
                notes.append("⚠️ 一男一女正戏但「主题元素」点名了 `%s` —— "
                             "显式指令优先，保留（画面会同时有道具）" % cur)
            else:
                state["n_theme"] = ""
                notes.append("一男一女 → 清空穿戴式道具 `%s`（不需要）" % cur)
    else:
        # v4.5.43（用户 2026-10-31）：单人 / 群交同样戴不了穿戴式 ——
        # `strap-on` / `futanari` 需要"一名女性佩戴 + 另一名承受"。
        # ⚠️ 只清这两个，**不要动** `sex toys` / `dildo`：自慰与玩具展示场景
        #    （sc_msb_toys_show / sc_msb_dildo_ride）本来就是 solo。
        if cur in YURI_TOY_TAGS:
            if "n_theme" in _forced_keys:
                notes.append("⚠️ 单人/群交但点名了 `%s` —— 显式指令优先，保留" % cur)
            else:
                state["n_theme"] = ""
                notes.append("单人/群交 → 清空 `%s`（穿戴式需要另一名女性承受）" % cur)
    return notes


# ============================================================================
# 五之二、成品检查与自动修复（v3.8.0）
# ============================================================================
#
# 与 sanitize 的分工（很重要，别把两者混起来）：
#
#   sanitize()  看 **state 字段** —— 只管"我们抽出来的字段之间"的矛盾。
#   repair()    看 **成品标签串** —— 里面还有 sanitize 看不到的来源：
#                 手写补句 extra_body、法典预设自带的 n_extra、
#                 锁定 / 强制指定的值、上游拼接台送进来的内容。
#
# 典型漏洞（sanitize 抓不到、repair 能抓）：手写补句写了 `full body`、
# 视角抽到 `pov` —— 两者分属不同来源，state 层面查不出矛盾，
# 但成品是"第一人称 + 全身"（F7 报的就是这个）。
#
# 三条纪律：
#   ① 只修**可判定、可解释**的项，每一条都进报告（不静默）；
#   ② 优先"剔除"，其次"改写"，**绝不凭空发明**词库里没有的词；
#   ③ 结果对同一输入**幂等**（修完再修不再变）。

# 观察成品时要用的四组"结构性"标签（判据与自检 F6/F7/F8/F9/F14 对齐，
# 不另立一套口径 —— 两套口径各判各的正是历史上反复出错的根因）。
_STAND_TAGS = ("standing sex", "stand and carry")
_LIE_TAGS = ("lying on stomach", "on all fours", "prone bone")
_LOFI_TAGS = ("selfie", "webcam footage", "security camera footage")
_PRO_DOF = ("focus on face", "depth of field, blurry background")
_PUPIL_TAGS = ("heart-shaped pupils", "rolled back eyes")
_MULTI_TAGS = ("multiple girls", "multiple boys", "2girls", "1boy", "hetero",
               "yuri", "orgy", "group sex")


def level_floor_tags() -> dict:
    """{单个标签: 逐选项上抬确认过的最低档位}（缓存）—— 只收 LEVEL_RAISE 覆盖的标签。

    为什么成品层需要它：判定表是**纯标签**的，对 `pov crotch` 这类词判 0；
    而它的档位是在**字段上下文**里确认的（见 anima_v4_overrides.LEVEL_RAISE）。
    repair / 检查节点只看成品标签串，拿不到字段，所以必须把「字段层已经确认过的
    档位」按标签折算回来 —— 否则用户把手写补句写成 `pov crotch` 时，
    它会以 0 档的口径混进全年龄正文。

    这是**只升**的折算：取该标签在所有登记项里的最高档位。
    """
    if "raise_floor_tags" not in _CACHE:
        from .anima_v4_overrides import LEVEL_RAISE
        pose_l0 = _pose_l0_tokens()
        m = {}
        for (_fk, en), (target, _why) in LEVEL_RAISE.items():
            for t in str(en).split(","):
                t = t.strip().lower()
                if not t:
                    continue
                # ⚠️⚠️ v3.22.2 修 bug：**多标签选项拆开后会误伤中性姿势词**。
                #   `('n_pos', 'against wall, sex')` 的意思是"这个**组合**是 3 档"，
                #   拆开后 `against wall` 单独也变成 3 档 —— 而它本身是 `pose_base`
                #   的 L0 选项。后果：SFW 场景自己写的「靠墙而立」到成品层被
                #   `repair()` 当"越档"剔除（实测 158 条 × 3 组里 054/055 六张全中）。
                #   真正的档位锚是同一串里的 `sex`（判定表本来判 3），姿势词不该背这个锅。
                if t in pose_l0:
                    continue
                m[t] = max(m.get(t, 0), int(target))
        _CACHE["raise_floor_tags"] = m
    return _CACHE["raise_floor_tags"]


def _pose_l0_tokens() -> set:
    """`pose_sfw` 段里**本身是 L0 选项**的姿势词集合（给上抬折算做免伤白名单）。

    只收姿势段的四个字段 —— 范围刻意窄：它们是"中性姿势词"，出现在任何
    多标签组合里都不该被组合的整体档位拖着上抬。
    """
    if "pose_l0_tokens" not in _CACHE:
        bag = set()
        for key in ("pose_base", "arms_hands", "action", "pose_risque"):
            _sec, fld = field_by_key(key)
            for _zh, en in ((fld or {}).get("opts") or []):
                if option_level(key, en) == 0:
                    bag.add(str(en).strip().lower())
        _CACHE["pose_l0_tokens"] = bag
    return _CACHE["pose_l0_tokens"]


def level_ceiling_tags() -> dict:
    """{单个标签: 逐选项**下调**过的最低档位}（缓存）—— 只收 LEVEL_DEMOTE 覆盖的标签。

    与 `level_floor_tags()` 完全对称：那个把 RAISE 折算回标签层，这个把 DEMOTE 折算回去。

    ⚠️ 为什么成品层需要它（v3.15.0 补）：`tag_level()` 是**成品层**判档
    （repair 与报告都用它），而 LEVEL_DEMOTE 是**字段级**校准。不折算回来就会错位 ——
    实测 `hair_fx / wet hair`（字段层 demote 到 0）在成品层仍被判 2，
    于是「SFW 出图 state 里全是 L0，报告的 real_level 却是 2」。
    v3.15.0 的 place 预抽改变了随机序列，才把这条既有错位撞出来。

    这是**只降**的折算：取该标签在所有登记项里的最低档位；
    最终 `tag_level` 再与上抬折算取 max —— 两张表互不覆盖。
    """
    if "ceiling_tags" not in _CACHE:
        m = {}
        for (_fk, en), (target, _why) in LEVEL_DEMOTE.items():
            for t in str(en).split(","):
                t = t.strip().lower()
                if t:
                    m[t] = min(m.get(t, 99), int(target))
        _CACHE["ceiling_tags"] = m
    return _CACHE["ceiling_tags"]


def tag_level(t: str) -> int:
    """单个标签的**有效档位** = max(判定表∩下调折算, 上抬折算)。

    ⚠️ 不改 `anima_v4_nsfw.grade_nsfw` —— 那个函数与造型台**逐字对拍**
       （tests/check_nsfw_parity.py），动它等于让两个插件对同一句话给出
       不同档位。折算只发生在本层，作为"准入"口径使用。

    三段合成（v3.15.0 起）：
        base = 判定表结果
        base = min(base, 下调折算)     ← 字段层说过"这个上下文里它更低"
        out  = max(base, 上抬折算)     ← 字段层说过"判定表漏了这个词"
    """
    key = str(t).strip().lower()
    base = int(N.grade_nsfw([key])["num"])
    ceil = level_ceiling_tags().get(key)
    if ceil is not None:
        base = min(base, ceil)
    return max(base, int(level_floor_tags().get(key, 0)))


# ============================================================================
# 五之二、高等级保底（v3.41.0）
# ============================================================================
#
# 问题：整套准入体系只有**上限**（`option_level <= lv`），没有下限。
# 于是"选 L3/L4"只代表"允许"高档内容出现，不代表"要求"它出现 ——
# 而 L3/L4 的载体全挤在 `pose_nsfw` + `special_nsfw` 的五个字段里，
# 每个字段独立抽签（约 65%），同时缺席的概率很高。
# 实测（800 抽/档）：L2 掉 13%、L3 掉 39%、L4 掉 59%。
#
# 解法：抽完之后统一校一次「**真正成立**的实际档位」，不够就补一条。
#   ① 判据用 `effective_level()` —— 它会把"人数上不可能成立"的标签排除掉
#      （solo + missionary 不算 L3）。只看标签会把假达标当成真达标。
#   ② 补的锚点**必须是本档的选项**（`option_level == lv`），不是"随便找个高档词"。
#   ③ 载体按"语义自然度"排序：先正戏 → 个人状态 → 主题 → 露出 → 服装。
#   ④ 只覆盖**未被锁定、未被场景显式写入**的字段（沿用「显式 > 随机」）。
#   ⑤ 确实无载体可写（全被锁 / 全被闭麦）→ 写进报告，绝不静默降级。
LEVEL_FLOOR_ORDER = ("n_act", "n_pos", "n_orgasm", "n_cum", "n_theme",
                     "pose_risque", "n_exposure", "n_state", "n_body",
                     "n_fabric", "cloth_state_sfw")


def nl_sentences(extra=None) -> set:
    """全部「自然语言整句」的集合（term_hints + 场景 scene_nl，小写去空白）。

    v4.5.0：`extra` 收**运行时生成**的整句（多角色外貌绑定句）——
    它们不在任何 JSON 里，但同样是整句，repair / F11 一样要放行。

    v4.4.2：`repair()` 的档位剔除与 `check()` 的 F11 长标签检查都要跳过它们 ——
    整句不是 tag，`tag_level()` 拿它去查判定表会误判档位（实测 L0 场景那句
    被判 1 档，在 sfw 里被整句剔掉），F11 也会把它当"超长未登记标签"报红。

    ⚠️ 用**精确集合**而不是"长且含句号"这类启发式：后者会把真实的长 tag
       也放过，实测 L0 200 抽的档位基线立刻红。
    """
    if "nl_sentences" not in _CACHE:
        s = set()
        for sub in term_hints().values():
            for v in (sub or {}).values():
                t = str(v).strip().lower()
                if t:
                    s.add(t)
        for rec in scene_index().values():
            # v4.4.5：正文里实际出现的可能是**拼接串**（场景句 + 变体追加句），
            # 也可能是**变体替换句 + 另池追加句**（触手：束缚句替换 + 口部句追加）。
            # 所以不能只把单句塞进集合，要把「所有 base × 所有 extra」都算出来，
            # 否则 F11 会把拼接串当"超长未登记标签"报红（实测 hardcore 档 3/200）。
            bases, extras = [], []
            t = str((rec or {}).get("scene_nl") or "").strip().lower()
            if t:
                bases.append(t)
            rv = (rec or {}).get("random_variants")
            pools = []
            if isinstance(rv, list):
                pools = [rv]
            elif isinstance(rv, dict):
                pools = [((v.get("items") if isinstance(v, dict)
                           and isinstance(v.get("items"), list) else v) or [])
                         for v in rv.values()]
            for body in pools:
                for it in (body or []):
                    if not isinstance(it, dict):
                        continue
                    nl = str(it.get("nl") or "").strip().lower()
                    if nl:
                        bases.append(nl)
                    add = str(it.get("nl_extra") or "").strip().lower()
                    if add:
                        extras.append(add)
            for b in bases:
                s.add(b)
            for e in extras:
                s.add(e)
                for b in bases:
                    s.add((b + " " + e).strip())
        _CACHE["nl_sentences"] = s
    if not extra:
        return _CACHE["nl_sentences"]
    return _CACHE["nl_sentences"] | {
        str(x).strip().lower() for x in extra if str(x or "").strip()}


def effective_level(state: dict) -> int:
    """当前 state 里**画面真能成立**的最高档位。

    与 `check()` 的 `real_level` 不同：这里会把「人数上不可能」的标签排除 ——
    `1girl, solo` 带着 `missionary` / `creampie` 时，那两个标签不计入档位，
    因为一个人做不了一对一正戏。差别直接影响保底判定：
    只看标签会认为"已经 L3 了"，于是不补，画面其实还是不够。

    ⚠️ 只对 `n_act` / `n_cum` 做这道检查。`n_pos` 刻意不查 ——
       `spread legs` / `bent over` / `bound wrists` 在 solo 下完全成立，
       按字段一刀切会误判（词库不区分"被摆成"与"自己摆"）。
    """
    count = str(state.get("count") or "")
    solo = not count_has_partner(count)
    best = 0
    for k, v in state.items():
        if str(k).startswith("_") or not v:
            continue
        raw = str(v).strip()
        if not raw:
            continue
        if solo and k in PARTNER_GATED_FIELDS \
                and raw.lower() not in SOLO_OK_PARTNER_OPTS:
            continue
        if k in ("n_extra", "nl"):
            for t in raw.split(","):
                if t.strip():
                    best = max(best, tag_level(t.strip()))
            continue
        if k not in FIELD_SLOT:
            continue
        for t in raw.split(","):
            if t.strip():
                best = max(best, tag_level(t.strip()))
    return best


def level_floor_candidates(field_key: str, lv: int, state: dict) -> list:
    """该字段里能当"本档锚点"的选项 [(zh, en), ...]。

    准入五条：**成品档位正好是 lv**、没被 `bans_for` 禁掉、与当前人数相容、
    选项权重不是 0、不是"靠段下限撑起来"的假档。

    ⚠️ 判档用 `tag_level`，**不是** `option_level` —— 这是个真 bug 的修复：
       两者会不一致（`intercrural` 的 option_level=2 因为 `pose_nsfw` 段下限是 2，
       而判定表判它 1）。原来按 option_level 选锚点，选完 `effective_level`
       一验还是 L1，于是"补了等于没补"（实测 L2 有 7/400 卡在这里）。
       既然验证口径是 tag_level，选用口径就必须是同一个。
    """
    _sec, fld = field_by_key(field_key)
    if not fld:
        return []
    count = str(state.get("count") or "")
    solo = not count_has_partner(count)
    multi = girl_count_for(count) >= 2
    bans = L.bans_for(state, field_key)
    out = []
    for zh, en in (fld.get("opts") or []):
        if en in bans:
            continue
        if tag_level(en) != lv:
            continue
        # 还要过**准入口径**这一关：`option_level` 会算上段下限（`pose_nsfw` 是 2），
        # 所以 `bent over` 这种"判定表判 1、段下限抬到 2"的值在 L1 会被
        # F15 / 超档回滚判成越档，当场 `state.pop()` 掉 —— 补了等于白补。
        # 两个口径必须同时满足，这就是 `check_v3_pose` 里说过的"两套口径各判各的"。
        if option_level(field_key, en) > lv:
            continue
        # 上肢单槽（v3.39.0）也会吃掉锚点：`pose_risque` 里 `stretching` /
        # `hair flip` 属手部动作，`arms_hands` 一旦占手，整个 `pose_risque`
        # 会被 sanitize 清掉。已经有人占手就换非手部的锚点。
        if field_key in L.HAND_SLOT and en in (L.HAND_SLOT.get(field_key) or ()) \
                and L.hand_slot_state(state):
            continue
        low = str(en).strip().lower()
        if field_key in MULTI_CLEAR_FIELDS and multi:
            continue
        if solo and field_key in PARTNER_GATED_FIELDS \
                and low not in SOLO_OK_PARTNER_OPTS:
            continue
        # 三人以上才成立的元素：solo / 一男一女都不能当锚点
        # （实测 extreme 档曾把 `threesome` 补给了 `1girl, solo`）
        if low in GROUP_ONLY_TAGS and not count_is_group(count):
            continue
        # v4.5.43：穿戴式 / 扶她只在双女里成立 —— 单人/异性/群交都不能拿它当锚点
        if low in YURI_TOY_TAGS and not is_yuri_count(count):
            continue
        out.append((zh, en))
    return out


def ensure_level_floor(state: dict, lv: int, weights: dict, locks,
                       preset_id: str, rng) -> list:
    """抽完之后的高等级保底。返回报告说明（空列表 = 不需要干预）。

    `rng` 必须是**派生随机源**（不消耗主随机流），否则 lv=0/已达标的样本
    也会被改动随机序列，所有既有基线一起漂。
    """
    if lv <= 0:
        return []
    real = effective_level(state)
    if real >= lv:
        return []
    locks = set(locks or ())
    dropped = set((scene_index().get(str(preset_id or "")) or {})
                  .get("drop_fields") or ())
    # ⚠️ v4.2.1：**被选项权重点名的字段，保底不得改写**。
    #    实测：点名「交尾压制 = 3」在 L4 下被保底换成了 `double penetration`
    #    （`LEVEL_FLOOR_ORDER` 第一个就是 n_act）—— 用户看到的是"点名了却没出来"。
    #    跳过它继续看后面的载体（n_pos / n_orgasm / n_cum / n_theme …），
    #    档位照样能抬，只是不用用户点名的那一项。
    _forced_keys = {f[0] for f in (state.get("_forced_tags") or ())}
    tried = []
    for key in LEVEL_FLOOR_ORDER:
        if key in locks or key in dropped or key in _forced_keys:
            continue
        if blocked_by_weight(weights, key):
            continue
        _sec, fld = field_by_key(key)
        if not fld:
            continue
        cands = level_floor_candidates(key, lv, state)
        if not cands:
            tried.append(key)
            continue
        pick = weighted_pick(
            rng, cands,
            lambda it: max(0.0, option_weight(weights, key, it[1])))
        if pick is None:
            pick = cands[0]
        state[key] = pick[1]
        return [f"高等级保底：实际 L{real} < 选 L{lv}，"
                f"补 `{fld.get('lab') or key}` = {pick[0]}（{pick[1]}）"]
    return [f"高等级保底：实际 L{real} < 选 L{lv}，但无可用载体"
            f"（已锁 / 已闭麦：{'、'.join(tried) or '全部字段'}）—— 本张会低于所选档"]


def _field_tags(field_key: str) -> set:
    """某个字段所有选项拆成单标签后的集合（缓存在 _CACHE）。

    给"成品层"的服装互斥用：成品里出现某个标签时，光看标签不知道它来自
    哪个字段，得反查词库才能判断"这算不算具体服装"。
    """
    if "field_tags" not in _CACHE:
        m = {}
        for s in sections():
            for f in s["fields"]:
                bag = set()
                for _zh, en in f.get("opts", []):
                    for t in str(en).split(","):
                        t = t.strip().lower()
                        if t:
                            bag.add(t)
                m[f["key"]] = bag
        _CACHE["field_tags"] = m
    return _CACHE["field_tags"].get(field_key) or set()


def repair(tags, lv: int, extra_nl=None):
    """对**成品标签串**做检查 + 自动修复。返回 (新标签列表, 修复记录列表)。

    修三类：
      ① 档位    —— 超档标签剔除（已登记校准项除外，见 LEVEL_DEMOTE）；
      ② 结构互斥 —— 数量 / 视角 / 眼罩 / 服装 / 设备景深 / 体位（判定看成品）；
      ③ 格式    —— 下划线归一成空格 + 去重保序。

    ⚠️ 刻意**不修**的（写明理由，免得以后有人"顺手补上"）：
      · 超长且不在词库内的标签（F11）—— 那可能正是用户手写的作品名，
        删掉等于替用户删内容；留在报告里给人看。
      · 标签数量（F1）—— 那是密度自适应的活，在这里削只会越削越少。
      · 状态类致命项（F15 准入 / F16 槽位映射）—— 它们看的是字段，成品层
        看不到字段归属，只能由生成流程自己保证（force/lock 注入的越档值
        由 ① 兜住）。
    """
    fixes = []
    out = []

    # ── ③-a 格式：下划线归一 + 去重保序 ─────────────────────────────
    # ⚠️ 规则与自检 F12 完全一致（`score_` 例外）—— 不一致的话会出现
    #    "修完还是红"。
    # ⚠️ v5.0.10：**角色 tag 例外**。`texas_(arknights)` 里的下划线是
    #    danbooru tag 的组成部分，不是格式错误 —— 归一成 `texas (arknights)`
    #    会削弱模型识别率，正是本次改动要解决的问题。
    _role_ok = char_output_tag_set()
    norm, seen = [], set()
    for t in tags:
        t = str(t or "").strip().lower()
        if not t:
            continue
        fixed = t
        if "_" in t and "score_" not in t and t not in _role_ok:
            fixed = re.sub(r"[\s_]+", " ", t).strip()
        if fixed != t:
            fixes.append(f"格式：`{t}` → `{fixed}`")
        if fixed and fixed not in seen:
            seen.add(fixed)
            norm.append(fixed)

    # ── ① 档位：超档剔除（已登记校准项除外）──────────────────────────
    # ⚠️ 用 tag_level（含逐选项上抬折算），不用裸的 grade_nsfw ——
    #    否则手写补句里的 `pov crotch` 会被判 0 档而混进低档正文。
    calibrated = _calibrated_tags()
    # v4.5.0：`extra_nl` = 运行时生成的整句（多角色外貌绑定句），与静态整句同权
    _nl = nl_sentences(extra_nl)
    for t in norm:
        # ⚠️ v4.4.2：**自然语言整句不是 tag** —— 见 nl_sentences() 的说明。
        if t not in calibrated and t in _nl:
            out.append(t)
            continue
        if tag_level(t) > lv and t not in calibrated:
            fixes.append(f"档位：剔除超 {lv} 档的 `{t}`")
            continue
        out.append(t)

    def _drop(pred, why):
        """剔除命中的标签（就地改 out）。"""
        hit = [t for t in out if pred(t)]
        if hit:
            fixes.append(why + "：" + ", ".join(f"`{h}`" for h in hit))
            keep = [t for t in out if not pred(t)]
            out[:] = keep

    def _replace(old, new, why):
        if old in out and new not in out:
            out[out.index(old)] = new
            fixes.append(why + f"：`{old}` → `{new}`")
        elif old in out:
            _drop(lambda t: t == old, why + "（保留已有的新写法）")

    # ── ② 结构互斥（看成品，不看字段）──────────────────────────────
    # 服装：判据与 F9 一致，只认最严格的「全裸」——
    #   state 层的 sanitize 用的是 4 词表（含 topless/bottomless），那是**字段语义**
    #   下的判断（选了"仅上裸"就不该再抽整套服装）；成品层没有字段语义，
    #   `topless` + 半身裙是站得住的组合，不能连坐。
    if "completely nude" in out or "nude with accessories only" in out:
        outfit = _field_tags("outfit")
        _drop(lambda t: t in outfit, "服装互斥（全裸）")
    if "pantyhose" in out and ("bare feet" in out or "barefoot" in out):
        _drop(lambda t: t in ("bare feet", "barefoot"), "服装互斥（连裤袜与赤足）")

    # 眼罩：与 F8 同口径（眼镜按 `glasses` 判，墨镜不算 —— F8 也只认 glasses）
    if "blindfold" in out:
        _drop(lambda t: t in _PUPIL_TAGS, "眼罩互斥（蒙眼无法呈现心形瞳/翻白眼）")
        _drop(lambda t: t == "glasses", "眼罩互斥（眼罩与眼镜冲突）")

    # 数量：与 F6 同口径（solo 与多人标签互斥）
    if "solo" in out and any(t in _MULTI_TAGS for t in out):
        _drop(lambda t: t == "solo", "数量互斥（solo 与多人标签冲突）")

    # 视角：与 F7 / sanitize 同口径
    if any("pov" in t for t in out):
        _drop(lambda t: t == "full body", "视角互斥（POV 看不到自身全身）")
    if "from above" in out and "from below" in out:
        _drop(lambda t: t == "from below", "视角互斥（俯拍与仰拍同现）")
    if "from above" in out and "looking up" in out:
        _replace("looking up", "looking at viewer", "视角互斥（俯拍时的抬眼）")
    if "from below" in out and "looking down" in out:
        _replace("looking down", "looking at viewer", "视角互斥（仰拍时的垂眼）")
    if "facing away" in out and "looking at viewer" in out:
        _drop(lambda t: t == "looking at viewer", "视角互斥（背对却注视镜头）")

    # 设备 × 景深：与 S5 / sanitize 同口径
    if any(t in _LOFI_TAGS for t in out) and any(t in _PRO_DOF for t in out):
        _drop(lambda t: t in ("focus on face", "depth of field, blurry background"),
              "设备与画质（低画质设备不配专业景深效果）")

    # 体位：与 F14 同口径
    if any(t in _STAND_TAGS for t in out) and any(t in _LIE_TAGS for t in out):
        _drop(lambda t: t in _LIE_TAGS, "体位互斥（站立性行为与俯卧体态矛盾）")

    # 修复过程可能造出重复（_replace 改成了已存在的值等）→ 再收一次
    dedup, seen2 = [], set()
    for t in out:
        if t not in seen2:
            seen2.add(t)
            dedup.append(t)
    return dedup, fixes


def blocked_by_weight(weights: dict, field_key: str) -> bool:
    """该字段是不是被用户用权重明确关掉了。

    ⚠️ 语义：**权重 0 = 用户明确说"不要这个"**，是一个强指令。
       所以除了随机抽取要尊重它，两条自动写入路径也必须尊重：
         · 角色外貌对齐（apply_appearance）
         · 法典场景填充（apply_preset）
       曾经漏过这一条：用户把「发色」权重设 0、或把整个「头发」段权重设 0，
       结果抽到角色时 APPEAR 表照样把 hair_color 写了进去 ——
       用户看到的是"我明明关了，它还是出来了"。
    """
    sec = _field_section().get(field_key)
    if sec and section_weight(weights, sec) <= 0.0:
        return True
    return field_weight(weights, field_key) <= 0.0


def char_by_trigger() -> dict:
    """{trigger: 角色记录}。给外貌自动对齐用。

    ⚠️ role 字段存的是 **trigger**（`jane doe, zenless zone zero`），
       不是 tag，所以这里按 trigger 建索引。

    ⚠️ v3.46.1：同一个 trigger 会被**多个形态**共用（全库 50 组，如
       `hatsune miku, vocaloid` 同时属于 hatsune_miku 与 hatsune_miku_(append)；
       叶瞬光的 `ye shunguang, zenless zone zero` 同时属于本体与 enlightened_state）。
       原先写 `m[trig] = c` —— 后出现的那条覆盖前面的，于是**选本体却命中空数据的
       皮肤形态**（实测选中叶瞬光后 appearance_for 直接返回空表，发色瞳色全随机）。
       改为先到先得：库里本体排在皮肤前面，基础形态胜出。
    """
    if "char_by_trigger" not in _CACHE:
        m = {}
        for c in characters_data().get("characters", []):
            m.setdefault((c.get("trigger") or "").strip().lower(), c)
        _CACHE["char_by_trigger"] = m
    return _CACHE["char_by_trigger"]


_CHAR_HAIR_PATH = os.path.join(_HERE, "presets", "char_hair_style.json")
HAIR_STYLE_FIELDS = ("hair_len", "hair_tie", "hair_curl", "hair_acc")

# 「人物特征」字段（v4.2.0）：**有角色时归角色官方设定**，所以选项权重 3.0
# 在这些字段上不强制（硬塞会得到"官方角色 + 不属于她的发色/头饰"）。
# 清单 = `apply_appearance()` 写的那 5 个 + `apply_hair_style()` 写的那 4 个。
# ⚠️ 不在这里的字段（情绪 / 嘴部 / 眼神 / 姿势 / 场景 / 服装 / 指甲 …）都是
#    "非人物特征"，选项权重 3.0 **无论勾不勾「不指定角色」都必须生效**。
CHAR_APPEAR_FIELDS = frozenset(
    {"hair_color", "eyes_color", "figure", "skin", "face_shape"}
    | set(HAIR_STYLE_FIELDS))

# 「表情三件套」（v4.2.0）：与场景档位联动 —— L0/L1 场景里权重说了算
# （用户点「哭泣 = 3」就该哭），L2 及以上场景的表情是场景立意的一部分，
# 由场景预设说了算（用户口径：「L3+ 场景表情必出，场景优先」）。
EXPRESSION_FIELDS = frozenset({"emotion", "mouth", "eye_gaze"})


def char_hair_style() -> dict:
    """角色官方发型表（v3.45.0）。

    结构 `{"styles": {tag: {hair_len / hair_tie / hair_curl: en}}}`。
    首次调用读盘；**改文件后必须重启**（与词库 / 角色库同一条缓存纪律）。
    缺文件或损坏 → 空表（特性整体关掉，不抛异常）。
    """
    if "char_hair" not in _CACHE:
        try:
            doc = _load_json(_CHAR_HAIR_PATH)
        except Exception:
            doc = {}
        styles = doc.get("styles") if isinstance(doc, dict) else None
        _CACHE["char_hair"] = styles if isinstance(styles, dict) else {}
    return _CACHE["char_hair"]


def hair_style_for(role_trigger: str) -> dict:
    """按角色 trigger / tag 取官方发型三项；只返回**合法且非空**的项。

    ⚠️ 合法性用词库现算（`field_by_key`）：表里写错词就当作没有这一项 ——
       词库外的串写进 state 会静默失效，用户会以为"开了却没效果"。
    """
    trig = (role_trigger or "").strip().lower()
    if not trig:
        return {}
    idx = _CACHE.get("char_hair_index")
    if idx is None:
        idx = {}
        for tag, rec in char_hair_style().items():
            if isinstance(rec, dict):
                idx[str(tag).strip().lower()] = rec
        for c in characters_data().get("characters", []):
            t = str(c.get("tag") or "").strip().lower()
            tr = str(c.get("trigger") or "").strip().lower()
            if tr and t in idx:
                idx.setdefault(tr, idx[t])
        _CACHE["char_hair_index"] = idx
    rec = idx.get(trig)
    if not isinstance(rec, dict):
        return {}
    out = {}
    for key in HAIR_STYLE_FIELDS:
        val = str(rec.get(key) or "").strip().lower()
        if not val:
            continue
        # v3.45.0：「不束发」必须能表达 —— hair_tie 的 13 个选项全是"束起来"，
        # 没有"散开"这一档。写 none = 把该字段清空（不输出任何束发标签），
        # 否则披发角色会被随机塞进双马尾/麻花辫。
        if val in ("none", "散开", "不束发", "loose"):
            out[key] = "none"
            continue
        _s, fld = field_by_key(key)
        allowed = {str(o[1]).strip().lower() for o in (fld or {}).get("opts", [])}
        if val in allowed:
            out[key] = val
    return out


HAIR_PICK_AUTO = "角色标签发型"   # 取角色标签/官方发型表里的那一套（即 char_hair_style）
HAIR_PICK_ALIASES = ("角色官方", "官方发型", "auto")   # 旧值/别名，一律按「角色标签发型」处理
# 互斥组 G2 的两侧：需要长发的束法 / 算作短发的长度值
HAIR_LONG_TIES = {"twintails", "low ponytail", "high ponytail", "side ponytail",
                  "braid", "single braid", "twin braids", "hair bun", "double bun",
                  "drill hair", "half updo", "ponytail"}
HAIR_SHORT_LENS = {"short hair", "bob cut"}
# extras 里可能写死的发长 / 束法标签（用户显式指定同维度时要剔掉这些）
HAIR_LEN_TAGS = {"short hair", "long hair", "very long hair", "medium hair",
                 "shoulder-length hair", "bob cut"}
HAIR_TIE_TAGS = {"twintails", "ponytail", "low ponytail", "high ponytail",
                 "side ponytail", "braid", "single braid", "twin braids",
                 "hair bun", "double bun", "drill hair", "half updo"}
HAIR_PICK_RANDOM = "随机"


HAIR_PICK_SEP = "｜"     # 选项里「中文｜英文标签」的分隔符


def hair_pick_strip(value: str) -> str:
    """把下拉里带中文的选项还原成**纯英文标签**。

    `very long hair｜超长发`（或 `超长发｜very long hair`）→ `very long hair`。
    两个方向都认：拼装顺序改了也不会静默失效。
    """
    v = str(value or "").strip()
    if HAIR_PICK_SEP not in v:
        return v
    parts = [x.strip() for x in v.split(HAIR_PICK_SEP) if x.strip()]
    if not parts:
        return ""
    # 英文标签一定全是 ASCII（`very long hair`、`bob cut`），中文那半含全角字符。
    # 用这个判据比猜位置稳 —— 将来把拼装顺序改成 `英文｜中文` 也不用改这里。
    for p in parts:
        if p.isascii():
            return p
    return parts[-1]


def hair_pick_options(key: str) -> list:
    """某个发型字段的下拉选项：角色标签发型 / 随机 / 该字段词库全部取值（带中文）。

    三个固定项排在最前，保证「跟角色」和「随机」永远可选 ——
    即使某字段词库为空，这两个选项仍在。

    ⚠️ 选项文本是 `中文｜英文标签`（v3.47.1 起）。英文那半才是**写进 state 的值**，
       所以取值前必须过 `hair_pick_strip()`；后端仍然接受不带中文的裸标签
       （老工作流里存的就是裸标签）。
    """
    out = [HAIR_PICK_AUTO, HAIR_PICK_RANDOM]
    try:
        _s, fld = field_by_key(key)
        for o in (fld or {}).get("opts", []):
            en = str(o[1])
            zh = str(o[0] or "").strip()
            label = (zh + HAIR_PICK_SEP + en) if (zh and zh != en) else en
            if label not in out:
                out.append(label)
    except Exception:
        pass
    return out


def apply_hair_style(state: dict, weights: dict = None, picks: dict = None) -> list:
    """把发型写进 state（长度 / 束法 / 卷度 / 发饰）。返回被改的字段。

    `picks`（v3.47.0）逐项控制：`{hair_len: "角色官方"|"随机"|具体值, ...}`。
    - 全部为「角色官方」或 picks 为空 → 走**旧路径**（整组跟角色官方发型），零漂移
    - 某项「随机」→ 该字段清空，交给常态随机
    - 某项具体值 → 强写（"none" = 清空，表达「不束发」）

    与 `apply_appearance` 同一套语义：用户把某字段权重设 0 就不写。
    """
    hs = hair_style_for(state.get("role") or "") or {}
    _picks = {k: hair_pick_strip(str((picks or {}).get(k) or HAIR_PICK_AUTO))
              for k in HAIR_STYLE_FIELDS}
    _picks = {k: (HAIR_PICK_AUTO if v in HAIR_PICK_ALIASES else v)
              for k, v in _picks.items()}
    _explicit = any(v not in (HAIR_PICK_AUTO, "") for v in _picks.values())
    weights = weights or empty_weights()

    if _explicit:
        # ⚠️ 互斥组 G2：`short hair` × `twintails|long hair|very long hair`。
        #    用户显式选了「双马尾」而角色标签是短发时，两者会被 apply_mutex 判为
        #    矛盾 —— 实测结果是把**双马尾剔掉**（B 侧），用户看到"选了却没生效"。
        #    这里提前适配：显式束法需要长发、而长度仍是「角色标签发型」且为短发时，
        #    把长度换成兼容的长发。用户显式指定了长度则完全尊重，不覆盖。
        _tie_pick = _picks.get("hair_tie") or ""
        _len_pick = _picks.get("hair_len") or ""
        _len_fix = (_tie_pick not in (HAIR_PICK_AUTO, HAIR_PICK_RANDOM, "")
                    and _len_pick == HAIR_PICK_AUTO
                    and _tie_pick in HAIR_LONG_TIES)

        # v3.47.0：记下用户**显式指定**的字段（不含「角色标签发型」）。
        # 为什么需要：char_feat.json 的 `drop` 会把这些字段清空（拉姆/雷姆等 12 个角色
        # drop 了 hair_tie / hair_len），而 `_collect_with_feat` 在 collect 之前执行 drop
        # —— 于是选择器写的值被无声清掉，表现为"选了没反应"。显式指令优先于 drop。
        _xh_list = [k for k in HAIR_STYLE_FIELDS
                    if _picks[k] not in (HAIR_PICK_AUTO, "")]
        # G2 兼容把长度从短发改成 long hair 时，长度也算"被动显式"——
        # 否则 extras 里的 `short hair` 会留下来，和双马尾凑成矛盾组合
        # （实测正文同时出现 `short hair, twintails`）。
        if _len_fix and str(hs.get("hair_len") or "") in HAIR_SHORT_LENS:
            if "hair_len" not in _xh_list:
                _xh_list.append("hair_len")
        state["_hair_picks_explicit"] = _xh_list

        changed = []
        for key in HAIR_STYLE_FIELDS:
            pick = _picks[key]
            if pick == HAIR_PICK_RANDOM:
                # 随机 = 让常态随机接管（先清空，后续字段抽取会重新填）
                if state.get(key):
                    state[key] = ""
                changed.append(key)
                continue
            if pick in (HAIR_PICK_AUTO, ""):
                val = hs.get(key)
                # G2 兼容：显式选了需要长发的束法，而角色标签给的是短发 →
                # 换成 long hair，否则 apply_mutex 会把用户选的束法剔掉。
                if key == "hair_len" and _len_fix and str(val or "") in HAIR_SHORT_LENS:
                    val = "long hair"
                if val is None:
                    continue          # 角色没有这一项 → 保持随机，不写
            else:
                val = pick
            if blocked_by_weight(weights, key):
                continue
            state[key] = "" if val == "none" else val
            changed.append(key)
        if changed:
            state["_hair_style_applied"] = True
        return changed

    # ── 旧路径（全部「角色官方」）：整组跟随，行为与 v3.45.0 完全一致 ──
    if not hs:
        return []
    changed = []
    for key, val in hs.items():
        if blocked_by_weight(weights, key):
            continue
        # "none" = 该子项官方就是不适用（如 hair_tie 的「不束发」）→ 清空
        state[key] = "" if val == "none" else val
        changed.append(key)
    if changed:
        state["_hair_style_applied"] = True
    return changed


_CHAR_FEAT_PATH = os.path.join(_HERE, "presets", "char_feat.json")


_TERM_HINTS_PATH = os.path.join(_HERE, "presets", "term_hints.json")


def term_hints() -> dict:
    """复杂词条的自然语言补充句（v4.4.0）。

    结构 `{field: {en(lower): "一句话"}}`。首次调用读盘，**改文件后必须重启**。

    为什么需要它（2026-10-29 实测）：`n_act` / `n_theme` 里有 8 条词条
    只给 tag 串时**完全画不出来** —— `irrumatio` 画成水下游泳、
    `facesitting` 画成樱花街拍照、`double penetration` 画成双女自拍。
    在正文结尾补一句英文自然语言后，这 8 条全部救活（严格一致 0/15 → 10/15）。
    原因：自然语言带主谓宾与物理细节，而 tag 堆只是一个名词，
    模型只能靠先验瞎猜（"双击齐下"落成两个女孩）。
    """
    if "term_hints" not in _CACHE:
        try:
            doc = _load_json(_TERM_HINTS_PATH)
        except Exception:
            doc = {}
        out = {}
        if isinstance(doc, dict):
            for fk, sub in doc.items():
                if str(fk).startswith("_") or not isinstance(sub, dict):
                    continue
                out[str(fk)] = {str(k).strip().lower(): str(v).strip()
                                for k, v in sub.items() if v}
        _CACHE["term_hints"] = out
    return _CACHE["term_hints"]


def term_hint_text(state: dict) -> str:
    """本轮**被点名（3.0）且抽中**的 n_act / n_theme 词条 → 拼成补充句。

    只看 `_forced_tags`：它记的是"这一轮真的写进正文的点名项"，
    所以 hint 与正文严格同源 —— 不会出现"正文没有这个动作、
    结尾却在描述这个动作"的错位。没点名（走随机）时不加，
    保证既有随机路径逐字节零漂移。
    """
    hints = term_hints()
    if not hints:
        return ""
    out = []
    for k, _zh, en in (state.get("_forced_tags") or ()):
        t = (hints.get(k) or {}).get(str(en).strip().lower())
        if t:
            out.append(t)
    return " ".join(out)


def char_feat_table() -> dict:
    """角色固定特征表（v3.46.0）。

    结构 `{"feats": {tag: {"extras": [...], "pick": {池名: [...]}, "drop": [...]}}}`。
    首次调用读盘；**改文件后必须重启**（与词库 / 角色库同一条缓存纪律）。
    缺文件或损坏 → 空表（特性整体关掉，不抛异常）。
    """
    if "char_feat" not in _CACHE:
        try:
            doc = _load_json(_CHAR_FEAT_PATH)
        except Exception:
            doc = {}
        feats = doc.get("feats") if isinstance(doc, dict) else None
        _CACHE["char_feat"] = feats if isinstance(feats, dict) else {}
    return _CACHE["char_feat"]


# 兜底通道要跳过的标签：人数词由 count 字段管，胸部词由 figure 字段管，
# 重复塞进 extra 只会让正文出现两遍同一个意思。
_CUSTOM_CHAR_SKIP = frozenset({
    "1girl", "1boy", "2girls", "multiple girls", "no humans", "solo",
    "huge breasts", "large breasts", "medium breasts", "small breasts", "flat chest",
})


def is_custom_role(role_trigger: str) -> bool:
    """这条角色记录是不是用户自己在面板里手建的（`src == "user"`）。"""
    c = char_by_trigger().get(str(role_trigger or "").strip().lower())
    return bool(c) and str(c.get("src") or "") == "user"


def _custom_tags_extra(role_trigger: str):
    """自定义角色的「词库外标签」兜底 → `(extras, [], {})`。

    内置角色走 `presets/char_feat_table.json`（58 条，人工整理过 extras/drop/set），
    自定义角色没有这张表，于是 `appearance_for()` 只挑得出 `hair` / `eyes` /
    胸部三个槽位，剩下的标签**全部消失**。

    这里把"没有槽位可落"的标签原样交出去，由调用方拼进 `body_extra`
    （标签区末尾、自然语言句之前）—— ⚠️ 不能拼 `tail_extra`：那里排在
    `scene_nl` 之后，T5 会当散文读，权重掉一截（见 `collect()` 的说明）。

    排序保持用户输入顺序（用户是按"先主体后配件"写的）。
    """
    c = char_by_trigger().get(str(role_trigger or "").strip().lower())
    if not c or str(c.get("src") or "") != "user":
        return [], [], {}
    slot = {str(v or "").strip().lower() for v in appearance_for(role_trigger).values()}
    out = []
    for t in c.get("tags") or []:
        t = str(t).strip().lower()
        if not t or len(t) > 40:
            continue
        if t in slot or t in _CUSTOM_CHAR_SKIP:
            continue
        if t not in out:
            out.append(t)
    return out, [], {}


def _char_feat_index() -> dict:
    """特征表的两种 key 都建索引：**角色 tag** 与 **完整 trigger**。

    `presets/char_feat.json` 的 `feats` 是按 tag 写的（`emilia_(re:zero)`），
    但引擎各处拿到的是 trigger（`emilia, re:zero`）—— 不建映射就永远查不到。
    """
    idx = _CACHE.get("char_feat_index")
    if idx is None:
        idx = {}
        for tag, rec in char_feat_table().items():
            if isinstance(rec, dict):
                idx[str(tag).strip().lower()] = rec
        for c in characters_data().get("characters", []):
            t = str(c.get("tag") or "").strip().lower()
            tr = str(c.get("trigger") or "").strip().lower()
            if tr and t in idx:
                idx.setdefault(tr, idx[t])
        _CACHE["char_feat_index"] = idx
    return idx


def _feat_goes_body(role_trigger: str) -> bool:
    """这条角色的特征标签该拼 **body_extra** 还是 **tail_extra**。

    v5.4.1 起自定义角色（`src=="user"`）走 body —— 它们的 extras 是"兜底拼出来的
    整串词库外标签"，拼 tail 会排到 `scene_nl` 之后被 T5 当散文读。

    v5.4.3 起并进内置库的那批角色 `src` 变成 `manual`，`is_custom_role()` 不再为真，
    所以在 `presets/char_feat.json` 的条目上写 `"body": true` 来标记，继续走 body。
    其余 58 条内置角色的行为**保持原样**（走 tail），避免基线漂移。
    """
    if is_custom_role(role_trigger):
        return True
    rec = _char_feat_index().get(str(role_trigger or "").strip().lower())
    return bool(rec and rec.get("body"))


def char_feat_for(role_trigger: str, seed=None):
    """取角色的固定特征 → `(extra 标签列表, 要清空的字段列表, 要强设的字段表)`。

    为什么需要这张表：引擎的特征通道只有 5 个槽位（hair / eyes /
    figure / skin / face）＋ 发型四项。**词库外标签**（`pointy ears`、
    `visor` …）没有槽位可落，`appearance_for()` 会把它们直接丢掉 ——
    写进角色库 `tags` 也没用。而 extra 是唯一合规的落点。

    `pick` 池用 `derive_seed` 派生，**不消耗 pass_rng** → 表外的角色
    一个标签不加、一次随机不抽，固定 seed 的既有基线逐字节不变。

    `set` 是给**词库内字段**用的强制值（如把 APPEAR 表写错的瞳色改回来）。
    APPEAR 表优先级最高、覆盖层改不动，所以只能在这里盖。
    """
    trig = (role_trigger or "").strip().lower()
    if not trig:
        return [], [], {}
    idx = _char_feat_index()
    rec = idx.get(trig)
    if not isinstance(rec, dict):
        # v5.4.1（用户 2026-11-05 20:23「给角色库增加几个 AI 的虚拟角色形象」）：
        # 自定义角色**没有**预生成的特征表（`char_feat_table()` 只有 58 条内置角色），
        # 于是 `fin ears` / `blue scales` / `chubby` 这些词库外标签无处可落，被
        # `appearance_for()` 静默丢掉 —— 实测 12 条标签只有 2 条进正文（发色 + 瞳色），
        # 出图当然"还原度不够"。这里给自定义角色补一条兜底通道。
        # ⚠️ 判据是 `src == "user"`：内置 2330 条（wardrobe/safebooru/manual）零影响。
        return _custom_tags_extra(trig)
    extras = []
    for t in rec.get("extras") or []:
        t = str(t).strip().lower()
        if t and t not in extras:
            extras.append(t)
    # 变体池：每池抽一条（如眼罩灯带颜色）。派生种子，不吃 pass_rng。
    picks = rec.get("pick")
    if isinstance(picks, dict):
        for name, pool in picks.items():
            cand = [str(x).strip().lower() for x in (pool or []) if str(x).strip()]
            if not cand:
                continue
            if seed is None or int(seed) < 0:
                pick = cand[random.Random().randrange(len(cand))]
            else:
                pick = cand[derive_seed(seed, "charfeat:" + str(name), 0) % len(cand)]
            if pick not in extras:
                extras.append(pick)
    drops = []
    for k in rec.get("drop") or []:
        k = str(k).strip()
        if k and k not in drops:
            drops.append(k)
    # `set` 只认词库里真实存在的字段与选项 —— 写错就当没写，别静默变成脏标签
    sets = {}
    for k, v in (rec.get("set") or {}).items():
        _s, fld = field_by_key(str(k).strip())
        if not fld:
            continue
        allowed = {str(o[1]).strip().lower() for o in fld.get("opts", [])}
        val = str(v).strip().lower()
        if val in allowed:
            sets[str(k).strip()] = val
    return extras, drops, sets


_OUTFIT_PATH = os.path.join(_HERE, "presets", "character_outfits.json")

# 服装字段全集。预设启用时**默认全部闭麦** —— 漏一个就会出现
# 「预设的白裙 + 随机抽到的泳装」同时在场。
OUTFIT_FIELDS = ("outfit", "top", "bottom", "socks", "sock_color", "shoes",
                 "accessory", "cloth_color", "cloth_mat", "cloth_state_sfw",
                 "cloth_nsfw", "n_fabric", "n_shoes_keep")

# 「这件衣服叫什么」的字段（不含颜色 / 材质 / 状态这类**调色板锚点**）。
# v5.0.13：只有这几类才用 `_covered_by_tags()` 判"预设 tags 是不是已经说得更具体"，
# 颜色材质字段照旧强设 —— 否则 `black skirt` 会把 `cloth_color = black` 判成重复。
GARMENT_FIELDS = ("outfit", "top", "bottom", "socks", "shoes", "accessory")


def outfits_data() -> dict:
    """角色服装预设 `{role_tag: {enabled, list}}`。

    v5.0.2（用户 2026-11-02）：「角色编辑界面增加服装预设 … 启用后优先于
    随机权重，锁定人物服装」。

    ⚠️ 这是**用户数据**，不是构建产物 —— 放 `presets/character_outfits.json`，
       不要塞进 `anima_v4_characters.json`（那是 build_characters.py 的产物，
       重跑会整份覆盖）。写操作走 `clear_cache()` 后重新读盘。
    """
    if "outfits" not in _CACHE:
        try:
            with open(_OUTFIT_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            data = raw.get("outfits") or {}
            _CACHE["outfits"] = data if isinstance(data, dict) else {}
        except Exception:
            _CACHE["outfits"] = {}
    return _CACHE["outfits"]


def outfit_rec(role_trigger: str) -> dict:
    """角色 → `{enabled, list}`。没有就空 dict。

    ⚠️ 三种写法都要认（实测踩到，第一版只认原样 → 预设静默全失效）：
       · 库里的 tag 形态   `tifa_lockhart`（下划线，`character_outfits.json` 用它做 key）
       · 完整 trigger     `tifa lockhart, final fantasy`（空格 + 作品名）
       · 裸角色名         `tifa lockhart`（手填常见）
    运行时拿到的往往是后两种，所以必须归一化到第一种。
    """
    trig = (role_trigger or "").strip().lower()
    if not trig:
        return {}
    d = outfits_data()
    head = trig.split(",")[0].strip()
    cands = [trig]
    if head and head != trig:
        cands.append(head)
    for c in list(cands):
        u = c.replace(" ", "_")
        if u not in cands:
            cands.append(u)
    # v5.4.3：自定义角色的 **tag** 是 `user_<slug>`（`make_user_tag` 生成的），
    # 而服装预设前端 `openOutfitDialog()` 是拿 **tag** 去查 `ui.outfits[role]` ——
    # 引擎这边却拿 trigger 查。两边要的形态不一样，key 只写一种就会有一边查不到
    # （实测：服装数据明明在库里，面板上却显示「还没有服装预设」）。
    # 这里把 `user_` 变体也加进候选，两种 key 都能命中。
    for c in list(cands):
        u = "user_" + c.replace(" ", "_")
        if u not in cands:
            cands.append(u)
    for c in cands:
        rec = d.get(c)
        if isinstance(rec, dict):
            return rec
    # 兜底：`furina` → `furina_(genshin_impact)`、`castorice` → `castorice_(honkai:...)`
    # 只认「裸名 + 作品后缀」这一种形态，避免 `souryuu` 误命中 `souryuu_(kancolle)`
    for c in cands:
        for suffix in ("_(", " ("):
            for k in d:
                if k.startswith(c + suffix):
                    rec = d.get(k)
                    if isinstance(rec, dict):
                        return rec
    return {}


def outfit_item(role_trigger: str) -> dict:
    """启用中的那一套（dict）；没启用 / id 对不上 → 空 dict。"""
    rec = outfit_rec(role_trigger)
    eid = str(rec.get("enabled") or "").strip()
    if not eid:
        return {}
    for o in rec.get("list") or []:
        if str(o.get("id") or "") == eid:
            return o if isinstance(o, dict) else {}
    return {}


def outfit_for(role_trigger: str, lv: int = 99):
    """启用中的那套服装 → `(extra 标签, 要清空的字段, 要强设的字段)`。

    与 `char_feat_for()` **同一个返回形状**，调用方直接合并三元组。
    没启用 / 档位不够 → 空三元组（对既有随机零影响）。

    ⚠️ 为什么走 extra 而不是词库字段：角色专属服装（`plugsuit` /
       `blue jacket` / `top hat` / `white tank top`）**根本不在词库池里**
       （实测 outfit 只有 70 项、accessory 只有 13 项）。塞进池子会污染
       2000+ 角色的随机抽取；而 extra 是唯一「不过 cap、不过互斥、
       不进随机池」的通道 —— 与 `char_feat` 的 extras 同一条路。
    """
    return _outfit_triplet(outfit_item(role_trigger), lv)


def _outfit_triplet(item: dict, lv: int = 99):
    """一套服装条目 → `(extra 标签, 要清空的字段, 要强设的字段)`。

    v5.1.0：`outfit_for()`（角色预设）与 `library_outfit()`（全局服装库）
    **共用这一份** —— 两条路的档位闸门、闭麦、`_covered_by_tags` 去重必须
    逐字一致，否则同一个词在两条路上会出现两种结果。
    """
    if not item:
        return [], [], {}
    try:
        olevel = int(item.get("level") or 0)
    except Exception:
        olevel = 0
    if int(lv) < olevel:
        return [], [], {}

    extras = []
    for t in item.get("tags") or []:
        t = str(t).strip().lower()
        if t and t not in extras:
            extras.append(t)

    drops = []
    for k in (item.get("drop") or OUTFIT_FIELDS):
        k = str(k).strip()
        if k and k not in drops:
            drops.append(k)

    # 写了 fields 的字段**必须同时闭麦**，否则会被随机值覆盖（set 在 drop 之后生效）
    #
    # ⚠️ v5.0.13（用户 2026-11-04）：「服装由预设决定」= 一套说一次，不许同一件衣服
    #    说两遍。手写 `fields` 的角色（蒂法 / 明日香 / 芙宁娜…14 套）会同时给出
    #    `fields.bottom = skirt` 与 `tags` 里的 `black skirt` —— 正文里就是
    #    `skirt, … , black skirt`，模型拿到两个粒度不同的裙子词，出图在
    #    "短裙"与"黑裙"之间摇摆。判据：**tags 已经把这一档说清楚了就不再 set**
    #    （tag 等于该值 / 以「该值」结尾 / 以「该值 + 空格」开头），只保留闭麦。
    #    只对**衣物类**字段判，`cloth_color` / `cloth_mat` 这类调色板锚点不参与
    #    —— `black skirt` 以 `black ` 开头，拿颜色字段去套会把锚点误删。
    sets = {}
    for k, v in (item.get("fields") or {}).items():
        k = str(k).strip()
        val = str(v).strip().lower()
        if not k or not val:
            continue
        _sec, fld = field_by_key(k)
        if not fld:
            continue
        allowed = {str(o[1]).strip().lower() for o in fld.get("opts", [])}
        if val in allowed:
            # tags 已经写得更具体 → 只闭麦，不再输出这个粗粒度值
            if k in GARMENT_FIELDS and _covered_by_tags(val, extras):
                if k not in drops:
                    drops.append(k)
                continue
            sets[k] = val
            if k not in drops:
                drops.append(k)
    return extras, drops, sets


def _covered_by_tags(val: str, extras) -> bool:
    """`val` 这件衣服是否已被 `extras` 里某个更具体的 tag 覆盖。

    `skirt` ⊂ `black skirt` / `pencil skirt` / `pleated skirt` → 覆盖。
    纯字符串后缀/前缀匹配，两边都加了空格边界，所以 `shirt` 不会命中
    `t-shirt`（那是另一种衣服）。
    """
    v = str(val or "").strip().lower()
    if not v:
        return False
    for t in (extras or []):
        t = str(t or "").strip().lower()
        if not t:
            continue
        if t == v or t.endswith(" " + v) or t.startswith(v + " "):
            return True
    return False


# ── v5.1.0：全局服装库（用户 2026-11-05）────────────────────────────────
#
# 「增加服装库，预设分类好各种丰富的整套服装搭配，UI 与角色库、场景搭配同级；
#   选择后原理与角色服装预设一致，可以直接套用。」
#
# 优先级口径（用户拍板）：**角色预设 > 服装库 > 随机**。
#   · 角色启用了自己的预设 → 该角色用它（档位不够就留空，**不拿服装库顶**，
#     否则"我明明给这个角色锁了衣服、出来的却是别的"会变成一个查不出的坑）；
#   · 角色没启用 → 用服装库启用中的那一套；
#   · 服装库也没启用 → 空三元组，随机路径与 v5.0.31 逐字节一致。
_LIBRARY_PATH = os.path.join(_HERE, "presets", "outfit_library.json")


def library_data() -> dict:
    """`{meta, items}`。坏文件 → 空库（面板退化成空列表，不抛）。"""
    if "outfit_library" not in _CACHE:
        try:
            with open(_LIBRARY_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
        items = raw.get("outfits")
        _CACHE["outfit_library"] = {
            "meta": meta,
            "items": [x for x in items if isinstance(x, dict)]
            if isinstance(items, list) else [],
        }
    return _CACHE["outfit_library"]


def library_active_item() -> dict:
    """服装库里启用中的那一套；没启用 / id 对不上 → 空 dict。"""
    d = library_data()
    aid = str((d.get("meta") or {}).get("active") or "").strip().lower()
    if not aid:
        return {}
    for o in d.get("items") or []:
        if str(o.get("id") or "") == aid:
            return o
    return {}


# 袜子 / 鞋子的判定词根。用来把套装 `tags` 里"属于这一类"的标签挑出来 ——
# 覆盖时**必须同时从 extras 剔除**，否则正文里会出现两双袜子（套装那双 + 你指定的）。
_SOCK_WORDS = ("thighhigh", "pantyhose", "sock", "stocking", "garter", "tights",
               "barefoot", "leg warmer", "kneehigh")
_SHOE_WORDS = ("heel", "boot", "shoe", "loafer", "sneaker", "sandal", "mary jane",
               "slipper", "geta", "uwabaki", "bare feet", "barefoot")


def _tag_class(t: str) -> str:
    """这个标签属于 socks / shoes / 都不是。"""
    t = str(t or "").strip().lower()
    if not t:
        return ""
    if any(w in t for w in _SOCK_WORDS):
        return "socks"
    if any(w in t for w in _SHOE_WORDS):
        return "shoes"
    return ""


def library_overrides() -> dict:
    """服装库的覆盖项（袜子 / 鞋子）。空串 = 跟随那套服装自己的搭配。"""
    try:
        from . import anima_v4_store as _S
        return _S.library_overrides()
    except Exception:
        meta = library_data().get("meta") or {}
        ov = meta.get("overrides")
        if not isinstance(ov, dict):
            return {"socks": "", "shoes": ""}
        return {k: str(ov.get(k) or "").strip().lower() for k in ("socks", "shoes")}


def library_scene_override() -> bool:
    """v5.9.0：服装库是否强制覆盖「场景搭配里写死的推荐服装」。

    True  = 旧行为（服装库赢）；False（默认）= 场景带具体衣物字段时整套让路。
    ⚠️ 与 `library_overrides()` 一样走 store 读盘（写操作后 `clear_cache()` 只清
       引擎侧 `_CACHE`，store 每次 `_library_load()` 都重读，所以改完立刻生效）。
    """
    try:
        from . import anima_v4_store as _S
        return bool(_S.library_scene_override())
    except Exception:
        return bool((library_data().get("meta") or {}).get("scene_override"))


def apply_library_overrides(extras, drops, sets):
    """把用户指定的袜子 / 鞋子盖到服装库那套上 → 新的三元组。

    v5.5.0（用户 2026-11-05 21:27）：服装库那套仍然负责整体穿搭，这里**只换两件**。
    三件事缺一不可：
      ① 从 `extras` 剔掉同类标签（套装写的是 `white thighhighs` / `pointed toe high heels`）；
      ② 把该字段加进 `drops`（否则随机值会把它顶掉）；
      ③ 在 `sets` 里写死新值（`drops` 只闭麦，`sets` 才赋值）。
    ⚠️ 换鞋时还要连 `n_shoes_keep` 一起清 —— 那是"高潮后保留哪双鞋"的字段，
       不换的话会在 NSFW 档出现两双鞋。
    """
    ov = library_overrides()
    if not any(ov.get(k) for k in ("socks", "shoes")):
        return extras, drops, sets
    extras = list(extras or [])
    drops = list(drops or [])
    sets = dict(sets or {})
    for k in ("socks", "shoes"):
        v = str(ov.get(k) or "").strip().lower()
        if not v:
            continue
        extras = [t for t in extras if _tag_class(t) != k]
        if k not in drops:
            drops.append(k)
        sets[k] = v
        if k == "shoes":
            if "n_shoes_keep" not in drops:
                drops.append("n_shoes_keep")
            sets.pop("n_shoes_keep", None)
    return extras, drops, sets


def library_outfit(lv: int = 99):
    """服装库启用中的那一套 → `(extra, drop, set)`。没启用 → 空三元组。

    v5.5.0：再叠一层用户的袜子 / 鞋子覆盖项。
    ⚠️ 档位不够时 `_outfit_triplet()` 返回空三元组（整套不生效），
       这时**不能**只盖袜子鞋子 —— 那会变成"衣服随机、袜鞋指定"，
       与"整套闸门"的语义相反。所以先判空。
    """
    tri = _outfit_triplet(library_active_item(), lv)
    if not tri[0] and not tri[1]:
        return tri
    return apply_library_overrides(*tri)


def effective_outfit(role_trigger: str, lv: int = 99,
                     scene_has_outfit: bool = False):
    """「这个角色现在穿什么」——角色预设优先，服装库兜底。

    ⚠️ 判据是 `outfit_item()` **有没有启用**，不是 `outfit_for()` 返不返东西：
       角色启用了 L3 的套装、当前档位是 SFW 时，`outfit_for()` 也会返回空 ——
       若拿"空"去判兜底，就会悄悄换成服装库那一套，与用户的显式选择打架。

    v5.9.0 `scene_has_outfit`：本轮场景**实际写入**了具体衣物字段（outfit /
      top / bottom / socks / shoes / accessory）时为 True。此时若「覆盖场景推荐
      服装」开关是关闭的，服装库**整套让路**（返回空三元组），让场景的推荐
      服装原样生效。
      · 只让服装库这一层：角色预设照常优先，档位闸门照常生效；
      · 整套让路包含袜子 / 鞋子覆盖项 —— `library_outfit()` 根本不会被调用，
        不会出现「衣服按场景、袜鞋按服装库」的半套状态；
      · 默认 `False` → 所有旧调用零漂移；开启开关后也与 v5.8.0 逐字节一致。
    """
    item = outfit_item(role_trigger)
    if item:
        return _outfit_triplet(item, lv)
    if scene_has_outfit and not library_scene_override():
        return [], [], {}
    return library_outfit(lv)


def appearance_for(role_trigger: str) -> dict:
    """给一个角色 trigger 找外貌数据。返回 {hair, eyes, figure, skin, face}。

    三个来源，优先级从高到低：
      ① 源 HTML 的 APPEAR 表 —— 人工核准，但只覆盖 221 条
      ② 角色库的 `hair` / `eyes` 字段（来自造型台，覆盖 ~2000 条）
      ③ 角色库的官方特征 tags —— 里面有 `aqua hair` / `green eyes` 这种
         现成标签，直接挑出来用

    为什么必须补这一层：角色库从 221 扩到 2687 之后，APPEAR 表一下就
    只覆盖 8% 了。如果不补，自检 F10（角色未绑定发色/瞳色）会大面积报红，
    而模型在没有发色瞳色时会自己乱猜。
    """
    trig = (role_trigger or "").strip().lower()
    if not trig:
        return {}

    # ① 人工资核准的表
    a = appear().get(role_trigger) or appear().get(trig)
    if a:
        return dict(a)

    # ②③ 角色库
    c = char_by_trigger().get(trig)
    if not c:
        return {}
    out = {}
    hair = (c.get("hair") or "").strip().lower()
    eyes = (c.get("eyes") or "").strip().lower()
    if hair:
        out["hair"] = hair if hair.endswith("hair") else f"{hair} hair"
    if eyes:
        out["eyes"] = eyes if eyes.endswith("eyes") else f"{eyes} eyes"

    # 从官方特征 tags 里捞（`very long hair` / `aqua eyes` …）
    for t in c.get("tags") or []:
        t = str(t).strip().lower()
        if not t or len(t) > 40:
            continue
        if "hair" in t and "hair" not in out:
            out["hair"] = t
        elif "eyes" in t and "eyes" not in out:
            out["eyes"] = t
        elif t in ("huge breasts", "large breasts", "medium breasts",
                   "small breasts", "flat chest") and "figure" not in out:
            out["figure"] = t
    return out


def duo_appearance_text(triggers) -> str:
    """多位角色**各自**的外貌，写成「谁有什么」的整句（v4.5.0）。

    为什么是整句而不是并列标签：
      danbooru 系提示词是**平铺**的，`blonde hair, black hair` 挤在一起时
      模型不知道哪个颜色属于谁 —— 单角色时靠 `hair_color` 字段对齐没问题，
      两位角色就只有一个字段可用。而自然语言能带**主谓绑定**
      （`artoria has blonde hair and green eyes. rin has black hair and blue eyes.`），
      这正是 v4.4.4 起反复验证有效的手段（复杂词条 0/15 → 11/15）。

    ⚠️ 句内**不能出现逗号** —— `collect()` 会按逗号把 tail_extra 拆成标签堆
      （v4.4.4 踩过）。分句一律用句号。

    ⚠️ 只有一位角色有外貌数据时不写：那与单角色对齐是同一件事，
      交给字段通道（走 `apply_appearance`）更准。
    """
    parts = []
    for t in list(triggers or [])[:3]:
        t = str(t or "").strip()
        if not t:
            continue
        a = appearance_for(t)
        if not a:
            continue
        bits = [str(a.get(k) or "").strip()
                for k in ("hair", "eyes") if str(a.get(k) or "").strip()]
        if not bits:
            continue
        name = role_output_name(t) or t.split(",")[0].strip()
        # ⚠️ 两处必须提前归一，否则整句与放行集合对不上：
        #    · 逗号 —— tail_extra 按逗号切句（重名角色保留 `eve,stellar blade`）；
        #    · 下划线 —— `repair()` 的"下划线归一成空格"会把正文换成 `ayaka sakai`，
        #      而放行集合里还是 `ayaka_sakai`，F11 照样报"超长未登记标签"。
        name = re.sub(r"\s+", " ", re.sub(r"[_,;\n]+", " ", name)).strip()
        parts.append(f"{name} has " + " and ".join(bits))
    if len(parts) < 2:
        return ""
    # 最后一道保险：整句里不许再有逗号
    return re.sub(r"[,\n;]+", " ", ". ".join(parts) + ".").strip()


def _duo_name(trigger: str) -> str:
    """整句里怎么称呼这个角色：显示名优先，逗号 / 下划线一律抹掉。

    ⚠️ 抹下划线不是洁癖：`repair()` 会把正文里的下划线归一成空格，
       而放行集合里若还是 `ayaka_sakai`，F11 会把它当"超长未登记标签"报红。
    """
    name = role_output_name(trigger) or str(trigger or "").split(",")[0].strip()
    return re.sub(r"\s+", " ", re.sub(r"[_,;\n]+", " ", name)).strip()


def _duo_join(parts, need: int = 2) -> str:
    """把"每人一句"拼成整段。

    `need` 是**最少几句才值得输出**：
      · 外貌 / 官方配件默认 2 —— 只有一位时说"谁长什么样"没有意义，
        单角色通道本来就更准（调用方只在多人分支里走这里）；
      · 服装写 `need=1`（v6.6.1）—— 双人里只有一位启用预设时，
        "<2 句就不写"会把她的官方服装整句吞掉，共享服装字段也不会被清空，
        两人最后穿上随机服装（2026-11-07 审查实测：texas 的
        `collared shirt / necktie / black vest` 变成随机 `hakama`）。
    """
    if len(parts) < max(1, int(need)):
        return ""
    return re.sub(r"[,\n;]+", " ", ". ".join(parts) + ".").strip()


def duo_outfit_text(triggers, lv: int = 99,
                    scene_has_outfit: bool = False) -> str:
    """双人时**各自**的启用服装，写成「谁穿什么」的整句（v5.0.7）。

    为什么必须有：`outfit_for()` 是单角色接口，而 `_collect_with_feat` 里那道
    「多人各有各的官方配件，对齐其中一个是错的」会把服装预设**一起跳过** ——
    于是两人都启用了官方服装，出来的还是随机皮衣与黑裙（用户 2026-11-02 实测）。
    平铺标签分不清"哪件是谁的"，只能走整句（与 `duo_appearance_text` 同一套办法）。

    ⚠️ 句内不许有逗号 —— `collect()` 会按逗号把 tail_extra 拆成标签堆，
       `X wears purple kimono, neck ribbon` 会被拆成两段互不相关的标签。
    ⚠️ **不受 `appearance_follow` 控制**：服装是用户手动启用的，不是"跟随"。
    """
    parts = []
    for t in list(triggers or [])[:3]:
        t = str(t or "").strip()
        if not t:
            continue
        # v5.1.0：角色预设优先、服装库兜底 —— 双人也要吃全局服装库，
        # 否则「服装库选了女仆装，单人出女仆、双人出随机」会显得像坏掉。
        # v5.9.0：把「场景带推荐服装」的事实透传下去，双人也得遵守让路开关，
        # 否则会出现「单人按场景穿、双人文案里还在写服装库那套」的 split-brain。
        _ex, _drop, _set = effective_outfit(t, lv, scene_has_outfit)
        bits = [str(x).strip() for x in (_ex or [])[:5] if str(x).strip()]
        if not bits:
            continue
        parts.append(f"{_duo_name(t)} wears " + " and ".join(bits))
    # v6.6.1：need=1 —— 双人里只有一位启用预设时也要写出来（见 _duo_join 说明）
    return _duo_join(parts, need=1)


def duo_feat_text(triggers, seed=None, lv: int = 99,
                  scene_has_outfit: bool = False) -> str:
    """双人时**各自**的角色固定特征（精灵耳 / 机械眼罩 / 羊角…），同上一套写法。

    与服装同一个坑：`char_feat_for()` 也是单角色接口，双人时被整块跳过，
    于是"官方配件"只有单人才出得来。

    ⚠️ 必须排除**服装句已经写过**的标签：甘雨的服装 tags 里就有 `goat horns`
    （它是"官方服装套"的一部分），特征句再写一遍 → `wears goat horns` +
    `has goat horns` 并存，模型会照着画两对角。
    """
    parts = []
    for t in list(triggers or [])[:3]:
        t = str(t or "").strip()
        if not t:
            continue
        _ex, _drop, _set = char_feat_for(t, seed)
        _otags = {str(x).strip().lower()
                  for x in (effective_outfit(t, lv, scene_has_outfit)[0] or [])}
        bits = [str(x).strip() for x in (_ex or [])[:6]
                if str(x).strip() and str(x).strip().lower() not in _otags]
        if not bits:
            continue
        parts.append(f"{_duo_name(t)} has " + " and ".join(bits[:5]))
    # v6.6.1：与服装同一口径 —— 只有一位有官方配件时也要写（否则她的
    # `goat horns` / `mechanical eyepatch` 在双人里静默消失）
    return _duo_join(parts, need=1)


def apply_appearance(state: dict, weights: dict = None) -> list:
    """按角色对齐发色/瞳色/体型/肤色/脸型。返回被改的字段。"""
    a = appearance_for(state.get("role") or "")
    if not a:
        return []
    weights = weights or empty_weights()
    changed = []
    pairs = (("hair", "hair_color"), ("eyes", "eyes_color"),
             ("figure", "figure"), ("skin", "skin"), ("face", "face_shape"))
    for src, dst in pairs:
        if not a.get(src):
            continue
        # 用户明确关掉的字段，官方设定也不覆盖
        if blocked_by_weight(weights, dst):
            continue
        state[dst] = a[src]
        changed.append(dst)
    return changed


# ============================================================================
# 六、输出槽位序（ANIMA3 v3.0 规范）
# ============================================================================
#
# 源脚本按**界面段序**输出，结果是「…场景 → 光影 → 服装状态 → 体位」——
# 服装状态排在了场景后面。ANIMA3 规范的顺序是：
#
#   count/gender → character/series → appearance → clothing/state
#   → pose/action/sex → expression/reaction → camera/shot
#   → scene/environment → detail/mood → 自然语言末尾
#
# 所以这里按**字段**做一次重排：把 NSFW 段的字段并回它语义所属的槽。
#
SLOTS = ("count", "character", "appearance", "clothing", "pose",
         "expression", "camera", "scene", "lighting", "detail", "free")

FIELD_SLOT = {
    # ① 数量与身份
    "count": "count",
    "role": "character",
    # ② 头发
    "hair_len": "appearance", "hair_color": "appearance", "hair_curl": "appearance",
    "hair_tie": "appearance", "hair_bangs": "appearance", "hair_fx": "appearance",
    "hair_acc": "appearance",
    # ③ 五官与表情
    "face_shape": "appearance", "eyes_color": "appearance",
    "temperament": "appearance",
    "emotion": "expression", "eye_gaze": "expression", "mouth": "expression",
    # ④ 身材与肤质
    "figure": "appearance", "skin": "appearance", "leg": "appearance",
    "skin_detail": "detail",
    # ⑤ 妆容
    "makeup": "detail", "nails": "detail",
    # ⑥ 服装（SFW）
    "outfit": "clothing", "cloth_mat": "clothing", "cloth_state_sfw": "clothing",
    "socks": "clothing", "shoes": "clothing", "accessory": "clothing",
    # v3.19.0 新增：服装颜色（独立颜色词条）→ 归 clothing 槽。
    # ⚠️ 它在 state 里只是"待合成"的中间值：draw 结束前会被 colorize 拼进
    #    具体单品并**删除自身**，所以正常情况下它不会以裸色 tag 出现在正文。
    #    登记进 FIELD_SLOT 是为了 collect/权重/面板三条链路都认得出它。
    "cloth_color": "clothing",
    # v3.20.0 新增：袜子专用色（只管 socks；cloth_color 只管下装/上装/整套）
    "sock_color": "clothing",
    # v3.12.0 新增：上装 / 下装 → 与 outfit 并列，同归 clothing 槽
    "top": "clothing", "bottom": "clothing",
    # ⑦ 姿势（SFW）
    "pose_base": "pose", "arms_hands": "pose", "action": "pose",
    # v3.14.0 新增：暗示姿势（L1–L2）→ 与 pose_base 并列，同归 pose 槽
    "pose_risque": "pose",
    # ⑧ 镜头
    "shot_size": "camera", "viewpoint": "camera", "dof": "camera", "device": "camera",
    # ⑨ 场景
    "place": "scene", "prop": "scene", "weather": "scene",
    "comp": "camera", "texture_fx": "detail",
    # ⑩ 光影
    "light_fx": "lighting",
    # ⑪ 服装状态（NSFW）→ 并回 clothing
    "n_state": "clothing", "n_exposure": "clothing", "n_body": "clothing",
    "n_fabric": "clothing", "n_shoes_keep": "clothing",
    # ⑫ 体位与性行为（NSFW）→ 并回 pose
    "n_act": "pose", "n_pos": "pose", "n_orgasm": "pose",
    "n_cum": "pose", "n_fluid": "pose",
    # ⑬ 特殊主题（NSFW）→ 道具类，归 detail
    "n_theme": "detail",
    # ⑭ 法典场景：n_canon 是个"填充器"，本身不产出标签；n_extra 是补充
    "n_canon": "free", "n_extra": "free",
    # ⑮ 自然语言补位
    "nl": "free",
}

# 超长裁剪顺序（源脚本 dropList 的忠实移植）。
# ⚠️ 判决时不是简单地按这个顺序削，而是「低优先级先削」，
#    优先级 = 段权重 × 字段权重 —— 用户把某字段权重调高，它就不容易被削掉。
DROP_ORDER = ("texture_fx", "comp", "prop", "weather", "hair_acc", "nails",
              "skin_detail", "leg")


def tag_cap_for(count_en: str):
    """按人数给标签数上限。数值全部来自源脚本 sanitizeRandom()。"""
    cnt = str(count_en or "")
    solo = "solo" in cnt and "multiple" not in cnt
    duo = bool(re.search(r"hetero|yuri|1boy", cnt))
    return 29 if solo else (37 if duo else 45)


def tag_window(count_en: str):
    """自检 F1 的合格区间（下界, 上界）。数值来自源脚本 checkTags()。

    与 tag_cap_for 的区别：cap 是「不许超过」，window 是「最好落在里面」。
    密度自适应瞄准的是 window —— 只瞄 cap 会一路往下掉，掉出下界照样红。
    """
    cnt = str(count_en or "")
    solo = "solo" in cnt and "multiple" not in cnt
    duo = bool(re.search(r"hetero|yuri|1boy", cnt))
    multi = bool(re.search(r"multiple|orgy|group sex", cnt))
    if solo:
        return 16, 30
    if duo:
        return 22, 38
    if multi:
        return 30, 48
    return 16, 42


# ============================================================================
# 六之五、特写「收紧取景」—— 引擎侧的**单一真源**（v6.7.0）
# ============================================================================
#
# 背景：这套「按部位只留看得见的字段」原本只写在 `anima_wardrobe_v4_studio.js`
# 里（`NEAR_VISIBLE` / `NEAR_DROP_SECTIONS` / `NEAR_DROP_ROLE` / `countBagFor`）。
# 面板手动套用没问题，但「勾选后每次运行自动随机」必须由引擎做 —— 而引擎看不懂
# JS 常量，于是同一套规则出现了第二个副本的风险。
#
# 处理：把表搬到 Python，**这里就是真源**；前端通过 `/contract` 读（找不到才回落
# 到自己那份常量）。两边口径必须一致，`tests/check_v3_pickpool.py` 有对拍。

NEAR_LOCAL_SECTIONS = ["hair", "face", "body", "makeup"]

NEAR_VISIBLE = {
    "面部": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs",
             "hair_fx", "hair_acc", "eyes_color", "emotion", "eye_gaze", "mouth",
             "skin", "skin_detail", "makeup", "accessory", "shot_size",
             "viewpoint", "dof", "place", "weather", "prop", "light_fx",
             "texture_fx"],
    "眼与唇": ["hair_bangs", "hair_fx", "eyes_color", "emotion", "eye_gaze",
               "mouth", "skin", "makeup", "shot_size", "dof", "place",
               "light_fx", "texture_fx"],
    "上半身与胸": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs",
                   "hair_fx", "hair_acc", "eyes_color", "emotion", "eye_gaze",
                   "mouth", "figure", "skin", "skin_detail", "makeup", "outfit",
                   "cloth_color", "cloth_mat", "cloth_state_sfw", "top",
                   "accessory", "arms_hands", "pose_base", "shot_size",
                   "viewpoint", "dof", "place", "prop", "weather", "comp",
                   "light_fx", "texture_fx"],
    # ⚠️ 四肢/臀不放 `dof`：候选里有 `focus on face`，与"只看局部"自相矛盾
    "手部": ["skin", "arms_hands", "pose_base", "shot_size", "viewpoint",
             "place", "prop", "weather", "light_fx"],
    "手部·有脸": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs",
                  "hair_fx", "hair_acc", "eyes_color", "emotion", "eye_gaze",
                  "mouth", "skin", "skin_detail", "makeup", "accessory",
                  "arms_hands", "pose_base", "shot_size", "viewpoint", "dof",
                  "place", "prop", "weather", "light_fx"],
    "🔞 成人向": ["skin", "skin_detail", "figure", "pose_base",
                  "shot_size", "viewpoint", "place", "prop", "weather", "light_fx"],
    "🔞 成人向·有脸": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs",
                       "hair_fx", "hair_acc", "eyes_color", "emotion", "eye_gaze",
                       "mouth", "skin", "skin_detail", "makeup", "accessory",
                       "figure", "pose_base", "shot_size", "viewpoint", "place",
                       "prop", "weather", "light_fx"],
    "腿部": ["skin", "cloth_color", "sock_color", "socks", "shoes", "pose_base",
             "shot_size", "viewpoint", "place", "prop", "weather", "light_fx"],
    "脚部": ["skin", "cloth_color", "sock_color", "socks", "shoes", "pose_base",
             "shot_size", "viewpoint", "place", "prop", "weather", "light_fx"],
    "臀部": ["skin", "cloth_color", "sock_color", "socks", "shoes", "pose_base",
             "shot_size", "viewpoint", "place", "prop", "weather", "light_fx"],
}

# 部位 → 要关掉哪几段（面部/眼与唇留 ②③ 才能出五官与表情）
NEAR_DROP_SECTIONS = {
    "面部": ["body", "makeup"], "眼与唇": ["body", "makeup"], "上半身与胸": [],
    "手部": NEAR_LOCAL_SECTIONS, "腿部": NEAR_LOCAL_SECTIONS,
    "脚部": NEAR_LOCAL_SECTIONS, "臀部": NEAR_LOCAL_SECTIONS,
    "🔞 成人向": NEAR_LOCAL_SECTIONS,
}

# 部位 → 要不要清角色（局部特写不该有脸）；标了 `role_ok` 的条目例外
NEAR_DROP_ROLE = {"手部": True, "腿部": True, "脚部": True, "臀部": True,
                  "🔞 成人向": True}


def all_field_keys() -> list:
    """全部字段 key（按段顺序）—— 精简档"清零"要按这张表算。"""
    if "all_field_keys" not in _CACHE:
        out = []
        for sec in sections():
            for f in (sec.get("fields") or []):
                k = str(f.get("key") or "")
                if k and k not in out:
                    out.append(k)
        _CACHE["all_field_keys"] = out
    return _CACHE["all_field_keys"]


def near_part_of(sc) -> str:
    """特写条目属于哪个部位（数据里叫 `theme`，payload 里叫 `part`）。"""
    return str((sc or {}).get("part") or (sc or {}).get("theme") or "").strip()


def near_slim_fields(sc, vis_key: str = "") -> list:
    """这条特写要**清零**哪些字段 = 全字段 −（可见白名单 ∪ 它自己写的 ∪ 特例）。"""
    keep = set(NEAR_VISIBLE.get(vis_key or near_part_of(sc), []) or [])
    for k in ((sc or {}).get("tags") or {}).keys():
        keep.add(k)
    keep |= {"n_canon", "n_extra", "count", "role"}
    # 条目点名闭麦的字段要从白名单里摘掉（口交特写连 `pose_base` 都不能留）
    for k in ((sc or {}).get("drop_fields") or ()):
        keep.discard(k)
    # `random_fields` 的字段由后端抽值，权重清零会让 `blocked_by_weight` 挡掉
    for k in ((sc or {}).get("random_fields") or {}).keys():
        keep.add(k)
    return [k for k in all_field_keys() if k not in keep]


def near_slim_profile(sc) -> dict:
    """特写条目 → 精简档；不是特写（或部位不认识）返回空 dict。"""
    if not sc:
        return {}
    part = near_part_of(sc)
    if part not in NEAR_VISIBLE:
        return {}
    if sc.get("role_ok"):
        face_key = (part + "·有脸") if (part + "·有脸") in NEAR_VISIBLE \
            else "🔞 成人向·有脸"
        return {"part": part, "sections": ["body", "makeup"],
                "fields": near_slim_fields(sc, face_key),
                "drop_role": False, "role_ok": True}
    return {"part": part, "sections": list(NEAR_DROP_SECTIONS.get(part) or []),
            "fields": near_slim_fields(sc),
            "drop_role": bool(NEAR_DROP_ROLE.get(part)), "role_ok": False}


def near_count_bag(count_lock: str = "") -> dict:
    """特写的人数袋：默认钉死单人，条目自带 `count_lock` 就按它钉。"""
    want = str(count_lock or "").strip() or "1girl, solo"
    fld = field_by_key("count")[1] or {}
    bag = {}
    for o in (fld.get("opts") or []):
        en = str(o[1])
        bag[en] = (WEIGHT_MAX if en == want else 0.0)
    return bag


def slim_weights_for_near(weights: dict, sc) -> dict:
    """把精简档写进一份**拷贝**的权重表（不动调用方传进来的那份）。

    与前端 `applySlimTo()` 同一套动作：关段 / 清字段 / 换人数袋。
    """
    prof = near_slim_profile(sc)
    if not prof:
        return weights
    w = {"sections": dict((weights or {}).get("sections") or {}),
         "fields": dict((weights or {}).get("fields") or {}),
         "options": {k: (dict(v) if isinstance(v, dict) else v)
                     for k, v in ((weights or {}).get("options") or {}).items()}}
    for k in prof["sections"]:
        w["sections"][k] = 0
    for k in prof["fields"]:
        w["fields"][k] = 0
    w["options"]["count"] = near_count_bag((sc or {}).get("count_lock"))
    for k, v in ((weights or {}).items()):
        if k not in w:
            w[k] = v          # 保留 `_ui` / `forced` 之类的旁路键
    return w


# ============================================================================
# 七、随机引擎
# ============================================================================


class DrawResult(dict):
    """普通 dict，只是给调用方一个稳定的字段名约定。"""


def draw(level="suggestive", seed=None, *, base_prob=DEFAULT_BASE_PROB,
         weights=None, enabled_sections=None, locks=None, tag_cap="自动",
         forced=None, force_role="", extra_body="", max_retry=8,
         appearance_follow=True, selected_roles=None, big_breasts=False,
         breast_size=None, role_works="", outfit_style="随机",
         role_fav_only=False, hair_follow=True, hair_picks=None,
         role_pick=True, scene_pick=False,
         dry_skin_filter=False, dry_skin_groups="", dry_skin_tags="",
         duo_role_fill=True, pick_pool_mode="", pick_pool_ids=""):
    """跑一次完整抽取。

    参数
    ----
    level         : 档位（中文标签 / 数字 / 旧英文 key 都认，见 resolve_level）
    seed          : None 或 <0 → 每次重抽；>=0 → 逐字节可复现
    base_prob     : 可选字段的基准抽取概率（源脚本写死 0.65）
    weights       : 三层权重 dict（见 parse_weights）
    enabled_sections : 参与随机的段；None = 全部（受档位闸门约束）
    locks         : 锁定字段集合 —— 锁定的字段不参与本次随机
    tag_cap       : "自动" 或 int
    forced        : {field_key: en} 强制指定，优先级最高
    force_role    : 指定单个角色 tag（会触发 APPEAR 外貌对齐）
    extra_body    : 手写补充，拼在末尾
    max_retry     : 抽出来的东西被判超档时最多重试几次
    appearance_follow : 抽到角色时是否用 APPEAR 表对齐发色/瞳色/体型
    selected_roles : **面板里点选的**角色 tag 列表 —— 抽取概率 100%，
                     并联动人数标签（1 位 / 双女 / 群像，见 headcount_tags）
    big_breasts   : v3.2.0 巨乳开关（v3.17.0 起为**兼容保留**：仅当
                    breast_size 不指定时生效，锁定 huge breasts）。
    breast_size   : v3.17.0 胸部大小等级（BREAST_SIZES 的中文键：「平胸 /
                    小胸 / 中等 / 大胸 / 巨乳 / 爆乳」）。None/空/"不指定"
                    = 不干预（figure 参与随机，可被角色对齐覆盖）；
                    指定 = 锁定 figure，最高优先级。
    role_works    : **按作品 IP 限定随机角色**（v3.10.0）。作品 slug / 英文名 /
                     中文名，可多个。只在「没点选角色、也没手填角色」时生效 ——
                     于是每次运行都从这些 IP 里重新抽（配合 seed=-1 即"每次换人"）
    role_fav_only : **只在收藏角色里随机**（v3.38.0）。开启后随机角色的候选池
                     收缩到 `user_favorites.json` 里的角色。与 role_works 同时
                     给出时取**交集**（该作品 ∩ 我的收藏）；交集为空会逐级退化
                     并把结果写进报告，不会静默变成"没有角色"。
    outfit_style  : **服装风格智配**（v3.19.0）。取值见
                     anima_v4_outfit_style.STYLE_CHOICES：
                     「随机」= 智配开、锚点自动（整套 > 上装 > 下装）；
                     「不限」= 智配整体关闭（回到 v3.18 行为）；
                     具体风格（学院/职场/传统/运动/日常/居家/夜场/
                     奇幻/泳装）= 强制向该风格收敛。
                     只影响服装六类字段，不碰发型/五官/姿势/场景。
    dry_skin_filter : v6.5.0「皮肤干爽过滤」总开关。False（默认）= 完全不介入，
                      零漂移；True = 在成品 tag 列表层剔除致水珠词条。
    dry_skin_groups : v6.5.0 要关闭的分组 id（逗号分隔）；空 / all = 全部组。
                      见 SKIN_MOISTURE_GROUPS / strip_skin_moisture()。
    dry_skin_tags : v6.5.2 **逐条模式**要关闭的英文 tag（逗号分隔），
                      "all" = 全部、"none" = 一条都不关；
                      空串 = 回落 dry_skin_groups 组语义（零回归）。
    role_pick     : **这一抽要不要角色**（v4.1.0，面板「角色库」页签的
                     「🚫 不指定角色」）。False = 一个角色都不抽 ——
                     `force_role` 与 `selected_roles` 也一并让路（两者都不销毁，
                     取消勾选立刻恢复）；发色 / 瞳色 / 体型 / 发型全部按词库权重
                     随机，就是"没有 IP 的纯词库人物"。True = 旧行为（零漂移）。
    duo_role_fill : v6.6.0「双女自动补位」总开关（默认 True）。
                     只影响「**只选了 1 位角色** × 双女/多女人数」这一种组合：
                     True = 自动补一位**同作品**的女性（该 IP 只有她一位时回退
                     全库随机），避免模型把同一个人画两遍；
                     False = 完全不介入，**零 rng 消耗**（与 v6.5.2 逐字节一致）。
    pick_pool_mode / pick_pool_ids : v6.7.0「勾选池每次自动随机」。
                     mode = "" 关闭 / "pose" 姿势库 / "art" 艺术构图 /
                     "near" 特写搭配（写法只为报告与面板对齐，引擎只认 ids）；
                     ids = 逗号分隔的场景 id（面板勾选集合）。
                     打开时**每次 draw 从 ids 里随机抽一条**当成本轮场景，
                     等价于"面板手动套用那一条"：姿势/艺术只锁构图，
                     特写连**收紧取景**（关段 / 清字段 / 人数袋 / 清角色）一起做。
                     ⚠️ 用户 `forced["n_canon"]` 优先，勾选池不会覆盖它；
                        `scene_pick` 也随之让路（forced 天然压过它）。
                     ⚠️ seed >= 0 时抽哪条是确定的（可复现），seed < 0 才每次换。

    ⚠️ 角色优先级（自上而下，先命中者胜）：
       ⓿ role_pick=False（**模式开关，压过一切** —— 勾上就是"这批不要角色"，
          留个例外会让它变成"有时候不抽"，用户永远猜不到）
       ① force_role（手填一个）  ② selected_roles（面板点选 = 固定）
       ③ role_works / role_fav_only（按 IP、按收藏每次随机）  ④ 全库随机
       ①②是"用户显式指定"，写进 role 字段；③④是"自动随机"，不改写人数标签。
       ③里 role_fav_only 是**进一步收窄**，不是并列的另一档。

    ⚠️ selected_roles 与 force_role 的区别：
       force_role 是"指定一个角色"，selected_roles 是"角色浏览器里勾选的
       一或多位角色"。两者都写 role 字段，selected_roles 优先。
       多位角色时**不做 APPEAR 外貌对齐** —— 两个人各有各的官方发色，
       强行对齐其中一个是错的（角色的 danbooru tag 本身已经带足了特征）。

    返回 dict：text / state / level / level_num / report / dropped / notes
    """
    weights = weights or empty_weights()
    lv_key = resolve_level(level)
    lv = LEVEL_NUM[lv_key]
    locks = set(locks or ())
    forced = dict(forced or {})
    notes = []

    # ── v6.7.0：勾选池「每次运行自动随机」─────────────────────────────
    # 面板上勾了若干条（姿势库 / 艺术构图 / 特写搭配）并打开总开关后，
    # **每一次 draw** 从这里换一条 —— 等价于"手动套用那一条"，不再需要点按钮。
    # 必须在所有场景逻辑之前落成 `forced["n_canon"]`：`forced` 是最高优先，
    # 于是 `scene_pick`（整库随机）天然让路，与用户拍板的"勾选池优先"一致。
    # ⚠️ 用户自己给了 `forced["n_canon"]` 时**不动**（显式 > 面板勾选池）。
    # ⚠️ seed >= 0 用独立 rng（同 seed 同一条，可复现）；seed < 0 每次换。
    #    用独立 `random.Random` 而不是共享 `rng`：不改动主序列的抽取轨迹。
    if pick_pool_mode and str(pick_pool_ids or "").strip() \
            and "n_canon" not in forced:
        _ids = [x.strip() for x in str(pick_pool_ids).split(",") if x.strip()]
        _valid = [i for i in _ids if i in scene_index()]
        if _valid:
            try:
                _prng = random.Random(int(seed)) if (seed is not None
                                                     and int(seed) >= 0) \
                    else random.Random()
            except (TypeError, ValueError):
                _prng = random.Random()
            _picked = _valid[_prng.randrange(len(_valid))]
            forced["n_canon"] = _picked
            _picked_zh = (scene_index().get(_picked) or {}).get("zh") or _picked
            _lab = {"pose": "姿势库", "art": "艺术构图", "near": "特写搭配"}.get(
                str(pick_pool_mode), str(pick_pool_mode))
            notes.append("勾选池随机（%s %d 条）→ 本次抽到「%s」"
                         % (_lab, len(_valid), _picked_zh))
            # 特写：连**收紧取景**一起做（关段 / 清字段 / 人数袋 / 清角色）。
            # 与面板手动套用同一套规则（真源在 near_slim_profile）。
            _sc_pick = scene_index().get(_picked) or {}
            # 档位对齐：面板套用场景时会 `syncLevelFor`（只升不降），引擎这条
            # 路也要一致，否则勾选池里放了 L3 特写、档位停在 sfw 时会被闸门
            # 静默拦掉（表现为"抽到了却没套上"）。
            _need_lv = int(_sc_pick.get("level") or 0)
            if _need_lv > lv:
                _oldk = lv_key
                lv = _need_lv
                lv_key = level_key_for_num(lv) or _oldk
                notes.append("勾选池：该场景要求 L%d → 档位已从 %s 升到 %s（只升不降）"
                             % (lv, LEVEL_ZH.get(_oldk, _oldk),
                                LEVEL_ZH.get(lv_key, lv_key)))
            _prof = near_slim_profile(_sc_pick)
            if _prof:
                weights = slim_weights_for_near(weights, _sc_pick)
                notes.append("特写收紧取景：关 %d 段 / 清 %d 个字段 / 人数钉死「%s」"
                             % (len(_prof["sections"]), len(_prof["fields"]),
                                str(_sc_pick.get("count_lock") or "1girl, solo")))
                if _prof["drop_role"]:
                    selected_roles = []
                    force_role = ""
                    locks.add("role")
                    notes.append("特写收紧取景：画框里没有脸 → 已清空角色并锁住 role")
        elif str(pick_pool_ids or "").strip():
            notes.append("⚠️ 勾选池里的 id 全都不在场景库（可能删过）→ 本次忽略勾选池")

    # ── v5.0.1：同类型互斥的「必现」去重 ──────────────────────────────
    # 必须在 `forced_pose_weight_fields()` 之前做：降级会影响"点名了哪些字段"，
    # 顺序反了就会出现「n_act 已经被降为 1.0，却仍被当成点名 → 场景被 block」。
    notes.extend(enforce_option_mutex(weights))

    # ── v4.3.0：点名了越档词 → 档位跟着词条自动上调（用户口径 22:12）──
    # 「不放行」= 档位闸门不拆；「自动调整档位」= 整张图改按词条的档位抽。
    # 只认 POSE_WEIGHT_FIELDS（姿势 4 + 体位 5 + 主题 1）—— 用户口径限这三组，
    # 发色/服装的 3.0 不会把一张 SFW 图抬成 extreme。
    # 只升不降：点了 L3 词而档位已经是 extreme 时，保持 extreme。
    _need_lv = forced_pose_level(weights)
    if _need_lv > lv:
        _old_lv = lv
        _old_key = lv_key
        lv = _need_lv
        lv_key = level_key_for_num(lv) or _old_key
        notes.append(
            "档位按点名词条自动上调：L%d %s → L%d %s"
            "（词条档位高于所选档位，本张整体按新档抽取）"
            % (_old_lv, LEVEL_ZH.get(_old_key, _old_key),
               lv, LEVEL_ZH.get(lv_key, lv_key)))

    # ── v4.2.1：点名了性内容 → 自动场景让路 ──────────────────────────
    # 用户口径："未使用场景搭配时，权重必须生效（含 L3~L4 的性行为）"。
    # 问题在于"没用场景搭配"≠"没有场景"：引擎会按档位**自动**抽一条场景，
    # 而自动场景的 `count_lock`（人数·1girl, solo）与 `drop_fields`
    # （闭麦·n_act / n_pos）会把点名项清掉 —— 实测 seed 2018 就是这么丢的。
    # 规则：四个性内容字段（n_act / n_pos / n_cum / n_theme）任一点名 3.0，
    # 且用户**没有**自己点名场景 → 本次不自动套场景（要场景请显式套用）。
    # v4.3.0：扩到 POSE_WEIGHT_FIELDS 全部 10 个（用户口径：再加特殊主题）——
    # 实测 missionary 设 0.5/1.0/2.0 分布完全一致，就是自动场景写死 n_act 造成的。
    # 口径仍是"只有 3.0 才触发"（用户 22:05 拍板），中间值不排除场景。
    _forced_pose_fields = forced_pose_weight_fields(weights)
    _block_auto_scene = (bool(_forced_pose_fields)
                         and not user_pinned_scene(weights, forced, locks))

    # ── 按作品 IP 限定随机角色（v3.10.0）──────────────────────────────
    # ⚠️ 解析放在最前面：它是**输入校验** —— 认不出来的写法要立刻进报告，
    #    而不是等到角色段才发现"池子是空的"，那时用户根本不知道为什么。
    works_filter = resolve_works(role_works)
    if role_works and not works_filter:
        notes.append(f"⚠️ 作品串没认出来：`{role_works}`"
                     "（可用作品 slug / 英文名 / 中文名，多个用 | 分隔）")
    elif role_works:
        bad_works = unknown_works(role_works)
        if bad_works:
            notes.append("⚠️ 认不出的作品名（已忽略）："
                         + "、".join(f"`{b}`" for b in bad_works))

    # base_prob 兜底：0 / 负数 / 非数字一律当"没设置"，用默认值。
    # ⚠️ 为什么要兜：v3.1.0 的「采用这条」在那一栏为空时会写 Number("") === 0，
    #    而 base_prob = 0 意味着**所有可选字段都不抽**，出图只剩必填项 ——
    #    用户看到的就是"随机坏了"。这里把历史脏数据救回来。
    #    （想在界面上"少抽可选字段"应该调低数值而不是调到 0，0 没有可用语义）
    if not isinstance(base_prob, (int, float)) or base_prob <= 0:
        base_prob = DEFAULT_BASE_PROB

    if seed is None or int(seed) < 0:
        rng = random.Random()
        seeded = False
    else:
        rng = random.Random(int(seed))
        seeded = True

    enabled = set(enabled_sections) if enabled_sections is not None \
        else set(section_ids())

    if "count" not in enabled:
        notes.append("①数量与身份段已关闭 —— 正文不含人数标签，请确认上游是否补齐")

    # 权重表里引用了不存在的 key → 不阻断，但要说出来
    bad_w = validate_weights(weights)
    if bad_w:
        notes.extend(bad_w[:6])

    state = {}
    drawn = []
    cap = None

    def _one_pass(pass_rng, eff_p, variant_pick=None, variant_seed=None,
                  force_map=None):
        """抽一遍。返回 (state, drawn, notes)。

        `force_map` 给了就**代替**闭包里的 forced —— 密度重试时用它把
        `count` 钉在第一轮抽到的值上（理由见 draw 里 `_fixed_count` 的注释）。
        """
        # ── v4.4.6：点名了正戏动作 / 主题 → 闭麦「动态动作」「情绪」────
        #
        # 为什么不是「给 action 也补一句自然语言」：那会**更糟**。
        #   上一轮验证 51 条复杂词条时，8 条完全无效里有 3 条就是被 `action`
        #   抽到的日常词抢走了画面 —— `irrumatio` 画成「水下游泳」、
        #   `hypnотиs` 画成「蹲着」、`gag ball` 画成「打哈欠」。
        #   日常动词（walking / eating / swimming …）在 CLIP 里的权重远高于
        #   `irrumatio` 这类长尾动作短语：tag 堆打不过它，补一句话更打不过。
        #
        # 所以方向反过来了 —— 不是给它加码，而是**点名正戏时不让它进来抢**。
        #
        # ⚠️ 只在「n_act / n_theme 里真的有选项被点名 3.0」时生效；
        #    全默认权重下集合为空，抽取路径与改动前逐字节一致
        #    （零漂移由 check_v3_near 对拍钉住，与预抽同一个道理）。
        _skip_fields = ({"action", "emotion"}
                        if has_forced_sex_content(weights) else set())

        F = forced if force_map is None else force_map
        st, dr, nt = {}, [], []
        pre = {}          # 预抽结果（v3.15.0）：字段 key → (zh, en, num)
        # v4.3.0：被调过权重的字段（≠1.0 且 >0）—— 这些字段一律必抽。
        # 全默认时是空集合，抽取路径与 v4.2.3 逐字节一致。
        _tuned = tuned_weight_fields(weights)

        def _pre_place():
            """场景预抽：把 `place` 从场景段里**提前**抽出来。

            为什么必须提前：`place` 在 ⑨ 场景段，排在 ⑥ 服装 / ⑦ 姿势
            **之后**，而「服装 × 场景」「场景 × 姿势」两条联动都要求它先定下来。
            不预抽的话这两条联动永远读到空的 place（表现：联动完全不生效，
            而且**不报任何错**）。

            与正式循环里的抽取**完全同构**（段权重 → 字段权重 → 概率 → 选项权重），
            只是换了执行时机。抽不中就不抽 —— 联动自然退化成"无场景"，
            与 v3.14.0 的行为逐字一致（新增能力默认不干预）。
            """
            key = "place"
            hit = None
            for sec in sections():
                for f in sec["fields"]:
                    if f["key"] == key:
                        hit = (sec["id"], f)
                        break
            if not hit:
                return
            sec_id, fld = hit
            if sec_id not in enabled or key in locks:
                return
            if key in F:
                en = F[key]
                zh = next((z for z, e in (fld.get("opts") or []) if e == en), en)
                pre[key] = (zh, en, option_level(key, en))
                # ⚠️ **立刻**写进 state：服装/姿势段的抽取就在后面，
                #    联动读的就是 st["place"]。只存进 pre 等到主循环再写，
                #    那时服装早就抽完了 —— 联动会静默失效（实测踩到）。
                st[key] = en
                return
            sw = section_weight(weights, sec_id)
            fw = field_weight(weights, key)
            if sw <= 0.0 or fw <= 0.0:
                return
            pool = eligible_options(key, lv)
            if not pool:
                return
            # ⚠️ v4.3.0：预抽也必须走「选项权重 3.0 = 必现」的同一条口径。
            #    以前这里直接 `weighted_pick(pool)`，主循环看到 `key in pre`
            #    就不再重抽 —— 于是 place / outfit / top / bottom /
            #    arms_hands / pose_risque 六个字段的 3.0 全部作废
            #    （实测 place 8/15、outfit 6/15、arms_hands 6/15）。
            _forced = ([] if (role_pick and key in CHAR_APPEAR_FIELDS)
                       else forced_option_pool(pool, key, weights))
            if not (fld.get("req") or fw >= WEIGHT_MAX
                    or _forced or key in _tuned):
                if not pass_rng.random() < clamp_prob(float(eff_p) * sw * fw):
                    return
            pick = weighted_pick(
                pass_rng, _forced or pool,
                # v3.38.6：场景预抽也要吃风格偏置 —— `place` 走的是这条预抽路径
                # （不是主循环），漏了它就会出现"选了暗黑少女却抽到白天城堡"。
                lambda it: (option_weight(weights, key, it[1]) * sw * fw
                            * OS.field_option_bias(key, outfit_style, it[1])))
            if pick is None:
                return
            pre[key] = pick
            st[key] = pick[1]
            # ⚠️ 必现项要登记 —— `sanitize` / `trim_to_cap` / 场景 `drop_fields`
            #    的豁免全靠这个键。漏了就会出现"抽到了又被删"（实测 seed 1002：
            #    arms_hands 抽中后被场景 `闭麦·arms_hands` 清掉）。
            if _forced:
                st.setdefault("_forced_tags", []).append((key, pick[0], pick[1]))

        # 预抽专用随机源：`pass_rng` 的状态副本。
        # ⚠️ 不消耗主随机流 —— 否则新增的预抽会改动整条随机序列，把与本功能
        #    无关的基线全部撞歪（实测 400 轮稳定复现「图书馆坐姿 22% vs
        #    卧室 12%」这种失败）。服装与手部两段预抽**共用这一个副本**：
        #    各自复制同一状态会让两段的首个 roll 完全相关。
        pre_rng = random.Random()
        pre_rng.setstate(pass_rng.getstate())

        def _pre_outfit():
            """服装定调预抽（v3.19.0）：把 outfit / top / bottom **提前**抽出来。

            为什么必须提前：⑥ 服装段里 socks / shoes / accessory 排在
            top / bottom 之前，而「风格智配」要求先有定调单品 ——
            不预抽的话袜子鞋子永远拿不到锚点，智配等于没做。

            顺序即依赖：outfit（整套）→ top（上装）→ bottom（下装），
            后抽的能看到前面已定的锚点，于是这三件自己就先自洽了。
            与 _pre_place 完全同构：抽不中就跳过，智配自然退化成"不干预"。
            """
            for key in ("outfit", "top", "bottom"):
                if key in st or key in locks:
                    continue
                hit = None
                for sec in sections():
                    for f in sec["fields"]:
                        if f["key"] == key:
                            hit = (sec["id"], f)
                            break
                    if hit:
                        break
                if not hit:
                    continue
                sec_id, fld = hit
                if sec_id not in enabled:
                    continue
                if key in F:
                    en = F[key]
                    zh = next((z for z, e in (fld.get("opts") or []) if e == en), en)
                    pre[key] = (zh, en, option_level(key, en))
                    st[key] = en
                    continue
                sw = section_weight(weights, sec_id)
                fw = field_weight(weights, key)
                if sw <= 0.0 or fw <= 0.0:
                    continue
                pool = eligible_options(key, lv)
                # v4.3.0：3.0 必现（与主循环同口径）—— 显式指令 > 风格收敛，
                # 所以有必现项时不再做风格收窄/剔除，直接在那几项里抽。
                _forced = ([] if (role_pick and key in CHAR_APPEAR_FIELDS)
                           else forced_option_pool(pool, key, weights))
                if not _forced:
                    if key == "outfit":
                        # 控件指定了具体风格 → 整套硬收敛（见 OS.allowed_outfit）
                        pool = OS.allowed_outfit(pool, outfit_style)
                    # 风格铁律（硬剔除）—— fail-open：剔光就保留原池
                    sbans = OS.style_bans(st, key, outfit_style)
                    if sbans:
                        kept = [it for it in pool if it[1] not in sbans]
                        if kept:
                            pool = kept
                if not pool:
                    continue
                _lb = OS.layer_bias(st, key, outfit_style)
                if not (fld.get("req") or fw >= WEIGHT_MAX
                        or _forced or key in _tuned):
                    # 层级抑制必须乘在这里：它决定「这个字段抽不抽」，不乘选项权重没用
                    if not pre_rng.random() < clamp_prob(float(eff_p) * sw * fw * _lb):
                        continue
                pick = weighted_pick(
                    pre_rng, _forced or pool,
                    lambda it: (option_weight(weights, key, it[1]) * sw * fw
                                * OS.style_bias(st, key, it[1], outfit_style)))
                if pick is None:
                    continue
                pre[key] = pick
                st[key] = pick[1]
                if _forced:
                    st.setdefault("_forced_tags", []).append((key, pick[0], pick[1]))

        def _pre_hands():
            """上肢定序预抽（v3.39.0）：先定手，再让姿势退让。

            为什么必须预抽：`pose_sfw` 段里的**书写顺序**是
            `pose_base → arms_hands → pose_risque`，而保留优先级恰好反过来
            （arms_hands > pose_risque > pose_base）。不预抽的话 pose_base 先抽、
            先占住手部槽位，等 arms_hands 想剔它已经晚了 —— 只能事后清掉
            （随机数白花，"抽了又被删"，报告与成品对不上）。

            按 `HAND_ORDER` 先跑完两个高优先级字段并写进 st，pose_base 在自己的
            位置上就能从 `L.bans_for()` 看到"手已经被占用"，从而剔掉自己的
            三个手部项候选（`arms at sides` / `hands on own knees` / `head rest`）。

            ⚠️ pose_base 刻意**不**在这里抽：它还要读上面两个字段的结果，
               留在主循环原位才能吃到联动；而且它的候选过滤是"剔除"，
               放在主循环里与其它字段走完全同一条路径，便于对拍。
            ⚠️ rng 用 pre_rng（pass_rng 的状态副本），不消耗主随机流。
            """
            for key in ("arms_hands", "pose_risque"):
                if key in st or key in locks:
                    continue
                hit = None
                for sec in sections():
                    for f in sec["fields"]:
                        if f["key"] == key:
                            hit = (sec["id"], f)
                            break
                    if hit:
                        break
                if not hit:
                    continue
                sec_id, fld = hit
                if sec_id not in enabled:
                    continue
                if key in F:
                    en = F[key]
                    zh = next((z for z, e in (fld.get("opts") or []) if e == en), en)
                    pre[key] = (zh, en, option_level(key, en))
                    st[key] = en
                    continue
                sw = section_weight(weights, sec_id)
                fw = field_weight(weights, key)
                if sw <= 0.0 or fw <= 0.0:
                    continue
                pool = eligible_options(key, lv)
                if not pool:
                    continue
                # v4.3.0：3.0 必现（与主循环同口径）。显式指令 > 联动 ——
                # 有必现项时不再按"手已被占用"剔候选，抽完由 sanitize 让另一侧退
                # （`_forced_tags` 进 protected，见 sanitize 的上肢单槽段）。
                _forced = ([] if (role_pick and key in CHAR_APPEAR_FIELDS)
                           else forced_option_pool(pool, key, weights))
                # 上游字段已占手 → 剔掉自己这边的手部项（fail-open：剔光回原池）
                bans = set() if _forced else (
                    L.bans_for(st, key) | OS.style_bans(st, key, outfit_style))
                if bans:
                    kept = [it for it in pool if it[1] not in bans]
                    if kept and len(kept) != len(pool):
                        nt.append("上肢单槽：%s 剔除 %d 项（手已被占用）"
                                  % (fld.get("lab") or key, len(pool) - len(kept)))
                        pool = kept
                if fld.get("req") or fw >= WEIGHT_MAX or _forced or key in _tuned:
                    take = True
                else:
                    take = pre_rng.random() < clamp_prob(
                        float(eff_p) * sw * fw
                        * OS.layer_bias(st, key, outfit_style))
                if not take:
                    continue
                pick = weighted_pick(
                    pre_rng, _forced or pool,
                    lambda it: (option_weight(weights, key, it[1]) * sw * fw
                                * L.bias_for(st, key, it[1])
                                * OS.style_bias(st, key, it[1], outfit_style)
                                * OS.field_option_bias(key, outfit_style, it[1])))
                if pick is None:
                    continue
                pre[key] = pick
                st[key] = pick[1]
                if _forced:
                    st.setdefault("_forced_tags", []).append((key, pick[0], pick[1]))

        _pre_place()

        # ⚠️ 顺序不可换：place 先定，服装才能吃到「场景 × 服装」联动；
        #    服装定调再喂给风格智配。反过来会让场景联动静默失效。
        _pre_outfit()

        # v3.39.0：手部动作在姿势段之前定序（优先级 arms_hands > pose_risque），
        # 这样 pose_base 轮到自己的时候已经知道手被谁占着。与 place / outfit
        # 两段预抽无依赖关系，放在它们之后只为共用 pre_rng 的消耗顺序稳定。
        _pre_hands()

        for sec in sections():
            sid = sec["id"]
            if sid not in enabled:
                continue
            sw = section_weight(weights, sid)
            if sw <= 0.0:
                continue

            for fld in sec["fields"]:
                key = fld["key"]
                # ⚠️ 角色字段**不参与常规随机**（v3.2.0 起）。
                #    它的选择已由「角色库」页签接管 —— 见下面「角色：统一入口」。
                #    留在这里的话，权重与角色库选择会互相打架：
                #    用户点了角色，却被 weights.fields.role = 0 静默否决。
                if key == "role":
                    continue
                # 锁定 / 强制指定的字段：由调用方/上游给值，跳过随机
                if key in locks:
                    continue
                # v4.4.6：已点名正戏动作 / 主题 → 不随机抽「动态动作」「情绪」。
                #   理由见 `_skip_fields` 的注释：它们是画面权重的强竞争者。
                if key in _skip_fields:
                    if not st.get("_action_muted"):
                        st["_action_muted"] = True
                        nt.append(
                            "动作优先：已点名性行为/主题 → 本次不随机抽「动态动作」"
                            "与「情绪」（日常动作词会压掉点名的动作）")
                    continue
                # 场景已在预抽阶段定值（v3.15.0）—— 含 forced 的情况。
                # ⚠️ 必须排在 forced 分支**之前**：forced 的 place 在预抽时
                #    就已经写进 state 了，这里再走 forced 分支会直接 continue，
                #    place 的抽取记录就丢了（报告里看不到它）。
                if key in pre:
                    _zh, _en, _num = pre[key]
                    st[key] = _en
                    dr.append((key, _zh, _en, _num,
                               option_weight(weights, key, _en)))
                    continue
                if key in F:
                    st[key] = F[key]
                    continue
                # 文本字段（手写）：不随机，留给调用方
                if fld.get("type") == "text" or not fld.get("opts"):
                    continue

                fw = field_weight(weights, key)
                if fw <= 0.0:
                    continue

                pool = eligible_options(key, lv)
                # ── v4.2.1：性行为被点名 → 人数必须先能承载它 ────────────
                # 必须排在下面的"人数相容"过滤**之前**，否则 solo 会把
                # 27 个需要伴侣的动作一次剔光，用户的 3.0 连候选都没有。
                # 只做一次（第一次碰到性内容字段时）。
                if key in SEX_CONTENT_FIELDS and "_sex_count_fix" not in st:
                    st["_sex_count_fix"] = True
                    _target = forced_sex_count_target(weights)
                    _c0 = str(st.get("count") or "")
                    _count_forced = any(
                        t[0] == "count"
                        for t in (st.get("_forced_tags") or ()))
                    _cur_ok = (_count_forced
                               or count_satisfies(_c0, _target))
                    if _target and not _cur_ok:
                        if _count_forced:
                            # 用户把人数本身也点到 3.0 → 人数优先，别自作主张
                            nt.append(
                                "⚠️ 性行为已点名必现，但人数被点名成 `%s` —— "
                                "两者矛盾，本次按人数走（性行为可能立不起来）" % _c0)
                        else:
                            st["count"] = _target
                            nt.append(
                                "性行为已点名必现 → 人数由 `%s` 抬到 `%s`"
                                % (_c0 or "(空)", _target))
                # v3.41.2：solo 场景不抽"需要伴侣"的动作。
                # 实拍证据：放开多人选项后，50 张里有 7 张出现 `1girl, solo` +
                # `blowjob` / `penetration` / `deepthroat` —— 画面上只能画成
                # "低头张嘴"，档位标签对了但内容立不起来。
                # ⚠️ 只在人数**已经定死**时做：selected_roles / force_role 会在
                #    循环之后改写人数，那时按 solo 过滤就会误伤双人场景。
                if key in PARTNER_GATED_FIELDS \
                        and not (selected_roles or force_role) \
                        and not count_has_partner(st.get("count") or ""):
                    kept = [it for it in pool
                            if str(it[1]).strip().lower() in SOLO_OK_PARTNER_OPTS]
                    if len(kept) != len(pool):
                        nt.append("人数相容：%s 剔除 %d 项需要伴侣的选项（当前 solo）"
                                  % (fld.get("lab") or key, len(pool) - len(kept)))
                        pool = kept
                    if not pool:
                        continue
                # ── v4.5.43：穿戴式 / 扶她只在「双女性交」里抽 ────────────────
                # 用户 2026-10-31：「穿戴式词条增加抽取规则，只在双女性交时能够
                # 抽取出来让一名女性佩戴」。它们需要「一人佩戴 + 一人承受」，
                # 单人只能画出"扶着腰凭空戴道具"，一男一女更是自相矛盾，
                # 群交里也会把画面带成多出来的道具。
                # ⚠️ 只剔 `strap-on` / `futanari`，**不动** `sex toys` / `dildo`：
                #    自慰与玩具展示场景本来就是 solo（sc_msb_toys_show 等）。
                # ⚠️ 用户**显式点名**（权重 3.0）时让路 —— 那是明确指令，
                #    `check_v3_weights` 的「点名的 strap-on 仍保留」就是守这条：
                #    人数与主题都由用户拍板时，按人数走、主题保留并在报告里说明矛盾。
                _toy_named = any(
                    option_weight(weights, key, str(it[1])) >= WEIGHT_MAX
                    for it in pool
                    if str(it[1]).strip().lower() in YURI_TOY_TAGS)
                if key == "n_theme" and not _toy_named \
                        and not is_yuri_count(st.get("count") or ""):
                    kept = [it for it in pool
                            if str(it[1]).strip().lower() not in YURI_TOY_TAGS]
                    if len(kept) != len(pool):
                        nt.append("穿戴式只在双女里抽取：%s 剔除 %d 项（当前 %s）"
                                  % (fld.get("lab") or key, len(pool) - len(kept),
                                     st.get("count") or "(空)"))
                        pool = kept
                    if not pool:
                        continue
                if key == "outfit":
                    # 主循环这条也要收紧：预抽没抽中时这里会重抽整套，
                    # 漏了就会绕过风格硬收敛（实测抽到「泳装 + 护士服」）。
                    pool = OS.allowed_outfit(pool, outfit_style)
                # ⚠️ v3.18.0：manual_only 的预设（特写搭配）只有**被显式套用**
                #    （选项权重 ≥ 3.0）才进候选。默认权重下这段是恒等变换，
                #    随机路径与 v3.17.2 逐字节一致 —— 见 tests/check_v3_near.py。
                if key == "n_canon":
                    if _block_auto_scene:
                        # v4.2.1：点名了姿势/体位 → 不自动套场景（每轮只说一次）
                        if not st.get("_auto_scene_skipped"):
                            st["_auto_scene_skipped"] = True
                            _labs = []
                            for _k in _forced_pose_fields:
                                _s, _f = field_by_key(_k)
                                if _f:
                                    _labs.append(_f.get("lab") or _k)
                            nt.append(
                                "已点名 %s（权重 3.0）→ 本次不自动套场景"
                                "（自动场景的人数/闭麦/写死值会覆盖点名项；"
                                "要场景请在面板显式套用）" % "、".join(_labs))
                        continue
                    pool = _canon_pool_for_draw(pool, lv, weights)
                    # v3.40.0：按档位分层（只在随机路径生效；显式套用 / lv=0 不干预）
                    pool, _lvn = scene_level_layered(pool, lv, weights, _scene_coin)
                    if _lvn:
                        nt.append(_lvn)
                if not pool:
                    continue

                # ── 联动① 剔除矛盾项（v3.15.0）─────────────────────────
                # 体位配对（n_act × n_pos）、表情配套（emotion × eye_gaze/mouth）、
                # 场景违和（place × 服装）。表在 anima_v4_link.py。
                # ⚠️ **fail-open**：全被剔光时保留原池 —— 宁可留一组矛盾，
                #    也不要让"这个字段整段消失"（用户会以为随机坏了）。
                bans = L.bans_for(st, key) | OS.style_bans(st, key, outfit_style)
                # ── v4.2.1：点名了体位 → 动作必须与它相容 ────────────────
                # 联动是**单向**的（n_act → n_pos）：n_act 先抽，之后才轮到
                # n_pos。不这么做的话，先随机到的动作会把用户点名的体位
                # 判成矛盾剔掉 —— 又是"点了 3.0 却没生效"。
                # 只在 n_pos 恰好点名**一个**选项时生效（点了两个以上 =
                # "这几个都行"，没法唯一定向）。
                if key == "n_act":
                    _fp = [it[1] for it in forced_option_pool(
                        (field_by_key("n_pos")[1].get("opts") or []),
                        "n_pos", weights)]
                    if len(_fp) == 1:
                        # 正向：点名的体位会禁掉哪些动作（可能为空）
                        bans = bans | L.bans_for({**st, "n_pos": _fp[0]}, key)
                        # 反向：哪些动作会**禁掉**点名的体位 —— 联动表是
                        # 单向的（n_act → n_pos），必须手工求逆，否则先抽到的
                        # 动作（cowgirl / full nelson / masturbation …）会把
                        # 用户点名的体位判成矛盾剔掉。
                        bans = bans | {it[1] for it in pool if _fp[0] in
                                       L.bans_for({**st, "n_act": it[1]}, "n_pos")}
                if bans:
                    # v4.2.1：被联动判矛盾而丢掉的"点名单词"要单独说一句 ——
                    # 否则又是"我明明点了 3.0 却没出现"（例：点名传教士、
                    # 同时随机到不相容的体位，联动会把一侧剔掉）。
                    _lost_forced = [it[1] for it in pool
                                    if it[1] in bans
                                    and option_weight(weights, key, it[1])
                                    >= WEIGHT_MAX]
                    kept = [it for it in pool if it[1] not in bans]
                    if kept and len(kept) != len(pool):
                        nt.append("联动：%s 剔除 %d 项矛盾的候选"
                                  % (fld.get("lab") or key, len(pool) - len(kept)))
                        if _lost_forced:
                            nt.append("⚠️ 「%s」点名的 %s 与当前状态矛盾，本次未生效"
                                      % (fld.get("lab") or key,
                                         "、".join(_lost_forced)))
                        pool = kept

                # ── 选项级「必现」（v4.1.1）─────────────────────────────
                # 池子里有选项被点到 3.0 → 这个**选项**必须出现。
                # ⚠️ 必须在档位闸门 + 联动剔除**之后**取：
                #    被档位挡掉（SFW 抽不到 L2 词）或被联动判为矛盾的选项，
                #    不该被"强制"拉回来 —— 那等于给越档/自相矛盾开后门。
                # ⚠️⚠️ 只在 **角色特征字段** 上让路（`CHAR_APPEAR_FIELDS`）：
                #    有角色时发色 / 瞳色 / 体型 / 肤色 / 脸型 / 发型 / 发饰归
                #    角色官方设定 —— 外貌对齐与发型对齐本来就会覆盖它们，
                #    此时再按选项权重硬塞一个头饰，等于给官方角色挂上不属于她的配件。
                # ⚠️ v4.2.0 用户口径（2026-10-29 19:25）：**非人物特征**（情绪 /
                #    嘴部 / 眼神 / 姿势 / 场景 / 服装 / 指甲 …）的 3.0 必须
                #    **不论勾不勾「不指定角色」都生效** —— 上一版一刀切按
                #    `role_pick` 关掉，把「哭泣=3」也一起关掉了（实测 45/46 号图）。
                _forced = ([] if (role_pick and key in CHAR_APPEAR_FIELDS)
                           else forced_option_pool(pool, key, weights))

                # 必填字段（源脚本的 req）永远抽；
                # 字段权重顶到 3.0 也永远抽（用户表达"这个一定要有"）；
                # 池子里有选项顶到 3.0 同样永远抽 —— 否则那个选项连
                # 出现的机会都没有（字段先被 base_prob 跳过了，实测 2/2 漏）。
                if fld.get("req") or fw >= WEIGHT_MAX or _forced or key in _tuned:
                    take = True
                elif key == "n_canon":
                    # v5.0.1（用户 2026-11-01）：**取消概率抽签，改成明确开关**。
                    # 旧行为：每轮 12% 命中、best-of-8 排序下来实测约 60% 的图带场景，
                    # 用户口径是「不知道什么时候会冒出场景」→ 现在：
                    #   · `scene_pick=True`  → 必出（并从所选档位里锁档抽一条）
                    #   · `scene_pick=False` → 一条都不出（含 SFW 档）
                    # ⚠️ 仍然无条件消耗一次 `pass_rng`：勾与不勾的 rng 消耗次数必须
                    #    一致，否则这个字段之后的每一个字段都会连锁漂移
                    #    （与 v4.1.1「强制项也要照旧调 weighted_pick」同一条纪律）。
                    pass_rng.random()
                    take = bool(scene_pick)
                elif (key in ("outfit", "makeup")
                      and OS.resolve_choice(outfit_style) not in ("", "auto")):
                    # v3.38.0：控件**显式指定**了风格时，整套服装与妆容都必须出现 ——
                    #    · 整套服装缺席 → 其它部位没有锚点，智配等于空转（实测 6/30）；
                    #    · 妆容缺席 → 衣服对了脸不对，整张图仍然不成立
                    #      （用户实测：哥特 + `no makeup`）。
                    take = True
                else:
                    take = pass_rng.random() < clamp_prob(
                        float(eff_p) * sw * fw * OS.layer_bias(st, key, outfit_style))
                if not take:
                    continue

                # ⚠️ v4.1.1：有「选项必现」时把候选**收窄到那几项**再抽。
                #    以前只把 3.0 当乘数：15 个选项里 3/(3+14) ≈ 18%，
                #    30 个选项只剩 10% —— 用户把「紫色发」点到 3.0，连抽两张都没中。
                #    仍然调用 weighted_pick（照旧消耗 1 次 rng）：
                #    有强制项 / 没强制项的 rng 消耗次数必须一致，
                #    否则这个字段之后的每一个字段都会连锁漂移。
                pick = weighted_pick(
                    pass_rng, _forced or pool,
                    lambda it: (option_weight(weights, key, it[1]) * sw * fw
                                # 联动③ 场景倾向（v3.15.0）：场景把相关选项
                                # 权重放大（默认 ×1.0 = 不干预），但**不剥夺**
                                # 其它选项 —— 泳池边照样可能抽到连衣裙。
                                * L.bias_for(st, key, it[1])
                                * OS.style_bias(st, key, it[1], outfit_style)
                                # v3.38.0：选项级风格偏置（目前只服务妆容）——
                                # 服装走上面那条"锚点+兼容度"，妆容没有锚点，
                                # 靠 STYLE_MAKEUP 的直方图决定该配哪几种妆。
                                * OS.field_option_bias(key, outfit_style, it[1])))
                if pick is None:
                    # 池子里所有选项权重都是 0 —— 等价于该字段被关掉，
                    # 不是错误，但值得记一笔（否则用户会以为随机坏了）
                    nt.append(f"字段「{fld['lab']}」候选权重全为 0，本次未抽取")
                    continue

                zh, en, num = pick
                st[key] = en
                dr.append((key, zh, en, num, option_weight(weights, key, en)))
                # 记下"这一轮哪些字段是用户点名必现的"：collect 拿它给互斥豁免，
                # trim_to_cap 拿它跳过密度裁剪（见两处注释）
                if _forced:
                    st.setdefault("_forced_tags", []).append((key, zh, en))

        # 选项级必现要写进报告：用户调了权重就该看见"它到底生效没有"，
        # 而不是靠翻正文猜（这也是"权重静默失效"这类问题唯一的现场证据）。
        # ⚠️ v4.2.0：这条说明**必须排在场景填充之后** —— L2+ 场景会用
        #    "场景表情优先"盖掉点名的情绪，先写就会出现"说明里有哭泣、正文里
        #    没有"的自相矛盾（用户实测正是这么被误导的）。

        # ── 法典场景填充 ──────────────────────────────────────────
        preset_id = st.get("n_canon") or ""
        if preset_id and preset_id in canon():
            # ⚠️ locks 必须传进去：预设会往 n_act / n_pos / n_theme 里写字，
            #    而锁定字段是"留给上游给值"的，预设不得代填（v3.8.0 补）。
            # ⚠️ forced 也算锁：用户显式强制的字段，优先级高于法典预设。
            #    不这样的话「forced place=poolside」会被预设里的 place=field 盖掉
            #    （实测撞到：sc_snow_field_scarf）。
            _fl = set(locks) | set(forced)
            # v3.38.0：控件**显式指定**服装风格时，法典场景不许写整套服装。
            #    场景里的 outfit 是写死的（教室 → serafuku、泳池 → 泳装），
            #    一旦套上就把风格收敛整个推翻 —— 实测选「哥特」的 30 抽里有 1 抽
            #    套到 sc_pose_classroom_sit，整套被改回水手服。
            #    场景其余字段（地点/光线/姿势）照常生效，只让出"定调单品"这一项。
            if OS.resolve_choice(outfit_style) not in ("", "auto"):
                _fl.add("outfit")
            filled = apply_preset(st, preset_id, lv, weights,
                                  locks=_fl, rng=pass_rng,
                                  variant_pick=variant_pick, variant_seed=variant_seed)
            if filled:
                nt.append("已应用法典场景：" + "、".join(filled))

        # ── v5.9.0：本轮场景是否带「具体衣物字段」的推荐服装 ────────────
        # 判据用 `_preset_fields` ∩ GARMENT_FIELDS，而不是直接查 scenes.json：
        #   `apply_preset()` 已经过档位 / 权重 0 / 锁定 / 权重 3.0 四道闸门，
        #   `written` 里只有**本轮真的写进 state** 的字段。查库会把「写了 L3
        #   泳装但当前 SFW 档」「被锁住没写进去」的场景也算成有推荐服装 ——
        #   服装库白让路，画面反而只剩随机服装。
        # 这里只判「事实」；开关值由 `effective_outfit()` 自己读，单人与双人
        # 共用同一条判据，不会出现单人让路、双人不让路的 split-brain。
        # ⚠️ 必须存进 `st` 而不是局部变量：`_collect_with_feat()` 是 draw 内
        #    与 `_one_pass()` **平级**的另一个嵌套函数，读不到这里的局部变量
        #    （第一版写成局部变量，check_v3_outfitlib 直接 NameError）。
        st["_scene_has_outfit"] = bool(
            st.get("n_canon")
            and (set(st.get("_preset_fields") or ()) & set(GARMENT_FIELDS)))
        # ── v6.6.1：场景**点名不要衣服**（全裸 / 换装中途）────────────────
        # 判据只认"主体衣物"三件（outfit / top / bottom）—— 只闭麦 socks /
        # shoes / accessory 不算（赤足或摘配饰的场景，整套预设照样成立），
        # 否则"沙滩赤足"这类场景会把白背心黑裙一起废掉，属于误伤。
        # 命中时**角色服装预设让路**：否则正文会同时写
        # `white tank top / black skirt` 和 `completely nude`，模型二选一，
        # 实测出现过"上半身穿衬衫、下半身全裸"的崩坏图。
        # 这条原先只影响单人路径，v6.6.1 让双人也遵守（同一判据，不分人数）。
        _sc_drop = set((scene_index().get(str(st.get("n_canon") or "")) or {})
                       .get("drop_fields") or ())
        st["_scene_no_outfit"] = bool(
            st.get("n_canon")
            and (_sc_drop & {"outfit", "top", "bottom"}))

        # ── 选项级必现的如实汇报（排在场景之后，见上一条注释）─────────
        _ft = st.get("_forced_tags") or []
        _over = set(st.get("_preset_overrode_forced") or ())
        _ft_eff = [x for x in _ft
                   if x[0] not in _over and x[0] not in _NO_EMIT_FIELDS]
        if _ft_eff:
            nt.append("按选项权重 %g 强制出现：%s"
                      % (WEIGHT_MAX, "、".join(f"{zh}" for _k, zh, _e in _ft_eff)))
        if _over:
            nt.append("场景表情优先（L%d 场景）→ 权重点名的 %s 已让路"
                      % (canon_level(preset_id),
                         "、".join(f"{zh}" for k, zh, _e in _ft if k in _over)))

        # ── 角色：统一入口（v3.2.0 起不再看权重）───────────────────
        # ⚠️ 顺序很重要，必须排在**法典场景之后**：
        #    法典预设会往 n_act / n_pos / n_theme 里写字，
        #    而多人模式下这些单人向字段要清空。之前放在预设之前，
        #    预设又把它们写回来了 —— 单元测试抓到过。
        #
        # 优先级（2026-10-03 与用户确认）：
        #   ① 手填 force_role —— 逃生舱，能填角色库里没有的自定义 tag
        #   ② 角色库已选     —— 点卡片 = 抽取概率 100%
        #   ③ 都没给         —— 必定从角色库随机抽 1 位（保证出图有角色）
        #
        # ⚠️ 这里**完全不读 weights**。「角色权重」在 v3.2.0 已取消：
        #    权重只能表达"想要什么倾向"，而角色是显式选择；两个入口并存时，
        #    用户点了角色却被权重静默否决，表现就是"我明明选了怎么没出来"。
        sel = expand_roles(selected_roles or [])
        triggers = []
        auto = False          # 角色是不是"系统自动随机抽的"
        if not role_pick:
            # ⚠️ v4.1.0：面板「角色库」页签的「🚫 不指定角色」——
            #    一个角色都不抽。为什么必须排在 force_role **之前**：
            #    它是一个**模式开关**（"这一批图不要角色"），留例外就会变成
            #    "有时候抽有时候不抽"，用户永远猜不到规律。
            #    不销毁任何东西 —— 手填与已选都原样留在控件里，取消勾选即恢复；
            #    但要让报告说清楚"它们这次为什么没生效"。
            _fr = str(force_role or "").strip()
            if _fr:
                nt.append(f"已勾选「不指定角色」→ 手填的 `{_fr}` 本次不生效"
                          "（清空「指定角色」或取消勾选即恢复）")
            if sel:
                nt.append(f"已勾选「不指定角色」→ 角色库里已选的 {len(sel)} 位"
                          "本次不生效（取消勾选即恢复）")
            nt.append("已勾选「不指定角色」→ 不抽角色，发色 / 瞳色 / 体型 / 发型"
                      "全部按词库权重抽取")
        elif force_role:
            triggers = expand_roles([force_role])
            if sel:
                nt.append("手填角色已覆盖角色库里的选择")
            # v4.5.34（用户 2026-10-30 拍板「这个场景不选角色」）：
            # `no_role` 场景（第一人称·俯视自身）本来就不该有脸，但显式点名
            # 优先级更高 —— 不阻止（用户可能就要这个人），只在报告里说清楚
            # 「脸为什么回来了」，否则用户只会觉得「插件没生效」。
            if scene_says_no_role(st.get("n_canon")):
                nt.append("⚠️ 该场景声明「画框里没有脸」，但手填了角色 —— "
                          "角色名会把镜头拉回脸部，建议清空「指定角色」")
        elif sel:
            triggers = sel
            if scene_says_no_role(st.get("n_canon")):
                nt.append("⚠️ 该场景声明「画框里没有脸」，但角色库里选了角色 —— "
                          "角色名会把镜头拉回脸部，建议取消选择（真机对照："
                          "不选角色 24/24 无脸，选了多数带脸）")
        elif "role" in locks:
            # ⚠️ v3.18.0：锁定字段 = "留给上游给值"，角色也不例外。
            #   在此之前 locks 只挡了预设代填，没挡"未选角色 → 必抽 1 位"，
            #   于是面板把 role 锁上也照样冒出角色 tag。
            #   这个口子对特写搭配是致命的：角色 tag 会把画框从腿/脚拉回脸。
            nt.append("角色字段已锁定 → 不自动抽角色（特写搭配用这招把画框锁在局部）")
        elif scene_says_no_role(st.get("n_canon")):
            # ⚠️ v3.32.0：场景可以声明 `no_role` —— "这一条的画框里没有脸"。
            #    为什么不能只靠前端 `dropRole`：那个机关只服务特写页（manual_only），
            #    普通场景套用根本不经过它；而这里的立意是**第一人称自我视角**
            #    （`sc_ccc_pov_self_body`），观众就是本人在低头看自己身体，
            #    角色 tag（`herta` / `siege` …）会把镜头从"自己的身体"拉回一张脸。
            #    与 locks 同一条纪律：**引擎侧也要兜一层**，否则直接走 API 就漏了。
            #    放在用户显式选择之后判：force_role / 面板点选仍然优先 ——
            #    用户主动点名的人不该被场景静默否决（那会表现成"我选了怎么没出来"）。
            nt.append("场景声明「画框里没有脸」→ 不自动抽角色"
                      "（角色名会把镜头拉回面部）；要角色请在面板里显式选择")
        else:
            # 未选角色 → 按「人数构成」抽**对应数量**的女性角色。
            #
            # ⚠️ 以前这里是固定抽 1 位，并用 headcount_tags 把人数强制改写成
            #    `1girl, solo` —— 结果把 count 抽到的「一男一女 / 双女 /
            #    一女多男 / 多人群交」全盖掉了。现在反过来：
            #    **人数决定角色数**，组合才真正生效。
            # 男方由 count 里的通用标签承担（`1boy` / `multiple boys`），
            # 角色库全女性，没有男性角色可指。
            want = girl_count_for(st.get("count", ""))
            fav_only = bool(role_fav_only)
            got = pick_random_roles(pass_rng, lv, want, works=works_filter,
                                    favorites_only=fav_only)
            if not got and fav_only and works_filter:
                # 该作品 ∩ 收藏 为空 → 先放宽作品限制，只保留收藏。
                got = pick_random_roles(pass_rng, lv, want, favorites_only=True)
                if got:
                    nt.append("⚠️ 指定作品 ∩ 收藏为空 → 已退化为「只在收藏内」随机"
                              f"（{len(got)} 位）")
            if not got and fav_only:
                # 收藏池在当前档位也空了 → 再退到全库。静默回退会让用户以为
                # "我开了仅收藏它却不听话"，所以一定写进报告。
                got = pick_random_roles(pass_rng, lv, want, works=works_filter)
                if not got and works_filter:
                    got = pick_random_roles(pass_rng, lv, want)
                if got:
                    n_fav = len(STORE.list_favorites())
                    nt.append(
                        "⚠️ 收藏池在当前档位没有可用角色"
                        + ("（收藏 0 位）" if not n_fav else f"（收藏 {n_fav} 位）")
                        + " → 已回退到普通随机"
                        f"（{len(got)} 位）")
            if not got and works_filter:
                # ⚠️ 指定的 IP 在当前档位下一位可用角色都没有 → **回退全库**，
                #    而不是"出图没有角色"。回退一定写进报告 —— 静默回退会让
                #    用户以为"我选了 IP 它却不听话"。
                got = pick_random_roles(pass_rng, lv, want)
                if got:
                    nt.append("⚠️ 指定的作品在当前档位下没有可用角色 → 已回退全库随机"
                              f"（{len(got)} 位）")
            if got:
                triggers = got
                auto = True
                if works_filter and fav_only:
                    src = "作品 " + "/".join(f"`{w}`" for w in works_filter) + " ∩ 收藏"
                elif works_filter:
                    src = "作品 " + "/".join(f"`{w}`" for w in works_filter)
                elif fav_only:
                    src = "收藏"
                else:
                    src = "全库"
                nt.append(f"未选角色 → 从{src}按人数组合抽了 {len(got)} 位："
                          + "、".join(f"`{g}`" for g in got))

        if triggers:
            # ── v6.6.0：双女自动补位（用户 2026-11-07）─────────────────
            # 「只选了 1 位角色 + 双女/多女人数」= 模型只能把同一个人画两遍。
            # 判据只看**人数**（`girl_count_for`），不问人数是场景 `count_lock`
            # 钉的还是「人数构成」自己抽的 —— 用户 2026-11-07 拍板"任何双女
            # 人数都补"。`auto=True` 那条路不走这里：它本来就按人数抽够了。
            #
            # ⚠️ 只在开关打开时才消耗 rng；关闭态（或人数不足 2 / 角色数已够）
            #    一次 `random` 都不吃 ⇒ 与 v6.5.2 逐字节零漂移。
            # ⚠️ 补进来的人**必须**走 `apply_selected_roles`：双人外貌/服装是靠
            #    `_role_triggers` 逐人绑定成整句的（见 duo_appearance_text），
            #    只往 `st["role"]` 里拼字符串会被四张查表全部查空。
            _duo_filled = 0
            if duo_role_fill and not auto and len(triggers) < 3:
                _need_g = girl_count_for(st.get("count", ""))
                _want_g = min(int(_need_g), 3) - len(triggers)
                while _want_g > 0:
                    _partner, _mode = pick_duo_partner(
                        pass_rng, lv, triggers[0], exclude=triggers,
                        favorites_only=bool(role_fav_only),
                        works=works_filter)
                    if not _partner:
                        break
                    triggers.append(_partner)
                    _duo_filled += 1
                    _want_g -= 1
                    if _mode == "same_work":
                        nt.append("双女自动补位：同作品「%s」→ 补上第二位女性 `%s`"
                                  "（避免模型把同一个人画两遍）"
                                  % (_role_short(triggers[0]), _role_short(_partner)))
                    elif _mode == "unknown_work":
                        nt.append("⚠️ 双女自动补位：角色 `%s` 不在角色库里"
                                  "（查不到所属作品）→ 已回退全库随机补位 `%s`"
                                  % (_role_short(triggers[0]),
                                     _role_short(_partner)))
                    else:
                        nt.append("⚠️ 双女自动补位：角色 `%s` 的作品里只有她一位女性"
                                  " → 已回退全库随机补位 `%s`（两位可能不同世界观）"
                                  % (_role_short(triggers[0]),
                                     _role_short(_partner)))
            # auto=True 时**不改写人数标签**：人数是 count 抽出来的组合，
            # 角色只是填进这个组合的角色位。
            # auto=False（用户显式选中 / 手填）时才按角色数改写 ——
            # 那时人数理应由"选了几个人"决定。
            # ⚠️ v3.42.20：场景 `count_lock` 已显式定人数（count 在
            #    _preset_fields）时**不改写** —— 显式选角色只填女位。
            #    否则「双人正戏场景 + 点 1 位角色」会把 1girl,1boy,hetero
            #    压成 1girl,solo，sanitize 判"无伴侣"清空 n_act/n_cum，
            #    场景立意（体位/人数）整段消失。实测 15 个 sc_ccc_* 双人/
            #    多人正戏场景 3/3 复现：传教士画成骑乘、双插压成 solo。
            #    面板手动「点角色 + 套场景」同样受益。
            # ⚠️ v6.6.0：刚补完位时同样**不改写** —— count 已经写明 2girls，
            #    再按"2 位角色"改写只会把 `2girls` 换成 `2girls, yuri`，
            #    等于替用户升了一档语义（L0 双女场景会被无端加 yuri）。
            _count_locked = "count" in set(state.get("_preset_fields") or ())
            nt.extend(apply_selected_roles(st, triggers, lv,
                                           rewrite_count=not auto
                                           and not _count_locked
                                           and not _duo_filled))
            st["_role_count"] = len(triggers)
            # v5.0.2：服装预设报告 —— 只报一次（密度重试 8 轮，每轮都会走到这里）
            # v5.0.7：双人改成"按人绑定"的口径，不能再写"服装字段全部闭麦"
            #        （双人时并不闭麦，只是把共享字段清掉、改用绑定句）。
            if not any(_x.startswith("已锁定服装") or _x.startswith("已启用服装")
                       for _x in nt):
                _oids = []
                for _tr in triggers[:3]:
                    _oi = outfit_item(_tr)
                    if _oi:
                        _oids.append("%s（%s）"
                                     % (_oi.get("zh") or _oi.get("id") or "",
                                        _tr.split(",")[0].strip()))
                if _oids:
                    if len(triggers) >= 2:
                        # v6.6.1：这句只报"启用了什么"，**不承诺绑定成功** ——
                        # 真正绑没绑由下面「服装按人绑定 / 预设没有可写标签」判定。
                        nt.append("已启用服装：%s —— 多人时按人绑定成整句"
                                  "（结果见下一条服装备注）"
                                  % " / ".join(_oids))
                    else:
                        nt.append("已锁定服装：%s —— 本张服装字段全部闭麦，"
                                  "随机抽不到别的衣服" % _oids[0])

        # ── v4.2.3：点名性行为 → 人数下限在角色联动之后仍要守住 ──────────
        # 实测（`%_00065/68/69_.png` 三连）：选了雷电将军 → `apply_selected_roles`
        # 按"选中 1 位"把人数改写成 `1girl, solo`（v3.2.0 的既有语义：人数跟着
        # 角色数走），把字段循环里抬起来的 hetero **又压了回去** → 人数相容
        # 把点名的 `anilingus` 清掉 —— 用户看到"我改了性行为权重却没生效"。
        # 规则：点名了需要对方/群交的性行为，且角色数只有 1 位女性
        # （1 位 → `1girl, 1boy, hetero` 兼容；2 位双女 → 与异性行为矛盾，不抬，
        #  让位给"双女用穿戴式"那条规则），且人数不是用户自己点名的 → 抬回去。
        _sex_target = forced_sex_count_target(weights)
        if _sex_target and "_sex_count_fix" in st \
                and not any(t[0] == "count"
                            for t in (st.get("_forced_tags") or ())):
            _c1 = str(st.get("count") or "")
            if not count_satisfies(_c1, _sex_target) \
                    and girl_count_for(_c1) == 1:
                st["count"] = _sex_target
                nt.append("性行为已点名必现 → 人数由 `%s`（按角色数改写）抬到 `%s`"
                          % (_c1 or "(空)", _sex_target))

        # ── 角色外貌对齐 ──────────────────────────────────────────
        # ⚠️ 只在**单个**角色时做。两位以上各有各的官方发色，
        #    强行对齐其中一位是错的（角色 tag 本身已经带足特征）。
        if st.get("role"):
            if len(triggers) >= 2:
                # v4.5.0：两位以上**各自**对齐 —— 平铺标签分不清"哪个颜色是谁的"，
                # 所以改成按角色名绑定的整句（见 duo_appearance_text 的说明）。
                _forced_keys = {f[0] for f in (st.get("_forced_tags") or ())}
                _dyn = set()
                _duo = duo_appearance_text(triggers) if appearance_follow else ""
                if _duo:
                    st["_duo_appearance"] = _duo
                    _dyn.add(_duo.strip().lower())
                    # 共享的发色/瞳色**必须让位**：它们只有一个槽位，
                    # 随机抽出来的 `brown hair` 会和整句里的两个发色打架
                    #（实测这类自相矛盾的并列标签会让两人长得越来越像）。
                    # ⚠️ 用户点名（3.0）的字段不动 —— 显式 > 对齐。
                    for _k in ("hair_color", "eyes_color"):
                        if _k not in _forced_keys and st.get(_k):
                            st[_k] = ""
                    st["_appearance"] = "duo"
                    nt.append("多位角色：外貌按人绑定（"
                              + " / ".join(triggers) + "）")
                else:
                    st["_appearance"] = "multi"
                    nt.append("多位角色：已跳过外貌对齐（各自保留官方特征）")

                # ── v5.0.7：双人各自的服装预设与固定特征 ──────────────
                # 与"外貌对齐"同一个坑：`outfit_for()` / `char_feat_for()` 都是
                # **单角色接口**，而上面那道「多人各有各的官方配件」判断会把它们
                # 一起跳过 —— 于是两人都启用了官方服装，出来的还是随机皮衣黑裙
                #（用户 2026-11-02 实测）。这里补成按人绑定的整句。
                # v6.6.1：场景点名闭麦衣物时双人也让路（与单人同一判据）——
                # 裸体场景里写"谁穿什么"会让模型一半穿衣一半裸。
                _do = "" if st.get("_scene_no_outfit") else duo_outfit_text(
                    triggers, lv, bool(st.get("_scene_has_outfit")))
                if st.get("_scene_no_outfit") \
                        and any(outfit_item(_t) for _t in triggers[:3]) \
                        and not any("角色服装预设让路" in _n for _n in nt):
                    nt.append("角色服装预设让路：本场景点名闭麦衣物"
                              "（全裸 / 换装中），双人也按此处理")
                if _do:
                    st["_duo_outfit"] = _do
                    _dyn.add(_do.strip().lower())
                    # 共享的服装字段同样让位：平铺的 `jeans` 会和整句里的
                    # 「谁穿什么」打架，模型二选一的结果通常是两件都画。
                    # v6.6.1：清空集合 = 基础 6 项 ∪ **各人预设的 drop 并集**。
                    #   以前只清 6 项，漏了袜子/鞋子/配饰 —— 实测 texas 的
                    #   官方造型写成整句后，正文里还留着随机 `fishnet pantyhose`
                    #   （她那套本来就 drop 了 socks/shoes），等于自己和自己打架。
                    _duo_drop = {"outfit", "top", "bottom", "cloth_color",
                                 "cloth_mat", "cloth_state_sfw"}
                    for _t in triggers[:3]:
                        _it = outfit_item(_t)
                        if _it:
                            _duo_drop |= set(_it.get("drop") or ())
                    for _k in sorted(_duo_drop):
                        if _k not in _forced_keys and st.get(_k):
                            st[_k] = ""
                    nt.append("多位角色：服装按人绑定（各自启用中的预设）")
                elif any(outfit_item(_t) for _t in triggers[:3]):
                    # v6.6.1：有人启用预设却写不出整句（预设没有可写标签 /
                    # 档位不足）→ 共享服装字段不会被清空，必须如实说，
                    # 不能沿用上面那句"已按人绑定"（2026-11-07 审查踩过：
                    # 备注说已绑定，正文里是随机 hakama）。
                    nt.append("⚠️ 多位角色：服装预设里没有可写入的标签（或档位不足）"
                              "→ 共享服装字段按随机处理")
                if appearance_follow:
                    _df = duo_feat_text(triggers, seed, lv,
                                        bool(st.get("_scene_has_outfit")))
                    if _df:
                        st["_duo_feat"] = _df
                        _dyn.add(_df.strip().lower())
                        nt.append("多位角色：官方配件按人绑定")
                if _dyn:
                    st["_nl_dynamic"] = set(st.get("_nl_dynamic") or ()) | _dyn
            elif not appearance_follow:
                st["_appearance"] = "off"
                nt.append("角色外貌对齐已关闭 —— 发色/瞳色按随机与权重决定")
            elif not appearance_for(st["role"]):
                st["_appearance"] = "nodata"
                nt.append("该角色没有外貌数据（不在 APPEAR 表，角色库里也没特征 tags）"
                          "—— 发色瞳色交给模型自己认")
            else:
                changed = apply_appearance(st, weights)
                st["_appearance"] = "applied" if changed else "blocked"
                if changed:
                    nt.append("角色外貌已对齐（会覆盖发色/瞳色等选项权重）："
                              + "、".join(changed))

        # ── 角色发型对齐（v3.45.0，控件 hair_follow）──────────────────
        # 与外貌对齐**独立开关**：发色关了也能单开发型。
        # 单角色才做（多人各有各的发型，对齐其中一个是错的）。
        # v3.47.0：四个发型下拉（hair_len/tie/curl/acc_ pick）接管 ——
        # 只要有一项不是「角色官方」，就按 picks 逐项应用，hair_follow 不再参与。
        # 全部「角色官方」时**完全走旧路径**（零漂移）。
        _hp = hair_picks or {}
        _hp_exp = any(str(_hp.get(k) or HAIR_PICK_AUTO).strip()
                      not in (HAIR_PICK_AUTO, "") for k in HAIR_STYLE_FIELDS)
        if st.get("role"):
            if len(triggers) >= 2:
                st["_hair_style"] = "multi"
            elif _hp_exp:
                _hch = apply_hair_style(st, weights, picks=_hp)
                st["_hair_style"] = "picked" if _hch else "blocked"
                if _hch:
                    nt.append("发型按选择器指定：" + "、".join(_hch))
            elif not hair_follow:
                st["_hair_style"] = "off"
                nt.append("角色发型对齐已关闭 —— 发型按随机与权重决定")
            elif not hair_style_for(st["role"]):
                st["_hair_style"] = "nodata"
                nt.append("该角色没有官方发型数据 —— 发型交给随机")
            else:
                _hch = apply_hair_style(st, weights)
                st["_hair_style"] = "applied" if _hch else "blocked"
                if _hch:
                    nt.append("角色发型已对齐（长度 / 束法 / 卷度）："
                              + "、".join(_hch))
        elif _hp_exp:
            # ⚠️ v4.1.0：**没有角色**时，发型选择器里显式指定的项照样生效。
            #    不写这条的话「不指定角色 + 发型长度 = 超长发」会静默失效 ——
            #    用户看到的是"我明明选了超长发，却抽成短发"。
            #    只走显式分支：没有角色就没有"官方发型"可取，
            #    「角色标签发型 / 随机」两档在这里等价于"交给常态随机"，无需干预。
            _hch = apply_hair_style(st, weights, picks=_hp)
            st["_hair_style"] = "picked" if _hch else "blocked"
            if _hch:
                nt.append("发型按选择器指定（本次不抽角色）：" + "、".join(_hch))

        # ── 胸部大小等级（v3.17.0，扩展自巨乳开关）────────────────────
        # ⚠️ 必须排在外貌对齐**之后**：APPEAR 表里有 10 个角色带 figure 值
        #    （211 个为空），撞上就会被冲掉。显式指定 = **最高优先级**，
        #    覆盖角色官方设定与任何权重（与原巨乳开关语义一致）。
        # 兼容：breast_size 为空时回退旧开关 big_breasts（老工作流不迁移）。
        _bs_en = BREAST_SIZES.get(str(breast_size or "").strip())
        if _bs_en:
            st["figure"] = _bs_en
            st["_breast_size"] = _bs_en
            nt.append(f"胸部大小：身材轮廓锁定为「{breast_size}」（{_bs_en}）")
        elif big_breasts:
            st["figure"] = "huge breasts"
            st["_big_breasts"] = True
            nt.append("巨乳开关：身材轮廓锁定为「巨乳」（huge breasts）"
                      "（旧版开关兼容，建议改用「胸部大小」下拉）")

        # ── 禁项复核（v3.38.3）──────────────────────────────────────
        # `breast_size` / 旧 `big_breasts` 开关会在 apply_preset **之后**直接写 st["figure"]，
        # 那时 apply_preset 内部的禁项扫描已经跑完了 → 禁项被绕过。
        # 这里在所有参数注入之后、sanitize 之前再扫一次，保证"场景说不许"的标签一定不出现。
        _ban2 = apply_bans(st, preset_id)
        if _ban2:
            nt.append("禁项·参数复核后清空：" + "、".join(_ban2))

        # ── 消解自相矛盾 ──────────────────────────────────────────
        cleared = sanitize(st, weights)
        if cleared:
            nt.append("互斥消解清空：" + "、".join(cleared))

        # ── 穿戴式道具 ↔ 人数（v4.2.2，用户口径）──────────────────
        # 「双女性行为使用穿戴式道具，一男一女不使用穿戴式道具」。
        # 排在 sanitize 之后：人数在这里已是最终值（场景 count_lock /
        # 角色联动都写完了），先补后清才不会互相打架。
        for _n in apply_count_toy_rule(st, lv, weights):
            nt.append(_n)

        # ⚠️ 高等级保底**不在这里**做：`trim_to_cap`（draw 里的兜底裁剪）还会
        #    清空字段，先补后削等于没补。见 draw 里 trim 之后的那一段。

        # ── 颜色合成（v3.19.0）─────────────────────────────────
        # ⚠️ 必须排在 sanitize **之后**：sanitize 可能清掉 socks
        #    （例如 pantyhose + bare feet），着色目标要按清理后的状态定。
        for _n in OS.colorize(st):
            nt.append(_n)

        return st, dr, nt

    # ── 密度自适应 ────────────────────────────────────────────────
    #
    # 为什么不学源脚本「抽完再硬削」：
    #   源脚本从固定的 8 个软字段里删，实测一次能删掉 13 个字段
    #   （发长 / 发型 / 脸型 / 气质 / 眼神 / 体型 / 肤色 全没了），
    #   画面特色被削平，而且 F1 还照样红。
    #   这里改成**降密度重抽** —— 每个字段始终是干净的「抽中 / 没抽中」，
    #   不会出现「字段留了但值被删空」这种半吊子状态。
    #
    # 重抽用派生种子，所以 seed>=0 时整体依然逐字节可复现。
    auto = tag_cap in ("自动", None, "")
    density_notes = []
    best = None

    # 密度用**二分**收敛，不用比例反馈。
    # 比例反馈的毛病：标签数 15（目标 23）→ 下一轮密度 1.53 倍 → 直接冲到 40，
    # 两头都不合格，而"最接近的一轮"仍然是那个 15。二分能夹住。
    #
    # 为什么是 8 轮而不是 3-4 轮：
    #   每一轮用的派生种子不同，同一个密度系数下标签数本身有 ±5 的抖动，
    #   所以"收敛"收敛的是系数，落点仍会飘。多跑几轮取最接近的那次，
    #   实测能把自检 F1 的失败率从 ~1% 压到 0。8 轮总耗时仍在 5ms 以内。
    # v3.24.1：候选构图变体在**一次 draw 内只定一次**，不跟着密度重试走。
    # 理由见 apply_preset 里 variant_pick 的注释（best-of-8 会把它压成单边）。
    _variant_pick = (random.Random(derive_seed(int(seed), "variant", 0)).random()
                     if seeded else random.random())
    # v3.27.0：多池变体要按池名分别派生，所以这里给**种子**而不是预先算好的浮点数。
    # 未指定 seed（每次重抽）时也造一个，保证同一次 draw 内所有尝试用同一组变体。
    _variant_seed = int(seed) if seeded else random.randrange(1, 1 << 30)

    # v3.40.0：场景档位分层的骰子。
    # ⚠️ 用派生种子、**不消耗 pass_rng** —— 否则所有既有统计基线都会被撞歪
    #    （包括与场景无关的姿势/表情分布）。lv=0 时这个值根本不参与判断，
    #    所以 SFW 档与改动前逐字节一致。
    # ⚠️ 一次 draw 内只翻一次牌：密度重试是 best-of-8，每轮重翻会把命中率
    #    压成单边（`_variant_pick` 上踩过同一个坑）。
    _scene_coin = random.Random(
        derive_seed(_variant_seed, "scene_level", 0)).random()

    r_lo, r_hi = 0.05, 1.8
    ratio = 1.0

    # ── 角色固定特征（v3.46.0）──────────────────────────────────────
    # 词库外标签（精灵耳 / 机械眼罩 …）唯一合规的落点是 extra 尾部：
    # `collect(tail_extra=)` 不过 cap、不过互斥，也不会进任何随机池 ——
    # 若塞进词库（hair_acc 之类）会被 2000+ 角色随机抽到，等于污染全库。
    def _collect_with_feat(st):
        eb = extra_body
        # v4.4.0：被点名（3.0）的复杂词条补一句自然语言（见 term_hints 的说明）。
        # 与角色固定特征共用 tail_extra 通道：不过 cap、不过互斥、不进任何随机池。
        _hint = term_hint_text(st)
        # v4.4.2：场景条目自带的自然语言（`nl`），与词条提示拼在一起
        _snl = str(st.get("_scene_nl") or "").strip()
        if _snl:
            _hint = (_hint + " " + _snl).strip() if _hint else _snl
        # v4.5.0：多角色外貌绑定句（与场景句同一通道，见 duo_appearance_text）
        # ⚠️ 用**逗号 + 空格**而不是空格拼接：tail_extra 会按逗号切成标签，
        #    切成独立一条才进得了 `_nl_dynamic` 的放行集合；拼在场景句后面
        #    会变成"场景句 + 绑定句"的新串，F11 会把它当超长未登记标签报红
        #    （gate 5 档实测：sfw 6 / extreme 38 张中招）。
        if _hint:
            eb = (eb.strip() + ", " + _hint) if eb.strip() else _hint
        # v5.0.7：双人各自的服装 / 官方配件句（与外貌句同一条 tail_extra 通道）。
        # ⚠️ 逗号 + 空格拼接 —— tail_extra 按逗号切标签，切成独立一条才进得了
        #    `_nl_dynamic` 的放行集合；拼在场景句后面会变成新串，F11 报超长未登记。
        for _duo_key in ("_duo_appearance", "_duo_outfit", "_duo_feat"):
            _duo = str(st.get(_duo_key) or "").strip()
            if _duo:
                eb = (eb.strip() + ", " + _duo) if eb.strip() else _duo
        # ── v5.0.13：角色服装预设（**不受「外貌对齐」管辖**）───────────────
        # 用户口径：「服装由角色预设决定，未启用则随机抽取」。
        # 这段原先写在外貌对齐的 `if` 里面 → 用户只想关官方发色/瞳色时，服装预设
        # 会跟着静默失效（实测 seed 12345 / 蒂法 / appearance_follow=False：
        # `_outfit_tags` 为空，正文退回随机皮裤）。服装是**手选**的，不是"跟随"。
        # ⚠️ 标签走 body 通道（标签区末尾、自然语言句之前），不走 tail ——
        #    排在场景句之后会被当散文读，白背心/黑裙这种细节会明显变弱。
        _obody = ""
        if int(st.get("_role_count") or 1) < 2:
            # v5.1.0：角色预设 > 服装库 > 随机（见 effective_outfit 的说明）
            # v5.9.0：场景带推荐服装且开关关闭时，服装库整套让路。
            # v6.6.1：场景**点名闭麦衣物**（全裸）时角色预设也让路 ——
            #   否则"她穿着白背心黑裙"和"completely nude"会同时进正文。
            _oex, _odrop, _oset = effective_outfit(
                st.get("role") or "", lv,
                bool(st.get("_scene_has_outfit")))
            if _oex and st.get("_scene_no_outfit"):
                _oex, _odrop, _oset = [], [], {}
                # 密度重试每轮都会走到这里 —— 只报一次（按 notes 去重，
                # 不能只靠 st 上的 flag：每轮 _one_pass 都是新 state）
                if not any("角色服装预设让路" in _n for _n in notes):
                    notes.append("角色服装预设让路：本场景点名闭麦衣物"
                                 "（全裸 / 换装中），穿与不穿不能同时成立")
            if _oex:
                _obody = ", ".join(_oex)
                st["_outfit_tags"] = _obody
            for _k in (_odrop or ()):
                st[_k] = ""
            # set 排在 drop 之后：同一字段既 drop 又 set 时以 set 为准
            for _k, _v in (_oset or {}).items():
                st[_k] = _v
        # 多人各有各的官方配件，对齐其中一个是错的（同外貌/发型对齐口径）
        if appearance_follow and int(st.get("_role_count") or 1) < 2:
            _feat, _drops, _sets = char_feat_for(st.get("role") or "", seed)
            # ⚠️ char_feat 的 extras 里有 5 个角色写死了发长/束法（胡桃 twintails、
            #    拉姆雷姆 short hair、希格莉德 ponytail…）。用户用发型选择器显式指定
            #    **同一维度**时必须把 extras 里的那份剔掉，否则会同时出现两个长度标签
            #    （实测 `bob cut` + `short hair` 并存）或触发 G2 互斥。
            _xh = set(st.get("_hair_picks_explicit") or ())
            if _feat and "hair_len" in _xh:
                _feat = [x for x in _feat if x not in HAIR_LEN_TAGS]
            if _feat and "hair_tie" in _xh:
                _feat = [x for x in _feat if x not in HAIR_TIE_TAGS]
            if _feat:
                # v5.4.1：自定义角色走 **body** 通道。
                # 它们没有人工整理的特征表，兜底出来的是整串词库外标签
                # （`fin ears` / `blue scales` / `chubby`…），拼 tail 会排到
                # `scene_nl` 之后被 T5 当散文读，权重掉一截。
                # 内置 58 条的行为**保持原样**（拼 tail），否则基线会动。
                if _feat_goes_body(st.get("role") or ""):
                    _obody = (_obody.strip() + ", " + ", ".join(_feat)) \
                        if _obody.strip() else ", ".join(_feat)
                else:
                    eb = (eb.strip() + ", " + ", ".join(_feat)) if eb.strip() \
                        else ", ".join(_feat)
                st["_char_feat"] = ", ".join(_feat)
            _xhair = set(st.get("_hair_picks_explicit") or ())
            for _k in _drops:
                if _k in _xhair:
                    continue      # 用户显式选了这一项，drop 不得覆盖（见 apply_hair_style）
                st[_k] = ""
            # set 排在 drop 之后：同一字段既 drop 又 set 时以 set 为准
            for _k, _v in _sets.items():
                st[_k] = _v
        return collect(st, tail_extra=eb, body_extra=_obody)

    for attempt in range(8):
        if attempt == 0:
            rng = random.Random(int(seed)) if seeded else random.Random()
        else:
            rng = random.Random(derive_seed(
                seed if seeded else random.random(), "density", attempt))
        eff_p = clamp_prob(base_prob * ratio)
        state, drawn, step_notes = _one_pass(rng, eff_p, _variant_pick, _variant_seed)

        cap = tag_cap_for(state.get("count", "")) if auto else int(tag_cap)
        tags = _collect_with_feat(state)
        # ⚠️ 密度瞄准的必须是**修完之后**的标签数：repair 会剔掉超档 / 冲突标签，
        #    拿未修的数字收敛会一路瞄高，修完就掉出 F1 的下界。
        n = len(repair(tags, lv, state.get("_nl_dynamic"))[0])

        if not auto:
            best = ((0, 0), state, drawn, step_notes, tags, cap)
            break

        # 瞄准自检 F1 的合格区间打分：0 = 落在区间内，其余为离最近边的距离
        lo, hi = tag_window(state.get("count", ""))
        score = 0 if lo <= n <= hi else (lo - n if n < lo else n - hi)
        # v3.42.1：**场景优先**——先比「本轮有没有抽到场景」，再比标签数。
        # 为什么必须这样：改之前是 `(score, 有无场景)`，只有同分才偏向场景，
        # 于是第 1 轮没抽中场景、而它标签数更漂亮时，后面几轮即使抽到了
        # 也永远赢不了 —— 实测同一批 seed 上"出现率"会从 65% 掉到 50%
        # （设定值只兑现了第一轮的那一次抽签）。
        has_scene = bool(state.get("n_canon"))
        # ⚠️ lv=0 必须走**旧排序** `(score, 有无场景)`，否则 SFW 档也会被
        #    "场景优先"推着跑满 8 轮 → 实测出现率从 33% 飙到 87%，
        #    SFW 零漂移立刻破功。只有 lv≥1 才用场景优先。
        _sc = ((score, 0 if has_scene else 1) if lv <= 0
               else (0 if has_scene else 1, score))
        if best is None or _sc < best[0]:
            best = (_sc, state, drawn, step_notes, tags, cap)
        # 只有「带场景且落进窗口」才提前收工（lv=0 保持旧逻辑 → SFW 零漂移）。
        # 否则无场景轮一旦 score=0 就 break，后面的轮根本没机会补场景。
        if score == 0 and (lv <= 0 or has_scene):
            break

        target = (lo + hi) // 2
        if n < target:
            r_lo = ratio          # 太稀，往密的方向收
        else:
            r_hi = ratio          # 太密，往稀的方向收
        new_ratio = round((r_lo + r_hi) / 2, 3)
        if abs(new_ratio - ratio) < 1e-3:
            break                 # 夹逼到底了，再跑也是同一个值
        ratio = new_ratio
        density_notes.append(
            f"标签数 {n}（目标 {lo}–{hi}）→ 密度 {ratio} 再试")

    _score, state, drawn, step_notes, tags, cap = best
    notes.extend(density_notes)
    notes.extend(step_notes)

    # 兜底裁剪：重抽 4 轮仍超（法典预设自带一大串标签时可能发生）才硬削
    dropped = trim_to_cap(state, cap, weights)

    # ── 高等级保底（v3.41.0）──────────────────────────────────────────
    # ⚠️ 位置是踩了三次才定下来的，三个位置都不能再挪：
    #    ① 不能在 `_one_pass` 里（sanitize 之前）—— v3.39.0 的上肢单槽会在
    #       `arms_hands` 占手时清掉整个 `pose_risque`，而它是 L1 唯一的载体，
    #       先补后清 = 白补（实测 L1 有 26% 因此掉回 L0）；
    #    ② 不能在 `trim_to_cap` 之前 —— trim 也会清空字段，同样白补；
    #    ③ 必须排在 `collect` 之前 —— 它改的是 state，collec t之后改就进不了正文。
    # 这里拿到的才是**真正会写进提示词**的最终 state。
    _floor = ensure_level_floor(
        state, lv, weights, set(locks) | set(forced),
        state.get("n_canon") or "",
        random.Random(derive_seed(_variant_seed, "level_floor", lv)))
    if _floor:
        notes.extend(_floor)
        # 补进来的锚点自身可能与已抽内容互斥，再过一遍消解（幂等，不会破坏已干净的结果）
        _cleared_f = sanitize(state, weights)
        if _cleared_f:
            notes.append("保底后消解：" + "、".join(_cleared_f))
    # ── 权重 0 = 永不出现（v4.3.0）────────────────────────────────────
    # ⚠️ 位置：trim / 保底 / sanitize 之后，collect 之前 —— 这三步都可能
    #    把"用户已经关掉的选项"重新写回 state（场景 tags、保底锚点、互斥改写）。
    #    这里是进正文前的最后一道闸，代价是"关掉的词不会因保底而降档"，
    #    但那正是用户的口径：0 就是永不出现。
    #    ⚠️ 例外：绑定了角色且开了外貌对齐时，发色/瞳色/发型归角色官方设定 ——
    #       用户把「黑发」设 0 不该让角色变成没有发色（既有口径：官方 > 权重）。
    _zero_protect = set(locks) | set(forced)
    if state.get("role") and appearance_follow:
        _zero_protect |= set(CHAR_APPEAR_FIELDS)
    _zeroed = swept_zero_weight(state, weights, protect=_zero_protect)
    if _zeroed:
        notes.append("权重 0（关闭）清空：" + "、".join(_zeroed))

    tags = _collect_with_feat(state)

    # ── 成品检查 + 自动修复（v3.8.0）───────────────────────────────
    # ⚠️ 必须排在 trim 之后、check 之前：
    #    · 排在 trim 之后 —— trim 会把字段清空，早于它修等于修完又被削；
    #    · 排在 check 之前 —— check 报的是"最终交付的那串"，
    #      修完再体检，报告里的结论才和用户看到的正文一致。
    tags, fix_notes = repair(tags, lv, state.get("_nl_dynamic"))
    text = ", ".join(tags)

    # 自检要报告"权重有没有真的用上"，先把它挂进 state（下划线键不参与输出）
    state["_weights_tuned"] = weight_stats(weights)["total"]

    # ── 体检 + 超档回滚（最多重试 max_retry 次）────────────────────
    report = check(tags, state, lv)
    tries = 0
    while report["over_level"] and tries < max_retry and not seeded_is_locked(forced):
        tries += 1
        # 回滚策略：把超档的字段清掉再重抽一次那个字段。
        # 刻意不做「整轮重抽」—— 那会把用户已经满意的字段一起洗掉。
        for key in report["over_fields"]:
            state.pop(key, None)
        for key in report["over_fields"]:
            pool = eligible_options(key, lv)
            if not pool:
                continue
            pick = weighted_pick(
                rng, pool, lambda it: option_weight(weights, key, it[1]))
            if pick:
                state[key] = pick[1]
        tags = _collect_with_feat(state)
        tags, more_fix = repair(tags, lv, state.get("_nl_dynamic"))
        for f in more_fix:
            if f not in fix_notes:
                fix_notes.append(f)
        text = ", ".join(tags)
        report = check(tags, state, lv)
    if tries:
        notes.append(f"超档回滚 {tries} 轮")
    if fix_notes:
        notes.append(f"成品自动修复 {len(fix_notes)} 项：" + "；".join(fix_notes[:8])
                     + ("…" if len(fix_notes) > 8 else ""))

    report["notes"] = notes
    report["drawn"] = [
        {"field": k, "zh": zh, "en": en, "level": num, "weight": round(w, 2)}
        for k, zh, en, num, w in drawn]
    report["dropped"] = dropped
    report["seeded"] = seeded
    # 修复明细单独给一个字段：前端与「提示词检查与修复」节点都用它
    report["repaired"] = fix_notes
    # 完整 trigger（含作品名）留给报告与面板溯源 —— 正文里已经去掉了
    report["role_full"] = [str(t) for t in (state.get("_role_triggers") or [])]

    # v3.18.6：正文去掉作品 IP 名（重名角色例外）。只影响输出，
    # 不参与抽取 / 自检 / 自动修复 —— 否则同 seed 的抽取结果会变。
    out_tags = strip_role_works(tags, state)
    # v6.5.0：干爽过滤是**最后一步**，只动成品 tag 列表 —— 前面的抽取 / 档位 /
    # 互斥 / 报告全部一字不变，所以关闭态零漂移、开启态也不改随机序列。
    if dry_skin_filter:
        out_tags, _dry_removed = strip_skin_moisture(
            out_tags, dry_skin_groups, dry_skin_tags)
        if _dry_removed:
            _show = "、".join(_dry_removed[:8])
            if len(_dry_removed) > 8:
                _show += "…"
            notes.append("干爽过滤：剔除 %d 项（%s）"
                         % (len(_dry_removed), _show))
            report["notes"] = notes
    out_text = ", ".join(out_tags)
    return {
        "text": out_text,
        "state": state,
        "level": lv_key,
        "level_num": lv,
        "level_label": level_label(lv_key),
        "report": report,
        "notes": notes,
        "dropped": dropped,
        "cn_summary": cn_summary(out_tags),
        "tags": out_tags,
    }


def seeded_is_locked(forced) -> bool:
    """强制指定了字段时不做回滚 —— 用户明确点了要这个，回滚等于抗命。"""
    return bool(forced)


def apply_preset(state: dict, preset_id: str, lv: int,
                 weights: dict = None, locks=None, rng=None,
                 variant_pick=None, variant_seed=None) -> list:
    """法典场景一键填充。返回实际填入的字段中文标签列表。

    ⚠️ 与其他字段走**同三条闸门**：
       · 档位：预设要写进去的字段如果本档不允许，照样不写；
       · 权重：用户把某字段权重设 0（或关掉整段）就跳过该字段；
       · 锁定（v3.8.0 补）：`lock_fields` 里的字段"留给上游给值"，
         预设不得代填。少这一条，锁了 n_act 也会被预设写回去 ——
         用户看到的是"我锁了它还改"。
       少任何一条，法典预设都会变成绕过用户设置的"后门"。
    """
    p = canon().get(preset_id)
    if not p:
        return []
    weights = weights or empty_weights()
    locks = set(locks or ())
    filled = []
    # v3.22.2：记下"这一轮由预设**显式写入**的字段"。`sanitize()` 靠它判优先级：
    # 显式指令 > 随机值，冲突时让步的必须是随机那一侧（见 sanitize 里的注释）。
    written = []
    # ⚠️ v4.2.0：**被选项权重 3.0 点名的字段，场景预设不得覆盖**。
    #    实测踩到（`%_00045_/_00046_.png`）：用户把「情绪 = 哭泣」调到 3.0，
    #    引擎确实抽到了 `crying`，紧接着套用的 `sc_pose_wariza` 预设写死了
    #    `emotion = smile` —— 直接盖掉，正文里只剩 smile，用户看到的是
    #    "权重 3 又没生效"。优先级与 locks 同级：**显式指令 > 预设**。
    _forced_keys = {f[0] for f in (state.get("_forced_tags") or ())}
    # v4.3.0：被调过权重（中间值）的字段 —— 场景覆盖它们时要如实说明
    _tuned_keys = tuned_weight_fields(weights)
    # ── v4.4.2：场景级自然语言（条目可选键 `scene_nl`）────────────────
    # 为什么单独一个键：`scene_nl` 不是词库字段，`field_by_key()` 认不出来，
    # 下面的字段循环会直接跳过它；这里取出来放进下划线键，由
    # `_collect_with_feat` 拼进 tail_extra（不过 cap、不过互斥、不进随机池）。
    # 没写 `scene_nl` 的条目一个字节都不加 —— 261 条里绝大多数保持原样。
    _scene_nl = str(p.get("scene_nl") or "").strip()
    if _scene_nl:
        state["_scene_nl"] = _scene_nl
    # ⚠️ v4.2.0（用户口径）：**场景档位决定「表情谁说了算」**
    #    · L0 / L1：权重能生效 —— 用户把「哭泣」点到 3.0，场景写的 smile 让路；
    #    · L2 及以上：场景表情必出、场景优先 —— 成人向正戏的表情和动作是绑死
    #      的（触手要崩坏、口交要含住），被权重改成哭会直接毁掉场景立意。
    #    只对 `EXPRESSION_FIELDS`（情绪 / 嘴部 / 眼神）生效；其它字段
    #    （发色/服装/姿势…）仍然是"用户点名必现 > 场景预设"。
    _sc_lv = canon_level(preset_id)
    _expr_scene_wins = _sc_lv >= 2
    _overrode = []
    for k, v in p.items():
        _sec, fld = field_by_key(k)
        if not fld:
            continue
        if not v:
            continue
        # ⚠️ n_extra 由下面**专门的**分支处理（逐标签判档）。
        #    这里必须跳过：它的值是一整串附加标签，走 `_field_ok_under` 会被
        #    所在段的段下限（canon = 2）误挡，或者原样整串写进去再做一次过滤 ——
        #    两条路一起走只是浪费，还容易让"到底谁说了算"变糊。
        if k == "n_extra":
            continue
        if k in locks:
            continue
        if k in _forced_keys:
            # v4.2.0：L2+ 场景的表情压过权重点名（见上面 _expr_scene_wins 的说明）
            if not (k in EXPRESSION_FIELDS and _expr_scene_wins):
                continue
            _overrode.append(k)
        if blocked_by_weight(weights, k):
            continue
        # ⚠️ v4.3.0：**选项级权重 0 = 这一项永不出现**。场景 `tags` 是写死的，
        #    以前它绕过选项权重直接写进去 —— 实测 `missionary = 0` 仍有 2/40
        #    出现，全部来自场景预设。这里先把门关上（最终 state 还有一道 sweep）。
        if option_weight(weights, k, str(v)) <= 0.0:
            filled.append("%s 让路（该选项权重 0）" % fld.get("lab", k))
            continue
        if _field_ok_under(k, v, lv):
            state[k] = v
            written.append(k)
            filled.append(fld.get("lab", k))
            # v4.3.0：中间值（≠1.0）的字段被场景写死时，如实说明 ——
            # 否则用户只会看到"我调了 2.0 却没差别"，不知道是场景说了算。
            if k in _tuned_keys:
                filled.append("%s 由场景决定（选项权重本次未参与）"
                              % fld.get("lab", k))
    # ── 候选随机字段（v3.22.0）───────────────────────────────────────
    # 条目可以在 `random_fields` 里给某字段列几个候选，套用时**抽一个** ——
    # 这样"口交特写"每次的口部状态与表情会变，而不是一条死配方。
    # ⚠️ 三条必须守住的：
    #   ① 候选照样过档位闸门（`_field_ok_under`），不许变成绕过档位的后门；
    #   ② 锁定 / 权重 0 的字段一样跳过（与 tags 里的写死值同一条判定）；
    #   ③ **没有候选就一次 rng 都不消耗** —— 否则所有旧条目的随机序列都会漂移，
    #      零漂移对拍（check_v3_near ④）会全红。
    # ── 人数钉死（v3.22.1）───────────────────────────────────────────
    # `count_lock` 以前**只有前端**在特写页用过（`countBagFor`），普通场景完全没接 ——
    # 于是「双人正戏」抽到 `2girls, yuri`、「自慰」抽到多人，而引擎在多人模式下会
    # 清空单人向的 `n_act` / `n_pos` / `n_theme`（规则本身没错），结果是场景的立意
    # 整段消失：实测 158 条里 21 条中招（肛交→群交、单人自慰→双人）。
    # 这里把声明接到引擎：套用场景时先把人数定下来，后面的人数清空分支就不会误伤。
    sc_rec = scene_index().get(str(preset_id or "")) or {}
    lock_count = str(sc_rec.get("count_lock") or "").strip()
    # ⚠️ v4.2.1：**点名的性行为压过场景的人数固定值**。
    #    实测链路：用户点名「正常位 = 3」→ 循环里人数被抬到
    #    `1girl, 1boy, hetero` → 场景 `count_lock` 又写回 `1girl, solo`
    #    → 末尾「人数相容」把 n_act 清空 → 用户看到"3.0 又没生效"。
    #    显式 > 预设：场景的 solo 声明让路（场景其余字段照常生效）。
    _need = forced_sex_count_target(weights)
    _lock_conflict = bool(lock_count) and not count_satisfies(lock_count, _need)
    if lock_count and "count" not in locks \
            and "count" not in _forced_keys \
            and not _lock_conflict \
            and not blocked_by_weight(weights, "count") \
            and count_lock_level(lock_count) <= lv:
        state["count"] = lock_count
        written.append("count")
        filled.append("人数·" + lock_count)
    elif lock_count and _lock_conflict:
        filled.append("人数让路（点名的性行为需要 `%s`，未写 %s）"
                      % (_need, lock_count))

    # ── 闭麦字段（v3.22.1：从"只在前端"扩到引擎）────────────────────
    # `drop_fields` 原本只在特写页的 `slimFieldsFor` 里生效（那一段只处理 manual_only
    # 条目），普通场景点「套用」根本不经过它 —— 于是「别把 device 抽成自拍」这类声明
    # 在普通场景里等于没写。这里在引擎侧兜一层：把已经抽到的值撤回。
    for _k in (sc_rec.get("drop_fields") or []):
        if _k in SCENE_FORBIDDEN_FIELDS or _k in locks:
            continue
        # ⚠️ v4.1.1：被"选项权重 3.0"点名的字段，场景的闭麦声明不该把它撤掉
        #    （显式 > 预设，与 locks 同级）。否则"我点了紫色发 + 套个场景"
        #    就成了没有发色，用户只会认为权重又坏了。
        #    判据用 `_forced_tags`（只在「不指定角色」这条路才会被写）。
        if _k in {f[0] for f in (state.get("_forced_tags") or ())}:
            continue
        if state.get(_k):
            state[_k] = ""
            filled.append("闭麦·" + _k)

    picked = {}
    for k, cands in scene_random_fields(preset_id).items():
        _sec, fld = field_by_key(k)
        if not fld or k in locks or blocked_by_weight(weights, k):
            continue
        ok = [v for v in cands if _field_ok_under(k, v, lv)]
        if not ok:
            continue
        v = ok[min(int(rng.random() * len(ok)), len(ok) - 1)] if rng else ok[0]
        state[k] = v
        written.append(k)
        picked[k] = ok
        zh = next((z for z, e in (fld.get("opts") or []) if e == v), v)
        filled.append(f"{fld.get('lab', k)}·{zh}")

    # ⚠️ 随机字段**彼此**也可能打架 —— 两个独立抽签会把「惊讶 + 咬唇」这种
    #    矛盾组合抽出来，而 `sanitize()` 遇到它只会把整字段清空（实测 12 抽里 2 次
    #    mouth 直接没了，画面就少了口部状态）。所以抽完再按联动表校验一轮：
    #    被禁的**重抽**（只从没被禁的候选里挑），而不是留给 sanitize 去清空。
    for k, ok in picked.items():
        if k not in state or not state.get(k):
            continue
        banned = L.bans_for(state, k)
        if state[k] not in banned:
            continue
        alt = [c for c in ok if c not in banned]
        fld = field_by_key(k)[1] or {}
        if alt:
            state[k] = alt[min(int(rng.random() * len(alt)), len(alt) - 1)] if rng else alt[0]
            zh = next((z for z, e in (fld.get("opts") or []) if e == state[k]), state[k])
            filled.append(f"{fld.get('lab', k)}→{zh}（避开联动矛盾）")
        else:
            state[k] = ""
            filled.append(f"{fld.get('lab', k)}（候选与本轮其它字段全冲突，未写入）")

    # n_extra 是预设自带的补充标签串，逐标签判档后保留合规部分
    extra = p.get("n_extra")
    # ── 候选构图变体（v3.24.0）───────────────────────────────────────
    # 抽一条变体，把它的短语**并在 extra 前面一起**过档位闸门 ——
    # 与 n_extra 同一个入口，所以"变体里写了一个高档词"照样会被 cut 掉。
    variant_zh = ""
    _pools = scene_random_variants(preset_id)
    if _pools and "n_extra" not in locks and not blocked_by_weight(weights, "n_extra"):
        # ⚠️ 抽哪个变体由 draw() **一次定好**再传进来（v3.24.1 的教训）：
        #    不能让每次尝试自己抽 —— draw() 会跑最多 8 轮密度二分并取"最接近靶心"的一轮，
        #    而不同变体的标签数不同 → 得分系统性偏向一侧，实测 150 抽里 149 次同一侧。
        # v3.27.0：多池，每池各自抽一条；每池的随机数按 (seed, 池名) 派生，
        #    所以"姿势池"与"道具池"互不干扰，也不跟尝试轮次挂钩。
        _names = []
        # v4.4.5：变体的自然语言。为什么两种键：
        #   · `nl` 覆盖场景 `scene_nl` —— 变体描述的是**主维度**（哪种姿势 / 哪种器械 /
        #     舌头什么形态），与场景那句是并列关系，两句同时进正文会互相打架
        #     （"舌头伸出、唾液拉丝" + "舌上卷、张口" → 模型只会画一个，另一句变噪声）。
        #   · `nl_extra` 追加 —— 变体是**副维度**（手里拿什么 / 手指在做什么 / 触手碰哪），
        #     与场景那句不冲突，两句都要。
        _v_nl, _v_add = "", []
        for _p in _pools:
            _items = _p["items"]
            if variant_seed is not None:
                _x = random.Random(derive_seed(int(variant_seed),
                                              "variant:" + str(_p["key"]), 0)).random()
            elif variant_pick is not None:
                _x = float(variant_pick)
            else:
                _x = rng.random() if rng else 0.0
            _i = min(int(_x * len(_items)), len(_items) - 1)
            _it = _items[_i]
            _names.append(_it["zh"])
            extra = f"{extra}, {_it['extra']}" if extra else _it["extra"]
            if _it.get("nl"):
                _v_nl = _it["nl"]
            if _it.get("nl_extra"):
                _v_add.append(_it["nl_extra"])
        variant_zh = " / ".join(_names)
        if _v_nl or _v_add:
            _base = _v_nl or str(state.get("_scene_nl") or "").strip()
            _joined = " ".join([x for x in [_base] + _v_add if x])
            if _joined:
                state["_scene_nl"] = _joined
    if extra and "n_extra" not in locks and not blocked_by_weight(weights, "n_extra"):
        kept, removed = split_by_level(extra, lv)
        if kept:
            state["n_extra"] = kept
            # ⚠️ v3.41.2：必须登记进 `_preset_fields` —— 它是"场景显式写入"的清单，
            #    `trim_to_cap` 靠它决定"先削谁"。不登记的话，标签一超上限
            #    （`n_extra` 在 `free` 槽、优先级低）就会被削掉：
            #    实测「舌与唾液」20 抽里有 6 张的 `tongue` 被 trim 吃掉。
            written.append("n_extra")
            filled.append("场景补充")
        if removed:
            state["_n_extra_removed"] = removed
    if variant_zh:
        # 记下本轮抽中的变体名：报告里要显示（"构图·我躺着"），面板也能回读
        state["_scene_variant"] = variant_zh
        filled.append("构图·" + variant_zh)
    # ── 禁项（v3.38.0：`ban_tags`）───────────────────────────────────
    # 条目可以点名"这几个标签在本条里绝对不要"，抽到了就地作废。
    # 为什么不能只靠"不写进 tags"：字段不写 = 权重仍是 1.0 = 照样随机抽 ——
    # 姿势场景里 `own hands clasped` / `steepled fingers` / `clenched hands`
    # 这三个"双手合拢"类动作会从 `arms_hands` 的随机抽签漏进画面
    # （实测 27 条未声明手部的姿势场景，每条 5–8%，全库 16%）。
    # 放在**最后**扫一遍：tags 写死的、random_fields 抽到的、变体拼进去的都能盖住。
    # ⚠️ 只清值、不重抽 —— 与 `drop_fields` 同一条纪律，rng 消耗保持不变。
    _hit = apply_bans(state, preset_id)
    for _k in _hit:
        filled.append("禁项·" + _k)
    state["_preset_fields"] = written
    state["n_canon"] = preset_id
    # v4.2.0：被场景表情盖掉的"用户点名必现"字段 —— 报告里要如实说明
    # （否则用户看到"按选项权重 3 强制出现：哭泣"却找不到哭泣，比不写更糟）
    if _overrode:
        state["_preset_overrode_forced"] = _overrode
    return filled


def apply_bans(state: dict, preset_id: str) -> list:
    """把场景 `ban_tags` 点名的值从 state 里清掉，返回被清的字段 key 列表。

    v3.38.3：抽成独立函数并在**流程末尾再调一次** —— 原先只在 `apply_preset` 内部扫，
    而 `breast_size`（胸部大小下拉）是在 apply_preset **之后**才写 `st["figure"]` 的，
    于是「口交特写」被用户设成「巨乳」时，`huge breasts` 会绕过禁项进正文，
    表现就是"只要脸和生殖器的特写里出现了胸部"。
    """
    sc = scene_index().get(str(preset_id or "")) or {}
    bans = set(sc.get("ban_tags") or ())
    if not bans:
        return []
    hit = []
    for k, v in list(state.items()):
        if k.startswith("_") or not v:
            continue
        if str(v).strip() in bans:
            state[k] = ""
            hit.append(k)
    return hit


def _field_ok_under(field_key: str, en: str, lv: int) -> bool:
    """预设要写的某个值，在本档位下是否允许。"""
    if field_key == "n_canon":
        return True
    n = option_level(field_key, en)
    return n <= lv


def split_by_level(text: str, lv: int):
    """把一段逗号串按档位切开。返回 (保留, 剔除)。"""
    keep, removed = [], []
    for t in str(text or "").split(","):
        t = t.strip()
        if not t:
            continue
        n = int(N.grade_nsfw([t])["num"])
        (keep if n <= lv else removed).append(t)
    return ", ".join(keep), removed


def trim_to_cap(state: dict, cap: int, weights: dict) -> list:
    """标签数超限时按优先级削，返回被清空的字段 key 列表。

    优先级 = 段权重 × 字段权重，**低的先削**；同权重时源脚本 dropList 里
    列过的软字段先削。

    为什么不能照抄源脚本的固定 dropList：
        源脚本只从 DROP_ORDER 那 8 个字段里削。独图（29 上限）下，
        那 8 个字段往往一个都没被抽中，于是「削到上限」变成一句空话，
        抽出来 33 个标签，自检 F1 直接红。这是源脚本的一个真 bug。
        这里改成「权重最低的先削，DROP_ORDER 只在同权重时决定先后」。

    永不削的字段：人数（地基）、角色（削了就不是这个人了）、
                 法典预设 id、以及两个手写文本框（用户自己的话）。
                 外加 **v3.9.0：预设自己写进去的那些字段** ——
                 用户点了「一键设置场景搭配」，那些字段就是显式指令，
                 被密度裁剪削掉一半等于"选了场景却少了一半"。
    """
    def count_of(v):
        return len([t for t in str(v).split(",") if t.strip()])

    def uniq_est():
        """**去重后**的标签数 —— 与 collect() 的产出同口径。

        ⚠️ v3.38.0 修：以前这里是 `sum(count_of(v))`，把各字段里重复出现的
        同一个标签重复计数。est 高估 → 明明只有 20 个唯一标签也会被判定"超 29"
        → 连环削掉 4-5 个字段 → 最终只剩 15 个，自检 F1 直接跌破下界。
        实测 sfw/extreme 档 200 抽里各漏 7~10 次，全是"差 1 个"。
        """
        seen = set()
        for k, v in state.items():
            if str(k).startswith("_"):
                continue
            for t in str(v or "").split(","):
                t = t.strip().lower()
                if t:
                    seen.add(t)
        return len(seen)

    est = uniq_est()
    if est <= cap:
        return []

    protected = {"count", "role", "n_canon", "nl", "n_extra"}
    # 预设（法典 / 场景搭配）写进去的字段：显式指令，不参与裁剪
    preset_id = state.get("n_canon") or ""
    if preset_id:
        protected |= set(canon().get(preset_id, {}).keys())
    # ⚠️ v3.34.1：`random_fields` / `count_lock` / 构图变体写进去的字段同样要保护。
    #    `canon()` 里的场景预设只有 `tags` + `n_extra`，**不含 random_fields** ——
    #    于是「景别 / 姿势 / 景深 / 眼神」这些由 random_fields 抽出来的字段
    #    会被当成可削项删掉。实测 seed 4 的「景别·全身」就这样被削没了，
    #    正文里只有 `depth of field, blurry background` 没有 `full body`，
    #    用户看到的是"选了全身照却出特写"。
    #    `_preset_fields` 正是 apply_preset 记下的"这一轮由预设显式写入"的字段清单。
    protected |= set(state.get("_preset_fields") or ())
    # ⚠️ v3.42.19：节点控件「胸部大小」直写的 figure 不参与裁剪。
    #    breast_size / big_breasts 开关在 apply_preset **之后**写 st["figure"]
    #    （+ _breast_size / _big_breasts 标记），figure 既不是 _preset_fields
    #    也无人给它顶权重 → 超限时被当普通低权重字段削掉。
    #    实测：贞操带场景（extra 15 词）标签数爆 cap，figure 权重 1.0
    #    排在 candidates 前列被削 —— 用户改了胸部大小，positive 里却没有
    #    对应标签，表现为"未生效"（PNG dropped 里能看到 figure）。
    #    显式指定 = 最高优先级指令，与角色发色瞳色同级保护。
    if state.get("_breast_size") or state.get("_big_breasts"):
        protected |= {"figure"}
    # ⚠️ v3.45.0：角色发型对齐写入的三项同样不参与裁剪 —— 与发色/瞳色同级。
    #    漏了这一步，标签数爆 cap 时发型会被当低权重字段削掉，
    #    表现为"勾了发型对齐，正文里却没有发型"。
    if state.get("_hair_style_applied"):
        protected |= set(HAIR_STYLE_FIELDS)
    # 绑定了角色就必须留住发色/瞳色 —— 这是自检 F10 的硬要求，
    # 削掉了 FR 会红，而且模型会自己乱猜发色，比少一个道具严重得多。
    if state.get("role"):
        protected |= {"hair_color", "eyes_color"}
    # ⚠️ v4.1.1：**选项权重点到 3.0** 的字段同样不参与密度裁剪。
    #    用户把「浅棕发」「女仆头饰」点到 3.0 = "这张必须有"，被密度裁剪
    #    削掉等于选择失效 —— 实测 `%_00033_.png` 的 `maid headdress`
    #    就是这么消失的（drawn 里有、正文里没有）。
    #    判据用 `_forced_tags`（只在「不指定角色」那条路才会被写），
    #    而不是扫全表找权重 3.0 —— 有角色时人物特征归角色官方管，
    #    不能因为"随机抽中了那个 3.0 选项"就多给一层特权。
    for _k, _zh, _en in (state.get("_forced_tags") or ()):
        protected.add(_k)
    # ⚠️ v4.2.2：「双女正戏补穿戴式道具」补上来的 n_theme 同样不参与裁剪 ——
    #    规则说了"使用穿戴式道具"，被密度削掉等于没生效。
    if state.get("_toy_rule"):
        protected |= {"n_theme"}
    softness = {k: i for i, k in enumerate(DROP_ORDER)}
    candidates = [k for k, v in state.items()
                  if v and not str(k).startswith("_") and k not in protected]
    # 排序键：先看权重（低→高），再看是不是源脚本点名的软字段，最后按字段出现顺序
    candidates.sort(key=lambda k: (field_weight(weights, k),
                                   0 if k in softness else 1))

    dropped = []
    for k in candidates:
        if est <= cap:
            break
        dropped.append(k)
        state[k] = ""
        # 去重计数随削随算，不能再用 count_of 递减（那会把重复标签也减掉）
        est = uniq_est()
    return dropped


# ============================================================================
# 八、拼接与中文摘要
# ============================================================================


# 「填充器」型字段：它们在 state 里**有值**，但那个值是**预设 id**，
# 不是标签 —— 绝不能当作标签拼进正文。
#
# ⚠️⚠️ 这是一个 v3.0.0 起就存在的真 bug（v3.8.0 才发现并修）：
#    `FIELD_SLOT["n_canon"] = "free"` 让 collect() 把 `c8` 这种 id 当成标签
#    输出。实测 600 次 explicit/hardcore/extreme 抽样里 **195 次（32.5%）**
#    正文末尾带着一个 `c1`…`c14` 的垃圾 token —— 它进 CLIP 是纯噪声，
#    而且会让"标签数"虚高、让 F11/F12 之外的自检看不出问题。
#    更糟的是：v3.8.0 的 repair() 会把 `s_xxx` 这种带下划线的场景 id
#    "归一"成 `s xxx`（格式修复对标签是对的，对 id 就是破坏）——
#    所以在 collect 这一层就不让它们进正文，是最干净的位置。
#
# ⚠️ 只跳过**输出**，不跳过字段本身：n_canon 仍然参与抽取、仍然受档位闸门、
#    仍然由 apply_preset() 展开成真正的标签。
_NO_EMIT_FIELDS = frozenset({"n_canon"})


def collect(state: dict, tail_extra: str = "", body_extra: str = "") -> list:
    """按 ANIMA3 槽位序收集标签：去重保序、小写规范、互斥消解。

    `body_extra`（v5.0.13）与 `tail_extra` 的区别只在**位置**：
      · body_extra —— 拼在标签区**末尾**、自然语言句之前。角色服装预设走这里。
      · tail_extra —— 拼在最末尾，装的是自然语言整句（场景描述 / 外貌绑定句）
        与手写补充（`extra_body`）。
    为什么要分开：Anima 官方顺序是 `[tags][自然语言]`，标签落到句子后面会被
    T5 当成散文的一部分，权重明显变弱 —— 实测蒂法的预设服装
    （`white tank top` / `black skirt` / `suspenders`）被排在场景句之后，
    出图里那件白背心经常只剩"一件浅色上衣"。
    """
    buckets = {s: [] for s in SLOTS}
    role_out = []
    for key, val in state.items():
        if str(key).startswith("_"):
            continue
        if key in _NO_EMIT_FIELDS:
            continue
        v = str(val or "").strip()
        if not v:
            continue
        slot = FIELD_SLOT.get(key)
        if not slot:
            continue
        if key == "role":
            # ⚠️ v5.0.11：角色改由 `role_output_tags()` 产出 **Anima 官方写法**
            #    （`texas, arknights` —— 角色与作品两个独立 tag、空格不用下划线）。
            #    这里把整串塞进 character 桶，下面的拍平阶段按逗号拆开，
            #    拆出来的顺序是「角色 → 作品」，正好对上官方
            #    `[1girl] [character] [series]` 的槽位顺序。
            #    `state["role"]` 本身仍然保持 trigger 不动：外貌 / 发型 / 服装 /
            #    特征四张表都拿它当 key 查表，改了会全部查空。
            role_out = role_output_tags(state)
            buckets[slot].append(", ".join(role_out) if role_out else v)
            continue
        buckets[slot].append(v)

    ordered = []
    for s in SLOTS:
        ordered.extend(buckets[s])

    # 拍平 → 去重保序 → 小写 → 互斥
    seen = set()
    flat = []
    for chunk in ordered:
        for t in chunk.split(","):
            t = t.strip().lower()
            if not t or t in seen:
                continue
            seen.add(t)
            flat.append(t)

    # 显式写入的标签（预设/场景）在互斥里享有优先权 —— 见 apply_mutex 的说明
    # ⚠️ v5.0.11：**角色 tag（含作品名）也算显式指令**，必须并进 keep。
    #    `apply_mutex` 用 `re.search` 做子串匹配，形态角色会撞上服装互斥组 ——
    #    实测 `asuna (bunny)` 含 `bunny`，不加保护会被「兔耳 vs 日常服」剔掉。
    #    映射表里是 `角色, 作品` 两段式，这里按逗号拆开逐个登记。
    _keep = set()
    for _chunk in role_out:
        for _t in str(_chunk or "").split(","):
            _t = _t.strip().lower()
            if _t:
                _keep.add(_t)
    for _k in (state.get("_preset_fields") or ()):
        for _t in str(state.get(_k) or "").split(","):
            _t = _t.strip().lower()
            if _t:
                _keep.add(_t)
    # ⚠️ v4.1.1：**选项权重点到 3.0** 的标签同样是显式指令，与场景写死的同级。
    #    漏了这条，互斥组会把用户点名的标签当"随机值"剔掉
    #    （例：`short hair × twintails` 里点名短发，束法那条会连坐）。
    for _k, _zh, _en in (state.get("_forced_tags") or ()):
        _t = str(_en or "").strip().lower()
        if _t:
            _keep.add(_t)
    flat = apply_mutex(flat, keep=_keep)

    # v5.0.13：标签区的补充（角色服装预设）—— 排在自然语言句之前
    _body = str(body_extra or "").strip()
    if _body:
        for t in _body.split(","):
            t = t.strip().lower()
            if t and t not in seen:
                seen.add(t)
                flat.append(t)

    tail = str(tail_extra or "").strip()
    if tail:
        for t in tail.split(","):
            t = t.strip().lower()
            if t and t not in seen:
                seen.add(t)
                flat.append(t)
    return flat


# ============================================================================
# v6.5.0：皮肤干爽过滤（用户 2026-11-07「可以直接选择关闭会增加皮肤水珠的词条」）
#
# ⚠️ 只在**成品 tag 列表**层剔除，不碰抽取 / 随机池 / 档位闸门：
#    · 关闭时零漂移（一次 rng 都不吃，text 逐字节不变）；
#    · 开启时能覆盖全部通道 —— 字段随机、服装库 tags、袜子覆盖项、场景
#      extra / n_extra、body_extra、extra_body 都会被 collect() 拍平成同一条
#      tag 列表，只在这一层拦才拦得住「场景里写死的雨珠/湿皮」。
# ⚠️ 不列入「亮丝星点袜 / 珠光连裤袜」：2026-11-07 跨 4 场景 + 泳装 2 seed
#    共 40 张真机对照确认它们只作用于袜面，不影响裸露皮肤。
# ⚠️ 词条一律写 collect() 拍平后的**单 tag**（原选项里的复合串已按逗号拆开）。
# ============================================================================

SKIN_MOISTURE_GROUPS = {
    "skin":    ("皮肤", ("glistening skin", "damp skin", "wet skin",
                         "oily skin", "raindrops on face")),
    "weather": ("天气与水", ("rain", "puddle", "water drop", "steam",
                            "splashing water", "wet floor", "water surface")),
    "hair":    ("头发", ("wet hair", "damp hair")),
    "cloth":   ("衣物与体液", ("wet clothes", "wet cotton", "wet t-shirt",
                              "sweat")),
    "light":   ("光效", ("caustics",)),
}

# v6.5.1：英文 tag → 中文名（面板「干爽过滤」明细要常驻显示，不能只放 hover）。
# ⚠️ 这里必须覆盖 SKIN_MOISTURE_GROUPS 里**每一条**，缺一条面板就会退回显示英文。
SKIN_MOISTURE_ZH = {
    "glistening skin": "湿润光泽肌",
    "damp skin": "潮湿皮肤",
    "wet skin": "湿皮",
    "oily skin": "油皮",
    "raindrops on face": "脸上雨滴",
    "rain": "雨天",
    "puddle": "水洼",
    "water drop": "水珠",
    "steam": "蒸汽氤氲",
    "splashing water": "溅水",
    "wet floor": "湿地面",
    "water surface": "水面",
    "wet hair": "湿发",
    "damp hair": "潮湿头发",
    "wet clothes": "湿透衣物",
    "wet cotton": "湿棉布",
    "wet t-shirt": "湿T恤",
    "sweat": "香汗淋漓",
    "caustics": "水面焦散",
}


def skin_moisture_items(gid):
    """某个分组的 [(英文 tag, 中文名), ...]，供 /contract 与面板明细。"""
    tags = SKIN_MOISTURE_GROUPS.get(gid, ("", ()))[1]
    return [(t, SKIN_MOISTURE_ZH.get(t, t)) for t in tags]


# 流泪白名单：命中任一条 → 放行 `glistening skin`
# （场景 sc_near_face_tears「泪光特写」用 skin_detail=glistening skin 表现泪光，
#   一刀切会误伤。用户口径「流泪情况不算」。）
SKIN_TEAR_MARKERS = frozenset((
    "tears", "crying", "tear streak", "damp cheek", "glistening eyes",
    "watery eyes", "catchlight",
))
SKIN_TEAR_KEEP = frozenset(("glistening skin",))


def skin_moisture_group_options():
    """给前端 / 夹具用：[(组 id, 中文名, [英文 tag ...]), ...]。"""
    return [(k, zh, list(tags)) for k, (zh, tags) in
            SKIN_MOISTURE_GROUPS.items()]


SKIN_MOISTURE_ALL_TAGS = frozenset(
    t for _zh, tags in SKIN_MOISTURE_GROUPS.values() for t in tags)


def skin_moisture_ban_set(groups=None, only_tags=None) -> set:
    """要剔除的词条集合。

    v6.5.2 逐条模式：`only_tags` **优先于** groups
      · "all"        → 全部词条
      · "none"       → 显式空集（一条都不关；不能用空串，空串是回落语义）
      · 其它非空串  → 逗号分隔的英文 tag，只关这些（词表外的静默忽略）
      · "" / None   → **回落 groups 组语义**（老工作流零回归）

    groups 为空 / None / 含 all = 全部组。
    """
    raw = str(only_tags or "").strip().lower()
    if raw == "all":
        return set(SKIN_MOISTURE_ALL_TAGS)
    if raw == "none":
        return set()
    if raw:
        wanted = {s.strip().lower() for s in raw.split(",") if s.strip()}
        # 只认词表里的 tag：手填拼错不会误伤任意标签
        return wanted & set(SKIN_MOISTURE_ALL_TAGS)
    ids = {s.strip().lower() for s in str(groups or "").split(",")
           if s.strip()}
    if not ids or "all" in ids:
        ids = set(SKIN_MOISTURE_GROUPS)
    ban = set()
    for k in ids:
        if k in SKIN_MOISTURE_GROUPS:
            ban.update(t.strip().lower()
                       for t in SKIN_MOISTURE_GROUPS[k][1])
    return ban


def strip_skin_moisture(tags, groups=None, only_tags=None):
    """成品层剔除致水珠词条 → (新 tag 列表, 被剔除的 tag 列表)。

    纯列表过滤，不消耗随机数、不改 state、不改档位与互斥结果。
    """
    ban = skin_moisture_ban_set(groups, only_tags)
    if not ban:
        return list(tags), []
    flat = [str(t) for t in tags]
    has_tears = any(t.strip().lower() in SKIN_TEAR_MARKERS for t in flat)
    out, removed = [], []
    for t in flat:
        s = t.strip().lower()
        if s in ban and not (has_tears and s in SKIN_TEAR_KEEP):
            removed.append(t)
            continue
        out.append(t)
    return out, removed


def _cn_color(tag, index: dict) -> str:
    """中文摘要的单标签翻译：额外认「颜色 + 款式」合成 tag（v3.19.0）。

    `purple thighhighs` 整串不在词库里 —— 它是运行时合成出来的，
    所以先整串反查，失败再把颜色前缀剥掉、查剩下的款式词。
    """
    key = str(tag).strip().lower()
    zh = index.get(key)
    if zh:
        return zh
    for c in OS.COLOR_TAGS:
        if key.startswith(c + " "):
            rzh = index.get(key[len(c) + 1:])
            if rzh:
                return OS.COLOR_ZH.get(c, c) + rzh
            break
    return str(tag)



def cn_summary(tags) -> str:
    """中文摘要：把英文标签反查回界面中文名（源脚本 renderCnSummary 的移植）。"""
    index = _cn_index()
    return "，".join(_cn_color(t, index) for t in tags)


def _cn_index() -> dict:
    """英文标签 → 界面中文名（源脚本 renderCnSummary 的移植）。

    ⚠️ 必须把角色库并进来：2000 条角色的 trigger 不在 pools.json 里，
       不并的话中文摘要会把角色显示成一长串英文 tag。
    """
    if "cn_index" not in _CACHE:
        idx = {}
        for sec in sections():
            for fld in sec["fields"]:
                for zh, en in fld.get("opts", []):
                    for t in en.split(","):
                        t = t.strip().lower()
                        if t and t not in idx:
                            idx[t] = zh
        for zh, en, _n in _role_options():
            for t in en.split(","):
                t = t.strip().lower()
                if t and t not in idx:
                    idx[t] = zh
        # ⚠️ 人数词的覆盖：源脚本的「多人群交」映射到
        #    `multiple girls, orgy, group sex` 三个标签，于是逐标签反查时
        #    `multiple girls` 会被翻译成「多人群交」——
        #    而我们自动生成的群像标签只有 `multiple girls`（不含 orgy），
        #    摘要里显示成「多人群交」是错的（真机端到端抓到过）。
        #    覆盖成中性词；真的抽到 orgy 时那几个标签自己会显示成「多人群交」。
        #    注意键必须是**单个标签**（索引是按逗号拆开建的），
        #    写 "1girl, solo" 这种组合是永远匹配不到的。
        idx.update({
            "multiple girls": "多人群像",
            "2girls": "双女",
        })
        _CACHE["cn_index"] = idx
    return _CACHE["cn_index"]


# ============================================================================
# 九、自检（F 致命项 + S 风格项）
# ============================================================================


def _option_index() -> list:
    """[(field_key, 选项英文原文, 该选项的标签集合), ...]（缓存）。

    给成品文本**反推字段**用（见 infer_state）。只收带选项的字段；
    role / n_canon 刻意不收 —— 前者的 2000+ 条角色不是"选项"语义，
    后者的值是预设 id（c1…），成品里根本不会出现。
    """
    if "option_index" not in _CACHE:
        out = []
        for s in sections():
            for f in s["fields"]:
                if f["key"] in ("role", "n_canon") or f.get("type") == "text":
                    continue
                for _zh, en in f.get("opts", []):
                    tset = frozenset(
                        t.strip().lower() for t in str(en).split(",") if t.strip())
                    if tset:
                        out.append((f["key"], en, tset))
        # 长选项优先：标签多的选项更有辨识度，先匹配就不容易被短选项抢走
        out.sort(key=lambda x: -len(x[2]))
        _CACHE["option_index"] = out
    return _CACHE["option_index"]


def infer_state(tags) -> dict:
    """从**成品标签串**反推 {字段: 选项}，best-effort。

    判据：某个选项的**全部**标签都在成品里 → 认定该字段取了这个值；
    同一字段有多个候选命中时，取**标签最多**的那个（最有辨识度）。

    ⚠️ 这是给「提示词检查与修复」节点用的**估计**，不是权威：
       成品里没有字段信息，手写补句可能同时命中两个选项。
       所以报告里会标明"字段为反推估计"。
    ⚠️ 对生成的提示词来说这个反推基本是全对的 —— 因为 collect() 就是
       逐字段把选项原值拼进去的（一个选项可能有多个标签，全在）。
    """
    bag = {str(t).strip().lower() for t in (tags or []) if str(t).strip()}
    best = {}
    for fk, en, tset in _option_index():
        if tset <= bag:
            prev = best.get(fk)
            if prev is None or len(tset) > len(prev[1]):
                best[fk] = (en, tset)
    return {fk: en for fk, (en, _t) in best.items()}


def check_text(tags, lv: int):
    """对任意成品文本做一次体检。返回 (反推出来的 state, 体检报告)。

    与生成流程里的 `check()` 是**同一个函数** —— 两条路的结论不会打架。
    区别只在 state：生成流程有真 state，这里靠 infer_state 反推。
    """
    state = infer_state(tags)
    return state, check(tags, state, lv)


def format_check_report(tags, lv, report, fixes=None, level_label_txt="") -> str:
    """把"检查 + 修复"结果排成人类可读的多行文本（检查与修复节点用）。"""
    lines = [
        f"目标档位 : {level_label_txt}（上限 {lv}）",
        f"标签数   : {report['n']}",
        f"体检     : 致命项 {report['ok_f']}/{report['total_f']}"
        f" · 风格项 {report['ok_s']}/{report['total_s']}",
        f"实际落档 : {report['real_level']} {report['real_level_label']}"
        "（校准口径，与抽取准入同一把尺子 · v3.16.0）",
    ]
    if report.get("level_floor_hits"):
        lines.append("词汇校准 : " + "、".join(report["level_floor_hits"]))
    lines.append("")
    if fixes:
        lines.append(f"── 已自动修复 {len(fixes)} 项 ──")
        for f in fixes:
            lines.append("  · " + f)
    else:
        lines.append("── 已自动修复 0 项 ──")
    lines.append("")
    lines.append("── 致命项 ──")
    for c in report["F"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[!!]'} {c['id']} {c['name']} —— {c['note']}")
    lines.append("")
    lines.append("── 风格项 ──")
    for c in report["S"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[??]'} {c['id']} {c['name']} —— {c['note']}")
    lines.append("")
    lines.append("注：字段归属是从成品标签反推的估计（成品里没有字段信息）；"
                 "致命项里的「档位准入 / 槽位映射」因此仅作参考，"
                 "超档与互斥已由自动修复在成品层处理。")
    return "\n".join(lines)


def check(tags, state: dict, lv: int) -> dict:
    """体检。F 项失败 = 这条提示词有问题；S 项失败 = 风格提醒。

    移植自源脚本 checkTags() 的 F1–F14 / S1–S15，并新增三项：
      F15 档位合规（本次新增能力的核心保证）
      F16 槽位顺序（确保字段映射表没漏字段）
      S16 权重生效（调了权重却一条没抽中 → 提示）
    """
    F, S = [], []
    tags = [str(t) for t in tags]
    n = len(tags)

    seen, dup = set(), []
    for t in tags:
        if t in seen:
            dup.append(t)
        seen.add(t)

    count_en = state.get("count", "")
    solo = "solo" in count_en and "multiple" not in count_en
    duo = bool(re.search(r"hetero|yuri|1boy", count_en))
    multi = bool(re.search(r"multiple|orgy|group sex", count_en))
    lo, hi = (16, 30) if solo else (22, 38) if duo else (30, 48) if multi else (16, 42)

    F.append(_c("F1", "标签数量", lo <= n <= hi,
                f"{n} 个（模板要求 {'单人' if solo else '双人' if duo else '复杂'} "
                f"{lo}-{hi}）"))

    bad_q = [t for t in tags if any(b in t for b in
             ("masterpiece", "best quality", "score_9", "score_8",
              "highres", "absurdres"))]
    F.append(_c("F2", "禁词检查", not bad_q,
                "含质量词：" + ",".join(bad_q) if bad_q else "无质量词（工作流自动加前缀）"))

    art = [t for t in tags if t.startswith("@")]
    F.append(_c("F3", "画师名", not art,
                "含画师名：" + ",".join(art) if art else "无画师名"))

    F.append(_c("F4", "光线标签", True,
                "光影区为刻意选用，不判失败"))

    F.append(_c("F5", "重复标签", not dup,
                "重复：" + ",".join(dup[:6]) if dup else "无重复"))

    has_solo = "solo" in count_en
    other = any(x in count_en for x in ("hetero", "1boy", "yuri", "multiple", "orgy"))
    F.append(_c("F6", "数量一致性", not (has_solo and other),
                "solo 与多人标签冲突" if (has_solo and other) else "人数标签自洽"))

    va, shot = state.get("viewpoint", ""), state.get("shot_size", "")
    # ⚠️ 与源脚本对照时注意：源脚本 F7 里还有一条
    #      closeFull = shot.includes('close-up') && shot === 'full body'
    #    它是**恒假**的（一个字符串不可能同时等于 'full body' 又包含
    #    'close-up'）—— 移植时刻意剔除，不是漏项。别在对拍时"补"回去。
    #    同理 va_bad 里的 `va == "from side" and "from behind" in va` 也恒假，
    #    但那是源脚本原样带过来的判据，保留（真值表上等价于只看前半条）。
    va_bad = (("from above" in va and "from below" in va)
              or (va == "from side" and "from behind" in va))
    pov_full = "pov" in va and shot == "full body"
    F.append(_c("F7", "视角互斥", not (va_bad or pov_full),
                "POV 无法看到自身全身" if pov_full
                else "仰拍俯拍/正侧背互斥" if va_bad else "视角组合自洽"))

    blind = state.get("n_theme") == "blindfold"
    heart = any(x in state.get("n_orgasm", "")
                for x in ("heart-shaped pupils", "rolled back eyes"))
    glasses = state.get("accessory") == "glasses"
    F.append(_c("F8", "眼罩互斥", not (blind and (heart or glasses)),
                "蒙眼无法呈现心形瞳/翻白眼" if (blind and heart)
                else "眼罩与眼镜冲突" if (blind and glasses) else "无眼罩冲突"))

    nude = state.get("n_state") == "completely nude"
    ph = state.get("socks") == "pantyhose"
    bare = state.get("shoes") in ("bare feet",) or state.get("socks") == "barefoot"
    F.append(_c("F9", "服装互斥", not ((nude and state.get("outfit")) or (ph and bare)),
                "全裸与具体服装同现" if (nude and state.get("outfit"))
                else "连裤袜与赤足冲突" if (ph and bare) else "服装组合相容"))

    # F10 角色完整性。
    # ⚠️ 语义在 v3.1.0 收窄了：角色库从 221 扩到 2600+ 之后，不可能每个角色
    #    都有核准过的外貌数据。所以判据从「必须绑发色瞳色」改成
    #    「**有**外貌数据就必须绑上；没有数据不算错，但要说明白」。
    #    否则这条会大面积误报，把一个正常结果判成致命错误。
    if not state.get("role"):
        F.append(_c("F10", "角色完整性", True, "未使用角色标签，免检"))
    else:
        mode = state.get("_appearance") or ""
        bound = bool(state.get("hair_color") or state.get("eyes_color"))
        if bound:
            F.append(_c("F10", "角色完整性", True, "角色外观已绑定"))
        elif mode == "nodata":
            F.append(_c("F10", "角色完整性", True,
                        "该角色无外貌数据 —— 发色瞳色交给模型识别"))
        elif mode == "multi":
            F.append(_c("F10", "角色完整性", True,
                        "多位角色，按各自官方特征输出，不做统一对齐"))
        elif mode == "duo":
            # v4.5.0：两位以上走**按人绑定**的整句 —— 共享的发色/瞳色字段
            # 故意留空（平铺标签分不清哪个颜色是谁的），所以"没绑字段"是对的。
            F.append(_c("F10", "角色完整性", True,
                        "多位角色：外貌按人绑定（整句），共享字段已让位"))
        elif mode == "off":
            F.append(_c("F10", "角色完整性", True,
                        "外貌对齐被手动关闭 —— 按随机结果输出"))
        else:
            F.append(_c("F10", "角色完整性", False,
                        "该角色有外貌数据却没绑定发色/瞳色"))

    # 长标签只在「不在词库里」时才算异常。
    # 反例：`dungeon ni deai wo motomeru no wa machigatteiru darou ka`
    # 是《地错》的 danbooru 正式作品 tag，53 字符，完全合法 ——
    # 源脚本按 40 字硬卡，会把它判成致命项，是个假阳性。
    known = _cn_index()
    # v4.4.2：自然语言整句（`term_hints` / 场景 `scene_nl`）不是"超长标签"。
    # v4.5.0：运行时生成的多角色外貌绑定句同属这一类。
    # v5.0.10：角色 tag（`texas_(arknights)` / 形态双层）长度天然偏长，
    # 且带下划线是**合法 danbooru 写法** —— F11 / F12 都要放行，否则每张
    # 带角色的图都会挂两条「致命项」，用户以为生成坏了。
    _role_ok = char_output_tag_set()
    _nl = nl_sentences(state.get("_nl_dynamic"))
    long_tags = [t for t in tags
                 if len(t) > 40 and t not in known and t not in _nl
                 and t not in _role_ok]
    long_ok = [t for t in tags
               if len(t) > 40 and (t in known or t in _role_ok)]
    F.append(_c("F11", "标签长度", not long_tags,
                "超长且不在词库内：" + ",".join(long_tags[:4]) if long_tags
                else (f"{len(long_ok)} 个长标签均为词库内作品名" if long_ok
                      else "长度正常")))

    bad_fmt = [t for t in tags
               if re.match(r"^[A-Z]", t)
               or ("_" in t and "score_" not in t and t not in _role_ok)]
    F.append(_c("F12", "标签格式", not bad_fmt,
                "大写或下划线：" + ",".join(bad_fmt[:6]) if bad_fmt else "全小写正确"))

    stand = bool(re.search(r"standing sex|stand and carry", state.get("n_act", "")))
    lie = bool(re.search(r"lying on stomach|on all fours|prone bone",
                         state.get("n_pos", "")))
    F.append(_c("F14", "体位姿态", not (stand and lie),
                "站立性行为与俯卧体态矛盾" if (stand and lie) else "体位协调"))

    # ── 新增 ──────────────────────────────────────────────────────
    # v3.16.0 口径统一（用户拍板）：报告的「实际落档」改按**校准口径**计算，
    # 与准入闸门（eligible_options → tag_level）同一把尺子。
    #
    # 为什么改：v3.15.0 之前这里用 `grade_nsfw(tags)` 原始口径，而准入层走
    # 校准链（LEVEL_DEMOTE / LEVEL_RAISE 折算），两层不一致 —— 实测
    # 「选 SFW 却显示落档 2（wet hair 误命中，23%）」「选 explicit 却显示
    # 落档 3（paizuri/cum on breasts 有意归入成人层，40%）」，用户看到的
    # 档位数字与所选等级对不上。
    #
    # ⚠️ 判定表 `grade_nsfw` 本身**一字不动**（与造型台逐字对拍的承诺不变，
    #    F17 也仍然按原始口径裁决"真越档"）。变的只是**展示口径**：
    #    tag_level() = max(判定表∩下调折算, 上抬折算)，与准入完全同源。
    # raw_level 保留原始口径，供对拍与排查（不再进主展示行）。
    # v4.4.2：自然语言整句不参与判档（见 nl_sentences()）。
    _nl_real = nl_sentences(state.get("_nl_dynamic"))
    real = max((tag_level(t) for t in tags if t not in _nl_real), default=0)
    raw_level = int(N.grade_nsfw(tags)["num"])

    # F15 档位**准入**：抽出来的每个有档位概念的字段，其值必须 ≤ 目标档。
    #
    # 这是「低档不越界」的硬保证。因为候选池在抽取**之前**就已经按档位
    # 收敛过了（见 eligible_options），所以正常情况下它恒为真 ——
    # 一旦为假，说明有人绕过了闸门（新加的预设 / 上游注入 / 回归），
    # 这正是它值得作为致命项的原因。
    admit_bad = sorted({
        k for k, v in state.items()
        if not str(k).startswith("_") and v and FIELD_SLOT.get(k)
        and field_value_num(k, v) > lv
    })
    F.append(_c("F15", "档位准入", not admit_bad,
                "越档字段：" + ", ".join(admit_bad) if admit_bad
                else f"全部字段都在 {lv} 档以内"))

    # F16 槽位映射：字段表漏字段时，标签会被静默丢掉 —— 必须显式拦住。
    unmapped = sorted(k for k in state
                      if not str(k).startswith("_") and state[k]
                      and k not in FIELD_SLOT)
    F.append(_c("F16", "槽位映射", not unmapped,
                "未映射字段：" + ",".join(unmapped) if unmapped
                else f"{len(FIELD_SLOT)} 字段全部有槽位"))

    # F17 判档一致性：把成品喂回**造型台那套 grade_nsfw**，看它判多少档。
    #
    # 为什么要与 F15 分开：
    #   「选项准入」和「文本判档」是两件事。判定表只看标签、看不到字段，
    #   所以 `wet hair`（头发段，纯发型状态）会被 ^wet 模式判成 2 档 ——
    #   这类误命中登记在 anima_v4_overrides.LEVEL_DEMOTE 里，属于**已登记**。
    #   于是这里分三档处理：
    #     · 判档 ≤ 目标档                        → 通过
    #     · 超档标签全部来自已登记校准项          → 通过，在 S17 里说明
    #     · 出现未登记的超档标签                  → 真越档，致命
    # ⚠️ v4.4.4：自然语言整句（`term_hints` / 场景 `scene_nl`）**不参与判档**。
    #    不排除的话，那句 "she walks along the rainy street in a soaked serafuku…"
    #    会被 `grade_nsfw` 拿关键词判成 1~2 档 → L0 场景的 F17 直接报"未登记超档"，
    #    用户看到一串假红。`nl_sentences()` 是精确集合（人工写过的整句），
    #    不含任何词库标签，排除它不会开越档后门。
    over_tags = sorted({t for t in tags
                        if t not in _nl_real
                        and int(N.grade_nsfw([t])["num"]) > lv})
    calibrated_tags = _calibrated_tags()
    rogue = [t for t in over_tags if t not in calibrated_tags]
    F.append(_c("F17", "判档一致性", not rogue,
                "未登记的超档标签：" + ", ".join(rogue[:6]) if rogue
                else (f"判定表判档 {raw_level} ≤ {lv}" if raw_level <= lv
                      else f"判定表判档 {raw_level} > {lv}，但超档项均为已登记校准项"
                           "（展示口径已按校准折算，见「实际落档」）")))

    # F18 上肢互斥（v3.39.0）：同一双手只能有一个手部动作。
    #
    # 为什么补这一项：`mutex_groups` 六组与 sanitize 的硬规则此前**完全没有**
    # 上肢这一维，抽取期也没人比对三个姿势字段 —— 实测擦边档 14.5%、SFW 档
    # 2.5% 的样本同时出现两条互斥手部动作（`hands on own knees` + `finger to
    # mouth`），而自检 F1–F17 **全部通过**，用户只能从出图（"手长在奇怪的
    # 位置"）才发现。这条把它变成可拦截、可复现、能进报告的判定。
    #
    # 判据只认词库三个字段（`L.HAND_SLOT`），手写 extra 里的自由短语不参与 ——
    # 那是用户显式写的，不该被判成错（"一手撩发一手提裙"可以是故意的）。
    # 判据以 **state** 为首选（只有它分得清"这个标签来自哪个字段"）：
    #   · 三个姿势字段 + `n_state` 的 4 个撞名值 → 占手；
    #   · `n_extra` 里的自由短语（场景 `extra` 写的 `stretching`）→ 也算占手。
    # 只有在 state 里一个手部字段都没有时（`check_text` 给**任意成品文本**体检，
    # state 靠 infer_state 反推），才退回标签串计数 —— 那时归属本就不可知。
    _hand_holders = L.hand_slot_state(state or {})
    _extra_hand = [t.strip() for t in
                   str((state or {}).get("n_extra") or "").split(",")
                   if t.strip() and t.strip() in L.HAND_ALL]
    if _hand_holders or _extra_hand:
        _n_hand = (sum(len(v) for v in _hand_holders.values())
                   + len(_extra_hand))
        if _n_hand > 1:
            _hand_note = "同时出现 %d 条手部动作：%s" % (
                _n_hand,
                "；".join([f"{k}={','.join(sorted(v))}"
                           for k, v in _hand_holders.items()]
                          + ([f"n_extra={','.join(_extra_hand)}"]
                             if _extra_hand else [])))
        else:
            _hand_note = "手部动作唯一：" + next(iter(
                [t for v in _hand_holders.values() for t in v] + _extra_hand))
    else:
        _hand_distinct = sorted({t for t in tags if t in L.HAND_ALL})
        _n_hand = len(_hand_distinct)
        if _n_hand > 1:
            _hand_note = "同时出现 %d 条手部动作：%s" % (
                _n_hand, ",".join(_hand_distinct))
        elif _n_hand == 1:
            _hand_note = "手部动作唯一：" + _hand_distinct[0]
        else:
            _hand_note = "本张无词库内手部动作（免检）"
    F.append(_c("F18", "上肢互斥", _n_hand <= 1, _hand_note))

    # ── 风格项 ────────────────────────────────────────────────────
    gloomy = ("fog", "thunderstorm", "rain", "snow", "night")
    cheerful = ("grin", "smile", "sweet")
    S.append(_c("S1", "情绪与氛围",
                not (state.get("emotion") and state.get("weather") in gloomy
                     and state.get("emotion") in cheerful),
                "开朗表情配阴郁天气，注意色彩情绪一致"))

    lewd = ("ahegao", "fucked silly", "mind break", "orgasm", "tongue out")
    S.append(_c("S2", "身份与姿态",
                not (re.search(r"innocent|pure", state.get("temperament", ""))
                     and any(x in state.get("n_orgasm", "") for x in lewd)),
                "清纯气质配崩坏表情（反差设定除外）"))

    if state.get("role"):
        modern = ("police uniform", "flight attendant uniform", "office lady",
                  "cheerleader uniform")
        ancient = ("miko", "china dress", "kimono")
        conflict = ((state.get("outfit") in modern and re.search(
            r"touhou|frieren|re:zero|vocaloid", state["role"]))
            or (state.get("outfit") in ancient and re.search(
                r"zenless|chainsaw|persona|one-punch", state["role"])))
        S.append(_c("S4", "跨世界观冲突", not conflict,
                    "服装与角色世界观存在冲突" if conflict
                    else "已绑定作品标签：" + state["role"]))
    else:
        S.append(_c("S4", "跨世界观冲突", True, "未使用角色标签，免检"))

    lofi = ("selfie", "webcam footage", "security camera footage")
    S.append(_c("S5", "设备与画质",
                not (state.get("device") in lofi and state.get("dof") in
                     ("focus on face", "depth of field, blurry background")),
                "低画质设备不配专业景深效果"))

    pub = ("classroom", "library", "hallway", "office", "cafe", "street",
           "convenience store", "movie theater", "stairwell", "train interior")
    if lv >= 2:
        bad_scene = (state.get("n_state") == "completely nude"
                     and state.get("place") in pub)
        S.append(_c("S6", "场景与裸露", not bad_scene,
                    "公共场景配全裸档位，建议改半脱或 stealth 描述" if bad_scene
                    else "场景与裸露档位一致"))
    else:
        S.append(_c("S6", "场景与裸露", True, "本档无裸露标签，免检"))

    S.append(_c("S8", "道具位置", True,
                f"道具「{state.get('prop')}」已交代位置" if state.get("prop")
                else "未使用道具"))

    gaze, vp = state.get("eye_gaze", ""), state.get("viewpoint", "")
    S.append(_c("S9", "眼神与机位",
                not ((vp == "from above" and gaze == "looking up")
                     or (vp == "from below" and gaze == "looking down")),
                "视线方向与机位矛盾" if ((vp == "from above" and gaze == "looking up")
                                 or (vp == "from below" and gaze == "looking down"))
                else "眼神方向与机位匹配"))

    if state.get("light_fx"):
        S.append(_c("S12", "光影氛围", True, "光效：" + state["light_fx"]))
    else:
        S.append(_c("S12", "光影氛围", True, "未指定光效（底模已内置）"))

    S.append(_c("S16", "权重生效", True,
                "本次未使用权重（全默认）" if not state.get("_weights_tuned")
                else f"已调 {state['_weights_tuned']} 项权重"))

    if over_tags and not rogue:
        S.append(_c("S17", "已登记校准项", True,
                    f"判档 {real} 高于目标 {lv}，但超档标签 "
                    f"（{', '.join(over_tags[:4])}）全部来自 anima_v4_overrides "
                    f"的 LEVEL_DEMOTE 白名单，属预期"))
    else:
        S.append(_c("S17", "已登记校准项", True, "本次未触发人工校准"))

    ok_f = sum(1 for c in F if c["ok"])
    ok_s = sum(1 for c in S if c["ok"])
    # 词汇校准命中（信息项，不判成败）：这些标签判定表判 0、但逐选项升档表
    # 在字段上下文里确认过档位 —— 报告的"实际落档"用的是判定表口径，
    # 这里把差额单独列出来，免得用户以为两者矛盾。
    floor_hits = sorted({t for t in tags if tag_level(t) > lv})
    return {
        "F": F, "S": S, "n": n, "dup": dup,
        # over_level / over_fields 是**准入**层面的越界，回滚循环看的是它。
        # 刻意不用"文本判档"当回滚依据 —— wet hair 那类误命中会让它死循环。
        "over_level": bool(admit_bad),
        "over_fields": admit_bad,
        "real_level": real,
        "real_level_label": N.NSFW_LABEL[N.NSFW_KEY_OF_NUM[real]],
        # 原始判定表口径（v3.16.0 起仅作排查辅助，不再当"实际落档"展示）。
        "raw_level": raw_level,
        "calibrated_hits": over_tags,
        "rogue": rogue,
        "level_floor_hits": floor_hits,
        "ok_f": ok_f, "total_f": len(F), "ok_s": ok_s, "total_s": len(S),
    }


def _calibrated_tags() -> set:
    """LEVEL_DEMOTE 覆盖到的所有英文标签（把选项串拆成单标签）。

    ⚠️ 刻意**只收 DEMOTE、不收 RAISE**（v3.8.0）：本集合的语义是
       「允许在低档出现、但判档会超档的已登记例外」。RAISE 是**往下钉档位**的，
       它覆盖的标签（pov crotch 等）本来就进不了低档，把它们放进白名单
       等于反过来给 F17 开了一个洞。
    """
    if "calibrated_tags" not in _CACHE:
        bag = set()
        for _fk, en in LEVEL_DEMOTE:
            for t in str(en).split(","):
                t = t.strip().lower()
                if t:
                    bag.add(t)
        _CACHE["calibrated_tags"] = bag
    return _CACHE["calibrated_tags"]


def field_value_num(field_key: str, value: str) -> int:
    """某个字段**当前值**的准入档位 —— 引擎侧唯一的"这个字段算几档"入口。

    ⚠️ 文本字段（`nl` / `n_extra`）必须**按整串逐标签判**，不能走
       `option_level()` —— 那条路会套上所在段的段下限：`n_extra` 属于
       canon 段（下限 2），于是一条 L0 的附加标签串会被判成 2 档。
       v3.9.0 的场景搭配库里 SFW 预设就带 n_extra（`afternoon light, quiet`），
       判错会让它在全年龄档被当成"越档"（测试与自检都会误报）。
    ⚠️ 与 `check()` 的 F15、以及 tests/check_v3_gate.py 的准入层**共用本函数**，
       三处口径必须一致（历史上"两套口径各判各的"出过好几次事）。
    """
    if field_key in ("nl", "n_extra"):
        return int(N.grade_nsfw([t.strip() for t in str(value).split(",")
                                 if t.strip()])["num"])
    return option_level(field_key, value)


def _c(cid, name, ok, note):
    if ok is None:
        ok = True
    return {"id": cid, "name": name, "ok": bool(ok), "note": note}


def format_report(res: dict) -> str:
    """把体检结果排成人类可读的多行文本（节点的 report 输出）。"""
    r = res["report"]
    lines = [
        f"档位   : {res['level_label']}（目标 {res['level_num']}）"
        f" · 实际落档 {r['real_level']} {r['real_level_label']}",
        f"标签数 : {r['n']}",
        f"自检   : 致命项 {r['ok_f']}/{r['total_f']}"
        f" · 风格项 {r['ok_s']}/{r['total_s']}",
        "",
        "── 致命项 ──",
    ]
    for c in r["F"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[!!]'} {c['id']} {c['name']} —— {c['note']}")
    lines.append("")
    lines.append("── 风格项 ──")
    for c in r["S"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[??]'} {c['id']} {c['name']} —— {c['note']}")
    if res.get("dropped"):
        lines.append("")
        lines.append("裁剪掉的字段（超标签上限）：" + "、".join(res["dropped"]))
    if res.get("notes"):
        lines.append("")
        lines.append("── 过程记录 ──")
        for n in res["notes"]:
            lines.append("  · " + n)
    lines.append("")
    lines.append("── 调试：本次抽出的字段与档位 ──")
    for d in r.get("drawn", []):
        lines.append(f"  {d['field']:16s} L{d['level']} w{d['weight']:<5}"
                     f" {d['zh']} = {d['en']}")
    return "\n".join(lines)
