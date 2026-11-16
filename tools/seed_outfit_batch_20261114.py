# -*- coding: utf-8 -*-
"""一次性种入 9 套 L0 服装（v6.23.0 的 A′ 批次，用户 2026-11-14「先做 C 和 A」）。

为什么是 9 套而不是"每类 +10"：按 `PLACE_STYLE` 的 61 个地点算覆盖，
`casual` 是 25 地点 / 6 套 L0（4.2 地点每套）、`home` 是 10 / 4（2.5），
而 fantasy / night / academic / office / traditional / sporty 六个风格锚
已经是 0.1–0.2（过剩 5–8 倍）。所以这批只补 casual 6 + home 3。

⚠️ 真源是磁盘 `presets/outfit_library.json`（当前 144 套），
   `tools/build_outfit_library.py` 的 `OUTFITS` 源表只有 110 套 —— **绝不能跑写盘版**，
   那会抹掉后来手工追加的 34 套。所以本脚本走 `save_library_outfit()` 直接入库。

⚠️ 每套必须写 `fields`（不是只写 tags）：只写 tags 的套会绕开
   `style_bans()` 与档位收敛（现有 75 套 L0 里已有 21 套这种）。
   本批 fields 一律用**词库真实选项值**，档位由 `_level_of()` 反算校验必须为 0。

跑法：python tools/seed_outfit_batch_20261114.py [--check]
      --check 只校验不入库。
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
BUILD = None
try:
    import build_outfit_library as BUILD   # 同目录，拿 _level_of 复用同一套派生
except Exception as _e:                     # pragma: no cover
    print("[warn] 无法导入 build_outfit_library：%s" % _e)

OUTFITS = [
    # ── casual ×6：地点缺口是 cafe / park / bridge / bicycle / flower field / mountain ──
    dict(
        id="ol_white_shirt_wide", zh="白衬衫 · 阔腿裤", cat="casual", level=0,
        tags=["white shirt", "beige wide-leg pants", "brown loafers",
              "canvas tote bag", "tucked in shirt"],
        fields={"top": "shirt", "bottom": "wide-leg pants",
                "shoes": "loafers", "cloth_color": "white"},
        note="通勤 / 咖啡厅向，白衬衫扎进阔腿裤的干净线条"),

    dict(
        id="ol_polo_tennis", zh="Polo · 网球裙", cat="casual", level=0,
        tags=["white polo shirt", "white tennis skirt", "white sneakers",
              "ankle socks", "sports wristband"],
        fields={"top": "polo shirt", "bottom": "tennis skirt",
                "shoes": "sneakers", "socks": "ankle socks",
                "cloth_color": "white"},
        note="公园 / 街道运动休闲向，网球裙不露、polo 领口收着"),

    dict(
        id="ol_turtleneck_long", zh="高领针织 · 长裙", cat="casual", level=0,
        tags=["brown turtleneck", "cream long skirt", "brown ankle boots",
              "knitwear", "shoulder bag"],
        fields={"top": "turtleneck", "bottom": "long skirt",
                "shoes": "ankle boots", "cloth_mat": "knitwear",
                "cloth_color": "brown"},
        note="秋日林道 / 桥边向，针织与长裙的棕米配色"),

    dict(
        id="ol_tee_overalls", zh="T恤 · 背带裤", cat="casual", level=0,
        tags=["grey t-shirt", "denim overalls", "white sneakers",
              "canvas backpack", "rolled up sleeves"],
        fields={"top": "t-shirt", "bottom": "overalls",
                "shoes": "sneakers", "cloth_mat": "denim",
                "cloth_color": "grey"},
        note="骑车 / 郊外向，背带裤不写露肤，靠工装轮廓撑画面"),

    dict(
        id="ol_denim_jacket_dress", zh="牛仔外套 · 白裙", cat="casual", level=0,
        tags=["blue denim jacket", "white a-line skirt", "white sandals",
              "straw hat", "crossbody bag"],
        fields={"top": "denim jacket", "bottom": "a-line skirt",
                "shoes": "sandals", "accessory": "straw hat",
                "cloth_mat": "denim", "cloth_color": "blue"},
        note="花田 / 海边步道向，外套敞开但里面是完整白裙"),

    dict(
        id="ol_parka_cargo", zh="短羽绒 · 工装裤", cat="casual", level=0,
        tags=["black down jacket", "khaki cargo pants", "black sneakers",
              "fur trim hood", "knit scarf"],
        fields={"top": "down jacket", "bottom": "cargo pants",
                "shoes": "sneakers", "cloth_color": "black"},
        note="山道 / 公路向的冬季外套，不写湿冷氛围（那是场景的事）"),

    # ── home ×3：bedroom / kitchen / balcony 三个高频居家地点 ──
    dict(
        id="hm_tee_shorts", zh="家居T · 短裤", cat="home", level=0,
        tags=["cream t-shirt", "grey shorts", "white slippers",
              "ribbed socks", "messy hair"],
        fields={"top": "t-shirt", "bottom": "shorts",
                "shoes": "slippers", "socks": "ribbed socks"},
        note="厨房 / 客厅向的日常家居，短裤选 `shorts` 不是 `short shorts`（那是 L1）"),

    dict(
        id="hm_tank_wide", zh="背心 · 阔腿裤", cat="home", level=0,
        tags=["white tank top", "grey wide-leg pants", "barefoot",
              "hair clip", "loose hair"],
        fields={"top": "tank top", "bottom": "wide-leg pants",
                "shoes": "bare feet"},
        note="阳台 / 卧室向，背心用 `tank top`（`camisole` 是 L1，别用）"),

    dict(
        id="hm_sweater_dress", zh="针织连衣裙 · 毛袜", cat="home", level=0,
        tags=["beige sweater dress", "white slippers", "white ribbed socks",
              "knitwear", "long sleeves"],
        fields={"outfit": "sweater dress", "shoes": "slippers",
                "socks": "ribbed socks", "cloth_mat": "knitwear"},
        note="冬季卧室向，整套只写 `outfit` 一次，避免与 top/bottom 重复描述"),
]


def main(check_only=False):
    if BUILD is None:
        print("[fail] 缺 _level_of，无法校验派生档位")
        return 1
    errs = []
    for o in OUTFITS:
        lv = BUILD._level_of(o)
        if lv != 0:
            errs.append("%s 派生档位是 L%d（本批要求 L0）" % (o["id"], lv))
        if not (o.get("fields") or {}).get("outfit") and \
                not (o.get("fields") or {}).get("top"):
            errs.append("%s 既没有 fields.outfit 也没有 fields.top" % o["id"])
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
    print("[ok] 9 套派生档位全 L0、id 不撞、fields 齐备")
    if check_only:
        return 0
    for o in OUTFITS:
        S.save_library_outfit(dict(o), new=False)
    lib = S.library_payload()
    print("[done] 入库后 %d 套 / %d 类；counts=%s"
          % (lib["count"], len(lib["categories"]), json.dumps(lib["counts"])))
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
