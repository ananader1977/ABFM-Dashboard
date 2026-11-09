"""Bounded-batch, projected reads; never decode the entire work-order file first."""
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

# Return freed buffers to the operating system instead of retaining a large pool.
pa.set_memory_pool(pa.system_memory_pool())


def load_table(path, task_type=None, columns=None):
    dataset = ds.dataset(path, format="parquet")
    expression = None
    if task_type is not None:
        expression = pc.utf8_upper(pc.utf8_trim_whitespace(ds.field("Task Type"))) == task_type
    scanner = dataset.scanner(
        columns=list(columns) if columns is not None else None,
        filter=expression,
        use_threads=False,
        batch_size=512,
        batch_readahead=0,
        fragment_readahead=0,
        fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=False),
    )
    table = pa.Table.from_batches(scanner.to_batches(), schema=scanner.projected_schema)
    return table.to_pandas(types_mapper=pd.ArrowDtype)


SOURCE_VISUAL_TYPES = {"barChart", "clusteredBarChart", "stackedBarChart", "columnChart", "clusteredColumnChart", "stackedColumnChart", "hundredPercentStackedBarChart", "hundredPercentStackedColumnChart", "lineChart", "areaChart", "pieChart", "donutChart", "card", "cardVisual"}


def source_visuals(page):
    return [c for c in sorted(page['visuals'], key=lambda c: (c.get('position', {}).get('y', 0), c.get('position', {}).get('x', 0))) if c.get('visual', {}).get('visualType') in SOURCE_VISUAL_TYPES]


def work_order_columns(available, view, filter_fields=(), visual=None):
    needed = {"Task Id", "Status", "Task Type", "Task Due Date", "Task Reported Date", "Category", "Priority", "Project Name", "Contract Title", "Contract In Charge", "Building", "Building Name", "Service Type", "Supervisor", "Trade", "SEGMET", "Categories"}
    needed.update({"Technician · Assigned Resource", "Contract Manager (Contract)", "Source site"})
    needed.update(filter_fields)
    if view == 'Source charts':
        needed.update({'Documents', 'Resources'})
        def fields(node):
            if isinstance(node, dict):
                if 'Column' in node:
                    needed.add(node['Column'].get('Property', ''))
                for child in node.values():
                    fields(child)
            elif isinstance(node, list):
                for child in node:
                    fields(child)
        fields(visual)
    return tuple(c for c in available if c in needed)


def display_columns(available, table, view, filter_fields=(), visual=None):
    if table == 'Reactive':
        return work_order_columns(available, view, filter_fields, visual)
    needed = {
        'Status','Priority','Priority:','Category','Project Name','Contract Title','Contract In Charge',
        'PROJECT NAME','Project Details:','Premises','Managers','Manager','Vendor_Category','Vendor Name',
        'COMPANY NAME','SEGMET','Categories','Contract','Building Name','Trade','Designation','Contract Ref',
        'Balance Value','Unbilled','Invoiced Value','Non collectable Amounts','Annual Value (Excl. 5%)',
        'Monthly Cost','2026 Cost ','Completion Date','Unbilled Date','Inv. Date','Quotation Request Date:',
        'Quotation  Expected  Date:','Timestamp','MR DATE','LPO.DATE','PR. DATE:','Starting Date','Ending Date',
        'Start Date','End Date ','Client AMC End','Client AMC Status','Client AMC Status1','DOJ','AL Starts','AL Ends','Data Updated Date','Reported',
        'Survey Date','Date','Delay Days','EMAIL ID','MOBILE NO','ITEM DESCRIPTION','QUANTITY',
        'HR','Name','IsActive','Submission','Manpower on sites','Manpower as per contract','Status_3',
        'Detailed Scope of Work – (Needs to include details of material, services and manpower clearly)',
        'Overall satisfaction with Al Bonian Facilities Management','Quality of our work','Our response time',
        'Courtesy and professionalism of our personnel','Task ID',
    }
    needed.update({"Technician · Assigned Resource", "Contract Manager (Contract)"})
    needed.update(filter_fields)
    def fields(node):
        if isinstance(node, dict):
            if 'Column' in node:needed.add(node['Column'].get('Property',''))
            for child in node.values():fields(child)
        elif isinstance(node,list):
            for child in node:fields(child)
    if view=='Source charts':fields(visual)
    normalized = {s.strip().casefold() for s in needed}
    return tuple(c for c in available if c.strip().casefold() in normalized)


def load_rows(path, task_type, columns, positions):
    """Read only the displayed rows, without retaining all detail text in RAM."""
    positions = sorted(int(p) for p in positions)
    if not positions:
        return pd.DataFrame(columns=columns)
    dataset = ds.dataset(path, format='parquet')
    expression = None
    if task_type is not None:
        expression = pc.utf8_upper(pc.utf8_trim_whitespace(ds.field('Task Type'))) == task_type
    scanner = dataset.scanner(columns=list(columns), filter=expression, use_threads=False,
                              batch_size=256, batch_readahead=0, fragment_readahead=0,
                              fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=False))
    batches, offset, next_position = [], 0, 0
    for batch in scanner.to_batches():
        within = []
        while next_position < len(positions) and positions[next_position] < offset + batch.num_rows:
            within.append(positions[next_position] - offset)
            next_position += 1
        if within:
            batches.append(batch.take(pa.array(within, type=pa.int64())))
        offset += batch.num_rows
        if next_position == len(positions):
            break
    return pa.Table.from_batches(batches, schema=scanner.projected_schema).to_pandas(types_mapper=pd.ArrowDtype)
