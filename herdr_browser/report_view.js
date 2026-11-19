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
  const root = document.querySelector('#task-content');
  const live = root.dataset.live !== 'false';
  let sourceTree = snapshot(root);
  let etag = null, cursor = null, historyLoaded = false, busy = false, historyBusy = false;
  let legacyHistory = false;
  const escape = value => String(value).replace(/[&<>"']/g, x => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[x]));
  const inlineText = value => escape(value).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  const referenceIds = text => Array.from(text.matchAll(/\[([^\]\n]+)\]\(#([a-zA-Z0-9_-]{1,96})\)/g), m => m[2]);
  function scanLead(text) {
    if(text.includes('**'))return text;
    const prefix=text.split('[',1)[0],words=[...prefix.matchAll(/\S+/g)];
    if(!words.length)return text;
    const colon=prefix.indexOf(':');
    const end=colon>0 && colon<60 && prefix.slice(0,colon).trim().split(/\s+/).length<=7 ?
      colon+1 : words[Math.min(3,words.length-1)].index+words[Math.min(3,words.length-1)][0].length;
    return '**'+text.slice(0,end)+'**'+text.slice(end);
  }
  function paragraphs(text,lookup,compact=false,scan=true) {
    const inline = part => {
      let value = inlineText(part);
      if (lookup) value = value.replace(/\[([^\]\n]+)\]\(#([a-zA-Z0-9_-]{1,96})\)/g, (match,label,id) => {
        const context = lookup.get(id);
        return context ? '<a class="appendix-citation" href="#' + escape(id) + '">' + label +
          ' <span class="citation-number">(Appendix ' + context.number + ')</span></a>' : match;
      });
      return value.replace(/\n/g, '<br>');
    };
    const output=[],prose=[],items=[];
    const flushProse=()=>{
      if(!prose.length)return;
      const block=prose.join('\n');
      if(scan && (compact || block.trim().split(/\s+/).length+2*referenceIds(block).length>45)) {
        const parts=block.split(/(?:(?<=[.!?;])|(?<=[.!?;]\*\*))\s+(?=(?:\*\*)?[A-Z])/);
        output.push('<ul class="report-bullets">'+parts.map(part=>'<li>'+inline(scanLead(part))+'</li>').join('')+'</ul>');
      } else output.push('<p>'+inline(block)+'</p>');
      prose.length=0;
    };
    const flushItems=()=>{if(items.length){output.push('<ul class="report-bullets">'+items.map(item=>'<li>'+inline(item)+'</li>').join('')+'</ul>');items.length=0;}};
    for(const line of text.trim().split('\n')) {
      const match=line.match(/^[-*] +(.*)/);
      if(match){flushProse();items.push(match[1]);}
      else if(!line.trim()){flushProse();}
      else if(items.length && /^(  |\t)/.test(line)){items[items.length-1]+='\n'+line.trim();}
      else{flushItems();prose.push(line);}
    }
    flushProse();flushItems();
    return output.join('');
  }
  function emphasizeExistingText() {
    // An older running server may still return escaped plain text. Build only
    // strong/text nodes, so a report can never inject arbitrary markup.
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode, parent = node.parentElement;
      if (parent?.closest('p,li,td,th,figcaption') &&
          !parent.closest('strong,code,pre,script,style,textarea,.ic-readout') &&
          /\*\*[^*\n]+\*\*/.test(node.nodeValue)) nodes.push(node);
    }
    for (const node of nodes) {
      const fragment = document.createDocumentFragment(), pattern = /\*\*([^*\n]+)\*\*/g;
      let last = 0, match;
      while ((match = pattern.exec(node.nodeValue))) {
        fragment.append(document.createTextNode(node.nodeValue.slice(last, match.index)));
        const strong = document.createElement('strong'); strong.textContent = match[1];
        fragment.append(strong); last = pattern.lastIndex;
      }
      fragment.append(document.createTextNode(node.nodeValue.slice(last)));
      node.replaceWith(fragment);
    }
  }
  // Also style visual sections when an older server reads this newer script.
  const visualStyle = document.createElement('style');
  visualStyle.textContent = `


    strong{font-weight:700}
.review-strip{display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:16px;margin-top:18px}
.review-strip button{font-size:16px;padding:7px 11px}.review-note{font-size:15px;color:#596575}
.report-bullets{max-width:68ch;padding-left:25px;margin:14px 0 24px}
.report-bullets>li{margin:10px 0;line-height:1.55;overflow-wrap:anywhere}
.report-bullets>li::marker{color:#507bb1}
.task-overview{padding-bottom:18px;margin-bottom:30px;border-bottom:1px solid #d4dce6}
.task-overview h1{margin:0 0 12px}
.task-overview p{margin:0}
.task-overview .current-task{margin-bottom:12px;color:#465368}
.current-task .status{white-space:nowrap}
.citation-number{font-size:.85em;white-space:nowrap}
figure[id]{scroll-margin-top:24px}
#task-progress,#results,#next-action{scroll-margin-top:24px}
.main-reading{margin:32px 0;scroll-margin-top:24px}
.current-work{margin:28px 0;padding-left:18px;border-left:3px solid #d5dde7}
#task-content>section#current-work.current-work{border-top:0;padding-top:0;margin-top:28px}
.current-work>h2{margin-top:0}
.current-work h3,.phase-group h3{font-size:22px;line-height:1.35}
.main-reading h4{font-size:20px;line-height:1.4;margin:16px 0}
#task-content>#next-action,#task-content>#future-work,#task-content>#previous-work,#task-content>#history{
 border-top:1px solid #bdc8d4;margin-top:32px;padding-top:24px}
.appendix-links,.appendix-return{font-size:16px;line-height:1.65}
.appendix-links a{display:inline-block;margin:3px 12px 3px 0}
.appendices{margin-top:52px;border-top:3px solid #9cabbc;padding-top:18px}
.appendices>h2{font-size:28px;margin-bottom:8px}
.appendix-intro{font-size:17px;color:#626771;margin-top:0}
.appendix-group{margin:32px 0;padding:4px 0 0 18px;border-left:2px solid #d4dce6}
.appendix-group>h3{font-size:22px;line-height:1.35;margin:0 0 16px}
.appendix-group .task-part{margin:0;scroll-margin-top:24px}
.task-part>summary{font-size:18px;line-height:1.45}
.appendix-number{font-size:16px;color:#626771;font-weight:400;margin-right:7px}
.appendix-preview{display:block;font-size:16px;font-weight:400;line-height:1.5;color:#505b68;margin:7px 0 0 26px}
.task-part[open]>summary .appendix-preview{display:none}
.task-part h2{font-size:21px}
.appendix-return{margin:10px 0 18px}
.recorded-details{font-size:17px;margin-top:20px}

    .prose-comparison{table-layout:fixed}
    figure{margin:26px 0}figcaption,.axis-note{font-size:16px;color:#626771;margin-top:10px}
    .comparison-pairs{list-style:none;padding:0;margin:20px 0}
    .comparison-pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr) minmax(0,1fr);gap:16px;align-items:center;margin:20px 0}
    .comparison-node{border:1px solid #bdc8d4;border-radius:8px;background:#fff;padding:15px 12px;text-align:center;font-size:18px;font-weight:600;overflow-wrap:anywhere}
    .comparison-edge{display:flex;flex-direction:column;align-items:center;text-align:center;color:#285e9c;font-size:17px;line-height:1.45}
    .comparison-arrow{font-size:34px;line-height:1.1}
    .comparison-fixed{border-top:2px solid #bdc8d4;padding:8px 16px;background:#edf1f5}
    .comparison-fixed ul{list-style:none;display:flex;flex-wrap:wrap;gap:6px 20px;padding:0;margin:8px 0;font-size:17px}
    .comparison-fixed li{margin:0}.comparison-resources{margin:14px 0 6px;font-size:18px;font-weight:600}
    .flow-steps{list-style:none;padding:0;display:flex;gap:30px;counter-reset:flow}
    .flow-steps li{position:relative;flex:1;min-width:0;border-top:3px solid #507bb1;background:#edf1f5;padding:16px;margin:0;counter-increment:flow}
    .flow-steps strong,.flow-steps span{display:block;font-size:17px;overflow-wrap:anywhere}
    .flow-steps strong:before{content:counter(flow) ". ";color:#285e9c}
    .flow-steps span{margin-top:8px;line-height:1.5}
    .flow-steps li:not(:last-child):after{content:"→";position:absolute;right:-25px;top:20px;color:#285e9c;font-size:25px}
.progress-overview{margin:20px 0 28px;padding-bottom:24px;border-bottom:1px solid #bdc8d4}
.progress-overview .flow-steps strong:before{content:none}
.progress-overview .progress-completed{border-color:#48705c;background:#f0f4f1}
.progress-overview .progress-current{border-color:#285e9c;background:#e9f0f9}
.progress-overview .progress-next{border-color:#8793a2;background:#f0f1f3}
.progress-overview .ic-readout{display:none}
.progress-overview[data-inspecting=true] .ic-readout{display:block}
    table{width:100%;border-collapse:collapse;font-size:17px;line-height:1.45}
    th,td{padding:12px 10px;text-align:left;border-bottom:1px solid #d4d8df;overflow-wrap:anywhere}
    thead{background:#edf1f5}tbody th{font-weight:600}tbody tr:nth-child(even){background:#f0f2f5}
    .bar-row{display:grid;grid-template-columns:minmax(140px,1fr) 2fr 94px;gap:16px;align-items:center;margin:18px 0}
    .bar-label{font-size:17px;font-weight:600}.bar-label small{display:block;font-size:15px;font-weight:400;color:#626771}
    .bar-track{height:22px;background:#e0e6ed;border-radius:4px;overflow:hidden}
    .bar-fill{height:100%;background:#507bb1}.bar-value{font-size:17px;font-variant-numeric:tabular-nums}
    .curve-legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:16px}
    .curve-legend i{display:inline-block;width:12px;height:12px;margin-right:7px;border-radius:50%}
    .curve-layout{display:grid;grid-template-columns:38px minmax(0,1fr);column-gap:8px}
    .curve-y-ticks{position:relative;grid-column:1;height:240px;font-size:16px}
    .curve-y-ticks span{position:absolute;right:0;transform:translateY(-50%)}
    .curve-plot{grid-column:2;position:relative;height:240px;border-left:1px solid #9faebd;border-bottom:1px solid #9faebd;
      background:repeating-linear-gradient(to top,#dfe5ec 0 1px,transparent 1px 25%);}
    .curve-plot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
    .curve-point{position:absolute;width:8px;height:8px;border-radius:50%;transform:translate(-50%,-50%)}
    .curve-x-ticks{grid-column:2;position:relative;height:36px;font-size:16px}
    .curve-x-ticks span{position:absolute;top:9px;transform:translateX(-50%)}
    .curve-x-ticks span:first-child{transform:none}.curve-x-ticks span:last-child{transform:translateX(-100%)}
    .curve-x-label{font-size:16px;text-align:center;margin:5px 0}
    @media(max-width:600px){
    .comparison-pair{grid-template-columns:1fr;gap:8px;padding-bottom:20px;border-bottom:1px solid #d4d8df}
    .comparison-edge{flex-direction:row;justify-content:center;gap:12px}.comparison-arrow{transform:rotate(90deg)}
    .flow-steps{flex-direction:column;gap:26px}
    .flow-steps li:not(:last-child):after{content:"↓";right:50%;top:auto;bottom:-28px;transform:translateX(50%)}
    table,tbody,tr,th,td{display:block}thead{display:none}
    tbody tr{border-bottom:1px solid #bdc8d4;padding:8px 0}
    tbody th,tbody td{border:0;padding:6px 10px}tbody td:before{content:attr(data-label) ": ";font-weight:600}
    .bar-row{grid-template-columns:1fr 94px;gap:8px}.bar-label{grid-column:1 / -1}.bar-track{grid-column:1}}`;
  document.head.appendChild(visualStyle);
  function visuals(state,sectionId) {
    const colors = ['#59636f','#2263bd','#08775e','#9a4eb3','#9a4b56','#537684','#68602a','#555'];
    const curves = (state.curves || []).map((chart,chartIndex) => {
      let legend = '', marks = '';
      chart.series.forEach((series,index) => {
        const color = colors[index];
        legend += '<span><i style="background:' + color + '"></i>' + escape(series.label) +
          (series.points.length === 0 ? ' · no measurement' : series.points.length === 1 ? ' · one measurement' : '') + '</span>';
        const segments = []; let current = [];
        series.points.forEach(point => {
          if (current.length && point.x - current.at(-1).x > chart.gap) {
            segments.push(current); current = [];
          }
          current.push(point);
        });
        if (current.length) segments.push(current);
        segments.forEach(segment => {
          const coordinates = segment.map(p => (p.x / chart.x_maximum * 100) + ',' +
            (100 - p.y / chart.y_maximum * 100)).join(' ');
          marks += '<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polyline points="' +
            coordinates + '" fill="none" stroke="' + color + '" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>';
        });
        series.points.forEach(point => {
          const label = series.label + ': ' + point.y + ' at ' + point.x;
          marks += '<span class="curve-point" aria-label="' + escape(label) + '" title="' + escape(label) +
            '" style="left:' + (point.x / chart.x_maximum * 100) + '%;top:' +
            (100 - point.y / chart.y_maximum * 100) + '%;background:' + color + '"></span>';
        });
      });
      const yTicks = Array.from({length:5},(_,i) => '<span style="top:' + (100-i*25) + '%">' +
        (chart.y_maximum*i/4) + '</span>').join('');
      const xTicks = Array.from({length:5},(_,i) => '<span style="left:' + (i*25) + '%">' + (chart.x_maximum >= 1000 ?
        chart.x_maximum*i/4/1000 + 'k' : chart.x_maximum*i/4) + '</span>').join('');
      const anchor = sectionId ? ' id="curve-' + escape(sectionId) + '-' + (chartIndex+1) + '" tabindex="-1"' : '';
      return '<figure class="learning-curve"' + anchor + ' data-chart="' + escape(JSON.stringify(chart)) + '"><h2>' + escape(chart.title) + '</h2><div class="curve-legend">' +
        legend + '</div><p class="axis-note">' + escape(chart.y_label) + '</p><div class="curve-layout">' +
        '<div class="curve-y-ticks">' + yTicks + '</div><div class="curve-plot">' + marks +
        '</div><div class="curve-x-ticks">' + xTicks + '</div></div><p class="curve-x-label">' +
        escape(chart.x_label) + '</p><figcaption>' + escape(chart.caption) + '</figcaption></figure>';
    }).join('');
    const tables = (state.tables || []).map((table,tableIndex) => '<figure class="comparison"' +
      (sectionId ? ' id="table-' + escape(sectionId) + '-' + (tableIndex+1) + '" tabindex="-1"' : '') + '><h2>' +
      escape(table.title) + '</h2><table' + (table.row_headers === false ? ' class="prose-comparison"' : '') +
      '><thead><tr>' + table.columns.map(label =>
        '<th scope="col">' + escape(label) + '</th>').join('') + '</tr></thead><tbody>' +
      table.rows.map(row => '<tr>' + row.map((value,i) => '<' + (i || table.row_headers === false ? 'td' : 'th scope="row"') +
        ' data-label="' + escape(table.columns[i]) + '">' + escape(value) + '</' +
        (i || table.row_headers === false ? 'td' : 'th') + '>').join('') + '</tr>').join('') + '</tbody></table><figcaption>' +
      escape(table.caption) + '</figcaption></figure>').join('');
    const figures = (state.figures || []).map(figure => '<figure class="bar-chart"><h2>' +
      escape(figure.title) + '</h2><p class="axis-note">' + escape(figure.unit) + ' · scale 0–' +
      figure.maximum + '</p>' + figure.values.map(row => '<div class="bar-row"><div class="bar-label">' +
        escape(row.label) + '<small>' + escape(row.note) + '</small></div>' +
        '<div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:' +
        (row.value / figure.maximum * 100) + '%"></div></div><div class="bar-value">' +
        row.value + ' / ' + figure.maximum + '</div></div>').join('') +
      '<figcaption>' + escape(figure.caption) + '</figcaption></figure>').join('');
    const flows = (state.flows || []).map(flow => '<figure class="flow-diagram"><h2>' +
      escape(flow.title) + '</h2><ol class="flow-steps">' + flow.steps.map(step =>
        '<li><strong>' + escape(step.title) + '</strong><span>' + escape(step.text) +
        '</span></li>').join('') + '</ol><figcaption>' + escape(flow.caption) +
      '</figcaption></figure>').join('');
    return curves + tables + figures + flows;
  }
  function interactiveCurves(state) {
    const colors=['#59636f','#2263bd','#08775e','#9a4eb3','#9a4b56','#537684','#a06a00','#6633cc'];
    const charts=[...(state?.curves||[]),...(state?.main_sections||[]).flatMap(s=>s.curves||[]),
                  ...(state?.sections||[]).flatMap(s=>s.curves||[])];
    root.querySelectorAll('figure.learning-curve').forEach((figure,index)=>{
      // Closed appendices retain their recorded data without building or
      // redrawing interactive charts the reader cannot currently see.
      if (figure.closest('details:not([open])')) return;
      let holder=figure.querySelector('.ic-holder');
      if(holder && figure._curveSource === figure.dataset.chart)return;
      let chart;
      try{chart=JSON.parse(figure.dataset.chart);}catch(_){chart=charts.find(c=>c.title===figure.querySelector('h2,h3,h4')?.textContent);}
      if(!chart)return;
      if(!holder){
        holder=document.createElement('div');holder.className='ic-holder';
        figure.querySelectorAll('.curve-layout,.curve-x-label,.axis-note').forEach(n=>n.remove());
        figure.insertBefore(holder,figure.querySelector('figcaption'));
      }
      figure._curveSource=figure.dataset.chart;
      figure._curveKey ||= location.pathname+':'+chart.title+':'+index;
      InteractiveCurve.mount(holder,{...chart,markers:true,series:chart.series.map((s,i)=>({...s,color:colors[i%colors.length]}))},figure._curveKey);
    });
  }
  root.addEventListener('toggle', event => {
    if (event.target.tagName === 'DETAILS' && event.target.open) interactiveCurves();
  }, true);
  function interactiveVisuals(state) {
    emphasizeExistingText();
    interactiveCurves(state);
    root.querySelectorAll('.bar-row').forEach(row=>{
      const units=row.closest('figure')?.querySelector('.axis-note')?.textContent.trim() || '';
      row.title=row.textContent.trim()+(units?' · '+units:'');row.tabIndex=0;row.setAttribute('aria-label',row.title);
      if(row.dataset.interactive)return;row.dataset.interactive='true';
      const detail=document.createElement('p');detail.className='ic-readout';detail.hidden=true;detail.textContent=row.title;row.after(detail);
      const show=()=>detail.hidden=false, hide=()=>detail.hidden=true;
      row.addEventListener('pointerenter',show);row.addEventListener('pointerleave',hide);row.addEventListener('focus',show);row.addEventListener('blur',hide);
    });
    InteractiveDiagram.mountAll(root);
    root.querySelectorAll('.progress-overview').forEach(figure => {
      if (figure.dataset.progressInspection) return;
      figure.dataset.progressInspection = 'true';
      const hint = figure.querySelector('.ic-readout');
      figure.querySelectorAll('.flow-steps li').forEach(node => {
        const show = () => {
          figure.dataset.inspecting = 'true';
          hint.textContent = node.title;
        };
        const hide = () => {
          if (!figure.contains(document.activeElement)) delete figure.dataset.inspecting;
        };
        node.addEventListener('pointerenter', show);
        node.addEventListener('pointerdown', show);
        node.addEventListener('focus', show);
        node.addEventListener('pointerleave', hide);
        node.addEventListener('blur', () => delete figure.dataset.inspecting);
      });
    });
  }
  function links(rows, title, id = 'supporting-links') {
    if (!rows?.length) return '';
    const list = '<ul>' + rows.map(row => '<li><a href="' + escape(row.url) + '">' +
      escape(row.title) + '</a>' + (row.description ? '<p class="link-purpose">' +
        escape(row.description) + '</p>' : '') + '</li>').join('') + '</ul>';
    return rows.length > 3 ? '<details id="' + escape(id) + '"><summary>' + escape(title) +
      '</summary>' + list + '</details>' : '<div class="linked-pages"><h3>' +
      escape(title) + '</h3>' + list + '</div>';
  }
  function planDiagram(state) {
    const diagram = state.plan_diagram;
    if (!diagram?.comparisons) return '';
    return '<figure class="plan-diagram"><h2>' + escape(diagram.title) +
      '</h2><ul class="comparison-pairs">' + diagram.comparisons.map(row =>
        '<li class="comparison-pair"><span class="comparison-node">' + escape(row.from) +
        '</span><span class="comparison-edge"><span>Change: ' + escape(row.change) +
        '</span><span class="comparison-arrow" aria-hidden="true">→</span></span>' +
        '<span class="comparison-node">' + escape(row.to) + '</span></li>').join('') +
      '</ul><div class="comparison-fixed"><h3>Keep the original setup</h3><ul>' +
      diagram.fixed.map(label => '<li>' + escape(label) + '</li>').join('') +
      '</ul><p class="comparison-resources">' + escape(diagram.resources) +
      '</p></div><figcaption>' + escape(diagram.caption) + '</figcaption></figure>';
  }
  // Older report servers read this installed script on every page load.
  // Their pages can use the simpler layout before the GUI is relaunched.
  function appendixLayout(state) {
    const groups = new Map(), lookup = new Map();
    state.sections.forEach(row => {
      const group = row.group || 'Supporting detail';
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(row);
    });
    let groupIndex = 0;
    groups.forEach(rows => {
      groupIndex++;
      rows.forEach((row, index) => {
        const context = {row, number:groupIndex + '.' + (index + 1), mains:[]};
        lookup.set(row.id,context);
        [['curves','curve'],['tables','table']].forEach(([field,kind]) =>
          (row[field] || []).forEach((_,i) => lookup.set(kind+'-'+row.id+'-'+(i+1),context)));
      });
    });
    (state.main_sections || []).forEach((main,index) =>
      [...new Set([...main.sections,...referenceIds(main.text)])].forEach(id => {
        const context = lookup.get(id), anchor = 'main-'+(index+1);
        if (context && !context.mains.some(m => m.anchor === anchor)) context.mains.push({anchor,title:main.title});
      }));
    const fullPlan = state.tables?.length || state.figures?.length || state.curves?.length || state.flows?.length || state.plan_diagram?.comparisons;
    [['summary','task-summary','Current work'],
     ['results','results',state.results_title||'Latest recorded progress'],
     ['next_action','next-action','Next action'],
     ['plan',fullPlan?'full-plan':'plan','Plan']].forEach(([name,anchor,title]) =>
      [...new Set(referenceIds(state[name]))].forEach(id => {
        const context = lookup.get(id);
        if (context && !context.mains.some(m => m.anchor === anchor)) context.mains.push({anchor,title});
      }));
    return {groups,lookup};
  }
  function mainReading(state, lookup, phase='current') {
    return (state.main_sections || []).map((row,index) => {
      if ((row.phase || 'current') !== phase) return '';
      const cited = new Set(referenceIds(row.text).filter(id => lookup.has(id)).map(id => lookup.get(id).row.id));
      const supporting = row.sections.filter(id => lookup.has(id) && !cited.has(id)).map(id => {
        const section = lookup.get(id);
        return '<a href="#' + escape(id) + '">Appendix ' + section.number + ': ' + escape(section.row.title) + '</a>';
      });
      return '<section id="main-' + (index+1) + '" class="main-reading" tabindex="-1"><h3>' +
        escape(row.title) + '</h3>' + paragraphs(row.text,lookup,true) +
        visuals(row,'main-'+(index+1)).replaceAll('<h2>','<h4>').replaceAll('</h2>','</h4>') +
        (supporting.length ? '<p class="appendix-links">Supporting detail: ' + supporting.join('; ') + '</p>' : '') + '</section>';
    }).join('');
  }
  function appendixBody(state, groups, lookup) {
    if (!groups.size) return '';
    const intro = state.main_sections?.length ? 'The main report links to the relevant detail below. Each appendix returns to the finding it supports.' :
      'Supporting records and implementation details are grouped below.';
    let body = '<section class="appendices" id="appendices"><h2>Appendix · supporting detail</h2>' +
      '<p class="appendix-intro">' + intro + '</p>', groupIndex = 0;
    groups.forEach((rows,title) => {
      body += '<section class="appendix-group"><h3>' + (++groupIndex) + ' · ' + escape(title) + '</h3>';
      rows.forEach(row => {
        const context = lookup.get(row.id);
        body += '<details id="' + escape(row.id) + '" class="task-part"><summary id="appendix-summary-' + escape(row.id) + '">' +
          '<span class="appendix-number">' + context.number + '</span> <span class="appendix-title">' +
          escape(row.title) + '</span>' + (row.summary ? '<span class="appendix-preview">' +
            inlineText(row.summary) + '</span>' : '') + '</summary>';
        if (context.mains.length) body += '<p class="appendix-return">' + context.mains.map(main =>
          '<a href="#' + escape(main.anchor) + '">↑ Return to: ' + escape(main.title) + '</a>').join(' · ') + '</p>';
        if (row.summary) body += paragraphs(row.text);
        body += visuals(row,row.id) + planDiagram(row);
        const hasVisuals = row.tables?.length || row.figures?.length || row.curves?.length || row.plan_diagram?.comparisons || row.flows?.length;
        if (!row.summary) body += hasVisuals ? '<details class="recorded-details"><summary>Recorded details</summary>' +
          paragraphs(row.text) + '</details>' : paragraphs(row.text);
        if (row.source_text) body += '<details id="source-' + escape(row.id) +
          '" class="recorded-details"><summary>Exact source wording and records</summary>' + paragraphs(row.source_text,undefined,false,false) + '</details>';
        body += links(row.references, row.links_title || 'Open related pages', 'links-' + row.id) + '</details>';
      });
      body += '</section>';
    });
    return body + '</section>';
  }
  function progressOverview(state) {
    const overview = state.progress_overview;
    if (!overview?.completed) return '';
    return '<figure class="progress-overview" aria-label="Progress toward the main task"><ol class="flow-steps">' +
      [['completed','Done so far'],['current','Current stage'],['next','Next step']].map(([key,label]) =>
        '<li class="progress-'+key+'" id="progress-'+key+'"><strong>'+label+'</strong><span>'+
        inlineText(overview[key])+'</span></li>').join('')+'</ol><figcaption>'+
        inlineText(overview.caption)+'</figcaption></figure>';
  }
  function legacyBody(state) {
    const {groups,lookup} = appendixLayout(state);
    const status = state.status.charAt(0).toUpperCase() + state.status.slice(1);
    let body = '<header class="task-overview" id="overview" data-header-layout="task-v2"><h1>' +
      escape(state.title) + '</h1><p class="current-task">' +
      (state.current_task ? '<strong>Current task:</strong> ' + inlineText(state.current_task) +
        ' <span class="status">· ' + escape(status) + '</span>' :
        '<strong>Current status:</strong> ' + escape(status)) + '</p>';
    if (state.objective) {
      body += '<p id="task-objective">' +
        inlineText(state.objective) + '</p>';
    }
    body += '</header><!-- report-lists-v2 --><!-- report-groups-v3 -->';
    if (state.progress_overview?.completed) {
      body += '<section id="task-progress" tabindex="-1"><h2>Progress toward the main task</h2>' +
        progressOverview(state) + '</section>';
    }
    body += '<section id="current-work" class="current-work" aria-labelledby="current-work-title" tabindex="-1">' +
      '<h2 id="current-work-title">Current work</h2><div id="task-summary" tabindex="-1">' +
      (paragraphs(state.summary,lookup,true) || '<p>Add the main goal and current recommendation.</p>') + '</div>';
    body += visuals(state).replaceAll('<h2>','<h3>').replaceAll('</h2>','</h3>');
    for (const [name, label] of [['results',state.results_title || 'Latest recorded progress'],['plan',"What we're doing"]]) {
      if (name === 'plan') {
        body += mainReading(state,lookup);
      }
      if (name === 'plan' && state.plan_diagram?.comparisons) {
        body += '<details id="full-plan"><summary>Plan and comparison rules</summary>' +
          planDiagram(state) + paragraphs(state.plan,lookup) + '</details>';
        continue;
      }
      if (!state[name]) continue;
      if (name === 'plan' && (state.tables?.length || state.figures?.length || state.curves?.length || state.flows?.length)) {
        body += '<details id="full-plan"><summary>Plan and comparison rules</summary>' +
          paragraphs(state.plan,lookup) + '</details>';
        continue;
      }
      const first = state[name].trim().split('\n\n')[0];
      const brief = first;
      const essential = name === 'results' && !!state.results_title;
      body += '<section id="' + name + '" tabindex="-1"><h3>' + escape(label) + '</h3>' + paragraphs(essential ? state[name] : brief,lookup);
      const remaining = state[name].trim().split('\n\n').slice(1).join('\n\n');
      if (!essential && remaining)
        body += '<details id="full-' + name + '"><summary>Read the full ' +
          (name === 'results' ? 'progress update' : 'plan') + '</summary>' +
          paragraphs(remaining,lookup) + '</details>';
      body += '</section>';
    }
    body += '</section>';
    if (state.next_action) body += '<section class="next-action" id="next-action" tabindex="-1"><h2>Next action</h2>' +
      paragraphs(state.next_action,lookup) + '</section>';
    for (const [phase,id,label] of [['future','future-work','Future work'],
                                   ['previous','previous-work','Completed work and prior records']]) {
      const reading = mainReading(state,lookup,phase);
      if (reading) body += '<section id="'+id+'" class="phase-group" aria-labelledby="'+id+'-title">'+
        '<h2 id="'+id+'-title">'+label+'</h2>'+reading+'</section>';
    }
    body += appendixBody(state, groups, lookup);
    body += links(state.references, state.links_title || 'Open related pages');
    if (state.revision > 1) body += '<details id="history"><summary>Earlier updates</summary>' +
      '<p class="history-note">Saved earlier plans and progress.</p>' +
      '<button id="refresh-history" type="button" hidden>Show latest updates</button>' +
      '<div id="history-items"></div><button id="more-history" type="button">' +
      'Load earlier updates</button></details>';
    return body;
  }
  let resolvedAnchor = null;
  function showAnchor(force = true) {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    const node = document.getElementById(id);
    if (!force && node === resolvedAnchor) return;
    resolvedAnchor = node;
    for (let current = node; current && current !== root; current = current.parentElement) {
      if (current.tagName === 'DETAILS') current.open = true;
    }
  }
  async function loadHistory() {
    if (!live || busy || historyBusy) return;
    const holder = root.querySelector('#history-items'), button = root.querySelector('#more-history');
    if (!holder || !button) return;
    historyBusy = true; button.disabled = true;
    try {
      const endpoint = legacyHistory ? '/history' : '/updates';
      const query = cursor === null ? '' : '?before=' + encodeURIComponent(
        legacyHistory ? cursor : JSON.stringify(cursor));
      let response = await fetch(location.pathname + endpoint + query);
      if (response.status === 404 && !legacyHistory) {
        legacyHistory = true; cursor = null;
        response = await fetch(location.pathname + '/history');
      }
      if (!response.ok) throw Error('Earlier updates unavailable');
      const data = await response.json(), rows = legacyHistory ? data : data.rows;
      rows.forEach(row => {
        const detail = document.createElement('details');
        detail.id = 'revision-' + row.id + '-' + row.revision;
        detail.innerHTML = '<summary>' + escape(row.part || 'Main task') +
          ' · Revision ' + row.revision + ' · ' +
          escape(new Date(row.updated * 1000).toLocaleString()) + '</summary>' +
          [['summary','Goal'],['results','Recorded progress'],['next_action','Next action'],['plan','Plan']]
          .filter(([name]) => row[name]).map(([name,label]) => '<h3>' + label +
            '</h3>' + paragraphs(row[name])).join('') + visuals(row) + planDiagram(row);
        holder.appendChild(detail);
      });
      interactiveVisuals();
      historyLoaded = true;
      cursor = legacyHistory ? rows.at(-1)?.revision ?? cursor : data.cursor;
      button.hidden = legacyHistory ? rows.length < 20 : cursor === null;
    } catch (_) {
      document.querySelector('#update-indicator').textContent = 'Earlier updates unavailable · retry';
    } finally { button.disabled = false; historyBusy = false; }
  }
  root.addEventListener('toggle', event => {
    if (event.target.id === 'history' && event.target.open && !historyLoaded) loadHistory();
  }, true);
  root.addEventListener('click', event => {
    const anchor = event.target.closest('a[href^="#"]');
    if (anchor) {
      let node;
      try { node = document.getElementById(decodeURIComponent(anchor.hash.slice(1))); } catch (_) {}
      if (node) {
        event.preventDefault();
        history.pushState(null,'',anchor.hash);
        showAnchor();
        node.scrollIntoView({block:'start'});
        const focus = node.tagName === 'DETAILS' ? node.querySelector('summary') : node;
        focus.focus({preventScroll:true});
      }
    }
    if (event.target.id === 'more-history') loadHistory();
    if (event.target.id === 'refresh-history') {
      root.querySelector('#history-items').innerHTML = '';
      cursor = null; historyLoaded = false; event.target.hidden = true; loadHistory();
    }
  });
  // Compare publisher markup with its previous version, not with the enhanced
  // live DOM. This preserves controls and layout wrappers added by renderers.
  function bind(source, live) {
    source._live = live;
    [...source.childNodes].forEach((child, index) => bind(child, live.childNodes[index]));
    return source;
  }
  function snapshot(live) { return bind(live.cloneNode(true), live); }
  function inherit(before, after) {
    after._live = before?._live;
    [...after.childNodes].forEach((child, index) => inherit(before?.childNodes[index], child));
  }
  function identities(parent) {
    const occurrences = new Map();
    return [...parent.childNodes].map(node => {
      let key = node.nodeType === 1 ? node.tagName : '#' + node.nodeType;
      if (node.id) key += ':' + node.id;
      else if (node.matches?.('figure')) key += ':' + node.className + ':' + (node.querySelector('h2,h3,h4')?.textContent || '');
      else if (node.matches?.('tr')) key += ':' + node.firstElementChild?.textContent;
      const count = occurrences.get(key) || 0; occurrences.set(key, count + 1);
      return [key + ':' + count, node];
    });
  }
  function dispose(node) {
    node?._interactiveCleanup?.();
    node?.querySelectorAll?.('.ic-holder,.ic-metric-chart').forEach(holder => holder._interactiveCleanup?.());
    node?.remove();
  }
  function patchSource(before, after) {
    const live = before._live; after._live = live;
    if (before.isEqualNode(after)) { inherit(before, after); return; }
    if (before.nodeType !== 1) { live.nodeValue = after.nodeValue; return; }
    // Runtime-only attributes (open state, adapter IDs, classes) are retained.
    for (const name of new Set([...before.getAttributeNames(), ...after.getAttributeNames()])) {
      if (before.getAttribute(name) === after.getAttribute(name)) continue;
      if (after.hasAttribute(name)) live.setAttribute(name, after.getAttribute(name));
      else live.removeAttribute(name);
    }
    if (after.matches('figure.learning-curve')) {
      // The measured-curve renderer updates data within the existing controls.
      const old = [...before.children], next = [...after.children];
      for (let i = 0; i < next.length; i++) {
        if (old[i]?.tagName === next[i].tagName) {
          if (next[i].matches('h2,h3,h4,figcaption')) patchSource(old[i], next[i]);
          else inherit(old[i], next[i]);
        }
      }
      return;
    }
    const old = identities(before), next = identities(after), oldByKey = new Map(old);
    const used = new Set(next.map(([key]) => key));
    // A renderer may have replaced a text node (for example a status cell).
    // Only that changed container needs fresh publisher markup.
    if (old.some(([key, node]) => used.has(key) && !node._live?.isConnected)) {
      live.replaceChildren(...[...after.childNodes].map(node => bind(node, node.cloneNode(true))._live));
      return;
    }
    for (const [key, node] of old) if (!used.has(key)) dispose(node._live);
    for (const [key, node] of next) {
      const previous = oldByKey.get(key);
      if (previous) patchSource(previous, node);
      else bind(node, node.cloneNode(true));
    }
    const commonBefore = JSON.stringify(old.filter(([key]) => used.has(key)).map(([key]) => key));
    const commonAfter = JSON.stringify(next.filter(([key]) => oldByKey.has(key)).map(([key]) => key));
    const reordered = commonBefore !== commonAfter;
    // Leave existing nodes where adapters placed them when source order is
    // unchanged. Insert new siblings beside the nearest surviving source node.
    let anchor = null;
    for (const [key, node] of [...next].reverse()) {
      const item = node._live;
      if (!oldByKey.has(key) || (reordered && item.parentNode === live)) live.insertBefore(item, anchor);
      let direct = item;
      while (direct?.parentNode && direct.parentNode !== live) direct = direct.parentNode;
      if (direct?.parentNode === live) anchor = direct;
    }
  }
  function replaceReport(html) {
    const content = document.createElement('template'); content.innerHTML = html;
    const next = sourceTree.cloneNode(false); next.append(content.content);
    patchSource(sourceTree, next); sourceTree = next;
  }
  async function refresh() {
    if (busy || historyBusy || document.hidden || window.getSelection()?.toString()) return;
    busy = true;
    try {
      const response = await fetch(location.pathname + '/data',
        {headers: etag ? {'If-None-Match': etag} : {}});
      if (response.status === 304) {
        document.querySelector('#update-indicator').textContent = 'Current task';
        return;
      }
      if (!response.ok) throw Error('Task page unavailable');
      etag = response.headers.get('ETag');
      const data = await response.json();
      window.HerdrReview.report(data.state, location.pathname.split('/task/')[0], data.review);
      if (data.state.parent) {
        const parent = location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1) +
          data.state.parent + '#' + data.state.id;
        location.replace(parent);
        return;
      }
      const oldLayout = root.querySelector('nav[role=tablist]');
      const groupUpgrade = !root.querySelector(':scope > #current-work') ||
        root.dataset.groupLayout !== 'v3';
      // Layout adapters may nest charts in comparison containers. Count every
      // declared chart, including main passages and appendices, at any depth.
      const expectedCurves = (data.state.curves || []).length +
        [...(data.state.main_sections || []), ...data.state.sections]
          .reduce((count, row) => count + (row.curves || []).length, 0);
      const curveUpgrade = expectedCurves !== root.querySelectorAll('figure.learning-curve').length;
      const expectedDiagrams = Number(!!data.state.plan_diagram?.comparisons) +
        data.state.sections.filter(row => row.plan_diagram?.comparisons).length;
      const diagramUpgrade = expectedDiagrams !== root.querySelectorAll('#full-plan > .plan-diagram, .task-part > .plan-diagram').length;
      const expectedFlows = (data.state.flows || []).length +
        data.state.sections.reduce((count,row) => count + (row.flows || []).length,0);
      const flowUpgrade = expectedFlows !== root.querySelectorAll('#current-work > .flow-diagram, .task-part > .flow-diagram').length;
      const unformatted = /\*\*[^*\n]+\*\*/.test(data.html);
      const proseUpgrade = unformatted && root.dataset.proseLayout !== 'v1';
      const listUpgrade = root.dataset.textLayout !== 'lists-v2';
      const appendixUpgrade = data.state.sections.length > 0 && !root.querySelector('.appendices');
      const mainUpgrade = (data.state.main_sections || []).length !== root.querySelectorAll('.main-reading').length;
      const objectiveUpgrade = !root.querySelector('[data-header-layout="task-v2"]');
      const progressUpgrade = !!data.state.progress_overview?.completed && !root.querySelector('.progress-overview');
      const expectedCitations = ['summary','results','plan','next_action'].reduce((n,key) => n+referenceIds(data.state[key]).length,0) +
        (data.state.main_sections || []).reduce((n,row) => n+referenceIds(row.text).length,0);
      const citationUpgrade = expectedCitations !== root.querySelectorAll('.appendix-citation').length;
      if (!oldLayout && !groupUpgrade && !curveUpgrade && !diagramUpgrade && !flowUpgrade && !proseUpgrade && !listUpgrade && !appendixUpgrade && !mainUpgrade && !objectiveUpgrade && !progressUpgrade && !citationUpgrade && String(data.state.revision) === root.dataset.revision) return;
      const y = scrollY, focusedNode = document.activeElement, focused = focusedNode?.id;
      const open = Array.from(root.querySelectorAll('details[open]')).map(node => node.id);
      const previousHistory = root.querySelector('#history-items')?.innerHTML;
      const missingCurves = data.state.curves?.length && !data.html.includes('class="learning-curve"');
      const missingDiagram = expectedDiagrams !== (data.html.match(/class="plan-diagram"/g) || []).length;
      const missingFlows = expectedFlows !== (data.html.match(/class="flow-diagram"/g) || []).length;
      const missingAppendix = data.state.sections.length > 0 && !data.html.includes('class="appendices"');
      const missingMain = (data.state.main_sections || []).length !== (data.html.match(/class="main-reading"/g) || []).length;
      const missingObjective = !data.html.includes('data-header-layout="task-v2"');
      const missingProgress = !!data.state.progress_overview?.completed && !data.html.includes('class="progress-overview"');
      const missingLists = !data.html.includes('<!-- report-lists-v2 -->');
      const missingGroup = !data.html.includes('<!-- report-groups-v3 -->');
      const missingCitations = expectedCitations !== (data.html.match(/class="appendix-citation"/g) || []).length;
      replaceReport(/role="tablist"/.test(data.html) || missingGroup || missingCurves || missingDiagram || missingFlows || unformatted || missingAppendix || missingMain || missingObjective || missingProgress || missingLists || missingCitations ? legacyBody(data.state) : data.html);
      interactiveVisuals(data.state);
      root.dispatchEvent(new CustomEvent('herdr:report-update',{detail:{state:data.state}}));
      root.dataset.proseLayout = 'v1';
      root.dataset.textLayout = 'lists-v2';
      root.dataset.groupLayout = 'v3';
      root.dataset.revision = data.state.revision;
      window.HerdrReview.report(data.state, location.pathname.split('/task/')[0], data.review);
      document.title = data.state.title;
      if (previousHistory && root.querySelector('#history-items')) {
        if (root.querySelector('#history-items').innerHTML !== previousHistory)
          root.querySelector('#history-items').innerHTML = previousHistory;
        root.querySelector('#refresh-history').hidden = false;
      } else { cursor = null; historyLoaded = false; }
      open.forEach(id => { const node = document.getElementById(id); if (node) node.open = true; });
      showAnchor(false);
      if (focusedNode?.isConnected) focusedNode.focus?.({preventScroll:true});
      else if (focused && document.getElementById(focused)) document.getElementById(focused).focus({preventScroll:true});
      scrollTo(0, y);
      document.querySelector('#update-indicator').textContent = 'Updated';
    } catch (_) {
      document.querySelector('#update-indicator').textContent = 'Updates paused · reconnecting';
    } finally {
      busy = false;
      if (root.querySelector('#history')?.open && !historyLoaded) loadHistory();
    }
  }
  interactiveVisuals();
  showAnchor();
  addEventListener('hashchange', showAnchor);
  if (live) {
    refresh();
    setInterval(refresh, 3000);
    addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  }
})();
