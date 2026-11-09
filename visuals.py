"""Small aggregate-only figures and the Al Bonian executive visual theme."""
import html
import re
import pandas as pd
import streamlit as st
from analytics import dates, today, normalized, resolve, numbers, OPEN, COMPLETED

INK = '#122843'
GOLD = '#B3955B'
SILVER = '#AEB9C6'
TEAL = '#527B73'
PALETTE = [INK, GOLD, SILVER, TEAL, '#D6C49D', '#657A93', '#DEDAD1']

CSS = '''<style>
html,body,.stApp,input,button,textarea,select{font-family:Calibri,Arial,sans-serif}
:root{--ink:#122843;--gold:#b3955b;--paper:#f7f5f0}
.stApp{background:linear-gradient(145deg,#faf9f6 0%,#f1eee7 100%);color:var(--ink)}
[data-testid="stHeader"]{background:rgba(250,249,246,.94)}
.block-container{max-width:1600px;padding-top:4.5rem;padding-left:1.5rem;padding-right:1.5rem;padding-bottom:3rem}
h1,h2,h3{color:var(--ink);letter-spacing:-.025em}
h1,h2{font-family:Calibri,Arial,sans-serif!important;font-weight:500!important}
[data-testid="stSidebar"]{background:linear-gradient(170deg,#142c49,#0b1b30);border-right:1px solid #b3955b66}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#f5f2e9!important}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:#bec8d4!important;font-size:.78rem}
[data-testid="stSidebar"] [role="radiogroup"]{gap:.12rem}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:.35rem .5rem;border-radius:8px;transition:background .15s}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:#b3955b24}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:#b3955b29;border-left:2px solid #cbb27c}
[data-testid="stSidebar"] .stButton button{background:transparent;color:#f2e8d3;border-color:#b3955b77}
.brand{background:#122843;padding:22px 24px;border:1px solid #b3955b66;border-radius:16px;margin-bottom:20px;box-shadow:0 12px 35px #07172a16}
.brand img{display:block;max-width:250px;width:100%;height:auto}
[data-testid="stSidebar"] .brand{background:transparent;border:0;border-bottom:1px solid #b3955b66;border-radius:0;padding:8px 2px 26px;box-shadow:none}
[data-testid="stSidebar"] .brand img{max-width:215px}
.executive-hero{background:linear-gradient(110deg,#142e4e,#0c1d33);border:1px solid #b3955b70;border-radius:20px;padding:30px 34px;margin:0 0 22px;box-shadow:0 16px 40px #12284312;position:relative;overflow:hidden}
.executive-hero:after{content:'';position:absolute;right:-50px;top:-100px;width:300px;height:300px;border:1px solid #b3955b35;border-radius:50%;pointer-events:none}
.executive-hero h1{font-size:2.5rem!important;color:#fbf8f0!important;margin:8px 0 12px;padding:0;line-height:1.16}
.eyebrow{color:#cfb77e;font-size:.65rem;letter-spacing:.22em;font-weight:650;text-transform:uppercase}
.hero-meta{color:#c5cdda;font-size:.78rem;letter-spacing:.025em}
[data-testid="stMetric"]{background:#fffefa;border:1px solid #ddd5c5;border-top:3px solid #b3955b;border-radius:12px;padding:11px 14px;box-shadow:0 7px 24px #12284307;min-height:84px;margin-bottom:4px}
[data-testid="stMetricLabel"]{color:#67778b;font-size:.76rem;letter-spacing:.035em}
[data-testid="stMetricValue"]{font-family:Calibri,Arial,sans-serif;color:#122843;font-size:1.65rem!important;font-weight:500}
[data-testid="stForm"], [data-testid="stExpander"]{background:#fffefa;border:1px solid #ddd5c5;border-radius:14px}
[data-testid="stForm"]{padding:22px}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:16px!important;border-color:#ded6c6!important}
.stButton button,.stFormSubmitButton button{border-radius:9px;min-height:42px;border-color:#b3955b80;font-weight:550;letter-spacing:.02em}
.stFormSubmitButton button{background:#122843;color:#fff9ec;border:1px solid #122843}
.stFormSubmitButton button:hover{background:#213f61;color:white;border-color:#b3955b}
[data-testid="stTextInput"] input,[data-testid="stSelectbox"] [data-baseweb="select"]{border-radius:9px}
[data-testid="stPlotlyChart"],[data-testid="stVegaLiteChart"]{background:#fffefa;border:1px solid #e0d9cc;border-radius:16px;overflow:hidden;box-shadow:0 6px 24px #12284305}
.chart-kicker{font-size:.65rem;letter-spacing:.16em;color:#9b804a;font-weight:650;text-transform:uppercase;margin:8px 0}
.chart-heading{font-family:Calibri,Arial,sans-serif;font-size:1.35rem;color:#122843;margin:0 0 12px;line-height:1.35}
.queue-value{font-family:Calibri,Arial,sans-serif;font-size:1.9rem;color:#122843;line-height:1.2;margin:4px 0}
.queue-label{font-size:.8rem;color:#65758a;letter-spacing:.025em;margin-bottom:10px}
[data-testid="stCaptionContainer"]{color:#6b7888}
@media(max-width:760px){.executive-hero{padding:23px}.executive-hero h1{font-size:2rem!important}.block-container{padding:1.4rem 1rem}.brand{padding:18px}[data-testid="stMetricValue"]{font-size:1.6rem!important}}
[class*="st-key-metric_status_red_"] [data-testid="stMetricValue"], [class*="st-key-metric_status_red_"] [data-testid="stMetricLabel"] * {color:#b42318!important;font-weight:700!important}
[class*="st-key-metric_status_green_"] [data-testid="stMetricValue"], [class*="st-key-metric_status_green_"] [data-testid="stMetricLabel"] * {color:#16703d!important;font-weight:700!important}
[class*="st-key-metric_status_orange_"] [data-testid="stMetricValue"], [class*="st-key-metric_status_orange_"] [data-testid="stMetricLabel"] * {color:#c66a00!important;font-weight:700!important}
[class*="st-key-metric_status_lightgreen_"] [data-testid="stMetricValue"], [class*="st-key-metric_status_lightgreen_"] [data-testid="stMetricLabel"] * {color:#6b9f5c!important;font-weight:700!important}
[class*="st-key-compact_review_"] [data-testid="stVerticalBlock"]{gap:.35rem}
[class*="st-key-compact_review_"] [data-testid="stMetric"]{padding:8px 10px;min-height:72px;margin-bottom:0}
[class*="st-key-compact_review_"] .stButton button{min-height:34px;font-size:.78rem}
[class*="st-key-task_links_"] [data-testid="stHorizontalBlock"]{gap:3px}
[class*="st-key-task_links_"] .stButton button{font-size:.72rem;min-height:26px;padding:0;color:#122843;text-decoration:underline;white-space:nowrap}
[class*="st-key-task_links_"] .stButton button p{font-size:.72rem;white-space:nowrap}
[class*="st-key-task_links_"] .stButton button:hover{color:#9b804a}
.st-key-mobile_workspace_navigation{display:none}
/* Keep the navigation and dashboard in separate layout tracks at every width. */
[data-testid="stAppViewContainer"] > div:has(> [data-testid="stMain"]){position:relative!important;flex:1 1 0%!important;min-width:0!important;width:0!important}
[data-testid="stMain"]{min-width:0!important;width:100%!important}
[data-testid="stSidebar"][data-collapsed="false"]{width:280px!important;min-width:280px!important;max-width:280px!important;flex:0 0 280px!important}
[data-testid="stSidebarResizeHandle"]{display:none}
[data-testid="stSidebarCollapseButton"]{visibility:visible!important}
[data-testid="stSidebar"] [data-testid="stLinkButton"] a{background:transparent;color:#f2e8d3;border-color:#b3955b77}
[data-testid="stSidebar"] [data-testid="stLinkButton"] a:hover{background:#b3955b24;color:#fffefa}
@media(max-width:900px){
 .st-key-mobile_workspace_navigation{display:block;margin-bottom:1rem}
 [data-testid="stSidebar"],[data-testid="stExpandSidebarButton"]{display:none!important}

 [data-testid="stAppViewContainer"]{flex-direction:column!important;overflow:hidden!important}
 [data-testid="stAppViewContainer"] > div:has(> [data-testid="stMain"]){width:100%!important;height:auto!important;min-height:0!important;flex:1 1 0%!important}
 [data-testid="stMain"]{position:relative!important;height:100%!important;min-height:0!important}
 .block-container{padding-top:4.5rem!important;padding-left:1rem!important;padding-right:1rem!important}
}
</style>''' 


