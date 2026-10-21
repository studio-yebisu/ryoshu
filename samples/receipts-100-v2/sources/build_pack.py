"""Integrate independent renderers, preserve their raw designs, vary acquisition.

No expected labels are drawn on the receipts. They live only in the manifest.
All temporary full-size raster evidence stays on C:, not the project disk.
"""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import zipfile

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageFont, ImageDraw, ImageOps
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT=Path(__file__).resolve().parent
POPLER=os.environ.get('RYOSHU_PDFTOPPM') or shutil.which('pdftoppm')
AUTHORS=['kimi','sol','flashnext','flash1017','sonnet','opus']
MODEL_IDS={'kimi':'kimi-k3','sol':'gpt-6.1-sol','flashnext':'deepseek-v4.1-flash','flash1017':'deepseek-v4.1-flash:1017','sonnet':'claude-sonnet-5-5','opus':'claude-opus-5-5'}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def review_source(path):
    source=path.read_text(encoding='utf-8-sig')
    tree=ast.parse(source,str(path))
    imports=[]
    forbidden=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import): imports += [a.name for a in node.names]
        if isinstance(node,ast.ImportFrom): imports.append(node.module or '')
        if isinstance(node,ast.Call):
            name=ast.unparse(node.func)
            if name in {'eval','exec','compile','__import__','os.system','os.popen','shutil.rmtree','Path.unlink'} or name.startswith(('subprocess.','requests.','urllib.','socket.')): forbidden.append(name)
    allowed={'argparse','ast','collections','datetime','decimal','functools','hashlib','io','json','math','os','pathlib','random','re','reportlab','PIL','pypdf','string','sys','textwrap','typing','uuid'}
    forbidden += [i for i in imports if i.split('.')[0] not in allowed]
    if forbidden: raise RuntimeError(f'{path}: review unsafe/unsupported operations first: {forbidden}')
    return {'sha256':sha(path),'imports':imports,'functions':len([n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)])}

