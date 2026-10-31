"""Extract a private PBIX snapshot. Never copy its data to a source repository."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone


def extract(source, destination):
    from pbixray import PBIXRay
    import pandas as pd
    import numpy as np
    from pbixray.vertipaq_decoder import VertiPaqDecoder
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    model = PBIXRay(str(source))
    warnings = []
    original = VertiPaqDecoder._handle_special_cases
    def checked_dates(self, values, semantic_type, column_name=None):
        if semantic_type == "Date":
            numeric = pd.to_numeric(values, errors="coerce")
            invalid = numeric.notna() & ~numeric.between(-693593, 2958465)
            if invalid.any():
                warnings.append({"column": column_name, "invalid_date_values": int(invalid.sum())})
            values = numeric.mask(invalid).to_numpy(dtype="float64", na_value=np.nan)
            valid = np.isfinite(values)
            out = np.full(len(values), np.datetime64("NaT", "s"), dtype="datetime64[s]")
            seconds = np.rint(values[valid] * 86400).astype("int64")
            out[valid] = np.datetime64("1899-12-30T00:00:00", "s") + seconds.astype("timedelta64[s]")
            return pd.Series(out, index=numeric.index)
        return original(self, values, semantic_type, column_name)
    VertiPaqDecoder._handle_special_cases = checked_dates
    stage = Path(tempfile.mkdtemp(prefix="snapshot-", dir=destination))
    try:
        tables = {}
        for i, name in enumerate(model.tables):
            if name == "User_Security" or name.startswith(("LocalDateTable_", "DateTableTemplate_")):
                continue
            frame = model.get_table(name)
            if len(frame.columns) == 0:
                continue
            filename = f"table_{i:03}.parquet"
            frame.to_parquet(stage / filename, index=False, row_group_size=2048)
            tables[name] = {"file": filename, "rows": len(frame), "columns": list(frame.columns)}
        pages = []
        with zipfile.ZipFile(source) as archive:
            order = json.loads(archive.read("Report/definition/pages/pages.json"))["pageOrder"]
            for page_id in order:
                prefix = f"Report/definition/pages/{page_id}/"
                page = json.loads(archive.read(prefix + "page.json"))
                visuals = []
                for n in archive.namelist():
                    if n.startswith(prefix + "visuals/") and n.endswith("visual.json"):
                        v = json.loads(archive.read(n))
                        visuals.append(v)
                pages.append({"name": page["displayName"], "visuals": visuals})
        manifest = {"imported_at": datetime.now(timezone.utc).isoformat(),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "tables": tables, "pages": pages,
                    "measures": [r for r in model.dax_measures.to_dict("records") if r.get("TableName") != "User_Security"], "import_warnings": warnings}
        (stage / "manifest.json").write_text(json.dumps(manifest, default=str))
        # Immutable snapshot directories allow readers to finish during an import.
        os.replace(stage, destination / ("snapshot-" + manifest["source_sha256"][:12] + "-" + stage.name[-8:]))
        completed = sorted(destination.glob("snapshot-*/manifest.json"), key=lambda p: p.stat().st_mtime)[-1].parent
        temp_pointer = destination / "current.tmp"
        temp_pointer.write_text(completed.name)
        os.replace(temp_pointer, destination / "current.txt")
        return {"tables": len(tables), "rows": sum(t["rows"] for t in tables.values()), "pages": len(pages)}
    finally:
        VertiPaqDecoder._handle_special_cases = original
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("pbix")
    p.add_argument("--data-dir", default=os.environ.get("ABFM_DATA_DIR", "data"))
    args = p.parse_args()
    print(json.dumps(extract(args.pbix, args.data_dir)))
