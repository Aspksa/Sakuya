"""HTTP integration checks for the local browser interface and persisted settings."""
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import test_launcher as fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'bootstrap'))
from common import HTTP
spec = importlib.util.spec_from_file_location('web_core_tests', ROOT / 'app/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name).resolve()
        (self.data / 'logs').mkdir()
        (self.data / 'logs/sakuya.log').write_text('Sakuya test\n', encoding='utf-8')
        self.environment = patch.dict(os.environ, {'SAKUYA_DATA_DIR':str(self.data)})
        self.environment.start()
        self.settings = {'port':0, 'autostart_browser':False, 'preserved':'keep this field'}
        core.save(self.data,self.settings)
        self.logger = logging.getLogger('web-tests')
        self.logger.addHandler(logging.NullHandler())
        self.stop = threading.Event()
        self.server = core.create_server(self.data,'sakuya.test','test-session','native-secret',self.stop,self.settings,self.logger)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.bootstrap = self.get('/api/bootstrap')

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        self.environment.stop(); self.temp.cleanup()

    def get(self,path):
        with HTTP.open(self.url + path,timeout=3) as response:
            return json.load(response)

    def post(self,path,body,headers=None):
        values = {'Content-Type':'application/json','Origin':self.url,'X-Sakuya-CSRF':self.bootstrap['csrf']}
        if headers is not None: values.update(headers)
        values = {key:value for key,value in values.items() if value is not None}
        request = urllib.request.Request(self.url + path,data=json.dumps(body).encode(),headers=values)
        try:
            with HTTP.open(request,timeout=4) as response:
                return response.status,json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code,json.load(error)

    def test_bootstrap_real_state_and_static_assets(self):
        self.assertEqual(self.bootstrap['pid'],os.getpid())
        self.assertEqual(self.bootstrap['url'],self.url)
        self.assertEqual(self.bootstrap['python_version'],sys.version.split()[0])
        self.assertNotEqual(self.bootstrap['csrf'],'native-secret')
        self.assertNotIn('preserved',self.bootstrap['settings'])
        for path,mime in [('/','text/html'),('/assets/app.css','text/css'),('/assets/app.js','text/javascript'),('/favicon.svg','image/svg+xml')]:
            with self.subTest(path=path),HTTP.open(self.url+path,timeout=3) as response:
                self.assertIn(mime,response.headers['Content-Type'])
                self.assertIn("connect-src 'self'",response.headers['Content-Security-Policy'])
                self.assertTrue(response.read())
        with self.assertRaises(urllib.error.HTTPError) as error:
            HTTP.open(self.url+'/assets/../../bootstrap/common.py',timeout=3)
        error.exception.close()
        self.assertEqual(error.exception.code,404)

    def test_browser_writes_require_same_origin_and_csrf(self):
        payload={'port':43900,'autostart_browser':True}
        for headers in ({'Origin':None},{'Origin':'https://evil.example'}, {'X-Sakuya-CSRF':'wrong'},
                        {'X-Sakuya-CSRF':'é'},{'Sec-Fetch-Site':'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post('/api/settings',payload,headers)[0],403)
        self.assertEqual(self.settings['port'],0)
        self.assertFalse(self.stop.is_set())

    def test_settings_are_validated_before_mutation(self):
        for payload in ({'port':True,'autostart_browser':False},{'port':65536,'autostart_browser':False},
                        {'port':12.3,'autostart_browser':False},{'port':0,'autostart_browser':'true'},
                        {'port':0,'autostart_browser':False,'unexpected':1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post('/api/settings',payload)[0],400)
        self.assertEqual(json.loads((self.data/'settings.json').read_text())['port'],0)

    def test_save_failure_keeps_old_settings_and_success_updates_disk(self):
        with patch.object(core,'save',side_effect=PermissionError('disk denied')):
            self.assertEqual(self.post('/api/settings',{'port':43900,'autostart_browser':True})[0],500)
        self.assertEqual(self.settings['port'],0)
        code,result=self.post('/api/settings',{'port':43900,'autostart_browser':True})
        self.assertEqual(code,200)
        self.assertTrue(result['restart_required'])
        self.assertEqual(self.get('/api/status')['settings']['port'],43900)
        self.assertEqual(self.get('/api/status')['url'],self.url)
        saved=json.loads((self.data/'settings.json').read_text())
        self.assertEqual(saved['preserved'],'keep this field')
        self.assertEqual(saved['port'],43900)

    def test_native_actions_are_bounded_and_startup_routes_to_existing_core(self):
        self.assertEqual(self.post('/api/open',{'target':'../../secrets'})[0],400)
        self.assertEqual(self.post('/api/startup',{'enabled':'yes'})[0],400)
        if os.name=='nt':
            with patch.object(core.startup,'configure') as configure:
                self.assertEqual(self.post('/api/startup',{'enabled':True})[0],200)
                configure.assert_called_once_with('enable',quiet=True)
        else:
            self.assertEqual(self.post('/api/startup',{'enabled':True})[0],501)
            self.assertEqual(self.post('/api/open',{'target':'data'})[0],501)
        self.assertEqual(self.post('/api/shutdown',{}, {'X-Sakuya-CSRF':self.bootstrap['csrf']})[0],403)
        self.assertFalse(self.stop.is_set())

    def test_tool_manifest_and_update_status_are_read_only(self):
        tools=self.get('/api/tools')
        self.assertFalse(tools['arbitrary_commands'])
        self.assertEqual(tools['actions'], ['open-data','open-documents','open-log'] if os.name=='nt' else [])
        status=self.get('/api/update/status')
        self.assertFalse(status['automatic_install'])
        self.assertTrue(status['recovery_available'])
        self.assertEqual(status['repository'],'Aspksa/Sakuya')
        self.assertEqual(self.post('/api/open',{'target':'../../unsafe'})[0],400)
        self.assertEqual(self.post('/api/open',{'target':123})[0],400)

    def test_update_prepare_rejects_invalid_and_stale_commits(self):
        from unittest.mock import patch
        good = 'a'*40
        self.assertEqual(self.post('/api/update/prepare', {'commit':'../../evil'})[0],400)
        with patch.object(core.updater,'check_remote', return_value={'reachable': True, 'commit': 'b'*40}):
            code, result = self.post('/api/update/prepare', {'commit':good})
            self.assertEqual(code,409)
            self.assertIn('GitHub',result['error'])
        with patch.object(core.updater,'check_remote', return_value={'reachable': False}):
            self.assertEqual(self.post('/api/update/prepare', {'commit':good})[0],409)

    def test_update_prepare_arms_only_verified_candidate(self):
        from unittest.mock import patch
        good = 'a'*40
        with patch.object(core.updater,'check_remote', return_value={'reachable': True, 'commit':good}), \
             patch.object(core,'stage_release') as stage, \
             patch.object(core,'verify_staged_release',return_value=True) as verify, \
             patch.object(core,'recover') as recover, \
             patch.object(core,'arm_verified_update') as arm:
            with patch.dict(os.environ, {'SAKUYA_ROOT':str(self.data)}):
                code, result = self.post('/api/update/prepare', {'commit':good})
            self.assertEqual(code,200)
            self.assertTrue(result['restart_required'])
            stage.assert_called_once()
            verify.assert_called_once()
            recover.assert_called_once()
            arm.assert_called_once()
        self.assertFalse(self.stop.is_set())

    def test_restart_requires_confirmation_and_armed_transaction(self):
        self.assertEqual(self.post('/api/update/restart', {'confirm': False})[0], 400)
        if os.name != 'nt':
            self.assertEqual(self.post('/api/update/restart', {'confirm': True})[0], 501)
        else:
            self.assertEqual(self.post('/api/update/restart', {'confirm': True})[0], 409)
        self.assertFalse(self.stop.is_set())

    def test_restart_rejects_cross_origin(self):
        self.assertEqual(self.post('/api/update/restart', {'confirm': True},
                                   {'Origin': 'https://invalid.example'})[0], 403)
        self.assertFalse(self.stop.is_set())

    def test_cloudru_key_and_model_endpoints_are_bounded(self):
        self.assertEqual(self.post('/api/cloudru/key', {'key': 42})[0],400)
        self.assertEqual(self.post('/api/cloudru/model', {'model': 'invalid model'})[0],400)
        self.assertEqual(self.post('/api/cloudru/delete', {'confirm':False})[0],400)
        self.assertEqual(self.post('/api/cloudru/key', {'key':'secret-value'},
                                   {'Origin':'https://external.example'})[0],403)
        with patch.object(core.cloudru, 'store_key') as store:
            if os.name == 'nt':
                self.assertEqual(self.post('/api/cloudru/key', {'key':'mock-test-secret'})[0],200)
                store.assert_called_once()
            else:
                self.assertEqual(self.post('/api/cloudru/key', {'key':'mock-test-secret'})[0],501)
                store.assert_not_called()
        self.assertEqual(self.post('/api/cloudru/model', {'model':'test/model'})[0],200)
        status=self.get('/api/cloudru/status')
        self.assertEqual(status['model'],'test/model')
        self.assertNotIn('key',status)

    def test_logs_are_bounded(self):
        (self.data/'logs/sakuya.log').write_text('\n'.join('line '+str(i) for i in range(100)),encoding='utf-8')
        result=self.get('/api/logs')
        self.assertEqual(len(result['lines']),80)
        self.assertEqual(result['lines'][-1],'line 99')


class WebPersistenceTests(unittest.TestCase):
    setUp=fixtures.ProcessTests.setUp
    tearDown=fixtures.ProcessTests.tearDown
    start=fixtures.ProcessTests.start
    ready=fixtures.ProcessTests.ready
    shutdown=fixtures.ProcessTests.shutdown

    def test_browser_settings_survive_supervisor_shutdown_and_restart(self):
        process=self.start(); state=self.ready(process)
        with HTTP.open(state['url']+'/api/bootstrap',timeout=3) as response: bootstrap=json.load(response)
        request=urllib.request.Request(state['url']+'/api/settings',data=json.dumps({'port':0,'autostart_browser':True}).encode(),
            headers={'Content-Type':'application/json','Origin':state['url'],'X-Sakuya-CSRF':bootstrap['csrf']})
        with HTTP.open(request,timeout=3) as response: self.assertEqual(response.status,200)
        self.shutdown(state); process.communicate(timeout=10)
        self.assertTrue(json.loads((self.data/'settings.json').read_text())['autostart_browser'])
        process=self.start(); state=self.ready(process)
        with HTTP.open(state['url']+'/api/bootstrap',timeout=3) as response: current=json.load(response)
        self.assertTrue(current['settings']['autostart_browser'])
        self.assertEqual(current['mode'],'diagnostic')
        self.shutdown(state); process.communicate(timeout=10)


if __name__=='__main__': unittest.main()
