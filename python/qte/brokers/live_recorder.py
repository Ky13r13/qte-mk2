"""Record SPX cash index and exact SPXW subscriptions locally, or replay the capture offline."""
import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
import os
import sys
from tempfile import NamedTemporaryFile
import time

from qte.adapters.schwab_live_stream import SpxStreamReplay
from qte.brokers.schwab_auth import AuthError
from qte.brokers.schwab_live_sdk import sdk_factory
from qte.forward_sessions import MAX_LINE, encode
from qte.json_input import strict_json


class Capture:
    """The market-only raw journal is the source of truth. The other files are derived."""
    def __init__(self, output, contracts, max_age_ms, *, desk=None):
        self.replay = SpxStreamReplay(contracts, max_age_ms)
        self.output = Path(output)
        self.desk = Path(desk) if desk else None
        self.counts = Counter()
        self.quotes = {}
        self.last_published = None

    def __enter__(self):
        self.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        with ExitStack() as stack:
            if self.desk:
                if os.name != "posix":
                    raise RuntimeError("Schwab Live Desk publication requires POSIX file locking")
                import fcntl
                self.desk.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(self.desk / ".publisher.lock", os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR, 0o600)
                lock = stack.enter_context(os.fdopen(fd, "r+b"))
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Schwab Live Desk already has an active recorder") from None
            self.raw = stack.enter_context((self.output / "raw.ndjson").open("xb"))
            self.quotes_file = stack.enter_context((self.output / "quotes.ndjson").open("xb"))
            self.publish()
            self.files = stack.pop_all()
        return self

    def __exit__(self, *args):
        self.files.close()

    def append(self, event):
        line = encode(event)
        if len(line) > MAX_LINE:
            raise ValueError("Market packet exceeds journal size limit")
        self.raw.write(line + b"\n")
        self.raw.flush()
        os.fsync(self.raw.fileno())
        publish_now = event["event"] != "data"
        for row in self.replay.apply(event):
            kind = row["event"]
            if kind in ("subscribed", "disconnect"):
                self.quotes.clear()
            elif kind in ("quote", "unavailable"):
                previous = self.quotes.get(row["contract"])
                if previous is None or previous["event"] != kind:
                    publish_now = True
                self.quotes[row["contract"]] = row
            elif kind in ("underlying", "underlying_unavailable") and not self.counts[kind]:
                publish_now = True
            self.counts[kind] += 1
            self.quotes_file.write(encode(row) + b"\n")
        self.quotes_file.flush()
        os.fsync(self.quotes_file.fileno())
        received = event["received_utc_ns"]
        if publish_now or self.last_published is None or received - self.last_published >= 1_000_000_000:
            self.publish()

    def publish(self):
        options = self.replay.options
        status = dict(schema="schwab_live_status_v1", connected=options.books is not None,
                      epoch=options.epoch, received_utc_ns=options.received or None,
                      max_age_ms=options.max_age_ms, counts=dict(self.counts),
                      underlying=self.replay.underlying, quotes=list(self.quotes.values()),
                      contracts=sorted(options.contracts))
        for directory in {self.output, self.desk} - {None}:
            with NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(encode(status))
                    stream.close()
                    temporary.replace(directory / "status.json")
                finally:
                    temporary.unlink(missing_ok=True)
        self.last_published = options.received


