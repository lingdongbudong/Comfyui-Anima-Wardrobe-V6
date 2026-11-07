/**
 * Anima V3 面板 · 前端冒烟
 * ---------------------------------------------------------------------------
 * 不是"语法检查"那种假冒烟。这里真的把面板跑起来：
 *   ① 用 jsdom 造一个真 DOM；
 *   ② 把插件 JS 复制到临时目录，让 `../../scripts/app.js` 能解析到桩；
 *   ③ 桩里的 fetchApi 喂的是 tools/dump_ui_fixtures.py 从**真后端**导出的
 *      夹具 —— 接口改了字段名，这里立刻红；
 *   ④ 真去点按钮、填权重、读控件，断言结果。
 *
 * ⚠️ jsdom 陷阱（本项目踩过，写用例前必读）：
 *   · `h()` 传含换行的模板字符串会让 cssText 整段丢弃 →
 *     断言**不能靠 style.x / cssText 定位**，要用 DOM 结构或 getAttribute。
 *   · 同名文本跨区重复 → 查找必须限定在目标区（这里全用 querySelector
 *     限定在具体容器下，不全局搜文本）。
 *   · 用例之间共享同一个 node 状态 → 新用例前先复位。
 *
 * 跑法：node tests/av3_ui_smoke.mjs
 *      （需先跑 python tools/dump_ui_fixtures.py 生成夹具）
 */
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.dirname(HERE);
const SRC_JS = path.join(PLUGIN, "js", "anima_wardrobe_v4_studio.js");
const FIX = path.join(HERE, "fixtures");

let PASS = 0;
const FAILS = [];
function ck(name, cond, note = "") {
  if (cond) {
    PASS++;
    console.log(`  [OK] ${name}`);
  } else {
    FAILS.push(name);
    console.log(`  [!!] ${name}${note ? "  " + note : ""}`);
  }
}

/* ── 0. 前置检查 ─────────────────────────────────────────────────── */
for (const f of ["pools.json", "contract.json", "characters.json", "scenes.json",
                 "outfit_library.json"]) {
  if (!fs.existsSync(path.join(FIX, f))) {
    console.error(`缺少夹具 ${f}，请先跑：python tools/dump_ui_fixtures.py`);
    process.exit(2);
  }
}
const POOLS = JSON.parse(fs.readFileSync(path.join(FIX, "pools.json"), "utf8"));
const CONTRACT = JSON.parse(fs.readFileSync(path.join(FIX, "contract.json"), "utf8"));

/* 角色库夹具：**真实全量数据**（2687 条 / 288 作品）。
   ⚠️⚠️ 这里以前是手写的 6 条合成数据，于是漏掉了一个线上 bug：
      我把 character.work 写成了 slug，而真实数据里它是**作品英文名** ——
      「点 IP 作品 → 角色列表空掉」就在 66 条断言全绿的情况下溜了过去。
      夹具必须与真后端同源，这条没有例外。

   ⚠️ 也**不能抽样**：works[].count 必须与 characters 里属于该作品的实际
      条数一致，否则「点作品 → 卡片数 == count」这条断言测的就是假数据。*/
const CHAR_FIX = JSON.parse(
  fs.readFileSync(path.join(FIX, "characters.json"), "utf8"));
/* 场景搭配库夹具（v3.9.0）—— 同样是真后端导出的 */
const SCENE_FIX = JSON.parse(
  fs.readFileSync(path.join(FIX, "scenes.json"), "utf8"));
/* 全局服装库夹具（v5.1.0 起；v5.2.0 扩到 110 套 / 10 类）*/
const LIB_FIX = JSON.parse(
  fs.readFileSync(path.join(FIX, "outfit_library.json"), "utf8"));
/* 夹具形状自检：夹具本身不自洽的话，后面所有断言都是空转。 */
{
  const ids = new Set(CHAR_FIX.works.map((w) => w.id));
  const bad = CHAR_FIX.characters.filter((c) => !ids.has(c.work_id));
  if (bad.length) {
    console.error(`夹具自检失败：${bad.length} 条角色的 work_id 不在 works.id 里，`
      + `例 ${bad.slice(0, 3).map((c) => `${c.tag}→${c.work_id || "(空)"}`).join(", ")}`);
    process.exit(3);
  }
  const cnt = new Map();
  for (const c of CHAR_FIX.characters) cnt.set(c.work_id, (cnt.get(c.work_id) || 0) + 1);
  const mismatch = CHAR_FIX.works.filter((w) => (cnt.get(w.id) || 0) !== w.count);
  if (mismatch.length) {
    console.error(`夹具自检失败：${mismatch.length} 个作品的 count 与实际条数不符，`
      + `例 ${mismatch.slice(0, 3).map((w) => {
        return `${w.id} count=${w.count} 实际=${cnt.get(w.id) || 0}`;
      }).join(", ")}`);
    process.exit(3);
  }
  console.log(`[夹具] 角色 ${CHAR_FIX.characters.length} · 作品 ${CHAR_FIX.works.length}`
    + ` · work_id 与 works.count 自检通过`);
}

/* ⚠️ 夹具里的 `tags` 只有前 8 条 —— 因为 `characters_payload()` 就是这么发的
   （`tags[:8]`，卡片展示够用）。而"编辑特征标签"必须拿到**全量**，
   所以这里额外读一次内置角色库**原文**，当作全量的独立基准。
   不这么做的话，"标签行数 = 全量"只能拿 8 条自证，等于没测。 */
const RAW_CHARS = JSON.parse(fs.readFileSync(
  path.join(PLUGIN, "presets", "anima_v4_characters.json"), "utf8"));
const RAW_BY_TAG = new Map(RAW_CHARS.characters.map((c) => [c.tag, c]));
{
  const more = [...RAW_BY_TAG.values()].filter((c) => (c.tags || []).length > 8);
  if (!more.length) {
    console.error("夹具自检失败：内置角色库里没有一条标签数 > 8 的角色，"
      + "特征标签用例无法验证“全量 vs 载荷”的差异");
    process.exit(3);
  }
}

/* jsdom 从哪来：优先 JSDOM_PATH 环境变量，其次仓库内 node_modules、上一级 node_modules。
   ⚠️ 别写死绝对路径 —— 别人 clone 下来必然 ENOENT。 */
const JSDOM_ENTRY = (() => {
  const cands = [
    process.env.JSDOM_PATH,
    path.join(process.cwd(), "node_modules/jsdom/lib/api.js"),
    path.join(process.cwd(), "..", "node_modules/jsdom/lib/api.js"),
  ].filter(Boolean);
  for (const c of cands) if (fs.existsSync(c)) return c;
  console.error("找不到 jsdom：先 `npm i jsdom`，或用 JSDOM_PATH 指向 jsdom/lib/api.js");
  process.exit(4);
})();
const { JSDOM } = await import(pathToFileURL(JSDOM_ENTRY).href);

/* ── 1. jsdom 环境 ───────────────────────────────────────────────── */
// ⚠️ 必须挂 virtualConsole：jsdom 里事件处理函数抛的异常会被吞进
//    virtual console，不接出来的话用例只会看到"元素没出现"，
//    完全不知道是代码报错了。
const vc = new (await import(pathToFileURL(JSDOM_ENTRY).href)).VirtualConsole();
vc.on("jsdomError", (e) => console.error("[jsdomError]", e.detail || e));
vc.on("error", (...a) => console.error("[jsdom console.error]", ...a));
const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", {
  pretendToBeVisual: true,
  virtualConsole: vc,
});
global.window = dom.window;
global.document = dom.window.document;
// Node 22 自带只读的 global.navigator（只有 getter），直接赋值会炸，
// 必须用 defineProperty 覆盖。
Object.defineProperty(global, "navigator", {
  value: dom.window.navigator,
  configurable: true,
  writable: true,
});
global.HTMLElement = dom.window.HTMLElement;
global.alert = () => {};
// ⚠️ 插件脚本是在 Node 上下文里跑的（只是 import 了浏览器桩），
//    所以浏览器全局要一个个显式挂上去。FileReader 是 v3.4.0 上传预览图
//    用到的 —— 不挂就是 "FileReader is not defined"，而且它抛在事件回调里，
//    不挂 virtualConsole 的话只会看到"元素没出现"。
global.FileReader = dom.window.FileReader;
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);

/* ── 2. 搭临时目录 + 桩 ─────────────────────────────────────────── */
// 目录结构必须还原浏览器里的 URL 布局：
//   真实运行时插件脚本在  /extensions/<插件名>/xxx.js
//     （ComfyUI 的 WEB_DIRECTORY="./js" 是把 js/ 目录**挂在** extensions 根上的，
//       不会多出一层 js/ —— 这一点踩过一次，写成 js/ 子目录会 404）
//   它 import 的是        ../../scripts/app.js  →  /scripts/app.js
// 所以这里建成 <TMP>/extensions/<插件>/... + <TMP>/scripts/...，还原真实布局。
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "av3ui-"));
const PLUG_DIR = path.join(TMP, "extensions", "Comfyui-Anima-Wardrobe-V5");
fs.mkdirSync(PLUG_DIR, { recursive: true });
fs.mkdirSync(path.join(TMP, "scripts"), { recursive: true });
fs.writeFileSync(path.join(TMP, "package.json"), '{"type":"module"}', "utf8");
fs.copyFileSync(SRC_JS, path.join(PLUG_DIR, "anima_wardrobe_v4_studio.js"));

fs.writeFileSync(path.join(TMP, "scripts", "app.js"), `
export const app = {
  canvas: { },
  graph: { setDirtyCanvas() {} },
  registerExtension(ext) { globalThis.__AV3_EXT = ext; },
};
`, "utf8");

fs.writeFileSync(path.join(TMP, "scripts", "api.js"), `
export const api = {
  fetchApi(url, opts) { return globalThis.__AV3_FETCH(url, opts); },
};
`, "utf8");

/* 记录所有请求，供断言用 */
const CALLS = [];
/* 用户数据的内存桩（命名预设 / 角色收藏）。
   刻意维护**内存状态**、写操作返回"最新完整列表" —— 与真后端契约一致
   （见 __init__.py 里那三条约定）。只回一个固定值的话，
   "保存后列表变了没""收藏后计数涨了没"这类断言就全是空转。 */
const STORE = { presets: {}, favorites: [] };
const countW = (w) => (!w ? 0
  : Object.keys(w.sections || {}).length + Object.keys(w.fields || {}).length
    + Object.values(w.options || {})
        .reduce((n, x) => n + Object.keys(x || {}).length, 0));
const presetList = () => Object.entries(STORE.presets)
  .map(([name, p]) => ({
    name,
    saved_at: p.saved_at || "",
    summary: `${p.level || "—"} · 权重 ${countW(p.weights)} 项`,
    data: Object.fromEntries(
      Object.entries(p).filter(([k]) => k !== "saved_at")),
  }))
  .sort((a, b) => (a.saved_at < b.saved_at ? 1 : -1));

/* 角色覆盖层的内存桩（v3.4.0 角色编辑 + 自定义角色）。
   与真后端同构：基于**夹具**重算，edits 覆盖内置、custom 追加、
   works.count 重算 —— 断言"保存后卡片真的变了"才有意义。 */
const USERCH = { edits: {}, custom: [], next: 1 };
// v3.43.0：场景收藏（桩里用一个数组模拟后端的 user_scene_favorites.json）
const SCENE_FAV = [];
// v5.0.4：作品 IP 收藏（值域是作品 slug，模拟 user_work_favorites.json）
const WORK_FAV = [];
// v5.0.5：角色服装预设（模拟 character_outfits.json）。按角色懒建 ——
// 断言里拿的是网格第一张卡片的角色，事先不知道是谁。
const OUTFIT_STUB = {};
// v5.1.0：服装库的"当前启用"（空串 = 关闭），只在桩里存活
let LIB_ACTIVE = "";
// v5.5.0：袜子/鞋子覆盖项（模拟 outfit_library.json 的 _meta.overrides）
let LIB_OV = { socks: "", shoes: "" };
// v5.9.0：是否覆盖场景推荐服装（模拟 _meta.scene_override，默认关闭 = 场景优先）
let LIB_SCENE_OV = false;
const libPayload = () => Object.assign({}, LIB_FIX,
  { active: LIB_ACTIVE, overrides: Object.assign({}, LIB_OV),
    scene_override: LIB_SCENE_OV });
function outfitsStub(role) {
  const k = String(role || "").toLowerCase();
  if (!OUTFIT_STUB[k]) {
    OUTFIT_STUB[k] = {
      enabled: "casual",
      list: [
        { id: "casual", zh: "常服", level: 0, tags: ["white shirt", "a-line skirt"],
          fields: {}, drop: [], verified: true },
        { id: "uniform", zh: "校服", level: 0, tags: ["school uniform", "serafuku"],
          fields: {}, drop: [], verified: false },
      ],
    };
  }
  return OUTFIT_STUB[k];
}
// v3.18.6：内置角色也能删 —— 桩里用 Set 记"已删"，charPayload 过滤掉它们
const REMOVED_CHARS = new Set();
const workIdOf = (en) => (CHAR_FIX.works.find((w) => w.en === en) || {}).id || "";
function charPayload() {
  const chars = CHAR_FIX.characters.filter((c) => !REMOVED_CHARS.has(c.tag))
    .map((c) => ({ ...c, user: false }));
  for (const c of chars) {
    const p = USERCH.edits[c.tag];
    if (p) Object.assign(c, p);
  }
  for (const c of USERCH.custom) {
    chars.push({ ...c, user: true, work_id: workIdOf(c.work) });
  }
  const works = CHAR_FIX.works.map((w) => ({ ...w }));
  const cnt = {};
  for (const c of chars) if (c.work_id) cnt[c.work_id] = (cnt[c.work_id] || 0) + 1;
  for (const w of works) w.count = cnt[w.id] || 0;
  return { available: true, meta: {}, works, characters: chars };
}

