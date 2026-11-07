# -*- coding: utf-8 -*-
"""把 `presets/anima_v4_scenes.json` 汇总成一份**单文件离线 HTML**。

用法：
    python tools/build_scene_catalog.py
    python tools/build_scene_catalog.py --out "D:/somewhere/xxx.html"

设计口径（用户 2026-11-05 23:23 拍板）：
  · 全部 444 条一条不漏；L2+ 的「正文与变体」默认再折叠一层（点开才看）。
  · 卡面只放概览，点开看完整 tags / extra / 正文 / 变体池 / 闭麦等。
  · 搜索（中文名/id/tags/正文）、页签/档位/题材/随机池/变体筛选、排序、统计条。
  · 单文件、零网络依赖，数据内嵌进 <script type="application/json">。
"""
import argparse
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SCENES = os.path.join(PLUGIN, "presets", "anima_v4_scenes.json")
INIT = os.path.join(PLUGIN, "__init__.py")
DEFAULT_OUT = r"C:\Users\VGBFC\Desktop\场景库总览.html"

GROUP_ORDER = ["sfw", "nsfw", "near", "pose", "art"]


def load():
    d = json.load(open(SCENES, encoding="utf-8"))
    m = re.search(r'__version__\s*=\s*"(.*?)"', open(INIT, encoding="utf-8").read())
    version = m.group(1) if m else "?"
    scenes = d["scenes"]

    groups = []
    for g in d.get("groups", []):
        gid = g["id"]
        groups.append({
            "id": gid,
            "desc": g.get("desc") or "",
            "count": sum(1 for s in scenes if s["group"] == gid),
        })
    groups.sort(key=lambda x: GROUP_ORDER.index(x["id"])
                if x["id"] in GROUP_ORDER else 99)

    levels = {str(i): sum(1 for s in scenes if int(s["level"]) == i)
              for i in range(5)}
    themes = sorted({str(s.get("theme") or "") for s in scenes if s.get("theme")})
    manual = sum(1 for s in scenes
                 if s.get("manual_only") or s.get("manual"))
    meta = {
        "version": version,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "S:/AI_workV5/ComfyUI/custom_nodes/Comfyui-Anima-Wardrobe-V6/"
                  "presets/anima_v4_scenes.json",
        "total": len(scenes),
        "groups": groups,
        "levels": levels,
        "themes": themes,
        "random_pool": len(scenes) - manual,
        "manual": manual,
        "variants": sum(1 for s in scenes if s.get("random_variants")),
        "random_fields": sum(1 for s in scenes if s.get("random_fields")),
        "random_any": sum(1 for s in scenes
                          if s.get("random_variants") or s.get("random_fields")),
    }
    return scenes, meta


# CSS/JS 里有大量 {} —— 用占位符整体替换，绝不用 f-string / str.format。
TPL = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>场景库总览 · Comfyui-Anima-Wardrobe-V6</title>
<style>
:root{
  --bg:#14151a; --panel:#1c1e26; --card:#22242e; --card2:#1a1c24;
  --bd:#343746; --tx:#e7e9f1; --mu:#969cb0; --ac:#7aa2f7;
  --l0:#5fbf8f; --l1:#d9b45a; --l2:#e08a52; --l3:#d7607a; --l4:#a06bd6;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
  font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  font-size:14px;line-height:1.6}
code,.mono{font-family:ui-monospace,Consolas,"Courier New",monospace}
header{position:sticky;top:0;z-index:20;background:rgba(20,21,26,.97);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--bd);padding:16px 22px 12px}
h1{margin:0 0 2px;font-size:21px;letter-spacing:.5px}
.sub{color:var(--mu);font-size:12px;margin-bottom:12px}
.sub b{color:var(--tx);font-weight:600}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.stat{background:var(--panel);border:1px solid var(--bd);border-radius:8px;
  padding:6px 12px;min-width:96px}
.stat .n{font-size:18px;font-weight:700;line-height:1.2}
.stat .k{font-size:11px;color:var(--mu)}
.lvbar{display:flex;height:16px;border-radius:5px;overflow:hidden;
  border:1px solid var(--bd);margin-bottom:12px;min-width:220px;flex:1}
