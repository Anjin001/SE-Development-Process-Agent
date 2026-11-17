"""Digital-human memory: experience cards, retrieval and usage records.

Implements the parts of 领域数字人记忆系统设计 that the orchestration side
consumes: a card is published against case sources, retrieved by program rules
(object / operation / state, plus free text) and any use is recorded so a later
review can trace which version was borrowed.

Access follows the design's stage table: during independent ideation only the
published baseline is readable; a candidate card is visible inside the project
that produced it and, once published, to everyone. Retrieval never returns a
retired version.
"""
import json

from .contracts import InvalidReference

EVIDENCE_RANK = {"pending": 1, "simulated": 2, "walkthrough": 3,
                 "pilot": 4, "production": 5}
BASELINE_ONLY_STAGES = ("intake", "race")
TAG_TYPES = (("object", "objects"), ("operation", "operations"),
             ("state", "states"), ("missing", "missing"))
RETIRED = "retired"


class Memory:
    def __init__(self, store):
        self.store = store
        self.db = store.db

    # ---------- writing ----------

    def register_agent(self, agent_id, name, duty="", domain_tags=(), focus=()):
        with self.store.tx() as db:
            db.execute(
                "INSERT INTO agents(agent_id, name, duty, domain_tags, focus)"
                " VALUES (?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET"
                " name=excluded.name, duty=excluded.duty,"
                " domain_tags=excluded.domain_tags, focus=excluded.focus",
                (agent_id, name, duty, json.dumps(list(domain_tags), ensure_ascii=False),
                 json.dumps(list(focus), ensure_ascii=False)))
        return {"agent_id": agent_id, "name": name}

    def join_project(self, project_id, agent_id, group_id=None, duty="", phases=()):
        with self.store.tx() as db:
            db.execute("INSERT INTO project_members(project_id, agent_id, group_id, duty,"
                       " phases) VALUES (?,?,?,?,?)", (project_id, agent_id, group_id, duty,
                                                       json.dumps(list(phases))))

    def add_case(self, case):
        for field in ("case_id", "case_type"):
            if not str(case.get(field, "")).strip():
                raise InvalidReference("案例缺少必要字段", field=field)
        with self.store.tx() as db:
            db.execute(
                "INSERT INTO project_cases(case_id, source_project, case_type, constraints,"
                " tradeoffs, reached_stage, result_state) VALUES (?,?,?,?,?,?,?)",
                (case["case_id"], case.get("source_project"), case["case_type"],
                 case.get("constraints"), case.get("tradeoffs"),
                 case.get("reached_stage"), case.get("result_state")))
        return case["case_id"]

    def publish_experience(self, card, origin_project=None, sources=()):
        """Store a new version of a card; the previous version stays readable."""
        for field in ("domain", "symptom", "trigger", "correction", "pass_condition"):
            if not str(card.get(field, "")).strip():
                raise InvalidReference("经验卡缺少必要字段", field=field)
        state = card.get("publish_state", "candidate")
        if state not in ("candidate", "published", "review", "retired"):
            raise InvalidReference("发布状态无效", field="publish_state", value=state)
        evidence = card.get("evidence_state", "pending")
        if evidence not in EVIDENCE_RANK:
            raise InvalidReference("证据级别无效", field="evidence_state", value=evidence)

        experience_id = card.get("experience_id") or self._next_experience_id()
        version = self._next_version(experience_id)
        with self.store.tx() as db:
            db.execute(
                "INSERT INTO experiences(experience_id, version, domain, symptom, trigger,"
                " check_stage, correction, pass_condition, scope, exceptions,"
                " publish_state, evidence_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (experience_id, version, card["domain"], card["symptom"], card["trigger"],
                 card.get("check_stage"), card["correction"], card["pass_condition"],
                 card.get("scope"), card.get("exceptions"), state, evidence))
            for tag_type, key in TAG_TYPES:
                for tag in card.get(key, []):
                    db.execute("INSERT INTO experience_tags(experience_id, version, tag_type,"
                               " tag) VALUES (?,?,?,?)", (experience_id, version, tag_type, tag))
            for phrase in card.get("keywords", []):
                db.execute("INSERT INTO experience_keywords(experience_id, version, phrase)"
                           " VALUES (?,?,?)", (experience_id, version, phrase))
            for source in sources:
                db.execute(
                    "INSERT INTO memory_sources(experience_id, version, source_id,"
                    " source_version, source_project, stance, source_access)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (experience_id, version, source["id"], source.get("version"),
                     source.get("project", origin_project), source.get("stance", "support"),
                     source.get("access")))
        return {"experience_id": experience_id, "version": version}

    def alias(self, domain, tag_type, alias, tag):
        """Register a synonym so later searches normalise to ``tag``."""
        with self.store.tx() as db:
            db.execute("INSERT INTO tag_aliases(domain, tag_type, alias, tag) VALUES (?,?,?,?)"
                       " ON CONFLICT(domain, tag_type, alias) DO UPDATE SET tag=excluded.tag",
                       (domain, tag_type, alias.strip().lower(), tag))

    # ---------- reading ----------

    def history(self, experience_id):
        rows = self.db.execute("SELECT * FROM experiences WHERE experience_id=?"
                               " ORDER BY version", (experience_id,)).fetchall()
        if not rows:
            raise InvalidReference("经验卡不存在", experience_id=experience_id)
        return [self._card(row) for row in rows]

    def search(self, project_id, objects=(), operations=(), states=(), text="",
               domain=None, stage=None, limit=5, include_retired=False):
        """Return the best-matching current versions the caller may read."""
        wanted = {"object": self._normalise(domain, "object", objects),
                  "operation": self._normalise(domain, "operation", operations),
                  "state": self._normalise(domain, "state", states)}
        scored = []
        for row in self._current_versions(include_retired):
            if not self._readable(row, project_id, stage):
                continue
            tags = self._tags(row["experience_id"], row["version"])
            hits, score, matched_all = [], 0, True
            for tag_type in ("object", "operation", "state"):
                if not wanted[tag_type]:
                    continue
                common = wanted[tag_type] & tags.get(tag_type, set())
                if common:
                    hits.append({"type": tag_type, "values": sorted(common)})
                    score += 2
                else:
                    matched_all = False
            if not any(wanted.values()):
                matched_all = False
            for phrase in self._phrases(row["experience_id"], row["version"]):
                if phrase and text and phrase in text:
                    hits.append({"type": "keyword", "values": [phrase]})
                    score += 1
            if hits and matched_all:
                score += 2                      # the whole trigger fired
            if score <= 0:
                continue
            scored.append((score, EVIDENCE_RANK.get(row["evidence_state"], 0),
                           -row["version"], row, hits, matched_all))
        scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]["experience_id"]),
                    reverse=True)
        results = []
        for score, evidence, _, row, hits, matched_all in scored[:limit]:
            card = self._card(row)
            card.update({"score": score, "matched": hits,
                         "trigger_matched": matched_all,
                         "evidence_rank": evidence})
            results.append(card)
        return results

    def record_usage(self, run_id, agent_id, experience_id, version, decision,
                     reason="", verify_project=None, expected=None, observed=None,
                     evidence=None):
        if decision not in ("adopted", "declined"):
            raise InvalidReference("使用决定无效", field="decision")
        with self.store.tx() as db:
            db.execute(
                "INSERT INTO memory_usage(run_id, agent_id, experience_id, version, decision,"
                " reason, verify_project, expected, observed, evidence, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id, agent_id,"
                " experience_id, version) DO UPDATE SET decision=excluded.decision,"
                " reason=excluded.reason, expected=excluded.expected,"
                " observed=excluded.observed, evidence=excluded.evidence",
                (run_id, agent_id, experience_id, version, decision, reason,
                 verify_project, expected, observed, evidence,
                 self.store.now().isoformat()))
        return {"experience_id": experience_id, "version": version, "decision": decision}

    def usages(self, experience_id=None, version=None):
        clause, params = "", ()
        if experience_id is not None:
            clause, params = " WHERE experience_id=?", (experience_id,)
            if version is not None:
                clause, params = " WHERE experience_id=? AND version=?", (experience_id, version)
        rows = self.db.execute("SELECT * FROM memory_usage" + clause +
                               " ORDER BY created_at", params).fetchall()
        return [dict(row) for row in rows]

    # ---------- internals ----------

    def _next_experience_id(self):
        row = self.db.execute("SELECT COUNT(DISTINCT experience_id) AS n FROM experiences"
                              ).fetchone()
        return "EXP-%03d" % (row["n"] + 1)

    def _next_version(self, experience_id):
        row = self.db.execute("SELECT COALESCE(MAX(version), 0) AS v FROM experiences"
                              " WHERE experience_id=?", (experience_id,)).fetchone()
        return row["v"] + 1

    def _current_versions(self, include_retired):
        rows = self.db.execute(
            "SELECT e.* FROM experiences e JOIN (SELECT experience_id,"
            " MAX(version) AS v FROM experiences GROUP BY experience_id) latest"
            " ON e.experience_id=latest.experience_id AND e.version=latest.v").fetchall()
        return [row for row in rows if include_retired or row["publish_state"] != RETIRED]

    def _readable(self, row, project_id, stage):
        if row["publish_state"] == "published":
            return True
        if stage in BASELINE_ONLY_STAGES:
            return False
        origin = self.db.execute(
            "SELECT 1 FROM memory_sources WHERE experience_id=? AND version=?"
            " AND source_project=? LIMIT 1",
            (row["experience_id"], row["version"], project_id)).fetchone()
        return origin is not None

    def _normalise(self, domain, tag_type, values):
        wanted = set()
        for value in values:
            if not value:
                continue
            key = str(value).strip().lower()
            alias = self.db.execute(
                "SELECT tag FROM tag_aliases WHERE tag_type=? AND alias=?"
                " AND (? IS NULL OR domain=?) LIMIT 1",
                (tag_type, key, domain, domain)).fetchone()
            wanted.add(alias["tag"] if alias else key)
        return wanted

    def _tags(self, experience_id, version):
        rows = self.db.execute("SELECT tag_type, tag FROM experience_tags"
                               " WHERE experience_id=? AND version=?",
                               (experience_id, version)).fetchall()
        tags = {"object": set(), "operation": set(), "state": set(), "missing": set()}
        for row in rows:
            tags.setdefault(row["tag_type"], set()).add(row["tag"].lower())
        return tags

    def _phrases(self, experience_id, version):
        rows = self.db.execute("SELECT phrase FROM experience_keywords"
                               " WHERE experience_id=? AND version=?",
                               (experience_id, version)).fetchall()
        return [row["phrase"] for row in rows]

    def _card(self, row):
        return {"experience_id": row["experience_id"], "version": row["version"],
                "domain": row["domain"], "symptom": row["symptom"],
                "trigger": row["trigger"], "check_stage": row["check_stage"],
                "correction": row["correction"], "pass_condition": row["pass_condition"],
                "scope": row["scope"], "exceptions": row["exceptions"],
                "publish_state": row["publish_state"],
                "evidence_state": row["evidence_state"]}