#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给角色服装预设「补官方服装」＋「官方条目加锁」（v6.29.0，用户 2026-11-23）。

用户口径（原话）：
  「角色库角色目前大多只有一套官方预设服装，请为有多套官方服装的角色
    增加其他未增加的官方预设服装。」
  「官方预设服装不支持通过手动删除按钮删除，避免误删。」

────────────────────────────────────────────────────────────────────────
为什么要单独一个工具，而不是直接改 character_outfits.json
────────────────────────────────────────────────────────────────────────
`presets/character_outfits.json` 是**用户数据**（不是构建产物），手改一次，
下次想批量再补就得重新对一遍 id，很容易覆盖掉用户在面板上加的套。
这里把「数据源」和「用户数据」分开：

  工具 `tools/official_outfits_extra.json`（手工维护 / 可重复跑）
        ↓ merge（只 append 缺的 id，绝不改已有条目）
  `presets/character_outfits.json`（用户数据，跑前自动备份）

────────────────────────────────────────────────────────────────────────
两个来源
────────────────────────────────────────────────────────────────────────
① `--auto`：**库里已有的换装版本条目**。
   实证发现角色库本来就把官方换装当成独立角色收录了一份：
     `changli_(wuthering_waves)` + `changli_(laurel_nymph)_(wuthering_waves)`
     `keqing_(genshin_impact)` + `keqing_(opulent_splendor)_(genshin_impact)`
   这些变体条目自带一套服装预设（当年按 safebooru 频率统计出来的），
   把它**继承**成基础角色的一套服装，tags 与来源天然一致，不需要外部联网。
   ⚠️ 变体条目的 tags 里混着身份标签（`blue halo` / `cross hair ornament`）
      和统计噪声，所以过一道 **服装词白名单**；洗完不足 2 条的直接跳过
      （宁可少一套，也不要一套「认不出穿什么」的）。

② `--extra`：**手工数据文件** `official_outfits_extra.json`。
   给「有官方皮肤、但库里没有对应换装角色」的人补（原神/星铁/绝区零/鸣潮等）。
   每套写 id / zh / tags / level / note，工具按同一套规则合并。

────────────────────────────────────────────────────────────────────────
其它开关
────────────────────────────────────────────────────────────────────────
  `--backfill-official`  把现有 id == `default` 的那套标成 `official: true`
                         （面板表现为 🔒，不可删除；只标一次，不重复写盘）
  `--clean-empty`        删掉 tags 为空的脏条目（如 kochiya_sanae.default_2）
  `--dry-run`            只打印将发生的改动，不写盘
  `--check`              只校验：不写盘，返回非 0 表示还有可补的条目

⚠️ 写盘后引擎侧是**文件指纹**感知（nodes._global_state_fingerprint 已含
   OUTFITS_FILE），不需要重启；但前端缓存的 ui.outfits 要重开面板才刷。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
PRESETS = os.path.join(PLUGIN, "presets")
CHARS = os.path.join(PRESETS, "anima_v4_characters.json")
OUTFITS = os.path.join(PRESETS, "character_outfits.json")
EXTRA = os.path.join(HERE, "official_outfits_extra.json")

DROP_ALL = ["outfit", "top", "bottom", "socks", "sock_color", "shoes",
            "accessory", "cloth_color", "cloth_mat", "cloth_state_sfw",
            "cloth_nsfw", "n_fabric", "n_shoes_keep"]

