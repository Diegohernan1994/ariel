"""Monitor HTTP horario, persistente, sin usar el modelo ni Telegram."""
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from agent_runtime import check_store, CORDOBA_TZ
import json


def next_scan(now):
    for day in range(2):
        date = now + timedelta(days=day)
        for hour in range(8, 23):
            candidate = date.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now:
                return candidate


def classify(result):
    if result.get("status") in (401, 403, 429):
        return "unverified"
    if result.get("reachable") and 200 <= result.get("status", 0) < 300:
        return "online" if result.get("html") and result.get("catalogProductsFound") else "degraded"
    return "possible_down"


class StoreMonitor:
    def __init__(self, path, products, checker=check_store, retry_seconds=10):
        self.path, self.products, self.checker = path, products, checker
        self.retry_seconds = retry_seconds
        self.lock = threading.Lock()
        self.stop = threading.Event()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS checks (id TEXT PRIMARY KEY, started TEXT, finished TEXT, result TEXT)")

    def snapshot(self, now=None):
        now = now or datetime.now(CORDOBA_TZ)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT started, finished, result FROM checks WHERE finished IS NOT NULL ORDER BY finished DESC LIMIT 30").fetchall()
        history = [dict(json.loads(r[2]), startedAt=r[0], checkedAt=r[1]) for r in rows]
        latest = history[0] if history else None
        active = 8 <= now.hour < 22 or (now.hour == 22 and now.minute == 0)
        stale = bool(latest and active and now - datetime.fromisoformat(latest["checkedAt"]) > timedelta(minutes=70))
        return {"latest": latest, "history": history, "running": self.lock.locked(),
                "nextScheduledAt": next_scan(now).isoformat(), "scheduledActive": active,
                "stale": stale, "schedule": "Todos los días, cada hora de 08:00 a 22:00 inclusive. Córdoba, Argentina (UTC−3).",
                "scope": "Comprueba respuesta HTTP y contenido del catálogo. No comprueba pagos ni envío de mensajes. Una falla desde este servidor no prueba caída global."}

    def scan(self, slot=None):
        if not self.lock.acquire(blocking=False):
            return False
        key = slot or "manual-" + uuid.uuid4().hex
        started = datetime.now(CORDOBA_TZ).isoformat()
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                existing = db.execute("SELECT started, finished FROM checks WHERE id=?", (key,)).fetchone()
                if existing:
                    # Un trabajo interrumpido puede recuperarse tras cinco minutos.
                    if existing[1] or datetime.now(CORDOBA_TZ) - datetime.fromisoformat(existing[0]) < timedelta(minutes=5):
                        return False
                    db.execute("DELETE FROM checks WHERE id=?", (key,))
                try:
                    db.execute("INSERT INTO checks VALUES (?, ?, NULL, NULL)", (key, started))
                except sqlite3.IntegrityError:
                    return False
            attempts = []
            for attempt in range(2):
                try:
                    result = self.checker(self.products())
                except Exception as error:
                    result = {"reachable": False, "error": type(error).__name__}
                attempts.append(result)
                if classify(result) == "online":
                    break
                if attempt == 0:
                    self.stop.wait(self.retry_seconds)
            state = classify(attempts[-1])
            report = {"state": state, "attempts": attempts, "trigger": "scheduled" if slot else "manual"}
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("UPDATE checks SET finished=?, result=? WHERE id=?",
                           (datetime.now(CORDOBA_TZ).isoformat(), json.dumps(report), key))
            return True
        finally:
            self.lock.release()

    def tick(self, now=None):
        now = now or datetime.now(CORDOBA_TZ)
        if 8 <= now.hour < 22 or (now.hour == 22 and now.minute == 0):
            slot = now.strftime("scheduled-%Y-%m-%d-%H")
            self.scan(slot)

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception as error:
                    print("Monitor: " + type(error).__name__, flush=True)
                self.stop.wait(15)
        threading.Thread(target=loop, name="store-monitor", daemon=True).start()
