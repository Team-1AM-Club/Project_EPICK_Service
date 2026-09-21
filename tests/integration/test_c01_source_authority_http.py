import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from uuid import UUID

import pytest

from test_c01 import SOURCE, allowed_version, parse, store_at
from w3_knowledge.c01.authority import SourceAuthorityUnavailable, SourceNotRegistered
from w3_knowledge.c01.source_authority_http import HTTPSourceAuthority, create_source_authority


class AuthorityHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_body = {"source_id": SOURCE, "registered": True}
    delay_seconds = 0.0
    trickle_seconds = 0.0
    seen = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        if type(self).delay_seconds:
            time.sleep(type(self).delay_seconds)
        body = json.dumps(type(self).response_body).encode()
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            if type(self).trickle_seconds:
                for chunk in (body[:1], body[1:2], body[2:]):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(type(self).trickle_seconds)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture
def authority_server():
    AuthorityHandler.response_status = 200
    AuthorityHandler.response_body = {"source_id": SOURCE, "registered": True}
    AuthorityHandler.delay_seconds = 0.0
    AuthorityHandler.trickle_seconds = 0.0
    AuthorityHandler.seen = []
    server = HTTPServer(("127.0.0.1", 0), AuthorityHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join(timeout=3)
    server.server_close()


def test_real_http_authority_allows_registered_and_rejects_unregistered(authority_server, tmp_path):
    authority = HTTPSourceAuthority(authority_server, "synthetic-token")
    with store_at(tmp_path / "source.db", authority=authority) as store:
        assert store.consume(parse(allowed_version()))["receipt"] == "COMMITTED"
        assert AuthorityHandler.seen == [
            (f"/internal/v1/sources/{SOURCE}/authority", "Bearer synthetic-token")
        ]
        AuthorityHandler.response_body = {"source_id": SOURCE, "registered": False}
        with pytest.raises(SourceNotRegistered):
            store.consume(parse(allowed_version()))
        assert store.db.execute("SELECT count(*) FROM c01_events").fetchone()[0] == 1


@pytest.mark.parametrize(
    "status,body",
    [
        (503, {"detail": {"code": "SOURCE_AUTHORITY_UNAVAILABLE"}}),
        (401, {"detail": {"code": "SOURCE_AUTHORITY_UNAUTHORIZED"}}),
        (200, {"source_id": SOURCE, "registered": 1}),
        (200, {"source_id": "10000000-0000-4000-8000-000000000006", "registered": True}),
        (200, {"source_id": SOURCE, "registered": True, "owner": "private"}),
    ],
)
def test_http_authority_fails_closed_on_non_authoritative_response(authority_server, status, body):
    AuthorityHandler.response_status = status
    AuthorityHandler.response_body = body
    authority = HTTPSourceAuthority(authority_server, "synthetic-token")
    with pytest.raises(SourceAuthorityUnavailable, match="SOURCE_AUTHORITY_UNAVAILABLE"):
        authority.is_registered(UUID(SOURCE))


def test_source_authority_factory_requires_explicit_safe_configuration(
    monkeypatch, authority_server
):
    monkeypatch.setenv("W3_SOURCE_AUTHORITY_ENDPOINT", authority_server)
    monkeypatch.setenv("W3_SOURCE_AUTHORITY_TOKEN", "synthetic-token")
    assert create_source_authority().is_registered(UUID(SOURCE)) is True
    monkeypatch.delenv("W3_SOURCE_AUTHORITY_TOKEN")
    with pytest.raises(ValueError, match="SOURCE_AUTHORITY_CONFIGURATION_INVALID"):
        create_source_authority()
    with pytest.raises(ValueError, match="SOURCE_AUTHORITY_CONFIGURATION_INVALID"):
        HTTPSourceAuthority("http://example.com:9000", "synthetic-token")


def test_http_authority_read_timeout_is_unavailable(authority_server):
    AuthorityHandler.delay_seconds = 0.2
    authority = HTTPSourceAuthority(
        authority_server,
        "synthetic-token",
        connect_timeout_seconds=0.05,
        read_timeout_seconds=0.02,
        total_timeout_seconds=0.1,
    )
    with pytest.raises(SourceAuthorityUnavailable, match="SOURCE_AUTHORITY_UNAVAILABLE"):
        authority.is_registered(UUID(SOURCE))


def test_http_authority_total_timeout_covers_trickled_body(authority_server):
    AuthorityHandler.trickle_seconds = 0.05
    authority = HTTPSourceAuthority(
        authority_server,
        "synthetic-token",
        read_timeout_seconds=0.2,
        total_timeout_seconds=0.08,
    )
    with pytest.raises(SourceAuthorityUnavailable, match="SOURCE_AUTHORITY_UNAVAILABLE"):
        authority.is_registered(UUID(SOURCE))
