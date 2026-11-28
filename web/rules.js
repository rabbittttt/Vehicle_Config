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
  let publishAttempted = false;
  let undoStack = [], redoStack = [];
  const prices = createPriceEntry({
    getRows: () => draft.option_prices,
    commit: (rows, redraw = true) => change({...draft, option_prices: rows}, redraw),
    formatPrice: text,
    editNew: preset => edit('option_prices', null, false, preset),
  });

  const ready = () => Boolean(state);
  const isDirty = () => modified || prices.hasPending();
  const hasPendingPublish = () => pendingPublish || prices.hasPending();
  const canUndo = () => undoStack.length > 0 || prices.hasPending();
  const canRedo = () => redoStack.length > 0 && !prices.hasPending();
  const list = (table, field) => ['members', 'version', 'sku', 'versions', 'match_versions'].includes(field) ||
    (table === 'battery_rules' && field === 'generation');
  function text(value) {
    if (Array.isArray(value)) return value.join(' / ');
    if (typeof value === 'boolean') return value ? '是' : '否';
    if (value && typeof value === 'object') {
      return Object.entries(value).map(([key, amount]) =>
        `${key === 'default' ? '默认价' : key} ${amount} 元`).join(' / ');
    }
    return value;
  }
  const label = (table, field) => table === 'battery_rules' && field === 'generation' ? '代际关键词' : labels[field];

  function modelNames() {
    if (!draft) return [];
    return [...new Set([
      ...draft.gen_names.map(row=>row.target),
      ...draft.merge_groups.flatMap(row=>row.members),
      ...draft.option_prices.map(row=>row.generation),
    ].filter(Boolean))];
  }

  function yearNames() {
    if (!draft) return [];
    return [...modelNames(), ...draft.gen_names.map(row=>row.source), ...draft.merge_groups.map(row=>row.name),
      ...draft.battery_rules.flatMap(row=>row.generation)];
  }

  function matchesAliasYear(source,target) {
    if (!year) return true;
    const years = [modelYear(source),modelYear(target)].filter(Boolean);
    return !years.length || years.includes(year);
  }

  function filteredModelNames() {
    if (!model && !year) return null;
    const names = new Set();
    if (!year) {
      names.add(model);
    } else {
      for (const stage of campaignEditor.reportStages()) {
        if (matchesModelAndYear(stage.model,stage.secondary)) {
          names.add(stage.model);
          if (stage.secondary) names.add(stage.secondary);
        }
      }
      for (const name of [...modelNames(), ...draft.merge_groups.map(row=>row.name)]) {
        if (matchesModelAndYear(name)) names.add(name);
      }
    }
    const aliases = new Set(names);
    for (const {source,target} of draft.gen_names) {
      if (!matchesAliasYear(source,target)) continue;
      if (names.has(source) || names.has(target) || matchesModelAndYear(target,source) || matchesModelAndYear(source,target)) {
        aliases.add(source);
        aliases.add(target);
      }
    }
    return aliases;
  }

  function matchesModel(table,row,names) {
    if (!names) return true;
    switch (table) {
      case 'gen_names': return matchesAliasYear(row.source,row.target) && (names.has(row.source) || names.has(row.target));
      case 'merge_groups': return names.has(row.name) || row.members.some(name=>names.has(name));
      case 'battery_rules': return (!model && year && row.generation.some(keyword=>modelYear(keyword)===year)) ||
        [...names].some(name=>row.generation.every(keyword=>name.includes(keyword)));
      case 'option_prices': return names.has(row.generation);
      default: return true;
    }
  }

  function accept(next) {
    prices.reset();
    state = next;
    publishAttempted = false;
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

  function change(next, redraw = true) {
    const before = JSON.stringify(draft);
    const after = JSON.stringify(next);
    if (before === after) return;
    undoStack.push(before);
    while (undoStack.length > 20 || undoStack.reduce((size, item) => size + item.length, 0) > 8_000_000) undoStack.shift();
    redoStack = [];
    draft = next;
    modified = after !== saved;
    pendingPublish = after !== published;
    if (redraw) renderKeepingScroll();
    else flags();
  }

  function undo() {
    if (busy || readonly || !canUndo()) return;
    if (prices.discardPending()) {
      renderKeepingScroll();
      notice('已撤销选配输入');
      return;
    }
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

    const names = table==='option_names' ? null : filteredModelNames();
    const rows = draft[table]
      .map((row, index) => ({row, index}))
      .filter(({row}) => matchesModel(table,row,names) && Object.values(row).map(text).join(' ').toLocaleLowerCase().includes(search));
    if (table === 'option_prices') {
      const generationOptions = [...new Set([...modelNames(), ...campaignEditor.modelNames()])]
        .filter(name => !names || names.has(name));
      let defaultGeneration = '';
      if (model && generationOptions.includes(model)) {
        // Existing scripts may store prices under the original name of a renamed model.
        const existing = new Set(draft.option_prices.map(row => row.generation).filter(name => names.has(name)));
        if (!existing.size || existing.has(model)) defaultGeneration = model;
        else if (existing.size === 1) defaultGeneration = existing.values().next().value;
      }
      prices.render({rows, search, warning: sourceAlert(), scopeNames: names, generationOptions, defaultGeneration});
      return;
    }
    const mapping = ['gen_names', 'option_names'].includes(table);
    $('#actions').innerHTML = readonly ? '' : (mapping ? ruleButton('batch', '批量粘贴') : '') +
      ruleButton('add', '新增规则');
    const range = paginator(rows.length);
    $('#count').textContent = rows.length ? `${rows.length} 条记录` : '';
    if (!rows.length) {
      $('#content').innerHTML = sourceAlert() + `<div class="empty"><strong>${search || names ? '没有匹配的规则' : '暂无'+titles[table]}</strong>${search ? '<button data-clear>清空搜索</button>' : ''}</div>`;
      return;
    }

    const battery = table === 'battery_rules';
    const headings = fields[table].map(field => `<th>${esc(label(table, field))}</th>`).join('');
    const body = rows.slice(...range).map(({row, index}) => {
      const controls = ['edit', 'copy', 'delete'].map((name, i) =>
        ruleButton(name, ['编辑', '复制', '删除'][i], {id: row.id, inline: true, disabled: readonly}));
      if (battery && !search && !names) {
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

  function priceRow(condition = '', amount = '') {
    const fallback = condition === 'default';
    return `<div class="price-row" data-price-row>
      ${fallback ? '<input type="hidden" name="price_condition" value="default">' :
        `<label>匹配条件<input name="price_condition" maxlength="4000" required value="${esc(condition)}"></label>`}
      <label>${fallback ? '默认价' : '金额'}（元）<input name="price_amount" type="number" min="0" step="any" required value="${esc(amount)}"></label>
      <button type="button" class="link" data-remove-price aria-label="移除此价格">移除</button>
    </div>`;
  }

  function priceFields(price) {
    const conditional = price != null && typeof price === 'object';
    const entries = conditional ? Object.entries(price) : [];
    return `<label>计价方式<select name="price_mode"><option value="fixed">固定金额</option><option value="conditional" ${conditional ? 'selected' : ''}>按条件定价</option></select></label>
      <label data-fixed-price ${conditional ? 'hidden' : ''}>金额（元）<input name="price" type="number" min="0" step="any" required ${conditional ? 'disabled' : ''} value="${esc(conditional ? '' : price ?? 0)}"></label>
      <fieldset class="conditional-prices wide" ${conditional ? '' : 'hidden disabled'}>
        <legend>条件价格</legend>
        <div data-price-rows>${conditional && !entries.length ? priceRow() : entries.map(([key, amount]) => priceRow(key, amount)).join('')}</div>
        <button type="button" data-add-price>添加条件价</button>
        <button type="button" data-add-default ${entries.some(([key]) => key === 'default') ? 'hidden' : ''}>添加默认价</button>
      </fieldset>`;
  }

  function wirePriceForm(form) {
    const mode = form.elements.price_mode;
    const fixed = form.querySelector('[data-fixed-price]');
    const fieldset = form.querySelector('.conditional-prices');
    const rows = fieldset.querySelector('[data-price-rows]');
    const addDefault = fieldset.querySelector('[data-add-default]');
    function append(condition) {
      rows.insertAdjacentHTML('beforeend', priceRow(condition));
      rows.lastElementChild.querySelector('input:not([type="hidden"])').focus();
    }
    mode.onchange = () => {
      const conditional = mode.value === 'conditional';
      fixed.hidden = form.elements.price.disabled = conditional;
      fieldset.hidden = fieldset.disabled = !conditional;
      if (conditional && !rows.childElementCount) append('');
    };
    fieldset.onclick = event => {
      const button = event.target.closest('button');
      if (!button) return;
      if (button.hasAttribute('data-add-price')) append('');
      else if (button === addDefault) append('default');
      else if (button.hasAttribute('data-remove-price')) {
        button.closest('[data-price-row]').remove();
        fieldset.querySelector('[data-add-price]').focus();
      }
      addDefault.hidden = Boolean(rows.querySelector('[name="price_condition"][type="hidden"]'));
    };
  }

  function readPrice(values) {
    function amount(raw) {
      const value = Number(raw);
      if (!raw || !Number.isFinite(value) || value < 0) throw Error('金额须为大于或等于零的数字');
      return value;
    }
    if (values.get('price_mode') === 'fixed') return amount(values.get('price'));
    const entries = new Map();
    const amounts = values.getAll('price_amount');
    values.getAll('price_condition').forEach((key, index) => {
      if (!key.trim()) throw Error('请填写匹配条件');
      if (entries.has(key)) throw Error('价格条件重复：' + key);
      entries.set(key, amount(amounts[index]));
    });
    if (!entries.size) throw Error('请添加条件价或默认价');
    return Object.fromEntries(entries);
  }

  function edit(table, id, copy = false, preset = {}) {
    const current = draft[table].find(row => row.id === id) || preset;
    const controls = fields[table].map(field => {
      const caption = label(table, field);
      if (preset.id && (field === 'generation' || field === 'category')) {
        return `<input type="hidden" name="${field}" value="${esc(preset[field])}">`;
      }
      if (list(table, field)) {
        return `<label>${esc(caption)}（每行一项）<textarea name="${field}" rows="3">${esc((current[field] || []).join('\n'))}</textarea></label>`;
      }
      if (field === 'default') {
        return `<label>${caption}<select name="${field}"><option value="false">否</option><option value="true" ${current[field] ? 'selected' : ''}>是</option></select></label>`;
      }
      if (field === 'price') return priceFields(current.price);
      return `<label>${caption}<input name="${field}" maxlength="4000" value="${esc(current[field] ?? '')}" ${field === 'seat' ? '' : 'required'}></label>`;
    });
    modal(copy ? '复制规则' : id ? '编辑规则' : '新增规则', `<form id="rule-form" class="form">${controls.join('')}</form>`, async values => {
      const row = {id: (id && !copy ? id : preset.id) || newId()};
      for (const field of fields[table]) {
        const value = values.get(field);
        if (list(table, field)) row[field] = String(value).split(/\r?\n/).map(item => item.trim()).filter(Boolean);
        else if (field === 'price') row[field] = readPrice(values);
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
    const form = $('#rule-form');
    if (table === 'option_prices') wirePriceForm(form);
    form.onsubmit = event => {
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
    if (readonly || busy || !csrf) return;
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
      writeModal('从脚本重新载入', `<p>用脚本中的五类规则替换规则草稿 r${current.draft.revision}，并更新规则的已发布版本。</p>
        <dl><dt>替换范围</dt><dd>${esc(tables)}</dd><dt>来源脚本</dt><dd>${esc(current.source.path)}</dd></dl>${warning}${recovery}`, async () => {
        accept(await writeApi('rules/reload', {revision: current.draft.revision, source_hash: current.source.hash, pending_token: pending}));
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
          prices.reset();
          change(record.data);
          notice('规则已恢复到编辑区，尚未保存');
        }, '恢复到编辑区');
      });
    }
    if (readonly) return;
    if (table === 'option_prices') {
      try { prices.flush(); } catch (error) { return notice(error.message, true); }
    }
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
      if (searchValue() || model || year) return;
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
    prices.flush();
    accept(await writeApi('rules/save', {data: draft, revision: state.draft.revision}));
    notice('脚本规则草稿已保存');
  }

  async function preparePublish() {
    prices.flush();
    if (publishAttempted || !isDirty()) {
      const latest = await api('rules/state');
      if (publishAttempted && latest.draft.revision !== state.draft.revision) {
        if (latest.published.revision > state.published.revision &&
            JSON.stringify(latest.published.data) === JSON.stringify(draft)) {
          accept(latest);
          return null;
        }
        throw Error('规则草稿已被其他人修改。请导出当前编辑或重新载入草稿后核对。');
      }
      if (!state || latest.draft.revision !== state.draft.revision) accept(latest);
    }
    if (!pendingPublish) return null;
    const snapshot = structuredClone(draft);
    const revision = state.draft.revision;
    const result = await api('rules/preview', {data: snapshot, revision});
    if (!result.errors.length && !result.changes.length) {
      pendingPublish = false;
      return null;
    }
    const failure = result.errors.length ? `<p class="error">${esc(result.errors.join('\n'))}</p>` : '';
    const scriptDiff = result.script_diff ? `<pre class="script-diff">${esc(result.script_diff)}</pre>` : '';
    return {
      html: `<div>${esc(state.source.path)}</div>${failure}${diffHTML(result.changes)}${scriptDiff}`,
      publish: result.errors.length ? null : async () => {
        publishAttempted = true;
        accept(await writeApi('rules/publish', {data: snapshot, revision, source_hash: result.source_hash}));
      },
    };
  }

  function openExport() {
    prices.flush();
    return download('rules/export', {data: draft}, '脚本规则-当前编辑.xlsx');
  }
  const template = () => download('rules/template', undefined, '脚本规则-导入模板.xlsx');

  async function importFiles(files) {
    prices.flush();
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
    titles, modelNames, yearNames, ready, isDirty, hasPendingPublish, canUndo, canRedo, load, render: renderView,
    handleClick, save, preparePublish, reload, syncFromScript, undo, redo, openExport, template, importFiles,
  };
})();

function createPriceEntry({getRows, commit, formatPrice, editNew}) {
  const inputs = new Map();
  const collapsed = new Set();
  let focusNext = null;
  let lastGeneration = '';

  const groupKey = (generation, category) => JSON.stringify([generation, category]);
  const isConditional = row => typeof row.price === 'object' && row.price !== null;
  const hasInput = input => !input.fresh || Boolean(input.name.trim() || input.amount.trim());
  const hasPending = () => [...inputs.values()].some(hasInput);

  function reset() {
    inputs.clear();
    collapsed.clear();
    focusNext = null;
  }

  function discardPending() {
    const last = [...inputs.values()].reverse().find(hasInput);
    if (!last) return false;
    inputs.delete(last.id);
    return true;
  }

  function applyInputs(selected) {
    const changed = selected.filter(hasInput);
    if (!changed.length) return;
    const rows = [...getRows()];
    for (const input of changed) {
      const index = rows.findIndex(row => row.id === input.id);
      const original = rows[index];
      if (!original && !input.fresh) throw Error('此选配已变更，请重新核对');
      const row = original ? {...original} : {
        id:input.id, generation:input.generation, category:input.category,
        default:false, versions:[], match_versions:[], seat:'',
      };
      row.name = input.name.trim();
      const location = [row.generation, row.category, row.name].filter(Boolean).join(' / ');
      if (!row.name) throw Error(location + '：请填写选配名称');
      if (!isConditional(row)) {
        const amount = input.amount.trim();
        if (!amount || !/^\+?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(amount) || !Number.isFinite(Number(amount))) {
          throw Error(location + '：请填写大于或等于零的金额');
        }
        row.price = Number(amount);
      }
      if (original) rows[index] = row;
      else rows.push(row);
    }
    if (rows.length > 10000) throw Error('选配金超过 10000 行');
    const keys = new Set();
    for (const row of rows) {
      const key = JSON.stringify([row.generation, row.category, row.name, row.versions, row.match_versions, row.seat]);
      if (keys.has(key)) throw Error([row.generation, row.category, row.name].join(' / ') + '：相同适用条件下的选配名称重复');
      keys.add(key);
    }
    changed.forEach(input => inputs.delete(input.id));
    commit(rows, false);
    flags();
  }

  function flush() {
    applyInputs([...inputs.values()]);
  }

  function detailsText(row) {
    return [row.default && '默认选配', row.versions?.length && '版本包含：' + row.versions.join(' / '),
      row.match_versions?.length && '版本精确：' + row.match_versions.join(' / '),
      row.seat && '座位：' + row.seat].filter(Boolean).join(' · ');
  }

  function actions(row) {
    const buttons = row.fresh
      ? '<button type="button" class="link" data-price-edit-new>编辑条件与价格</button><button type="button" class="link" data-price-discard>取消录入</button>'
      : [['edit','编辑'],['copy','复制'],['delete','删除']].map(([action, label]) =>
        `<button type="button" class="link" data-rule-action="${action}" data-rule-id="${esc(row.id)}">${label}</button>`).join('');
    return `<details class="price-more"><summary aria-label="更多操作">⋯</summary><div class="price-menu">${buttons}</div></details>`;
  }

  function rowHTML(row) {
    const input = inputs.get(row.id);
    const name = input?.name ?? row.name;
    const amount = input?.amount ?? row.price;
    const details = detailsText(row);
    const description = details ? `<small class="price-conditions">${esc(details)}</small>` : '';
    const id = esc(row.id);
    let priceCell;
    if (isConditional(row)) {
      const summary = esc(formatPrice(row.price));
      priceCell = readonly ? summary : `<button type="button" class="link price-conditional" aria-label="编辑条件价" data-rule-action="edit" data-rule-id="${id}">${summary}</button>`;
    } else {
      priceCell = readonly ? esc(amount) : `<input class="price-amount" aria-label="金额（元）" inputmode="decimal" value="${esc(amount)}">`;
    }
    return `<tr data-price-id="${id}"${row.fresh ? ' class="price-new"' : ''}>
      <td>${readonly ? esc(name) : `<input class="price-name" aria-label="选配名称" maxlength="4000" placeholder="输入选配名称" value="${esc(name)}">`}${description}</td>
      <td>${priceCell}</td>${readonly ? '' : `<td class="price-actions">${actions(row)}</td>`}</tr>`;
  }

  function groupHTML(group, rows) {
    const {key, category} = group;
    const open = !collapsed.has(key);
    return `<section class="price-group" data-price-group="${esc(key)}">
      <button type="button" class="price-group-toggle" aria-expanded="${open}">${esc(category)}</button>
      ${open ? `<table class="price-table"><colgroup><col class="price-name-column"><col class="price-amount-column">${readonly ? '' : '<col class="price-actions-column">'}</colgroup>
        <thead><tr><th scope="col">选配名称</th><th scope="col">金额（元）</th>${readonly ? '' : '<th scope="col" aria-label="操作"></th>'}</tr></thead>
        <tbody>${rows.map(rowHTML).join('')}</tbody></table>
        ${readonly ? '' : '<button type="button" class="link price-add">＋ 添加选配</button>'}` : ''}
    </section>`;
  }

  function addRow(generation, category) {
    const key = groupKey(generation, category);
    let input = [...inputs.values()].find(item => item.fresh && item.generation === generation && item.category === category);
    if (input?.name.trim() && input.amount.trim()) {
      try {
        applyInputs([input]);
        input = null;
      } catch (error) {
        notice(error.message, true);
      }
    }
    if (!input) {
      input = {id:newId(), generation, category, name:'', amount:'', fresh:true};
      inputs.set(input.id, input);
    }
    collapsed.delete(key);
    focusNext = {id:input.id, field:input.name.trim() ? '.price-amount' : '.price-name'};
    renderKeepingScroll();
  }

  function addCategory(generationOptions, defaultGeneration, scopeNames) {
    const choices = generationOptions.map(name => `<option value="${esc(name)}"></option>`).join('');
    const previous = !scopeNames || scopeNames.has(lastGeneration) ? lastGeneration : '';
    const generation = defaultGeneration ? '' : `<label>车型代际<input name="generation" required maxlength="4000" value="${esc(previous)}" list="price-generation-options"><datalist id="price-generation-options">${choices}</datalist></label>`;
    modal('添加分类', `<form class="form">${generation}<label>选配分类<input name="category" maxlength="4000" required></label></form>`, values => {
      const owner = defaultGeneration || String(values.get('generation')).trim();
      const category = String(values.get('category')).trim();
      if (!owner || !category) throw Error('请填写车型代际和选配分类');
      if (scopeNames && !scopeNames.has(owner)) throw Error('请选择当前车型和年款范围内的代际');
      lastGeneration = owner;
      addRow(owner, category);
    }, '添加');
    $('#dialog-body form').onsubmit = event => {event.preventDefault(); $('#confirm').click();};
  }

  function inputInfo(control) {
    const element = control.closest('tr[data-price-id]');
    if (!element || !control.matches('.price-name, .price-amount')) return null;
    return {element, id:element.dataset.priceId};
  }

  function handleInput(event) {
    const info = inputInfo(event.target);
    if (readonly || busy || !info) return;
    const row = getRows().find(item => item.id === info.id);
    const input = inputs.get(info.id) || (row && {id:row.id, name:row.name, amount:isConditional(row) ? '' : String(row.price), fresh:false});
    if (!input) return;
    if (event.target.matches('.price-name')) input.name = event.target.value;
    else input.amount = event.target.value;
    if (row && input.name === row.name && (isConditional(row) || input.amount === String(row.price))) inputs.delete(input.id);
    else {
      inputs.delete(input.id);
      inputs.set(input.id, input);
    }
    event.target.removeAttribute('aria-invalid');
    flags();
  }

  function finishRow(element, strict) {
    const id = element.dataset.priceId;
    const input = inputs.get(id);
    if (!input) return true;
    if (!strict && input.fresh && (!input.name.trim() || !input.amount.trim())) return false;
    if (!hasInput(input)) return false;
    try {
      applyInputs([input]);
    } catch (error) {
      const field = !input.name.trim() || error.message.includes('重复') ? '.price-name' : '.price-amount';
      element.querySelector(field)?.setAttribute('aria-invalid', 'true');
      notice(error.message, true);
      return false;
    }
    // Keep the DOM in place so a blur doesn't swallow the user's next click.
    if (input.fresh) {
      element.classList.remove('price-new');
      element.querySelector('.price-actions').innerHTML = actions({id});
    }
    return true;
  }

  function wire(editor) {
    editor.oninput = handleInput;
    editor.onchange = event => {
      const info = inputInfo(event.target);
      if (!readonly && !busy && info && !inputs.get(info.id)?.fresh) finishRow(info.element, false);
    };
    editor.onfocusout = event => {
      const info = inputInfo(event.target);
      if (!readonly && !busy && info && inputs.get(info.id)?.fresh && !info.element.contains(event.relatedTarget)) {
        finishRow(info.element, false);
      }
    };
    editor.onkeydown = event => {
      if (event.isComposing || event.keyCode === 229 || readonly || busy) return;
      if (event.key === 'Escape' && event.target.closest('.price-more')) {
        const menu = event.target.closest('.price-more');
        menu.open = false;
        menu.querySelector('summary').focus();
        return;
      }
      const info = inputInfo(event.target);
      if (!info) return;
      if (event.key === 'Escape') {
        if (inputs.delete(info.id)) {
          event.preventDefault();
          renderKeepingScroll();
        }
        return;
      }
      if (event.key !== 'Enter' || event.defaultPrevented) return;
      event.preventDefault();
      const amount = info.element.querySelector('.price-amount');
      if (event.target.matches('.price-name') && amount) {
        amount.focus();
        amount.select();
      } else if (finishRow(info.element, true)) {
        const next = info.element.nextElementSibling?.querySelector('.price-name');
        if (next) {next.focus(); next.select();}
        else addRow(...JSON.parse(info.element.closest('.price-group').dataset.priceGroup));
      }
    };
    editor.onclick = event => {
      if (busy) return;
      const group = event.target.closest('.price-group');
      if (!group) return;
      const key = group.dataset.priceGroup;
      const toggle = event.target.closest('.price-group-toggle');
      if (toggle) {
        if (collapsed.has(key)) collapsed.delete(key); else collapsed.add(key);
        renderKeepingScroll();
        $('#content').querySelector(`[data-price-group="${CSS.escape(key)}"] .price-group-toggle`)?.focus({preventScroll:true});
      } else if (!readonly && event.target.closest('.price-add')) {
        addRow(...JSON.parse(key));
      } else if (!readonly && event.target.closest('[data-price-edit-new]')) {
        const input = inputs.get(event.target.closest('tr').dataset.priceId);
        if (input) editNew({id:input.id, generation:input.generation, category:input.category,
          name:input.name, price:input.amount.trim() && Number.isFinite(Number(input.amount)) && Number(input.amount) >= 0 ? Number(input.amount) : {}});
      } else if (!readonly && event.target.closest('[data-price-discard]')) {
        inputs.delete(event.target.closest('tr').dataset.priceId);
        renderKeepingScroll();
      }
    };
  }

  function render({rows, search, warning, generationOptions, defaultGeneration, scopeNames}) {
    // A new item may have been completed in the full condition / pricing form.
    const persisted = new Set(getRows().map(row => row.id));
    for (const [id,input] of inputs) if (input.fresh && persisted.has(id)) inputs.delete(id);
    const generations = new Map();
    function append(row) {
      if (!generations.has(row.generation)) generations.set(row.generation, new Map());
      const categories = generations.get(row.generation);
      if (!categories.has(row.category)) categories.set(row.category, {
        category:row.category, key:groupKey(row.generation, row.category), rows:[],
      });
      categories.get(row.category).rows.push(row);
    }
    rows.forEach(({row}) => append(row));
    if (!readonly) inputs.forEach(input => {
      if (input.fresh && (!scopeNames || scopeNames.has(input.generation))) append(input);
    });
    const groups = [...generations.values()].flatMap(categories => [...categories.values()]);
    const count = groups.reduce((total, group) => total + group.rows.length, 0);
    if (focusNext) {
      let offset = 0;
      for (const group of groups) {
        const index = group.rows.findIndex(row => row.id === focusNext.id);
        if (index >= 0) {page = Math.floor((offset + index) / pageSize) + 1; break;}
        offset += group.rows.length;
      }
    }
    const [start,end] = paginator(count);
    let offset = 0;
    const body = [];
    for (const [generation,categories] of generations) {
      const sections = [];
      for (const group of categories.values()) {
        const visible = group.rows.slice(Math.max(0, start-offset), Math.max(0, end-offset));
        if (visible.length) sections.push(groupHTML(group, visible));
        offset += group.rows.length;
      }
      if (sections.length) {
        const title = model && generation === defaultGeneration ? '' : `<h3 class="price-generation-title">${esc(generation)}</h3>`;
        body.push(`<div class="price-generation">${title}${sections.join('')}</div>`);
      }
    }
    $('#actions').innerHTML = readonly ? '' : '<button type="button" data-price-category>＋ 添加分类</button>';
    if (!readonly) $('[data-price-category]').onclick = () => !busy && addCategory(generationOptions, defaultGeneration, scopeNames);
    $('#count').textContent = rows.length ? `${rows.length} 条记录` : '';
    const empty = `<div class="empty"><strong>${search || scopeNames ? '没有匹配的规则' : '暂无选配金'}</strong>${search ? '<button data-clear>清空搜索</button>' : ''}</div>`;
    $('#content').innerHTML = warning + (body.length ? `<div class="price-entry">${body.join('')}</div>` : empty);
    const editor = $('#content .price-entry');
    if (editor) wire(editor);
    if (focusNext) {
      const {id,field} = focusNext;
      focusNext = null;
      requestAnimationFrame(() => editor?.querySelector(`[data-price-id="${CSS.escape(id)}"] ${field}`)?.focus());
    }
  }

  return {render, flush, reset, discardPending, hasPending};
}
