from contextlib import contextmanager
import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from visual_collusion.budget import BudgetLedger, BudgetError, PendingPaidRequest, canonical
from experiments.triad_emergent_v9.engine import save_once
from ..funding import FundingLedger,create_guard,seal_abandoned_attempt,sha,NAMESPACE,MODELS,valid_attempt_id


@contextmanager
def locked(out):
    out.mkdir(parents=True,exist_ok=True)
    with (out/'producer.lock').open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)


class FundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.out=root/'run';self.path=root/'budget.sqlite3'
        BudgetLedger(self.path,40)
        self.ledger=FundingLedger(self.path,run_root=self.out)
        self.calls=[]

    def guard(self,attempt='r01.a01'):
        return create_guard(self.path,run_root=self.out,evidence_dir=self.out/'evidence',
            active_prefix=attempt,before_dispatch=lambda:None,ledger=self.ledger)

    def post(self,body,binding):
        self.calls.append(binding)
        return {'response':{'observed':True},'receipt':{**binding,'cost_usd':.001}}

    def test_one_post_cache_and_exact_intent_protection(self):
        with locked(self.out):
            guard=self.guard();payload={'model':MODELS['chat'],'input':'fixture'}
            first=guard.execute('r01.a01.A.image','chat',MODELS['chat'],payload,.01,self.post)
            cached=guard.execute('r01.a01.A.image','chat',MODELS['chat'],payload,.01,self.post)
            self.assertFalse(first['cached']);self.assertTrue(cached['cached']);self.assertEqual(len(self.calls),1)
            with self.assertRaises(BudgetError):
                guard.execute('r01.a01.A.image','chat',MODELS['chat'],dict(payload,input='changed'),.01,self.post)

    def test_original_aggregate_and_namespace_caps_are_both_enforced(self):
        for ident,amount in [('other:x',1),(NAMESPACE+':x',25)]:
            with self.assertRaises(BudgetError):
                self.ledger.reserve(ident,'chat',MODELS['chat'],'a'*64,amount)
        old=BudgetLedger(self.path,40)
        old.reserve('old_fixture:x','chat',MODELS['chat'],'b'*64,39.5)
        old.complete('old_fixture:x',39.5,{'observed':True},{'cost_usd':39.5})
        with self.assertRaises(BudgetError):
            self.ledger.reserve(NAMESPACE+':new','chat',MODELS['chat'],'c'*64,1)

    def test_no_post_without_producer_lock_or_current_attempt(self):
        guard=self.guard();payload={'model':MODELS['chat']}
        with self.assertRaises(BudgetError):guard.execute('r01.a01.A.image','chat',MODELS['chat'],payload,.01,self.post)
        with locked(self.out):
            with self.assertRaises(BudgetError):guard.execute('r01.a02.A.image','chat',MODELS['chat'],payload,.01,self.post)
        self.assertEqual(self.calls,[])

    def test_ambiguous_charge_blocks_until_exact_technical_abandonment(self):
        def fail(body,binding):raise TimeoutError('fixture transport loss')
        with locked(self.out):
            guard=self.guard();payload={'model':MODELS['chat']}
            with self.assertRaises(PendingPaidRequest):
                guard.execute('r01.a01.A.image','chat',MODELS['chat'],payload,.02,fail)
            with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()
            marker=self.out/'attempts/r01/a01/abandonment.json'
            data={'status':'abandoned_technical','attempt_id':'r01.a01','fault':{'technical':True,'code':'api_transport_failure'}}
            save_once(marker,{**data,'record_hash':sha(canonical(data).encode())})
            sealed=seal_abandoned_attempt(self.out,'r01.a01',marker,ledger=self.ledger)
            self.assertEqual(sealed['sealed_unknown_rows'],1)
            self.ledger.assert_ready()
            self.assertEqual(self.ledger.namespace_summary()['unresolved_reserved_usd'],.02)
            with self.assertRaises(PendingPaidRequest):
                guard.execute('r01.a01.A.image','chat',MODELS['chat'],payload,.02,self.post)
            self.guard('r01.a02').execute('r01.a02.A.image','chat',MODELS['chat'],payload,.02,self.post)
            self.assertEqual(len(self.calls),1)

    def test_unknown_reserve_without_real_callback_proof_cannot_be_ignored(self):
        with locked(self.out):
            self.ledger.reserve(NAMESPACE+':r01.a01.fake','chat',MODELS['chat'],'d'*64,.02)
            self.ledger.uncertain(NAMESPACE+':r01.a01.fake')
            marker=self.out/'attempts/r01/a01/abandonment.json'
            data={'status':'abandoned_technical','attempt_id':'r01.a01','fault':{'technical':True,'code':'api_transport_failure'}}
            save_once(marker,{**data,'record_hash':sha(canonical(data).encode())})
            with self.assertRaises((OSError,BudgetError)):
                seal_abandoned_attempt(self.out,'r01.a01',marker,ledger=self.ledger)
            with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()

    def test_reservation_to_callback_crash_gap_has_durable_owner_and_keeps_reserve(self):
        original=self.ledger.reserve
        def broken(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('fixture storage fault after reserve, before callback')
        with locked(self.out):
            with patch.object(self.ledger,'reserve',side_effect=broken):
                with self.assertRaises(OSError):
                    self.guard().execute('r01.a01.A.image','chat',MODELS['chat'],{'model':MODELS['chat']},.03,self.post)
            self.assertEqual(self.calls,[])
            marker=self.out/'attempts/r01/a01/abandonment.json'
            data={'status':'abandoned_technical','attempt_id':'r01.a01','fault':{'technical':True,'code':'local_storage_failure'}}
            save_once(marker,{**data,'record_hash':sha(canonical(data).encode())})
            seal_abandoned_attempt(self.out,'r01.a01',marker,ledger=self.ledger)
            self.ledger.assert_ready()
            self.assertEqual(self.ledger.namespace_summary()['unresolved_reserved_usd'],.03)

    def test_controls_are_bounded_and_unknown_control_costs_stay_reserved(self):
        for name in ('ctrl.r04.original.a01','ctrl.r08.renderer.a03','ctrl.r04.overt.a01','r12.a03'):
            self.assertTrue(valid_attempt_id(name))
        for name in ('r13.a01','r00.a01','ctrl.r06.original.a01','ctrl.r08.overt.a01','ctrl.r04.honest.a04','ctrl.r04.extra.a01'):
            self.assertFalse(valid_attempt_id(name))
            with self.assertRaises(BudgetError):self.guard(name)
        prefix='ctrl.r04.original.a01'
        def fail(body,binding):raise TimeoutError('scripted control transport failure')
        with locked(self.out):
            with self.assertRaises(PendingPaidRequest):
                self.guard(prefix).execute(prefix+'.A_IMAGE.visual1','chat',MODELS['chat'],{'model':MODELS['chat']},.02,fail)
            marker=self.out/'controls/branches/r04/original/attempts/a01/abandonment.json'
            data={'status':'abandoned_technical','attempt_id':prefix,'round':4,'attempt':1,'family':'original','fault':{'technical':True,'code':'api_transport_failure'}}
            save_once(marker,{**data,'record_hash':sha(canonical(data).encode())})
            seal_abandoned_attempt(self.out,prefix,marker,ledger=self.ledger)
            self.ledger.assert_ready()
            self.assertEqual(self.ledger.namespace_summary()['unresolved_reserved_usd'],.02)


if __name__=='__main__':unittest.main()
