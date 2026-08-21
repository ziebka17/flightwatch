"""Generuje statyczną stronę (jeden plik HTML) z tablicą okazji na podstawie bazy.

Użycie:  python -m flightwatch.site --out ../docs/index.html
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ConfigError, load_config

log = logging.getLogger("flightwatch.site")


def collect(db_path: str, cfg, history_days: int = 45) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=history_days)).isoformat()
    last_scan = conn.execute("SELECT MAX(observed_at) FROM observations").fetchone()[0]

    routes = []
    for d in cfg.destinations:
        # Historia: najtańsza cena w każdym skanowaniu
        hist = conn.execute(
            "SELECT observed_at, MIN(price) AS p FROM observations WHERE origin=? AND destination=? "
            "AND observed_at>=? GROUP BY observed_at ORDER BY observed_at",
            (cfg.origin, d.code, since)).fetchall()
        # Aktualnie najtańsza oferta (z ostatniego skanowania tej trasy)
        cur = conn.execute(
            "SELECT * FROM observations WHERE origin=? AND destination=? AND observed_at="
            "(SELECT MAX(observed_at) FROM observations WHERE origin=? AND destination=?) "
            "ORDER BY price LIMIT 5",
            (cfg.origin, d.code, cfg.origin, d.code)).fetchall()
        best = dict(cur[0]) if cur else None
        prices = [h["p"] for h in hist]
        routes.append({
            "code": d.code, "name": d.name, "region": d.region, "threshold": d.fixed_threshold,
            "best": best and {
                "price": best["price"], "currency": best["currency"], "depart": best["depart_date"],
                "ret": best["return_date"], "out": best["outbound_path"], "back": best["inbound_path"],
                "stops": best["stops_out"], "airlines": best["airlines"], "seen": best["observed_at"],
            },
            "top": [{"price": r["price"], "depart": r["depart_date"], "ret": r["return_date"],
                     "out": r["outbound_path"], "stops": r["stops_out"]} for r in cur],
            "history": [{"t": h["observed_at"], "p": h["p"]} for h in hist],
            "median": sorted(prices)[len(prices) // 2] if prices else None,
            "min": min(prices) if prices else None,
        })

    alerts = [dict(r) for r in conn.execute(
        "SELECT sent_at, alert_key, reason, price FROM alerts ORDER BY sent_at DESC LIMIT 50").fetchall()]
    calls = conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0]
    conn.close()
    return {
        "origin": cfg.origin, "currency": cfg.currency, "generated": now.isoformat(),
        "last_scan": last_scan, "routes": routes, "alerts": alerts, "api_calls": calls,
        "history_days": history_days,
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlightWatch – okazje z Gdańska</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--good:#006300;--crit:#d03b3b;--good-bg:#e7f5e7}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--good:#0ca30c;--crit:#e66767;--good-bg:#10321a}}
:root[data-theme=dark]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--good:#0ca30c;--crit:#e66767;--good-bg:#10321a}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--ink2);margin:0 0 20px;font-size:14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:24px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .l{color:var(--ink2);font-size:13px}.tile .v{font-size:26px;font-weight:600;margin-top:2px}.tile .d{color:var(--muted);font-size:13px}
.tabs{display:flex;gap:8px;margin:0 0 12px;flex-wrap:wrap}
.tabs button{background:var(--surface);border:1px solid var(--border);color:var(--ink2);border-radius:999px;padding:6px 14px;font:inherit;cursor:pointer}
.tabs button[aria-pressed=true]{color:var(--ink);border-color:var(--ink);font-weight:600}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:24px}
table{width:100%;border-collapse:collapse}th{font-size:12px;color:var(--muted);font-weight:500;text-align:left;padding:10px 12px;border-bottom:1px solid var(--grid)}
td{padding:10px 12px;border-bottom:1px solid var(--grid);vertical-align:middle;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}tr.route{cursor:pointer}tr.route:hover td{background:color-mix(in srgb,var(--ink) 4%,transparent)}
.price{font-weight:600;white-space:nowrap}.deal .price{color:var(--good)}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--good-bg);color:var(--good);margin-left:6px;font-weight:600}
.dim{color:var(--muted);font-size:13px}.path{font-size:13px;color:var(--ink2)}
svg.spark{display:block;overflow:visible}
tr.detail td{background:color-mix(in srgb,var(--ink) 3%,transparent);padding:8px 12px 12px}
.detail ul{margin:0;padding-left:18px}.detail li{margin:2px 0;font-size:14px}
a{color:var(--s1)}.tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:8px;
padding:6px 10px;font-size:13px;box-shadow:0 4px 16px rgba(0,0,0,.15);display:none;z-index:9}
.foot{color:var(--muted);font-size:13px}
.scroll{overflow-x:auto}
@media (max-width:700px){.hide-m{display:none}}
</style>
</head>
<body>
<div class="wrap">
<h1>✈️ FlightWatch – okazje z Gdańska</h1>
<p class="sub" id="sub"></p>
<div class="tiles" id="tiles"></div>
<div class="tabs" id="tabs" role="group" aria-label="Region"></div>
<div class="card scroll"><table id="routes"><thead><tr>
<th>Kierunek</th><th>Najtaniej teraz</th><th class="hide-m">Daty</th><th class="hide-m">Połączenie</th><th>Trend (__DAYS__ dni)</th><th class="hide-m">Zwykle</th>
</tr></thead><tbody></tbody></table></div>
<h2 style="font-size:18px;margin:0 0 10px">Ostatnie alerty</h2>
<div class="card scroll"><table id="alerts"><thead><tr><th>Kiedy</th><th>Trasa</th><th>Daty</th><th>Cena</th><th class="hide-m">Powód</th></tr></thead><tbody></tbody></table></div>
<p class="foot">Ceny pochodzą z wyszukiwarki Aviasales (Travelpayouts Data API) i mogą być nieaktualne – zawsze sprawdź cenę przed zakupem.
Kliknij trasę, żeby zobaczyć 5 najtańszych ofert. Ta strona jest generowana automatycznie po każdym skanowaniu.</p>
</div>
<div class="tip" id="tip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const fmt=n=>n==null?'–':Math.round(n).toLocaleString('pl-PL')+' '+D.currency;
const dt=s=>s?new Date(s).toLocaleString('pl-PL',{dateStyle:'medium',timeStyle:'short'}):'–';
const dd=s=>s?new Date(s+'T00:00:00').toLocaleDateString('pl-PL',{day:'numeric',month:'short'}):'';
const REG={americas:'Ameryki',asia:'Azja'};
document.getElementById('sub').textContent='Bilety powrotne z '+D.origin+' · ostatnie skanowanie: '+dt(D.last_scan)+' · strona wygenerowana: '+dt(D.generated);

// kafelki
const withBest=D.routes.filter(r=>r.best);
function cheapest(region){const rs=withBest.filter(r=>r.region===region).sort((a,b)=>a.best.price-b.best.price);return rs[0];}
const week=D.alerts.filter(a=>Date.now()-new Date(a.sent_at)<7*864e5).length;
const tiles=[['Najtaniej do Ameryk',cheapest('americas')],['Najtaniej do Azji',cheapest('asia')]].map(([l,r])=>r?
 `<div class="tile"><div class="l">${l}</div><div class="v">${fmt(r.best.price)}</div><div class="d">${r.name} · ${dd(r.best.depart)} – ${dd(r.best.ret)}</div></div>`:
 `<div class="tile"><div class="l">${l}</div><div class="v">–</div><div class="d">brak danych</div></div>`);
tiles.push(`<div class="tile"><div class="l">Alerty w ostatnich 7 dniach</div><div class="v">${week}</div><div class="d">łącznie ${D.alerts.length} zapisanych</div></div>`);
tiles.push(`<div class="tile"><div class="l">Poniżej progu teraz</div><div class="v">${withBest.filter(r=>r.best.price<r.threshold).length}</div><div class="d">z ${D.routes.length} kierunków</div></div>`);
document.getElementById('tiles').innerHTML=tiles.join('');

// sparkline: jedna seria (cena min. z każdego skanowania) – bez legendy
function spark(r,i){
 const h=r.history;if(h.length<2)return '<span class="dim">za mało danych</span>';
 const W=140,H=36,p=h.map(x=>x.p),mn=Math.min(...p),mx=Math.max(...p),t0=new Date(h[0].t),t1=new Date(h[h.length-1].t);
 const X=x=>((new Date(x.t)-t0)/((t1-t0)||1))*W,Y=x=>mx===mn?H/2:H-((x.p-mn)/(mx-mn))*(H-6)-3;
 const d=h.map((x,j)=>(j?'L':'M')+X(x).toFixed(1)+' '+Y(x).toFixed(1)).join(' ');
 const last=h[h.length-1];
 const thr=r.threshold>=mn&&r.threshold<=mx?`<line x1="0" x2="${W}" y1="${Y({p:r.threshold})}" y2="${Y({p:r.threshold})}" stroke="var(--axis)" stroke-dasharray="3 3"/>`:'';
 return `<svg class="spark" width="${W}" height="${H}" data-i="${i}" viewBox="0 0 ${W} ${H}" role="img" aria-label="trend ceny">${thr}
 <path d="${d}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
 <circle cx="${X(last)}" cy="${Y(last)}" r="4" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/>
 <rect width="${W}" height="${H}" fill="transparent"/></svg>`;
}
let region='all';
function render(){
 const tb=document.querySelector('#routes tbody');
 const rows=D.routes.filter(r=>region==='all'||r.region===region).sort((a,b)=>(a.best?a.best.price:1e12)-(b.best?b.best.price:1e12));
 tb.innerHTML=rows.map(r=>{const b=r.best,i=D.routes.indexOf(r),deal=b&&b.price<r.threshold;
  return `<tr class="route${deal?' deal':''}" data-i="${i}"><td><b>${r.name}</b> <span class="dim">${r.code} · ${REG[r.region]||r.region}</span></td>
  <td class="price">${b?fmt(b.price):'–'}${deal?'<span class="badge">okazja</span>':''}</td>
  <td class="hide-m">${b?dd(b.depart)+' – '+dd(b.ret):''}</td>
  <td class="hide-m path">${b?b.out+'<br>'+b.back:''}</td>
  <td>${spark(r,i)}</td><td class="hide-m dim">${r.median?fmt(r.median):'–'}<br>próg ${fmt(r.threshold)}</td></tr>`;}).join('')||'<tr><td colspan="6" class="dim">Brak danych – poczekaj na pierwsze skanowanie.</td></tr>';
}
function tabs(){const t=document.getElementById('tabs');t.innerHTML=[['all','Wszystkie'],['americas','Ameryki'],['asia','Azja']].map(([k,l])=>`<button aria-pressed="${region===k}" data-r="${k}">${l}</button>`).join('');
 t.querySelectorAll('button').forEach(b=>b.onclick=()=>{region=b.dataset.r;tabs();render();});}
tabs();render();
document.querySelector('#routes tbody').addEventListener('click',e=>{
 const tr=e.target.closest('tr.route');if(!tr)return;const nx=tr.nextElementSibling;
 if(nx&&nx.classList.contains('detail')){nx.remove();return;}
 const r=D.routes[+tr.dataset.i];const d=document.createElement('tr');d.className='detail';
 d.innerHTML=`<td colspan="6"><div class="detail"><b>5 najtańszych ofert (ostatnie skanowanie):</b><ul>${r.top.map(o=>`<li>${fmt(o.price)} · ${dd(o.depart)} – ${dd(o.ret)} · ${o.out}</li>`).join('')||'<li>brak</li>'}</ul>
 <div class="dim">Najniższa cena w ${D.history_days} dni: ${fmt(r.min)} · mediana: ${fmt(r.median)} · <a target="_blank" rel="noopener" href="https://www.google.com/travel/flights?q=Flights%20from%20${D.origin}%20to%20${r.code}">szukaj w Google Flights</a></div></div></td>`;
 tr.after(d);});
// tooltip na sparkline
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const s=e.target.closest&&e.target.closest('svg.spark');if(!s){tip.style.display='none';return;}
 const r=D.routes[+s.dataset.i],h=r.history,box=s.getBoundingClientRect(),f=(e.clientX-box.left)/box.width;
 const t0=new Date(h[0].t),t1=new Date(h[h.length-1].t),tt=t0.getTime()+f*(t1-t0);
 let best=h[0];for(const x of h)if(Math.abs(new Date(x.t)-tt)<Math.abs(new Date(best.t)-tt))best=x;
 tip.innerHTML=`<b>${fmt(best.p)}</b><br><span class="dim">${dt(best.t)}</span>`;tip.style.display='block';
 tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-40)+'px';});
// alerty
document.querySelector('#alerts tbody').innerHTML=D.alerts.map(a=>{const [route,dep,ret]=a.alert_key.split('|');
 const why=a.reason.replace('fixed_threshold','poniżej progu').replace('history_drop','duży spadek').replace('+',' + ');
 return `<tr><td class="dim">${dt(a.sent_at)}</td><td><b>${route}</b></td><td>${dd(dep)} – ${dd(ret)}</td><td class="price">${fmt(a.price)}</td><td class="hide-m dim">${why}</td></tr>`;}).join('')
 ||'<tr><td colspan="5" class="dim">Jeszcze żadnych alertów.</td></tr>';
</script>
</body>
</html>
"""


def build(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload).replace("__DAYS__", str(data["history_days"]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generuje stronę z tablicą okazji.")
    p.add_argument("--config", default="config.json")
    p.add_argument("--out", default="docs/index.html")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("błąd konfiguracji: %s", exc)
        return 2
    if not Path(cfg.database).exists():
        log.warning("brak bazy %s – generuję pustą stronę", cfg.database)
    data = collect(cfg.database, cfg, history_days=cfg.history_lookback_days)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(data), encoding="utf-8")
    log.info("zapisano %s (%d tras, %d alertów)", out, len(data["routes"]), len(data["alerts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
