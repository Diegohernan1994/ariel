"""Eventos fechados y consultas compartidas entre panel y agente."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

LOCAL = timezone(timedelta(hours=-3))
EVENTS = {"page_view", "product_view", "add_to_cart", "whatsapp_click", "abandoned_cart", "session_end"}


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=15)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL)")
            yield db
    finally:
        db.close()


def record(path, payload):
    payload = dict(payload)
    if payload.get("event") == "whatsapp_order":
        payload["event"] = "whatsapp_click"
    if payload.get("event") not in EVENTS:
        raise ValueError("Evento no reconocido")
    for key in ("durationSec", "cartValue", "clicks"):
        number = payload.get(key, 0)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 1000000000:
            raise ValueError("Valor inválido: " + key)
    for key in ("productId", "campaign", "adKey", "adName", "source", "sessionId", "eventId"):
        if not isinstance(payload.get(key, ""), str) or len(payload.get(key, "")) > 300:
            raise ValueError("Campo inválido: " + key)
    items = payload.get("cartItems", [])
    if not isinstance(items, list) or len(items) > 100 or any(not isinstance(i, dict) for i in items):
        raise ValueError("Carrito inválido")
    now = datetime.now(LOCAL).isoformat()
    with connect(path) as db:
        cursor = db.execute("INSERT OR IGNORE INTO events VALUES (?, ?, ?)",
                            (payload.get("eventId") or uuid.uuid4().hex, now, json.dumps(payload)))
        return {"recorded": cursor.rowcount == 1, "receivedAt": now}


def query(path, products, filters=None):
    filters = filters or {}
    start, end = filters.get("start_date", ""), filters.get("end_date", "")
    for value in (start, end):
        if value:
            datetime.strptime(value, "%Y-%m-%d")
    if start and end and start > end:
        raise ValueError("La fecha inicial debe ser anterior o igual a la final")
    product, campaign = filters.get("product_id", ""), filters.get("campaign", "")
    with connect(path) as db:
        rows = db.execute("SELECT at, payload FROM events ORDER BY at").fetchall()
    events = [(at, json.loads(raw)) for at, raw in rows]
    campaigns = sorted({p.get("campaign") or "Sin campaña identificada" for _, p in events})
    def matches(at, p):
        ids = {p.get("productId", "")} | {i.get("id", "") for i in p.get("cartItems", [])}
        return ((not start or at[:10] >= start) and (not end or at[:10] <= end)
                and (not product or product in ids)
                and (not campaign or campaign == (p.get("campaign") or "Sin campaña identificada")))
    selected = [(at, p) for at, p in events if matches(at, p)]
    def summarize(entries):
        counts = {name: 0 for name in EVENTS}
        sessions, durations = set(), {}
        for at, p in entries:
            counts[p["event"]] += 1
            sid = p.get("sessionId")
            if sid:
                sessions.add(sid)
                if p.get("durationSec", 0) > 0:
                    durations[sid] = max(durations.get(sid, 0), p["durationSec"])
        avg = round(sum(durations.values()) / len(durations)) if durations else 0
        rate = round(100 * len({p["sessionId"] for _, p in entries if p["event"] == "whatsapp_click" and p.get("sessionId")}) / len(sessions), 1) if sessions else None
        return {"pageViews": counts["product_view"] if product else counts["page_view"],
                "uniqueVisitors": len(sessions), "whatsappCheckoutClicks": counts["whatsapp_click"],
                "addToCartClicks": counts["add_to_cart"], "addToCartCount": counts["add_to_cart"],
                "abandonedCartsCount": counts["abandoned_cart"],
                "abandonedCartsValue": sum(p.get("cartValue", 0) for _, p in entries if p["event"] == "abandoned_cart"),
                "avgDurationFormatted": f"{avg // 60}m {avg % 60:02d}s" if durations else "Sin datos",
                "totalClicks": sum(counts[e] for e in ("product_view", "add_to_cart", "whatsapp_click")),
                "avgClicksPerVisitor": None, "conversionRate": rate, "abandonmentRate": None,
                "durationSampleSessions": len(durations), "eventCount": len(entries)}
    result = summarize(selected)
    days = {}
    for at, event in selected:
        days.setdefault(at[:10], []).append((at, event))
    result["dailySeries"] = [{"date": day, **summarize(entries)} for day, entries in sorted(days.items())]

    ads = {}
    for at, p in selected:
        key = p.get("adKey") or "directo"
        ads.setdefault(key, []).append((at, p))
    result["topAds"] = []
    for key, entries in ads.items():
        metrics = summarize(entries)
        p = entries[0][1]
        result["topAds"].append({"id": key, "adName": p.get("adName") or "Sin anuncio identificado",
            "source": p.get("source") or "Desconocido", "views": metrics["pageViews"],
            "whatsappClicks": metrics["whatsappCheckoutClicks"], "abandoned": metrics["abandonedCartsCount"],
            "conversionRate": metrics["conversionRate"], "avgDurationFormatted": metrics["avgDurationFormatted"]})
    result["topProducts"] = []
    for p in products:
        entries = [(at, event) for at, event in selected if event.get("productId") == p["id"]
                   or any(i.get("id") == p["id"] for i in event.get("cartItems", []))]
        if not entries:
            continue
        metrics = summarize(entries)
        result["topProducts"].append({"id": p["id"], "name": p["name"],
            "views": sum(e["event"] == "product_view" and e.get("productId") == p["id"] for _, e in entries),
            "addedToCart": metrics["addToCartCount"], "whatsappClicks": metrics["whatsappCheckoutClicks"],
            "abandoned": metrics["abandonedCartsCount"]})
    result["topProducts"].sort(key=lambda p: p["views"], reverse=True)
    result["topAds"].sort(key=lambda a: a["whatsappClicks"], reverse=True)
    result["recentSessions"] = [{"time": at[:16].replace("T", " "), "type": p["event"],
        "adName": p.get("adName", ""), "source": p.get("source", ""), "topProduct": p.get("productName", ""),
        "durationSec": p.get("durationSec", 0), "cartValue": p.get("cartValue", 0), "clicks": p.get("clicks", 0)}
        for at, p in selected[-12:][::-1]]
    result.update({"filters": filters, "options": {"products": [{"id": p["id"], "name": p["name"]} for p in products], "campaigns": campaigns},
        "coverageStart": events[0][0] if events else None, "filteredEventCount": len(selected),
        "warning": "Solo eventos fechados recibidos desde esta actualización. Historial anterior sin fecha excluido. Zona Córdoba. Sesiones no equivalen a personas. Abandonos son señales provisionales. Clics WA no son ventas.",
        "viewMetric": "Vistas del producto" if product else "Vistas del catálogo"})
    return result
