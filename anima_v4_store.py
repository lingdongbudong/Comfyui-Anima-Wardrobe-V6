# -*- coding: utf-8 -*-
"""用户数据的持久化：命名预设 与 角色收藏。

两个文件都放在插件目录的 `presets/` 下：

    presets/user_presets.json    命名预设（一组配置快照）
    presets/user_favorites.json  收藏的角色 tag

为什么落在插件目录而不是浏览器 localStorage（2026-10-03 与用户确认）：
  · 换浏览器 / 清缓存后还在；
  · 能直接备份、能连同插件一起发给别人；
  · 与其它 presets 数据同源，好管理。

⚠️⚠️ 写盘一律**原子替换**（先写同目录 .tmp，再 os.replace 顶掉目标）。
    直接 `open(path, "w")` 是"先截断、后写"，中途任何异常都会留下一个
    0 字节文件 —— 用户的预设和收藏就全没了。
    这个坑我在本项目的工具脚本里踩过一次（见 ARCHIVE 坑⑥：
    `newline` 参数非法 → ValueError → 文件已被截断成空），存储层必须
    从一开始就防住。

⚠️ 所有读操作都**容错**：文件不存在 → 返回空结构；JSON 损坏 → 把坏文件
    另存为 `.broken` 后重新开始，绝不抛异常把面板带崩。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(_HERE, "presets")

PRESETS_FILE = os.path.join(PRESET_DIR, "user_presets.json")
FAVORITES_FILE = os.path.join(PRESET_DIR, "user_favorites.json")

PRESET_SCHEMA = "anima-wardrobe-v4/presets@1"
FAVORITE_SCHEMA = "anima-wardrobe-v4/favorites@1"

MAX_PRESETS = 60            # 预设条数上限，防止无限增长
MAX_NAME_LEN = 40
MAX_FAVORITES = 3000

# 所有写操作串行化。文件都很小，一把锁足够，不需要更细的粒度。
_lock = threading.Lock()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write(path, data):
    """原子写：先写同目录的 .tmp，再 os.replace 顶掉目标。

    ⚠️ 不要改成 `open(path, "w")` —— 那是先截断后写，写一半崩了就是
       0 字节文件。`.tmp` + `os.replace` 在 Windows 上同样是原子的。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())      # 真正落盘，而不是停在 OS 缓存里
        except OSError:
            pass
    os.replace(tmp, path)


