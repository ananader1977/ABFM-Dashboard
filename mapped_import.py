"""Stream mapped multi-system workbooks into the master work-order schema."""
import json
import re
import tempfile
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from escalation import MANAGER,email,text

SITE_ALIASES={'G.V.V (Maximo)':['The Gardens Co.','G.V.V','G.V.V.'], 'Meydan (Maximo)':['Meydan GS','Meydan'], 'MR1 Report (Maximo)':['MR1'], 'DP World (MRI)':['Jebel Ali Free Zone - DP World','DP World']}
MRI_HEADERS={'Task ID':'TA_TASK_ID','Contract Title':'CTR_REFERENCE','Building':'BG_SITE','Floor':'LO_FLOOR','Location':'LO_DESCRIPTION','Priority':'TA_PRIORITY','Category':'TA_CATEGORY','Status':'TA_STATUS','Logged By':'Reporter','Respond Date':'Response Date','Contain Date (SLA)':'Contain Date'}
WORK_TYPES={'PM':'PPM','PPM':'PPM','RM':'Reactive','EM':'Reactive','CM':'Corrective','INSP':'Pro-active','HSE':'Pro-active','BREAKDOWN':'Reactive','SCHEDULEDTASK':'Corrective','PROACTIVE':'Pro-active'}
STATUSES={'CLOSE':'Completed','CLOSED':'Completed','COMP':'Completed','COMPLETE':'Completed','COMPLETED':'Completed','HISTORY':'Completed','RFI':'Completed',
 'CAN':'Canceled','CANCELED':'Canceled','CANCELLED':'Canceled','TENANT CANCELED':'Canceled','TENANT CANCELLED':'Canceled','CLOSE NEGATIVELY':'Canceled',
 'APPR':'Scheduled','APPROVED':'Scheduled','WAPPR':'Unscheduled','WAITING ON APPROVAL':'Unscheduled','ASSIGNED':'In Progress','INPRG':'In Progress','IN PROGRESS':'In Progress',
 'TENANT NOT AVAILABLE':'Unscheduled','TENANT NOT AVAIL':'Unscheduled','QUOTATION PREPARATION':'Unscheduled','QUOTATION SUBMITTED':'Unscheduled','QPRE':'Unscheduled','QSUB':'Unscheduled','ACTIVE':'Unscheduled','HOLD NO ACCESS':'Unscheduled','ATTENDED':'Unscheduled','HOLD':'Unscheduled'}
EXTRAS=['Source site','Source system','Source sheet','Source work type','Source status','Source manager','Source description','Mapping note']

def clean(value):
    return '' if value is None else str(value).replace('\xa0',' ').strip()

def token(value):return ' '.join(clean(value).upper().replace('_',' ').split())

def source_sheets(book):
    return {column:next((s for s in aliases if s in book.sheetnames),None) for column,aliases in SITE_ALIASES.items()}

def roster_candidate(book,root):
    from escalation import load_config
    config=dict(load_config(root))
    names={'operations':['Operation Data','Opertation Data'],'procurement':['Procurement Data','Procurment Data']}
    for kind,aliases in names.items():
        name=next((n for n in aliases if n in book.sheetnames),None)
        if not name:raise ValueError('Mapped imports require Operation Data and Procurement Data recipient sheets.')
        s=book[name];s.reset_dimensions();rows=s.iter_rows(values_only=True);headers=[clean(v) for v in next(rows,())];unique={}
        for values in rows:
            row=dict(zip(headers,values))
            if kind=='operations':
                if not clean(row.get(MANAGER)):continue
                person={'name':clean(row.get(MANAGER)),'email':email(row.get('email')),'director':email(row.get('Director Email')),'coo':email(row.get('COO E-mail'))}
            else:
                if not clean(row.get('Assigned To (Procurement)')):continue
                person={'approver':clean(row.get('Approved by (Operation)')),'operations_email':email(row.get('Approved by (Operation email)')),'assignee':clean(row.get('Assigned To (Procurement)')),'email':email(row.get('Procurement email')),'procurement_manager':email(row.get('Procurment Manager email')),'director':email(row.get('Director Email')),'coo':email(row.get('COO E-mail'))}
            required=['email','director','coo'] if kind=='operations' else ['email','procurement_manager']
            if any(not person[k] for k in required):raise ValueError(f'{name}: missing or invalid recipient address for '+person.get('name',person.get('assignee','')))
            unique[json.dumps(person,sort_keys=True)]=person
        config[kind]=list(unique.values())
    return config

def site_managers(book):
    name=next(n for n in ['Operation Data','Opertation Data'] if n in book.sheetnames)
    s=book[name];s.reset_dimensions();rows=s.iter_rows(values_only=True);headers=[clean(v) for v in next(rows,())];people={}
    from mail_reports import display_name
    for values in rows:
        row=dict(zip(headers,values));site=token(row.get('Service Account'));manager=clean(row.get(MANAGER))
        if site and manager:people.setdefault(site,{})[display_name(manager)]=manager
    return {site:next(iter(managers.values())) for site,managers in people.items() if len(managers)==1}

