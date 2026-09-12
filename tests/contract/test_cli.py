from w3_knowledge.cli import main


def test_cli_requires_existing_synthetic_fixture(capsys) -> None:
    fixture = "tests/fixtures/synthetic/normal"
    assert main(["validate", "--fixture", str(fixture), "--mode", "synthetic"]) == 0
    assert "mode=SYNTHETIC" in capsys.readouterr().out


def test_cli_returns_contract_error_for_missing_input(capsys, tmp_path) -> None:
    assert main(["diagnose-sample", "--input", str(tmp_path / "missing.json")]) == 2
    assert "INPUT_CONTRACT_INVALID" in capsys.readouterr().out
