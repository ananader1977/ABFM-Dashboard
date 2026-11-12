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
STATUS_MAP = {'Completed':'COMPLETE','Scheduled':'ACTIVE','In Progress':'ASSIGNED','Unscheduled':'ACTIVE','Canceled':'CANCELLED','Cancelled':'CANCELLED'}


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


REQUIRED_EXCEL_COLUMNS = ['WO No.','Work Order Type','System Status','Priority','Assigned Resource','Contract Manager (Contract) (Contract)','Created On']


def _excel_open(source):
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException
    from zipfile import BadZipFile
    if hasattr(source,'seek'):source.seek(0)
    try:return load_workbook(source,read_only=True,data_only=True,keep_links=False)
    except (InvalidFileException,BadZipFile,OSError,ValueError,EOFError) as exc:
        raise ValueError('Cannot read this workbook. Upload a valid, unencrypted Excel .xlsx file.') from exc


def excel_sheet_names(source):
    book=_excel_open(source)
    try:return list(book.sheetnames)
    finally:book.close()


def _excel_rows(book,sheet_name):
    if sheet_name not in book.sheetnames:raise ValueError('The selected worksheet is missing from this workbook.')
    sheet=book[sheet_name]
    # Some exports report incorrect worksheet dimensions. Stream the actual XML rows.
    sheet.reset_dimensions()
    rows=sheet.iter_rows(values_only=True)
    first=next(rows,())
    headers=['' if value is None else str(value).strip() for value in first]
    while headers and not headers[-1]:headers.pop()
    if not headers or any(not name for name in headers):raise ValueError('The first worksheet row must contain column headings without gaps.')
    if len({name.casefold() for name in headers})!=len(headers):raise ValueError('Duplicate column headings are not allowed.')
    missing=[name for name in REQUIRED_EXCEL_COLUMNS if name not in headers]
    if missing:raise ValueError('Missing required columns: '+', '.join(missing)+'. Select the work-order sheet, with headings in row 1.')
    return headers,rows


def preview_excel(source,sheet_name):
    book=_excel_open(source)
    try:
        headers,rows=_excel_rows(book,sheet_name)
        samples=[]
        for values in rows:
            if any(value is not None and str(value).strip() for value in values):
                samples.append(list(values[:len(headers)])+[None]*max(0,len(headers)-len(values)))
                if len(samples)==5:break
        if not samples:raise ValueError('The selected worksheet contains no work-order records.')
        return pd.DataFrame(samples,columns=headers)
    finally:book.close()


def _excel_date(value,epoch,row_number):
    from datetime import date,datetime
    from openpyxl.utils.datetime import from_excel
    try:
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            if not 1 <= value <= 100000:raise ValueError()
            result=pd.Timestamp(from_excel(value,epoch))
        elif isinstance(value,(date,datetime)):
            result=pd.Timestamp(value)
        elif isinstance(value,str) and value.strip():
            import re
            value=value.strip()
            if re.match(r'^\d{4}-\d{2}-\d{2}',value):result=pd.Timestamp(value)
            else:
                result=None
                for fmt in ['%d/%m/%Y %H:%M:%S','%d/%m/%Y %H:%M','%d/%m/%Y','%d-%m-%Y','%d/%m/%y %I:%M %p','%d/%m/%Y %I:%M %p']:
                    try:result=pd.Timestamp(datetime.strptime(value,fmt));break
                    except ValueError:pass
                if result is None:raise ValueError()
        else:raise ValueError()
        if pd.isna(result):raise ValueError()
        if result.tzinfo is not None:result=result.tz_convert('Asia/Dubai').tz_localize(None)
        return result.to_pydatetime()
    except (ValueError,TypeError,OverflowError) as exc:
        raise ValueError(f'Excel row {row_number}: Created On must be a valid Excel date or date text (YYYY-MM-DD or DD/MM/YYYY).') from exc


