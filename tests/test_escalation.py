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
  for g in groups:
   self.assertIn('director@example.com',g['cc']);self.assertIn('coo@example.com',g['cc'])
  self.assertIn('manager@example.com',groups[1]['cc'])
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
