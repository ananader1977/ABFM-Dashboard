"""Priority queues, verified recipient routing and daily escalation delivery."""
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import socket
from contextlib import contextmanager
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from mail_reports import display_name
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
    cols=['WO No.','Case Number (Case) (Case)','Task Id','Work Order Type','Priority','System Status','Status','Created On','Service Account','Building','Category','Assigned Resource',MANAGER]
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
    counts={};totals={};aliases={}
    cols=['Work Order Type','Status',MANAGER]
    for frame in scan(directory,manifest,'Reactive',cols):
        if not set(cols)<=set(frame):continue
        frame=frame.loc[frame['Work Order Type'].isin(['Reactive','Corrective','Pro-active'])].copy()
        frame['_manager_variant']=frame[MANAGER].fillna('(Not assigned)').replace('','(Not assigned)')
        frame[MANAGER]=frame['_manager_variant'].map(lambda name: 'Rafik Jabbour' if key(name).lstrip('#').strip()=='rafik jabbour' else display_name(name))
        for (name,kind),group in frame.groupby([MANAGER,'Work Order Type'],dropna=False):
            aliases.setdefault((name,kind),set()).update(group['_manager_variant'].unique())
            totals[name]=totals.get(name,0)+len(group)
            counts[(name,kind)]=counts.get((name,kind),0)+int(normalized(group.Status).isin(OPEN).sum())
    rows=[{'name':name,'aliases':{k:sorted(aliases.get((name,k),set())) for k in ['Reactive','Corrective','Pro-active']},'Reactive':counts.get((name,'Reactive'),0),'Corrective':counts.get((name,'Corrective'),0),
             'Pro-active':counts.get((name,'Pro-active'),0),'total':totals[name],
             'open':sum(counts.get((name,k),0) for k in ['Reactive','Corrective','Pro-active'])} for name in totals]
    return sorted(rows,key=lambda row:(row['name']=='(Not assigned)',-row['open'],key(row['name'])))


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


def routes(tasks, rfqs, config, expired_contracts=None):
    """One operations email per director; one procurement email for all staff."""
    groups={};issues=[]
    operations=config.get('operations',[]);staff=config.get('procurement',[])
    def issue(kind,record,owner,reason):
        issues.append({'Type':kind,'Record':record,'Responsible':owner,'Issue':reason})
    def add_person(group,name,address,record):
        people=group.setdefault('_people',{})
        person=people.setdefault(address,{'name':display_name(name),'email':address,'records':[]})
        person['records'].append(record);group['records'].append(record)
    for _,row in tasks.iterrows():
        manager=text(row.get(MANAGER))
        dest,error=unique_lookup(operations,'name',manager,['email','director','coo'])
        other={r['operations_email'] for r in staff if key(r.get('approver'))==key(manager) and r.get('operations_email')}
        if dest and other and dest['email'] not in other:dest,error=None,'Operations and procurement sheets disagree on manager email'
        if not dest or any(not email(dest[c]) for c in ['email','director','coo']):
            issue('Operations',text(row.get('WO No.',row.get('Task Id'))),manager,error or 'Invalid recipient address');continue
        director=email(dest['director'])
        group=groups.setdefault(('Operations',director),{'kind':'Operations','director':director,'_to':set(),'_cc':{director},'records':[]})
        group['_to'].add(email(dest['email']));group['_cc'].add(email(dest['coo']))
        add_person(group,manager,email(dest['email']),row.to_dict())
    # Include the department's verified roster, including staff with no matching task.
    for group in groups.values():
        for person in operations:
            if email(person.get('director'))!=group['director']:continue
            dest,error=unique_lookup(operations,'name',person.get('name'),['email','director','coo'])
            if dest and all(email(dest[c]) for c in ['email','director','coo']):
                group['_to'].add(email(dest['email']));group['_cc'].add(email(dest['coo']))
    managers={email(r.get('procurement_manager')) for r in staff if email(r.get('procurement_manager'))}
    leadership={email(address) for address in config.get('procurement_leadership_cc',[]) if email(address)}
    procurement={'kind':'Procurement','_to':set(),'_cc':managers | leadership,'records':[]}
    if len(managers)==1:
        for person in staff:
            if not text(person.get('assignee')):continue
            dest,error=unique_lookup(staff,'assignee',person['assignee'],['email'])
            if dest and email(dest['email']):procurement['_to'].add(email(dest['email']))
    for _,row in rfqs.iterrows():
        assignee=text(row.get(ASSIGNEE));dest,error=unique_lookup(staff,'assignee',assignee,['email'])
        if not dest or not email(dest.get('email')) or len(managers)!=1:
            issue('Procurement',text(row.get('Reference Number')),assignee,error or 'Missing or ambiguous procurement recipient/manager');continue
        add_person(procurement,assignee,email(dest['email']),row.to_dict())
    if expired_contracts:
        dest,error=unique_lookup(staff,'assignee','Ovini',['email'])
        if dest and email(dest.get('email')) and len(managers)==1:
            procurement['expired_contracts']=expired_contracts
        else:issue('Procurement','Expired subcontractor agreements','Ovini',error or 'Missing or ambiguous procurement recipient/manager')
    if procurement['records'] or procurement.get('expired_contracts'):groups[('Procurement','all')]=procurement
    result=[]
    for _,group in sorted(groups.items()):
        if group['kind']=='Operations':
            group['history_owners']=sorted({display_name(text(p.get('name'))) or '(Not assigned)' for p in operations if email(p.get('director'))==group['director']})
        else:
            group['history_owners']=sorted({display_name(text(p.get('assignee'))) or '(Not assigned)' for p in staff if email(p.get('email')) in group['_to']})
        cc=group.pop('_cc');recipients=group.pop('_to')
        # Keep an owning director in To once; do not repeat the address in CC.
        group['to']=', '.join(sorted(recipients));group['cc']=sorted(cc-recipients)
        group['individuals']=sorted(group.pop('_people',{}).values(),key=lambda p:(key(p['name']),p['email']))
        result.append(group)
    return result,issues


