import type { Artifact } from '../types/v2'

export function pendingArtifacts(artifacts: readonly Artifact[]) {
  const latest = new Map<string, Artifact>()
  for (const artifact of artifacts) {
    if ((latest.get(artifact.output_id)?.version ?? 0) < artifact.version) latest.set(artifact.output_id, artifact)
  }
  return [...latest.values()].filter(({ status }) => status === 'draft')
}

export const validCommit = (value: string) => /^[a-f0-9]{40}$/i.test(value)
