#!/usr/bin/env python3
"""Build a self-contained viewer.html with all_elev.json embedded inline,
so it opens by double-click (file://) without a local server.
Day selector switches between each day and the whole route."""
import json, os

src = "all_elev.json" if os.path.exists("all_elev.json") else "day1_elev.json"
data = open(src).read()

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Solar Car — Elevation</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root{--bg:#0e1116;--panel:#161b22;--fg:#e6edf3;--muted:#8b949e;--accent:#f0a500;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:14px/1.4 system-ui,Segoe UI,Roboto,sans-serif}
  #app{display:flex;flex-direction:column;height:100vh}
  header{padding:8px 14px;background:var(--panel);border-bottom:1px solid #30363d;
    display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  header h1{font-size:15px;margin:0;color:var(--accent);white-space:nowrap}
  #tabs{display:flex;gap:4px}
  #tabs button{background:#21262d;color:var(--fg);border:1px solid #30363d;border-radius:6px;
    padding:4px 10px;cursor:pointer;font:inherit;font-size:13px}
  #tabs button:hover{border-color:var(--accent)}
  #tabs button.active{background:var(--accent);color:#161b22;border-color:var(--accent);font-weight:600}
  #locate{background:#21262d;color:var(--fg);border:1px solid #30363d;border-radius:6px;
    padding:4px 10px;cursor:pointer;font:inherit;font-size:13px;white-space:nowrap}
  #locate:hover{border-color:#4cc9f0}
  #locate.on{background:#4cc9f0;color:#161b22;border-color:#4cc9f0;font-weight:600}
  #locate.err{border-color:#f94144;color:#f94144}
  .stats{display:flex;gap:16px;flex-wrap:wrap;margin-left:auto}
  .stat{color:var(--muted)} .stat b{color:var(--fg)}
  #map{flex:1 1 auto;min-height:0}
  #chartwrap{flex:0 0 240px;min-height:240px;background:var(--panel);
    border-top:1px solid #30363d;position:relative;overflow:hidden}
  #chart{position:absolute;inset:0;width:100%;height:100%;display:block}
  #tip{position:absolute;pointer-events:none;background:#000c;border:1px solid var(--accent);
    border-radius:6px;padding:6px 8px;font-size:12px;white-space:nowrap;display:none;z-index:5}
  .leaflet-container{background:#0a0d12}
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>2026 Solar Car Challenge</h1>
    <div id="tabs"></div>
    <button id="locate" title="Show this device's location on the map">📍 Locate me</button>
    <div class="stats">
      <span class="stat">Dist <b id="s-dist"></b></span>
      <span class="stat">Range <b id="s-range"></b></span>
      <span class="stat">Ascent <b id="s-asc"></b></span>
      <span class="stat">Descent <b id="s-desc"></b></span>
    </div>
  </header>
  <div id="map"></div>
  <div id="chartwrap"><canvas id="chart"></canvas><div id="tip"></div></div>
</div>
<script>
const DATA = __DATA__;
const DAYS = DATA.days || [{day:DATA.summary.day||"Day 1", summary:DATA.summary, segments:DATA.segments}];
const PALETTE=['#f0a500','#4cc9f0','#90be6d','#f94144','#b388eb'];

// ---- map base ----
const map = L.map('map',{zoomControl:true}).setView([31.3,-98.5],6);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const cursor = L.circleMarker([0,0],{radius:7,color:'#fff',weight:2,
  fillColor:'#f0a500',fillOpacity:1,opacity:0}).addTo(map);
cursor.setStyle({fillOpacity:0});

// ---- build a view (single day index, or 'all') ----
function buildView(sel){
  let segs=[], points=[], dividers=[], summary, color;
  if(sel==='all'){
    let off=0;
    DAYS.forEach((d,di)=>{
      const c=PALETTE[di%PALETTE.length];
      dividers.push({at:off,label:d.day}); // marker at the START of each day, so each section shows its own day
      d.segments.forEach(s=>{
        const sp=s.points.map(p=>({...p,d:p.d+off}));
        segs.push({name:d.day+' · '+s.name, points:sp, color:c});
        points.push(...sp);
      });
      off=points[points.length-1].d;
    });
    summary=DATA.grand;
  } else {
    const d=DAYS[sel];
    d.segments.forEach((s,i)=>{
      segs.push({name:s.name, points:s.points, color:PALETTE[i%PALETTE.length]});
      points.push(...s.points);
    });
    (d.summary.segment_bounds||[]).slice(0,-1).forEach(b=>
      dividers.push({at:b.at_km*1000,label:''}));
    summary=d.summary;
  }
  return {segs,points,dividers,summary};
}

// ---- render map ----
function renderMap(view){
  routeLayer.clearLayers();
  const all=[];
  view.segs.forEach(s=>{
    const ll=s.points.map(p=>[p.lat,p.lon]); all.push(...ll);
    L.polyline(ll,{color:s.color,weight:4,opacity:.9}).bindTooltip(s.name).addTo(routeLayer);
    L.circleMarker(ll[0],{radius:4,color:'#fff',fillColor:s.color,fillOpacity:1})
      .bindTooltip(s.name+' start').addTo(routeLayer);
  });
  map.fitBounds(L.latLngBounds(all),{padding:[20,20]});
}

// ---- elevation chart ----
const cv=document.getElementById('chart'), ctx=cv.getContext('2d'), tip=document.getElementById('tip');
const M={l:48,r:10,t:12,b:22};
let V, pts, dmax, eMin, eMax, W,H,plotW,plotH;
const X=d=>M.l+plotW*(d/dmax);
const Y=e=>M.t+plotH*(1-(e-eMin)/(eMax-eMin));
function gradeColor(g){const a=Math.min(Math.abs(g)/8,1);
  return `rgb(${Math.round(80+175*a)},${Math.round(190-150*a)},70)`;}
function setView(view){
  V=view; pts=view.points; dmax=pts[pts.length-1].d;
  eMin=Infinity;eMax=-Infinity;
  for(const p of pts){if(p.ele<eMin)eMin=p.ele;if(p.ele>eMax)eMax=p.ele;}
  const pad=(eMax-eMin)*0.08||10; eMin-=pad; eMax+=pad;
  resize();
}
function resize(){
  const r=cv.parentElement.getBoundingClientRect();
  cv.width=r.width*devicePixelRatio; cv.height=r.height*devicePixelRatio;
  ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
  W=r.width;H=r.height;plotW=W-M.l-M.r;plotH=H-M.t-M.b;
  draw();
}
function draw(){
  if(!pts) return;
  ctx.clearRect(0,0,W,H);
  ctx.strokeStyle='#30363d';ctx.fillStyle='#8b949e';ctx.font='11px system-ui';
  ctx.textAlign='right';ctx.textBaseline='middle';
  for(let i=0;i<=5;i++){const e=eMin+(eMax-eMin)*i/5,y=Y(e);
    ctx.beginPath();ctx.moveTo(M.l,y);ctx.lineTo(W-M.r,y);ctx.stroke();
    ctx.fillText(Math.round(e)+'m',M.l-6,y);}
  ctx.textAlign='center';ctx.textBaseline='top';
  for(let i=0;i<=8;i++){const d=dmax*i/8;ctx.fillText((d/1000).toFixed(0)+'km',X(d),H-M.b+5);}
  // area
  ctx.beginPath();ctx.moveTo(X(0),Y(pts[0].ele));
  for(const p of pts)ctx.lineTo(X(p.d),Y(p.ele));
  ctx.lineTo(X(dmax),Y(eMin));ctx.lineTo(X(0),Y(eMin));ctx.closePath();
  ctx.fillStyle='rgba(240,165,0,.10)';ctx.fill();
  // grade-colored line
  ctx.lineWidth=1.6;
  for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i],dd=b.d-a.d;
    ctx.strokeStyle=gradeColor(dd>0?(b.ele-a.ele)/dd*100:0);
    ctx.beginPath();ctx.moveTo(X(a.d),Y(a.ele));ctx.lineTo(X(b.d),Y(b.ele));ctx.stroke();}
  // dividers
  ctx.strokeStyle='#58a6ff88';ctx.setLineDash([4,4]);
  ctx.fillStyle='#58a6ff';ctx.font='10px system-ui';ctx.textAlign='left';ctx.textBaseline='top';
  for(const dv of V.dividers){const x=X(dv.at);
    if(x>M.l+0.5){ctx.beginPath();ctx.moveTo(x,M.t);ctx.lineTo(x,H-M.b);ctx.stroke();}
    if(dv.label)ctx.fillText(' '+dv.label,x,M.t+1);}
  ctx.setLineDash([]);
}
function nearest(px){const d=(px-M.l)/plotW*dmax;let lo=0,hi=pts.length-1;
  while(lo<hi){const m=(lo+hi)>>1;if(pts[m].d<d)lo=m+1;else hi=m;}
  if(lo>0&&Math.abs(pts[lo-1].d-d)<Math.abs(pts[lo].d-d))lo--;return lo;}
