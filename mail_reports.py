"""Recipient-scoped escalation attachments using the application theme."""
import base64
import html
import re
from io import BytesIO
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.utils import ImageReader
from brand import LOGO

DISPLAY_NAMES = {
    'Ahmed Mohamed Mohamed Amin Alghonamy': 'Ahmed Amin',
    'Akhil Raveendran Nair Raveendran Nair': 'Akhil Nair',
    'Varughese Mathew Othikkamannil Ninan Varughese': 'Varughese Mathew',
    'Pragneshkumar Pravinbhai Patel': 'Pragneshkumar Patel',
    '# Rafik Jabbour':'Rafik Jabbour',
    '# Rafik Jabour':'Rafik Jabbour',
    '#Rafik Jabbour':'Rafik Jabbour',
    '#Rafik Jabour':'Rafik Jabbour',
    '# Sanjay Sonu Shirodkar':'Sanjay Shirodkar',
    '#Sanjay Sonu Shirodkar':'Sanjay Shirodkar',
    '# Sanjay Shirodkar':'Sanjay Shirodkar',
    '#Sanjay Shirodkar':'Sanjay Shirodkar',
    'Sanjay Sonu Shirodkar':'Sanjay Shirodkar',
}

def display_name(value):
    value=str(value).strip()
    canonical={'rafik jabbour':'Rafik Jabbour','rafik jabour':'Rafik Jabbour','sanjay shirodkar':'Sanjay Shirodkar','sanjay sonu shirodkar':'Sanjay Shirodkar'}
    normalized=' '.join(re.sub(r'^[#\s]+','',value).casefold().split())
    return canonical.get(normalized,DISPLAY_NAMES.get(value,value))

def cell(value):
    return '' if value is None or pd.isna(value) else str(value)

