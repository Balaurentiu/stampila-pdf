#!/usr/bin/env python3
"""
PDF Ștampilă v2 - Aplică ștampile pe documente PDF.
- Ancorare pe text (căutare "CONTASIST" sau text personalizat)
- Detectare tabele + poziționare sub ele
- Mod manual cu click exact (fixat pentru landscape)
- Opacitate, scară, aplicare pe toate paginile
"""

import os, io, sys, time, socket, platform, subprocess, shutil, json
from flask import Flask, request, send_file, jsonify, render_template_string
import fitz
from PIL import Image
import numpy as np

app = Flask(__name__)

# Paths — writable in both dev and packaged (PyInstaller) mode
if getattr(sys, 'frozen', False):
    _APP_DATA = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'StampilaPDF')
else:
    _APP_DATA = os.path.dirname(os.path.abspath(__file__))

UPLOAD = os.path.join(_APP_DATA, 'uploads')
OUTPUT = os.path.join(_APP_DATA, 'output')
TEMP   = os.path.join(_APP_DATA, 'temp')
os.makedirs(UPLOAD, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
os.makedirs(TEMP,   exist_ok=True)


def find_stamp_anchors(page, anchor_text='Semnătură'):
    """Find position where stamp should be placed on a page.
    Simply searches for the anchor text and places stamp below its last occurrence.
    Returns list with one (center_x, y_bottom) tuple, or empty list if not found."""
    results = page.search_for(anchor_text)
    if not results:
        return []  # Anchor text not found on this page
    
    # Use the last occurrence on the page
    last = results[-1]
    center_x = (last.x0 + last.x1) / 2
    y_bottom = last.y1 + 2  # just below the text
    return [(center_x, y_bottom)]


def detect_tables(page):
    """Detect table-like structures by finding clusters of horizontal lines."""
    paths = page.get_drawings()
    h_lines = {}
    for p in paths:
        for item in p.get("items", []):
            if item[0] == "l":
                s, e = item[1], item[2]
                if abs(s.y - e.y) < 2 and abs(s.x - e.x) > 50:
                    y = round(s.y, 0)
                    h_lines.setdefault(y, []).append((min(s.x, e.x), max(s.x, e.x)))

    if len(h_lines) < 3:
        return []

    ys = sorted(h_lines.keys())
    clusters, cur = [], [ys[0]]
    for i in range(1, len(ys)):
        if ys[i] - ys[i-1] < 30:
            cur.append(ys[i])
        else:
            if len(cur) >= 3:
                clusters.append(cur)
            cur = [ys[i]]
    if len(cur) >= 3:
        clusters.append(cur)

    tables = []
    for cl in clusters:
        y0, y1 = min(cl), max(cl)
        x0 = min(lx0 for y in cl for lx0, _ in h_lines[y])
        x1 = max(lx1 for y in cl for _, lx1 in h_lines[y])
        tables.append((x0, y0, x1, y1))
    return tables


def apply_stamp(pdf_bytes, stamp_bytes, mode='text', scale=0.75, margin=15,
                manual_x=None, manual_y=None, manual_page=None, all_pages=False,
                anchor_text='Semnătură', opacity=0.5, rotation=0,
                manual_positions=None):
    """Returns (pdf_bytes, total_stamps, warnings_list).
    warnings_list contains messages for pages where anchor was not found.

    manual_positions: optional dict {page_index: (x, y)} for placing the stamp
    at a different, individually chosen position on each page (manual mode)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Prepare stamp image
    stamp_img = Image.open(io.BytesIO(stamp_bytes))

    # Apply rotation (expand=True keeps full image visible after rotation)
    if rotation % 360 != 0:
        stamp_img = stamp_img.convert("RGBA")
        stamp_img = stamp_img.rotate(rotation, expand=True)

    # Apply opacity
    if opacity < 1.0:
        stamp_img = stamp_img.convert("RGBA")
        d = list(stamp_img.getdata())
        stamp_img.putdata([(r, g, b, int(a * opacity)) for r, g, b, a in d])
    
    buf = io.BytesIO()
    stamp_img.save(buf, format="PNG")
    buf.seek(0)
    stamp_data = buf.getvalue()

    w_px, h_px = stamp_img.size
    target_w = 140 * scale  # 140pt base width (close to original stamp size)
    sf = target_w / w_px
    sw, sh = w_px * sf, h_px * sf

    # Individual manual mode: a different (x, y) per page.
    if mode == 'manual' and manual_positions:
        total = 0
        warnings = []
        for pi in sorted(manual_positions.keys()):
            if pi < 0 or pi >= len(doc):
                continue
            mxp, myp = manual_positions[pi]
            page = doc[pi]
            rect = fitz.Rect(mxp, myp, mxp + sw, myp + sh)
            page.insert_image(rect, stream=stamp_data)
            total += 1
        out = io.BytesIO()
        doc.save(out)
        doc.close()
        out.seek(0)
        return out.getvalue(), total, warnings

    pages = list(range(len(doc))) if all_pages else ([manual_page if manual_page is not None else 0])
    total = 0
    warnings = []

    for pi in pages:
        if pi >= len(doc):
            continue
        page = doc[pi]
        
        if mode == 'manual' and manual_x is not None and manual_y is not None:
            # Manual mode - coordinates are in PDF points (corrected for page dimensions)
            rect = fitz.Rect(manual_x, manual_y, manual_x + sw, manual_y + sh)
            page.insert_image(rect, stream=stamp_data)
            total += 1
            
        elif mode == 'text' and anchor_text:
            # Find anchor positions using Cenzor/CONTASIST detection
            found = find_stamp_anchors(page, anchor_text)
            
            if found:
                for (ax, ay) in found:
                    # Center stamp horizontally on the anchor point
                    stamp_x = ax - sw / 2
                    stamp_y = ay
                    
                    if stamp_x < 2:
                        stamp_x = 2
                    if stamp_x + sw > page.rect.width - 2:
                        stamp_x = page.rect.width - sw - 2
                    
                    rect = fitz.Rect(stamp_x, stamp_y, stamp_x + sw, stamp_y + sh)
                    page.insert_image(rect, stream=stamp_data)
                    total += 1
            else:
                # No anchor found - skip this page, add warning
                warnings.append(f'Pagina {pi+1}: textul \"{anchor_text}\" nu a fost gasit. Stampila NU a fost aplicata.')
                total += 1
                
        elif mode == 'table':
            tables = detect_tables(page)
            if tables:
                for t in tables:
                    x0, y0, x1, y1 = t
                    stamp_x = x0 + (x1 - x0 - sw) / 2
                    stamp_y = y1 + margin
                    if stamp_y + sh > page.rect.height:
                        stamp_y = page.rect.height - sh - margin
                    rect = fitz.Rect(stamp_x, stamp_y, stamp_x + sw, stamp_y + sh)
                    page.insert_image(rect, stream=stamp_data)
                    total += 1
            else:
                # No tables found - skip, add warning
                warnings.append(f'Pagina {pi+1}: nu s-au gasit tabele. Stampila NU a fost aplicata.')

    out = io.BytesIO()
    doc.save(out)
    doc.close()
    out.seek(0)
    return out.getvalue(), total, warnings


def preview_page(pdf_bytes, page_num=0):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_num >= len(doc):
        page_num = 0
    page = doc[page_num]
    pw = page.rect.width
    ph = page.rect.height
    # Higher DPI for better preview
    dpi = 150
    mat = fitz.Matrix(dpi/72, dpi/72)
    pix = page.get_pixmap(matrix=mat)
    data = pix.tobytes("png")
    doc.close()
    return data, pw, ph


HTML = '''<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PDF Ștampilă</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:20px}
h1{text-align:center;color:#f8fafc;font-size:1.8em;margin-bottom:8px}
.sub{text-align:center;color:#94a3b8;margin-bottom:28px;font-size:.92em}
.C{max-width:950px;margin:0 auto}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:22px;margin-bottom:18px}
.card h2{color:#38bdf8;margin-bottom:14px;font-size:1.15em}
.uz{border:2px dashed #475569;border-radius:8px;padding:28px;text-align:center;cursor:pointer;transition:.3s;margin-bottom:10px}
.uz:hover,.uz.dg{border-color:#38bdf8;background:rgba(56,189,248,.05)}
.uz.ok{border-color:#22c55e;background:rgba(34,197,94,.05)}
.uz .ic{font-size:2em;margin-bottom:6px}
.uz .lb{color:#94a3b8}
.uz .fn{color:#22c55e;font-weight:600;margin-top:5px}
input[type=file]{display:none}
.opts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}
.og label{display:block;color:#94a3b8;font-size:.84em;margin-bottom:3px}
.og input,.og select{width:100%;padding:8px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:.93em}
.cbg{display:flex;align-items:center;gap:8px;margin-top:14px}
.cbg input[type=checkbox]{width:18px;height:18px;accent-color:#38bdf8}
.btn{display:block;width:100%;padding:13px;border:none;border-radius:8px;font-size:1.05em;font-weight:600;cursor:pointer;transition:.2s;text-align:center}
.bp{background:#2563eb;color:#fff}.bp:hover{background:#1d4ed8}.bp:disabled{background:#475569;cursor:not-allowed}
.bs{background:#16a34a;color:#fff}.bs:hover{background:#15803d}
.mt{display:flex;gap:6px;margin:14px 0;flex-wrap:wrap}
.mb{flex:1;min-width:140px;padding:10px 8px;border:2px solid #334155;border-radius:8px;background:0;color:#94a3b8;cursor:pointer;text-align:center;font-size:.85em;transition:.2s}
.mb.ac{border-color:#38bdf8;color:#38bdf8;background:rgba(56,189,248,.1)}
.tc{max-width:100%;border:1px solid #334155;border-radius:8px;cursor:crosshair;background:#fff;display:block;margin:10px auto}
.pa{margin-top:14px;text-align:center}
.st{padding:10px 14px;border-radius:8px;margin-top:10px;font-size:.93em}
.si{background:rgba(56,189,248,.1);border:1px solid rgba(56,189,248,.3);color:#38bdf8}
.ss{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);color:#22c55e}
.se{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#ef4444}
.row{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:700px){.row{grid-template-columns:1fr}.opts{grid-template-columns:1fr}}
.info{background:rgba(250,204,21,.1);border:1px solid rgba(250,204,21,.3);color:#fbbf24;padding:10px 14px;border-radius:8px;margin-top:10px;font-size:.9em}
.highlight{position:absolute;border:2px solid #22c55e;background:rgba(34,197,94,.2);pointer-events:none;border-radius:4px}
</style>
</head>
<body>
<div class="C">
<h1>📄 PDF Ștampilă v2</h1>
<p class="sub">Aplică ștampile pe documente PDF — sub text ancoră, sub tabele sau manual</p>

<div class="card">
<h2>1. Încarcă fișierele</h2>
<div class="row">
<div><div class="uz" id="pz" onclick="document.getElementById('pi').click()">
<div class="ic">📄</div><div class="lb">Document PDF</div><div class="fn" id="pf"></div></div>
<input type="file" id="pi" accept=".pdf"></div>
<div><div class="uz" id="sz" onclick="document.getElementById('si').click()">
<div class="ic">🔘</div><div class="lb">Imagine ștampilă (PNG transparent)</div><div class="fn" id="sf"></div></div>
<input type="file" id="si" accept="image/*"></div>
</div></div>

<div class="card">
<h2>2. Mod de poziționare</h2>
<div class="mt">
<button class="mb ac" id="m-txt" onclick="sm('text')">🔍 Sub text ancoră</button>
<button class="mb" id="m-tbl" onclick="sm('table')">📊 Sub tabele</button>
<button class="mb" id="m-man" onclick="sm('manual')">👆 Manual (click)</button>
</div>
<div id="txt-opts" style="margin-top:12px">
<div class="og" style="max-width:400px">
<label>Text ancoră (caută sub acest text)</label>
<input type="text" id="anchor" value="Semnătură" placeholder="Ex: Semnătură, VIZAT, Director...">
</div>
<div class="info" id="anchor-info">Caută textul din câmpul de mai sus și plasează ștampila sub textul găsit. Dacă nu găsește textul, afișează mesaj de înștiințare.</div>
</div>
<div id="man-opts" style="display:none;margin-top:8px">
<div class="info">👆 Click pe preview pentru a poziționa ștampila. Coordonatele sunt în puncte PDF (corectate pentru landscape).</div>
<div class="cbg"><input type="checkbox" id="indiv" onchange="ti()"><label for="indiv">Poziționare individuală pe fiecare pagină</label></div>
<div class="info" id="indiv-info" style="display:none">📑 Pentru fiecare pagină: alege pagina din „Pagină preview", generează preview-ul și dă click pentru a plasa ștampila. Pozițiile se rețin separat pentru fiecare pagină. La final, exportă documentul complet ștampilat.</div>
<div id="pos-status" style="margin-top:8px"></div>
</div>
<div class="opts" style="margin-top:14px">
<div class="og"><label>Scară ștampilă</label><select id="sc">
<option value="0.4">Foarte mică (0.4x)</option>
<option value="0.6">Mică (0.6x)</option>
<option value="0.8" selected>Medie (0.8x)</option>
<option value="1">Normală (1x)</option>
<option value="1.3">Mare (1.3x)</option>
<option value="1.6">Foarte mare (1.6x)</option></select></div>
<div class="og"><label>Pagină preview</label><select id="pp"><option value="0">Pagina 1</option></select></div>
<div class="og"><label>Margin sub ancoră (pt)</label><input type="number" id="mg" value="8" min="0" max="100"></div>
<div class="og"><label>Opacitate ștampilă</label><select id="op">
<option value="1" selected>100% (opac)</option><option value="0.85">85%</option>
<option value="0.7">70%</option><option value="0.5">50%</option><option value="0.3">30%</option></select></div>
<div class="og"><label>Rotație ștampilă</label><select id="rt">
<option value="0" selected>0° (normal)</option>
<option value="15">15°</option>
<option value="30">30°</option>
<option value="45">45°</option>
<option value="90">90°</option>
<option value="180">180°</option>
<option value="270">270°</option>
<option value="315">315°</option>
<option value="345">345°</option></select></div>
</div>
<div class="cbg"><input type="checkbox" id="ap"><label for="ap">Aplică pe TOATE paginile</label></div>
</div>

<div class="card">
<h2>3. Preview & Aplică</h2>
<button class="btn bp" id="pb" onclick="gp()" disabled>👁️ Generează Preview</button>
<div class="pa" id="pva" style="display:none;position:relative">
<canvas id="cv" class="tc"></canvas>
<div id="hl"></div>
</div>
<div id="sa"></div>
<button class="btn bs" id="ab" onclick="as()" style="display:none;margin-top:12px">✅ Aplică Ștampila & Descarcă</button>
</div>
</div>

<script>
let PF=null,SF=null,MD='text',MX=null,MY=null,PW=0,PH=0;
let INDIV=false,POS={},CURPG=0;
document.getElementById('pi').onchange=e=>{PF=e.target.files[0];document.getElementById('pz').classList.add('ok');document.getElementById('pf').textContent=PF?PF.name:'';up();cr()};
document.getElementById('si').onchange=e=>{SF=e.target.files[0];document.getElementById('sz').classList.add('ok');document.getElementById('sf').textContent=SF?SF.name:'';cr()};
['pz','sz'].forEach(id=>{let z=document.getElementById(id);z.ondragover=e=>{e.preventDefault();z.classList.add('dg')};z.ondragleave=()=>z.classList.remove('dg');z.ondrop=e=>{e.preventDefault();z.classList.remove('dg');let inp=id==='pz'?'pi':'si';document.getElementById(inp).files=e.dataTransfer.files;document.getElementById(inp).dispatchEvent(new Event('change'))}});
document.getElementById('pp').onchange=()=>{if(MD==='manual'&&INDIV&&PF&&SF)gp()};

function sm(m){
  MD=m;
  document.getElementById('m-txt').classList.toggle('ac',m==='text');
  document.getElementById('m-tbl').classList.toggle('ac',m==='table');
  document.getElementById('m-man').classList.toggle('ac',m==='manual');
  document.getElementById('txt-opts').style.display=m==='text'?'block':'none';
  document.getElementById('man-opts').style.display=m==='manual'?'block':'none';
  MX=null;MY=null;
}

function ti(){
  INDIV=document.getElementById('indiv').checked;
  document.getElementById('indiv-info').style.display=INDIV?'block':'none';
  // "Aplică pe TOATE paginile" nu are sens împreună cu poziționarea individuală
  let ap=document.getElementById('ap');
  ap.disabled=INDIV;
  if(INDIV)ap.checked=false;
  POS={};MX=null;MY=null;
  updPosStatus();
}

function updPosStatus(){
  let el=document.getElementById('pos-status');
  if(!INDIV){el.innerHTML='';return}
  let keys=Object.keys(POS).map(Number).sort((a,b)=>a-b);
  if(keys.length===0){el.innerHTML='<div class="st si">Nicio pagină poziționată încă.</div>';return}
  let list=keys.map(k=>(k+1)).join(', ');
  el.innerHTML='<div class="st ss">✅ Pagini poziționate ('+keys.length+'): '+list+'</div>';
}

function cr(){document.getElementById('pb').disabled=!(PF&&SF)}

async function up(){
  if(!PF)return;
  let f=new FormData();f.append('pdf',PF);
  try{
    let r=await fetch('/api/page-count',{method:'POST',body:f});
    let d=await r.json();
    let s=document.getElementById('pp');s.innerHTML='';
    for(let i=0;i<d.pages;i++){let o=document.createElement('option');o.value=i;o.textContent='Pagina '+(i+1);s.appendChild(o)}
    if(d.orientation)document.getElementById('sa').innerHTML='<div class="st si">Orientare: '+d.orientation+'</div>';
  }catch(e){console.error(e)}
}

async function gp(){
  if(!PF||!SF)return;
  let f=new FormData();
  f.append('pdf',PF);f.append('stamp',SF);
  f.append('page',document.getElementById('pp').value);
  f.append('scale',document.getElementById('sc').value);
  f.append('margin',document.getElementById('mg').value);
  f.append('opacity',document.getElementById('op').value);
  f.append('rotation',document.getElementById('rt').value);
  f.append('mode',MD);
  f.append('anchor',document.getElementById('anchor').value);
  CURPG=parseInt(document.getElementById('pp').value)||0;
  if(MD==='manual'&&INDIV){MX=POS[CURPG]?POS[CURPG].x:null;MY=POS[CURPG]?POS[CURPG].y:null;}
  if(MD==='manual'&&MX!==null){f.append('manual_x',MX);f.append('manual_y',MY)}

  document.getElementById('pb').disabled=true;
  document.getElementById('pb').textContent='⏳ Generare...';
  try{
    let r=await fetch('/api/preview',{method:'POST',body:f});
    if(!r.ok)throw new Error('Eroare la generare preview');
    // Get page dimensions from header
    PW=parseFloat(r.headers.get('X-Page-Width')||612);
    PH=parseFloat(r.headers.get('X-Page-Height')||792);
    let blob=await r.blob();
    let area=document.getElementById('pva');area.style.display='block';
    let cv=document.getElementById('cv'),ctx=cv.getContext('2d'),img=new Image();
    img.onload=()=>{
      cv.width=img.width;cv.height=img.height;
      ctx.drawImage(img,0,0);
      let scale=img.width/PW;
      let drawMarker=()=>{
        ctx.drawImage(img,0,0);
        if(MX===null||MY===null)return;
        let stW=150*parseFloat(document.getElementById('sc').value)*scale;
        let stH=60*scale;
        ctx.fillStyle='rgba(56,189,248,.25)';ctx.strokeStyle='#38bdf8';ctx.lineWidth=2;
        ctx.fillRect(MX*scale,MY*scale,stW,stH);
        ctx.strokeRect(MX*scale,MY*scale,stW,stH);
      };
      // If this page already has a stored position (individual mode), show it
      if(MD==='manual'&&INDIV&&POS[CURPG]){MX=POS[CURPG].x;MY=POS[CURPG].y;drawMarker();}
      cv.onclick=e=>{
        if(MD!=='manual')return;
        let rect=cv.getBoundingClientRect();
        let sx=cv.width/rect.width,sy=cv.height/rect.height;
        // Convert click to PDF points (respecting actual page dimensions)
        MX=(e.clientX-rect.left)*sx/scale;
        MY=(e.clientY-rect.top)*sy/scale;
        drawMarker();
        if(INDIV){
          POS[CURPG]={x:MX,y:MY};
          updPosStatus();
          ss('info','Pagina '+(CURPG+1)+' poziționată: X='+Math.round(MX)+', Y='+Math.round(MY)+' pt. Poți trece la pagina următoare.');
        }else{
          ss('info','Poziție: X='+Math.round(MX)+', Y='+Math.round(MY)+' pt (pagină '+PW.toFixed(0)+'×'+PH.toFixed(0)+')');
        }
      };
    };
    img.src=URL.createObjectURL(blob);
    document.getElementById('ab').style.display='block';
    let warns=r.headers.get('X-Warnings');
    let msg='Preview generat. Verifică poziția ștampilei.';
    if(warns){msg+='<br>⚠️ '+warns.split(' | ').join('<br>⚠️ ');}
    ss('info',msg);
  }catch(e){ss('error','Eroare: '+e.message)}
  document.getElementById('pb').disabled=false;
  document.getElementById('pb').textContent='👁️ Generează Preview';
}

async function as(){
  if(!PF||!SF)return;
  let f=new FormData();
  f.append('pdf',PF);f.append('stamp',SF);
  f.append('scale',document.getElementById('sc').value);
  f.append('margin',document.getElementById('mg').value);
  f.append('opacity',document.getElementById('op').value);
  f.append('rotation',document.getElementById('rt').value);
  f.append('mode',MD);
  f.append('anchor',document.getElementById('anchor').value);
  f.append('all_pages',document.getElementById('ap').checked?'1':'0');
  f.append('page',document.getElementById('pp').value);
  if(MD==='manual'&&INDIV){
    if(Object.keys(POS).length===0){ss('error','Nu ai poziționat nicio pagină. Alege o pagină, generează preview și dă click pentru a plasa ștampila.');return}
    f.append('positions',JSON.stringify(POS));
  }else if(MD==='manual'&&MX!==null){f.append('manual_x',MX);f.append('manual_y',MY)}

  document.getElementById('ab').textContent='⏳ Procesare...';
  document.getElementById('ab').disabled=true;
  const isDesktop=typeof window.pywebview!=='undefined';
  try{
    if(isDesktop){
      // Step 1: process PDF on server, get token
      let r=await fetch('/api/apply-prepare',{method:'POST',body:f});
      if(!r.ok)throw new Error('Eroare la procesare');
      let d=await r.json();
      if(!d.ok)throw new Error(d.error||'Eroare necunoscuta');
      // Step 2: open native Save As dialog via PyWebView API
      ss('info','Alege locația de salvare...');
      let res=await window.pywebview.api.save_dialog(d.token, d.suggested);
      if(res.cancelled){ss('info','Salvare anulată.');
      }else if(res.ok){
        let msg='✅ Documentul a fost salvat!';
        if(d.stamps)msg+=' ('+d.stamps+' ștampilă/e)';
        if(d.warnings&&d.warnings.length)msg+='<br>⚠️ '+d.warnings.join('<br>⚠️ ');
        msg+='<br><small>📁 '+res.path+'</small>';
        ss('success',msg);
      }else{throw new Error(res.error||'Eroare la salvare');}
    }else{
      let r=await fetch('/api/apply',{method:'POST',body:f});
      if(!r.ok)throw new Error('Eroare la aplicare');
      let warns=r.headers.get('X-Warnings');
      let stamps=r.headers.get('X-Stamps-Applied');
      let blob=await r.blob();
      let url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download=PF.name.replace('.pdf','')+'_stampilat.pdf';a.click();
      URL.revokeObjectURL(url);
      let msg='✅ Documentul cu ștampilă a fost descărcat!';
      if(stamps)msg+=' ('+stamps+' ștampilă/e aplicată/e)';
      if(warns){msg+='<br>⚠️ '+warns.split(' | ').join('<br>⚠️ ');}
      ss('success',msg);
    }
  }catch(e){ss('error','Eroare: '+e.message)}
  document.getElementById('ab').textContent='✅ Aplică Ștampila & Descarcă';
  document.getElementById('ab').disabled=false;
}

function ss(t,m){document.getElementById('sa').innerHTML='<div class="st s'+t[0]+'">'+m+'</div>'}
</script>
</body>
</html>'''


def parse_manual_positions(form):
    """Parse the 'positions' form field (JSON {page_index: {x, y}})
    into a dict {int page_index: (float x, float y)}, or None if absent/invalid."""
    raw = form.get('positions')
    if not raw:
        return None
    try:
        data = json.loads(raw)
        result = {}
        for k, v in data.items():
            result[int(k)] = (float(v['x']), float(v['y']))
        return result or None
    except (ValueError, TypeError, KeyError):
        return None


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/api/page-count', methods=['POST'])
def page_count():
    f = request.files.get('pdf')
    if not f:
        return jsonify({'error': 'No PDF'}), 400
    doc = fitz.open(stream=f.read(), filetype="pdf")
    n = len(doc)
    # Detect orientation
    page = doc[0]
    orient = 'landscape' if page.rect.width > page.rect.height else 'portrait'
    doc.close()
    return jsonify({'pages': n, 'orientation': orient})


@app.route('/api/preview', methods=['POST'])
def preview():
    pf = request.files.get('pdf')
    sf = request.files.get('stamp')
    if not pf or not sf:
        return jsonify({'error': 'Missing files'}), 400
    
    pdf_b, stamp_b = pf.read(), sf.read()
    pg = int(request.form.get('page', 0))
    sc = float(request.form.get('scale', 0.8))
    mg = float(request.form.get('margin', 8))
    op = float(request.form.get('opacity', 0.5))
    md = request.form.get('mode', 'text')
    anchor = request.form.get('anchor', 'CONTASIST')
    mx = request.form.get('manual_x', type=float)
    my = request.form.get('manual_y', type=float)
    rot = int(request.form.get('rotation', 0))

    result_bytes, n, warnings = apply_stamp(pdf_b, stamp_b, mode=md, scale=sc, margin=mg,
                                   manual_x=mx, manual_y=my, manual_page=pg,
                                   all_pages=False, anchor_text=anchor, opacity=op, rotation=rot)
    
    # Generate preview
    pv_data, pw, ph = preview_page(result_bytes, pg)
    
    response = send_file(io.BytesIO(pv_data), mimetype='image/png')
    response.headers['X-Page-Width'] = str(pw)
    response.headers['X-Page-Height'] = str(ph)
    response.headers['X-Warnings'] = ' | '.join(warnings).encode('ascii', 'replace').decode('ascii') if warnings else ''
    return response


@app.route('/api/apply', methods=['POST'])
def apply():
    pf = request.files.get('pdf')
    sf = request.files.get('stamp')
    if not pf or not sf:
        return jsonify({'error': 'Missing files'}), 400
    
    pdf_b, stamp_b = pf.read(), sf.read()
    sc = float(request.form.get('scale', 0.8))
    mg = float(request.form.get('margin', 8))
    op = float(request.form.get('opacity', 0.5))
    md = request.form.get('mode', 'text')
    anchor = request.form.get('anchor', 'CONTASIST')
    ap = request.form.get('all_pages', '0') == '1'
    pg = int(request.form.get('page', 0))
    mx = request.form.get('manual_x', type=float)
    my = request.form.get('manual_y', type=float)
    rot = int(request.form.get('rotation', 0))
    positions = parse_manual_positions(request.form)

    result_bytes, n, warnings = apply_stamp(pdf_b, stamp_b, mode=md, scale=sc, margin=mg,
                                   manual_x=mx, manual_y=my, manual_page=pg,
                                   all_pages=ap, anchor_text=anchor, opacity=op, rotation=rot,
                                   manual_positions=positions)

    name = (pf.filename or 'document').removesuffix('.pdf')
    response = send_file(io.BytesIO(result_bytes), mimetype='application/pdf',
                     as_attachment=True, download_name=f'{name}_stampilat.pdf')
    response.headers['X-Warnings'] = ' | '.join(warnings).encode('ascii', 'replace').decode('ascii') if warnings else ''
    response.headers['X-Stamps-Applied'] = str(n)
    return response


REPORT_DIR = os.path.join(_APP_DATA, 'reports')
os.makedirs(REPORT_DIR, exist_ok=True)

REPORT_HTML = '''<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Raportare erori - PDF Ștampilă</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:24px}
h1{text-align:center;color:#f8fafc;font-size:1.6em;margin-bottom:6px}
.sub{text-align:center;color:#94a3b8;margin-bottom:28px;font-size:.9em}
.C{max-width:860px;margin:0 auto}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:22px;margin-bottom:18px}
.card h2{color:#38bdf8;margin-bottom:14px;font-size:1.05em}
.dz{border:2px dashed #475569;border-radius:8px;padding:32px;text-align:center;cursor:pointer;transition:.3s}
.dz:hover,.dz.dg{border-color:#38bdf8;background:rgba(56,189,248,.05)}
.dz .ic{font-size:2.2em;margin-bottom:8px}
.dz .lb{color:#94a3b8;font-size:.9em}
input[type=file]{display:none}
textarea{width:100%;padding:10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:.93em;resize:vertical;min-height:90px;margin-top:12px}
.btn{display:block;width:100%;padding:12px;border:none;border-radius:8px;font-size:1em;font-weight:600;cursor:pointer;background:#2563eb;color:#fff;margin-top:14px;transition:.2s}
.btn:hover{background:#1d4ed8}.btn:disabled{background:#475569;cursor:not-allowed}
.st{padding:10px 14px;border-radius:8px;margin-top:12px;font-size:.9em}
.si{background:rgba(56,189,248,.1);border:1px solid rgba(56,189,248,.3);color:#38bdf8}
.ss{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);color:#22c55e}
.se{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#ef4444}
.previews{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.previews img{max-height:120px;border-radius:6px;border:1px solid #334155}
.reports{display:flex;flex-direction:column;gap:14px}
.rep{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px}
.rep .meta{color:#64748b;font-size:.8em;margin-bottom:8px}
.rep .msg{color:#e2e8f0;font-size:.92em;margin-bottom:10px;white-space:pre-wrap}
.rep-imgs{display:flex;flex-wrap:wrap;gap:8px}
.rep-imgs img{max-height:200px;border-radius:6px;border:1px solid #334155;cursor:pointer}
.rep-imgs img:hover{border-color:#38bdf8}
.empty{color:#475569;text-align:center;padding:20px;font-size:.9em}
.back{display:inline-block;margin-bottom:18px;color:#38bdf8;text-decoration:none;font-size:.9em}
.back:hover{text-decoration:underline}
</style>
</head>
<body>
<div class="C">
<a href="/" class="back">← Înapoi la aplicație</a>
<h1>🐛 Raportare erori</h1>
<p class="sub">Trimite screenshot-uri cu erorile întâlnite</p>

<div class="card">
<h2>Trimite un raport nou</h2>
<div class="dz" id="dz" onclick="document.getElementById('fi').click()">
  <div class="ic">🖼️</div>
  <div class="lb">Click sau trage screenshot-urile aici (PNG, JPG)</div>
</div>
<input type="file" id="fi" accept="image/*" multiple>
<div class="previews" id="pv"></div>
<textarea id="msg" placeholder="Descrie eroarea (opțional): ce ai făcut, ce s-a întâmplat..."></textarea>
<button class="btn" id="sb" onclick="send()">📤 Trimite raport</button>
<div id="st"></div>
</div>

<div class="card">
<h2>Rapoarte primite</h2>
<div class="reports" id="rl">{{REPORTS}}</div>
</div>
</div>

<script>
let FILES=[];
let fi=document.getElementById('fi');
let dz=document.getElementById('dz');

fi.onchange=e=>{addFiles(e.target.files)};
dz.ondragover=e=>{e.preventDefault();dz.classList.add('dg')};
dz.ondragleave=()=>dz.classList.remove('dg');
dz.ondrop=e=>{e.preventDefault();dz.classList.remove('dg');addFiles(e.dataTransfer.files)};

function addFiles(list){
  for(let f of list){FILES.push(f)}
  let pv=document.getElementById('pv');pv.innerHTML='';
  FILES.forEach(f=>{let r=new FileReader();r.onload=e=>{let img=document.createElement('img');img.src=e.target.result;pv.appendChild(img)};r.readAsDataURL(f)});
}

async function send(){
  if(FILES.length===0){st('error','Adaugă cel puțin un screenshot.');return}
  let fd=new FormData();
  FILES.forEach(f=>fd.append('imgs',f));
  fd.append('msg',document.getElementById('msg').value);
  document.getElementById('sb').disabled=true;
  document.getElementById('sb').textContent='⏳ Se trimite...';
  try{
    let r=await fetch('/report',{method:'POST',body:fd});
    let d=await r.json();
    if(d.ok){st('success','✅ Raport trimis! Mulțumesc.');FILES=[];document.getElementById('pv').innerHTML='';document.getElementById('msg').value='';setTimeout(()=>location.reload(),1200);}
    else{st('error','Eroare: '+d.error);}
  }catch(e){st('error','Eroare rețea: '+e.message)}
  document.getElementById('sb').disabled=false;
  document.getElementById('sb').textContent='📤 Trimite raport';
}

function st(t,m){document.getElementById('st').innerHTML='<div class="st s'+t[0]+'">'+m+'</div>'}
</script>
</body>
</html>'''


@app.route('/report', methods=['GET', 'POST'])
def report():
    if request.method == 'POST':
        imgs = request.files.getlist('imgs')
        msg = request.form.get('msg', '').strip()
        if not imgs:
            return jsonify({'ok': False, 'error': 'Nicio imagine'}), 400
        ts = time.strftime('%Y%m%d_%H%M%S')
        rep_dir = os.path.join(REPORT_DIR, ts)
        os.makedirs(rep_dir, exist_ok=True)
        for i, img in enumerate(imgs):
            ext = os.path.splitext(img.filename)[1] or '.png'
            img.save(os.path.join(rep_dir, f'img{i}{ext}'))
        if msg:
            with open(os.path.join(rep_dir, 'msg.txt'), 'w', encoding='utf-8') as f:
                f.write(msg)
        return jsonify({'ok': True})

    # Build reports list HTML
    reps_html = ''
    if os.path.exists(REPORT_DIR):
        entries = sorted(os.listdir(REPORT_DIR), reverse=True)
        for entry in entries:
            ep = os.path.join(REPORT_DIR, entry)
            if not os.path.isdir(ep):
                continue
            msg_file = os.path.join(ep, 'msg.txt')
            msg_text = open(msg_file, encoding='utf-8').read() if os.path.exists(msg_file) else ''
            imgs_html = ''
            for fn in sorted(os.listdir(ep)):
                if fn.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                    imgs_html += f'<img src="/report/img/{entry}/{fn}" onclick="window.open(this.src)">'
            ts_fmt = f"{entry[6:8]}.{entry[4:6]}.{entry[:4]} {entry[9:11]}:{entry[11:13]}:{entry[13:15]}"
            reps_html += f'''<div class="rep">
<div class="meta">📅 {ts_fmt}</div>
{"<div class='msg'>"+msg_text+"</div>" if msg_text else ""}
<div class="rep-imgs">{imgs_html}</div>
</div>'''
    if not reps_html:
        reps_html = '<div class="empty">Niciun raport încă.</div>'
    return render_template_string(REPORT_HTML.replace('{{REPORTS}}', reps_html))


@app.route('/report/img/<ts>/<filename>')
def report_img(ts, filename):
    safe_ts = os.path.basename(ts)
    safe_fn = os.path.basename(filename)
    path = os.path.join(REPORT_DIR, safe_ts, safe_fn)
    if not os.path.exists(path):
        return '', 404
    return send_file(path)


# Temp files waiting for save dialog { token -> tmp_path }
_pending_files = {}


def open_file_os(path):
    if platform.system() == 'Windows':
        os.startfile(path)
    elif platform.system() == 'Darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


@app.route('/api/apply-prepare', methods=['POST'])
def apply_prepare():
    """Desktop mode step 1: process PDF, save to temp file, return token for save dialog."""
    pf = request.files.get('pdf')
    sf = request.files.get('stamp')
    if not pf or not sf:
        return jsonify({'error': 'Missing files'}), 400

    pdf_b, stamp_b = pf.read(), sf.read()
    sc = float(request.form.get('scale', 0.8))
    mg = float(request.form.get('margin', 8))
    op = float(request.form.get('opacity', 1.0))
    md = request.form.get('mode', 'text')
    anchor = request.form.get('anchor', 'CONTASIST')
    ap = request.form.get('all_pages', '0') == '1'
    pg = int(request.form.get('page', 0))
    mx = request.form.get('manual_x', type=float)
    my = request.form.get('manual_y', type=float)
    rot = int(request.form.get('rotation', 0))
    positions = parse_manual_positions(request.form)

    result_bytes, n, warnings = apply_stamp(pdf_b, stamp_b, mode=md, scale=sc, margin=mg,
                                   manual_x=mx, manual_y=my, manual_page=pg,
                                   all_pages=ap, anchor_text=anchor, opacity=op, rotation=rot,
                                   manual_positions=positions)

    token = str(int(time.time() * 1000))
    tmp = os.path.join(TEMP, f'pending_{token}.pdf')
    with open(tmp, 'wb') as f:
        f.write(result_bytes)
    _pending_files[token] = tmp

    name = (pf.filename or 'document').removesuffix('.pdf') + '_stampilat.pdf'
    return jsonify({'ok': True, 'token': token, 'suggested': name,
                    'stamps': n, 'warnings': warnings})


@app.route('/api/apply-save', methods=['POST'])
def apply_save():
    """Desktop mode: saves stamped PDF to Downloads folder and opens it."""
    pf = request.files.get('pdf')
    sf = request.files.get('stamp')
    if not pf or not sf:
        return jsonify({'error': 'Missing files'}), 400

    pdf_b, stamp_b = pf.read(), sf.read()
    sc = float(request.form.get('scale', 0.8))
    mg = float(request.form.get('margin', 8))
    op = float(request.form.get('opacity', 1.0))
    md = request.form.get('mode', 'text')
    anchor = request.form.get('anchor', 'CONTASIST')
    ap = request.form.get('all_pages', '0') == '1'
    pg = int(request.form.get('page', 0))
    mx = request.form.get('manual_x', type=float)
    my = request.form.get('manual_y', type=float)
    rot = int(request.form.get('rotation', 0))
    positions = parse_manual_positions(request.form)

    result_bytes, n, warnings = apply_stamp(pdf_b, stamp_b, mode=md, scale=sc, margin=mg,
                                   manual_x=mx, manual_y=my, manual_page=pg,
                                   all_pages=ap, anchor_text=anchor, opacity=op, rotation=rot,
                                   manual_positions=positions)

    name = (pf.filename or 'document').removesuffix('.pdf')
    downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
    os.makedirs(downloads, exist_ok=True)
    out_path = os.path.join(downloads, f'{name}_stampilat.pdf')
    with open(out_path, 'wb') as f:
        f.write(result_bytes)

    open_file_os(out_path)

    return jsonify({'ok': True, 'path': out_path, 'stamps': n,
                    'warnings': warnings})


class DesktopAPI:
    def save_dialog(self, token, suggested_name):
        """Called from JS: opens native Save As dialog and writes the temp file to chosen path."""
        import webview
        tmp_path = _pending_files.pop(token, None)
        if not tmp_path or not os.path.exists(tmp_path):
            return {'ok': False, 'error': 'Fișier temporar negăsit'}
        try:
            result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=suggested_name,
                file_types=('PDF (*.pdf)',)
            )
            if result:
                save_path = result[0] if isinstance(result, (list, tuple)) else result
                if not save_path.lower().endswith('.pdf'):
                    save_path += '.pdf'
                shutil.copy2(tmp_path, save_path)
                return {'ok': True, 'path': save_path}
            else:
                return {'ok': False, 'cancelled': True}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def start_flask():
    app.run(host='127.0.0.1', port=8090, debug=False, use_reloader=False)


def wait_for_flask(port=8090, timeout=15):
    """Poll until Flask is accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


if __name__ == '__main__':
    if '--server' not in sys.argv:
        try:
            import webview
            import threading
            t = threading.Thread(target=start_flask, daemon=True)
            t.start()
            wait_for_flask()
            api = DesktopAPI()
            webview.create_window('PDF Ștampilă', 'http://127.0.0.1:8090',
                                  width=1100, height=820, resizable=True, js_api=api)
            webview.start()
        except ImportError:
            app.run(host='0.0.0.0', port=8090, debug=False)
    else:
        app.run(host='0.0.0.0', port=8090, debug=False)