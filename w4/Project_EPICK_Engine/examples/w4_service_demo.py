"""In-process HTTP demo using simulated models and backend. No external calls."""

from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / "samples/service-handoff" / name).read_text(encoding="utf-8"))


class DemoBackend:
    def __init__(self, diagnostic=False):
        self.context = read("server-context.diagnostic.json" if diagnostic else "server-context.synthetic.json")
        self.allowed = {"PROCESS": True, "SEND_TO_PROVIDER": True, "RETURN_TO_CALLER": True}
        self.accessible = True
        self.checks = []

    def load_context(self, *, user_id, project_id):
        if (not self.accessible or user_id != self.context["project"]["owner_id"]
                or project_id != self.context["project"]["project_id"]):
            return None
        return deepcopy(self.context)

    def authorize(self, **kwargs):
        self.checks.append(deepcopy(kwargs))
        return self.allowed[kwargs["action"]] and kwargs["context_version"] == self.context["context_version"]


class DemoClient:
    simulated = True
    content_logging_enabled = False
    provider = "simulated-fixture"

    def __init__(self, stage):
        self.model = f"simulated-{stage}"
        self.responses = read("extraction-responses.simulated.json")
        self.calls = []
        self.after_call = None
        self.mutate_response = None

    def complete_json(self, *, stage, system_prompt, payload):
        eid = payload["episode_id"]
        self.calls.append({"stage": stage, "episode_id": eid})
        if stage == "extraction":
            response = deepcopy(self.responses[eid])
        else:
            qid = payload["question"]["question_id"]
            refs = {}
            if qid == "expertise" and eid == "v4-raw-study":
                refs = {"specific_area": ["u2"], "sustained_learning": ["u2"], "practical_use": ["u2", "u3"]}
            if qid == "job_experience" and eid == "v4-raw-study":
                refs = {"job_connection": ["u3"]}
            response = {"checks": [{"check_id": c["id"], "status": "SUPPORTED" if c["id"].split(".")[1] in refs else "NOT_SHOWN",
                                    "evidence_ids": refs.get(c["id"].split(".")[1], [])}
                                   for c in payload["question"]["content_checks"]], "company_links": []}
            if eid == "v4-raw-study":
                response["company_links"] = [{"kind": c["kind"], "id": c["id"],
                    "evidence_ids": ["u2" if c["kind"] == "CLAIM" else "u3"]} for c in payload["company_criteria"]]
        if self.mutate_response:
            self.mutate_response(payload, response)
        if self.after_call:
            self.after_call()
        return response


def create_demo_app(backend=None, extractor=None, judge=None, authenticate=None):
    from fastapi import FastAPI
    from epick_w4.api import create_service_router
    backend = backend or DemoBackend()
    extractor, judge = extractor or DemoClient("extraction"), judge or DemoClient("judgment")
    app = FastAPI()
    app.include_router(create_service_router(authenticate=authenticate or (lambda: "user-demo"), backend=backend,
                        client_factory=lambda: judge, extraction_client_factory=lambda: extractor))
    return app
