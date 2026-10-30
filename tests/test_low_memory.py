"""Offline tests. Real local processes; OCR/Discord/Firestore APIs are doubles.

Run: python -m unittest discover -s tests -v
Numerical/parser cases use installed numpy/pandas, but never a real bot token.
"""
from __future__ import annotations
import ast
import asyncio
import base64
from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import resource_runtime as runtime
from resource_workers import pdf_job, excel_job


def extract_ocr(worker, directory):
    tree = ast.parse((ROOT / 'cogs/automod.py').read_text())
    wanted = {'OCRLine', 'OCRResult', '_Job', 'OCRPool', '_parse_result'}
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes += [n for n in tree.body if getattr(n, 'name', None) in wanted]
    ns = dict(asyncio=asyncio, base64=base64, deque=deque, dataclass=dataclass,
              asdict=asdict, field=field, hashlib=hashlib, json=json, math=math,
              os=os, Path=Path, sys=sys, time=time, Any=object,
              MAX_IMAGE_BYTES=8*1024*1024, MAX_OCR_LINES=200,
              MAX_OCR_CHARACTERS=8000, MAX_DIMENSION=8192,
              _SINGLE_FILE=worker, _PROJECT_ROOT=directory,
              __name__=__name__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<ocr-source>', 'exec'), ns)
    return ns


OCR_STUB = r'''
import json,sys,time,os
print(json.dumps({'type':'ready','status':'ready'}),flush=True)
for line in sys.stdin:
    r=json.loads(line)
    if r['filename']=='crash': os._exit(3)
    if r['filename']=='hang': time.sleep(20)
    if r['filename']=='slow': time.sleep(.2)
    print(json.dumps({'status':'ok','lines':[{'text':'test','confidence':.99,
          'bbox':[[0,0],[1,0],[1,1],[0,1]]}],'width':10,'height':10}),flush=True)
'''


def extract_parser():
    tree=ast.parse((ROOT/'cogs/player_Record.py').read_text())
    helpers={'clean_player_name','safe_float','add_innings','col_idx_to_letter'}
    nodes=[n for n in tree.body if getattr(n,'name',None) in helpers]
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PlayerRecord')
    method=next(n for n in cls.body if getattr(n,'name',None)=='_parse_single_sheet')
    nodes.append(method)
    ns={'re':re,'math':math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'<record-source>','exec'),ns)
    return ns


class CacheTests(unittest.TestCase):
    def test_capacity_and_recent_access(self):
        c=runtime.TTLBoolCache(2); c['a']=True;c['b']=False
        self.assertTrue(c['a']);c['c']=True
        self.assertNotIn('b',c);self.assertEqual(len(c),2)

    def test_expiry_and_false_values(self):
        clock=[0];c=runtime.TTLBoolCache(2,10,lambda:clock[0]);c['a']=False
        self.assertIn('a',c);self.assertFalse(c['a']);clock[0]=10
        self.assertNotIn('a',c);self.assertEqual(len(c),0)

    def test_invalid_and_clamped_environment(self):
        with patch.dict(os.environ,{'CACHE':'invalid'}):
            self.assertEqual(runtime.env_int('CACHE',100,1,1000),100)
        with patch.dict(os.environ,{'CACHE':'99999'}):
            self.assertEqual(runtime.env_int('CACHE',100,1,1000),1000)

    def test_reading_missing_cache_does_not_insert(self):
        c=runtime.TTLBoolCache();self.assertIsNone(c.get('absent'));self.assertEqual(len(c),0)


