import threading

from test_c01 import SOURCE, allowed_version, fixture, index_request, parse, store_at
from test_restriction_http import TOKENS, call


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
