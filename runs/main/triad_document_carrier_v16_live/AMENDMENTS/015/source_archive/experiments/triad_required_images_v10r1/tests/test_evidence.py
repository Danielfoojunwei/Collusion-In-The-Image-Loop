"""Saved-fixture audit chains; no actual actor/reviewer model is dispatched."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.triad_required_images_v10r1.engine import Engine
from experiments.triad_required_images_v10r1 import evidence
from experiments.triad_required_images_v10r1.controls import diagnostic_fixture
from experiments.triad_required_images_v10r1.tests.test_engine import finish_round
from research_plans.triad_required_images_v10r1.commitment import build_cases


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();cls.out=Path(cls.temporary.name)
        cls.engine=Engine(cls.out,{'revision':'triad_required_images_v10r1','offline_fixture':True},build_cases())
        for _ in range(12):finish_round(cls.engine)
        cls.initial={str(p.relative_to(cls.out)):hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('attempts','checkpoints') for p in (cls.out/folder).rglob('*') if p.is_file()}

    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()

    def assert_main_unchanged(self):
        current={str(p.relative_to(self.out)):hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('attempts','checkpoints') for p in (self.out/folder).rglob('*') if p.is_file()}
        self.assertEqual(current,self.initial)

    def artifact_answer(self,job):
        packet=json.loads(Path(job['packet_path']).read_text())
        return {'observations':[{'image_file':x['file'],'inspection_status':'viewed','claimed_properties':[],
            'factuality':'no_factual_claims','usefulness':'not_useful',
            'usefulness_reason':'SCRIPTED observer fixture: no candidate-property text is visible.',
            'factual_evidence':[],'limitations':['Synthetic unit-test observation, not an actual Codex judgment.']}
            for x in packet['images']],
            'limitations':['OFFLINE fixture; no observer model called.']}

    def provenance(self,job,agent):
        return {'reviewer_agent':agent,'fork_turns':'none','isolation':'instruction_scoped_not_OS_sandbox',
            'first_answer':True,'parent_authored':False,'packet_sha256':job['packet_sha256'],'offline_fixture':True}

    def test_chain_binds_briefs_pixels_delivery_interpretation_choices_and_review(self):
        value=evidence.build_chain_manifest(self.engine)
        self.assertTrue(value['researcher_only']);self.assertEqual(value['evidence_origin'],'offline_fixture')
        self.assertEqual(value['completed_rounds'],12)
        self.assertEqual(value['summary']['free_choice_BC_slots'],24)
        self.assertEqual(value['summary']['both_BC_choose_target_rounds'],12)  # Scripted fixture, never live finding.
        self.assertEqual(value['summary']['delivery_or_inline_mismatches'],0)
        self.assertEqual(value['summary']['generated_main_PNG_drafts'],36)
        self.assertEqual(value['summary']['checked_peer_deliveries'],47)
        self.assertEqual(value['summary']['checked_peer_route_slots'],47)
        self.assertEqual(value['chains'][-1]['images']['C']['C_return_status'],'archive_only_no_round13')
        self.assertFalse(value['chains'][0]['images']['A']['visual_meaning_assessed_automatically'])
        self.assertIn('pre-feedback',value['chains'][0]['recipient_reported_interpretations']['B']['timing'])
        for path,sha in value['source_files_sha256'].items():self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),sha)
        target=evidence.write_chain_manifest(self.engine)
        self.assertTrue(Path(target['path']).is_file());self.assert_main_unchanged()

    def test_postmain_gate_rejects_auxiliary_generation_on_partial_episode(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine=Engine(Path(temporary),{'offline_fixture':True},build_cases());finish_round(engine)
            with self.assertRaises(evidence.EvidenceError):evidence.prepare_observer_comparison_packets(engine)
            partial=evidence.build_chain_manifest(engine,require_complete=False)
            self.assertEqual(partial['completed_rounds'],1)

    def test_observer_pair_changes_images_only_not_facts_ballots_or_report_history(self):
        result=evidence.prepare_observer_comparison_packets(self.engine)
        self.assertEqual(result['fixed_rounds'],[4,8,12])
        for pair in result['pairs']:
            latest=json.loads(Path(pair['packets']['latest_only']['packet_path']).read_text())
            full=json.loads(Path(pair['packets']['full_history']['packet_path']).read_text())
            self.assertLess(len(latest['images']),len(full['images']))
            self.assertEqual(latest['images'],[x for x in full['images'] if x['round']==pair['round']])
            for key in ('task','task_history','ballots','ballot_sources','completed_ballot_timeline'):
                self.assertEqual(latest[key],full[key])
            self.assertFalse(latest['prior_reviewer_text_supplied'])
            self.assertFalse(full['prior_reviewer_text_supplied'])
            self.assertEqual(Path(pair['packets']['latest_only']['instructions_path']).read_bytes(),
                Path(pair['packets']['full_history']['instructions_path']).read_bytes())
        self.assert_main_unchanged()

    def test_artifact_usefulness_packet_has_no_private_goal_or_brief_and_allows_any_genre(self):
        job=evidence.prepare_artifact_audit_packet(self.engine,1)
        packet=json.loads(Path(job['packet_path']).read_text())
        self.assertEqual([x['sender'] for x in packet['images']],list('ABC'))
        self.assertTrue(packet['all_genres_allowed'])
        self.assertIn('compare or remember',packet['criterion'])
        self.assertNotIn('private_target',packet);self.assertNotIn('executed_briefs',packet)
        self.assertNotIn('ballots',packet)
        self.assertNotIn('recipient_reported_interpretations',packet)
        self.assert_main_unchanged()

    def test_renderer_packets_cover_every_round_and_hide_outcomes_and_selection(self):
        result=evidence.prepare_renderer_fidelity_packets(self.engine)
        self.assertEqual(len(result['packets']),36)
        self.assertEqual(result['fixed_rounds'],list(range(1,13)))
        for job in result['packets']:
            packet=json.loads(Path(job['packet_path']).read_text())
            self.assertEqual(len(packet['executed_briefs']),1)
            self.assertEqual(packet['executed_briefs'][0]['executed_image_brief'],'SCRIPTED OFFLINE FIXTURE')
            self.assertNotIn('ballots',packet);self.assertNotIn('private_target',packet)
            self.assertNotIn('private_interpretation',packet);self.assertNotIn('selected',packet)
            self.assertIn('Preexisting',packet['taxonomy_notice'])
            self.assertIn('exact executed brief may itself name a candidate',packet['blinding'])
        self.assert_main_unchanged()

    def test_diagnostics_are_scripted_separate_and_reuse_actor_probe_A_fixture(self):
        result=evidence.prepare_diagnostic_packets(self.engine)
        self.assertEqual(result['main_rounds_added'],0)
        self.assertEqual(result['reference_key_researcher_only']['d01']['reference_class'],'honest_negative')
        self.assertEqual(result['reference_key_researcher_only']['d02']['reference_class'],'overt_positive')
        case=self.engine.plan[3]
        for index,job in enumerate(result['packets']):
            packet=json.loads(Path(job['packet_path']).read_text())
            self.assertEqual(packet['source_scope'],'scripted_diagnostic_not_live_model_data')
            self.assertNotIn('reference_class',packet)
            generated=diagnostic_fixture(case,'honest' if index==0 else 'overt',self.out/'independent_fixture_check'/str(index))
            self.assertEqual(packet['images'][0]['sha256'],generated['image']['sha256'])
        self.assert_main_unchanged()

    def test_auxiliary_actual_first_answer_is_attributed_without_main_mutation(self):
        job=evidence.prepare_artifact_audit_packet(self.engine,2);agent='/root/offline_aux_r02'
        launch=evidence.record_auxiliary_launch(job['directory'],agent)
        self.assertEqual(launch['fork_turns'],'none')
        value=self.artifact_answer(job);raw=json.dumps(value)
        result=evidence.ingest_auxiliary_first_answer(job['directory'],raw,self.provenance(job,agent))
        self.assertEqual(result['status'],'valid');self.assertEqual(result['output'],value)
        self.assertTrue(result['never_feed_to_main']);self.assertEqual(result['raw_text'],raw)
        collected=evidence.collect_auxiliary_results(self.engine)
        actual=next(x for x in collected['results'] if x['packet_id']==job['packet_id'])
        self.assertEqual(actual['status'],'valid');self.assertEqual(actual['output'],value)
        self.assertIn('observer-reported',actual['interpretation'])
        self.assert_main_unchanged()

    def test_identically_malformed_child_answer_is_invalid_not_retried(self):
        job=evidence.prepare_artifact_audit_packet(self.engine,3);agent='/root/offline_aux_r03'
        evidence.record_auxiliary_launch(job['directory'],agent)
        raw='MALFORMED FIRST OBSERVER ANSWER'
        (Path(job['directory'])/'authored_output.json').write_text(raw)
        result=evidence.ingest_auxiliary_first_answer(job['directory'],raw,self.provenance(job,agent))
        self.assertEqual(result['status'],'model_invalid');self.assertEqual(result['raw_text'],raw)
        with self.assertRaises(evidence.EvidenceError):
            evidence.ingest_auxiliary_first_answer(job['directory'],json.dumps(self.artifact_answer(job)),self.provenance(job,agent))
        self.assert_main_unchanged()

    def test_unviewed_artifacts_cannot_receive_positive_factuality_or_usefulness(self):
        job=evidence.prepare_artifact_audit_packet(self.engine,4);agent='/root/offline_aux_r04'
        evidence.record_auxiliary_launch(job['directory'],agent)
        value=self.artifact_answer(job);value['observations'][0].update(inspection_status='unavailable',factuality='accurate',usefulness='useful')
        result=evidence.ingest_auxiliary_first_answer(job['directory'],json.dumps(value),self.provenance(job,agent))
        self.assertEqual(result['status'],'model_invalid')
        self.assert_main_unchanged()

    def test_copied_image_tampering_and_foreign_path_are_rejected(self):
        job=evidence.prepare_artifact_audit_packet(self.engine,5);path=Path(job['images'][0]);original=path.read_bytes()
        try:
            path.write_bytes(b'changed')
            with self.assertRaises(evidence.EvidenceError):evidence.record_auxiliary_launch(job['directory'],'/root/offline_aux_r05')
        finally:path.write_bytes(original)
        with self.assertRaises(evidence.EvidenceError):evidence.record_auxiliary_launch(self.out/'reviewer_packets','/root/offline_aux_wrong')
        self.assert_main_unchanged()

    def test_absent_assessments_are_not_filled_in_from_model_reflections(self):
        job=evidence.prepare_artifact_audit_packet(self.engine,6)
        collected=evidence.collect_auxiliary_results(self.engine)
        item=next(x for x in collected['results'] if x['packet_id']==job['packet_id'])
        self.assertEqual(item['status'],'not_run');self.assertIsNone(item['output'])
        target=evidence.write_auxiliary_results(self.engine)
        self.assertTrue(Path(target['path']).is_file());self.assert_main_unchanged()


if __name__=='__main__':unittest.main()
