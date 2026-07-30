"""
Eventos corporativos que pueden mover el precio, MÁS ALLÁ de los resultados
trimestrales: keynotes y conferencias de producto (WWDC, GTC, Google I/O…),
lanzamientos, contratos, aprobaciones y el ex-dividendo.

POR QUÉ ESTE MÓDULO ES ASÍ
--------------------------
No existe una API pública fiable de eventos corporativos, así que se combinan
dos vías complementarias:

1. CALENDARIO CURADO (estático, en este mismo fichero). Sin red → es imposible
   que se cuelgue o que una web caída deje la sección vacía. Cubre los eventos
   ANUALES recurrentes de las empresas más negociadas, con mes aproximado; en la
   UI se marcan como "aprox." para no aparentar una precisión que no se tiene.
2. DETECCIÓN EN TITULARES (dinámica). Cubre CUALQUIER acción y capta lo que el
   calendario no puede anticipar: un contrato ganado, una aprobación, un
   lanzamiento anunciado.

BLINDAJE: ninguna función de este módulo lanza NUNCA. Cada fuente va en su
propio try/except y, si falla, simplemente se omite; el agregador devuelve [] en
el peor caso y entonces la aplicación se comporta exactamente como antes.
Tampoco importa nada de red: es Python puro (solo datetime y re).
"""
from __future__ import annotations

import re
from datetime import date, datetime

# ── Tipos de evento (se usan para el icono/color en la UI) ────────────────
TIPO_RESULTADOS = "resultados"
TIPO_PRODUCTO   = "producto"      # keynote, conferencia, lanzamiento
TIPO_DIVIDENDO  = "dividendo"
TIPO_NEGOCIO    = "negocio"       # contrato, adquisición, aprobación
TIPO_CORPORATIVO = "corporativo"  # junta de accionistas, investor day

_VACIOS = {"", "unknown", "n/a", "n/d", "none", "-", "—", "nan", "null"}


def _es_vacio(v) -> bool:
    return v is None or str(v).strip().lower() in _VACIOS


