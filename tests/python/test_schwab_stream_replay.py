from hashlib import sha256
import json

import pytest

from qte.brokers.schwab_stream_replay import normalize

KEY = "SPXW  261120P06500000"
SECOND = "SPXW  261120C06500000"
NOW = 1795185000000


def subscribed(stamp, keys=(KEY,)):
    return {"event": "subscribed", "received_utc_ns": stamp * 1_000_000, "contracts": list(keys)}


def update(stamp, fields, key=KEY):
    return {"event": "data", "received_utc_ns": stamp * 1_000_000, "message": {
        "data": [{"service": "LEVELONE_OPTIONS", "command": "SUBS", "timestamp": stamp,
                  "content": [{"key": key, **fields}]}]}}


def initial():
    return {"2": .9, "3": 1., "16": 10, "17": 8, "33": "Normal", "38": NOW, "delayed": False}


def write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_replay_preserves_clocks_and_invalidates_deltas_across_disconnect(tmp_path):
    source, output = tmp_path / "capture", tmp_path / "report"
    write(source, [
        subscribed(NOW), update(NOW + 1, initial()), update(NOW + 2, {"3": 1.1}),
        update(NOW + 3, {"38": NOW + 2}), update(NOW + 4, {"2": 1.2, "38": NOW + 3}),
        update(NOW + 5, {"2": .95, "38": NOW + 5}),
        {"event": "disconnect", "received_utc_ns": (NOW + 6) * 1_000_000},
        subscribed(NOW + 7), update(NOW + 8, {"38": NOW + 8, "2": .8}),
    ])
    summary = normalize(source, output, [KEY], 5)
    rows = [json.loads(line) for line in output.read_bytes().splitlines()]
    assert summary["source_sha256"] == sha256(source.read_bytes()).hexdigest()
    assert rows[-1] == summary
    assert rows[1]["bid"] == .9
    assert rows[2]["reason"] == "incomplete" and "ask" not in rows[2]
    assert (rows[3]["bid"], rows[3]["ask"], rows[3]["source_quote_ms"]) == (.9, 1.1, NOW + 2)
    assert (rows[3]["provider_packet_ms"], rows[3]["received_utc_ns"]) == (NOW + 3, (NOW + 3) * 1_000_000)
    assert rows[4]["reason"] == "invalid_market"
    assert rows[5]["bid"] == .95 and rows[5]["ask"] == 1.1
    assert rows[8]["reason"] == "incomplete" and rows[8]["epoch"] == 2


@pytest.mark.parametrize(("fields", "reason"), [
    ({"delayed": True}, "delayed"), ({"33": "Halted"}, "trading_status"),
    ({"17": 0}, "invalid_market"), ({"38": NOW - 100}, "stale"),
    ({"38": NOW + 100}, "source_after_receipt"),
])
def test_unavailable_observations_have_no_executable_book(tmp_path, fields, reason):
    source, output = tmp_path / "capture", tmp_path / "report"
    write(source, [subscribed(NOW), update(NOW + 1, {**initial(), **fields})])
    summary = normalize(source, output, [KEY], 10)
    assert summary["unavailable_reasons"] == {reason: 1}
    assert summary["counts"].get("quote", 0) == 0


@pytest.mark.parametrize("bad", [
    update(NOW + 2, {"38": NOW + 1}, key=SECOND), update(NOW - 4, {"38": NOW - 4}),
    update(NOW + 2, {"38": NOW - 1}), subscribed(NOW + 2, [SECOND]),
])
def test_invalid_capture_does_not_publish_partial_result(tmp_path, bad):
    source, output = tmp_path / "capture", tmp_path / "report"
    write(source, [subscribed(NOW), update(NOW + 1, initial()), bad])
    with pytest.raises(ValueError, match="Journal line 3"):
        normalize(source, output, [KEY], 10)
    assert list(tmp_path.iterdir()) == [source]
