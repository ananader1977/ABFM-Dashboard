"""Recipient-scoped escalation attachments using the application theme."""
import base64
import html
from io import BytesIO
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.utils import ImageReader
from brand import LOGO

DISPLAY_NAMES = {
    'Ahmed Mohamed Mohamed Amin Alghonamy': 'Ahmed Amin',
    'Akhil Raveendran Nair Raveendran Nair': 'Akhil Nair',
    'Varughese Mathew Othikkamannil Ninan Varughese': 'Varughese Mathew',
    'Pragneshkumar Pravinbhai Patel': 'Pragneshkumar Patel',
}

def display_name(value):
    return DISPLAY_NAMES.get(str(value).strip(), str(value).strip())

def cell(value):
    return '' if value is None or pd.isna(value) else str(value)

def pdf_bytes(kind, frame, sent_at, deadline, source_date):
    navy=colors.HexColor('#122843');gold=colors.HexColor('#B3955B')
    output=BytesIO(); width,height=landscape(A4)
    doc=SimpleDocTemplate(output,pagesize=(width,height),leftMargin=28,rightMargin=28,topMargin=100,bottomMargin=36,
        title='ABFM '+kind+' escalation',author='Al Bonian FM')
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=8,leading=11,textColor=navy)
    heading=ParagraphStyle('heading',parent=body,fontName='Helvetica-Bold',fontSize=13,leading=17)
    small=ParagraphStyle('small',parent=body,fontSize=7,leading=9)
    header=ParagraphStyle('header',parent=small,fontName='Helvetica-Bold',textColor=colors.white)
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
    labels={'Contract Manager (Contract) (Contract)':'Contract manager','Assigned To (Procurement / Store) please put e-mail address of procurement / store officer':'Procurement assignee','Quotation Request Date:':'Requested','Approved by (Operation)':'Operations approver','Project Details:':'Project','Created On':'Opened','System Status':'Status','Work Order Type':'Type'}
    columns=list(frame.columns)
    if kind=='Operations':weights=[.8,.8,.5,.7,1,.5,1.3,1.1,1.3]
    else:weights=[.8,.7,.5,1,1.7,1.2,1.3]
    weights=weights[:len(columns)]; widths=[(width-56)*x/sum(weights) for x in weights]
    data=[[para(labels.get(c,c),header) for c in columns]]
    data.extend([[para(v) for v in row] for row in frame.itertuples(index=False,name=None)])
    table=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f0ece3')]),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#ddd5c5')),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    story.append(table);doc.build(story,onFirstPage=page,onLaterPages=page)
    return output.getvalue()
