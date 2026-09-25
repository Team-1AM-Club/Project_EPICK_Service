"""Local C01 HTTP server. Scope and retention cap must be supplied explicitly."""

import argparse
import importlib
import json
import os
import sqlite3

from ..restriction.http import make_server as shared_server
from . import contracts
from .store import Store


def load_source_authority(spec):
    if not spec or ":" not in spec:
        raise ValueError("SOURCE_AUTHORITY_FACTORY_REQUIRED")
    module, factory = spec.split(":", 1)
    authority = getattr(importlib.import_module(module), factory)()
    if not callable(getattr(authority, "is_registered", None)):
        raise ValueError("INVALID_SOURCE_AUTHORITY_FACTORY")
    return authority


def make_server(store, tokens, *, port=8764, host="127.0.0.1"):
    return shared_server(
        store,
        tokens,
        port=port,
        host=host,
        contracts=contracts,
        prefix="/c01/v1",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="W2 C01 / W3 integration candidate")
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "0.0.0.0"])
    parser.add_argument("--port", type=int, default=8764)
    parser.add_argument("--restriction-scope", required=True, choices=["version"])
    parser.add_argument("--max-ttl-seconds", required=True, type=int)
    parser.add_argument("--source-authority", required=True, help="Trusted module:factory")
    args = parser.parse_args(argv)
    tokens = {
        role: os.environ.get(f"W3_{role.upper()}_TOKEN", "") for role in ("w2", "operator", "w4")
    }
    try:
        with Store(
            args.db,
            restriction_scope=args.restriction_scope,
            max_ttl_seconds=args.max_ttl_seconds,
            source_authority=load_source_authority(args.source_authority),
        ) as store:
            server = make_server(store, tokens, port=args.port, host=args.host)
            print(
                json.dumps(
                    {
                        "url": f"http://{args.host}:{server.server_port}",
                        "schema_version": contracts.VERSION,
                        "adoption": "PENDING",
                    }
                ),
                flush=True,
            )
            try:
                server.serve_forever()
            finally:
                server.server_close()
    except (AttributeError, ImportError, ValueError, sqlite3.Error, OSError):
        print('{"error":"STARTUP_FAILED_CHECK_DB_SCOPE_TTL_PORT_TOKENS"}')
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
