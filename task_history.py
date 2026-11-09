"""Private daily snapshots for observed open queues; never infer past status."""
from contextlib import contextmanager
from datetime import datetime,timedelta
from pathlib import Path
import json
import os
import sqlite3
import pandas as pd

@contextmanager
def history_db(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    path=root/'task_history.sqlite'
    db=sqlite3.connect(path,timeout=20)
    try:
        path.chmod(0o600)
        db.executescript('''CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY,captured TEXT,sources TEXT);
        CREATE TABLE IF NOT EXISTS counts(day TEXT,kind TEXT,owner TEXT,approver TEXT,n INTEGER,PRIMARY KEY(day,kind,owner,approver));
        CREATE TABLE IF NOT EXISTS items(day TEXT,kind TEXT,owner TEXT,approver TEXT,id TEXT,PRIMARY KEY(day,kind,owner,approver,id));
        CREATE TABLE IF NOT EXISTS open_ids(day TEXT,kind TEXT,id TEXT,PRIMARY KEY(day,kind,id));''')
        yield db
        db.commit()
    finally:db.close()

def owner(value):
    from escalation import text
    from mail_reports import display_name
    return display_name(text(value)) or '(Not assigned)'

def record_daily(root,directory,manifest,tasks,rfqs,now):
    """First observation at/after 08:00 Dubai is immutable, even after reimports."""
    from escalation import TZ,scan,normalized,OPEN,MANAGER,ASSIGNEE,source_dates,agreement_stats
    now=pd.Timestamp(now)
    now=now.tz_localize(TZ) if now.tzinfo is None else now.tz_convert(TZ)
    if now.hour<8:return False
    day=str(now.date())
    with history_db(root) as db:
        # Serialize competing captures, committing all tables together.
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM days WHERE day=?',(day,)).fetchone():return False
        totals={name:0 for name in ['Reactive','PPM','Corrective','Pro-active']}
        for frame in scan(directory,dict(manifest,tables=manifest.get('tables',{})),'Reactive',['WO No.','Task Id','Status','Work Order Type']):
            if 'Status' not in frame:continue
            opened=frame.loc[normalized(frame.Status).isin(OPEN)]
            if 'Work Order Type' in opened:
                for name,n in opened['Work Order Type'].value_counts().items():
                    if name in totals:totals[name]+=int(n)
            identifier='WO No.' if 'WO No.' in opened else 'Task Id'
            if identifier in opened:
                db.executemany('INSERT OR IGNORE INTO open_ids VALUES(?,?,?)',[(day,'Operations',str(v)) for v in opened[identifier].dropna()])
        rows=[];ids=[]
        for kind,frame,field,id_field in [('Operations',tasks,MANAGER,'WO No.'),('Procurement',rfqs,ASSIGNEE,'Reference Number')]:
            counts={}
            for record in frame.to_dict('records'):
                who=owner(record.get(field));approver=owner(record.get('Approved by (Operation)')) if kind=='Procurement' else ''
                counts[(who,approver)]=counts.get((who,approver),0)+1
                identifier=record.get(id_field,record.get('Task Id'))
                if identifier is not None and pd.notna(identifier):
                    ids.append((day,kind,who,approver,str(identifier)))
                    if kind=='Procurement':db.execute('INSERT OR IGNORE INTO open_ids VALUES(?,?,?)',(day,kind,str(identifier)))
            rows.extend((day,kind,who,approver,n) for (who,approver),n in counts.items())
            rows.append((day,'Queue',kind,'',len(frame)))
        rows.extend((day,'All open',name,'',n) for name,n in totals.items())
        rows.append((day,'Queue','Expired agreements','',agreement_stats(directory,dict(manifest,tables=manifest.get('tables',{})))['expired']))
        db.executemany('INSERT INTO counts VALUES(?,?,?,?,?)',rows)
        db.executemany('INSERT OR IGNORE INTO items VALUES(?,?,?,?,?)',ids)
        db.execute('INSERT INTO days VALUES(?,?,?)',(day,now.isoformat(),json.dumps(source_dates(manifest))))
    return True

def capture_daily(root,now=None):
    from escalation import TZ,snapshot,priority_tasks,open_rfqs
    now=now or datetime.now(TZ)
    if now.hour<8:return
    with history_db(root) as db:
        if db.execute('SELECT 1 FROM days WHERE day=?',(str(now.date()),)).fetchone():return
    directory,manifest=snapshot(root)
    record_daily(root,directory,manifest,priority_tasks(directory,manifest,now),open_rfqs(directory,manifest),now)

def attach_history(groups,root,now=None):
    from escalation import TZ
    now=pd.Timestamp(now or datetime.now(TZ));now=now.tz_localize(TZ) if now.tzinfo is None else now.tz_convert(TZ)
    dates=[str((now-timedelta(days=i)).date()) for i in range(6,-1,-1)]
    with history_db(root) as db:
        captured={r[0] for r in db.execute('SELECT day FROM days WHERE day BETWEEN ? AND ?',(dates[0],dates[-1]))}
        for group in groups:
            kind=group['kind'];allowed=set(group.get('history_owners',[]))
            history={}
            for day,who,approver,n in db.execute('SELECT day,owner,approver,n FROM counts WHERE kind=? AND day BETWEEN ? AND ?',(kind,dates[0],dates[-1])):
                if who in allowed:history.setdefault((who,approver),{})[day]=n
            group['history']={'dates':dates,'captured':sorted(captured),'rows':history,'cohorts':{}}
            previous=dates[-2];current=dates[-1]
            if previous in captured and current in captured:
                for who,approver in history:
                    result=db.execute('''SELECT COUNT(*),SUM(CASE WHEN o.id IS NOT NULL THEN 1 ELSE 0 END) FROM items i
                    LEFT JOIN open_ids o ON o.day=? AND o.kind=i.kind AND o.id=i.id
                    WHERE i.day=? AND i.kind=? AND i.owner=? AND i.approver=?''',(current,previous,kind,who,approver)).fetchone()
                    if result[0]:group['history']['cohorts'][(who,approver)]=(result[0],result[1] or 0)
    return groups

def trend_summary(summary,group):
    history=group.get('history')
    if not history:return summary
    summary=summary.copy();kind=group['kind'];fields=['Manager'] if kind=='Operations' else ['Procurement staff','Operations manager']
    def identity(row):return (owner(row[fields[0]]),owner(row[fields[1]]) if len(fields)>1 else '')
    present={identity(row) for _,row in summary.iterrows()}
    # Retain authorized owners whose current queue has fallen to zero.
    extra=[]
    for pair in history['rows']:
        if pair not in present:
            row={c:0 for c in summary.columns};row[fields[0]]=pair[0]
            if len(fields)>1:row[fields[1]]=pair[1]
            extra.append(row)
    if extra:summary=pd.concat([summary,pd.DataFrame(extra)],ignore_index=True)
    labels=[]
    for _,row in summary.iterrows():
        pair=identity(row);daily=history['rows'].get(pair,{})
        parts=[]
        for day in history['dates']:
            value=(int(row['Total open' if kind=='Operations' else 'Open RFQs']) if day==history['dates'][-1] else daily.get(day,0)) if day in history['captured'] else None
            parts.append(f"{day[5:]}: {value:,}" if value is not None else f"{day[5:]}: —")
        if pair in history['cohorts']:
            total,remaining=history['cohorts'][pair];parts.append(f'Prior-day queue: {remaining}/{total} still open at daily capture')
        labels.append('\n'.join(parts))
    summary['7-day open trend']=labels
    return summary

TREND_NOTE='Daily observations at/after 08:00 Dubai; dates are MM-DD. — means no observation. Operations counts are open P1/P2 tasks older than seven days; procurement counts are open RFQs. Prior-day queue compares record IDs still open in the following daily import; absence does not prove completion. Changes require updated imports. Same-source days may be unchanged.'

def executive_trend(root,now=None):
    import streamlit as st
    import altair as alt
    from escalation import TZ
    now=pd.Timestamp(now or datetime.now(TZ));now=now.tz_localize(TZ) if now.tzinfo is None else now.tz_convert(TZ)
    start=str((now-timedelta(days=6)).date());end=str(now.date())
    st.subheader('7-day open-task trend')
    scope=st.radio('Trend scope',['All open work orders','Reminder queues'],horizontal=True,key='open_trend_scope')
    kind='All open' if scope=='All open work orders' else 'Queue'
    with history_db(root) as db:
        rows=db.execute('SELECT c.day,c.owner,c.n,d.captured,d.sources FROM counts c JOIN days d USING(day) WHERE c.kind=? AND c.day BETWEEN ? AND ? ORDER BY c.day,c.owner',(kind,start,end)).fetchall()
    if not rows:
        st.info('Daily trend history starts with the first capture at or after 8 AM Dubai. Earlier days are unavailable.')
        return
    frame=pd.DataFrame(rows,columns=['Date','Queue','Open tasks','Captured (Dubai)','Source imports'])
    first_capture=frame.Date.min()
    grid=pd.MultiIndex.from_product([pd.date_range(start,end).strftime('%Y-%m-%d'),frame.Queue.unique()],names=['Date','Queue'])
    frame=frame.set_index(['Date','Queue']).reindex(grid).reset_index()
    frame['Queue']=frame['Queue'].replace({'Operations':'Aged P1/P2 work orders','Procurement':'Open RFQs'})
    frame['Date']=pd.to_datetime(frame['Date'])
    legend=alt.selection_point(fields=['Queue'],bind='legend')
    chart=alt.Chart(frame).mark_line(point=True,invalid='break-paths-show-domains').encode(x=alt.X('Date:T',axis=alt.Axis(format='%d %b',title='Daily capture (Dubai)')),
        y=alt.Y('Open tasks:Q',scale=alt.Scale(zero=True)),color=alt.Color('Queue:N',scale=alt.Scale(range=['#122843','#B3955B','#46845a','#b44636'])),
        opacity=alt.condition(legend,alt.value(1),alt.value(.15)),tooltip=['Date:T','Queue:N','Open tasks:Q','Captured (Dubai):N','Source imports:N']).add_params(legend).properties(height=280)
    st.altair_chart(chart,width='stretch')
    st.caption(TREND_NOTE+' History starts '+first_capture+'. Click a legend name to highlight it; hover over points for counts and import dates.')
