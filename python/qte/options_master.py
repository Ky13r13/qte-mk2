"""Bounded, development-only challenger tournament for short-hold options.

Uses QTE's existing Python quote replay, not the C++ equity engine. Reviewers
cannot execute code, change risk limits, or promote a policy into live trading.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import html
import json
from pathlib import Path
import random
from statistics import mean, median

from .weekly_reversal import CANDIDATE_DEFINITIONS, MAX_ENTRIES, replay_weekly_reversal
from .weekly_reversal_suite import COSTS, MAX_DEBIT, MULTIPLIER

MAX_REVISIONS = 3
MIN_DESIGN_TRADES = 10
MIN_VALIDATION_TRADES = 30
MIN_VALIDATION_SESSIONS = 20
REFERENCES = [dict(title='Bailey et al., The Probability of Backtest Overfitting',
                   url='https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf',
                   note='Repeated selection on historical outcomes can overfit; this workflow freezes before evaluation.')]


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()


def digest(value):
    return sha256(encode(value)).hexdigest()


def split_inputs(sessions, flags, count):
    """Split whole sessions, rejecting overlap before any strategy is selected."""
    if not isinstance(sessions, list) or not 2 <= len(sessions) <= 1000:
        raise ValueError('expected 2–1000 sessions')
    if type(count) is not int or not 1 <= count < len(sessions):
        raise ValueError('design_sessions must leave a nonempty chronological validation set')
    previous, identities = None, set()
    for s in sessions:
        if not isinstance(s, dict) or type(s.get('id')) is not str:
            raise ValueError('invalid session identity')
        if type(s.get('open_ns')) is not int or type(s.get('end_ns')) is not int:
            raise ValueError('invalid session timestamps')
        if s['id'] in identities or s['open_ns'] >= s['end_ns'] or previous is not None and s['open_ns'] < previous:
            raise ValueError('sessions must be unique, ordered and non-overlapping')
        identities.add(s['id'])
        previous = s['end_ns']
    if not isinstance(flags, list):
        raise ValueError('news_flags must be a list')
    for flag in flags:
        if (not isinstance(flag, dict) or set(flag) != {'timestamp_ns','available_ns','active'}
                or type(flag['timestamp_ns']) is not int or type(flag['available_ns']) is not int
                or type(flag['active']) is not bool or flag['available_ns'] < flag['timestamp_ns']):
            raise ValueError('invalid news availability')
    cutoff = sessions[count-1]['end_ns']
    design_flags = [f for f in flags if f['available_ns'] <= cutoff]
    return sessions[:count], design_flags, sessions[count:]


def summarize(candidate, session_ids):
    pnl = {s: 0.0 for s in session_ids}
    deployed = dict(pnl)
    for kind in ('trades','unresolved'):
        for trade in candidate[kind]:
            session = trade['session']
            pnl[session] += trade['net_pnl'] if kind == 'trades' else -trade['risk_deployed']
            deployed[session] += trade['risk_deployed']
    trades = candidate['trades']
    holds = [(t['exit_ns']-t['entry_ns'])/60e9 for t in trades]
    # Midpoints are attribution marks only. Execution still pays asks and sells bids.
    mids = [(t['exit_bid'] + t['exit_ask'] - t['entry_bid'] - t['entry_ask']) * MULTIPLIER / 2
            for t in trades]
    net = sum(pnl.values())
    return dict(net_pnl=net, closed_trades=len(trades), unresolved=len(candidate['unresolved']),
                sessions=len(pnl), active_sessions=sum(v>0 for v in deployed.values()),
                pnl_without_best_session=net-max([0,*pnl.values()]),
                worst_session=min(pnl.values()), max_session_debit=max(deployed.values()),
                total_debit=sum(deployed.values()), fees=sum(t['fees'] for t in trades),
                closed_midpoint_pnl=sum(mids), closed_execution_drag=sum(mids)-sum(t['net_pnl'] for t in trades),
                positive_midpoint_but_nonpositive_net=sum(mid > 0 >= t['net_pnl'] for mid, t in zip(mids, trades)),
                median_hold_minutes=median(holds) if holds else None,
                max_hold_minutes=max(holds) if holds else None,
                session_pnl=pnl, skipped=dict(Counter(t['reason'] for t in candidate['skipped'])))


def score(row):
    return min(row['base']['net_pnl'], row['stress']['net_pnl'])


def reject_reasons(row, *, validation=False):
    reasons=[]
    if score(row) <= 0:
        reasons.append('nonpositive_after_costs')
    threshold = MIN_VALIDATION_TRADES if validation else MIN_DESIGN_TRADES
    if min(row[c]['closed_trades'] for c in COSTS) < threshold:
        reasons.append('insufficient_closed_trades')
    if any(row[c]['unresolved'] for c in COSTS):
        reasons.append('unresolved_exits')
    if min(row[c]['pnl_without_best_session'] for c in COSTS) <= 0:
        reasons.append('depends_on_best_session')
    if validation and row['stress']['sessions'] < MIN_VALIDATION_SESSIONS:
        reasons.append('insufficient_validation_sessions')
    return reasons


def tournament(design, nominations=None):
    """Finite allowlisted revisions. Receives no validation data or fixture labels."""
    expected = [c['id'] for c in CANDIDATE_DEFINITIONS]
    if set(design) != set(expected):
        raise ValueError('only the complete registered candidate slate is accepted')
    nominees = {}
    for role in ('strategy_a', 'strategy_b'):
        candidate = (nominations or {}).get(role)
        if isinstance(candidate, str) and candidate in expected and candidate != 'cash':
            nominees.setdefault(candidate, role)
    order = list(dict.fromkeys([*nominees, *(name for name in expected if name != 'cash')]))
    selected, versions, attempts = 'cash', 0, []
    for index, candidate in enumerate(order):
        reasons = reject_reasons(design[candidate])
        if not reasons and score(design[candidate]) <= score(design[selected]):
            reasons.append('no_development_improvement')
        if not reasons and versions >= MAX_REVISIONS:
            reasons.append('revision_budget_exhausted')
        accepted = not reasons
        before = selected
        if accepted:
            selected = candidate
            versions += 1
        proposer = nominees.get(candidate)
        attempts.append(dict(candidate_id=candidate, previous_candidate_id=before, accepted=accepted,
            version=versions, proposed_by=proposer or ('fixed_slate_a' if index % 2 == 0 else 'fixed_slate_b'),
            challenged_by='fixed_development_gates',
            reason='Improved worst-cost development PnL and passed fixed gates.' if accepted else ', '.join(reasons),
            reasons=reasons, design_worst_cost_pnl=score(design[candidate])))
    return selected, attempts


def replay_cases(sessions, flags, *, candidate_id=None):
    results={}
    for name in COSTS:
        replay=replay_weekly_reversal(dict(sessions=sessions,news_flags=flags,costs=COSTS,
            cost_scenario=name,max_debit_dollars=MAX_DEBIT,multiplier=MULTIPLIER), candidate_id=candidate_id)
        results[name]={row['id']: row for row in replay['candidates']}
    return results


def uncertainty(values):
    """Exploratory whole-session block resampling; not selection-adjusted proof."""
    count=len(values)
    block=min(5,count)
    rng=random.Random(20261120)
    means=[]
    for _ in range(1000):
        sampled=[]
        while len(sampled)<count:
            start=rng.randrange(count)
            sampled.extend(values[(start+i)%count] for i in range(block))
        means.append(mean(sampled[:count]))
    means.sort()
    return dict(mean_session_pnl=mean(values), interval_95pct=[means[24],means[974]],
                method='1000 circular session-block resamples, blocks up to 5 sessions, seed 20261120; exploratory, not selection adjusted')


def build_protocol(development, design_flags, evaluation, *, evidence, source_sha256,
                   holdout_status='chronological but not certified untouched; synthetic paths previously inspected'):
    """Fixed selection rules. ``evaluation`` contributes session IDs only, never observations."""
    return dict(version=1,selection='worst base/stress net PnL versus cash, strict improvement; ties keep incumbent',
        candidate_order=[r['id'] for r in CANDIDATE_DEFINITIONS],candidate_definitions=deepcopy(CANDIDATE_DEFINITIONS),
        max_accepted_revisions=MAX_REVISIONS, min_design_closed_trades=MIN_DESIGN_TRADES,
        min_validation_closed_trades=MIN_VALIDATION_TRADES,min_validation_sessions=MIN_VALIDATION_SESSIONS,
        costs=deepcopy(COSTS),quantity=1,max_debit_plus_roundtrip_fees=MAX_DEBIT,
        max_entries_per_session=MAX_ENTRIES,hold_target_minutes=5,live_enabled=False,
        split=dict(design_ids=[s['id'] for s in development],validation_ids=[s['id'] for s in evaluation]),
        evidence=evidence,holdout_status=holdout_status,
        risk_policy='No size increases; no shorts/assignment; missing exit conservatively loses entire debit and blocks remaining session',
        engine='QTE Python options quote replay, not the C++ equity engine',
        evidence_blockers=['verified contract/exercise and settlement identity missing',
                           'quote size/depth and independent provider provenance missing',
                           'capture schema alone cannot certify historical quotes or executable fills'],
        source_sha256=source_sha256,
        development_sha256=digest(dict(sessions=development,news_flags=design_flags)))


def develop(development, design_flags, protocol, propose=None):
    """Development replay, nominations and the fixed tournament. Sees no evaluation data."""
    dev_raw=replay_cases(development,design_flags)
    design={r['id']:{cost:summarize(dev_raw[cost][r['id']],protocol['split']['design_ids']) for cost in COSTS}
            for r in CANDIDATE_DEFINITIONS}
    failures=[]
    for candidate,row in design.items():
        if candidate=='cash':continue
        worst=sorted(row['stress']['session_pnl'].items(),key=lambda x:(x[1],x[0]))[:3]
        regime_pnl=defaultdict(float)
        for t in dev_raw['stress'][candidate]['trades']:
            regime_pnl[t['observable_regime']]+=t['net_pnl']
        for t in dev_raw['stress'][candidate]['unresolved']:
            regime_pnl[t['observable_regime']]-=t['risk_deployed']
        failures.append(dict(candidate_id=candidate,reasons=reject_reasons(row),design_base=row['base'],
            design_stress=row['stress'],counterexamples=[dict(session=s,net_pnl=p) for s,p in worst if p<0],
            observed_entry_regime_pnl=dict(regime_pnl),
            base_to_stress_change=row['stress']['net_pnl']-row['base']['net_pnl']))
    review_context = dict(
        protocol={k: v for k, v in protocol.items() if k not in {'source_sha256', 'split'}},
        failures=failures,
        instruction='Nominate one registered candidate using development evidence and state a falsifier.',
    )
    nominations = propose(deepcopy(review_context)) if propose is not None else None
    selected, revisions = tournament(design, nominations)
    frozen = dict(candidate_id=selected,
        definition=next(deepcopy(c) for c in CANDIDATE_DEFINITIONS if c['id'] == selected),
        development_protocol_sha256=digest(review_context['protocol']),
        development_sha256=protocol['development_sha256'],
        selection_basis='development-only worst-cost PnL with fixed gates',
        revisions=deepcopy(revisions), live_enabled=False)
    frozen['sha256'] = digest(frozen)
    return dict(dev_raw=dev_raw, design=design, failures=failures, review_context=review_context,
                nominations=deepcopy(nominations) if isinstance(nominations, dict) else None,
                selected=selected, revisions=revisions, frozen=frozen)


def evaluate_frozen(selected, evaluation, news_flags, validation_ids, *, evidence):
    """One chronological replay of the frozen candidate under every cost scenario."""
    eval_raw = replay_cases(evaluation, news_flags, candidate_id=selected)
    validation = {cost: summarize(eval_raw[cost][selected], validation_ids) for cost in COSTS}
    blockers=reject_reasons(validation,validation=True)
    if selected=='cash':blockers.append('no_qualifying_development_candidate')
    if evidence=='synthetic_mechanics_only':blockers.append('synthetic_data_cannot_validate_market_profitability')
    elif evidence=='locked_forward_capture':blockers.append('quote_replay_fills_are_not_observed_broker_fills')
    else:blockers.append('capture_provenance_and_execution_not_independently_verified')
    diagnostics=uncertainty(list(validation['stress']['session_pnl'].values()))
    if diagnostics['interval_95pct'][0]<=0:blockers.append('validation_uncertainty_includes_nonpositive_mean')
    numerical_pass = (selected != 'cash' and not reject_reasons(validation, validation=True)
                      and diagnostics['interval_95pct'][0] > 0)
    return dict(eval_raw=eval_raw, validation=validation, blockers=blockers,
                diagnostics=diagnostics, numerical_pass=numerical_pass)


def run_master_research(*, sessions=None, news_flags=None, design_sessions=None, on_freeze=None, propose=None):
    if sessions is None:
        if news_flags is not None or design_sessions is not None:
            raise ValueError('default fixture requires default split and news')
        from .weekly_reversal_fixture import fixture_inputs
        sessions,news_flags,_=fixture_inputs()
        design_sessions=8
        evidence='synthetic_mechanics_only'
    else:
        evidence='user_supplied_capture_unverified'
    # Never permit caller mutation, including a freeze callback, to change the run.
    sessions,news_flags=deepcopy(sessions),deepcopy(news_flags)
    development,design_flags,evaluation=split_inputs(sessions,news_flags,design_sessions)
    protocol=build_protocol(development,design_flags,evaluation,evidence=evidence,
                            source_sha256=digest(dict(sessions=sessions,news_flags=news_flags)))
    stage=develop(development,design_flags,protocol,propose)
    failures,review_context=stage['failures'],stage['review_context']
    selected,revisions,frozen=stage['selected'],stage['revisions'],stage['frozen']
    dev_raw=stage['dev_raw']
    if on_freeze is not None:
        on_freeze(deepcopy(frozen), deepcopy(protocol))
    outcome=evaluate_frozen(selected,evaluation,news_flags,protocol['split']['validation_ids'],evidence=evidence)
    eval_raw,validation,blockers=outcome['eval_raw'],outcome['validation'],outcome['blockers']
    diagnostics,numerical_pass=outcome['diagnostics'],outcome['numerical_pass']
    master=dict(candidate_id=selected,status='synthetic_mechanics_only' if evidence=='synthetic_mechanics_only' else 'unverified_research_only',
        operational_candidate_id='cash',live_enabled=False,selection_basis=frozen['selection_basis'],
        frozen_sha256=frozen['sha256'],validation_gates_passed=numerical_pass,
        market_profitability_validated=False)
    # Model reviewers see development evidence only. Do not include evaluation
    # dates, input hashes, PnL, validation grades or final master status here.
    review_context.update(candidate_id=selected, revisions=revisions,
        instruction='Development-only critique of the frozen candidate and failure audit.')
    agents=[dict(role='premarket_bias',kind='deterministic_audit',finding='News/shock/quiet states come from available observations; fixture case labels are excluded.'),
        dict(role='strategy_a',kind='bounded_proposer',finding='Proposed registered fade/reentry candidates; proposals cannot alter execution or sizing.'),
        dict(role='strategy_b',kind='counterexample_challenger',finding='Retained losing sessions, stressed costs, missing exits and rejected revisions.'),
        dict(role='exposure',kind='deterministic_audit',finding='One long option, $650 debit-plus-fees cap, max five entries; worst cumulative daily debit cap is $3,250, not a $650 daily loss limit.'),
        dict(role='documentation',kind='deterministic_audit',finding='Protocol, development failures and frozen master precede evaluation; no automatic retry after validation.')]
    return dict(schema_version=1,experiment='options_master',evidence=evidence,protocol=protocol,
        master=master,frozen=frozen,revisions=revisions,failures=failures,validation=validation,
        validation_uncertainty=diagnostics,blockers=blockers,agents=agents,review_context=review_context,
        validation_trades={cost:dict(trades=eval_raw[cost][selected]['trades'],unresolved=eval_raw[cost][selected]['unresolved']) for cost in COSTS},
        development_replays=dev_raw,references=REFERENCES)


def render_html(result):
    m=result['master']
    parts=['<!doctype html><meta charset="utf-8"><title>QTE options master</title>',
           '<style>body{font:16px system-ui;max-width:1100px;margin:32px;color:#222;background:#fafafa}td,th{padding:9px;border-bottom:1px solid #bbb;text-align:left}table{border-collapse:collapse}code{overflow-wrap:anywhere}</style>',
           '<h1>Options master · challenger loop</h1>',
           f"<p>Research candidate: <strong>{html.escape(m['candidate_id'])}</strong>. Operational policy: cash. Evidence: {html.escape(result['evidence'])}. No validated market profitability.</p>",
           '<p>Five-minute target hold, one long weekly call or put, next-quote ask/bid fills. Quotes can delay actual exit. QTE Python options replay; no C++ portfolio integration or live order route.</p>',
           '<h2>Why it cannot be promoted</h2><ul>']
    parts += [f'<li>{html.escape(b)}</li>' for b in result['blockers']]
    parts += ['</ul><h2>Development revisions</h2><table><tr><th>Candidate</th><th>Accepted</th><th>Reason</th></tr>']
    for r in result['revisions']:
        parts.append(f"<tr><td>{html.escape(r['candidate_id'])}</td><td>{r['accepted']}</td><td>{html.escape(r['reason'])}</td></tr>")
    parts += ['</table><h2>Development failures</h2><table><tr><th>Candidate</th><th>Base $</th><th>Stress $</th><th>Midpoint P&amp;L $</th><th>Execution drag $</th><th>Worst session $</th><th>Failed checks</th></tr>']
    for f in result['failures']:
        stress = f['design_stress']
        parts.append(f"<tr><td>{html.escape(f['candidate_id'])}</td><td>{f['design_base']['net_pnl']:.2f}</td>"
                     f"<td>{stress['net_pnl']:.2f}</td><td>{stress['closed_midpoint_pnl']:.2f}</td>"
                     f"<td>{stress['closed_execution_drag']:.2f}</td><td>{stress['worst_session']:.2f}</td>"
                     f"<td>{html.escape(', '.join(f['reasons']) or 'Passed development gates')}</td></tr>")
    parts += ['</table><h2>Frozen candidate: chronological evaluation</h2><table><tr><th>Cost</th><th>PnL $</th><th>Trades</th><th>Sessions</th><th>Unresolved</th><th>Median hold minutes</th></tr>']
    for cost,r in result['validation'].items():
        parts.append(f"<tr><td>{cost}</td><td>{r['net_pnl']:.2f}</td><td>{r['closed_trades']}</td><td>{r['sessions']}</td><td>{r['unresolved']}</td><td>{r['median_hold_minutes']}</td></tr>")
    parts += ['</table><p>Evaluation failure ends the run. The JSON report retains every development candidate and skipped entry. Optional model nominations receive development evidence and must pass the same fixed gates.</p>']
    return '\n'.join(parts)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,help='Existing v1 sessions/news_flags capture; unverified until independently audited')
    parser.add_argument('--design-sessions',type=int,help='Explicit whole-session chronological cut for a supplied capture')
    parser.add_argument('--output',type=Path,required=True,help='New output directory')
    args=parser.parse_args()
    try:
        kwargs={}
        if args.input:
            from .gui.artifacts import strict_json
            with args.input.open('rb') as stream:raw=stream.read(32*1024*1024+1)
            if len(raw)>32*1024*1024:raise ValueError('capture exceeds 32 MiB')
            data=strict_json(raw)
            if (type(data) is not dict or set(data)!={'schema_version','sessions','news_flags'}
                    or type(data['schema_version']) is not int or data['schema_version']!=1):
                raise ValueError('unsupported capture envelope')
            kwargs=dict(sessions=data['sessions'],news_flags=data['news_flags'],design_sessions=args.design_sessions)
        elif args.design_sessions is not None:
            raise ValueError('--design-sessions requires --input')
        args.output.mkdir(parents=True,exist_ok=False)
        def freeze(record,protocol):
            (args.output/'protocol.json').write_bytes(encode(protocol)+b'\n')
            (args.output/'master-frozen.json').write_bytes(encode(record)+b'\n')
        result=run_master_research(**kwargs,on_freeze=freeze)
        (args.output/'report.json').write_bytes(encode(result)+b'\n')
        (args.output/'master-policy.json').write_bytes(encode(result['master'])+b'\n')
        (args.output/'report.html').write_text(render_html(result))
        code = [Path(__file__).with_name(name) for name in (
            'options_master.py', 'weekly_reversal.py', 'weekly_reversal_fixture.py',
            'weekly_reversal_suite.py', 'american_option.py',
        )]
        manifest=dict(code={str(p):sha256(p.read_bytes()).hexdigest() for p in code},
            artifacts={p.name:sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir() if p.is_file()})
        (args.output/'manifest.json').write_bytes(encode(manifest)+b'\n')
    except (OSError,ValueError,TypeError,KeyError) as error:
        parser.exit(2,f'qte: {error}\n')
    print(json.dumps(dict(output=str(args.output),master=result['master'],blockers=result['blockers']),indent=2))


if __name__=='__main__':
    main()
