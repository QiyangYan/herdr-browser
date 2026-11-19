/* Shared task-attention state. View acknowledgement is distinct from approval. */
(() => {
  if (window.HerdrReview) return;
  const memory = new Map();
  const pending = new Map();
  const key = (owner, goal) => 'herdr-conclusion-v1:' + owner + ':' + goal;
  const token = state => {
    const normalize = value => String(value || '').replace(/\*\*/g, '')
      .replace(/^[-*] +/gm, '').replace(/\s+/g, ' ').trim();
    const value = JSON.stringify(['summary','results','current_task'].map(name => normalize(state[name]))
      .concat(state.status === 'done' ? 'done' : 'update'));
    let hash = 2166136261;
    for (let n = 0; n < value.length; n++) hash = Math.imul(hash ^ value.charCodeAt(n), 16777619) >>> 0;
    return hash.toString(16).padStart(8, '0');
  };
  const read = (owner, goal) => {
    const id = key(owner, goal);
    if (memory.has(id)) return memory.get(id);
    try { return JSON.parse(localStorage.getItem(id) || '{}'); } catch (_) { return {}; }
  };
  const write = (owner, goal, mark) => {
    memory.set(key(owner, goal), mark);
    try { localStorage.setItem(key(owner, goal), JSON.stringify(mark)); } catch (_) {}
    dispatchEvent(new Event('herdr-review-change'));
  };
  const accept = (owner, goal, currentToken, mark, persistent) => {
    if (mark?.token === currentToken) memory.set(key(owner, goal), {...mark, persistent});
    const local = read(owner, goal);
    return local.token === currentToken ? local : {};
  };
  async function record(owner, goal, currentToken, mode, base) {
    let mark;
    try {
      if (window.webkit?.messageHandlers?.herdrReview) {
        const result = await new Promise((resolve,reject) => {
          const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
          const timer = setTimeout(() => {pending.delete(nonce);reject(Error('Review bridge unavailable'));},3000);
          pending.set(nonce,{resolve,reject,timer});
          window.webkit.messageHandlers.herdrReview.postMessage(JSON.stringify({owner,goal,token:currentToken,mode,nonce}));
        });
        mark = {...result.mark,persistent:true};
        write(owner,goal,mark);
        return mark;
      }
      const response = await fetch(base + '/task/' + owner + '/' + goal + '/review', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({token:currentToken, mode})
      });
      if (response.status === 409) return {};
      if (!response.ok) throw Error('Review storage unavailable');
      const result = await response.json();
      mark = {...result.mark, persistent:true};
    } catch (_) {
      // A legacy running report server can still render the new client.
      // Keep its temporary marker visibly distinct from shared persistence.
      const previous = read(owner, goal);
      mark = {token:currentToken, mode:previous.token === currentToken &&
        previous.mode === 'reviewed' ? 'reviewed' : mode, at:Date.now()/1000, persistent:false};
    }
    write(owner, goal, mark);
    return mark;
  }
  function attention(task) {
    if (task.agent_status === 'working' && task.available !== false) return ['green','In progress'];
    if (task.agent_status === 'blocked' && task.available !== false) return ['yellow','Needs input'];
    const conclusions = task.reports.filter(goal => goal.needs_reading);
    if (conclusions.some(goal => !accept(task.owner,goal.id,goal.token,goal.mark,task.persistent).mode))
      return ['yellow','Needs review'];
    if ((task.finished_at||0) > (task.finished_viewed_at||0)) return ['yellow','Finished · check outcome'];
    if (conclusions.length) return ['red',conclusions.every(goal =>
      accept(task.owner,goal.id,goal.token,goal.mark,task.persistent).mode === 'reviewed') ? 'Reviewed' : 'Viewed'];
    return ['red',task.available === false ? 'Task closed' : task.finished_at ? 'Viewed' : 'Idle'];
  }
  let current = null, recording = false;
  function strip() {
    if (!current) return;
    const header = document.querySelector('#task-content .task-overview');
    if (!header) return;
    let row = header.querySelector('.review-strip');
    if (!row) {
      row = document.createElement('div'); row.className = 'review-strip';
      const label = document.createElement('span'); label.className = 'review-state'; label.setAttribute('aria-live','polite');
      const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Mark reviewed';
      button.addEventListener('click', () => acknowledge('reviewed'));
      row.append(label,button); header.append(row);
    }
    const mark = read(current.owner,current.goal), matching = mark.token === current.token;
    row.querySelector('.review-state').textContent = matching ?
      (mark.mode === 'reviewed' ? 'Reviewed' : 'Viewed') : current.complete ? 'New conclusion · needs review' : 'Latest update · needs review';
    row.querySelector('button').disabled = matching && mark.mode === 'reviewed';
    let note = header.querySelector('.review-note');
    if (!note) { note=document.createElement('p'); note.className='review-note'; header.append(note); }
    note.textContent = matching && mark.persistent === false ?
      'Marked in this window. Apply update to save review markers across windows.' :
      'Viewing and interacting marks this Viewed. Mark reviewed confirms your review.';
  }
  async function acknowledge(mode) {
    if (!current || recording) return;
    const mark = read(current.owner,current.goal);
    if (mark.token === current.token && (mark.mode === 'reviewed' || mode === 'viewed')) return;
    recording = true;
    try { await record(current.owner,current.goal,current.token,mode,current.base); strip(); }
    finally { recording = false; }
  }
  function report(state, base, metadata) {
    if (state.status === 'archived' || !(state.summary || state.results)) { current=null; return; }
    current = {owner:state.owner,goal:state.id,base,token:metadata?.token || token(state),complete:state.status==='done'};
    accept(state.owner,state.id,current.token,metadata?.mark,metadata?.persistent);
    strip();
  }
  for (const event of ['pointerup','wheel','touchend','keydown']) {
    document.addEventListener(event, e => {
      if (!current || !e.isTrusted || document.hidden || !document.hasFocus()) return;
      if (e.target?.closest?.('.review-strip button')) return;
      const summary = document.querySelector('#task-summary') ||
        document.querySelector('#results,#task-progress');
      if (!summary) return;
      const box = summary.getBoundingClientRect();
      if (box.bottom > 0 && box.top < innerHeight) acknowledge('viewed');
    }, {passive:true});
  }
  addEventListener('herdr-review-change',strip);
  function nativeResult(nonce,result,error) {
    const request=pending.get(nonce);if(!request)return;
    clearTimeout(request.timer);pending.delete(nonce);
    error?request.reject(Error(error)):request.resolve(result);
  }
  function nativeSnapshot(spaces) {
    for(const space of spaces)for(const task of space.tasks)for(const goal of task.reports)
      accept(task.owner,goal.id,goal.token,goal.mark,true);
    dispatchEvent(new Event('herdr-review-change'));
  }
  window.HerdrReview = {token,read,accept,record,attention,report,nativeResult,nativeSnapshot};
})();

