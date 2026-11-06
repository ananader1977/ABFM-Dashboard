import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
import pandas as pd
from interactive import apply_chart_filters, selection_values, excel_bytes, export_rows, TECHNICIAN, MANAGER
from work_orders import canonicalize, install_replacement

class InteractionTests(unittest.TestCase):
    def test_chart_filters_intersect_and_keep_original_positions(self):
        f = pd.DataFrame({'Status':['A','B','A'],MANAGER:['X','X','Y']},index=[2,5,9])
        self.assertEqual(list(apply_chart_filters(f,{'Status':['A'],MANAGER:['X']}).index),[2])
        self.assertEqual(list(apply_chart_filters(f,{'Status':['A'],MANAGER:['X']},exclude='Status').index),[2,5])
        self.assertEqual(selection_values({'selection':{'pick':{'Category':['A','B']}}}),['A','B'])
        self.assertEqual(selection_values({'selection':{'pick':{}}}),[])

    def test_excel_export_filters_original_rows_and_preserves_literal_formula(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'data.parquet'
            pd.DataFrame({'Task Type':['PPM','BREAKDOWN','PPM'],'Text':['first','=1+1','last'],'Value':[10,20,30]}).to_parquet(path,row_group_size=1,index=False)
            result=excel_bytes(['Text','Value'],export_rows(str(path),'PPM',['Text','Value'],[1]))
            with zipfile.ZipFile(io.BytesIO(result)) as z:
                root=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
                self.assertIn('last', ''.join(root.itertext()))
                self.assertNotIn('first',''.join(root.itertext()))
            literal=excel_bytes(['Text'],[['=1+1']])
            with zipfile.ZipFile(io.BytesIO(literal)) as z:
                self.assertNotIn(b'<f>',z.read('xl/worksheets/sheet1.xml'))

    def fixture(self):
        return pd.DataFrame({'WO No.':[1,2,3,4], 'Work Order Type':['Reactive','PPM','Corrective','Pro-active'],
            'System Status':['Completed','Scheduled','Canceled','In Progress'], 'Assigned Resource':['A',None,'B','C'],
            'Contract Manager (Contract) (Contract)':['M','M',None,'N'],'Created On':pd.to_datetime(['2026-01-01']*4)})

    def test_approved_source_rules_and_missing_names(self):
        f=canonicalize(self.fixture())
        self.assertEqual(list(f['Status']),['COMPLETE','ACTIVE','CANCELLED','ASSIGNED'])
        self.assertEqual(list(f['Task Type']),['BREAKDOWN','PPM','SCHEDULEDTASK','PROACTIVE'])
        self.assertTrue(pd.isna(f[TECHNICIAN].iloc[1]))
        self.assertNotIn('Task Due Date',f)

    def test_replacement_preserves_other_tables_and_rolls_back_on_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'snapshot-old';old.mkdir()
            self.fixture().to_parquet(root/'new.parquet',index=False,row_group_size=2)
            pd.DataFrame({'old':[1]}).to_parquet(old/'old.parquet',index=False)
            pd.DataFrame({'keep':[2]}).to_parquet(old/'keep.parquet',index=False)
            m={'imported_at':'old date','tables':{'Reactive':{'file':'old.parquet'},'Other':{'file':'keep.parquet'}},'pages':[{'name':'Reactive','visuals':[]}]}
            (old/'manifest.json').write_text(json.dumps(m));(root/'current.txt').write_text(old.name)
            report=install_replacement(root/'new.parquet',root)
            new=root/(root/'current.txt').read_text()
            self.assertEqual(report['rows'],4)
            self.assertEqual((old/'keep.parquet').read_bytes(),(new/'keep.parquet').read_bytes())
            self.assertTrue((old/'old.parquet').exists())
            before=(root/'current.txt').read_text()
            dup=pd.concat([self.fixture(),self.fixture().iloc[:1]])
            dup.to_parquet(root/'bad.parquet',index=False)
            with self.assertRaises(ValueError):install_replacement(root/'bad.parquet',root)
            self.assertEqual(before,(root/'current.txt').read_text())
