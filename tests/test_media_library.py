from pathlib import Path
import unittest, tempfile, sys, os
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from media_library import MediaLibrary, RETENTION, MAX_FILE

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=1000000
        self.library=MediaLibrary(self.root/'uploads',self.root,max_files=2,max_bytes=100,clock=lambda:self.now)
    def tearDown(self):self.library.close();self.temp.cleanup()
    def add(self,label='photo.jpg',data=b'xx'):
        self.now+=1;return self.library.add(label,data)
    def test_oldest_moves_to_recoverable_trash(self):
        old=self.add();second=self.add();new=self.add()
        snapshot=self.library.snapshot()
        self.assertEqual(snapshot['activeCount'],2);self.assertEqual(snapshot['trashCount'],1)
        self.assertEqual(snapshot['trash'][0]['name'],old)
        self.assertTrue((self.library.root/old).exists());self.assertFalse(self.library.active(old))
        self.assertEqual(snapshot['usedBytes'],6)
        self.library.action(old,'restore')
        self.assertTrue(self.library.active(old));self.assertFalse(self.library.active(second))

    def test_favorites_and_in_use_survive_eviction(self):
        favorite=self.add();using=self.add();self.library.action(favorite,'favorite',True)
        with self.library.using([using]):
            with self.assertRaises(ValueError):self.add()
            with self.assertRaises(ValueError):self.library.action(using,'trash')
            self.assertTrue(next(x for x in self.library.snapshot()['files'] if x['name']==using)['inUse'])
        new=self.add()
        self.assertTrue(self.library.active(favorite));self.assertTrue(self.library.active(new));self.assertFalse(self.library.active(using))
        with self.assertRaises(ValueError):self.library.action(favorite,'trash')

    def test_seven_day_retention_and_total_space(self):
        self.library.max_bytes=6
        old=self.add();self.add();self.add();deleted_at=self.now
        with self.assertRaises(ValueError):self.add()
        self.assertTrue((self.library.root/old).exists())
        self.now=deleted_at+RETENTION-1
        self.library.snapshot();self.assertTrue((self.library.root/old).exists())
        self.now+=1
        self.library.snapshot();self.assertFalse((self.library.root/old).exists())
        self.add();self.assertEqual(self.library.snapshot()['usedBytes'],6)

    def test_invalid_names_limits_and_restore_expiry(self):
        for name in ['../file.jpg','a/b.jpg','a\\b.jpg']:
            with self.assertRaises(ValueError):self.library.action(name,'trash')
        with self.assertRaises(ValueError):self.library.add('image.svg',b'x')
        with self.assertRaises(ValueError):self.library.add('image.jpg',b'x'*(MAX_FILE+1))
        name=self.add();self.library.action(name,'trash');self.now+=RETENTION
        with self.assertRaises(ValueError):self.library.action(name,'restore')

    def test_existing_uploads_migrate_without_losing_bytes(self):
        for i in range(3):
            file=self.library.root/f'legacy{i}.jpg';file.write_bytes(b'existing');os.utime(file,(100+i,100+i))
        snapshot=self.library.snapshot()
        self.assertEqual((snapshot['activeCount'],snapshot['trashCount']),(2,1))
        self.assertEqual(snapshot['trash'][0]['name'],'legacy0.jpg')
        self.assertEqual(sum(p.stat().st_size for p in self.library.root.iterdir()),24)
        reopened=MediaLibrary(self.library.root,self.root,max_files=2,clock=lambda:self.now)
        self.assertEqual(reopened.snapshot()['trashCount'],1)

    def test_render_lock_is_released_on_failure(self):
        name=self.add()
        with self.assertRaises(RuntimeError):
            with self.library.using([name]):raise RuntimeError('fixture')
        self.library.action(name,'trash');self.assertFalse(self.library.active(name))

    def test_backup_restores_favorites_trash_and_originals(self):
        from manage import backup, restore
        # El nombre de carpeta coincide con el usado por el servidor y sus respaldos.
        library=MediaLibrary(self.root/'source'/'uploads_reales',self.root/'source',clock=lambda:self.now)
        favorite=library.add('favorite.jpg',b'original-favorite')
        trash=library.add('trash.jpg',b'original-trash')
        library.action(favorite,'favorite',True);library.action(trash,'trash')
        archive=self.root/'backup.zip';backup(self.root/'source',archive)
        restored=self.root/'restored';restore(archive,restored)
        snapshot=MediaLibrary(restored/'uploads_reales',restored,clock=lambda:self.now).snapshot()
        self.assertTrue(snapshot['files'][0]['favorite']);self.assertEqual(snapshot['trash'][0]['name'],trash)
        self.assertEqual((restored/'uploads_reales'/trash).read_bytes(),b'original-trash')

if __name__=='__main__':unittest.main(verbosity=2)
