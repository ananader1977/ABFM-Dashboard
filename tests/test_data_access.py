from pathlib import Path
import tempfile
import unittest
import pandas as pd
from data_access import load_table, load_rows, work_order_columns
from analytics import work_order_metrics


class ProjectedReads(unittest.TestCase):
    def test_task_filter_and_projection_preserve_metrics(self):
        frame = pd.DataFrame({
            "Task Type": ["BREAKDOWN", "PPM", " breakdown ", "SCHEDULEDTASK"],
            "Status": ["COMPLETE", "CANCELLED", "ACTIVE", "ASSIGNED"],
            "Task Due Date": pd.to_datetime(["2020-01-01"] * 4),
            "Long Description": ["large private description" * 500] * 4,
            "Task Id": ["1", "2", "3", "4"],
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.parquet"
            frame.to_parquet(path, index=False)
            columns = work_order_columns(frame.columns, "Overview")
            loaded = load_table(path, "BREAKDOWN", columns)
            self.assertEqual(len(loaded), 2)
            self.assertNotIn("Long Description", loaded)
            result = work_order_metrics(loaded)
            self.assertEqual(result["Completed"], 1)
            self.assertEqual(result["Open"], 1)
            self.assertEqual(result["Overdue open"], 1)
            # An explicitly selected filter field must remain available.
            columns = work_order_columns(frame.columns, "Overview", ["Task Id"])
            self.assertIn("Task Id", columns)
            detailed = load_rows(path, "PPM", list(frame.columns), [0])
            self.assertEqual(len(detailed), 1)
            self.assertIn("Long Description", detailed)
            self.assertEqual(work_order_metrics(detailed)["Cancelled"], 1)
