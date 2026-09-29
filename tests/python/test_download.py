import json
import pytest
from qte.adapters.alpaca_download import download_stock_bars
from test_adapters import ALPACA

def request(tmp_path,fetch,**kwargs):
    return download_stock_bars(symbols=['SPY'],timeframe='1Hour',start='2024-01-02T14:00:00Z',
        end='2024-01-02T17:00:00Z',feed='iex',action_free=True,cache_dir=tmp_path,
        page_fetcher=fetch,**kwargs)

def test_pages_merge_cache_is_offline_and_corruption_rejected(tmp_path):
    rows=json.loads(ALPACA.read_text())['bars']['SPY']; calls=[]
    def fetch(params):
        calls.append(params)
        return {'bars':{'SPY':rows[:1] if len(calls)==1 else rows[1:]},
                'next_page_token':'second' if len(calls)==1 else None}
    path=request(tmp_path,fetch)
    assert len(calls)==2 and calls[1]['page_token']=='second'
    assert calls[0]['asof']=='-' and calls[0]['adjustment']=='raw'
    assert len(json.loads(path.read_text())['bars']['SPY'])==3
    assert request(tmp_path,lambda _:pytest.fail('cache must not fetch'))==path
    path.write_text('{}')
    with pytest.raises(ValueError,match='checksum'): request(tmp_path,fetch)

@pytest.mark.parametrize('kind',['cycle','missing','duplicate','empty','max'])
def test_bad_pages_never_publish_cache(tmp_path,kind):
    rows=json.loads(ALPACA.read_text())['bars']['SPY']
    def fetch(params):
        if kind=='missing': return {'bars':{'SPY':rows}}
        if kind=='empty': return {'bars':{},'next_page_token':None}
        if kind=='duplicate': return {'bars':{'SPY':[rows[0],rows[0]]},'next_page_token':None}
        return {'bars':{'SPY':rows},'next_page_token':'repeated'}
    with pytest.raises(ValueError): request(tmp_path,fetch,max_pages=1 if kind=='max' else 3)
    assert list(tmp_path.iterdir())==[]

def test_network_retry_is_bounded_and_credentials_are_not_in_errors(monkeypatch):
    import qte.adapters.alpaca_download as module
    from urllib.error import HTTPError
    monkeypatch.setenv('APCA_API_KEY_ID','test-key')
    monkeypatch.setenv('APCA_API_SECRET_KEY','test-secret')
    delays=[]; calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            raise HTTPError(request.full_url,429,'test-secret',{'Retry-After':'999'},None)
    monkeypatch.setattr(module,'build_opener',lambda _:Opener())
    monkeypatch.setattr(module.time,'sleep',delays.append)
    with pytest.raises(ValueError) as exc: module.fetch_page({'feed':'iex'},retries=2)
    assert 'test-secret' not in str(exc.value)
    assert len(calls)==3 and delays==[30,30]

def test_missing_credentials_fail_without_network(monkeypatch):
    import qte.adapters.alpaca_download as module
    monkeypatch.delenv('APCA_API_KEY_ID',raising=False)
    monkeypatch.delenv('APCA_API_SECRET_KEY',raising=False)
    with pytest.raises(ValueError,match='environment'): module.fetch_page({})