# ── 服装词白名单：只有命中这些词根的 tag 才被认为是「说明穿什么」 ──────────
GARMENT_WORDS = (
    "dress", "skirt", "shirt", "blouse", "jacket", "coat", "cape", "cloak",
    "swimsuit", "bikini", "bodysuit", "leotard", "kimono", "yukata",
    "china dress", "qipao", "hanfu", "uniform", "suit", "vest", "waistcoat",
    "sweater", "cardigan", "hoodie", "parka", "pants", "trousers", "shorts",
    "jeans", "leggings", "thighhighs", "thigh boots", "kneehighs", "pantyhose",
    "stockings", "socks", "boots", "shoes", "heels", "sandals", "loafers",
    "sneakers", "gloves", "mittens", "hat", "cap", "beret", "headdress",
    "ribbon", "bow", "bowtie", "necktie", "scarf", "belt", "straps",
    "suspenders", "apron", "frills", "lace", "transparent", "bare shoulders",
    "off shoulder", "off-shoulder", "backless", "choker", "necklace",
    "earrings", "jewelry", "sunglasses", "glasses", "mask", "veil", "shawl",
    "robe", "gown", "tunic", "camisole", "tube top", "crop top", "sarong",
    "armor", "epaulettes", "strapless", "sleeveless", "twinning", "collar",
    "armband", "bracelet", "armlet", "garter", "halterneck", "one-piece",
    # 修道服 / 制服类（danbooru 的 `habit` 专指修女服，`nun` 是同一套的另一种写法）
    "nun", "habit", "pinafore", "corset", "bodice", "bloomers", "pajamas",
    "nightgown", "tracksuit", "jersey", "jumpsuit", "overalls", "wetsuit",
    # 头饰 / 配件类（皮肤经常靠这些认人：皇冠、面纱、翅膀、扇子、伞）
    "crown", "tiara", "wing", "fan", "parasol", "veil", "hairpin", "hair stick",
    "lingerie", "babydoll", "negligee", "kigurumi", "jersey", "sarong",
)

# ── 变体限定词里**不是服装**的（形态 / 性别 / 职业形态），不生成服装 ────────
NOT_OUTFIT = {
    "female", "male", "young", "adult", "small", "child", "robot", "human",
    "blade", "headspace", "puppet", "demiurge", "philia093", "ostina",
    "breakdown", "illumining_form", "moon_fox", "bird", "exostrider_armament",
    "ruler", "lancer", "lancer_alter", "avenger", "rider", "caster",
    "assassin", "mooncancer", "foreigner", "elite_ii", "fate",
    # 画风差分 / 形态 / 声库，不是换装
    "pc-98", "cat", "ghost", "sv", "work", "young_", "kid",
}