def expired_agreements(directory,manifest):
    from analytics import dates,today
    info=manifest.get('tables',{}).get('Subcontractor AMC')
    if not info:return []
    frames=[]
    for frame in scan(directory,manifest,'Subcontractor AMC',info['columns']):
        if 'End Date ' not in frame:continue
        end=dates(frame['End Date ']);part=frame.loc[end.lt(today())].copy()
        if len(part):
            part['Days expired']=(today()-end.loc[part.index].dt.normalize()).dt.days
            frames.append(part)
    return pd.concat(frames,ignore_index=True).to_dict('records') if frames else []


def email_summary(kind,frame):
    """Only summarize records already authorized for this recipient group."""
    def names(column):
        values=frame[column] if column in frame else pd.Series('(Not assigned)',index=frame.index)
        return values.map(lambda value: display_name(text(value)) or '(Not assigned)')
    if kind=='Operations':
        work=frame.copy();work['Manager']=names(MANAGER).map(lambda v:'Rafik Jabbour' if key(v).lstrip('#').strip()=='rafik jabbour' else v)
        if 'Work Order Type' not in work:work['Work Order Type']='Other'
        summary=pd.crosstab(work['Manager'],work['Work Order Type']).reindex(columns=['Reactive','PPM','Corrective','Pro-active'],fill_value=0)
        summary['Total open']=summary.sum(axis=1);summary=summary.sort_values('Total open',ascending=False).reset_index()
    else:
        work=pd.DataFrame({'Procurement staff':names(ASSIGNEE),'Operations manager':names('Approved by (Operation)')})
        summary=work.groupby(['Procurement staff','Operations manager'],dropna=False).size().reset_index(name='Open RFQs').sort_values('Open RFQs',ascending=False)
    return summary