function grade(i){const a=pts[Math.max(0,i-1)],b=pts[i],dd=b.d-a.d;
  return dd>0?((b.ele-a.ele)/dd*100):0;}
cv.addEventListener('mousemove',ev=>{
  const r=cv.getBoundingClientRect(),px=ev.clientX-r.left;
  if(px<M.l||px>W-M.r){tip.style.display='none';cursor.setStyle({opacity:0,fillOpacity:0});draw();return;}
  const i=nearest(px),p=pts[i];
  draw();
  ctx.strokeStyle='#fff8';ctx.beginPath();ctx.moveTo(X(p.d),M.t);ctx.lineTo(X(p.d),H-M.b);ctx.stroke();
  ctx.fillStyle='#f0a500';ctx.beginPath();ctx.arc(X(p.d),Y(p.ele),3.5,0,7);ctx.fill();
  cursor.setLatLng([p.lat,p.lon]).setStyle({opacity:1,fillOpacity:1});
  tip.style.display='block';
  tip.innerHTML=`${(p.d/1000).toFixed(1)} km<br>${p.ele.toFixed(0)} m<br>grade ${grade(i).toFixed(1)}%`;
  let tx=ev.clientX-r.left+12;if(tx>W-90)tx-=100;
  tip.style.left=tx+'px';tip.style.top=Math.max(0,ev.clientY-r.top-10)+'px';
});
cv.addEventListener('mouseleave',()=>{tip.style.display='none';
  cursor.setStyle({opacity:0,fillOpacity:0});draw();});