def status_style(label, percentage=None):
    if label=='In progress + Returned % (excl. canceled)':
        if percentage is None:return INK, '500', 'normal'
        if percentage>95:return '#16703d', '700', 'green'
        if percentage>=90:return '#6b9f5c', '700', 'lightgreen'
        return '#b42318', '700', 'red'
    if label in {'Past end date','In progress','Returned'}:
        return '#b42318', '700', 'red'
    if re.search(r'\b(?:open|expired)\b', label, flags=re.I) and percentage is not None:
        if percentage > 10:
            return '#b42318', '700', 'red'
        if percentage > 5:
            return '#c66a00', '700', 'orange'
    if re.search(r'complet(?:ed|ion)', label, flags=re.I) and percentage is not None:
        if percentage >= 95:
            return '#16703d', '700', 'green'
        if percentage >= 90:
            return '#6b9f5c', '700', 'lightgreen'
        return '#b42318', '700', 'red'
    return INK, '500', 'normal'


def styled_metric(container, label, value, percentage=None, key='value', share_label=None):
    color, weight, tone = status_style(label, percentage)
    with container.container(key='metric_status_' + tone + '_' + re.sub(r'[^a-zA-Z0-9_]', '_', str(key))):
        st.metric(label, value)
        if share_label is not None:
            st.markdown(f'<div data-status-share="{html.escape(label,quote=True)}" style="color:{color};font-weight:{weight};font-size:.8rem;margin:0 0 .5rem">{html.escape(share_label)}</div>',unsafe_allow_html=True)


