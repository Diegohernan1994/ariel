"""Galería privada con favoritos, papelera y protección durante el render."""
from pathlib import Path
import os, re, sqlite3, threading, time, uuid, shutil
from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta

IMAGES={'.jpg','.jpeg','.png','.webp'}
VIDEOS={'.mp4','.mov','.webm','.avi','.mkv','.m4v'}
RETENTION=7*86400
MAX_FILE=20*1024**2

class MediaLibrary:
    def __init__(self, uploads_dir, data_dir, max_files=100, max_bytes=2*1024**3, clock=time.time):
        self.root=Path(uploads_dir).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.db=str(Path(data_dir)/'media-library.sqlite3')
        self.max_files=max_files;self.max_bytes=max_bytes;self.clock=clock
        self.lock=threading.RLock();self.in_use={};self.stop=threading.Event();self.worker=None
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS media (name TEXT PRIMARY KEY, original TEXT, added REAL, size INTEGER, favorite INTEGER DEFAULT 0, deleted REAL)')

    def path(self,name):
        if not isinstance(name,str) or not name or '/' in name or '\\' in name or Path(name).name!=name:
            raise ValueError('Archivo inválido')
        result=self.root/name
        if result.is_symlink() or result.resolve().parent!=self.root: raise ValueError('Archivo inválido')
        return result

    def _sync(self,db):
        known={r[0] for r in db.execute('SELECT name FROM media')}
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in IMAGES|VIDEOS: continue
            stat=path.stat()
            if path.name not in known:
                db.execute('INSERT INTO media (name,original,added,size) VALUES (?,?,?,?)',(path.name,path.name,stat.st_mtime,stat.st_size))
        for name, in db.execute('SELECT name FROM media').fetchall():
            if not self.path(name).is_file(): db.execute('DELETE FROM media WHERE name=?',(name,))
        for name, in db.execute('SELECT name FROM media WHERE deleted IS NOT NULL AND deleted<=?',(self.clock()-RETENTION,)).fetchall():
            if self.in_use.get(name): continue
            self.path(name).unlink(missing_ok=True)
            db.execute('DELETE FROM media WHERE name=?',(name,))

    def _room(self,db,slots=0,exclude=()):
        count=db.execute('SELECT count(*) FROM media WHERE deleted IS NULL').fetchone()[0]
        needed=max(0,count+slots-self.max_files)
        if not needed:return
        eligible=[r[0] for r in db.execute('SELECT name FROM media WHERE deleted IS NULL AND favorite=0 ORDER BY added,name') if r[0] not in exclude and not self.in_use.get(r[0])]
        if len(eligible)<needed:
            raise ValueError('La galería está llena y los archivos restantes son favoritos o están en uso. Quitá un favorito o esperá que termine el reel.')
        db.executemany('UPDATE media SET deleted=? WHERE name=?',[(self.clock(),n) for n in eligible[:needed]])

    def snapshot(self):
        with self.lock, closing(sqlite3.connect(self.db,timeout=15)) as db, db:
            self._sync(db)
            try:self._room(db)
            except ValueError:pass
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT * FROM media ORDER BY added DESC,name').fetchall()
            active=[];trash=[]
            for r in rows:
                item={'name':r['name'],'originalName':r['original'],'type':'image' if Path(r['name']).suffix.lower() in IMAGES else 'video','url':'/uploads/'+r['name'],'size':r['size'],'favorite':bool(r['favorite']),'inUse':bool(self.in_use.get(r['name'])),'addedAt':datetime.fromtimestamp(r['added'],timezone(timedelta(hours=-3))).isoformat()}
                if r['deleted'] is None:active.append(item)
                else:
                    item['expiresAt']=datetime.fromtimestamp(r['deleted']+RETENTION,timezone(timedelta(hours=-3))).isoformat();trash.append(item)
            return {'files':active,'trash':trash,'activeCount':len(active),'favoriteCount':sum(x['favorite'] for x in active),'trashCount':len(trash),'usedBytes':sum(r['size'] for r in rows),'maxFiles':self.max_files,'maxBytes':self.max_bytes,'maxFileBytes':MAX_FILE,'retentionDays':7}

    def add(self,original,raw):
        if not isinstance(original,str) or not isinstance(raw,bytes) or not raw or len(raw)>MAX_FILE:
            raise ValueError('Cada archivo debe tener contenido y pesar como máximo 20 MB.')
        clean=re.sub(r'[^a-zA-Z0-9._-]','_',original)[:130]
        if Path(clean).suffix.lower() not in IMAGES|VIDEOS:raise ValueError('Usá JPG, PNG, WEBP, MP4, MOV o WEBM.')
        name='real_'+uuid.uuid4().hex+'_'+clean
        target=self.path(name);staging=self.path('.staging_'+uuid.uuid4().hex)
        with self.lock, closing(sqlite3.connect(self.db,timeout=15)) as db, db:
            self._sync(db)
            total=db.execute('SELECT coalesce(sum(size),0) FROM media').fetchone()[0]
            if total+len(raw)>self.max_bytes:
                raise ValueError('Se alcanzó el límite de espacio de la galería, incluida la papelera. No se eliminan archivos antes de cumplir 7 días. Esperá su vencimiento o ampliá el límite de almacenamiento.')
            if shutil.disk_usage(self.root).free < len(raw)+64*1024**2:
                raise ValueError('El servidor no tiene espacio disponible para subir el archivo.')
            self._room(db,slots=1)
            try:
                staging.write_bytes(raw);os.replace(staging,target)
                db.execute('INSERT INTO media (name,original,added,size) VALUES (?,?,?,?)',(name,original[:200],self.clock(),len(raw)))
            except Exception:
                staging.unlink(missing_ok=True);target.unlink(missing_ok=True);raise
        return name

    def action(self,name,action,value=None):
        self.path(name)
        with self.lock, closing(sqlite3.connect(self.db,timeout=15)) as db, db:
            self._sync(db)
            row=db.execute('SELECT favorite,deleted FROM media WHERE name=?',(name,)).fetchone()
            if not row:raise ValueError('El archivo ya no está disponible.')
            if self.in_use.get(name):raise ValueError('Este archivo está en uso. Esperá a que termine el reel.')
            if action=='favorite':
                if not isinstance(value,bool) or row[1] is not None:raise ValueError('Favorito inválido.')
                db.execute('UPDATE media SET favorite=? WHERE name=?',(int(value),name))
            elif action=='trash':
                if row[0]:raise ValueError('Quitá la marca de favorito antes de moverlo a la papelera.')
                if row[1] is None:db.execute('UPDATE media SET deleted=? WHERE name=?',(self.clock(),name))
            elif action=='restore':
                if row[1] is not None:
                    self._room(db,slots=1,exclude=(name,));db.execute('UPDATE media SET deleted=NULL WHERE name=?',(name,))
            else:raise ValueError('Acción no válida.')
        return {'ok':True}

    def active(self,name):
        try:self.path(name)
        except ValueError:return False
        with self.lock, closing(sqlite3.connect(self.db)) as db:
            return bool(db.execute('SELECT 1 FROM media WHERE name=? AND deleted IS NULL',(name,)).fetchone())

    @contextmanager
    def using(self,names):
        if not isinstance(names,list) or not names:raise ValueError('Seleccioná fotos o clips para tu reel.')
        with self.lock, closing(sqlite3.connect(self.db,timeout=15)) as db, db:
            self._sync(db)
            for name in names:
                self.path(name)
                if not db.execute('SELECT 1 FROM media WHERE name=? AND deleted IS NULL',(name,)).fetchone():
                    raise ValueError('Un archivo seleccionado ya no está activo. Elegilo de nuevo en la galería.')
            for name in names:self.in_use[name]=self.in_use.get(name,0)+1
        try:yield
        finally:
            with self.lock:
                for name in names:
                    self.in_use[name]-=1
                    if not self.in_use[name]:del self.in_use[name]

    def start(self):
        if self.worker:return
        def maintain():
            while not self.stop.wait(3600):
                try:self.snapshot()
                except Exception:pass
        self.worker=threading.Thread(target=maintain,daemon=True);self.worker.start()

    def close(self):
        self.stop.set()
