from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import engine

app = FastAPI(title="EPICK Service API")


@app.get("/health")
def health() -> dict[str, str]:
    """FastAPI 프로세스가 실행 중인지"""
    return {"status": "ok"}


@app.get("/health/ready")
def readiness() -> dict[str, str]:
    """PostgreSQL에 연결할 수 있는지 체크"""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "ready",
            "database": "connected",
        }

    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="database unavailable",
        ) from error