# ── 变体限定词 → 中文皮肤名（没有 · 后缀时用；只补常用的） ────────────────
QUAL_ZH = {
    "swimsuit": "泳装", "bunny": "兔女郎", "maid": "女仆装", "dress": "礼服",
    "track": "运动服", "band": "乐队服", "cheer_squad": "啦啦队服",
    "first_ascension": "一破", "second_ascension": "二破",
    "third_ascension": "三破", "traveling_outfit": "英灵旅装",
    "opulent_splendor": "霓裾翩跹", "laurel_nymph": "桂枝宁芙",
    "peach_blossom": "桃夭灼灼", "splashing_summer": "夏日旖旎",
    "plush_dream": "绒梦", "peppermint": "薄荷甜心",
    "peach_parfait": "白桃芭菲", "exorcistic_adjuration": "叱妖诰",
    "perpetual_spark": "永恒火花", "infernal": "炎狱", "mercenary": "佣兵",
    "xuanling": "玄翎", "student": "学生装", "heavy_arms": "重装",
    "innocent_days": "纯真岁月", "crystal_wave": "水晶浪潮",
    "great_villain": "大反派", "sparkling_summer": "闪耀夏日",
    "escape_queen": "逃亡女王", "blue_ocean": "碧海", "vision_eye": "幻视之眼",
    "winter_slayer": "冬日杀手", "ninja_thief": "忍盗", "aqua_marine": "碧海陆战",
    "ice_rose": "冰玫瑰", "unkind_maid": "冷酷女仆", "frost_ticket": "霜之券",
    "chic_ocean": "时髦海岸", "winter_owner": "冬之主人",
    "wonderland_bunny": "仙境兔女郎", "ordinary_mechanic": "普通机修工",
    "bay_goddess": "海湾女神", "winter_sweets": "冬日甜点",
    "serendipity": "不期而遇", "boom_and_shock": "轰鸣与震撼",
    "ultimate_hero": "究极英雄", "marine_study": "海洋研究",
    "romantic_maid": "浪漫女仆", "snow_buddy": "雪之伙伴",
    "bonding_chain": "束缚之链", "blooming_bunny": "绽放兔女郎",
    "miracle_fairy": "奇迹妖精", "red_hood": "红帽", "pure_grace": "纯恩",
    "winter_shopper": "冬日购物", "bloom_in_summer": "夏日绽放",
    "black_shadow": "暗影", "twinkling_bunny": "闪耀兔女郎",
    "agent_bunny": "特工兔女郎", "innocent_maid": "纯真女仆",
    "fortune_mate": "命定伴侣", "killer_wife": "杀手妻子",
    "silent_track": "无声轨迹", "nova": "启行", "summeretto": "晴歌",
    "lv.999": "LV.999", "miss_pink_elf": "粉色妖精小姐",
    "herrscher_of_human:_ego": "人之律者", "trick_or_treatment": "不给糖就捣蛋",
    "memories_at_trifas": "特里法斯的回忆", "piercing_bunny": "穿刺兔女郎",
    "stunning_speedster": "巅峰时速", "scorching-hot_training": "炙热的网球练习",
    "listless_lapin": "懒懒的白兔", "unfading_smile": "永不褪色的笑容",
    "azure_horizons": "碧波青云", "luxurious_wheels": "奢华座驾",
    "enraptured_companion": "恋慕伴侣", "poolside_coincidence": "泳池边的偶遇",
    "pro_swimmer": "职业泳手", "1st_costume": "首套服装",
    "casual": "便服", "streetwear": "街头装", "append": "Append",
    "school_uniform": "校服", "summer": "夏日装", "sailor": "水手服",
    "silver_soldier": "银色士兵", "dangerous_beast": "危险野兽",
    "herrscher_of_sentience": "识之律者", "silverwing:_n-ex": "银翼 N-EX",
    "swimsuit_foreigner": "泳装", "swimsuit_lancer": "泳装",
    "swimsuit_archer": "泳装", "swimsuit_berserker": "泳装",
    "swimsuit_caster": "泳装", "swimsuit_ruler": "泳装",
    "swimsuit_mooncancer": "泳装", "swimsuit_assassin": "泳装",
    "alter_swimsuit_rider": "泳装", "bride": "新娘",
    "ver._shinjuku_1999": "新宿 1999",
}

# 主件词：一套服装至少得有一条「主体衣装」，否则就是只剩手套 / 项链的残渣
# （实测 `anby_demara_(silver_soldier)` 只统计出两条手套词，出图根本没有衣服）。
CORE_WORDS = (
    "dress", "skirt", "shirt", "blouse", "jacket", "coat", "cape", "cloak",
    "swimsuit", "bikini", "bodysuit", "leotard", "kimono", "yukata", "qipao",
    "china dress", "hanfu", "uniform", "suit", "sweater", "cardigan", "hoodie",
    "pants", "trousers", "shorts", "jeans", "armor", "robe", "gown", "tunic",
    "camisole", "tube top", "crop top", "sailor", "nun", "habit", "apron",
    "lingerie", "babydoll", "negligee", "kigurumi", "leotard", "sarong",
)

STATS = {"added": 0, "skipped_exists": 0, "skipped_thin": 0, "skipped_form": 0}


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def is_garment(tag: str) -> bool:
    t = str(tag).lower()
    return any(w in t for w in GARMENT_WORDS)


def clean_tags(tags):
    out = []
    for t in tags or []:
        t = str(t).strip().lower()
        if not t or t in out:
            continue
        if is_garment(t):
            out.append(t)
    return out