globalThis.__AV3_FETCH = async (url, opts) => {
  CALLS.push({ url, opts });
  const body = (u) => ({ ok: true, status: 200, json: async () => u });
  const arg = () => JSON.parse((opts && opts.body) || "{}");
  if (url.endsWith("/pools")) return body(POOLS);
  if (url.endsWith("/contract")) return body(CONTRACT);
  if (url.endsWith("/scenes")) return body(SCENE_FIX);
  // ── v3.43.0：场景收藏（**独立文件**，值域是场景 id，不是角色 tag）──
  // ⚠️ 必须排在 "/scenes" 之前判断吗？不必（endsWith 不会混淆），
  //    但顺序写清楚，避免以后有人把它挪到通用分支后面。
  if (url.endsWith("/scenes/favorites")) return body({ favorites: [...SCENE_FAV] });
  if (url.endsWith("/scenes/favorites/toggle")) {
    const b = arg();
    const sid = String(b.id || "");
    const has = SCENE_FAV.includes(sid);
    const want = (b.on === undefined || b.on === null) ? !has : !!b.on;
    if (!/^sc_[a-z0-9_]+$/.test(sid)) {
      return { ok: false, status: 400, json: async () => ({ error: "场景 id 不合法" }) };
    }
    if (want && !has) SCENE_FAV.push(sid);
    else if (!want && has) SCENE_FAV.splice(SCENE_FAV.indexOf(sid), 1);
    return body({ favorites: [...SCENE_FAV] });
  }
  // ── v5.0.4：作品 IP 收藏（值域是作品 slug）──────────────────────
  // ⚠️⚠️ 必须排在 "/favorites" 与 "/favorites/toggle" 之前 ——
  //     那两个分支用的是 `endsWith`，而 "/works/favorites" 的结尾
  //     恰好也是 "/favorites"，先判通用分支会把这里整个吞掉
  //     （表现：点星没有任何请求，或把作品 slug 当角色 tag 收藏）。
  if (url.endsWith("/works/favorites/toggle")) {
    const b = arg();
    const slug = String(b.work || "");
    if (!/^[a-z0-9_:]+$/.test(slug)) {
      return { ok: false, status: 400, json: async () => ({ error: "作品 slug 不合法" }) };
    }
    const has = WORK_FAV.includes(slug);
    const want = (b.on === undefined || b.on === null) ? !has : !!b.on;
    if (want && !has) WORK_FAV.push(slug);
    else if (!want && has) WORK_FAV.splice(WORK_FAV.indexOf(slug), 1);
    return body({ favorites: [...WORK_FAV] });
  }
  if (url.endsWith("/works/favorites")) return body({ favorites: [...WORK_FAV] });
  // ── v5.1.0：全局服装库（模拟 outfit_library.json 的 _meta.active）──
  // ⚠️ "/outfit-library/enable" 必须排在 "/outfit-library" 之前（endsWith 前缀）。
  // ⚠️ override 必须排在 /outfit-library 之前（endsWith 前缀关系）
  // ⚠️ v5.9.0：scene-override 必须排在 override 之前（更具体的路径优先）
  if (url.endsWith("/outfit-library/scene-override")) {
    const b = arg();
    LIB_SCENE_OV = !!b.enabled;
    return body({ ok: true, scene_override: LIB_SCENE_OV, library: libPayload() });
  }
  if (url.endsWith("/outfit-library/override")) {
    const b = arg();
    LIB_OV = Object.assign({}, LIB_OV, { [String(b.field || "")]: String(b.value || "") });
    return body({ ok: true, overrides: LIB_OV, library: libPayload() });
  }
  if (url.endsWith("/outfit-library/enable")) {
    const b = arg();
    const oid = String(b.id || "").toLowerCase();
    if (oid && !LIB_FIX.outfits.some((o) => o.id === oid)) {
      return { ok: false, status: 400, json: async () => ({ error: "找不到这套服装" }) };
    }
    LIB_ACTIVE = oid;
    return body({ ok: true, active: oid, library: libPayload() });
  }
  if (url.endsWith("/outfit-library")) return body(libPayload());
  // ── v5.0.5：角色服装预设（桩里默认启用「常服」，用来测取消启用）──
  // ⚠️ "/outfits/enable" 必须排在 "/outfits" 之前（同样是 endsWith 前缀问题）。
  if (url.endsWith("/outfits/enable")) {
    const b = arg();
    const rec = OUTFIT_STUB[String(b.role || "").toLowerCase()];
    if (!rec) {
      return { ok: false, status: 400, json: async () => ({ error: "这个角色没有服装预设" }) };
    }
    const oid = String(b.id || "").toLowerCase();   // 空串 = 停用
    rec.enabled = oid;
    return body({ ok: true, enabled: oid, outfits: OUTFIT_STUB });
  }
  if (url.endsWith("/outfits")) return body({ outfits: OUTFIT_STUB });
  if (url.endsWith("/characters")) {
    return body(charPayload());
  }
  // ── 角色编辑 / 自定义角色（v3.4.0）──────────────────────────────
  // ⚠️ 顺序：thumb-clear / save / delete / reset 都要排在 "/characters" 与
  //    泛化的 "/thumb/" 之前判（endsWith("/characters") 本身不歧义，但
  //    "/thumb/" 是 includes，得留神）。这里其实都不冲突，仍按具体在前排。
  if (url.endsWith("/characters/save")) {
    const b = arg();
    const f = b.fields || {};
    if (!String(f.trigger || "").trim()) {
      return { ok: false, status: 400, json: async () => ({ error: "角色标签不能为空" }) };
    }
    // 特征标签校验（与真后端同口径：小写英文，单条 ≤40 字符）
    const tagList = Array.isArray(f.tags) ? f.tags : [];
    const badTag = tagList.find((t) => String(t).length > 40
      || !/^[a-z0-9][a-z0-9 _\-:'()./!+]*$/.test(String(t).toLowerCase()));
    if (badTag) {
      return { ok: false, status: 400, json: async () => ({ error: "标签不合法：" + badTag }) };
    }
    if (b.mode === "create") {
      const tag = "user_stub" + (USERCH.next++);
      USERCH.custom.push({
        tag, name: String(f.trigger).split(",")[0].trim(), zh: f.zh || "",
        work: f.work || "", trigger: f.trigger, pop: 0, hair: "", eyes: "",
        tags: tagList.slice(), thumb: tag,
      });
      return body({ ok: true, data: charPayload(), character: { tag } });
    }
    const tag = b.tag;
    const cu = USERCH.custom.find((c) => c.tag === tag);
    if (cu) {
      cu.trigger = f.trigger;
      cu.name = String(f.trigger).split(",")[0].trim();
      cu.zh = f.zh || "";
      cu.work = f.work || "";
      cu.tags = tagList.slice();
    } else {
      const p = { ...(USERCH.edits[tag] || {}), trigger: f.trigger };
      // 留空 = 清除覆盖（与真后端 _norm_overlay 丢空值的语义一致）
      if (f.zh) p.zh = f.zh; else delete p.zh;
      if (f.work) p.work = f.work; else delete p.work;
      if (tagList.length) p.tags = tagList.slice(); else delete p.tags;
      USERCH.edits[tag] = p;
    }
    return body({ ok: true, data: charPayload(), character: { tag } });
  }
  if (url.includes("/characters/detail")) {
    // 单个角色的完整记录 + 内置原始标签（列表载荷只带 tags[:8]，所以必须按需拉）
    const q = decodeURIComponent((url.split("?tag=")[1] || ""));
    const bi = CHAR_FIX.characters.find((c) => c.tag === q);
    const cu = USERCH.custom.find((c) => c.tag === q);
    if (!bi && !cu) {
      return { ok: false, status: 404, json: async () => ({ error: "找不到角色：" + q }) };
    }
    // ⚠️ 全量标签取自**原文**（RAW_BY_TAG），不能用夹具里被截断的 tags[:8]
    const raw = RAW_BY_TAG.get(q);
    const bt = raw ? (raw.tags || []) : [];
    const eff = cu ? { ...cu }
      : { ...bi, tags: bt.slice(), ...(USERCH.edits[q] || {}) };
    return body({
      ok: true, tag: q, user: !!cu,
      trigger: eff.trigger || "", zh: eff.zh || "", work: eff.work || "",
      thumb: eff.thumb || "", hair: eff.hair || "", eyes: eff.eyes || "",
      tags: (eff.tags || []).slice(),
      builtin_tags: bt.slice(),
      tags_overridden: !!(USERCH.edits[q] && "tags" in USERCH.edits[q]),
    });
  }
  if (url.endsWith("/characters/delete")) {
    // v3.18.6：单条（tag）与批量（tags）都支持，且**内置角色也能删**
    const a = arg();
    const want = Array.isArray(a.tags) ? a.tags : [a.tag];
    const deleted = [];
    const failed = [];
    for (const t of want) {
      const i = USERCH.custom.findIndex((c) => c.tag === t);
      if (i >= 0) {
        USERCH.custom.splice(i, 1);
        deleted.push(t);
        continue;
      }
      if (!CHAR_FIX.characters.some((c) => c.tag === t) || REMOVED_CHARS.has(t)) {
        failed.push({ tag: t, error: "找不到这个角色（可能已经被删掉）" });
        continue;
      }
      REMOVED_CHARS.add(t);
      deleted.push(t);
    }
    if (!deleted.length) {
      return { ok: false, status: 400,
               json: async () => ({ error: failed[0]?.error || "删除失败", failed }) };
    }
    return body({ ok: true, data: charPayload(), deleted, failed });
  }
  if (url.endsWith("/characters/reset")) {
    const tag = arg().tag;
    if (USERCH.custom.some((c) => c.tag === tag)) {
      return { ok: false, status: 400, json: async () => ({ error: "这是自定义角色" }) };
    }
    delete USERCH.edits[tag];
    return body({ ok: true, data: charPayload() });
  }
  if (url.endsWith("/characters/thumb-clear")) {
    const tag = arg().tag;
    const cu = USERCH.custom.find((c) => c.tag === tag);
    if (cu) cu.thumb = tag;
    else if (USERCH.edits[tag]) delete USERCH.edits[tag].thumb;
    return body({ ok: true, data: charPayload(), thumb: "user_" + tag });
  }
  if (url.endsWith("/characters/thumb")) {
    const b = arg();
    const tag = b.tag;
    const cu = USERCH.custom.find((c) => c.tag === tag);
    if (cu) cu.thumb = "user_" + tag;
    else USERCH.edits[tag] = { ...(USERCH.edits[tag] || {}), thumb: "user_" + tag };
    return body({ ok: true, data: charPayload(), thumb: "user_" + tag });
  }
  // 用户数据：注意顺序 —— 更具体的子路径先判（虽然 endsWith 本身不歧义）
  if (url.endsWith("/favorites/toggle")) {
    const b = arg();
    const on = b.on === undefined ? !STORE.favorites.includes(b.tag) : !!b.on;
    if (on && !STORE.favorites.includes(b.tag)) STORE.favorites.push(b.tag);
    if (!on) STORE.favorites = STORE.favorites.filter((t) => t !== b.tag);
    return body({ favorites: STORE.favorites });
  }
  if (url.endsWith("/favorites")) return body({ favorites: STORE.favorites });
  if (url.endsWith("/presets/save")) {
    const b = arg();
    if (!String(b.name || "").trim()) {
      return { ok: false, status: 400, json: async () => ({ error: "预设名不能为空" }) };
    }
    STORE.presets[b.name] = { ...(b.data || {}), saved_at: "2026-10-03 16:00:00" };
    return body({ presets: presetList() });
  }
  if (url.endsWith("/presets/delete")) {
    delete STORE.presets[arg().name];
    return body({ presets: presetList() });
  }
  if (url.endsWith("/presets")) return body({ presets: presetList() });
  if (url.includes("/thumb/")) {
    // 刻意回 404：验证面板会退化成首字母色块而不是整片空白
    return { ok: false, status: 404, json: async () => ({}) };
  }
  if (url.endsWith("/roll")) {
    // 与后端同一套语义的**最小**桩：这里只负责"回一个形状正确的响应"，
    // 真正的抽取逻辑由 Python 侧的 check_v3_gate 全套基线保证。
    const req = JSON.parse(opts.body);
    return body({
      text: "1girl, solo, black hair, school uniform, sitting, smile",
      tags: ["1girl", "solo", "black hair", "school uniform", "sitting", "smile"],
      state: { count: "1girl, solo", hair_color: "black hair" },
      level: req.nsfw_level,
      level_label: "0 · 全年龄",
      cn_summary: "单人少女，黑发，JK制服，端坐，微笑",
      dropped: [],
      notes: ["测试桩"],
      report: {
        n: 6, ok_f: 16, total_f: 16, ok_s: 10, total_s: 10,
        real_level: 0, drawn: [],
      },
    });
  }
  return { ok: false, status: 404, json: async () => ({}) };
};

/* ── 3. 载入插件 ─────────────────────────────────────────────────── */
await import(pathToFileURL(path.join(PLUG_DIR, "anima_wardrobe_v4_studio.js")).href);
const EXT = globalThis.__AV3_EXT;
ck("注册扩展成功", !!EXT && EXT.name === "Anima.Wardrobe.V3.Studio");

/* ── 4. 造节点 ───────────────────────────────────────────────────── */
function makeNode() {
  const node = {
    widgets: [],
    __av3Buttons: false,
    addWidget(type, name, value, cb) {
      const w = { type, name, value, callback: cb, serialize: true };
      this.widgets.push(w);
      return w;
    },
  };
  node.widgets.push(
    { type: "combo", name: "nsfw_level", value: "sfw", callback: null },
    { type: "number", name: "seed", value: -1, callback: null },
    { type: "combo", name: "tag_cap", value: "自动", callback: null },
    { type: "number", name: "base_prob", value: 0.65, callback: null },
    { type: "text", name: "disabled_sections", value: "", callback: null },
    { type: "text", name: "lock_fields", value: "", callback: null },
    { type: "text", name: "force_role", value: "", callback: null },
    { type: "text", name: "weights_json", value: "", callback: null },
    { type: "text", name: "extra_body", value: "", callback: null },
    { type: "toggle", name: "appearance_follow", value: true, callback: null },
    { type: "text", name: "selected_roles", value: "", callback: null },
    { type: "toggle", name: "big_breasts", value: false, callback: null },
    { type: "text", name: "role_works", value: "", callback: null },
    // v3.17.0：胸部大小等级（追加末尾；夹具必须与真后端 INPUT_TYPES 同源）
    { type: "combo", name: "breast_size", value: "不指定",
      options: { values: ["不指定", "平胸", "小胸", "中等", "大胸", "巨乳", "爆乳"] },
      callback: null },
    // v3.19.0：服装风格智配（同样追加末尾，与 nodes.py INPUT_TYPES 顺序一致）
    { type: "combo", name: "outfit_style", value: "随机", callback: null },
    // v3.45.0：角色官方发型对齐（追加末尾，与 nodes.py INPUT_TYPES 顺序一致）
    { type: "toggle", name: "hair_follow", value: true, callback: null },
    // v4.1.0：不指定角色（同样追加末尾 —— 控件顺序是契约）
    { type: "toggle", name: "role_pick", value: true, callback: null },
    // v5.0.1：场景搭配随机抽取（追加末尾；默认 False = 不出场景）
    { type: "toggle", name: "scene_pick", value: false, callback: null },
    // v6.5.0：皮肤干爽过滤（追加末尾；默认关 = 零漂移）
    { type: "toggle", name: "dry_skin_filter", value: false, callback: null },
    { type: "text", name: "dry_skin_groups", value: "", callback: null },
    // v6.5.2：逐条模式（追加末尾）
    { type: "text", name: "dry_skin_tags", value: "", callback: null },
    // v6.6.0：双女自动补位（追加末尾；默认开）
    { type: "toggle", name: "duo_role_fill", value: true, callback: null },
    // v6.7.0：勾选池每次自动随机（追加末尾；默认关闭）
    { type: "text", name: "pick_pool_mode", value: "", callback: null },
    { type: "text", name: "pick_pool_ids", value: "", callback: null },
  );
  return node;
}

const nodeType = function () {};
nodeType.prototype = {};
await EXT.beforeRegisterNodeDef(nodeType, { name: "AnimaV4RandomPrompt" });
ck("对非目标节点不生效", await (async () => {
  const other = function () {};
  other.prototype = {};
  await EXT.beforeRegisterNodeDef(other, { name: "Krea2PortraitV12" });
  return !other.prototype.onNodeCreated;
})());

/* v5.0.0：节点类型名升为 AnimaV5*，V4 / V3 在 NODE_CLASS_MAPPINGS 里保留为别名。
   面板必须三种都认 —— 只认主名的话，打开任何旧工作流，节点上都不会出现
   「打开随机面板」按钮（上面那次挂的是 V4，已证明别名可用；这里补 V5 与 V3）。 */
for (const nm of ["AnimaV5RandomPrompt", "AnimaV3RandomPrompt"]) {
  const t = function () {};
  t.prototype = {};
  await EXT.beforeRegisterNodeDef(t, { name: nm });
  ck(`面板也认节点类型 ${nm}`, typeof t.prototype.onNodeCreated === "function");
}

const node = makeNode();
nodeType.prototype.onNodeCreated.call(node);
const btnNames = node.widgets.filter((w) => w.type === "button").map((w) => w.name);
ck("节点挂上 4 个按钮", btnNames.length === 4, JSON.stringify(btnNames));
ck("按钮不进工作流 JSON", node.widgets.filter((w) => w.type === "button")
  .every((w) => w.serialize === false));

/* 控件名与后端 INPUT_TYPES 的一致性 —— 拼错一个字母面板就静默失灵 */
const BACKEND_WIDGETS = [
  "nsfw_level", "seed", "tag_cap", "base_prob", "disabled_sections",
  "lock_fields", "force_role", "weights_json", "extra_body",
  "appearance_follow",
  "selected_roles",
  "big_breasts",
  "role_works",
  "breast_size",
  "outfit_style",
  "role_fav_only",
  "hair_follow",
  "hair_len_pick",
  "hair_tie_pick",
  "hair_curl_pick",
  "hair_acc_pick",
  "role_pick",
  "scene_pick",
  // v6.5.0 / v6.5.2
  "dry_skin_filter",
  "dry_skin_groups",
  "dry_skin_tags",
  // v6.6.0：双女自动补位
  "duo_role_fill",
  // v6.7.0：勾选池自动随机
  "pick_pool_mode",
  "pick_pool_ids",
];
const jsSrc = fs.readFileSync(SRC_JS, "utf8");
const used = [...jsSrc.matchAll(/w(?:get|set)\(node,\s*"([^"]+)"/g)].map((m) => m[1]);
const unknown = [...new Set(used)].filter((n) => !BACKEND_WIDGETS.includes(n));
ck("JS 引用的控件名全部存在于节点", unknown.length === 0, JSON.stringify(unknown));

/* 接口 URL 与后端路由的一致性 */
const routes = [...jsSrc.matchAll(/`\/\$\{NS\}\/([a-z-]+)`/g)].map((m) => m[1]);
const pyInit = fs.readFileSync(path.join(PLUGIN, "__init__.py"), "utf8");
// v4.0.0：路由改用 `_dual_get/_dual_post` 动态拼接（同一处理函数镜像到 v4 与 v3），
// 所以字面量 `"/anima-wardrobe-v4/xxx"` 不再出现 —— 三种登记形式都要认。
const badRoutes = [...new Set(routes)].filter((r) =>
  !pyInit.includes(`/${r}\``)
  && !pyInit.includes(`"/anima-wardrobe-v4/${r}"`)
  && !pyInit.includes(`_dual_get("/${r}")`)
  && !pyInit.includes(`_dual_post("/${r}")`));
ck("JS 请求的路由后端都有", badRoutes.length === 0, JSON.stringify(badRoutes));

/* ── 5. 打开面板 ─────────────────────────────────────────────────── */
const openBtn = node.widgets.find((w) => w.name.includes("打开随机面板"));
openBtn.callback();
for (let i = 0; i < 80 && !document.body.querySelector(".av3-panel"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const panel = document.body.querySelector(".av3-panel");
ck("面板已打开", !!panel);
if (!panel) {
  console.log("\n[av3_ui_smoke] 面板没打开，后续用例跳过");
  console.log(`通过 ${PASS}，失败 ${FAILS.length}`);
  process.exit(1);
}

ck("请求了 /pools 与 /contract",
  CALLS.some((c) => c.url.endsWith("/pools")) &&
  CALLS.some((c) => c.url.endsWith("/contract")));

/* v5.0.21（用户 2026-11-04 20:13「面板内 NSFW 等级隐藏」）：
   档位条**界面上不可见**（display:none），但仍留在 DOM 里 ——
   ① 下面「切到过激档」的用例还要点它；② 将来挪到设置区只需去掉那行样式。
   所以这里断言的是「按钮齐 + 容器被隐藏」，不是「按钮消失」。 */
const lvBtns = panel.querySelectorAll(".av3-lv button");
ck("档位按钮 = 契约里的档位数", lvBtns.length === CONTRACT.levels.length,
  `${lvBtns.length} vs ${CONTRACT.levels.length}`);
ck("档位条已从面板隐藏（NSFW 等级只留节点控件这一个入口）",
  panel.querySelector(".av3-lv")?.style.display === "none",
  panel.querySelector(".av3-lv")?.style.display);
ck("默认选中当前档位（sfw）",
  [...lvBtns].find((b) => b.classList.contains("on"))?.textContent.includes("全年龄"));

/* v6.5.1：词条明细常驻显示（中文 + 英文）、段落标题去 R18 图标、分类 id 归一 */
{
  const det = panel.querySelector("#av3-dry-skin-detail");
  ck("干爽过滤有常驻词条明细区", !!det);
  ck("词条明细默认展开", !!det && det.style.display !== "none");
  const dtxt = det ? det.textContent : "";
  ck("明细含中文词条名", dtxt.includes("湿润光泽肌"), dtxt.slice(0, 80));
  ck("明细含英文 tag", dtxt.includes("glistening skin"));
  const cnt = panel.querySelector("#av3-dry-skin-detail-count");
  ck("明细显示已关闭组数 / 词条数",
     !!cnt && /已关闭 5 组 \/ 19 条词条/.test(cnt.textContent),
     cnt && cnt.textContent);
  const secTitles = [...panel.querySelectorAll(".av3-sec .hd .t")]
    .map((e) => e.textContent);
  ck("词库与权重页段落标题不再显示 R18 图标",
     !secTitles.some((s) => s.includes("🔞")),
     secTitles.filter((s) => s.includes("🔞")).join("|"));
  ck("面板 JS 保留历史分类 id 归一映射",
     jsSrc.includes("OUTFIT_CAT_ALIAS") && jsSrc.includes("nsfw_outfit"));
}

/* v6.5.2 干爽过滤：逐条开关 / 组批量 / 全选清空 / 写回 dry_skin_tags */
{
  const dryWrap = panel.querySelector("#av3-dry-skin");
  ck("词库与权重页有干爽过滤面板", !!dryWrap);
  const dryCb = panel.querySelector("#av3-dry-skin-cb");
  ck("干爽过滤默认关闭（零漂移）", !!dryCb && dryCb.checked === false);
  const dryGroups = panel.querySelectorAll(
    "#av3-dry-skin-groups input[type=checkbox]");
  const wantGroups = (CONTRACT.dry_skin_groups || []).length;
  ck("干爽分组数 = contract 的组数", dryGroups.length === wantGroups,
     `${dryGroups.length} vs ${wantGroups}`);
  const wantTags = (CONTRACT.dry_skin_groups || [])
    .reduce((n, g) => n + (g.items || g.tags || []).length, 0);
  const tagCbs = panel.querySelectorAll(
    "#av3-dry-skin-detail input[type=checkbox][data-tag]");
  ck("每条词条都有独立复选框", tagCbs.length === wantTags,
     `${tagCbs.length} vs ${wantTags}`);
  ck("逐条复选框默认全勾（保持全部关闭）",
     [...tagCbs].every((c) => c.checked === true));
  const wt = node.widgets.find((w) => w.name === "dry_skin_tags");
  if (dryCb && wt && tagCbs.length) {
    dryCb.checked = true;
    dryCb.onchange();
    const wf = node.widgets.find((w) => w.name === "dry_skin_filter");
    ck("勾上干爽过滤会写回节点控件", !!(wf && wf.value === true),
       wf && wf.value);

    // 逐条：取消一条 → 写入 18 条列表，该组变半选
    const first = tagCbs[0];
    const firstTag = first.dataset.tag;
    first.checked = false;
    first.onchange();
    ck("取消单条会写入逐条列表（不含该条）",
       typeof wt.value === "string" && wt.value.length > 0
       && wt.value !== "all" && wt.value !== "none"
       && !wt.value.split(",").includes(firstTag), wt.value);
    let g0 = panel.querySelector(
      '#av3-dry-skin-groups input[data-group="skin"]');
    ck("部分勾选时组复选框为半选态", !!g0 && g0.indeterminate === true,
       g0 && g0.indeterminate);

    // 组批量：勾回整组 → 不再半选
    if (g0) {
      g0.checked = true;
      g0.onchange();
      g0 = panel.querySelector(
        '#av3-dry-skin-groups input[data-group="skin"]');
      ck("组批量勾选后不再半选", !!g0 && !g0.indeterminate);
    }

    // 清空 → 显式 "none"（绝不能回落成「全部关闭」）
    panel.querySelector("#av3-dry-skin-none").onclick();
    ck("清空写入 none（显式一条都不关）", wt.value === "none", wt.value);
    ck("清空后自动关掉总开关",
       node.widgets.find((w) => w.name === "dry_skin_filter").value === false);
    // 全选 → "all"
    panel.querySelector("#av3-dry-skin-all").onclick();
    ck("全选写入 all", wt.value === "all", wt.value);

    // 复位，别污染后面重开面板的用例
    dryCb.checked = false;
    dryCb.onchange();
    wt.value = "";
    node.widgets.find((w) => w.name === "dry_skin_groups").value = "";
  }
}

const secEls = panel.querySelectorAll(".av3-sec");
ck("段落块 = 词库段数", secEls.length === POOLS.sections.length,
  `${secEls.length} vs ${POOLS.sections.length}`);

let fieldRows = 0;
let weightInputs = 0;
for (const s of POOLS.sections) {
  const el = panel.querySelector(`.av3-sec[data-sec="${s.id}"]`);
  if (!el) continue;
  const rows = el.querySelectorAll(".av3-row");
  fieldRows += rows.length;
  for (const r of rows) if (r.querySelector("input.av3-w")) weightInputs++;
}
const totalFields = POOLS.sections.reduce((n, s) => n + s.fields.length, 0);
// v3.2.0：角色字段从权重页移除了（选择权交给「角色库」页签），
// 所以权重页的行数 = 词库字段数 - 1。这里要**精确**等于，不能只判"少一个"，
// 否则哪天误删了别的字段也测不出来。
const weightPageFields = totalFields - 1;
ck("字段行 = 词库字段数 - 1（角色已移出权重页）",
  fieldRows === weightPageFields, `${fieldRows} vs ${weightPageFields}`);
ck("每个字段都有权重输入框",
  weightInputs === weightPageFields, `${weightInputs} vs ${weightPageFields}`);

// 角色字段确实不在权重页里 —— 这条是上面那个 -1 的正面依据
{
  const roleRow = panel.querySelector('.av3-row[data-key="role"]');
  ck("权重页里没有角色字段行", !roleRow,
    roleRow ? roleRow.outerHTML.slice(0, 80) : "");
  const anyRoleLab = [...panel.querySelectorAll(".av3-row .lab")]
    .some((e) => e.textContent.includes("角色"));
  ck("权重页里没有叫「角色」的字段标签", !anyRoleLab);
}
// 行必须带 data-key：refreshSectionState 靠它对齐 🎯 徽标，
// 用索引对齐的话跳过 role 之后会整体错位一格
ck("每行都有 data-key",
  [...panel.querySelectorAll(".av3-row")].every((r) => !!r.dataset.key),
  [...panel.querySelectorAll(".av3-row")].filter((r) => !r.dataset.key).length + " 行缺 key");

ck("默认展开前三段",
  panel.querySelectorAll(".av3-sec.open").length === 3,
  String(panel.querySelectorAll(".av3-sec.open").length));

/* ── 6. 权重细则弹窗 ─────────────────────────────────────────────── */
const hairSec = panel.querySelector('.av3-sec[data-sec="hair"]');
const colorRow = [...hairSec.querySelectorAll(".av3-row")]
  .find((r) => r.querySelector(".lab")?.textContent === "颜色");
ck("找到「头发 / 颜色」字段行", !!colorRow);
const badge = colorRow.querySelector(".av3-t");
ck("🎯 徽标初始为未调", badge.textContent.trim() === "🎯", badge.textContent);

badge.click();
const mini = document.body.querySelector(".av3-mini");
ck("权重细则弹窗打开", !!mini);
const fieldOpts = POOLS.sections
  .find((s) => s.id === "hair").fields.find((f) => f.key === "hair_color").opts;
const oRows = mini.querySelectorAll(".av3-oRow");
ck("细则里选项数 = 该字段选项数", oRows.length === fieldOpts.length,
  `${oRows.length} vs ${fieldOpts.length}`);

/* 快捷按钮「3」→ 权重 3.0 */
const firstQuick3 = oRows[0].querySelectorAll(".av3-t")[3];
firstQuick3.click();
const firstInput = oRows[0].querySelector("input.av3-w");
ck("快捷按钮写入输入框", firstInput.value === "3", firstInput.value);

/* 应用 → 回写控件 + 徽标变三态之一 */
mini.querySelector(".ft .pri").click();
ck("弹窗关闭", !document.body.querySelector(".av3-mini"));
const wj = node.widgets.find((w) => w.name === "weights_json").value;
let wjParsed = null;
try { wjParsed = JSON.parse(wj); } catch (e) { /* 留 null，下面断言会红 */ }
ck("权重写回 weights_json（结构化核对）",
  !!wjParsed && wjParsed.options?.hair_color?.["black hair"] === 3,
  wj);
ck("🎯 徽标变成「调过」", badge.textContent.trim().startsWith("🎯 ")
  || badge.textContent.trim() === "🎯 全部", badge.textContent);

/* ── 6.5 同类型互斥只能有一个「必现」（v5.0.1）─────────────────────
   用户 2026-11-01：「权重主动选择为 3 时如果其他词条权重也为 3，自动把之前
   选择的降为 1 —— 权重 3 是必出，同类型互斥的两条不能都必出」。
   上面那条用例已经把第一个选项（black hair）设成 3.0，这里再点第二个的「3」。 */
badge.click();
const mini2 = document.body.querySelector(".av3-mini");
ck("重开细则弹窗（black hair 已是 3.0）", !!mini2);
const rows2 = mini2.querySelectorAll(".av3-oRow");
const in0 = rows2[0].querySelector("input.av3-w");
const in1 = rows2[1].querySelector("input.av3-w");
ck("第一个选项仍是 3", in0.value === "3", in0.value);
rows2[1].querySelectorAll(".av3-t")[3].click();        // 第二个选项点「3」
ck("第二个选项变 3", in1.value === "3", in1.value);
ck("先前那个自动降为 1（同类型互斥）", in0.value === "1", in0.value);
ck("弹窗里给了降级说明", /已自动降为 1/.test(
  (mini2.querySelector("#av3-w-mutex-hint") || {}).textContent || ""),
  (mini2.querySelector("#av3-w-mutex-hint") || {}).textContent);
// ⚠️ `.av3-mini` 是弹窗**内容**，外层还有一个 `.av3-mask` 遮罩 ——
//    只 remove 内层会留下 mask，后面「面板已关闭」两条断言就会红（踩过）。
const mask2 = mini2.closest(".av3-mask");
(mask2 || mini2).remove();

/* ── 7. 清空权重 ─────────────────────────────────────────────────── */
const clearBtn = [...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("清空权重"));
clearBtn.click();
ck("清空后控件为空串",
  node.widgets.find((w) => w.name === "weights_json").value === "");

/* ── 8. 段权重 0 → 写进 disabled_sections（采用时才写） ─────────── */
const sceneSec = panel.querySelector('.av3-sec[data-sec="scene"]');
const sceneW = sceneSec.querySelector(".hd input.av3-w");
sceneW.value = "0";
sceneW.dispatchEvent(new dom.window.Event("input"));
ck("段权重 0 时该段置灰", sceneSec.classList.contains("off"));

/* ── 8.5 段「本档不参与」的判据（v3.8.0 修）───────────────────────
   判据必须是「段内**还有没有** ≤ 当前档的选项」，不能是「段最高档 ≤ 当前档」：
   后者在段内混入分档词条后会误标 —— v3.8.0 给「视角」加了 `pov crotch`(L2)、
   `pov, fellatio`(L3) 之后，⑧镜头段的最高档被抬到 3，全年龄档就会被打上
   「本档不参与」，而该段 16 条全年龄词条照样在抽。 */
const secTitle = (id) => panel.querySelector(`.av3-sec[data-sec="${id}"] .hd .t`).textContent;
const labeled = (id) => secTitle(id).includes("本档不参与");
ck("⑧镜头 在全年龄档不被误标（段内仍有全年龄视角词条）", !labeled("camera"), secTitle("camera"));
ck("④身材与肤质 在全年龄档不被误标", !labeled("body"), secTitle("body"));
ck("🔞服装状态 在全年龄档仍标为不参与", labeled("cloth_nsfw"), secTitle("cloth_nsfw"));
// ⚠️ v3.9.0 起法典段的判据变了：场景搭配库里有 L0 的 SFW 预设，
//    所以全年龄档下该段**有活可干** —— 不再标「本档不参与」。
ck("🎬法典/场景段 在全年龄档参与（有 L0 场景搭配）",
  !labeled("canon"), secTitle("canon"));

/* ── 9. 全部随机（预演） ─────────────────────────────────────────── */
const rollBtn = [...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("全部随机"));
rollBtn.click();
for (let i = 0; i < 80 && !(panel.querySelector(".av3-out")?.textContent || "")
  .includes("school uniform"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const outTxt = panel.querySelector(".av3-out").textContent;
ck("结果渲染到输出区", outTxt.includes("school uniform"), outTxt.slice(0, 60));
ck("中文摘要渲染", panel.querySelector(".av3-sum").textContent.includes("单人少女"));
ck("自检行渲染", /自检/.test(panel.querySelector(".av3-sc").textContent));

const rollCall = CALLS.filter((c) => c.url.endsWith("/roll")).pop();
const sent = JSON.parse(rollCall.opts.body);
ck("请求带了档位", sent.nsfw_level === "sfw", sent.nsfw_level);
ck("请求带了关闭的段", sent.disabled_sections === "scene", sent.disabled_sections);
ck("请求带了外貌对齐开关", sent.appearance_follow === true);
ck("请求带了发型对齐开关", sent.hair_follow === true);

/* ── 9.5 预演必须带上「已选角色」与「锁定字段」（v3.8.0 修）──────────
   修之前这两个没发 → 选了角色点「全部随机」看到的预览是全库随机抽的，
   而真正跑工作流按 selected_roles 走 —— 预览与实跑对不上，
   与「面板与工作流共用同一套引擎」的承诺直接矛盾。 */
const rollCalls = () => CALLS.filter((c) => c.url.endsWith("/roll"));
const nRoll = rollCalls().length;
node.widgets.find((w) => w.name === "selected_roles").value = "hatsune_miku";
node.widgets.find((w) => w.name === "lock_fields").value = "place";
rollBtn.click();
for (let i = 0; i < 80 && rollCalls().length === nRoll; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const sent2 = JSON.parse(rollCalls().pop().opts.body);
ck("预演请求带上已选角色（修：预演 ≠ 实跑）",
  sent2.selected_roles === "hatsune_miku", String(sent2.selected_roles));
ck("预演请求带上锁定字段",
  sent2.lock_fields === "place", String(sent2.lock_fields));
node.widgets.find((w) => w.name === "selected_roles").value = "";
node.widgets.find((w) => w.name === "lock_fields").value = "";

/* ── 10. 采用这条 → 写回 seed ───────────────────────────────────── */
const before = node.widgets.find((w) => w.name === "seed").value;
const adopt = [...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("采用这条"));
adopt.click();
const after = node.widgets.find((w) => w.name === "seed").value;
ck("采用后 seed 被写回", after !== before && after >= 0, `${before} → ${after}`);
ck("采用后档位写回 nsfw_level",
  node.widgets.find((w) => w.name === "nsfw_level").value === "sfw");
ck("采用后 disabled_sections 写回",
  node.widgets.find((w) => w.name === "disabled_sections").value === "scene");


/* ── 10.5 角色库页签 ────────────────────────────────────────────── */
const tabBtns = [...panel.querySelectorAll(".av3-tabs button")];
ck("有八个页签（词库权重 / 角色库 / 👗 服装库 / 🎬 场景搭配 / 🧘 姿势库 / 🎨 艺术构图 / ⚔️ 武器场景 / 🔍 特写搭配）",
  tabBtns.length === 8, tabBtns.map((b) => b.textContent).join("/"));
/* v5.1.0（用户 2026-11-05）：
   ① 服装库升为一级页签（与角色库同级）；
   ② 姿势从「场景搭配里的一个筛选按钮」升为一级页签（与场景搭配同级）。 */
ck("服装库是独立页签（v5.1.0，与角色库同级）",
  tabBtns[2].textContent.includes("服装库"), tabBtns[2].textContent);
ck("场景搭配是独立页签", tabBtns[3].textContent.includes("场景搭配"),
  tabBtns[3].textContent);
ck("姿势库是独立页签（v5.1.0，与场景搭配同级）",
  tabBtns[4].textContent.includes("姿势库"), tabBtns[4].textContent);
/* v5.0.21（用户 2026-11-04 20:13）：艺术构图从「场景搭配里的一个筛选按钮」
   升为一级页签，与场景搭配、特写搭配同级。 */
ck("艺术构图是独立页签（v5.0.21，与场景搭配同级）",
  tabBtns[5].textContent.includes("艺术构图"), tabBtns[5].textContent);
ck("武器场景是独立页签（v5.10.0，与艺术构图同级）",
  tabBtns[6].textContent.includes("武器场景"), tabBtns[6].textContent);
ck("特写搭配是独立页签（v3.18.0，与场景搭配分开）",
  tabBtns[7].textContent.includes("特写搭配"), tabBtns[7].textContent);
ck("默认在词库页", tabBtns[0].classList.contains("on")
  && panel.querySelectorAll(".av3-sec").length > 0);

tabBtns[1].click();               // 切到角色库
for (let i = 0; i < 100 && !panel.querySelector(".av3-card"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("切页后请求了 /characters", CALLS.some((c) => c.url.endsWith("/characters")));
ck("权重树已让位（左列不再有段落块）",
  panel.querySelectorAll(".av3-sec").length === 0);

// ⚠️ 作品行会随每次 renderChars() 重建，所以这里用**函数**查而不是缓存数组，
//    否则后续点击会操作到已经脱离文档的旧节点。
const workRows = () => [...panel.querySelectorAll(".av3-work")];
const rowOf = (label) =>
  workRows().find((r) => r.querySelector(".n").textContent === label);
const workLabel = (w) => w.zh || w.en;

// 作品列表 = 「全部作品」+ 「⭐ 我的收藏」+ 每个作品一行
ck("作品列表 = 作品数 + 2（「全部作品」+「我的收藏」）",
  workRows().length === CHAR_FIX.works.length + 2, String(workRows().length));
ck("「⭐ 我的收藏」紧跟在「全部作品」后面",
  workRows()[1].querySelector(".n").textContent.includes("我的收藏"),
  workRows()[1].querySelector(".n").textContent);
ck("收藏为 0 时该行置灰", workRows()[1].classList.contains("empty"));
ck("作品显示中文名", !!rowOf(workLabel(CHAR_FIX.works[0])),
  workLabel(CHAR_FIX.works[0]));
ck("v5.0.4 起保底作品不再挂 tgt 类（★ 只归收藏，两个星同屏分不清）",
  workRows().filter((r) => r.classList.contains("tgt")).length === 0,
  `${workRows().filter((r) => r.classList.contains("tgt")).length} 行仍带 tgt`);

const cards = () => [...panel.querySelectorAll(".av3-card")];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── v5.0.4：作品 IP 收藏（星标 + 置顶）────────────────────────────
// 桩在文件头部 `WORK_FAV`（模拟 user_work_favorites.json，值域是作品 slug）。
{
  const firstW = CHAR_FIX.works[0];
  const starOf = (label) => {
    const r = rowOf(label);
    return r && r.querySelector(".wstar");
  };
  ck("每个真作品行都有星标控件",
    workRows().slice(2).every((r) => !!r.querySelector(".wstar")),
    `${workRows().slice(2).filter((r) => !r.querySelector(".wstar")).length} 行缺`);
  ck("「全部作品」「我的收藏」是视图不是作品，没有星标",
    !rowOf("全部作品").querySelector(".wstar")
    && !workRows()[1].querySelector(".wstar"));
  ck("未收藏时星标是空心 ☆", starOf(workLabel(firstW)).textContent === "☆",
    starOf(workLabel(firstW)).textContent);

  const nBefore = CALLS.filter((c) => c.url.endsWith("/works/favorites/toggle")).length;
  starOf(workLabel(firstW)).click();
  // ⚠️ 等待条件必须看 **DOM**，不能只看桩数组 WORK_FAV：
  //    桩在 mock handler 里同步写入，而前端要等 await r.json() 之后才 buildWorkList，
  //    盯着桩数组会早一步放行，断言就落到还没重建的旧节点上（实测红）。
  for (let i = 0; i < 100; i++) {
    await sleep(10);
    const r = rowOf(workLabel(firstW));
    if (r && r.classList.contains("fav")) break;
  }
  ck("点 ☆ 发出 /works/favorites/toggle（独立于角色收藏那条路由）",
    CALLS.filter((c) => c.url.endsWith("/works/favorites/toggle")).length === nBefore + 1);
  ck("收藏的作品置顶到真作品第一行",
    workRows()[2].querySelector(".n").textContent === workLabel(firstW),
    workRows()[2].querySelector(".n").textContent);
  ck("收藏行带 fav 类 + 实心 ★",
    rowOf(workLabel(firstW)).classList.contains("fav")
    && starOf(workLabel(firstW)).textContent === "★",
    starOf(workLabel(firstW)).textContent);
  ck("点星不切换当前 IP（仍停在「全部作品」）",
    rowOf("全部作品").classList.contains("on")
    && !rowOf(workLabel(firstW)).classList.contains("on"));

  starOf(workLabel(firstW)).click();
  for (let i = 0; i < 100; i++) {
    await sleep(10);
    const r = rowOf(workLabel(firstW));
    if (r && !r.classList.contains("fav")) break;
  }
  ck("再点一次取消收藏，第一行回到原来的作品",
    !rowOf(workLabel(firstW)).classList.contains("fav")
    && workRows()[2].querySelector(".n").textContent === workLabel(CHAR_FIX.works[0]),
    workRows()[2].querySelector(".n").textContent);
}

const PAGE = 96;   // 面板分页渲染上限（与 ui.charLimit 初值一致）
ck("角色卡片 = 首页分页数（不是把 2687 条一次塞进 DOM）",
  cards().length === Math.min(PAGE, CHAR_FIX.characters.length),
  `${cards().length} / 总 ${CHAR_FIX.characters.length}`);

// 默认按人气降序：期望值从夹具现算，不硬编码名字 ——
// 硬编码的断言会随数据变动而红，那种红没有信息量。
const top = [...CHAR_FIX.characters].sort((a, b) => b.pop - a.pop)[0];
ck("默认按人气排行（第一位是人气最高的）",
  cards()[0].querySelector(".nm").textContent === (top.zh || top.name),
  `${cards()[0].querySelector(".nm").textContent} vs ${top.zh || top.name}`);
ck("卡片带 dataset.tag（测试定位用）", cards()[0].dataset.tag === top.tag,
  cards()[0].dataset.tag || "(空)");
ck("卡片显示中文名 + 英文名",
  cards()[0].querySelector(".en").textContent === top.name,
  cards()[0].querySelector(".en").textContent);
ck("卡片显示人气值", !!cards()[0].querySelector(".pp").textContent);

// 缩略图加载失败 → 应退化成首字母色块，而不是留一个破图。
// ⚠️ jsdom 默认不加载图片，onerror 永远不会自己触发，
//    所以这里**手工派发 error 事件** —— 测的是我们自己的降级逻辑，
//    不是 jsdom 的加载器。
const phImgs = [...panel.querySelectorAll(".av3-card img.ph")];
phImgs.forEach((im) => im.dispatchEvent(new dom.window.Event("error")));
ck("缩略图加载失败时退化成首字母色块",
  panel.querySelectorAll(".av3-card .ph.letter").length === phImgs.length
  && panel.querySelectorAll(".av3-card img.ph").length === 0,
  `色块 ${panel.querySelectorAll(".av3-card .ph.letter").length} / 破图 ${panel.querySelectorAll(".av3-card img.ph").length}`);
ck("色块用角色名首字",
  [...panel.querySelectorAll(".av3-card .ph.letter")]
    .map((e) => e.textContent).join("").includes((top.zh || top.name).slice(0, 1)),
  [...panel.querySelectorAll(".av3-card .ph.letter")].map((e) => e.textContent).join(""));

// ── 点 IP 作品筛选 ────────────────────────────────────────────────
// ★ 这一条就是线上 bug 的回归用例。
//   期望值取自 **works[].count**（后端算的），实际值取前端按 work_id 筛出的
//   卡片数 —— 两边来源不同，所以能真正验证一致性。
//   用错字段（`c.work`，那是作品英文名）时列表为空，这里会得到 0。
const zzz = CHAR_FIX.works.find((w) => w.id === "zenless_zone_zero");
rowOf(workLabel(zzz)).click();
ck(`点「${workLabel(zzz)}」→ 卡片数 == works.count（${zzz.count}）`,
  cards().length === Math.min(PAGE, zzz.count),
  `${cards().length}（应为 ${Math.min(PAGE, zzz.count)}；字段用错时是 0）`);
ck("点作品后列表非空（不是「没有匹配的角色」）",
  cards().length > 0
  && !panel.querySelector(".av3-grid .av3-empty"),
  panel.querySelector(".av3-grid .av3-empty")?.textContent || "");
ck("作品行高亮", rowOf(workLabel(zzz)).classList.contains("on"));

// 再拿一个「角色数 < 分页上限」的作品做**精确**数量断言，不被分页掩盖
const small = CHAR_FIX.works
  .filter((w) => w.count >= 3 && w.count <= PAGE - 1)
  .sort((a, b) => b.count - a.count)[0];
rowOf(workLabel(small)).click();
ck(`点「${workLabel(small)}」→ 卡片数精确 == count（${small.count}）`,
  cards().length === small.count, `${cards().length} vs ${small.count}`);
ck("切换作品后卡片确实换了（不是残留上一批）",
  cards().every((c) => {
    const rec = CHAR_FIX.characters.find((x) => x.tag === c.dataset.tag);
    return rec && rec.work_id === small.id;
  }));

// 回全部 + 搜索
rowOf("全部作品").click();
const searchIn = panel.querySelector(".av3-charbar input[type=text]");
const typeSearch = (v) => {
  searchIn.value = v;
  searchIn.dispatchEvent(new dom.window.Event("input"));
};

typeSearch(top.zh || top.name);
ck(`关键词搜索命中中文名（${top.zh || top.name}）`,
  cards().length >= 1 && cards().some((c) => c.dataset.tag === top.tag),
  String(cards().length));

// work_id 也在搜索串里 —— 从别处粘 slug 进来也能搜到
typeSearch(zzz.id);
ck(`关键词能搜到 work_id（${zzz.id}）`,
  cards().length === Math.min(PAGE, zzz.count), String(cards().length));

/* ── v5.3.1：角色库搜索框的 IME 保护（用户 2026-11-05「输入中文会自动变成英文」）──
   与场景页同一个根因：拼音还在组合串里，charQuery 就被写成半截拼音，
   紧接着 renderChars() 按它重建输入框。这组断言钉住三件事：
   组合中不写 charQuery、不重建输入框、上屏后按中文命中。 */
typeSearch("");            // 先把上一条（work_id）筛选清掉，基准才是全量首屏
await sleep(30);
const charQ = () => panel.querySelector(".av3-charbar input[type=text]");
const q0 = charQ();
q0.dispatchEvent(new dom.window.Event("compositionstart"));
q0.value = "hutao";                       // 组合串（拼音，还没上屏）
q0.dispatchEvent(new dom.window.Event("input"));
await sleep(30);
ck("角色搜索 IME：组合中不按拼音过滤（仍是全量首屏）",
  cards().length === PAGE, `${cards().length} 张 / 期望 ${PAGE}`);
ck("角色搜索 IME：组合中输入框节点没被重建",
  charQ() === q0, charQ() === q0 ? "" : "节点被重建 → 拼音会被提交成字母");
const zh0 = top.zh || top.name;
q0.value = zh0;                           // 上屏中文
q0.dispatchEvent(new dom.window.Event("compositionend"));
await sleep(30);
ck(`角色搜索 IME：上屏后按中文命中（${zh0}）`,
  cards().some((c) => c.dataset.tag === top.tag), String(cards().length));
ck("角色搜索 IME：框里是中文，不是拼音",
  charQ() && charQ().value === zh0, `「${charQ()?.value}」`);
typeSearch("");

// ── 选中角色 ──────────────────────────────────────────────────────
// ⚠️ 不再按中文名找卡片：真实数据里那几位未必落在首页，
//    硬编码名字的用例会随数据变动而红。
//    改成「筛到一个小作品，然后点前几张卡片」。
const roleWidget = () => node.widgets.find((w) => w.name === "selected_roles").value;
const cardOf = (tag) => cards().find((c) => c.dataset.tag === tag);

const pickWork = CHAR_FIX.works
  .filter((w) => w.count >= 3 && w.count <= 40)
  .sort((a, b) => b.count - a.count)[0];
rowOf(workLabel(pickWork)).click();
ck("小作品下至少有三张卡片可选", cards().length >= 3, String(cards().length));
const t0 = cards()[0].dataset.tag;
const t1 = cards()[1].dataset.tag;
const t2 = cards()[2].dataset.tag;

cardOf(t0).click();
ck("点卡片写入 selected_roles", roleWidget() === t0, roleWidget());
ck("选中卡片高亮 + 打勾",
  cardOf(t0).classList.contains("on") && !!cardOf(t0).querySelector(".tick"));
ck("chips 显示已选角色", panel.querySelectorAll(".av3-chip").length === 1);
ck("人数预览 = 1girl solo（当前档位 sfw）",
  panel.querySelector(".av3-chips").textContent.includes("1girl, solo"),
  panel.querySelector(".av3-chips").textContent);

// 两位（双女）
cardOf(t1).click();
ck("两位角色写入两个 tag（| 分隔）",
  roleWidget() === `${t0}|${t1}`, roleWidget());
ck("两位 + 全年龄档 → 预览 2girls, multiple girls（不用 yuri）",
  panel.querySelector(".av3-chips").textContent.includes("2girls, multiple girls"),
  panel.querySelector(".av3-chips").textContent);

// 再点一次取消
cardOf(t1).click();
ck("再点一次取消选中", roleWidget() === t0, roleWidget());
cardOf(t1).click();

// 三位 → 群像
cardOf(t2).click();
ck("三位 → 预览 multiple girls（多人群像）",
  panel.querySelector(".av3-chips").textContent.includes("multiple girls"),
  panel.querySelector(".av3-chips").textContent);

// 切档位后预览要跟着变。
// ⚠️ 切档位会重建整个角色库页，卡片元素全部换新 ——
//    所以这里必须用 cardOf(tag) 重新查，不能留着旧引用点。
[...panel.querySelectorAll(".av3-lv button")].find((b) => b.textContent.includes("过激"))
  .click();
ck("切到过激档：3 位仍是群像",
  panel.querySelector(".av3-chips").textContent.includes("multiple girls"));
cardOf(t2).click();               // 退回 2 位
ck("2 位 + 过激档 → 预览 2girls, yuri",
  panel.querySelector(".av3-chips").textContent.includes("2girls, yuri"),
  panel.querySelector(".av3-chips").textContent);
[...panel.querySelectorAll(".av3-lv button")].find((b) => b.textContent.includes("全年龄"))
  .click();
ck("切回全年龄档 → 预览回到 2girls, multiple girls",
  panel.querySelector(".av3-chips").textContent.includes("2girls, multiple girls"),
  panel.querySelector(".av3-chips").textContent);
ck("切档位不会把角色库页顶掉", panel.querySelectorAll(".av3-card").length > 0);

// 清除
[...panel.querySelectorAll(".av3-btn")].find((b) => b.textContent.includes("清除已选角色"))
  .click();
ck("一键清除已选角色", roleWidget() === "", JSON.stringify(roleWidget()));
ck("清除后 chips 清空", panel.querySelectorAll(".av3-chip").length === 0);

/* ── 10.5.5 🎲 按当前筛选（作品 IP）随机抽角色（v3.8.0）──────────── */
// 候选池 = 当前的「作品筛选 + 关键词」结果，所以先用一个已知作品做精确断言。
rowOf(workLabel(small)).click();
const poolTags = new Set(cards().map((c) => c.dataset.tag));
const rndN = panel.querySelector(".av3-charbar input[type=number]");
const rndBtn = [...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("随机抽"));
ck("角色库页有 🎲 随机抽按钮", !!rndBtn);
ck("抽取位数输入框默认 1", !!rndN && rndN.value === "1", rndN && rndN.value);

const setN = (v) => {
  rndN.value = String(v);
  rndN.dispatchEvent(new dom.window.Event("change"));
};
setN(2);
rndBtn.click();
const picked = String(roleWidget()).split("|").filter(Boolean);
ck("🎲 一次抽 2 位", picked.length === 2, JSON.stringify(picked));
ck("抽到的角色全部来自当前筛选池",
  picked.every((t) => poolTags.has(t)), JSON.stringify(picked));
ck("抽到的角色互不重复", new Set(picked).size === picked.length);
ck("抽完 chips 同步为 2 位", panel.querySelectorAll(".av3-chip").length === 2);
ck("抽完卡片同步打勾",
  picked.every((t) => cardOf(t) && cardOf(t).classList.contains("on")));

// 追加语义（与手动点卡片一致）：再抽不会把已有的顶掉
setN(1);
rndBtn.click();
const picked3 = String(roleWidget()).split("|").filter(Boolean);
ck("🎲 是追加而不是替换",
  picked3.length === 3 && picked.every((t) => picked3.includes(t)),
  JSON.stringify(picked3));

// 池子被抽空 / 空结果时给提示而不是抛异常
// ⚠️ 状态条不是页面里唯一的 .av3-meta（左列提示、结果区都有），
//    所以限定在右列并"任一命中"来断言，别用全局 querySelector 抓第一个。
const statusText = () => [...panel.querySelectorAll(".av3-right .av3-meta")]
  .map((e) => e.textContent).join(" | ");
const clearBtn2 = [...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("清除已选角色"));
clearBtn2.click();
typeSearch("zzz_no_such_character_zzz");
rndBtn.click();
ck("空池子时只给提示、不写控件", roleWidget() === "", roleWidget());
ck("空池子提示出现在状态条", /没有角色可抽/.test(statusText()),
  statusText().slice(0, 80));
typeSearch("");
rowOf("全部作品").click();

/* ── 10.5.6 🔁 随机池（role_works · 每次自动换人，v3.10.0）───────── */
const rw = () => node.widgets.find((w) => w.name === "role_works").value;
const poolChips = () => [...panel.querySelectorAll("[data-pool]")];
const poolBtn = (t) => [...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes(t));

ck("随机池区默认显示「未限定」",
  panel.querySelector(".av3-chips").parentElement
  && [...panel.querySelectorAll(".av3-meta")]
    .some((e) => e.textContent.includes("（未限定）")),
  String(rw()));

// 选一个作品再加进池子
rowOf(workLabel(small)).click();
poolBtn("＋ 加入随机池").click();
ck("加入随机池 → 写到 role_works 控件", rw() === small.id, rw());
ck("随机池出现该作品的 chip",
  poolChips().length === 1 && poolChips()[0].dataset.pool === small.id,
  String(poolChips().length));

// 重复加入不叠加
poolBtn("＋ 加入随机池").click();
ck("重复加入不会产生第二条", poolChips().length === 1, String(poolChips().length));

// 再加一个作品 → 两个用 | 连接
rowOf(workLabel(zzz)).click();
poolBtn("＋ 加入随机池").click();
ck("多作品用 | 连接",
  rw() === `${small.id}|${zzz.id}`, rw());
ck("chips 同步为 2 条", poolChips().length === 2, String(poolChips().length));

// 「全部作品」不是有效 IP —— 不该被加进去
rowOf("全部作品").click();
poolBtn("＋ 加入随机池").click();
ck("「全部作品」不能被加进随机池（不是 IP）", poolChips().length === 2, rw());

// 已选角色存在时要给出「已选角色优先」的警告
cardOf(cards()[0].dataset.tag).click();
ck("有已选角色时提示「已选角色优先」",
  [...panel.querySelectorAll(".av3-meta")]
    .some((e) => e.textContent.includes("已选角色优先")),
  panel.querySelector(".av3-chips").textContent);
[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("清除已选角色")).click();

// 单个 chip 的 × 移除
poolChips()[0].querySelector("b").click();
ck("点 chip 的 × 移除该作品", rw() === zzz.id, rw());
ck("移除后 chips 只剩 1 条", poolChips().length === 1);

// 预演请求要带上随机池
const nRoll3 = CALLS.filter((c) => c.url.endsWith("/roll")).length;
rollBtn.click();
for (let i = 0; i < 80
     && CALLS.filter((c) => c.url.endsWith("/roll")).length === nRoll3; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const sent3 = JSON.parse(CALLS.filter((c) => c.url.endsWith("/roll")).pop().opts.body);
ck("预演请求带上随机池（role_works）",
  sent3.role_works === zzz.id, String(sent3.role_works));

// 清空
poolBtn("清空随机池").click();
ck("清空随机池 → 控件为空串", rw() === "", rw());
ck("清空后 chips 归零", poolChips().length === 0);

/* ── 10.5.8b 👗 角色服装预设弹窗（v5.0.2）────────────────────────────
   用户 2026-11-02 实测：第一版把内容直接塞进 `.av3-mini`，而那个 class
   本身就是**全屏遮罩**（position:fixed; inset:0）—— 结果是没背景、
   内容摊满整屏、压在角色网格上。这组断言钉住「必须像 openCharEditor
   一样用 .card 包裹」，并且状态文案要能区分已启用 / 未启用。 */
ck("服装弹窗用 .card 包裹（严禁直接塞进 .av3-mini）",
  /box\.className = "card av3-outfit-card"/.test(jsSrc)
  && /inner\.style\.position = "static"/.test(jsSrc),
  "结构退回直塞 .av3-mini 就会摊满整屏");
ck("弹窗里有已启用 / 未启用两种状态文案",
  /● 已启用/.test(jsSrc) && /○ 未启用/.test(jsSrc));
ck("弹窗有「当前状态」摘要行", /id = "av3-outfit-state"/.test(jsSrc));
ck("角色卡有 👗 服装入口",
  [...panel.querySelectorAll(".av3-card .edit")]
    .some((b) => b.id && b.id.startsWith("av3-outfitbtn-")),
  "没有找到 av3-outfitbtn-*");

/* ── v5.0.5：服装预设「取消启用」（用户 2026-11-02 截图反馈）────────
   反馈原文：「角色服装预设界面只有启用服装没有取消启用，请增加取消启用
   服装预设交互按钮」。根因：启用是 radio，radio 天然**只能换不能空** ——
   只有一套预设时用户根本没有退路（只能删掉这套，或去手改 JSON）。 */
{
  const tgtTag = cards()[0].dataset.tag;
  outfitsStub(tgtTag);            // 预置：该角色两套，默认启用「常服」
  const doc = dom.window.document;
  cards()[0].querySelector('[id^="av3-outfitbtn-"]').click();
  for (let i = 0; i < 100 && !doc.querySelector("#av3-outfit-disable"); i++) await sleep(10);
  const od = doc.querySelector("#av3-outfit-dialog");
  ck("服装弹窗打开", !!od);
  ck("已启用时底部出现「✕ 取消启用」按钮", !!doc.querySelector("#av3-outfit-disable"));
  ck("状态行提示可点「取消启用」",
    /取消启用/.test((doc.querySelector("#av3-outfit-state") || {}).textContent || ""),
    (doc.querySelector("#av3-outfit-state") || {}).textContent);
  ck("已启用那一行的圆点 tooltip 写明「再点一次可取消」",
    /取消启用/.test((doc.querySelector('[id^="av3-outfit-radio-"]') || {}).title || ""),
    (doc.querySelector('[id^="av3-outfit-radio-"]') || {}).title);

  doc.querySelector("#av3-outfit-disable").click();
  for (let i = 0; i < 100 && outfitsStub(tgtTag).enabled !== ""; i++) await sleep(10);
  ck("点「取消启用」→ 后端收到空 id（停用，不是删除）",
    outfitsStub(tgtTag).enabled === "", outfitsStub(tgtTag).enabled);
  for (let i = 0; i < 100 && doc.querySelector("#av3-outfit-disable"); i++) await sleep(10);
  ck("取消启用后按钮消失（未启用时不显示，避免空操作）",
    !doc.querySelector("#av3-outfit-disable"));
  ck("状态行变回「未启用」",
    /未启用/.test((doc.querySelector("#av3-outfit-state") || {}).textContent || ""),
    (doc.querySelector("#av3-outfit-state") || {}).textContent);
  ck("预设本身没被删（两套都还在，随时可再启用）",
    (outfitsStub(tgtTag).list || []).length === 2);
  // 收尾：弹窗是全屏遮罩，留着会挡住后续断言的点击
  const mk = od && (od.closest(".av3-mask") || od.parentElement);
  if (mk) mk.remove();
}

/* ── 10.5.9 🚫 不指定角色（role_pick · v4.1.0）────────────────────── */
const rpOf = () => node.widgets.find((w) => w.name === "role_pick");
ck("节点有 role_pick 控件", !!rpOf());
const rpBox0 = panel.querySelector("#av3-role-pick-off");
ck("角色库页有「不指定角色」复选框", !!rpBox0);
ck("默认不勾选 = 照常抽角色", !!rpBox0 && rpBox0.checked === false);

// 先点一位角色：验证「让路，但不销毁」
rowOf(workLabel(pickWork)).click();
const tRp = cards()[0].dataset.tag;
cardOf(tRp).click();
ck("先选 1 位角色（准备验证让路）", roleWidget() === tRp, roleWidget());

const toggleNoRole = (on) => {
  const cb = panel.querySelector("#av3-role-pick-off");   // 每次重渲后是**新**元素
  cb.checked = on;
  cb.dispatchEvent(new dom.window.Event("change"));
};
toggleNoRole(true);
ck("勾选 → 写 role_pick=false", rpOf().value === false, String(rpOf().value));
ck("勾选后已选角色**没被清掉**（只是本次不生效）",
  roleWidget() === tRp, roleWidget());
ck("开关行写明「本次不出角色、已选原样留着」",
  (panel.querySelector(".av3-rolemode")?.textContent || "").includes("原样留着"),
  panel.querySelector(".av3-rolemode")?.textContent || "");
ck("chips 区提示「本次不生效」",
  [...panel.querySelectorAll(".av3-chips")]
    .some((e) => e.textContent.includes("本次不生效")),
  panel.querySelector(".av3-chips").textContent);

// 预演必须与实跑同参（老坑：面板抽的和实跑的不一样）
const nRollRp = CALLS.filter((c) => c.url.endsWith("/roll")).length;
rollBtn.click();
for (let i = 0; i < 80
     && CALLS.filter((c) => c.url.endsWith("/roll")).length === nRollRp; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const sentRp = JSON.parse(
  CALLS.filter((c) => c.url.endsWith("/roll")).pop().opts.body);
ck("预演请求带上 role_pick=false", sentRp.role_pick === false,
  String(sentRp.role_pick));

// 取消勾选 → 一切恢复，已选角色原样还在
toggleNoRole(false);
ck("取消勾选 → 写回 role_pick=true", rpOf().value === true,
  String(rpOf().value));
ck("取消勾选后已选角色原样还在", roleWidget() === tRp, roleWidget());
[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("清除已选角色")).click();

/* ── 10.5b 多选批量删除（v3.18.6，内置角色也能删）──────────────── */
const selRaw = () => String(node.widgets.find((w) => w.name === "selected_roles").value || "");
const selBefore = selRaw();
const multiBtn = [...panel.querySelectorAll(".av3-btn")]
  .find((b) => (b.textContent || "").includes("多选"));
ck("角色库页有「☑ 多选」按钮", !!multiBtn, multiBtn?.textContent || "");
multiBtn.click();
await sleep(40);
ck("多选模式出现「删除所选」按钮", !!panel.querySelector("#av3-chars-delall"),
  panel.querySelector("#av3-chars-delall")?.textContent || "");
ck("未勾选时删除按钮禁用",
  panel.querySelector("#av3-chars-delall").disabled === true);
const nBeforeDel = cards().length;
const pickTags = [cards()[0].dataset.tag, cards()[1].dataset.tag];
cards()[0].click();
cards()[1].click();
await sleep(50);
ck("勾选不写入 selected_roles（删角色不该顺手改提示词）",
  selRaw() === selBefore, selRaw());
ck("勾选 2 个后按钮计数为 2",
  /（2）/.test(panel.querySelector("#av3-chars-delall").textContent),
  panel.querySelector("#av3-chars-delall").textContent);
ck("被勾选的卡片带蓝色对勾",
  panel.querySelectorAll(".av3-card .pick").length === 2,
  String(panel.querySelectorAll(".av3-card .pick").length));
panel.querySelector("#av3-chars-delall").click();       // 第一步：进入确认态
ck("第一步只进入确认态，不删",
  /再点一次/.test(panel.querySelector("#av3-chars-delall").textContent),
  panel.querySelector("#av3-chars-delall").textContent);
ck("确认态下卡片数未变", cards().length === nBeforeDel, String(cards().length));
panel.querySelector("#av3-chars-delall").click();       // 第二步：真删
await sleep(80);
// ⚠️ 不能断言"卡片数减 2"：角色库默认只渲染前 96 张（ui.charLimit），
//    总数远大于 96 时删两个仍然是 96 张。改成断言"被删的确实不在了"+"状态条报数"。
ck("批量删除后状态条报告删除数量",
  /已删除 2 个/.test(panel.querySelector("#av3-status")?.textContent || ""),
  panel.querySelector("#av3-status")?.textContent || "");
ck("被删的角色确实消失了",
  pickTags.every((t) => !cards().some((c) => c.dataset.tag === t)),
  pickTags.join("/"));
ck("删除后多选自动关闭", !panel.querySelector("#av3-chars-delall"));
ck("删除后 selected_roles 仍未被改动", selRaw() === selBefore, selRaw());

/* ── 10.6 收藏（⭐）────────────────────────────────────────────── */
const starOf = (tag) => cards().find((c) => c.dataset.tag === tag)
  ?.querySelector(".fav");
const favRow = () => workRows()[1];
const favN = () => Number(favRow().querySelector(".c").textContent);

rowOf("全部作品").click();
const ftag = cards()[0].dataset.tag;
ck("卡片右上角有收藏星（默认 ☆）", starOf(ftag)?.textContent === "☆",
  starOf(ftag)?.textContent || "（没有 .fav 元素）");
ck("人气徽标不再占右上角（改到名字右边）",
  !!cards()[0].querySelector(".nmRow .pp"), "");
ck("还没有收藏时「我的收藏」行置灰、计数为 0",
  favRow().classList.contains("empty") && favN() === 0, String(favN()));

const roleBefore = roleWidget();
starOf(ftag).click();
for (let i = 0; i < 60 && !starOf(ftag).classList.contains("on"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("点 ☆ → 变 ★ 且带 on 类",
  starOf(ftag).textContent === "★" && starOf(ftag).classList.contains("on"),
  starOf(ftag).textContent);
ck("收藏计数实时更新", favN() === 1, String(favN()));
ck("收藏不会顺带把角色选中（stopPropagation 生效）",
  roleWidget() === roleBefore, `${roleWidget()} vs ${roleBefore}`);
ck("有收藏后该行不再置灰", !favRow().classList.contains("empty"));

rowOf("⭐ 我的收藏").click();
ck("点「我的收藏」→ 只显示已收藏的角色", cards().length === 1,
  String(cards().length));
ck("收藏视图里就是刚收藏的那位", cards()[0]?.dataset.tag === ftag,
  cards()[0]?.dataset.tag || "");
ck("收藏视图里星标仍是 ★（列表与收藏视图状态一致）",
  starOf(ftag)?.classList.contains("on"));

starOf(ftag).click();
for (let i = 0; i < 60 && favN() !== 0; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("取消收藏后计数归零", favN() === 0, String(favN()));
ck("取消后该行重新置灰", favRow().classList.contains("empty"));
ck("收藏为空时给出明确空状态",
  (panel.querySelector(".av3-grid .av3-empty")?.textContent || "")
    .includes("还没有收藏"),
  panel.querySelector(".av3-grid .av3-empty")?.textContent?.slice(0, 24) || "");
rowOf("全部作品").click();

// 切回词库页
tabBtns[0].click();
ck("切回词库页恢复权重树", panel.querySelectorAll(".av3-sec").length > 0
  && panel.querySelectorAll(".av3-card").length === 0);

/* ── 10.7 预设 ─────────────────────────────────────────────────── */
const preBox = () => [...panel.querySelectorAll(".av3-box")]
  .find((b) => (b.querySelector("h4") || {}).textContent === "预设");
const preSel = () => preBox().querySelector("select");
const preNameIn = () => preBox().querySelector("input[type=text]");
const preBtn = (t) => [...preBox().querySelectorAll("button")]
  .find((b) => b.textContent.includes(t));
const lvBtn = (t) => [...panel.querySelectorAll(".av3-lv button")]
  .find((b) => b.textContent.includes(t));

ck("设置区下方有「预设」小节", !!preBox());
ck("没有预设时下拉禁用并提示「还没有预设」",
  preSel().disabled
  && (preSel().options[0].textContent || "").includes("还没有预设"),
  preSel().options[0]?.textContent || "");

// 先改出一套配置：档位 → 过激，第一个字段权重 → 2.5
lvBtn("过激").click();
const wInput = panel.querySelector(".av3-row input.av3-w");
const wKey = wInput.closest(".av3-row").dataset.key;
wInput.value = "2.5";
wInput.dispatchEvent(new dom.window.Event("input"));

preNameIn().value = "我的常用";
preNameIn().dispatchEvent(new dom.window.Event("input"));
preBtn("保存当前配置").click();
for (let i = 0; i < 60 && preSel().disabled; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("保存后下拉里出现该预设",
  [...preSel().options].some((o) => o.value === "我的常用"),
  [...preSel().options].map((o) => o.value).join("/"));
ck("下拉文案带摘要（看得出存了什么）",
  (preSel().options[0].textContent || "").includes("过激"),
  preSel().options[0]?.textContent || "");
ck("保存后清空名称输入框", preNameIn().value === "", preNameIn().value);

// 把配置改走，再一键应用回来
lvBtn("全年龄").click();
const wInput2 = panel.querySelector(".av3-row input.av3-w");
wInput2.value = "0.5";
wInput2.dispatchEvent(new dom.window.Event("input"));
ck("改走之后档位确实变了",
  node.widgets.find((w) => w.name === "nsfw_level").value === "sfw",
  String(node.widgets.find((w) => w.name === "nsfw_level").value));

preBtn("📥 应用").click();
for (let i = 0; i < 60 && preSel().disabled; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("应用预设 → 档位恢复",
  node.widgets.find((w) => w.name === "nsfw_level").value === "hardcore",
  String(node.widgets.find((w) => w.name === "nsfw_level").value));
let wj2 = {};
try {
  wj2 = JSON.parse(node.widgets.find((w) => w.name === "weights_json").value || "{}");
} catch (e) { /* 留空，下面断言会红 */ }
ck("应用预设 → 权重恢复", Number(wj2.fields?.[wKey]) === 2.5,
  JSON.stringify(wj2.fields || {}));
ck("应用预设 → 档位按钮的选中态也跟着变",
  lvBtn("过激").classList.contains("on"));

// 删除（两步确认，不用 window.confirm —— jsdom 里没实现）
preBtn("删除").click();
ck("删除是两步确认（第一次点只改文案）", !preBtn("删除"),
  preBtn("删除")?.textContent || "");
preBtn("再点一次").click();
for (let i = 0; i < 60 && !preSel().disabled; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
// ⚠️ 额外等一拍：删除是写操作，要确认**在途的 GET 不会把它变回来**
//    （loadPresets 的响应可能更晚到，带的是删除前的快照）。
//    没有 presetsSeq 防作废时，这条会红。
await new Promise((r) => setTimeout(r, 120));
ck("删除后回到「还没有预设」状态", preSel().disabled,
  preSel().options[0]?.textContent || "");
ck("删除后下拉里不残留旧条目",
  ![...preSel().options].some((o) => o.value === "我的常用"),
  [...preSel().options].map((o) => o.value).join("/"));

/* ── 10.8 免责声明 ─────────────────────────────────────────────── */
const discEntry = [...panel.querySelectorAll(".av3-head button")]
  .find((b) => b.textContent.includes("免责声明"));
ck("面板头部有免责声明入口", !!discEntry);
discEntry.click();
const disc = [...document.body.querySelectorAll(".av3-disc")].pop();
ck("点开后弹出免责声明", !!disc);
const dTxt = disc ? disc.textContent : "";
ck("含开头说明（开源 / 本机生成 / 不联网）",
  dTxt.includes("本插件为开源插件")
  && dTxt.includes("生成过程全部在你本机完成，不联网、不上传任何内容"),
  dTxt.slice(0, 36));
ck("含年龄硬约束", dTxt.includes("十八岁以下禁止使用")
  && dTxt.includes("18 岁以上虚构人物"));
ck("含插件作者署名", dTxt.includes("插件作者：灵冻不冻"));
ck("含源脚本作者署名（飞蓬）",
  dTxt.includes("ANIMA提示词生成器.html") && dTxt.includes("飞蓬"));
ck("含全部 5 条条款",
  ["①", "②", "③", "④", "⑤"].every((n) => dTxt.includes(n)),
  ["①", "②", "③", "④", "⑤"].filter((n) => !dTxt.includes(n)).join(""));
disc.querySelector(".ft button").click();
ck("点「我已阅读」关闭弹窗", !document.body.querySelector(".av3-disc"));

/* ── 10.9 胸部大小等级（v3.17.0，取代巨乳开关；面板最底部）──────── */
const footEl = panel.querySelector(".av3-foot");
ck("面板最底部有独立底部条", !!footEl, panel.lastElementChild?.className);
/* v3.19.0：底部条变成两条（胸部大小 + 服装风格），
   断言改成"最后一个子元素仍是底部条"，而不是"必须是胸部那条"。 */
ck("底部条确实是面板的最后一个子元素（不会被两列滚走）",
  !!panel.lastElementChild?.classList?.contains("av3-foot"),
  panel.lastElementChild?.className);
const footEls = panel.querySelectorAll(".av3-foot");
ck("底部条共两条（胸部大小 + 服装风格）", footEls.length === 2, String(footEls.length));
const osFoot = footEls[1];
const osSel2 = osFoot.querySelector("select");
ck("第二条底部条里有服装风格下拉", !!osSel2);
ck("标签写着「服装风格」", osFoot.textContent.includes("服装风格"));
ck("十八个选项（随机/不限 + 十六个风格）",
  osSel2.options.length === 18 &&
  [...osSel2.options].map((o) => o.value).join("|")
    === "随机|不限|学院|职场|传统|运动|日常|居家|夜店|奇幻|泳装|哥特|洛丽塔|朋克|战术风|暗黑少女|辣妹|女仆",
  [...osSel2.options].map((o) => o.value).join("|"));
{ /* 改动下拉必须真的写进节点控件 */
  const osWidget = () => node.widgets.find((w) => w.name === "outfit_style").value;
  osSel2.value = "夜店";
  osSel2.dispatchEvent(new dom.window.Event("change"));
  ck("切换服装风格写进节点控件 outfit_style", osWidget() === "夜店", String(osWidget()));
  osSel2.value = "随机";
  osSel2.dispatchEvent(new dom.window.Event("change"));
}
const bsSel = footEl.querySelector("select");
ck("底部条里是胸部大小下拉", !!bsSel);
ck("标签写着「胸部大小」", footEl.textContent.includes("胸部大小"));
ck("七个选项（不指定 + 六档）",
  bsSel.options.length === 7 &&
  [...bsSel.options].map((o) => o.value).join("|")
    === "不指定|平胸|小胸|中等|大胸|巨乳|爆乳",
  [...bsSel.options].map((o) => o.value).join("|"));
ck("默认「不指定」", bsSel.value === "不指定", String(bsSel.value));
ck("带说明文字（写明覆盖什么）",
  footEl.textContent.includes("覆盖外貌对齐与权重"), footEl.textContent.slice(0, 60));

bsSel.value = "巨乳";
bsSel.dispatchEvent(new dom.window.Event("change"));
const bsWidget = () => node.widgets.find((w) => w.name === "breast_size").value;
ck("选择后写回节点控件 breast_size", bsWidget() === "巨乳", String(bsWidget()));

// 抽取请求要带上它 —— 否则面板预演与真机结果会不一致
CALLS.length = 0;
[...panel.querySelectorAll(".av3-btn")].find((b) => b.textContent.includes("全部随机"))
  .click();
for (let i = 0; i < 60 && !CALLS.some((c) => c.url.endsWith("/roll")); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const bbRollCall = CALLS.filter((c) => c.url.endsWith("/roll")).pop();
let bbRollBody = {};
try { bbRollBody = JSON.parse(bbRollCall.opts.body); } catch (e) { /* 留空，断言会红 */ }
ck("抽取请求带上 breast_size = 巨乳", bbRollBody.breast_size === "巨乳",
  JSON.stringify(bbRollBody.breast_size));
ck("旧字段 big_breasts 不再出现在预演请求里", !("big_breasts" in bbRollBody),
  JSON.stringify(Object.keys(bbRollBody)));

bsSel.value = "不指定";
bsSel.dispatchEvent(new dom.window.Event("change"));
ck("改回「不指定」也写回", bsWidget() === "不指定", String(bsWidget()));

/* 10.9 之一：老工作流兼容 —— 旧开关开着 → 打开面板时一次性平移为「巨乳」 */
{
  const oldNode = makeNode();
  oldNode.widgets.find((w) => w.name === "big_breasts").value = true;
  nodeType.prototype.onNodeCreated.call(oldNode);
  // 与主面板同一入口：点「打开随机面板」按钮（onNodeCreated 只挂按钮不开面板）
  oldNode.widgets.find((w) => w.name.includes("打开随机面板")).callback();
  // 等**第二个** .av3-panel 出现（第一个是主面板，别拿错）
  let oldSel = null;
  for (let i = 0; i < 100 && !oldSel; i++) {
    await new Promise((r) => setTimeout(r, 10));
    const panels = document.body.querySelectorAll(".av3-panel");
    if (panels.length >= 2) oldSel = panels[panels.length - 1].querySelector(".av3-foot select");
  }
  ck("旧巨乳开关=True 的老工作流，打开面板即平移为「巨乳」",
     !!oldSel && oldSel.value === "巨乳", oldSel ? String(oldSel.value) : "无面板");
  // 平移是一次性的：旧开关应已退役
  ck("平移后旧开关退役（big_breasts=false）",
     oldNode.widgets.find((w) => w.name === "big_breasts").value === false,
     String(oldNode.widgets.find((w) => w.name === "big_breasts").value));
}

/* ── 10.10 角色编辑与自定义角色（v3.4.0 · v3.5.0 加特征标签）───── */
/* sleep 已提到文件前部（10.5b 批量删除要用） */
const editor = () => document.body.querySelector(".av3-editor");
const edInputs = () => [...editor().querySelectorAll(".av3-efld input")];
const edBtn = (t) => [...editor().querySelectorAll(".ft button")]
  .find((b) => b.textContent.includes(t));
const cardByTag = (tag) => cards().find((c) => c.dataset.tag === tag);
const PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8Dw"
  + "HwAFBQIAX8jx0gAAAABJRU5ErkJggg==";

/* ⚠️ v3.5.0 起，保存按钮在"特征标签读取完"之前是禁用的（防止把 tags 当空发出
   而静默清掉覆盖）。所以每次打开弹窗都要等它放行，否则点击是空操作。 */
async function waitEdReady() {
  for (let i = 0; i < 100; i++) {
    const b = edBtn("保存") || edBtn("添加角色");
    if (b && !b.disabled) return true;
    await sleep(10);
  }
  return false;
}
// 标签行的输入框（不在 .av3-efld 里，单独查）
const tagRowInputs = () => [...editor().querySelectorAll(".av3-tagrow input")];
const tagTexts = () => tagRowInputs().map((i) => i.value);

tabBtns[1].click();
for (let i = 0; i < 100 && !panel.querySelector(".av3-card"); i++) await sleep(10);

ck("角色库顶部有「添加角色」入口",
  [...panel.querySelectorAll(".av3-charbar .av3-btn")]
    .some((b) => b.textContent.includes("添加角色")));
ck("每张卡片都有编辑按钮 ✎", cards().every((c) => !!c.querySelector(".edit")));
ck("内置角色不带「自定义」角标",
  cards().every((c) => !c.querySelector(".av3-badge")));

/* ── 编辑一个内置角色 ── */
const editTag = cards()[0].dataset.tag;
const origRec = CHAR_FIX.characters.find((c) => c.tag === editTag);
cards()[0].querySelector(".edit").click();
ck("点 ✎ 打开角色编辑弹窗", !!editor());
ck("弹窗标题带角色名", (editor().querySelector(".hd")?.textContent || "")
  .includes("编辑角色"));
ck("弹窗里标明角色 tag（tag 本身不可改）",
  (editor().querySelector(".hd .tag")?.textContent || "") === editTag,
  editor().querySelector(".hd .tag")?.textContent || "");
ck("编辑弹窗有 3 个字段行 + 图片区",
  editor().querySelectorAll(".av3-efld").length === 3
  && !!editor().querySelector(".av3-ethumb"),
  String(editor().querySelectorAll(".av3-efld").length));
ck("内置角色同时给「↺ 恢复默认」与「🗑 删除角色」（v3.18.6 起内置可删）",
  !!edBtn("恢复默认") && !!edBtn("删除角色"),
  edBtn("恢复默认")?.textContent + " / " + edBtn("删除角色")?.textContent);
// ⚠️ 基准取**内置原文**（RAW_BY_TAG），不取夹具 —— 夹具是从 characters_payload()
//    生成的，万一接口漏发 trigger，两边一起 undefined 会让这条断言**虚假通过**
//    （v3.4.0 就是这么漏过去的，v3.5.0 才补上 trigger）。
ck("角色标签预填当前 trigger（对照内置原文）",
  edInputs()[2].value === (RAW_BY_TAG.get(editTag)?.trigger || "")
  && !!edInputs()[2].value,
  `${JSON.stringify(edInputs()[2].value)} vs ${JSON.stringify(RAW_BY_TAG.get(editTag)?.trigger)}`);
ck("作品输入框带候选作品 datalist",
  !!editor().querySelector("input[list=av3-worklist]")
  && editor().querySelectorAll("#av3-worklist option").length > 0);
ck("详情没读出来之前保存按钮是禁用的",
  (() => {
    const b = edBtn("保存");
    return b ? b.disabled === true : false;
  })(), "（或详情已即时返回，见下一条）");
ck("等一拍后保存放行（特征标签已就绪）", await waitEdReady());

// 空标签必须被拦在弹窗里（后端也会拒，但前端不该让用户白发一次请求）
edInputs()[2].value = "";
edBtn("保存").click();
await sleep(30);
ck("空角色标签被拦（弹窗不关、给出错误）",
  !!editor() && (editor().querySelector(".av3-eerr")?.textContent || "")
    .includes("不能为空"),
  editor()?.querySelector(".av3-eerr")?.textContent || "（弹窗被关了）");

CALLS.length = 0;
edInputs()[0].value = "冒烟改过";
edInputs()[2].value = "edited via smoke, test series";
edBtn("保存").click();
for (let i = 0; i < 80 && editor(); i++) await sleep(10);
ck("保存后弹窗关闭", !editor());
ck("保存走了 /characters/save",
  CALLS.some((c) => c.url.endsWith("/characters/save")));
ck("保存后卡片中文名变成改后的值",
  cardByTag(editTag)?.querySelector(".nm")?.textContent === "冒烟改过",
  cardByTag(editTag)?.querySelector(".nm")?.textContent || "(卡片没了)");
ck("改动不会污染内置夹具（原记录仍是旧中文名）",
  CHAR_FIX.characters.find((c) => c.tag === editTag)?.zh !== "冒烟改过");

/* ── 添加自定义角色 ── */
[...panel.querySelectorAll(".av3-charbar .av3-btn")]
  .find((b) => b.textContent.includes("添加角色")).click();
ck("点「添加角色」打开同一个弹窗",
  !!editor() && (editor().querySelector(".hd")?.textContent || "").includes("添加"));
ck("新增时主按钮是「添加角色」", !!edBtn("添加角色"));
ck("新增时没有删除 / 恢复默认（还没存呢）",
  !edBtn("删除角色") && !edBtn("恢复默认"));
ck("新增时「清除自定义图片」禁用",
  editor().querySelectorAll(".av3-ethumb .ops .row button")[1].disabled === true);

edInputs()[0].value = "冒烟新角色";
edInputs()[1].value = "smoke series";
edInputs()[2].value = "smoke custom girl, smoke series";
CALLS.length = 0;
edBtn("添加角色").click();
for (let i = 0; i < 80 && editor(); i++) await sleep(10);
ck("新增后弹窗关闭", !editor());
ck("新增走的是 create 模式",
  (() => {
    const c = CALLS.filter((x) => x.url.endsWith("/characters/save")).pop();
    try { return JSON.parse(c.opts.body).mode === "create"; } catch (e) { return false; }
  })());
const newCard = () => cards().find((c) => c.querySelector(".nm")?.textContent === "冒烟新角色");
ck("网格里出现刚加的自定义角色", !!newCard(),
  cards().map((c) => c.querySelector(".nm")?.textContent).join("/"));
ck("新增后搜索框自动筛选到新角色（不然人气 0 会沉底看不见）",
  (panel.querySelector(".av3-charbar input[type=text]")?.value || "").includes("冒烟新角色"),
  panel.querySelector(".av3-charbar input[type=text]")?.value || "");
const newTag = newCard()?.dataset.tag || "";
ck("自定义角色 tag 带 user_ 前缀", newTag.startsWith("user_"), newTag);
ck("自定义角色带「自定义」角标", !!newCard()?.querySelector(".av3-badge"));

/* ── 预览图：客户端校验 + 成功上传 ── */
newCard().querySelector(".edit").click();
await waitEdReady();
const fileIn = editor().querySelector('input[type=file]');
const setFiles = (f, name) => Object.defineProperty(fileIn, "files", {
  value: [f], configurable: true,
});
// ⚠️ jsdom 不加载图片、也不会自己触发 change，这里手工派发 —— 测的是我们
//    自己的校验与上传逻辑，不是 jsdom 的加载器。
setFiles(new dom.window.File(["x"], "a.txt", { type: "text/plain" }), "a.txt");
fileIn.dispatchEvent(new dom.window.Event("change"));
ck("非图片类型被拦", (editor().querySelector(".av3-eerr")?.textContent || "")
  .includes("只支持"), editor().querySelector(".av3-eerr")?.textContent || "");

setFiles(new dom.window.File([new Uint8Array(2 * 1024 * 1024 + 16)], "big.png",
                             { type: "image/png" }), "big.png");
fileIn.dispatchEvent(new dom.window.Event("change"));
ck("超 2MB 图片被拦", (editor().querySelector(".av3-eerr")?.textContent || "")
  .includes("过大"), editor().querySelector(".av3-eerr")?.textContent || "");

const pngBytes = Uint8Array.from(atob(PNG_1PX), (ch) => ch.charCodeAt(0));
setFiles(new dom.window.File([pngBytes], "p.png", { type: "image/png" }), "p.png");
fileIn.dispatchEvent(new dom.window.Event("change"));
for (let i = 0; i < 60; i++) {
  if ((editor()?.querySelector(".pv img")?.src || "").startsWith("data:image")) break;
  await sleep(10);
}
ck("选图后弹窗内立即预览本地图",
  (editor()?.querySelector(".pv img")?.src || "").startsWith("data:image"),
  (editor()?.querySelector(".pv img")?.src || "").slice(0, 24));

CALLS.length = 0;
edBtn("保存").click();
for (let i = 0; i < 80 && editor(); i++) await sleep(10);
const thumbCall = CALLS.filter((c) => c.url.endsWith("/characters/thumb")).pop();
ck("点保存时才上传图片（选了图先存内存）", !!thumbCall);
let thumbBody = {};
try { thumbBody = JSON.parse(thumbCall.opts.body); } catch (e) { /* 下面会红 */ }
ck("上传带 data_url 与角色 tag",
  String(thumbBody.data_url || "").startsWith("data:image") && thumbBody.tag === newTag,
  `${String(thumbBody.data_url || "").slice(0, 20)} / ${thumbBody.tag}`);

/* ── 删除自定义角色（两步确认）── */
newCard().querySelector(".edit").click();
await waitEdReady();
ck("自定义角色弹窗给「删除角色」", !!edBtn("删除角色"));
edBtn("删除角色").click();
ck("删除两步确认（第一次只改文案）", !!edBtn("再点一次"));
edBtn("再点一次").click();
for (let i = 0; i < 80 && editor(); i++) await sleep(10);
ck("删除后弹窗关闭", !editor());

const searchIn2 = panel.querySelector(".av3-charbar input[type=text]");
searchIn2.value = "";
searchIn2.dispatchEvent(new dom.window.Event("input"));
await sleep(20);
ck("删除后该角色从网格消失", !cardByTag(newTag), newTag);
ck("删除后一个「自定义」角标都不剩",
  !cards().some((c) => c.querySelector(".av3-badge")));

/* ── 恢复默认（内置）── */
const backCard = () => cardByTag(editTag);
backCard().querySelector(".edit").click();
await waitEdReady();
ck("编辑过的内置角色：中文名显示改后的值",
  edInputs()[0].value === "冒烟改过", edInputs()[0].value);
ck("编辑过的内置角色：角色标签显示改后的值",
  edInputs()[2].value === "edited via smoke, test series", edInputs()[2].value);
edBtn("恢复默认").click();
edBtn("再点一次").click();
for (let i = 0; i < 80 && editor(); i++) await sleep(10);
ck("恢复默认后弹窗关闭", !editor());
ck("恢复默认后中文名回到内置值",
  (backCard()?.querySelector(".nm")?.textContent || "") === (origRec?.zh || origRec?.name),
  `${backCard()?.querySelector(".nm")?.textContent} vs ${origRec?.zh || origRec?.name}`);
ck("恢复默认后重新打开弹窗是内置 trigger",
  (() => { backCard().querySelector(".edit").click();
    const v = edInputs()[2].value;
    edBtn("取消").click();
    return v === (origRec?.trigger || ""); })(),
  "（见上一条）");

/* ── 10.11 特征标签编辑（v3.5.0）────────────────────────────────
   挑一个"全量标签 > 8 条"的角色来测 —— 只有这样才验得出
   "编辑弹窗拿到的是全量、不是列表载荷里的前 8 条"。 */
const tagTestTag = (() => {
  const inPage = cards().map((c) => c.dataset.tag)
    .find((t) => (RAW_BY_TAG.get(t)?.tags || []).length > 8);
  if (inPage) return inPage;
  const any = [...RAW_BY_TAG.values()]
    .filter((c) => (c.tags || []).length > 8)
    .sort((a, b) => (b.pop || 0) - (a.pop || 0))[0];
  return any ? any.tag : "";
})();
const rawTags = (RAW_BY_TAG.get(tagTestTag)?.tags || []).slice();
ck("找到一个全量标签 > 8 条的角色用于本组用例",
  rawTags.length > 8, `${tagTestTag} → ${rawTags.length} 条`);

// 让它出现在网格里（可能不在首页）
const searchIn3 = panel.querySelector(".av3-charbar input[type=text]");
searchIn3.value = tagTestTag;
searchIn3.dispatchEvent(new dom.window.Event("input"));
await sleep(30);
const openEditorOn = async (tag) => {
  // 先收掉可能残留的弹窗（上一段若断言失败、没走到关闭，会留下一个旧弹窗，
  // 而 querySelector(".av3-editor") 只返回第一个 → 后续断言会读错对象）
  const stale = document.body.querySelector(".av3-editor");
  if (stale) {
    const m = stale.closest(".av3-mask");
    (m || stale).remove();
  }
  const card = cardByTag(tag);
  if (!card) return false;
  card.querySelector(".edit").click();
  const ready = await waitEdReady();
  ck("同一时刻只有一个编辑弹窗",
    document.body.querySelectorAll(".av3-editor").length === 1,
    String(document.body.querySelectorAll(".av3-editor").length));
  return ready;
};
ck("打开该角色的编辑弹窗", await openEditorOn(tagTestTag));

ck("编辑弹窗里有「特征标签」区", !!editor().querySelector(".av3-tagsec"));
ck("标签区写明生效条件（体型直接生效 / 发色瞳色要没记录）",
  (editor().querySelector(".av3-tagsec")?.textContent || "").includes("体型标签")
  && (editor().querySelector(".av3-tagsec")?.textContent || "").includes("发色瞳色标签"),
  editor().querySelector(".av3-tagsec")?.textContent?.slice(0, 40) || "");
ck("标签行数 = 该角色的**全量**标签（不是列表载荷里的前 8 条）",
  tagRowInputs().length === rawTags.length && tagRowInputs().length > 8,
  `${tagRowInputs().length} vs 全量 ${rawTags.length}`);
ck("标签行预填内置内容（顺序一致）",
  tagTexts().join("|") === rawTags.join("|"), tagTexts().join("|"));
ck("头部显示内置条数",
  (editor().querySelector(".av3-taghead .av3-meta")?.textContent || "")
    .includes(String(rawTags.length)),
  editor().querySelector(".av3-taghead .av3-meta")?.textContent || "");
ck("头部有「恢复内置标签」入口", !!editor().querySelector(".av3-taghead .av3-btn"));
ck("底部有「＋ 添加标签」与「已 N 条（上限 40）」计数",
  !!editor().querySelector(".av3-tagfoot .av3-btn")
  && (editor().querySelector(".av3-tagfoot .av3-meta")?.textContent || "")
    .includes(`已 ${rawTags.length} 条`)
  && (editor().querySelector(".av3-tagfoot .av3-meta")?.textContent || "")
    .includes("上限 40"),
  editor().querySelector(".av3-tagfoot .av3-meta")?.textContent || "");
ck("每行都有一个 × 删除按钮",
  editor().querySelectorAll(".av3-tagrow").length === tagRowInputs().length
  && editor().querySelectorAll(".av3-tagrow .av3-btn").length === tagRowInputs().length);

/* 增：＋ 添加标签 */
const n0 = tagRowInputs().length;
editor().querySelector(".av3-tagfoot .av3-btn").click();
ck("点「＋ 添加标签」新增一行", tagRowInputs().length === n0 + 1,
  String(tagRowInputs().length));
ck("新增行是空的、带占位提示",
  tagRowInputs().at(-1).value === ""
  && (tagRowInputs().at(-1).placeholder || "").includes("breasts"),
  tagRowInputs().at(-1).placeholder || "");
ck("空行不计入「已 N 条」计数",
  (editor().querySelector(".av3-tagfoot .av3-meta")?.textContent || "")
    .includes(`已 ${n0} 条`),
  editor().querySelector(".av3-tagfoot .av3-meta")?.textContent || "");

/* 改：直接改那一行 */
const typeTag = (el, v) => {
  el.value = v;
  el.dispatchEvent(new dom.window.Event("input"));   // 手打会触发 input，程序赋值不会
};
typeTag(tagRowInputs().at(-1), "large breasts");
typeTag(tagRowInputs()[0], "1girl edited");
ck("填入内容后计数 +1",
  (editor().querySelector(".av3-tagfoot .av3-meta")?.textContent || "")
    .includes(`已 ${n0 + 1} 条`),
  editor().querySelector(".av3-tagfoot .av3-meta")?.textContent || "");
CALLS.length = 0;
edBtn("保存").click();
for (let i = 0; i < 80 && editor(); i++) await sleep(10);
ck("改完标签保存成功（弹窗关闭）", !editor(),
  editor()?.querySelector(".av3-eerr")?.textContent || "");
let tagBody = {};
try {
  tagBody = JSON.parse(
    CALLS.filter((c) => c.url.endsWith("/characters/save")).pop().opts.body);
} catch (e) { /* 下条断言会红 */ }
ck("保存请求带 tags 数组（新增 + 改过的都在）",
  Array.isArray(tagBody.fields?.tags)
  && tagBody.fields.tags.includes("large breasts")
  && tagBody.fields.tags.includes("1girl edited")
  && tagBody.fields.tags.length === n0 + 1,
  JSON.stringify(tagBody.fields?.tags ?? null));

/* 回读：覆盖已生效 + 头部标注 */
await openEditorOn(tagTestTag);
ck("重新打开后标签 = 改后的列表",
  tagTexts().includes("large breasts") && tagTexts().includes("1girl edited"),
  tagTexts().join("|"));
ck("头部标注「已被修改」",
  (editor().querySelector(".av3-taghead .av3-meta")?.textContent || "").includes("已被修改"),
  editor().querySelector(".av3-taghead .av3-meta")?.textContent || "");

/* 删：点该行的 × */
const beforeDel = tagRowInputs().length;
[...editor().querySelectorAll(".av3-tagrow .av3-btn")].at(-1).click();
ck("点 × 删掉最后一行", tagRowInputs().length === beforeDel - 1,
  String(tagRowInputs().length));
ck("删掉的是最后那条「large breasts」",
  !tagTexts().includes("large breasts"), tagTexts().join("|"));

/* 恢复内置标签：先弄脏，再一键还原 */
editor().querySelector(".av3-tagfoot .av3-btn").click();
tagRowInputs().at(-1).value = "huge breasts";
ck("先弄脏：当前列表与内置不同",
  tagTexts().join("|") !== rawTags.join("|"));
editor().querySelector(".av3-taghead .av3-btn").click();
ck("「恢复内置标签」把行填回内置值",
  tagTexts().join("|") === rawTags.join("|"), tagTexts().join("|"));
CALLS.length = 0;
edBtn("保存").click();
for (let i = 0; i < 80 && editor(); i++) await sleep(10);
let tagBody2 = {};
try {
  tagBody2 = JSON.parse(
    CALLS.filter((c) => c.url.endsWith("/characters/save")).pop().opts.body);
} catch (e) { /* 下条断言会红 */ }
ck("与内置一致时发空数组（真清覆盖，而不是写一份副本）",
  Array.isArray(tagBody2.fields?.tags) && tagBody2.fields.tags.length === 0,
  JSON.stringify(tagBody2.fields?.tags ?? null));
await openEditorOn(tagTestTag);
ck("清掉覆盖后头部不再标「已被修改」",
  !(editor().querySelector(".av3-taghead .av3-meta")?.textContent || "").includes("已被修改"),
  editor().querySelector(".av3-taghead .av3-meta")?.textContent || "");

/* 校验：非法标签被后端拒绝，弹窗不关 */
typeTag(tagRowInputs()[0], "中文标签");
CALLS.length = 0;
edBtn("保存").click();
for (let i = 0; i < 60 && !CALLS.some((c) => c.url.endsWith("/characters/save")); i++) {
  await sleep(10);
}
await sleep(40);
ck("非法标签被拒（弹窗不关 + 错误显示在弹窗里）",
  !!editor() && (editor().querySelector(".av3-eerr")?.textContent || "").includes("不合法"),
  editor()?.querySelector(".av3-eerr")?.textContent || "（弹窗被关了）");
ck("被拒后保存按钮重新可用（能改回去再存）",
  (() => { const b = edBtn("保存"); return !!b && !b.disabled; })());

typeTag(tagRowInputs()[0], rawTags[0] || "1girl");
edBtn("取消").click();
ck("取消关闭标签编辑弹窗", !editor());
searchIn3.value = "";
searchIn3.dispatchEvent(new dom.window.Event("input"));
await sleep(20);

/* ── 10.12 场景搭配（v3.9.0）──────────────────────────────────── */
tabBtns.find((b) => b.textContent.includes("场景搭配")).click();
for (let i = 0; i < 100 && !panel.querySelector(".av3-sec[data-scene]"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("切到场景页后请求了 /scenes", CALLS.some((c) => c.url.endsWith("/scenes")));

const sceneCards = () => [...panel.querySelectorAll(".av3-sec[data-scene]")];
// v3.43.0：场景页改成**首屏 60 条懒加载**（190 张卡一次建 DOM 会卡）。
// 所以"全量"类断言必须先点「显示全部」把 rest 放出来。
const loadAllScenes = async () => {
  for (let i = 0; i < 20; i++) {
    const b = panel.querySelector("#av3-scene-moreall");
    if (!b) return;
    b.click();
    await new Promise((r) => setTimeout(r, 20));
  }
};
ck("首屏只渲染 60 条（懒加载生效）",
  sceneCards().length === 60 && !!panel.querySelector("#av3-scene-more"),
  `${sceneCards().length} 张 / more=${!!panel.querySelector("#av3-scene-more")}`);
await loadAllScenes();

// v3.18.0：这一页只列**场景搭配**；特写搭配在第五个页签，不混排。
// v5.0.21：艺术构图（manual_only）也已独立成页签，本页不再收录 → 判据回到 `!manual`。
// v5.1.0：姿势已独立成页签 —— 场景搭配页只剩 sfw + nsfw
const nonManualCount = SCENE_FIX.scenes
  .filter((s) => !s.manual && s.group !== "pose").length;
ck("场景卡数量 == 库里的非特写场景数",
  sceneCards().length === nonManualCount,
  `${sceneCards().length} vs ${nonManualCount}`);
ck("场景搭配页不含任何艺术构图卡（它们在自己的页签里）",
  !sceneCards().some((c) => (c.dataset.scene || "").startsWith("sc_art_")),
  sceneCards().map((c) => c.dataset.scene).filter((i) => i.startsWith("sc_art_")).length);
/* ── v5.1.0：姿势升为一级页签，场景页**不再**有分组筛选条 ──────────
   用户 2026-11-05：「场景搭配内的姿势场景 UI 改为与角色库、场景搭配同级，
   方便选择。」连带两条：
     ① 场景搭配页那条「全部 / 🧎 姿势场景」筛选条整条撤掉；
     ② 姿势卡一条都不再出现在这一页（它们全在「🧘 姿势库」页签）。 */
ck("场景页不再有语义分组筛选条（姿势已升页签）",
  !panel.querySelector("#av3-scene-groups"));
ck("场景搭配页不含任何姿势卡",
  !sceneCards().some((c) => (c.dataset.scene || "").startsWith("sc_pose_")),
  sceneCards().map((c) => c.dataset.scene)
    .filter((i2) => i2.startsWith("sc_pose_")).length);
ck("场景搭配页不渲染背景分类筛选条（那是艺术构图页专有）",
  !panel.querySelector("#av3-scene-bgs"));

ck("场景页一张特写卡都没有",
  !sceneCards().some((c) => (c.dataset.scene || "").startsWith("sc_near_")),
  sceneCards().map((c) => c.dataset.scene).filter((i) => i.startsWith("sc_near_")).join("/"));

/* ── v5.0.21：艺术构图升为一级页签（与场景搭配 / 特写搭配同级）─────────
   用户 2026-11-04 20:13：「艺术构图与场景搭配、特写搭配同级，方便选择」。
   连带两条：① 分组条在这一页整条不渲染（页签就是分组，再摆一排是重复入口）；
            ② 背景分类筛选条要出现，且能真的筛出对应批次。 */
tabBtns[5].click();
await new Promise((r) => setTimeout(r, 60));
ck("艺术构图页签下不再渲染分组条（页签本身即分组）",
  !panel.querySelector("#av3-scene-groups"));
const artCount = SCENE_FIX.scenes.filter((s) => s.group === "art").length;
await loadAllScenes();
ck("艺术构图页签只列 group=art 的场景",
  sceneCards().length === artCount
  && sceneCards().every((c) => (c.dataset.scene || "").startsWith("sc_art_")),
  `${sceneCards().length} vs ${artCount}`);
/* v6.6.1：艺术构图也是全 manual_only（103 条，`scene_options()` 里 0 条），
   原来这一页摆着「🎬 随机抽取场景搭配」开关 —— 它是**节点级**的，勾上之后
   实跑抽的是普通场景，永远抽不到 art。改成和姿势库 / 武器场景一样的勾选随机。 */
ck("艺术构图页不显示「随机抽取场景搭配」开关（全 manual_only，抽不到）",
  !panel.querySelector("#av3-scene-pick"));
ck("艺术构图页改为「勾选后随机」工具条",
  !!panel.querySelector("#av3-pickbar-art"),
  [...panel.querySelectorAll("[id^='av3-pickbar-']")].map((e) => e.id).join("/"));
ck("艺术构图卡都带勾选框",
  sceneCards().length > 0
  && sceneCards().every((c) => !!c.querySelector("input.av3-pick")));
/* v6.7.0：勾选池「每次运行自动随机」—— 三组互斥，同时只能开一组 */
const wPool = (k) => String((node.widgets.find((w) => w.name === k) || {}).value || "");
const artAutoCb = () => panel.querySelector("#av3-pick-auto-art");
ck("艺术构图工具条有「每次运行自动随机」复选框", !!artAutoCb());
ck("默认关闭（pick_pool_mode 为空）",
  wPool("pick_pool_mode") === "", wPool("pick_pool_mode"));
{ // 勾一条 + 打开自动随机 → 写 mode / ids
  const c0 = sceneCards()[0].querySelector("input.av3-pick");
  c0.checked = true;
  c0.dispatchEvent(new dom.window.Event("change"));
  await sleep(30);
  artAutoCb().checked = true;
  artAutoCb().dispatchEvent(new dom.window.Event("change"));
  await sleep(60);
  ck("勾上后 pick_pool_mode = art", wPool("pick_pool_mode") === "art",
    wPool("pick_pool_mode"));
  ck("pick_pool_ids 与勾选集同步", wPool("pick_pool_ids").length > 0,
    wPool("pick_pool_ids"));
  ck("勾选变化会跟着更新 ids（再勾一条 → ids 变长）", await (async () => {
    const before = wPool("pick_pool_ids").split(",").filter(Boolean).length;
    const c1 = sceneCards()[1].querySelector("input.av3-pick");
    c1.checked = true;
    c1.dispatchEvent(new dom.window.Event("change"));
    await sleep(30);
    const after = wPool("pick_pool_ids").split(",").filter(Boolean).length;
    return after === before + 1;
  })(), wPool("pick_pool_ids"));
}
const bgbar = panel.querySelector("#av3-scene-bgs");
ck("艺术构图页签有背景分类筛选条（v5.0.20）", !!bgbar,
  bgbar ? bgbar.textContent.slice(0, 48) : "无");
const gardenCount = SCENE_FIX.scenes.filter((s) => s.bg === "花园俯拍").length;
const gBtn = bgbar
  && [...bgbar.querySelectorAll("button")].find((b) => b.textContent.includes("花园俯拍"));
ck("背景筛选条里有「花园俯拍」按钮", !!gBtn && gardenCount > 0,
  `${!!gBtn} / ${gardenCount}`);
if (gBtn) gBtn.click();
await new Promise((r) => setTimeout(r, 40));
await loadAllScenes();
ck("点「花园俯拍」只留那一批",
  sceneCards().length === gardenCount, `${sceneCards().length} vs ${gardenCount}`);
// ⚠️ 必须把背景筛选清掉再走：sceneBg 是**跨页签保留**的状态，
//    留着它会让下面「搜索命中 N 条」的基准全错（实测踩过）。
bgbar.querySelector("button[data-bg='']")?.click();
await new Promise((r) => setTimeout(r, 30));
tabBtns[3].click();              // 回场景页，后面的用例继续在这页跑
await new Promise((r) => setTimeout(r, 60));
await loadAllScenes();

/* ── 10.12b ⚔️ 武器场景（v5.10.0 → v5.11.0 改为「作品 → 武器」两级）──
   守六件事：① 只列 group=weapon（80 条，全部 sc_wp_ 前缀）；
            ② 第一级作品条（全部 + 通用 + 8 个 IP = 10 个按钮）；
            ③ 「全部作品」时第二级按 8 类武器类型（9 个按钮）；
            ④ 不显示「随机抽取场景搭配」开关（全 manual_only）；
            ⑤ 点 IP 作品 → 只剩 4 条，第二级换成具体武器名、卡片带《作品名》；
            ⑥ 点具体武器 → 只剩 1 条。 */
tabBtns[6].click();
await sleep(80);
const weaponTotal = SCENE_FIX.scenes.filter((s) => s.group === "weapon").length;
await loadAllScenes();
ck("武器场景页只列 group=weapon 的 80 条",
  sceneCards().length === weaponTotal && weaponTotal === 80,
  `${sceneCards().length} vs ${weaponTotal}`);
ck("武器场景卡片 id 全是 sc_wp_ 前缀",
  sceneCards().every((c) => (c.dataset.scene || "").startsWith("sc_wp_")));
const worksBar = panel.querySelector("#av3-weapon-works");
ck("第一级作品分类条（全部 + 通用 + 8 个 IP = 10 个）",
  !!worksBar && worksBar.querySelectorAll("button").length === 10,
  String(worksBar ? worksBar.querySelectorAll("button").length : "无"));
let wbar = panel.querySelector("#av3-weapon-cats");
ck("「全部作品」时第二级按 8 类武器类型（9 个按钮）",
  !!wbar && wbar.querySelectorAll("button").length === 9,
  String(wbar ? wbar.querySelectorAll("button").length : "无"));
ck("武器场景页不显示「随机抽取场景搭配」开关（全 manual_only）",
  !panel.querySelector("#av3-scene-pick"));
const bladeCount = SCENE_FIX.scenes.filter(
  (s) => s.group === "weapon" && s.theme === "刀剑").length;
const wBlade = wbar && [...wbar.querySelectorAll("button")]
  .find((b) => b.dataset.cat === "刀剑");
if (wBlade) wBlade.click();
await sleep(60);
await loadAllScenes();
ck("「全部作品」下点「刀剑」按武器类型筛（数量动态）",
  sceneCards().length === bladeCount,
  `${sceneCards().length} vs ${bladeCount}`);
panel.querySelector("#av3-weapon-cats button[data-cat='all']")?.click();
await sleep(30);

const evaBtn = panel.querySelector("#av3-weapon-works button[data-work='neon_genesis_evangelion']");
if (evaBtn) evaBtn.click();
await sleep(60);
await loadAllScenes();
const evaCount = SCENE_FIX.scenes.filter(
  (s) => s.group === "weapon" && s.work === "neon_genesis_evangelion").length;
ck("点「新世纪福音战士」只剩该作品 4 条",
  sceneCards().length === evaCount && evaCount === 4,
  `${sceneCards().length} vs ${evaCount}`);
wbar = panel.querySelector("#av3-weapon-cats");
ck("选中 IP 作品后第二级换成具体武器名（朗基努斯之枪）",
  !!wbar && [...wbar.querySelectorAll("button")]
    .some((b) => b.dataset.cat === "朗基努斯之枪"),
  wbar ? [...wbar.querySelectorAll("button")].map((b) => b.dataset.cat).join("/") : "无");
ck("IP 武器卡片标题带《新世纪福音战士》",
  [...panel.querySelectorAll(".av3-sec[data-scene] .t")]
    .some((e) => e.textContent.includes("《新世纪福音战士》")));
wbar?.querySelector("button[data-cat='朗基努斯之枪']")?.click();
await sleep(60);
await loadAllScenes();
ck("点「朗基努斯之枪」只剩那 1 条",
  sceneCards().length === 1, String(sceneCards().length));
panel.querySelector("#av3-weapon-works button[data-work='all']")?.click();
await sleep(30);
tabBtns[3].click();
await sleep(60);
await loadAllScenes();

/* ── 10.12a 🧘 姿势库（v5.1.0）────────────────────────────────────
   用户 2026-11-05：「场景搭配内的姿势场景 UI 改为与角色库、场景搭配同级，
   同时增加更多姿势，例如瑜伽的各种姿势。」
   守三件事：① 只列 group=pose（含 manual_only 的瑜伽/伸展）；
            ② 页内有分类条（全部 / 瑜伽 / 伸展 / 基础姿势）；
            ③ 不显示随机场景开关（姿势不进随机池）。 */
tabBtns[4].click();
await sleep(80);
const posePageCards = () => [...panel.querySelectorAll(".av3-sec[data-scene]")];
const poseTotal = SCENE_FIX.scenes.filter((s) => s.group === "pose").length;
await loadAllScenes();
ck("姿势库页签只列 group=pose（含 manual_only 的瑜伽 / 伸展）",
  posePageCards().length === poseTotal,
  `${posePageCards().length} vs ${poseTotal}`);
/* v6.4.0：标题长，三列会被截成「瑜…」→ 改一行两列（jsdom 不做布局，只验 class 挂上） */
ck("姿势库网格挂上两列 class（.av3-scenegrid.two）",
  panel.querySelector(".av3-scenegrid")?.classList.contains("two") === true,
  panel.querySelector(".av3-scenegrid")?.className || "无网格");
ck("姿势库页有分类条（全部 / 瑜伽 / 伸展 / 基础姿势）",
  [...panel.querySelectorAll("#av3-pose-cats button")].length === 4,
  [...panel.querySelectorAll("#av3-pose-cats button")]
    .map((b) => b.textContent).join("|"));
ck("姿势库页不显示「随机抽取场景搭配」开关（姿势不进随机池）",
  !panel.querySelector("#av3-scene-pick"));
ck("姿势卡都带【姿势·】角标",
  posePageCards().every((c) => /姿势·/.test(c.textContent)));

const yogaTotal = SCENE_FIX.scenes.filter((s) => s.group === "pose"
  && String(s.theme || "").includes("瑜伽")).length;
const pickPoseCat = async (cat) => {
  [...panel.querySelectorAll("#av3-pose-cats button")]
    .find((b) => b.dataset.cat === cat).click();
  await sleep(60);
  await loadAllScenes();
};
await pickPoseCat("yoga");
ck("点「🧘 瑜伽」只剩瑜伽卡",
  posePageCards().length === yogaTotal && yogaTotal >= 32,
  `${posePageCards().length} vs ${yogaTotal}`);
ck("瑜伽卡带【姿势·瑜伽】角标",
  posePageCards().every((c) => /姿势·瑜伽/.test(c.textContent)));
const stretchTotal = SCENE_FIX.scenes.filter((s) => s.group === "pose"
  && String(s.theme || "").includes("伸展")).length;
await pickPoseCat("stretch");
ck("点「🤸 伸展」只剩伸展卡",
  posePageCards().length === stretchTotal && stretchTotal >= 8,
  `${posePageCards().length} vs ${stretchTotal}`);
await pickPoseCat("basic");
ck("点「🧎 基础姿势」只剩非瑜伽/伸展的旧姿势",
  posePageCards().length === poseTotal - yogaTotal - stretchTotal,
  `${posePageCards().length} vs ${poseTotal - yogaTotal - stretchTotal}`);
await pickPoseCat("all");

/* ── 10.12a1·v6.1.0 三处 UI 增强（用户 2026-11-07）────────────────────
   ① 角色库网格撑满左列剩余高度（.av3-left.charmode）；
   ② 右栏「已套用」卡片：场景 / 服装库 / 角色预设，各带 ✕ 一键取消；
   ③ 姿势库 / 武器场景：勾选后在页内随机抽一条并套用。 */
const firstPick = posePageCards()[0]?.querySelector("input.av3-pick");
ck("姿势库每张卡带勾选框", !!firstPick);
const pbarEl = () => panel.querySelector("#av3-pickbar-pose");
ck("姿势库页有「勾选后随机」工具条（全选 / 反选 / 随机 / 清空 四个按钮）",
  !!pbarEl() && pbarEl().querySelectorAll("button").length === 4,
  pbarEl() ? String(pbarEl().querySelectorAll("button").length) : "无工具条");
ck("未勾选时「🎲 从勾选随机」是禁用态",
  pbarEl()?.querySelector("#av3-pick-roll")?.disabled === true);
if (firstPick) {
  firstPick.checked = true;
  firstPick.dispatchEvent(new dom.window.Event("change"));
}
await sleep(30);
ck("勾选一条后计数变 1、「从勾选随机」解禁",
  pbarEl()?.querySelector(".cnt")?.textContent === "1 条已勾选"
  && pbarEl()?.querySelector("#av3-pick-roll")?.disabled === false,
  pbarEl()?.querySelector(".cnt")?.textContent);
ck("右栏存在「已套用」卡片", !!panel.querySelector("#av3-applied"));
pbarEl()?.querySelector("#av3-pick-roll")?.click();
await sleep(80);
ck("点「从勾选随机」后该条被套用（卡片带 on）",
  panel.querySelectorAll(".av3-sec[data-scene].on").length === 1,
  String(panel.querySelectorAll(".av3-sec[data-scene].on").length));
const appliedRow = () => [...panel.querySelectorAll("#av3-applied .av3-applied-row")]
  .find((r) => r.textContent.includes("场景"));
ck("「已套用」里出现场景行", !!appliedRow(),
  panel.querySelector("#av3-applied")?.textContent || "");
appliedRow()?.querySelector(".av3-applied-x")?.click();
await sleep(80);
ck("点 ✕ 一键取消场景套用后该行消失", !appliedRow());

/* v6.6.0：全选 / 反选（作用域 = 当前筛选结果，用户 2026-11-07 拍板）。
   与「🎲 从勾选随机」同一条工具条上 —— 先圈定范围，再随机。 */
{
  const poseN = posePageCards().length;
  const poseCnt = () => pbarEl()?.querySelector(".cnt")?.textContent || "";
  ck("「☑ 全选」按钮上带当前筛选条数",
    /全选（\d+）/.test(pbarEl()?.querySelector("#av3-pick-all")?.textContent || ""),
    pbarEl()?.querySelector("#av3-pick-all")?.textContent || "");
  ck("全选 / 反选不随「0 条已勾选」置灰（否则空集时没有出口）",
    pbarEl()?.querySelector("#av3-pick-all")?.disabled === false
    && pbarEl()?.querySelector("#av3-pick-invert")?.disabled === false);
  pbarEl().querySelector("#av3-pick-all").click();
  await sleep(20);
  ck("点「☑ 全选」= 勾中本页全部姿势",
    poseCnt() === `${poseN} 条已勾选`, `${poseCnt()} vs ${poseN}`);
  pbarEl().querySelector("#av3-pick-invert").click();
  await sleep(20);
  ck("再点「⇄ 反选」= 全部取消", poseCnt() === "0 条已勾选", poseCnt());
  pbarEl().querySelector("#av3-pick-invert").click();
  await sleep(20);
  ck("反选第二次回到全选（可逆）", poseCnt() === `${poseN} 条已勾选`, poseCnt());
  pbarEl().querySelector("#av3-pick-clear").click();
  await sleep(60);
  ck("「清空勾选」后归零",
    (pbarEl()?.querySelector(".cnt")?.textContent || "") === "0 条已勾选",
    pbarEl()?.querySelector(".cnt")?.textContent || "");
}

/* v6.7.0：三组互斥 —— 在姿势页勾上自动随机，艺术构图那个必须自动关掉 */
{
  const poseAuto = () => panel.querySelector("#av3-pick-auto-pose");
  ck("姿势库工具条有「每次运行自动随机」复选框", !!poseAuto());
  const c0 = posePageCards()[0]?.querySelector("input.av3-pick");
  if (c0) {
    c0.checked = true;
    c0.dispatchEvent(new dom.window.Event("change"));
  }
  await sleep(30);
  poseAuto().checked = true;
  poseAuto().dispatchEvent(new dom.window.Event("change"));
  await sleep(60);
  ck("切到姿势库后 pick_pool_mode = pose（互斥：艺术构图自动让位）",
    wPool("pick_pool_mode") === "pose", wPool("pick_pool_mode"));
  ck("ids 换成了姿势的勾选集", /sc_pose_/.test(wPool("pick_pool_ids")),
    wPool("pick_pool_ids"));
  /* 收尾：关掉自动随机，别把状态带进后面的用例 */
  const pb2 = () => panel.querySelector("#av3-pick-auto-pose");
  if (pb2()) {
    pb2().checked = false;
    pb2().dispatchEvent(new dom.window.Event("change"));
    await sleep(40);
  }
  ck("关闭后 pick_pool_mode 归空", wPool("pick_pool_mode") === "",
    wPool("pick_pool_mode"));
}

tabBtns[1].click();
await sleep(120);
ck("角色库左列挂上 charmode（网格吃掉剩余高度）",
  panel.querySelector(".av3-left")?.classList.contains("charmode") === true);
tabBtns[4].click();
await sleep(80);
await loadAllScenes();

/* ── 10.12a2 👗 服装库（v5.1.0）────────────────────────────────────
   用户 2026-11-05：「增加服装库，预设分类好各种丰富的整套服装搭配，
   UI 与角色库、场景搭配同级方便选择。选择后原理与角色服装预设一致。」
   守四件事：① 整库渲染；② 分类筛选；③ 点卡片 = 启用（单选）；④ 能关闭。 */
tabBtns[2].click();
await sleep(100);
/* v6.1.0：右栏新增「已套用」卡片，打开面板时就会拉一次服装库（要显示 active 套）。
   本用例原意是「服装库数据来自服务端、不是内置假数据」—— 提前拉也算满足，
   只是那次请求可能已被前面的 CALLS.length = 0 清掉，所以补一条「已渲染出卡片」的或条件。 */
ck("服装库数据来自服务端（请求过，或已由右栏「已套用」提前拉取并缓存）",
  CALLS.some((c) => c.url.endsWith("/outfit-library"))
  || panel.querySelectorAll(".av3-sec[data-outfit]").length > 0);
const libCards = () => [...panel.querySelectorAll(".av3-sec[data-outfit]")];
ck("服装库渲染全部 110 套",
  libCards().length === LIB_FIX.outfits.length,
  `${libCards().length} vs ${LIB_FIX.outfits.length}`);
/* v6.3.0：分类数改动态（曾写死 11）。
   v6.4.0：分类条默认**收起**，要展开才对得上全部类。 */
const libCatN = (LIB_FIX.categories || []).length;
const catBtns = () => [...panel.querySelectorAll("#av3-outfitlib-cats button")];
ck("分类条默认收起（只留「全部」+ 展开按钮）",
  catBtns().length === 2 && /展开分类/.test(catBtns()[1]?.textContent || ""),
  catBtns().map((b) => b.textContent).join("|"));
panel.querySelector("#av3-outfitlib-cats-toggle")?.click();
await sleep(60);
ck(`点展开后 = 全部 + ${libCatN} 类 + 收起按钮`,
  catBtns().length === libCatN + 2
  && /收起分类/.test(catBtns().slice(-1)[0]?.textContent || ""),
  catBtns().map((b) => b.textContent).join("|"));
const pickLibCat = async (cat) => {
  catBtns().find((b) => b.dataset.cat === cat)?.click();
  await sleep(50);
};
await pickLibCat("swim");
ck("点「泳装内衣」只留该类 10 套", libCards().length === 10,
  `${libCards().length}`);
await pickLibCat("");
ck("点「全部」恢复全量", libCards().length === LIB_FIX.outfits.length,
  `${libCards().length}`);

libCards()[0].querySelector(".hd").click();
await sleep(80);
ck("点卡片发出 /outfit-library/enable",
  CALLS.some((c) => c.url.endsWith("/outfit-library/enable")));
ck("启用后卡片显示「已启用」",
  /已启用/.test(libCards()[0].textContent), libCards()[0].textContent.slice(0, 40));
/* v6.1.1（用户 2026-11-07 02:20「右栏已套用只列出了场景，没有列出服装」）：
   /outfit-library 的 active 在**顶层**，早前按 meta.active 读永远拿不到。 */
ck("启用后右栏「已套用」出现服装库那一行",
  /服装库/.test(panel.querySelector("#av3-applied")?.textContent || ""),
  panel.querySelector("#av3-applied")?.textContent || "");
/* v5.3.1（用户 2026-11-05「我需要的是点击展开才展开」）：
   点卡片 = 选中/取消，**不该**顺手展开；只有点 ▶ 才展开。 */
ck("点卡片启用后不会自动展开",
  !/\bopen\b/.test(libCards()[0].className), libCards()[0].className);
libCards()[0].querySelector(".arw").click();
await sleep(40);
ck("点 ▶ 才展开这套的明细",
  /\bopen\b/.test(libCards()[0].className), libCards()[0].className);
libCards()[0].querySelector(".arw").click();
await sleep(40);
ck("再点 ▶ 收起", !/\bopen\b/.test(libCards()[0].className),
  libCards()[0].className);
/* 两列网格：jsdom 不算 grid 布局，但样式表里必须有这条规则（否则真浏览器是单列） */
ck("服装库列表是两列网格（样式表里有 grid-template-columns:repeat(2…）",
  [...document.querySelectorAll("style")].some((st) =>
    /#av3-outfitlib-list\{[^}]*grid-template-columns:repeat\(2/.test(st.textContent || "")),
  "找不到两列规则");
ck("启用态条显示当前启用的那一套",
  /当前启用/.test(panel.querySelector("#av3-outfitlib-state")?.textContent || ""),
  panel.querySelector("#av3-outfitlib-state")?.textContent || "");
ck("启用后出现「关闭（回到随机）」按钮",
  !!panel.querySelector("#av3-outfitlib-off"));
panel.querySelector("#av3-outfitlib-off").click();
await sleep(80);
ck("点「关闭」后回到未启用",
  /未启用/.test(panel.querySelector("#av3-outfitlib-state")?.textContent || "")
  && !panel.querySelector("#av3-outfitlib-off"),
  panel.querySelector("#av3-outfitlib-state")?.textContent || "");
ck("关闭后右栏「已套用」的服装库行跟着消失",
  !/服装库/.test(panel.querySelector("#av3-applied")?.textContent || ""),
  panel.querySelector("#av3-applied")?.textContent || "");
/* v6.1.1：角色库网格的行高必须钉在内容上 —— 只断言 CSS 规则存在
   （jsdom 不做布局，真浏览器里少了 max-content 就会被压成扁条）。 */
ck("角色库网格显式钉住行高（align-content:start + grid-auto-rows:max-content）",
  [...document.querySelectorAll("style")].some((st) =>
    /charmode \.av3-grid\{[^}]*grid-auto-rows:max-content/.test(st.textContent || "")),
  "找不到行高规则");

const oq = panel.querySelector("#av3-outfitlib-q");
oq.value = "maid";
oq.dispatchEvent(new dom.window.Event("input"));
await sleep(60);
ck("服装库搜索命中女仆装",
  libCards().length >= 1 && libCards().length < LIB_FIX.outfits.length
  && libCards().some((c) => c.textContent.includes("女仆")),
  `${libCards().length} 张`);
oq.value = "";
oq.dispatchEvent(new dom.window.Event("input"));
await sleep(60);

/* ── v5.5.0：袜子 / 鞋子覆盖（用户 2026-11-05 21:27）──────────────
   「可以指定选择丝袜与鞋子款式以及是否穿丝袜与鞋子，两个可以分别指定也可
     不启用（不启用就跟随服装库搭配的服装）。」
   守四件事：① 两条下拉在场；② 默认是「跟随服装库」（空值）；
            ③ 能选「不穿」（= 词库真实选项 barefoot）；④ 切换会发 override 请求。 */
ck("服装库页有袜子/鞋子覆盖栏", !!panel.querySelector("#av3-outfitlib-ov"));
const selS = panel.querySelector("#av3-ov-socks");
const selH = panel.querySelector("#av3-ov-shoes");
ck("丝袜 / 鞋子两条下拉都在", !!selS && !!selH,
  `${!!selS} / ${!!selH}`);
ck("默认都是「跟随服装库」（空值）",
  selS.value === "" && selH.value === "",
  `socks=${selS.value} shoes=${selH.value}`);
ck("丝袜下拉含「不穿 · 裸足」（值 = 词库真实选项 barefoot）",
  [...selS.options].some((o) => o.value === "barefoot"
    && o.textContent.includes("不穿")),
  [...selS.options].slice(0, 3).map((o) => o.value).join(","));
ck("鞋子下拉含「不穿 · 赤足」（值 = bare feet）",
  [...selH.options].some((o) => o.value === "bare feet"
    && o.textContent.includes("不穿")));
ck("丝袜下拉选项覆盖词库全量（>=80 项）",
  selS.options.length >= 80, String(selS.options.length));

selS.value = "black pantyhose";
selS.dispatchEvent(new dom.window.Event("change"));
await sleep(90);
ck("切换丝袜发出 /outfit-library/override",
  CALLS.some((c) => c.url.endsWith("/outfit-library/override")));
ck("覆盖后引擎侧记下该值", LIB_OV.socks === "black pantyhose", String(LIB_OV.socks));
const selS2 = panel.querySelector("#av3-ov-socks");
ck("重绘后下拉仍停在选中的那项",
  selS2 && selS2.value === "black pantyhose",
  selS2 ? selS2.value : "无");

// 只覆盖袜子时，鞋子仍是「跟随」
ck("两个可以分别指定（鞋仍是跟随）",
  (panel.querySelector("#av3-ov-shoes") || {}).value === "",
  String((panel.querySelector("#av3-ov-shoes") || {}).value));

selS2.value = "";
selS2.dispatchEvent(new dom.window.Event("change"));
await sleep(80);
ck("选回「跟随服装库」清空该项", LIB_OV.socks === "", String(LIB_OV.socks));

/* ── v5.9.0：覆盖「场景搭配里写死的推荐服装」开关 ─────────────────
   默认关闭 = 场景优先；开启 = 服装库固定覆盖。守四件事：
   ① 复选框在场且默认不勾；② 摘要回显当前值；③ 切换会带 enabled 发请求；
   ④ 重绘后勾选态从后端返回值读回来，不会被重绘丢掉。 */
ck("服装库页有「覆盖场景推荐服装」开关",
  !!panel.querySelector("#av3-outfitlib-override-scene"));
let scChk = panel.querySelector("#av3-outfitlib-override-scene");
ck("默认关闭（场景有推荐服装时按场景走）",
  scChk && scChk.checked === false, scChk ? String(scChk.checked) : "无");
ck("摘要回显当前值（关闭）",
  String((panel.querySelector("#av3-outfitlib-override-scene-state") || {}).textContent || "")
    .includes("关闭"));
scChk.checked = true;
scChk.dispatchEvent(new dom.window.Event("change"));
await sleep(90);
ck("开启发出 /outfit-library/scene-override",
  CALLS.some((c) => c.url.endsWith("/outfit-library/scene-override")));
ck("请求体带 enabled:true",
  CALLS.some((c) => c.url.endsWith("/outfit-library/scene-override")
    && /"enabled":true/.test(String((c.opts && c.opts.body) || ""))));
ck("桩记下开启", LIB_SCENE_OV === true, String(LIB_SCENE_OV));
scChk = panel.querySelector("#av3-outfitlib-override-scene");
ck("重绘后仍保持开启", scChk && scChk.checked === true,
  scChk ? String(scChk.checked) : "无");
ck("摘要回显当前值（开启）",
  String((panel.querySelector("#av3-outfitlib-override-scene-state") || {}).textContent || "")
    .includes("开启"));
scChk.checked = false;
scChk.dispatchEvent(new dom.window.Event("change"));
await sleep(90);
ck("请求体带 enabled:false（可关回去）",
  CALLS.some((c) => c.url.endsWith("/outfit-library/scene-override")
    && /"enabled":false/.test(String((c.opts && c.opts.body) || ""))));
ck("桩记下关闭", LIB_SCENE_OV === false, String(LIB_SCENE_OV));

/* 面板放大（v5.5.0）：容器 1180×840 → 1520×980，字号不动 */
ck("面板放大到 1520×980（只放容器、不放字号）",
  [...document.querySelectorAll("style")].some((st) =>
    /\.av3-panel\{[^}]*width:min\(1520px/.test(st.textContent || "")),
  "样式表里找不到 1520 规则");

// 回到场景页，后面的用例继续在那页跑
tabBtns[3].click();
await sleep(60);
await loadAllScenes();
/* ── 10.12b 场景检索 / 收藏 / 浏览增强（v3.43.0）─────────────────
   一页 190 条，只靠滚动找不到东西，所以补了搜索 / 收藏 / 题材条 /
   档位折叠 / 懒加载 / 紧凑模式。这几条都只影响**视图**，不碰权重表。 */

// ① 搜索（空格分词 AND）
const sceneQ = () => panel.querySelector("#av3-scene-q");
ck("场景页有搜索框", !!sceneQ());
const typeSceneQ = async (v) => {
  const el = sceneQ();
  el.value = v;
  el.dispatchEvent(new dom.window.Event("input"));
  await sleep(30);
};
await typeSceneQ("教室 托腮");
ck("两个词是 AND：只剩同时命中的那一条",
  sceneCards().length === 1
  && sceneCards()[0].dataset.scene === "sc_classroom_chin_rest",
  `${sceneCards().length} 张：${sceneCards().map((c) => c.dataset.scene).join("/")}`);
ck("「命中 N 条」跟着搜索结果变",
  /命中 1 条/.test(panel.querySelector("#av3-scene-hits")?.textContent || ""),
  panel.querySelector("#av3-scene-hits")?.textContent || "");
ck("搜索时输入框仍是焦点（重绘没把光标弄丢）",
  dom.window.document.activeElement === sceneQ(),
  String(dom.window.document.activeElement?.id
    || dom.window.document.activeElement?.tagName || "（无）"));
await typeSceneQ("雨");
// 期望值从夹具**算**而不是写死 —— 场景库每加一条含「雨」的，写死的 4 就假红。
// ⚠️ v5.5.1：口径必须与前端 `sceneHay()` 一致。前端搜的是
//   zh / theme / desc / id / part / 字段中文名 / extra / 变体中文名，
//   只算 `zh` 会漏 —— 加了氛围变体后，`extra` 里出现 rain 的条目也会被搜到。
const sceneHayOf = (s) => [
  s.zh, s.theme, s.desc, s.id, s.part,
  (s.fields || []).map((f) => (f.lab || "") + " " + (f.en || "")).join(" "),
  s.extra || "",
  (s.variants || []).join(" "),
].join(" ").toLowerCase();
const rainN = SCENE_FIX.scenes.filter((s) => !s.manual
  && sceneHayOf(s).includes("雨")).length;
ck("中文描述 / 题材也能搜到", sceneCards().length === rainN && rainN > 0,
  `${sceneCards().length} vs ${rainN}`);
await typeSceneQ("kneeling");
ck("英文标签（字段值）同样可搜",
  sceneCards().length > 0 && sceneCards().length < 60,
  `${sceneCards().length} 张`);
await typeSceneQ("zzz这个场景不存在");
ck("搜不到时给空状态，而不是一片空白",
  !!panel.querySelector("#av3-scene-empty") && sceneCards().length === 0);
ck("空状态下不再挂「还有 N 条」", !panel.querySelector("#av3-scene-more"));
panel.querySelector("#av3-scene-clear").click();
await sleep(30);
ck("「清空筛选」把搜索词也清掉，且回到首屏 60 条",
  sceneQ().value === "" && sceneCards().length === 60,
  `「${sceneQ().value}」/ ${sceneCards().length} 张`);

/* ── 10.12a2 中文输入法（IME）不能被重绘打断（v4.5.40）──────────────
   用户 2026-10-31 实测：场景搭配搜索栏打中文，框里出现 `JiaoShi` 这种字母。
   根因是 `q.oninput` 每次都走 renderScenes() → `left.innerHTML=""`，
   **输入框节点被重建**；拼音还在组合串里就随节点一起没了，浏览器把原始
   拼音提交成明文。这组断言钉住「组合期间一次重绘都不发生」。 */
const node0 = sceneQ();
node0.dispatchEvent(new dom.window.Event("compositionstart"));
node0.value = "jiaoshi";                       // 组合串（还没上屏）
node0.dispatchEvent(new dom.window.Event("input"));
await sleep(30);
ck("IME：组合中的 input 不触发重绘（输入框节点原样保留）",
  sceneQ() === node0, sceneQ() === node0 ? "" : "节点被重建 → 拼音会被提交成字母");
ck("IME：组合中列表不跟着字母过滤（仍是全量首屏）",
  sceneCards().length === 60, `${sceneCards().length} 张`);
node0.value = "教室 托腮";                    // 上屏（两个词，与上面那条断言同一组）
node0.dispatchEvent(new dom.window.Event("compositionend"));
await sleep(30);
ck("IME：上屏后按中文过滤（教室 · 托腮）",
  sceneCards().length === 1
  && sceneCards()[0].dataset.scene === "sc_classroom_chin_rest",
  `${sceneCards().length} 张：${sceneCards().map((c) => c.dataset.scene).join("/")}`);
ck("IME：上屏后的输入框值是中文，不是拼音",
  sceneQ() && sceneQ().value === "教室 托腮", `「${sceneQ()?.value}」`);
// 收尾：清掉搜索词，后面的断言仍按全量算
await typeSceneQ("");

/* ── 10.12a3 推荐画幅徽标 / 比例筛选 / 随机场景开关（v5.0.1）──────────
   用户 2026-11-01 三条口径：
     ① 场景要按构图标「竖 / 横 / 通用」，并在场景 UI 看得到；
     ② 加「竖屏和通用比例」「横屏和通用比例」两组筛选；
     ③ 场景页加「场景搭配随机抽取」勾选（取消原来的 60% 概率规则）。 */
ck("每张场景卡都有推荐画幅徽标",
  sceneCards().length > 0
  && sceneCards().every((c) => !!c.querySelector(".av3-aspect")),
  `${sceneCards().filter((c) => !c.querySelector(".av3-aspect")).length} 张没有徽标`);
ck("徽标文案只有 竖 / 横 / 通 三种",
  new Set(sceneCards().map((c) => c.querySelector(".av3-aspect").textContent)).size <= 3,
  [...new Set(sceneCards().map((c) => c.querySelector(".av3-aspect").textContent))].join("/"));
ck("场景页有三个比例筛选按钮",
  !!panel.querySelector("#av3-scene-aspect-all")
  && !!panel.querySelector("#av3-scene-aspect-p")
  && !!panel.querySelector("#av3-scene-aspect-l"));

panel.querySelector("#av3-scene-aspect-p").click();
await sleep(40);
ck("「竖屏·通用」下不出现横屏推荐",
  sceneCards().every((c) => !/aspect-landscape/
    .test(c.querySelector(".av3-aspect").className)),
  `${sceneCards().filter((c) => /aspect-landscape/.test(
    c.querySelector(".av3-aspect").className)).length} 张横屏混进来`);
ck("「竖屏·通用」按钮点亮",
  /pri/.test(panel.querySelector("#av3-scene-aspect-p").className));

panel.querySelector("#av3-scene-aspect-l").click();
await sleep(40);
ck("「横屏·通用」下不出现竖屏推荐",
  sceneCards().every((c) => !/aspect-portrait/
    .test(c.querySelector(".av3-aspect").className)));

panel.querySelector("#av3-scene-aspect-all").click();
await sleep(40);
ck("「全部」恢复首屏 60 条", sceneCards().length === 60,
  `${sceneCards().length} 张`);

const pickCb = () => panel.querySelector("#av3-scene-pick-cb");
const spVal = () => node.widgets.find((w) => w.name === "scene_pick")?.value;
ck("场景页有「随机抽取场景搭配」复选框", !!pickCb());
ck("复选框默认不勾（= 不出场景）", pickCb()?.checked === false && spVal() === false);
pickCb().checked = true;
pickCb().dispatchEvent(new dom.window.Event("change"));
await sleep(30);
ck("勾选后写进节点控件 scene_pick（预演与实跑同参）", spVal() === true,
  String(spVal()));
pickCb().checked = false;                       // 重绘后节点已换新，重新取
pickCb().dispatchEvent(new dom.window.Event("change"));
await sleep(30);
ck("取消勾选后写回 false", spVal() === false, String(spVal()));

// ② 题材快捷筛选条
const themeBar = () => panel.querySelector("#av3-scene-themes");
ck("有题材快捷筛选条", !!themeBar());
ck("题材条默认按条数降序、最多 18 个 + 「更多」",
  [...themeBar().querySelectorAll("button[data-theme]")].length === 18
  && !!panel.querySelector("#av3-scene-thememore"),
  `${[...themeBar().querySelectorAll("button[data-theme]")].length} 个`);
const themeBtn = (t) => [...themeBar().querySelectorAll("button")]
  .find((b) => b.dataset.theme === t);
ck("题材条里有 BDSM", !!themeBtn("BDSM"),
  [...themeBar().querySelectorAll("button")].slice(0, 6)
    .map((b) => b.textContent).join("|"));
themeBtn("BDSM").click();
await sleep(30);
ck("选题材后只剩该题材的卡",
  sceneCards().length === 18 && sceneCards().every((c) => /【BDSM】/.test(c.textContent)),
  `${sceneCards().length} 张`);
ck("选中的题材按钮高亮",
  /pri/.test(themeBtn("BDSM")?.className || ""), themeBtn("BDSM")?.className);
themeBtn("BDSM").click();
await sleep(30);
const allThemeBtn = () => [...themeBar().querySelectorAll("button")]
  .find((b) => b.textContent.startsWith("全部题材"));
ck("再点一次取消题材筛选", sceneCards().length === 60 && /pri/.test(
  allThemeBtn()?.className || ""),
  `${sceneCards().length} 张 / 按钮=${allThemeBtn()?.className} / theme="${allThemeBtn()?.textContent}"`);
ck("「更多题材」能展开被截断的题材",
  (() => {
    panel.querySelector("#av3-scene-thememore").click();
    return [...themeBar().querySelectorAll("button[data-theme]")].length > 18;
  })(),
  String([...themeBar().querySelectorAll("button[data-theme]")].length));
panel.querySelector("#av3-scene-thememore").click();
await sleep(20);

/* ⑥ 题材条整条可收起（v3.44.0）───────────────────────────────
   两级折叠必须能分开：整条 = 「▾/▸ 🏷 题材」，条内 = 「更多题材 / 只显示常用的」。
   收起后当前题材仍要看得见，否则会出现"列表被筛掉一半却看不出原因"。 */
const themeTog = () => panel.querySelector("#av3-scene-themetoggle");
const themeChips = () => [...themeBar().querySelectorAll("button[data-theme]")];
ck("题材条有整条收起按钮，默认展开",
  /▾/.test(themeTog()?.textContent || "") && themeChips().length === 18,
  `${themeTog()?.textContent} / ${themeChips().length} 个题材`);
panel.querySelector("#av3-scene-thememore").click();     // 展开到全部 62
await sleep(20);
ck("条内那颗改叫「只显示常用的」，不再与整条收起撞名",
  /只显示常用/.test(panel.querySelector("#av3-scene-thememore")?.textContent || "")
  && themeChips().length > 18,
  `${panel.querySelector("#av3-scene-thememore")?.textContent} / ${themeChips().length} 个`);
panel.querySelector("#av3-scene-thememore").click();     // 收回 18 个常用
await sleep(20);
ck("「只显示常用的」能收回到 18 个",
  themeChips().length === 18, String(themeChips().length));

themeBtn("BDSM").click();
await sleep(30);
themeTog().click();
await sleep(30);
ck("收起后题材 chip 全部消失",
  themeChips().length === 0, String(themeChips().length));
ck("收起后仍显示当前题材（不会筛掉一半却看不出原因）",
  /当前：BDSM/.test(panel.querySelector("#av3-scene-themesum")?.textContent || ""),
  panel.querySelector("#av3-scene-themesum")?.textContent || "（没有摘要）");
ck("收起后筛选仍然生效（卡片还是那 18 条 BDSM）",
  sceneCards().length === 18 && sceneCards().every((c) => /【BDSM】/.test(c.textContent)),
  `${sceneCards().length} 张`);
ck("收起态箭头变 ▸",
  /▸/.test(themeTog()?.textContent || ""), themeTog()?.textContent);
panel.querySelector("#av3-scene-themeclear").click();
await sleep(30);
ck("收起态能直接 ✕ 取消题材，不用先展开",
  !panel.querySelector("#av3-scene-themeclear") && sceneCards().length === 60,
  `${sceneCards().length} 张`);
themeTog().click();
await sleep(30);
ck("再点一次展开，题材 chip 回来",
  themeChips().length === 18 && /▾/.test(themeTog().textContent),
  `${themeChips().length} 个 / ${themeTog().textContent}`);
await loadAllScenes();

// ③ 档位分组可折叠
const lvHead = (n) => panel.querySelector("#av3-scene-lv" + n);
const levelOf = (id) => (SCENE_FIX.scenes.find((s) => s.id === id) || {}).level;
const l0Cards = () => sceneCards().filter((c) => levelOf(c.dataset.scene) === 0);
// v5.0.21：艺术构图已独立成页签、不再出现在场景页 → 口径回到 !manual
// v5.1.0：姿势已独立成页签，场景页的 L0 只算 sfw + nsfw
const nonManualL0 = SCENE_FIX.scenes
  .filter((s) => !s.manual && s.group !== "pose" && s.level === 0).length;
ck("档位标题行变成可折叠的（带 ▼）",
  /▼/.test(lvHead(0)?.textContent || ""), lvHead(0)?.textContent || "（没有 L0 标题）");
ck("折叠前 L0 卡在场", l0Cards().length === nonManualL0,
  `${l0Cards().length} vs ${nonManualL0}`);
lvHead(0).click();
await sleep(30);
ck("点档位标题后这一档收起（L0 卡全部消失）", l0Cards().length === 0,
  `还剩 ${l0Cards().length} 张`);
ck("收起后标题变成 ▶", /▶/.test(lvHead(0)?.textContent || ""),
  lvHead(0)?.textContent || "");
lvHead(0).click();
await sleep(30);
ck("再点一次展开回来", l0Cards().length === nonManualL0,
  `${l0Cards().length} vs ${nonManualL0}`);

// ④ 收藏（两页共用，存独立文件）
const sceneStarOf = (id) => panel.querySelector(`.av3-sec[data-scene="${id}"] .av3-sfav`);
const favId = "sc_rooftop_stretch";
ck("每张场景卡都有 ☆ 收藏按钮", sceneStarOf(favId)?.textContent === "☆",
  sceneStarOf(favId)?.textContent || "（没有星标）");
CALLS.length = 0;
sceneStarOf(favId).click();
for (let i = 0; i < 60
     && !CALLS.some((c) => c.url.endsWith("/scenes/favorites/toggle")); i++) {
  await sleep(10);
}
await sleep(50);
ck("点 ☆ 发出 /scenes/favorites/toggle（独立于角色收藏那条路由）",
  CALLS.some((c) => c.url.endsWith("/scenes/favorites/toggle")),
  CALLS.map((c) => c.url).filter((u) => u.includes("favor")).join("|"));
ck("点 ☆ 不会顺手把卡片展开（stopPropagation 生效）",
  !panel.querySelector(`.av3-sec[data-scene="${favId}"]`)?.classList.contains("open"));
ck("收藏后星标变 ★", sceneStarOf(favId)?.textContent === "★", sceneStarOf(favId)?.textContent);
ck("收藏项被置顶到「⭐ 我的收藏」区",
  /（1）/.test(panel.querySelector("#av3-scene-favsec")?.textContent || ""),
  panel.querySelector("#av3-scene-favsec")?.textContent || "（没有收藏区）");
// v4.5.41：收藏卡现在包在 3 列网格里（收藏区标题的下一层是 .av3-scenegrid）
const favFirstCard = () => panel.querySelector("#av3-scene-favsec")
  ?.nextElementSibling?.querySelector?.(".av3-sec[data-scene]") || null;
ck("置顶区第一张就是它",
  favFirstCard()?.dataset.scene === favId,
  favFirstCard()?.dataset.scene || "（空）");
ck("置顶后**不在**档位分组里重复出现",
  sceneCards().filter((c) => c.dataset.scene === favId).length === 1,
  String(sceneCards().filter((c) => c.dataset.scene === favId).length));

/* ── v4.5.41：收藏区「只在交互后展开」+ 3 列网格 + 说明收起 ────────
   用户 2026-10-31：简化场景搭配界面、方便浏览；收藏只在交互后展开。 */
panel.querySelector("#av3-scene-favsec").click();
await sleep(30);
ck("点收藏区标题能收起（收起后收藏卡不渲染，不挤占浏览位置）",
  sceneCards().filter((c) => c.dataset.scene === favId).length === 0
  && /▶/.test(panel.querySelector("#av3-scene-favsec")?.textContent || ""),
  `${sceneCards().filter((c) => c.dataset.scene === favId).length} 张 / `
  + (panel.querySelector("#av3-scene-favsec")?.textContent || "（没有收藏区）"));
panel.querySelector("#av3-scene-favsec").click();
await sleep(30);
ck("再点一次又展开回来", favFirstCard()?.dataset.scene === favId,
  favFirstCard()?.dataset.scene || "（空）");
const grids = [...panel.querySelectorAll(".av3-scenegrid")];
ck("场景卡排进 3 列网格（每张卡的父节点都是 .av3-scenegrid）",
  grids.length > 0
  && sceneCards().every((c) => c.parentElement?.classList.contains("av3-scenegrid")),
  `${grids.length} 个网格 / ${sceneCards().length} 张卡`);
ck("网格里每格只放一张卡（分组标题不进网格）",
  grids.every((g) => g.children.length > 0
    && [...g.children].every((n) => n.classList.contains("av3-sec"))),
  grids.map((g) => g.children.length).join("|"));
ck("「套用并预演」是次级动作（折叠时由 CSS 隐藏，带 .av3-act2）",
  !!panel.querySelector(".av3-sec .av3-act2")
  || !!panel.querySelector(".av3-sceneact .av3-act2"),
  "没找到 .av3-act2");
ck("用法说明默认收起成一个按钮（不再占四行）",
  !!panel.querySelector("#av3-scene-help") && !panel.querySelector("#av3-scene-tip"),
  panel.querySelector("#av3-scene-help") ? "说明还摊开着" : "没有说明按钮");
panel.querySelector("#av3-scene-help").click();
await sleep(30);
ck("点「用法说明」才展开正文", !!panel.querySelector("#av3-scene-tip"));
panel.querySelector("#av3-scene-help").click();
await sleep(30);
ck("再点一次收起", !panel.querySelector("#av3-scene-tip"));
panel.querySelector("#av3-scene-favonly").click();
await sleep(30);
ck("「⭐ 只看收藏」只剩收藏项",
  sceneCards().length === 1 && sceneCards()[0].dataset.scene === favId,
  `${sceneCards().length} 张`);
panel.querySelector("#av3-scene-favonly").click();
await sleep(30);
const nFavPost = CALLS.filter((c) => c.url.endsWith("/scenes/favorites/toggle")).length;
sceneStarOf(favId).click();
for (let i = 0; i < 60
     && CALLS.filter((c) => c.url.endsWith("/scenes/favorites/toggle")).length === nFavPost; i++) {
  await sleep(10);
}
await sleep(50);
// ⚠️ 取消收藏后它不再置顶，回到档位分组里 —— 排在 60 条之后也算正常，
//    所以先放全量再断言"回到分组"，否则测的是懒加载窗口而不是分类逻辑。
await loadAllScenes();
ck("取消收藏后置顶区消失、卡片回到档位分组",
  !panel.querySelector("#av3-scene-favsec")
  && sceneCards().filter((c) => c.dataset.scene === favId).length === 1,
  `favsec=${!!panel.querySelector("#av3-scene-favsec")}`
  + ` / 卡片数=${sceneCards().filter((c) => c.dataset.scene === favId).length}`
  + ` / 总卡=${sceneCards().length}`);

// ⑤ 紧凑模式
panel.querySelector("#av3-scene-compact").click();
await sleep(30);
ck("紧凑模式给每张卡挂 compact 类",
  sceneCards().length > 0 && sceneCards().every((c) => c.classList.contains("compact")));
ck("紧凑模式下动作行仍在（套用不必先展开）",
  !!sceneCards()[0].querySelector(".av3-sceneact .av3-btn"));
panel.querySelector("#av3-scene-compact").click();
await sleep(30);
ck("切回卡片模式去掉 compact 类",
  sceneCards().every((c) => !c.classList.contains("compact")));

// ⚠️ v3.10.0：一级分组是 **NSFW 档位**（level_groups），不是 sfw/nsfw
ck("载荷带五个档位分组", (SCENE_FIX.level_groups || []).length === 5,
  String((SCENE_FIX.level_groups || []).length));
ck("每个非空档位组都有小标题",
  (SCENE_FIX.level_groups || []).filter((g) => g.count > 0)
    .every((g) => [...panel.querySelectorAll(".av3-note")]
      .some((e) => e.textContent.includes(g.title))),
  (SCENE_FIX.level_groups || []).map((g) => `${g.title}:${g.count}`).join(" "));
ck("档位分组标题带条数",
  /（\d+）/.test([...panel.querySelectorAll(".av3-note")]
    .map((e) => e.textContent).join("")));
ck("当前档位那一组被标出来",
  [...panel.querySelectorAll(".av3-note")]
    .some((e) => e.textContent.includes("当前档位")));
ck("卡片标题显示题材标签（【…】）",
  /【.+】/.test(sceneCards()[0].querySelector(".hd").textContent),
  sceneCards()[0].querySelector(".hd").textContent.slice(0, 48));
// ⚠️ v3.11.0：场景卡**默认折叠**（87 条全展开要滚几千像素）。
//    动作行必须在 .bd **外面**，否则折叠时连「一键套用」都点不到。
ck("场景卡默认折叠", sceneCards().every((c) => !c.classList.contains("open")));
const sceneBtnOf = (card, kw) => [...card.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes(kw));
ck("折叠时动作行仍可见（一键套用 / 套用并预演都在）",
  !!sceneBtnOf(sceneCards()[0], "一键套用") && !!sceneBtnOf(sceneCards()[0], "预演"),
  [...sceneCards()[0].querySelectorAll(".av3-btn")].map((b) => b.textContent).join("/"));
ck("卡片标出档位",
  /L\d|全档/.test(sceneCards()[0].querySelector(".hd").textContent),
  sceneCards()[0].querySelector(".hd").textContent);

// 点标题行 → 展开出字段列表
sceneCards()[0].querySelector(".hd").click();
ck("点标题行后卡片展开", sceneCards()[0].classList.contains("open"));
ck("展开后列出被固定的字段（lab：值）",
  sceneCards()[0].querySelectorAll(".av3-chip").length
    >= (SCENE_FIX.scenes[0].fields || []).length,
  String(sceneCards()[0].querySelectorAll(".av3-chip").length));
sceneCards()[0].querySelector(".hd").click();
ck("再点一次收起", !sceneCards()[0].classList.contains("open"));

// 全部展开 / 全部收起
panel.querySelector("#av3-scene-expand").click();
ck("「全部展开」把每张卡都打开",
  sceneCards().every((c) => c.classList.contains("open")),
  `${sceneCards().filter((c) => c.classList.contains("open")).length} 张`);
ck("按钮文案变成「全部收起」",
  /全部收起/.test(panel.querySelector("#av3-scene-expand").textContent));
panel.querySelector("#av3-scene-expand").click();
ck("「全部收起」把每张卡都合上",
  sceneCards().every((c) => !c.classList.contains("open")));

const wjson = () => JSON.parse(
  node.widgets.find((w) => w.name === "weights_json").value || "{}");
const sceneIdOf = (card) => card.dataset.scene;

// 一键套用
const applyBtnOf = (card) => [...card.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("套用") && !b.textContent.includes("预演"));
const target = sceneCards().find((c) => sceneIdOf(c) === "sc_rooftop_stretch")
  || sceneCards()[0];
const targetId = sceneIdOf(target);
applyBtnOf(target).click();
const w1 = wjson();
ck("套用后 n_canon 字段权重 = 3.0（必现）",
  Number(w1.fields?.n_canon) === 3, JSON.stringify(w1.fields));
ck("套用后本场景选项 = 3.0",
  Number(w1.options?.n_canon?.[targetId]) === 3,
  JSON.stringify(w1.options?.n_canon?.[targetId]));
const others = Object.entries(w1.options?.n_canon || {})
  .filter(([k]) => k !== targetId);
ck("同字段其它预设全部归零（保证只出这一条）",
  others.length > 0 && others.every(([, v]) => Number(v) === 0),
  `${others.length} 项`);
ck("套用后段权重不是 0（否则段会被 disabled_sections 关掉）",
  Number(w1.sections?.canon ?? 1) !== 0, JSON.stringify(w1.sections));
ck("套用后状态条显示当前套用",
  /当前套用/.test(panel.querySelector("#av3-scene-cur")?.textContent || ""),
  panel.querySelector("#av3-scene-cur")?.textContent || "");
ck("该卡按钮变成已套用且禁用",
  applyBtnOf(panel.querySelector(`.av3-sec[data-scene="${targetId}"]`)).disabled === true);

// 套用并预演 → 应发出 /roll
const nRoll2 = CALLS.filter((c) => c.url.endsWith("/roll")).length;
const pvBtn = [...panel.querySelector(`.av3-sec[data-scene="${targetId}"]`)
  .querySelectorAll(".av3-btn")].find((b) => b.textContent.includes("预演"));
pvBtn.click();
for (let i = 0; i < 80
     && CALLS.filter((c) => c.url.endsWith("/roll")).length === nRoll2; i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("「套用并预演」真的发出了 /roll",
  CALLS.filter((c) => c.url.endsWith("/roll")).length > nRoll2);

// v3.11.0：👁 预览 = **只看不套用**（临时权重，不改权重表）
const sceneCardOf = (id) => panel.querySelector(`.av3-sec[data-scene="${id}"]`);
const pvWBefore = JSON.stringify(wjson());
const nRollPv = CALLS.filter((c) => c.url.endsWith("/roll")).length;
const pvEyeBtn = sceneBtnOf(sceneCardOf(targetId), "预览");
ck("场景卡有「👁 预览」按钮", !!pvEyeBtn,
  [...sceneCardOf(targetId).querySelectorAll(".av3-btn")].map((b) => b.textContent).join("/"));
pvEyeBtn.click();
// ⚠️ 等**内联框真的渲染出来**，不能只等 /roll 被调用 —— 请求与响应是异步的
for (let i = 0; i < 100
     && !(sceneCardOf(targetId).querySelector(".av3-sceneout")?.textContent || "")
       .includes("school uniform"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
ck("「👁 预览」发出了 /roll",
  CALLS.filter((c) => c.url.endsWith("/roll")).length > nRollPv);
ck("「👁 预览」**不改动**权重表（与「套用并预演」的关键区别）",
  JSON.stringify(wjson()) === pvWBefore, JSON.stringify(wjson()).slice(0, 80));
const pvInlineOut = sceneCardOf(targetId).querySelector(".av3-sceneout");
ck("预览结果显示在卡片**内联**框里",
  !!pvInlineOut && pvInlineOut.style.display !== "none"
  && (pvInlineOut.textContent || "").includes("school uniform"),
  (pvInlineOut && pvInlineOut.textContent || "").slice(0, 60));
ck("内联预览带档位/自检行",
  !!pvInlineOut && /自检/.test(pvInlineOut.textContent));
pvEyeBtn.click();
ck("再点「👁 预览」收起内联框",
  sceneCardOf(targetId).querySelector(".av3-sceneout").style.display === "none",
  sceneCardOf(targetId).querySelector(".av3-sceneout").style.display);

// 取消套用
[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("取消套用")).click();
const w2 = wjson();
ck("取消套用后 n_canon 选项清空",
  !w2.options?.n_canon || Object.keys(w2.options.n_canon).length === 0,
  JSON.stringify(w2.options?.n_canon));
ck("取消套用后字段权重回到默认（不写进权重表）",
  w2.fields?.n_canon === undefined, JSON.stringify(w2.fields));
ck("取消后状态条显示未套用",
  /未套用/.test(panel.querySelector("#av3-scene-cur")?.textContent || ""),
  panel.querySelector("#av3-scene-cur")?.textContent || "");

/* ── 10.13 特写搭配（v3.18.0）────────────────────────────────── */
const nearTab = tabBtns.find((b) => b.textContent.includes("特写搭配"));
const nearPartOf = (id) => (SCENE_FIX.scenes.find((s) => s.id === id) || {}).part || "";
// v5.0.9：特写页只认 group=near（艺术构图也在 manual_only，但归场景页）
// v5.1.0：瑜伽 / 伸展也是 manual_only，但它们归「🧘 姿势库」，不进特写页
const manualScenes = SCENE_FIX.scenes.filter((s) => s.manual && s.group === "near");
const nearCards = () => [...panel.querySelectorAll(".av3-sec[data-scene^='sc_near_']")];
const btnByText = (kw) => [...panel.querySelectorAll(".av3-btn")]
  .find((b) => (b.textContent || "").trim().startsWith(kw));

nearTab.click();
for (let i = 0; i < 100 && !nearCards().length; i++) await sleep(10);
ck("特写页卡片数 == 载荷里的 manual 条数",
  nearCards().length === manualScenes.length,
  `${nearCards().length} vs ${manualScenes.length}`);
ck("特写卡都带「🔍 仅手动」角标",
  nearCards().every((c) => c.textContent.includes("仅手动")),
  nearCards().filter((c) => !c.textContent.includes("仅手动")).map((c) => c.dataset.scene).join("/"));
const LOCAL_PARTS = ["腿部", "脚部", "手部", "臀部"];
const wOfRW = () => String(node.widgets.find((w) => w.name === "role_works").value || "");
const roleSrc = panel.querySelector("#av3-near-rolesrc");
const roleIp = panel.querySelector("#av3-near-rolesrc-ip");
ck("特写页有「🎭 角色来源」栏（面部/眼唇/上半身 用）", !!roleSrc && !!roleIp,
  `${!!roleSrc} / ${!!roleIp}`);
ck("角色来源默认显示「全库随机」",
  /全库随机/.test(roleSrc?.textContent || ""), roleSrc?.textContent || "");
roleIp.value = "genshin impact";
roleIp.dispatchEvent(new dom.window.Event("input"));
await sleep(20);
ck("填作品 IP 写进同一个 role_works 控件",
  wOfRW() === "genshin impact", wOfRW());
ck("状态文案跟着变成「在作品…内随机」",
  /genshin impact/.test(roleSrc?.textContent || ""), roleSrc?.textContent || "");
roleIp.value = "";
roleIp.dispatchEvent(new dom.window.Event("input"));
await sleep(20);
ck("清空后回到全库随机",
  wOfRW() === "" && /全库随机/.test(roleSrc?.textContent || ""),
  `${wOfRW()} / ${roleSrc?.textContent}`);
/* ── v5.0.6：手部里的「有脸」条目也带角色 ──────────────────────────
   用户 2026-11-02 实测「手指抚唇」：选定人物套用后，画面是个没身份的侧脸。
   根因：手部组一律 dropRole（清 selected_roles + force_role 并锁 role），
   且把 ②③④⑤ 全关 —— 发型、瞳色、肤色一个都进不了正文。 */
{
  const lipSc = SCENE_FIX.scenes.find((s) => s.id === "sc_near_hand_lip");
  ck("「手指抚唇」在夹具里标了 role_ok（要手也要脸）", !!(lipSc && lipSc.role_ok),
    lipSc ? String(lipSc.role_ok) : "(缺这条)");
  const lipShot = ((lipSc && lipSc.fields) || []).find((f) => f.field === "shot_size");
  ck("它的景别放宽到 close-up（extreme close-up 装不下脸）",
    !!(lipShot && lipShot.en === "close-up"), lipShot ? lipShot.en : "(无)");
  ck("前端有「手部·有脸」白名单", /"手部·有脸"/.test(jsSrc));
  ck("nearSlim 对任意 role_ok 条目都不清角色（不再只限成人向）",
    /if \(sc\.role_ok\) \{/.test(jsSrc)
    && !/part === "🔞 成人向" && sc\.role_ok/.test(jsSrc));
  btnByText("手部").click();
  await sleep(40);
  ck("切到「手部」页仍能看到角色来源栏（否则不知道带的是谁）",
    !!panel.querySelector("#av3-near-rolesrc"));
  btnByText("面部").click();
  await sleep(40);
}

/* role 锁定诊断 + 自动解锁（v3.18.4）—— 修 v3.18.3 的"只有指定角色生效" */
const lockOf = () => String(node.widgets.find((w) => w.name === "lock_fields").value || "");
const hasRoleLock = () => /(^|[,\s])role([,\s]|$)/.test(lockOf());
node.widgets.find((w) => w.name === "lock_fields").value = "role";
btnByText("面部").click();          // 触发一次 renderNear
await sleep(40);
const lockWarn = panel.querySelector("#av3-near-rolelock");
ck("role 被锁时特写页出现诊断行", !!lockWarn,
  [...panel.querySelectorAll(".av3-charbar")].map((e) => e.textContent).join(" | ").slice(0, 160));
ck("诊断行说明随机 / IP 抽角色不会生效",
  /不会生效/.test(lockWarn?.textContent || ""), lockWarn?.textContent || "");
ck("诊断行带「🔓 解除 role 锁」按钮", !!panel.querySelector("#av3-near-roleunlock"));
panel.querySelector("#av3-near-roleunlock").click();
await sleep(30);
ck("点解除后 lock_fields 里没有 role", !hasRoleLock(), lockOf());
ck("解除后诊断行消失", !panel.querySelector("#av3-near-rolelock"));

/* 套用"要角色"的条目时，自动摘掉残留的 role 锁 */
node.widgets.find((w) => w.name === "lock_fields").value = "role";
btnByText("全部").click();
await sleep(30);
const faceCard4 = nearCards().find((c) => c.dataset.scene === "sc_near_face_classroom");
ck("面部组样例卡在列表里", !!faceCard4);
applyBtnOf(faceCard4).click();
await sleep(30);
ck("套用面部条目时自动摘掉残留的 role 锁", !hasRoleLock(), lockOf());
ck("状态条说明已解除 role 锁（不静默）",
  /role 锁/.test(panel.querySelector("#av3-status")?.textContent || ""),
  panel.querySelector("#av3-status")?.textContent || "");
[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("取消套用")).click();
await sleep(20);
node.widgets.find((w) => w.name === "lock_fields").value = "";   // 收尾，别污染后面的用例

// v5.0.6：标了 role_ok 的条目（手指抚唇）不再清角色，角标也该跟着变
const roleOkIds = new Set(SCENE_FIX.scenes.filter((s) => s.role_ok).map((s) => s.id));
ck("手/腿/脚/臀 卡片带「✂ 收紧取景」+「🧹 清角色」角标（role_ok 条目除外）",
  nearCards().filter((c) => LOCAL_PARTS.includes(nearPartOf(c.dataset.scene))
      && !roleOkIds.has(c.dataset.scene))
    .every((c) => c.textContent.includes("收紧取景") && c.textContent.includes("清角色")),
  nearCards().filter((c) => LOCAL_PARTS.includes(nearPartOf(c.dataset.scene))
      && !roleOkIds.has(c.dataset.scene)
      && !(c.textContent.includes("收紧取景") && c.textContent.includes("清角色")))
    .map((c) => c.dataset.scene).join("/"));
ck("标了 role_ok 的条目改显示「角色生效」而不是「清角色」",
  nearCards().filter((c) => roleOkIds.has(c.dataset.scene))
    .every((c) => c.textContent.includes("角色生效")
      && !c.textContent.includes("清角色")),
  nearCards().filter((c) => roleOkIds.has(c.dataset.scene)).map((c) => c.dataset.scene).join("/"));
ck("成人向卡都带「收紧取景」，且「清角色 / 角色生效」二选一",
  nearCards().filter((c) => nearPartOf(c.dataset.scene) === "🔞 成人向")
    .every((c) => c.textContent.includes("收紧取景")
      && (c.textContent.includes("清角色") || c.textContent.includes("角色生效"))));
ck("面部卡带「收紧取景」但**不**带「清角色」（角色与表情要保留）",
  nearCards().filter((c) => nearPartOf(c.dataset.scene) === "面部")
    .every((c) => c.textContent.includes("收紧取景")
      && !c.textContent.includes("清角色")));
ck("上半身与胸卡也不清角色",
  nearCards().filter((c) => nearPartOf(c.dataset.scene) === "上半身与胸")
    .every((c) => !c.textContent.includes("清角色")));
ck("按部位分组的小标题都在（八个部位，含臀部与成人向）",
  ["面部", "眼与唇", "上半身与胸", "手部", "腿部", "脚部", "臀部", "🔞 成人向"]
    .every((p) => [...panel.querySelectorAll(".av3-note")]
      .some((e) => (e.textContent || "").startsWith(p))),
  nearCards().length + " 张卡");

/* 部位筛选：点「腿部」只剩腿组 */
btnByText("腿部").click();
await sleep(40);
const legN = manualScenes.filter((s) => s.part === "腿部").length;
ck("部位筛选「腿部」只剩腿部条目",
  nearCards().length === legN
  && nearCards().every((c) => nearPartOf(c.dataset.scene) === "腿部"),
  `${nearCards().length} vs ${legN}`);
btnByText("全部").click();
await sleep(40);
ck("筛回「全部」恢复全部特写条数", nearCards().length === manualScenes.length,
  String(nearCards().length));

/* ── v6.6.0 特写搭配也能「勾选后随机」（用户 2026-11-07）──────────────
   特写组同样是 manual_only，所以与姿势库 / 武器场景共用一条工具条：
   ☑ 全选 / ⇄ 反选 / 🎲 从勾选随机 / 清空勾选。 */
{
  const npbar = () => panel.querySelector("#av3-pickbar-near");
  const ncnt = () => npbar()?.querySelector(".cnt")?.textContent || "";
  /* ⚠️ 用 `.av3-sec[data-scene]` 而不是 `nearCards()`：本页除 60 条
     group=near 之外，还混着 42 条更早的 manual_only 条目（sc_msb_* /
     sc_les_* / sc_ccc_*），它们 id 不带 `sc_near_` 前缀 —— 「全选」的作用域
     是页面上**所有的卡**，用窄选择器会比出 60 vs 102 的假失败。 */
  const allNearCardEls = () => [...panel.querySelectorAll(".av3-sec[data-scene]")];
  ck("特写页有「勾选后随机」工具条（全选 / 反选 / 随机 / 清空）",
    !!npbar() && npbar().querySelectorAll("button").length === 4,
    npbar() ? String(npbar().querySelectorAll("button").length) : "无工具条");
  ck("特写页每张卡都带勾选框",
    allNearCardEls().length > 0
    && allNearCardEls().every((c) => !!c.querySelector("input.av3-pick")),
    allNearCardEls().filter((c) => !c.querySelector("input.av3-pick"))
      .map((c) => c.dataset.scene).join("/"));
  npbar().querySelector("#av3-pick-all").click();
  await sleep(20);
  ck("特写页「☑ 全选」= 本页全部卡片数",
    ncnt() === `${allNearCardEls().length} 条已勾选`,
    `${ncnt()} vs ${allNearCardEls().length}`);
  npbar().querySelector("#av3-pick-invert").click();
  await sleep(20);
  ck("特写页「⇄ 反选」= 全部取消", ncnt() === "0 条已勾选", ncnt());
  /* 只勾一条低档面部条目再随机 —— 结果可预期，也不会把档位抬起来 */
  npbar().querySelector("#av3-pick-clear").click();
  await sleep(60);
  const faceC = nearCards().find((c) => c.dataset.scene === "sc_near_face_classroom");
  const fcb = faceC?.querySelector("input.av3-pick");
  if (fcb) {
    fcb.checked = true;
    fcb.dispatchEvent(new dom.window.Event("change"));
  }
  await sleep(20);
  ck("勾一条后计数为 1、随机按钮解禁",
    ncnt() === "1 条已勾选"
    && npbar().querySelector("#av3-pick-roll").disabled === false, ncnt());
  npbar().querySelector("#av3-pick-roll").click();
  await sleep(80);
  ck("点「🎲 从勾选随机」后该条被套用（卡片带 on）",
    panel.querySelectorAll(".av3-sec[data-scene].on").length === 1
    && /抽到/.test(panel.querySelector("#av3-status")?.textContent || ""),
    panel.querySelector("#av3-status")?.textContent || "");
  ck("抽中即套用，等于手动点「套用」（n_canon 权重 3.0）",
    Number(wjson().fields.n_canon) === 3.0
    && Number(wjson().options.n_canon?.sc_near_face_classroom) === 3.0,
    JSON.stringify(wjson().options.n_canon || {}));
  [...panel.querySelectorAll(".av3-btn")]
    .find((b) => b.textContent.includes("取消套用"))?.click();
  await sleep(40);
  npbar().querySelector("#av3-pick-clear")?.click();
  await sleep(40);
  ck("特写页勾选收尾归零（不污染后续用例）",
    (npbar()?.querySelector(".cnt")?.textContent || "") === "0 条已勾选",
    npbar()?.querySelector(".cnt")?.textContent || "");
}

/* v3.44.0：部位筛选条也能整条收起（与场景页题材条同一套手感） */
const partTog = () => panel.querySelector("#av3-near-parttoggle");
const partBtns = () => [...panel.querySelectorAll("#av3-near-parts button")];
const nParts = (SCENE_FIX.near_parts || []).length;
ck("部位条有整条收起按钮，默认展开",
  /▾/.test(partTog()?.textContent || "") && partBtns().length === nParts + 2,
  `${partTog()?.textContent} / ${partBtns().length} 颗 / near_parts=${nParts}`);
partTog().click();
await sleep(30);
ck("部位条收起后只剩一颗按钮 + 摘要",
  partBtns().length === 1
  && /全部部位/.test(panel.querySelector("#av3-near-partsun")?.textContent || ""),
  `${partBtns().length} 颗 / `
  + `${panel.querySelector("#av3-near-partsun")?.textContent || ""}`);
ck("收起部位条不影响卡片（仍是全部特写）",
  nearCards().length === manualScenes.length, String(nearCards().length));
partTog().click();
await sleep(30);
btnByText("腿部").click();
await sleep(40);
partTog().click();
await sleep(30);
ck("收起后摘要显示当前部位",
  /当前：腿部/.test(panel.querySelector("#av3-near-partsun")?.textContent || ""),
  panel.querySelector("#av3-near-partsun")?.textContent || "（没有摘要）");
ck("收起后部位筛选仍生效",
  nearCards().length === legN
  && nearCards().every((c) => nearPartOf(c.dataset.scene) === "腿部"),
  `${nearCards().length} vs ${legN}`);
panel.querySelector("#av3-near-partclear").click();
await sleep(30);
ck("收起态能直接 ✕ 取消部位",
  !panel.querySelector("#av3-near-partclear")
  && nearCards().length === manualScenes.length,
  String(nearCards().length));
partTog().click();
await sleep(30);
ck("再展开部位条回来", partBtns().length === nParts + 2, String(partBtns().length));

/* 套用腿组：n_canon 命中 + ③② 段自动关 + 角色三件套清空（全部可恢复） */
const wOf = (name) => node.widgets.find((w) => w.name === name).value;
const faceBefore = Number(wjson().sections?.face ?? 1);
const hairBefore = Number(wjson().sections?.hair ?? 1);
// 先摆一份"用户自己的角色设置"进去，才能验证"清了"和"还回来了"
node.widgets.find((w) => w.name === "selected_roles").value = "hatsune miku";
node.widgets.find((w) => w.name === "role_works").value = "genshin impact";
const rolesBefore = String(wOf("selected_roles"));
const legCard = nearCards().find((c) => c.dataset.scene === "sc_near_leg_crossed")
  || nearCards().find((c) => nearPartOf(c.dataset.scene) === "腿部");
const legId = legCard.dataset.scene;
applyBtnOf(legCard).click();
const wN = wjson();
ck("套用特写：n_canon 字段权重 = 3.0", Number(wN.fields?.n_canon) === 3,
  JSON.stringify(wN.fields));
ck("套用特写：本条选项 = 3.0",
  Number(wN.options?.n_canon?.[legId]) === 3,
  JSON.stringify(wN.options?.n_canon?.[legId]));
ck("套用腿组：③ 五官与表情段被设为 0", Number(wN.sections?.face) === 0,
  JSON.stringify(wN.sections));
ck("套用腿组：② 头发段也被设为 0（发色也会锚住脸）",
  Number(wN.sections?.hair) === 0, JSON.stringify(wN.sections));
ck("套用腿组：④ 身材 ⑤ 妆容 段也被设为 0（v3.18.1 极简）",
  Number(wN.sections?.body) === 0 && Number(wN.sections?.makeup) === 0,
  JSON.stringify(wN.sections));
ck("套用腿组：竞争字段权重被设为 0（服装/身材/动作/设备/构图）",
  ["outfit", "top", "bottom", "figure", "action", "device", "comp"]
    .every((k) => Number(wN.fields?.[k]) === 0),
  JSON.stringify(wN.fields));
ck("套用腿组：人数钉死单人（1girl, solo = 3.0，其它 0）",
  Number(wN.options?.count?.["1girl, solo"]) === 3
  && Object.entries(wN.options?.count || {})
    .filter(([k]) => k !== "1girl, solo").every(([, v]) => Number(v) === 0),
  JSON.stringify(wN.options?.count));
ck("套用腿组：已选角色被清空", String(wOf("selected_roles") || "") === "",
  String(wOf("selected_roles")));
ck("套用腿组：限定作品池被清空", String(wOf("role_works") || "") === "",
  String(wOf("role_works")));
ck("套用腿组：role 被加进锁定字段（否则引擎仍会必抽 1 位角色）",
  /(^|[,\s])role([,\s]|$)/.test(String(wOf("lock_fields") || "")),
  String(wOf("lock_fields")));
ck("套用腿组：状态条明说收紧取景（不静默）",
  /收紧取景/.test(panel.querySelector("#av3-status")?.textContent || ""),
  panel.querySelector("#av3-status")?.textContent || "");
ck("特写页状态条显示当前套用",
  /当前套用/.test(panel.querySelector("#av3-near-cur")?.textContent || ""),
  panel.querySelector("#av3-near-cur")?.textContent || "");
ck("已套用卡按钮禁用",
  applyBtnOf(panel.querySelector(`.av3-sec[data-scene="${legId}"]`)).disabled === true);

[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("取消套用")).click();
const wN2 = wjson();
ck("取消套用：n_canon 选项清空",
  !wN2.options?.n_canon || Object.keys(wN2.options.n_canon).length === 0,
  JSON.stringify(wN2.options?.n_canon));
ck("取消套用：③ 段恢复原值",
  Number(wN2.sections?.face ?? 1) === (faceBefore === 0 ? 1 : faceBefore),
  `${wN2.sections?.face} vs ${faceBefore}`);
ck("取消套用：② 段恢复原值",
  Number(wN2.sections?.hair ?? 1) === (hairBefore === 0 ? 1 : hairBefore),
  `${wN2.sections?.hair} vs ${hairBefore}`);
ck("取消套用：④⑤ 段 / 被收的字段 / 人数袋 全部还回去",
  wN2.sections?.body === undefined && wN2.sections?.makeup === undefined
  && wN2.fields?.outfit === undefined && wN2.fields?.figure === undefined
  && wN2.options?.count === undefined,
  JSON.stringify({ s: wN2.sections, f: wN2.fields, c: wN2.options?.count }));
ck("取消套用：角色选择原样还回去",
  String(wOf("selected_roles") || "") === rolesBefore
  && String(wOf("role_works") || "") === "genshin impact",
  `${wOf("selected_roles")} / ${wOf("role_works")}`);
ck("取消套用：锁定字段表也还回去（role 不残留）",
  !/(^|[,\s])role([,\s]|$)/.test(String(wOf("lock_fields") || "")),
  String(wOf("lock_fields")));
ck("取消后特写页状态条回到未套用",
  /未套用/.test(panel.querySelector("#av3-near-cur")?.textContent || ""),
  panel.querySelector("#av3-near-cur")?.textContent || "");

/* 套用面部组：角色与表情必须**保留**（需求 1） */
const faceCard = nearCards().find((c) => nearPartOf(c.dataset.scene) === "面部");
const faceId = faceCard.dataset.scene;
node.widgets.find((w) => w.name === "selected_roles").value = "hatsune miku";
const rolesFace = String(wOf("selected_roles"));
applyBtnOf(faceCard).click();
await sleep(30);
const wF = wjson();
ck("套用面部组：selected_roles **没被清空**", String(wOf("selected_roles")) === rolesFace,
  `${wOf("selected_roles")} vs ${rolesFace}`);
ck("套用面部组：没有把 role 加进锁定字段",
  !/(^|[,\s])role([,\s]|$)/.test(String(wOf("lock_fields") || "")),
  String(wOf("lock_fields")));
ck("套用面部组：② 头发 / ③ 五官段**保持不动**",
  wF.sections?.hair === undefined && wF.sections?.face === undefined,
  JSON.stringify(wF.sections));
ck("套用面部组：④ 身材 / ⑤ 妆容段被关",
  Number(wF.sections?.body) === 0 && Number(wF.sections?.makeup) === 0,
  JSON.stringify(wF.sections));
// ⚠️ `comp` 可能被预设自己写死（sc_near_face_classroom 就写了 comp=portrait），
//    那种字段会被 slimFieldsFor 自动保留 —— 所以只断言"一定该为 0"的那几个。
ck("套用面部组：姿势/手部/设备字段被收（不再拉成上半身）",
  ["action", "arms_hands", "device"].every((k) => Number(wF.fields?.[k]) === 0),
  JSON.stringify(wF.fields));
ck("套用面部组：看不见的服装与鞋袜权重为 0（正文不再描述画面外）",
  ["outfit", "top", "bottom", "socks", "shoes", "leg"].every((k) => Number(wF.fields?.[k]) === 0),
  JSON.stringify(wF.fields));
ck("面部组状态条也是「收紧取景」",
  /收紧取景/.test(panel.querySelector("#av3-status")?.textContent || ""),
  panel.querySelector("#av3-status")?.textContent || "");
[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("取消套用")).click();
await sleep(20);
ck("取消后角色与段权重都还回去（面部组）",
  String(wOf("selected_roles")) === rolesFace
  && wjson().sections?.body === undefined,
  JSON.stringify(wjson().sections));

/* 成人向：有脸条目保留角色 + 双女人数钉死（v3.18.3） */
btnByText("🔞 成人向").click();
await sleep(40);
ck("成人向分组里也有「🎭 角色来源」栏",
  !!panel.querySelector("#av3-near-rolesrc"),
  panel.querySelector("#av3-near-rolesrc")?.textContent || "（没有）");
ck("成人向的角色来源标题写明「仅标了角色生效的条目」",
  /角色生效/.test(panel.querySelector("#av3-near-rolelabel")?.textContent || ""),
  panel.querySelector("#av3-near-rolelabel")?.textContent || "");
const adultCard = (id) => panel.querySelector(`.av3-sec[data-scene="${id}"]`);
ck("口交特写标了「🎭 角色生效」",
  /角色生效/.test(adultCard("sc_near_nsfw_fellatio")?.textContent || ""),
  adultCard("sc_near_nsfw_fellatio")?.textContent?.slice(0, 80));
ck("乳首特写仍是「🧹 清角色」（无脸）",
  /清角色/.test(adultCard("sc_near_nsfw_nipple")?.textContent || "")
  && !/角色生效/.test(adultCard("sc_near_nsfw_nipple")?.textContent || ""),
  adultCard("sc_near_nsfw_nipple")?.textContent?.slice(0, 80));
ck("舔阴特写标了「👭 双女」",
  /双女/.test(adultCard("sc_near_nsfw_cunnilingus")?.textContent || ""),
  adultCard("sc_near_nsfw_cunnilingus")?.textContent?.slice(0, 80));

/* 切到过激档才套得动 L3 的双女条目 */
lvBtn("过激").click();
await sleep(50);
nearTab.click();
await sleep(30);
btnByText("🔞 成人向").click();
await sleep(40);
const cuCard = panel.querySelector('.av3-sec[data-scene="sc_near_nsfw_cunnilingus"]');
ck("过激档下双女条目可见", !!cuCard);
node.widgets.find((w) => w.name === "selected_roles").value = "hatsune miku";
applyBtnOf(cuCard).click();
await sleep(30);
const wCu = wjson();
ck("双女套用后人数钉死 2girls, yuri",
  Number(wCu.options?.count?.["2girls, yuri"]) === 3
  && Object.entries(wCu.options?.count || {})
    .filter(([k]) => k !== "2girls, yuri").every(([, v]) => Number(v) === 0),
  JSON.stringify(wCu.options?.count));
/* v6.6.1：**改口径** —— 以前套用双女条目会把已选角色清空（v3.18 的机制，
   那时引擎还不会补位）。v6.6.0 起引擎会自动补一位同作品女性，前端再清
   就等于扔掉用户点的角色、换成两位全随机，所以现在必须**保留**。 */
ck("已选角色只有 1 位 → 保留（交给引擎补一位同作品女性）",
  String(wOf("selected_roles") || "") === "hatsune miku", String(wOf("selected_roles")));
ck("状态条说明「保留 + 引擎补位」",
  /补一位|补位/.test(panel.querySelector("#av3-status")?.textContent || ""),
  panel.querySelector("#av3-status")?.textContent || "");
ck("双女条目没有把 role 锁死（要抽两位角色）",
  !/(^|[,\s])role([,\s]|$)/.test(String(wOf("lock_fields") || "")),
  String(wOf("lock_fields")));
[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("取消套用")).click();
await sleep(20);
ck("取消后人数袋与角色都还回去",
  wjson().options?.count === undefined
  && String(wOf("selected_roles") || "") === "hatsune miku",
  `${JSON.stringify(wjson().options?.count)} / ${wOf("selected_roles")}`);
lvBtn("全年龄").click();
await sleep(40);
nearTab.click();
await sleep(30);

/* 基线持久化（v3.18.5）—— 修"腿部服装随机不生效" */
node.widgets.find((w) => w.name === "lock_fields").value = "";
btnByText("全部").click();
await sleep(30);
const faceCard5 = nearCards().find((c) => c.dataset.scene === "sc_near_face_classroom");
applyBtnOf(faceCard5).click();
await sleep(40);
const wFace = wjson();
ck("套用面部后 socks/shoes 权重被清零（白名单不含袜鞋）",
  Number(wFace.fields?.socks) === 0 && Number(wFace.fields?.shoes) === 0,
  JSON.stringify({ socks: wFace.fields?.socks, shoes: wFace.fields?.shoes }));
ck("清零前的原值写进 weights_json 的 _nearBase（刷新也还原得回来）",
  !!(wFace._nearBase && wFace._nearBase.fields),
  Object.keys(wFace._nearBase || {}).join(","));
ck("_nearBase 记下了 socks/shoes 的原始值",
  "socks" in (wFace._nearBase?.fields || {}) && "shoes" in (wFace._nearBase?.fields || {}),
  JSON.stringify(wFace._nearBase?.fields || {}).slice(0, 120));

/* 不取消、直接换套腿部 —— 旧实现若内存快照丢了就永远恢复不回来 */
// v3.38.1：部分腿部条目改成"不穿鞋"，闭麦了 shoes —— 它们本来就会让 shoes 停在 0，
// 不能再拿"任意第一条腿部"当恢复样本，这里指定一条未闭麦鞋的腿部条目。
const legCard3 = nearCards().find((c) => c.dataset.scene === "sc_near_leg_crossed")
  || nearCards().find((c) => nearPartOf(c.dataset.scene) === "腿部");
applyBtnOf(legCard3).click();
await sleep(40);
const wLeg = wjson();
ck("换套腿部后 socks/shoes 权重恢复（不再是 0）",
  Number(wLeg.fields?.socks ?? 1) !== 0 && Number(wLeg.fields?.shoes ?? 1) !== 0,
  JSON.stringify({ socks: wLeg.fields?.socks, shoes: wLeg.fields?.shoes }));
ck("腿部条目自身仍保留袜鞋字段（白名单里没漏）",
  Number(wLeg.fields?.socks ?? 1) !== 0,
  JSON.stringify(wLeg.fields));

[...panel.querySelectorAll(".av3-btn")]
  .find((b) => b.textContent.includes("取消套用")).click();
await sleep(30);
const wEnd = wjson();
ck("取消套用后 _nearBase 从 weights_json 消失", !wEnd._nearBase,
  JSON.stringify(wEnd._nearBase || "none"));
ck("取消套用后 socks/shoes 不再是 0",
  Number(wEnd.fields?.socks ?? 1) !== 0 && Number(wEnd.fields?.shoes ?? 1) !== 0,
  JSON.stringify({ socks: wEnd.fields?.socks, shoes: wEnd.fields?.shoes }));

/* 切回场景页，确认两页互不干扰 */
tabBtns.find((b) => b.textContent.includes("场景搭配")).click();
await sleep(40);
await loadAllScenes();       // 懒加载：全量断言前先放出来
ck("切回场景页后卡片数仍是非特写数",
  sceneCards().length === nonManualCount, `${sceneCards().length} vs ${nonManualCount}`);

/* v3.25.0：关闭前把界面调成一个"可辨识"的状态，供后面验证记忆 */
/* v5.1.0：场景页的「🧎 姿势场景」筛选条已随页签升级撤掉 ——
   换成一个同样"可辨识"的状态：停在「🧘 姿势库」页签，页内选「瑜伽」。 */
[...panel.querySelectorAll(".av3-tabs button")]
  .find((b) => b.textContent.includes("姿势库")).click();
await sleep(80);
[...panel.querySelectorAll("#av3-pose-cats button")]
  .find((b) => b.dataset.cat === "yoga").click();
await sleep(60);
// ⚠️ 点完 renderScenes() 会整块重建分类条，旧引用是**游离节点**，
//    必须重新查一次才能读到选中态（否则永远看到旧 class）。
const poseBtnAfter = [...document.body.querySelectorAll("#av3-pose-cats button")]
  .find((b) => b.dataset.cat === "yoga");
ck("关闭前已切到「🧘 姿势库 · 瑜伽」",
  !!poseBtnAfter && poseBtnAfter.classList.contains("pri"),
  poseBtnAfter ? poseBtnAfter.className : "无该按钮");

/* ── 11. 关闭面板 ────────────────────────────────────────────────── */
[...panel.querySelectorAll(".av3-btn")].find((b) => b.textContent === "关闭").click();
// 10.9 之一里为兼容断言开过第二个面板（oldNode 的），一并关掉
[...document.body.querySelectorAll(".av3-mask .av3-btn")]
  .filter((b) => b.textContent === "关闭").forEach((b) => b.click());
ck("面板已关闭", !document.body.querySelector(".av3-mask"));

/* ── 12. v3.25.0 界面记忆：关掉再打开，回到上次那一幕 ─────────────── */
const memo = wjson()._ui || {};
ck("关闭时把界面记忆写进 weights_json._ui", !!memo && typeof memo === "object",
  JSON.stringify(memo).slice(0, 120));
ck("记住的页签是「姿势库」", memo.tab === "pose", String(memo.tab));
ck("记住的姿势分类是「瑜伽」（v5.1.0 新状态）",
  memo.poseCat === "yoga", String(memo.poseCat));
// v3.44.0：两条筛选条的收起态也要跟着记（否则每次重开都回到展开）
ck("记住的题材条 / 部位条收起态都是布尔",
  typeof memo.sceneThemeOpen === "boolean" && typeof memo.nearPartOpen === "boolean",
  JSON.stringify({ t: memo.sceneThemeOpen, p: memo.nearPartOpen }));
ck("记住的展开态是数组（段的 openSecs）", Array.isArray(memo.openSecs),
  JSON.stringify(memo.openSecs));
ck("记住的滚动是比例 0–1（left / right）",
  !!memo.scroll && Number(memo.scroll.left) >= 0 && Number(memo.scroll.left) <= 1
  && Number(memo.scroll.right) >= 0 && Number(memo.scroll.right) <= 1,
  JSON.stringify(memo.scroll));

openBtn.callback();
for (let i = 0; i < 80 && !document.body.querySelector(".av3-panel"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const panel2 = document.body.querySelector(".av3-panel");
ck("面板重新打开", !!panel2);
const onTab2 = panel2 && [...panel2.querySelectorAll(".av3-tabs button")]
  .find((b) => b.classList.contains("on"));
ck("重开后停在上次的页签（姿势库）",
  !!onTab2 && onTab2.textContent.includes("姿势库"),
  onTab2 ? onTab2.textContent : "无");
await sleep(120);
// 姿势库是懒加载页：重开面板后要先等 /scenes 回来才渲染分类条
for (let i = 0; i < 120
  && !(panel2 && panel2.querySelector("#av3-pose-cats button[data-cat='yoga']")); i++) {
  await sleep(10);
}
const poseBtn2 = panel2 && [...panel2.querySelectorAll("#av3-pose-cats button")]
  .find((b) => b.dataset.cat === "yoga");
ck("重开后「🧘 瑜伽」分类仍是选中态",
  !!poseBtn2 && poseBtn2.classList.contains("pri"),
  (poseBtn2 ? poseBtn2.className : "无") + " | memo=" + String(memo.poseCat)
  + " | cats=" + [...(panel2 ? panel2.querySelectorAll("#av3-pose-cats button") : [])]
    .map((b) => b.dataset.cat + ":" + b.className).join(","));

/* 收尾前把第二个面板也关掉，避免残留 mask 影响后续断言 */
[...document.body.querySelectorAll(".av3-mask .av3-btn")]
  .filter((b) => b.textContent === "关闭").forEach((b) => b.click());
ck("重开的面板已关闭", !document.body.querySelector(".av3-mask"));

/* ── v5.0.23：艺术构图页签也要能记住 ────────────────────────────────
   用户 2026-11-04 20:58：「停留在艺术构图界面后关闭再打开，就不是停留的界面」。
   根因是记忆还原的白名单 `_TABS` 漏了 "art"，落到 default 的 weights。
   这里跑一整轮「停在艺术构图 → 关闭 → 重开」来钉死它。 */
openBtn.callback();
for (let i = 0; i < 80 && !document.body.querySelector(".av3-panel"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const panel3 = document.body.querySelector(".av3-panel");
const artTab3 = panel3 && [...panel3.querySelectorAll(".av3-tabs button")]
  .find((b) => b.textContent.includes("艺术构图"));
ck("第三个面板打开且有艺术构图页签", !!artTab3);
if (artTab3) artTab3.click();
await sleep(80);
[...document.body.querySelectorAll(".av3-mask .av3-btn")]
  .filter((b) => b.textContent === "关闭").forEach((b) => b.click());
await sleep(30);
ck("停在艺术构图时，记忆里写的是 art", wjson()._ui?.tab === "art",
  String(wjson()._ui?.tab));
openBtn.callback();
for (let i = 0; i < 80 && !document.body.querySelector(".av3-panel"); i++) {
  await new Promise((r) => setTimeout(r, 10));
}
const panel4 = document.body.querySelector(".av3-panel");
const onTab4 = panel4 && [...panel4.querySelectorAll(".av3-tabs button")]
  .find((b) => b.classList.contains("on"));
ck("重开后停在艺术构图（v5.0.23 修的 bug）",
  !!onTab4 && onTab4.textContent.includes("艺术构图"),
  onTab4 ? onTab4.textContent : "无");
ck("重开到艺术构图时没有分组条（页签即分组）",
  !panel4?.querySelector("#av3-scene-groups"));
[...document.body.querySelectorAll(".av3-mask .av3-btn")]
  .filter((b) => b.textContent === "关闭").forEach((b) => b.click());

/* ── 收尾 ────────────────────────────────────────────────────────── */
fs.rmSync(TMP, { recursive: true, force: true });
console.log(`\n[av3_ui_smoke] 通过 ${PASS} / 失败 ${FAILS.length}`);
if (FAILS.length) {
  console.log("失败用例：" + FAILS.join(" · "));
  process.exit(1);
}
process.exit(0);
