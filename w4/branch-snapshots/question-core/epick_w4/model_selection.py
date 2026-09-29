"""Connect reviewed comparison results to the winning configured model adapter."""

from copy import deepcopy
import os

from .llm_contract import LLMError
from .llm_prompts import CANDIDATE_PROMPT, EXTRACTION_PROMPT, QUESTION_PROMPT
from .model_eval import AXES, approval_valid, digest, document_digest, evaluate_runs
from .synthetic_policy import check_sample_payload


def _require(condition, code):
    if not condition:
        raise LLMError(code, "configuration")


class EvaluatedClient:
    """One selected client; no comparison calls or fallback during inference.

    The host owns adapter implementations and their truthful settings metadata.
    The current comparison covers question/matching, not raw extraction accuracy.
    """

    def __init__(self, client, winner, selection):
        self._client = client
        self._winner = deepcopy(winner)
        self._selection = deepcopy(selection)
        self._check_configuration()

    def _check_configuration(self):
        client = self._client
        try:
            settings = getattr(client, "generation_config", None)
            matches = (
                client.simulated is False
                and client.provider == self._winner["provider"]
                and client.model == self._winner["model"]
                and isinstance(settings, dict) and bool(settings)
                and digest(settings) == digest(self._winner["generation_config"])
            )
        except Exception:
            matches = False
        _require(matches, "LLM_SELECTED_CONFIGURATION_MISMATCH")

    @property
    def provider(self):
        return self._winner["provider"]

    @property
    def model(self):
        return self._winner["model"]

    @property
    def simulated(self):
        return False

    @property
    def generation_config(self):
        return deepcopy(self._winner["generation_config"])

    @property
    def selection(self):
        return deepcopy(self._selection)

    def complete_json(self, *, stage, system_prompt, payload):
        self._check_configuration()
        prompts = {"question": QUESTION_PROMPT, "candidates": CANDIDATE_PROMPT,
                   "extraction": EXTRACTION_PROMPT}
        _require(stage in prompts and system_prompt == prompts[stage],
                 "LLM_SELECTED_PROMPT_MISMATCH")
        # Apply the same fictional-content boundary to every provider adapter.
        check_sample_payload(stage, system_prompt, payload)
        try:
            return self._client.complete_json(stage=stage, system_prompt=system_prompt, payload=payload)
        except LLMError:
            raise
        except Exception:
            raise LLMError("LLM_CLIENT_CALL_FAILED", stage) from None


def select_evaluated_client(benchmark, policy, runs, *, client_factories):
    """Recompute the comparison, then construct only its unique winning adapter.

    client_factories maps evaluated candidate IDs to host-configured zero-argument
    factories. Factories must expose effective generation_config without secrets.
    Neither edited score summaries nor user request JSON can select the model.
    """
    try:
        reviewed = (isinstance(benchmark, dict) and isinstance(policy, dict)
                    and approval_valid(benchmark) and approval_valid(policy))
        _require(reviewed, "LLM_EVALUATION_NOT_APPROVED")
        if policy.get("schema_version") in ("w4-stage-policy/0.2", "w4-stage-policy/0.3"):
            from .stage_eval import evaluate_stage_runs
            report = evaluate_stage_runs(benchmark, policy, runs)
        else:
            report = evaluate_runs(benchmark, policy, runs)
    except LLMError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise LLMError("LLM_EVALUATION_INVALID", "configuration") from None
    recommendation = report["recommendation"]
    _require(report["status"] == "EVALUATED", "LLM_SELECTION_UNAVAILABLE")
    _require(recommendation["status"] != "TIED", "LLM_SELECTION_TIED")
    _require(recommendation["status"] == "HIGHEST_OBSERVED_SCORE"
             and len(recommendation["candidate_ids"]) == 1, "LLM_SELECTION_UNAVAILABLE")
    candidate_id = recommendation["candidate_ids"][0]
    winner = next(row for row in report["results"] if row["candidate_id"] == candidate_id)
    run = next(run for run in runs if run["candidate_id"] == candidate_id)
    factory = client_factories.get(candidate_id) if isinstance(client_factories, dict) else None
    _require(callable(factory), "LLM_SELECTED_ADAPTER_UNAVAILABLE")
    selection = {
        "method": "highest_observed_score/0.1", "candidate_id": candidate_id,
        "scores": winner["scores"], "total_score": winner["total_score"],
        "evaluated_axes": list(AXES), "unevaluated_stages": ["extraction"],
        "benchmark_sha256": report["benchmark_sha256"], "policy_sha256": report["policy_sha256"],
        "protocol_sha256": policy["protocol_sha256"], "scorer_version": report["scorer_version"],
        "run_sha256": document_digest(run),
        "generation_config_sha256": digest(winner["generation_config"]),
    }
    try:
        client = factory()
    except LLMError:
        raise
    except Exception:
        raise LLMError("LLM_CLIENT_INITIALIZATION_FAILED", "configuration") from None
    return EvaluatedClient(client, winner, selection)


def available_client_factories(runs):
    """CLI adapters currently installed here. An unavailable winner never falls back.

    Backends may pass their own candidate-to-factory registry to the selector for
    other providers. No adapter, credential or endpoint is loaded from run JSON.
    """
    from .solar_client import SolarClient

    def solar_factory(model):
        return lambda: SolarClient(os.environ.get("UPSTAGE_API_KEY", ""), model=model)

    if not isinstance(runs, list):
        return {}
    return {run["candidate_id"]: solar_factory(run["model"]) for run in runs
            if isinstance(run, dict) and run.get("provider") == "upstage"
            and isinstance(run.get("candidate_id"), str) and isinstance(run.get("model"), str)}
