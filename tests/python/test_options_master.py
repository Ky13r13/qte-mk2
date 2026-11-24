"""Counterexample selection and the boundary between development and validation."""
from copy import deepcopy

import pytest

from qte import options_master as master
from qte.weekly_reversal import CANDIDATE_DEFINITIONS, replay_weekly_reversal
from test_weekly_reversal import _payload


def test_loss_and_unresolved_exit_prevent_master_revisions():
    def costs(pnl, unresolved=0):
        return {name: dict(net_pnl=pnl, closed_trades=15, unresolved=unresolved,
                           pnl_without_best_session=pnl-1) for name in master.COSTS}

    design = {c['id']: costs(-10) for c in CANDIDATE_DEFINITIONS}
    design['cash'] = costs(0)
    design['confirmed_fade_1sigma'] = costs(25, unresolved=1)
    assert master.tournament(design, {'strategy_a': 'confirmed_fade_1sigma'})[0] == 'cash'
    design['regime_gated_confirmed_1_5sigma'] = costs(20)
    selected, attempts = master.tournament(design, {'strategy_a': 'unregistered',
                                                     'strategy_b': 'regime_gated_confirmed_1_5sigma'})
    assert selected == 'regime_gated_confirmed_1_5sigma'
    assert attempts[0]['accepted'] and attempts[0]['proposed_by'] == 'strategy_b'
    assert any('unresolved_exits' in row['reasons'] for row in attempts)
    assert all(row['candidate_id'] != 'unregistered' for row in attempts)


def make_sessions(count):
    day_ns = 86_400_000_000_000
    sessions = []
    for i in range(count):
        session = _payload()['sessions'][0]
        session['id'] = f'session-{i}'
        for row in [session, session['opening_daily_iv'], session['weekly'], *session['bars'], *session['option_quotes']]:
            for key, value in row.items():
                if key.endswith('_ns'):
                    row[key] = value+i*day_ns
        sessions.append(session)
    return sessions


def test_candidate_frozen_before_selected_only_validation_and_no_holdout_leakage(monkeypatch):
    sessions = make_sessions(15)
    design_count = 12
    true_replay = master.replay_cases
    events = []

    def replay(rows, flags, *, candidate_id=None):
        events.append(('replay', candidate_id))
        return true_replay(rows, flags, candidate_id=candidate_id)

    def propose(evidence):
        events.append(('propose', None))
        assert 'session-12' not in master.encode(evidence).decode()
        assert 'source_sha256' not in evidence['protocol']
        return {'strategy_a': 'confirmed_fade_1sigma'}

    def freeze(frozen, protocol):
        events.append(('freeze', frozen['candidate_id']))
        assert protocol['split']['validation_ids'] == ['session-12','session-13','session-14']

    monkeypatch.setattr(master, 'replay_cases', replay)
    kwargs = dict(news_flags=[], design_sessions=design_count, propose=propose, on_freeze=freeze)
    first = master.run_master_research(sessions=sessions, **kwargs)
    candidate = first['master']['candidate_id']
    assert candidate == 'confirmed_fade_1sigma'
    assert events == [('replay', None), ('propose', None), ('freeze', candidate), ('replay', candidate)]
    changed = deepcopy(sessions)
    for session in changed[design_count:]:
        for quote in session['option_quotes'][2:]:
            quote['bid'] = 0.0
    second = master.run_master_research(sessions=changed, **kwargs)
    assert first['frozen'] == second['frozen']
    assert first['protocol']['source_sha256'] != second['protocol']['source_sha256']
    assert first['validation']['stress']['unresolved'] == 0
    assert second['validation']['stress']['unresolved'] == 3
    assert 'unresolved_exits' in second['blockers']
    assert second['master']['operational_candidate_id'] == 'cash'
    assert not second['master']['market_profitability_validated']


def test_rejects_overlapping_cut_and_unknown_replay_candidate():
    sessions = make_sessions(2)
    sessions[1]['open_ns'] = sessions[0]['end_ns'] - 1
    with pytest.raises(ValueError, match='non-overlapping'):
        master.split_inputs(sessions, [], 1)
    with pytest.raises(ValueError, match='unknown replay candidate'):
        replay_weekly_reversal(_payload(), candidate_id='unregistered')
