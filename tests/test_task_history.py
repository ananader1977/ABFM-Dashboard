import unittest,tempfile,json
from pathlib import Path
from datetime import datetime
import pandas as pd
from escalation import TZ,MANAGER,ASSIGNEE,priority_tasks,open_rfqs,routes,email_summary,build_email
from task_history import record_daily,attach_history,trend_summary,history_db,capture_daily

class TaskHistoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.directory=self.root/'snapshot-test';self.directory.mkdir()
 def tearDown(self):self.tmp.cleanup()
 def install(self,work,rfqs,stamp):
  self.manifest={'imported_at':stamp,'work_order_source':{'imported_at':stamp},'tables':{}}
  for table,records in [('Reactive',work),('RFQ',rfqs)]:
   frame=pd.DataFrame(records);filename=table+'.parquet';frame.to_parquet(self.directory/filename,index=False)
   self.manifest['tables'][table]={'file':filename,'columns':list(frame.columns)}
  (self.directory/'manifest.json').write_text(json.dumps(self.manifest));(self.root/'current.txt').write_text(self.directory.name)
 def wo(self,identifier,status='ACTIVE',manager='Alice'):
  return {'WO No.':str(identifier),'Work Order Type':'Reactive','Status':status,'Priority':'P1','Created On':'2026-10-01',MANAGER:manager}
 def rfq(self,identifier,status='In Progress'):
  return {'Reference Number':identifier,'Status':status,ASSIGNEE:'Buyer','Approved by (Operation)':'Alice'}
 def record(self,day):
  now=datetime(2026,11,day,8,0,tzinfo=TZ)
  tasks=priority_tasks(self.directory,self.manifest,now);rfqs=open_rfqs(self.directory,self.manifest)
  return record_daily(self.root,self.directory,self.manifest,tasks,rfqs,now)
 def groups(self,day):
  config={'operations':[{'name':'Alice','email':'alice@example.com','director':'director@example.com','coo':'coo@example.com'}],
   'procurement':[{'assignee':'Buyer','email':'buyer@example.com','procurement_manager':'pm@example.com'}]}
  tasks=priority_tasks(self.directory,self.manifest,datetime(2026,11,day,8,tzinfo=TZ));rfqs=open_rfqs(self.directory,self.manifest)
  groups,_=routes(tasks,rfqs,config)
  return attach_history(groups,self.root,datetime(2026,11,day,8,tzinfo=TZ))
 def test_immutable_daily_capture_next_day_cohort_and_new_tasks(self):
  self.install([self.wo(1),self.wo(2),self.wo(9,manager='Other')],[self.rfq('R1')],'2026-11-09')
  self.assertTrue(self.record(9));self.assertFalse(self.record(9))
  self.install([self.wo(1,'COMPLETE'),self.wo(2),self.wo(3)],[self.rfq('R1','Completed'),self.rfq('R2')],'2026-11-10')
  self.assertTrue(self.record(10))
  groups=self.groups(10);ops=next(g for g in groups if g['kind']=='Operations')
  self.assertEqual(len(ops['history']['dates']),5)
  summary=trend_summary(email_summary('Operations',pd.DataFrame(ops['records'])),ops)
  label=summary.iloc[0]['5-day open trend']
  self.assertIn('11-09: 2',label);self.assertIn('11-10: 2',label);self.assertIn('1/2 still open',label)
  self.assertNotIn('Other',summary.to_string());self.assertIn('11-06: —',label)
  proc=next(g for g in groups if g['kind']=='Procurement')
  self.assertEqual(proc['history']['cohorts'][('Buyer','Alice')],(1,0))
  message=build_email(ops,'sender@example.com','date')
  markup=message.get_body(preferencelist=('html',)).get_content()
  self.assertIn('5-day open trend',markup);self.assertIn('<br/>',markup)
  self.assertIn('1/2 still open',markup)
  self.assertEqual(sum(a.get_content_type()=='application/pdf' for a in message.iter_attachments()),1)
 def test_missing_day_is_not_zero_and_authorized_zero_row_is_kept(self):
  self.install([self.wo(1)],[self.rfq('R1')],'old');self.record(9)
  self.install([self.wo(1,'COMPLETE'),self.wo(2,manager='Other')],[self.rfq('R1')],'new');self.record(11)
  group={'kind':'Operations','records':[],'to':'alice@example.com','cc':[],'history_owners':['Alice']}
  attach_history([group],self.root,datetime(2026,11,11,8,tzinfo=TZ))
  summary=trend_summary(email_summary('Operations',pd.DataFrame(columns=[MANAGER,'Work Order Type'])),group)
  self.assertEqual(summary.iloc[0]['Total open'],0)
  self.assertIn('11-10: —',summary.iloc[0]['5-day open trend']);self.assertIn('11-11: 0',summary.iloc[0]['5-day open trend'])
  self.assertNotIn('Prior-day',summary.iloc[0]['5-day open trend'])
 def test_capture_before_eight_is_skipped_and_scheduler_capture_survives_reopen(self):
  self.install([self.wo(1)],[self.rfq('R1')],'2026-11-09')
  capture_daily(self.root,datetime(2026,11,9,7,59,tzinfo=TZ))
  with history_db(self.root) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM days').fetchone()[0],0)
  capture_daily(self.root,datetime(2026,11,9,8,tzinfo=TZ))
  capture_daily(self.root,datetime(2026,11,9,14,tzinfo=TZ))
  with history_db(self.root) as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM days').fetchone()[0],1)
   self.assertEqual(db.execute("SELECT n FROM counts WHERE kind='All open' AND owner='Reactive'").fetchone()[0],1)
  self.assertEqual((self.root/'task_history.sqlite').stat().st_mode & 0o777,0o600)
 def test_chart_renders_interactive_scopes(self):
  from streamlit.testing.v1 import AppTest
  self.install([self.wo(1)],[self.rfq('R1')],'2026-11-09');self.record(9)
  script=f"from task_history import executive_trend\nfrom datetime import datetime\nfrom escalation import TZ\nexecutive_trend({str(self.root)!r},datetime(2026,11,9,9,tzinfo=TZ))"
  app=AppTest.from_string(script).run();self.assertFalse(app.exception)
  app.radio[0].set_value('Daily reminders').run();self.assertFalse(app.exception)
  app.radio[1].set_value('Reminder queues').run();self.assertFalse(app.exception)
 def test_upload_comparison_recovers_old_snapshot_and_tracks_outcomes(self):
  import shutil
  from task_history import import_observations,compare_imports
  self.install([self.wo(i) for i in range(1,5)],[self.rfq('R1')],'2026-11-07T08:00:00+00:00')
  before=self.root/'snapshot-before';shutil.copytree(self.directory,before)
  self.install([self.wo(1),self.wo(2,'COMPLETE'),self.wo(3,'CANCELLED'),self.wo(5)],[self.rfq('R1')],'2026-11-09T08:00:00+00:00')
  rows=import_observations(self.root);self.assertEqual(len(rows),2)
  self.assertEqual(len(import_observations(self.root)),2)
  total=compare_imports(self.root,before.name,self.directory.name).iloc[-1]
  for column,expected in [('Previous open',4),('Latest open',2),('Change in open',-2),('Still open',1),('Now completed',1),('Now cancelled',1),('Not in latest',1),('New / reopened',1)]:self.assertEqual(total[column],expected)
  from streamlit.testing.v1 import AppTest
  script=f"from task_history import executive_trend\nfrom datetime import datetime\nfrom escalation import TZ\nexecutive_trend({str(self.root)!r},datetime(2026,11,9,14,tzinfo=TZ))"
  app=AppTest.from_string(script).run();self.assertFalse(app.exception)
  self.assertEqual(len(app.selectbox),1)
 def test_manager_cards_show_previous_current_and_cleared_managers(self):
  import shutil
  from escalation import manager_comparison
  self.install([self.wo(1),self.wo(2,manager='# Rafik Jabour')],[self.rfq('R1')],'2026-11-07T08:00:00+00:00')
  shutil.copytree(self.directory,self.root/'snapshot-before')
  self.install([self.wo(1,'COMPLETE'),self.wo(2,manager='Rafik Jabbour'),self.wo(3,manager='Rafik Jabbour')],[self.rfq('R1')],'2026-11-09T08:00:00+00:00')
  rows,stamp=manager_comparison(self.directory,self.manifest)
  self.assertEqual(stamp,'2026-11-07T08:00:00+00:00')
  rafik=next(r for r in rows if r['name']=='Rafik Jabbour');alice=next(r for r in rows if r['name']=='Alice')
  self.assertEqual((rafik['previous_open'],rafik['open'],rafik['change']),(1,2,1))
  self.assertEqual((alice['previous_open'],alice['open'],alice['change']),(1,0,-1))