def page_header(name, imported_at):
    stamp = str(imported_at)[:16].replace('T', ' ')
    st.markdown(f'<section class="executive-hero"><div class="eyebrow">AL BONIAN FM · OPERATION WORKSPACE</div><h1>{html.escape(name)}</h1><div class="hero-meta">Operations intelligence &nbsp; / &nbsp; Snapshot imported {html.escape(stamp)} UTC</div></section>', unsafe_allow_html=True)


def polish(figure, height=360):
    figure.update_layout(template='plotly_white', paper_bgcolor='#fffefa', plot_bgcolor='#fffefa',
                         font=dict(family='Calibri, Arial, sans-serif', size=12, color=INK),
                         margin=dict(l=24,r=22,t=25,b=35), height=height,
                         legend=dict(orientation='h',y=-.18,x=0,font=dict(size=11)),
                         hoverlabel=dict(bgcolor=INK,font_color='white'),
                         colorway=PALETTE)
    figure.update_xaxes(gridcolor='#eae5da',zerolinecolor='#eae5da',title_font=dict(size=11))
    figure.update_yaxes(gridcolor='#eae5da',zerolinecolor='#eae5da',title_font=dict(size=11),automargin=True)
    return figure


def heading(kicker, title):
    st.markdown(f'<div class="chart-kicker">{html.escape(kicker)}</div><div class="chart-heading">{html.escape(title)}</div>', unsafe_allow_html=True)


def field(frame, names):
    for name in names:
        try:
            return resolve(frame,name)
        except KeyError:
            pass
    return None


def categories(frame):
    result=[]
    for name in ['Status','Priority','Category','Contract Title','Project Name','Contract In Charge','PROJECT NAME','Project Details:','Premises','Managers','Manager','Vendor_Category','Vendor Name','COMPANY NAME','SEGMET','Categories','Contract','Building Name','Trade','Designation','Contract Ref']:
        value=field(frame,[name])
        if value and value not in result:
            result.append(value)
    return result


