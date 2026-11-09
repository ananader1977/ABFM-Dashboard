"""Compact linked charts and bounded-memory Excel exports."""
import gc
import io
import math
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import streamlit as st
from analytics import dates, resolve
from visuals import INK, GOLD, PALETTE, categories, date_fields

MISSING = '(Not specified)'
TECHNICIAN = 'Technician · Assigned Resource'
MANAGER = 'Contract Manager (Contract)'
MANAGER_FIELDS = {MANAGER,'Contract Manager (Contract) (Contract)','Approved by (Operation)','Contract In Charge','Manager','Managers'}


def labels(series):
    from mail_reports import DISPLAY_NAMES, display_name
    result=series.astype('string[pyarrow]').str.strip().fillna(MISSING).replace('', MISSING).replace(DISPLAY_NAMES)
    if series.name in MANAGER_FIELDS:
        result=result.replace({value:display_name(value) for value in result.unique()})
    return result


def reconcile_multiselect(state_key,choices,normalize_names=False):
    from mail_reports import display_name
    if state_key not in st.session_state:return
    previous=st.session_state[state_key] or []
    valid=set(choices)
    chosen=list(dict.fromkeys(display_name(value) if normalize_names else value for value in previous))
    chosen=[value for value in chosen if value in valid]
    if chosen!=previous:st.session_state[state_key]=chosen


def refresh_work_order_filters(state,version):
    if state.get('_work_order_filter_version')==version:return
    pages=['Reactive','PPM','Corrective','Pro-active']
    prefixes=tuple(prefix+page+'_' for page in pages for prefix in ['quick_','chart_filters_','columns_','record_page_','export_columns_','export_page_'])+('filter_Reactive_','date_Reactive_','search_Reactive_')
    for name in list(state):
        if name.startswith(prefixes) or name in {'fields_Reactive','executive_scope'}:state.pop(name,None)
    state['_work_order_filter_version']=version


def apply_chart_filters(frame, selections, exclude=None):
    result = frame
    for field, values in selections.items():
        if field != exclude and field in frame and values:
            result = result.loc[labels(result[field]).isin(values)]
    return result


def selection_values(event):
    selection = event.get('selection', {}).get('pick', {})
    if isinstance(selection, list):
        return [row['Category'] for row in selection if isinstance(row, dict) and 'Category' in row]
    values = selection.get('Category', [])
    if not values:
        values = [v.get('Category') for v in selection.get('vlPoint', {}).get('or', []) if 'Category' in v]
    return list(values) if isinstance(values, (list, tuple)) else [values]


def chart_callback(widget, state, field):
    values = selection_values(st.session_state.get(widget, {}))
    selected = dict(st.session_state.get(state, {}))
    if values:
        selected[field] = values
    else:
        selected.pop(field, None)
    st.session_state[state] = selected


def clear_charts(state):
    st.session_state[state] = {}
    st.session_state[state + '_generation'] = st.session_state.get(state + '_generation', 0) + 1


def chart(data, key, callback, title, horizontal=True):
    st.markdown('**' + title + '**')
    spec = {
        'height': 235, 'background': '#fffefa',
        'params': [{'name': 'pick', 'select': {'type': 'point', 'fields': ['Category'], 'toggle': 'event.shiftKey', 'clear': 'dblclick'}}],
        'mark': {'type': 'bar', 'cornerRadiusEnd': 3, 'color': GOLD},
        'encoding': {
            'y' if horizontal else 'x': {'field': 'Category', 'type': 'nominal', 'sort': None,
                'axis': {'title': None, 'labelLimit': 155, 'labelAngle': 0 if horizontal else -35}},
            'x' if horizontal else 'y': {'field': 'Records', 'type': 'quantitative', 'axis': {'title': 'Records'}},
            'opacity': {'condition': {'param': 'pick', 'value': 1}, 'value': .25},
            'tooltip': [{'field': 'Category'}, {'field': 'Records', 'type': 'quantitative', 'format': ','}]
        },
        'config': {'font':'Calibri','view': {'stroke': None}, 'axis': {'labelColor': INK, 'titleColor': INK, 'gridColor': '#eae5da'}}
    }
    return st.vega_lite_chart(data, spec, key=key, on_select=callback, selection_mode='pick', width='stretch')


