# 维护者指南 · Comfyui-Anima-Wardrobe-V6

> 改代码之前请通读一遍。这份文档记录的是**踩出来的规矩** ——
> 每一条背后都至少有一次返工或一次线上事故。

## 目录

- [一、目录与数据流](#一目录与数据流)
- [二、对外 HTTP 路由](#二对外-http-路由)
- [三、改东西的正确姿势](#三改东西的正确姿势)
- [四、场景库总览网页（离线单文件）](#四场景库总览网页离线单文件)
- [五、测试基线](#五测试基线只增不减--全部-0-失败)
- [六、开发纪律](#六开发纪律)

---

## 一、目录与数据流

```
Comfyui-Anima-Wardrobe-V6/
├── __init__.py              节点注册 + HTTP 路由 /anima-wardrobe-v6/*（另挂 v5 / v4 / v3 三套）
├── nodes.py                 4 个节点（×4 个别名版本）+ 面板接口的 payload 构造
├── anima_v4_rules.py        随机引擎 / 档位闸门 / 三层权重 / 角色选中 / 自检 / 槽位序
├── anima_v4_nsfw.py         NSFW 判定层（**自动生成，勿手改**）
├── anima_v4_overrides.py    人工校准层（段下限 + 逐选项上抬 LEVEL_RAISE + 降级 LEVEL_DEMOTE）
├── anima_v4_link.py         字段联动层（体位配对 / 表情配套 / 场景倾向三张表）
├── anima_v4_outfit_style.py 服装智配 / 配色 / 风格联动
├── anima_v4_store.py        预设 / 收藏 / 服装库 / 场景收藏的原子落盘
├── anima_v4_pngmeta.py      把正文注入 PNG 元数据（**不改用户工作流**）
├── presets/
│   ├── anima_v4_pools.json        词库（零漂移导出物，**真源是 HTML**）
│   ├── anima_v4_levels.json       每条选项的档位（派生数据）
│   ├── anima_v4_characters.json   角色库 2432 位（联网构建，**只读**）
│   ├── anima_v4_scenes.json       场景搭配 524 条（6 组：sfw/near/nsfw/pose/art/weapon）
│   ├── anima_v4_scene_aspects.json 每条场景的画幅（**别写进 scenes.json**）
│   ├── outfit_library.json        服装库 135 套 / 11 类
│   ├── character_outfits.json     角色 → 服装预设映射（覆盖 2432 位）
│   ├── char_hair_style.json       角色官方发型对齐表
│   ├── char_output_tags.json      角色输出标签（按 Anima 官方写法核验）
│   ├── thumbs/                    角色缩略图 2432 张 / 29 MB（与角色 1:1，作者本地生成）
│   ├── user_characters.json       用户角色覆盖层（编辑 + 自定义）—— 入库存空模板
│   ├── user_thumbs/               自定义角色预览图（仓库里是 9 张示例）
│   ├── user_presets.json          命名预设（用户数据）—— 入库存空模板
│   ├── user_favorites.json        角色收藏（用户数据）—— 入库存空模板
│   ├── user_scene_favorites.json  场景收藏 —— 入库存空模板
│   └── user_work_favorites.json   作品 IP 收藏 —— 入库存空模板
├── locales/zh|en/nodeDefs.json    界面中文（显示名 / 控件名 / 下拉值）
├── js/anima_wardrobe_v4_studio.js 可视化面板（8 页签）
├── tools/                   导出 / 分类 / 同步 / 建库 / 抓图 / 迁移 / 建工作流
├── tests/                   30 个基线脚本 + fixtures/ + run_all.py
└── workflows/               5 份出图工作流（29B 系列 + 12B 角色面板）
```

> ⚠️ **缩略图**：2432 张全部由作者本地用 Anima 出图裁切生成，与角色库 1:1，
> 仅作面板里的角色识别对照。`tools/fetch_character_thumbs.py` 是给
> 「不想自己出图」的人准备的备用抓取路径，**仓库自带这批不是它抓的**。
> 缩略图缺席**不阻塞任何功能**，面板退化为首字母色块。
>
> ⚠️ `presets/thumbs.bak-*/`（抓图前的同批备份，16 MB）**不入库** —— 完全重复。


---

## 二、对外 HTTP 路由

| 路由 | 用途 |
|---|---|
| `GET /anima-wardrobe-v5/health` | 自检：段数 / 字段数 / 选项数 / 档位列表 |
| `GET /anima-wardrobe-v5/pools` | 完整词库（含中文名与逐项档位） |
| `GET /anima-wardrobe-v5/contract` | 段落 / 字段 / 槽位 / 档位契约 |
| `GET /anima-wardrobe-v5/scenes` | 场景搭配 + 特写搭配（v3.18.0 起每条带 `manual` / `part`，`near_parts` 给出部位顺序） |
| `GET /anima-wardrobe-v5/characters` | 角色库（作品 + 角色，约 928 KB；含 `trigger` 与 `user` 标记，`tags` 只带前 8 条） |
| `GET /anima-wardrobe-v5/characters/detail?tag=` | 单个角色**完整**记录 + 内置原始标签（编辑弹窗用） |
| `GET /anima-wardrobe-v5/thumb/<slug>` | 角色缩略图（**先查 `user_thumbs/` 再查 `thumbs/`**，自动试 webp/jpg/png） |
| `GET /anima-wardrobe-v5/roll-options` | 随机面板的初值 |
| `POST /anima-wardrobe-v5/roll` | 跑一次抽取（与节点共用同一套引擎） |
| `POST /anima-wardrobe-v5/characters/save` | 新增 / 编辑角色（`mode` = `create` / `edit`） |
| `POST /anima-wardrobe-v5/characters/delete` | 删除自定义角色 |
| `POST /anima-wardrobe-v5/characters/reset` | 内置角色恢复默认 |
| `POST /anima-wardrobe-v5/characters/delete` | 删除角色（v3.18.6：**内置也能删**；传 `tags:[...]` 即批量；内置记录先写 tombstone） |
| `POST /anima-wardrobe-v5/characters/thumb` | 上传预览图（base64 data URL） |
| `POST /anima-wardrobe-v5/characters/thumb-clear` | 清除自定义预览图 |


---

## 三、改东西的正确姿势

| 想改什么 | 改哪里 | 改完必须跑 |
|---|---|---|
| **词条** | `ANIMA提示词生成器.html`（**唯一真源**） | `node tools/export_v3_pools.mjs` → `python tools/classify_v3_levels.py` |
| **判定表** | 造型台 `wardrobe_rules.py`（**唯一真源**） | `python tools/sync_nsfw_tables.py` |
| **段下限 / 上抬·降级白名单** | `anima_v4_overrides.py` | `python tools/classify_v3_levels.py` |
| **角色库** | `tools/build_characters.py` 里的排除表 / 目标作品 / 阈值 | `python tools/build_characters.py` → `python tests/check_v3_chars.py` |
| **角色编辑 / 自定义角色** | 面板里点 ✎ / ＋ 添加角色（存 `presets/user_characters.json`） | `python tests/check_v3_userdata.py` |
| **场景搭配预设** | `presets/anima_v4_scenes.json`（**人写内容**） | `python tools/build_scenes.py`（校验 + 派生档位）→ `python tests/check_v3_scenes.py` |
| **特写搭配（v3.18.0）** | 同上文件，`group=near` + `manual_only=true` | `python tools/build_scenes.py` → `python tests/check_v3_near.py`（**随机隔离 + 零影响对拍**） |
| 随机逻辑 / 权重 / 角色选中 | `anima_v4_rules.py` | `python tests/run_all.py` |
| **出图元数据（提示词写没写进 PNG）** | `anima_v4_pngmeta.py` | `python tests/check_v3_pngmeta.py`（真机：提交一张最小图，解剖产物） |
| 面板 | `js/anima_wardrobe_v4_studio.js` | `node tests/av3_ui_smoke.mjs` |
| **节点控件个数** | `nodes.py`（**只能追加到末尾**） | 对应版本的迁移脚本：v3.1.0 → `fix_workflows_v301.py`、v3.2.0 → `fix_workflows_v302.py`、**v3.10.0 → `fix_workflows_v303.py`**（都先 `--dry`） |

⚠️ **只改 JSON 不改 HTML，下次导出会被原样冲回去。**

---

---

## 四、场景库总览网页（离线单文件）

`tools/build_scene_catalog.py` 把 `presets/anima_v4_scenes.json` 汇总成一份
**单文件离线 HTML**（数据内嵌，不依赖网络）：

```bash
python tools/build_scene_catalog.py                       # 默认写到桌面「场景库总览.html」
python tools/build_scene_catalog.py --out D:/xxx.html    # 换位置
# 渲染验证脚本需 jsdom，属开发期脚本，未随仓库分发
```

页面内容（用户 2026-11-05 23:23 拍板）：

| 项 | 说明 |
|---|---|
| 收录 | **全部 444 条**一条不漏 |
| 卡面 | 中文名 / id / 页签 / 档位 / 题材 / 一句说明 / 人数锁 · 地点 · 景别 |
| 展开 | 完整 `tags` 表、`extra` 标签、`scene_nl` 正文、变体池与随机字段、闭麦字段、禁用词、元信息 |
| 成人档 | **L2 及以上的「正文与变体」默认再折叠一层**，点开才显示；顶部有「展开全部成人正文」开关 |
| 交互 | 全文搜索（中文名/id/题材/tags/正文，空格 = AND）、页签/档位/题材/随机池/变体筛选、6 种排序、统计条与档位分布、重置 |
| 统计 | 总数 / 页签数 / 随机池 / 仅手动 / 带随机性 / 带变体池 / 题材数 + L0–L4 分布条 |

⚠️ 场景库**没有配图**（只有角色库有缩略图），所以这是文字型目录。
⚠️ 数据变更后**重跑生成脚本**即可刷新，网页本身不会自动更新。


---

## 五、测试基线（只增不减 · 全部 0 失败）

```bash
python tests/run_all.py          # 一键全跑，默认每档 200 次抽样
python tests/run_all.py 500      # 更狠
```

| 测试 | 钉住什么 |
|---|---|
| `check_v3_pools.py` | 词库零漂移（与源 HTML 对拍）+ 档位表一致 + 段下限生效 |
| `check_nsfw_parity.py` | 33 项常量/函数与造型台**逐字一致** + 27 组标签判档行为对拍 |
| `check_v3_gate.py` | 五档 × N 次：准入越界 0 · NSFW 段泄漏 0 · 未登记超档标签 0 · 自检全绿 |
| `check_v3_link.py` | **字段联动（v3.15.0）**：三张表 ↔ 词库一致性 · place 主题覆盖完整 · **运行时零矛盾**（体位配对 / 表情配套）· 场景预抽可用（含 lock/forced）· **场景加权与硬剔除实测生效**（泳池边泳装占比 vs 教室对照）· **sanitize 兜底路径**（全场景随机 80 轮无违和）· **场景搭配库字段引用完整性** |
| `check_v3_pose.py` | **姿势段 + 暗示姿势档位（v3.14.0）**：字段与档位分布 · **段级闸门按"段内最低档"判**（按最高档判会让 SFW 档姿势整段消失）· 逐选项闸门 · **实抽 SFW 零泄漏 / L1·L2 能抽到** · 姿势 tag 无废弃写法 · 场景库引用全合法 |
| `check_v3_weights.py` | 9 组权重语义（0 必无 / 3.0 必现 / 非法输入不炸 / 权重 0 让路…）+ **锁定字段不被法典预设写回**（v3.8.0） |
| `check_v3_near.py` | **特写搭配（v3.18.0）**：32 条结构与部位校验 · **随机隔离（5 档 × N seed 零命中）** · 显式套用才生效 · **摘掉特写后输出 SHA256 全等** · 载荷 manual/part/near_parts · 合法 `sc_*` 不再被误报 · 关 ③ 段后 F10 仍通过 |
| `check_v3_scenes.py` | 场景搭配库（v3.9.0 起）：结构与取值校验 · 派生档位一致 · canon 合并 · 按档可用性 · **预设字段裁不掉（真固定）** · id 不进正文 · 面板载荷与引擎同口径 · **挖矿主题（原「自慰」，v3.11.0）：覆盖 L2/L3/L4 · solo 约定 · 档位闸门** |
| `check_v3_repair.py` | 成品检查与自动修复（v3.8.0）：超档剔除 · 校准项保住 · 判定表盲区折算 · 六组互斥 · 格式归一 · **幂等** · **正常生成零改动** · 校准表不变量（两表不相交 + 选项原文真实存在 + 落盘档位==运行时） |
| `check_v3_roles.py` | 12 组角色语义（含 v3.10.0 的**随机池**：作品名解析 / 只含该作品 / 每次换人 / 可复现 / 优先级 / 无效回退）（分隔符 / 100% 抽取 / 按人数组合抽 N 位 / 群像 / 透传 / 巨乳开关） |
| `check_v3_chars.py` | 角色库结构 + 合规（无宝可梦/无男性）+ 覆盖 + `/characters` 的 work_id 映射 + `--net` 抽查标签真实存在 |
| `check_v3_charhair.py` | **角色官方发型对齐（v3.45.0）**：发型表 key 是真角色 · 值是真词库选项 · 开启后写入官方值 · 关闭一字不写 · 权重 0 让路 · **表里没有的角色开/关逐字段相同（零漂移）** |
| `check_v3_store.py` | 预设/收藏存储：原子写 · 幂等 · 参数校验 · 坏文件容错 · 上限保护 |
| `check_v3_userdata.py` | 用户角色覆盖层：原子写 · 参数校验 · 合并正确性（edits/custom/works.count/**tags 整条替换**）· **缓存纪律** · 删除与恢复默认 · 图片落盘与安全 · **特征标签（列表语义 + 校验 + 引擎采用）** · **内置库逐字节零改动** |
| `check_v3_pngmeta.py` | PNG 元数据注入：**先钉死"病"**（承载位全是空串）· 注入后承载位/终点位/兜底位都写对 · **不越界**（质量词/分隔符/负向/插件自身控件零改动、除文本控件外整份工作流逐字节一致）· 幂等 · **API 只加惰性键且已声明输入零改动** · 异常安全 · 节点接线 |
| `av3_ui_smoke.mjs` | jsdom 真跑面板：**252 条**断言（权重树 / 角色浏览器 / **🎲 按 IP 随机抽角色** / **🔁 随机池** / **🎬 场景搭配页（档位分组 + **默认折叠** + **全部展开** + **👁 非破坏性预览**）** / 收藏 / 预设 / 免责声明 / 巨乳开关 / **角色编辑与自定义角色** / **特征标签增删改** / **段「本档不参与」判据** / **预演请求带 selected_roles+lock_fields**） |

> `av3_ui_smoke.mjs` 的 fetch 桩喂的是 `tools/dump_ui_fixtures.py` 从**真后端**
> 导出的夹具 —— 接口改了字段名，冒烟立刻红。它抓到过 4 个真 bug：
> 面板把 `/contract` 的 `option_count` 用在 `/pools` 的字段上（权重细则按钮全灰）、
> 改了权重没点「采用」就不生效、**点档位按钮直接抛异常导致档位切不动**（v3.0.0 就存在）、
> 缩略图加载失败没退化处理。

---

## 六、开发纪律

改这个插件之前，下面六条请当成硬性前提。

### 6.1 零漂移

词库的唯一真源是 `ANIMA提示词生成器.html`，`presets/anima_v4_pools.json` 只是导出物。
**关闭态必须一次 rng 都不吃** —— 新增任何功能，关闭时输出要与改动前逐字节相同。
这条是「能安心加功能」的地基，破了之后就没法判断回归。

### 6.2 备份名精确到毫秒

引擎改写 `presets/*.json` 前会落 `*_bak_<YYYYMMDD-HHMMSS-mmm>/` 快照。
同名备份会互相覆盖 —— 一天里连改三次，前两份就没了。

### 6.3 新增节点控件只能放 `optional` 末尾

⚠️ 放 `required` 会让老 API 工作流直接报 `Required input is missing`，
而 ComfyUI 的处理方式是**丢弃整张输出**（不是跳过该节点）。
现有六个 optional 控件（`dry_skin_filter` / `dry_skin_groups` / `dry_skin_tags` /
`duo_role_fill` / `pick_pool_mode` / `pick_pool_ids`）一律进前端 `HIDDEN` 列表，
并且 `/roll`、`buildConfig` / `applyConfig`、`contract_payload`、`IS_CHANGED` 必须同参 ——
漏一处就会出现"面板改了但引擎不知道"。`tests/check_v3_dryskin.py` 里有断言钉住这条。

### 6.4 批量脚本必须拒绝覆盖已存在 id

往场景库 / 服装库批量插入条目时，**默认拒绝覆盖同 id**。
历史上踩过：批量脚本静默覆盖了两条已写好的场景，目录看着条数对，内容已经没了。

### 6.5 测试基线

`tests/run_all.py` **28 项**，其中 2 项是历史红（场景搭配预设 / 角色发型对齐），
`check_v3_scenes.py` 另有 4 项历史红。**与基线一致就算通过**，不要为了让全绿去改断言。
UI 冒烟 `tests/av3_ui_smoke.mjs` 约 **609 条**断言。

### 6.6 只用 Write/Edit 改文件

批量替换前先判换行（当前所有待改文件是 LF）。
⚠️ 不要让脚本用 `open(p,'w',newline='
')` 之类的方式重写含中文的大文件 ——
编码一错就是整份文件重编码，diff 里全是噪声。
前端改动特别注意：**CSS 在 `const CSS = \`...\`` 模板字符串里，注释写反引号会 `SyntaxError`**。
