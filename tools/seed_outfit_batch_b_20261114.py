# -*- coding: utf-8 -*-
"""一次性种入 14 套高档服装（v6.24.0 的 B 批次，用户 2026-11-14 15:24「做B」）。

为什么是 14 套、且 **L2 一套不加**：
  · L2 已有 21 套（nsfw 13 / swim 5 / hosiery 2 / home 1），而 `cloth_nsfw` 段的
    内容包（n_state 17 / n_exposure 10 / n_fabric 9）在 L2 起**每次必出**——
    再补内衣套只会在正文里和 `see-through` / `latex` 双重描述；
  · 真正的空白是 **L3 缺"成型穿搭"**（现有 11 套里 7 套是缚具/束身/裸围裙）、
    **L4 只有 1 套**（悬吊缚）→ extreme 档等于没得选。

本批分布：L3 七套（皮革束腰 / 半脱衬衫 / 湿身蕾丝 / 束腰吊袜带 / 半透连体泳衣 /
露背浴袍 / 网袜连体）、L4 七套（缚·和服半脱 / 全束具 / 裸围裙·项圈 / 项圈开衩长裙 /
侧系带微比基尼 / 缚·男友衬衫 / 全包乳胶）。

⚠️ **档位口径**：`level` 手写在条目里（`_clean_outfit` 会保留并 clamp 0–4）。
   `check_v3_outfitlib` 的断言是 `level >= max(字段准入, 标签档位)`，手写更高档合法 ——
   本批正是靠这个：像 `latex catsuit` / `apron only` / `leather corset` 这类
   **判定表不认识的词**只有 0–1 档，必须人工标到 L3/L4，否则永远进不了高档池。
   脚本会打印每套的「派生档 / 标注档」，派生 > 标注 就是漏标，直接拒收。

⚠️ 泳装词根（bikini / swimsuit / one-piece）只出现在 `swim` 那两套里 ——
   池化在非泳装锚点会整条剔除它们（`_is_swim_suit`），这是设计好的行为。

⚠️ 真源仍是磁盘 `presets/outfit_library.json`，**不要跑 build 写盘版**
   （源表只有 110 套，会抹掉手工追加的 43 套）。

跑法：python tools/seed_outfit_batch_b_20261114.py [--check]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PLUGIN, "tests"))
import _boot

S = _boot.load("anima_v4_store")
try:
    import build_outfit_library as BUILD
except Exception as _e:                     # pragma: no cover
    BUILD = None
    print("[warn] 无法导入 build_outfit_library：%s" % _e)

OUTFITS = [
    # ── L3 ×7：成型的情趣穿搭（不是只绑绳子）──────────────────────────
    dict(
        id="ns_leather_corset", zh="皮质束腰 · 短裙", cat="nsfw_outfit", level=3,
        tags=["black leather corset", "black miniskirt", "thigh boots",
              "front zipper", "glossy"],
        fields={"bottom": "corset", "cloth_mat": "leather", "shoes": "thigh boots"},
        note="皮革束腰压腰线 + 短裙，靠材质与轮廓撑档（latex/leather 判定表只给 L1）"),

    dict(
        id="ns_half_slip_shirt", zh="半脱衬衫 · 蕾丝内里", cat="nsfw_outfit", level=3,
        tags=["unbuttoned white shirt", "black lace bra", "panties",
              "bare shoulders", "one shoulder slip"],
        fields={"top": "shirt", "shoes": "bare feet"},
        note="衬衫半褪 + 内里可见，写清「露在哪」而不是只写 nude"),

    dict(
        id="ns_wet_lace", zh="湿身蕾丝 · 半透", cat="nsfw_outfit", level=3,
        tags=["wet clothes", "white lace lingerie", "see-through",
              "clinging fabric", "barefoot"],
        fields={"cloth_state_sfw": "wet clothes", "shoes": "bare feet"},
        note="湿透贴身的白色蕾丝；与内容包 n_state 的 see-through 互补而非重复"),

    dict(
        id="ns_corset_garter", zh="束腰 · 吊袜带", cat="nsfw_outfit", level=3,
        tags=["black corset", "garter belt", "black stockings",
              "thigh strap", "lace trim"],
        fields={"bottom": "corset", "socks": "black thighhighs, garter straps",
                "shoes": "bare feet"},
        note="经典束腰 + 吊袜带三件套，与「酒红星点」那套区分在黑系无星点"),

    dict(
        id="sw_sheer_onepiece", zh="半透连体泳衣", cat="swim", level=3,
        tags=["sheer one-piece swimsuit", "high leg", "wet skin",
              "poolside", "barefoot"],
        fields={"outfit": "one-piece swimsuit", "socks": "barefoot",
                "shoes": "bare feet"},
        note="半透连体泳衣；含泳装词根 → 池化在非泳装锚点会整条剔除（预期行为）"),

    dict(
        id="hm_open_robe", zh="露背浴袍 · 半褪", cat="home", level=3,
        tags=["white bathrobe", "open back", "slipping off shoulders",
              "bare legs", "bath towel around waist"],
        fields={"top": "bathrobe", "shoes": "bare feet"},
        note="浴袍从肩头滑落、露出背脊；居家场景的 L3 载体"),

    dict(
        id="hs_fishnet_bodysuit", zh="网袜连体", cat="hosiery", level=3,
        tags=["fishnet bodystocking", "black bodysuit", "garter straps",
              "thigh strap", "barefoot"],
        fields={"socks": "fishnet bodystocking", "shoes": "bare feet"},
        note="渔网连体袜当外穿，配吊袜带；`fishnet bodystocking` 本身只判 L2"),

    # ── L4 ×7：极端档的整套（现有只有一套悬吊缚）──────────────────────
    dict(
        id="ns_shibari_kimono", zh="缚 · 和服半脱", cat="nsfw_outfit", level=4,
        tags=["suspension bondage", "shibari", "kimono pulled down",
              "rope marks", "bare shoulders"],
        fields={"outfit": "kimono", "shoes": "bare feet"},
        note="和服被绳索吊起半褪，与纯「龟甲缚」区分在有衣物载体"),

    dict(
        id="ns_full_harness", zh="全束具 · 皮装", cat="nsfw_outfit", level=4,
        tags=["full body harness", "leather straps", "blindfold",
              "ball gag", "barefoot"],
        fields={"cloth_mat": "leather", "shoes": "bare feet"},
        note="全身束具 + 口球 + 蒙眼三件套，极端档的「装备流」"),

    dict(
        id="ns_apron_leash", zh="裸围裙 · 项圈", cat="nsfw_outfit", level=4,
        tags=["apron only", "naked apron", "collar and leash",
              "long gloves", "from behind"],
        fields={"outfit": "apron only", "accessory": "choker", "shoes": "bare feet"},
        note="裸围裙 + 项圈牵引 + 长手套；与 L3「裸围裙」区分在多两件配饰与 L4 准入"),

    dict(
        id="ns_collar_gown", zh="项圈 · 开衩长裙", cat="nsfw_outfit", level=4,
        tags=["collar and leash", "evening gown", "high slit",
              "long gloves", "thigh strap"],
        fields={"outfit": "evening gown", "accessory": "choker",
                "shoes": "stiletto heels"},
        note="穿得住礼服的极端档：长裙高开衩 + 项圈，画面仍「有衣服」"),

    dict(
        id="sw_side_tie_micro", zh="侧系带微型比基尼", cat="swim", level=4,
        tags=["micro bikini", "side-tie bikini", "bare legs",
              "navel", "wet skin"],
        fields={"outfit": "micro bikini", "socks": "barefoot", "shoes": "bare feet"},
        note="侧系带微型比基尼，比现有「微型比基尼」更少布料（L3→L4）"),

    dict(
        id="hm_shibari_shirt", zh="缚 · 男友衬衫", cat="home", level=4,
        tags=["shibari", "oversized shirt", "rope", "bare legs", "blushing"],
        fields={"top": "shirt", "shoes": "bare feet"},
        note="绳缚 + 男友衬衫，居家场景的极端档载体"),

    dict(
        id="ns_latex_catsuit", zh="全包乳胶 · 前开口", cat="nsfw_outfit", level=4,
        tags=["latex catsuit", "full coverage", "front zipper open",
              "glossy", "barefoot"],
        fields={"cloth_mat": "latex", "shoes": "bare feet"},
        note="全包乳胶连体、前拉链拉开；判定表不认识 catsuit，必须人工标 L4"),
]


def main(check_only=False):
    if BUILD is None:
        print("[fail] 缺 _level_of，无法校验档位")
        return 1
    errs, rows = [], []
    for o in OUTFITS:
        derived = BUILD._level_of(o)
        if derived > int(o["level"]):
            errs.append("%s 派生 L%d > 标注 L%d（内容比标注更露，会漏档）"
                        % (o["id"], derived, o["level"]))
        if not (o.get("fields") or {}):
            errs.append("%s 没有 fields（纯 tags 会绕开闸门）" % o["id"])
        rows.append((o["level"], derived, o["id"], len(o["tags"]),
                     len(o.get("fields") or {})))
    lib = S.library_payload()
    ids = {x["id"] for x in lib["outfits"]}
    dup = [o["id"] for o in OUTFITS if o["id"] in ids]
    if dup:
        errs.append("id 已存在（拒绝覆盖）：%s" % dup)
    if errs:
        print("[fail] 校验未过：")
        for e in errs:
            print("  -", e)
        return 1
    n3 = sum(1 for o in OUTFITS if o["level"] == 3)
    n4 = sum(1 for o in OUTFITS if o["level"] == 4)
    print("[ok] %d 套（L3 %d / L4 %d）派档均未超标注、fields 齐备、id 不撞"
          % (len(OUTFITS), n3, n4))
    for lv, dv, oid, nt, nf in rows:
        print("   L%d 标 / L%d 派  %-24s tags=%d fields=%d" % (lv, dv, oid, nt, nf))
    if check_only:
        return 0
    for o in OUTFITS:
        S.save_library_outfit(dict(o), new=False)
    lib = S.library_payload()
    print("[done] 入库后 %d 套；counts=%s"
          % (lib["count"], json.dumps(lib["counts"], ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