addEventListener('resize',resize);

// ---- tabs + select ----
function fmt(n){return (n>=0?'+':'−')+Math.abs(Math.round(n));}
function select(sel,btn){
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  const view=buildView(sel);
  const s=view.summary;
  document.getElementById('s-dist').textContent=s.distance_km.toFixed(1)+' km';
  document.getElementById('s-range').textContent=
    (sel==='all'?'':s.min_ele+'–'+s.max_ele+' m');
  document.getElementById('s-asc').textContent=fmt(s.ascent_m)+' m';
  document.getElementById('s-desc').textContent='−'+Math.abs(s.descent_m)+' m';
  renderMap(view);
  setView(view);
  setTimeout(()=>map.invalidateSize(),60);
}
const tabs=document.getElementById('tabs');
DAYS.forEach((d,i)=>{const b=document.createElement('button');b.textContent=d.day;
  b.onclick=()=>select(i,b);tabs.appendChild(b);});
if(DAYS.length>1){const b=document.createElement('button');b.textContent='Whole route';
  b.onclick=()=>select('all',b);tabs.appendChild(b);}
select(0,tabs.firstChild);

// ---- this device's live location (browser geolocation) ----
// Plots wherever the viewing phone/laptop is, as a blue "This device" dot with
// an accuracy ring, and keeps it updated. The dot is added straight to the map
// (not routeLayer) so switching day tabs never clears it. We don't auto-pan -
// the course stays framed; the button recenters on the dot on demand.
// Note: browsers only grant geolocation in a secure context (HTTPS, or
// localhost). Over a plain-http LAN address the button shows "Needs HTTPS".
let meMarker=null, meAcc=null, meWatch=null;
const locateBtn=document.getElementById('locate');
function showMe(lat,lon,acc){
  const ll=[lat,lon];
  if(!meMarker){
    meAcc=L.circle(ll,{radius:acc,color:'#4cc9f0',weight:1,
      fillColor:'#4cc9f0',fillOpacity:.12}).addTo(map);
    meMarker=L.circleMarker(ll,{radius:7,color:'#fff',weight:2,
      fillColor:'#4cc9f0',fillOpacity:1}).bindTooltip('This device').addTo(map);
  } else { meMarker.setLatLng(ll); meAcc.setLatLng(ll).setRadius(acc); }
}
function locFail(err){
  locateBtn.classList.remove('on'); locateBtn.classList.add('err');
  const insecure = location.protocol!=='https:'
    && !['localhost','127.0.0.1'].includes(location.hostname);
  if(err && err.code===1){ locateBtn.textContent='📍 Location blocked';
    locateBtn.title='Location permission was denied for this site.'; }
  else if(insecure){ locateBtn.textContent='📍 Needs HTTPS';
    locateBtn.title='Browsers only allow location over HTTPS (or localhost). '
      +'Open Home Assistant via HTTPS to plot this device.'; }
  else { locateBtn.textContent='📍 No location';
    locateBtn.title='Could not get a location fix.'; }
}
function startLocate(){
  if(!('geolocation' in navigator)){ locFail(null); return; }
  if(meWatch!=null) navigator.geolocation.clearWatch(meWatch);
  meWatch=navigator.geolocation.watchPosition(
    pos=>{ locateBtn.classList.remove('err'); locateBtn.classList.add('on');
      locateBtn.textContent='📍 Center on me'; locateBtn.title='Center the map on this device';
      showMe(pos.coords.latitude,pos.coords.longitude,pos.coords.accuracy||25); },
    locFail, {enableHighAccuracy:true,maximumAge:5000,timeout:20000});
}
locateBtn.onclick=()=>{
  if(meMarker) map.setView(meMarker.getLatLng(), Math.max(map.getZoom(),13));
  else startLocate();
};
startLocate();   // attempt on load; falls back gracefully if blocked
</script>
</body>
</html>
"""

open("viewer.html", "w", encoding="utf-8").write(HTML.replace("__DATA__", data))
print(f"Wrote viewer.html ({len(data)} bytes embedded from {src})")
