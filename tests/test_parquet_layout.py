import json
from pathlib import Path
import tempfile
import unittest
import pyarrow as pa
import pyarrow.parquet as pq
from parquet_layout import prepare_snapshot, ROW_GROUP_SIZE


class SnapshotLayout(unittest.TestCase):
    def test_repacking_is_lossless_and_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / 'snapshot-test'
            snapshot.mkdir()
            table = pa.table({'id': list(range(5001)), 'description': ['long description ' * 50, None, ''] * 1667})
            path = snapshot / 'table.parquet'
            pq.write_table(table, path)
            (root / 'current.txt').write_text(snapshot.name)
            (snapshot / 'manifest.json').write_text(json.dumps({'tables': {'test': {'file': path.name}}}))
            self.assertEqual(prepare_snapshot(root), 1)
            self.assertTrue(table.equals(pq.read_table(path)))
            metadata = pq.read_metadata(path)
            self.assertTrue(all(metadata.row_group(i).num_rows <= ROW_GROUP_SIZE for i in range(metadata.num_row_groups)))
            self.assertEqual(prepare_snapshot(root), 0)
