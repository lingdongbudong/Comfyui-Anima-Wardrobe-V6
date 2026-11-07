# -*- coding: utf-8 -*-
"""校验「待新增角色」的 danbooru tag 是否存在，并统计其高频服装标签。

背景（2026-11-07）：
  用户要求给角色库加《明日方舟：终末地》16 人 + 鬼灭之刃 7 人，并确保每人有
  一套官方服装预设。名单写在 `new_characters_20261107.json`，但里面的 tag 是
  **猜测**（按 danbooru 命名习惯推的），必须先校验 —— 写错 tag 的后果不是报错，
  而是出图时画出另一个人，或者干脆画不出角色。

为什么不能硬编服装预设：
  现有 2323 套预设的 note 里写着「safebooru 100 张统计（white tank top 32% …）」——
  即**从真实投稿统计高频服装词**得到的，不是凭印象写。本脚本把这一步自动化：
  抓该角色的 Top N 帖子 tag，按频率排序，只保留服装类关键词。

用法（需要能访问 safebooru）：
    python tools/verify_new_characters.py            # 只校验 tag 存在性
    python tools/verify_new_characters.py --stats    # 再统计每人服装 tag
    python tools/verify_new_characters.py --write    # 把结果写成 _report.json
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
IN_JSON = os.path.join(HERE, "new_characters_20261107.json")
OUT_JSON = os.path.join(HERE, "new_characters_20261107_report.json")

MIRROR = "https://safebooru.donmai.us"
# ⚠️ safebooru 的 WAF 拦浏览器 UA：curl/8.4.0 → 200，Chrome UA → 403。
UA = "curl/8.4.0"

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_last = [0.0]


def http_json(url, tries=4, timeout=45):
    for i in range(tries):
        gap = 0.12 - (time.time() - _last[0])
        if gap > 0:
            time.sleep(gap)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with _opener.open(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    return None


# ── 服装类关键词（danbooru 常见服装 tag）──────────────────────────────────
# 统计时只留这些，避免把发型 / 表情 / 姿势混进"服装预设"。
CLOTH_HINTS = (
    "dress", "skirt", "shirt", "blouse", "jacket", "coat", "cloak", "cape",
    "uniform", "serafuku", "sailor", "suit", "vest", "sweater", "hoodie",
    "kimono", "yukata", "hakama", "haori", "obi", "chinese clothes",
    "japanese clothes", "maid", "apron", "headdress", "nun", "habit",
    "armor", "plate", "gauntlet", "greave", "leotard", "bodysuit", "plugsuit",
    "wetsuit", "swimsuit", "bikini", "one-piece swimsuit", "school swimsuit",
    "thighhighs", "pantyhose", "stockings", "socks", "legwear", "garter",
    "boots", "shoes", "sandals", "heels", "slippers", "barefoot", "loafers",
    "gloves", "elbow gloves", "fingerless gloves", "scarf", "necktie",
    "bowtie", "belt", "suspenders", "hat", "cap", "beret", "headband",
    "tiara", "crown", "veil", "ribbon", "hair ribbon", "choker", "earrings",
    "necklace", "bracelet", "goggles", "mask", "eyepatch", "backpack",
    "outfit", "clothes", "attire", "tunic", "robe", "trenchcoat", "hood",
    "crop top", "tank top", "tube top", "camisole", "shorts", "pants",
    "jeans", "trousers", "leggings", "sleeves", "detached sleeves",
)

# 明显不属于"官方服装"的自由词：换装 / 二创服装 / 状态词
NOISE = (
    "cosplay", "alternate", "swimsuit", "bikini", "nude", "topless",
    "wet", "torn", "censored", "underwear", "panties", "bra",
)


def is_cloth(tag_name):
    t = tag_name.replace("_", " ")
    if any(n in t for n in NOISE):
        return False
    return any(h in t for h in CLOTH_HINTS)


def check_tag(tag):
    """返回 (存在?, category, post_count)。danbooru category=4 才是角色。"""
    url = f"{MIRROR}/tags.json?search%5Bname%5D={urllib.parse.quote(tag, safe='')}"
    data = http_json(url) or []
    if not data:
        # name 查不到时退回前缀匹配，能看出"tag 存在但需要 _(作品) 后缀"
        url2 = (f"{MIRROR}/tags.json?search%5Bname_matches%5D="
                f"{urllib.parse.quote(tag + '*', safe='')}&limit=8")
        alt = http_json(url2) or []
        if alt:
            return False, [a["name"] for a in alt if a.get("post_count", 0) > 0][:5], 0
        return False, None, 0
    t = data[0]
    return True, t.get("category"), t.get("post_count", 0)


def cloth_stats(tag, sample=100):
    """抓该角色最近 N 张 SFW 投稿，统计服装类 tag 频率。"""
    url = (f"{MIRROR}/posts.json?tags={urllib.parse.quote(tag, safe='')}"
           f"+solo&limit={sample}")
    posts = http_json(url) or []
    cnt = {}
    total = 0
    for p in posts:
        raw = p.get("tag_string") or (p.get("tag_string_general", "") + " "
                                      + p.get("tag_string_character", ""))
        tags = [x for x in str(raw).split() if x]
        if not tags:
            continue
        total += 1
        for t in set(tags):
            if is_cloth(t):
                cnt[t.replace("_", " ")] = cnt.get(t.replace("_", " "), 0) + 1
    top = sorted(cnt.items(), key=lambda kv: -kv[1])[:8]
    return total, [{"tag": k, "n": v,
                    "pct": round(100.0 * v / total) if total else 0} for k, v in top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="额外统计服装 tag")
    ap.add_argument("--write", action="store_true", help="写报告 JSON")
    args = ap.parse_args()

    spec = json.load(open(IN_JSON, encoding="utf-8"))
    rows = []
    print(f"待校验 {len(spec['characters'])} 个角色\n")
    for i, c in enumerate(spec["characters"], 1):
        tag = c["tag_guess"]
        try:
            ok, extra, pop = check_tag(tag)
        except Exception as e:
            print(f"{i:2d}. {c['zh']:8s} {tag:34s} 请求失败: {e}")
            rows.append({**c, "ok": None, "error": str(e)})
            continue
        if ok:
            kind = {1: "artist", 3: "copyright", 4: "character"}.get(extra, str(extra))
            print(f"{i:2d}. ✓ {c['zh']:8s} {tag:34s} cat={kind:9s} post={pop}")
            row = {**c, "ok": True, "category": extra, "post_count": pop}
        else:
            alt = extra if isinstance(extra, list) else []
            print(f"{i:2d}. ✗ {c['zh']:8s} {tag:34s} 不存在 | 近似: {alt}")
            row = {**c, "ok": False, "alternatives": alt}
        if args.stats and row.get("ok"):
            n, top = cloth_stats(tag)
            row["cloth"] = top
            print(f"      服装统计（{n} 张）: "
                  + " / ".join(f"{t['tag']} {t['pct']}%" for t in top[:6]))
        rows.append(row)

    good = sum(1 for r in rows if r.get("ok"))
    print(f"\n合计：存在 {good} / {len(rows)}；不存在 {len(rows) - good}")
    if args.write:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "rows": rows}, open(OUT_JSON, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"报告 → {OUT_JSON}")
    return 0 if good == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
