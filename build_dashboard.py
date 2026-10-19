#!/usr/bin/env python3
"""Build self-contained interactive report from local Strategy Lab JSON files.

No network requests, no secrets, no Binance API and no build dependencies.
A static HTML artifact opens locally OR can be deployed to GitHub Pages.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from autotrading.catalog import CATALOG

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "lab"

HTML = r"""<!doctype html>
<html lang="hu"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoTrading · Strategy Lab 2.0</title>
<style>
:root{color-scheme:dark;--bg:#090e18;--panel:#101a2b;--line:#28364e;--text:#eaf0fc;--muted:#9aa9c2;--accent:#79d9b1;--danger:#f19090;--blue:#90b8ff}
*{box-sizing:border-box}body{margin:0;font:14px/1.55 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:radial-gradient(circle at 95% 0%,#14293b,var(--bg) 32%);color:var(--text)}
main{width:min(1400px,94%);margin:auto;padding:32px 0 70px}
header{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap}
h1{font-size:clamp(25px,3vw,40px);margin:8px 0 0;letter-spacing:-.04em}h2{font-size:20px;margin:0 0 12px}
.eyebrow{font-size:12px;letter-spacing:.13em;text-transform:uppercase;color:var(--accent);font-weight:700}
.subtle,.muted,small{color:var(--muted)}small{font-size:12px}.subtle{max-width:850px}
.actions{display:flex;gap:9px;flex-wrap:wrap}a.btn,button{background:#1e314b;border:1px solid #355273;border-radius:9px;color:var(--text);text-decoration:none;padding:9px 12px;cursor:pointer;font:inherit}a.btn:hover,button:hover{background:#294462}button.active{background:#276656;border-color:#63c8a1}
.notice{background:#241c25;border-left:3px solid #f6bc76;padding:12px 16px;margin:18px 0;border-radius:6px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}
.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 8px 28px #0002}
.card .value{font-size:clamp(21px,2.3vw,30px);font-weight:760;letter-spacing:-.04em;margin:4px 0}
.card .label{font-size:12px;color:var(--muted)}.panel{margin:16px 0}
.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:15px}.toolbar label{color:var(--muted)}
table{width:100%;border-collapse:collapse;text-align:left}th{font-size:12px;letter-spacing:.02em;color:var(--muted);font-weight:600;white-space:nowrap}
th,td{padding:12px 10px;border-bottom:1px solid #273449}th.sort{cursor:pointer}th.sort:hover{color:var(--text)}tr:last-child td{border-bottom:none}
tbody tr:hover{background:#19263b}td.numeric{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.positive{color:var(--accent)}.negative{color:var(--danger)}.pill{display:inline-block;border:1px solid #3f5770;border-radius:15px;padding:2px 8px;font-size:11px;color:var(--muted)}
.tablewrap{overflow:auto}.hint{font-size:12px;color:var(--muted)}
.chart{width:100%;height:260px}.legend{display:flex;gap:18px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:12px}
.dot{display:inline-block;width:9px;height:9px;border-radius:100%;margin-right:5px}
.barlabel{display:flex;align-items:center;justify-content:space-between;margin-top:12px}.regime{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
.regime>div{border:1px solid var(--line);padding:12px;border-radius:10px}.regime h3{margin:0 0 8px;font-size:14px}
select{background:#1b2a41;color:white;border:1px solid #355273;border-radius:8px;padding:9px 12px}
footer{font-size:12px;color:var(--muted);margin-top:35px}svg{overflow:visible}
@media(max-width:1000px){.grid{grid-template-columns:1fr 1fr}.regime{grid-template-columns:1fr}}
@media(max-width:560px){.grid{grid-template-columns:1fr}.card .value{font-size:26px}}
</style></head><body><main>
<header><div><div class="eyebrow">Autotrading / research only</div><h1>Strategy Lab <span style="color:var(--accent)">2.0</span></h1><p class="subtle">15 stratégia · valódi historikus spot árak · véletlenszerű, egymást nem átfedő tesztablakok · piaci rezsimek. Nem indít kereskedést.</p></div><div class="actions"><a class="btn" href="https://github.com/olivrberron-design/try.autotrading/actions/workflows/strategy-lab.yml" target="_blank" rel="noopener">↗ Új teszt indítása</a><a class="btn" href="https://github.com/olivrberron-design/try.autotrading" target="_blank" rel="noopener">Forráskód</a></div></header>
<div class="notice"><strong>Kutatási eredmények, nem bizonyított kereskedési előny.</strong> A költségek és a végrehajtás becslések. A korábbi 2026-os backtesztek már felfedtek részleteket a jelenlegi fenntartott időszakból. Igazán új bizonyítékot későbbi forward demoteszt adhat.</div>
<section class="toolbar"><label for="asset">Piac</label><select id="asset"></select><span id="run-meta" class="muted"></span></section>
<section class="grid">
<div class="card"><div class="label">Stratégiák</div><div id="k-strategies" class="value">–</div><small>Azonos adatokon</small></div>
<div class="card"><div class="label">Átfedő időablakok</div><div id="k-overlap" class="value">–</div><small>Elvárt érték: 0%</small></div>
<div class="card"><div class="label">Vizsgált tesztidőszakok</div><div id="k-windows" class="value">–</div><small>Időben különálló ablakok</small></div>
<div class="card"><div class="label">Minimális szűrőn átment</div><div id="k-eligible" class="value">–</div><small>Még nem AI / nem éles stratégia</small></div>
</section>
<section class="panel"><h2>Stratégiák rangsora</h2><p class="hint">Kattints az oszlopfejlécre a rendezéshez. A „medián többlet” az azonos 20%-os allokációjú buy-and-holdhoz képest számított különbség; önmagában nem profit.</p><div class="tablewrap"><table><thead><tr>
<th class="sort" data-sort="name">Stratégia ↕</th>
<th class="sort" data-sort="median_return_pct">Medián hozam ↕</th>
<th class="sort" data-sort="median_excess_vs_bh20_pct">Medián többlet ↕</th>
<th class="sort" data-sort="beat_bh20_windows_pct">Benchmark-győzelem ↕</th>
<th class="sort" data-sort="worst_window_return_pct">Legrosszabb ↕</th>
<th class="sort" data-sort="max_window_drawdown_pct">Max. DD ↕</th>
<th class="sort" data-sort="completed_trades_across_windows">Kötések ↕</th><th>Szűrő</th></tr></thead><tbody id="rankings"></tbody></table></div></section>
<section class="panel"><h2>Időszakonkénti hozam</h2><p class="hint">Pontok: a kiválasztott stratégia egy-egy nem átfedő történelmi időablakának becsült hozama. A pont színe a kezdő piaci környezetet jelzi. A vízszintes szürke vonal a nulla.</p>
<div class="toolbar"><label for="strategy">Stratégia</label><select id="strategy"></select><span id="chosen-info" class="muted"></span></div><svg id="chart" class="chart" role="img" aria-label="Időablakok becsült hozamai"></svg><div class="legend"><span><i class="dot" style="background:#79d9b1"></i>Emelkedő trend induláskor</span><span><i class="dot" style="background:#f6bc76"></i>Csökkenő trend induláskor</span><span><i class="dot" style="background:#90b8ff"></i>Oldalazó induláskor</span></div>
</section>
<section class="panel"><h2>Piaci környezet szerinti eredmények</h2><p class="hint">Az egyes ablakok INDULÁSAKOR elérhető 1 órás EMA50/200 alapján. Kevés ablaknál nem vonható le megbízható következtetés.</p><div id="regimes" class="regime"></div></section>
<section class="panel"><h2>Vizsgált historikus időablakok</h2><p class="hint">Az egyes ablakok időrendben követik egymást, de kezdőpontjuk és hosszuk véletlenszerű. Minden stratégia ugyanazokat az időszakokat kapja.</p><div class="tablewrap"><table><thead><tr><th>Időszak</th><th>Kezdő rezsim</th><th>Napok</th><th>Kiválasztott stratégia hozama</th><th>BH20 hozama</th><th>Többlet</th></tr></thead><tbody id="windows"></tbody></table></div></section>
<footer id="footer">A dashboard önálló HTML-fájl; nincs szükség API-kulcsra, internetkapcsolatra vagy futó szerverre. A jelenlegi piac- és rezsimértékelések hipotézisek, nem tanított AI.</footer>
</main><script type="application/json" id="data">__PAYLOAD__</script><script>
"use strict";
const data=JSON.parse(document.getElementById("data").textContent);
const $=id=>document.getElementById(id);
const pairs=Object.keys(data.reports), desc=data.catalog;
let asset=pairs[0], strategy="", sort="median_excess_vs_bh20_pct", descending=true;
const fm=v=>v===null||v===undefined?"n/a":(v>0?"+":"")+Number(v).toFixed(2)+"%";
const css=v=>v>0?"positive":v<0?"negative":"";
const esc=s=>String(s).replace(/[&<>"']/g,x=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[x]));
const sel=$("asset");
pairs.forEach(p=>{const el=document.createElement("option");el.value=p;el.textContent=p;sel.append(el)});
sel.addEventListener("change",()=>{asset=sel.value;strategy="";render()});
$("strategy").addEventListener("change",e=>{strategy=e.target.value;plotAndRegimes()});
document.querySelectorAll("th.sort").forEach(th=>th.addEventListener("click",()=>{
  const next=th.dataset.sort;descending=sort===next?!descending:true;sort=next;drawRanking();
}));
function render(){
  const r=data.reports[asset], stats=r.strategies;
  $("k-strategies").textContent=Object.keys(stats).length;
  $("k-overlap").textContent=r.overlap_pct_of_unique_discovery_bars+"%";
  $("k-windows").textContent=r.window_count;
  $("k-eligible").textContent=Object.values(stats).filter(x=>x.screen_eligible).length;
  $("run-meta").textContent=data.generated_at+" · "+r.discovery_start_utc+" → "+r.discovery_end_utc+" · seed "+r.seed;
  const names=Object.keys(stats);
  const opt=$("strategy");opt.replaceChildren();
  for(const name of names){const o=document.createElement("option");o.value=name;o.textContent=name;opt.append(o)}
  if(!strategy||!stats[strategy])strategy=r.selected_by_discovery_only||names[0];
  opt.value=strategy;
  drawRanking();plotAndRegimes();
}
function drawRanking(){
 const stats=data.reports[asset].strategies;
 const rows=Object.keys(stats).map(name=>({name,...stats[name]}));
 rows.sort((a,b)=>{
  const av=a[sort],bv=b[sort];
  return (typeof av==="string" ? av.localeCompare(bv) : ((av??-999)-(bv??-999)))*(descending?-1:1);
 });
 $("rankings").innerHTML=rows.map(r=>`<tr><td title="${esc(desc[r.name]||"")}"><strong>${esc(r.name)}</strong><br><small>${esc(desc[r.name]||"")}</small></td>
 <td class="numeric ${css(r.median_return_pct)}">${fm(r.median_return_pct)}</td>
 <td class="numeric ${css(r.median_excess_vs_bh20_pct)}">${fm(r.median_excess_vs_bh20_pct)}</td>
 <td class="numeric">${fm(r.beat_bh20_windows_pct)}</td>
 <td class="numeric ${css(r.worst_window_return_pct)}">${fm(r.worst_window_return_pct)}</td>
 <td class="numeric">${fm(r.max_window_drawdown_pct)}</td>
 <td class="numeric">${r.completed_trades_across_windows}</td>
 <td><span class="pill">${r.screen_eligible?"megfelelt":"kevés adat"}</span></td></tr>`).join("");
}
function plotAndRegimes(){
 const r=data.reports[asset], entries=r.sampled_windows.map(x=>({w:x,ret:x.results[strategy].return_pct,bh:x.results[strategy].buy_hold_equal_allocation_pct}));
 $("chosen-info").textContent=desc[strategy]||"";
 const svg=$("chart");svg.replaceChildren();
 const width=Math.max(680,svg.getBoundingClientRect().width||850), height=260,left=52,right=18,top=14,bottom=40;
 svg.setAttribute("viewBox",`0 0 ${width} ${height}`);
 const ymax=Math.max(1,...entries.map(e=>Math.abs(e.ret)),...entries.map(e=>Math.abs(e.bh)))*1.14;
 const y=v=>top+(ymax-v)/(2*ymax)*(height-top-bottom);
 const element=(tag,attrs={},text)=>{
   const node=document.createElementNS("http://www.w3.org/2000/svg",tag);
   Object.entries(attrs).forEach(([k,v])=>node.setAttribute(k,v));
   if(text!==undefined)node.textContent=text;
   svg.append(node);return node;
 };
 for(let m=-2;m<=2;m++){
   const v=ymax*m/2; const p=y(v);
   element("line",{x1:left,y1:p,x2:width-right,y2:p,stroke:m===0?"#8d9bb0":"#29394d","stroke-dasharray":m===0?"":"3 4"});
   element("text",{x:left-9,y:p+4,"text-anchor":"end",fill:"#9aa9c2","font-size":11},fm(v));
 }
 const dx=(width-left-right)/Math.max(1,entries.length);
 entries.forEach((e,i)=>{
   const x=left+dx*(i+.5);
   const color=({bull:"#79d9b1",bear:"#f6bc76",sideways:"#90b8ff"})[e.w.start_regime_1h_ema]||"#adbcd2";
   element("line",{x1:x,y1:y(0),x2:x,y2:y(e.ret),stroke:color,"stroke-width":2,opacity:.55});
   const circle=element("circle",{cx:x,cy:y(e.ret),r:5,fill:color,stroke:"#09121d","stroke-width":1.5});
   const tooltip=document.createElementNS("http://www.w3.org/2000/svg","title");
   tooltip.textContent=`${e.w.start_utc}–${e.w.end_utc}: ${strategy} ${fm(e.ret)}, BH20 ${fm(e.bh)}`;
   circle.append(tooltip);
   if(i===0||i===entries.length-1||i%Math.max(1,Math.floor(entries.length/6))===0){
     element("text",{x,y:height-13,"text-anchor":"middle",fill:"#9aa9c2","font-size":10},e.w.start_utc.slice(5));
   }
 });
 const regimes=r.strategies[strategy].by_start_regime;
 $("regimes").innerHTML=["bull","bear","sideways"].map(k=>{
  const x=regimes[k], label={bull:"Emelkedő",bear:"Csökkenő",sideways:"Oldalazó"}[k];
  return `<div><h3>${label}</h3><div class="label">${x?x.windows:0} időablak</div><div class="value ${x?css(x.median_return_pct):""}" style="font-size:23px;font-weight:700">${x?fm(x.median_return_pct):"n/a"}</div><small>Medián abszolút hozam</small><p>Medián többlet: <strong>${x?fm(x.median_excess_vs_bh20_pct):"n/a"}</strong></p></div>`;
 }).join("");
 $("windows").innerHTML=entries.map(e=>`<tr><td>${esc(e.w.start_utc)} → ${esc(e.w.end_utc)}</td><td>${esc(e.w.start_regime_1h_ema)}</td>
 <td>${e.w.duration_days}</td><td class="numeric ${css(e.ret)}">${fm(e.ret)}</td>
 <td class="numeric ${css(e.bh)}">${fm(e.bh)}</td><td class="numeric ${css(e.ret-e.bh)}">${fm(e.ret-e.bh)}</td></tr>`).join("");
}
render();
</script></body></html>"""


def build(input_dir, output_dir):
    reports = {}
    for path in sorted(input_dir.glob("*.json")):
        if path.stem in ("BTCUSDC", "ETHUSDC"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            reports[doc["symbol"]] = doc
    if not reports:
        raise ValueError("No BTCUSDC/ETHUSDC JSON reports found")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "reports": reports, "catalog": CATALOG,
    }, ensure_ascii=False, separators=(",", ":"))
    # Prevent the JSON payload terminating a script tag (even on future data).
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output_path = output_dir / "index.html"
    output_path.write_text(HTML.replace("__PAYLOAD__", payload), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build offline/browser dashboard from completed Strategy Lab reports")
    ap.add_argument("--input", type=Path, default=None, help="Strategy Lab run folder (default newest)")
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "site")
    opts = ap.parse_args()
    folder = opts.input or max((x for x in DATA.iterdir() if x.is_dir()), key=lambda p: p.name)
    page = build(folder, opts.output)
    print("Browser dashboard ready:", page)
