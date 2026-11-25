'use strict';
if (window.frameElement?.matches('#frame-container > iframe[data-app="vehicle-config"]')) {
  document.documentElement.classList.add('planhub-embedded');
}

const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {models:'车型档案', phases:'发布阶段', versions:'版本', options:'配置项目', matrix:'配置适用关系', aliases:'名称映射', groups:'汇总规则', history:'变更记录'};
const sections = {
  models: ['models', 'phases'],
  matrix: ['matrix', 'versions', 'options', 'battery_rules', 'option_prices'],
  aliases: ['aliases', 'gen_names', 'option_names'],
  groups: ['groups', 'merge_groups'],
  history: ['history', 'rule_history'],
};
const viewLabels = {
  ...names, ...orderRules.titles,
  aliases: '业务名称映射', groups: '车型汇总组', history: '配置变更记录',
};
const isRuleView = () => Object.hasOwn(orderRules.titles, view);
const labels = {id:'编号',brand:'品牌',series:'车系',generation:'代际',subgeneration:'二级代际',year:'年款',energy:'能源类型',tier:'产品档位',status:'状态',model_id:'车型',name:'名称',presale_date:'小订日期',sale_date:'首销日期',power:'动力',battery:'电池包',category:'类别',source:'来源',target_type:'对象类型',target_id:'标准对象',scope:'生效范围',start_date:'生效日期',end_date:'截止日期',members:'成员',version_id:'版本',option_id:'配置项目',state:'适用状态'};
const fields = {models:['brand','series','generation','subgeneration','year','energy','tier','status'], phases:['model_id','name','presale_date','sale_date'], versions:['model_id','name','power','battery','status'], options:['model_id','category','name','status'], aliases:['source','name','target_type','target_id'], groups:['name','scope','start_date','end_date','members']};
let required, identityFields;
function newId() {
  if(crypto.randomUUID)return crypto.randomUUID();
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}
const states = ['未配置','标配','选配','不可选'];
function optionCategories() {
  return [...new Set(['动力','电池包','外观','内饰','轮毂','其他', ...data.options.map(row=>row.category).filter(Boolean)])];
}
let data, revision, csrf, published, savedJSON='', publishedJSON='', pendingPublish=false, tab='models', view='models';
let model='', year='', query='', statusFilter='', categoryFilter='', stateFilter='', page=1;
let ruleQuery='';
let dirty=false, busy=false, readonly=false, selected=new Set(), undoStack=[], redoStack=[], confirmAction=null;
const pageSize=40;
const matrixKey=(v,o)=>JSON.stringify([v,o]);
const newMatrixCells=new Set();
const modelName = r => [r.brand,r.series,r.generation,r.subgeneration,r.year && r.year+'款'].filter(Boolean).join(' ');
function display(table,id) {const r=data[table]?.find(x=>x.id===id); return r ? (table==='models' ? modelName(r) : r.name) : id;}
function referenceName(table,id) {const r=data[table]?.find(x=>x.id===id); return r ? (table==='models' ? modelName(r) : [r.name,display('models',r.model_id)].filter(Boolean).join(' · ')) : id;}
function option(value,text,current) {return `<option value="${esc(value)}" ${current===value?'selected':''}>${esc(text)}</option>`;}
function setText(element, value) {
  if (element.textContent !== value) element.textContent = value;
}
let noticeTimer;
function notice(text='',error=false) {
  clearTimeout(noticeTimer);
  setText($('#error-notice'), error ? text : '');
  $('#error-detail').hidden=!error || !text;
  setText($('#notice'), error ? '' : text);
  $('#notice').title=error ? '' : text;
  if (text && !error) noticeTimer=setTimeout(()=>setText($('#notice'),''),4000);
}
async function api(path,body) {
  const response=await fetch('api/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  const result=await response.json(); if(!response.ok) throw Error(result.error||'请求失败'); return result;
}
// Disable all live inputs for the entire request, including dialogs. Never let a save response replace newer edits.
function flags() {
  document.querySelectorAll('[data-request-disabled]').forEach(e=>{e.disabled=e.dataset.requestDisabled==='true';delete e.dataset.requestDisabled;});
  const rules = isRuleView();
  const editor=activeEditor();
  const ready=editor.ready();
  $('#save').disabled = readonly || !ready || !editor.isDirty();
  $('#publish').disabled = readonly || !ready || !editor.canPublish();
  setText($('#publish'), rules ? '发布并更新脚本' : '发布配置');
  $('#import').disabled = readonly || !ready;
  $('#export').disabled = $('#template').disabled = !ready;
  $('#undo').disabled = readonly || !editor.canUndo();
  $('#redo').disabled = readonly || !editor.canRedo();
  const status=editor.status();
  const otherDraft = rules ? dirty && '车型配置有未保存修改' : orderRules.isDirty() && '脚本规则有未保存修改';
  const statusText=busy && showProgress ? progressLabel : readonly ? '维护中 · 只读' : [status, otherDraft].filter(Boolean).join(' · ');
  setText($('#status'),statusText);
  $('#status').title=statusText;
  document.body.setAttribute('aria-busy',String(busy));
  if(busy) document.querySelectorAll('button,input,select,textarea').forEach(e=>{e.dataset.requestDisabled=String(e.disabled);e.disabled=true;});
}
let showProgress=false, progressLabel='正在载入…';
async function run(fn, label='正在处理…') {
  if(busy)return;
  busy=true;
  progressLabel=label;
  const focused=document.activeElement;
  const progress=setTimeout(()=>{showProgress=true;setText($('#status'),label);},180);
  flags();
  try {await fn();} catch(e) {if($('#dialog').open)$('#dialog-error').textContent=e.message; else notice(e.message,true);}
  finally {
    clearTimeout(progress);
    showProgress=false;busy=false;flags();
    // Disabling the focused input can send the keyboard to the document body.
    if (document.activeElement===document.body && focused?.isConnected && !focused.disabled && !$('#dialog').open) {
      focused.focus({preventScroll:true});
    }
  }
}
function syncDraftState(snapshot) {
  dirty=snapshot!==savedJSON;
  pendingPublish=snapshot!==publishedJSON;
}
function renderKeepingScroll() {
  const scroll=$('#content .scroll');
  const top=scroll?.scrollTop, left=scroll?.scrollLeft;
  const active=document.activeElement?.dataset;
  const selector=active?.v?`[data-v="${CSS.escape(active.v)}"][data-o="${CSS.escape(active.o)}"]` :
    active?.ruleId?`[data-rule-action="${CSS.escape(active.ruleAction)}"][data-rule-id="${CSS.escape(active.ruleId)}"]`:'';
  render();
  if(selector)$('#content')?.querySelector(selector)?.focus({preventScroll:true});
  if(top||left)$('#content .scroll')?.scrollTo(left,top);
}
function mutate(fn, refresh=renderKeepingScroll) {
  if(readonly)return;
  const before=JSON.stringify(data);fn();const after=JSON.stringify(data);
  if(before!==after) {undoStack.push(before);while(undoStack.length>20||undoStack.reduce((n,s)=>n+s.length,0)>8_000_000)undoStack.shift();redoStack=[];}
  syncDraftState(after);refresh();
}
function accept(s) {
  if(s.required){required=s.required;identityFields=s.identity_fields;}
  data=s.draft.data; revision=s.draft.revision;published=s.published;
  savedJSON=JSON.stringify(data);publishedJSON=JSON.stringify(published.data);syncDraftState(savedJSON);
  newMatrixCells.clear();
  undoStack=[];redoStack=[];selected.clear();
  if(s.csrf)csrf=s.csrf;if('readonly' in s)readonly=s.readonly;
  const publishedBadge=$('#published');
  publishedBadge.hidden=!published.revision;
  publishedBadge.textContent=published.revision?`已发布配置 v${published.revision}`:'';
  renderKeepingScroll();
}
function modelIds() {return new Set(data.models.filter(r=>(!model||r.id===model)&&(!year||r.year===year)).map(r=>r.id));}
function matches(r) {return !query || [...Object.values(r),r.model_id?display('models',r.model_id):'',r.target_id?referenceName(r.target_type,r.target_id):''].join(' ').toLocaleLowerCase().includes(query.toLocaleLowerCase());}
function scopedRows(table,search=true) {
  const ids=modelIds();
  return data[table].filter(r=>{
    let scoped=true;
    if(table==='models')scoped=ids.has(r.id);
    if(['versions','options','phases'].includes(table))scoped=ids.has(r.model_id);
    if((model||year)&&table==='aliases') {const target=data[r.target_type]?.find(x=>x.id===r.target_id);scoped=!!target&&ids.has(r.target_type==='models'?target.id:target.model_id);}
    if((model||year)&&table==='groups')scoped=(r.members||'').split(',').some(id=>ids.has(id));
    return scoped&&(!statusFilter||!('status' in r)||r.status===statusFilter)&&(!search||matches(r));
  });
}
function filters() {
  const rules = isRuleView();
  const modelScope = !rules && view !== 'history';
  $('#model').closest('label').hidden = !modelScope;
  $('#year').closest('label').hidden = !modelScope;
  if(model&&!data.models.some(r=>r.id===model))model='';
  if(modelScope) {
    const modelChoices=option('','全部车型','')+data.models.map(r=>option(r.id,modelName(r)+(r.status==='停用'?' · 停用':''),'')).join('');
    updateOptions($('#model'),modelChoices,model);
    const years=[...new Set(data.models.map(r=>r.year).filter(Boolean))].sort().reverse();
    if(!years.includes(year))year='';
    updateOptions($('#year'),option('','全部年款','')+years.map(y=>option(y,y+'款','')).join(''),year);
  }
  $('#status-filter-label').hidden=!['models','versions','options','matrix'].includes(view);
  $('#category-filter-label').hidden = $('#state-filter-label').hidden = view !== 'matrix';
  if (view === 'matrix') {
    const available=optionCategories();
    if(!available.includes(categoryFilter))categoryFilter='';
    $('#category-filter').innerHTML = option('', '全部类别', categoryFilter) + available.map(c => option(c,c,categoryFilter)).join('');
    $('#state-filter').innerHTML = option('', '全部状态', stateFilter) + states.map(s => option(s,s,stateFilter)).join('');
  }
  $('#search').placeholder = rules ? '搜索当前规则或记录' : view === 'history' ? '记录、操作人或时间' : view === 'matrix' ? '配置项目、类别或编号' : '名称、品牌或编号';
  const activeFilters = [
    modelScope && model, modelScope && year, !$('#status-filter-label').hidden && statusFilter,
    view==='matrix' && categoryFilter, view==='matrix' && stateFilter, rules ? ruleQuery : query,
  ].filter(Boolean).length;
  $('#clear-filters').hidden = !activeFilters;
  setText(filterToggle, activeFilters ? `筛选 · ${activeFilters}` : '筛选');
  $('#consumer').hidden = tab !== 'history';
  setText($('#consumer'), rules ? '业务报表：未重新生成' : '业务报表：尚未接入');
}
const renderedOptions=new WeakMap();
function updateOptions(select, html, value) {
  if(renderedOptions.get(select)!==html) {
    select.innerHTML=html;
    renderedOptions.set(select,html);
  }
  if(select.value!==value) select.value=value;
}
function actionButton(action,text) {return `<button data-action="${action}" ${readonly?'disabled':''}>${text}</button>`;}
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
let viewAnimation;
reduceMotion.addEventListener('change', () => { if (reduceMotion.matches) viewAnimation?.cancel(); });
function revealView() {
  const content = $('#content');
  const ongoing = viewAnimation?.playState === 'running';
  const from = ongoing ? getComputedStyle(content).opacity : '0.82';
  viewAnimation?.cancel();
  if (reduceMotion.matches || !content.animate) return;
  const style = getComputedStyle(content);
  viewAnimation = content.animate([{opacity:from},{opacity:1}], {
    duration: parseFloat(style.getPropertyValue('--motion-view')),
    easing: style.getPropertyValue('--ease-out').trim(),
  });
}
function switchView(nextTab, nextView=nextTab) {
  if (tab === nextTab && view === nextView) return;
  clearTimeout(searchTimer);
  cancelAnimationFrame(searchFrame);
  const content=$('#content');
  delete content.dataset.historyView;
  content.removeAttribute('aria-busy');
  tab = nextTab;
  view = nextView;
  selected.clear();
  page = 1;
  $('#search').value = isRuleView() ? ruleQuery : query;
  notice();
  render();
  revealView();
  if (isRuleView() && !orderRules.ready()) {
    run(orderRules.load,'正在读取脚本规则…');
  }
}
function revealActiveTab(row) {
  const active = row.querySelector('.active');
  if (!active || row.scrollWidth <= row.clientWidth) return;
  const bounds = row.getBoundingClientRect();
  const item = active.getBoundingClientRect();
  if (item.left < bounds.left) row.scrollLeft += item.left - bounds.left;
  else if (item.right > bounds.right) row.scrollLeft += item.right - bounds.right;
}
const nav = $('header nav');
const mobileTab = $('#mobile-tab');
for (const button of nav.querySelectorAll('[data-tab]')) {
  mobileTab.add(new Option(button.textContent.trim(), button.dataset.tab));
}
mobileTab.onchange = () => {
  if (busy || !data) mobileTab.value = tab;
  else switchView(mobileTab.value);
};
const filterToggle = $('.mobile-filter-toggle');
filterToggle.onclick = () => {
  filterToggle.setAttribute('aria-expanded', String(filterToggle.getAttribute('aria-expanded') !== 'true'));
};
const viewTabs = $('#view-tabs');
const tabSizes = new ResizeObserver(entries => entries.forEach(entry => revealActiveTab(entry.target)));
tabSizes.observe(nav);
tabSizes.observe(viewTabs);
function render() {
  if(!data)return;filters();
  if (nav.dataset.active !== tab) {
    nav.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-current',b.dataset.tab===tab?'page':'false');});
    mobileTab.value = tab;
    nav.dataset.active = tab;
    viewTabs.innerHTML = sections[tab].map(t => `<button id="view-${t}" data-view="${t}">${viewLabels[t]}</button>`).join('');
    revealActiveTab(nav);
  }
  if (viewTabs.dataset.active !== view) {
    for (const button of viewTabs.children) {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
    viewTabs.dataset.active = view;
    viewTabs.closest('section').setAttribute('aria-labelledby','view-'+view);
    revealActiveTab(viewTabs);
  }
  if (isRuleView()) {
    $('#bulkbar').hidden = true;
    orderRules.render(view, ruleQuery);
    flags();
    return;
  }
  const needsModel=view!=='models'&&!data.models.length;
  $('#actions').innerHTML=view==='history'?'<button data-runtime>运行路径</button>':readonly||needsModel?'':view==='matrix'?actionButton('add:versions','新增版本')+actionButton('add:options','新增配置'):(['versions','options'].includes(view)?actionButton('batch-add:'+view,'批量新增'):'')+actionButton('add:'+view,'新增'+names[view]);
  if(view==='matrix')renderMatrix();else if(view==='history')renderHistory();else renderTable();
  flags();
}
function paginator(total, rowsPerPage=pageSize) {
  const pages=Math.max(1,Math.ceil(total/rowsPerPage));page=Math.min(page,pages);
  $('#pagination').innerHTML=pages>1?`<span>第 ${page} / ${pages} 页</span><button data-page="${page-1}" ${page===1?'disabled':''}>上一页</button><button data-page="${page+1}" ${page===pages?'disabled':''}>下一页</button>`:'';
  return [(page-1)*rowsPerPage,page*rowsPerPage];
}
function checkbox(id,label) {return `<label class="row-hit"><input type="checkbox" class="row-check" data-select="${esc(id)}" aria-label="${esc(label)}" ${selected.has(id)?'checked':''}></label>`;}
let selectionRecords=[];
function updateSelection() {
  const all=$('#select-all');
  if (!all) return;
  all.checked=selected.size>0 && selected.size===selectionRecords.length;
  all.indeterminate=selected.size>0 && !all.checked;
  const count=$('#selection-count');
  count.textContent=`已选 ${selected.size} 项`;
  for(const element of [count,...$('#bulkbar').querySelectorAll('[data-bulk-control]')])element.hidden=!selected.size;
  const apply=$('#apply-status');
  if(apply) apply.disabled=!selectionRecords.some(row=>selected.has(row.id)&&row.status!==$('#batch-status').value);
}
function selectionToolbar(records, matrix=false) {
  selectionRecords=records;
  const visibleIds=new Set(records.map(r=>r.id));
  selected=new Set([...selected].filter(id=>visibleIds.has(id)));
  const bar=$('#bulkbar');
  bar.hidden=readonly || !records.length;
  if(bar.hidden){bar.replaceChildren();return;}
  const extra=matrix?'<button data-bulk-control id="batch-matrix">批量设置</button>':'<label data-bulk-control class="inline-label">批量状态<select id="batch-status"><option>规划</option><option>在售</option><option>停用</option></select></label><button data-bulk-control id="apply-status">应用状态</button>';
  bar.innerHTML=`<label class="check-label"><input id="select-all" type="checkbox">全选筛选结果</label><span id="selection-count"></span>${extra}`;
  $('#select-all').onchange=e=>{
    if(busy||readonly)return;
    selected=new Set(e.target.checked?records.map(r=>r.id):[]);
    for(const box of $('#content').querySelectorAll('[data-select]')){
      box.checked=selected.has(box.dataset.select);
      box.closest('tr').classList.toggle('selected',box.checked);
    }
    updateSelection();
  };
  if(matrix)$('#batch-matrix').onclick=batchMatrix;
  else{$('#batch-status').onchange=updateSelection;$('#apply-status').onclick=batchStatus;}
  updateSelection();
}
function renderTable() {
  const records=scopedRows(view);const range=paginator(records.length);
  $('#count').textContent=records.length ? `${records.length} 条记录` : '';
  const selectable=!readonly&&['models','versions','options'].includes(view);
  selectionToolbar(selectable?records:[]);
  const columns=view==='models'?['brand','series','generation','subgeneration','year','energy','status']:fields[view];
  const actionCell=r=>readonly?'':`<td class="operation"><button class="link" data-edit="${esc(r.id)}">编辑</button>${view==='models'?`<button class="link" data-copy="${esc(r.id)}">复制年款</button>`:''}</td>`;
  $('#content').innerHTML=records.length?`<div class="scroll"><table><thead><tr>${selectable?'<th class="check-cell"></th>':''}${columns.map(f=>`<th>${labels[f]}</th>`).join('')}${readonly?'':'<th class="operation">操作</th>'}</tr></thead><tbody>${records.slice(...range).map(r=>`<tr class="${selected.has(r.id)?'selected':''}">${selectable?`<td class="check-cell">${checkbox(r.id,'选择 '+(r.name||modelName(r)))}</td>`:''}${columns.map(f=>`<td${f==='year'?' class="year-value"':''}>${tableValue(r,f)}</td>`).join('')}${actionCell(r)}</tr>`).join('')}</tbody></table></div>`:emptyCatalog();
}
function emptyCatalog() {
  const filtered=view==='matrix' ? Boolean(query||model||year||categoryFilter||statusFilter||stateFilter) : data[view].length>0;
  let message=filtered?'没有符合筛选的记录':'暂无'+names[view], action='';
  if(!data.models.length&&view!=='models'){
    message='暂无车型档案';
    if(!readonly)action=actionButton('create-model','新增车型档案');
  }else if(filtered)action='<button data-clear>清空筛选</button>';
  else if(view==='matrix')message=!data.versions.length?'暂无版本':'暂无配置项目';
  return `<div class="empty"><strong>${message}</strong>${action}</div>`;
}
function tableValue(r,f) {
  if(f==='status')return `<span class="pill ${r[f]==='停用'?'retired':''}">${esc(r[f])}</span>`;
  if(f==='model_id')return esc(display('models',r[f]));
  if(f==='target_type')return esc(names[r[f]]||r[f]);
  if(f==='target_id')return esc(referenceName(r.target_type,r[f]));
  if(f==='members')return esc((r[f]||'').split(',').map(id=>display('models',id)).join('、'));
  return esc(r[f]||'—');
}
function matrixRows() {
  const versions=scopedRows('versions',false), index=new Map(data.matrix.map(r=>[matrixKey(r.version_id,r.option_id),r]));
  const options=scopedRows('options').filter(o=>(!categoryFilter||o.category===categoryFilter)&&(!stateFilter||versions.some(v=>v.model_id===o.model_id&&(index.get(matrixKey(v.id,o.id))?.state||'未配置')===stateFilter)));
  return {versions,options,index};
}
function renderMatrix() {
  const {versions,options,index}=matrixRows();
  // Native modal focus changes touch every select on the page. Bound the number of live cells.
  const rowsPerPage=Math.max(1,Math.min(pageSize,Math.floor(96 / Math.max(1,versions.length))));
  const range=paginator(versions.length ? options.length : 0,rowsPerPage);
  $('#count').textContent=options.length || versions.length ? `${options.length} 项配置 · ${versions.length} 个版本` : '';
  selectionToolbar(versions.length?options:[],true);
  $('#content').innerHTML=versions.length&&options.length?`<div class="scroll matrix-scroll"><table class="matrix-table"><thead><tr>${readonly?'':'<th class="check-cell"></th>'}<th class="sticky-col">配置项目</th>${versions.map(v=>`<th>${esc(v.name)}<small>${esc(display('models',v.model_id))}</small></th>`).join('')}</tr></thead><tbody>${options.slice(...range).map(o=>`<tr class="${selected.has(o.id)?'selected':''}">${readonly?'':`<td class="check-cell">${checkbox(o.id,'选择 '+o.name)}</td>`}<td class="sticky-col"><small>${esc(o.category)}</small>${esc(o.name)}</td>${versions.map(v=>{
    if(v.model_id!==o.model_id)return '<td class="inapplicable">—</td>';
    const state=index.get(matrixKey(v.id,o.id))?.state||'未配置';
    return `<td><select class="state-${states.indexOf(state)}" data-v="${esc(v.id)}" data-o="${esc(o.id)}" aria-label="${esc(v.name+' '+o.name)}" ${readonly?'disabled':''}>${states.map(s=>option(s,s,state)).join('')}</select></td>`;
  }).join('')}</tr>`).join('')}</tbody></table></div>`:emptyCatalog();
}
function setMatrix(versionId,optionId,state,index=null) {
  const key=matrixKey(versionId,optionId);
  let row=index?index.get(key):data.matrix.find(r=>r.version_id===versionId&&r.option_id===optionId);
  if(!row){
    if(state==='未配置')return;
    row={id:newId(),version_id:versionId,option_id:optionId};
    data.matrix.push(row);
    newMatrixCells.add(row.id);
    index?.set(key,row);
  }else if(state==='未配置'&&newMatrixCells.has(row.id)){
    data.matrix.splice(data.matrix.indexOf(row),1);
    index?.delete(key);
    return;
  }
  row.state=state;
}
function batchMatrix() {
  const {versions,options,index}=matrixRows(),chosen=options.filter(o=>selected.has(o.id));
  modal('批量设置配置',`<div class="form"><label>适用状态<select id="batch-state">${states.map(s=>option(s,s,'选配')).join('')}</select></label><label class="check-label"><input id="only-unset" type="checkbox" checked>仅填充未配置</label><div class="wide"><h3>目标版本</h3><div class="members">${versions.filter(v=>chosen.some(o=>o.model_id===v.model_id)).map(v=>`<label><input name="batch-version" type="checkbox" value="${esc(v.id)}" checked>${esc(referenceName('versions',v.id))}</label>`).join('')}</div></div><div id="batch-impact" class="wide selection-summary">已选 ${chosen.length} 项配置</div></div>`,()=>{
    const ids=new Set([...document.querySelectorAll('[name="batch-version"]:checked')].map(e=>e.value));if(!ids.size)throw Error('请选择目标版本');
    const state=$('#batch-state').value,onlyUnset=$('#only-unset').checked;let affected=0;
    mutate(()=>{for(const o of chosen)for(const v of versions)if(ids.has(v.id)&&v.model_id===o.model_id){const old=index.get(matrixKey(v.id,o.id))?.state||'未配置';if((!onlyUnset||old==='未配置')&&old!==state){setMatrix(v.id,o.id,state,index);affected++;}}});
    notice(`已更新 ${affected} 项适用关系，尚未保存`);
  },'应用设置');
  const impact=()=>{
    const ids=new Set([...document.querySelectorAll('[name="batch-version"]:checked')].map(e=>e.value));
    const state=$('#batch-state').value,onlyUnset=$('#only-unset').checked;
    const count=chosen.reduce((n,o)=>n+versions.filter(v=>{const old=index.get(matrixKey(v.id,o.id))?.state||'未配置';return ids.has(v.id)&&v.model_id===o.model_id&&(!onlyUnset||old==='未配置')&&old!==state;}).length,0);
    $('#batch-impact').textContent=`已选 ${chosen.length} 项配置 · ${ids.size} 个版本 · 将修改 ${count} 项适用关系`;
    $('#confirm').disabled=count===0;
  };
  $('#dialog-body').onchange=impact;impact();
}

function batchStatus() {
  const status=$('#batch-status').value,rows=scopedRows(view).filter(r=>selected.has(r.id)&&r.status!==status);
  modal('批量修改状态',`<div class="selection-summary">${rows.length} 条记录 → ${esc(status)}</div><ul class="record-list">${rows.map(r=>`<li>${esc(r.name||modelName(r))}</li>`).join('')}</ul>`,()=>{selected.clear();mutate(()=>rows.forEach(r=>r.status=status));notice(`已修改 ${rows.length} 条记录，尚未保存`);},'应用状态');
}
let historyRequest=0;
async function renderHistory(search=query, warning='') {
  const request=++historyRequest, requestedView=view;
  const rules=requestedView==='rule_history';
  const path=rules?'rules/history':'history';
  $('#bulkbar').hidden=true;
  const content=$('#content');
  content.setAttribute('aria-busy','true');
  if(content.dataset.historyView!==requestedView) {
    $('#count').textContent='';
    $('#pagination').replaceChildren();
    content.textContent='正在读取变更记录…';
    content.dataset.historyView=requestedView;
  }
  try {
    const result=await api(path+'?'+new URLSearchParams({page,q:search}));
    if(request!==historyRequest || view!==requestedView)return;
    page=result.page;
    paginator(result.total);
    $('#count').textContent=result.total ? `${result.total} 条记录` : '';
    const rows=result.history.map(record=>{
      const action=rules?`data-rule-action="history" data-rule-id="${record.id}"`:`data-history="${record.id}"`;
      return `<tr><td>#${record.id}</td><td>${esc(record.kind)}</td><td>${esc(record.actor)}</td><td>${esc(new Date(record.time).toLocaleString())}</td><td><button class="link" ${action}>差异与恢复</button></td></tr>`;
    });
    $('#content').innerHTML=warning+(rows.length?`<div class="scroll"><table><thead><tr><th>记录</th><th>操作</th><th>操作人</th><th>时间</th><th></th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`:'<div class="empty"><strong>暂无变更记录</strong></div>');
  } catch(error) {
    if(request===historyRequest && view===requestedView)$('#content').textContent=error.message;
  } finally {
    if(request===historyRequest && view===requestedView)content.removeAttribute('aria-busy');
  }
}
let initialForm=null, discardPrompt=null, lastOpener='', returnFocus='';
document.addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button||button.closest('dialog'))return;
  const attrs=button.getAttributeNames().filter(name=>name.startsWith('data-')&&name!=='data-request-disabled');
  lastOpener=button.id?'#'+CSS.escape(button.id):attrs.map(name=>`[${name}="${CSS.escape(button.getAttribute(name))}"]`).join('');
},true);
$('#dialog').addEventListener('close',()=>{
  if($('#dialog').open||document.activeElement!==document.body&&!$('#dialog').contains(document.activeElement))return;
  const opener=returnFocus&&$(returnFocus);
  (opener?.getClientRects().length&&!opener.disabled?opener:$('#view-tabs .active'))?.focus({preventScroll:true});
});
function formSnapshot() {
  const form=$('#dialog-body form');
  return form?JSON.stringify([...new FormData(form)]):null;
}
function hasFormInput() {
  return $('#dialog').open && initialForm!==null && initialForm!==formSnapshot();
}
function closeOrKeepEditing() {
  if(busy)return;
  if(discardPrompt){
    $('#dialog-title').textContent=discardPrompt.title;
    $('#confirm').textContent=discardPrompt.confirm;
    $('#cancel').textContent='取消';
    $('#dialog-body').hidden=$('#dialog-error').hidden=false;
    const focus=discardPrompt.focus;
    discardPrompt=null;
    (focus?.isConnected?focus:$('#dialog-body form input, #dialog-body form select, #dialog-body form textarea'))?.focus();
  }else if(hasFormInput()){
    discardPrompt={title:$('#dialog-title').textContent, confirm:$('#confirm').textContent, focus:$('#dialog-body form :focus')};
    $('#dialog-title').textContent='放弃尚未应用的输入？';
    $('#dialog-body').hidden=$('#dialog-error').hidden=true;
    $('#confirm').textContent='放弃输入';
    $('#cancel').textContent='继续编辑';
    $('#cancel').focus();
  }else $('#dialog').close();
}
function modal(title,html,action,label='确定') {
  discardPrompt=null;
  $('#dialog-body').hidden=$('#dialog-error').hidden=false;
  $('#cancel').textContent='取消';
  $('#dialog-body').onchange=null;$('#dialog-title').textContent=title;$('#dialog-body').innerHTML=html;$('#dialog-error').textContent='';confirmAction=action;
  $('#confirm').textContent=label;$('#confirm').hidden=!action;$('#confirm').disabled=false;
  initialForm=formSnapshot();
  if(!$('#dialog').open){returnFocus=lastOpener;$('#dialog').showModal();}flags();
  const form=$('#dialog-body form');
  if(form){
    const controls=[...form.querySelectorAll('input:not([type=checkbox]),select,textarea')];
    (controls.find(control=>control.required&&!control.value)||controls[0])?.focus();
  }
}
function formSelect(field,values,current,mandatory=false) {return `<select name="${field}" ${mandatory?'required':''}>${option('','请选择',current)+values.map(([id,text])=>option(id,text,current)).join('')}</select>`;}
function defaultModel() {
  const ids=modelIds();
  return ids.size===1?[...ids][0]:'';
}
function edit(table,id,copy=false) {
  const source=data[table].find(r=>r.id===id),record=structuredClone(source||{id:newId(),status:'规划',model_id:defaultModel(),target_type:'models',category:categoryFilter||'外观'});
  if(copy){record.id=newId();record.year='';}
  const mandatory=new Set(required[table]);
  modal(copy?'复制年款':(source?'编辑':'新增')+names[table],`<form id="edit-form" class="form">${fields[table].map(f=>{
    let control;
    if(f==='members') {const set=new Set((record.members||'').split(','));control=`<div class="members">${data.models.map(m=>`<label><input type="checkbox" name="member" value="${esc(m.id)}" ${set.has(m.id)?'checked':''}>${esc(modelName(m))}</label>`).join('')}</div>`;}
    else if(f==='status')control=formSelect(f,['规划','在售','停用'].map(x=>[x,x]),record[f],true);
    else if(f==='model_id')control=formSelect(f,data.models.map(r=>[r.id,modelName(r)]),record[f],true);
    else if(f==='target_type')control=formSelect(f,['models','versions','options'].map(x=>[x,names[x]]),record[f],true);
    else if(f==='target_id')control=formSelect(f,(data[record.target_type]||[]).map(r=>[r.id,referenceName(record.target_type,r.id)]),record[f],true);
    else if(f==='category')control=formSelect(f,optionCategories().map(x=>[x,x]),record[f],true);
    else control=`<input name="${f}" value="${esc(record[f]||'')}" type="${f.endsWith('_date')?'date':'text'}" ${mandatory.has(f)?'required':''} maxlength="4000">`;
    return `<label ${f==='members'?'class="wide"':''}>${labels[f]}${mandatory.has(f)?'<span class="required" aria-hidden="true">*</span>':''}${control}</label>`;
  }).join('')}<div class="wide muted">编号：${esc(record.id)}</div></form>`,async values=>{
    const clean={id:record.id};for(const f of fields[table])clean[f]=f==='members'?values.getAll('member').join(','):String(values.get(f)||'').trim();
    if(table==='groups'&&!clean.members)throw Error('请选择汇总成员');
    for(const f of mandatory)if(!clean[f])throw Error('请填写'+labels[f]);
    if(source&&!copy&&fields[table].every(field=>clean[field]===(source[field]||'')))return;
    const rows=data[table].filter(r=>r.id!==clean.id);
    const keyFields=identityFields[table];
    const duplicate=table==='aliases'
      ? (await api('check-name',{row:clean,others:rows.map(r=>({source:r.source||'',name:r.name||''}))})).duplicate
      : rows.some(r=>keyFields.every(f=>(r[f]||'')===(clean[f]||'')));
    if(duplicate)throw Error('同一范围内名称已存在');
    mutate(()=>{
      if(copy){
        data.models.push(clean);
        const versions=new Map(), options=new Map();
        for(const [table,ids] of [['versions',versions],['options',options]]){
          for(const row of data[table].filter(r=>r.model_id===source.id)){
            const id=newId();
            ids.set(row.id,id);
            data[table].push({...row,id,model_id:clean.id});
          }
        }
        for(const phase of data.phases.filter(r=>r.model_id===source.id)){
          data.phases.push({...phase,id:newId(),model_id:clean.id,presale_date:'',sale_date:''});
        }
        for(const row of data.matrix.filter(r=>versions.has(r.version_id)&&options.has(r.option_id))){
          data.matrix.push({...row,id:newId(),version_id:versions.get(row.version_id),option_id:options.get(row.option_id)});
        }
        model=clean.id;
        year='';
      }
      else if(source)Object.assign(source,clean);else data[table].push(clean);
    });
  });
  $('#edit-form').onsubmit=e=>{e.preventDefault();$('#confirm').click();};
  if(table==='aliases')$('[name="target_type"]').onchange=e=>{$('[name="target_id"]').innerHTML=option('','请选择','')+(data[e.target.value]||[]).map(r=>option(r.id,referenceName(e.target.value,r.id),'')).join('');};
}
function batchAdd(table) {
  modal('批量新增'+names[table],`<form id="batch-form" class="form"><label>车型${formSelect('model_id',data.models.map(r=>[r.id,modelName(r)]),defaultModel(),true)}</label>${table==='options'?`<label>类别${formSelect('category',optionCategories().map(c=>[c,c]),categoryFilter||'外观',true)}</label>`:''}<label class="wide">名称（每行一项）<textarea name="names" rows="9" required maxlength="20000"></textarea></label></form>`,values=>{
    const modelId=values.get('model_id'),category=values.get('category');
    const list=String(values.get('names')).split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
    if(!list.length||list.length>200)throw Error('每次可新增 1–200 项');
    if(new Set(list).size!==list.length)throw Error('输入的名称中存在重复项');
    const duplicates=list.filter(name=>data[table].some(r=>r.model_id===modelId&&r.name===name&&(table!=='options'||r.category===category)));
    if(duplicates.length)throw Error('已存在：'+duplicates.join('、'));
    mutate(()=>list.forEach(name=>data[table].push(table==='options'?{id:newId(),model_id:modelId,name,category,status:'规划'}:{id:newId(),model_id:modelId,name,power:'',battery:'',status:'规划'})));
    notice(`已新增 ${list.length} 项，尚未保存`);
  },'应用到编辑区');
}
function pretty(value) {if(value&&typeof value==='object')return Object.entries(value).filter(([k])=>k!=='id').map(([k,v])=>`${labels[k]||k}：${v}`).join('；');return String(value??'');}
function differences(changes) {
  const counts=new Set(changes.map(c=>c.table+'|'+c.id)).size;
  return changes.length?`<div class="selection-summary">${counts} 条记录 · ${changes.length} 处变更</div><div class="scroll"><table class="diff"><thead><tr><th>对象 / 字段</th><th>修改前</th><th>修改后</th></tr></thead><tbody>${changes.map(c=>`<tr><td>${esc(names[c.table])}<small>${esc(display(c.table,c.id)||c.id)}</small>${esc(labels[c.field]||c.field)}</td><td>${esc(pretty(c.before))}</td><td>${esc(pretty(c.after))}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">没有变更</div>';
}
async function saveDraft() {const snapshot=structuredClone(data);accept(await api('save',{data:snapshot,revision}));notice('草稿已保存');}
async function previewPublish() {
  const snapshot=structuredClone(data),baseRevision=revision,result=await api('preview',{data:snapshot,revision:baseRevision});
  modal('发布配置',`<p class="error">${esc(result.errors.join('\n'))}</p>${differences(result.changes)}`,result.errors.length||!result.changes.length?null:async()=>{accept(await api('publish',{data:snapshot,revision:baseRevision}));notice('配置已发布');},'确认发布');
}
async function download(path,body,filename) {
  const response=await fetch('api/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok)throw Error((await response.json()).error);
  const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function openExport() {
  const downloadCurrent=()=>download('export',{data},'车型配置-当前编辑.xlsx');
  if (!published.revision) return run(downloadCurrent,'正在导出 Excel…');
  modal('导出 Excel',`<div class="export-options"><label><input type="radio" name="export-source" value="editor" checked>当前编辑内容${dirty?' · 含未保存修改':''}</label><label><input type="radio" name="export-source" value="published">已发布配置 · v${published.revision}</label></div>`,()=> $('[name="export-source"]:checked').value==='published'
    ? download('export',{data:published.data},`车型配置-已发布-v${published.revision}.xlsx`)
    : downloadCurrent(),'下载 Excel');
}
function fileBase64(file) {return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('读取文件失败：'+file.name));reader.readAsDataURL(file);});}
async function encodeWorkbooks(files) {
  if(files.length>10)throw Error('每批最多 10 个 Excel 文件');
  if(files.some(f=>!f.name.toLowerCase().endsWith('.xlsx')))throw Error('请选择 .xlsx 文件');
  if(files.reduce((n,f)=>n+f.size,0)>5_500_000)throw Error('本批文件总大小不能超过 5.5 MB');
  return Promise.all(files.map(async f=>({name:f.name,content:await fileBase64(f)})));
}
async function importFiles(files) {
  const payload=await encodeWorkbooks(files);
  const result=await api('import-batch',{files:payload,revision,...(dirty ? {editor:data} : {})});
  const summary=result.summary||{},issues=result.issues||[];
  const issueHTML=issues.length?`<div class="scroll"><table class="diff"><thead><tr><th>文件 / 工作表</th><th>位置</th><th>问题</th></tr></thead><tbody>${issues.map(i=>`<tr><td>${esc(i.file)}<small>${esc(i.sheet)}</small></td><td>${i.row?'第 '+esc(i.row)+' 行':''} ${esc(i.field||'')}</td><td class="error">${esc(i.message)}</td></tr>`).join('')}</tbody></table></div>`:`<p class="error">${esc((result.errors||[]).join('\n'))}</p>`;
  modal('导入预览',`<div class="file-list">${files.map(f=>`<span>${esc(f.name)}</span>`).join('')}</div><div class="import-summary"><span>新增 <b>${summary.added??0}</b></span><span>更新 <b>${summary.updated??0}</b></span><span>未变更 <b>${summary.unchanged??0}</b></span><span>问题 <b>${(result.errors||[]).length||issues.length}</b></span></div>${issueHTML}${differences(result.changes||[])}`,result.errors?.length||issues.length||!result.changes?.length?null:async()=>{
    const fresh=await api('state');if(fresh.draft.revision!==result.revision||revision!==result.revision)throw Error('草稿已被其他人修改，请重新载入后再次导入');
    mutate(()=>{data=result.data;});notice('导入内容已应用，尚未保存');
  },'应用到编辑区');
}
function requestImport() {$('#file').click();}
$('#confirm').onclick=()=>{
  if(discardPrompt){discardPrompt=null;$('#dialog').close();return;}
  const form=$('#dialog-body form');if(form&&!form.reportValidity())return;
  const values=form?new FormData(form):null;
  run(async()=>{if(confirmAction){$('#dialog-error').textContent='';const done=await confirmAction(values);if(done!==false)$('#dialog').close();}});
};
$('#close').onclick=$('#cancel').onclick=closeOrKeepEditing;
$('#dialog').addEventListener('cancel',e=>{e.preventDefault();closeOrKeepEditing();});
document.addEventListener('click',e=>{
  if(busy)return;const b=e.target.closest('button');if(!b)return;
  if(b.hasAttribute('data-runtime'))return run(async()=>{
    const info=await api('runtime');
    const rows=[['数据目录',info.data_dir,info.sources.data_dir],['车型数据库',info.catalog],
      ['规则数据库',info.rules_db],['规则脚本',info.shared_config||'未配置',info.sources.shared_config],
      ['脚本状态',info.script_exists?'文件存在':'文件未找到'],['Excel 导出',info.excel]];
    if(info.release.version)rows.push(['运行包版本',info.release.version],['构建提交',info.release.gitCommit||'旧包未记录',info.release.sourceDirty?'含未提交修改':'']);
    modal('运行路径','<dl>'+rows.map(([label,value,source])=>`<dt>${esc(label)}</dt><dd>${esc(value)} ${source?'（'+esc(source)+'）':''}</dd>`).join('')+'</dl>');
  });
  if(b.dataset.tab)return switchView(b.dataset.tab);
  if(b.dataset.view)return switchView(tab,b.dataset.view);
  if(b.dataset.ruleAction && isRuleView())return orderRules.handleClick(b,view);
  if(b.dataset.action&&!readonly){const [action,t]=b.dataset.action.split(':');if(action==='create-model'){switchView('models');edit('models');}else if(action==='add')edit(t);else if(action==='batch-add')batchAdd(t);}
  if(b.dataset.edit&&!readonly)edit(view,b.dataset.edit);
  if(b.dataset.copy&&!readonly)edit('models',b.dataset.copy,true);
  if(b.dataset.page){page=Number(b.dataset.page);render();}
  if(b.hasAttribute('data-clear'))clearFilters();
  if(b.dataset.history)run(async()=>{
    const h=await api('history/'+b.dataset.history);
    modal('变更记录 #'+b.dataset.history,differences(h.changes),readonly?null:()=>{
      mutate(()=>{for(const t of Object.keys(data)){const ids=new Set(h.data[t].map(r=>r.id));h.data[t].push(...data[t].filter(r=>!ids.has(r.id)).map(r=>({...r,...(['models','versions','options'].includes(t)?{status:'停用'}:{})})));}data=h.data;});notice('历史记录已恢复到编辑区，尚未保存');
    },'恢复到草稿');
  });
});
$('#content').onchange=e=>{
  if(busy||readonly)return;
  const cell=e.target, id=cell.dataset.select;
  if(id){cell.checked?selected.add(id):selected.delete(id);cell.closest('tr').classList.toggle('selected',cell.checked);updateSelection();return;}
  const v=cell.dataset.v,o=cell.dataset.o;
  if(!v)return;
  mutate(()=>setMatrix(v,o,cell.value),()=>{
    cell.className='state-'+states.indexOf(cell.value);
    const row=cell.closest('tr');
    if(!stateFilter||[...row.querySelectorAll('[data-v]')].some(select=>select.value===stateFilter)){flags();return;}
    const scroll=cell.closest('.scroll'),top=scroll.scrollTop,left=scroll.scrollLeft;
    const next=(row.nextElementSibling||row.previousElementSibling)?.querySelector('[data-o]')?.dataset.o;
    render();
    const newScroll=$('#content .scroll');
    if(newScroll){
      newScroll.scrollTo(left,top);
      if(next)newScroll.querySelector(`[data-v="${CSS.escape(v)}"][data-o="${CSS.escape(next)}"]`)?.focus({preventScroll:true});
    }else $('#state-filter').focus({preventScroll:true});
  });
};
function clearFilters() {
  if (isRuleView()) {
    ruleQuery = '';
  } else {
    model=year=query=statusFilter=categoryFilter=stateFilter='';
    $('#status-filter').value='';
  }
  $('#search').value='';
  selected.clear();
  page=1;
  render();
}
$('#clear-filters').onclick=clearFilters;
for(const [id,assign] of [['model',v=>model=v],['year',v=>year=v],['status-filter',v=>statusFilter=v],
  ['category-filter',v=>categoryFilter=v],['state-filter',v=>stateFilter=v]]) {
  $('#'+id).onchange=e=>{if(busy)return;assign(e.target.value);selected.clear();page=1;render();};
}
let searchTimer, searchFrame;
function updateSearch() {
  if(busy)return;
  const value=$('#search').value.trim();
  if(isRuleView())ruleQuery=value;else query=value;
  selected.clear();
  page=1;
  clearTimeout(searchTimer);
  cancelAnimationFrame(searchFrame);
  if (view==='history' || view==='rule_history') {
    historyRequest++;
    searchTimer=setTimeout(render,120);
  } else {
    searchFrame=requestAnimationFrame(render);
  }
}
$('#search').oninput=e=>{if(!e.isComposing)updateSearch();};
$('#search').addEventListener('compositionend',updateSearch);
const catalogEditor={
  ready:()=>Boolean(data), isDirty:()=>dirty, canPublish:()=>pendingPublish,
  canUndo:()=>undoStack.length>0, canRedo:()=>redoStack.length>0,
  status:()=>!data?(busy?'正在载入…':'尚未载入'):dirty?'有未保存的修改':`草稿 r${revision} · 已保存`,
  save:saveDraft, previewPublish,
  reload(){
    const load=async()=>{accept(await api('state'));notice();};
    if(dirty)modal('放弃未保存修改？','<div class="selection-summary">重新载入服务器草稿</div>',load,'重新载入');else run(load);
  },
  undo(){
    if(busy||readonly||!undoStack.length)return;
    redoStack.push(JSON.stringify(data));const previous=undoStack.pop();data=JSON.parse(previous);syncDraftState(previous);selected.clear();renderKeepingScroll();notice('已撤销');
  },
  redo(){
    if(busy||readonly||!redoStack.length)return;
    undoStack.push(JSON.stringify(data));const next=redoStack.pop();data=JSON.parse(next);syncDraftState(next);selected.clear();renderKeepingScroll();notice('已重做');
  },
};
function activeEditor(){return isRuleView()?orderRules:catalogEditor;}
$('#save').onclick=()=>run(activeEditor().save,'正在保存草稿…');
$('#error-detail').onclick=()=>modal('操作未完成',`<p class="error">${esc($('#error-notice').textContent)}</p>`);
$('#publish').onclick=()=>run(activeEditor().previewPublish,'正在核对发布差异…');
$('#reload').onclick=()=>activeEditor().reload();
$('#undo').onclick=()=>activeEditor().undo();
$('#redo').onclick=()=>activeEditor().redo();
$('#export').onclick=()=>isRuleView()?run(orderRules.openExport):openExport();
$('#template').onclick=()=>run(isRuleView()?orderRules.template:()=>download('template',undefined,'车型配置-导入模板.xlsx'));
$('#import').onclick=requestImport;
$('#file').onchange=e=>{
  const files=[...e.target.files];e.target.value='';if(!files.length)return;
  const rules=isRuleView(), importer=rules?orderRules.importFiles:importFiles;
  run(()=>importer(files),'正在预览导入…');
};
window.addEventListener('keydown',event=>{
  if(event.defaultPrevented||event.altKey||event.isComposing||!(event.ctrlKey||event.metaKey))return;
  const key=event.key.toLowerCase();
  if(key==='s'){
    event.preventDefault();
    if(!busy&&!$('#dialog').open&&!$('#save').disabled)$('#save').click();
    return;
  }
  if(busy||$('#dialog').open||event.target.isContentEditable||event.target.closest('textarea,input:not([type=checkbox])'))return;
  const action=key==='z'?(event.shiftKey?'redo':'undo'):key==='y'?'redo':'';
  if(action&&!$('#'+action).disabled){event.preventDefault();activeEditor()[action]();}
});
window.addEventListener('beforeunload',e=>{if(dirty||orderRules.isDirty()||hasFormInput()){e.preventDefault();e.returnValue='';}});
run(async()=>accept(await api('state')));
