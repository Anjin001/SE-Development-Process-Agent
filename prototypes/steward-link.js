/* Primary workspace: one conversation, with inspectable execution milestones. */
window.createStewardLink = function ({q,el,action,get,post,open,modelMode,limits,existingProject,openProject,onJobChange,canContinue,views}) {
  const section = () => el('section',null,'bg-white border border-slate-200 rounded-xl p-5 shadow-2xs space-y-3');
  const small = message => el('p',message,'text-xs text-slate-600 leading-relaxed');
  const strong = message => el('strong',message,'text-sm text-slate-900');
  const line = (...items) => {const node=el('div',null,'flex flex-wrap items-center gap-2'); node.append(...items); return node;};
  const field = (label, initial, multiline=false) => {
    const wrap=el('label',label,'block text-xs text-slate-700 space-y-1');
    const input=el(multiline?'textarea':'input',null,'w-full bg-white border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-900');
    input.value=initial || ''; wrap.append(input); return [wrap,input];
  };
  const phases = [
    ['groups','分组'],['race','出案'],['evaluate','评审'],['merge','整合'],
    ['decisions','定规则'],['confirm','需求规格'],['plan','编排'],['start','开工'],['develop','开发'],['delivery','交付'],
  ];
  const statuses = {running:'运行中',pausing:'正在收尾',paused:'已暂停',gate:'等你选定的确认点',
                    failed:'执行遇到问题',completed:'已交付',cancelled:'已停止'};
  const gateLabels = {spec:'发布需求规格前',plan:'开始开发前',verification:'集成验收时',delivery:'交付前'};
  const safeRead = key=>{try {return sessionStorage.getItem(key);}catch{return null;}};
  const safeSave = (key,value)=>{try {sessionStorage.setItem(key,value);}catch{}};
  let selected=null, busy=false, syncing=false, visible=false, refreshFailed=false, history=[], shown=null;
  let picked = safeRead('se-agent-steward-job') || '', jobFingerprint='', canReplan=false;
  const cfg = {gates:[],group_count:0,parallel:0,race_parallel:3,prompts:[],mode:'auto',guidance:'',directory:''};
  const buttons=line();
  const newButton=action('新对话',()=> {
    selection++; selected=null; picked=''; safeSave('se-agent-steward-job',''); input.value=''; render();
  });
  const historyPicker=el('select',null,'text-xs border border-slate-200 bg-white rounded-md px-2 py-2 max-w-xs');
  historyPicker.setAttribute('aria-label','历史对话');
  historyPicker.onchange=()=> { if(historyPicker.value) select(historyPicker.value); };
  const adopt=action('接管当前项目',()=>send('接管当前项目',null,true));
  buttons.append(newButton,historyPicker,adopt);
  const progress=section();
  const log=el('details',null,'border border-slate-200 rounded-xl bg-white p-4 text-xs text-slate-600');
  const eventView=el('ul',null,'ml-4 mt-2 list-disc space-y-1');
  log.append(el('summary','执行记录','font-semibold cursor-pointer'), eventView);
  const overview=el('div');
  let overviewKey='';
  let activeKey='';
  const activity=el('div',null,'space-y-1');
  activity.setAttribute('role','status');
  const taskList=el('div',null,'text-xs text-slate-600 space-y-1');
  const controls=line();
  progress.append(strong('运行与历史对话'),buttons,activity,taskList,controls);
  const conversation=section();
  const msgs=el('div',null,'space-y-2 max-h-64 overflow-y-auto');
  msgs.style.overflowAnchor='auto';
  conversation.append(msgs);
  const {row:compose, input, button:sendBtn}=views.composer(value=>send(value));
  const alert=el('p',null,'text-sm text-rose-700'); alert.setAttribute('role','alert'); alert.hidden=true;
  conversation.append(compose,alert);

  const summaryBox = el('details',null,'border border-slate-200 rounded-xl bg-white p-4 text-xs text-slate-600');
  summaryBox.append(el('summary','查看综合方案、工作流和交付','font-semibold cursor-pointer'));
  const summaryBody=el('div',null,'space-y-2 mt-3'); summaryBox.append(summaryBody);
  const advanced=el('details',null,'border border-slate-200 rounded-xl bg-white p-4 text-xs text-slate-600');
  advanced.append(el('summary','高级设置 · 可选','font-semibold cursor-pointer'));
  let savingGates = false;
  const {bar:gateBar, inputs:gateInputs}=views.gates({
    title:'流程确认门禁', options:Object.entries(gateLabels), selected:cfg.gates,
    change(key, checked) {
      cfg.gates = checked ? [...new Set([...cfg.gates, key])] : cfg.gates.filter(item => item !== key);
      if (selected) updateGates();
    },
  });
  const settings=el('div',null,'pt-3 space-y-3');
  settings.append(small('这些设置用于新对话或从需求重新出案。'));
  const [groupWrap,groupCount]=field('分组数（0 代表自动）','0');
  groupCount.type='number';groupCount.min=0;groupCount.max=5;groupCount.oninput=()=>cfg.group_count=Number(groupCount.value);
  const [raceWrap,raceParallel]=field('出案并发上限','3');
  raceParallel.type='number'; raceParallel.min=1; raceParallel.max=5;
  raceParallel.oninput=()=>cfg.race_parallel=Number(raceParallel.value);
  const [parallelWrap,parallel]=field('开发并发上限（0 代表自动）','0');
  parallel.type='number';parallel.min=0;parallel.max=6;parallel.oninput=()=>cfg.parallel=Number(parallel.value);
  const type=el('select',null,'text-xs bg-white border border-slate-300 rounded px-2 py-2');
  for (const [id,title] of [['auto','自动 · 已接入模型时真实开发'],['mock','离线演示 · 不写源码'],['real','真实开发 · 需要模型 API']]) {
    const opt=el('option',title); opt.value=id; type.append(opt);
  }
  type.setAttribute('aria-label','开发模式');
  type.onchange=()=>cfg.mode=type.value;
  const [prefWrap,pref]=field('给主理人的流程偏好','',true);
  pref.placeholder='比如：A 组偏最小版本，B 组偏可维护性；把接口验证放在开发前。';
  pref.oninput=()=>cfg.guidance=pref.value;
  const [pathWrap,path]=field('产物目录（留空使用自动位置）','');
  path.placeholder='例如 D:\\Projects\\MyApp';path.oninput=()=>cfg.directory=path.value;
  const routes=el('div',null,'grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2');
  const promptFields=[];
  for(let i=0;i<5;i++){
    const [wrap,f]=field(String.fromCharCode(65+i)+' 组的提示词覆盖（可留空）','',true);
    f.rows=2; f.oninput=()=>{cfg.prompts=promptFields.map(r=>r.value);}; promptFields.push(f); routes.append(wrap);
  }
  const prompts=el('details',null,'space-y-2');
  prompts.append(el('summary','分组提示词覆盖','cursor-pointer'),routes);
  const generated=el('div',null,'space-y-2');
  const modeWrap = el('label','开发模式','block text-xs text-slate-700 space-y-1');
  type.className = 'block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs';
  modeWrap.append(type);
  const grid = el('div',null,'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3');
  grid.append(groupWrap,raceWrap,parallelWrap,modeWrap);
  const scheduling = el('div',null,'rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-2');
  scheduling.append(strong('分组与并发'),grid);
  const output = el('div',null,'rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-2');
  output.append(strong('流程偏好与产物'),prefWrap,pathWrap,
    small('产物目录留空时，由工作台分配独立目录。'));
  const groupPrompts = el('div',null,'rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-2');
  groupPrompts.append(prompts,generated);
  settings.append(scheduling,output,groupPrompts,
    line(action('从已确认方案编排并单步',()=>send('从任务编排开始，单步调试','replan_manual')),
      action('从需求重新出案',()=>restart()),action('打开任务分解',()=>open('task-planner'))));
  advanced.append(settings);
  const root = views.layout(overview,gateBar,conversation,progress,advanced,summaryBox,log);

  function messageContent(value){alert.textContent=value || '';alert.hidden=!value;}
  function elapsed(seconds){
    const n=Math.max(0,Math.floor(seconds || 0)); return n<60?n+' 秒':Math.floor(n/60)+' 分 '+n%60+' 秒';
  }
  async function send(raw,command=null,takeover=false,settings=null){
    if(busy || savingGates || !raw.trim()) return;
    if(selected && (command==='continue' || /^(?:继续|开始|同意|确认|通过|确认继续|继续推进)[。！!？? ]*$/.test(raw.trim()))
        && !canContinue()) return messageContent('请先回到对应栏目保存手动修改，再继续。');
    const maxBytes = limits()?.max_input_bytes;
    if (maxBytes && new TextEncoder().encode(raw.trim()).length > maxBytes) return messageContent('输入超过 '+maxBytes.toLocaleString()+' 字节（UTF-8）。');
    const project=takeover?existingProject():null;
    const submittingSettings = !selected || takeover || command === 'redo';
    busy=true; messageContent(''); render();
    try {
      const value=takeover
        ? await post('/workspace/steward',{project_id:project?.id,settings:cfg})
        : selected ? await post('/workspace/steward/'+selected.id+'/chat',{message:raw,command,settings})
        : await post('/workspace/steward',{markdown:raw,settings:cfg});
      picked=value.id; safeSave('se-agent-steward-job',picked); selected=value; refreshFailed=false;
      if (submittingSettings) displaySettings(value.settings);
      if(raw===input.value)input.value='';
      loadHistory();
    }catch(err){messageContent(err.message);}
    finally{busy=false; render();}
  }
  async function restart(){
    if(!selected || busy) return;
    if(['running','pausing'].includes(selected.status)){messageContent('先在对话中暂停，再用这些设置重跑。');return;}
    await send('按高级设置重新运行','redo',false,cfg);
  }
  async function updateGates() {
    if (!selected || savingGates) return;
    const id = selected.id;
    const previous = selected.settings.gates;
    savingGates = true;
    render();
    try {
      const updated = await post('/workspace/steward/' + id + '/gates', {gates:cfg.gates});
      if (selected?.id === id) {
        selected = updated;
        cfg.gates = updated.settings.gates;
      }
    } catch (error) {
      if (selected?.id === id) {
        cfg.gates = previous;
        messageContent(error.message);
      }
    } finally {
      savingGates = false;
      render();
    }
  }
  function displaySettings(settings) {
    Object.assign(cfg, settings);
    groupCount.value = cfg.group_count;
    raceParallel.value = cfg.race_parallel;
    parallel.value = cfg.parallel;
    type.value = cfg.mode;
    pref.value = cfg.guidance;
    path.value = cfg.directory;
    promptFields.forEach((field, index) => { field.value = cfg.prompts[index] || ''; });
  }

  let selection = 0;
  async function select(id) {
    const request = ++selection;
    try {
      const data = await get('/workspace/steward/' + id);
      if (request !== selection || busy || savingGates) return;
      selected = data;
      picked = id;
      safeSave('se-agent-steward-job', id);
      displaySettings(data.settings);
      render();
    } catch (error) {
      if (request === selection) messageContent(error.message);
    }
  }
  async function loadHistory(){
    try{
      const index=await get('/workspace/steward');
      history=index.items;
      canReplan=index.team_plan===true;
      render();
      const empty=el('option','历史对话');empty.value=''; historyPicker.replaceChildren(empty);
      for(const item of history){const opt=el('option',item.title.slice(0,32)+' · '+(statuses[item.status] || item.status));opt.value=item.id;historyPicker.append(opt);}
      historyPicker.value=selected?.id || '';
    }catch(err){messageContent(err.message);}
  }
  function render(){
    const s=selected;
    const fingerprint=JSON.stringify([s?.id,s?.project_id,s?.stage,s?.status,s?.gate,s?.settings.gates,s?.run_id]);
    if(fingerprint!==jobFingerprint){
      jobFingerprint=fingerprint;
      queueMicrotask(()=>onJobChange?.());
    }
    const index=s?phases.findIndex(([key])=>key===s.stage):-1;
    const model = {
      title:s?.title || '项目主理人', version:s?.summary?.plan_version,
      done:s?.execution?.done ?? 0, total:s?.execution?.total ?? s?.summary?.tasks?.length ?? 0,
      stage:s?.status === 'completed' ? 8 : [0,1,2,3,3,3,4,5,6,7][index] ?? -1,
      complete:s?.status === 'completed',
    };
    const nextOverviewKey = JSON.stringify(model);
    if (overviewKey !== nextOverviewKey) {
      overviewKey = nextOverviewKey;
      overview.replaceChildren(views.overview(model));
    }
    const current=s ? (statuses[s.status] || s.status) + ' · ' + (s.activity || phases[index]?.[1]) :
      busy ? '正在交接给主理人' : '等待你的需求';
    let duration='';
    if(s?.status==='running'){
      duration='当前步骤已用 '+elapsed(Date.now()/1000-s.activity_at);
      if(s.timeout) duration+=' · 单次模型请求超时设置 '+s.timeout+' 秒';
      if(s.execution?.estimated_seconds>0)duration+=' · 按排期预计剩余约 '+elapsed(s.execution.estimated_seconds);
    }
    const mainText=current+(refreshFailed?' · 暂时无法同步':'');
    if(activity.firstChild?.textContent!==mainText)activity.replaceChildren(strong(mainText),small(''));
    activity.lastChild.textContent=s?.error || (s?.status==='gate'?'你开启了「'+gateLabels[s.gate]+'」确认。直接回复继续，或说要改哪里。':duration);
    let indicator=s? '第 '+s.phase+' 轮 · '+(s.summary?.mode==='real'?'真实代码开发':s.summary?.mode==='mock'?'模拟执行':'方案与任务准备中') :
      (modelMode()==='model'?'已配置真实模型 · 可直接交给主理人':'未配置模型 API · 可先跑离线演示');
    if(s?.groups.length){
      const racing=s.groups.filter(g=>g.status==='running').length;
      indicator+=' · '+s.groups.filter(g=>g.finished).length+'/'+s.groups.length+' 组交案';
      if(racing)indicator+=' · '+racing+' 组正在并发出案';
    }
    if(s?.execution)indicator+=' · '+s.execution.done+'/'+s.execution.total+' 项任务完成';
    const running=(s?.execution?.running || []).map(t=>t.id+' · '+t.step);
    if(s?.stage==='race')running.push(...s.groups.filter(g=>g.status==='running' || g.status==='failed').map(g=>g.step));
    if(taskList.textContent!==indicator+running.join('')) taskList.replaceChildren(small(indicator),...running.map(small));
    const key=(s?.id || '')+':'+(s?.status || '')+':'+busy+':'+canReplan;
    if(key!==activeKey){
      activeKey=key;controls.replaceChildren();
      if(s?.status==='running')controls.append(action('暂停派发',()=>send('暂停','pause')));
      if(s?.status==='paused'||s?.status==='gate')controls.append(action('继续',()=>send('继续','continue'),true));
      if(s?.status==='failed'){
        controls.append(action('从当前步骤重试',()=>send('重试','retry'),true));
        if(canReplan && s?.project_id && s?.run_id)controls.append(action('按模块重新拆分',()=>send('按模块重新拆分','replan')));
      }
    }
    const changes=JSON.stringify([s?.id,s?.messages,s?.events,s?.summary,s?.target,s?.delivery,s?.groups]);
    if(changes!==shown){
      shown=changes;
      msgs.replaceChildren(...(s?.messages || [{role:'steward',text:'你好。把需求直接发给我就行，默认不需要你逐步确认。'}]).map(views.message));
      msgs.scrollTop=msgs.scrollHeight;
      eventView.replaceChildren(...(s?.events || []).slice(-25).map(e=>el('li',e.title+(e.detail?' · '+e.detail:''))));
      log.hidden=!s?.events.length;
      summaryBox.hidden=!s?.summary && !s?.project_id;
      summaryBody.replaceChildren();
      if(s?.summary?.markdown)summaryBody.append(el('pre',s.summary.markdown,'bg-slate-50 p-2 rounded whitespace-pre-wrap max-h-64 overflow-auto'));
      if(s?.summary?.tasks?.length){
        const tasks=el('ul',null,'list-disc ml-5');
        for(const t of s.summary.tasks)tasks.append(el('li',t.id+' · '+t.title+' · '+elapsed(t.estimate_seconds)+(t.depends_on.length?' · 前置 '+t.depends_on.join('、'):'')+(t.outputs?.length?' · 产物 '+t.outputs.join('、'):'')));
        summaryBody.append(strong('已生成的任务与依赖'),tasks);
      }
      if(s?.target?.repo)summaryBody.append(small('代码仓库：'+s.target.repo));
      if(s?.delivery)summaryBody.append(small('交付文件：'+s.delivery.directory));
      if(s?.delivery?.git_branch)summaryBody.append(small('合并后的交付分支：'+s.delivery.git_branch),
        small('交付提交：'+s.delivery.git_commit));
      if(s?.project_id)summaryBody.append(action('打开任务图、甘特图与产物',()=>openProject(s.project_id)));
      generated.replaceChildren();
      if(s?.groups.length) {
        const detail=el('details',null,'space-y-2 text-xs');
        detail.append(el('summary','查看主理人生成的分工与提示词','cursor-pointer'));
        for (const g of s.groups) detail.append(small(g.id+' 组 · '+g.focus),el('pre',g.prompt,'whitespace-pre-wrap p-2 bg-slate-50 rounded'));
        generated.append(detail);
      }
    }
    newButton.disabled=busy || savingGates; sendBtn.disabled=busy || savingGates;
    historyPicker.disabled=busy || savingGates;
    adopt.hidden=!existingProject() || !!s;
    adopt.disabled=busy || savingGates;
    sendBtn.textContent=busy?'正在处理…':'发送指令';
    gateInputs.forEach(({key,input})=>{input.checked=cfg.gates.includes(key); input.disabled=busy || savingGates;});
  }
  let initialized=false;
  const timer=setInterval(async()=>{
    if(!picked || syncing || busy || savingGates || ['completed','cancelled'].includes(selected?.status))return;
    syncing=true;
    const id=picked, previous=selected;
    try{
      const value=await get('/workspace/steward/'+id);
      if(id===picked && selected===previous && !busy && !savingGates){selected=value;refreshFailed=false;render();}
    }catch{refreshFailed=true;render();}
    finally{syncing=false;}
  },1300);
  window.addEventListener('pagehide',event=>{if(!event.persisted)clearInterval(timer);});
  return {
    show(wrap){visible=true; if(root.parentNode!==wrap)wrap.append(root);render(); if(!initialized){initialized=true;loadHistory();if(picked)select(picked);}},
    hide(){visible=false;},
    current(){return selected;},
    control(command){return send(command==='pause'?'暂停':'继续',command);},
    async enableGate(name){
      if(!selected || busy || savingGates || cfg.gates.includes(name))return;
      cfg.gates=[...cfg.gates,name];
      await updateGates();
    },
    load(id){open('steward');select(id);},
    newProject(){selection++; selected=null; picked='';safeSave('se-agent-steward-job','');open('steward'); render();},
  };
};
