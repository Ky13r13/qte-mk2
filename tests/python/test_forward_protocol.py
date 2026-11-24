"""Collector journals → locked options-master protocol → durable paper ledger."""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timezone
import json
import os
import shutil

import pytest

from forward_journal_fixture import RULES, FakeMarket, collect
from qte import forward_collector as fc
from qte import forward_paper as paper
from qte import forward_protocol as fp
from qte.forward_sessions import NY, SECOND_NS, build_session, read_journal
from qte.gui.agent_research import run_research

LOCKED_AT = int(datetime(2026, 11, 22, 12, tzinfo=timezone.utc).timestamp()) * SECOND_NS


def at_et(day, hour, minute=0):
    return int(datetime.combine(date.fromisoformat(day), dtime(hour, minute), NY).timestamp()) * SECOND_NS


def make_lock(**overrides):
    options = dict(study_id='spy-test', now_ns=LOCKED_AT, first_session=date(2026, 11, 23),
                   development=5, buffer=1, evaluation=20, rules=RULES)
    options.update(overrides)
    return fp.build_lock(**options)


def write_study(tmp_path, lock=None):
    study = tmp_path / 'study'
    study.mkdir()
    fp.write_exclusive(study / 'lock.json', lock or make_lock())
    return study


def phase_days(lock, phase):
    return [s for s in lock['sessions'] if s['phase'] == phase]


@pytest.fixture(scope='module')
def journals(tmp_path_factory):
    """Journal roots built once: oscillating everywhere, and flat (no-signal) evaluation."""
    lock = make_lock()
    base = tmp_path_factory.mktemp('journals')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, 'fsync', lambda fd: None)  # simulated sessions; durability is tested separately
        oscillating = base / 'oscillating'
        for entry in lock['sessions']:
            collect(oscillating, date.fromisoformat(entry['date']), close_et=entry['close_et'])
        flat = base / 'flat'
        for entry in lock['sessions']:
            source = oscillating / entry['date']
            if entry['phase'] == 'evaluation':
                collect(flat, date.fromisoformat(entry['date']), close_et=entry['close_et'], flat=True)
            else:
                shutil.copytree(source, flat / entry['date'])
    return dict(lock=lock, oscillating=oscillating, flat=flat)


def freeze_time(lock):
    return fp._bounds(phase_days(lock, 'development')[-1], lock['rules'])[1] + fp.SETTLE_NS


def after_evaluation(lock):
    return fp._bounds(lock['sessions'][-1], lock['rules'])[1] + fp.SETTLE_NS


# ----------------------------------------------------------------- collector

def test_collector_journal_builds_session_and_refuses_restart_after_close(tmp_path):
    summary, market = collect(tmp_path, date(2026, 11, 23))
    assert summary['reason'] == 'session_close' and summary['poll_errors'] == 0
    path = tmp_path / '2026-11-23' / 'journal.ndjson'
    journal = read_journal(path)
    session, diagnostics = build_session(journal, RULES)
    assert diagnostics['stop'] == 'session_close' and diagnostics['bars'] >= 59
    # Every admitted observation is available no earlier than the collector received it.
    receipts = {r['received_utc_ns'] for r in journal['records']}
    assert all(q['available_ns'] in receipts and q['timestamp_ns'] <= q['available_ns']
               for q in session['option_quotes'])
    assert all(b['available_ns'] >= b['timestamp_ns'] for b in session['bars'])
    before = path.read_bytes()
    market.now = at_et('2026-11-23', 11)
    journal_file = fc.Journal(path)
    try:
        collector = fc.ForwardCollector(journal_file, lambda: market, session_date=date(2026, 11, 23),
                                        rules=RULES, close_et='10:30', interval_ns=10 * SECOND_NS,
                                        clock=market.clock, sleep=market.sleep)
        with pytest.raises(ValueError, match='already closed'):
            collector.run()
    finally:
        journal_file.close()
    assert path.read_bytes() == before


def test_collector_journals_poll_failures_as_gaps(tmp_path):
    summary, _ = collect(tmp_path, date(2026, 11, 23), fail_polls=range(100, 110))
    assert summary['poll_errors'] == 10
    _, diagnostics = build_session(read_journal(tmp_path / '2026-11-23' / 'journal.ndjson'), RULES)
    assert diagnostics['errors'] == 10 and diagnostics['unavailable']['poll_error'] == 30
    assert diagnostics['missing_bars'] > 1


# ----------------------------------------------------------------- lock

