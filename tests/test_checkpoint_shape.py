"""Checkpoint corruption tests. Offline; controlled worker plus real process control."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from anomalynerd import bounded
from anomalynerd.model import Axis, TidyTable, Flag
from dataclasses import asdict

class CheckpointShapeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.table = TidyTable('sample', {'method': Axis('method', 'unordered_cat')}, 'method', 'accuracy', False,
                               [{'method': 'A', 'value': 1.}, {'method': 'B', 'value': 2.}])
        self.worker = patch.object(bounded, '_run_bounded_one', return_value={'status': 'completed', 'flags': [], 'notes': []})
        self.mock = self.worker.start()
        self.addCleanup(self.worker.stop)
        bounded.run_bounded(self.table, seconds=2, checkpoint_dir=self.root)
        self.path = sorted(self.root.glob('*.json'))[0]
        self.original = json.loads(self.path.read_text())
        self.mock.reset_mock()

    def corrupt(self, value):
        self.path.write_text(json.dumps(value))
        flags, runs = bounded.run_bounded(self.table, seconds=2, checkpoint_dir=self.root)
        self.assertEqual([], flags)
        self.assertEqual(len(bounded.DETECTORS), len(runs))
        self.assertEqual(1, self.mock.call_count, 'only damaged detector should rerun')
        self.assertEqual('completed', json.loads(self.path.read_text())['status'])

    def test_valid_cache_reused(self):
        bounded.run_bounded(self.table, seconds=2, checkpoint_dir=self.root)
        self.mock.assert_not_called()
    def test_null(self): self.corrupt(None)
    def test_list(self): self.corrupt([])
    def test_number(self): self.corrupt(42)
    def test_string(self): self.corrupt('completed')
    def test_flags_missing(self):
        v=copy.deepcopy(self.original); del v['flags']; self.corrupt(v)
    def test_flags_null(self):
        v=copy.deepcopy(self.original); v['flags']=None; self.corrupt(v)
    def test_flags_dict(self):
        v=copy.deepcopy(self.original); v['flags']={}; self.corrupt(v)
    def test_flag_missing_fields(self):
        v=copy.deepcopy(self.original); v['flags']=[{'type':'trend'}]; self.corrupt(v)
    def test_flag_not_dict(self):
        v=copy.deepcopy(self.original); v['flags']=[False]; self.corrupt(v)
    def test_notes_not_list(self):
        v=copy.deepcopy(self.original); v['notes']='broken'; self.corrupt(v)
    def test_notes_nonstring(self):
        v=copy.deepcopy(self.original); v['notes']=[123]; self.corrupt(v)
    def test_wrong_key(self):
        v=copy.deepcopy(self.original); v['key']='old'; self.corrupt(v)
    def test_wrong_detector(self):
        v=copy.deepcopy(self.original); v['detector']='old'; self.corrupt(v)
    def test_failed_not_reused(self):
        v=copy.deepcopy(self.original); v['status']='failed'; self.corrupt(v)
    def test_timeout_not_reused(self):
        v=copy.deepcopy(self.original); v['status']='timed_out'; self.corrupt(v)
    def test_valid_flag_preserved(self):
        flag = Flag('point_outlier', 'sample', 'method', {}, {}, 8., 1.)
        v=copy.deepcopy(self.original); v['flags']=[asdict(flag)]
        self.path.write_text(json.dumps(v))
        flags, runs=bounded.run_bounded(self.table, seconds=2, checkpoint_dir=self.root)
        self.assertEqual(1, len(flags)); self.assertEqual('HIGH', flags[0].priority)
        self.mock.assert_not_called()
    def test_invalid_flag_fields(self):
        valid = asdict(Flag('point_outlier', 'sample', 'method', {}, {}, 8., 1.))
        for field, value in [('type', None), ('table', 4), ('axis', []), ('coords', []),
                             ('detail', 'bad'), ('strength', '8'), ('strength', True),
                             ('strength', float('nan')), ('support', None),
                             ('priority', None), ('suggestion', 2)]:
            with self.subTest(field=field, value=value):
                v=copy.deepcopy(self.original); raw=copy.deepcopy(valid); raw[field]=value
                v['flags']=[raw]; self.mock.reset_mock(); self.corrupt(v)
    def test_settings_invalidate(self):
        bounded.run_bounded(self.table, seconds=1, checkpoint_dir=self.root)
        self.assertEqual(len(bounded.DETECTORS), self.mock.call_count)
    def test_table_invalidate(self):
        self.table.rows[0]['value']=3.
        bounded.run_bounded(self.table, seconds=2, checkpoint_dir=self.root)
        self.assertEqual(len(bounded.DETECTORS), self.mock.call_count)

class RealCheckpointIntegration(unittest.TestCase):
    def test_real_worker_corrupt_cache_recovers_same_flags(self):
        table = TidyTable('known_duplicate',
            {'p1': Axis('p1', 'numeric'), 'method': Axis('method', 'unordered_cat')},
            'method', 'RMSE', True,
            [{'p1': p, 'method': m, 'value': v} for p, m, v in
             [(0.1, 'X', 12.34), (0.2, 'X', 379.18), (0.3, 'X', 379.18),
              (0.1, 'Z', 5.), (0.2, 'Z', 5.), (0.3, 'Z', 5.)]])
        with tempfile.TemporaryDirectory() as root:
            first, runs = bounded.run_bounded(table, seconds=4, checkpoint_dir=root)
            self.assertTrue(all(r['status'] == 'completed' for r in runs))
            self.assertTrue(first, 'real detectors must produce nonempty findings')
            self.assertIn('duplicate', {f.type for f in first})
            self.assertIn('flatness', {f.type for f in first})
            files = sorted(Path(root).glob('*.json'))
            self.assertEqual(8, len(files))
            preserved = {p.name: p.read_bytes() for p in files[1:]}
            files[0].write_text('null')
            resumed, again = bounded.run_bounded(table, seconds=4, checkpoint_dir=root)
            self.assertTrue(all(r['status'] == 'completed' for r in again))
            self.assertEqual([f.to_dict() for f in first], [f.to_dict() for f in resumed])
            self.assertEqual(preserved, {p.name: p.read_bytes() for p in files[1:]})
            cached, third = bounded.run_bounded(table, seconds=4, checkpoint_dir=root)
            self.assertEqual(again, third)
            self.assertEqual([f.to_dict() for f in resumed], [f.to_dict() for f in cached])

if __name__=='__main__': unittest.main()
