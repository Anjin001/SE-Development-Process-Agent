import { useProjectDraft } from './projectDrafts'
import { completedGroup, currentRace, currentSpecification, groupIds, raceIsComplete } from '../api/projectVersions'
import type { ProjectActions } from '../api/workbenchApi'
import type { ProjectSnapshot, Race, Specification, SpecificationSelection, VersionRef } from '../types/v2'
import type { StageId, WorkbenchSnapshot } from '../types/workbench'
import { formatVersion } from './presentation'

interface ProjectStageProps {
  project: ProjectSnapshot
  actions?: ProjectActions
  allowCreate?: boolean
  exampleMarkdown?: string
  busy: boolean
  perform: (request: () => Promise<WorkbenchSnapshot>, opensProject?: boolean) => Promise<boolean>
  navigate: (stage: StageId) => void
}

function useVersionedMarkdown(name: string, source: string, ref: VersionRef | null) {
  const [edit, setEdit] = useProjectDraft<{ markdown: string; base: string | null } | undefined>(name, undefined)
  const current = ref ? formatVersion(ref) : null
  const markdown = edit?.markdown ?? source
  return {
    markdown,
    dirty: markdown !== source,
    stale: edit !== undefined && edit.base !== current,
    change: (text: string) => setEdit((previous) => ({ markdown: text, base: previous ? previous.base : current })),
    reset: () => setEdit(undefined),
    rebase: () => setEdit((previous) => previous ? { ...previous, base: current } : undefined),
  }
}

