# -*- coding: utf-8 -*-
"""按构图合理性给场景推荐画幅（竖 / 横 / 通用）—— 生成 presets/anima_a4_scene_aspects.json

用户 2026-11-01：
「场景搭配内场景根据场景构图合理性增加推荐竖屏比例使用还是横屏比例使用，
  如果都可以就标注比例通用。需要在显示场景 UI 对界面看得到。」

为什么单独存一个文件、而不是写进 `presets/anima_v4_scenes.json`：
  scenes.json 是 `tools/build_scenes.py` 的**产物**，重跑构建会被整份覆盖 ——
  标在里面的 aspect 会静默消失。这份映射是**人工可改的旁挂数据**，
  要改某一条直接改这里的 why / a，不用碰生成器。

判定口径（顺序即优先级）
  0. 大前提：这 269 条的主体**都是人**。按项目既有的画幅总表，人物独大默认竖 3:4，
     所以**没有横向证据就判竖**，而不是一律"通用" —— 否则一半以上场景都没有
     推荐值，筛选功能等于空转。
  1. 场景自己写了 comp：landscape → 横；portrait → 竖（但特写取景 → 通用）。
  2. 特写 / 上半身取景（close-up / extreme close-up / upper body）→ **通用**：
     框里没有"横向空间"这个概念，竖横都成立。
  3. 多人（2girls / hetero / multiple / group）→ **横**：要横向并排才站得下。
  4. wide shot → 横。
  5. 关键词：横向证据（躺 / 趴 / 床 / 地 / 沙发 / 浴缸 / 街 / 四肢着地 …）
     与竖向证据（站 / 悬吊 / 墙 / 门 / 窗 / 楼梯 / 镜前 …）互相抵消；
     只有横向证据 → 横；只有竖向证据 → 竖；两边都有 → 通用。
  6. 都没有 → 竖（人物题材默认）。

用法：
    python tools/build_scene_aspects.py            # 写文件
    python tools/build_scene_aspects.py --dry      # 只打印分布
    python tools/build_scene_aspects.py --csv out.csv   # 附一份明细供人工抽查
"""
import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SCENES = os.path.join(PLUGIN, "presets", "anima_v4_scenes.json")
OUT = os.path.join(PLUGIN, "presets", "anima_v4_scene_aspects.json")

MULTI_TOKENS = ("2girls", "1boy", "hetero", "multiple", "group sex", "3girls")
# ⚠️ 只收「真的需要横向空间」的证据：躺卧、贴地、宽景。
#    别把 street / pool / 双脚离地 这类收进来 —— 单人街拍、泳池边出水、
#    悬吊离地都是**竖构图**更常见（实测把它们算成横，会把 40+ 条单人场景判错）。
LAND_KW = ("躺", "趴", "床", "地板", "地毯", "沙发", "浴缸", "池边",
           "四肢着地", "并排", "横躺", "摊开",
           "lying", "on back", "on all fours", "on the floor", "on a bed",
           "couch", "sofa", "bathtub", "poolside")
PORT_KW = ("站立", "站姿", "站着", "站起", "悬吊", "吊起", "吊在", "绳缚",
           "靠墙", "贴墙", "门框", "门口", "窗边", "窗前", "楼梯", "蹲",
           "镜前", "全身", "直立", "走廊",
           "standing", "suspended", "hanging", "wall", "doorway", "stair",
           "mirror")
BOTH_SHOT = ("close-up", "extreme close-up", "upper body")


def guess(sc):
    """返回 (aspect, why)。aspect ∈ portrait / landscape / both。"""
    tags = sc.get("tags") or {}
    comp = (tags.get("comp") or "").strip()
    shot = (tags.get("shot_size") or "").strip()
    cnt = (sc.get("count_lock") or "").strip()
    text = " ".join([
        sc.get("zh") or "", sc.get("desc") or "",
        sc.get("extra") or "", sc.get("scene_nl") or "",
    ])

    both_shot = shot in BOTH_SHOT
    # ⚠️ v5.0.13（用户 2026-11-04）：艺术构图（group=art）**一律横屏**，且压过下面
    #    所有启发式。这一组的立意是"巨物压边 + 大量留白"的横向平面设计，
    #    组描述写的就是"全部 16:9 横向"；但按下面的规则跑，
    #    `comp=portrait` / `竖:wall` / 特写类会被判成竖屏或通用（实测 90 条里 31 条不是横）——
    #    选到那条场景却按竖屏出图，巨物会被裁掉一半。
    if (sc.get("group") or "") == "art":
        return "landscape", "艺术构图组：巨物主导的横向平面设计，统一 16:9"
    if comp == "landscape":
        return "landscape", "场景自带 comp=landscape"
    if comp == "portrait":
        if both_shot:
            return "both", "comp=portrait，但取景是特写/上半身，竖横都成立"
        return "portrait", "场景自带 comp=portrait"

    if both_shot:
        return "both", "取景是特写/上半身，竖横都成立"

    if any(k in cnt for k in MULTI_TOKENS):
        return "landscape", "多人同框，要横向并排才站得下"

    if shot == "wide shot":
        return "landscape", "wide shot"

    land = [k for k in LAND_KW if k in text]
    port = [k for k in PORT_KW if k in text]
    if land and not port:
        return "landscape", "横:" + "/".join(land[:3])
    if port and not land:
        return "portrait", "竖:" + "/".join(port[:3])
    if land and port:
        return "both", ("横:" + "/".join(land[:2]) + " 竖:" + "/".join(port[:2]))
    return "portrait", "人物题材默认竖（无横向证据）"


def main():
    dry = "--dry" in sys.argv
    csv_path = None
    if "--csv" in sys.argv:
        i = sys.argv.index("--csv")
        if i + 1 < len(sys.argv):
            csv_path = sys.argv[i + 1]

    data = json.load(io.open(SCENES, encoding="utf-8"))
    scenes = data.get("scenes") or []
    out = {}
    tally = {"portrait": 0, "landscape": 0, "both": 0}
    rows = []
    for sc in scenes:
        a, why = guess(sc)
        out[sc["id"]] = {"a": a, "why": why}
        tally[a] += 1
        rows.append([sc["id"], sc.get("zh", ""), sc.get("level", 0),
                     (sc.get("tags") or {}).get("shot_size") or "",
                     sc.get("count_lock") or "", a, why])

    print("场景总数 %d" % len(scenes))
    print("竖屏 %(portrait)d / 横屏 %(landscape)d / 通用 %(both)d" % tally)

    if csv_path:
        with io.open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "中文名", "档位", "shot_size", "count_lock",
                        "aspect", "理由"])
            w.writerows(rows)
        print("明细已写 %s" % csv_path)

    if dry:
        return

    payload = {
        "_meta": {
            "schema": "anima-v5/scene-aspects@1",
            "note": "场景推荐画幅：portrait=竖屏 / landscape=横屏 / both=通用。"
                    "由 tools/build_scene_aspects.py 生成，可手工改 a 与 why；"
                    "scenes.json 重建不会覆盖本文件。",
            "generated_by": "tools/build_scene_aspects.py",
            "count": len(out),
        },
        "aspects": out,
    }
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=1))
    print("已写 %s" % OUT)


if __name__ == "__main__":
    main()
