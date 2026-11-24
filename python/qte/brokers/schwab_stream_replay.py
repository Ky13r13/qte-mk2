"""Replay a recorded Schwab market-data journal offline, with separate event and receipt clocks."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from qte.adapters.schwab_option_stream import OptionStreamReplay
from qte.json_input import strict_json

_MAX_LINE = 4 * 1024 * 1024


def normalize(capture, output, contracts, max_age_ms):
    replay = OptionStreamReplay(contracts, max_age_ms)
    digest = sha256()
    counts = Counter()
    reasons = Counter()
    lines = 0
    if os.path.lexists(output):
        raise FileExistsError(output)
    with capture.open("rb") as source:
        with NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as staging:
            path = Path(staging.name)
            try:
                def write(row):
                    staging.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")

                while raw := source.readline(_MAX_LINE + 1):
                    lines += 1
                    if len(raw) > _MAX_LINE:
                        raise ValueError(f"Journal line {lines} exceeds the record size limit")
                    digest.update(raw)
                    try:
                        for row in replay.apply(strict_json(raw)):
                            counts[row["event"]] += 1
                            if row["event"] == "unavailable":
                                reasons[row["reason"]] += 1
                            write(row)
                    except ValueError as exc:
                        raise ValueError(f"Journal line {lines}: {exc}") from exc
                if not counts["subscribed"]:
                    raise ValueError("Capture contains no acknowledged subscription")
                summary = dict(event="summary", schema="schwab_option_stream_replay_v1", source_sha256=digest.hexdigest(),
                               records=lines, contracts=sorted(replay.contracts), max_age_ms=max_age_ms,
                               counts=dict(counts), unavailable_reasons=dict(reasons),
                               ended_subscribed=replay.books is not None)
                write(summary)
                staging.flush()
                os.fsync(staging.fileno())
            except BaseException:
                staging.close()
                path.unlink(missing_ok=True)
                raise
        try:
            os.link(path, output)
        finally:
            path.unlink(missing_ok=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True, help="Market-data-only NDJSON capture")
    parser.add_argument("--output", type=Path, required=True, help="New normalized NDJSON output in an existing directory")
    parser.add_argument("--contract", action="append", required=True, help="Exact padded contract symbol; repeat for multiple contracts")
    parser.add_argument("--max-age-ms", type=int, required=True, help="Maximum permitted receipt minus source age")
    args = parser.parse_args(argv)
    summary = normalize(args.capture, args.output, args.contract, args.max_age_ms)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
