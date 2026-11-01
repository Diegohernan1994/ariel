import os, sys, json, tempfile, threading, http.client, unittest, sqlite3, zipfile
from contextlib import closing
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['ARIEL_LOCAL_SETUP'] = '1'
temp = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = temp.name
import server
from access_control import AccessControl, password_matches
from manage import backup, restore

class PrivateDeployment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadedHTTPServer(('127.0.0.1', 0), server.AgentRequestHandler)
        cls.port = cls.http.server_address[1]
        cls.origin = f'http://127.0.0.1:{cls.port}'
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=8)
        hdr = {'Origin':self.origin, 'Content-Type':'application/json', **(headers or {})}
        body = data if isinstance(data, bytes) else (json.dumps(data) if data is not None else None)
        conn.request(method, path, body, hdr)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_01_private_access_and_setup(self):
        self.assertEqual(self.request('GET','/')[0], 303)
        for path in ['/api/state','/api/analytics','/uploads/private.jpg','/voices/test.mp3','/reels/test.mp4']:
            self.assertEqual(self.request('GET',path)[0],401)
        self.assertEqual(self.request('GET','/health/live')[0],200)
        self.assertEqual(self.request('GET','/health/ready')[0],503)
        self.assertEqual(self.request('GET','/manifest.webmanifest')[0],200)
        _, headers, _ = self.request('GET','/setup')
        cookie = headers['Set-Cookie'].split(';')[0]
        csrf = cookie.split('=',1)[1]
        credentials = {'username':'dueño','password':'temporary-fixture-789','confirmPassword':'temporary-fixture-789'}
        self.assertEqual(self.request('POST','/api/auth/setup',credentials)[0],403)
        hdr = {'Cookie':cookie,'X-CSRF-Token':csrf}
        self.assertEqual(self.request('POST','/api/auth/setup',credentials,hdr)[0],200)
        self.assertEqual(self.request('POST','/api/auth/setup',credentials,hdr)[0],403)
        self.assertEqual(self.request('GET','/health/ready')[0],200)
        stored = json.loads((Path(temp.name)/'.owner-auth.json').read_text())
        self.assertNotIn('temporary-fixture-789',json.dumps(stored))
        self.assertTrue(password_matches(credentials['password'],stored['passwordHash']))

    def test_02_login_csrf_restart_logout(self):
        _, headers, _ = self.request('GET','/login')
        cookie = headers['Set-Cookie'].split(';')[0]
        hdr = {'Cookie':cookie,'X-CSRF-Token':cookie.split('=',1)[1]}
        data = {'username':'dueño','password':'temporary-fixture-789'}
        self.assertEqual(self.request('POST','/api/auth/login',data,{**hdr,'Origin':'https://hostil.example'})[0],403)
        status, headers, _ = self.request('POST','/api/auth/login',data,hdr)
        self.assertEqual(status,200)
        session_cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',headers['Set-Cookie']); self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        authenticated = {'Cookie':session_cookie}
        self.assertEqual(self.request('GET','/api/state',headers=authenticated)[0],200)
        status, _, raw = self.request('GET','/api/auth/session',headers=authenticated)
        self.assertEqual(status,200)
        csrf = json.loads(raw)['csrfToken']
        self.assertEqual(self.request('POST','/api/auth/logout',{},authenticated)[0],403)
        restarted = AccessControl(temp.name,local_setup=True)
        self.assertIsNotNone(restarted.session(authenticated))
        self.assertEqual(self.request('POST','/api/auth/logout',{}, {**authenticated,'X-CSRF-Token':csrf})[0],200)
        self.assertIsNone(restarted.session(authenticated))
        self.assertEqual(self.request('GET','/api/state',headers=authenticated)[0],401)

    def test_025_fresh_challenge_replaces_stale_page(self):
        _, headers, raw = self.request('GET','/api/auth/login-challenge')
        cookie = headers['Set-Cookie'].split(';')[0]
        fresh = json.loads(raw)['csrfToken']
        self.assertIn('no-store',headers['Cache-Control'])
        data = {'username':'dueño','password':'temporary-fixture-789'}
        self.assertEqual(self.request('POST','/api/auth/login',data,{'Cookie':cookie,'X-CSRF-Token':'stale-token'})[0],403)
        status, headers, raw = self.request('GET','/api/auth/login-challenge')
        self.assertEqual(status,200)
        renewed = json.loads(raw)['csrfToken']
        self.assertNotEqual(fresh,renewed)
        self.assertEqual(self.request('POST','/api/auth/login',data,{'Cookie':headers['Set-Cookie'].split(';')[0],'X-CSRF-Token':renewed})[0],200)

    def test_027_support_requires_session_and_records_failures(self):
        from unittest.mock import patch
        for path in ['/api/support/reports']:
            self.assertEqual(self.request('GET',path)[0],401)
        self.assertEqual(self.request('POST','/api/support/report',{'description':'prueba'})[0],401)
        _, headers, _ = self.request('GET','/login')
        cookie = headers['Set-Cookie'].split(';')[0]
        status, headers, _ = self.request('POST','/api/auth/login',{'username':'dueño','password':'temporary-fixture-789'}, {'Cookie':cookie,'X-CSRF-Token':cookie.split('=',1)[1]})
        self.assertEqual(status,200)
        hdr = {'Cookie':headers['Set-Cookie'].split(';')[0]}
        _, _, raw = self.request('GET','/api/auth/session',headers=hdr)
        hdr['X-CSRF-Token']=json.loads(raw)['csrfToken']
        status, _, raw = self.request('POST','/api/support/report',{'section':'tools','description':'No termina el reel. apiKey: fixture-secret'},hdr)
        self.assertEqual(status,200);self.assertFalse(json.loads(raw)['sent'])
        status, _, raw = self.request('GET','/api/support/reports',headers=hdr)
        self.assertEqual(status,200);self.assertNotIn(b'fixture-secret',raw)
        self.assertEqual(self.request('GET','/support-widget.js',headers=hdr)[0],200)
        with patch.object(server,'answer_help',side_effect=RuntimeError('fixture failure')):
            status, _, raw = self.request('POST','/api/support/chat',{'message':'Ayuda','section':'tools'},hdr)
        self.assertEqual(status,500)
        self.assertEqual(json.loads(raw)['incident']['status'],'saved_locally')
        self.assertNotIn(b'fixture failure',raw)
        with patch.object(server,'execute_agent_instruction',return_value={'reply':'Falló la consulta','systemError':'AI_REQUEST_FAILED'}):
            status, _, raw = self.request('POST','/api/chat',{'message':'prueba'},hdr)
        self.assertEqual(status,200);self.assertIn('incident',json.loads(raw))


    def test_03_untrusted_tracking_and_invalid_bodies(self):
        self.assertEqual(self.request('POST','/api/track',{}, {'Origin':'https://hostil.example'})[0],403)
        status, headers, raw = self.request('POST','/api/track',{'event':'page_view','eventId':'private-deploy-fixture','sessionId':'fixture'}, {'Origin':'https://tienda-marcos.pages.dev'})
        self.assertEqual(status,200)
        self.assertTrue(json.loads(raw)['recorded'])
        self.assertEqual(headers['Access-Control-Allow-Origin'],'https://tienda-marcos.pages.dev')
        self.assertEqual(self.request('POST','/api/track',b'\xff', {'Origin':'https://tienda-marcos.pages.dev'})[0],400)
        self.assertEqual(self.request('POST','/api/track',b'x'*65537, {'Origin':'https://tienda-marcos.pages.dev'})[0],413)

    def test_04_production_and_limits(self):
        with tempfile.TemporaryDirectory() as path:
            with self.assertRaises(RuntimeError): AccessControl(path,'https://panel.example.com')
            auth = AccessControl(path, local_setup=True)
            auth.configure('marcos','temporary-fixture-789')
            with self.assertRaises(RuntimeError): AccessControl(path,'http://panel.example.com')
            production = AccessControl(path,'https://panel.example.com')
            self.assertIn('; Secure',production.cookie('test','value',1))
            for _ in range(5): self.assertIsNone(production.login('marcos','incorrect','peer'))
            with self.assertRaises(PermissionError): production.login('marcos','temporary-fixture-789','peer')

    def test_05_backup_and_recovery(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path); source = root/'source'; source.mkdir()
            (source/'state.json').write_text(json.dumps({'aiConfig':{'apiKey':'fixture-key'},'chatHistory':[{'text':'dato conservado'}]}))
            (source/'.secrets.local.json').write_text('secret-fixture')
            (source/'sessions.sqlite3').write_text('excluded')
            with closing(sqlite3.connect(source/'analytics.sqlite3')) as db, db:
                db.execute('CREATE TABLE events (value TEXT)'); db.execute("INSERT INTO events VALUES ('visita')")
            archive = root/'backup.zip'; backup(source,archive)
            with zipfile.ZipFile(archive) as z:
                self.assertNotIn('.secrets.local.json',z.namelist()); self.assertNotIn('sessions.sqlite3',z.namelist())
                self.assertNotIn(b'fixture-key',z.read('state.json'))
            restored = root/'restored'; restore(archive,restored)
            self.assertEqual(json.loads((restored/'state.json').read_text())['chatHistory'][0]['text'],'dato conservado')
            with closing(sqlite3.connect(restored/'analytics.sqlite3')) as db: self.assertEqual(db.execute('SELECT value FROM events').fetchone()[0],'visita')
            with self.assertRaises(ValueError): restore(archive,restored)
            bad = root/'bad.zip'
            with zipfile.ZipFile(bad,'w') as z: z.writestr('../escape','bad')
            with self.assertRaises(ValueError): restore(bad,root/'empty')
            self.assertFalse((root/'escape').exists())

if __name__ == '__main__': unittest.main(verbosity=2)

