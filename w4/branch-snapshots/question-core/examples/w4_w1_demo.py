"""One synthetic Run in SQLite, for acceptance tests; not W1's PostgreSQL adapter."""

from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from uuid import UUID, uuid4, uuid5

from epick_w4.synthetic_policy import content_hash
from epick_w4.w1_bridge import BridgeError, EpisodeVersionBinding, RunBinding
from examples.w4_c01_demo import build_context


def fixture():
    owner, run, project, question, question_version, snapshot = [uuid4() for _ in range(6)]
    context = build_context()
    context["project"] = {"owner_id": str(owner), "project_id": str(project)}
    context["snapshot"]["snapshot_id"] = str(snapshot)
    context["context_version"] = "w1-synthetic-run-" + str(run)
    for episode in context["episodes"]:
        episode["owner_id"] = str(owner)
    request = {"schema_version": "w4-service-input/0.1", "request_id": str(run),
        "project_id": str(project), "question": {"scope_id": context["question_scope_id"], "question_id": "expertise"},
        "top_k": 3}
    binding = RunBinding(owner, run, project, question, question_version, snapshot, uuid4(),
        content_hash(context), request,
        tuple(EpisodeVersionBinding(e["episode_id"], e["version"], uuid4()) for e in context["episodes"]))
    return binding, context


class SyntheticRunStore:
    """Short SQLite transactions demonstrate CAS publication and later revocation.

    W1 must implement the same guarantees across its own Run/Candidate/result and
    Source-epoch tables. This isolated fixture deliberately does not touch W1 DBs.
    """
    def __init__(self, path, binding, context, *, check_sources):
        self.binding = deepcopy(binding)
        self.check_sources = check_sources
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE run (status TEXT, lease TEXT, context TEXT, allowed INTEGER, publication TEXT)")
        self.db.execute("INSERT INTO run VALUES ('PENDING',NULL,?,1,NULL)",
                        (json.dumps(context, ensure_ascii=False),))
        self.db.commit()

    def close(self):
        self.db.close()

    def acquire(self, *, owner_user_id, run_id):
        if (owner_user_id, run_id) != (self.binding.owner_user_id, self.binding.run_id):
            raise BridgeError("W1_RUN_NOT_FOUND")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            status, allowed = self.db.execute("SELECT status,allowed FROM run").fetchone()
            if not allowed:
                raise BridgeError("W1_RUN_NOT_FOUND")
            if status == "LIMITED":
                return None
            if status != "PENDING":
                raise BridgeError("W1_RUN_NOT_PENDING")
            lease = uuid4()
            self.db.execute("UPDATE run SET status='RUNNING',lease=?", (str(lease),))
        self.binding = replace(self.binding, lease_token=lease)
        return deepcopy(self.binding)

    def load_context(self, binding):
        if binding != self.binding:
            return None
        row = self.db.execute("SELECT context FROM run WHERE status='RUNNING' AND lease=? AND allowed=1",
                              (str(binding.lease_token),)).fetchone()
        return json.loads(row[0]) if row else None

    def authorize(self, binding, **action):
        context = self.load_context(binding)
        return bool(context and action["context_version"] == context["context_version"]
                    and action["user_id"] == str(binding.owner_user_id)
                    and action["project_id"] == str(binding.project_id))

    def publish(self, binding, publication):
        if binding != self.binding:
            return False
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            context = self.load_context(binding)
            if (context is None or content_hash(context) != binding.context_sha256
                    or not self.sources_current(context)):
                return False
            self.db.execute("UPDATE run SET publication=?,status='LIMITED' WHERE lease=? AND status='RUNNING'",
                (json.dumps(publication, ensure_ascii=False), str(binding.lease_token)))
        return True

    def fail(self, binding, code):
        with self.db:
            self.db.execute("UPDATE run SET status='FAILED' WHERE lease=? AND status='RUNNING'",
                            (str(binding.lease_token),))

    def read_result(self, owner, run):
        if (owner, run) != (self.binding.owner_user_id, self.binding.run_id):
            return None
        row = self.db.execute("SELECT context,publication FROM run WHERE status='LIMITED' AND allowed=1").fetchone()
        if (not row or content_hash(json.loads(row[0])) != self.binding.context_sha256
                or not self.sources_current(json.loads(row[0]))):
            return None
        return json.loads(row[1])

    def sources_current(self, context):
        try:
            self.check_sources(context["company_knowledge"])
            return True
        except Exception:
            return False

    def revoke(self):
        with self.db:
            self.db.execute("UPDATE run SET allowed=0,publication=NULL")

    def cancel(self):
        with self.db:
            self.db.execute("UPDATE run SET status='CANCELLED',publication=NULL")

    def change_context(self):
        context = json.loads(self.db.execute("SELECT context FROM run").fetchone()[0])
        context["context_version"] += "-changed"
        with self.db:
            self.db.execute("UPDATE run SET context=?,publication=NULL", (json.dumps(context),))


def candidate_responses(publication):
    """Simulate IDs assigned by the fixture store; W1 will use persisted DB IDs."""
    return [{"id": str(uuid5(UUID(publication["run_id"]), publication["result_version"] + row["episode_version_id"])),
             "candidate_no": row["internal_rank"],
             **{k: v for k, v in row.items() if k != "internal_rank"}} for row in publication["candidates"]]
