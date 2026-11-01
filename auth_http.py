"""Protección HTTP compartida para panel, API y archivos privados."""
import hmac
import json
import os
import secrets


PUBLIC_ASSETS = {'/manifest.webmanifest', '/sw.js', '/pwa.js', '/offline.html',
                 '/icons/ariel-192.png', '/icons/ariel-512.png', '/icons/ariel-maskable-512.png'}


class AuthHTTPMixin:
    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        super().end_headers()

    def _auth_json(self, data, status=200, cookie=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, destination):
        self.send_response(303)
        self.send_header('Location', destination)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _auth_get(self, path):
        auth = self.server.access
        if path == '/health/live':
            self._auth_json({'ok': True})
            return False
        if path == '/health/ready':
            ready = auth.configured and os.access(self.server.data_dir, os.W_OK)
            self._auth_json({'ok': ready}, 200 if ready else 503)
            return False
        if path in ('/auth-ui.js', '/auth-client.js'):
            with open(os.path.join(self.server.public_dir, path[1:]), 'rb') as file:
                body = file.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        if path in ('/login', '/setup'):
            if path == '/setup' and auth.configured:
                self._redirect('/login')
                return False
            if path == '/setup' and not (auth.local_setup and self.client_address[0] in ('127.0.0.1', '::1')):
                self._auth_json({'error': 'Configuración inicial no disponible'}, 403)
                return False
            if path == '/login' and not auth.configured:
                self._redirect('/setup')
                return False
            if auth.session(self.headers):
                self._redirect('/')
                return False
            token = secrets.token_urlsafe(32)
            with open(os.path.join(self.server.public_dir, 'auth.html'), encoding='utf-8') as file:
                body = file.read().replace('__CSRF__', token).replace('__MODE__', 'setup' if path == '/setup' else 'login').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Set-Cookie', auth.cookie('ariel_login_csrf', token, 600))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        if path in PUBLIC_ASSETS:
            return True
        session = auth.session(self.headers)
        if not session:
            if path.startswith('/api/') or path.startswith(('/uploads/', '/voices/', '/reels/')):
                self._auth_json({'error': 'Necesitás iniciar sesión'}, 401)
            else:
                self._redirect('/login' if auth.configured else '/setup')
            return False
        if path == '/api/auth/session':
            self._auth_json({'username': session['username'], 'csrfToken': session['csrf']})
            return False
        return True

    def _auth_post(self, path):
        auth = self.server.access
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = -1
        maximum = 65536 if path.startswith('/api/auth/') or path == '/api/track' else 32 * 1024 * 1024
        if not 0 <= length <= maximum:
            self._auth_json({'error': 'Solicitud demasiado grande o inválida'}, 413)
            return False
        if self.headers.get_content_type() != 'application/json':
            self._auth_json({'error': 'Se requiere JSON'}, 415)
            return False
        if path == '/api/track':
            if self.headers.get('Origin', '') not in self.server.store_origins:
                self._auth_json({'error': 'Origen no autorizado'}, 403)
                return False
            return True
        if not auth.origin_allowed(self.headers, self.client_address[0]):
            self._auth_json({'error': 'Origen no autorizado'}, 403)
            return False
        token = self.headers.get('X-CSRF-Token', '')
        if path in ('/api/auth/login', '/api/auth/setup'):
            expected = auth.cookie_value(self.headers, 'ariel_login_csrf')
            if not token or not expected or not hmac.compare_digest(token, expected):
                self._auth_json({'error': 'Recargá la pantalla de acceso'}, 403)
                return False
            if path == '/api/auth/setup' and not (auth.local_setup and not auth.configured and self.client_address[0] in ('127.0.0.1', '::1')):
                self._auth_json({'error': 'Configuración inicial no disponible'}, 403)
                return False
            return True
        session = auth.session(self.headers)
        if not session:
            self._auth_json({'error': 'Necesitás iniciar sesión'}, 401)
            return False
        if not token or not hmac.compare_digest(token, session['csrf']):
            self._auth_json({'error': 'Sesión inválida. Recargá el panel.'}, 403)
            return False
        self.auth_session = session
        return True

    def _handle_auth_post(self, path, payload):
        auth = self.server.access
        if path == '/api/auth/setup':
            try:
                if payload.get('password') != payload.get('confirmPassword'):
                    raise ValueError('Las contraseñas no coinciden')
                auth.configure(payload.get('username', ''), payload.get('password', ''))
                self._auth_json({'ok': True})
            except ValueError as error:
                self._auth_json({'error': str(error)}, 400)
            return True
        if path == '/api/auth/login':
            username, password = payload.get('username', ''), payload.get('password', '')
            if not isinstance(username, str) or not isinstance(password, str) or len(password) > 512:
                self._auth_json({'error': 'Datos de acceso inválidos'}, 400)
                return True
            try:
                token = auth.login(username, password, self.client_address[0])
            except PermissionError as error:
                self._auth_json({'error': str(error)}, 429)
                return True
            if not token:
                self._auth_json({'error': 'Usuario o contraseña incorrectos'}, 401)
            else:
                self._auth_json({'ok': True}, cookie=auth.cookie('ariel_session', token, 43200))
            return True
        if path == '/api/auth/logout':
            auth.logout(self.auth_session)
            self._auth_json({'ok': True}, cookie=auth.cookie('ariel_session', '', 0))
            return True
        return False
