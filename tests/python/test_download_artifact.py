import hashlib
import json
from pathlib import Path

import pytest

from qte.download_artifact import (
    compile_download_configuration,
    verify_download_files,
    verify_download_output,
    write_download_output,
)
from test_adapters import ALPACA


def config():
    return {"symbols": ["SPY"], "timeframe": "1Hour", "start": "2024-01-02T14:00:00Z",
            "end": "2024-01-02T17:00:00Z", "feed": "iex", "action_free": True}


def create(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(config()))
    rows = json.loads(ALPACA.read_text())["bars"]["SPY"]
    calls = []
    def fetch(params):
        calls.append(params)
        return {"bars": {"SPY": rows[:1] if len(calls) == 1 else rows[1:]},
                "next_page_token": "next" if len(calls) == 1 else None}
    output = write_download_output(path, tmp_path / "output", page_fetcher=fetch)
    return output, calls


def captured(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def rehash(files):
    complete = json.loads(files["complete.json"])
    complete["sha256"] = {name: hashlib.sha256(raw).hexdigest()
                          for name, raw in files.items() if name != "complete.json"}
    files["complete.json"] = (json.dumps(complete, indent=2, sort_keys=True) + "\n").encode()


def test_write_and_verify_exact_recursive_lineage(tmp_path):
    output, calls = create(tmp_path)
    checked = verify_download_output(output)
    assert len(calls) == 2 and calls[1]["page_token"] == "next"
    assert checked["manifest"]["artifact_kind"] == "alpaca_download_v1"
    assert checked["summary"]["page_count"] == 2
    assert set(captured(output)) == {"configuration.json", "download_artifact.json", "complete.json",
        *{f"cache/{checked['manifest']['request_id']}/{name}" for name in (
            "download.json", "bars.json", "page-000000.json", "page-000001.json")}}


@pytest.mark.parametrize("change", ["request", "page", "bars", "inventory"])
def test_rehashed_semantic_forgery_is_rejected(tmp_path, change):
    output, _ = create(tmp_path)
    files = captured(output)
    request_id = json.loads(files["download_artifact.json"])["request_id"]
    if change == "request":
        metadata = json.loads(files[f"cache/{request_id}/download.json"])
        metadata["request"]["feed"] = "sip"
        files[f"cache/{request_id}/download.json"] = json.dumps(metadata, sort_keys=True,
            separators=(",", ":")).encode()
    elif change == "page":
        page = json.loads(files[f"cache/{request_id}/page-000000.json"])
        page["next_page_token"] = None
        raw = json.dumps(page, sort_keys=True, separators=(",", ":")).encode()
        files[f"cache/{request_id}/page-000000.json"] = raw
        metadata = json.loads(files[f"cache/{request_id}/download.json"])
        metadata["sha256"]["page-000000.json"] = hashlib.sha256(raw).hexdigest()
        files[f"cache/{request_id}/download.json"] = json.dumps(metadata, sort_keys=True,
            separators=(",", ":")).encode()
    elif change == "bars":
        files[f"cache/{request_id}/bars.json"] = b"{}"
        metadata = json.loads(files[f"cache/{request_id}/download.json"])
        metadata["sha256"]["bars.json"] = hashlib.sha256(b"{}").hexdigest()
        files[f"cache/{request_id}/download.json"] = json.dumps(metadata, sort_keys=True,
            separators=(",", ":")).encode()
    else:
        files[f"cache/{request_id}/extra.json"] = b"{}"
    rehash(files)
    with pytest.raises(ValueError):
        verify_download_files(files)


def test_duplicate_json_and_symlink_are_rejected(tmp_path):
    output, _ = create(tmp_path)
    files = captured(output)
    files["configuration.json"] = b'{"symbols":["SPY"],"symbols":["QQQ"]}'
    rehash(files)
    with pytest.raises(ValueError, match="duplicate"):
        verify_download_files(files)
    (output / "link").symlink_to(output / "configuration.json")
    with pytest.raises(ValueError, match="symlink"):
        verify_download_output(output)


def test_compile_contract_is_strict_and_bounded():
    assert compile_download_configuration(config()) == config()
    for invalid in ({**config(), "secret": "x"}, {**config(), "action_free": False},
                    {**config(), "symbols": ["SPY"] * 101}, {**config(), "start": config()["end"]}):
        with pytest.raises(ValueError):
            compile_download_configuration(invalid)
