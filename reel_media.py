"""Reels con archivos reales: fotos animadas y videos, sin servicios de IA."""
from pathlib import Path
import subprocess
import tempfile
import uuid
import json
import math

IMAGES = {'.jpg', '.jpeg', '.png', '.webp'}
TRANSITIONS = {'none', 'fade', 'dissolve', 'fadeblack', 'wipeleft', 'wiperight', 'slideleft', 'slideright', 'smoothleft', 'circleopen', 'pixelize'}

VIDEOS = {'.mp4', '.mov', '.webm', '.avi', '.mkv', '.m4v'}


def run(args):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args], capture_output=True)
    if result.returncode:
        raise RuntimeError('No se pudo procesar un archivo multimedia. Comprobá que se pueda reproducir.')


def render_segment(source, destination):
    source = Path(source)
    # Llenar 9:16 mediante recorte centrado, sin estirar ni agregar franjas.
    frame = 'scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2,crop=1080:1920,setsar=1'
    if source.suffix.lower() in IMAGES:
        # 150 cuadros = 5 segundos. Conservar desde el segundo 1 hasta el 4.
        filters = frame + ",zoompan=z='1+0.25*on/149':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=150:s=1080x1920:fps=30,trim=start_frame=30:end_frame=120,setpts=PTS-STARTPTS"
        inputs = ['-i', str(source)]
    elif source.suffix.lower() in VIDEOS:
        filters = frame + ',fps=30,setpts=PTS-STARTPTS'
        inputs = ['-i', str(source)]
    else:
        raise ValueError('Formato no compatible. Usá JPG, PNG, WEBP, MP4, MOV o WEBM.')
    filters += ',scale=out_range=tv,format=yuv420p'
    run([*inputs, '-map', '0:v:0', '-vf', filters, '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2', '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-r', '30', '-video_track_timescale', '15360', str(destination)])


def build_reel(uploads_dir, reels_dir, temp_dir, filenames, transition="none"):
    if not isinstance(transition, str) or transition not in TRANSITIONS:
        raise ValueError("Transición no válida")
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
            if transition == 'none' or len(sources) == 1:
                run(['-f', 'concat', '-safe', '1', '-i', str(listing), '-map', '0:v:0', '-an', '-c:v', 'copy', '-movflags', '+faststart', str(destination)])
            else:
                join_with_transitions([scratch/f'{i:04d}.mp4' for i in range(len(sources))], destination, transition)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return name


def join_with_transitions(segments, destination, transition):
    durations = []
    inputs = []
    filters = []
    for i, path in enumerate(segments):
        metadata = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(path)]))
        duration = float(metadata['format']['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Un clip no tiene duración válida')
        durations.append(round(duration * 30) / 30)
        inputs += ['-i', str(path)]
        filters.append(f'[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v{i}]')
    current, elapsed = 'v0', durations[0]
    for i in range(1, len(segments)):
        # Hasta nueve cuadros de solapamiento (0,3 s); adaptar a clips muy cortos.
        frames = min(9, int(round(durations[i-1]*30))//2, int(round(durations[i]*30))//2)
        output = f'joined{i}'
        if frames:
            overlap = frames / 30
            filters.append(f'[{current}][v{i}]xfade=transition={transition}:duration={overlap:.6f}:offset={elapsed-overlap:.6f}[{output}]')
        else:
            overlap = 0
            filters.append(f'[{current}][v{i}]concat=n=2:v=1:a=0,settb=AVTB[{output}]')
        elapsed += durations[i] - overlap
        current = output
    run([*inputs, '-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', f'[{current}]', '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2', '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-r', '30', '-movflags', '+faststart', str(destination)])