(() => {
  'use strict';
  let etag=null,busy=false,snapshot=null,base=location.pathname.split('/session/')[0],persistent=false;
  const items=document.querySelector('#session-items'),indicator=document.querySelector('#update-indicator');
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const review=window.HerdrReview;
  const metadata=new Map();
  const style=document.createElement('style');
  style.textContent=`.session-guide{margin-bottom:18px}
.attention-legend{list-style:none;display:flex;flex-wrap:wrap;gap:12px 24px;padding:0;font-size:16px}
.attention-legend li,.task-attention{display:flex;align-items:center;gap:9px}
.attention-dot{display:inline-block;flex:none;width:12px;height:12px;border-radius:50%;border:1px solid #0002}
.attention-dot.green{background:#18804a}.attention-dot.yellow{background:#d8a012}.attention-dot.red{background:#bd3342}
.session-options,.space-count,.task-attention,.report-attention{font-size:16px;color:#596575}
.session-space{margin:38px 0 46px;padding-top:22px;border-top:2px solid #c6d0dc}
.session-space>h2{font-size:27px;margin:0 0 6px}
.space-count{margin:0 0 20px}
.session-task{margin:16px 0 24px;padding:0 0 0 18px;border-left:3px solid #d5dde7}
.session-task-heading{display:flex;gap:12px;flex-wrap:wrap;align-items:baseline}
.session-task h3{font-size:21px;margin:0}.task-attention{line-height:1.5}
.session-reports{list-style:none;padding:0;margin:12px 0 0}
.session-report{margin:14px 0 24px}.session-report h4{font-size:19px;margin:0 0 6px}
.session-report p{margin:5px 0}.session-phase{font-size:17px;line-height:1.5}
.quiet-tasks{margin-top:18px;padding:12px 0}.quiet-tasks>summary{font-size:17px;font-weight:600}
.quiet-tasks[open]>.session-task{margin-top:22px}
.session-steps{font-size:16px;margin:10px 0}.session-steps>summary{font-size:16px}
.review-strip{display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:16px;margin-top:18px}
.review-strip button{font-size:16px;padding:7px 11px}.review-note{font-size:15px;color:#596575}
`;
  document.head.append(style);
  if(!document.querySelector('.session-guide')){
    const intro=document.querySelector('main>h1')?.nextElementSibling;
    if(intro?.tagName==='P'){intro.className='session-guide';intro.innerHTML='<strong>Space → task → main report.</strong> Active work and new conclusions come first.';}
    const legend=document.createElement('ul');legend.className='attention-legend';
    legend.innerHTML='<li><span class="attention-dot green" aria-hidden="true"></span>In progress</li><li><span class="attention-dot yellow" aria-hidden="true"></span>Needs review or input</li><li><span class="attention-dot red" aria-hidden="true"></span>Quiet · viewed, reviewed or idle</li>';
    intro?.after(legend);
  }
  const archivedToggle=document.querySelector('main>a[href*="completed="]');
  if(archivedToggle)archivedToggle.textContent=new URL(location.href).searchParams.get('completed')==='true'?
    'Hide archived / closed tasks':'Include archived / closed tasks';
  const quietAge=task=>{
    if(!task.last_activity_at)return '';
    const seconds=Math.max(0,Math.floor(Date.now()/1000-task.last_activity_at));
    const age=seconds<60?'less than a minute':seconds<3600?Math.floor(seconds/60)+' min':seconds<86400?Math.floor(seconds/3600)+' h':Math.floor(seconds/86400)+' days';
    return ' · last activity '+age+' ago';
  };
  function taskHTML(task) {
    const [color,label]=review.attention(task);
    const age=color==='red'?quietAge(task):'';
    const reports=task.reports.map(goal=>{
      const url=base+'/task/'+task.owner+'/'+goal.id,id='goal-'+task.owner+'-'+goal.id;
      const mark=review.accept(task.owner,goal.id,goal.token,goal.mark,persistent);
      const attention=goal.needs_reading?(mark.mode==='reviewed'?'Reviewed':mark.mode==='viewed'?'Viewed':goal.has_conclusion?'New conclusion · needs review':'Latest update · needs review'):goal.legacy?'Saved websites · no living summary yet':'';
      const steps=goal.steps?.length?'<details class="session-steps" id="steps-'+id+'"><summary>Supporting steps · '+goal.steps.length+'</summary><ul>'+goal.steps.map(step=>'<li><a href="'+escape(url+'#'+step.id)+'">'+escape(step.title)+'</a></li>').join('')+'</ul></details>':'';
      return '<li class="session-report" data-goal="'+escape(goal.id)+'"><h4><a id="'+id+'" href="'+escape(url)+'">'+escape(goal.title)+'</a></h4>'+(attention?'<p class="report-attention">'+attention+'</p>':'')+(goal.current_task?'<p class="session-phase"><strong>Current:</strong> '+escape(goal.current_task)+'</p>':'')+steps+'</li>';
    }).join('');
    return '<article class="session-task" id="task-'+task.owner+'" data-owner="'+task.owner+'"><div class="session-task-heading"><h3>Task: '+escape(task.label)+'</h3><span class="task-attention" title="'+escape(label+age)+'"><span class="attention-dot '+color+'" aria-hidden="true"></span><span class="attention-label">'+escape(label+age)+'</span></span></div><ul class="session-reports">'+reports+'</ul></article>';
  }
  function render(spaces) {
    const groups=spaces.map(space=>({...space,tasks:space.tasks.map(task=>({...task,persistent}))}));
    groups.forEach(space=>space.tasks.sort((a,b)=>({yellow:0,green:1,red:2}[review.attention(a)[0]]-({yellow:0,green:1,red:2}[review.attention(b)[0]]))||a.label.localeCompare(b.label)||a.owner.localeCompare(b.owner)));
    groups.sort((a,b)=>Number(a.tasks.every(t=>review.attention(t)[0]==='red'))-Number(b.tasks.every(t=>review.attention(t)[0]==='red'))||a.label.localeCompare(b.label)||a.id.localeCompare(b.id));
    return groups.map(space=>{
      const id=space.dom_id||'space-'+encodeURIComponent(space.id);
      const active=space.tasks.filter(task=>review.attention(task)[0]!=='red'),quiet=space.tasks.filter(task=>review.attention(task)[0]==='red');
      return '<section class="session-space" id="'+escape(id)+'" data-space-id="'+escape(space.id)+'"><h2>Space: '+escape(space.display_label||space.label)+'</h2><p class="space-count">'+active.length+' active or awaiting attention · '+quiet.length+' quiet</p>'+active.map(taskHTML).join('')+(quiet.length?'<details class="quiet-tasks" id="quiet-'+escape(id)+'"><summary>Other tasks · '+quiet.length+'</summary>'+quiet.map(taskHTML).join('')+'</details>':'')+'</section>';
    }).join('')||'<p>No task pages yet.</p>';
  }
  function update(spaces) {
    snapshot=spaces;
    const next=render(spaces);
    // Compare the layout without the server's bootstrap JSON.
    const current=items.cloneNode(true);current.querySelector('#session-data')?.remove();
    if(next===current.innerHTML)return;
    const y=scrollY,focused=document.activeElement?.id;
    const open=[...items.querySelectorAll('details[open]')].map(node=>node.id);
    items.innerHTML=next;items.dataset.layout='spaces-v1';
    open.forEach(id=>{const node=document.getElementById(id);if(node)node.open=true;});
    if(focused&&document.getElementById(focused)){const target=document.getElementById(focused);let ancestor=target.parentElement;while(ancestor&&ancestor!==items){if(ancestor.tagName==='DETAILS')ancestor.open=true;ancestor=ancestor.parentElement;}target.focus({preventScroll:true});}
    scrollTo(0,y);
  }
  async function legacySpaces(doc) {
    const tasks=new Map(),groups=new Map();
    const rows=[...doc.querySelectorAll('#session-items > .task')];
    const owners=[...new Set(rows.map(row=>row.querySelector('h2 a')?.href.match(/\/task\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))];
    // Only old servers need task-home metadata. Three short requests at a time;
    // no extra WebViews or live sites are created.
    let cursor=0;
    await Promise.all(Array.from({length:Math.min(3,owners.length)},async()=>{
      while(cursor<owners.length){
        const owner=owners[cursor++],cached=metadata.get(owner);
        if(cached&&Date.now()-cached.at<60000)continue;
        try {
          const response=await fetch(base+'/task/'+owner+'/start');if(!response.ok)throw Error('Task unavailable');
          const home=new DOMParser().parseFromString(await response.text(),'text/html');
          const labels=[...home.querySelectorAll('p')];
          const workspace=labels.find(p=>p.querySelector('label')?.textContent==='Workspace:')?.textContent.replace(/^Workspace:\s*/,'').trim();
          const status=labels.find(p=>p.querySelector('label')?.textContent==='Status:')?.textContent.match(/^Status:\s*(.*?)Section:/)?.[1]?.trim();
          const name=home.querySelector('h1')?.textContent.replace(/^Task:\s*/,'');
          metadata.set(owner,{label:name,space:workspace,agent_status:({'Working':'working','Needs input':'blocked','Done':'done','Idle':'idle'})[status]||'unknown',at:Date.now()});
        } catch(_) {metadata.set(owner,{at:Date.now()});}
      }
    }));
    for(const row of rows){
      const link=row.querySelector('h2 a'),match=link?.href.match(/\/task\/([a-f0-9]{64})\/([a-zA-Z0-9_-]+)/);if(!match)continue;
      const owner=match[1],meta=metadata.get(owner)||{},line=row.querySelector('p')?.textContent||'';
      const task=tasks.get(owner)||{owner,label:meta.label||line.split(' · Herdr task: ')[1]||'Unnamed',agent_status:meta.agent_status||'unknown',available:!line.startsWith('Herdr task closed'),reports:[]};
      const status=({'Done':'done','Archived':'archived','Working':'working','Blocked':'blocked','Planned':'planned'})[line.split(' · ')[0]]||'planned';
      if((status==='archived'||!task.available)&&new URL(location.href).searchParams.get('completed')!=='true')continue;
      const goal={id:match[2],title:link.textContent,status,legacy:match[2]==='start',current_task:'',mark:{},token:'',steps:[],needs_reading:false,has_conclusion:status==='done'&&match[2]!=='start'};
      if(!goal.legacy){
        try {const response=await fetch(link.href+'/data');if(response.ok){const data=await response.json();goal.current_task=data.state.current_task||'';goal.token=data.review?.token||review.token(data.state);goal.mark=data.review?.mark||{};if(data.review?.persistent){persistent=true;modern=null;}goal.needs_reading=data.state.status!=='archived'&&!!(data.state.summary||data.state.results);goal.has_conclusion=data.state.status==='done'&&goal.needs_reading;}}catch(_){}
      }
      task.reports.push(goal);tasks.set(owner,task);
    }
    for(const task of tasks.values()){
      const label=metadata.get(task.owner)?.space||'Space unavailable';
      // Legacy HTML exposes recorded labels only. Unknown spaces are kept
      // separate rather than guessing ownership from report/task titles.
      const id=label==='Space unavailable'?'unavailable-'+task.owner:'recorded-label:'+label;
      if(!groups.has(id))groups.set(id,{id,label,tasks:[]});groups.get(id).tasks.push(task);
    }
    return [...groups.values()];
  }
  let modern=null;
  function nativeSpaces() {
    const data=window.HerdrNativeSnapshot;if(!data)return null;
    base=data.base;persistent=true;
    const archived=new URL(location.href).searchParams.get('completed')==='true';
    return data.spaces.map(space=>({...space,tasks:space.tasks.filter(task=>archived||task.available).map(task=>({...task,reports:task.reports.filter(goal=>archived||goal.status!=='archived')})).filter(task=>task.reports.length)})).filter(space=>space.tasks.length);
  }
  async function refresh() {
    if(busy||document.hidden||window.getSelection()?.toString())return;
    busy=true;
    try {
      const native=nativeSpaces();if(native){update(native);indicator.textContent='Current tasks';return;}
      if(modern!==false){
        const response=await fetch(location.pathname+'/data'+location.search,{headers:etag?{'If-None-Match':etag}:{}});
        if(response.status===304){if(snapshot)update(snapshot);if(indicator.textContent.startsWith('Updates paused'))indicator.textContent='Current tasks';return;}
        if(response.ok){const data=await response.json();etag=response.headers.get('ETag');base=data.base;persistent=true;modern=true;update(data.spaces);indicator.textContent='Current tasks';return;}
        if(response.status!==404)throw Error('Index unavailable');modern=false;etag=null;
      }
      const url=new URL(location.href);url.searchParams.set('completed','true');
      const response=await fetch(url,{headers:etag?{'If-None-Match':etag}:{}});
      if(response.status===304 && [...metadata.values()].every(row=>Date.now()-row.at<60000))return;
      if(!response.ok&&response.status!==304)throw Error('Index unavailable');
      const html=response.status===304?await (await fetch(url)).text():await response.text();etag=response.headers.get('ETag');
      const doc=new DOMParser().parseFromString(html,'text/html');
      const legacy=await legacySpaces(doc);update(nativeSpaces()||legacy);indicator.textContent='Current tasks';
    } catch(_){indicator.textContent='Updates paused · reconnecting';}
    finally{busy=false;}
  }
  const seed=document.querySelector('#session-data');
  if(seed){try{update(JSON.parse(seed.textContent));}catch(_){}}
  if(items.dataset.live==='false')return;
  refresh();setInterval(refresh, 5000);
  addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  addEventListener('herdr-review-change',()=>{if(snapshot)update(snapshot);});
  addEventListener('herdr-session-snapshot',()=>{const spaces=nativeSpaces();if(spaces)update(spaces);});
  addEventListener('storage',()=>{if(snapshot)update(snapshot);});
})();