def zh_of(qual: str, var_char: dict, base_char: dict = None) -> str:
    """皮肤中文名：优先用变体角色中文名里的 `·` 后缀，其次查表，最后原样。

    ⚠️ 中文名里的 `·` 不一定分得开「名字 / 皮肤」：`长离·桂冠宁芙` 是皮肤的写法，
       但 `安比·德玛拉` / `噶呜·古拉` / `哈科斯·贝尔丝` 本来就是个双段名字 ——
       把后半截当皮肤名，就会冒出一套叫「德玛拉」「古拉」的衣服。
       判据：后缀出现在**基础角色**的中文名里 → 它是名字的一部分，不能用。
    """
    zh = str((var_char or {}).get("zh") or "")
    base_zh = str((base_char or {}).get("zh") or "")
    if "·" in zh:
        tail = zh.split("·", 1)[1].strip()
        if tail and base_zh and tail in base_zh:
            tail = ""
        # `·` 后缀必须真的是中文皮肤名（`长离·桂冠宁芙` → 桂冠宁芙）。
        # Fate 那批变体的中文名是「阿尔托莉雅·Alter」，后缀是角色差分名，
        # 拿它当皮肤名会变成一套叫「Alter」的衣服 —— 必须挡住。
        if tail and re.search(r"[\u4e00-\u9fff]", tail):
            return tail
    return QUAL_ZH.get(qual, qual.replace("_", " "))


def variant_pairs(chars):
    """[(base_tag, qual, variant_tag)] —— 基础角色 ↔ 它的官方换装条目。

    两种写法都要认（v6.29.0 补第二种）：
      · 带作品后缀 `changli_(wuthering_waves)` ← `changli_(laurel_nymph)_(wuthering_waves)`
      · 无作品后缀 `hatsune_miku`             ← `hatsune_miku_(append)`
      1035 位角色是第二种（东方 / Vocaloid / hololive / EVA …），最早的版本只扫
      「基础 + 作品」二维键，整批漏掉 —— 用户 2026-11-23「感觉新增服装的角色不多」。
    剥括号一律从右往左：最后一层当作品（可能为空），倒数第二层才是限定词。
    """
    tags = {c.get("tag", "") for c in chars}
    parsed = []          # (base, qual, work)
    for t in tags:
        m = re.match(r"^(.*?)_\(([^()]*)\)$", t)
        if not m:
            continue
        head, last = m.group(1), m.group(2)
        m2 = re.match(r"^(.*?)_\(([^()]*)\)$", head)
        if m2:
            base, qual, work = m2.group(1), m2.group(2), last
        else:
            base, qual, work = head, last, ""
        if not base or not qual or ":" in qual:
            continue
        parsed.append((base, qual, work))

    by_base = {}
    for base, qual, work in parsed:
        by_base.setdefault((base, work), []).append(qual)

    pairs = []
    for t in tags:
        m = re.match(r"^(.*?)_\(([^()]*)\)$", t)
        if m and not m.group(1).count("("):
            # 形如 `name_(work)`：只有一层括号才是基础角色
            base, work = m.group(1), m.group(2)
            if not m.group(2).count("(") and ":" not in work:
                key = (base, work)
            else:
                continue
        elif "(" not in t:
            base, work = t, ""
            key = (base, work)
        else:
            continue
        for qual in by_base.get(key, []):
            pairs.append((t, qual, "%s_(%s)%s" % (base, qual, ("_(" + work + ")") if work else "")))
    return sorted(set(pairs))


def build_auto(chars, outfits):
    """从库内换装版本继承服装条目 → {base_tag: [item, ...]}"""
    bytag = {c.get("tag", ""): c for c in chars}
    res = {}
    for base, qual, var in variant_pairs(chars):
        if qual in NOT_OUTFIT:
            STATS["skipped_form"] += 1
            continue
        vrec = outfits.get(var) or {}
        vlist = [x for x in (vrec.get("list") or []) if isinstance(x, dict)]
        if not vlist:
            STATS["skipped_thin"] += 1
            continue
        src = vlist[0]
        tags = clean_tags(src.get("tags"))
        if len(tags) < 2 or not any(any(w in t for w in CORE_WORDS) for t in tags):
            STATS["skipped_thin"] += 1
            continue
        if base == var:
            continue
        res.setdefault(base, []).append({
            "id": qual,
            "zh": zh_of(qual, bytag.get(var), bytag.get(base)),
            "level": int(src.get("level") or 0),
            "tags": tags,
            "fields": {},                       # 一律不写 fields：tags 已经说清楚了
            "drop": list(DROP_ALL),
            "verified": False,                  # 继承来的 tags 没在「基础角色」上实测过
            "official": True,
            "note": "官方换装 · 继承自库内换装条目 `%s`（tags 过服装词白名单，未真机验收）"
                    % var,
        })
    return res


