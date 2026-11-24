from hashlib import sha256
import json

from qte.cli import main


def test_audit_checks_nested_outputs_keeps_missing_and_changed_distinct(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x").write_bytes(b"hello")
    path = tmp_path / "manifest.json"
    digest = sha256(b"hello").hexdigest()
    path.write_text(json.dumps({"outputs": {"data/x": digest}, "code": {"old.py": "historical"}}))
    assert main(["audit-evidence", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["matched"] == 1
    path.write_text(json.dumps({"outputs": {"data/x": digest, "data/y": digest}}))
    (tmp_path / "data" / "x").write_bytes(b"changed")
    assert main(["audit-evidence", str(path)]) == 1
    assert [row["status"] for row in json.loads(capsys.readouterr().out)[0]["differences"]] == ["changed", "missing"]
    path.write_text(json.dumps({"outputs": {"../outside": digest}}))
    assert main(["audit-evidence", str(path)]) == 1
    assert "invalid relative" in json.loads(capsys.readouterr().out)[0]["error"]
