import { Background, Controls, Handle, MarkerType, Position, ReactFlow } from '@xyflow/react'
import type { DefaultEdgeOptions, Node, NodeProps } from '@xyflow/react'
import { Clock3 } from 'lucide-react'
import { useMemo } from 'react'
import '@xyflow/react/dist/style.css'
import type { TaskPreview } from '../types/workbench'
import { layoutDependencies, taskNodeSize } from './dependencyLayout'
import { formatDuration, taskStatusText } from './presentation'

type TaskNode = Node<{
  task: TaskPreview
  isSelected: boolean
  onSelect: (id: string) => void
}, 'task'>

const fitOptions = { padding: 0.14, maxZoom: 1 }
const controlLabels = {
  'controls.ariaLabel': '依赖图视图控制',
  'controls.zoomIn.ariaLabel': '放大依赖图',
  'controls.zoomOut.ariaLabel': '缩小依赖图',
  'controls.fitView.ariaLabel': '适应视图',
}
const lineColor = '#8598b9'

function TaskCard({ data }: NodeProps<TaskNode>) {
  const { task, isSelected, onSelect } = data
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <button
        type="button"
        className="graph-task nodrag nopan"
        aria-pressed={isSelected}
        onClick={() => onSelect(task.id)}
      >
        <span className="graph-task-meta">
          <span>
            <strong>{task.id}</strong>
            {task.onCriticalPath && <span className="graph-critical" title="关键路径任务">关键</span>}
          </span>
          <span><Clock3 size={10} aria-hidden="true" /> {formatDuration(task.estimateSeconds)}</span>
        </span>
        <strong className="graph-task-name" title={task.title}>{task.title}</strong>
        <span className="graph-task-role" title={task.role}>{task.role}</span>
        <span className={`chip chip-${task.status}`}>{taskStatusText[task.status]}</span>
      </button>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </>
  )
}

const nodeTypes = { task: TaskCard }

const defaultEdgeOptions: DefaultEdgeOptions = {
  type: 'smoothstep',
  style: { stroke: lineColor, strokeWidth: 1.8 },
  markerEnd: { type: MarkerType.ArrowClosed, color: lineColor, width: 15, height: 15 },
}

export function DependencyPreview({ tasks, selectedId, onSelect }: {
  tasks: readonly TaskPreview[]
  selectedId: string
  onSelect: (id: string) => void
}) {
  const { positions, edges } = useMemo(() => layoutDependencies(tasks), [tasks])
  const nodes: TaskNode[] = tasks.map((task) => ({
    id: task.id,
    type: 'task',
    position: positions.get(task.id)!,
    ...taskNodeSize,
    data: { task, isSelected: task.id === selectedId, onSelect },
  }))

  return (
    <div className="graph-preview">
      <div className="graph-topline"><strong>任务依赖</strong><span>拖动空白处平移，点击任务查看详情</span></div>
      <div className="graph-canvas" role="region" aria-label="任务依赖图">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          defaultEdgeOptions={defaultEdgeOptions}
          nodeTypes={nodeTypes}
          ariaLabelConfig={controlLabels}
          fitView
          fitViewOptions={fitOptions}
          minZoom={0.1}
          maxZoom={2}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          panOnDrag
          zoomOnDoubleClick={false}
          zoomOnPinch
          zoomOnScroll={false}
          preventScrolling={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={17} size={1.2} color="#d6e0f1" />
          <Controls showInteractive={false} fitViewOptions={fitOptions} position="bottom-right" />
        </ReactFlow>
      </div>
      <div className="graph-legend">
        <span><i /> 直接依赖</span>
        {tasks.some((task) => task.onCriticalPath) && <span><b className="graph-critical">关键</b> 排期标记的关键路径任务</span>}
      </div>
    </div>
  )
}
