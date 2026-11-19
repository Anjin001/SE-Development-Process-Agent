-- Persistence for the two modules this member owns (docs/接口契约-v2.md §3-§5):
--   * orchestration: plans, runs, attempts, artifacts, events, idempotency
--   * digital-human memory: agents, cases, experiences, checkpoints
-- One database, one project record. The memory side is schema-only in this
-- slice; its service lands next. The memory design doc calls its generic
-- record table "artifacts"; here the typed records (drafts, evaluations,
-- specifications) take that role, so "artifacts" stays unique to deliverables.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id          TEXT PRIMARY KEY,
  stage       TEXT NOT NULL DEFAULT 'intake',   -- intake/race/specification/planning/execution/completed
  created_at  TEXT NOT NULL
);

-- ---------- requirement, race, specification ----------

CREATE TABLE IF NOT EXISTS requirements (
  project_id  TEXT NOT NULL REFERENCES projects(id),
  version     INTEGER NOT NULL CHECK (version >= 1),
  markdown    TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (project_id, version)
);

CREATE TABLE IF NOT EXISTS requirement_items (
  project_id         TEXT NOT NULL,
  requirement_version INTEGER NOT NULL,
  id                 TEXT NOT NULL,
  title              TEXT NOT NULL,
  text               TEXT NOT NULL,
  section            TEXT,
  start_line         INTEGER,
  end_line           INTEGER,
  PRIMARY KEY (project_id, requirement_version, id),
  FOREIGN KEY (project_id, requirement_version)
    REFERENCES requirements(project_id, version)
);

CREATE TABLE IF NOT EXISTS drafts (
  project_id          TEXT NOT NULL,
  requirement_version INTEGER NOT NULL,
  id                  TEXT NOT NULL,            -- A / B
  title               TEXT NOT NULL,
  summary             TEXT NOT NULL,
  PRIMARY KEY (project_id, requirement_version, id),
  FOREIGN KEY (project_id, requirement_version)
    REFERENCES requirements(project_id, version)
);

CREATE TABLE IF NOT EXISTS evaluations (
  project_id          TEXT NOT NULL,
  requirement_version INTEGER NOT NULL,
  draft_id            TEXT NOT NULL,
  criteria            TEXT NOT NULL,            -- [{criterion,score,reason,claim_ids}]
  PRIMARY KEY (project_id, requirement_version, draft_id),
  FOREIGN KEY (project_id, requirement_version, draft_id)
    REFERENCES drafts(project_id, requirement_version, id)
);

CREATE TABLE IF NOT EXISTS specifications (
  project_id          TEXT NOT NULL,
  version             INTEGER NOT NULL CHECK (version >= 1),
  id                  TEXT NOT NULL,
  status              TEXT NOT NULL,            -- draft / confirmed
  requirement_version INTEGER,
  selection           TEXT,                     -- A / B / merge
  markdown            TEXT NOT NULL,
  confirmed_by        TEXT,
  confirmed_at        TEXT,
  PRIMARY KEY (project_id, version)
);

CREATE TABLE IF NOT EXISTS spec_decisions (
  project_id   TEXT NOT NULL,
  spec_version INTEGER NOT NULL,
  id           TEXT NOT NULL,                   -- DEC-01
  scope        TEXT,
  choice       TEXT NOT NULL,
  reason       TEXT,
  requirement_ids TEXT NOT NULL DEFAULT '[]',
  draft_ids    TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (project_id, spec_version, id),
  FOREIGN KEY (project_id, spec_version) REFERENCES specifications(project_id, version)
);

CREATE TABLE IF NOT EXISTS spec_open_items (
  project_id   TEXT NOT NULL,
  spec_version INTEGER NOT NULL,
  id           TEXT NOT NULL,                   -- OPEN-01
  question     TEXT NOT NULL,
  blocking     INTEGER NOT NULL DEFAULT 1,
  requirement_ids TEXT NOT NULL DEFAULT '[]',
  resolution   TEXT,
  PRIMARY KEY (project_id, spec_version, id),
  FOREIGN KEY (project_id, spec_version) REFERENCES specifications(project_id, version)
);

-- ---------- plan ----------

CREATE TABLE IF NOT EXISTS plans (
  project_id   TEXT NOT NULL REFERENCES projects(id),
  id           TEXT NOT NULL,                   -- PLAN-01
  version      INTEGER NOT NULL CHECK (version >= 1),
  spec_id      TEXT NOT NULL,
  spec_version INTEGER NOT NULL,
  base_commit  TEXT,
  max_parallel INTEGER NOT NULL CHECK (max_parallel >= 1),
  created_at   TEXT NOT NULL,
  PRIMARY KEY (project_id, id, version)
);

