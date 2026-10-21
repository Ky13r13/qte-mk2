"""Synthetic scale measurement; no inference about historical strategy merit."""
import argparse
import json
from qte.benchmark import run_benchmark

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--bars',type=int,default=10000)
    parser.add_argument('--strategy',choices=['hold','sma'],default='hold')
    args=parser.parse_args()
    if not 1<=args.bars<=1_000_000: parser.error('bars must be in [1,1000000]')
    print(json.dumps(run_benchmark(args.bars, args.strategy),indent=2))

if __name__=='__main__': main()