def install_mapped_workbook(book,root,source_name,master_sheet):
    from work_orders import _excel_rows,_excel_date,install_replacement,TYPE_MAP,STATUS_MAP
    root=Path(root);headers,master_rows=_excel_rows(book,master_sheet)
    mapping_sheet=book['Field Mapping'];mapping_sheet.reset_dimensions();mapping_rows=mapping_sheet.iter_rows(values_only=True);mapping_headers=[clean(v) for v in next(mapping_rows,())]
    if not mapping_headers or mapping_headers[0]!='Dynamic':raise ValueError('Field Mapping must start with the Dynamic master-field column.')
    mapping={clean(row[0]):{column:clean(value) for column,value in zip(mapping_headers[1:],row[1:])} for row in mapping_rows if row and clean(row[0])}
    if set(headers)-set(mapping):raise ValueError('Field Mapping is missing master fields: '+', '.join(sorted(set(headers)-set(mapping))))
    recipient_config=roster_candidate(book,root);managers=site_managers(book);sources=source_sheets(book)
    fields=headers+EXTRAS;schema=pa.schema([pa.field(name,pa.timestamp('ns') if name=='Created On' else pa.string()) for name in fields])
    counts={};status_counts={};notes=[];batch=[];manager_backfills=0;category_names={}
    with tempfile.TemporaryDirectory(prefix='mapped-workbook-',dir=root) as temporary:
        prepared=Path(temporary)/'merged.parquet'
        with pq.ParquetWriter(prepared,schema) as writer:
            def append(record,site,row_number):
                record['Created On']=_excel_date(record.get('Created On'),book.epoch,row_number)
                for field in fields:
                    if field!='Created On':record[field]=clean(record.get(field)) or None
                if record['Work Order Type'] not in TYPE_MAP:raise ValueError(f'{site} row {row_number}: unmapped work type {record["Work Order Type"]!r}.')
                if record['System Status'] not in STATUS_MAP:raise ValueError(f'{site} row {row_number}: unmapped status {record["System Status"]!r}.')
                batch.append(record);counts[site]=counts.get(site,0)+1
                status_counts[record['System Status']]=status_counts.get(record['System Status'],0)+1
                if len(batch)==512:writer.write_table(pa.Table.from_pylist(batch,schema=schema),row_group_size=512);batch.clear()
            for row_number,values in enumerate(master_rows,2):
                if not any(clean(v) for v in values):continue
                record=dict(zip(headers,values));record.update({'Source site':clean(record.get('Service Account')) or master_sheet,'Source sheet':master_sheet,'Source system':'Dynamics','Source work type':record.get('Work Order Type'),'Source status':record.get('System Status'),'Source manager':record.get(MANAGER)})
                if clean(record.get('Category')):category_names.setdefault(token(record['Category']),clean(record['Category']))
                if not clean(record.get(MANAGER)) and token(record.get('Service Account')) in managers:
                    record[MANAGER]=managers[token(record['Service Account'])]
                    record['Mapping note']='Blank manager filled from unique Operation Data service-account mapping.'
                    manager_backfills+=1
                if isinstance(record.get('WO No.'),(int,float)):record['WO No.']=str(int(record['WO No.']))
                append(record,master_sheet,row_number)
            for column,site in sources.items():
                if site is None:continue
                sheet=book[site];sheet.reset_dimensions();rows=sheet.iter_rows(values_only=True);source_headers=[clean(v) for v in next(rows,())]
                counts.setdefault(site,0)
                for row_number,values in enumerate(rows,2):
                    if not any(clean(v) for v in values):continue
                    row=dict(zip(source_headers,values));record={}
                    for field in headers:
                        origin=mapping[field].get(column,'')
                        if token(origin) in {'','N/A'}:record[field]=None;continue
                        actual=origin if origin in row else MRI_HEADERS.get(origin,origin) if column=='DP World (MRI)' else origin
                        record[field]=row.get(actual)
                    original_type=record.get('Work Order Type');original_status=record.get('System Status')
                    kind=WORK_TYPES.get(token(original_type));note=''
                    if not kind and not clean(original_type) and re.search(r'\bPPM\b',token(row.get('Description'))):
                        kind='PPM';note='Blank source work type; PPM explicitly stated in source description.'
                        notes.append({'sheet':site,'row':row_number,'WO No.':clean(record.get('WO No.')),'note':note})
                    record['Work Order Type']=kind or clean(original_type)
                    record['Category']=category_names.get(token(record.get('Category')),record.get('Category'))
                    status=STATUSES.get(token(original_status)) or STATUSES.get(token(row.get('Status')))
                    if column == 'MR1 Report (Maximo)':
                        source_statuses = {token(original_status), token(row.get('Status'))}
                        if 'CLOSE NEGATIVELY' in source_statuses:
                            status = 'Completed'
                        elif 'WAITING ON MATERIAL' in source_statuses:
                            status = 'Unscheduled'
                    if column == 'G.V.V (Maximo)' and 'CLOSE NEGATIVELY' in {token(original_status), token(row.get('Status'))}:
                        status = 'Unscheduled'
                    record['System Status']=status or clean(original_status)
                    if token(site) not in managers:raise ValueError(f'{site}: no unique line manager in Operation Data. Use the exact site worksheet name in Service Account.')
                    record['Source manager']=record.get(MANAGER)
                    record[MANAGER]=managers[token(site)]
                    priority=record.get('Priority')
                    if clean(priority) and re.fullmatch(r'\d+(?:\.0)?',clean(priority)):
                        record['Priority']='P'+str(int(float(priority)))+' - Source priority '+str(int(float(priority)))
                    record.update({'Source site':site,'Source sheet':site,'Source system':'MRI' if column=='DP World (MRI)' else 'Maximo','Source work type':original_type,'Source status':original_status,'Source description':row.get('Description',row.get('TA_SHORT_DESC')),'Mapping note':note})
                    append(record,site,row_number)
            if batch:writer.write_table(pa.Table.from_pylist(batch,schema=schema),row_group_size=512)
        metadata={'merged_sources':counts,'mapping_notes':notes,'mapped_status_counts':status_counts,'manager_backfills':manager_backfills,'field_mapping_applied':True,'empty_source_sheets':[s for s,n in counts.items() if n==0]}
        return install_replacement(prepared,root,source_name=source_name,sheet_name='Merged mapped sheets',metadata=metadata,recipient_config=recipient_config)
