"""Bounded development search and chronological Schwab minute research."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import qte
from .adapters.schwab_research import read_full_minute_sessions
from .minute_option_scenarios import MODEL_SCENARIOS, value_trades
from .minute_replay import CASH, COSTS, HOLD, LIMIT, SLEEVE, dataset, replay, summarize
from .strategies.minute_signals import FAMILIES, Policy, WARMUP, features

SYMBOLS = ('SPY', 'QQQ')
ROUNDS = 3
DEVELOPMENT_DAYS = 15
MIN_TRADES = 30


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.write('\n')


def subset(sessions, days):
    return {day: sessions[day] for day in days}


class SliceReplay:
    def __init__(self, sessions, observations, days):
        self.days = days
        self.sessions = {s: subset(sessions[s], days) for s in SYMBOLS}
        self.observations = {s: subset(observations[s], days) for s in SYMBOLS}
        self.datasets = {s: dataset(s, self.sessions[s]) for s in SYMBOLS}

    def evaluate(self, policy):
        outcome = {}
        for cost in COSTS:
            cases = {}
            for symbol in SYMBOLS:
                trades, skips = replay(self.datasets[symbol], self.sessions[symbol],
                                       self.observations[symbol], policy, cost)
                cases[symbol] = dict(metrics=summarize(self.days, trades), trades=trades, skipped=skips)
            combined = [t for s in SYMBOLS for t in cases[s]['trades']]
            outcome[cost] = dict(combined=summarize(self.days, combined), symbols=cases)
        return outcome


def rank(outcome):
    stressed = outcome['stress_delayed']['combined']
    enough = stressed['trades'] >= 12 and stressed['active_dates'] >= 5
    return (enough, min(outcome[c]['combined']['net_pnl'] for c in COSTS), stressed['trades'])


def failures(outcome):
    failures = []
    if any(outcome[c]['combined']['net_pnl'] <= 0 for c in COSTS):
        failures.append('nonpositive_after_costs_or_delay')
    if any(outcome[c]['combined']['trades'] < MIN_TRADES or outcome[c]['combined']['active_dates'] < 8 for c in COSTS):
        failures.append('sparse_results')
    if any(outcome[c]['combined']['net_without_best_date'] <= 0 for c in COSTS):
        failures.append('nonpositive_excluding_best_date')
    if any(outcome['stress_delayed']['symbols'][s]['metrics']['net_pnl'] <= 0 for s in SYMBOLS):
        failures.append('fails_cross_symbol_check')
    return failures


def selection_rank(outcome):
    return (not failures(outcome), *rank(outcome))


def search(low, baseline, high, evaluate):
    """Compare both halves each round. No monotonicity assumption."""
    attempts = {}
    rounds = []

    def sample(value):
        value = round(value, 8)
        if value not in attempts:
            attempts[value] = evaluate(value)

    for value in (low, baseline, high, (low+high)/2):
        sample(value)
    for iteration in range(ROUNDS):
        center = (low+high)/2
        proposals = (round((low+center)/2, 8), round((center+high)/2, 8))
        for value in proposals:
            sample(value)
        eligible = sorted(v for v in attempts if low <= v <= high)
        incumbent = max(eligible, key=lambda v: (selection_rank(attempts[v]), -abs(v-baseline), -v))
        # Halve the search interval around the local winner, including endpoints.
        span = (high-low)/2
        new_low = min(max(incumbent-span/2, low), high-span)
        new_high = new_low+span
        rounds.append(dict(round=iteration+1, before=[low,high],
            proposals=[dict(role=role, threshold=value, failed_checks=failures(attempts[value]),
                            stressed=attempts[value]['stress_delayed']['combined'])
                       for role,value in zip(('lower_half_probe','upper_half_probe'),proposals,strict=True)],
            round_winner=incumbent, after=[new_low,new_high]))
        low, high = new_low, new_high
    best = max(attempts, key=lambda v: (selection_rank(attempts[v]), -abs(v-baseline), -v))
    return best, rounds


def compact(outcome):
    return {key: dict(combined=row['combined'], symbols={s: row['symbols'][s]['metrics'] for s in SYMBOLS})
            for key, row in outcome.items()}


def run(capture_root, output):
    output.mkdir(parents=True, exist_ok=False)
    filenames = {s: capture_root/f'{s}-minute-20261012-20261120.json' for s in SYMBOLS}
    loaded = {s: read_full_minute_sessions(filenames[s], s) for s in SYMBOLS}
    sessions = {s: result[0] for s, result in loaded.items()}
    dates = tuple(sessions[SYMBOLS[0]])
    if len(dates) != 30 or any(tuple(sessions[s]) != dates for s in SYMBOLS):
        raise ValueError('Fixed study requires the same 30 complete dates for both symbols')
    boundary = WARMUP+DEVELOPMENT_DAYS
    design_dates, validation_dates = dates[WARMUP:boundary], dates[boundary:]
    protocol = dict(schema_version=1, families=FAMILIES, rounds=ROUNDS, costs=COSTS, symbols=SYMBOLS,
        hold_minutes=HOLD, max_entries_per_symbol_and_day=LIMIT, sleeve=SLEEVE, starting_cash=CASH,
        warmup_dates=dates[:WARMUP], development_dates=design_dates, evaluation_dates=validation_dates,
        prior_overlap='chronological retrospective; previous Robinhood data overlap and some dates independently inspected',
        guards='Search only one threshold at a time per family and fixed regime toggle. Keep all trial outputs. Paired calendar days, cash reference.',
        selection='Rank passing candidates first, then prefer at least 12 trades and 5 dates, then worst combined net PnL across costs/delay.',
        gates='All costs positive, all ex-best-date positive, at least 30 trades and eight active dates at each cost, delayed stress positive on each symbol.',
        clock='provider bar start interpreted as UTC ms; available at end; open-to-open five-minute hold; stress_delayed defers entry one minute; sizing chosen at signal close.',
        option_scenarios=MODEL_SCENARIOS,
        references={'research_integrity':'https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf'},
        data={s: dict(path=str(filenames[s]),sha256=loaded[s][1]) for s in SYMBOLS})
    write(output/'protocol.json', protocol)
    design_observations = {s: features(subset(sessions[s], dates[:boundary])) for s in SYMBOLS}
    development = SliceReplay(sessions, design_observations, design_dates)
    search_log, trials, finalists = [], {}, {}

    def trial_key(policy):
        return f'{policy.family}-{int(policy.gated)}-{policy.threshold:.8f}'

    def trial(policy):
        key = trial_key(policy)
        if key not in trials:
            value = development.evaluate(policy)
            trials[key] = value
            write(output/f'development-{key}.json', dict(policy=asdict(policy),results=value))
        return trials[key]

    for family, bounds in FAMILIES.items():
        candidates = []
        for gated in (False, True):
            threshold, rounds = search(*bounds, lambda threshold: trial(Policy(family, threshold, gated)))
            candidates.append(Policy(family, threshold, gated))
            search_log.append(dict(family=family, gated=gated, rounds=rounds))
        finalists[family] = max(candidates, key=lambda p: (selection_rank(trial(p)), not p.gated))
    qualified = [p for p in finalists.values() if not failures(trial(p))]
    master = max(qualified,key=lambda p:rank(trial(p))) if qualified else None
    frozen = dict(master=asdict(master) if master else 'cash', finalists={k:asdict(p) for k,p in finalists.items()},
                  baselines={f:asdict(Policy(f,b[1],False)) for f,b in FAMILIES.items()}, search=search_log,
                  trial_count=len(trials), decision='frozen before evaluation; rejected master stays cash')
    write(output/'frozen.json', frozen)
    print(f'Frozen {len(trials)} trials before evaluation. Master: {frozen["master"]}', flush=True)
    observation = {s: features(sessions[s]) for s in SYMBOLS}
    evaluation = SliceReplay(sessions, observation, validation_dates)
    report = {}
    for family,p in finalists.items():
        baseline = Policy(family,FAMILIES[family][1],False)
        comparisons = {}
        for name, candidate in (('refined',p),('fixed',baseline)):
            result = evaluation.evaluate(candidate) if name == 'refined' or candidate != p else comparisons['refined']['_trades']
            option_models = {s: value_trades(result['stress_delayed']['symbols'][s]['trades']) for s in SYMBOLS}
            comparisons[name] = dict(policy=asdict(candidate),development=compact(trials[trial_key(candidate)]),
                evaluation=compact(result),failures=failures(result),option_models=option_models,_trades=result)
        report[family] = comparisons
    write(output/'evaluation.json', report)
    summary = {family: {name: {k: value[k] for k in ('policy','development','evaluation','failures')}
                        for name,value in comparisons.items()} for family,comparisons in report.items()}
    master_status = ('no_development_candidate' if master is None else
                     'evaluation_failed' if report[master.family]['refined']['failures'] else 'passed_retrospective_screen')
    write(output/'summary.json', dict(frozen_master=frozen['master'], master_status=master_status,
                                      operational_policy='cash', trial_count=len(trials), results=summary))
    from .minute_refinement_report import render_report
    (output/'report.html').write_text(render_report(protocol,frozen,report,sessions))
    code = [Path(__file__), Path(__file__).with_name('minute_replay.py'), Path(__file__).with_name('minute_option_scenarios.py'),
            Path(__file__).with_name('minute_refinement_report.py'), Path(__file__).parent/'adapters/schwab_research.py',
            Path(__file__).parent/'strategies/minute_signals.py', Path(__file__).with_name('american_option.py'),Path(qte._core.__file__)]
    write(output/'manifest.json',dict(code={str(p):sha256(p.read_bytes()).hexdigest() for p in code},
                                      artifacts={p.name:sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir())}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    run(args.captures,args.output)


if __name__=='__main__':
    main()
