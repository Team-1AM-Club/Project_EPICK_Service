"""Optional FastAPI adapter. The host must supply its authentication dependency."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .contracts import ContractError, object_value
from .evidence_extraction import extract_evidence
from .llm_contract import JsonClient, LLMError, parse_json_object
from .pipeline import recommend

MAX_BODY_BYTES = 1_000_000


def _llm_status(error: LLMError) -> int:
    if error.code in ("LLM_REAL_DATA_NOT_ENABLED", "LLM_SYNTHETIC_SAMPLE_REQUIRED"):
        return 403
    if error.code in ("LLM_INPUT_LIMIT_EXCEEDED", "LLM_CANDIDATE_LIMIT_EXCEEDED"):
        return 413
    if error.stage == "configuration" or error.code == "LLM_RATE_LIMITED":
        return 503
    return 504 if error.code == "LLM_TIMEOUT" else 502


def create_router(*, authenticate: Callable, client_factory: Callable[[], JsonClient],
                  extraction_client_factory: Callable[[], JsonClient] | None = None) -> APIRouter:
    """Legacy synthetic demo router; not a service authorization boundary.

    For server-owned data and policy checks use create_service_router instead.
    authenticate must return a trusted user ID or raise the host's auth error.

    Mount in a backend app with app.include_router(...). No server is started here.
    Credentials/model selection come from client_factory, never the request JSON.
    """
    router = APIRouter()

    def execute(payload: dict, user_id: str, operation: Callable):
        project = object_value(payload.get("project"), "project")
        if project.get("owner_id") != user_id:
            raise ContractError("PROJECT_ACCESS_DENIED", "project.owner_id")
        factory = extraction_client_factory if operation is extract_evidence and extraction_client_factory else client_factory
        return operation(payload, user_id=user_id, llm=factory())

    async def dispatch(request: Request, user_id: str, operation: Callable):
        if not isinstance(user_id, str) or not user_id.strip():
            return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"error": "JSON_CONTENT_TYPE_REQUIRED"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return JSONResponse({"error": "REQUEST_BODY_TOO_LARGE"}, status_code=413)
            body.extend(chunk)
        try:
            payload = parse_json_object(body.decode("utf-8"), "input", allow_fence=False)
        except (UnicodeError, LLMError):
            return JSONResponse({"error": "INVALID_JSON_OBJECT"}, status_code=400)
        try:
            result = await run_in_threadpool(execute, payload, user_id, operation)
            return JSONResponse(result)
        except ContractError as error:
            status = 403 if error.code == "PROJECT_ACCESS_DENIED" else 422
            return JSONResponse({"error": error.code, "field": error.field}, status_code=status)
        except LLMError as error:
            return JSONResponse({"error": error.code, "stage": error.stage},
                                status_code=_llm_status(error))

    @router.post("/w4/recommend")
    async def recommend_endpoint(request: Request, user_id: str = Depends(authenticate)):
        return await dispatch(request, user_id, recommend)

    @router.post("/w4/extract-evidence")
    async def extraction_endpoint(request: Request, user_id: str = Depends(authenticate)):
        return await dispatch(request, user_id, extract_evidence)

    def detailed_operation(payload, *, user_id, llm):
        from .detailed_recommendation import recommend_from_raw
        if extraction_client_factory is None:
            raise LLMError("LLM_EXTRACTION_CLIENT_NOT_CONFIGURED", "configuration")
        return recommend_from_raw(payload, user_id=user_id, extraction_llm=extraction_client_factory(), judgment_llm=llm)

    @router.post("/w4/recommend-from-raw")
    async def detailed_endpoint(request: Request, user_id: str = Depends(authenticate)):
        return await dispatch(request, user_id, detailed_operation)

    return router


def create_service_router(*, authenticate: Callable, backend, client_factory: Callable,
                          extraction_client_factory: Callable, c01_consumer=None, c01_split=False) -> APIRouter:
    """Mount this router alone in the team service; factories must not call models."""
    from pydantic import ValidationError
    from .service_adapter import ServiceError, execute_service
    from .c01_consumer import C01Error

    router = APIRouter()

    @router.post("/w4/recommend-from-raw")
    async def service_endpoint(request: Request, user_id: str = Depends(authenticate)):
        if not isinstance(user_id, str) or not user_id.strip():
            return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"error": "JSON_CONTENT_TYPE_REQUIRED"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return JSONResponse({"error": "REQUEST_BODY_TOO_LARGE"}, status_code=413)
            body.extend(chunk)
        try:
            payload = parse_json_object(body.decode("utf-8"), "input", allow_fence=False)
        except (UnicodeError, LLMError):
            return JSONResponse({"error": "INVALID_JSON_OBJECT"}, status_code=400)
        try:
            result = await run_in_threadpool(execute_service, payload, user_id=user_id, backend=backend,
                                            extraction_factory=extraction_client_factory,
                                            judgment_factory=client_factory, c01_consumer=c01_consumer,
                                            c01_split=c01_split)
            return JSONResponse(result)
        except C01Error as error:
            safe = {"C01_CONSUMER_NOT_CONFIGURED", "C01_STATUS_UNAVAILABLE", "C01_CONTEXT_CHANGED",
                    "C01_SOURCE_NOT_READY", "C01_SOURCE_EXPIRED", "C01_SIGNAL_CONFLICT", "C01_DELIVERY_FAILED"}
            return JSONResponse({"error": error.code if error.code in safe else "SERVICE_FAILURE"},
                                status_code=error.status if error.code in safe else 503)
        except ServiceError as error:
            # Only this module's fixed codes are exposed; never provider exception text.
            safe = {"BACKEND_UNAVAILABLE", "PROJECT_NOT_FOUND", "BACKEND_CONTEXT_INVALID", "CONTEXT_CHANGED",
                    "POLICY_SERVICE_UNAVAILABLE", "POLICY_DENIED", "MODEL_CONTENT_LOGGING_NOT_DISABLED",
                    "MODEL_CONFIGURATION_INVALID", "PROJECT_QUESTION_SCOPE_MISMATCH", "ENGINE_OUTPUT_INVALID"}
            return JSONResponse({"error": error.code if error.code in safe else "SERVICE_FAILURE"},
                                status_code=error.status if error.code in safe else 503)
        except (ValidationError, ContractError):
            return JSONResponse({"error": "INVALID_W4_CONTRACT"}, status_code=422)
        except LLMError as error:
            codes = {"LLM_REAL_DATA_NOT_ENABLED", "LLM_SYNTHETIC_SAMPLE_REQUIRED", "LLM_TIMEOUT",
                     "LLM_INPUT_LIMIT_EXCEEDED", "LLM_CANDIDATE_LIMIT_EXCEEDED", "LLM_RATE_LIMITED"}
            code = error.code if error.code in codes else "MODEL_FAILURE"
            return JSONResponse({"error": code}, status_code=_llm_status(LLMError(code, "model")))
        except Exception:
            # Host adapters can raise arbitrary messages, including request/response text.
            return JSONResponse({"error": "SERVICE_FAILURE"}, status_code=503)

    return router