CREATE TABLE IF NOT EXISTS plan_tasks (
  project_id   TEXT NOT NULL,
  plan_id      TEXT NOT NULL,
  plan_version INTEGER NOT NULL,
  position     INTEGER NOT NULL,
  id           TEXT NOT NULL,
  title        TEXT NOT NULL,
  phase        TEXT NOT NULL,
  role         TEXT NOT NULL,
  depends_on   TEXT NOT NULL DEFAULT '[]',
  write_paths  TEXT NOT NULL DEFAULT '[]',
  outputs      TEXT NOT NULL DEFAULT '[]',
  requirement_ids TEXT NOT NULL DEFAULT '[]',
  decision_ids    TEXT NOT NULL DEFAULT '[]',
  open_item_ids   TEXT NOT NULL DEFAULT '[]',
  estimate_seconds     INTEGER,
  review_required      INTEGER NOT NULL DEFAULT 0,
  completion_condition TEXT NOT NULL,
  PRIMARY KEY (project_id, plan_id, plan_version, id),
  FOREIGN KEY (project_id, plan_id, plan_version)
    REFERENCES plans(project_id, id, version)
);

-- ---------- run ----------

CREATE TABLE IF NOT EXISTS runs (
  id               TEXT PRIMARY KEY,            -- RUN-01
  project_id       TEXT NOT NULL REFERENCES projects(id),
  plan_id          TEXT NOT NULL,
  plan_version     INTEGER NOT NULL,
  mode             TEXT NOT NULL DEFAULT 'manual',   -- auto / manual
  dispatch         TEXT NOT NULL DEFAULT 'active',   -- active / paused
  state            TEXT NOT NULL DEFAULT 'active',   -- active / completed / stopped
  revision         INTEGER NOT NULL DEFAULT 1,
  started_at       TEXT NOT NULL,
  ended_at         TEXT,
  latest_event_seq INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (project_id, plan_id, plan_version)
    REFERENCES plans(project_id, id, version)
);

-- one active run per project
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active
  ON runs(project_id) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS run_tasks (
  run_id           TEXT NOT NULL REFERENCES runs(id),
  task_id          TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'waiting',
  blockers         TEXT NOT NULL DEFAULT '[]',
  missing_input_ids TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (run_id, task_id)
);

-- manual mode fixes a batch into this queue; claim consumes it
CREATE TABLE IF NOT EXISTS dispatch_queue (
  run_id   TEXT NOT NULL REFERENCES runs(id),
  task_id  TEXT NOT NULL,
  position INTEGER NOT NULL,
  PRIMARY KEY (run_id, task_id)
);

