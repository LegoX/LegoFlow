/* Curator's sidebar, metric cards, batch rows and dialogs, for terminal evidence. */
'use strict';
let data = JSON.parse(document.getElementById('dashboard-data').textContent);
const LIVE = document.body.dataset.live === 'true';
const $ = id => document.getElementById(id);
const esc = value => String(value == null ? '—' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
const sourceName = value => ({stackoverflow:'Stack Overflow',json:'Questions JSON',tasks:'Local candidates'})[value] || 'Not recorded';
const stageName = value => ({idle:'Idle',preflight:'Preflight',check:'Check',collect:'Collect / import',generate:'Generate',normalize:'Normalize','validate-oracle':'Validate Oracle','validate-nop':'Validate NOP',publish:'Publish',completed:'Completed',failed:'Failed',interrupted:'Interrupted'})[value] || 'Not recorded';
const taskStatus = value => ({published:'Current publication',retained:'Retained version',validated:'Validated',rejected:'Rejected',pending:'Pending',running:'Running',missing:'Unavailable'})[value] || 'Not recorded';
const gateName = gate => gate && ({passed:'Passed',failed:'Failed',pending:'Not recorded',conflict:'Evidence conflict'})[gate.status] || 'Not recorded';
const cls = value => ['completed','passed','published','validated'].includes(value) ? 'good' : ['failed','rejected','missing','conflict'].includes(value) ? 'bad' : ['running','pending','interrupted'].includes(value) ? 'warn' : 'quiet';
const badge = (value,label) => `<span class="status ${cls(value)}">${esc(label || value || 'Not recorded')}</span>`;
const empty = (title,detail) => `<div class="empty-state"><strong>${esc(title)}</strong>${esc(detail)}</div>`;
const metric = (label,value,detail='') => `<div class="card"><div class="k">${esc(label)}</div><div class="v">${esc(value)}${detail ? `<small>${esc(detail)}</small>`:''}</div></div>`;
const time = value => {if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':d.toLocaleString('en-GB',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'});};
const duration = value => value == null ? '—' : value < 60 ? `${Math.round(value)}s` : value < 3600 ? `${Math.floor(value/60)}m ${Math.round(value%60)}s` : `${Math.floor(value/3600)}h ${Math.floor(value%3600/60)}m`;
const short = value => value && value.length > 22 ? value.slice(0,12)+'…'+value.slice(-6) : value || '—';
const tasks = () => Array.isArray(data.tasks) ? data.tasks : [];
const batches = () => Array.isArray(data.batches) ? data.batches : [];
const historyRows = () => Array.isArray(data.history) ? data.history : [];
const key = row => [row.batch_id || 'latest',row.run_id || '',row.id].join('::');
let page = ['overview','tasks','validation','runs'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
let selectedTask = null;
let modalOpener = null;
const openFolds = new Set();

function showPage(name) {
  if(!['overview','tasks','validation','runs'].includes(name))name='overview';
  page=name;
  document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id==='page-'+name));
  document.querySelectorAll('.nav-item').forEach(el=>{const active=el.dataset.page===name;el.classList.toggle('active',active);if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  $('page-title').textContent=({overview:'Overview',tasks:'Task List',validation:'Validation',runs:'Runs'})[name];
  $('page-sub').textContent=({overview:`${number(data.batch_count)} published ${data.batch_count===1?'batch':'batches'} · ${number(data.published_task_count)} current tasks · ${data.status || 'never-run'}`,tasks:'Per-batch breakdown and task-level evidence — select a batch or filter the list',validation:'Fresh Oracle = 1 and NOP = 0 · latest run evidence',runs:'Archived attempts — successes, failures and interrupted runs'})[name];
  if(location.hash!=='#'+name)window.history.replaceState(null,'','#'+name);
}

function renderOverview(){
  const v=data.validation || {}, o=v.oracle || {}, n=v.nop || {};
  const latestVerified=Array.isArray(data.verified_tasks)?data.verified_tasks.length:null;
  const rate=data.total_candidates>0 && ['completed','failed','interrupted'].includes(data.status) ? (100*(latestVerified||0)/data.total_candidates).toFixed(1)+'%' : '—';
  const status=data.status || 'never-run';
  let body=`<div class="section-head">Statistics</div><div class="cards">`+
    metric('Published tasks',number(data.published_task_count),'current manifest')+
    metric('Published batches',number(data.batch_count),'current + retained')+
    metric('Candidates',number(data.total_candidates),'latest attempt')+
    (data.source && data.source!=='tasks' ? metric('Questions collected',number(data.collected_questions),'latest selected seeds')+metric('Generation completed',number(data.generation_completed),'includes failed candidates'):'')+
    metric('Oracle passes',o.recorded ? number(o.passed):'—','latest recorded trials')+
    metric('NOP passes',n.recorded ? number(n.passed):'—','latest recorded trials')+
    metric('Rejected',data.run_id ? number(data.rejected_count):'—','latest attempt')+
    metric('Verified yield',rate,'latest verified / candidates')+
    metric('Archived runs',number(data.archive_count),'all recorded attempts')+`</div>`;
  if(status==='never-run')body+=empty('No curation run yet','Set up and check a finite batch, then run Terminal Curator. No tasks or validation results are inferred from an empty workspace.');
  else {
    body+=`<div class="section-head">Current run</div><div class="panel"><div class="run-heading"><h2>${badge(status,status.replace('-',' '))}</h2><span class="mono">${esc(data.run_id)}</span></div><div class="run-meta"><span>Source: ${esc(sourceName(data.source))}</span><span>Stage: ${esc(stageName(data.stage))}</span>${data.source!=='tasks'&&data.generation_completed!=null?`<span>Generation: ${number(data.generation_completed)} / ${number(data.collected_questions)} completed · ${number(data.generation_failed)} failed</span>`:''}<span>Started: ${esc(time(data.started_at))}</span><span>Updated: ${esc(time(data.updated_at || data.completed_at))}</span></div>`;
    const phases=['check','collect','generate','normalize','validate-oracle','validate-nop','publish'];
    const index=phases.indexOf(data.stage);
    body+=`<div class="stage-flow">`+phases.map((stage,i)=>{const skipped=data.source==='tasks'&&stage==='generate';const state=skipped?'skipped':data.status==='completed' || (data.status==='running' && index>i)?'done':data.status==='running'&&index===i?'current':'';return `<div class="stage-step ${state}"><span class="step-no">${skipped?'SKIPPED':String(i+1).padStart(2,'0')}</span>${esc(stageName(stage))}</div>`;}).join('')+`</div>`;
    if(['failed','interrupted','unavailable'].includes(status))body+=`<div class="mini">This attempt ${esc(status)}. Any previously published batch is retained; detailed failure text stays in local run evidence.</div>`;
    body+=`</div>`;
  }
  body+=`<details class="fold" data-fold="pipeline"><summary>› Pipeline funnel<span class="hint">measured stages in the latest attempt</span></summary><div class="fold-body"><table><thead><tr><th>Stage</th><th>Count</th><th>Scope</th></tr></thead><tbody><tr><td>Questions selected</td><td>${esc(number(data.collected_questions))}</td><td>${data.source==='tasks'?'not used for imports':'latest run'}</td></tr><tr><td>Candidates attempted</td><td>${esc(number(data.total_candidates))}</td><td>latest run</td></tr><tr><td>Oracle trials recorded</td><td>${o.recorded ? number(o.recorded):'—'}</td><td>latest run</td></tr><tr><td>NOP trials recorded</td><td>${n.recorded ? number(n.recorded):'—'}</td><td>latest run</td></tr><tr><td>Runtime verified</td><td>${data.run_id ? number(latestVerified):'—'}</td><td>latest run</td></tr><tr><td>Published tasks</td><td>${number(data.published_task_count)}</td><td>current publication</td></tr></tbody></table><p class="mini">Source scan totals and semantic approval rates are not recorded, so they are not estimated.</p></div></details>`;
  const sources={};batches().filter(b=>b.current).forEach(b=>Object.entries(b.source_counts||{}).forEach(([s,c])=>sources[s]=(sources[s]||0)+c));
  body+=`<details class="fold" data-fold="composition"><summary>› Task composition<span class="hint">metadata from the current publication</span></summary><div class="fold-body"><div class="grid2"><section class="tag-card"><h3>Sources</h3>${distribution(sources,sourceName)}</section><section class="tag-card"><h3>Difficulty labels <span>when recorded</span></h3>${distribution(batches().find(b=>b.current)?.difficulty_counts||{},s=>s==='unknown'?'Not recorded':s)}</section></div></div></details>`;
  if(Object.keys(data.token_usage||{}).length)body+=`<details class="fold" data-fold="usage"><summary>› Generation usage<span class="hint">reported numeric usage, no billing estimate</span></summary><div class="fold-body"><div class="cards">`+Object.entries(data.token_usage).map(([k,v])=>metric(k.replaceAll('_',' '),number(v))).join('')+`</div></div></details>`;
  $('overview-content').innerHTML=body;
}
function distribution(values,label){
  const rows=Object.entries(values),total=rows.reduce((sum,[,n])=>sum+n,0);
  if(!total)return '<p class="muted">No published metadata available.</p>';
  return '<div class="tag-rows">'+rows.map(([name,count])=>`<div class="tag-row"><span class="tag-name">${esc(label(name))}</span><span class="tag-track"><span class="tag-fill" style="width:${Math.max(0,Math.min(100,100*count/total))}%"></span></span><span class="tag-count">${number(count)} (${(100*count/total).toFixed(0)}%)</span></div>`).join('')+'</div>';
}
function renderBatches(){
  $('batch-list').innerHTML=batches().map(b=>`<div class="ds-line"><button class="ds-row batch-row" data-batch="${esc(b.id)}"><span><span class="name">${b.current?'Current publication':'Retained batch'}</span><span class="desc mono">${esc(short(b.id))}</span></span><span><span class="k">Listed</span><span class="v">${number(b.listed_count)}</span></span><span><span class="k">Available</span><span class="v">${number(b.available_count)}</span></span><span><span class="k">Source</span><span class="v wrap">${esc(Object.keys(b.source_counts||{}).length===1?sourceName(Object.keys(b.source_counts)[0]):'Mixed')}</span></span><span><span class="k">State</span><span class="v">${b.current?'Current':'Retained'}</span></span><span><span class="k">Run</span><span class="desc mono">${esc(short(b.run_id))}</span></span></button><button class="samples-btn" data-batch="${esc(b.id)}">Tasks <span class="n">${number(b.shown_count)}</span></button></div>`).join('') || empty('No published batches','Only a verified manifest creates a publication. In-progress and rejected candidates stay separate.');
  const select=$('batch-filter'),value=select.value;
  let options='<option value="">All batches</option><option value="latest">Latest, unpublished</option>'+batches().map(b=>`<option value="${esc(b.id)}">${b.current?'Current':'Retained'} · ${esc(short(b.id))}</option>`).join('');
  if(value&&value!=='latest'&&!batches().some(b=>b.id===value))options+=`<option value="${esc(value)}">Unavailable · ${esc(short(value))}</option>`;
  if(select.innerHTML!==options)select.innerHTML=options;
  select.value=value;
}
function taskTable(rows){
  if(!rows.length)return empty('No matching tasks','Try another status, source, batch or search. An empty list never implies a successful validation.');
  return `<div class="table-wrap" tabindex="0" aria-label="Task evidence table"><table><thead><tr><th>Task</th><th>Status</th><th>Source</th><th>Oracle</th><th>NOP</th><th>Difficulty</th></tr></thead><tbody>`+rows.map(row=>`<tr><td class="task-name"><button data-task="${esc(key(row))}">${esc(row.id)}</button>${row.tags?.length?`<div>${row.tags.slice(0,3).map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div>`:''}</td><td>${badge(row.status,taskStatus(row.status))}${row.phase?`<div class="mini">${esc(stageName(row.phase))}</div>`:''}</td><td>${esc(sourceName(row.source))}</td><td>${badge(row.oracle?.status,gateName(row.oracle))}</td><td>${badge(row.nop?.status,gateName(row.nop))}</td><td>${esc(row.difficulty||'—')}</td></tr>`).join('')+`</tbody></table></div>`;
}
function renderTasks(){
  const query=$('task-search').value.trim().toLowerCase(),status=$('status-filter').value,source=$('source-filter').value,batch=$('batch-filter').value;
  const rows=tasks().filter(row=>(!query||[row.id,...row.tags||[]].join(' ').toLowerCase().includes(query))&&(!status||row.status===status)&&(!source||(row.source||'unknown')===source)&&(!batch||(row.batch_id||'latest')===batch));
  $('task-table').innerHTML=taskTable(rows);$('task-count').textContent=`${rows.length} / ${tasks().length} shown`;
  document.querySelectorAll('[data-batch]').forEach(el=>el.classList.toggle('active',!!batch&&el.dataset.batch===batch));
}
function renderValidation(){
  const summary=data.validation||{};
  let body='<div class="section-head">Latest validation</div><div class="cards">';
  ['oracle','nop'].forEach(agent=>{const s=summary[agent]||{};body+=metric(agent==='oracle'?'Oracle passes':'NOP passes',s.recorded?number(s.passed):'—',s.recorded?`${number(s.passed)} / ${number(s.recorded)} recorded`:'no trials recorded');});
  body+=metric('Candidates',number(data.total_candidates),'latest run')+metric('Rejected',data.run_id?number(data.rejected_count):'—','includes normalization rejects')+'</div>';
  const rows=tasks().filter(t=>data.run_id&&t.run_id===data.run_id);
  body+=rows.length?taskTable(rows):empty('No validation evidence for this run','Trials appear when the adapter records an Oracle or NOP outcome. Missing results are not counted as NOP reward zero.');
  body+=`<details class="fold" data-fold="acceptance"><summary>› Acceptance rules<span class="hint">runtime gate and evidence scope</span></summary><div class="fold-body"><div class="method-body"><p><strong>Oracle:</strong> reference solution succeeds, reward equals 1, no Harbor exception, and meaningful pytest assertions pass.</p><p><strong>NOP:</strong> a separate fresh environment receives reward 0 because assertions fail, with no collection/runtime errors.</p><p><strong>Evidence:</strong> task details distinguish raw reward/JUnit from report-only verdicts when old validation files have been cleaned. Conflicting evidence is flagged.</p><p><strong>Hand-off:</strong> an agent still reviews the requirement/test/reference agreement. This dashboard does not infer semantic approval or a solved Tracer trajectory from runtime validation.</p></div></div></details>`;
  $('validation-content').innerHTML=body;
}
function renderRuns(){
  const rows=historyRows();
  $('runs-content').innerHTML='<div class="section-head">Run history</div>'+ (rows.length?`<div class="table-wrap" tabindex="0" aria-label="Archived run history"><table><thead><tr><th>Run</th><th>Status</th><th>Source</th><th>Started</th><th>Duration</th><th>Candidates</th><th>Verified</th></tr></thead><tbody>`+rows.map(row=>`<tr><td class="left"><span class="mono">${esc(row.id)}</span><div class="mini">${esc(short(row.run_id))}</div></td><td>${badge(row.status,row.status)}</td><td>${esc(sourceName(row.source))}</td><td>${esc(time(row.started_at))}</td><td class="mono">${esc(duration(row.duration_sec))}</td><td>${number(row.candidates)}</td><td>${number(row.verified_count)}</td></tr>`).join('')+'</tbody></table></div>':empty('No archived runs','The first start.sh execution creates an archive on exit, including failed or interrupted attempts.'))+'<p class="footer-note">Newest archives first. A launcher archive and a batch run have different IDs; both are shown when their matching report exists. Missing measurements are left blank, rather than replaced with zeros.</p>';
}
function reasonName(reason){return ({'reward-mismatch':'Reward does not match the gate','trial-exception':'Harbor trial exception','duplicate-trial':'Duplicate trial results','missing-trial':'Missing trial result','verifier-evidence':'Missing or invalid assertion evidence','validation-rejected':'Validation rejected; inspect local evidence','evidence-conflict':'Report and raw evidence disagree','checksum-mismatch':'Harbor task checksum does not match the validation report'})[reason]||'—';}
function trialDetail(title,trial){
  trial=trial||{};
  const rows=[['Verdict',gateName(trial)],['Evidence',({raw:'Reward + JUnit recorded',partial:'Reward recorded; JUnit unavailable',report:'Report only; raw files unavailable',none:'Not recorded'})[trial.basis]||'Not recorded'],['Task linkage',trial.checksum_matches===true?'Harbor checksum matches':trial.checksum_matches===false?'Harbor checksum mismatch':'Not recorded'],['Expected reward',trial.expected_reward],['Actual reward',trial.reward],['Tests',trial.tests],['Assertion failures',trial.failures],['Verifier errors',trial.errors],['Skipped tests',trial.skipped],['Outcome',reasonName(trial.reason)]];
  return `<div class="panel"><h2>${esc(title)} <span>${badge(trial.status,gateName(trial))}</span></h2><table class="evidence-table">`+rows.map(([label,value])=>`<tr><td>${esc(label)}</td><td class="mono">${esc(value)}</td></tr>`).join('')+'</table></div>';
}
function activeTaskButton(taskKey){
  return [...document.querySelectorAll('.page.active [data-task]')].find(el=>el.dataset.task===taskKey);
}
function updateTaskDetail(){
  const row=tasks().find(t=>key(t)===selectedTask);
  if(!row){
    $('task-sub').textContent='Not included in the current snapshot';
    $('task-detail').innerHTML=empty('Task unavailable in this snapshot','Previously displayed verifier outcomes have been cleared. Close this dialog and select a task from the current list.');
    return;
  }
  $('task-title').textContent=row.id;$('task-sub').textContent=`${taskStatus(row.status)} · ${sourceName(row.source)}`;
  $('task-detail').innerHTML=`<div class="detail-grid">${trialDetail('Oracle',row.oracle)}${trialDetail('NOP',row.nop)}</div><div class="sub">Recorded metadata</div><table><tr><td>Batch</td><td class="mono">${esc(row.batch_id)}</td></tr><tr><td>Run</td><td class="mono">${esc(row.run_id)}</td></tr><tr><td>Harbor task checksum</td><td class="mono">${esc(row.harbor_checksum)}</td></tr><tr><td>Block file digest</td><td class="mono">${esc(row.block_checksum)}</td></tr><tr><td>Difficulty</td><td>${esc(row.difficulty)}</td></tr><tr><td>Question ID</td><td class="mono">${esc(row.question_id)}</td></tr><tr><td>Tags</td><td>${(row.tags||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join('')||'—'}</td></tr></table><p class="explanation">Task text, source files, solutions, prompts and host paths are intentionally not part of this view. Runtime gate results do not certify semantic review or a successful solving trajectory.</p>`;
}
function openModal(id,opener){closeModals(false);modalOpener=opener;$(id).hidden=false;$(id).querySelector('[data-close]').focus();}
function closeModals(restore=true){
  const panels=[...document.querySelectorAll('.modal')],wasOpen=panels.some(el=>!el.hidden);
  panels.forEach(el=>el.hidden=true);
  if(!restore||!wasOpen)return;
  const target=modalOpener?.isConnected&&modalOpener.getClientRects().length
    ?modalOpener:activeTaskButton(selectedTask)||document.querySelector('.nav-item.active');
  target?.focus({preventScroll:true});
}
function renderAll(){
  document.querySelectorAll('.page .fold').forEach(el=>{
    const id=el.closest('.page').id+':'+el.dataset.fold;
    if(el.open)openFolds.add(id);else openFolds.delete(id);
  });
  const scroll=$('main-content').scrollTop, windowScroll=window.scrollY;
  const detailScroll=$('task-detail').scrollTop;
  const focused=document.activeElement;
  const focusedTask=focused?.dataset?.task,focusedBatch=focused?.dataset?.batch;
  const sampleButton=focused?.classList?.contains('samples-btn');
  const focusedFold=focused?.closest?.('.fold');
  const foldFocus=focused?.tagName==='SUMMARY'&&focusedFold?focusedFold.closest('.page').id+':'+focusedFold.dataset.fold:null;
  $('global-stats').innerHTML=[['Batches',data.batch_count],['Published tasks',data.published_task_count],['Latest candidates',data.total_candidates],['Archived runs',data.archive_count]].map(([label,value])=>`<div class="sidebar-stat"><span class="l">${esc(label)}</span><span class="v">${number(value)}</span></div>`).join('');
  $('nav-tasks').textContent=number(data.published_task_count);$('nav-runs').textContent=number(data.archive_count);
  $('snapshot-label').textContent=(LIVE?'Live · ':'Snapshot · ')+time(data.snapshot_at);
  const a=data.availability||{},notices=[];
  if(['config','state','report','index','manifest'].some(k=>a[k]==='unavailable'))notices.push('Some local evidence could not be read. Available values are shown; unavailable measurements remain blank.');
  if(a.ignored_manifest_entries)notices.push('Invalid or duplicate manifest entries were ignored. Inspect the local publication before handing it downstream.');
  if(tasks().some(t=>t.status==='missing'))notices.push('A manifest references unavailable task files. The listed count is not proof that every task can be loaded.');
  if(a.limited)notices.push('This snapshot is bounded. Older batches, runs, or task rows may be omitted from the detail tables.');
  $('notices').innerHTML=notices.map(text=>`<div class="notice">${esc(text)}</div>`).join('');
  renderOverview();renderBatches();renderTasks();renderValidation();renderRuns();showPage(page);
  document.querySelectorAll('.page .fold').forEach(el=>el.open=openFolds.has(el.closest('.page').id+':'+el.dataset.fold));
  if(!$('taskPanel').hidden){updateTaskDetail();$('task-detail').scrollTop=detailScroll;}
  else if(focusedTask)activeTaskButton(focusedTask)?.focus({preventScroll:true});
  else if(foldFocus){
    [...document.querySelectorAll('.page.active .fold')].find(el=>el.closest('.page').id+':'+el.dataset.fold===foldFocus)?.querySelector('summary').focus({preventScroll:true});
  }
  else if(focusedBatch){
    [...document.querySelectorAll('.page.active [data-batch]')].find(el=>el.dataset.batch===focusedBatch&&el.classList.contains('samples-btn')===sampleButton)?.focus({preventScroll:true});
  }
  $('main-content').scrollTop=scroll;
  if(window.scrollY!==windowScroll)window.scrollTo(0,windowScroll);
}
async function refresh(){
  if(!LIVE){location.reload();return;}
  try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw new Error();const next=await response.json();if(next.block!=='terminal-curator')throw new Error();data=next;renderAll();}catch(e){$('snapshot-label').textContent='Refresh unavailable · last snapshot retained';}
}
document.querySelectorAll('[data-page]').forEach(el=>el.addEventListener('click',()=>showPage(el.dataset.page)));
['task-search','status-filter','source-filter','batch-filter'].forEach(id=>$(id).addEventListener(id==='task-search'?'input':'change',renderTasks));
document.addEventListener('click',event=>{const task=event.target.closest('[data-task]');if(task){selectedTask=task.dataset.task;updateTaskDetail();openModal('taskPanel',task);}const batch=event.target.closest('[data-batch]');if(batch){$('batch-filter').value=batch.dataset.batch;renderTasks();$('task-search').scrollIntoView({block:'nearest'});}if(event.target.matches('.modal,[data-close]'))closeModals();});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeModals();if(event.key==='Tab'){const modal=[...document.querySelectorAll('.modal')].find(el=>!el.hidden);if(!modal)return;const items=[...modal.querySelectorAll('button,[href],input,select,[tabindex="0"]')].filter(el=>!el.disabled),first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){last.focus();event.preventDefault();}else if(!event.shiftKey&&document.activeElement===last){first.focus();event.preventDefault();}}});
$('infoToggle').addEventListener('click',event=>openModal('infoPanel',event.currentTarget));
$('themeToggle').addEventListener('click',()=>{const value=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=value;try{localStorage.setItem('curator-theme',value);}catch(e){}});
$('refreshToggle').addEventListener('click',refresh);
window.addEventListener('hashchange',()=>showPage(location.hash.slice(1)));
renderAll();
if(LIVE)setInterval(refresh,10000);
