"""One-time, digest-verified private snapshot loading, followed by Streamlit."""
import gc
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import zipfile

PAGE = b'''<!doctype html><html><head><meta charset="utf-8"><title>Al Bonian FM setup</title>
<style>body{background:#102b4e;color:white;font:18px system-ui;max-width:640px;margin:12vh auto;padding:30px}button{padding:12px 20px;margin-top:20px}input{display:block;margin-top:20px}</style></head>
<body><h1>Al Bonian FM</h1><h2>Private data initialization</h2>
<p>Upload the approved private application package. Only the exact pre-approved file is accepted. No uploaded data is served publicly.</p>
<input type="file" id="package" accept=".zip" aria-label="Private application package"><button id="upload">Initialize private data</button><p id="status" role="status"></p>
<script>document.querySelector('#upload').onclick=async()=>{const file=document.querySelector('#package').files[0];if(!file)return;const b=document.querySelector('#upload');b.disabled=true;const s=document.querySelector('#status');s.textContent='Uploading private data...';try{const r=await fetch('/initialize',{method:'POST',headers:{'Content-Type':'application/zip'},body:file});s.textContent=await r.text();if(r.ok){s.textContent+=' Opening sign-in shortly.';setTimeout(()=>location.reload(),8000);}else{b.disabled=false;}}catch(e){s.textContent='Upload interrupted. Reload to check initialization status.';b.disabled=false;}};</script></body></html>'''


def install_package(payload, root, expected):
    if not expected or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected):
        raise ValueError("This file is not the approved private package.")
    if (root / "current.txt").exists():
        raise ValueError("Data is already initialized.")
    prefix = "ABFM-Dashboard/data/"
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 300 * 1024 * 1024:
            raise ValueError("Package is too large.")
        pointer = archive.read(prefix + "current.txt").decode().strip()
        if Path(pointer).name != pointer or not pointer.startswith("snapshot-"):
            raise ValueError("Invalid snapshot pointer.")
        manifest = json.loads(archive.read(prefix + pointer + "/manifest.json"))
        if "User_Security" in manifest["tables"]:
            raise ValueError("Legacy credentials cannot be imported.")
        files = ["manifest.json"] + [entry["file"] for entry in manifest["tables"].values()]
        if any(Path(name).name != name for name in files):
            raise ValueError("Invalid snapshot paths.")
        stage = Path(tempfile.mkdtemp(prefix="init-", dir=root))
        try:
            for name in files:
                (stage / name).write_bytes(archive.read(prefix + pointer + "/" + name))
            account_bytes = archive.read(prefix + "accounts.sqlite")
            # Activate the pointer last. No partial snapshot is exposed.
            destination = root / pointer
            if destination.exists():
                raise ValueError("Snapshot directory already exists.")
            os.replace(stage, destination)
            account_temp = root / "accounts.initial"
            account_temp.write_bytes(account_bytes)
            account_temp.chmod(0o600)
            os.replace(account_temp, root / "accounts.sqlite")
            temp_pointer = root / "current.initial"
            temp_pointer.write_text(pointer)
            os.replace(temp_pointer, root / "current.txt")
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def main():
    root = Path(os.environ.get("ABFM_DATA_DIR", "/var/data"))
    port = int(os.environ.get("PORT", "10000"))
    if not (root / "current.txt").exists():
        expected = os.environ.get("ABFM_PACKAGE_SHA256", "")
        lock = threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Never log request headers, URLs, or uploaded content.

            def answer(self, code, body, kind="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/_stcore/health":
                    self.answer(200, b"ok")
                else:
                    self.answer(200, PAGE, "text/html; charset=utf-8")

            def do_POST(self):
                if self.path != "/initialize":
                    self.answer(404, b"Not found")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 50 * 1024 * 1024:
                        raise ValueError("Invalid upload size.")
                    self.connection.settimeout(180)
                    with lock:
                        install_package(self.rfile.read(size), root, expected)
                    self.answer(201, b"Private data and accounts initialized successfully.")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                except (ValueError, KeyError, OSError, zipfile.BadZipFile):
                    self.answer(400, b"Package rejected. Use the approved private package; existing data is retained.")
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        print("Waiting for approved private data initialization.", flush=True)
        server.serve_forever()
        server.server_close()
        gc.collect()
    from security import apply_approved_admin_reset
    if apply_approved_admin_reset(root, os.environ.pop('ABFM_APPROVED_ADMIN_RESET', None)):
        print('Approved admin credential reset applied.', flush=True)
    from parquet_layout import prepare_snapshot
    if prepare_snapshot(root):
        print("Prepared bounded data blocks for dashboard reads.", flush=True)
    print("Starting Al Bonian FM dashboard.", flush=True)
    os.execvp("python", ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", f"--server.port={port}", "--browser.gatherUsageStats=false"])


if __name__ == "__main__":
    main()