def install_excel_replacement(source,root,sheet_name):
    """Read Excel in small batches; activate only a completely validated snapshot."""
    from datetime import date,datetime
    root=Path(root)
    book=_excel_open(source)
    try:
        if 'Field Mapping' in book.sheetnames:
            from mapped_import import install_mapped_workbook
            return install_mapped_workbook(book,root,Path(str(getattr(source,'name',source))).name,sheet_name)
        headers,rows=_excel_rows(book,sheet_name)
        schema=pa.schema([pa.field(name,pa.timestamp('ns') if name=='Created On' else pa.string()) for name in headers])
        types={name.casefold():name for name in TYPE_MAP}
        statuses={name.casefold():name for name in STATUS_MAP}
        with tempfile.TemporaryDirectory(prefix='excel-import-',dir=root) as directory:
            prepared=Path(directory)/'validated.parquet'
            with pq.ParquetWriter(prepared,schema) as writer:
                batch=[]
                for row_number,values in enumerate(rows,start=2):
                    if not any(value is not None and str(value).strip() for value in values):continue
                    if any(value is not None and str(value).strip() for value in values[len(headers):]):raise ValueError(f'Excel row {row_number}: values extend beyond the column headings.')
                    record={}
                    for i,name in enumerate(headers):
                        value=values[i] if i<len(values) else None
                        if name=='Created On':record[name]=_excel_date(value,book.epoch,row_number)
                        elif value is None:record[name]=None
                        elif name=='WO No.' and isinstance(value,(int,float)) and not isinstance(value,bool):
                            if not float(value).is_integer():raise ValueError(f'Excel row {row_number}: WO No. must be a whole number or text identifier.')
                            record[name]=str(int(value))
                        elif isinstance(value,(date,datetime)):record[name]=value.isoformat()
                        else:record[name]=str(value).strip() or None
                    kind=types.get(str(record['Work Order Type']).casefold())
                    if not kind:raise ValueError(f'Excel row {row_number}: Work Order Type must be Reactive, PPM, Corrective or Pro-active.')
                    status=statuses.get(str(record['System Status']).casefold())
                    if not status:raise ValueError(f'Excel row {row_number}: unknown System Status. Use Completed, Scheduled, In Progress, Unscheduled or Canceled/Cancelled.')
                    if not record['WO No.']:raise ValueError(f'Excel row {row_number}: WO No. is missing.')
                    record['Work Order Type']=kind;record['System Status']=status;batch.append(record)
                    if len(batch)==512:
                        writer.write_table(pa.Table.from_pylist(batch,schema=schema),row_group_size=512);batch=[]
                if batch:writer.write_table(pa.Table.from_pylist(batch,schema=schema),row_group_size=512)
            source_name=Path(str(getattr(source,'name',source))).name
            return install_replacement(prepared,root,source_name=source_name,sheet_name=sheet_name)
    finally:book.close()


def install_replacement(source, root, source_name="Anas - All Work Orders.xlsx", sheet_name=None,metadata=None,recipient_config=None):
    current = root / (root / 'current.txt').read_text().strip()
    manifest = json.loads((current / 'manifest.json').read_text())
    stage = Path(tempfile.mkdtemp(prefix='snapshot-wo-', dir=root))
    try:
        output = stage / 'work_orders.parquet'
        writer = None
        count = 0
        ids = set()
        counts = {}
        blank_priorities = 0
        columns = None
        try:
            incoming = pq.ParquetFile(source)
            canonical_fields = [pa.field('Task Id', pa.string()), pa.field('Task Type', pa.string()), pa.field('Status', pa.string()), pa.field('Task Reported Date', pa.timestamp('ns')), pa.field(TECHNICIAN, pa.string()), pa.field(MANAGER, pa.string())]
            if 'Service Account' in incoming.schema_arrow.names:
                canonical_fields.append(pa.field('Contract Title', pa.string()))
            out_schema = pa.schema([f for f in incoming.schema_arrow if f.name not in {c.name for c in canonical_fields}] + canonical_fields)
            for batch in incoming.iter_batches(batch_size=512):
                frame = canonicalize(batch.to_pandas())
                blank_priorities += int(frame['Priority'].astype('string').fillna('').str.strip().eq('').sum()) if 'Priority' in frame else len(frame)
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
        for table_name, info in manifest['tables'].items():
            if table_name != 'Reactive' and not (stage / info['file']).exists():
                link_or_copy(current / info['file'], stage / info['file'])
        for page in manifest['pages']:
            if page['name'] not in TYPE_MAP:
                manifest.setdefault('page_imported_at',{}).setdefault(page['name'],manifest['imported_at'])
        manifest['pages'] = [{'name':name,'visuals':[]} for name in TYPE_MAP] + [p for p in manifest['pages'] if p['name'] not in TYPE_MAP]
        manifest['work_orders_only'] = len(manifest['tables']) == 1
        manifest['measures'] = []
        manifest['import_warnings'] = []
        manifest.pop('source_sha256', None)
        for page in manifest['pages']:
            if page['name'] in {'Reactive','PPM','Corrective'}:
                page['visuals'] = []
        if not any(p['name'] == 'Pro-active' for p in manifest['pages']):
            manifest['pages'].insert(3, {'name':'Pro-active','visuals':[]})
        manifest['work_order_source'] = {'name':source_name,'sheet':sheet_name,'blank_priority_rows':blank_priorities,'rows':count,'counts':counts,'imported_at':datetime.now(timezone.utc).isoformat(),'technician_field':'Assigned Resource','previous_snapshot':current.name,'due_dates_available':False}
        manifest['work_order_source'].update(metadata or {})
        manifest['imported_at'] = manifest['work_order_source']['imported_at']
        (stage / 'manifest.json').write_text(json.dumps(manifest))
        pointer = root / 'current.replacement'
        pointer.write_text(stage.name)
        config_path=root/'escalation_config.json'
        previous_config=config_path.read_bytes() if recipient_config is not None and config_path.exists() else None
        if recipient_config is not None:
            from escalation import save_config
            save_config(root,recipient_config)
        try:
            os.replace(pointer, root / 'current.txt')
        except Exception:
            if recipient_config is not None:
                if previous_config is None:config_path.unlink(missing_ok=True)
                else:config_path.write_bytes(previous_config)
            raise
        # Import activation is independent of the derived comparison cache.
        # The executive page retries history recovery from retained snapshots.
        try:
            from task_history import import_observations
            import_observations(root)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Upload comparison cache will be rebuilt from retained snapshots.')
        return manifest['work_order_source']
    except Exception:
        shutil.rmtree(stage)
        raise


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def restrict_active_snapshot(root):
    """Retire old datasets from the active snapshot while retaining recovery."""
    pointer = root / 'current.txt'
    if not pointer.exists():
        return False
    current = root / pointer.read_text().strip()
    manifest = json.loads((current / 'manifest.json').read_text())
    if not manifest.get('work_order_source') or manifest.get('work_orders_only') or manifest.get('requested_restored_pages'):
        return False
    stage = Path(tempfile.mkdtemp(prefix='snapshot-work-orders-only-', dir=root))
    try:
        info = manifest['tables']['Reactive']
        link_or_copy(current / info['file'], stage / info['file'])
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


