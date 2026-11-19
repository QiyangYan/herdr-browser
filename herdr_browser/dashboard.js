/* Shared task dashboard: no task names, hosts, credentials or private data. */
(() => {
  'use strict';
  const root = document.getElementById('task-content');
  if (!root) return;
  const live = root.dataset.live !== 'false';
  const initial = document.getElementById('herdr-dashboard-data');
  let state = initial ? JSON.parse(initial.textContent) : null;
  let busy = false, changed = false, signature = '', host, ui;
  const rows = new Map(), archived = new Map();
  const columns = ['Agent', 'Task', 'Status', 'Last updated', 'Needs your help'];
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
    .session-dashboard [hidden]{display:none!important}
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
  function age(raw) {
    const delta = Date.now() - Date.parse(raw);
    if (!Number.isFinite(delta)) return 'Time not recorded';
    if (delta < -60000) return 'Timestamp is ahead of this clock';
    const minutes = Math.max(0, Math.floor(delta / 60000));
    return minutes < 1 ? 'Less than a minute ago' : minutes < 60 ? `${minutes} min ago` :
      minutes < 1440 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min ago` :
      `${Math.floor(minutes / 1440)} days ago`;
  }
  function time(raw, box = make('span')) {
    if (box.dataset.timestamp === (raw || '')) return box;
    box.dataset.timestamp = raw || '';
    if (!raw || !Number.isFinite(Date.parse(raw))) { box.textContent = 'Not recorded'; return box; }
    let el = box.querySelector('time');
    if (!el) { el = make('time'); box.replaceChildren(el); }
    el.textContent = new Date(raw).toISOString().slice(0,19).replace('T',' ') + ' UTC';
    el.dateTime = raw; el.title = raw; el.tabIndex = 0;
    if (live) {
      let a = box.querySelector('.sd-age');
      if (!a) { a = make('span', undefined, 'sd-age'); box.append(a); }
      a.textContent = age(raw); a.dataset.recorded = raw;
    }
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
  function text(node, value) {
    if (node.textContent !== value) node.textContent = value;
  }
  function taskLink(container, row) {
    const url = safeURL(row.report_url), tag = url ? 'A' : 'SPAN';
    let node = container.firstElementChild;
    if (node?.tagName !== tag) { node = make(tag.toLowerCase()); container.replaceChildren(node); }
    text(node, row.task);
    if (url && node.getAttribute('href') !== url) node.href = url;
  }
  function ordered(container, records, cache, create, update) {
    const ids = new Set(records.map(row => row.id));
    for (const [id, view] of cache) if (!ids.has(id)) { view.node.remove(); cache.delete(id); }
    let position = container.firstElementChild;
    for (const row of records) {
      let view = cache.get(row.id);
      if (!view) { view = create(row); cache.set(row.id, view); }
      const signature = JSON.stringify(row) + ':' + state.persistent;
      if (signature !== view.signature) { view.row = row; update(view, row); view.signature = signature; }
      if (view.node !== position) container.insertBefore(view.node, position);
      position = view.node.nextElementSibling;
    }
  }
  function workRow(row) {
    const node = make('tr'); node.dataset.dashboardId = row.id;
    const cells = columns.map(label => { const td = make('td'); td.dataset.label = label; node.append(td); return td; });
    const view = {node, cells, name:make('strong'), status:make('span', undefined, 'sd-status'),
      label:make('span'), summary:make('p', undefined, 'sd-detail'), stamp:make('span'), help:make('span')};
    const dot = make('span', undefined, 'sd-dot'); dot.setAttribute('aria-hidden','true');
    view.status.append(dot, view.label);
    cells[0].append(view.name); cells[2].append(view.status, view.summary);
    cells[3].append(view.stamp); cells[4].append(view.help);
    if (live) {
      view.remove = make('button', 'Remove', 'sd-remove'); view.remove.type = 'button';
      view.remove.title = 'Move completed work to dashboard history';
      view.remove.dataset.dashboardFocus = 'remove:' + row.id;
      view.remove.addEventListener('click', () => change(view.row, 'archive'));
      cells[0].append(view.remove);
    }
    return view;
  }
  function updateWork(view, row) {
    text(view.name, row.agent_label);
    view.name.title = row.agent_id ? 'Subagent: ' + row.agent_id : 'Subagent identity not recorded';
    taskLink(view.cells[1], row);
    view.status.dataset.status = row.status; text(view.label, labels[row.status] || 'Unknown');
    text(view.summary, row.summary || ''); view.summary.hidden = !row.summary;
    time(row.updated_at, view.stamp);
    text(view.help, row.needs_help || 'No request recorded.'); view.help.className = row.needs_help ? 'sd-help' : '';
    if (view.remove) {
      view.remove.hidden = !state.persistent || !terminal.has(row.status);
      view.remove.setAttribute('aria-label', 'Remove ' + row.task + ' from dashboard');
    }
  }
  function historyRow(row) {
    const node = make('div', undefined, 'sd-history-item'); node.dataset.dashboardId = row.id;
    const view = {node, task:make('span'), agent:make('p', undefined, 'sd-detail'),
      summary:make('p', undefined, 'sd-detail'), stamp:make('span')};
    const actions = make('div', undefined, 'sd-actions');
    const button = make('button', 'Copy task reference'); button.type = 'button';
    button.addEventListener('click', () => copy(view.row, node, button)); actions.append(button);
    view.copy = button;
    if (live) {
      view.restore = make('button', 'Restore to dashboard'); view.restore.type = 'button';
      view.restore.dataset.dashboardFocus = 'restore:' + row.id;
      view.restore.addEventListener('click', () => change(view.row, 'restore')); actions.append(view.restore);
    }
    node.append(view.task, view.agent, view.summary, view.stamp, actions);
    return view;
  }
  function updateHistory(view, row) {
    taskLink(view.task, row); text(view.agent, 'Subagent: ' + (row.agent_id || 'Not recorded'));
    text(view.summary, row.summary || ''); view.summary.hidden = !row.summary;
    time(row.updated_at, view.stamp);
    if (view.restore) view.restore.hidden = !state.persistent;
    const field = view.node.querySelector('textarea');
    if (field && field.value !== reference(row)) field.value = reference(row);
  }
  function mount() {
    host = make('section', undefined, 'session-dashboard');
    host.id = 'session-dashboard'; host.setAttribute('aria-labelledby','session-dashboard-title');
    const title = make('h2', 'Session dashboard'); title.id = 'session-dashboard-title';
    const updated = make('p', undefined, 'sd-updated'), stamp = make('span');
    updated.append(make('strong', 'Last recorded update: '), stamp);
    const table = make('table'); table.setAttribute('aria-label','Work in this session');
    const head = make('thead'), hr = make('tr'), body = make('tbody');
    for (const label of columns) { const th = make('th', label); th.scope = 'col'; hr.append(th); }
    head.append(hr); table.append(head, body);
    const caption = make('p',
      'Saved reports, not a live process monitor. Green: running; yellow: preparing or waiting; grey: complete or stopped; red: error. Only explicit requests appear under “Needs your help”.', 'sd-caption');
    const empty = make('p', 'No subagent records have been published for this task. Supporting task reports appear automatically.', 'sd-detail');
    const details = make('details', undefined, 'sd-history'); details.id = 'dashboard-history';
    const historyTitle = make('summary'), historyItems = make('div');
    const noHistory = make('p', 'Completed tasks you remove will appear here.', 'sd-detail');
    details.append(historyTitle, make('p', 'Completed work keeps its report and task reference. Restoring a row does not restart its agent.', 'sd-detail'), noHistory, historyItems);
    const message = make('p', undefined, 'sd-notice'); message.setAttribute('role','status');
    message.setAttribute('aria-live','polite');
    host.append(title, updated, table, caption, empty);
    if (!live) host.append(make('p', 'Saved snapshot. Open the main task to change dashboard history.', 'sd-detail'));
    host.append(details, message);
    ui = {stamp, body, empty, historyTitle, noHistory, historyItems};
  }
  function render() {
    if (!state) return;
    // Legacy publishers can keep sending their table; it supplies this dashboard.
    for (const figure of root.querySelectorAll('figure.comparison')) {
      if (figure.querySelector('h2,h3,h4')?.textContent.trim().toLowerCase() === 'subagent dashboard')
        figure.hidden = true;
    }
    if (!host) mount();
    if (!host.isConnected) {
      const header = root.querySelector(':scope > header.task-overview');
      if (header) header.after(host); else root.prepend(host);
    }
    const next = JSON.stringify(state);
    if (next === signature) return;
    signature = next;
    time(state.updated_at, ui.stamp);
    ordered(ui.body, state.rows, rows, workRow, updateWork);
    ordered(ui.historyItems, state.history, archived, historyRow, updateHistory);
    text(ui.historyTitle, `Dashboard history (${state.history.length})`);
    ui.noHistory.hidden = state.history.length > 0;
    ui.empty.hidden = state.rows.some(row => row.agent_id || row.source === 'saved table' || row.source === 'saved subagent report');
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
