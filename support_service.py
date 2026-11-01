"""Ayuda contextual y reportes locales. No envía correos ni mensajes externos."""
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone, timedelta

SECTIONS = {'tools':'Herramientas', 'gallery':'Galería', 'analytics':'Analíticas', 'status':'Estado de tienda', 'chat':'Chat'}
GUIDE = '''Ariel es un panel comercial. Secciones:
Herramientas: subir varias fotos o videos con Ctrl/Mayús en PC; máximo 20 MB por archivo.
Los archivos quedan seleccionados en el orden de subida. Tocar una miniatura desmarca o agrega al final.
Cada foto genera 5 segundos de zoom y conserva los 3 centrales. Videos conservan duración.
Salida MP4 1080x1920, 9:16, recorte centrado, 30 fps, sin audio. Diez transiciones y corte directo.
Las transiciones solapan hasta 0,3 s por unión. Armar mi reel inicia el montaje con la selección actual.
El montaje usa FFmpeg en el servidor, no una API de video. No modifica ni anima objetos mediante IA.
Galería: hasta 100 archivos activos, favoritos protegidos y papelera recuperable 7 días. Los más antiguos no favoritos ni en uso pasan a papelera al alcanzar el límite. El espacio total incluye papelera, por defecto 2048 MB. Si se llena, se bloquea la subida sin borrar antes de plazo. Se puede seleccionar material y usarlo en un nuevo reel. Quitar del armado no borra el original.
Analíticas: filtros de fechas, producto y campaña. Tablas o gráfico. Clic a WhatsApp no es venta.
Sin eventos significa que no hay registros, no demuestra que no hubo visitas.
Estado de tienda: revisión HTTP/HTML cada hora de 08 a 22 inclusive, hora Córdoba. Revisión manual disponible.
HTTP correcto no prueba que todo el checkout funcione. Un 403/429 no prueba una caída.
Costos: se administran en Herramientas. Las ventas requieren datos confirmados, nunca se deducen de clics.
El catálogo del panel no sincroniza automáticamente con la tienda pública.
Los reportes se guardan localmente para revisión. NO hay envío a correo, soporte externo ni notificaciones al desarrollador.
Ayudar con pasos simples, una prueba por vez. Nunca sugerir borrar datos, restablecer todo, desactivar seguridad o compartir claves.
Si faltan datos, pedir el mensaje exacto y la sección, nunca la contraseña. No afirmar diagnóstico ni arreglo sin verificar.
El asistente de ayuda no puede ejecutar cambios ni reparar el servidor; puede explicar y registrar un reporte local mediante el botón.
'''


def redact(text):
    text = str(text)[:2400]
    text = re.sub(r'https?://[^\s]+', '[enlace omitido]', text)
    text = re.sub(r'(?i)(?:sk-[\w-]+|bearer\s+\S+)', '[credencial omitida]', text)
    text = re.sub(r'(?i)(password|contraseña|api[_ -]?key|token|secret)\s*[:=]\s*\S+', r'\1: [omitido]', text)
    return text


class SupportService:
    def __init__(self, data_dir):
        self.db = os.path.join(data_dir, 'support.sqlite3')
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created TEXT, stamp REAL, section TEXT, code TEXT, route TEXT, description TEXT, occurrences INTEGER DEFAULT 1)')

    def record(self, section='chat', code='USER_REPORT', route='', description=''):
        section = section if isinstance(section,str) and section in SECTIONS else 'chat'
        code = re.sub(r'[^A-Z0-9_]', '', str(code).upper())[:60] or 'USER_REPORT'
        route = str(route).split('?')[0][:150]
        if not re.fullmatch(r'/api/[a-zA-Z0-9_/-]+', route): route = ''
        description = redact(description)
        now = time.time()
        with closing(sqlite3.connect(self.db,timeout=10)) as db, db:
            row = db.execute('SELECT id FROM reports WHERE section=? AND code=? AND route=? AND description=? AND stamp>? ORDER BY stamp DESC LIMIT 1', (section,code,route,description,now-300)).fetchone()
            if row:
                report_id=row[0]
                db.execute('UPDATE reports SET occurrences=occurrences+1 WHERE id=?',(report_id,))
            else:
                report_id=uuid.uuid4().hex[:12]
                db.execute('INSERT INTO reports (id,created,stamp,section,code,route,description) VALUES (?,?,?,?,?,?,?)', (report_id,datetime.now(timezone(timedelta(hours=-3))).isoformat(),now,section,code,route,description))
        return {'id':report_id,'status':'saved_locally','sent':False,
                'message':f'Reporte {report_id} guardado para revisión. No se envió por correo ni a un soporte externo.'}

    def recent(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT id,created,section,code,description,occurrences FROM reports ORDER BY stamp DESC LIMIT 10')]


def answer_help(payload, state):
    message=payload.get('message','')
    if not isinstance(message,str) or not message.strip() or len(message)>2000:
        raise ValueError('Escribí tu duda en hasta 2000 caracteres.')
    section=payload.get('section','tools')
    if not isinstance(section,str) or section not in SECTIONS: raise ValueError('Sección no válida')
    cfg=state.get('aiConfig',{})
    if not cfg.get('apiKey'):
        return 'Para usar la ayuda conversacional falta configurar OpenAI. Podés guardar un reporte con el botón «Registrar problema». No se enviará por correo.'
    from agent_runtime import request_response
    history=payload.get('history',[])
    if not isinstance(history,list): raise ValueError('Conversación inválida')
    messages=[]
    for item in history[-8:]:
        if isinstance(item,dict) and item.get('role') in ('user','assistant') and isinstance(item.get('text'),str):
            messages.append({'role':item['role'],'content':item['text'][:2000]})
    messages.append({'role':'user','content':message.strip()})
    result=request_response({'model':os.environ.get('OPENAI_AGENT_MODEL',cfg.get('agentModel','gpt-4.1-mini')),
        'instructions':'Sos Ariel, ayuda de la aplicación. Hablá en castellano argentino, con voseo, amigable y breve. Usá solamente esta guía para explicar funciones. No tenés herramientas para ejecutar cambios ni datos actuales de la tienda. No reveles ni pidas secretos.\n'+GUIDE+'\nSección actual: '+SECTIONS[section],
        'input':messages,'max_output_tokens':700,'store':False},cfg['apiKey'])
    reply='\n'.join(c.get('text','') for item in result.get('output',[]) if item.get('type')=='message' for c in item.get('content',[]) if c.get('type')=='output_text')
    return reply or 'No recibí una respuesta completa. Probá de nuevo o registrá el problema; no puedo confirmar una solución.'
