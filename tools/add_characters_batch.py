# -*- coding: utf-8 -*-
"""把一批新角色 + 服装预设增量写入插件数据（2026-11-07）。

设计原则（对齐项目纪律）：
  ① **只追加，不重建** —— 不跑 build_characters.py（它依赖 safebooru，现在被
     Cloudflare 拦着；而且整份重建会碰现有 2409 条）。这里直接改产物 + 同步
     手工表，两边一致，下次联网重跑构建也不会丢（build 的 render() 会合并
     tools/manual_characters.json）。
  ② **拒绝覆盖已有 tag** —— 撞车直接中止（v5.7.0 踩过静默覆盖的坑）。
  ③ 改前毫秒备份，改后打印每一项的增减，便于逐条核对。
  ④ 排序保持既有约定：characters 按 pop 降序（新角色 pop=0 追加末尾）、
     works 按 count 降序（新作品按实际人数插到对应位置）。

用法：python tools/add_characters_batch.py [--dry]
"""
import argparse
import io
import json
import os
import shutil
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SPEC = os.path.join(HERE, "new_characters_20261107.json")
CHARS = os.path.join(PLUGIN, "presets", "anima_v4_characters.json")
OUTFITS = os.path.join(PLUGIN, "presets", "character_outfits.json")
MANUAL = os.path.join(HERE, "manual_characters.json")


def stamp():
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time()*1000)%1000:03d}"


def load(p):
    with io.open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save(p, obj, backup=True):
    if backup:
        shutil.copy2(p, f"{p}.bak-{stamp()}-before-addchars")
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    spec = load(SPEC)
    chars_doc = load(CHARS)
    outfits_doc = load(OUTFITS)
    manual_doc = load(MANUAL)

    old_n = len(chars_doc["characters"])
    have = {c["tag"] for c in chars_doc["characters"]}
    new = spec["characters"]

    dup = [c["tag"] for c in new if c["tag"] in have]
    if dup:
        raise SystemExit(f"✗ 这些 tag 已在库中，拒绝覆盖：{dup}")

    # ── 角色 ──────────────────────────────────────────────────────────
    for c in new:
        assert c.get("trigger"), f"{c['tag']} 缺 trigger"
        assert c.get("thumb"), f"{c['tag']} 缺 thumb"
        chars_doc["characters"].append(c)

    # ── 作品：新增 + 现有 count 重算（只动受影响的） ──────────────────
    by_work = {}
    for c in chars_doc["characters"]:
        by_work[c["work"]] = by_work.get(c["work"], 0) + 1

    ws = chars_doc["works"]
    ids = {w["id"] for w in ws}
    for w in spec["works"]:
        if w["id"] in ids:
            continue
        ws.append({"id": w["id"], "en": w["en"], "zh": w["zh"],
                   "count": by_work.get(w["en"], 0), "pop": 0, "target": False})
    for w in ws:
        if w["en"] in by_work:
            w["count"] = by_work[w["en"]]
    ws.sort(key=lambda x: (-x["count"], x["id"]))

    # ── 服装预设 ──────────────────────────────────────────────────────
    outs = outfits_doc.setdefault("outfits", {})
    added_outfits = 0
    for tag, rec in (spec.get("outfits") or {}).items():
        if tag in outs and (outs[tag].get("list") or []):
            continue
        outs[tag] = rec
        added_outfits += 1
    outfits_doc.setdefault("_meta", {})["roles"] = sum(
        1 for v in outs.values() if (v.get("list") or []))

    # ── 手工表同步（保证联网重跑 build 不丢） ─────────────────────────
    mw = manual_doc["_meta"].setdefault("works", {})
    for w in spec["works"]:
        mw[w["en"]] = w["zh"]
    man_have = {c["tag"] for c in manual_doc["characters"]}
    man_added = 0
    for c in new:
        if c["tag"] in man_have:
            continue
        manual_doc["characters"].append(c)
        man_added += 1

    print(f"角色 {old_n} → {old_n + len(new)}（+{len(new)}）")
    print(f"作品 {len(ids)} → {len(ids) + sum(1 for w in spec['works'] if w['id'] not in ids)}")
    print(f"预设条目 {len(outs) - added_outfits} → {len(outs)}（+{added_outfits}）")
    print(f"手工表 +{man_added} 条")
    print("新增角色:", ", ".join(c["zh"] for c in new))

    if args.dry:
        print("\n[dry] 未写盘")
        return 0

    save(CHARS, chars_doc)
    save(OUTFITS, outfits_doc)
    save(MANUAL, manual_doc)
    print("\n✓ 已写盘（三份都做了毫秒备份）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
