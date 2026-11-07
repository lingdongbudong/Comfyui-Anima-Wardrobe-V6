# -*- coding: utf-8 -*-
"""Comfyui-Anima-Wardrobe-V6

ANIMA3 提示词随机生成插件（含五级 NSFW 档位闸门与三层权重调节）。
零第三方依赖，纯 Python + 原生前端 JS。

与 Comfyui-Anima-Wardrobe（造型台 v2.x）**并存**：
类名、分类、HTTP 路由、前端脚本全部独立命名。两个插件同时安装时，
ComfyUI 启动日志里可能提示"同族插件"，属正常现象；
真正的冲突只有「同一个插件的两份副本」。

对外路由（全部挂在 ComfyUI 自己的 aiohttp 上，不额外起服务）。
v6.0.0 起前缀升为 /anima-wardrobe-v6/，**同时保留 v6 + v5 + v4 + v3 四套挂载** ——
旧前端（被浏览器缓存的 V5/V4 JS）与用户自己的外部脚本不会 404：
    GET  /anima-wardrobe-v6/pools     完整词库（含中文名与逐项档位）
    GET  /anima-wardrobe-v6/contract  段落 / 字段 / 槽位 / 档位契约
    GET  /anima-wardrobe-v6/scenes    场景搭配预设库（v3.9.0，分组 + 固定字段组合）
    GET  /anima-wardrobe-v6/characters 角色库（作品 + 角色，带 work_id）
    GET  /anima-wardrobe-v6/thumb/{slug} 角色缩略图
    GET  /anima-wardrobe-v6/roll-options  随机面板的初值
    POST /anima-wardrobe-v6/roll      跑一次抽取（与节点共用同一套引擎）
    GET  /anima-wardrobe-v6/presets          命名预设列表
    POST /anima-wardrobe-v6/presets/save     保存预设
    POST /anima-wardrobe-v6/presets/delete   删除预设
    GET  /anima-wardrobe-v6/favorites        收藏列表
    POST /anima-wardrobe-v6/favorites/toggle 收藏 / 取消收藏
    GET  /anima-wardrobe-v6/scenes/favorites        场景收藏列表（v3.43.0）
    POST /anima-wardrobe-v6/scenes/favorites/toggle 场景收藏 / 取消（v3.43.0）
    GET  /anima-wardrobe-v6/works/favorites         作品 IP 收藏列表（v5.0.4）
    POST /anima-wardrobe-v6/works/favorites/toggle  作品 IP 收藏 / 取消（v5.0.4）
    POST /anima-wardrobe-v6/characters/save        新增 / 编辑角色（v3.4.0）
    POST /anima-wardrobe-v6/characters/delete      删除自定义角色（v3.4.0）
    POST /anima-wardrobe-v6/characters/reset       内置角色恢复默认（v3.4.0）
    POST /anima-wardrobe-v6/characters/thumb       上传角色预览图（v3.4.0）
    POST /anima-wardrobe-v6/characters/thumb-clear 清除自定义预览图（v3.4.0）
    GET  /anima-wardrobe-v6/characters/detail      单个角色完整记录 + 内置标签（v3.5.0）
    GET  /anima-wardrobe-v6/health    自检
（以上每条都另外挂了一份 /anima-wardrobe-v4/ 与 /anima-wardrobe-v3/ 的同名副本。）
"""
from .nodes import (NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS,
                    character_detail_payload, characters_payload,
                    contract_payload, pools_payload, scenes_payload)

import os

WEB_DIRECTORY = "./js"

# 插件版本。改动记在 README 的「版本历史」里，health 路由也会回它 ——
# 排查"装的是哪一版"时比翻文件可靠。
__version__ = "6.7.0"

# 角色缩略图目录（约 2000 张 webp）。由 tools/fetch_character_thumbs.py 生成，
# 缺席时面板退化成首字母色块 —— 不阻塞任何功能。
_THUMB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "presets", "thumbs")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


