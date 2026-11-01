import os
import sys
import json
import time
import re
import base64
import shutil
import asyncio
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from PIL import Image, ImageDraw, ImageFont
import speech_recognition as sr
import edge_tts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from access_control import AccessControl
from auth_http import AuthHTTPMixin
from analytics_store import query as query_analytics, record as record_analytics
DATA_DIR = os.path.realpath(os.environ.get("DATA_DIR", BASE_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
ANALYTICS_DB = os.path.join(DATA_DIR, "analytics.sqlite3")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
SECRET_FILE = os.path.join(DATA_DIR, ".secrets.local.json")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
REELS_DIR = os.path.join(DATA_DIR, "reels")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads_reales")
VOICES_DIR = os.path.join(DATA_DIR, "voices")
TEMP_DIR = os.path.join(DATA_DIR, "temp_build")

os.makedirs(PUBLIC_DIR, exist_ok=True)
os.makedirs(REELS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(VOICES_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Copiar imágenes de catálogo iniciales a uploads_reales si está vacío
src_imgs = os.path.join(PARENT_DIR, "public", "images")
if os.path.exists(src_imgs) and not os.listdir(UPLOADS_DIR):
    for f in os.listdir(src_imgs):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            shutil.copy2(os.path.join(src_imgs, f), os.path.join(UPLOADS_DIR, f))

src_reels = os.path.join(PARENT_DIR, "reels_publicidad")
reels_init_flag = os.path.join(REELS_DIR, ".initialized")
if os.path.exists(src_reels) and not os.path.exists(reels_init_flag):
    for f in os.listdir(src_reels):
        if f.lower().endswith(".mp4"):
            dest_f = os.path.join(REELS_DIR, f)
            if not os.path.exists(dest_f):
                shutil.copy2(os.path.join(src_reels, f), dest_f)
    with open(reels_init_flag, "w") as flag_f:
        flag_f.write("1")

if not os.path.exists(STATE_FILE):
    shutil.copy2(os.path.join(BASE_DIR, "seed-state.json"), STATE_FILE)

state_lock = threading.Lock()
state_operation_lock = threading.RLock()
telegram_status = {"running": False, "botUsername": None, "lastError": None}

def load_state():
    with state_lock:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                # Compatibilidad: los antiguos "pedidos" solo medían clics.
                analytics = state.get("storeAnalytics", {})
                for group in ("productStats", "adStats"):
                    for item in analytics.get(group, {}).values():
                        if "whatsappClicks" not in item:
                            item["whatsappClicks"] = item.get("whatsappOrders", 0)
                        if group == "adStats" and "whatsappCartValue" not in item:
                            item["whatsappCartValue"] = item.get("revenue", 0)
                for event in analytics.get("recentEvents", []):
                    for key in ("type", "status"):
                        if event.get(key) == "whatsapp_order":
                            event[key] = "whatsapp_click"
                    if event.get("type") == "whatsapp_click":
                        event["detail"] = "Clic a WhatsApp (sin venta confirmada)"
                ai = state.setdefault("aiConfig", {})
                if os.path.exists(SECRET_FILE):
                    with open(SECRET_FILE, "r", encoding="utf-8") as secrets_file:
                        ai["apiKey"] = json.load(secrets_file).get("openaiApiKey", "")
                if os.environ.get("OPENAI_API_KEY"):
                    ai["apiKey"] = os.environ["OPENAI_API_KEY"]
                return state
        return {}

def save_state(data):
    with state_lock:
        persisted = json.loads(json.dumps(data))
        persisted.get("aiConfig", {}).pop("apiKey", None)
        with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(persisted, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(STATE_FILE + ".tmp", STATE_FILE)

# ==============================================================================
# CÁLCULOS FINANCIEROS, DEVOLUCIONES Y CPA MÁXIMO DE META ADS
# ==============================================================================
def calculate_product_metrics(product, biz):
    price = float(product.get("price", 0))
    cost = float(product.get("cost", 0))
    pkg = float(product.get("packagingCost", 500))
    disc_pct = float(biz.get("transferDiscountPercent", 15))
    ret_pct = float(biz.get("returnReservePercent", 2.0))

    transfer_price = round(price * (1 - disc_pct / 100.0))
    return_reserve = round(price * (ret_pct / 100.0))
    total_direct_cost = cost + pkg + return_reserve

    net_margin_regular = price - total_direct_cost
    net_margin_transfer = transfer_price - total_direct_cost

    cpa_max = max(0, round(net_margin_transfer * 0.85))
    cpa_ideal = max(0, round(net_margin_transfer * 0.30))

    return {
        "id": product["id"],
        "name": product["name"],
        "price": int(price),
        "transferPrice": int(transfer_price),
        "cost": int(cost),
        "packagingCost": int(pkg),
        "returnReserve": int(return_reserve),
        "netMarginRegular": int(net_margin_regular),
        "netMarginTransfer": int(net_margin_transfer),
        "cpaIdeal": int(cpa_ideal),
        "cpaMax": int(cpa_max)
    }

def compute_store_analytics(state, filters=None):
    return query_analytics(ANALYTICS_DB, state.get("products", []), filters)

# ==============================================================================
# MOTOR DE EDICIÓN REAL CON FFMPEG (CLIPS DE VIDEO Y FOTOS REALES DEL CELULAR)
# 0% IA GENERATIVA DE IMAGEN: RESPETA 100% EL PRODUCTO Y LA TEXTURA ORIGINAL
# ==============================================================================
def get_font(size, bold=True):
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "seguiemb.ttf" if bold else "segoeui.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arial.ttf"
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()

def create_overlay_frame_rgba(badge, headline, subline, cta_text, out_png):
    """
    Crea un marco transparente (RGBA 1080x1920) en tonos pastel cálidos (crema, salvia suave y grafito, cero flúor/neón),
    dejando una ventana central 100% transparente (x=60..1020, y=350..1270) donde se ve
    el clip de video real o la foto real del celular SIN TOCAR NI ALTERAR LA TEXTURA DEL PRODUCTO.
    """
    w, h = 1080, 1920
    im = Image.new("RGBA", (w, h), (246, 243, 236, 255))
    draw = ImageDraw.Draw(im)

    for y in range(h):
        r = int(246 - (246 - 237) * (y / h))
        g = int(243 - (243 - 233) * (y / h))
        b = int(236 - (236 - 224) * (y / h))
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    # Ventana transparente para el video/foto original (960x920)
    vx, vy, vw, vh = 60, 350, 960, 920
    draw.rectangle([vx, vy, vx + vw, vy + vh], fill=(0, 0, 0, 0))
    draw.rounded_rectangle([vx - 2, vy - 2, vx + vw + 2, vy + vh + 2], radius=16, outline=(196, 189, 176, 255), width=4)

    font_brand = get_font(30, True)
    draw.text((60, 75), "ALMOHADAS VISCOELÁSTICAS CÓRDOBA", fill=(92, 122, 103, 255), font=font_brand)

    if badge:
        font_badge = get_font(26, True)
        draw.rounded_rectangle([60, 135, 580, 198], radius=12, fill=(214, 229, 218, 255))
        draw.text((80, 150), badge[:26].upper(), fill=(47, 72, 56, 255), font=font_badge)

    font_head = get_font(40, True)
    draw.text((60, 228), headline[:34].upper(), fill=(44, 48, 46, 255), font=font_head)

    font_sub = get_font(33, True)
    draw.text((60, 1315), subline, fill=(92, 74, 62, 255), font=font_sub)

    if cta_text:
        py = 1580
        draw.rounded_rectangle([70, py, 1010, py + 115], radius=22, fill=(116, 148, 127, 255))
        font_cta = get_font(32, True)
        draw.text((100, py + 36), cta_text[:32].upper(), fill=(255, 255, 255, 255), font=font_cta)

    font_foot = get_font(24, True)
    draw.text((70, 1750), "100% Producto Real • Entregas en Córdoba • tienda-marcos.pages.dev", fill=(115, 119, 116, 255), font=font_foot)
    im.save(out_png)

def render_segment_from_real_media(media_path, badge, headline, subline, cta_text, out_seg_mp4, duration=5):
    """
    Toma un clip de video real (.mp4/.mov/.webm) o una foto real (.jpg/.png/.webp) subida por el usuario,
    y genera un segmento MP4 1080x1920 a 30fps sin voz, respetando el 100% de la imagen/video original.
    """
    ext = os.path.splitext(media_path)[1].lower()
    overlay_png = out_seg_mp4.replace(".mp4", "_overlay.png")
    create_overlay_frame_rgba(badge, headline, subline, cta_text, overlay_png)

    is_video = ext in (".mp4", ".mov", ".webm", ".avi", ".mkv")

    if is_video:
        # Escalar y encuadrar el clip de video real en la ventana de 960x920 (x=60, y=350) sin deformar proporción
        filter_complex = (
            "[0:v]scale=960:920:force_original_aspect_ratio=increase,crop=960:920,setsar=1,fps=30[vid];"
            "color=c=#F6F3EC:s=1080x1920:r=30[bg];"
            "[bg][vid]overlay=60:350:shortest=1[tmp];"
            "[tmp][1:v]overlay=0:0"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", media_path,
            "-i", overlay_png,
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            out_seg_mp4
        ]
    else:
        # Foto real del celular/cámara: encuadrar intacta en 960x920 sin alterar texturas ni aplicar filtros IA
        slide_jpg = out_seg_mp4.replace(".mp4", "_static.jpg")
        with Image.open(overlay_png) as ov_im:
            base_im = Image.new("RGB", (1080, 1920), (255, 255, 255))
            with Image.open(media_path) as pim:
                pim = pim.convert("RGB")
                cw, ch = 960, 920
                scale = max(cw / pim.width, ch / pim.height)
                sw, sh = int(pim.width * scale), int(pim.height * scale)
                resized = pim.resize((sw, sh), Image.Resampling.LANCZOS)
                ox, oy = (sw - cw) // 2, (sh - ch) // 2
                cropped = resized.crop((ox, oy, ox + cw, oy + ch))
                base_im.paste(cropped, (60, 350))
            base_im.paste(ov_im, (0, 0), ov_im)
            base_im.save(slide_jpg, quality=95)

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(duration), "-i", slide_jpg,
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            out_seg_mp4
        ]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def list_uploaded_media():
    valid_exts = (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp")
    items = []
    if os.path.exists(UPLOADS_DIR):
        for fname in sorted(os.listdir(UPLOADS_DIR), key=lambda x: os.path.getmtime(os.path.join(UPLOADS_DIR, x)), reverse=True):
            if fname.lower().endswith(valid_exts):
                ext = os.path.splitext(fname)[1].lower()
                mtype = "video" if ext in (".mp4", ".mov", ".webm") else "image"
                items.append({"name": fname, "type": mtype, "url": f"/uploads/{fname}"})
    return items

def generate_visual_reel(angle="cervical", custom_title=None, selected_files=None):
    state = load_state()
    biz = state.get("business", {})
    products = state.get("products", [])
    p_pack_cerv = next((p for p in products if p["id"] == "pack-2-cervicales"), products[0])
    p_cerv = next((p for p in products if p["id"] == "cervical-individual"), products[0])

    disc = biz.get("transferDiscountPercent", 15)
    pack_transf = round(p_pack_cerv["price"] * (1 - disc / 100.0))
    cerv_transf = round(p_cerv["price"] * (1 - disc / 100.0))

    # Obtener los archivos reales subidos por el usuario (clips de video o fotos reales)
    uploaded = list_uploaded_media()
    if selected_files and len(selected_files) > 0:
        media_paths = [os.path.join(UPLOADS_DIR, f) for f in selected_files if os.path.exists(os.path.join(UPLOADS_DIR, f))]
    else:
        # Usar los últimos 3 archivos subidos (priorizando clips de video/fotos reales recién subidas)
        media_paths = [os.path.join(UPLOADS_DIR, item["name"]) for item in uploaded[:3]]

    if not media_paths:
        raise RuntimeError("No hay fotos ni clips cargados en la galería de archivos reales.")

    # Si subió 1 o 2 archivos, repetirlos para cubrir las 3 escenas del Reel
    while len(media_paths) < 3:
        media_paths.append(media_paths[-1])

    ts = datetime.now().strftime("%H%M%S")
    angle_key = angle.lower()

    if "pack" in angle_key or "oferta" in angle_key or "2" in angle_key:
        reel_name = f"REEL_REAL_PACK2_{ts}.mp4"
        scenes_text = [
            ("PROMO PACK X2", custom_title or "PACK X2 VISCOELÁSTICAS", f"PRECIO LISTA: ${p_pack_cerv['price']:,}\nCON TRANSFERENCIA: ${pack_transf:,}".replace(",", "."), f"{disc}% OFF EXTRA TRANSFERENCIA"),
            ("CALIDAD REAL", "ESPUMA CON MEMORIA REAL", "2 ALMOHADAS DE 60x40 CM\nCON FUNDA CON CIERRE LAVABLE", "NO SE DEFORMA CON EL USO"),
            ("ENVÍOS EN CÓRDOBA", "RECIBILO HOY EN TU CASA", f"DENTRO DE CIRCUNVALACIÓN ${biz.get('shippingInsideRing', 3000):,}\nENVÍO GRATIS +$100.000".replace(",", "."), "PEDÍ DIRECTO POR WHATSAPP")
        ]
    elif "rebote" in angle_key or "memoria" in angle_key or "demo" in angle_key:
        reel_name = f"REEL_REAL_REBOTE_{ts}.mp4"
        scenes_text = [
            ("PRODUCTO 100% REAL", custom_title or "MIRÁ LA DENSIDAD REAL", "ESPUMA VISCOELÁSTICA TERMOSENSIBLE\nRECUPERA SU FORMA AL INSTANTE", "SIN RELLENOS QUE SE APLASTAN"),
            ("DETALLES DE CALIDAD", "FUNDA SUAVE CON CIERRE", f"DESDE ${cerv_transf:,} ABONANDO\nCON {disc}% OFF TRANSFERENCIA".replace(",", "."), "DESMONTABLE Y LAVABLE"),
            ("CÓRDOBA CAPITAL", "ENTREGA RÁPIDA EN EL DÍA", "VERIFICÁS LA CALIDAD AL RECIBIR\nATENCIÓN DIRECTA EN WHATSAPP", "TOCÁ EL LINK DEL CATÁLOGO")
        ]
    else:
        reel_name = f"REEL_REAL_CERVICAL_{ts}.mp4"
        scenes_text = [
            ("ALIVIO CERVICAL", custom_title or "¿DOLOR DE CUELLO AL DORMIR?", "DISEÑO ANATÓMICO DE DOBLE ALTURA\nALINEA CUELLO Y COLUMNA", "ESPUMA CON MEMORIA REAL"),
            ("PRECIO DIRECTO", "INDIVIDUAL O PACK X2 AHORRO", f"INDIVIDUAL: ${cerv_transf:,} C/ TRANSF.\nPACK X2: ${pack_transf:,} C/ TRANSF.".replace(",", "."), f"{disc}% OFF POR TRANSFERENCIA"),
            ("CÓRDOBA CAPITAL", "ENVÍOS RÁPIDOS A DOMICILIO", "DENTRO DE CIRCUNVALACIÓN $3.000\nFUERA DE CIRCUNVALACIÓN $5.000", "PEDÍ LA TUYA POR WHATSAPP")
        ]

    max_clips = min(len(media_paths), 8)
    seg_dur = 4 if max_clips > 3 else 5
    seg_files = []
    for idx in range(max_clips):
        mpath = media_paths[idx]
        b_txt, h_txt, s_txt, c_txt = scenes_text[idx % len(scenes_text)]
        seg_mp4 = os.path.join(TEMP_DIR, f"seg_{ts}_{idx}.mp4")
        render_segment_from_real_media(mpath, b_txt, h_txt, s_txt, c_txt, seg_mp4, duration=seg_dur)
        seg_files.append(seg_mp4)

    concat_txt = os.path.join(TEMP_DIR, f"concat_segs_{ts}.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for sf in seg_files:
            f.write(f"file '{sf}'\n")

    out_mp4 = os.path.join(REELS_DIR, reel_name)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_txt,
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        out_mp4
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return reel_name

# ==============================================================================
# MOTOR DE VOZ DEL AGENTE: ESCUCHAR AUDIOS (STT) Y RESPONDER CON VOZ (TTS)
# (Los Reels MP4 siguen 100% sin voz; esto es solo para hablar con el Agente)
# ==============================================================================
def clean_text_for_speech(text):
    """Limpia markdown, emojis y símbolos para que la nota de voz en español argentino suene 100% natural."""
    s = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"(?m)^\s*(?:\d+[.)]|[-*•#]+)\s+", "", s)
    s = s.replace("…", ".").replace("...", ".").replace("—", ", ").replace("–", ", ")
    s = re.sub(r"`[^`]+`", "", s)
    s = s.replace("**", "").replace("*", "").replace(">", "")
    s = s.replace("tienda-marcos.pages.dev", "tu tienda online")
    s = re.sub(r"\$(\d{1,3}(?:\.\d{3})+|\d+)", r"\1 pesos", s)
    s = s.replace("CPA", "costo por compra").replace("CPC", "costo por clic")
    s = s.replace("15% OFF", "15 por ciento de descuento").replace("Transf.", "con transferencia")
    s = re.sub(r"[^\w\s,.:;¿?¡!%+-áéíóúÁÉÍÓÚñÑüÜ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > 700:
        s = s[:700].rsplit(" ", 1)[0] + ". Te dejé el detalle completo por escrito en el chat."
    return s

def generate_agent_voice_note(reply_text):
    """
    Genera una nota de voz en español argentino (es-AR-TomasNeural) tanto en .mp3 (para el panel web)
    como en .ogg Opus (para enviar como Nota de Voz nativa en Telegram con sendVoice).
    """
    spoken = clean_text_for_speech(reply_text)
    if not spoken:
        return None, None
    ts = datetime.now().strftime("%H%M%S_%f")[:10]
    mp3_name = f"voz_agente_{ts}.mp3"
    ogg_name = f"voz_agente_{ts}.ogg"
    mp3_path = os.path.join(VOICES_DIR, mp3_name)
    ogg_path = os.path.join(VOICES_DIR, ogg_name)

    async def _tts():
        comm = edge_tts.Communicate(spoken, "es-AR-TomasNeural", rate="+6%")
        await comm.save(mp3_path)

    asyncio.run(_tts())
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus", "-b:a", "48k", "-vbr", "on", ogg_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return mp3_name, ogg_path

def transcribe_voice_file(audio_path):
    """
    Convierte un audio entrante de Telegram (.ogg/.oga) o del navegador (.webm/.wav/.mp3)
    a WAV 16kHz mono y lo transcribe a texto en español argentino (es-AR).
    """
    wav_path = audio_path + "_16k.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    recognizer = sr.Recognizer()
    with sr.AudioFile(wav_path) as src_audio:
        audio_data = recognizer.record(src_audio)
    try:
        os.remove(wav_path)
    except Exception:
        pass
    return recognizer.recognize_google(audio_data, language="es-AR")

# ==============================================================================
# CEREBRO DEL AGENTE: PROCESA ÓRDENES DESDE EL PANEL WEB O TELEGRAM
# ==============================================================================
def execute_agent_instruction(user_text, source="panel", selected_files=None, generate_voice=False, is_voice_input=False, analytics_filters=None):
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    from agent_runtime import execute
    return execute(user_text, source, selected_files, generate_voice, is_voice_input, api={**globals(), "analytics_filters": analytics_filters or {}})


def send_telegram_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    urllib.request.urlopen(req, timeout=15)

def send_telegram_voice(token, chat_id, ogg_path, caption=""):
    """Envía una Nota de Voz nativa (con ondas de audio) al chat de Telegram usando sendVoice."""
    boundary = "----WebKitFormBoundaryVoice7MA4YWxk"
    url = f"https://api.telegram.org/bot{token}/sendVoice"
    filename = os.path.basename(ogg_path)
    with open(ogg_path, "rb") as f:
        voice_bytes = f.read()

    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8"))
    if caption:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:900]}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"{filename}\"\r\nContent-Type: audio/ogg\r\n\r\n".encode("utf-8")
        + voice_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    body = b"".join(parts)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req, timeout=30)

def send_telegram_video(token, chat_id, video_path, caption=""):
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8"))
    if caption:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:900]}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{filename}\"\r\nContent-Type: video/mp4\r\n\r\n".encode("utf-8")
        + video_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    body = b"".join(parts)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req, timeout=45)

