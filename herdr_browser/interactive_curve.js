/* Portable measured-curve renderer. No external libraries or inferred timestamps. */
(() => {
  'use strict';
  const states = new Map(), ns = 'http://www.w3.org/2000/svg';
  const style = document.createElement('style');
  style.textContent = `
  .bar-value{min-width:0;overflow-wrap:anywhere;white-space:normal}
  .ic-controls{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;margin:12px 0;font-size:16px}
  .ic-controls select,.ic-controls input,.ic-controls button{font:inherit;padding:5px 8px;border:1px solid #aebdcc;border-radius:5px;background:white;color:#233247}
  .ic-controls label{max-width:100%;min-width:0}.ic-controls select{max-width:100%}.ic-controls input{width:108px}.ic-controls button{cursor:pointer}
  .ic-controls input[type=checkbox]{width:auto;accent-color:#2263bd}
  .ic-controls .ic-join-label{display:inline-flex;gap:8px;align-items:center}
  .ic-run-row{display:inline-flex;align-items:center;flex:1 1 340px;max-width:100%}
  .ic-run-visible{flex:none;width:18px;height:18px;accent-color:#2263bd}
  .ic-run-summary{flex-basis:100%;display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:16px}
  .ic-run-summary button{font:inherit;border:1px solid #aebdcc;border-radius:5px;background:white;padding:4px 9px;cursor:pointer}
  .ic-run-row[data-hidden=true] .ic-legend-button{text-decoration:line-through;opacity:.45}
  .ic-range-controls{font-size:15px;margin:8px 0}.ic-range-controls summary{cursor:pointer}
  .ic-zoom-selection{fill:#2263bd20;stroke:#2263bd;stroke-width:1.5;stroke-dasharray:4 3;pointer-events:none}
  .learning-curve:fullscreen{box-sizing:border-box;background:#f7f9fc;padding:20px;overflow:auto;width:100%}
  .ic-stage{position:relative}.ic-stage svg{width:100%;display:block;background:#fff;border:1px solid #dae1e8;border-radius:6px;touch-action:pan-y}
  .ic-tooltip{position:absolute;z-index:2;pointer-events:none;background:#172c42;color:white;padding:10px 13px;border-radius:6px;font:16px/1.5 system-ui;max-width:min(370px,94%);white-space:pre-line}
  .ic-readout{font-size:16px;line-height:1.5;min-height:46px;color:#46596d;font-variant-numeric:tabular-nums}
  .ic-holder .ic-readout{height:72px;min-height:72px;overflow:auto}
  .ic-note{font-size:16px;color:#596879;margin:6px 0}
  .curve-legend .ic-legend-button{display:inline-flex;align-items:center;gap:7px;font:inherit;padding:5px 8px;border:1px solid transparent;border-radius:5px;background:transparent;color:inherit;cursor:pointer;transition:opacity 100ms}
  .curve-legend .ic-legend-button[data-active="true"]{font-weight:650;border-color:currentColor;background:#f2f6fa}
  .curve-legend .ic-legend-button i{display:inline-block;width:18px;height:3px;flex:none}
  .ic-series{transition:opacity 100ms}
  .ic-controls :focus-visible,.curve-legend .ic-legend-button:focus-visible,.ic-stage svg:focus-visible{outline:3px solid #397fb7;outline-offset:2px}
  @media(max-width:600px){.ic-controls{gap:8px 12px}.ic-controls input{width:90px}.ic-holder .ic-readout{height:96px;min-height:96px}}`;
  document.head.append(style);
  const svgEl = (tag, attrs, text) => {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const format = x => Math.abs(x) >= 1000 ? (x / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
    : Number(x.toPrecision(4)).toLocaleString();
  const known = x => typeof x === 'number' && Number.isFinite(x);
  function savedState(key, axes) {
    let state=states.get(key);
    if(!state){
      try{state=JSON.parse(localStorage.getItem('interactive-curve:'+key));}catch(_){}
      state ||= {axis:axes[0].key,min:null,max:null};
      states.set(key,state);
    }
    state.hidden ||= [];state.yRanges ||= {};
    return state;
  }
  function joinedSeries(series, join) {
    const colors=new Map(series.map(s=>[s.label,s.color]));
    const raw=series.map(s=>({...s,color:colors.get(s.resume_of)||s.color,
      points:s.points.map(p=>({...p,process_elapsed_seconds:p.elapsed_seconds,
        resumed:!!s.resume_of,source_run:s.label}))}));
    if(!join)return raw;
    const joined=raw.filter(s=>!s.resume_of);
    for(const resumed of raw.filter(s=>s.resume_of)){
      const parent=joined.find(s=>s.label===resumed.resume_of);
      if(!parent){joined.push(resumed);continue;}
      parent.points=parent.points.filter(p=>p.x<=resumed.resume_from).concat(resumed.points.map(p=>({
        ...p,elapsed_seconds:known(p.elapsed_seconds)&&known(resumed.elapsed_offset_seconds)
          ? p.elapsed_seconds+resumed.elapsed_offset_seconds : undefined
      })));
      parent.hasResume=true;parent.runtimeOffset=known(resumed.elapsed_offset_seconds);
    }
    return joined;
  }
  window.InteractiveCurve = {
    mount(holder, chart, key) {
      // A different metric may receive samples while the selected metric is
      // unchanged. Keep its plot, focus and inspection state intact.
      const source = JSON.stringify([key, chart]);
      if (holder._curveSignature === source) return;
      if (holder._updateCurve?.(chart, key)) {
        holder._curveSignature = source;
        return;
      }
      holder._curveSignature = source;
      holder._interactiveCleanup?.();
      holder.querySelectorAll('.ic-metric-chart').forEach(inner => inner._interactiveCleanup?.());
      holder._updateCurve = null;
      // Preserve saved selections when the report adds the second state student.
      if(chart.title==='Full training loss · state students')try{
        const destination='interactive-curve:'+key, previous='interactive-curve:'+key.replace('Full training loss · state students','Full training loss · teacher-identical student');
        if(!localStorage.getItem(destination)&&localStorage.getItem(previous))localStorage.setItem(destination,localStorage.getItem(previous));
      }catch(_){}
      if(chart.follows_table){
        const figure=holder.closest('figure');
        const table=[...figure.parentElement.querySelectorAll(':scope > figure.comparison')].find(e=>e.querySelector('h2,h3,h4')?.textContent===chart.follows_table);
        if(table&&table.parentElement===figure.parentElement)figure.parentElement.insertBefore(table,figure);
      }
      if (chart.metrics?.length) {
        let selected;
        try { selected=localStorage.getItem('interactive-curve-metric:'+key); } catch (_) {}
        if (!chart.metrics.some(m=>m.key===selected)) selected=chart.metrics[0].key;
        holder.replaceChildren();
        const controls=document.createElement('div'); controls.className='ic-controls ic-metric-controls';
        const label=document.createElement('label'); label.textContent='Metric ';
        const select=document.createElement('select'); select.className='ic-metric';
        for(const metric of chart.metrics){
          const option=document.createElement('option'); option.value=metric.key; option.textContent=metric.label;
          select.append(option);
        }
        select.value=selected; label.append(select); controls.append(label);
        const hasResume=chart.metrics.some(m=>m.series.some(s=>s.resume_of));
        let join=true;
        try{join=localStorage.getItem('interactive-curve-join:'+key)!=='false';}catch(_){}
        let joinInput;
        if(hasResume){
          const joinLabel=document.createElement('label');joinLabel.className='ic-join-label';
          joinInput=document.createElement('input');joinInput.type='checkbox';joinInput.className='ic-join-resumes';joinInput.checked=join;
          joinLabel.append(joinInput,document.createTextNode('Join resumed runs'));controls.append(joinLabel);
          joinLabel.title='Join at the actual resume checkpoint. Runtime sums recorded runtimes and excludes the outage. Uncheck to show each process separately.';
        }
        const inner=document.createElement('div'); inner.className='ic-metric-chart';
        holder.append(controls,inner);
        const render=()=>{
          const metric=chart.metrics.find(m=>m.key===select.value);
          const colors=new Map(chart.series.map(s=>[s.label,s.color]));
          const active={...chart,...metric,metrics:undefined,
            metricKey:metric.key,joined:join,hasResume,
            series:joinedSeries(metric.series.map(s=>({...s,color:colors.get(s.label)})),join)};
          window.InteractiveCurve.mount(inner,active,key);
        };
        select.addEventListener('change',()=>{
          try { localStorage.setItem('interactive-curve-metric:'+key,select.value); } catch (_) {}
          render();
        });
        joinInput?.addEventListener('change',()=>{
          join=joinInput.checked;
          try{localStorage.setItem('interactive-curve-join:'+key,String(join));}catch(_){}
          const state=savedState(key,chart.axes||[{key:'x'}]);
          if(state.axis==='elapsed_seconds'){
            state.min=state.max=null;
            try{localStorage.setItem('interactive-curve:'+key,JSON.stringify(state));}catch(_){}
          }
          render();
        });
        const metricSchema = value => JSON.stringify([value.follows_table, value.metrics?.map(m =>
          [m.key, m.label, m.series.some(s => s.resume_of)])]);
        holder._updateCurve = (next, nextKey) => {
          if (nextKey !== key || metricSchema(next) !== metricSchema(chart)) return false;
          chart = next; render(); return true;
        };
        render();
        return;
      }
      const axes = chart.axes || [{key:'x', label:chart.x_label || 'Optimizer updates', divisor:1}];
      const state=savedState(key,axes), metricKey=chart.metricKey||chart.y_label||'value';
      if (!axes.some(a => a.key === state.axis)) state.axis = axes[0].key;
      if (!['linear','log'].includes(state.yScale)) state.yScale = chart.log ? 'log' : 'linear';
      const remember = () => {
        try { localStorage.setItem('interactive-curve:' + key, JSON.stringify(state)); } catch (_) {}
      };
      holder.replaceChildren();
      const controls = document.createElement('div'); controls.className = 'ic-controls';
      const axisLabel = document.createElement('label'); axisLabel.textContent = 'X axis ';
      const select = document.createElement('select'); select.className = 'ic-axis';
      axes.forEach(a => {const option = document.createElement('option'); option.value = a.key; option.textContent = a.label; select.append(option);});
      select.value = state.axis; axisLabel.append(select); controls.append(axisLabel);
      const scaleLabel = document.createElement('label'); scaleLabel.textContent = 'Y scale ';
      const scaleSelect = document.createElement('select'); scaleSelect.className = 'ic-y-scale';
      for (const [value,label] of [['linear','Linear'],['log','Log (base 10)']]) {
        const option = document.createElement('option'); option.value = value; option.textContent = label; scaleSelect.append(option);
      }
      scaleSelect.value = state.yScale; scaleLabel.append(scaleSelect); controls.append(scaleLabel);
      const ranges=document.createElement('details');ranges.className='ic-range-controls';
      const rangeTitle=document.createElement('summary');rangeTitle.textContent='Exact axis ranges';
      const rangeFields=document.createElement('div');rangeFields.className='ic-controls';ranges.append(rangeTitle,rangeFields);
      const inputs = ['X from ', 'X to '].map((label, i) => {
        const wrap = document.createElement('label'); wrap.textContent = label;
        const input = document.createElement('input'); input.type = 'number'; input.step = 'any'; input.min = '0';
        input.className = i ? 'ic-max' : 'ic-min'; wrap.append(input); rangeFields.append(wrap); return input;
      });
      const yInputs=['Y from ','Y to '].map((label,i)=>{
        const wrap=document.createElement('label');wrap.textContent=label;
        const input=document.createElement('input');input.type='number';input.step='any';input.min='0';
        input.className=i?'ic-y-max':'ic-y-min';wrap.append(input);rangeFields.append(wrap);return input;
      });
      const modeLabel=document.createElement('label');modeLabel.textContent='Drag ';
      const mode=document.createElement('select');mode.className='ic-drag-mode';
      for(const [value,text] of [['zoom','Zoom'],['pan','Pan']]){
        const option=document.createElement('option');option.value=value;option.textContent=text;mode.append(option);
      }
      mode.value=state.mode||'zoom';modeLabel.append(mode);controls.append(modeLabel);
      const toolButton=(text,className)=>{
        const button=document.createElement('button');button.type='button';button.className=className;button.textContent=text;controls.append(button);return button;
      };
      const zoomIn=toolButton('Zoom +','ic-zoom-in'),zoomOut=toolButton('Zoom −','ic-zoom-out');
      const reset=toolButton('Reset zoom','ic-reset'),expand=toolButton('Expand','ic-expand');
      const stage = document.createElement('div'); stage.className = 'ic-stage';
      const tooltip = document.createElement('div'); tooltip.className = 'ic-tooltip'; tooltip.hidden = true;
      const readout = document.createElement('div'); readout.className = 'ic-readout'; readout.setAttribute('aria-live','polite');
      const note = document.createElement('p'); note.className = 'ic-note';
      holder.append(controls,ranges,stage,readout,note);
      let redrawTimer, lastWidth=null, hovered=null, focused=null, updateHighlight=()=>{};
      if (!chart.series.some(s=>s.label===state.pinned)) state.pinned=null;
      const isHidden=s=>state.hidden.includes(s.label);
      const activeLabel=()=>[hovered,focused,state.pinned].find(label=>label&&!state.hidden.includes(label));
      let externalHighlights=[];
      const activeLabels=()=>externalHighlights.length?externalHighlights:(activeLabel()?[activeLabel()]:[]);
      holder.setHighlightedSeries=labels=>{
        externalHighlights=labels.filter(label=>chart.series.some(s=>s.label===label));
        updateHighlight();
      };
      const legend=holder.closest('figure')?.querySelector('.curve-legend');
      const legendButtons=new Map();
      function clearHighlight(){hovered=focused=state.pinned=null;remember();updateHighlight();}
      if(legend){
        legend.replaceChildren();
        const summary=document.createElement('div');summary.className='ic-run-summary';
        const count=document.createElement('span');count.className='ic-visible-count';summary.append(count);
        for(const [text,hide] of [['Show all',false],['Hide all',true]]){
          const button=document.createElement('button');button.type='button';button.textContent=text;button.className=hide?'ic-hide-all':'ic-show-all';
          button.addEventListener('click',()=>{
            const labels=chart.series.map(s=>s.label);
            state.hidden=state.hidden.filter(label=>!labels.includes(label)).concat(hide?labels:[]);
            remember();draw();
          });summary.append(button);
        }
        legend.append(summary);
        for(const s of chart.series){
          const row=document.createElement('div');row.className='ic-run-row';
          const visible=document.createElement('input');visible.type='checkbox';visible.className='ic-run-visible';visible.checked=!isHidden(s);
          visible.dataset.series=s.label;visible.setAttribute('aria-label','Show '+s.label);
          visible.addEventListener('change',()=>{
            state.hidden=state.hidden.filter(label=>label!==s.label);
            if(!visible.checked)state.hidden.push(s.label);
            row.dataset.hidden=String(!visible.checked);remember();draw();
          });
          const button=document.createElement('button');button.type='button';button.className='ic-legend-button';
          button.dataset.series=s.label;button.setAttribute('aria-pressed',String(state.pinned===s.label));
          button.title='Hover or focus to highlight. Click or press Enter to pin; Escape clears the selection.';
          const swatch=document.createElement('i');swatch.style.background=s.color||'#2263bd';swatch.setAttribute('aria-hidden','true');
          button.append(swatch,document.createTextNode(s.label+(s.points.length?'':' · no measurement')));
          button.addEventListener('pointerenter',()=>{hovered=s.label;updateHighlight();});
          button.addEventListener('pointerleave',()=>{hovered=null;updateHighlight();});
          button.addEventListener('focus',()=>{if(button.matches(':focus-visible')){focused=s.label;updateHighlight();}});
          button.addEventListener('blur',()=>{focused=null;updateHighlight();});
          button.addEventListener('click',()=>{state.pinned=state.pinned===s.label?null:s.label;remember();updateHighlight();});
          button.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();clearHighlight();}});
          row.append(visible,button);legend.append(row);legendButtons.set(s.label,button);
        }
        holder.closest('figure').insertBefore(legend,holder.closest('figure').querySelector('figcaption'));
      }
      holder.fitHighlightedSeries=()=>{
        const axis=axes.find(a=>a.key===state.axis), divisor=axis.divisor||1;
        const points=chart.series.filter(s=>externalHighlights.includes(s.label)&&!s.reference&&!isHidden(s)).flatMap(s=>s.points);
        const values=points.map(p=>p[axis.key]).filter(known).map(v=>v/divisor);
        if(!values.length)return false;
        state.min=0;state.max=Math.max(1e-6,...values)*1.04;
        remember();draw();return true;
      };
      let viewport, changeZoom;
      function draw() {
        const axis = axes.find(a => a.key === state.axis), divisor = axis.divisor || 1;
        const value = p => known(p[axis.key]) ? p[axis.key] / divisor : null;
        const shown=chart.series.filter(s=>!isHidden(s));
        const all = shown.filter(s=>!s.reference).flatMap(s => s.points.map(p => ({s,p,x:value(p)})));
        const available = all.filter(t => t.x !== null);
        const missing = all.length - available.length;
        const clockResets = axis.key==='elapsed_seconds' ? shown.reduce((total,s)=>{
          const times=s.points.map(value).filter(t=>t!==null);
          return total+times.slice(1).filter((t,i)=>t<times[i]).length;
        },0) : 0;
        const observed = Math.max(1e-6,...available.map(t => t.x));
        const lower = state.min ?? 0, upper = state.max ?? observed;
        if (!(known(lower) && known(upper) && lower >= 0 && upper > lower)) return;
        for(const [i,v] of [lower,upper].entries())if(document.activeElement!==inputs[i])inputs[i].value=Number(v.toPrecision(7));
        const inRange = available.filter(t => t.x >= lower && t.x <= upper);
        const log = state.yScale === 'log';
        const plottable = p => known(p.y) && (!log || p.y > 0);
        const visible = inRange.filter(t => plottable(t.p));
        const excluded = log ? inRange.filter(t => known(t.p.y) && t.p.y <= 0).length : 0;
        const excludedIntervals = log ? visible.filter(t => known(t.p.lo) && known(t.p.hi) && (t.p.lo <= 0 || t.p.hi <= 0)).length : 0;
        const width = Math.max(290, holder.getBoundingClientRect().width);
        lastWidth=holder.getBoundingClientRect().width;
        const left=61, right=18, bottom=57, pw=width-left-right;
        const yLabel=(chart.y_label||'Value')+(log?' · log scale':'');
        const labelLines=[''], labelLimit=Math.max(20,Math.floor(pw/8.5));
        for(const word of yLabel.split(' ')){
          const last=labelLines.length-1;
          if(labelLines[last] && labelLines[last].length+word.length+1>labelLimit)labelLines.push(word);
          else labelLines[last]+=(labelLines[last]?' ':'')+word;
        }
        const extraLabelHeight=(labelLines.length-1)*20;
        const height=Math.max(330+extraLabelHeight,document.fullscreenElement===holder.closest('figure')?innerHeight*.55:0);
        const top=32+extraLabelHeight, ph=height-top-bottom;
        const positiveValues = visible.flatMap(t=>[t.p.y,t.p.lo,t.p.hi]).filter(y=>known(y)&&y>0);
        const autoMin = log ? (positiveValues.length ? Math.min(...positiveValues)*.85 : .001) : 0;
        const automaticMax = Math.max(1e-8,...visible.flatMap(t=>[t.p.y,t.p.hi]).filter(known))*1.08;
        const requestedMax = known(chart.y_maximum) && chart.y_maximum > 0 ? chart.y_maximum : automaticMax;
        const range=state.yRanges[metricKey];
        const ymin=range&&(!log||range.min>0)?range.min:autoMin;
        const ymax=range&&range.max>ymin?range.max:(log?Math.max(requestedMax,ymin*10):requestedMax);
        yInputs.forEach((input,i)=>{if(document.activeElement!==input)input.value=Number([ymin,ymax][i].toPrecision(7));});
        const X = x => left+(x-lower)/(upper-lower)*pw;
        const Y = y => top+(1-(log?(Math.log10(y)-Math.log10(ymin))/(Math.log10(ymax)-Math.log10(ymin)):(y-ymin)/(ymax-ymin)))*ph;
        const invX=px=>lower+(px-left)/pw*(upper-lower);
        const invY=py=>log?10**(Math.log10(ymin)+(1-(py-top)/ph)*(Math.log10(ymax)-Math.log10(ymin))):ymin+(1-(py-top)/ph)*(ymax-ymin);
        viewport={lower,upper,ymin,ymax};
        const setBounds=(a,b,c,d)=>{
          if(!(b>a&&d>c)||!Number.isFinite(a+b+c+d))return;
          state.min=Math.max(0,a);state.max=state.min+(b-a);
          state.yRanges[metricKey]={min:Math.max(log?Number.MIN_VALUE:0,c),max:Math.max(log?Number.MIN_VALUE:0,c)+(d-c)};
          remember();draw();
        };
        changeZoom=(factor,cx=(lower+upper)/2,cy=log?Math.sqrt(ymin*ymax):(ymin+ymax)/2)=>{
          const a=cx+(lower-cx)*factor,b=cx+(upper-cx)*factor;
          if(log){const center=Math.log10(cy);setBounds(a,b,10**(center+(Math.log10(ymin)-center)*factor),10**(center+(Math.log10(ymax)-center)*factor));}
          else setBounds(a,b,cy+(ymin-cy)*factor,cy+(ymax-cy)*factor);
        };
        const svg = svgEl('svg',{viewBox:`0 0 ${width} ${height}`,height,tabindex:0,role:'img','aria-label':`${chart.title} versus ${axis.label}; ${log?'logarithmic base 10':'linear'} y scale. Arrow keys inspect recorded values.`});
        svg.style.touchAction='none';svg.style.cursor=mode.value==='pan'?'grab':'crosshair';
        svg.dataset.xMin=lower;svg.dataset.xMax=upper;svg.dataset.yMin=ymin;svg.dataset.yMax=ymax;
        svg.dataset.plotLeft=left;svg.dataset.plotTop=top;svg.dataset.plotWidth=pw;svg.dataset.plotHeight=ph;
        svg.append(svgEl('title',{},chart.title));
        const tickCount=width<450?2:4;
        for(let i=0;i<=tickCount;i++){
          const y = log ? 10**(Math.log10(ymin)+(Math.log10(ymax)-Math.log10(ymin))*i/tickCount) : ymin+(ymax-ymin)*i/tickCount;
          const x = lower+(upper-lower)*i/tickCount;
          svg.append(svgEl('line',{x1:left,x2:width-right,y1:Y(y),y2:Y(y),stroke:'#e1e7ed'}),
            svgEl('text',{x:left-8,y:Y(y)+5,'text-anchor':'end','font-size':16,fill:'#46596d'},format(y)),
            svgEl('text',{x:X(x),y:height-32,'text-anchor':i===0?'start':i===tickCount?'end':'middle','font-size':16,fill:'#46596d'},format(x)));
        }
        const yTitle=svgEl('text',{x:left,y:22,'font-size':16,fill:'#43556a'});
        labelLines.forEach((line,i)=>yTitle.append(svgEl('tspan',{x:left,y:22+i*20},(i?' ':'')+line)));
        svg.append(yTitle,svgEl('text',{x:left+pw/2,y:height-8,'text-anchor':'middle','font-size':16,fill:'#43556a'},axis.label));
        const plotted = svgEl('g',{}), clip = `ic-clip-${Math.random().toString(36).slice(2)}`;
        const defs=svgEl('defs',{}), cp=svgEl('clipPath',{id:clip});
        cp.append(svgEl('rect',{x:left,y:top,width:pw,height:ph})); defs.append(cp); svg.append(defs);
        plotted.setAttribute('clip-path',`url(#${clip})`); svg.append(plotted);
        const phases=(chart.phases||[]).filter(p=>known(p[axis.key]));
        for(const phase of phases){
          if(!known(phase[axis.key]))continue;
          const x=phase[axis.key]/divisor;if(x<lower||x>upper)continue;
          const mark=svgEl('line',{x1:X(x),x2:X(x),y1:top,y2:height-bottom,stroke:'#9b8766','stroke-dasharray':'3 4'});
          mark.append(svgEl('title',{},phase.label));plotted.append(mark);
        }
        const seriesGroups=new Map();
        for(const s of shown){
          const group=svgEl('g',{class:'ic-series','data-series':s.label});plotted.append(group);seriesGroups.set(s.label,group);
          if(s.reference){
            const p=s.points[0];
            if(plottable(p)){
              group.dataset.reference='true';
              const line=svgEl('line',{x1:left,x2:width-right,y1:Y(p.y),y2:Y(p.y),stroke:s.color||'#9a4eb3','stroke-width':2,'stroke-dasharray':'7 5'});
              line.append(svgEl('title',{},`${s.label}: ${p.y} ${chart.y_label}; single measured reference, held constant.`));
              group.append(line,svgEl('text',{x:left+8,y:Y(p.y)-8,fill:s.color||'#9a4eb3','font-size':14},`Teacher reference: ${p.y}%`));
            }
            continue;
          }
          const points=s.points.filter(p=>value(p)!==null), color=s.color||'#2263bd';
          let segment=[], segments=[];
          for(const p of points){
            if (!plottable(p)) { if(segment.length)segments.push(segment); segment=[]; continue; }
            const previous=segment.at(-1);
            if(previous && ((chart.gap && p.x-previous.x>chart.gap) || value(p)<=value(previous))){
              segments.push(segment); segment=[];
            }
            segment.push(p);
          }
          if(segment.length)segments.push(segment);
          if(!s.independent)for(const part of segments)group.append(svgEl('polyline',{
            points:part.map(p=>`${X(value(p))},${Y(p.y)}`).join(' '),fill:'none',stroke:color,'stroke-width':1.6,
            'stroke-dasharray':s.resume_of?'6 4':'none'}));
          for(const p of points.filter(p=>plottable(p)&&value(p)>=lower&&value(p)<=upper)){
            const x=X(value(p)),y=Y(p.y);
            if(known(p.lo)&&known(p.hi)&&(!log||(p.lo>0&&p.hi>0)))group.append(svgEl('line',{x1:x,x2:x,y1:Y(p.lo),y2:Y(p.hi),stroke:color}));
            if(s.independent)group.append(svgEl('polygon',{points:`${x},${y-6} ${x+6},${y} ${x},${y+6} ${x-6},${y}`,fill:'white',stroke:color,'stroke-width':2}));
            else if(chart.markers)group.append(svgEl('circle',{cx:x,cy:y,r:4,fill:color}));
          }
        }
        const cross=svgEl('line',{y1:top,y2:height-bottom,stroke:'#576879','stroke-dasharray':'3 3',visibility:'hidden'});
        const dot=svgEl('circle',{r:5,fill:'white',stroke:'#172c42','stroke-width':2,visibility:'hidden'});
        const selection=svgEl('rect',{class:'ic-zoom-selection',visibility:'hidden'});
        svg.append(cross,dot,selection);
        if(!visible.length)svg.append(svgEl('text',{x:left+pw/2,y:165,'text-anchor':'middle','font-size':17},log && excluded ? 'No positive measurements in this range' : 'No recorded measurements in this range'));
        stage.replaceChildren(svg,tooltip); tooltip.hidden=true;
        const inspectHint='Drag to zoom. Check runs below to show or hide them. Hover, tap or arrow keys inspect exact values.';
        readout.textContent=inspectHint;
        note.textContent=(excluded?`${excluded} non-positive value(s) cannot be shown on a log scale; select Linear to inspect them. `:'')+
          (excludedIntervals?`${excludedIntervals} uncertainty interval(s) include non-positive bounds; their bars are omitted on the log scale. `:'')+
          (missing?`${missing} point(s) lack ${axis.label}; omitted on this axis. `:'')+
          (axis.key==='elapsed_seconds'?'Recorded elapsed time includes setup and runtime overhead; independent evaluations use matching checkpoint-round time. ':'')+
          (clockResets?`${clockResets} elapsed-clock reset(s); segments use their recorded process clocks. `:'')+
          (chart.hasResume?(chart.joined?'Resumes join at their checkpoint; runtime sums recorded runtimes and excludes downtime. ':'Resumed processes are separate dashed curves: updates/rounds keep their original coordinates; runtime starts at process start. '):'')+
          (phases.length?'Dashed lines: '+phases.map(p=>p.label).join(', ')+'. ':'')+
          'Lines connect measured points only. Ctrl + wheel zooms; Shift + wheel pans. Double-click or Reset zoom restores the view.';
        let index=0, drag=null;
        const inspectable=()=>{
          const labels=activeLabels();
          const references=shown.filter(s=>s.reference).map(s=>({s,p:s.points[0],x:(lower+upper)/2}));
          return [...visible,...references].filter(t=>plottable(t.p)&&t.p.y>=ymin&&t.p.y<=ymax&&(!labels.length||labels.includes(t.s.label)));
        };
        updateHighlight=()=>{
          const labels=activeLabels(), label=labels[0];index=0;
          for(const [name,group] of seriesGroups){
            group.setAttribute('opacity',labels.length&&!labels.includes(name)?'0.14':'1');
            group.querySelectorAll('polyline').forEach(line=>line.setAttribute('stroke-width',labels.includes(name)?'3.2':'1.6'));
          }
          if(label&&seriesGroups.has(label))plotted.append(seriesGroups.get(label));
          for(const [name,button] of legendButtons){
            button.dataset.active=String(labels.includes(name));button.style.opacity=labels.length&&!labels.includes(name)?'0.5':'1';
            button.setAttribute('aria-pressed',String(state.pinned===name));
            const row=button.closest('.ic-run-row');row.dataset.hidden=String(state.hidden.includes(name));
            row.querySelector('input').checked=!state.hidden.includes(name);
          }
          if(legend)legend.querySelector('.ic-visible-count').textContent=`Runs · ${shown.length}/${chart.series.length} visible`;
          tooltip.hidden=true;cross.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');
          readout.textContent=labels.length?labels.join(' + ')+' · highlighted'+(inspectable().length?' · Inspect the plot for exact recorded values.':' · No recorded measurements on this axis in this range.') : inspectHint;
        };
        updateHighlight();
        const details = t => {
          const p=t.p;
          if(t.s.reference)return `${t.s.label} · constant reference\n${p.y} ${chart.y_label}\n${p.successes}/${p.episodes} successes · 95% Wilson ${p.lo.toFixed(2)}–${p.hi.toFixed(2)}%\nOne initial evaluation at update ${p.x}; ${known(p.elapsed_seconds)?(p.elapsed_seconds/3600).toPrecision(7)+' recorded elapsed hours':'elapsed time unavailable'}. This is not a repeated teacher evaluation.`;
          const experiment=t.s.label.match(/^E\d+/)?.[0];
          const reference=chart.series.find(s=>s.reference&&(!experiment||s.label.match(/^E\d+/)?.[0]===experiment));
          const gap=reference?`\nStudent − teacher: ${(p.y-reference.points[0].y).toFixed(2)} percentage points`:'';
          return `${t.s.label}${t.s.independent?' · independent evaluation':''}\n${p.y} ${chart.y_label||''}`+
            (known(p.round)?` · round ${p.round}`:'')+`\n${p.x.toLocaleString()} optimizer updates`+
            (known(p.elapsed_seconds)?` · ${(p.elapsed_seconds/3600).toPrecision(7)} elapsed hours`:' · elapsed time unavailable')+
            (p.resumed?`\nResumed run · ${p.source_run}\n${(p.process_elapsed_seconds/3600).toPrecision(7)} process elapsed hours`:'')+
            (p.episodes?`\n${p.successes}/${p.episodes} successes · 95% Wilson ${p.lo.toFixed(2)}–${p.hi.toFixed(2)}%`:'')+gap;
        };
        function inspect(t,mx,my){
          if(!t)return;
          cross.setAttribute('x1',X(t.x));cross.setAttribute('x2',X(t.x));cross.setAttribute('visibility','visible');
          dot.setAttribute('cx',X(t.x));dot.setAttribute('cy',Y(t.p.y));dot.setAttribute('visibility','visible');
          readout.textContent=details(t).replace(/\n/g,' · ');
          tooltip.textContent=details(t);tooltip.hidden=false;
          const rect=stage.getBoundingClientRect(), tw=tooltip.offsetWidth;
          tooltip.style.left=Math.max(5,Math.min(rect.width-tw-5,mx+14))+'px';
          tooltip.style.top=Math.max(5,Math.min(height-tooltip.offsetHeight-5,my+12))+'px';
        }
        function nearest(event){
          const rect=svg.getBoundingClientRect(),mx=(event.clientX-rect.left)*width/rect.width,my=(event.clientY-rect.top)*height/rect.height;
          let best=null, distance=Infinity;
          for(const sample of inspectable()){const t=sample.s.reference?{...sample,x:invX(Math.max(left,Math.min(width-right,mx)))}:sample;const d=Math.hypot(X(t.x)-mx,Y(t.p.y)-my);if(d<distance){distance=d;best=t;}}
          inspect(best,mx*rect.width/width,my*rect.height/height);
        }
        const position=e=>{
          const rect=svg.getBoundingClientRect();
          return {x:Math.max(left,Math.min(width-right,(e.clientX-rect.left)*width/rect.width)),
            y:Math.max(top,Math.min(height-bottom,(e.clientY-rect.top)*height/rect.height))};
        };
        svg.addEventListener('pointermove',e=>{
          if(!drag){nearest(e);return;}
          const end=position(e);tooltip.hidden=true;
          selection.setAttribute('x',Math.min(drag.x,end.x));selection.setAttribute('y',Math.min(drag.y,end.y));
          selection.setAttribute('width',Math.abs(end.x-drag.x));selection.setAttribute('height',Math.abs(end.y-drag.y));
          selection.setAttribute('visibility',mode.value==='zoom'?'visible':'hidden');
        });
        svg.addEventListener('pointerleave',()=>{tooltip.hidden=true;cross.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');});
        svg.addEventListener('pointerdown',e=>{
          nearest(e);
          if(e.button===0){drag=position(e);svg.setPointerCapture(e.pointerId);}
        });
        svg.addEventListener('pointerup',e=>{
          if(drag===null)return;
          const start=drag,end=position(e);drag=null;selection.setAttribute('visibility','hidden');
          if(Math.hypot(end.x-start.x,end.y-start.y)<12)return;
          if(mode.value==='pan'){
            const dx=invX(start.x)-invX(end.x);
            if(log){const ratio=invY(start.y)/invY(end.y);setBounds(lower+dx,upper+dx,ymin*ratio,ymax*ratio);}
            else {const dy=invY(start.y)-invY(end.y);setBounds(lower+dx,upper+dx,ymin+dy,ymax+dy);}
          }else{
            const a=Math.abs(end.x-start.x)>=12?Math.min(invX(start.x),invX(end.x)):lower;
            const b=Math.abs(end.x-start.x)>=12?Math.max(invX(start.x),invX(end.x)):upper;
            const c=Math.abs(end.y-start.y)>=12?Math.min(invY(start.y),invY(end.y)):ymin;
            const d=Math.abs(end.y-start.y)>=12?Math.max(invY(start.y),invY(end.y)):ymax;
            setBounds(a,b,c,d);
          }
        });
        svg.addEventListener('pointercancel',()=>{drag=null;selection.setAttribute('visibility','hidden');});
        svg.addEventListener('dblclick',()=>reset.click());
        svg.addEventListener('keydown',e=>{
          if(e.key==='Escape'){e.preventDefault();clearHighlight();return;}
          const candidates=inspectable();
          if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)||!candidates.length)return;
          e.preventDefault();index=e.key==='Home'?0:e.key==='End'?candidates.length-1:Math.max(0,Math.min(candidates.length-1,index+(e.key==='ArrowRight'?1:-1)));
          inspect(candidates[index],X(candidates[index].x),Y(candidates[index].p.y));
        });
        svg.addEventListener('wheel',e=>{
          if(!e.ctrlKey&&!e.shiftKey)return;e.preventDefault();
          const span=upper-lower;
          if(e.shiftKey){const delta=Math.sign(e.deltaY||e.deltaX)*span*.1;state.min=Math.max(0,lower+delta);state.max=state.min+span;}
          else {const p=position(e);changeZoom(e.deltaY>0?1.2:.8,invX(p.x),invY(p.y));return;}
          remember();draw();
        },{passive:false});
        holder.dispatchEvent(new CustomEvent('ic:mounted',{bubbles:true}));
      }
      select.addEventListener('change',()=>{state.axis=select.value;state.min=state.max=null;remember();draw();});
      scaleSelect.addEventListener('change',()=>{state.yScale=scaleSelect.value;remember();draw();});
      mode.addEventListener('change',()=>{state.mode=mode.value;remember();draw();});
      inputs.forEach(input=>input.addEventListener('change',()=>{
        const min=Number(inputs[0].value),max=Number(inputs[1].value);
        if(Number.isFinite(min)&&Number.isFinite(max)&&min>=0&&max>min){state.min=min;state.max=max;remember();draw();}
        else {input.setCustomValidity('Use a nonnegative range with To greater than From.');input.reportValidity();return;}
        inputs.forEach(i=>i.setCustomValidity(''));
      }));
      yInputs.forEach(input=>input.addEventListener('change',()=>{
        const min=Number(yInputs[0].value),max=Number(yInputs[1].value);
        if(Number.isFinite(min)&&Number.isFinite(max)&&min>=0&&max>min&&(state.yScale!=='log'||min>0)){
          state.yRanges[metricKey]={min,max};remember();draw();yInputs.forEach(i=>i.setCustomValidity(''));
        }else {input.setCustomValidity('Use increasing nonnegative bounds; Log requires positive bounds.');input.reportValidity();}
      }));
      zoomIn.addEventListener('click',()=>changeZoom(.7));zoomOut.addEventListener('click',()=>changeZoom(1/.7));
      reset.addEventListener('click',()=>{state.min=state.max=null;delete state.yRanges[metricKey];remember();draw();});
      expand.addEventListener('click',async()=>{
        const figure=holder.closest('figure');
        try{if(document.fullscreenElement)await document.exitFullscreen();else await figure.requestFullscreen();}catch(_){}
      });
      if(!document.fullscreenEnabled)expand.hidden=true;
      const resize = () => {
        clearTimeout(redrawTimer);
        redrawTimer=setTimeout(()=>{
          if(holder.isConnected&&Math.abs(holder.getBoundingClientRect().width-lastWidth)>.5)draw();
        },100);
      };
      const schema = value => JSON.stringify([
        value.axes || [{key:'x', label:value.x_label || 'Optimizer updates', divisor:1}],
        value.y_label, value.metricKey, value.log, value.follows_table,
        value.series.map(s => [s.label, s.color]), !!value.metrics?.length
      ]);
      holder._updateCurve = (next, nextKey) => {
        if (nextKey !== key || schema(next) !== schema(chart)) return false;
        chart = next;
        for (const series of chart.series) {
          const label = legendButtons.get(series.label)?.lastChild;
          if (label) label.nodeValue = series.label + (series.points.length ? '' : ' · no measurement');
        }
        draw(); return true;
      };
      const observer=new ResizeObserver(resize);observer.observe(holder);
      // Repeated live renders must release old observers.
      holder._interactiveCleanup?.();holder._interactiveCleanup=()=>observer.disconnect();
      draw();
    }
  };
  window.InteractiveDiagram = {
    mountAll(root=document) {
      root.querySelectorAll('figure table tbody tr').forEach(row=>{
        row.classList.add('node');
        const labels=[...row.closest('table').querySelectorAll('thead th')].map(cell=>cell.textContent.trim());
        row.dataset.detail=[...row.children].map((cell,i)=>(labels[i]||'Value')+': '+cell.textContent.trim()).join(' · ');
      });
      root.querySelectorAll('figure').forEach(figure=>{
        const nodes=[...figure.querySelectorAll('.node,.comparison-node,.flow-step,.flow-steps li,.clarity-flow li')];
        if(!nodes.length)return;
        figure.dataset.interactiveDiagram='true';
        let hint=figure.querySelector(':scope > .ic-readout');
        const caption=figure.querySelector('figcaption');
        if(!hint){
          hint=document.createElement('p');hint.className='ic-readout';
          hint.textContent='Hover, tap or focus a diagram step to inspect its details.';
          figure.insertBefore(hint,caption);
        }
        for(const node of nodes){
          node.tabIndex=0;node.title=node.dataset.detail||(node.innerText||node.textContent).trim().replace(/\s+/g,' ');
          if(node.dataset.diagramBound)continue;node.dataset.diagramBound='true';
          const show=()=>{
            nodes.forEach(n=>n.style.outline='');
            node.style.outline='2px solid #397fb7';
            hint.textContent=node.title+(caption&&!node.closest('table')?' · '+caption.textContent.trim():'');
          };
          node.addEventListener('pointerenter',show);node.addEventListener('pointerdown',show);node.addEventListener('focus',show);
        }
      });
    }
  };
})();
