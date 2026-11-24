"""Generate a labeled, offline desk example from the same public APIs as an observed capture."""
from dataclasses import asdict
from pathlib import Path
import tempfile

from .brokers.live_recorder import replay_journal
from .execution_quality import from_files
from .json_input import strict_json
from .live_outputs import publish
from .live_risk import position_risk
from .live_risk_quotes import apply_recorded_market
from .market_sessions import import_xnys, entry_window


def build_demo(repository, output):
    examples = repository / "examples"
    spec = strict_json((examples / "option-position-risk.json").read_bytes())
    probes = examples / "live-desk/probes.json"
    output = Path(output)
    if output.exists():
        raise ValueError("Demo output already exists. Open the existing demo or select a new directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        root = Path(tmp) / "demo"
        keys = [leg["contract"] for leg in spec["positions"]]
        replay_journal(examples / "live-desk/synthetic-raw.ndjson", root / "capture", keys, spec["max_age_ms"])
        schedule = import_xnys("2026-11-20", "2026-11-20")
        publish(root / "schedule.json", asdict(schedule))
        quotes = root / "capture/quotes.ndjson"
        quality = from_files(probes, root / "schedule.json", quotes)
        publish(root / "quality.json", quality)
        with quotes.open("rb") as stream:
            inputs = apply_recorded_market(spec, (strict_json(line) for line in stream))
        risk = position_risk(inputs)
        publish(root / "risk.json", dict(risk, market_input="synthetic_recorder"))
        policy = entry_window(schedule, now_ns=spec["asof_ns"], product="SPXW_PM", expiry="2026-11-20",
                              hold_ns=300_000_000_000, buffer_ns=60_000_000_000)
        publish(root / "session.json", dict(policy, asof_ns=spec["asof_ns"]))
        root.rename(output)
    return output
