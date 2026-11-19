/* Shared task dashboard: no task names, hosts, credentials or private data. */
(() => {
  'use strict';
  const root = document.getElementById('task-content');
  if (!root) return;
  const live = root.dataset.live !== 'false';
  const initial = document.getElementById('herdr-dashboard-data');
  let state = initial ? JSON.parse(initial.textContent) : null;
  let busy = false, changed = false, historyOpen = false, signature = '', host;
  const terminal = new Set(['complete', 'stopped']);
  const labels = {preparing:'Preparing', running:'Running', waiting:'Waiting',
    complete:'Complete', error:'Error', stopped:'Stopped', unknown:'Unknown'};
  const style = document.createElement('style');
  style.textContent = `
    .session-dashboard{margin:24px 0 30px;overflow-wrap:anywhere}
    .session-dashboard h2{font-size:24px;margin:0 0 8px}
    .session-dashboard .sd-caption,.session-dashboard .sd-updated{font-size:16px;color:#536173;margin:8px 0 14px}
    .session-dashboard table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:17px}
    .session-dashboard th,.session-dashboard td{padding:12px 10px;vertical-align:top;text-align:left;border-bottom:1px solid #cfd8e3}
    .session-dashboard th{background:#ebf0f5}
    .session-dashboard th:nth-child(1){width:16%}.session-dashboard th:nth-child(2){width:23%}
    .session-dashboard th:nth-child(3){width:27%}.session-dashboard th:nth-child(4){width:18%}
    .session-dashboard tbody tr:nth-child(even){background:#f0f3f7}
    .session-dashboard .sd-status{display:flex;gap:7px;align-items:center;font-weight:650}
    .session-dashboard .sd-dot{display:inline-block;width:11px;height:11px;border-radius:50%;flex:0 0 11px;background:#7b8591}
    .session-dashboard [data-status=running] .sd-dot{background:#218544}
    .session-dashboard [data-status=preparing] .sd-dot,.session-dashboard [data-status=waiting] .sd-dot{background:#b98600}
    .session-dashboard [data-status=error] .sd-dot{background:#c73932}
    .session-dashboard .sd-detail{font-size:15px;line-height:1.5;margin:5px 0}
    .session-dashboard time{font-size:14px;line-height:1.5;display:block}
    .session-dashboard .sd-age{display:block;font-size:14px;color:#536173}
    .session-dashboard button{font:inherit;font-size:14px;background:white;border:1px solid #9baaba;border-radius:5px;padding:5px 9px;cursor:pointer}
    .session-dashboard button:disabled{opacity:.6;cursor:wait}
    .session-dashboard :focus-visible{outline:3px solid #2263bd;outline-offset:3px}
    .session-dashboard .sd-remove{display:block;margin-top:9px}
    .session-dashboard .sd-history{margin-top:20px;border-top:1px solid #cfd8e3;padding-top:14px}
    .session-dashboard summary{font-weight:650;cursor:pointer}
    .session-dashboard .sd-history-item{padding:15px 0;border-bottom:1px solid #cfd8e3}
    .session-dashboard .sd-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
    .session-dashboard .sd-notice{font-size:15px;margin:10px 0}
    .session-dashboard .sd-notice[data-error=true]{color:#a52b27}
    .session-dashboard .sd-copy{width:100%;box-sizing:border-box}
    .session-dashboard .sd-help{font-weight:600}
    @media(max-width:700px){
      .session-dashboard table,.session-dashboard tbody{display:block}
      .session-dashboard thead{display:none}
      .session-dashboard tr{display:block;padding:12px 0;border-bottom:1px solid #cfd8e3}
      .session-dashboard td{display:block;width:auto!important;padding:5px 10px;border:0}
      .session-dashboard td:before{content:attr(data-label);display:block;font-weight:600;font-size:14px;color:#536173}
      .session-dashboard .sd-remove{display:inline-block;margin:6px}
    }`;
  document.head.append(style);
  const make = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  const safeURL = url => {
    try { const parsed = new URL(url); return /^https?:$/.test(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : ''; }
    catch { return ''; }
  };
  function reference(row) {
    return `Subagent: ${row.agent_id || 'Not recorded'}\nTask: ${row.task}\nTask ID: ${row.task_id || row.id}\nReport: ${row.report_url || 'Not recorded'}`;
  }
  function link(row) {
    const url = safeURL(row.report_url);
    if (!url) return make('span', row.task);
    const el = make('a', row.task); el.href = url; return el;
  }
  function age(raw) {
    const delta = Date.now() - Date.parse(raw);
    if (!Number.isFinite(delta)) return 'Time not recorded';
    if (delta < -60000) return 'Timestamp is ahead of this clock';
    const minutes = Math.max(0, Math.floor(delta / 60000));
    return minutes < 1 ? 'Less than a minute ago' : minutes < 60 ? `${minutes} min ago` :
      minutes < 1440 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min ago` :
      `${Math.floor(minutes / 1440)} days ago`;
  }
  function time(raw) {
    const box = make('span');
    if (!raw || !Number.isFinite(Date.parse(raw))) return make('span', 'Not recorded');
    const el = make('time', new Date(raw).toISOString().slice(0,19).replace('T',' ') + ' UTC');
    el.dateTime = raw; el.title = raw; el.tabIndex = 0;
    box.append(el);
    if (live) { const a = make('span', age(raw), 'sd-age'); a.dataset.recorded = raw; box.append(a); }
    return box;
  }
  function notice(text, error=false) {
    const el = host?.querySelector('.sd-notice');
    if (el) { el.textContent = text; el.dataset.error = String(error); }
  }
  async function change(row, action) {
    if (changed) return;
    changed = true;
    host.querySelectorAll('button').forEach(el => el.disabled = true);
    try {
      const response = await fetch(location.pathname + '/dashboard', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id:row.id, action}), cache:'no-store'});
      if (!response.ok) throw Error(await response.text());
      state = await response.json(); render();
      notice(action === 'archive' ? 'Moved to dashboard history. Its report and task reference are retained.' :
        'Restored to the dashboard. This does not restart the task.');
      const target = action === 'archive' ? host.querySelector('.sd-history summary') :
        [...host.querySelectorAll('[data-dashboard-focus]')].find(el => el.dataset.dashboardFocus === 'remove:' + row.id);
      target?.focus({preventScroll:true});
    } catch (_) {
      notice('The change could not be saved. Reconnect and try again.', true);
    } finally {
      changed = false; host?.querySelectorAll('button').forEach(el => el.disabled = false);
    }
  }
  async function copy(row, container, button) {
    try { await navigator.clipboard.writeText(reference(row)); button.textContent = 'Reference copied'; }
    catch {
      let field = container.querySelector('textarea');
      if (!field) { field = make('textarea', undefined, 'sd-copy'); field.readOnly = true; field.rows = 5;
        field.value = reference(row); container.append(field); }
      field.focus(); field.select(); button.textContent = 'Select and copy reference';
    }
  }
  function render() {
    if (!state) return;
    // Legacy publishers can keep sending their table; it supplies this dashboard.
    for (const figure of root.querySelectorAll('figure.comparison')) {
      if (figure.querySelector('h2,h3,h4')?.textContent.trim().toLowerCase() === 'subagent dashboard')
        figure.hidden = true;
    }
    if (!host?.isConnected) {
      host = make('section', undefined, 'session-dashboard');
      host.id = 'session-dashboard'; host.setAttribute('aria-labelledby','session-dashboard-title');
      const header = root.querySelector(':scope > header.task-overview');
      if (header) header.after(host); else root.prepend(host);
      signature = '';
    }
    const next = JSON.stringify(state);
    if (next === signature) return;
    signature = next;
    const focus = document.activeElement?.dataset.dashboardFocus;
    const title = make('h2','Session dashboard'); title.id = 'session-dashboard-title';
    const updated = make('p', undefined, 'sd-updated');
    updated.append(make('strong', 'Last recorded update: '), time(state.updated_at));
    const table = make('table'); table.setAttribute('aria-label','Work in this session');
    const columns = ['Agent', 'Task', 'Status', 'Last updated', 'Needs your help'];
    const head = make('thead'), hr = make('tr');
    for (const label of columns) { const th = make('th', label); th.scope = 'col'; hr.append(th); }
    head.append(hr); const body = make('tbody');
    for (const row of state.rows) {
      const tr = make('tr'); tr.dataset.dashboardId = row.id;
      const cells = columns.map(label => { const td = make('td'); td.dataset.label = label; tr.append(td); return td; });
      const name = make('strong', row.agent_label);
      name.title = row.agent_id ? 'Subagent: ' + row.agent_id : 'Subagent identity not recorded';
      cells[0].append(name); cells[1].append(link(row));
      const status = make('span', undefined, 'sd-status'); status.dataset.status = row.status;
      const dot = make('span', undefined, 'sd-dot'); dot.setAttribute('aria-hidden','true');
      status.append(dot, document.createTextNode(labels[row.status] || 'Unknown'));
      cells[2].append(status);
      if (row.summary) cells[2].append(make('p', row.summary, 'sd-detail'));
      cells[3].append(time(row.updated_at));
      cells[4].append(make('span', row.needs_help || 'No request recorded.', row.needs_help ? 'sd-help' : ''));
      if (live && state.persistent && terminal.has(row.status)) {
        const remove = make('button', 'Remove', 'sd-remove'); remove.type = 'button';
        remove.title = 'Move completed work to dashboard history';
        remove.setAttribute('aria-label','Remove ' + row.task + ' from dashboard');
        remove.dataset.dashboardFocus = 'remove:' + row.id;
        remove.addEventListener('click', () => change(row, 'archive')); cells[0].append(remove);
      }
      body.append(tr);
    }
    table.append(head, body);
    const caption = make('p',
      'Saved reports, not a live process monitor. Green: running; yellow: preparing or waiting; grey: complete or stopped; red: error. Only explicit requests appear under “Needs your help”.',
      'sd-caption');
    const details = make('details', undefined, 'sd-history'); details.id = 'dashboard-history';
    details.open = historyOpen; details.addEventListener('toggle', () => { historyOpen = details.open; });
    details.append(make('summary', `Dashboard history (${state.history.length})`),
      make('p', 'Completed work keeps its report and task reference. Restoring a row does not restart its agent.', 'sd-detail'));
    if (!state.history.length) details.append(make('p','Completed tasks you remove will appear here.','sd-detail'));
    for (const row of state.history) {
      const item = make('div', undefined, 'sd-history-item'); item.append(link(row));
      item.append(make('p','Subagent: ' + (row.agent_id || 'Not recorded'), 'sd-detail'));
      if (row.summary) item.append(make('p', row.summary, 'sd-detail'));
      item.append(time(row.updated_at));
      const actions = make('div', undefined, 'sd-actions');
      const button = make('button','Copy task reference'); button.type = 'button';
      button.addEventListener('click', () => copy(row, item, button)); actions.append(button);
      if (live && state.persistent) {
        const restore = make('button','Restore to dashboard'); restore.type = 'button';
        restore.dataset.dashboardFocus = 'restore:' + row.id;
        restore.addEventListener('click', () => change(row,'restore')); actions.append(restore);
      }
      item.append(actions); details.append(item);
    }
    const message = make('p', undefined, 'sd-notice'); message.setAttribute('role','status');
    message.setAttribute('aria-live','polite');
    const parts = [title, updated, table, caption];
    if (!state.rows.some(row => row.agent_id || row.source === 'saved table' || row.source === 'saved subagent report'))
      parts.push(make('p','No subagent records have been published for this task. Supporting task reports appear automatically.', 'sd-detail'));
    if (!live) parts.push(make('p','Saved snapshot. Open the main task to change dashboard history.', 'sd-detail'));
    parts.push(details, message); host.replaceChildren(...parts);
    if (focus) {
      const target = [...host.querySelectorAll('[data-dashboard-focus]')].find(el => el.dataset.dashboardFocus === focus);
      if (target) target.focus({preventScroll:true});
    }
  }
  async function refresh() {
    if (busy || changed || document.hidden) return;
    busy = true;
    try {
      const response = await fetch(location.pathname + '/dashboard', {cache:'no-store'});
      if (!response.ok) throw Error('Dashboard unavailable');
      state = await response.json(); render();
      if (host?.querySelector('.sd-notice')?.dataset.error === 'true') notice('');
    } catch (_) { notice('Dashboard updates paused; showing the last saved records.', true); }
    finally { busy = false; }
  }
  new MutationObserver(render).observe(root,{childList:true,subtree:true});
  render();
  if (live) {
    refresh(); setInterval(refresh,5000);
    addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    setInterval(() => host?.querySelectorAll('[data-recorded]').forEach(el => {
      const text = age(el.dataset.recorded); if (el.textContent !== text) el.textContent = text;
    }),15000);
  }
})();
