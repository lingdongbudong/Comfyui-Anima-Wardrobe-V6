# -*- coding: utf-8 -*-
"""作品 IP 收藏基线（v5.0.4 新增）。

守六件事：

  ① **真实库的 slug 全部合法** —— 284 个作品里有一个带冒号的
     （`persona_5:_the_phantom_x`），正则少写一个字符就会让它"永远收藏不进去"，
     而且只有它一个，肉眼极难发现；
  ② 切换幂等：重复收藏同一个不产生两条；取消一个不存在的也不报错；
  ③ 非法 slug 一律拒绝，且**不落盘**；
  ④ 上限保护：到 200 个后拒绝再写；
  ⑤ **三份收藏互不污染** —— 角色 tag / 场景 id / 作品 slug 值域完全不同，
     任何一个消费方拿到别人的 key 都会静默失效（「仅收藏内随机」空池那类）；
  ⑥ 脏数据容错：手改文件塞进非法条目时，列表静默过滤而不是抛异常。

⚠️ 测试把三个收藏文件的路径**全部重定向到临时目录**，全程不碰
   `presets/user_favorites.json` / `user_scene_favorites.json` /
   `user_work_favorites.json`。

跑法：python tests/check_v3_workfav.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _boot  # noqa: E402

S = _boot.load("anima_v4_store")

FAILS = []


def ck(name, cond, note=""):
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [!!] {name}  {note}")
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="av5_workfav_")
_OLD = (S.WORK_FAV_FILE, S.FAVORITES_FILE, S.SCENE_FAV_FILE)
S.WORK_FAV_FILE = os.path.join(TMP, "user_work_favorites.json")
S.FAVORITES_FILE = os.path.join(TMP, "user_favorites.json")
S.SCENE_FAV_FILE = os.path.join(TMP, "user_scene_favorites.json")

try:
    print("── ① 真实库 slug 合法性 ──")
    data = json.load(io.open(os.path.join(S.PRESET_DIR, "anima_v4_characters.json"),
                             encoding="utf-8"))
    slugs = [w.get("id") for w in (data.get("works") or [])]
    bad = [s for s in slugs if not S._work_slug_ok(s)]
    ck("全部作品 slug 都过 _work_slug_ok", not bad, str(bad[:3]))
    ck("带冒号的 slug 合法（persona_5:_the_phantom_x）",
       S._work_slug_ok("persona_5:_the_phantom_x"))
    ck("作品数 > 200（说明上限 200 是个真限制，不是摆设）", len(slugs) > 200, str(len(slugs)))

    print("\n── ② 空库起步 / 切换 ──")
    ck("文件不存在时返回空列表（不抛异常）", S.list_work_favorites() == [])
    r = S.toggle_work_favorite("kantai_collection", True)
    ck("收藏一个作品", r == ["kantai_collection"], str(r))
    r = S.toggle_work_favorite("kantai_collection", True)
    ck("重复收藏幂等（不产生两条）", r == ["kantai_collection"], str(r))
    r = S.toggle_work_favorite("persona_5:_the_phantom_x", True)
    ck("含冒号的作品也能收藏进去", r == ["kantai_collection", "persona_5:_the_phantom_x"], str(r))
    r = S.toggle_work_favorite("kantai_collection", None)
    ck("on=None 时按当前状态取反（取消）", "kantai_collection" not in r, str(r))
    r = S.toggle_work_favorite("no_such_work", False)
    ck("取消一个本来就不在的收藏：成功且不报错", r == ["persona_5:_the_phantom_x"], str(r))

    print("\n── ③ 非法 slug 拒绝且不落盘 ──")
    before = S.list_work_favorites()
    for bad_slug in ("Kantai_Collection", "舰队Collection", "kantai collection", "", None):
        ok = False
        try:
            S.toggle_work_favorite(bad_slug, True)
        except ValueError:
            ok = True
        ck(f"拒绝 {bad_slug!r}", ok)
    ck("非法请求没有改动磁盘", S.list_work_favorites() == before, str(S.list_work_favorites()))

    print("\n── ④ 上限保护 ──")
    while len(S.list_work_favorites()) < S.MAX_WORK_FAVORITES:
        S.toggle_work_favorite(f"w{len(S.list_work_favorites()):04d}", True)
    ck("能装满到上限", len(S.list_work_favorites()) == S.MAX_WORK_FAVORITES)
    capped = False
    try:
        S.toggle_work_favorite("one_more_work", True)
    except ValueError:
        capped = True
    ck(f"超上限拒绝（{S.MAX_WORK_FAVORITES}）", capped)

    print("\n── ⑤ 三份收藏互不污染 ──")
    ck("没写过角色收藏文件", not os.path.exists(S.FAVORITES_FILE))
    ck("没写过场景收藏文件", not os.path.exists(S.SCENE_FAV_FILE))
    doc = json.load(io.open(S.WORK_FAV_FILE, encoding="utf-8"))
    ck("作品收藏文件 schema 是 work-favorites@1",
       doc.get("_meta", {}).get("schema") == S.WORK_FAV_SCHEMA,
       str(doc.get("_meta")))
    ck("值域里没有 sc_ / 角色 tag 混入",
       not any(v.startswith("sc_") or "(" in v for v in doc.get("favorites") or []))

    print("\n── ⑥ 脏数据容错 ──")
    with io.open(S.WORK_FAV_FILE, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"_meta": {"schema": S.WORK_FAV_SCHEMA},
                             "favorites": ["ok_work", 42, "Bad Slug", None, "ok_work"]}))
    got = S.list_work_favorites()
    ck("非法条目被静默滤掉 + 去重", got == ["ok_work"], str(got))
finally:
    S.WORK_FAV_FILE, S.FAVORITES_FILE, S.SCENE_FAV_FILE = _OLD
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILS:
    print(f"[check_v3_workfav] 失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("[check_v3_workfav] 通过：slug 合法性 / 幂等 / 校验 / 上限 / 隔离 / 容错")