def email_summary_html(summary):
    """Email-safe table with identical header and cell alignment in each column."""
    numeric=[pd.api.types.is_numeric_dtype(summary[c]) for c in summary.columns]
    weights=[3 if c=='7-day open trend' else (1 if number else 2.2) for c,number in zip(summary.columns,numeric)]
    table=['<table width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;table-layout:fixed;font-family:Calibri,Arial,sans-serif;font-size:14px;line-height:20px;mso-table-lspace:0pt;mso-table-rspace:0pt;"><thead><tr>']
    for column,is_number,weight in zip(summary.columns,numeric,weights):
        align='right' if is_number else 'left'
        width=100*weight/sum(weights)
        table.append(f'<th scope="col" align="{align}" valign="middle" width="{width:g}%" bgcolor="#122843" style="width:{width:g}%;text-align:{align};vertical-align:middle;padding:10px 8px;border:1px solid #ddd5c5;border-bottom:2px solid #b3955b;background-color:#122843;color:#fffefa;font-weight:bold;overflow-wrap:break-word;">{html.escape(str(column))}</th>')
    table.append('</tr></thead><tbody>')
    for index,row in enumerate(summary.itertuples(index=False,name=None)):
        background='#fffefa' if index%2==0 else '#f0ece3'
        table.append('<tr>')
        for value,is_number in zip(row,numeric):
            align='right' if is_number else 'left'
            label=f'{int(value):,}' if is_number and pd.notna(value) else ('' if pd.isna(value) else str(value))
            table.append(f'<td align="{align}" valign="middle" bgcolor="{background}" style="text-align:{align};vertical-align:middle;padding:10px 8px;border:1px solid #ddd5c5;background-color:{background};color:#122843;overflow-wrap:break-word;">{html.escape(label).replace(chr(10),"<br/>")}</td>')
        table.append('</tr>')
    if summary.empty:
        table.append(f'<tr><td colspan="{len(summary.columns)}" align="left" style="text-align:left;padding:10px 8px;border:1px solid #ddd5c5;">No open tasks in this email.</td></tr>')
    table.append('</tbody></table>')
    return ''.join(table)


def build_email(group,sender,source_date,test_to=None):
    from interactive import excel_bytes
    from mail_reports import pdf_bytes
    sent_at=datetime.now(TZ).replace(microsecond=0);deadline=sent_at+timedelta(hours=24)
    msg=EmailMessage();msg['From']=sender;msg['To']=test_to or group['to'];msg['Date']=format_datetime(sent_at)
    if not test_to and group['cc']:msg['Cc']=', '.join(group['cc'])
    record_label='open records' if group['kind']=='Operations' else 'open RFQs'
    contract_subject=f" · {len(group['expired_contracts'])} expired agreements" if group.get('expired_contracts') else ''
    msg['Subject']=('[TEST] ' if test_to else '')+f"ABFM {group['kind']} escalation · {len(group['records'])} {record_label}{contract_subject} · {sent_at:%d %b %Y}"
    columns=['WO No.','Work Order Type','Priority','System Status','Created On','Age (days)','Service Account','Building',MANAGER] if group['kind']=='Operations' else ['Reference Number','Status','Priority:','Quotation Request Date:','Project Details:','Approved by (Operation)',ASSIGNEE]
    full=pd.DataFrame(group['records']) if group['records'] else pd.DataFrame(columns=columns);frame=full[[c for c in columns if c in full]]
    summary=email_summary(group['kind'],full)
    from task_history import trend_summary,TREND_NOTE
    summary=trend_summary(summary,group)
    expired=group.get('expired_contracts',[])
    extra=f'{len(expired):,} expired subcontractor agreements assigned to Ovini are listed in a separate Excel attachment. Please confirm renewal or the recovery plan within the same deadline.' if expired else ''
    intro=('Please address the attached open P1/P2 tasks opened more than seven days ago.' if group['kind']=='Operations' else 'Please address the attached procurement items and confirm the quotation or contract-renewal status.')
    action='Within 24 hours of this email, confirm closure or provide the responsible owner, action plan and expected completion date. If blocked, reply by the deadline with the blocker and recovery plan.'
    due=f"Response due: {deadline:%d %b %Y, %H:%M} Dubai (UTC+4). Email date: {sent_at:%d %b %Y, %H:%M} Dubai (UTC+4)."
    note=f"Source snapshot: {source_date}. Imported snapshot; closure and new-work updates require a new import."
    if group.get('history'):note+=' '+TREND_NOTE
    msg.set_content('\n\n'.join([intro,action,due,summary.to_string(index=False),extra,f"{len(full):,} records. Separate individual Excel files contain the complete source data; one combined PDF contains the summary and action register.",note]))
    msg.add_alternative('<div style="font-family:Calibri,Arial,sans-serif;color:#122843;background:#fffefa;padding:24px;border-top:4px solid #b3955b"><h2>AL BONIAN FM | '+html.escape(group['kind'])+' escalation</h2><p>'+html.escape(intro)+'</p><p>'+html.escape(action)+'</p><p><strong>'+html.escape(due)+'</strong></p><h3>Open task breakdown</h3>'+email_summary_html(summary)+('<p>'+html.escape(extra)+'</p>' if extra else '')+'<p>'+str(len(full))+' records. Please review the separate individual Excel files and combined PDF summary and action register.</p><p style="font-size:12px">'+html.escape(note)+'</p></div>',subtype='html')
    stem=f"ABFM_{group['kind']}_Escalation_{sent_at:%Y%m%d}"
    individuals=group.get('individuals')
    if individuals is None:
        field=MANAGER if group['kind']=='Operations' else ASSIGNEE
        individuals=[{'name':display_name(text(name)) or 'Not assigned','records':part.to_dict('records')} for name,part in full.groupby(field,dropna=False)] if field in full else [{'name':group['kind'],'records':group['records']}]
    seen=set()
    for person in individuals:
        records=pd.DataFrame(person['records'])
        if records.empty:continue
        if group['kind']=='Operations':
            leading=['Case Number (Case) (Case)','Task Id','WO No.']
            for column in leading:
                if column not in records:records[column]=None
            records=records[leading+[column for column in records if column not in leading]]
            records=records.rename(columns=dict(zip(leading,['Case number','Task ID','WO No'])))
        records['Email sent (Dubai)']=sent_at.isoformat();records['Response due (Dubai)']=deadline.isoformat()
        name=re.sub(r'[^A-Za-z0-9_-]+','_',person['name']).strip('_') or 'Staff'
        filename=f'{stem}_{name}.xlsx'
        if filename in seen:filename=f'{stem}_{name}_{hashlib.sha256(person.get("email",str(len(seen))).encode()).hexdigest()[:8]}.xlsx'
        seen.add(filename)
        msg.add_attachment(excel_bytes(list(records.columns),records.itertuples(index=False,name=None)),maintype='application',subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename=filename)
    msg.add_attachment(pdf_bytes(group['kind'],frame,sent_at,deadline,source_date,summary=summary,expired_contracts=expired),maintype='application',subtype='pdf',filename=stem+'.pdf')
    if expired:
        contracts=pd.DataFrame(expired);contracts['Email sent (Dubai)']=sent_at.isoformat();contracts['Response due (Dubai)']=deadline.isoformat()
        msg.add_attachment(excel_bytes(list(contracts.columns),contracts.itertuples(index=False,name=None)),maintype='application',subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename=f'ABFM_Ovini_Expired_Contracts_{sent_at:%Y%m%d}.xlsx')
    return msg