def pdf_bytes(kind, frame, sent_at, deadline, source_date, summary=None, expired_contracts=None):
    navy=colors.HexColor('#122843');gold=colors.HexColor('#B3955B')
    output=BytesIO(); width,height=landscape(A4)
    doc=SimpleDocTemplate(output,pagesize=(width,height),leftMargin=28,rightMargin=28,topMargin=100,bottomMargin=36,
        title='ABFM '+kind+' escalation',author='Al Bonian FM')
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=8,leading=11,textColor=navy)
    heading=ParagraphStyle('heading',parent=body,fontName='Helvetica-Bold',fontSize=13,leading=17,keepWithNext=True)
    small=ParagraphStyle('small',parent=body,fontSize=7,leading=9)
    header=ParagraphStyle('header',parent=small,fontName='Helvetica-Bold',textColor=colors.white)
    numeric=ParagraphStyle('numeric',parent=small,alignment=2)
    numeric_header=ParagraphStyle('numeric_header',parent=header,alignment=2)
    def para(value,style=small):
        # The complete original values are retained in Excel.
        text=cell(value).replace('–','-').replace('—','-')
        text=text.encode('cp1252','replace').decode('cp1252')
        if len(text)>350:text=text[:347]+'...'
        return Paragraph(html.escape(display_name(text)).replace('\n','<br/>'),style)
    def page(canvas,doc):
        canvas.setFillColor(colors.HexColor('#F7F5F0'));canvas.rect(0,0,width,height,fill=1,stroke=0)
        canvas.setFillColor(navy);canvas.rect(0,height-84,width,84,fill=1,stroke=0)
        canvas.drawImage(ImageReader(BytesIO(base64.b64decode(LOGO))),28,height-65,width=142,height=51,mask='auto')
        canvas.setFillColor(colors.white);canvas.setFont('Helvetica-Bold',14)
        canvas.drawString(190,height-30,kind+' escalation | '+str(len(frame))+' open records')
        canvas.setFont('Helvetica',9)
        canvas.drawString(190,height-47,'Email date: '+sent_at.strftime('%d %b %Y, %H:%M')+' Dubai (UTC+4)')
        canvas.drawString(190,height-63,'Response due: '+deadline.strftime('%d %b %Y, %H:%M')+' Dubai (UTC+4)')
        canvas.setStrokeColor(gold);canvas.setLineWidth(3);canvas.line(0,height-85,width,height-85)
        canvas.setFillColor(navy);canvas.setFont('Helvetica',7)
        canvas.drawString(28,20,'AL BONIAN FM | OPERATION WORKSPACE | Confidential - intended recipients only')
        canvas.drawRightString(width-28,20,'Page '+str(doc.page))
    story=[Paragraph('Action required within 24 hours',heading),Spacer(1,6),
        Paragraph('Please address the attached items and confirm closure or provide an owner, action plan and expected completion date within 24 hours of the email date. If blocked, reply by the deadline with the blocker and recovery plan.',body),Spacer(1,6),
        Paragraph(html.escape('Source snapshot: '+str(source_date)+'. Imported snapshot; current closures require a new import.'),small),
        Paragraph('All matching records are listed below. Full source fields and unabridged text are in the Excel attachment; long PDF cells are shortened to 350 characters.',small),Spacer(1,12)]
    def make_table(data_frame,weights,labels=None,padding=6):
        columns=list(data_frame.columns);labels=labels or {}
        widths=[(width-56)*x/sum(weights) for x in weights]
        numbers=[pd.api.types.is_numeric_dtype(data_frame[c]) and c not in {'WO No.','Reference Number','Sr. #'} for c in columns]
        data=[[para(labels.get(c,c),numeric_header if number else header) for c,number in zip(columns,numbers)]]
        for row in data_frame.itertuples(index=False,name=None):
            data.append([para(f'{v:,}' if number and pd.notna(v) else v,numeric if number else small) for v,number in zip(row,numbers)])
        table=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f0ece3')]),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#ddd5c5')),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),padding),('BOTTOMPADDING',(0,0),(-1,-1),padding)]))
        return table
    if summary is not None and len(summary):
        story.extend([Paragraph('Open task breakdown',heading),Spacer(1,6),make_table(summary,[2 if not pd.api.types.is_numeric_dtype(summary[c]) else 1 for c in summary],padding=4),Spacer(1,14)])
        if '7-day open trend' in summary:
            from task_history import TREND_NOTE
            story.extend([Paragraph(html.escape(TREND_NOTE.replace('—','-')),small),Spacer(1,12)])
    if summary is not None and len(summary)>8:story.append(PageBreak())
    story.extend([Paragraph('All open task records',heading),Spacer(1,6)])
    labels={'Contract Manager (Contract) (Contract)':'Contract manager','Assigned To (Procurement / Store) please put e-mail address of procurement / store officer':'Procurement assignee','Quotation Request Date:':'Requested','Approved by (Operation)':'Operations approver','Project Details:':'Project','Created On':'Opened','System Status':'Status','Work Order Type':'Type'}
    weights=([.8,.8,.5,.7,1,.5,1.3,1.1,1.3] if kind=='Operations' else [.8,.7,.5,1,1.7,1.2,1.3])[:len(frame.columns)]
    if len(frame):story.append(make_table(frame,weights,labels))
    else:story.append(Paragraph('No open task records in this email.',body))
    if expired_contracts:
        contracts=pd.DataFrame(expired_contracts)
        wanted=['Vendor Name','Project Name','Contract In Charge','End Date ','Days expired']
        selected=[c for c in wanted if c in contracts]
        if not selected:selected=list(contracts.columns)[:5]
        story.extend([PageBreak(),Paragraph(f'Expired subcontractor agreements - Ovini ({len(contracts):,})',heading),Spacer(1,6),
            Paragraph('Vendor agreement End Date is before the email date in Dubai. Complete contract fields are provided in the separate expired-contracts Excel attachment.',body),Spacer(1,10),
            make_table(contracts[selected],[1]*len(selected),{'End Date ':'Agreement end date'})])
    doc.build(story,onFirstPage=page,onLaterPages=page)
    return output.getvalue()
