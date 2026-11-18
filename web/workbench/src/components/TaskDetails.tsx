import type { Artifact, Attempt, ReviewRecord, VersionRef } from '../types/v2'
import type { TaskPreview } from '../types/workbench'
import { formatDuration, formatVersion, taskStatusText } from './presentation'

const attemptStatus: Record<Attempt['state'], string> = {
  running: '执行中', succeeded: '执行成功', failed: '执行失败', input_required: '待补充输入',
}
const artifactStatus: Record<Artifact['status'], string> = {
  draft: '草稿', approved: '已批准', rejected: '已退回',
}
const reviewDecision: Record<ReviewRecord['decision'], string> = {
  approve: '批准原稿', revise: '批准修订版', reject: '退回',
}
const artifactKind: Record<Artifact['kind'], string> = { git_commit: 'Git 提交', text: '文本' }
const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' })

function Timestamp({ value, empty }: { value: string | null; empty: string }) {
  return value ? <time dateTime={value}>{dateTimeFormat.format(new Date(value))}</time> : empty
}

function Attempts({ attempts }: { attempts: readonly Attempt[] }) {
  return (
    <section className="task-detail-section" aria-labelledby="task-attempts-title">
      <h4 id="task-attempts-title">执行尝试</h4>
      {attempts.length === 0 ? <p className="task-empty">暂无执行尝试。</p> : (
        <ul className="task-history">
          {attempts.map((attempt) => (
            <li key={attempt.id}>
              <div className="detail-heading">
                <strong>第 {attempt.number} 次 · <code>{attempt.id}</code></strong>
                <span>{attemptStatus[attempt.state]}</span>
              </div>
              <dl className="task-fields">
                <div><dt>开始</dt><dd><Timestamp value={attempt.started_at} empty="未开始" /></dd></div>
                <div><dt>结束</dt><dd><Timestamp value={attempt.ended_at} empty="未结束" /></dd></div>
                <div><dt>输入版本</dt><dd>{attempt.input_refs.map(formatVersion).join('、') || '无'}</dd></div>
              </dl>
              {attempt.error && (
                <>
                  <p className="task-failure">失败原因：{attempt.error.message}（{attempt.error.code}）</p>
                  {Object.keys(attempt.error.details).length > 0 && (
                    <details className="task-expand">
                      <summary>查看错误详情</summary>
                      <pre>{JSON.stringify(attempt.error.details, null, 2)}</pre>
                    </details>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Artifacts({ artifacts, reviewRecords }: {
  artifacts: readonly Artifact[]
  reviewRecords: readonly ReviewRecord[]
}) {
  return (
    <section className="task-detail-section" aria-labelledby="task-artifacts-title">
      <h4 id="task-artifacts-title">实际产物与审核</h4>
      {artifacts.length === 0 ? <p className="task-empty">暂无实际产物。</p> : (
        <ul className="task-history">
          {artifacts.map((artifact) => (
            <li key={formatVersion(artifact)}>
              <div className="detail-heading">
                <strong><code>{formatVersion(artifact)}</code></strong>
                <span className={`chip chip-${artifact.status === 'approved' ? 'done' : artifact.status === 'rejected' ? 'failed' : 'waiting'}`}>
                  {artifactStatus[artifact.status]}
                </span>
              </div>
              <dl className="task-fields">
                <div><dt>类型与来源</dt><dd>{artifactKind[artifact.kind]} · {artifact.origin === 'human' ? '人工修订' : 'Agent'}</dd></div>
                <div><dt>输出与尝试</dt><dd>{artifact.output_id} · {artifact.attempt_id}</dd></div>
                {artifact.based_on_version !== null && (
                  <div><dt>基于版本</dt><dd>{formatVersion({ id: artifact.id, version: artifact.based_on_version })}</dd></div>
                )}
                <div className="task-wide-field"><dt>Git SHA</dt><dd>{artifact.commit_sha ? <code>{artifact.commit_sha}</code> : '无'}</dd></div>
                {artifact.changed_paths.length > 0 && (
                  <div className="task-wide-field"><dt>实际修改路径</dt><dd>{artifact.changed_paths.join('、')}</dd></div>
                )}
              </dl>
              {artifact.content !== null && (
                <details className="task-expand"><summary>查看文本产物</summary><pre>{artifact.content}</pre></details>
              )}
            </li>
          ))}
        </ul>
      )}
      {reviewRecords.length > 0 && (
        <div className="task-reviews">
          <h5>审核记录</h5>
          <ul>
            {reviewRecords.map((record, index) => (
              <li key={`${record.created_at}:${index}`}>
                {reviewDecision[record.decision]} · {record.actor_id} · <Timestamp value={record.created_at} empty="时间未知" />
                {record.note && <p>{record.note}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

export function TaskDetails({ task, tasks, specRef, onSelect }: {
  task: TaskPreview
  tasks: readonly TaskPreview[]
  specRef: VersionRef
  onSelect: (id: string) => void
}) {
  const titleById = new Map(tasks.map(({ id, title }) => [id, title]))
  const taskLink = (id: string) => titleById.has(id)
    ? <button type="button" className="task-link" onClick={() => onSelect(id)} title={titleById.get(id)}>{id}</button>
    : <code>{id}</code>

  return (
    <section className="task-details" aria-labelledby="task-details-title">
      <div className="detail-heading">
        <h3 id="task-details-title" aria-live="polite"><span>{task.id}</span> {task.title}</h3>
        <span className={`chip chip-${task.status}`}>{taskStatusText[task.status]}</span>
      </div>
      <dl className="task-fields">
        <div><dt>规格来源</dt><dd><code>{formatVersion(specRef)}</code></dd></div>
        <div><dt>执行角色</dt><dd>{task.role}</dd></div>
        <div><dt>估时</dt><dd>{formatDuration(task.estimateSeconds)}{task.onCriticalPath && ' · 关键路径'}</dd></div>
        <div><dt>需求引用</dt><dd>{task.requirementIds.join('、') || '无'}</dd></div>
        <div><dt>决策引用</dt><dd>{task.decisionIds.join('、') || '无'}</dd></div>
        <div>
          <dt>前置任务</dt>
          <dd className="task-link-list">
            {task.dependencies.length ? task.dependencies.map((id) => <span key={id}>{taskLink(id)}</span>) : '无'}
          </dd>
        </div>
        <div className="task-wide-field"><dt>修改路径</dt><dd>{task.writePaths.join('、') || '无'}</dd></div>
        <div className="task-wide-field">
          <dt>预期产物</dt>
          <dd>{task.outputs.length ? (
            <ul className="task-outputs">
              {task.outputs.map(({ id, kind, path }) => (
                <li key={id}><code>{id}</code> · {artifactKind[kind]}{path && <> · <code>{path}</code></>}</li>
              ))}
            </ul>
          ) : '无'}</dd>
        </div>
        <div className="task-wide-field"><dt>完成条件</dt><dd>{task.completionCondition || '未提供'}</dd></div>
        <div><dt>人工审核</dt><dd>{task.reviewRequired ? '需要' : '不需要'}</dd></div>
      </dl>
      {(task.blockers.length > 0 || task.missingInputIds.length > 0) && (
        <section className="task-detail-section task-blockers" aria-labelledby="task-blockers-title">
          <h4 id="task-blockers-title">阻塞原因</h4>
          {task.blockers.length > 0 && (
            <ul>
              {task.blockers.map(({ code, ref_id, message }, index) => (
                <li key={`${code}:${ref_id}:${index}`}>
                  <p>{message}（{code} · {ref_id}）</p>
                  {titleById.has(ref_id) && <p>关联任务：{taskLink(ref_id)} {titleById.get(ref_id)}</p>}
                </li>
              ))}
            </ul>
          )}
          {task.missingInputIds.length > 0 && <p>缺少输入：{task.missingInputIds.join('、')}</p>}
        </section>
      )}
      <Attempts attempts={task.attempts} />
      <Artifacts artifacts={task.artifacts} reviewRecords={task.reviewRecords} />
    </section>
  )
}