def render(path,out_prefix,dpi=140):
    subprocess.run([str(POPLER),'-r',str(dpi),'-f','1','-singlefile','-png',str(path),str(out_prefix)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    return Path(str(out_prefix)+'.png')

def solve(matrix, vector):
    rows=[list(map(float,row))+[float(v)] for row,v in zip(matrix,vector)]
    for col in range(len(rows)):
        pivot=max(range(col,len(rows)),key=lambda r:abs(rows[r][col]))
        rows[col],rows[pivot]=rows[pivot],rows[col]
        d=rows[col][col]
        if abs(d)<1e-10: raise ValueError('singular perspective')
        rows[col]=[x/d for x in rows[col]]
        for row in range(len(rows)):
            if row==col: continue
            factor=rows[row][col]
            rows[row]=[x-factor*y for x,y in zip(rows[row],rows[col])]
    return [r[-1] for r in rows]

def camera(image,rng):
    w,h=image.size
    border=max(22,int(min(w,h)*.1))
    ow,oh=w+border*2,h+border*2
    target=[(border*.8,border*1.4),(ow-border*.7,border*.6),(ow-border*1.25,oh-border*.65),(border*1.1,oh-border*1.25)]
    source=[(0,0),(w,0),(w,h),(0,h)]
    mat=[]; vec=[]
    for (x,y),(u,v) in zip(target,source):
        mat.append([x,y,1,0,0,0,-u*x,-u*y]);vec.append(u)
        mat.append([0,0,0,x,y,1,-v*x,-v*y]);vec.append(v)
    coeff=solve(mat,vec)
    warp=image.transform((ow,oh),Image.Transform.PERSPECTIVE,coeff,resample=Image.Resampling.BICUBIC,fillcolor=(126,119,107))
    # A gentle left/right illumination gradient; printed information is retained.
    shade=Image.new('RGB',(ow,oh)); draw=ImageDraw.Draw(shade)
    for x in range(ow):
        value=int(230+25*x/max(1,ow-1));draw.line((x,0,x,oh),fill=(value,value,value))
    return ImageChops.multiply(warp,shade),{'corners_px':target,'lighting':'gentle horizontal gradient'}

def mode_for(index):
    # Author groups each receive several representations; no model equals one capture style.
    position=(index*37)%100
    if position<48:return 'digital'
    if position<66:return 'flatbed'
    if position<78:return 'thermal_fade'
    if position<86:return 'fax'
    if position<96:return 'camera'
    return 'rotation'

def acquire(source,destination,clean_png,index,mode):
    reader=PdfReader(source)
    assert len(reader.pages)==1
    page=reader.pages[0]
    w,h=float(page.mediabox.width),float(page.mediabox.height)
    details={'mode':mode,'seed':index*859,'source_page_points':[round(w,2),round(h,2)]}
    if mode=='digital': shutil.copyfile(source,destination);return details
    if mode=='rotation':
        writer=PdfWriter();writer.add_page(page);angle=90 if index%2 else 270;writer.pages[0].rotate(angle)
        writer.add_metadata({'/Title':'架空テスト領収書','/Producer':'Independent model design / capture test'})
        with destination.open('wb') as f:writer.write(f)
        details['rotation_degrees']=angle;return details
    rng=random.Random(index*859)
    image=Image.open(clean_png).convert('RGB')
    if mode=='flatbed':
        skew=(-1 if index%2 else 1)*round(.35+rng.random()*.7,2)
        image=ImageOps.grayscale(image).convert('RGB').rotate(skew,Image.Resampling.BICUBIC,expand=True,fillcolor='white')
        image=ImageEnhance.Contrast(image).enhance(.92)
        details['skew_degrees']=skew
    elif mode=='thermal_fade':
        image=ImageOps.grayscale(image)
        image=image.point(lambda x:int(112+x*.53)).convert('RGB')
        if index%2:image=image.filter(ImageFilter.GaussianBlur(.25))
        details['ink_min_gray']=112;details['paper_gray']=247
    elif mode=='fax':
        small=(max(200,int(w/72*100)),max(200,int(h/72*100)))
        image=ImageOps.grayscale(image).resize(small,Image.Resampling.LANCZOS)
        image=image.point(lambda x:255 if x>176 else 0).convert('RGB')
        details['effective_dpi']=100
    elif mode=='camera':
        image,extra=camera(image,rng);details.update(extra)
    buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=86 if mode!='camera' else 80)
    buffer.seek(0)
    # Keep the whole paper in frame; expansion is proportional to the raster.
    scale=image.width/Image.open(clean_png).width
    physical_w=w*scale;physical_h=h*image.height/Image.open(clean_png).height
    c=canvas.Canvas(str(destination),pagesize=(physical_w,physical_h),pageCompression=1)
    c.setTitle('架空テスト領収書');c.setAuthor('Independent model author / synthetic capture')
    c.drawImage(ImageReader(buffer),0,0,width=physical_w,height=physical_h);c.showPage();c.save()
    return details

def contact_sheets(cases,out,render_dir):
    font=ImageFont.truetype('C:/Windows/Fonts/meiryo.ttc',19)
    small=ImageFont.truetype('C:/Windows/Fonts/meiryo.ttc',15)
    tiles=[]
    thumb_dir=out/'thumbnails';thumb_dir.mkdir(exist_ok=True)
    for row in cases:
        image=Image.open(render_dir/(row['case_id']+'.png')).convert('RGB')
        thumb=image.copy();thumb.thumbnail((560,660))
        thumb.save(thumb_dir/(row['case_id']+'.jpg'),quality=82)
        tile=Image.new('RGB',(360,520),'#ebece8');draw=ImageDraw.Draw(tile)
        im=image.copy();im.thumbnail((336,435))
        tile.paste(im,((360-im.width)//2,8+(435-im.height)//2))
        draw.text((12,451),row['case_id']+'  '+row['author'],font=font,fill='#1a2924')
        title=row['title']
        while draw.textbbox((0,0),title,font=small)[2]>336:title=title[:-2]+'…'
        draw.text((12,481),title,font=small,fill='#3f4b43')
        tiles.append(tile)
    for batch in range(5):
        sheet=Image.new('RGB',(1800,2080),'white')
        for local,tile in enumerate(tiles[batch*20:(batch+1)*20]):sheet.paste(tile,((local%5)*360,(local//5)*520))
        sheet.save(out/f'contact-{batch+1:02d}.jpg',quality=89)

def gallery(cases,out):
    cards=[]
    by_author={author:[r for r in cases if r['author']==author] for author in AUTHORS}
    interleaved=[by_author[author][i] for i in range(20) for author in AUTHORS if i<len(by_author[author])]
    for row in interleaved:
        search=' '.join([row['case_id'],row['title'],row['author'],row['model'],row['layout_description'],row['acquisition']['mode']])
        cards.append('<article data-search="'+html.escape(search,quote=True)+'"><a target="_blank" href="'+html.escape(row['file'],quote=True)+'"><img loading="lazy" src="thumbnails/'+row['case_id']+'.jpg" alt="'+html.escape(row['title'],quote=True)+'"></a><h2>'+row['case_id']+' · '+html.escape(row['title'])+'</h2><p>'+html.escape(row['model'])+' / '+html.escape(row['acquisition']['mode'])+'</p><small>'+html.escape(row['layout_description'])+'</small></article>')
    document='''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>架空領収書100パターン</title><style>body{margin:0;background:#f2f2ec;color:#20392f;font:15px/1.7 "Yu Gothic",sans-serif}header{max-width:1600px;margin:auto;padding:32px}h1{font-size:28px;margin:0}input{width:min(600px,80%);padding:12px;border:1px solid #b9c6bd;border-radius:8px;font:inherit}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:22px;padding:0 32px 40px;max-width:1600px;margin:auto}article{background:white;padding:15px;border-radius:10px}article[hidden]{display:none}img{display:block;width:100%;height:360px;object-fit:contain;background:#eee}h2{font-size:15px}p,small{font-size:12px}a{color:#1d604a}</style><header><h1>架空領収書 100パターン</h1><p>6つの実行モデルによる独立した帳票設計。すべて架空の検証資料です。画像を押すと個別PDFが開きます。</p><p><a href="receipts-100-review.pdf">100ページの合本</a> · <a href="manifest.json">期待値と作成記録</a></p><input type="search" aria-label="パターンを検索" placeholder="例：ホテル / kimi / camera / V032"><span id="count">100件</span></header><main>'''+''.join(cards)+'''</main><script>const input=document.querySelector('input'),cards=[...document.querySelectorAll('article')];input.addEventListener('input',()=>{let n=0;for(const card of cards){const ok=card.dataset.search.toLowerCase().includes(input.value.toLowerCase());card.hidden=!ok;n+=ok}document.querySelector('#count').textContent=n+'件'});</script></html>'''
    before, script_and_end = document.split('<script>',1)
    script, after = script_and_end.split('</script>',1)
    document = before + '<script src="gallery.js"></script>' + after
    (out/'gallery.html').write_text(document,encoding='utf-8')
    (out/'gallery.js').write_text(script,encoding='utf-8')

def main():
    if not POPLER: raise SystemExit('Install Poppler and add pdftoppm to PATH, or set RYOSHU_PDFTOPPM.')
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--authors',nargs='*',default=AUTHORS);ap.add_argument('--assemble',action='store_true');args=ap.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    (out/'individual').mkdir(exist_ok=True)
    clean=ROOT/'clean';clean.mkdir(exist_ok=True)
    clean_render=ROOT/'clean-render';clean_render.mkdir(exist_ok=True)
    final_render=ROOT/'render';final_render.mkdir(exist_ok=True)
    source_reports={}
    for author in args.authors:
        script=ROOT/'authors'/author/'generate.py'
        source_reports[author]=review_source(script)
        dest=clean/author
        subprocess.run([sys.executable,str(script),'--output',str(dest)],check=True,cwd=script.parent)
        fragment=json.loads((dest/'manifest-fragment.json').read_text(encoding='utf-8-sig'))
        if isinstance(fragment,dict):fragment=fragment.get('cases') or fragment.get('receipts')
        for row in fragment:
            case_id=row['case_id'];index=int(case_id[1:]);source=dest/row['file']
            if source.parent.resolve()!= (dest/'individual').resolve():raise ValueError('unexpected source path')
            png=render(source,clean_render/case_id,170)
            destination=out/'individual'/(case_id+'.pdf')
            row['acquisition']=acquire(source,destination,png,index,mode_for(index))
            row['file']='individual/'+case_id+'.pdf';row['author']=author;row['model']=MODEL_IDS[author]
            row['sha256']=sha(destination);row['clean_pdf_sha256']=sha(source);row['generator_sha256']=source_reports[author]['sha256']
            render(destination,final_render/case_id,120)
        (ROOT/'authors'/author/'final-fragment.json').write_text(json.dumps(fragment,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'author':author,'pages_built':len(fragment),'source_review':source_reports[author]},ensure_ascii=False),flush=True)
    if not args.assemble:return
    cases=[];authors=[]
    for author in AUTHORS:
        cases+=json.loads((ROOT/'authors'/author/'final-fragment.json').read_text(encoding='utf-8'))
        p=ROOT/'authors'/author/'run.json'
        meta=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'author':author,'requested_model':MODEL_IDS[author],'route':'archived generator; local regeneration','status':'authored'}
        meta['generator_file_sha256']=sha(ROOT/'authors'/author/'generate.py')
        raw=ROOT/'authors'/author/'raw-output.jsonl'
        if raw.is_file():meta['raw_output_sha256']=sha(raw)
        tail=ROOT/'authors'/author/'tail-output.jsonl'
        if tail.is_file():meta['continuation_output_sha256']=sha(tail)
        fixes=ROOT/'authors'/author/'adjustments.json'
        if fixes.is_file():meta['integration_adjustments']=json.loads(fixes.read_text(encoding='utf-8'))
        requests=[]
        for log in (ROOT/'authors'/author).glob('*stderr.log'):
            for line in log.read_text(encoding='utf-8',errors='replace').splitlines():
                if line.startswith('[receipt-request] '):requests.append(json.loads(line[len('[receipt-request] '):]))
        if requests:meta['captured_requests']=requests
        authors.append({k:v for k,v in meta.items() if k not in ['pid','usage']})
    cases.sort(key=lambda r:r['case_id'])
    assert [r['case_id'] for r in cases]==[f'V{i:03d}' for i in range(1,101)]
    assert len({r['layout_id'] for r in cases})==100
    manifest={'version':'receipts-100-v2','created_utc':datetime.now(timezone.utc).isoformat(),'fictional':True,'purpose':'OCR and accounting candidate robustness fixtures; no real transactions','ground_truth_policy':'human context and expected accounts are held outside receipt/model input; generated labels require review','cases':cases,'authors':authors,'counts':{'cases':100,'layouts':100,'models':len(AUTHORS),'capture_modes':dict(Counter(r['acquisition']['mode'] for r in cases))}}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    writer=PdfWriter()
    for row in cases:
        writer.append(out/row['file'],outline_item=row['case_id']+' '+row['title'])
    writer.add_metadata({'/Title':'架空領収書100パターン / 閲覧用合本','/Author':'Six independently executed model authors'})
    with (out/'receipts-100-review.pdf').open('wb') as f:writer.write(f)
    contact_sheets(cases,out,final_render);gallery(cases,out)
    readme='''# 架空領収書100パターン v2\n\n6モデルによる独立した描画コードから作成した100件です。旧30件の描画コードは使っていません。全件が架空で、実際の取引・申告には使えません。\n\n- `gallery.html`: 画像一覧と検索。個別PDFへリンクします。\n- `individual/`: 読取テスト用の1ページPDF100件。アプリは50ファイルずつ取り込めます。\n- `receipts-100-review.pdf`: 閲覧専用100ページ合本。アプリへ一括取込するファイルではありません。\n- `manifest.json`: 発行元、日時、金額、別添の業務背景、科目の許容候補、保留条件、設計と撮影条件、作成モデル、ハッシュ。これは検証用の期待値で、OCRや科目モデルの入力には混ぜません。\n- `sources/`: モデルが作成した描画コード。親による修正があれば別途記録します。生回答・認証情報・実行ログは配布物に含めません。\n\n48件は電子PDF、18件は傾いた平面スキャン、12件は薄い印字、8件は100dpi白黒FAX、10件は斜め撮影、4件は90/270度回転です。全ページの可読性と構造は別途レンダリングして確認します。\n\nFlashNextという固定IDは登録されていなかったため、既存Ollama Cloudで公開されている `deepseek-v4.1-flash` をHarness経由で使用します。1017固定版は別に `deepseek-v4.1-flash:1017` として実行します。モデル名は実行経路と返却情報に基づくもので、プロバイダー内部の実体を証明するものではありません。\n'''
    readme=readme.replace('全ページの可読性と構造は別途レンダリングして確認します。','全ページをレンダリングし、構造の違いと可読性を確認しています。')
    readme=readme.replace('をHarness経由で使用します。','をHarness経由で使用しました。').replace('として実行します。','として実行しました。')
    readme+='\n再生成する場合は、同梱した `sources/build_pack.py --output <新しい保存先> --assemble` を、このPCの既存PDF用Pythonで実行できます。作成済みの描画コードを使うため、この再生成でモデルAPIは呼びません。元の保存先に上書きせず新しい保存先を指定してください。\n'
    (out/'README.md').write_text(readme,encoding='utf-8')
    sources=out/'sources';sources.mkdir(exist_ok=True)
    for author in AUTHORS:
        author_dir=sources/'authors'/author;author_dir.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/'authors'/author/'generate.py',author_dir/'generate.py')
        public_meta=next(a for a in authors if a['author']==author)
        (author_dir/'run.json').write_text(json.dumps(public_meta,ensure_ascii=False,indent=2),encoding='utf-8')
        fixes=ROOT/'authors'/author/'adjustments.json'
        if fixes.is_file():shutil.copyfile(fixes,author_dir/'adjustments.json')
    adjustments={a:json.loads((ROOT/'authors'/a/'adjustments.json').read_text(encoding='utf-8')) for a in AUTHORS if (ROOT/'authors'/a/'adjustments.json').is_file()}
    (sources/'integration-adjustments.json').write_text(json.dumps(adjustments,ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copyfile(ROOT/'BRIEF.md',sources/'BRIEF.md')
    shutil.copyfile(Path(__file__).resolve(),sources/'build_pack.py')
    shutil.copyfile(ROOT/'validate_pack.py',sources/'validate_pack.py')
    pending_zip=out/'.receipts-100.zip.tmp'
    with zipfile.ZipFile(pending_zip,'w',zipfile.ZIP_DEFLATED) as archive:
        for f in sorted(out.rglob('*')):
            if f.is_file() and f.name not in {'receipts-100.zip','.receipts-100.zip.tmp','receipts-100-review.pdf'}:
                archive.write(f,f.relative_to(out))
    pending_zip.replace(out/'receipts-100.zip')
    print(json.dumps({'complete':True,'pages':len(cases),'capture_modes':manifest['counts']['capture_modes']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