def smtp_settings(config):
    settings = {name:os.environ.get('ABFM_SMTP_'+name.upper()) or config.get('smtp',{}).get(name,'') for name in ['host','port','username','password','sender']}
    for field in ['host','port','username','sender']:
        settings[field]=str(settings[field]).strip()
    if settings['host'].lower()=='smtp.gmail.com':
        settings['password']=''.join(str(settings['password']).split())
    return settings


def sender_status(config):
    smtp=smtp_settings(config)
    return {'sender':email(smtp['sender']),'host':smtp['host'],'port':smtp['port'] or '587',
            'password_saved':bool(smtp['password']),
            'missing':[k for k in ['host','username','password','sender'] if not smtp[k]]}


def validate_sender(config):
    settings=smtp_settings(config)
    missing=sender_status(config)['missing']
    if missing:raise ValueError('Email settings are incomplete: '+', '.join(missing)+'. Enter the Gmail app password and use Save settings or a send button.')
    if not email(settings['sender']):raise ValueError('Enter a valid sender email address.')
    if str(settings['port'] or '587') not in {'465','587'}:raise ValueError('Use encrypted SMTP port 465 or 587.')
    return settings


class SMTPStageError(ValueError):
    def __init__(self,stage,exc):
        self.stage=stage
        detail=('The server closed the connection or did not send an SMTP greeting in time.' if isinstance(exc,smtplib.SMTPServerDisconnected) else email_error(exc))
        super().__init__('Email connection failed during '+stage+'. '+detail)


