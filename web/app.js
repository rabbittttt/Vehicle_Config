'use strict';
const $ = s => document.querySelector(s);
const frameAccount = window.vehicleConfigFrameAccount;
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sections = {
  schedule: ['schedule'],
  generation: ['gen_names', 'merge_groups'],
  options: ['battery_rules', 'option_names', 'option_prices'],
  reporting: [...Object.keys(campaignEditor.reportMetrics), 'businessTotals'],
  history: ['campaign_history', 'rule_history'],
};
const viewLabels = {
  ...orderRules.titles,
  ...campaignEditor.reportMetrics, dailyOrders:'小订进度', businessTotals:'结果核对', campaign_history:'日程与填报发布',
};
let tab='schedule', view=sections.schedule[0], csrf='', readonly=true, busy=false, confirmAction=null;
let sessionUser = '';
let readonlyStatus = '维护中 · 只读';
let page=1, model='', year='';
let scheduleToolsAvailable=false;
const pageSize=40;
const queries={};
const searchValue=()=>queries[tab] || '';
const isReportingView=()=>tab==='reporting';
const isCampaignView=()=>tab==='schedule' || isReportingView();
const isRuleView=()=>Object.hasOwn(orderRules.titles,view);
const activeEditor=()=>isRuleView()?orderRules:campaignEditor;
const publishGroups = [
  {title:'车型与日程、小订首销填报', editor:campaignEditor},
  {title:'代际规则、电池与选配', editor:orderRules},
];
const modelYear=name=>String(name ?? '').match(/(?:19|20)\d{2}(?=\s*款)/)?.[0] || '';
function matchesModelAndYear(primary,secondary) {
  return (!model || primary===model) && (!year || modelYear(primary)===year || modelYear(secondary)===year);
}
function newId() {
  if(crypto.randomUUID)return crypto.randomUUID();
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}
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
function serviceFetch(path,body) {
  const headers = body===undefined ? {} : {'Content-Type':'application/json','X-CSRF-Token':csrf};
  if (window.parent !== window) {
    const identity = frameAccount?.current;
    if (!identity) throw Error('等待门户账号。');
    headers['Sop-Frame-Identity'] = encodeURIComponent(JSON.stringify(identity));
  }
  return fetch('api/'+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});
}
async function api(path,body) {
  const response=await serviceFetch(path,body);
  const result=await response.json();
  if(!response.ok) throw Object.assign(new Error(result.error||'请求失败'),{status:response.status});
  return result;
}
async function writeApi(path, body) {
  if (readonly || !csrf) throw Error('当前无法写入');
  if (sessionUser) return api(path, body);
  const owner = ($('#dialog[open] #write-owner') || $('#owner')).value.trim();
  if (!owner) throw Error('请填写维护人');
  const result = await api(path, {...body, owner});
  $('#owner').value = owner;
  try { localStorage.setItem('vehicle-config-owner', owner); } catch { /* The save has succeeded. */ }
  return result;
}
// Disable all live inputs for the entire request, including dialogs. Never let a save response replace newer edits.
function flags() {
  document.querySelectorAll('[data-request-disabled]').forEach(e=>{e.disabled=e.dataset.requestDisabled==='true';delete e.dataset.requestDisabled;});
  const editor=activeEditor(), ready=editor.ready(), history=view==='campaign_history';
  $('#save').disabled=readonly || history || !ready || !editor.isDirty();
  $('#publish').disabled=readonly || !csrf || !publishGroups.some(({editor})=>!editor.ready() || editor.hasPendingPublish());
  for(const id of ['save','undo','redo']) $('#'+id).hidden=history;
  for(const id of ['template','import','export']) $('#'+id).hidden=tab==='history';
  $('#import').disabled=readonly || !ready;
  $('#export').disabled=$('#template').disabled=!ready;
  $('#clear-filters').disabled=!searchValue();
  $('#campaign-import').hidden=$('#reload-rules').hidden=readonly;
  $('#campaign-import').disabled=$('#reload-rules').disabled=readonly || !csrf;
  $('#campaign-undo-import').hidden=readonly || !campaignEditor.canUndoImport();
  $('#undo').disabled=readonly || history || !editor.canUndo();
  $('#redo').disabled=readonly || history || !editor.canRedo();
  updateDraftStatus();
  setText($('#reload'),history?'刷新记录':'重新载入草稿');
  document.body.setAttribute('aria-busy',String(busy));
  if(busy) document.querySelectorAll('button,input,select,textarea').forEach(e=>{e.dataset.requestDisabled=String(e.disabled);e.disabled=true;});
}
let showProgress=false, progressLabel='正在载入…';
function updateDraftStatus() {
  let state;
  if (showProgress) state=progressLabel;
  else if (!csrf) state=busy?'正在载入…':'未载入';
  else if (readonly) state=readonlyStatus;
  else if (publishGroups.some(({editor})=>editor.isDirty())) state='有未保存修改';
  else if (publishGroups.some(({editor})=>!editor.ready())) state='草稿未完整载入';
  else if (publishGroups.some(({editor})=>editor.hasPendingPublish())) state='待发布';
  else state='无待发布变更';
  if (!showProgress && campaignEditor.hasPendingProjection()) state+=' · 报表数据待同步';
  const label='配置草稿 · '+state;
  setText($('#status'),label);
  $('#status').title=label;
}
async function run(fn, label='正在处理…') {
  if(busy)return;
  busy=true;
  progressLabel=label;
  const focused=document.activeElement;
  const progress=setTimeout(()=>{showProgress=true;updateDraftStatus();},180);
  flags();
  try {await fn();} catch(e) {if($('#dialog').open)$('#dialog-error').textContent=e.message; else notice(e.message,true);}
  finally {
    clearTimeout(progress);
    showProgress=false;busy=false;flags();
    // Disabling the focused input can send the keyboard to the document body.
    if (document.activeElement===document.body || document.activeElement===$('#dialog')) {
      if ($('#dialog').open) {
        const owner=$('#write-owner');
        (owner?.required && !owner.value.trim() ? owner : $('#cancel')).focus({preventScroll:true});
      } else if (focused?.isConnected && !focused.disabled) {
        focused.focus({preventScroll:true});
      }
    }
  }
}
function renderKeepingScroll() {
  const scroll=$('#content .scroll');
  const top=scroll?.scrollTop, left=scroll?.scrollLeft;
  const active=document.activeElement?.dataset;
  const selector=active?.ruleId?`[data-rule-action="${CSS.escape(active.ruleAction)}"][data-rule-id="${CSS.escape(active.ruleId)}"]`:'';
  render();
  if(selector)$('#content')?.querySelector(selector)?.focus({preventScroll:true});
  if(top||left)$('#content .scroll')?.scrollTo(left,top);
}
function filters() {
  const names=[...new Set([...campaignEditor.modelNames(),...orderRules.modelNames()])];
  if(!names.includes(model))model='';
  updateOptions($('#model'),option('','全部车型','')+names.map(n=>option(n,n,'')).join(''),model);
  const yearNames=campaignEditor.reportStages().flatMap(stage=>[stage.model,stage.secondary]).concat(orderRules.yearNames());
  const years=[...new Set(yearNames.map(modelYear).filter(Boolean))].sort().reverse();
  if(!years.includes(year))year='';
  updateOptions($('#year'),option('','全部年款','')+years.map(y=>option(y,y+'款','')).join(''),year);
  const count=[model,year,searchValue()].filter(Boolean).length;
  setText(filterToggle,count?'筛选 · '+count:'筛选');
}
const renderedOptions=new WeakMap();
function updateOptions(select, html, value) {
  if(renderedOptions.get(select)!==html) {
    select.innerHTML=html;
    renderedOptions.set(select,html);
  }
  if(select.value!==value) select.value=value;
}
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
function switchView(nextTab, nextView=sections[nextTab]?.[0]) {
  if(!sections[nextTab]?.includes(nextView) || tab===nextTab && view===nextView)return;
  clearTimeout(searchTimer);
  cancelAnimationFrame(searchFrame);
  const content=$('#content');
  delete content.dataset.historyView;
  content.removeAttribute('aria-busy');
  tab=nextTab;
  view=nextView;
  page=1;
  $('#search').value=searchValue();
  notice();
  render();
  revealView();
  if(!activeEditor().ready() && view!=='campaign_history')run(activeEditor().load,'正在读取数据…');
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
const toolsButton = $('#data-tools');
const toolsPanel = $('#data-tools-panel');
function showDataTools(open) {
  toolsPanel.hidden = !open;
  toolsButton.setAttribute('aria-expanded', String(open));
}
toolsButton.onclick = () => showDataTools(toolsPanel.hidden);
toolsButton.parentElement.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !toolsPanel.hidden) {
    event.preventDefault();
    showDataTools(false);
    toolsButton.focus();
  }
});
toolsButton.parentElement.addEventListener('focusout', event => {
  if (!toolsButton.parentElement.contains(event.relatedTarget)) showDataTools(false);
});
document.addEventListener('click', event => {
  if (!toolsPanel.hidden && !toolsButton.parentElement.contains(event.target)) showDataTools(false);
});
const filterToggle = $('.mobile-filter-toggle');
filterToggle.onclick = () => {
  filterToggle.setAttribute('aria-expanded', String(filterToggle.getAttribute('aria-expanded') !== 'true'));
};
const viewTabs = $('#view-tabs');
function updateExcelActions() {
  const scope = isRuleView() ? '脚本规则' : isReportingView() ? '小订与首销' : '车型与日程';
  for (const [id, description] of [
    ['template', '下载' + scope + '模板（Excel）'],
    ['import', '导入' + scope + '（Excel）'],
    ['export', '导出' + scope + '（Excel）'],
  ]) {
    const button = $('#' + id);
    button.title = description;
    button.setAttribute('aria-label', id==='import'?'导入':button.title);
  }
}
const tabSizes = new ResizeObserver(entries => entries.forEach(entry => revealActiveTab(entry.target)));
tabSizes.observe(nav);
tabSizes.observe(viewTabs);
function render() {
  filters();
  if (nav.dataset.active !== tab) {
    nav.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-current',b.dataset.tab===tab?'page':'false');});
    nav.dataset.active = tab;
    viewTabs.hidden = sections[tab].length === 1;
    viewTabs.innerHTML = viewTabs.hidden ? '' : sections[tab].map(t => `<button id="view-${t}" data-view="${t}">${viewLabels[t]}</button>`).join('');
    revealActiveTab(nav);
  }
  if (viewTabs.dataset.active !== view) {
    updateExcelActions();
    for (const button of viewTabs.children) {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
    viewTabs.dataset.active = view;
    viewTabs.closest('section').setAttribute('aria-labelledby',viewTabs.hidden ? 'tab-'+tab : 'view-'+view);
    revealActiveTab(viewTabs);
  }
  if(isCampaignView()) campaignEditor.render(view,searchValue());
  else if(isRuleView()) orderRules.render(view,searchValue());
  else {
    $('#actions').replaceChildren();
    renderHistory(searchValue());
  }
  flags();
}
function paginator(total, rowsPerPage=pageSize) {
  const pages=Math.max(1,Math.ceil(total/rowsPerPage));page=Math.min(page,pages);
  $('#pagination').innerHTML=pages>1?`<span>第 ${page} / ${pages} 页</span><button data-page="${page-1}" ${page===1?'disabled':''}>上一页</button><button data-page="${page+1}" ${page===pages?'disabled':''}>下一页</button>`:'';
  return [(page-1)*rowsPerPage,page*rowsPerPage];
}
let historyRequest=0;
async function renderHistory(search=searchValue(), warning='') {
  const request=++historyRequest, requestedView=view;
  const rules=requestedView==='rule_history';
  const path=rules?'rules/history':'campaign/history';
  const content=$('#content');
  if(!rules && !scheduleToolsAvailable) {
    $('#count').textContent='';
    $('#pagination').replaceChildren();
    content.textContent='重启车型配置服务后可查看发布记录。';
    return;
  }
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
      const link=rules?`<td><button class="link" data-rule-action="history" data-rule-id="${esc(record.id)}">差异与恢复</button></td>`:'';
      return `<tr><td>#${esc(record.id)}</td><td>${esc(record.kind)}</td><td>${esc(record.actor)}</td><td>${esc(new Date(record.time).toLocaleString())}</td>${link}</tr>`;
    });
    $('#content').innerHTML=warning+(rows.length?`<div class="scroll"><table><thead><tr><th>记录</th><th>操作</th><th>操作人</th><th>时间</th>${rules?'<th>操作</th>':''}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`:`<div class="empty"><strong>${rules?'暂无变更记录':'暂无发布记录'}</strong></div>`);
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
  if(toolsPanel.contains(button)){lastOpener='#data-tools';return;}
  const attrs=button.getAttributeNames().filter(name=>name.startsWith('data-')&&name!=='data-request-disabled');
  lastOpener=button.id?'#'+CSS.escape(button.id):attrs.map(name=>`[${name}="${CSS.escape(button.getAttribute(name))}"]`).join('');
},true);
$('#dialog').addEventListener('close',()=>{
  if($('#dialog').open||document.activeElement!==document.body&&!$('#dialog').contains(document.activeElement))return;
  const opener=returnFocus&&$(returnFocus);
  (opener?.getClientRects().length&&!opener.disabled?opener:$('#view-tabs .active') || nav.querySelector('.active'))?.focus({preventScroll:true});
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
    const controls=[...form.querySelectorAll('input:not([type=checkbox],[type=hidden],:disabled),select:not(:disabled),textarea:not(:disabled)')];
    (controls.find(control=>control.required&&!control.value)||controls[0])?.focus();
  }
}
function writeModal(title,html,action,label) {
  if (action) {
    const owner = sessionUser || $('#owner').value.trim();
    html += '<label>本次维护人<input id="write-owner" maxlength="100" value="' + esc(owner) + '" ' + (sessionUser?'readonly':'required') + '></label>';
  }
  modal(title,html,action,label);
  if (action && !sessionUser && !$('#write-owner').value) $('#write-owner').focus();
}

