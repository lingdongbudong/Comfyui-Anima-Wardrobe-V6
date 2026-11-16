# -*- coding: utf-8 -*-
"""服装库池化随机基线（v6.23.0 新增）。

守三件事：

  ① **关闭时零漂移** —— 没有 `_meta.pool`（旧文件）或 `on=false` 时，
     `draw()` 正文逐字节不变、`effective_outfit()` 与 `outfit_for()` 逐字符相同；
  ② **开启时行为正确** —— 同 seed 复现 / 不同 seed 会换套 / 分类与档位过滤生效 /
     池空时**整套让路**（绝不回落 `_meta.active`）/ 非泳装锚点剔掉泳装套；
  ③ **既有优先级不动** —— 角色预设照常压过池化、场景带推荐服装时池化也让路；
     池化与「钉一套」互斥；`_last` 写盘**不碰**主库文件（否则破节点缓存）。

⚠️ 全程在临时副本上跑（`_boot.isolate_outfit_library`），用户真实库一个字节不动。

用法：`python tests/check_v3_outfitpick.py`
"""
import atexit
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _boot

R = _boot.load("anima_v4_rules")
S = _boot.load("anima_v4_store")

PASS, FAIL = [], []


def ck(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK' if ok else '!!'}] {name}" + (f"  {detail}" if detail else ""))


# ── 隔离：库文件与 `_last` 文件都指到临时目录 ────────────────────────────
REAL = S.LIBRARY_FILE
TMP = _boot.isolate_outfit_library(R)
_REAL_LAST = S.POOL_LAST_FILE
_TMPDIR2 = tempfile.mkdtemp(prefix="av5-pool-last-")
S.POOL_LAST_FILE = os.path.join(_TMPDIR2, "outfit_library_last.json")


def _restore_last():
    S.POOL_LAST_FILE = _REAL_LAST
    shutil.rmtree(_TMPDIR2, ignore_errors=True)


atexit.register(_restore_last)


def _raw():
    with open(TMP, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write(data):
    with open(TMP, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    R.clear_cache()


def _drop_pool_key():
    d = _raw()
    (d.get("_meta") or {}).pop("pool", None)
    _write(d)


def _reset_pool(**kw):
    S.set_library_pool(on=False, cats=[], max_level=4, ids=[])
    if kw:
        S.set_library_pool(**kw)
    R.clear_cache()


print("── ① 旧文件兼容 / 关闭态零漂移 ──")
_drop_pool_key()
ck("缺 `_meta.pool` 键 → 视为关闭（旧文件零迁移）",
   R.library_pool()["on"] is False, str(R.library_pool()))

_t_absent = R.draw(level="sfw", seed=1001)["text"]
_reset_pool()
_t_off = R.draw(level="sfw", seed=1001)["text"]
ck("on=false 与缺键的正文逐字节一致（关闭态零漂移）", _t_absent == _t_off,
   f"{len(_t_absent)} vs {len(_t_off)} 字符")

S.set_library_active("")
R.clear_cache()
_pair_ok = all(
    R.effective_outfit(r, 0) == R.outfit_for(r, 0)
    for r in ("hatsune miku", "tifa lockhart", "asuna (sao)"))
ck("关闭态 effective_outfit == outfit_for（未启用时不改变任何随机结果）", _pair_ok)

print("\n── ② 抽签行为 ──")
_reset_pool(on=True)
_a = R.resolve_library_pick(0, 777)
_b = R.resolve_library_pick(0, 777)
ck("同 seed 两次解析出同一套（可复现）",
   _a["item"] and _a["item"]["id"] == _b["item"]["id"],
   f'{_a["item"].get("id")} vs {_b["item"].get("id")}')

seen = {R.resolve_library_pick(0, -1)["item"].get("id") for _ in range(40)}
ck("seed=-1 连抽 40 次至少出现 2 种（不是恒取第一条）", len(seen) >= 2,
   f"{len(seen)} 种")

_reset_pool(on=True, cats=["casual"])
cats40 = {R.resolve_library_pick(0, s)["item"].get("cat") for s in range(40)}
ck("cats=['casual'] → 命中全为 casual", cats40 == {"casual"}, str(cats40))

_reset_pool(on=True, cats=[], max_level=0)
lv40 = [R.resolve_library_pick(4, s)["item"].get("level") for s in range(40)]
ck("max_level=0 + 档位 extreme → 命中全 L0", set(lv40) == {0}, str(set(lv40)))
_reset_pool(on=True, cats=["casual"], max_level=4)
# ⚠️ payload 的 count **不含运行期档位**（lv 要等 draw 才知道），所以这里拿 lv=4
# 比 —— lv=0 时 resolve 会再砍掉 L1+，6 vs 10 的差是设计使然，不是 bug。
_why = R.resolve_library_pick(4, 5)["why"]
_pl = S.library_payload()["pool"]
ck("池内候选数与 payload 的 count 一致（同一条过滤逻辑）",
   _why.startswith("服装池 %d 套" % _pl["count"]),
   f'{_why} / count={_pl["count"]}')

print("\n── ③ 池空 / 优先级 / 泳装 ──")
_reset_pool(on=True, cats=["swim"], max_level=4)
_empty = R.resolve_library_pick(0, 3)
ck("lv=0 且池内全是 L1+（swim）→ 整套让路，item 为空",
   _empty["on"] is True and _empty["item"] == {}, str(_empty["item"]))
ck("池空时 why 写明原因（不静默）", "无可用套" in (_empty["why"] or ""),
   _empty["why"])
S.set_library_active("ol_knit_pleated")
R.clear_cache()
ck("池空时不回落 active（钉的那套不会被偷偷穿上）",
   R.effective_outfit("hatsune miku", 0, False, _empty) == ([], [], {}))
S.set_library_active("")
R.clear_cache()

_role = "hatsune miku"
_pick = R.resolve_library_pick(0, 11)
ck("角色启用预设时池化不生效（角色预设 > 服装库）",
   R.effective_outfit(_role, 0, False, _pick) == R.outfit_for(_role, 0)
   if R.outfit_item(_role) else True)

ck("场景带推荐服装且开关关闭 → 池化也让路（空三元组）",
   R.effective_outfit("hatsune miku", 0, True, _pick) == ([], [], {}))

_reset_pool(on=True, cats=["swim"])
_hall = R.resolve_library_pick(0, 5, {"place": "hallway"})
ck("非泳装锚点（hallway）→ 泳装套全被剔除（池空）",
   _hall["item"] == {} and "泳装" in (_hall["why"] or ""), _hall["why"])
_beach = R.resolve_library_pick(3, 5, {"place": "beach"})
ck("泳装锚点（beach）→ 允许抽到泳装套",
   bool(_beach["item"]) and R._is_swim_suit(_beach["item"]),
   _beach["item"].get("id"))

print("\n── ④ 互斥与指纹 ──")
_reset_pool(on=True, cats=["casual"])
S.set_library_active("ol_knit_pleated")
R.clear_cache()
ck("钉一套 → 池化自动关闭（互斥，双向都保证）",
   S.library_payload()["pool"]["on"] is False
   and S.library_payload()["active"] == "ol_knit_pleated")
S.set_library_pool(on=True, cats=["casual"])
R.clear_cache()
ck("开池化 → active 被清空（不会同时钉一套）",
   S.library_payload()["active"] == "")

_m1 = os.stat(TMP).st_mtime_ns
S.set_library_pool(on=True, cats=["office"])
_n1 = os.stat(TMP).st_mtime_ns
ck("改池子 → 主库文件 mtime 变（节点缓存必破，seed>=0 也会重抽）", _m1 != _n1,
   f"{_m1} → {_n1}")
S.set_library_pool_last("ol_knit_pleated")
_n2 = os.stat(TMP).st_mtime_ns
ck("写 `_last` 不碰主库文件（否则每张图都破缓存、可复现性崩）", _n1 == _n2,
   f"{_n1} → {_n2}")
ck("`_last` 通过 payload 暴露给面板",
   S.library_payload()["pool"]["_last"] == "ol_knit_pleated",
   S.library_payload()["pool"]["_last"])

_reset_pool()
print(f"\n[check_v3_outfitpick] 通过 {len(PASS)} 项，失败 {len(FAIL)} 项"
      + (f"：{FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
