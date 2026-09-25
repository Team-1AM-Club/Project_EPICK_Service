"""Localhost-only HTTP transport for the draft integration contract."""

import argparse
import hmac
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from ..c01.authority import SourceAuthorityUnavailable, SourceNotRegistered
from .contracts import VERSION
from . import contracts as legacy_contracts
from .store import Store

MAX_BODY = 2_000_000


def make_server(
    store,
    tokens,
    *,
    port=8763,
    host="127.0.0.1",
    contracts=legacy_contracts,
    prefix="/v1",
):
    if (
        set(tokens) != {"w2", "operator", "w4"}
        or any(not token for token in tokens.values())
        or len(set(tokens.values())) != 3
    ):
        raise ValueError("THREE_DISTINCT_ROLE_TOKENS_REQUIRED")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass  # Avoid logging URLs, bodies or Authorization headers.

        def reply(self, status, value):
            data = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def discard_small_request_body(self):
            """Avoid a TCP reset when rejecting a small POST before parsing its body."""
            if self.command != "POST" or self.headers.get("Transfer-Encoding"):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if 0 < length <= MAX_BODY:
                self.rfile.read(length)

        def authorized(self, roles):
            authorization = self.headers.get("Authorization", "")
            role = next(
                (
                    name
                    for name, token in tokens.items()
                    if hmac.compare_digest(authorization, "Bearer " + token)
                ),
                None,
            )
            if role not in roles:
                self.discard_small_request_body()
                self.reply(
                    401 if role is None else 403,
                    {"error": "UNAUTHORIZED" if role is None else "FORBIDDEN"},
                )
                return False
            return True

        def do_GET(self):
            try:
                target = urlsplit(self.path)
                if target.path == "/health":
                    store.db.execute("SELECT count(*) FROM identity").fetchone()
                    self.reply(
                        200,
                        {
                            "status": "ok",
                            "schema_version": contracts.VERSION,
                            "adoption": "PENDING",
                            "index": "sqlite-fts5",
                        },
                    )
                    return
                if not self.authorized(
                    {"w4", "operator", "w2"}
                    if target.path.startswith(prefix + "/status/")
                    else {"w4", "operator"}
                ):
                    return
                if target.path.startswith(prefix + "/status/"):
                    source = target.path.removeprefix(prefix + "/status/")
                    pattern = (
                        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                        if prefix == "/c01/v1"
                        else r"[A-Za-z][A-Za-z0-9._:-]{0,127}"
                    )
                    if not re.fullmatch(pattern, source):
                        raise ValueError("INVALID_SOURCE")
                    self.reply(200, store.status(source))
                elif target.path == prefix + "/search":
                    params = parse_qs(target.query)
                    if set(params) != {"q"} or len(params["q"]) != 1 or len(params["q"][0]) > 200:
                        raise ValueError("INVALID_QUERY")
                    self.reply(200, {"results": store.search(params["q"][0])})
                elif target.path == prefix + "/signals":
                    self.reply(200, {"signals": store.signals()})
                else:
                    self.reply(404, {"error": "NOT_FOUND"})
            except (ValueError, KeyError):
                self.reply(422, {"error": "CONTRACT_INVALID"})
            except sqlite3.Error:
                self.reply(503, {"error": "STORAGE_UNAVAILABLE", "index_ack": False})

        def do_POST(self):
            routes = {
                prefix + "/events": ({"w2"}, contracts.Event, store.consume),
                prefix + "/replay": ({"w2"}, contracts.ReplayBatch, store.replay),
                prefix + "/snapshot": ({"w2"}, contracts.Snapshot, store.snapshot),
                prefix + "/index": ({"operator"}, contracts.IndexRequest, store.index),
                prefix + "/signals/ack": ({"w4"}, contracts.DeliveryReceipt, None),
                prefix + "/purge": ({"operator"}, None, None),
            }
            if self.path not in routes:
                self.reply(404, {"error": "NOT_FOUND"})
                return
            roles, model, operation = routes[self.path]
            if not self.authorized(roles):
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("TRANSFER_ENCODING_UNSUPPORTED")
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_BODY:
                    self.reply(413, {"error": "PAYLOAD_TOO_LARGE"})
                    return
                if length <= 0 or self.headers.get_content_type() != "application/json":
                    raise ValueError("JSON_BODY_REQUIRED")
                raw = self.rfile.read(length)
                value = model.model_validate_json(raw) if model else json.loads(raw)
                if self.path == prefix + "/signals/ack":
                    success = store.acknowledge(value.signal_id)
                    self.reply(200 if success else 404, {"delivered": success})
                elif self.path == prefix + "/purge":
                    if value != {}:
                        raise ValueError("EMPTY_OBJECT_REQUIRED")
                    self.reply(200, store.purge())
                else:
                    result = operation(value)
                    code = 503 if result["reason"] == "INDEX_FAILED" else 200
                    if result.get("outcome") in {"CONFLICT", "STALE_SNAPSHOT", "CURSOR_AHEAD"}:
                        code = 409
                    self.reply(code, result)
            except SourceAuthorityUnavailable:
                self.reply(503, {"error": "SOURCE_AUTHORITY_UNAVAILABLE", "index_ack": False})
            except SourceNotRegistered:
                self.reply(422, {"error": "SOURCE_NOT_REGISTERED", "index_ack": False})
            except (ValidationError, ValueError, TypeError):
                self.reply(422, {"error": "CONTRACT_INVALID", "index_ack": False})
            except sqlite3.Error:
                self.reply(503, {"error": "STORAGE_UNAVAILABLE", "index_ack": False})

    return HTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="W3 local restriction integration candidate")
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=8763)
    args = parser.parse_args(argv)
    tokens = {
        role: os.environ.get(f"W3_{role.upper()}_TOKEN", "") for role in ("w2", "operator", "w4")
    }
    try:
        with Store(args.db) as store:
            server = make_server(store, tokens, port=args.port)
            print(
                json.dumps(
                    {"url": f"http://127.0.0.1:{server.server_port}", "schema_version": VERSION}
                ),
                flush=True,
            )
            try:
                server.serve_forever()
            finally:
                server.server_close()
    except (ValueError, sqlite3.Error, OSError):
        print(json.dumps({"error": "STARTUP_FAILED_CHECK_DB_PORT_AND_ROLE_TOKENS"}))
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
