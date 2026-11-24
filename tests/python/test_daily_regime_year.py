import json

import pytest

from examples.daily_regime_year import Inputs, YearPanel


def test_year_replay_refuses_to_write_under_capture_or_reuse_an_output(tmp_path):
    inputs = Inputs(*(tmp_path / name for name in ("archive", "daily", "minute", "prior")))
    with pytest.raises(ValueError, match="outside input"):
        inputs.protect(inputs.archive / "another-run")
    existing = tmp_path / "old-run"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        inputs.protect(existing)


def test_year_panel_rejects_unknown_frequency_before_reading_bars(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "wrong.json").write_text(json.dumps({"request": {"interval": "5minute"}, "response": {}}))
    with pytest.raises(ValueError, match="unsupported Robinhood interval"):
        YearPanel(Inputs(archive=tmp_path)).load()
