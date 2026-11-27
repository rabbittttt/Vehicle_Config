'use strict';

const campaignEditor = (() => {
  const summaryName = '车型汇总';
  const dailyName = '小订by天';
  let saved, current;
  let edits = [], undo = [], redo = [];
  let progressName = dailyName, lastModel = '';

  const ready = () => Boolean(current?.available);
  const isDirty = () => edits.length > 0;
  const canUndoImport = () => Boolean(saved?.canUndoImport && !isDirty());
  const sheet = name => current?.sheets.find(item => item.name === name);
  const reportingSheets = () => current.sheets.filter(info=>![summaryName,'小订进度','大定进度'].includes(info.name));
  const payload = () => ({revision:saved.revision, edits});

  function accept(result) {
    saved = current = result;
    edits = []; undo = []; redo = [];
    if (isCampaignView()) renderKeepingScroll();
    else flags();
  }
  async function load() { accept(await api('campaign/state')); }
  function reload() {
    if (isDirty()) modal('放弃未保存的小订与首销修改？','<p>将重新读取已保存的草稿。</p>', load, '重新载入草稿');
    else run(load);
  }
  async function apply(changes) {
    if (!changes.length) return;
    const next = edits.concat(changes);
    const result = await api('campaign/preview',{revision:saved.revision,edits:next});
    undo.push(edits.length);
    if (undo.length > 30) undo.shift();
    redo = [];
    edits = next;
    current = result;
    renderKeepingScroll();
  }
  function step(back) {
    if (busy || readonly || (back ? !undo.length : !redo.length)) return;
    run(async () => {
      const next = back ? edits.slice(0,undo.at(-1)) : edits.concat(redo.at(-1));
      const result = next.length ? await api('campaign/preview',{revision:saved.revision,edits:next}) : saved;
      if (back) redo.push(edits.slice(undo.pop()));
      else { undo.push(edits.length); redo.pop(); }
      edits = next; current = result; renderKeepingScroll();
    });
  }
  async function save() {
    accept(await writeApi('campaign/save',payload()));
    notice('小订与首销草稿已保存');
  }
  function previewPublish() {
    const counts = new Map();
    for (const item of edits) counts.set(item.sheet,(counts.get(item.sheet)||0)+1);
    const list = [...counts].map(([name,count]) => `<dt>${esc(name)}</dt><dd>${count} 处编辑</dd>`).join('');
    const description = '<p>同时发布本次草稿中的车型资料、发布日程和填报数据。</p>' + (list ? `<dl>${list}</dl>` : '');
    writeModal('发布车型日程与填报数据', description, async () => {
      accept(await writeApi('campaign/publish',payload()));
      notice(current.projectionPending ? current.projectionError || '数据已发布，下游同步待重试' : '小订与首销数据已发布', Boolean(current.projectionPending));
    },'确认发布');
  }
  const action = (kind,label,attrs='') => `<button type="button" data-campaign-action="${kind}" ${attrs}>${label}</button>`;
  const empty = message => `<div class="empty"><strong>${esc(message)}</strong></div>`;
  const dateText = value => String(value ?? '').slice(0,10);
  const displayValue = value => value === null || value === undefined || value === '' ? '—' : String(value);
  const lower = text => String(text ?? '').toLocaleLowerCase();
  const matched = (values,query) => !query || values.some(value => lower(value).includes(lower(query)));
  const numbers = new Intl.NumberFormat('zh-CN',{maximumFractionDigits:6});
  const numericText = value => typeof value === 'number' ? numbers.format(value) : displayValue(value);
  const percentage = value => typeof value === 'number' ? Number((value*100).toFixed(6))+'%' : displayValue(value);
  const used = value => value !== '' && value !== null && value !== undefined;
  function table(headings,body) {
    return `<div class="scroll"><table class="campaign-table"><thead><tr>${headings.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function renderModelTable(headings,body,modelCount,extraClass='') {
    const head=`<th scope="col">车型<span class="report-count">（${modelCount}）</span></th>`+
      headings.map(h=>`<th scope="col">${esc(h)}</th>`).join('');
    $('#content').innerHTML=`<div class="report-matrix scroll ${extraClass}"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    wireReportStages();
  }
  function pageRows(rows) {
    const interval = paginator(rows.length);
    $('#count').textContent = rows.length ? `${rows.length} 条记录` : '';
    return rows.slice(...interval);
  }
  function stages() {
    return (sheet(summaryName)?.rows.slice(1) || []).filter(r => r?.[0]);
  }
  const modelNames = () => [...new Set(stages().map(row=>row[0]))];
  const modelYears = () => [...new Set(stages().flatMap(row=>[modelYear(row[0]),modelYear(row[1])]).filter(Boolean))].sort().reverse();
  function stageSelect(target) {
    if (target) {
      const key = JSON.stringify([target.model,target.secondary]);
      return `<input type="hidden" name="stage" value="${esc(key)}"><label class="wide">车型与阶段<output>${esc(stageLabel(target.model,target.secondary))}</output></label>`;
    }
    const variants = reportStages().filter(stage=>stage.hasSummary);
    const pairs = variants.map(v => [JSON.stringify([v.model,v.secondary]),v.label]);
    const initial = pairs.some(([key])=>key===lastModel) ? lastModel : pairs.length===1 ? pairs[0][0] : '';
    return `<label class="wide">车型与阶段${formSelect('stage', pairs, initial, true)}</label>`;
  }
  const identityColumns = [0,1,2,3];
  const scheduleColumns = [4,5,6,7,8,9,10,11,12,13];
  const scheduleGroups = [
    ['发布信息',[4,7]], ['小订期',[5,8,9,12]], ['首销期',[6,10,11,13]],
  ];
  const totalsGroups = [
    ['小订与转化',[15,16,21]],
    ['大定与留存',[18,19,25]],
    ['锁单',[27]],
  ];
  const totalColumns = [15,16,18,19,21,25,27];
  // Zero-based summary columns: ratio, numerator, denominator.
  const reviewRates = [[17,16,15],[20,19,18],[22,21,15],[26,25,15],[28,27,25],[29,27,18]];
  function summaryRows(query, columns) {
    const info = sheet(summaryName);
    return info.rows.map((r,i)=>({r,row:i+1})).slice(1)
      .filter(({r})=>r[0] && (isReportingView() || matchesScheduleModel(r[0],r[1])) && matched(columns.map(index=>r[index]),query));
  }
  function identityCell(r) {
    return `<td>${esc(r[0])}${r[1] ? `<small>${esc(r[1])}</small>` : ''}</td>`;
  }
  function stageLabel(primary,secondary) {
    return secondary && !String(secondary).startsWith(primary) ? primary+' · '+secondary : secondary || primary;
  }
  function rowAction(kind,row,caption=readonly?'查看':'编辑') {
    return `<td class="operation">${action(kind,caption,`class="link" data-row="${row}"`)}</td>`;
  }
  function renderIdentity(query) {
    $('#actions').innerHTML = readonly ? '' : action('add-stage','新增车型');
    const headings = ['代际名','二级代际名','产品档位','能源类型','操作'];
    const details = identityColumns.slice(1);
    const body = pageRows(summaryRows(query,identityColumns)).map(({r,row}) => {
      const metadata = details.filter(index=>used(r[index])).map(index=>
        `<div><dt>${headings[index]}</dt><dd>${esc(r[index])}</dd></div>`).join('');
      return `<tr><td><span class="identity-name">${esc(r[0])}</span>${metadata ? `<dl class="identity-details">${metadata}</dl>` : ''}</td>
        ${details.map(index=>`<td>${esc(displayValue(r[index]))}</td>`).join('')}${rowAction('identity',row)}</tr>`;
    }).join('');
    $('#content').innerHTML = body ? `<div class="campaign-identities">${table(headings,body)}</div>` : empty(query?'没有匹配的车型':'暂无车型资料');
  }
  function timeWindow(r,start,end) {
    if (!used(r[start]) && !used(r[end])) return '<td>—</td>';
    return `<td>${esc(displayValue(r[start]))}<small>至 ${esc(displayValue(r[end]))}</small></td>`;
  }
  function renderSchedule(query) {
    $('#actions').innerHTML = readonly ? '' : action('add-stage','新增发布日程');
    const body = pageRows(summaryRows(query,[0,1,...scheduleColumns])).map(({r,row})=>`<tr>
      ${identityCell(r)}<td>${esc(displayValue(r[4]))}</td><td>${esc(displayValue(r[7]))}</td>
      <td>${esc(displayValue(r[5]))}</td>${timeWindow(r,8,9)}<td>${esc(displayValue(r[12]))}</td>
      <td>${esc(displayValue(r[6]))}</td>${timeWindow(r,10,11)}<td>${esc(displayValue(r[13]))}</td>${rowAction('stage',row)}</tr>`).join('');
    $('#content').innerHTML = body ? table(['车型与阶段','发布类型','留资日期','小订发布时间','小订日期','小订天数','首销发布时段','首销日期','首销期天数','操作'],body) : empty(query?'没有匹配的日程':'暂无发布日程');
  }
  function grossDifference(small,direct,gross) {
    if (![small,direct,gross].every(value=>Number.isFinite(value) && value>=0)) return '';
    const difference=gross-(small+direct);
    return difference ? `差额 ${difference>0?'+':'−'}${numericText(Math.abs(difference))}` : '';
  }
  function grossDifferenceDetail(small,direct,gross) {
    const difference=grossDifference(small,direct,gross);
    return difference ? `小转大与直接大定合计 ${numericText(small+direct)}，实绩 ${numericText(gross)}，${difference}` : '';
  }
  function renderTotals(query) {
    $('#actions').innerHTML = !readonly && !stages().length ? action('new-stage','新增发布日程') : '';
    const labels=sheet(summaryName).rows[0];
    const models=new Map();
    for(const {r,row} of summaryRows(query,[0,1,...totalColumns])) {
      const model=r[0], secondary=r[1] || '';
      if(!models.has(model))models.set(model,[]);
      models.get(model).push({model,secondary,r,row});
    }
    const [first,last]=paginator(models.size);
    $('#count').textContent='';
    const body=[...models.values()].slice(first,last).map(variants=>{
      const stage=activeVariant(variants,query);
      const {r,row}=stage;
      const difference=grossDifference(r[16],r[19],r[18]);
      const cells=totalColumns.map(index=>`<td>${esc(numericText(r[index]))}${index===18 && difference ? `<small class="campaign-discrepancy">${esc(difference)}</small>` : ''}</td>`).join('');
      return `<tr data-model="${esc(stage.model)}">${reportIdentity(variants,stage)}${cells}<td class="report-actions">${action('totals','核对',`class="link" data-row="${row}"`)}</td></tr>`;
    }).join('');
    if(body)renderModelTable([...totalColumns.map(index=>labels[index]),'操作'],body,models.size,'campaign-totals');
    else $('#content').innerHTML=empty(query?'没有匹配的车型':'暂无可核对的车型');
  }
  function reviewTotals(info,row,subject) {
    rowForm(info,row,totalColumns,subject+' · 结果核对',totalsGroups);
    const form=$('#dialog-body form');
    const labels=info.rows[0], record=info.rows[row-1];
    const historical=new Set(info.literalCells || []);
    for(const input of form.querySelectorAll('input[type="number"]')) input.step='1';
    const grossControl=form.elements.f18 || form.querySelector('output[aria-label="大定量"]');
    const grossLabel=grossControl.closest('label');
    const grossField=document.createElement('div');
    grossLabel.before(grossField);
    grossField.append(grossLabel);
    grossField.insertAdjacentHTML('beforeend','<small class="campaign-discrepancy" id="review-gross-difference" aria-live="polite" hidden></small>');
    const difference=grossField.querySelector('small');
    grossControl.setAttribute('aria-describedby',difference.id);

    form.insertAdjacentHTML('beforeend','<details class="campaign-rates wide"><summary>查看比例</summary><dl></dl></details>');
    const details=form.querySelector('.campaign-rates');
    function quantity(index) {
      const input=form.elements['f'+index];
      if(!input) return record[index];
      return input.validity.valid && Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : null;
    }
    function renderRates() {
      details.querySelector('dl').innerHTML=reviewRates.map(([rate,numerator,denominator])=>{
        const top=quantity(numerator), bottom=quantity(denominator);
        const computed=Number.isFinite(top) && bottom>0 ? top/bottom : null;
        const stored=record[rate];
        // Imported exceptions retain their reported value when the quantities change.
        const independent=historical.has(row+':'+(rate+1)) && Number.isFinite(stored);
        const value=independent ? stored : computed;
        const formula=`${labels[numerator]} ${numericText(top)} ÷ ${labels[denominator]} ${numericText(bottom)}`;
        const note=independent ? `<small>历史值${computed!==null && percentage(computed)!==percentage(stored) ? '，按数量计算 '+esc(percentage(computed)) : ''}</small>` : '';
        return `<div><dt>${esc(labels[rate])}</dt><dd><output aria-label="${esc(labels[rate])}">${esc(percentage(value))}</output>${note}<small>${esc(formula)}</small></dd></div>`;
      }).join('');
    }
    function update() {
      const text=grossDifferenceDetail(quantity(16),quantity(19),quantity(18));
      difference.textContent=text;
      difference.hidden=!text;
      if(details.open)renderRates();
    }
    details.ontoggle=()=>{if(details.open)renderRates();};
    form.addEventListener('input',update);
    update();
  }
  const reportVariants = new Map();
  const timelinePositions = new Map();
  let reportAxis, indexedWorkbook, indexedReports, calendarTarget, dateSearchPosition, stopTimeline;
  function dateKey(value) {
    const stamp = dateText(value);
    return /^\d{4}-\d{2}-\d{2}$/.test(stamp) && Number.isFinite(Date.parse(stamp)) ? stamp : '';
  }
  function dayNumber(stamp) {
    return Date.parse(stamp) / 86400000;
  }
  function reportIndex() {
    if (indexedWorkbook === current) return indexedReports;
    const models = new Map(), dates = new Set(), metricDays = new Map(), dateStages = new Map();
    let lastDailyDay = 1;
    let hasDaily = false, hasAligned = false;
    function findStage(model, secondary) {
      if (!models.has(model)) models.set(model, new Map());
      const variants = models.get(model);
      if (!variants.has(secondary)) variants.set(secondary, {
        model, secondary, label:stageLabel(model,secondary), start:'', records:new Map(), observed:new Set(), lastDate:'',
      });
      return variants.get(secondary);
    }
    for (const row of stages()) {
      const stage = findStage(row[0], row[1] || '');
      // Duplicate summary identities do not unambiguously specify an anchor.
      stage.start = stage.hasSummary ? '' : dateKey(row[8]);
      stage.hasSummary = true;
      const end = dateKey(row[9]);
      if (stage.start && end) lastDailyDay = Math.max(lastDailyDay,dayNumber(end)-dayNumber(stage.start)+1);
      stage.openEnded = !end;
    }
    for (const info of reportingSheets()) {
      const daily = info.name === dailyName;
      const headers = info.headerRows || [1];
      // Later progress blocks contain rates, not the reported quantities.
      for (const [i, head] of (daily ? headers : headers.slice(0,1)).entries()) {
        const labels = info.rows[head-1];
        const secondaryColumn = labels.indexOf('二级代际名');
        const columns = new Map();
        labels.forEach((label, index) => {
          const axis = daily ? dateKey(label) : /^D[1-9]\d*$/.test(label) ? label : '';
          if (axis) columns.set(axis,index+1);
        });
        if (daily) for (const date of columns.keys()) dates.add(date);
        else metricDays.set(info.name,[...columns.keys()]);
        const end = (headers[i+1] || info.rows.length+1)-1;
        for (let r = head; r < end; r++) {
          const row = info.rows[r], model = row[0];
          if (!model || model === '合计' || model === '总计') continue;
          const stage = findStage(model, row[secondaryColumn] || '');
          if (!stage.records.has(info.name)) stage.records.set(info.name, []);
          stage.records.get(info.name).push({row:r+1, values:row, columns});
          for (const [key,col] of columns) {
            if (!used(row[col-1])) continue;
            stage.observed.add(info.name);
            if (!daily) continue;
            hasDaily = true;
            if (!dateStages.has(key)) dateStages.set(key,new Set());
            dateStages.get(key).add(stage);
            if (key > stage.lastDate) stage.lastDate = key;
            if (!stage.start || key < stage.start) continue;
            hasAligned = true;
            const day = dayNumber(key) - dayNumber(stage.start) + 1;
            lastDailyDay = Math.max(lastDailyDay,day+(stage.openEnded ? 1 : 0));
          }
        }
      }
    }
    const stageList = Array.from(models.values()).flatMap(variants=>Array.from(variants.values()));
    indexedWorkbook = current;
    indexedReports = {models,stageList,dates:[...dates].sort(),lastDailyDay,
      metricDays,dateStages,defaultAxis:hasDaily && !hasAligned ? 'date' : 'day'};
    return indexedReports;
  }
  function reportStages() {
    return reportIndex().stageList;
  }
  function dailyEntries(target) {
    const stage = reportIndex().models.get(target.model)?.get(target.secondary);
    const entries = [];
    for (const record of stage?.records.get(dailyName) || []) {
      if (target.row && record.row!==Number(target.row)) continue;
      for (const [date,col] of record.columns) {
        const value = record.values[col-1];
        if (used(value)) entries.push({date,col,row:record.row,value});
      }
    }
    return entries.sort((a,b)=>b.date.localeCompare(a.date) || a.row-b.row);
  }
  function progressBlock(name=progressName) {
    const s = sheet(name);
    const starts = s.headerRows || [1];
    const start = starts[0]-1;
    const end = starts[1] ? starts[1]-1 : s.rows.length;
    const labels = s.rows[start];
    const days = labels.map((name,i)=>/^D\d+$/i.test(name)?i:-1).filter(i=>i>=0);
    return {s,start,end,labels,days};
  }
  function activeVariant(variants,query='') {
    const matching = variants.filter(stage=>matched([stage.model,stage.secondary],query));
    const visible = matching.length ? matching : variants;
    return visible.find(stage=>stage.secondary === reportVariants.get(stage.model)) ||
      visible.find(stage=>stage.observed?.has(progressName)) || visible[0];
  }
  function reportIdentity(variants,stage,extra='') {
    const shortName = item => {
      const name = item.secondary;
      if (!name) return '未分阶段';
      return name.startsWith(item.model) ? name.slice(item.model.length).trim() || '本代际' : name;
    };
    let secondary = '';
    if (variants.length > 1) {
      const options = variants.map(item=>`<option value="${esc(item.secondary)}" title="${esc(item.secondary)}" ${item.secondary===stage.secondary?'selected':''}>${esc(shortName(item))}</option>`).join('');
      secondary = `<select data-report-stage aria-label="${esc(stage.model+' · 二级代际')}" title="${esc(stage.secondary)}">${options}</select>`;
    } else if (stage.secondary && stage.secondary !== stage.model) secondary = `<small title="${esc(stage.secondary)}">${esc(shortName(stage))}</small>`;
    return `<th scope="row" aria-colindex="1"><span class="report-name">${esc(stage.model)}</span>${secondary}${extra}</th>`;
  }
  function wireReportStages() {
    for (const select of $('#content').querySelectorAll('[data-report-stage]')) {
      select.onchange = () => {
        const model = select.closest('tr').dataset.model;
        reportVariants.set(model,select.value);
        renderKeepingScroll();
        $(`#content tr[data-model="${CSS.escape(model)}"] [data-report-stage]`)?.focus({preventScroll:true});
      };
    }
  }
  function searchDates(index,query) {
    if (progressName !== dailyName || !/^\d{4}(-\d{2}){0,2}$/.test(query)) return null;
    if (query.length===4 && index.stageList.some(stage=>(stage.hasSummary || stage.records.has(dailyName)) &&
      matched([stage.model,stage.secondary],query))) return null;
    return index.dates.filter(date=>date.startsWith(query) && index.dateStages.has(date));
  }
  function timelineAxis(index,axis,filteredDates) {
    if (axis === 'date') {
      let keys = filteredDates ?? index.dates;
      if (!keys.length && !filteredDates) {
        const today = new Date();
        keys = [new Date(today.getTime()-today.getTimezoneOffset()*60000).toISOString().slice(0,10)];
      }
      return {length:keys.length, key:i=>keys[i], index:key=>keys.indexOf(key)};
    }
    let days = index.lastDailyDay, extra = [];
    if (progressName !== dailyName) {
      const keys = index.metricDays.get(progressName) || [];
      // New progress columns follow the service limit. Keep imported sparse headers verbatim.
      days = Math.min(1004,Math.max(1,...keys.map(key=>Number(key.slice(1)))));
      extra = keys.filter(key=>Number(key.slice(1))>days).sort((a,b)=>a.length-b.length || a.localeCompare(b));
    }
    return {length:days+extra.length, key:i=>i<days ? 'D'+(i+1) : extra[i-days],
      index:key=>Number(key.slice(1))<=days ? Number(key.slice(1))-1 : days+extra.indexOf(key)};
  }
  function reportCell(stage,records,key,axis,colIndex) {
    let target = key;
    if (progressName === dailyName && axis === 'day') {
      const time = (dayNumber(stage.start) + Number(key.slice(1)) - 1) * 86400000;
      target = new Date(time).toISOString().split('T')[0];
      if (!dateKey(target)) return `<td aria-colindex="${colIndex}">—</td>`;
    }
    const matches = records.filter(record=>record.columns.has(target));
    // Disjoint blocks and repeated historical rows retain their physical coordinates.
    const buttons = (matches.length ? matches : [null]).map(record=>{
      const col = record?.columns.get(target);
      const value = record?.values[col-1];
      const text = esc(numericText(value));
      const cls = 'report-value'+(used(value) ? '' : ' missing');
      if (readonly || (!record && (!stage.hasSummary || records.length > 1))) return `<span class="${cls}">${text}</span>`;
      const attributes = `data-model="${esc(stage.model)}" data-secondary="${esc(stage.secondary)}" data-axis="${esc(target)}" data-key="${esc(key)}"`;
      const location = record ? `data-row="${record.row}" data-col="${col}"` : '';
      const label = [stage.label, key, target !== key ? target : '', matches.length > 1 ? '第 '+record.row+' 行' : '',
        used(value) ? numericText(value) : '未填'].filter(Boolean).join(' · ');
      return action('report-cell',text,`class="${cls}" ${attributes} ${location} aria-label="${esc(label)}" title="${esc(label)}"`);
    });
    return `<td aria-colindex="${colIndex}">${buttons.join(' ')}</td>`;
  }
  function showTimeline(groups,count,axis,times,query,filtered) {
    const positionKey = filtered ? '' : progressName+'|'+axis;
    const variants = groups.map(items=>activeVariant(items,query));
    const records = variants.map(stage=>stage.records.get(progressName) || []);
    const needsCalendar = stage=>progressName===dailyName && axis==='day' && !stage.start;
    const spacer = '<td class="report-spacer" aria-hidden="true"></td>';
    const rows = groups.map((group,i)=>{
      const stage = variants[i], entries = records[i];
      const attrs = `class="link" data-model="${esc(stage.model)}" data-secondary="${esc(stage.secondary)}"`;
      const actions = [];
      if (!readonly && stage.hasSummary && entries.length<=1) actions.push(action('report-add','填报',attrs));
      if (progressName!==dailyName) entries.forEach((entry,n)=>actions.push(action('report-detail',entries.length>1?'明细 '+(n+1):'明细',`${attrs} data-row="${entry.row}"`)));
      else if (entries.length) actions.push(action('report-detail','明细',attrs));
      const calendarLink = needsCalendar(stage) ? `<button type="button" class="link" data-report-calendar="${esc(stage.lastDate)}" aria-label="${esc(stage.label+' · 按日期查看')}">按日期查看</button>` : '';
      return `<tr data-model="${esc(stage.model)}">${reportIdentity(group,stage,calendarLink)}${spacer}${spacer}<td class="report-actions" aria-colindex="${times.length+2}">${actions.join(' ')}</td></tr>`;
    }).join('');
    const blankHead = '<th class="report-spacer" aria-hidden="true"></th>';
    $('#content').innerHTML = `<div class="report-matrix report-timeline scroll" data-axis="${axis}" tabindex="0" role="region" aria-label="填报时间表"><table aria-colcount="${times.length+2}"><colgroup></colgroup><thead><tr><th scope="col" aria-colindex="1">车型<span class="report-count">（${count}）</span></th>${blankHead}${blankHead}<th scope="col" aria-colindex="${times.length+2}">操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    wireReportStages();
    const grid = $('#content .report-timeline'), table = grid.firstElementChild;
    const columns = table.querySelector('colgroup');
    const bands = [...table.rows].map(row=>({before:row.cells[1], after:row.cells[2]}));
    let start=0, end=0, cellWidth=0, scale=1, track=0, available=0, modelWidth=0, actionsWidth=0, outerWidth, scrollFrame;
    const position = ()=>grid.scrollLeft/(cellWidth*scale);
    function paint(force=false) {
      const first = Math.min(times.length-1,Math.floor(position()));
      const limit = Math.min(times.length,first+Math.ceil(available/cellWidth)+1);
      if (!force && scale===1 && first>=start && limit<=end) return;
      const next = Math.max(0,first-4), until = Math.min(times.length,limit+4);
      const span = (until-next)*cellWidth;
      const leading = Math.max(0,Math.min(track-span,next*cellWidth+grid.scrollLeft*(1-1/scale)));
      const trailing = Math.max(0,track-leading-span);
      const widths=[modelWidth,leading,...Array(until-next).fill(cellWidth),trailing,actionsWidth];
      columns.replaceChildren(...widths.map(width=>{
        const col=document.createElement('col');
        col.style.width=width+'px';
        return col;
      }));
      // Keep the intersecting cells in place so a scroll preserves focus and existing buttons.
      const overlapStart = Math.min(end,Math.max(start,next));
      const overlapEnd = Math.max(overlapStart,Math.min(end,until));
      const focused=grid.contains(document.activeElement) ? document.activeElement : null;
      for (let r=0;r<bands.length;r++) {
        const {before,after} = bands[r];
        for(let i=start;i<overlapStart;i++) before.nextElementSibling.remove();
        for(let i=overlapEnd;i<end;i++) after.previousElementSibling.remove();
        const cells = (from,to)=>{
          let html='';
          for(let i=from;i<to;i++) {
            const key=times.key(i);
            if (!r) html+=`<th scope="col" aria-colindex="${i+2}">${esc(key)}</th>`;
            else html+=needsCalendar(variants[r-1]) ? `<td aria-colindex="${i+2}">—</td>` : reportCell(variants[r-1],records[r-1],key,axis,i+2);
          }
          return html;
        };
        if (overlapStart===overlapEnd) before.insertAdjacentHTML('afterend',cells(next,until));
        else {
          if (next<overlapStart) before.insertAdjacentHTML('afterend',cells(next,overlapStart));
          if (until>overlapEnd) after.insertAdjacentHTML('beforebegin',cells(overlapEnd,until));
        }
      }
      if (focused && !focused.isConnected) grid.focus({preventScroll:true});
      start=next; end=until;
    }
    function measure() {
      const width=grid.clientWidth;
      if (!width || width===outerWidth) return;
      const at=cellWidth ? position() : null;
      const css=getComputedStyle(grid);
      const px=(name,fallback)=>parseFloat(css.getPropertyValue(name)) || fallback;
      modelWidth=px('--report-model-width',180);
      actionsWidth=px('--report-actions-width',80);
      available=Math.max(1,width-modelWidth-actionsWidth);
      cellWidth=Math.max(px('--report-time-width',axis==='date'?98:68),available/times.length);
      // Date arithmetic can span centuries. Stay inside the browser's maximum layout width.
      const full=cellWidth*times.length;
      track=Math.min(full,16000000);
      scale=track===full ? 1 : (track-available)/(full-available);
      outerWidth=width;
      table.style.width=(modelWidth+actionsWidth+track)+'px';
      const initial=calendarTarget && axis==='date' ? times.index(calendarTarget) : null;
      const old=filtered && dateSearchPosition?.query===query ? dateSearchPosition.at : timelinePositions.get(positionKey);
      const target=at ?? (initial>=0 && initial!==null ? initial : old ?? (axis==='date' && !filtered ? times.length-1 : 0));
      grid.scrollLeft=target*cellWidth*scale;
      paint(true);
    }
    measure();
    calendarTarget=null;
    const observer = new ResizeObserver(()=>{
      if (grid.isConnected) measure();
      else dispose();
    });
    observer.observe(grid);
    grid.onscroll=()=>{
      if (positionKey) timelinePositions.set(positionKey,position());
      if (!scrollFrame) scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;paint();});
    };
    grid.onkeydown=event=>{
      const button=event.target.closest('[data-campaign-action="report-cell"]');
      if (!button || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const i=times.index(button.dataset.key), row=button.closest('tr');
      const next=event.key==='Home'?0:event.key==='End'?times.length-1:Math.max(0,Math.min(times.length-1,i+(event.key==='ArrowLeft'?-1:1)));
      const left=grid.scrollLeft/scale;
      if(next*cellWidth<left) grid.scrollLeft=next*cellWidth*scale;
      else if((next+1)*cellWidth>left+available) grid.scrollLeft=((next+1)*cellWidth-available)*scale;
      paint();
      const target=row.querySelector('[data-key="'+CSS.escape(times.key(next))+'"]');
      (target || grid).focus({preventScroll:true});
    };
    grid.onclick=event=>{
      const control=event.target.closest('[data-report-calendar]');
      if (!control || busy) return;
      reportAxis='date';
      calendarTarget=control.dataset.reportCalendar;
      render();
      $('#report-axis')?.focus({preventScroll:true});
    };
    function dispose() {
      if (grid.isConnected && cellWidth) {
        if (filtered) dateSearchPosition={query,at:position()};
        else timelinePositions.set(positionKey,position());
      }
      observer.disconnect();
      cancelAnimationFrame(scrollFrame);
      if (stopTimeline===dispose) stopTimeline=null;
    }
    stopTimeline=dispose;
  }
  function renderReport(query) {
    const index = reportIndex();
    const daily = progressName === dailyName;
    const filteredDates = searchDates(index,query);
    if (!filteredDates) dateSearchPosition=null;
    const axis = filteredDates ? 'date' : daily ? reportAxis || index.defaultAxis : 'day';
    const times = timelineAxis(index,axis,filteredDates);
    const select = (id,label,options,disabled=false)=>`<label class="inline-label">${label}<select id="${id}" aria-label="${label}" ${disabled?'disabled':''}>${options}</select></label>`;
    const metrics = reportingSheets().map(info=>option(info.name,info.name===dailyName?'每日小订':info.name,progressName));
    const toolbar = [select('campaign-progress','指标',metrics.join(''))];
    if (daily) toolbar.push(select('report-axis','横轴',option('day','按日序',axis)+option('date','按日期',axis),Boolean(filteredDates)));
    if (!readonly) toolbar.push(index.stageList.some(stage=>stage.hasSummary) ? action('batch','批量粘贴') : action('new-stage','新增发布日程'));
    $('#actions').innerHTML = toolbar.join('');
    const refresh = selector=>{ render(); $(selector)?.focus({preventScroll:true}); };
    $('#campaign-progress').onchange = e=>{progressName=e.target.value;page=1;refresh('#campaign-progress');};
    if (daily && !filteredDates) $('#report-axis').onchange = e=>{reportAxis=e.target.value;refresh('#report-axis');};
    const dateMatches = filteredDates && new Set(filteredDates.flatMap(key=>[...index.dateStages.get(key)]));
    const groups = Array.from(index.models.values(),variants=>[...variants.values()].filter(stage=>
      (stage.hasSummary || stage.records.has(progressName)) &&
      (dateMatches ? dateMatches.has(stage) : matched([stage.model,stage.secondary],query)))).filter(variants=>variants.length);
    const window = paginator(groups.length);
    $('#count').textContent = '';
    if (!groups.length) { $('#content').innerHTML = empty(filteredDates ? '该日期范围暂无小订记录' : query ? '没有匹配的车型' : '暂无车型'); return; }
    showTimeline(groups.slice(...window),groups.length,axis,times,query,Boolean(filteredDates));
  }
  function renderView(view,query) {
    stopTimeline?.();
    if (!ready()) {
      $('#count').textContent = '';
      $('#pagination').innerHTML = $('#actions').innerHTML = '';
      $('#content').innerHTML = empty(current?.reason || '正在读取小订与首销…');
      return;
    }
    if (view==='dailyOrders') renderReport(query);
    else if (view==='businessTotals') renderTotals(query);
    else if (view==='launchSchedule') renderSchedule(query);
    else renderIdentity(query);
  }

  function numericValue(value,percent=false) {
    const text = String(value).trim();
    if (!text) return null;
    const amount = Number(text.replace(/%$/,''));
    if (!Number.isFinite(amount) || amount<0) throw Error('数值须为有限的非负数');
    return percent || text.endsWith('%') ? amount/100 : amount;
  }
  function dateField(label) { return label==='留资' || String(label).includes('日期'); }
  function rowForm(info,row,indexes,title,groups=null) {
    const r = info.rows[row-1] || [];
    const head = (info.headerRows || [1]).filter(x=>x<=row).at(-1) || 1;
    const labels = info.rows[head-1];
    const changes = [];
    const derived = new Set(info.derivedCells || []);
    const scheduleReferences = info.name===summaryName ? [] : sheet(summaryName).rows[0].slice(0,14);
    function control(i) {
      const field = String(labels[i]);
      const coord = row+':'+(i+1);
      const computed = derived.has(coord) || (info.computed.includes(i+1) && !(info.literalCells||[]).includes(coord));
      const date = dateField(field);
      const number = !date && field!=='二级代际名' && ((info.name===summaryName && i>=12) || (info.name!==summaryName && i>0));
      const percent = /率|占比/.test(field);
      let val = r[i] ?? '';
      if (percent && typeof val === 'number') val = Number((val*100).toFixed(10));
      if (date) val = dateText(val);
      const unit = percent?'（%）':'';
      const label = field==='留资'?'留资日期':field;
      if (readonly || computed || scheduleReferences.includes(field)) return `<label>${esc(label)}${unit}<output aria-label="${esc(label)}${unit}">${esc(displayValue(val))}</output></label>`;
      const type = date?'date':number?'number':'text';
      const opts = number?'min="0" step="any"':'maxlength="256"';
      changes.push({i,number,percent,original:String(val)});
      return `<label>${esc(label)}${unit}<input name="f${i}" type="${type}" ${opts} value="${esc(val)}" ${i===0?'required list="campaign-models"':''}></label>`;
    }
    const renderFields = columns => columns.filter(i=>used(labels[i])).map(control).join('');
    const controls = groups ? groups.map(([name,columns])=>`<fieldset class="wide campaign-field-group"><legend>${esc(name)}</legend><div class="form">${renderFields(columns)}</div></fieldset>`).join('') : renderFields(indexes);
    const suggestions = indexes.includes(0) ? `<datalist id="campaign-models">${modelNames().map(name=>option(name,name,'')).join('')}</datalist>` : '';
    modal(title,`<form class="form campaign-form">${controls}${suggestions}</form>`, readonly?null:async values=>{
      const pending = [];
      for (const {i,number,percent,original} of changes) {
        const raw = String(values.get('f'+i)).trim();
        if (raw===original) continue;
        pending.push({sheet:info.name,row,col:i+1,value:number?numericValue(raw,percent):raw||null});
      }
      await apply(pending);
    },'应用');
    if(info.name===summaryName && row>info.rows.length) {
      const form=$('#dialog-body form');
      const manual=new Set();
      for(const i of [2,3]) form.elements['f'+i].oninput=()=>manual.add(i);
      form.elements.f0.oninput=()=>{
        const name=form.elements.f0.value.trim();
        const existing=stages().filter(r=>r[0]===name);
        const common=i=>{
          const values=[...new Set(existing.map(r=>r[i]).filter(used))];
          return values.length===1?values[0]:'';
        };
        for(const index of [2,3]) {
          if(!manual.has(index))form.elements['f'+index].value=common(index);
        }
      };
    }
    bindSubmit();
  }
  function bindSubmit() {
    initialForm=formSnapshot();
    const form = $('#dialog-body form');
    if (form) form.onsubmit = e=>{e.preventDefault();if (!readonly) $('#confirm').click();};
  }
  function entryForm(batch, target=null) {
    const daily = progressName===dailyName;
    const info = sheet(progressName);
    const title = daily?'每日小订':progressName;
    const amountName = daily?'小订量':'数量';
    const axis = target?.axis;
    const axisField = daily?'date':'day';
    const axisInput = daily ? '<label>日期<input type="date" name="date"' : '<label>日序<input type="number" name="day" min="1" step="1"';
    const inputs = batch ? `<label class="wide">${daily?'日期':'日序'}、数量（两列，每行一条）<textarea name="rows" rows="7" placeholder="${daily?'2026-01-01':'D1'}&#9;120" required></textarea></label>` :
      `${axisInput} ${axis?'readonly':''} required></label><label>${amountName}<input name="amount" type="number" min="0" step="1"></label>`;
    const block = daily ? null : progressBlock();
    let records = [], sourceCell = null;
    modal((batch?'批量粘贴':'填报')+' · '+title,`<form class="form campaign-form">${stageSelect(target)}${inputs}</form>`,async values=>{
      lastModel=values.get('stage');
      const [model,secondary]=JSON.parse(lastModel);
      const rows=batch?String(values.get('rows')).trim().split(/\r?\n/).map(line=>line.trim().split('\t')):[[values.get(axisField),values.get('amount')]];
      if (rows.length>1000 || rows.some(r=>r.length!==2 || (batch && !r[1].trim()))) throw Error('一次可粘贴 1–1000 行，每行须有两列');
      const seen=new Set();
      const mapped=rows.map(([rawKey,rawValue],index)=>{
        const key=rawKey.trim();
        const value=rawValue.trim()==='' ? null : Number(rawValue.trim());
        if (value!==null && (!Number.isSafeInteger(value) || value<0)) throw Error(`第 ${index+1} 行须填写非负整数数量`);
        let patch;
        if (daily) {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) throw Error(`第 ${index+1} 行日期须为 YYYY-MM-DD`);
          patch={sheet:dailyName,model,secondary,date:key,value};
        } else {
          const day=Number(key.replace(/^D/i,''));
          if (!Number.isInteger(day)||day<1) throw Error(`第 ${index+1} 行日序须为正整数`);
          patch={sheet:progressName,model,secondary,section:1,day,value};
        }
        const id=patch.date || patch.day;
        if(seen.has(id))throw Error(`第 ${index+1} 行的日期或日序重复`);
        seen.add(id);
        if (!batch && sourceCell) {
          if ((sourceCell.value ?? null)===value) return null;
          return {sheet:info.name,row:sourceCell.row,col:sourceCell.col,value};
        }
        return patch;
      });
      await apply(mapped.filter(Boolean));
    },'应用');
    const form=$('#dialog-body form');
    if (!batch) {
      const control=form.elements[axisField], amount=form.elements.amount;
      function syncAmount() {
        const label=daily?control.value:'D'+control.value;
        const found=[];
        for (const record of records) {
          const col=record.columns.get(label);
          if(col && (!target?.col || col===Number(target.col))) found.push({row:record.row,col,value:record.values[col-1]});
        }
        sourceCell=found.length===1?found[0]:null;
        amount.value=sourceCell?.value ?? '';
        amount.required=!used(sourceCell?.value);
      }
      function syncStage() {
        const selection=form.elements.stage.value;
        records=[];
        if (selection) {
          const [model,secondary]=JSON.parse(selection);
          const stage=reportIndex().models.get(model)?.get(secondary);
          records=stage?.records.get(info.name) || [];
          if(target?.row) records=records.filter(record=>record.row===Number(target.row));
        }
        if(axis) control.value=daily?axis:axis.slice(1);
        else if(daily) {
          const now=new Date();
          control.value=[now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
        } else {
          const record=records.length===1?records[0].values:[];
          const last=block.days.filter(i=>used(record[i])).at(-1);
          control.value=last===undefined?1:Number(block.labels[last].slice(1))+1;
        }
        syncAmount();
      }
      control.addEventListener('input',syncAmount);
      form.elements.stage.addEventListener('change',syncStage);
      syncStage();
      if (axis) { amount.focus();amount.select(); }
    }
    bindSubmit();
  }
  function dailyDetail(target) {
    const entries=dailyEntries(target);
    const title=stageLabel(target.model,target.secondary)+' · 每日小订';
    if (!entries.length) return modal(title,empty('暂无每日小订记录'));
    const fields=entries.map(({date,row,value},i)=>{
      const repeated=entries[i-1]?.date===date || entries[i+1]?.date===date;
      const source=repeated?'第 '+row+' 行':'';
      const dateCell=esc(date)+(repeated?`<small>${source}</small>`:'');
      const input=`<input type="number" name="n${i}" aria-label="${esc([date,source,'小订量'].filter(Boolean).join(' '))}" min="0" step="1" value="${esc(value)}">`;
      return `<tr><td>${dateCell}</td><td>${readonly?esc(numericText(value)):input}</td></tr>`;
    }).join('');
    modal(title,`<form class="campaign-form">${table(['日期','小订量'],fields)}</form>`,readonly?null:async values=>{
      const changes=[];
      entries.forEach(({row,col,value},i)=>{
        const raw=values.get('n'+i);
        const next=raw.trim()===''?null:Number(raw);
        if (next!==null && (!Number.isSafeInteger(next) || next<0)) throw Error('小订量须为非负整数');
        if (next!==value) changes.push({sheet:dailyName,row,col,value:next});
      });
      await apply(changes);
    },'应用');
    bindSubmit();
  }
  function handle(button) {
    const kind=button.dataset.campaignAction;
    if(!ready())return;
    const target=button.dataset;
    const row=Number(target.row);
    if(kind==='report-detail'){
      if(progressName===dailyName)return dailyDetail(target);
      if(!row)return modal(stageLabel(target.model,target.secondary)+' · '+progressName,empty('暂无进度记录'));
      const p=progressBlock();
      const record=p.s.rows[row-1];
      const secondary=record[p.labels.indexOf('二级代际名')] || '';
      const subject=stageLabel(record[0],secondary);
      const independentEnd=p.labels.indexOf('小转大结束日期');
      const columns=independentEnd<0?p.days:[independentEnd,...p.days];
      rowForm(p.s,row,columns,subject+' · '+progressName);
      return;
    }
    if(['identity','stage','totals'].includes(kind)){
      const s=sheet(summaryName);
      const record=s.rows[row-1];
      const subject=stageLabel(record[0],record[1]);
      if(kind==='identity')return rowForm(s,row,identityColumns,subject+' · 车型资料');
      if(kind==='stage')return rowForm(s,row,scheduleColumns,subject+' · 发布日程',scheduleGroups);
      return reviewTotals(s,row,subject);
    }
    if(readonly)return;
    if(kind==='new-stage') switchView('schedule','launchSchedule');
    if(kind==='new-stage'||kind==='add-stage') {
      const s=sheet(summaryName);
      if(view==='vehicleIdentity')return rowForm(s,s.rows.length+1,identityColumns,'新增车型');
      return rowForm(s,s.rows.length+1,[...identityColumns,...scheduleColumns],'新增发布日程',[['车型资料',identityColumns],...scheduleGroups]);
    }
    if(kind==='batch') return entryForm(true);
    if(kind==='report-cell'||kind==='report-add')return entryForm(false,target);
  }
  async function openExport() {
    if(!ready())return;
    const reporting=isReportingView();
    const check=(name,value,label)=>`<label><input type="checkbox" name="${name}" value="${esc(value)}" ${name==='sheets'?'checked':''}>${esc(label)}</label>`;
    const publishedData=saved.publishedRevision?await api('campaign/published'):null;
    const exportData=publishedData||saved;
    const sheetBoxes=dataset=>dataset.sheets.map(s=>check('sheets',s.name,s.name)).join('');
    const modelBoxes=dataset=>{
      const rows=dataset.sheets.find(s=>s.name===summaryName).rows.slice(1);
      return [...new Set(rows.filter(r=>r[0]).map(r=>reporting ? r[1]||r[0] : r[0]))].map(m=>check('models',m,m)).join('');
    };
    const models=modelBoxes(exportData);
    const sheetOptions=reporting ? `<fieldset><legend>工作表</legend><div class="members campaign-export-sheets">${sheetBoxes(exportData)}</div></fieldset>
      <fieldset class="campaign-window"><legend>每日小订日期范围</legend><div class="form">
        <label>开始日期<input type="date" name="dateFrom"></label><label>截止日期<input type="date" name="dateTo"></label></div></fieldset>` : '';
    modal(reporting?'导出小订与首销':'导出车型与日程',`<form class="campaign-export"><label>数据版本<select name="source">
      ${saved.publishedRevision ? '<option value="published">已发布数据</option>':''}<option value="draft">已保存草稿</option></select></label>
      ${sheetOptions}
      <details class="campaign-export-models" ${models?'':'hidden'}><summary>筛选车型（默认全部）</summary><div class="members">${models}</div></details></form>`,async values=>{
      const sheets=reporting?values.getAll('sheets'):[summaryName];
      if(!sheets.length)throw Error('请至少选择一个工作表');
      const selection={sheets,models:values.getAll('models'),source:values.get('source')};
      if(!reporting)selection.scope='schedule';
      if(sheets.includes(dailyName)) {
        const start=values.get('dateFrom'),end=values.get('dateTo');
        if(start && end && start>end)throw Error('每日小订截止日期不能早于开始日期');
        if(start)selection.dateFrom=start;
        if(end)selection.dateTo=end;
      }
      await download('campaign/export',selection,reporting?'小订与首销.xlsx':'车型与日程.xlsx');
    },'下载 Excel');
    const sheetArea=$('.campaign-export-sheets');
    const updateWindow=()=>{$('.campaign-window').hidden=!sheetArea.querySelector('[value="小订by天"]').checked;};
    if(sheetArea)sheetArea.onchange=updateWindow;
    $('[name="source"]').onchange=e=>{
      const area=$('.campaign-export-models');
      const selected=e.target.value==='published'?publishedData:saved;
      if(sheetArea) {
        const unchecked=new Set([...sheetArea.querySelectorAll('input:not(:checked)')].map(input=>input.value));
        sheetArea.innerHTML=sheetBoxes(selected);
        for(const input of sheetArea.querySelectorAll('input'))input.checked=!unchecked.has(input.value);
        updateWindow();
      }
      const names=modelBoxes(selected);
      area.querySelector('.members').innerHTML=names;
      area.hidden=!names;
    };
  }
  const saveBeforeImport = '车型日程或填报数据有未保存修改。请先保存草稿，再导入旧表。';
  function selectLegacy() {
    if (readonly || busy) return;
    if (isDirty()) {notice(saveBeforeImport,true);return;}
    $('#campaign-file').click();
  }
  function stageList(label, stages) {
    if (!stages.length) return '';
    const list = stages.map(s=>`<li>${esc([s.generation,s.subgeneration].filter(Boolean).join(' · '))}</li>`).join('');
    return `<details><summary>${label} · ${stages.length} 项</summary><ul>${list}</ul></details>`;
  }
  async function importFiles(files) {
    if (readonly) return;
    if (isDirty()) throw Error(saveBeforeImport);
    if (files.length!==1) throw Error('请选择一份旧工作簿');
    const file=files[0];
    if(file.size>8*1024*1024) throw Error('旧表不能超过 8 MB');
    // Use the current server revision. Other editors keep their own local input and undo stacks.
    const latest=await api('campaign/state');
    const content=await fileBase64(file);
    const result=await api('campaign/import-preview',{revision:latest.revision,content});
    const changes=result.changes;
    const rows=changes.tables.map(t=>`<tr><td>${esc(t.name)}</td><td>${esc(t.before)}</td><td>${esc(t.after)}</td><td>${t.changed?'有变更':'相同'}</td></tr>`).join('');
    const comparison=table(['工作表','现有草稿行数','旧表行数','内容'],rows);
    const description=result.identical?'旧表与当前草稿相同。':result.requiresConfirmation
      ? '确认后，整份小订与首销草稿将替换为旧表内容。核对后可另行发布。'
      : '旧表将导入为小订与首销草稿。';
    writeModal('旧表导入预览',`<div class="file-list"><span>${esc(file.name)}</span></div><p>${description}</p>${comparison}
      <div class="campaign-import-stages">${stageList('新增车型阶段',changes.addedStages)}${stageList('移出车型阶段',changes.removedStages)}</div>`,
      result.identical ? null : async()=>{
        const imported=await writeApi('campaign/import',{revision:result.revision,content,confirmReplace:true});
        accept(imported); notice('旧表已导入小订与首销草稿');
      }, result.requiresConfirmation?'替换草稿':'导入草稿');
    if(result.identical)$('#cancel').textContent='关闭';
  }
  function undoImport() {
    if (readonly || busy || !canUndoImport()) return;
    const revision=saved.revision;
    writeModal('撤销旧表导入','<p>恢复导入前的小订与首销草稿。</p>',async()=>{
      accept(await writeApi('campaign/undo-import',{revision}));
      notice('已恢复导入前的草稿');
    },'恢复草稿');
  }
  function status() {
    if (!ready()) return current?.reason || '小订与首销尚未载入';
    if (isDirty()) return '小订与首销有未保存修改';
    if (current.projectionPending) return '已发布 · 下游同步待重试';
    return `小订与首销草稿 r${saved.revision} · ${saved.hasUnpublishedChanges?'待发布':'已保存'}`;
  }
  return {ready,isDirty,modelNames,modelYears,canUndoImport,selectLegacy,undoImport,canPublish:()=>isDirty()||Boolean(saved?.hasUnpublishedChanges),
    canUndo:()=>undo.length>0,canRedo:()=>redo.length>0,undo:()=>step(true),redo:()=>step(false),
    render:renderView,load,reload,status,handle,save,previewPublish,openExport,importFiles};
})();