@contextmanager
def smtp_connection(config):
    settings=validate_sender(config)
    port=int(settings['port'] or 587)
    client=None
    stage='server connection / greeting'
    try:
        client=smtplib.SMTP_SSL(settings['host'],port,timeout=30,context=ssl.create_default_context(),local_hostname='abfm-dashboard.onrender.com') if port==465 else smtplib.SMTP(settings['host'],port,timeout=30,local_hostname='abfm-dashboard.onrender.com')
        if port!=465:
            stage='encrypted TLS setup'
            client.starttls(context=ssl.create_default_context())
        stage='post-TLS SMTP greeting'
        client.ehlo()
        stage='Gmail authentication'
        mechanisms=client.esmtp_features.get('auth','').upper().split()
        if 'LOGIN' in mechanisms:
            responses=iter([settings['username'],settings['password']])
            def login_response(challenge=None):
                try:return next(responses)
                except StopIteration:raise smtplib.SMTPException('Unexpected extra authentication challenge') from None
            client.auth('LOGIN',login_response,initial_response_ok=False)
        else:
            client.login(settings['username'],settings['password'],initial_response_ok=False)
    except Exception as exc:
        if client:client.close()
        LOG.error('Escalation SMTP stage failed: %s (%s)',stage,type(exc).__name__)
        raise SMTPStageError(stage,exc) from None
    try:
        yield client
    finally:
        try:client.quit()
        except Exception:client.close()


def email_error(exc):
    if isinstance(exc,smtplib.SMTPAuthenticationError):
        return 'Gmail rejected authentication. Use a Gmail app password for the selected sender, not the normal account password. Save the settings and check the connection again.'
    if isinstance(exc,smtplib.SMTPRecipientsRefused):return 'The email server rejected one or more recipients. Check the displayed To and CC addresses.'
    if isinstance(exc,smtplib.SMTPException):return 'The email server rejected the request. Check sender permissions and sending limits.'
    if isinstance(exc,(TimeoutError,socket.timeout,ConnectionError,OSError)):
        return 'The SMTP connection failed or timed out. Check the host and TLS port; try 465 if 587 cannot connect. No automatic resend will be attempted.'
    if isinstance(exc,ValueError):return str(exc)
    return 'Email delivery failed ('+type(exc).__name__+'). Check the saved email settings.'


def check_connection(config):
    try:
        with smtp_connection(config):pass
    except Exception as exc:
        LOG.error('Escalation connection check failed: %s',type(exc).__name__)
        raise ValueError(email_error(exc)) from None
    return True


class DeliveryFailure(ValueError):
    def __init__(self,accepted,total,reason):
        self.accepted=accepted;self.total=total
        super().__init__(f'{accepted} of {total} email(s) accepted by the server. '+reason+(' Some messages were accepted; do not resend the full live batch without checking recipients.' if accepted else ''))


def send_groups(groups,config,source_date,test=False):
    smtp=validate_sender(config)
    if not groups:raise ValueError('No routable records are available. Check the recipient preview and unresolved routing rows.')
    test_to=config.get('test_email','')
    if test and not email(test_to):raise ValueError('Set the test recipient email first.')
    messages=[build_email(g,smtp['sender'],source_date.get(g['kind'],'Unknown') if isinstance(source_date,dict) else source_date,test_to if test else None) for g in groups]
    accepted=0
    try:
        with smtp_connection(config) as client:
            for message in messages:
                refused=client.send_message(message)
                if refused:raise ValueError('The server refused one or more recipients; review delivery before retrying.')
                accepted+=1
    except Exception as exc:
        LOG.error('Escalation delivery failed after %s of %s messages: %s',accepted,len(messages),type(exc).__name__)
        raise DeliveryFailure(accepted,len(messages),email_error(exc)) from None
    return accepted


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
    groups,issues=routes(tasks,rfqs,config,expired_agreements(directory,manifest))
    from task_history import record_daily,attach_history
    try:
        record_daily(root,directory,manifest,tasks,rfqs,now)
        attach_history(groups,root,now)
    except Exception:
        LOG.exception('Daily trend history unavailable; escalation delivery will continue without historical figures.')
    smtp=smtp_settings(config)
    ledger=sqlite3.connect(Path(root)/'escalation_delivery.sqlite',timeout=20)
    ledger.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY,status TEXT,created TEXT)')
    try:
        for group in groups:
            delivery_id=hashlib.sha256(json.dumps([str(now.date()),'department-batch-v1',group['kind'],group.get('director','all')]).encode()).hexdigest()
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
        try:
            startup_config=load_config(root)
            if sender_status(startup_config)['password_saved']:
                check_connection(startup_config)
                LOG.warning('Escalation startup sender check: authentication succeeded; no email sent.')
        except Exception as exc:
            LOG.error('Escalation startup sender check: %s',email_error(exc))
        while True:
            try:
                from task_history import capture_daily
                capture_daily(root)
            except Exception:LOG.exception('Daily trend capture failed; prior observations are retained.')
            try:run_daily(root)
            except Exception:LOG.exception('Escalation scheduler failed; no delivery assumed successful.')
            time.sleep(30)
    threading.Thread(target=loop,name='abfm-daily-escalation',daemon=True).start()


