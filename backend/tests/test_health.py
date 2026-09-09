from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app import main

client = TestClient(main.app)


class SuccessfulConnection:
    def execute(self, statement: object) -> None:
        return None


    def __enter__(self) -> "SuccessfulConnection":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        return False


class SuccessfulEngine:
    def connect(self) -> SuccessfulConnection:
        return SuccessfulConnection()


class FailingEngine:
    def connect(self) -> None:
        raise SQLAlchemyError()


def test_health_reports_running() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_connected_database(monkeypatch) -> None:
    monkeypatch.setattr(main, "engine", SuccessfulEngine())

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "connected"}


def test_readiness_reports_unavailable_database(monkeypatch) -> None:
    monkeypatch.setattr(main, "engine", FailingEngine())

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