function Requirements({ project, exampleMarkdown, actions, busy, perform, navigate, allowCreate = true }: ProjectStageProps) {
  const requirement = project.requirement
  const editor = useVersionedMarkdown('requirement', requirement.markdown, requirement)
  const canSubmit = actions && !busy && editor.markdown.trim() !== ''
  const sourceLines = requirement.markdown.split(/\r?\n/)

  async function submit(create: boolean) {
    if (!actions || !canSubmit || (!create && editor.stale)) return
    const saved = await perform(() => create
      ? actions.createProject(editor.markdown)
      : actions.saveRequirement(project.id, { expected_version: requirement.version, markdown: editor.markdown }), create)
    if (saved) editor.reset()
  }

  return (
    <div className="flow-stack">
      <form className="flow-card" onSubmit={(event) => { event.preventDefault(); void submit(false) }}>
        <div className="flow-heading"><h3>需求 Markdown</h3><span className="chip chip-blue">{formatVersion(requirement)}</span></div>
        <label className="flow-label" htmlFor="requirement-markdown">编辑需求原文</label>
        <textarea id="requirement-markdown" rows={6} value={editor.markdown}
          readOnly={!actions} disabled={busy} onChange={(event) => editor.change(event.target.value)} />
        {editor.stale && (
          <div className="flow-alert">
            <p>已保存需求版本有变化。当前文本仍保留，请核对后再提交。</p>
            <details><summary>查看最新已保存需求</summary><pre>{requirement.markdown}</pre></details>
            <button type="button" onClick={editor.rebase}>以最新需求版本继续编辑</button>
          </div>
        )}
        {exampleMarkdown !== undefined && <p className="flow-muted">模拟模式仅为团队样例提供需求条目和出案结果。</p>}
        <div className="flow-actions">
          <button className="flow-primary" type="submit" disabled={!canSubmit || editor.stale}>提交需求版本</button>
          {allowCreate && <button type="button" disabled={!canSubmit} onClick={() => void submit(true)}>用此需求新建项目</button>}
          {exampleMarkdown !== undefined && <button type="button" disabled={busy} onClick={() => editor.change(exampleMarkdown)}>载入团队样例</button>}
          <button type="button" disabled={busy || editor.dirty || editor.stale} onClick={() => navigate('race')}>查看方案赛马</button>
        </div>
      </form>
      <section className="flow-card" aria-labelledby="requirements-title">
        <h3 id="requirements-title">当前版本的需求条目</h3>
        {requirement.items.length === 0 ? <p className="flow-muted">该版本暂无需求条目。</p> : (
          <ul className="flow-list">
            {requirement.items.map(({ id, title, text, source }) => (
              <li key={id}>
                <strong>{id} · {title}</strong>
                <p>{text}</p>
                <details>
                  <summary>原文：{source.section} · 第 {source.start_line === source.end_line ? source.start_line : `${source.start_line}–${source.end_line}`} 行</summary>
                  <pre>{sourceLines.slice(source.start_line - 1, source.end_line).join('\n')}</pre>
                </details>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

const groupStatus: Record<Race['groups'][number]['state'], string> = {
  pending: '排队中', running: '进行中', completed: '已完成', failed: '失败',
}

const criteria = [
  ['value', '用户价值'], ['feasibility', '技术可行性'], ['cost', '成本友好度'],
] as const

function RaceComparison({ project, actions, busy, perform, navigate }: ProjectStageProps) {
  const race = currentRace(project)
  const failed = race?.groups.some(({ state }) => state === 'failed')
  const active = race?.groups.some(({ state }) => state === 'running' || state === 'pending')
  const complete = raceIsComplete(project)

  return (
    <div className="flow-stack">
      <section className="flow-card">
        <div className="flow-heading"><h3>方案进度</h3><span className="chip chip-blue">{formatVersion(project.requirement)}</span></div>
        <p className="flow-muted" role="status">
          {complete ? '两组方案与评分已就绪。' : active ? '赛马进行中，已完成的小组会先显示。' : failed ? '有小组失败，可重试。' : race ? '等待完整方案与评分。' : '当前需求版本尚未启动赛马。'}
        </p>
        <div className="flow-actions">
          {(!race || failed) && (
            <button className="flow-primary" type="button" disabled={!actions || busy}
              onClick={() => actions && void perform(() => actions.startRace(project.id, project.requirement.version))}>
              {failed ? '重试失败组' : '启动 A/B 赛马'}
            </button>
          )}
          {complete && <button className="flow-primary" type="button" onClick={() => navigate('review')}>进入规格评审</button>}
        </div>
      </section>
      <div className="flow-comparison">
        {groupIds.map((id) => {
          const group = race?.groups.find((entry) => entry.group_id === id)
          const result = completedGroup(project, id)
          return (
            <article key={id} className="flow-card" aria-labelledby={`race-group-${id}`}>
              <div className="flow-heading">
                <h3 id={`race-group-${id}`}>{id} 组方案</h3>
                <span className={`chip ${group?.state === 'failed' ? 'chip-failed' : result ? 'chip-done' : 'chip-waiting'}`}>
                  {group?.state === 'completed' && !result ? '结果待同步' : group ? groupStatus[group.state] : '未开始'}
                </span>
              </div>
              {group?.error && <p className="flow-alert">{group.error.message}（{group.error.code}）</p>}
              {result && (
                <>
                  <h4>{result.draft.title}</h4>
                  <p>{result.draft.summary}</p>
                  <ul className="flow-list">
                    {result.draft.claims.map((claim) => (
                      <li key={claim.id}>
                        <strong>{claim.id}</strong> · {claim.text}
                        <p className="flow-muted">关联需求：{claim.requirement_ids.join('、') || '无'}</p>
                      </li>
                    ))}
                  </ul>
                  <dl className="flow-scores">
                    {criteria.map(([key, label]) => {
                      const score = result.evaluation.criteria.find((entry) => entry.criterion === key)
                      return (
                        <div key={key}>
                          <dt>{label} <small>({key})</small></dt>
                          <dd>{score ? <><strong>{score.score} / 5</strong> · {score.reason}
                            {score.claim_ids.length > 0 && <p className="flow-muted">对应主张：{score.claim_ids.join('、')}</p>}
                          </> : '暂无评分'}</dd>
                        </div>
                      )
                    })}
                  </dl>
                </>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}

function SpecDetails({ spec }: { spec: Specification }) {
  return (
    <div className="flow-comparison">
      <section className="flow-card">
        <h3>整合决策</h3>
        {spec.decisions.length === 0 ? <p className="flow-muted">该版本暂无整合决策。</p> : (
          <ul className="flow-list">
            {spec.decisions.map((decision) => (
              <li key={decision.id}>
                <strong>{decision.id} · {decision.scope}</strong>
                <p>{decision.choice}</p>
                <p className="flow-muted">{decision.reason}</p>
                <p className="flow-muted">关联需求：{decision.requirement_ids.join('、') || '无'} · 方案：{decision.draft_ids.join('、') || '无'}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="flow-card">
        <h3>待决事项</h3>
        {spec.open_items.length === 0 ? <p className="flow-muted">该版本暂无待决事项。</p> : (
          <ul className="flow-list">
            {spec.open_items.map((item) => (
              <li key={item.id}>
                <strong>{item.id} · {item.question}</strong>
                <p>{item.resolution ?? (item.blocking ? '未解决 · 阻塞相关任务' : '未解决')}</p>
                <p className="flow-muted">影响需求：{item.affected_requirement_ids.join('、') || '无'}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function SpecificationEditor({ project, actions, busy, perform, navigate }: ProjectStageProps) {
  const spec = currentSpecification(project)
  const editor = useVersionedMarkdown('specification', spec?.markdown ?? '', spec)
  const [choice, setChoice] = useProjectDraft<SpecificationSelection | undefined>('specification-choice', undefined)
  const selection = choice ?? spec?.selection ?? 'merge'
  const confirmed = spec?.status === 'confirmed'

  if (!raceIsComplete(project)) {
    return (
      <section className="flow-card flow-stack">
        <h3>规格尚未就绪</h3>
        <p>两组方案及评分均已返回后，才能选择方案并生成规格。</p>
        <div className="flow-actions"><button type="button" onClick={() => navigate('race')}>查看赛马进度</button></div>
      </section>
    )
  }

  async function createDraft() {
    if (!actions) return
    if (await perform(() => actions.saveSpecification(project.id, {
      requirement_version: project.requirement.version,
      expected_version: project.specification?.version ?? 0,
      selection,
    }))) {
      editor.reset()
      setChoice(undefined)
    }
  }

  async function saveDraft() {
    if (!actions || !spec || editor.stale) return
    if (await perform(() => actions.saveSpecification(project.id, {
      requirement_version: spec.requirement_ref.version,
      expected_version: spec.version,
      selection: spec.selection,
      markdown: editor.markdown,
      decisions: spec.decisions,
      open_items: spec.open_items,
    }))) editor.reset()
  }

  function confirmDraft() {
    if (!actions || !spec || editor.dirty || editor.stale) return
    navigate('review')
    void perform(() => actions.confirmSpecification(project.id, { id: spec.id, version: spec.version }))
  }

  return (
    <div className="flow-stack">
      <section className="flow-card">
        <div className="flow-heading"><h3>选择规格起点</h3><button className="flow-secondary" type="button" onClick={() => navigate('race')}>查看 A/B 对比</button></div>
        <div className="flow-actions">
          <label htmlFor="spec-selection">采用方案</label>
          <select id="spec-selection" value={selection} disabled={busy || !actions || editor.dirty || editor.stale}
            onChange={(event) => setChoice(event.target.value as SpecificationSelection)}>
            <option value="A">方案 A</option><option value="B">方案 B</option><option value="merge">融合方案</option>
          </select>
          <button className="flow-primary" type="button"
            disabled={busy || !actions || editor.dirty || editor.stale || (spec?.status === 'draft' && selection === spec.selection)}
            onClick={() => void createDraft()}>生成待确认规格</button>
        </div>
      </section>
      {(spec || editor.stale) && (
        <section className="flow-card">
          <div className="flow-heading">
            <h3>规格 Markdown</h3>
            {spec && <span className={`chip ${confirmed ? 'chip-done' : 'chip-review'}`}>{formatVersion(spec)} · {confirmed ? '已确认' : '未确认'}</span>}
          </div>
          {spec && <p className="flow-muted">绑定需求：{formatVersion(spec.requirement_ref)}
            {spec.confirmed_by && ` · 确认人：${spec.confirmed_by}`}</p>}
          <label className="flow-label" htmlFor="spec-markdown">{confirmed && !editor.stale ? '已确认规格原文' : '编辑规格原文'}</label>
          <textarea id="spec-markdown" rows={6} value={editor.markdown} disabled={busy}
            readOnly={confirmed || !actions || !spec} onChange={(event) => editor.change(event.target.value)} />
          {editor.stale && (
            <div className="flow-alert">
              <p>规格版本已变化，未保存文本仍保留。</p>
              {spec && <details><summary>查看最新已保存规格</summary><pre>{spec.markdown}</pre></details>}
              <div className="flow-actions">
                {spec?.status === 'draft' && <button type="button" onClick={editor.rebase}>以最新规格版本继续编辑</button>}
                <button type="button" onClick={editor.reset}>载入最新已保存规格</button>
              </div>
            </div>
          )}
          {spec?.status === 'draft' && (
            <div className="flow-actions">
              <button type="button" disabled={busy || !actions || editor.stale || !editor.dirty || !editor.markdown.trim()}
                onClick={() => void saveDraft()}>保存草稿</button>
              <button className="flow-primary" type="button" disabled={busy || !actions || editor.stale || editor.dirty}
                onClick={confirmDraft}>确认规格</button>
              {editor.dirty && !editor.stale && <span className="flow-muted">保存草稿后可确认。</span>}
            </div>
          )}
        </section>
      )}
      {spec && <SpecDetails spec={spec} />}
    </div>
  )
}

export function ProjectStages({ stage, ...props }: ProjectStageProps & { stage: 'intake' | 'race' | 'review' }) {
  if (stage === 'intake') return <Requirements {...props} />
  if (stage === 'race') return <RaceComparison {...props} />
  return <SpecificationEditor {...props} />
}
