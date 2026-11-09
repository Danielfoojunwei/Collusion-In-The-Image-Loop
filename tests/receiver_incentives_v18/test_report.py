from pathlib import Path
import tempfile
import unittest

from next_batch.receiver_incentives_v18.report import public_records,choice_counts,charts,build
from next_batch.receiver_incentives_v18.statistics import analyze


def fixture():
    return [dict(pair_id=f'pair{pair}',arm=arm,cohort_id=f'g{pair}_{arm}',round=n,
        case_id=f's{n:02}',target='P2',client_best='P1',B_choice='P1',C_choice='P1',
        B_result={'valid':True,'fields':{'interpretation':'PRIVATE DO NOT EXPORT','memory_note':'SECRET'}},
        C_result={'valid':True},B_page_refs=[{'example':'not read by whitelist'}],
        raw_reasoning='PRIVATE REASONING',audited=False)
        for pair in range(1,11) for arm in (0,4000) for n in range(1,4)]


class ReportTests(unittest.TestCase):
    def test_public_export_preserves_all_planned_rows_and_excludes_private_text(self):
        rows=fixture();rows[0].update(B_choice=None,technical_missing=True)
        public=public_records(rows)
        self.assertEqual(len(public),60)
        self.assertIsNone(public[0]['B_choice'])
        self.assertTrue(public[0]['technical_missing'])
        self.assertNotIn('PRIVATE',str(public));self.assertNotIn('SECRET',str(public))
        self.assertEqual(public[0]['B_delivered_page_count'],1)

    def test_missing_refusal_and_target_have_distinct_counts(self):
        rows=fixture();rows[0]['B_choice']=None;rows[1]['B_choice']='refuse';rows[2]['B_choice']='P2'
        counts=choice_counts(rows)
        self.assertEqual(counts['B_0']['planned'],30)
        self.assertEqual(counts['B_0']['missing'],1)
        self.assertEqual(counts['B_0']['refuse'],1)
        self.assertEqual(counts['B_0']['sponsor_preference'],1)
        self.assertEqual(counts['B_0']['client_correct'],27)

    def test_grouped_charts_render_from_synthetic_data_only(self):
        rows=fixture();analysis=analyze(rows,expected_pairs=10)
        with tempfile.TemporaryDirectory() as folder:
            charts(analysis,choice_counts(rows),folder)
            for name in ('paired_adoption','paired_contrasts','individual_choices','design_flow'):
                self.assertGreater((Path(folder)/(name+'.png')).stat().st_size,1000)
                self.assertGreater((Path(folder)/(name+'.pdf')).stat().st_size,1000)

    def test_collection_guard_stops_before_reading_live_records(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'freeze.json').write_text(json.dumps({'assignment':[{'cohort_id':'not_complete'}]}))
            with self.assertRaisesRegex(RuntimeError,'completion markers'):
                build(root,root/'out')
            self.assertFalse((root/'out').exists())


if __name__=='__main__':unittest.main()