.lvbar i{display:block;height:100%}
.lvbar i[title]:hover{filter:brightness(1.25)}
.lvlegend{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--mu);margin-bottom:12px}
.lvlegend span::before{content:"";display:inline-block;width:8px;height:8px;
  border-radius:2px;margin-right:4px;background:var(--c)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input[type=text],select{background:var(--card);color:var(--tx);
  border:1px solid var(--bd);border-radius:7px;padding:6px 9px;font-size:13px;
  font-family:inherit;outline:none}
input[type=text]{min-width:260px;flex:1}
input[type=text]:focus,select:focus{border-color:var(--ac)}
button{background:var(--card);color:var(--tx);border:1px solid var(--bd);
  border-radius:7px;padding:6px 11px;font-size:13px;cursor:pointer;font-family:inherit}
button:hover{border-color:var(--ac)}
label.chk{display:inline-flex;align-items:center;gap:5px;font-size:12px;
  color:var(--mu);cursor:pointer;white-space:nowrap}
.count{margin-left:auto;font-size:12px;color:var(--mu);white-space:nowrap}
.count b{color:var(--ac);font-size:15px}
main{padding:18px 22px 60px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:10px}
details.card{background:var(--card);border:1px solid var(--bd);border-radius:9px;
  overflow:hidden;align-self:start}
details.card[open]{grid-column:span 2}
details.card>summary{cursor:pointer;list-style:none;padding:9px 11px;display:block}
details.card>summary::-webkit-details-marker{display:none}
details.card>summary:hover{background:var(--card2)}
.row1{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
.name{font-weight:650;font-size:14px}
.badge{font-size:10.5px;padding:1px 6px;border-radius:4px;border:1px solid;
  white-space:nowrap;line-height:1.5}
.badge.g{border-color:#4b7fc0;color:#9cc2ff}
.badge.g[data-g="nsfw"]{border-color:#a6506a;color:#f0a2b8}
.badge.g[data-g="near"]{border-color:#3f8f8f;color:#8fdad4}
.badge.g[data-g="pose"]{border-color:#7a62b0;color:#c3aef0}
.badge.g[data-g="art"]{border-color:#a6863f;color:#e8cf8a}
.lv{font-size:10.5px;padding:1px 6px;border-radius:4px;font-weight:700;
  color:#14151a;white-space:nowrap}
.lv0{background:var(--l0)}.lv1{background:var(--l1)}.lv2{background:var(--l2)}
.lv3{background:var(--l3)}.lv4{background:var(--l4)}
.th{font-size:11px;color:var(--mu)}
.sid{font-size:10.5px;color:#7f8599;margin-left:auto}
.desc{font-size:12px;color:var(--mu);margin-top:4px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mini{font-size:11px;color:#7f8599;margin-top:3px}
.hit{font-size:10px;color:#e0b45a;border:1px solid #7a6530;
  border-radius:4px;padding:0 5px}
.body{border-top:1px solid var(--bd);padding:10px 11px 12px;background:var(--card2)}
.sec{margin-bottom:11px}
.sec:last-child{margin-bottom:0}
.sec h4{margin:0 0 5px;font-size:11px;color:var(--ac);letter-spacing:.6px;
  text-transform:uppercase;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12px}
td{padding:2px 6px 2px 0;vertical-align:top}
td.k{color:var(--mu);width:112px;white-space:nowrap}
td.v{word-break:break-word}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{font-size:11px;background:#2c2f3b;border:1px solid #3c4050;
  border-radius:4px;padding:1px 6px;word-break:break-word}
.chip.drop{border-color:#5a4550;color:#d4a9b6}
.chip.ban{border-color:#5a4a40;color:#d9b48f}
.nl{font-size:12.5px;color:#cfd4e4;background:#20222c;border-left:2px solid var(--ac);
  padding:7px 9px;border-radius:0 5px 5px 0;margin:0}
details.sensitive{border:1px dashed #5a4550;border-radius:6px;padding:6px 9px;
  background:#1e1c24}
details.sensitive>summary{cursor:pointer;font-size:11.5px;color:#d4a9b6;
  list-style:none}
details.sensitive>summary::-webkit-details-marker{display:none}
details.sensitive>summary::before{content:"▸ ";color:#d4a9b6}
details.sensitive[open]>summary::before{content:"▾ "}
details.sensitive .sec{margin-top:9px}
.var{margin-bottom:7px}
.var .vt{font-size:11px;color:var(--ac);margin-bottom:3px}
.empty{text-align:center;color:var(--mu);padding:60px 20px;font-size:14px}
footer{padding:14px 22px 30px;color:#6f758a;font-size:11px;border-top:1px solid var(--bd)}
@media(max-width:760px){
  details.card[open]{grid-column:span 1}
  input[type=text]{min-width:100%}
  header{padding:12px 14px}main{padding:12px 14px 40px}
}
</style>
</head>
<body>
<header>
  <h1>场景库总览</h1>
  <div class="sub">Comfyui-Anima-Wardrobe-V6 · <b>v__VERSION__</b> ·
    <b>__TOTAL__</b> 条预设 · 生成于 __TIME__ · 数据来源 <span class="mono">__SOURCE__</span></div>
  <div class="stats" id="stats"></div>
  <div class="lvbar" id="lvbar"></div>
  <div class="lvlegend" id="lvlegend"></div>
  <div class="controls">
    <input type="text" id="q" placeholder="搜索：中文名 / id / 题材 / tags / 正文（空格 = 全部命中）" autocomplete="off">
    <select id="fGroup"><option value="">全部页签</option></select>
    <select id="fLevel"><option value="">全部档位</option></select>
    <select id="fTheme"><option value="">全部题材</option></select>
    <select id="fPool"><option value="">随机池/手动</option><option value="pool">进随机池</option><option value="manual">仅手动套用</option></select>
    <select id="fVar"><option value="">变体池</option><option value="yes">带变体池</option><option value="no">无变体池</option></select>
    <select id="sort">
      <option value="default">排序：页签→档位→id</option>
      <option value="name">按中文名</option>
      <option value="lvup">按档位（低→高）</option>
      <option value="lvdown">按档位（高→低）</option>
      <option value="id">按 id</option>
      <option value="theme">按题材</option>
    </select>
    <button id="reset">重置</button>
    <label class="chk"><input type="checkbox" id="sensAll"> 展开全部成人正文</label>
    <span class="count">当前 <b id="shown">0</b> / __TOTAL__ 条</span>
  </div>
</header>
<main><div class="grid" id="grid"></div><div class="empty" id="empty" hidden>没有匹配的场景，试试放宽筛选条件。</div></main>
<footer>单文件离线页面 · 数据内嵌，不依赖网络 · 由 <span class="mono">tools/build_scene_catalog.py</span> 生成，场景库变更后重跑即可刷新。L2 及以上条目的正文与变体默认折叠。</footer>

<script id="scene-data" type="application/json">__DATA__</script>
<script id="scene-meta" type="application/json">__META__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("scene-data").textContent);
const META = JSON.parse(document.getElementById("scene-meta").textContent);
const LVNAME = ["L0 全年龄","L1 擦边","L2 explicit","L3 hardcore","L4 extreme"];
const LVCOLOR = ["var(--l0)","var(--l1)","var(--l2)","var(--l3)","var(--l4)"];
const GNAME = {sfw:"🎬 场景搭配",nsfw:"🔞 成人场景",near:"🔍 特写搭配",
               pose:"🧘 姿势库",art:"🎨 艺术构图"};
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v)
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
  .replace(/"/g,"&quot;").replace(/'/g,"&#39;");

/* 全文检索串：概览 + 字段值 + extra + 正文 + 变体 + 闭麦/禁用 */
function hay(s){
  const parts = [s.id,s.zh,s.theme,s.desc,s.group,s.count_lock,s.extra,s.scene_nl,
    Object.values(s.tags||{}).join(" "),
    (s.drop_fields||[]).join(" "),(s.ban_tags||[]).join(" "),
    variantText(s)].join(" ");
  return parts.toLowerCase();
}
/* L2+ 折叠区里的内容（用于标"正文命中"） */
function hiddenHay(s){
  const rv = s.random_variants||{};
  let t = String(s.scene_nl||"");
  for (const pool of Object.values(rv)){
    for (const it of ((pool&&pool.items)||[])){
      t += " " + (it.nl||"") + " " + (it.nl_extra||"");
    }
  }
  return t.toLowerCase();
}
function variantText(s){
  const rv = s.random_variants||{}; const out=[];
  for (const [k,pool] of Object.entries(rv)){
    out.push(k, pool&&pool.label||"");
    for (const it of ((pool&&pool.items)||[])){
      out.push(it.zh||"", it.extra||"", it.nl||"", it.nl_extra||"");
    }
  }
  const rf = s.random_fields||{};
  for (const [k,vals] of Object.entries(rf)) out.push(k,(vals||[]).join(" "));
  return out.join(" ");
}
const HAY = new Map(DATA.map(s=>[s.id,hay(s)]));
const HIDDEN = new Map(DATA.map(s=>[s.id,hiddenHay(s)]));
const isManual = (s) => !!(s.manual_only || s.manual);
const isSensitive = (s) => Number(s.level) >= 2;

/* ── 顶部统计 ── */
(function stats(){
  const blocks = [
    [META.total,"场景总数"],[META.groups.length,"页签"],
    [META.random_pool,"随机池条目"],[META.manual,"仅手动套用"],
    [META.random_any,"带随机性"],[META.variants,"带变体池"],
    [META.themes.length,"题材数"]
  ];
  $("stats").innerHTML = blocks.map(([n,k])=>
    `<div class="stat"><div class="n">${n}</div><div class="k">${k}</div></div>`).join("");
  $("lvbar").innerHTML = [0,1,2,3,4].map(i=>{
    const n = META.levels[String(i)]||0;
    return `<i style="width:${(n/META.total*100).toFixed(2)}%;background:${LVCOLOR[i]}"
      title="${LVNAME[i]}：${n} 条"></i>`;
  }).join("");
  $("lvlegend").innerHTML = [0,1,2,3,4].map(i=>
    `<span style="--c:${LVCOLOR[i]}">${LVNAME[i]} ${META.levels[String(i)]||0}</span>`).join("");
  for (const g of META.groups){
    const o=document.createElement("option");
    o.value=g.id; o.textContent=`${GNAME[g.id]||g.id}（${g.count}）`;
    $("fGroup").appendChild(o);
  }
  for (let i=0;i<5;i++){
    const o=document.createElement("option");
    o.value=String(i); o.textContent=`${LVNAME[i]}（${META.levels[String(i)]||0}）`;
    $("fLevel").appendChild(o);
  }
  for (const t of META.themes){
    const o=document.createElement("option"); o.value=t; o.textContent=t;
    $("fTheme").appendChild(o);
  }
})();

/* ── 详情区 ── */
function chips(arr, cls){
  arr = (arr||[]).filter(x=>String(x||"").trim());
  if (!arr.length) return "";
  return `<div class="chips">${arr.map(x=>
    `<span class="chip ${cls||""}">${esc(x)}</span>`).join("")}</div>`;
}
function sec(title, inner){
  return inner ? `<div class="sec"><h4>${title}</h4>${inner}</div>` : "";
}
function tagsTable(s){
  const rows = Object.entries(s.tags||{});
  if (!rows.length) return "";
  return `<table>${rows.map(([k,v])=>
    `<tr><td class="k mono">${esc(k)}</td><td class="v">${esc(v)}</td></tr>`).join("")}</table>`;
}
function variantsHtml(s){
  const rv = s.random_variants||{}; const rf = s.random_fields||{};
  let out="";
  for (const [k,pool] of Object.entries(rv)){
    const items = (pool&&pool.items)||[];
    out += `<div class="var"><div class="vt">${esc(k)} · ${esc(pool&&pool.label||"")}（${items.length} 变体）</div>`;
    out += items.map(it=>{
      const bits=[`<b>${esc(it.zh||"")}</b>`];
      if (it.extra) bits.push(`extra: ${esc(it.extra)}`);
      if (it.nl) bits.push(`nl: ${esc(it.nl)}`);
      if (it.nl_extra) bits.push(`nl_extra: ${esc(it.nl_extra)}`);
      return `<div class="chip" style="display:block;margin:3px 0;line-height:1.5">${bits.join("<br>")}</div>`;
    }).join("") + `</div>`;
  }
  for (const [k,vals] of Object.entries(rf)){
    out += `<div class="var"><div class="vt mono">random_fields.${esc(k)}</div>` +
      chips(vals) + `</div>`;
  }
  return out;
}
function metaHtml(s){
  const bits=[];
  if (s.count_lock) bits.push(`人数锁：<span class="mono">${esc(s.count_lock)}</span>`);
  bits.push(isManual(s) ? "仅手动套用（不进随机池）" : "进随机池");
  if (s.no_role) bits.push("no_role（画框里没有脸）");
  if (s.role_ok) bits.push("role_ok");
  if (s.bg) bits.push(`底色：<span class="mono">${esc(s.bg)}</span>`);
  if (s.comp) bits.push(`构图：<span class="mono">${esc(s.comp)}</span>`);
  return `<div style="font-size:11.5px;color:var(--mu)">${bits.join(" · ")}</div>`;
}
function bodyHtml(s){
  let out = "";
  out += sec("固定字段 tags", tagsTable(s));
  out += sec("extra 标签", chips(String(s.extra||"").split(",").map(x=>x.trim())));
  out += sec("闭麦字段 drop_fields", chips(s.drop_fields,"drop"));
  out += sec("禁用词 ban_tags", chips(s.ban_tags,"ban"));
  out += metaHtml(s);

  const main = sec("scene_nl 正文",
      s.scene_nl ? `<p class="nl">${esc(s.scene_nl)}</p>` : "") +
    sec("变体池 / 随机字段", variantsHtml(s));
  if (isSensitive(s)){
    out += `<details class="sensitive" data-sens="1"><summary>展开正文与变体（L2+ 默认折叠）</summary>${main}</details>`;
  } else {
    out += main;
  }
  return out;
}
function miniHtml(s){
  const t=s.tags||{}; const p=[];
  if (s.count_lock) p.push(esc(s.count_lock));
  if (t.place) p.push(esc(t.place));
  if (t.shot_size) p.push(esc(t.shot_size));
  if (t.viewpoint) p.push(esc(t.viewpoint));
  if (isManual(s)) p.push("仅手动");
  if (s.random_variants) p.push("带变体");
  return p.join(" · ");
}
function cardHtml(s, hiddenHit){
  const g=GNAME[s.group]||s.group, lv=Number(s.level);
  return `<details class="card" data-id="${esc(s.id)}">
    <summary>
      <div class="row1">
        <span class="name">${esc(s.zh)}</span>
        <span class="badge g" data-g="${esc(s.group)}">${esc(g)}</span>
        <span class="lv lv${lv}">${LVNAME[lv]}</span>
        <span class="th">${esc(s.theme||"")}</span>
        ${hiddenHit?'<span class="hit">正文命中</span>':""}
        <span class="sid mono">${esc(s.id)}</span>
      </div>
      <div class="desc">${esc(s.desc||"")}</div>
      <div class="mini">${miniHtml(s)}</div>
    </summary>
    <div class="body">${bodyHtml(s)}</div>
  </details>`;
}

/* ── 筛选 / 排序 / 渲染 ── */
let openSet = new Set();
let sensOpen = new Set();
let sensAll = false;
function currentOpen(){
  openSet = new Set([...document.querySelectorAll("details.card[open]")]
    .map(d=>d.dataset.id));
  sensOpen = new Set([...document.querySelectorAll("details.card[open] details.sensitive[open]")]
    .map(d=>d.closest("details.card").dataset.id));
}
function render(){
  currentOpen();
  const q=$("q").value.trim().toLowerCase();
  const toks=q?q.split(/\s+/):[];
  const fg=$("fGroup").value, fl=$("fLevel").value, ft=$("fTheme").value;
  const fp=$("fPool").value, fv=$("fVar").value, so=$("sort").value;

  let list = DATA.filter(s=>{
    if (fg && s.group!==fg) return false;
    if (fl && String(s.level)!==fl) return false;
    if (ft && s.theme!==ft) return false;
    if (fp==="pool" && isManual(s)) return false;
    if (fp==="manual" && !isManual(s)) return false;
    if (fv==="yes" && !s.random_variants) return false;
    if (fv==="no" && s.random_variants) return false;
    if (toks.length){
      const h=HAY.get(s.id)||"";
      if (!toks.every(t=>h.includes(t))) return false;
    }
    return true;
  });
  const gi=(g)=>{const i=META.groups.findIndex(x=>x.id===g);return i<0?99:i;};
  if (so==="name") list.sort((a,b)=>String(a.zh).localeCompare(String(b.zh),"zh"));
  else if (so==="id") list.sort((a,b)=>a.id.localeCompare(b.id));
  else if (so==="lvup") list.sort((a,b)=>a.level-b.level||a.id.localeCompare(b.id));
  else if (so==="lvdown") list.sort((a,b)=>b.level-a.level||a.id.localeCompare(b.id));
  else if (so==="theme") list.sort((a,b)=>String(a.theme).localeCompare(String(b.theme),"zh")||a.id.localeCompare(b.id));
  else list.sort((a,b)=>gi(a.group)-gi(b.group)||a.level-b.level||a.id.localeCompare(b.id));

  $("shown").textContent=list.length;
  $("empty").hidden=list.length>0;
  $("grid").innerHTML=list.map(s=>{
    const hiddenHit = toks.length && isSensitive(s) &&
      toks.some(t=>(HIDDEN.get(s.id)||"").includes(t)) &&
      !toks.some(t=>(HAY.get(s.id)||"").includes(t) &&
        !(HIDDEN.get(s.id)||"").includes(t));
    return cardHtml(s,hiddenHit);
  }).join("");

  for (const d of document.querySelectorAll("details.card")){
    const id=d.dataset.id;
    if (openSet.has(id)) d.open=true;
    const sd=d.querySelector("details.sensitive");
    if (sd && (sensAll || sensOpen.has(id))) sd.open=true;
  }
}
let timer=null;
function debounce(){clearTimeout(timer);timer=setTimeout(render,110);}
$("q").addEventListener("input",debounce);
for (const id of ["fGroup","fLevel","fTheme","fPool","fVar","sort"])
  $(id).addEventListener("change",render);
$("sensAll").addEventListener("change",(e)=>{
  sensAll=e.target.checked;
  for (const d of document.querySelectorAll("details.sensitive")) d.open=sensAll;
  if (!sensAll) sensOpen=new Set();
});
$("reset").addEventListener("click",()=>{
  $("q").value="";
  for (const id of ["fGroup","fLevel","fTheme","fPool","fVar"]) $(id).value="";
  $("sort").value="default"; $("sensAll").checked=false; sensAll=false;
  openSet=new Set(); sensOpen=new Set(); render();
});
render();
</script>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    scenes, meta = load()

    # 内嵌 JSON：把 '<' 转成 \u003c，避免正文里出现 </script> 截断标签
    def embed(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    html = (TPL.replace("__VERSION__", meta["version"])
               .replace("__TIME__", meta["generated_at"])
               .replace("__SOURCE__", meta["source"])
               .replace("__TOTAL__", str(meta["total"]))
               .replace("__DATA__", embed(scenes))
               .replace("__META__", embed(meta)))
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    print(f"已生成 {out}  ({len(html):,} 字符 / {meta['total']} 条场景)")
    print("  v%s · 分组 %s" % (meta["version"],
          {g["id"]: g["count"] for g in meta["groups"]}))


if __name__ == "__main__":
    main()