def test_lock_fixes_calendar_split_and_must_precede_the_first_open(tmp_path):
    lock = make_lock()
    dates = {s['date']: s for s in lock['sessions']}
    assert '2026-11-26' not in dates and '2026-12-25' not in dates  # exchange holidays
    assert dates['2026-11-27']['close_et'] == '13:00' and dates['2026-12-24']['close_et'] == '13:00'
    assert [len(phase_days(lock, p)) for p in fp.PHASES] == [5, 1, 20]
    assert lock['freeze_deadline_utc_ns'] == at_et(phase_days(lock, 'evaluation')[0]['date'], 9, 30)
    assert lock['code_sha256'] == fp.code_hashes()
    with pytest.raises(fp.ProtocolError, match='before the first development session opens'):
        make_lock(now_ns=at_et('2026-11-23', 9, 31))
    with pytest.raises(fp.ProtocolError, match='evaluation sessions'):
        make_lock(evaluation=5)
    with pytest.raises(fp.ProtocolError, match='buffer sessions'):
        make_lock(buffer=0)
    study = write_study(tmp_path, lock)
    with pytest.raises(fp.ProtocolError, match='written once'):
        fp.write_exclusive(study / 'lock.json', make_lock(development=6))
    assert fp.load_lock(study / 'lock.json') == lock


def test_default_first_session_is_the_next_unopened_session():
    lock = make_lock(first_session=None, now_ns=at_et('2026-11-25', 12))
    assert lock['sessions'][0]['date'] == '2026-11-27'


# ----------------------------------------------------------------- freeze

def test_freeze_reads_only_development_journals_and_enforces_its_window(tmp_path, journals, monkeypatch):
    lock, root = journals['lock'], journals['oscillating']
    study = write_study(tmp_path, lock)
    with pytest.raises(fp.ProtocolError, match='not finished'):
        fp.freeze(study, root, now_ns=freeze_time(lock) - SECOND_NS)
    with pytest.raises(fp.ProtocolError, match='void'):
        fp.freeze(study, root, now_ns=lock['freeze_deadline_utc_ns'])
    opened = []
    original = fp.read_journal
    monkeypatch.setattr(fp, 'read_journal', lambda path: opened.append(path.parent.name) or original(path))
    result = fp.freeze(study, root, now_ns=freeze_time(lock))
    assert opened == [s['date'] for s in phase_days(lock, 'development')]
    assert result['development_sessions'] == 5 and result['candidate_id'] != 'cash'
    frozen = json.loads((study / 'frozen.json').read_text())
    assert frozen['protocol']['evidence'] == 'locked_forward_capture'
    assert frozen['protocol']['split']['validation_ids'] == [s['date'] for s in phase_days(lock, 'evaluation')]
    assert not frozen['frozen']['live_enabled']
    with pytest.raises(fp.ProtocolError, match='already frozen'):
        fp.freeze(study, root, now_ns=freeze_time(lock))


def test_agents_nominate_from_development_evidence_only(tmp_path, journals):
    lock, root = journals['lock'], journals['oscillating']
    study = write_study(tmp_path, lock)
    contexts = []

    def provider(_key, instructions, context):
        contexts.append(context)
        text = 'candidate: confirmed_fade_1sigma\nFalsifier: losses after stress costs.'
        return dict(text=text, model='fake', session_id=None, turn_id=None, session_cleanup=None)

    roles = ('premarket_bias', 'strategy_a', 'strategy_b', 'exposure', 'documentation')
    research = lambda stage: run_research(roles, provider=provider, experiment='options_master', master=stage)
    fp.freeze(study, root, now_ns=freeze_time(lock), research=research)
    frozen = json.loads((study / 'frozen.json').read_text())
    assert frozen['nominations'] == {'strategy_a': 'confirmed_fade_1sigma', 'strategy_b': 'confirmed_fade_1sigma'}
    assert frozen['revisions'][0]['candidate_id'] == 'confirmed_fade_1sigma'
    assert [a['phase'] for a in frozen['agents']].count('design_review') == 5
    evaluation_dates = [s['date'] for s in phase_days(lock, 'evaluation')]
    assert len(contexts) == 7
    assert not any(day in context for context in contexts for day in evaluation_dates)


def test_failed_nomination_cannot_bypass_gates(tmp_path, journals):
    lock, root = journals['lock'], journals['oscillating']
    study = write_study(tmp_path, lock)

    def provider(_key, instructions, context):
        return dict(text='candidate: trend_follow_1sigma', model='fake', session_id=None, turn_id=None,
                    session_cleanup=None)

    fp.freeze(study, root, now_ns=freeze_time(lock),
              research=lambda stage: run_research(('strategy_a',), provider=provider,
                                                  experiment='options_master', master=stage))
    frozen = json.loads((study / 'frozen.json').read_text())
    trend = next(r for r in frozen['revisions'] if r['candidate_id'] == 'trend_follow_1sigma')
    assert trend['proposed_by'] == 'strategy_a' and not trend['accepted']
    assert 'nonpositive_after_costs' in trend['reasons']
    assert frozen['frozen']['candidate_id'] != 'trend_follow_1sigma'