RESTORED_PAGES = {'MR tracker':'MR Main Sheet','RFQ':'RFQ','SubContractors Agrements':'Subcontractor AMC'}


def restore_requested_workspaces(root):
    """Restore precisely the three user-selected datasets, keeping current work orders."""
    pointer = root / 'current.txt'
    current = root / pointer.read_text().strip()
    manifest = json.loads((current / 'manifest.json').read_text())
    if not manifest.get('work_order_source') or manifest.get('requested_restored_pages') == list(RESTORED_PAGES):
        return False
    # Follow recorded snapshot lineage rather than selecting an arbitrary old dataset.
    sources = {}
    previous = manifest.get('retired_snapshot') or manifest['work_order_source'].get('previous_snapshot')
    visited = set()
    while previous and previous not in visited and len(visited) < 20:
        if Path(previous).name != previous:
            raise ValueError('Invalid prior snapshot path.')
        visited.add(previous)
        directory = root / previous
        old = json.loads((directory / 'manifest.json').read_text())
        for page, table in RESTORED_PAGES.items():
            if table in old['tables'] and table not in sources:
                sources[table] = (directory, old['tables'][table], old.get('page_imported_at', {}).get(page, old['imported_at']), next((p for p in old['pages'] if p['name'] == page), {'name':page,'visuals':[]}))
        if len(sources) == len(RESTORED_PAGES):
            break
        previous = old.get('retired_snapshot') or old.get('work_order_source', {}).get('previous_snapshot')
    if len(sources) != len(RESTORED_PAGES):
        raise ValueError('The prior snapshot does not contain all three requested workspaces.')
    stage = Path(tempfile.mkdtemp(prefix='snapshot-restored-workspaces-', dir=root))
    try:
        for info in manifest['tables'].values():
            link_or_copy(current / info['file'], stage / info['file'])
        manifest.setdefault('page_imported_at', {})
        for page, table in RESTORED_PAGES.items():
            directory, info, stamp, definition = sources[table]
            # Assign a distinct file name to avoid collisions across source snapshots.
            name = 'restored_' + table.replace(' ', '_') + '.parquet'
            link_or_copy(directory / info['file'], stage / name)
            manifest['tables'][table] = dict(info, file=name)
            manifest['pages'] = [p for p in manifest['pages'] if p['name'] != page] + [definition]
            manifest['page_imported_at'][page] = stamp
        manifest['work_orders_only'] = False
        manifest['requested_restored_pages'] = list(RESTORED_PAGES)
        manifest['restoration_previous_snapshot'] = current.name
        (stage / 'manifest.json').write_text(json.dumps(manifest))
        temporary = root / 'current.restore_workspaces'
        temporary.write_text(stage.name)
        os.replace(temporary, pointer)
        return True
    except Exception:
        shutil.rmtree(stage)
        raise


