"""Offline technical format amendment checks; never call a provider."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from scripts import triad_max_format_v1 as amendment
from scripts import triad_response_format_v4 as v4
from experiments.triad_max_pressure_v3.contracts import IMAGE_REVIEW_KEYS
from experiments.triad_max_pressure_v3.tests import test_backend as backend_fixture


def review(alias=False):
    result = {key: 'Exact literal private assessment: ' + key for key in IMAGE_REVIEW_KEYS}
    if alias:
        result['generated_role_assessment'] = result.pop('generator_role_assessment')
    return result


def value(mode='memory_only', alias=False):
    result = {'private_note': 'Exact original own note.', 'image_review': review(alias)}
    return result if mode == 'memory_only' else {'action': 'deliver', 'arguments': result}


def row(content, mode='memory_only', finish='stop', refusal=None, provider='OpenInference'):
    stage = 'POSTVERDICT_B_LOG.step1' if mode == 'memory_only' else 'POSTVERDICT_C_IMAGE_LOG.step4'
    identifier = amendment.NAMESPACE + ':fixture.r01.' + stage
    usage = {'cost': 0.0001, 'is_byok': False}
    response = {'id': 'fixture-response', 'model': v4.MODEL, 'provider': provider,
                'usage': usage, 'choices': [{'finish_reason': finish, 'message': {
                    'content': content if isinstance(content, str) else json.dumps(content), 'refusal': refusal}}]}
    receipt = {'request_id': identifier, 'request_hash': 'fixture-request-hash',
               'requested_model': v4.MODEL, 'kind': 'chat', 'returned_model': v4.MODEL,
               'provider': provider, 'response_id': 'fixture-response', 'usage': usage, 'cost_usd': 0.0001}
    raw_response, raw_receipt = json.dumps(response), json.dumps(receipt)
    return {'request_id': identifier, 'request_hash': 'fixture-request-hash', 'status': 'completed',
            'kind': 'chat', 'model': v4.MODEL, 'actual_units': 100000,
            'response_json': raw_response, 'receipt_json': raw_receipt,
            'result_hash': hashlib.sha256((raw_response + '\n' + raw_receipt).encode()).hexdigest()}


class PoststageRecoveryTests(unittest.TestCase):
    def test_exact_private_key_alias_preserves_all_values(self):
        original = value(alias=True)
        record = amendment.recover_completed_poststage(row(original), 'memory_only')
        expected = value()
        self.assertEqual(record['context_value'], expected)
        self.assertEqual(record['parsed_original_value'], original)
        self.assertTrue(record['alias_changes'][0]['value_bytes_preserved_by_alias'])
        self.assertFalse(record['decision_inferred_or_corrected'])
        self.assertFalse(record['new_POST_or_repair_query'])

    def test_c_terminal_complete_json_after_brace_free_prose(self):
        original = value('image_memory')
        text = 'Rendering notes: concrete output is ready.\n' + json.dumps(original)
        record = amendment.recover_completed_poststage(row(text, 'image_memory'), 'image_memory')
        self.assertEqual(record['context_value'], original)
        self.assertEqual(record['original_text'], text)
        self.assertEqual(record['prefix_diagnostic']['exact_prefix'], 'Rendering notes: concrete output is ready.\n')
        self.assertIn('brace_free_prose_prefix_before_terminal_image_memory_delivery', record['syntax_flags'])
        self.assertIs(v4.parse_response_object, amendment._ORIGINAL_PARSE)

    def test_memory_only_terminal_object_after_brace_free_prose(self):
        original = value(alias=True)
        record = amendment.recover_completed_poststage(row('Private summary.\n' + json.dumps(original)), 'memory_only')
        self.assertEqual(record['context_value'], value())
        self.assertIn('brace_free_prose_prefix_before_terminal_memory_only_object', record['syntax_flags'])
        with self.assertRaises(amendment.FormatRepairError):
            amendment.recover_completed_poststage(row('Summary.\n' + json.dumps(original) + ' trailing'), 'memory_only')

    def test_alias_and_existing_marked_private_excerpts_retain_full_text(self):
        original = value(alias=True)
        original['private_note'] = 'é' * 1400
        original['image_review']['generated_role_assessment'] = 'é' * 400
        record = amendment.recover_completed_poststage(row(original), 'memory_only')
        current = record['context_value']
        self.assertLessEqual(len(current['private_note'].encode()), 2048)
        self.assertLessEqual(len(current['image_review']['generator_role_assessment'].encode()), 500)
        self.assertIn('Technical excerpt', current['private_note'])
        self.assertIn('Technical excerpt', current['image_review']['generator_role_assessment'])
        full = [item['full_original_text'] for item in record['private_text_normalizations']]
        self.assertIn(original['private_note'], full)
        self.assertIn(original['image_review']['generated_role_assessment'], full)
        self.assertEqual(record['parsed_original_value'], original)

    def test_ambiguous_or_extra_alias_keys_rejected(self):
        for mutate in ('both', 'extra', 'missing'):
            original = value(alias=True)
            if mutate == 'both': original['image_review']['generator_role_assessment'] = 'Conflicting second field.'
            if mutate == 'extra': original['image_review']['other'] = 'Extra.'
            if mutate == 'missing': del original['image_review']['observed_rendering']
            with self.subTest(mutate=mutate), self.assertRaises(amendment.FormatRepairError):
                amendment.recover_completed_poststage(row(original), 'memory_only')

    def test_a_malformed_trailing_note_is_never_guessed(self):
        text = json.dumps({'image_review': review(alias=True)}) + ', "private_note":"unescaped "quotes"}'
        with self.assertRaises(amendment.FormatRepairError):
            amendment.recover_completed_poststage(row(text), 'memory_only')
        with self.assertRaises(amendment.FormatRepairError):
            amendment.recover_completed_poststage(row({'image_review': review(alias=True)}), 'memory_only')

    def test_c3_extra_fields_and_missing_brace_are_not_recovered(self):
        extra = value('image_memory')
        extra['arguments']['image_review']['concrete_rendering'] = 'Not an allowed alias.'
        del extra['arguments']['image_review']['observed_rendering']
        for text in (json.dumps(extra), json.dumps(value('image_memory'))[:-1],
                     'Prose. ' + json.dumps(extra), 'Prose. ' + json.dumps(value('image_memory'))[:-1]):
            with self.subTest(text=text), self.assertRaises(amendment.FormatRepairError):
                amendment.recover_completed_poststage(row(text, 'image_memory'), 'image_memory')

    def test_preface_braces_extra_objects_or_trailing_text_rejected(self):
        literal = json.dumps(value('image_memory'))
        for text in ('Candidates {P1,P4}. ' + literal, 'Bad } preface. ' + literal,
                     'Prose. ' + literal + ' {}', 'Prose. ' + literal + ' trailing',
                     '{} Prose. ' + literal, 'Prose. ' + literal + '}'):
            with self.subTest(text=text), self.assertRaises(amendment.FormatRepairError):
                amendment.recover_completed_poststage(row(text, 'image_memory'), 'image_memory')

    def test_duplicate_and_nonfinite_json_rejected(self):
        literal = json.dumps(value('image_memory'))
        duplicate = literal.replace('"action": "deliver"', '"action":"deliver","action":"deliver"')
        nonfinite = literal.replace('"private_note": "Exact original own note."', '"private_note":NaN')
        for text in (duplicate, 'Prose. ' + duplicate, nonfinite, 'Prose. ' + nonfinite):
            with self.subTest(text=text), self.assertRaises(amendment.FormatRepairError):
                amendment.recover_completed_poststage(row(text, 'image_memory'), 'image_memory')

    def test_refusal_nonstop_and_receipt_tampering_not_recovered(self):
        for raw in (row(value(alias=True), finish='length'), row(value(alias=True), refusal='Refused')):
            with self.assertRaises(amendment.FormatRepairError):
                amendment.recover_completed_poststage(raw, 'memory_only')
        raw = row(value(alias=True));raw['actual_units'] = 1
        with self.assertRaises(amendment.FormatEvidenceError):
            amendment.recover_completed_poststage(raw, 'memory_only')
        raw = row(value(alias=True));raw['response_json'] += ' '
        with self.assertRaises(amendment.FormatEvidenceError):
            amendment.recover_completed_poststage(raw, 'memory_only')
        self.assertIs(v4.parse_response_object, amendment._ORIGINAL_PARSE)

    def test_modes_and_request_identity_are_restricted(self):
        for mode in ('vote_only', 'image_vote'):
            with self.assertRaises(amendment.FormatRepairError):
                amendment.recover_completed_poststage(row(value()), mode)
        raw = row(value());raw['request_id'] = amendment.NAMESPACE + ':fixture.r01.C_VOTE.step1'
        with self.assertRaises(amendment.FormatEvidenceError):
            amendment.recover_completed_poststage(raw, 'memory_only')

    def test_actual_r1_readonly_records_when_available(self):
        ledger = Path(__file__).resolve().parents[1] / 'runs/main/budget.sqlite3'
        if not ledger.exists(): self.skipTest('Actual ledger unavailable; fixtures above remain authoritative tests')
        rows = {}
        for role, mode, step in [('A','memory_only',1),('B','memory_only',1),('C','image_memory',4)]:
            stage = 'POSTVERDICT_' + role + '_LOG' if role != 'C' else 'POSTVERDICT_C_IMAGE_LOG'
            try: rows[role] = v4.completed_row(ledger, amendment.NAMESPACE + f':case_05_max.r01.{stage}.step{step}')
            except amendment.FormatEvidenceError: self.skipTest('Actual round1 records unavailable')
        with self.assertRaises(amendment.FormatRepairError):
            amendment.recover_completed_poststage(rows['A'], 'memory_only')
        b = amendment.recover_completed_poststage(rows['B'], 'memory_only')
        c = amendment.recover_completed_poststage(rows['C'], 'image_memory')
        self.assertEqual(b['parsed_original_value']['image_review']['generated_role_assessment'],
                         b['context_value']['image_review']['generator_role_assessment'])
        self.assertEqual(c['context_value']['action'], 'deliver')
        self.assertFalse(c['private_note_inferred_or_completed'])
        self.assertEqual(c['prefix_diagnostic']['prefix_utf8_bytes'], 2846)


class FutureBackendTests(unittest.TestCase):
    def setUp(self):
        self.fixture = backend_fixture.BackendTests();self.fixture.setUp()
    def tearDown(self):
        self.fixture.tearDown()
    def backend(self, actions):
        base = self.fixture.backend(actions)
        return amendment.FormatV1Backend(self.fixture.config, self.fixture.root / 'amended', self.fixture.ledger.path,
            guard=self.fixture.guard, api_key='FIXTURE_ONLY_NOT_A_CREDENTIAL', transport=self.fixture.transport)

    def test_future_B_alias_same_paid_response_no_extra_call_and_cached(self):
        backend = self.backend([value(alias=True)])
        request = self.fixture.request('memory_only', 'B');request['request_id']='fixture.r02.POSTVERDICT_B_LOG'
        observed = backend.run_actor(request)
        self.assertEqual(observed['status'], 'valid')
        self.assertEqual(observed['output']['image_review'], review())
        self.assertEqual(len(self.fixture.transport.posts_exact), 1)
        self.assertEqual(backend.run_actor(request), observed)
        self.assertEqual(len(self.fixture.transport.posts_exact), 1)
        self.assertEqual(len(list((backend.evidence_dir/'format_v1/normalizations').glob('*.json'))),1)

    def test_future_C_terminal_delivery_uses_exact_latest_generated_pixels(self):
        literal = value('image_memory')
        backend = self.backend([{'action':'generate_image','prompt':'Fixture next-image prompt','reference':'none'},
                                'Output inspection summary.\n' + json.dumps(literal)])
        request=self.fixture.request('image_memory','C');request['request_id']='fixture.r02.POSTVERDICT_C_IMAGE_LOG'
        observed=backend.run_actor(request)
        self.assertEqual(observed['status'],'valid')
        self.assertEqual(Path(observed['output']['image']['path']).read_bytes(),self.fixture.transport.image_bytes)
        self.assertEqual(len(self.fixture.transport.posts_exact),3)
        self.assertEqual(backend.run_actor(request),observed)
        self.assertEqual(len(self.fixture.transport.posts_exact),3)


if __name__ == '__main__':
    unittest.main()
