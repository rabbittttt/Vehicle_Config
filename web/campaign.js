'use strict';

const campaignEditor = (() => {
  const summaryName = '车型汇总';
  const dailyName = '小订by天';
  let saved, current;
  let edits = [], undo = [], redo = [];
  let progressName = '小订进度', section = 1, lastModel = '';

  const ready = () => Boolean(current?.available);
  const isDirty = () => edits.length > 0;
  const canUndoImport = () => Boolean(saved?.canUndoImport && !isDirty());
  const sheet = name => current?.sheets.find(item => item.name === name);
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
    accept(await api('campaign/save',payload()));
    notice('小订与首销草稿已保存');
  }
  function previewPublish() {
    const counts = new Map();
    for (const item of edits) counts.set(item.sheet,(counts.get(item.sheet)||0)+1);
    const list = [...counts].map(([name,count]) => `<dt>${esc(name)}</dt><dd>${count} 处编辑</dd>`).join('');
    const description = '<p>同时发布本次草稿中的车型资料、发布日程和填报数据。</p>' + (list ? `<dl>${list}</dl>` : '');
    modal('发布车型日程与填报数据', description, async () => {
      accept(await api('campaign/publish',payload()));
      notice(current.projectionPending ? current.projectionError || '数据已发布，下游同步待重试' : '小订与首销数据已发布', Boolean(current.projectionPending));
    },'确认发布');
  }
  const action = (kind,label,attrs='') => `<button type="button" data-campaign-action="${kind}" ${attrs}>${label}</button>`;
  const empty = message => `<div class="empty"><strong>${esc(message)}</strong></div>`;
  const dateText = value => String(value ?? '').slice(0,10);
  const displayValue = value => value === null || value === undefined || value === '' ? '—' : String(value);
  const lower = text => String(text ?? '').toLocaleLowerCase();
  const matched = (values,query) => !query || values.some(value => lower(value).includes(lower(query)));
  const numericText = value => typeof value === 'number' ? value.toLocaleString('zh-CN',{maximumFractionDigits:6}) : displayValue(value);
  const percentage = value => typeof value === 'number' ? Number((value*100).toFixed(6))+'%' : displayValue(value);
  const used = value => value !== '' && value !== null && value !== undefined;
  function table(headings,body) {
    return `<div class="scroll"><table class="campaign-table"><thead><tr>${headings.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
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
  function choices() {
    return stages().map(r => ({model:r[0], secondary:r[1] || '', label:r[1] ? r[0]+' · '+r[1] : r[0]}));
  }
  function stageSelect() {
    const variants = choices();
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
    ['小订与转化',[15,16,17,21,22,23,24]],
    ['大定与留存',[18,19,20,25,26]],
    ['锁单',[27,28,29]],
  ];
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
  function rowAction(kind,row) {
    return `<td class="operation">${action(kind,readonly?'查看':'编辑',`class="link" data-row="${row}"`)}</td>`;
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
  function renderTotals(query) {
    $('#actions').innerHTML = !readonly && !stages().length ? action('new-stage','新增发布日程') : '';
    const cols = [15,16,18,19,25,27];
    const body = pageRows(summaryRows(query,[0,1,...cols])).map(({r,row})=>`<tr>${identityCell(r)}
      ${cols.map(index=>`<td>${esc(numericText(r[index]))}</td>`).join('')}${rowAction('totals',row)}</tr>`).join('');
    const labels=sheet(summaryName).rows[0];
    $('#content').innerHTML = body ? table(['车型与阶段',...cols.map(index=>labels[index]),'操作'],body) : empty(query?'没有匹配的车型':'暂无经营汇总');
  }
  const dailyEntriesBySheet = new WeakMap();
  function dailyEntries() {
    const info = sheet(dailyName);
    const cached = dailyEntriesBySheet.get(info);
    if (cached) return cached;
    const headers = new Set(info.headerRows || [1]);
    const result = [];
    let header = [];
    info.rows.forEach((row,i)=>{
      if (headers.has(i+1)) { header = row; return; }
      if (!row[0] || /^(合计|总计)$/.test(row[0])) return;
      const secondary = row[header.indexOf('二级代际名')];
      const model = secondary ? row[0]+' · '+secondary : row[0];
      const modelSearch = lower(model);
      row.forEach((value,index) => {
        const stamp = dateText(header[index]);
        if (index && used(value) && /^\d{4}-\d{2}-\d{2}$/.test(stamp)) result.push({date:stamp,model,modelSearch,value,row:i+1,col:index+1});
      });
    });
    result.sort((a,b)=>b.date.localeCompare(a.date) || a.row-b.row);
    dailyEntriesBySheet.set(info,result);
    return result;
  }
  function renderDaily(query) {
    const available = stages().length;
    $('#actions').innerHTML = readonly ? '' : available ? action('batch','批量粘贴')+action('daily','填小订') : action('new-stage','新增发布日程');
    const entries = dailyEntries();
    const term = lower(query);
    const rows = term ? entries.filter(r=>r.date.includes(term) || r.modelSearch.includes(term)) : entries;
    const body = pageRows(rows).map(r=>`<tr><td>${esc(r.date)}</td><td>${esc(r.model)}</td><td>${esc(numericText(r.value))}</td>${readonly?'':`<td class="operation">${action('edit-daily','编辑',`class="link" data-row="${r.row}" data-col="${r.col}"`)}</td>`}</tr>`).join('');
    $('#content').innerHTML = body ? table(['日期','车型与阶段','小订量',...(readonly?[]:['操作'])],body) : empty(query?'没有匹配的记录':'暂无每日小订');
  }
  const progressRatios = {
    '小订进度':'当日小订 / 首日小订','小订期退订进度':'累计退订 / 总小订',
    '小转大进度':'累计小转大 / 总小转大','直接大定进度':'累计直接大定 / 总直接大定',
    '大定进度':'累计大定 / 总大定','首销期退订进度':'累计退订 / 总小订',
  };
  const blockTitle = n => n===1?'数量':n===2?progressRatios[progressName]:'当日直接大定 / 当日大定';
  function progressBlock() {
    const s = sheet(progressName);
    const starts = s.headerRows || [1];
    if (section > starts.length) section = 1;
    const start = starts[section-1]-1;
    const end = starts[section] ? starts[section]-1 : s.rows.length;
    const labels = s.rows[start];
    const days = labels.map((name,i)=>/^D\d+$/i.test(name)?i:-1).filter(i=>i>=0);
    return {s,starts,start,end,labels,days};
  }
  function renderProgress(query) {
    const p = progressBlock();
    const select = (id,label,entries) => `<label class="inline-label">${label}<select aria-label="${label}" id="${id}">${entries}</select></label>`;
    const options = current.sheets.slice(2).map(s=>option(s.name,s.name,progressName)).join('');
    const blocks = p.starts.map((_,i)=>option(String(i+1),blockTitle(i+1),String(section))).join('');
    $('#actions').innerHTML = select('campaign-progress','指标',options)+(p.starts.length>1?select('campaign-section','分组',blocks):'')+
      (readonly?'':stages().length ? action('batch','批量粘贴')+action('progress','填进度') : action('new-stage','新增发布日程'));
    $('#campaign-progress').onchange = e=>{progressName=e.target.value;section=1;page=1;render();};
    const selector = $('#campaign-section');
    if(selector)selector.onchange = e=>{section=Number(e.target.value);page=1;render();};
    const secondaryCol = p.labels.indexOf('二级代际名');
    const entries = p.s.rows.map((r,i)=>({r,i})).slice(p.start+1,p.end).filter(({r})=>r[0] && matched([r[0],r[secondaryCol]],query));
    const body = pageRows(entries).map(({r,i})=>{
      const filled = p.days.filter(c=>used(r[c]));
      const last = filled.at(-1);
      return `<tr><td>${esc(r[0])}${r[secondaryCol]?`<small>${esc(r[secondaryCol])}</small>`:''}</td><td>${last===undefined?'—':esc(p.labels[last])}</td><td>${esc(section===1?numericText(r[last]):percentage(r[last]))}</td><td>${filled.length}</td><td>${action('progress-row','明细',`class="link" data-row="${i+1}"`)}</td></tr>`;
    }).join('');
    $('#content').innerHTML = body ? table(['车型与阶段','最近日序','最近数值','已填天数','操作'],body) : empty(query?'没有匹配的进度':'暂无'+progressName);
  }
  function renderView(view,query) {
    if (!ready()) {
      $('#count').textContent = '';
      $('#pagination').innerHTML = $('#actions').innerHTML = '';
      $('#content').innerHTML = empty(current?.reason || '正在读取小订与首销…');
      return;
    }
    if (view==='dailyOrders') renderDaily(query);
    else if (view==='stageProgress') renderProgress(query);
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
      const percent = /率|占比/.test(field) || (section>1 && /^D\d+$/.test(field) && info.name!==summaryName);
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
  function dailyForm(item) {
    const now = new Date();
    const today = [now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
    modal(item?'编辑每日小订':'填小订',`<form class="form campaign-form">
      ${item?`<label class="wide">车型与阶段<output>${esc(item.model)}</output></label>`:stageSelect()}
      <label>日期<input name="date" type="date" value="${item?esc(item.date):today}" ${item?'readonly':''} required></label>
      <label>小订量<input name="amount" type="number" min="0" step="1" value="${esc(item?.value)}" required></label></form>`,async values=>{
      const value = Number(values.get('amount'));
      if (item) return apply([{sheet:dailyName,row:item.row,col:item.col,value}]);
      lastModel=values.get('stage');
      const [model,secondary]=JSON.parse(lastModel);
      await apply([{sheet:dailyName,model,secondary,date:values.get('date'),value}]);
    },'应用');
    if(!item) {
      const form=$('#dialog-body form');
      const info=sheet(dailyName);
      const knownAmount=()=>{
        if(!form.elements.stage.value)return;
        const [model,secondary]=JSON.parse(form.elements.stage.value);
        let header=[], found;
        const heads=new Set(info.headerRows);
        info.rows.some((row,index)=>{
          if(heads.has(index+1)){header=row;return false;}
          if(row[0]!==model || (row[header.indexOf('二级代际名')]||'')!==secondary)return false;
          const col=header.indexOf(form.elements.date.value);
          if(col<0 || !used(row[col]))return false;
          found=row[col];
          return true;
        });
        form.elements.amount.value=found ?? '';
      };
      form.elements.stage.onchange=form.elements.date.onchange=knownAmount;
      knownAmount();
    }
    bindSubmit();
  }
  function entryForm(batch) {
    const daily = view==='dailyOrders';
    const ratio = !daily && section>1;
    const title = daily?'每日小订':progressName+' · '+blockTitle(section);
    const column = ratio?'百分比（%）':'数量';
    const inputs = batch ? `<label class="wide">${daily?'日期':'日序'}、${column}（两列，每行一条）<textarea name="rows" rows="7" placeholder="${daily?'2026-01-01':'D1'}&#9;${ratio?'12.5%':'120'}" required></textarea></label>` :
      `<label>日序<input type="number" name="day" min="1" step="1" required></label><label>${column}<input name="amount" type="number" min="0" step="${ratio?'any':'1'}" required></label>`;
    modal((batch?'批量粘贴':'填进度')+' · '+title,`<form class="form">${stageSelect()}${inputs}</form>`,async values=>{
      lastModel=values.get('stage');
      const [model,secondary]=JSON.parse(lastModel);
      const rows=batch?String(values.get('rows')).trim().split(/\r?\n/).map(line=>line.trim().split('\t')):[[values.get('day'),values.get('amount')]];
      if (rows.length>1000 || rows.some(r=>r.length!==2 || !r[1].trim())) throw Error('一次可粘贴 1–1000 行，每行须有两列');
      const seen=new Set();
      const mapped=rows.map(([rawKey,rawValue],index)=>{
        const key=rawKey.trim();
        const value=numericValue(rawValue,ratio);
        if (!ratio && !Number.isSafeInteger(value)) throw Error(`第 ${index+1} 行须填写整数数量`);
        let patch;
        if (daily) {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) throw Error(`第 ${index+1} 行日期须为 YYYY-MM-DD`);
          patch={sheet:dailyName,model,secondary,date:key,value};
        } else {
          const day=Number(key.replace(/^D/i,''));
          if (!Number.isInteger(day)||day<1) throw Error(`第 ${index+1} 行日序须为正整数`);
          patch={sheet:progressName,model,secondary,section,day,value};
        }
        const id=patch.date || patch.day;
        if(seen.has(id))throw Error(`第 ${index+1} 行的日期或日序重复`);
        seen.add(id);
        return patch;
      });
      await apply(mapped);
    },'应用');
    if(!batch) {
      const form=$('#dialog-body form');
      const block=progressBlock();
      const findIndex=()=>{
        if(!form.elements.stage.value)return -1;
        const [model,secondary]=JSON.parse(form.elements.stage.value);
        const secondaryCol=block.labels.indexOf('二级代际名');
        return block.s.rows.findIndex((r,i)=>i>block.start && i<block.end && r[0]===model && (r[secondaryCol]||'')===secondary);
      };
      const derived = new Set(block.s.derivedCells || []);
      const syncAmount=()=>{
        const index=findIndex(),col=block.labels.indexOf('D'+form.elements.day.value);
        const value=block.s.rows[index]?.[col];
        const locked=derived.has((index+1)+':'+(col+1));
        form.elements.amount.value=used(value)?ratio?Number((value*100).toFixed(10)):value:'';
        form.elements.amount.readOnly=locked;
        $('#confirm').disabled=locked;
        form.elements.amount.labels[0].firstChild.textContent=locked?'自动计算（%）':column;
      };
      form.elements.stage.onchange=()=>{
        const row=block.s.rows[findIndex()] || [],latest=block.days.filter(i=>used(row[i])).at(-1);
        form.elements.day.value=latest===undefined?1:Number(block.labels[latest].slice(1))+1;
        syncAmount();
      };
      form.elements.day.onchange=syncAmount;
      form.elements.stage.onchange();
    }
    bindSubmit();
  }
  function handle(button) {
    const kind=button.dataset.campaignAction;
    if(!ready())return;
    const row=Number(button.dataset.row),col=Number(button.dataset.col);
    if(kind==='progress-row'){
      const p=progressBlock();
      const record=p.s.rows[row-1];
      const subject=stageLabel(record[0],record[p.labels.indexOf('二级代际名')]);
      return rowForm(p.s,row,p.labels.map((_,i)=>i).filter(i=>i!==0),subject+' · '+progressName+' · '+blockTitle(section));
    }
    if(['identity','stage','totals'].includes(kind)){
      const s=sheet(summaryName);
      const record=s.rows[row-1];
      const subject=stageLabel(record[0],record[1]);
      if(kind==='identity')return rowForm(s,row,identityColumns,subject+' · 车型资料');
      if(kind==='stage')return rowForm(s,row,scheduleColumns,subject+' · 发布日程',scheduleGroups);
      const metrics=s.rows[0].map((_,i)=>i).slice(15);
      return rowForm(s,row,metrics,subject+' · 经营汇总',totalsGroups);
    }
    if(readonly)return;
    if(kind==='new-stage') switchView('schedule','launchSchedule');
    if(kind==='new-stage'||kind==='add-stage') {
      const s=sheet(summaryName);
      if(view==='vehicleIdentity')return rowForm(s,s.rows.length+1,identityColumns,'新增车型');
      return rowForm(s,s.rows.length+1,[...identityColumns,...scheduleColumns],'新增发布日程',[['车型资料',identityColumns],...scheduleGroups]);
    }
    if(kind==='batch'||kind==='progress') return entryForm(kind==='batch');
    if(kind==='daily')return dailyForm();
    if(kind==='edit-daily')return dailyForm(dailyEntries().find(item=>item.row===row&&item.col===col));
  }
  async function openExport() {
    if(!ready())return;
    const reporting=isReportingView();
    const check=(name,value,label)=>`<label><input type="checkbox" name="${name}" value="${esc(value)}" ${name==='sheets'?'checked':''}>${esc(label)}</label>`;
    const sheetList=current.sheets.map(s=>check('sheets',s.name,s.name)).join('');
    const publishedData=saved.publishedRevision?await api('campaign/published'):null;
    const modelBoxes=dataset=>{
      const rows=dataset.sheets.find(s=>s.name===summaryName).rows.slice(1);
      return [...new Set(rows.filter(r=>r[0]).map(r=>reporting ? r[1]||r[0] : r[0]))].map(m=>check('models',m,m)).join('');
    };
    const models=modelBoxes(publishedData||saved);
    const sheetOptions=reporting ? `<fieldset><legend>工作表</legend><div class="members">${sheetList}</div></fieldset>
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
    const dailyBox=$('[name="sheets"][value="小订by天"]');
    if(dailyBox)dailyBox.onchange=()=>{$('.campaign-window').hidden=!dailyBox.checked;};
    $('[name="source"]').onchange=e=>{
      const area=$('.campaign-export-models');
      const names=modelBoxes(e.target.value==='published'?publishedData:saved);
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
    modal('旧表导入预览',`<div class="file-list"><span>${esc(file.name)}</span></div><p>${description}</p>${comparison}
      <div class="campaign-import-stages">${stageList('新增车型阶段',changes.addedStages)}${stageList('移出车型阶段',changes.removedStages)}</div>`,
      result.identical ? null : async()=>{
        const imported=await api('campaign/import',{revision:result.revision,content,confirmReplace:true});
        accept(imported); notice('旧表已导入小订与首销草稿');
      }, result.requiresConfirmation?'替换草稿':'导入草稿');
    if(result.identical)$('#cancel').textContent='关闭';
  }
  function undoImport() {
    if (readonly || busy || !canUndoImport()) return;
    const revision=saved.revision;
    modal('撤销旧表导入','<p>恢复导入前的小订与首销草稿。</p>',async()=>{
      accept(await api('campaign/undo-import',{revision}));
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