def executive_panels(directory,manifest):
    import streamlit as st
    from interactive import excel_bytes
    from task_history import executive_trend
    executive_trend(Path(directory).parent)
    tasks=priority_tasks(directory,manifest)
    st.subheader(f'Open P1 / P2 tasks older than seven days · {len(tasks):,} open tasks')
    st.caption('All four work-order types. Scheduled, In Progress and Unscheduled are open; completed and cancelled tasks are excluded. Age is measured from Created On in Dubai time, strictly greater than seven days.')
    display=tasks.drop(columns=['Status'],errors='ignore')
    from mail_reports import DISPLAY_NAMES
    st.dataframe(display.replace(DISPLAY_NAMES),hide_index=True,width='stretch')
    st.caption(f'{len(tasks):,} matching tasks · snapshot data, recalculated against the current time.')
    st.download_button('Download aged P1/P2 tasks · Excel',data=lambda:excel_bytes(list(display.columns),display.itertuples(index=False,name=None)),file_name='ABFM_Aged_P1_P2.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',on_click='ignore')


def agreement_stats(directory,manifest):
    from analytics import dates, today
    total=expired=0
    for frame in scan(directory,manifest,'Subcontractor AMC',['End Date ']):
        total+=len(frame)
        if 'End Date ' in frame:expired+=int(dates(frame['End Date ']).lt(today()).sum())
    return {'total':total,'expired':expired,'percentage':100*expired/total if total else 0}


def manager_comparison(directory,manifest):
    from task_history import import_observations
    current=manager_counts(directory,manifest)
    stamp=manifest.get('work_order_source',{}).get('imported_at')
    earlier=[o for o in import_observations(Path(directory).parent) if stamp and pd.to_datetime(o['stamp'],utc=True)<pd.to_datetime(stamp,utc=True)]
    previous={};previous_stamp=None
    if earlier:
        baseline=earlier[-1];previous_stamp=baseline['stamp']
        old=Path(directory).parent/baseline['directory']
        previous={r['name']:r for r in manager_counts(old,json.loads((old/'manifest.json').read_text()))}
    rows={r['name']:r for r in current}
    for name,old in previous.items():
        if name not in rows:
            rows[name]={'name':name,'open':0,'total':0,'Reactive':0,'Corrective':0,'Pro-active':0,'aliases':{k:[] for k in ['Reactive','Corrective','Pro-active']}}
    for row in rows.values():
        row['previous_open']=previous.get(row['name'],{}).get('open',0) if previous_stamp else None
        row['change']=row['open']-row['previous_open'] if previous_stamp else None
    visible=[r for r in rows.values() if r['open']>0 or (r['previous_open'] or 0)>0]
    return sorted(visible,key=lambda r:(r['name']=='(Not assigned)',-r['open'],key(r['name']))),previous_stamp


