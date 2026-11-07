/**
 * Anima 提示词随机生成（V6）· 可视化面板
 * ---------------------------------------------------------------------------
 * 挂在节点 AnimaV6RandomPrompt 上，点「打开随机面板」弹出。
 * （V4 / V3 是同一个类的别名，面板也认 —— 见下方 NODE_TYPES。）
 *
 * 面板与后端共用同一套随机引擎（POST /anima-wardrobe-v6/roll 与节点
 * 调的是同一个 R.draw），所以面板里看到的结果 = 定下种子后工作流跑出来的结果。
 * 这就是「采用这条」按钮的底气：它只是把 seed 写回节点控件。
 *
 * 界面口径（沿用本项目既有约定）：
 *   · 能在面板里配的项，节点上不重复外露（所以节点上只有 9 个控件）；
 *   · 动作类按钮（随机 / 换种子 / 清空）与入口按钮保留在节点上；
 *   · 权重用三层：段 / 字段 / 选项，0 = 关闭，1.0 = 默认；
 *   · 🎯 三态指示：🎯 未调 / 🎯 n 调了 n 项 / 🎯 全部 全部非默认。
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NS = "anima-wardrobe-v6";
// 缩略图 URL 的版本号（v5.0.12）。
// ⚠️ 为什么要这个：浏览器对 `thumbs/xxx.webp` 这类同名文件会长期缓存，
//    服务端换了图也照样吃缓存（实测：后端 Last-Modified 已是新时间，
//    浏览器一次都没回源，面板看起来「没更新」）。
//    给 URL 带一个版本参数后，版本一变就是新 URL，浏览器必然重新拉取。
//    **换图后如果想让所有人立刻看到，把这里加一版即可**（同时后端
//    Cache-Control 已经是 no-cache，双保险）。
const THUMB_VER = "5.0.12";
const thumbURL = (slug) => `/${NS}/thumb/${slug}?v=${THUMB_VER}`;
const NODE_TYPE = "AnimaV6RandomPrompt";

/** 面板要挂上去的**全部**节点类型名。
 *
 *  v6.0.0 起主名是 AnimaV6*，但 NODE_CLASS_MAPPINGS 里 V5 / V4 / V3 仍作为别名注册
 *  （工作流 JSON 存的是节点类型名，删了别名旧工作流就报「节点缺失」）。
 *  所以这里必须四种都认 —— 只认 NODE_TYPE 的话，打开任何旧工作流，
 *  节点上都没有「打开随机面板」按钮。 */
const NODE_TYPES = new Set([
  NODE_TYPE,
  "AnimaV5RandomPrompt",
  "AnimaV4RandomPrompt",
  "AnimaV3RandomPrompt",
]);

/** 「⭐ 我的收藏」这个伪作品 id。
 *  真作品 id 都是 slug（小写字母数字下划线），不会出现双下划线开头，撞不上。 */
const FAV_ID = "__fav__";

const LEVEL_SHORT = {
  sfw: "0 全年龄",
  suggestive: "1 擦边",
  explicit: "2 成人",
  hardcore: "3 过激",
  extreme: "4 极端",
  any: "不限",
};

/* ── 状态：只放进程内缓存，节点值才是真源 ───────────────────────────── */
const CACHE = { pools: null, contract: null, loading: null };

async function fetchJSON(url) {
  const r = await api.fetchApi(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
  return await r.json();
}

function ensureData() {
  if (!CACHE.loading) {
    CACHE.loading = Promise.all([
      fetchJSON(`/${NS}/pools`),
      fetchJSON(`/${NS}/contract`),
    ]).then(([p, c]) => {
      CACHE.pools = p;
      CACHE.contract = c;
      return CACHE;
    });
  }
  return CACHE.loading;
}

/* ── 节点控件读写 ─────────────────────────────────────────────────── */
function wget(node, name, dflt = "") {
  const w = (node.widgets || []).find((x) => x.name === name);
  return w ? w.value : dflt;
}

function wset(node, name, value) {
  const w = (node.widgets || []).find((x) => x.name === name);
  if (!w) return false;
  w.value = value;
  if (w.callback) {
    try {
      w.callback(value, app.canvas, node, [0, 0], null);
    } catch (e) {
      /* 某些控件没有 callback 参数契约，忽略即可，值已经写进去了 */
    }
  }
  return true;
}

/* ── 权重表读写（控件里存的是 JSON 文本）─────────────────────────── */
function loadWeights(node) {
  const raw = wget(node, "weights_json", "");
  const empty = { sections: {}, fields: {}, options: {} };
  if (!raw) return empty;
  try {
    const d = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!d || typeof d !== "object") return empty;
    return {
      sections: d.sections && typeof d.sections === "object" ? { ...d.sections } : {},
      fields: d.fields && typeof d.fields === "object" ? { ...d.fields } : {},
      options: d.options && typeof d.options === "object" ? { ...d.options } : {},
      // v3.18.5：特写套用前的「原始权重基线」。放在 weights_json 里而不是内存变量，
      // 这样刷新 / 节点重开 / 换工作流之后仍能把被清零的字段还原回去。
      _nearBase: d._nearBase && typeof d._nearBase === "object" ? d._nearBase : undefined,
      // v3.23.0：等级自动同步的两个附加键（引擎忽略下划线键）
      _autoLevel: typeof d._autoLevel === "boolean" ? d._autoLevel : undefined,
      _levelBase: d._levelBase && typeof d._levelBase === "object" ? d._levelBase : undefined,
      // v3.25.0：界面记忆（页签 / 滚动 / 展开 / 筛选）。同样挂在 weights_json 里，
      // 随工作流保存；引擎只认 sections/fields/options 与下划线键的白名单，不会把它喂给随机。
      _ui: d._ui && typeof d._ui === "object" ? d._ui : undefined,
    };
  } catch (e) {
    // 与后端 parse_weights 同一口径：非法 JSON 当空表，不弹错
    return empty;
  }
}

function saveWeights(node, w) {
  const out = {};
  const strip = (bag) => {
    const o = {};
    for (const [k, v] of Object.entries(bag || {})) {
      const f = Number(v);
      if (Number.isFinite(f) && Math.abs(f - 1) > 1e-9) o[k] = Math.round(f * 1000) / 1000;
    }
    return o;
  };
  const s = strip(w.sections);
  const f = strip(w.fields);
  if (Object.keys(s).length) out.sections = s;
  if (Object.keys(f).length) out.fields = f;
  const o = {};
  for (const [fk, sub] of Object.entries(w.options || {})) {
    const clean = strip(sub);
    if (Object.keys(clean).length) o[fk] = clean;
  }
  if (Object.keys(o).length) out.options = o;
  if (w._nearBase) out._nearBase = w._nearBase;
  // v3.23.0：等级自动同步的开关与撤销基线（引擎忽略下划线键，只占存储）
  if (typeof w._autoLevel === "boolean") out._autoLevel = w._autoLevel;
  if (w._levelBase) out._levelBase = w._levelBase;
  // v3.25.0：界面记忆
  if (w._ui && typeof w._ui === "object") out._ui = w._ui;
  wset(node, "weights_json", Object.keys(out).length ? JSON.stringify(out, null, 1) : "");
}

/** 某个字段被调过的选项数 */
function tunedCount(w, fieldKey) {
  return Object.keys(w.options?.[fieldKey] || {}).length;
}

/** 🎯 三态：0 未调 / 1 部分 / 2 全部 */
function weightBadge(w, fieldKey, total) {
  const n = tunedCount(w, fieldKey);
  if (!n) return { text: "🎯", state: 0 };
  if (total && n >= total) return { text: "🎯 全部", state: 2 };
  return { text: `🎯 ${n}`, state: 1 };
}

/* ── 免责声明 ───────────────────────────────────────────────────────
   ⚠️ 这里的文案是**用户逐字给的**，不要改写、不要精简、不要调整措辞。
      改文案属于内容变更，得先跟用户确认。
      展示位置：面板头部「⚠ 免责声明」按钮 → 大弹窗（可滚动）。        */
const DISCLAIMER_TITLE = "免责声明与使用条款";
const DISCLAIMER_INTRO =
  "本插件为开源插件（Comfyui-Anima-Wardrobe-V6），按原样提供、"
  + "用于 ComfyUI 工作流内的提示词生成。生成过程全部在你本机完成，"
  + "不联网、不上传任何内容。十八岁以下禁止使用。";
const DISCLAIMER_BYLINE = [
  "插件作者：灵冻不冻",
  "插件底层框架源于脚本：ANIMA提示词生成器.html 已授权开源 —— 作者：飞蓬。",
];
const DISCLAIMER_ITEMS = [
  "① 所有角色一律视为18 岁以上虚构人物，禁止用于真实人物、未成年人、非自愿及暴力伤害相关内容。",
  "② 角色名与作品名等知识产权归各自权利人所有，此处仅作提示词检索与描述；角色头像取自公开图库，仅用于识别对照，不作他用。",
  "③ 提示词是词库模板的随机组合，不保证每条都符合预期 —— 正式出片前请自行筛选；同一设置换个种子结果就不同。",
  "④ 调高「内容强度」会让措辞明显更露骨，请自行确认是否符合你所在平台的发布政策。",
  "⑤ 生成内容及其用途由你自行负责，本插件不对由此产生的任何后果承担责任。",
];

/** 打开免责声明弹窗。返回弹窗根节点，方便测试与外部关闭。 */
function openDisclaimer() {
  const maskEl = document.createElement("div");
  maskEl.className = "av3-mask";
  const inner = document.createElement("div");
  inner.className = "av3-mini";
  inner.style.position = "static";
  maskEl.appendChild(inner);

  const box = document.createElement("div");
  box.className = "card av3-disc";
  inner.appendChild(box);

  const hd = document.createElement("div");
  hd.className = "hd";
  hd.textContent = DISCLAIMER_TITLE;
  box.appendChild(hd);

  const bd = document.createElement("div");
  bd.className = "bd";
  const lead = document.createElement("p");
  lead.className = "lead";
  lead.textContent = DISCLAIMER_INTRO;
  bd.appendChild(lead);
  for (const line of DISCLAIMER_BYLINE) {
    const p = document.createElement("p");
    p.className = "by";
    p.textContent = line;
    bd.appendChild(p);
  }
  for (const it of DISCLAIMER_ITEMS) {
    const p = document.createElement("p");
    p.className = "it";
    p.textContent = it;
    bd.appendChild(p);
  }
  box.appendChild(bd);

  const ft = document.createElement("div");
  ft.className = "ft";
  const ok = document.createElement("button");
  ok.className = "av3-btn pri";
  ok.textContent = "我已阅读";
  ok.onclick = () => maskEl.remove();
  ft.appendChild(ok);
  box.appendChild(ft);

  // 点遮罩空白处也能关（与面板里其它弹窗一致）
  maskEl.onclick = (e) => {
    if (e.target === maskEl) maskEl.remove();
  };
  document.body.appendChild(maskEl);
  return maskEl;
}

/* ── 一次性注入样式 ───────────────────────────────────────────────── */
const CSS = `
.av3-mask{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;display:flex;align-items:center;justify-content:center;}
/* v5.5.0（用户 2026-11-05 21:27「面板打开后再放大一点（文字不放大）」）
   → 只放大容器，字号一个都没动。角色网格是 auto-fill minmax(96px)，
  所以宽出来的部分会自动变成更多列，不需要改任何布局规则。
   1180×840 → 1520×980，1080p 屏放得下。
   ⚠️ 这条注释在 CSS 模板字符串里，**不能写反引号** —— 会把字符串截断。 */
.av3-panel{width:min(1520px,96vw);height:min(980px,94vh);display:flex;flex-direction:column;
  background:var(--comfy-menu-bg,#1e1e1e);color:var(--fg-color,#ddd);
  border:1px solid var(--border-color,#444);border-radius:10px;overflow:hidden;font-size:13px;}
.av3-head{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border-color,#444);flex-wrap:wrap;}
.av3-head h3{margin:0;font-size:14px;font-weight:600;color:#e8c98a;}
.av3-lv{display:flex;gap:0;border:1px solid var(--border-color,#444);border-radius:7px;overflow:hidden;}
.av3-lv button{border:0;background:transparent;color:var(--fg-color,#bbb);padding:6px 12px;cursor:pointer;font-size:12px;}
.av3-lv button.on{background:#d9b36c;color:#241a05;font-weight:600;}
.av3-btn{border:1px solid var(--border-color,#555);background:transparent;color:var(--fg-color,#ccc);
  border-radius:7px;padding:6px 12px;cursor:pointer;font-size:12px;}
.av3-btn:hover{border-color:#d9b36c;color:#e8c98a;}
.av3-btn.pri{background:#d9b36c;border-color:#d9b36c;color:#241a05;font-weight:600;}
.av3-btn:disabled{opacity:.4;cursor:not-allowed;}
.av3-body{flex:1;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,430px);overflow:hidden;}
.av3-left{overflow:auto;padding:10px 12px;border-right:1px solid var(--border-color,#444);}
.av3-right{overflow:auto;padding:10px 12px;display:flex;flex-direction:column;gap:10px;}
.av3-sec{border:1px solid var(--border-color,#3a3a3a);border-radius:8px;margin-bottom:8px;overflow:hidden;}
.av3-sec>.hd{display:flex;align-items:center;gap:8px;padding:7px 10px;background:rgba(255,255,255,.035);cursor:pointer;}
.av3-sec>.hd .t{flex:1;font-weight:600;color:#e8c98a;font-size:12.5px;}
.av3-sec>.bd{padding:8px 10px;display:none;}
.av3-sec.open>.bd{display:block;}
.av3-sec.off{opacity:.45;}
.av3-sec>.hd .th{font-size:11.5px;color:#9a9a9a;}
.av3-sec>.hd .arw{font-size:10px;color:#9a9a9a;width:9px;}
/* 场景卡的动作行与预览框是 .av3-sec 的**直接子元素**（不在 .bd 里）——
   否则折叠时会把「一键套用」一起藏掉，必须展开才能套用。 */
.av3-sceneact{display:flex;gap:6px;flex-wrap:wrap;padding:0 10px 9px;}
.av3-sceneout{margin:0 10px 9px;max-height:220px;}
/* ── v3.43.0：场景卡收藏星标 / 紧凑模式 / 可折叠档位标题 ──
   星标用独立类名 .av3-sfav（不叫 .fav）—— .av3-card .fav 是**绝对定位**的
   角标，场景卡在 flex 标题行里，除非 relative 否则会飘到面板左上角。 */
.av3-sec>.hd .av3-sfav{font-size:14px;line-height:1;color:#7d7d7d;cursor:pointer;
  padding:0 2px;user-select:none;flex:0 0 auto;}
.av3-sec>.hd .av3-sfav:hover{color:#e8c98a;}
.av3-sec>.hd .av3-sfav.on{color:#f5c451;text-shadow:0 0 6px rgba(245,196,81,.45);}
/* v5.3.1（用户 2026-11-05 19:23「服装库浏览改为一行两列，方便浏览」）：
   110 套单列要滚很久，改成两列网格；**展开的卡跨两列**（不然 250px 宽的卡里
   十几个 chip 会堆成一条长柱，右边还空一大块）。 */
#av3-outfitlib-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:7px;align-items:start;}
#av3-outfitlib-list>.av3-sec{margin-bottom:0;}
#av3-outfitlib-list>.av3-sec.open{grid-column:1 / -1;}
#av3-outfitlib-list>.av3-sec>.hd{padding:6px 8px;gap:5px;flex-wrap:wrap;}
#av3-outfitlib-list>.av3-sec>.hd .t{font-size:12px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;}
#av3-outfitlib-list>.av3-sec>.hd .th{font-size:10.5px;}
/* v5.0.1：推荐画幅徽标（竖 / 横 / 通）—— 与档位、字段数同排，折叠时也看得见 */
.av3-aspect{flex:0 0 auto;font-size:10px;line-height:1;padding:2px 5px;
  border-radius:4px;border:1px solid #444;color:#a9a9a9;
  background:rgba(255,255,255,.04);user-select:none;}
.av3-aspect-portrait{color:#86b7ea;border-color:#31506e;background:rgba(86,150,214,.14);}
.av3-aspect-landscape{color:#8ed49b;border-color:#2f5238;background:rgba(90,186,120,.14);}
.av3-aspect-both{color:#b0b0b0;border-color:#4a4a4a;background:rgba(255,255,255,.05);}
.av3-lvhead{cursor:pointer;user-select:none;}
.av3-lvhead:hover{color:#f0d9a8;}
.av3-lvhead.closed{opacity:.72;}
.av3-favhead{margin:4px 0 6px;color:#f5c451;}
/* 紧凑模式：压间距与字号，**不藏动作行** —— 「一键套用」是这一页的主操作，
   藏起来等于为了密度牺牲可用性。密度靠压行高与字号拿。 */
.av3-sec.compact{margin-bottom:4px;border-radius:6px;}
.av3-sec.compact>.hd{padding:3px 8px;gap:6px;}
.av3-sec.compact>.hd .t{font-weight:500;font-size:12px;}
.av3-sec.compact>.hd .th,.av3-sec.compact>.hd .av3-meta{font-size:10.5px;}
.av3-sec.compact>.bd{padding:5px 8px;}
.av3-sec.compact>.bd .av3-chip{font-size:11px;padding:1px 8px;}
.av3-sec.compact .av3-sceneact{padding:0 8px 5px;gap:4px;}
.av3-sec.compact .av3-sceneact .av3-btn{padding:2px 8px;font-size:11px;border-radius:5px;}
/* ── v4.5.41：场景卡改成 3 列网格（用户 2026-10-31「一行显示 3 条」）──────
   面板左栏是 minmax(0,1fr)（右侧固定 430），实际约 1100px → 每格 ≈350px；
   窄屏（<900）退成 2 列，再窄退 1 列，避免标题被挤成一串省略号。
   ⚠️ 展开的那一张用 grid-column:1/-1 横跨整行 —— 字段列表与预览框在 350px
      里没法看，展开就是"我要细看这一条"的意思。 */
/* ⚠️ 用 auto-fit + minmax(280px,1fr)，**不用 @media**：面板宽度由 litegraph 决定，
   不是视口宽度 —— 媒体查询在这里量的是浏览器窗口，窄面板照样硬排 3 列。
   auto-fit 按容器实际宽度算：~1100px 三列、~700px 两列、再窄一列。 */
.av3-scenegrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:8px;margin-bottom:8px;align-items:start;}
/* v6.4.0：姿势库 / 武器场景改一行两列。
   这两个页签的标题是「《作品名》+ 武器名」，三列时宽度不够，
   被 .t 的 ellipsis 截成「瑜…」「⚔ 明日…」，认不出是哪条。
   列宽翻倍后标题基本能一行显示完整。其它页签仍是 auto-fit 三列。 */
.av3-scenegrid.two{grid-template-columns:repeat(2,minmax(0,1fr));}
.av3-scenegrid>.av3-sec{margin-bottom:0;}
.av3-scenegrid>.av3-sec.open{grid-column:1 / -1;}
.av3-scenegrid>.av3-sec>.hd{padding:6px 8px;gap:5px;}
.av3-scenegrid>.av3-sec>.hd .t{font-size:12px;min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.av3-scenegrid>.av3-sec>.hd .th{font-size:10.5px;max-width:110px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.av3-scenegrid .av3-sceneact{padding:0 8px 8px;gap:4px;}
.av3-scenegrid .av3-sceneact .av3-btn{padding:3px 6px;font-size:11px;border-radius:5px;}
/* 「套用并预演」是次级动作：3 列里塞不下，只在展开时出现（v4.5.41） */
.av3-scenegrid>.av3-sec:not(.open) .av3-act2{display:none;}

.av3-row{display:flex;align-items:center;gap:7px;padding:4px 0;border-bottom:1px dashed rgba(255,255,255,.06);}
.av3-row:last-child{border-bottom:0;}
.av3-row .lab{flex:1;font-size:12.5px;}
.av3-row .lv{font-size:11px;color:#8a8a8a;min-width:52px;text-align:right;}
.av3-w{width:58px;background:var(--comfy-input-bg,#2a2a2a);color:var(--fg-color,#ddd);
  border:1px solid var(--border-color,#555);border-radius:5px;padding:3px 5px;font-size:12px;text-align:center;}
.av3-t{border:1px solid var(--border-color,#555);background:transparent;color:#9a9a9a;border-radius:5px;
  padding:2px 7px;font-size:11.5px;cursor:pointer;}
.av3-t.s1{color:#e0a458;border-color:#e0a458;}
.av3-t.s2{color:#7ec98b;border-color:#7ec98b;}
.av3-t:disabled{opacity:.35;cursor:not-allowed;}
.av3-box{background:rgba(255,255,255,.04);border:1px solid var(--border-color,#3a3a3a);border-radius:8px;padding:9px 11px;}
.av3-box h4{margin:0 0 7px;font-size:12.5px;color:#e8c98a;font-weight:600;}
.av3-out{background:rgba(0,0,0,.32);border:1px solid var(--border-color,#3a3a3a);border-radius:6px;
  padding:9px;min-height:96px;max-height:230px;overflow:auto;line-height:1.66;white-space:pre-wrap;
  word-break:break-word;font-size:12.5px;}
.av3-meta{font-size:11.5px;color:#9a9a9a;line-height:1.7;}
.av3-sc{font-size:11.5px;line-height:1.65;}
.av3-ok{color:#7ec98b;} .av3-no{color:#e07070;} .av3-wa{color:#e0a458;}
.av3-fld{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px;}
.av3-fld label{flex:1;color:#aaa;}
.av3-fld input[type=text],.av3-fld select{flex:1.6;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;padding:4px 6px;font-size:12px;}
.av3-sum{font-size:11.5px;color:#9a9a9a;line-height:1.7;max-height:112px;overflow:auto;}
.av3-mini{position:fixed;inset:0;z-index:10001;display:flex;align-items:center;justify-content:center;}
.av3-mini .card{width:min(620px,92vw);max-height:80vh;display:flex;flex-direction:column;
  background:var(--comfy-menu-bg,#1e1e1e);border:1px solid var(--border-color,#555);border-radius:10px;overflow:hidden;}
.av3-mini .card .hd{padding:9px 12px;border-bottom:1px solid var(--border-color,#444);display:flex;
  align-items:center;gap:8px;font-size:13px;font-weight:600;color:#e8c98a;}
.av3-mini .card .bd{overflow:auto;padding:8px 12px;}
.av3-mini .card .ft{padding:8px 12px;border-top:1px solid var(--border-color,#444);display:flex;gap:8px;justify-content:flex-end;}
/* 免责声明：比普通小弹窗更大（用户要求"可以交互展开全屏内容"） */
.av3-mini .card.av3-disc{width:min(760px,94vw);max-height:88vh;}
.av3-disc .bd p{margin:0 0 10px;font-size:12.5px;line-height:1.78;color:var(--fg-color,#ccc);}
.av3-disc .bd p.it{color:#dcdcdc;}
.av3-disc .bd p.by{color:#e8c98a;font-size:12px;margin-bottom:5px;}
.av3-disc .bd p.lead{color:#e8c98a;}
.av3-oRow{display:flex;align-items:center;gap:8px;padding:3px 0;}
.av3-oRow .n{flex:1;font-size:12.5px;}
.av3-oRow .e{font-size:11px;color:#7d7d7d;max-width:270px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
/* v4.2.3：细则里调过权重的选项 —— 文字变色，一眼看出"这套权重动过谁" */
.av3-oRow .n.av3-tuned{color:#ffd479;font-weight:600;}
.av3-oRow .e.av3-tuned{color:#d9b36c;}
.av3-oRow input.av3-tuned{color:#ffd479;font-weight:600;}
.av3-note{font-size:11.5px;color:#e0a458;line-height:1.6;margin-top:6px;}
.av3-tabs{display:flex;flex-wrap:wrap;border:1px solid var(--border-color,#444);border-radius:7px;overflow:hidden;}
.av3-tabs button{border:0;background:transparent;color:var(--fg-color,#bbb);padding:5px 13px;cursor:pointer;font-size:12.5px;}
.av3-tabs button.on{background:#d9b36c;color:#241a05;font-weight:600;}
.av3-charbar{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:8px;}
.av3-charbar input[type=text]{flex:1;min-width:150px;background:var(--comfy-input-bg,#2a2a2a);color:var(--fg-color,#ddd);
  border:1px solid var(--border-color,#555);border-radius:6px;padding:5px 8px;font-size:12.5px;}
.av3-chips{display:flex;gap:5px;flex-wrap:wrap;margin:6px 0 9px;}
.av3-chip{display:inline-flex;align-items:center;gap:5px;background:rgba(217,179,108,.13);
  border:1px solid rgba(217,179,108,.45);border-radius:14px;padding:2px 6px 2px 10px;font-size:12px;color:#f0d9a8;}
.av3-chip b{font-weight:500;cursor:pointer;color:#e8c98a;padding:0 3px;}
.av3-chip b:hover{color:#fff;}
/* ── 角色特征筛选面板（2026-10-29）──
   横跨整宽，位于操作条与两栏主体之间；收起时整个节点不创建。 */
.av3-traitpanel{border:1px solid rgba(217,179,108,.28);border-radius:8px;
  background:rgba(217,179,108,.055);padding:7px 9px 8px;margin:0 0 9px;}
.av3-trait-hd{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:6px;
  padding-bottom:5px;border-bottom:1px dashed rgba(217,179,108,.2);}
.av3-trait-hd .av3-meta{margin-right:auto;}
.av3-trait-hd b{color:#e8c98a;font-weight:600;}
.av3-trait-hd .av3-btn{padding:1px 8px;font-size:11px;}
.av3-traitgrp{display:flex;align-items:flex-start;gap:7px;margin:3px 0;}
.av3-traitgrp>.gl{flex:0 0 auto;width:50px;font-size:11px;color:#9a8f7c;
  line-height:19px;text-align:right;letter-spacing:.5px;}
.av3-traitgrp>.items{display:flex;flex-wrap:wrap;gap:4px;flex:1 1 auto;}
.av3-tbtn{font:inherit;font-size:11px;line-height:1.55;padding:0 8px;cursor:pointer;
  color:#cfc6b6;background:rgba(255,255,255,.045);
  border:1px solid rgba(255,255,255,.13);border-radius:11px;transition:none;}
.av3-tbtn:hover{color:#fff;border-color:rgba(217,179,108,.55);}
.av3-tbtn.on{color:#241a05;background:#d9b36c;border-color:#d9b36c;font-weight:600;}
.av3-tbtn:disabled{opacity:.3;cursor:not-allowed;}
.av3-tbtn:disabled:hover{color:#cfc6b6;border-color:rgba(255,255,255,.13);}
.av3-cbody{display:grid;grid-template-columns:190px minmax(0,1fr);gap:10px;align-items:start;}
.av3-works{max-height:520px;overflow:auto;border:1px solid var(--border-color,#3a3a3a);border-radius:8px;padding:5px;}
.av3-work{display:flex;align-items:center;gap:6px;padding:4px 7px;border-radius:6px;cursor:pointer;font-size:12.5px;}
.av3-work:hover{background:rgba(255,255,255,.05);}
.av3-work.on{background:rgba(217,179,108,.18);color:#f0d9a8;}
.av3-work .c{margin-left:auto;font-size:11px;color:#8a8a8a;}
/* v5.0.4：作品 IP 收藏。收藏中的行常驻 ★（金色），未收藏的只在 hover 时
   浮出 ☆ —— 不占常驻视觉，也不会让 284 行每一行都挂一个图标。*/
.av3-work .wstar{flex:0 0 12px;width:12px;text-align:center;font-size:12px;
  color:#8a8a8a;opacity:0;transition:opacity .12s;}
.av3-work:hover .wstar{opacity:.75;}
.av3-work .wstar.on{opacity:1;color:#e8c27a;}
.av3-work.fav .c{color:#c9a86a;}
/* ⚠️ v5.0.4：target（保底收录的目标作品）不再显示 ★ —— 用户口径是
   「星形只归收藏」，两个星同屏会分不清哪个是"我收藏的"。数据字段保留，
   这里只留样式不删（以后要恢复，前端加一行 class 即可）。*/
.av3-work.tgt .n::before{content:"★";color:#d9b36c;margin-right:3px;}
/* 收藏为 0 时置灰，但仍可点（点进去看空状态提示） */
.av3-work.empty{opacity:.45;}
.av3-preset{display:flex;gap:7px;align-items:center;margin:5px 0;}
.av3-preset select,.av3-preset input[type=text]{flex:1;min-width:0;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;
  padding:4px 6px;font-size:12px;}
.av3-preset .av3-btn{padding:4px 9px;}
/* 面板底部条：目前只放「巨乳」开关。固定在最下方，不随两列滚动。 */
.av3-foot{display:flex;align-items:center;gap:10px;padding:7px 14px;flex-wrap:wrap;
  border-top:1px solid var(--border-color,#444);background:rgba(255,255,255,.02);}
.av3-foot label{display:inline-flex;align-items:center;gap:7px;cursor:pointer;
  font-size:12.5px;user-select:none;}
.av3-foot input[type=checkbox]{width:15px;height:15px;cursor:pointer;accent-color:#d9b36c;}
.av3-foot .hint{font-size:11.5px;color:#8a8a8a;}
.av3-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:7px;
  max-height:520px;overflow:auto;padding-right:2px;}
/* v6.1.0（用户 2026-11-07）：角色库页「网格下面一大块空白」——
   根因是 .av3-works / .av3-grid 都写死 max-height:520px，作品少的时候
   网格 4 行就到底，下面白着。这里只给角色库页加 charmode：
   左列变 flex 列、网格吃掉剩余高度 → 同屏多显示 1–2 行角色。
   ⚠️ 只挂在 .av3-left.charmode 上 —— 别的页签（场景 / 服装 / 词库）
      是整列滚动，改成 flex 会把它们的滚动条弄丢。 */
.av3-left.charmode{display:flex;flex-direction:column;min-height:0;}
.av3-left.charmode .av3-cbody{flex:1 1 auto;min-height:0;align-items:stretch;}
.av3-left.charmode .av3-works{max-height:none;min-height:0;overflow:auto;}
/* ⚠️ v6.1.1：只加 height:100% 会把卡片压成扁条（用户 2026-11-07 02:20 截图）——
   grid 默认 align-content:stretch 会把"行"按容器高度分摊，容器被 flex 定高后
   每行只剩几十像素，缩略图与名字一起被挤没。
   align-content:start + grid-auto-rows:max-content 把行高钉死在内容高度，
   超出就滚动 —— 这才是"同屏多显示几行"该有的行为。
   ⚠️ 本段在 CSS 模板字符串里：注释里不许出现反引号，会把字符串截断成语法错误。 */
.av3-left.charmode .av3-grid{max-height:none;min-height:0;height:100%;overflow:auto;
  align-content:start;grid-auto-rows:max-content;align-items:start;}
/* 场景卡勾选框（v6.1.0 · 姿势库 / 武器场景的「勾选后随机抽取」）。
   放在标题行最左、箭头之前 —— 那个位置原本是空的。 */
.av3-pick{flex:0 0 auto;width:15px;height:15px;margin:0 1px 0 0;cursor:pointer;
  accent-color:#d9b36c;}
/* 勾选随机工具条 */
.av3-pickbar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:0 0 7px;
  padding:5px 9px;border:1px solid #3a3a3a;border-radius:7px;
  background:rgba(217,179,108,.07);font-size:12px;}
.av3-pickbar .cnt{font-size:11.5px;color:#d9b36c;font-weight:600;}
/* 「已套用」卡片（v6.1.0）：一行一项，右侧 ✕ 一键取消 */
.av3-applied-row{display:flex;align-items:flex-start;gap:7px;padding:3px 0;
  font-size:12px;line-height:1.45;border-top:1px dashed rgba(255,255,255,.07);}
.av3-applied-row:first-of-type{border-top:none;}
.av3-applied-row .tx{flex:1;min-width:0;word-break:break-all;}
.av3-applied-row .tx b{color:#e8c98a;font-weight:600;}
.av3-applied-x{flex:0 0 auto;padding:1px 7px;font-size:11px;line-height:1.5;}
.av3-card{border:1px solid var(--border-color,#3a3a3a);border-radius:8px;overflow:hidden;cursor:pointer;
  background:rgba(255,255,255,.03);position:relative;}
.av3-card:hover{border-color:#d9b36c;}
.av3-card.on{border:2px solid #d9b36c;background:rgba(217,179,108,.14);}
.av3-card .ph{width:100%;aspect-ratio:1/1;object-fit:cover;display:block;background:#26262e;}
.av3-card .ph.letter{display:flex;align-items:center;justify-content:center;font-size:26px;color:#5f5e5a;}
.av3-card .nmRow{display:flex;align-items:center;gap:4px;padding:4px 5px 1px;}
.av3-card .nmRow .nm{flex:1;min-width:0;padding:0;font-size:11.5px;line-height:1.3;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.av3-card .en{padding:0 5px 5px;font-size:10.5px;color:#8a8a8a;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
/* 人气徽标跟着名字走（原来是绝对定位在右上角，那个角要让给收藏星） */
.av3-card .pp{flex:0 0 auto;font-size:10px;color:#8a8a8a;}
/* 收藏星：卡片右上角。切到别的角都会跟「选中打勾」或人气撞车。 */
.av3-card .fav{position:absolute;top:3px;right:3px;width:19px;height:19px;display:flex;
  align-items:center;justify-content:center;font-size:13px;line-height:1;color:#9a9a9a;
  cursor:pointer;border-radius:50%;background:rgba(0,0,0,.55);user-select:none;}
.av3-card .fav:hover{color:#e8c98a;background:rgba(0,0,0,.75);}
.av3-card .fav.on{color:#f5c451;}
.av3-card .tick{position:absolute;top:3px;left:3px;background:#d9b36c;color:#241a05;
  font-size:11px;border-radius:9px;width:16px;height:16px;display:flex;align-items:center;
  justify-content:center;font-weight:700;}
.av3-card .pick{position:absolute;top:3px;left:3px;background:#3b82f6;color:#fff;
  font-size:11px;border-radius:9px;width:16px;height:16px;display:flex;align-items:center;
  justify-content:center;font-weight:700;z-index:3;}
.av3-more{grid-column:1/-1;text-align:center;padding:8px;}
.av3-empty{font-size:12px;color:#8a8a8a;padding:14px;text-align:center;grid-column:1/-1;}
/* v3.4.0 角色编辑 / 自定义角色 */
.av3-card .edit{position:absolute;right:3px;bottom:3px;width:19px;height:19px;display:flex;
  align-items:center;justify-content:center;font-size:12px;line-height:1;color:#9a9a9a;
  cursor:pointer;border-radius:50%;background:rgba(0,0,0,.55);user-select:none;}
.av3-card .edit:hover{color:#e8c98a;background:rgba(0,0,0,.75);}
.av3-card .av3-badge{position:absolute;left:3px;bottom:3px;font-size:9.5px;line-height:1;
  padding:2px 4px;border-radius:4px;background:rgba(126,201,139,.92);color:#12301c;
  user-select:none;}
.av3-mini .card.av3-editor{width:min(560px,94vw);}
.av3-editor .hd .tag{margin-left:auto;font-size:11px;color:#8a8a8a;font-weight:400;}
.av3-efld{display:flex;align-items:center;gap:8px;margin:7px 0;font-size:12.5px;}
.av3-efld label{flex:0 0 72px;color:#aaa;}
.av3-efld input[type=text]{flex:1;min-width:0;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;
  padding:4px 6px;font-size:12.5px;}
.av3-ethumb{display:flex;gap:10px;align-items:flex-start;margin:6px 0 4px;}
.av3-ethumb .pv{width:84px;height:84px;flex:0 0 84px;border-radius:8px;overflow:hidden;
  background:#26262e;display:flex;align-items:center;justify-content:center;
  font-size:24px;color:#5f5e5a;}
.av3-ethumb .pv img{width:100%;height:100%;object-fit:cover;display:block;}
.av3-ethumb .ops{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;}
.av3-ethumb .ops .row{display:flex;gap:6px;flex-wrap:wrap;}
.av3-eerr{color:#e07070;font-size:11.5px;line-height:1.5;min-height:16px;margin:2px 0 0;}
/* v3.5.0 特征标签编辑区 */
.av3-tagsec{border-top:1px dashed var(--border-color,#3a3a3a);margin-top:10px;padding-top:9px;}
.av3-taghead{display:flex;align-items:center;gap:8px;margin-bottom:5px;}
.av3-taghead .ttl{font-size:12.5px;font-weight:600;color:#e8c98a;}
.av3-taghead .av3-btn{margin-left:auto;padding:3px 8px;font-size:11.5px;}
.av3-tagrows{max-height:186px;overflow:auto;display:flex;flex-direction:column;gap:4px;}
.av3-tagrow{display:flex;gap:5px;align-items:center;}
.av3-tagrow input{flex:1;min-width:0;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;
  padding:3px 6px;font-size:12px;}
.av3-tagrow .av3-btn{padding:2px 8px;font-size:12px;line-height:1.35;flex:0 0 auto;}
.av3-tagfoot{display:flex;align-items:center;gap:8px;margin-top:6px;}
.av3-tagsec .av3-meta{font-size:11.5px;color:#9a9a9a;line-height:1.6;}
`;

let cssDone = false;
function injectCSS() {
  if (cssDone) return;
  cssDone = true;
  const s = document.createElement("style");
  s.textContent = CSS;
  document.head.appendChild(s);
}

/* ── 主面板 ───────────────────────────────────────────────────────── */
function openPanel(node) {
  injectCSS();
  ensureData().then(() => buildPanel(node)).catch((e) => {
    console.error("[AnimaV6] 面板数据加载失败", e);
    alert("Anima V3 面板加载失败：" + e.message + "\n请确认插件已随 ComfyUI 启动，并刷新页面。");
  });
}

function lsGet(node, name, dflt = "") {
  return wget(node, name, dflt);
}

function buildPanel(node) {
  const { pools, contract } = CACHE;
  const mask = document.createElement("div");
  mask.className = "av3-mask";

  const panel = document.createElement("div");
  panel.className = "av3-panel";
  mask.appendChild(panel);

  /* 面板内的临时状态。权重与设置类改动会**立即**写回节点（见 persist），
     只有 seed 需要点「采用这条」—— 它是预览产物，不能乱写。 */
  // v3.25.0：界面记忆 —— 存在 weights_json._ui 里（按节点，随工作流保存）。
  //   ⚠️ 必须**先**拿到记忆再建 ui，否则后面的默认值会把记忆盖掉。
  const _w0 = loadWeights(node);
  const _memo = (_w0._ui && typeof _w0._ui === "object") ? _w0._ui : {};
  /* ⚠️ v5.0.23：新增页签**必须同步加进这个白名单** —— 它是记忆还原的守门人，
     漏一个的表现就是「停在艺术构图关闭、重开却回到词库页」（用户 2026-11-04 20:58 报的正是这个）。 */
  /* v5.3.1：角色库搜索框的 IME 组合态（见 renderChars / qIn 的注释）*/
  let charComposing = false;
  let charPendingRender = false;

  const _TABS = ["weights", "chars", "outfits", "scenes", "pose", "art",
                 "weapon", "near"];
  /* v6.5.1：历史分类 id 归一（与 anima_v4_store.CAT_ALIASES 同源）。
     老工作流里 weights_json._ui.outfitCat 可能仍是 "nsfw"，
     改 id 后不归一就会「筛选看着选中、实际一条都不匹配」。 */
  const OUTFIT_CAT_ALIAS = { nsfw: "nsfw_outfit" };
  function normOutfitCat(c) {
    const s = String(c || "").trim();
    return OUTFIT_CAT_ALIAS[s] || s;
  }
  const _secIds = new Set(pools.sections.map((s) => s.id));
  const ui = {
    level: String(lsGet(node, "nsfw_level", "sfw")),
    weights: _w0,
    lastResult: null,
    lastSeed: null,
    // 默认展开前三段 —— 全折叠的面板第一眼看着像坏了；
    // 有记忆则按记忆还原（顺手滤掉已经不存在的段 id，语料升级后不会炸）
    openSecs: new Set(Array.isArray(_memo.openSecs) && _memo.openSecs.length
      ? _memo.openSecs.filter((id) => _secIds.has(id))
      : pools.sections.slice(0, 3).map((s) => s.id)),
    tab: _TABS.includes(_memo.tab) ? _memo.tab : "weights",
    // 滚动位置用**比例**存（0–1），不用绝对像素：两次打开之间内容高度可能变
    _scrollMemo: (_memo.scroll && typeof _memo.scroll === "object") ? _memo.scroll : null,
    _scrollDone: false,
    chars: null,            // 角色库（懒加载）
    charsLoading: false,
    favorites: null,        // Set<tag>，懒加载；null = 还没拉过
    favLoading: false,
    // v5.0.4：收藏的**作品 IP**（Set<slug>）。与角色收藏是两个值域，
    //         分开存（user_work_favorites.json），也分开加载 ——
    //         角色页要同时用到两份，但任一份拉失败都不该拖住另一份。
    workFav: null,
    workFavLoading: false,
    // 作品筛选 / 搜索词也属于"界面"，一并还原
    charWork: (typeof _memo.charWork === "string" ? _memo.charWork : ""),
    charQuery: (typeof _memo.charQuery === "string" ? _memo.charQuery : ""),
    // 2026-10-29：特征筛选（Set<danbooru tag>）+ 面板展开态，随界面一起记
    charTraits: new Set(Array.isArray(_memo.charTraits) ? _memo.charTraits : []),
    charTraitOpen: !!_memo.charTraitOpen,
    charLimit: 96,          // 角色网格分页渲染上限（2000 张一次渲染会卡）
    charRndN: 1,            // 🎲 随机抽角色：一次抽几位（v3.8.0）
    // v3.18.6：角色库多选删除 —— 打开后点卡片是"勾选"而不是"加入已选角色"
    charMulti: false,
    charPicked: new Set(),
    scenes: null,           // 场景搭配库（懒加载，v3.9.0）
    // v3.11.0：场景卡默认**折叠**（87 条全展开要滚很久），
    // 记每张卡的展开态；「全部展开/收起」一次改全部。
    sceneOpen: (_memo.sceneOpen && typeof _memo.sceneOpen === "object") ? { ..._memo.sceneOpen } : {},
    // v3.21.0：场景页的**语义分组**筛选（"all" / "pose"）。
    // 注意与一级分组不冲突：一级仍按档位（L0–L4），这条只过滤哪些卡片出现在页上。
    /* ⚠️ v5.1.0：姿势已独立成页签，scenes 页的 group 过滤一并废掉。
       这里**固定 "all"** —— 旧记忆里如果存着 `"pose"`，而场景页 now 又把 pose
       排除了，两边相乘就是一张空列表（实测很容易被当成"场景库坏了"）。 */
    sceneGroup: "all",
    /* v6.1.0：姿势库 / 武器场景的勾选集（随界面一起记忆）。
       这两组都是 manual_only、不参与全局随机池 —— 勾选只服务页内的
       「🎲 从勾选随机」，所以与 scene_pick 开关是两回事。
       v6.6.0：特写搭配（near）也有同一套 —— 同样是 manual_only。 */
    scenePick: {
      pose: Array.isArray(_memo.scenePick && _memo.scenePick.pose) ? _memo.scenePick.pose : [],
      weapon: Array.isArray(_memo.scenePick && _memo.scenePick.weapon) ? _memo.scenePick.weapon : [],
      near: Array.isArray(_memo.scenePick && _memo.scenePick.near) ? _memo.scenePick.near : [],
      // v6.6.1：艺术构图也接同一套（103 条全 manual_only，不进随机池）
      art: Array.isArray(_memo.scenePick && _memo.scenePick.art) ? _memo.scenePick.art : [],
    },
    // v5.1.0：姿势库内部的分类（all / yoga / stretch / basic）
    poseCat: ["all", "yoga", "stretch", "basic"].indexOf(_memo.poseCat) >= 0
      ? _memo.poseCat : "all",
    // v5.11.0：武器场景两级筛选 —— 作品(work) → 该作品下的武器(weapon)
    weaponWork: (typeof _memo.weaponWork === "string" ? _memo.weaponWork : "all"),
    weaponCat: (typeof _memo.weaponCat === "string" ? _memo.weaponCat : "all"),
    /* v5.0.20：背景分类筛选（纯色棚拍 / 双色对角 / 花园俯拍 / 室内实景 / 黑底逆光）。
       与 sceneTheme（具体配色）是两个**独立**维度，可叠加：
       例「艺术构图 + 花园俯拍」只看花园那 20 条。空串 = 不筛。 */
    sceneBg: (typeof _memo.sceneBg === "string" ? _memo.sceneBg : ""),
    scenesLoading: false,
    /* ── v3.43.0：场景页检索 / 收藏 / 浏览状态 ──────────────────────
       全部属于"界面"，随 weights._ui 一起记（见 rememberUi）；
       SceneFav 是**数据**（Set<场景 id>），懒加载，null = 还没拉过。 */
    sceneFav: null,
    sceneFavLoading: false,
    sceneQuery: (typeof _memo.sceneQuery === "string" ? _memo.sceneQuery : ""),
    sceneTheme: (typeof _memo.sceneTheme === "string" ? _memo.sceneTheme : ""),
    sceneThemeAll: _memo.sceneThemeAll === true,
    // v3.44.0：题材筛选条整条收起态。默认**展开** —— 藏起来的新功能等于没有；
    // 用户收起过就记住（与 sceneGroup / sceneClosed 同一套记忆）。
    sceneThemeOpen: _memo.sceneThemeOpen !== false,
    sceneFavOnly: _memo.sceneFavOnly === true,
    sceneCompact: _memo.sceneCompact === true,
    // v5.0.1：比例筛选（"all" / "p" = 竖屏·通用 / "l" = 横屏·通用）。
    // 与搜索、收藏、档位分组是 **AND** 叠加 —— 三者可以同时生效。
    sceneAspect: (_memo.sceneAspect === "p" || _memo.sceneAspect === "l")
      ? _memo.sceneAspect : "all",
    // v5.0.2：角色服装预设 {role_tag: {enabled, list}}，懒加载后缓存在这里
    outfits: null,
    /* v5.1.0：全局服装库（/outfit-library 整份，懒加载）。
       `nil` = 还没拉过；`active` 在 d 里，不在 ui 上 —— 启用态由服务端持有，
       面板只负责显示与切换（与角色预设同一条纪律：状态必须跨工作流一致）。 */
    outfitLib: null,
    outfitLibLoading: false,
    // 服装库页的筛选态（属于"界面"，随 weights._ui 记）
    outfitCat: normOutfitCat(typeof _memo.outfitCat === "string" ? _memo.outfitCat : ""),
    /* v6.4.0：分类条默认**收起**（11 类平铺要占两行），点「▸ 展开分类」看全部。 */
    outfitCatOpen: !!_memo.outfitCatOpen,
    /* v6.5.1：干爽过滤词条明细默认展开（界面记忆，随 weights._ui 存）。 */
    dryDetailOpen: _memo.dryDetailOpen !== false,
    outfitQuery: (typeof _memo.outfitQuery === "string" ? _memo.outfitQuery : ""),
    outfitOpen: (_memo.outfitOpen && typeof _memo.outfitOpen === "object")
      ? { ..._memo.outfitOpen } : {},
    // v4.5.41：收藏区默认**收起**（用户「收藏的场景只在交互后展开」）；
    // 用法说明同样默认收起 —— 首屏先给列表。
    sceneFavOpen: _memo.sceneFavOpen === true,
    sceneTipOpen: _memo.sceneTipOpen === true,
    // 档位分组的折叠态：{"2": true} = L2 收起。空对象 = 全展开。
    sceneClosed: (_memo.sceneClosed && typeof _memo.sceneClosed === "object")
      ? { ..._memo.sceneClosed } : {},
    sceneLimit: 60,         // 首屏渲染条数（懒加载，滚到底 +60）
    _sceneMore: false,      // 本轮还有没有未渲染的卡片（滚动续载用）
    // v3.18.0：「🔍 特写搭配」页的状态 —— 部位筛选 + 套用时关掉的 ③ 段快照
    nearPart: (typeof _memo.nearPart === "string" ? _memo.nearPart : ""),
    // v6.6.1：特写套用「双女条目 + 只选 1 位」时，状态条要说清是引擎补位
    //（v3.18 的"清空已选"已废，见 applyScene）
    nearDuoFill: false,
    // v3.44.0：特写页部位筛选条的整条收起态（默认展开）
    nearPartOpen: _memo.nearPartOpen !== false,
    nearRestore: null,      // 已废弃（v3.18.5）：基线改存 weights._nearBase，刷新也不丢
    presets: [],            // 命名预设立列表（懒加载）
    presetsLoading: false,
  };
  // 节点上已有的 disabled_sections 要折成段权重 0，否则面板显示"全开"、
  // 实际跑起来却关着几段，两边对不上。
  for (const sid of String(lsGet(node, "disabled_sections", "")).split(",")) {
    const t = sid.trim();
    if (t && ui.weights.sections[t] === undefined) ui.weights.sections[t] = 0;
  }

  /* ── 头部 ── */
  const head = document.createElement("div");
  head.className = "av3-head";
  const title = document.createElement("h3");
  title.textContent = "Anima 提示词随机生成（V6）";
  head.appendChild(title);

  /* 左列分两页：词库权重 / 角色库。角色库 2000+ 条，进面板时**不预加载**，
     点到那一页才拉 —— 否则每次开面板都要等 400KB JSON。 */
  const tabs = document.createElement("div");
  tabs.className = "av3-tabs";
  const tabBtns = {};
  for (const [key, label] of [["weights", "词库与权重"], ["chars", "角色库"],
                              ["outfits", "👗 服装库"],
                              ["scenes", "🎬 场景搭配"], ["pose", "🧘 姿势库"],
                              ["art", "🎨 艺术构图"],
                              ["weapon", "⚔️ 武器场景"],
                              ["near", "🔍 特写搭配"]]) {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => {
      // v5.0.21：背景分类只属于艺术构图页 —— 切走时清掉，
      // 否则会带着一个界面上看不见的筛选（列表莫名其妙变短）。
      if (key !== "art") ui.sceneBg = "";
      ui.tab = key;
      syncTabs();
    };
    tabs.appendChild(b);
    tabBtns[key] = b;
  }
  head.appendChild(tabs);

  /* v5.0.21：档位条**从界面隐藏**（用户 2026-11-04 20:13「面板内 NSFW 等级隐藏」）。
     与 v3.44.0 立的规矩一致 ——「同一个设置只留一个入口」：档位本来就有节点控件，
     面板再摆一排按钮属于重复外露。
     ⚠️ 只做 `display:none`、**不从 DOM 里删**：① 前端冒烟的档位用例（切到「过激」
        验 2girls, yuri）还要点它；② 将来要挪到设置区，去掉这一行即可。 */
  const lvBox = document.createElement("div");
  lvBox.className = "av3-lv";
  lvBox.style.display = "none";
  const lvBtns = {};
  for (const lv of contract.levels) {
    const b = document.createElement("button");
    b.textContent = LEVEL_SHORT[lv.key] || lv.key;
    b.title = lv.desc || "";
    b.onclick = () => {
      ui.level = lv.key;
      syncLevel();
    };
    lvBox.appendChild(b);
    lvBtns[lv.key] = b;
  }
  head.appendChild(lvBox);   // v5.0.21：容器进 DOM 但 display:none（见上），界面上不可见

  const spacer = document.createElement("div");
  spacer.style.flex = "1";
  head.appendChild(spacer);

  // 免责声明入口放头部而不是底部：它得在任何页签下都看得见，
  // 而底部两列都在滚动容器里，会被滚走。
  const discBtn = document.createElement("button");
  discBtn.className = "av3-btn";
  discBtn.textContent = "⚠ 免责声明";
  discBtn.title = "使用条款与责任说明";
  discBtn.onclick = () => openDisclaimer();
  head.appendChild(discBtn);

  const closeBtn = document.createElement("button");
  closeBtn.className = "av3-btn";
  closeBtn.textContent = "关闭";
  closeBtn.onclick = () => {
    rememberUi();               // v3.25.0：先把"停在哪"记下来，再存权重
    saveWeights(node, ui.weights);
    mask.remove();
    app.graph.setDirtyCanvas(true, true);
  };
  head.appendChild(closeBtn);
  panel.appendChild(head);

  /* ── 主体两列 ── */
  const body = document.createElement("div");
  body.className = "av3-body";
  const left = document.createElement("div");
  left.className = "av3-left";
  const right = document.createElement("div");
  right.className = "av3-right";
  body.appendChild(left);
  body.appendChild(right);
  panel.appendChild(body);

  /* ── v3.25.0：界面记忆（续）────────────────────────────────────
     关闭面板时记：页签 / 两列滚动比例 / 段与卡片展开态 / 各类筛选；
     再打开时原样还原。
     ⚠️ 滚动存**比例**不存像素 —— 中间可能改过筛选、内容高度变了，
        像素值两次打开之间不可比，比例才稳定。
     ⚠️ 角色库 / 场景库是懒加载，还原时左列还是空占位（高度 0），
        所以定位要**等内容到位**再重试，见 restoreUiScroll。 */
  function _scrollRatio(el) {
    const max = el.scrollHeight - el.clientHeight;
    return max > 8 ? Math.min(1, Math.max(0, el.scrollTop / max)) : 0;
  }
  function _applyRatio(el, r) {
    const max = el.scrollHeight - el.clientHeight;
    if (max > 8) el.scrollTop = Math.round(max * Math.min(1, Math.max(0, Number(r) || 0)));
  }
  function rememberUi() {
    ui.weights._ui = {
      tab: ui.tab,
      openSecs: Array.from(ui.openSecs),
      sceneOpen: { ...ui.sceneOpen },
      sceneGroup: ui.sceneGroup,
      scenePick: { pose: (pickArr("pose") || []).slice(),
                   weapon: (pickArr("weapon") || []).slice(),
                   near: (pickArr("near") || []).slice(),
                   art: (pickArr("art") || []).slice() },
      poseCat: ui.poseCat,
      weaponWork: ui.weaponWork,
      weaponCat: ui.weaponCat,
      outfitCat: ui.outfitCat,
      outfitCatOpen: ui.outfitCatOpen,
      dryDetailOpen: ui.dryDetailOpen,
      outfitQuery: ui.outfitQuery,
      outfitOpen: { ...ui.outfitOpen },
      sceneBg: ui.sceneBg,
      // v3.43.0：检索 / 收藏 / 浏览偏好一起记 —— 下次打开还在原处
      sceneQuery: ui.sceneQuery,
      sceneAspect: ui.sceneAspect,
      sceneTheme: ui.sceneTheme,
      sceneThemeAll: ui.sceneThemeAll,
      // v3.44.0：两条筛选条的收起态（场景页题材 / 特写页部位）
      sceneThemeOpen: ui.sceneThemeOpen,
      nearPartOpen: ui.nearPartOpen,
      sceneFavOnly: ui.sceneFavOnly,
      sceneFavOpen: ui.sceneFavOpen,
      sceneTipOpen: ui.sceneTipOpen,
      sceneCompact: ui.sceneCompact,
      sceneClosed: { ...ui.sceneClosed },
      nearPart: ui.nearPart,
      charWork: ui.charWork,
      charQuery: ui.charQuery,
      charTraits: [...ui.charTraits],
      charTraitOpen: ui.charTraitOpen,
      scroll: { left: _scrollRatio(left), right: _scrollRatio(right) },
    };
  }
  let _scrollTries = 0;
  function restoreUiScroll() {
    if (ui._scrollDone) return;
    const memo = ui._scrollMemo;
    if (!memo) { ui._scrollDone = true; return; }
    _applyRatio(left, memo.left);
    _applyRatio(right, memo.right);
    const maxL = left.scrollHeight - left.clientHeight;
    // 左列还在"正在加载…"占位（高度不够）→ 过一会儿再试，最多约 4 秒
    if (maxL > 8 || _scrollTries >= 40) { ui._scrollDone = true; return; }
    _scrollTries += 1;
    setTimeout(restoreUiScroll, 100);
  }

  /* ── 底部条：胸部大小等级（v3.17.0，取代巨乳开关）────────────────
     放在面板**最底部**（独立于两列，不随滚动跑掉）。
     「不指定」= figure 参与随机（可被角色官方设定覆盖）；
     指定档位 = figure 锁定为对应 tag，优先级高于角色外貌对齐与权重。
     状态写进节点控件 breast_size，随工作流 JSON 保存。
     ⚠️ 旧工作流里 big_breasts=True 的一次性兼容：升级后首次打开面板时，
        若 breast_size 还没选过且旧开关是开的 → 显示为「巨乳」，
        但**不回写**旧开关（保持原值，真正生效以 breast_size 为准）。 */
  const foot = document.createElement("div");
  foot.className = "av3-foot";
  const bsLab = document.createElement("label");
  bsLab.title = "指定后身材轮廓（figure）锁定为所选档位，覆盖角色设定与权重";
  const bsTxt = document.createElement("span");
  bsTxt.textContent = "胸部大小";
  bsLab.appendChild(bsTxt);
  foot.appendChild(bsLab);
  /* 真源映射与后端 BREAST_SIZES / BREAST_SIZE_CHOICES 保持一致（改两边要同步）。 */
  const BS_CHOICES = [
    ["不指定", ""], ["平胸", "flat chest"], ["小胸", "small breasts"],
    ["中等", "medium breasts"], ["大胸", "large breasts"],
    ["巨乳", "huge breasts"], ["爆乳", "gigantic breasts"],
  ];
  const bsSel = document.createElement("select");
  for (const [zh] of BS_CHOICES) {
    const opt = document.createElement("option");
    opt.value = zh;
    opt.textContent = zh;
    bsSel.appendChild(opt);
  }
  /* 兼容读（v3.17.0）：老工作流的 big_breasts=True 语义平移 ——
     ⚠️ ComfyUI 加载老工作流时，缺失的尾部控件会用**默认值**填充
        （breast_size="不指定"），无法区分"默认填充"与"用户真选"。
        所以判定只能靠旧开关：它还开着 = 旧语义"要巨乳"没被消费过 →
        一次性平移成 breast_size=巨乳 + 旧开关退役（写回）。
        面板没打开过就跑工作流的情况由**后端**兜底（同一条规则）。 */
  const bbOld = lsGet(node, "big_breasts", false) === true;
  const bsSaved = String(lsGet(node, "breast_size", "不指定") || "不指定");
  if (bbOld && bsSaved === "不指定") {
    wset(node, "breast_size", "巨乳");
    wset(node, "big_breasts", false);
    bsSel.value = "巨乳";
  } else {
    bsSel.value = BS_CHOICES.some(([zh]) => zh === bsSaved) ? bsSaved : "不指定";
  }
  foot.appendChild(bsSel);
  const bsHint = document.createElement("span");
  bsHint.className = "hint";
  bsHint.textContent = "不指定=随机（可被角色设定覆盖）；指定=锁定身材轮廓，覆盖外貌对齐与权重";
  foot.appendChild(bsHint);
  bsSel.onchange = () => {
    wset(node, "breast_size", bsSel.value);
    app.graph.setDirtyCanvas(true, true);
    setStatus(bsSel.value === "不指定" ? "胸部大小：不指定（figure 参与随机）"
                                      : `胸部大小已锁定：${bsSel.value}`, "");
  };
  panel.appendChild(foot);

  /* ── 底部条②：服装风格智配（v3.19.0）──────────────────────────
     与节点控件 outfit_style 一一对应，真源在 anima_v4_outfit_style.STYLE_CHOICES
     （两边改动必须同步，check_v3_color.py 会断言）。
     「随机」= 智配开、锚点自动（整套 > 上装 > 下装）；
     「不限」= 智配整体关闭，回到 v3.18 的纯随机（对照复现用）；
     具体风格 = 整套服装**硬收敛**到该风格，其余部位软加权。 */
  const foot2 = document.createElement("div");
  foot2.className = "av3-foot";
  const osLab = document.createElement("label");
  osLab.title = "只影响服装六类字段（整套/上装/下装/腿上/鞋子/配饰），不碰发型五官与姿势";
  const osTxt = document.createElement("span");
  osTxt.textContent = "服装风格";
  osLab.appendChild(osTxt);
  foot2.appendChild(osLab);
  const osSel = document.createElement("select");
  const OS_CHOICES = ["随机", "不限", "学院", "职场", "传统", "运动",
                      "日常", "居家", "夜店", "奇幻", "泳装",
                      "哥特", "洛丽塔", "朋克", "战术风",
                      "暗黑少女", "辣妹", "女仆"];
  for (const zh of OS_CHOICES) {
    const opt = document.createElement("option");
    opt.value = zh;
    opt.textContent = zh;
    osSel.appendChild(opt);
  }
  const osSaved = String(lsGet(node, "outfit_style", "随机") || "随机");
  osSel.value = OS_CHOICES.includes(osSaved) ? osSaved : "随机";
  foot2.appendChild(osSel);
  const osHint = document.createElement("span");
  osHint.className = "hint";
  osHint.textContent = "随机=自动定调；指定=整套服装按该风格收敛";
  foot2.appendChild(osHint);
  osSel.onchange = () => {
    wset(node, "outfit_style", osSel.value);
    app.graph.setDirtyCanvas(true, true);
    setStatus(osSel.value === "不限" ? "服装风格：不限（智配关闭，纯随机）"
              : osSel.value === "随机" ? "服装风格：随机（智配开，自动定调）"
              : `服装风格已收敛：${osSel.value}`, "");
  };
  panel.appendChild(foot2);

  /* ── 左列：段落 / 字段 / 权重 ── */
  const secEls = new Map();

  /* 状态条：两个页签都要用（权重页的动作反馈、角色页的清除反馈），
     所以挂在 buildPanel 作用域而不是某一列里面。 */
  let statEl = null;
  /* 「🎬 场景搭配」页要能一键套用后立刻跑一次预演 —— 而 runRoll 定义在
     renderRight 内部（那个作用域拿不到），所以在 buildPanel 层留个钩子，
     由 renderRight 结尾把 runRoll 挂上来。 */
  let panelRoll = null;
  /** v3.11.0：非破坏性预览（只看不套用）。同样由 renderRight 结尾挂上来。 */
  let panelPreview = null;
  /**
   * 读请求序号 —— 写操作会 +1，用来**作废在途的读**。
   *
   * ⚠️ 为什么必须有：GET 的响应可能比随后的写操作**更晚**到达，
   *    而且它带的是**发请求那一刻的旧快照**。不加防护的话，
   *    "删掉预设 → 面板上又冒出来"（冒烟抓到过）：
   *      ① applyConfig → loadPresets() 发出 GET（快照：还有 1 条）
   *      ② 用户点删除 → POST 成功 → ui.presets = []
   *      ③ ①的响应姗姗来迟 → ui.presets 被旧快照覆盖 → 列表复活
   *    规则：**后发起的写操作作废在途的读**，写操作的返回值才是权威。
   */
  let presetsSeq = 0;
  let favSeq = 0;
  let workFavSeq = 0;   // v5.0.4：作品 IP 收藏的请求序号（与角色收藏互不干扰）
  function setStatus(text, cls) {
    if (!statEl) return;
    statEl.className = "av3-meta " + (cls === "ok" ? "av3-ok"
      : cls === "no" ? "av3-no" : "");
    statEl.textContent = text;
  }

  /* ── 角色库：懒加载 + 作品筛选 + 搜索 + 多选 ───────────────── */
  //
  // 数据量 2000+ 条，两条纪律：
  //   ① **点到这一页才拉**（不预加载），否则每次开面板都要等 400KB JSON；
  //   ② 网格**分页渲染**（默认 96 张，点「显示更多」+96），
  //      2000 个 <img> 一次性塞进 DOM 会把浏览器卡死。
  //
  // 选中结果写进节点控件 selected_roles（用 `|` 分隔的**角色 tag**）。
  // ⚠️ 分隔符不能用逗号：完整 trigger（`hakurei reimu, touhou`）里就有逗号。

  function selectedList() {
    return String(lsGet(node, "selected_roles", "") || "")
      .split(/[|;\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function writeSelected(list) {
    wset(node, "selected_roles", list.join("|"));
    app.graph.setDirtyCanvas(true, true);
  }

  /* ── 收藏（⭐）───────────────────────────────────────────────
     收藏是**长期标记**，与「已选角色」是两个独立维度：
       收藏 = 我常想用的角色，跨会话保留；
       已选 = 这一次出图要用的角色。
     收藏不会自动变成已选，但收藏列表里可以正常点选。
     存插件目录 presets/user_favorites.json（见 anima_v4_store）。
  */

  function loadFavorites() {
    if (ui.favorites || ui.favLoading) return;
    ui.favLoading = true;
    const seq = ++favSeq;
    api.fetchApi(`/${NS}/favorites`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        // 期间发生过 toggle → 这份快照过期，丢掉（同 presetsSeq 的道理）
        if (seq !== favSeq) return;
        ui.favorites = new Set(d.favorites || []);
        ui.favLoading = false;
        if (ui.tab === "chars") refreshFavUI();
      })
      .catch((e) => {
        // 拉不到收藏不该挡住浏览 —— 降级成空集合，点 ⭐ 时再报错
        ui.favorites = new Set();
        ui.favLoading = false;
        console.error("[AnimaV6] 收藏加载失败", e);
      });
  }

  async function toggleFav(tag, on) {
    try {
      const r = await api.fetchApi(`/${NS}/favorites/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag, on }),
      });
      const d = await r.json();
      if (d.error) {
        setStatus("收藏失败：" + d.error, "no");
        return false;
      }
      // 后端返回的是**最新完整列表**，直接采用，不再多发一次 GET
      ui.favorites = new Set(d.favorites || []);
      favSeq += 1;          // 作废在途的 GET（它会覆盖成旧快照）
      setStatus(`${on ? "已收藏" : "已取消收藏"}（共 ${ui.favorites.size} 位）`, "ok");
      return true;
    } catch (e) {
      setStatus("收藏失败：" + e.message, "no");
      return false;
    }
  }

  /* ── 作品 IP 收藏（v5.0.4）─────────────────────────────────────
     为什么是**独立一套**：角色收藏的键是角色 tag（`firefly_(honkai:_star_rail)`），
     这里的是作品 slug（`kantai_collection`）。混在一个 Set 或一个文件里，
     两边都会多出永远命中不了的脏 key —— 后端也因此分文件存
     （user_work_favorites.json）。
     界面上只做两件事：收藏的排最前 + 行尾一颗 ★。 */

  function loadWorkFavorites() {
    if (ui.workFav || ui.workFavLoading) return;
    ui.workFavLoading = true;
    const seq = ++workFavSeq;
    api.fetchApi(`/${NS}/works/favorites`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        if (seq !== workFavSeq) return;   // 期间点过 toggle → 这份快照作废
        ui.workFav = new Set(d.favorites || []);
        ui.workFavLoading = false;
        // 作品列表就是这份数据渲染的，拉到后原地重填（不动右侧网格）
        const box = left.querySelector(".av3-works");
        if (box && ui.chars) buildWorkList(box, ui.chars);
      })
      .catch((e) => {
        // 拉不到不该挡住浏览：降级成空集合，点星时再报错
        ui.workFav = new Set();
        ui.workFavLoading = false;
        console.error("[AnimaV6] 作品收藏加载失败", e);
      });
  }

  async function toggleWorkFav(slug, on) {
    try {
      const r = await api.fetchApi(`/${NS}/works/favorites/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ work: slug, on }),
      });
      const d = await r.json();
      if (d.error) {
        setStatus("作品收藏失败：" + d.error, "no");
        return false;
      }
      // 后端回的是**最新完整列表**，直接采用，不再多发一次 GET
      ui.workFav = new Set(d.favorites || []);
      workFavSeq += 1;        // 作废在途的 GET（它会覆盖成旧快照）
      const box = left.querySelector(".av3-works");
      if (box && ui.chars) buildWorkList(box, ui.chars);
      setStatus(`${on ? "已收藏作品" : "已取消收藏"}（共 ${ui.workFav.size} 个 IP）`, "ok");
      return true;
    } catch (e) {
      setStatus("作品收藏失败：" + e.message, "no");
      return false;
    }
  }

  /**
   * 左栏作品列表。抽成独立函数是为了收藏 / 取消收藏时**只重填这一块**：
   * 直接 renderChars() 会把右侧网格打回 96 张并把滚动位置丢掉。
   *
   * 排序（用户口径 2026-11-02）：收藏的 IP 整体提到最前，组内仍按角色数降序；
   * 其余作品原样跟在后面，**不折叠**。
   * `target`（保底收录的目标作品）自 v5.0.4 起**不再显示 ★** —— 星形只归收藏。
   */
  function buildWorkList(box, d) {
    const wf = ui.workFav || new Set();
    box.innerHTML = "";
    const mkWork = (id, label, count, fav, togglable) => {
      const row = document.createElement("div");
      row.className = "av3-work" + (ui.charWork === id ? " on" : "")
        + (fav ? " fav" : "");
      row.dataset.work = id;
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = label;
      const c = document.createElement("span");
      c.className = "c";
      c.textContent = count;
      row.appendChild(n);
      row.appendChild(c);
      if (togglable) {
        const st = document.createElement("span");
        st.className = "wstar" + (fav ? " on" : "");
        st.textContent = fav ? "★" : "☆";
        st.title = fav ? "取消收藏这个作品 IP" : "收藏这个作品 IP";
        st.onclick = (ev) => {
          ev.stopPropagation();   // 别把"收藏"点成"切换 IP"
          toggleWorkFav(id, !fav);
        };
        row.appendChild(st);
      }
      row.onclick = () => {
        ui.charWork = id;
        ui.charLimit = 96;
        renderChars();
      };
      return row;
    };

    box.appendChild(mkWork("", "全部作品", (d.characters || []).length, false, false));
    // 「⭐ 我的收藏」紧跟「全部作品」—— 这两个都是"筛选视图"，
    // 与下面按作品分组的列表不是一类，放一起最好找。
    const favRow = mkWork(FAV_ID, "⭐ 我的收藏",
                          (ui.favorites || new Set()).size, false, false);
    if (!(ui.favorites || new Set()).size) favRow.classList.add("empty");
    box.appendChild(favRow);

    const all = d.works || [];
    all.filter((w) => wf.has(w.id))
       .forEach((w) => box.appendChild(
         mkWork(w.id, w.zh || w.en, w.count, true, true)));
    all.filter((w) => !wf.has(w.id))
       .forEach((w) => box.appendChild(
         mkWork(w.id, w.zh || w.en, w.count, false, true)));
  }

  /**
   * 就地刷新收藏相关的 UI（不重建网格 —— 重建会把滚动位置丢掉）。
   *
   * ⚠️ 这里刻意用 DOM 查询而不是 renderChars 里的 cardEls Map：
   *    收藏可能在「词库与权重」页签下被加载完，那时网格根本没建，
   *    Map 是空的。查询 DOM 任何页签下都安全。
   */
  function refreshFavUI() {
    const favs = ui.favorites || new Set();
    for (const el of left.querySelectorAll(".av3-card")) {
      const tag = el.dataset.tag;
      const st = el.querySelector(".fav");
      if (!st || !tag) continue;
      const on = favs.has(tag);
      st.textContent = on ? "★" : "☆";
      st.classList.toggle("on", on);
      st.title = on ? "取消收藏" : "加入收藏";
    }
    const row = left.querySelector(`.av3-work[data-work="${FAV_ID}"]`);
    if (row) {
      row.querySelector(".c").textContent = favs.size;
      row.classList.toggle("empty", favs.size === 0);
    }
  }

  /** 人数标签预览。规则与后端 headcount_tags 一致，这里只是即时提示。 */
  function headcountPreview(n) {
    const lvNum = levelNum(ui.level);
    if (n === 1) return "1girl, solo";
    if (n === 2) {
      return lvNum <= 1 ? "2girls, multiple girls（擦边档不用 yuri）"
                        : "2girls, yuri";
    }
    if (n >= 3) return "multiple girls（多人群像）";
    return "";
  }

  function loadChars() {
    if (ui.chars || ui.charsLoading) return;
    ui.charsLoading = true;
    api.fetchApi(`/${NS}/characters`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        ui.chars = d;
        ui.charsLoading = false;
        if (ui.tab === "chars") renderChars();
      })
      .catch((e) => {
        ui.charsLoading = false;
        console.error("[AnimaV6] 角色库加载失败", e);
        if (ui.tab === "chars") {
          left.innerHTML = `<div class="av3-empty">角色库加载失败：${e.message}<br>
            请确认 presets/anima_v4_characters.json 存在（跑 tools/build_characters.py 生成）。</div>`;
        }
      });
  }

  /* ── 角色特征筛选表（2026-10-29）───────────────────────────────
     按"标志性外形"快速筛角色：猫耳、精灵耳、白发、粉发……
     数据源就是角色自带的 `tags`（danbooru 英文标签），不新增后端接口。

     ⚠️ 这里的 tag 必须与 presets/anima_v4_characters.json 里**逐字一致**
        （小写、单空格）。写错不报错，只会永远 0 命中 —— 静默失效。
        改表前先统计一遍：对每个 tag 数含它的角色数，确认不是 0。

     多选语义：**组内 OR、组间 AND**
        选「白发」+「粉发」 → 白发 或 粉发
        选「白发」+「猫耳」 → 白发 且 猫耳（两组各挑一个）
  */
  const TRAIT_GROUPS = [
    { id: "hair_color", zh: "发色", items: [
      ["white hair", "白发"], ["grey hair", "灰发"], ["blonde hair", "金发"],
      ["brown hair", "棕发"], ["black hair", "黑发"], ["blue hair", "蓝发"],
      ["purple hair", "紫发"], ["pink hair", "粉发"], ["red hair", "红发"],
      ["green hair", "绿发"], ["orange hair", "橙发"],
      ["streaked hair", "挑染"], ["gradient hair", "渐变发"],
    ] },
    { id: "eye_color", zh: "瞳色", items: [
      ["red eyes", "红瞳"], ["blue eyes", "蓝瞳"], ["green eyes", "绿瞳"],
      ["purple eyes", "紫瞳"], ["yellow eyes", "金瞳"], ["brown eyes", "棕瞳"],
      ["grey eyes", "灰瞳"], ["pink eyes", "粉瞳"],
      ["heterochromia", "异色瞳"], ["white pupils", "白瞳"],
    ] },
    { id: "hair_style", zh: "发型", items: [
      ["very long hair", "超长发"], ["long hair", "长发"], ["short hair", "短发"],
      ["twintails", "双马尾"], ["ponytail", "马尾"], ["ahoge", "呆毛"],
      ["sidelocks", "鬓发"], ["blunt bangs", "齐刘海"],
      ["hair between eyes", "眼间垂发"], ["drill hair", "钻头卷"],
      ["hime cut", "姬发式"],
    ] },
    { id: "ears_horns", zh: "耳与角", items: [
      ["cat ears", "猫耳"], ["fox ears", "狐耳"], ["dog ears", "狗耳"],
      ["rabbit ears", "兔耳"], ["animal ears", "兽耳"],
      ["pointy ears", "尖耳"], ["elf", "精灵"],
      ["horns", "角"], ["dragon horns", "龙角"], ["halo", "光环"],
    ] },
    { id: "body", zh: "体型", items: [
      ["large breasts", "大胸"], ["medium breasts", "中胸"],
      ["small breasts", "小胸"], ["flat chest", "平胸"],
    ] },
    { id: "marks", zh: "标志物", items: [
      ["glasses", "眼镜"], ["fang", "虎牙"], ["sharp teeth", "尖牙"],
      ["eyepatch", "眼罩"], ["ear piercing", "耳钉"],
      ["mole under mouth", "嘴角痣"], ["freckles", "雀斑"], ["tattoo", "纹身"],
      ["hair ribbon", "发带"], ["hair bow", "蝴蝶结"],
      ["serafuku", "水手服"], ["school uniform", "校服"], ["maid", "女仆"],
    ] },
  ];

  const TRAIT_ZH = new Map();          // tag -> 中文名（面板按钮与"已选"摘要共用）
  for (const g of TRAIT_GROUPS) for (const [t, zh] of g.items) TRAIT_ZH.set(t, zh);

  function charHasTrait(c, tag) {
    const ts = c && c.tags;
    // 角色 tags 是普通数组；用 indexOf 而不是 includes，兼容老浏览器的 hasOwnProperty 语义差异
    return !!(ts && ts.indexOf(tag) >= 0);
  }

  /* 组内 OR、组间 AND。sel 为空 = 全通过。
     ⚠️ 只在**该组有选中项**时才判该组，没选中的组直接跳过 ——
        否则空组会被当成"必须满足"，所有角色都被筛掉。 */
  function traitPass(c, sel) {
    if (!sel || !sel.size) return true;
    for (const g of TRAIT_GROUPS) {
      const picked = g.items.filter((it) => sel.has(it[0]));
      if (!picked.length) continue;
      if (!picked.some((it) => charHasTrait(c, it[0]))) return false;
    }
    return true;
  }

  function matchChar(c, q) {
    if (!q) return true;
    // work_id 也纳入搜索串：用户可能从别处粘 `zenless_zone_zero` 进来
    const hay = `${c.zh} ${c.name} ${c.tag} ${c.work} ${c.work_id}`.toLowerCase();
    return hay.includes(q);
  }

  /* ── 服装预设一键模板（v5.0.9，用户 2026-11-03）──────────────────
     「有没有更方便的填写方法」：把六套日常搭配做成按钮，点一下直接填。

     ⚠️ 硬约束（改模板前必读）：
        · `fields` 的值必须与 `/pools` 里该字段的 opts **英文原文逐字一致**，
          否则后端 `outfit_for()` 会静默丢弃（不报错、只是不生效）。
        · 词库外的细节（版型 / 材质 / 手持物）一律放 `tags` —— 走 extra 通道，
          不受词库约束、也不过档位闸门。
        · `drop` 不写也安全：后端默认用 `_OUTFIT_DROP_DEFAULT`（13 个字段全集）。
          但为了「预设白裙 + 随机泳装并存」这类崩坏彻底不发生，模板这里显式写全。
  */
  const OUTFIT_TEMPLATES = [
    {
      id: "casual_offshoulder_knit", zh: "露肩针织衫", level: 0,
      note: "日常通勤向：露肩针织 + 百褶裙 + 过膝袜 + 乐福鞋。冷清干净的白天感。",
      tags: ["white off-shoulder sweater", "black pleated skirt",
             "black thighhighs", "brown loafers", "crossed legs",
             "hand on own thigh", "oversized clothes", "knitwear"],
      fields: { outfit: "sweater and skirt", top: "off-shoulder sweater",
                bottom: "pleated skirt", socks: "black thighhighs",
                shoes: "loafers", cloth_color: "white",
                cloth_mat: "knitwear", cloth_state_sfw: "off shoulder" },
    },
    {
      id: "casual_hoodie_shorts", zh: "卫衣短裤", level: 0,
      note: "周末居家向：灰卫衣 + 短裤 + 白球鞋。松垮衣服的慵懒感。",
      tags: ["grey oversized hoodie", "black shorts", "white sneakers",
             "white ankle socks", "standing on one leg",
             "hands in own pockets", "oversized clothes", "casual"],
      fields: { outfit: "hoodie and shorts", top: "hoodie", bottom: "shorts",
                socks: "ankle socks", shoes: "sneakers", cloth_color: "grey",
                cloth_state_sfw: "oversized clothes" },
    },
    {
      id: "casual_denim_tee", zh: "牛仔短衫", level: 0,
      note: "夏日常见向：白 T + 牛仔裙 + 白球鞋。干净利落，不出手套袜。",
      tags: ["white t-shirt", "blue denim skirt", "bare legs",
             "white sneakers", "sleeves rolled up", "tucked in",
             "denim", "casual"],
      fields: { top: "t-shirt", bottom: "denim skirt", shoes: "sneakers",
                cloth_color: "white", cloth_mat: "denim",
                cloth_state_sfw: "tucked in" },
    },
    {
      id: "casual_cardigan_pencil", zh: "开衫铅笔裙", level: 0,
      note: "通勤向：米色长开衫 + 黑铅笔裙 + 黑丝。轻熟、有腰线。",
      tags: ["beige long cardigan", "black pencil skirt", "white blouse",
             "black pantyhose", "black low heels", "handbag",
             "one hand on waist", "office lady", "smart casual"],
      fields: { top: "cardigan", bottom: "pencil skirt",
                socks: "black pantyhose", shoes: "chunky heels",
                accessory: "handbag", cloth_color: "black",
                cloth_mat: "knitwear" },
    },
    {
      id: "casual_oversized_tee", zh: "宽大T恤居家", level: 0,
      note: "居家向：只有一件宽大 T 恤 + 赤足。最松的日常。",
      tags: ["oversized white t-shirt", "bare legs", "barefoot",
             "sitting on bed", "one hand on own cheek", "messy hair",
             "loungewear", "at home"],
      fields: { top: "t-shirt", socks: "barefoot", shoes: "bare feet",
                cloth_color: "white",
                cloth_state_sfw: "oversized clothes" },
    },
    {
      id: "casual_sailor_summer", zh: "夏水手服", level: 0,
      note: "夏日校园向：短袖水手 + 蓝百褶 + 白及膝袜。明亮干净。",
      tags: ["white short-sleeve sailor shirt", "blue pleated skirt",
             "white kneehighs", "brown loafers", "holding umbrella",
             "summer uniform", "sunny day"],
      fields: { outfit: "serafuku", top: "sailor shirt",
                bottom: "pleated skirt", socks: "kneehighs",
                shoes: "loafers", cloth_color: "blue" },
    },
  ];

  /* ── 👗 角色服装预设（v5.0.2，用户 2026-11-02）────────────────────
     「角色编辑界面增加服装预设，有几套预设几套；启用后优先于随机权重，
       目的是锁定人物服装」。
     数据：presets/character_outfits.json（用户数据，不是构建产物）。
     启用后引擎把 13 个服装字段**全部闭麦**，只出这套 tags ——
     tags 走 extra 尾部通道，词库外的专属服装词（plugsuit / top hat）也能进。
     ⚠️ role 用库里的 tag（下划线形态），与 JSON 的 key 一致。 */
  async function loadOutfits(force) {
    if (ui.outfits && !force) return ui.outfits;
    try {
      const r = await api.fetchApi(`/${NS}/outfits`, { cache: "no-store" });
      const d = await r.json();
      ui.outfits = d.outfits || {};
    } catch (e) {
      ui.outfits = ui.outfits || {};
    }
    return ui.outfits;
  }

  function outfitOp(role, op, body) {
    return api.fetchApi(`/${NS}/outfits/${op}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ role }, body || {})),
    }).then((r) => r.json());
  }

  function openOutfitDialog(c) {
    const role = String(c.tag || "").toLowerCase();
    const mask = document.createElement("div");
    mask.className = "av3-mask";
    const inner = document.createElement("div");
    inner.className = "av3-mini";
    /* ⚠️ `.av3-mini` 本身就是**全屏遮罩**（position:fixed; inset:0），
       内容框必须是它内部的 `.card` —— 直接把内容塞进 `.av3-mini` 会变成
       没有背景、内容摊满整屏（v5.0.2 第一版就是这个坑，截图里一片混乱）。
       这里与 `openCharEditor()` 保持同一套结构。 */
    inner.style.position = "static";
    mask.appendChild(inner);
    const box = document.createElement("div");
    box.className = "card av3-outfit-card";
    box.id = "av3-outfit-dialog";
    inner.appendChild(box);
    document.body.appendChild(mask);
    mask.onclick = (e) => { if (e.target === mask) mask.remove(); };

    function form(base) {
      const o = base || { id: "", zh: "", level: 0, tags: [], fields: {},
                          verified: false, note: "" };
      box.innerHTML = "";
      const hd = document.createElement("div");
      hd.className = "hd";
      /* ⚠️ 标题里带一个版本标识 —— 排查「改了代码界面没变」时，
         先看这里有没有 V5.0.9-DROPDOWN：
           · 有 → 新代码已生效，界面没变是别的原因；
           · 没有 → 浏览器还在跑旧版本（缓存 / 没重启 / 加载了别的插件）。 */
      hd.textContent = `👗 ${o.id ? "编辑" : "新增"}服装 · ${c.zh || c.name}`
        + " [v5.0.9-DROPDOWN]";
      box.appendChild(hd);

      const bd = document.createElement("div");
      bd.className = "bd";
      bd.style.cssText = "padding:8px 12px;display:flex;flex-direction:column;gap:7px";
      box.appendChild(bd);

      const row = (label, el) => {
        const w = document.createElement("label");
        w.className = "av3-meta";
        w.style.cssText = "display:flex;gap:8px;align-items:flex-start";
        const t = document.createElement("span");
        t.textContent = label;
        t.style.cssText = "flex:0 0 74px;padding-top:4px";
        w.appendChild(t); w.appendChild(el);
        bd.appendChild(w);
        return el;
      };
      const inp = (v, ph) => {
        const i = document.createElement("input");
        i.className = "av3-w";
        i.value = v || "";
        i.placeholder = ph || "";
        i.style.cssText = "flex:1;min-width:0;padding:3px 6px";
        return i;
      };
      const ta = (v, ph) => {
        const t = document.createElement("textarea");
        t.className = "av3-w";
        t.value = v || "";
        t.placeholder = ph || "";
        t.rows = 3;
        t.style.cssText = "flex:1;min-width:0;padding:3px 6px;resize:vertical";
        return t;
      };

      const idIn = row("id", inp(o.id, "default / plugsuit / school …"));
      const zhIn = row("中文名", inp(o.zh, "原作 · 白背心黑裙"));
      const lvIn = row("档位", inp(String(o.level || 0), "0–4（0 = 全年龄）"));
      const tagIn = row("服装标签", ta((o.tags || []).join(", "),
        "white tank top, black skirt, suspenders …（逗号或换行分隔）"));

      /* ── 一键模板（v5.0.9 用户要求「直接增加一键添加通用服装预设」）──────
         **提到下拉区之前**，且不依赖 /pools —— 即使词库没加载完，
         模板按钮也必须立即可用（它就是"懒得填"的入口，最不该被别的东西拖累）。
         只在「新增」时显示 —— 编辑已有条目不该被模板盖掉。 */
      if (!o.id) {
        const tplWrap = document.createElement("div");
        tplWrap.className = "av3-meta";
        tplWrap.style.cssText = "display:flex;flex-direction:column;gap:4px;"
          + "padding:2px 0 2px 82px";
        const tl = document.createElement("div");
        tl.textContent = "一键添加通用服装预设（点一下全部填好，可直接保存）：";
        tl.style.cssText = "color:#e8c98a;font-size:12px";
        tplWrap.appendChild(tl);
        const tplBar = document.createElement("div");
        tplBar.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
        tplBar.id = "av3-outfit-templates";
        for (const T of OUTFIT_TEMPLATES) {
          const b = document.createElement("button");
          b.className = "av3-btn";
          b.type = "button";
          b.id = "av3-outfit-tpl-" + T.id;
          b.textContent = T.zh;
          b.title = T.note || "";
          b.onclick = () => {
            idIn.value = T.id;
            zhIn.value = T.zh;
            lvIn.value = String(T.level || 0);
            tagIn.value = T.tags.join(", ");
            for (const k of Object.keys(fld)) delete fld[k];
            Object.assign(fld, T.fields || {});
            try { syncAdvanced(); } catch (e) { /* 下拉未就绪也要能套模板 */ }
            setStatus(`已套用模板「${T.zh}」——可直接保存，或再微调`, "ok");
          };
          tplBar.appendChild(b);
        }
        tplWrap.appendChild(tplBar);
        bd.appendChild(tplWrap);
      }

      /* ── v5.0.9：字段值改成**下拉选择**（用户 2026-11-03：「有没有更方便的填写方法」）
         以前是一坨自由文本 `top: crop top\nbottom: skirt` —— 用户得手打英文，
         而且写错会被后端 `outfit_for()` **静默丢弃**（不报错、只是不生效），
         最难查的那种失效。

         现在每个服装字段一个下拉，选项直接来自 `/pools`（与引擎同源），
         显示成「中文名 → 英文值」，选中即写入。留空 = 该字段不锁、交给随机。

         ⚠️ 旧写法继续可用：底下保留「高级：直接编辑 fields 文本」的折叠区，
            两边的值是同一个 `fields` 字典 —— 用下拉改完，文本会同步；反之亦然。
         ⚠️ 下拉里出现的值**保证都是词库合法选项**（opts 原文），
            所以「选出来的东西一定生效」这条是硬保证。 */
      const CLOTH_EDIT_FIELDS = [
        ["outfit", "整套服装"], ["top", "上装与外套"], ["bottom", "下装"],
        ["socks", "腿上"], ["shoes", "鞋子"], ["accessory", "配饰"],
        ["cloth_color", "服装颜色"], ["cloth_mat", "材质"],
        ["cloth_state_sfw", "穿着方式"], ["sock_color", "袜子颜色"],
      ];

      // 取该字段的词库合法选项（与引擎同源）。pools 未就绪时返回空 → 调用方降级。
      function optsOf(key) {
        for (const s of ((CACHE.pools || {}).sections || [])) {
          for (const f of (s.fields || [])) {
            if (f.key === key) return f.opts || [];
          }
        }
        return [];
      }

      // 当前 fields 的可变副本（下拉与高级文本共用同一个字典）
      const fld = Object.assign({}, o.fields || {});

      const fdWrap = document.createElement("div");
      fdWrap.style.cssText = "display:flex;flex-direction:column;gap:4px;"
        + "flex:1;min-width:0";
      const selects = {};

      /* ⚠️ v5.0.9 修复「下拉没出现」：
         下拉选项来自 `/pools`，而 `ensureData()` 是**异步**的 ——
         用户在面板刚打开时就点「＋ 新增一套」，`CACHE.pools` 还是 null，
         `optsOf()` 全部返回空数组，循环里 `continue` 把每一行都跳过 →
         **整块下拉一个都不渲染**，看起来就像"新功能没生效"。
         现在：渲染时先放一行占位，等 `ensureData()` 落地后**重建**下拉区。 */
      let selectRowsBuilt = false;
      function buildSelectRows() {
        try {
          fdWrap.innerHTML = "";
          for (const k of Object.keys(selects)) delete selects[k];
          const anyPools = optsOf("top").length > 0;
          if (!anyPools) {
            const wait = document.createElement("div");
            wait.className = "av3-meta";
            wait.id = "av3-outfit-wait";
            wait.textContent = "词库加载中…（下拉选项就绪后会自动出现）";
            fdWrap.appendChild(wait);
            selectRowsBuilt = false;
            return;
          }
        for (const [fk, lab] of CLOTH_EDIT_FIELDS) {
          const opts = optsOf(fk);
          if (!opts.length) continue;        // 词库里没有这个字段 → 不渲染

          const line = document.createElement("div");
          line.style.cssText = "display:flex;gap:6px;align-items:center;font-size:12px";
          const nm = document.createElement("span");
          nm.textContent = lab;
          nm.style.cssText = "flex:0 0 74px;color:#9a9a9a";
          line.appendChild(nm);

          const sel = document.createElement("select");
          sel.className = "av3-w";
          sel.dataset.field = fk;
          sel.style.cssText = "flex:1;min-width:0;width:auto;text-align:left;"
            + "padding:3px 6px;font-size:12px";
          const blank = document.createElement("option");
          blank.value = "";
          blank.textContent = "（不锁 · 交给随机）";
          sel.appendChild(blank);
          for (const [zh, en] of opts) {
            const op = document.createElement("option");
            op.value = en;
            op.textContent = `${zh} → ${en}`;
            sel.appendChild(op);
          }
          const cur = String(fld[fk] || "");
          if (cur) {
            if (![...sel.options].some((x) => x.value === cur)) {
              const op = document.createElement("option");
              op.value = cur;
              op.textContent = `${cur}（词库外 · 会被引擎丢弃）`;
              sel.appendChild(op);
            }
            sel.value = cur;
          }
          sel.onchange = () => {
            if (sel.value) fld[fk] = sel.value;
            else delete fld[fk];
            syncAdvanced();
          };
          selects[fk] = sel;
          line.appendChild(sel);
          fdWrap.appendChild(line);
        }
          selectRowsBuilt = true;
        } catch (e) {
          /* 任何一步出错都不许把后面的字段值/备注/底栏一起拖下水 */
          console.warn("[AnimaWardrobeV6] 下拉区构建失败：", e);
          selectRowsBuilt = false;
          fdWrap.innerHTML = "";
          const err = document.createElement("div");
          err.className = "av3-meta";
          err.style.cssText = "color:#e0a458";
          err.textContent = "下拉区构建失败：" + (e && e.message ? e.message
            : String(e)) + "（请用下面的高级文本框）";
          fdWrap.appendChild(err);
        }
      }
      buildSelectRows();

      const fldRow = document.createElement("div");
      fldRow.className = "av3-meta";
      fldRow.style.cssText = "display:flex;gap:8px;align-items:flex-start";
      const fldTag = document.createElement("span");
      fldTag.textContent = "字段值";
      fldTag.style.cssText = "flex:0 0 74px;padding-top:4px";
      fldRow.appendChild(fldTag);
      fldRow.appendChild(fdWrap);
      bd.appendChild(fldRow);

      // 词库没就绪 → 等它，然后重建下拉（不阻塞弹窗其它部分渲染）
      if (!selectRowsBuilt) {
        ensureData().then(() => {
          if (!selectRowsBuilt) buildSelectRows();
        }).catch(() => {
          const w = fdWrap.querySelector("#av3-outfit-wait");
          if (w) w.textContent = "词库加载失败 —— 下拉不可用，请用下面的高级文本框";
        });
      }


      /* 高级：直接编辑 fields 文本（保留旧习惯 + 允许写词库外的实验值）。
         与上面下拉共用 `fld` —— 两边实时同步。 */
      const adv = document.createElement("details");
      adv.style.cssText = "margin:2px 0 4px 82px;font-size:11.5px";
      const advSum = document.createElement("summary");
      advSum.textContent = "高级：直接编辑字段文本（词库外的值会被引擎丢弃）";
      advSum.style.cssText = "cursor:pointer;color:#9a9a9a";
      adv.appendChild(advSum);
      const fdIn = document.createElement("textarea");
      fdIn.className = "av3-w";
      fdIn.rows = 4;
      fdIn.style.cssText = "width:100%;margin-top:4px;padding:4px 6px;"
        + "resize:vertical;text-align:left;font-size:12px";
      fdIn.placeholder = "top: crop top\nbottom: skirt（可留空）";
      adv.appendChild(fdIn);
      bd.appendChild(adv);

      function fldToText() {
        return Object.entries(fld).map(([k, v]) => k + ": " + v).join("\n");
      }
      function syncAdvanced() {
        fdIn.value = fldToText();
        // 下拉跟着文本走（文本里改了词库内的值时，下拉也要亮起来）
        for (const [fk, sel] of Object.entries(selects)) {
          const v = String(fld[fk] || "");
          if (v && ![...sel.options].some((x) => x.value === v)) {
            const op = document.createElement("option");
            op.value = v;
            op.textContent = `${v}（词库外 · 会被引擎丢弃）`;
            sel.appendChild(op);
          }
          sel.value = v;
        }
      }
      fdIn.value = fldToText();
      fdIn.oninput = () => {
        // 文本 → `fld`，再回同步下拉（不回写文本，避免打字时光标跳走）
        const obj = {};
        for (const line of fdIn.value.split(/[\n;]/)) {
          const i = line.indexOf(":");
          if (i < 0) continue;
          const k = line.slice(0, i).trim();
          const v = line.slice(i + 1).trim();
          if (k && v) obj[k] = v;
        }
        for (const k of Object.keys(fld)) delete fld[k];
        Object.assign(fld, obj);
        for (const [fk, sel] of Object.entries(selects)) {
          const v = String(fld[fk] || "");
          if (v && ![...sel.options].some((x) => x.value === v)) {
            const op = document.createElement("option");
            op.value = v;
            op.textContent = `${v}（词库外 · 会被引擎丢弃）`;
            sel.appendChild(op);
          }
          sel.value = v;
        }
      };

      const nvIn = row("备注", inp(o.note, "数据来源 / 实测结论"));

      const vf = document.createElement("label");
      vf.className = "av3-meta";
      vf.style.cssText = "display:flex;gap:6px;align-items:center;padding-left:82px";
      const vfc = document.createElement("input");
      vfc.type = "checkbox";
      vfc.checked = !!o.verified;
      vf.appendChild(vfc);
      vf.appendChild(document.createTextNode("已真机实测（29B / 24 步 / 3 种子）"));
      bd.appendChild(vf);

      const ft = document.createElement("div");
      ft.className = "ft";
      ft.id = "av3-outfit-actions";
      /* ⚠️ v5.0.9：**先挂到 box**，再往里塞按钮。
         原因：`.av3-mini .card` 是 `flex-direction:column` + `max-height:80vh`，
         `.bd` 是滚动区、`.ft` 是固定底栏 —— 只要 ft 是 card 的子节点，它一定可见。
         以前的写法是「所有中间区块渲染完 → 最后才 appendChild(ft)」，
         于是**任何一处中间代码抛异常，底栏就整个消失**（用户实测：
         模板栏出来了，但保存/关闭按钮全没有）。先挂载 = 底栏永远在。 */
      box.appendChild(ft);
      const mk = (text, fn, pri, id) => {
        const b = document.createElement("button");
        b.className = "av3-btn" + (pri ? " pri" : "");
        if (id) b.id = id;
        b.type = "button";
        b.textContent = text;
        b.onclick = fn;
        return b;
      };
      /* 用户 2026-11-03 明确要求：新增自定义服装要有「保存」与「关闭」两个按钮。 */
      ft.appendChild(mk("← 返回列表", () => repaint(), false, "av3-outfit-back"));
      ft.appendChild(mk("保存", async () => {
        /* v5.0.9：fields 直接取 `fld`（下拉与高级文本共用的那份）——
           不再从文本重新解析，否则下拉改了、文本没同步时会丢值。
           高级文本的 oninput 已经把内容实时写回 `fld`，所以这里一定是最新的。 */
        const fields = Object.assign({}, fld);
        const tags = tagIn.value.split(/[,，\n]/).map((x) => x.trim())
          .filter(Boolean);
        /* ⚠️ v5.0.9 修复「新增一套会覆盖上一套」：
           旧版这里写的是 `id: idIn.value.trim() || "default"`，
           而 `save_outfit()` 的语义是「同 id = 更新」——
           新增时 id 框是空的 → 兜底成 "default" → 撞上已有的那套 →
           静默覆盖。用户看到的是"加第二套，第一套没了"。
           现在：① 新增不再兜底成 default，空 id 交给后端生成；
                  ② 新增时显式传 `new: true`，后端保证分到不撞车的 id
                     （`default` → `default_2` → `default_3` …）。
           编辑已有条目仍然按原 id 覆盖，语义不变。 */
        const isNew = !o.id;
        const wantId = idIn.value.trim();
        if (isNew && !wantId) {
          /* 空 id 给一个人类可读的占位，后端 `_unique_outfit_id` 会补序号 */
          idIn.value = "default";
        }
        const d = await outfitOp(role, "save", { outfit: {
          id: idIn.value.trim() || "default",
          zh: zhIn.value.trim(),
          level: Number(lvIn.value) || 0,
          tags, fields,
          verified: vfc.checked,
          note: nvIn.value.trim(),
        }, new: isNew });
        if (d.error) return setStatus(d.error, "warn");
        ui.outfits = d.outfits;
        setStatus(`👗 已保存服装「${zhIn.value.trim() || idIn.value}」`, "ok");
        repaint();
      }), true, "av3-outfit-save");
      /* 关闭：直接移除整个遮罩。与「← 返回列表」不同 —— 返回是回到列表页，
         关闭是彻底退出弹窗（用户要求这两个都要有）。 */
      ft.appendChild(mk("关闭", () => mask.remove(), false, "av3-outfit-close"));
    }

    function repaint() {
      const rec = (ui.outfits || {})[role] || { enabled: "", list: [] };
      box.innerHTML = "";

      /* v5.0.5：取消启用。
         ⚠️ 单选按钮天然**只能换、不能空** —— 第二版之前只能"选另一套"来顶掉，
            而只有一套预设时用户根本没有退路（只能删掉这套或去改 JSON）。
            这里给两条退路：① 再点一次已选中的圆点；② 底部「取消启用」按钮。
            两者都走同一个请求：`POST /outfits/enable` + `id: ""` —— 后端
            `set_outfit_enabled()` 收到空串就是停用（预设本身保留，只是回到随机）。*/
      async function disableOutfit() {
        const d = await outfitOp(role, "enable", { id: "" });
        if (d.error) return setStatus(d.error, "warn");
        ui.outfits = d.outfits;
        setStatus("👗 已取消启用：这个角色出图时服装回到词库随机（预设仍保留）", "ok");
        repaint();
        refreshApplied();          // v6.1.0：右栏「已套用」里的角色预设那行同步
      }

      const hd = document.createElement("div");
      hd.className = "hd";
      hd.textContent = `👗 服装预设 · ${c.zh || c.name}`;
      box.appendChild(hd);

      const tip = document.createElement("div");
      tip.className = "av3-meta";
      tip.style.cssText = "padding:0 12px 8px;line-height:1.5";
      const curId = rec.enabled || "";
      const curItem = (rec.list || []).find((o) => o.id === curId);
      tip.id = "av3-outfit-state";
      tip.innerHTML = curItem
        ? '当前状态：<b style="color:#8ed49b">● 已启用「'
          + (curItem.zh || curId) + '」</b> —— 该角色出图时服装锁定，不再随机。'
          + '<span class="av3-meta">（要恢复随机：点下面的「✕ 取消启用」，'
          + '或再点一次已选中的圆点）</span>'
        : '当前状态：<b style="color:#b0b0b0">○ 未启用</b> —— 该角色出图时'
          + '服装仍按词库随机；选中下面任意一套即可锁定。';
      box.appendChild(tip);

      const bd = document.createElement("div");
      bd.className = "bd";
      bd.style.cssText = "padding:0 12px 8px";
      box.appendChild(bd);

      const list = rec.list || [];
      if (!list.length) {
        const em = document.createElement("div");
        em.className = "av3-empty";
        em.id = "av3-outfit-empty";
        em.textContent = "还没有服装预设 —— 点下面「＋ 新增一套」开始。";
        bd.appendChild(em);
      }
      for (const o of list) {
        const r2 = document.createElement("div");
        r2.className = "av3-row";
        r2.dataset.outfit = o.id;
        const on = (rec.enabled || "") === o.id;
        r2.style.cssText = "display:flex;align-items:center;gap:7px;"
          + "padding:6px 2px;border-bottom:1px solid #2e2e2e"
          + (on ? ";background:rgba(90,186,120,.10)" : "");

        const rb = document.createElement("input");
        rb.type = "radio";
        rb.name = "av3-outfit-enable";
        rb.id = "av3-outfit-radio-" + o.id;
        rb.checked = (rec.enabled || "") === o.id;
        rb.title = on
          ? "已启用 —— 再点一次可取消启用（服装回到随机）"
          : "启用这套（同一角色同时只有一套生效）";
        // v5.0.5：点已选中的圆点 = 取消启用。
        // radio 自身不会因为再点一次而取消，onchange 也不会触发，只能在这里兜住；
        // 与底部「✕ 取消启用」走同一个 disableOutfit()。
        rb.onclick = () => { if (on) disableOutfit(); };
        rb.onchange = async () => {
          const d = await outfitOp(role, "enable", { id: o.id });
          if (d.error) return setStatus(d.error, "warn");
          ui.outfits = d.outfits;
          setStatus(`👗 已启用「${o.zh || o.id}」：本角色服装锁定，不再随机`, "ok");
          repaint();
          refreshApplied();          // v6.1.0：同上
        };
        r2.appendChild(rb);

        const nm = document.createElement("span");
        nm.style.cssText = "flex:1;min-width:0;font-size:12px";
        nm.innerHTML = (on
          ? '<span class="av3-on" style="color:#8ed49b;font-weight:700">'
            + '● 已启用</span> '
          : '<span class="av3-off" style="color:#7d7d7d">○ 未启用</span> ')
          + `<b>${o.zh || o.id}</b> <span class="av3-meta">L${o.level || 0}`
          + ` · ${(o.tags || []).length} 标签</span> `
          + (o.verified
            ? '<span class="av3-aspect av3-aspect-landscape">已实测</span>'
            : '<span class="av3-aspect">未实测</span>');
        nm.title = (o.note || "") + "\n" + (o.tags || []).join(", ");
        r2.appendChild(nm);

        const eb = document.createElement("button");
        eb.className = "av3-btn";
        eb.id = "av3-outfit-edit-" + o.id;
        eb.textContent = "\u270E";
        eb.title = "编辑这套";
        eb.onclick = () => form(o);
        r2.appendChild(eb);

        const db = document.createElement("button");
        db.className = "av3-btn";
        db.id = "av3-outfit-del-" + o.id;
        db.textContent = "\u2715";
        db.title = "删除这套";
        db.onclick = async () => {
          const d = await outfitOp(role, "delete", { id: o.id });
          if (d.error) return setStatus(d.error, "warn");
          ui.outfits = d.outfits;
          setStatus(`已删除服装「${o.zh || o.id}」`, "ok");
          repaint();
        };
        r2.appendChild(db);

        bd.appendChild(r2);
      }

      const ft = document.createElement("div");
      ft.className = "ft";
      const add = document.createElement("button");
      add.className = "av3-btn pri";
      add.id = "av3-outfit-add";
      add.textContent = "＋ 新增一套";
      add.onclick = () => form(null);
      ft.appendChild(add);
      // v5.0.5：只有"当前有启用"时才出现 —— 没启用时这个按钮点了等于空操作，
      // 常驻反而让人以为有隐藏状态。
      if (rec.enabled) {
        const off = document.createElement("button");
        off.className = "av3-btn";
        off.id = "av3-outfit-disable";
        off.textContent = "✕ 取消启用";
        off.title = "取消启用后，这个角色的服装回到词库随机（预设本身保留，随时可再启用）";
        off.onclick = disableOutfit;
        ft.appendChild(off);
      }
      const cl = document.createElement("button");
      cl.className = "av3-btn";
      cl.textContent = "关闭";
      cl.onclick = () => mask.remove();
      ft.appendChild(cl);
      box.appendChild(ft);
    }

    loadOutfits(true).then(repaint);
  }

  /* ── 角色编辑 / 自定义角色（v3.4.0）─────────────────────────────
     数据落在插件目录 presets/user_characters.json（内置角色库只读 ——
     它是 tools/build_characters.py 的联网重建产物，直接改会被重跑覆盖）。
     弹窗字段与后端可编辑键一一对齐：角色标签 / 中文名 / 作品 / 预览图。

     ⚠️ 角色 tag（slug）**不可改**：它是 char_index 的键、也是图片文件名，
        改它等于"删掉一个再造一个"，已保存工作流里的 selected_roles 会失效。
  */

  function findChar(tag) {
    return ((ui.chars && ui.chars.characters) || [])
      .find((c) => c.tag === tag) || null;
  }

  /** 角色写操作统一入口。返回 {ok, error, data}（data 是后端整包响应）。 */
  async function postCharJSON(path, payload) {
    try {
      const r = await api.fetchApi(`/${NS}/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      let d = {};
      try { d = await r.json(); } catch (e) { d = {}; }
      return { ok: r.ok !== false && !d.error, error: d.error || "", data: d };
    } catch (e) {
      return { ok: false, error: e.message || String(e), data: {} };
    }
  }

  /** 写操作返回的是**最新完整角色表**，直接采用并重渲（见后端 _chars_resp）。 */
  function applyCharData(body) {
    if (body && body.data) ui.chars = body.data;
    if (ui.tab === "chars") renderChars();
  }

  /**
   * 拉单个角色的**完整**记录（含全量特征标签 + 内置原始标签）。
   *
   * ⚠️ 列表接口只发 tags[:8]（卡片展示够用）—— 编辑标签必须拿全量，
   *    否则用户会看到"我的标签少了"并把它当成 bug。
   * ⚠️ 用查询串而不是路径参数：角色 tag 里有 1762 条带 `(` `)` `:` 等字符。
   */
  async function getCharDetail(tag) {
    try {
      const r = await api.fetchApi(
        `/${NS}/characters/detail?tag=${encodeURIComponent(tag)}`,
        { cache: "no-store" });
      let d = {};
      try { d = await r.json(); } catch (e) { d = {}; }
      return { ok: r.ok !== false && !d.error, error: d.error || "", data: d };
    } catch (e) {
      return { ok: false, error: e.message || String(e), data: {} };
    }
  }

  function openCharEditor(rec, isNew) {
    const orig = rec || {};
    const isUser = !!orig.user;

    const maskEl = document.createElement("div");
    maskEl.className = "av3-mask";
    const inner = document.createElement("div");
    inner.className = "av3-mini";
    inner.style.position = "static";
    maskEl.appendChild(inner);
    const box = document.createElement("div");
    box.className = "card av3-editor";
    inner.appendChild(box);

    const hd = document.createElement("div");
    hd.className = "hd";
    hd.textContent = isNew ? "添加自定义角色"
      : `编辑角色 · ${orig.zh || orig.name || orig.tag}`;
    if (!isNew) {
      const tg = document.createElement("span");
      tg.className = "tag";
      tg.textContent = orig.tag || "";
      hd.appendChild(tg);
    }
    box.appendChild(hd);

    const bd = document.createElement("div");
    bd.className = "bd";
    box.appendChild(bd);

    /* 错误行在这里就声明（DOM 里挂在最后）—— 下面好几个异步分支都要写它 */
    const err = document.createElement("div");
    err.className = "av3-eerr";

    const mkField = (label, el) => {
      const row = document.createElement("div");
      row.className = "av3-efld";
      const l = document.createElement("label");
      l.textContent = label;
      row.appendChild(l);
      row.appendChild(el);
      bd.appendChild(row);
      return el;
    };

    const zhIn = document.createElement("input");
    zhIn.type = "text";
    zhIn.maxLength = 24;
    zhIn.placeholder = "留空则显示英文名";
    zhIn.value = orig.zh || "";
    mkField("中文名", zhIn);

    const workIn = document.createElement("input");
    workIn.type = "text";
    workIn.maxLength = 60;
    workIn.placeholder = "选现有作品，或直接输一个新的";
    workIn.value = orig.work || "";
    workIn.setAttribute("list", "av3-worklist");
    mkField("作品", workIn);
    const dl = document.createElement("datalist");
    dl.id = "av3-worklist";
    for (const w of ((ui.chars && ui.chars.works) || [])) {
      const o = document.createElement("option");
      o.value = w.en;
      o.label = w.zh || w.en;
      dl.appendChild(o);
    }
    bd.appendChild(dl);

    const trigIn = document.createElement("input");
    trigIn.type = "text";
    trigIn.placeholder = "如 hatsune miku, vocaloid（英文小写）";
    trigIn.value = orig.trigger || "";
    mkField("角色标签", trigIn);

    /* 预览图：本地选图 → 存成 data URL，**点保存时才上传**
       （避免用户选了图又取消，磁盘里留一张没人引用的图）。 */
    const thumbRow = document.createElement("div");
    thumbRow.className = "av3-ethumb";
    const pv = document.createElement("div");
    pv.className = "pv";
    const paintPreview = (src) => {
      pv.innerHTML = "";
      if (src) {
        const im = document.createElement("img");
        im.src = src;
        im.onerror = () => {
          pv.innerHTML = "";
          pv.textContent = (orig.zh || orig.name || "?").slice(0, 1).toUpperCase();
        };
        pv.appendChild(im);
      } else {
        pv.textContent = (orig.zh || orig.name || "?").slice(0, 1).toUpperCase();
      }
    };
    paintPreview(orig.thumb ? thumbURL(orig.thumb) : "");
    thumbRow.appendChild(pv);

    const ops = document.createElement("div");
    ops.className = "ops";
    let pendingImage = null;

    const fileIn = document.createElement("input");
    fileIn.type = "file";
    fileIn.accept = "image/png,image/jpeg,image/webp";
    fileIn.style.display = "none";

    const pickBtn = document.createElement("button");
    pickBtn.className = "av3-btn";
    pickBtn.textContent = "🖼 选择本地图片";
    pickBtn.onclick = () => fileIn.click();
    fileIn.onchange = () => {
      const f = fileIn.files && fileIn.files[0];
      if (!f) return;
      if (!/^image\/(png|jpeg|webp)$/.test(f.type || "")) {
        err.textContent = "只支持 PNG / JPG / WEBP 图片";
        return;
      }
      if (f.size > 2 * 1024 * 1024) {
        err.textContent = "图片过大（上限 2MB）";
        return;
      }
      err.textContent = "";
      const rd = new FileReader();
      rd.onload = () => {
        pendingImage = String(rd.result || "");
        paintPreview(pendingImage);
      };
      rd.readAsDataURL(f);
    };

    const clrThumb = document.createElement("button");
    clrThumb.className = "av3-btn";
    clrThumb.textContent = "↩ 清除自定义图片";
    clrThumb.title = "删掉上传的图，回到内置图 / 首字母色块";
    clrThumb.disabled = isNew;
    clrThumb.onclick = async () => {
      if (!orig.tag) return;
      const r = await postCharJSON("characters/thumb-clear", { tag: orig.tag });
      if (!r.ok) {
        err.textContent = r.error || "清除失败";
        return;
      }
      pendingImage = null;
      const found = findChar(orig.tag);
      paintPreview(found && found.thumb ? thumbURL(found.thumb) : "");
      applyCharData(r.data);
      setStatus("已清除自定义图片", "ok");
    };

    const row1 = document.createElement("div");
    row1.className = "row";
    row1.appendChild(pickBtn);
    row1.appendChild(clrThumb);
    const hint = document.createElement("div");
    hint.className = "av3-meta";
    hint.textContent = "PNG / JPG / WEBP，≤2MB；保存时自动缩到 160px。"
      + "留空 = 用内置图或首字母色块。";
    ops.appendChild(row1);
    ops.appendChild(hint);
    ops.appendChild(fileIn);
    thumbRow.appendChild(ops);
    bd.appendChild(thumbRow);

    /* ── 特征标签（v3.5.0）────────────────────────────────────────
       这是**列表**字段（与 trigger/中文名/作品那种字符串不同），所以 UI 用
       「逐行输入框 + ×」：增 = ＋ 添加标签，删 = 点 ×，改 = 直接改那一行。

       ⚠️ 全量标签要**按需拉** /characters/detail —— 列表接口只带 tags[:8]
          （卡片展示用）。不拉全量的话用户会看到"我的标签少了"。
       ⚠️ 详情没到位前**禁止保存**：否则会把 tags 当空发出，
          静默清掉已有的覆盖（这个坑比"少存一条"严重得多）。
       ⚠️ 保存时若标签与内置完全一致 → 发空数组（= 不写覆盖），
          这样"恢复内置标签"能真的把覆盖清掉，而不是写一份和内置一样的副本。 */
    let tagsLoaded = isNew;
    let onTagsReady = null;
    let builtinTags = [];

    const normTags = (arr) => (arr || [])
      .map((s) => String(s == null ? "" : s).trim().toLowerCase())
      .filter(Boolean);
    const sameTags = (a, b) => {
      const x = normTags(a);
      const y = normTags(b);
      return x.length === y.length && x.every((v, i) => v === y[i]);
    };

    const tagSec = document.createElement("div");
    tagSec.className = "av3-tagsec";
    const tagHead = document.createElement("div");
    tagHead.className = "av3-taghead";
    const tagTitle = document.createElement("span");
    tagTitle.className = "ttl";
    tagTitle.textContent = "特征标签";
    tagHead.appendChild(tagTitle);
    const tagMeta = document.createElement("span");
    tagMeta.className = "av3-meta";
    tagHead.appendChild(tagMeta);
    const tagRestore = document.createElement("button");
    tagRestore.className = "av3-btn";
    tagRestore.textContent = "↩ 恢复内置标签";
    tagRestore.title = "把标签还原为内置值（保存后即清除你的覆盖）";
    tagRestore.style.display = "none";
    tagHead.appendChild(tagRestore);
    tagSec.appendChild(tagHead);

    const tagRows = document.createElement("div");
    tagRows.className = "av3-tagrows";
    tagSec.appendChild(tagRows);

    const tagFoot = document.createElement("div");
    tagFoot.className = "av3-tagfoot";
    const addTagBtn = document.createElement("button");
    addTagBtn.className = "av3-btn";
    addTagBtn.textContent = "＋ 添加标签";
    const tagCount = document.createElement("span");
    tagCount.className = "av3-meta";
    tagFoot.appendChild(addTagBtn);
    tagFoot.appendChild(tagCount);
    tagSec.appendChild(tagFoot);

    const tagHint = document.createElement("div");
    tagHint.className = "av3-meta";
    tagHint.textContent = "用在外貌对齐：体型标签（large breasts / flat chest 等）"
      + "直接生效；发色瞳色标签只在该角色没有发色瞳色记录时才生效。"
      + "单个 ≤40 字符（更长的会被引擎忽略），最多 40 条。"
      + "清空全部 = 恢复内置标签。";
    tagSec.appendChild(tagHint);

    const tagInputs = () => [...tagRows.querySelectorAll("input")];
    const tagValues = () => tagInputs()
      .map((i) => String(i.value || "").trim()).filter(Boolean);

    function paintTagCount() {
      tagCount.textContent = `已 ${tagValues().length} 条（上限 40）`;
    }

    function mkTagRow(value) {
      const row = document.createElement("div");
      row.className = "av3-tagrow";
      const inp = document.createElement("input");
      inp.type = "text";
      inp.maxLength = 40;
      inp.value = value || "";
      inp.placeholder = "如 large breasts";
      inp.oninput = paintTagCount;
      const del = document.createElement("button");
      del.className = "av3-btn";
      del.textContent = "\u00d7";
      del.title = "删除这条特征标签";
      del.onclick = () => { row.remove(); paintTagCount(); };
      row.appendChild(inp);
      row.appendChild(del);
      tagRows.appendChild(row);
      return inp;
    }

    function setTagRows(list) {
      tagRows.innerHTML = "";
      for (const t of list) mkTagRow(t);
      paintTagCount();
    }

    addTagBtn.onclick = () => {
      const i = mkTagRow("");
      paintTagCount();
      if (i.focus) i.focus();
    };
    tagRestore.onclick = () => {
      setTagRows(builtinTags);
      tagRestore.style.display = "none";
      tagMeta.textContent = `内置 ${builtinTags.length} 条（保存后清除覆盖）`;
      err.textContent = "";
    };

    function loadTags() {
      if (isNew) {
        tagsLoaded = true;
        setTagRows([]);
        tagMeta.textContent = "新角色（无内置标签）";
        tagRestore.style.display = "none";
        if (onTagsReady) onTagsReady();
        return;
      }
      tagRows.innerHTML = '<div class="av3-meta">正在读取特征标签…</div>';
      tagMeta.textContent = "";
      getCharDetail(orig.tag).then((res) => {
        if (!res.ok) {
          tagRows.innerHTML = "";
          const retry = document.createElement("button");
          retry.className = "av3-btn";
          retry.textContent = "↻ 重试读取";
          retry.onclick = () => { err.textContent = ""; loadTags(); };
          tagRows.appendChild(retry);
          err.textContent = "特征标签读取失败：" + (res.error || "未知原因")
            + "（已禁止保存，避免误清空）";
          return;
        }
        builtinTags = res.data.builtin_tags || [];
        setTagRows(res.data.tags || []);
        tagMeta.textContent = builtinTags.length
          ? `内置 ${builtinTags.length} 条${res.data.tags_overridden ? " · 已被修改" : ""}`
          : "内置无标签";
        tagRestore.style.display = builtinTags.length ? "" : "none";
        tagsLoaded = true;
        if (onTagsReady) onTagsReady();
      });
    }
    loadTags();

    bd.appendChild(tagSec);
    bd.appendChild(err);

    const ft = document.createElement("div");
    ft.className = "ft";
    const mkBtn2 = (text, cls, fn) => {
      const b = document.createElement("button");
      b.className = "av3-btn" + (cls ? " " + cls : "");
      b.textContent = text;
      b.onclick = fn;
      return b;
    };

    if (!isNew) {
      /* 两步确认而不是 window.confirm（jsdom 没实现 confirm，测试跑不了）。
         v3.18.6：**内置角色也能删除**了 ——
           · 「↺ 恢复默认」只清用户覆盖（改过的标签/中文名），角色本身还在
           · 「🗑 删除角色」把角色从库里移除（内置的会写 tombstone） */
      const armBtn = (text, path, okMsg, confirmText) => {
        let armed = false;
        let timer = null;
        const b = mkBtn2(text, "", async () => {
          if (!armed) {
            armed = true;
            b.textContent = confirmText || "⚠ 再点一次确认";
            timer = setTimeout(() => { armed = false; b.textContent = text; }, 4000);
            return;
          }
          if (timer) clearTimeout(timer);
          const r = await postCharJSON(path, { tag: orig.tag });
          if (!r.ok) {
            err.textContent = r.error || "操作失败";
            return;
          }
          applyCharData(r.data);
          maskEl.remove();
          setStatus(okMsg, "ok");
        });
        return b;
      };
      if (!isUser) {
        ft.appendChild(armBtn("↺ 恢复默认", "characters/reset",
                              "已恢复默认（角色仍在库里）"));
      }
      ft.appendChild(armBtn("🗑 删除角色", "characters/delete",
                            isUser ? "已删除自定义角色" : "已从角色库删除（可在 presets/_retired/ 找回）",
                            "⚠ 再点一次确认删除"));
    }

    ft.appendChild(mkBtn2("取消", "", () => maskEl.remove()));

    const save = mkBtn2(isNew ? "添加角色" : "保存", "pri", async () => {
      /* ⚠️ 详情没到位就不许保存 —— 否则 tags 会被当空发出，
         静默清掉用户已有的标签覆盖。 */
      if (!tagsLoaded) {
        err.textContent = "特征标签还没读出来，先点上面的「↻ 重试读取」";
        return;
      }
      const trigger = String(trigIn.value || "").trim();
      if (!trigger) {
        err.textContent = "角色标签不能为空";
        return;
      }
      // 与内置完全一致 → 发空数组（= 不写覆盖），"恢复内置标签"才真的清得掉
      let tagList = tagValues();
      if (!isNew && sameTags(tagList, builtinTags)) tagList = [];
      const fields = {
        trigger,
        zh: String(zhIn.value || "").trim(),
        work: String(workIn.value || "").trim(),
        tags: tagList,
      };
      save.disabled = true;
      const res = await postCharJSON("characters/save", {
        mode: isNew ? "create" : "edit",
        tag: orig.tag || "",
        fields,
      });
      if (!res.ok) {
        err.textContent = res.error || "保存失败";
        save.disabled = false;
        return;
      }
      const tag = (res.data.character && res.data.character.tag) || orig.tag;
      let last = res.data;
      if (pendingImage && tag) {
        const r2 = await postCharJSON("characters/thumb",
                                      { tag, data_url: pendingImage });
        if (!r2.ok) {
          applyCharData(res.data);
          err.textContent = "角色已保存，但图片保存失败：" + (r2.error || "");
          save.disabled = false;
          return;
        }
        last = r2.data;
      }
      applyCharData(last);
      maskEl.remove();
      if (isNew) {
        // 新角色人气为 0，按人气排序会落到很后面 —— 直接把搜索框填成它，
        // 用户一睁眼就看见刚加的那位（不然会以为"添加没生效"）。
        const c = (last.data && last.data.characters || [])
          .find((x) => x.tag === tag);
        ui.charWork = "";
        ui.charQuery = String((c && (c.zh || c.name)) || "").toLowerCase();
        ui.charLimit = 96;
        renderChars();
        setStatus("已添加自定义角色（左侧列表已筛选到它）", "ok");
      } else {
        setStatus("已保存角色修改", "ok");
      }
    });
    ft.appendChild(save);
    box.appendChild(ft);

    /* 特征标签没读出来之前，保存是禁用的（见 loadTags 的注释） */
    save.disabled = !tagsLoaded;
    onTagsReady = () => { save.disabled = false; };

    maskEl.onclick = (e) => {
      if (e.target === maskEl) maskEl.remove();
    };
    document.body.appendChild(maskEl);
    if (trigIn.focus) trigIn.focus();
  }

  function renderChars() {
    // v5.3.1：中文输入法组合期间**不重画**（否则输入框被换掉，拼音被当成结果提交）。
    // pending 标记保证上屏后会补一次完整重绘，界面不会停在旧状态。
    if (charComposing) { charPendingRender = true; return; }
    loadFavorites();
    loadWorkFavorites();   // v5.0.4：作品 IP 收藏（与角色收藏并行拉，互不阻塞）
    const d = ui.chars;
    if (!d) {
      left.innerHTML = `<div class="av3-empty">正在加载角色库…</div>`;
      loadChars();
      return;
    }
    if (!d.available || !d.characters.length) {
      left.innerHTML = `<div class="av3-empty">角色库还是空的。<br>
        先跑 <code>python tools/build_characters.py</code> 生成
        <code>presets/anima_v4_characters.json</code>，<br>
        再跑 <code>tools/fetch_character_thumbs.py</code> 补缩略图。</div>`;
      return;
    }

    left.innerHTML = "";
    left.classList.add("charmode");   // v6.1.0：网格撑满剩余高度（见 .av3-left.charmode）
    const sel = selectedList();

    /* 顶部操作条 */
    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    const qIn = document.createElement("input");
    qIn.type = "text";
    qIn.placeholder = "搜索角色（中文名 / 英文名 / tag / 作品）";
    qIn.value = ui.charQuery;
    /* ⚠️ v5.3.1 BUG 修复（用户 2026-11-05 19:23「输入中文它会自动刷新为英文，
       不让我打字输入中文」）：角色库搜索框**没有 IME 保护**，而场景页早就有
       （见 sceneSearchBar 那段 v4.5.40 的说明）。根因是同一个：
       输入法的拼音还在组合串里没上屏，`ui.charQuery` 已经被每次 keystroke 写成了
       半截拼音，紧接着 `renderChars()` 又把输入框按 `ui.charQuery` 重建成拼音 ——
       用户看到的就是「打中文自动变英文」。

       修法三层，缺一层都会复发：
         ① 组合期间（`isComposing` / `_av3composing`）**一次都不写 charQuery**；
         ② `renderChars()` 在组合期间**整体推迟**（charPendingRender），
            绝不重建输入框 —— 场景页是整列重画，这里是异步加载器（收藏/作品收藏）
            回来时重画，效果一样：节点被换掉，组合串就丢；
         ③ compositionend 后才写值 + 重绘。 */
    qIn.addEventListener("compositionstart", () => { charComposing = true; });
    qIn.addEventListener("compositionend", () => {
      charComposing = false;
      ui.charQuery = qIn.value.trim().toLowerCase();
      ui.charLimit = 96;
      if (charPendingRender) { charPendingRender = false; renderChars(); }
      else renderGrid();
    });
    qIn.oninput = (e) => {
      if (charComposing || (e && e.isComposing)) return;
      ui.charQuery = qIn.value.trim().toLowerCase();
      ui.charLimit = 96;
      renderGrid();
    };
    bar.appendChild(qIn);
    const addBtn = document.createElement("button");
    addBtn.className = "av3-btn pri";
    addBtn.textContent = "＋ 添加角色";
    addBtn.title = "新增一个自定义角色：自填角色标签、中文名、作品与预览图";
    addBtn.onclick = () => openCharEditor(null, true);
    bar.appendChild(addBtn);
    const clearBtn = document.createElement("button");
    clearBtn.className = "av3-btn";
    clearBtn.textContent = "清除已选角色";
    clearBtn.disabled = !sel.length;
    clearBtn.onclick = () => {
      writeSelected([]);
      renderChars();
      setStatus("已清除全部已选角色", "");
    };
    bar.appendChild(clearBtn);

    /* ── 特征筛选入口（2026-10-29）─────────────────────────────────
       点开后在下方展开面板；面板内的按钮点击只重绘网格与自身状态，
       不走 renderChars() —— 那会重建整列 DOM，滚动位置和输入焦点全丢。 */
    const traitBtn = document.createElement("button");
    traitBtn.className = "av3-btn" + (ui.charTraitOpen ? " pri" : "");
    traitBtn.textContent = "🎨 特征筛选" + (ui.charTraits.size ? ` ${ui.charTraits.size}` : "");
    traitBtn.title = "按外形特征筛角色：猫耳 / 精灵耳 / 白发 / 粉发……（组内或、组间且）";
    traitBtn.onclick = () => {
      ui.charTraitOpen = !ui.charTraitOpen;
      renderChars();
    };
    bar.appendChild(traitBtn);

    /* v3.18.6：多选删除（内置角色也能删）——
       打开后点卡片是"勾选"，与"加入已选角色"互斥，避免误点把角色加进提示词。 */
    const multiBtn = document.createElement("button");
    multiBtn.className = "av3-btn" + (ui.charMulti ? " pri" : "");
    multiBtn.textContent = ui.charMulti ? "☑ 多选中" : "☑ 多选";
    multiBtn.title = "打开后点卡片是勾选（不加入已选角色），用于批量删除";
    multiBtn.onclick = () => {
      ui.charMulti = !ui.charMulti;
      ui.charPicked.clear();
      renderChars();
    };
    bar.appendChild(multiBtn);

    if (ui.charMulti) {
      const picked = ui.charPicked;
      const delAll = document.createElement("button");
      delAll.className = "av3-btn";
      delAll.id = "av3-chars-delall";
      delAll.textContent = `🗑 删除所选（${picked.size}）`;
      delAll.disabled = !picked.size;
      delAll.title = "两步确认后删除；内置角色会写进 presets/_retired/ 的 tombstone";
      let armed = false;
      let timer = null;
      delAll.onclick = async () => {
        if (!picked.size) return;
        if (!armed) {
          armed = true;
          delAll.textContent = `⚠ 再点一次确认删除 ${picked.size} 个`;
          timer = setTimeout(() => {
            armed = false;
            delAll.textContent = `🗑 删除所选（${picked.size}）`;
          }, 4000);
          return;
        }
        if (timer) clearTimeout(timer);
        const r = await postCharJSON("characters/delete", { tags: [...picked] });
        if (!r.ok) {
          setStatus(r.error || "删除失败", "no");
          return;
        }
        // ⚠️ postCharJSON 的 `data` 是**整个响应体**，deleted/failed 在它里面
        const body = r.data || {};
        const n = (body.deleted || []).length;
        const bad = (body.failed || []).length;
        ui.charPicked.clear();
        ui.charMulti = false;
        applyCharData(r.data);
        setStatus(bad ? `已删除 ${n} 个，失败 ${bad} 个` : `已删除 ${n} 个角色`, "ok");
      };
      bar.appendChild(delAll);
    }

    /* 🎲 按当前筛选随机抽角色（v3.8.0）
       候选池 = 当前的「作品筛选 + 关键词 + 收藏视图」结果 —— 也就是你在左边
       点某个 IP、或在搜索框里打了字之后看到的那些人。
       抽到的角色**追加**进「已选角色」（与手动点卡片同一语义：都是"加一位"），
       想换一批先点「清除已选角色」。

       ⚠️ 为什么不新增节点控件：抽到谁最终落在 `selected_roles` 里，而它本来
          就随工作流保存 —— 所以"抽到谁"是可复现的，不需要再开一个控件口径。
          控件一旦新增就要配工作流迁移脚本，为了一个面板动作不值当。 */
    const rndN = document.createElement("input");
    rndN.type = "number";
    rndN.min = "1";
    rndN.max = "6";
    rndN.step = "1";
    rndN.value = String(ui.charRndN || 1);
    rndN.title = "一次抽几位（1–6）";
    rndN.style.cssText = "width:56px;flex:0 0 auto";
    rndN.onchange = () => {
      const v = Math.max(1, Math.min(6, Math.round(Number(rndN.value) || 1)));
      rndN.value = String(v);
      ui.charRndN = v;
    };
    bar.appendChild(rndN);
    const rndBtn = document.createElement("button");
    rndBtn.className = "av3-btn";
    rndBtn.textContent = "🎲 随机抽";
    rndBtn.title = "从当前筛选结果（作品 + 关键词 + 特征）里随机抽角色，直接加入「已选角色」";
    rndBtn.onclick = () => {
      const pool = filtered();
      if (!pool.length) {
        setStatus("当前筛选下没有角色可抽（先换个作品、清掉关键词，或去掉几个特征）", "no");
        return;
      }
      const cur = selectedList();
      const have = new Set(cur);
      const rest = pool.filter((c) => !have.has(c.tag));
      if (!rest.length) {
        setStatus(`当前筛选的 ${pool.length} 位已经全选上了`, "");
        return;
      }
      const want = Math.max(1, Math.min(6, Math.round(Number(rndN.value) || 1)));
      const take = Math.min(want, rest.length);
      // Fisher–Yates：不重复地抽 take 位（同一位角色不能出现两次，
      // 否则模型会把它当成两个人 —— 与后端 pick_random_roles 同一条理由）
      const arr = rest.slice();
      for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        const t = arr[i];
        arr[i] = arr[j];
        arr[j] = t;
      }
      const got = arr.slice(0, take);
      writeSelected(cur.concat(got.map((c) => c.tag)));
      paintChips();
      paintCards();
      setStatus(`🎲 从「${ui.charWork || "全部作品"}」抽到 `
        + got.map((c) => c.zh || c.name).join("、")
        + `（共 ${cur.length + got.length} 位）`, "ok");
    };
    bar.appendChild(rndBtn);
    left.appendChild(bar);

    /* ── 🚫 不指定角色（role_pick，v4.1.0）───────────────────────────
       "这一批图不要角色，人物长相全交给词库权重"。
       与「已选角色 / 随机池 / 仅收藏」是**压制**关系：勾上之后它们全部让路，
       但一个字都不销毁 —— 取消勾选立刻恢复，所以是个安全的临时开关。

       ⚠️ 为什么不直接往 `lock_fields` 里塞 `role`：那个字段的语义是
          "留给上游给值"（特写页用它把画框锁在局部），而这里要的是
          "别抽角色、但照常抽特征" —— 两回事。而且 lock_fields 在面板里
          没有可见入口（v3.18.4 的坑：用户根本不知道自己锁了什么）。
       ⚠️ 状态存在**节点控件**里而不是 localStorage：预演与实跑共用同一个
          引擎，开关只活在浏览器里就会出现"预演没角色、实跑有角色"。 */
    const modeBox = document.createElement("div");
    // ⚠️ 刻意**不复用** `.av3-chips`：那个 class 是"已选角色 chip 行"的选择器，
    //    测试与场景页都靠 `querySelector(".av3-chips")` 取第一行，
    //    这里混进去会把它们全部取错（实测 6 条冒烟用例直接红）。
    modeBox.className = "av3-rolemode";
    modeBox.style.cssText = "display:flex;gap:5px;flex-wrap:wrap;"
      + "align-items:center;margin:6px 0 9px;";
    left.appendChild(modeBox);

    function paintNoRole() {
      modeBox.innerHTML = "";
      const lab = document.createElement("label");
      lab.className = "av3-meta";
      lab.style.cssText = "display:inline-flex;align-items:center;gap:6px;cursor:pointer;";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = "av3-role-pick-off";
      cb.title = "勾选后本次不抽角色：发色 / 瞳色 / 体型 / 发型全部按词库权重随机"
        + "\n已选角色与随机池不会被清掉，取消勾选即恢复";
      cb.checked = lsGet(node, "role_pick", true) === false;
      cb.onchange = () => {
        wset(node, "role_pick", !cb.checked);
        app.graph.setDirtyCanvas(true, true);
        paintNoRole();
        paintChips();
        paintPool();
        setStatus(cb.checked
          ? "🚫 不指定角色：本次不抽角色，人物特征全部按词库权重抽"
          : "已恢复抽角色（按已选 / 随机池 / 全库随机）", "ok");
      };
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode("🚫 不指定角色（只按词库权重抽特征）"));
      modeBox.appendChild(lab);

      const tip = document.createElement("span");
      tip.className = "av3-meta";
      if (cb.checked) {
        const nSel = selectedList().length;
        const nPool = poolList().length;
        tip.textContent = "（本次不出角色"
          + (nSel ? `；已选 ${nSel} 位` : "")
          + (nPool ? `；随机池 ${nPool} 个作品` : "")
          + " 都原样留着，取消勾选即恢复）";
        tip.classList.add("av3-no");
      } else {
        tip.textContent = "（勾选后不抽角色：长相、发色、瞳色、体型全交给词库权重）";
      }
      modeBox.appendChild(tip);

      /* ── v6.6.0：双女自动补位（用户 2026-11-07）──────────────────────
         痛点：角色库只选 1 位 + 抽到双女场景 → 正文里只有一个角色 tag，
         模型为凑人数只能把**同一个人画两遍**。
         开关写进 node 控件（与上面 role_pick 同规），预演 / 实跑 / 预设
         三处共用同一个真值。 */
      const dlab = document.createElement("label");
      dlab.className = "av3-meta";
      dlab.style.cssText = "display:inline-flex;align-items:center;gap:6px;"
        + "cursor:pointer;" + (cb.checked ? "opacity:.45" : "");
      const dcb = document.createElement("input");
      dcb.type = "checkbox";
      dcb.id = "av3-duo-fill";
      dcb.title = "只选了 1 位角色、又赶上双女/多女人数时，自动补一位"
        + "**同作品**的女性角色（该 IP 只有她一位时回退全库随机），"
        + "避免模型把同一个人画两遍。\n取消勾选 = 完全不介入（与旧版行为一致）。"
        + "\n⚠️ 系统自动随机角色那条路不需要它：那边本来就按人数抽够了。";
      dcb.checked = lsGet(node, "duo_role_fill", true) !== false;
      dcb.disabled = cb.checked;         // 不抽角色时这条没有意义
      dcb.onchange = () => {
        wset(node, "duo_role_fill", dcb.checked);
        app.graph.setDirtyCanvas(true, true);
        setStatus(dcb.checked
          ? "🧑‍🤝‍🧑 双女自动补位已开：只选 1 位角色时，双女场景会自动补同位女性"
          : "双女自动补位已关：双女场景 + 只选 1 位角色时，画面可能出现两个相同角色",
          dcb.checked ? "ok" : "warn");
      };
      dlab.appendChild(dcb);
      dlab.appendChild(document.createTextNode(
        "🧑‍🤝‍🧑 双女场景自动补同位角色（同作品优先）"));
      modeBox.appendChild(dlab);
    }
    paintNoRole();

    /* 特征面板：只在展开时构建，收起时不留空节点 */
    if (ui.charTraitOpen) left.appendChild(buildTraitPanel());

    /* 已选角色 chips（带人数标签预览） */
    const chipBox = document.createElement("div");
    chipBox.className = "av3-chips";
    left.appendChild(chipBox);

    /* ── 随机池（role_works，v3.10.0）───────────────────────────────
       「已选角色」是"抽一次就固定"（写 selected_roles）；
       「随机池」是"每跑一次自动换"（写 role_works，不写 selected_roles）。

       两个维度必须**分开**：前者是这次出图要用的角色，后者是"从哪个作品
       范围里每次重新抽"。合成一个入口的话，用户永远分不清"我点了 🎲 之后
       到底是固定了还是每次换"。 */
    const poolBox = document.createElement("div");
    poolBox.className = "av3-chips";
    left.appendChild(poolBox);

    /* ── 仅收藏内随机（role_fav_only，v3.38.0）───────────────────────
       与「随机池」是两个正交的收窄条件：随机池按作品切，这个按收藏切。
       两个都开 = 交集（该作品 ∩ 我的收藏），后端抽不到时会逐级退化并
       在报告里写明，不会静默变成"没有角色"。 */
    const favBox = document.createElement("div");
    favBox.className = "av3-chips";
    left.appendChild(favBox);

    function paintFav() {
      favBox.innerHTML = "";
      const lab = document.createElement("label");
      lab.className = "av3-meta";
      lab.style.cssText = "display:inline-flex;align-items:center;gap:6px;cursor:pointer;";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!lsGet(node, "role_fav_only", false);
      cb.onchange = () => {
        wset(node, "role_fav_only", cb.checked);
        app.graph.setDirtyCanvas(true, true);
        setStatus(cb.checked
          ? "⭐ 随机角色只在收藏里抽"
          : "随机角色回到全库／作品池", "ok");
      };
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode("⭐ 仅收藏角色内随机"));
      favBox.appendChild(lab);
      const tip = document.createElement("span");
      tip.className = "av3-meta";
      const n = (ui.favorites || new Set()).size;
      tip.textContent = n ? `（收藏 ${n} 位）` : "（还没有收藏）";
      favBox.appendChild(tip);
    }
    paintFav();

    function poolList() {
      return String(lsGet(node, "role_works", "") || "")
        .split(/[|;\n]/).map((s) => s.trim()).filter(Boolean);
    }

    function writePool(list) {
      wset(node, "role_works", list.join("|"));
      app.graph.setDirtyCanvas(true, true);
    }

    const workLabelOf = (slug) => {
      const w = (d.works || []).find((x) => x.id === slug);
      return w ? (w.zh || w.en) : slug;
    };

    function paintPool() {
      const cur = poolList();
      const sel = selectedList();
      poolBox.innerHTML = "";
      const lead = document.createElement("span");
      lead.className = "av3-meta";
      lead.textContent = "🔁 随机池：";
      poolBox.appendChild(lead);

      if (!cur.length) {
        const hint = document.createElement("span");
        hint.className = "av3-meta";
        hint.textContent = "（未限定）—— 点左边某个作品后按「＋ 加入随机池」";
        poolBox.appendChild(hint);
      }
      cur.forEach((slug) => {
        const chip = document.createElement("span");
        chip.className = "av3-chip";
        chip.dataset.pool = slug;
        chip.textContent = workLabelOf(slug);
        chip.title = `每次运行都从这个作品的角色里重新抽（${slug}）`;
        const x = document.createElement("b");
        x.textContent = "\u00d7";
        x.title = "从这个池子里移除";
        x.onclick = () => {
          writePool(cur.filter((s) => s !== slug));
          paintPool();
          setStatus(`已把「${workLabelOf(slug)}」移出随机池`, "");
        };
        chip.appendChild(x);
        poolBox.appendChild(chip);
      });

      const addBtn = document.createElement("button");
      addBtn.className = "av3-btn";
      addBtn.textContent = "＋ 加入随机池";
      addBtn.title = "把当前选中的作品加入「每次自动换」的随机池";
      addBtn.onclick = () => {
        const w = ui.charWork;
        if (!w || w === FAV_ID) {
          setStatus("先在左侧点一个作品 IP，再加入随机池", "no");
          return;
        }
        if (cur.includes(w)) {
          setStatus(`「${workLabelOf(w)}」已经在随机池里了`, "");
          return;
        }
        writePool(cur.concat([w]));
        paintPool();
        setStatus(`已把「${workLabelOf(w)}」加入随机池 —— 每次运行都会换人`
          + (selectedList().length ? "（⚠️ 但已选角色优先，先清除已选才会生效）" : ""),
          selectedList().length ? "no" : "ok");
      };
      poolBox.appendChild(addBtn);

      if (cur.length) {
        const clr = document.createElement("button");
        clr.className = "av3-btn";
        clr.textContent = "清空随机池";
        clr.onclick = () => {
          writePool([]);
          paintPool();
          setStatus("已清空随机池（回到全库随机）", "");
        };
        poolBox.appendChild(clr);
      }

      const note = document.createElement("span");
      note.className = "av3-meta";
      if (lsGet(node, "role_pick", true) === false) {
        // v4.1.0：模式开关压过随机池（池子本身留着，取消勾选即恢复）
        note.textContent = "🚫 已勾选「不指定角色」→ 随机池本次不生效";
        note.classList.add("av3-no");
      } else if (sel.length) {
        note.textContent = "⚠️ 已选角色优先：随机池要等「清除已选角色」后才生效";
        note.classList.add("av3-no");
      } else if (cur.length) {
        note.textContent = "每次运行都从池子里重新抽一位（种子 -1 时）";
      }
      poolBox.appendChild(note);
    }

    function paintChips() {
      const cur = selectedList();
      chipBox.innerHTML = "";
      if (!cur.length) {
        const hint = document.createElement("span");
        hint.className = "av3-meta";
        hint.textContent = lsGet(node, "role_pick", true) === false
          ? "🚫 已勾选「不指定角色」—— 本次不抽角色，人物特征全部按词库权重抽"
          : "未选角色 —— 角色会完全随机。点下面的卡片即选中（抽取概率 100%），"
            + "或用 🎲 按当前筛选随机抽。";
        chipBox.appendChild(hint);
        clearBtn.disabled = true;
        return;
      }
      clearBtn.disabled = false;
      const idx = new Map((d.characters || []).map((c) => [c.tag, c]));
      cur.forEach((tag) => {
        const c = idx.get(tag);
        const chip = document.createElement("span");
        chip.className = "av3-chip";
        chip.textContent = c ? (c.zh || c.name) : tag;
        const x = document.createElement("b");
        x.textContent = "\u00d7";
        x.title = "移除";
        x.onclick = () => {
          writeSelected(cur.filter((t) => t !== tag));
          paintChips();
          paintPool();
          paintCards();
        };
        chip.appendChild(x);
        chipBox.appendChild(chip);
      });
      const note = document.createElement("span");
      note.className = "av3-meta";
      note.style.marginLeft = "6px";
      if (lsGet(node, "role_pick", true) === false) {
        // v4.1.0：勾了「不指定角色」时，人数标签不会被这几位改写 ——
        // 说清楚，否则用户会以为"选了 2 位怎么还是 1girl"
        note.textContent = `共 ${cur.length} 位（🚫 不指定角色已勾选 → 本次不生效，`
          + "人数标签由人数组合决定）";
        note.classList.add("av3-no");
      } else {
        note.textContent = `共 ${cur.length} 位 → 人数标签将写 \`${headcountPreview(cur.length)}\``;
      }
      chipBox.appendChild(note);
    }

    /* 主体：左作品列表 / 右角色网格 */
    const body2 = document.createElement("div");
    body2.className = "av3-cbody";
    const workBox = document.createElement("div");
    workBox.className = "av3-works";
    const gridBox = document.createElement("div");
    gridBox.className = "av3-grid";
    body2.appendChild(workBox);
    body2.appendChild(gridBox);
    left.appendChild(body2);

    /* 作品列表（v5.0.4 起抽成 buildWorkList：收藏的 IP 置顶 + 行尾 ★） */
    buildWorkList(workBox, d);

    /* 角色网格 */
    const cardEls = new Map();

    /* 作品 / 搜索 / 收藏 三层过滤（**不含**特征），特征面板的命中数也基于它算 */
    function baseFiltered() {
      const q = ui.charQuery;
      const favs = ui.favorites || new Set();
      // ⚠️⚠️ 按作品筛选必须用 **work_id**（作品 slug），不能用 c.work。
      //    后端给的角色里，`work` 是**作品英文名**（`zenless zone zero`），
      //    而作品行的 id 是 slug（`zenless_zone_zero`）——
      //    拿两者直接比会永远不相等，表现就是「点任何 IP 作品都是空列表」。
      //    这是 v3.1.0 的线上 bug，根因记在 nodes.characters_payload() 里。
      //
      //    FAV_ID（⭐ 我的收藏）是个**伪作品筛选**，它按收藏集合判，不走 work_id。
      const list = (d.characters || []).filter((c) => {
        if (ui.charWork === FAV_ID) {
          if (!favs.has(c.tag)) return false;
        } else if (ui.charWork && c.work_id !== ui.charWork) {
          return false;
        }
        return matchChar(c, q);
      });
      // ⚠️ 面板自己按人气降序排，不依赖后端给的顺序。
      //    后端确实已经排好了，但"默认按人气排行展示"是这个界面的**契约**，
      //    靠上游顺序实现的话，哪天接口换个排序就悄悄坏掉。
      //    filter() 已经返回新数组，这里 sort 不会污染源数据。
      list.sort((a, b) => (b.pop || 0) - (a.pop || 0));
      return list;
    }

    /* 最终列表 = baseFiltered ∩ 特征筛选（组内或、组间且） */
    function filtered() {
      const base = baseFiltered();
      if (!ui.charTraits.size) return base;
      return base.filter((c) => traitPass(c, ui.charTraits));
    }

    /* ── 特征面板（2026-10-29）─────────────────────────────────────
       每个按钮上标「中文名 + 当前范围内命中数」，选之前就知道会不会筛成空。
       命中数是 2383 角色 × 61 特征 的 indexOf 统计，实测几十毫秒，只在展开时算一次。 */
    function buildTraitPanel() {
      const box = document.createElement("div");
      box.className = "av3-traitpanel";
      const base = baseFiltered();

      const cnt = new Map();
      for (const g of TRAIT_GROUPS) {
        for (const it of g.items) {
          let n = 0;
          for (const c of base) if (charHasTrait(c, it[0])) n++;
          cnt.set(it[0], n);
        }
      }

      const hd = document.createElement("div");
      hd.className = "av3-trait-hd";
      box.appendChild(hd);
      const body = document.createElement("div");
      box.appendChild(body);

      function paintHead() {
        const n = ui.charTraits.size;
        const hit = n ? base.filter((c) => traitPass(c, ui.charTraits)).length : base.length;
        // 工具条上那个按钮也跟着变 —— 不改的话面板里选到 2 项，
        // 外面还显示「特征筛选 1」，看着像没生效（实测踩过）。
        traitBtn.textContent = "🎨 特征筛选" + (n ? ` ${n}` : "");
        hd.innerHTML = "";
        const t = document.createElement("span");
        t.className = "av3-meta";
        t.innerHTML = n
          ? `已选 <b>${n}</b> 项 · 命中 <b>${hit}</b> 位 / 共 ${base.length} 位`
          : `点下面的特征筛角色（当前 ${base.length} 位）`;
        hd.appendChild(t);
        if (n) {
          const cl = document.createElement("button");
          cl.className = "av3-btn";
          cl.textContent = "清空特征";
          cl.onclick = () => {
            ui.charTraits.clear();
            renderChars();
          };
          hd.appendChild(cl);
        }
        const sh = document.createElement("button");
        sh.className = "av3-btn";
        sh.textContent = "收起";
        sh.onclick = () => {
          ui.charTraitOpen = false;
          renderChars();
        };
        hd.appendChild(sh);
      }

      for (const g of TRAIT_GROUPS) {
        const row = document.createElement("div");
        row.className = "av3-traitgrp";
        const lab = document.createElement("span");
        lab.className = "gl";
        lab.textContent = g.zh;
        row.appendChild(lab);
        const wrap = document.createElement("div");
        wrap.className = "items";
        for (const it of g.items) {
          const tag = it[0];
          const n = cnt.get(tag) || 0;
          const on = ui.charTraits.has(tag);
          const b = document.createElement("button");
          b.className = "av3-tbtn" + (on ? " on" : "");
          b.textContent = `${it[1]} ${n}`;
          b.title = n ? `${tag} · ${n} 位` : `${tag} · 当前范围内没有`;
          // 0 命中的禁掉（除非已经选中，那还得允许取消）
          b.disabled = !n && !on;
          b.onclick = () => {
            if (ui.charTraits.has(tag)) ui.charTraits.delete(tag);
            else ui.charTraits.add(tag);
            b.classList.toggle("on", ui.charTraits.has(tag));
            b.disabled = !n && !ui.charTraits.has(tag);
            ui.charLimit = 96;      // 换筛选条件时重置分页，否则可能停在空白页
            paintHead();
            renderGrid();
          };
          wrap.appendChild(b);
        }
        row.appendChild(wrap);
        body.appendChild(row);
      }
      paintHead();
      return box;
    }

    function paintCards() {
      const cur = new Set(selectedList());
      for (const [tag, el] of cardEls) {
        const on = cur.has(tag);
        el.classList.toggle("on", on);
        let tick = el.querySelector(".tick");
        if (on && !tick) {
          tick = document.createElement("span");
          tick.className = "tick";
          tick.textContent = "\u2713";
          el.appendChild(tick);
        } else if (!on && tick) {
          tick.remove();
        }
      }
    }

    function renderGrid() {
      gridBox.innerHTML = "";
      cardEls.clear();
      const list = filtered();
      const shown = list.slice(0, ui.charLimit);

      if (!list.length) {
        const e = document.createElement("div");
        e.className = "av3-empty";
        const favN = (ui.favorites || new Set()).size;
        if (ui.charWork === FAV_ID && !favN) {
          e.innerHTML = "还没有收藏任何角色。<br>"
            + "点角色卡片右上角的 <b>☆</b> 即可收藏 —— "
            + "收藏存在插件目录里，换浏览器、重开 ComfyUI 都还在。";
        } else if (ui.charWork === FAV_ID) {
          e.textContent = "当前关键词下没有已收藏的角色。";
        } else {
          e.textContent = "没有匹配的角色。换个关键词，或点左上「全部作品」。";
        }
        gridBox.appendChild(e);
        return;
      }

      for (const c of shown) {
        const card = document.createElement("div");
        card.className = "av3-card";
        card.dataset.tag = c.tag;      // 测试与调试时用来精确定位到某个角色
        card.title = `${c.zh || c.name}\n${c.tag}\n${c.work} · 人气 ${c.pop}`;

        // 缩略图：抓不到就退化成首字母色块（不阻断浏览）
        const img = document.createElement("img");
        img.className = "ph";
        img.loading = "lazy";
        img.alt = c.zh || c.name;
        img.src = thumbURL(c.thumb);
        img.onerror = () => {
          const ph = document.createElement("div");
          ph.className = "ph letter";
          ph.textContent = (c.zh || c.name || "?").slice(0, 1).toUpperCase();
          img.replaceWith(ph);
        };
        card.appendChild(img);

        /* 收藏星（卡片右上角）。人气徽标改到名字右边 ——
           那个角原本是它，现在要让给 ⭐，两个都堆在右上会叠住。 */
        const favOn = (ui.favorites || new Set()).has(c.tag);
        const star = document.createElement("span");
        star.className = "fav" + (favOn ? " on" : "");
        star.textContent = favOn ? "★" : "☆";
        star.title = favOn ? "取消收藏" : "加入收藏";
        star.onclick = (e) => {
          // ⚠️ 必须挡住冒泡 —— 否则点收藏会顺带把这张卡选中 / 取消选中
          e.stopPropagation();
          const want = !(ui.favorites || new Set()).has(c.tag);
          toggleFav(c.tag, want).then((ok) => {
            if (!ok) return;
            // 在「⭐ 我的收藏」视图里取消收藏 → 卡片应当消失，必须重渲网格。
            // 注意 refreshFavUI 两种情况都要调：它负责更新计数行，
            // 而 renderGrid 只管网格 —— 漏了它计数就停在旧值上。
            if (ui.charWork === FAV_ID) renderGrid();
            refreshFavUI();
          });
        };
        card.appendChild(star);

        /* 编辑入口（卡片右下角）。放右下是因为左上给了「选中打勾」、
           右上给了收藏星，右下是唯一不撞的角。 */
        const editBtn = document.createElement("span");
        editBtn.className = "edit";
        editBtn.textContent = "\u270E";
        editBtn.title = "编辑这个角色（标签 / 中文名 / 作品 / 预览图）";
        editBtn.onclick = (e) => {
          // 同收藏星：必须挡住冒泡，否则点编辑会顺带把这张卡选中
          e.stopPropagation();
          openCharEditor(c, false);
        };
        card.appendChild(editBtn);

        /* v5.0.2：服装预设入口（与编辑按钮并排，靠左一格）。
           用户 2026-11-02：「角色编辑界面增加服装预设 … 启用后锁定人物服装」。 */
        const outfitBtn = document.createElement("span");
        outfitBtn.className = "edit";
        outfitBtn.id = "av3-outfitbtn-" + c.tag;
        outfitBtn.textContent = "\uD83D\uDC57";
        outfitBtn.title = "这个角色的官方服装预设（启用后服装锁定、不再随机）";
        outfitBtn.style.cssText = "right:26px";
        outfitBtn.onclick = (e) => {
          e.stopPropagation();
          openOutfitDialog(c);
        };
        card.appendChild(outfitBtn);

        if (c.user) {
          const bd = document.createElement("span");
          bd.className = "av3-badge";
          bd.textContent = "自定义";
          bd.title = "这是你自己添加的角色（存在 presets/user_characters.json）";
          card.appendChild(bd);
        }

        const nmRow = document.createElement("div");
        nmRow.className = "nmRow";
        const nm = document.createElement("div");
        nm.className = "nm";
        nm.textContent = c.zh || c.name;
        const pp = document.createElement("span");
        pp.className = "pp";
        pp.textContent = c.pop >= 1000 ? `${Math.round(c.pop / 1000)}k` : String(c.pop);
        nmRow.appendChild(nm);
        nmRow.appendChild(pp);
        card.appendChild(nmRow);

        const en = document.createElement("div");
        en.className = "en";
        en.textContent = c.name;
        card.appendChild(en);

        if (ui.charMulti && ui.charPicked.has(c.tag)) {
          const pk = document.createElement("span");
          pk.className = "pick";
          pk.textContent = "\u2713";
          card.appendChild(pk);
        }

        if (!ui.charMulti && new Set(selectedList()).has(c.tag)) {
          const tick = document.createElement("span");
          tick.className = "tick";
          tick.textContent = "\u2713";
          card.appendChild(tick);
        }

        card.onclick = () => {
          if (ui.charMulti) {
            // 多选模式：只勾选，绝不改动 selected_roles（否则删个角色顺手把提示词改了）
            if (ui.charPicked.has(c.tag)) ui.charPicked.delete(c.tag);
            else ui.charPicked.add(c.tag);
            renderChars();
            return;
          }
          // 单/多选：点一次选中，再点取消。人数标签由后端按个数自动定，
          // 这里只负责把 tag 写进控件。
          const cur = selectedList();
          const i = cur.indexOf(c.tag);
          if (i >= 0) cur.splice(i, 1);
          else cur.push(c.tag);
          writeSelected(cur);
          paintChips();
          paintPool();
          paintCards();
        };

        gridBox.appendChild(card);
        cardEls.set(c.tag, card);
      }

      if (list.length > shown.length) {
        const more = document.createElement("div");
        more.className = "av3-more";
        const b = document.createElement("button");
        b.className = "av3-btn";
        b.textContent = `显示更多（还有 ${list.length - shown.length} 位）`;
        b.onclick = () => {
          ui.charLimit += 96;
          renderGrid();
        };
        more.appendChild(b);
        gridBox.appendChild(more);
      } else {
        const done = document.createElement("div");
        done.className = "av3-more av3-meta";
        done.textContent = `共 ${list.length} 位`;
        gridBox.appendChild(done);
      }
    }

    paintChips();
    paintPool();
    renderGrid();
  }

  /**
   * 档位按钮的选中态 + 重渲左列。
   *
   * ⚠️ 必须挂在 buildPanel 作用域，不能塞在 renderRight 里 ——
   *    档位按钮是在**头部**创建的，它的 onclick 闭包看不到
   *    renderRight 内部的函数。之前放在里面，点档位会直接
   *    `ReferenceError: syncLevel is not defined`，档位根本切不动。
   *    这是 UI 冒烟抓出来的（jsdom 的 virtualConsole 报的错）。
   */
  function syncLevel() {
    for (const [k, b] of Object.entries(lvBtns)) b.classList.toggle("on", k === ui.level);
    // 走 syncTabs 而不是 renderTree：否则在「角色库」页切档位会被权重树顶掉
    syncTabs();
  }

  function syncTabs() {
    for (const [k, b] of Object.entries(tabBtns)) b.classList.toggle("on", k === ui.tab);
    if (ui.tab === "chars") {
      loadChars();
      loadFavorites();
      if (ui.chars) renderChars();
      else left.innerHTML = `<div class="av3-empty">正在加载角色库…</div>`;
    } else if (ui.tab === "outfits") {
      /* v5.1.0：服装库升为一级页签（与角色库 / 场景搭配同级）。
         数据走 /outfit-library（懒加载）—— 90 条不算大，但也没必要在开面板时拉。 */
      loadOutfitLibrary();
      if (ui.outfitLib) renderOutfitLibrary();
      else left.innerHTML = `<div class="av3-empty">正在加载服装库…</div>`;
    } else if (ui.tab === "pose") {
      /* v5.1.0：姿势库独立成页 —— 与场景搭配**共用 /scenes**，
         只是强制只看 group=pose（新增的瑜伽 / 伸展全是 manual_only）。 */
      loadScenes();
      if (ui.scenes) renderScenes();
      else left.innerHTML = `<div class="av3-empty">正在加载姿势库…</div>`;
    } else if (ui.tab === "scenes") {
      loadScenes();
      if (ui.scenes) renderScenes();
      else left.innerHTML = `<div class="av3-empty">正在加载场景搭配库…</div>`;
    } else if (ui.tab === "art") {
      /* v5.0.21：艺术构图升为**一级页签**（与场景搭配 / 特写搭配同级）。
         数据同一份 /scenes，只是强制只看 group=art，并把分组条整条藏起来 ——
         页签已经就是"分组"，再摆一排「全部 / 姿势 / 艺术构图」是重复入口。 */
      loadScenes();
      if (ui.scenes) renderScenes();
      else left.innerHTML = `<div class="av3-empty">正在加载艺术构图库…</div>`;
    } else if (ui.tab === "weapon") {
      /* v5.10.0：武器场景升为**一级页签** —— 与 art / pose 同一份 /scenes，
         只是强制只看 group=weapon（48 条全部 manual_only，不进随机池）。
         ⚠️ 少了这个分支，切过去左列会停在词库树（v5.1.0 姿势库踩过同款）。 */
      loadScenes();
      if (ui.scenes) renderScenes();
      else left.innerHTML = `<div class="av3-empty">正在加载武器场景库…</div>`;
    } else if (ui.tab === "near") {
      // v3.18.0：特写搭配与场景搭配**共用同一份数据**（/scenes 一次拉全），
      // 只是各自过滤、各自分组 —— 少一次往返，也不会读到两份不一致的库。
      loadScenes();
      if (ui.scenes) renderNear();
      else left.innerHTML = `<div class="av3-empty">正在加载特写搭配库…</div>`;
    } else {
      renderTree();
    }
  }

  /* ── 场景搭配（v3.9.0）───────────────────────────────────────────
     与角色库同一条纪律：**点到这一页才拉**数据；选中状态落在**权重表**里
     （`fields.n_canon = 3.0` + `options.n_canon` 只有本预设 = 3.0），
     所以不新增任何节点控件、不需要工作流迁移脚本，且随工作流保存。

     ⚠️ 为什么不新增控件：节点侧**没有"字段取值"控件** —— 字段值只能来自
        随机 / 权重 / force_role / 预设。预设这条路的承载位就是 n_canon，
        它已经在权重表里有位置，复用它比新开一个控件干净得多。
  */
  function loadScenes() {
    if (ui.scenes || ui.scenesLoading) return;
    ui.scenesLoading = true;
    api.fetchApi(`/${NS}/scenes`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        ui.scenes = d;
        ui.scenesLoading = false;
        // ⚠️ v5.1.0："pose" 也必须在这里重绘 —— 少了它，切到姿势库后数据回来了
        //    界面却永远停在「正在加载姿势库…」（前端冒烟抓到的真 bug）。
        if (ui.tab === "scenes" || ui.tab === "art" || ui.tab === "pose"
            || ui.tab === "weapon") {
          renderScenes();
        } else if (ui.tab === "near") renderNear();
      })
      .catch((e) => {
        ui.scenesLoading = false;
        console.error("[AnimaV6] 场景搭配库加载失败", e);
        if (ui.tab === "scenes" || ui.tab === "pose" || ui.tab === "weapon") {
          left.innerHTML = `<div class="av3-empty">场景搭配库加载失败：${e.message}</div>`;
        }
      });
  }

  /** 当前套用的场景 id（从权重表反推：字段必现 + 该选项 3.0）。 */
  function appliedSceneId() {
    if (Number(ui.weights.fields.n_canon ?? 1) < 3) return "";
    for (const [en, v] of Object.entries(ui.weights.options.n_canon || {})) {
      if (en.startsWith("sc_") && Number(v) >= 3) return en;
    }
    return "";
  }

  /* ── v6.1.0：姿势库 / 武器场景的「勾选后随机」状态（用户 2026-11-07）───
     用户：「姿势库与武器场景增加勾选后可随机抽取功能」。
     这两组全是 manual_only（不进全局随机池），所以给一条**页内**通道：
     勾若干条 → 从勾选集里抽一条并立刻套用（写回节点权重）。
     存 id 数组而非 Set —— rememberUi 要把它 JSON 化。 */
  function pickArr(key) {
    if (!ui.scenePick) ui.scenePick = {};
    if (!Array.isArray(ui.scenePick[key])) ui.scenePick[key] = [];
    return ui.scenePick[key];
  }

  /* ── v6.7.0：勾选池「每次运行自动随机」────────────────────────────
     用户 2026-11-08：「和场景搭配内的随机抽取功能类似，勾选随机抽取后
     每次运行能够自动更换随机抽取场景，而不是需要手动点一下再随机抽取」。
     真值存在两个节点控件上（入口只有本页的复选框，见 HIDDEN）：
       pick_pool_mode = "" / "pose" / "art" / "near"（互斥，同时只开一组）
       pick_pool_ids  = 逗号分隔的场景 id —— 引擎每次 draw 从这里换一条。
     ⚠️ 勾选变了要立刻同步 ids，否则引擎还用着旧的集合。 */
  function poolMode() {
    return String(wget(node, "pick_pool_mode", "") || "").trim();
  }
  /** 把当前勾选集同步到控件；池子空了自动关掉自动随机。 */
  function syncPoolIds() {
    const m = poolMode();
    if (!m) return;
    const ids = (pickArr(m) || []).join(",");
    wset(node, "pick_pool_ids", ids);
    if (!ids) {
      wset(node, "pick_pool_mode", "");
      setStatus("勾选集已空 → 已关闭「每次运行自动随机」", "warn");
    }
    app.graph.setDirtyCanvas(true, true);
  }
  function setPoolMode(mode) {
    const label = { pose: "姿势库", art: "艺术构图", near: "特写搭配" }[mode] || mode;
    wset(node, "pick_pool_mode", mode || "");
    wset(node, "pick_pool_ids", mode ? (pickArr(mode) || []).join(",") : "");
    app.graph.setDirtyCanvas(true, true);
    if (mode) {
      setStatus(`✅ 每个工作流运行都会从「${label}」的 ${pickArr(mode).length} 条勾选里`
        + "自动换一条（三组互斥，其余已关闭）", "ok");
    } else {
      setStatus("已关闭勾选池自动随机：场景恢复成不自动套用", "warn");
    }
    repaintScenePage(ui.tab === "near" ? "near" : "scenes");
  }

  /** 只更新工具条的计数与按钮可用性 —— 不重画整页，勾选时滚动位置不跳。 */
  function syncPickBar(key) {
    const arr = pickArr(key);
    const bar = document.getElementById("av3-pickbar-" + key);
    if (!bar) return;
    const c = bar.querySelector(".cnt");
    if (c) c.textContent = `${arr.length} 条已勾选`;
    bar.querySelectorAll("button").forEach((b) => {
      // v6.6.0：「全选 / 反选」永远可点 —— 一条都没勾时它俩正是出口，
      // 跟着「随机 / 清空」一起置灰就会把用户卡死在空勾选集里。
      if (b.dataset.keep === "1") return;
      b.disabled = !arr.length;
    });
  }

  /** 从勾选集里随机抽一条并套用。
   *  v6.6.0：`key` 由调用方给（pose / weapon / near）——
   *  原先靠 `ui.tab` 反推，特写页接进来以后反推就不成立了。 */
  function pickRandom(key) {
    const k = key || (ui.tab === "pose" ? "pose"
      : ui.tab === "near" ? "near"
        : ui.tab === "art" ? "art" : "weapon");
    const ids = pickArr(k).slice();
    if (!ids.length) { setStatus("先勾选几条，再点随机", "warn"); return; }
    const all = (ui.scenes && ui.scenes.scenes) || [];
    const pool = ids.map((id) => all.find((s) => s.id === id)).filter(Boolean);
    if (!pool.length) {
      setStatus("勾选的条目已不在库里（可能在别的页签删过）", "warn");
      return;
    }
    const sc = pool[Math.floor(Math.random() * pool.length)];
    applyScene(sc);
    // ⚠️ 不要再手动 repaintScenePage：`applyScene()` 末尾已经按当前页签重绘
    //    （v6.6.2：以前这里多调一次，等于整页渲两遍，勾选集大时明显卡一下）。
    setStatus(`🎲 从 ${pool.length} 条勾选里抽到「${sc.zh}」并已套用`
      + (pool.length === 1 ? "（只勾了一条，等于直接套用它）" : ""), "ok");
  }

  /* ── v6.6.0：勾选随机工具条（姿势库 / 武器场景 / 特写搭配 三页共用）──
     用户 2026-11-07：「增加一键全部选择功能」「特写搭配也增加可勾选随机抽取」。
       · 全选 / 反选的作用域 = **当前筛选结果**（搜索词 / 分类 / 部位都算），
         用户拍板：先搜「跪」再点全选，就该只勾中所有跪姿；无筛选时等于整页全选。
       · 全选是**并集**（不清掉已有勾选）—— 换一次筛选再全选能攒出跨分类的组合；
         想重来用「清空勾选」。
       · 全选 / 反选挂 `data-keep=1`：syncPickBar 不许把它们的 disabled 跟着
         "0 条已勾选"一起打开，否则一条没勾时用户反而没有出口。 */
  function buildPickBar(key, label, visibleIds) {
    const arr = pickArr(key);
    const bar = document.createElement("div");
    bar.className = "av3-pickbar";
    bar.id = "av3-pickbar-" + key;
    const lab = document.createElement("span");
    lab.textContent = label;
    bar.appendChild(lab);
    const cnt = document.createElement("span");
    cnt.className = "cnt";
    cnt.textContent = `${arr.length} 条已勾选`;
    bar.appendChild(cnt);

    const mkScope = (id, text, title, onclick) => {
      const b = document.createElement("button");
      b.className = "av3-btn";
      b.id = id;
      b.textContent = text;
      b.title = title;
      b.dataset.keep = "1";
      // v6.6.1：当前筛选结果为空时没有可勾的东西 —— 直接置灰，
      // 免得点了没反应像是坏了（syncPickBar 不会碰 data-keep 的按钮，
      // 所以这个 disabled 只在重绘时按 visibleIds 重新算）。
      b.disabled = !visibleIds.length;
      b.onclick = onclick;
      return b;
    };
    // 「全选」只有在当前筛选结果里有东西时才可点 —— 空结果没有可勾的
    bar.appendChild(mkScope(
      "av3-pick-all", `☑ 全选（${visibleIds.length}）`,
      "把**当前筛选结果**全部加进勾选（不清掉已有勾选）；"
      + "无筛选时等于整页全选",
      () => {
        if (!visibleIds.length) return;
        for (const id of visibleIds) if (arr.indexOf(id) < 0) arr.push(id);
        syncPickBar(key);
        syncPoolIds();               // v6.7.0：自动随勾选更新
        setStatus(`已全选当前 ${visibleIds.length} 条（共 ${arr.length} 条已勾选）`, "ok");
      }));
    bar.appendChild(mkScope(
      "av3-pick-invert", "⇄ 反选",
      "在当前筛选结果里反转勾选状态（已勾的取消、没勾的勾上）",
      () => {
        if (!visibleIds.length) return;
        for (const id of visibleIds) {
          const i = arr.indexOf(id);
          if (i >= 0) arr.splice(i, 1); else arr.push(id);
        }
        syncPickBar(key);
        syncPoolIds();               // v6.7.0：自动随勾选更新
        setStatus(`已反选当前 ${visibleIds.length} 条（共 ${arr.length} 条已勾选）`, "ok");
      }));

    const roll = document.createElement("button");
    roll.className = "av3-btn pri";
    roll.id = "av3-pick-roll";
    roll.textContent = "🎲 从勾选随机";
    roll.disabled = !arr.length;
    roll.title = "从勾选的条目里抽一条，抽中即套用（与手动点「套用」等价）";
    roll.onclick = () => pickRandom(key);
    bar.appendChild(roll);

    const clr = document.createElement("button");
    clr.className = "av3-btn";
    clr.id = "av3-pick-clear";
    clr.textContent = "清空勾选";
    clr.disabled = !arr.length;
    clr.onclick = () => {
      ui.scenePick[key] = [];
      repaintScenePage(ui.tab === "near" ? "near" : "scenes");
    };
    bar.appendChild(clr);

    /* v6.7.0：**每次运行自动随机**（三组互斥）。勾上之后不需要再点
       「🎲 从勾选随机」—— 引擎每次 draw 自己从勾选集里换一条。 */
    const autoLab = document.createElement("label");
    autoLab.className = "av3-meta";
    autoLab.style.cssText = "display:inline-flex;align-items:center;gap:5px;"
      + "cursor:pointer;margin-left:4px";
    const autoCb = document.createElement("input");
    autoCb.type = "checkbox";
    autoCb.id = "av3-pick-auto-" + key;
    autoCb.checked = poolMode() === key;
    autoCb.disabled = !arr.length;
    autoCb.title = "勾上后**每次运行**都从上面勾选的条目里自动换一条"
      + "（不用再点「🎲 从勾选随机」）\n三组互斥：同时只能开一组。"
      + "\n⚠️ seed 固定时抽到的那条是确定的；seed=-1 才每次换。";
    autoCb.onchange = () => setPoolMode(autoCb.checked ? key : "");
    autoLab.appendChild(autoCb);
    autoLab.appendChild(document.createTextNode("每次运行自动随机"));
    bar.appendChild(autoLab);
    return bar;
  }

  /** n_canon 的全部候选（法典 + 场景），来自 /pools —— 与引擎同源。 */
  function nCanonOptions() {
    const f = findField("n_canon");
    return (f && f.opts ? f.opts : []).map((o) => o.en);
  }

  /* ── 特写搭配的「精简档」（v3.18.1 · 按部位分级）────────────────
     实测依据（2026-10-18 三轮对照，共 20+ 张）：
       · 只关 ②③ 段 → 腿/脚仍出半身（Y_leg），因为 ④身材 / ⑥服装 / 动作 / 自拍 还在拉画框；
       · 再关 ④⑤ + 动作/手部/暗示姿势/设备/构图/质感 → **真成腿部特写**（M_leg：头出画，
         画面主体就是腿，正文只剩 16 个标签）；
       · 面部组同样精简 → **脸 + 肩 + 颈**（M_face），不再给上半身照。
     所以按部位分三档：
       · 面部 / 眼与唇：关 ④⑤ + 上述字段，**保留 ②③ 与角色**（要的就是五官与表情）
       · 上半身与胸  ：只关 figure / leg（保服装与姿势，否则会画成裸体）
       · 手 / 腿 / 脚 / 臀：关 ②③④⑤ + 上述字段，并**锁 role + 清角色**（局部不该有脸）
     ⚠️ 全部可恢复：套用前把"被改动的段 / 字段 / 人数袋 / 角色三件套 / 锁定表"整份快照，
        取消套用（或换套另一条）时原样还回去。 */
  /* ── 「画框里能看见什么」白名单 ────────────────────────────────
     用户口径（v3.18.1）：**特写正文只描述画面里能看见的东西**。
     所以不是"关掉几个捣乱的字段"，而是**反过来**：每个部位只留一列"可见字段"，
     其余全部权重 0（预设自己写进去的字段会自动保留，见 slimFieldsFor）。
     好处：面部特写不会再带 `pantyhose / slippers / legs together` 这种看不见的词；
     腿脚特写也不会再带发型/表情/上衣。 */
  const NEAR_VISIBLE = {
    "面部": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs", "hair_fx",
             "hair_acc", "eyes_color", "emotion", "eye_gaze", "mouth", "skin",
             "skin_detail", "makeup", "accessory", "shot_size", "viewpoint", "dof",
             "place", "weather", "prop", "light_fx", "texture_fx"],
    "眼与唇": ["hair_bangs", "hair_fx", "eyes_color", "emotion", "eye_gaze", "mouth",
               "skin", "makeup", "shot_size", "dof", "place", "light_fx",
               "texture_fx"],
    "上半身与胸": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs",
                   "hair_fx", "hair_acc", "eyes_color", "emotion", "eye_gaze", "mouth",
                   "figure", "skin", "skin_detail", "makeup", "outfit", "cloth_color", "cloth_mat",
                   "cloth_state_sfw", "top", "accessory", "arms_hands", "pose_base",
                   "shot_size", "viewpoint", "dof", "place", "prop", "weather", "comp",
                   "light_fx", "texture_fx"],
    // ⚠️ 四肢/臀不放 `dof`：它的候选里有 `focus on face`，与"只看局部"自相矛盾
    //    （实测 R_leg64 / R_foot85 都抽到过它）。景深要就由 preset 自己写死。
    "手部": ["skin", "arms_hands", "pose_base", "shot_size", "viewpoint",
             "place", "prop", "weather", "light_fx"],
    // v5.0.6：手部里**点名要看脸**的条目（`role_ok`：手 + 脸同框）。
    // 用户 2026-11-02 实测「手指抚唇」：套用后角色被清空、画面成了没身份的侧脸。
    // 这一档 = 手部白名单 + 头发/五官，让选定人物的发型、瞳色、肤色真的进正文。
    "手部·有脸": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs",
                  "hair_fx", "hair_acc", "eyes_color", "emotion", "eye_gaze", "mouth",
                  "skin", "skin_detail", "makeup", "accessory",
                  "arms_hands", "pose_base", "shot_size", "viewpoint", "dof",
                  "place", "prop", "weather", "light_fx"],
    // 成人向：胸/臀/腿/口都有，可见面最宽（figure 描述胸型、skin_detail 描述肤质）
    // 成人向里胸/口/臀的极近景居多，袜鞋多半在画外 —— 只留预设自己写的
    //（`sc_near_nsfw_garter` 写了 socks，照样保留）
    "🔞 成人向": ["skin", "skin_detail", "figure", "pose_base",
                  "shot_size", "viewpoint", "place", "prop", "weather", "light_fx"],
    // 成人向里"看得见脸/头发"的条目（role_ok）：多留 ②头发 ③五官，才认得出是谁
    "🔞 成人向·有脸": ["hair_len", "hair_color", "hair_curl", "hair_tie", "hair_bangs",
                       "hair_fx", "hair_acc", "eyes_color", "emotion", "eye_gaze", "mouth",
                       "skin", "skin_detail", "makeup", "accessory", "figure",
                       "pose_base", "shot_size", "viewpoint", "place", "prop",
                       "weather", "light_fx"],
    "腿部": ["skin", "cloth_color", "sock_color", "socks", "shoes", "pose_base", "shot_size", "viewpoint",
             "place", "prop", "weather", "light_fx"],
    "脚部": ["skin", "cloth_color", "sock_color", "socks", "shoes", "pose_base", "shot_size", "viewpoint",
             "place", "prop", "weather", "light_fx"],
    "臀部": ["skin", "cloth_color", "sock_color", "socks", "shoes", "pose_base", "shot_size", "viewpoint",
             "place", "prop", "weather", "light_fx"],
  };
  /* 段 id → 中文序号（只用于卡片角标的 tooltip，不用引词库） */
  const SECTION_SHORT = { hair: "②头发", face: "③五官", body: "④身材", makeup: "⑤妆容" };
  const NEAR_LOCAL_SECTIONS = ["hair", "face", "body", "makeup"];
  /* 部位 → 要不要关 ②③④⑤ 段（面部组要留着 ②③ 才能出五官与表情） */
  const NEAR_DROP_SECTIONS = {
    "面部": ["body", "makeup"], "眼与唇": ["body", "makeup"], "上半身与胸": [],
    "手部": NEAR_LOCAL_SECTIONS, "腿部": NEAR_LOCAL_SECTIONS,
    "脚部": NEAR_LOCAL_SECTIONS, "臀部": NEAR_LOCAL_SECTIONS,
    "🔞 成人向": NEAR_LOCAL_SECTIONS,
  };
  const NEAR_DROP_ROLE = { "手部": true, "腿部": true, "脚部": true, "臀部": true,
                           "🔞 成人向": true };
  /* 保留角色的部位（面板在这一页给"角色来源"入口） */
  const NEAR_ROLE_PARTS = ["面部", "眼与唇", "上半身与胸"];

  /* ── v6.7.0：白名单**真源在引擎**（/contract.near_slim）──────────
     这张表原先只活在 JS 里；引擎要自己做「勾选池自动随机 + 收紧取景」，
     两边各抄一份必然漂移。现在引擎是唯一真源，这里只做覆盖 ——
     读不到（老后端 / 夹具缺失）才用上面的本地常量兜底。
     ⚠️ 用 Object.assign 就地改：这几个是 const，但对象可变。 */
  if (contract && contract.near_slim) {
    const _ns = contract.near_slim;
    if (_ns.visible) Object.assign(NEAR_VISIBLE, _ns.visible);
    if (_ns.drop_sections) Object.assign(NEAR_DROP_SECTIONS, _ns.drop_sections);
    if (_ns.drop_role) Object.assign(NEAR_DROP_ROLE, _ns.drop_role);
    if (Array.isArray(_ns.local_sections) && _ns.local_sections.length) {
      NEAR_LOCAL_SECTIONS.length = 0;
      NEAR_LOCAL_SECTIONS.push(..._ns.local_sections);
    }
  }

  /** 这条 preset 要清零哪些字段：全字段 −（可见白名单 ∪ 它自己写进去的字段）。 */
  function slimFieldsFor(sc, visKey) {
    const keep = new Set(NEAR_VISIBLE[visKey || sc.part || ""] || []);
    for (const f of (sc.fields || [])) keep.add(f.field);
    keep.add("n_canon");
    // ⚠️ n_extra 是预设自带的"部位补充词"（thighs / toes / ass…）。清掉它
    //    等于把配方的灵魂抹了，而且标签数会掉到自检下限以下（实测 13 条 → F1 红）。
    keep.add("n_extra");
    keep.add("count");   // 人数单独用"钉死单人"处理
    keep.add("role");    // 角色由 applyScene 单独处理（清/留）
    // ⚠️ v3.22.0：`drop_fields` —— 条目**点名要闭麦**的字段，从白名单里摘掉。
    //    为什么必须单独有这个机关：口交特写要"只看见脸和肩"，可 `pose_base` 无论
    //    写哪个值都在给全身画姿势（写 `kneeling` 直接出跪着的一整个人）。
    //    "不写在 tags 里"也不够 —— 白名单里有它就等于权重仍是 1.0，照样随机抽，
    //    抽到 `all fours` 一样把身体拉进画面。只有清零才真的不说话。
    //    必须在 `sc.fields` 之后摘：万一配方同时写了同一个字段（校验会拦），
    //    这里仍以 drop 为准。
    for (const k of (sc.drop || [])) keep.delete(k);
    // ⚠️ v3.22.0：`random_fields` 的字段是"由后端抽一个值写进去"的，
    //    前端必须把它们留在白名单里（权重别清零），否则后端抽出来的值
    //    会被 `blocked_by_weight` 挡在门外 —— 表现是"候选项一个都没生效"。
    for (const k of Object.keys(sc.random || {})) keep.add(k);
    const out = [];
    for (const sec of pools.sections) {
      for (const f of (sec.fields || [])) {
        if (!keep.has(f.key)) out.push(f.key);
      }
    }
    return out;
  }

  /** 这条特写该用哪一档精简；不是特写搭配就返回 null。 */
  function nearSlim(sc) {
    if (!sc || !sc.manual) return null;
    const part = sc.part || "";
    if (!(part in NEAR_VISIBLE)) return null;
    // v3.18.3：成人向里标了 role_ok 的条目（口交 / 舔阴 / 舌与唾液 / 胸沟 / 薄纱）
    // 画框里看得见脸或头发 → 走"有脸"档：保留角色与 ②③，只关 ④⑤。
    // v5.0.6：**任何部位**标 `role_ok` 都按这条走 —— 手部先例「手指抚唇」
    // 要手也要脸（用户 2026-11-02），否则套用时角色被 dropRole 清空，
    // 画面变成"没身份的侧脸"。白名单优先取 `<部位>·有脸`，没有就退回成人向那档。
    if (sc.role_ok) {
      const faceKey = ((part + "·有脸") in NEAR_VISIBLE)
        ? (part + "·有脸") : "🔞 成人向·有脸";
      return {
        part,
        sections: ["body", "makeup"],
        fields: slimFieldsFor(sc, faceKey),
        dropRole: false,
        roleOk: true,
      };
    }
    return {
      part,
      sections: NEAR_DROP_SECTIONS[part] || [],
      fields: slimFieldsFor(sc),
      dropRole: !!NEAR_DROP_ROLE[part],
      roleOk: false,
    };
  }

  /* 锁定字段的读写（v3.18.0 用于"锁住 role = 不自动抽角色"） */
  function lockList() {
    return String(lsGet(node, "lock_fields", "") || "")
      .split(/[,;\n]/).map((t) => t.trim()).filter(Boolean);
  }
  function writeLocks(list) {
    wset(node, "lock_fields", list.join(", "));
  }

  /** 人数钉死：默认单人（`1girl, 1boy` 那种组合会把镜头变成"看着对方"）；
   *  少数条目自带 `count_lock`（如舔阴特写 = `2girls, yuri`）就按它钉。 */
  function countBagFor(sc) {
    const want = (sc && sc.count_lock) || "1girl, solo";
    const f = findField("count");
    const bag = {};
    for (const o of (f && f.opts) || []) bag[o.en] = (o.en === want ? 3.0 : 0.0);
    return bag;
  }

  /** 把精简档写进一份权重表（套用与预览共用同一份逻辑，避免"预览 ≠ 实跑"）。 */
  function applySlimTo(w, prof, sc) {
    w.sections = w.sections || {};
    w.fields = w.fields || {};
    w.options = w.options || {};
    for (const k of prof.sections) w.sections[k] = 0;
    for (const k of prof.fields) w.fields[k] = 0;
    w.options.count = countBagFor(sc);
  }

  /* 把"套用特写时被改掉的东西"整体还回去（换套 / 取消 都走这里）。 */
  function restoreNear() {
    // ⚠️ v3.18.5：基线存在 `weights._nearBase`（随 weights_json 持久化），不是内存变量。
    //    旧实现只存 ui.nearRestore，刷新 / 节点重开即丢 —— 被清零的字段
    //    （`socks` / `shoes` / `outfit` …）就永久停在 0，表现为"腿部服装随机不生效"
    //    （实测：`fields.socks=0` 时袜鞋 8/8 全无；干净权重 8/8 都出）。
    const r = ui.weights._nearBase;
    if (!r) return false;
    for (const [k, v] of Object.entries(r.sections || {})) {
      if (v === null || v === undefined) delete ui.weights.sections[k];
      else ui.weights.sections[k] = v;
    }
    for (const [k, v] of Object.entries(r.fields || {})) {
      if (v === null || v === undefined) delete ui.weights.fields[k];
      else ui.weights.fields[k] = v;
    }
    if (r.countBag === null || r.countBag === undefined) delete ui.weights.options.count;
    else ui.weights.options.count = JSON.parse(JSON.stringify(r.countBag));
    writeSelected(String(r.roles || "").split("|").map((t) => t.trim()).filter(Boolean));
    wset(node, "force_role", r.force || "");
    wset(node, "role_works", r.works || "");
    writeLocks(String(r.locks || "").split(",").map((t) => t.trim()).filter(Boolean));
    const ri = panel.querySelector("#av3-rolein");
    if (ri) ri.value = r.force || "";
    delete ui.weights._nearBase;
    ui.nearRestore = null;
    return true;
  }

  /* ── v3.23.0：套用场景时自动同步 NSFW 等级 ──────────────────────
     用户口径（2026-10-23）：
       · **只升不降** —— 场景档位高于当前等级才升；已经比场景高就不动
         （否则"选个全年龄场景"会把你手动调到 L4 的设置悄悄拉回 0）；
       · **只在手动套用**时生效，随机抽中的场景不改等级（跑图跑着自己改设置最吓人）；
       · 面板留一个勾选开关，默认开，值存 `weights_json._autoLevel`（引擎忽略下划线键，
         随工作流持久化，和 `_nearBase` 同一个套路）。
     为什么需要：场景自带档位（`sc.level` 由 build_scenes 按内容派生），
     但节点上的 `nsfw_level` 是独立控件 —— 套了 L3 场景却停在 sfw，引擎会在
     档位闸门处把场景内容整段挡掉，表现就是"套用了没效果"。 */
  const LEVEL_KEYS = ["sfw", "suggestive", "explicit", "hardcore", "extreme"];

  function autoLevelOn() {
    return ui.weights._autoLevel !== false;
  }

  /** 把等级抬到场景档位（只升不降）。返回提示文案，空串 = 没动。 */
  function syncLevelFor(sc) {
    if (!autoLevelOn()) return "";
    const want = Math.max(0, Math.min(4, Number(sc.level ?? 0)));
    const cur = LEVEL_KEYS.indexOf(String(lsGet(node, "nsfw_level", "sfw")));
    if (cur < 0) return "";                      // 等级是「不限」等未知值 → 不动
    if (want <= cur) return "";                  // 已经够高（或不降）
    wset(node, "nsfw_level", LEVEL_KEYS[want]);
    ui.level = LEVEL_KEYS[want];
    // 记下这次自动调整：取消套用时，只有"等级还停在自动升上去的那档"才还回去
    ui.weights._levelBase = {
      from: LEVEL_KEYS[cur], to: LEVEL_KEYS[want], at: sc.id,
    };
    return `等级已自动从「${LEVEL_SHORT[LEVEL_KEYS[cur]]}」升到「${LEVEL_SHORT[LEVEL_KEYS[want]]}」`;
  }

  /** 一键套用：字段必现 + 其它预设归零 + 本预设命中。 */
  function applyScene(sc) {
    const all = nCanonOptions();
    if (!all.length) {
      setStatus("找不到 n_canon 字段，无法套用场景", "no");
      return;
    }
    // ⚠️ v3.18.5：每次套用前**无条件**回到用户原始状态（看 weights._nearBase，不看内存标志）。
    //    否则上一次清零的字段（socks / shoes / outfit / figure …）会一直停在 0 ——
    //    这正是"腿部服装随机不生效"的根因：面部/成人向条目把 socks/shoes 清零，
    //    基线只存内存，刷新后丢失，切回腿部时袜鞋恒为 0。
    if (ui.weights._nearBase) restoreNear();
    ui.weights.fields.n_canon = 3.0;
    const bag = {};
    for (const en of all) bag[en] = 0;
    bag[sc.id] = 3.0;
    ui.weights.options.n_canon = bag;
    // ⚠️ 段权重不能是 0 —— 否则 persist() 会把 canon 写进 disabled_sections，
    //    整个预设段被关掉，"套用了却没生效"。
    if (Number(ui.weights.sections.canon ?? 1) === 0) ui.weights.sections.canon = 1;

    const prof = nearSlim(sc);
    // v3.32.0：**普通场景**也能声明 `no_role`（画框里没有脸）。
    //   `nearSlim` 只服务特写页（manual_only），普通场景套用根本不经过那段，
    //   于是「第一人称 · 俯视自身」照样会随机抽一位角色 —— 而角色 tag
    //   （`herta` / `siege` …）会把镜头从"自己的身体"拉回一张脸，
    //   正是用户说的"第一人称视角里不该有面部特征"。
    //   这里让普通场景走同一套"清角色 + 锁 role"，只是不精简字段。
    const noRole = !prof && !!sc.no_role;
    let roleUnlocked = false;
    if (prof || noRole) {
      // 快照基线（写进 weights_json，随工作流持久化）：被改动的段/字段原值
      // （null = 原本没设过）+ 人数袋 + 角色三件套 + 锁定表。
      {
        const snapSec = {};
        const snapFld = {};
        for (const k of (prof ? prof.sections : [])) snapSec[k] = ui.weights.sections[k] ?? null;
        for (const k of (prof ? prof.fields : [])) snapFld[k] = ui.weights.fields[k] ?? null;
        ui.weights._nearBase = {
          at: sc.id,
          sections: snapSec,
          fields: snapFld,
          countBag: ui.weights.options.count
            ? JSON.parse(JSON.stringify(ui.weights.options.count)) : null,
          roles: String(lsGet(node, "selected_roles", "") || ""),
          force: String(lsGet(node, "force_role", "") || ""),
          works: String(lsGet(node, "role_works", "") || ""),
          locks: lockList().join(","),
        };
      }
      if (prof) applySlimTo(ui.weights, prof, sc);
      /* v6.6.1：**不再清空已选角色**。
         v3.18 那套「不足 2 位就清空，让引擎按 IP/全库抽两位」写在引擎还不会
         补位的时候；v6.6.0 起引擎会**自动补一位同作品女性**（duo_role_fill），
         前端再清一遍等于把用户点的角色扔掉、换成两位全随机。
         现在只留一句提示，第二位交给引擎挑。 */
      if (prof && sc.count_lock && /^2girls/.test(sc.count_lock) && !prof.dropRole
          && selectedList().length === 1) {
        ui.nearDuoFill = true;      // 状态条用（见下面的 parts）
      }
      if (prof ? prof.dropRole : noRole) {
        // 清角色三件套 + 锁 role：默认"未选角色 → 必抽 1 位"，不清不锁照样会冒出角色 tag
        writeSelected([]);
        wset(node, "force_role", "");
        wset(node, "role_works", "");
        const lk = lockList();
        if (lk.indexOf("role") < 0) writeLocks(lk.concat("role"));
        const ri = panel.querySelector("#av3-rolein");
        if (ri) ri.value = "";
      } else if (lockList().indexOf("role") >= 0) {
        // ⚠️ v3.18.4 修 bug：这一条**要角色**，但「锁定字段」里还留着 role ——
        //    引擎见 locks 含 role 就跳过「自动抽角色」，表现正是
        //    "随机角色 / 作品 IP 都不出人，只有手填 force_role 才能出人"。
        //    那个 role 是之前套用无脸条目时写进去的，写在节点控件上、随工作流持久化，
        //    页面刷新或节点重开后内存快照丢失，锁就永久留着了。
        //    这里直接摘掉；快照已记下原 locks，取消套用会原样还回。
        writeLocks(lockList().filter((t) => t !== "role"));
        roleUnlocked = true;
      }
    }
    // v3.23.0：等级同步（只升不降，见 syncLevelFor 的说明）
    const lvNote = syncLevelFor(sc);
    persist();
    if (ui.tab === "weights") renderTree();
    else if (ui.tab === "chars") renderChars();
    else if (ui.tab === "near") renderNear();
    else if (ui.tab === "scenes" || ui.tab === "art" || ui.tab === "pose"
             || ui.tab === "weapon") renderScenes();
    if (prof) {
      const parts = [];
      if (prof.sections.length) parts.push(`关 ${prof.sections.length} 段`);
      if (prof.fields.length) parts.push(`收 ${prof.fields.length} 个字段`);
      if (prof.dropRole) parts.push("清空角色并锁住 role");
      if (roleUnlocked) parts.push("已解除残留的 role 锁（否则随机/IP 抽角色不生效）");
      if (sc.count_lock) parts.push(`人数钉死「${sc.count_lock}」`);
      if (sc.drop && sc.drop.length) {
        parts.push(`条目点名闭麦 ${sc.drop.length} 个字段（${sc.drop.join(" / ")}）`);
      }
      if (ui.nearDuoFill) {
        parts.push("双女条目：保留你选的 1 位，引擎自动补一位（同作品优先）");
        ui.nearDuoFill = false;
      }
      setStatus(`已套用「${sc.zh}」：字段已固定；并按「${sc.part}」收紧取景`
        + `（${parts.join(" · ")}）`
        + (lvNote ? `；${lvNote}` : "")
        + "，取消套用时全部恢复", "ok");
    } else if (noRole) {
      setStatus(`已套用「${sc.zh}」：这些字段被固定，不再参与随机`
        + "；本场景画框里没有脸 → 已清空角色并锁住 role（角色名会把镜头拉回面部）"
        + (roleUnlocked ? "；已解除残留的 role 锁" : "")
        + (lvNote ? `；${lvNote}` : "")
        + "，取消套用时全部恢复", "ok");
    } else {
      setStatus(`已套用「${sc.zh}」：这些字段被固定，不再参与随机`
        + (lvNote ? `；${lvNote}` : ""), "ok");
    }
    refreshApplied();          // v6.1.0：套用后右栏「已套用」立刻显示这一条
  }

  function clearScene() {
    ui.weights.fields.n_canon = 1.0;
    ui.weights.options.n_canon = {};
    const restored = restoreNear();
    // v3.23.0：把自动升过的等级还回去 —— 但只在这一档还是**它自己升上去**的时候，
    // 用户手动改过（比如又调到 extreme）就不动，免得撤销动作把用户的选择吃掉。
    let lvBack = "";
    const lb = ui.weights._levelBase;
    if (lb && String(lsGet(node, "nsfw_level", "")) === lb.to) {
      wset(node, "nsfw_level", lb.from);
      ui.level = lb.from;
      lvBack = `，等级还原为「${LEVEL_SHORT[lb.from] || lb.from}」`;
    }
    delete ui.weights._levelBase;
    persist();
    if (ui.tab === "weights") renderTree();
    else if (ui.tab === "chars") renderChars();
    else if (ui.tab === "near") renderNear();
    else if (ui.tab === "scenes" || ui.tab === "art" || ui.tab === "pose"
             || ui.tab === "weapon") renderScenes();
    setStatus((restored
      ? "已取消套用，并把段权重 / 字段权重 / 人数袋 / 角色选择 / 锁定字段全部还回去"
      : "已取消场景套用（预设回到随机）") + lvBack, "");
    refreshApplied();          // v6.1.0：取消后右栏那行跟着消失
  }

  /** v3.23.0：「套用时自动同步等级」开关 —— 场景页与特写页共用同一条。 */
  function autoLevelBar() {
    const bar = document.createElement("div");
    bar.className = "av3-levelbar";      // 独立 class：不抢「🎭 角色来源」那条 charbar 的位置
    bar.style.cssText = "display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
      + "padding:6px 10px;margin-bottom:8px;border:1px solid #2c2f3a;border-radius:8px;"
      + "background:#1b1d25;font-size:12.5px";
    const lb = document.createElement("label");
    lb.style.cssText = "display:flex;align-items:center;gap:6px;cursor:pointer";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = "av3-autolevel";
    cb.checked = autoLevelOn();
    lb.appendChild(cb);
    lb.appendChild(document.createTextNode("套用时自动同步 NSFW 等级"));
    const note = document.createElement("span");
    note.className = "av3-meta";
    note.id = "av3-autolevel-note";
    const paint = () => {
      const key = String(lsGet(node, "nsfw_level", "sfw"));
      note.textContent = (cb.checked ? "只升不降" : "已关闭")
        + `｜当前等级：${LEVEL_SHORT[key] || key}`;
    };
    cb.onchange = () => {
      ui.weights._autoLevel = cb.checked;
      persist();
      paint();
      setStatus(cb.checked
        ? "已开启：套用场景时自动把等级抬到场景档位（只升不降，随机抽中不动）"
        : "已关闭：套用场景不再改动等级", "");
    };
    paint();
    bar.appendChild(lb);
    bar.appendChild(note);
    return bar;
  }

  /* ── v3.43.0：场景页的检索 / 收藏 / 浏览增强 ──────────────────────
     一页 190 条，只靠滚动和 5 个档位标题找不到东西，所以补四件：

       ① 搜索 —— 空格分词 AND，命中「中文名 / 题材 / 描述 / 固定字段标签
          （中英）/ 补充串 / 构图变体名 / id」；
       ② 收藏 —— 场景页与特写页**共用一份**，存
          presets/user_scene_favorites.json（值域是场景 id，与角色收藏分开）；
       ③ 题材快捷筛选条 —— 按条数降序，超过 18 个折叠成「更多」；
       ④ 浏览 —— 档位分组可折叠 + 首屏 60 条懒加载（滚到底再 +60）
          + 紧凑行模式。

     ⚠️ 这些全是**纯前端视图**：不改权重表、不碰 rng，
        零漂移基线（关掉时一次 rng 都不消耗）不受影响。
     ⚠️ 题材列表是**已下发数据的派生物**，故意不新增 /scenes 载荷字段 ——
        少一次契约变更，就少一次夹具与断言连锁改动的机会。
  */
  const SCENE_PAGE = 60;          // 首屏渲染条数

  /** 搜索词 → 分词数组（空格 / 逗号分隔，语义是 AND）。 */
  function sceneTokens(q) {
    return String(q || "").toLowerCase().split(/[\s,，]+/).filter(Boolean);
  }

  /** 一条场景的可搜文本，缓存挂在 payload 对象上（内存态，不落盘）。 */
  function sceneHay(sc) {
    if (sc._hay === undefined) {
      sc._hay = [
        sc.zh, sc.theme, sc.desc, sc.id, sc.part,
        // v5.11.0：作品 / 武器也可搜（「朗基努斯」「EVA」「原神」都能命中）
        sc.work, sc.work_zh, sc.weapon, sc.weapon_en,
        (sc.fields || []).map((f) => f.lab + " " + f.en).join(" "),
        sc.extra || "",
        (sc.variants || []).join(" "),
      ].join(" ").toLowerCase();
    }
    return sc._hay;
  }

  /** v5.11.0：武器页第二级筛选的 key。
      选中某个 IP 作品 → 按具体武器名（4 个按钮）；
      选「全部作品」或「通用」→ 按 8 类武器类型（通用 48 条有 48 个不同
      武器名，全列出来会把一排按钮炸掉）。 */
  function weaponCatKey(s) {
    const pickWork = ui.weaponWork && ui.weaponWork !== "all";
    const isIP = s.work && s.work !== "general";
    return String((pickWork && isIP) ? (s.weapon || "") : (s.theme || ""));
  }

  function sceneHit(sc, toks) {
    if (!toks.length) return true;
    const hay = sceneHay(sc);
    return toks.every((t) => hay.indexOf(t) >= 0);
  }

  /** 题材列表：条数降序，同数保持库内书写顺序。 */
  function sceneThemes(list) {
    const order = [];
    const cnt = {};
    for (const sc of list) {
      const t = String(sc.theme || "");
      if (!t) continue;
      if (cnt[t] === undefined) { cnt[t] = 0; order.push(t); }
      cnt[t] += 1;
    }
    const rank = new Map(order.map((t, i) => [t, i]));
    return order.map((t) => ({ name: t, count: cnt[t] }))
      .sort((a, b) => (b.count - a.count) || (rank.get(a.name) - rank.get(b.name)));
  }

  /* ── 场景收藏（⭐）──────────────────────────────────────────────
     与角色收藏**分开一个文件**：值域是场景 id（sc_xxx），塞进
     user_favorites.json 会让「仅收藏内随机」拿场景 id 去角色库比对，
     多出一批永远命中不了的脏 key（见 anima_v4_store 的注释）。
     场景页与特写页共用同一份，所以两边都能看到 ☆/★。 */
  let scFavSeq = 0;

  function loadSceneFavs() {
    if (ui.sceneFav || ui.sceneFavLoading) return;
    ui.sceneFavLoading = true;
    const seq = ++scFavSeq;
    api.fetchApi(`/${NS}/scenes/favorites`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        if (seq !== scFavSeq) return;     // 期间 toggle 过 → 这份快照已过期
        ui.sceneFav = new Set(d.favorites || []);
        ui.sceneFavLoading = false;
        // ⚠️ v5.1.0："pose" 也必须在这里重绘 —— 少了它，切到姿势库后数据回来了
        //    界面却永远停在「正在加载姿势库…」（前端冒烟抓到的真 bug）。
        if (ui.tab === "scenes" || ui.tab === "art" || ui.tab === "pose"
            || ui.tab === "weapon") {
          renderScenes();
        } else if (ui.tab === "near") renderNear();
      })
      .catch((e) => {
        // 拉不到收藏不该挡住浏览 —— 降级成空集合，点 ☆ 时再报错
        ui.sceneFav = new Set();
        ui.sceneFavLoading = false;
        console.error("[AnimaV6] 场景收藏加载失败", e);
      });
  }

  async function toggleSceneFav(sc, on) {
    try {
      const r = await api.fetchApi(`/${NS}/scenes/favorites/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: sc.id, on }),
      });
      const d = await r.json();
      if (d.error) {
        setStatus("场景收藏失败：" + d.error, "no");
        return false;
      }
      ui.sceneFav = new Set(d.favorites || []);
      scFavSeq += 1;        // 作废在途的 GET（它会覆盖成旧快照）
      setStatus(`${on ? "已收藏" : "已取消收藏"}「${sc.zh}」`
        + `（共 ${ui.sceneFav.size} 条）`, "ok");
      return true;
    } catch (e) {
      setStatus("场景收藏失败：" + e.message, "no");
      return false;
    }
  }

  /** 重绘 + 保持滚动位置与输入焦点 —— 搜索框每次按键都走这里。 */
  function repaintScenePage(which, focusId) {
    const top = left.scrollTop;
    if (which === "near") renderNear(); else renderScenes();
    left.scrollTop = top;
    if (!focusId) return;
    const el = left.querySelector("#" + focusId);
    if (!el || !el.focus) return;
    el.focus();
    const n = String(el.value || "").length;
    try { el.setSelectionRange(n, n); } catch (e) { /* 非文本框不支持，忽略 */ }
  }

  /** 滚到底自动续 60 条。只挂一次，避免每次重绘叠一个监听。 */
  let sceneScrollHooked = false;
  function hookSceneScroll() {
    if (sceneScrollHooked) return;
    sceneScrollHooked = true;
    left.addEventListener("scroll", () => {
      if (!ui._sceneMore) return;
      if (left.scrollTop + left.clientHeight < left.scrollHeight - 160) return;
      ui.sceneLimit += SCENE_PAGE;
      repaintScenePage(ui.tab === "near" ? "near" : "scenes");
    });
  }

  /** 搜索框 + ⭐只看收藏 + 紧凑切换（+ 清空筛选）。两页共用，id 前缀区分。 */
  function sceneSearchBar(nShown, prefix, which) {
    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    bar.id = prefix + "-bar";
    const q = document.createElement("input");
    q.type = "text";
    q.id = prefix + "-q";
    q.placeholder = "搜索：中文名 / 题材 / 描述 / 固定标签 / id（空格分隔多个词，须全部命中）";
    q.value = ui.sceneQuery;
    // ⚠️ v4.5.40：中文输入法（IME）的修复 —— 用户 2026-10-31 实测「打中文会自动变成字母」。
    //    根因不是按键被拦，而是**每次按键都重建输入框节点**：
    //    `renderScenes()` 第一行就是 `left.innerHTML = ""`，搜索框跟着整列一起重画。
    //    输入法的拼音还在组合串里没上屏，节点已经被销毁 → 浏览器把原始拼音
    //    （`JiaoShi` 这种）直接提交，于是框里全是字母、中文永远打不进去。
    //    修法：compositionstart → compositionend 之间**一次重绘都不做**，
    //    上屏后再按最终的中文过滤。（与角色页 `renderGrid()` 只重画网格同一个道理，
    //    那一页从来没这个毛病，正说明问题出在"重建输入框"而不是输入法。）
    // ⚠️ 用 addEventListener 而不是 `q.oncompositionstart = ...`：
    //    jsdom（前端冒烟跑在它上面）没实现这两个 IDL 属性，赋值等于没挂，
    //    断言会呈现"修了还是坏的"假象。
    q.addEventListener("compositionstart", () => { q._av3composing = true; });
    q.addEventListener("compositionend", () => {
      q._av3composing = false;
      ui.sceneQuery = q.value;
      ui.sceneLimit = SCENE_PAGE;
      repaintScenePage(which, prefix + "-q");
    });
    q.oninput = (e) => {
      // 组合中（Chrome 会给 isComposing=true）：一个字都别动，等 compositionend
      if (q._av3composing || (e && e.isComposing)) return;
      ui.sceneQuery = q.value;
      ui.sceneLimit = SCENE_PAGE;
      repaintScenePage(which, prefix + "-q");
    };
    bar.appendChild(q);

    const favN = (ui.sceneFav || new Set()).size;
    const fb = document.createElement("button");
    fb.className = "av3-btn" + (ui.sceneFavOnly ? " pri" : "");
    fb.id = prefix + "-favonly";
    fb.textContent = `⭐ 只看收藏 ${favN}`;
    fb.title = ui.sceneFavOnly ? "点一下回到全部" : "只显示已收藏的场景";
    fb.disabled = !favN && !ui.sceneFavOnly;
    fb.onclick = () => {
      ui.sceneFavOnly = !ui.sceneFavOnly;
      ui.sceneLimit = SCENE_PAGE;
      repaintScenePage(which);
    };
    bar.appendChild(fb);

    const cb = document.createElement("button");
    cb.className = "av3-btn" + (ui.sceneCompact ? " pri" : "");
    cb.id = prefix + "-compact";
    cb.textContent = ui.sceneCompact ? "▦ 紧凑" : "▤ 卡片";
    cb.title = ui.sceneCompact
      ? "切回卡片模式（间距放宽）"
      : "紧凑模式：压掉间距与字号，一屏能扫更多条";
    cb.onclick = () => {
      ui.sceneCompact = !ui.sceneCompact;
      repaintScenePage(which);
    };
    bar.appendChild(cb);

    /* v5.0.1（用户 2026-11-01）：「增加竖屏和通用比例、横屏和通用比例筛选功能，
       目的是快速筛选对应比例。」
       三段互斥（全部 / 竖屏·通用 / 横屏·通用），与搜索、收藏、档位分组 AND 叠加。
       · 「竖屏·通用」= aspect 不是 landscape 的（portrait + both）
       · 「横屏·通用」= aspect 不是 portrait 的（landscape + both）
       ⚠️ 只在场景页出（`which === "scenes"`）：特写页 60 条几乎全是"通"，
          放一排按钮只会占地方、点了也没变化。 */
    if (which === "scenes") {
      const aspWrap = document.createElement("span");
      aspWrap.id = prefix + "-aspect";
      aspWrap.style.cssText = "display:inline-flex;border:1px solid #3a3a3a;"
        + "border-radius:6px;overflow:hidden;margin-left:2px";
      const ASP = [
        ["all", "全部", "不按比例筛"],
        ["p", "竖屏·通用", "竖屏推荐 + 比例通用的场景（排除横屏推荐）"],
        ["l", "横屏·通用", "横屏推荐 + 比例通用的场景（排除竖屏推荐）"],
      ];
      for (const [k, label, tip] of ASP) {
        const b = document.createElement("button");
        const on = (ui.sceneAspect || "all") === k;
        b.className = "av3-btn" + (on ? " pri" : "");
        b.id = `${prefix}-aspect-${k}`;
        b.textContent = label;
        b.title = tip;
        b.style.cssText = "border:0;border-radius:0;"
          + (on ? "" : "background:transparent");
        b.onclick = () => {
          ui.sceneAspect = k;
          ui.sceneLimit = SCENE_PAGE;
          repaintScenePage(which);
        };
        aspWrap.appendChild(b);
      }
      bar.appendChild(aspWrap);
    }

    if (ui.sceneQuery || ui.sceneTheme || ui.sceneFavOnly || ui.sceneBg
        || (ui.sceneAspect && ui.sceneAspect !== "all")) {
      const clr = document.createElement("button");
      clr.className = "av3-btn";
      clr.id = prefix + "-clear";
      clr.textContent = "清空筛选";
      clr.onclick = () => {
        ui.sceneQuery = "";
        ui.sceneTheme = "";
        ui.sceneBg = "";            // v5.0.20：背景分类一并复位
        ui.sceneFavOnly = false;
        ui.sceneAspect = "all";     // v5.0.1：比例筛选一并复位
        ui.sceneLimit = SCENE_PAGE;
        repaintScenePage(which);
      };
      bar.appendChild(clr);
    }
    const n = document.createElement("span");
    n.className = "av3-meta";
    n.id = prefix + "-hits";
    n.textContent = `命中 ${nShown} 条`;
    bar.appendChild(n);
    return bar;
  }

  /** 题材快捷筛选条（两级：整条收起 / 条内 18↔全部）。
   *
   *  ⚠️ 两级都叫"收起"会看糊涂，所以文案分开：
   *     · 整条 —— 「▾ 🏷 题材 62」/「▸ 🏷 题材 62」
   *     · 条内 —— 「＋ 更多题材（62）」/「− 只显示常用的」
   *
   *  v3.44.0：整条可收起。收起后**当前选中的题材仍显示在摘要里**
   *  —— 否则会出现"列表被筛掉一半却看不出原因"，还会顺手给一个 ✕ 直接取消，
   *  不必为了取消一个题材先把它展开。
   */
  function sceneThemeBar(list, which) {
    const all = sceneThemes(list);
    if (all.length < 2) return null;
    const CAP = 18;
    const cur = ui.sceneTheme || "";
    let capped = ui.sceneThemeAll ? all.slice() : all.slice(0, CAP);
    // 选中的题材如果被截断在 18 名之外，补进来 —— 否则筛选生效了却看不到高亮
    if (cur && !capped.some((t) => t.name === cur)) {
      const hit = all.find((t) => t.name === cur);
      if (hit) capped = capped.concat([hit]);
    }
    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    bar.id = "av3-scene-themes";

    const tog = document.createElement("button");
    tog.className = "av3-btn" + (cur ? " pri" : "");
    tog.id = "av3-scene-themetoggle";
    tog.textContent = `${ui.sceneThemeOpen ? "▾" : "▸"} 🏷 题材 ${all.length}`;
    tog.title = ui.sceneThemeOpen
      ? "收起整条题材筛选（收起后当前题材仍显示）"
      : "展开题材筛选条";
    tog.onclick = () => {
      ui.sceneThemeOpen = !ui.sceneThemeOpen;
      repaintScenePage(which);
    };
    bar.appendChild(tog);

    if (!ui.sceneThemeOpen) {
      const s = document.createElement("span");
      s.className = "av3-meta";
      s.id = "av3-scene-themesum";
      s.textContent = cur ? `当前：${cur}` : "未按题材过滤";
      bar.appendChild(s);
      if (cur) {
        const x = document.createElement("button");
        x.className = "av3-btn";
        x.id = "av3-scene-themeclear";
        x.textContent = "✕ 取消题材";
        x.title = "清掉题材筛选（不用先展开）";
        x.onclick = () => {
          ui.sceneTheme = "";
          ui.sceneLimit = SCENE_PAGE;
          repaintScenePage(which);
        };
        bar.appendChild(x);
      }
      return bar;
    }

    const mk = (label, val) => {
      const b = document.createElement("button");
      b.className = "av3-btn" + (cur === val ? " pri" : "");
      b.textContent = label;
      if (val) b.dataset.theme = val;
      b.onclick = () => {
        ui.sceneTheme = (ui.sceneTheme === val) ? "" : val;   // 再点一次取消
        ui.sceneLimit = SCENE_PAGE;
        repaintScenePage(which);
      };
      bar.appendChild(b);
    };
    mk(`全部题材 ${list.length}`, "");
    for (const t of capped) mk(`${t.name} ${t.count}`, t.name);
    if (all.length > CAP) {
      const more = document.createElement("button");
      more.className = "av3-btn";
      more.id = "av3-scene-thememore";
      more.textContent = ui.sceneThemeAll
        ? "− 只显示常用的" : `＋ 更多题材（${all.length}）`;
      more.title = ui.sceneThemeAll
        ? `收回前 ${CAP} 个常用题材（整条收起请点「🏷 题材」）`
        : `展开全部 ${all.length} 个题材`;
      more.onclick = () => {
        ui.sceneThemeAll = !ui.sceneThemeAll;
        repaintScenePage(which);
      };
      bar.appendChild(more);
    }
    return bar;
  }

  /* ── 👗 服装库（v5.1.0，用户 2026-11-05）──────────────────────────
     「增加服装库，预设分类好各种丰富的整套服装搭配，UI 与角色库、场景搭配同级；
       选择后原理与角色服装预设一致，可以直接套用。」

     数据：presets/outfit_library.json（tools/build_outfit_library.py 生成，
           9 类 × 10 套）。语义是**全局一套**：点卡片即启用（单选），再点取消。
     优先级：角色自己启用了服装预设 → 该角色用角色预设；否则用服装库这一套；
           两个都没有 → 服装走随机。判据在后端 `effective_outfit()`。

     ⚠️ 不新增节点控件：与角色服装预设同一条纪律 —— 状态放服务端，
        随插件走而不是随工作流走，也就没有"旧工作流要迁移"这件事。
  */
  function loadOutfitLibrary(force) {
    if (ui.outfitLib && !force) return;
    if (ui.outfitLibLoading) return;
    ui.outfitLibLoading = true;
    api.fetchApi(`/${NS}/outfit-library`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        ui.outfitLibLoading = false;
        if (d && d.error) throw new Error(d.error);
        ui.outfitLib = d;
        if (ui.tab === "outfits") renderOutfitLibrary();
        refreshApplied();          // v6.1.0：右栏「已套用」里的服装库那行要跟着出来
      })
      .catch((e) => {
        ui.outfitLibLoading = false;
        console.error("[AnimaV6] 服装库加载失败", e);
        if (ui.tab === "outfits") {
          left.innerHTML = `<div class="av3-empty">服装库加载失败：${e.message}<br>
            确认 <code>presets/outfit_library.json</code> 存在，
            必要时跑 <code>python tools/build_outfit_library.py</code> 重建。</div>`;
        }
      });
  }

  function outfitLibEnable(id) {
    return api.fetchApi(`/${NS}/outfit-library/enable`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id || "" }),
    }).then((r) => r.json()).then((d) => {
      if (d && d.library) ui.outfitLib = d.library;
      refreshApplied();          // v6.1.0：启用/停用后右栏「已套用」同步
      return d;
    });
  }

  function renderOutfitLibrary() {
    left.classList.remove("charmode");
    const d = ui.outfitLib;
    if (!d) {
      left.innerHTML = `<div class="av3-empty">正在加载服装库…</div>`;
      loadOutfitLibrary();
      return;
    }
    const items = d.outfits || [];
    if (!items.length) {
      left.innerHTML = `<div class="av3-empty">服装库是空的。<br>
        跑 <code>python tools/build_outfit_library.py</code> 重建。</div>`;
      return;
    }
    const LAB = {};
    for (const sec of pools.sections) {
      for (const f of (sec.fields || [])) LAB[f.key] = f.lab || f.key;
    }
    const cats = d.categories || [];
    const active = String(d.active || "");
    const activeItem = items.find((o) => o.id === active) || null;

    left.innerHTML = "";

    /* 1) 启用态：一句话说清"现在穿什么"，并给一个显式的关闭入口。
          ⚠️ 必须能关 —— 不然用户想回到随机服装时无处可去。 */
    const st = document.createElement("div");
    st.className = "av3-charbar";
    st.id = "av3-outfitlib-state";
    const badge = document.createElement("span");
    badge.className = "av3-meta";
    badge.textContent = activeItem
      ? `👗 当前启用：${activeItem.zh}（${(cats.find((c) => c.id === activeItem.cat) || {}).zh || activeItem.cat}）`
      : "👗 当前未启用 —— 服装走随机；角色启用了自己的预设时按角色预设";
    if (activeItem) badge.style.color = "#e8c98a";
    st.appendChild(badge);
    if (activeItem) {
      const off = document.createElement("button");
      off.className = "av3-btn";
      off.id = "av3-outfitlib-off";
      off.textContent = "关闭（回到随机）";
      off.onclick = async () => {
        off.disabled = true;
        const r = await outfitLibEnable("");
        off.disabled = false;
        if (r && r.error) setStatus("关闭失败：" + r.error, "no");
        else setStatus("👗 服装库已关闭：服装回到随机", "ok");
        renderOutfitLibrary();
      };
      st.appendChild(off);
    }
    left.appendChild(st);

    /* v5.5.0（用户 2026-11-05 21:27）：袜子 / 鞋子覆盖栏。
       「可以指定选择丝袜与鞋子款式以及是否穿丝袜与鞋子，两个可以分别指定也可
         不启用（不启用就跟随服装库搭配的服装）。」
       「不穿」不是特殊值 —— 写词库里的 `barefoot` / `bare feet`。 */
    left.appendChild(outfitOverrideBar(d));

    /* 2) 分类条（9 类 + 全部），计数按整库算 */
    const cbar = document.createElement("div");
    cbar.className = "av3-charbar";
    cbar.id = "av3-outfitlib-cats";
    const mkCat = (cid, label, n) => {
      const b = document.createElement("button");
      b.className = "av3-btn" + (ui.outfitCat === cid ? " pri" : "");
      b.dataset.cat = cid;
      b.textContent = `${label} ${n}`;
      b.onclick = () => {
        ui.outfitCat = cid;
        renderOutfitLibrary();
      };
      cbar.appendChild(b);
    };
    mkCat("", "全部", items.length);
    /* v6.4.0（用户 2026-11-07）：「服装库分类筛选增加展开按钮，交互后可以展开与收起」。
       11 个分类平铺要占两行、把下面的卡片顶下去，所以默认收起：
         · 收起时只留「全部」+（有选中时）当前那一类 + 展开按钮；
         · 展开后列出全部类，按钮变「▾ 收起分类」。
       状态存 ui.outfitCatOpen 并随 rememberUi 记忆，重开面板保持上次的样子。 */
    const curCat = ui.outfitCat || "";
    if (ui.outfitCatOpen) {
      for (const c of cats) mkCat(c.id, c.zh || c.id, (d.counts || {})[c.id] || 0);
    } else if (curCat) {
      // 收起时必须保留当前选中项 —— 否则用户看不出自己正在筛什么
      const c = cats.find((x) => x.id === curCat);
      if (c) mkCat(c.id, c.zh || c.id, (d.counts || {})[c.id] || 0);
    }
    const catToggle = document.createElement("button");
    catToggle.className = "av3-btn";
    catToggle.id = "av3-outfitlib-cats-toggle";
    catToggle.textContent = ui.outfitCatOpen
      ? "▾ 收起分类" : `▸ 展开分类（${cats.length}）`;
    catToggle.title = ui.outfitCatOpen
      ? "收起分类，只留「全部」与当前选中的分类"
      : "平铺显示全部 " + cats.length + " 个分类";
    catToggle.onclick = () => {
      ui.outfitCatOpen = !ui.outfitCatOpen;
      renderOutfitLibrary();
    };
    cbar.appendChild(catToggle);
    left.appendChild(cbar);

    /* 3) 搜索（多词 AND，命中中文名 / tags / 字段值） */
    const sbar = document.createElement("div");
    sbar.className = "av3-charbar";
    sbar.id = "av3-outfitlib-bar";
    const q = document.createElement("input");
    q.type = "text";
    q.id = "av3-outfitlib-q";
    q.placeholder = "搜服装：白裙 / maid / 水手 / 婚纱 …（空格 = 全部命中）";
    q.value = ui.outfitQuery || "";
    const applyQ = () => {
      ui.outfitQuery = q.value;
      renderOutfitLibrary();
      const el = left.querySelector("#av3-outfitlib-q");
      if (el) { el.focus(); const n = el.value.length; try { el.setSelectionRange(n, n); } catch (e) {} }
    };
    q.oninput = applyQ;
    sbar.appendChild(q);
    if (ui.outfitQuery) {
      const clr = document.createElement("button");
      clr.className = "av3-btn";
      clr.textContent = "清空";
      clr.onclick = () => { ui.outfitQuery = ""; renderOutfitLibrary(); };
      sbar.appendChild(clr);
    }
    left.appendChild(sbar);

    /* 4) 卡片列表 */
    const toks = String(ui.outfitQuery || "").toLowerCase()
      .split(/\s+/).filter(Boolean);
    const shown = items.filter((o) => {
      if (ui.outfitCat && o.cat !== ui.outfitCat) return false;
      if (!toks.length) return true;
      const hay = [o.zh, o.cat, String(o.note || "")]
        .concat(o.tags || [])
        .concat(Object.values(o.fields || {}))
        .join(" ").toLowerCase();
      return toks.every((t) => hay.indexOf(t) >= 0);
    });
    const note = document.createElement("div");
    note.className = "av3-meta";
    note.id = "av3-outfitlib-note";
    note.style.margin = "0 0 8px";
    note.innerHTML = `共 <b>${shown.length}</b> 套 · 点卡片即<b>启用</b>（再点取消）；`
      + `▶ 看这套锁了哪些词。档位不够的套装不会生效。`;
    left.appendChild(note);

    const list = document.createElement("div");
    list.id = "av3-outfitlib-list";
    for (const o of shown) list.appendChild(outfitLibCard(o, o.id === active, LAB, cats));
    left.appendChild(list);
  }

  /* 服装库的「覆盖项」栏（v5.5.0）：只换袜子 / 鞋子，其余仍用那套服装。
     ⚠️ 三个细节：
       ① 「不穿」用词库真实选项 `barefoot` / `bare feet` —— 只闭麦不写词的话，
          模型在无提示时默认会给角色补一双鞋；
       ② 选项文案带英文值（`黑色过膝袜 · black thighhighs`），因为出图是按英文值走的，
          只看中文分不清 `pantyhose` 与 `black pantyhose`；
       ③ 变更后整栏重绘（`renderOutfitLibrary`），因为启用态那一行也要跟着刷新。 */
  function outfitOverrideBar(d) {
    const ov = d.overrides || {};
    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    bar.id = "av3-outfitlib-ov";

    const fieldOf = (k) => {
      for (const sec of pools.sections) {
        for (const f of (sec.fields || [])) if (f.key === k) return f;
      }
      return null;
    };

    const mk = (field, label, bareVal, bareZh) => {
      const wrap = document.createElement("label");
      wrap.style.cssText = "display:inline-flex;align-items:center;gap:4px;margin-right:10px";
      const sp = document.createElement("span");
      sp.className = "av3-meta";
      sp.textContent = label;
      const sel = document.createElement("select");
      sel.id = "av3-ov-" + field;
      sel.style.cssText = "font-size:12px;padding:3px 5px;background:#26262e;color:#ddd;"
        + "border:1px solid #555;border-radius:6px;max-width:210px";
      const o0 = document.createElement("option");
      o0.value = "";
      o0.textContent = "跟随服装库";
      sel.appendChild(o0);
      // ⚠️ 前端拿到的选项是**对象** `{zh, en, level}`（`pools_payload()` 转过），
      //    不是池子源文件里的 `[中文, 英文]` 二元数组 —— 用下标会拿到 undefined。
      for (const opt of ((fieldOf(field) || {}).opts || [])) {
        const o = document.createElement("option");
        o.value = opt.en;
        o.textContent = (opt.en === bareVal) ? bareZh : opt.zh + " · " + opt.en;
        sel.appendChild(o);
      }
      const cur = String(ov[field] || "");
      sel.value = cur;
      if (cur === bareVal) sel.style.color = "#e8c98a";
      sel.onchange = async () => {
        sel.disabled = true;
        let r = {};
        try {
          r = await api.fetchApi(`/${NS}/outfit-library/override`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ field: field, value: sel.value }),
          }).then((x) => x.json());
        } catch (e) {
          r = { error: e.message };
        }
        sel.disabled = false;
        if (r && r.error) { setStatus(label + " 覆盖失败：" + r.error, "no"); return; }
        if (r && r.library) ui.outfitLib = r.library;
        setStatus(sel.value
          ? `已指定 ${label}：${sel.options[sel.selectedIndex].textContent}（覆盖服装库那套）`
          : `${label} 已回到「跟随服装库」`, "ok");
        renderOutfitLibrary();
      };
      wrap.appendChild(sp);
      wrap.appendChild(sel);
      return wrap;
    };

    /* v5.9.0：「覆盖场景搭配里的推荐服装」总开关（用户 2026-11-06）。
       关闭（默认）= 场景带具体衣物字段时服装库整套让路，按场景推荐服装走；
       开启 = 服装库固定覆盖场景服装（即 v5.8.0 及以前的行为）。
       ⚠️ 同一个设置只留这一个入口；checkbox 本身＋旁边一行摘要回显当前值，
          变更后整栏重绘，值从后端返回的 library 里读回来，不会被重绘丢掉。 */
    const sceneChk = document.createElement("input");
    sceneChk.type = "checkbox";
    sceneChk.id = "av3-outfitlib-override-scene";
    sceneChk.checked = !!d.scene_override;
    sceneChk.style.cssText = "vertical-align:middle;margin:0 4px 0 0";
    sceneChk.title = "开启后服装库选的整套服装会覆盖场景搭配里写死的推荐服装；关闭时场景有推荐服装就按场景走";
    const sceneLbl = document.createElement("label");
    sceneLbl.className = "av3-meta";
    sceneLbl.style.cssText = "display:inline-flex;align-items:center;margin-right:6px;cursor:pointer";
    sceneLbl.appendChild(sceneChk);
    sceneLbl.appendChild(document.createTextNode("👗 覆盖场景推荐服装"));
    bar.appendChild(sceneLbl);

    const sceneState = document.createElement("span");
    sceneState.className = "av3-meta";
    sceneState.id = "av3-outfitlib-override-scene-state";
    sceneState.textContent = sceneChk.checked
      ? "开启 · 服装库固定覆盖场景服装"
      : "关闭 · 场景有推荐服装时按场景走";
    bar.appendChild(sceneState);

    const sep = document.createElement("span");
    sep.className = "av3-meta";
    sep.textContent = " ｜ ";
    bar.appendChild(sep);

    sceneChk.onchange = async () => {
      sceneChk.disabled = true;
      let r = {};
      try {
        r = await api.fetchApi(`/${NS}/outfit-library/scene-override`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: sceneChk.checked }),
        }).then((x) => x.json());
      } catch (e) {
        r = { error: e.message };
      }
      sceneChk.disabled = false;
      if (r && r.error) { setStatus("覆盖场景服装开关失败：" + r.error, "no"); return; }
      if (r && r.library) ui.outfitLib = r.library;
      setStatus(sceneChk.checked
        ? "已开启：服装库固定覆盖场景推荐服装"
        : "已关闭：场景有推荐服装时按场景搭配走", "ok");
      renderOutfitLibrary();
    };

    bar.appendChild(mk("socks", "🦵 丝袜", "barefoot", "不穿 · 裸足"));
    bar.appendChild(mk("shoes", "👠 鞋子", "bare feet", "不穿 · 赤足"));

    const note = document.createElement("span");
    note.className = "av3-meta";
    note.textContent = "只覆盖这两件，其余仍用服装库那套（没启用服装库时不生效）";
    bar.appendChild(note);
    return bar;
  }

  function outfitLibCard(o, on, LAB, cats) {
    const open = !!ui.outfitOpen[o.id];
    const box = document.createElement("div");
    box.className = "av3-sec" + (on ? " on" : "") + (open ? " open" : "");
    box.dataset.outfit = o.id;

    const hd = document.createElement("div");
    hd.className = "hd";
    hd.title = on ? "点一下 = 停用这套（服装回到随机）" : "点一下 = 启用这套（全局生效）";
    const arw = document.createElement("span");
    arw.className = "arw";
    arw.textContent = open ? "▼" : "▶";
    arw.title = "展开 / 收起明细";
    arw.onclick = (ev) => {
      ev.stopPropagation();
      ui.outfitOpen[o.id] = !ui.outfitOpen[o.id];
      renderOutfitLibrary();
    };
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = o.zh;
    const th = document.createElement("span");
    th.className = "th";
    th.textContent = `【${(cats.find((c) => c.id === o.cat) || {}).zh || o.cat}】`;
    const lv = document.createElement("span");
    lv.className = "av3-meta";
    lv.textContent = o.level > 0 ? `L${o.level}` : "全档";
    lv.title = "这套服装的准入档位（超过就抽不到；档位不够时不生效）";
    const nf = document.createElement("span");
    nf.className = "av3-meta";
    nf.textContent = `${(o.tags || []).length} 词`;
    hd.appendChild(arw); hd.appendChild(t); hd.appendChild(th);
    hd.appendChild(lv); hd.appendChild(nf);
    if (on) {
      const ok = document.createElement("span");
      ok.className = "av3-meta";
      ok.textContent = "✅ 已启用";
      ok.style.color = "#e8c98a";
      hd.appendChild(ok);
    }
    hd.onclick = async () => {
      const want = on ? "" : o.id;
      const r = await outfitLibEnable(want);
      if (r && r.error) { setStatus("切换失败：" + r.error, "no"); return; }
      setStatus(want ? `👗 已启用「${o.zh}」` : `👗 已停用「${o.zh}」（服装回到随机）`, "ok");
      // ⚠️ v5.3.1（用户 2026-11-05 19:23「我需要的是点击展开才展开」）：
      //    这里以前顺手把卡片展开了 —— 选择与展开是两件事，别替用户做主。
      renderOutfitLibrary();
    };
    box.appendChild(hd);

    const bd = document.createElement("div");
    bd.className = "bd";
    if (o.note) {
      const de = document.createElement("div");
      de.className = "av3-meta";
      de.textContent = o.note;
      de.style.marginBottom = "5px";
      bd.appendChild(de);
    }
    const rows = document.createElement("div");
    rows.style.cssText = "display:flex;flex-wrap:wrap;gap:4px";
    for (const tg of (o.tags || [])) {
      const chip = document.createElement("span");
      chip.className = "av3-chip";
      chip.textContent = tg;
      rows.appendChild(chip);
    }
    for (const [k, v] of Object.entries(o.fields || {})) {
      const chip = document.createElement("span");
      chip.className = "av3-chip";
      chip.textContent = `${LAB[k] || k}：${v}`;
      chip.title = `${k} = ${v}（词库字段，写入时会被锁定）`;
      rows.appendChild(chip);
    }
    const dchip = document.createElement("span");
    dchip.className = "av3-chip";
    dchip.textContent = `闭麦 ${(o.drop || []).length} 个服装字段`;
    dchip.title = (o.drop || []).join(", ");
    rows.appendChild(dchip);
    bd.appendChild(rows);
    box.appendChild(bd);
    return box;
  }

  function renderScenes() {
    left.classList.remove("charmode");   // v6.1.0：角色库以外的页签恢复整列滚动
    const d = ui.scenes;
    if (!d) {
      left.innerHTML = `<div class="av3-empty">正在加载场景搭配库…</div>`;
      loadScenes();
      loadSceneFavs();
      return;
    }
    if (!d.available || !(d.scenes || []).length) {
      left.innerHTML = `<div class="av3-empty">场景搭配库是空的。<br>
        确认 <code>presets/anima_v4_scenes.json</code> 存在，
        必要时跑 <code>python tools/build_scenes.py</code> 重建。</div>`;
      return;
    }
    if (!ui.sceneFav && !ui.sceneFavLoading) loadSceneFavs();

    left.innerHTML = "";
    const applied = appliedSceneId();
    const favSet = ui.sceneFav || new Set();

    // v3.18.0：特写搭配（manual_only）在「🔍 特写搭配」页单独展示，
    // 这一页只列原来的场景搭配 —— 两个库不混排。
    // v5.0.21：艺术构图（manual_only）也已升为**独立页签**，本页不再收录它 ——
    //          原来靠 `|| s.group === "art"` 把它捞回来，现在会与页签重复。
    //          注意 art 本身是 manual_only，所以 `!s.manual` 天然就把它排除了。
    /* v5.0.21：艺术构图是一级页签 —— 在这一页只取 group=art，
       分组条整条不渲染（页签本身就承担了分组职责，再摆一排是重复入口）。
       ⚠️ base0 必须**分页签**取：art 是 manual_only，若统一用 `!s.manual`，
          艺术构图页会被筛成空列表（实测 bug，bgbar 直接 null）。 */
    const onlyArt = (ui.tab === "art");
    /* v5.1.0：姿势库升为一级页签 —— 与「🎬 场景搭配」共用 /scenes，
       只是各看各的 group。场景搭配页从此**不再**收录 pose，
       姿势页则把 manual_only 的瑜伽 / 伸展也一并收进来（它们只在这里出现）。 */
    const onlyPose = (ui.tab === "pose");
    /* v5.10.0：武器场景升为一级页签 —— 与 art / pose 同一份 /scenes，
       只是各看各的 group。这一组全部 manual_only，场景搭配页的 `!s.manual`
       天然把它排除，随机池分布零漂移。 */
    const onlyWeapon = (ui.tab === "weapon");
    const allScenes = (d.scenes || []);
    const base0 = onlyArt
      ? allScenes.filter((s) => s.group === "art")
      : onlyWeapon
        ? allScenes.filter((s) => s.group === "weapon")
        : onlyPose
          ? allScenes.filter((s) => s.group === "pose")
          : allScenes.filter((s) => !s.manual && s.group !== "pose");
    const grp = onlyArt ? "art"
      : (onlyWeapon ? "weapon" : (onlyPose ? "pose" : ui.sceneGroup));
    let base = grp === "all" ? base0
      : base0.filter((s) => s.group === grp);
    // v5.1.0：姿势库内部再分一层（瑜伽 / 伸展 / 基础姿势），按 theme 前缀判
    if (onlyPose && ui.poseCat && ui.poseCat !== "all") {
      base = base.filter((s) => {
        const th = String(s.theme || "");
        if (ui.poseCat === "yoga") return th.indexOf("瑜伽") >= 0;
        if (ui.poseCat === "stretch") return th.indexOf("伸展") >= 0;
        return th.indexOf("瑜伽") < 0 && th.indexOf("伸展") < 0;
      });
    }
    const toks = sceneTokens(ui.sceneQuery);
    // v5.10.0：武器页不认场景页的题材筛选（串过来会把列表筛空），
    // 它自己的分类走下面 weaponCat 那条。
    let shown = (onlyWeapon ? base : (ui.sceneTheme
      ? base.filter((s) => (s.theme || "") === ui.sceneTheme) : base));
    // v5.0.20：背景分类（bg 是独立维度，与 theme / 搜索 / 收藏 AND 叠加）
    // v5.0.21：只有艺术构图页才有这一维，别的页签不认这个状态
    if (onlyArt && ui.sceneBg) shown = shown.filter((s) => (s.bg || "") === ui.sceneBg);
    // v5.11.0：武器场景两级筛选 —— ① 作品 ② 该作品下的武器
    if (onlyWeapon && ui.weaponWork && ui.weaponWork !== "all") {
      shown = shown.filter((s) => (s.work || "general") === ui.weaponWork);
    }
    if (onlyWeapon && ui.weaponCat && ui.weaponCat !== "all") {
      shown = shown.filter((s) => weaponCatKey(s) === ui.weaponCat);
    }
    if (toks.length) shown = shown.filter((s) => sceneHit(s, toks));
    if (ui.sceneFavOnly) shown = shown.filter((s) => favSet.has(s.id));
    /* v5.0.1：比例筛选。「竖屏·通用」= 排除横屏推荐；「横屏·通用」= 排除竖屏推荐。
       没标注的场景在后端已回退成 both，两侧都会留下 —— 不会因为缺数据而消失。 */
    if (ui.sceneAspect === "p") {
      shown = shown.filter((s) => s.aspect !== "landscape");
    } else if (ui.sceneAspect === "l") {
      shown = shown.filter((s) => s.aspect !== "portrait");
    }
    // 有筛选时，空档位不再占一行「这一档暂无预设」—— 那会把结果挤到屏幕外
    const filtering = !!(toks.length || ui.sceneTheme || ui.sceneFavOnly
      || (onlyArt && ui.sceneBg)
      || grp !== "all"
      || (ui.sceneAspect && ui.sceneAspect !== "all"));

    /* ── 🎬 随机场景（scene_pick，v5.0.1）─────────────────────────────
       用户 2026-11-01：「取消掉抽到场景搭配的 60% 概率规则；UI 增加场景搭配
       随机抽取功能，勾选后可以在场景搭配内根据选择的 NSFW 等级随机抽取对应
       等级场景。」所以这个开关就是**唯一**入口：
         · 勾上 → 本次必出，且只从所选档位的场景里抽（引擎里是锁档 L_n）；
         · 不勾 → 一条都不出（含 SFW 档），要场景就在下面显式套用。
       ⚠️ 状态存节点控件而不是 localStorage —— 预演（面板）与实跑（工作流）
          必须同参，否则又回到"面板抽的和实跑的不一样"的老坑。 */
    const pickBox = document.createElement("label");
    pickBox.id = "av3-scene-pick";
    pickBox.style.cssText = "display:flex;align-items:center;gap:7px;cursor:pointer;"
      + "margin:0 0 7px;padding:5px 9px;border:1px solid #3a3a3a;border-radius:7px;"
      + "background:rgba(255,255,255,.03);font-size:12px;color:inherit";
    const pcb = document.createElement("input");
    pcb.type = "checkbox";
    pcb.id = "av3-scene-pick-cb";
    pcb.checked = !!wget(node, "scene_pick", false);
    pcb.title = "勾上 = 本次出图从场景库按所选 NSFW 档位随机抽一条（锁档、必有）\n"
      + "不勾 = 完全不出自动场景（v5.0.1 起取消原来的 60% 概率抽签）";
    pcb.onchange = () => {
      wset(node, "scene_pick", pcb.checked);
      app.graph.setDirtyCanvas(true, true);
      setStatus(pcb.checked
        ? "🎬 随机场景已开启：本次按当前档位抽一条场景搭配"
        : "🎬 随机场景已关闭：本次不套用任何自动场景", "ok");
      repaintScenePage("scenes");
    };
    const pickTxt = document.createElement("span");
    pickTxt.textContent = "🎬 随机抽取场景搭配（按当前 NSFW 档位）";
    pickBox.appendChild(pcb);
    pickBox.appendChild(pickTxt);
    // 档位提示跟在后面：让"按哪个档位抽"当场可见，省得用户去猜
    const lvTag = document.createElement("span");
    lvTag.className = "av3-meta";
    lvTag.style.cssText = "margin-left:auto;font-size:11px;opacity:.75";
    lvTag.textContent = `当前 ${ui.level || "sfw"}`;
    pickBox.appendChild(lvTag);
    // v5.1.0：姿势页不显示「随机抽取场景搭配」开关 —— 姿势不进随机池，
    // 摆在那儿只会让人以为勾了就能随机出瑜伽。
    // v5.10.0：武器场景全是 manual_only，和姿势库一样不显示「随机抽取」开关
    // v6.6.1：**艺术构图同理**（103 条全 manual_only，`scene_options()` 里 0 条）。
    //    以前这里显示着一个永远抽不到 art 的开关，而且它是**节点级**的 ——
    //    用户在艺术页勾上，实跑抽的是普通场景，最容易误判成"插件坏了"。
    //    现在三组 manual_only 页签统一改成「勾选后随机」工具条。
    if (!onlyPose && !onlyWeapon && !onlyArt) {
      left.appendChild(pickBox);
    } else {
      /* ── v6.1.0：姿势库 / 武器场景的「勾选后随机」工具条 ────────────
         用户 2026-11-07：「姿势库与武器场景增加勾选后可随机抽取功能」。
         与上面那条全局 pickBox 的分工：
           · pickBox：整库按 NSFW 档位随机（抽到哪条不可控，且这两组不参与）；
           · 这条：用户先把范围缩到自己要的那几条，再随机。
         v6.6.0：工具条本体抽成 buildPickBar（三页共用），
         并加上「全选 / 反选」—— 作用域是当前筛选结果（`shown`）。
         v6.6.1：艺术构图（art）接入第四页。 */
      const pkey = onlyPose ? "pose" : onlyWeapon ? "weapon" : "art";
      left.appendChild(buildPickBar(
        pkey,
        onlyPose ? "🎯 勾选姿势后随机"
          : onlyWeapon ? "🎯 勾选武器后随机" : "🎯 勾选构图后随机",
        shown.map((s) => s.id)));
    }

    /* v4.5.41（用户 2026-10-31「简化场景搭配选择界面，方便选择与浏览」）：
       四行说明默认收起成一个「? 用法说明」按钮 —— 首屏先给列表，
       要看规则自己点开。 */
    const tipBtn = document.createElement("button");
    tipBtn.className = "av3-btn";
    tipBtn.id = "av3-scene-help";
    tipBtn.textContent = (ui.sceneTipOpen ? "▾" : "▸") + " 用法说明";
    tipBtn.title = "这一页怎么用 / 搜索语法 / 为什么预设不写人数与角色";
    tipBtn.style.cssText = "margin-bottom:6px;font-size:11.5px;padding:2px 9px";
    tipBtn.onclick = () => {
      ui.sceneTipOpen = !ui.sceneTipOpen;
      repaintScenePage("scenes");
    };
    left.appendChild(tipBtn);
    if (ui.sceneTipOpen) {
      const tip = document.createElement("div");
      tip.className = "av3-meta";
      tip.id = "av3-scene-tip";
      tip.innerHTML = "把 <b>姿势 / 场景 / 表情 / 动作</b> 的固定组合一次套用 ——"
        + "套用到的字段<b>不再参与随机</b>，也不会被标签数裁剪削掉。<br>"
        + "🔎 搜索支持多个词（空格分隔，须<b>全部命中</b>）；"
        + "⭐ 收藏<b>两页共用</b>、跨会话保留；🎬 一级分组按 <b>NSFW 档位</b>，"
        + "档位不够的搭配根本不会出现在候选池里。<br>"
        + "⚠️ 预设<b>不写人数与角色</b>：那两项仍由你点选；"
        + "🔍 按部位取景的<b>特写搭配</b>在「🔍 特写搭配」页签，"
        + "🎨 平面构图在「🎨 艺术构图」页签。"
        + "🧘 <b>姿势</b>（跪坐 / 瑜伽 / 伸展）已独立成「🧘 姿势库」页签，本页不再混排。"
        + "⚔️ 持械造型在「⚔️ 武器场景」页签。";
      tip.style.marginBottom = "8px";
      left.appendChild(tip);
    }
    left.appendChild(autoLevelBar());
    left.appendChild(sceneSearchBar(shown.length, "av3-scene", "scenes"));

    /* ── 姿势库的分类筛选条（v5.1.0）───────────────────────────────
       用户 2026-11-05：「场景搭配内的姿势场景 UI 改为与角色库、场景搭配同级
       方便选择。同时增加更多姿势，例如瑜伽的各种姿势。」
       于是姿势升为**一级页签** —— 页签本身就是分组，原来那条
       「全部 / 🧎 姿势场景」的切换条在场景搭配页已经没有必要（那里只剩 sfw+nsfw）。
       这里只服务于姿势库内部：瑜伽 / 伸展 / 基础姿势（按 theme 前缀判）。 */
    if (onlyPose) {
      const pbar = document.createElement("div");
      pbar.className = "av3-charbar";
      pbar.id = "av3-pose-cats";
      for (const [gid, label] of [["all", "全部"],
                                  ["yoga", "🧘 瑜伽"],
                                  ["stretch", "🤸 伸展"],
                                  ["basic", "🧎 基础姿势"]]) {
        const gb = document.createElement("button");
        gb.className = "av3-btn" + (ui.poseCat === gid ? " pri" : "");
        gb.dataset.cat = gid;
        gb.textContent = label;
        gb.onclick = () => {
          ui.poseCat = gid;
          ui.sceneLimit = SCENE_PAGE;
          repaintScenePage("scenes");
        };
        pbar.appendChild(gb);
      }
      const pnote = document.createElement("span");
      pnote.className = "av3-meta";
      pnote.textContent = "姿势库＝以人物体式为核心；纯姿势型不锁环境，可套用在任何地点。"
        + "瑜伽 / 伸展为新增条目，默认不进随机池（只手动套用）";
      pbar.appendChild(pnote);
      left.appendChild(pbar);
    }

    /* ── ⚔️ 武器场景两级分类（v5.11.0）─────────────────────────────
       用户：「都按照 EVA 武器等分类进行管理，其他作品也一样，
              总之要方便知道是使用的那个作品的什么武器。」
       ① 第一排作品（全部 / 通用 / 8 个 IP），② 第二排该作品的武器。
       作品与武器都从数据动态收集 —— 以后加作品不用改前端。
       ⚠️ 换作品必须清掉上一作品的武器筛选，否则会筛出空列表。 */
    if (onlyWeapon) {
      const worksBar = document.createElement("div");
      worksBar.className = "av3-charbar";
      worksBar.id = "av3-weapon-works";
      const workList = ["all"];
      const seenWorks = new Set();
      const workLabel = {};
      for (const s of base) {
        const w = String(s.work || "general");
        if (!seenWorks.has(w)) { seenWorks.add(w); workList.push(w); }
        workLabel[w] = s.work_zh || w;
      }
      for (const w of workList) {
        const wb = document.createElement("button");
        wb.className = "av3-btn" + (ui.weaponWork === w ? " pri" : "");
        wb.dataset.work = w;
        wb.textContent = w === "all" ? "全部作品" : (workLabel[w] || w);
        wb.onclick = () => {
          ui.weaponWork = w;
          ui.weaponCat = "all";
          ui.sceneLimit = SCENE_PAGE;
          repaintScenePage("scenes");
        };
        worksBar.appendChild(wb);
      }
      left.appendChild(worksBar);

      const wbar = document.createElement("div");
      wbar.className = "av3-charbar";
      wbar.id = "av3-weapon-cats";
      const pool = (ui.weaponWork === "all") ? base
        : base.filter((s) => (s.work || "general") === ui.weaponWork);
      const wCats = ["all"];
      const seenW = new Set();
      for (const s of pool) {
        const k = weaponCatKey(s);
        if (k && !seenW.has(k)) { seenW.add(k); wCats.push(k); }
      }
      for (const gid of wCats) {
        const gb = document.createElement("button");
        gb.className = "av3-btn" + (ui.weaponCat === gid ? " pri" : "");
        gb.dataset.cat = gid;
        gb.textContent = gid === "all" ? "全部" : gid;
        gb.onclick = () => {
          ui.weaponCat = gid;
          ui.sceneLimit = SCENE_PAGE;
          repaintScenePage("scenes");
        };
        wbar.appendChild(gb);
      }
      const wnote = document.createElement("span");
      wnote.className = "av3-meta";
      wnote.textContent = "卡片标题带《作品名》与武器名 · 全部只手动套用"
        + "（不进随机池）；竖/横徽标是推荐画幅";
      wbar.appendChild(wnote);
      left.appendChild(wbar);
    }

    /* ── 背景分类筛选条（v5.0.20）─────────────────────────────────
       五类：纯色棚拍 / 双色对角 / 花园俯拍 / 室内实景 / 黑底逆光。
       只在当前列表里**真有** bg 值时才出这一条 —— 姿势场景没有 bg，
       不该白占一行。计数同样按当前分组算，切换分组数字会跟着变。 */
    const bgCnt = {};
    for (const s of base) {
      const b = String(s.bg || "");
      if (b) bgCnt[b] = (bgCnt[b] || 0) + 1;
    }
    const bgKeys = ["纯色棚拍", "双色对角", "花园俯拍", "室内实景", "黑底逆光"]
      .filter((k) => bgCnt[k]);
    // v5.0.21：背景分类只属于艺术构图（只有 art 组有 bg 字段），
    // 在「场景搭配」页不渲染 —— 用户 2026-11-04 20:38 报的就是这条串页了。
    if (onlyArt && bgKeys.length) {
      const bbar = document.createElement("div");
      bbar.className = "av3-charbar";
      bbar.id = "av3-scene-bgs";
      const bgBtn = (val, label) => {
        const b = document.createElement("button");
        b.className = "av3-btn" + (ui.sceneBg === val ? " pri" : "");
        b.dataset.bg = val;
        b.textContent = label;
        b.onclick = () => {
          ui.sceneBg = (ui.sceneBg === val) ? "" : val;   // 再点一次取消
          ui.sceneLimit = SCENE_PAGE;
          repaintScenePage("scenes");
        };
        return b;
      };
      bbar.appendChild(bgBtn("", "🧱 全部背景"));
      for (const k of bgKeys) bbar.appendChild(bgBtn(k, k + " " + bgCnt[k]));
      left.appendChild(bbar);
    }

    const tbar = sceneThemeBar(base, "scenes");
    if (tbar) left.appendChild(tbar);

    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    const cur = (d.scenes || []).find((s) => s.id === applied);
    const st = document.createElement("span");
    st.className = "av3-meta";
    st.id = "av3-scene-cur";
    st.textContent = !cur ? "当前未套用场景"
      : cur.manual ? `当前套用：${cur.zh}（特写页 · L${cur.level}）`
      : `当前套用：${cur.zh}（L${cur.level}）`;
    bar.appendChild(st);
    if (cur) {
      const cb = document.createElement("button");
      cb.className = "av3-btn";
      cb.textContent = "取消套用";
      cb.onclick = () => clearScene();
      bar.appendChild(cb);
    }
    const sum = document.createElement("span");
    sum.className = "av3-meta";
    sum.id = "av3-scene-sum";
    // 计数按**本页实际列表**算，不读 payload 的全局 by_level ——
    // 那个数含特写搭配，直接显示会与这一页对不上。
    const bl = {};
    for (const s of shown) bl[String(s.level)] = (bl[String(s.level)] || 0) + 1;
    sum.textContent = "共 " + shown.length + " 条（"
      + [0, 1, 2, 3, 4].filter((n) => bl[String(n)])
        .map((n) => `L${n}:${bl[String(n)]}`).join(" · ") + "）";
    bar.appendChild(sum);
    const allOpen = shown.length > 0 && shown.every((sc) => ui.sceneOpen[sc.id]);
    const exp = document.createElement("button");
    exp.className = "av3-btn";
    exp.id = "av3-scene-expand";
    exp.textContent = allOpen ? "▾ 全部收起" : "▸ 全部展开";
    exp.title = allOpen ? "把所有场景卡的字段列表收起来" : "把所有场景卡的字段列表展开";
    exp.onclick = () => {
      const on = !allOpen;
      ui.sceneOpen = {};
      if (on) for (const sc of shown) ui.sceneOpen[sc.id] = true;
      repaintScenePage("scenes");
    };
    bar.appendChild(exp);
    left.appendChild(bar);

    if (!shown.length) {
      const e = document.createElement("div");
      e.className = "av3-empty";
      e.id = "av3-scene-empty";
      e.textContent = "没有匹配的场景。换个词，或点上面的「清空筛选」。";
      left.appendChild(e);
      ui._sceneMore = false;
      return;
    }

    /* ── 正文：⭐ 收藏置顶分区 → 各档位分组（懒加载 + 可折叠）──────
       ⚠️ 收藏的条目**只出现在置顶分区**，不再重复出现在档位分组里 ——
          同一条在一页上出现两次，套用状态会和滚动位置一起乱。 */
    let budget = ui.sceneLimit;
    let hidden = 0;
    // v4.5.41：卡片放进 3 列网格（用户「一行显示 3 条」）。每组一个网格容器，
    // 分组标题仍在网格外，行高与标题节奏不变。
    let grid = null;
    const pushCard = (sc) => {
      if (budget <= 0) { hidden += 1; return; }
      budget -= 1;
      if (!grid) {
        grid = document.createElement("div");
        /* v6.4.0：姿势库与武器场景的标题长（《作品名》+ 武器名），三列时被
           ellipsis 截成「瑜…」。这两个页签改成一行两列，列宽翻倍。
           其它页签（场景搭配 / 艺术构图 / 特写）保持三列不变。 */
        grid.className = "av3-scenegrid"
          + ((ui.tab === "pose" || ui.tab === "weapon") ? " two" : "");
        left.appendChild(grid);
      }
      grid.appendChild(sceneCard(sc, sc.id === applied));
    };

    /* v4.5.41：收藏区默认**只留一行标题**（用户「收藏的场景只在交互后展开」）。
       不展开时收藏卡根本不渲染 —— 既不在置顶区、也不在档位分组里
       （置顶区本来就是"收藏不重复出现"的唯一出口），所以首屏是干净的全量列表。
       两种例外：① 点「⭐ 只看收藏」强制展开；② 刚收藏了一条就自动展开
       （否则用户点了 ★ 却发现卡片凭空消失）。 */
    const favList = shown.filter((s) => favSet.has(s.id));
    if (favList.length) {
      const favOpen = !!ui.sceneFavOpen || !!ui.sceneFavOnly;
      const fh = document.createElement("div");
      fh.className = "av3-note av3-favhead" + (favOpen ? "" : " closed");
      fh.id = "av3-scene-favsec";
      fh.title = favOpen ? "点一下收起" : "点一下展开收藏的场景";
      fh.textContent = `⭐ 我的收藏（${favList.length}）`
        + (favOpen
          ? "　两页共用 · 再点 ★ 取消 · 不在下列档位分组里重复出现"
          : "　▶ 点一下展开（默认收起，不占浏览位置）");
      if (!ui.sceneFavOnly) {
        fh.style.cursor = "pointer";
        fh.onclick = () => {
          ui.sceneFavOpen = !favOpen;
          repaintScenePage("scenes");
        };
      }
      left.appendChild(fh);
      if (favOpen) for (const sc of favList) pushCard(sc);
    }
    grid = null;

    // ⚠️ 一级分组用 **档位**（level_groups），不是 sfw/nsfw —— 档位是派生数据，
    //    且与节点上的档位按钮同名，点着「过激」就能直接找到对应搭配。
    //    空组（该档暂时没有预设）在**没有筛选**时照样渲染，让人一眼看出
    //    "这一档还没内容"；一旦有筛选就跳过，否则 5 行空标题会把结果挤走。
    const rest = shown.filter((s) => !favSet.has(s.id));
    const lvNow = levelNum(ui.level);
    for (const g of (d.level_groups || [])) {
      const list = rest.filter((s) => s.level === g.num);
      if (!list.length && filtering) continue;
      const closed = !!ui.sceneClosed[String(g.num)];
      const h = document.createElement("div");
      h.className = "av3-note av3-lvhead" + (closed ? " closed" : "");
      h.style.cssText = "margin:10px 0 6px";
      h.dataset.level = String(g.num);
      h.id = "av3-scene-lv" + g.num;
      h.title = closed ? "点一下展开这一档" : "点一下收起这一档";
      h.textContent = `${closed ? "▶" : "▼"} ${g.title}（${list.length}）`
        + (g.desc ? "　" + g.desc : "")
        + (g.num === lvNow ? "　← 当前档位" : "");
      h.onclick = () => {
        ui.sceneClosed[String(g.num)] = !closed;
        repaintScenePage("scenes");
      };
      left.appendChild(h);
      grid = null;              // v4.5.41：每档一个网格，标题留在网格外
      if (!list.length) {
        const e = document.createElement("div");
        e.className = "av3-meta";
        e.style.margin = "0 0 6px 4px";
        e.textContent = "（这一档暂无预设）";
        left.appendChild(e);
        continue;
      }
      if (closed) continue;
      for (const sc of list) pushCard(sc);
    }

    if (hidden > 0) {
      const mb = document.createElement("div");
      mb.className = "av3-charbar";
      mb.id = "av3-scene-more";
      mb.style.marginTop = "10px";
      const t = document.createElement("span");
      t.className = "av3-meta";
      t.textContent = `还有 ${hidden} 条未渲染（滚到底部自动续载）`;
      mb.appendChild(t);
      const b = document.createElement("button");
      b.className = "av3-btn pri";
      b.id = "av3-scene-morebtn";
      b.textContent = `▸ 再显示 ${Math.min(SCENE_PAGE, hidden)} 条`;
      b.onclick = () => { ui.sceneLimit += SCENE_PAGE; repaintScenePage("scenes"); };
      mb.appendChild(b);
      const ba = document.createElement("button");
      ba.className = "av3-btn";
      ba.id = "av3-scene-moreall";
      ba.textContent = "显示全部";
      ba.title = "一次性渲染剩下的全部（190 条左右，低配机会卡一下）";
      ba.onclick = () => { ui.sceneLimit = 9999; repaintScenePage("scenes"); };
      mb.appendChild(ba);
      left.appendChild(mb);
    }
    ui._sceneMore = hidden > 0;
    hookSceneScroll();
  }

  /* ── 🔍 特写搭配（v3.18.0）───────────────────────────────────────
     与「🎬 场景搭配」**共用同一份数据**（/scenes 一次拉全），但两个页签
     各自过滤、各自分组，一条也不混排：
       · 这一页只列 `manual: true` 的 32 条；场景页反过来只列非 manual 的 122 条。
       · 一级分组按**部位**（顺序 = 库里书写顺序：面部 → 眼与唇 → 上半身与胸 → 手 → 腿 → 脚），
         不按档位 —— 找"拍腿"时不该先想"这是几档"；档位只做卡片角标。
       · 套用仍走同一个 `n_canon` 权重机制：引擎侧只对「选项权重 ≥ 3.0」放行，
         所以随机抽取永远抽不到它们（见 tests/check_v3_near.py）。
  */
  function renderNear() {
    left.classList.remove("charmode");
    const d = ui.scenes;
    if (!d) {
      left.innerHTML = `<div class="av3-empty">正在加载特写搭配库…</div>`;
      loadScenes();
      return;
    }
    // v5.10.0：武器场景也是 manual_only 且已独立成页签，别把它捞进特写页
    // v6.6.0：姿势库同理（v5.1.0 独立成页签时漏排了）—— 42 条 `group=pose`
    //   的瑜伽 / 伸展条目一直混在这里：它们没有 `part`，下面的分组循环不渲染，
    //   于是"共 102 条"却只有 60 张卡，勾选 / 随机还会命中看不见的条目。
    const all = (d.scenes || []).filter((s) => s.manual
      && s.group !== "art" && s.group !== "weapon" && s.group !== "pose");
    if (!all.length) {
      left.innerHTML = `<div class="av3-empty">特写搭配库是空的。<br>
        确认 <code>presets/anima_v4_scenes.json</code> 里有 <code>manual_only</code> 条目，
        必要时跑 <code>python tools/build_scenes.py</code> 重建。</div>`;
      return;
    }
    // v3.43.0：收藏与场景页**共用一份**，所以这一页也要拉（拉过就不重复请求）
    if (!ui.sceneFav && !ui.sceneFavLoading) loadSceneFavs();
    left.innerHTML = "";
    const applied = appliedSceneId();

    const tip = document.createElement("div");
    tip.className = "av3-meta";
    tip.innerHTML = "按 <b>部位</b> 取景的固定组合：<b>景别 + 部位焦点 + 机位/景深 + 局部场景</b> 一次锁定。<br>"
      + "🔍 这一页<b>只允许手动套用</b> —— 不进随机池，抽卡时不会突然冒出特写。"
      + "（v6.6.0：想在一批里随机，用上面的 <b>☑ 勾选 + 🎲 从勾选随机</b>。）<br>"
      + "✂ 套用时会按部位<b>收紧取景</b>：面部 / 眼与唇 关 ④⑤ + 动作/手部/设备/构图"
      + "（<b>角色与表情保留</b>）；上半身与胸 只关「身材轮廓 / 腿部线条」两项；"
      + "<b>手 / 腿 / 脚 / 臀</b> 关 ②③④⑤ 并清空角色（局部特写不该有脸）。"
      + "取消套用时全部恢复。<br>"
      + "⚠️ 代价：手/腿/脚/臀/成人向 出图时<b>不会有具体角色</b>；"
      + "面部 / 眼与唇 / 上半身与胸 保留角色，并支持<b>全库随机</b>或<b>限定作品 IP 内随机</b>。";
    tip.style.marginBottom = "8px";
    left.appendChild(tip);
    left.appendChild(autoLevelBar());

    /* v3.43.0：这一页同样要能搜、能只看收藏 —— 60 条按部位排开也不好找。
       ⚠️ 这里**不另开"收藏置顶分区"**：本页的分组维度是部位（面部→腿→脚），
          置顶一块收藏会把部位顺序打断，反而更难读；收藏用「⭐只看收藏」筛。 */
    const toks = sceneTokens(ui.sceneQuery);
    const favSet = ui.sceneFav || new Set();
    let hits = toks.length ? all.filter((s) => sceneHit(s, toks)) : all.slice();
    if (ui.sceneFavOnly) hits = hits.filter((s) => favSet.has(s.id));
    left.appendChild(sceneSearchBar(hits.length, "av3-near", "near"));

    /* ── v6.6.0：特写搭配的「勾选后随机」工具条（用户 2026-11-07）───────
       特写组同样是 manual_only（不进随机池），所以与姿势库 / 武器场景
       同一套机制：勾几条 → 从勾选集里抽一条并套用。
       作用域 = 当前筛选结果（搜索 + 收藏 + 部位），无筛选时等于本页全选。
       ⚠️ 抽到的条目可能来自不同部位 —— 套用时 `applyScene` 会按该条目的
          `nearSlim` 重新收紧取景，并先 `restoreNear()` 还回上一条的改动，
          所以不会出现"上一次关掉的字段一直停着"（v3.18.5 那个坑）。 */
    left.appendChild(buildPickBar(
      "near", "🎯 勾选特写后随机",
      /* ⚠️ 作用域必须是**看得见的卡**：`hits` 里还混着 42 条 `part` 不在
         `near_parts` 里的老 manual 条目，它们在下面的分组循环里根本不渲染 ——
         把看不见的条目也塞进勾选，就会出现"全选 102、页面只有 60 张卡"，
         而且「从勾选随机」可能抽到一张页面上找不到的搭配。 */
      hits.filter((s) => (ui.nearPart ? s.part === ui.nearPart
        : (d.near_parts || []).indexOf(s.part) >= 0)).map((s) => s.id)));

    /* ── 🎭 角色来源（v3.18.2）────────────────────────────────────
       面部 / 眼与唇 / 上半身与胸 三组**保留角色**，所以这一页也要能像场景搭配那样
       "不选 = 全库随机、选了作品 IP = 该 IP 内随机"。
       ⚠️ 写的是同一个节点控件 `role_works`（与角色库「🔁 随机池」同一份数据），
          不是另开一套设置 —— 两边改的是一处。 */
    const partNow = ui.nearPart || "";
    // v5.0.6：「手部」也放角色条 —— 该组里有点名要看脸的条目（`role_ok`，
    // 如「手指抚唇」），不给入口的话用户根本不知道这一条带的是谁。
    // 手部其它条目仍按原设计清角色，套用后状态条会写明「清空角色并锁住 role」。
    if (!partNow || NEAR_ROLE_PARTS.indexOf(partNow) >= 0
        || partNow === "🔞 成人向" || partNow === "手部") {
      const rb = document.createElement("div");
      rb.className = "av3-charbar";
      rb.style.flexWrap = "wrap";
      const lb = document.createElement("span");
      lb.className = "av3-meta";
      lb.textContent = (partNow === "🔞 成人向" || partNow === "手部")
        ? "🎭 角色来源（仅标了「🎭 角色生效」的条目）："
        : "🎭 角色来源（仅 面部 / 眼与唇 / 上半身与胸）：";
      rb.appendChild(lb);
      // v3.43.0：给标题一个 id —— 之前只靠「第一个 .av3-charbar 里的 .av3-meta」
      // 定位，本页新增搜索栏后这个顺序假设立刻失效（冒烟里当场红了一条）。
      lb.id = "av3-near-rolelabel";
      const ip = document.createElement("input");
      ip.type = "text";
      ip.id = "av3-near-rolesrc-ip";
      ip.placeholder = "留空 = 全库随机；填作品 slug / 英文名，多个用 | 分隔";
      ip.value = String(lsGet(node, "role_works", "") || "");
      ip.style.cssText = "flex:1;min-width:190px";
      const state = document.createElement("span");
      state.className = "av3-meta";
      state.id = "av3-near-rolesrc";
      const paint = () => {
        const w = String(lsGet(node, "role_works", "") || "").trim();
        const n = selectedList().length;
        state.textContent = n ? `已选 ${n} 位角色（优先使用）`
          : w ? `在作品「${w}」内随机` : "全库随机";
      };
      ip.oninput = () => {
        wset(node, "role_works", ip.value);
        app.graph.setDirtyCanvas(true, true);
        paint();
      };
      rb.appendChild(ip);
      rb.appendChild(state);
      const go = document.createElement("button");
      go.className = "av3-btn";
      go.textContent = "🎭 去角色库";
      go.title = "角色库的「🔁 随机池」写的是同一个 role_works 控件";
      go.onclick = () => { ui.tab = "chars"; syncTabs(); };
      rb.appendChild(go);
      paint();
      left.appendChild(rb);
    }

    /* ── ⚠️ role 锁定诊断（v3.18.4）────────────────────────────────
       `lock_fields` 含 `role` 时引擎会**跳过自动抽角色**，只剩手填 force_role 能出人。
       这个锁本来是"套用无脸条目时临时写进去"的，但它写在节点控件上、随工作流持久化；
       页面刷新 / 节点重开 / 换工作流之后内存快照丢了，role 就永久锁着 ——
       表现为"随机角色与作品 IP 抽角色都不生效，只有指定角色生效"。
       面板此前对 lock_fields **没有任何可见入口**，用户无从察觉。这里给它一个出口。 */
    {
      const roleLocked = lockList().indexOf("role") >= 0;
      const curNow = (d.scenes || []).find((s) => s.id === applied);
      const keepsRole = curNow && curNow.manual ? !nearSlim(curNow)?.dropRole : false;
      if (roleLocked) {
        const wb = document.createElement("div");
        wb.className = "av3-charbar";
        wb.style.flexWrap = "wrap";
        const wt = document.createElement("span");
        wt.className = "av3-meta";
        wt.id = "av3-near-rolelock";
        if (keepsRole || !curNow) wt.style.color = "#e8a33d";
        wt.textContent = (keepsRole || !curNow)
          ? "⚠️ role 被锁在「锁定字段」里 → 随机角色与作品 IP 抽角色都不会生效（只有手填角色才出人）"
          : "role 已临时锁定：本条不含角色（换套或取消套用会自动还回）";
        wb.appendChild(wt);
        if (keepsRole || !curNow) {
          const ub = document.createElement("button");
          ub.className = "av3-btn";
          ub.id = "av3-near-roleunlock";
          ub.textContent = "🔓 解除 role 锁";
          ub.title = "从「锁定字段」里移除 role；不改动你的其它锁定项";
          ub.onclick = () => {
            writeLocks(lockList().filter((t) => t !== "role"));
            persist();
            renderNear();
            setStatus("已从「锁定字段」移除 role —— 随机角色与作品 IP 抽角色恢复生效", "ok");
          };
          wb.appendChild(ub);
        }
        left.appendChild(wb);
      }
    }

    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    const cur = (d.scenes || []).find((s) => s.id === applied);
    const st = document.createElement("span");
    st.className = "av3-meta";
    st.id = "av3-near-cur";
    st.textContent = !cur ? "当前未套用特写搭配"
      : cur.manual ? `当前套用：${cur.zh}（${cur.part} · L${cur.level}）`
      : `当前套用的是场景搭配「${cur.zh}」（见 🎬 页）`;
    bar.appendChild(st);
    if (cur) {
      const cb = document.createElement("button");
      cb.className = "av3-btn";
      cb.textContent = "取消套用";
      cb.onclick = () => clearScene();
      bar.appendChild(cb);
    }
    const sum = document.createElement("span");
    sum.className = "av3-meta";
    sum.id = "av3-near-sum";
    sum.textContent = `共 ${hits.length} 条 · ${(d.near_parts || []).length} 个部位`;
    bar.appendChild(sum);
    const allOpen = hits.length > 0 && hits.every((sc) => ui.sceneOpen[sc.id]);
    const exp = document.createElement("button");
    exp.className = "av3-btn";
    exp.id = "av3-near-expand";
    exp.textContent = allOpen ? "▾ 全部收起" : "▸ 全部展开";
    exp.onclick = () => {
      const on = !allOpen;
      ui.sceneOpen = {};
      if (on) for (const sc of hits) ui.sceneOpen[sc.id] = true;
      renderNear();
    };
    bar.appendChild(exp);
    left.appendChild(bar);

    /* 部位筛选条（“全部 / 面部 / 腿 …”）。用同一套 av3-btn，
       选中的挂 pri（金底）—— 与「套用」按钮同色，语义上都是"当前生效"。
       v3.44.0：整条可收起，与场景页的题材条同一套手感（收起了仍显示当前部位）。 */
    const sel = document.createElement("div");
    sel.className = "av3-charbar";
    sel.id = "av3-near-parts";
    sel.style.flexWrap = "wrap";
    const parts = (d.near_parts || []).slice();
    const partCur = ui.nearPart || "";
    const ptog = document.createElement("button");
    ptog.className = "av3-btn" + (partCur ? " pri" : "");
    ptog.id = "av3-near-parttoggle";
    ptog.textContent = `${ui.nearPartOpen ? "▾" : "▸"} 🏷 部位 ${parts.length}`;
    ptog.title = ui.nearPartOpen ? "收起部位筛选条（收起后当前部位仍显示）" : "展开部位筛选条";
    ptog.onclick = () => {
      ui.nearPartOpen = !ui.nearPartOpen;
      repaintScenePage("near");
    };
    sel.appendChild(ptog);
    if (!ui.nearPartOpen) {
      const s = document.createElement("span");
      s.className = "av3-meta";
      s.id = "av3-near-partsun";
      s.textContent = partCur ? `当前：${partCur}` : "全部部位";
      sel.appendChild(s);
      if (partCur) {
        const x = document.createElement("button");
        x.className = "av3-btn";
        x.id = "av3-near-partclear";
        x.textContent = "✕ 取消部位";
        x.title = "清掉部位筛选（不用先展开）";
        x.onclick = () => {
          ui.nearPart = "";
          repaintScenePage("near");
        };
        sel.appendChild(x);
      }
      left.appendChild(sel);
    } else {
      const mkPart = (label, val) => {
        const b = document.createElement("button");
        b.className = "av3-btn" + (partCur === val ? " pri" : "");
        b.textContent = label;
        b.onclick = () => { ui.nearPart = val; renderNear(); };
        sel.appendChild(b);
      };
      mkPart(`全部 ${hits.length}`, "");
      for (const p of parts) {
        mkPart(`${p} ${hits.filter((s) => s.part === p).length}`, p);
      }
      left.appendChild(sel);
    }

    // ⚠️ 用 hits（已按搜索 / 收藏筛过）而不是 all —— 否则搜了词，部位计数仍是全量
    const shown = ui.nearPart ? hits.filter((s) => s.part === ui.nearPart) : hits;
    if (!shown.length) {
      const e = document.createElement("div");
      e.className = "av3-empty";
      e.id = "av3-near-empty";
      e.textContent = "没有匹配的特写搭配。换个词，或点上面的「清空筛选」。";
      left.appendChild(e);
      return;
    }
    for (const p of (ui.nearPart ? [ui.nearPart] : parts)) {
      const list = shown.filter((s) => s.part === p);
      if (!list.length) continue;
      const h = document.createElement("div");
      h.className = "av3-note";
      h.style.cssText = "margin:10px 0 6px";
      h.dataset.part = p;
      h.textContent = `${p}（${list.length}）`;
      left.appendChild(h);
      for (const sc of list) left.appendChild(sceneCard(sc, sc.id === applied));
    }
  }

  /** 场景卡（v3.11.0 改版）。
   *
   *  两个改动都是"方便预览与选择"：
   *    ① **默认折叠** —— 87 条全展开要滚几千像素；现在只出一行
   *       （箭头 + 中文名 + 【题材】+ L档 + 字段数），点标题行才展开字段。
   *    ② **动作行常驻** —— 「一键套用 / 👁 预览 / 套用并预演」放在 `.bd`
   *       **外面**，折叠状态也能直接操作，不用先展开。
   */
  function sceneCard(sc, on) {
    const open = !!ui.sceneOpen[sc.id];
    const onFav = (ui.sceneFav || new Set()).has(sc.id);
    const box = document.createElement("div");
    box.className = "av3-sec" + (on ? " on" : "") + (open ? " open" : "")
      + (ui.sceneCompact ? " compact" : "");
    box.dataset.scene = sc.id;

    const hd = document.createElement("div");
    hd.className = "hd";
    hd.title = "点一下展开 / 收起这条搭配固定了哪些字段";

    /* v6.1.0：姿势库 / 武器场景的勾选框（标题行最左、箭头之前）。
       v6.6.0：特写搭配（near）也接同一套 —— 三页都是 manual_only，
       都需要"先圈定范围再随机"。
       ⚠️ 必须 stopPropagation —— hd 的 onclick 是「展开 / 收起」，
          不拦的话勾一下会顺手把卡片展开。 */
    if (ui.tab === "pose" || ui.tab === "weapon" || ui.tab === "near"
        || ui.tab === "art") {
      const pkey = ui.tab === "near" ? "near" : ui.tab;
      const parr = pickArr(pkey);
      const pcb = document.createElement("input");
      pcb.type = "checkbox";
      pcb.className = "av3-pick";
      pcb.checked = parr.indexOf(sc.id) >= 0;
      pcb.title = "勾选后可在上方「🎲 从勾选随机」里抽到它";
      pcb.onclick = (ev) => ev.stopPropagation();
      pcb.onchange = () => {
        const i = parr.indexOf(sc.id);
        if (pcb.checked && i < 0) parr.push(sc.id);
        else if (!pcb.checked && i >= 0) parr.splice(i, 1);
        syncPickBar(pkey);       // 只更新计数，不重画（保住滚动位置）
        syncPoolIds();           // v6.7.0：自动随机跟着勾选取值
      };
      hd.appendChild(pcb);
    }

    const arw = document.createElement("span");
    arw.className = "arw";
    arw.textContent = open ? "▼" : "▶";
    const t = document.createElement("span");
    t.className = "t";
    // v5.11.0：IP 武器条目前缀《作品名》—— 一眼看出「哪个作品的什么武器」
    t.textContent = (sc.work_zh && sc.work !== "general")
      ? "《" + sc.work_zh + "》" + sc.zh : sc.zh;
    const th = document.createElement("span");
    th.className = "th";
    th.textContent = sc.theme ? `【${sc.theme}】` : "";
    const lv = document.createElement("span");
    lv.className = "av3-meta";
    lv.textContent = sc.level > 0 ? `L${sc.level}` : "全档";
    lv.title = "该预设可用到的最高档位（超过就抽不到它）";
    const nf = document.createElement("span");
    nf.className = "av3-meta";
    nf.textContent = `${(sc.fields || []).length} 字段`;
    hd.appendChild(arw); hd.appendChild(t); hd.appendChild(th);
    hd.appendChild(lv);
    /* v5.0.1：推荐画幅徽标。数据来自 presets/anima_v4_scene_aspects.json
       （旁挂映射，重跑 build_scenes 不会丢）；缺省 both。 */
    const apKey = sc.aspect === "portrait" || sc.aspect === "landscape"
      ? sc.aspect : "both";
    const apTag = document.createElement("span");
    apTag.className = "av3-aspect av3-aspect-" + apKey;
    apTag.textContent = apKey === "portrait" ? "竖"
      : apKey === "landscape" ? "横" : "通";
    apTag.title = apKey === "portrait"
      ? "推荐竖屏：人物独大 / 竖向动作（站、悬吊、靠墙）—— 竖构图 3:4"
      : apKey === "landscape"
        ? "推荐横屏：多人并排 / 躺卧贴地 / 宽景 —— 横构图（如 16:9 或 3:2）"
        : "比例通用：特写或没有强空间关系，竖横都成立";
    hd.appendChild(apTag);
    hd.appendChild(nf);
    /* v3.43.0：收藏星标常驻标题行（折叠也看得见、点得到）。
       ⚠️ 必须 stopPropagation —— 标题行的 onclick 是"展开 / 收起"，
          不拦的话点一下星标会顺手把卡片展开。 */
    const sfav = document.createElement("span");
    sfav.className = "av3-sfav" + (onFav ? " on" : "");
    sfav.textContent = onFav ? "★" : "☆";
    sfav.title = onFav
      ? "取消收藏（场景页与特写页共用这一份）"
      : "收藏这条（两页共用，存 presets/user_scene_favorites.json 跨会话保留）";
    sfav.onclick = async (ev) => {
      ev.stopPropagation();
      const want = !(ui.sceneFav || new Set()).has(sc.id);
      if (await toggleSceneFav(sc, want)) {
        // v4.5.41：收藏区默认收起 —— 刚收藏的那条如果不自动展开，
        // 用户点了 ★ 只会看到卡片凭空消失（它已从档位分组里挪走）。
        ui.sceneFavOpen = want;
        repaintScenePage(ui.tab === "near" ? "near" : "scenes");
      }
    };
    hd.appendChild(sfav);
    // v3.18.0：特写搭配的角标 —— 「只允许手动套用」+ 该部位会收紧什么，
    // 都在点之前就看得见，不靠事后解释。
    if (sc.manual) {
      const mb = document.createElement("span");
      mb.className = "av3-meta";
      mb.textContent = "🔍 仅手动";
      mb.title = "特写搭配不进随机池：只能在这里点「套用」，抽卡时不会突然出现";
      hd.appendChild(mb);
      const prof = nearSlim(sc);
      if (prof) {
        const fb = document.createElement("span");
        fb.className = "av3-meta";
        fb.textContent = "✂ 收紧取景";
        const secLab = prof.sections.length
          ? `关 ${prof.sections.map((k) => SECTION_SHORT[k] || k).join("/")} 段` : "不动段";
        fb.title = `套用时按「${sc.part}」收紧：${secLab}；`
          + `并把 ${prof.fields.length} 个竞争字段权重设为 0；取消套用时恢复`;
        hd.appendChild(fb);
        if (prof.dropRole) {
          const rb = document.createElement("span");
          rb.className = "av3-meta";
          rb.textContent = "🧹 清角色";
          rb.title = "套用时临时清空「已选角色 / 手填角色 / 限定作品」并锁住 role"
            + "（角色 tag 会把画框拉回脸）；取消套用时原样恢复";
          hd.appendChild(rb);
        } else if (prof.roleOk) {
          const rb = document.createElement("span");
          rb.className = "av3-meta";
          rb.textContent = "🎭 角色生效";
          rb.title = "这一条画框里看得见脸/头发 → 保留角色与 ②③ 段，"
            + "支持全库随机或限定作品 IP 内随机";
          hd.appendChild(rb);
        }
        if (sc.count_lock) {
          const cb2 = document.createElement("span");
          cb2.className = "av3-meta";
          cb2.textContent = /^2girls/.test(sc.count_lock) ? "👭 双女" : `👥 ${sc.count_lock}`;
          cb2.title = `套用时人数钉死为「${sc.count_lock}」`;
          hd.appendChild(cb2);
        }
      }
    }
    box.appendChild(hd);

    const bd = document.createElement("div");
    bd.className = "bd";
    if (sc.desc) {
      const de = document.createElement("div");
      de.className = "av3-meta";
      de.textContent = sc.desc;
      de.style.marginBottom = "5px";
      bd.appendChild(de);
    }
    const rows = document.createElement("div");
    rows.style.cssText = "display:flex;flex-wrap:wrap;gap:4px";
    for (const f of (sc.fields || [])) {
      const chip = document.createElement("span");
      chip.className = "av3-chip";
      chip.textContent = `${f.lab}：${f.en}`;
      chip.title = `${f.field} = ${f.en}（L${f.level}）`;
      rows.appendChild(chip);
    }
    if (sc.extra) {
      const chip = document.createElement("span");
      chip.className = "av3-chip";
      chip.textContent = `补充 ${sc.extra_count} 条`;
      chip.title = sc.extra;
      rows.appendChild(chip);
    }
    bd.appendChild(rows);
    box.appendChild(bd);

    const setOpen = (o) => {
      ui.sceneOpen[sc.id] = o;
      box.classList.toggle("open", o);
      arw.textContent = o ? "▼" : "▶";
    };
    hd.onclick = () => setOpen(!box.classList.contains("open"));

    /* ── 动作行（常驻，折叠时也能点）── */
    const act = document.createElement("div");
    act.className = "av3-sceneact";
    const b = document.createElement("button");
    b.className = "av3-btn" + (on ? "" : " pri");
    b.textContent = on ? "✅ 已套用" : "✅ 一键套用";
    b.disabled = on;
    b.onclick = () => applyScene(sc);
    act.appendChild(b);

    /* 👁 预览：**只看不套用** —— 不改写权重表，临时构造权重跑一次抽取。
       这是与「套用并预演」的关键区别：后者会真的把这条搭配写进权重。 */
    const out = document.createElement("div");
    out.className = "av3-out av3-sceneout";
    out.style.display = "none";
    const pv = document.createElement("button");
    pv.className = "av3-btn" + (on ? "" : " pri");
    pv.textContent = "👁 预览";
    pv.dataset.on = "0";
    pv.title = "只看不套用：不改动你的权重表，临时按这条搭配抽一次并显示结果";
    pv.onclick = async () => {
      if (pv.dataset.on === "1") {          // 再点一次 = 收起
        out.style.display = "none";
        pv.dataset.on = "0";
        pv.textContent = "👁 预览";
        return;
      }
      pv.disabled = true;
      pv.textContent = "抽取中…";
      try {
        const d = await panelPreview(sc);
        if (!d || d.error) {
          out.textContent = "预览失败：" + ((d && d.error) || "无响应");
        } else {
          out.innerHTML = "";
          const body = document.createElement("div");
          body.textContent = d.text || "（空）";
          const m2 = document.createElement("div");
          m2.className = "av3-meta";
          m2.style.marginTop = "6px";
          const rep = d.report || {};
          m2.textContent = `档位 ${d.level_label}｜落档 ${rep.real_level}`
            + `｜标签 ${rep.n}｜自检 ${rep.ok_f}/${rep.total_f}`;
          out.appendChild(body);
          out.appendChild(m2);
        }
        out.style.display = "block";
        pv.dataset.on = "1";
        pv.textContent = "👁 收起预览";
      } catch (e) {
        out.textContent = "预览失败：" + e.message;
        out.style.display = "block";
        pv.textContent = "👁 预览";
      } finally {
        pv.disabled = false;
      }
    };
    act.appendChild(pv);

    const pv2 = document.createElement("button");
    pv2.className = "av3-btn av3-act2";   // v4.5.41：次级动作，3 列网格里只在展开时显示
    pv2.textContent = "🎲 套用并预演";
    pv2.title = "套用这条搭配（会写进权重表）后立刻跑一次抽取";
    pv2.onclick = () => {
      applyScene(sc);
      if (panelRoll) panelRoll("all");
    };
    act.appendChild(pv2);
    box.appendChild(act);
    box.appendChild(out);
    return box;
  }

  /**
   * 这个段在当前档位下"有没有事可做"（用于给段标题挂「本档不参与」）。
   *
   * ⚠️ 判据是「段内**还有没有** ≤ 当前档的选项」，**不是**段最高档 ≤ 当前档。
   *    用 max_level 判会误伤分档段：v3.8.0 给「视角」加了第一人称词条
   *    （`pov crotch` 是 2 档、`pov, fellatio` 是 3 档），⑧镜头段的最高档
   *    被抬到 3 —— 于是全年龄档会给 ⑧镜头 打上「本档不参与」，
   *    而实际上该段 16 条全年龄词条照样在抽，纯属误导。
   *    （同样的误标也一直存在于「④ 身材与肤质」段，本次一并修掉。）
   *
   * ⚠️ 纯文本段（一个带选项的字段都没有，如 ⑫自然语言补位）恒参与 ——
   *    它没有档位概念，内容由自检兜底。
   */
  function sectionEligible(sec) {
    const lvNum = levelNum(ui.level);
    let optFields = 0;
    for (const f of (sec.fields || [])) {
      const opts = f.opts || [];
      if (!opts.length) continue;          // 文本框 / 预设位按"无选项"跳过
      optFields += 1;
      if (opts.some((o) => Number(o.level || 0) <= lvNum)) return true;
    }
    return optFields === 0;
  }

  function renderTree() {
    left.classList.remove("charmode");
    left.innerHTML = "";
    const tip = document.createElement("div");
    tip.className = "av3-meta";
    tip.innerHTML =
      "<b>权重</b>　段 / 字段 / 选项三层，数值 0–3：" +
      "<b>0</b> = 关闭（永不出现）　<b>1.0</b> = 默认　<b>3.0</b> = 字段必定出现　" +
      "中间值是相对倾向。<br>" +
      "想让某条选项「一定有」，把它的字段权重拉到 3.0，再把同字段其它选项设为 0。<br>" +
      "⚠️ 低于当前档位的内容不会出现 —— 权重改不了这一点。<br>" +
      "🎭 <b>角色的选择在「角色库」页签</b>（点卡片即采用），这里不提供角色权重。";
    tip.style.marginBottom = "8px";
    left.appendChild(tip);

    /* ── v6.5.2 皮肤干爽过滤（逐条可控，用户 2026-11-07）──────────────
       ⚠️ 真值只有一个：`dry_skin_tags`（逗号分隔英文 tag）
         "all" = 全部；"none" = 显式一条都不关；空串 = **回落 dry_skin_groups 组语义**
       老工作流没有这个控件 → 空串 → 引擎按原组语义跑，零回归。
       组复选框是**派生状态**（全勾 / 半选 indeterminate / 全不勾）+ 批量操作，
       不再单独存值；明细区每条词条各有独立复选框。
       ⚠️ 引擎只在成品 tag 列表层剔除，不吃 rng、不改档位；关闭态零漂移。
       ⚠️ 分组定义从 /contract 读（唯一真源在引擎），下面只是断线兜底。 */
    const DRY_FALLBACK = [
      { id: "skin", zh: "皮肤" }, { id: "weather", zh: "天气与水" },
      { id: "hair", zh: "头发" }, { id: "cloth", zh: "衣物与体液" },
      { id: "light", zh: "光效" },
    ];
    const dryDefs = (contract && Array.isArray(contract.dry_skin_groups)
                     && contract.dry_skin_groups.length)
      ? contract.dry_skin_groups : DRY_FALLBACK;
    function groupItems(g) {
      return (g.items && g.items.length) ? g.items
        : (g.tags || []).map((t) => ({ en: t, zh: t }));
    }
    const ALL_DRY_TAGS = dryDefs.flatMap(
      (g) => groupItems(g).map((it) => it.en));

    // 逐条勾选真值。新控件为空 → 按老组语义展开（默认全关，零回归）。
    const savedTags = String(wget(node, "dry_skin_tags", "") || "")
      .trim().toLowerCase();
    const savedGroups = String(wget(node, "dry_skin_groups", "") || "").trim();
    const savedGroupSet = savedGroups && savedGroups !== "all"
      ? new Set(savedGroups.split(",").map((s) => s.trim()).filter(Boolean))
      : null;
    let checkedTags;
    if (!savedTags) {
      checkedTags = new Set(dryDefs
        .filter((g) => !savedGroupSet || savedGroupSet.has(g.id))
        .flatMap((g) => groupItems(g).map((it) => it.en)));
    } else if (savedTags === "none") {
      checkedTags = new Set();
    } else if (savedTags === "all") {
      checkedTags = new Set(ALL_DRY_TAGS);
    } else {
      checkedTags = new Set(savedTags.split(",")
        .map((s) => s.trim()).filter(Boolean));
    }

    const dryWrap = document.createElement("div");
    dryWrap.id = "av3-dry-skin";
    dryWrap.style.cssText = "margin:0 0 9px;padding:7px 9px;border:1px solid #3a3a3a;"
      + "border-radius:7px;background:rgba(255,255,255,.03);font-size:12px;color:inherit";
    const dryHead = document.createElement("label");
    dryHead.style.cssText = "display:flex;align-items:center;gap:7px;cursor:pointer";
    const dryCb = document.createElement("input");
    dryCb.type = "checkbox";
    dryCb.id = "av3-dry-skin-cb";
    dryCb.checked = !!wget(node, "dry_skin_filter", false);
    dryCb.title = "勾上 = 最终提示词里剔除勾选的致水珠词条\n"
      + "不勾 = 完全不介入（逐字节零漂移）";
    const dryTxt = document.createElement("span");
    dryTxt.textContent = "🚫 关闭会增加皮肤水珠的词条（干爽过滤）";
    dryHead.appendChild(dryCb);
    dryHead.appendChild(dryTxt);
    dryWrap.appendChild(dryHead);

    const dryBox = document.createElement("div");
    dryBox.id = "av3-dry-skin-groups";
    dryBox.style.cssText = "display:flex;flex-wrap:wrap;gap:5px 12px;margin:7px 0 0 18px";
    const dryGroupCbs = [];
    for (const g of dryDefs) {
      const lab = document.createElement("label");
      lab.style.cssText = "display:flex;align-items:center;gap:4px;cursor:pointer";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.dataset.group = g.id;
      if (g.tags && g.tags.length) cb.title = g.tags.join(", ");
      const sp = document.createElement("span");
      sp.textContent = g.zh;
      lab.appendChild(cb);
      lab.appendChild(sp);
      dryBox.appendChild(lab);
      dryGroupCbs.push(cb);
    }
    dryWrap.appendChild(dryBox);

    /* v6.5.1：常驻明细 —— 直接列出「本次会关闭」的词条（中文 + 英文），
       跟随勾选实时刷新；旧 contract 没有 items 时退回只显示英文 tag。 */
    const dryDetailHead = document.createElement("div");
    dryDetailHead.style.cssText = "display:flex;align-items:center;gap:8px;margin:7px 0 0 18px";
    const dryDetailBtn = document.createElement("button");
    dryDetailBtn.type = "button";
    dryDetailBtn.id = "av3-dry-skin-detail-toggle";
    const dryDetailOpen = ui.dryDetailOpen !== false;
    dryDetailBtn.textContent = dryDetailOpen ? "▾ 收起词条明细" : "▸ 展开词条明细";
    dryDetailHead.appendChild(dryDetailBtn);
    const dryDetailCount = document.createElement("span");
    dryDetailCount.id = "av3-dry-skin-detail-count";
    dryDetailCount.style.cssText = "opacity:.7;font-size:11px";
    dryDetailHead.appendChild(dryDetailCount);
    dryWrap.appendChild(dryDetailHead);

    const dryDetail = document.createElement("div");
    dryDetail.id = "av3-dry-skin-detail";
    dryDetail.style.cssText = "margin:5px 0 0 18px;padding:6px 8px;"
      + "border-left:2px solid #4a4a4a;font-size:11px;line-height:1.7;opacity:.9";
    dryDetail.style.display = dryDetailOpen ? "" : "none";
    dryWrap.appendChild(dryDetail);

    /* 组复选框 = 逐条勾选状态的派生显示（全勾 / 半选 / 全不勾）。 */
    function syncGroupCbs() {
      dryDefs.forEach((g, i) => {
        const items = groupItems(g).map((it) => it.en);
        const on = items.filter((t) => checkedTags.has(t)).length;
        const cb = dryGroupCbs[i];
        cb.checked = items.length > 0 && on === items.length;
        cb.indeterminate = on > 0 && on < items.length;
      });
    }
    function renderDryDetail() {
      dryDetail.innerHTML = "";
      let pickedCount = 0;
      let pickedGroups = 0;
      for (const g of dryDefs) {
        const items = groupItems(g);
        const on = items.filter((it) => checkedTags.has(it.en)).length;
        if (on) pickedGroups++;
        pickedCount += on;
        const row = document.createElement("div");
        row.dataset.group = g.id;
        row.style.cssText = "margin:2px 0";
        const b = document.createElement("b");
        b.textContent = "【" + g.zh + "】";
        row.appendChild(b);
        for (const it of items) {
          const lab = document.createElement("label");
          lab.style.cssText = "display:inline-flex;align-items:center;gap:3px;"
            + "margin-right:10px;cursor:pointer;white-space:nowrap";
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.dataset.tag = it.en;         // v6.5.2：逐条开关
          cb.checked = checkedTags.has(it.en);
          cb.title = it.en;
          cb.onchange = () => {
            if (cb.checked) checkedTags.add(it.en);
            else checkedTags.delete(it.en);
            writeDryTags();
          };
          const sp = document.createElement("span");
          sp.textContent = it.zh + "（" + it.en + "）";
          lab.appendChild(cb);
          lab.appendChild(sp);
          row.appendChild(lab);
        }
        dryDetail.appendChild(row);
      }
      dryDetailCount.textContent =
        `已关闭 ${pickedGroups} 组 / ${pickedCount} 条词条`;
    }
    dryDetailBtn.onclick = () => {
      ui.dryDetailOpen = !dryDetailOpen;
      persist();
      renderTree();
    };

    const dryTip = document.createElement("div");
    dryTip.className = "av3-meta";
    dryTip.style.cssText = "margin:6px 0 0 18px;opacity:.75;font-size:11px";
    dryTip.textContent = "默认全部关闭；可逐条勾选 / 取消，点「清空」后只勾一条 = 只关闭那一条。流泪（含泪 / 哭泣 / 泪痕）时保留泪光用的「湿润光泽肌」。";
    dryWrap.appendChild(dryTip);

    const dryBtns = document.createElement("div");
    dryBtns.style.cssText = "display:flex;gap:8px;margin:6px 0 0 18px";
    const dryAll = document.createElement("button");
    dryAll.type = "button";
    dryAll.id = "av3-dry-skin-all";
    dryAll.textContent = "全选";
    const dryNone = document.createElement("button");
    dryNone.type = "button";
    dryNone.id = "av3-dry-skin-none";
    dryNone.textContent = "清空";
    dryBtns.appendChild(dryAll);
    dryBtns.appendChild(dryNone);
    dryWrap.appendChild(dryBtns);

    /* 唯一的写入口：把逐条勾选落成 dry_skin_tags。
       ⚠️ 不再写 dry_skin_groups —— 它只作为「空 tags 时」的兜底语义保留，
          老工作流里已有的值一个字符都不动。 */
    function writeDryTags() {
      const picked = ALL_DRY_TAGS.filter((t) => checkedTags.has(t));
      wset(node, "dry_skin_tags",
           picked.length === 0 ? "none"
           : picked.length === ALL_DRY_TAGS.length ? "all"
           : picked.join(","));
      // "none" 是显式空集（不会回落组语义）；一条都没勾 → 顺手关掉总开关
      if (!picked.length && dryCb.checked) {
        dryCb.checked = false;
        wset(node, "dry_skin_filter", false);
      }
      syncGroupCbs();
      renderDryDetail();
      app.graph.setDirtyCanvas(true, true);
    }
    dryCb.onchange = () => {
      wset(node, "dry_skin_filter", dryCb.checked);
      app.graph.setDirtyCanvas(true, true);
      setStatus(dryCb.checked
        ? "🚫 干爽过滤已开启：成品提示词里剔除勾选的致水珠词条"
        : "🚫 干爽过滤已关闭：正文保持原样（零漂移）", "ok");
    };
    // 组复选框 = 批量勾选 / 取消该组全部词条（自身状态由 syncGroupCbs 派生）
    dryGroupCbs.forEach((cb, i) => {
      cb.onchange = () => {
        const items = groupItems(dryDefs[i]).map((it) => it.en);
        for (const t of items) {
          if (cb.checked) checkedTags.add(t);
          else checkedTags.delete(t);
        }
        writeDryTags();
      };
    });
    dryAll.onclick = () => {
      checkedTags = new Set(ALL_DRY_TAGS);
      writeDryTags();
    };
    dryNone.onclick = () => {
      checkedTags = new Set();
      writeDryTags();
    };
    syncGroupCbs();
    renderDryDetail();
    left.appendChild(dryWrap);

    for (const sec of pools.sections) {
      const eligible = sectionEligible(sec);
      const el = document.createElement("div");
      el.className = "av3-sec";
      el.dataset.sec = sec.id;

      const hd = document.createElement("div");
      hd.className = "hd";
      const arrow = document.createElement("span");
      arrow.textContent = "▶";
      const t = document.createElement("span");
      t.className = "t";
      // v6.5.1：词库与权重页不显示 R18 禁止图标（🔞），只留文字。
      // 只改渲染层、不动 pools.json —— 那是 export_v3_pools.mjs 的生成物，
      // 改数据会在下次重跑时被覆盖，且没必要动零漂移基线。
      t.textContent = String(sec.title || "").replace(/^🔞\s*/, "")
        + (eligible ? "" : "　（本档不参与）");
      const swIn = document.createElement("input");
      swIn.className = "av3-w";
      swIn.type = "number";
      swIn.min = "0";
      swIn.max = "3";
      swIn.step = "0.25";
      swIn.title = "段权重";
      swIn.value = fmtW(ui.weights.sections[sec.id]);
      swIn.onclick = (e) => e.stopPropagation();
      swIn.oninput = () => {
        setWeight(ui.weights.sections, sec.id, swIn.value);
        refreshSectionState();
        persist();
      };
      hd.appendChild(arrow);
      hd.appendChild(t);
      hd.appendChild(swIn);
      hd.onclick = () => {
        if (ui.openSecs.has(sec.id)) ui.openSecs.delete(sec.id);
        else ui.openSecs.add(sec.id);
        renderTree();
      };
      el.appendChild(hd);

      const bd = document.createElement("div");
      bd.className = "bd";
      for (const f of sec.fields) {
        // ⚠️⚠️ 角色字段已从权重页移除（v3.2.0）。
        //    角色的选择完全交给「角色库」页签 —— 点卡片 = 抽取概率 100%。
        //    这里再留一个权重框，用户就会以为"权重能关掉角色"，
        //    而那样做会与角色库的选择打架（点了角色却没出来）。
        //    段权重与其它字段都不受影响。
        if (f.key === "role") continue;

        const row = document.createElement("div");
        row.className = "av3-row";
        // ⚠️ 行必须带 key：上面跳过了 role，refreshSectionState 就不能再靠
        //    「第 i 行 = 第 i 个字段」的索引对齐 —— 会整体错位一格。
        row.dataset.key = f.key;

        const lab = document.createElement("span");
        lab.className = "lab";
        lab.textContent = f.lab;
        row.appendChild(lab);

        const lvInfo = document.createElement("span");
        lvInfo.className = "lv";
        if (f.type === "text" || !optCount(f)) {
          lvInfo.textContent = "手写";
        } else {
          const fs = fieldSpread(f.key);
          lvInfo.textContent = fs ? `L${fs}` : "";
          lvInfo.title = "该字段候选的档位分布";
        }
        row.appendChild(lvInfo);

        const wIn = document.createElement("input");
        wIn.className = "av3-w";
        wIn.type = "number";
        wIn.min = "0";
        wIn.max = "3";
        wIn.step = "0.25";
        wIn.title = "字段权重";
        wIn.value = fmtW(ui.weights.fields[f.key]);
        wIn.oninput = () => {
          setWeight(ui.weights.fields, f.key, wIn.value);
          refreshSectionState();
          persist();
        };
        row.appendChild(wIn);

        const badgeBtn = document.createElement("button");
        badgeBtn.className = "av3-t";
        badgeBtn.disabled = !optCount(f);
        badgeBtn.onclick = () => openDetail(node, ui, f, badgeBtn);
        row.appendChild(badgeBtn);

        bd.appendChild(row);
      }
      el.appendChild(bd);
      left.appendChild(el);
      secEls.set(sec.id, el);
    }
    refreshSectionState();
    applyOpenState();
  }

  function fieldSpread(fieldKey) {
    const f = findField(fieldKey);
    if (!f) return "";
    const lv = new Set((f.opts || []).map((o) => o.level));
    const arr = [...lv].sort((a, b) => a - b);
    return arr.join("/");
  }

  /**
   * 字段的候选数。
   * ⚠️ 只能从**词库**（/pools）的 opts 算 —— /contract 里的字段叫 option_count，
   *    两个接口的字段形状不一样，混用会让按钮永远是灰的（冒烟抓到过一次）。
   */
  function optCount(f) {
    return (f && f.opts ? f.opts.length : 0);
  }

  /** 被段权重关掉的段 id 串 */
  function disabledTxt() {
    return pools.sections
      .filter((s) => Number(ui.weights.sections[s.id] ?? 1) === 0)
      .map((s) => s.id)
      .join(",");
  }

  /**
   * 把面板状态落回节点控件。
   *
   * ⚠️ 这里必须**边改边存**，不能等「采用这条」：
   *    用户改完权重直接去排队，如果改动还在面板内存里，跑出来的还是旧设置 ——
   *    "我明明调了，怎么没生效"。这个坑冒烟抓到过一次。
   *    唯一例外是 seed：它代表"复现哪一条预览"，没预览过就不该乱写，
   *    所以只有「采用这条」会写它。
   */
  function persist() {
    saveWeights(node, ui.weights);
    wset(node, "disabled_sections", disabledTxt());
    app.graph.setDirtyCanvas(true, true);
  }

  function findField(key) {
    for (const s of pools.sections) {
      for (const f of s.fields) if (f.key === key) return f;
    }
    return null;
  }

  function setWeight(bag, key, raw) {
    const f = Number(raw);
    if (!Number.isFinite(f)) {
      delete bag[key];
      return;
    }
    const clamped = Math.max(0, Math.min(3, f));
    if (Math.abs(clamped - 1) < 1e-9) delete bag[key];
    else bag[key] = Math.round(clamped * 1000) / 1000;
  }

  function fmtW(v) {
    const f = Number(v);
    return Number.isFinite(f) ? String(Math.round(f * 1000) / 1000) : "1";
  }

  function applyOpenState() {
    for (const [sid, el] of secEls) {
      const on = ui.openSecs.has(sid);
      el.classList.toggle("open", on);
      el.querySelector(".hd").firstChild.textContent = on ? "▼" : "▶";
    }
  }

  /** 段开关状态、🎯 徽标、权重计数 —— 任何权重改动后都要刷新 */
  function refreshSectionState() {
    for (const [sid, el] of secEls) {
      const sw = Number(ui.weights.sections[sid] ?? 1);
      el.classList.toggle("off", sw === 0);
    }
    for (const sec of pools.sections) {
      const el = secEls.get(sec.id);
      if (!el) continue;
      for (const f of sec.fields) {
        if (f.key === "role") continue;       // 权重页里没有这一行
        // ⚠️ 按 data-key 查，**不能**用 rows[i] 索引 —— 跳过了 role 之后，
        //    索引与 sec.fields 的下标会错位一格，🎯 徽标全挂到别的字段上。
        const btn = el.querySelector(
          `.av3-row[data-key="${f.key}"] .av3-t`);
        if (!btn) continue;
        const b = weightBadge(ui.weights, f.key, optCount(f));
        btn.textContent = b.text;
        btn.className = "av3-t" + (b.state === 1 ? " s1" : b.state === 2 ? " s2" : "");
      }
    }
    refreshCounter();
  }

  /* ── 选项级权重细则 ── */
  function openDetail(node, ui, field, badgeBtn) {
    const card = document.createElement("div");
    card.className = "av3-mask";
    const inner = document.createElement("div");
    inner.className = "av3-mini";
    inner.style.position = "static";
    card.appendChild(inner);
    const box = document.createElement("div");
    box.className = "card";
    inner.appendChild(box);

    const hd = document.createElement("div");
    hd.className = "hd";
    hd.textContent = `权重细则 · ${field.lab}`;
    box.appendChild(hd);

    const bd = document.createElement("div");
    bd.className = "bd";
    box.appendChild(bd);

    const cur = { ...(ui.weights.options[field.key] || {}) };
    const inputs = [];

    /* v5.0.1（用户 2026-11-01）：同类型互斥只能有一个「必现」（权重 3）。
       点第二个 3 时，自动把同字段先前那个降回 1.0 并说明原因 ——
       否则两个 3.0 会被 forced_option_pool 一起收进候选池、由 weighted_pick
       二选一，等于「假必现」。
       ⚠️ 后端 draw() 里还有一层兜底 `enforce_option_mutex()`：
          外部脚本直接拼 weights_json 绕过这里，也不会出现两个必现。 */
    const hintEl = document.createElement("div");
    hintEl.className = "av3-meta";
    hintEl.id = "av3-w-mutex-hint";
    hintEl.style.cssText = "display:none;padding:3px 9px;color:#e0a458;"
      + "font-size:11.5px;line-height:1.45";
    let hintTimer = null;
    const showHint = (t) => {
      hintEl.textContent = t;
      hintEl.style.display = "block";
      if (hintTimer) clearTimeout(hintTimer);
      hintTimer = setTimeout(() => { hintEl.style.display = "none"; }, 5000);
    };
    const dropOtherForced = (keepEn) => {
      const dropped = [];
      for (const it of inputs) {
        if (it.en === keepEn) continue;
        const v = Number(cur[it.en]);
        if (Number.isFinite(v) && v >= 3) {
          setWeight(cur, it.en, 1);
          it.inp.value = "1";
          it.paint();
          dropped.push(it.zh || it.en);
        }
      }
      if (dropped.length) {
        showHint(`同类型只能有一个必现：「${dropped.join("」「")}」已自动降为 1.0`);
      }
      return dropped;
    };

    for (const o of field.opts) {
      const row = document.createElement("div");
      row.className = "av3-oRow";
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = o.zh;
      n.title = `档位 L${o.level}`;
      if (o.level > 0) {
        const tag = document.createElement("span");
        tag.style.cssText = "color:#e0a458;font-size:11px;margin-left:5px";
        tag.textContent = `L${o.level}`;
        n.appendChild(tag);
      }
      const e = document.createElement("span");
      e.className = "e";
      e.textContent = o.en || "（空）";
      const inp = document.createElement("input");
      inp.className = "av3-w";
      inp.type = "number";
      inp.min = "0";
      inp.max = "3";
      inp.step = "0.25";
      inp.value = cur[o.en] !== undefined ? String(cur[o.en]) : "1";
      // v4.2.3：权重 ≠ 1.0 时文字变色 —— 一眼看出"这套细则动过谁"。
      // 三处都要刷：快捷按钮 / 手输 / 底部批量按钮。
      const paint = () => {
        const v = inp.value === "" ? 1 : Number(inp.value);
        const on = Number.isFinite(v) && Math.abs(v - 1) > 1e-9;
        n.classList.toggle("av3-tuned", on);
        e.classList.toggle("av3-tuned", on);
        inp.classList.toggle("av3-tuned", on);
      };
      const setTo = (v) => {
        inp.value = String(v);
        setWeight(cur, o.en, v);
        paint();
        if (Number(v) >= 3) dropOtherForced(o.en);
      };
      inp.oninput = () => {
        setWeight(cur, o.en, inp.value);
        paint();
        if (Number(inp.value) >= 3) dropOtherForced(o.en);
      };
      const quick = document.createElement("span");
      quick.style.cssText = "display:flex;gap:3px";
      for (const v of [0, 1, 2, 3]) {
        const qb = document.createElement("button");
        qb.className = "av3-t";
        qb.textContent = String(v);
        qb.title = v === 0 ? "禁用该项" : `权重 ${v}`;
        qb.onclick = () => setTo(v);
        quick.appendChild(qb);
      }
      row.appendChild(n);
      row.appendChild(e);
      row.appendChild(inp);
      row.appendChild(quick);
      bd.appendChild(row);
      paint();
      inputs.push({ en: o.en, zh: o.zh, inp, paint });
    }

    const ft = document.createElement("div");
    ft.className = "ft";
    const mk = (text, fn) => {
      const b = document.createElement("button");
      b.className = "av3-btn";
      b.textContent = text;
      b.onclick = fn;
      return b;
    };
    ft.appendChild(mk("全部重置为 1.0", () => {
      for (const k of Object.keys(cur)) delete cur[k];
      for (const it of inputs) {
        it.inp.value = "1";
        it.paint();
      }
    }));
    ft.appendChild(mk("全部禁用（0）", () => {
      for (const it of inputs) {
        cur[it.en] = 0;
        it.inp.value = "0";
        it.paint();
      }
    }));
    ft.appendChild(mk("加倍（2.0）", () => {
      for (const it of inputs) {
        cur[it.en] = 2;
        it.inp.value = "2";
        it.paint();
      }
    }));
    const apply = mk("应用", () => {
      const clean = {};
      for (const [k, v] of Object.entries(cur)) {
        const f = Number(v);
        if (Number.isFinite(f) && Math.abs(f - 1) > 1e-9) clean[k] = f;
      }
      if (Object.keys(clean).length) ui.weights.options[field.key] = clean;
      else delete ui.weights.options[field.key];
      card.remove();
      refreshSectionState();
      persist();
      if (badgeBtn) badgeBtn.blur();
    });
    apply.classList.add("pri");
    ft.appendChild(apply);
    box.appendChild(hintEl);   // v5.0.1：互斥降级提示
    box.appendChild(ft);

    card.onclick = (e) => {
      if (e.target === card) card.remove();
    };
    document.body.appendChild(card);
  }

  /* ── 右列：设置 + 结果 ── */
  const counter = document.createElement("div");
  counter.className = "av3-meta";

  function refreshCounter() {
    const s = Object.keys(ui.weights.sections).length;
    const f = Object.keys(ui.weights.fields).length;
    const o = Object.values(ui.weights.options).reduce((n, x) => n + Object.keys(x).length, 0);
    counter.innerHTML = `已调权重：段 <b>${s}</b> · 字段 <b>${f}</b> · 选项 <b>${o}</b>`;
  }

  /* ── v6.1.0：右侧「已套用」卡片（用户 2026-11-07）─────────────────
     用户：「UI 界面右边增加显示已选择的服装、已选择的场景，支持一键取消套用」。
     显示三类**不在节点控件里**的覆盖，各带一个 ✕：
       · 服装库启用套 —— 服务端全局态（presets/outfit_library.json 的 meta.active）
       · 角色服装预设 —— presets/character_outfits.json 里已选角色 enabled 的那套
       · 场景搭配    —— 节点 weights_json 里 n_canon 被写死的那条（appliedSceneId）
     为什么要这块：这三项都是"面板关掉就看不见"的状态，出图莫名其妙不随机时
     最常就是它们压着，以前只能靠翻 JSON 排查。 */
  let appliedBox = null;

  function appliedSceneObj() {
    const id = appliedSceneId();
    if (!id) return null;
    const list = (ui.scenes && ui.scenes.scenes) || [];
    return list.find((s) => s.id === id) || { id, zh: id, level: 0 };
  }

  function activeLibOutfit() {
    const lib = ui.outfitLib;
    if (!lib) return null;
    /* ⚠️ v6.1.1 BUG 修复（用户 2026-11-07 02:20「右栏已套用只列出了场景，没有列出服装」）：
       `GET /outfit-library` 把 active 放在**顶层**（schema / updated_at / active /
       overrides / scene_override / categories / count / outfits），不是 meta.active ——
       按 meta.active 读永远拿到 undefined，服装库那行就永远不出现。
       这里三种形态都兜：顶层 active、meta.active、_meta.active。 */
    const raw = (lib.active !== undefined)
      ? lib.active
      : ((lib.meta || lib._meta || {}).active);
    const id = String(raw || "");
    if (!id) return null;
    return (lib.outfits || []).find((o) => o.id === id) || { id, zh: id };
  }

  function activeRoleOutfits() {
    const g = ui.outfits || {};
    const out = [];
    for (const t of selectedList()) {
      const role = String(t || "").toLowerCase();
      const rec = g[role];
      if (!role || !rec || !rec.enabled) continue;
      const item = (rec.list || []).find((o) => o.id === rec.enabled);
      out.push({ role, id: rec.enabled, zh: (item && item.zh) || rec.enabled });
    }
    return out;
  }

  function renderAppliedInto(box) {
    box.innerHTML = "<h4>已套用</h4>";
    const sc = appliedSceneObj();
    const lo = activeLibOutfit();
    const ro = activeRoleOutfits();

    if (!sc && !lo && !ro.length) {
      const e = document.createElement("div");
      e.className = "av3-meta";
      e.id = "av3-applied-empty";
      e.textContent = "当前没有固定覆盖 —— 服装与场景都走随机。";
      box.appendChild(e);
    } else {
      const mkRow = (icon, label, text, clearFn, clearTitle) => {
        const row = document.createElement("div");
        row.className = "av3-applied-row";
        const t = document.createElement("span");
        t.className = "tx";
        t.appendChild(document.createTextNode(icon + " "));
        const b = document.createElement("b");
        b.textContent = label;          // 角色 tag / 场景名一律走 textContent，不拼 HTML
        t.appendChild(b);
        t.appendChild(document.createTextNode(" · " + text));
        row.appendChild(t);
        const x = document.createElement("button");
        x.className = "av3-btn av3-applied-x";
        x.textContent = "✕";
        x.title = clearTitle;
        x.onclick = async () => { await clearFn(); refreshApplied(); };
        row.appendChild(x);
        box.appendChild(row);
      };
      if (lo) {
        mkRow("👗", "服装库", (lo.zh || lo.id),
          async () => {
            const d = await outfitLibEnable("");
            if (d && d.library) ui.outfitLib = d.library;
            if (ui.tab === "outfits") renderOutfitLibrary();
            setStatus(`👗 已停用服装库「${lo.zh || lo.id}」：服装回到随机`, "ok");
          },
          "停用服装库这套（服装回到词库随机）");
      }
      for (const r of ro) {
        mkRow("🧍", "角色预设", `${r.role} → ${r.zh}`,
          async () => {
            const d = await outfitOp(r.role, "enable", { id: "" });
            if (d && d.outfits) ui.outfits = d.outfits;
            setStatus(`👗 已取消「${r.role}」的服装预设（这套预设仍保留）`, "ok");
          },
          "取消这个角色的服装预设（预设本身保留）");
      }
      if (sc) {
        mkRow("🎬", "场景", (sc.zh || sc.id) + (sc.level > 0 ? `  L${sc.level}` : ""),
          async () => { clearScene(); },
          "取消场景套用（被固定的字段全部恢复）");
      }
    }
    const hint = document.createElement("div");
    hint.className = "av3-meta";
    hint.style.cssText = "margin-top:5px;font-size:11px;opacity:.7";
    hint.textContent = "这三项是服务端/节点里的固定覆盖，与「随机」互斥。";
    box.appendChild(hint);
  }

  /** 只重画「已套用」卡片，不重建整个右栏（避免输入框失焦、状态条被清）。 */
  function refreshApplied() {
    if (appliedBox && appliedBox.isConnected) renderAppliedInto(appliedBox);
  }

  function renderRight() {
    right.innerHTML = "";

    /* 结果 */
    const outBox = document.createElement("div");
    outBox.className = "av3-box";
    outBox.innerHTML = "<h4>生成结果</h4>";
    const outEl = document.createElement("div");
    outEl.className = "av3-out";
    outEl.textContent = "点下方「全部随机」抽一条。（题外：面板与工作流共用同一套引擎，"
      + "所以「采用这条」之后跑工作流，出来的就是这一条。）";
    outBox.appendChild(outEl);
    const metaEl = document.createElement("div");
    metaEl.className = "av3-meta";
    metaEl.style.marginTop = "7px";
    outBox.appendChild(metaEl);
    const sumEl = document.createElement("div");
    sumEl.className = "av3-sum";
    sumEl.style.marginTop = "6px";
    outBox.appendChild(sumEl);
    const scEl = document.createElement("div");
    scEl.className = "av3-sc";
    scEl.style.marginTop = "7px";
    outBox.appendChild(scEl);
    right.appendChild(outBox);

    /* 动作 */
    const actBox = document.createElement("div");
    actBox.className = "av3-box";
    actBox.innerHTML = "<h4>动作</h4>";
    const actRow = document.createElement("div");
    actRow.style.cssText = "display:flex;gap:7px;flex-wrap:wrap";
    const mkBtn = (text, cls, fn) => {
      const b = document.createElement("button");
      b.className = "av3-btn" + (cls ? " " + cls : "");
      b.textContent = text;
      b.onclick = fn;
      return b;
    };
    const rollBtn = mkBtn("🎲 全部随机（预演）", "pri", () => runRoll("all"));
    const seedBtn = mkBtn("🔁 换种子", "", () => runRoll("all"));
    const clearBtn = mkBtn("🧹 清空权重", "", () => {
      ui.weights = { sections: {}, fields: {}, options: {} };
      renderTree();
      persist();
      setStatus("已清空全部权重", "");
    });
    const adoptBtn = mkBtn("✅ 采用这条（写回 seed）", "", () => {
      if (ui.lastSeed === null) {
        setStatus("先点一次「全部随机」再采用", "no");
        return;
      }
      wset(node, "nsfw_level", ui.level);
      wset(node, "seed", ui.lastSeed);
      wset(node, "tag_cap", capSel.value);
      // ⚠️ 空栏不写回：Number("") === 0 会把基准概率静默清零，
      //    而 0 意味着"所有可选字段都不抽" —— 这是 v3.1.0 的显示 0 那个 bug。
      if (probIn.value.trim() !== "") {
        const pv = Number(probIn.value);
        if (Number.isFinite(pv)) wset(node, "base_prob", pv);
      }
      wset(node, "force_role", roleIn.value);
      wset(node, "extra_body", extraIn.value);
      wset(node, "appearance_follow", apeChk.checked);
      wset(node, "hair_follow", wget(node, "hair_follow", true) !== false);
      wset(node, "disabled_sections", disabledTxt());
      saveWeights(node, ui.weights);
      app.graph.setDirtyCanvas(true, true);
      setStatus(`已写回节点：档位 ${LEVEL_SHORT[ui.level]}，seed ${ui.lastSeed}`, "ok");
    });
    actRow.appendChild(rollBtn);
    actRow.appendChild(seedBtn);
    actRow.appendChild(adoptBtn);
    actRow.appendChild(clearBtn);
    actBox.appendChild(actRow);
    statEl = document.createElement("div");
    statEl.className = "av3-meta";
    statEl.id = "av3-status";   // 冒烟测试要读状态条文案（v3.18.0 加 id）
    statEl.style.marginTop = "6px";
    actBox.appendChild(statEl);
    right.appendChild(actBox);

    /* v6.1.0：已套用（服装库 / 角色服装预设 / 场景）—— 每项带 ✕ 一键取消 */
    appliedBox = document.createElement("div");
    appliedBox.className = "av3-box";
    appliedBox.id = "av3-applied";
    renderAppliedInto(appliedBox);
    right.appendChild(appliedBox);
    // 这两份数据都是懒加载：还没拉过就先拉一次，回来再补画（不阻塞右栏）
    if (!ui.outfitLib && !ui.outfitLibLoading) loadOutfitLibrary();
    if (!ui.outfits) loadOutfits().then(() => refreshApplied());

    /* 设置 */
    const cfgBox = document.createElement("div");
    cfgBox.className = "av3-box";
    cfgBox.innerHTML = "<h4>设置</h4>";

    const mkRow = (label, el) => {
      const d = document.createElement("div");
      d.className = "av3-fld";
      const l = document.createElement("label");
      l.textContent = label;
      d.appendChild(l);
      d.appendChild(el);
      cfgBox.appendChild(d);
      return el;
    };

    const capSel = document.createElement("select");
    for (const c of contract.tag_cap_choices) {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c === "自动" ? "自动（按人数）" : c;
      capSel.appendChild(o);
    }
    capSel.value = String(lsGet(node, "tag_cap", "自动"));
    capSel.onchange = () => {
      wset(node, "tag_cap", capSel.value);
      app.graph.setDirtyCanvas(true, true);
    };
    mkRow("标签数上限", capSel);

    const probIn = document.createElement("input");
    probIn.type = "number";
    probIn.min = "0";
    probIn.max = "1";
    probIn.step = "0.05";
    // ⚠️ 读回时兜底。控件值可能是空的 / 非数字 / 历史脏数据 0：
    //    v3.1.0 的「采用这条」在这一栏为空时写了 Number("") === 0，
    //    而 base_prob = 0 意味着"所有可选字段都不抽"，出图只剩必填项 ——
    //    用户看到的就是"随机坏了"。这里把它当"没设置"，回到默认值。
    const probSaved = Number(lsGet(node, "base_prob", NaN));
    probIn.value = String(Number.isFinite(probSaved) && probSaved > 0
      ? probSaved : contract.weights.base_prob_default);
    probIn.oninput = () => {
      // 空串不写回：Number("") === 0 会静默把基准概率清零
      if (probIn.value.trim() === "") return;
      const v = Number(probIn.value);
      if (Number.isFinite(v)) wset(node, "base_prob", Math.max(0, Math.min(1, v)));
    };
    probIn.onblur = () => {
      // 失焦时把空栏补回默认值，避免留下一个"看着像 0"的空框
      if (probIn.value.trim() === "") {
        probIn.value = String(contract.weights.base_prob_default);
        wset(node, "base_prob", contract.weights.base_prob_default);
      }
    };
    probIn.onchange = () => app.graph.setDirtyCanvas(true, true);
    mkRow("基准抽取概率", probIn);

    const roleIn = document.createElement("input");
    roleIn.type = "text";
    roleIn.placeholder = "留空 = 随机角色；填 danbooru 角色 tag";
    roleIn.id = "av3-rolein";   // v3.18.0：套用腿/脚/手特写时要清空并恢复它
    roleIn.value = String(lsGet(node, "force_role", ""));
    roleIn.oninput = () => wset(node, "force_role", roleIn.value);
    mkRow("指定角色", roleIn);

    const apeChk = document.createElement("input");
    apeChk.type = "checkbox";
    apeChk.style.flex = "0 0 auto";
    apeChk.checked = lsGet(node, "appearance_follow", true) !== false;
    apeChk.title = "开启时，抽到角色会用官方发色/瞳色覆盖随机结果";
    apeChk.onchange = () => {
      wset(node, "appearance_follow", apeChk.checked);
      app.graph.setDirtyCanvas(true, true);
    };
    const apeWrap = document.createElement("span");
    apeWrap.style.cssText = "flex:1.6;display:flex;align-items:center;gap:6px;font-size:12px";
    apeWrap.appendChild(apeChk);
    const apeTxt = document.createElement("span");
    apeTxt.textContent = "按角色官方外貌对齐（会覆盖发色/瞳色的权重）";
    apeWrap.appendChild(apeTxt);
    mkRow("外貌对齐", apeWrap);

    // v3.47.0：发型选择器 —— 4 个下拉接管原来的 hair_follow 复选。
    // 房规：控件只许**追加**在 INPUT_TYPES 末尾，hair_follow 的控件位保留
    // （老工作流的 widgets_values 不会错位），只是界面不再渲染它。
    // 每项三档语义：角色标签发型（取角色标签里的官方发型）/ 随机 / 具体款式。
    const HAIR_PICK_SLOTS = [
      ["hair_len_pick", "发型长度"],
      ["hair_tie_pick", "束发方式"],
      ["hair_curl_pick", "卷发质地"],
      ["hair_acc_pick", "发饰"],
    ];
    for (const [hkey, hlabel] of HAIR_PICK_SLOTS) {
      const hw = (node.widgets || []).find((x) => x.name === hkey);
      const hraw = hw && hw.options && hw.options.values;
      const hvals = Array.isArray(hraw)
        ? hraw
        : (hraw && typeof hraw === "object" ? Object.keys(hraw) : []);
      const hsel = document.createElement("select");
      hsel.id = "av3-" + hkey;
      hsel.style.cssText = "flex:1.6;font-size:12px";
      hsel.title = "「角色标签发型」= 取该角色标签里的官方发型（等同旧版「按角色官方发型对齐」）；\n「随机」= 这一项交给随机；\n其它 = 固定成该款式。";
      for (const hv of hvals) {
        const hop = document.createElement("option");
        hop.value = hv;
        hop.textContent = hv === "角色标签发型" ? "角色标签发型（默认）" : hv;
        hsel.appendChild(hop);
      }
      hsel.value = (hw && hw.value) || "角色标签发型";
      hsel.onchange = () => {
        wset(node, hkey, hsel.value);
        app.graph.setDirtyCanvas(true, true);
      };
      mkRow(hlabel, hsel);
    }

    const extraIn = document.createElement("input");
    extraIn.type = "text";
    extraIn.placeholder = "手写补句，拼在末尾";
    extraIn.value = String(lsGet(node, "extra_body", ""));
    extraIn.oninput = () => wset(node, "extra_body", extraIn.value);
    mkRow("手写补句", extraIn);

    cfgBox.appendChild(counter);
    const secTip = document.createElement("div");
    secTip.className = "av3-note";
    secTip.textContent = "关闭的段会写进节点的 disabled_sections（段开关只有面板这一个入口，节点上不重复外露）。";
    cfgBox.appendChild(secTip);
    right.appendChild(cfgBox);

    /* ── 预设：把当前这一整套配置存下来，随时一键恢复 ─────────────
       存的是面板上的这一套状态：档位 + 三层权重 + 段开关 + 已选角色
       + 标签上限 + 基准概率 + 外貌对齐 + 手写补句 + 手填角色。

       ⚠️ **不含 seed**。seed 代表"复现某一条预览"，是一次性的；
          把它存进预设会让"加载预设"变成"每次都出同一张"，
          那不是这个功能该有的语义。
    */
    const preBox = document.createElement("div");
    preBox.className = "av3-box";
    preBox.innerHTML = "<h4>预设</h4>";

    const preRow1 = document.createElement("div");
    preRow1.className = "av3-preset";
    const preSel = document.createElement("select");
    const preApply = mkBtn("📥 应用");
    const preDel = mkBtn("🗑 删除");
    preRow1.appendChild(preSel);
    preRow1.appendChild(preApply);
    preRow1.appendChild(preDel);
    preBox.appendChild(preRow1);

    const preRow2 = document.createElement("div");
    preRow2.className = "av3-preset";
    const preName = document.createElement("input");
    preName.type = "text";
    preName.placeholder = "新预设名称（同名会覆盖）";
    preName.maxLength = 40;
    const preSave = mkBtn("💾 保存当前配置", "pri");
    preRow2.appendChild(preName);
    preRow2.appendChild(preSave);
    preBox.appendChild(preRow2);

    const preTip = document.createElement("div");
    preTip.className = "av3-meta";
    preTip.textContent = "预设存在插件目录的 presets/user_presets.json —— 可备份、可随插件分享给别人。";
    preBox.appendChild(preTip);
    right.appendChild(preBox);

    // 重渲之后要能恢复"当前选中的预设"，所以记在 ui 上而不是 DOM 里
    if (ui.presetCur === undefined) ui.presetCur = "";

    /**
     * 预设摘要 —— **前端自己拼**，不用后端返回的 summary。
     *
     * 后端存/回的是档位 key（`hardcore`），而界面上要显示「3 过激」；
     * 档位标签是前端的概念（LEVEL_SHORT），后端不该知道。
     * 后端的 summary 留着给非前端消费者用，这里不走它。
     */
    function presetSummary(p) {
      const d = p.data || {};
      const bits = [LEVEL_SHORT[d.level] || d.level || "—"];
      const n = String(d.selected_roles || "").split("|")
        .filter((t) => t.trim()).length;
      if (n) bits.push(`${n} 位角色`);
      const w = d.weights || {};
      const wn = Object.keys(w.sections || {}).length
        + Object.keys(w.fields || {}).length
        + Object.values(w.options || {})
            .reduce((a, x) => a + Object.keys(x || {}).length, 0);
      if (wn) bits.push(`权重 ${wn} 项`);
      if (String(d.force_role || "").trim()) bits.push("手填角色");
      // v4.1.0：不指定角色 —— 预设摘要里要能一眼看出"这套是不出角色的"
      if (d.role_pick === false) bits.push("不抽角色");
      return bits.join(" · ");
    }

    function paintPresets() {
      preSel.innerHTML = "";
      if (!ui.presets.length) {
        const o = document.createElement("option");
        o.value = "";
        o.textContent = "（还没有预设）";
        preSel.appendChild(o);
        preSel.disabled = true;
        preApply.disabled = true;
        preDel.disabled = true;
        return;
      }
      preSel.disabled = false;
      preApply.disabled = false;
      preDel.disabled = false;
      for (const p of ui.presets) {
        const o = document.createElement("option");
        o.value = p.name;
        o.textContent = `${p.name} —— ${presetSummary(p)}`;
        o.title = `保存于 ${p.saved_at}`;
        preSel.appendChild(o);
      }
      if (ui.presetCur && ui.presets.some((p) => p.name === ui.presetCur)) {
        preSel.value = ui.presetCur;
      } else {
        ui.presetCur = preSel.value;
      }
    }

    function loadPresets() {
      const seq = ++presetsSeq;
      return api.fetchApi(`/${NS}/presets`, { cache: "no-store" })
        .then((r) => r.json())
        .then((d) => {
          // 期间发生过写操作 → 这份快照已经过期，丢掉。
          // 见 presetsSeq 的注释：不然会把删掉的预设又变回来。
          if (seq !== presetsSeq) return;
          ui.presets = d.presets || [];
          paintPresets();
        })
        .catch((e) => setStatus("预设列表加载失败：" + e.message, "no"));
    }

    /** 收集面板当前这一整套配置。 */
    function collectConfig() {
      const bpRaw = probIn.value.trim();
      const bp = bpRaw === "" ? NaN : Number(bpRaw);
      return {
        level: ui.level,
        weights: JSON.parse(JSON.stringify(ui.weights)),
        selected_roles: String(lsGet(node, "selected_roles", "") || ""),
        // v3.10.0：随机池（限定作品 IP）也跟着预设走
        role_works: String(lsGet(node, "role_works", "") || ""),
        // v3.38.0：仅收藏内随机，与随机池正交，一并存进预设
        role_fav_only: !!lsGet(node, "role_fav_only", false),
        tag_cap: String(capSel.value || "自动"),
        base_prob: Number.isFinite(bp) && bp > 0
          ? bp : contract.weights.base_prob_default,
        appearance_follow: !!apeChk.checked,
        // v3.45.0：角色官方发型对齐（老预设无此字段 → 读取时回落到开）
        hair_follow: true,
        hair_len_pick: wget(node, "hair_len_pick"),
        hair_tie_pick: wget(node, "hair_tie_pick"),
        hair_curl_pick: wget(node, "hair_curl_pick"),
        hair_acc_pick: wget(node, "hair_acc_pick"),
        extra_body: String(extraIn.value || ""),
        force_role: String(roleIn.value || ""),
        // v3.17.0：胸部大小等级取代巨乳开关（预设里也跟着存新字段）
        breast_size: String(bsSel.value || "不指定"),
        // v3.19.0：服装风格智配（老预设无此字段 → 读取时回落到「随机」）
        outfit_style: String(osSel.value || "随机"),
        // v4.1.0：不指定角色（老预设无此字段 → 读取时回落到"抽角色"）
        role_pick: lsGet(node, "role_pick", true) !== false,
        // v5.0.1：场景随机抽取（老预设无此字段 → 读取时回落到不抽）
        scene_pick: !!wget(node, "scene_pick", false),
        // v6.5.0：干爽过滤随预设一起存 / 恢复（老预设没这两个键 → 不动现值）
        dry_skin_filter: !!wget(node, "dry_skin_filter", false),
        dry_skin_groups: String(wget(node, "dry_skin_groups", "") || ""),
        dry_skin_tags: String(wget(node, "dry_skin_tags", "") || ""),
        // v6.6.0：双女自动补位（老预设没这个键 → 读取时回落到"开"）
        duo_role_fill: wget(node, "duo_role_fill", true) !== false,
        // v6.7.0：勾选池自动随机随预设存 / 恢复（老预设没键 → 不动现值）
        pick_pool_mode: String(wget(node, "pick_pool_mode", "") || ""),
        pick_pool_ids: String(wget(node, "pick_pool_ids", "") || ""),
        // 兼容保留：老版本预设读取方还能认得 big_breasts（新值为 false 语义）
        big_breasts: false,
      };
    }

    /** 把一份预设写回面板与节点。 */
    function applyConfig(data) {
      if (!data || typeof data !== "object") return;
      if (data.level && contract.levels.some((l) => l.key === data.level)) {
        ui.level = String(data.level);
      }
      const w = data.weights;
      if (w && typeof w === "object") {
        // 整表替换而不是合并 —— 预设的语义就是"这一整套配置"
        ui.weights = {
          sections: { ...(w.sections || {}) },
          fields: { ...(w.fields || {}) },
          options: { ...(w.options || {}) },
        };
      }
      if (data.selected_roles !== undefined) {
        wset(node, "selected_roles", String(data.selected_roles || ""));
      }
      if (data.role_works !== undefined) {
        wset(node, "role_works", String(data.role_works || ""));
      }
      if (data.role_fav_only !== undefined) {
        wset(node, "role_fav_only", !!data.role_fav_only);
      }
      // v4.1.0：不指定角色（老预设没有这个键 → 不动现值，保持向后兼容）
      if (data.role_pick !== undefined) {
        wset(node, "role_pick", !!data.role_pick);
      }
      // v5.0.1：场景随机抽取 —— 老预设没有这个字段，不动现值（不强行关掉）
      if (data.scene_pick !== undefined) {
        wset(node, "scene_pick", !!data.scene_pick);
      }
      // v6.5.0：干爽过滤 —— 老预设没有这两个键就不动现值（向后兼容）
      if (data.dry_skin_filter !== undefined) {
        wset(node, "dry_skin_filter", !!data.dry_skin_filter);
      }
      if (data.dry_skin_groups !== undefined) {
        wset(node, "dry_skin_groups", String(data.dry_skin_groups || ""));
      }
      if (data.dry_skin_tags !== undefined) {
        wset(node, "dry_skin_tags", String(data.dry_skin_tags || ""));
      }
      // v6.6.0：双女自动补位 —— 老预设没有这个键就不动现值（向后兼容）
      if (data.duo_role_fill !== undefined) {
        wset(node, "duo_role_fill", !!data.duo_role_fill);
      }
      // v6.7.0：勾选池自动随机（老预设没有这两个键 → 不动现值）
      if (data.pick_pool_mode !== undefined) {
        wset(node, "pick_pool_mode", String(data.pick_pool_mode || ""));
      }
      if (data.pick_pool_ids !== undefined) {
        wset(node, "pick_pool_ids", String(data.pick_pool_ids || ""));
      }
      if (data.tag_cap !== undefined) {
        wset(node, "tag_cap", String(data.tag_cap || "自动"));
      }
      if (data.base_prob !== undefined) {
        const bp = Number(data.base_prob);
        wset(node, "base_prob",
             Number.isFinite(bp) && bp > 0 ? bp : contract.weights.base_prob_default);
      }
      if (data.appearance_follow !== undefined) {
        wset(node, "appearance_follow", !!data.appearance_follow);
      }
      for (const _hk of ["hair_len_pick", "hair_tie_pick",
                         "hair_curl_pick", "hair_acc_pick"]) {
        if (data[_hk] !== undefined) wset(node, _hk, data[_hk]);
      }
      if (data.hair_follow !== undefined) {
        wset(node, "hair_follow", !!data.hair_follow);
      }
      if (data.extra_body !== undefined) {
        wset(node, "extra_body", String(data.extra_body || ""));
      }
      if (data.force_role !== undefined) {
        wset(node, "force_role", String(data.force_role || ""));
      }
      // v3.17.0：优先读新字段 breast_size；老预设只有 big_breasts →
      // 开着就映射为「巨乳」（与后端兼容逻辑一致）。两者都没有 → 不动现值。
      if (data.outfit_style !== undefined) {
        const os = String(data.outfit_style || "随机");
        const valid = OS_CHOICES.includes(os) ? os : "随机";
        wset(node, "outfit_style", valid);
        osSel.value = valid;
      }
      if (data.breast_size !== undefined) {
        const bs = String(data.breast_size || "不指定");
        wset(node, "breast_size", bs);
        bsSel.value = bs;
      } else if (data.big_breasts !== undefined) {
        const bs = data.big_breasts ? "巨乳" : "不指定";
        wset(node, "breast_size", bs);
        bsSel.value = bs;
        wset(node, "big_breasts", false); // 旧开关退役：语义已并入 breast_size
      }
      wset(node, "nsfw_level", ui.level);
      saveWeights(node, ui.weights);
      wset(node, "disabled_sections", disabledTxt());
      app.graph.setDirtyCanvas(true, true);
      // 设置区的控件值都是从节点读的，索性整块重画一次
      // （renderRight 末尾会 syncLevel → syncTabs，左列也跟着刷新）
      renderRight();
    }

    preSel.onchange = () => {
      ui.presetCur = preSel.value;
    };

    preSave.onclick = async () => {
      const name = preName.value.trim();
      if (!name) {
        setStatus("先给预设起个名字", "no");
        if (preName.focus) preName.focus();
        return;
      }
      try {
        const r = await api.fetchApi(`/${NS}/presets/save`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, data: collectConfig() }),
        });
        const d = await r.json();
        if (d.error) {
          setStatus("保存失败：" + d.error, "no");
          return;
        }
        ui.presets = d.presets || [];
        presetsSeq += 1;      // 作废所有在途的 GET（它会覆盖成旧快照）
        ui.presetCur = name;
        paintPresets();
        preName.value = "";
        setStatus(`已保存预设「${name}」`, "ok");
      } catch (e) {
        setStatus("保存失败：" + e.message, "no");
      }
    };

    preApply.onclick = () => {
      const p = ui.presets.find((x) => x.name === preSel.value);
      if (!p) {
        setStatus("先选一个预设", "no");
        return;
      }
      ui.presetCur = p.name;
      applyConfig(p.data);
      setStatus(`已应用预设「${p.name}」`, "ok");
    };

    // 删除用"再点一次确认"而不是 window.confirm ——
    // confirm 在 jsdom 里没实现（会抛异常），测试没法跑；
    // 而且原生的 confirm 在 ComfyUI 里样式也很突兀。
    let delArmed = false;
    let delTimer = null;
    preDel.onclick = async () => {
      const name = preSel.value;
      if (!name) return;
      if (!delArmed) {
        delArmed = true;
        preDel.textContent = "⚠ 再点一次";
        delTimer = setTimeout(() => {
          delArmed = false;
          preDel.textContent = "🗑 删除";
        }, 4000);
        return;
      }
      if (delTimer) clearTimeout(delTimer);
      delArmed = false;
      preDel.textContent = "🗑 删除";
      try {
        const r = await api.fetchApi(`/${NS}/presets/delete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        const d = await r.json();
        ui.presets = d.presets || [];
        presetsSeq += 1;      // 作废所有在途的 GET（它会覆盖成旧快照）
        if (ui.presetCur === name) ui.presetCur = "";
        paintPresets();
        setStatus(`已删除预设「${name}」`, "ok");
      } catch (e) {
        setStatus("删除失败：" + e.message, "no");
      }
    };

    paintPresets();
    loadPresets();

    /* 抽一次（v3.11.0 抽出）：runRoll 与「👁 预览」共用同一条请求体，
       唯一区别是 weights_json 传谁 —— 预览传的是**临时权重**，不落盘。 */
    async function rollOnce(weightsJson, over) {
      // over：临时覆盖（v3.18.0 特写预览用 —— 腿/脚/手套用时会清角色，
      // 预览必须跟着清，否则看到的是"套用后不会出现"的构图）
      const ov = over || {};
      const r = await api.fetchApi(`/${NS}/roll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          section: "all",
          seed: Math.floor(Math.random() * 2147483000),
          nsfw_level: ui.level,
          tag_cap: capSel.value,
          base_prob: Number(probIn.value),
          weights_json: weightsJson,
          disabled_sections: ov.disabled_sections ?? disabledTxt(),
          force_role: ov.force_role ?? roleIn.value,
          // ⚠️ v3.8.0 修：这两个之前**没发**，于是"预演 ≠ 实跑"——
          //    选了角色点「全部随机」时预览是全库随机抽的，
          //    而真正跑工作流是按 selected_roles 走，两边对不上。
          selected_roles: ov.selected_roles
            ?? String(lsGet(node, "selected_roles", "") || ""),
          lock_fields: ov.lock_fields ?? String(lsGet(node, "lock_fields", "") || ""),
          // v3.10.0：随机池（限定作品 IP）—— 也必须发，否则预演里
          // "每次自动换"的效果看不到（预演会退回全库随机）
          role_works: ov.role_works ?? String(lsGet(node, "role_works", "") || ""),
          extra_body: extraIn.value,
          appearance_follow: apeChk.checked,
          // v3.47.0：发型选择器（预演必须与实跑同参，否则又是"预演 ≠ 实跑"）
          hair_follow: wget(node, "hair_follow", true) !== false,
          hair_len_pick: wget(node, "hair_len_pick", "角色标签发型"),
          hair_tie_pick: wget(node, "hair_tie_pick", "角色标签发型"),
          hair_curl_pick: wget(node, "hair_curl_pick", "角色标签发型"),
          hair_acc_pick: wget(node, "hair_acc_pick", "角色标签发型"),
          // v3.17.0：胸部大小等级（预演必须与实跑同参，否则又回到
          // "预演 ≠ 实跑" 的老坑）；后端对 "不指定" 不干预。
          breast_size: bsSel.value,
          // v3.19.0：服装风格智配（同一条理由 —— 预演必须与实跑同参）
          outfit_style: osSel.value,
          // v3.38.0：仅收藏内随机（同上；不给就又是"面板抽的和实跑的不一样"）
          role_fav_only: !!lsGet(node, "role_fav_only", false),
          // v4.1.0：不指定角色（同上 —— 不发的后果就是"预演没角色、实跑有角色"）
          role_pick: lsGet(node, "role_pick", true) !== false,
          // v5.0.1：场景随机抽取（不发的后果同上：预演不出场景、实跑出）
          scene_pick: !!wget(node, "scene_pick", false),
          // v6.5.0：干爽过滤（预演必须与实跑同参，否则面板看到的是没过滤的）
          dry_skin_filter: !!wget(node, "dry_skin_filter", false),
          dry_skin_groups: String(wget(node, "dry_skin_groups", "") || ""),
          // v6.5.2：逐条模式，预演必须与实跑同参
          dry_skin_tags: String(wget(node, "dry_skin_tags", "") || ""),
          // v6.6.0：双女自动补位 —— 不发的后果就是"预演两个不同的人、
          // 实跑两个一样的人"，最容易被误判成"补位坏了"
          duo_role_fill: lsGet(node, "duo_role_fill", true) !== false,
          // v6.7.0：勾选池自动随机 —— 预演必须与实跑同参，否则面板
          // "预览"永远停在上一次手动套用的那条
          pick_pool_mode: String(wget(node, "pick_pool_mode", "") || ""),
          pick_pool_ids: String(wget(node, "pick_pool_ids", "") || ""),
        }),
      });
      return r.json();
    }

    /* 👁 非破坏性预览（v3.11.0）：**只看不套用**。
       构造「当前权重 + n_canon 强制本场景」的临时表发给引擎，
       全程不碰 ui.weights / 不 persist —— 预览多少次都不会改变你的设置。 */
    async function previewScene(sc) {
      const all = nCanonOptions();
      const w = JSON.parse(JSON.stringify(ui.weights || {}));
      w.sections = w.sections || {};
      w.fields = w.fields || {};
      w.options = w.options || {};
      w.fields.n_canon = 3.0;
      const bag = {};
      for (const en of all) bag[en] = 0;
      bag[sc.id] = 3.0;
      w.options.n_canon = bag;
      // 段权重不能是 0（否则引擎把 canon 段当关闭，预设根本不生效）
      if (Number(w.sections.canon ?? 1) === 0) w.sections.canon = 1;
      // 预览要能代表"套用后长什么样" —— 与 applyScene 共用 applySlimTo，
      // 否则看到的是"套用后不会出现"的构图（老坑：预览 ≠ 实跑）。
      const prof = nearSlim(sc);
      if (prof) {
        applySlimTo(w, prof, sc);
        if (prof.dropRole) {
          const lk = lockList();
          return rollOnce(JSON.stringify(w), {
            force_role: "",
            selected_roles: "",
            role_works: "",
            lock_fields: lk.indexOf("role") < 0
              ? lk.concat("role").join(", ") : lk.join(", "),
          });
        }
      }
      return rollOnce(JSON.stringify(w));
    }

    async function runRoll(section) {
      setStatus("正在抽取…", "");
      const seed = Math.floor(Math.random() * 2147483000);
      try {
        // v3.11.0：请求体与「👁 预览」共用 rollOnce，这里传当前权重表
        const d = await rollOnce(JSON.stringify(ui.weights));
        if (d.error) {
          setStatus("抽取失败：" + d.error, "no");
          return;
        }
        ui.lastResult = d;
        ui.lastSeed = seed;
        outEl.textContent = d.text || "（空）";
        sumEl.textContent = d.cn_summary ? "中文摘要：" + d.cn_summary : "";
        const rep = d.report || {};
        const okAll = rep.ok_f === rep.total_f;
        scEl.innerHTML =
          `档位 <b>${d.level_label}</b>｜落档 <b>${rep.real_level}</b>｜标签 <b>${rep.n}</b>`
          + `｜自检 <span class="${okAll ? "av3-ok" : "av3-no"}">${rep.ok_f}/${rep.total_f}</span>`
          + `｜风格 <span class="${rep.ok_s === rep.total_s ? "av3-ok" : "av3-wa"}">${rep.ok_s}/${rep.total_s}</span>`;
        const extra = [];
        if ((d.dropped || []).length) extra.push("裁剪：" + d.dropped.join("、"));
        for (const n of d.notes || []) extra.push(n);
        if (extra.length) {
          const nd = document.createElement("div");
          nd.className = "av3-note";
          nd.textContent = extra.join(" ｜ ");
          scEl.appendChild(nd);
        }
        setStatus(`抽好了 · seed ${seed}（点「采用这条」写回节点即可复现）`, "ok");
      } catch (e) {
        setStatus("请求失败：" + e.message, "no");
      }
    }

    /* 档位切换要重渲左列（低档内容要标出来） */
    syncLevel();
    panelRoll = runRoll;          // 场景搭配页的「套用并预演」用
    panelPreview = previewScene;  // 场景搭配页的「👁 预览」（只看不套用）用
  }

  function levelNum(key) {
    const found = contract.levels.find((l) => l.key === key);
    return found ? found.num : 99;
  }

  renderRight();
  refreshCounter();
  syncTabs();
  restoreUiScroll();            // v3.25.0：按记忆回到上次的位置（懒加载页会自动重试）
  document.body.appendChild(mask);
}

/* ── 节点按钮 ─────────────────────────────────────────────────────── */
function addNodeButtons(node) {
  if (node.__av3Buttons) return;
  node.__av3Buttons = true;

  const mk = (text, tip, fn) => {
    const b = node.addWidget("button", text, null, fn);
    b.serialize = false; // 按钮不进工作流 JSON
    b.tooltip = tip;
    return b;
  };

  mk("⚙ 打开随机面板", "可视化选择 + 三层权重调节", () => openPanel(node));
  mk("🔁 换种子", "seed 设为 -1 之外的新随机值，下次排队重新抽", () => {
    wset(node, "seed", Math.floor(Math.random() * 2147483000));
    app.graph.setDirtyCanvas(true, true);
  });
  mk("🎲 每次重抽", "seed = -1：每次排队都重新随机", () => {
    wset(node, "seed", -1);
    app.graph.setDirtyCanvas(true, true);
  });
  mk("🧹 清空权重", "把三层权重表清空回默认", () => {
    wset(node, "weights_json", "");
    app.graph.setDirtyCanvas(true, true);
  });
}

app.registerExtension({
  name: "Anima.Wardrobe.V3.Studio",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!NODE_TYPES.has(nodeData.name)) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated ? onCreated.apply(this, arguments) : undefined;
      // v3.47.0：节点原生控件的显示名中文化。
      // 只改 `widget.label`，**不动控件名**（name 是工作流契约，改了老工作流全废）。
      const WZ = {
        breast_size: "胸部大小",
        outfit_style: "服装风格",
        role_fav_only: "只从收藏选角",
        role_pick: "不指定角色（只按词库权重抽特征）",
        scene_pick: "随机场景（按当前档位抽一条场景搭配）",
        hair_follow: "角色发型对齐（旧开关，已被下面四项取代）",
        hair_len_pick: "发型长度",
        hair_tie_pick: "束发方式",
        hair_curl_pick: "卷发质地",
        hair_acc_pick: "发饰",
      };
      // v3.47.1：退役控件只隐藏、**不删**（删了 widgets_values 会错位，老工作流全废）。
      // `computeSize = () => [0, -4]` 是社区通用的"零高度"写法：widget 还在、
      // 值照常序列化，只是画不出来；节点高度自动收缩。
      const HIDDEN = {
        big_breasts: true,   // 已被「胸部大小」下拉取代
        hair_follow: true,   // 已被下面 4 个发型下拉取代
        role_pick: true,     // v4.1.0：入口只有面板「角色库」页签那一个复选框
        scene_pick: true,    // v5.0.1：入口只有面板「🎬 场景搭配」页顶部那一个复选框
        // v6.5.0：入口只有面板「词库与权重」页顶部那一组勾选
        dry_skin_filter: true,
        dry_skin_groups: true,
        dry_skin_tags: true,   // v6.5.2：入口只有面板明细里的逐条复选框
        // v6.6.0：入口只有面板「角色库」页签那一个复选框
        duo_role_fill: true,
        // v6.7.0：入口只有「姿势库 / 艺术构图 / 特写搭配」三页工具条上的
        // 「每次运行自动随机」复选框（三组互斥）
        pick_pool_mode: true,
        pick_pool_ids: true,
      };
      for (const wz of (this.widgets || [])) {
        if (HIDDEN[wz.name]) {
          wz.hidden = true;
          wz.computeSize = () => [0, -4];
        }
        if (WZ[wz.name]) wz.label = WZ[wz.name];
      }
      addNodeButtons(this);
      return r;
    };
  },
});
