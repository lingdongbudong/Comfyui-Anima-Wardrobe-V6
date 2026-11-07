# -*- coding: utf-8 -*-
"""ANIMA-Wardrobe-V3 · 节点定义。

四个节点，职责单一、可单独接线：

  ① AnimaV4RandomPrompt  「Anima 提示词随机生成（V4）」
       主力节点。按档位从词库里抽一段完整正文，输出英文标签串。
  ② AnimaV4PromptGrader  「Anima 提示词分级审查（V4）」
       把任意提示词喂回造型台那套判定表，看它落在第几档、命中哪些分类。
  ③ AnimaV4PromptAssemble「Anima 提示词拼接台（V4）」
       把随机结果与手写正文按 ANIMA3 槽位序合并、去重、消解互斥。
  ④ AnimaV4PromptCheck   「Anima 提示词检查与修复（V4）」（v3.8.0 新增）
       跑 17 条致命项 / 10 条风格项，并按目标档位自动修复可修项。

⚠️ 类名 / 分类 / 文件全部与 Comfyui-Anima-Wardrobe 独立命名，
   两个插件可以同时安装，不会互相覆盖。
"""
from __future__ import annotations

import itertools
import json
import os
import random as _random
import time

from . import anima_v4_nsfw as N
from . import anima_v4_pngmeta as PM
from . import anima_v4_rules as R


_VERSION_CACHE = []

# 最近一次「元数据注入」的统计。纯可观测性，不参与任何生成逻辑 ——
# 出图后想知道"到底写进去没有"，看它比翻 PNG 快。
_META_LAST = {}


def _record_meta(st):
    _META_LAST.clear()
    _META_LAST.update(st or {})


