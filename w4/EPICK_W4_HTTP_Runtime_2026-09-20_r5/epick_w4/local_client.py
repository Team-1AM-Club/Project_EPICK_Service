"""Actual inference through a loopback llama.cpp server, using synthetic inputs."""

from copy import deepcopy
from datetime import datetime, timezone
from http.client import HTTPException
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .llm_contract import LLMError, parse_json_object
from .output_schemas import SCHEMAS, schema_for
from .synthetic_policy import check_sample_payload


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalClient:
    simulated = False

    def __init__(self, *, provider, model, model_sha256, port=18080, timeout=120, structured=False, capture_traces=True):
        if (not isinstance(provider, str) or not provider.strip()
                or not isinstance(model, str) or not model.strip()
                or not isinstance(model_sha256, str) or len(model_sha256) != 64
                or any(c not in "0123456789abcdef" for c in model_sha256)
                or type(port) is not int or not 1024 <= port <= 65535
                or type(timeout) is not int or not 1 <= timeout <= 120
                or type(structured) is not bool or type(capture_traces) is not bool):
            raise LLMError("LLM_LOCAL_CONFIGURATION_INVALID", "configuration")
        self.provider, self.model, self.model_sha256 = provider, model, model_sha256
        self.port, self.timeout = port, timeout
        self.structured = structured
        self.content_logging_enabled = capture_traces
        self.calls = []
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    @property
    def generation_config(self):
        config = {"temperature": 0, "top_k": 0, "top_p": 1, "min_p": 0,
                "repeat_penalty": 1, "presence_penalty": 0, "seed": 42,
                "max_tokens": 4096, "stream": False,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
                "reasoning_effort": "none", "cache_prompt": False}
        config["model_sha256"] = self.model_sha256
        if self.structured:
            del config["response_format"]
            config["stage_response_formats"] = {
                stage: {"type": "json_object", "schema": deepcopy(schema)}
                for stage, schema in SCHEMAS.items()}
            config["schema_binding"] = "input_ids_and_coherent_states/0.3"
        return config

    def complete_json(self, *, stage, system_prompt, payload):
        check_sample_payload(stage, system_prompt, payload)
        settings = self.generation_config
        settings.pop("model_sha256", None)
        settings.pop("schema_binding", None)
        formats = settings.pop("stage_response_formats", None)
        if formats is not None:
            settings["response_format"] = {"type": "json_object", "schema": schema_for(stage, payload)}
        body = {"model": self.model, "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ], **settings}
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 250_000:
            raise LLMError("LLM_INPUT_LIMIT_EXCEEDED", stage)
        trace = {"stage": stage, "started_at": datetime.now(timezone.utc).isoformat(),
                 "request": deepcopy(body) if self.content_logging_enabled else None, "response": None, "error": None}
        started = time.perf_counter()
        try:
            request = Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                              data=encoded, method="POST",
                              headers={"Content-Type": "application/json"})
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise LLMError("LLM_RESPONSE_LIMIT_EXCEEDED", stage)
            envelope = parse_json_object(raw.decode("utf-8"), stage)
            if self.content_logging_enabled:
                trace["response"] = envelope
            if envelope.get("model") != self.model:
                raise LLMError("LLM_LOCAL_MODEL_MISMATCH", stage)
            choices = envelope.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise LLMError("LLM_INVALID_RESPONSE", stage)
            choice = choices[0]
            if choice.get("finish_reason") == "length":
                raise LLMError("LLM_OUTPUT_TRUNCATED", stage)
            message = choice.get("message")
            if choice.get("finish_reason") != "stop" or not isinstance(message, dict):
                raise LLMError("LLM_INVALID_RESPONSE", stage)
            if message.get("refusal") or message.get("tool_calls"):
                raise LLMError("LLM_RESPONSE_REJECTED", stage)
            return parse_json_object(message.get("content"), stage)
        except LLMError as error:
            trace["error"] = error.code
            raise
        except HTTPError as error:
            code = "LLM_REDIRECT_REJECTED" if 300 <= error.code < 400 else "LLM_HTTP_ERROR"
            error.close()
            trace["error"] = code
            raise LLMError(code, stage) from None
        except (URLError, OSError, HTTPException):
            trace["error"] = "LLM_NETWORK_ERROR"
            raise LLMError("LLM_NETWORK_ERROR", stage) from None
        except UnicodeError:
            trace["error"] = "LLM_INVALID_RESPONSE"
            raise LLMError("LLM_INVALID_RESPONSE", stage) from None
        finally:
            trace["elapsed_seconds"] = round(time.perf_counter() - started, 6)
            self.calls.append(trace)
