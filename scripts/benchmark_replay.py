"""Synthetic scale measurement; no inference about historical strategy merit."""
import argparse
import json
import time
import qte
from qte.provenance import source_identity
from qte.strategies import MovingAverageConfig, MovingAverageRegimeStrategy

class Hold(qte.Strategy):
    def on_bar(self,ctx,bar): pass

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--bars',type=int,default=10000)
    parser.add_argument('--strategy',choices=['hold','sma'],default='hold')
    args=parser.parse_args()
    if not 1<=args.bars<=1_000_000: parser.error('bars must be in [1,1000000]')
    start=time.perf_counter()
    bars=[]
    for i in range(args.bars):
        price=100+(i%80 if i%80<40 else 80-i%80)*0.1
        bars.append(qte.Bar('SPY',i*60000000000,(i+1)*60000000000,price,price+1,price-1,price,1000))
    data=qte.Dataset.from_bars(bars,interval_ns=60000000000,source_id='synthetic-scale-v1')
    loaded=time.perf_counter()
    strategy=Hold() if args.strategy=='hold' else MovingAverageRegimeStrategy('SPY',MovingAverageConfig(5,20,1))
    result=qte.BacktestEngine(qte.BacktestConfig(100000,history_capacity=64)).run(data,strategy)
    done=time.perf_counter()
    print(json.dumps({'kind':'synthetic_not_historical','bars':args.bars,'load_seconds':loaded-start,
          'replay_seconds':done-loaded,'bars_per_second':args.bars/(done-loaded),
          'strategy':args.strategy,'fills':len(result.fills),
          'final_equity':result.equity_curve[-1].equity,'source_identity':source_identity()},indent=2))

if __name__=='__main__': main()
