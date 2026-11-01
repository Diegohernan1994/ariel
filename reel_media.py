"""Reels con archivos reales: fotos animadas y videos, sin servicios de IA."""
from pathlib import Path
import subprocess
import tempfile
import uuid

IMAGES = {'.jpg', '.jpeg', '.png', '.webp'}
VIDEOS = {'.mp4', '.mov', '.webm', '.avi', '.mkv', '.m4v'}


def run(args):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args], capture_output=True)
    if result.returncode:
        raise RuntimeError('No se pudo procesar un archivo multimedia. Comprobá que se pueda reproducir.')


def render_segment(source, destination):
    source = Path(source)
    # Encuadre completo, sin estirar; fondo negro cuando la proporción no es 9:16.
    frame = 'scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1'
    if source.suffix.lower() in IMAGES:
        # 150 cuadros = 5 segundos. Conservar desde el segundo 1 hasta el 4.
        filters = frame + ",zoompan=z='1+0.04*on/149':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=150:s=1080x1920:fps=30,trim=start_frame=30:end_frame=120,setpts=PTS-STARTPTS"
        inputs = ['-i', str(source)]
    elif source.suffix.lower() in VIDEOS:
        filters = frame + ',fps=30,setpts=PTS-STARTPTS'
        inputs = ['-i', str(source)]
    else:
        raise ValueError('Formato no compatible. Usá JPG, PNG, WEBP, MP4, MOV o WEBM.')
    filters += ',scale=out_range=tv,format=yuv420p'
    run([*inputs, '-map', '0:v:0', '-vf', filters, '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2', '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-r', '30', '-video_track_timescale', '15360', str(destination)])


def build_reel(uploads_dir, reels_dir, temp_dir, filenames):
    if not isinstance(filenames, list) or not filenames:
        raise ValueError('Seleccioná las fotos y los clips en el orden que querés para tu reel.')
    root = Path(uploads_dir).resolve()
    sources = []
    for name in filenames:
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Nombre de archivo inválido')
        path = (root/name).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError('Uno de los archivos seleccionados ya no está disponible. Volvé a seleccionarlo.')
        if path.suffix.lower() not in IMAGES | VIDEOS:
            raise ValueError('Formato no compatible')
        sources.append(path)
    name = f'REEL_{uuid.uuid4().hex}.mp4'
    destination = Path(reels_dir)/name
    try:
        with tempfile.TemporaryDirectory(prefix='reel_', dir=temp_dir) as scratch:
            scratch = Path(scratch)
            for index, source in enumerate(sources):
                render_segment(source, scratch/f'{index:04d}.mp4')
            listing = scratch/'segments.txt'
            listing.write_text(''.join(f"file '{index:04d}.mp4'\n" for index in range(len(sources))), encoding='utf-8')
            run(['-f', 'concat', '-safe', '1', '-i', str(listing), '-map', '0:v:0', '-an', '-c:v', 'copy', '-movflags', '+faststart', str(destination)])
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return name
