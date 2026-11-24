"""Market-hours forward collector: Schwab REST chains at the open, then quote polls.

Run once per regular session from the repository root, before 09:30 ET:

    .venv/bin/python -m qte.forward_collector --contract examples/schwab-forward-contract.json

The collector is read-only. It never submits orders. It writes an append-only,
fsynced NDJSON journal to ``build/forward/journals/YYYY-MM-DD/journal.ndjson``.
Each response line keeps the raw provider body, its canonical SHA-256 and the
collector's request and receipt clocks. Failures are journaled as gaps.

Polling REST snapshots is not a tick tape. Quotes between polls are unobserved.
See qte.brokers.live_recorder for separate streaming capture of SPX and SPXW
using the optional Schwab SDK.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time

from .forward_sessions import (
    DEFAULT_RULES, NY, SCHEMA, SECOND_NS, chain_queries, digest, encode, et_date, find_opening,
    read_journal, session_bounds, validate_rules,
)

OPENING_DELAY_NS = 10 * SECOND_NS
OPENING_RETRY_NS = 15 * SECOND_NS
CLOSE_GRACE_NS = 15 * SECOND_NS
MAX_BACKOFF_NS = 60 * SECOND_NS
_RECOVERABLE = (OSError, ValueError, RuntimeError)


def collector_sha256() -> str:
    names = ('forward_collector.py', 'forward_sessions.py')
    h = sha256()
    for name in names:
        h.update(Path(__file__).with_name(name).read_bytes())
    return h.hexdigest()


class Journal:
    """Append-only NDJSON writer. Every record is flushed and fsynced before returning."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.seq = 0
        self.last_received = 0
        if self.path.exists():
            existing = read_journal(self.path)
            if existing['truncated_tail_bytes']:
                self._preserve_partial_tail(existing['truncated_tail_bytes'])
            self.seq = len(existing['records'])
            self.last_received = existing['last_received_utc_ns']
            self.existing = existing
        else:
            self.existing = None
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        self.fd = os.open(self.path, flags, 0o600)

    def _preserve_partial_tail(self, size):
        """A crash can leave one partial line. Keep its bytes beside the journal, then cut it."""
        raw = self.path.read_bytes()
        partial = self.path.with_name(f'{self.path.name}.partial-{len(raw)}')
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw[-size:])
            stream.flush()
            os.fsync(stream.fileno())
        with self.path.open('r+b') as stream:
            stream.truncate(len(raw) - size)
            stream.flush()
            os.fsync(stream.fileno())

    def write(self, record: dict) -> dict:
        record = dict(record, seq=self.seq)
        if record['received_utc_ns'] < self.last_received:
            raise RuntimeError('collector clock moved backward; journal stopped')
        line = encode(record) + b'\n'
        view = memoryview(line)
        while view:
            written = os.write(self.fd, view)
            view = view[written:]
        os.fsync(self.fd)
        self.seq += 1
        self.last_received = record['received_utc_ns']
        return record

    def close(self):
        os.close(self.fd)


