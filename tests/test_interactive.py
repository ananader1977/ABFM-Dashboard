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
        self.assertEqual(selection_values({'selection':{'pick':[{'Category':'P3 - Medium'}]}}),['P3 - Medium'])

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
            self.assertTrue((old/'keep.parquet').exists())
            self.assertTrue((new/'keep.parquet').exists())
            active=json.loads((new/'manifest.json').read_text())
            self.assertEqual(set(active['tables']),{'Reactive','Other'})
            self.assertFalse(active['work_orders_only'])
            self.assertTrue((old/'old.parquet').exists())
            before=(root/'current.txt').read_text()
            dup=pd.concat([self.fixture(),self.fixture().iloc[:1]])
            dup.to_parquet(root/'bad.parquet',index=False)
            with self.assertRaises(ValueError):install_replacement(root/'bad.parquet',root)
            self.assertEqual(before,(root/'current.txt').read_text())

    def test_manager_alias_filters_merge_variants_without_losing_rows(self):
        from interactive import labels,apply_chart_filters,MANAGER
        frame=pd.DataFrame({MANAGER:['# Rafik Jabbour','Rafik Jabbour','#Rafik Jabour','# Sanjay Sonu Shirodkar','Sanjay Shirodkar',' #Sanjay Shirodkar ',None]})
        choices=set(labels(frame[MANAGER]))
        self.assertEqual(choices,{'Rafik Jabbour','Sanjay Shirodkar','(Not specified)'})
        self.assertEqual(len(apply_chart_filters(frame,{MANAGER:['Rafik Jabbour']})),3)
        self.assertEqual(len(apply_chart_filters(frame,{MANAGER:['Sanjay Shirodkar']})),3)
        self.assertEqual(len(frame),7)

    def test_new_import_clears_only_work_order_filters_once(self):
        from interactive import refresh_work_order_filters
        state={'_work_order_filter_version':'old','quick_Reactive_Contract Manager (Contract)':['Old name'],
            'chart_filters_Corrective_Reactive':{'Status':['ACTIVE']},'filter_Reactive_Priority':['P1'],
            'fields_Reactive':['Priority'],'executive_scope':{'page':'Reactive'},'filter_RFQ_Status':['In progress'],
            'identity':{'username':'test'},'workspace_page':'Reactive'}
        refresh_work_order_filters(state,'new')
        self.assertNotIn('quick_Reactive_Contract Manager (Contract)',state)
        self.assertNotIn('chart_filters_Corrective_Reactive',state)
        self.assertNotIn('executive_scope',state)
        self.assertEqual(state['filter_RFQ_Status'],['In progress'])
        self.assertEqual(state['identity'],{'username':'test'})
        self.assertEqual(state['workspace_page'],'Reactive')
        state['quick_Reactive_Contract Manager (Contract)']=['New name']
        refresh_work_order_filters(state,'new')
        self.assertEqual(state['quick_Reactive_Contract Manager (Contract)'],['New name'])
