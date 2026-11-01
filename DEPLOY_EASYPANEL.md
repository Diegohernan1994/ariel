# Ariel: preparación para EasyPanel

Estado al 31/10/2026. Los cambios están en los repositorios locales. Todavía no se publicaron en GitHub ni se creó el servicio en EasyPanel.

## Listo y comprobado

- Acceso de un único dueño, contraseña protegida con PBKDF2 y sesiones de 12 horas. Cerrar sesión revoca el acceso. Cookies HttpOnly y SameSite; Secure en producción.
- Panel, API de administración, fotos subidas, voces y reels requieren sesión. Las acciones requieren además un token de protección y el origen autorizado.
- Configuración inicial disponible solamente en la vista local. El servidor de producción se niega a iniciar sin dueño configurado y sin una dirección HTTPS para el panel.
- Conversaciones, productos, memoria, analíticas, historial de monitoreo y archivos generados se pueden conservar en una carpeta persistente.
- Herramienta de respaldo y restauración. La restauración exige una carpeta vacía y rechaza rutas que intenten salir de ella.
- El respaldo excluye claves de API y sesiones. Conserva los datos del negocio y, si existe al hacerlo, la verificación de la contraseña. Guardarlo en un lugar privado.
- Dockerfile preparado para ejecutar sin privilegios de administrador, con FFmpeg y controles de salud. Telegram desactivado.
- Plantilla inicial sin conversaciones, credenciales ni costos privados. Una instalación sin restaurar datos comienza sin productos cargados.

Pasaron cinco pruebas integradas de acceso, sesión, protección de acciones, límites y restauración; también compiló la tienda Astro. Docker no está instalado en esta PC: la construcción y ejecución del contenedor quedan pendientes de comprobar en el servidor.

## Acceso local

Abrir http://localhost:4567/setup y elegir usuario y contraseña de al menos 12 caracteres. La creación y el envío de las credenciales los hace el dueño directamente en esa pantalla. Después, ingresar al panel. No enviar la contraseña por el chat.

Ya existe un respaldo previo a este paso en `backups/antes-easypanel-20261031.zip`, dentro del repositorio del agente. Se creó antes de configurar el dueño; no incluye sus futuras credenciales. Crear un nuevo respaldo antes de migrar.

## Configuración de producción

Usar el repositorio separado `Diegohernan1994/agente-marcos`, construir con su Dockerfile y dirigir el servicio al puerto interno **4567**. Usar **una sola instancia**: el estado combina JSON y SQLite y no está preparado para múltiples réplicas.

Montar almacenamiento persistente en `/data`. El usuario del contenedor tiene UID y GID **10001**; comprobar que pueda escribir en el volumen. Sin ese montaje se perderían los datos al reemplazar el contenedor.

Configurar las variables del servicio, sin subir secretos a Git:

| Variable | Valor o criterio |
| --- | --- |
| `DATA_DIR` | `/data` |
| `PORT` | `4567` |
| `PUBLIC_PANEL_URL` | Origen HTTPS real del panel, sin rutas ni barra final |
| `ADMIN_USERNAME` | Usuario del dueño |
| `ADMIN_PASSWORD_HASH` | Verificación de la contraseña, nunca la contraseña original |
| `OPENAI_API_KEY` | Clave de OpenAI en las variables privadas del servicio |
| `OPENAI_AGENT_MODEL` | `gpt-4.1-mini`, valor actualmente utilizado |
| `STORE_ALLOWED_ORIGINS` | `https://tienda-marcos.pages.dev`; agregar el dominio propio cuando exista, separado por coma |
| `ENABLE_TELEGRAM` | `0` |
| `TZ` | `America/Argentina/Cordoba` |

No habilitar `ARIEL_LOCAL_SETUP` en producción. Para las credenciales se puede usar el archivo `.owner-auth.json` restaurado en `/data` o las dos variables `ADMIN_*`. Si se definen, las variables tienen prioridad.

Si se elige generar una nueva verificación para las variables, ejecutar desde la carpeta del agente, en una terminal privada:

```sh
python -c "import getpass; from access_control import password_hash; print(password_hash(getpass.getpass('Contraseña: ')))"
```

La contraseña no se muestra al escribir. Copiar únicamente la verificación resultante al campo privado de EasyPanel, conservando literalmente los signos `$`. No compartirla ni publicarla.

## Migración y recuperación

Desde la carpeta del agente:

```sh
python manage.py backup --output backups/migracion.zip
python manage.py restore --input backups/migracion.zip --into RUTA_VACIA
```

Hacer el respaldo definitivo con el servicio detenido para que los archivos multimedia y el estado correspondan al mismo momento. Restaurar en una carpeta vacía, verificarla y usarla como contenido del volumen persistente antes de iniciar producción. No restaurar encima de una instalación activa. Las sesiones no se trasladan: hay que volver a ingresar. La clave OpenAI se configura aparte.

Los respaldos automáticos externos siguen pendientes de configurar en el servidor. Mantener una copia fuera del mismo volumen del panel y probar una recuperación antes de dar la migración por terminada.

## Conexión de la tienda

El repositorio `Diegohernan1994/tienda-marcos` continúa siendo independiente. En su compilación, configurar:

```text
PUBLIC_AGENT_TRACK_URL=https://DOMINIO-REAL-DEL-PANEL/api/track
```

Volver a compilar y publicar la tienda. En desarrollo usa el panel local; en producción, si falta esta variable, no envía eventos al equipo de quien visita la tienda. La URL configurada para producción debe usar HTTPS.

Abrir WhatsApp se sigue registrando como clic, no como venta confirmada.

## Pendientes antes de abrir el acceso definitivo

1. Crear el acceso local y hacer un respaldo actualizado.
2. Revisar y publicar los cambios locales en ambos repositorios.
3. Configurar dominio, HTTPS, volumen y variables en EasyPanel.
4. Construir y probar el contenedor; restaurar los datos y comprobar el reinicio conservando conversaciones y analíticas.
5. Publicar la tienda con la dirección real del seguimiento y verificar un recorrido completo: visita, producto, carrito y clic a WhatsApp.
6. Comprobar el chat, la voz, las descargas privadas, la instalación PWA y el monitoreo horario de 08:00 a 22:00 de Córdoba desde el dominio público.
7. Configurar respaldos externos. Telegram queda para la etapa posterior acordada.

El despliegue tampoco agrega integraciones que todavía no existen: las ventas requieren confirmación real, los resultados de anuncios necesitan datos y los cambios del catálogo del panel aún no se sincronizan automáticamente con la tienda pública.
