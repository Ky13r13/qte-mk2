"""Historical stock data acquisition; authenticated GET only, never trading."""
from __future__ import annotations
import hashlib
import json
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
                return json.loads(raw)
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
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def download_stock_bars(*,symbols,timeframe,start,end,feed,action_free,cache_dir,
                        page_fetcher=None,max_pages=10000):
    """Immutable complete cache keyed by explicit request. End is inclusive (Alpaca).

    action_free is a user declaration; downloading does not establish it.
    Cache reuse is offline, checksum-verified, and never refreshes implicitly.
    """
    interval=interval_for_timeframe(timeframe)
    if feed not in ('iex','sip'): raise ValueError('feed must explicitly be iex or sip')
    if action_free is not True: raise ValueError('action_free=True declaration required')
    if not isinstance(symbols,(list,tuple)) or not symbols or any(
        not isinstance(s,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',s) for s in symbols):
        raise ValueError('symbols must be a nonempty list of normalized stock symbols')
    if len(set(symbols))!=len(symbols): raise ValueError('duplicate requested symbols')
    if parse_rfc3339_utc_ns(start)>=parse_rfc3339_utc_ns(end): raise ValueError('start must precede end')
    if type(max_pages) is not int or max_pages<1: raise ValueError('max_pages must be positive')
    params={'symbols':','.join(sorted(symbols)),'timeframe':timeframe,'start':start,'end':end,
            'feed':feed,'adjustment':'raw','currency':'USD','sort':'asc','asof':'-','limit':10000}
    request_id=hashlib.sha256(_encoded({'version':1,'request':params})).hexdigest()
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
        combined={s:[] for s in sorted(symbols)}; token=None; seen=set(); checksums={}
        for page_index in range(max_pages):
            page=fetch({**params,**({'page_token':token} if token else {})})
            if not isinstance(page,dict) or 'next_page_token' not in page or not isinstance(page.get('bars'),dict):
                raise ValueError('malformed Alpaca page')
            filename=f'page-{page_index:06d}.json'; raw=_encoded(page)
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
        raw=_encoded(fixture); (stage/'bars.json').write_bytes(raw)
        checksums['bars.json']=hashlib.sha256(raw).hexdigest()
        load_alpaca_fixture(stage/'bars.json',interval_ns=interval)
        (stage/'download.json').write_bytes(_encoded({'version':1,'request':params,'sha256':checksums,
                'page_count':page_index+1,'fetched_at_ns':time.time_ns(),
                'assumptions':['raw','declared_action_free','asof_mapping_disabled','historical_zero_publication_delay']}))
        # Directory rename publishes a fully validated dataset atomically.
        stage.rename(final)
    return final/'bars.json'
