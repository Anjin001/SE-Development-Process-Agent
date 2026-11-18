import type { GroupId, ProjectSnapshot, VersionRef } from '../types/v2'

export const groupIds: readonly GroupId[] = ['A', 'B']

export function sameVersion(left: VersionRef | null, right: VersionRef | null) {
  return left !== null && right !== null && left.id === right.id && left.version === right.version
}

export function currentRace(project: ProjectSnapshot) {
  return project.race && sameVersion(project.race.requirement_ref, project.requirement) ? project.race : null
}

export function currentSpecification(project: ProjectSnapshot) {
  const specification = project.specification
  return specification && sameVersion(specification.requirement_ref, project.requirement) ? specification : null
}

export function completedGroup(project: ProjectSnapshot, id: GroupId) {
  const group = currentRace(project)?.groups.find((item) => item.group_id === id)
  if (group?.state !== 'completed') return null
  const draft = project.drafts.find((item) => item.id === id && sameVersion(item.requirement_ref, project.requirement))
  const evaluation = project.evaluations.find((item) => item.draft_id === id)
  if (!draft || !evaluation) return null
  const scored = ['value', 'feasibility', 'cost'].every((criterion) => evaluation.criteria.some((item) => item.criterion === criterion))
  return scored ? { draft, evaluation } : null
}

export function raceIsComplete(project: ProjectSnapshot) {
  return groupIds.every((id) => completedGroup(project, id) !== null)
}
