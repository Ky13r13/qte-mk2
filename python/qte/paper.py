"""Offline-testable paper coordination. No broker SDK, credentials or network."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Protocol
import uuid

TERMINAL=frozenset({'filled','canceled','rejected'})
STATUSES=TERMINAL | {'open','partially_filled'}

class PaperError(RuntimeError): pass

def money(value):
    try: result=Decimal(str(value))
    except InvalidOperation: raise PaperError('invalid numeric value') from None
    if not result.is_finite(): raise PaperError('numeric value must be finite')
    return result

@dataclass(frozen=True)
class Intent:
    symbol: str
    side: str
    quantity: int
    reference_price: str

@dataclass(frozen=True)
class BrokerOrder:
    client_id: str
    broker_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int
    status: str

@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    paper: bool
    cash: str
    positions: dict[str,int]
    open_orders: tuple[BrokerOrder,...] = ()
    applied_execution_ids: frozenset[str] = frozenset()

@dataclass(frozen=True)
class ExecutionReport:
    execution_id: str
    client_id: str
    quantity: int
    price: str
    commission: str

class PaperGateway(Protocol):
    def snapshot(self) -> AccountSnapshot: ...
    def lookup(self, client_id: str) -> BrokerOrder | None: ...
    def submit(self, client_id: str, intent: Intent) -> BrokerOrder: ...
    def cancel(self, client_id: str) -> BrokerOrder: ...

@dataclass(frozen=True)
class TradingControls:
    symbols: frozenset[str]
    max_order_quantity: int
    max_order_notional: str
    heartbeat_timeout_seconds: float = 30.0

class PaperCoordinator:
    def __init__(self, journal: Path, gateway: PaperGateway, *, account_id: str,
                 controls: TradingControls, risk_check: Callable[[Intent],bool],
                 local_snapshot: Callable[[],AccountSnapshot], clock=time.monotonic):
        if not account_id or not controls.symbols or type(controls.max_order_quantity) is not int or controls.max_order_quantity<1:
            raise PaperError('account, symbols and positive quantity control required')
        if money(controls.max_order_notional)<=0 or not 0<controls.heartbeat_timeout_seconds<3600:
            raise PaperError('invalid notional or heartbeat control')
        self.gateway=gateway; self.account_id=account_id; self.controls=controls
        self.risk_check=risk_check; self.local_snapshot=local_snapshot; self.clock=clock
        self.ready=False; self.connected=False; self.last_heartbeat=0.0
        self.owner=uuid.uuid4().hex
        self.db=sqlite3.connect(journal)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(
              client_id TEXT PRIMARY KEY,broker_id TEXT UNIQUE,symbol TEXT NOT NULL,side TEXT NOT NULL,
              quantity INTEGER NOT NULL,filled INTEGER NOT NULL,status TEXT NOT NULL,reference_price TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS executions(
              execution_id TEXT PRIMARY KEY,client_id TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL,payload TEXT NOT NULL);
        ''')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('account',?)",(account_id,))
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('namespace',?)",(uuid.uuid4().hex[:12],))
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('next_id','1')")
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('halted','0')")
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('lease','')")
        if self._get('account')!=account_id:
            self.db.close(); raise PaperError('journal belongs to a different account')

    def _get(self,key):
        return self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()[0]

    def _audit(self,event,payload):
        self.db.execute('INSERT INTO audit(event,payload) VALUES(?,?)',
                        (event,json.dumps(payload,sort_keys=True)))

    def close(self):
        self.ready=False
        with self.db:
            self.db.execute("UPDATE metadata SET value='' WHERE key='lease' AND value=?",(self.owner,))
        self.db.close()

    def recover_lease(self, *, reason: str):
        """Operator-only takeover after confirming the previous process is stopped."""
        if not reason.strip(): raise PaperError('recovery requires an operator reason')
        self.ready=False
        with self.db:
            self.db.execute("UPDATE metadata SET value='' WHERE key='lease'")
            self.db.execute("UPDATE metadata SET value='1' WHERE key='halted'")
            self._audit('operator_lease_recovery',{'reason':reason})

    def _claim(self):
        with self.db:
            changed=self.db.execute("UPDATE metadata SET value=? WHERE key='lease' AND (value='' OR value=?)",
                                    (self.owner,self.owner)).rowcount
            if not changed: raise PaperError('journal leased by another coordinator; explicit recovery required')

    def disconnected(self):
        self.connected=False; self.ready=False
        with self.db: self._audit('disconnected',{})

    def heartbeat(self):
        if not self.connected: raise PaperError('reconciliation required after disconnect')
        self.last_heartbeat=self.clock()

    def halt(self, reason: str):
        if not reason: raise PaperError('halt requires a reason')
        self.ready=False
        with self.db:
            self.db.execute("UPDATE metadata SET value='1' WHERE key='halted'")
            self._audit('halt',{'reason':reason})

    def _connection(self):
        if self._get('lease')!=self.owner: raise PaperError('coordinator does not own journal lease')
        if not self.connected or self.clock()-self.last_heartbeat>self.controls.heartbeat_timeout_seconds:
            self.disconnected(); raise PaperError('connection heartbeat expired; reconcile before trading')

    def _apply_order(self, order: BrokerOrder):
        row=self.db.execute('SELECT * FROM orders WHERE client_id=?',(order.client_id,)).fetchone()
        if row is None: raise PaperError('unknown broker order')
        if order.status not in STATUSES or not order.broker_id or type(order.filled_quantity) is not int:
            raise PaperError('invalid acknowledgement')
        if (order.symbol,order.side,order.quantity)!=(row['symbol'],row['side'],row['quantity']):
            raise PaperError('acknowledgement intent mismatch')
        if row['broker_id'] and row['broker_id']!=order.broker_id: raise PaperError('broker ID changed')
        if not row['filled']<=order.filled_quantity<=row['quantity']: raise PaperError('fill quantity regression or overfill')
        if order.status=='filled' and order.filled_quantity!=order.quantity: raise PaperError('incomplete filled acknowledgement')
        if order.status=='open' and order.filled_quantity!=0: raise PaperError('open acknowledgement has fills')
        if order.status=='partially_filled' and not 0<order.filled_quantity<order.quantity: raise PaperError('invalid partial fill')
        if order.status=='rejected' and order.filled_quantity!=0: raise PaperError('rejected order has fills')
        if row['status'] in TERMINAL and (order.status!=row['status'] or order.filled_quantity!=row['filled']):
            raise PaperError('terminal order changed')
        self.db.execute('UPDATE orders SET broker_id=?,filled=?,status=? WHERE client_id=?',
            (order.broker_id,order.filled_quantity,order.status,order.client_id))
        self._audit('broker_order',order.__dict__)

    def reconcile(self, *, clear_halt=False):
        self.ready=False; self.connected=False
        self._claim()
        try:
            remote=self.gateway.snapshot(); local=self.local_snapshot()
            if remote.paper is not True or remote.account_id!=self.account_id or local.account_id!=self.account_id or local.paper is not True:
                raise PaperError('paper account identity mismatch')
            for snapshot in (remote,local):
                if any(type(q) is not int for q in snapshot.positions.values()): raise PaperError('positions must be whole shares')
            if money(remote.cash)!=money(local.cash) or {s:q for s,q in remote.positions.items() if q} != {s:q for s,q in local.positions.items() if q}:
                raise PaperError('ledger reconciliation mismatch')
            rows=self.db.execute('SELECT * FROM orders ORDER BY client_id').fetchall()
            known={row['client_id'] for row in rows}
            if len({o.client_id for o in remote.open_orders})!=len(remote.open_orders): raise PaperError('duplicate broker open orders')
            if any(o.client_id not in known for o in remote.open_orders): raise PaperError('unknown external open order')
            with self.db:
                for order in remote.open_orders: self._apply_order(order)
                for row in rows:
                    if row['status'] not in TERMINAL:
                        order=self.gateway.lookup(row['client_id'])
                        if order is None: raise PaperError('uncertain order absent from broker lookup')
                        self._apply_order(order)
                actual={row['client_id'] for row in self.db.execute(
                    "SELECT client_id FROM orders WHERE status IN ('open','partially_filled')")}
                if actual!={o.client_id for o in remote.open_orders}:
                    raise PaperError('inconsistent broker open-order snapshot')
                facts=self.db.execute('SELECT execution_id,client_id,payload FROM executions').fetchall()
                if not {r['execution_id'] for r in facts}.issubset(local.applied_execution_ids):
                    raise PaperError('ledger execution watermark is behind journal')
                totals={}
                for fact in facts:
                    totals[fact['client_id']]=totals.get(fact['client_id'],0)+json.loads(fact['payload'])['quantity']
                for row in self.db.execute('SELECT client_id,filled FROM orders'):
                    if totals.get(row['client_id'],0)!=row['filled']:
                        raise PaperError('broker fills and execution journal disagree')
                if clear_halt:
                    self.db.execute("UPDATE metadata SET value='0' WHERE key='halted'")
                self._audit('reconciled',{'clear_halt':clear_halt})
            self.connected=True; self.last_heartbeat=self.clock()
            self.ready=self._get('halted')=='0'
        except Exception:
            self.ready=False; self.connected=False
            raise

    def submit(self,intent: Intent) -> str:
        self._connection()
        if not self.ready or self._get('halted')!='0': raise PaperError('submissions disabled')
        if intent.symbol not in self.controls.symbols or intent.side not in ('buy','sell'):
            raise PaperError('symbol or side not permitted')
        if type(intent.quantity) is not int or not 0<intent.quantity<=self.controls.max_order_quantity:
            raise PaperError('order size control rejected intent')
        price=money(intent.reference_price)
        if price<=0 or price*intent.quantity>money(self.controls.max_order_notional): raise PaperError('notional control rejected intent')
        if self.risk_check(intent) is not True: raise PaperError('risk rejected intent')
        with self.db:
            number=int(self._get('next_id'))
            client_id=f'qte-{self._get("namespace")}-{number:020d}'
            self.db.execute("UPDATE metadata SET value=? WHERE key='next_id'",(str(number+1),))
            self.db.execute('INSERT INTO orders VALUES(?,NULL,?,?,?,0,?,?)',
                            (client_id,intent.symbol,intent.side,intent.quantity,'prepared',str(price)))
            self._audit('prepared',{'client_id':client_id,**intent.__dict__})
        try:
            ack=self.gateway.submit(client_id,intent)
            if ack.client_id!=client_id: raise PaperError('acknowledgement client ID mismatch')
            with self.db: self._apply_order(ack)
            if ack.filled_quantity: self.ready=False
        except Exception:
            with self.db:
                self.db.execute("UPDATE orders SET status='unknown' WHERE client_id=?",(client_id,))
                self._audit('submission_uncertain',{'client_id':client_id})
            self.disconnected()
            raise PaperError(f'submission uncertain for {client_id}; reconcile before retry') from None
        return client_id

    def acknowledge(self,order: BrokerOrder):
        """Apply asynchronous broker state; partial/terminal fills require reconciliation."""
        try:
            self._connection()
            with self.db: self._apply_order(order)
            if order.filled_quantity: self.ready=False
        except Exception:
            self.disconnected()
            raise

    def cancel(self,client_id):
        self._connection()
        row=self.db.execute('SELECT status FROM orders WHERE client_id=?',(client_id,)).fetchone()
        if row is None: raise PaperError('unknown local order')
        if row['status'] in TERMINAL: return
        with self.db: self._audit('cancel_requested',{'client_id':client_id})
        try:
            ack=self.gateway.cancel(client_id)
            if ack.client_id!=client_id: raise PaperError('cancel acknowledgement ID mismatch')
            with self.db: self._apply_order(ack)
        except Exception:
            self.disconnected()
            raise PaperError('cancellation uncertain; reconcile before trading') from None

    def record_execution(self,report: ExecutionReport) -> bool:
        """Journal an execution fact for the future ledger bridge; no cash mutation."""
        try:
            # Recovery can import missing fills while disconnected. Only the
            # journal lease is required; reconciliation still gates trading.
            self._claim()
            if not report.execution_id or type(report.quantity) is not int or report.quantity<=0 or money(report.price)<=0 or money(report.commission)<0:
                raise PaperError('invalid execution report')
            payload=json.dumps(report.__dict__,sort_keys=True)
            with self.db:
                old=self.db.execute('SELECT payload FROM executions WHERE execution_id=?',(report.execution_id,)).fetchone()
                if old:
                    if old['payload']!=payload: raise PaperError('conflicting duplicate execution')
                    return False
                order=self.db.execute('SELECT quantity FROM orders WHERE client_id=?',(report.client_id,)).fetchone()
                if not order: raise PaperError('execution for unknown order')
                previous=self.db.execute('SELECT payload FROM executions WHERE client_id=?',(report.client_id,)).fetchall()
                if sum(json.loads(r['payload'])['quantity'] for r in previous)+report.quantity>order['quantity']:
                    raise PaperError('execution overfill')
                self.db.execute('INSERT INTO executions VALUES(?,?,?)',(report.execution_id,report.client_id,payload))
                self._audit('execution',report.__dict__)
            # New fills change the ledger: block further trading until ledger reconciliation.
            self.ready=False
            return True
        except Exception:
            self.ready=False
            raise

    def orders(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM orders ORDER BY client_id')]
