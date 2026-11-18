import dagre from '@dagrejs/dagre'
import type { TaskPreview } from '../types/workbench'

export const taskNodeSize = { width: 180, height: 108 }

// The plan already describes the DAG. Dagre only positions its nodes on the canvas.
export function layoutDependencies(tasks: readonly Pick<TaskPreview, 'id' | 'dependencies'>[]) {
  const graph = new dagre.graphlib.Graph()
    .setDefaultEdgeLabel(() => ({}))
    .setGraph({ rankdir: 'LR', ranksep: 60, nodesep: 36 })
  for (const { id } of tasks) graph.setNode(id, { ...taskNodeSize })

  const edges = tasks.flatMap(({ id, dependencies }) =>
    [...new Set(dependencies)].filter((source) => graph.hasNode(source)).map((source) => {
      graph.setEdge(source, id)
      return { id: JSON.stringify([source, id]), source, target: id }
    }),
  )

  dagre.layout(graph)
  const positions = new Map(tasks.map(({ id }) => {
    const { x, y } = graph.node(id)
    return [id, { x: x - taskNodeSize.width / 2, y: y - taskNodeSize.height / 2 }]
  }))
  return { positions, edges }
}
