from __future__ import annotations

import uvicorn

from app.runtime.w4_question_core_context_adapter import (
    create_configured_w4_question_core_context_app,
)

if __name__ == "__main__":
    uvicorn.run(
        create_configured_w4_question_core_context_app(),
        host="0.0.0.0",
        port=8081,
        log_level="info",
    )
