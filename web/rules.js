'use strict';

const orderRules = (() => {
  const titles = {
    gen_names: '代际显示名称',
    merge_groups: '代际合并',
    battery_rules: '电池包规则',
    option_names: '选配名称合并',
    option_prices: '选配金',
    rule_history: '脚本变更记录',
  };
  const fields = {
    gen_names: ['source', 'target'],
    merge_groups: ['name', 'members'],
    battery_rules: ['generation', 'version', 'sku', 'capacity'],
    option_names: ['source', 'target'],
    option_prices: ['generation', 'category', 'name', 'price', 'default', 'versions', 'match_versions', 'seat'],
  };
  const labels = {
    source: '原名称', target: '标准名称', name: '名称', members: '源代际', generation: '代际',
    version: '动力及版本关键词', sku: 'SKU 关键词', capacity: '电池容量', category: '选配分类',
    price: '金额', default: '默认选配', versions: '版本包含', match_versions: '版本精确', seat: '座位类型',
  };

  let state, draft, saved = '', published = '', modified = false, pendingPublish = false;
  let undoStack = [], redoStack = [];

  const ready = () => Boolean(state);
  const isDirty = () => modified;
  const canPublish = () => Boolean(pendingPublish && state?.source.available && !state.source.external_modified && !state.sync.pending_token);
  const canUndo = () => undoStack.length > 0;
  const canRedo = () => redoStack.length > 0;
  const status = () => !ready() ? '脚本规则未载入' : isDirty() ? '脚本规则有未保存修改' : `脚本规则草稿 r${state.draft.revision} · 已保存`;
  const list = (table, field) => ['members', 'version', 'sku', 'versions', 'match_versions'].includes(field) ||
    (table === 'battery_rules' && field === 'generation');
  const text = value => Array.isArray(value) ? value.join(' / ') : typeof value === 'boolean' ? (value ? '是' : '否') : value;
  const label = (table, field) => table === 'battery_rules' && field === 'generation' ? '代际关键词' : labels[field];

  function accept(next) {
    state = next;
    draft = structuredClone(next.draft.data);
    saved = JSON.stringify(draft);
    published = JSON.stringify(next.published.data);
    pendingPublish = saved !== published;
    modified = false;
    undoStack = [];
    redoStack = [];
    if (isRuleView()) renderKeepingScroll();
    else flags();
  }

  async function load() {
    accept(await api('rules/state'));
  }

  function reload() {
    const refresh = async () => {
      await load();
      notice();
    };
    if (isDirty()) {
      modal('放弃未保存的规则修改？', '<div class="selection-summary">重新载入已保存的脚本规则草稿</div>', refresh, '重新载入草稿');
    } else {
      run(refresh);
    }
  }

  function change(next) {
    const before = JSON.stringify(draft);
    const after = JSON.stringify(next);
    if (before === after) return;
    undoStack.push(before);
    while (undoStack.length > 20 || undoStack.reduce((size, item) => size + item.length, 0) > 8_000_000) undoStack.shift();
    redoStack = [];
    draft = next;
    modified = after !== saved;
    pendingPublish = after !== published;
    renderKeepingScroll();
  }

  function undo() {
    if (busy || readonly || !canUndo()) return;
    redoStack.push(JSON.stringify(draft));
    const previous = undoStack.pop();
    draft = JSON.parse(previous);
    modified = previous !== saved;
    pendingPublish = previous !== published;
    renderKeepingScroll();
    notice('已撤销规则修改');
  }

  function redo() {
    if (busy || readonly || !canRedo()) return;
    undoStack.push(JSON.stringify(draft));
    const next = redoStack.pop();
    draft = JSON.parse(next);
    modified = next !== saved;
    pendingPublish = next !== published;
    renderKeepingScroll();
    notice('已重做规则修改');
  }

  function ruleButton(action, caption, { id = '', disabled = false, inline = false } = {}) {
    return `<button ${inline ? 'class="link"' : ''} data-rule-action="${action}" data-rule-id="${esc(id)}" ${disabled ? 'disabled' : ''}>${caption}</button>`;
  }

  function sourceAlert() {
    const warning = state.sync.error || (!state.source.available ? '脚本检查未通过：' + (state.source.error || '脚本不可用') :
      state.source.external_modified ? '脚本已被其他程序修改' : '');
    if (!warning) return '';
    const reload = state.source.available && !readonly ? ' <button class="link" data-reload-rules>从脚本重新载入</button>' : '';
    return `<div class="rule-source-status error" role="status">${esc(warning)}${reload}</div>`;
  }

  function renderView(table, query) {
    $('#count').textContent = '';
    $('#actions').innerHTML = '';
    if (!ready()) {
      $('#pagination').innerHTML = '';
      $('#content').innerHTML = '<div class="empty"><strong>脚本规则未载入</strong></div>';
      return;
    }

    const search = query.toLocaleLowerCase();
    if (table === 'rule_history') {
      renderHistory(search, sourceAlert());
      return;
    }

    const mapping = ['gen_names', 'option_names'].includes(table);
    $('#actions').innerHTML = readonly ? '' : (mapping ? ruleButton('batch', '批量粘贴') : '') +
      ruleButton('add', '新增规则');

    const rows = draft[table]
      .map((row, index) => ({row, index}))
      .filter(({row}) => Object.values(row).map(text).join(' ').toLocaleLowerCase().includes(search));
    const range = paginator(rows.length);
    $('#count').textContent = rows.length ? `${rows.length} 条记录` : '';
    if (!rows.length) {
      $('#content').innerHTML = sourceAlert() + `<div class="empty"><strong>${search ? '没有匹配的规则' : '暂无'+titles[table]}</strong>${search ? '<button data-clear>清空筛选</button>' : ''}</div>`;
      return;
    }

    const battery = table === 'battery_rules';
    const headings = fields[table].map(field => `<th>${esc(label(table, field))}</th>`).join('');
    const body = rows.slice(...range).map(({row, index}) => {
      const controls = ['edit', 'copy', 'delete'].map((name, i) =>
        ruleButton(name, ['编辑', '复制', '删除'][i], {id: row.id, inline: true, disabled: readonly}));
      if (battery && !search) {
        controls.push(ruleButton('up', '上移', {id: row.id, inline: true, disabled: readonly || index === 0}));
        controls.push(ruleButton('down', '下移', {id: row.id, inline: true, disabled: readonly || index === draft[table].length - 1}));
      }
      const cells = fields[table].map(field => `<td>${esc(text(row[field]))}</td>`).join('');
      return `<tr>${battery ? `<td>${index + 1}</td>` : ''}${cells}${readonly?'':`<td class="operation"><div class="rule-actions">${controls.join('')}</div></td>`}</tr>`;
    }).join('');
    $('#content').innerHTML = sourceAlert() + `<div class="scroll"><table class="rule-table"><thead><tr>${battery ? '<th>顺序</th>' : ''}${headings}${readonly?'':'<th>操作</th>'}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function diffHTML(changes) {
    if (!changes.length) return '<div class="empty">没有变更</div>';
    const describe = value => value == null ? '—' : Array.isArray(value) ? text(value) :
      Object.entries(value).filter(([key]) => key !== 'id').map(([key, item]) => `${labels[key] || key}：${text(item)}`).join('\n');
    return `<div class="scroll"><table class="diff"><thead><tr><th>规则 / 字段</th><th>修改前</th><th>修改后</th></tr></thead><tbody>${changes.map(change => `
      <tr><td>${esc(titles[change.table])}<small>${esc(change.field)}</small></td><td>${esc(describe(change.before))}</td><td>${esc(describe(change.after))}</td></tr>`
    ).join('')}</tbody></table></div>`;
  }

  function edit(table, id, copy = false) {
    const current = draft[table].find(row => row.id === id) || {};
    const controls = fields[table].map(field => {
      const caption = label(table, field);
      if (list(table, field)) {
        return `<label>${esc(caption)}（每行一项）<textarea name="${field}" rows="3">${esc((current[field] || []).join('\n'))}</textarea></label>`;
      }
      if (field === 'default') {
        return `<label>${caption}<select name="${field}"><option value="false">否</option><option value="true" ${current[field] ? 'selected' : ''}>是</option></select></label>`;
      }
      const attrs = field === 'price' ? 'type="number" min="0" step="any"' : 'maxlength="4000"';
      return `<label>${caption}<input name="${field}" ${attrs} value="${esc(current[field] ?? (field === 'price' ? 0 : ''))}" ${field === 'seat' ? '' : 'required'}></label>`;
    });
    modal(copy ? '复制规则' : id ? '编辑规则' : '新增规则', `<form id="rule-form" class="form">${controls.join('')}</form>`, async values => {
      const row = {id: id && !copy ? id : newId()};
      for (const field of fields[table]) {
        const value = values.get(field);
        if (list(table, field)) row[field] = String(value).split(/\r?\n/).map(item => item.trim()).filter(Boolean);
        else if (field === 'price') row[field] = Number(value);
        else if (field === 'default') row[field] = value === 'true';
        else row[field] = String(value).trim();
      }
      if (id && !copy && fields[table].every(field=>JSON.stringify(row[field])===JSON.stringify(current[field]))) return;
      const next = structuredClone(draft);
      const index = next[table].findIndex(item => item.id === row.id);
      if (index < 0) next[table].push(row);
      else next[table][index] = row;
      await validate(next);
      change(next);
    });
    $('#rule-form').onsubmit = event => {
      event.preventDefault();
      $('#confirm').click();
    };
  }

  async function validate(next) {
    const {errors} = await api('rules/validate', {data: next});
    if (errors.length) throw Error(errors.join('\n'));
  }

  function batch(table) {
    modal('批量粘贴', '<form><label>原名称 ⇥ 标准名称（每行一组）<textarea name="rows" rows="12" required></textarea></label></form>', async values => {
      const rows = String(values.get('rows')).split(/\r?\n/).filter(line => line.trim()).map(line => line.split('\t').map(value => value.trim()));
      if (!rows.length || rows.length > 200 || rows.some(row => row.length !== 2 || row.some(value => !value))) {
        throw Error('每次最多粘贴 200 行，每行须有两个非空名称');
      }
      if (new Set(rows.map(row => row[0])).size !== rows.length) throw Error('原名称重复');
      const next = structuredClone(draft);
      for (const [source, target] of rows) {
        const existing = next[table].find(row => row.source === source);
        if (existing) existing.target = target;
        else next[table].push({id: newId(), source, target});
      }
      await validate(next);
      change(next);
    }, '应用到编辑区');
  }

  function syncFromScript() {
    if (readonly || busy || !data) return;
    return run(async () => {
      const current = await api('rules/state');
      if (!current.source.available) {
        modal('脚本检查未通过', `<p class="error">${esc(current.source.error || '脚本不可用')}</p><p>本次未载入，规则草稿未更改。</p>`);
        $('#cancel').textContent = '关闭';
        return;
      }
      if (isDirty() && state.draft.revision !== current.draft.revision) {
        throw Error('规则草稿已被其他人修改。请在规则表导出当前编辑，再重新载入草稿核对。');
      }
      const pending = current.sync.pending_token;
      if(!pending && !isDirty() && current.sync.status==='synced'){
        if (!state || state.draft.revision !== current.draft.revision || state.sync.status !== 'synced') accept(current);
        notice('规则草稿已与脚本一致');
        return;
      }
      const tables = Object.keys(fields).map(key => titles[key]).join('、');
      const overwritten = [];
      if (JSON.stringify(current.draft.data) !== JSON.stringify(current.published.data)) overwritten.push('已保存但尚未发布的规则');
      if (isDirty()) overwritten.push('本地未保存的规则编辑');
      const warning = overwritten.length ? `<p class="error">${overwritten.join('、')}会一并替换。</p>` : '';
      const recovery = pending ? '<p>上次中断的发布将存入脚本变更记录。</p>' : '';
      modal('从脚本重新载入', `<p>用脚本中的五类规则替换规则草稿 r${current.draft.revision}，并更新规则的已发布版本。</p>
        <dl><dt>替换范围</dt><dd>${esc(tables)}</dd><dt>来源脚本</dt><dd>${esc(current.source.path)}</dd></dl>${warning}${recovery}`, async () => {
        accept(await api('rules/reload', {revision: current.draft.revision, source_hash: current.source.hash, pending_token: pending}));
        notice('已从脚本重新载入');
      }, '替换规则并载入');
    }, '正在核对脚本规则…');
  }

  function handleClick(button, table) {
    const {ruleAction: action, ruleId: id} = button.dataset;
    if (!ready() || busy || button.disabled || !action) return;
    if (action === 'history') {
      return run(async () => {
        const record = await api('rules/history/' + id);
        modal('脚本变更记录 #' + id, diffHTML(record.changes), readonly ? null : () => {
          change(record.data);
          notice('规则已恢复到编辑区，尚未保存');
        }, '恢复到编辑区');
      });
    }
    if (readonly) return;
    if (action === 'batch') return batch(table);
    if (action === 'add') return edit(table);
    if (action === 'edit' || action === 'copy') return edit(table, id, action === 'copy');
    if (action === 'delete') {
      const record = draft[table].find(row => row.id === id);
      const caption = fields[table].map(field => text(record[field])).filter(Boolean).join(' · ');
      return modal('删除规则', `<div class="selection-summary">${esc(caption)}</div>`, () => {
        change({...draft, [table]: draft[table].filter(row => row.id !== id)});
      }, '删除');
    }
    if (action === 'up' || action === 'down') {
      if (ruleQuery) return;
      const rows = [...draft[table]];
      const index = rows.findIndex(row => row.id === id);
      const target = index + (action === 'up' ? -1 : 1);
      if (index >= 0 && target >= 0 && target < rows.length) {
        [rows[index], rows[target]] = [rows[target], rows[index]];
        page = Math.floor(target / pageSize) + 1;
        change({...draft, [table]: rows});
        const idSelector = `[data-rule-id="${CSS.escape(id)}"]`;
        const forward = $('#content').querySelector(idSelector + `[data-rule-action="${action}"]`);
        const reverse = action === 'up' ? 'down' : 'up';
        const next = forward?.disabled ? $('#content').querySelector(idSelector + `[data-rule-action="${reverse}"]`) : forward;
        next?.focus({preventScroll: true});
        if (next) {
          const bounds=next.getBoundingClientRect();
          if(bounds.top<0 || bounds.bottom>$('footer').getBoundingClientRect().top) next.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
        }
      }
    }
  }

  async function save() {
    accept(await api('rules/save', {data: draft, revision: state.draft.revision}));
    notice('脚本规则草稿已保存');
  }

  async function previewPublish() {
    const snapshot = structuredClone(draft);
    const revision = state.draft.revision;
    const result = await api('rules/preview', {data: snapshot, revision});
    const failure = result.errors.length ? `<p class="error">${esc(result.errors.join('\n'))}</p>` : '';
    const scriptDiff = result.script_diff ? `<pre class="script-diff">${esc(result.script_diff)}</pre>` : '';
    modal('发布并更新脚本', `<div>${esc(state.source.path)}</div>${failure}${diffHTML(result.changes)}${scriptDiff}`,
      result.errors.length || !result.changes.length ? null : async () => {
        accept(await api('rules/publish', {data: snapshot, revision, source_hash: result.source_hash}));
        notice('脚本已更新 · 业务报表未重新生成');
      }, '确认发布');
  }

  const openExport = () => download('rules/export', {data: draft}, '脚本规则-当前编辑.xlsx');
  const template = () => download('rules/template', undefined, '脚本规则-导入模板.xlsx');

  async function importFiles(files) {
    const payload = await encodeWorkbooks(files);
    const result = await api('rules/import', {files: payload, revision: state.draft.revision, ...(modified ? {editor:draft} : {})});
    const {summary, issues, changes, errors} = result;
    const problemList = issues.length ? `<div class="scroll"><table class="diff"><thead><tr><th>文件 / 工作表</th><th>位置</th><th>问题</th></tr></thead><tbody>${issues.map(issue => `
      <tr><td>${esc(issue.file)}<small>${esc(issue.sheet)}</small></td><td>${issue.row ? '第 ' + esc(issue.row) + ' 行' : ''} ${esc(issue.field)}</td><td class="error">${esc(issue.message)}</td></tr>`
    ).join('')}</tbody></table></div>` : '';
    modal('脚本规则导入预览', `<div class="file-list">${files.map(file => `<span>${esc(file.name)}</span>`).join('')}</div>
      <div class="import-summary"><span>新增 <b>${summary.added}</b></span><span>更新 <b>${summary.updated}</b></span><span>未变更 <b>${summary.unchanged}</b></span><span>问题 <b>${issues.length}</b></span></div>
      ${problemList}${diffHTML(changes)}`, errors.length || !changes.length ? null : async () => {
        const fresh = await api('rules/state');
        if (fresh.draft.revision !== result.revision || state.draft.revision !== result.revision) {
          throw Error('规则草稿已被其他人修改，请重新载入草稿后再次导入');
        }
        change(result.data);
        notice('脚本规则已导入编辑区，尚未保存');
      }, '应用到编辑区');
  }

  return {
    titles, ready, isDirty, canPublish, canUndo, canRedo, status, load, render: renderView,
    handleClick, save, previewPublish, reload, syncFromScript, undo, redo, openExport, template, importFiles,
  };
})();