def build_extra(extra_doc):
    res = {}
    for tag, items in (extra_doc.get("outfits") or {}).items():
        lst = []
        for it in items:
            oid = str(it.get("id") or "").strip().lower().replace(" ", "_")
            # ⚠️ 手工条目**不洗**：白名单是给 `--auto` 洗机器继承噪声用的，
            #    人写的时候是有意为之（`high slit` / `japanese clothes` /
            #    `flower hair ornament` 这类词白名单认不出，但确实是这套的特征）。
            tags, seen = [], set()
            for t in it.get("tags") or []:
                t = str(t).strip().lower()
                if t and t not in seen:
                    seen.add(t)
                    tags.append(t)
            if not oid or not tags:
                continue
            lst.append({
                "id": oid,
                "zh": str(it.get("zh") or oid),
                "level": int(it.get("level") or 0),
                "tags": tags,
                "fields": {k: str(v).lower() for k, v in (it.get("fields") or {}).items()},
                "drop": list(DROP_ALL),
                "verified": bool(it.get("verified")),
                "official": True,
                "note": str(it.get("note") or ""),
            })
        if lst:
            res.setdefault(str(tag), []).extend(lst)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="从库内换装版本继承")
    ap.add_argument("--extra", action="store_true", help="合并 official_outfits_extra.json")
    ap.add_argument("--backfill-official", action="store_true",
                    help="把 id==default 的现有条目标成 official（不可删除）")
    ap.add_argument("--clean-empty", action="store_true", help="删掉 tags 为空的脏条目")
    ap.add_argument("--replace", action="store_true",
                    help="同 id 已存在且是 official 时，用 extra 里的版本覆盖（看图修正过的条目）")
    ap.add_argument("--clean-thin", action="store_true",
                    help="删掉「只有配件、没有主体衣装」的官方条目（启用后会闭麦全部服装字段 = 没穿衣服）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if not any((a.auto, a.extra, a.backfill_official, a.clean_empty, a.clean_thin)):
        ap.error("至少要给一个动作：--auto / --extra / --backfill-official / --clean-empty")

    chars = (load_json(CHARS, {}) or {}).get("characters") or []
    doc = load_json(OUTFITS, {}) or {}
    outfits = doc.get("outfits") or {}
    if not isinstance(outfits, dict):
        raise SystemExit("character_outfits.json 的 outfits 不是字典，停止")

    plan = {}
    if a.auto:
        for k, v in build_auto(chars, outfits).items():
            plan.setdefault(k, []).extend(v)
    if a.extra:
        edoc = load_json(EXTRA, {}) or {}
        for k, v in build_extra(edoc).items():
            plan.setdefault(k, []).extend(v)

    changes = []          # [(role, item)]
    for role, items in plan.items():
        bucket = outfits.get(role)
        if not isinstance(bucket, dict):
            bucket = {"enabled": "", "list": []}
            outfits[role] = bucket
        lst = [x for x in (bucket.get("list") or []) if isinstance(x, dict)]
        have = {str(x.get("id") or "") for x in lst}
        for it in items:
            if it["id"] in have:
                if a.replace:
                    for i2, old in enumerate(lst):
                        if str(old.get("id") or "") != it["id"] or not old.get("official"):
                            continue
                        merged = dict(old)
                        merged.update({k: it[k] for k in ("zh", "tags", "level", "note", "verified")})
                        merged["fields"] = it.get("fields") or old.get("fields") or {}
                        lst[i2] = merged
                        changes.append((role, merged))
                        break
                else:
                    STATS["skipped_exists"] += 1
                continue
            lst.append(it)
            have.add(it["id"])
            changes.append((role, it))
        bucket["list"] = lst
        bucket.setdefault("enabled", "")

    # 官方标记回填
    marked = 0
    if a.backfill_official:
        for role, bucket in outfits.items():
            for x in (bucket.get("list") or []):
                if isinstance(x, dict) and str(x.get("id")) == "default" and not x.get("official"):
                    x["official"] = True
                    marked += 1

    # 脏条目（tags 为空）
    removed = []
    if a.clean_empty:
        for role, bucket in list(outfits.items()):
            lst = [x for x in (bucket.get("list") or []) if isinstance(x, dict)]
            keep = [x for x in lst if x.get("tags")]
            for x in lst:
                if not x.get("tags"):
                    removed.append((role, x.get("id")))
            bucket["list"] = keep
            if not keep:
                outfits.pop(role, None)

    # 只有配件、没有主体衣装的官方条目
    thin_removed = []
    if a.clean_thin:
        for role, bucket in list(outfits.items()):
            lst = [x for x in (bucket.get("list") or []) if isinstance(x, dict)]
            keep = []
            for x in lst:
                _tags = list(x.get("tags") or [])
                _has_core = any(any(w in t for w in CORE_WORDS) for t in _tags)
                if x.get("official") and str(x.get("id")) != "default" and not _has_core:
                    # 先试**按 id 补一条主体衣装**（id 通常就写着这套是什么）：
                    # `swimsuit_caster` / `splashing_summer` → swimsuit，
                    # `dress` → dress，`bunny` → playboy bunny，`maid` → maid。
                    # 补不出来的（`serendipity` / `boom_and_shock` 这种纯代号）直接删。
                    _oid = str(x.get("id") or "")
                    _add = None
                    for _kw, _g in (("swimsuit", "swimsuit"), ("swimmer", "swimsuit"),
                                    ("summer", "swimsuit"), ("bunny", "playboy bunny"),
                                    ("maid", "maid"), ("dress", "dress")):
                        if _kw in _oid:
                            _add = _g
                            break
                    if _add and _add not in _tags:
                        x["tags"] = _tags + [_add]
                        keep.append(x)
                        continue
                    thin_removed.append((role, _oid, _tags))
                    continue
                keep.append(x)
            bucket["list"] = keep
            if not keep:
                outfits.pop(role, None)
        print("删除「只有配件」的官方条目：%d 条" % len(thin_removed))
        for r, i, t in thin_removed[:20]:
            print("  - %-38s %-20s %s" % (r, i, t))

    print("将新增 %d 套（涉及 %d 位角色）" % (len(changes), len({c[0] for c in changes})))
    print("  跳过：已存在 %d / tags 太少 %d / 非服装形态 %d"
          % (STATS["skipped_exists"], STATS["skipped_thin"], STATS["skipped_form"]))
    if a.backfill_official:
        print("官方标记回填：%d 条" % marked)
    if a.clean_empty:
        print("删除空 tags 条目：%d 条 %s" % (len(removed), removed[:5]))

    # 抽样打印，便于肉眼确认不是垃圾数据
    for role, it in changes[:12]:
        print("  + %-38s %-22s %s" % (role, it["zh"], ", ".join(it["tags"][:5])))

    if a.check:
        return 0 if not changes else 1
    if a.dry_run:
        print("（dry-run，未写盘）")
        return 0
    if not (changes or marked or removed or thin_removed):
        print("没有改动，不写盘。")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    bak = OUTFITS + ".bak-2026" + stamp[4:]
    shutil.copy2(OUTFITS, bak)
    meta = doc.get("_meta") or {}
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    meta["roles"] = len(outfits)
    meta["outfits"] = sum(len((b.get("list") or [])) for b in outfits.values())
    doc["_meta"] = meta
    doc["outfits"] = outfits
    tmp = OUTFITS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, OUTFITS)
    print("已写盘：%s（备份 %s）" % (os.path.basename(OUTFITS), os.path.basename(bak)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
