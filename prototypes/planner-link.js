/* Connect the QLM workbench to the local intake, review, and task planner. */
window.createPlannerLink = function ({ q, el, switchModule, toast, showTask }) {
  const dedicated = q('#module-dedicated-view');
  const workbench = q('#workbench-split-view');
  const sidebar = q('#right-steward-sidebar');
  const sidebarBody = q('#steward-sidebar-body');
  const phaseNames = {
    planning: '接口与业务规则', development: '功能开发',
    documentation: '使用说明', integration: '集成交付',
  };
  const btnClass = 'px-3.5 py-2 rounded-lg border border-slate-300 bg-white hover:bg-slate-100 font-semibold disabled:opacity-50';
  const primaryClass = 'px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold disabled:opacity-50';
  const cardClass = 'bg-white border border-slate-200 rounded-xl p-5 space-y-3';
  const inputClass = 'w-full border border-slate-300 rounded-md px-2.5 py-1.5 bg-white text-slate-800';

  const frames = el('div', null, 'flex-1 min-w-0');
  frames.style.cssText = 'display:none; width:100%; height:100%';
  dedicated.before(frames);
  const makeFrame = (src, title) => {
    const frame = el('iframe');
    frame.title = title;
    if (!window.createWorkspaceLink) frame.src = src + '?embedded=1';
    frame.style.cssText = 'display:none; border:0; width:100%; height:100%';
    frames.append(frame);
    return frame;
  };
  const intake = makeFrame('/intake', '需求录入与赛马');
  const review = makeFrame('/review', '评审与方案整合');
  let specification = null;
  let snapshot = null;
  let draft = [];
  let dirty = false;
  let busy = false;
  let mode = '本地模拟';
  let parallel = 2;
  let extension = null;

  const open = key => switchModule(key, q('.nav-item[data-module="' + key + '"]').dataset.title);
  const action = (title, handler, primary = false) => {
    const button = el('button', title, primary ? primaryClass : btnClass);
    button.type = 'button';
    button.addEventListener('click', handler);
    return button;
  };
  async function post(path, body) {
    const response = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || '请求失败，请重试。');
    return data;
  }
  function accept(data) {
    snapshot = data;
    draft = structuredClone(data.plan.tasks);
    dirty = false;
    parallel = data.plan.max_parallel;
  }
  function ready() {
    return snapshot && specification?.review_id === snapshot.review_id
      && specification.version === snapshot.plan.spec_ref.version
      && specification.id === snapshot.plan.spec_ref.id;
  }

  window.setModelMode = value => {
    mode = value === 'model' ? '模型模式' : '本地模拟';
    q('#planner-model-mode').textContent = mode;
  };
  window.returnToRace = payload => {
    const receive = intake.contentWindow.receiveReviewInstruction;
    if (typeof receive !== 'function') throw new Error('赛马页面正在加载。');
    receive(payload);
    open('intake-race');
    intake.contentDocument.getElementById('steward-note')?.focus();
  };
  window.openRaceReview = async payload => {
    const receive = review.contentWindow.receiveRaceHandoff;
    if (typeof receive !== 'function') throw new Error('评审页面正在加载。');
    await receive(payload);
    open('review-synthesis');
  };
  window.setPlannerSpecification = reference => { specification = reference; };
  window.openTaskPlanner = async reference => {
    const samePlan = ready();
    const data = await post('/preview/plans', reference);
    if (!samePlan || data.plan.version !== snapshot.plan.version) accept(data);
    open('task-planner');
  };
  q('#select-project').style.display = 'none';
  q('#select-engine').style.display = 'none';
  const modeBadge = el('button', mode, 'rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100');
  modeBadge.type = 'button';
  modeBadge.title = '打开模型 API 配置';
  modeBadge.onclick = () => open('model-router');
  modeBadge.id = 'planner-model-mode';
  q('#select-project').after(modeBadge);
  q('#btn-step-sim').style.display = 'none';
  q('#graph-demo-legend').style.display = 'none';
  q('#graph-caption').textContent = '任务依赖图';
  q('#graph-hint').textContent = '拖拽平移 · 点击节点查看任务来源';
  q('#filter-异常').style.display = 'none';
  q('#progress-pct').style.display = 'none';
  q('#progress-bar').parentElement.style.display = 'none';
  document.querySelectorAll('.ws-tab:not([data-tab="graph"])').forEach(tab => { tab.style.display = 'none'; });

  function download(name, content, mime) {
    const link = el('a');
    link.href = URL.createObjectURL(new Blob([content], { type: mime }));
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function renderGantt(plan, schedule, run = null, onInspect = null) {
    const card = el('section', null, cardClass);
    card.dataset.testid = 'task-gantt';
    card.setAttribute('aria-label', '任务甘特图');
    card.append(el('h3', '任务甘特图', 'text-sm font-bold text-slate-900'));
    const finish = Math.max(...schedule.map(row => row.early_finish_seconds), 1);
    card.append(el('p', '已保存计划 v' + plan.version + ' · 预计 ' + Number((finish / 60).toFixed(2)) +
      ' 分钟 · 并发上限 ' + plan.max_parallel + ' · 琥珀色表示关键路径', 'text-xs text-slate-600'));
    card.append(el('p', '需评审的任务：提交产物 → 评审 → 通过后放行下游；退回则修改并重新提交。', 'text-xs text-slate-600'));
    const scroller = el('div', null, 'overflow-x-auto');
    const chart = el('div', null, 'relative');
    chart.style.minWidth = '760px';
    const axis = el('div', null, 'relative h-7 ml-52 text-xs mono text-slate-500');
    for (let tick = 0; tick <= 5; tick++) {
      const label = el('span', Number((finish * tick / 300).toFixed(2)) + 'm');
      label.style.cssText = 'position:absolute;left:' + tick * 20 + '%;transform:translateX(' +
        (tick === 0 ? '0' : tick === 5 ? '-100%' : '-50%') + ')';
      axis.append(label);
    }
    chart.append(axis);
    const rows = el('div', null, 'relative');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const rowHeight = 64;
    svg.setAttribute('viewBox', '0 0 1000 ' + plan.tasks.length * rowHeight);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.style.cssText = 'position:absolute;left:208px;top:0;width:calc(100% - 208px);height:100%;pointer-events:none;z-index:2';
    const slots = new Map(schedule.map(row => [row.task_id, row]));
    const indices = new Map(plan.tasks.map((task, index) => [task.id, index]));
    for (const [index, task] of plan.tasks.entries()) {
      const slot = slots.get(task.id);
      if (!slot) continue;
      const row = el('div', null, 'flex items-center border-t border-slate-100');
      row.style.height = rowHeight + 'px';
      const label = el('div', null, 'w-52 shrink-0 pr-3 text-xs');
      label.append(el('strong', task.id + ' · ' + task.title, 'block truncate text-slate-700'));
      const dependencies = task.depends_on.length ? '前置 ' + task.depends_on.join(', ') : '无前置任务';
      const dependencyLine = el('span', dependencies, 'block truncate text-slate-500');
      dependencyLine.title = dependencies;
      label.append(dependencyLine);
      if (task.review_required) {
        const current = run?.tasks?.[task.id];
        const rejected = current?.artifacts?.some(item => item.status === 'rejected');
        const status = current?.state;
        const caption = status === 'done' ? '评审通过' : status === 'review' ? (rejected ? '返工后复审' : '待评审')
          : rejected ? (status === 'failed' ? '已退回 · 等待返工' : '已退回 · 返工中') : '提交后评审';
        label.append(el('span', caption, 'block font-semibold ' +
          (rejected && status !== 'done' ? 'text-red-700' : status === 'done' ? 'text-emerald-700' : 'text-indigo-700')));
      }
      const track = el('div', null, 'relative flex-1 h-full');
      track.style.backgroundImage = 'repeating-linear-gradient(to right,#e2e8f0 0,#e2e8f0 1px,transparent 1px,transparent 20%)';
      const bar = action(Number((task.estimate_seconds / 60).toFixed(2)) + 'm', () => {
        if (onInspect) onInspect(task.id);
        else if (q('#linked-task-' + task.id.slice(1))) {
          q('#linked-task-' + task.id.slice(1)).scrollIntoView({block:'center',behavior:'smooth'});
          q('#linked-task-' + task.id.slice(1)).focus();
        } else extension?.inspect(task.id);
      });
      bar.className = 'absolute rounded text-white text-xs font-semibold overflow-hidden whitespace-nowrap ' +
        (slot.critical ? 'bg-amber-600' : 'bg-indigo-500');
      bar.style.cssText = 'top:20px;height:24px;left:' + slot.early_start_seconds / finish * 100 +
        '%;width:' + (slot.early_finish_seconds - slot.early_start_seconds) / finish * 100 + '%;min-width:3px';
      const caption = task.id + ' · ' + task.title + ' · ' + Number((slot.early_start_seconds / 60).toFixed(2)) +
        '—' + Number((slot.early_finish_seconds / 60).toFixed(2)) + ' 分钟' + (slot.critical ? ' · 关键路径' : '');
      bar.title = caption;
      bar.setAttribute('aria-label', caption);
      track.append(bar);
      row.append(label, track);
      rows.append(row);
      for (const predecessor of task.depends_on) {
        const before = slots.get(predecessor);
        if (!before) continue;
        const startX = before.early_finish_seconds / finish * 1000;
        const startY = (indices.get(predecessor) + .5) * rowHeight;
        const endX = slot.early_start_seconds / finish * 1000;
        const endY = (index + .5) * rowHeight;
        const offset = Math.min(startX + 9, 998);
        const path = document.createElementNS(svg.namespaceURI, 'path');
        path.setAttribute('d', 'M ' + startX + ' ' + startY + ' H ' + offset + ' V ' + endY + ' H ' + endX);
        path.setAttribute('stroke', '#64748b');
        path.setAttribute('stroke-width', '1');
        path.setAttribute('fill', 'none');
        const title = document.createElementNS(svg.namespaceURI, 'title');
        title.textContent = predecessor + ' → ' + task.id;
        path.append(title);
        svg.append(path);
      }
    }
    rows.append(svg);
    chart.append(rows);
    scroller.append(chart);
    card.append(scroller);
    return card;
  }

  function renderPlanner(container) {
    const form = el('form', null, 'max-w-5xl mx-auto space-y-4 text-sm');
    const header = el('div', null, cardClass);
    header.append(el('h2', '任务分解与配置', 'text-lg font-bold text-slate-900'));
    form.append(header);
    container.append(form);
    if (!ready()) {
      header.append(el('p', specification ? '已确认规格，进入评审页生成当前版本的任务清单。' : '请先在评审页确认方案，再进入任务分解。', 'text-slate-600'));
      header.append(action('前往评审', () => open('review-synthesis'), true));
      return;
    }

    const plan = snapshot.plan;
    const availableRefs = [...new Set(plan.tasks.flatMap(task => task.requirement_ids))];
    header.append(el('p', '规格 ' + plan.spec_ref.id + '@' + plan.spec_ref.version + ' · 计划 ' + plan.id + '@' + plan.version,
      'mono text-xs text-slate-600'));
    const parallelLabel = el('label', '并发上限', 'text-xs text-slate-600 flex items-center gap-2');
    const parallelInput = el('input', null, inputClass);
    parallelInput.type = 'number';
    parallelInput.min = '1';
    parallelInput.step = '1';
    parallelInput.value = parallel;
    parallelInput.style.width = '90px';
    parallelInput.oninput = () => { parallel = Number(parallelInput.value); changed(); };
    parallelLabel.append(parallelInput);
    header.append(parallelLabel);
    const buttons = el('div', null, 'flex flex-wrap gap-2');
    const add = action('＋ 新增任务', () => {
      const next = 1 + Math.max(...draft.map(task => Number(task.id.slice(1).split('.')[0])));
      const id = 'T' + next;
      draft.push({ id, phase: 'development', title: '', role: 'develop',
        depends_on: [draft[0].id], estimate_seconds: 300, review_required: true, requirement_ids: [] });
      draft.find(task => task.phase === 'integration').depends_on.push(id);
      dirty = true;
      open('task-planner');
      q('#linked-task-' + next)?.focus();
    });
    const save = action('保存任务清单', () => {}, true);
    save.type = 'submit';
    const graph = action('查看依赖图', () => open('workbench'));
    const json = action('下载 workflow.json', () =>
      download('workflow.json', JSON.stringify(snapshot.plan, null, 2) + '\n', 'application/json'));
    const markdown = action('下载 task.md', () => download('task.md', snapshot.task_markdown, 'text/markdown;charset=utf-8'));
    buttons.append(add, save, graph, json, markdown);
    header.append(buttons);
    const status = el('p', null, 'text-xs text-slate-600');
    status.setAttribute('role', 'status');
    header.append(status);
    if (snapshot.schedule) form.append(renderGantt(snapshot.plan, snapshot.schedule));
    if (extension) extension.planActions(header, snapshot, () => dirty);
    function updateActions(message) {
      for (const control of [graph, json, markdown]) control.disabled = busy || dirty;
      add.disabled = busy;
      parallelInput.disabled = busy;
      save.disabled = busy || !dirty;
      form.querySelectorAll('fieldset').forEach(fieldset => { fieldset.disabled = busy; });
      status.textContent = message || (dirty ? '有未保存的修改。保存后可查看新依赖与下载文件。' : '已保存 · 可查看依赖图和导出计划');
    }
    function changed() {
      dirty = true;
      updateActions();
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || !dirty) return;
      busy = true;
      updateActions('正在保存…');
      try {
        accept(await post('/preview/plans/edit', {
          review_id: snapshot.review_id, expected_version: plan.version, tasks: draft, max_parallel: parallel,
        }));
        busy = false;
        toast('已保存任务清单 v' + snapshot.plan.version);
        if (!container.classList.contains('hidden')) open('task-planner');
      } catch (error) {
        busy = false;
        updateActions(error.message);
      }
    });

    for (const [phase, label] of Object.entries(phaseNames)) {
      const tasks = draft.filter(task => task.phase === phase);
      if (!tasks.length) continue;
      const group = el('section', null, cardClass);
      group.append(el('h3', label, 'text-sm font-bold text-indigo-700'));
      form.append(group);
      for (const task of tasks) {
        const row = el('fieldset', null, 'rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2');
        row.append(el('legend', task.id, 'mono px-1 font-bold text-indigo-700'));
        const fields = el('div', null, 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5');
        const field = (label, control) => {
          const wrapper = el('label', label, 'block text-xs text-slate-600 space-y-1');
          wrapper.append(control);
          fields.append(wrapper);
        };
        const input = (label, value, setter, type = 'text') => {
          const control = el('input', null, inputClass);
          control.type = type;
          control.value = value;
          control.oninput = () => { setter(control.value); changed(); };
          field(label, control);
          return control;
        };
        const title = input('任务名称', task.title, value => { task.title = value; });
        title.required = true;
        title.id = 'linked-task-' + task.id.slice(1);
        input('执行角色', task.role, value => { task.role = value; }).required = true;
        const split = value => value.split(/[,，、\s]+/).filter(Boolean);
        input('前置任务编号', task.depends_on.join(', '), value => { task.depends_on = split(value); });
        const estimate = input('估时（分钟）', Number((task.estimate_seconds / 60).toFixed(3)),
          value => { task.estimate_seconds = Math.round(Number(value) * 60); }, 'number');
        estimate.min = '0.001';
        estimate.step = 'any';
        estimate.required = true;
        const check = el('input', null, 'ml-2');
        check.type = 'checkbox';
        check.checked = task.review_required;
        check.onchange = () => { task.review_required = check.checked; changed(); };
        field('需要人工审核', check);
        const isNew = !plan.tasks.some(original => original.id === task.id);
        if (isNew) {
          const refs = input('关联需求编号', task.requirement_ids.join(', '), value => { task.requirement_ids = split(value); });
          refs.placeholder = availableRefs.join(', ');
          refs.required = true;
          fields.append(action('移除未保存的任务', () => {
            draft = draft.filter(row => row.id !== task.id);
            for (const row of draft) row.depends_on = row.depends_on.filter(id => id !== task.id);
            open('task-planner');
          }));
        }
        row.append(fields);
        if (extension && task.outputs) {
          const details = el('details', null, 'text-xs text-slate-600 space-y-2');
          details.append(el('summary', '交付物、来源与完成条件'));
          const editField = (label, value, update, multiline = false) => {
            const wrapper = el('label', label, 'block text-xs text-slate-600 space-y-1');
            const input = el(multiline ? 'textarea' : 'input', null, inputClass);
            input.value = value || '';
            input.oninput = () => { update(input.value); changed(); };
            wrapper.append(input); details.append(wrapper);
          };
          const list = value => value.split(/[,，、\\s]+/).filter(Boolean);
          editField('写入范围', task.write_paths.join(', '), value => { task.write_paths = list(value); });
          editField('关联需求编号', task.requirement_ids.join(', '), value => { task.requirement_ids = list(value); });
          editField('关联决策编号', task.decision_ids.join(', '), value => { task.decision_ids = list(value); });
          editField('关联未决项编号', task.open_item_ids.join(', '), value => { task.open_item_ids = list(value); });
          editField('完成条件', task.completion_condition, value => { task.completion_condition = value; }, true);
          for (const output of task.outputs) {
            editField(output.id + ' 输出路径', output.path || '', value => { output.path = value || null; });
            const label = el('label', output.id + ' 输出类型', 'block text-xs text-slate-600 space-y-1');
            const select = el('select', null, inputClass);
            for (const kind of ['text', 'git_commit']) {
              const option = el('option', kind === 'text' ? '文本' : 'Git 提交');
              option.value = kind; select.append(option);
            }
            select.value = output.kind;
            select.onchange = () => { output.kind = select.value; changed(); };
            label.append(select); details.append(label);
          }
          row.append(details);
        }
        const sources = [...task.requirement_ids, ...(task.decision_ids || []), ...(task.open_item_ids || [])];
        if (sources.length) row.append(el('p', '来源 ' + sources.join(' · '), 'mono text-xs text-slate-600'));
        if (task.write_paths?.length) row.append(el('p', '预计产物 ' + task.write_paths.join(' · '), 'mono text-xs text-slate-600'));
        if (task.open_item_ids?.length) row.append(el('p', '涉及待决问题，请在执行前确认处置。', 'text-xs text-amber-700'));
        group.append(row);
      }
    }
    updateActions();
  }

  function graphData() {
    const view = extension?.executionSnapshot() || snapshot;
    if (!view || (!extension?.executionSnapshot() && !ready())) return null;
    const layers = new Map();
    const positions = new Map();
    const tasksById = new Map(view.plan.tasks.map(task => [task.id, task]));
    function layer(task) {
      if (!layers.has(task.id)) {
        layers.set(task.id, Math.max(-1, ...task.depends_on.map(id => layer(tasksById.get(id)))) + 1);
      }
      return layers.get(task.id);
    }
    for (const task of view.plan.tasks) {
      const col = layer(task);
      const items = positions.get(col) || [];
      items.push(task);
      positions.set(col, items);
    }
    const phases = [...positions].sort(([a], [b]) => a - b).map(([col, items]) => ({
      id: 'layer-' + col, title: '依赖层级 ' + (col + 1),
      box: { x: 18 + col * 234, y: 22, w: 214, h: 37 + items.length * 80 },
      tasks: items.map((task, row) => ({
        id: task.id, code: task.id, name: task.title, owner: task.role, status: extension?.taskStatus(task.id) || 'planned',
        estimateMin: Number((task.estimate_seconds / 60).toFixed(2)),
        spec: [...task.requirement_ids, ...task.decision_ids, ...task.open_item_ids].join(' · '),
        output: task.write_paths.join(' · '), deps: task.depends_on,
        reviewRequired: task.review_required,
        testFiles: task.outputs.filter(item=>item.path?.startsWith('tests/')).map(item=>item.path),
        critical: view.schedule?.find(row => row.task_id === task.id)?.critical || false,
        x: 27 + col * 234, y: 54 + row * 80,
      })),
    }));
    return { phases, badge: '计划 v' + view.plan.version, reworkEdges: extension?.reworkEdges() || [] };
  }

  function render(module) {
    if (extension?.render(module)) return true;
    const frame = module === 'intake-race' ? intake : module === 'review-synthesis' ? review : null;
    frames.style.display = frame ? 'block' : 'none';
    intake.style.display = frame === intake ? 'block' : 'none';
    review.style.display = frame === review ? 'block' : 'none';
    const graph = module === 'workbench' && !!graphData();
    q('#btn-fit-graph').style.display = graph ? '' : 'none';
    q('#btn-toggle-right-sidebar').style.display = frame ? 'none' : '';
    sidebar.style.display = frame ? 'none' : '';
    if (frame) {
      dedicated.classList.add('hidden');
      workbench.classList.add('hidden');
      return true;
    }
    sidebarBody.replaceChildren();
    if (module === 'task-planner' || module === 'workbench') {
      sidebarBody.append(el('h2', '任务分解与人工检查', 'text-sm font-bold text-slate-800'));
      if (ready()) {
        const blocked = snapshot.plan.tasks.filter(task => task.open_item_ids.length).length;
        sidebarBody.append(el('p', '已生成 ' + snapshot.plan.tasks.length + ' 项任务。保存时检查前置编号、规格版本和循环依赖。'));
        if (blocked) sidebarBody.append(el('p', blocked + ' 项任务涉及待决问题。', 'text-amber-700'));
      }
      sidebarBody.append(el('p', '人工核对任务边界、交付物和依赖，再保存计划。', 'text-slate-600'));
      q('#steward-case-label').textContent = ready() ? snapshot.plan.spec_ref.id : '待确认规格';
      q('#steward-parallel-label').textContent = ready() ? 'max_parallel = ' + snapshot.plan.max_parallel : '—';
    }
    if (graph) {
      extension?.sidebar(module);
      return false;
    }
    workbench.classList.add('hidden');
    dedicated.classList.remove('hidden');
    dedicated.replaceChildren();
    if (module === 'task-planner' || module === 'workbench') renderPlanner(dedicated);
    else {
      const card = el('div', null, cardClass);
      card.append(el('p', '本次联调已接入需求赛马、方案评审和任务分解。', 'text-sm text-slate-600'));
      card.append(action('返回任务分解', () => open('task-planner')));
      dedicated.append(card);
      sidebar.style.display = 'none';
    }
    extension?.sidebar(module);
    return true;
  }

  if (window.createWorkspaceLink) {
    extension = window.createWorkspaceLink({ q, el, open, action, download, toast,
      getPlan: () => snapshot, accept, setSpecification: value => { specification = value; },
      renderGantt, showTask, isDirty: () => dirty, clearPlan: () => { snapshot = null; draft = []; dirty = false; },
    });
  }
  return { render, graphData, open, renderTab: (tab, selection) => extension?.renderTab(tab, selection) || false,
    inspect: id => extension?.inspect(id), isFullWorkspace: !!extension };
};
