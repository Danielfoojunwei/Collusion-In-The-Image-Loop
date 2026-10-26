"""Offline checkpoint-continuation checks; no network or paid calls."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import continuation as c
from experiments.triad_ascii_records_v10r2.tests.test_engine import finish_round, commit_actor
from research_plans.triad_ascii_records_v10r2.commitment import build_cases


class ContinuationTests(unittest.TestCase):
    def test_original_checkpoint_and_own_memory_are_valid_readonly(self):
        before={str(p):c.file_sha(p) for p in (c.OUT/'checkpoints').glob('*.json')}
        e=c.Engine(c.OUT,json.loads((c.OUT/'config.json').read_text()),json.loads((c.OUT/'plan.json').read_text()))
        self.assertEqual(e.status()['committed_rounds'],4)
        self.assertIsNone(e.status()['active_attempt'])
        for role in 'ABC':
            notes=e._own_logs(5,role)
            self.assertEqual(len(notes),4)
        self.assertEqual(before,{str(p):c.file_sha(p) for p in (c.OUT/'checkpoints').glob('*.json')})

    def test_round_five_restart_preserves_four_rounds_and_finishes_twelve(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run';e=c.Engine(out,{'offline_fixture':True},build_cases())
            for _ in range(4):finish_round(e)
            before={str(p):c.file_sha(p) for p in (out/'checkpoints').glob('*.json')}
            request=e.actor_request();e.begin_actor_request(request)
            e.abandon_current({'technical':True,'code':'SCRIPTED_OUTPUT_EXHAUSTION'})
            reloaded=c.Engine(out,{'offline_fixture':True},build_cases())
            next_request=reloaded.actor_request()
            self.assertEqual((next_request['round'],next_request['attempt']),(5,2))
            self.assertEqual(len(reloaded._own_logs(5,'A')),4)
            self.assertEqual(reloaded._image(4,'C'),e._image(4,'C'))
            for _ in range(8):finish_round(reloaded)
            self.assertEqual(reloaded.status()['committed_rounds'],12)
            self.assertEqual(before,{str(p):c.file_sha(p) for p in (out/'checkpoints').glob('r0[1-4].commit.json')})
            self.assertEqual(sum(reloaded._image(n,r) is not None for n in range(1,13) for r in 'ABC'),36)

    def test_sources_preserve_all_512_frozen_files(self):
        old=json.loads((c.BASELINE/'freeze.json').read_text())['source_files']
        new=c.sources()
        self.assertEqual(len(old),512)
        self.assertTrue(all(new[k]==v for k,v in old.items()))

    def test_backend_keeps_exact_frozen_sampling(self):
        config=json.loads((c.BASELINE/'config.json').read_text())
        obj=object.__new__(c.Backend)
        self.assertTrue(obj.verify_binding(config))
        for field,value in [('max_output_tokens',8192),('all_actor_stages_temperature',2.0),('reasoning_effort','low')]:
            changed=deepcopy(config);changed['sampling'][field]=value
            with self.assertRaises(c.output_room.BackendError):obj.verify_binding(changed)

    def test_new_backend_fault_translates_to_original_typed_fault(self):
        obj=object.__new__(c.Backend)
        with patch.object(c.output_room.Backend,'perform',side_effect=c.output_room.TechnicalFault('timeout','r05.a02.A_IMAGE')):
            with self.assertRaises(c.prior.TechnicalFault) as caught:obj.perform({})
        self.assertEqual(caught.exception.fault,'timeout')

    def test_other_checkpoint_paths_rejected(self):
        with self.assertRaises(c.prior.CoordinatorError):c.Coordinator('/tmp/not-the-selected-checkpoint')

    def test_new_failure_preserves_old_abort_and_blocks_no_extra_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);abort=root/'EPISODE_ABORTED.json';abort.write_text('OLD MARKER')
            obj=object.__new__(c.Coordinator);obj.out=root
            with patch.object(c,'AMENDMENT',root/'continuation'):
                result=obj._abort_episode({'round':5,'stage':'A_IMAGE','request_id':'r05.a02.A_IMAGE'},'SCRIPTED_FAILURE')
                self.assertTrue((root/'continuation/STOPPED.json').exists())
            self.assertEqual(abort.read_text(),'OLD MARKER')
            self.assertFalse(result['automatic_further_paid_retry'])

    def test_cancellation_is_not_converted_to_retry(self):
        obj=object.__new__(c.Coordinator)
        obj.locked=lambda:__import__('contextlib').nullcontext()
        obj.dispatch=lambda command:(_ for _ in ()).throw(c.funding.OperatorStop())
        result=io.StringIO();obj.serve(io.StringIO('{"command":"advance"}\n'),result)
        self.assertEqual(json.loads(result.getvalue())['result']['status'],'stopped')


if __name__=='__main__':unittest.main()
