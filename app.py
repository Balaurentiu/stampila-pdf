#!/usr/bin/env python3
"""
PDF Ștampilă v2 - Aplică ștampile pe documente PDF.
- Ancorare pe text (căutare "CONTASIST" sau text personalizat)
- Detectare tabele + poziționare sub ele
- Mod manual cu click exact (fixat pentru landscape)
- Opacitate, scară, aplicare pe toate paginile
"""

import os, io, time
from flask import Flask, request, send_file, jsonify, render_template_string
import fitz
from PIL import Image
import numpy as np

app = Flask(__name__)
UPLOAD = '/data/stampila/uploads'
OUTPUT = '/data/stampila/output'
os.makedirs(UPLOAD, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)


def find_stamp_anchors(page, anchor_text='CONTASIST'):
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
                anchor_text='CONTASIST', opacity=0.5, rotation=0):
    """Returns (pdf_bytes, total_stamps, warnings_list).
    warnings_list contains messages for pages where anchor was not found."""
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
<input type="text" id="anchor" value="CONTASIST" placeholder="Ex: CONTASIST, SRL, semnătură...">
</div>
<div class="info" id="anchor-info">Caută textul din câmpul de mai sus și plasează ștampila sub textul găsit. Dacă nu găsește textul, afișează mesaj de înștiințare.</div>
</div>
<div id="man-opts" style="display:none;margin-top:8px">
<div class="info">👆 Click pe preview pentru a poziționa ștampila. Coordonatele sunt în puncte PDF (corectate pentru landscape).</div>
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
document.getElementById('pi').onchange=e=>{PF=e.target.files[0];document.getElementById('pz').classList.add('ok');document.getElementById('pf').textContent=PF?PF.name:'';up();cr()};
document.getElementById('si').onchange=e=>{SF=e.target.files[0];document.getElementById('sz').classList.add('ok');document.getElementById('sf').textContent=SF?SF.name:'';cr()};
['pz','sz'].forEach(id=>{let z=document.getElementById(id);z.ondragover=e=>{e.preventDefault();z.classList.add('dg')};z.ondragleave=()=>z.classList.remove('dg');z.ondrop=e=>{e.preventDefault();z.classList.remove('dg');let inp=id==='pz'?'pi':'si';document.getElementById(inp).files=e.dataTransfer.files;document.getElementById(inp).dispatchEvent(new Event('change'))}});

function sm(m){
  MD=m;
  document.getElementById('m-txt').classList.toggle('ac',m==='text');
  document.getElementById('m-tbl').classList.toggle('ac',m==='table');
  document.getElementById('m-man').classList.toggle('ac',m==='manual');
  document.getElementById('txt-opts').style.display=m==='text'?'block':'none';
  document.getElementById('man-opts').style.display=m==='manual'?'block':'none';
  MX=null;MY=null;
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
      cv.onclick=e=>{
        if(MD!=='manual')return;
        let rect=cv.getBoundingClientRect();
        let sx=cv.width/rect.width,sy=cv.height/rect.height;
        // Convert click to PDF points (respecting actual page dimensions)
        MX=(e.clientX-rect.left)*sx/scale;
        MY=(e.clientY-rect.top)*sy/scale;
        // Redraw + show stamp position marker
        ctx.drawImage(img,0,0);
        let stW=150*parseFloat(document.getElementById('sc').value)*scale;
        let stH=60*scale;
        ctx.fillStyle='rgba(56,189,248,.25)';ctx.strokeStyle='#38bdf8';ctx.lineWidth=2;
        ctx.fillRect(MX*scale,MY*scale,stW,stH);
        ctx.strokeRect(MX*scale,MY*scale,stW,stH);
        ss('info','Poziție: X='+Math.round(MX)+', Y='+Math.round(MY)+' pt (pagină '+PW.toFixed(0)+'×'+PH.toFixed(0)+')');
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
  if(MD==='manual'&&MX!==null){f.append('manual_x',MX);f.append('manual_y',MY)}

  document.getElementById('ab').textContent='⏳ Procesare...';
  document.getElementById('ab').disabled=true;
  try{
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
  }catch(e){ss('error','Eroare: '+e.message)}
  document.getElementById('ab').textContent='✅ Aplică Ștampila & Descarcă';
  document.getElementById('ab').disabled=false;
}

function ss(t,m){document.getElementById('sa').innerHTML='<div class="st s'+t[0]+'">'+m+'</div>'}
</script>
</body>
</html>'''


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

    result_bytes, n, warnings = apply_stamp(pdf_b, stamp_b, mode=md, scale=sc, margin=mg,
                                   manual_x=mx, manual_y=my, manual_page=pg,
                                   all_pages=ap, anchor_text=anchor, opacity=op, rotation=rot)
    
    name = (pf.filename or 'document').removesuffix('.pdf')
    response = send_file(io.BytesIO(result_bytes), mimetype='application/pdf',
                     as_attachment=True, download_name=f'{name}_stampilat.pdf')
    response.headers['X-Warnings'] = ' | '.join(warnings).encode('ascii', 'replace').decode('ascii') if warnings else ''
    response.headers['X-Stamps-Applied'] = str(n)
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8090, debug=False)