def _jsonable(obj):
    """把 set / tuple 递归转成 list，保证 `web.json_response` 能序列化。

    ⚠️ v5.0.3 实测：`draw()` 的 state 里会出现 `_nl_dynamic`（多人外貌绑定句），
       类型是 **set**。单人场景不产生它，所以平时无感；一旦
       「勾了随机场景 + 允许自动抽角色」且场景是双人（或档位把人抬到多人），
       `/roll` 就在 `json.dumps` 这一层抛 `TypeError: Object of type set is not
       JSON serializable`，前端只看到 500 —— 排查时 aiohttp 的 traceback
       不进 ComfyUI 的日志文件，很难定位。
    """
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _register_routes():
    """把词库与抽取能力暴露给前端面板。

    ⚠️ 路由前缀是 /anima-wardrobe-v5/，刻意与造型台的 /anima-wardrobe/ 分开 ——
       两边都能装，前缀撞了会导致后注册的那份静默失效。
    """
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception as exc:  # pragma: no cover - 非 ComfyUI 环境
        print(f"[AnimaWardrobeV6] 无法注册 HTTP 路由: {exc}")
        return

    routes = PromptServer.instance.routes

    # v6.0.0：同一条处理函数同时挂到 v6 / v5 / v4 / v3 四条路径。
    # · v6 = 本版前缀，新前端用；
    # · v5 = 上一版前缀（v5.0.0–v5.11.0）。浏览器缓存里的 V5 JS、
    #        用户自己写的外部脚本都还在请求它，删了直接 404；
    # · v4 = 更早的旧版前缀。浏览器缓存里的 V4 JS 仍在请求它；
    # · v3 = 更早的前缀，v4.0.0 起就一直留着，继续留着不花任何成本。
    # ⚠️ 必须定义在函数**内部** —— `routes` 是这里的局部变量，放外面会 NameError，
    #    而外层 try/except 会把异常吞成一句「无法注册 HTTP 路由」，全部路由静默消失。
    _PREFIXES = ("/anima-wardrobe-v6", "/anima-wardrobe-v5",
                 "/anima-wardrobe-v4", "/anima-wardrobe-v3")

    def _dual_get(path):
        def deco(fn):
            for _p in _PREFIXES:
                routes.get(_p + path)(fn)
            return fn
        return deco

    def _dual_post(path):
        def deco(fn):
            for _p in _PREFIXES:
                routes.post(_p + path)(fn)
            return fn
        return deco

    async def _json_body(request):
        """解析请求体成 dict。坏 JSON / 非对象一律当空 dict，不抛。"""
        try:
            body = await request.json()
        except Exception:
            body = {}
        return body if isinstance(body, dict) else {}

    def _decode_data_url(s):
        """把前端 FileReader 给的 `data:image/png;base64,...` 解成 bytes。

        ⚠️ 只认 base64 的 data URL —— 前端就是这么传的（本地选图 → FileReader），
           不接受裸 URL（那会让后端变成 SSRF 跳板，为一个换图功能不值当）。
        """
        import base64 as _b64
        import re as _re
        s = str(s or "").strip()
        m = _re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.+)$", s, _re.S)
        if not m:
            raise ValueError("图片数据格式不正确（应为 base64 data URL）")
        try:
            return _b64.b64decode(m.group(1), validate=False)
        except Exception:
            raise ValueError("图片 base64 解码失败")

    def _chars_resp(extra=None):
        """角色写操作的统一返回：**最新完整角色表** + 可选附加字段。

        与预设/收藏同一套约定 —— 写操作的返回值才是权威，前端拿它直接重渲，
        不再多发一次 GET（少一轮往返，也不会读到中间态）。
        ⚠️ 必须**先清缓存再取**，否则拿到的是写之前的快照。
        """
        from . import anima_v4_rules as R
        R.clear_cache()
        out = {"ok": True, "data": characters_payload()}
        if extra:
            out.update(extra)
        return out

    @_dual_get("/pools")
    async def _pools(request):
        return web.json_response(pools_payload())

    @_dual_get("/contract")
    async def _contract(request):
        return web.json_response(contract_payload())

    @_dual_get("/characters")
    async def _characters(request):
        """角色库（作品 + 角色），给面板的角色浏览器用。

        组装逻辑在 nodes.characters_payload()，**不是**在这里写 ——
        因为前端冒烟的夹具（tools/dump_ui_fixtures.py）也要用它，
        夹具必须与真后端同源，否则测的是一个想象出来的后端。
        """
        return web.json_response(characters_payload())

    @_dual_get("/scenes")
    async def _scenes(request):
        """场景搭配预设库（v3.9.0）：分组 + 每个预设的固定字段组合。

        组装逻辑同样在 nodes.scenes_payload()，理由与 /characters 一致 ——
        前端冒烟的夹具必须与真后端同源。
        """
        return web.json_response(scenes_payload())

    @_dual_get("/thumb/{name}")
    async def _thumb(request):
        """角色缩略图。直接从 presets/thumbs/ 读，不走静态目录挂载
        （WEB_DIRECTORY 指向 js/，presets/ 不在静态服务范围内）。

        扩展名由服务端自己试：抓图工具在有 Pillow 时存 .webp，
        没有时退回 .jpg，面板不需要知道是哪种。
        找不到图就回 404 —— 面板会退化成首字母色块，不影响使用。

        v3.4.0：**先查用户目录再查内置目录**。用户给角色换过图时，
        presets/user_thumbs/ 里的那份必须优先命中，否则换了图看不到变化。
        """
        from aiohttp import web as _web
        import re as _re
        from . import anima_v4_store as S
        slug = request.match_info.get("name", "")
        # ⚠️ 路径穿越防护：只允许我们自己的 slug 形态（小写字母数字下划线）
        if not _re.fullmatch(r"[a-z0-9_]{1,120}", slug):
            return _web.Response(status=400)
        for folder in (S.USER_THUMB_DIR, _THUMB_DIR):
            for ext in ("webp", "jpg", "png"):
                path = os.path.join(folder, f"{slug}.{ext}")
                if os.path.isfile(path):
                    return _web.FileResponse(path, headers={
                        # ⚠️ v5.0.12：原来是 `public, max-age=604800`（7 天强缓存）——
                        #    文件名不变（`texas_arknights.webp`），换图后浏览器
                        #    直接吃缓存，**面板看起来完全没更新**（实测：服务端
                        #    Last-Modified 已是新时间，浏览器一次都没回源）。
                        #    改成 no-cache = 每次带 ETag 回源验证：内容没变时
                        #    aiohttp 的 FileResponse 自动回 304（几十字节），
                        #    换图后立刻生效，2402 张图的加载速度不受影响。
                        "Cache-Control": "no-cache",
                        # ⚠️ 必须显式给 Content-Type：aiohttp 是按扩展名查
                        #   系统 mimetypes 库猜的，而 Windows 的库里**没有 webp**
                        #   → 实测会退成 `application/octet-stream`。
                        #   <img> 一般不看这个头，但某些严格环境会把它当下载，
                        #   白给一个头更稳。
                        "Content-Type": f"image/{ext}",
                    })
        return _web.Response(status=404)

    @_dual_get("/roll-options")
    async def _roll_options(request):
        from . import anima_v4_rules as R
        return web.json_response({
            "levels": contract_payload()["levels"],
            "tag_cap_choices": ["自动", "16", "20", "24", "28", "32",
                                "36", "40", "48"],
            "base_prob_default": R.DEFAULT_BASE_PROB,
            "weights_default": R.empty_weights(),
            # v6.5.0：皮肤干爽过滤的分组（唯一真源在引擎 SKIN_MOISTURE_GROUPS）
            "dry_skin_groups": [
                {"id": k, "zh": zh, "tags": list(tags),
                 "items": [{"en": en, "zh": z}
                           for en, z in R.skin_moisture_items(k)]}
                for k, zh, tags in R.skin_moisture_group_options()
            ],
            "sections": [
                {"id": s["id"], "title": s["title"],
                 "max_level": R.section_max_level(s["id"])}
                for s in R.sections()
            ],
        })

    @_dual_post("/roll")
    async def _roll(request):
        """面板的「全部随机 / 本段随机」按钮调这里。

        与节点调用的是同一个 R.draw —— 面板看到的结果和直接跑工作流
        逐字节一致（同 seed 下）。所以面板可以用来预演，满意了再定种子。
        """
        from . import anima_v4_rules as R
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        section = str(body.get("section") or "all")
        seed = body.get("seed", None)
        try:
            seed = None if seed in (None, "") else int(seed)
        except (TypeError, ValueError):
            seed = None

        enabled = set(R.section_ids())
        disabled = str(body.get("disabled_sections") or "")
        for chunk in disabled.replace("\n", ",").split(","):
            c = chunk.strip()
            if c:
                enabled.discard(c)
        # 「本段随机」：只跑指定段，其余段落原样保留调用方给的当前值
        locks = set()
        for chunk in str(body.get("lock_fields") or "").replace("\n", ",").split(","):
            c = chunk.strip()
            if c:
                locks.add(c)
        if section != "all":
            if section not in set(R.section_ids()):
                return web.json_response(
                    {"error": f"未知段落 id：{section}"}, status=400)
            for sid in R.section_ids():
                if sid != section:
                    enabled.discard(sid)

        try:
            res = R.draw(
                level=body.get("nsfw_level") or "sfw",
                seed=seed,
                base_prob=float(body.get("base_prob") or R.DEFAULT_BASE_PROB),
                weights=R.parse_weights(body.get("weights_json") or ""),
                enabled_sections=enabled,
                locks=locks,
                tag_cap=body.get("tag_cap") or "自动",
                force_role=str(body.get("force_role") or "").strip(),
                extra_body=str(body.get("extra_body") or ""),
                appearance_follow=bool(body.get("appearance_follow", True)),
                selected_roles=R.parse_selected_roles(body.get("selected_roles") or ""),
                big_breasts=bool(body.get("big_breasts", False)),
                # v3.17.0：胸部大小等级（面板预演与节点实跑必须同参）
                breast_size=str(body.get("breast_size") or "不指定"),
                # v3.10.0：按作品 IP 限定随机角色（作品 slug / 英文名 / 中文名，| 分隔）
                role_works=str(body.get("role_works") or "").strip(),
                # v3.19.0：服装风格智配 —— 与节点控件同参，否则又是「预演 ≠ 实跑」
                outfit_style=str(body.get("outfit_style") or "随机"),
                # v3.38.0：只在收藏角色内随机 —— 同上，预演必须与实跑同参
                role_fav_only=bool(body.get("role_fav_only", False)),
                # v3.45.0：角色官方发型对齐 —— 同样必须与节点实跑同参
                hair_follow=bool(body.get("hair_follow", True)),
                # v4.1.0：发型选择器 4 项 —— **之前这里漏传**（JS 一直在发，
                # 但 handler 没接），于是"面板预演"与"节点实跑"在显式发型下不一致。
                # 顺手补齐：预演 = 实跑 同参是这套引擎的硬规矩。
                hair_picks={
                    "hair_len": str(body.get("hair_len_pick") or "").strip(),
                    "hair_tie": str(body.get("hair_tie_pick") or "").strip(),
                    "hair_curl": str(body.get("hair_curl_pick") or "").strip(),
                    "hair_acc": str(body.get("hair_acc_pick") or "").strip(),
                },
                # v4.1.0：不指定角色 —— 面板预演必须与节点实跑同参，
                # 否则用户勾上之后"预演没角色、实跑却有角色"（老坑重演）
                role_pick=bool(body.get("role_pick", True)),
                # v5.0.1：场景搭配随机抽取（面板复选框）—— 同上，预演必须与实跑同参；
                # 缺省 False = 不出场景（旧前端不带这个字段时也安全）
                scene_pick=bool(body.get("scene_pick", False)),
                # v6.5.0：面板预演必须与节点实跑同参（老规矩）
                dry_skin_filter=bool(body.get("dry_skin_filter", False)),
                dry_skin_groups=str(body.get("dry_skin_groups") or "").strip(),
                # v6.5.2：逐条模式必须与节点实跑同参
                dry_skin_tags=str(body.get("dry_skin_tags") or "").strip(),
                # v6.6.0：双女自动补位 —— 面板预演必须与节点实跑同参，
                # 否则会重演"预演两个不同的人、实跑两个一样的人"这类老坑。
                # 缺省 True = 与节点控件默认值一致。
                duo_role_fill=bool(body.get("duo_role_fill", True)),
                # v6.7.0：勾选池自动随机 —— 预演也要走同一条路，
                # 否则面板"预览"看到的永远是上一次手动套用的那条。
                pick_pool_mode=str(body.get("pick_pool_mode") or "").strip(),
                pick_pool_ids=str(body.get("pick_pool_ids") or "").strip(),
            )
        except Exception:
            import traceback as _tb
            _txt = _tb.format_exc()
            try:
                _p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "_roll_error.log")
                with open(_p, "a", encoding="utf-8") as _fh:
                    _fh.write(_txt + "\n" + "-" * 70 + "\n")
            except Exception:
                pass
            return web.json_response(
                {"error": (_txt.strip().splitlines() or ["?"])[-1][:400],
                 "traceback": _txt[-2000:]}, status=500)
        return web.json_response({
            "text": res["text"],
            "tags": _jsonable(res["tags"]),
            "state": _jsonable(res["state"]),
            "level": res["level"],
            "level_label": res["level_label"],
            "cn_summary": res["cn_summary"],
            "dropped": res["dropped"],
            "notes": res["notes"],
            "report": {
                "n": res["report"]["n"],
                "ok_f": res["report"]["ok_f"],
                "total_f": res["report"]["total_f"],
                "ok_s": res["report"]["ok_s"],
                "total_s": res["report"]["total_s"],
                "real_level": res["report"]["real_level"],
                "drawn": res["report"]["drawn"],
                # v3.18.6：正文去掉了作品名，完整 trigger 留在这里供溯源
                "role_full": res["report"].get("role_full") or [],
            },
        })

    # ── 用户数据：命名预设 与 角色收藏 ────────────────────────────
    # 落在插件目录 presets/ 下的两个 JSON 里（见 anima_v4_store）。
    # 三个约定：
    #   ① 写操作**全部返回最新完整列表**，前端拿返回值直接重渲，
    #      不需要紧跟着再发一次 GET（少一轮往返，也不会读到中间态）；
    #   ② 参数错误回 400 + {"error": "..."}，前端弹在状态条上；
    #   ③ 前缀仍是我们自己的 /anima-wardrobe-v5/（另挂 v4 / v3），与造型台互不干扰。

    @_dual_get("/presets")
    async def _presets_list(request):
        from . import anima_v4_store as S
        return web.json_response({"presets": S.list_presets()})

    @_dual_post("/presets/save")
    async def _presets_save(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        try:
            items = S.save_preset(body.get("name"), body.get("data"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"presets": items})

    @_dual_post("/presets/delete")
    async def _presets_delete(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        return web.json_response({"presets": S.delete_preset(body.get("name"))})

    @_dual_get("/favorites")
    async def _fav_list(request):
        from . import anima_v4_store as S
        return web.json_response({"favorites": S.list_favorites()})

    @_dual_post("/favorites/toggle")
    async def _fav_toggle(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        try:
            items = S.toggle_favorite(body.get("tag"), body.get("on"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"favorites": items})

    # ── 场景收藏（v3.43.0）────────────────────────────────────────
    # 与角色收藏**分开两条路由、分开一个文件**（见 anima_v4_store 的注释）：
    # 值域是场景 id（sc_xxx），不是角色 tag，混用会让「仅收藏内随机」脏掉。
    @_dual_get("/scenes/favorites")
    async def _scenefav_list(request):
        from . import anima_v4_store as S
        return web.json_response({"favorites": S.list_scene_favorites()})

    @_dual_post("/scenes/favorites/toggle")
    async def _scenefav_toggle(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        try:
            items = S.toggle_scene_favorite(body.get("id"), body.get("on"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"favorites": items})

    # ── 作品 IP 收藏（v5.0.4）──────────────────────────────────────
    # 值域是**作品 slug**（kantai_collection 这种），与角色 tag / 场景 id 分开存。
    # 前端拿到列表后只做两件事：作品行前面显示 ★、把收藏的排到列表最前。
    @_dual_get("/works/favorites")
    async def _workfav_list(request):
        from . import anima_v4_store as S
        return web.json_response({"favorites": S.list_work_favorites()})

    @_dual_post("/works/favorites/toggle")
    async def _workfav_toggle(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        try:
            items = S.toggle_work_favorite(body.get("work"), body.get("on"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"favorites": items})

    # ── 角色编辑 / 自定义角色（v3.4.0）──────────────────────────────
    # 数据落在 presets/user_characters.json（见 anima_v4_store），
    # 内置角色库永不被改写 —— 它是联网重建产物，改了会被重跑覆盖。

    @_dual_post("/characters/save")
    async def _chars_save(request):
        """新增（mode=create）或编辑（mode=edit）一个角色。"""
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        # 生成自定义 tag 时要避开**内置** tag（存储层看不到内置库，由这里给全）
        taken = [c.get("tag") for c in R.characters_data().get("characters", [])]
        try:
            rec, _ov = S.save_user_character(
                body.get("mode"), body.get("tag"),
                body.get("fields") or {}, taken_tags=taken)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(_chars_resp({"character": rec}))

    @_dual_post("/characters/delete")
    async def _chars_delete(request):
        """删除角色（v3.18.6 起 **内置角色也能删**）。

        · 自定义角色 → 删覆盖层记录（旧行为）
        · 内置角色   → 从 `presets/anima_v4_characters.json` 物理移除，
                       完整记录先写进 `presets/_retired/anima_v4_characters.removed.json`
        · body 里给 `tags: [...]` 就是批量删除
        """
        from . import anima_v4_store as S
        body = await _json_body(request)
        tags = body.get("tags")
        if isinstance(tags, list) and tags:
            ok, bad, _ov = S.delete_characters([str(t) for t in tags])
            if not ok:
                return web.json_response(
                    {"error": (bad[0]["error"] if bad else "删除失败"), "failed": bad},
                    status=400)
            return web.json_response(_chars_resp({"deleted": ok, "failed": bad}))
        try:
            kind, _ov = S.delete_character(body.get("tag"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(_chars_resp({
            "deleted": [str(body.get("tag") or "")], "kind": kind}))

    @_dual_post("/characters/reset")
    async def _chars_reset(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        try:
            S.reset_user_character(body.get("tag"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(_chars_resp())

    @_dual_post("/characters/thumb")
    async def _chars_thumb(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        try:
            raw = _decode_data_url(body.get("data_url"))
            slug = S.set_user_thumb(body.get("tag"), raw)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(_chars_resp({"thumb": slug}))

    @_dual_post("/characters/thumb-clear")
    async def _chars_thumb_clear(request):
        from . import anima_v4_store as S
        body = await _json_body(request)
        try:
            slug = S.clear_user_thumb(body.get("tag"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(_chars_resp({"thumb": slug}))

    @_dual_get("/characters/detail")
    async def _chars_detail(request):
        """单个角色的完整记录 + 内置原始特征标签（编辑弹窗用，v3.5.0）。

        ⚠️ 用查询串 `?tag=` 而**不是**路径参数：角色 tag 里有 1762 条带
        `(` `)` `:` `/` 等字符，路径参数那套 `[a-z0-9_]` 正则收不下
        （顺带说明：`/thumb/{slug}` 能用路径参数，是因为 `thumb` 是
        sanitized 过的 slug，不是 tag 本身）。
        """
        tag = request.query.get("tag", "")
        data = character_detail_payload(tag)
        if not data.get("ok"):
            return web.json_response({"error": data.get("error", "找不到该角色")},
                                     status=404)
        return web.json_response(data)

    # ── 角色服装预设（v5.0.2，用户 2026-11-02）────────────────────
    # 数据结构见 anima_v4_store 的「角色服装预设」段：
    #   {role_tag: {enabled: id, list: [{id, zh, level, tags, fields, drop, ...}]}}
    # ⚠️ 任何写操作后都要 `R.clear_cache()` —— 引擎侧 outfits 走 _CACHE，
    #    不清就是「改了不生效」。
    @_dual_get("/outfits")
    async def _outfits_list(request):
        from . import anima_v4_store as S
        return web.json_response({"outfits": S.list_outfits()})

    @_dual_post("/outfits/save")
    async def _outfits_save(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        try:
            rec, items = S.save_outfit(body.get("role"), body.get("outfit") or {},
                                       new=bool(body.get("new")))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        R.clear_cache()
        return web.json_response({"ok": True, "outfit": rec, "list": items,
                                  "outfits": S.list_outfits()})

    # ── v5.0.9：批量导入（用户 2026-11-03「有没有更方便的填写方法」）──
    # 一次请求把**多套**写进同一个角色，逐套走 `save_outfit(..., new=True)`
    # （id 撞车自动改名，不会覆盖已有条目）。
    # 与「一套一套点保存」完全等价，只是省掉 N 次往返。
    # ⚠️ 单套失败不中止整批 —— 返回 `errors` 明细，让用户看到哪几套没进去。
    @_dual_post("/outfits/import")
    async def _outfits_import(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        role = body.get("role")
        items = body.get("outfits")
        if not isinstance(items, list) or not items:
            return web.json_response({"error": "outfits 必须是非空数组"},
                                     status=400)
        added, errors = [], []
        for idx, o in enumerate(items):
            try:
                rec, _lst = S.save_outfit(role, o or {}, new=bool(body.get("new", True)))
                added.append(rec)
            except ValueError as exc:
                errors.append({"index": idx,
                               "id": (o or {}).get("id") if isinstance(o, dict) else "",
                               "error": str(exc)})
        R.clear_cache()
        return web.json_response({"ok": True, "added": added,
                                  "errors": errors,
                                  "outfits": S.list_outfits()})

    @_dual_post("/outfits/delete")
    async def _outfits_delete(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        try:
            items, enabled = S.delete_outfit(body.get("role"), body.get("id"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        R.clear_cache()
        return web.json_response({"ok": True, "list": items, "enabled": enabled,
                                  "outfits": S.list_outfits()})

    @_dual_post("/outfits/enable")
    async def _outfits_enable(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        try:
            enabled = S.set_outfit_enabled(body.get("role"), body.get("id"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        R.clear_cache()
        return web.json_response({"ok": True, "enabled": enabled,
                                  "outfits": S.list_outfits()})

    # ── v5.1.0：全局服装库（用户 2026-11-05）────────────────────────
    # 数据结构见 anima_v4_store 的「全局服装库」段：
    #   {_meta: {schema, active, categories, count}, outfits: [{id, zh, cat, level, tags, fields, drop}]}
    # 与角色服装预设的唯一区别是**不绑角色** —— 角色预设优先，服装库兜底。
    # ⚠️ 写操作后同样必须 `R.clear_cache()`：引擎侧走 _CACHE，不清就是"点了不生效"。
    @_dual_get("/outfit-library")
    async def _library_get(request):
        from . import anima_v4_store as S
        return web.json_response(S.library_payload())

    @_dual_post("/outfit-library/save")
    async def _library_save(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        try:
            rec, items = S.save_library_outfit(body.get("outfit") or {},
                                              new=bool(body.get("new")))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        R.clear_cache()
        return web.json_response({"ok": True, "outfit": rec, "count": len(items),
                                  "library": S.library_payload()})

    @_dual_post("/outfit-library/delete")
    async def _library_delete(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        try:
            items = S.delete_library_outfit(body.get("id"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        R.clear_cache()
        return web.json_response({"ok": True, "count": len(items),
                                  "library": S.library_payload()})

    @_dual_post("/outfit-library/enable")
    async def _library_enable(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        try:
            active = S.set_library_active(body.get("id"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        R.clear_cache()
        return web.json_response({"ok": True, "active": active,
                                  "library": S.library_payload()})

    # ── v5.5.0：服装库的袜子 / 鞋子覆盖（用户 2026-11-05 21:27）──────
    # 「可以指定选择丝袜与鞋子款式以及是否穿丝袜与鞋子，两个可以分别指定也可
    #   不启用（不启用就跟随服装库搭配的服装）。指定后权重覆盖装库搭配的服装。」
    # 值合法性在这里校验（存储层看不到词库）：必须是 socks / shoes 字段的真实选项。
    # 「不穿」不是特殊值 —— 就写词库里的 `barefoot` / `bare feet`。
    @_dual_post("/outfit-library/override")
    async def _library_override(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        field = str(body.get("field") or "").strip().lower()
        value = str(body.get("value") or "").strip().lower()
        if field not in ("socks", "shoes"):
            return web.json_response({"error": "只支持 socks / shoes"}, status=400)
        if value:
            _sec, fld = R.field_by_key(field)
            vals = {str(o[1]).strip().lower()
                    for o in ((fld or {}).get("opts") or [])}
            if value not in vals:
                return web.json_response(
                    {"error": "词库里没有这个选项：%s" % value}, status=400)
        try:
            ov = S.set_library_override(field, value)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        R.clear_cache()
        return web.json_response({"ok": True, "overrides": ov,
                                  "library": S.library_payload()})

    # ── v5.9.0：服装库是否覆盖「场景搭配里写死的推荐服装」──────────────
    # 关闭（默认）= 场景带推荐服装时服装库整套让路；开启 = 服装库强制覆盖
    # （回到 v5.8.0 及以前的行为）。只管服装库这一层，角色预设不受影响。
    @_dual_post("/outfit-library/scene-override")
    async def _library_scene_override(request):
        from . import anima_v4_store as S
        from . import anima_v4_rules as R
        body = await _json_body(request)
        raw = body.get("enabled")
        if isinstance(raw, bool):
            enabled = raw
        else:
            enabled = str(raw or "").strip().lower() in ("1", "true", "yes", "on")
        val = S.set_library_scene_override(enabled)
        R.clear_cache()
        return web.json_response({"ok": True, "scene_override": val,
                                  "library": S.library_payload()})

    @_dual_get("/health")
    async def _health(request):
        from . import anima_v4_rules as R
        return web.json_response({
            "ok": True,
            "version": __version__,
            "sections": len(R.sections()),
            "fields": len(R.field_keys()),
            "options": sum(len(f.get("opts", []))
                           for s in R.sections() for f in s["fields"]),
            "scenes": len(R.scene_list()),
            "outfit_library": len(R.library_data().get("items") or []),
            "outfit_library_active": (R.library_data().get("meta") or {}).get("active") or "",
            "library_scene_override": R.library_scene_override(),
            "levels": list(R.LEVEL_KEYS),
        })

    print(f"[AnimaWardrobeV6] v{__version__} 路由已注册: "
          "pools / contract / scenes / characters / thumb / roll-options / roll / "
          "presets / favorites / outfit-library / outfit-library-override / "
          "outfit-library-scene-override / "
          "characters(save|delete|reset|thumb|thumb-clear|detail) / health"
          "（前缀 v6 + v5 + v4 + v3 四挂）")


try:
    _register_routes()
except Exception as exc:  # pragma: no cover
    print(f"[AnimaWardrobeV6] 路由注册失败（不影响节点使用）: {exc}")
