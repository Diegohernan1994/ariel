from pathlib import Path
import sys, tempfile, subprocess, json
from PIL import Image
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from reel_media import build_reel
def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp)
    for name in ['uploads','reels','temp']: (tmp/name).mkdir()
    colors=['red','green','blue','yellow','cyan','magenta']
    for index,color in enumerate(colors): Image.new('RGB',(320,240),color).save(tmp/'uploads'/f'{index}.jpg')
    name=build_reel(tmp/'uploads',tmp/'reels',tmp/'temp',[f'{i}.jpg' for i in range(6)])
    data=probe(tmp/'reels'/name); video=data['streams'][0]
    assert (video['width'],video['height'],video['pix_fmt'],video['r_frame_rate'])==(1080,1920,'yuv420p','30/1')
    assert abs(float(data['format']['duration'])-18)<0.1, data['format']['duration']
    # Comprobar el orden de las seis escenas con un píxel del centro.
    for i,color in enumerate(colors):
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(i*3+1.5),'-i',str(tmp/'reels'/name),'-frames:v','1','-vf','crop=2:2:540:960','-f','rawvideo','-pix_fmt','rgb24','-'])
        expected=Image.new('RGB',(1,1),color).getpixel((0,0))
        assert all(abs(raw[c]-expected[c])<12 for c in range(3)),(i,list(raw[:3]),expected)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=white:s=320x240:r=24:d=2','-c:v','libx264',str(tmp/'uploads'/'clip.mp4')],check=True)
    name=build_reel(tmp/'uploads',tmp/'reels',tmp/'temp',['0.jpg','clip.mp4','1.jpg'])
    data=probe(tmp/'reels'/name)
    assert abs(float(data['format']['duration'])-8)<0.15,data['format']['duration']
    assert len(data['streams'])==1
    try: build_reel(tmp/'uploads',tmp/'reels',tmp/'temp',['../escape.jpg'])
    except ValueError: pass
    else: raise AssertionError('No se rechazó la ruta inválida')
    assert not list((tmp/'temp').iterdir())
    print('OK: seis fotos = 18 segundos; orden correcto; mezcla foto/video = 8 segundos; MP4 1080x1920 30fps; limpieza temporal y rutas seguras.')
