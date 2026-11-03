"""前端页面模板：单文件 HTML + 内联 CSS/JS，由 webui.py 作为单一响应体返回。

安全约束：渲染一律走 DOM 文本节点，不用 innerHTML 拼接用户可控内容。
"""

PAGE = """<!doctype html><meta charset="utf-8"><title>研鉴 FinAudit</title>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#1f2328}
.wrap{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:18px;font-weight:600;margin:0 0 4px}
h2{font-size:14px;font-weight:600;margin:0 0 10px}
.sub{color:#656d76;font-size:13px;margin-bottom:18px}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px;margin-top:14px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px}
input[placeholder],select,button{font-size:13px;padding:7px 10px;border:1px solid #d0d7de;border-radius:6px;background:#fff}
button{background:#1f6feb;color:#fff;border-color:#1f6feb;cursor:pointer}
button:disabled{background:#9aa4ae;border-color:#9aa4ae;cursor:default}
button.ghost{background:#fff;color:#1f2328;border-color:#d0d7de}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:10px;margin-top:12px}
.kv{background:#f6f8fa;border-radius:8px;padding:10px}
.kv b{display:block;font-size:20px;font-weight:600}
.kv span{font-size:12px;color:#656d76}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
th,td{border-bottom:1px solid #eaeef2;padding:8px 6px;text-align:left;vertical-align:top}
th{color:#656d76;font-weight:500;white-space:nowrap}
td.r{color:#57606a;font-size:12px;line-height:1.5}
td.wrap{max-width:430px;overflow-wrap:anywhere}
/* 编号列不折行（CLM-0001 断成两行很难读）；规则列限宽，把余量让给主张正文 */
#claims td:nth-child(1),#calcs td:nth-child(1),#audit td:nth-child(1){white-space:nowrap}
#claims td:nth-child(4){max-width:240px}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.fail-text{color:#b4232d}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;white-space:nowrap}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#57606a}
.note{color:#656d76;font-size:12px;margin-top:10px;line-height:1.6}
.warn{color:#9a6700;font-size:12px;margin-top:8px}
.agent-board{padding:0;overflow:hidden;border-color:#d8e0ea}
.agent-board-head{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;padding:22px 24px;background:linear-gradient(118deg,#102741,#17466d);color:#fff}
.agent-eyebrow{font-size:11px;letter-spacing:.08em;color:#b8d5ee;text-transform:uppercase;font-weight:700}
.agent-board-head h2{font-size:20px;margin:5px 0 6px;color:#fff}
.agent-board-head p{font-size:12px;line-height:1.6;color:#d7e5f2;margin:0;max-width:740px}
.agent-architecture{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
.agent-architecture span{font-size:11px;padding:4px 8px;border:1px solid #ffffff40;border-radius:20px;color:#eff7ff;background:#ffffff0d}
.agent-status-panel{min-width:150px;padding:12px 14px;border:1px solid #ffffff30;border-radius:10px;background:#ffffff12}
.agent-status-panel span,.agent-status-panel strong{display:block}
.agent-status-panel .status-caption{font-size:10px;color:#c6d9eb}
.agent-status-panel strong{font-size:15px;margin:4px 0}
.agent-status-panel #agent-current-stage{font-size:11px;color:#d7e5f2;line-height:1.5}
.agent-body{padding:18px 20px 22px}
.agent-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.agent-kpi{padding:11px 13px;border:1px solid #e5eaf0;border-radius:9px;background:#f8fafc}
.agent-kpi strong{display:block;font-size:21px;line-height:1.15;color:#193b5b}
.agent-kpi span{display:block;font-size:12px;color:#657487;margin-top:5px}
.flow-head{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;margin-top:20px}
.flow-head strong{font-size:13px;color:#27384a}
.flow-head span{font-size:11px;color:#758294}
.stage-track{display:grid;grid-template-columns:repeat(8,minmax(112px,1fr));gap:0;overflow-x:auto;list-style:none;padding:12px 0 4px;margin:0}
.stage-step{position:relative;min-width:112px;padding-right:10px}
.stage-step:after{content:"";position:absolute;left:30px;right:0;top:13px;height:2px;background:#e2e8ef}
.stage-step:last-child:after{display:none}
.stage-step.done:after{background:#63a77b}
.stage-node{position:relative;z-index:1;width:26px;height:26px;display:flex;align-items:center;justify-content:center;border-radius:50%;border:2px solid #d7dfe8;background:#fff;color:#6c7887;font-size:10px;font-weight:700}
.stage-step.done .stage-node{border-color:#24834a;background:#eaf6ee;color:#176536}
.stage-step.running .stage-node{border-color:#0969da;background:#eaf3ff;color:#0758b7;box-shadow:0 0 0 4px #0969da18}
.stage-step.fail .stage-node{border-color:#cf222e;background:#fff0f0;color:#b4232d}
.stage-step.skipped .stage-node{border-style:dashed;color:#9aa4ae}
.stage-code{font-size:11px;color:#7b8794;margin-top:7px}
.stage-name{font-size:12px;font-weight:650;color:#34465a;margin-top:2px}
.stage-state-label{font-size:10px;color:#8290a0;margin-top:3px}
.stage-step.done .stage-state-label{color:#24834a}.stage-step.running .stage-state-label{color:#0969da}.stage-step.fail .stage-state-label{color:#cf222e}
.stage-detail{font-size:11px;line-height:1.45;color:#7b8794;margin-top:3px;max-width:126px}
.stage-step.running .stage-name{color:#0758b7}
.stage-step.fail .stage-name{color:#b4232d}
.agents{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:10px}
.agent{position:relative;min-width:0;border:1px solid #e2e8ef;border-radius:10px;padding:12px;background:#fff;transition:border-color .15s,box-shadow .15s}
.agent.done{border-color:#badcc5;background:#fbfefc}
.agent.running{border-color:#5b9bea;background:#f3f8ff;box-shadow:0 0 0 2px #0969da12}
.agent.fail{border-color:#e5a7ab;background:#fff8f8}
.agent.interrupted{border-color:#e5c27b;background:#fffaf0}
.agent.pending{background:#fafbfd}
.agent-top{display:flex;align-items:center;justify-content:space-between;gap:6px}
.agent-code{font:700 10px ui-monospace,Consolas,monospace;color:#53677c;background:#edf2f7;border-radius:5px;padding:3px 6px}
.agent-state{font-size:11px;font-weight:650;border-radius:20px;padding:3px 7px;background:#edf1f5;color:#647386;white-space:nowrap}
.agent-state.done{background:#e8f5eb;color:#176536}
.agent-state.running{background:#e5f0ff;color:#0758b7}
.agent-state.fail{background:#fde8e8;color:#b4232d}
.agent-state.interrupted{background:#fff1cf;color:#875b00}
.agent-name{font-size:13px;font-weight:700;color:#24384d;margin-top:9px}
.agent-stage{font-size:11px;color:#7b8794;margin-top:2px}
.agent-desc{font-size:12px;line-height:1.55;color:#536477;margin-top:8px;min-height:56px}
.agent-output{font-size:11px;line-height:1.5;color:#315978;background:#f2f7fb;border-radius:6px;padding:6px 7px;margin-top:8px}
.agent-activity{font-size:11px;line-height:1.5;color:#6b7785;margin-top:7px;overflow-wrap:anywhere}
.agent-activity b{color:#48586b;font-size:10px}
.event-panel{margin-top:20px;border-top:1px solid #e8edf2;padding-top:2px}
.agent-events{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;list-style:none;padding:0;margin:10px 0 0}
.agent-event{display:grid;grid-template-columns:54px minmax(0,1fr);gap:10px;padding:10px;border:1px solid #e5eaf0;border-radius:8px;background:#fbfcfd;min-width:0}
.agent-event.attention{border-color:#ebc989;background:#fffbf2}
.event-seq{font:11px ui-monospace,Consolas,monospace;color:#738195;padding-top:2px}
.event-title{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12px;font-weight:650;color:#2d4055}
.event-stage,.event-type{font-size:9px;color:#68788a;background:#edf2f7;border-radius:10px;padding:2px 6px}
.event-detail{font-size:11px;line-height:1.5;color:#68788a;margin-top:4px;overflow-wrap:anywhere}
.event-handoff{font-size:11px;line-height:1.45;color:#315978;margin-top:5px}
.event-handoff.bad{color:#9a6700}
.events-empty{font-size:11px;line-height:1.6;color:#758294;border:1px dashed #d8e0e8;border-radius:8px;padding:14px;margin-top:10px;background:#fbfcfd}
.agent-legend{display:flex;gap:10px;flex-wrap:wrap;font-size:10px;color:#718096}
.legend-dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;background:#adb8c4}
.legend-dot.active{background:#0969da}.legend-dot.complete{background:#24834a}.legend-dot.error{background:#cf222e}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
/* C 区主张表有 4 列，给左栏更多宽度，避免规则列被压成每行六七个字 */
.cols.claims-cols{grid-template-columns:minmax(0,1.45fr) minmax(0,1fr)}
.ev{border:1px solid #e3e6ea;border-radius:8px;padding:10px;margin-top:8px;background:#fbfcfd}
pre{white-space:pre-wrap;word-break:break-all;background:#f6f8fa;border-radius:6px;padding:8px;font-size:12px;margin:6px 0 0}
.dl{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
a.dlbtn{font-size:12px;padding:5px 9px;border:1px solid #d0d7de;border-radius:6px;color:#0969da;text-decoration:none;background:#fff}
.sel{background:#f6f8fa}
.sel td{background:#f6f8fa}
@media(max-width:960px){.agents{grid-template-columns:repeat(3,minmax(0,1fr))}.stage-track{grid-template-columns:repeat(8,minmax(100px,1fr))}}
@media(max-width:700px){.wrap{padding:14px}.agent-board-head{padding:18px;display:block}.agent-status-panel{margin-top:14px;max-width:none}.agent-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.agents{grid-template-columns:repeat(2,minmax(0,1fr))}.agent-events{grid-template-columns:1fr}.cols{grid-template-columns:1fr}
.cols>div{min-width:0}
/* 窄屏下让表格保持可读列宽，靠 .tablewrap 横向滚动，避免每列被压成竖排 */
#claims{min-width:600px}#claims td:nth-child(2){min-width:200px}
#calcs{min-width:430px}#audit{min-width:500px}#review{min-width:560px}
#claims td:nth-child(4),#claims td:nth-child(1),#calcs td:nth-child(1),#audit td:nth-child(1){white-space:nowrap}
input[placeholder]{min-width:0;max-width:100%}}
@media(max-width:460px){.agents{grid-template-columns:1fr}.agent-desc{min-height:0}.agent-body{padding:14px}.agent-board-head h2{font-size:18px}.agent-architecture{gap:5px}}
</style>
<div class="wrap">
<h1>研鉴 FinAudit · 研报主张核验</h1>
<div class="sub">面向金融投研的证据核验工作台：LLM 拆解主张，规则与程序负责核验计算，独立审计后形成可追溯交付。</div>

<div class="card"><h2>A · 任务概览</h2>
  <div class="row"><b>用自己的材料</b></div>
  <div class="row">
    <input type="file" id="files" multiple>
    <label>研报 <select id="report"><option value="">先选文件…</option></select></label>
  </div>
  <div class="row">
    <input id="subject" placeholder="主体化名" value="甲企业" size="10">
    <input id="period" placeholder="期间" value="2024年度" size="10">
    <input id="cutoff" placeholder="数据截止点" value="2025-04-30" size="12">
    <button id="up" onclick="upload()">上传并核验</button>
  </div>
  <div class="note">支持 PDF / Markdown / TXT。单文件 ≤ 20MB，合计 ≤ 60MB，最多 10 份。上传材料无金标准，需用真实模型或本机模型拆解主张。</div>
  <div class="row" style="border-top:1px solid #eaeef2;padding-top:12px"><b>或用内置示例夹具</b></div>
  <div class="row">
    <label>夹具 <select id="fx">
      <option value="demo01">demo01 · Markdown</option>
      <option value="demo01_pdf">demo01_pdf · PDF 文本层</option>
      <option value="demo01_scan">demo01_scan · 扫描件 OCR</option>
      <option value="bench">bench · 300 条规模基准</option>
    </select></label>
    <label>模型 <select id="preset" onchange="onPreset()">
      <option value="mock">mock · 离线占位</option>
      <option value="local">local · 本机模型（Ollama）</option>
      <option value="modelscope">modelscope · DeepSeek-V4-Pro</option>
      <option value="deepseek">deepseek · deepseek-chat</option>
    </select></label>
    <input id="key" type="password" placeholder="API Key" size="20">
    <button id="go" onclick="run()">运行核验</button>
    <span class="mono" id="runhint"></span>
  </div>
  <div class="row">
    <label><input type="checkbox" id="egress"> 我确认：本次运行将把研报正文发送到外部模型服务</label>
  </div>
  <div class="note" id="egressNote"></div>
  <div class="warn" id="mockWarn" hidden>上传材料没有金标准，mock 模式无法拆解主张；请选择 local / modelscope / deepseek，或改选内置示例夹具。</div>
  <div class="row"><span class="mono" id="stat"></span></div>
  <div class="grid" id="counts"></div>
  <div class="grid" id="metrics"></div>
  <div class="note" id="note"></div>
</div>

<section class="card agent-board" aria-labelledby="agents-title">
  <div class="agent-board-head">
    <div>
      <div class="agent-eyebrow">协作运行台 · RA1—RA8</div>
      <h2 id="agents-title">多智能体协作流程</h2>
      <p>从材料解析、证据检索、主张拆解，到确定性核验、独立审计与报告交付。当前状态和事件来自后端；角色名册来自系统定义，职责说明对照角色协议编写。</p>
      <div class="agent-architecture" aria-label="核心协作原则">
        <span>RA1 统一编排</span><span>RA4 LLM 拆解主张</span><span>RA5 唯一写入核验状态</span><span>RA6 确定性计算</span><span>RA7 独立审计</span>
      </div>
    </div>
    <div class="agent-status-panel" aria-live="polite">
      <span class="status-caption">本次作业</span>
      <strong id="agent-job-status">尚未启动</strong>
      <span id="agent-current-stage">运行后显示当前阶段</span>
    </div>
  </div>
  <div class="agent-body">
    <div class="agent-kpis" id="agent-kpis" aria-live="polite"></div>
    <div class="flow-head"><div><strong>执行阶段</strong> <span>有事件表示已触发；跳过阶段不会显示为已完成</span></div></div>
    <ol class="stage-track" id="stage-track" aria-label="S0 到 S7 执行阶段"></ol>
    <div class="flow-head">
      <div><strong>智能体分工</strong> <span>查看每个角色的工作边界、主要产出和最近动作</span></div>
      <div class="agent-legend" aria-label="状态说明"><span><i class="legend-dot active"></i>运行中</span><span><i class="legend-dot complete"></i>已完成</span><span><i class="legend-dot"></i>未运行</span><span><i class="legend-dot error"></i>异常</span></div>
    </div>
    <div class="agents" id="agents"></div>
    <div class="event-panel">
      <div class="flow-head"><div><strong>协作事件与交接</strong> <span>最多返回最近 200 条；展示交接摘要，不展示材料正文</span></div><span id="agent-event-count"></span></div>
      <ol class="agent-events" id="agent-event-list"></ol>
      <div class="events-empty" id="agent-events-empty">尚未运行任务。启动核验后，这里会按发生顺序展示 Agent 调用、规则判定、计算和交接记录。</div>
    </div>
  </div>
</section>

<div class="card"><h2>C · 主张与证据</h2>
  <div class="row"><label>任务目录 <select id="task"></select></label>
    <button class="ghost" onclick="loadReview()">加载已完成任务</button></div>
  <div class="cols claims-cols">
    <div><div class="tablewrap"><table id="claims"></table></div></div>
    <div id="evidence"><div class="note">选中左侧一条主张，查看其候选证据；点「定位原文」回看材料原文。</div></div>
  </div>
</div>

<div class="card"><h2>D · 复核与交付</h2>
  <div class="cols">
    <div><div class="note"><b>计算过程</b>（D 组，确定性程序执行）</div><div class="tablewrap"><table id="calcs"></table></div></div>
    <div><div class="note"><b>审计事件</b>（审计复核 / 越权 / 工具失败 / 外发请求 / 升级）</div><div class="tablewrap"><table id="audit"></table></div></div>
  </div>
  <div class="note" style="margin-top:12px"><b>人工复核</b>：界面只做受控呈现与留痕，不改判定状态（状态唯一写点是 RA5）。</div>
  <div class="tablewrap"><table id="review"></table></div>
  <div class="note" id="rvstat"></div>
  <div id="reviewlog"></div>
  <div class="note" style="margin-top:12px"><b>交付下载</b></div>
  <div class="dl" id="dl"></div>
</div>
</div>
<script>
function el(tag, cls, text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!==undefined) e.textContent=text; return e;}
function mktable(node, headers){ node.innerHTML=''; const t=el('table'); const tr=el('tr'); for(const h of headers){const th=el('th',null,h); tr.appendChild(th);} t.appendChild(tr); node.appendChild(t); return t;}
function row(t, cells, cls){ const tr=el('tr', cls||null); for(const c of cells){ const td = (c && c.nodeType)? el('td') : el('td',null,String(c===null||c===undefined?'':c)); if(c && c.nodeType) td.appendChild(c); tr.appendChild(td);} t.appendChild(tr); return tr;}
function tag(text){ const s=el('span','tag '+text,text); return s;}

document.getElementById('files').addEventListener('change',function(){
  const s=document.getElementById('report'); s.textContent='';
  for(const f of this.files){ const o=document.createElement('option'); o.value=f.name; o.textContent=f.name; s.appendChild(o); }
});

let JOB=null, TIMER=null;
const JOBKEY='finaudit_job';   // 刷新页面后据此恢复轮询，避免看不到仍在跑的作业

function rememberJob(id){
  try{ id ? sessionStorage.setItem(JOBKEY,id) : sessionStorage.removeItem(JOBKEY); }catch(e){}
}
function restoreJob(){
  let saved=null;
  try{ saved=sessionStorage.getItem(JOBKEY); }catch(e){}
  if(!saved) return;
  JOB=saved;
  document.getElementById('stat').textContent='恢复上次作业…';
  document.getElementById('go').disabled=true;
  poll();
}
let PENDING=null;   // 上传产生、尚未运行的夹具；点「运行核验」时优先于下拉选择

function isExternal(p){ return p==='modelscope'||p==='deepseek'; }

function runTarget(){
  if(PENDING) return {fx:PENDING, label:'上传材料（未运行）'};
  const sel=document.getElementById('fx');
  const opt=sel.options[sel.selectedIndex];
  return {fx:sel.value, label:opt?opt.textContent:sel.value};
}
function updateRunHint(){
  const t=runTarget();
  document.getElementById('runhint').textContent='将运行：'+t.label;
}
document.getElementById('fx').addEventListener('change', function(){ PENDING=null; updateRunHint(); });
function onPreset(){
  const p=document.getElementById('preset').value;
  const ext = isExternal(p);
  const box=document.getElementById('egress');
  box.disabled=!ext; if(!ext) box.checked=false;
  document.getElementById('key').disabled = (p==='mock');
  const host = p==='modelscope' ? 'api-inference.modelscope.cn' : (p==='deepseek' ? 'api.deepseek.com' : '');
  document.getElementById('egressNote').textContent = ext
    ? ('外发口径：外部端点 '+host+'，研报正文将离开本机，需勾选上方确认后才会发送；未勾选则拒绝调用，不静默降级。')
    : (p==='local' ? '外发口径：本机端点 127.0.0.1:11434，材料不出本机（需先自行启动本机模型服务）。'
                   : '外发口径：离线占位，不出本机。');
  // 上传材料没有金标准，mock 无法拆解；在按钮旁直接说明，不让用户点了才报错
  const warn=document.getElementById('mockWarn');
  warn.hidden = !(PENDING && p==='mock');
}

async function upload(){
  const fs=document.getElementById('files').files;
  if(!fs.length){ alert('请先选择文件（研报 + 证据材料）'); return; }
  const fd=new FormData();
  for(const f of fs) fd.append('file', f);
  fd.append('report', document.getElementById('report').value || fs[0].name);
  fd.append('subject', document.getElementById('subject').value);
  fd.append('period', document.getElementById('period').value);
  fd.append('cutoff', document.getElementById('cutoff').value);
  const stat=document.getElementById('stat'), up=document.getElementById('up');
  up.disabled=true; stat.textContent='上传并登记中…';
  try{
    const r=await fetch('/api/upload',{method:'POST',body:fd}); const d=await r.json();
    if(!d.ok){ alert(d.error||'上传失败'); stat.textContent=''; up.disabled=false; return; }
    PENDING=d.fixture; updateRunHint();
    // 材料已登记到任务目录，清空文件选择，避免重复上传生成多余任务
    document.getElementById('files').value='';
    const rep=document.getElementById('report'); rep.textContent='';
    const ph=document.createElement('option'); ph.value=''; ph.textContent='先选文件…'; rep.appendChild(ph);
    const preset=document.getElementById('preset').value;
    stat.textContent='已登记 '+d.files.length+' 份材料';
    up.disabled=false;
    if(preset==='mock'){
      document.getElementById('mockWarn').hidden=false;
      stat.textContent+='：上传材料需选 local / modelscope / deepseek 才能拆解主张，选好模型后点「运行核验」。';
      return;
    }
    stat.textContent+='，开始核验…';
    await start(PENDING, preset);
    PENDING=null; updateRunHint();
  }catch(e){ alert(String(e)); up.disabled=false; }
}

function run(){
  const p=document.getElementById('preset').value;
  if(isExternal(p) && !document.getElementById('egress').checked){
    alert('该模型为外部端点：请勾选「确认外发」，或改用 local/mock。'); return; }
  const t=runTarget();
  if(PENDING && p==='mock'){
    alert('上传材料没有金标准，mock 模式无法拆解主张。请选择 local / modelscope / deepseek，或改选内置示例夹具。');
    return;
  }
  start(t.fx, p);
  PENDING=null; updateRunHint();
}

async function start(fx, preset){
  const go=document.getElementById('go'), stat=document.getElementById('stat');
  if(go) go.disabled=true;
  document.getElementById('counts').textContent='';
  document.getElementById('metrics').textContent='';
  renderAgents([], [], '', '任务创建中');
  document.getElementById('claims').innerHTML='';
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({fixture:fx, preset:preset, key:document.getElementById('key').value,
                           allow_external:document.getElementById('egress').checked})});
    const d=await r.json();
    if(!d.ok){ stat.textContent=''; alert(d.error); if(go) go.disabled=false; return; }
    JOB=d.job_id; rememberJob(JOB); poll();
  }catch(e){ if(stat) stat.textContent='失败'; alert(String(e)); if(go) go.disabled=false; }
}

async function poll(){
  if(TIMER) clearInterval(TIMER);
  TIMER=setInterval(tick, 800); tick();
}
async function tick(){
  if(!JOB) return;
  const r=await fetch('/api/job?id='+encodeURIComponent(JOB)); const d=await r.json();
  if(!d.ok){
    // 服务重启或作业被淘汰后，轮询必须停下并提示，不能一直空转
    clearInterval(TIMER); TIMER=null; JOB=null; rememberJob(null);
    document.getElementById('go').disabled=false;
    document.getElementById('stat').textContent='作业已失效（服务可能已重启），请重新运行';
    renderAgents([], [], '', '尚未启动', {});
    return;
  }
  renderAgents(d.agents||[], d.events||[], d.stage||'', d.status||'运行中', d.stage_event_counts||{});
  document.getElementById('stat').textContent = d.status||'';
  if(d.status==='完成'||d.status==='失败'){
    clearInterval(TIMER); TIMER=null; document.getElementById('go').disabled=false;
    rememberJob(null);
    if(d.status==='失败'){ alert(d.error||'运行失败'); return; }
    renderCounts(d.counts||{}); renderMetrics(d.metrics||{});
    renderClaims(d.items||[]);
    const note=document.getElementById('note');
    note.textContent = d.note||'';
    if(d.warning){ const w=el('div','warn','⚠ '+d.warning); note.appendChild(w); }
    await initTasks();          // 新任务目录要及时出现在下拉里
    await loadReview(d.task);
  }
}

function renderCounts(counts){
  const box=document.getElementById('counts'); box.textContent='';
  for(const k in counts){ const d=el('div','kv'); const b=el('b',null,String(counts[k])); d.appendChild(b); const s=el('span','tag '+k,k); d.appendChild(s); box.appendChild(d); }
}
function renderMetrics(m){
  const box=document.getElementById('metrics'); box.textContent='';
  for(const k in m){ const d=el('div','kv'); d.appendChild(el('b',null,String(m[k]))); d.appendChild(el('span',null,k)); box.appendChild(d); }
}
const STAGES=[
  {code:'S0',name:'任务受理',detail:'锁定主体、期间与材料'},
  {code:'S1',name:'材料登记',detail:'登记材料并生成哈希'},
  {code:'S2',name:'解析定位',detail:'解析文档并建立原文定位'},
  {code:'S3',name:'证据检索',detail:'检索候选证据并回链来源'},
  {code:'S4',name:'主张拆解',detail:'LLM 拆分原子主张，不作真伪判断'},
  {code:'S5',name:'核验计算',detail:'RA5 判定，RA6 确定性计算'},
  {code:'S6',name:'独立审计',detail:'复核证据、结论与升级项'},
  {code:'S7',name:'报告交付',detail:'生成 O1 / O2 / O3 并校验'}
];
const AGENT_GUIDE={
  RA1:{title:'总控与任务编排',desc:'校验任务边界，锁定主体化名、期间和授权材料，按阶段组织执行并处理升级。',output:'任务单、流程编排与升级记录'},
  RA2:{title:'文档解析 Agent',desc:'解析 PDF、Markdown 等材料，提取文档结构、表格与可回看的原文定位。',output:'文档结构块与定位索引'},
  RA3:{title:'事实检索 Agent',desc:'围绕主张从已登记材料中检索候选事实，保留材料来源和精确定位。',output:'候选证据集与来源回链'},
  RA4:{title:'主张拆解 Agent（LLM）',desc:'把研报段落拆成可核验的原子主张；不搜索证据，也不替核验模块判断真伪。',output:'带类型和期间的原子主张清单'},
  RA5:{title:'核验 Agent',desc:'按规则核对数值、单位、期间、口径与证据；系统中唯一写入核验状态的角色。',output:'逐项核验结果与计算请求'},
  RA6:{title:'财务计算 Agent',desc:'调用确定性公式程序计算同比、比例等指标并记录公式和输入来源；计算本身不由 LLM 心算。',output:'可复算的公式、输入与计算记录'},
  RA7:{title:'独立审计 Agent',desc:'只读复查核验结论、证据链和修改建议，标出不一致项及需人工复核的事项。',output:'审计发现与升级项清单'},
  RA8:{title:'报告 Agent',desc:'仅整理已审定的结论、来源与适用边界，生成供人工复核阅读的报告。',output:'O1 人读核查报告'}
};
const ACTOR_NAMES={
  RA1:'总控 Agent',RA2:'文档解析 Agent',RA3:'事实检索 Agent',RA4:'主张拆解 Agent',
  RA5:'核验 Agent',RA6:'财务计算 Agent',RA7:'独立审计 Agent',RA8:'报告 Agent',
  'finaudit-calc':'确定性计算程序','登记程序':'材料登记程序','pdf-parser':'PDF 解析程序',
  OCR:'OCR 程序',EXPORT:'导出程序',HUMAN:'人工复核员'
};
function stageClass(stage,index,currentIndex,events,status,stageCounts){
  const hasEvent=Object.prototype.hasOwnProperty.call(stageCounts,stage.code)
    ? Number(stageCounts[stage.code])>0 : events.some(function(e){return e.stage===stage.code;});
  if(status==='完成') return hasEvent?'done':'skipped';
  if(status==='失败'&&index===currentIndex) return 'fail';
  if(currentIndex>=0&&index<currentIndex) return hasEvent?'done':'skipped';
  if(currentIndex>=0&&index===currentIndex) return 'running';
  return 'pending';
}
function renderStages(currentStage,events,status,stageCounts){
  const box=document.getElementById('stage-track'); box.textContent='';
  const currentIndex=STAGES.findIndex(function(s){return s.code===currentStage;});
  const labels={done:'已完成',running:'执行中',fail:'执行失败',skipped:'未触发',pending:'待执行'};
  STAGES.forEach(function(s,i){
    const state=stageClass(s,i,currentIndex,events,status,stageCounts);
    const li=el('li','stage-step '+state);
    li.appendChild(el('div','stage-node',String(i)));
    li.appendChild(el('div','stage-code',s.code));
    li.appendChild(el('div','stage-name',s.name));
    li.appendChild(el('div','stage-state-label',labels[state]||'待执行'));
    li.appendChild(el('div','stage-detail',s.detail));
    box.appendChild(li);
  });
}
function renderAgentKpis(agents,events){
  const box=document.getElementById('agent-kpis'); box.textContent='';
  const participated=agents.filter(function(a){return a.status!=='未运行';}).length;
  const completed=agents.filter(function(a){return a.status==='已完成';}).length;
  const handoffs=events.filter(function(e){return e.event_type==='交接'&&e.handoff&&e.handoff.missing_count===0;}).length;
  const errors=events.filter(function(e){return e.event_type==='工具失败'||e.event_type==='越权';}).length;
  [[String(participated)+' / '+String(agents.length),'已参与角色'],[String(completed),'已完成角色'],[String(handoffs),'字段齐全交接'],[String(errors),'最近窗口内异常事件']].forEach(function(item){
    const card=el('div','agent-kpi'); card.appendChild(el('strong',null,item[0])); card.appendChild(el('span',null,item[1])); box.appendChild(card);
  });
}
function renderAgentCards(agents){
  const box=document.getElementById('agents'); box.textContent='';
  const stateMap={'已完成':'done','运行中':'running','失败':'fail','已中断':'interrupted','未运行':'pending'};
  agents.forEach(function(a){
    const guide=AGENT_GUIDE[a.code]||{title:a.name,desc:'按系统角色边界参与本次投研核验流程。',output:'参见运行事件'};
    const state=stateMap[a.status]||'pending';
    const card=el('article','agent '+state);
    const top=el('div','agent-top');
    top.appendChild(el('span','agent-code',a.code+' · '+a.stage));
    top.appendChild(el('span','agent-state '+state,a.status));
    card.appendChild(top);
    card.appendChild(el('div','agent-name',guide.title));
    card.appendChild(el('div','agent-stage',a.name+'｜运行事件 '+String(a.count||0)+' 条'));
    card.appendChild(el('div','agent-desc',guide.desc));
    card.appendChild(el('div','agent-output','主要产出：'+guide.output));
    card.appendChild(el('div','agent-activity',a.last?'最近动作：'+a.last:'最近动作：尚无该角色的运行事件'));
    box.appendChild(card);
  });
}
function renderAgentEvents(events){
  const list=document.getElementById('agent-event-list'); list.textContent='';
  const recent=events.slice(-12).reverse();
  document.getElementById('agent-event-count').textContent='可见 '+String(events.length)+' 条｜显示最近 '+String(recent.length)+' 条';
  document.getElementById('agent-events-empty').hidden=recent.length>0;
  recent.forEach(function(e){
    const h=e.handoff||null;
    const attention=e.event_type==='工具失败'||e.event_type==='越权'||(h&&h.missing_count>0);
    const item=el('li','agent-event'+(attention?' attention':''));
    item.appendChild(el('div','event-seq','#'+String(e.seq||'—')));
    const body=el('div');
    const title=el('div','event-title',e.event_type||'运行事件');
    title.appendChild(el('span','event-stage',e.stage||'阶段未知'));
    body.appendChild(title);
    const actor=ACTOR_NAMES[e.actor]||e.actor||'未知参与者';
    body.appendChild(el('div','event-detail','参与者：'+actor));
    if(h){
      const missing=Number(h.missing_count||0);
      const handoffText='交付物 '+(h.code||'—')+' '+(h.name||'未登记交接')+'｜'+(h.src||actor)+' → '+(h.dst||'接收方未知')+'｜缺失字段 '+String(missing)+' 项';
      body.appendChild(el('div','event-handoff'+(missing?' bad':''),handoffText));
    }
    item.appendChild(body); list.appendChild(item);
  });
}
function renderAgents(agents,events,currentStage,status,stageCounts){
  agents=Array.isArray(agents)?agents:[]; events=Array.isArray(events)?events:[];
  stageCounts=stageCounts||{};
  const stage=STAGES.find(function(s){return s.code===currentStage;});
  document.getElementById('agent-job-status').textContent=status||'尚未启动';
  document.getElementById('agent-current-stage').textContent=stage?(stage.code+' · '+stage.name):((status==='完成')?'全部阶段已结束':'等待阶段事件');
  renderAgentKpis(agents,events);
  renderStages(currentStage,events,status||'尚未启动',stageCounts);
  renderAgentCards(agents);
  renderAgentEvents(events);
}
async function initAgentRoster(){
  try{
    const r=await fetch('/api/agents'); const d=await r.json();
    renderAgents(d.agents||[], [], '', '尚未启动');
  }catch(e){
    document.getElementById('agent-job-status').textContent='角色清单加载失败';
  }
}

let CLAIMS=[];
function ruleLabel(it){
  const names=(it.rule_names||[]);
  if(!names.length) return it.rules||'—';
  return names.length<=2 ? names.join('、') : names.slice(0,2).join('、')+' 等 '+names.length+' 条';
}

function renderClaims(items){
  CLAIMS=items;
  const t=mktable(document.getElementById('claims'),['ID','主张','状态','规则']);
  items.forEach(function(it,i){
    const tr=row(t,[it.id, (it.text||'').slice(0,60), tag(it.status), ruleLabel(it)]);
    // 规则列显示中文名，编号放悬停提示供追溯；名称过多时折叠成「+N」
    tr.children[3].title = [it.rules, (it.rule_ids&&it.rule_ids!=='—') ? '(编号 '+it.rule_ids+')' : ''].filter(Boolean).join(' ');
    tr.style.cursor='pointer';
    tr.onclick=function(){ showEvidence(i, tr); };
  });
}
function showEvidence(i, tr){
  const box=document.getElementById('evidence'); box.textContent='';
  const it=CLAIMS[i];
  const p=el('div','note'); p.textContent='主张：'+(it.text||'')+'｜类型 '+it.type+'｜理由 '+(it.reason||'')+'｜定位 '+(it.loc||'—');
  box.appendChild(p);
  const evs=it.evidence||[];
  if(!evs.length){ box.appendChild(el('div','note','无候选证据（出口：证据不足/需人工复核，不臆造匹配）')); }
  for(const e of evs){
    const card=el('div','ev');
    card.appendChild(el('div','mono',e.candidate_id+'｜'+e.material_id+'｜'+(e.locator||'—')));
    card.appendChild(el('div',null,'摘录：'+(e.quote||'—')));
    card.appendChild(el('div',null,'OCR 置信档：'+(e.ocr_band||'—')));
    const b=el('button','ghost','定位原文'); b.style.marginTop='6px';
    b.onclick=function(){ locate(e); };
    card.appendChild(b);
    const pre=el('pre'); pre.id='pre-'+e.candidate_id; card.appendChild(pre);
    box.appendChild(card);
  }
}
async function locate(e){
  const task=document.getElementById('task').value || (window.LASTTASK||'');
  const r=await fetch('/api/block?task='+encodeURIComponent(task)+'&block_id='+encodeURIComponent(e.block_id||''));
  const d=await r.json();
  const pre=document.getElementById('pre-'+e.candidate_id);
  if(pre) pre.textContent = d.ok ? ('【'+d.material_id+'｜'+(d.locator||'—')+'｜解析状态 '+(d.parse_status||'—')+'】\\n'+(d.content_text||'')) : ('定位失败：'+(d.error||''));
}

async function initTasks(){
  const r=await fetch('/api/tasks'); const d=await r.json();
  const s=document.getElementById('task'); s.textContent='';
  for(const t of (d.tasks||[])){const o=document.createElement('option');o.value=t;o.textContent=t;s.appendChild(o);}
}
async function loadReview(task){
  if(task) {
    const s=document.getElementById('task');
    if(s){
      // 运行刚产生的任务目录可能还没出现在下拉里；先补 option 再选中，否则 select.value 会静默失败
      if(![...s.options].some(o=>o.value===task)){
        const o=document.createElement('option'); o.value=task; o.textContent=task; s.appendChild(o);
      }
      s.value=task;
    }
    window.LASTTASK=task;
  }
  const t=document.getElementById('task').value; if(!t) return;
  window.LASTTASK=t;
  const r=await fetch('/api/review?task='+encodeURIComponent(t)); const d=await r.json();
  if(!d.ok){ document.getElementById('rvstat').textContent=d.error||''; return; }
  // 历史任务也还原 Agent 面板（事件流来自该任务的 C-events.jsonl），不留"尚未启动"
  if(d.agents && d.agents.length){
    renderAgents(d.agents, d.events||[], d.stage||'', d.run_status||'完成', d.stage_event_counts||{});
  }
  if(!CLAIMS.length) renderClaims(d.items||[]);
  const ct=mktable(document.getElementById('calcs'),['calc','公式','输出','状态']);
  for(const c of (d.calcs||[])){
    const tr=row(ct,[c.calc_id, c.formula_text||c.formula_id, c.output, c.result_status]);
    // 公式显示中文名，原始编号放悬停提示；失败项就地给出原因，不用悬停才能看到
    tr.children[1].title=c.formula_id||'';
    tr.children[2].className='num';
    if(c.result_status && c.result_status!=='成功'){
      const st=tr.children[3];
      st.className='fail-text';
      if(c.error){
        st.textContent='';
        st.appendChild(document.createTextNode(c.result_status));
        const why=el('div','note',c.error); why.style.marginTop='2px'; st.appendChild(why);
      }
    }
  }
  const at=mktable(document.getElementById('audit'),['seq','类型','角色','摘要']);
  for(const a of (d.audit||[])){
    const tr=row(at,[a.seq, a.event_type, a.actor, a.summary||'']);
    tr.children[3].className='r wrap';
  }
  const rt=mktable(document.getElementById('review'),['claim','原文摘录','状态','处置','提交']);
  for(const s of (d.status||[])){
    const sel=document.createElement('select');
    const placeholder=document.createElement('option');placeholder.textContent='选择处置';placeholder.value='';placeholder.disabled=true;placeholder.selected=true;sel.appendChild(placeholder);
    for(const a of ['确认已查看（仅留痕）','标记需人工复核（仅留痕）']){const o=document.createElement('option');o.textContent=a;sel.appendChild(o);}
    const btn=el('button','ghost','提交');
    btn.onclick=function(){ doReview(s.claim_id, sel.value); };
    row(rt,[s.claim_id, (s.text||'').slice(0,40), tag(s.status), sel, btn]);
  }
  const logbox=document.getElementById('reviewlog'); logbox.textContent='';
  if(d.review_log&&d.review_log.length){
    const t2=el('table'); const hdr=el('tr');
    for(const h of ['claim','处置','操作人','时间']){const th=el('th',null,h);hdr.appendChild(th);} t2.appendChild(hdr);
    for(const r of d.review_log){ const tr=el('tr');
      for(const v of [r.claim_id,r.action,r.operator,r.at]){const td=el('td','mono',String(v||''));tr.appendChild(td);} t2.appendChild(tr); }
    logbox.appendChild(el('div','note','复核留痕（H10）：'));
    logbox.appendChild(t2);
  }
  const dl=document.getElementById('dl'); dl.textContent='';
  for(const f of (d.files||[])){
    const a=document.createElement('a'); a.className='dlbtn'; a.textContent=f.name;
    a.href='/api/download?task='+encodeURIComponent(t)+'&file='+encodeURIComponent(f.name);
    dl.appendChild(a);
  }
}
async function doReview(cid, action){
  const t=document.getElementById('task').value;
  if(!action){ document.getElementById('rvstat').textContent='请先选择处置方式，再提交（'+cid+'）。'; return; }
  const r=await fetch('/api/review/action',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({task:t, claim_id:cid, action:action, operator:'人工复核员'})});
  const d=await r.json();
  if(d.ok) await loadReview(t);
  // 提示写在留痕表之外，避免被 loadReview 重建内容覆盖
  document.getElementById('rvstat').textContent = d.ok ? ('已留痕：'+cid+' '+action+'｜'+d.note) : (d.error||'');
}
initAgentRoster(); onPreset(); initTasks(); updateRunHint(); restoreJob();
</script>"""
