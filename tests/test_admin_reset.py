import json
import secrets
import tempfile
import unittest
from security import apply_approved_admin_reset, connect, password_hash, verify, save_user


class ApprovedAdminReset(unittest.TestCase):
    def test_reset_is_admin_only_and_applies_once(self):
        with tempfile.TemporaryDirectory() as root:
            db = connect(root)
            for user, role in [('admin', 'admin'), ('operations', 'operations')]:
                db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', (user, password_hash('old-test-password'), role))
            db.execute('INSERT INTO attempts VALUES (?,?,?)', ('admin', 5, 99999999999))
            db.commit()
            db.close()
            payload = json.dumps({'username': 'admin', 'request_id': secrets.token_hex(16), 'password_hash': password_hash('new-test-password')})
            self.assertTrue(apply_approved_admin_reset(root, payload))
            db = connect(root)
            admin = db.execute("SELECT password,version FROM users WHERE username='admin'").fetchone()
            ops = db.execute("SELECT password,version FROM users WHERE username='operations'").fetchone()
            self.assertTrue(verify('new-test-password', admin['password']))
            self.assertEqual(admin['version'], 2)
            self.assertTrue(verify('old-test-password', ops['password']))
            self.assertEqual(ops['version'], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE username='admin'").fetchone()[0], 0)
            db.close()
            save_user(root, 'admin', 'admin', 'admin', 'later-test-password')
            self.assertFalse(apply_approved_admin_reset(root, payload))
            db = connect(root)
            self.assertTrue(verify('later-test-password', db.execute("SELECT password FROM users WHERE username='admin'").fetchone()[0]))
            db.close()

    def test_no_request_changes_nothing(self):
        self.assertFalse(apply_approved_admin_reset('unused', None))
