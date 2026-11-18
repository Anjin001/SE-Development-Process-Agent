/* Bind the QLM interface to the combined workspace services. */
window.createWorkspaceLink = function (hooks) {
  const { q, el, open, action, post, toast, getPlan, accept, setSpecification,
    clearPlan, isDirty, renderGantt, download, showTask } = hooks;
  const dedicated = q('#module-dedicated-view');
  const sidebarBody = q('#steward-sidebar-body');
  const cardClass = 'bg-white border border-slate-200 rounded-xl p-5 space-y-3 shadow-2xs';
  const inputClass = 'w-full border border-slate-300 rounded-lg px-3 py-2 bg-white text-xs text-slate-800';
  const oldPicker = q('#select-project');
  const picker = oldPicker.cloneNode(false);
  picker.style.display = '';
  picker.setAttribute('aria-label', '当前项目');
  oldPicker.replaceWith(picker);
  const state = {
    module: 'intake-race', config: null, project: null, run: null, runPlan: null, reviewTab: 'handoff',
    spec: null, dirty: false, selection: 'merge', inspection: null, pending: false, error: '',
    markdown: '# 设备预约系统\n\n- 用户可以查看设备并提交预约。\n- 管理员可以停用设备，停用后不能新增预约。\n- 用户可以在我的预约中查看状态。',
    version: 1, frozen: null, groups: [], extraPrompt: '', paused: false, queue: false,
    simulateFailure: false, selectedTask: null, edits: new Map(), feedback: '', feedbackGroup: '',
    memory: null, domain: '', terms: '', projects: [], memoryDraft: {},
  };
  let epoch = 0;
  let polling = false;
  const split = value => value.split(/[,，、\s]+/).filter(Boolean);
  const text = (value, cls = 'text-xs text-slate-600 leading-relaxed') => el('p', value, cls);
  const card = title => {
    const node = el('section', null, cardClass);
    if (title) node.append(el('h2', title, 'text-sm font-bold text-slate-900'));
    return node;
  };
  const field = (parent, label, value, update, multiline = false) => {
    const wrapper = el('label', label, 'block text-xs text-slate-600 space-y-1');
    const input = el(multiline ? 'textarea' : 'input', null, inputClass);
    if (multiline) input.rows = 4;
    input.value = value ?? '';
    input.oninput = () => update(input.value);
    wrapper.append(input); parent.append(wrapper);
    return input;
  };
  const line = (...nodes) => {
    const row = el('div', null, 'flex flex-wrap items-center gap-2');
    row.append(...nodes); return row;
  };
  async function get(path) {
    const response = await fetch(path, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || data.error || '读取失败');
    return data;
  }
  async function write(path, body) {
    const response = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || data.error || '操作失败');
    return data;
  }
  for (const [id, title, path] of [
    ['btn-doc', '整合运行说明', '/workspace/docs/runtime'],
    ['btn-changelog', '接口契约 v2', '/workspace/docs/contract'],
  ]) {
    const previous = q('#' + id);
    const button = previous.cloneNode(true);
    previous.replaceWith(button);
    button.onclick = async () => {
      try {
        const data = await get(path);
        q('#info-modal-title').textContent = title;
        const body = q('#info-modal-body');
        body.replaceChildren(el('pre', data.markdown, 'whitespace-pre-wrap mono text-xs'));
        q('#info-modal').classList.remove('hidden');
      } catch (error) {
        toast(error.message);
      }
    };
  }
  function refresh() { open(state.module); }
  async function operate(callback) {
    if (state.pending) return;
    state.pending = true; state.error = ''; refresh();
    try { await callback(); }
    catch (error) { state.error = error.message; }
    finally { state.pending = false; refresh(); }
  }
  function setProject(project) {
    state.project = project;
    state.spec = project.specification ? structuredClone(project.specification) : null;
    state.selection = project.specification?.selection || 'merge';
    state.dirty = false; state.inspection = null;
    const spec = project.specification;
    setSpecification(spec?.status === 'confirmed'
      ? { review_id: project.id, id: spec.id, version: spec.version } : null);
  }
  function dirty() {
    state.dirty = true; state.inspection = null;
    document.querySelectorAll('[data-requires-clean-spec]').forEach(button => { button.disabled = true; });
  }
  function projectURL(suffix = '') {
    if (!state.project) throw new Error('请先完成赛马交接。');
    return '/workspace/projects/' + state.project.id + suffix;
  }
  async function reviewAction(name, body = {}) {
    setProject(await post('/preview/projects/' + state.project.id + '/actions/' + name, body));
    state.reviewTab = name === 'evaluate' ? 'eval' : 'spec';
  }
  async function load(projectId) {
    epoch++;
    const data = await get('/workspace/projects/' + projectId);
    setProject(data.review); clearPlan();
    if (data.planning) accept(data.planning);
    state.run = data.run;
    state.runPlan = data.run ? await get('/api/v2/projects/' + projectId + '/plans/' +
      data.run.plan_ref.id + '/versions/' + data.run.plan_ref.version) : null;
    state.markdown = data.review.requirement.markdown;
    state.version = data.review.requirement.version;
    state.frozen = data.review.requirement;
    state.groups = data.review.drafts.map(draft => ({
      id: draft.id, prompt: '复核本组既有草案，逐条对齐需求并补充边界情况。',
      status: 'done', result: { draft, ideas: [], challenge: '' },
    }));
    picker.value = projectId; state.memory = null;
  }
  async function updateProjects() {
    state.config = await get('/workspace/config');
    state.projects = state.config.projects;
    picker.replaceChildren();
    const blank = el('option', '＋ 新建需求'); blank.value = ''; picker.append(blank);
    for (const project of state.projects) {
      const option = el('option', project.title + ' · ' + project.id.slice(0, 6));
      option.value = project.id; picker.append(option);
    }
    picker.value = state.project?.id || '';
  }
  picker.onchange = () => {
    const id = picker.value;
    if ((state.dirty || isDirty()) && !confirm('切换项目会清除当前未保存的编辑，是否继续？')) {
      picker.value = state.project?.id || ''; return;
    }
    operate(async () => {
      if (id) { await load(id); state.module = state.run ? 'workbench' : getPlan() ? 'task-planner' : 'review-synthesis'; }
      else {
        epoch++; state.project = state.run = state.runPlan = state.spec = state.frozen = null;
        state.memory = null; state.version = 1;
        state.groups.forEach(group => { group.status = 'idle'; group.result = null; });
        clearPlan(); setSpecification(null); state.module = 'intake-race';
      }
    });
  };
  async function runGroup(group) {
    if (!state.frozen || state.paused || group.status === 'running') return;
    const current = epoch;
    const frozen = structuredClone(state.frozen);
    group.status = 'running'; group.error = ''; refresh();
    try {
      const result = await post('/preview/group', {
        markdown: frozen.markdown, version: frozen.version, group_id: group.id,
        prompt: group.prompt, simulate_failure: state.simulateFailure && group.id === 'B' && !group.failedOnce,
      });
      if (current !== epoch) return;
      group.result = result; group.status = 'done';
    } catch (error) {
      if (current !== epoch) return;
      group.status = 'failed'; group.failedOnce = true; group.error = error.message;
    }
    refresh();
  }
  async function race() {
    if (state.queue || !state.frozen) return;
    if (state.groups.some(group => !group.prompt.trim())) { toast('每个小组都需要提示词。'); return; }
    state.queue = true;
    const currentEpoch = epoch;
    for (const group of state.groups) {
      if (state.paused || epoch !== currentEpoch) break;
      if (group.status !== 'done') await runGroup(group);
    }
    state.queue = false; refresh();
  }
  async function handoff() {
    const payload = { requirement: state.frozen, drafts: state.groups.map(group => group.result.draft) };
    const same = state.project && JSON.stringify(state.project.requirement) === JSON.stringify(state.frozen);
    if (same) await reviewAction('update-drafts', { ...payload, expected_round: state.project.round });
    else {
      setProject(await post('/preview/projects', payload)); clearPlan(); state.run = state.runPlan = null;
    }
    await updateProjects(); state.reviewTab = 'handoff'; state.module = 'review-synthesis';
  }
  function renderIntake(wrap) {
    const requirement = card('需求录入与快照版本控制');
    const input = field(requirement, '需求 Markdown', state.markdown, value => { state.markdown = value; }, true);
    input.rows = 8; input.disabled = !!state.frozen || state.pending;
    const running = state.groups.some(group => group.status === 'running');
    const confirm = action('确认需求快照', () => operate(async () => {
      state.frozen = await post('/preview/snapshot', { markdown: state.markdown, version: state.version });
      q('#sidebar-req-ver').textContent = state.frozen.id + '@v' + state.version;
    }), true);
    confirm.disabled = !!state.frozen || state.pending;
    const edit = action('编辑并创建新版本', () => {
      epoch++; state.version++; state.frozen = null;
      for (const group of state.groups) { group.status = 'idle'; group.result = null; }
      refresh();
    });
    edit.disabled = !state.frozen || running;
    requirement.append(line(confirm, edit));
    if (state.frozen) {
      requirement.append(text('已锁定 ' + state.frozen.id + '@v' + state.version));
      const items = el('div', null, 'grid grid-cols-1 md:grid-cols-2 gap-2');
      for (const item of state.frozen.items) {
        const row = el('div', null, 'p-3 rounded-lg border border-slate-200 bg-slate-50');
        row.append(el('strong', item.id + ' · ' + item.title, 'text-xs text-indigo-700'));
        row.append(text('来源 L' + item.source.start_line + '—L' + item.source.end_line)); items.append(row);
      }
      requirement.append(items);
    }
    wrap.append(requirement);
    const groups = card('赛马出案 · 分组与提示词配置');
    const add = action('＋ 添加讨论组', () => {
      const id = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').find(id => !state.groups.some(group => group.id === id));
      if (!id) return;
      state.groups.push({ id, prompt: state.extraPrompt, status: 'idle' }); refresh();
    });
    add.disabled = running || state.pending;
    const run = action('启动赛马生成草案', () => race(), true);
    run.disabled = !state.frozen || running || state.paused || state.pending;
    groups.append(line(add, run));
    const toggle = el('label', null, 'flex gap-2 items-center text-xs text-slate-600');
    const fail = el('input'); fail.type = 'checkbox'; fail.checked = state.simulateFailure;
    fail.onchange = () => { state.simulateFailure = fail.checked; };
    toggle.append(fail, el('span', '模拟 B 组首次中断')); groups.append(toggle);
    const grid = el('div', null, 'grid grid-cols-1 md:grid-cols-2 gap-3.5');
    const labels = { idle: '待派发', running: '生成中', done: '已提交', failed: '失败' };
    const all = state.groups.length >= 2 && state.groups.every(group => group.status === 'done');
    for (const group of state.groups) {
      const box = el('article', null, 'p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-2');
      box.append(el('strong', group.id + ' 组 · ' + labels[group.status], 'text-xs text-slate-800'));
      const prompt = field(box, group.id + ' 组系统提示词', group.prompt, value => {
        group.prompt = value; group.status = 'idle'; group.result = null;
        q('#native-race-handoff')?.setAttribute('disabled', '');
      }, true);
      prompt.disabled = running || state.pending;
      if (group.error) box.append(text(group.error, 'text-xs text-red-700'));
      const rerun = action(group.status === 'failed' ? '重试 ' + group.id + ' 组' : '单独运行 ' + group.id + ' 组', () => runGroup(group));
      rerun.disabled = !state.frozen || running || state.paused || state.pending; box.append(rerun);
      if (state.groups.length > 2) {
        const remove = action('移除 ' + group.id + ' 组', () => { state.groups = state.groups.filter(other => other !== group); refresh(); });
        remove.disabled = running || state.pending; box.append(remove);
      }
      if (all) {
        const draft = group.result.draft;
        box.append(el('h3', draft.title, 'font-semibold text-sm text-indigo-700'), text(draft.summary));
        for (const claim of draft.claims) box.append(text(claim.id + ' · ' + claim.requirement_ids.join(', ') + ' · ' + claim.text));
        const audit = el('details', null, 'text-xs text-slate-600');
        audit.append(el('summary', '查看组内讨论记录'));
        for (const idea of group.result.ideas) audit.append(text(idea.role + ' · ' + idea.proposal + ' ' + idea.risk));
        audit.append(text(group.result.challenge || '已从历史项目恢复草案。')); box.append(audit);
      }
      grid.append(box);
    }
    groups.append(grid);
    if (all) {
      const handoffButton = action('进入评审与整合 →', () => operate(handoff), true);
      handoffButton.id = 'native-race-handoff';
      groups.append(handoffButton);
    }
    wrap.append(groups);
  }
  function reviewButton(title, callback, primary = false, clean = false) {
    const button = action(title, () => operate(callback), primary);
    button.disabled = state.pending || (clean && state.dirty);
    if (clean) button.dataset.requiresCleanSpec = '';
    return button;
  }
  function renderReview(wrap) {
    if (!state.project) {
      const empty = card('评审与方案整合');
      empty.append(text('先完成至少两个小组的赛马出案。'), action('返回需求与赛马', () => open('intake-race')));
      wrap.append(empty); return;
    }
    const project = state.project;
    const tabs = card();
    tabs.append(line(...[['handoff', '交接材料'], ['eval', '独立评价'], ['spec', '方案与决策'], ['history', '历史版本']]
      .map(([key, label]) => action(label, () => { state.reviewTab = key; refresh(); }, state.reviewTab === key))));
    wrap.append(tabs);
    if (state.reviewTab === 'handoff') {
      const materials = card('同版本需求与参赛草案');
      materials.append(text(project.requirement.id + '@v' + project.requirement.version + ' · 第 ' + project.round + ' 轮'));
      materials.append(el('pre', JSON.stringify({requirement:project.requirement,drafts:project.drafts},null,2),
        'p-4 bg-slate-900 text-emerald-300 mono text-xs rounded-xl overflow-auto max-h-96'));
      materials.append(reviewButton('生成独立评价', () => reviewAction('evaluate'), true)); wrap.append(materials);
    } else if (state.reviewTab === 'eval') {
      if (!project.evaluations.length) wrap.append(reviewButton('生成独立评价', () => reviewAction('evaluate'), true));
      for (const evaluation of project.evaluations) {
        const box = card(evaluation.draft_id + ' 组独立评价');
        const scores = el('div', null, 'grid grid-cols-3 gap-3');
        for (const criterion of evaluation.criteria) {
          const item = el('div', null, 'rounded-lg p-3 border border-slate-200 bg-slate-50');
          item.append(el('strong', ({value:'用户价值',feasibility:'技术可行性',cost:'成本友好度'})[criterion.criterion] +
            ' · ' + criterion.score + '/5', 'text-xs text-indigo-700'));
          item.append(text(criterion.reason), text('依据 ' + criterion.claim_ids.join(', '), 'text-xs mono text-slate-500'));
          scores.append(item);
        }
        box.append(scores); wrap.append(box);
      }
    } else if (state.reviewTab === 'history') {
      const versions = card('方案历史版本');
      for (const version of [...project.versions].reverse()) {
        const row = el('details', null, 'p-3 border border-slate-200 rounded-lg text-xs');
        row.append(el('summary', version.id + '@v' + version.version + ' · 第 ' + version.round + ' 轮 · ' + version.status));
        row.addEventListener('toggle', async () => {
          if (row.open && row.children.length === 1) {
            try {
              const historical = await get('/preview/projects/' + project.id + '/specifications/' + version.id + '/versions/' + version.version);
              row.append(el('pre', historical.markdown, 'whitespace-pre-wrap mono pt-3'));
            } catch (error) { row.append(text(error.message)); }
          }
        });
        versions.append(row);
      }
      for (const record of project.review_records) versions.append(text('v' + record.version + ' · ' + record.action + ' · ' + record.reason));
      wrap.append(versions);
    } else if (!state.spec) wrap.append(text('在右侧选择方案起点并生成草稿。'));
    else {
      const spec = state.spec;
      const openItems = card('待决事项 · 人工答复与取舍');
      for (const item of spec.open_items) {
        const row = el('div', null, 'p-3 border border-amber-200 rounded-lg bg-amber-50 space-y-2');
        row.id = 'open-' + item.id;
        row.append(el('strong', item.id + ' · ' + item.question, 'text-xs text-amber-950'));
        row.append(text('关联需求 ' + item.affected_requirement_ids.join(', ')));
        for (const option of item.options || []) row.append(text(option.draft_id + ' · ' + option.claim_id + ' · ' + (option.text || option.choice || '')));
        const response = field(row, '人工答复 ' + item.id, item.resolution || '', value => {
          item.resolution = value.trim() || null; dirty();
        }, true);
        const apply = action('将答复写入决策', () => {
          if (!response.value.trim()) { toast('请填写具体答复。'); return; }
          item.resolution = response.value.trim();
          const id = 'DEC-HUMAN-' + item.id;
          const prior = spec.decisions.find(decision => decision.id === id);
          const decision = {id,scope:item.question,choice:item.resolution,reason:'负责人确认待决问题',
            requirement_ids:[...item.affected_requirement_ids],draft_ids:[...new Set((item.options || []).map(option => option.draft_id))]};
          if (!decision.draft_ids.length) decision.draft_ids = project.drafts.map(draft => draft.id);
          if (prior) Object.assign(prior,decision); else spec.decisions.push(decision);
          dirty(); refresh();
        });
        apply.disabled = state.pending; row.append(apply); openItems.append(row);
      }
      if (!spec.open_items.length) openItems.append(text('当前没有待决事项。'));
      wrap.append(openItems);
      const decisions = card('方案取舍与决策编辑');
      decisions.append(action('＋ 新增决策', () => {
        spec.decisions.push({id:'DEC-HUMAN-' + crypto.randomUUID().slice(0,8),scope:'',choice:'',reason:'',
          requirement_ids:[project.requirement.items[0].id],draft_ids:project.drafts.map(draft=>draft.id)});
        dirty();refresh();
      }));
      for (const decision of spec.decisions) {
        const row = el('fieldset', null, 'grid grid-cols-1 md:grid-cols-2 gap-2 p-3 bg-slate-50 border border-slate-200 rounded-lg');
        row.append(el('legend', decision.id, 'mono text-xs text-indigo-700'));
        field(row, '取舍范围', decision.scope, value => { decision.scope = value; dirty(); });
        field(row, '采用做法', decision.choice, value => { decision.choice = value; dirty(); }, true);
        field(row, '决策理由', decision.reason, value => { decision.reason = value; dirty(); }, true);
        field(row, '关联需求', decision.requirement_ids.join(', '), value => { decision.requirement_ids = split(value); dirty(); });
        row.append(action('删除决策 ' + decision.id, () => {
          spec.decisions = spec.decisions.filter(item => item !== decision); dirty(); refresh();
        }));
        decisions.append(row);
      }
      field(decisions, '人工补充说明', spec.notes_markdown || '', value => { spec.notes_markdown = value; dirty(); }, true);
      decisions.append(reviewButton('保存人工编辑为新版本', () => reviewAction('save', {
        expected_version:spec.version,markdown:spec.markdown,notes_markdown:spec.notes_markdown || '',
        decisions:spec.decisions,open_items:spec.open_items,
      }), true));
      const confirm = reviewButton('确认当前方案', () => reviewAction('confirm', {
        expected_version:state.project.specification.version,
      }), true, true);
      confirm.disabled ||= spec.status === 'confirmed'; decisions.append(confirm);
      if (state.project.specification.status === 'confirmed') decisions.append(reviewButton('进入任务分解 →', async () => {
        await window.openTaskPlanner({review_id:project.id,version:state.project.specification.version}); state.module = 'task-planner';
      }, true, true));
      wrap.append(decisions);
      const markdown = card('综合方案正文 · ' + spec.id + '@v' + spec.version + ' · ' + spec.status);
      markdown.append(text(state.dirty ? '结构化编辑待保存，保存后同步生成正文。' : '正文已与结构化决策同步。'));
      markdown.append(el('pre', spec.markdown, 'whitespace-pre-wrap mono text-xs bg-slate-50 rounded-lg p-4'));
      if (spec.status === 'confirmed' && !state.dirty) markdown.append(action('下载方案 Markdown',
        () => download('specification.md', spec.markdown, 'text/markdown;charset=utf-8')));
      wrap.append(markdown);
    }
  }
  async function runAction(name, body = {}) {
    const previous = state.run;
    state.run = await write(projectURL('/runs/' + previous.id + '/actions/' + name),
      {...body,expected_revision:previous.revision});
  }
  function planActions(parent, snapshot, draftDirty) {
    const controls = el('div', null, 'pt-3 border-t border-slate-200 space-y-2');
    controls.append(text('人工核对后可启动开发。执行模式 ' + (state.config?.mode || 'auto') + '。'));
    const start = mode => operate(async () => {
      if (draftDirty()) throw new Error('请先保存任务修改。');
      state.run = await write(projectURL('/runs'), { plan_ref:{id:snapshot.plan.id,version:snapshot.plan.version},mode });
      state.runPlan = structuredClone(snapshot);
      state.module = 'workbench';
    });
    const manual = action('启动开发 · 手动单步', () => start('manual'), true);
    const automatic = action('启动开发 · 自动派发', () => start('auto'));
    manual.disabled = automatic.disabled = state.pending || state.run?.state === 'active';
    controls.append(line(manual, automatic));
    if (state.run) controls.append(action('查看当前运行', () => open('workbench')));
    parent.append(controls);
  }
  function inspect(id) { state.selectedTask = id; showTask(id); }
  function renderDetails(container) {
    const plan = state.runPlan?.plan || getPlan()?.plan;
    if (!plan) return;
    state.selectedTask ||= plan.tasks[0].id;
    const task = plan.tasks.find(task => task.id === state.selectedTask) || plan.tasks[0];
    const taskRun = state.run?.tasks[task.id];
    const box = card(task.id + ' · ' + task.title);
    box.append(text('来源 ' + [...task.requirement_ids,...task.decision_ids,...task.open_item_ids].join(' · ')));
    box.append(text('完成条件 ' + task.completion_condition), text('预计产物 ' + task.write_paths.join(' · ')));
    const select = el('select', null, inputClass); select.setAttribute('aria-label','选择执行任务');
    for (const item of plan.tasks) { const option = el('option',item.id + ' · ' + item.title); option.value=item.id; select.append(option); }
    select.value=task.id; select.onchange=()=>inspect(select.value); box.append(select); container.append(box);
    if (!taskRun) { box.append(text('尚未启动开发。')); return; }
    box.append(text('任务状态 ' + taskRun.state));
    if (taskRun.missing_input_ids.length) {
      const values={};
      for(const id of taskRun.missing_input_ids) {
        const item=state.project.specification.open_items.find(item=>item.id===id);
        const input=field(box,id+' · '+(item?.question || '补充输入'),'',value=>{values[id]=value;},true); input.required=true;
      }
      box.append(action('提交缺失输入',()=>operate(()=>runAction('supply-input',{task_id:task.id,values})),true));
    }
    if (taskRun.state==='failed') {
      const note=taskRun.attempts.at(-1)?.error;
      if(note) box.append(el('pre',note,'whitespace-pre-wrap text-xs text-red-700'));
      box.append(action('重试任务',()=>operate(()=>runAction('retry',{task_id:task.id})),true));
    }
    const candidate=taskRun.artifacts.filter(artifact=>artifact.status==='draft');
    const expected=Object.fromEntries(candidate.map(artifact=>[artifact.output_id,artifact.version]));
    const revisions={};
    for(const artifact of taskRun.artifacts) {
      const entry=el('div',null,'p-3 border border-slate-200 rounded-lg space-y-2');
      entry.append(el('strong',artifact.output_id+'@v'+artifact.version+' · '+artifact.status,'mono text-xs text-indigo-700'));
      entry.append(text('来源 '+artifact.origin+' · 类型 '+artifact.kind));
      if(artifact.kind==='text') {
        const key=state.run.id+':'+artifact.id+':'+artifact.version;
        const original=state.edits.has(key)?state.edits.get(key):artifact.content;
        const input=field(entry,'产物正文 '+artifact.output_id,original,value=>{
          state.edits.set(key,value);revisions[artifact.output_id]={content:value};
        },true);
        input.rows=6; input.readOnly=artifact.status!=='draft';
        if(state.edits.has(key))revisions[artifact.output_id]={content:original};
      } else {
        entry.append(el('code',artifact.commit_sha || '','mono block text-xs break-all'));
        entry.append(text('改动文件 '+artifact.changed_paths.join(', ')));
        if(artifact.status==='draft' && state.config?.real_available) {
          field(entry,'人工修订提交 '+artifact.output_id,artifact.commit_sha,value=>{
            revisions[artifact.output_id]={commit_sha:value.trim()};
          });
        }
      }
      box.append(entry);
    }
    if(candidate.length) {
      let note=''; field(box,'审核备注','',value=>{note=value;});
      box.append(line(
        action('批准产物',()=>operate(()=>runAction('review',{task_id:task.id,decision:'approve',expected_versions:expected,note})),true),
        action('保存修改并批准',()=>operate(()=>runAction('review',{task_id:task.id,decision:'revise',expected_versions:expected,revisions,note}))),
        action('退回修改',()=>operate(()=>runAction('review',{task_id:task.id,decision:'reject',expected_versions:expected,note})))
      ));
    }
    for(const attempt of taskRun.attempts) box.append(text('尝试 '+attempt.id+' · '+(attempt.provider || '待执行')+
      (attempt.model?' / '+attempt.model:'')+' · '+attempt.state));
  }
  function renderTab(tab) {
    if(tab==='graph')return false;
    const container=q(tab==='details'?'#panel-details':tab==='monitor'?'#panel-monitor':'#panel-activity');
    container.replaceChildren();
    const wrap=el('div',null,'p-5 space-y-4 max-w-5xl mx-auto'); container.append(wrap);
    if(tab==='details')renderDetails(wrap);
    else if(tab==='monitor') {
      const snapshot=state.runPlan || getPlan();
      if(snapshot?.schedule)wrap.append(renderGantt(snapshot.plan,snapshot.schedule));
      const summary=card('开发运行');
      summary.append(text(state.run?'运行 '+state.run.id+' · '+state.run.state+' · '+state.run.dispatch:'尚未启动开发'));
      if(state.run)for(const task of Object.values(state.run.tasks)) summary.append(action(task.task_id+' · '+task.state,()=>inspect(task.task_id)));
      wrap.append(summary);
    } else {
      const events=card('运行记录'); wrap.append(events);
      if(!state.run){events.append(text('尚未启动开发。'));return true;}
      get('/api/v2/projects/'+state.project.id+'/runs/'+state.run.id+'/events?limit=500').then(data=>{
        if(!events.isConnected)return;
        for(const event of data.events || data.items || data) events.append(text(
          '#'+event.seq+' · '+event.type+' · '+(event.task_id || '')+' · '+JSON.stringify(event.payload)));
      }).catch(error=>events.append(text(error.message)));
    }
    return true;
  }
  function renderSidebar(module) {
    sidebarBody.replaceChildren(); sidebarBody.append(el('strong','STAGE STEWARD · 主理控制','text-xs text-indigo-700'));
    q('#steward-case-label').textContent=state.project?.requirement.id || '新建需求';
    q('#steward-parallel-label').textContent='max_parallel = '+(getPlan()?.plan.max_parallel || 2);
    if(state.error)sidebarBody.append(text(state.error,'text-xs p-3 rounded-lg bg-red-50 text-red-700'));
    if(state.pending)sidebarBody.append(text('正在处理…'));
    if(module==='intake-race') {
      sidebarBody.append(el('h3','赛马出案主理 AI','text-sm font-bold'),text('独立调用按组隔离。全部小组提交后公开草案并交接评审。'));
      sidebarBody.append(action(state.paused?'恢复赛马派发':'暂停后续派发',()=>{
        state.paused=!state.paused;refresh();if(!state.paused)race();
      }));
      const targets=el('select',null,inputClass);
      for(const group of state.groups){const option=el('option',group.id+' 组');option.value=group.id;targets.append(option);}
      targets.value=state.feedbackGroup || state.groups[0]?.id; targets.onchange=()=>{state.feedbackGroup=targets.value;}; sidebarBody.append(targets);
      const instruction=field(sidebarBody,'负责人补充指令',state.feedback,value=>{state.feedback=value;},true);
      const apply=action('追加指令并重跑该组',()=>{
        if(!instruction.value.trim()){toast('请填写补充指令。');return;}
        const group=state.groups.find(group=>group.id===targets.value); if(!group)return;
        group.prompt+='\n负责人补充指令：'+instruction.value.trim();
        group.status='idle';group.result=null;state.feedback=''; runGroup(group);
      },true);
      apply.disabled=state.groups.some(group=>group.status==='running') || !state.frozen || state.paused; sidebarBody.append(apply);
    } else if(module==='review-synthesis') {
      sidebarBody.append(el('h3','评审与整合主理 AI','text-sm font-bold'));
      if(!state.project){sidebarBody.append(text('等待需求与草案交接。'));return;}
      if(state.project.evaluations.length) {
        const select=el('select',null,inputClass);
        for(const [id,label] of [['merge','融合各组方案'],...state.project.drafts.map(draft=>[draft.id,'以 '+draft.id+' 组为起点'])]){
          const option=el('option',label);option.value=id;select.append(option);
        }
        select.value=state.selection;select.onchange=()=>{state.selection=select.value;};
        sidebarBody.append(select,reviewButton('生成方案草稿',()=>reviewAction('propose',{
          selection:state.selection,expected_version:state.project.latest_version,
        }),true,true));
      }
      if(state.spec)sidebarBody.append(reviewButton('检查当前方案',async()=>{
        const result=await post('/preview/projects/'+state.project.id+'/actions/inspect',{
          expected_version:state.spec.version,markdown:state.spec.markdown,notes_markdown:state.spec.notes_markdown || '',
          decisions:state.spec.decisions,open_items:state.spec.open_items,
        }); state.inspection=result.findings;
      }));
      for(const finding of state.inspection || state.project.findings) {
        sidebarBody.append(text(finding.summary,finding.severity==='error'?'text-xs text-red-700':'text-xs text-slate-600'));
        if(finding.ref_id)sidebarBody.append(action('定位 '+finding.ref_id,()=>{
          state.reviewTab='spec';refresh();document.getElementById('open-'+finding.ref_id)?.scrollIntoView({block:'center'});
        }));
      }
      const target=el('select',null,inputClass);
      for(const draft of state.project.drafts){const option=el('option',draft.id+' 组');option.value=draft.id;target.append(option);}
      target.value=state.feedbackGroup || state.project.drafts[0].id;target.onchange=()=>{state.feedbackGroup=target.value;}; sidebarBody.append(target);
      field(sidebarBody,'返工说明',state.feedback,value=>{state.feedback=value;},true);
      sidebarBody.append(action('退回指定小组补充',()=>{
        if(!state.feedback.trim()){toast('请填写返工说明。');return;}
        state.feedbackGroup=target.value;open('intake-race');
      }));
    } else {
      sidebarBody.append(el('h3','编排与执行主理','text-sm font-bold'));
      if(getPlan())sidebarBody.append(text('计划 v'+getPlan().plan.version+' · '+getPlan().plan.tasks.length+' 项任务'));
      if(state.run) {
        sidebarBody.append(text('运行 '+state.run.id+' · '+state.run.state));
        const advance=action('推进一轮派发',()=>operate(()=>runAction('advance')),true);
        advance.disabled=state.pending || state.run.state!=='active' || state.run.mode!=='manual' || state.run.dispatch!=='active';
        const pause=action(state.run.dispatch==='active'?'暂停后续派发':'继续派发',
          ()=>operate(()=>runAction(state.run.dispatch==='active'?'pause':'resume')));
        pause.disabled=state.pending || state.run.state!=='active'; sidebarBody.append(line(advance,pause));
        for(const finding of state.run.findings) {
          sidebarBody.append(text(finding.summary));
          for(const id of finding.affected_task_ids)sidebarBody.append(action('处理 '+id,()=>inspect(id)));
        }
        const review=Object.values(state.run.tasks).find(task=>task.state==='review');
        if(review)sidebarBody.append(action('审阅并放行 '+review.task_id,()=>inspect(review.task_id),true));
        if(state.run.state==='completed')sidebarBody.append(action('记录本次经验',()=>open('exp-memory')));
      } else sidebarBody.append(text('核对需求覆盖、任务边界与待决问题，再保存和启动开发。'));
    }
  }
  async function loadMemory() {
    state.memory=await get(projectURL('/memory')+'?domain='+encodeURIComponent(state.domain)+'&terms='+encodeURIComponent(state.terms));
  }
  function renderMemory(wrap) {
    const box=card('领域经验卡库');
    if(!state.project){box.append(text('先选择或创建项目。'));wrap.append(box);return;}
    field(box,'领域',state.domain,value=>{state.domain=value;});
    field(box,'关键词或同义词',state.terms,value=>{state.terms=value;});
    box.append(line(action('查询经验',()=>operate(loadMemory),true), action('采用最新已审核经验基线',()=>operate(async()=>{
      await write(projectURL('/memory/baseline'),{});await loadMemory();
    }))));
    if(state.memory && !state.memory.items.length)box.append(text('当前基线没有匹配经验。可更新基线后按领域和关键词检索。'));
    for(const record of state.memory?.items || []) {
      const row=el('article',null,'p-3 rounded-lg border border-slate-200 bg-slate-50 space-y-2');
      row.append(el('strong',record.id+'@v'+record.version+' · '+record.visibility,'text-xs text-indigo-700'));
      row.append(text('问题 '+record.body.problem),text('原因 '+record.body.cause),text('改正 '+record.body.correction));
      row.append(text('验证条件 '+record.body.pass_condition+' · 证据等级 '+record.body.evidence_state));
      if(record.visibility!=='published')row.append(action('审核并发布经验',()=>operate(async()=>{
        await write(projectURL('/memory/publish'),{ref:{id:record.id,version:record.version},expected_version:record.version,note:'项目负责人核对问题、证据与改正步骤后发布。'});
        await loadMemory();
      }))); box.append(row);
    }
    wrap.append(box);
    const create=card('记录一次具体踩坑与改正'); const body=state.memoryDraft;
    for(const [key,label] of [['domain','经验领域'],['problem','本次发现的问题'],['cause','问题原因'],['trigger','再次遇到的触发条件'],
      ['correction','下次执行的改正步骤'],['pass_condition','如何确认已经改正'],['evidence','本次证据或复现记录'],['tags','关键词、同义词']]) {
      field(create,label,key==='tags'?(body[key] || []).join(', '):(body[key] || ''),value=>{
        body[key]=key==='tags'?split(value):value;
      },key!=='domain'&&key!=='tags');
    }
    create.append(action('保存候选经验',()=>operate(async()=>{await write(projectURL('/memory'),body);state.memoryDraft={};await loadMemory();}),true));
    wrap.append(create);
  }
  function render(module) {
    state.module=module;
    q('#progress-pct').style.display = state.run ? '' : 'none';
    q('#progress-bar').parentElement.style.display = state.run ? '' : 'none';
    q('#btn-toggle-right-sidebar').style.display='';q('#right-steward-sidebar').style.display='';
    document.querySelectorAll('.ws-tab').forEach(tab=>{tab.style.display='';});
    if(module==='task-planner'||module==='workbench')return false;
    q('#workbench-split-view').classList.add('hidden'); dedicated.classList.remove('hidden'); dedicated.replaceChildren();
    const wrap=el('div',null,'max-w-5xl mx-auto space-y-5 text-xs'); dedicated.append(wrap);
    if(state.error)wrap.append(text(state.error,'p-3 rounded-lg bg-red-50 text-red-700 text-sm'));
    if(module==='intake-race')renderIntake(wrap);
    else if(module==='review-synthesis')renderReview(wrap);
    else if(module==='exp-memory')renderMemory(wrap);
    else if(module==='model-router') {
      const box=card('高低配模型路由');
      box.append(text('配置文件 se_agent/model_config.json，可用 SE_AGENT_MODEL_CONFIG 指向自定义文件。'));
      box.append(el('pre',JSON.stringify(state.config?.router,null,2),'whitespace-pre-wrap mono text-xs'));wrap.append(box);
    } else {
      const box=card(module==='sqlite-store'?'SQLite 项目存储':'项目记录与数字人上下文');
      box.append(text('需求、评审版本、任务计划、执行产物与领域经验共用当前项目数据库。'));
      if(state.project)box.append(action('查看当前项目可见记录',async()=>{
        try {
          const records=await get(projectURL('/memory/records'));
          box.append(el('pre',JSON.stringify(records,null,2),'whitespace-pre-wrap mono text-xs max-h-96 overflow-auto'));
        }catch(error){toast(error.message);}
      }));
      if(state.project)box.append(action('下载当前项目交付记录',()=>operate(async()=>{
        const data=await get(projectURL());download('project-delivery.json',JSON.stringify(data,null,2),'application/json');
      })));wrap.append(box);
    }
    renderSidebar(module);return true;
  }
  async function initialize() {
    try {
      const defaults=await get('/preview/config');state.extraPrompt=defaults.extra_prompt;
      state.groups=Object.entries(defaults.prompts).map(([id,prompt])=>({id,prompt,status:'idle'}));
      q('#planner-model-mode').textContent=defaults.mode==='model'?'已连接模型':'本地模拟';
      await updateProjects();
      if(state.projects.length) {
        await load(state.projects[0].id);state.module=state.run?'workbench':getPlan()?'task-planner':'review-synthesis';
      }refresh();
    }catch(error){state.error=error.message;refresh();}
  }
  initialize();
  setInterval(async()=>{
    if(polling || !state.run || state.run.state!=='active' || state.module!=='workbench' || state.pending)return;
    polling=true;
    try {
      const run=await get(projectURL('/runs/'+state.run.id));
      if(run.revision!==state.run.revision){state.run=run;refresh();}
    }catch(error){state.error=error.message;}
    finally{polling=false;}
  },1500);
  return {render,sidebar:renderSidebar,renderTab,planActions,inspect,
    executionSnapshot:()=>state.module === 'workbench' ? state.runPlan : null,
    taskStatus:id=>({done:'completed',review:'in_progress',failed:'failed',running:'running'})[state.run?.tasks[id]?.state] ||
      (state.run?'pending':'planned')};
};
