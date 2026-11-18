import { useState } from 'react'
import { pendingArtifacts, validCommit } from '../api/reviewArtifacts'
import type { Run, RunCommand } from '../types/v2'
import { formatVersion } from './presentation'

export function DeliveryReview({ run, selectedId, onSelect, submit, busy }: {
  run: Run | null
  selectedId?: string
  onSelect: (id: string) => void
  submit?: (command: RunCommand) => Promise<boolean>
  busy: boolean
}) {
  const [drafts, setDrafts] = useState(new Map<string, { base: string; note: string; edits: ReadonlyMap<string, string> }>())
  const queue = run?.tasks.filter(({ state }) => state === 'review') ?? []
  const task = queue.find(({ task_id }) => task_id === selectedId)
  const pending = pendingArtifacts(task?.artifacts ?? [])
  const key = JSON.stringify([run?.id, selectedId])
  const base = JSON.stringify(pending.map(formatVersion).sort())
  const draft = drafts.get(key) ?? { base, note: '', edits: new Map<string, string>() }
  const stale = draft.base !== base
  const saveDraft = (next: typeof draft) => setDrafts((previous) => new Map(previous).set(key, next))
  const changed = pending.filter((item) => {
    const edit = draft.edits.get(item.id)
    if (edit === undefined) return false
    if (item.kind === 'text') return edit !== item.content
    const sha = edit.trim().toLowerCase()
    return sha !== '' && sha !== item.commit_sha?.toLowerCase()
  })
  const invalid = changed.some((item) => {
    const value = draft.edits.get(item.id)!.trim()
    return item.kind === 'text' ? !value : !validCommit(value)
  })
  const disabled = busy || stale || !submit || !run || run.state !== 'active' || !pending.length

  async function send(decision: 'approve' | 'revise' | 'reject') {
    if (!run || !task || !submit || disabled) return
    if (decision === 'revise' && (!changed.length || invalid)) return
    if (decision === 'reject' && !draft.note.trim()) return
    const revisions = decision === 'revise' ? Object.fromEntries(changed.map((item) => [
      item.id, item.kind === 'text' ? { content: draft.edits.get(item.id)! } : { commit_sha: draft.edits.get(item.id)!.trim() },
    ])) : {}
    if (await submit({
      action: 'review', task_id: task.task_id, decision, expected_revision: run.revision,
      expected_versions: Object.fromEntries(pending.map(({ id, version }) => [id, version])),
      revisions, note: draft.note.trim(),
    })) setDrafts((previous) => { const next = new Map(previous); next.delete(key); return next })
  }

  return (
    <section className="flow-card delivery-review" aria-labelledby="delivery-title">
      <h3 id="delivery-title">交付审阅</h3>
      <div className="flow-actions" aria-label="待审核任务">
        {queue.map(({ task_id }) => <button key={task_id} type="button" aria-pressed={selectedId === task_id}
          onClick={() => onSelect(task_id)}>审阅 {task_id}</button>)}
      </div>
      {!queue.length && <p>暂无待审核任务。历史版本与审核记录见下方任务详情。</p>}
      {queue.length > 0 && !task && <p>请选择待审核任务。</p>}
      {stale && <div className="flow-alert">
        <p>待审版本已变化。本地草稿仍保留，请比对新版本后再应用。</p>
        {draft.note && <p>本地备注：{draft.note}</p>}
        {draft.edits.size > 0 && <pre>{[...draft.edits.entries()].map(([id, value]) => `${id}: ${value}`).join('\n')}</pre>}
        {task && <button type="button" onClick={() => saveDraft({ ...draft, base })}>核对后继续编辑</button>}
      </div>}
      {task && !pending.length && <p>暂无可提交的待审产物，请刷新运行。</p>}
      {pending.map((artifact) => (
        <article key={formatVersion(artifact)} className="review-artifact">
          <h4>{formatVersion(artifact)} · {artifact.output_id}</h4>
          <p>{artifact.kind === 'text' ? '文本' : 'Git 提交'} · {artifact.origin === 'agent' ? 'Agent' : '人工修订'} · 尝试 {artifact.attempt_id}</p>
          {artifact.kind === 'text' ? <pre>{artifact.content ?? '暂无原稿内容'}</pre> : (
            <><p>Git SHA：<code>{artifact.commit_sha ?? '无'}</code></p><p>变更路径：{artifact.changed_paths.join('、') || '无'}</p></>
          )}
          <label>{artifact.kind === 'text' ? '修订内容' : '修订 SHA'} · {artifact.id}
            <textarea rows={artifact.kind === 'text' ? 4 : 1} disabled={disabled}
              value={draft.edits.get(artifact.id) ?? (artifact.kind === 'text' ? artifact.content ?? '' : '')}
              onChange={(event) => saveDraft({ ...draft, edits: new Map(draft.edits).set(artifact.id, event.target.value) })} />
          </label>
        </article>
      ))}
      {pending.length > 0 && <>
        <label>审核备注<textarea rows={2} disabled={disabled} value={draft.note}
          onChange={(event) => saveDraft({ ...draft, note: event.target.value })} /></label>
        {invalid && <p className="flow-alert">修订文本不能为空，Git 修订需填写完整的 40 位十六进制 SHA。</p>}
        <div className="flow-actions">
          <button type="button" disabled={disabled} onClick={() => void send('approve')}>批准原稿</button>
          <button type="button" disabled={disabled || invalid || !changed.length} onClick={() => void send('revise')}>修订并批准</button>
          <button type="button" disabled={disabled || !draft.note.trim()} onClick={() => void send('reject')}>退回</button>
        </div>
        <p className="flow-muted">退回必须填写原因。Git 修订提交的归属和可达性由后端校验。</p>
      </>}
    </section>
  )
}
