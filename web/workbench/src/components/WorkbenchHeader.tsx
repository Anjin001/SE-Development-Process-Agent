import { ArrowRight, Check, FlaskConical, Route, Workflow } from 'lucide-react'
import type { StageId, WorkbenchSnapshot } from '../types/workbench'
import { stageStatusText } from './presentation'

export function WorkbenchHeader({ snapshot, viewedStage, demo, onStageChange }: {
  snapshot: WorkbenchSnapshot
  viewedStage: StageId
  demo: boolean
  onStageChange: (id: StageId) => void
}) {
  const { project, stages, activeStage } = snapshot
  const activeIndex = stages.findIndex((stage) => stage.id === activeStage)

  return (
    <>
      <header className="masthead">
        <div className="masthead-brand">
          <span className="brand-icon"><Workflow size={21} aria-hidden="true" /></span>
          <div>
            <p>SE DEVELOPMENT PROCESS AGENT</p>
            <strong>开发流程工作台</strong>
          </div>
        </div>
        {demo && <span className="demo-label"><FlaskConical size={14} aria-hidden="true" /> 模拟演示</span>}
      </header>
      <div className="project-bar">
        <div>
          <p className="breadcrumb">项目 <ArrowRight size={12} aria-hidden="true" /> <span>{project.reference}</span></p>
          <h1>{project.name}</h1>
        </div>
        <span className="project-progress"><Route size={16} aria-hidden="true" /> 第 {activeIndex + 1} / {stages.length} 阶段</span>
      </div>
      <nav className="stages" aria-label="项目阶段">
        <ol>
          {stages.map((stage, index) => (
            <li key={stage.id}>
              <button
                type="button"
                className={`stage-button stage-${stage.status}`}
                aria-current={stage.id === viewedStage ? 'step' : undefined}
                onClick={() => onStageChange(stage.id)}
              >
                <span className="stage-number">{stage.status === 'complete' ? <Check size={14} aria-hidden="true" /> : String(index + 1).padStart(2, '0')}</span>
                <span><strong>{stage.label}</strong><small>{stageStatusText[stage.status]}</small></span>
              </button>
            </li>
          ))}
        </ol>
      </nav>
    </>
  )
}