class ForwardCollector:
    """One session. ``client_provider()`` returns a client with ``get`` and ``contract_sha256``."""

    def __init__(self, journal: Journal, client_provider, *, session_date: date, rules: dict,
                 close_et: str, interval_ns: int, clock=time.time_ns, sleep=time.sleep,
                 log=lambda message: None):
        self.journal = journal
        self.client_provider = client_provider
        self.session_date = session_date
        self.rules = validate_rules(dict(rules))
        if close_et not in self.rules['allowed_close_et']:
            raise ValueError('declared close is not an allowed session close')
        self.close_et = close_et
        self.open_ns, self.close_ns = session_bounds(session_date, self.rules['open_et'], close_et)
        if type(interval_ns) is not int or not SECOND_NS <= interval_ns <= 60 * SECOND_NS:
            raise ValueError('poll interval must be 1-60 seconds')
        self.interval_ns = interval_ns
        self.clock, self.sleep, self.log = clock, sleep, log
        self.records = list(journal.existing['records']) if journal.existing else []

    # -- clock helpers
    def _now(self):
        return self.clock()

    def _sleep_until(self, target_ns):
        while (remaining := target_ns - self._now()) > 0:
            self.sleep(min(remaining, 30 * SECOND_NS) / SECOND_NS)

    def _write(self, record):
        written = self.journal.write(record)
        self.records.append(written)
        return written

    # -- requests
    def _exchange(self, operation, query):
        started = self._now()
        try:
            client = self.client_provider()
            response = client.get(operation, query_parameters=query)
        except _RECOVERABLE as error:
            # Messages from the read-only client omit bodies and credentials.
            text = type(error).__name__ + (': ' + str(error)[:300] if type(error).__name__ in (
                'BrokerConnectionError', 'AuthError') else '')
            return self._write(dict(event='error', received_utc_ns=max(self._now(), started),
                                    request_started_utc_ns=started, operation=operation,
                                    query=query, error=text))
        received = self._now()
        return self._write(dict(event='response', received_utc_ns=received, request_started_utc_ns=started,
                                operation=operation, query=query, response_sha256=digest(response),
                                response=response))

    def _start_record(self, contract_sha256):
        base = dict(received_utc_ns=self._now())
        if self.journal.existing is None:
            return self._write(dict(base, event='start', schema=SCHEMA,
                session_date=self.session_date.isoformat(), underlying=self.rules['underlying'],
                open_et=self.rules['open_et'], close_et=self.close_et,
                interval_ms=self.interval_ns // 1_000_000, contract_sha256=contract_sha256,
                rules=self.rules, collector_sha256=collector_sha256()))
        start = self.journal.existing['start']
        if (start['session_date'], start['rules'], start['close_et'], start['interval_ms']) != (
                self.session_date.isoformat(), self.rules, self.close_et, self.interval_ns // 1_000_000):
            raise ValueError('existing journal was started with different session settings')
        if any(r['event'] == 'stop' and r['reason'] == 'session_close' for r in self.records):
            raise ValueError('existing journal already completed this session')
        return self._write(dict(base, event='resume', collector_sha256=collector_sha256()))

    def _opening(self):
        recorded = [r for r in self.records if r['event'] == 'selection']
        if recorded:
            return recorded[-1]
        today_query, weekly_query = chain_queries(self.session_date, self.rules)
        deadline = self.open_ns + self.rules['opening_deadline_s'] * SECOND_NS
        self._sleep_until(self.open_ns + OPENING_DELAY_NS)
        while self._now() < deadline:
            self._exchange('optionChain', today_query)
            self._exchange('optionChain', weekly_query)
            opening, reasons = find_opening(self.records, session_date=self.session_date,
                                            open_ns=self.open_ns, close_ns=self.close_ns, rules=self.rules)
            if opening is not None:
                return self._write(dict(event='selection', received_utc_ns=self._now(),
                                        **{k: opening[k] for k in ('weekly', 'contract_terms',
                                                                   'today_seq', 'weekly_seq')}))
            if reasons:
                self.log(f'opening snapshot rejected: {reasons[-1]}')
            self.sleep(OPENING_RETRY_NS / SECOND_NS)
        return None

    def run(self) -> dict:
        if self._now() > self.close_ns:
            # Refuse before touching the journal: a finished session's bytes must not change.
            raise ValueError('session already closed')
        client = self.client_provider()
        self._start_record(client.contract_sha256)
        reason = 'session_close'
        try:
            if self._now() > self.close_ns:
                raise ValueError('session already closed')
            selection = self._opening()
            if selection is None:
                reason = 'no_opening_selection'
                return self._finish(reason)
            weekly = selection['weekly']
            query = dict(symbols=','.join((self.rules['underlying'], weekly['call_contract_id'],
                                           weekly['put_contract_id'])))
            self.log(f"polling {query['symbols']} every {self.interval_ns / SECOND_NS:g}s")
            backoff = 0
            next_tick = max(self._now(), self.open_ns)
            while self._now() <= self.close_ns + CLOSE_GRACE_NS:
                self._sleep_until(next_tick)
                record = self._exchange('quotes', query)
                if record['event'] == 'error':
                    backoff = min(MAX_BACKOFF_NS, max(self.interval_ns, 2 * backoff))
                    self.log(f"poll failed ({record['error']}); retrying in {backoff / SECOND_NS:g}s")
                    next_tick = self._now() + backoff
                else:
                    backoff = 0
                    next_tick += self.interval_ns
                    # Do not burst to catch up after a stall; resume the cadence from now.
                    if next_tick <= self._now():
                        next_tick = self._now() + self.interval_ns
        except KeyboardInterrupt:
            reason = 'interrupted'
        except Exception:
            reason = 'fatal_error'
            self._finish(reason)
            raise
        return self._finish(reason)

    def _finish(self, reason):
        self._write(dict(event='stop', received_utc_ns=self._now(), reason=reason))
        polls = [r for r in self.records if r.get('operation') == 'quotes']
        return dict(journal=str(self.journal.path), reason=reason, records=len(self.records),
                    quote_polls=len(polls), poll_errors=sum(r['event'] == 'error' for r in polls))


# ----------------------------------------------------------------------------- CLI

def _token_client_provider(repo: Path, contract: dict, *, refresh_credentials):
    """Load the local token; refresh shortly before expiry if credentials were entered."""
    from .brokers.schwab_auth import AuthError, app_fingerprint, exchange, market_client, TokenStore
    import secrets
    store = TokenStore(repo)
    state = dict(client=None, expires=0.0)

    def provider():
        if state['client'] is not None and state['expires'] > time.time() + 120:
            return state['client']
        document = store.load()
        if document['expires_at'] <= time.time() + 120:
            if refresh_credentials is None:
                raise AuthError('Access token is expiring and no refresh credentials were entered.')
            key, secret = refresh_credentials
            if not secrets.compare_digest(document['app_sha256'], app_fingerprint(key)):
                raise AuthError('App key differs from the stored authorization.')
            document = exchange(key, secret, refresh_token=document['token']['refresh_token'])
            store.save(document)
        state['client'] = market_client(document, contract)
        state['expires'] = document['expires_at']
        return state['client']
    return store, provider


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--contract', type=Path, required=True,
                        help='Market-data OpenAPI allowlist containing optionChain and quotes GET operations')
    parser.add_argument('--output-root', type=Path, default=Path('build/forward/journals'))
    parser.add_argument('--lock', type=Path,
                        help='Protocol lock whose builder rules the journal must use (default: built-in rules)')
    parser.add_argument('--close-et', default=None, help='Declared session close, e.g. 13:00 on an early-close day')
    parser.add_argument('--interval-s', type=int, default=5, help='Quote poll interval (1-60 s)')
    parser.add_argument('--no-refresh', action='store_true',
                        help='Do not prompt for app credentials; the run stops polling when the token expires')
    args = parser.parse_args(argv)
    try:
        rules = dict(DEFAULT_RULES)
        today = et_date(time.time_ns())
        if today.weekday() >= 5:
            raise ValueError('today is a weekend in New York; no regular session')
        close_et = args.close_et
        if args.lock:
            from .forward_protocol import load_lock, lock_close
            lock = load_lock(args.lock)
            rules = lock['rules']
            locked = lock_close(lock, today)
            if locked is None:
                print(f'note: {today} is not a session of study {lock["study_id"]}; collecting anyway',
                      file=sys.stderr)
            elif close_et not in (None, locked):
                raise ValueError(f'the lock calendar closes {today} at {locked}, not {close_et}')
            else:
                close_et = locked
        validate_rules(rules)
        contract = json.loads(args.contract.read_text())
        credentials = None
        if not args.no_refresh:
            from .brokers.schwab_auth import credentials as prompt
            print('Schwab access tokens expire about every 30 minutes. Enter app credentials once so '
                  'the collector can refresh the local token. They are kept in memory only.')
            credentials = prompt()
        store, provider = _token_client_provider(args.repo_root, contract, refresh_credentials=credentials)
        directory = args.output_root / today.isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        with store.lock():
            journal = Journal(directory / 'journal.ndjson')
            try:
                collector = ForwardCollector(journal, provider, session_date=today, rules=rules,
                                             close_et=close_et or rules['close_et'],
                                             interval_ns=args.interval_s * SECOND_NS,
                                             log=lambda m: print(datetime.now(NY).strftime('%H:%M:%S ') + m,
                                                                 flush=True))
                summary = collector.run()
            finally:
                journal.close()
        print(json.dumps(summary, indent=2))
        return 0
    except KeyboardInterrupt:
        print('qte forward collector: canceled before collection started.', file=sys.stderr)
    except Exception as error:  # report a sanitized reason and fail closed
        print(f'qte forward collector: {type(error).__name__}: {error}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