async function reviewPublication(groups=publishGroups, completed=[], previousOwner='') {
  const prepared = await Promise.all(groups.map(async group => {
    try {
      if (!group.editor.ready()) await group.editor.load();
      const preview = await group.editor.preparePublish();
      return preview ? {...preview, group} : null;
    } catch (error) {
      return {group, html:`<p class="error">${esc(error.message)}</p>`, publish:null};
    }
  }));
  const pending = prepared.filter(Boolean);
  const sections = pending.map(({group,html})=>`<section><h3>${esc(group.title)}</h3>${html}</section>`).join('');
  const receipt = completed.map(result=>`<p>${esc(result.message)}</p>`).join('');
  if (!pending.length) {
    modal('发布', receipt + '<p>当前没有待发布的变更。</p>');
    $('#cancel').textContent='关闭';
    return;
  }

  async function publish() {
    const owner = $('#write-owner').value.trim();
    if (!owner) {
      throw Error('请填写维护人');
    }
    const results = [...completed];
    for (const [index, item] of pending.entries()) {
      try {
        const warning = await item.publish();
        results.push({message:item.group.title+'已发布'+(warning?'。'+warning:''), warning});
      } catch (error) {
        const remaining = pending.slice(index).map(step=>step.group);
        const details = [
          ...results.map(result=>`<p>${esc(result.message)}</p>`),
          `<p class="error">${esc(item.group.title)}：${esc(error.message)}</p>`,
          ...remaining.slice(1).map(group=>`<p>${esc(group.title)}尚未执行</p>`),
        ].join('');
        modal('发布结果', details, async () => {
          await reviewPublication(remaining, results, owner);
          return false;
        }, '重新核对');
        $('#cancel').textContent='关闭';
        return false;
      }
    }
    notice(results.map(result=>result.message).join('。'), results.some(result=>result.warning));
    if (tab==='history') await renderHistory();
  }
  const valid = pending.every(item=>item.publish);
  writeModal('核对发布变更', receipt+sections, valid ? publish : null, '确认发布');
  if (valid && previousOwner && !sessionUser) $('#write-owner').value=previousOwner;
  if (!valid) $('#cancel').textContent='关闭';
}
function formSelect(field,values,current,mandatory=false) {return `<select name="${field}" ${mandatory?'required':''}>${option('','请选择',current)+values.map(([id,text])=>option(id,text,current)).join('')}</select>`;}
async function download(path,body,filename) {
  const response=await serviceFetch(path,body);
  if(!response.ok)throw Error((await response.json()).error);
  const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function fileBase64(file) {return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('读取文件失败：'+file.name));reader.readAsDataURL(file);});}
