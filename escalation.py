"""Priority queues, verified recipient routing and daily escalation delivery."""
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.dataset as ds
from analytics import OPEN, normalized

TZ = ZoneInfo('Asia/Dubai')
MANAGER = 'Contract Manager (Contract) (Contract)'
ASSIGNEE = 'Assigned To (Procurement / Store) please put e-mail address of procurement / store officer'
LOG = logging.getLogger('abfm.escalation')


def text(value):
    return '' if value is None or pd.isna(value) else str(value).strip()


def email(value):
    value = text(value).replace('\\xa0', '').replace('\xa0', '').strip().lower()
    if not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', value):
        return ''
    return value


def key(value):
    return ' '.join(text(value).casefold().split())


def load_config(root):
    path = Path(root) / 'escalation_config.json'
    return json.loads(path.read_text()) if path.exists() else {'enabled':False,'operations':[],'procurement':[]}


def save_config(root, config):
    path = Path(root) / 'escalation_config.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(config))
    temp.chmod(0o600)
    os.replace(temp,path)


def import_roster(source, root):
    if str(getattr(source,'name',source)).lower().endswith('.json'):
        incoming=json.load(source) if hasattr(source,'read') else json.loads(Path(source).read_text())
        config=load_config(root)
        for section in ['operations','procurement']:
            if not isinstance(incoming.get(section),list):raise ValueError('Invalid recipient configuration.')
            config[section]=incoming[section]
        if incoming.get('smtp'):
            config['smtp']=dict(config.get('smtp',{}),**{k:v for k,v in incoming['smtp'].items() if k!='password'})
        config['test_email']=email(incoming.get('test_email',config.get('test_email','')))
        save_config(root,config)
        return config
    import openpyxl
    workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
    aliases = {'operations':['Operation Data','Opertation Data'], 'procurement':['Procurement Data','Procurment Data']}
    config = load_config(root)
    for target, names in aliases.items():
        sheet = next((workbook[n] for n in names if n in workbook.sheetnames),None)
        if sheet is None:
            raise ValueError('The workbook must contain Operation Data and Procurement Data sheets.')
        rows = sheet.iter_rows(values_only=True)
        headers = [text(v) for v in next(rows)]
        unique = {}
        for values in rows:
            row = {h:text(v) for h,v in zip(headers,values)}
            if target=='operations':
                name = row.get(MANAGER,'')
                if not name:
                    continue
                record = {'name':name,'email':email(row.get('email')),'director':email(row.get('Director Email')),'coo':email(row.get('COO E-mail'))}
            else:
                record = {'approver':row.get('Approved by (Operation)',''),'operations_email':email(row.get('Approved by (Operation email)')),
                    'assignee':row.get('Assigned To (Procurement)',''),'email':email(row.get('Procurement email')),
                    'procurement_manager':email(row.get('Procurment Manager email')),
                    'director':email(row.get('Director Email')),'coo':email(row.get('COO E-mail'))}
            unique[json.dumps(record,sort_keys=True)] = record
        config[target] = list(unique.values())
    workbook.close()
    # Import never enables delivery or changes existing sender credentials.
    save_config(root,config)
    return config


def snapshot(root):
    root=Path(root); directory=root/(root/'current.txt').read_text().strip()
    return directory,json.loads((directory/'manifest.json').read_text())


def scan(directory, manifest, table, columns):
    info=manifest['tables'].get(table)
    if not info:
        return
    available=[c for c in columns if c in info['columns']]
    reader=ds.dataset(directory/info['file'],format='parquet').scanner(columns=available,batch_size=512,use_threads=False,
        batch_readahead=0,fragment_readahead=0,fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=False))
    for batch in reader.to_batches():
        yield batch.to_pandas(types_mapper=pd.ArrowDtype)


def opened_time(series):
    # Excel Created On values are interpreted as local Dubai wall-clock times.
    parsed=pd.to_datetime(series,errors='coerce')
    if getattr(parsed.dt,'tz',None) is None:
        return parsed.dt.tz_localize('Asia/Dubai')
    return parsed.dt.tz_convert(TZ)


def priority_mask(frame, now=None):
    now=pd.Timestamp(now or datetime.now(TZ))
    if now.tzinfo is None:now=now.tz_localize(TZ)
    if not {'Status','Priority','Created On'} <= set(frame):
        return pd.Series(False,index=frame.index)
    priority=frame['Priority'].astype('string').str.strip().str.upper().str.match(r'^P[12](?:\b|\s*-)',na=False)
    return normalized(frame.Status).isin(OPEN) & priority & opened_time(frame['Created On']).lt(now-pd.Timedelta(days=7))


