"""Executive action counts, aggregated in small batches without caching records."""
import gc
import html
import json
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import streamlit as st
from analytics import TASK_TYPES, OPEN, normalized, resolve, numbers, dates, today, work_order_metrics
from visuals import page_header, heading, ring, bars, draw, INK, GOLD, SILVER

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
    page_header(PAGE,manifest['imported_at'])
    with st.spinner('Preparing the executive overview…'):
        data=collect(str(directory),(directory/'manifest.json').stat().st_mtime_ns,tuple(pages))
    stats=data['work_orders']
    columns=st.columns(4)
    columns[0].metric('Open work orders',f"{sum(v['Open'] for v in stats.values()):,}")
    columns[1].metric('Overdue open work orders',f"{sum(v['Overdue open'] for v in stats.values()):,}" if data['due_available'] else '—')
    rfq=next((r['count'] for r in data['rows'] if r['page']=='RFQ'),None)
    columns[2].metric('Open RFQs','—' if rfq is None else f'{rfq:,}')
    columns[3].metric('Positive unbilled balance · AED',f"{data['positive_balance']:,.2f}")
    heading('OPEN WORK & ACTION QUEUE','Review records across your workspaces')
    st.caption('Each card shows its own rule. These counts are not added into a global total because workspaces can overlap and use different record units.')
    for offset in range(0,len(data['rows']),3):
        columns=st.columns(3,gap='medium')
        for column,row in zip(columns,data['rows'][offset:offset+3]):
            with column,st.container(border=True):
                value='—' if row['count'] is None else f"{row['count']:,}"
                st.markdown(f'<div class="chart-kicker">{html.escape(row["page"])}</div><div class="queue-value">{value}</div><div class="queue-label">{html.escape(row["label"])}</div>',unsafe_allow_html=True)
                st.caption(row['definition'])
                if row['count'] is not None:
                    st.button('View matching records',key='queue_'+row['page'],on_click=navigate,args=(row['page'],row['rule'],row['table']),width='stretch')
    view=st.selectbox('Executive visual',['Work-order health','Action queue','Overdue aging'],key='executive_visual')
    if view=='Work-order health':
        rows=[{'Workspace':page,'Status':name,'Records':values[name]} for page,values in stats.items() for name in ['Completed','Open','Cancelled']]
        heading('DELIVERY HEALTH','Work orders by current status')
        draw(pd.DataFrame(rows),{'mark':{'type':'bar','cornerRadiusEnd':3},'encoding':{'x':{'field':'Workspace','type':'nominal','axis':{'labelAngle':0,'title':None}},'y':{'field':'Records','type':'quantitative'},'color':{'field':'Status','type':'nominal','scale':{'domain':['Completed','Open','Cancelled'],'range':[INK,GOLD,SILVER]}},'tooltip':[{'field':'Workspace'},{'field':'Status'},{'field':'Records','type':'quantitative','format':','}]}},'executive_health',360)
    elif view=='Action queue':
        included=[r for r in data['rows'] if r['count'] is not None]
        counts=pd.Series({r['page']:r['count'] for r in included})
        heading('REVIEW PRIORITIES','Source records matching each workspace rule')
        bars(counts.rename_axis('Workspace').reset_index(name='Records'),'Workspace','Records','executive_queue')
        st.caption('Data-quality, feedback, and historical staffing exceptions are review counts, not verified unclosed tasks. See each card’s definition.')
    else:
        counts=pd.Series(data['aging'])
        heading('BACKLOG RISK','Age of overdue open work orders')
        left,right=st.columns([1.4,1])
        with left:bars(counts.rename_axis('Age').reset_index(name='Work orders'),'Age','Work orders','executive_aging',vertical=True)
        with right:ring(counts,'executive_aging_ring','Overdue')
    st.caption('Calculated from the imported snapshot, not a live Power BI connection. Subcontractor renewal counts use Client AMC Status1, as confirmed.')
