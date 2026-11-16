# -*- coding: utf-8 -*-
"""K2 母题（branch=scene）→ Anima 场景的批量降维工具。

背景与口径见 `docs/K2母题并入评估_20261113.md`：
  · K2 母题是 800–1800 词的「平面设计海报」长文，**不能整段塞进 `scene_nl`**
    （`collect()` 按逗号切分 → 伪标签碎片 → F11/repair 连环报红）；
  · 可行路线是 **A 抽取式**：只保留 场景 / 材质 / 光 / 色彩，降维成 8–15 个
    Anima 真字段 + 一句 `scene_nl`；
  · 三条硬删：角色名（Hatsune Miku 等）、`canonical identity` 段、
    `titled "…"` 这类**画面内文字**要求（真机验证会画成乱码）。

本脚本只做**机械活**，不做判断：
  `--list`   未入库的 scene 分支清单（按 manifest 顺序编号）
  `--digest` 打印某一段的紧凑摘要（raw 前 700 字 + 关键词命中），供人工降维
  `--merge`  把人工写好的 patch 合并进 `presets/anima_v4_scenes.json`
             （拒绝覆盖已存在 id；写完请接着跑 tools/build_scenes.py 派生档位）

⚠️ 分类（group）由人写进 patch，脚本不猜 —— 见 `--merge` 的校验。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
K2 = os.path.join(os.path.dirname(PLUGIN), "Comfyui-K2-Editorial", "presets")

SCENES = os.path.join(PLUGIN, "presets", "anima_v4_scenes.json")
PREFIX = "sc_k2_"

#: 词 → Anima 字段值的启发式映射（只用于 digest 提示，不自动入库）
HINT = {
    "place": [
        ("glass cistern", "aquarium"), ("aquarium", "aquarium"),
        ("cathedral", "church"), ("chapel", "church"), ("monastery", "church"),
        ("cloister", "church"), ("shrine", "shrine"), ("torii", "shrine"),
        ("temple", "shrine"), ("castle", "castle"), ("ruin", "ruins"),
        ("ruins", "ruins"), ("wreckage", "ruins"), ("battlefield", "ruins"),
        ("cave", "cave"), ("cavern", "cave"), ("grotto", "cave"),
        ("lighthouse", "lighthouse"), ("beacon tower", "lighthouse"),
        ("greenhouse", "garden"), ("botanical", "garden"), ("garden", "garden"),
        ("orchard", "garden"), ("forest", "forest"), ("woodland", "forest"),
        ("grove", "forest"), ("jungle", "forest"),
        ("flower field", "flower field"), ("meadow", "field"), ("field", "field"),
        ("wheat", "field"), ("desert", "desert"), ("dune", "desert"),
        ("sand", "desert"), ("mountain", "mountain"), ("ridge", "mountain"),
        ("cliff", "mountain"), ("hill", "hill"), ("slope", "hill"),
        ("lake", "lake"), ("reservoir", "lake"), ("pond", "lake"),
        ("river", "river"), ("canal", "river"), ("stream", "river"),
        ("waterfall", "waterfall"), ("cascade", "waterfall"),
        ("underwater", "underwater"), ("submerged", "underwater"),
        ("seabed", "underwater"), ("pool", "poolside"), ("poolside", "poolside"),
        ("onsen", "onsen"), ("hot spring", "onsen"), ("bathhouse", "onsen"),
        ("beach", "beach"), ("shore", "beach"), ("coast", "beach"),
        ("bridge", "bridge"), ("overpass", "bridge"), ("viaduct", "bridge"),
        ("street", "street"), ("boulevard", "street"), ("avenue", "street"),
        ("alley", "alley"), ("backstreet", "alley"),
        ("crosswalk", "crosswalk"), ("intersection", "crosswalk"),
        ("stairwell", "stairwell"), ("staircase", "stairwell"),
        ("stairs", "stairwell"), ("elevator", "elevator"),
        ("rooftop", "rooftop"), ("roof deck", "rooftop"),
        ("balcony", "balcony"), ("terrace", "balcony"),
        ("bedroom", "bedroom"), ("bed ", "on bed"), ("hotel room", "hotel room"),
        ("hotel", "hotel room"), ("bathroom", "bathroom"),
        ("bathtub", "in bathtub"), ("shower", "showering"),
        ("kitchen", "kitchen"), ("classroom", "classroom"),
        ("library", "library"), ("archive", "library"),
        ("office", "office"), ("studio", "office"),
        ("cafe", "cafe"), ("coffee", "cafe"), ("bar ", "bar (place)"),
        ("bakery", "cafe"), ("changing room", "changing room"),
        ("locker room", "changing room"), ("fitting room", "fitting room"),
        ("convenience store", "convenience store"),
        ("park", "park"), ("amusement park", "amusement park"),
        ("ferris wheel", "ferris wheel"), ("cinema", "movie theater"),
        ("movie theater", "movie theater"), ("train station", "train station"),
        ("platform", "train station"), ("train interior", "train interior"),
        ("car interior", "car interior"), ("motorcycle", "on motorcycle"),
        ("bicycle", "bicycle"), ("bus stop", "bus stop"), ("bus", "bus stop"),
        ("stadium", "stadium"), ("arena", "stadium"), ("stage", "stage"),
        ("concert", "concert"), ("graveyard", "graveyard"),
        ("cemetery", "graveyard"), ("aquarium", "aquarium"),
        ("apartment", "apartment"), ("atrium", "hallway"),
        ("corridor", "hallway"), ("hallway", "hallway"),
    ],
    "light_fx": [
        ("caustic", "caustics"), ("god ray", "god rays"), ("shaft of light", "god rays"),
        ("backlit", "backlighting"), ("backlight", "backlighting"),
        ("rim light", "rim lighting"), ("neon", "neon lights"),
        ("moonlight", "moonlight"), ("moon", "moonlight"),
        ("candle", "candlelight"), ("fluorescent", "studio lighting"),
        ("soft light", "soft lighting"), ("diffuse", "soft lighting"),
        ("warm light", "warm lighting"), ("golden hour", "warm lighting"),
        ("dark background", "dark background"), ("black void", "dark background"),
        ("morning", "morning light"), ("dawn", "morning light"),
        ("studio", "studio lighting"),
    ],
    "weather": [
        ("rain", "rain"), ("storm", "thunderstorm"), ("snow", "snow"),
        ("blizzard", "snow"), ("fog", "fog"), ("mist", "fog"),
        ("steam", "steam"), ("vapor", "steam"), ("cherry blossom", "cherry blossoms"),
        ("petal", "cherry blossoms"), ("autumn", "falling leaves"),
        ("leaf", "falling leaves"), ("sunset", "sunset"), ("dusk", "twilight"),
        ("twilight", "twilight"), ("starry", "starry sky"), ("star field", "starry sky"),
        ("wind", "wind"), ("smoke", "smoke"), ("ash", "smoke"),
        ("spark", "sparks"), ("water drop", "water drop"), ("droplet", "water drop"),
        ("puddle", "puddle"), ("rainbow", "rainbow"), ("lightning", "lightning"),
        ("overcast", "overcast"), ("sunbeam", "sunbeam"), ("clear sky", "clear sky"),
        ("night", "night"),
    ],
    "prop": [
        ("pillar", "pillar"), ("column", "pillar"), ("statue", "statue"),
        ("lantern", "lantern"), ("candle", "candle"), ("mirror", "mirror"),
        ("window", "large window"), ("curtain", "curtains"), ("bookshelf", "bookshelf"),
        ("book", "book"), ("desk", "desk"), ("chair", "chair"), ("couch", "couch"),
        ("umbrella", "umbrella"), ("parasol", "parasol"), ("tree", "tree"),
        ("stair", "stairs"), ("railing", "railing"), ("vending machine", "vending machine"),
        ("chandelier", "chandelier"), ("vase", "vase"), ("plant", "potted plant"),
        ("door", "door"), ("swing", "swing"), ("balloon", "balloon"),
        ("blanket", "blanket"), ("towel", "towel"), ("cake", "cake"),
        ("cup", "cup"), ("table", "table"), ("counter", "counter"),
    ],
}


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _seeds():
    out = {}
    for fn in ("seeds_motifs.json", "seeds_extra.json"):
        p = os.path.join(K2, fn)
        if not os.path.exists(p):
            continue
        for s in _load(p).get("seeds") or []:
            sid = str(s.get("id") or "")
            if sid:
                out[sid] = s
    return out


def _manifest():
    d = _load(os.path.join(K2, "manifest.json"))
    return d.get("motifs") or []


def _scene_ids():
    d = _load(SCENES)
    return {s["id"] for s in (d.get("scenes") or [])}


def pending():
    """branch=scene 且还没入库的母题（manifest 顺序）。

    判"已入库"用**中文名**而不是 id：上一轮那 10 条用的是可读 id
    （`sc_k2_still_section`），与 manifest 的 `ComfyUI_temp_*` 对不上；
    后续 art 组入库时 id 又统一改成了 `sc_art_k2_*`（前端冒烟要求
    `sc_art_` 前缀）。所以判据是"id 里含 k2" + zh 相同，而不是固定前缀。
    """
    d = _load(SCENES)
    have = {str(s.get("zh") or "").strip()
            for s in (d.get("scenes") or [])
            if "k2" in str(s.get("id") or "")}
    out = []
    for m in _manifest():
        if m.get("branch") != "scene":
            continue
        mid = str(m.get("id") or "")
        if not mid or str(m.get("zh") or "").strip() in have:
            continue
        out.append(m)
    return out


def _hits(text: str, key: str) -> list:
    low = text.lower()
    seen = []
    for word, val in HINT[key]:
        if word in low and val not in seen:
            seen.append(val)
    return seen


def cmd_list():
    items = pending()
    print("未入库的 scene 分支：%d 条" % len(items))
    for i, m in enumerate(items):
        print("%3d  %-28s %-14s %-9s %s"
              % (i, m["id"], (m.get("zh") or "")[:12], m.get("aspect") or "",
                 "、".join(m.get("tags") or [])[:40]))
    return 0


def cmd_digest(start, count, ids=None):
    """`ids` 给了就按 id 取（不受"已入库就会从 pending 里消失"的索引漂移影响）。"""
    if ids:
        want = [i.strip() for i in ids.split(",") if i.strip()]
        by_id = {str(m.get("id")): m for m in _manifest()}
        items = [(i, by_id[i]) for i in want if i in by_id]
        seeds = _seeds()
        for i, m in items:
            _dump_one(i, m, seeds)
        return 0
    items = pending()[start:start + count]
    seeds = _seeds()
    for i, m in enumerate(items, start):
        _dump_one(i, m, seeds)
    return 0


def _dump_one(i, m, seeds):
    raw = str((seeds.get(m["id"]) or {}).get("raw") or "")
    head = " ".join(raw.split())[:700]
    print("=" * 78)
    print("[%s] %s | %s | %s | %s | %s"
          % (i, m["id"], m.get("zh") or "", m.get("title") or "",
             m.get("aspect") or "", "、".join(m.get("tags") or [])))
    print("  place?: %s" % (_hits(head, "place") or "-"))
    print("  light?: %s" % (_hits(head, "light_fx") or "-"))
    print("  weath?: %s" % (_hits(head, "weather") or "-"))
    print("  prop? : %s" % (_hits(head, "prop") or "-"))
    print("  raw   : %s" % head)


def cmd_merge(path, dry=False):
    patch = _load(path)
    new = patch.get("scenes") if isinstance(patch, dict) else patch
    if not isinstance(new, list) or not new:
        print("[k2] patch 里没有 scenes 数组：%s" % path)
        return 1
    data = _load(SCENES)
    scenes = data.get("scenes") or []
    have = {s["id"] for s in scenes}
    bad = []
    # 组不同 id 前缀不同：art 组前端冒烟要求 `sc_art_`，weapon 组要求 `sc_wp_`，
    # 其余（sfw / pose）沿用 `sc_k2_`。
    ID_PREFIX = {"art": "sc_art_", "weapon": "sc_wp_"}
    for s in new:
        sid = str(s.get("id") or "")
        want = ID_PREFIX.get(str(s.get("group") or ""), PREFIX)
        if not sid.startswith(want):
            bad.append((sid, "id 必须以 %s 开头（group=%s）"
                        % (want, s.get("group"))))
        elif sid in have:
            bad.append((sid, "已存在（拒绝覆盖）"))
        if str(s.get("group") or "") not in ("sfw", "nsfw", "near", "pose",
                                             "art", "weapon"):
            bad.append((sid, "group 不合法：%r" % s.get("group")))
        for k in ("zh", "tags", "scene_nl", "count_lock", "aspect"):
            if not s.get(k):
                bad.append((sid, "缺字段 %s" % k))
        if "level" in s:
            bad.append((sid, "level 必须由 build_scenes.py 派生，不要手写"))
    if bad:
        print("[k2] 校验失败 %d 项：" % len(bad))
        for sid, why in bad[:20]:
            print("   -", sid, why)
        return 1
    if dry:
        print("[k2] dry-run：%d 条可合并（现有 %d → %d）"
              % (len(new), len(scenes), len(scenes) + len(new)))
        return 0
    # 备份（精确到毫秒）
    import shutil
    import time
    ts = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    bak = os.path.join(PLUGIN, "_bak_k2batch_%s" % ts)
    os.makedirs(bak, exist_ok=True)
    shutil.copy2(SCENES, os.path.join(bak, "anima_v4_scenes.json"))
    scenes.extend(new)
    data["scenes"] = scenes
    with open(SCENES, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("[k2] 已合并 %d 条 → %d 条（备份 %s）"
          % (len(new), len(scenes), os.path.basename(bak)))
    print("[k2] 接着跑：python tools/build_scenes.py && python tools/build_scene_aspects.py")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "--list":
        return cmd_list()
    if cmd == "--digest":
        if len(argv) > 1 and not argv[1].lstrip("-").isdigit():
            return cmd_digest(0, 0, ids=argv[1])
        start = int(argv[1]) if len(argv) > 1 else 0
        count = int(argv[2]) if len(argv) > 2 else 20
        return cmd_digest(start, count)
    if cmd == "--merge":
        if len(argv) < 2:
            print("用法：--merge patch.json [--dry]")
            return 1
        return cmd_merge(argv[1], "--dry" in argv)
    print("未知命令：%s" % cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
