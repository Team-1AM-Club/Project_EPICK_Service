"""Freeze equal v4 scorers/data, then measure both existing prompt profiles sequentially."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

REBIND = '''
import json, sys
from pathlib import Path
root=Path(sys.argv[1]); sys.path.insert(0,str(root))
from epick_w4.stage_eval import build_stage_requests, validate_stage_policy
from epick_w4.extraction_eval import build_extraction_requests, validate_references
from epick_w4.model_eval import digest
from epick_w4.synthetic_policy import content_hash, check_sample_payload
def read(name): return json.loads((root/name).read_text(encoding="utf-8"))
def write(name,value): (root/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\\n",encoding="utf-8")
b=read("samples/evaluation/benchmark.v4.draft.json")
p=read("samples/evaluation/policy.v4.draft.json")
requests=build_stage_requests(b)
p["protocol_sha256"]=digest(requests)
validate_stage_policy(b,p)
write("samples/evaluation/policy.v4.draft.json",p)
a=read("epick_w4/evaluation_allowlist.json")
a["request_sha256"]=sorted(set(a["request_sha256"])|{content_hash({k:r[k] for k in ("stage","system_prompt","payload")}) for r in requests})
write("epick_w4/evaluation_allowlist.json",a)
for r in requests: check_sample_payload(r["stage"],r["system_prompt"],r["payload"])
source=read("samples/extraction/raw-experiences.v4.synthetic.json")
ref=read("samples/extraction/benchmark.v4.draft.json")
ref["protocol_sha256"]=digest(build_extraction_requests(source))
validate_references(source,ref)
write("samples/extraction/benchmark.v4.draft.json",ref)
'''


def fingerprints(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in (".py", ".json")
            and p.name != "frozen-manifest.json"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        parser.error("Choose a new directory; past measurements are immutable")
    output.mkdir(parents=True)
    snapshots = {}
    for profile in ("a", "b"):
        runtime = output / f"profile-{profile}-runtime"
        for folder, pattern in (("epick_w4", "*"), ("samples", "**/*.json"), ("scripts", "*.py")):
            for source in (ROOT / folder).glob(pattern):
                if not source.is_file() or source.suffix not in (".py", ".json"):
                    continue
                target = runtime / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        if profile == "a":
            for name in ("llm_prompts.py", "output_schemas.py", "local_client.py"):
                shutil.copyfile(ROOT / "output/review-v3-20260909/v2-source/epick_w4" / name,
                                runtime / "epick_w4" / name)
        subprocess.run([sys.executable, "-c", REBIND, str(runtime)], check=True)
        snapshots[profile] = fingerprints(runtime)
        (runtime / "frozen-manifest.json").write_text(json.dumps(snapshots[profile], indent=2), encoding="utf-8")
    models = json.loads((ROOT / "samples/evaluation/local-models.v2.json").read_text(encoding="utf-8"))["models"]
    completed = []
    for model in models:
        for profile in ("b", "a"):
            runtime = output / f"profile-{profile}-runtime"
            if fingerprints(runtime) != snapshots[profile]:
                raise RuntimeError("Frozen evaluation code or data changed")
            target = output / f"{profile}-{model['candidate_id']}"
            print(f"START {profile} {model['candidate_id']}", flush=True)
            subprocess.run([sys.executable, str(runtime / "scripts/run-local-evaluation.py"),
                            "--v4", "--skip-demo", "--candidate", model["candidate_id"],
                            "--fit-target-mib", "4096" if "26b" in model["candidate_id"] else "1536",
                            "--output-dir", str(target)], check=True)
            completed.append({"profile": profile, "candidate_id": model["candidate_id"], "directory": str(target)})
    if any(fingerprints(output / f"profile-{p}-runtime") != expected for p, expected in snapshots.items()):
        raise RuntimeError("Frozen evaluation changed during measurements")
    (output / "completed.json").write_text(json.dumps(completed, indent=2), encoding="utf-8")
    print("A/B v4 collection finished; compare stage scores and gates before choosing integration candidates.", flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