async def collect(factory, capture, duration_seconds, max_reconnects=5, *, clock=time.time_ns,
                  sleep=asyncio.sleep):
    if not 0 < duration_seconds <= 86400 or not 0 <= max_reconnects <= 100:
        raise ValueError("Duration must be within 24 hours and max reconnects within 0–100")
    contracts = sorted(capture.replay.options.contracts)
    attempts = 0
    deadline = asyncio.timeout(duration_seconds)
    try:
        async with deadline:
            while True:
                stream = factory()
                active = False
                def callback(packet):
                    try:
                        capture.append(dict(event="data", received_utc_ns=clock(), message={"data": [packet]}))
                    except OSError as exc:
                        # Disk errors inside a synchronous SDK callback must stop the recorder.
                        raise RuntimeError("Could not persist market data") from exc
                try:
                    async with asyncio.timeout(45):
                        await stream.open(contracts, callback)
                    capture.append(dict(event="subscribed", received_utc_ns=clock(), contracts=contracts))
                    active = True
                    while True:
                        # TCP/WebSocket transport liveness; a quiet symbol still ages out by source time.
                        async with asyncio.timeout(90):
                            await stream.read()
                except (ConnectionError, TimeoutError):
                    if attempts >= max_reconnects:
                        raise RuntimeError("Schwab stream reconnect budget exhausted") from None
                    attempts += 1
                finally:
                    try:
                        if active:
                            capture.append(dict(event="disconnect", received_utc_ns=clock()))
                    finally:
                        await stream.close()
                await sleep(min(30, 2 ** (attempts - 1)))
    except TimeoutError:
        if not deadline.expired():
            raise


def replay_journal(source, output, contracts, max_age_ms):
    with Path(source).open("rb") as stream, Capture(output, contracts, max_age_ms) as capture:
        while line := stream.readline(MAX_LINE+2):
            if len(line) > MAX_LINE+1 or not line.endswith(b"\n"):
                raise ValueError("Partial or oversized raw journal record")
            capture.append(strict_json(line))
        if not capture.counts["subscribed"]:
            raise ValueError("No acknowledged subscriptions in source")
        capture.publish()
        return dict(capture.counts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private directory inside repository")
    parser.add_argument("--desk", action="store_true", help="Publish live status to the GUI in build/live-desk/capture")
    parser.add_argument("--contract", required=True, action="append", help="Exact padded SPXW key (repeatable)")
    parser.add_argument("--max-age-ms", type=int, required=True, help="Maximum provider source age at receipt")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--duration-seconds", type=int, help="Live recording duration (1–86400)")
    mode.add_argument("--replay", type=Path, help="Rebuild directly from a saved raw.ndjson, offline")
    parser.add_argument("--max-reconnects", type=int, default=5)
    args = parser.parse_args(argv)
    repo = Path.cwd()
    output = args.output.resolve()
    if not (repo / "AGENTS.md").is_file() or not output.is_relative_to(repo):
        parser.error("Run from the repository root and choose a new output directory inside it")
    if args.replay and args.desk:
        parser.error("--desk requires a live recording")
    if args.replay:
        replay_journal(args.replay, output, args.contract, args.max_age_ms)
        return 0
    if not 0 < args.duration_seconds <= 86400 or not 0 <= args.max_reconnects <= 100:
        parser.error("Duration must be 1–86400 seconds and reconnect budget 0–100")
    desk = (repo / "build/live-desk/capture").resolve() if args.desk else None
    if desk and not desk.is_relative_to(repo):
        parser.error("Live Desk directory must be inside the repository")
    capture = Capture(output, args.contract, args.max_age_ms, desk=desk)
    async def record():
        async with sdk_factory(repo) as factory:
            with capture:
                await collect(factory, capture, args.duration_seconds, args.max_reconnects)
    try:
        asyncio.run(record())
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except (FileExistsError, FileNotFoundError, AuthError, RuntimeError, ValueError) as exc:
        # Only local exceptions with known, credential-free messages may reach the console.
        if type(exc) in (FileExistsError, FileNotFoundError):
            print("Capture path exists or a required local file is missing.", file=sys.stderr)
        elif type(exc) is AuthError or (type(exc) is RuntimeError and str(exc).startswith(("Schwab streaming dependency", "Schwab stream reconnect", "Could not persist market data", "Schwab Live Desk"))):
            print(str(exc), file=sys.stderr)
        else:
            print(f"Capture failed ({type(exc).__name__}). Check subscription keys, data and credentials.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Schwab stream failed ({type(exc).__name__}). Check authorization and streaming entitlements.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
