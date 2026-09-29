from dataclasses import replace
import pytest
from qte.paper import (PaperCoordinator, PaperError, Intent, BrokerOrder,
    AccountSnapshot, ExecutionReport, TradingControls)

class Broker:
    def __init__(self):
        self.orders={}; self.cash='10000'; self.positions={}; self.fail_submit=False; self.fail_cancel=False
        self.paper=True; self.submissions=0
    def snapshot(self):
        return AccountSnapshot('paper-account',self.paper,self.cash,self.positions,
            tuple(o for o in self.orders.values() if o.status in ('open','partially_filled')))
    def lookup(self,id): return self.orders.get(id)
    def submit(self,id,intent):
        self.submissions+=1
        order=BrokerOrder(id,'broker-'+id,intent.symbol,intent.side,intent.quantity,0,'open')
        self.orders[id]=order
        if self.fail_submit: raise TimeoutError()
        return order
    def cancel(self,id):
        if self.fail_cancel: raise TimeoutError()
        self.orders[id]=replace(self.orders[id],status='canceled')
        return self.orders[id]

def setup(tmp_path,broker=None,local=None,clock=lambda:0,risk=lambda i:True):
    broker=broker or Broker()
    local=local or (lambda:AccountSnapshot('paper-account',True,'10000',{}))
    c=PaperCoordinator(tmp_path/'journal.sqlite',broker,account_id='paper-account',
        controls=TradingControls(frozenset({'SPY'}),10,'1000'),risk_check=risk,local_snapshot=local,clock=clock)
    return c,broker

def test_startup_ack_cancel_and_restart_identity(tmp_path):
    c,b=setup(tmp_path)
    with pytest.raises(PaperError): c.submit(Intent('SPY','buy',1,'100'))
    c.reconcile(); first=c.submit(Intent('SPY','buy',1,'100'))
    c.cancel(first); assert c.orders()[0]['status']=='canceled'
    c.close()
    c,b=setup(tmp_path,b); c.reconcile()
    second=c.submit(Intent('SPY','buy',1,'100'))
    assert first!=second and first.rsplit('-',1)[0]==second.rsplit('-',1)[0]
    c.close()

def test_timeout_is_recovered_by_id_without_resubmit(tmp_path):
    c,b=setup(tmp_path); c.reconcile(); b.fail_submit=True
    with pytest.raises(PaperError,match='uncertain'): c.submit(Intent('SPY','buy',1,'100'))
    assert c.orders()[0]['status']=='unknown'
    c.close(); c,b=setup(tmp_path,b); c.reconcile()
    assert c.orders()[0]['status']=='open' and b.submissions==1
    c.close()

def test_absent_uncertain_order_stays_blocked(tmp_path):
    c,b=setup(tmp_path); c.reconcile(); b.fail_submit=True
    with pytest.raises(PaperError): c.submit(Intent('SPY','buy',1,'100'))
    b.orders.clear()
    with pytest.raises(PaperError,match='absent'): c.reconcile()
    assert not c.ready; c.close()

def test_execution_dedup_watermark_partial_fill_and_terminal_regression(tmp_path):
    state={'local':AccountSnapshot('paper-account',True,'10000',{})}
    c,b=setup(tmp_path,local=lambda:state['local']); c.reconcile()
    id=c.submit(Intent('SPY','buy',2,'100'))
    b.orders[id]=replace(b.orders[id],filled_quantity=1,status='partially_filled')
    c.acknowledge(b.orders[id]); assert not c.ready
    fact=ExecutionReport('e1',id,1,'100','0')
    assert c.record_execution(fact) and not c.record_execution(fact)
    b.cash='9900'; b.positions={'SPY':1}
    state['local']=replace(state['local'],cash='9900',positions={'SPY':1})
    with pytest.raises(PaperError,match='watermark'): c.reconcile()
    state['local']=replace(state['local'],applied_execution_ids=frozenset({'e1'}))
    c.reconcile(); assert c.ready
    with pytest.raises(PaperError,match='conflicting'): c.record_execution(replace(fact,price='101'))
    c.reconcile()
    with pytest.raises(PaperError,match='overfill'): c.record_execution(ExecutionReport('e2',id,2,'100','0'))
    c.reconcile(); c.cancel(id)
    with pytest.raises(PaperError,match='terminal'): c.acknowledge(replace(b.orders[id],status='partially_filled'))
    c.close()