CREATE TABLE IF NOT EXISTS attempts (
  id          TEXT PRIMARY KEY,                 -- T2.1
  run_id      TEXT NOT NULL REFERENCES runs(id),
  task_id     TEXT NOT NULL,
  number      INTEGER NOT NULL CHECK (number >= 1),
  state       TEXT NOT NULL,                    -- running/succeeded/failed/input_required
  worker_id   TEXT,
  lease_until TEXT,
  created_at  TEXT NOT NULL,
  started_at  TEXT,
  ended_at    TEXT,
  error       TEXT,
  input_refs  TEXT NOT NULL DEFAULT '[]',
  report_hash TEXT,
  provider    TEXT,
  model       TEXT,
  usage       TEXT,
  checkout_commit TEXT,
  UNIQUE (run_id, task_id, number),
  FOREIGN KEY (run_id, task_id) REFERENCES run_tasks(run_id, task_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id             TEXT NOT NULL,                 -- ART-T2-OUT-01, stable across versions
  run_id         TEXT NOT NULL REFERENCES runs(id),
  task_id        TEXT NOT NULL,
  attempt_id     TEXT,
  output_id      TEXT NOT NULL,
  version        INTEGER NOT NULL CHECK (version >= 1),
  based_on_version INTEGER,
  kind           TEXT NOT NULL,                 -- git_commit / text
  origin         TEXT NOT NULL,                 -- agent / human
  status         TEXT NOT NULL,                 -- draft / approved / rejected / superseded
  commit_sha     TEXT,
  changed_paths  TEXT NOT NULL DEFAULT '[]',
  content        TEXT,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (id, version),
  UNIQUE (run_id, task_id, output_id, version)
);

-- text supplied for a blocking open item, per task
CREATE TABLE IF NOT EXISTS input_submissions (
  run_id       TEXT NOT NULL REFERENCES runs(id),
  task_id      TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  value        TEXT NOT NULL,
  submitted_by TEXT,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (run_id, task_id, item_id)
);

-- supervisor prompts, kept apart from run state as the contract asks
CREATE TABLE IF NOT EXISTS findings (
  run_id     TEXT NOT NULL REFERENCES runs(id),
  code       TEXT NOT NULL,
  ref_id     TEXT NOT NULL,
  stage      TEXT,
  severity   TEXT NOT NULL,                     -- info / warning / error
  summary    TEXT NOT NULL,
  evidence_refs     TEXT NOT NULL DEFAULT '[]',
  affected_task_ids TEXT NOT NULL DEFAULT '[]',
  suggested_action  TEXT,
  PRIMARY KEY (run_id, code, ref_id)
);

CREATE TABLE IF NOT EXISTS review_records (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            TEXT NOT NULL REFERENCES runs(id),
  task_id           TEXT NOT NULL,
  decision          TEXT NOT NULL,              -- approve / revise / reject
  expected_versions TEXT NOT NULL DEFAULT '{}',
  approved_versions TEXT NOT NULL DEFAULT '{}',
  note              TEXT NOT NULL DEFAULT '',
  actor_id          TEXT NOT NULL,
  created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  run_id     TEXT NOT NULL REFERENCES runs(id),
  seq        INTEGER NOT NULL,
  type       TEXT NOT NULL,
  task_id    TEXT,
  attempt_id TEXT,
  created_at TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS idempotency (
  scope        TEXT NOT NULL,                   -- project id, or project collection
  method       TEXT NOT NULL,
  path         TEXT NOT NULL,
  actor_id     TEXT NOT NULL,
  key          TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status       INTEGER NOT NULL,
  body         TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (scope, method, path, actor_id, key)
);

-- ---------- digital-human memory (schema only in this slice) ----------

CREATE TABLE IF NOT EXISTS agents (
  agent_id   TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  duty       TEXT,
  domain_tags TEXT NOT NULL DEFAULT '[]',
  focus      TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS project_members (
  project_id TEXT NOT NULL REFERENCES projects(id),
  agent_id   TEXT NOT NULL REFERENCES agents(agent_id),
  group_id   TEXT,
  duty       TEXT,
  phases     TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (project_id, agent_id)
);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id      TEXT NOT NULL REFERENCES projects(id),
  conversation_id TEXT NOT NULL,
  sender          TEXT NOT NULL,
  phase           TEXT,
  body            TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
  project_id  TEXT NOT NULL REFERENCES projects(id),
  id          TEXT NOT NULL,
  title       TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'open',
  spec_version INTEGER,
  resolution  TEXT,
  PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS project_cases (
  case_id      TEXT PRIMARY KEY,
  source_project TEXT,
  case_type    TEXT NOT NULL,                   -- real / classroom / external
  constraints  TEXT,
  tradeoffs    TEXT,
  reached_stage TEXT,
  result_state TEXT
);

CREATE TABLE IF NOT EXISTS experiences (
  experience_id TEXT NOT NULL,
  version       INTEGER NOT NULL DEFAULT 1,
  domain        TEXT NOT NULL,
  symptom       TEXT NOT NULL,
  trigger       TEXT NOT NULL,
  check_stage   TEXT,
  correction    TEXT NOT NULL,
  pass_condition TEXT NOT NULL,
  scope         TEXT,
  exceptions    TEXT,
  publish_state TEXT NOT NULL DEFAULT 'candidate',  -- candidate/published/review/retired
  evidence_state TEXT NOT NULL DEFAULT 'pending',   -- pending/simulated/walkthrough/pilot/production
  PRIMARY KEY (experience_id, version)
);

CREATE TABLE IF NOT EXISTS experience_tags (
  experience_id TEXT NOT NULL,
  version       INTEGER NOT NULL,
  tag_type      TEXT NOT NULL,
  tag           TEXT NOT NULL,
  PRIMARY KEY (experience_id, version, tag_type, tag),
  FOREIGN KEY (experience_id, version) REFERENCES experiences(experience_id, version)
);

CREATE TABLE IF NOT EXISTS tag_aliases (
  domain   TEXT NOT NULL,
  tag_type TEXT NOT NULL,
  alias    TEXT NOT NULL,
  tag      TEXT NOT NULL,
  PRIMARY KEY (domain, tag_type, alias)
);

CREATE TABLE IF NOT EXISTS experience_keywords (
  experience_id TEXT NOT NULL,
  version       INTEGER NOT NULL,
  phrase        TEXT NOT NULL,
  PRIMARY KEY (experience_id, version, phrase),
  FOREIGN KEY (experience_id, version) REFERENCES experiences(experience_id, version)
);

CREATE TABLE IF NOT EXISTS memory_sources (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  experience_id TEXT NOT NULL,
  version       INTEGER NOT NULL,
  source_id     TEXT NOT NULL,
  source_version INTEGER,
  source_project TEXT,
  stance        TEXT NOT NULL DEFAULT 'support',   -- support / counter
  source_access TEXT,
  FOREIGN KEY (experience_id, version) REFERENCES experiences(experience_id, version)
);

CREATE TABLE IF NOT EXISTS checkpoints (
  run_id     TEXT NOT NULL REFERENCES runs(id),
  phase      TEXT NOT NULL,
  progress   TEXT NOT NULL DEFAULT '{}',
  pending    TEXT NOT NULL DEFAULT '[]',
  conversations TEXT NOT NULL DEFAULT '{}',
  memory_versions TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, phase)
);

CREATE TABLE IF NOT EXISTS memory_usage (
  run_id        TEXT NOT NULL,
  agent_id      TEXT NOT NULL,
  experience_id TEXT NOT NULL,
  version       INTEGER NOT NULL,
  decision      TEXT,                            -- adopted / declined
  reason        TEXT,
  verify_project TEXT,
  expected      TEXT,
  observed      TEXT,
  evidence      TEXT,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (run_id, agent_id, experience_id, version)
);

PRAGMA user_version = 1;