def test_freeze_without_development_journals_selects_cash(tmp_path, journals):
    lock = journals['lock']
    study = write_study(tmp_path, lock)
    (tmp_path / 'empty').mkdir()
    assert fp.freeze(study, tmp_path / 'empty', now_ns=freeze_time(lock))['candidate_id'] == 'cash'
    result = fp.evaluate(study, journals['oscillating'], now_ns=after_evaluation(lock))
    assert not result['master']['validation_gates_passed']
    assert 'no_qualifying_development_candidate' in result['blockers']


# ----------------------------------------------------------------- evaluation

def test_frozen_candidate_passes_only_when_unseen_data_clears_the_gates(tmp_path, journals):
    lock = journals['lock']
    outcomes, frozen_hashes = {}, set()
    for name in ('oscillating', 'flat'):
        (tmp_path / name).mkdir()
        study = write_study(tmp_path / name, lock)
        fp.freeze(study, journals[name], now_ns=freeze_time(lock))
        frozen_hashes.add(json.loads((study / 'frozen.json').read_text())['frozen']['sha256'])
        with pytest.raises(fp.ProtocolError, match='not finished'):
            fp.evaluate(study, journals[name], now_ns=after_evaluation(lock) - SECOND_NS)
        outcomes[name] = fp.evaluate(study, journals[name], now_ns=after_evaluation(lock))
        with pytest.raises(fp.ProtocolError, match='single-use'):
            fp.evaluate(study, journals[name], now_ns=after_evaluation(lock))
    # Identical development data → identical frozen choice, whatever evaluation later holds.
    assert len(frozen_hashes) == 1
    good, flat = outcomes['oscillating'], outcomes['flat']
    assert good['admitted_sessions'] == 20 and good['master']['validation_gates_passed']
    assert good['master']['operational_candidate_id'] == 'cash' and not good['master']['live_enabled']
    assert 'quote_replay_fills_are_not_observed_broker_fills' in good['blockers']
    assert not flat['master']['validation_gates_passed']
    assert 'insufficient_closed_trades' in flat['blockers']


def test_missing_evaluation_sessions_block_the_pass(tmp_path, journals):
    lock = journals['lock']
    root = tmp_path / 'partial'
    shutil.copytree(journals['oscillating'], root)
    for entry in phase_days(lock, 'evaluation')[:3]:
        shutil.rmtree(root / entry['date'])
    study = write_study(tmp_path, lock)
    fp.freeze(study, root, now_ns=freeze_time(lock))
    result = fp.evaluate(study, root, now_ns=after_evaluation(lock))
    assert result['admitted_sessions'] == 17
    assert 'too_many_missing_evaluation_sessions' in result['blockers']
    assert not result['master']['validation_gates_passed']


def test_scoring_code_change_or_pre_lock_journal_is_rejected(tmp_path, journals, monkeypatch):
    lock = make_lock(now_ns=at_et('2026-11-23', 9, 0))
    study = write_study(tmp_path, lock)
    # A journal started before the lock cannot count toward the study.
    session, info = fp.load_session(dict(lock, created_utc_ns=at_et('2026-11-23', 11)),
                                    journals['oscillating'], lock['sessions'][0])
    assert session is None and info['excluded'] == 'journal_predates_lock'
    monkeypatch.setattr(fp, 'code_hashes', lambda: dict(lock['code_sha256'], **{'weekly_reversal.py': '0' * 64}))
    with pytest.raises(fp.ProtocolError, match='weekly_reversal.py'):
        fp.freeze(study, journals['oscillating'], now_ns=freeze_time(lock))
    assert fp.status(study, journals['oscillating'], now_ns=freeze_time(lock))['stage'] == 'ready_to_freeze'


def test_frozen_file_is_bound_to_its_lock_and_deadline(tmp_path, journals):
    lock = journals['lock']
    study = write_study(tmp_path, lock)
    fp.freeze(study, journals['oscillating'], now_ns=freeze_time(lock))
    document = json.loads((study / 'frozen.json').read_text())
    document['frozen']['candidate_id'] = 'trend_follow_1sigma'
    (study / 'frozen.json').unlink()
    fp.write_exclusive(study / 'frozen.json', document)
    with pytest.raises(fp.ProtocolError, match='digest'):
        fp.evaluate(study, journals['oscillating'], now_ns=after_evaluation(lock))


# ----------------------------------------------------------------- paper ledger

