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