def priority_tasks(directory,manifest,now=None):
    cols=['WO No.','Task Id','Work Order Type','Priority','System Status','Status','Created On','Service Account','Building','Category','Assigned Resource',MANAGER]
    frames=[]
    for frame in scan(directory,manifest,'Reactive',cols):
        selected=frame.loc[priority_mask(frame,now)].copy()
        if not selected.empty:frames.append(selected)
    if not frames:return pd.DataFrame(columns=cols+['Age (days)'])
    result=pd.concat(frames,ignore_index=True)
    now=pd.Timestamp(now or datetime.now(TZ))
    if now.tzinfo is None:now=now.tz_localize(TZ)
    result['Age (days)']=((now-opened_time(result['Created On'])).dt.total_seconds()/86400).round(1)
    return result.sort_values(['Priority','Created On'])


def manager_counts(directory,manifest):
    counts={};totals={}
    cols=['Work Order Type','Status',MANAGER]
    for frame in scan(directory,manifest,'Reactive',cols):
        if not set(cols)<=set(frame):continue
        frame=frame.loc[frame['Work Order Type'].isin(['Reactive','Corrective','Pro-active'])].copy()
        frame[MANAGER]=frame[MANAGER].fillna('(Not assigned)').replace('','(Not assigned)')
        for (name,kind),group in frame.groupby([MANAGER,'Work Order Type'],dropna=False):
            totals[name]=totals.get(name,0)+len(group)
            counts[(name,kind)]=counts.get((name,kind),0)+int(normalized(group.Status).isin(OPEN).sum())
    return [{'name':name,'Reactive':counts.get((name,'Reactive'),0),'Corrective':counts.get((name,'Corrective'),0),
             'Pro-active':counts.get((name,'Pro-active'),0),'total':totals[name],
             'open':sum(counts.get((name,k),0) for k in ['Reactive','Corrective','Pro-active'])} for name in sorted(totals)]


def open_rfqs(directory,manifest):
    cols=['Reference Number','Status','Priority:','Quotation Request Date:','Project Details:','Approved by (Operation)',ASSIGNEE,'Detailed Scope of Work – (Needs to include details of material, services and manpower clearly)']
    frames=[]
    for frame in scan(directory,manifest,'RFQ',cols):
        if 'Status' in frame:
            part=frame.loc[normalized(frame.Status).isin({'IN PROGRESS','RETURNED'})]
            if not part.empty:frames.append(part)
    return pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(columns=cols)


def unique_lookup(rows, field, value, required):
    matches=[r for r in rows if key(r.get(field))==key(value)]
    if not matches:return None,'No recipient mapping'
    variants={tuple(r.get(c,'') for c in required) for r in matches}
    if len(variants)!=1:return None,'Conflicting recipient mappings'
    row=matches[0]
    if any(not row.get(c) for c in required):return None,'Missing required recipient or Director/COO'
    return row,''


def routes(tasks, rfqs, config):
    groups={};issues=[]
    for _,row in tasks.iterrows():
        manager=text(row.get(MANAGER))
        dest,error=unique_lookup(config.get('operations',[]),'name',manager,['email','director','coo'])
        # Cross-sheet address conflicts require a correction, not a guessed identity.
        other={r['operations_email'] for r in config.get('procurement',[]) if key(r.get('approver'))==key(manager) and r.get('operations_email')}
        if dest and other and dest['email'] not in other:dest,error=None,'Operations and procurement sheets disagree on manager email'
        if not dest:
            issues.append({'Type':'Operations','Record':text(row.get('WO No.',row.get('Task Id'))),'Responsible':manager,'Issue':error});continue
        cc=tuple(sorted({dest['director'],dest['coo']}))
        group=groups.setdefault(('Operations',dest['email'],cc),[]);group.append(row.to_dict())
    staff=config.get('procurement',[])
    managers={r.get('procurement_manager') for r in staff if r.get('procurement_manager')}
    for _,row in rfqs.iterrows():
        assignee=text(row.get(ASSIGNEE));approver=text(row.get('Approved by (Operation)'))
        dest,error=unique_lookup(staff,'assignee',assignee,['email'])
        ops,ops_error=unique_lookup(staff,'approver',approver,['operations_email','director','coo'])
        if not dest or not ops or len(managers)!=1:
            issues.append({'Type':'Procurement','Record':text(row.get('Reference Number')),'Responsible':f'{assignee} / {approver}',
                           'Issue':error or ops_error or 'Procurement manager is missing or ambiguous'});continue
        cc=tuple(sorted({ops['operations_email'],ops['director'],ops['coo'],next(iter(managers))}))
        group=groups.setdefault(('Procurement',dest['email'],cc),[]);group.append(row.to_dict())
    return [{'kind':kind,'to':to,'cc':list(cc),'records':rows} for (kind,to,cc),rows in groups.items()],issues


