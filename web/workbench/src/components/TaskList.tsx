import type { TaskPreview } from '../types/workbench'
import { formatDuration, taskStatusText } from './presentation'

export function TaskList({ tasks, selectedId, onSelect }: {
  tasks: readonly TaskPreview[]
  selectedId: string
  onSelect: (id: string) => void
}) {
  return (
    <section className="workflow-task-list" aria-labelledby="workflow-task-list-title">
      <h3 id="workflow-task-list-title">任务列表</h3>
      <ul>
        {tasks.map((task) => (
          <li key={task.id}>
            <button type="button" aria-pressed={task.id === selectedId} onClick={() => onSelect(task.id)}>
              <strong><span>{task.id}</span> {task.title}</strong>
              <span className="task-list-meta">
                <small>{task.role} · {formatDuration(task.estimateSeconds)}</small>
                <span className={`chip chip-${task.status}`}>{taskStatusText[task.status]}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
