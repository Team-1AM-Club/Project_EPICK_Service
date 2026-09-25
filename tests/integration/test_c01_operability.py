import sqlite3

from test_c01 import SOURCE, allowed_version, index_request, parse, store_at
from test_restriction_http import TOKENS


def test_c01_server_supports_explicit_container_bind(tmp_path):
    from w3_knowledge.c01.http import make_server

    with store_at(tmp_path / "bind.sqlite") as store:
        server = make_server(store, TOKENS, port=0, host="0.0.0.0")
        try:
            assert server.server_address[0] == "0.0.0.0"
        finally:
            server.server_close()


def test_online_backup_restores_durable_ready_state(tmp_path):
    from w3_knowledge.c01.operator import backup_database, restore_database

    source = tmp_path / "source.sqlite"
    backup = tmp_path / "backup.sqlite"
    restored = tmp_path / "restored.sqlite"
    with store_at(source) as store:
        store.consume(parse(allowed_version()))
        assert store.index(index_request(store))["index_ack"] is True

    backup_database(source, backup)
    restore_database(backup, restored)

    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert db.execute("SELECT consumer FROM identity").fetchone() == (
            "w3-c01/0.2-candidate/r2",
        )
    with store_at(restored) as store:
        status = store.status(SOURCE)
        assert status["event_cursor"] == 1
        assert status["index_ack"] is True


def test_backup_and_restore_refuse_overwrite_or_wrong_database(tmp_path):
    from w3_knowledge.c01.operator import backup_database, restore_database

    source = tmp_path / "source.sqlite"
    backup = tmp_path / "backup.sqlite"
    occupied = tmp_path / "occupied.sqlite"
    with store_at(source):
        pass
    backup_database(source, backup)
    occupied.write_bytes(b"do-not-replace")

    for operation in (
        lambda: backup_database(source, backup),
        lambda: restore_database(backup, occupied),
        lambda: backup_database(occupied, tmp_path / "invalid.sqlite"),
    ):
        try:
            operation()
        except ValueError as error:
            assert str(error) in {"TARGET_ALREADY_EXISTS", "NOT_C01_DATABASE"}
        else:
            raise AssertionError("unsafe database operation was accepted")