def linked_charts(frame, table, page):
    """Render charts before metrics so callbacks apply to every downstream view."""
    frame = frame.copy(deep=False)
    month_fields = date_fields(frame, table)
    if month_fields:
        frame['Reported month · ' + month_fields[0]] = dates(frame[month_fields[0]]).dt.strftime('%Y-%m').astype('string[pyarrow]')
    dims = []
    for name in ['Status', MANAGER, TECHNICIAN, 'Priority', 'Category'] + categories(frame):
        if name in frame and name not in dims:
            dims.append(name)
    if month_fields:
        dims.insert(min(3, len(dims)), 'Reported month · ' + month_fields[0])
    # Dataset-specific fields provide useful charts when generic dimensions are absent.
    for name in frame.columns:
        if len(dims) >= 6:
            break
        if name not in dims and not pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_datetime64_any_dtype(frame[name]) and 1 < frame[name].nunique() <= 40:
            dims.append(name)
    dims = dims[:6]
    state = 'chart_filters_' + page + '_' + table
    from mail_reports import display_name
    active = {c: list(dict.fromkeys(display_name(value) if c in MANAGER_FIELDS else value for value in v)) for c, v in st.session_state.get(state, {}).items() if c in dims}
    st.caption('Click a bar to filter charts, totals and records. Shift-click selects several values; double-click clears that chart. Up to 12 categories per chart; use filters to find other values.')
    st.button('Clear chart selections', key=state + '_clear', on_click=clear_charts, args=(state,))
    if active:
        st.caption('Selected: ' + ' · '.join(f'{c}: {", ".join(map(str, v))}' for c, v in active.items()))
    for offset in range(0, len(dims), 2):
        for column, field in zip(st.columns(2), dims[offset:offset + 2]):
            with column:
                context = apply_chart_filters(frame, active, exclude=field)
                counts = labels(context[field]).value_counts()
                if field.startswith('Reported month'):
                    counts = counts.sort_index().tail(12)
                else:
                    counts = counts.head(12)
                data = counts.rename_axis('Category').reset_index(name='Records')
                widget = f'{state}_{dims.index(field)}_{st.session_state.get(state + "_generation", 0)}'
                chart(data, widget, lambda w=widget, s=state, f=field: chart_callback(w, s, f), field,
                      horizontal=not field.startswith('Reported month'))
    result = apply_chart_filters(frame, active)
    return result.drop(columns=[c for c in result if c.startswith('Reported month · ')])


def quick_filters(frame, page):
    fields = [MANAGER] + ([TECHNICIAN] if page in {'Reactive', 'PPM', 'Corrective', 'Pro-active'} else [])
    if 'Source site' in frame:
        fields.insert(0,'Source site')
    if page=='Reactive' and 'Category' in frame:
        fields.append('Category')
    columns = st.columns(len(fields))
    for column, field in zip(columns, fields):
        if field not in frame:
            continue
        with column:
            choices = sorted(labels(frame[field]).unique().tolist())
            state_key='quick_' + page + '_' + field
            reconcile_multiselect(state_key,choices,normalize_names=field in MANAGER_FIELDS)
            chosen = st.multiselect(field, choices, key=state_key)
            if chosen:
                frame = frame.loc[labels(frame[field]).isin(chosen)]
    return frame


def col_name(index):
    value = ''
    while index:
        index, digit = divmod(index - 1, 26)
        value = chr(65 + digit) + value
    return value


