"""Atomic reservations including conservative historical and account liabilities."""
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


class FundingHold(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def units(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise FundingHold('Invalid nonnegative amount')
    return int((amount * 10**9).to_integral_value(rounding=ROUND_CEILING))


def seed_from_reconciliation(legacy, key):
    """Charge unmatched account usage conservatively to the restricted program."""
    needed = ['aggregate_committed_usd', 'program_committed_usd', 'uncertain_reserve_usd', 'settled_usd']
    if not all(k in legacy for k in needed) or key.get('usage') is None or key.get('limit_remaining') is None:
        raise FundingHold('Historical reconciliation and real current account metadata required')
    usage = units(key['usage'])
    unmatched = max(0, usage - units(legacy['settled_usd']))
    return {
        'aggregate_units': max(units(legacy['aggregate_committed_usd']), usage + units(legacy['uncertain_reserve_usd'])),
        'program_units': units(legacy['program_committed_usd']) + unmatched,
        'period_remaining_units': units(key['limit_remaining']),
        'unmatched_usage_units_charged_to_program': unmatched,
        'historical_rows': legacy['rows'],
        'key_limit_reset': key.get('limit_reset'),
        'meaning': 'Weekly allowance never resets cumulative experiment liabilities; old databases untouched.',
    }


class Ledger:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, seed, development_cap):
        if self.path.exists():
            raise FundingHold('Ledger already exists; never reinitialize funding')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for key in ['aggregate_units', 'program_units', 'period_remaining_units', 'historical_rows']:
            if key not in seed or not isinstance(seed[key], int) or seed[key] < 0:
                raise FundingHold('Valid immutable baseline required')
        cap = units(development_cap)
        if cap <= 0:
            raise FundingHold('Positive explicit development allocation required')
        with self.transaction() as db:
            db.execute('CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE calls (request_id TEXT PRIMARY KEY, wire_hash TEXT NOT NULL, kind TEXT NOT NULL, reserved INTEGER NOT NULL, actual INTEGER, status TEXT NOT NULL, created REAL NOT NULL, finished REAL)')
            for key, value in {'seed': seed, 'development_cap_units': cap, 'aggregate_cap_units': units(40), 'program_cap_units': units(24)}.items():
                db.execute('INSERT INTO settings VALUES (?,?)', (key, canonical(value)))

    def reserve(self, request_id, kind, wire, amount):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', request_id):
            raise FundingHold('Invalid paid identity')
        if kind not in {'research_development', 'actor', 'image', 'decoder', 'monitor'}:
            raise FundingHold('Unknown funded role')
        reserve = units(amount)
        if not reserve:
            raise FundingHold('No zero-reservation paid calls')
        if not self.path.exists():
            raise FundingHold('Funded ledger missing')
        digest = hashlib.sha256(wire).hexdigest()
        with self.transaction() as db:
            settings = {r['name']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
            if db.execute('SELECT 1 FROM calls WHERE request_id=?', (request_id,)).fetchone():
                raise FundingHold('Paid identity already used; POST replay forbidden')
            if db.execute("SELECT 1 FROM calls WHERE status!='completed'").fetchone():
                raise FundingHold('Outstanding or ambiguous new paid request blocks dispatch')
            committed = db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM calls').fetchone()[0]
            seed = settings['seed']
            checks = [(committed + reserve, settings['development_cap_units']),
                      (seed['aggregate_units'] + committed + reserve, settings['aggregate_cap_units']),
                      (seed['program_units'] + committed + reserve, settings['program_cap_units']),
                      (committed + reserve, seed['period_remaining_units'])]
            if any(total > cap for total, cap in checks):
                raise FundingHold('Development/aggregate/program/current-period cap exceeded')
            db.execute('INSERT INTO calls VALUES (?,?,?,?,NULL,?,?,NULL)', (request_id, digest, kind, reserve, 'pending', time.time()))
        return digest

    def complete(self, request_id, actual):
        amount = units(actual)
        with self.transaction() as db:
            row = db.execute('SELECT * FROM calls WHERE request_id=?', (request_id,)).fetchone()
            if row is None or row['status'] != 'pending':
                raise FundingHold('Settlement is not a first pending result')
            if amount > row['reserved']:
                db.execute("UPDATE calls SET actual=?,status='overrun',finished=? WHERE request_id=?", (amount, time.time(), request_id))
            else:
                db.execute("UPDATE calls SET actual=?,status='completed',finished=? WHERE request_id=?", (amount, time.time(), request_id))
        if amount > row['reserved']:
            raise FundingHold('Actual cost exceeds reservation; all future paid dispatch held')

    def uncertain(self, request_id):
        with self.transaction() as db:
            db.execute("UPDATE calls SET status='uncertain',finished=? WHERE request_id=? AND status='pending'", (time.time(), request_id))

    def hold_settled(self, request_id):
        with self.transaction() as db:
            db.execute("UPDATE calls SET status='route_hold' WHERE request_id=? AND status='completed'", (request_id,))

    def summary(self):
        if not self.path.exists():
            raise FundingHold('Ledger missing')
        with self.transaction() as db:
            settings = {r['name']: json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
            rows = [dict(r) for r in db.execute('SELECT request_id,kind,reserved,actual,status FROM calls ORDER BY created')]
        return {'seed': settings['seed'], 'calls': rows,
                'new_committed_usd': sum(r['actual'] if r['actual'] is not None else r['reserved'] for r in rows) / 1e9,
                'development_cap_usd': settings['development_cap_units'] / 1e9}