def distribution(series):
    return series.fillna('(Not specified)').astype('string[pyarrow]').replace('','(Not specified)').value_counts()


def draw(data, spec, key, height=360):
    spec = dict(spec, height=height, background='#fffefa')
    spec['config'] = {'font':'Calibri','view':{'stroke':None},
                      'axis':{'labelColor':INK,'titleColor':INK,'gridColor':'#eae5da','domain':False,'ticks':False,'labelPadding':9,'titleFontWeight':'normal','labelFontSize':11},
                      'legend':{'labelColor':INK,'titleColor':INK,'labelFontSize':11,'orient':'bottom','title':None,'symbolType':'circle'}}
    st.vega_lite_chart(data,spec,use_container_width=True,theme=None,key=key)


def ring(counts, key, caption='Records'):
    counts=counts[counts.gt(0)]
    if counts.empty:
        st.info('No records in this selection.')
        return
    total=int(counts.sum())
    if len(counts)>7:
        counts=pd.concat([counts.head(6),pd.Series({'Remaining categories':counts.iloc[6:].sum()})])
    data=pd.DataFrame({'Category':counts.index.astype('string[pyarrow]'),'Records':counts.to_numpy(),'Share':counts.to_numpy()/total})
    arc={'mark':{'type':'arc','innerRadius':88,'outerRadius':119,'stroke':'#fffefa','strokeWidth':3},
         'encoding':{'theta':{'field':'Records','type':'quantitative','stack':True},
                     'color':{'field':'Category','type':'nominal','scale':{'domain':list(data.Category),'range':PALETTE},'legend':{'columns':2,'labelLimit':180}},
                     'tooltip':[{'field':'Category','type':'nominal'},{'field':'Records','type':'quantitative','format':','},{'field':'Share','type':'quantitative','format':'.1%'}]}}
    draw(data,{'layer':[arc,
        {'data':{'values':[{'label':f'{total:,}'}]},'mark':{'type':'text','font':'Calibri','fontSize':28,'color':INK,'dy':-8},'encoding':{'text':{'field':'label'}}},
        {'data':{'values':[{'label':caption.upper()}]},'mark':{'type':'text','fontSize':9,'color':'#788494','dy':15},'encoding':{'text':{'field':'label'}}}]},key)


def bars(data, name, value_label, key, vertical=False, limit=None):
    if data.empty:
        st.info('No records in this selection.')
        return
    category={'field':name,'type':'nominal','sort':None if vertical else '-x','axis':{'title':None,'labelLimit':185,'labelAngle':0}}
    value={'field':value_label,'type':'quantitative','axis':{'title':value_label,'format':'~s'}}
    if limit is not None:value['scale']={'domain':[0,limit]}
    number_format=',.2f' if value_label=='AED' else ',.1f' if '%' in value_label else ',.0f'
    tooltip=[{'field':name,'type':'nominal'},{'field':value_label,'type':'quantitative','format':number_format}]
    if 'Recognized responses' in data.columns:tooltip.append({'field':'Recognized responses','type':'quantitative','format':','})
    encoding={'x':category if vertical else value,'y':value if vertical else category,'tooltip':tooltip}
    draw(data,{'layer':[{'mark':{'type':'bar','color':GOLD,'cornerRadiusEnd':3},'encoding':encoding},
                       {'mark':{'type':'text','color':INK,'fontSize':11,'dy':-8 if vertical else 0,'dx':0 if vertical else 6,'align':'center' if vertical else 'left'},
                        'encoding':dict(encoding,text={'field':value_label,'type':'quantitative','format':'.3~s'})}]},key,height=max(360,min(530,32*len(data)+90)) if not vertical else 360)


def ranking(counts, name, key, value_label='Records'):
    data=counts.sort_values(ascending=False).head(12).rename_axis(name).reset_index(name=value_label)
    bars(data,name,value_label,key)
    st.caption('Top 12 groups shown. Hover over a bar for its value.')