# ══════════════════════════════════════════════════════════════════════════
# 1. CALENDARIO CURADO — eventos ANUALES recurrentes por ticker
#    (mes, día aproximado, nombre, tipo). El día es orientativo: en la UI se
#    muestra como "mes año · aprox.".
# ══════════════════════════════════════════════════════════════════════════
RECURRING_EVENTS = {
    "AAPL": [(6, 9,  "WWDC — conferencia de desarrolladores", TIPO_PRODUCTO),
             (9, 10, "Keynote de septiembre (nuevo iPhone)", TIPO_PRODUCTO)],
    "NVDA": [(3, 18, "GTC — conferencia de IA", TIPO_PRODUCTO),
             (6, 2,  "Computex — keynote", TIPO_PRODUCTO)],
    "GOOGL": [(5, 14, "Google I/O — conferencia de desarrolladores", TIPO_PRODUCTO),
              (10, 8, "Evento Pixel", TIPO_PRODUCTO)],
    "GOOG": [(5, 14, "Google I/O — conferencia de desarrolladores", TIPO_PRODUCTO),
             (10, 8, "Evento Pixel", TIPO_PRODUCTO)],
    "MSFT": [(5, 20, "Microsoft Build — conferencia de desarrolladores", TIPO_PRODUCTO),
             (11, 18, "Microsoft Ignite", TIPO_PRODUCTO)],
    "AMZN": [(12, 1, "AWS re:Invent — conferencia de nube", TIPO_PRODUCTO),
             (7, 15, "Prime Day", TIPO_PRODUCTO)],
    "META": [(9, 25, "Meta Connect — realidad aumentada e IA", TIPO_PRODUCTO)],
    "TSLA": [(1, 2,  "Entregas del trimestre", TIPO_NEGOCIO),
             (4, 2,  "Entregas del trimestre", TIPO_NEGOCIO),
             (7, 2,  "Entregas del trimestre", TIPO_NEGOCIO),
             (10, 2, "Entregas del trimestre", TIPO_NEGOCIO),
             (6, 10, "Junta anual de accionistas", TIPO_CORPORATIVO)],
    "CRM":  [(9, 17, "Dreamforce — conferencia anual", TIPO_PRODUCTO)],
    "ADBE": [(10, 14, "Adobe MAX — conferencia de creatividad", TIPO_PRODUCTO)],
    "ORCL": [(9, 9,  "Oracle CloudWorld", TIPO_PRODUCTO)],
    "AMD":  [(1, 7,  "Keynote en CES", TIPO_PRODUCTO),
             (6, 3,  "Computex — keynote", TIPO_PRODUCTO)],
    "INTC": [(9, 16, "Intel Innovation", TIPO_PRODUCTO)],
    "QCOM": [(10, 21, "Snapdragon Summit", TIPO_PRODUCTO)],
    "AVGO": [(3, 10, "Presentación de infraestructura de IA", TIPO_PRODUCTO)],
    "CSCO": [(6, 3,  "Cisco Live", TIPO_PRODUCTO)],
    "IBM":  [(5, 20, "IBM Think", TIPO_PRODUCTO)],
    "NOW":  [(5, 6,  "ServiceNow Knowledge", TIPO_PRODUCTO)],
    "SNOW": [(6, 2,  "Snowflake Summit", TIPO_PRODUCTO)],
    "PLTR": [(3, 12, "AIPCon — conferencia de IA", TIPO_PRODUCTO)],
    "SHOP": [(6, 18, "Shopify Editions (verano)", TIPO_PRODUCTO)],
    "NFLX": [(9, 15, "Presentación de contenidos de temporada", TIPO_PRODUCTO)],
    "DIS":  [(8, 9,  "D23 — expo de fans y estrenos", TIPO_PRODUCTO)],
    "SONY": [(1, 7,  "Keynote en CES", TIPO_PRODUCTO)],
    "F":    [(1, 7,  "Presentaciones en CES", TIPO_PRODUCTO)],
    "GM":   [(1, 7,  "Presentaciones en CES", TIPO_PRODUCTO)],
    "RIVN": [(1, 7,  "Presentaciones en CES", TIPO_PRODUCTO)],
    "BA":   [(7, 21, "Salón aeronáutico (pedidos)", TIPO_NEGOCIO)],
    "LMT":  [(7, 21, "Salón aeronáutico (contratos de defensa)", TIPO_NEGOCIO)],
    "RTX":  [(7, 21, "Salón aeronáutico (contratos de defensa)", TIPO_NEGOCIO)],
    "JNJ":  [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
    "PFE":  [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
    "MRK":  [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
    "LLY":  [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
    "ABBV": [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
    "AMGN": [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
    "MRNA": [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
    "COIN": [(5, 29, "Conferencia State of Crypto", TIPO_PRODUCTO)],
    "UBER": [(9, 10, "Uber Go-Get (producto)", TIPO_PRODUCTO)],
    "ABNB": [(5, 13, "Airbnb Summer Release", TIPO_PRODUCTO)],
    "SBUX": [(3, 12, "Junta anual de accionistas", TIPO_CORPORATIVO)],
    "WMT":  [(6, 5,  "Junta anual de accionistas", TIPO_CORPORATIVO)],
    "BRK-B": [(5, 3, "Junta anual de accionistas", TIPO_CORPORATIVO)],
    "JPM":  [(5, 20, "Investor Day", TIPO_CORPORATIVO)],
    "GS":   [(1, 13, "Conferencia financiera de inicio de año", TIPO_CORPORATIVO)],
}

# Eventos por SECTOR — red de cobertura para tickers no curados.
_SECTOR_EVENTS = {
    "technology":             [(1, 7, "CES — feria de tecnología", TIPO_PRODUCTO)],
    "consumer cyclical":      [(1, 7, "CES — feria de tecnología", TIPO_PRODUCTO)],
    "communication services": [(1, 7, "CES — feria de tecnología", TIPO_PRODUCTO)],
    "healthcare":             [(1, 13, "Conferencia sanitaria de JPMorgan", TIPO_NEGOCIO)],
}

_MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fmt_fecha(d: date, aprox: bool = False) -> str:
    """'2026-09-10' → '10 sep 2026' (o 'sep 2026' si es aproximada)."""
    corto = _MESES_ES[d.month][:3]
    return f"{corto} {d.year}" if aprox else f"{d.day} {corto} {d.year}"


def next_occurrence(month: int, day: int, hoy: date | None = None) -> date | None:
    """Próxima fecha futura de un patrón anual (si ya pasó este año → el que
    viene). None si los parámetros no son válidos. NUNCA lanza."""
    try:
        hoy = hoy or date.today()
        month, day = int(month), int(day)
        for año in (hoy.year, hoy.year + 1):
            try:
                cand = date(año, month, day)
            except ValueError:          # 30 de febrero y demás
                cand = date(año, month, 28)
            if cand >= hoy:
                return cand
    except Exception:
        pass
    return None


def _parse_fecha(v) -> date | None:
    """Acepta date/datetime/'YYYY-MM-DD'/'MM/DD/YYYY'/ISO. None si no se puede."""
    if v is None:
        return None
    try:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        s = str(v).strip()
        if _es_vacio(s):
            return None
        s = s.replace("Z", "").split("T")[0].split(" ")[0]
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════
# 2. DETECCIÓN DE EVENTOS EN TITULARES — cobertura universal
# ══════════════════════════════════════════════════════════════════════════
# Patrones de evento FUTURO o recién anunciado. Se guardan en minúscula.
_PATRONES = [
    # (regex, tipo, etiqueta corta en español)
    (r"\b(wwdc|keynote|developer conference|i/o\b|re:invent|dreamforce|gtc\b|"
     r"computex|ignite|build conference|summit|expo)\b", TIPO_PRODUCTO, "Conferencia o keynote"),
    (r"\b(unveil|unveils|will launch|to launch|launches|launching|debut|debuts|"
     r"introduc(?:e|es|ing)|reveal|reveals|announce[sd]? (?:new|next))\b",
     TIPO_PRODUCTO, "Lanzamiento de producto"),
    (r"\b(wins? (?:a )?contract|awarded (?:a )?contract|contract award|"
     r"secures? (?:a )?deal|signs? (?:a )?deal|partnership with|new order)\b",
     TIPO_NEGOCIO, "Contrato o acuerdo"),
    (r"\b(fda approval|approved by the fda|receives approval|regulatory approval|"
     r"clearance)\b", TIPO_NEGOCIO, "Aprobación regulatoria"),
    (r"\b(investor day|analyst day|shareholder meeting|annual meeting|"
     r"capital markets day)\b", TIPO_CORPORATIVO, "Evento para inversores"),
    (r"\b(acquisition|acquires|to acquire|merger|buyout|takeover)\b",
     TIPO_NEGOCIO, "Operación corporativa"),
    (r"\b(guidance|outlook|forecast) (?:raise|raised|cut|update)",
     TIPO_NEGOCIO, "Revisión de previsiones"),
]

# Señales de que el titular habla de algo FUTURO (sube la relevancia).
_FUTURO = re.compile(
    r"\b(will|to |upcoming|next month|next week|scheduled|set to|plans? to|"
    r"expected|ahead of|previa|próxim)", re.I)


def _claves_empresa(ticker, nombre) -> list[str]:
    """Palabras que identifican a la compañía en un titular: el ticker y las
    primeras palabras significativas de su nombre ('Coca-Cola Company' → 'coca')."""
    claves = []
    try:
        tk = str(ticker or "").strip().lower()
        if len(tk) >= 2:
            claves.append(tk.split("-")[0])
        nom = str(nombre or "").lower()
        for basura in (" inc", " corp", " corporation", " company", " co.", " plc",
                       " ltd", " holdings", " group", ",", "."):
            nom = nom.replace(basura, " ")
        for palabra in nom.split():
            if len(palabra) >= 4:
                claves.append(palabra)
    except Exception:
        pass
    return claves[:4]


def detect_event_signals(news, max_items: int = 4, ticker=None, nombre=None) -> list[dict]:
    """Extrae eventos de una lista de noticias (la que devuelve get_news).

    Si se pasan `ticker`/`nombre`, solo se aceptan titulares que mencionen a la
    compañía — el respaldo de noticias de Nasdaq mezcla titulares del sector y
    un evento del competidor no es un catalizador de esta acción.

    Devuelve [{'titulo','tipo','etiqueta','futuro','fecha_txt'}] — sin fecha
    exacta, porque el titular rara vez la trae. NUNCA lanza: ante cualquier
    problema devuelve []."""
    out = []
    claves = _claves_empresa(ticker, nombre)
    try:
        for item in (news or [])[:20]:
            try:
                titulo = str((item or {}).get("title") or "").strip()
                if not titulo:
                    continue
                bajo = titulo.lower()
                if claves and not any(c in bajo for c in claves):
                    continue
                for rx, tipo, etiqueta in _PATRONES:
                    if re.search(rx, bajo):
                        out.append({
                            "titulo": titulo,
                            "tipo": tipo,
                            "etiqueta": etiqueta,
                            "futuro": bool(_FUTURO.search(titulo)),
                            "fecha_txt": str((item or {}).get("date") or "")[:10],
                        })
                        break
            except Exception:
                continue
        # Primero lo que apunta al futuro; sin duplicar el mismo titular
        vistos, unicos = set(), []
        for e in sorted(out, key=lambda x: not x["futuro"]):
            clave = e["titulo"][:60].lower()
            if clave in vistos:
                continue
            vistos.add(clave)
            unicos.append(e)
        return unicos[:max_items]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════
# 3. AGREGADOR — une todas las fuentes en una sola agenda ordenada
# ══════════════════════════════════════════════════════════════════════════
def get_upcoming_catalysts(ticker, info=None, earnings=None, news=None,
                           horizonte_dias: int = 400) -> list[dict]:
    """Agenda de próximos catalizadores, ordenada por fecha.

    Cada evento: {fecha (date|None), fecha_txt, dias (int|None), tipo, titulo,
                  fuente, confirmado (bool)}.
    - confirmado=True  → fecha real (resultados, ex-dividendo).
    - confirmado=False → fecha aproximada del calendario curado, o señal de
      titular sin fecha.

    BLINDAJE: cada fuente en su propio try/except. NUNCA lanza; devuelve [] si
    todo falla, y entonces el resto de la app funciona como antes."""
    eventos: list[dict] = []
    hoy = date.today()
    info = info or {}
    earnings = earnings or {}

    # ── (a) Resultados trimestrales — fecha real ─────────────────────────
    try:
        f = _parse_fecha(earnings.get("next_earnings"))
        if f and f >= hoy:
            eventos.append({
                "fecha": f, "fecha_txt": _fmt_fecha(f), "dias": (f - hoy).days,
                "tipo": TIPO_RESULTADOS, "titulo": "Reporte de resultados",
                "fuente": "calendario de resultados", "confirmado": True,
            })
    except Exception:
        pass

    # ── (b) Ex-dividendo — fecha real (info tiene respaldo TradingView) ──
    try:
        for clave in ("ex_dividend_date", "exDividendDate", "ex_dividend"):
            f = _parse_fecha(info.get(clave))
            if f and f >= hoy:
                eventos.append({
                    "fecha": f, "fecha_txt": _fmt_fecha(f), "dias": (f - hoy).days,
                    "tipo": TIPO_DIVIDENDO, "titulo": "Fecha ex-dividendo",
                    "fuente": "datos de la compañía", "confirmado": True,
                })
                break
    except Exception:
        pass

    # ── (c) Calendario curado — sin red, nunca falla ─────────────────────
    try:
        tk = str(ticker or "").upper().strip()
        patrones = list(RECURRING_EVENTS.get(tk, []))
        if not patrones:
            sector = str(info.get("sector") or "").strip().lower()
            patrones = list(_SECTOR_EVENTS.get(sector, []))
        for mes, dia, nombre, tipo in patrones:
            f = next_occurrence(mes, dia, hoy)
            if not f:
                continue
            dias = (f - hoy).days
            if dias > horizonte_dias:
                continue
            eventos.append({
                "fecha": f, "fecha_txt": _fmt_fecha(f, aprox=True), "dias": dias,
                "tipo": tipo, "titulo": nombre,
                "fuente": "calendario anual", "confirmado": False,
            })
    except Exception:
        pass

    # ── (d) Señales detectadas en titulares — cobertura universal ────────
    try:
        _nombre = info.get("company_name") or info.get("longName") or info.get("name")
        for s in detect_event_signals(news, ticker=ticker, nombre=_nombre):
            eventos.append({
                "fecha": None, "fecha_txt": "en titulares recientes", "dias": None,
                "tipo": s["tipo"], "titulo": s["titulo"],
                "fuente": "noticias", "confirmado": False,
                "etiqueta": s.get("etiqueta", ""),
            })
    except Exception:
        pass

    # Orden: primero lo datado (por cercanía), después las señales sin fecha.
    try:
        eventos.sort(key=lambda e: (e["dias"] is None, e["dias"] if e["dias"] is not None else 0))
    except Exception:
        pass
    return eventos


def resumen_eventos(eventos, dias: int = 90) -> dict:
    """Métricas de la agenda para el scoring y la prosa. NUNCA lanza."""
    try:
        eventos = eventos or []
        datados = [e for e in eventos if e.get("dias") is not None]
        proximos = [e for e in datados if e["dias"] <= dias]
        no_resultados = [e for e in proximos if e.get("tipo") != TIPO_RESULTADOS]
        senales = [e for e in eventos if e.get("dias") is None]
        return {
            "total": len(eventos),
            "proximos_90d": len(proximos),
            "otros_90d": len(no_resultados),      # catalizadores que NO son earnings
            "senales_noticias": len(senales),
            "siguiente": datados[0] if datados else (eventos[0] if eventos else None),
            "siguiente_no_resultados": no_resultados[0] if no_resultados else None,
        }
    except Exception:
        return {"total": 0, "proximos_90d": 0, "otros_90d": 0,
                "senales_noticias": 0, "siguiente": None,
                "siguiente_no_resultados": None}
