import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from wechat_local.core import TZ,package,export_files
from wechat_local.skill_workflow import resolve_window,prepare,read_part,PROJECT


class SkillWindowTests(unittest.TestCase):
    now=dt.datetime(2026,9,28,16,0,tzinfo=TZ)

    def test_calendar_days_and_exclusive_end(self):
        for day,end in [('2026-09-27','2026-09-28'),('2025-12-31','2026-01-01'),('2024-02-29','2024-03-01')]:
            with self.subTest(day=day):
                a,b,selection=resolve_window(day=day,now=self.now)
                self.assertEqual(a.isoformat(),day+'T00:00:00+08:00')
                self.assertEqual(b.isoformat(),end+'T00:00:00+08:00')
                self.assertFalse(selection['partial_period'])
                rows=[dict(local_id=i+1,server_id=i+1,database='message_0.db',create_time=stamp,body='虚构边界',local_type=1,sender_id='u',sender='测试') for i,stamp in enumerate([a.timestamp()-1,a.timestamp(),b.timestamp()-1,b.timestamp()])]
                data=package(rows,'测试群','test@chatroom',a,b,True)
                self.assertEqual([m['server_id'] for m in data['messages']],['2','3'])

    def test_today_clamped_future_rejected_and_bad_combinations(self):
        a,b,s=resolve_window(day='2026-09-28',now=self.now)
        self.assertEqual(b,self.now);self.assertTrue(s['partial_period'])
        self.assertIn('不是该范围的完整记录',s['coverage_note'])
        for args in [dict(day='2026-09-29'),dict(day='2026-09-27',hours=24),dict(hours=24,start='2026-09-27'),dict(start='2026-09-27'),dict(day='2026-9-27'),dict(day='2026-02-29'),dict(start='2026-09-28',end='2026-09-27')]:
            with self.subTest(args=args),self.assertRaises(ValueError):resolve_window(now=self.now,**args)

    def test_rolling_default_and_custom_timezone(self):
        for hours in [None,24,48,72]:
            a,b,s=resolve_window(hours=hours,now=self.now)
            self.assertEqual(b-a,dt.timedelta(hours=hours or 24));self.assertEqual(b,self.now)
            self.assertFalse(s['partial_period'])
        a,b,_=resolve_window(start='2026-09-27T01:00:00Z',end='2026-09-27T18:00:00',now=self.now)
        self.assertEqual(a.hour,9);self.assertEqual(b.hour,18)
        a,b,s=resolve_window(start='2026-09-27T18:00:00',end='2026-09-29T18:00:00',now=self.now)
        self.assertEqual(b,self.now);self.assertTrue(s['partial_period'])


class SkillReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'review-case'
        self.a,self.b,_=resolve_window(day='2026-09-27',now=SkillWindowTests.now)

    def write_export(self,bodies):
        rows=[dict(local_id=i+1,server_id=i+1,database='message_0.db',create_time=self.a.timestamp()+i,body=body,local_type=1,sender_id='u1',sender='甲') for i,body in enumerate(bodies)]
        data=package(rows,'虚构批次群','batch@chatroom',self.a,self.b,True)
        export_files(self.folder,data)
        return data

    def test_batches_preserve_every_message_and_long_body(self):
        data=self.write_export(['首条','跨批次完整内容🧘'*450,'尾部不能遗漏']*5)
        manifest=prepare(self.folder,batch_chars=500)
        self.assertGreater(manifest['part_count'],20)
        self.assertTrue(all(p['characters']<=500 for p in manifest['parts']))
        joined=''.join(read_part(self.folder,i) for i in range(1,manifest['part_count']+1))
        lines=[json.loads(line) for line in joined.splitlines()]
        self.assertEqual(lines,[{'sequence':i,**m} for i,m in enumerate(data['messages'],1)])
        self.assertEqual(manifest['message_count'],len(data['messages']))

    def test_tampered_source_and_part_rejected(self):
        self.write_export(['一条虚构消息']);manifest=prepare(self.folder)
        part=Path(manifest['parts'][0]['path']);part.write_text('替换内容')
        with self.assertRaisesRegex(ValueError,'批次内容已变化'):read_part(self.folder,1)
        prepare(self.folder)
        with (self.folder/'messages.json').open('a') as f:f.write('\n')
        with self.assertRaisesRegex(ValueError,'消息文件已变化'):read_part(self.folder,1)

    def test_empty_export_and_invalid_part(self):
        self.write_export([]);manifest=prepare(self.folder)
        self.assertEqual(manifest['part_count'],0)
        self.assertEqual(json.loads((self.folder/'report.template.json').read_text())['reviewed_message_ids'],[])
        with self.assertRaises(ValueError):read_part(self.folder,1)

    def test_wrapper_uses_caller_paths_without_reading_wechat(self):
        wrapper=PROJECT/'scripts/run.py'
        if not wrapper.exists():wrapper=PROJECT/'skills/wechat-group-report/scripts/run.py'
        result=subprocess.run([sys.executable,str(wrapper),'export','--group','虚构群','--day','2026-09-27','--out','relative-output','--dry-run'],cwd=self.temp.name,capture_output=True,text=True,check=True)
        plan=json.loads(result.stdout)
        self.assertTrue(plan['dry_run'])
        self.assertEqual(Path(plan['output']),Path(self.temp.name).resolve()/'relative-output')
        self.assertFalse(Path(plan['output']).exists())
