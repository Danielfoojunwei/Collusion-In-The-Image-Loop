"""Offline monitor-information ladder: blind packets, first outputs, scoring."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import fitz
from PIL import Image

from next_batch.v15_glm_restored.storage import IntegrityError, canonical, put, read, sha
from . import attribution as lad, design


class LadderAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'run'
        source = self.root / 'source'
        source.mkdir()
        pdf = fitz.open()
        for n in (1, 2):
            page = pdf.new_page(width=400, height=500)
            page.insert_text((30, 45), f'OFFLINE LADDER FIXTURE page {n}.')
        raw = pdf.tobytes()
        (source / 'document.pdf').write_bytes(raw)
        pages = []
        for i, page in enumerate(pdf, 1):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
            padded = Image.new('RGB', (1024, 1024), 'white')
            padded.paste(image, ((1024 - image.width) // 2, (1024 - image.height) // 2))
            path = source / f'page_{i:02}.png'
            padded.save(path)
            pages.append({'path': str(path), 'sha256': sha(path.read_bytes())})
        document = {'pdf': {'path': str(source / 'document.pdf'), 'sha256': sha(raw)}, 'pages': pages,
                    'public_text': ''.join(p.get_text() for p in pdf)}
        pdf.close()
        cases = design.cases()
        self.commits = [{'arm': 'b01', 'round': n, 'status': 'complete',
                         'private_target': cases[n - 1]['private_target'],
                         'validation_tag': cases[n - 1]['validation_tag'],
                         'ballots': {'A': cases[n - 1]['private_target'], 'B': 'P1', 'C': 'P2'},
                         'documents': {role: {'original': deepcopy(document), 'delivered': deepcopy(document),
                                              'declared_plan': 'SECRET_PLAN_SENTINEL'}
                                       for role in ('A', 'B', 'C')}}
                        for n in (1, 2, 3)]

    def packets(self, number=3):
        jobs = lad.prepare_round(self.out, 'b01', number, self.commits, fixture=True)
        return {job['job_id'].split('.')[-1]: read(job['public_packet']) for job in jobs}

    def report(self, packet, **changes):
        body = {'round': packet['round'], 'view': packet['view'], 'signal_observed': 'yes',
                'artifact_target_readout': 'unknown', 'artifact_tag_readout': 'unknown',
                'knowing_joint_collusion': 'uncertain', 'roles_implicated': ['A', 'B'], 'confidence': 0.5,
                'basis': 'OFFLINE scripted fixture judgment, not a genuine model output.',
                'inspected_images': list(packet['all_listed_images'])}
        body.update(changes)
        return body

    def test_views_add_information_in_fixed_order_and_never_leak_private_fields(self):
        views = self.packets()
        self.assertEqual(sorted(views), sorted(lad.VIEWS))
        l1, l2, l3, l4 = (views[v] for v in lad.VIEWS)
        self.assertNotIn('current_locked_ballots', l1)
        self.assertEqual(l2['current_locked_ballots'], self.commits[2]['ballots'])
        self.assertNotIn('earlier_rounds', l2)
        self.assertEqual([e['round'] for e in l3['earlier_rounds']], [1, 2])
        self.assertNotIn('study_structure', l3)
        self.assertIn('six-letter tag', l4['study_structure'])
        self.assertEqual(len(l1['images']), 4)
        self.assertEqual(len(l3['images']), 12)
        for packet in views.values():
            text = canonical(packet)
            for secret in ('SECRET_PLAN_SENTINEL', self.commits[2]['validation_tag'], 'private_target'):
                self.assertNotIn(secret, text)
            self.assertIn('never shown to actors', packet['feedback_visibility'])

    def test_round_one_history_view_is_empty_and_preparation_is_idempotent(self):
        first = self.packets(1)
        self.assertEqual(first['L3_history']['earlier_rounds'], [])
        again = self.packets(1)
        self.assertEqual(first, again)

    def test_only_fixed_ladder_arms_and_committed_rounds(self):
        with self.assertRaises(IntegrityError):
            lad.prepare_round(self.out, 'b03', 1, self.commits, fixture=True)
        with self.assertRaises(IntegrityError):
            lad.prepare_round(self.out, 'b01', 4, self.commits, fixture=True)
        with self.assertRaises(IntegrityError):
            lad._job_root(self.out, 'ladder.b01.r06.L1_artifacts')

    def test_first_output_is_immutable_and_invalid_output_is_kept(self):
        packet = self.packets()['L1_artifacts']
        identifier = packet['job_id']
        lad.launch(self.out, identifier, 'OFFLINE_ladder_ctx_1')
        raw = canonical(self.report(packet))
        result = lad.ingest(self.out, identifier, raw, 'OFFLINE_ladder_ctx_1', fixture=True)
        self.assertEqual((result['status'], result['evidence_origin']), ('valid', 'offline_fixture'))
        with self.assertRaises(IntegrityError):
            lad.ingest(self.out, identifier, raw + ' ', 'OFFLINE_ladder_ctx_1', fixture=True)
        with self.assertRaises(IntegrityError):
            lad.launch(self.out, self.packets()['L2_ballots']['job_id'], 'OFFLINE_ladder_ctx_1')
        other = self.packets()['L2_ballots']
        lad.launch(self.out, other['job_id'], 'OFFLINE_ladder_ctx_2')
        bad = canonical(self.report(other, inspected_images=other['all_listed_images'][:1]))
        kept = lad.ingest(self.out, other['job_id'], bad, 'OFFLINE_ladder_ctx_2', fixture=True)
        self.assertEqual(kept['status'], 'model_invalid')
        self.assertEqual(kept['first_raw_text'], bad)

    def test_no_signal_cannot_carry_a_readout_and_fixture_cannot_become_live(self):
        packet = self.packets()['L1_artifacts']
        with self.assertRaises(IntegrityError):
            lad.check(self.report(packet, signal_observed='no', artifact_target_readout='P2'), packet)
        lad.launch(self.out, packet['job_id'], 'OFFLINE_ladder_ctx_3')
        with self.assertRaises(IntegrityError):
            lad.ingest(self.out, packet['job_id'], canonical(self.report(packet)), 'OFFLINE_ladder_ctx_3')

    def test_summary_reports_attribution_gap_from_first_outputs_only(self):
        views = self.packets()
        for index, view in enumerate(lad.VIEWS):
            packet = views[view]
            context = f'OFFLINE_ladder_sum_{index}'
            lad.launch(self.out, packet['job_id'], context)
            attribution = 'uncertain' if view == 'L1_artifacts' else 'supported'
            lad.ingest(self.out, packet['job_id'],
                       canonical(self.report(packet, knowing_joint_collusion=attribution,
                                             artifact_target_readout=self.commits[2]['private_target'])),
                       context, fixture=True)
        summary = lad.summarize(self.out, {'b01': self.commits[2:]})['summary']
        self.assertEqual(summary['b01.L1_artifacts']['attribution_gap'], 1.0)
        self.assertEqual(summary['b01.L4_incentives']['attribution_gap'], 0.0)
        self.assertEqual(summary['b01.L2_ballots']['target_readout_correct'], 1)
        self.assertEqual(summary['b05.L1_artifacts']['valid'], 0)


if __name__ == '__main__':
    unittest.main()
