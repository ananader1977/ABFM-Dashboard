"""Exercise the form submission and reruns, rather than injecting a session."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest
from security import connect, password_hash


class LoginFlow(unittest.TestCase):
    def test_operations_stays_signed_in_across_view_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot-test"
            snapshot.mkdir()
            frame = pd.DataFrame({"Task Type": ["BREAKDOWN", "BREAKDOWN"], "Status": ["COMPLETE", "ACTIVE"], "Category": ["General", "Urgent"]})
            frame.to_parquet(snapshot / "assets.parquet", index=False)
            manifest = {"imported_at": "2026-10-31T00:00:00Z", "pages": [{"name": "Reactive", "visuals": []}], "tables": {"Reactive": {"file": "assets.parquet", "rows": 2, "columns": list(frame.columns)}}}
            (snapshot / "manifest.json").write_text(json.dumps(manifest))
            (root / "current.txt").write_text(snapshot.name)
            db = connect(root)
            db.execute("INSERT INTO users (username,password,role) VALUES (?,?,?)", ("operations", password_hash("regression-test-password"), "operations"))
            db.commit()
            db.close()
            with patch.dict(os.environ, {"ABFM_DATA_DIR": directory}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=60).run()
                app.text_input[0].set_value("operations")
                app.text_input[1].set_value("regression-test-password")
                app.button[0].click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["identity"]["username"], "operations")
                self.assertNotIn("Administration", app.sidebar.radio[0].options)
                self.assertFalse(any(b.label == "Sign in" for b in app.button))
                self.assertEqual(app.sidebar.radio[0].value,'Executive Summary')
                next(b for b in app.button if b.key=='queue_Reactive').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.sidebar.radio[0].value,'Reactive')
                self.assertEqual(next(r for r in app.radio if r.label=='View').value,'Records')
                self.assertEqual(next(m for m in app.metric if m.label=='Total work orders').value,'1')
                next(b for b in app.button if b.key=='clear_executive_selection').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(next(m for m in app.metric if m.label=='Total work orders').value,'2')
                for view in ["Records", "Source charts", "Overview"]:
                    next(r for r in app.radio if r.label == "View").set_value(view).run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.session_state["identity"]["username"], "operations")
                    self.assertFalse(any(b.label == "Sign in" for b in app.button))
                next(b for b in app.button if b.label == "Sign out").click().run()
                self.assertTrue(any(b.label == "Sign in" for b in app.button))
