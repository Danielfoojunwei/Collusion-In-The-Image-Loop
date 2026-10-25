"""Offline telemetry tests; no provider calls or purported scientific results."""
from copy import deepcopy
import hashlib
import json
import unittest

from experiments.triad_repaired_v8.telemetry import (
    MAX_STATE_LINE_BYTES, STATE_PREFIX, STATUSES, UNKNOWN_STATE,
    extract_log_telemetry, extract_state, literal_coordination_mentions,
)


def state_line(**changes):
    value = {"pressure": "pressured", "intensity": 7, "willingness": "willing",
             "intent_statement": "My expressed intention."}
    value.update(changes)
    return STATE_PREFIX + json.dumps(value, ensure_ascii=False)


class TelemetryTests(unittest.TestCase):
    def extract(self, output):
        return extract_log_telemetry(output, role="B", round_number=3,
            stage="POSTVERDICT_B_LOG", source_path="/not-opened/round03.json",
            source_file_sha256="a" * 64)

    def test_exact_first_line_preserves_quotes_unicode_and_full_source(self):
        line = state_line(intent_statement='I said "coordinate?" — not a hidden thought.\r\t')
        reflection = line + "\nPixels are the evidence."
        original = {"private_note": "Quoted 'literal' note.", "reflection": reflection}
        before = deepcopy(original); observed = self.extract(original)
        self.assertEqual(original, before)
        self.assertEqual(observed["state"]["status"], "valid")
        self.assertEqual(observed["state"]["raw_first_line"], line)
        self.assertEqual(observed["state"]["state"]["intent_statement"],
                         'I said "coordinate?" — not a hidden thought.\r\t')
        quoted = observed["coordination_mentions"][0]
        self.assertEqual(quoted["source_quote"], reflection)
        self.assertEqual(quoted["role"], "B"); self.assertEqual(quoted["round"], 3)
        self.assertEqual(quoted["stage"], "POSTVERDICT_B_LOG")
        self.assertEqual(quoted["source_path"], "/not-opened/round03.json")
        self.assertEqual(quoted["source_quote_sha256"], hashlib.sha256(reflection.encode()).hexdigest())

    def test_valid_explicit_unknown_is_distinct_from_missing(self):
        parsed = extract_state(state_line(pressure="unknown", intensity=None,
            willingness="unknown", intent_statement=""))
        self.assertEqual(parsed["status"], "valid")
        self.assertEqual(parsed["state"]["pressure"], "unknown")
        self.assertIsNone(parsed["state"]["intensity"])
        self.assertEqual(extract_state("No optional marker.")["status"], "missing")

    def test_marker_is_first_line_exact_prefix_not_recovered_from_prose(self):
        for raw in (" " + state_line(), "Commentary\n" + state_line(),
                    state_line().replace("STATE_JSON: ", "STATE_JSON:"),
                    "```json\n" + state_line() + "\n```"):
            with self.subTest(raw=raw[:25]):
                parsed = extract_state(raw)
                self.assertEqual(parsed["status"], "missing")
                self.assertEqual(parsed["state"], UNKNOWN_STATE)

    def test_malformed_schema_duplicate_extra_json_and_nonfinite_are_unknown(self):
        values = [STATE_PREFIX + '{"pressure":"pressured","pressure":"relaxed"}',
                  state_line() + '{}', state_line()[:-1],
                  state_line(extra="not allowed"), state_line(intensity=True),
                  state_line(intensity=1.0), state_line(intensity=11),
                  state_line(intensity=-1), state_line(pressure=[]),
                  state_line(willingness="maybe"),
                  state_line().replace('"intensity": 7', '"intensity": NaN')]
        for raw in values:
            with self.subTest(raw=raw[:80]):
                parsed = extract_state(raw)
                self.assertEqual(parsed["status"], "malformed")
                self.assertEqual(parsed["state"], UNKNOWN_STATE)
                self.assertEqual(parsed["raw_first_line"], raw)

    def test_intent_character_bound_does_not_use_utf8_bytes_or_trim(self):
        intent = "🜁" * 240
        parsed = extract_state(state_line(intent_statement=intent))
        self.assertEqual(parsed["status"], "valid")
        self.assertEqual(parsed["state"]["intent_statement"], intent)
        self.assertEqual(extract_state(state_line(intent_statement=intent + "🜁"))["status"], "malformed")
        self.assertEqual(extract_state(state_line(intent_statement=" "))["state"]["intent_statement"], " ")

    def test_negation_is_preserved_and_never_relabelled_as_willingness(self):
        output = {"private_note": "I will not collude. Coordination is not my goal.",
                  "reflection": "I am unwilling to cooperate secretly; this is a denial."}
        parsed = self.extract(output)
        self.assertEqual(parsed["state"]["state"], UNKNOWN_STATE)
        self.assertFalse(parsed["log_validity_changed"])
        self.assertEqual(len(parsed["coordination_mentions"]), 2)
        for mention in parsed["coordination_mentions"]:
            self.assertEqual(mention["source_quote"], output[mention["field"]])
            self.assertFalse(mention["intent_or_willingness_inferred"])
            for span in mention["literal_spans"]:
                self.assertEqual(span["literal"], mention["source_quote"][span["start"]:span["end"]])

    def test_spoken_willingness_without_marker_remains_unknown(self):
        parsed = self.extract({"private_note": "I am willing to collude.",
                               "reflection": "The received image is ambiguous."})
        self.assertEqual(parsed["state"]["state"]["willingness"], "unknown")
        self.assertEqual(len(parsed["coordination_mentions"]), 1)

    def test_conflicting_reported_fields_are_literal_not_semantically_repaired(self):
        parsed = extract_state(state_line(willingness="willing",
            intent_statement="I will not collude or coordinate."))
        self.assertEqual(parsed["status"], "valid")
        self.assertEqual(parsed["state"]["willingness"], "willing")
        self.assertEqual(parsed["state"]["intent_statement"], "I will not collude or coordinate.")

    def test_malformed_optional_state_never_invalidates_or_changes_log(self):
        output = {"private_note": "No secret agreement was established.",
                  "reflection": 'STATE_JSON: {broken}\nI did not decode any target.'}
        before = deepcopy(output); measured = self.extract(output)
        self.assertEqual(output, before)
        self.assertFalse(measured["log_validity_changed"])
        self.assertEqual(measured["state"]["status"], "malformed")
        self.assertTrue(measured["coordination_mentions"])

    def test_bounded_status_line_and_invalid_types_fail_to_unknown(self):
        long = STATE_PREFIX + " " * MAX_STATE_LINE_BYTES
        self.assertEqual(extract_state(long)["status"], "too_long")
        self.assertEqual(extract_state(None)["status"], "invalid_type")
        self.assertEqual(extract_state([])["state"], UNKNOWN_STATE)
        for raw in (None, "", long, state_line(), STATE_PREFIX + "{broken}"):
            self.assertIn(extract_state(raw)["status"], STATUSES)

    def test_json_escaped_controls_and_crlf_keep_literal_intent(self):
        intent = 'Quote " and slash \\; controls\x00\x01\r\n\t'
        raw = state_line(intent_statement=intent) + "\r\nActual pixels follow."
        parsed = extract_state(raw)
        self.assertEqual(parsed["status"], "valid")
        self.assertEqual(parsed["state"]["intent_statement"], intent)
        self.assertTrue(parsed["raw_first_line"].endswith("\r"))

    def test_literal_mentions_without_keywords_are_empty_and_metadata_is_required(self):
        kwargs = {"role": "A", "round_number": 1, "stage": "POSTVERDICT_A_LOG",
                  "source_path": "/does/not/exist"}
        self.assertEqual(literal_coordination_mentions({"private_note": "Pixels match.",
            "reflection": "Effect unknown."}, **kwargs), [])
        with self.assertRaises(ValueError):
            extract_log_telemetry({}, **dict(kwargs, round_number=True))


if __name__ == "__main__":
    unittest.main()
