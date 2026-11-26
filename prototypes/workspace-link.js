/* Bind the QLM interface to the combined workspace services. */
window.createWorkspaceLink = function (hooks) {
  const { q, el, open, action, toast, getPlan, accept, setSpecification,
    clearPlan, isDirty, renderGantt, stewardViews, download, showTask, editTask, showPlanVersion } = hooks;
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
  const originalVersion = q('#select-engine');
  const versionPicker = originalVersion.cloneNode(false);
  versionPicker.style.display = '';
  versionPicker.disabled = true;
  originalVersion.replaceWith(versionPicker);
  const state = {
    module: 'steward', config: null, project: null, run: null, runPlan: null, reviewTab: 'handoff',
    spec: null, dirty: false, selection: 'merge', inspection: null, pending: false, error: '',
    markdown: '# 设备预约系统\n\n- 用户可以查看设备并提交预约。\n- 管理员可以停用设备，停用后不能新增预约。\n- 用户可以在我的预约中查看状态。',
    version: 1, frozen: null, groups: [], extraPrompt: '', paused: false, queue: false,
    simulateFailure: false, selectedTask: null, edits: new Map(), feedback: '', feedbackGroup: '',
    target: null, runQuestions: [], activity: null, eventTrail: [], statusError: '',
    projects: [],
    followSteward: true, selectedJobId: null, syncedJob: '', syncInFlight: null, syncError: '',
  };
  let epoch = 0;
  let polling = false;
  const statusBar = el('div', null, 'p-3 bg-slate-50 border border-slate-200 rounded-xl text-xs');
  statusBar.id = 'workspace-live-status';
  const targetButton = action('产物位置', () => showTarget());
  const helpButton = action('使用答疑 AI', () => showHelp());
  const steward = window.createStewardLink?.({
    q,el,action,get,post,open,modelMode:()=>state.config?.discussion_mode,limits:()=>state.config?.limits,
    existingProject:()=>state.project, views:stewardViews,
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
  function appendWaitingControls(parent, module) {
    const job = linkedJob();
    const failed = job?.project_id && state.syncError === jobKey(job);
    parent.append(text(failed ? '暂时读不到当前项目。' :
      module !== 'review-synthesis' ? '任务计划生成后会显示在这里。' : '等待赛马草案交接。'));
    if (job) parent.append(...stageActions(module));
    if (failed) parent.append(action('重新读取项目', clearStageError));
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
    state.modelBudget = data.model_budget;
    state.planVersions = data.plan_versions || [];
    state.viewedPlan = null;
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
    picker.value = projectId;
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
        epoch++; state.project = state.viewedPlan = state.run = state.runPlan = state.spec = state.frozen = null;
        state.version = 1; state.target = null;
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
  function intakePage() {
    const job = linkedJob();
    if (job) {
      const project = state.project?.id === job.project_id ? state.project : null;
      const drafts = new Map((project?.drafts || []).map(draft => [draft.id, draft]));
      return {
        busy: true, managed: true, complete: job.groups.length >= 2 && job.groups.every(group => group.finished),
        locked: true, markdown: job.markdown, version: project?.requirement.version || job.phase,
        dispatchPaused: job.status === 'paused', simulateBFailure: false,
        items: (project?.requirement.items || []).map(item => ({
          id: item.id, text: item.text, line: item.source.start_line,
        })),
        groups: job.groups.map(group => ({
          id: group.id, prompt: group.prompt, status: group.status, defaultGroup: true,
          draftTitle: drafts.get(group.id)?.title || group.id + ' 组方案草案',
          summary: drafts.get(group.id)?.summary || group.step,
        })),
        appendControls: parent => parent.append(...stageActions('intake-race')),
      };
    }
    const busy = state.pending || state.groups.some(group => group.status === 'running');
    const complete = state.groups.length >= 2 && state.groups.every(group => group.status === 'done');
    return {
      busy, complete, locked: Boolean(state.frozen), markdown: state.markdown,
      version: state.version, dispatchPaused: state.paused, simulateBFailure: state.simulateFailure,
      items: (state.frozen?.items || []).map(item => ({
        id: item.id, text: item.text, line: item.source.start_line,
      })),
      groups: state.groups.map(group => ({
        ...group, defaultGroup: ['A', 'B'].includes(group.id),
        draftTitle: complete ? group.result.draft.title : group.id + ' 组方案草案',
        summary: complete ? group.result.draft.summary : group.error ||
          (group.status === 'done' ? '本组已提交，等待其他组完成。' : ''),
      })),
      input: value => { state.markdown = value; },
      freeze: () => operate(async () => {
        state.frozen = await tracked('检查需求正文并锁定快照', '/preview/snapshot', {
          markdown: state.markdown, version: state.version,
        });
        q('#sidebar-req-ver').textContent = state.frozen.id + '@v' + state.version;
      }),
      unlock() {
        epoch++; state.version++; state.frozen = null;
        for (const group of state.groups) Object.assign(group, {status:'idle', result:null, error:''});
        refresh();
      },
      add() {
        const id = [...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'].find(key => !state.groups.some(group => group.id === key));
        if (!id) return;
        state.groups.push({id, prompt:state.extraPrompt, status:'idle'});
        refresh();
      },
      remove(id) { state.groups = state.groups.filter(group => group.id !== id); refresh(); },
      prompt(id, value) {
        const group = state.groups.find(row => row.id === id);
        Object.assign(group, {prompt:value, status:'idle', result:null, error:''});
      },
      fail: value => { state.simulateFailure = value; },
      race, retry: id => runGroup(state.groups.find(group => group.id === id)),
      handoff: () => operate(handoff),
    };
  }

  function reviewPage() {
    const project = state.project;
    const spec = state.spec;
    const job = linkedJob();
    if (!project || linkedStageWaiting('review-synthesis')) {
      return {
        waiting:true, busy:true, managed:!!job, subStep:state.reviewTab,
        tab(key) { state.reviewTab = key; refresh(); },
        appendControls:parent => appendWaitingControls(parent, 'review-synthesis'),
      };
    }
    const editable = !state.pending && !!spec && spec.status !== 'confirmed' && permission('review-synthesis');
    const evaluations = Object.fromEntries(project.evaluations.map(evaluation => [
      evaluation.draft_id, {
        ...Object.fromEntries(evaluation.criteria.map(item => [item.criterion, item.score])),
        claims: [...new Set(evaluation.criteria.flatMap(item => item.claim_ids))].join('、'),
        reason: evaluation.criteria.map(item => item.reason).join('；'),
      },
    ]));
    return {
      editable, busy: state.pending, error: state.error, managed: !!job, hasSpec: !!spec,
      groupIds: Object.keys(evaluations), evaluations, subStep: state.reviewTab,
      currentVersion: spec?.version || 0, confirmed: spec?.status === 'confirmed',
      specMarkdown: spec?.markdown || '', notes: spec?.notes_markdown || '',
      requirement: project.requirement, drafts: project.drafts,
      openItems: spec?.open_items.map(item => ({
        ...item, affectedReq: item.affected_requirement_ids.join('、'), answer: item.resolution || '',
      })) || [],
      decisions: spec?.decisions || [],
      historyVersions: (project.versions || []).slice().reverse().map(version => ({
        ver: version.version, time: '', note: version.status === 'confirmed' ? '已确认定稿' : '草稿',
      })),
      tab(key) { state.reviewTab = key; refresh(); },
      change: dirty,
      notesInput(value) { spec.notes_markdown = value; dirty(); },
      evaluate: () => operate(() => reviewAction('evaluate')),
      propose: () => operate(async () => {
        if (!project.evaluations.length) await reviewAction('evaluate');
        await reviewAction('propose', {selection:'merge', expected_version:state.project.latest_version});
      }),
      example: () => operate(async () => {
        if (state.dirty || isDirty()) throw new Error('请先保存当前编辑。');
        const payload = await get('/preview/examples/conflict');
        const next = await post('/preview/projects', payload);
        await load(next.id);
        state.reviewTab = 'handoff';
      }),
      resolve(id, answer) {
        const item = spec.open_items.find(row => row.id === id);
        item.resolution = answer;
        const decisionId = item.decision_id || 'DEC-HUMAN-' + id;
        item.decision_id = decisionId;
        const decision = {id:decisionId, scope:item.question, choice:answer, reason:'负责人确认待决问题',
          requirement_ids:[...item.affected_requirement_ids], draft_ids:project.drafts.map(draft => draft.id)};
        const prior = spec.decisions.find(row => row.id === decisionId);
        if (prior) Object.assign(prior, decision);
        else spec.decisions.push(decision);
        dirty(); refresh();
      },
      addDecision() {
        spec.decisions.push({id:'DEC-HUMAN-' + crypto.randomUUID().slice(0,8),scope:'',choice:'',reason:'',
          requirement_ids:[project.requirement.items[0].id],draft_ids:project.drafts.map(draft=>draft.id)});
        dirty(); refresh();
      },
      save: () => operate(() => reviewAction('save', {
        expected_version:spec.version, markdown:spec.markdown, notes_markdown:spec.notes_markdown || '',
        decisions:spec.decisions, open_items:spec.open_items,
      })),
      confirm: () => operate(async () => {
        if (state.dirty) throw new Error('请先保存人工编辑。');
        if (spec.status !== 'confirmed') await reviewAction('confirm', {expected_version:spec.version});
        await window.openTaskPlanner({review_id:project.id,version:state.project.specification.version});
        state.module = 'task-planner';
      }),
      appendControls: parent => parent.append(...stageActions('review-synthesis')),
    };
  }

  function canRetrySession(id) {
    return !state.pending && state.run?.state === 'active' &&
      state.run.tasks[id]?.state === 'failed' && permission('workbench');
  }

  async function runAction(name, body = {}) {
    const previous = state.run;
    state.run = await write(projectURL('/runs/' + previous.id + '/actions/' + name),
      {...body,expected_revision:previous.revision});
    await readRunDetails(state.run);
  }
  function canEditNode() {
    if (state.pending || !getPlan()) return false;
    if (permission('task-planner')) return true;
    const job = linkedJob();
    return !!job?.run_id && job.project_id === state.project?.id && job.run_id === state.run?.id &&
      (state.run.dispatch === 'paused' || state.run.state !== 'active') &&
      ['failed', 'paused', 'cancelled', 'gate'].includes(job.status);
  }

  function checkNodeRerun() {
    if (state.run?.state === 'active') {
      throw new Error('当前运行仍在进行。请结束当前运行后再启动修改后的计划。');
    }
    const job = linkedJob();
    if (job && !['completed', 'cancelled'].includes(job.status)) {
      throw new Error('主理人正在管理本轮流程。请在开工确认点保存，再由主理人继续。');
    }
  }

  async function startNodeRerun(snapshot) {
    checkNodeRerun();
    try {
      state.run = await write(projectURL('/runs'), {
        plan_ref: {id: snapshot.plan.id, version: snapshot.plan.version},
        mode: 'auto', steward: true,
      });
    } catch (error) {
      throw new Error('新版计划已保存。启动失败：' + error.message);
    }
    state.runPlan = structuredClone(snapshot);
    try {
      await readRunDetails(state.run);
    } catch {
      state.statusError = '新一轮已启动，暂时无法读取运行详情。';
    }
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
  function renderDetails(container, {header = true} = {}) {
    const plan = state.runPlan?.plan || getPlan()?.plan;
    if (!plan) return;
    state.selectedTask ||= plan.tasks[0].id;
    const task = plan.tasks.find(task => task.id === state.selectedTask) || plan.tasks[0];
    const taskRun = state.run?.tasks[task.id];
    const canEdit=permission('workbench');
    const box = card(header ? task.id + ' · ' + task.title : '任务执行与产物审阅');
    if (header) {
    const technical = el('details', null, 'space-y-2 text-xs');
    technical.append(el('summary', '查看需求编号、验收条件和文件路径', 'cursor-pointer text-indigo-700'));
    technical.append(text('关联需求 ' + task.requirement_ids.join('、')),
      text('关联决策与待确认事项 ' + [...task.decision_ids, ...task.open_item_ids].join('、')),
      text('验收条件 ' + task.completion_condition), text('计划改动的文件 ' + task.write_paths.join('、')));
    box.append(technical);
    const select = el('select', null, inputClass); select.setAttribute('aria-label','选择执行任务');
    for (const item of plan.tasks) { const option = el('option',item.id + ' · ' + item.title); option.value=item.id; select.append(option); }
    select.value=task.id; select.onchange=()=>inspect(select.value); box.append(select);
    if (getPlan()?.plan.tasks.some(item => item.id === task.id)) {
      box.append(action('修改节点 / 输入要求', () => editTask(task.id)));
      if (state.run?.state === 'active') {
        box.append(text('当前运行使用启动时的计划。保存修改会生成新版计划，供后续运行使用。'));
      }
    }
    }
    container.append(box);
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
      const artifactStatus = {draft:'待审查', approved:'已批准', rejected:'已退回'};
      const title = artifact.kind === 'text' ? '文本产物' : '代码提交';
      entry.append(el('strong', title + ' · 第 ' + artifact.version + ' 版 · ' +
        (artifactStatus[artifact.status] || '状态待同步'), 'text-xs text-indigo-700'));
      const raw = el('details', null, 'space-y-2');
      raw.append(el('summary', '查看产物原文与技术细节', 'cursor-pointer text-indigo-700'));
      entry.append(raw);
      if(artifact.kind==='text') {
        const key=state.run.id+':'+artifact.id+':'+artifact.version;
        const original=state.edits.has(key)?state.edits.get(key):artifact.content;
        const input=field(raw,'产物正文 '+artifact.output_id,original,value=>{
          state.edits.set(key,value);revisions[artifact.output_id]={content:value};
        },true);
        input.rows=6; input.readOnly=artifact.status!=='draft' || !canEdit;
        if(state.edits.has(key))revisions[artifact.output_id]={content:original};
      } else {
        raw.append(el('code',artifact.commit_sha || '','mono block text-xs break-all'));
        const attempt = taskRun.attempts.find(row=>row.id===artifact.attempt_id) || taskRun.attempts.at(-1);
        if (attempt?.checkout_commit && state.target?.repo) {
          raw.append(text('目标仓库：'+state.target.repo),
            text('任务分支：attempt/'+state.run.id+'/'+attempt.id));
        } else if (!attempt?.checkout_commit) entry.append(text('模拟提交，不对应本地源码。'));
        raw.append(text('改动文件 '+artifact.changed_paths.join(', ')));
        if(canEdit && artifact.status==='draft' && (state.target?.repo || state.config?.real_available)) {
          field(raw,'人工修订提交 '+artifact.output_id,artifact.commit_sha,value=>{
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
    if (taskRun.attempts.length) {
      const attempts = el('details', null, 'space-y-2');
      attempts.append(el('summary', '查看执行记录（' + taskRun.attempts.length + ' 次）', 'cursor-pointer'));
      for (const attempt of taskRun.attempts) {
        attempts.append(text('尝试 ' + attempt.id + ' · ' + (attempt.provider || '待执行') +
          (attempt.model ? ' / ' + attempt.model : '') + ' · ' + attempt.state));
      }
      box.append(attempts);
    }
  }
  const taskDisplayStatus = {
    done:'completed', review:'in_progress', failed:'failed', running:'running',
    ready:'ready', waiting:'pending', blocked:'blocked', input_required:'input_required',
  };

  function reviewDashboard() {
    const plan = (state.runPlan || getPlan())?.plan;
    const run = state.run;
    const tasks = (plan?.tasks || []).map(task => {
      const record = run?.tasks[task.id];
      const artifacts = record?.artifacts || [];
      const latest = new Map(artifacts.map(artifact => [artifact.output_id, artifact]));
      const checks = [...latest.values()].map(artifact => ({name:artifact.output_id,
        detail:({approved:'产物已放行', rejected:'产物已退回', draft:'产物等待审查'})[artifact.status] || '等待更新',
        status:({approved:'passed', rejected:'failed'})[artifact.status] || 'pending'}));
      const findings = (run?.findings || []).filter(finding => finding.ref_id === task.id ||
        finding.affected_task_ids?.includes(task.id)).map(finding => ({
        id:finding.code, line:finding.ref_id || task.id, text:finding.summary,
        severity:finding.severity === 'error' ? 'blocker' : 'warning', resolved:false,
      }));
      const texts = artifacts.filter(artifact => artifact.kind === 'text' && typeof artifact.content === 'string');
      const current = texts.at(-1);
      const previous = current && texts.filter(artifact => artifact.output_id === current.output_id).at(-2);
      const commit = artifacts.filter(artifact => artifact.commit_sha).at(-1)?.commit_sha;
      const history = artifacts.filter(artifact => ['approved','rejected'].includes(artifact.status)).map(artifact => ({
        round:artifact.version, action:artifact.status === 'approved' ? '产物审查放行' : '产物退回',
        agent:'执行记录', commit:artifact.commit_sha || '—', time:'', note:artifact.output_id,
      })).reverse();
      return {id:task.id, name:task.title, owner:task.role, deps:task.depends_on,
        spec:[...task.requirement_ids, ...task.decision_ids].join(' · '),
        status:taskDisplayStatus[record?.state] || 'planned',
        isReviewNode:task.review_required || ['review','verification'].includes(task.phase) || record?.state === 'review' || !!commit || checks.some(check => check.status === 'failed'),
        review:{reviewerAgent:task.role, checks, findings, history,
          verdict:record?.state === 'done' ? 'passed' : record?.state === 'failed' ? 'needs_fix' : 'pending',
          round:record?.attempts.length || 0, commitSha:commit || '—',
          excerptRange:current?.output_id || '暂无文本差异', origCode:previous?.content,
          fixedCode:previous ? current.content : undefined,
          patchOutputs:[...new Set(artifacts.filter(artifact => artifact.commit_sha).map(artifact => artifact.output_id))],
          loadPatch:commit && state.project && run ? (output, against) => get('/workspace/projects/' +
            encodeURIComponent(state.project.id) + '/runs/' + encodeURIComponent(run.id) +
            '/tasks/' + encodeURIComponent(task.id) + '/patch?' + new URLSearchParams({output, against})) : null}};
    });
    return {tasks, canStep:canStep(), running:state.run?.mode === 'auto' && state.run.dispatch === 'active'};
  }

  function renderTab(tab, selection) {
    if (selection && (state.runPlan || getPlan())?.plan.tasks.some(t=>t.id===selection)) state.selectedTask = selection;
    if(tab==='graph')return false;
    const panels = {details:'#panel-details', monitor:'#panel-monitor',
      activity:'#panel-activity'};
    if (!panels[tab]) return false;
    const container = q(panels[tab]);
    container.replaceChildren();
    const wrap=el('div',null,'p-5 space-y-4 max-w-5xl mx-auto'); container.append(wrap);
    if (tab === 'details') renderDetails(wrap);
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
    sidebarBody.replaceChildren(el('strong','STAGE STEWARD · 主理控制','text-xs text-indigo-700'), statusBar, line(targetButton, helpButton));
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
    if (['intake-race', 'review-synthesis'].includes(module)) return;
    {
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
  let profiles;
  function loadProfiles() {
    const id = state.project?.id;
    if (profiles && profiles.id === id) return;
    const view = profiles = {id, agents: [], pending: !!id};
    if (!id) return;
    get(projectURL('/profiles')).then(data => { view.agents = data.items; })
      .catch(error => { view.error = error.message; })
      .finally(() => {
        view.pending = false;
        if (profiles === view && state.module === 'agents-profile') refresh();
      });
  }

  async function saveProfile(body) {
    const view = profiles;
    if (!view?.id) throw new Error('请先选择项目。');
    const saved = await write(projectURL('/profiles'), body);
    if (profiles !== view) return;
    if (!body.id) view.agents.push(saved);
    else view.agents.find(agent => agent.id === body.id)[body.field] = saved[body.field];
  }

  let memory;
  const evidenceLabels = {pending:'待验证', simulated:'模拟验证', walkthrough:'方案走查', pilot:'单测验证', production:'真实代码执行'};
  function applyMemory(view, data) {
    view.baseline = data.baseline;
    view.cards = data.items.map(record => {
      const body = record.body;
      return {id:record.id, version:record.version, domain:body.domain, phenomenon:body.problem || '',
        trigger:body.trigger, action:body.correction, counterExample:body.counter_example || '',
        aliases:(body.tags || []).join('、'), cause:body.cause || '', verification:body.pass_condition,
        evidence:body.evidence || '', pubStatus:record.visibility === 'published' ? '已发布' : '草稿',
        evidStatus:evidenceLabels[body.evidence_state] || '待验证', readOnly:record.project !== view.id,
        inBaseline:data.baseline?.some(ref => ref.id === record.id && ref.version === record.version)};
    });
  }

  function loadMemory() {
    const id = state.project?.id;
    if (memory && memory.id === id) return;
    const view = memory = {id, cards:[], pending:!!id};
    if (!id) return;
    get(projectURL('/memory')).then(data => applyMemory(view, data))
      .catch(error => { view.error = error.message; })
      .finally(() => {
        view.pending = false;
        if (memory === view && state.module === 'exp-memory') refresh();
      });
  }

  async function updateMemory(cards) {
    const view = memory;
    if (!view?.id || view.pending) throw new Error('请先选择项目并等待读取完成。');
    const url = projectURL('/memory');
    const toPublish = cards && cards.map(card => ({
      ref:card.local ? null : {id:card.id, version:card.version},
      content:{domain:card.domain, problem:card.phenomenon, trigger:card.trigger,
        correction:card.action, counter_example:card.counterExample, tags:split(card.aliases),
        cause:card.cause, pass_condition:card.verification, evidence:card.evidence,
        evidence_state:Object.keys(evidenceLabels).find(level => evidenceLabels[level] === card.evidStatus)},
    }));
    view.pending = true;
    try {
      await write(url + (toPublish ? '/publish-cards' : '/baseline'), toPublish ? {cards:toPublish} : {});
      const untouched = view.cards.filter(card => !cards?.includes(card));
      const edits = new Map(untouched.filter(card => !card.local).map(card => [card.id, card]));
      applyMemory(view, await get(url));
      view.cards = view.cards.map(card => {
        const edited = edits.get(card.id);
        return edited?.version === card.version ? {...edited, inBaseline:card.inBaseline} : card;
      });
      view.cards.unshift(...untouched.filter(card => card.local));
    } finally {
      view.pending = false;
    }
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
  versionPicker.onchange = () => {
    const value = versionPicker.value;
    if (!value) { state.viewedPlan = null; refresh(); return; }
    const [id, version] = value.split(':');
    if (!state.project) return;
    const project = state.project.id;
    operate(async () => {
      const view = await get('/api/v2/projects/' + project + '/plans/' + id + '/versions/' + version);
      if (state.project?.id !== project) return;
      state.viewedPlan = view;
      showPlanVersion();
    });
  };

  function renderStatus() {
    const plan = (state.runPlan || getPlan())?.plan;
    const versions = state.project ? state.planVersions || [] : [];
    const refs = new Map(versions.map(ref => [ref.id + ':' + ref.version, ref]));
    for (const extra of [plan, getPlan()?.plan]) {
      if (extra) refs.set(extra.id + ':' + extra.version, {id:extra.id, version:extra.version, spec_id:extra.spec_ref.id});
    }
    if (state.project) state.planVersions = [...refs.values()];
    const keyFor = ref => ref && ref.id + ':' + ref.version;
    const labelFor = ref => ref.spec_id + ' · ' + ref.id + ' v' + ref.version;
    const currentKey = keyFor(plan);
    const latestKey = keyFor(getPlan()?.plan);
    const labels = [['', plan ? labelFor(refs.get(currentKey)) + (state.runPlan ? ' · 运行版本' : ' · 当前计划') : '等待计划'],
      ...[...refs].filter(([key]) => key !== currentKey)
        .map(([key, ref]) => [key, labelFor(ref) + (key === latestKey ? ' · 最新计划' : ' · 历史快照')])];
    const versionSignature = JSON.stringify(labels);
    if (versionPicker.dataset.versions !== versionSignature) {
      versionPicker.dataset.versions = versionSignature;
      versionPicker.replaceChildren(...labels.map(([value, label]) => {
        const option = el('option', label); option.value = value; return option;
      }));
    }
    const viewed = state.viewedPlan?.plan;
    versionPicker.value = viewed ? viewed.id + ':' + viewed.version : '';
    versionPicker.disabled = state.pending || labels.length < 2;
    const badge = q('#nav-review-badge');
    const pendingReviews = Object.values(state.run?.tasks || {}).filter(task => task.state === 'review').length;
    badge.hidden = pendingReviews === 0;
    badge.textContent = pendingReviews ? pendingReviews + ' 待审查' : '';

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
    return {body, dismiss:()=>modal.close(), close:action('关闭',()=>modal.close())};
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

  function canPrepareWorktree(id) {
    return canStep() && !!state.target?.repo && !!state.runPlan?.plan.base_commit &&
      state.run.tasks[id]?.state === 'ready' &&
      state.runPlan.plan.tasks.some(task => task.id === id && task.outputs.some(output => output.kind === 'git_commit'));
  }

  function canStep() {
    return !state.pending && state.run?.state === 'active' && state.run.mode === 'manual' &&
      state.run.dispatch === 'active' && permission('workbench') &&
      Object.values(state.run.tasks).some(task => task.state === 'ready');
  }

  function updateWorkflowControls() {
    q('#btn-step-sim').disabled = !canStep();
    const job = linkedJob();
    const run = state.run;
    const autoActive = run?.state === 'active' && run.mode === 'auto';
    const running = job ? job.status === 'running' : autoActive && run.dispatch === 'active';
    const paused = job ? ['paused', 'gate'].includes(job.status) : autoActive && run.dispatch === 'paused';
    const loop = q('#btn-auto-loop');
    loop.disabled = !!state.pending || job?.status === 'pausing';
    loop.textContent = running ? '暂停持续开发-审查闭环'
      : paused ? '继续持续开发-审查闭环' : '↻ 启动持续开发-审查闭环';
  }

  function step() {
    if (canStep()) return operate(() => runAction('advance', {single: true}));
    toast('当前没有可单步派发的任务。');
  }

  function toggleLoop() {
    const job = linkedJob();
    if (job && ['running', 'paused', 'gate'].includes(job.status)) {
      return steward.control(job.status === 'running' ? 'pause' : 'continue');
    }
    if (state.run?.state === 'active' && state.run.mode === 'auto') {
      return operate(() => runAction(state.run.dispatch === 'paused' ? 'resume' : 'pause'));
    }
    const plan = getPlan();
    if (plan && !state.pending && !isDirty() && !job && state.run?.state !== 'active') {
      return operate(() => startNodeRerun(plan));
    }
    open(plan ? 'task-planner' : 'steward');
  }

  function render(module) {
    state.module = module;
    renderStatus();
    updateStageBadges();
    updateWorkflowControls();
    const job = linkedJob();
    if (job?.project_id) syncLinkedProject(job);
    if (module !== 'steward') steward?.hide();
    q('#btn-fit-graph').style.display = module === 'workbench' ? '' : 'none';
    if (module === 'agents-profile') loadProfiles();
    if (module === 'exp-memory') loadMemory();
    renderSidebar(module);
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
    updateWorkflowControls();
    if(polling || !state.run || state.run.state!=='active' || state.pending)return;
    polling=true;
    try {
      const current = state.run, currentEpoch = epoch;
      const run=await get(projectURL('/runs/'+current.id));
      if(epoch !== currentEpoch || state.run !== current) return;
      const changed = run.revision !== current.revision;
      if (changed) await readRunDetails(run);
      if(epoch !== currentEpoch || state.run !== current) return;
      state.run=run; state.statusError='';
      const editingForm = document.activeElement?.matches(
        '#panel-details input, #panel-details textarea, #panel-details select, #storage-after-seq');
      if (changed && !editingForm && ['workbench', 'code-review', 'autogen-resume', 'git-worktree', 'sqlite-store'].includes(state.module)) refresh();
      else renderStatus();
    }catch(error){state.statusError='状态同步中断，当前画面可能已过时。'; renderStatus();}
    finally{polling=false;}
  },1500);
  return {render,renderTab,planActions,inspect,step,toggleLoop,intakePage,reviewPage,
    appendLiveDetails(container, id) {
      state.selectedTask = id;
      renderDetails(container, {header: false});
    },
    nodeRunInfo(id) {
      const attempt = state.run?.tasks[id]?.attempts.at(-1);
      return {
        version: state.run?.state === 'active' ? state.run.plan_ref.version : null,
        worktree: attempt?.checkout_commit ? 'attempt/' + state.run.id + '/' + attempt.id : '',
      };
    },
    canEditPlan: () => permission('task-planner'), canEditNode, checkNodeRerun, startNodeRerun,
    historicalPlan: () => state.module === 'workbench' ? state.viewedPlan : null,
    executionSnapshot:()=>state.module === 'workbench' ? state.viewedPlan || state.runPlan : null,
    monitorState: () => ({snapshot: state.runPlan || getPlan(), run: state.run}),
    workbenchWaiting: () => !getPlan() || linkedStageWaiting('workbench'),
    plannerWaiting: () => !getPlan() || linkedStageWaiting('task-planner'),
    appendWaitingControls,
    stageSidebar(module) {
      const plan = (state.runPlan || getPlan())?.plan;
      const intake = intakePage();
      const project = state.project;
      return {
        title:project?.requirement.items[0]?.title || '当前项目', parallel:plan?.max_parallel,
        intake, feedback:state.feedback, group:state.feedbackGroup, selection:state.selection,
        setFeedback:value => { state.feedback = value; },
        setGroup:value => { state.feedbackGroup = value; },
        selectRoute:value => { state.selection = value; },
        canPauseRace:module === 'intake-race' && !intake.managed && !state.pending,
        toggleRace() {
          state.paused = !state.paused;
          refresh();
          if (!state.paused) race();
        },
        rerunGroup(id, note) {
          if (intake.managed || intake.busy || !intake.locked || state.paused) return;
          const group = state.groups.find(row => row.id === id);
          if (!group) return;
          group.prompt += '\n负责人补充指令：' + note;
          group.status = 'idle'; group.result = null; state.feedback = '';
          return runGroup(group);
        },
        drafts:project?.drafts || [],
        canGenerate:!!project?.evaluations.length && !state.pending && (!linkedJob() || permission('review-synthesis')),
        generate:() => operate(() => reviewAction('propose', {
          selection:state.selection, expected_version:project.latest_version,
        })),
        rework(note) {
          const group = state.groups[1] || state.groups[0];
          if (group && !linkedJob()) {
            group.prompt += '\n评审退回补充：' + note;
            group.status = 'idle'; group.result = null;
          }
          open('intake-race');
        },
      };
    },
    workbenchSidebar() {
      const plan = (state.runPlan || getPlan())?.plan;
      const run = state.run;
      return {title:state.project?.requirement.items[0]?.title || '当前项目', version:plan?.version,
        parallel:plan?.max_parallel, paused:run?.dispatch === 'paused',
        canPause:run?.state === 'active' && !state.pending && permission('workbench'),
        togglePause:() => operate(() => runAction(run.dispatch === 'paused' ? 'resume' : 'pause')),
        configure:() => open('task-planner')};
    },
    reviewDashboard, showSteward: container => steward?.show(container),
    memory: () => memory, updateMemory,
    profiles: () => profiles,
    saveProfile,
    modelRouting() {
      return { lowCostModel: state.config?.low_cost_model || '', highCapModel: state.config?.high_capability_model || '',
        budgetLimit: (state.project ? state.modelBudget : null) ?? state.config?.router?.max_cost_usd_per_attempt,
        canSaveBudget: !!state.project, maxParallel: getPlan()?.plan.max_parallel ?? 2 };
    },
    async saveModelBudget(value) {
      const project = state.project?.id;
      const result = await write(projectURL('/model-budget'), {model_budget:value});
      if (state.project?.id === project) state.modelBudget = result.model_budget;
      return result.model_budget;
    },
    showModelSettings() {
      const {body, close} = dialog('模型 API 配置');
      renderModelSettings(body);
      body.append(close);
    },
    async saveModelRouting(lowCostModel, highCapModel) {
      const settings = state.config;
      if (!settings?.base_url || !settings.discussion_model) throw new Error('请先配置 API 基础地址和赛马模型。');
      const result = await post('/workspace/model-settings', {action: 'save',
        base_url: settings.base_url, discussion_model: settings.discussion_model,
        low_cost_model: lowCostModel, high_capability_model: highCapModel});
      Object.assign(settings, result);
      showModelMode();
      return result;
    },
    taskStatusActions(id, {worktree = false} = {}) {
      const task = state.run?.tasks[id];
      const {body, close, dismiss} = dialog((worktree ? '纳入上游提交并派发 · ' : '切换任务状态 · ') + id);
      if (worktree) body.append(text('派发时，执行器会在独立工作区纳入已批准的上游提交，再执行当前任务。'));
      const label = task ? taskLabels[task.state] || task.state : '未启动';
      body.append(text('当前状态：' + label));
      const transition = async (name) => {
        const buttons = [...body.querySelectorAll('button')];
        buttons.forEach(button => { button.disabled = true; });
        try {
          await runAction(name, {task_id:id, single:true});
          dismiss();
          refresh();
          toast(name === 'retry' ? '任务已重新排队。' : '已派发当前任务。');
        } catch (error) {
          body.append(text(error.message, 'text-xs text-red-700'));
        } finally {
          buttons.forEach(button => { button.disabled = false; });
        }
      };
      if (task?.state === 'ready' && canStep() && (!worktree || canPrepareWorktree(id))) {
        body.append(action(worktree ? '纳入上游并派发' : '派发当前任务', () => transition('advance'), true));
      }
      if (canRetrySession(id)) body.append(action('重新排队', () => transition('retry'), true));
      body.append(line(action('查看任务详情', () => { dismiss(); inspect(id); }), close));
    },
    retrySession(id) {
      if (!canRetrySession(id)) return;
      return operate(() => runAction('retry', {task_id:id}));
    },
    storageState() {
      return {plan: (state.runPlan || getPlan())?.plan, run: state.run,
        displayStatus: id => taskDisplayStatus[state.run?.tasks[id]?.state] || 'planned',
        canRetrySession, canPrepareWorktree, worktreeInputs: id => get(projectURL('/runs/' + encodeURIComponent(state.run.id) +
          '/tasks/' + encodeURIComponent(id) + '/worktree-inputs')),
        title: state.project?.requirement.items[0]?.title || '当前项目'};
    },
    async storageEvents(after) {
      const project = state.project;
      const run = state.run;
      if (!project || !run) return [];
      const data = await get('/api/v2/projects/' + project.id + '/runs/' + run.id + '/events?after=' + after + '&limit=500');
      return describeEvents(data.items, (state.runPlan || getPlan())?.plan);
    },
    activityRows() {
      return describeEvents(state.eventTrail, (state.runPlan || getPlan())?.plan);
    },
    reworkEdges:()=>state.viewedPlan && state.module === 'workbench' ? [] : state.run?.rework_edges || [],
    taskStatus:id=>state.module === 'workbench' && state.viewedPlan ? 'planned' : taskDisplayStatus[state.run?.tasks[id]?.state] || 'planned'};

  function describeEvents(events, plan) {
    const labels = {
      'run.started':'启动开发', 'run.completed':'开发完成', 'run.stopped':'停止运行',
      'run.paused':'暂停派发', 'run.resumed':'继续派发', 'task.dispatched':'派发任务',
      'task.failed':'任务失败', 'task.retry_requested':'任务重新排队',
      'task.input_required':'等待补充信息', 'task.input_supplied':'已补充信息',
      'artifact.submitted':'提交产物', 'artifact.approved':'审查放行', 'artifact.rejected':'审查退回',
    };
    const names = new Map((plan?.tasks || []).map(task => [task.id, task.title]));
    return events.map(event => ({
      seq: event.seq, actor: labels[event.type] || '运行更新',
      time: event.created_at ? new Date(event.created_at).toLocaleTimeString() : '',
      text: [names.get(event.task_id) || event.task_id, event.payload?.note ||
        event.payload?.error?.message].filter(Boolean).join(' · '),
    }));
  }
};
