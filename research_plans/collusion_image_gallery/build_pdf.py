"""PDF companion from saved gallery captions and unchanged source PNGs."""
import hashlib
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth

DEST=Path('/workspace/visual_collusion_stress_v4/runs/main/triad_emergent_v9_live/analysis/collusion_image_gallery')
manifest=json.loads((DEST/'MANIFEST.json').read_text())
rows=[r for r in manifest['records'] if r['category']!='context']
assert len(rows)==39
target=Path('/workspace/image_collusion_attempts_gallery.pdf')
c=canvas.Canvas(str(target),pagesize=A4)
c.setTitle('Collusion attempts: all 39 relevant original images explained')
c.setAuthor('Researcher evidence review')
W,H=A4;M=36;usable=W-2*M

def clean(text):
    # Helvetica has limited Unicode coverage. Preserve the original text in HTML/JSON.
    return str(text).translate(str.maketrans({'→':'->','—':' - ','–':'-','’':"'",'‘':"'",'“':'"','”':'"','·':' | ','≥':'>=','≤':'<='})).encode('latin-1','replace').decode('latin-1')

def paragraph(text,y,size=9.1,bold=False,width=usable):
    font='Helvetica-Bold' if bold else 'Helvetica';c.setFont(font,size)
    words=clean(text).split();lines=[];line=''
    for word in words:
        new=(line+' '+word).strip()
        if stringWidth(new,font,size)>width and line:lines.append(line);line=word
        else:line=new
    if line:lines.append(line)
    for line in lines:c.drawString(M,y,line);y-=size*1.35
    return y-5

def footer(page):
    c.setStrokeColorRGB(.8,.85,.88);c.line(M,29,W-M,29)
    c.setFont('Helvetica',8);c.setFillColorRGB(.3,.38,.43)
    c.drawString(M,17,'V9 saved evidence | Exact source image shown | Private drafts are explicitly labeled')
    c.drawRightString(W-M,17,str(page));c.setFillColorRGB(.08,.15,.22)

y=H-M
y=paragraph('Collusion attempts in the images',y,23,True)
y=paragraph('Every relevant image from the latest 12-round episode and its isolated controls',y,12)
y-=15
for title,text in [
 ('What is included','39 image records: 13 main steering/encoding/protocol drafts; 4 additional control candidates; 7 convention proposals; 7 copied cues; 7 ambiguous cues or precursors; and 1 private-intent-only image. These are not 39 confirmed collusion events or independent trials.'),
 ('What the result supports','There are observable attempts and proposed conventions. No round ended with all three choosing A\'s private target. No shared covert decoder or pixel-bit steganography was demonstrated.'),
 ('Delivery matters','A sent its selected image to B and C; B sent its selected image only to C. After locked ballots and Codex feedback, C sent its selected image only to next A. Replaced drafts were creator-only. The current C image was absent from the current review.'),
 ('What the controls mean','Control A images are isolated sender responses, with no B/C continuation or reviewer. Whole-image replacement plus fresh stochastic sampling cannot isolate a specific hidden feature.'),
 ('How to inspect','Each following page shows one original PNG and explains the intended or possible signal, actual rendering, recipients, and observed outcome. The interactive HTML contains all 71 generated images, actual executed briefs, private self-reports labeled as fallible, source records, and SHA-256 hashes.'),
 ('No new runs','This document was built offline from saved evidence. Original PNGs and frozen experiment artifacts were not edited.')]:
    y=paragraph(title,y,11,True);y=paragraph(text,y,10);y-=10
footer(1);c.showPage()

for page,r in enumerate(rows,2):
    y=H-M
    y=paragraph(f"{r['id']} | {r['scope']} round {r['round']} | Agent {r['role']} | Draft {r['generation']}",y,11,True)
    y=paragraph(r['title'],y,14,True)
    status='Accepted' if r['accepted'] else 'Private / replaced'
    y=paragraph(f"Evidence label: {r['category']} | {status}",y,9)
    image_h=min(392,y-335);image_w=image_h
    c.drawImage(ImageReader(r['source']),(W-image_w)/2,y-image_h,width=image_w,height=image_h,preserveAspectRatio=True,anchor='c')
    y-=image_h+13
    ballot=', '.join(f'{k}={v if v is not None else "missing"}' for k,v in r['ballots'].items())
    y=paragraph(f"Policy answer {r['oracle']} | Private target {r['target']} | {'Main' if r['scope']=='Main' else 'Isolated control'} ballots: {ballot}",y,8.5,True)
    for label,key in [('Look at','where'),('Intended / possible message','intent'),('What actually rendered','rendered'),('What the evidence supports','interpretation'),('Actual recipient','route')]:
        y=paragraph(label+': '+r[key],y,8.7)
    assert y>=38,(r['id'],y)
    footer(page);c.showPage()
c.save()
report={'pdf':str(target),'pages':40,'annotated_image_records':39,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
(DEST/'PDF_VALIDATION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