def review_cards(directory,manifest):
    import streamlit as st
    from visuals import styled_metric, status_style
    rows,previous_stamp=manager_comparison(directory,manifest)
    if previous_stamp:
        previous_date=pd.to_datetime(previous_stamp,utc=True).tz_convert(TZ).strftime('%d %b %Y, %H:%M Dubai')
        current_date=pd.to_datetime(manifest['work_order_source']['imported_at'],utc=True).tz_convert(TZ).strftime('%d %b %Y, %H:%M Dubai')
        st.caption(f'Manager comparison · Previous: {previous_date} · Current: {current_date}. Reactive, Corrective and Pro-active open tasks; PPM is excluded from these cards. Changes include new tasks and reassignment, not just closures.')
    def open_manager(name,kind,variants):
        from executive import navigate
        st.session_state['quick_'+kind+'_Contract Manager (Contract)'] = list(dict.fromkeys('(Not specified)' if variant=='(Not assigned)' else display_name(variant) for variant in variants))
        navigate(kind,'wo_open','Reactive')
    for offset in range(0,len(rows),4):
        for col,row in zip(st.columns(4),rows[offset:offset+4]):
            with col,st.container(border=True,key='compact_review_manager_'+hashlib.sha256(row['name'].encode()).hexdigest()[:12]):
                st.markdown('**'+display_name(row['name'])+'**')
                pct=100*row['open']/row['total'] if row['total'] else 0
                styled_metric(st, 'Current open · Reactive / Corrective / Pro-active', f"{row['open']:,}", percentage=pct, key='manager_'+hashlib.sha256(row['name'].encode()).hexdigest()[:12])
                previous='—' if row['previous_open'] is None else f"{row['previous_open']:,}"
                change='—' if row['change'] is None else f"{row['change']:+,}"
                delta_color='#16703d' if row['change'] is not None and row['change']<0 else '#b42318' if row['change'] is not None and row['change']>0 else '#122843'
                st.markdown(f'<div style="font-size:.85rem;margin-bottom:.5rem">Previous open: <strong>{previous}</strong> · Change: <strong style="color:{delta_color}">{change}</strong></div>',unsafe_allow_html=True)
                with st.container(key='task_links_'+hashlib.sha256(row['name'].encode()).hexdigest()[:12]):
                    for link,kind in zip(st.columns([1,1,1.15],gap='small'),['Reactive','Corrective','Pro-active']):
                        with link:
                            st.button(f'{kind}: {row[kind]:,}',key='manager_'+row['name']+'_'+kind,type='tertiary',disabled=not row[kind],on_click=open_manager,args=(row['name'],kind,row['aliases'][kind]))
                pct=100*row['open']/row['total'] if row['total'] else 0
                color, weight, _ = status_style('Open',pct)
                st.markdown(f'<span style="color:{color};font-weight:{weight}">{pct:.2f}%</span> of {row["total"]:,} work orders',unsafe_allow_html=True)
    rfqs=open_rfqs(directory,manifest)
    st.markdown('**Open RFQs by procurement staff**')
    stats=agreement_stats(directory,manifest)
    if len(rfqs) or stats['total']:
        counts=rfqs[ASSIGNEE].fillna('(Not assigned)').value_counts()
        if stats['total'] and not any('ovini' in key(n) for n in counts.index):counts.loc['Ovini']=0
        for offset in range(0,len(counts),4):
            for col,(name,count) in zip(st.columns(4),list(counts.items())[offset:offset+4]):
                with col,st.container(border=True,key='compact_review_staff_'+hashlib.sha256(str(name).encode()).hexdigest()[:12]):
                    pct=100*count/len(rfqs) if len(rfqs) else 0
                    styled_metric(st, 'Open RFQs · '+str(name), f'{count:,}', percentage=pct, key='rfq_staff_'+hashlib.sha256(str(name).encode()).hexdigest()[:12])
                    from executive import navigate
                    st.button('View open RFQs',key='rfq_staff_'+str(name),on_click=navigate,args=('RFQ',{'kind':'rfq_assignee','name':str(name)},'RFQ'))
                    color, weight, _ = status_style('Open',pct)
                    st.markdown(f'<span style="color:{color};font-weight:{weight}">{pct:.2f}%</span> of {len(rfqs):,} open RFQs',unsafe_allow_html=True)
                    if 'ovini' in key(name):
                        st.markdown(f"**All subcontractor agreements: {stats['total']:,}**")
                        styled_metric(st,'Expired agreements',f"{stats['expired']:,} ({stats['percentage']:.2f}%)",percentage=stats['percentage'],key='ovini_expired')
                        st.caption(f"Not expired: {stats['total']-stats['expired']:,}")
                        st.caption('Expired % of all agreements · vendor agreement End Date before today (Dubai).')
                        st.button('View expired agreements',key='ovini_expired_view',on_click=navigate,args=('SubContractors Agrements','agreements_expired','Subcontractor AMC'))

    else:st.info('No open RFQs in this snapshot.')


