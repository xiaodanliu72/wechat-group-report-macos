import copy
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from PIL import Image
from wechat_local.core import package,window,export_files,dump
from wechat_local.master import build_master,render_html,render,Png


class Tags(HTMLParser):
    def __init__(self):super().__init__();self.names=[]
    def handle_starttag(self,tag,attrs):self.names.append(tag)


class MasterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'report'
        a,b=window(start='2026-09-27T12:00:00+08:00',end='2026-09-28T12:00:00+08:00')
        self.data=package([dict(local_id=1,server_id=1,database='message_0.db',create_time=a.timestamp(),body='明天复核，尚未完成。',local_type=1,sender_id='u',sender='甲')],'虚构群','fixture@chatroom',a,b,True)
        self.mid=self.data['messages'][0]['id']
        item={'text':'建议明天复核，尚未完成。','sources':[self.mid]}
        self.report={'reviewed_message_ids':[self.mid],'overview':copy.deepcopy(item),'topics':[copy.deepcopy(item)],
                     'todos':[dict(**item,owner='未明确',deadline='明天（原文）',status='未完成')],
                     'confirmed':[],'unresolved':[copy.deepcopy(item)],'other':[{'text':'其他信息也不能遗漏','sources':[self.mid]}]}
        export_files(self.folder,self.data);dump(self.folder/'report.json',self.report)

    def test_invalid_report_and_unread_messages_fail_before_output(self):
        for change in ['refs','review']:
            bad=copy.deepcopy(self.report)
            if change=='refs':bad['overview']['sources']=['nonexistent']
            else:bad['reviewed_message_ids']=[]
            dump(self.folder/'report.json',bad)
            with self.assertRaises(ValueError):render(self.folder)
            self.assertFalse((self.folder/'report-master.html').exists())

    def test_no_arbitrary_facts_or_false_completed_status(self):
        for master in [{'stats':[{'num':999}]},{'lede':'凭空结论'},{'timeline':['nonexistent']},{'timeline':[['12:00','某人','事件']]},{'title_main':42}]:
            with self.subTest(master=master),self.assertRaises(ValueError):build_master(self.data,self.report,master)
        m=build_master(self.data,self.report,{})
        self.assertNotEqual(m['todos'][0]['cls'],'urgent')
        self.assertEqual(m['todos'][0]['meta'][-1],['状态','未完成',''])

    def test_safe_static_html_full_window_sources_and_other(self):
        attack='</script><script>alert(1)</script><img src="https://example.com/x" onerror="alert(2)">'
        self.data['metadata']['group_name']=attack
        self.data['messages'][0]['text']=attack
        self.data['messages'][0]['sender']=attack
        self.report['topics'][0]['text']=attack
        m=build_master(self.data,self.report,{'timeline':[self.mid],'title_accent':attack})
        path=render_html(self.folder,m);text=path.read_text()
        tags=Tags();tags.feed(text)
        self.assertNotIn('script',tags.names);self.assertNotIn('img',tags.names)
        self.assertIn('details',tags.names);self.assertIn(self.mid,text)
        self.assertIn('2026-09-27T12:00:00+08:00',text);self.assertIn('2026-09-28T12:00:00+08:00',text)
        self.assertIn('其他信息也不能遗漏',text);self.assertIn('【虚构测试】',text)
        self.assertIn('&lt;script&gt;',text)

    def test_empty_window_is_valid(self):
        a,b=window();data=package([],'空群','empty@chatroom',a,b,True)
        report={'reviewed_message_ids':[],'overview':{'text':'本机窗口内未读取到消息','sources':[]},**{k:[] for k in ('topics','todos','confirmed','unresolved','other')}}
        m=build_master(data,report,{})
        self.assertEqual(m['count'],0);self.assertTrue(render_html(self.folder,m).is_file())

    def test_oversized_individual_card_is_split_without_truncating_content(self):
        self.report['todos'][0]['text']='长任务内容，'*4200+'结尾必须保留'
        m=build_master(self.data,self.report,{})
        renderer=Png();recorded=[]
        original=renderer.b_lede
        def capture(text,**kwargs):recorded.append(text);return original(text,**kwargs)
        renderer.b_lede=capture
        files=renderer.render(self.folder,m)
        self.assertGreater(len(files),1)
        self.assertIn(self.report['todos'][0]['text'],''.join(recorded))
        for p in files:
            with Image.open(p) as im:
                self.assertEqual(im.width,1240);self.assertLessEqual(im.height,renderer.PAGE_MAX)
        self.assertEqual(files[-1].name,f'report-master-{len(files):02d}.png')

    def test_cli_render_produces_standard_and_magazine_outputs(self):
        import subprocess,sys
        result=subprocess.run([sys.executable,'-m','wechat_local.skill_workflow','render',str(self.folder)],capture_output=True,text=True,check=True)
        value=json.loads(result.stdout)
        self.assertTrue(value['ok']);self.assertEqual(len(value['files']),8)
        self.assertTrue(all(Path(p).is_file() for p in value['files']))
        self.assertEqual(value['validation']['preferred_html'],'report-master.html')

    def test_mac_serif_font_and_cited_editorial_layout(self):
        from wechat_local.master import SONGTI
        renderer=Png()
        if Path(SONGTI).is_file():
            self.assertEqual(renderer.f_title.getname(),('Songti SC','Black'))
            self.assertEqual(renderer.f_h3.getname(),('Songti SC','Bold'))
        self.report['topics'][0]['text']='复核安排：建议明天复核，尚未完成。'
        m=build_master(self.data,self.report,{})
        self.assertEqual(m['topics'][0]['title'],'复核安排')
        self.assertIn('尚未完成',m['topics'][0]['body'])
        self.assertIn('〔来源 1〕',m['topics'][0]['body'])
        self.assertIn('2026-09-27',m['date_label']);self.assertIn('2026-09-28',m['date_label'])

    def test_timeline_excerpt_keeps_full_original_in_html(self):
        self.data['messages'][0]['text']='原文'*100+'必须核对的结尾'
        m=build_master(self.data,self.report,{'timeline':[self.mid]})
        self.assertIn('摘录，全文见来源',m['timeline'][0][2])
        self.assertIn('必须核对的结尾',render_html(self.folder,m).read_text())
        renderer=Png();height,draw=renderer.b_timeline(m['timeline'])
        im=renderer.Image.new('RGB',(renderer.WIDTH,height+512));end=draw(renderer.ImageDraw.Draw(im),0)
        self.assertLessEqual(end,im.height)

    def test_report_above_old_height_limit_stays_one_clear_image(self):
        self.report['topics'][0]['text']='计划复核，'*3000+'末尾仍然保留'
        renderer=Png()
        files=renderer.render(self.folder,build_master(self.data,self.report,{}))
        self.assertEqual(len(files),1)
        with Image.open(files[0]) as im:
            self.assertEqual(im.width,1240)
            self.assertGreater(im.height,12000)
            self.assertLessEqual(im.height,16000)
            self.assertEqual(im.getpixel((5,im.height-50)),(25,23,19))