def build_email(group,sender,source_date,test_to=None):
    msg=EmailMessage();msg['From']=sender;msg['To']=test_to or group['to']
    if not test_to:msg['Cc']=', '.join(group['cc'])
    msg['Subject']=('[TEST] ' if test_to else '')+f"ABFM {group['kind']} escalation · {len(group['records'])} open records · {datetime.now(TZ):%d %b %Y}"
    columns=['WO No.','Work Order Type','Priority','System Status','Created On','Age (days)','Service Account','Building',MANAGER] if group['kind']=='Operations' else ['Reference Number','Status','Priority:','Quotation Request Date:','Project Details:','Approved by (Operation)',ASSIGNEE]
    frame=pd.DataFrame(group['records']);cols=[c for c in columns if c in frame];frame=frame[cols]
    note=f"Source snapshot: {source_date}. This is an imported snapshot; closure and new-work updates require a new import."
    intro='Review the open P1/P2 tasks opened more than seven days ago and provide the action plan and expected closure date.' if group['kind']=='Operations' else 'Review the open RFQs and provide the quotation status and expected completion date.'
    msg.set_content(intro+'\n\n'+note+'\n\n'+frame.to_string(index=False))
    msg.add_alternative('<p>'+html.escape(intro)+'</p><p>'+html.escape(note)+'</p>'+frame.to_html(index=False,escape=True),subtype='html')
    return msg


def smtp_settings(config):
    return {name:os.environ.get('ABFM_SMTP_'+name.upper(),config.get('smtp',{}).get(name,'')) for name in ['host','port','username','password','sender']}


def send_groups(groups,config,source_date,test=False):
    smtp=smtp_settings(config)
    if not smtp['host'] or not email(smtp['sender']) or not smtp['username'] or not smtp['password']:
        raise ValueError('Configure an authenticated SMTP sender before sending escalation emails.')
    if not groups:return 0
    test_to=config.get('test_email','')
    if test and not email(test_to):raise ValueError('Set the test recipient email first.')
    if test:
        # Two test messages: one combined example for each escalation stream.
        groups=[{'kind':kind,'to':test_to,'cc':[], 'records':[row for g in groups if g['kind']==kind for row in g['records']]} for kind in ['Operations','Procurement'] if any(g['kind']==kind for g in groups)]
    messages=[build_email(g,smtp['sender'],source_date.get(g['kind'],'Unknown') if isinstance(source_date,dict) else source_date,test_to if test else None) for g in groups]
    # Always encrypt transport and require authenticated SMTP.
    port=int(smtp['port'] or 587)
    client=smtplib.SMTP_SSL(smtp['host'],port,timeout=30,context=ssl.create_default_context()) if port==465 else smtplib.SMTP(smtp['host'],port,timeout=30)
    with client:
        if port!=465:client.starttls(context=ssl.create_default_context())
        client.login(smtp['username'],smtp['password'])
        for message in messages:client.send_message(message)
    return len(messages)


def source_dates(manifest):
    return {'Operations':manifest.get('work_order_source',{}).get('imported_at',manifest['imported_at']),
            'Procurement':manifest.get('page_imported_at',{}).get('RFQ',manifest['imported_at'])}