DATE_FIELDS={
 'Reactive':['Task Reported Date','Task Due Date'],
 'Unbilled':['Completion Date','Unbilled Date','Inv. Date'],
 'RFQ':['Quotation Request Date:','Quotation  Expected  Date:','Timestamp'],
 'MR Main Sheet':['MR DATE','LPO.DATE','PR. DATE:'],
 'AMC':['Starting Date','Ending Date'],
 'Subcontractor AMC':['Start Date','End Date ','Client AMC End'],
 'Site Staff List':['DOJ','AL Starts'],
 'Staff List Management':['Data Updated Date','DOJ','AL Starts'],
 '2023 Survey Details':['Reported','Survey Date'],
 'Vendor Database':['Date'],
}
MONEY_FIELDS={'Unbilled':['Balance Value','Unbilled','Invoiced Value','Non collectable Amounts'],
              'AMC':['Annual Value (Excl. 5%)'], 'Subcontractor AMC':['Monthly Cost','2026 Cost ']}
SURVEY_FIELDS={'Quality':'Quality of our work','Response time':'Our response time',
               'Professionalism':'Courtesy and professionalism of our personnel',
               'Overall satisfaction':'Overall satisfaction with Al Bonian Facilities Management'}


def date_fields(frame,table):
    return [c for name in DATE_FIELDS.get(table,[]) if (c:=field(frame,[name]))]