def _read(path):
    """读 JSON。任何异常都降级成空 dict，坏文件另存 `.broken`。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            raise ValueError("顶层不是对象")
        return d
    except Exception:
        # 宁可丢这一份坏文件，也不能让它把面板读崩 ——
        # 另存一份便于事后排查，而不是直接删掉。
        try:
            os.replace(path, path + ".broken")
        except OSError:
            pass
        return {}


def _clean_name(name):
    return " ".join(str(name or "").split())[:MAX_NAME_LEN].strip()


def _summary(p):
    """一行摘要，给下拉列表显示用。"""
    lv = str(p.get("level") or "").strip()
    n = len([t for t in str(p.get("selected_roles") or "").split("|") if t.strip()])
    w = p.get("weights") if isinstance(p.get("weights"), dict) else {}
    wn = (len(w.get("sections") or {}) + len(w.get("fields") or {})
          + sum(len(v or {}) for v in (w.get("options") or {}).values()))
    bits = [lv or "—"]
    if n:
        bits.append(f"{n} 位角色")
    if wn:
        bits.append(f"权重 {wn} 项")
    if str(p.get("force_role") or "").strip():
        bits.append("手填角色")
    return " · ".join(bits)


# ============================================================================
# 预设
# ============================================================================

def list_presets():
    """全部预设（含完整内容，前端一次拿全，点加载零额外请求）。"""
    doc = _read(PRESETS_FILE)
    presets = doc.get("presets")
    if not isinstance(presets, dict):
        return []
    out = []
    for name, p in presets.items():
        if not isinstance(p, dict):
            continue
        out.append({
            "name": name,
            "saved_at": str(p.get("saved_at") or ""),
            "summary": _summary(p),
            # saved_at 是元信息，不属于"配置"，不往回传
            "data": {k: v for k, v in p.items() if k != "saved_at"},
        })
    out.sort(key=lambda x: x["saved_at"], reverse=True)
    return out


def save_preset(name, data):
    """保存（同名即覆盖）。返回最新列表。"""
    name = _clean_name(name)
    if not name:
        raise ValueError("预设名不能为空")
    if not isinstance(data, dict):
        raise ValueError("预设内容必须是一个对象")
    with _lock:
        doc = _read(PRESETS_FILE)
        presets = doc.get("presets")
        if not isinstance(presets, dict):
            presets = {}
        if name not in presets and len(presets) >= MAX_PRESETS:
            raise ValueError(f"预设数量已达上限 {MAX_PRESETS}，请先删掉几个")
        entry = dict(data)
        entry["saved_at"] = _now()
        presets[name] = entry
        _atomic_write(PRESETS_FILE, {
            "_meta": {"schema": PRESET_SCHEMA, "updated_at": entry["saved_at"],
                      "count": len(presets)},
            "presets": presets,
        })
    return list_presets()


def delete_preset(name):
    """删除。删不存在的名字不报错（幂等），返回最新列表。"""
    name = _clean_name(name)
    with _lock:
        doc = _read(PRESETS_FILE)
        presets = doc.get("presets")
        if isinstance(presets, dict) and name in presets:
            del presets[name]
            _atomic_write(PRESETS_FILE, {
                "_meta": {"schema": PRESET_SCHEMA, "updated_at": _now(),
                          "count": len(presets)},
                "presets": presets,
            })
    return list_presets()


# ============================================================================
# 收藏
# ============================================================================

def list_favorites():
    """收藏的角色 tag 列表（按收藏先后）。"""
    doc = _read(FAVORITES_FILE)
    fav = doc.get("favorites")
    if not isinstance(fav, list):
        return []
    return [t for t in fav if isinstance(t, str) and t.strip()]


def toggle_favorite(tag, on=None):
    """收藏 / 取消收藏。`on=None` 时按当前状态取反。返回最新列表。

    ⚠️ 幂等：重复收藏同一个不会产生两条。
    """
    tag = str(tag or "").strip()
    if not tag:
        raise ValueError("角色 tag 不能为空")
    with _lock:
        cur = list_favorites()
        has = tag in cur
        want = (not has) if on is None else bool(on)
        if want and not has:
            if len(cur) >= MAX_FAVORITES:
                raise ValueError(f"收藏已达上限 {MAX_FAVORITES}")
            cur.append(tag)
        elif not want and has:
            cur.remove(tag)
        _atomic_write(FAVORITES_FILE, {
            "_meta": {"schema": FAVORITE_SCHEMA, "updated_at": _now(),
                      "count": len(cur)},
            "favorites": cur,
        })
    return cur


# ============================================================================
# 场景收藏（v3.43.0）
# ============================================================================
#
# ⚠️ **另开一个文件**，不塞进 user_favorites.json：
#   · 那份文件的 schema 是 `favorites@1`，值域是**角色 tag**。混进 `sc_xxx`
#     会让 rules.favorite_role_keys() 拿它去角色库里比对，多出一批永远命中
#     不了的脏 key（表现是「仅收藏内随机」莫名变空池），且难以回溯；
#   · 分开存，用户能单独备份 / 迁移其中一份，互不牵连。
#
# 存的是**场景 id**（`sc_xxx`），不是显示名 —— 显示名会改（v3.33 改过一次），
# 拿名字收藏一改名就全丢。id 是稳定的。
#
# 数量上限按库总量（250 条）留足余量；超过就该考虑了，不该静默丢弃。

SCENE_FAV_FILE = os.path.join(PRESET_DIR, "user_scene_favorites.json")
SCENE_FAV_SCHEMA = "anima-wardrobe-v4/scene-favorites@1"
MAX_SCENE_FAVORITES = 600


def _scene_id_ok(sid):
    """场景 id 只允许 `sc_` + 小写字母数字下划线。

    与库里 250 条 id 的形态一致（最长 28 字符，这里放宽到 83）。
    拦掉手改文件和脏请求，避免把任意字符串写进收藏文件。
    """
    return bool(re.fullmatch(r"sc_[a-z0-9_]{1,80}", str(sid or "")))


def list_scene_favorites():
    """收藏的场景 id 列表（按收藏先后）。坏条目静默滤掉，不抛异常。"""
    doc = _read(SCENE_FAV_FILE)
    fav = doc.get("favorites")
    if not isinstance(fav, list):
        return []
    out = []
    for t in fav:
        if isinstance(t, str) and _scene_id_ok(t) and t not in out:
            out.append(t)
    return out


def toggle_scene_favorite(sid, on=None):
    """收藏 / 取消收藏一个场景。`on=None` 时按当前状态取反。返回最新列表。

    ⚠️ 幂等：重复收藏同一条不会产生两条。
    ⚠️ 取消一条**本来就不在**的收藏也返回成功（不报错）——
       面板在旧快照上点两下不该弹红字。
    """
    sid = str(sid or "").strip()
    if not _scene_id_ok(sid):
        raise ValueError(f"场景 id 不合法：{sid!r}")
    with _lock:
        cur = list_scene_favorites()
        has = sid in cur
        want = (not has) if on is None else bool(on)
        if want and not has:
            if len(cur) >= MAX_SCENE_FAVORITES:
                raise ValueError(f"场景收藏已达上限 {MAX_SCENE_FAVORITES}")
            cur.append(sid)
        elif not want and has:
            cur.remove(sid)
        _atomic_write(SCENE_FAV_FILE, {
            "_meta": {"schema": SCENE_FAV_SCHEMA, "updated_at": _now(),
                      "count": len(cur)},
            "favorites": cur,
        })
    return cur


# ============================================================================
# 作品 IP 收藏（v5.0.4）
# ============================================================================
#
# ⚠️ 又是**另开一个文件**，理由与场景收藏同源：
#   · `user_favorites.json` 的值域是**角色 tag**；
#   · `user_scene_favorites.json` 的值域是**场景 id**（sc_xxx）；
#   · 这里是**作品 slug**（kantai_collection / goddess_of_victory_nikke）。
#   三份值域完全不同的东西混在一个 favorites 数组里，任何一个消费方
#   （「仅收藏内随机」、场景筛选中标、作品置顶）都会拿到一批永远命中不了的
#   脏 key，而且难以回溯是谁写的。
#
# 存 slug 而不是中文名：显示名会改（`舰队Collection` 可能被改成 `舰队收藏`），
# 名字一改收藏就全丢；slug 是 build_characters 的稳定主键。
#
# 上限 200：作品库现在 284 个，收藏一半已经是"重度用户"的边界，
# 超过就该考虑用搜索而不是收藏当导航了。

WORK_FAV_FILE = os.path.join(PRESET_DIR, "user_work_favorites.json")
WORK_FAV_SCHEMA = "anima-wardrobe-v4/work-favorites@1"
MAX_WORK_FAVORITES = 200


def _work_slug_ok(slug):
    """作品 slug 只允许小写字母 / 数字 / 下划线 / 冒号。

    ⚠️ 冒号是必须的：`persona_5:_the_phantom_x` 就在真实库里（284 个作品里
       唯一一个带冒号的）。用 `[a-z0-9_]` 会把它判成非法 —— 表现为
       「这个作品点收藏永远失败」，而且只有它一个，很难发现。
    长度放宽到 80（实际最长 57）。
    """
    return bool(re.fullmatch(r"[a-z0-9_:]{1,80}", str(slug or "")))


def list_work_favorites():
    """收藏的作品 slug 列表（按收藏先后）。坏条目静默滤掉，不抛异常。"""
    doc = _read(WORK_FAV_FILE)
    fav = doc.get("favorites")
    if not isinstance(fav, list):
        return []
    out = []
    for t in fav:
        if isinstance(t, str) and _work_slug_ok(t) and t not in out:
            out.append(t)
    return out


def toggle_work_favorite(slug, on=None):
    """收藏 / 取消收藏一个作品 IP。`on=None` 时按当前状态取反。返回最新列表。

    ⚠️ 幂等：重复收藏同一个不会产生两条。
    ⚠️ 取消一条本来就不在的收藏也返回成功（面板在旧快照上连点两下不该弹红字）。
    """
    slug = str(slug or "").strip()
    if not _work_slug_ok(slug):
        raise ValueError(f"作品 slug 不合法：{slug!r}")
    with _lock:
        cur = list_work_favorites()
        has = slug in cur
        want = (not has) if on is None else bool(on)
        if want and not has:
            if len(cur) >= MAX_WORK_FAVORITES:
                raise ValueError(f"收藏的作品已达上限 {MAX_WORK_FAVORITES}")
            cur.append(slug)
        elif not want and has:
            cur.remove(slug)
        _atomic_write(WORK_FAV_FILE, {
            "_meta": {"schema": WORK_FAV_SCHEMA, "updated_at": _now(),
                      "count": len(cur)},
            "favorites": cur,
        })
    return cur


# ============================================================================
# 用户角色覆盖层（v3.4.0：角色编辑 + 自定义角色）
# ============================================================================
#
# 为什么必须另存一层，而不是直接改 presets/anima_v4_characters.json：
#   那份 2687 条的角色库是 tools/build_characters.py 的**联网重建产物**，
#   重跑一次就把手改内容全冲掉。所以用户的新增/编辑一律落在
#   presets/user_characters.json，加载时"内置 + 覆盖层"合并（见 rules._merge_overlay）。
#   顺带保住 tests/check_v3_chars.py 的合规断言不被用户数据打破。
#
# 结构：
#   {
#     "_meta":  {...},
#     "edits":  {"<内置 tag>": {"trigger"|"zh"|"work"|"thumb": "…",
#                               "tags": ["…"]}},        # 只存被改的键
#     "custom": [ {完整角色记录, "src": "user"} ]
#   }
#
# ⚠️ `tags` 是**列表**，与其余字符串键的语义不同（见 _norm_tags / _parse_tags）；
#    "空列表" 的语义 = 清除这一项覆盖、回退内置标签（与"中文名留空"一致）。
#
# ⚠️ 图片一律另放 presets/user_thumbs/，**不放进 presets/thumbs/**：
#    tools/fetch_character_thumbs.py 的 verify_existing() 会遍历 thumbs/
#    删掉它判定不合格的图，重建缩略图时也可能清目录 —— 用户上传的图
#    放在那里等于随时可能被脚本误删。

# 内置角色库（v3.18.6 起支持从面板删除单个角色）
# ⚠️ 它是 tools/build_characters.py 的产物；删除写入 tombstone，重跑脚本会重建回来。
CHARACTERS_FILE = os.path.join(PRESET_DIR, "anima_v4_characters.json")
RETIRED_DIR = os.path.join(PRESET_DIR, "_retired")
RETIRED_CHARS_FILE = os.path.join(RETIRED_DIR, "anima_v4_characters.removed.json")

USER_CHARS_FILE = os.path.join(PRESET_DIR, "user_characters.json")
USER_THUMB_DIR = os.path.join(PRESET_DIR, "user_thumbs")
USER_CHARS_SCHEMA = "anima-wardrobe-v4/user-characters@1"

EDITABLE_KEYS = ("trigger", "zh", "work", "thumb", "tags", "hair", "eyes")

MAX_USER_CHARS = 500                 # 自定义角色条数上限
MAX_TRIGGER_LEN = 120
MAX_ZH_LEN = 24
MAX_WORK_LEN = 60
MAX_TAGS = 40                        # 单个角色的特征标签条数上限
MAX_TAG_LEN = 40                     # ⚠️ 引擎侧阈值：见下方 _check_tag_item
MAX_THUMB_BYTES = 2 * 1024 * 1024    # 上传图片上限 2MB
THUMB_SIZE = 160                     # 缩略图最长边（与面板卡片一致）

# danbooru 风格：小写字母 / 数字 / 空格 / , _ - : ( ) ' .
# ⚠️ 刻意**不收大写与中文** —— 角色标签是要喂给模型的英文 tag，
#    混进大写或中文在 SD/ANIMA 上匹配不到，属于"看着能用其实没用"。
_TRIGGER_RE = re.compile(r"^[a-z0-9][a-z0-9 ,_:()'.\-]*$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# 特征标签的字符集比 trigger **宽一点**（实测内置数据里出现过 . ' - : ( ) / ! +），
# 但同样不收大写与中文；逗号在标签里是分隔符、不是内容。
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _\-:'()\./!+]*$")


def _clean_text(v, maxlen=None):
    """折掉多余空白（可选截断）。不做字符集校验（那是 _check_trigger 的事）。

    ⚠️ trigger 一律**不截断**：静默把用户的长标签切掉，他会以为存进去了，
       实际词条已经变了 —— 这种"看着能用其实没用"最难排查。宁可报错。
    """
    s = " ".join(str(v or "").split())
    return s[:maxlen].strip() if maxlen else s.strip()


def _check_trigger(t):
    if not t:
        raise ValueError("角色标签不能为空")
    if len(t) > MAX_TRIGGER_LEN:
        raise ValueError(f"角色标签过长（上限 {MAX_TRIGGER_LEN} 字符）")
    if not _TRIGGER_RE.match(t):
        raise ValueError("角色标签只能用英文小写字母、数字，以及空格 , _ - : ( ) ' .")
    return t


# ── 特征标签（tags：列表型字段）──────────────────────────────────────

def _split_tag_source(v):
    """接受「数组」或「逗号/换行分隔的字符串」两种输入。

    前端是一行一个输入框（传数组）；但用户完全可能整串粘进来，
    所以字符串形态要能拆 —— 逗号在 danbooru 里是分隔符，不是标签内容。
    """
    if v is None:
        return []
    if isinstance(v, str):
        return re.split(r"[,\n]", v)
    if isinstance(v, (list, tuple)):
        return list(v)
    raise ValueError("特征标签必须是一组字符串")


def _norm_tags(v):
    """读盘用的**容错**归一：小写、折空白、去重、丢弃超长项、截到上限。

    ⚠️ 这里绝不抛异常 —— 用户手改坏了 JSON，代价只能是"这条标签没生效"，
       不能是"整个面板打不开"。
    """
    out, seen = [], set()
    for it in _split_tag_source(v) if v is not None else []:
        t = " ".join(str(it or "").split()).lower()
        if not t or len(t) > MAX_TAG_LEN or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


def _check_tag_item(t):
    """单个标签的**严格**校验（保存时用）。"""
    if len(t) > MAX_TAG_LEN:
        # 这条不是拍脑袋定的：anima_v4_rules.appearance_for() 会
        # `if len(t) > 40: continue` —— 存进去也永远不会被采用。
        # 与其让用户"加了没反应"，不如直接拒并说清原因。
        raise ValueError(
            f"标签「{t[:16]}…」超过 {MAX_TAG_LEN} 字符 —— "
            f"引擎会忽略超过 {MAX_TAG_LEN} 字符的特征标签，存了也不生效")
    if not _TAG_RE.match(t):
        raise ValueError(
            f"标签「{t}」只能用小写英文、数字，以及空格 _ - : ' ( ) . / ! +")
    return t


def _parse_tags(v):
    """保存用的**严格**解析。返回小写去重后的列表。

    ⚠️ 空列表是**合法**值，语义由调用方决定（内置角色 = 回退内置标签）。
    """
    out, seen = [], set()
    for it in _split_tag_source(v):
        t = " ".join(str(it or "").split()).lower()
        if not t or t in seen:
            continue
        if len(out) >= MAX_TAGS:
            raise ValueError(f"特征标签最多 {MAX_TAGS} 条")
        seen.add(t)
        out.append(_check_tag_item(t))
    return out


def _drop_empty(patch, list_keys=("tags",)):
    """过滤覆盖项：字符串键丢空串，列表键丢空列表。

    空值的语义是"清除这一项覆盖、回退内置值"，所以**不能落进文件**
    （落进去会让新写的编辑覆盖不掉旧的）。
    """
    out = {}
    for k, v in patch.items():
        if k in list_keys:
            if v:
                out[k] = v
        elif str(v or "").strip():
            out[k] = v
    return out


def user_thumb_slug(tag):
    """角色 tag → 用户图片 slug。

    ⚠️ 内置角色的图**不能**覆盖 presets/thumbs/ 里的原件（重建会丢），
       所以一律加 `user_` 前缀；自定义角色的 tag 本身就是 `user_` 开头，
       直接用。这样两条来源永不撞名。
    """
    tag = str(tag or "").strip()
    if not tag:
        return ""
    return tag if tag.startswith("user_") else "user_" + tag


def make_user_tag(trigger, taken=None):
    """按 trigger 生成唯一的自定义角色 tag（`user_` 前缀）。

    taken 由调用方传入"当前已经存在的全部 tag"（含内置）—— 存储层
    看不到内置角色库，所以这一步只能让调用方给全。
    """
    taken = {str(t).lower() for t in (taken or ())}
    base = _SLUG_RE.sub("_", (trigger or "").split(",")[0].strip().lower()).strip("_")
    base = base or "char"
    cand = ("user_" + base)[:120].strip("_")
    if cand.lower() not in taken:
        return cand
    for i in range(2, 10000):
        alt = f"{cand}_{i}"[:120]
        if alt.lower() not in taken:
            return alt
    return cand


def _norm_overlay(doc) -> dict:
    """把任意读到的结构归一成 {"edits": {...}, "custom": [...]}。

    ⚠️ 只保留 EDITABLE_KEYS 里的键、且**丢弃空值** —— 空值语义是
       "清除这一项覆盖、回退内置值"，所以不该落进文件（落进去会让
       新写的编辑覆盖不掉旧的）。
    """
    doc = doc if isinstance(doc, dict) else {}
    edits = doc.get("edits")
    edits = edits if isinstance(edits, dict) else {}
    clean_edits = {}
    for tag, patch in edits.items():
        if not isinstance(patch, dict):
            continue
        keep = {}
        for k, v in patch.items():
            if k not in EDITABLE_KEYS:
                continue
            if k == "tags":                 # 列表型：单独归一（空列表 = 清除覆盖）
                lst = _norm_tags(v)
                if lst:
                    keep[k] = lst
                continue
            s = str(v or "").strip()
            if s:
                keep[k] = s
        if keep:
            clean_edits[str(tag)] = keep
    raw_custom = doc.get("custom")
    custom = []
    if isinstance(raw_custom, list):
        for c in raw_custom:
            if isinstance(c, dict) and str(c.get("tag") or "").strip():
                custom.append(c)
    return {"edits": clean_edits, "custom": custom}


def load_user_characters() -> dict:
    """读用户角色覆盖层。文件缺席/损坏 → 空结构（_read 会容错）。"""
    return _norm_overlay(_read(USER_CHARS_FILE))


def _save_overlay(ov):
    _atomic_write(USER_CHARS_FILE, {
        "_meta": {"schema": USER_CHARS_SCHEMA, "updated_at": _now(),
                  "edits": len(ov["edits"]), "custom": len(ov["custom"])},
        "edits": ov["edits"],
        "custom": ov["custom"],
    })


def save_user_character(mode, tag, fields, taken_tags=None):
    """新增（mode=create）或编辑（mode=edit）一个角色。

    返回 (记录, 最新覆盖层)。

    · create —— 生成 `user_` 前缀的唯一 tag，整条追加进 custom；
    · edit   —— 先找 custom（直接改记录），找不到就当内置处理，写进 edits。
                触发词是必填的；中文名/作品/特征标签**留空即"清除覆盖、回退内置值"**，
                这与 _norm_overlay 丢弃空值的语义一致。
                ⚠️ 特征标签是**列表**：非空 = 整条替换内置 tags；空 = 回退内置。
                   顺序与大小写按用户输入归一（小写去重）。
    """
    mode = str(mode or "edit").strip().lower()
    fields = fields if isinstance(fields, dict) else {}
    # trigger **不截断**（见 _clean_text）：超长直接由 _check_trigger 报错
    trigger = _check_trigger(_clean_text(fields.get("trigger")))
    zh = _clean_text(fields.get("zh"), MAX_ZH_LEN)
    work = _clean_text(fields.get("work"), MAX_WORK_LEN)
    tags = _parse_tags(fields.get("tags"))
    # v3.44.4：发色 / 瞳色也要能改（空 = 清除覆盖、回退内置值，与 zh/work/tags 同语义）。
    # ⚠️ 为什么必须补这一层：`appearance_for()` 的取值优先级是
    #    ① APPEAR 表 → ② 角色库 `hair`/`eyes` 字段 → ③ 官方特征 tags。
    #    只覆盖 tags 打不过 ② —— 内置库把 `hair` 写成 `brown` 时，
    #    就算把 tags 里的 `brown hair` 删掉，出图仍然是 `brown hair`
    #    （2026-10-27 实测：明日香 orange/brown 冲突，改完 tags 无效）。
    hair = _clean_text(fields.get("hair"), 40)
    eyes = _clean_text(fields.get("eyes"), 40)

    with _lock:
        ov = load_user_characters()

        if mode == "create":
            if len(ov["custom"]) >= MAX_USER_CHARS:
                raise ValueError(f"自定义角色已达上限 {MAX_USER_CHARS}，请先删掉几个")
            taken = set(taken_tags or ())
            taken |= {str(c.get("tag") or "").lower() for c in ov["custom"]}
            taken |= {str(t).lower() for t in ov["edits"]}
            new_tag = make_user_tag(trigger, taken)
            rec = {
                "tag": new_tag,
                "name": trigger.split(",")[0].strip(),
                "zh": zh,
                "work": work,
                "trigger": trigger,
                "pop": 0,
                "hair": hair,
                "eyes": eyes,
                # ⚠️ 自定义角色**没有内置标签**可回退 → 空列表就是"它没有特征标签"，
                #    而不是"清除覆盖"（覆盖层的空值语义只对 edits 成立）。
                "tags": tags,
                "src": "user",
                "thumb": new_tag,          # 图 slug == tag；没上传图时退化成首字母色块
            }
            ov["custom"].append(rec)
            _save_overlay(ov)
            return rec, ov

        tag = str(tag or "").strip()
        if not tag:
            raise ValueError("缺少角色 tag")

        for c in ov["custom"]:
            if c.get("tag") == tag:
                c["trigger"] = trigger
                c["name"] = trigger.split(",")[0].strip()
                c["zh"] = zh
                c["work"] = work
                c["tags"] = tags           # 自定义角色：直接改记录（空 = 真的没有标签）
                if hair:
                    c["hair"] = hair
                if eyes:
                    c["eyes"] = eyes
                if not str(c.get("thumb") or "").strip():
                    c["thumb"] = tag
                _save_overlay(ov)
                return dict(c), ov

        cur = dict(ov["edits"].get(tag) or {})
        cur["trigger"] = trigger
        cur["zh"] = zh          # "" = 清除覆盖
        cur["work"] = work
        cur["tags"] = tags      # [] = 清除覆盖、回退内置标签
        cur["hair"] = hair      # "" = 清除覆盖、回退内置发色
        cur["eyes"] = eyes      # "" = 清除覆盖、回退内置瞳色
        cur = _drop_empty(cur)
        if cur:
            ov["edits"][tag] = cur
        else:
            ov["edits"].pop(tag, None)
        _save_overlay(ov)
        return {"tag": tag, **cur}, ov


def delete_user_character(tag):
    """删除一个**自定义**角色（连带删掉它上传的图）。内置角色不在 custom 里。

    返回最新覆盖层。tag 不在 custom 里 → 抛错（内置角色该走「恢复默认」）。
    """
    tag = str(tag or "").strip()
    if not tag:
        raise ValueError("缺少角色 tag")
    with _lock:
        ov = load_user_characters()
        before = len(ov["custom"])
        ov["custom"] = [c for c in ov["custom"] if c.get("tag") != tag]
        if len(ov["custom"]) == before:
            raise ValueError("只能删除自定义角色（内置角色请用「恢复默认」）")
        _save_overlay(ov)
    _delete_user_thumb(user_thumb_slug(tag))
    return ov


def delete_character(tag):
    """删除一个角色：自定义 → 删记录；**内置 → 从角色库物理移除**（v3.18.6）。

    返回 (kind, 最新覆盖层)，kind ∈ {"custom", "builtin"}。

    ⚠️ 内置删除是**写盘操作**：先把完整记录记进 tombstone
       （`presets/_retired/anima_v4_characters.removed.json`），再原子写回
       `presets/anima_v4_characters.json`。想找回就往 tombstone 里翻。
    ⚠️ 重跑 `tools/build_characters.py` 会把内置库整体重建 → 被删的会回来。
       那是刻意的：该脚本的语义是"从上游重新拉一遍"，不该记住本地删除。
    """
    tag = str(tag or "").strip()
    if not tag:
        raise ValueError("缺少角色 tag")
    with _lock:
        ov = load_user_characters()
        before = len(ov["custom"])
        ov["custom"] = [c for c in ov["custom"] if c.get("tag") != tag]
        if len(ov["custom"]) != before:
            _save_overlay(ov)
            kind = "custom"
        else:
            kind = _delete_builtin_character(tag)
            ov["edits"].pop(tag, None)
            _save_overlay(ov)
    if kind == "custom":
        _delete_user_thumb(user_thumb_slug(tag))
    return kind, ov


def _delete_builtin_character(tag):
    """从内置角色库移除一条，并记 tombstone。返回 "builtin"。"""
    doc = _read(CHARACTERS_FILE)
    chars = doc.get("characters")
    if not isinstance(chars, list) or not chars:
        raise ValueError("角色库文件不可读，无法删除")
    idx = None
    key = tag.strip().lower()
    for i, c in enumerate(chars):
        # ⚠️ 两种都能删：面板传的是**记录 tag**（`klee_(genshin_impact)`），
        #    而 selected_roles 里存的是 **trigger**（`klee, genshin impact`）。
        #    只认其中一种就会出现"删了没反应 / 报找不到"。
        if (str(c.get("tag") or "").strip().lower() == key
                or str(c.get("trigger") or "").strip().lower() == key):
            idx = i
            break
    if idx is None:
        raise ValueError("找不到这个角色（可能已经被删掉）")
    rec = chars.pop(idx)

    ret = _read(RETIRED_CHARS_FILE)
    removed = ret.get("removed")
    if not isinstance(removed, list):
        removed = []
    removed.append({"tag": tag, "removed_at": _now(), "record": rec})
    _atomic_write(RETIRED_CHARS_FILE, {
        "_meta": {"schema": "anima-wardrobe-v4/characters-retired@1",
                  "updated_at": _now(), "removed": len(removed)},
        "removed": removed,
    })

    doc["characters"] = chars
    _atomic_write(CHARACTERS_FILE, doc)
    return "builtin"


def delete_characters(tags):
    """批量删除。逐个走 `delete_character`，返回 (成功 tags, 失败明细, 覆盖层)。"""
    ok, bad = [], []
    for t in (tags or []):
        try:
            _kind, _ov = delete_character(t)
            ok.append(str(t))
        except ValueError as exc:
            bad.append({"tag": str(t), "error": str(exc)})
    return ok, bad, load_user_characters()


def reset_user_character(tag):
    """把**内置**角色恢复到内置原值（清掉 edits 项 + 删掉用户换的图）。

    自定义角色不能走这里（它不是"覆盖"，删掉就没了）→ 抛错让调用方改用删除。
    """
    tag = str(tag or "").strip()
    if not tag:
        raise ValueError("缺少角色 tag")
    with _lock:
        ov = load_user_characters()
        if any(c.get("tag") == tag for c in ov["custom"]):
            raise ValueError("这是自定义角色，请用「删除」而不是「恢复默认」")
        ov["edits"].pop(tag, None)
        _save_overlay(ov)
    _delete_user_thumb(user_thumb_slug(tag))
    return ov


# ── 用户图片 ─────────────────────────────────────────────────────────

def _sniff_ext(raw):
    """从文件头猜图片格式。认不出返回 None。"""
    b = bytes(raw[:16])
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


def _encode_thumb(raw):
    """把上传图压成 160px 的 webp。没装 Pillow 就原样存（面板三种都认）。

    ⚠️ 没 Pillow 时必须**先嗅探格式再决定扩展名** —— 直接按 .webp 存一个
       png 字节流，浏览器可能仍能显示（靠嗅探），但 /thumb 路由返回的
       Content-Type 会说谎，某些严格环境会当成下载。
    """
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(bytes(raw)))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=90, method=4)
        return "webp", buf.getvalue()
    except Exception:
        ext = _sniff_ext(raw)
        if ext not in ("png", "jpg", "webp"):
            raise ValueError("无法识别的图片格式（只支持 PNG / JPG / WEBP）")
        return ext, bytes(raw)


def _write_thumb_file(slug, raw):
    """原子落盘 + 清掉同 slug 的其它扩展名（避免路由先命中旧图）。"""
    if not _SLUG_SAFE_RE.fullmatch(slug or ""):
        raise ValueError("角色 tag 不合法，无法生成图片名")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise ValueError("图片内容为空")
    if len(raw) > MAX_THUMB_BYTES:
        raise ValueError(f"图片过大（上限 {MAX_THUMB_BYTES // 1024 // 1024}MB）")
    os.makedirs(USER_THUMB_DIR, exist_ok=True)
    fmt, data = _encode_thumb(raw)
    dst = os.path.join(USER_THUMB_DIR, f"{slug}.{fmt}")
    tmp = dst + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, dst)
    for ext in ("webp", "jpg", "png"):
        if ext == fmt:
            continue
        p = os.path.join(USER_THUMB_DIR, f"{slug}.{ext}")
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    return fmt


_SLUG_SAFE_RE = re.compile(r"[a-z0-9_]{1,120}")


def _delete_user_thumb(slug):
    for ext in ("webp", "jpg", "png"):
        p = os.path.join(USER_THUMB_DIR, f"{slug}.{ext}")
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def set_user_thumb(tag, raw):
    """保存用户上传的预览图，并把指针写进覆盖层。返回图片 slug。

    自定义角色：直接更新它记录的 `thumb`；
    内置角色：写进 edits[tag]["thumb"]（与 trigger 等覆盖项共用一条）。
    """
    tag = str(tag or "").strip()
    if not tag:
        raise ValueError("缺少角色 tag")
    slug = user_thumb_slug(tag)
    with _lock:
        _write_thumb_file(slug, raw)
        ov = load_user_characters()
        for c in ov["custom"]:
            if c.get("tag") == tag:
                c["thumb"] = slug
                _save_overlay(ov)
                return slug
        cur = dict(ov["edits"].get(tag) or {})
        cur["thumb"] = slug
        ov["edits"][tag] = cur
        _save_overlay(ov)
    return slug


def clear_user_thumb(tag):
    """清掉用户上传的图，回到内置图（内置）或首字母色块（自定义）。"""
    tag = str(tag or "").strip()
    if not tag:
        raise ValueError("缺少角色 tag")
    slug = user_thumb_slug(tag)
    with _lock:
        ov = load_user_characters()
        for c in ov["custom"]:
            if c.get("tag") == tag:
                c["thumb"] = tag
                _save_overlay(ov)
                break
        else:
            cur = dict(ov["edits"].get(tag) or {})
            cur.pop("thumb", None)
            if cur:
                ov["edits"][tag] = cur
            else:
                ov["edits"].pop(tag, None)
            _save_overlay(ov)
    _delete_user_thumb(slug)
    return slug


# ══════════════════════════════════════════════════════════════════════════
# 角色服装预设（v5.0.2，用户 2026-11-02）
#
# 「角色编辑界面增加服装预设，启用后优先于随机权重，锁定人物服装」。
#
# ⚠️ 数据放 `presets/character_outfits.json`，**不进 user_characters.json**：
#    服装是多套列表 + 启用态，和角色覆盖层（edits/custom）结构完全不同；
#    混在一起会被 `_norm_overlay` / `_save_overlay` 重建掉。
# ⚠️ 也不是构建产物 —— `build_characters.py` 只写 anima_v4_characters.json。
# ══════════════════════════════════════════════════════════════════════════
OUTFITS_FILE = os.path.join(PRESET_DIR, "character_outfits.json")
OUTFIT_SCHEMA = "anima-v5/character-outfits@1"

# 服装字段全集（与 rules.OUTFIT_FIELDS 同口径）。预设默认全部闭麦。
_OUTFIT_DROP_DEFAULT = [
    "outfit", "top", "bottom", "socks", "sock_color", "shoes", "accessory",
    "cloth_color", "cloth_mat", "cloth_state_sfw", "cloth_nsfw",
    "n_fabric", "n_shoes_keep",
]


def _outfits_load():
    """读盘；文件坏了/不存在 → 空表（不抛，面板退化为空列表）。"""
    try:
        with open(OUTFITS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
    except Exception:
        raw = {}
    data = raw.get("outfits")
    return (raw, data if isinstance(data, dict) else {})


def list_outfits():
    """全部角色的服装预设 `{role: {enabled, list}}`。"""
    _raw, data = _outfits_load()
    return data


def _outfits_save(meta, data):
    _atomic_write(OUTFITS_FILE, {
        "_meta": {
            "schema": OUTFIT_SCHEMA,
            "updated_at": _now(),
            "roles": len(data),
            **{k: v for k, v in (meta or {}).items() if k != "schema"},
        },
        "outfits": data,
    })


def _clean_outfit(raw):
    """清洗一套服装条目。返回 dict；非法返回 None。"""
    if not isinstance(raw, dict):
        return None
    oid = _clean_text(raw.get("id"), 40).strip().lower().replace(" ", "_")
    if not oid:
        return None
    tags = []
    for t in (raw.get("tags") or []):
        t = _clean_text(t, 80).strip().lower()
        if t and t not in tags:
            tags.append(t)
    fields = {}
    if isinstance(raw.get("fields"), dict):
        for k, v in raw["fields"].items():
            k = _clean_text(k, 40).strip()
            v = _clean_text(v, 120).strip().lower()
            if k and v:
                fields[k] = v
    drop = []
    for k in (raw.get("drop") or _OUTFIT_DROP_DEFAULT):
        k = _clean_text(k, 40).strip()
        if k and k not in drop:
            drop.append(k)
    try:
        level = max(0, min(4, int(raw.get("level") or 0)))
    except Exception:
        level = 0
    return {
        "id": oid,
        "zh": _clean_text(raw.get("zh"), 60).strip() or oid,
        "level": level,
        "tags": tags,
        "fields": fields,
        "drop": drop,
        "verified": bool(raw.get("verified")),
        "note": _clean_text(raw.get("note"), 400).strip(),
    }


def _unique_outfit_id(lst, base):
    """在已有条目里挑一个不撞车的 id：base / base_2 / base_3 …

    v5.0.9 修复：新增服装时如果 id 撞上已有的，会**静默覆盖**那一套
    （用户实测：加第二套就把第一套顶掉了）。根因是前端把空 id 兜底成
    `"default"`，而 `save_outfit()` 的语义是「同 id = 更新」——
    结果每次"新增"都在改同一套。
    这里给一条后端兜底：只要调用方明说 `new=True`，就一定会分到新 id。
    """
    base = str(base or "").strip().lower() or "default"
    ids = {str(x.get("id") or "") for x in (lst or []) if isinstance(x, dict)}
    if base not in ids:
        return base
    n = 2
    while f"{base}_{n}" in ids:
        n += 1
    return f"{base}_{n}"


def save_outfit(role_tag, outfit, new=False):
    """新增或更新一套服装（同 id 覆盖）。返回 (最新条目, 该角色全部条目)。

    ⚠️ `new=True` 是**新增**语义：id 撞车时自动改成 `<id>_2` / `<id>_3`，
    绝不覆盖已有条目。前端「＋ 新增一套」必须传它 ——
    否则用户加第二套就会把第一套顶掉（v5.0.9 修的就是这个）。
    """
    role = _clean_text(role_tag, 200).strip().lower()
    if not role:
        raise ValueError("缺少角色 tag")
    rec = _clean_outfit(outfit)
    if not rec:
        raise ValueError("服装条目缺少 id")
    raw, data = _outfits_load()
    bucket = data.get(role)
    if not isinstance(bucket, dict):
        bucket = {"enabled": "", "list": []}
    lst = [x for x in (bucket.get("list") or []) if isinstance(x, dict)]
    if new:
        rec["id"] = _unique_outfit_id(lst, rec["id"])
    replaced = False
    for i, x in enumerate(lst):
        if str(x.get("id") or "") == rec["id"]:
            lst[i] = rec
            replaced = True
            break
    if not replaced:
        lst.append(rec)
    bucket["list"] = lst
    if not bucket.get("enabled"):
        bucket["enabled"] = ""
    data[role] = bucket
    _outfits_save(raw.get("_meta"), data)
    return rec, lst


def delete_outfit(role_tag, outfit_id):
    """删掉一套。删的是启用中的那套 → enabled 清空。返回 (该角色剩余条目, 新的 enabled)。"""
    role = _clean_text(role_tag, 200).strip().lower()
    oid = _clean_text(outfit_id, 40).strip().lower()
    raw, data = _outfits_load()
    bucket = data.get(role)
    if not isinstance(bucket, dict):
        raise ValueError("这个角色没有服装预设")
    lst = [x for x in (bucket.get("list") or [])
           if isinstance(x, dict) and str(x.get("id") or "") != oid]
    if len(lst) == len(bucket.get("list") or []):
        raise ValueError("找不到这套服装：%s" % (oid or "(空)"))
    bucket["list"] = lst
    if str(bucket.get("enabled") or "") == oid:
        bucket["enabled"] = ""
    # v5.0.9：删空之后把角色键一并摘掉 —— 否则会留下 `{"enabled": "", "list": []}`
    # 的空壳，面板上表现为"这个角色有预设记录但一套都没有"。删干净就该真的干净。
    if lst:
        data[role] = bucket
    else:
        data.pop(role, None)
    _outfits_save(raw.get("_meta"), data)
    return lst, bucket.get("enabled") or ""


def set_outfit_enabled(role_tag, outfit_id):
    """启用 / 停用。传空串 = 停用（回到随机抽服装）。返回新的 enabled。"""
    role = _clean_text(role_tag, 200).strip().lower()
    oid = _clean_text(outfit_id, 40).strip().lower()
    raw, data = _outfits_load()
    bucket = data.get(role)
    if not isinstance(bucket, dict):
        raise ValueError("这个角色没有服装预设")
    if oid:
        ids = {str(x.get("id") or "") for x in (bucket.get("list") or [])
               if isinstance(x, dict)}
        if oid not in ids:
            raise ValueError("找不到这套服装：%s" % oid)
    bucket["enabled"] = oid
    data[role] = bucket
    _outfits_save(raw.get("_meta"), data)
    return oid


# ══════════════════════════════════════════════════════════════════════════
# 全局服装库（v5.1.0，用户 2026-11-05）
#
# 「增加服装库，预设分类好各种丰富的整套服装搭配，UI 与角色库、场景搭配同级；
#   选择后原理与角色服装预设一致，可以直接套用。」
#
# 与「角色服装预设」的区别只有一处：**不绑角色**。
#   · 角色预设   `{role: {enabled, list}}` —— 某个角色专属，优先；
#   · 服装库     `{_meta.active, outfits: [...]}` —— 全局一套，角色没有预设时兜底。
# 条目结构与角色预设**逐字段一致**（`_clean_outfit` 直接复用），
# 所以引擎侧的合并逻辑（extras / drop / sets）可以原样共用。
#
# ⚠️ 数据由 `tools/build_outfit_library.py` 生成（level 是派生值），
#    但 `_meta.active` 是**用户状态** —— 重跑构建必须保留（脚本已处理）。
# ══════════════════════════════════════════════════════════════════════════
LIBRARY_FILE = os.path.join(PRESET_DIR, "outfit_library.json")
LIBRARY_SCHEMA = "anima-v5/outfit-library@1"

# v6.5.1：分类 id `nsfw` → `nsfw_outfit`（用户 2026-11-07 要求连 id 一起改）。
# 老工作流 / 老预设 / 面板筛选记忆（weights_json._ui.outfitCat）里可能仍存
# "nsfw"，一律在这里归一。⚠️ 引擎匹配服装用的是**套装 id**（不是 cat），
# 所以归一只影响面板分类表与筛选，正文零漂移。
CAT_ALIASES = {"nsfw": "nsfw_outfit"}


def normalize_cat(value):
    """把历史分类 id 归一到当前 id；未知值原样小写返回。"""
    c = str(value or "").strip().lower()
    return CAT_ALIASES.get(c, c)


def _library_load():
    """读盘。返回 (原始 dict, meta, 条目列表)。坏文件 → 空库，不抛。"""
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    items = raw.get("outfits")
    if not isinstance(items, list):
        items = []
    return raw, meta, [x for x in items if isinstance(x, dict)]


def _library_cats(meta):
    cats = meta.get("categories")
    if not isinstance(cats, list):
        return []
    return [c for c in cats if isinstance(c, dict) and c.get("id")]


def library_payload():
    """给面板的整份服装库：分类表 + 全部条目 + 启用态。"""
    raw, meta, raw_items = _library_load()
    # v6.5.1：分类 id 归一（copy 一份，绝不原地改 raw / 不写盘）
    cats = []
    for c in _library_cats(meta):
        c = dict(c)
        c["id"] = normalize_cat(c.get("id"))
        cats.append(c)
    items = []
    for x in raw_items:
        x = dict(x)
        if "cat" in x:
            x["cat"] = normalize_cat(x.get("cat"))
        items.append(x)
    active = str(meta.get("active") or "").strip().lower()
    if active and active not in {str(x.get("id") or "") for x in items}:
        active = ""
    counts = {}
    for x in items:
        c = str(x.get("cat") or "")
        counts[c] = counts.get(c, 0) + 1
    ov = meta.get("overrides")
    if not isinstance(ov, dict):
        ov = {}
    return {
        "schema": str(meta.get("schema") or LIBRARY_SCHEMA),
        "updated_at": str(meta.get("updated_at") or ""),
        "active": active,
        # v5.5.0：袜子 / 鞋子的**覆盖项**（用户 2026-11-05 21:27）
        # `{"socks": "", "shoes": ""}`，空串 = 跟随这套服装自己的搭配。
        # 与 `active` 一样是**全局用户状态**，重跑 build_outfit_library 会保留。
        "overrides": {k: str(ov.get(k) or "") for k in ("socks", "shoes")},
        # v5.9.0：服装库是否覆盖「场景搭配里写死的推荐服装」。
        # False（默认）= 场景带推荐服装时服装库整套让路；True = 服装库强制覆盖
        # （即 v5.8.0 及以前的既有行为）。同样是全局用户状态，重跑构建会保留。
        "scene_override": bool(meta.get("scene_override")),
        "categories": cats,
        "count": len(items),
        "counts": counts,
        "outfits": items,
    }


def _library_write(meta, items, active=None):
    """整份写回。`active` 传 None = 沿用原值（构建/保存条目时用）。"""
    meta = dict(meta or {})
    if active is None:
        active = str(meta.get("active") or "")
    ids = {str(x.get("id") or "") for x in items}
    active = active if active in ids else ""
    meta.update({
        "schema": LIBRARY_SCHEMA,
        "updated_at": _now(),
        "count": len(items),
        "active": active,
    })
    meta.setdefault("categories", [])
    _atomic_write(LIBRARY_FILE, {"_meta": meta, "outfits": items})


def save_library_outfit(outfit, new=False):
    """新增 / 更新一套。`new=True` 时 id 撞车自动改名，绝不覆盖。"""
    raw, meta, items = _library_load()
    rec = _clean_outfit(outfit)
    if not rec:
        raise ValueError("服装条目缺少 id")
    cat = _clean_text((outfit or {}).get("cat"), 40).strip().lower()
    cats = {c["id"] for c in _library_cats(meta)}
    if not cat:
        raise ValueError("缺少分类 cat")
    if cats and cat not in cats:
        raise ValueError("分类 %s 不在分类表里" % cat)
    rec["cat"] = cat
    if new:
        rec["id"] = _unique_outfit_id(items, rec["id"])
    replaced = False
    for i, x in enumerate(items):
        if str(x.get("id") or "") == rec["id"]:
            items[i] = rec
            replaced = True
            break
    if not replaced:
        items.append(rec)
    _library_write(meta, items)
    return rec, items


def delete_library_outfit(outfit_id):
    """删掉一套。删的是启用中的那套 → active 清空。返回剩余条目。"""
    oid = _clean_text(outfit_id, 40).strip().lower()
    raw, meta, items = _library_load()
    left = [x for x in items if str(x.get("id") or "") != oid]
    if len(left) == len(items):
        raise ValueError("找不到这套服装：%s" % (oid or "(空)"))
    _library_write(meta, left)
    return left


def set_library_active(outfit_id):
    """启用 / 停用。空串 = 关闭（回到随机服装）。返回新的 active。"""
    oid = _clean_text(outfit_id, 40).strip().lower()
    raw, meta, items = _library_load()
    if oid:
        ids = {str(x.get("id") or "") for x in items}
        if oid not in ids:
            raise ValueError("找不到这套服装：%s" % oid)
    _library_write(meta, items, active=oid)
    return oid


# ══════════════════════════════════════════════════════════════════════════
# 服装库的「覆盖项」：只改袜子 / 鞋子（v5.5.0，用户 2026-11-05 21:27）
#
# 需求原话：「可以指定选择丝袜与鞋子款式以及是否穿丝袜与鞋子，两个可以分别指定
#   也可不启用（不启用就跟随服装库搭配的服装）。指定后权重覆盖装库搭配的服装。
#   目的是我想要指定部分穿搭的同时又可以使用服装库搭配的服装。」
#
# 语义：服装库那套给整体，这里只覆盖两个字段，其余照旧。
#   · 空串 = 跟随（默认）
#   · `barefoot` / `bare feet` = 明确"不穿"（词库真实选项，模型认识）
#
# ⚠️ 值必须**同时**从套装的 `tags` 里剔除（引擎侧 `apply_library_overrides` 做），
#    否则正文里会同时出现 `white thighhighs` 与你指定的那双 —— 模型会二选一，
#    有时甚至画出两条袜子。
# ══════════════════════════════════════════════════════════════════════════
LIB_OVERRIDE_FIELDS = ("socks", "shoes")


def library_overrides():
    """当前覆盖项。返回 `{"socks": "", "shoes": ""}`（缺字段补空串）。"""
    raw, meta, _items = _library_load()
    ov = meta.get("overrides")
    if not isinstance(ov, dict):
        ov = {}
    return {k: str(ov.get(k) or "").strip().lower() for k in LIB_OVERRIDE_FIELDS}


def set_library_override(field, value):
    """设定 / 清除一个覆盖项。返回值同 `library_overrides()`。

    `field` 只认 socks / shoes；`value` 空串 = 回到"跟随服装库"。
    值合法性由调用方（路由层）用词库校验 —— 存储层看不到词库。
    """
    f = str(field or "").strip().lower()
    if f not in LIB_OVERRIDE_FIELDS:
        raise ValueError("只支持 %s 两个字段" % " / ".join(LIB_OVERRIDE_FIELDS))
    v = _clean_text(value, 60).strip().lower()
    raw, meta, items = _library_load()
    ov = meta.get("overrides")
    if not isinstance(ov, dict):
        ov = {}
    ov[f] = v
    meta["overrides"] = {k: str(ov.get(k) or "").strip().lower()
                         for k in LIB_OVERRIDE_FIELDS}
    _library_write(meta, items)
    return dict(meta["overrides"])


# ── v5.9.0：服装库 ↔ 场景推荐服装的让路开关 ──────────────────────────────
#
# 需求原话：「服装库页面增加覆盖场景搭配服装交互功能，开启后服装库选择的服装
#   可以覆盖场景搭配内的场景服装；不开启，有指定风格服装的场景就以场景搭配的
#   服装优先。」
# 语义：**默认关闭 = 场景优先**（这是新语义，v5.8.0 及以前是服装库永远赢）。
#   · 关闭：抽到带具体衣物字段（outfit/top/bottom/socks/shoes/accessory）的
#     场景时，服装库整套让路 —— 连袜子/鞋子覆盖项一起不生效（与「档位不够时
#     整套不生效」同一条口径，不能变成「衣服按场景、袜鞋按服装库」）；
#   · 开启：服装库强制覆盖场景服装，与旧版逐字节一致。
# 判据在引擎侧按「场景本轮**实际写入**的字段」算，这里只存一个全局 bool。
# ⚠️ 只管服装库这一层：角色服装预设仍然是最高优先级，不受本开关影响。
# ══════════════════════════════════════════════════════════════════════════

def library_scene_override():
    """当前开关值。缺字段 / 脏值一律按 False（= 场景优先）处理。"""
    _raw, meta, _items = _library_load()
    return bool(meta.get("scene_override"))


def set_library_scene_override(enabled):
    """设定开关，返回新值。写操作后调用方必须 `R.clear_cache()`。"""
    _raw, meta, items = _library_load()
    meta["scene_override"] = bool(enabled)
    _library_write(meta, items)
    return bool(meta["scene_override"])
