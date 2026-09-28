"""Agent-facing workflow: calendar windows, bounded review batches, final artifacts."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from .core import TZ,window,validate_messages,render,SECTIONS,dump

PROJECT=Path(__file__).resolve().parents[1]

def caller_path(value):
    path=Path(value).expanduser()
    if not path.is_absolute():path=Path(os.environ.get('WECHAT_REPORT_CALLER_CWD',os.getcwd()))/path
    return path.resolve()

def resolve_window(day=None,hours=None,start=None,end=None,now=None):
    now=(now or dt.datetime.now(TZ)).astimezone(TZ).replace(microsecond=0)
    if day and any(v is not None for v in (hours,start,end)):
        raise ValueError('--day 不能与 --hours/--start/--end 同时使用')
    if hours is not None and (start or end):raise ValueError('--hours 不能与起止时间同时使用')
    if bool(start)!=bool(end):raise ValueError('自定义时间范围需同时提供 --start 和 --end')
    if day:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):raise ValueError('日期必须为 YYYY-MM-DD')
        date=dt.date.fromisoformat(day)
        a=dt.datetime.combine(date,dt.time(),TZ);requested_end=a+dt.timedelta(days=1)
    elif start:
        a,requested_end=window(start=start,end=end)
    else:
        a,requested_end=now-dt.timedelta(hours=hours or 24),now
    if a>=now:raise ValueError('不能读取尚未发生的时间范围')
    b=min(requested_end,now)
    if a>=b:raise ValueError('时间范围为空')
    note=''
    if b<requested_end:note='请求范围尚未结束；本次只统计截至 '+b.isoformat()+' 的已同步记录，不是该范围的完整记录。'
    return a,b,{'requested_day':day,'requested_end':requested_end.isoformat(),'partial_period':b<requested_end,'coverage_note':note}

def prepare(folder,batch_chars=6000):
    folder=Path(folder).resolve();source=folder/'messages.json';data=json.loads(source.read_text())
    validate_messages(data)
    if not 500<=batch_chars<=12000:raise ValueError('批次字符上限应为 500 到 12000')
    review=folder/'review';review.mkdir(exist_ok=True,mode=0o700)
    chunks=[];current=''
    for i,m in enumerate(data['messages'],1):
        text=json.dumps({'sequence':i,**m},ensure_ascii=False)+'\n'
        if current and len(current)+len(text)>batch_chars:chunks.append(current);current=''
        if len(text)>batch_chars:
            chunks.extend(text[n:n+batch_chars] for n in range(0,len(text),batch_chars))
        else:current+=text
    if current:chunks.append(current)
    parts=[]
    for i,chunk in enumerate(chunks,1):
        p=review/f'part-{i:04d}.txt';p.write_text(chunk);p.chmod(0o600)
        parts.append({'number':i,'path':str(p),'characters':len(chunk),'sha256':hashlib.sha256(chunk.encode()).hexdigest()})
    manifest={'message_count':len(data['messages']),'part_count':len(parts),'metadata':data['metadata'],'messages_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'parts':parts,'note':'按顺序阅读所有批次；超长单条消息可能跨批次，连续拼接可恢复完整 JSONL。批次清单不证明智能体已阅读。'}
    dump(review/'manifest.json',manifest)
    template=folder/'report.template.json'
    if not template.exists():dump(template,{'reviewed_message_ids':[],'overview':{'text':'','sources':[]},**{k:[] for k in SECTIONS}})
    return manifest

def read_part(folder,number):
    folder=Path(folder).resolve();manifest=json.loads((folder/'review/manifest.json').read_text())
    if hashlib.sha256((folder/'messages.json').read_bytes()).hexdigest()!=manifest['messages_sha256']:
        raise ValueError('消息文件已变化，需重新 prepare')
    if not 1<=number<=manifest['part_count']:raise ValueError('批次编号超出范围')
    part=manifest['parts'][number-1];p=Path(part['path']).resolve()
    if p.parent!=folder/'review':raise ValueError('批次路径超出当前报告目录')
    text=p.read_text()
    if hashlib.sha256(text.encode()).hexdigest()!=part['sha256']:raise ValueError('批次内容已变化，需重新 prepare')
    return text

def main():
    os.umask(0o077)
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='action',required=True)
    ex=sub.add_parser('export',help='只用现有钥匙串密钥导出并准备总结批次')
    ex.add_argument('--group');ex.add_argument('--group-id');ex.add_argument('--db-root',type=caller_path)
    ex.add_argument('--day');ex.add_argument('--hours',type=int,choices=[24,48,72]);ex.add_argument('--start');ex.add_argument('--end');ex.add_argument('--out',type=caller_path);ex.add_argument('--dry-run',action='store_true')
    pre=sub.add_parser('prepare',help='对已有 messages.json 生成完整阅读批次')
    pre.add_argument('folder',type=caller_path);pre.add_argument('--batch-chars',type=int,default=6000)
    rd=sub.add_parser('read',help='输出一个完整批次；智能体必须依次读完全部批次')
    rd.add_argument('folder',type=caller_path);rd.add_argument('--part',type=int,required=True)
    re_=sub.add_parser('render',help='验证智能体编写的 report.json 并生成六文件')
    re_.add_argument('folder',type=caller_path);re_.add_argument('--report',type=caller_path)
    a=ap.parse_args()
    if a.action=='read':print(read_part(a.folder,a.part),end='');return
    if a.action=='prepare':
        m=prepare(a.folder,a.batch_chars);print(json.dumps({'directory':str(a.folder.resolve()),'message_count':m['message_count'],'part_count':m['part_count'],'manifest':str(a.folder.resolve()/'review/manifest.json')},ensure_ascii=False));return
    if a.action=='render':
        folder=a.folder.resolve();report=a.report or folder/'report.json'
        render(folder,json.loads(report.read_text()))
        validation=json.loads((folder/'validation.json').read_text())
        names=['messages.json','messages.txt','report.json','summary.md','index.html',*validation['png_files']]
        if any(not (folder/name).is_file() for name in names):raise RuntimeError('交付文件不完整')
        print(json.dumps({'ok':True,'files':[str(folder/n) for n in names],'validation':validation},ensure_ascii=False));return
    name=a.group or input('请输入完整群名：').strip()
    if not name:raise ValueError('群名不能为空')
    start,end,selection=resolve_window(a.day,a.hours,a.start,a.end)
    out=a.out or PROJECT/'outputs'/('report-'+dt.datetime.now(TZ).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    plan={'group':name,'group_id':a.group_id,'start':start.isoformat(),'end':end.isoformat(),'timezone':'Asia/Shanghai','interval':'[start, end)','output':str(out.resolve()),**selection}
    if a.dry_run:print(json.dumps({'dry_run':True,**plan},ensure_ascii=False,indent=2));return
    if out.exists():raise ValueError('输出目录已存在，请指定新目录')
    from .preparation import select_account
    from .keychain_store import export
    root,_=select_account(Path.home()/'Library/Containers/com.tencent.xinWeChat/Data',a.db_root)
    data=export(root,name,a.group_id,start,end,out)
    data['metadata'].update(selection)
    if selection['coverage_note']:data['metadata']['scope']+=' '+selection['coverage_note']
    dump(out/'messages.json',data)
    manifest=prepare(out)
    print(json.dumps({'ok':True,**plan,'message_count':len(data['messages']),'part_count':manifest['part_count'],'manifest':str(out.resolve()/'review/manifest.json'),'summary_pending':True,'client_launch_performed':False},ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as e:
        print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),file=sys.stderr);raise SystemExit(2)