def download_telegram_file(token, file_id, ext_hint=".jpg", target_dir=None):
    info_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
    with urllib.request.urlopen(info_url, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
        file_path = data["result"]["file_path"]
    ext = os.path.splitext(file_path)[1] or ext_hint
    dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    fname = f"tg_real_{datetime.now().strftime('%H%M%S')}_{file_id[-6:]}{ext}"
    folder = target_dir or UPLOADS_DIR
    dest = os.path.join(folder, fname)
    with urllib.request.urlopen(dl_url, timeout=30) as r, open(dest, "wb") as out_f:
        out_f.write(r.read())
    return fname if target_dir is None else dest

def telegram_polling_loop():
    last_update_id = 0
    while True:
        try:
            state = load_state()
            tg = state.get("telegramConfig", {})
            token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("botToken", "").strip()
            if not token:
                telegram_status["running"] = False
                time.sleep(3)
                continue

            if not telegram_status["running"]:
                me_url = f"https://api.telegram.org/bot{token}/getMe"
                with urllib.request.urlopen(me_url, timeout=10) as r:
                    me_data = json.loads(r.read().decode("utf-8"))
                    if me_data.get("ok"):
                        telegram_status["running"] = True
                        telegram_status["botUsername"] = me_data["result"].get("username")
                        telegram_status["lastError"] = None

            poll_url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=15"
            with urllib.request.urlopen(poll_url, timeout=20) as r:
                updates = json.loads(r.read().decode("utf-8"))
                if updates.get("ok"):
                    for upd in updates.get("result", []):
                        last_update_id = upd["update_id"]
                        msg = upd.get("message") or upd.get("edited_message")
                        if not msg:
                            continue
                        chat_id = str(msg["chat"]["id"])

                        # 1. Si manda una NOTA DE VOZ o AUDIO por Telegram
                        voice_obj = msg.get("voice") or msg.get("audio")
                        if voice_obj:
                            fid = voice_obj["file_id"]
                            try:
                                audio_local_path = download_telegram_file(token, fid, ".ogg", target_dir=TEMP_DIR)
                                transcribed = transcribe_voice_file(audio_local_path)
                                res = execute_agent_instruction(
                                    transcribed,
                                    source="telegram",
                                    generate_voice=True,
                                    is_voice_input=True
                                )
                                clean_reply = (
                                    f"Escuché tu orden: \"{transcribed}\"\n\n"
                                    + res["reply"].replace("**", "").replace("*", "")
                                )
                                send_telegram_message(token, chat_id, clean_reply)

                                if res.get("voiceOggPath") and os.path.exists(res["voiceOggPath"]):
                                    send_telegram_voice(token, chat_id, res["voiceOggPath"], caption="Respuesta en voz del Agente")

                                if res.get("reel"):
                                    video_full = os.path.join(REELS_DIR, res["reel"])
                                    if os.path.exists(video_full):
                                        send_telegram_video(token, chat_id, video_full, caption=f"Reel {res['reel']} (100% producto real, sin voz)")
                            except Exception as ve:
                                send_telegram_message(token, chat_id, f"No pude escuchar bien el audio ({ve}). ¿Podés repetir el mensaje de voz un poquito más claro?")
                            continue

                        # 2. Si manda un VIDEO corto desde el celular por Telegram
                        if "video" in msg:
                            fid = msg["video"]["file_id"]
                            saved_name = download_telegram_file(token, fid, ".mp4")
                            send_telegram_message(
                                token,
                                chat_id,
                                f"Clip de video real recibido ({saved_name}) y guardado intacto.\nMandame más clips/fotos o mandame un audio diciendo 'Armame el reel' para unirlos respetando 100% el producto."
                            )
                            continue

                        # 3. Si manda una FOTO real desde el celular por Telegram
                        if "photo" in msg and len(msg["photo"]) > 0:
                            fid = msg["photo"][-1]["file_id"]
                            saved_name = download_telegram_file(token, fid, ".jpg")
                            send_telegram_message(
                                token,
                                chat_id,
                                f"Foto real de tu celular recibida ({saved_name}) y guardada intacta.\nMandame más fotos/clips o mandame un audio diciendo 'Armame el reel' para generar el video."
                            )
                            continue

                        # 4. Si manda TEXTO por Telegram (también responde con texto + Nota de Voz)
                        user_text = msg.get("text", "").strip()
                        if not user_text:
                            continue

                        res = execute_agent_instruction(user_text, source="telegram", generate_voice=True)
                        clean_reply = res["reply"].replace("**", "").replace("*", "")
                        send_telegram_message(token, chat_id, clean_reply)

                        if res.get("voiceOggPath") and os.path.exists(res["voiceOggPath"]):
                            send_telegram_voice(token, chat_id, res["voiceOggPath"], caption="Respuesta en voz del Agente")

                        if res.get("reel"):
                            video_full = os.path.join(REELS_DIR, res["reel"])
                            if os.path.exists(video_full):
                                send_telegram_video(token, chat_id, video_full, caption=f"Reel {res['reel']} (100% producto real, sin voz)")
        except Exception as e:
            telegram_status["running"] = False
            telegram_status["lastError"] = str(e)
            time.sleep(5)

# ==============================================================================
# SERVIDOR HTTP + API REST
# ==============================================================================
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    def __init__(self, *args, **kwargs):
        self.access = access_control
        self.data_dir = DATA_DIR
        self.public_dir = PUBLIC_DIR
        self.store_origins = set(os.environ.get("STORE_ALLOWED_ORIGINS", "https://tienda-marcos.pages.dev").split(","))
        if access_control.local_setup:
            self.store_origins.update({"http://localhost:4321", "http://127.0.0.1:4321"})
        super().__init__(*args, **kwargs)
    daemon_threads = True
    allow_reuse_address = False

class AgentRequestHandler(AuthHTTPMixin, SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        origin = self.headers.get("Origin", "")
        if urllib.parse.urlparse(self.path).path == "/api/track" and origin in self.server.store_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        if urllib.parse.urlparse(self.path).path != "/api/track" or origin not in self.server.store_origins:
            self._auth_json({"error": "Origen no autorizado"}, 403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self._auth_json({"error": "Método no permitido"}, 405)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self._auth_get(path):
            return

        pwa_assets = {
            "/manifest.webmanifest": "application/manifest+json",
            "/sw.js": "application/javascript",
            "/pwa.js": "application/javascript",
            "/offline.html": "text/html; charset=utf-8",
            "/icons/ariel-192.png": "image/png",
            "/icons/ariel-512.png": "image/png",
            "/icons/ariel-maskable-512.png": "image/png",
        }
        if path in pwa_assets:
            asset = os.path.join(PUBLIC_DIR, path.lstrip("/"))
            if not os.path.isfile(asset):
                self.send_error(404)
                return
            with open(asset, "rb") as file:
                body = file.read()
            self.send_response(200)
            self.send_header("Content-Type", pwa_assets[path])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            if path == "/sw.js":
                self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/store-status":
            self._send_json(store_monitor.snapshot())
            return

        if path == "/api/analytics":
            try:
                filters = {key: value[0] for key, value in urllib.parse.parse_qs(parsed.query).items()
                           if key in ("start_date", "end_date", "product_id", "campaign")}
                self._send_json(compute_store_analytics(load_state(), filters))
            except ValueError as error:
                self._send_json({"error": str(error)}, 400)
            return

        if path == "/api/state":
            state = load_state()
            biz = state.get("business", {})
            hidden_reels = set(state.get("deletedReels", []))
            for hr in list(hidden_reels):
                hr_path = os.path.join(REELS_DIR, hr)
                if os.path.exists(hr_path):
                    try:
                        os.remove(hr_path)
                    except Exception:
                        pass
            enriched_products = [calculate_product_metrics(p, biz) for p in state.get("products", [])]
            reels = sorted(
                [f for f in os.listdir(REELS_DIR) if f.endswith(".mp4") and f not in hidden_reels],
                key=lambda x: os.path.getmtime(os.path.join(REELS_DIR, x)),
                reverse=True
            )
            self._send_json({
                "business": biz,
                "products": enriched_products,
                "storeAnalytics": compute_store_analytics(state),
                "agentMemory": state.get("agentMemory", {"learnedRules": [], "campaignHistory": []}),
                "chatHistory": state.get("chatHistory", []),
                "reels": reels,
                "uploads": list_uploaded_media(),
                "telegramStatus": telegram_status,
                "telegramConfigured": bool(state.get("telegramConfig", {}).get("botToken")),
                "aiConfigured": bool(state.get("aiConfig", {}).get("apiKey"))
            })
            return

        if path.startswith("/reels/"):
            fname = os.path.basename(path)
            fpath = os.path.join(REELS_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f"inline; filename=\"{fname}\"")
                self.end_headers()
                self.wfile.write(data)
                return

        if path.startswith("/voices/"):
            fname = os.path.basename(path)
            fpath = os.path.join(VOICES_DIR, fname)
            if os.path.exists(fpath):
                ctype = "audio/ogg" if fname.endswith(".ogg") else "audio/mpeg"
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

        if path.startswith("/uploads/"):
            fname = os.path.basename(path)
            fpath = os.path.join(UPLOADS_DIR, fname)
            if os.path.exists(fpath):
                ext = os.path.splitext(fname)[1].lower()
                ctype = "video/mp4" if ext in (".mp4", ".mov", ".webm") else "image/jpeg"
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

        if path == "/" or path == "/index.html":
            index_file = os.path.join(PUBLIC_DIR, "index.html")
            if os.path.exists(index_file):
                with open(index_file, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

        self.send_error(404, "Not Found")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/api/chat", "/api/voice-order", "/api/products", "/api/config", "/api/memory/add", "/api/memory/delete", "/api/clear-chat", "/api/delete-reel"):
            with state_operation_lock:
                return self._handle_post()
        return self._handle_post()

    def _handle_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self._auth_post(path):
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            payload = json.loads(raw_body) if raw_body else {}
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError):
            self._auth_json({"error": "JSON inválido"}, 400)
            return
        if self._handle_auth_post(path, payload):
            return

        if path == "/api/store-status/scan":
            threading.Thread(target=store_monitor.scan, daemon=True).start()
            self._send_json({"accepted": True}, 202)
            return

        if path == "/api/chat":
            msg = payload.get("message", "").strip()
            selected_files = payload.get("selectedFiles", None)
            gen_voice = bool(payload.get("generateVoice", False))
            is_voice_in = bool(payload.get("isVoiceInput", False))
            if not msg:
                self._send_json({"error": "Mensaje vacío"}, 400)
                return
            res = execute_agent_instruction(
                msg,
                source="panel",
                selected_files=selected_files,
                generate_voice=gen_voice,
                is_voice_input=is_voice_in,
                analytics_filters=payload.get("analyticsFilters", {})
            )
            self._send_json(res)
            return

        if path == "/api/voice-order":
            # Recibe un audio grabado desde el micrófono del panel web o subido en base64, lo transcribe y responde con voz
            b64 = payload.get("audioBase64", "")
            ext = payload.get("ext", ".webm")
            selected_files = payload.get("selectedFiles", None)
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            if not b64:
                self._send_json({"error": "Audio vacío"}, 400)
                return
            raw_audio = base64.b64decode(b64)
            tmp_audio = os.path.join(TEMP_DIR, f"web_mic_{datetime.now().strftime('%H%M%S_%f')}{ext}")
            with open(tmp_audio, "wb") as f:
                f.write(raw_audio)
            try:
                transcribed = transcribe_voice_file(tmp_audio)
                res = execute_agent_instruction(
                    transcribed,
                    source="panel",
                    selected_files=selected_files,
                    generate_voice=True,
                    is_voice_input=True,
                    analytics_filters=payload.get("analyticsFilters", {})
                )
                self._send_json(res)
            except Exception as e:
                self._send_json({"error": f"No se pudo transcribir el audio: {e}"}, 400)
            finally:
                try:
                    os.remove(tmp_audio)
                except Exception:
                    pass
            return

        if path == "/api/upload":
            files = payload.get("files", [])
            saved = []
            for fobj in files:
                raw_name = re.sub(r"[^a-zA-Z0-9._-]", "_", fobj.get("name", "archivo.jpg"))
                b64 = fobj.get("dataBase64", "")
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                raw_bytes = base64.b64decode(b64)
                final_name = f"real_{datetime.now().strftime('%H%M%S')}_{raw_name}"
                dest_path = os.path.join(UPLOADS_DIR, final_name)
                with open(dest_path, "wb") as out_f:
                    out_f.write(raw_bytes)
                saved.append(final_name)
            self._send_json({"ok": True, "saved": saved})
            return

        if path == "/api/delete-reel":
            raw_fname = urllib.parse.unquote(payload.get("filename", "")).strip()
            if not raw_fname:
                self._send_json({"error": "Archivo inválido"}, 400)
                return
            state = load_state()
            deleted_list = set(state.get("deletedReels", []))

            if raw_fname == "ALL":
                for f in os.listdir(REELS_DIR):
                    if f.endswith(".mp4"):
                        fpath = os.path.join(REELS_DIR, f)
                        try:
                            os.remove(fpath)
                        except Exception:
                            deleted_list.add(f)
                for m in state.get("chatHistory", []):
                    m.pop("reel", None)
            else:
                fname = os.path.basename(raw_fname)
                fpath = os.path.join(REELS_DIR, fname)
                if os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                        deleted_list.discard(fname)
                    except Exception:
                        deleted_list.add(fname)
                else:
                    deleted_list.add(fname)
                for m in state.get("chatHistory", []):
                    if m.get("reel") == f"/reels/{fname}":
                        m.pop("reel", None)

            state["deletedReels"] = list(deleted_list)
            save_state(state)
            self._send_json({"ok": True})
            return

        if path == "/api/delete-upload":
            fname = os.path.basename(urllib.parse.unquote(payload.get("filename", "")).strip())
            if not fname:
                self._send_json({"error": "Archivo inválido"}, 400)
                return
            fpath = os.path.join(UPLOADS_DIR, fname)
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except Exception as e:
                    self._send_json({"error": str(e)}, 500)
                    return
            self._send_json({"ok": True})
            return

        if path == "/api/clear-chat":
            state = load_state()
            state["chatHistory"] = []
            save_state(state)
            self._send_json({"ok": True})
            return

        if path == "/api/build-reel":
            angle = payload.get("angle", "cervical")
            selected_files = payload.get("selectedFiles", None)
            try:
                generated_reel = generate_visual_reel(angle=angle, selected_files=selected_files)
                self._send_json({"ok": True, "reel": generated_reel})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        if path == "/api/memory/add":
            rule_text = payload.get("text", "").strip()
            if not rule_text:
                self._send_json({"error": "Regla vacía"}, 400)
                return
            state = load_state()
            mem = state.setdefault("agentMemory", {"learnedRules": [], "campaignHistory": []})
            rules = mem.setdefault("learnedRules", [])
            new_rule = {
                "id": f"mem-{int(time.time())}",
                "text": rule_text,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "category": payload.get("category", "Aprendido de Marcos")
            }
            rules.insert(0, new_rule)
            save_state(state)
            self._send_json({"ok": True, "agentMemory": mem})
            return

        if path == "/api/memory/delete":
            rule_id = payload.get("id", "").strip()
            state = load_state()
            mem = state.setdefault("agentMemory", {"learnedRules": [], "campaignHistory": []})
            mem["learnedRules"] = [r for r in mem.get("learnedRules", []) if r.get("id") != rule_id]
            save_state(state)
            self._send_json({"ok": True, "agentMemory": mem})
            return

        if path == "/api/products":
            state = load_state()
            biz = state.setdefault("business", {})
            if "transferDiscountPercent" in payload:
                biz["transferDiscountPercent"] = float(payload["transferDiscountPercent"])
            if "returnReservePercent" in payload:
                biz["returnReservePercent"] = float(payload["returnReservePercent"])
            if "products" in payload and isinstance(payload["products"], list):
                cleaned = []
                for idx, item in enumerate(payload["products"]):
                    pid = item.get("id") or f"prod-{idx+1}-{int(time.time())}"
                    cleaned.append({
                        "id": pid,
                        "name": str(item.get("name", "Producto")).strip() or "Producto",
                        "price": int(float(item.get("price", 0))),
                        "cost": int(float(item.get("cost", 0))),
                        "packagingCost": int(float(item.get("packagingCost", 500)))
                    })
                state["products"] = cleaned
            save_state(state)
            enriched = [calculate_product_metrics(p, biz) for p in state.get("products", [])]
            self._send_json({"ok": True, "business": biz, "products": enriched})
            return

        if path == "/api/track":
            try:
                result = record_analytics(ANALYTICS_DB, payload)
                self._send_json({"ok": True, **result})
            except (ValueError, TypeError) as error:
                self._send_json({"error": str(error)}, 400)
            return

        if path == "/api/analytics/reset":
            state = load_state()
            sa = state.setdefault("storeAnalytics", {})
            sa["pageViews"] = 0
            sa["uniqueVisitors"] = 0
            sa["totalClicks"] = 0
            sa["totalSessionsForDuration"] = 0
            sa["totalDurationSeconds"] = 0
            sa["addToCartCount"] = 0
            sa["whatsappCheckoutClicks"] = 0
            sa["abandonedCartsCount"] = 0
            sa["abandonedCartsValue"] = 0
            for pid, pdata in sa.get("productStats", {}).items():
                pdata["views"] = 0
                pdata["addedToCart"] = 0
                pdata["whatsappClicks"] = 0
                pdata["abandoned"] = 0
            for akey, adata in sa.get("adStats", {}).items():
                adata["views"] = 0
                adata["clicks"] = 0
                adata["sessions"] = 0
                adata["totalDurationSeconds"] = 0
                adata["addedToCart"] = 0
                adata["whatsappClicks"] = 0
                adata["abandoned"] = 0
                adata["whatsappCartValue"] = 0
            sa["recentEvents"] = []
            save_state(state)
            self._send_json({"ok": True, "storeAnalytics": compute_store_analytics(state)})
            return

        if path == "/api/config":
            state = load_state()
            if "botToken" in payload:
                state.setdefault("telegramConfig", {})["botToken"] = payload["botToken"].strip()
                telegram_status["running"] = False
            if "apiKey" in payload:
                api_key = payload["apiKey"].strip()
                if api_key:
                    with state_lock:
                        with open(SECRET_FILE + ".tmp", "w", encoding="utf-8") as secrets_file:
                            json.dump({"openaiApiKey": api_key}, secrets_file)
                        os.replace(SECRET_FILE + ".tmp", SECRET_FILE)
            if "apiUrl" in payload and payload["apiUrl"].strip():
                state.setdefault("aiConfig", {})["apiUrl"] = payload["apiUrl"].strip()
            if "model" in payload and payload["model"].strip():
                state.setdefault("aiConfig", {})["model"] = payload["model"].strip()
            save_state(state)
            self._send_json({"ok": True})
            return

        self.send_error(404, "Not Found")

from store_monitor import StoreMonitor
access_control = AccessControl(DATA_DIR, os.environ.get("PUBLIC_PANEL_URL", ""), os.environ.get("ARIEL_LOCAL_SETUP") == "1")
store_monitor = StoreMonitor(os.path.join(DATA_DIR, "store-monitor.sqlite3"), lambda: load_state().get("products", []))

if __name__ == "__main__":
    store_monitor.start()
    if os.environ.get("ENABLE_TELEGRAM") == "1":
        threading.Thread(target=telegram_polling_loop, daemon=True).start()
    host = "127.0.0.1" if access_control.local_setup else "0.0.0.0"
    port = int(os.environ.get("PORT", "4567"))
    server = ThreadedHTTPServer((host, port), AgentRequestHandler)
    print(f"Ariel escuchando en el puerto {port}", flush=True)
    server.serve_forever()