async function encodeWorkbooks(files) {
  if(files.length>10)throw Error('每批最多 10 个 Excel 文件');
  if(files.some(f=>!f.name.toLowerCase().endsWith('.xlsx')))throw Error('请选择 .xlsx 文件');
  if(files.reduce((n,f)=>n+f.size,0)>5_500_000)throw Error('本批文件总大小不能超过 5.5 MB');
  return Promise.all(files.map(async f=>({name:f.name,content:await fileBase64(f)})));
}
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
  if(toolsPanel.contains(b)){
    showDataTools(false);
    toolsButton.focus({preventScroll:true});
  }
  if(b.id==='reload-rules' || b.hasAttribute('data-reload-rules'))return orderRules.syncFromScript();
  if(b.id==='campaign-import')return campaignEditor.selectLegacy();
  if(b.id==='campaign-undo-import')return campaignEditor.undoImport();
  if(b.hasAttribute('data-runtime'))return run(async()=>{
    const info=await api('runtime');
    const rows=[['数据目录',info.data_dir,info.sources.data_dir],['旧车型档案库',info.catalog],
      ['规则数据库',info.rules_db],['小订与首销数据库',info.campaign_db],['规则脚本',info.shared_config||'未配置',info.sources.shared_config],
      ['脚本状态',info.script_exists?'文件存在':'文件未找到'],['Excel 导出',info.excel]];
    if(info.release.version)rows.push(['运行包版本',info.release.version],['构建提交',info.release.gitCommit||'旧包未记录',info.release.sourceDirty?'含未提交修改':'']);
    modal('运行路径','<dl>'+rows.map(([label,value,source])=>`<dt>${esc(label)}</dt><dd>${esc(value)} ${source?'（'+esc(source)+'）':''}</dd>`).join('')+'</dl>');
  });
  if(b.dataset.tab)return switchView(b.dataset.tab);
  if(b.dataset.view)return switchView(tab,b.dataset.view);
  if(b.dataset.campaignAction && isCampaignView())return campaignEditor.handle(b);
  if(b.dataset.ruleAction && isRuleView())return orderRules.handleClick(b,view);
  if(b.dataset.page){page=Number(b.dataset.page);render();}
  if(b.hasAttribute('data-clear'))clearSearch();
});
function clearSearch() {
  queries[tab]='';
  $('#search').value='';
  page=1;
  render();
}
$('#clear-filters').onclick=clearSearch;
$('#model').onchange=e=>{model=e.target.value;page=1;render();};
$('#year').onchange=e=>{year=e.target.value;page=1;render();};
let searchTimer, searchFrame;
function updateSearch() {
  if(busy)return;
  const value=$('#search').value.trim();
  queries[tab]=value;
  page=1;
  clearTimeout(searchTimer);
  cancelAnimationFrame(searchFrame);
  if (tab==='history') {
    historyRequest++;
    searchTimer=setTimeout(render,120);
  } else {
    searchFrame=requestAnimationFrame(render);
  }
}
$('#search').oninput=e=>{if(!e.isComposing)updateSearch();};
$('#search').addEventListener('compositionend',updateSearch);
$('#save').onclick=()=>{
  if (!sessionUser && !$('#owner').value.trim()) writeModal('保存草稿','',activeEditor().save,'保存草稿');
  else run(activeEditor().save,'正在保存草稿…');
};
$('#error-detail').onclick=()=>modal('操作未完成',`<p class="error">${esc($('#error-notice').textContent)}</p>`);
$('#publish').onclick=()=>run(()=>reviewPublication(),'正在核对发布差异…');
$('#reload').onclick=()=>!csrf?run(start):view==='campaign_history'?renderHistory():activeEditor().reload();
$('#undo').onclick=()=>activeEditor().undo();
$('#redo').onclick=()=>activeEditor().redo();
$('#export').onclick=()=>{
  if(tab==='schedule' && !scheduleToolsAvailable) return notice('重启车型配置服务后可导出 A–N 列。',true);
  run(activeEditor().openExport,'正在导出…');
};
$('#template').onclick=()=>run(()=>{
  if(isRuleView()) return orderRules.template();
  const name=isReportingView()?'小订首销填报':'车型与日程';
  return download('campaign/template?scope='+tab,undefined,name+'-导入模板.xlsx');
});
$('#import').onclick=()=>{
  $('#file').multiple=isRuleView();
  $('#file').click();
};
$('#file').onchange=e=>{
  const files=[...e.target.files];e.target.value='';
  if(files.length)run(()=>isCampaignView()?campaignEditor.importModule(files,tab):orderRules.importFiles(files),'正在预览导入…');
};
$('#campaign-file').onchange=event=>{
  const files=[...event.target.files];event.target.value='';
  if(files.length) run(()=>campaignEditor.importFiles(files),'正在比对旧表与草稿…');
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
window.addEventListener('beforeunload',e=>{
  if(frameAccount && !frameAccount.current)return;
  if(orderRules.isDirty()||campaignEditor.isDirty()||hasFormInput()){e.preventDefault();e.returnValue='';}
});
async function start() {
  if (window.parent !== window && !frameAccount) throw Error('请重启车型配置服务后重新载入，核对门户账号。');
  if (frameAccount) await frameAccount.ready;
  let session;
  try {
    session=await api('session');
    scheduleToolsAvailable=true;
  } catch(error) {
    if (error.status!==404) throw error;
    if (frameAccount) throw Error('请重启车型配置服务后重新载入，核对门户账号。');
    session=await api('state');
  }
  if (!session.csrf) throw Error('无法确认操作人会话，请刷新后重试。');
  if (session.mode===undefined && session.canWrite===undefined) {
    if (frameAccount) throw Error('请重启车型配置服务后重新载入，核对门户账号。');
    readonly=true;
    readonlyStatus='只读 · 重启车型配置服务后可署名写入';
  } else {
    if (!['gateway','console'].includes(session.mode) || typeof session.canWrite!=='boolean')
      throw Error('无法确认操作人会话，请刷新后重试。');
    if (frameAccount && (!frameAccount.current || session.user !== frameAccount.current.account))
      throw Error('门户账号与服务端会话不一致。');
    sessionUser=typeof session.user==='string'?session.user.trim():'';
    if (session.mode==='gateway' && !sessionUser) throw Error('缺少门户账号。');
    const owner=$('#owner');
    owner.readOnly=Boolean(sessionUser);
    if (sessionUser) owner.value=sessionUser;
    else {
      try { owner.value=localStorage.getItem('vehicle-config-owner') || ''; } catch { owner.value=''; }
    }
    $('#owner-label').hidden=false;
    readonly=session.readonly || !session.canWrite;
  }
  csrf=session.csrf;
  render();
  if (readonly) {
    await campaignEditor.load();
  } else {
    const loads = await Promise.allSettled(publishGroups.map(group=>group.editor.load()));
    const failures = loads.flatMap((result,index)=>result.status==='rejected'
      ? [publishGroups[index].title+'：'+result.reason.message] : []);
    if (failures.length) throw Error(failures.join('。'));
  }
}
run(start,frameAccount?'等待门户账号…':'正在载入…');