@pytest.mark.parametrize('kind',['external','cash','positions','live'])
def test_reconciliation_mismatches_block_submission(tmp_path,kind):
    c,b=setup(tmp_path)
    if kind=='external': b.orders['external']=BrokerOrder('external','x','SPY','buy',1,0,'open')
    if kind=='cash': b.cash='9999'
    if kind=='positions': b.positions={'SPY':1}
    if kind=='live': b.paper=False
    with pytest.raises(PaperError): c.reconcile()
    assert not c.ready; c.close()

def test_halt_persists_and_cancel_allowed(tmp_path):
    c,b=setup(tmp_path); c.reconcile(); id=c.submit(Intent('SPY','buy',1,'100'))
    c.halt('operator'); c.cancel(id); c.close()
    c,b=setup(tmp_path,b); c.reconcile(); assert not c.ready
    c.reconcile(clear_halt=True); assert c.ready; c.close()

def test_stale_connection_and_cancel_failure_require_reconcile(tmp_path):
    now=[0]; c,b=setup(tmp_path,clock=lambda:now[0]); c.reconcile()
    now[0]=31
    with pytest.raises(PaperError,match='expired'): c.submit(Intent('SPY','buy',1,'100'))
    c.reconcile(); id=c.submit(Intent('SPY','buy',1,'100')); b.fail_cancel=True
    with pytest.raises(PaperError,match='uncertain'): c.cancel(id)
    assert not c.ready; c.close()

def test_controls_and_risk_block_before_external_call(tmp_path):
    c,b=setup(tmp_path,risk=lambda _:False); c.reconcile()
    for intent in (Intent('QQQ','buy',1,'100'),Intent('SPY','buy',11,'100'),
                   Intent('SPY','buy',1,'2000'),Intent('SPY','buy',1,'100')):
        with pytest.raises(PaperError): c.submit(intent)
    assert b.submissions==0 and c.orders()==[]; c.close()

def test_second_owner_is_blocked_and_operator_recovery_halts(tmp_path):
    c,b=setup(tmp_path); c.reconcile()
    other,_=setup(tmp_path,b)
    with pytest.raises(PaperError,match='leased'): other.reconcile()
    other.recover_lease(reason='test simulates stopped previous owner')
    other.reconcile(); assert not other.ready
    with pytest.raises(PaperError,match='lease'): c.submit(Intent('SPY','buy',1,'100'))
    c.close(); other.close()

def test_immediate_fill_acknowledgement_blocks_until_accounting(tmp_path):
    c,b=setup(tmp_path); c.reconcile()
    original=b.submit
    def fill(id,intent):
        order=replace(original(id,intent),status='filled',filled_quantity=intent.quantity)
        b.orders[id]=order
        return order
    b.submit=fill
    c.submit(Intent('SPY','buy',1,'100'))
    assert not c.ready
    with pytest.raises(PaperError,match='journal disagree'): c.reconcile()
    c.close()

def test_restart_can_import_missing_executions_before_reconciliation(tmp_path):
    state={'local':AccountSnapshot('paper-account',True,'10000',{})}
    c,b=setup(tmp_path,local=lambda:state['local']); c.reconcile()
    id=c.submit(Intent('SPY','buy',1,'100')); c.close()
    b.orders[id]=replace(b.orders[id],status='filled',filled_quantity=1)
    b.cash='9900'; b.positions={'SPY':1}
    state['local']=replace(state['local'],cash='9900',positions={'SPY':1})
    c,b=setup(tmp_path,b,local=lambda:state['local'])
    with pytest.raises(PaperError): c.reconcile()
    assert c.record_execution(ExecutionReport('late-fill',id,1,'100','0'))
    state['local']=replace(state['local'],applied_execution_ids=frozenset({'late-fill'}))
    c.reconcile(); assert c.ready
    c.close()
