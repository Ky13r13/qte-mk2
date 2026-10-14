"""Historical stock data acquisition; authenticated GET only, never trading."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .alpaca import SCHEMA_ID, load_alpaca_fixture
from .common import interval_for_timeframe, parse_rfc3339_utc_ns

ENDPOINT='https://data.alpaca.markets/v2/stocks/bars'

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def fetch_page(params, *, retries=3):
    key, secret=os.environ.get('APCA_API_KEY_ID'),os.environ.get('APCA_API_SECRET_KEY')
    if not key or not secret:
        raise ValueError('set APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment')
    request=Request(ENDPOINT+'?'+urlencode(params),headers={
        'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret,'Accept':'application/json'})
    for attempt in range(retries+1):
        try:
            with build_opener(NoRedirect()).open(request,timeout=30) as response:
                raw=response.read(32*1024*1024+1)
                if len(raw)>32*1024*1024: raise ValueError('Alpaca page exceeds 32 MiB')
                def pairs(items):
                    value={}
                    for name,item in items:
                        if name in value: raise ValueError('Alpaca page contains a duplicate JSON key')
                        value[name]=item
                    return value
                value=json.loads(raw,object_pairs_hook=pairs,
                    parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Alpaca page contains a nonfinite number')))
                _encoded(value)
                return value
        except HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==retries:
                raise ValueError(f'Alpaca HTTP {error.code}; credentials and response body omitted') from None
            delay=min(2**attempt,30)
            retry_after=error.headers.get('Retry-After','')
            if retry_after.isdigit(): delay=min(int(retry_after),30)
            time.sleep(delay)
        except (URLError,TimeoutError):
            if attempt==retries: raise ValueError('Alpaca connection failed after bounded retries') from None
            time.sleep(min(2**attempt,30))

def _encoded(value):
    pending=[value]
    while pending:
        item=pending.pop()
        if isinstance(item,str):
            try: item.encode('utf-8')
            except UnicodeEncodeError: raise ValueError('Alpaca page contains invalid Unicode') from None
        elif isinstance(item,float) and not math.isfinite(item):
            raise ValueError('Alpaca page contains a nonfinite number')
        elif isinstance(item,dict): pending.extend(item.keys()); pending.extend(item.values())
        elif isinstance(item,(list,tuple)): pending.extend(item)
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def stock_bars_request(*,symbols,timeframe,start,end,feed,action_free):
    """Return the canonical, validated provider request and its cache identity."""
    interval=interval_for_timeframe(timeframe)
    if feed not in ('iex','sip'): raise ValueError('feed must explicitly be iex or sip')
    if action_free is not True: raise ValueError('action_free=True declaration required')
    if not isinstance(symbols,(list,tuple)) or not symbols or any(
        not isinstance(s,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',s) for s in symbols):
        raise ValueError('symbols must be a nonempty list of normalized stock symbols')
    if len(set(symbols))!=len(symbols): raise ValueError('duplicate requested symbols')
    if parse_rfc3339_utc_ns(start)>=parse_rfc3339_utc_ns(end): raise ValueError('start must precede end')
    params={'symbols':','.join(sorted(symbols)),'timeframe':timeframe,'start':start,'end':end,
            'feed':feed,'adjustment':'raw','currency':'USD','sort':'asc','asof':'-','limit':10000}
    request_id=hashlib.sha256(_encoded({'version':1,'request':params})).hexdigest()
    return params,request_id,interval

def download_stock_bars(*,symbols,timeframe,start,end,feed,action_free,cache_dir,
                        page_fetcher=None,max_pages=10000,max_page_bytes=32*1024*1024,
                        max_total_bytes=None):
    """Immutable complete cache keyed by explicit request. End is inclusive (Alpaca).

    action_free is a user declaration; downloading does not establish it.
    Cache reuse is offline, checksum-verified, and never refreshes implicitly.
    """
    params,request_id,interval=stock_bars_request(symbols=symbols,timeframe=timeframe,start=start,
        end=end,feed=feed,action_free=action_free)
    if type(max_pages) is not int or max_pages<1: raise ValueError('max_pages must be positive')
    if type(max_page_bytes) is not int or max_page_bytes<1: raise ValueError('max_page_bytes must be positive')
    if max_total_bytes is not None and (type(max_total_bytes) is not int or max_total_bytes<1):
        raise ValueError('max_total_bytes must be positive')
    root=Path(cache_dir); root.mkdir(parents=True,exist_ok=True)
    final=root/request_id
    if final.exists():
        metadata=json.loads((final/'download.json').read_text())
        if metadata['request']!=params: raise ValueError('cached request mismatch')
        for filename,digest in metadata['sha256'].items():
            if Path(filename).name!=filename: raise ValueError('invalid cache filename')
            if hashlib.sha256((final/filename).read_bytes()).hexdigest()!=digest:
                raise ValueError('cached file checksum mismatch')
        if 'bars.json' not in metadata['sha256']: raise ValueError('missing cache dataset checksum')
        load_alpaca_fixture(final/'bars.json',interval_ns=interval)
        return final/'bars.json'
    fetch=page_fetcher or fetch_page
    # TemporaryDirectory cleans only this invocation's incomplete staging files.
    with tempfile.TemporaryDirectory(prefix='.staging-',dir=root) as temporary:
        stage=Path(temporary)
        combined={s:[] for s in sorted(symbols)}; token=None; seen=set(); checksums={}; total_bytes=0
        for page_index in range(max_pages):
            page=fetch({**params,**({'page_token':token} if token else {})})
            if not isinstance(page,dict) or 'next_page_token' not in page or not isinstance(page.get('bars'),dict):
                raise ValueError('malformed Alpaca page')
            filename=f'page-{page_index:06d}.json'; raw=_encoded(page)
            if len(raw)>max_page_bytes: raise ValueError('Alpaca page exceeds artifact file limit')
            total_bytes+=len(raw)
            if max_total_bytes is not None and total_bytes>max_total_bytes:
                raise ValueError('Alpaca pages exceed artifact byte limit')
            (stage/filename).write_bytes(raw); checksums[filename]=hashlib.sha256(raw).hexdigest()
            for symbol,rows in page['bars'].items():
                if symbol not in combined or not isinstance(rows,list): raise ValueError('unexpected symbol or bar list')
                for row in rows:
                    if not isinstance(row,dict): raise ValueError('invalid bar row')
                    t=parse_rfc3339_utc_ns(row.get('t'))
                    if not parse_rfc3339_utc_ns(start)<=t<=parse_rfc3339_utc_ns(end):
                        raise ValueError('bar outside requested inclusive time range')
                combined[symbol].extend(rows)
            token=page['next_page_token']
            if token is None: break
            if not isinstance(token,str) or not token or token in seen: raise ValueError('invalid or cyclic page token')
            seen.add(token)
        else: raise ValueError('pagination exceeded max_pages')
        if any(not rows for rows in combined.values()): raise ValueError('requested symbol returned no bars')
        fixture={'_qte':{'schema':SCHEMA_ID,'feed':feed,'timeframe':timeframe,
                         'adjustment':'raw','action_free':True},'bars':combined,'next_page_token':None}
        raw=_encoded(fixture)
        if len(raw)>max_page_bytes: raise ValueError('Alpaca dataset exceeds artifact file limit')
        total_bytes+=len(raw)
        if max_total_bytes is not None and total_bytes>max_total_bytes:
            raise ValueError('Alpaca download exceeds artifact byte limit')
        (stage/'bars.json').write_bytes(raw)
        checksums['bars.json']=hashlib.sha256(raw).hexdigest()
        load_alpaca_fixture(stage/'bars.json',interval_ns=interval)
        (stage/'download.json').write_bytes(_encoded({'version':1,'request':params,'sha256':checksums,
                'page_count':page_index+1,'fetched_at_ns':time.time_ns(),
                'assumptions':['raw','declared_action_free','asof_mapping_disabled','historical_zero_publication_delay']}))
        # Directory rename publishes a fully validated dataset atomically.
        stage.rename(final)
    return final/'bars.json'