def overview(frame, table, page):
    prefix=f'executive_{page}_{table}'
    dims=categories(frame)
    date_options=date_fields(frame,table)
    money=[c for name in MONEY_FIELDS.get(table,[]) if (c:=field(frame,[name]))]
    choices=['Portfolio mix']
    if date_options:choices.append('Monthly activity')
    if table=='Reactive' and field(frame,['Task Due Date']) and field(frame,['Status']):choices.append('Overdue aging')
    if money and dims:choices.append('Financial exposure')
    end=field(frame,['Ending Date'] if table=='AMC' else ['End Date '] if table=='Subcontractor AMC' else [])
    if end:choices.append('Contract horizons')
    if table=='2023 Survey Details' and any(field(frame,[v]) for v in SURVEY_FIELDS.values()):choices.append('Satisfaction profile')
    lens=st.selectbox('Additional analysis',choices,key=prefix+'_lens')
    if frame.empty:
        st.info('There are no records for the current filters.')
        return
    if lens=='Portfolio mix':
        if not dims:
            st.info('No category fields are available for this dataset.')
            return
        primary=field(frame,['Status']) or dims[0]
        options=[c for c in dims if c!=primary] or dims
        group=st.selectbox('Category field',options,key=prefix+'_group')
        left,right=st.columns([.95,1.35],gap='large')
        with left:
            heading('PORTFOLIO MIX', 'Work-order status' if table=='Reactive' else primary)
            if table=='Reactive':
                status=normalized(frame[resolve(frame,'Status')])
                counts=pd.Series({'Completed':int(status.isin(COMPLETED).sum()),'Open':int(status.isin(OPEN).sum()),'Cancelled':int(status.eq('CANCELLED').sum()),'Other':int((~status.isin(COMPLETED|OPEN|{'CANCELLED'})).sum())})
            else:counts=distribution(frame[primary])
            ring(counts,prefix+'_ring')
        with right:
            heading('CONCENTRATION',f'Records by {group}')
            ranking(distribution(frame[group]),group,prefix+'_rank')
        st.caption('Counts represent source records. Cancelled work orders remain separate from completed work.')
    elif lens=='Monthly activity':
        date_col=st.selectbox('Date field',date_options,key=prefix+'_date')
        values=dates(frame[date_col])
        months=values.dt.strftime('%Y-%m')
        counts=months.dropna().value_counts().sort_index().tail(24)
        heading('ACTIVITY OVER TIME',f'Records by {date_col} month')
        if counts.empty:
            st.info('No valid dates are available for this selection.')
            return
        data=counts.rename_axis('Month').reset_index(name='Records')
        enc={'x':{'field':'Month','type':'ordinal','sort':None,'axis':{'labelAngle':-40,'title':'Month'}},
             'y':{'field':'Records','type':'quantitative','axis':{'title':'Records'}},
             'tooltip':[{'field':'Month','type':'ordinal'},{'field':'Records','type':'quantitative','format':','}]}
        draw(data,{'layer':[{'mark':{'type':'area','color':GOLD,'opacity':.17},'encoding':enc},
                            {'mark':{'type':'line','color':INK,'strokeWidth':3,'point':{'filled':True,'fill':GOLD,'size':45}},'encoding':enc}]},prefix+'_trend',380)
        st.caption(f'Up to 24 latest reporting months. {int(values.isna().sum()):,} missing or invalid dates are excluded. This chart counts records by the selected date field.')
    elif lens=='Overdue aging':
        status=normalized(frame[resolve(frame,'Status')])
        due=dates(frame[resolve(frame,'Task Due Date')])
        age=(today()-due).dt.days
        aged=age.loc[status.isin(OPEN)&due.lt(today())]
        bands=pd.cut(aged,bins=[0,7,30,60,90,float('inf')],labels=['1–7 days','8–30 days','31–60 days','61–90 days','Over 90 days'])
        counts=bands.value_counts(sort=False)
        left,right=st.columns([1.4,1],gap='large')
        with left:
            heading('BACKLOG RISK','Overdue open work orders')
            data=counts.rename_axis('Age').reset_index(name='Work orders')
            bars(data,'Age','Work orders',prefix+'_aging',vertical=True)
        with right:
            heading('EXPOSURE MIX','Age of overdue backlog')
            ring(counts,prefix+'_aging_ring','Overdue')
        st.caption('ACTIVE and ASSIGNED work orders with recorded due dates before today, evaluated in Dubai time. Missing due dates are excluded.')
    elif lens=='Financial exposure':
        amount=st.selectbox('Value field (AED)',money,key=prefix+'_amount')
        group=st.selectbox('Group by',dims,key=prefix+'_moneygroup')
        values=numbers(frame,amount)
        data=pd.DataFrame({'Group':frame[group].fillna('(Not specified)').astype('string[pyarrow]'),'Value':values})
        totals=data.groupby('Group',dropna=False).Value.sum(min_count=1).dropna()
        heading('FINANCIAL EXPOSURE',f'{amount} by {group}')
        ranking(totals,group,prefix+'_money','AED')
        st.caption('Sum of the selected source value field in AED. Negative adjustments are retained; no currency conversion is applied.')
    elif lens=='Contract horizons':
        remaining=(dates(frame[end])-today()).dt.days
        counts=pd.Series({'Past end date':int(remaining.lt(0).sum()),'Within 30 days':int(remaining.between(0,30).sum()),'31–60 days':int(remaining.between(31,60).sum()),'61–90 days':int(remaining.between(61,90).sum()),'Beyond 90 days':int(remaining.gt(90).sum()),'Missing date':int(remaining.isna().sum())})
        left,right=st.columns([1.4,1],gap='large')
        with left:
            heading('CONTRACT HORIZONS',f'Records by {end}')
            ranking(counts,'Horizon',prefix+'_expiry_bar')
        with right:
            heading('PORTFOLIO OUTLOOK','End-date distribution')
            ring(counts,prefix+'_expiry_ring')
        st.caption('Date-based review flags across selected records. A past end date does not establish whether a contract has been renewed or closed.')
    elif lens=='Satisfaction profile':
        rows=[]
        for label,column in SURVEY_FIELDS.items():
            col=field(frame,[column])
            if not col:continue
            ratings=normalized(frame[col])
            satisfied=int(ratings.isin({'SATISFIED','VERY SATISFIED'}).sum())
            unhappy=int(ratings.eq('NOT SATISFIED').sum())
            if satisfied+unhappy:rows.append({'Dimension':label,'Satisfied %':100*satisfied/(satisfied+unhappy),'Recognized responses':satisfied+unhappy})
        heading('SERVICE EXPERIENCE','Customer satisfaction by dimension')
        if not rows:
            st.info('No recognized satisfaction ratings are available.')
            return
        bars(pd.DataFrame(rows),'Dimension','Satisfied %',prefix+'_survey',limit=100)
        st.caption('Satisfied and Very Satisfied ÷ (Satisfied + Very Satisfied + Not Satisfied). Blank and other ratings are excluded.')
