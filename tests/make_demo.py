from pathlib import Path
from wechat_local.core import *
a,b=window(start='2026-09-27T12:00:00+08:00',end='2026-09-28T12:00:00+08:00')
texts=[('甲','建议把复盘安排在周三。'),('乙','收到建议，我还没确认时间。'),('甲','确认本次先整理问题清单。'),('乙','我负责整理问题清单，明天下午三点前发到群里。'),('丙','附件已经发出，请查收。'),('甲','周三是否开会还需要确认。'),('丙','<msg><appmsg><title>复盘资料</title><url>https://example.com/demo</url></appmsg></msg>'),('乙','')]
rows=[dict(local_id=i+1,server_id=i+1,database='message_0.db',create_time=int(a.timestamp())+i*60,sender_id=s,sender=s,body=text,local_type=49 if i==6 else 3 if i==7 else 1) for i,(s,text) in enumerate(texts)]
data=package(rows,'虚构项目讨论群','fixture@chatroom',a,b,True);ids=[m['id'] for m in data['messages']]
r={'reviewed_message_ids':ids,'overview':{'text':'群内讨论复盘准备，确认先整理问题清单；会议时间仍未确认。','sources':[ids[0],ids[2],ids[5]]},'topics':[{'text':'周三开会是建议，乙仅表示收到，尚未形成会议决定。','sources':ids[:2]}],'todos':[{'text':'整理问题清单并发到群内','owner':'乙','deadline':'明天下午三点前（原文相对时间）','status':'已认领，未见完成证据','sources':[ids[3]]}],'confirmed':[{'text':'甲确认先整理问题清单。','sources':[ids[2]]},{'text':'丙表示附件已经发出；这是群内自述，未验证实际文件。','sources':[ids[4]]}],'unresolved':[{'text':'周三是否开会待确认。','sources':[ids[5]]}],'other':[{'text':'分享了一张标题为“复盘资料”的链接卡片；另有图片，未解析图片内容。','sources':[ids[6],ids[7]]}]}
out=Path('outputs/fixture-20260928');export_files(out,data);render(out,r);print(out.resolve())
