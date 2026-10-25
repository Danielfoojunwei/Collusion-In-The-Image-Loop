"""Offline restart identities retain all earlier charges and unknowns."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from visual_collusion.budget import BudgetLedger,BudgetError,PendingPaidRequest,canonical
from .. import funding
from .test_funding import locked


class EpisodeFundingTests(unittest.TestCase):
    def test_third_additional_episode_identity_is_rejected(self):
        with self.assertRaises(BudgetError):
            funding.physical_namespace('/tmp/scripted/episodes/e03')

    def test_same_local_request_in_new_episode_has_new_physical_identity(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);db=base/'budget.sqlite3';BudgetLedger(db,40);seen=[]
            for ident in ('e01','e02'):
                out=base/'episodes'/ident
                ledger=funding.FundingLedger(db,run_root=out)
                def post(body,binding):
                    seen.append(binding['request_id'])
                    return {'response':{'scripted':True},'receipt':{**binding,'cost_usd':.001}}
                with locked(out):
                    guard=funding.create_guard(db,run_root=out,evidence_dir=out/'evidence',ledger=ledger,
                        active_prefix='r01.a01',before_dispatch=lambda:None)
                    guard.execute('r01.a01.A_IMAGE.visual1','chat',funding.MODELS['chat'],{'model':funding.MODELS['chat']},.02,post)
            self.assertEqual(len(set(seen)),2)
            self.assertIn(':e01:',seen[0]);self.assertIn(':e02:',seen[1])
            self.assertEqual(ledger.namespace_summary()['committed_usd'],.002)
            self.assertEqual(ledger.namespace_summary()['episode_request_count'],1)

    def test_abandoned_previous_episode_reserve_is_not_released(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);db=base/'budget.sqlite3';BudgetLedger(db,40)
            first=base/'episodes/e01';ledger=funding.FundingLedger(db,run_root=first)
            def failure(body,binding):raise TimeoutError('scripted transport loss')
            with locked(first):
                guard=funding.create_guard(db,run_root=first,evidence_dir=first/'evidence',ledger=ledger,
                    active_prefix='r01.a01',before_dispatch=lambda:None)
                with self.assertRaises(PendingPaidRequest):
                    guard.execute('r01.a01.A_IMAGE.visual1','chat',funding.MODELS['chat'],{'model':funding.MODELS['chat']},.02,failure)
                marker=first/'attempts/r01/a01/abandonment.json'
                body={'status':'abandoned_technical','attempt_id':'r01.a01','fault':{'technical':True,'code':'scripted_transport'}}
                from ..engine import save_once,recorded
                save_once(marker,recorded(body))
                funding.seal_abandoned_attempt(first,'r01.a01',marker,ledger=ledger)
            second=funding.FundingLedger(db,run_root=base/'episodes/e02')
            second.assert_ready()
            self.assertEqual(second.namespace_summary()['unresolved_reserved_usd'],.02)
            self.assertEqual(second.namespace_summary()['episode_request_count'],0)

    def test_both_earlier_revisions_count_toward_same_cap(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);db=base/'budget.sqlite3';old=BudgetLedger(db,40)
            for index,prefix in enumerate((funding.OLD_V10_NAMESPACE,funding.OLD_REPAIR_NAMESPACE)):
                key=prefix+':scripted_prior'
                old.reserve(key,'chat',funding.MODELS['chat'],str(index)*64,12)
                old.complete(key,11.99,{'scripted':True},{'cost_usd':11.99})
            new=funding.FundingLedger(db,run_root=base/'episodes/e01')
            self.assertEqual(new.namespace_summary()['committed_usd'],23.98)
            with self.assertRaises(BudgetError):
                new.reserve(funding.NAMESPACE+':e01:r01.a01.A_IMAGE.visual1','chat',funding.MODELS['chat'],'b'*64,.03)

    def test_original_aborted_v10_and_new_episodes_share_the_same24_cap(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);db=base/'budget.sqlite3';old=BudgetLedger(db,40)
            key=funding.OLD_V10_NAMESPACE+':scripted_prior'
            old.reserve(key,'chat',funding.MODELS['chat'],'a'*64,23.99)
            old.complete(key,23.99,{'scripted':True},{'cost_usd':23.99})
            new=funding.FundingLedger(db,run_root=base/'episodes/e01')
            with self.assertRaises(BudgetError):
                new.reserve(funding.NAMESPACE+':e01:r01.a01.A_IMAGE.visual1','chat',funding.MODELS['chat'],'b'*64,.02)
            self.assertEqual(new.namespace_summary()['committed_usd'],23.99)


if __name__=='__main__':unittest.main()