def run_daily(root,now=None):
    config=load_config(root)
    if not config.get('enabled'):return
    now=now or datetime.now(TZ)
    if now.hour!=8:return
    directory,manifest=snapshot(root)
    tasks=priority_tasks(directory,manifest,now);rfqs=open_rfqs(directory,manifest)
    groups,issues=routes(tasks,rfqs,config)
    smtp=smtp_settings(config)
    ledger=sqlite3.connect(Path(root)/'escalation_delivery.sqlite',timeout=20)
    ledger.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY,status TEXT,created TEXT)')
    try:
        for group in groups:
            delivery_id=hashlib.sha256(json.dumps([str(now.date()),group['kind'],group['to'],group['cc']]).encode()).hexdigest()
            try:
                ledger.execute('INSERT INTO deliveries VALUES(?,?,?)',(delivery_id,'sending',now.isoformat()));ledger.commit()
            except sqlite3.IntegrityError:continue
            try:
                send_groups([group],config,source_dates(manifest))
                ledger.execute('UPDATE deliveries SET status=? WHERE id=?',('sent',delivery_id));ledger.commit()
            except Exception:
                # Delivery may have reached SMTP before a timeout. Do not blindly resend.
                ledger.execute('UPDATE deliveries SET status=? WHERE id=?',('needs review',delivery_id));ledger.commit()
                LOG.error('Escalation delivery needs review; inspect sender configuration and private delivery ledger.')
        report={'date':str(now.date()),'groups':len(groups),'routing_issues':len(issues),'checked_at':now.isoformat()}
        (Path(root)/'escalation_last_run.json').write_text(json.dumps(report))
    finally:ledger.close()


def start_scheduler(root):
    def loop():
        while True:
            try:run_daily(root)
            except Exception:LOG.exception('Escalation scheduler failed; no delivery assumed successful.')
            time.sleep(30)
    threading.Thread(target=loop,name='abfm-daily-escalation',daemon=True).start()


def executive_panels(directory,manifest):
    import streamlit as st
    from interactive import excel_bytes
    tasks=priority_tasks(directory,manifest)
    st.subheader('Open P1 / P2 tasks older than seven days')
    st.caption('All four work-order types. Scheduled, In Progress and Unscheduled are open; completed and cancelled tasks are excluded. Age is measured from Created On in Dubai time, strictly greater than seven days.')
    display=tasks.drop(columns=['Status'],errors='ignore')
    st.dataframe(display,hide_index=True,width='stretch')
    st.caption(f'{len(tasks):,} matching tasks · snapshot data, recalculated against the current time.')
    st.download_button('Download aged P1/P2 tasks · Excel',data=lambda:excel_bytes(list(display.columns),display.itertuples(index=False,name=None)),file_name='ABFM_Aged_P1_P2.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',on_click='ignore')


def review_cards(directory,manifest):
    import streamlit as st
    rows=manager_counts(directory,manifest)
    def open_manager(name,kind):
        from executive import navigate
        st.session_state['quick_'+kind+'_Contract Manager (Contract)'] = ['(Not specified)' if name=='(Not assigned)' else name]
        navigate(kind,'wo_open','Reactive')
    for offset in range(0,len(rows),3):
        for col,row in zip(st.columns(3),rows[offset:offset+3]):
            with col,st.container(border=True):
                st.markdown('**'+row['name']+'**')
                st.metric('Open Reactive / Corrective / Pro-active',f"{row['open']:,}")
                st.caption(' · '.join(f'{k}: {row[k]:,}' for k in ['Reactive','Corrective','Pro-active']))
                for kind in ['Reactive','Corrective','Pro-active']:
                    if row[kind]:st.button(f'View {kind} ({row[kind]:,})',key='manager_'+row['name']+'_'+kind,on_click=open_manager,args=(row['name'],kind))
                pct=100*row['open']/row['total'] if row['total'] else 0
                color='#b42318' if pct>5 else '#16703d' if pct<5 else '#65758a'
                st.markdown(f'<span style="color:{color};font-weight:700">{pct:.2f}%</span> of {row["total"]:,} work orders',unsafe_allow_html=True)
    rfqs=open_rfqs(directory,manifest)
    st.markdown('**Open RFQs by procurement staff**')
    if len(rfqs):
        counts=rfqs[ASSIGNEE].fillna('(Not assigned)').value_counts()
        for offset in range(0,len(counts),3):
            for col,(name,count) in zip(st.columns(3),list(counts.items())[offset:offset+3]):
                with col,st.container(border=True):
                    st.metric(str(name),f'{count:,}')
                    from executive import navigate
                    st.button('View open RFQs',key='rfq_staff_'+str(name),on_click=navigate,args=('RFQ',{'kind':'rfq_assignee','name':str(name)},'RFQ'))
                    pct=100*count/len(rfqs);color='#b42318' if pct>5 else '#16703d' if pct<5 else '#65758a'
                    st.markdown(f'<span style="color:{color}">{pct:.2f}%</span> of {len(rfqs):,} open RFQs',unsafe_allow_html=True)
    else:st.info('No open RFQs in this snapshot.')


