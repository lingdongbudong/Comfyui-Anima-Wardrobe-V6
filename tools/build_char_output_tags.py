# -*- coding: utf-8 -*-
"""角色正文输出 tag 构建（v5.0.11）。

用户需求（2026-11-03）：
    「更新角色库所有角色标签格式为 texas\\(arknights\\) 前面是角色，后面是作品。
      之前的格式为 texas，arknights。部分人物识别不了。」

**最终口径以 Anima 官方文档为准**（circlestone-labs/Anima 模型卡 + Anima-2.9B 作者说明）：

    · 「Use lowercase for tags, and **spaces instead of underscores**.
       **Score tags are the only tags that use underscores.**」
    · 「**Don't use underscore except for score tags.**」（2.9B 作者）
    · 顺序：「[quality/meta/year/safety] [1girl/1boy] [character] [series]
       [artist] [general]」
    · 「Characters should (think "**must**" in this case) be **follow by their
       series/copyrights** ... follow by their appearances (the more the better).」
    · 官方完整示例：`... 1girl, oomuro sakurako, yuru yuri, @nnn yryr, smile, ...`

结论：**角色与作品是两个独立 tag，空格写法，不用下划线**。
    `texas_(arknights)` 这种 danbooru 原始 tag 形式**不符合官方要求**（下划线）。

问题根因（实测 seed=42）：
    · `texas_(arknights)` → 正文只写 `texas`（作品名被 strip_role_works 剔除）
      —— 缺作品名，直接违反官方「must be followed by series」
    · `fubuki_(kancolle)` → 正文 `fubuki, kantai collection`（这条本来是对的）

本脚本产出 `presets/char_output_tags.json`：
    { trigger 原文 : {"out": Anima 写法, "ref": danbooru 参考 tag, "src": 来源, "posts": 投稿数} }

口径：
    ① 只改输出层 —— 库内 trigger / 收藏 / 服装预设键 / 别名表一律不动；
    ② 作品名以 danbooru 官方 API 逐条核验（works.id 不可靠：
       `goddess_of_victory_nikke` 查不到，正式名是 `goddess_of_victory:_nikke`）；
    ③ 形态/职阶括号保留在角色名里，作品单独写：
       `houshou_marine_(1st_costume)` → `houshou marine (1st costume), hololive`。

跑法：
    python tools/build_char_output_tags.py --works     # 只核验作品层，看命中率
    python tools/build_char_output_tags.py --all       # 全量（作品 + 角色）
    python tools/build_char_output_tags.py --all --resume   # 断点续跑（用本地缓存）

⚠️ danbooru 直连可用（2026-11-03 实测），safebooru 全站 Cloudflare 挑战。
⚠️ UA 必须给 curl（danbooru 对浏览器 UA 也不友好，沿用 build_characters 的结论）。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PRESETS = os.path.join(ROOT, "presets")
CACHE_DIR = os.path.join(HERE, ".cache_tags")
OUT_PATH = os.path.join(PRESETS, "char_output_tags.json")

API = "https://danbooru.donmai.us"
UA = "curl/8.4.0"

_local = threading.local()


def _opener():
    """每线程独立的 opener。

    ⚠️ 踩过的坑：共享一个 `OpenerDirector` 时，多线程会互相等它的内部状态，
       8 路并发实测只有 **1.1 req/s**（等于串行）。改成线程本地后才是真并发。
    """
    op = getattr(_local, "op", None)
    if op is None:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _local.op = op
    return op


_lock = threading.Semaphore(10)        # 10 路并发（实测约 3-4 req/s）
_last = [0.0]


def http_json(url, tries=4, timeout=30):
    for i in range(tries):
        with _lock:
            gap = 0.06 - (time.time() - _last[0])
            if gap > 0:
                time.sleep(gap)
            _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with _opener().open(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # 限流：退避后重试，别把礼貌额度打穿
                time.sleep(2.0 * (i + 1))
                continue
            if i == tries - 1:
                raise
            time.sleep(1.0 * (i + 1))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.0 * (i + 1))
    return None


def _cache_path(tag):
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", tag)
    return os.path.join(CACHE_DIR, safe + ".json")


def lookup(tag, resume=True):
    """查一个 tag 在 danbooru 的记录。返回 dict 或 None（不存在）。

    ⚠️ 用 `search[name]=` 精确匹配 —— 模糊匹配会把 `kaga_(kancolle)` 这类
       已废弃别名混进来，拿到错的 name。
    """
    if not tag:
        return None
    cp = _cache_path(tag)
    if resume and os.path.exists(cp):
        try:
            with open(cp, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    url = API + "/tags.json?search%5Bname%5D=" + urllib.parse.quote(tag) + "&limit=1"
    try:
        d = http_json(url)
    except Exception as exc:
        print(f"    [ERR] {tag}: {exc}")
        d = None
    rec = None
    if d:
        r0 = d[0]
        # 精确校验：接口偶尔会宽松匹配，名字不等就当没查到
        if str(r0.get("name") or "").lower() == tag.lower():
            rec = {
                "name": r0.get("name"),
                "category": r0.get("category"),
                "post_count": r0.get("post_count") or 0,
                "is_deprecated": bool(r0.get("is_deprecated")),
            }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False)
    return rec


# ============================================================================
# ① 作品层：works.id 不可靠，逐条核验
# ============================================================================

def _slug_candidates(work):
    """工作名 → 候选 danbooru copyright tag。

    danbooru 的规范只有一条：空格变下划线，其余字符（`:` `/` `!` `'`）原样保留。
    实测对照：`goddess of victory: nikke` → `goddess_of_victory:_nikke`。
    """
    en = str(work.get("en") or "").strip()
    wid = str(work.get("id") or "").strip()
    out = []
    if en:
        out.append(en.lower().replace(" ", "_"))
    if wid and wid not in out:
        out.append(wid)
    return out


def build_works(chars, works):
    """{work 英文名(小写) : 正式 copyright tag}"""
    print(f"\n① 核验作品层（{len(works)} 个）…")
    mapping = {}
    stat = {"exact": 0, "alt": 0, "miss": 0}
    misses = []

    def one(w):
        cands = _slug_candidates(w)
        for i, c in enumerate(cands):
            rec = lookup(c)
            if _usable(rec, cat=3):
                return (w.get("en") or "").lower(), rec["name"], ("exact" if i == 0 else "alt")
        # 都查不到 → 退回 works.id（标 miss，后续角色层还会再试）
        return (w.get("en") or "").lower(), (cands[-1] if cands else ""), "miss"

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as ex:
        for en, tag, src in ex.map(one, works):
            if en:
                mapping[en] = tag
            stat[src] = stat.get(src, 0) + 1
            if src == "miss":
                misses.append((en, tag))
    print(f"   命中 {stat['exact']} / 备选 {stat['alt']} / 未命中 {stat['miss']}")
    if misses:
        print("   未命中（退回 works.id，角色层会再试）：", misses[:10])
    return mapping


# ============================================================================
# ② 角色层
# ============================================================================

LAYER = re.compile(r"_\(([^()]+)\)")


def parse_tag(tag):
    """`abigail_williams_(third_ascension)_(fate)` → (`abigail_williams`, ['third_ascension','fate'])。

    ⚠️ 必须**逐层**解析，不能用 `^(.+?)_\\((.+)\\)$` ——
       那个正则对多层括号会把中间几层吞进 inner（实测拼出
       `abigail_williams_(swimsuit_foreigner)_(fate)_(fate)` 这种作品名重复的垃圾）。
    """
    layers = LAYER.findall(tag)
    subject = LAYER.sub("", tag).strip()
    return subject, layers


def split_tag(tag):
    """兼容旧调用：`houshou_marine_(1st_costume)` → ("houshou_marine", "1st_costume")。"""
    subject, layers = parse_tag(tag)
    return subject, (layers[-1] if layers else "")


def is_work_inner(inner, work_tags, works_by_name):
    """括号内容是不是「作品」而不是「形态/职阶」。"""
    low = inner.lower()
    if low in work_tags:
        return True
    # `kancolle` 这类旧缩写：查得到它在 danbooru 是不是 copyright
    rec = lookup(inner)
    if rec and rec.get("category") == 3:
        return True
    return False


def _usable(rec, cat=4):
    """这个 tag 能不能用在正文里。

    ⚠️ **`post_count > 0` 是硬门槛**，踩过的坑：
       `kaga_(kantai_collection)` 在 danbooru 里**存在**、`category=4`、
       `is_deprecated=False` —— 但 **post_count = 0**（空壳/别名残留），
       真正有投稿的是 `kaga_(kancolle)`（22249 张）。
       只校验「存在 + 分类」会把整批舰 C 角色写到模型从没见过的空 tag 上。
    """
    return bool(rec) and rec.get("category") == cat \
        and not rec.get("is_deprecated") and (rec.get("post_count") or 0) > 0


def lookup_fuzzy(subject, cat=4):
    """按角色名模糊反查：`akali_*` → `akali_(league)`。

    ⚠️ 为什么必须有这一层：`works` 表里的作品名**不等于** danbooru 角色 tag
       括号里用的缩写。实测 `akali` 的真 tag 是 `akali_(league)`（1655 投稿），
       而库里写的是 `league of legends` —— 拼 `akali_(league_of_legends)`
       在 danbooru 上是个 0 投稿空壳，模型从没见过。
       精确候选全 miss 后，按 `角色名_*` 反查、取投稿最高的 cat 4 条目。
    """
    if not subject:
        return None
    cp = _cache_path("fuzzy_" + subject)
    if os.path.exists(cp):
        try:
            with open(cp, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    url = (API + "/tags.json?search%5Bname_matches%5D="
           + urllib.parse.quote(subject + "_*")
           + f"&search%5Bcategory%5D={cat}&limit=20")
    try:
        d = http_json(url)
    except Exception as exc:
        print(f"    [ERR fuzzy] {subject}: {exc}")
        d = None
    best = None
    for x in (d or []):
        pc = int(x.get("post_count") or 0)
        if pc <= 0 or x.get("is_deprecated"):
            continue
        if best is None or pc > int(best.get("post_count") or 0):
            best = {"name": x.get("name"), "category": x.get("category"),
                    "post_count": pc, "is_deprecated": bool(x.get("is_deprecated"))}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as fh:
        json.dump(best, fh, ensure_ascii=False)
    return best


def is_work_layer(x, work_en, wtag):
    """这一层括号是不是「作品」而不是形态/职阶。

    ⚠️ **不要求投稿数 > 0**：`kancolle` 是 cat 3、投稿 0 的旧别名
       （正式名 `kantai_collection`），它仍然标示作品归属 ——
       按投稿数判会把 `kaga_(kancolle)` 拆成 `kaga (kancolle), kantai collection`（重复）。
       投稿数只用来决定「输出时用哪个名字」，见 `anima_format`。
    """
    a = str(x or "").replace("_", " ").strip().lower()
    if not a:
        return False
    b = str(work_en or "").strip().lower()
    if b and (a in b or b in a):
        return True            # `nikke` ⊂ `goddess of victory: nikke`
    if wtag and a == str(wtag).replace("_", " ").lower():
        return True
    rec = lookup(x)
    return bool(rec and rec.get("category") == 3 and not rec.get("is_deprecated"))


def anima_format(ref, work_en, wtag):
    """danbooru tag → **Anima 官方写法**（空格、角色与作品两个独立 tag）。

    官方口径见文件头。转换示例：
        texas_(arknights)                 → `texas, arknights`
        kaga_(kancolle)                   → `kaga, kantai collection`（别名换成正式名）
        ade_(nikke)                       → `ade, goddess of victory: nikke`
        ade_(agent_bunny)_(nikke)         → `ade (agent bunny), goddess of victory: nikke`
        byleth_(female)_(fire_emblem)     → `byleth (female), fire emblem`
        hou..._(1st_costume) + hololive   → `houshou marine (1st costume), hololive`

    ⚠️ 作品名两套来源：括号里那层（`kancolle` / `nikke` / `league`）通常是
       **简称或旧别名**，投稿数为 0。这时改用 `works` 表里的作品全名
       （`kantai collection` / `goddess of victory: nikke`）—— 官方示例用的
       也是全名（`oomuro sakurako, yuru yuri`）。
    """
    subject, layers = parse_tag(ref)
    subj_layers, work_layer = [], ""
    for x in layers:
        if is_work_layer(x, work_en, wtag):
            work_layer = x          # 最外层的作品层（后面的覆盖前面的）
        else:
            subj_layers.append(x)

    subj = subject
    for x in subj_layers:
        subj += f" ({x})"
    subj = re.sub(r"\s+", " ", subj.replace("_", " ")).strip()

    work = ""
    if work_layer:
        rec_w = lookup(work_layer)
        if rec_w and (rec_w.get("post_count") or 0) > 0:
            work = work_layer.replace("_", " ")     # 括号里那层本身就是正式名
    if not work:
        work = str(wtag or "").replace("_", " ") or str(work_layer or "").replace("_", " ")
    work = re.sub(r"\s+", " ", work).strip()

    if work and work.lower() == subj.lower():
        work = ""                   # 角色名与作品名相同，别重复写
    return f"{subj}, {work}" if work else subj


def build_chars(chars, work_map, works):
    print(f"\n② 核验角色层（{len(chars)} 条）…")
    work_tags = {str(v).lower() for v in work_map.values() if v}
    works_by_en = {(w.get("en") or "").lower(): w for w in works}

    def one(c):
        tag = str(c.get("tag") or "").strip()
        work_en = str(c.get("work") or "").strip().lower()
        trig = str(c.get("trigger") or tag).strip()
        subject, layers = parse_tag(tag)
        wtag = work_map.get(work_en, "")
        w = works_by_en.get(work_en) or {}
        if not wtag:
            wtag = str(w.get("id") or "").replace(" ", "_")

        def mk(ref, src, posts=0):
            """danbooru 参考 tag → Anima 写法（统一出口，别在别处拼字符串）。"""
            return (trig, {"out": anima_format(ref, work_en, wtag),
                           "ref": ref, "src": src, "posts": posts})

        # 最外层括号是作品 → 原 tag 本身已经是「角色_(形态)_(作品)」形态
        has_work_layer = bool(layers) and is_work_inner(layers[-1], work_tags, works_by_en)
        form_layers = layers[:-1] if has_work_layer else list(layers)

        # ① 原 tag 本身就是有效的 danbooru 角色 tag → 用它做参考。
        #    实测：`ade_(nikke)` 741 投稿、`houshou_marine_(1st_costume)` 4903 ——
        #    这些写法模型都认得，拿它拆分（角色 / 作品）最稳。
        rec0 = lookup(tag)
        if _usable(rec0):
            return mk(rec0["name"], "same", rec0.get("post_count", 0))

        # ② 原 tag 无效 → 按口径补作品；有形态层就双层
        #    ⚠️ 作品 tag 自身带括号时（`honkai_(series)`）不能拿来拼 ——
        #    会拼出 `fu_hua_(...)_(honkai_(series))` 这种嵌套括号。
        wtag_ok = bool(wtag) and "(" not in wtag and ")" not in wtag
        if form_layers and wtag_ok:
            fp = "".join(f"_({x})" for x in form_layers)
            cand = f"{subject}{fp}_({wtag})"
            rec = lookup(cand)
            if _usable(rec):
                return mk(rec["name"], "layered", rec.get("post_count", 0))
            return mk(cand, "layered_forced")

        cands = []
        if wtag_ok:
            cands.append((f"{subject}_({wtag})", "canon"))
        cands.append((tag, "same"))
        cands.append((subject, "bare"))

        for cand, src in cands:
            if not cand:
                continue
            rec = lookup(cand)
            # `canon` 必须是真角色（cat 4 + 有投稿）；
            # `same` / `bare` 放宽到「有投稿」即可 —— 原库 tag 语义上就是角色，
            # 个别条目在 danbooru 未分类（cat 0），例如 `chocobo`（1746 投稿）。
            ok = _usable(rec) or (
                src in ("same", "bare") and rec
                and (rec.get("post_count") or 0) > 0
                and not rec.get("is_deprecated"))
            if ok:
                return mk(rec["name"], src, rec.get("post_count", 0))

        # ④ 兜底：按角色名模糊反查（`akali_*` → `akali_(league)`）
        rec = lookup_fuzzy(subject)
        if rec:
            return mk(rec["name"], "fuzzy", rec.get("post_count", 0))

        if wtag_ok:
            return mk(f"{subject}_({wtag})", "canon_unverified")
        return mk(tag, "fallback")

    # ⚠️ 同一 trigger 可能对应**多个角色**（库里有 50 组、111 条）：
    #       `hakurei reimu, touhou` → `hakurei_reimu` 与 `hakurei_reimu_(pc-98)`
    #    引擎拿到的只有 trigger，分辨不出用户点的是哪一个。
    #    若按原顺序写入，后者会覆盖前者 —— 实测把 `hakurei reimu` 映射成了
    #    `hakurei_reimu_(pc-98)`（另一个角色，只有 1247 投稿）。
    #    所以**无括号的正牌优先**（tag 里不带 `(` 的排前面），先到先得。
    ordered = sorted(
        chars,
        key=lambda c: (1 if "(" in str(c.get("tag") or "") else 0,
                       str(c.get("tag") or "")))

    from concurrent.futures import ThreadPoolExecutor
    out = {}
    stat = {}
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        for trig, rec in ex.map(one, ordered):
            if not trig:
                continue
            done += 1
            if done % 200 == 0:
                el = time.time() - t0
                print(f"   {done}/{len(chars)}  {el:.0f}s  "
                      f"({done / max(el, 1e-6):.1f} 条/s)", flush=True)
            if trig in out:
                # trigger 撞车：保留先到的（正牌），后来的只计数不覆盖
                stat["dup_skipped"] = stat.get("dup_skipped", 0) + 1
                continue
            out[trig] = rec
            stat[rec["src"]] = stat.get(rec["src"], 0) + 1
    print("   来源分布：", dict(sorted(stat.items(), key=lambda kv: -kv[1])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--works", action="store_true", help="只核验作品层")
    ap.add_argument("--all", action="store_true", help="全量（作品 + 角色）")
    ap.add_argument("--resume", action="store_true", help="复用本地缓存")
    args = ap.parse_args()
    if not (args.works or args.all):
        ap.print_help()
        return 1

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(PRESETS, "anima_v4_characters.json"), "r",
              encoding="utf-8") as fh:
        d = json.load(fh)
    chars, works = d.get("characters") or [], d.get("works") or []

    work_map = build_works(chars, works)
    if args.works:
        print("\n作品层映射（前 20）：")
        for k, v in list(work_map.items())[:20]:
            print(f"   {k:42s} → {v}")
        return 0

    tag_map = build_chars(chars, work_map, works)

    # 单独核验用户自定义角色（不在构建产物里，但会进正文）
    up = os.path.join(PRESETS, "user_characters.json")
    if os.path.exists(up):
        with open(up, "r", encoding="utf-8") as fh:
            u = json.load(fh)
        extra = []
        for k, v in (u.get("edits") or {}).items():
            if v.get("trigger"):
                extra.append({"tag": k, "work": v.get("work") or "",
                              "trigger": v["trigger"]})
        for v in (u.get("custom") or []):
            if v.get("trigger"):
                extra.append({"tag": v.get("tag") or "", "work": v.get("work") or "",
                              "trigger": v["trigger"]})
        if extra:
            print(f"\n③ 核验用户自定义角色（{len(extra)} 条）…")
            tag_map.update(build_chars(extra, work_map, works))

    payload = {
        "_meta": {
            "schema": "anima-v5/char-output-tags@2",
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "danbooru.donmai.us/tags.json 逐条核验",
            "spec": ("Anima 官方（circlestone-labs/Anima 模型卡 + Anima-2.9B 作者）："
                     "\"Use lowercase for tags, and spaces instead of underscores. "
                     "Score tags are the only tags that use underscores.\" / "
                     "\"Don't use underscore except for score tags.\" / "
                     "\"Characters should (think 'must') be follow by their "
                     "series/copyrights\" / 顺序 [1girl][character][series]"
                     "[artist][general]。"),
            "note": ("`out` = 直接写进正文的 Anima 写法（`texas, arknights`）；"
                     "`ref` = 它来自的 danbooru tag（`texas_(arknights)`，仅作溯源）。"
                     "`src`：same=原 tag 有效并沿用其拆分 / canon=按 角色_(作品) 拼且核验命中 / "
                     "layered=形态+作品双层命中 / layered_forced=双层、danbooru 查不到 / "
                     "fuzzy=模糊反查命中（`akali` → `akali_(league)`）/ "
                     "canon_unverified=danbooru 查不到仍按口径拼 / bare=只剩角色名 / "
                     "fallback=全部未命中沿用原 tag。"
                     "⚠️ 只影响正文输出，**不改库内 trigger**；"
                     "表缺失或条目未命中时引擎回退旧逻辑（零漂移）。"),
        },
        "tags": tag_map,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"\n✅ 写入 {OUT_PATH}（{len(tag_map)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
