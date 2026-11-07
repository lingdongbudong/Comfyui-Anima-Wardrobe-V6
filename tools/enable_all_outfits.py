# -*- coding: utf-8 -*-
"""批量启用 / 还原角色服装预设（给面板图批量跑图用）。

背景：`presets/character_outfits.json` 默认**全不启用**（服装随机）。
面板特征图要角色穿官方服装，所以跑图前把每个角色的第一套预设批量打开，
跑完还原。

为什么不用 2402 次 `/outfits/enable`：那要写 2.6MB 的 JSON 2402 次，
没必要。这里一次改库 + 重启 ComfyUI 让 `_CACHE` 失效，跑完再还原一次。

跑法：
    python enable_outfits.py on     # 全部启用（tags<3 的跳过，那些会翻车）
    python enable_outfits.py off    # 还原到备份
    python enable_outfits.py status # 查看当前启用数
"""
import json
import os
import shutil
import sys

PRESETS = r"S:/AI_workV5/ComfyUI/custom_nodes/Comfyui-Anima-Wardrobe-V6/presets"
PATH = os.path.join(PRESETS, "character_outfits.json")
BAK = PATH + ".bak-before-panel"


def load():
    with open(PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(d):
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    d = load()
    o = d.get("outfits") or {}

    if mode == "on":
        if not os.path.exists(BAK):
            shutil.copy2(PATH, BAK)
            print(f"已备份 → {BAK}")
        n = skip = 0
        for role, rec in o.items():
            lst = rec.get("list") or []
            if not lst:
                continue
            # 挑第一套：优先 verified + level 0 + tags 够长
            best = None
            for s in lst:
                tags = s.get("tags") or []
                if len(tags) < 3:
                    continue          # tag <3 必翻车（记忆里实测过）
                if s.get("level", 0) != 0:
                    continue          # SFW 跑图，非 0 档会被闸门挡住
                score = (1 if s.get("verified") else 0) * 10 + min(len(tags), 8)
                if best is None or score > best[0]:
                    best = (score, s)
            if best is None:
                skip += 1
                continue
            rec["enabled"] = best[1].get("id") or "default"
            n += 1
        save(d)
        print(f"已启用 {n} 个角色，跳过 {skip} 个（无可用预设）")
        return 0

    if mode == "off":
        if not os.path.exists(BAK):
            print(f"没有备份文件：{BAK}")
            return 1
        shutil.copy2(BAK, PATH)
        print(f"已还原 ← {BAK}")
        return 0

    en = [k for k, v in o.items() if v.get("enabled")]
    print(f"角色 {len(o)} 个，当前启用 {len(en)} 个")
    print("  样例:", en[:8])
    return 0


if __name__ == "__main__":
    sys.exit(main())
