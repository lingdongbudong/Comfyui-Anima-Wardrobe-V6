# -*- coding: utf-8 -*-
"""测试专用的插件加载器。

为什么不能直接 `import Comfyui-Anima-Wardrobe-V6.anima_v4_rules`：
  ① 目录名带 `-`，不是合法标识符；
  ② 直接跑 `__init__.py` 会一路 import 到 ComfyUI 的 server / aiohttp，
     裸 Python 环境里必然 ImportError —— 我们要测的是规则层，不是节点注册。

做法：手工造一个「只有 __path__ 的空包」塞进 sys.modules，
     再 import_module 子模块。相对导入 `from . import anima_v4_nsfw`
     靠 __path__ 就能解析，全程不碰 __init__.py。
"""
import importlib
import os
import sys
import types

PKG = "Comfyui-Anima-Wardrobe-V6"
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)


def _ensure_pkg():
    if PKG in sys.modules:
        return sys.modules[PKG]
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [PLUGIN_DIR]
    pkg.__package__ = PKG
    sys.modules[PKG] = pkg
    return pkg


def load(name: str):
    """加载插件里的某个模块，例如 load("anima_v4_rules")。"""
    _ensure_pkg()
    full = f"{PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    return importlib.import_module(full)


def isolate_outfit_library(R):
    """把服装库状态隔离到临时副本（active 清空），atexit 自动还原指针。

    ⚠️ v5.10.0：用户可能正在面板上启用某一套服装库 —— `effective_outfit()`
       会把 13 个服装字段整段闭麦，所有「随机服装 / 风格 / 权重」类基线会被
       整套覆盖（实测一次同时红 4 个用例，日志里全是用户那套 serafuku）。
       `check_v3_scenes` 早有「测前关、atexit 还原」的同类教训；这里更进一步
       走**临时副本**：真实文件一个字节都不动，与并行跑的出图脚本互不干扰。
    """
    import atexit
    import json
    import shutil
    import tempfile
    S = load("anima_v4_store")
    real = S.LIBRARY_FILE
    td = tempfile.mkdtemp(prefix="av5-libiso-")
    copy = os.path.join(td, "outfit_library.json")
    shutil.copy(real, copy)
    with open(copy, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("_meta", {})["active"] = ""
    with open(copy, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    S.LIBRARY_FILE = copy
    R._LIBRARY_PATH = copy
    R.clear_cache()

    def _restore():
        S.LIBRARY_FILE = real
        R._LIBRARY_PATH = real
        R.clear_cache()
        shutil.rmtree(td, ignore_errors=True)

    atexit.register(_restore)
    return copy
