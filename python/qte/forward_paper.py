"""Durable paper-trading ledger: the locked candidate slate run forward on collected sessions.

    .venv/bin/python -m qte.forward_paper update --study-id spy-weekly-v1   # after each close
    .venv/bin/python -m qte.forward_paper report --study-id spy-weekly-v1

Every registered candidate keeps the parameters recorded in the study lock.
After each locked session settles, ``update`` builds that session from its own
journal, replays all candidates under the base and stress costs, and appends
one hash-chained record to ``paper-ledger.ndjson``. That record holds the paper
fills, any write-offs and the cumulative P&L. Sessions without a usable journal
are recorded as excluded, so the ledger has no silent gaps.

On every start the ledger verifies its hash chain. It then recomputes every
recorded session from the journals and halts on any difference. A changed or
back-filled journal, or changed scoring code, stops the run. It never rewrites
history.

Fills are simulated from observed quotes: buys pay the later ask and sells
take the first eligible bid after the hold, as in ``weekly_reversal``. They are
not broker paper-account fills, and no order leaves this process. This ledger
is the only accounting authority for the forward study. It does not use the
C++ portfolio, which has no options support.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

from .forward_protocol import (
    ProtocolError, SETTLE_NS, _bounds, check_code, read_frozen, read_lock, load_session,
)
from .forward_sessions import digest, encode
from .json_input import strict_json

SCHEMA = 'qte-forward-paper-ledger-v1'
LEDGER = 'paper-ledger.ndjson'


class LedgerMismatch(RuntimeError):
    """Recorded history disagrees with a fresh recomputation. Investigate; do not delete."""


def _seal(record: dict) -> dict:
    return dict(record, sha256=digest(record))


def _session_results(lock: dict, session: dict | None) -> dict:
    """Per candidate and cost: paper fills, write-offs and session P&L from one session."""
    from .weekly_reversal import replay_weekly_reversal

    selection = lock['selection']
    results = {name: {} for name in lock['paper_candidates']}
    for cost in selection['costs']:
        if session is None:
            for name in results:
                results[name][cost] = dict(fills=[], session_net_pnl=0.0, closed_trades=0, unresolved=0)
            continue
        replay = replay_weekly_reversal(dict(sessions=[session], news_flags=[], costs=selection['costs'],
                                             cost_scenario=cost,
                                             max_debit_dollars=selection['max_debit_plus_roundtrip_fees'],
                                             multiplier=selection['multiplier']))
        rows = {row['id']: row for row in replay['candidates']}
        for name in results:
            row = rows[name]
            fills = []
            for trade in sorted(row['trades'] + row['unresolved'], key=lambda t: t['entry_ns']):
                fills.append(dict(action='buy_to_open', contract_id=trade['contract_id'], quantity=1,
                                  time_ns=trade['entry_ns'], quote_ns=trade['entry_quote_ns'],
                                  quote_ask=trade['entry_ask'], cash=-trade['entry_debit']))
                if 'exit_ns' in trade:
                    fills.append(dict(action='sell_to_close', contract_id=trade['contract_id'], quantity=1,
                                      time_ns=trade['exit_ns'], quote_ns=trade['exit_quote_ns'],
                                      quote_bid=trade['exit_bid'], cash=trade['exit_credit']))
                else:
                    # No eligible exit bid: mark the contract at zero and charge the reserved exit fee.
                    fills.append(dict(action='write_off_unresolved', contract_id=trade['contract_id'],
                                      quantity=1, time_ns=trade['deadline_ns'], reason=trade['reason'],
                                      cash=-(trade['risk_deployed'] - trade['entry_debit'])))
            net = math.fsum(f['cash'] for f in fills)
            expected = row['aggregate']['net_pnl']
            if not math.isclose(net, expected, rel_tol=0, abs_tol=1e-6):
                raise LedgerMismatch(f'{name}/{cost}: fills sum to {net}, replay reports {expected}')
            results[name][cost] = dict(fills=fills, session_net_pnl=net,
                                       closed_trades=len(row['trades']), unresolved=len(row['unresolved']))
    return results


def expected_records(lock: dict, lock_sha: str, journal_root: Path, *, now_ns: int):
    """Yield the ledger records that should exist now, in order. Pure apart from journal reads."""
    previous = _seal(dict(schema=SCHEMA, kind='open', seq=0, prev_sha256=None, study_id=lock['study_id'],
                          lock_sha256=lock_sha, candidates=lock['paper_candidates'],
                          costs=lock['selection']['costs'],
                          fill_policy='later ask to open, first eligible bid after five minutes to close; '
                                      'unresolved exits marked at zero'))
    yield previous
    totals = {name: {cost: dict(net_pnl=0.0, closed_trades=0, unresolved=0, sessions=0)
                     for cost in lock['selection']['costs']} for name in lock['paper_candidates']}
    for entry in lock['sessions']:
        if now_ns < _bounds(entry, lock['rules'])[1] + SETTLE_NS:
            return
        session, info = load_session(lock, journal_root, entry)
        results = _session_results(lock, session)
        for name, by_cost in results.items():
            for cost, row in by_cost.items():
                total = totals[name][cost]
                total['net_pnl'] = total['net_pnl'] + row['session_net_pnl']
                total['closed_trades'] += row['closed_trades']
                total['unresolved'] += row['unresolved']
                total['sessions'] += session is not None
                row['cumulative'] = dict(total)
        previous = _seal(dict(kind='session', seq=previous['seq'] + 1, prev_sha256=previous['sha256'],
                              date=entry['date'], phase=entry['phase'],
                              status='recorded' if session is not None else 'excluded',
                              journal_sha256=info.get('journal_sha256'), excluded=info.get('excluded'),
                              results=results))
        yield previous


def read_ledger(path: Path, *, recover_partial: bool = True) -> list[dict]:
    """Verify sequence and hash chain. A crash-truncated final line is preserved beside the file."""
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines = raw.split(b'\n')
    tail = lines.pop()
    if tail:
        if not recover_partial:
            raise LedgerMismatch("Ledger has a partial final record. Preserve the file and investigate.")
        partial = path.with_name(f'{path.name}.partial-{len(raw)}')
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(tail)
            os.fsync(stream.fileno())
        with path.open('r+b') as stream:
            stream.truncate(len(raw) - len(tail))
            os.fsync(stream.fileno())
    records, previous = [], None
    for number, line in enumerate(lines):
        record = strict_json(line)
        if type(record) is not dict:
            raise LedgerMismatch(f'ledger line {number}: not an object')
        body = dict(record)
        claimed = body.pop('sha256', None)
        if (record.get('seq') != number or claimed != digest(body)
                or record.get('prev_sha256') != (previous['sha256'] if previous else None)):
            raise LedgerMismatch(f'ledger line {number}: broken sequence or hash chain')
        records.append(record)
        previous = record
    return records


def append_session(path, schema, body):
    """Append one immutable session using the forward paper ledger sequence and seal."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        records = read_ledger(path, recover_partial=False)
        for existing in records:
            if existing.get("schema") != schema:
                raise LedgerMismatch("Ledger schema mismatch")
            if existing["date"] == body["date"]:
                return existing, False
        record = _seal(dict(body, schema=schema, seq=len(records),
                            prev_sha256=records[-1]["sha256"] if records else None))
        view = memoryview(encode(record)+b"\n")
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        return record, True
    finally:
        os.close(fd)


