import datetime as dt
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo
from defusedxml import ElementTree as ET

TZ = ZoneInfo('Asia/Shanghai')
TYPES = {1:'文本',3:'图片',34:'语音',43:'视频',47:'表情',49:'卡片',10000:'系统消息',10002:'系统消息'}

def window(hours=24, start=None, end=None):
    def parse(s):
        d=dt.datetime.fromisoformat(s)
        return d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)
    b=parse(end) if end else dt.datetime.now(TZ).replace(microsecond=0)
    a=parse(start) if start else b-dt.timedelta(hours=hours)
    if a>=b: raise ValueError('起点必须早于终点')
    return a,b

def timestamp(v):
    n=int(v)
    # Explicit accepted epoch ranges, never silently guess arbitrary units.
    if 946684800<=n<4102444800: return n
    if 946684800000<=n<4102444800000 and n%1000==0: return n//1000
    if 946684800000<=n<4102444800000: return n/1000
    raise ValueError('不支持的时间戳单位或日期范围')

def parse_body(raw, local_type, sender):
    if isinstance(raw,bytes):
        if raw.startswith(b'\x28\xb5\x2f\xfd'):
            from compression import zstd
            output=bytearray()
            while raw:
                decoder=zstd.ZstdDecompressor()
                output.extend(decoder.decompress(raw,max_length=16*1024*1024-len(output)+1))
                if len(output)>16*1024*1024 or not decoder.eof:
                    raise ValueError('消息解压超过限制或压缩流不完整')
                raw=decoder.unused_data
            raw=bytes(output)
        raw=raw.decode('utf-8','strict')
    if sender and raw.startswith(sender+':\n'): raw=raw[len(sender)+2:]
    typ=int(local_type)&0xffffffff
    result={'type':TYPES.get(typ,f'未知类型 {local_type}'),'text':'','details':{}}
    if typ in (1,10000,10002):
        result['text']=raw
        links=re.findall(r'https?://[^\s<>]+',raw)
        if links:result['details']['links']=links
    elif typ==49 or (typ==34 and raw.lstrip().startswith('<')):
        try:
            root=ET.fromstring(raw)
            def value(path): return root.findtext(path) or ''
            if typ==49:
                for key,path in [('title','.//appmsg/title'),('description','.//appmsg/des'),('url','.//appmsg/url'),('file_extension','.//appattach/fileext'),('file_size','.//appattach/totallen'),('card_type','.//appmsg/type')]:
                    if value(path): result['details'][key]=value(path)
                ref=root.find('.//refermsg')
                if ref is not None:
                    result['details']['quote']={k:ref.findtext(k) or '' for k in ('svrid','displayname','content','type','createtime')}
                result['text']=result['details'].get('title','')
            else:
                node=root.find('.//voicetrans')
                if node is not None and node.get('transtext'):
                    result['text']=node.get('transtext');result['details']['transcript_source']='微信消息中已有 voicetrans.transtext'
        except Exception:
            result['details']['parse_warning']='XML 无法安全解析，未推断内容'
    if typ in (3,34,43,47) and not result['text']:
        result['text']=f'[{result["type"]}：未解析媒体内容]'
    if not result['text']: result['text']=f'[{result["type"]}]'
    return result

def normalize(rows, group_id, start, end):
    seen={};out=[];duplicates=0
    for row in rows:
        t=timestamp(row['create_time'])
        if not start.timestamp()<=t<end.timestamp(): continue
        parsed=parse_body(row['body'],row['local_type'],row.get('sender_id'))
        identity=f'{group_id}:server:{row["server_id"]}' if str(row.get('server_id','0')) not in ('0','','None') else f'{group_id}:{row["database"]}:{row["local_id"]}'
        mid=hashlib.sha256(identity.encode()).hexdigest()[:24]
        m={'id':mid,'server_id':str(row.get('server_id','0')),'local_id':str(row['local_id']),'database':row['database'],'timestamp':t,'time':dt.datetime.fromtimestamp(t,TZ).isoformat(),'sender_id':row.get('sender_id'),'sender':row.get('sender') or row.get('sender_id') or '未映射发送人',**parsed}
        if mid in seen:
            old=seen[mid]
            if any(old[k]!=m[k] for k in ('timestamp','sender_id','type','text','details')): raise ValueError('相同消息标识存在冲突，需人工复核')
            old.setdefault('duplicate_sources',[]).append({'database':m['database'],'local_id':m['local_id']});duplicates+=1
        else: seen[mid]=m;out.append(m)
    out.sort(key=lambda m:(m['timestamp'],int(m['local_id']),m['database']))
    return out,duplicates

def package(rows,name,gid,start,end,fixture=False,extra=None):
    msgs,duplicates=normalize(rows,gid,start,end)
    meta={'group_name':name,'group_id':gid,'start':start.isoformat(),'end':end.isoformat(),'timezone':'Asia/Shanghai','interval':'[start, end)','message_count':len(msgs),'speaker_count':len({m['sender_id'] for m in msgs if m['sender_id']}),'unknown_sender_messages':sum(not m['sender_id'] for m in msgs),'duplicates_removed':duplicates,'type_counts':dict(Counter(m['type'] for m in msgs)),'fixture':fixture,'scope':'仅本机当前账号已同步记录；不代表群完整历史。无法仅据本地数据库证明同步完整。',**(extra or {})}
    return {'metadata':meta,'messages':msgs}

