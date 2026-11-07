import unittest
from datetime import datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from escalation import build_email, MANAGER
from mail_reports import display_name, DISPLAY_NAMES
from visuals import status_style

class MailReports(unittest.TestCase):
    def test_attachments_deadline_and_test_recipient_isolation(self):
        group={'kind':'Operations','to':'owner@example.com','cc':['director@example.com','coo@example.com'],
               'records':[{'WO No.':'123','Work Order Type':'Reactive','Priority':'P1','System Status':'In Progress','Created On':'2026-10-01','Age (days)':37,'Service Account':'Account','Building':'Building',MANAGER:next(iter(DISPLAY_NAMES)),'Full detail':'<original & full>'}]}
        msg=build_email(group,'sender@example.com','2026-11-07','test@example.com')
        self.assertEqual(msg['To'],'test@example.com');self.assertIsNone(msg['Cc'])
        parts=list(msg.iter_attachments());self.assertEqual(len(parts),2)
        self.assertEqual(parts[1].get_content_type(),'application/pdf')
        self.assertTrue(parts[1].get_payload(decode=True).startswith(b'%PDF'))
        with ZipFile(BytesIO(parts[0].get_payload(decode=True))) as z:
            xml=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows=xml.findall('.//s:row',ns);self.assertEqual(len(rows),2)
        values=[''.join(c.itertext()) for c in rows[1]]
        self.assertIn('<original & full>',values);self.assertIn(next(iter(DISPLAY_NAMES)),values)
        sent,due=map(datetime.fromisoformat,values[-2:])
        self.assertEqual((due-sent).total_seconds(),86400)
        self.assertEqual(sent,parsedate_to_datetime(msg['Date']))
        live=build_email(group,'sender@example.com','2026-11-07')
        self.assertEqual(live['To'],group['to']);self.assertIn('director@example.com',live['Cc'])

    def test_short_names_and_expired_colors(self):
        for original,short in DISPLAY_NAMES.items():self.assertEqual(display_name(original),short)
        self.assertEqual(status_style('Expired agreements',10.02)[2],'red')
        self.assertEqual(status_style('Expired agreements',7)[2],'orange')
        self.assertEqual(status_style('Expired agreements',5)[2],'normal')

    def test_summary_counts_and_ovini_extra_attachment(self):
        import pandas as pd
        from escalation import ASSIGNEE, email_summary, routes
        records=[{ASSIGNEE:'Ovini','Approved by (Operation)':'Ahmed Mohamed Mohamed Amin Alghonamy','Reference Number':'R1','Status':'In progress'},
                 {ASSIGNEE:'Ovini','Approved by (Operation)':'Ahmed Mohamed Mohamed Amin Alghonamy','Reference Number':'R2','Status':'Returned'}]
        summary=email_summary('Procurement',pd.DataFrame(records))
        self.assertEqual(summary.iloc[0]['Open RFQs'],2)
        self.assertEqual(summary.iloc[0]['Operations manager'],'Ahmed Amin')
        expired=[{'Vendor Name':'Vendor A','End Date ':'2026-01-01','Days expired':310}]
        staff={'assignee':'Ovini','email':'ovini@example.com','approver':'Ahmed Mohamed Mohamed Amin Alghonamy','operations_email':'ahmed@example.com','director':'director@example.com','coo':'coo@example.com','procurement_manager':'procurement@example.com'}
        config={'operations':[],'procurement':[staff]}
        groups,issues=routes(pd.DataFrame(),pd.DataFrame(records),config,expired)
        self.assertFalse(issues);self.assertEqual(len(groups),1)
        msg=build_email(groups[0],'sender@example.com','2026-11-07')
        self.assertEqual(len(list(msg.iter_attachments())),3)
        body=msg.get_body(preferencelist=('html',)).get_content()
        self.assertIn('<table',body);self.assertIn('Ahmed Amin',body);self.assertIn('1 expired subcontractor',body)
        self.assertIn('Ovini_Expired_Contracts',list(msg.iter_attachments())[-1].get_filename())
        groups,issues=routes(pd.DataFrame(),pd.DataFrame(),config,expired)
        self.assertFalse(issues);self.assertEqual(groups[0]['records'],[])
        self.assertEqual(len(list(build_email(groups[0],'sender@example.com','2026-11-07').iter_attachments())),2)
        ops=email_summary('Operations',pd.DataFrame([{MANAGER:'Manager A','Work Order Type':'Reactive'},{MANAGER:'Manager A','Work Order Type':'Corrective'},{MANAGER:'Manager B','Work Order Type':'PPM'}]))
        self.assertEqual(ops['Total open'].sum(),3)
        self.assertEqual(ops.iloc[0]['Reactive'],1);self.assertEqual(ops.iloc[0]['Corrective'],1)
        missing,issues=routes(pd.DataFrame(),pd.DataFrame(),{'operations':[],'procurement':[]},expired)
        self.assertEqual(missing,[]);self.assertEqual(len(issues),1)

    def test_manual_test_merge_keeps_expired_contracts_without_live_cc(self):
        from unittest.mock import patch,MagicMock
        from escalation import send_groups,ASSIGNEE
        group={'kind':'Procurement','to':'ovini@example.com','cc':['director@example.com'],
            'records':[{ASSIGNEE:'Ovini','Reference Number':'R1','Status':'In progress'}],
            'expired_contracts':[{'Vendor Name':'Vendor A','End Date ':'2026-01-01'}]}
        connection=MagicMock();connection.__enter__.return_value.send_message.return_value={}
        with patch('escalation.validate_sender',return_value={'sender':'sender@example.com'}),patch('escalation.smtp_connection',return_value=connection):
            self.assertEqual(send_groups([group],{'test_email':'test@example.com'},'2026-11-07',test=True),1)
        msg=connection.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(msg['To'],'test@example.com');self.assertIsNone(msg['Cc'])
        self.assertEqual(len(list(msg.iter_attachments())),3)
