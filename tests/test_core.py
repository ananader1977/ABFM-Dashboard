import tempfile
import time
import unittest
import pandas as pd
from analytics import task_frame, work_order_metrics, summarize, source_filters
from security import authenticate, connect, current_user, password_hash, save_user


class BusinessRules(unittest.TestCase):
    def test_types_and_cancelled_separation(self):
        frame = pd.DataFrame({"Task Type": ["BREAKDOWN"] * 5 + ["PPM"], "Status": ["COMPLETE", "HISTORY", "CANCELLED", "ASSIGNED", "ACTIVE", "COMPLETE"], "Task Due Date": ["2020-01-01"] * 6})
        selected = task_frame(frame, "Reactive")
        metrics = work_order_metrics(selected)
        self.assertEqual(metrics["Total work orders"], 5)
        self.assertEqual(metrics["Completed"], 2)
        self.assertEqual(metrics["Cancelled"], 1)
        self.assertEqual(metrics["Open"], 2)
        self.assertEqual(metrics["Completion % (excl. cancelled)"], 50)
        self.assertEqual(metrics["Overdue open"], 2)
        self.assertEqual(len(task_frame(frame, "PPM")), 1)
        self.assertEqual(len(task_frame(frame, "Corrective")), 0)

    def test_unknown_and_no_due_dates(self):
        frame = pd.DataFrame({"Status": ["OTHER", "ACTIVE", "CANCELLED"], "Task Due Date": [None] * 3})
        metrics = work_order_metrics(frame)
        self.assertEqual(metrics["Other status"], 1)
        self.assertEqual(metrics["Open without due date"], 1)
        self.assertEqual(metrics["Overdue open"], 0)

    def test_empty_denominator(self):
        metrics = work_order_metrics(pd.DataFrame({"Status": ["CANCELLED"]}))
        self.assertIsNone(metrics["Completion % (excl. cancelled)"])

    def test_quotation_award_statuses(self):
        frame = pd.DataFrame({"Status": ["Approved Not Started", "Work In Progress", "Completed Not Invoice", "Invoiced", "Under Review"], "Total Value": [10, 20, 30, 40, 500]})
        result, _ = summarize(frame, "Quotation", "Quotations")
        self.assertEqual(result["Awarded quotations"], 4)
        self.assertEqual(result["Awarded value (AED)"], 100)

    def test_saved_categorical_filters(self):
        condition = {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "r"}}, "Property": "Status"}}], "Values": [[{"Literal": {"Value": "'ACTIVE'"}}]]}}
        container = {"filterConfig": {"filters": [{"filter": {"From": [{"Name": "r", "Entity": "Reactive"}], "Where": [{"Condition": condition}]}}]}}
        frame = pd.DataFrame({"Status": ["ACTIVE", "HISTORY", "CANCELLED"]})
        selected, labels = source_filters(frame, container, "Reactive")
        self.assertEqual(list(selected.Status), ["ACTIVE"])
        self.assertEqual(labels, ["Status: ACTIVE"])


class AccessRules(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        with connect(self.root) as db:
            db.execute("INSERT INTO users (username,password,role) VALUES (?,?,?)", ("admin", password_hash("test-password-123"), "admin"))
        save_user(self.root, "admin", "operations", "operations", "ops-password-123")

    def tearDown(self):
        self.temp.cleanup()

    def test_operations_cannot_administer(self):
        with self.assertRaises(PermissionError):
            save_user(self.root, "operations", "intruder", "admin", "new-password-123")

    def test_password_change_revokes_session(self):
        user = authenticate(self.root, "operations", "ops-password-123")
        identity = dict(user, expires=time.time() + 3600)
        self.assertIsNotNone(current_user(self.root, identity))
        save_user(self.root, "admin", "operations", "operations", "replacement-password-123")
        self.assertIsNone(current_user(self.root, identity))
        self.assertIsNone(authenticate(self.root, "operations", "ops-password-123"))

    def test_last_admin_protected(self):
        with self.assertRaises(ValueError):
            save_user(self.root, "admin", "admin", "operations")

    def test_lockout(self):
        for _ in range(5):
            self.assertIsNone(authenticate(self.root, "operations", "wrong"))
        self.assertIsNone(authenticate(self.root, "operations", "ops-password-123"))


if __name__ == "__main__":
    unittest.main()
