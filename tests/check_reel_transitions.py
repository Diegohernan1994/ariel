from pathlib import Path
import tempfile, sys, subprocess, json
from PIL import Image
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from reel_media import render_segment, join_with_transitions, build_reel, TRANSITIONS
def probe(p):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(p)]))
with tempfile.TemporaryDirectory() as folder:
    folder=Path(folder)
    for color in ['red','blue']:
        Image.new('RGB',(640,240),color).save(folder/f'{color}.jpg')
        render_segment(folder/f'{color}.jpg',folder/f'{color}.mp4')
    data=probe(folder/'red.mp4')
    assert abs(float(data['format']['duration'])-3)<0.05
    # La esquina superior izquierda también tiene imagen: no hay franjas negras.
    frame=subprocess.check_output(['ffmpeg','-v','error','-ss','1','-i',str(folder/'red.mp4'),'-frames:v','1','-vf','crop=2:2:2:2','-f','rawvideo','-pix_fmt','rgb24','-'])
    assert frame[0]>220 and frame[1]<20 and frame[2]<20, list(frame[:3])
    for effect in sorted(TRANSITIONS-{'none'}):
        output=folder/f'{effect}.mp4'
        join_with_transitions([folder/'red.mp4',folder/'blue.mp4'],output,effect)
        info=probe(output); stream=info['streams'][0]
        assert (stream['width'],stream['height'],stream['pix_fmt'],stream['r_frame_rate'])==(1080,1920,'yuv420p','30/1'),effect
        assert abs(float(info['format']['duration'])-5.7)<0.07,(effect,info['format']['duration'])
        # Leer todos los cuadros, para detectar fallos fuera del encabezado.
        subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],check=True)
        print(f'OK: {effect}, 5,7 s, 1080x1920, 30 fps',flush=True)
    # Tres entradas de distinta duración y frecuencia, incluido un clip muy corto.
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=240x320:r=24:d=0.2','-c:v','libx264',str(folder/'short.mp4')],check=True)
    (folder/'out').mkdir();(folder/'temp').mkdir()
    name=build_reel(folder,folder/'out',folder/'temp',['red.jpg','short.mp4','blue.jpg'],transition='fade')
    info=probe(folder/'out'/name)
    assert abs(float(info['format']['duration'])-6.033333)<0.08,info['format']['duration']
    try: build_reel(folder,folder/'out',folder/'temp',['red.jpg'],transition='invalid')
    except ValueError: pass
    else: raise AssertionError('Transición inválida aceptada')
    print('OK: mezcla de tres entradas, clip corto y validación de transición.',flush=True)
