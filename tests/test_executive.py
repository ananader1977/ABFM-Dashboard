import unittest
import pandas as pd
from executive import scope_frame


class ExecutiveRules(unittest.TestCase):
    def test_rfq_open_excludes_blank_and_finished_statuses(self):
        frame=pd.DataFrame({'Status':['Completed','In progress','', 'Returned','Cancelled','Quotation Received']})
        result=scope_frame(frame,'rfq_open')
        self.assertEqual(list(result.index),[1,3])

    def test_subcontractors_use_confirmed_second_field(self):
        frame=pd.DataFrame({'Client AMC Status':['Signed']*3,'Client AMC Status1':['Signed','Under Renewal','Submitted']})
        self.assertEqual(list(scope_frame(frame,'subcontractor_renewal').index),[1,2])

    def test_positive_balances_preserve_row_positions(self):
        frame=pd.DataFrame({'Balance Value':[0,120,-20,30,None]},index=[10,20,30,40,50])
        self.assertEqual(list(scope_frame(frame,'positive_balance').index),[20,40])

    def test_feedback_is_counted_once_per_record(self):
        frame=pd.DataFrame({
            'Quality of our work':['Not Satisfied','Satisfied'],
            'Our response time':['Not Satisfied','Satisfied'],
            'Courtesy and professionalism of our personnel':['Satisfied','Satisfied'],
            'Overall satisfaction with Al Bonian Facilities Management':['Not Satisfied','Satisfied']})
        self.assertEqual(len(scope_frame(frame,'survey_review')),1)
