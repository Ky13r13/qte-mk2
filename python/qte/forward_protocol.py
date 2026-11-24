"""Locked forward protocol for the options-master agents on real market-hours captures.

The study runs in four steps, each from the repository root:

    .venv/bin/python -m qte.forward_protocol lock --study-id spy-weekly-v1
    .venv/bin/python -m qte.forward_collector --contract examples/schwab-forward-contract.json \\
        --lock build/forward/studies/spy-weekly-v1/lock.json          # every session
    .venv/bin/python -m qte.forward_protocol freeze --study-id spy-weekly-v1   # after development
    .venv/bin/python -m qte.forward_protocol evaluate --study-id spy-weekly-v1 # after evaluation

``lock`` fixes everything that could otherwise be tuned after seeing data. That
covers the builder rules, the exchange calendar, the development, buffer and
evaluation session dates, the candidate slate, costs, gates and the SHA-256 of
the scoring code. It must be written before the first development session
opens. ``freeze`` reads only development-date journals. Optional agents
nominate registered candidates, and the fixed tournament selects one. The
result is written once, before the first evaluation session opens. The buffer
session(s) between the two windows give time to freeze. ``evaluate`` replays
the frozen candidate once over the evaluation dates and refuses to run again.

This is a protocol, not tamper-proofing. Files are ordinary local files. Commit
or otherwise timestamp ``lock.json`` and ``frozen.json`` externally if third
parties must trust the ordering.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import time

from .forward_sessions import (
    DEFAULT_RULES, NY, SECOND_NS, JournalError, build_session, digest, encode, read_journal,
    session_bounds, validate_rules,
)
from .json_input import strict_json

LOCK_SCHEMA = 'qte-forward-lock-v1'
FROZEN_SCHEMA = 'qte-forward-frozen-v1'
EVALUATION_SCHEMA = 'qte-forward-evaluation-v1'
PHASES = ('development', 'buffer', 'evaluation')
SETTLE_NS = 20 * 60 * SECOND_NS  # the collector stops 15 s after the close; leave a margin
STUDY_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')
SCORING_CODE = ('options_master.py', 'weekly_reversal.py', 'weekly_reversal_suite.py',
                'forward_sessions.py', 'forward_protocol.py', 'forward_paper.py')
MAX_FILE = 64 * 1024 * 1024

# NYSE full-day closures and 13:00 early closes. Verify against the exchange's
# published calendar before locking; the lock records the resolved dates.
NYSE_HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19',
    '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25',
    '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18',
    '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24',
))
NYSE_EARLY_CLOSES = frozenset(date.fromisoformat(d) for d in ('2026-11-27', '2026-12-24', '2027-11-26'))
CALENDAR_YEARS = (2026, 2027)


class ProtocolError(ValueError):
    """The requested step would break the locked protocol."""


# ------------------------------------------------------------------- calendar

def is_session(day: date) -> bool:
    if day.year not in CALENDAR_YEARS:
        raise ProtocolError(f'no built-in exchange calendar for {day.year}')
    return day.weekday() < 5 and day not in NYSE_HOLIDAYS


def session_close(day: date, rules: dict) -> str:
    close = '13:00' if day in NYSE_EARLY_CLOSES else rules['close_et']
    if close not in rules['allowed_close_et']:
        raise ProtocolError(f'{day} closes at {close}, which the builder rules do not allow')
    return close


def sessions_from(first: date, count: int) -> list[date]:
    days, day = [], first
    while len(days) < count:
        if is_session(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def next_session(now_ns: int, rules: dict) -> date:
    """The first session whose open is still in the future."""
    day = datetime.fromtimestamp(now_ns / SECOND_NS, NY).date()
    while not (is_session(day) and session_bounds(day, rules['open_et'], session_close(day, rules))[0] > now_ns):
        day += timedelta(days=1)
    return day


# ------------------------------------------------------------------- files

def code_hashes() -> dict:
    here = Path(__file__).parent
    return {name: sha256((here / name).read_bytes()).hexdigest() for name in SCORING_CODE}


def write_exclusive(path: Path, value) -> str:
    """Publish ``value`` at ``path`` only if nothing exists there. Returns the file SHA-256."""
    path = Path(path)
    raw = encode(value) + b'\n'
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ProtocolError(f'{path.name} already exists; protocol files are written once') from None
    finally:
        temporary.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return sha256(raw).hexdigest()


def read_document(path: Path, schema: str) -> tuple[dict, str]:
    path = Path(path)
    if path.is_symlink():
        raise ProtocolError(f'{path.name} must not be a symlink')
    raw = path.read_bytes()
    if len(raw) > MAX_FILE:
        raise ProtocolError(f'{path.name} exceeds the size limit')
    document = strict_json(raw)
    if type(document) is not dict or document.get('schema') != schema:
        raise ProtocolError(f'{path.name} is not a {schema} document')
    return document, sha256(raw).hexdigest()


# ------------------------------------------------------------------- lock

def build_lock(*, study_id: str, now_ns: int, first_session: date | None = None,
               development: int = 20, buffer: int = 1, evaluation: int = 30,
               max_missing_evaluation_fraction: float = 0.1, rules: dict | None = None) -> dict:
    from .options_master import (MAX_REVISIONS, MIN_DESIGN_TRADES, MIN_VALIDATION_SESSIONS,
                                 MIN_VALIDATION_TRADES)
    from .weekly_reversal import CANDIDATE_DEFINITIONS, MAX_ENTRIES
    from .weekly_reversal_suite import COSTS, MAX_DEBIT, MULTIPLIER

    if type(study_id) is not str or not STUDY_ID.fullmatch(study_id):
        raise ProtocolError('study id must be 1-63 lowercase letters, digits or hyphens')
    rules = validate_rules(deepcopy(rules if rules is not None else DEFAULT_RULES))
    for name, value, low in (('development', development, 1), ('buffer', buffer, 1),
                             ('evaluation', evaluation, MIN_VALIDATION_SESSIONS)):
        if type(value) is not int or not low <= value <= 250:
            raise ProtocolError(f'{name} sessions must be an integer in [{low}, 250]')
    if type(max_missing_evaluation_fraction) not in (int, float) or not 0 <= max_missing_evaluation_fraction < 1:
        raise ProtocolError('max missing evaluation fraction must be in [0, 1)')
    first = first_session or next_session(now_ns, rules)
    if not is_session(first):
        raise ProtocolError(f'{first} is not an exchange session')
    days = sessions_from(first, development + buffer + evaluation)
    phases = ['development'] * development + ['buffer'] * buffer + ['evaluation'] * evaluation
    sessions = [dict(date=d.isoformat(), close_et=session_close(d, rules), phase=p) for d, p in zip(days, phases)]
    first_open = _bounds(sessions[0], rules)[0]
    if now_ns >= first_open:
        raise ProtocolError('the lock must be written before the first development session opens')
    first_evaluation = next(s for s in sessions if s['phase'] == 'evaluation')
    return dict(
        schema=LOCK_SCHEMA, study_id=study_id, created_utc_ns=now_ns, rules=rules, sessions=sessions,
        freeze_deadline_utc_ns=_bounds(first_evaluation, rules)[0],
        max_missing_evaluation_fraction=float(max_missing_evaluation_fraction),
        selection=dict(candidate_definitions=deepcopy(list(CANDIDATE_DEFINITIONS)), costs=deepcopy(COSTS),
                       max_debit_plus_roundtrip_fees=MAX_DEBIT, multiplier=MULTIPLIER, quantity=1,
                       max_entries_per_session=MAX_ENTRIES, max_accepted_revisions=MAX_REVISIONS,
                       min_design_closed_trades=MIN_DESIGN_TRADES,
                       min_validation_closed_trades=MIN_VALIDATION_TRADES,
                       min_validation_sessions=MIN_VALIDATION_SESSIONS),
        paper_candidates=[c['id'] for c in CANDIDATE_DEFINITIONS if c['id'] != 'cash'],
        news='No news feed is collected; news_flags are empty, so regime gates veto only observed excursion shocks.',
        calendar_source='qte.forward_protocol built-in NYSE table; verify before relying on it',
        code_sha256=code_hashes(),
    )


def _bounds(session: dict, rules: dict) -> tuple[int, int]:
    return session_bounds(date.fromisoformat(session['date']), rules['open_et'], session['close_et'])


def validate_lock(lock: dict) -> dict:
    keys = {'schema', 'study_id', 'created_utc_ns', 'rules', 'sessions', 'freeze_deadline_utc_ns',
            'max_missing_evaluation_fraction', 'selection', 'paper_candidates', 'news',
            'calendar_source', 'code_sha256'}
    if set(lock) != keys or lock['schema'] != LOCK_SCHEMA or not STUDY_ID.fullmatch(str(lock['study_id'])):
        raise ProtocolError('malformed lock')
    rules = validate_rules(lock['rules'])
    sessions = lock['sessions']
    if type(sessions) is not list or not sessions:
        raise ProtocolError('lock lists no sessions')
    previous, order = None, []
    for s in sessions:
        if type(s) is not dict or set(s) != {'date', 'close_et', 'phase'} or s['phase'] not in PHASES:
            raise ProtocolError('malformed lock session')
        day = date.fromisoformat(s['date'])
        if previous is not None and day <= previous:
            raise ProtocolError('lock sessions must be strictly increasing')
        if s['close_et'] not in rules['allowed_close_et']:
            raise ProtocolError('lock session close is not allowed by its rules')
        previous = day
        if not order or order[-1] != s['phase']:
            order.append(s['phase'])
    if order != list(PHASES):
        raise ProtocolError('lock sessions must run development, buffer, evaluation')
    first_evaluation = next(s for s in sessions if s['phase'] == 'evaluation')
    if (lock['freeze_deadline_utc_ns'] != _bounds(first_evaluation, rules)[0]
            or type(lock['created_utc_ns']) is not int
            or lock['created_utc_ns'] >= _bounds(sessions[0], rules)[0]):
        raise ProtocolError('lock timing is inconsistent')
    return lock


def read_lock(path: Path) -> tuple[dict, str]:
    lock, lock_sha = read_document(path, LOCK_SCHEMA)
    return validate_lock(lock), lock_sha


def load_lock(path: Path) -> dict:
    return read_lock(path)[0]


def lock_close(lock: dict, day: date) -> str | None:
    return next((s['close_et'] for s in lock['sessions'] if s['date'] == day.isoformat()), None)


def check_code(lock: dict):
    current = code_hashes()
    changed = sorted(name for name in SCORING_CODE if current.get(name) != lock['code_sha256'].get(name))
    if changed:
        raise ProtocolError('scoring code changed since the lock: ' + ', '.join(changed)
                            + '. Start a new study with a new lock and fresh sessions.')


def phase_sessions(lock: dict, phase: str) -> list[dict]:
    return [s for s in lock['sessions'] if s['phase'] == phase]


def phase_closed(lock: dict, phase: str, now_ns: int) -> bool:
    return now_ns >= _bounds(phase_sessions(lock, phase)[-1], lock['rules'])[1] + SETTLE_NS


# ------------------------------------------------------------------- session loading

def load_session(lock: dict, journal_root: Path, entry: dict):
    """Build one locked session from its own journal only. Never lists other dates."""
    path = Path(journal_root) / entry['date'] / 'journal.ndjson'
    base = dict(session_date=entry['date'], phase=entry['phase'])
    if not path.is_file() or path.is_symlink():
        return None, dict(base, excluded='missing_journal')
    try:
        journal = read_journal(path)
    except (JournalError, OSError) as error:
        return None, dict(base, excluded='invalid_journal', detail=str(error)[:300])
    start = journal['start']
    if start['session_date'] != entry['date']:
        return None, dict(base, excluded='journal_date_mismatch', journal_sha256=journal['sha256'])
    if start['received_utc_ns'] <= lock['created_utc_ns']:
        return None, dict(base, excluded='journal_predates_lock', journal_sha256=journal['sha256'])
    if start['close_et'] != entry['close_et']:
        return None, dict(base, excluded='close_differs_from_lock_calendar', journal_sha256=journal['sha256'])
    try:
        session, diagnostics = build_session(journal, lock['rules'])
    except JournalError as error:
        return None, dict(base, excluded='inconsistent_journal', detail=str(error)[:300],
                          journal_sha256=journal['sha256'])
    return session, dict(base, **diagnostics)


def load_phase(lock: dict, journal_root: Path, phase: str):
    sessions, diagnostics = [], []
    for entry in phase_sessions(lock, phase):
        session, info = load_session(lock, journal_root, entry)
        diagnostics.append(info)
        if session is not None:
            sessions.append(session)
    return sessions, diagnostics


# ------------------------------------------------------------------- freeze

def freeze(study_dir: Path, journal_root: Path, *, now_ns: int, research=None) -> dict:
    """Development-only selection. ``research(stage)`` may wrap the stage with agents.

    ``stage(propose=None)`` runs the development replay and fixed tournament
    and returns its record. ``research`` must call it exactly once and return
    ``dict(backtest=<stage record>, agents=[...], picks=[...])``, which is the
    contract of ``qte.gui.agent_research.run_research(master=...)``.
    """
    from .options_master import REFERENCES, build_protocol, develop
    from .weekly_reversal import CANDIDATE_DEFINITIONS

    study_dir = Path(study_dir)
    lock, lock_sha = read_lock(study_dir / 'lock.json')
    check_code(lock)
    if (study_dir / 'frozen.json').exists():
        raise ProtocolError('the study is already frozen')
    if not phase_closed(lock, 'development', now_ns):
        raise ProtocolError('the development window has not finished')
    if now_ns >= lock['freeze_deadline_utc_ns']:
        raise ProtocolError('the freeze deadline (first evaluation open) has passed; this study is void')
    sessions, diagnostics = load_phase(lock, journal_root, 'development')
    evaluation_ids = [dict(id=s['date']) for s in phase_sessions(lock, 'evaluation')]
    protocol = build_protocol(sessions, [], evaluation_ids, evidence='locked_forward_capture',
                              source_sha256=digest(dict(sessions=sessions, news_flags=[])),
                              holdout_status='evaluation dates fixed by lock ' + lock_sha
                              + '; frozen before the first evaluation session opened')
    calls = []

    def stage(propose=None):
        if calls:
            raise ProtocolError('the development stage runs once')
        calls.append(True)
        if not sessions:
            protocol_view = {k: v for k, v in protocol.items() if k not in {'source_sha256', 'split'}}
            frozen = dict(candidate_id='cash', definition=next(deepcopy(c) for c in CANDIDATE_DEFINITIONS
                                                                if c['id'] == 'cash'),
                          development_protocol_sha256=digest(protocol_view),
                          development_sha256=protocol['development_sha256'],
                          selection_basis='no admitted development sessions; cash by rule',
                          revisions=[], live_enabled=False)
            frozen['sha256'] = digest(frozen)
            return dict(dev_raw={}, design={}, failures=[], nominations=None, selected='cash', revisions=[],
                        frozen=frozen, references=REFERENCES,
                        review_context=dict(protocol=protocol_view, failures=[],
                                            instruction='No development sessions were admitted.'))
        return dict(develop(sessions, [], protocol, propose), references=REFERENCES)

    if research is None:
        record, agents, picks = stage(), [], []
    else:
        outcome = research(stage)
        record, agents, picks = outcome['backtest'], outcome.get('agents', []), outcome.get('picks', [])
    if not calls:
        raise ProtocolError('the research wrapper did not run the development stage')
    document = dict(schema=FROZEN_SCHEMA, study_id=lock['study_id'], lock_sha256=lock_sha,
                    created_utc_ns=now_ns, development_sessions=diagnostics, protocol=protocol,
                    frozen=record['frozen'], revisions=record['revisions'], failures=record['failures'],
                    design=record['design'], nominations=record['nominations'], agents=agents, picks=picks,
                    review_context=record['review_context'], development_replays=record['dev_raw'])
    sha = write_exclusive(study_dir / 'frozen.json', document)
    return dict(frozen_sha256=sha, candidate_id=record['frozen']['candidate_id'],
                development_sessions=len(sessions), declared=len(diagnostics), agents=len(agents))


def read_frozen(study_dir: Path, lock: dict, lock_sha: str) -> tuple[dict, str]:
    document, sha = read_document(Path(study_dir) / 'frozen.json', FROZEN_SCHEMA)
    if document.get('lock_sha256') != lock_sha or document.get('study_id') != lock['study_id']:
        raise ProtocolError('frozen.json belongs to a different lock')
    if type(document.get('created_utc_ns')) is not int or document['created_utc_ns'] >= lock['freeze_deadline_utc_ns']:
        raise ProtocolError('frozen.json was written after the freeze deadline')
    frozen = dict(document['frozen'])
    claimed = frozen.pop('sha256', None)
    if claimed != digest(frozen):
        raise ProtocolError('frozen candidate record does not match its digest')
    return document, sha


# ------------------------------------------------------------------- evaluation

def evaluate(study_dir: Path, journal_root: Path, *, now_ns: int) -> dict:
    from .options_master import evaluate_frozen

    study_dir = Path(study_dir)
    lock, lock_sha = read_lock(study_dir / 'lock.json')
    check_code(lock)
    frozen_doc, frozen_sha = read_frozen(study_dir, lock, lock_sha)
    if (study_dir / 'evaluation.json').exists():
        raise ProtocolError('the evaluation already ran; it is single-use')
    if not phase_closed(lock, 'evaluation', now_ns):
        raise ProtocolError('the evaluation window has not finished')
    selected = frozen_doc['frozen']['candidate_id']
    sessions, diagnostics = load_phase(lock, journal_root, 'evaluation')
    declared = len(diagnostics)
    missing = declared - len(sessions)
    protocol_blockers = []
    if missing > lock['max_missing_evaluation_fraction'] * declared:
        protocol_blockers.append('too_many_missing_evaluation_sessions')
    if sessions:
        outcome = evaluate_frozen(selected, sessions, [], [s['id'] for s in sessions],
                                  evidence='locked_forward_capture')
        validation, uncertainty = outcome['validation'], outcome['diagnostics']
        trades = {cost: dict(trades=raw[selected]['trades'], unresolved=raw[selected]['unresolved'])
                  for cost, raw in outcome['eval_raw'].items()}
        blockers, numerical_pass = outcome['blockers'] + protocol_blockers, outcome['numerical_pass']
    else:
        validation, uncertainty, trades, numerical_pass = None, None, None, False
        blockers = ['no_evaluation_sessions', *protocol_blockers, 'quote_replay_fills_are_not_observed_broker_fills']
        if selected == 'cash':
            blockers.insert(0, 'no_qualifying_development_candidate')
    passed = numerical_pass and not protocol_blockers
    master = dict(candidate_id=selected, status='locked_forward_research_only', operational_candidate_id='cash',
                  live_enabled=False, frozen_sha256=frozen_doc['frozen']['sha256'],
                  validation_gates_passed=passed, market_profitability_validated=False)
    document = dict(schema=EVALUATION_SCHEMA, study_id=lock['study_id'], lock_sha256=lock_sha,
                    frozen_file_sha256=frozen_sha, created_utc_ns=now_ns, master=master,
                    evaluation_sessions=diagnostics, declared_sessions=declared, admitted_sessions=len(sessions),
                    validation=validation, validation_uncertainty=uncertainty, blockers=blockers,
                    validation_trades=trades)
    sha = write_exclusive(study_dir / 'evaluation.json', document)
    return dict(evaluation_sha256=sha, master=master, blockers=blockers,
                admitted_sessions=len(sessions), declared_sessions=declared)


# ------------------------------------------------------------------- status

def status(study_dir: Path, journal_root: Path, *, now_ns: int) -> dict:
    study_dir = Path(study_dir)
    lock, lock_sha = read_lock(study_dir / 'lock.json')
    try:
        check_code(lock)
        code = 'unchanged'
    except ProtocolError as error:
        code = str(error)
    frozen, evaluated = (study_dir / 'frozen.json').exists(), (study_dir / 'evaluation.json').exists()
    if evaluated:
        stage = 'evaluated'
    elif frozen:
        stage = 'ready_to_evaluate' if phase_closed(lock, 'evaluation', now_ns) else 'collecting_evaluation'
    elif now_ns >= lock['freeze_deadline_utc_ns']:
        stage = 'void_freeze_deadline_missed'
    elif phase_closed(lock, 'development', now_ns):
        stage = 'ready_to_freeze'
    else:
        stage = 'collecting_development'
    sessions = []
    for entry in lock['sessions']:
        open_ns, close_ns = _bounds(entry, lock['rules'])
        path = Path(journal_root) / entry['date'] / 'journal.ndjson'
        sessions.append(dict(entry, journal=path.is_file(), finished=now_ns >= close_ns + SETTLE_NS))
    return dict(study_id=lock['study_id'], lock_sha256=lock_sha, stage=stage, scoring_code=code,
                freeze_deadline=datetime.fromtimestamp(lock['freeze_deadline_utc_ns'] / SECOND_NS, NY).isoformat(),
                sessions=sessions)


# ------------------------------------------------------------------- CLI

def _research_wrapper(args):
    roles = tuple(r for r in (args.agents or '').split(',') if r)
    if not roles:
        return None
    from functools import partial
    from .gui.agent_provider import consult, consult_ollama, read_agent_key, validate_ollama_model
    from .gui.agent_research import ROLES, run_research
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ProtocolError('unknown agent roles: ' + ', '.join(sorted(unknown)))
    if args.ollama_model:
        provider, key = partial(consult_ollama, model=validate_ollama_model(args.ollama_model)), None
    elif args.allow_openai:
        key = read_agent_key(args.repo_root)
        if key is None:
            raise ProtocolError('no OpenAI key in build/qte-agents/.env.local')
        provider = consult
    else:
        raise ProtocolError('--agents needs --ollama-model or --allow-openai')
    return lambda stage: run_research(roles, key=key, provider=provider, experiment='options_master', master=stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('plan', 'lock', 'status', 'freeze', 'evaluate'))
    parser.add_argument('--study-id', required=True)
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--studies-root', type=Path, default=Path('build/forward/studies'))
    parser.add_argument('--journal-root', type=Path, default=Path('build/forward/journals'))
    parser.add_argument('--first-session', type=date.fromisoformat,
                        help='First development session (default: next session not yet open)')
    parser.add_argument('--development', type=int, default=20)
    parser.add_argument('--buffer', type=int, default=1)
    parser.add_argument('--evaluation', type=int, default=30)
    parser.add_argument('--max-missing-evaluation-fraction', type=float, default=0.1)
    parser.add_argument('--agents', help='freeze: comma-separated agent roles, e.g. strategy_a,strategy_b,exposure')
    parser.add_argument('--ollama-model', help='freeze: local Ollama model for --agents')
    parser.add_argument('--allow-openai', action='store_true', help='freeze: use the OpenAI key for --agents')
    args = parser.parse_args(argv)
    study_dir = args.studies_root / args.study_id
    now = time.time_ns()
    try:
        if args.command in ('plan', 'lock'):
            lock = build_lock(study_id=args.study_id, now_ns=now, first_session=args.first_session,
                              development=args.development, buffer=args.buffer, evaluation=args.evaluation,
                              max_missing_evaluation_fraction=args.max_missing_evaluation_fraction)
            if args.command == 'lock':
                study_dir.mkdir(parents=True, exist_ok=True)
                result = dict(lock_sha256=write_exclusive(study_dir / 'lock.json', lock), path=str(study_dir / 'lock.json'))
            else:
                result = dict(note='dry run; nothing written')
            phases = {p: [s['date'] for s in lock['sessions'] if s['phase'] == p] for p in PHASES}
            result.update({p: f'{d[0]} .. {d[-1]} ({len(d)} sessions)' for p, d in phases.items()},
                          early_closes=[s['date'] for s in lock['sessions'] if s['close_et'] != lock['rules']['close_et']],
                          freeze_deadline=datetime.fromtimestamp(lock['freeze_deadline_utc_ns'] / SECOND_NS, NY).isoformat())
        elif args.command == 'status':
            result = status(study_dir, args.journal_root, now_ns=now)
        elif args.command == 'freeze':
            result = freeze(study_dir, args.journal_root, now_ns=now, research=_research_wrapper(args))
        else:
            result = evaluate(study_dir, args.journal_root, now_ns=now)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'qte forward protocol: {error}', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
