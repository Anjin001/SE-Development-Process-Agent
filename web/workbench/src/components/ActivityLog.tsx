import { ChevronDown, History } from 'lucide-react'
import type { ActivityEntry } from '../types/workbench'

const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export function ActivityLog({ entries }: { entries: readonly ActivityEntry[] }) {
  return (
    <details className="activity">
      <summary>
        <History size={17} aria-hidden="true" />
        <strong>活动记录</strong>
        <span className="activity-count">{entries.length} 条</span>
        <ChevronDown className="activity-chevron" size={17} aria-hidden="true" />
      </summary>
      {entries.length > 0 ? (
        <ol>
          {entries.map((entry) => (
            <li key={entry.id}>
              <time dateTime={entry.time}>{clock.format(new Date(entry.time))}</time>
              <div><strong>{entry.title}</strong><p>{entry.description}</p></div>
            </li>
          ))}
        </ol>
      ) : <p className="activity-empty">暂无活动记录。</p>}
    </details>
  )
}
