"""Administración local: credenciales privadas y respaldos portables."""
import argparse
import getpass
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from access_control import AccessControl

FILES = {'state.json', 'analytics.sqlite3', 'store-monitor.sqlite3', 'support.sqlite3', 'media-library.sqlite3', '.owner-auth.json'}
DIRECTORIES = {'uploads_reales', 'reels', 'voices', 'images'}


def backup(data_dir, destination):
    data_dir, destination = Path(data_dir), Path(destination)
    if destination.exists():
        raise ValueError('El respaldo de destino ya existe')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp)
        for name in FILES:
            source = data_dir / name
            if not source.exists():
                continue
            if name.endswith('.sqlite3'):
                with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(staging/name)) as target:
                    db.backup(target)
            else:
                shutil.copy2(source, staging/name)
        if (staging/'state.json').exists():
            state = json.loads((staging/'state.json').read_text(encoding='utf-8'))
            state.get('aiConfig', {}).pop('apiKey', None)
            state.get('telegramConfig', {}).pop('botToken', None)
            (staging/'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        for name in DIRECTORIES:
            if (data_dir/name).exists():
                shutil.copytree(data_dir/name, staging/name)
        with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
            for source in staging.rglob('*'):
                if source.is_file():
                    archive.write(source, source.relative_to(staging).as_posix())


def restore(source, target):
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError('Restaurá en una carpeta vacía; no se sobrescriben datos existentes')
    with zipfile.ZipFile(source) as archive:
        if sum(info.file_size for info in archive.infolist()) > 2 * 1024**3:
            raise ValueError('Respaldo demasiado grande para esta herramienta')
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename
                    or not path.parts or (path.parts[0] not in FILES and path.parts[0] not in DIRECTORIES)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Archivo inválido dentro del respaldo')
            if path.parts[0] in FILES and len(path.parts) != 1:
                raise ValueError('Ruta inválida')
        target.mkdir(parents=True, exist_ok=True)
        archive.extractall(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default=os.environ.get('DATA_DIR', os.path.dirname(__file__)))
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init-owner')
    commands.add_parser('backup').add_argument('--output', required=True)
    recovery = commands.add_parser('restore')
    recovery.add_argument('--input', required=True)
    recovery.add_argument('--into', required=True)
    args = parser.parse_args()
    if args.command == 'init-owner':
        Path(args.data_dir).mkdir(parents=True, exist_ok=True)
        auth = AccessControl(args.data_dir, local_setup=True)
        username = input('Usuario: ')
        password = getpass.getpass('Contraseña (mínimo 12 caracteres): ')
        if password != getpass.getpass('Repetí la contraseña: '):
            raise SystemExit('Las contraseñas no coinciden')
        auth.configure(username, password)
        print('Acceso privado configurado. No se mostró ni guardó la contraseña original.')
    elif args.command == 'backup':
        backup(args.data_dir, args.output)
        print('Respaldo creado. Guardalo en un lugar privado; contiene datos del negocio y la verificación del acceso.')
    else:
        restore(args.input, args.into)
        print('Restauración completada. Iniciá el servidor con DATA_DIR apuntando a esa carpeta.')
