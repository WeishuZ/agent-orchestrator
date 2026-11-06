const $ = s => document.querySelector(s);
const names = {label:'Label and aggregate', qc:'Generate and QC', distribution:'Distribution shaping'};
const samples = {label:'The new treatment reduced symptoms in the study.\nThe restaurant serves lunch and dinner.\nResearchers reported a promising clinical trial.',qc:'Photosynthesis converts light energy into chemical energy. Create a set of learning questions from this material.',distribution:'What is photosynthesis?\nExplain the role of chlorophyll.\nCompare the light-dependent and light-independent reactions.\nHow do plants store energy?\nDescribe the Calvin cycle.'};
let selected = null, state = {runs:[]}, lastSnapshot = ''; 
const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = n => '$' + Number(n || 0).toFixed(6);
function displayStatus(run){return run.status === 'running' && run.steps.some(s=>s.status==='waiting') ? 'waiting' : run.status}
function showForm(){ $('#dialog').showModal(); updateForm(); }
function updateForm(){let w=$('#workflow').value; $('#input').value=samples[w]; $('#input-label').childNodes[0].textContent= w==='qc'?'Source material ':w==='label'?'Documents ':'Items '; $('#input-label span').textContent=w==='qc'?'Text':'One per line'; $('#target-wrap').hidden=w!=='distribution';$('#reviewers-wrap').hidden=w!=='qc'; $('#form-error').textContent=''; }
$('#examples-btn').onclick=async()=>{const button=$('#examples-btn');button.disabled=true;button.textContent='starting…';try{const result=await post('/api/examples',{});selected=result.ids[0];await refresh()}catch(error){alert(error.message)}finally{button.disabled=false;button.textContent='[+] examples'}};
$('#new-btn').onclick=showForm; $('#close').onclick=$('#cancel').onclick=()=>$('#dialog').close(); $('#workflow').onchange=updateForm;
async function post(path,data){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const type=response.headers.get('content-type')||'';if(!type.includes('application/json')){if(path==='/api/message'&&response.status===404)throw Error('This server is running an older version. Restart it to enable chat; in-memory runs will be cleared.');throw Error(`Server returned ${response.status} instead of JSON. Restart the app and try again.`)}const result=await response.json();if(!response.ok)throw Error(result.error||'Request failed');return result}
$('#launch-form').onsubmit=async e=>{e.preventDefault();let w=$('#workflow').value, text=$('#input').value.trim(), lines=text.split('\n').map(s=>s.trim()).filter(Boolean), inputs=w==='qc'?{source:text,reviewers:$('#reviewers').value==='multiple'?['accuracy_reviewer','clarity_reviewer']:['qc_reviewer']}:w==='label'?{documents:lines}:{items:lines,target:$('#target').value};try{let result=await post('/api/runs',{workflow:w,inputs,run_budget:$('#run-budget').value?Number($('#run-budget').value):null,call_budget:$('#call-budget').value?Number($('#call-budget').value):null});selected=result.id;$('#dialog').close();await refresh()}catch(error){$('#form-error').textContent=error.message}};
let selectedNode = null;
function graphMarkup(run) {
 let steps = run.steps;
 if (run.workflow === 'qc') {
   const sessions=Object.values(run.sessions||{});
   const groups=sessions.length?sessions.map(session=>run.steps.filter(s=>s.session_id===session.id)):['generator','qc_reviewer','gate'].map(role=>run.steps.filter(s=>s.role===role));
   steps=groups.flatMap(instances=>{
     if(!instances.length)return [];
     const first=instances[0], latest=instances[instances.length-1];
     const role=instances[0].role;
     const memberIds=new Set(instances.map(s=>s.id));
     const parentIds=[...new Set(instances.flatMap(s=>s.dependencies||[]).filter(id=>!memberIds.has(id)))];
     const parents=parentIds.map(id=>run.steps.find(s=>s.id===id)).filter(Boolean);
     const mappedParents=parents.map(parent=>run.steps.find(s=>s.session_id===parent.session_id)?.id||parent.id);
     const dependencies=[...new Set(mappedParents)].filter(id=>id!==instances[0].id);
     // A session view summarizes recurrence; keep forward generator -> review -> gate edges.
     const forwardDependencies=role==='generator'?[]:dependencies;
     const questions=(run.events||[]).filter(e=>instances.some(s=>s.id===e.step_id) && ['question','chat_question'].includes(e.kind)).length;
     const feedback=role==='generator'?run.steps.filter(s=>s.role==='gate'&&s.output?.decision==='revise').length:0;
     return [{...latest,id:first.id,sessionNodeId:latest.id,dependencies:forwardDependencies,questions,feedback,sessionVersions:instances.length}];
   });
 }
 if (!steps.length) return '<div class="graph-empty">Steps will appear here as the workflow runs.</div>';
 const byId = new Map(steps.map(s => [s.id, s]));
 const depths = new Map();
 function depth(s, visiting = new Set()) {
   if (depths.has(s.id)) return depths.get(s.id);
   if (visiting.has(s.id)) return 0;
   visiting.add(s.id);
   const parents=(s.dependencies || []).filter(id=>byId.has(id));
   const d=parents.length ? 1+Math.max(...parents.map(id=>depth(byId.get(id),new Set(visiting)))) : 0;
   depths.set(s.id,d); return d;
 }
 const columns=[];
 steps.forEach(s=>{const d=depth(s); (columns[d] ||= []).push(s)});
 const rowHeight=run.workflow==='distribution'?96:72, colWidth=run.workflow==='distribution'?235:205, nodeWidth=run.workflow==='distribution'?194:164, nodeHeight=run.workflow==='distribution'?78:56, pad=18;
 const itemCount=run.workflow==='distribution'?(run.inputs?.items?.length||0):0;
 const maxRows=Math.max(itemCount,...columns.map(c=>c.length));
 const height=Math.max(190,maxRows*rowHeight+pad*2+58), width=Math.max(300,columns.length*colWidth+pad*2-40);
 const pos=new Map();
 columns.forEach((column,c)=>column.forEach((step,i)=>{
   const y=run.workflow==='distribution' && Number.isInteger(step.item_index) ? (height-itemCount*rowHeight)/2+step.item_index*rowHeight : run.workflow==='distribution'&&step.display_name ? (height-nodeHeight)/2 : (height-column.length*rowHeight)/2+i*rowHeight;
   pos.set(step.id,{x:pad+c*colWidth,y});
 }));
 const lines=steps.flatMap(s=>(s.dependencies||[]).filter(id=>pos.has(id)).map(id=>{const from=pos.get(id),to=pos.get(s.id),x1=from.x+nodeWidth,y1=from.y+nodeHeight/2,x2=to.x,y2=to.y+nodeHeight/2,mid=(x1+x2)/2;return `<path d="M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2-4} ${y2}" marker-end="url(#arrow)"/>`})).join('');
 const nodes=steps.map(s=>{
   const p=pos.get(s.id), isItem=run.workflow==='distribution' && Number.isInteger(s.item_index);
   const action=s.role==='difficulty_scorer'?(s.iteration>0?'re-score':'score'):s.role==='hardener'?'harden':s.role==='softener'?'soften':s.role;
   const title=isItem?`Item ${s.item_index+1} · ${action}`:(s.display_name||s.role).replaceAll('_',' ');
   const original=isItem?String(run.inputs.items[s.item_index]):'';
   const count=s.sessionVersions?`${s.sessionVersions} pass${s.sessionVersions===1?'':'es'}`:run.workflow==='distribution'?`round ${s.iteration+1}`:`v${s.iteration+1}`;
   return `<button class="graph-node ${escapeHTML(s.status)} ${isItem&&s.iteration>0?'revision-node':''}" style="left:${p.x}px;top:${p.y}px;width:${nodeWidth}px;height:${nodeHeight}px" data-node="${escapeHTML(s.sessionNodeId || s.id)}" ${isItem?`data-item="${s.item_index}" title="${escapeHTML(original)}"`:''} aria-label="${escapeHTML(title)}, ${escapeHTML(count)}, ${escapeHTML(s.status)}"><span class="node-title">${escapeHTML(title)}</span><span class="node-meta"><span class="node-dot"></span>${s.status==='waiting'?'Needs input':escapeHTML(s.status)} <span>· ${escapeHTML(count)}</span></span>${isItem?`<span class="item-caption">${escapeHTML(original)}</span>`:''}${s.display_name&&s.output?.actual_counts?`<span class="histogram-summary">target ${s.output.target_counts.join(' · ')}<br>actual&nbsp; ${s.output.actual_counts.join(' · ')}</span>`:''}${s.questions||s.feedback?`<span class="node-badges">${s.feedback?`↻ ${s.feedback} feedback`:''}${s.questions?` ? ${s.questions}`:''}</span>`:''}</button>`
 }).join('');
 return `<div class="graph-scroll" aria-label="Workflow dependency graph"><div class="graph-canvas" style="width:${width}px;height:${height}px"><svg class="graph-edges" width="${width}" height="${height}" aria-hidden="true"><defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#66c8e8" stroke="none"/></marker><marker id="loop-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#ffae39" stroke="none"/></marker></defs>${lines}</svg>${nodes}</div></div>`;
}
function renderNode() {
 const r=state.runs.find(x=>x.id===selected);
 let step=r?.steps.find(s=>s.id===selectedNode);
 if(step && r.workflow==='qc'){const all=r.steps.filter(s=>step.session_id?s.session_id===step.session_id:s.role===step.role);step=all[all.length-1];selectedNode=step.id}
 if(!step){if($('#node-dialog').open)$('#node-dialog').close();selectedNode=null;return}
 const input=$('#node-content .answer-input'), draft=input?.value || '', focused=document.activeElement===input, cursor=input?.selectionStart;
 const oldLog=$('#node-content .session-log'), shouldFollow=!oldLog || oldLog.scrollHeight-oldLog.scrollTop-oldLog.clientHeight<40, scrollTop=oldLog?.scrollTop||0;
 const openDetails=new Set([...document.querySelectorAll('#node-content details[open]')].map(el=>el.dataset.detail));
 const deps=(step.dependencies||[]).map(id=>r.steps.find(s=>s.id===id)).filter(Boolean);
 const sessionSteps=r.workflow==='qc'?r.steps.filter(s=>step.session_id?s.session_id===step.session_id:s.role===step.role):[step];
 const sessionIds=new Set(sessionSteps.map(s=>s.id));
 let events=(r.events||[]).filter(event=>sessionIds.has(event.step_id));
 const timeOf=e=>new Date(e.time*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
 const mainEvents=events.filter(e=>['question','answer_received','call_completed','call_failed','chat_message','chat_completed','chat_question','chat_failed','review_feedback','compute_completed'].includes(e.kind));
 const background=events.filter(e=>!['question','answer_received','call_completed','call_failed','chat_message','chat_completed','chat_question','chat_failed','review_feedback','compute_completed'].includes(e.kind));
 let questionNumber=0;
 const timeline=mainEvents.map(e=>{
   const time=escapeHTML(timeOf(e));
   if(e.kind==='answer_received'||e.kind==='chat_message') return `<div class="chat-user"><span class="chat-user-content">› ${escapeHTML(e.data?.answer||'')}</span><time>${time}</time></div>`;
   if(e.kind==='review_feedback') return `<div class="chat-message feedback-message"><div class="chat-message-header"><strong>QC feedback #${e.data.number}</strong><time>${time}</time></div><pre class="chat-result">${escapeHTML(JSON.stringify(e.data.findings,null,2))}</pre></div>`;
   if(e.kind==='question'||e.kind==='chat_question') return `<div class="chat-message"><div class="chat-message-header"><strong>${escapeHTML(step.role)} · question #${++questionNumber}</strong><time>${time}</time></div><div>${escapeHTML(e.message)}</div></div>`;
   if(e.kind==='call_failed'||e.kind==='chat_failed') return `<div class="chat-message chat-error"><div class="chat-message-header"><strong>Call failed</strong><time>${time}</time></div>${escapeHTML(e.message)}</div>`;
   const usage=e.data?.usage, output=e.data?.output;
   return `<div class="chat-message"><div class="chat-message-header"><strong>${escapeHTML(e.role||step.role)} · result</strong><time>${time}</time></div><pre class="chat-result">${escapeHTML(JSON.stringify(output,null,2))}</pre>${usage?`<div class="chat-usage">${usage.tokens_in} input · ${usage.tokens_out} output · ${money(usage.cost_usd)}</div>`:''}</div>`;
 }).join('');
 const activity=background.map(e=>`<div class="activity-row"><time>${escapeHTML(timeOf(e))}</time><span>${escapeHTML(e.message)}</span>${e.data?`<pre>${escapeHTML(JSON.stringify(e.data,null,2))}</pre>`:''}</div>`).join('');
 const attempts=sessionSteps.reduce((sum,s)=>sum+s.attempts.length,0), waiting=step.status==='waiting' || step.chat_status==='waiting';
 const busy=['queued','running'].includes(step.chat_status) || ['queued','running'].includes(step.status);
 const canSend=step.kind!=='compute' && !r.stop_reason && !busy && (waiting || step.status==='completed');
 const stateLabel=busy?'Processing…':waiting?'Waiting for your answer':step.status;
 $('#node-content').innerHTML=`<div class="chat-header"><div><h2 id="node-title">~/runs/${escapeHTML(r.id.slice(0,6))}/${escapeHTML(step.display_name||step.role)} <span>· round ${step.iteration+1}</span></h2><p>session ${escapeHTML(step.session_id||step.id)} · ${step.kind==='compute'?'deterministic computation':`${attempts} ${attempts===1?'attempt':'attempts'}`}</p></div><div class="session-header-actions"><span class="status ${escapeHTML(step.status)}">● ${escapeHTML(stateLabel)}</span><button type="button" id="close-node" class="icon-button" aria-label="Close node details">×</button></div></div><div class="session-log chat-transcript" role="log" aria-label="Agent session history"><details class="chat-disclosure" data-detail="input" ${openDetails.has('input')?'open':''}><summary>◆ Input &amp; dependencies</summary><div class="disclosure-content"><pre>${escapeHTML(JSON.stringify(step.inputs,null,2))}</pre><span>Dependencies: ${deps.length?deps.map(d=>escapeHTML(d.role)+' #'+escapeHTML(d.id.slice(0,5))).join(' · '):'none'}</span></div></details>${background.length?`<details class="chat-disclosure" data-detail="activity" ${openDetails.has('activity')?'open':''}><summary>◆ Activity <span>(${background.length})</span></summary><div class="disclosure-content">${activity}</div></details>`:''}<details class="chat-disclosure" data-detail="tools" ${openDetails.has('tools')?'open':''}><summary>◆ Tools &amp; thinking</summary><div class="disclosure-content session-muted">This executor does not report tool calls or thinking.</div></details>${timeline||'<div class="chat-empty">Waiting for the agent’s first response.</div>'}${step.status==='running'||['queued','running'].includes(step.chat_status)?'<div class="chat-processing">◌ Processing…</div>':''}${step.status==='queued'?'<div class="chat-processing">◌ Waiting for a worker…</div>':''}${step.error?`<div class="chat-error">${escapeHTML(step.error)}</div>`:''}</div><div class="chat-composer-wrap"><div class="chat-composer"><span>›</span><textarea class="answer-input" rows="2" aria-label="Chat message" placeholder="${waiting?'Type your answer…':canSend?'Message this agent…':busy?'Waiting for the current call…':step.kind==='compute'?'Deterministic computation · no chat':'Messaging unavailable for this run'}" ${canSend?'':'disabled'}></textarea><button class="answer-btn" ${canSend?'':'disabled'} aria-label="Send message">Send ↵</button></div><div class="error" id="answer-error" role="alert"></div><div class="chat-footer"><span>${canSend?'Enter to send · Shift+Enter for a new line':busy?'Agent is processing the current call.':'This session cannot accept messages.'}</span><span>${step.kind==='compute'?'Compute · no charge':'AgentExecutor'} · ${escapeHTML(step.status)}</span></div></div>`;
 $('#close-node').onclick=()=>$('#node-dialog').close();
 const log=$('#node-content .session-log');if(log)log.scrollTop=shouldFollow?log.scrollHeight:scrollTop;
 const answer=$('#node-content .answer-input');if(answer){answer.value=draft;if(focused&&!answer.disabled){answer.focus();answer.setSelectionRange(cursor,cursor)}}
 const send=async()=>{if(!canSend)return;const button=$('#node-content .answer-btn');button.disabled=true;try{await post('/api/message',{run_id:r.id,step_id:step.id,message:answer.value});answer.value='';await refresh()}catch(e){$('#answer-error').textContent=e.message;button.disabled=false}};
 const button=$('#node-content .answer-btn');if(button)button.onclick=send;
 if(answer)answer.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(!button.disabled)send()}};
}
$('#node-dialog').addEventListener('close',()=>{selectedNode=null});
let canvasExpanded=false;
let fullscreenRequested=false;
function closeCanvasView(){
 if(!canvasExpanded)return;
 canvasExpanded=false;
 const dialog=$('#node-dialog');
 if(dialog.open)dialog.close();
 document.body.append(dialog);
 $('#graph-fullscreen').hidden=true;
 document.body.classList.remove('canvas-open');
 $('#expand-canvas')?.focus();
}
async function openCanvasView(){
 canvasExpanded=true;
 const panel=$('#graph-fullscreen');
 panel.hidden=false;
 document.body.classList.add('canvas-open');
 panel.append($('#node-dialog'));
 render();
 $('#canvas-exit').focus();
 try { if(panel.requestFullscreen){await panel.requestFullscreen();fullscreenRequested=true} } catch (_) { fullscreenRequested=false }
}
$('#canvas-exit').onclick=async()=>{
 if(document.fullscreenElement===$('#graph-fullscreen')) { try {await document.exitFullscreen()} catch (_) {} }
 closeCanvasView();
};
document.addEventListener('fullscreenchange',()=>{
 if(canvasExpanded&&fullscreenRequested&&document.fullscreenElement!==$('#graph-fullscreen')){fullscreenRequested=false;closeCanvasView()}
});
document.addEventListener('keydown',event=>{
 if(event.key==='Escape' && canvasExpanded && !$('#node-dialog').open){event.preventDefault();$('#canvas-exit').click()}
});
$('#run-filter').onchange=()=>{const value=$('#run-filter').value;const match=state.runs.find(r=>value==='all'||value==='active'&&r.status==='running'||value==='waiting'&&r.steps.some(s=>s.status==='waiting'||s.chat_status==='waiting')||value==='failed'&&['failed','stopped','not_converged'].includes(r.status));if(match){selected=match.id;refresh()}else render()};
function render(){
 const questions=state.runs.flatMap(run=>run.steps.filter(s=>s.status==='waiting'||s.chat_status==='waiting').map(step=>({run,step})));
 const inbox=$('#question-inbox');inbox.hidden=!questions.length;
 inbox.innerHTML=`<div class="inbox-title">? ${questions.length} pending question${questions.length===1?'':'s'}</div>${questions.map(({run,step})=>`<button data-question-run="${run.id}" data-question-step="${step.id}"><strong>${escapeHTML(run.name||names[run.workflow])} / ${escapeHTML(step.role)}</strong><span>${escapeHTML((step.question||step.chat_question)?.text||'Input required')}</span></button>`).join('')}`;
 document.querySelectorAll('[data-question-run]').forEach(b=>b.onclick=async()=>{if($('#node-dialog').open)$('#node-dialog').close();selected=b.dataset.questionRun;await refresh();selectedNode=b.dataset.questionStep;renderNode();$('#node-dialog').showModal()});
 $('#run-count').textContent=state.runs.length; $('#worker-status').textContent=state.workers===null?`agent calls: ${state.active_workers} active · unlimited`:`workers: ${state.active_workers}/${state.workers} active`;
 const filter=$('#run-filter').value;
 const visibleRuns=state.runs.filter(r=>filter==='all'||filter==='active'&&r.status==='running'||filter==='waiting'&&r.steps.some(s=>s.status==='waiting'||s.chat_status==='waiting')||filter==='failed'&&['failed','stopped','not_converged'].includes(r.status));
 $('#runs').innerHTML=visibleRuns.length?visibleRuns.map(r=>`<button class="run ${r.id===selected?'selected':''}" data-run="${r.id}"><div class="run-title">${escapeHTML(r.name || names[r.workflow])}</div><div class="run-meta"><span class="status ${displayStatus(r)}">● ${displayStatus(r)==='waiting'?'Needs input':displayStatus(r)}</span><span>${escapeHTML(r.id.slice(0,6))} · ${money(r.cost_usd)}</span></div></button>`).join(''):`<p class="list-empty">${state.runs.length?'no runs match this filter':'no processes · start a run_'}</p>`;
 document.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>{selected=b.dataset.run;if($('#node-dialog').open)$('#node-dialog').close();refresh()});
 let r=state.runs.find(x=>x.id===selected); if(!r){$('#detail').innerHTML='<div class="empty"><h2>No runs yet</h2><p>Start a workflow to see its graph.</p></div>';return;}
 const scroll=$('#detail .graph-scroll'), scrollLeft=scroll?.scrollLeft || 0, scrollTop=scroll?.scrollTop || 0;
 $('#detail').innerHTML=`<div class="detail-top"><div><h2>${escapeHTML(r.name || names[r.workflow])}</h2><p>${r.demo?'Example · instant mock · ':''}Run ${escapeHTML(r.id)} · ${new Date(r.created*1000).toLocaleString()}</p></div><span class="status ${displayStatus(r)}">● ${displayStatus(r)==='waiting'?'Needs input':displayStatus(r)}</span></div><div class="summary"><div class="metric"><small>Total cost</small><strong>${money(r.cost_usd)}</strong></div><div class="metric"><small>Run limit</small><strong>${r.run_budget===null?'None':money(r.run_budget)}</strong></div><div class="metric"><small>Call limit</small><strong>${r.call_budget===null?'None':money(r.call_budget)}</strong></div><div class="metric"><small>Tokens in / out</small><strong>${r.tokens_in} / ${r.tokens_out}</strong></div></div>${r.error?`<p class="error">${escapeHTML(r.error)}</p>`:''}${r.steps.filter(s=>s.status==='waiting').map(s=>`<button class="waiting-shortcut" data-waiting-node="${s.id}">? ${escapeHTML(s.role)} needs input · open session →</button>`).join('')}<div class="subheading graph-heading"><div><h3>&gt; execution graph <span class="count">${r.steps.length}</span></h3><p>${r.workflow==='qc'?'one session per agent · select a node for its history':r.workflow==='distribution'?'score → compare → revise → score → compare':'nodes: agent calls · arrows: data dependencies'}</p></div><div class="graph-actions"><span class="graph-hint">scroll →</span><button id="expand-canvas" class="secondary" aria-label="Open canvas full screen">⤢ Full screen</button></div></div>${graphMarkup(r)}<p class="graph-caption">${r.workflow==='qc'?'revisions and feedback appear in each agent session':r.workflow==='distribution'?'each item stays on its own row · highlighted nodes are revisions or re-scores · scroll right for later rounds':'select a node for details'}</p>${r.output?`<div class="subheading"><h3>&gt; output</h3></div><pre>${escapeHTML(JSON.stringify(r.output,null,2))}</pre>`:''}`;
 $('#expand-canvas').onclick=openCanvasView;
 if(canvasExpanded){const old=$('#canvas-content .graph-scroll'), x=old?.scrollLeft||0, y=old?.scrollTop||0;$('#canvas-run-name').textContent=r.name||names[r.workflow];$('#canvas-content').innerHTML=graphMarkup(r);const current=$('#canvas-content .graph-scroll');if(current){current.scrollLeft=x;current.scrollTop=y}}
 const nextScroll=$('#detail .graph-scroll');if(nextScroll){nextScroll.scrollLeft=scrollLeft;nextScroll.scrollTop=scrollTop}
 document.querySelectorAll('[data-waiting-node]').forEach(b=>b.onclick=()=>{selectedNode=b.dataset.waitingNode;renderNode();if(!$('#node-dialog').open)$('#node-dialog').showModal()});
 document.querySelectorAll('[data-node]').forEach(b=>b.onclick=()=>{selectedNode=b.dataset.node;renderNode();if(!$('#node-dialog').open)$('#node-dialog').showModal()});
 if($('#node-dialog').open)renderNode();
}
let refreshInFlight=null;
async function refresh(){
 if(refreshInFlight){await refreshInFlight;return refresh()}
 refreshInFlight=(async()=>{
  try{
   const response=await fetch('/api/state?summary=1');if(!response.ok)throw Error('State fetch failed');
   const next=await response.json();
   if(!next.runs.some(run=>run.id===selected))selected=next.runs[0]?.id||null;
   if(selected){const detail=await fetch('/api/runs/'+selected);if(!detail.ok)throw Error('Run fetch failed');const run=await detail.json();next.runs=next.runs.map(r=>r.id===run.id?run:r)}
   const snapshot=JSON.stringify(next);if(snapshot===lastSnapshot)return;lastSnapshot=snapshot;state=next;render();
  }catch(error){console.error(error)}
 })();
 try{await refreshInFlight}finally{refreshInFlight=null}
}
refresh();setInterval(refresh,750);
