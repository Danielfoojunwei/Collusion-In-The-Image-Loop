"""First reviewer answers are observations even when malformed or refused."""
import unittest
from scripts.service_repaired_review import observed_value

class FirstFinalTests(unittest.TestCase):
    def test_actual_json_is_preserved(self):
        raw = '{"decision":"P1","other":[1,true,null]}'
        value, status = observed_value(raw)
        self.assertEqual(status,"json")
        self.assertEqual(value,{"decision":"P1","other":[1,True,None]})

    def test_invalid_first_final_is_not_replaced_or_repaired(self):
        for raw in ["I cannot access the image.", "", '{"verdict":"accept","verdict":"reject"}', '{"x":NaN}', '{"x":"\\ud800"}']:
            with self.subTest(raw=raw):
                value,status=observed_value(raw)
                self.assertEqual(value,raw)
                self.assertEqual(status,"invalid_json_preserved_as_text")

if __name__ == "__main__":
    unittest.main()
