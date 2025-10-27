#pragma once
// UI don gian khong dau (an toan ma hoa)
const char PAGE_HTML[] PROGMEM = R"HTML(
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32-CAM RHYX M21 + Servos</title>
<style>
:root{--gap:12px}
body{font-family:sans-serif;margin:16px;max-width:820px}
.card{padding:12px;margin:12px 0;border:1px solid #ddd;border-radius:12px;box-shadow:0 2px 6px rgba(0,0,0,.05)}
.row{display:flex;align-items:center;gap:12px;margin:8px 0;flex-wrap:wrap}
label{min-width:90px}
.range{flex:1}
button,.btn{padding:.55rem .9rem;border-radius:10px;border:1px solid #ccc;background:#f6f6f6;cursor:pointer;text-decoration:none;color:#111;display:inline-flex;align-items:center;gap:.4rem}
button.primary,.btn.primary{background:#e9f3ff;border-color:#90caf9}
button.warn,.btn.warn{background:#fff4e5;border-color:#ffcc80}
button.danger,.btn.danger{background:#ffe9e9;border-color:#ef9a9a}
button.active{background:#e9ffe9;border-color:#8bc34a}
#preview{width:100%;max-height:420px;object-fit:contain;border-radius:12px;border:1px solid #ddd}
small{color:#555}
.toolbar{display:flex;gap:var(--gap);flex-wrap:wrap}
.badge{display:inline-block;padding:.2rem .5rem;border-radius:999px;border:1px solid #ddd;background:#fafafa;font-size:.8rem;color:#444}
</style></head>
<body>
<h2>ESP32-CAM (RHYX M21 / GC2145) + Dual Servo</h2>

<!-- Thanh nut he thong -->
<div class="card">
  <div class="toolbar">
    <a class="btn primary" href="/wifi" title="Cau hinh Wi-Fi">⚙️ Cau hinh Wi-Fi</a>
    <a class="btn" href="/first-change" title="Doi tai khoan">🔑 Doi tai khoan</a>
    <a class="btn" href="/qr" title="Ma QR truy cap">#️⃣ Ma QR</a>
    <a class="btn danger" href="/logout" title="Dang xuat">🚪 Dang xuat</a>
  </div>
</div>

<div class="card">
  <img id="preview" alt="stream"/>
  <div class="row">
    <button id="snap">📷 Snapshot</button>
    <a class="btn" href="/capture" target="_blank">/capture</a>
    <a class="btn" href="/bmp" target="_blank">/bmp</a>
    <a class="btn" href="/status" target="_blank">/status</a>
    <span class="badge" id="hintHost"></span>
  </div>
  <div class="row"><small>Camera GC2145 (RHYX M21) khong autofocus; co AE/AWB.</small></div>
  <div class="row">
    <button id="aeBtn"  class="active">AE: ON</button>
    <button id="awbBtn" class="active">AWB: ON</button>
    <button id="ledBtn">LED: OFF</button>
  </div>
</div>

<div class="card">
  <h3>Servos</h3>
  <div class="row">
    <label for="s1">Servo 1</label>
    <input id="s1" class="range" type="range" min="0" max="180" value="90"><span id="s1v">90°</span>
  </div>
  <div class="row">
    <label for="s2">Servo 2</label>
    <input id="s2" class="range" type="range" min="0" max="180" value="90"><span id="s2v">90°</span>
  </div>
</div>

<script>
const s1=document.getElementById('s1'), s2=document.getElementById('s2');
const s1v=document.getElementById('s1v'), s2v=document.getElementById('s2v');
const aeBtn=document.getElementById('aeBtn'), awbBtn=document.getElementById('awbBtn');
const ledBtn=document.getElementById('ledBtn'), snap=document.getElementById('snap');
const preview=document.getElementById('preview'); const hintHost=document.getElementById('hintHost');

(function initStream(){
  const host=location.hostname; hintHost.textContent=host?("host: "+host):"";
  const url81="http://"+host+":81/stream"; const fb="/stream";
  preview.src=url81; preview.onerror=()=>{preview.onerror=null; preview.src=fb;};
})();
function d(fn,ms){let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms);}}
const push1=d(v=>fetch(`/servo?ch=1&val=${v}`),50);
const push2=d(v=>fetch(`/servo?ch=2&val=${v}`),50);
s1.oninput=e=>{s1v.textContent=e.target.value+'°';push1(e.target.value);};
s2.oninput=e=>{s2v.textContent=e.target.value+'°';push2(e.target.value);};
let aeOn=true,awbOn=true,ledOn=false;
aeBtn.onclick=async()=>{aeOn=!aeOn;await fetch(`/control?var=aec&val=${aeOn?1:0}`);aeBtn.classList.toggle('active',aeOn);aeBtn.textContent=`AE: ${aeOn?'ON':'OFF'}`;};
awbBtn.onclick=async()=>{awbOn=!awbOn;await fetch(`/control?var=awb&val=${awbOn?1:0}`);awbBtn.classList.toggle('active',awbOn);awbBtn.textContent=`AWB: ${awbOn?'ON':'OFF'}`;};
ledBtn.onclick=async()=>{ledOn=!ledOn;await fetch(`/led?val=${ledOn?1:0}`);ledBtn.textContent=`LED: ${ledOn?'ON':'OFF'}`;};
snap.onclick=()=>{ window.open('/capture','_blank'); };
</script>
</body></html>
)HTML";
