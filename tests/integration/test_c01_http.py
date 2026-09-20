import threading
from types import ModuleType
from uuid import UUID

import pytest

from test_c01 import (
    SOURCE,
    SourceAuthority,
    allowed_version,
    fixture,
    index_request,
    parse,
    store_at,
)
from test_restriction_http import TOKENS, call


def test_c01_source_authority_factory_is_explicit_and_startup_errors_are_sanitized(
    tmp_path, monkeypatch, capsys
):
    import sys

    from w3_knowledge.c01.http import load_source_authority, main

    module = ModuleType("test_source_authority_factory")
    module.build = SourceAuthority
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert isinstance(load_source_authority(f"{module.__name__}:build"), SourceAuthority)

    with pytest.raises(ValueError, match="SOURCE_AUTHORITY_FACTORY_REQUIRED"):
        load_source_authority("implicit")
    assert (
        main(
            [
                "--db",
                str(tmp_path / "startup.db"),
                "--restriction-scope",
                "version",
                "--max-ttl-seconds",
                "3600",
                "--source-authority",
                "missing_authority_module:build",
            ]
        )
        == 2
    )
    assert (
        capsys.readouterr().out.strip()
        == '{"error":"STARTUP_FAILED_CHECK_DB_SCOPE_TTL_PORT_TOKENS"}'
    )


def test_c01_http_receipts_role_auth_and_w4_delivery(tmp_path):
    from w3_knowledge.c01.http import make_server

    with store_at(tmp_path / "http.db") as store:
        server = make_server(store, TOKENS, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            assert call(base, "/c01/v1/events", "bad", allowed_version())[0] == 401
            assert call(base, "/c01/v1/events", "w4", allowed_version())[0] == 403
            code, reply = call(base, "/c01/v1/events", "w2", allowed_version())
            assert code == 200 and reply["receipt"] == "COMMITTED"
            assert reply["restriction_revision"] == 0
            assert call(base, "/c01/v1/index", "operator", index_request(store).model_dump())[1][
                "index_ack"
            ]
            assert len(call(base, "/c01/v1/search?q=cloud")[1]["results"]) == 1
            signal = call(base, "/c01/v1/signals")[1]["signals"][-1]
            assert call(base, "/c01/v1/signals/ack", "w4", {"signal_id": signal["signal_id"]})[1][
                "delivered"
            ]
            assert call(base, "/c01/v1/signals/ack", "w4", {"signal_id": signal["signal_id"]})[1][
                "delivered"
            ]  # Repeat the exact ACK when the first response is lost.
            assert call(base, f"/c01/v1/status/{SOURCE}")[1]["index_ack"]
            wrong = fixture("restriction-changed")
            wrong["payload"]["job_id"] = "never-echo"
            code, reply = call(base, "/c01/v1/events", "w2", wrong)
            assert code == 422 and "never-echo" not in str(reply)
            # Independent stored input still works after malformed HTTP request.
            assert store.consume(parse(allowed_version()))["outcome"] == "DUPLICATE"
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()


def test_c01_http_source_authority_failures_are_closed_and_sanitized(tmp_path):
    from w3_knowledge.c01.http import make_server

    cases = [
        (SourceAuthority(allowed=set()), 422, "SOURCE_NOT_REGISTERED"),
        (
            SourceAuthority(error=TimeoutError("PRIVATE_AUTHORITY_DETAIL")),
            503,
            "SOURCE_AUTHORITY_UNAVAILABLE",
        ),
    ]
    for index, (authority, expected_code, expected_error) in enumerate(cases):
        with store_at(tmp_path / f"authority-{index}.db", authority=authority) as store:
            server = make_server(store, TOKENS, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                code, reply = call(base, "/c01/v1/events", "w2", allowed_version())
                assert code == expected_code
                assert reply == {"error": expected_error, "index_ack": False}
                assert "PRIVATE_AUTHORITY_DETAIL" not in str(reply)
                assert authority.calls == [UUID(SOURCE)]
                assert store.db.execute("SELECT count(*) FROM c01_events").fetchone()[0] == 0
            finally:
                server.shutdown()
                worker.join(timeout=3)
                server.server_close()