def admin_panel(root):
    import streamlit as st
    st.subheader('Escalation emails')
    config=load_config(root)
    roster=st.file_uploader('Responsible parties workbook',type=['xlsx','json'],key='escalation_roster')
    if roster and st.button('Import operations and procurement recipients'):
        try:
            config=import_roster(roster,root);st.success('Recipient sheets imported privately. Sending remains at its existing setting.')
        except ValueError as exc:st.error(str(exc))
    with st.expander('Import recipient configuration'):
        st.caption('Alternative to the workbook uploader: paste the verified recipient JSON into this authenticated form.')
        pasted=st.text_area('Recipient configuration JSON',key='recipient_json_paste')
        if st.button('Import pasted recipient configuration'):
            try:
                import io
                payload=io.StringIO(pasted);payload.name='recipients.json'
                config=import_roster(payload,root)
                st.success('Recipient configuration imported privately.')
            except (ValueError,TypeError,KeyError) as exc:st.error(str(exc))
    directory,manifest=snapshot(root)
    tasks=priority_tasks(directory,manifest);rfqs=open_rfqs(directory,manifest)
    groups,issues=routes(tasks,rfqs,config)
    st.caption('Daily schedule: 08:00 Asia/Dubai. Separate operations and procurement escalation messages are grouped by To and CC so unrelated contract records are not shared. Every live message includes its Director and COO.')
    st.dataframe(pd.DataFrame([{'Email type':g['kind'],'To':g['to'],'CC':', '.join(g['cc']),'Records':len(g['records'])} for g in groups]),hide_index=True,width='stretch')
    if issues:
        st.warning(f'{len(issues):,} records have missing or conflicting routing and will not be emailed until corrected.')
        st.dataframe(pd.DataFrame(issues),hide_index=True,width='stretch')
    with st.expander('Edit responsible parties and email addresses'):
        operations=st.data_editor(pd.DataFrame(config.get('operations',[])),num_rows='dynamic',key='operations_recipient_editor',width='stretch')
        procurement=st.data_editor(pd.DataFrame(config.get('procurement',[])),num_rows='dynamic',key='procurement_recipient_editor',width='stretch')
        if st.button('Save recipient corrections'):
            for section,frame in [('operations',operations),('procurement',procurement)]:
                rows=frame.fillna('').to_dict('records')
                for row in rows:
                    for field in ['email','director','coo','operations_email','procurement_manager']:
                        if field in row:row[field]=email(row[field])
                config[section]=rows
            save_config(root,config);st.success('Recipient corrections saved. Reopen this page to refresh the routing preview.')
    smtp=config.get('smtp',{})
    with st.form('escalation_smtp'):
        host=st.text_input('SMTP host',value=smtp.get('host',''))
        port=st.text_input('SMTP TLS port',value=str(smtp.get('port','587')))
        sender=st.text_input('Sender email',value=smtp.get('sender',''))
        username=st.text_input('SMTP username',value=smtp.get('username',''))
        password=st.text_input('SMTP password / app password (blank keeps existing)',type='password')
        test_email=st.text_input('Test recipient email (tests go only here)',value=config.get('test_email',''))
        enabled=st.checkbox('Enable daily 8 AM escalations',value=config.get('enabled',False))
        if st.form_submit_button('Save email settings'):
            candidate=dict(config,smtp={'host':host.strip(),'port':port.strip(),'sender':email(sender),'username':username.strip(),'password':password or smtp.get('password','')},test_email=email(test_email),enabled=enabled)
            ready=smtp_settings(candidate)
            if enabled and (not groups or any(not ready[k] for k in ['host','username','password','sender'])):
                st.error('Daily sending was not enabled. Resolve recipient routing and configure the authenticated SMTP sender first.')
            elif port not in {'465','587'}:
                st.error('Use encrypted SMTP port 465 or 587.')
            else:save_config(root,candidate);config=candidate;st.success('Email settings saved. Daily time: 08:00 Dubai.')
    if st.button('Send test escalation to test recipient only'):
        try:
            count=send_groups(groups,config,source_dates(manifest),test=True)
            st.success(f'{count} test email(s) sent to the test recipient only. No production CC recipients were used.')
        except Exception as exc:st.error(str(exc))
    if st.button('Send live escalation emails now'):
        try:
            count=send_groups(groups,config,source_dates(manifest))
            st.success(f'{count} escalation email(s) accepted by the SMTP server.')
        except Exception as exc:st.error(str(exc))
    report=Path(root)/'escalation_last_run.json'
    if report.exists():st.json(json.loads(report.read_text()))
