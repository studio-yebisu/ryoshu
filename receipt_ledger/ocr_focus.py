"""Indra re-reads poorly recognized amount regions; originals stay immutable."""
import re
from statistics import median
from .extraction import TOTAL_LABEL, compact, total_candidates


def rectify_pages(converter, original, extension, pages, folder):
    if extension!='.pdf':return pages
    import cv2
    import numpy as np
    import pypdfium2 as pdfium
    from PIL import Image
    result=[]
    with pdfium.PdfDocument(original) as pdf:
        for source in pages:
            result.append(source)
            if source['source']!='ocr':continue
            page=pdf[source['index']];bitmap=page.render(scale=converter.dpi/72)
            try:rgb=np.array(bitmap.to_pil().convert('RGB'))
            finally:bitmap.close();page.close()
            h,w=rgb.shape[:2];gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
            _,mask=cv2.threshold(gray,195,255,cv2.THRESH_BINARY)
            contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            if not contours:continue
            contour=max(contours,key=cv2.contourArea)
            if cv2.contourArea(contour)<w*h*.4:continue
            quad=cv2.approxPolyDP(contour,.025*cv2.arcLength(contour,True),True)
            if len(quad)!=4:continue
            points=quad.reshape(4,2).astype('float32');sums=points.sum(axis=1);diff=np.diff(points,axis=1).ravel()
            tl=points[np.argmin(sums)];br=points[np.argmax(sums)];tr=points[np.argmin(diff)];bl=points[np.argmax(diff)]
            if max(abs(tl[1]-tr[1]),abs(bl[1]-br[1]),abs(tl[0]-bl[0]),abs(tr[0]-br[0]))<min(w,h)*.018:continue
            width=int(max(np.linalg.norm(br-bl),np.linalg.norm(tr-tl)));height=int(max(np.linalg.norm(tr-br),np.linalg.norm(tl-bl)))
            if width<120 or height<120:continue
            transform=cv2.getPerspectiveTransform(np.array([tl,tr,br,bl]),np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]],dtype='float32'))
            rectified=cv2.warpPerspective(rgb,transform,(width,height),borderValue=(255,255,255))
            rectified=cv2.copyMakeBorder(rectified,20,20,20,20,cv2.BORDER_CONSTANT,value=(255,255,255))
            path=folder/f'rectified-p{source["index"]}.png';Image.fromarray(rectified).save(path)
            for _,p in converter.convert(str(path),max_pages=1):
                if p.source=='error':continue
                old_len=len(compact(source['text']));new_len=len(compact(p.text))
                if new_len<old_len*.7:continue
                result[-1]={**source,'initial_text':source['text'],'initial_lines':source['lines'],'text':p.text,
                            'lines':[{'text':l.text,'confidence':float(l.conf),'box':[float(v) for v in l.box]} for l in p.lines],
                            'preprocessing':'perspective_correction','ocr_image':path.name}
    return result


def focus_regions(converter, original, extension, pages, folder):
    results=[]
    if extension!='.pdf':return results
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(original) as pdf:
        for source in pages:
            if source['source']!='ocr':continue
            primary=total_candidates(source['text'])
            if primary and max(c['priority'] for c in primary)>=3:continue
            lines=[l for l in source['lines'] if l['text'].strip()]
            if not lines:continue
            if source.get('ocr_image'):
                from PIL import Image
                with Image.open(folder/source['ocr_image']) as image:im=image.convert('RGB')
            else:
                page=pdf[source['index']];bitmap=page.render(scale=converter.dpi/72)
                try:im=bitmap.to_pil().convert('RGB')
                finally:bitmap.close();page.close()
            w,h=im.size
            if max(l['box'][2] for l in lines)>w*1.1 or max(l['box'][3] for l in lines)>h*1.1:continue
            labels=[l for l in lines if TOTAL_LABEL.search(compact(l['text'])) or re.search(r'金額|ご請求|領収|返済額',compact(l['text']))]
            amounts=[l for l in lines if re.search(r'\d[\d, .]{2,}',l['text'])]
            typical=median([max(1,l['box'][3]-l['box'][1]) for l in amounts]) if amounts else 1
            prominent=sorted([l for l in amounts if l['box'][3]-l['box'][1]>typical*1.3],key=lambda l:-(l['box'][3]-l['box'][1]))
            targets=(labels+prominent)[:8];crops=[]
            for label in targets:
                x0,y0,x1,y1=label['box'];lh=max(15,y1-y0)
                box=(max(0,int(x0-w*.08)),max(0,int(y0-lh*2)),min(w,int(x1+w*.5)),min(h,int(y1+lh*5)))
                if any(abs(box[1]-b[1])<lh*2 and abs(box[0]-b[0])<w*.15 for b in crops):continue
                crops.append(box)
                if len(crops)>=3:break
            for i,box in enumerate(crops):
                crop=im.crop(box)
                if crop.height<500:crop=crop.resize((crop.width*2,crop.height*2))
                path=folder/f'focus-p{source["index"]}-{i}.png';crop.save(path)
                for _,p in converter.convert(str(path),max_pages=1):
                    if p.source=='error':continue
                    results.append({'index':source['index'],'source':'indra_region_ocr','region':list(box),'text':p.text,
                                    'lines':[{'text':l.text,'confidence':float(l.conf),'box':[float(v) for v in l.box]} for l in p.lines]})
    return results


def focus_headers(converter,original,extension,pages,folder):
    """Retry missing metadata with bounded header crops, retaining every OCR result."""
    if extension!='.pdf':return []
    import pypdfium2 as pdfium
    results=[]
    with pdfium.PdfDocument(original) as pdf:
        for source in pages:
            if source['source']!='ocr':continue
            page=pdf[source['index']];bitmap=page.render(scale=converter.dpi/72)
            try:im=bitmap.to_pil().convert('RGB')
            finally:bitmap.close();page.close()
            w,h=im.size
            for i,box in enumerate([(0,0,w,int(h*.35)),(0,0,int(w*.65),int(h*.12))]):
                crop=im.crop(box);scale=max(1,min(4,1600/crop.width))
                if scale!=1:crop=crop.resize((int(crop.width*scale),int(crop.height*scale)))
                path=folder/f'header-p{source["index"]}-{i}.png';crop.save(path)
                for _,p in converter.convert(str(path),max_pages=1):
                    if p.source=='error':continue
                    results.append({'index':source['index'],'source':'indra_header_ocr','region':list(box),'text':p.text,
                                    'lines':[{'text':l.text,'confidence':float(l.conf),'box':[float(v) for v in l.box]} for l in p.lines]})
    return results
