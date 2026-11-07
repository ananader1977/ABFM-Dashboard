import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from escalation import priority_mask,routes,build_email,send_groups,MANAGER,ASSIGNEE,email

class EscalationTests(unittest.TestCase):
 def test_exact_age_priority_and_open_status(self):
  now=pd.Timestamp('2026-11-07 08:00',tz='Asia/Dubai')
  f=pd.DataFrame({'Status':['ACTIVE']*5+['COMPLETE','CANCELLED'],'Priority':['P1 - Emergency','P2 - High','P3','P10','P1','P1','P2'],
    'Created On':['2026-10-30 07:00','2026-10-31 08:00','2026-10-01','2026-10-01','bad','2026-10-01','2026-10-01']})
  self.assertEqual(priority_mask(f,now).tolist(),[True,False,False,False,False,False,False])
 def config(self):
  return {'operations':[{'name':'Manager','email':'manager@example.com','director':'director@example.com','coo':'coo@example.com'}],
   'procurement':[{'approver':'Manager','operations_email':'manager@example.com','director':'director@example.com','coo':'coo@example.com',
   'assignee':'Buyer','email':'buyer@example.com','procurement_manager':'procmanager@example.com'}]}
 def test_scoped_routes_always_include_director_and_coo(self):
  ops=pd.DataFrame([{MANAGER:'Manager','WO No.':'1'}]);rfq=pd.DataFrame([{ASSIGNEE:'Buyer','Approved by (Operation)':'Manager','Reference Number':'R1'}])
  groups,issues=routes(ops,rfq,self.config());self.assertFalse(issues);self.assertEqual(len(groups),2)
  operations=next(g for g in groups if g['kind']=='Operations');procurement=next(g for g in groups if g['kind']=='Procurement')
  self.assertEqual(operations['cc'],['coo@example.com','director@example.com'])
  self.assertEqual(procurement['cc'],['procmanager@example.com'])
  self.assertEqual(procurement['to'],'buyer@example.com')
 def test_unmapped_or_conflicting_routes_are_blocked(self):
  c=self.config();c['procurement'][0]['operations_email']='different@example.com'
  groups,issues=routes(pd.DataFrame([{MANAGER:'Manager'}]),pd.DataFrame(),c)
  self.assertFalse(groups);self.assertEqual(len(issues),1)
 def test_test_email_never_copies_production_recipients(self):
  g={'kind':'Operations','to':'manager@example.com','cc':['director@example.com','coo@example.com'],'records':[{'WO No.':'1'}]}
  m=build_email(g,'sender@example.com','snapshot',test_to='tester@example.com')
  self.assertEqual(m['To'],'tester@example.com');self.assertIsNone(m['Cc'])
 def test_sender_required_and_email_cleaning(self):
  self.assertEqual(email('ahmed.a@albonian.fm\\xa0 '),'ahmed.a@albonian.fm')
  with self.assertRaises(ValueError):send_groups([],{},'date')

 def test_daily_schedule_deduplicates_and_skips_outside_eight_am(self):
  from escalation import run_daily,save_config,TZ
  from datetime import datetime
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);save_config(root,{'enabled':True})
   group={'kind':'Operations','to':'manager@example.com','cc':['director@example.com','coo@example.com'],'records':[{'WO No.':'1'}]}
   with patch('escalation.snapshot',return_value=(root,{'imported_at':'snapshot'})), patch('escalation.priority_tasks',return_value=pd.DataFrame()), patch('escalation.open_rfqs',return_value=pd.DataFrame()), patch('escalation.routes',return_value=([group],[])), patch('escalation.send_groups',return_value=1) as send:
    run_daily(root,datetime(2026,11,7,7,59,tzinfo=TZ));self.assertEqual(send.call_count,0)
    run_daily(root,datetime(2026,11,7,8,0,tzinfo=TZ));run_daily(root,datetime(2026,11,7,8,1,tzinfo=TZ));self.assertEqual(send.call_count,1)
 def test_manager_aliases_reconcile_counts_and_sort_unassigned_last(self):
  from escalation import manager_counts
  frame=pd.DataFrame({'Work Order Type':['Reactive']*6,'Status':['ACTIVE','ACTIVE','COMPLETE','ACTIVE','ACTIVE','ACTIVE'],
    MANAGER:['# Rafik Jabbour','Rafik Jabbour','Rafik Jabbour',None,None,'Another Manager']})
  with patch('escalation.scan',return_value=iter([frame])):
   rows=manager_counts(Path('.'),{})
  rafik=next(r for r in rows if r['name']=='Rafik Jabbour')
  self.assertEqual(rafik['open'],2);self.assertEqual(rafik['total'],3)
  self.assertEqual(rafik['aliases']['Reactive'],['# Rafik Jabbour','Rafik Jabbour'])
  self.assertEqual(rows[0]['name'],'Rafik Jabbour');self.assertEqual(rows[-1]['name'],'(Not assigned)')
  self.assertEqual(sum(r['open'] for r in rows),5)
 def test_gmail_app_password_spaces_and_empty_environment_override(self):
  from escalation import smtp_settings
  config={'smtp':{'host':'smtp.gmail.com','username':'sender@example.com','password':'aaaa bbbb cccc dddd','sender':'sender@example.com','port':'587'}}
  with patch.dict('os.environ',{'ABFM_SMTP_PASSWORD':''}):
   self.assertEqual(smtp_settings(config)['password'],'aaaabbbbccccdddd')
 def test_smtp_check_sends_nothing_and_partial_failure_is_explicit(self):
  from escalation import check_connection,DeliveryFailure
  config={'smtp':{'host':'smtp.gmail.com','username':'sender@example.com','password':'synthetic','sender':'sender@example.com','port':'587'}}
  with patch('escalation.smtplib.SMTP') as smtp:
   client=smtp.return_value
   self.assertTrue(check_connection(config));client.send_message.assert_not_called()
   groups=[{'kind':'Operations','to':'manager@example.com','cc':['director@example.com','coo@example.com'],'records':[{'WO No.':'1'}]},
     {'kind':'Procurement','to':'buyer@example.com','cc':['director@example.com','coo@example.com'],'records':[{'Reference Number':'R1'}]}]
   client.send_message.side_effect=[{},TimeoutError('network')]
   with self.assertRaises(DeliveryFailure) as error:send_groups(groups,config,'snapshot')
   self.assertEqual(error.exception.accepted,1);self.assertIn('do not resend',str(error.exception))

 def test_four_department_messages_preserve_people_files_and_record_totals(self):
  from io import BytesIO
  from zipfile import ZipFile
  from xml.etree import ElementTree as ET
  from unittest.mock import MagicMock
  config={'operations':[
    {'name':'Alice','email':'alice@example.com','director':'d1@example.com','coo':'coo@example.com'},
    {'name':'Bob','email':'bob@example.com','director':'d1@example.com','coo':'coo@example.com'},
    {'name':'Carol','email':'carol@example.com','director':'d2@example.com','coo':'coo@example.com'},
    {'name':'Rafik','email':'rafik@example.com','director':'rafik@example.com','coo':'coo@example.com'}],
    'procurement':[{'assignee':n,'email':n.lower()+'@example.com','procurement_manager':'pm@example.com'} for n in ['Buyer','Ovini']]}
  tasks=pd.DataFrame([{MANAGER:n,'WO No.':str(100+i),'Work Order Type':'Reactive','Priority':'P1','System Status':'In Progress','Created On':'2026-10-01'} for i,n in enumerate(['Alice','Alice','Bob','Carol','Rafik'])])
  rfqs=pd.DataFrame([{ASSIGNEE:n,'Reference Number':'R'+str(i),'Status':'In progress','Approved by (Operation)':'Unmapped approver'} for i,n in enumerate(['Buyer','Ovini','Buyer'])])
  groups,issues=routes(tasks,rfqs,config,[{'Vendor Name':'Vendor A','End Date ':'2026-01-01'}])
  self.assertFalse(issues);self.assertEqual(len(groups),4)
  self.assertEqual(sum(len(g['records']) for g in groups),8)
  procurement=next(g for g in groups if g['kind']=='Procurement')
  self.assertEqual(set(procurement['to'].split(', ')),{'buyer@example.com','ovini@example.com'})
  self.assertEqual(procurement['cc'],['pm@example.com'])
  d1=next(g for g in groups if g.get('director')=='d1@example.com')
  self.assertEqual(set(d1['to'].split(', ')),{'alice@example.com','bob@example.com'})
  self.assertEqual(len(d1['individuals']),2)
  self.assertEqual(len(d1['records']),3)
  rafik=next(g for g in groups if g.get('director')=='rafik@example.com')
  self.assertEqual(rafik['to'],'rafik@example.com');self.assertEqual(rafik['cc'],['coo@example.com'])
  for group in groups:
   msg=build_email(group,'sender@example.com','2026-11-07')
   attachments=list(msg.iter_attachments());pdfs=[a for a in attachments if a.get_content_type()=='application/pdf']
   self.assertEqual(len(pdfs),1)
   self.assertIn('Open task breakdown',msg.get_body(preferencelist=('html',)).get_content())
   extracted=0;files=0
   for attachment in attachments:
    filename=attachment.get_filename()
    if not filename.endswith('.xlsx') or 'Expired_Contracts' in filename:continue
    files+=1
    with ZipFile(BytesIO(attachment.get_payload(decode=True))) as archive:
     root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
     extracted+=len(root.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'))-1
   self.assertEqual(extracted,len(group['records']))
   self.assertEqual(files,len(group['individuals']))
  connection=MagicMock();connection.__enter__.return_value.send_message.return_value={}
  with patch('escalation.validate_sender',return_value={'sender':'sender@example.com'}),patch('escalation.smtp_connection',return_value=connection):
   self.assertEqual(send_groups(groups,{'test_email':'test@example.com'},'snapshot',test=True),4)
  sent=connection.__enter__.return_value.send_message.call_args_list
  for call in sent:
   self.assertEqual(call.args[0]['To'],'test@example.com');self.assertIsNone(call.args[0]['Cc'])
