"""Executive action counts, aggregated in small batches without caching records."""
import gc
import html
import json
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import streamlit as st
from analytics import TASK_TYPES, OPEN, COMPLETED, normalized, resolve, numbers, dates, today, work_order_metrics
from visuals import page_header, heading, ring, bars, draw, INK, GOLD, SILVER, status_style, styled_metric

PAGE = 'Executive Summary'
RULES = {
 'Unbilled Revenue':('Unbilled','positive_balance',['Balance Value'],'Positive balances','Balance Value greater than zero.'),
 'RFQ':('RFQ','rfq_open',['Status'],'Open RFQs','Status is In progress or Returned. Blank statuses are excluded.'),
 'AMC':('AMC','renewal',['Status'],'Pending contracts','Status is Under Renewal or Submitted.'),
 'SubContractors Agrements':('Subcontractor AMC','subcontractor_renewal',['Client AMC Status1'],'Pending contracts','Client AMC Status1 is Under Renewal or Submitted.'),
 'Manpower':('Allocation','staffing',['Status_3'],'Staffing exceptions','Allocation Status_3 is Not Matching; counts contract-allocation records.'),
 'Manpower Management':('Allocation Management','staffing_history',['Status'],'Historical mismatches','Status is Not Matching across imported reporting snapshots, not unique current contracts.'),
 'Vendor Database':('Vendor Database','vendor_quality',['EMAIL ID','MOBILE NO'],'Contact-data gaps','Email or mobile is missing. This is a data-quality exception, not an open task.'),
 'MR tracker':('MR Main Sheet','mr_quality',['PROJECT NAME','ITEM DESCRIPTION','QUANTITY'],'Incomplete MR rows','Project, item description, or quantity is missing. No open/closed field is available.'),
 'Customer Satisfaction':('2023 Survey Details','survey_review',['Quality of our work','Our response time','Courtesy and professionalism of our personnel','Overall satisfaction with Al Bonian Facilities Management'],'Feedback to review','At least one Not Satisfied rating; follow-up closure is not tracked.'),
}


def scope_frame(frame, rule):
    if isinstance(rule,dict) and rule.get('kind') == 'rfq_assignee':
        from escalation import ASSIGNEE, key
        return frame.loc[normalized(frame.Status).isin({'IN PROGRESS','RETURNED'}) & frame[ASSIGNEE].map(key).eq(key(rule['name']))]
    if rule=='agreements_expired':return frame.loc[dates(frame['End Date ']).lt(today())]
    if rule=='wo_completed':return frame.loc[normalized(frame[resolve(frame,'Status')]).isin(COMPLETED)]
    if rule=='wo_cancelled':return frame.loc[normalized(frame[resolve(frame,'Status')]).eq('CANCELLED')]
    if rule=='wo_other':return frame.loc[~normalized(frame[resolve(frame,'Status')]).isin(OPEN | COMPLETED | {'CANCELLED'})]
    if rule=='wo_open':return frame.loc[normalized(frame[resolve(frame,'Status')]).isin(OPEN)]
    if rule=='positive_balance':return frame.loc[numbers(frame,'Balance Value').gt(0).fillna(False)]
    if rule=='rfq_open':return frame.loc[normalized(frame[resolve(frame,'Status')]).isin({'IN PROGRESS','RETURNED'})]
    if rule in {'renewal','subcontractor_renewal'}:
        col='Client AMC Status1' if rule=='subcontractor_renewal' else 'Status'
        return frame.loc[normalized(frame[resolve(frame,col)]).isin({'UNDER RENEWAL','SUBMITTED'})]
    if rule in {'staffing','staffing_history'}:
        return frame.loc[normalized(frame[resolve(frame,'Status_3' if rule=='staffing' else 'Status')]).eq('NOT MATCHING')]
    columns={'vendor_quality':['EMAIL ID','MOBILE NO'],
             'mr_quality':['PROJECT NAME','ITEM DESCRIPTION','QUANTITY'],
             'survey_review':RULES['Customer Satisfaction'][2]}.get(rule)
    if columns:
        selected=pd.Series(False,index=frame.index)
        for col in columns:
            value=normalized(frame[resolve(frame,col)])
            selected |= value.eq('NOT SATISFIED' if rule=='survey_review' else '').fillna(False)
        return frame.loc[selected]
    return frame


def batches(directory, manifest, table, columns):
    info=manifest['tables'][table]
    names={c.strip().casefold():c for c in info['columns']}
    actual=[names[c.strip().casefold()] for c in columns if c.strip().casefold() in names]
    scanner=ds.dataset(directory/info['file'],format='parquet').scanner(columns=actual,use_threads=False,batch_size=512,batch_readahead=0,fragment_readahead=0,fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=False))
    for batch in scanner.to_batches():
        yield pa.Table.from_batches([batch]).to_pandas(types_mapper=pd.ArrowDtype)


