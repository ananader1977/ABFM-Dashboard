"""Atomically replace only work orders; retain all other datasets and prior snapshot."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime, timezone
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from interactive import TECHNICIAN, MANAGER

TYPE_MAP = {'Reactive':'BREAKDOWN','PPM':'PPM','Corrective':'SCHEDULEDTASK','Pro-active':'PROACTIVE'}
STATUS_MAP = {'Completed':'COMPLETE','Scheduled':'ACTIVE','In Progress':'ASSIGNED','Unscheduled':'ACTIVE','Canceled':'CANCELLED'}


def canonicalize(frame):
    required = ['WO No.', 'Work Order Type', 'System Status', 'Assigned Resource', 'Contract Manager (Contract) (Contract)', 'Created On']
    if not set(required) <= set(frame.columns):
        raise ValueError('Replacement file is missing required work-order columns.')
    if frame['Work Order Type'].isna().any():
        raise ValueError('Work Order Type is missing; replacement was not applied.')
    unknown = set(frame['Work Order Type'].dropna().unique()) - set(TYPE_MAP)
    if unknown:
        raise ValueError('Unmapped work-order types: ' + ', '.join(sorted(unknown)))
    result = frame.copy(deep=False)
    result['Task Id'] = frame['WO No.'].astype('string[pyarrow]')
    result['Task Type'] = frame['Work Order Type'].map(TYPE_MAP).astype('string[pyarrow]')
    result['Status'] = frame['System Status'].map(STATUS_MAP).fillna(frame['System Status']).astype('string[pyarrow]')
    result['Task Reported Date'] = pd.to_datetime(frame['Created On'], errors='coerce')
    result[TECHNICIAN] = frame['Assigned Resource'].astype('string[pyarrow]')
    result[MANAGER] = frame['Contract Manager (Contract) (Contract)'].astype('string[pyarrow]')
    if 'Service Account' in frame:
        result['Contract Title'] = frame['Service Account'].astype('string[pyarrow]')
    return result


def install_replacement(source, root):
    current = root / (root / 'current.txt').read_text().strip()
    manifest = json.loads((current / 'manifest.json').read_text())
    stage = Path(tempfile.mkdtemp(prefix='snapshot-wo-', dir=root))
    try:
        output = stage / 'work_orders.parquet'
        writer = None
        count = 0
        ids = set()
        counts = {}
        columns = None
        try:
            incoming = pq.ParquetFile(source)
            canonical_fields = [pa.field('Task Id', pa.string()), pa.field('Task Type', pa.string()), pa.field('Status', pa.string()), pa.field('Task Reported Date', pa.timestamp('ns')), pa.field(TECHNICIAN, pa.string()), pa.field(MANAGER, pa.string())]
            if 'Service Account' in incoming.schema_arrow.names:
                canonical_fields.append(pa.field('Contract Title', pa.string()))
            out_schema = pa.schema([f for f in incoming.schema_arrow if f.name not in {c.name for c in canonical_fields}] + canonical_fields)
            for batch in incoming.iter_batches(batch_size=512):
                frame = canonicalize(batch.to_pandas())
                for value in frame['Task Id']:
                    if pd.isna(value) or not str(value).strip() or value in ids:
                        raise ValueError('Missing or duplicate WO No.; replacement was not applied.')
                    ids.add(value)
                for name, value in frame['Work Order Type'].value_counts().items():
                    counts[name] = counts.get(name, 0) + int(value)
                table = pa.Table.from_pandas(frame, schema=out_schema, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(output, table.schema)
                    columns = list(frame.columns)
                writer.write_table(table, row_group_size=512)
                count += len(frame)
        finally:
            if writer:
                writer.close()
        if not count:
            raise ValueError('Replacement contains no records.')
        manifest['tables']['Reactive'] = {'file': output.name, 'rows': count, 'columns': columns}
        manifest['tables'] = {'Reactive': manifest['tables']['Reactive']}
        manifest['pages'] = [{'name':name,'visuals':[]} for name in TYPE_MAP]
        manifest['work_orders_only'] = True
        manifest['measures'] = []
        manifest['import_warnings'] = []
        manifest.pop('source_sha256', None)
        for page in manifest['pages']:
            if page['name'] in {'Reactive','PPM','Corrective'}:
                page['visuals'] = []
        if not any(p['name'] == 'Pro-active' for p in manifest['pages']):
            manifest['pages'].insert(3, {'name':'Pro-active','visuals':[]})
        manifest['work_order_source'] = {'name':'Anas - All Work Orders.xlsx','rows':count,'counts':counts,'imported_at':datetime.now(timezone.utc).isoformat(),'technician_field':'Assigned Resource','previous_snapshot':current.name,'due_dates_available':False}
        manifest['imported_at'] = manifest['work_order_source']['imported_at']
        (stage / 'manifest.json').write_text(json.dumps(manifest))
        pointer = root / 'current.replacement'
        pointer.write_text(stage.name)
        os.replace(pointer, root / 'current.txt')
        return manifest['work_order_source']
    except Exception:
        shutil.rmtree(stage)
        raise


def restrict_active_snapshot(root):
    """Retire old datasets from the active snapshot while retaining recovery."""
    pointer = root / 'current.txt'
    if not pointer.exists():
        return False
    current = root / pointer.read_text().strip()
    manifest = json.loads((current / 'manifest.json').read_text())
    if not manifest.get('work_order_source') or manifest.get('work_orders_only'):
        return False
    stage = Path(tempfile.mkdtemp(prefix='snapshot-work-orders-only-', dir=root))
    try:
        info = manifest['tables']['Reactive']
        os.link(current / info['file'], stage / info['file'])
        manifest['tables'] = {'Reactive': info}
        manifest['pages'] = [{'name':name,'visuals':[]} for name in TYPE_MAP]
        manifest['measures'] = []
        manifest['import_warnings'] = []
        manifest['work_orders_only'] = True
        manifest['retired_snapshot'] = current.name
        manifest['imported_at'] = manifest['work_order_source']['imported_at']
        (stage / 'manifest.json').write_text(json.dumps(manifest))
        temp = root / 'current.work_orders_only'
        temp.write_text(stage.name)
        os.replace(temp, pointer)
        return True
    except Exception:
        shutil.rmtree(stage)
        raise


def restore_snapshot(root, name):
    if Path(name).name != name or not name.startswith('snapshot-') or not (root / name / 'manifest.json').is_file():
        raise ValueError('Invalid snapshot selected.')
    temp = root / 'current.restore'
    temp.write_text(name)
    os.replace(temp, root / 'current.txt')
