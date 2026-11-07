"""Server-side account store. Only salted password hashes are persisted."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
import json
import re


def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "accounts.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','operations')), active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1)")
    db.execute("CREATE TABLE IF NOT EXISTS attempts (username TEXT PRIMARY KEY, failures INTEGER, locked_until REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS audit (at REAL, actor TEXT, action TEXT, target TEXT)")
    db.execute('CREATE TABLE IF NOT EXISTS browser_sessions (token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,version INTEGER NOT NULL,expires REAL NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS browser_session_expiry ON browser_sessions(expires)')
    db.commit()
    return db


def password_hash(password):
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Password must contain 12–256 characters.")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + digest


def verify(password, stored):
    try:
        salt, digest = stored.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


def authenticate(root, username, password):
    username = username.strip().lower()[:100]
    with connect(root) as db:
        attempt = db.execute("SELECT * FROM attempts WHERE username=?", (username,)).fetchone()
        if attempt and attempt["locked_until"] > time.time():
            return None
        user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if user and len(password) <= 256 and verify(password, user["password"]):
            db.execute("DELETE FROM attempts WHERE username=?", (username,))
            db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), username, "login", username))
            return {k: user[k] for k in ("username", "role", "version")}
        count = (attempt["failures"] if attempt else 0) + 1
        db.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?)", (username, count, time.time() + 900 if count >= 5 else 0))
    return None


def current_user(root, identity):
    if not identity or identity.get("expires", 0) < time.time():
        return None
    with connect(root) as db:
        row = db.execute("SELECT username,role,version FROM users WHERE username=? AND active=1", (identity["username"],)).fetchone()
        if row and row["version"] == identity["version"]:
            return dict(row)
    return None


def save_user(root, actor, username, role, password=None, active=True):
    username = username.strip().lower()
    if not username or len(username) > 100 or role not in ("admin", "operations"):
        raise ValueError("Enter a valid username and role.")
    with connect(root) as db:
        admin = db.execute("SELECT role,active FROM users WHERE username=?", (actor,)).fetchone()
        if not admin or admin["role"] != "admin" or not admin["active"]:
            raise PermissionError("Admin access required.")
        existing = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if existing and existing["role"] == "admin" and existing["active"] and (role != "admin" or not active):
            if db.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                raise ValueError("Keep at least one active administrator.")
        hashed = password_hash(password) if password else (existing["password"] if existing else None)
        if not hashed:
            raise ValueError("New accounts require a password.")
        db.execute("INSERT INTO users (username,password,role,active,version) VALUES (?,?,?,?,1) ON CONFLICT(username) DO UPDATE SET password=excluded.password,role=excluded.role,active=excluded.active,version=users.version+1", (username, hashed, role, int(active)))
        db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), actor, "save account", username))


def rename_user(root, actor, old, new):
    new = new.strip().lower()
    if not new or len(new) > 100:
        raise ValueError("Enter a valid username.")
    with connect(root) as db:
        admin = db.execute("SELECT role,active FROM users WHERE username=?", (actor,)).fetchone()
        if not admin or admin["role"] != "admin" or not admin["active"]:
            raise PermissionError("Admin access required.")
        if db.execute("SELECT 1 FROM users WHERE username=?", (new,)).fetchone():
            raise ValueError("That username already exists.")
        if db.execute("UPDATE users SET username=?,version=version+1 WHERE username=?", (new, old)).rowcount != 1:
            raise ValueError("Account not found.")
        db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), actor, "rename account to " + new, old))


def bootstrap(root):
    """Run from the server terminal; credentials never enter Git or app logs."""
    import getpass
    with connect(root) as db:
        if db.execute("SELECT count(*) FROM users").fetchone()[0]:
            raise ValueError("Accounts already exist. Use the admin screen.")
        for username, role in (("admin", "admin"), ("operations", "operations")):
            password = getpass.getpass(f"Set {username} password (12+ characters): ")
            db.execute("INSERT INTO users (username,password,role) VALUES (?,?,?)", (username, password_hash(password), role))


def apply_approved_admin_reset(root, payload):
    """Apply an owner-approved secret supplied by the hosting control plane once."""
    if not payload:
        return False
    request = json.loads(payload)
    request_id = request.get('request_id', '')
    hashed = request.get('password_hash', '')
    if request.get('username') != 'admin' or not re.fullmatch(r'[0-9a-f]{32}', request_id) or not re.fullmatch(r'[0-9a-f]{32}:[0-9a-f]{128}', hashed):
        raise ValueError('Invalid approved admin reset request.')
    db = connect(root)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS applied_admin_resets (request_id TEXT PRIMARY KEY, applied_at REAL NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM applied_admin_resets WHERE request_id=?', (request_id,)).fetchone():
            db.commit()
            return False
        user = db.execute("SELECT role,active FROM users WHERE username='admin'").fetchone()
        if not user or user['role'] != 'admin' or not user['active']:
            raise ValueError('The active admin account was not found.')
        db.execute("UPDATE users SET password=?,version=version+1 WHERE username='admin'", (hashed,))
        db.execute("DELETE FROM attempts WHERE username='admin'")
        db.execute('INSERT INTO applied_admin_resets VALUES (?,?)', (request_id, time.time()))
        db.execute('INSERT INTO audit VALUES (?,?,?,?)', (time.time(), 'hosting-owner', 'approved password reset', 'admin'))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    bootstrap(os.environ.get("ABFM_DATA_DIR", "data"))


SESSION_COOKIE = '__Host-abfm_session'
SESSION_LIFETIME = 8 * 3600


def create_browser_session(root, user):
    """Persist only a token digest; keep the existing eight-hour access window."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with connect(root) as db:
        current = db.execute('SELECT version FROM users WHERE username=? AND active=1', (user['username'],)).fetchone()
        if not current or current['version'] != user['version']:
            raise ValueError('Account changed. Sign in again.')
        db.execute('DELETE FROM browser_sessions WHERE expires<=?', (now,))
        db.execute('INSERT INTO browser_sessions VALUES (?,?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), user['username'], user['version'], now + SESSION_LIFETIME))
    return token


def browser_session_user(root, token):
    if not token or not isinstance(token,str) or len(token)>128:
        return None
    with connect(root) as db:
        row = db.execute('''SELECT u.username,u.role,u.version FROM browser_sessions s
            JOIN users u ON u.username=s.username
            WHERE s.token_hash=? AND s.expires>? AND u.active=1 AND u.version=s.version''',
            (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return dict(row) if row else None


def revoke_browser_session(root, token):
    if token and isinstance(token,str) and len(token)<=128:
        with connect(root) as db:
            db.execute('DELETE FROM browser_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))


def configure_connection_secret(root):
    """Keep Streamlit's XSRF signing key private and stable across service restarts."""
    import os
    if os.environ.get('STREAMLIT_SERVER_COOKIE_SECRET'):
        return
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    path=root/'streamlit_cookie_secret'
    try:
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor,'w') as target:
            target.write(secrets.token_urlsafe(48))
    path.chmod(0o600)
    secret=path.read_text().strip()
    if len(secret)<32:raise ValueError('The private connection signing key is invalid.')
    os.environ['STREAMLIT_SERVER_COOKIE_SECRET']=secret