PROCUREMENT_EXPORTS = {
    'RFQ': ('RFQ', 'Form Responses 1', ('Reference Number', 'Timestamp'), ('Status', 'Approved by (Operation)', 'Assigned To (Procurement / Store) please put e-mail address of procurement / store officer')),
    'Subcontractor AMC': ('SubContractors Agrements', 'AMC Tracker', ('Vendor Name', 'Job Code'), ('Vendor Name', 'Job Code', 'End Date')),
}


def install_procurement_exports(root, rfq, agreements):
    """Validate both Excel exports before activating a single new snapshot."""
    from zipfile import BadZipFile
    from openpyxl.utils.exceptions import InvalidFileException

    def header(value):
        return ' '.join(str(value or '').split()).casefold()

    root = Path(root)
    pointer = root / 'current.txt'
    original = pointer.read_text().strip()
    current = root / original
    manifest = json.loads((current / 'manifest.json').read_text())
    stage = Path(tempfile.mkdtemp(prefix='snapshot-procurement-', dir=root))
    stamp = datetime.now(timezone.utc).isoformat()
    counts = {}
    try:
        for table, source in [('RFQ', rfq), ('Subcontractor AMC', agreements)]:
            page, tab, identifiers, required = PROCUREMENT_EXPORTS[table]
            old = manifest['tables'][table]
            canonical = {header(c): c for c in old['columns']}
            try:
                book = _excel_open(source)
            except (BadZipFile, InvalidFileException) as exc:
                raise ValueError(f'{table}: upload a valid Excel workbook.') from exc
            try:
                sheets = [s for s in book if s.title.strip() == tab]
                if len(sheets) != 1:
                    raise ValueError(f'{table}: expected the {tab} worksheet.')
                sheet = sheets[0]
                sheet.reset_dimensions()
                rows = sheet.iter_rows(values_only=True)
                headings = next(rows, ())
                keys = [header(c) for c in headings]
                if len(set(k for k in keys if k)) != sum(bool(k) for k in keys):
                    raise ValueError(f'{table}: duplicate column headings.')
                missing = [c for c in required if header(c) not in keys]
                if missing or not any(header(c) in keys for c in identifiers):
                    raise ValueError(f'{table}: missing tracker columns: {", ".join(missing or identifiers)}.')
                columns = [canonical.get(k, str(c).strip()) for c, k in zip(headings, keys) if k]
                positions = [i for i, k in enumerate(keys) if k]
                identity_positions = [keys.index(header(c)) for c in identifiers if header(c) in keys]
                values = []
                for row in rows:
                    if not any(i < len(row) and str(row[i] or '').strip() for i in identity_positions):
                        continue
                    values.append([str(row[i]) if i < len(row) and row[i] is not None else None for i in positions])
                if not values:
                    raise ValueError(f'{table}: no tracker records found.')
            finally:
                book.close()
            frame = pd.DataFrame(values, columns=columns, dtype='string')
            filename = 'procurement_rfq.parquet' if table == 'RFQ' else 'procurement_agreements.parquet'
            frame.to_parquet(stage / filename, index=False)
            manifest['tables'][table] = {'file': filename, 'rows': len(frame), 'columns': columns}
            manifest.setdefault('page_imported_at', {})[page] = stamp
            manifest.setdefault('procurement_sources', {})[table] = {
                'name': Path(getattr(source, 'name', 'Excel export')).name,
                'imported_at': stamp, 'rows': len(frame),
            }
            counts[table] = len(frame)
        for table, info in manifest['tables'].items():
            if table not in PROCUREMENT_EXPORTS and not (stage / info['file']).exists():
                link_or_copy(current / info['file'], stage / info['file'])
        manifest['retired_snapshot'] = original
        (stage / 'manifest.json').write_text(json.dumps(manifest))
        candidate = stage / 'activate.txt'
        candidate.write_text(stage.name)
        if pointer.read_text().strip() != original:
            raise ValueError('Another import changed the active snapshot. Apply these files again.')
        os.replace(candidate, pointer)
    except Exception:
        shutil.rmtree(stage)
        raise
    return counts
