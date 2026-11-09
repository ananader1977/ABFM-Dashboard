import io,json,tempfile,unittest
from pathlib import Path
from openpyxl import Workbook
import pandas as pd
from mapped_import import SITE_ALIASES,MRI_HEADERS
from work_orders import install_excel_replacement
from escalation import MANAGER,snapshot

class MappedImportTests(unittest.TestCase):
 def workbook(self,unknown=False):
  headers=['Created On','WO No.','Work Order Type','System Status','Priority','Assigned Resource',MANAGER,'Service Account','Category']
  b=Workbook();s=b.active;s.title='Anas - All Work Orders';s.append(headers);s.append(['2026-11-01','M1','Reactive','Completed','P1','Tech',None,'Account','Civil'])
  m=b.create_sheet('Field Mapping');m.append(['Dynamic','DP World (MRI)','MR1 Report (Maximo)','G.V.V (Maximo)','Meydan (Maximo)'])
  source={'Created On':['Task Reported Date']+['Reported Date']*3,'WO No.':['Task ID']+['Work Order']*3,'Work Order Type':['Task Type']+['Work Type']*3,'System Status':['Status']+['Status Description']*3,'Priority':['Priority']*4,'Assigned Resource':['Resource']+['Assigned Tech. Name']*3,MANAGER:['Supervisors']+['N/A']*3,'Service Account':['Contract Title','Site','Location','Site'],'Category':['Category']+['Category Desc']*3}
  for h in headers:m.append([h]+source[h])
  for name in ['The Gardens Co.','Meydan GS']:
   s=b.create_sheet(name);s.append(['Work Order','Reported Date','Work Type','Status Description','Priority','Assigned Tech. Name','Location','Site','Category Desc','Description'])
   s.append([name+'-1','09/11/26 1:30 PM','BAD' if unknown else 'PM','Close Negatively',2,'Worker','Location','Site','Civil','Job'])
  b.create_sheet('MR1')
  s=b.create_sheet('Jebel Ali Free Zone - DP World');s.append(['TA_TASK_ID','Task Reported Date','Task Type','TA_STATUS','TA_PRIORITY','CTR_REFERENCE','TA_CATEGORY']);s.append(['D1','2026-11-09','BREAKDOWN','ASSIGNED','P2 Urgent','Contract','CIVIL'])
  s=b.create_sheet('Operation Data');s.append(['Service Account',MANAGER,'email','Director Email','COO E-mail'])
  for site in ['Account','The Gardens Co.','Meydan GS','Jebel Ali Free Zone - DP World']:s.append([site,'Manager','manager@example.com','director@example.com','coo@example.com'])
  s=b.create_sheet('Procurement Data');s.append(['Assigned To (Procurement)','Procurement email','Procurment Manager email']);s.append(['Buyer','buyer@example.com','pm@example.com'])
  f=io.BytesIO();b.save(f);f.seek(0);f.name='multi.xlsx';return f
 def root(self,d):
  root=Path(d);old=root/'snapshot-old';old.mkdir();pd.DataFrame({'Status':['In progress']}).to_parquet(old/'rfq.parquet',index=False)
  (old/'manifest.json').write_text(json.dumps({'imported_at':'2026-11-01','tables':{'RFQ':{'file':'rfq.parquet','rows':1,'columns':['Status']}},'pages':[{'name':'RFQ','visuals':[]}]}));(root/'current.txt').write_text(old.name)
  (root/'escalation_config.json').write_text(json.dumps({'enabled':True,'smtp':{'password':'synthetic'},'procurement_leadership_cc':['leader@example.com']}))
  return root
 def test_complete_merge_status_manager_date_source_and_config_preservation(self):
  with tempfile.TemporaryDirectory() as d:
   root=self.root(d);report=install_excel_replacement(self.workbook(),root,'Anas - All Work Orders')
   self.assertEqual(report['rows'],4);self.assertEqual(report['merged_sources']['MR1'],0)
   directory,manifest=snapshot(root);frame=pd.read_parquet(directory/manifest['tables']['Reactive']['file'])
   self.assertEqual(frame.loc[frame['Source site'].eq('Meydan GS'),'System Status'].iloc[0],'Canceled')
   self.assertEqual(frame.loc[frame['Source site'].eq('The Gardens Co.'),MANAGER].iloc[0],'Manager')
   self.assertEqual(frame.loc[frame['WO No.'].eq('D1'),'Work Order Type'].iloc[0],'Reactive')
   self.assertEqual(frame.loc[frame['WO No.'].eq('D1'),'Category'].iloc[0],'Civil')
   self.assertEqual(frame.loc[frame['WO No.'].eq('M1'),MANAGER].iloc[0],'Manager')
   self.assertEqual(report['manager_backfills'],1)
   self.assertIn('RFQ',manifest['tables'])
   config=json.loads((root/'escalation_config.json').read_text());self.assertTrue(config['enabled']);self.assertEqual(config['smtp']['password'],'synthetic');self.assertEqual(config['procurement_leadership_cc'],['leader@example.com'])
 def test_unknown_mapping_does_not_activate_or_modify_recipients(self):
  with tempfile.TemporaryDirectory() as d:
   root=self.root(d);original=(root/'escalation_config.json').read_bytes()
   with self.assertRaises(ValueError):install_excel_replacement(self.workbook(unknown=True),root,'Anas - All Work Orders')
   self.assertEqual((root/'current.txt').read_text(),'snapshot-old');self.assertEqual((root/'escalation_config.json').read_bytes(),original)
