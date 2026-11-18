import type { VersionRef } from '../types/v2'
import type { StageStatus, TaskPreview } from '../types/workbench'

export const stageStatusText: Record<StageStatus, string> = {
  complete: '已完成',
  active: '进行中',
  waiting: '等待前置',
}

export const taskStatusText: Record<TaskPreview['status'], string> = {
  waiting: '等待前置',
  ready: '就绪',
  running: '执行中',
  review: '待人工审核',
  failed: '失败',
  input_required: '待补充输入',
  blocked: '受阻',
  done: '已完成',
  unstarted: '未运行',
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return '未估时'
  const minutes = Math.floor(seconds / 60)
  const remainder = Number((seconds % 60).toFixed(3))
  if (minutes === 0) return `${remainder} 秒`
  return remainder === 0 ? `${minutes} 分钟` : `${minutes} 分 ${remainder} 秒`
}

export function formatVersion(ref: VersionRef) {
  return `${ref.id}@v${ref.version}`
}