def dump(path,data): Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
def export_files(folder,data):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False,mode=0o700)
    dump(folder/'messages.json',data)
    (folder/'messages.txt').write_text('\n\n'.join(f'[{m["id"]}] {m["time"]} {m["sender"]}\n{m["text"]}\n'+(json.dumps(m['details'],ensure_ascii=False) if m['details'] else '') for m in data['messages']),encoding='utf8')

SECTIONS={'topics':'主要话题及讨论结论','todos':'待办事项','confirmed':'已解决或已确认事项','unresolved':'尚未解决的问题','other':'其他信息'}
def validate_messages(data):
    """Recheck serialized exports before rendering; do not trust saved counters."""
    meta, messages = data['metadata'], data['messages']
    a, b = dt.datetime.fromisoformat(meta['start']), dt.datetime.fromisoformat(meta['end'])
    if a.tzinfo is None or b.tzinfo is None or a >= b:
        raise ValueError('导出时间窗口必须带时区且起点早于终点')
    if meta.get('interval') != '[start, end)':
        raise ValueError('导出时间区间不是左闭右开')
    ids = [m['id'] for m in messages]
    if any(type(x) is not str or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('原始消息 ID 必须是非空且唯一的字符串')
    previous = None
    for m in messages:
        stamp = dt.datetime.fromisoformat(m['time'])
        if stamp.tzinfo is None or not a <= stamp < b:
            raise ValueError('存在超出窗口或未带时区的消息')
        if type(m['timestamp']) not in (int, float) or stamp.timestamp() != m['timestamp']:
            raise ValueError('消息时间文本与时间戳不一致')
        if previous is not None and m['timestamp'] < previous:
            raise ValueError('消息未按时间排序')
        previous = m['timestamp']
    expected = {
        'message_count': len(messages),
        'speaker_count': len({m['sender_id'] for m in messages if m['sender_id']}),
        'unknown_sender_messages': sum(not m['sender_id'] for m in messages),
        'type_counts': dict(Counter(m['type'] for m in messages)),
    }
    for key, value in expected.items():
        if type(meta.get(key)) is not type(value) or meta[key] != value:
            raise ValueError('导出统计与消息明细不一致：' + key)
    return True

def validate_report(data,report):
    validate_messages(data)
    ids={m['id'] for m in data['messages']}
    if report.get('reviewed_message_ids')!=[m['id'] for m in data['messages']]: raise ValueError('必须逐条阅读全部消息，并按导出顺序列出 reviewed_message_ids')
    if not isinstance(report.get('overview'),dict): raise ValueError('overview 必须为带 text 和 sources 的对象')
    for section in SECTIONS:
        if not isinstance(report.get(section),list): raise ValueError(f'缺少栏目 {section}')
    for item in [report['overview']]+[v for k in SECTIONS for v in report[k]]:
        if not isinstance(item.get('text'),str) or not item['text'].strip(): raise ValueError('结论文本为空')
        refs=item.get('sources')
        if not isinstance(refs,list) or (ids and not refs) or any(r not in ids for r in refs): raise ValueError('结论引用缺失或不存在')
    for item in report['todos']:
        for k in ('owner','deadline','status'):
            if not item.get(k): raise ValueError(f'待办缺少 {k}，不明确时填写未明确')
    # Existence checks do not establish semantic entailment: session reviewer must inspect evidence.
    return True

def render(folder,report):
    folder=Path(folder);data=json.loads((folder/'messages.json').read_text());validate_report(data,report)
    meta=data['metadata'];byid={m['id']:m for m in data['messages']};e=html.escape
    sequence={m['id']:i for i,m in enumerate(data['messages'],1)}
    title=('【虚构测试】' if meta['fixture'] else '')+meta['group_name']+' · 聊天总结'
    info=f'{meta["start"]} 至 {meta["end"]}（Asia/Shanghai，包含起点、不包含终点）'
    coverage='群 ID：'+meta['group_id']+'；'+ '、'.join(f'{n} 条{k}' for k,n in meta['type_counts'].items())+'。来源编号对应 messages.json 顺序；稳定消息 ID 可在网页展开核对。'
    if data['messages']:coverage+=' 本窗口首条 '+data['messages'][0]['time']+'，末条 '+data['messages'][-1]['time']+'。'
    coverage+=' 图片、音视频和附件正文未解析；群内通报与自述未作外部核实。'
    lines=[title,info,f'{meta["message_count"]} 条消息 · {meta["speaker_count"]} 位已识别发言人',meta['scope'],coverage]
    parts=[f'<header><small>本地微信 · 可追溯报告</small><h1>{e(title)}</h1><p>{e(info)}</p><strong>{meta["message_count"]} 条消息 · {meta["speaker_count"]} 位已识别发言人</strong><p>{e(meta["scope"])}</p><p>{e(coverage)}</p></header>']
    def item_html(item):
        text=item['text']
        if 'owner' in item: text+=f'｜负责人：{item["owner"]}｜时间要求：{item["deadline"]}｜状态：{item["status"]}'
        lines.append(text+' '+'〔来源 '+ '、'.join(str(sequence[r]) for r in item['sources'])+'〕')
        refs=''
        for r in item['sources']:
            m=byid[r]; excerpt=m['text']
            if m['details']:excerpt+='\n'+json.dumps(m['details'],ensure_ascii=False)
            refs+=f'<article><strong>消息 {sequence[r]}</strong> · <code>{e(r)}</code><p>{e(m["time"])} · {e(m["sender"])}</p><pre>{e(excerpt)}</pre></article>'
        return f'<div class="item"><p>{e(text)}</p><details><summary>核对来源（{len(item["sources"])} 条）</summary>{refs}</details></div>'
    parts.append('<section><h2>简短概览</h2>'+item_html(report['overview'])+'</section>')
    for key,label in SECTIONS.items():
        lines.append('\n'+label)
        parts.append(f'<section><h2>{label}</h2>'+(''.join(item_html(x) for x in report[key]) or '<p class="muted">未提取到有明确依据的事项。</p>')+'</section>')
        if not report[key]: lines.append('未提取到有明确依据的事项。')
    css='*{box-sizing:border-box}body{margin:0;background:#eff3f5;color:#183039;font:16px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}main{max-width:920px;margin:30px auto;padding:0 20px}header,section{background:white;border:1px solid #dce5e7;border-radius:18px;padding:28px;margin-bottom:18px}header{border-top:6px solid #16856a}h1{font-size:30px;line-height:1.4}h2{font-size:21px;color:#08745a}small,.muted{color:#657b82}p,pre{overflow-wrap:anywhere}pre{white-space:pre-wrap;font:inherit}summary{color:#14775f;cursor:pointer}details{background:#f1f6f5;padding:10px 14px;border-radius:8px}article{border-top:1px solid #cdded9}code{font-size:12px}.item{margin:18px 0}@media(max-width:600px){main{margin:12px auto;padding:0 10px}header,section{padding:20px}h1{font-size:24px}}'
    document='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'"><title>'+e(title)+'</title><style>'+css+'</style><main>'+''.join(parts)+'</main></html>'
    (folder/'index.html').write_text(document,encoding='utf8')
    source_map='\n\n## 来源编号与稳定消息 ID\n\n'+'\n'.join(f'- {sequence[r]}：`{r}`' for r in dict.fromkeys(r for item in [report['overview']]+[x for key in SECTIONS for x in report[key]] for r in item['sources']))
    (folder/'summary.md').write_text('\n\n'.join(lines)+source_map,encoding='utf8');dump(folder/'report.json',report)
    pngs=render_png(folder,lines)
    dump(folder/'validation.json',{'references_exist':True,'reviewed_message_count':len(report['reviewed_message_ids']),'semantic_review':'由总结者人工核对，程序不声称已证明语义支持','png_files':pngs,'fixture':meta['fixture']})

def render_png(folder,lines):
    from PIL import Image,ImageDraw,ImageFont
    fontpath=next((p for p in ['/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/Hiragino Sans GB.ttc','/System/Library/Fonts/STHeiti Medium.ttc'] if Path(p).exists()),None)
    if fontpath is None: raise RuntimeError('未找到已知中文字体')
    font=ImageFont.truetype(fontpath,28); titlefont=ImageFont.truetype(fontpath,40)
    width=1120; margin=60;maxheight=14000; prepared=[]
    for idx,line in enumerate(lines):
        f=titlefont if idx==0 else ImageFont.truetype(fontpath,34) if line.strip() in SECTIONS.values() else font
        for paragraph in line.split('\n'):
            current=''
            for char in paragraph:
                if f.getlength(current+char)>width-margin*2:
                    prepared.append((current,f,60 if idx==0 else 46));current=''
                current+=char
            prepared.append((current,f,60 if idx==0 else 46))
        prepared.append(('',font,22))
    pages=[];page=[];height=120
    for row in prepared:
        if height+row[2]>maxheight: pages.append(page);page=[];height=120
        page.append(row);height+=row[2]
    if page:pages.append(page)
    names=[]
    for n,rows in enumerate(pages,1):
        height=120+sum(r[2] for r in rows)+(50 if len(pages)>1 else 0)
        im=Image.new('RGB',(width,height),'#f6f9f8');d=ImageDraw.Draw(im);d.rectangle((0,0,width,12),fill='#16856a');y=55
        if len(pages)>1:d.text((margin,y),f'第 {n}/{len(pages)} 图 · 内容较长，按 14000 像素分图',font=font,fill='#16856a');y+=50
        for line,f,h in rows:d.text((margin,y),line,font=f,fill='#183039');y+=h
        name='report.png' if n==1 else f'report-{n:02d}.png';im.save(folder/name);names.append(name)
    return names
