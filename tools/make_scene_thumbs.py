# -*- coding: utf-8 -*-
"""把已有的批量出图裁成「场景预览图」写进 presets/scene_thumbs/（v6.19.0）。

## 解决什么

场景页 781 条只靠名字猜长什么样，要套用再跑一张才知道。这个脚本把**已经跑过
的**批量图挑出来，按场景 id 命名存进插件目录，面板直接读。

## 数据从哪来

批量脚本（anima-200-scene-batch）每次都会写 jsonl，每行带 `scene`（场景 id）、
`file` + `subfolder`（相对 ComfyUI/output）或 `path`（绝对）。本脚本把这些
批次日志扫一遍，建 `场景 id -> 图片路径` 的映射，只认**还在盘上**的图。

已有批次实测覆盖（2026-11-13，全库 781 条）：
    泷奈 268 / 心海 237 / 忍 230 / 赞妮·竖 227 / 赞妮·横 80 / 02 那批 / artoria 5
    → 并集 353 条，剩 428 条要新跑（见 --missing-out 导出的清单）。

## 输出规格

    presets/scene_thumbs/<scene_id>.webp     最长边 480、q82、约 3~5 KB/张

**只存一档大图**，不做"小图 + 大图"两份：
  · 折叠态小图（44×44）用 CSS 缩同一张，首屏 60 条约 180 KB，可接受；
  · 展开时浏览器已缓存，图立刻出现、不闪；
  · 两套文件要管两遍命名、两遍孤儿清理，收益不值。
保留**原比例**（不裁成固定 4:3）：竖图 270×480、横图 480×270 ——
面板用 `object-fit:contain` + max-height 300px 显示，比例不失真，
而且"这条是竖构图还是横构图"一眼看得出来。

## 用法

    PY="S:/AI_workV5/python_embeded/python.exe"     # 必须嵌入版，系统 python 没 Pillow

    $PY tools/make_scene_thumbs.py --dry                  # 只报告覆盖，不写盘
    $PY tools/make_scene_thumbs.py                        # 全量生成（已有图会被覆盖）
    $PY tools/make_scene_thumbs.py --only-missing         # 只补还没生成的
    $PY tools/make_scene_thumbs.py --missing-out m.json   # 把缺图清单导出给出图脚本

## ⚠️ 踩点

1. **路径要用 output 根拼 `subfolder`**，不是相对 jsonl 所在目录 ——
   批次日志的 `subfolder` 形如 `图片\\Anima\\角色图\\井之上泷奈`，带反斜杠。
2. **只在盘上才算数**。批次日志记录过、但图已被清理的行要跳过：
   否则会生成一张指向不存在文件的缩略图，面板显示破图。
3. **同一场景多批次都有图时取先命中的**。`--prefer` 可以把某个批次提到最前
   （例如后来重拍的更满意的一套）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
PRESETS = os.path.join(PLUGIN, "presets")
SCENES_FILE = os.path.join(PRESETS, "anima_v4_scenes.json")
OUT_DIR = os.path.join(PRESETS, "scene_thumbs")

OUTPUT_ROOT = "S:/AI_workV5/ComfyUI/output"
SKILLS_DIR = "S:/技能"

# 用户 2026-11-13 **二次收口**：先是「NSFW 组不加预览图」，随后明确成
# 「NSFW L1 及以上场景都不加预览图」。所以判据是 **level**，不是 group：
# 擦边内容散落在各组里 —— sfw 组 70 条 L1+（含「擦边 L2」12 条）、
# near 26 条、pose 15 条、art 2 条，按 group 卡会漏掉这 113 条。
# 能做预览图的只剩 **561 条 L0**。
# 后端也有一道同样的闸（nodes.scenes_payload 的 thumb 字段），
# 这里只是让工具不去生成 —— 两道都要有，否则盘上的历史文件照样会被显示。
SKIP_MIN_LEVEL = 1

# 裁切参数（2026-11-13 实测：480/q82 = 17~23KB，440/q78 = 13~14KB）。
# 展开态的显示高度封顶 300px，440 长边的竖图实际显示 168×300、
# 像素密度 1.47x，缩一档看不出差别；而 781 张的总量从 17MB 降到 10MB，
# 首屏 60 条从 1.4MB 降到 0.8MB —— 这一步换得值。
LONG_EDGE = 440
QUALITY = 78


def _load_scenes():
    with open(SCENES_FILE, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d["scenes"] if isinstance(d, dict) and "scenes" in d else d


def _default_logs():
    """默认扫两类位置：工作区的临时批次日志 + output 里随批次留下的。"""
    pats = [os.path.join(SKILLS_DIR, "*.jsonl"),
            os.path.join(OUTPUT_ROOT, "图片", "Anima", "角色图", "*.jsonl")]
    out = []
    for p in pats:
        out += sorted(glob.glob(p))
    return out


def _resolve(row):
    """jsonl 一行 → 盘上的绝对路径（不存在就回空串）。"""
    p = row.get("path") or ""
    if p and os.path.isfile(p):
        return p
    sub = row.get("subfolder") or ""
    fn = row.get("file") or ""
    if not fn:
        return ""
    p = os.path.join(OUTPUT_ROOT, sub, fn)
    return p if os.path.isfile(p) else ""


def build_map(logs, prefer=()):
    """扫描批次日志，返回 {scene_id: 图片绝对路径}。

    后到的批次**不覆盖**先到的 —— 先到先得；`prefer` 里的批次文件会被提到最前，
    用来表达"这套我更喜欢"。
    """
    order = [p for p in prefer] + [p for p in logs if p not in prefer]
    m = {}
    scanned = 0
    for path in order:
        if not os.path.isfile(path):
            print(f"  [跳过] 找不到日志 {path}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                scanned += 1
                sid = str(row.get("scene") or "")
                if not sid or sid in m:
                    continue
                real = _resolve(row)
                if real:
                    m[sid] = real
    print(f"  扫描 {scanned} 行，命中在盘图 {len(m)} 条")
    return m


def _to_thumb(src, dst):
    """读图 → 等比缩到最长边 LONG_EDGE → 存 webp。返回目标字节数。"""
    from PIL import Image

    im = Image.open(src)
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        # 面板背景是深色，透明直接铺白会出白框；铺成中性深灰更接近面板底色
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (24, 24, 24))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((LONG_EDGE, LONG_EDGE), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im.save(dst, "WEBP", quality=QUALITY, method=6)
    return os.path.getsize(dst)


def main(argv=None):
    ap = argparse.ArgumentParser(description="把批量出图裁成场景预览图")
    ap.add_argument("--logs", nargs="*", default=None,
                    help="批次 jsonl 路径（默认扫工作区 + output 里的 *.jsonl）")
    ap.add_argument("--prefer", nargs="*", default=[],
                    help="优先使用的批次 jsonl（同名场景以它为准）")
    ap.add_argument("--dry", action="store_true", help="只报告，不写盘")
    ap.add_argument("--only-missing", action="store_true",
                    help="只生成 OUT_DIR 里还没有的")
    ap.add_argument("--missing-out", default="",
                    help="把缺图清单写到这个 json（给出图脚本当输入）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 张（试跑用）")
    args = ap.parse_args(argv)

    scenes = _load_scenes()
    by_id = {s["id"]: s for s in scenes}
    logs = args.logs if args.logs else _default_logs()
    print(f"场景库 {len(scenes)} 条；扫描 {len(logs)} 份批次日志")

    m = build_map(logs, prefer=args.prefer)

    have, miss, skipped = [], [], []
    for s in scenes:
        sid = s["id"]
        # L1 及以上整条跳过：既不算"有图"，也不算"缺图"（它本来就不该有）
        if int(s.get("level", 0) or 0) >= SKIP_MIN_LEVEL:
            skipped.append(sid)
            continue
        if sid in m:
            have.append(sid)
        else:
            miss.append(sid)

    todo = have
    if args.only_missing:
        todo = [sid for sid in have
                if not os.path.isfile(os.path.join(OUT_DIR, f"{sid}.webp"))]
    if args.limit:
        todo = todo[: args.limit]

    print(f"可生成 {len(have)} 条 / 缺图 {len(miss)} 条"
          f"（本轮处理 {len(todo)} 条；跳过 L{SKIP_MIN_LEVEL}+ 共 {len(skipped)} 条）")

    written = 0
    failed = []
    total_bytes = 0
    if not args.dry:
        for i, sid in enumerate(todo, 1):
            src = m[sid]
            dst = os.path.join(OUT_DIR, f"{sid}.webp")
            try:
                n = _to_thumb(src, dst)
                written += 1
                total_bytes += n
            except Exception as exc:
                failed.append((sid, src, str(exc)))
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} …")

    # 按组分组的覆盖报告（组内再拆 L0 / L1+ —— 因为跳过是按 level 判的，
    # 只看组会把"sfw 组里 70 条 L1+ 被跳过"这件事藏起来）
    groups = {}
    for s in scenes:
        g = s.get("group") or "?"
        d = groups.setdefault(g, {"total": 0, "have": 0, "missing": 0, "skip": 0})
        d["total"] += 1
        if int(s.get("level", 0) or 0) >= SKIP_MIN_LEVEL:
            d["skip"] += 1
        elif s["id"] in m:
            d["have"] += 1
        else:
            d["missing"] += 1
    print("\n分组覆盖（跳过 = L%d 及以上，不挂预览图）：" % SKIP_MIN_LEVEL)
    for g, d in sorted(groups.items()):
        print(f"  {g:<8} {d['have']:>3}/{d['total']:<3} 有图，缺 {d['missing']:<3}"
              f" 跳过 {d['skip']}")

    if written:
        print(f"\n写入 {written} 张，共 {total_bytes / 1024:.0f} KB"
              f"（平均 {total_bytes / max(written, 1) / 1024:.1f} KB/张）→ {OUT_DIR}")
    if failed:
        print(f"\n⚠️ 失败 {len(failed)} 条：")
        for sid, src, err in failed[:10]:
            print(f"  {sid}  {src}\n    {err}")

    if args.missing_out:
        # 缺图清单：带上面板需要的字段，出图脚本可以直接吃。
        # ⚠️ 只导出**当前库内**的 id —— 老批次里已删除的场景不应该再占出图名额。
        recs = []
        for sid in miss:
            s = by_id[sid]
            recs.append({
                "id": sid,
                "zh": s.get("zh", ""),
                "group": s.get("group", ""),
                "theme": s.get("theme", ""),
                # level 要带上：出图脚本用它定 nsfw_level（L0→sfw … L4→extreme），
                # 缺了这一项全都会按 sfw 跑，高等级场景出的图跟档位对不上。
                "level": int(s.get("level", 0) or 0),
                "aspect": s.get("aspect", "both"),
                "desc": s.get("desc", ""),
                "work": s.get("work", ""),
                "weapon": s.get("weapon", ""),
            })
        with open(args.missing_out, "w", encoding="utf-8") as f:
            json.dump({"count": len(recs), "scenes": recs}, f,
                      ensure_ascii=False, indent=1)
        print(f"\n缺图清单 {len(recs)} 条 → {args.missing_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
