'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {models:'车型档案', phases:'发布阶段', versions:'版本', options:'配置项目', matrix:'配置适用关系', aliases:'名称映射', groups:'汇总规则', history:'变更记录'};
const labels = {id:'编号',brand:'品牌',series:'车系',generation:'代际',subgeneration:'二级代际',year:'年款',energy:'能源类型',tier:'产品档位',status:'状态',model_id:'车型',name:'名称',presale_date:'小订日期',sale_date:'首销日期',power:'动力',battery:'电池包',category:'类别',source:'来源',target_type:'对象类型',target_id:'标准对象',scope:'生效范围',start_date:'生效日期',end_date:'截止日期',members:'成员',version_id:'版本',option_id:'配置项目',state:'适用状态'};
const fields = {models:['brand','series','generation','subgeneration','year','energy','tier','status'], phases:['model_id','name','presale_date','sale_date'], versions:['model_id','name','power','battery','status'], options:['model_id','category','name','status'], aliases:['source','name','target_type','target_id'], groups:['name','scope','start_date','end_date','members']};
const required = {models:['brand','series','generation','year','energy','status'], phases:['model_id','name'], versions:['model_id','name','status'], options:['model_id','category','name','status'], aliases:['source','name','target_type','target_id'], groups:['name','scope','members']};
function newId() {
  if(crypto.randomUUID)return crypto.randomUUID();
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}
const states = ['未配置','标配','选配','不可选'];
const categories = ['动力','电池包','外观','内饰','轮毂','其他'];
let data, revision, csrf, published, history=[], savedJSON='', tab='models', view='models';
let model='', year='', query='', statusFilter='', categoryFilter='', stateFilter='', page=1;
let dirty=false, busy=false, readonly=false, selected=new Set(), undoStack=[], redoStack=[], confirmAction=null;
const pageSize=40;
const matrixKey=(v,o)=>JSON.stringify([v,o]);
const modelName = r => [r.brand,r.series,r.generation,r.subgeneration,r.year && r.year+'款'].filter(Boolean).join(' ');
function display(table,id) {const r=data[table]?.find(x=>x.id===id); return r ? (table==='models' ? modelName(r) : r.name) : id;}
function referenceName(table,id) {const r=data[table]?.find(x=>x.id===id); return r ? (table==='models' ? modelName(r) : [r.name,display('models',r.model_id)].filter(Boolean).join(' · ')) : id;}
function option(value,text,current) {return `<option value="${esc(value)}" ${current===value?'selected':''}>${esc(text)}</option>`;}
function notice(text='',error=false) {$('#notice').textContent=text; $('#notice').classList.toggle('error-notice',error);}
async function api(path,body) {
  const response=await fetch('api/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  const result=await response.json(); if(!response.ok) throw Error(result.error||'请求失败'); return result;
}
// Disable all live inputs for the entire request, including dialogs. Never let a save response replace newer edits.
function flags() {
  document.querySelectorAll('[data-request-disabled]').forEach(e=>{e.disabled=e.dataset.requestDisabled==='true';delete e.dataset.requestDisabled;});
  $('#save').disabled=readonly||!dirty||!data;
  $('#publish').disabled=readonly||!data;
  $('#import').disabled=readonly||!data;
  $('#export').disabled=$('#template').disabled=!data;
  $('#undo').disabled=readonly||!undoStack.length; $('#redo').disabled=readonly||!redoStack.length;
  $('#status').textContent=busy?'正在处理…':readonly?'维护中 · 只读':dirty?'有未保存的修改':`草稿 r${revision} · 已保存`;
  document.body.setAttribute('aria-busy',String(busy));
  if(busy) document.querySelectorAll('button,input,select,textarea').forEach(e=>{e.dataset.requestDisabled=String(e.disabled);e.disabled=true;});
}
async function run(fn) {
  if(busy)return; busy=true;flags();
  try {await fn();} catch(e) {if($('#dialog').open)$('#dialog-error').textContent=e.message; else notice(e.message,true);}
  finally {busy=false;flags();}
}
function mutate(fn) {
  if(readonly)return;
  const before=JSON.stringify(data);fn();const after=JSON.stringify(data);
  if(before!==after) {undoStack.push(before);while(undoStack.length>20||undoStack.reduce((n,s)=>n+s.length,0)>8_000_000)undoStack.shift();redoStack=[];}
  dirty=after!==savedJSON;render();
}
function accept(s) {
  data=s.draft.data; revision=s.draft.revision;published=s.published;history=s.history;
  savedJSON=JSON.stringify(data);dirty=false;undoStack=[];redoStack=[];selected.clear();
  if(s.csrf)csrf=s.csrf;if('readonly' in s)readonly=s.readonly;
  const publishedBadge=$('#published');
  publishedBadge.hidden=!published.revision;
  publishedBadge.textContent=published.revision?`已发布配置 v${published.revision}`:'';
  render();
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
  if(model&&!data.models.some(r=>r.id===model))model='';
  $('#model').innerHTML=option('','全部车型',model)+data.models.map(r=>option(r.id,modelName(r)+(r.status==='停用'?' · 停用':''),model)).join('');
  $('#year').innerHTML=option('','全部年款',year)+[...new Set(data.models.map(r=>r.year).filter(Boolean))].sort().reverse().map(y=>option(y,y+'款',year)).join('');
  $('#status-filter-label').hidden=!['models','versions','options','matrix'].includes(view);
  $('#search').placeholder=view==='matrix'?'配置项目、类别或编号':'名称、品牌或编号';
}
function actionButton(action,text) {return `<button data-action="${action}" ${readonly?'disabled':''}>${text}</button>`;}
function switchView(nextTab,nextView=nextTab) {tab=nextTab;view=nextView;selected.clear();page=1;render();}
function render() {
  if(!data)return;filters();
  document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-current',b.dataset.tab===tab?'page':'false');});
  $('#title').textContent=names[view];
  const tabs=tab==='matrix'?['matrix','versions','options']:tab==='models'?['models','phases']:[];
  $('#view-tabs').innerHTML=tabs.map(t=>`<button data-view="${t}" class="${t===view?'active':''}" aria-pressed="${t===view}">${names[t]}</button>`).join('');
  $('#view-tabs').hidden=!tabs.length;
  $('#actions').innerHTML=view==='matrix'?actionButton('add:versions','新增版本')+actionButton('add:options','新增配置'):view==='history'?'':(['versions','options'].includes(view)?actionButton('batch-add:'+view,'批量新增'):'')+actionButton('add:'+view,'新增'+names[view]);
  if(view==='matrix')renderMatrix();else if(view==='history')renderHistory();else renderTable();
  flags();
}
function paginator(total) {
  const pages=Math.max(1,Math.ceil(total/pageSize));page=Math.min(page,pages);
  $('#pagination').innerHTML=total>pageSize?`<span>共 ${total} 条 · 第 ${page} / ${pages} 页</span><button data-page="${page-1}" ${page===1?'disabled':''}>上一页</button><button data-page="${page+1}" ${page===pages?'disabled':''}>下一页</button>`:'';
  return [(page-1)*pageSize,page*pageSize];
}
function checkbox(id,label) {return `<input type="checkbox" class="row-check" data-select="${esc(id)}" aria-label="${esc(label)}" ${selected.has(id)?'checked':''} ${readonly?'disabled':''}>`;}
function selectionToolbar(records,extra='') {
  const visibleIds=new Set(records.map(r=>r.id));selected=new Set([...selected].filter(id=>visibleIds.has(id)));
  const all=records.length>0&&selected.size===records.length;
  $('#bulkbar').innerHTML=`<label class="check-label"><input id="select-all" type="checkbox" ${all?'checked':''} ${readonly||!records.length?'disabled':''}>全选筛选结果</label><span id="selection-count">已选 ${selected.size} 项</span>${extra}`;
  const selectAll=$('#select-all');selectAll.indeterminate=selected.size>0&&!all;
  selectAll.onchange=()=>{if(busy||readonly)return;selected=new Set(selectAll.checked?records.map(r=>r.id):[]);render();};
}
function renderTable() {
  const records=scopedRows(view);const range=paginator(records.length);
  $('#count').textContent=`${records.length} 条记录`;
  let extra='';if(['models','versions','options'].includes(view))extra=`<label class="inline-label">批量状态<select id="batch-status"><option>规划</option><option>在售</option><option>停用</option></select></label><button id="apply-status" ${readonly||!selected.size?'disabled':''}>应用状态</button>`;
  if(!extra){$('#bulkbar').innerHTML='';$('#bulkbar').hidden=true;}else{$('#bulkbar').hidden=false;selectionToolbar(records,extra);$('#apply-status').disabled=readonly||!selected.size;$('#apply-status').onclick=batchStatus;}
  const columns=view==='models'?['brand','series','generation','subgeneration','year','energy','status']:fields[view];
  const selectable=!!extra;
  $('#content').innerHTML=records.length?`<div class="scroll"><table><thead><tr>${selectable?'<th class="check-cell"></th>':''}${columns.map(f=>`<th>${labels[f]}</th>`).join('')}<th class="operation">操作</th></tr></thead><tbody>${records.slice(...range).map(r=>`<tr class="${selected.has(r.id)?'selected':''}">${selectable?`<td class="check-cell">${checkbox(r.id,'选择 '+(r.name||modelName(r)))}</td>`:''}${columns.map(f=>`<td>${tableValue(r,f)}</td>`).join('')}<td class="operation"><button class="link" data-edit="${view}:${esc(r.id)}" ${readonly?'disabled':''}>编辑</button>${view==='models'?`<button class="link" data-copy="${esc(r.id)}" ${readonly?'disabled':''}>复制年款</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty"><strong>${data[view].length?'没有符合筛选的记录':'暂无'+names[view]}</strong>${data[view].length?'<button data-clear>清空筛选</button>':actionButton('add:'+view,'新增'+names[view])}</div>`;
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
  const {versions,options,index}=matrixRows(),range=paginator(options.length);
  $('#count').textContent=`${options.length} 项配置 · ${versions.length} 个版本`;
  $('#bulkbar').hidden=false;
  selectionToolbar(options,`<label class="inline-label">类别<select id="category-filter">${option('','全部类别',categoryFilter)+categories.map(c=>option(c,c,categoryFilter)).join('')}</select></label><label class="inline-label">配置状态<select id="state-filter">${option('','全部状态',stateFilter)+states.map(s=>option(s,s,stateFilter)).join('')}</select></label><button id="batch-matrix" ${readonly||!selected.size||!versions.length?'disabled':''}>批量设置</button>`);
  $('#batch-matrix').disabled=readonly||!selected.size||!versions.length;$('#batch-matrix').onclick=batchMatrix;
  $('#category-filter').onchange=e=>{categoryFilter=e.target.value;selected.clear();page=1;render();};
  $('#state-filter').onchange=e=>{stateFilter=e.target.value;selected.clear();page=1;render();};
  $('#content').innerHTML=versions.length&&options.length?`<div class="scroll matrix-scroll"><table class="matrix-table"><thead><tr><th class="check-cell"></th><th class="sticky-col">配置项目</th>${versions.map(v=>`<th>${esc(v.name)}<small>${esc(display('models',v.model_id))}</small></th>`).join('')}</tr></thead><tbody>${options.slice(...range).map(o=>`<tr class="${selected.has(o.id)?'selected':''}"><td class="check-cell">${checkbox(o.id,'选择 '+o.name)}</td><td class="sticky-col"><small>${esc(o.category)}</small>${esc(o.name)}</td>${versions.map(v=>{
    if(v.model_id!==o.model_id)return '<td class="inapplicable">—</td>';
    const state=index.get(matrixKey(v.id,o.id))?.state||'未配置';
    return `<td><select class="state-${states.indexOf(state)}" data-v="${esc(v.id)}" data-o="${esc(o.id)}" aria-label="${esc(v.name+' '+o.name)}" ${readonly?'disabled':''}>${states.map(s=>option(s,s,state)).join('')}</select></td>`;
  }).join('')}</tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>暂无符合筛选的配置关系</strong></div>';
}
function setMatrix(versionId,optionId,state,index=null) {
  let row=index?index.get(matrixKey(versionId,optionId)):data.matrix.find(r=>r.version_id===versionId&&r.option_id===optionId);
  if(!row){if(state==='未配置')return;row={id:newId(),version_id:versionId,option_id:optionId};data.matrix.push(row);if(index)index.set(matrixKey(versionId,optionId),row);}row.state=state;
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
  };
  $('#dialog-body').onchange=impact;impact();
}

function batchStatus() {
  const rows=scopedRows(view).filter(r=>selected.has(r.id)),status=$('#batch-status').value;
  modal('批量修改状态',`<div class="selection-summary">${rows.length} 条记录 → ${esc(status)}</div><ul class="record-list">${rows.map(r=>`<li>${esc(r.name||modelName(r))}</li>`).join('')}</ul>`,()=>{mutate(()=>rows.forEach(r=>r.status=status));selected.clear();render();notice(`已修改 ${rows.length} 条记录，尚未保存`);},'应用状态');
}
function renderHistory() {
  $('#bulkbar').hidden=true;
  const records=history.filter(r=>!query||[r.id,r.kind,r.actor,r.time].join(' ').toLowerCase().includes(query.toLowerCase())),range=paginator(records.length);
  $('#count').textContent=`${records.length} 条记录`;
  $('#content').innerHTML=records.length?`<div class="scroll"><table><thead><tr><th>记录</th><th>操作</th><th>操作人</th><th>时间</th><th></th></tr></thead><tbody>${records.slice(...range).map(h=>`<tr><td>#${h.id}</td><td>${esc(h.kind)}</td><td>${esc(h.actor)}</td><td>${esc(new Date(h.time).toLocaleString())}</td><td><button class="link" data-history="${h.id}">差异与恢复</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>暂无变更记录</strong></div>';
}
function modal(title,html,action,label='确定') {
  $('#dialog-body').onchange=null;$('#dialog-title').textContent=title;$('#dialog-body').innerHTML=html;$('#dialog-error').textContent='';confirmAction=action;
  $('#confirm').textContent=label;$('#confirm').hidden=!action;$('#confirm').disabled=false;
  if(!$('#dialog').open)$('#dialog').showModal();flags();
}
function formSelect(field,values,current,mandatory=false) {return `<select name="${field}" ${mandatory?'required':''}>${option('','请选择',current)+values.map(([id,text])=>option(id,text,current)).join('')}</select>`;}
function edit(table,id,copy=false) {
  const source=data[table].find(r=>r.id===id),record=structuredClone(source||{id:newId(),status:'规划',model_id:model||scopedRows('models',false)[0]?.id||'',target_type:'models',category:'外观'});
  if(copy){record.id=newId();record.year='';}
  const mandatory=new Set(required[table]);
  modal(copy?'复制年款':(source?'编辑':'新增')+names[table],`<form id="edit-form" class="form">${fields[table].map(f=>{
    let control;
    if(f==='members') {const set=new Set((record.members||'').split(','));control=`<div class="members">${data.models.map(m=>`<label><input type="checkbox" name="member" value="${esc(m.id)}" ${set.has(m.id)?'checked':''}>${esc(modelName(m))}</label>`).join('')}</div>`;}
    else if(f==='status')control=formSelect(f,['规划','在售','停用'].map(x=>[x,x]),record[f],true);
    else if(f==='model_id')control=formSelect(f,data.models.map(r=>[r.id,modelName(r)]),record[f],true);
    else if(f==='target_type')control=formSelect(f,['models','versions','options'].map(x=>[x,names[x]]),record[f],true);
    else if(f==='target_id')control=formSelect(f,(data[record.target_type]||[]).map(r=>[r.id,referenceName(record.target_type,r.id)]),record[f],true);
    else if(f==='category')control=formSelect(f,categories.map(x=>[x,x]),record[f],true);
    else control=`<input name="${f}" value="${esc(record[f]||'')}" type="${f.endsWith('_date')?'date':'text'}" ${mandatory.has(f)?'required':''} maxlength="4000">`;
    return `<label ${f==='members'?'class="wide"':''}>${labels[f]}${mandatory.has(f)?'<span class="required" aria-hidden="true">*</span>':''}${control}</label>`;
  }).join('')}<div class="wide muted">编号：${esc(record.id)}</div></form>`,values=>{
    const clean={id:record.id};for(const f of fields[table])clean[f]=f==='members'?values.getAll('member').join(','):String(values.get(f)||'').trim();
    if(table==='groups'&&!clean.members)throw Error('请选择汇总成员');
    for(const f of mandatory)if(!clean[f])throw Error('请填写'+labels[f]);
    const rows=data[table].filter(r=>r.id!==clean.id);
    if(rows.some(r=>table==='models'?['brand','series','generation','subgeneration','year'].every(f=>(r[f]||'')===(clean[f]||'')):table==='aliases'?r.source===clean.source&&r.name===clean.name:table==='groups'?r.name===clean.name:r.name===clean.name&&r.model_id===clean.model_id&&(table!=='options'||r.category===clean.category)))throw Error('同一范围内名称已存在');
    mutate(()=>{
      if(copy){data.models.push(clean);const mapping={};for(const t of ['versions','options','phases'])for(const r of data[t].filter(x=>x.model_id===source.id)){const next={...r,id:newId(),model_id:clean.id};mapping[r.id]=next.id;if(t==='phases'){next.presale_date='';next.sale_date='';}data[t].push(next);}for(const r of data.matrix.filter(x=>mapping[x.version_id]&&mapping[x.option_id]))data.matrix.push({...r,id:newId(),version_id:mapping[r.version_id],option_id:mapping[r.option_id]});model=clean.id;year='';}
      else if(source)Object.assign(source,clean);else data[table].push(clean);
    });
  });
  $('#edit-form').onsubmit=e=>{e.preventDefault();$('#confirm').click();};
  if(table==='aliases')$('[name="target_type"]').onchange=e=>{$('[name="target_id"]').innerHTML=option('','请选择','')+(data[e.target.value]||[]).map(r=>option(r.id,referenceName(e.target.value,r.id),'')).join('');};
}
function batchAdd(table) {
  modal('批量新增'+names[table],`<form id="batch-form" class="form"><label>车型${formSelect('model_id',data.models.map(r=>[r.id,modelName(r)]),model||scopedRows('models',false)[0]?.id,true)}</label>${table==='options'?`<label>类别${formSelect('category',categories.map(c=>[c,c]),'外观',true)}</label>`:''}<label class="wide">名称（每行一项）<textarea name="names" rows="9" required maxlength="20000"></textarea></label></form>`,values=>{
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
  const snapshot=structuredClone(data),baseRevision=revision,result=await api('preview',{data:snapshot});
  modal('发布配置',`<p class="error">${esc(result.errors.join('\n'))}</p>${differences(result.changes)}`,result.errors.length||!result.changes.length?null:async()=>{accept(await api('publish',{data:snapshot,revision:baseRevision}));notice('配置已发布');},'确认发布');
}
async function download(path,body,filename) {
  const response=await fetch('api/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok)throw Error((await response.json()).error);
  const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function openExport() {
  modal('导出 Excel',`<div class="export-options"><label><input type="radio" name="export-source" value="editor" checked>当前编辑内容${dirty?' · 含未保存修改':''}</label><label><input type="radio" name="export-source" value="published" ${published.revision?'':'disabled'}>已发布配置${published.revision?' · v'+published.revision:' · 尚未发布'}</label></div>`,async()=>{const source=$('[name="export-source"]:checked').value;await download('export',{data:source==='published'?published.data:data},source==='published'?`车型配置-已发布-v${published.revision}.xlsx`:'车型配置-当前编辑.xlsx');},'下载 Excel');
}
function fileBase64(file) {return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('读取文件失败：'+file.name));reader.readAsDataURL(file);});}
async function importFiles(files) {
  if(!files.length)return;
  if(files.length>10)throw Error('每批最多 10 个 Excel 文件');
  if(files.some(f=>!f.name.toLowerCase().endsWith('.xlsx')))throw Error('请选择 .xlsx 文件');
  if(files.reduce((n,f)=>n+f.size,0)>5_500_000)throw Error('本批文件总大小不能超过 5.5 MB');
  const payload=await Promise.all(files.map(async f=>({name:f.name,content:await fileBase64(f)})));
  const result=await api('import-batch',{files:payload,revision});
  const summary=result.summary||{},issues=result.issues||[];
  const issueHTML=issues.length?`<div class="scroll"><table class="diff"><thead><tr><th>文件 / 工作表</th><th>位置</th><th>问题</th></tr></thead><tbody>${issues.map(i=>`<tr><td>${esc(i.file)}<small>${esc(i.sheet)}</small></td><td>${i.row?'第 '+esc(i.row)+' 行':''} ${esc(i.field||'')}</td><td class="error">${esc(i.message)}</td></tr>`).join('')}</tbody></table></div>`:`<p class="error">${esc((result.errors||[]).join('\n'))}</p>`;
  modal('导入预览',`<div class="file-list">${files.map(f=>`<span>${esc(f.name)}</span>`).join('')}</div><div class="import-summary"><span>新增 <b>${summary.added??0}</b></span><span>更新 <b>${summary.updated??0}</b></span><span>未变更 <b>${summary.unchanged??0}</b></span><span>问题 <b>${(result.errors||[]).length||issues.length}</b></span></div>${issueHTML}${differences(result.changes||[])}`,result.errors?.length||issues.length||!result.changes?.length?null:async()=>{
    const fresh=await api('state');if(fresh.draft.revision!==result.revision||revision!==result.revision)throw Error('草稿已被其他人修改，请重新载入后再次导入');
    mutate(()=>{data=result.data;});notice('导入内容已应用，尚未保存');
  },'应用到编辑区');
}
function requestImport() {$('#file').click();}
$('#confirm').onclick=()=>{
  const form=$('#dialog-body form');if(form&&!form.reportValidity())return;
  const values=form?new FormData(form):null;
  run(async()=>{if(confirmAction){$('#dialog-error').textContent='';const done=await confirmAction(values);if(done!==false)$('#dialog').close();}});
};
$('#close').onclick=$('#cancel').onclick=()=>{if(!busy)$('#dialog').close();};
$('#dialog').addEventListener('cancel',e=>{if(busy)e.preventDefault();});
document.addEventListener('click',e=>{
  if(busy)return;const b=e.target.closest('button');if(!b)return;
  if(b.dataset.tab)switchView(b.dataset.tab);
  if(b.dataset.view){view=b.dataset.view;selected.clear();page=1;render();}
  if(b.dataset.action&&!readonly){const [action,t]=b.dataset.action.split(':');if(action==='add')edit(t);else if(action==='batch-add')batchAdd(t);}
  if(b.dataset.edit&&!readonly){const [t,id]=b.dataset.edit.split(':');edit(t,id);}
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
  const id=e.target.dataset.select;if(id){e.target.checked?selected.add(id):selected.delete(id);render();return;}
  const v=e.target.dataset.v,o=e.target.dataset.o;if(v){const state=e.target.value;mutate(()=>setMatrix(v,o,state));}
};
function clearFilters() {model=year=query=statusFilter=categoryFilter=stateFilter='';$('#search').value='';$('#status-filter').value='';selected.clear();page=1;render();}
$('#clear-filters').onclick=clearFilters;
for(const [id,assign] of [['model',v=>model=v],['year',v=>year=v],['status-filter',v=>statusFilter=v]])$('#'+id).onchange=e=>{if(busy)return;assign(e.target.value);selected.clear();page=1;render();};
$('#search').oninput=e=>{if(busy)return;query=e.target.value.trim();selected.clear();page=1;render();};
$('#save').onclick=()=>run(saveDraft);$('#publish').onclick=()=>run(previewPublish);
$('#reload').onclick=()=>{const load=async()=>{accept(await api('state'));notice();};if(dirty)modal('放弃未保存修改？','<div class="selection-summary">重新载入服务器草稿</div>',load,'重新载入');else run(load);};
$('#undo').onclick=()=>{if(busy||readonly||!undoStack.length)return;redoStack.push(JSON.stringify(data));data=JSON.parse(undoStack.pop());dirty=JSON.stringify(data)!==savedJSON;selected.clear();render();notice('已撤销');};
$('#redo').onclick=()=>{if(busy||readonly||!redoStack.length)return;undoStack.push(JSON.stringify(data));data=JSON.parse(redoStack.pop());dirty=JSON.stringify(data)!==savedJSON;selected.clear();render();notice('已重做');};
$('#export').onclick=openExport;$('#template').onclick=()=>run(()=>download('template',undefined,'车型配置-导入模板.xlsx'));
$('#import').onclick=requestImport;
$('#file').onchange=e=>{
  const files=[...e.target.files];e.target.value='';if(!files.length)return;
  if(dirty)modal('保存草稿后导入','<div class="selection-summary">当前编辑有未保存的修改</div>',async()=>{await saveDraft();await importFiles(files);return false;},'保存并预览导入');
  else run(()=>importFiles(files));
};
window.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();if(!busy&&!readonly&&dirty&&!$('#dialog').open)run(saveDraft);}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
run(async()=>accept(await api('state')));