@st.cache_data(ttl=300,max_entries=1,show_spinner=False)
def collect(directory_name, manifest_modified, pages):
    directory=Path(directory_name)
    manifest=json.loads((directory/'manifest.json').read_text())
    totals={page:{'Total work orders':0,'Completed':0,'Open':0,'Cancelled':0,'Other status':0,'Overdue open':0,'Open without due date':0} for page in TASK_TYPES}
    aging={label:0 for label in ['1–7 days','8–30 days','31–60 days','61–90 days','Over 90 days']}
    rows=[]
    due_available=False
    if 'Reactive' in manifest['tables']:
        due_available='Task Due Date' in manifest['tables']['Reactive']['columns']
        for frame in batches(directory,manifest,'Reactive',['Task Type','Status','Task Due Date']):
            kinds=normalized(frame[resolve(frame,'Task Type')])
            for page,kind in TASK_TYPES.items():
                subset=frame.loc[kinds.eq(kind)]
                metrics=work_order_metrics(subset)
                for name in totals[page]:totals[page][name]+=int(metrics.get(name,0))
            if due_available:
                status=normalized(frame[resolve(frame,'Status')])
                age=(today()-dates(frame['Task Due Date'])).dt.days.loc[status.isin(OPEN)]
                for label,low,high in [('1–7 days',1,7),('8–30 days',8,30),('31–60 days',31,60),('61–90 days',61,90),('Over 90 days',91,float('inf'))]:
                    aging[label]+=int(age.between(low,high).sum())
        for page in TASK_TYPES:
            if page in pages:
                rows.append({'page':page,'table':'Reactive','rule':'wo_open','label':'Open work orders','count':totals[page]['Open'],'total':totals[page]['Total work orders'],'definition':'ACTIVE or ASSIGNED, within this task type. Cancelled remains separate.'})
    positive_balance=0.0
    for page,(table,rule,columns,label,definition) in RULES.items():
        if page not in pages:continue
        record={'page':page,'table':table,'rule':rule,'label':label,'count':None,'total':0,'definition':definition}
        info=manifest['tables'].get(table)
        if info:
            record['total']=info['rows']
            available={c.strip().casefold() for c in info['columns']}
            if all(c.strip().casefold() in available for c in columns):
                count=0
                for frame in batches(directory,manifest,table,columns):
                    selected=scope_frame(frame,rule)
                    count+=len(selected)
                    if rule=='positive_balance':positive_balance+=float(numbers(selected,'Balance Value').sum())
                record['count']=count
            else:record['definition']='Required source fields are unavailable. No count was inferred.'
        else:record['definition']='Source table is unavailable. No count was inferred.'
        rows.append(record)
        gc.collect()
    rows.sort(key=lambda row:pages.index(row['page']))
    return {'rows':rows,'work_orders':totals,'aging':aging,'positive_balance':positive_balance,'due_available':due_available}


def navigate(page,rule,table):
    st.session_state['workspace_page']=page
    st.session_state['executive_scope']={'page':page,'rule':rule,'label':'Executive action selection'}
    st.session_state[f'view_{page}']='Records'
    if page in {'Manpower','Manpower Management'}:st.session_state[f'dataset_{page}']=table


def clear_scope():
    st.session_state.pop('executive_scope',None)