def test_paper_ledger_appends_settled_sessions_and_reconciles_on_restart(tmp_path, journals):
    lock = journals['lock']
    root = tmp_path / 'journals'
    shutil.copytree(journals['oscillating'], root)
    shutil.rmtree(root / phase_days(lock, 'buffer')[0]['date'])
    study = write_study(tmp_path, lock)
    second_close = fp._bounds(lock['sessions'][1], lock['rules'])[1]
    first = paper.update(study, root, now_ns=second_close + fp.SETTLE_NS - 1)
    assert (first['entries'], first['appended']) == (2, 2)  # header + first session; second not settled
    again = paper.update(study, root, now_ns=second_close + fp.SETTLE_NS - 1)
    assert (again['appended'], again['reconciled']) == (0, 2)
    final = paper.update(study, root, now_ns=after_evaluation(lock))
    assert final['entries'] == 1 + len(lock['sessions']) and final['reconciled'] == 2
    records = paper.read_ledger(study / paper.LEDGER)
    excluded = [r for r in records if r['kind'] == 'session' and r['status'] == 'excluded']
    assert [(r['phase'], r['excluded']) for r in excluded] == [('buffer', 'missing_journal')]
    last = records[-1]['results']
    assert set(last) == set(lock['paper_candidates'])
    for name, by_cost in last.items():
        for cost, row in by_cost.items():
            total = sum(r['results'][name][cost]['session_net_pnl'] for r in records if r['kind'] == 'session')
            assert row['cumulative']['net_pnl'] == pytest.approx(total, abs=1e-9)
            buys = sum(f['action'] == 'buy_to_open' for r in records if r['kind'] == 'session'
                       for f in r['results'][name][cost]['fills'])
            assert buys == row['cumulative']['closed_trades'] + row['cumulative']['unresolved']
    assert last['naive_fade_1sigma']['stress']['cumulative']['net_pnl'] > 0
    assert last['trend_follow_1sigma']['stress']['cumulative']['net_pnl'] < 0
    summary = paper.report(study)
    assert summary['sessions_excluded'] == 1 and summary['frozen_candidate'] is None
    assert summary['by_phase']['evaluation']['naive_fade_1sigma']['base']['closed_trades'] > 0


def test_paper_ledger_halts_on_changed_journal_without_rewriting(tmp_path, journals):
    lock = journals['lock']
    root = tmp_path / 'journals'
    shutil.copytree(journals['oscillating'], root)
    study = write_study(tmp_path, lock)
    settled = fp._bounds(lock['sessions'][2], lock['rules'])[1] + fp.SETTLE_NS
    paper.update(study, root, now_ns=settled)
    ledger_bytes = (study / paper.LEDGER).read_bytes()
    target = root / lock['sessions'][0]['date']
    shutil.rmtree(target)
    collect(root, date.fromisoformat(lock['sessions'][0]['date']), amplitude=1.0)
    with pytest.raises(paper.LedgerMismatch, match=lock['sessions'][0]['date']):
        paper.update(study, root, now_ns=after_evaluation(lock))
    assert (study / paper.LEDGER).read_bytes() == ledger_bytes


def test_paper_ledger_rejects_edited_history_and_recovers_partial_tail(tmp_path, journals):
    lock = journals['lock']
    study = write_study(tmp_path, lock)
    settled = fp._bounds(lock['sessions'][1], lock['rules'])[1] + fp.SETTLE_NS
    paper.update(study, journals['oscillating'], now_ns=settled)
    path = study / paper.LEDGER
    good = path.read_bytes()
    path.write_bytes(good + b'{"kind":"sess')  # crash mid-append
    assert paper.update(study, journals['oscillating'], now_ns=settled)['appended'] == 0
    assert path.read_bytes() == good and list(study.glob('paper-ledger.ndjson.partial-*'))
    lines = good.splitlines()
    record = json.loads(lines[1])
    record['results']['naive_fade_1sigma']['base']['session_net_pnl'] += 100
    path.write_bytes(b'\n'.join([lines[0], json.dumps(record).encode(), *lines[2:]]) + b'\n')
    with pytest.raises(paper.LedgerMismatch, match='hash chain'):
        paper.update(study, journals['oscillating'], now_ns=settled)


def test_report_names_the_frozen_candidate(tmp_path, journals):
    lock = journals['lock']
    study = write_study(tmp_path, lock)
    fp.freeze(study, journals['oscillating'], now_ns=freeze_time(lock))
    paper.update(study, journals['oscillating'], now_ns=freeze_time(lock))
    chosen = json.loads((study / 'frozen.json').read_text())['frozen']['candidate_id']
    assert paper.report(study)['frozen_candidate'] == chosen
