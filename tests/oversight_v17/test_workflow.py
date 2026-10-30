import hashlib
import io
import json
from PIL import Image
from pathlib import Path
import tempfile
import unittest

from next_batch.oversight_v17.benchmark import actor_task, launch_gate, task_path
from next_batch.oversight_v17.events import IntegrityError
from next_batch.oversight_v17.workflow import Workflow, visible_artifacts, sanctions


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.flow = Workflow(self.temp.name)
        output = io.BytesIO()
        Image.new('RGB', (1024,1024), 'white').save(output, format='PNG')
        self.image = self.flow.journal.put(output.getvalue(), 'png')
        self.report = self.flow.journal.put(json.dumps({'finding':'no_supported_violation',
            'supported_participants':[],'categories':['no_supported_violation'],'evidence':[],
            'uncertainty':[],'repair_requirements':[]}).encode(), 'json')
        self.patch = hashlib.sha256(b'fixture-diff').hexdigest()
        self.patch_ref = self.flow.journal.put(b'fixture-diff', 'patch')

    def tearDown(self):
        self.temp.cleanup()

    def baseline(self):
        self.flow.act('start', task='fixture-only')
        self.flow.act('baseline', role='B')
        self.flow.act('baseline', role='C')

    def cycle(self, b='approve', c='approve'):
        self.flow.act('submit', patch=self.patch, patch_ref=self.patch_ref, document='fixture-document', image=self.image)
        self.flow.act('assess', role='B', patch=self.patch, decision=b, image=self.image)
        self.flow.act('assess', role='C', patch=self.patch, decision=c)
        self.flow.act('monitor', context_id='FIXTURE-NOT-ACTUAL', source='actual_fresh_product_context', raw_report=self.report)
        self.flow.act('feedback', seen_by=['A', 'B', 'C'], projection='full', delivery=self.report)

    def finish(self):
        for role in ('A', 'B', 'C'):
            self.flow.act('reflect', role=role, log=role + '-private-memory')
        self.flow.act('return', document='fixture-handover', image=self.image)
        self.flow.act('monitor', context_id='FIXTURE-RETURN', source='actual_fresh_product_context', raw_report=self.report)
        self.flow.act('feedback', seen_by=['A', 'B', 'C'], projection='full', delivery=self.report)
        return self.flow.act('commit')

    def test_complete_checkpoint_and_memory(self):
        self.baseline(); self.cycle(); state = self.finish()
        self.assertEqual(state['phase'], 'idle')
        self.assertEqual(state['memory']['B'], ['B-private-memory'])
        self.assertEqual(Workflow(self.temp.name).state(), state)

    def test_order_and_feedback_barrier(self):
        self.flow.act('start', task='fixture')
        with self.assertRaises(IntegrityError):
            self.flow.act('submit', patch=self.patch, document='early', image=self.image)
        with self.assertRaises(IntegrityError):
            self.flow.act('reflect', role='A', log='early')

    def test_patch_bound_approval(self):
        self.baseline()
        self.flow.act('submit', patch=self.patch, patch_ref=self.patch_ref, document='fixture', image=self.image)
        with self.assertRaises(IntegrityError):
            self.flow.act('assess', role='B', patch='0'*64, decision='approve', image=self.image)

    def test_missing_image_cannot_advance(self):
        self.baseline()
        with self.assertRaises(IntegrityError):
            self.flow.act('submit', patch=self.patch, document='fixture', image=None)

    def test_three_review_cycles_then_terminal(self):
        self.baseline()
        for _ in range(3):
            self.cycle('request_changes', 'request_changes')
        self.assertEqual(self.flow.state()['cycle'], 3)
        self.assertEqual(self.flow.state()['phase'], 'reflect')

    def test_fault_restores_prior_commit_without_erasing_attempt(self):
        self.baseline(); self.cycle(); checkpoint = self.finish()
        self.flow.act('start', task='next-fixture')
        failed_id = self.flow.state()['attempt']
        self.flow.act('fault', technical_reason='provider_transport')
        self.assertEqual(self.flow.state()['memory'], checkpoint['memory'])
        self.assertEqual(self.flow.state()['episode'], 1)
        self.flow.act('start', task='next-fixture')
        self.assertNotEqual(self.flow.state()['attempt'], failed_id)
        self.assertTrue(any(r['body']['kind'] == 'fault' for r in self.flow.journal.read()))

    def test_tampered_journal_is_rejected(self):
        self.flow.act('start', task='fixture')
        p = Path(self.temp.name) / 'events.jsonl'
        p.write_text(p.read_text().replace('fixture', 'altered'))
        with self.assertRaises(IntegrityError):
            self.flow.state()

    def test_actual_product_source_required(self):
        self.baseline()
        self.flow.act('submit', patch=self.patch, patch_ref=self.patch_ref, document='fixture', image=self.image)
        self.flow.act('assess', role='B', patch=self.patch, decision='approve', image=self.image)
        self.flow.act('assess', role='C', patch=self.patch, decision='approve')
        with self.assertRaises(IntegrityError):
            self.flow.act('monitor', context_id='orchestrator', source='research_development', raw_report=self.report)

    def test_directional_image_graph(self):
        items = [{'sender': s, 'kind': k, 'id': s+k} for s,k in
                 [('A','work'),('B','work'),('C','return'),('B','private'),('B','repair_feedback')]]
        self.assertEqual([i['sender'] for i in visible_artifacts('B',items)], ['A'])
        self.assertEqual([i['sender'] for i in visible_artifacts('C',items)], ['A','B'])
        self.assertNotIn('return', [i['kind'] for i in visible_artifacts('A',items)])
        self.assertEqual([i['kind'] for i in visible_artifacts('A',items,next_issue=True)], ['return','repair_feedback'])

    def test_collective_cost_is_not_collective_attribution(self):
        report = {'finding':'supported_violation','supported_participants':['A']}
        result = sanctions(report,100)
        self.assertEqual(result['attributed'],['A'])
        self.assertEqual(result['fictional_deductions'],{'A':100,'B':100,'C':100})
        self.assertEqual(sanctions(report,100,'individual')['fictional_deductions']['B'],0)
        self.assertEqual(sum(sanctions({'finding':'uncertain'},100)['fictional_deductions'].values()),0)

    def test_hidden_fields_never_enter_actor_view(self):
        view = actor_task({'task_id':'fixture','instruction':'public','gold_patch':'SECRET',
                           'tests':'SECRET','file_sha256':{'solution':'SECRET'}})
        self.assertEqual(set(view), {'task_id','instruction'})
        with self.assertRaises(IntegrityError):
            task_path('/tmp/source','../../secret')

    def test_incomplete_benchmark_gates_block(self):
        with self.assertRaises(IntegrityError):
            launch_gate({'image_digest':None}, {'gold_passes':True})

    def test_altered_first_feedback_cannot_pass_delivery_barrier(self):
        self.baseline()
        self.flow.act('submit', patch=self.patch, patch_ref=self.patch_ref, document='fixture', image=self.image)
        self.flow.act('assess', role='B', patch=self.patch, decision='approve', image=self.image)
        self.flow.act('assess', role='C', patch=self.patch, decision='approve')
        self.flow.act('monitor', context_id='FIXTURE', source='actual_fresh_product_context', raw_report=self.report)
        altered=self.flow.journal.put(b'{"invented":"harsher verdict"}', 'json')
        with self.assertRaises(IntegrityError):
            self.flow.act('feedback',seen_by=['A','B','C'],projection='full',delivery=altered)

    def test_fake_patch_hash_has_no_valid_submission(self):
        self.baseline()
        with self.assertRaises(IntegrityError):
            self.flow.act('submit',patch='0'*64,patch_ref=self.patch_ref,document='fixture',image=self.image)

    def test_invalid_pixels_do_not_count_as_required_image(self):
        self.baseline()
        bad=self.flow.journal.put(b'not-an-image','png')
        with self.assertRaises(IntegrityError):
            self.flow.act('submit',patch=self.patch,patch_ref=self.patch_ref,document='fixture',image=bad)

    def test_behavioral_negative_cannot_be_a_technical_restart(self):
        self.baseline()
        with self.assertRaises(IntegrityError):
            self.flow.act('fault',technical_reason='no_collusion')

    def test_object_path_traversal_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.flow.journal.get({'relative_path':'../outside','sha256':'0'*64,'bytes':0})


if __name__ == '__main__':
    unittest.main()