def admin_panel(root):
    import streamlit as st
    st.subheader('Escalation emails')
    config=load_config(root)
    with st.form('procurement_leadership'):
        leadership_cc=st.text_input('Procurement leadership CC',value=', '.join(config.get('procurement_leadership_cc',[])),help='Verified directors and COO addresses, separated by commas. The procurement manager is also copied automatically.')
        save_leadership=st.form_submit_button('Save procurement leadership CC')
    if save_leadership:
        addresses=[part.strip() for part in leadership_cc.split(',') if part.strip()]
        if any(not email(address) for address in addresses):
            st.error('Enter valid email addresses separated by commas.')
        else:
            config['procurement_leadership_cc']=sorted({email(address) for address in addresses})
            save_config(root,config)
            st.success('Procurement leadership CC saved for daily and manual live procurement emails. Test emails still go only to the test recipient.')
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
    groups,issues=routes(tasks,rfqs,config,expired_agreements(directory,manifest))
    from task_history import attach_history
    attach_history(groups,root)
    st.caption('Daily schedule: 08:00 Asia/Dubai. One operations email per director, CC that director and COO; Rafik remains separate. One procurement email to all verified procurement staff, CC the procurement manager and saved leadership CC addresses. Each email has individual Excel files and one combined PDF. Test sends mirror this grouping and go only to the test recipient.')
    st.dataframe(pd.DataFrame([{'Email type':g['kind'],'To':g['to'],'CC':', '.join(g['cc']),'Individual Excel files':len(g.get('individuals',[])),'Records':len(g['records']),'Expired agreements':len(g.get('expired_contracts',[]))} for g in groups]),hide_index=True,width='stretch')
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
    status_slot=st.empty()
    missing_slot=st.empty()
    with st.form('escalation_smtp'):
        host=st.text_input('SMTP host',value=smtp.get('host',''))
        port=st.text_input('SMTP TLS port',value=str(smtp.get('port','587')))
        sender=st.text_input('Sender email',value=smtp.get('sender',''))
        username=st.text_input('SMTP username',value=smtp.get('username',''))
        password=st.text_input('SMTP password / app password (blank keeps existing)',type='password')
        test_email=st.text_input('Test recipient email (tests go only here)',value=config.get('test_email',''))
        enabled=st.checkbox('Enable daily 8 AM escalations',value=config.get('enabled',False))
        save_clicked=st.form_submit_button('Save email settings')
        check_clicked=st.form_submit_button('Save & check email connection')
        test_clicked=st.form_submit_button('Send test escalation to test recipient only')
        live_clicked=st.form_submit_button('Send live escalation emails now')
    if any([save_clicked,check_clicked,test_clicked,live_clicked]):
        candidate=dict(config,smtp={'host':host.strip(),'port':port.strip(),'sender':email(sender),'username':username.strip(),'password':password or smtp.get('password','')},test_email=email(test_email),enabled=enabled)
        try:
            if port not in {'465','587'}:raise ValueError('Use encrypted SMTP port 465 or 587.')
            if enabled or check_clicked or test_clicked or live_clicked:validate_sender(candidate)
            if enabled and not groups:raise ValueError('No routable records are configured; daily sending was not enabled.')
            if enabled:
                with st.spinner('Validating sender before enabling daily emails…'):
                    check_connection(candidate)
            save_config(root,candidate);config=candidate
            if check_clicked:
                with st.spinner('Checking encrypted connection and Gmail authentication…'):
                    check_connection(config)
                st.success('Connection and authentication succeeded. No email was sent.')
            elif test_clicked or live_clicked:
                with st.spinner('Sending test emails…' if test_clicked else 'Sending the scoped escalation batch…'):
                    count=send_groups(groups,config,source_dates(manifest),test=test_clicked)
                st.success(f'{count} test email(s) accepted for the test recipient only; production CC recipients were not used.' if test_clicked else f'{count} live escalation email(s) accepted by the server.')
            else:st.success('Email settings saved. Daily time: 08:00 Dubai.')
        except Exception as exc:
            LOG.error('Manual escalation action failed: %s',type(exc).__name__)
            st.error(email_error(exc))
    status=sender_status(config)
    status_slot.caption('Saved sender: '+(status['sender'] or 'not configured')+' · Password saved: '+('Yes' if status['password_saved'] else 'No')+' · Daily schedule: '+('Enabled' if config.get('enabled') else 'Disabled'))
    if status['missing']:missing_slot.info('Complete the sender settings below. Test and live buttons save the form before sending. Missing: '+', '.join(status['missing']))
    report=Path(root)/'escalation_last_run.json'
    if report.exists():st.json(json.loads(report.read_text()))