def update(study_dir: Path, journal_root: Path, *, now_ns: int) -> dict:
    study_dir = Path(study_dir)
    lock, lock_sha = read_lock(study_dir / 'lock.json')
    check_code(lock)
    path = study_dir / LEDGER
    recorded = read_ledger(path)
    appended = 0
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        index = 0
        for record in expected_records(lock, lock_sha, journal_root, now_ns=now_ns):
            if index < len(recorded):
                if recorded[index] != record:
                    label = record.get('date', 'header')
                    raise LedgerMismatch(f'ledger entry {index} ({label}) differs from a fresh recomputation; '
                                         'a journal or the code changed after it was recorded')
            else:
                view = memoryview(encode(record) + b'\n')
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
                appended += 1
            index += 1
        if index < len(recorded):
            raise LedgerMismatch('the ledger records sessions that have not settled yet')
    finally:
        os.close(fd)
    return dict(ledger=str(path), entries=index, appended=appended, reconciled=len(recorded))


def report(study_dir: Path) -> dict:
    """Cumulative paper P&L per candidate and phase, from a verified ledger only."""
    study_dir = Path(study_dir)
    lock, lock_sha = read_lock(study_dir / 'lock.json')
    records = read_ledger(study_dir / LEDGER)
    if not records or records[0].get('lock_sha256') != lock_sha:
        raise ProtocolError('no ledger for this lock; run update first')
    frozen = None
    if (study_dir / 'frozen.json').exists():
        frozen = read_frozen(study_dir, lock, lock_sha)[0]['frozen']['candidate_id']
    phases = {}
    sessions = [r for r in records if r['kind'] == 'session']
    for record in sessions:
        bucket = phases.setdefault(record['phase'], {})
        for name, by_cost in record['results'].items():
            for cost, row in by_cost.items():
                cell = bucket.setdefault(name, {}).setdefault(
                    cost, dict(net_pnl=0.0, closed_trades=0, unresolved=0))
                cell['net_pnl'] += row['session_net_pnl']
                cell['closed_trades'] += row['closed_trades']
                cell['unresolved'] += row['unresolved']
    last = sessions[-1]['results'] if sessions else {}
    return dict(study_id=lock['study_id'], ledger_head_sha256=records[-1]['sha256'],
                sessions_recorded=sum(r['status'] == 'recorded' for r in sessions),
                sessions_excluded=sum(r['status'] == 'excluded' for r in sessions),
                frozen_candidate=frozen,
                cumulative={name: {cost: row['cumulative'] for cost, row in by_cost.items()}
                            for name, by_cost in last.items()},
                by_phase=phases,
                caution=f"{len(lock['paper_candidates'])} candidates run in parallel; the best one is a "
                'multiple-comparison winner. Only the frozen candidate on evaluation dates is the '
                'pre-registered test. Quote-replay fills, not broker fills.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('update', 'report'))
    parser.add_argument('--study-id', required=True)
    parser.add_argument('--studies-root', type=Path, default=Path('build/forward/studies'))
    parser.add_argument('--journal-root', type=Path, default=Path('build/forward/journals'))
    args = parser.parse_args(argv)
    study_dir = args.studies_root / args.study_id
    try:
        if args.command == 'update':
            result = update(study_dir, args.journal_root, now_ns=time.time_ns())
        else:
            result = report(study_dir)
    except (OSError, ValueError, KeyError, TypeError, LedgerMismatch) as error:
        print(f'qte forward paper: {type(error).__name__}: {error}', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
