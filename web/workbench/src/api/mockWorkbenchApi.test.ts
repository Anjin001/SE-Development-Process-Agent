import { expect, it } from 'vitest'
import { fixture } from '../mocks/scenario'
import { createMockWorkbenchApi } from './mockWorkbenchApi'

it('新建需求使用版本 1，拒绝旧版本提交及过早确认', async () => {
  const example = fixture.project_snapshot.requirement
  const api = createMockWorkbenchApi()
  const actions = api.projectActions!
  const { projectData: first } = await actions.createProject(example.markdown)
  expect(first.requirement).toEqual(example)
  expect(first.stage).toBe('intake')
  await expect(actions.saveRequirement(first.id, { expected_version: 0, markdown: example.markdown }))
    .rejects.toMatchObject({ code: 'VERSION_CONFLICT' })
  await expect(actions.saveSpecification(first.id, { requirement_version: 1, selection: 'A', expected_version: 0 }))
    .rejects.toMatchObject({ code: 'INVALID_STATE' })
})

it.each(['A', 'B'] as const)('使用 fixture 的方案 %s 作为待确认规格', async (selection) => {
  let clock = 0
  const api = createMockWorkbenchApi({ now: () => clock, failFirstB: false })
  const actions = api.projectActions!
  const { projectData: first } = await actions.createProject(fixture.project_snapshot.requirement.markdown)
  await actions.startRace(first.id, 1)
  clock = 2000
  await api.getSnapshot(first.id)
  const { projectData: draft } = await actions.saveSpecification(first.id, { requirement_version: 1, expected_version: 0, selection })
  const plan = fixture.project_snapshot.drafts.find(({ id }) => id === selection)!
  expect(draft.specification).toMatchObject({ selection, status: 'draft', version: 1, confirmed_at: null })
  expect(draft.specification?.markdown).toContain(plan.title)
  await expect(actions.confirmSpecification(first.id, { id: draft.specification!.id, version: 0 }))
    .rejects.toMatchObject({ code: 'VERSION_CONFLICT' })
})

it('保存自定义 Markdown 时不附带不相关的 fixture 需求或赛马结果', async () => {
  let clock = 0
  const api = createMockWorkbenchApi({ now: () => clock })
  const actions = api.projectActions!
  const markdown = '# 自定义需求\n另一项需求'
  const { projectData: custom } = await actions.createProject(markdown)
  expect(custom.requirement).toMatchObject({ markdown, items: [] })
  await actions.startRace(custom.id, 1)
  clock = 2000
  const { projectData: refreshed } = await api.getSnapshot(custom.id)
  expect(refreshed.drafts).toEqual([])
  expect(refreshed.evaluations).toEqual([])
  expect(refreshed.race?.groups.map(({ error }) => error?.code)).toEqual(['MOCK_SAMPLE_ONLY', 'MOCK_SAMPLE_ONLY'])
})
