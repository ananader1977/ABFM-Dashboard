"""Migrate large Parquet row groups atomically before starting the web app."""
import json
import os
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

ROW_GROUP_SIZE = 2048


def prepare_snapshot(root):
    root = Path(root)
    pointer = root / 'current.txt'
    if not pointer.exists():
        return 0
    name = pointer.read_text().strip()
    if Path(name).name != name:
        raise ValueError('Invalid snapshot pointer')
    directory = root / name
    manifest = json.loads((directory / 'manifest.json').read_text())
    converted = 0
    pa.set_memory_pool(pa.system_memory_pool())
    for table in manifest['tables'].values():
        filename = table['file']
        if Path(filename).name != filename:
            raise ValueError('Invalid table path')
        path = directory / filename
        source = pq.ParquetFile(path, memory_map=True, pre_buffer=False)
        if all(source.metadata.row_group(i).num_rows <= ROW_GROUP_SIZE for i in range(source.metadata.num_row_groups)):
            source.close()
            continue
        temporary = path.with_suffix('.parquet.preparing')
        try:
            with pq.ParquetWriter(temporary, source.schema_arrow, compression='zstd') as writer:
                for batch in source.iter_batches(batch_size=ROW_GROUP_SIZE, use_threads=False):
                    writer.write_batch(batch, row_group_size=ROW_GROUP_SIZE)
            source.close()
            os.replace(temporary, path)
            converted += 1
        finally:
            source.close()
            if temporary.exists():
                temporary.unlink()
    return converted
