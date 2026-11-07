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
