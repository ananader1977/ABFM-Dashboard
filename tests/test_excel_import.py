"""Validate direct Excel replacement against complete snapshots and failure rollback."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import pandas as pd
from interactive import excel_bytes
from work_orders import install_excel_replacement,excel_sheet_names,preview_excel,REQUIRED_EXCEL_COLUMNS,RESTORED_PAGES

class ExcelReplacement(unittest.TestCase):
 def workbook(self,rows,columns=None):
  columns=columns or REQUIRED_EXCEL_COLUMNS+['Service Account','Building']
  source=io.BytesIO(excel_bytes(columns,[[row.get(name) for name in columns] for row in rows]));source.name='Daily work orders.xlsx';return source
 def rows(self,count=4):
  kinds=['Reactive','PPM','Corrective','Pro-active']
  return [{'WO No.':str(i+1).zfill(6),'Work Order Type':kinds[i%4],'System Status':'Completed' if i%2==0 else 'Scheduled','Priority':'P2 - High','Assigned Resource':None,'Contract Manager (Contract) (Contract)':None,'Created On':'07/11/2026','Service Account':'Account','Building':'Building'} for i in range(count)]
 def snapshot(self,root):
  directory=root/'snapshot-original';directory.mkdir()
  pd.DataFrame({'old_work_orders':[True]}).to_parquet(directory/'old-wo.parquet',index=False)
  tables={'Reactive':{'file':'old-wo.parquet','columns':['old_work_orders'],'rows':1}};pages=[{'name':'Reactive','visuals':[]}]
  for i,(page,table) in enumerate(RESTORED_PAGES.items()):
   file=f'retained-{i}.parquet';pd.DataFrame({'Reference':['Do not replace']}).to_parquet(directory/file,index=False)
   tables[table]={'file':file,'rows':1,'columns':['Reference']};pages.append({'name':page,'visuals':[]})
  manifest={'imported_at':'original-date','tables':tables,'pages':pages,'requested_restored_pages':list(RESTORED_PAGES)}
  (directory/'manifest.json').write_text(json.dumps(manifest));(root/'current.txt').write_text(directory.name);return directory
 def test_excel_replaces_all_work_orders_and_preserves_other_datasets(self):
  with TemporaryDirectory() as directory:
   root=Path(directory);previous=self.snapshot(root);source=self.workbook(self.rows(520))
   self.assertEqual(excel_sheet_names(source),['Records']);self.assertEqual(len(preview_excel(source,'Records')),5)
   report=install_excel_replacement(source,root,'Records')
   active=root/(root/'current.txt').read_text();manifest=json.loads((active/'manifest.json').read_text())
   self.assertEqual(report['rows'],520);self.assertEqual(report['name'],'Daily work orders.xlsx');self.assertEqual(report['sheet'],'Records')
   self.assertEqual(report['counts'],{kind:130 for kind in ['Reactive','PPM','Corrective','Pro-active']})
   result=pd.read_parquet(active/manifest['tables']['Reactive']['file'])
   self.assertEqual(result['Task Id'].iloc[0],'000001');self.assertEqual(result['Status'].iloc[0],'COMPLETE')
   self.assertEqual(result['Task Reported Date'].iloc[0],pd.Timestamp('2026-11-07'))
   self.assertTrue(result['Contract Manager (Contract)'].isna().all())
   for page,table in RESTORED_PAGES.items():
    file=manifest['tables'][table]['file'];self.assertEqual((active/file).read_bytes(),(previous/file).read_bytes())
    self.assertEqual(manifest['page_imported_at'][page],'original-date')
   self.assertTrue((previous/'old-wo.parquet').exists());self.assertFalse(manifest['work_orders_only'])
 def test_invalid_rows_and_duplicate_ids_never_change_current_data(self):
  for field,value in [('WO No.',''),('Work Order Type','Unknown'),('System Status','Unknown'),('Created On','not a date')]:
   with self.subTest(field=field),TemporaryDirectory() as directory:
    root=Path(directory);self.snapshot(root);rows=self.rows(520);rows[-1][field]=value
    with self.assertRaises(ValueError):install_excel_replacement(self.workbook(rows),root,'Records')
    self.assertEqual((root/'current.txt').read_text(),'snapshot-original');self.assertEqual(list(root.glob('excel-import-*')),[])
  with TemporaryDirectory() as directory:
   root=Path(directory);self.snapshot(root);rows=self.rows(520);rows[-1]['WO No.']=rows[0]['WO No.']
   with self.assertRaisesRegex(ValueError,'duplicate'):install_excel_replacement(self.workbook(rows),root,'Records')
   self.assertEqual((root/'current.txt').read_text(),'snapshot-original')
 def test_missing_columns_empty_sheet_wrong_sheet_and_bad_excel_are_clear(self):
  with self.assertRaisesRegex(ValueError,'Priority'):preview_excel(self.workbook(self.rows(),[c for c in REQUIRED_EXCEL_COLUMNS if c!='Priority']),'Records')
  with self.assertRaisesRegex(ValueError,'no work-order'):preview_excel(self.workbook([]),'Records')
  with self.assertRaisesRegex(ValueError,'selected worksheet'):preview_excel(self.workbook(self.rows()),'Not a sheet')
  with self.assertRaisesRegex(ValueError,'valid, unencrypted'):excel_sheet_names(io.BytesIO(b'not an Excel workbook'))
 def test_date_cells_numeric_ids_case_and_cancellation_variants(self):
  rows=self.rows();rows[1]['Priority']=None;rows[0].update({'WO No.':201040,'Created On':pd.Timestamp('2026-11-07 10:00'),'Work Order Type':'reactive','System Status':'cancelled'})
  with TemporaryDirectory() as directory:
   root=Path(directory);self.snapshot(root);report=install_excel_replacement(self.workbook(rows),root,'Records')
   active=root/(root/'current.txt').read_text();frame=pd.read_parquet(active/'work_orders.parquet')
   self.assertEqual(report['blank_priority_rows'],1);self.assertEqual(len(frame),4)
   self.assertEqual(frame['Task Id'].iloc[0],'201040');self.assertEqual(frame['Status'].iloc[0],'CANCELLED')
   self.assertEqual(frame['Task Reported Date'].iloc[0],pd.Timestamp('2026-11-07 10:00'))