def xml_cell(value, address):
    if value is None or value is pd.NA or value is pd.NaT:
        return ''
    if isinstance(value, float) and not math.isfinite(value):
        return ''
    if isinstance(value, bool):
        return f'<c r="{address}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{address}"><v>{value}</v></c>'
    # inline strings preserve IDs, literal formulas and date ISO text safely.
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767]
    return f'<c r="{address}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def excel_bytes(columns, rows):
    """Streaming OOXML writer for application downloads; never builds a full sheet tree."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Records" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        with z.open('xl/worksheets/sheet1.xml', 'w') as sheet:
            sheet.write(b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><sheetData>')
            def write_row(number, values):
                sheet.write((f'<row r="{number}">' + ''.join(xml_cell(v, col_name(i) + str(number)) for i, v in enumerate(values, 1)) + '</row>').encode())
            write_row(1, columns)
            last = 1
            for last, row in enumerate(rows, 2):
                if last > 1048576:
                    raise ValueError('Excel row limit exceeded. Narrow the filters.')
                write_row(last, row)
            sheet.write((f'</sheetData><autoFilter ref="A1:{col_name(len(columns))}{last}"/></worksheet>').encode())
    return output.getvalue()


def export_rows(path, task_type, columns, positions, extras=None):
    """Read original text fields only in bounded batches for the selected positions."""
    positions = iter(sorted(int(i) for i in positions))
    target = next(positions, None)
    dataset = ds.dataset(path, format='parquet')
    source_columns = [c for c in columns if c in dataset.schema.names]
    expr = pc.utf8_upper(pc.utf8_trim_whitespace(ds.field('Task Type'))) == task_type if task_type else None
    scanner = dataset.scanner(columns=source_columns, filter=expr, use_threads=False, batch_size=256,
        batch_readahead=0, fragment_readahead=0, fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=False))
    offset = 0
    for batch in scanner.to_batches():
        if target is None:
            break
        picked = []
        while target is not None and target < offset + batch.num_rows:
            picked.append((target, target - offset))
            target = next(positions, None)
        if picked:
            values = batch.to_pydict()
            for position, row in picked:
                yield [values[c][row] if c in values else extras.at[position, c] if extras is not None and c in extras else None for c in columns]
        offset += batch.num_rows


def records_panel(selected, path, table, page, all_columns, task_type=None):
    from data_access import load_rows
    with st.expander('Records & Excel download', expanded=False):
        extra = [c for c in [TECHNICIAN, MANAGER] if c in selected and c not in all_columns]
        choices = list(all_columns) + extra
        visible = st.multiselect('Export / table columns', choices, default=choices,
            key='export_columns_' + page + '_' + table)
        size = 50
        pages = max(1, (len(selected) + size - 1) // size)
        number = st.number_input('Table page', min_value=1, max_value=pages, value=1,
            key=f'export_page_{page}_{table}_{len(selected)}')
        if visible:
            source = [c for c in visible if c in all_columns]
            positions = selected.index[(int(number)-1)*size:int(number)*size]
            preview = load_rows(str(path), task_type, source, positions) if source else pd.DataFrame(index=range(len(positions)))
            for c in extra:
                if c in visible:
                    preview[c] = selected.loc[positions, c].to_numpy()
            st.dataframe(preview[visible], hide_index=True, width='stretch')
            st.caption(f'{len(selected):,} filtered records. Excel contains every matching row, not just this table page. Dates export as ISO text; long text follows Excel’s 32,767-character cell limit.')
            # Deferred download avoids retaining large files on every dashboard rerun.
            def build_export():
                return excel_bytes(visible, export_rows(str(path), task_type, visible, selected.index, selected))
            st.download_button('Download filtered records · Excel', data=build_export,
                file_name=re.sub(r'[^A-Za-z0-9_-]', '_', page + '_' + table) + '.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                key='excel_' + page + '_' + table, on_click='ignore')
        else:
            st.info('Select at least one column to view or export.')