def _v3_version():
    """插件版本号（只读一次，结果缓存）。

    ⚠️ 用读文件而不是 `from . import __version__` —— 后者会在 import 期
       与 __init__ 形成循环（__init__ 先 import nodes）。
    """
    if _VERSION_CACHE:
        return _VERSION_CACHE[0]
    v = ""
    try:
        import os
        init = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
        with open(init, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("__version__"):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        v = ""
    _VERSION_CACHE.append(v)
    return v

SECTION_NAME = "AnimaWardrobeV6"

# 控件里显示的档位文案。值直接用 key —— 与造型台同一口径：
# 值不写中文，中文只在界面上显示，避免接口校验对不上。
LEVEL_CHOICES = [
    "sfw", "suggestive", "explicit", "hardcore", "extreme", "any",
]

TAG_CAP_CHOICES = ["自动", "16", "20", "24", "28", "32", "36", "40", "48"]

# v3.17.0：胸部大小等级下拉的选项。真源映射表在 rules（BREAST_SIZES），
# 这里只管界面文案；「不指定」= 沿用原巨乳开关关闭的行为。
BREAST_SIZE_CHOICES = [
    "不指定", "平胸", "小胸", "中等", "大胸", "巨乳", "爆乳",
]

# v3.19.0：「服装风格智配」下拉的候选。真源在 rules/OS.STYLE_CHOICES，
# 这里只做一次导出 —— 两处不一致会让"节点里能选、面板里没有"。
OUTFIT_STYLE_CHOICES = list(R.OS.STYLE_CHOICES)


def _split_ids(raw):
    """把「逗号 / 换行 / 空格」分隔的段 id 串拆成集合。"""
    out = set()
    for chunk in str(raw or "").replace("\n", ",").replace(" ", ",").split(","):
        c = chunk.strip()
        if c:
            out.add(c)
    return out


# ⚠️ v5.9.0：seed<0 时旧的 `time.time()` 在 Windows 上精度只有 ~15ms，
#    连续两次 IS_CHANGED 可能拿到同一个值（实测单测会红）→ 缓存不破，
#    「每次重抽」偶发失效。用单调计数器兜底，保证每次返回都不同。
_CHANGED_TICK = itertools.count()


def _global_state_fingerprint() -> str:
    """面板上能改、会立刻影响正文、但**不在节点输入里**的全局用户状态。

    ⚠️ v5.9.0 真机验证（12B 1105 工作流）踩到：ComfyUI 按节点输入 hash 缓存
       输出。服装库 `active` / 袜子鞋子 `overrides` / `scene_override` 开关、
       角色服装预设启用态都是**服务端全局态**，工作流与 seed 不变时用户在面板
       改了它们，节点**不会重跑** —— 实测 A/B 两次只切开关，第二次 2 秒返回、
       PNG 像素与第一次逐字节相同，连 PNG 元数据注入都被跳过。
    这里用文件的 `mtime_ns + size` 而不是读内容：任何写操作都会让指纹变化，
    状态没变时又保持恒定（缓存仍然有效，不会每次白白重跑）。
    ⚠️ 只列「通过 HTTP 路由写、且会进正文」的文件；词库 / 场景 / 角色内置库
       改盘本来就要重启（进程缓存自然清空），不必纳入。
    """
    try:
        from . import anima_v4_store as _S
        files = (_S.LIBRARY_FILE, _S.OUTFITS_FILE, _S.USER_CHARS_FILE,
                 _S.FAVORITES_FILE, _S.SCENE_FAV_FILE, _S.WORK_FAV_FILE)
        bits = []
        for p in files:
            try:
                st = os.stat(p)
                bits.append("%s:%d:%d" % (os.path.basename(p),
                                          st.st_mtime_ns, st.st_size))
            except OSError:
                bits.append(os.path.basename(p) + ":missing")
        return "|".join(bits)
    except Exception:
        return "unavailable"


class AnimaV4RandomPrompt:
    """按 NSFW 档位从词库里随机组合出一段 ANIMA3 正文。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "nsfw_level": (LEVEL_CHOICES, {"default": "sfw"}),
                "seed": ("INT", {"default": -1, "min": -1,
                                 "max": 2147483647,
                                 "tooltip": "-1 = 每次运行重抽；>=0 = 可复现"}),
                "tag_cap": (TAG_CAP_CHOICES, {"default": "自动"}),
                "base_prob": ("FLOAT", {"default": R.DEFAULT_BASE_PROB,
                                        "min": 0.0, "max": 1.0, "step": 0.05}),
                "disabled_sections": ("STRING", {"default": "",
                                                 "multiline": False}),
                "lock_fields": ("STRING", {"default": "", "multiline": False}),
                "force_role": ("STRING", {"default": "", "multiline": False}),
                "weights_json": ("STRING", {"default": "", "multiline": True}),
                "extra_body": ("STRING", {"default": "", "multiline": True}),
                # ⚠️ 控件顺序一旦发布就是契约：以后新增一律**追加到末尾**，
                #    否则已存盘的工作流 widgets_values 会整体错位。
                #    这条规矩从 3.0.0 第一天就开始执行。
                "appearance_follow": ("BOOLEAN", {"default": True}),
                # v3.1.0 追加：面板角色浏览器点选的角色（| 或 , 分隔）
                "selected_roles": ("STRING", {"default": "", "multiline": False}),
                # v3.2.0 追加：巨乳开关。开启时 figure 字段必定为 `huge breasts`，
                # 且优先级高于角色外貌对齐与任何权重（面板最底部的那个开关）。
                # ⚠️ v3.17.0 起被下面的 `breast_size` 六档下拉**取代**：
                #    按房规「退休控件只撤界面、不删控件位」—— INPUT_TYPES 里
                #    保留它（老工作流 widgets_values 不错位），前端 JS 不再渲染，
                #    后端保留兼容：开关=True 且 breast_size=不指定 → 视为巨乳。
                "big_breasts": ("BOOLEAN", {"default": False}),
                # v3.10.0 追加：**按作品 IP 限定随机角色**。
                # 只在"没点选角色、也没手填角色"时生效 —— 于是每次运行都从
                # 这些作品里重新抽（配合 seed=-1 就是"每跑一次自动换人"）。
                # 值接受 作品 slug / 英文名 / 中文名，多个用 | 分隔。
                "role_works": ("STRING", {"default": "", "multiline": False}),
                # v3.17.0 追加：**胸部大小等级**（取代上面的巨乳开关）。
                # 「不指定」= 原开关关闭的行为（figure 参与随机、可被角色官方
                # 设定覆盖）；指定档位 = 锁定 figure，最高优先级（覆盖角色
                # APPEAR 对齐与权重，与原巨乳开关语义一致）。
                # 六档 tag 已逐一验证（giant_breasts 是 danbooru 0 帖无效 tag，
                # 极限档用 gigantic_breasts）。
                "breast_size": (BREAST_SIZE_CHOICES, {"default": "不指定"}),
                # v3.19.0 追加：**服装风格智配**（追加在末尾 —— 控件顺序是契约）。
                # 「随机」= 智配开、锚点自动（整套 > 上装 > 下装）；
                # 「不限」= 智配整体关闭，回到 v3.18 的纯随机（便于对照复现）；
                # 具体风格 = 整套服装硬收敛到该风格，其余部位软加权。
                # ⚠️ 只作用于服装六类字段，不碰发型/五官/姿势/场景。
                "outfit_style": (OUTFIT_STYLE_CHOICES, {"default": "随机"}),
                # v3.38.0 追加：**只在收藏角色里随机**（追加在末尾 —— 控件顺序是契约）。
                # 与之配套的 `role_works` 仍管"按 IP 限定"，两者同时给时取交集。
                "role_fav_only": ("BOOLEAN", {"default": False}),
                # v3.45.0 追加：**角色官方发型对齐**（追加在末尾 —— 控件顺序是契约）。
                # 开：按 presets/char_hair_style.json 把 hair_len / hair_tie /
                #     hair_curl 写成该角色的官方发型，并保护不被密度裁剪削掉；
                # 关：三项完全随机（与 v3.44.x 行为一致）。
                # 与 appearance_follow **互相独立**：发色关了也能单开发型。
                "hair_follow": ("BOOLEAN", {"default": True}),
                # ── v3.47.0：发型选择器（4 个下拉，追加在末尾）──────────────
                # 每个都含「角色标签发型 / 随机 / 该字段词库全部取值」。
                # 全部选「角色标签发型」= 与旧 hair_follow=True 完全等价（零漂移）；
                # 只要有一项不是「角色标签发型」，就由选择器逐项接管，hair_follow 不再参与。
                # ⚠️ 控件顺序一旦发布就是契约，这四个只能**追加**，永远不许插到中间。
                "hair_len_pick": (R.hair_pick_options("hair_len"),
                                  {"default": R.HAIR_PICK_AUTO}),
                "hair_tie_pick": (R.hair_pick_options("hair_tie"),
                                  {"default": R.HAIR_PICK_AUTO}),
                "hair_curl_pick": (R.hair_pick_options("hair_curl"),
                                   {"default": R.HAIR_PICK_AUTO}),
                "hair_acc_pick": (R.hair_pick_options("hair_acc"),
                                  {"default": R.HAIR_PICK_AUTO}),
                # ── v4.1.0：**不指定角色**（追加在末尾 —— 控件顺序是契约）──────
                # 勾掉 = 这一抽一个角色都不抽（手填 / 角色库已选也一并让路，
                # 两者都不销毁），发色 / 瞳色 / 体型 / 发型全部按词库权重随机
                # —— 就是"没有 IP 的纯词库人物"。
                # 界面入口只有面板「角色库」页签那一个复选框（节点上隐藏，
                # 见 js 的 HIDDEN 表），与「段开关只有面板一个入口」同一条房规。
                "role_pick": ("BOOLEAN", {"default": True}),
                # ── v5.0.1：**场景搭配随机抽取**（追加在末尾 —— 控件顺序是契约）──
                # 勾上 = 本次从场景库按所选 NSFW 档位抽一条（锁档、必出）；
                # 不勾 = **完全不自动套场景**（v5.0.1 起废掉原来「每轮 12%、
                # best-of-8 下来约 60%」的概率抽签 —— 用户 2026-11-01 口径）。
                # 界面入口只有面板（见 js 的 HIDDEN 表），与「段开关只有一个入口」同规。
                # ⚠️ 追加在末尾 ⇒ 老工作流不受影响，**不需要迁移脚本**。
                "scene_pick": ("BOOLEAN", {"default": False}),
            },
            # ── v6.5.0：**皮肤干爽过滤**（用户 2026-11-07）──────────────
            # 两个控件入口只在随机面板（见 js 的 HIDDEN 表）：
            #   dry_skin_filter = 总开关（默认关 = 逐字节零漂移）
            #   dry_skin_groups = 勾选的分组 id，逗号分隔；空 / all = 全部
            # ⚠️ 引擎侧只在成品 tag 列表层剔除，不吃 rng、不改档位/互斥。
            # ⚠️⚠️ 必须放 **optional** 而不是 required：老 API 格式工作流与外部
            #    脚本提交时根本不带这两个键，放 required 会被 ComfyUI 核心校验
            #    判 "Required input is missing: dry_skin_*" 并**丢弃整张输出**
            #    （2026-11-07 18:20 重启后实测连挂 3 次）。
            #    控件顺序仍在最末（required 之后才轮到 optional），widgets_values
            #    不错位；generate() 有默认值、IS_CHANGED 用 kwargs.get 兜底，
            #    缺失即关闭态 = 零漂移。
            "optional": {
                "dry_skin_filter": ("BOOLEAN", {"default": False}),
                "dry_skin_groups": ("STRING", {"default": "", "multiline": False}),
                # v6.5.2：逐条模式 —— 逗号分隔的英文 tag；"all"/"none"/空串
                # 三种哨兵语义见 R.skin_moisture_ban_set()。
                # ⚠️ 必须追加在 optional 末尾（控件顺序是契约）。
                "dry_skin_tags": ("STRING", {"default": "", "multiline": False}),
                # v6.6.0：**双女自动补位**（用户 2026-11-07）。
                # 勾上（默认）= 「只选了 1 位角色 + 双女/多女人数」时自动补一位
                # 同作品的女性角色，避免模型把同一个人画两遍；该 IP 只有她一位
                # 女性时回退全库随机。取消 = 完全不介入（零 rng 消耗）。
                # ⚠️ 入口只有面板「角色库」页签那一个复选框（js 的 HIDDEN 表），
                #    与「🚫 不指定角色」同一条房规。
                # ⚠️ 同样必须放 **optional** 末尾：放 required 会让不带这个键的
                #    老 API 工作流整张输出被丢弃（v6.5.0 踩过）。
                "duo_role_fill": ("BOOLEAN", {"default": True}),
                # ── v6.7.0：**勾选池每次自动随机**（用户 2026-11-08）────────
                # 面板在「🧘 姿势库 / 🎨 艺术构图 / 🔍 特写搭配」三页各有一个
                # 「每次运行自动随机」复选框（互斥，同时只能开一组），勾上后
                # 每次运行从**当前勾选集合**里换一条，等价于自动点一次「套用」。
                #   pick_pool_mode = "" / "pose" / "art" / "near"（只影响报告措辞）
                #   pick_pool_ids  = 逗号分隔的场景 id（面板勾选集）
                # ⚠️ 与 `scene_pick` 同时开时**勾选池优先**（用户 2026-11-08 拍板），
                #    引擎侧靠"落成 forced.n_canon"天然压过整库随机。
                # ⚠️ 必须追加在 optional 末尾（控件顺序是契约）。
                "pick_pool_mode": ("STRING", {"default": "", "multiline": False}),
                "pick_pool_ids": ("STRING", {"default": "", "multiline": False}),
            },
            # ⚠️ hidden 输入**不是控件**、不占控件位、不进 widgets_values
            #    ⇒ 老工作流零影响，**不需要工作流迁移脚本**。
            #    它是 v3.6.0「把正文写进 PNG 元数据」的入口：
            #    EXTRA_PNGINFO 是同一次执行里所有节点共享的 dict，原生 SaveImage
            #    最后才序列化它 —— 插件在这里写，出图的元数据里就有正文了。
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "report", "level", "cn_summary")
    FUNCTION = "generate"
    CATEGORY = SECTION_NAME

    @classmethod
    def IS_CHANGED(cls, seed=-1, **kwargs):
        """seed<0 时每次都当成"变了"，否则 ComfyUI 会拿缓存不重抽。

        v5.9.0：seed>=0 时把**面板全局状态**也拼进指纹 —— 服装库启用态 /
        袜子鞋子覆盖 / 覆盖场景推荐服装开关 / 角色服装预设启用态改了以后，
        即使工作流与种子一字未动，节点也必须重跑（见
        `_global_state_fingerprint()` 的说明）。指纹只决定「是否重新执行」，
        不进 `generate()`，正文零漂移。
        """
        try:
            if int(seed) < 0:
                return f"rand:{time.time_ns()}:{next(_CHANGED_TICK)}"
        except (TypeError, ValueError):
            return f"rand:{time.time_ns()}:{next(_CHANGED_TICK)}"
        # v6.5.0：干爽过滤开关虽然是节点输入，但 seed>=0 时 IS_CHANGED 已经
        # 接管「是否重跑」的判定 —— 不并进指纹的话，切换开关会拿到旧输出
        # （与 v5.9.0 服装库全局态那个坑同源）。
        return (f"seed:{seed}|state:{_global_state_fingerprint()}"
                f"|dry:{int(bool(kwargs.get('dry_skin_filter')))}"
                f":{str(kwargs.get('dry_skin_groups') or '')}"
                f"|drytags:{str(kwargs.get('dry_skin_tags') or '')}"
                # v6.6.0：双女补位开关也要进指纹 —— 否则 seed>=0 时切换它
                # 会直接复用上一次的输出（与干爽过滤开关同源的老坑）。
                f"|duofill:{int(bool(kwargs.get('duo_role_fill', True)))}"
                # v6.7.0：勾选池变了就得重抽（否则 seed>=0 时换了一套勾选
                # 还会复用上一次的输出）
                f"|pool:{str(kwargs.get('pick_pool_mode') or '')}"
                f":{str(kwargs.get('pick_pool_ids') or '')}")

    def generate(self, nsfw_level, seed, tag_cap, base_prob,
                 disabled_sections, lock_fields, force_role,
                 weights_json, extra_body, appearance_follow=True,
                 selected_roles="", big_breasts=False, role_works="",
                 breast_size="不指定", outfit_style="随机",
                 role_fav_only=False, hair_follow=True,
                 hair_len_pick="", hair_tie_pick="", hair_curl_pick="", hair_acc_pick="",
                 role_pick=True, scene_pick=False,
                 dry_skin_filter=False, dry_skin_groups="", dry_skin_tags="",
                 duo_role_fill=True,
                 pick_pool_mode="", pick_pool_ids="",
                 extra_pnginfo=None, prompt=None, unique_id=None):
        enabled = set(R.section_ids()) - _split_ids(disabled_sections)
        # v3.46.2：**从 weights_json 读 forced**（不改控件顺序 = 契约安全）。
        # 为什么必须加：场景搭配库那 260 条的场景 id（sc_xxx）**不在** `n_canon`
        # 字段的选项池里（那 14 个是法典 c1..c14），所以给 options.n_canon 加权
        # 永远抽不到指定场景（实测 state["n_canon"] 会落到 c3 之类的法典条目）。
        # 而 `draw(forced={"n_canon": "sc_xxx"})` 是有效的 —— 节点原先没把这个
        # 参数接出来，于是"按场景 id 定点出图"从节点层根本做不到。
        # 用法：weights_json 里加一段 `{"forced": {"n_canon": "sc_ccc_voyeur_cam"}}`
        _forced = {}
        try:
            _raw = json.loads(weights_json) if str(weights_json or "").strip() else {}
            _f = _raw.get("forced") or {}
            if isinstance(_f, dict):
                _forced = {str(k): str(v) for k, v in _f.items()
                           if v is not None and str(v).strip() != ""}
        except Exception:
            _forced = {}
        res = R.draw(
            level=nsfw_level,
            seed=seed,
            base_prob=base_prob,
            weights=R.parse_weights(weights_json),
            enabled_sections=enabled,
            locks=_split_ids(lock_fields),
            tag_cap=tag_cap,
            force_role=str(force_role or "").strip(),
            extra_body=extra_body,
            appearance_follow=bool(appearance_follow),
            selected_roles=R.parse_selected_roles(selected_roles),
            big_breasts=bool(big_breasts),
            breast_size=str(breast_size or "不指定"),
            role_works=str(role_works or "").strip(),
            outfit_style=str(outfit_style or "随机"),
            role_fav_only=bool(role_fav_only),
            # v3.45.0：角色官方发型对齐
            hair_follow=bool(hair_follow),
            # v4.1.0：要不要角色（面板「🚫 不指定角色」；False = 一个都不抽）
            role_pick=bool(role_pick),
            # v5.0.1：场景搭配随机抽取（面板「🎬 随机场景」；False = 一条都不出）
            scene_pick=bool(scene_pick),
            # v3.46.2：场景定点锁定（weights_json 里的 forced 段，见上方解析）
            forced=_forced,
            # v3.47.0：发型选择器 —— 四项各自「角色标签发型 / 随机 / 具体值」
            hair_picks={
                "hair_len": str(hair_len_pick or "").strip(),
                "hair_tie": str(hair_tie_pick or "").strip(),
                "hair_curl": str(hair_curl_pick or "").strip(),
                "hair_acc": str(hair_acc_pick or "").strip(),
            },
            # v6.5.0：皮肤干爽过滤（默认全关 → 零漂移）
            dry_skin_filter=bool(dry_skin_filter),
            dry_skin_groups=str(dry_skin_groups or "").strip(),
            # v6.5.2：逐条模式（空串 = 回落组语义，零回归）
            dry_skin_tags=str(dry_skin_tags or "").strip(),
            # v6.6.0：双女自动补位（默认开；关掉 = 零 rng 消耗，零漂移）
            duo_role_fill=bool(duo_role_fill),
            # v6.7.0：勾选池每次自动随机（空 mode / 空 ids = 关闭，零漂移）
            pick_pool_mode=str(pick_pool_mode or "").strip(),
            pick_pool_ids=str(pick_pool_ids or "").strip(),
        )
        # v3.6.0：把正文写进 PNG 元数据（不改用户工作流 —— 见 anima_v4_pngmeta）。
        # ⚠️ 永远不能因为元数据写失败而让出图挂掉，模块内部已兜住异常；
        #    这里再包一层，连 import 之外的问题也不外溢。
        try:
            st = PM.inject_metadata(
                extra_pnginfo, prompt, res["text"],
                self_ids={str(unique_id)} if unique_id is not None else frozenset(),
                version=_v3_version())
            _record_meta(st)
        except Exception as exc:                      # pragma: no cover
            print(f"[AnimaWardrobeV6] 元数据注入失败（不影响出图）: {exc}")
        return (res["text"], R.format_report(res),
                res["level_label"], res["cn_summary"])


class AnimaV4PromptGrader:
    """任意提示词 → NSFW 档位 + 内容分类明细。

    与 AnimaV4RandomPrompt 共用同一套判定表（anima_v4_nsfw），
    所以"生成时判多少档"和"事后查多少档"永远不会打架。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"default": "", "multiline": True}),
            },
            "optional": {
                "detailed": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "STRING", "STRING")
    RETURN_NAMES = ("text", "num", "level", "report")
    FUNCTION = "grade"
    CATEGORY = SECTION_NAME

    def grade(self, text, detailed=True):
        tags = [t.strip() for t in str(text or "").split(",") if t.strip()]
        rep = N.grade_nsfw(tags)
        lines = [
            f"档位 : {rep['num']} / {rep['max']}  ·  {rep['label']}（{rep['level']}）",
            f"基线 : {rep['reason']}",
            f"标签 : {len(tags)} 个",
        ]
        if rep["categories"]:
            lines.append("成人向内容：")
            for label, items in rep["categories"].items():
                shown = ", ".join(items[:6])
                more = " …" if len(items) > 6 else ""
                lines.append(f"  · {label}（{len(items)}）: {shown}{more}")
        else:
            lines.append("成人向内容：无")
        if detailed and rep["hits"]:
            lines.append("命中标签：" + ", ".join(rep["hits"][:20]))
        return (str(text or ""), int(rep["num"]), rep["level"], "\n".join(lines))


