import { expect, it } from 'vitest'
import { fixture } from '../mocks/scenario'
import { layoutDependencies } from './dependencyLayout'

it('按 fixture 的直接前置绘制 T1 → T2/T3 → T4 的分叉与汇合', () => {
  const tasks = [...fixture.plan_response.plan.tasks].reverse().map(({ id, depends_on }) => ({ id, dependencies: depends_on }))
  const { positions, edges } = layoutDependencies(tasks)
  expect(edges.map(({ source, target }) => [source, target])).toHaveLength(4)
  expect(edges.map(({ source, target }) => [source, target])).toEqual(expect.arrayContaining([
    ['T1', 'T2'], ['T1', 'T3'], ['T2', 'T4'], ['T3', 'T4'],
  ]))
  const [t1, t2, t3, t4] = ['T1', 'T2', 'T3', 'T4'].map((id) => positions.get(id)!)
  expect(t1.x).toBeLessThan(t2.x)
  expect(t2.x).toBe(t3.x)
  expect(t2.y).not.toBe(t3.y)
  expect(t4.x).toBeGreaterThan(t3.x)
})

it('串行节点只画直接依赖，忽略不存在的图节点', () => {
  const { positions, edges } = layoutDependencies([
    { id: 'A', dependencies: [] },
    { id: 'B', dependencies: ['A'] },
    { id: 'C', dependencies: ['B', 'missing'] },
  ])
  expect(edges.map(({ source, target }) => [source, target])).toEqual([['A', 'B'], ['B', 'C']])
  expect(positions.get('A')!.x).toBeLessThan(positions.get('B')!.x)
  expect(positions.get('B')!.x).toBeLessThan(positions.get('C')!.x)
  expect(positions.has('missing')).toBe(false)
})