class StructureTests(unittest.TestCase):
    def test_light_modules_do_not_import_ai_or_pandas(self):
        code="import resource_runtime, resource_workers.pdf_job, resource_workers.excel_job,sys; assert not any(x in sys.modules for x in ('torch','easyocr','pandas','numpy','sentence_transformers','google.genai'))"
        subprocess.run([sys.executable,'-S','-c',code],cwd=ROOT,check=True,timeout=15)

    def test_existing_command_names_and_signatures_preserved(self):
        baseline=json.loads((ROOT/'tests/low_memory_commands_baseline.json').read_text())
        found=0
        for name,commands in baseline.items():
            tree=ast.parse((ROOT/name).read_text())
            funcs={n.name:n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
            for command in commands:
                self.assertIn(command['function'],funcs)
                node=funcs[command['function']]
                self.assertEqual(ast.dump(node.args,include_attributes=False),command['args'])
                self.assertTrue(any(isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr=='command'
                    and next((k.value.value for k in d.keywords if k.arg=='name' and isinstance(k.value,ast.Constant)), node.name)==command['name']
                    for d in node.decorator_list))
                found+=1
        self.assertEqual(found,72)

    def test_member_cache_not_disabled(self):
        s=(ROOT/'bot.py').read_text()
        self.assertIn('INTENTS.members = True',s)
        self.assertNotIn('MemberCacheFlags.none',s)

    def test_models_not_instantiated_at_import(self):
        tree=ast.parse((ROOT/'cogs/pdf_ai.py').read_text())
        for node in tree.body:
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                self.assertNotIn('sentence_transformers',ast.unparse(node))
                self.assertNotIn('numpy',ast.unparse(node))
            if isinstance(node,ast.Assign):
                self.assertNotIn('SentenceTransformer',ast.unparse(node))
        tree=ast.parse((ROOT/'cogs/player_Record.py').read_text())
        self.assertFalse(any(isinstance(n,ast.Import) and any(a.name=='pandas' for a in n.names) for n in tree.body))

    def test_docker_cpu_install_and_correct_entrypoint(self):
        docker=(ROOT/'Dockerfile').read_text()
        self.assertIn('download.pytorch.org/whl/cpu',docker)
        self.assertIn('assert torch.version.cuda is None',docker)
        self.assertEqual((ROOT/'Procfile').read_text().strip(),'worker: python bot.py')


class OCRLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        worker=self.root/'ocr.py';worker.write_text(OCR_STUB)
        self.ns=extract_ocr(worker,self.root)
        self.pool=self.ns['OCRPool'](self.root,idle_seconds=.08,timeout_seconds=1)

    async def asyncTearDown(self):
        await self.pool.close();self.tmp.cleanup()

    async def test_lazy_start_has_no_child_and_not_falsely_ready(self):
        self.assertFalse(await self.pool.start(eager=False))
        self.assertEqual(self.pool.readiness,'standby')
        self.assertIsNone(self.pool.health['worker_pid'])

    async def test_first_job_then_idle_reaps_and_next_job_restarts(self):
        await self.pool.start(eager=False)
        result=await self.pool.submit(b'bytes','x')
        self.assertEqual(result.status,'ok');self.assertEqual(result.text,'test')
        pid=self.pool.health['worker_pid'];self.assertTrue(pid)
        await asyncio.sleep(.2)
        self.assertEqual(self.pool.readiness,'standby');self.assertFalse(Path(f'/proc/{pid}').exists())
        result=await self.pool.submit(b'bytes','x')
        self.assertEqual(result.status,'ok')
        self.assertNotEqual(pid,self.pool.health['worker_pid'])

    async def test_eager_diagnostic_also_releases_without_image(self):
        self.assertTrue(await self.pool.start())
        pid=self.pool.health['worker_pid'];await asyncio.sleep(.2)
        self.assertFalse(self.pool.ready);self.assertFalse(Path(f'/proc/{pid}').exists())

    async def test_clean_idle_starts_do_not_trigger_crash_limit(self):
        await self.pool.start(eager=False)
        for _ in range(4):
            self.assertEqual((await self.pool.submit(b'x','ok')).status,'ok')
            await asyncio.sleep(.15)
        self.assertNotEqual(self.pool.readiness,'cooldown')

    async def test_active_job_not_killed_by_idle_timer(self):
        await self.pool.start(eager=False)
        self.assertEqual((await self.pool.submit(b'x','slow')).status,'ok')

    async def test_timeout_kills_and_enters_cooldown(self):
        self.pool.timeout_seconds=.1
        await self.pool.start(eager=False)
        result=await self.pool.submit(b'x','hang')
        self.assertEqual(result.status,'timeout');self.assertIsNone(self.pool._proc)
        self.assertEqual((await self.pool.submit(b'x','x')).status,'unavailable')

    async def test_crash_not_reported_as_safe(self):
        await self.pool.start(eager=False)
        self.assertEqual((await self.pool.submit(b'x','crash')).status,'error')
        self.assertFalse(self.pool.ready)

    async def test_large_and_empty_images_rejected_without_worker(self):
        await self.pool.start(eager=False)
        self.assertEqual((await self.pool.submit(b'','x')).status,'invalid')
        self.assertEqual((await self.pool.submit(b'x'*(8*1024*1024+1),'x')).status,'too_large')
        self.assertIsNone(self.pool._proc)

    async def test_close_reaps_active_worker_and_is_idempotent(self):
        await self.pool.start()
        pid=self.pool.health['worker_pid']
        task=asyncio.create_task(self.pool.submit(b'x','slow'));await asyncio.sleep(.04)
        await self.pool.close();await self.pool.close()
        self.assertEqual((await task).status,'closed')
        self.assertFalse(Path(f'/proc/{pid}').exists())

    async def test_persistent_override(self):
        self.pool.idle_seconds=-1
        self.assertTrue(await self.pool.start());pid=self.pool.health['worker_pid']
        await asyncio.sleep(.2)
        self.assertEqual(self.pool.health['worker_pid'],pid)


JOB_STUB=r'''
import json,sys,os,time
r=json.load(sys.stdin)
if r.get('pidfile'):
    open(r['pidfile'],'w').write(str(os.getpid()))
if r.get('mode')=='hang': time.sleep(30)
if r.get('mode')=='large':
    print('x'*3000000)
elif r.get('mode')=='bad':
    print(json.dumps({'ok':False,'error':'failed','kind':'PermissionDenied'}))
else:
    print(json.dumps({'ok':True,'answer':42}))
'''

class WorkManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        d=self.root/'resource_workers';d.mkdir();(d/'__init__.py').write_text('')
        (d/'pdf_job.py').write_text(JOB_STUB)
        self.patch=patch.object(runtime,'ROOT',self.root);self.patch.start()
        self.manager=runtime.WorkManager()

    async def asyncTearDown(self):
        await self.manager.close();self.patch.stop();self.tmp.cleanup()

    async def test_success_finishes_and_releases_slot(self):
        async with self.manager.slot('test') as job:
            self.assertEqual((await job.run('pdf_job',{}))['answer'],42)
        self.assertEqual(len(self.manager._processes),0);self.assertIsNone(self.manager.active)

    async def test_busy_rejected_without_queue(self):
        async with self.manager.slot('first'):
            with self.assertRaises(runtime.WorkerBusy):
                async with self.manager.slot('second'):
                    self.fail('must not run')

    async def test_unknown_worker_rejected(self):
        async with self.manager.slot('test') as job:
            with self.assertRaises(ValueError): await job.run('../../bot',{})

    async def test_timeout_reaps_process(self):
        pidfile=self.root/'pid'
        async with self.manager.slot('test') as job:
            with self.assertRaises(runtime.WorkerError):
                await job.run('pdf_job',{'mode':'hang','pidfile':str(pidfile)},timeout=2)
        self.assertFalse(Path('/proc/'+pidfile.read_text()).exists())

    async def test_cancellation_reaps_process(self):
        pidfile=self.root/'pid'
        async def run():
            async with self.manager.slot('test') as job:
                await job.run('pdf_job',{'mode':'hang','pidfile':str(pidfile)},timeout=15)
        task=asyncio.create_task(run())
        for _ in range(100):
            if pidfile.exists():break
            await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(Path('/proc/'+pidfile.read_text()).exists())

    async def test_oversized_output_does_not_hang(self):
        async with self.manager.slot('test') as job:
            with self.assertRaises(runtime.WorkerError):await job.run('pdf_job',{'mode':'large'},timeout=5)
        self.assertEqual(len(self.manager._processes),0)

    async def test_worker_failure_sanitized(self):
        async with self.manager.slot('test') as job:
            with self.assertRaises(runtime.WorkerError):await job.run('pdf_job',{'mode':'bad'})

    async def test_close_stops_current_job(self):
        pidfile=self.root/'pid'
        async def run():
            async with self.manager.slot('test') as job:
                await job.run('pdf_job',{'mode':'hang','pidfile':str(pidfile)},timeout=15)
        task=asyncio.create_task(run())
        for _ in range(100):
            if pidfile.exists():break
            await asyncio.sleep(.01)
        await self.manager.close()
        with self.assertRaises(runtime.WorkerError):await task
        self.assertFalse(Path('/proc/'+pidfile.read_text()).exists())


class PDFTests(unittest.TestCase):
    def test_chunks_identical_to_original(self):
        text='제1조 '+ 'A'*1500
        self.assertEqual(pdf_job.split_text(text),[text[i:i+500] for i in range(0,len(text),500)])
        self.assertEqual(pdf_job.extract_article('제 12 조 규칙'),'제 12 조')

    def test_model_name_preserved(self):
        self.assertEqual(pdf_job.MODEL_NAME,'all-MiniLM-L6-v2')

    def test_top_k_matches_original_full_sort(self):
        import numpy as np
        rng=np.random.default_rng(2026)
        query=rng.random(384).astype('float32')
        rows=[{'embedding':rng.random(384).tolist(),'text':str(i)} for i in range(100)]
        docs=[types.SimpleNamespace(to_dict=lambda row=row:row) for row in rows]
        expected=sorted(rows,key=lambda d:np.dot(query,np.array(d['embedding']))/(np.linalg.norm(query)*np.linalg.norm(np.array(d['embedding']))),reverse=True)[:3]
        self.assertEqual(pdf_job.top_matches(query,iter(docs)),expected)

    def test_equal_scores_retain_original_order(self):
        docs=[types.SimpleNamespace(to_dict=lambda i=i:{'embedding':[1.,0.],'text':str(i)}) for i in range(7)]
        self.assertEqual([x['text'] for x in pdf_job.top_matches([1.,0.],docs)],['0','1','2'])

    def test_corrupt_embeddings_do_not_crash_search(self):
        rows=[{}, {'embedding':[0,0]}, {'embedding':[float('nan'),0]}, {'embedding':[1]}, {'embedding':[1,0]}]
        docs=[types.SimpleNamespace(to_dict=lambda row=row:row) for row in rows]
        self.assertEqual(len(pdf_job.top_matches([1.,0.],docs)),1)

    def test_register_keeps_schema_and_batches(self):
        import numpy as np
        stored=[];calls=[]
        class Model:
            def encode(self,chunks,**kw):
                calls.append(len(chunks));return np.ones((len(chunks),384),dtype='float32')
        db=types.SimpleNamespace(collection=lambda name:types.SimpleNamespace(add=stored.append))
        with patch.object(pdf_job,'extract_pdf',return_value='A'*10000):
            result=pdf_job.register_pdf(db,{'path':'not-used','source':'test.pdf'},lambda:Model())
        self.assertEqual(result['chunks'],20);self.assertEqual(calls,[8,8,4])
        self.assertEqual(set(stored[0]),{'text','source','article','embedding'})
        self.assertEqual(len(stored[0]['embedding']),384)

    def test_missing_gemini_key_never_loads_model(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(pdf_job.JobError) as error:
                pdf_job.answer(None,{'question':'hello'},lambda:self.fail('model loaded'))
        self.assertEqual(error.exception.code,'missing_key')


class ParserTests(unittest.TestCase):
    def setUp(self):self.ns=extract_parser()

    def test_innings_unchanged(self):
        self.assertEqual(self.ns['add_innings'](1.2,2.2),4.1)
        self.assertEqual(self.ns['add_innings'](0.2,0.1),1.0)

    def test_nonfinite_numbers_do_not_leave_nan_in_sheet(self):
        self.assertEqual(self.ns['safe_float']('nan'),0)
        self.assertEqual(self.ns['safe_float']('inf'),0)
        self.assertEqual(self.ns['safe_float']('1,234'),1234)

    def test_actual_csv_parsing(self):
        rows=[['타자 기록','','','','',''],['선수명','타수','안타','타점','득점','도루'],
              ['Batter[1]',4,2,1,2,1],['합계','','','','',''],
              ['투수 기록','','','','',''],['선수명','이닝','타자','피안타','삼진','자책점'],
              ['Pitcher (P)',1.2,7,2,2,1],['합계','','','','','']]
        import csv
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sample.csv'
            with p.open('w',encoding='utf-8-sig',newline='') as f:csv.writer(f).writerows(rows)
            result=excel_job.parse_file(p,lambda *args:self.ns['_parse_single_sheet'](None,*args))
        self.assertEqual(result['batting'][0]['선수명'],'Batter')
        self.assertEqual(result['batting'][0]['안타'],2)
        self.assertEqual(result['pitching'][0]['이닝'],1.2)

    def test_debug_logs_are_bounded(self):
        logs=excel_job.LimitedLogs()
        for i in range(2000):logs.append('x'*2000)
        self.assertEqual(len(logs),100);self.assertEqual(len(logs[0]),400)

    def test_oversized_file_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'large.csv'
            with p.open('wb') as f:f.truncate(excel_job.MAX_FILE_BYTES+1)
            with self.assertRaises(excel_job.JobError):excel_job.parse_file(p,lambda *a:None)


if __name__=='__main__':unittest.main(verbosity=2)
