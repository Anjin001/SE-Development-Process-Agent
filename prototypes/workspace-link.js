/* Bind the QLM interface to the combined workspace services. */
window.createWorkspaceLink = function (hooks) {
  const { q, el, open, action, toast, getPlan, accept, setSpecification,
    clearPlan, isDirty, renderGantt, download, showTask } = hooks;
  const dedicated = q('#module-dedicated-view');
  const nav = q('#left-sidebar nav');
  const stageBounds = {
    'intake-race':[0,1], 'review-synthesis':[2,5], 'task-planner':[6,7], workbench:[8,9],
  };
  const stages = ['groups','race','evaluate','merge','decisions','confirm','plan','start','develop','delivery'];
  const stageBadges = new Map(Object.keys(stageBounds).map(key=>{
    const navItem=q('.nav-item[data-module="'+key+'"]');
    const badge=el('span',null,'sb-hide-on-collapse text-[10px] shrink-0');
    navItem?.append(badge); return [key,badge];
  }));
  const primaryNav = action('项目主理人',()=>{state.followSteward=true;open('steward');},true);
  primaryNav.dataset.module='steward';
  primaryNav.dataset.title='项目主理人';
  primaryNav.classList.add('nav-item','w-full');
  if (window.createStewardLink && !nav.querySelector('[data-module=steward]')) nav.prepend(primaryNav);
  const sidebarBody = q('#steward-sidebar-body');
  const cardClass = 'bg-white border border-slate-200 rounded-xl p-5 space-y-3 shadow-2xs';
  const inputClass = 'w-full border border-slate-300 rounded-lg px-3 py-2 bg-white text-xs text-slate-800';
  const oldPicker = q('#select-project');
  const picker = oldPicker.cloneNode(false);
  picker.style.display = '';
  picker.setAttribute('aria-label', '当前项目');
  oldPicker.replaceWith(picker);
  const state = {
    module: 'steward', config: null, project: null, run: null, runPlan: null, reviewTab: 'handoff',
    spec: null, dirty: false, selection: 'merge', inspection: null, pending: false, error: '',
    markdown: '# 设备预约系统\n\n- 用户可以查看设备并提交预约。\n- 管理员可以停用设备，停用后不能新增预约。\n- 用户可以在我的预约中查看状态。',
    version: 1, frozen: null, groups: [], extraPrompt: '', paused: false, queue: false,
    simulateFailure: false, selectedTask: null, edits: new Map(), feedback: '', feedbackGroup: '',
    target: null, runQuestions: [], activity: null, eventTrail: [], statusError: '',
    memory: null, domain: '', terms: '', projects: [], memoryDraft: {},
    followSteward: true, selectedJobId: null, syncedJob: '', syncInFlight: null, syncError: '',
  };
  let epoch = 0;
  let polling = false;
  const statusBar = el('div', null, 'px-5 py-2 bg-indigo-50 border-b border-indigo-100 text-xs shrink-0');
  statusBar.id = 'workspace-live-status';
  q('#active-badge').parentElement.parentElement.after(statusBar);
  const targetButton = action('产物位置', () => showTarget());
  const helpButton = action('使用答疑 AI', () => showHelp());
  q('#active-badge').before(targetButton, helpButton);
  const steward = window.createStewardLink?.({
    q,el,action,get,post,open,modelMode:()=>state.config?.discussion_mode,limits:()=>state.config?.limits,
    existingProject:()=>state.project,
    onJobChange:handleJobChange,
    canContinue:()=>!(state.dirty || isDirty()),
    openProject: id=>{
      if(state.dirty || isDirty()){toast('请先保存当前编辑。');return;}
      state.followSteward=true;
      open(steward.current()?.run_id?'workbench':'task-planner');
    },
  });
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
    if (update) input.oninput = () => update(input.value);
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
  async function post(path, body, headers = {}) {
    const response = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      const error = data?.error;
      throw new Error(typeof error === 'string' ? error : error?.message || '操作失败，请重试。');
    }
    return response.json();
  }
  function write(path, body) {
    return post(path, body, { 'Idempotency-Key': crypto.randomUUID() });
  }
  let documentRequest = 0;
  for (const [id, title, path] of [
    ['btn-doc', '整合运行说明', '/workspace/docs/runtime'],
    ['btn-changelog', '接口契约 v2', '/workspace/docs/contract'],
  ]) {
    const previous = q('#' + id);
    const button = previous.cloneNode(true);
    previous.replaceWith(button);
    button.onclick = async () => {
      try {
        const request = ++documentRequest;
        const data = await get(path);
        if (request !== documentRequest) return;
        q('#info-modal-title').textContent = title;
        const body = q('#info-modal-body');
        body.replaceChildren(el('pre', data.markdown, 'whitespace-pre-wrap mono text-xs'));
        body.scrollTop = 0;
        const modal = q('#info-modal');
        if (!modal.open) modal.showModal();
      } catch (error) {
        toast(error.message);
      }
    };
  }
  function refresh() { open(state.module); }
  function linkedJob() { return state.followSteward ? steward?.current() : null; }
  function jobKey(job) {
    return [job.id,job.project_id,job.stage,job.status,job.gate,job.run_id].join(':');
  }
  function handleJobChange() {
    const selected=steward?.current();
    if(selected?.id && selected.id!==state.selectedJobId) {
      state.selectedJobId=selected.id;state.followSteward=true;state.syncError='';state.reviewTab='spec';
    }else if(!selected){
      state.selectedJobId=null;state.syncedJob='';
    }
    updateStageBadges();
    const job=linkedJob();
    if(job?.project_id && !state.dirty && !isDirty()) syncLinkedProject(job);
    if(job && state.module!=='steward' && stageBounds[state.module] && !state.dirty && !isDirty()) refresh();
  }
  function updateStageBadges() {
    const job=linkedJob();
    const phase=job?stages.indexOf(job.stage):-1;
    for(const [module,badge] of stageBadges){
      if(!job){badge.textContent='';continue;}
      const [start,end]=stageBounds[module];
      const done=phase>end || job.status==='completed';
      const current=phase>=start && phase<=end && !done;
      badge.textContent=done?'完成':current?(job.status==='gate'?'待确认':job.status==='paused'?'暂停':
        job.status==='failed'?'卡住':job.status==='cancelled'?'停止':job.status==='pausing'?'收尾':'进行中'):'待开始';
      badge.className='sb-hide-on-collapse text-[10px] shrink-0 rounded px-1 '+(done?'text-emerald-700 bg-emerald-50':
        current?'text-indigo-700 bg-indigo-50':'text-slate-500');
    }
  }
  function syncLinkedProject(job) {
    if(!job?.project_id || state.dirty || isDirty() || state.pending) return;
    const key=jobKey(job);
    if(state.syncedJob===key || state.syncInFlight || state.syncError===key) return;
    const current=()=>linkedJob()?.id===job.id && linkedJob()?.project_id===job.project_id &&
      !state.dirty && !isDirty() && !state.pending;
    state.syncInFlight=key;
    load(job.project_id,current).then(loaded=>{
      if(loaded)state.syncedJob=key;
    }).catch(()=>{if(current())state.syncError=key;}).finally(()=>{
      state.syncInFlight=null;
      if(linkedJob()?.id===job.id && !state.dirty && !isDirty() && !state.pending) refresh();
    });
  }
  function clearStageError() {state.syncError='';refresh();}
  function permission(module) {
    const job=linkedJob();
    if(!job) return true;
    if(!job.project_id || state.project?.id!==job.project_id || state.syncedJob!==jobKey(job)) return false;
    if(module==='review-synthesis') return state.spec?.status==='draft' &&
      (job.status==='gate' && job.gate==='spec' || job.status==='paused' && job.stage==='confirm');
    if(module==='task-planner') return !!getPlan() &&
      (job.status==='gate' && job.gate==='plan' || job.status==='paused' || job.status==='completed');
    if(module==='workbench') return state.run?.mode==='manual' ||
      (job.status==='gate' && job.gate==='verification') ||
      state.runPlan?.plan.tasks.some(task=>task.phase==='verification' &&
        ['review','failed'].includes(state.run?.tasks[task.id]?.state)) ||
      (job.status==='paused' && state.run?.dispatch==='paused');
    return false;
  }
  function stageActions(module) {
    const job=linkedJob();
    if(!job) return [];
    const display=({running:'主理人正在推进',gate:'已停在你设置的确认点',paused:'主理人已暂停',
      pausing:'主理人正在收尾',failed:'执行遇到问题',completed:'本轮已交付',cancelled:'本轮已停止'})[job.status] || job.status;
    const controls=[text(display+' · '+job.activity,'text-xs font-semibold text-indigo-800')];
    if(permission(module)) {
      controls.push(text(module==='review-synthesis'?'可编辑方案，保存后主理人会使用新版本。':
        module==='task-planner'?(job.run_id?'可保存新的计划版本；已启动的运行继续使用原计划。':
          '可编辑任务、依赖和估时；保存后主理人将使用最新计划。'):'暂停期间可审阅或修订待审核产物。'));
    } else if(module!=='intake-race'){
      controls.push(text('此处可查看结果。需要修改时，可以回主理人对话提出要求，或在相应确认点手动编辑。'));
    }
    const actions=[];
    if(job.status==='running'){
      const phase=stages.indexOf(job.stage);
      if(module==='review-synthesis' && phase<=5 && !job.settings.gates.includes('spec'))
        actions.push(action('在方案确认前暂停让我编辑',()=>steward.enableGate('spec'),true));
      if(module==='task-planner' && phase<=7 && !job.settings.gates.includes('plan'))
        actions.push(action('在开工前暂停让我编辑',()=>steward.enableGate('plan'),true));
      actions.push(action('暂停主理人',()=>steward.control('pause')));
    }
    if(job.status==='paused' || job.status==='gate')
      actions.push(action('交回主理人继续',()=>{
        if(state.dirty || isDirty()){toast('请先保存手动修改。');return;}
        steward.control('continue');
      },true));
    actions.push(action('返回主理人对话',()=>open('steward')));
    controls.push(line(...actions));
    return controls;
  }
  function linkedStageWaiting(module) {
    const job=linkedJob();
    if(!job || !stageBounds[module] || module==='intake-race')return false;
    if(!job.project_id || state.project?.id!==job.project_id || !state.syncedJob.startsWith(job.id+':'))return true;
    if(module==='task-planner' && !getPlan())return true;
    if(module==='workbench' && !getPlan())return true;
    return false;
  }
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
    if(linkedJob() && (!permission('review-synthesis') || name!=='save'))
      throw new Error('请在方案确认点编辑并保存，由主理人负责确认。');
    const path = '/preview/projects/' + state.project.id + '/actions/' + name;
    const labels = {evaluate: '主理人组织独立评审', propose: '主理人汇总各组建议', confirm: '保存确认版方案'};
    setProject(await tracked(labels[name] || '核对方案修改', path, body));
    state.reviewTab = name === 'evaluate' ? 'eval' : 'spec';
  }
  async function load(projectId, current = () => true) {
    const data = await get('/workspace/projects/' + projectId);
    const runPlan = data.run ? await get('/api/v2/projects/' + projectId + '/plans/' +
      data.run.plan_ref.id + '/versions/' + data.run.plan_ref.version) : null;
    if(!current())return false;
    epoch++;
    setProject(data.review); clearPlan();
    if (data.planning) accept(data.planning);
    state.run = data.run; state.target = data.target || null;
    state.selectedTask = null; state.runQuestions = []; state.eventTrail = []; state.statusError = ''; state.activity = null;
    state.runPlan = runPlan;
    state.markdown = data.review.requirement.markdown;
    state.version = data.review.requirement.version;
    state.frozen = data.review.requirement;
    state.groups = data.review.drafts.map(draft => ({
      id: draft.id, prompt: '复核本组既有草案，逐条对齐需求并补充边界情况。',
      status: 'done', result: { draft, ideas: [], challenge: '' },
    }));
    if (!Array.from(picker.options).some(option=>option.value===projectId)) {
      const option=el('option',data.review.requirement.items[0].title+' · '+projectId.slice(0,6));
      option.value=projectId;picker.append(option);
    }
    picker.value = projectId; state.memory = null;
    if (state.run) await readRunDetails(state.run);
    return true;
  }
  async function updateProjects() {
    state.config = await get('/workspace/config');
    showModelMode();
    state.projects = state.config.projects;
    picker.replaceChildren();
    const blank = el('option', '＋ 新建需求'); blank.value = ''; picker.append(blank);
    for (const project of state.projects) {
      const option = el('option', project.title + ' · ' + project.id.slice(0, 6));
      option.value = project.id; picker.append(option);
    }
    picker.value = state.project?.id || '';
  }
  function showModelMode() {
    const badge = q('#planner-model-mode');
    badge.textContent = state.config?.discussion_mode === 'model' ? '真实使用 · 模型已配置' : '工作台 · 配置 API';
  }
  picker.onchange = () => {
    const id = picker.value;
    if ((state.dirty || isDirty()) && !confirm('切换项目会清除当前未保存的编辑，是否继续？')) {
      picker.value = state.project?.id || ''; return;
    }
    operate(async () => {
      if (id) {
        state.followSteward = !steward?.current()?.project_id || steward.current().project_id===id;
        state.syncedJob='';state.syncError='';
        await load(id);
        if(state.module!=='steward') state.module = state.run ? 'workbench' : getPlan() ? 'task-planner' : 'review-synthesis';
      }
      else {
        state.followSteward=true;state.syncedJob='';state.syncError='';
        epoch++; state.project = state.run = state.runPlan = state.spec = state.frozen = null;
        state.memory = null; state.version = 1; state.target = null;
        state.runQuestions = []; state.eventTrail = []; state.selectedTask = null; state.activity = null;
        state.groups.forEach(group => { group.status = 'idle'; group.result = null; });
        clearPlan(); setSpecification(null); state.module = steward ? 'steward' : 'intake-race';
        steward?.newProject();
      }
    });
  };
  async function runGroup(group) {
    if (!state.frozen || state.paused || group.status === 'running') return;
    const current = epoch;
    const frozen = structuredClone(state.frozen);
    group.status = 'running'; group.error = ''; refresh();
    try {
      const result = await tracked(group.id + ' 组开始出案', '/preview/group', {
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
    const pending = state.groups.filter(group=>group.status!=='done' && group.status!=='running');
    const worker = async () => {
      while (!state.paused && epoch === currentEpoch && pending.length) await runGroup(pending.shift());
    };
    try { await Promise.all(Array.from({length: Math.min(3,pending.length)}, worker)); }
    finally { state.queue = false; refresh(); }
  }
  async function handoff() {
    const payload = { requirement: state.frozen, drafts: state.groups.map(group => group.result.draft) };
    const same = state.project && JSON.stringify(state.project.requirement) === JSON.stringify(state.frozen);
    if (same) await reviewAction('update-drafts', { ...payload, expected_round: state.project.round });
    else {
      setProject(await tracked('保存赛马记录并交接评审', '/preview/projects', payload));
      clearPlan(); state.run = state.runPlan = state.target = null;
      state.runQuestions = []; state.eventTrail = []; state.selectedTask = null;
    }
    await updateProjects(); state.reviewTab = 'handoff'; state.module = 'review-synthesis';
  }
  function renderIntake(wrap) {
    const requirement = card('需求录入与快照版本控制');
    const input = field(requirement, '需求 Markdown', state.markdown, value => { state.markdown = value; }, true);
    input.rows = 8; input.disabled = !!state.frozen || state.pending;
    if (!state.frozen) {
      const hint = text('标题只用于分节。请在标题下写至少一条具体需求，例如「- 用户可以发布图文笔记。」');
      hint.id = 'requirement-markdown-hint';
      input.setAttribute('aria-describedby', hint.id);
      requirement.append(hint);
    }
    const running = state.groups.some(group => group.status === 'running');
    const confirm = action('确认需求快照', () => operate(async () => {
      state.frozen = await tracked('检查需求正文并锁定快照', '/preview/snapshot', { markdown: state.markdown, version: state.version });
      q('#sidebar-req-ver').textContent = state.frozen.id + '@v' + state.version;
    }), true);
    confirm.disabled = !!state.frozen || state.pending;
    const edit = action('编辑并创建新版本', () => {
      epoch++; state.version++; state.frozen = null;
      for (const group of state.groups) { group.status = 'idle'; group.result = null; group.error = ''; }
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
      const errorNotice = group.error ? text(group.error, 'text-xs text-red-700') : null;
      const prompt = field(box, group.id + ' 组系统提示词', group.prompt, value => {
        group.prompt = value; group.status = 'idle'; group.result = null;
        if (errorNotice) errorNotice.hidden = true;
        group.error = '';
        q('#native-race-handoff')?.setAttribute('disabled', '');
      }, true);
      prompt.disabled = running || state.pending;
      if (errorNotice) box.append(errorNotice);
      if (group.status === 'done' && !all) {
        box.append(text('本组草案已保留，等待其他组提交后一起公开。'));
      }
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
    if (state.groups.some(g => g.status === 'failed')) {
      groups.append(text('先重试失败组。已提交的组无需重跑。'));
    }
    if (all) {
      const handoffButton = action('进入评审与整合 →', () => operate(handoff), true);
      handoffButton.id = 'native-race-handoff';
      groups.append(handoffButton);
    }
    wrap.append(groups);
  }
  function reviewButton(title, callback, primary = false, clean = false) {
    const button = action(title, () => operate(callback), primary);
    button.disabled = state.pending || (clean && state.dirty) || (linkedJob() && !permission('review-synthesis'));
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
    const job=linkedJob();
    const canEdit=permission('review-synthesis');
    if (!job && (!project.specification || project.specification.status !== 'confirmed')) {
      const shortcut = card('主理人整合方案');
      shortcut.append(text('自动组织独立评审、融合各组可取的做法，再把需要你确认的方案交给你。'));
      if (!state.spec) shortcut.append(reviewButton('主理人整合方案', async () => {
        if (!state.project.evaluations.length) await reviewAction('evaluate');
        await reviewAction('propose', { selection: 'merge', expected_version: state.project.latest_version });
      }, true, true));
      wrap.append(shortcut);
    }
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
    } else if (!state.spec) wrap.append(text(job?'主理人还没生成综合方案。':'在右侧选择方案起点并生成草稿。'));
    else {
      const spec = state.spec;
      const openItems = el('details',null,cardClass);
      openItems.open=!job;
      openItems.append(el('summary',job?'主理人已处理的待决事项及其来源':'待决事项 · 人工答复与取舍',
        'cursor-pointer text-sm font-bold text-slate-900'));
      for (const item of spec.open_items) {
        const row = el('div', null, 'p-3 border border-amber-200 rounded-lg bg-amber-50 space-y-2');
        row.id = 'open-' + item.id;
        row.append(el('strong', item.id + ' · ' + item.question, 'text-xs text-amber-950'));
        row.append(text('关联需求 ' + item.affected_requirement_ids.join(', ')));
        for (const option of item.options || []) row.append(text(option.draft_id + ' · ' + option.claim_id + ' · ' + (option.text || option.choice || '')));
        const response = field(row, '处理答复 ' + item.id, item.resolution || '', value => {
          item.resolution = value.trim() || null; dirty();
        }, true);
        response.readOnly=!canEdit;
        const apply = action('将答复写入决策', () => {
          if (!response.value.trim()) { toast('请填写具体答复。'); return; }
          item.resolution = response.value.trim();
          const id = item.decision_id || 'DEC-HUMAN-' + item.id;
          item.decision_id=id;
          const prior = spec.decisions.find(decision => decision.id === id);
          const decision = {id,scope:item.question,choice:item.resolution,reason:'负责人确认待决问题',
            requirement_ids:[...item.affected_requirement_ids],draft_ids:[...new Set((item.options || []).map(option => option.draft_id))]};
          if (!decision.draft_ids.length) decision.draft_ids = project.drafts.map(draft => draft.id);
          if (prior) Object.assign(prior,decision); else spec.decisions.push(decision);
          dirty(); refresh();
        });
        if(canEdit){apply.disabled = state.pending; row.append(apply);} openItems.append(row);
      }
      if (!spec.open_items.length) openItems.append(text('当前没有待决事项。'));
      wrap.append(openItems);
      const decisions = card('方案取舍与决策编辑');
      if(canEdit)decisions.append(action('＋ 新增决策', () => {
        spec.decisions.push({id:'DEC-HUMAN-' + crypto.randomUUID().slice(0,8),scope:'',choice:'',reason:'',
          requirement_ids:[project.requirement.items[0].id],draft_ids:project.drafts.map(draft=>draft.id)});
        dirty();refresh();
      }));
      for (const decision of spec.decisions) {
        const row = el('fieldset', null, 'grid grid-cols-1 md:grid-cols-2 gap-2 p-3 bg-slate-50 border border-slate-200 rounded-lg');
        row.append(el('legend', decision.id, 'mono text-xs text-indigo-700'));
        field(row, '取舍范围', decision.scope, value => { decision.scope = value; dirty(); }).readOnly=!canEdit;
        field(row, '采用做法', decision.choice, value => {
          const prior=decision.choice;
          decision.choice=value;
          for(const item of spec.open_items){
            if(item.decision_id===decision.id || (!item.decision_id && prior?.trim()===item.resolution?.trim()
                && item.affected_requirement_ids.every(ref=>decision.requirement_ids.includes(ref)))) {
              item.decision_id=decision.id;item.resolution=value.trim() || null;
            }
          }
          dirty();
        }, true).readOnly=!canEdit;
        field(row, '决策理由', decision.reason, value => { decision.reason = value; dirty(); }, true).readOnly=!canEdit;
        field(row, '关联需求', decision.requirement_ids.join(', '), value => { decision.requirement_ids = split(value); dirty(); }).readOnly=!canEdit;
        if(canEdit && !spec.open_items.some(item=>item.decision_id===decision.id))row.append(action('删除决策 ' + decision.id, () => {
          spec.decisions = spec.decisions.filter(item => item !== decision); dirty(); refresh();
        }));
        decisions.append(row);
      }
      field(decisions, '人工补充说明', spec.notes_markdown || '', value => { spec.notes_markdown = value; dirty(); }, true).readOnly=!canEdit;
      if(canEdit)decisions.append(reviewButton('保存人工编辑为新版本', () => reviewAction('save', {
        expected_version:spec.version,markdown:spec.markdown,notes_markdown:spec.notes_markdown || '',
        decisions:spec.decisions,open_items:spec.open_items,
      }), true));
      const confirm = reviewButton('确认当前方案', () => reviewAction('confirm', {
        expected_version:state.project.specification.version,
      }), true, true);
      confirm.disabled ||= spec.status === 'confirmed'; if(!job)decisions.append(confirm);
      if (!job && state.project.specification.status === 'confirmed') decisions.append(reviewButton('进入任务分解 →', async () => {
        await localStep('分解任务，检查依赖并计算甘特排期',
          () => window.openTaskPlanner({review_id:project.id,version:state.project.specification.version}));
        state.module = 'task-planner';
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
    await readRunDetails(state.run);
  }
  function planActions(parent, snapshot, draftDirty) {
    const controls = el('div', null, 'pt-3 border-t border-slate-200 space-y-2');
    const job=linkedJob();
    if(job) {
      const form=parent.closest('form');
      if(!permission('task-planner') && form) {
        queueMicrotask(()=>{
          for(const input of form.querySelectorAll('input,textarea,select,fieldset,button[type="submit"]')) input.disabled=true;
          for(const button of form.querySelectorAll('button')){
            if(/新增任务|移除未保存的任务/.test(button.textContent))button.disabled=true;
          }
        });
        form.addEventListener('submit',event=>{
          if(permission('task-planner'))return;
          event.preventDefault();event.stopImmediatePropagation();
          toast('请在开工确认点手动修改任务。');
        },true);
      }
      controls.append(...stageActions('task-planner'));
      if(state.run)controls.append(action('查看本次运行',()=>open('workbench')));
      parent.append(controls);
      return;
    }
    const real = !!snapshot.plan.base_commit && (!!state.target?.repo || state.config?.mode === 'real');
    controls.append(text(real ? '真实代码执行 · '+(state.target?.repo || '启动时配置的 Git 仓库')
      : '模拟执行 · 不会生成应用源码。'));
    controls.append(action(state.target?.path ? '查看或修改产物位置' : '选择产物保存位置', showTarget));
    const start = mode => operate(async () => {
      if (draftDirty()) throw new Error('请先保存任务修改。');
      state.run = await localStep('绑定计划版本并启动开发', () => write(projectURL('/runs'), {
        plan_ref:{id:snapshot.plan.id,version:snapshot.plan.version}, mode, steward:mode==='auto',
      }));
      state.selectedTask = null; await readRunDetails(state.run);
      state.runPlan = structuredClone(snapshot);
      state.module = 'workbench';
    });
    const manual = action('启动开发 · 手动单步', () => start('manual'), true);
    const automatic = action('主理人启动自动开发', () => start('auto'), true);
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
    const canEdit=permission('workbench');
    const box = card(task.id + ' · ' + task.title);
    box.append(text('来源 ' + [...task.requirement_ids,...task.decision_ids,...task.open_item_ids].join(' · ')));
    box.append(text('完成条件 ' + task.completion_condition), text('预计产物 ' + task.write_paths.join(' · ')));
    const select = el('select', null, inputClass); select.setAttribute('aria-label','选择执行任务');
    for (const item of plan.tasks) { const option = el('option',item.id + ' · ' + item.title); option.value=item.id; select.append(option); }
    select.value=task.id; select.onchange=()=>inspect(select.value); box.append(select); container.append(box);
    if (!taskRun) { box.append(text('尚未启动开发。')); return; }
    box.append(text('任务状态：' + (taskLabels[taskRun.state] || taskRun.state)));
    for (const blocker of taskRun.blockers || []) {
      box.append(text('暂未放行 · ' + blocker.ref_id + ' · ' + blocker.message, 'text-xs text-amber-800'));
    }
    if (taskRun.artifacts.some(artifact=>artifact.status==='rejected')) {
      const history=el('div',null,'p-3 rounded-lg bg-red-50 text-red-800 space-y-2');
      history.append(el('strong','评审退回记录','text-sm')); box.append(history);
      const finding=state.run.findings?.find(item=>item.code==='TASK_FAILED' && item.ref_id===task.id);
      const affected=finding?.affected_task_ids?.filter(id=>id!==task.id) || [];
      if(affected.length) history.append(text('等待返工的下游：'+affected.join('、')));
      const endpoint='/api/v2/projects/'+state.project.id+'/runs/'+state.run.id+'/events';
      const loadReviews=async () => {
        let after=0;
        do {
          const data=await get(endpoint+'?limit=100&after='+after);
          if(!history.isConnected)return;
          for(const event of data.items || []) {
            if(event.task_id===task.id && event.type==='artifact.rejected')
              history.append(text('#'+event.seq+' · '+(event.payload?.note || '已退回修改')));
          }
          if(!data.has_more || data.next_after<=after)break;
          after=data.next_after;
        } while(history.isConnected);
      };
      loadReviews().catch(error=>{if(history.isConnected)history.append(text(error.message));});
    }
    if (taskRun.activity?.length) box.append(text(taskRun.activity.at(-1).label));
    if (taskRun.missing_input_ids.length) {
      const item = state.runQuestions.find(item => item.tasks.includes(task.id));
      if (item && state.run.state === 'active' && canEdit) renderQuestion(box, item);
      else box.append(text('本次运行已停止或问题待加载；不会向下派发。'));
    }
    if (taskRun.state==='failed') {
      const note=taskRun.attempts.at(-1)?.error;
      if(note) box.append(text(errorMessage(note),'text-xs text-red-700'));
      if(canEdit)box.append(action('重试任务',()=>operate(()=>runAction('retry',{task_id:task.id})),true));
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
        input.rows=6; input.readOnly=artifact.status!=='draft' || !canEdit;
        if(state.edits.has(key))revisions[artifact.output_id]={content:original};
      } else {
        entry.append(el('code',artifact.commit_sha || '','mono block text-xs break-all'));
        const attempt = taskRun.attempts.find(row=>row.id===artifact.attempt_id) || taskRun.attempts.at(-1);
        if (attempt?.checkout_commit && state.target?.repo) {
          entry.append(text('目标仓库：'+state.target.repo),
            text('任务分支：attempt/'+state.run.id+'/'+attempt.id));
        } else if (!attempt?.checkout_commit) entry.append(text('模拟提交，不对应本地源码。'));
        entry.append(text('改动文件 '+artifact.changed_paths.join(', ')));
        if(canEdit && artifact.status==='draft' && (state.target?.repo || state.config?.real_available)) {
          field(entry,'人工修订提交 '+artifact.output_id,artifact.commit_sha,value=>{
            revisions[artifact.output_id]={commit_sha:value.trim()};
          });
        }
      }
      box.append(entry);
    }
    if(candidate.length && state.run.state === 'active' && canEdit) {
      let note=''; field(box,'审核备注','',value=>{note=value;});
      box.append(line(
        action('批准产物',()=>operate(()=>runAction('review',{task_id:task.id,decision:'approve',expected_versions:expected,note})),true),
        action('保存修改并批准',()=>operate(()=>runAction('review',{task_id:task.id,decision:'revise',expected_versions:expected,revisions,note}))),
        action('退回修改',()=>operate(()=>runAction('review',{task_id:task.id,decision:'reject',expected_versions:expected,note})))
      ));
    }
    if(task.phase==='verification' && state.run.state==='active' && canEdit &&
       ['review','failed'].includes(taskRun.state)) {
      const ancestors=new Set();
      const byId=new Map(plan.tasks.map(item=>[item.id,item]));
      const visit=id=>{
        for(const before of byId.get(id)?.depends_on || []){
          if(ancestors.has(before))continue;
          ancestors.add(before); visit(before);
        }
      };
      visit(task.id);
      const targets=plan.tasks.filter(item=>ancestors.has(item.id) && state.run.tasks[item.id]?.state==='done');
      if (targets.length) {
        const rework=el('div',null,'p-3 border border-rose-200 rounded-lg space-y-2');
        rework.append(el('strong','按验收问题退回责任任务','text-sm'));
        const target=el('select',null,inputClass);
        target.setAttribute('aria-label','返工目标');
        for(const item of targets){
          const option=el('option',item.id+' · '+item.title); option.value=item.id; target.append(option);
        }
        rework.append(target);
        let evidence='';
        field(rework,'失败步骤、实际结果与修改要求','',value=>{evidence=value;},true);
        rework.append(action('退回到选中的任务',()=>{
          if(!evidence.trim()){toast('请填写验收证据和修改要求。');return;}
          operate(async()=>{
            await runAction('verification-return',{task_id:task.id,target_task_id:target.value,note:evidence});
            inspect(target.value);
          });
        }));
        box.append(rework);
      }
    }
    for(const attempt of taskRun.attempts) box.append(text('尝试 '+attempt.id+' · '+(attempt.provider || '待执行')+
      (attempt.model?' / '+attempt.model:'')+' · '+attempt.state));
  }
  function renderTab(tab, selection) {
    if (selection && (state.runPlan || getPlan())?.plan.tasks.some(t=>t.id===selection)) state.selectedTask = selection;
    if(tab==='graph')return false;
    const container=q(tab==='details'?'#panel-details':tab==='monitor'?'#panel-monitor':'#panel-activity');
    container.replaceChildren();
    const wrap=el('div',null,'p-5 space-y-4 max-w-5xl mx-auto'); container.append(wrap);
    if(tab==='details')renderDetails(wrap);
    else if(tab==='monitor') {
      const snapshot=state.runPlan || getPlan();
      if(snapshot?.schedule)wrap.append(renderGantt(snapshot.plan,snapshot.schedule,state.run,inspect));
      const summary=card('开发运行');
      summary.append(text(runStatus().title));
      if(state.run)for(const task of Object.values(state.run.tasks)) summary.append(action(task.task_id+' · '+(taskLabels[task.state] || task.state),()=>inspect(task.task_id)));
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
    if(linkedJob() && stageBounds[module]) {
      sidebarBody.append(...stageActions(module));
      if(module==='workbench' && state.run){
        sidebarBody.append(text(runStatus().title));
        if(state.run.mode==='manual' && state.run.state==='active'){
          const paused=state.run.dispatch==='paused';
          const step=action('单步派发',()=>operate(()=>runAction('advance',{single:true})),true);
          step.disabled=!!state.pending || paused || !Object.values(state.run.tasks).some(task=>task.state==='ready');
          sidebarBody.append(line(step,action(paused?'恢复单步派发':'暂停派发',
            ()=>operate(()=>runAction(paused?'resume':'pause')))));
        }
        const review=Object.values(state.run.tasks).find(task=>task.state==='review');
        if(review)sidebarBody.append(action('查看待审核产物 '+review.task_id,()=>inspect(review.task_id)));
      }
      return;
    }
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
      const displayed = state.runPlan || getPlan();
      if(displayed)sidebarBody.append(text('计划 v'+displayed.plan.version+' · '+displayed.plan.tasks.length+' 项任务 · '+
        (displayed.plan.base_commit?'真实代码执行':'模拟执行')));
      if(state.target?.path)sidebarBody.append(text('保存位置：'+state.target.path), action('查看或导出产物',showTarget));
      if(state.run) {
        sidebarBody.append(text(runStatus().title));
        const advance=action('推进一轮派发',()=>operate(()=>runAction('advance')),true);
        advance.disabled=state.pending || state.run.state!=='active' || state.run.mode!=='manual' || state.run.dispatch!=='active';
        const pause=action(state.run.dispatch==='active'?'暂停后续派发':'继续派发',
          ()=>operate(()=>runAction(state.run.dispatch==='active'?'pause':'resume')));
        pause.disabled=state.pending || state.run.state!=='active'; sidebarBody.append(line(advance,pause));
        const stop = action('结束本次运行', () => {
          if (confirm('结束本次运行并保留已生成的产物？之后可以启动新一轮。')) operate(()=>runAction('stop'));
        });
        stop.disabled = state.pending || state.run.state !== 'active'
          || Object.values(state.run.tasks).some(task=>task.state==='running');
        sidebarBody.append(stop);
        if (state.run.state === 'active') {
          if (state.runQuestions.some(item => item.default)) {
            sidebarBody.append(action('主理人处理常规卡点', ()=>operate(()=>runAction('steward-defaults')), true));
          }
          const first = state.runQuestions.find(item=>!item.default) || state.runQuestions[0];
          if (first) {
            const pane = card('只需先处理这一项');
            renderQuestion(pane, first, true); sidebarBody.append(pane);
            if(state.runQuestions.length>1) sidebarBody.append(text('另有 '+(state.runQuestions.length-1)+' 项，回答后依次显示。'));
          }
          const failed = Object.values(state.run.tasks).find(task=>task.state==='failed');
          if (failed) sidebarBody.append(text(errorMessage(failed.attempts.at(-1)?.error)),action('查看失败任务 '+failed.task_id,()=>inspect(failed.task_id)));
        }
        const review=Object.values(state.run.tasks).find(task=>task.state==='review');
        if(review && state.run.state === 'active')sidebarBody.append(action('审阅并放行 '+review.task_id,()=>inspect(review.task_id),true));
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
  function renderModelSettings(wrap) {
    const box = card('模型 API 配置');
    const settings = state.config;
    box.append(text('接入兼容 OpenAI Chat Completions 的服务。赛马与评审使用指定模型，开发执行按角色选择高低配模型。'));
    if (!settings) {
      box.append(text('正在读取模型设置。'));
      wrap.append(box);
      return;
    }
    const base = field(box, 'API 基础地址', settings.base_url);
    base.type = 'url';
    base.placeholder = 'https://api.openai.com/v1';
    const discussion = field(box, '赛马与评审模型', settings.discussion_model);
    discussion.placeholder = '填写服务商提供的模型 ID';
    const key = field(box, 'API Key', '');
    key.type = 'password';
    key.autocomplete = 'off';
    key.placeholder = settings.key_set ? '同一地址留空可沿用当前密钥' : '本地服务无需密钥时可留空';
    const workerModels = el('div', null, 'grid grid-cols-1 sm:grid-cols-2 gap-3');
    const low = field(workerModels, '低成本执行模型', settings.low_cost_model);
    const high = field(workerModels, '高能力执行模型', settings.high_capability_model);
    box.append(workerModels);
    box.append(text('密钥仅保存在当前服务进程中，重启后恢复启动配置。浏览器设置的三个模型共用同一地址和密钥。'));
    box.append(text(settings.real_available
      ? '已配置目标 Git 仓库。绑定仓库基线的计划可运行开发执行模型。'
      : '开发执行当前为模拟。启用真实代码执行需配置目标 Git 仓库和执行模式。'));
    const status = text('');
    status.setAttribute('role', 'status');
    const submit = async name => {
      const current = {action: name, base_url: base.value, discussion_model: discussion.value,
        api_key: key.value, low_cost_model: low.value, high_capability_model: high.value};
      const controls = [base, discussion, key, low, high, test, save, restore];
      controls.forEach(control => { control.disabled = true; });
      status.textContent = name === 'test' ? '正在发送连接测试…' : '正在应用模型设置…';
      try {
        const result = await post('/workspace/model-settings', name === 'restore' ? {action: name} : current);
        if (name === 'test') {
          status.textContent = '赛马与评审模型连接测试通过。';
        } else {
          Object.assign(state.config, result);
          key.value = '';
          showModelMode();
          if (state.module === 'model-router') refresh();
          toast(name === 'restore' ? '已恢复启动配置。' : '模型设置已保存。');
        }
      } catch (error) {
        status.textContent = error.message;
      } finally {
        controls.forEach(control => { control.disabled = false; });
      }
    };
    const test = action('测试赛马与评审连接', () => submit('test'));
    const save = action('保存模型设置', () => submit('save'), true);
    const restore = action('恢复启动配置', () => submit('restore'));
    box.append(line(save, test, restore), status);
    wrap.append(box);
  }

  const taskLabels = {done:'已完成', running:'正在执行', waiting:'等前置任务', ready:'等派发',
    input_required:'等确认', blocked:'被上游阻塞', failed:'失败', review:'等审阅'};
  const eventLabels = {'run.started':'已启动', 'run.paused':'已暂停派发', 'run.resumed':'已恢复派发',
    'run.stopped':'已结束本次运行', 'run.completed':'已完成',
    'task.dispatched':'开始执行', 'task.failed':'执行失败', 'task.retry_requested':'已请求重试',
    'artifact.submitted':'生成产物', 'artifact.approved':'产物通过', 'artifact.rejected':'产物退回',
    'task.input_supplied':'已记录决定', 'task.input_required':'等待业务决定'};

  function errorMessage(error) {
    if (!error) return '任务失败，打开详情查看并重试。';
    if (typeof error !== 'string') return error.message || error.code || '任务失败';
    try { return errorMessage(JSON.parse(error)); } catch { return error; }
  }

  function elapsed(seconds) {
    const n = Math.max(0, Math.floor(seconds || 0));
    return n < 60 ? n+' 秒' : Math.floor(n/60)+' 分 '+n%60+' 秒';
  }

  function runStatus() {
    const run = state.run;
    if (!run) return {title:getPlan()?'任务计划已保存 · 尚未启动开发':'尚未启动开发', detail:''};
    const tasks = Object.values(run.tasks);
    const count = key => tasks.filter(task => task.state === key).length;
    const running = tasks.filter(task => task.state==='running');
    const totals = ' · '+count('done')+'/'+tasks.length+' 项完成';
    if(run.state==='stopped') return {title:run.id+' · 已结束'+totals,detail:'已生成的产物仍可查看和导出。'};
    if(run.state==='completed') return {title:run.id+' · 已完成'+totals,detail:'可以导出交付记录。'};
    if (running.length) {
      const task = running[0], activity = task.activity?.at(-1);
      const started = Date.parse(task.attempts.at(-1)?.started_at || '');
      const duration = Number.isFinite(started) ? ' · 当前任务已用 '+elapsed((Date.now()-started)/1000) : '';
      const left = (state.runPlan?.schedule || []).filter(row =>
        run.tasks[row.task_id]?.state!=='done' && Number.isFinite(row.early_start_seconds) && Number.isFinite(row.early_finish_seconds));
      const estimate = left.length
        ? ' · 计划剩余约 '+elapsed(Math.max(...left.map(row=>row.early_finish_seconds))-Math.min(...left.map(row=>row.early_start_seconds)))
        : '';
      return {title:run.id+' · '+(run.dispatch==='paused'?'暂停派发，'+running.length+' 项正在收尾':running.length+' 项正在执行')+totals,
        detail:task.task_id+' · '+(activity?.label || 'Worker 处理中')+duration+estimate+'。实际时间受模型和审核影响。',
        steps:task.activity || []};
    }
    if(run.dispatch==='paused') return {title:run.id+' · 已暂停，当前没有任务在跑'+totals,
      detail:'继续派发后才会推进。还在等确认的事项不会自动放行。'};
    if(count('failed')) return {title:run.id+' · '+count('failed')+' 项失败，当前没有任务在跑'+totals,
      detail:errorMessage(tasks.find(t=>t.state==='failed').attempts.at(-1)?.error)};
    if(count('ready')) return {title:run.id+' · '+count('ready')+' 项待派发'+totals,
      detail:run.mode==='manual'?'点击右侧「推进一轮派发」。':'主理人会自动派发已满足前置条件的任务。'};
    if(count('input_required')) return {title:run.id+' · 等待业务确认，当前没有任务在跑'+totals,
      detail:'先看右侧问题。等待确认期间无法预计完成时间。'};
    if(count('review')) return {title:run.id+' · '+count('review')+' 项待审核，当前没有任务在跑'+totals,
      detail:'核对右侧产物后放行。等待审核期间无法预计完成时间。'};
    return {title:run.id+' · 等待前置条件'+totals,detail:'当前没有任务在跑。查看依赖图找阻塞点。'};
  }

  const statusTitle = el('strong', '', 'text-slate-900');
  statusTitle.setAttribute('role','status');
  const statusDetail = text('');
  const statusHistory = el('details', null, 'text-slate-600');
  statusHistory.append(el('summary','最近步骤与事件','cursor-pointer'));
  const statusSteps = el('ul',null,'ml-4 list-disc text-slate-600');
  statusHistory.append(statusSteps);
  statusBar.append(statusTitle,statusDetail,statusHistory);
  let lastRows = '';
  function renderStatus() {
    targetButton.textContent = state.target?.path ? '产物位置已选' : '产物位置';
    targetButton.title = state.target?.path || '选择项目的本地保存位置';
    if (!state.project) targetButton.disabled = true;
    else targetButton.disabled = false;
    const lastJob = state.activity;
    const job = lastJob?.running || (lastJob && state.module===lastJob.module && Date.now()-lastJob.ended<12000)
      ? lastJob : null;
    const current = job?.steps?.at(-1);
    let status = runStatus();
    const raceActive = state.module==='intake-race' && !linkedJob()
      ? state.groups.filter(group=>group.status==='running') : [];
    if(raceActive.length>1){
      status={title:raceActive.map(group=>group.id+' 组').join('、')+'并发出案',
        detail:'每组读同一份需求快照；已完成的小组不会重跑。',steps:job?.steps};
    } else if (job) {
      const duration = (Date.now()-job.started)/1000;
      status = {title:(job.running?'正在处理 · ':job.failed?'执行失败 · ':'已完成 · ')+job.label,
        detail:job.failed || (job.running ? (current ? current.label+' · ' : '')+'已用 '+elapsed(duration)
           +(job.expected ? ' · '+Math.min(job.steps.length,job.expected)+'/'+job.expected+' 步已开始' : '')
          +(current?.timeout_seconds ? ' · 单步请求超时设置 '+current.timeout_seconds+' 秒' : '')+'；还需多久取决于实际调用。'
          : '整体用时 '+elapsed(job.duration)),
        steps:job.steps};
    } else if (state.module === 'intake-race' && state.groups.some(g=>g.status==='failed')) {
      const failures = state.groups.filter(g=>g.status==='failed').map(g=>g.id+' 组');
      status = {title:failures.join('、')+'出案失败',detail:'已提交的组仍保留，可单独重试失败组。'};
    }
    const title = status.title + (state.statusError ? ' · 状态未同步' : '');
    if (statusTitle.textContent !== title) statusTitle.textContent=title;
    statusDetail.textContent=state.statusError || status.detail;
    const rows = [...(status.steps || []).map(step=>step.label),
      ...state.eventTrail.slice(-5).map(event => (event.task_id ? event.task_id+' · ' : '')+
        (eventLabels[event.type] || event.type))];
    const signature = JSON.stringify(rows);
    if(lastRows!==signature) {
      statusSteps.replaceChildren(...rows.map(row=>el('li',row)));
      lastRows=signature;
    }
    statusHistory.hidden=!rows.length;
  }

  async function localStep(label, callback, withProgress=false, expected=0) {
    const job = {label,id:crypto.randomUUID(),module:state.module,started:Date.now(),running:true,steps:[],expected};
    state.activity=job; renderStatus();
    let fetching=false;
    const poll = async () => {
      if(fetching || !job.running) return;
      fetching=true;
      try {job.steps=(await get('/workspace/progress/'+job.id)).steps; renderStatus();}
      catch { /* state remains visible through the local clock */ }
      finally {fetching=false;}
    };
    const timer = withProgress ? setInterval(poll,800) : null;
    try {
      const result = await callback(job.id);
      if(withProgress) await poll();
      return result;
    } catch(error) {
      job.failed=error.message;
      if(withProgress) await poll();
      throw error;
    } finally {
      if(timer)clearInterval(timer);
      job.running=false; job.ended=Date.now(); job.duration=(job.ended-job.started)/1000;
      renderStatus();
    }
  }

  function tracked(label,path,body) {
    const expected = path==='/preview/group' ? 4 :
      path.endsWith('/evaluate') ? state.project?.drafts.length : path.endsWith('/propose') ? 1 : 0;
    return localStep(label, id=>post(path,{...body,progress_id:id}), true, expected);
  }

  async function readRunDetails(run) {
    const projectId = state.project?.id;
    if (!projectId) return;
    const path = '/workspace/projects/'+projectId+'/runs/'+run.id;
    const [questions, events] = await Promise.allSettled([
      run.state==='active' ? get(path+'/questions') : Promise.resolve({items:[]}),
      get('/api/v2/projects/'+projectId+'/runs/'+run.id+'/events?after='+Math.max(0,run.latest_event_seq-7)+'&limit=7'),
    ]);
    if (state.project?.id!==projectId) return;
    state.runQuestions = questions.status==='fulfilled' ? questions.value.items : [];
    state.eventTrail = events.status==='fulfilled' ? events.value.items : [];
  }

  function renderQuestion(parent,item,sidebar=false) {
    parent.append(text(item.question,'text-xs font-semibold text-slate-900'));
    if(item.detail && item.question.replace(/？$/,'')!==item.detail.replace(/？$/,'')) {
      const detail = el('details',null,'text-xs text-slate-600');
      detail.append(el('summary','查看原始问题'),text(item.detail)); parent.append(detail);
    }
    if(item.note) parent.append(text('主理人处理：'+item.note));
    if(item.default){
      parent.append(text('主理人建议：'+item.default),
        action('采用主理人建议',()=>operate(()=>runAction('steward-apply',{item_id:item.id})),true));
      return;
    }
    const submit = answer => operate(async()=> {
      if(!answer.trim()) throw new Error('请先写一句答复。');
      await runAction('steward-answer',{item_id:item.id,answer});
    });
    for(const option of item.options.slice(0,6))
      parent.append(action(option.label,()=>submit(option.value),true));
    const input = field(parent,'一句话答复','');
    input.maxLength=1000;
    parent.append(action('提交答复',()=>submit(input.value),!item.options.length));
    if(sidebar) parent.append(text('一次答复会应用到这项决定涉及的 '+item.tasks.length+' 个任务。'));
  }

  function dialog(title) {
    const modal = el('dialog',null,'rounded-xl border border-slate-200 p-5 mx-auto my-auto w-[min(92vw,560px)] bg-white text-slate-900 shadow-lg');
    modal.style.cssText = 'position:fixed;inset:0;max-height:82vh;overflow:auto;margin:auto;';
    const body = el('div',null,'space-y-4');
    body.append(el('h2', title,'text-base font-bold text-slate-900'));
    modal.setAttribute('aria-label',title);
    modal.append(body);
    modal.addEventListener('close',()=>modal.remove());
    document.body.append(modal); modal.showModal();
    return {body,close:action('关闭',()=>modal.close())};
  }

  function showTarget() {
    if (!state.project) {toast('先完成赛马交接，再为这个项目选位置。'); return;}
    const modal = dialog('产物保存位置'), body = modal.body;
    const overview=text('方案、任务和运行记录会保存到本机数据库。下面的位置用来导出成文件，也可绑定真实代码仓库。');
    const path = field(body,'本地文件夹完整路径',state.target?.path || '');
    path.placeholder='例如 D:\\Projects\\XiaoLanShu';
    body.insertBefore(overview, path.parentElement);
    const toggle = el('label',null,'flex gap-2 items-center text-xs text-slate-700');
    const repoFlag=el('input'); repoFlag.type='checkbox';
    repoFlag.checked=!!state.target?.repo && state.target.path===state.target.repo;
    toggle.append(repoFlag,el('span','绑定为真实代码仓库（空目录会初始化 Git）')); body.append(toggle);
    const status = text(state.target?.repo ? '真实代码仓库：'+state.target.repo : '尚未绑定真实代码仓库。');
    status.setAttribute('role','status'); body.append(status);
    const projectId=state.project.id;
    const base='/workspace/projects/'+projectId;
    async function request(fn){
      save.disabled=exportFiles.disabled=true;
      try { await fn(); }
      catch(error){status.textContent=error.message;}
      finally {save.disabled=false; exportFiles.disabled=!state.target?.path;}
    }
    const save=action('保存位置',()=>request(async()=>{
      const result = await write(base+'/target',{path:path.value,real:repoFlag.checked});
      if(state.project?.id!==projectId)return;
      state.target=result.target;
      if(result.planning)accept(result.planning);
      status.textContent='已保存：'+result.target.path+(result.target.repo?' · 真实代码仓库 '+result.target.repo:'');
      if(result.run?.state==='active' && repoFlag.checked && result.planning?.plan.version!==result.run.plan_ref.version)
        status.textContent+='。现有运行仍使用旧计划；结束旧运行后再启动新计划。';
      refresh();
    }),true);
    const exportFiles=action('导出项目文件',()=>request(async()=>{
      const result=await write(base+'/export',{});
      status.textContent='已导出 '+result.files.length+' 个文件到 '+result.directory;
    }));
    exportFiles.disabled=!state.target?.path;
    body.append(line(save,exportFiles,modal.close));
  }

  function showHelp() {
    const modal = dialog('工作台使用答疑 AI'), body=modal.body;
    const current = runStatus();
    body.append(text(current.title+'。'+current.detail));
    const input=field(body,'你想了解什么？',''); input.maxLength=500;
    body.append(text('使用说明未覆盖的问题会交给当前配置的模型。'));
    const answer=text('可以问：怎么让 Agent 真的写代码？产物存在哪里？为什么显示等待确认？');
    answer.setAttribute('role','status');
    async function ask(question) {
      if(!question.trim())return;
      input.value=question; questionButton.disabled=true; answer.textContent='正在查找使用说明…';
      try {
        const result=await post('/workspace/help',{question,project_id:state.project?.id || null});
        answer.textContent=result.answer+(result.source==='model'?'（模型根据工作台说明回答）':'');
      } catch(error){answer.textContent=error.message;}
      finally {questionButton.disabled=false;}
    }
    const questionButton=action('提问',()=>ask(input.value),true);
    input.addEventListener('keydown',event=>{if(event.key==='Enter')ask(input.value);});
    body.append(line(...['产物存在哪里？','怎么开始真实执行？','任务为什么没动？'].map(question=>action(question,()=>ask(question)))));
    body.append(answer,line(questionButton,modal.close));
    input.focus();
  }

  function render(module) {
    state.module=module; renderStatus(); updateStageBadges();
    const job=linkedJob();
    if(job?.project_id)syncLinkedProject(job);
    const conversation=module==='steward' && steward;
    statusBar.hidden=!!conversation;
    targetButton.hidden=!!conversation;
    if(conversation){
      q('#btn-fit-graph').style.display='none';
      q('#right-steward-sidebar').style.display='none';
      q('#btn-toggle-right-sidebar').style.display='none';
      q('#workbench-split-view').classList.add('hidden');
      dedicated.classList.remove('hidden');
      if(!dedicated.querySelector('[data-steward-root]')){
        dedicated.replaceChildren();
        const wrap=el('div');wrap.setAttribute('data-steward-root','');
        dedicated.append(wrap);
        steward.show(wrap);
      }else steward.show(dedicated.querySelector('[data-steward-root]'));
      return true;
    }
    steward?.hide();
    q('#btn-fit-graph').style.display=module==='workbench'?'':'none';
    q('#progress-pct').style.display = state.run ? '' : 'none';
    q('#progress-bar').parentElement.style.display = state.run ? '' : 'none';
    q('#btn-toggle-right-sidebar').style.display='';q('#right-steward-sidebar').style.display='';
    document.querySelectorAll('.ws-tab').forEach(tab=>{tab.style.display='';});
    if(linkedStageWaiting(module)){
      q('#workbench-split-view').classList.add('hidden');dedicated.classList.remove('hidden');dedicated.replaceChildren();
      const wrap=el('div',null,'max-w-5xl mx-auto p-5');dedicated.append(wrap);
      const pending=card(q('#page-title').textContent);
      const failed=!!job.project_id && state.syncError===jobKey(job);
      pending.append(text(failed?'暂时读不到当前项目。':job.project_id && state.project?.id!==job.project_id?
        '正在读取当前对话的项目。':'这个阶段的结果还没有生成，生成后这里会自动更新。'),...stageActions(module));
      if(failed)pending.append(action('重新读取项目',clearStageError));
      wrap.append(pending);
      sidebarBody.replaceChildren(...stageActions(module));
      return true;
    }
    if(module==='task-planner'||module==='workbench')return false;
    q('#workbench-split-view').classList.add('hidden'); dedicated.classList.remove('hidden'); dedicated.replaceChildren();
    const wrap=el('div',null,'max-w-5xl mx-auto space-y-5 text-xs'); dedicated.append(wrap);
    if(state.error)wrap.append(text(state.error,'p-3 rounded-lg bg-red-50 text-red-700 text-sm'));
    if(module==='intake-race' && job){
      const box=card('需求快照与赛马');
      box.append(...stageActions(module));
      box.append(el('pre',job.markdown,'whitespace-pre-wrap text-sm bg-slate-50 p-3 rounded'));
      if(job.groups.length){
        box.append(text('主理人安排 '+job.groups.length+' 组，'+job.groups.filter(g=>g.finished).length+' 组已提交。'));
        for(const g of job.groups.filter(g=>g.status==='running' || g.status==='failed'))box.append(text(g.step));
        const routes=el('details');
        routes.append(el('summary','查看分组和提示词','cursor-pointer font-semibold'));
        for(const group of job.groups) routes.append(text(group.id+' 组 · '+group.focus),text(group.prompt));
        box.append(routes);
      }
      box.append(text('需求快照已经绑定本轮。修改需求或分组时，可回对话或在主理人高级设置中发起下一轮。'));
      wrap.append(box);
    }else if(module==='intake-race')renderIntake(wrap);
    else if(module==='review-synthesis')renderReview(wrap);
    else if(module==='exp-memory')renderMemory(wrap);
    else if(module==='model-router')renderModelSettings(wrap);
    else if(module==='code-review') {
      const review = Object.values(state.run?.tasks || {}).find(task => task.state === 'review');
      const box = card('Agent 代码审查');
      if (review) box.append(action('查看待审产物 ' + review.task_id, () => inspect(review.task_id), true));
      else box.append(text(state.run ? '当前没有待审任务。' : '启动开发后可查看待审产物。'));
      if (state.run) box.append(action('查看运行与审查记录', () => open('workbench')));
      wrap.append(box);
    }
    else {
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
      await updateProjects();
      if(state.projects.length) {
        await load(state.projects[0].id);
        state.module=steward?'steward':state.run?'workbench':getPlan()?'task-planner':'review-synthesis';
      }refresh();
    }catch(error){state.error=error.message;refresh();}
  }
  initialize();
  setInterval(async()=>{
    renderStatus();
    if(polling || !state.run || state.run.state!=='active' || state.module!=='workbench' || state.pending)return;
    polling=true;
    try {
      const current = state.run, currentEpoch = epoch;
      const run=await get(projectURL('/runs/'+current.id));
      if(epoch !== currentEpoch || state.run !== current) return;
      const changed = run.revision !== current.revision;
      if (changed) await readRunDetails(run);
      if(epoch !== currentEpoch || state.run !== current) return;
      state.run=run; state.statusError='';
      if(changed) refresh();
    }catch(error){state.statusError='状态同步中断，当前画面可能已过时。'; renderStatus();}
    finally{polling=false;}
  },1500);
  return {render,sidebar:renderSidebar,renderTab,planActions,inspect,
    executionSnapshot:()=>state.module === 'workbench' ? state.runPlan : null,
    reworkEdges:()=>state.run?.rework_edges || [],
    taskStatus:id=>({done:'completed',review:'in_progress',failed:'failed',running:'running'})[state.run?.tasks[id]?.state] ||
      (state.run?'pending':'planned')};
};