def render(directory,manifest,pages):
    page_header(PAGE,manifest.get('work_order_source', {}).get('imported_at', manifest['imported_at']))
    with st.spinner('Preparing the executive overview…'):
        data=collect(str(directory),(directory/'manifest.json').stat().st_mtime_ns,tuple(pages))
    stats=data['work_orders']
    columns=st.columns(4)
    if manifest.get('work_order_source'):
        for column, label, metric in zip(columns, ['Total work orders','Completed work orders','Open work orders','Cancelled work orders'], ['Total work orders','Completed','Open','Cancelled']):
            value = sum(v[metric] for v in stats.values())
            grand_total = sum(v['Total work orders'] for v in stats.values())
            percentage = None if not grand_total else 100 * value / grand_total
            share = '—' if percentage is None else f'{percentage:.2f}%'
            tone, weight, _ = status_style(label, percentage)
            breakdown = ''.join(f'<div style="display:flex;justify-content:space-between;gap:8px;margin-top:4px"><span>{html.escape(kind)}</span><strong style="color:{tone};font-weight:{weight}">{stats.get(kind,{}).get(metric,0):,}</strong></div>' for kind in ['Reactive','PPM','Corrective','Pro-active'])
            column.markdown(f'<div style="background:#fffefa;border:1px solid #ddd5c5;border-top:3px solid #b3955b;border-radius:12px;padding:11px 14px;min-height:202px;box-shadow:0 7px 24px #12284307"><div style="color:#67778b;font-size:.76rem;color:{tone};font-weight:{weight}">{html.escape(label)}</div><div class="queue-value" style="font-size:1.65rem;color:{tone};font-weight:{weight}">{value:,}</div><div style="font-size:.8rem;color:{tone};font-weight:{weight};margin:5px 0 8px"><strong style="font-weight:{weight}">{share}</strong> of all work orders</div><div style="border-top:1px solid #e5dece;padding-top:5px;font-size:.76rem;color:#65758a">{breakdown}</div></div>',unsafe_allow_html=True)
    else:
        styled_metric(columns[0], 'Open work orders', f"{sum(v['Open'] for v in stats.values()):,}", key='executive_open')
        styled_metric(columns[1], 'Overdue open work orders', f"{sum(v['Overdue open'] for v in stats.values()):,}" if data['due_available'] else '—', key='executive_overdue')
        rfq=next((r['count'] for r in data['rows'] if r['page']=='RFQ'),None)
        styled_metric(columns[2], 'Open RFQs', '—' if rfq is None else f'{rfq:,}', key='executive_rfq')
        columns[3].metric('Positive unbilled balance · AED',f"{data['positive_balance']:,.2f}")
    heading('OPEN WORK & ACTION QUEUE','Review records across your workspaces')
    st.caption('Each card shows its own rule. These counts are not added into a global total because workspaces can overlap and use different record units.')
    from escalation import review_cards
    if manifest.get('work_order_source'):
        review_cards(directory, manifest)
    else:
        for row in data['rows']:
            st.metric(row['page'], '—' if row['count'] is None else f"{row['count']:,}")
            if row['count'] is not None:
                st.button('View matching records',key='queue_'+row['page'],on_click=navigate,args=(row['page'],row['rule'],row['table']))
    from interactive import chart, selection_values, excel_bytes
    def status_click(key, page):
        values = selection_values(st.session_state.get(key, {}))
        if values:
            rule = {'Open':'wo_open','Completed':'wo_completed','Cancelled':'wo_cancelled','Other':'wo_other'}[values[0]]
            navigate(page, rule, 'Reactive')
    st.caption('Click a work-order status bar to open its matching records. Select an action bar to open that workspace’s review queue.')
    for column, (page, values) in zip(st.columns(len(stats)), stats.items()):
        with column:
            rows = [{'Category':label,'Records':values[name]} for label,name in [('Open','Open'),('Completed','Completed'),('Cancelled','Cancelled'),('Other','Other status')]]
            key = 'summary_status_' + page
            chart(pd.DataFrame(rows), key, lambda k=key,p=page: status_click(k,p), page)
    from escalation import executive_panels
    executive_panels(directory, manifest)
    left, right = st.columns(2)
    with left:
        included = [r for r in data['rows'] if r['count'] is not None]
        def queue_click():
            values = selection_values(st.session_state.get('summary_action_chart', {}))
            row = next((r for r in included if r['page'] in values), None)
            if row:
                navigate(row['page'], row['rule'], row['table'])
        chart(pd.DataFrame([{'Category':r['page'],'Records':r['count']} for r in included]),
            'summary_action_chart', queue_click, 'Action queues')
    with right:
        st.markdown('**Work-order source**')
        source = manifest.get('work_order_source')
        if source:
            st.write(source['name'])
            st.caption('Technician: Assigned Resource. Blank names stay unassigned. This workbook has no verified due date; overdue counts are unavailable.')
        else:
            st.caption('Original Power BI snapshot. Open any status bar to inspect records.')
    with st.expander('Executive records & Excel download'):
        summary = pd.DataFrame(data['rows']).rename(columns={'page':'Workspace','count':'Matching records','total':'Total records','definition':'Counting rule'})
        summary = summary[['Workspace','Matching records','Total records','Counting rule']]
        st.dataframe(summary, hide_index=True, width='stretch')
        st.download_button('Download executive summary · Excel',
            data=lambda: excel_bytes(list(summary.columns), summary.itertuples(index=False, name=None)),
            file_name='ABFM_Executive_Summary.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', on_click='ignore')
    source_name=manifest.get('work_order_source',{}).get('name','Anas - All Work Orders.xlsx')
    st.caption(f'Work orders use {source_name}. MR Tracker, RFQ and Subcontractors Agreement use their retained snapshot; source dates are shown per workspace. Cancellations remain separate.' if manifest.get('requested_restored_pages') else f'Only {source_name} is active. Counts use the confirmed status rules; cancellations remain separate.' if manifest.get('work_orders_only') else 'Calculated from imported snapshots. This is not a live Power BI connection.')
