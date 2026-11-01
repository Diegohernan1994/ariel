"""Autenticación de un único dueño, sesiones revocables y CSRF."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from http.cookies import SimpleCookie
from urllib.parse import urlparse


def password_hash(password):
    salt = secrets.token_hex(32)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def password_matches(password, encoded):
    try:
        scheme, count, salt, expected = encoded.split('$')
        if scheme != 'pbkdf2_sha256' or not 100000 <= int(count) <= 2000000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(count)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class AccessControl:
    def __init__(self, data_dir, public_url='', local_setup=False):
        self.path = os.path.join(data_dir, '.owner-auth.json')
        self.db_path = os.path.join(data_dir, 'sessions.sqlite3')
        self.public_url = public_url.rstrip('/')
        self.local_setup = local_setup
        self.lock = threading.Lock()
        self.failures = {}
        self.owner = {}
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as file:
                self.owner = json.load(file)
        if os.environ.get('ADMIN_PASSWORD_HASH'):
            self.owner = {'username': os.environ.get('ADMIN_USERNAME', 'marcos'),
                          'passwordHash': os.environ['ADMIN_PASSWORD_HASH']}
        if not local_setup:
            if not self.configured:
                raise RuntimeError('Falta configurar el acceso privado: ADMIN_USERNAME y ADMIN_PASSWORD_HASH o archivo del dueño en DATA_DIR')
            parsed = urlparse(self.public_url)
            if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in ('', '/'):
                raise RuntimeError('PUBLIC_PANEL_URL debe ser el origen HTTPS del panel')
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires INTEGER NOT NULL)')

    @property
    def configured(self):
        return bool(self.owner.get('username') and self.owner.get('passwordHash'))

    def configure(self, username, password):
        with self.lock:
            if self.configured or not self.local_setup:
                raise ValueError('La configuración inicial no está habilitada')
            if not isinstance(username, str) or not 3 <= len(username.strip()) <= 64:
                raise ValueError('El usuario debe tener entre 3 y 64 caracteres')
            if not isinstance(password, str) or not 12 <= len(password) <= 512:
                raise ValueError('La contraseña debe tener al menos 12 caracteres')
            owner = {'username': username.strip(), 'passwordHash': password_hash(password)}
            with open(self.path + '.tmp', 'w', encoding='utf-8') as file:
                json.dump(owner, file)
            os.replace(self.path + '.tmp', self.path)
            self.owner = owner

    def origin_allowed(self, headers, peer):
        origin = headers.get('Origin', '')
        if self.local_setup and peer in ('127.0.0.1', '::1'):
            host = headers.get('Host', '')
            parsed = urlparse('http://' + host)
            return parsed.hostname in ('localhost', '127.0.0.1', '::1') and origin == 'http://' + host
        return bool(self.public_url and origin == self.public_url)

    @staticmethod
    def cookie_value(headers, name):
        try:
            cookies = SimpleCookie()
            cookies.load(headers.get('Cookie', ''))
            return cookies[name].value if name in cookies else ''
        except Exception:
            return ''

    def cookie(self, name, value, age):
        secure = '' if self.local_setup else '; Secure'
        return f'{name}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}'

    def session(self, headers):
        if not self.configured:
            return None
        token = self.cookie_value(headers, 'ariel_session')
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with closing(sqlite3.connect(self.db_path)) as db:
            row = db.execute('SELECT csrf, expires FROM sessions WHERE token=?', (digest,)).fetchone()
        if not row or row[1] <= time.time():
            return None
        return {'csrf': row[0], 'token': digest, 'username': self.owner['username']}

    def login(self, username, password, peer):
        now = time.time()
        with self.lock:
            self.failures = {key: value for key, value in self.failures.items() if now - value[1] < 900}
            count, last = self.failures.get(peer, (0, 0))
            if count >= 5:
                raise PermissionError('Demasiados intentos. Esperá 15 minutos antes de volver a intentar.')
            valid_password = password_matches(password, self.owner.get('passwordHash', ''))
            if not valid_password or not hmac.compare_digest(username.encode(), self.owner.get('username', '').encode()):
                self.failures[peer] = (count + 1, now)
                return None
            self.failures.pop(peer, None)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('DELETE FROM sessions WHERE expires <= ?', (int(now),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), csrf, int(now) + 43200))
        return token

    def logout(self, session):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('DELETE FROM sessions WHERE token=?', (session['token'],))