class AnimaV4PromptAssemble:
    """把随机正文与手写补充按 ANIMA3 槽位序合并。

    ⚠️ 只做**拼接、去重、消解互斥**三件事，不改任何词条 ——
       上游是造型台/画师选择器写进来的内容时，这里不抢笔。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "main": ("STRING", {"default": "", "multiline": True}),
                "supplement": ("STRING", {"default": "", "multiline": True}),
                "mode": (["追加到末尾", "合并去重", "仅透传 main"],),
            },
            "optional": {
                "max_level": (["不限"] + LEVEL_CHOICES, {"default": "不限"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "assemble"
    CATEGORY = SECTION_NAME

    def assemble(self, main, supplement, mode, max_level="不限"):
        main_s = str(main or "").strip().rstrip(",")
        sup_s = str(supplement or "").strip().rstrip(",")

        if mode == "仅透传 main" or not sup_s:
            return (main_s,)

        if mode == "追加到末尾":
            return (f"{main_s}, {sup_s}" if main_s else sup_s,)

        # 合并去重：保留 main 的顺序，supplement 里重复的丢掉；
        # 再跑一遍互斥消解 —— 手写补句最容易和随机结果撞车
        # （比如随机到"全裸"，手写又写了"黑色过膝袜"）。
        seen, out = set(), []
        for chunk in (main_s, sup_s):
            for t in chunk.split(","):
                t = t.strip().lower()
                if t and t not in seen:
                    seen.add(t)
                    out.append(t)
        out = R.apply_mutex(out)

        if max_level != "不限":
            lv = R.level_num(max_level)
            # v3.16.0 口径统一：过滤判据从 grade_nsfw（判定表裸口径）改为
            # tag_level（校准口径）—— 与抽取准入 eligible_options 同一把尺子。
            # 否则选「2 · 成人」会把合法的 2 档校准词（paizuri / cum on breasts /
            # wet hair 等，判定表原始档 3 或 2）当成超档丢掉，与随机抽取的行为自相矛盾。
            kept, _removed = [], []
            for t in out:
                (kept if int(R.tag_level(t)) <= lv
                 else _removed).append(t)
            out = kept
        return (", ".join(out),)


class AnimaV4PromptCheck:
    """任意提示词 → 检查 + 按档位自动修复（v3.8.0 新增）。

    与另外两个节点的分工：

      · AnimaV4PromptGrader  只看**档位与成人向分类**（与造型台判定表逐字一致）；
      · AnimaV4PromptAssemble 只做**拼接**（不判断、不修改词条）；
      · AnimaV4PromptCheck   **诊断 + 治疗** —— 跑同一套 17 条致命项 / 10 条风格项，
                             并可按目标档位自动修复可修项。

    「可修项」三类（与生成流程里的 `R.repair` 完全是同一个函数，不另写一套）：
      · 档位   —— 超档标签剔除（已登记校准项除外）；
      · 互斥   —— 数量 / 视角 / 眼罩 / 服装 / 设备景深 / 体位；
      · 格式   —— 下划线归一 + 去重。

    ⚠️ 「超长且不在词库内的标签」「标签数量」**不自动修** ——
       前者可能正是你手写的作品名，后者该调密度/上限，不是这里削。
       这两条会如实出现在报告里。

    ⚠️ 本节点**不改现有工作流**：它是新增的第 4 个节点，没接就完全不存在。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"default": "", "multiline": True}),
                "level": (LEVEL_CHOICES, {"default": "sfw"}),
            },
            "optional": {
                # 想"只看不动"就把这个关掉：关掉后只出报告，正文原样透传
                "auto_fix": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("text", "report", "level", "num")
    FUNCTION = "check_and_fix"
    CATEGORY = SECTION_NAME

    def check_and_fix(self, text, level="sfw", auto_fix=True):
        lv_key = R.resolve_level(level)
        lv = R.LEVEL_NUM[lv_key]
        tags = [t.strip() for t in str(text or "").split(",") if t.strip()]

        fixes = []
        if auto_fix and tags:
            tags, fixes = R.repair(tags, lv)

        _state, report = R.check_text(tags, lv)
        out_text = ", ".join(tags)
        rep = R.format_check_report(
            tags, lv, report, fixes=fixes, level_label_txt=R.level_label(lv_key))
        return (out_text, rep, lv_key, int(report["real_level"]))


NODE_CLASS_MAPPINGS = {
    # ── v6.0.0 主名 ────────────────────────────────────────────────
    "AnimaV6RandomPrompt": AnimaV4RandomPrompt,
    "AnimaV6PromptGrader": AnimaV4PromptGrader,
    "AnimaV6PromptAssemble": AnimaV4PromptAssemble,
    "AnimaV6PromptCheck": AnimaV4PromptCheck,
    # ⚠️ v6.0.0：**V5 / V4 / V3 类名全部保留为别名**。工作流 JSON 里存的是节点类型名，
    #    只注册 V6 会让所有旧工作流（用户已有 300+ 张）报「节点缺失」。
    #    别名指向同一个类，旧工作流打开后功能完全一致。
    # ⚠️ 别名会让节点菜单里出现 4 套同名节点（V6 / V5 / V4 / V3）—— 这是兼容的代价，
    #    新工作流请用 V6。不要为了让菜单干净而删别名，删了旧工作流直接打不开。
    "AnimaV5RandomPrompt": AnimaV4RandomPrompt,
    "AnimaV5PromptGrader": AnimaV4PromptGrader,
    "AnimaV5PromptAssemble": AnimaV4PromptAssemble,
    "AnimaV5PromptCheck": AnimaV4PromptCheck,
    "AnimaV4RandomPrompt": AnimaV4RandomPrompt,
    "AnimaV4PromptGrader": AnimaV4PromptGrader,
    "AnimaV4PromptAssemble": AnimaV4PromptAssemble,
    "AnimaV4PromptCheck": AnimaV4PromptCheck,
    "AnimaV3RandomPrompt": AnimaV4RandomPrompt,
    "AnimaV3PromptGrader": AnimaV4PromptGrader,
    "AnimaV3PromptAssemble": AnimaV4PromptAssemble,
    "AnimaV3PromptCheck": AnimaV4PromptCheck,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AnimaV6RandomPrompt": "Anima 提示词随机生成（V6）",
    "AnimaV6PromptGrader": "Anima 提示词分级审查（V6）",
    "AnimaV6PromptAssemble": "Anima 提示词拼接台（V6）",
    "AnimaV6PromptCheck": "Anima 提示词检查与修复（V6）",
    # 旧名别名也要配显示名：不配的话前端退回类名（`AnimaV5RandomPrompt`），
    # 节点标题会变成一串英文，比「（V5）」难看得多。
    "AnimaV5RandomPrompt": "Anima 提示词随机生成（V5）",
    "AnimaV5PromptGrader": "Anima 提示词分级审查（V5）",
    "AnimaV5PromptAssemble": "Anima 提示词拼接台（V5）",
    "AnimaV5PromptCheck": "Anima 提示词检查与修复（V5）",
    "AnimaV4RandomPrompt": "Anima 提示词随机生成（V4）",
    "AnimaV4PromptGrader": "Anima 提示词分级审查（V4）",
    "AnimaV4PromptAssemble": "Anima 提示词拼接台（V4）",
    "AnimaV4PromptCheck": "Anima 提示词检查与修复（V4）",
    "AnimaV3RandomPrompt": "Anima 提示词随机生成（V3）",
    "AnimaV3PromptGrader": "Anima 提示词分级审查（V3）",
    "AnimaV3PromptAssemble": "Anima 提示词拼接台（V3）",
    "AnimaV3PromptCheck": "Anima 提示词检查与修复（V3）",
}

# 前端面板要用的契约（段/字段/档位/槽位/权重默认值）。
# 与造型台一样只暴露"结构"，不暴露任何词条内容 —— 词条走 pools 路由。
def contract_payload() -> dict:
    return {
        "plugin": "Comfyui-Anima-Wardrobe-V6",
        "levels": [
            {"num": n, "key": k, "label": l, "desc": d}
            for n, k, l, d in N.NSFW_SCALE
        ] + [{"num": 99, "key": "any", "label": "不限", "desc": "不设上限，全库开放"}],
        "sections": [
            {
                "id": s["id"],
                "title": s["title"],
                "cls": s.get("cls", ""),
                "max_level": R.section_max_level(s["id"]),
                "fields": [
                    {
                        "key": f["key"],
                        "lab": f["lab"],
                        "req": bool(f.get("req")),
                        "type": f.get("type", "select"),
                        "tail": bool(f.get("tail")),
                        "slot": R.FIELD_SLOT.get(f["key"], ""),
                        "option_count": len(f.get("opts", [])),
                    }
                    for f in s["fields"]
                ],
            }
            for s in R.sections()
        ],
        "field_slot": dict(R.FIELD_SLOT),
        # v6.7.0：特写「收紧取景」白名单 —— **真源在引擎**
        # （`anima_v4_rules.NEAR_VISIBLE` 等）。前端读这里，
        # 别再自己抄一份；找不到才回落本地常量（老版本 / 夹具缺失）。
        "near_slim": {
            "visible": {k: list(v) for k, v in R.NEAR_VISIBLE.items()},
            "drop_sections": {k: list(v) for k, v in R.NEAR_DROP_SECTIONS.items()},
            "drop_role": {k: bool(v) for k, v in R.NEAR_DROP_ROLE.items()},
            "local_sections": list(R.NEAR_LOCAL_SECTIONS),
        },
        "slots": list(R.SLOTS),
        "weights": {
            "min": R.WEIGHT_MIN,
            "max": R.WEIGHT_MAX,
            "default": R.DEFAULT_WEIGHT,
            "base_prob_default": R.DEFAULT_BASE_PROB,
        },
        "tag_cap_choices": TAG_CAP_CHOICES,
        "level_choices": LEVEL_CHOICES,
        # v6.5.0：皮肤干爽过滤分组。前端与夹具都从这里读，
        # 唯一真源是引擎的 SKIN_MOISTURE_GROUPS（别在 js 里再抄一份）。
        "dry_skin_groups": [
            {"id": k, "zh": zh, "tags": list(tags),
             "items": [{"en": en, "zh": z} for en, z in R.skin_moisture_items(k)]}
            for k, zh, tags in R.skin_moisture_group_options()
        ],
    }


def pools_payload() -> dict:
    """面板用的完整词库（含中文名 + 每条选项的档位）。"""
    lv = R.levels()
    opt_lv = lv.get("option_levels", {})
    canon_lv = lv.get("canon_levels", {})

    def _opts(f):
        """字段的候选列表。

        ⚠️ `n_canon` 的候选来自两处：pools.json 里的法典 14 组
           + 独立文件里的场景搭配库（自己带 level）。这里必须与
           `rules.eligible_options('n_canon', lv)` 用**同一份合并逻辑**，
           否则面板列出来的选项和引擎实际能抽到的不是一回事
           （历史教训：面板与引擎两套口径 = 用户看到 A 却抽出 B）。
        """
        pairs = list(f.get("opts", []))
        if f["key"] == "n_canon":
            # v3.18.0：这里必须 include_manual=True —— 权重树要能看到特写搭配，
            #    否则"套用后权重袋里有它、候选里却没有"，面板会显示成幽灵项。
            #    （引擎的随机准入是另一回事，见 rules._canon_pool_for_draw）
            pairs += [(zh, sid) for zh, sid, _n in R.scene_options(99, include_manual=True)]
        out = []
        for zh, en in pairs:
            if f["key"] == "n_canon":
                lvl = R.canon_level(en)
            else:
                lvl = int(opt_lv.get(f["key"], {}).get(en, 0))
            out.append({"zh": zh, "en": en, "level": lvl})
        return out

    return {
        "_meta": R.pools().get("_meta", {}),
        "sections": [
            {
                "id": s["id"],
                "title": s["title"],
                "cls": s.get("cls", ""),
                "max_level": R.section_max_level(s["id"]),
                "fields": [
                    {
                        "key": f["key"],
                        "lab": f["lab"],
                        "req": bool(f.get("req")),
                        "type": f.get("type", "select"),
                        "tail": bool(f.get("tail")),
                        "slot": R.FIELD_SLOT.get(f["key"], ""),
                        "opts": _opts(f),
                    }
                    for f in s["fields"]
                ],
            }
            for s in R.sections()
        ],
        "canon": [
            {"id": k, "level": R.canon_level(k), "fields": sorted(v.keys())}
            for k, v in sorted(R.canon().items())
        ],
        "counters": {
            "sections": len(R.sections()),
            "fields": len(R.field_keys()),
            "options": sum(len(f.get("opts", []))
                           for s in R.sections() for f in s["fields"]),
            "roles": len(R.field_by_key("role")[1]["opts"]),
            "appear": len(R.appear()),
            "scenes": len(R.scene_list()),
        },
    }


def _variant_items_of(sc: dict) -> list:
    """把 `random_variants` 的两种写法（数组 / 多池对象）拍平成条目数组。"""
    raw = sc.get("random_variants")
    out = []
    if isinstance(raw, list):
        out = [v for v in raw if isinstance(v, dict)]
    elif isinstance(raw, dict):
        for v in raw.values():
            body = v.get("items") if (isinstance(v, dict) and "items" in v) else v
            if isinstance(body, list):
                out += [x for x in body if isinstance(x, dict)]
    return out


def scenes_payload() -> dict:
    """场景搭配库（按 NSFW 档位分组 + 预设明细），给面板的「🎬 场景搭配」页签。

    与角色库同一条纪律：**结构由后端组装**，前端只渲染 ——
    这样测试夹具（tools/dump_ui_fixtures.py）能从真后端导出同一份数据。

    ⚠️ v3.10.0 起分组维度是 **NSFW 档位（L0–L4）**，不是 sfw/nsfw 两个大类：
       档位是**派生数据**（`tools/build_scenes.py` 按内容算出来的），天然可信；
       而且它和节点上的档位按钮同名，用户点着「过激」就能直接找到对应搭配。
       `group`（sfw/nsfw）退居为题材层的一个字段，`theme` 给出更细的题材标签
       （NTR / BDSM / 日常 …）显示在卡片上。
    """
    groups = R.scene_groups()
    scenes = []
    for sc in R.scene_list():
        rows = []
        for k, v in (sc.get("tags") or {}).items():
            _sec, fld = R.field_by_key(k)
            rows.append({
                "field": k,
                "lab": (fld or {}).get("lab", k),
                "en": v,
                "level": R.option_level(k, v),
            })
        extra = str(sc.get("extra") or "")
        scenes.append({
            "id": sc["id"],
            "zh": sc.get("zh", ""),
            "group": sc.get("group", ""),
            "theme": sc.get("theme", ""),
            # v5.11.0：武器场景的「作品 → 武器」两级元数据。
            # ⚠️ 只做面板分类 / 卡片标题 / 搜索，**绝不进提示词正文** ——
            #    作品 tag 会带出该作品的角色脸，与用户点选的角色打架。
            #    其它组没有这四个键，回退成空串，前端照旧渲染。
            "work": sc.get("work", ""),
            "work_zh": sc.get("work_zh", ""),
            "weapon": sc.get("weapon", ""),
            "weapon_en": sc.get("weapon_en", ""),
            # v5.0.20：背景五分类（纯色棚拍 / 双色对角 / 花园俯拍 / 室内实景 / 黑底逆光）。
            # 与 theme 是两个维度：theme 是具体配色或题材，bg 是"这套背景属于哪一类"，
            # 面板据此出一条独立的背景筛选条。空值 = 该场景没归类（前端不出按钮）。
            "bg": sc.get("bg", ""),
            "desc": sc.get("desc", ""),
            "level": int(sc.get("level", 0)),
            "fields": rows,
            "extra": extra,
            "extra_count": len([t for t in extra.split(",") if t.strip()]),
            # v3.18.0：特写搭配（manual_only）—— 只允许手动套用，页面与随机都分开
            "manual": bool(sc.get("manual_only")),
            "part": sc.get("theme", "") if sc.get("manual_only") else "",
            # v3.18.3：这一条是否有可见的脸/头发（决定套用时保不保留角色）
            "role_ok": bool(sc.get("role_ok")),
            # v3.32.0：`no_role` —— 这一条的画框里**根本没有脸**（第一人称自我视角）。
            # 套用时前端要清空角色三件套并锁住 role，否则随机抽到的角色 tag
            # 会把镜头从"自己的身体"拉回一张脸。与特写页的 `dropRole` 同一个动作，
            # 区别只是它不精简字段（普通场景，不经过 nearSlim）。
            "no_role": bool(sc.get("no_role")),
            # 人数钉死值（如双女条目的 `2girls, yuri`；空 = 默认单人）
            "count_lock": str(sc.get("count_lock") or ""),
            # v5.0.1：推荐画幅 —— portrait=竖屏 / landscape=横屏 / both=通用。
            # 来源是 presets/anima_v4_scene_aspects.json（旁挂映射，不是 scenes.json），
            # 缺席时一律 both。前端用它画徽标 + 做「竖屏·通用 / 横屏·通用」筛选。
            "aspect": R.scene_aspect(sc["id"]),
            # v3.22.0：套用时**强制清零**的字段（不是"写成某个值"）。
            # 典型用法：特写要保持画框纯净，但某字段写任何值都会把多余部位拉进来
            #（口交特写的 `pose_base` —— 写 `kneeling` 就出全身，不写则随机抽一个姿势）。
            "drop": [str(k) for k in (sc.get("drop_fields") or [])],
            # v3.22.0：候选随机字段 `{字段: [候选...]}` —— 套用时从候选里抽一个，
            # 让同一条特写每次的口部状态 / 表情不同（前端要据此把这些字段留在白名单里）。
            "random": {str(k): [str(v) for v in vals]
                       for k, vals in (sc.get("random_fields") or {}).items()},
            # v3.24.0 / v3.27.0：候选构图变体（只有名字给面板显示，实际短语由引擎抽）。
            # 数组=单池、对象=多池，两种写法都要能报给面板。
            "variants": [str(v.get("zh") or "")
                         for v in _variant_items_of(sc) if v.get("extra")],
        })

    # 档位分组（面板一级分组用）。空组保留在数组里、带上 count=0 ——
    # 前端可以选择跳过，但"有哪几档"这件事本身是契约的一部分。
    level_groups = []
    for n, key, label, desc in N.NSFW_SCALE:
        level_groups.append({
            "num": n, "key": key, "title": f"{n} · {label}", "desc": desc,
            "count": sum(1 for s in scenes if s["level"] == n),
        })

    # 特写搭配（manual_only）：部位顺序按**库里的书写顺序**来，不排序 ——
    # 排序会把「面部 → 眼与唇 → 上半身与胸 → 手 → 腿 → 脚」这种由整体到局部的
    # 编排打乱，而那正是用户找条目时的心理顺序。
    near_parts = []
    for sc in scenes:
        # v5.0.9：manual_only 不再等于"特写"—— 艺术构图（group=art）同样 manual_only。
        # 部位条只该收 group=near 的 theme，否则会把「艺术构图」也塞进部位列表。
        if sc.get("group") == "near" and sc.get("manual") and sc.get("part") \
                and sc["part"] not in near_parts:
            near_parts.append(sc["part"])

    return {
        "available": R.has_scene_library(),
        "meta": R.scenes_data().get("_meta", {}),
        "groups": groups,
        "level_groups": level_groups,
        "near_parts": near_parts,
        "scenes": scenes,
        "counters": {
            "scenes": len(scenes),
            "groups": len(groups),
            "near": sum(1 for s in scenes if s.get("manual") and s.get("group") == "near"),
            "by_level": {str(n): sum(1 for s in scenes if s["level"] == n)
                         for n in range(5)},
        },
    }


def characters_payload() -> dict:
    """面板用的角色库（作品列表 + 角色列表）。

    ⚠️⚠️ 这里必须补一个 `work_id` —— 它是 v3.1.0「点 IP 作品后角色列表
        空掉」那个 bug 的根因所在：

        角色记录里的 `work` 存的是**作品英文名**（`zenless zone zero`），
        而作品列表的 `id` 是 slug（`zenless_zone_zero`）。
        面板点作品行时写进去的是 `w.id`（slug），筛选时却拿
        `c.work === ui.charWork` 比 —— 一边 slug 一边英文名，
        **永远匹配不上**，所以点任何 IP 都是空列表。
        （实测：character.work ⊂ works.id → False；⊂ works.en → True）

        补 `work_id` 之后，前端筛选全程只用同一个键，不再有歧义。

    2000+ 条一次性发过去（JSON 约 780 KB，gzip 后 80 KB 左右），
    比按需分页简单得多，而且浏览翻页零延迟。
    """
    data = R.characters_data()
    works = data.get("works", [])
    # 英文名 → slug。用 setdefault：万一有同名作品，保留第一个而不是被覆盖。
    id_of_work = {}
    for w in works:
        id_of_work.setdefault(w.get("en", ""), w.get("id", ""))
    return {
        "available": R.has_character_library(),
        "meta": data.get("_meta", {}),
        "works": works,
        "characters": [
            {
                "tag": c["tag"],
                "name": c.get("name", ""),
                "zh": c.get("zh", ""),
                "work": c.get("work", ""),
                "work_id": id_of_work.get(c.get("work", ""), ""),
                # ⚠️ v3.5.0 补上：编辑弹窗的「角色标签」输入框要预填它。
                #    之前不发这个字段（卡片展示用不上），结果是**打开编辑弹窗时
                #    角色标签是空的** —— 不手动重填就被"不能为空"拦住，
                #    手填还可能把原值改错。代价只有约 70KB（26.7 字符/条 × 2687）。
                "trigger": c.get("trigger", ""),
                "pop": int(c.get("pop", 0)),
                "hair": c.get("hair", ""),
                "eyes": c.get("eyes", ""),
                "tags": c.get("tags", [])[:8],
                "thumb": c.get("thumb", ""),
                # v3.4.0：区分"内置角色"与"用户自定义角色"，前端据此显示角标、
                # 决定弹窗里给「删除」还是「恢复默认」。内置记录的 src 是
                # `wardrobe`（构建产物），用户新增的才被写成 `user`。
                "user": str(c.get("src") or "") == "user",
            }
            for c in data.get("characters", [])
        ],
    }


def character_detail_payload(tag: str) -> dict:
    """单个角色的**完整**记录 + 内置原始特征标签（v3.5.0，编辑弹窗用）。

    ⚠️ 为什么不把全量 tags 塞进列表载荷：`characters_payload()` 只发 tags[:8]
    （卡片展示够用）。2687 位全量标签会把 858KB 的载荷再撑大一半，而每次
    打开面板都要拉 —— 编辑弹窗一次只开一个角色，按需拉这一条最划算。

    ⚠️ `thumb` 是 sanitized slug，而 `tag` 可能带 `(` `)` `:` 等字符
    （实测 1762/2687 如此）—— 所以这个接口用**查询串** `?tag=`，
    不能做成 `/detail/{tag}` 那种路径参数（路径正则收不下这些字符）。

    `builtin_tags` / `tags_overridden` 是给"内置对照 + 一键还原"用的：
    只有内核记录存在时才给 builtin（自定义角色没有可回退的对象）。
    """
    tag = str(tag or "").strip()
    idx = R.char_index()
    m = idx.get(tag) or idx.get(tag.lower())
    if not m:
        return {"ok": False, "error": f"找不到角色：{tag}"}
    key = m.get("tag", "")
    b = R.builtin_char_index().get(key)
    patches = R.user_edits().get(key) or {}
    return {
        "ok": True,
        "tag": key,
        "user": str(m.get("src") or "") == "user",
        "trigger": m.get("trigger", ""),
        "zh": m.get("zh", ""),
        "work": m.get("work", ""),
        "thumb": m.get("thumb", ""),
        "hair": m.get("hair", ""),
        "eyes": m.get("eyes", ""),
        "tags": list(m.get("tags") or []),
        "builtin_tags": list(b.get("tags") or []) if b else [],
        "tags_overridden": "tags" in patches,
    }
