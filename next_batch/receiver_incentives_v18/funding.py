"""Carry-forward financing; atomic parallel reservations, no reset of liabilities."""
import hashlib
import json
from pathlib import Path
import re
import time

from next_batch.oversight_v17.finance import Ledger, FundingHold, canonical, seed_from_reconciliation, units


class StudyLedger(Ledger):
    def claim_technical_restart(self,incident):
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS technical_restarts (incident TEXT PRIMARY KEY, created REAL NOT NULL)')
            if db.execute("SELECT 1 FROM calls WHERE status NOT IN ('completed','pending')").fetchone():
                return False
            if db.execute('SELECT COUNT(*) FROM technical_restarts').fetchone()[0]>=2:return False
            db.execute('INSERT INTO technical_restarts VALUES (?,?)',(incident,time.time()))
        return True

    def reserve(self,request_id,kind,wire,amount):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',request_id) or kind not in {'research_development','actor','image','monitor'}:
            raise FundingHold('Invalid study paid identity/role')
        reserve=units(amount)
        if not reserve or not self.path.exists():raise FundingHold('Funded study ledger required')
        digest=hashlib.sha256(wire).hexdigest()
        with self.transaction() as db:
            settings={r['name']:json.loads(r['value']) for r in db.execute('SELECT * FROM settings')}
            if db.execute('SELECT 1 FROM calls WHERE request_id=?',(request_id,)).fetchone():
                raise FundingHold('A paid identity can never be reposted')
            if db.execute("SELECT 1 FROM calls WHERE status NOT IN ('completed','pending')").fetchone():
                raise FundingHold('Ambiguous/overrun/route-held charge blocks dispatch')
            committed=db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM calls').fetchone()[0]
            seed=settings['seed']
            if any(total>cap for total,cap in [
                (committed+reserve,settings['development_cap_units']),
                (seed['aggregate_units']+committed+reserve,settings['aggregate_cap_units']),
                (seed['program_units']+committed+reserve,settings['program_cap_units']),
                (committed+reserve,seed['period_remaining_units'])]):
                raise FundingHold('Study or inherited cumulative cap exceeded')
            db.execute('INSERT INTO calls VALUES (?,?,?,?,NULL,?,?,NULL)',
                (request_id,digest,kind,reserve,'pending',time.time()))
        return digest


def bridge(private_root,old_private,account,allocation='8.00'):
    """Open a distinct study ledger only after carrying every old liability."""
    root=Path(private_root);old=Path(old_private)
    ledger=StudyLedger(root/'budget.sqlite3')
    if ledger.path.exists():return ledger
    original=json.loads((old/'legacy_budget_reconciliation.json').read_text())
    parent=Ledger(old/'v17_development.sqlite3').summary()
    if any(r['status']!='completed' for r in parent['calls']):
        raise FundingHold('Unsettled parent-study liabilities require reconciliation')
    added=sum(r['actual'] for r in parent['calls'])
    combined={**original,'rows':original['rows']+len(parent['calls']),
        'aggregate_committed_usd':(units(original['aggregate_committed_usd'])+added)/1e9,
        'program_committed_usd':(units(original['program_committed_usd'])+added)/1e9,
        'settled_usd':(units(original['settled_usd'])+added)/1e9}
    seed=seed_from_reconciliation(combined,account)
    seed['aggregate_units']=max(seed['aggregate_units'],parent['seed']['aggregate_units']+added)
    seed['program_units']=max(seed['program_units'],parent['seed']['program_units']+added)
    seed['parent_account_usage_units']=units(account['usage'])
    proof={'funds_added_usd':0,'allocation_usd':float(allocation),'seed':seed,
        'parent_calls':parent['calls'],'legacy_summary_sha256':hashlib.sha256((old/'legacy_budget_reconciliation.json').read_bytes()).hexdigest(),
        'parent_ledger_sha256':hashlib.sha256((old/'v17_development.sqlite3').read_bytes()).hexdigest(),
        'meaning':'All historical costs, uncertainty and unmatched account usage carried forward. Old ledgers unchanged.'}
    root.mkdir(parents=True,exist_ok=True)
    (root/'funding_bridge.json').write_text(canonical(proof)+'\n')
    ledger.create(seed,allocation)
    return ledger
