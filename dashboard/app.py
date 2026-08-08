"""
DLP Market Analyzer — Bloomberg-style dashboard para el sistema de análisis de mercados.
Punto de entrada principal: streamlit run dashboard/app.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cargar .env ANTES de cualquier otro import — garantiza ANTHROPIC_API_KEY del .env real
from dotenv import load_dotenv
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

import json
import time
from datetime import datetime
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

# Tomar la key DIRECTAMENTE de la variable de entorno ya cargada
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
from agents.orchestrator import Orchestrator, StockAnalysis
from agents.screener import ScreenerAgent, ScreenerResult
from dashboard.styles import (
    BLOOMBERG_CSS, get_recommendation_badge, score_color,
    score_css_class, AGENT_ICONS, AGENT_ICON_SLUG,
)


def _agent_icon_html(agent_name):
    """Chip del ícono de sección: SVG personalizado (clase .agent-icon--<slug>)
    si el agente tiene slug; si no, cae al monograma (FN/TC/…) como antes."""
    slug = AGENT_ICON_SLUG.get(agent_name)
    if slug:
        return f'<span class="agent-icon agent-icon--{slug}"></span>'
    mono = AGENT_ICONS.get(agent_name) or (str(agent_name)[:2].upper() or "··")
    return f'<span class="agent-icon">{mono}</span>'
# charts se importa lazy para no cargar plotly al arrancar (ahorra ~80MB RAM)
def _charts():
    from dashboard import charts as _c
    return _c

def build_price_chart(*a, **k):        return _charts().build_price_chart(*a, **k)
def build_mountain_chart(*a, **k):     return _charts().build_mountain_chart(*a, **k)
def build_gauge(*a, **k):              return _charts().build_gauge(*a, **k)
def build_snowflake(*a, **k):          return _charts().build_snowflake(*a, **k)
def build_score_breakdown(*a, **k):    return _charts().build_score_breakdown(*a, **k)
def build_mini_gauge(*a, **k):         return _charts().build_mini_gauge(*a, **k)
def build_rr_chart(*a, **k):           return _charts().build_rr_chart(*a, **k)
def build_sector_heatmap(*a, **k):     return _charts().build_sector_heatmap(*a, **k)
def build_sector_rotation(*a, **k):    return _charts().build_sector_rotation(*a, **k)
def build_compact_gauge(*a, **k):      return _charts().build_compact_gauge(*a, **k)
def build_rsi_gauge(*a, **k):          return _charts().build_rsi_gauge(*a, **k)
def build_metric_bars(*a, **k):        return _charts().build_metric_bars(*a, **k)
def build_earnings_history_chart(*a, **k): return _charts().build_earnings_history_chart(*a, **k)
def build_sentiment_gauge(*a, **k):    return _charts().build_sentiment_gauge(*a, **k)
def build_fear_greed_gauge(*a, **k):   return _charts().build_fear_greed_gauge(*a, **k)
def build_holders_bars(*a, **k):       return _charts().build_holders_bars(*a, **k)


def _plotly(fig, *, config=None, **kwargs):
    """Renderiza una figura Plotly BLOQUEADA en TODA la app: sin zoom, sin
    arrastre/pan, sin doble-clic para hacer zoom y sin barra de herramientas.
    Conserva el hover (los tooltips siguen funcionando) y NO cambia el aspecto
    de la figura — solo desactiva la interacción.

    El bloqueo se aplica a dos niveles para que sea infalible en CUALQUIER tipo
    de gráfica (barras, líneas, velas, subplots, radar polar, gauge/indicador):
      · fixedrange=True en los ejes X/Y  → bloqueo definitivo de zoom/pan en las
        cartesianas (no-op inofensivo en gauge/radar, que no tienen ejes X/Y).
      · dragmode=False                   → bloquea arrastre/rotación (incl. radar).
      · config: sin modebar, sin scrollZoom, sin doubleClick.
    Reemplaza a st.plotly_chart en todos los puntos de render. NUNCA lanza."""
    try:
        fig.update_layout(dragmode=False)
        fig.update_xaxes(fixedrange=True)
        fig.update_yaxes(fixedrange=True)
    except Exception:
        pass
    cfg = dict(config or {})
    cfg["displayModeBar"] = False
    cfg["scrollZoom"] = False
    cfg["doubleClick"] = False
    # Cada gráfica va dentro de una TARJETA: st.container(border=True) crea un
    # wrapper [data-testid="stVerticalBlockBorderWrapper"] al que el CSS le da
    # fondo/borde/sombra. La tarjeta va sobre el WRAPPER, nunca sobre el propio
    # stPlotlyChart (poner padding ahí rompía la medición de ancho de Plotly y
    # las gráficas se desbordaban — ver styles.py, sección de gráficas).
    with st.container(border=True):
        st.plotly_chart(fig, config=cfg, **kwargs)


# ── Config de página ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="DLP Market Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(BLOOMBERG_CSS, unsafe_allow_html=True)


# ── State inicial ─────────────────────────────────────────────────────────

# Máximo de análisis mantenidos EN MEMORIA (RAM) a la vez. Acota el uso de
# memoria sin importar cuántos análisis se hayan acumulado.
# Debe coincidir con MAX_ANALYSES_ON_DISK (persistence) para que la barra
# lateral y el almacenamiento muestren lo mismo: los 4 más recientes.
# (4 y no 5: el contenedor de Render se quedaba sin memoria.)
MAX_HISTORY_IN_MEMORY = 4


def _prune_analyses_in_memory():
    """Mantiene en session_state.analyses solo los MAX_HISTORY_IN_MEMORY más
    recientes (por timestamp). NO borra nada del disco — solo libera RAM."""
    analyses = st.session_state.get("analyses") or {}
    if len(analyses) <= MAX_HISTORY_IN_MEMORY:
        return
    keep = sorted(analyses.values(),
                  key=lambda a: getattr(a, "timestamp", "") or "",
                  reverse=True)[:MAX_HISTORY_IN_MEMORY]
    keep_tickers = {a.ticker for a in keep}
    for t in list(analyses.keys()):
        if t not in keep_tickers:
            del st.session_state.analyses[t]


def init_state():
    from config.settings import SCANNER_DEFAULTS
    # Bump esta versión cuando cambies SCANNER_DEFAULTS, así fuerza el reset
    # del session_state de usuarios con filtros viejos en caché.
    SCANNER_DEFAULTS_VERSION = "v3-2026-06-05"

    defaults = {
        "analyses":            {},     # ticker → StockAnalysis (full)
        "selected_ticker":     None,
        "quick_view_ticker":   None,   # ticker en vista rápida (sin AI)
        "analyzing":           False,
        "scan_results":        [],
        "current_scan_id":     None,   # scan_id actualmente cargado
        "scan_running":        False,
        "client":              None,
        "agent_log":           [],
        # Scanner personalizable
        "scanner_config_open": False,                # mostrar página de configuración
        "scanner_filters":     dict(SCANNER_DEFAULTS),  # selección UI actual
        "sidebar_collapsed":   False,                # columna lateral minimizada
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

    # Si el usuario tiene una versión vieja de filtros en session_state, la
    # forzamos a actualizar a los nuevos defaults. Sin esto, los miembros que
    # ya entraron antes siguen con `rs_strength='fuerte'` y otros viejos
    # restrictivos en caché.
    if st.session_state.get("_scanner_defaults_version") != SCANNER_DEFAULTS_VERSION:
        st.session_state.scanner_filters = dict(SCANNER_DEFAULTS)
        st.session_state._scanner_defaults_version = SCANNER_DEFAULTS_VERSION

    # Helper para validar análisis (tesis real, no fallback)
    def _is_valid_analysis(a):
        return len(getattr(a, "investment_thesis", "") or "") > 200

    # ── Limpiar análisis corruptos de la session_state (en CADA rerun) ──
    bad_tickers = [t for t, a in st.session_state.analyses.items() if not _is_valid_analysis(a)]
    for t in bad_tickers:
        del st.session_state.analyses[t]

    # ── Cargar historial desde disco local (solo los N más recientes para
    #    acotar la memoria; los más antiguos quedan en disco, sin cargarse) ──
    if not st.session_state.get("_history_loaded"):
        try:
            from data.persistence import load_all_analyses as disk_load
            disk_saved = disk_load()
            valid = [a for a in disk_saved.values() if _is_valid_analysis(a)]
            valid.sort(key=lambda a: getattr(a, "timestamp", "") or "", reverse=True)
            for analysis in valid[:MAX_HISTORY_IN_MEMORY]:
                if analysis.ticker not in st.session_state.analyses:
                    st.session_state.analyses[analysis.ticker] = analysis
        except Exception:
            pass
        st.session_state._history_loaded = True


init_state()




# ── Protección anti-extracción (cosmética — deterrent contra curiosos) ──
def inject_protection():
    """Inyecta JS que bloquea click derecho, atajos de DevTools, view-source y
    save-page sobre el DOM REAL de la app.

    IMPORTANTE: `st.markdown(unsafe_allow_html=True)` permite HTML pero bloquea
    la ejecución de `<script>` por seguridad. Por eso usamos
    `components.html()`, que ejecuta JS dentro de un iframe sandbox. Desde el
    iframe accedemos a `window.parent.document` (el documento real del app
    Streamlit) y registramos listeners en ÉL — no en el iframe del componente.

    Es una capa DISUASIVA contra usuarios casuales. Un usuario técnico puede
    abrir DevTools desde el menú del navegador. Para bloqueo real, usar
    verificación de Referer en el servidor al desplegar."""
    components.html("""
    <script>
    (function() {
        // Acceder al DOM real del app Streamlit, no al del componente.
        const doc = (window.parent && window.parent.document) || document;

        // ── Bloqueo del menú contextual / atajos / drag ───────────────────
        // Handlers reutilizables para poder armarlos en CUALQUIER documento
        // accesible (el del app, el top, el propio componente y cualquier
        // iframe del mismo origen). Así el click derecho queda bloqueado en
        // TODA zona de la app, no solo en el documento principal.
        function _block(e) {
            e.preventDefault();
            e.stopPropagation();
            return false;
        }
        function _blockKeys(e) {
            const k = (e.key || '').toLowerCase();
            const blocked =
                e.key === 'F12' ||
                ((e.ctrlKey || e.metaKey) && e.shiftKey && (k === 'i' || k === 'j' || k === 'c')) ||
                (e.metaKey && e.altKey && (k === 'i' || k === 'j' || k === 'c')) ||
                ((e.ctrlKey || e.metaKey) && (k === 'u' || k === 's'));
            if (blocked) {
                e.preventDefault();
                e.stopPropagation();
                return false;
            }
        }
        // Idempotente POR documento: arma los listeners una sola vez en cada
        // uno (evita apilar listeners en cada rerun de Streamlit).
        function armDoc(d) {
            if (!d || d.__dlp_armed) return;
            try {
                d.__dlp_armed = true;
                d.addEventListener('contextmenu', _block, true);  // click derecho
                d.addEventListener('keydown', _blockKeys, true);  // F12 / DevTools / ver-fuente / guardar
                d.addEventListener('dragstart', _block, true);    // arrastrar links / imágenes
            } catch (e) {}
        }
        // Arma también todos los iframes del mismo origen (componentes, etc.).
        function armIframes(root) {
            if (!root) return;
            try {
                var frames = root.querySelectorAll('iframe');
                for (var i = 0; i < frames.length; i++) {
                    try { armDoc(frames[i].contentDocument); } catch (e) {}
                }
            } catch (e) {}
        }
        // Arma todos los documentos accesibles (app + top + componente + iframes).
        function armEverywhere() {
            armDoc(doc);
            armDoc(document);
            try { armDoc(window.top && window.top.document); } catch (e) {}
            armIframes(doc);
            try { if (window.top && window.top.document) armIframes(window.top.document); } catch (e) {}
        }
        armEverywhere();

        // 5. Eliminar branding de Streamlit Cloud — selectores agresivos.
        const HIDE_SELECTORS = [
            '[class*="viewerBadge"]', '[class*="ViewerBadge"]',
            '[class*="appViewerBadge"]', '[class*="stAppViewerBadge"]',
            '[data-testid*="viewerBadge"]', '[data-testid="stAppViewerBadge"]',
            '[data-testid="stToolbar"]', '[data-testid="stToolbarActions"]',
            '[data-testid="stStatusWidget"]', '[data-testid="stDecoration"]',
            '[data-testid="stHeader"]', '[data-testid="stAppDeployButton"]',
            '[data-testid="stDeployButton"]', 'header[data-testid="stHeader"]',
            'button[title="View fullscreen"]', 'button[title*="ullscreen"]',
            'button[aria-label*="ullscreen"]',
            '#MainMenu', '.stDeployButton', '.stAppDeployButton',
            'a[href*="streamlit.io"]', 'a[href*="share.streamlit.io"]',
            'footer.streamlit-footer', '.stApp > footer', '.stAppFooter',
        ];

        // Búsqueda por TEXTO — el método más robusto porque NO depende de
        // class names que Streamlit puede cambiar. Si encontramos un elemento
        // con texto "Built with Streamlit" o "Fullscreen", lo borramos junto
        // con sus 3 contenedores padres más cercanos.
        function removeByText(root) {
            try {
                var nodes = root.querySelectorAll('a, button, div, span, p, footer');
                var patterns = ['built with streamlit', 'made with streamlit', 'fullscreen'];
                for (var i = 0; i < nodes.length; i++) {
                    var el = nodes[i];
                    var txt = ((el.textContent || '') + '').trim().toLowerCase();
                    if (!txt || txt.length > 100) continue;  // skip vacío o muy largo
                    for (var p = 0; p < patterns.length; p++) {
                        if (txt === patterns[p] ||
                            (txt.length < 50 && txt.indexOf(patterns[p]) !== -1)) {
                            var target = el;
                            for (var k = 0; k < 3 && target.parentElement &&
                                 target.parentElement.tagName !== 'BODY' &&
                                 target.parentElement.tagName !== 'HTML'; k++) {
                                target = target.parentElement;
                            }
                            try { target.remove(); } catch (e) {}
                            break;
                        }
                    }
                }
            } catch (e) {}
        }

        function nukeBranding(root) {
            if (!root) return;
            // Por selectores conocidos
            try {
                HIDE_SELECTORS.forEach(function(sel) {
                    var nodes = root.querySelectorAll(sel);
                    for (var i = 0; i < nodes.length; i++) {
                        try {
                            nodes[i].style.display = 'none';
                            nodes[i].remove();
                        } catch (e) {}
                    }
                });
            } catch (e) {}
            // Por texto (catch-all)
            removeByText(root);
        }

        // Nukear en todos los documentos accesibles: el propio y window.top
        function nukeEverywhere() {
            nukeBranding(doc);
            try { if (window.top && window.top.document) nukeBranding(window.top.document); } catch (e) {}
            try { if (window.parent && window.parent.document) nukeBranding(window.parent.document); } catch (e) {}
        }

        // Oculta el "resize handle" del borde del sidebar (parecía arrastrable
        // pero el ancho está fijado). Robusto: busca cualquier elemento dentro
        // del sidebar cuyo cursor calculado sea de redimensionar y lo oculta.
        function hideSidebarResizer() {
            try {
                var sb = doc.querySelector('[data-testid="stSidebar"]');
                if (!sb) return;
                var view = doc.defaultView || window;
                var els = sb.querySelectorAll('div');
                for (var i = 0; i < els.length; i++) {
                    var cur = '';
                    try { cur = view.getComputedStyle(els[i]).cursor; } catch (e) {}
                    if (cur === 'col-resize' || cur === 'ew-resize') {
                        els[i].style.display = 'none';
                    }
                }
            } catch (e) {}
        }

        // ── Auto-fit del VALOR de las tarjetas a UNA sola línea ──────────────
        // Los valores (.status-pill-value / .kpi-tile-value) van con white-space:
        // nowrap; si el texto no cabe, aquí se ENCOGE la fuente hasta que quepa
        // completo en una línea (nunca se apila ni se corta con "…"). Mide el
        // ancho REAL, así funciona igual en desktop y en el iframe estrecho.
        function fitText(d) {
            if (!d) return;
            try {
                var els = d.querySelectorAll('.status-pill-value, .kpi-tile-value');
                var view = d.defaultView || window;
                for (var i = 0; i < els.length; i++) {
                    var el = els[i];
                    // Saltar los ocultos (pestañas inactivas): clientWidth 0 daría
                    // un encogido erróneo. Se ajustan cuando su pestaña se muestre.
                    if (!el.clientWidth || el.offsetParent === null) continue;
                    var txt = el.textContent || '';
                    var w = String(el.clientWidth);
                    // Re-ajustar SOLO cuando cambia el TEXTO o el ANCHO (resize /
                    // iframe más estrecho) → sin bucles ni parpadeo, pero re-encaja
                    // si el contenedor cambia de tamaño.
                    if (el.getAttribute('data-fit') === txt &&
                        el.getAttribute('data-fitw') === w) continue;
                    el.style.fontSize = '';   // vuelve a la fuente base del CSS
                    var size = parseFloat(view.getComputedStyle(el).fontSize) || 16;
                    var guard = 0;
                    while (el.scrollWidth > el.clientWidth + 1 && size > 9 && guard < 60) {
                        size -= 0.5;
                        el.style.fontSize = size + 'px';
                        guard++;
                    }
                    el.setAttribute('data-fit', txt);
                    el.setAttribute('data-fitw', w);
                }
            } catch (e) {}
        }

        // Repasa branding + re-arma el bloqueo de click derecho (por si aparece
        // un iframe/nodo nuevo tras un rerun de Streamlit) + oculta el resizer +
        // ajusta el tamaño del texto de las tarjetas.
        function sweep() { nukeEverywhere(); armEverywhere(); hideSidebarResizer(); fitText(doc); }

        sweep();
        try {
            var observer = new MutationObserver(sweep);
            observer.observe(doc.body || doc.documentElement, {
                childList: true, subtree: true, attributes: false
            });
        } catch (e) {}
        // Limpieza periódica MUY frecuente (cada 250ms) — garantiza que aunque
        // Streamlit reinyecte el badge tras un rerun, lo borramos en <500ms, y
        // que cualquier zona/iframe nuevo quede con el click derecho bloqueado.
        setInterval(sweep, 250);
    })();
    </script>
    """, height=0, width=0)


inject_protection()


# ── Anthropic Client ──────────────────────────────────────────────────────
def get_client():
    # ── VERSIÓN SIN IA (copia para clientes) ──────────────────────────────
    # Esta versión NO usa la API de Anthropic: todas las calificaciones y el
    # análisis se calculan por código (agents/code_engine.py). Por eso NO se
    # necesita API key ni cliente, y nunca se gastan créditos. Devolvemos None;
    # el Orchestrator funciona perfectamente con client=None.
    return None


# ── Header ────────────────────────────────────────────────────────────────
def render_header():
    # Solo el chip de fecha/hora, alineado a la derecha y pegado al contenido.
    # La marca pequeña de arriba a la izquierda se retiró: era redundante con
    # el título grande del home (y con la marca del sidebar) y, junto con su
    # borde y márgenes, creaba una franja de espacio muerto en TODAS las vistas.
    st.markdown(f"""
    <div class="terminal-topbar">
        <span class="terminal-topbar-time">{datetime.now().strftime("%Y-%m-%d · %H:%M")}</span>
    </div>
    """, unsafe_allow_html=True)


# ── Sidebar: Brand + Home + Historial (análisis y escaneos) ──────────────
_REC_TO_SLUG = {
    "MUY ATRACTIVO":  "strong_buy",
    "ATRACTIVO":      "buy",
    "EN OBSERVACIÓN": "watch",
    "POCO ATRACTIVO": "pass",
    # Backward compat: análisis guardados antes del renombrado
    "EVITAR":         "pass",
    "STRONG BUY":     "strong_buy",
    "BUY":            "buy",
    "WATCH":          "watch",
    "PASS":           "pass",
}


def _sb_go_home():
    st.session_state.selected_ticker = None
    st.session_state.quick_view_ticker = None
    st.session_state.scan_results = []
    st.session_state.current_scan_id = None
    st.session_state._show_scan_results = False
    st.session_state.scanner_config_open = False


def _sb_load_analysis(ticker: str):
    """Carga un análisis ya cacheado/persistido y limpia otros modos."""
    st.session_state.selected_ticker = ticker
    st.session_state.quick_view_ticker = None
    st.session_state.scan_results = []
    st.session_state.current_scan_id = None
    st.session_state._show_scan_results = False
    st.session_state.scanner_config_open = False


def _sb_load_scan(scan_id: str):
    """Carga un scan guardado desde disco y lo muestra en pantalla."""
    try:
        from data.persistence import load_scan_by_id
        results = load_scan_by_id(scan_id)
    except Exception:
        results = []
    st.session_state.scan_results = results
    st.session_state.current_scan_id = scan_id
    st.session_state._show_scan_results = True
    st.session_state._radar_scroll_top = True
    st.session_state.selected_ticker = None
    st.session_state.quick_view_ticker = None
    st.session_state.scanner_config_open = False
    # Limpiar diagnóstico del scan en vivo — ya no aplica
    st.session_state._scan_diagnostics = {}


@st.cache_data(show_spinner=False)
def _logo_data_uri():
    """Logo del sidebar como data-URI base64 — embebido, sin depender de rutas al
    desplegar. '' si no se encuentra el asset (entonces se usa el logo de texto)."""
    import base64
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo_dlp.png")
        with open(p, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return ""


def render_sidebar():
    with st.sidebar:
        # ── Brand — logo del club DLP (PNG). Fallback al logo de texto si el
        #    asset no está disponible. ─────────────────────────────────────
        _logo = _logo_data_uri()
        if _logo:
            st.markdown(
                f'<div class="sidebar-brand">'
                f'<img class="sidebar-brand-img" src="{_logo}" alt="DLP Club" />'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown("""
            <div class="sidebar-brand">
                <div class="sidebar-brand-logo">◈ DLP</div>
                <div class="sidebar-brand-sub">MARKET ANALYZER</div>
            </div>
            """, unsafe_allow_html=True)

        # ── Botón minimizar columna — el CSS lo posiciona (absoluto) sobre
        #    la misma línea del logo, arriba a la derecha. ─────────────────
        if st.button("«", key="sidebar_collapse_btn"):
            st.session_state.sidebar_collapsed = True
            st.rerun()

        # ── Home ─────────────────────────────────────────────────────────
        if st.button("⌂  Volver al Inicio", use_container_width=True,
                     key="sidebar_home"):
            _sb_go_home()
            st.rerun()

        # ── Historial: Análisis de Acciones (PRIMERO) ───────────────────
        st.markdown('<div class="sb-section-title">◈  Análisis Recientes</div>',
                    unsafe_allow_html=True)

        analyses = st.session_state.get("analyses", {}) or {}
        # Ordenar por timestamp descendente (más reciente arriba)
        analyses_sorted = sorted(
            analyses.values(),
            key=lambda a: getattr(a, "timestamp", "") or "",
            reverse=True,
        )

        if not analyses_sorted:
            st.markdown(
                '<div class="sb-empty">Sin análisis guardados todavía</div>',
                unsafe_allow_html=True,
            )
        else:
            for analysis in analyses_sorted:
                ticker = analysis.ticker
                rec = analysis.recommendation or "EN OBSERVACIÓN"
                rec_slug = _REC_TO_SLUG.get(rec, "watch")
                # Key → clase CSS: solo caracteres seguros (BRK.B → BRK_B)
                tk_safe = "".join(c if (c.isalnum() or c in "_-") else "_"
                                  for c in ticker)
                score = float(getattr(analysis, "composite_score", 0) or 0)
                color = score_color(score)
                badge_html = get_recommendation_badge(rec)

                # Tarjeta clicable: el container keyed recibe la clase
                # st-key-sbcard_… en su propio stVerticalBlock (mismo patrón
                # que sectbar_) y el CSS lo pinta como tarjeta. El sufijo
                # __rk_ codifica el rating para el acento izquierdo SIN
                # reutilizar __rec_ (así el CSS legacy no matchea jamás).
                # Termómetro (mismo .meter/.meter-dot de los KPI tiles) con el
                # dot en la posición del DLP Score y SU MISMO color, para que
                # número y dot cuenten la misma historia.
                _pct = max(0.0, min(100.0, score))
                _glow = {"#3DD68C": "61,214,140", "#E2B25C": "226,178,92",
                         "#F1495F": "241,73,95"}.get(color, "226,178,92")
                meter_html = (
                    f'<div class="meter"><span class="meter-dot" '
                    f'style="left:{_pct:.0f}%;background:{color};'
                    f'box-shadow:0 0 0 3px rgba({_glow},0.18), '
                    f'0 0 8px rgba({_glow},0.45);"></span></div>'
                )

                # Tipo de activo en pequeño junto al ticker (misma etiqueta que
                # el header del análisis): ACCIÓN / ETF / CRIPTO, fina y gris.
                _tipo_txt = {"accion": "ACCIÓN", "etf": "ETF", "crypto": "CRIPTO"}.get(
                    getattr(analysis, "asset_type", "accion") or "accion", "ACCIÓN")
                with st.container(key=f"sbcard_{tk_safe}__rk_{rec_slug}"):
                    st.markdown(
                        f'<div class="sb-card-head">'
                        f'<span class="sb-card-ticker">◈ {ticker}'
                        f'<span class="sb-card-tipo">{_tipo_txt}</span></span>'
                        f'<span class="sb-card-score" style="--sc:{color};">'
                        f'{score:.1f}'
                        f'<span class="sb-card-score-max">/100</span></span>'
                        f'</div>'
                        f'<div class="sb-badge-wrap">{badge_html}</div>'
                        f'{meter_html}',
                        unsafe_allow_html=True,
                    )
                    # Overlay invisible (CSS: absolute inset:0, opacity:0)
                    # que hace clicable TODA la tarjeta. Label real por
                    # accesibilidad y tests.
                    if st.button(f"◈ {ticker}", key=f"sbcardbtn_{tk_safe}"):
                        _sb_load_analysis(ticker)
                        st.rerun()

        # ── Historial de ESCANEOS: eliminado ────────────────────────────
        # Los escaneos ya no se persisten (pesaban demasiado y agotaban la RAM
        # del servicio), así que esta sección siempre estaría vacía. El escaneo
        # sigue funcionando con normalidad dentro de la sesión.


def render_top_nav():
    """Barra superior compacta con un botón Home centrado. Reemplaza al
    sidebar lateral en producción (Whop iframe es cuadrado — el sidebar
    apretaba demasiado el contenido). Solo se muestra en vistas NO-welcome."""
    col_a, col_home, col_c = st.columns([1, 2, 1])
    with col_home:
        if st.button("⌂  Volver al Inicio", use_container_width=True,
                     key="topnav_home_btn"):
            st.session_state.selected_ticker = None
            st.session_state.quick_view_ticker = None
            st.session_state.scan_results = []
            st.session_state.current_scan_id = None
            st.session_state._show_scan_results = False
            st.session_state.scanner_config_open = False
            st.rerun()


# ── Pre-API: validación + existencia del ticker (cero créditos Anthropic) ─
import re as _re_ticker  # alias local para no chocar con otros 're' en el archivo


def _sanitize_ticker_input(raw: str) -> tuple[str, Optional[str]]:
    """Limpia y valida el texto introducido por el usuario.

    Returns:
        (ticker_limpio, error_o_None)

    Reglas:
    - Cualquier whitespace (espacios, tabs) se elimina silenciosamente,
      en cualquier posición. "a apl " → "AAPL".
    - Solo se permiten A-Z, 0-9, '.' y '-' (tickers reales como BRK.B,
      BF-B incluyen punto y guion).
    - Otros caracteres (coma, slash, símbolos) → error explícito.
    - Largo máx. 10 chars; mínimo 1 letra.
    - Input completamente vacío → no es error, simplemente no hace nada.
    """
    if not raw:
        return "", None

    cleaned = _re_ticker.sub(r"\s+", "", raw).upper()
    if not cleaned:
        return "", None

    if not _re_ticker.fullmatch(r"[A-Z0-9.\-]+", cleaned):
        return cleaned, (
            f"El texto «{raw.strip()}» contiene caracteres no válidos para un ticker. "
            "Un ticker solo puede contener letras, números y los símbolos «.» o «-» "
            "(por ejemplo: AAPL, BRK.B, BF-B)."
        )

    if len(cleaned) > 10:
        return cleaned, (
            f"«{cleaned}» es demasiado largo para ser un ticker bursátil. "
            "Verifica que esté bien escrito (los tickers reales tienen entre 1 y 6 caracteres)."
        )

    if not _re_ticker.search(r"[A-Z]", cleaned):
        return cleaned, (
            f"«{cleaned}» no parece un ticker válido — debe contener al menos una letra."
        )

    return cleaned, None


def _ticker_exists_on_yahoo(ticker: str) -> bool:
    """Verifica que el ticker exista en Yahoo Finance.

    Usa `get_live_price`, que ya internamente usa `fast_info` de yfinance
    (la llamada más ligera disponible — descarga ~1KB en vez del .info
    completo) y cachea 60s. Es rápida (≤1s típico) y NO consume créditos
    Anthropic. Es la guarda que previene gastar tokens en tickers basura.
    """
    try:
        from data.market_data import get_live_price
        price = get_live_price(ticker)
        return bool(price and price > 0)
    except Exception:
        # Si la verificación falla por red/transient, NO bloqueamos el
        # análisis — preferimos un falso positivo (gastar créditos en un
        # ticker dudoso) que un falso negativo (bloquear un ticker real
        # porque Yahoo está rate-limitando). Errors transitorios → pasa.
        return True


def _is_analyzable_stock(ticker: str) -> bool:
    """True si el ticker es una acción analizable; False si es ETF/cripto/etc.
    Fail-open ante cualquier error (nunca bloquea una acción real)."""
    try:
        from data.market_data import is_stock_ticker
        return is_stock_ticker(ticker)
    except Exception:
        return True


def _detectar_tipo_seguro(ticker: str) -> str:
    """Tipo de activo ('accion'|'etf'|'etf_no_us'|'crypto'|'crypto_no_soportada')
    con fail-open a 'accion' ante cualquier error — nunca bloquea un ticker real."""
    try:
        from data.market_data import detectar_tipo_activo
        return detectar_tipo_activo(ticker)
    except Exception:
        return "accion"


# ── Run Analysis ──────────────────────────────────────────────────────────
_DEBUG_LOG_PATH = "/tmp/dlp_debug.log"
_DEBUG_LOG_MAX = 512 * 1024          # 512 KB


def _debug_log(msg: str) -> None:
    """Escribe a /tmp/dlp_debug.log con timestamp para depurar el flujo real.
    Se TRUNCA al pasar de 512 KB (antes crecía sin límite durante toda la vida
    del proceso, con tracebacks completos incluidos)."""
    try:
        modo = "a"
        try:
            if os.path.getsize(_DEBUG_LOG_PATH) > _DEBUG_LOG_MAX:
                modo = "w"           # es un log de diagnóstico, no de auditoría
        except OSError:
            pass
        with open(_DEBUG_LOG_PATH, modo) as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}\n")
    except Exception:
        pass


def run_analysis(ticker: str):
    import threading as _threading
    from data.persistence import save_analysis as disk_save

    _debug_log(f"run_analysis CALLED for ticker={ticker!r}")

    # ── PRE-API VALIDACIÓN — protege el gasto de créditos Anthropic ────
    # Esta capa corre ANTES de cualquier llamada al orquestador. Si el
    # ticker está mal escrito, contiene caracteres raros, o simplemente no
    # existe en Yahoo Finance, abortamos aquí mismo sin gastar un solo
    # token. La verificación de existencia usa `fast_info` (≤1s).

    # 1. Sanitizar entrada — quita espacios en cualquier posición.
    sanitized, sanitize_err = _sanitize_ticker_input(ticker)
    if sanitize_err:
        st.error(f"❌ {sanitize_err}")
        _debug_log(f"  sanitize rejected: {sanitize_err}")
        return
    if not sanitized:
        # Input vacío después de limpiar (o solo espacios) — silencioso,
        # mantiene el comportamiento previo de "click sin texto = no hace nada"
        _debug_log(f"  sanitize returned empty — silent no-op")
        return
    ticker = sanitized
    _debug_log(f"  sanitized → {ticker!r}")

    # 1b. ¿Escribió el NOMBRE de la empresa en vez del ticker? (error típico
    #     de principiante: "APPLE" en vez de "AAPL"). Se detecta con un mapa
    #     local (sin red) y se educa con el ticker correcto — mismo espíritu
    #     que el cartel de ETFs/criptos.
    _NOMBRE_A_TICKER = {
        "APPLE": "AAPL", "TESLA": "TSLA", "NVIDIA": "NVDA",
        "MICROSOFT": "MSFT", "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
        "AMAZON": "AMZN", "FACEBOOK": "META", "NETFLIX": "NFLX",
        "COCACOLA": "KO", "COCA": "KO", "DISNEY": "DIS", "INTEL": "INTC",
        "PAYPAL": "PYPL", "ADOBE": "ADBE", "SALESFORCE": "CRM",
        "ORACLE": "ORCL", "STARBUCKS": "SBUX", "MCDONALDS": "MCD",
        "WALMART": "WMT", "BOEING": "BA", "PALANTIR": "PLTR",
        "COINBASE": "COIN", "BROADCOM": "AVGO", "QUALCOMM": "QCOM",
        "HONDA": "HMC", "TOYOTA": "TM", "SONY": "SONY", "FERRARI": "RACE",
        "VISA": "V", "MASTERCARD": "MA", "PEPSI": "PEP", "PEPSICO": "PEP",
        "NIKE": "NKE", "AIRBNB": "ABNB", "UBER": "UBER", "SPOTIFY": "SPOT",
        # ETFs por nombre común
        "SP500": "SPY", "SANDP500": "SPY", "NASDAQ100": "QQQ", "NASDAQ": "QQQ",
        "VANGUARD500": "VOO", "RUSSELL2000": "IWM", "ORO": "GLD", "GOLD": "GLD",
        # Criptos por nombre (el símbolo directo BTC/ETH… lo resuelve la detección)
        "BITCOIN": "BTC", "ETHEREUM": "ETH", "ETHER": "ETH", "SOLANA": "SOL",
        "RIPPLE": "XRP", "CARDANO": "ADA", "DOGECOIN": "DOGE",
        "POLKADOT": "DOT", "CHAINLINK": "LINK", "AVALANCHE": "AVAX",
        "LITECOIN": "LTC", "TONCOIN": "TON", "POLYGON": "POL", "TRON": "TRX",
    }
    _sugerido = _NOMBRE_A_TICKER.get(ticker.replace("-", "").replace("_", ""))
    if _sugerido and _sugerido != ticker:
        st.error(
            f"❌ Para buscar una acción utiliza su **ticker** (el código de "
            f"cotización), no el nombre de la empresa.\n\n"
            f"Por ejemplo: no es **{ticker.title()}**, es **{_sugerido}**. "
            f"Escribe **{_sugerido}** en el buscador y pulsa Enter.\n\n"
            "_El análisis no se ejecutó — no se gastaron créditos._"
        )
        _debug_log(f"  company NAME detected ({ticker}) → suggested {_sugerido}")
        return

    # 2. Verificación de existencia vía Yahoo Finance (sin Claude). Si el
    #    análisis ya está cacheado en memoria, saltamos esta llamada — el
    #    cache es prueba suficiente de que el ticker existe y validamos
    #    en su día.
    if ticker not in st.session_state.analyses:
        with st.spinner(f"Verificando el ticker {ticker}…"):
            exists = _ticker_exists_on_yahoo(ticker)
        if not exists:
            st.error(
                f"❌ El ticker **{ticker}** no existe o no tiene datos de mercado disponibles.\n\n"
                "Verifica que esté bien escrito (ejemplos correctos: **AAPL** para Apple, "
                "**NVDA** para NVIDIA, **BRK.B** para Berkshire Hathaway clase B).\n\n"
                "_El análisis no se ejecutó — no se gastaron créditos._"
            )
            _debug_log(f"  yahoo says {ticker} does not exist — aborting")
            return
        _debug_log(f"  yahoo confirmed {ticker} exists")

        # 2b. Tipo de activo → cada tipo tiene SU análisis (acción, ETF US o
        #     cripto del universo) o su cartel honesto cuando no está cubierto.
        with st.spinner(f"Identificando el tipo de activo de {ticker}…"):
            _tipo_activo = _detectar_tipo_seguro(ticker)
        if _tipo_activo == "etf_no_us":
            _render_insight_card(
                "Este ETF europeo no está en el universo cubierto",
                f"**{ticker}** cotiza en una bolsa europea. Por ahora el análisis de ETFs "
                "cubre únicamente los domiciliados en Estados Unidos, donde las fuentes de "
                "datos permiten un análisis completo y fiable. Muchos ETFs europeos tienen "
                "un equivalente estadounidense sobre el mismo índice — prueba con su ticker "
                "de EE.UU. (por ejemplo, S&P 500 → **SPY** o **VOO**) — y los UCITS más "
                "populares SÍ están cubiertos: **CSPX, VUAA, VUSA, VWCE, VWRL, IWDA, "
                "XDWD, EQQQ, CNDX, ISAC** (en cualquiera de sus bolsas).",
                color="#9D8CE0", icon="🌐")
            _debug_log(f"  {ticker} is a non-US ETF — cartel shown")
            return
        if _tipo_activo == "crypto_no_soportada":
            _render_insight_card(
                "Esta criptomoneda no está en el universo analizable",
                f"**{ticker}** no forma parte de las criptomonedas principales que cubre "
                "el análisis. Están disponibles las grandes del mercado: BTC, ETH, SOL, "
                "XRP, BNB, ADA, DOGE, AVAX, LINK, DOT, LTC, TRX, TON, POL y SHIB.",
                color="#9D8CE0", icon="◈")
            _debug_log(f"  {ticker} crypto outside universe — cartel shown")
            return

    existing = st.session_state.analyses.get(ticker)
    if existing is not None:
        # Solo usar caché si la tesis es real (>300 chars) — si es fallback, re-analizar
        thesis_len = len(getattr(existing, "investment_thesis", "") or "")
        _debug_log(f"  cache hit, thesis_len={thesis_len}")
        # …y si le falta el short interest ("N/D"): esos análisis se generaron
        # cuando la fuente no cubría el NYSE (KO, JPM…). Ahora hay respaldo FINRA
        # para todas las acciones de EE.UU., así que se re-analiza para
        # completarlo en vez de arrastrar el hueco para siempre.
        # SOLO aplica a ACCIONES: un ETF o una cripto no tienen reporte
        # institucional y sin este guard se re-analizarían en cada apertura.
        _stale = False
        try:
            if getattr(existing, "asset_type", "accion") == "accion":
                _inst = (getattr(existing, "reports", {}) or {}).get("institutional")
                _si = ((getattr(_inst, "key_metrics", {}) or {}).get("short_interest") or "")
                _stale = str(_si).strip().upper() in ("N/D", "N/A", "—", "")
        except Exception:
            _stale = False
        if _stale:
            _debug_log("  cached analysis sin short interest → re-analizando")
            del st.session_state.analyses[ticker]
        elif thesis_len > 200:
            _debug_log(f"  using cached analysis, rerunning")
            st.session_state.selected_ticker = ticker
            st.session_state.quick_view_ticker = None
            st.rerun()
            return
        else:
            del st.session_state.analyses[ticker]
            _debug_log(f"  deleted bad cache")

    st.session_state.analyzing = True
    st.session_state.selected_ticker = ticker

    client = get_client()
    orchestrator = Orchestrator(client)

    loading_placeholder = st.empty()
    status_container = st.empty()

    # El tipo pudo no detectarse arriba (p. ej. el ticker venía cacheado en
    # session_state y se saltó la verificación). La detección está cacheada
    # 7 días, así que esta llamada es instantánea en la práctica.
    try:
        _tipo_activo
    except NameError:
        _tipo_activo = _detectar_tipo_seguro(ticker)

    # 6 agentes × 2 eventos (Analizando + Completado) = 12 ticks + Orquestador = 13
    # (macro+sentiment+catalysts ahora son 1 solo agente combinado: market_context)
    # ETF: 4 secciones × 2 = 8 · Cripto: 5 × 2 = 10.
    TOTAL_TICKS = {"accion": 13, "etf": 8, "crypto": 10}.get(_tipo_activo, 13)
    _metodo_analisis = {
        "accion": orchestrator.analyze,
        "etf": orchestrator.analyze_etf,
        "crypto": orchestrator.analyze_crypto,
    }.get(_tipo_activo, orchestrator.analyze)
    progress_count = [0.0]
    current_agent = [""]
    synthesis_started = [False]

    # Lanzado desde el RADAR: nada de skeleton en flujo (aparecía debajo de la
    # tarjeta pulsada) — solo el fondo oscurecido y el spinner de siempre.
    _desde_radar = bool(st.session_state.get("_show_scan_results")
                        or st.session_state.scan_results)
    _base_carga = ('<div class="alpha-dim-backdrop"></div>' if _desde_radar
                   else _skeleton_analysis_full_html())

    def _render_frame(smooth_pct: float):
        agent_label = current_agent[0] or "Iniciando agentes…"
        loading_placeholder.markdown(
            _base_carga + _spinner_overlay_html(
                text=f"ANÁLISIS DLP · {ticker}",
                sub=agent_label,
                progress=smooth_pct,
            ),
            unsafe_allow_html=True,
        )

    def progress_callback(agent_name: str, status: str):
        # Solo actualiza estado compartido — sin llamadas Streamlit desde hilos de fondo
        if agent_name == "Orquestador":
            synthesis_started[0] = True
            progress_count[0] = max(progress_count[0], TOTAL_TICKS - 1)
        elif "Analizando" in status:
            progress_count[0] += 0.5
        elif "Completado" in status or "Error" in status:
            progress_count[0] += 0.5
        current_agent[0] = f"{AGENT_ICONS.get(agent_name, '↻')} {agent_name}"

    # Lanzar el análisis en un hilo de fondo
    analysis_result = [None]
    analysis_error = [None]
    analysis_done = [False]

    def _run_bg():
        _debug_log(f"  [bg thread] STARTED for {ticker}")
        try:
            analysis_result[0] = _metodo_analisis(ticker, progress_callback=progress_callback)
            _debug_log(f"  [bg thread] {_metodo_analisis.__name__} RETURNED for {ticker}")
        except Exception as e:
            import traceback as _tb
            analysis_error[0] = e
            _debug_log(f"  [bg thread] EXCEPTION: {type(e).__name__}: {e}")
            _debug_log(f"  [bg thread] TRACEBACK:\n{_tb.format_exc()}")
        finally:
            analysis_done[0] = True
            _debug_log(f"  [bg thread] DONE flag set")

    _debug_log(f"  starting bg thread")
    bg_thread = _threading.Thread(target=_run_bg, daemon=True)
    bg_thread.start()

    # Bucle principal: actualiza el UI desde el hilo principal cada 200ms
    # smooth_pct avanza continuamente (nunca retrocede) para que la barra
    # se vea siempre en movimiento — los callbacks de agentes aceleran el avance.
    smooth_pct = [0.0]
    _render_frame(0.0)

    while not analysis_done[0]:
        time.sleep(0.2)
        real_pct = min((progress_count[0] / TOTAL_TICKS) * 100, 93.0)
        if synthesis_started[0]:
            real_pct = max(real_pct, 92.0)
        # Avanza al menos 0.4% por ciclo (≈2%/s base) + salta al progreso real si está más adelante
        smooth_pct[0] = min(smooth_pct[0] + 0.4, real_pct + 3.0, 95.0)
        _render_frame(smooth_pct[0])

    if analysis_error[0]:
        _debug_log(f"  ERROR detected in main thread: {analysis_error[0]}")
        try:
            loading_placeholder.empty()
        except Exception:
            pass
        st.error(f"Error analizando {ticker}: {analysis_error[0]}")
        st.session_state.analyzing = False
        return

    _debug_log(f"  bg thread done, result type={type(analysis_result[0]).__name__}")

    # Limpiar el loading INMEDIATAMENTE — sin sleep artificial.
    # El usuario percibe la transición como instantánea en vez de los
    # ~450ms de "tiempo muerto" que tenía antes.
    try:
        loading_placeholder.empty()
        status_container.empty()
    except Exception:
        pass

    analysis = analysis_result[0]
    # Clave = el ticker CANÓNICO del análisis (una cripto escrita como
    # "BTC-USD" se guarda como "BTC"); para acciones es el mismo de siempre.
    _tk_final = getattr(analysis, "ticker", None) or ticker
    st.session_state.analyses[_tk_final] = analysis
    st.session_state.selected_ticker = _tk_final
    st.session_state.quick_view_ticker = None
    st.session_state.analyzing = False
    # Acotar la memoria: conservar solo los N análisis más recientes en RAM
    # (el recién creado es el más nuevo, así que siempre se mantiene).
    _prune_analyses_in_memory()

    # Guardar SINCRÓNICAMENTE antes de continuar. Antes era un hilo daemon en
    # segundo plano, pero si el contenedor se recreaba justo después (redeploy,
    # reconexión, spin-down) el hilo podía no terminar y el análisis se perdía.
    # El write (Supabase + copia local) tarda ~0.2-0.8s: un coste mínimo frente
    # a perder el trabajo. Envuelto en try/except para no romper el flujo si
    # falla el IO.
    thesis_ok = len(getattr(analysis, "investment_thesis", "") or "") > 200
    if thesis_ok:
        try:
            disk_save(analysis)
        except Exception:
            pass
        # Conservar solo los N análisis más recientes (borra los viejos de
        # Supabase y disco). Evita que el historial crezca sin límite y acabe
        # agotando la memoria del servicio. El recién guardado es el más nuevo,
        # así que nunca se borra. Nunca rompe el flujo si falla.
        try:
            from data.persistence import prune_old_analyses
            prune_old_analyses(MAX_HISTORY_IN_MEMORY)
        except Exception:
            pass

    st.rerun()


# ── Run Market Scan ───────────────────────────────────────────────────────
def run_market_scan(filters: Optional[dict] = None):
    """Ejecuta un scan del mercado.
    filters: dict de filtros técnicos del screener (resultado de
             dashboard.scanner_filters.build_screener_filters).
             Si None, usa los defaults técnicos del ScreenerAgent.
    """
    st.session_state.scan_running = True
    screener = ScreenerAgent()

    # ── Carga con el MISMO lenguaje que los análisis (skeleton + spinner) y
    # duración GARANTIZADA de 10 segundos exactos: el progreso lo gobierna el
    # reloj, no el avance real. Si el escaneo real termina antes, el overlay
    # sigue animándose hasta cumplir los 10.0 s; si tardara más (rate-limits),
    # se sostiene en ~94% hasta acabar — el mínimo son siempre 10 s. ──
    _DURACION_SCAN = 10.0
    loading_placeholder = st.empty()
    _skel = ('<div class="qt-skel-grid">'
             + '<div class="qt-skel skeleton-block"></div>' * 10 + '</div>') * 2

    _estado_sub = ["Preparando el universo de acciones…"]
    _t0 = time.time()

    def _pintar():
        transcurrido = time.time() - _t0
        pct = min(transcurrido / _DURACION_SCAN * 100.0, 94.0)
        loading_placeholder.markdown(
            _skel + _spinner_overlay_html(
                text="ESCANEANDO EL MERCADO",
                sub=_estado_sub[0],
                progress=pct,
            ),
            unsafe_allow_html=True,
        )

    _ultimo_pintado = [0.0]

    def scan_callback(ticker, idx, total):
        _estado_sub[0] = f"◎ {ticker}  ·  {idx}/{total} analizadas"
        # Repintar como MUCHO 4 veces/segundo: hacerlo por cada ticker frenaba
        # el escaneo real (medido: de segundos a minutos).
        ahora = time.time()
        if ahora - _ultimo_pintado[0] >= 0.25:
            _ultimo_pintado[0] = ahora
            _pintar()

    _pintar()
    results = screener.run_full_scan(callback=scan_callback, filters=filters)

    # Completar los 10 s exactos con la animación viva (radar en barrido).
    _estado_sub[0] = "Ordenando candidatos por puntaje…"
    while time.time() - _t0 < _DURACION_SCAN:
        _pintar()
        time.sleep(0.15)
    loading_placeholder.markdown(
        _skel + _spinner_overlay_html(text="ESCANEANDO EL MERCADO",
                                      sub="Listo", progress=100.0),
        unsafe_allow_html=True,
    )
    time.sleep(0.25)
    loading_placeholder.empty()

    # Guardar diagnóstico para mostrar en la pantalla de resultados
    try:
        st.session_state._scan_diagnostics = screener.last_diagnostics
    except Exception:
        st.session_state._scan_diagnostics = {}

    st.session_state.scan_results = results
    st.session_state.scan_running = False
    # Forzar mostrar la pantalla de resultados aunque la lista venga vacía
    # (así el usuario ve "0 candidatos" en vez de ser devuelto al home).
    st.session_state._show_scan_results = True
    st.session_state._radar_scroll_top = True

    # Los escaneos YA NO SE PERSISTEN. Cada uno pesa muchísimo (cientos de
    # acciones con todos sus datos) y era la mayor fuente de consumo de RAM en
    # Render (el servicio excedía su límite de memoria). El escaneo sigue
    # funcionando igual DENTRO de la sesión (st.session_state.scan_results);
    # simplemente no se guarda al historial. Además se limpia cualquier escaneo
    # antiguo que quedara guardado de versiones anteriores.
    st.session_state.current_scan_id = None
    try:
        from data.persistence import prune_old_scans
        prune_old_scans(0)
    except Exception:
        pass

    st.rerun()


# ── Helpers reutilizables para tabs de agentes ───────────────────────────

def _conv_es(conv):
    """Traduce la convicción a español para MOSTRAR (femenino, concuerda con
    'convicción'). El valor interno (HIGH/MEDIUM/LOW) NO cambia: sigue
    alimentando los mapas de color, que usan las claves en inglés."""
    return {"HIGH": "ALTA", "MEDIUM": "MEDIA", "LOW": "BAJA"}.get(
        str(conv).upper(), str(conv))


def _render_agent_header(report):
    """Header strip con icono, nombre del agente, score y conviction badge."""
    score = report.score
    color = score_color(score)
    icon_html = _agent_icon_html(report.agent_name)
    conv_colors = {"HIGH": "#3DD68C", "MEDIUM": "#E2B25C", "LOW": "#F1495F"}
    conv_color = conv_colors.get(report.conviction, "#E2B25C")
    st.markdown(f"""
    <div class="agent-header">
        <div class="agent-header-left">
            {icon_html}
            <span class="agent-name">{report.agent_name}</span>
        </div>
        <div class="agent-header-right">
            <span class="agent-score" style="color:{color};">{score:.0f}<span class="agent-score-max">/100</span></span>
            <span class="conviction-badge" style="color:{conv_color};border-color:{conv_color}40;background:{conv_color}1A;">
                {_conv_es(report.conviction)}
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _strip_ui_emoji(text):
    """Quita emojis decorativos al inicio de un título de UI (el texto queda)."""
    import re as _re
    try:
        return _re.sub(r'^[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍\s]+', '', str(text)).strip() or str(text)
    except Exception:
        return text


def _meter_scale(value, lo, hi, invert=False):
    """Escala un dato real a 0–100 para el termómetro. lo→0, hi→100 (clamp).
    invert=True cuando MENOS es mejor (P/E, deuda, EV/EBITDA…). None si no hay dato."""
    try:
        if value is None:
            return None
        v = float(value)
        pct = (v - lo) / (hi - lo) * 100.0
        pct = max(2.0, min(98.0, pct))
        return 100.0 - pct if invert else pct
    except Exception:
        return None


def _meter_html(pct):
    """Termómetro rojo→ámbar→verde con dot en la posición del dato."""
    # Blindaje: cualquier valor NO numérico (None, texto, etc.) no pinta medidor
    # y NO rompe la fila entera de tiles/pills.
    if not isinstance(pct, (int, float)) or isinstance(pct, bool):
        return ""
    dot = "#F1495F" if pct < 35 else "#E2B25C" if pct < 68 else "#3DD68C"
    glow = {"#F1495F": "241,73,95", "#E2B25C": "226,178,92", "#3DD68C": "61,214,140"}[dot]
    return (f'<div class="meter"><span class="meter-dot" style="left:{pct:.0f}%;'
            f'background:{dot};box-shadow:0 0 0 3px rgba({glow},0.18), 0 0 8px rgba({glow},0.45);">'
            f'</span></div>')


def _render_metric_tiles(metrics):
    """Fila de KPI tiles. metrics = [{icon, label, value, color, tooltip?, meter?}]
    `meter` (0-100 opcional) pinta el termómetro de calidad del dato.
    El icon se acepta por compatibilidad pero NO se renderiza (sin emojis-icono)."""
    if not metrics:
        return
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            tooltip = m.get("tooltip", "")
            help_html = f'<span class="kpi-help" data-tooltip="{tooltip}">?</span>' if tooltip else ""
            st.markdown(f"""
            <div class="kpi-tile">
                <div class="kpi-tile-header">
                    <span class="kpi-tile-label">{m['label']}</span>
                    {help_html}
                </div>
                <div class="kpi-tile-value" style="color:{m['color']};">{m['value']}</div>
                {_meter_html(m.get('meter'))}
            </div>
            """, unsafe_allow_html=True)


def _render_status_pills(pills):
    """Fila de pills de estado. pills = [{label, value, level, meter?}].
    El color vive en un punto indicador y el termómetro traduce el nivel
    (o un `meter` 0-100 explícito) a posición rojo→ámbar→verde."""
    if not pills:
        return
    level_colors = {"good": "#3DD68C", "neutral": "#8D949E", "warn": "#E2B25C", "bad": "#F1495F"}
    level_meter = {"good": 88.0, "neutral": 55.0, "warn": 42.0, "bad": 12.0}
    cols = st.columns(len(pills))
    for col, p in zip(cols, pills):
        with col:
            level = p.get("level", "neutral")
            color = level_colors.get(level, "#8D949E")
            pct = p.get("meter", level_meter.get(level, 55.0))
            sub = p.get("sub", "")
            sub_html = f'<div class="status-pill-sub">{sub}</div>' if sub else ''
            # Botón "?" con tooltip (mismo mecanismo que los tiles de Fundamentales,
            # reutiliza la clase .kpi-help). Solo aparece si el pill trae "tooltip".
            tooltip = p.get("tooltip", "")
            help_html = f'<span class="kpi-help" data-tooltip="{tooltip}">?</span>' if tooltip else ""
            st.markdown(f"""
            <div class="status-pill">
                <div class="status-pill-header">
                    <span class="status-pill-label">{p['label']}</span>
                    {help_html}
                </div>
                <div class="status-pill-value"><span class="status-pill-dot" style="background:{color};"></span>{p['value']}</div>
                {sub_html}
                {_meter_html(pct)}
            </div>
            """, unsafe_allow_html=True)


def _no_latex(text):
    """Neutraliza el "$" para que Streamlit NO interprete el texto como LaTeX.

    La prosa generada lleva importes ("$315.32, protección $261.46…") y markdown
    trata un par de $…$ como fórmula: el tramo salía en cursiva serif ilegible
    ("206.84,proteccioˊn"). Como TODA esta prosa se pinta con
    unsafe_allow_html=True, se sustituye por la entidad HTML &#36;, que el
    navegador muestra como "$" y el parser de markdown/LaTeX ya no ve.
    Es idempotente y seguro con None."""
    if text is None:
        return ""
    return str(text).replace("$", "&#36;")


def _signal_card_html(title, items, kind):
    """Tarjeta única que agrupa las señales (kind = 'pos'|'neg')."""
    cls = "strength-item" if kind == "pos" else "risk-item"
    title_cls = "strength" if kind == "pos" else "risk"
    rows = "".join(f'<div class="{cls}">{_no_latex(i)}</div>' for i in items)
    return (f'<div class="signal-card signal-card--{kind}">'
            f'<div class="thesis-section-title {title_cls}">{_strip_ui_emoji(title)}</div>'
            f'{rows}</div>')


def _render_pros_cons(report, pros_title="Señales positivas", cons_title="Señales de riesgo"):
    # Ambas tarjetas se emiten en UN SOLO bloque flex (no en dos st.columns):
    # así `align-items: stretch` garantiza que las dos tengan SIEMPRE la misma
    # altura — la que tenga más ítems fija la altura y la otra la iguala. Con
    # columnas separadas el height:100% no propaga por el anidado de Streamlit.
    cards = ""
    if report.pros:
        cards += _signal_card_html(pros_title, report.pros[:3], "pos")
    if report.cons:
        cards += _signal_card_html(cons_title, report.cons[:3], "neg")
    if cards:
        st.markdown(f'<div class="signal-card-row">{cards}</div>',
                    unsafe_allow_html=True)


def _render_analysis_card(report, title="Análisis Detallado"):
    if not report.analysis:
        return
    st.markdown(f'<div class="section-title-bar">{_strip_ui_emoji(title)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="analysis-card"><div class="analysis-text">{_no_latex(report.analysis)}</div></div>',
        unsafe_allow_html=True,
    )


def _render_insight_card(title, content, color="#E2B25C", icon="💡"):
    """Card con barra lateral fina de acento semántico. El icon se acepta por
    compatibilidad pero no se renderiza (sin emojis-icono)."""
    if not content or not isinstance(content, str) or len(content) < 5:
        return
    st.markdown(f"""
    <div class="insight-card" style="border-left-color:{color};">
        <div class="insight-card-header">
            <span class="insight-card-title" style="color:{color};">{_strip_ui_emoji(title)}</span>
        </div>
        <div class="insight-card-body">{_no_latex(content)}</div>
    </div>
    """, unsafe_allow_html=True)


def _render_disclaimer():
    """Aviso legal al pie del Overview y del Riesgo.

    Deliberadamente discreto: mismo fondo oscuro que las demás tarjetas, gris
    apagado y letra pequeña. Tiene que estar y poder leerse, no robar atención
    al análisis. NUNCA lanza: es lo último que se pinta en la sección y no
    puede tumbarla."""
    try:
        st.markdown(
            # OJO: <div>, no <p>. La regla `.stMarkdown p` de styles.py fija
            # color con !important y tamaño 0.88rem, y se comía tanto el gris
            # apagado como la letra pequeña de este aviso.
            '<div class="disclaimer-card"><div class="disclaimer-text">'
            'DLP Analyzer se conecta y analiza en vivo los datos de mercado de cada acción. '
            'Esto no es una recomendación de inversión ni asesoría financiera personalizada.'
            '</div></div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def _aviso_ucits(analysis) -> None:
    """Tarjeta de señalización para ETFs UCITS: deja claro que la composición
    y el comportamiento mostrados son los del ÍNDICE que replica, vía su gemelo
    americano. Solo aparece en fondos UCITS. NUNCA lanza."""
    try:
        km = (analysis.reports.get("etf_perfil").key_metrics or {}) if analysis.reports.get("etf_perfil") else {}
        if not km.get("ucits"):
            return
        _render_insight_card(
            "Fondo UCITS (domiciliado en Europa)",
            f"Cotiza como <strong>{km.get('listado')}</strong> en {km.get('divisa_listado')}. "
            f"Los datos del fondo (coste, gestora, domicilio) son los del UCITS; la composición y el "
            f"comportamiento del precio corresponden al índice <strong>{km.get('indice')}</strong> que "
            f"replica, analizados a través de su gemelo americano <strong>{km.get('gemelo_us')}</strong> "
            f"(mismo índice, datos en USD).",
            color="#6FA3E0", icon="🌐")
    except Exception:
        pass


def _safe_num(value, default=None):
    """Convierte a float si es posible, retorna default si no.
    Un NaN también se trata como inválido (default): en Render los precios
    pueden llegar como NaN y sin esto salían '$nan' / 'nan%' en los tiles."""
    import math as _math
    try:
        if value is None or value == "" or value == "N/A":
            return default
        if isinstance(value, str):
            cleaned = value.replace("$", "").replace(",", "").replace("%", "").strip()
            n = float(cleaned)
        else:
            n = float(value)
        return default if _math.isnan(n) or _math.isinf(n) else n
    except Exception:
        return default


# ── Traducciones EN → ES para valores devueltos por los agentes ──────────
import re as _re

SPANISH_TRANSLATIONS = {
    "VERY BULLISH": "MUY ALCISTA",
    "VERY BEARISH": "MUY BAJISTA",
    "BULLISH": "ALCISTA",
    "BEARISH": "BAJISTA",
    "NEUTRAL": "NEUTRAL",
    "ACCUMULATING": "ACUMULANDO",
    "DISTRIBUTING": "DISTRIBUYENDO",
    "WIDE": "AMPLIO",
    "NARROW": "ESTRECHO",
    "NONE": "NINGUNO",
    "LOW": "BAJO",
    "MEDIUM": "MEDIO",
    "HIGH": "ALTO",
    "CRITICAL": "CRÍTICO",
    "EXCELLENT": "EXCELENTE",
    "GOOD": "BUENO",
    "AVERAGE": "PROMEDIO",
    "POOR": "POBRE",
    "EXPANDING RAPIDLY": "EXPANSIÓN RÁPIDA",
    "EXPANDING": "EN EXPANSIÓN",
    "STABLE": "ESTABLE",
    "CONTRACTING": "CONTRAYENDO",
    "STRONG": "FUERTE",
    "WEAK": "DÉBIL",
    "NORMAL": "NORMAL",
    "FLAT": "PLANA",
    "INVERTED": "INVERTIDA",
    "IMPROVING": "MEJORANDO",
    "DETERIORATING": "DETERIORANDO",
    "BUY THE FEAR": "COMPRAR EL MIEDO",
    "SELL THE HYPE": "VENDER EL HYPE",
    "NO SIGNAL": "SIN SEÑAL",
    "STRONG_BUY": "FUERTE COMPRA",
    "STRONG_SELL": "FUERTE VENTA",
    "STRONG BUY": "FUERTE COMPRA",
    "STRONG SELL": "FUERTE VENTA",
    "HOLD": "MANTENER",
    "PRICING POWER": "PRICING POWER",
    "NETWORK EFFECTS": "EFECTOS DE RED",
    "SWITCHING COSTS": "COSTOS DE CAMBIO",
    "COST ADVANTAGE": "VENTAJA EN COSTO",
    "INTANGIBLES": "INTANGIBLES",
    "MARKETPLACE": "MARKETPLACE",
    "PLATFORM": "PLATAFORMA",
    "TRADITIONAL": "TRADICIONAL",
    "COMMODITY": "COMMODITY",
    "OTHER": "OTRO",
    "RISK-ON": "RISK-ON",
    "RISK-OFF": "RISK-OFF",
    "HIGH POSITIVE": "ALTA POSITIVA",
    "HIGH NEGATIVE": "ALTA NEGATIVA",
    "FAVORABLE": "FAVORABLE",
    "UNFAVORABLE": "DESFAVORABLE",
}


def _translate_status(text):
    """Reemplaza términos en inglés por su equivalente en español (preserva mayúsculas/minúsculas del original)."""
    if not text or not isinstance(text, str):
        return text
    upper = text.upper().strip()
    if upper in SPANISH_TRANSLATIONS:
        # Mantén el case: si el original estaba en MAYÚS, devuelve MAYÚS
        if text.isupper():
            return SPANISH_TRANSLATIONS[upper]
        return SPANISH_TRANSLATIONS[upper].capitalize()
    # Reemplaza término por término (longest first)
    result = text
    for en, es in sorted(SPANISH_TRANSLATIONS.items(), key=lambda x: -len(x[0])):
        if text.isupper():
            replacement = es
        else:
            replacement = es.capitalize() if en[0].isupper() else es.lower()
        result = _re.sub(rf'\b{_re.escape(en)}\b', replacement, result, flags=_re.IGNORECASE)
    return result


def _clean_tile_value(value, max_len=22):
    """Limpia valor para tile: quita paréntesis, descripciones largas y traduce.

    NO trunca con "…": el valor llega COMPLETO al DOM y la rutina JS fitText()
    (en inject_protection) encoge la fuente para que quepa en UNA sola línea. El
    parámetro max_len se conserva por compatibilidad con los call sites, pero ya
    no recorta (la prioridad es que la palabra se vea entera)."""
    if value is None or value == "":
        return "—"
    s = str(value).strip()
    if not s or s.upper() in ("N/A", "—", "NONE", "NULL"):
        return "—"
    # Quita contenido en paréntesis (descripciones largas)
    s = _re.sub(r'\s*\([^)]*\)\s*', ' ', s).strip()
    # Quita descripciones largas tras " - " o " — " si tienen > 15 chars
    s = _re.sub(r'\s+[-—]\s+.{15,}$', '', s).strip()
    # Si empieza con "N/A", lo limpiamos
    if s.upper().startswith("N/A"):
        return "—"
    # Traduce términos comunes
    s = _translate_status(s)
    return s


def _extract_rr_ratio(value):
    """Extrae 'X.X:1' de un string como '1.82:1 ❌ INSUFICIENTE' (1 decimal)."""
    if value is None or value == "":
        return "—"
    s = str(value)
    m = _re.search(r'(\d+\.?\d*)\s*:\s*(\d+\.?\d*)', s)
    if m:
        try:
            num = float(m.group(1))
            den = float(m.group(2))
            return f"{num:.1f}:{int(den) if den == int(den) else den:.1f}"
        except Exception:
            return f"{m.group(1)}:{m.group(2)}"
    n = _safe_num(value)
    if n is not None:
        return f"{n:.1f}:1"
    return s[:10] if s else "—"


# ── Loading skeletons + spinner pequeño centrado ─────────────────────────

def _spinner_overlay_html(text: str = "CARGANDO", sub: str = "",
                          progress: float = None) -> str:
    """HTML del overlay de carga centrado.
    - progress=None  → spinner indeterminate (Quick View, scans, etc.)
    - progress=0-100 → ring circular SVG con % real animado suavemente

    NOTA: el HTML se construye SIN indentación interna porque Streamlit
    interpreta texto con 4+ espacios al inicio de línea como bloque de
    código (<pre>), mostrando el HTML crudo como texto.
    """
    sub_html = f'<div class="alpha-spinner-sub">{sub}</div>' if sub else ""

    if progress is None:
        indicator_html = '<div class="alpha-spinner"></div>'
    else:
        pct = max(0, min(100, float(progress)))
        circumference = 238.76  # 2π × 38 (radio del círculo en el SVG)
        offset = circumference * (1 - pct / 100)
        state_class = "complete" if pct >= 99.5 else ""
        indicator_html = (
            f'<div class="alpha-progress-ring-wrap {state_class}">'
            f'<svg class="alpha-progress-svg" viewBox="0 0 92 92">'
            f'<circle class="alpha-progress-bg" cx="46" cy="46" r="38"></circle>'
            f'<circle class="alpha-progress-fg" cx="46" cy="46" r="38" '
            f'style="stroke-dashoffset: {offset:.2f};"></circle>'
            f'</svg>'
            f'<div class="alpha-progress-value">{pct:.0f}%</div>'
            f'</div>'
        )

    return (
        f'<div class="alpha-spinner-overlay">'
        f'{indicator_html}'
        f'<div class="alpha-spinner-text">{text}</div>'
        f'{sub_html}'
        f'</div>'
    )


def _skeleton_quick_view_html() -> str:
    """Skeleton para la vista rápida — header + chart + métricas + noticias.
    HTML sin indentación interna (ver nota en _spinner_overlay_html)."""
    return (
        '<div class="skeleton-block skeleton-header" style="margin-bottom:18px;"></div>'
        '<div class="skeleton-grid skeleton-row-2">'
        '<div class="skeleton-block skeleton-chart"></div>'
        '<div>'
        '<div class="skeleton-block skeleton-tile" style="margin-bottom:8px;"></div>'
        '<div class="skeleton-block skeleton-tile" style="margin-bottom:8px;"></div>'
        '<div class="skeleton-block skeleton-tile" style="margin-bottom:8px;"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '</div>'
        '</div>'
        '<div style="margin-top:18px;"></div>'
        '<div class="skeleton-grid skeleton-row-6">'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '</div>'
        '<div style="margin-top:18px;"></div>'
        '<div class="skeleton-grid skeleton-row-2">'
        '<div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '</div>'
        '<div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '</div>'
        '</div>'
    )


def _skeleton_analysis_full_html() -> str:
    """Skeleton para el análisis DLP completo — overview con gauge + snowflake + breakdown + niveles.
    HTML sin indentación interna (ver nota en _spinner_overlay_html)."""
    return (
        '<div class="skeleton-grid" style="grid-template-columns: 1.2fr 1fr 1.5fr;">'
        '<div class="skeleton-block" style="height:280px;"></div>'
        '<div class="skeleton-block" style="height:280px;"></div>'
        '<div class="skeleton-block" style="height:280px;"></div>'
        '</div>'
        '<div style="margin-top:24px;"></div>'
        '<div class="skeleton-grid skeleton-row-2">'
        '<div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '<div class="skeleton-block skeleton-tile"></div>'
        '</div>'
        '<div>'
        '<div class="skeleton-block" style="height:160px;"></div>'
        '<div style="margin-top:14px;"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '<div class="skeleton-block skeleton-list-item"></div>'
        '</div>'
        '</div>'
    )


def _extract_percent(value):
    """Extrae el primer % de un string. Ej: '~31.2% (entre top 8...)' → '~31.2%'."""
    if value is None or value == "":
        return "—"
    s = str(value)
    m = _re.search(r'([~<>]?\s*-?\d+\.?\d*\s*%)', s)
    if m:
        return m.group(1).replace(" ", "")
    return _clean_tile_value(value)


def _live_risk_levels(analysis):
    """(precio_actual, protección, objetivo) EN VIVO — la MISMA lógica y las
    MISMAS fuentes que la pestaña de Riesgo y la gráfica R/R: precio en vivo
    (get_company_info) + niveles del análisis con respaldo get_risk_levels
    (OHLCV/TradingView). Cualquiera puede ser None si no hay datos."""
    from data.market_data import get_company_info, get_risk_levels
    info_live = get_company_info(analysis.ticker) or {}
    price  = _safe_num(info_live.get("current_price")) or _safe_num(analysis.entry_price)
    stop   = _safe_num(analysis.stop_loss)
    target = _safe_num(analysis.target_price) or _safe_num(info_live.get("target_price"))
    if price is None or stop is None or target is None:
        fr = get_risk_levels(analysis.ticker)
        if fr:
            price  = price  or fr.get("current_price")
            stop   = stop   or fr.get("stop")
            target = target or fr.get("target")
    # La protección NUNCA por encima del precio actual: si la acción cayó por
    # debajo del stop guardado en el análisis (p.ej. ORCL tras un desplome), se
    # reancla ~1% bajo el precio VIVO — sin esto el R/R y la asimetría salían
    # invertidos ("mínimo" por encima del precio actual, sin sentido).
    if price and stop and stop >= price * 0.99:
        stop = round(price * 0.99, 2)
    return price, stop, target


def _asymmetry_view(analysis):
    """Recalcula la asimetría REAL desde el precio actual, el objetivo y la
    protección EN VIVO — los MISMOS tres números que muestra la pestaña de
    Riesgo. Antes se leía un valor persistido del análisis que (a) solo detectaba
    la asimetría al alza y (b) podía contradecir lo que se ve en Riesgo.

        subida (upside)   = (objetivo − precio) / precio
        caída  (downside) = (precio − protección) / precio
        rr = subida / caída   ( >1 favorece al alza; <1 favorece la caída )

    Devuelve un dict listo para pintar, o None si no hay tres niveles válidos
    (entonces el render cae a los valores persistidos). NUNCA lanza."""
    try:
        price, stop, target = _live_risk_levels(analysis)
    except Exception:
        return None
    if not (price and stop and target) or not (stop < price < target):
        return None
    up   = (target - price) / price * 100.0
    down = (price - stop)   / price * 100.0
    if down <= 0:
        return None
    rr = up / down
    # Bandas SIMÉTRICAS alrededor de rr=1: una diferencia material en CUALQUIER
    # dirección (subida>caída o caída>subida) se marca como asimetría real. La
    # banda "equilibrado" es estrecha (±15%) para no tapar asimetrías reales.
    if   rr >= 2.5:  direction, strength = "alcista", "fuerte"
    elif rr >= 1.5:  direction, strength = "alcista", "moderado"
    elif rr >= 1.15: direction, strength = "alcista", "débil"
    elif rr >  0.87: direction, strength = "equilibrado", "moderado"
    elif rr >  0.67: direction, strength = "bajista", "débil"
    elif rr >  0.40: direction, strength = "bajista", "moderado"
    else:            direction, strength = "bajista", "fuerte"

    if direction == "alcista":
        icon, title = "📈", "Asimetría al Alza"
        body = (f"El potencial de subida hasta el objetivo (<span class='em'>+{up:.1f}%</span>) "
                f"supera al riesgo de caída hasta la protección (<span class='em'>−{down:.1f}%</span>): "
                f"una relación de <span class='em'>{rr:.1f} a 1</span> a favor. La recompensa esperada "
                f"compensa el riesgo asumido en el punto actual.")
        alpha = (f"Relación favorable: por cada 1% que se arriesga hasta la protección hay "
                 f"<b>~{rr:.1f}%</b> de recorrido potencial hasta el objetivo "
                 f"(<b>+{up:.1f}%</b> arriba frente a <b>−{down:.1f}%</b> abajo). La ventaja está en el "
                 f"precio de entrada actual.")
    elif direction == "bajista":
        inv = (down / up) if up else 0.0
        icon, title = "📉", "Asimetría a la Baja"
        body = (f"El riesgo de caída hasta la protección (<span class='em'>−{down:.1f}%</span>) "
                f"supera al potencial de subida hasta el objetivo (<span class='em'>+{up:.1f}%</span>): "
                f"se arriesga <span class='em'>{inv:.1f}</span> por cada 1 de recorrido al alza. La "
                f"recompensa actual no compensa el riesgo.")
        alpha = (f"Hoy la relación juega en contra: la caída potencial (<b>−{down:.1f}%</b>) es mayor que "
                 f"la subida potencial (<b>+{up:.1f}%</b>). Conviene esperar a un mejor punto de entrada "
                 f"que ofrezca una relación más favorable antes de tomar posición.")
    else:
        icon, title = "⚖️", "Riesgo Equilibrado"
        body = (f"El potencial de subida (<span class='em'>+{up:.1f}%</span>) y el riesgo de caída "
                f"(<span class='em'>−{down:.1f}%</span>) están muy parejos (relación "
                f"<span class='em'>{rr:.1f} a 1</span>). No hay una ventaja de asimetría de precio marcada.")
        alpha = (f"Subida y caída potenciales están parejas (<b>+{up:.1f}%</b> vs <b>−{down:.1f}%</b>). "
                 f"La ventaja no está en la asimetría de precio, sino en la calidad estructural del "
                 f"negocio y el horizonte temporal.")
    return {"direction": direction, "strength": strength, "icon": icon, "title": title,
            "body": body, "alpha": alpha, "upside": up, "downside": down, "rr": rr}


# ── Overview Tab ──────────────────────────────────────────────────────────
def render_overview(analysis: StockAnalysis):
    # Fila 1: Gauge (tacómetro) + Snowflake (radar), lado a lado y bien
    # proporcionados. El desglose de barras baja a su propia fila (abajo) para
    # que ninguna de las tres se solape ni se corte.
    col_gauge, col_snow = st.columns([1, 1], gap="medium")

    with col_gauge:
        fig = build_gauge(analysis.composite_score, analysis.recommendation)
        _plotly(fig, use_container_width=True, config={"displayModeBar": False},
                        key=f"chart_overview_gauge_{analysis.ticker}")

        # Badge de recomendación
        badge_html = get_recommendation_badge(analysis.recommendation)
        st.markdown(
            f'<div style="text-align:center;margin-top:-10px;">{badge_html}</div>',
            unsafe_allow_html=True,
        )

        # Conviction
        conviction_color = {"HIGH": "#3DD68C", "MEDIUM": "#C08E3B", "LOW": "#F1495F"}.get(
            analysis.conviction_level, "#C08E3B"
        )
        st.markdown(
            f'<div style="text-align:center;font-family:JetBrains Mono;font-size:0.75rem;color:{conviction_color};margin-top:4px;">'
            f'Convicción: {_conv_es(analysis.conviction_level)}</div>',
            unsafe_allow_html=True,
        )

    with col_snow:
        fig = build_snowflake(analysis.snowflake)
        # ÚNICA gráfica con hover: al pasar el ratón por un vértice muestra un
        # pop-up con la categoría y su calificación en grande. Por eso NO lleva
        # staticPlot (que desactivaría el hover). Zoom/arrastre siguen
        # bloqueados por _plotly (dragmode=False + sin scrollZoom/doubleClick).
        _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False},
                        key=f"chart_overview_snowflake_{analysis.ticker}")

    # Fila 2: Desglose por análisis (barras) a todo el ancho, para que se lean
    # completas las 8 barras sin recortes.
    # Reconstruimos el desglose desde los REPORTES reales para que cada barra
    # (incluida Riesgo) coincida SIEMPRE con su pestaña — también en análisis
    # antiguos cargados de disco cuyo score_breakdown guardado no incluía el
    # riesgo (antes mostraba 50 fijo).
    breakdown = dict(analysis.score_breakdown or {})
    for _k in ("fundamentals", "technical", "future", "institutional",
               "catalysts", "macro", "sentiment", "risk"):
        _rep = analysis.reports.get(_k)
        if _rep is not None:
            breakdown[_k] = _rep.score
    fig = build_score_breakdown(breakdown)
    _plotly(fig, use_container_width=True, config={"displayModeBar": False},
                    key=f"chart_overview_breakdown_{analysis.ticker}")

    st.markdown("---")

    # Fila 2: Info básica + Tesis + Niveles
    col_info, col_thesis = st.columns([1, 2])

    with col_info:
        st.markdown("#### Información")
        # Info en vivo (cacheada 60s): de aquí salen sector e industria, que
        # tienen respaldo TradingView y por tanto llegan también en Render.
        from data.market_data import get_company_info, get_risk_levels
        _live_info = get_company_info(analysis.ticker) or {}

        # Descripción del negocio SIN IA: se traduce la industria con un mapa
        # estático (data/industry_labels.py). La descripción larga de yfinance
        # no sirve aquí — viene en inglés y en Render llega vacía.
        #
        # BLINDAJE: si no se consigue el dato (acción poco conocida, fuentes
        # caídas, o incluso si el módulo fallara al importar), la fila
        # simplemente NO SE PINTA. Nunca se muestra "—", "Unknown" ni un error:
        # se construye el diccionario solo con lo que tiene valor real.
        try:
            from data.industry_labels import sector_es, describe_business
            _sector_txt = sector_es(_live_info.get("sector") or analysis.sector)
            _desc_txt = describe_business(_live_info.get("industry"),
                                          _live_info.get("sector") or analysis.sector)
        except Exception:
            _sector_txt = _desc_txt = ""

        def _hay(v):
            """Solo se pinta una fila si su valor es texto útil de verdad."""
            return bool(v) and str(v).strip().lower() not in (
                "", "—", "-", "n/a", "n/d", "none", "unknown", "nan")

        info_data = {}
        if _hay(analysis.company_name):
            info_data["Empresa"] = analysis.company_name
        if _hay(_sector_txt):
            info_data["Sector"] = _sector_txt
        if _hay(_desc_txt):
            info_data["Descripción"] = _desc_txt

        for k, v in info_data.items():
            # Layout grid (NO flex) — evita que key y value se solapen cuando el
            # value es largo. La Descripción lleva un modificador que le quita el
            # recorte de 2 líneas: se expande hacia abajo en vez de cortarse
            # con "…" (hay espacio de sobra en esta columna).
            cls = "overview-info-value"
            if k == "Descripción":
                cls += " overview-info-value--desc"
            st.markdown(
                f'<div class="overview-info-row">'
                f'<span class="overview-info-key">{k}</span>'
                f'<span class="{cls}">{_no_latex(v)}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Métricas Clave (KPIs premium con tooltips) ───────────
        if any([analysis.entry_price, analysis.target_price, analysis.risk_reward]):
            st.markdown('<div class="kpi-section-title">Métricas Clave</div>', unsafe_allow_html=True)

            # El "Precio Actual" debe reflejar SIEMPRE el precio en vivo del
            # momento en que se abre el análisis — aunque el análisis venga de
            # caché. get_company_info() sobreescribe current_price con el precio
            # en vivo (TTL 60s); si no está disponible, cae al entry_price
            # persistido. _safe_num filtra None/NaN → nunca "$nan".
            from data.market_data import get_company_info, get_risk_levels
            _live_info = get_company_info(analysis.ticker) or {}
            _current_price = _safe_num(_live_info.get("current_price")) or _safe_num(analysis.entry_price)
            # Target de analistas de get_company_info como respaldo probado en Render.
            _target = _safe_num(analysis.target_price) or _safe_num(_live_info.get("target_price"))
            _stop   = _safe_num(analysis.stop_loss)
            # Respaldo INFALIBLE: si el análisis cacheado no trae precio/stop/target
            # (datos bloqueados al generarse), se recalculan frescos (OHLCV o TradingView).
            if _target is None or _current_price is None or _stop is None:
                _fr = get_risk_levels(analysis.ticker)
                if _fr:
                    _current_price = _current_price or _fr.get("current_price")
                    _target = _target or _fr.get("target")
                    _stop   = _stop   or _fr.get("stop")
            # La protección NUNCA por encima del precio vivo: si la acción cayó
            # bajo el stop guardado, se reancla ~1% bajo el precio actual.
            if _current_price and _stop and _stop >= _current_price * 0.99:
                _stop = round(_current_price * 0.99, 2)
            # R/R calculado desde el PRECIO ACTUAL en vivo — IDÉNTICO a la pestaña
            # de Riesgo (antes leía analysis.risk_reward, calculado sobre una
            # entrada hipotética distinta → discrepaba con la pestaña de Riesgo).
            _down = ((_current_price - _stop) / _current_price * 100) if (_current_price and _stop) else None
            _up   = ((_target - _current_price) / _current_price * 100) if (_current_price and _target) else None
            rr_num = (_up / _down) if (_down and _down > 0 and _up is not None) else None
            entry_str  = f"${_current_price:.2f}"  if _current_price else "—"
            target_str = f"${_target:.2f}" if _target else "—"
            rr_str     = (f"{rr_num:.1f}:1" if rr_num is not None else _extract_rr_ratio(analysis.risk_reward))

            metrics = [
                {
                    "icon": "📍", "label": "Precio Actual", "value": entry_str, "color": "#E2B25C",
                    "tooltip": "Precio actual del activo en vivo (se refresca al abrir el análisis). Se usa como línea de referencia para calcular el upside hasta el precio objetivo y el downside hasta el nivel de protección.",
                },
                {
                    "icon": "🏁", "label": "Precio Objetivo", "value": target_str, "color": "#3DD68C",
                    "tooltip": "Precio donde tomar ganancias totales o parciales. Combina la resistencia técnica cercana (52W high, niveles psicológicos) con el valor intrínseco fundamental estimado.",
                },
                {
                    "icon": "⚖️", "label": "R/R Ratio", "value": rr_str,
                    "color": ("#3DD68C" if (rr_num or 0) >= 3 else
                              "#E2B25C" if (rr_num or 0) >= 2 else "#F1495F"),
                    "tooltip": "Risk/Reward Ratio — relación entre la ganancia potencial al target y la pérdida máxima al stop. Un 3:1 significa que arriesgas 1 para ganar 3. Mínimo aceptable para operar: 2:1. El color del valor indica si supera el umbral (verde ≥3, amarillo ≥2, rojo <2).",
                },
            ]

            # Tile NUEVO: Calidad de Largo Plazo (solo si está disponible — backward compat)
            lt_quality = getattr(analysis, "long_term_quality_score", None)
            if lt_quality is not None:
                quality_verdict = getattr(analysis, "quality_verdict", "") or ""
                verdict_es = {
                    "best-in-class": "Best-in-Class",
                    "high":          "Alta Calidad",
                    "average":       "Calidad Media",
                    "low":           "Calidad Baja",
                }.get(quality_verdict, quality_verdict.title())
                metrics.append({
                    "icon": "🏛️", "label": "Calidad LP", "value": f"{lt_quality:.0f}/100",
                    "color": ("#3DD68C" if lt_quality >= 85 else
                              "#6FA3E0" if lt_quality >= 70 else
                              "#E2B25C" if lt_quality >= 55 else "#F1495F"),
                    "tooltip": f"Calidad estructural de largo plazo (3-7 años). Promedio de Fundamentales + Future Viability. Veredicto: {verdict_es}. Empresas con score ≥85 son COMPOUNDERS (best-in-class) que merecen hold de muy largo plazo.",
                })

            for m in metrics:
                st.markdown(f"""
                <div class="kpi-tile">
                    <div class="kpi-tile-header">
                        <span class="kpi-tile-label">{m['icon']} {m['label']}</span>
                        <span class="kpi-help" data-tooltip="{m['tooltip']}">?</span>
                    </div>
                    <div class="kpi-tile-value" style="color:{m['color']};">{m['value']}</div>
                </div>
                """, unsafe_allow_html=True)

            # ── Upside/Downside COMPACTA — justo debajo de las Métricas Clave,
            #    aprovechando el hueco de esta columna. Usa los MISMOS niveles ya
            #    calculados (_current_price/_stop/_target). La versión grande vive
            #    en la pestaña de Riesgo (build_rr_chart sin compact). La propia
            #    figura ya lleva su título "UPSIDE/DOWNSIDE · R/R", así que aquí
            #    no se añade section-title-bar (evita el título duplicado). ──────
            if _current_price and _stop and _target:
                _plotly(build_rr_chart(_current_price, _stop, _target, analysis.ticker, compact=True),
                        use_container_width=True, config={"displayModeBar": False},
                        key=f"chart_overview_rr_{analysis.ticker}")

        # ── Vetos aplicados (alert box) ──────────────────────────
        if analysis.vetos_applied:
            st.markdown("""
            <div class="veto-section-header">
                <span class="veto-icon">⚠️</span>
                <span class="veto-title">Vetos Aplicados</span>
            </div>
            """, unsafe_allow_html=True)
            for veto in analysis.vetos_applied:
                st.markdown(f'<div class="veto-item">{_no_latex(veto)}</div>', unsafe_allow_html=True)

    with col_thesis:
        st.markdown("#### Tesis de Inversión")
        st.markdown(
            f'<div class="analysis-card"><div class="analysis-text">{_no_latex(analysis.investment_thesis)}</div></div>',
            unsafe_allow_html=True,
        )

        # ── Fortalezas / Riesgos en signal-cards de IGUAL altura ──────
        # Un solo bloque flex (no dos columnas) → align-items:stretch iguala
        # la altura de ambas tarjetas a la de la más alta.
        _sr_cards = ""
        if analysis.key_strengths:
            _sr_cards += _signal_card_html("Fortalezas Clave", analysis.key_strengths, "pos")
        if analysis.key_risks:
            _sr_cards += _signal_card_html("Riesgos Clave", analysis.key_risks, "neg")
        if _sr_cards:
            st.markdown(f'<div class="signal-card-row">{_sr_cards}</div>',
                        unsafe_allow_html=True)

        # ── Diagnóstico de Asimetría — RECALCULADO EN VIVO desde el precio
        #    actual, el objetivo y la protección (los MISMOS tres números que
        #    muestra la pestaña de Riesgo). Detecta la asimetría en AMBAS
        #    direcciones (subida>caída y caída>subida) y con cifras reales. ─────
        _strength_es = {"fuerte": "FUERTE", "moderado": "MODERADA", "débil": "LEVE"}
        _av = _asymmetry_view(analysis)
        if _av is not None:
            _s = _strength_es.get(_av["strength"], _av["strength"].upper())
            st.markdown(f"""
            <div class="asymmetry-card {_av['direction']}">
                <div class="asymmetry-header">
                    <span class="asymmetry-icon">{_av['icon']}</span>
                    <span class="asymmetry-title">{_av['title']}</span>
                    <span class="asymmetry-strength">{_s}</span>
                </div>
                <div class="asymmetry-body">{_av['body']}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            # Respaldo: sin tres niveles en vivo, usa el diagnóstico persistido.
            asym_dir = getattr(analysis, "asymmetry_direction", None)
            asym_str = getattr(analysis, "asymmetry_strength", None)
            if asym_dir in ("alcista", "bajista", "equilibrado"):
                asym_config = {
                    "alcista": {"icon": "📈", "title": "Asimetría al Alza",
                        "body": "El <span class='em'>potencial alcista supera materialmente al riesgo bajista</span>. La situación actual favorece tomar posición — la recompensa esperada justifica el riesgo asumido."},
                    "bajista": {"icon": "📉", "title": "Asimetría a la Baja",
                        "body": "El <span class='em'>riesgo bajista supera al potencial alcista</span>. La recompensa actual NO compensa el riesgo. Esperar mejor punto de entrada o evitar la posición."},
                    "equilibrado": {"icon": "⚖️", "title": "Riesgo Equilibrado",
                        "body": "El <span class='em'>potencial alcista y el riesgo bajista son similares</span>. No hay ventaja clara de asimetría — la decisión debe basarse en la calidad estructural del negocio y el horizonte temporal."},
                }[asym_dir]
                strength_label = ""
                if asym_str:
                    strength_label = f'<span class="asymmetry-strength">{_strength_es.get(asym_str, asym_str.upper())}</span>'
                st.markdown(f"""
                <div class="asymmetry-card {asym_dir}">
                    <div class="asymmetry-header">
                        <span class="asymmetry-icon">{asym_config['icon']}</span>
                        <span class="asymmetry-title">{asym_config['title']}</span>
                        {strength_label}
                    </div>
                    <div class="asymmetry-body">{asym_config['body']}</div>
                </div>
                """, unsafe_allow_html=True)

        # ── Oportunidad Asimétrica — interpretación accionable con las cifras
        #    reales (mismos niveles). Si no hay niveles en vivo, cae al texto
        #    persistido del orquestador. ─────────────────────────────────────
        _alpha_txt = (_av["alpha"] if _av is not None else
                      (analysis.alpha_opportunity
                       if analysis.alpha_opportunity and analysis.alpha_opportunity != "No identificada"
                       else None))
        if _alpha_txt:
            st.markdown(f"""
            <div class="alpha-opportunity-card">
                <div class="alpha-opportunity-header">
                    <span class="alpha-opportunity-icon">⚡</span>
                    <span class="alpha-opportunity-title">Oportunidad Asimétrica</span>
                </div>
                <div class="alpha-opportunity-body">{_no_latex(_alpha_txt)}</div>
            </div>
            """, unsafe_allow_html=True)

    # (La gráfica Upside/Downside del Overview se movió a la columna izquierda,
    # justo debajo de las Métricas Clave, en versión compacta — ver arriba en
    # `with col_info`. Así aprovecha el hueco de esa columna y no deja espacio
    # vacío. La versión grande sigue en la pestaña de Riesgo.)


# ── Technical Tab ─────────────────────────────────────────────────────────
def render_technical(analysis: StockAnalysis):
    tech_report = analysis.reports.get("technical")
    if tech_report is None:
        st.info("Análisis técnico no disponible.")
        return

    # Header con score + conviction
    _render_agent_header(tech_report)

    # ── Gráfica principal (candlestick + MAs + RSI + MACD + Volumen) ──
    from data.market_data import get_price_history, get_technical_indicators
    df = get_price_history(analysis.ticker, period="2y")
    # Indicadores con respaldo INFALIBLE (OHLCV → TradingView): Stage, 52W, MA,
    # RSI, ATR SIEMPRE con datos reales, aunque Yahoo/Nasdaq estén bloqueados.
    indicators = get_technical_indicators(analysis.ticker, df)

    # ── MODO DE ANÁLISIS ───────────────────────────────────────────────────
    # Un único control, centrado y protagonista, con dos modos:
    #   Pro     → gráfica completa (velas + medias + volumen + RSI + MACD)
    #   Básico  → versión simplificada (solo el cierre, con degradado)
    # Se usan dos st.button en vez de st.segmented_control porque este último
    # no admite iconos en las etiquetas y su contenedor real es
    # data-testid="stButtonGroup" (no "stSegmentedControl"), difícil de anclar.
    # Con botones, cada uno lleva la clase estable .st-key-<key>, sobre la que
    # el CSS dibuja el icono y centra el bloque.
    PRO, BASICO = "pro", "basico"
    mode_key = "_chart_mode"

    # Cualquier análisis abre SIEMPRE en modo Pro: al cambiar la acción que se
    # está viendo se restablece el defecto.
    if st.session_state.get("_chart_mode_ticker") != analysis.ticker:
        st.session_state["_chart_mode_ticker"] = analysis.ticker
        st.session_state[mode_key] = PRO
    mode = st.session_state.get(mode_key, PRO)

    st.markdown("""
    <div class="mode-switch-head">
        <span class="mode-switch-rule"></span>
        <span class="mode-switch-label">Modo de análisis</span>
        <span class="mode-switch-rule"></span>
    </div>
    """, unsafe_allow_html=True)

    # [1,2,2,1] deja las dos columnas centrales justo en el centro de la
    # página, y el CSS centra cada botón dentro de la suya. Se les da 1/3 del
    # ancho (no 1/6) para que en un iframe estrecho el botón siga teniendo
    # sitio de sobra y la etiqueta nunca se parta.
    _ms_l, ms_pro, ms_bas, _ms_r = st.columns([1, 2, 2, 1], gap="small")
    with ms_pro:
        if st.button("Pro", key="chart_mode_pro", use_container_width=True,
                     type="primary" if mode == PRO else "secondary"):
            st.session_state[mode_key] = PRO
            st.rerun()
    with ms_bas:
        if st.button("Básico", key="chart_mode_basico", use_container_width=True,
                     type="primary" if mode == BASICO else "secondary"):
            st.session_state[mode_key] = BASICO
            st.rerun()

    is_line = (mode == BASICO)

    title = "Precio — Vista Simplificada" if is_line else "Chart Multi-Indicador"
    st.markdown(f'<div class="section-title-bar">{title}</div>', unsafe_allow_html=True)

    fig = (build_mountain_chart(df, analysis.ticker) if is_line
           else build_price_chart(df, indicators, analysis.ticker))
    # No se puede arrastrar ni hacer zoom (dragmode=False en la figura +
    # scrollZoom off), pero SÍ se mantiene el hover para leer precio/OHLC.
    _plotly(
        fig, use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False},
        key=f"chart_technical_price_{analysis.ticker}_{'line' if is_line else 'candles'}",
    )

    # ── Status pills clave (Stage, RSI, MACD, Distancia 52W high) ──
    st.markdown('<div class="section-title-bar">Indicadores Clave</div>', unsafe_allow_html=True)

    # Todos los indicadores pasan por _safe_num → NaN/None se muestran como "—",
    # nunca como "nan%". (En cloud, si un dato faltara puntualmente, degrada bien.)
    stage = int(_safe_num(indicators.get("stage")) or 0)
    stage_level = "good" if stage == 2 else "neutral" if stage == 1 else "warn" if stage == 3 else "bad"
    stage_sub = {2: "Tendencia alcista", 1: "Acumulación", 3: "Distribución", 4: "Bajista"}.get(stage, "Sin definir")

    rsi = _safe_num(indicators.get("rsi_14"))
    rsi_level = "neutral" if rsi is None else ("bad" if rsi > 70 or rsi < 30 else "good" if 40 <= rsi <= 60 else "neutral")

    macd_hist = _safe_num(indicators.get("macd_hist"))
    macd_level = "neutral" if macd_hist is None else ("good" if macd_hist > 0 else "bad")
    macd_val = "—" if macd_hist is None else ("Alcista" if macd_hist > 0 else "Bajista")

    pct_high = _safe_num(indicators.get("pct_from_52w_high"))
    high_level = "neutral" if pct_high is None else ("good" if pct_high > -5 else "neutral" if pct_high > -15 else "bad")
    # En máximos: el precio está a 0.0% del máximo de 52 semanas (rango > -0.05
    # cubre el redondeo). En vez de "0.0%" se muestra "EN MÁXIMOS", encajado.
    _at_high = pct_high is not None and pct_high > -0.05
    high_value = "—" if pct_high is None else ("EN MÁXIMOS" if _at_high else f"{pct_high:.1f}%")
    high_sub = ("En su máximo 52S" if _at_high else
                "Cerca del máximo" if (pct_high is not None and pct_high > -5) else
                "Lejos del máximo" if pct_high is not None else "sin dato")

    _render_status_pills([
        {"label": "Stage Minervini", "value": (f"Stage {stage}" if stage else "—"), "level": stage_level, "sub": stage_sub,
         "tooltip": "Fase de la tendencia según Mark Minervini: 1 = base/acumulación tras una caída; 2 = tendencia alcista sana (la ideal para comprar, precio sobre sus medias); 3 = techo/distribución; 4 = tendencia bajista. Indica en qué momento del ciclo está la acción."},
        {"label": "RSI 14", "value": (f"{rsi:.1f}" if rsi is not None else "—"), "level": rsi_level,
         "sub": ("Sobrecomprado" if (rsi or 0) > 70 else "Sobrevendido" if (rsi is not None and rsi < 30) else "Neutral"),
         "tooltip": "Índice de Fuerza Relativa (0-100): mide si la acción viene muy comprada o muy vendida a corto plazo. Por encima de 70 = sobrecomprada (puede corregir); por debajo de 30 = sobrevendida (puede rebotar); 40-60 = zona neutral y sana."},
        {"label": "MACD Hist", "value": macd_val, "level": macd_level,
         "sub": (f"{macd_hist:+.3f}" if macd_hist is not None else "sin dato"),
         "tooltip": "Histograma del MACD: mide el momentum (la fuerza del impulso) del precio. Positivo y creciente = impulso alcista ganando fuerza; negativo = impulso bajista. Ayuda a ver si la tendencia se acelera o se agota."},
        {"label": "Dist. 52W High", "value": high_value, "level": high_level, "sub": high_sub,
         "tooltip": "Distancia entre el precio actual y su máximo de las últimas 52 semanas. Cerca de 0% significa que cotiza en máximos anuales (señal de fuerza); muy negativo significa que está lejos de sus máximos (débil o en corrección)."},
    ])

    # ── Performance vs MAs y vs SPY ──
    st.markdown('<div class="section-title-bar">Performance Relativa</div>', unsafe_allow_html=True)

    rs = tech_report.raw_data.get("rs", {}) or {}
    col_mas, col_rs = st.columns(2)

    with col_mas:
        ma_items = []
        for n, color in [(20, "#6FA3E0"), (50, "#F0C878"), (150, "#E0703F"), (200, "#F1495F")]:
            pct = _safe_num(indicators.get(f"price_vs_sma{n}_pct"))   # nan-safe
            if pct is not None:
                bar_color = "#3DD68C" if pct > 0 else "#F1495F"
                ma_items.append((f"vs SMA {n}", pct, bar_color))
        if ma_items:
            fig_ma = build_metric_bars(ma_items, height=220, title="DISTANCIA A MOVING AVERAGES",
                                       corner_radius=0)
            _plotly(fig_ma, use_container_width=True, config={"displayModeBar": False},
                            key=f"chart_technical_mas_{analysis.ticker}")

    with col_rs:
        # nan-safe: análisis cacheados de producción (yfinance bloqueado) traen
        # rs en NaN. Si TODOS vienen vacíos, re-consultamos fresco (get_relative_
        # _strength funciona con datos en vivo). Nunca dibuja barras con NaN.
        rs_vals = {p: _safe_num(rs.get(p)) for p in ("rs_1m", "rs_3m", "rs_6m")}
        if all(v is None for v in rs_vals.values()):
            try:
                from data.market_data import get_relative_strength
                fresh_rs = get_relative_strength(analysis.ticker) or {}
                rs_vals = {p: _safe_num(fresh_rs.get(p)) for p in ("rs_1m", "rs_3m", "rs_6m")}
            except Exception:
                pass
        rs_items = []
        for period, label in [("rs_1m", "RS 1M"), ("rs_3m", "RS 3M"), ("rs_6m", "RS 6M")]:
            v = rs_vals.get(period)
            if v is not None:
                bar_color = "#3DD68C" if v > 0 else "#F1495F"
                rs_items.append((label, v, bar_color))
        if rs_items:
            fig_rs = build_metric_bars(rs_items, height=220, title="RELATIVE STRENGTH vs S&P 500",
                                       corner_radius=0)
            _plotly(fig_rs, use_container_width=True, config={"displayModeBar": False},
                            key=f"chart_technical_rs_{analysis.ticker}")

    # ── Señales alcistas / bajistas (cards) ──
    _render_pros_cons(tech_report,
                      pros_title="📈 Top 3 Señales Alcistas",
                      cons_title="📉 Top 3 Señales Bajistas")

    # ── Análisis textual ──
    _render_analysis_card(tech_report, title="Análisis Técnico Completo")


# ── Generic Agent Tab ─────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────
# CUSTOM TABS: Fundamentales, Futuro, Smart Money, Catalizadores,
#              Sentimiento, Riesgo. Cada uno es un mini-dashboard visual.
# ──────────────────────────────────────────────────────────────────────

def render_fundamentals(analysis: StockAnalysis):
    report = analysis.reports.get("fundamentals")
    if report is None:
        st.info("Análisis fundamental no disponible.")
        return

    _render_agent_header(report)
    km = report.key_metrics or {}
    sub = report.sub_scores or {}
    rd  = report.raw_data or {}

    # SIEMPRE fetcheamos datos frescos de yfinance — no dependemos del JSON guardado
    from data.market_data import get_company_info, get_financials, compute_quality_ratios
    info = get_company_info(analysis.ticker) or {}
    financials = get_financials(analysis.ticker) or {}
    ratios_fresh = compute_quality_ratios(info, financials) or {}

    # Fallback chain: yfinance fresco → raw_data del agente → key_metrics del agente
    ratios = {**(rd.get("ratios") or {}), **ratios_fresh}

    # ── KPI tiles: Crecimiento + Rentabilidad ─────────────────────
    st.markdown('<div class="section-title-bar">Crecimiento y Rentabilidad</div>',
                unsafe_allow_html=True)

    rev_growth = ratios.get("revenue_growth_yoy")
    if rev_growth is None: rev_growth = _safe_num(km.get("revenue_growth"))

    roic = ratios.get("roic")
    if roic is None: roic = _safe_num(km.get("roic"))

    fcf_yield = ratios.get("fcf_yield")
    if fcf_yield is None: fcf_yield = _safe_num(km.get("fcf_yield"))

    gross_marg = ratios.get("gross_margin")
    if gross_marg is None: gross_marg = _safe_num(km.get("gross_margin"))

    # ── Métricas que NO APLICAN a este tipo de negocio ───────────────────
    # Un banco no reporta coste de ventas: su margen bruto llega como 0.0 y
    # se pintaba "0.0%" en ROJO con el termómetro al mínimo (le pasaba a JPM,
    # UNTY, CIB…). Ahora se dice claramente que la métrica no aplica.
    try:
        from data.industry_labels import (metricas_no_aplicables,
                                          motivo_no_aplica, tipo_negocio)
        _tipo_neg = tipo_negocio(info.get("sector"), info.get("industry"))
        _no_aplican = metricas_no_aplicables(info.get("sector"), info.get("industry"))
    except Exception:
        _tipo_neg, _no_aplican = "general", frozenset()
        def motivo_no_aplica(m, t): return ""

    def _tile_na(metrica, valor, tile):
        """Marca el tile con "—" SOLO si el dato falta o es 0 exacto y además la
        métrica no tiene sentido en este negocio. Un valor real jamás se oculta.

        Se muestra "—" (igual que cualquier otro hueco) en vez de "No aplica":
        el porqué se explica en el tooltip, sin gritar en la tarjeta.

        Se probó a marcar por TIPO DE NEGOCIO (siempre que la métrica estuviera
        en la lista del tipo) y se descartó: ocultaba cifras REPORTADAS. Realty
        Income publica coste de ventas y activo/pasivo corriente en sus estados,
        así que su margen bruto (92,6 %) y su ratio corriente (2,06) son reales
        y deben verse, aunque sea un REIT. La etiqueta del sector no decide si
        un dato existe; el dato decide.

        OJO: esto es SOLO pintura. Que el score no castigue estos huecos lo
        garantiza `_na_neutro` en agents/code_engine.py (score_fundamentals y
        score_future), que los convierte en None para que el cálculo los excluya
        o use su valor neutro."""
        if metrica in _no_aplican and (valor is None or valor == 0):
            _m = motivo_no_aplica(metrica, _tipo_neg)
            tile = dict(tile)
            tile["value"] = "—"
            tile["color"] = "#5E6570"
            tile["meter"] = None
            if _m:
                tile["tooltip"] = _m
        return tile

    _render_metric_tiles([
        {"icon": "📈", "label": "Revenue Growth YoY",
         "value": f"{rev_growth:+.1f}%" if rev_growth is not None else "—",
         "color": "#3DD68C" if (rev_growth or 0) > 0 else "#F1495F",
         "meter": _meter_scale(rev_growth, -5, 30),
         "tooltip": "Crecimiento de ingresos año contra año. >15% es excelente."},
        {"icon": "🎯", "label": "ROIC",
         "value": f"{roic:.1f}%" if roic is not None else "—",
         "color": "#3DD68C" if (roic or 0) > 15 else "#E2B25C" if (roic or 0) > 8 else "#F1495F",
         "meter": _meter_scale(roic, 0, 25),
         "tooltip": "Return on Invested Capital. >15% indica negocio de alta calidad."},
        _tile_na("fcf_yield", fcf_yield, {
            "icon": "💵", "label": "FCF Yield",
            "value": f"{fcf_yield:.2f}%" if fcf_yield is not None else "—",
            "color": "#3DD68C" if (fcf_yield or 0) > 5 else "#E2B25C" if (fcf_yield or 0) > 2 else "#F1495F",
            "meter": _meter_scale(fcf_yield, 0, 8),
            "tooltip": "Free Cash Flow Yield. FCF / Market Cap. >5% es atractivo."}),
        _tile_na("gross_margin", gross_marg, {
            "icon": "📊", "label": "Gross Margin",
            "value": f"{gross_marg:.1f}%" if gross_marg is not None else "—",
            "color": "#3DD68C" if (gross_marg or 0) > 50 else "#E2B25C" if (gross_marg or 0) > 30 else "#F1495F",
            "meter": _meter_scale(gross_marg, 20, 70),
            "tooltip": "Margen bruto: indica pricing power. >50% es excepcional."}),
    ])

    # ── Valoración tiles ─────────────────────────────────────────
    st.markdown('<div class="section-title-bar">Múltiplos de Valoración</div>',
                unsafe_allow_html=True)

    # Todos los múltiplos vienen DIRECTOS de yfinance (siempre frescos)
    pe       = _safe_num(info.get("pe_ratio"))      or _safe_num(km.get("pe_ratio"))
    fwd_pe   = _safe_num(info.get("forward_pe"))
    ps       = _safe_num(info.get("ps_ratio"))
    ev_ebit  = _safe_num(info.get("ev_ebitda"))     or _safe_num(km.get("ev_ebitda"))
    de       = ratios.get("debt_to_equity")          or _safe_num(km.get("debt_equity"))
    op_marg  = ratios.get("operating_margin")

    _render_metric_tiles([
        {"icon": "💎", "label": "P/E Trailing",
         "value": f"{pe:.1f}" if pe else "—", "color": "#6FA3E0",
         "meter": _meter_scale(pe, 10, 45, invert=True),
         "tooltip": "Price/Earnings (trailing). Múltiplo precio/utilidad de los últimos 12 meses. Compara contra el sector y la historia de la empresa."},
        {"icon": "🔮", "label": "P/E Forward",
         "value": f"{fwd_pe:.1f}" if fwd_pe else "—", "color": "#6FA3E0",
         "meter": _meter_scale(fwd_pe, 8, 40, invert=True),
         "tooltip": "Price/Earnings forward. Basado en el EPS estimado del próximo año. Si está bastante por debajo del trailing, indica crecimiento esperado."},
        _tile_na("ev_ebitda", ev_ebit, {"icon": "🏛️", "label": "EV/EBITDA",
         "value": f"{ev_ebit:.1f}" if ev_ebit else "—", "color": "#9D8CE0",
         "meter": _meter_scale(ev_ebit, 8, 24, invert=True),
         "tooltip": "Enterprise Value / EBITDA. <12 suele ser atractivo, >20 ya es caro. Es más fiable que P/E para comparar empresas con diferente estructura de capital."}),
        {"icon": "🏦", "label": "Debt/Equity",
         "value": f"{de:.2f}" if de is not None else "—",
         "color": "#3DD68C" if (de or 0) < 0.5 else "#E2B25C" if (de or 0) < 1.5 else "#F1495F",
         "meter": _meter_scale(de, 0, 2.5, invert=True),
         "tooltip": "Apalancamiento financiero (deuda/equity). <0.5 = sano, >1.5 = riesgoso. Negocios con cash flow estable toleran más deuda."},
    ])

    # Tiles secundarios (Margen operativo + P/S + adicionales)
    extra_tiles = []
    if op_marg is not None:
        extra_tiles.append({
            "icon": "⚙️", "label": "Operating Margin",
            "value": f"{op_marg:.1f}%",
            "color": "#3DD68C" if op_marg > 20 else "#E2B25C" if op_marg > 10 else "#F1495F",
            "meter": _meter_scale(op_marg, 0, 32),
            "tooltip": "Margen operativo: % de cada dólar de ingresos que queda tras costos operativos. >20% indica negocio escalable y eficiente.",
        })
    if ps is not None:
        extra_tiles.append({
            "icon": "📏", "label": "P/S Ratio",
            "value": f"{ps:.2f}",
            "color": "#9D8CE0",
            "meter": _meter_scale(ps, 1, 12, invert=True),
            "tooltip": "Price/Sales. Útil para empresas no rentables aún (SaaS, biotech). <3 suele ser razonable, >10 implica altas expectativas de crecimiento.",
        })
    roe_val = ratios.get("roe")
    if roe_val is not None:
        extra_tiles.append({
            "icon": "💼", "label": "ROE",
            "value": f"{roe_val:.1f}%",
            "color": "#3DD68C" if roe_val > 15 else "#E2B25C" if roe_val > 8 else "#F1495F",
            "meter": _meter_scale(roe_val, 0, 30),
            "tooltip": "Return on Equity: rentabilidad sobre patrimonio. >15% es excelente, indica gestión eficiente del capital de accionistas.",
        })
    cr = ratios.get("current_ratio")
    if cr is None and "current_ratio" in _no_aplican:
        # El tile desaparecía entero en bancos; ahora explica por qué.
        extra_tiles.append({
            "icon": "💧", "label": "Current Ratio", "value": "—",
            "color": "#5E6570", "meter": None,
            "tooltip": motivo_no_aplica("current_ratio", _tipo_neg) or
                       "No es una métrica significativa en este tipo de negocio."})
    elif cr is not None:
        extra_tiles.append({
            "icon": "💧", "label": "Current Ratio",
            "value": f"{cr:.2f}",
            "color": "#3DD68C" if cr > 1.5 else "#E2B25C" if cr > 1 else "#F1495F",
            "meter": _meter_scale(cr, 0.7, 2.5),
            "tooltip": "Liquidez de corto plazo: activos corrientes / pasivos corrientes. >1.5 = sólido, <1 = posible estrés de caja.",
        })

    if extra_tiles:
        _render_metric_tiles(extra_tiles[:4])

    # ── Datos directos de mercado ────────────────────────────────
    st.markdown('<div class="section-title-bar">Datos de Mercado</div>',
                unsafe_allow_html=True)

    # Market Cap
    mktcap_raw = info.get("market_cap", 0) or 0
    if mktcap_raw >= 1e12:
        mktcap_str = f"${mktcap_raw/1e12:.2f}T"
    elif mktcap_raw >= 1e9:
        mktcap_str = f"${mktcap_raw/1e9:.1f}B"
    elif mktcap_raw > 0:
        mktcap_str = f"${mktcap_raw/1e6:.0f}M"
    else:
        mktcap_str = "—"

    # Profit Margin (directo de YF — decimal)
    pm_raw = info.get("profit_margin")
    pm_str = f"{pm_raw*100:.2f}%" if pm_raw is not None else "—"
    pm_color = ("#3DD68C" if (pm_raw or 0)*100 > 20
                else "#E2B25C" if (pm_raw or 0)*100 > 10
                else "#F1495F")

    # Revenue TTM (directo de YF)
    rev_ttm = info.get("revenue_ttm", 0) or 0
    if rev_ttm >= 1e12:
        rev_ttm_str = f"${rev_ttm/1e12:.2f}T"
    elif rev_ttm >= 1e9:
        rev_ttm_str = f"${rev_ttm/1e9:.1f}B"
    elif rev_ttm > 0:
        rev_ttm_str = f"${rev_ttm/1e6:.0f}M"
    else:
        rev_ttm_str = "—"

    # Beta (directo de YF)
    beta_raw = info.get("beta")
    beta_str = f"{beta_raw:.2f}" if isinstance(beta_raw, (int, float)) else "—"
    beta_color = ("#3DD68C" if isinstance(beta_raw, (int, float)) and beta_raw < 1
                  else "#E2B25C" if isinstance(beta_raw, (int, float)) and beta_raw <= 1.5
                  else "#F1495F")

    # Dividendo anual en $/acción — SOLO INFORMATIVO (no entra en ningún score).
    # Tres estados resueltos en la capa de datos: paga / no_paga / desconocido.
    _div_rate = info.get("dividend_rate")
    _div_status = info.get("dividend_status")
    _div_fuente = info.get("dividend_fuente") or ""
    if _div_status == "paga" and isinstance(_div_rate, (int, float)) and _div_rate > 0:
        div_str = f"${_div_rate:,.2f}"
        div_color = "#3DD68C"
    elif _div_status == "no_paga":
        div_str, div_color = "No", "#5E6570"
    else:
        div_str, div_color = "—", "#5E6570"
    div_tip = ("Dividendo anual en dólares por acción. Es solo informativo: "
               "NO entra en ningún score ni en el análisis.")
    if _div_status == "paga" and _div_fuente.startswith("tradingview"):
        # OJO: no prometer "±5%" aquí. Esta vía deriva la cifra del dividendo
        # REALMENTE PAGADO en los últimos 12 meses, mientras que la fuente
        # principal da la tasa anualizada vigente. En emisores de EE.UU. ambas
        # coinciden (medido ≤4% de diferencia), pero en extranjeros —pagos
        # semestrales, variables o extraordinarios— divergen de verdad: CIB
        # 7.19% vs 2.76%, SAN 1.38% vs 1.97%. Se avisa en vez de fingir precisión.
        div_tip += (" Calculado sobre lo repartido en los últimos 12 meses; "
                    "puede diferir de la tasa anualizada vigente.")
    elif _div_status == "paga" and "yield" in _div_fuente:
        div_tip += " Estimado a partir del yield y el precio actual (±5%)."
    elif _div_status == "no_paga":
        div_tip += " Verificado en todas las fuentes: esta empresa no reparte dividendo."

    # Container keyed: esta fila tiene 5 tiles (una más que el resto) y en
    # pantallas intermedias (~900px, con el sidebar abierto) las columnas se
    # quedaban en 74px. El CSS scoped la reordena en 3+2 solo en ese tramo.
    with st.container(key="tiles_mercado"):
        _render_metric_tiles([
            {"icon": "💎", "label": "Market Cap",
             "value": mktcap_str, "color": "#E2B25C",
             "tooltip": "Capitalización de mercado total (precio × acciones en circulación)."},
            {"icon": "📊", "label": "Profit Margin",
             "value": pm_str, "color": pm_color,
             "tooltip": "Margen neto (Profit Margin). % de cada dólar de ingresos que queda como ganancia neta."},
            {"icon": "💰", "label": "Revenue TTM",
             "value": rev_ttm_str, "color": "#6FA3E0",
             "tooltip": "Ingresos totales de los últimos 12 meses (Trailing Twelve Months)."},
            {"icon": "📈", "label": "Beta",
             "value": beta_str, "color": beta_color,
             "tooltip": "Beta vs S&P 500. <1 = menos volátil que el índice, >1 = más volátil, 1 = correlación perfecta."},
            {"icon": "💵", "label": "Dividendo",
             "value": div_str, "color": div_color, "tooltip": div_tip},
        ])

    # ── Desglose de sub-scores ───────────────────────────────────
    st.markdown('<div class="section-title-bar">Pilares Fundamentales</div>',
                unsafe_allow_html=True)

    # Compatibilidad con análisis ANTIGUOS: si el sub_scores trae la clave "value"
    # es que se guardó con el snowflake mezclado, y entonces quality/growth quedaron
    # en escala 0-20 (no 0-25). Los reescalamos (×1.25) para que las barras lleguen
    # bien a 0-100. Los análisis nuevos no traen "value" y se usan tal cual.
    _legacy_scale = "value" in (sub or {})

    def _pillar_score(key, raw):
        if raw is None:
            return None
        if _legacy_scale and key in ("quality", "growth"):
            raw = float(raw) / 20 * 25  # 0-20 (snowflake viejo) → 0-25 real
        return float(raw) * 4  # escalar /25 → /100

    sub_items = []
    pillars = [
        ("Calidad",          "quality",          "#E2B25C"),
        ("Crecimiento",      "growth",           "#3DD68C"),
        ("Valoración",       "valuation",        "#6FA3E0"),
        ("Solidez Financiera", "financial_health", "#9D8CE0"),
    ]
    for label, key, color in pillars:
        val = _pillar_score(key, sub.get(key))
        if val is not None:
            sub_items.append((label, val, color))

    if sub_items:
        fig = build_metric_bars(sub_items, height=240,
                                title="SUB-SCORES (0-100)", x_format="num",
                                x_zero_line=False, color_by_score=True)
        _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_fund_pillars_{analysis.ticker}")

    # ── Pros / Cons ──
    _render_pros_cons(report)

    # ── EL HALLAZGO: la conclusión más importante en lenguaje simple ──
    _render_insight_card("El Hallazgo", rd.get("key_insight", ""),
                         color="#E2B25C", icon="🔎")

    # ── Insights: DCF Thesis + Earnings Quality ──
    _render_insight_card("Tesis DCF", rd.get("dcf_thesis", ""),
                         color="#3DD68C", icon="💎")
    _render_insight_card("Calidad de Earnings", rd.get("earnings_quality", ""),
                         color="#6FA3E0", icon="✓")

    # ── Análisis completo ──
    _render_analysis_card(report, title="Análisis Fundamental Completo")


def render_future(analysis: StockAnalysis):
    report = analysis.reports.get("future")
    if report is None:
        st.info("Análisis de viabilidad futura no disponible.")
        return

    _render_agent_header(report)
    km = report.key_metrics or {}
    sub = report.sub_scores or {}
    rd  = report.raw_data or {}

    # ── Status pills: 4 dimensiones críticas del futuro ──
    st.markdown('<div class="section-title-bar">Diagnóstico del Negocio Futuro</div>',
                unsafe_allow_html=True)

    moat_str = (km.get("moat_strength") or "").lower()
    moat_level = "good" if "amplio" in moat_str else "warn" if "estrecho" in moat_str else "bad"

    disr = (km.get("disruption_risk") or "").lower()
    disr_level = "good" if "bajo" in disr else "warn" if "medio" in disr else "bad"

    tam = (km.get("tam_growth") or "").lower()
    tam_level = "good" if "acelerada" in tam else "neutral" if "expansión" in tam else "warn"

    mgmt = (km.get("management_quality") or "").lower()
    mgmt_level = "good" if "excelente" in mgmt else "neutral" if "bueno" in mgmt else "warn"

    _render_status_pills([
        {"label": "Moat Defensivo",
         "value": _clean_tile_value(km.get("moat_strength"), max_len=14),
         "level": moat_level,
         "sub": _clean_tile_value(km.get("moat_type"), max_len=20),
         "tooltip": "Fuerza de la ventaja competitiva (moat) que protege al negocio de la competencia: marca, costes bajos, efecto red, patentes, costes de cambio. Amplio = muy difícil de atacar y defiende márgenes durante años; estrecho o ninguno = fácil de erosionar."},
        {"label": "Riesgo Disrupción",
         "value": _clean_tile_value(km.get("disruption_risk"), max_len=14),
         "level": disr_level, "sub": "IA / tecnología",
         "tooltip": "Probabilidad de que una nueva tecnología o modelo (IA, plataformas, nuevos entrantes) haga obsoleto el negocio. Bajo = modelo resistente al cambio; alto = la tesis a largo plazo corre peligro si no se adapta."},
        {"label": "Crecimiento TAM",
         "value": _clean_tile_value(km.get("tam_growth"), max_len=18),
         "level": tam_level, "sub": "Mercado direccionable",
         "tooltip": "Evolución del mercado total direccionable (TAM), el tamaño de la oportunidad que la empresa puede capturar. En expansión/acelerada = hay margen para crecer durante años; estancado = el crecimiento futuro será más difícil."},
        {"label": "Calidad Gerencia",
         "value": _clean_tile_value(km.get("management_quality"), max_len=14),
         "level": mgmt_level, "sub": "Asignación de capital",
         "tooltip": "Calidad del equipo directivo, sobre todo en asignación de capital: cómo reinvierten beneficios, recompras, dividendos y adquisiciones. Excelente = crean valor por acción con el tiempo; deficiente = destruyen valor aunque el negocio sea bueno."},
    ])

    # ── Bar chart: 4 pilares del futuro ──
    st.markdown('<div class="section-title-bar">Pilares de Viabilidad Futura</div>',
                unsafe_allow_html=True)

    sub_items = []
    pillars = [
        ("Calidad del Moat",     sub.get("moat_quality"),                 "#E2B25C"),
        ("Runway de Crecimiento", sub.get("growth_runway"),               "#3DD68C"),
        ("Resistencia Disrupción", sub.get("disruption_resilience"),      "#6FA3E0"),
        ("Capital Allocation",   sub.get("management_capital_allocation"), "#9D8CE0"),
    ]
    for label, val, color in pillars:
        if val is not None:
            sub_items.append((label, float(val) * 4, color))

    if sub_items:
        fig = build_metric_bars(sub_items, height=240,
                                title="SUB-SCORES (0-100)", x_format="num",
                                x_zero_line=False, color_by_score=True)
        _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_future_pillars_{analysis.ticker}")

    # ── Pros / Cons ──
    _render_pros_cons(report,
                      pros_title="🚀 Top 3 Ventajas Futuras",
                      cons_title="⚠️ Top 3 Riesgos Estructurales")

    # ── Insight: Future Thesis ──
    _render_insight_card("Tesis a 5 años", rd.get("future_thesis", ""),
                         color="#E2B25C", icon="🔭")

    # Key risks específicos (lista)
    key_risks = rd.get("key_risks") or []
    if key_risks and isinstance(key_risks, list):
        st.markdown('<div class="section-title-bar">Riesgos Críticos Identificados</div>',
                    unsafe_allow_html=True)
        for r in key_risks:
            st.markdown(f'<div class="risk-item">{r}</div>', unsafe_allow_html=True)

    _render_analysis_card(report, title="Análisis de Viabilidad Futura")


def render_institutional(analysis: StockAnalysis):
    report = analysis.reports.get("institutional")
    if report is None:
        st.info("Análisis de flujo institucional no disponible.")
        return

    _render_agent_header(report)
    km = report.key_metrics or {}
    rd = report.raw_data or {}
    holders_raw = rd.get("holders_raw", {}) or {}

    # ── Auto-reparación: si el análisis guardado trae la Propiedad Institucional
    # o el top de tenedores vacíos (p. ej. análisis viejos guardados antes de los
    # arreglos, o un hueco puntual de datos), re-consultamos holders frescos
    # (cacheados 12h) y RELLENAMOS solo lo que falte. Mismo patrón que
    # render_catalysts usa con earnings; nunca sobrescribe datos buenos. ────────
    def _tile_empty(v):
        return (not v) or str(v).strip() in ("", "N/A", "N/D", "—", "None")

    _fresh_km = {}
    # También se refresca cuando falta el VEREDICTO sobre los insiders: los
    # análisis guardados antes de esta versión no lo traen, y sin él un ADR
    # exento mostraría "fuente no disponible" en vez de explicar la exención.
    # Y AHORA además cuando ese veredicto se emitió SIN haber preguntado a la
    # SEC: esos análisis afirman que no hay datos de directivos cuando sí los
    # hay (CIB tiene 59 Form 4). Sin esta condición seguirían mintiendo hasta
    # que caducara la caché de 12 h.
    _sin_sec = ("sec" not in (holders_raw.get("insiders_fuentes") or [])
                and not (holders_raw.get("insider_transactions") or []))
    if (_tile_empty(km.get("institutional_ownership"))
            or not holders_raw.get("top_institutions")
            or "insiders_disponibles" not in holders_raw
            or _sin_sec):
        try:
            from data.market_data import get_holders_data, get_company_info
            from agents.code_engine import score_institutional
            _fresh_h = get_holders_data(analysis.ticker) or {}
            if not holders_raw.get("top_institutions") and _fresh_h.get("top_institutions"):
                holders_raw = _fresh_h
            elif _sin_sec and _fresh_h.get("insider_transactions"):
                # La SEC rescató operaciones que el análisis guardado no tenía:
                # entran junto con su veredicto y sus contadores.
                for _k in ("insider_transactions", "recent_insider_buys",
                           "recent_insider_sells", "insiders_disponibles",
                           "insiders_motivo", "insiders_pais", "insiders_fuentes"):
                    if _k in _fresh_h:
                        holders_raw = {**holders_raw, _k: _fresh_h[_k]}
            elif ("insiders_disponibles" not in holders_raw or _sin_sec) and _fresh_h:
                # Solo se COMPLETA el veredicto; los datos buenos ya guardados
                # (top de tenedores, %) se conservan intactos.
                for _k in ("insiders_disponibles", "insiders_motivo",
                           "insiders_pais", "insiders_fuentes"):
                    if _k in _fresh_h:
                        holders_raw = {**holders_raw, _k: _fresh_h[_k]}
            _fresh_score = score_institutional(
                _fresh_h, get_company_info(analysis.ticker) or {}) or {}
            if _fresh_h.get("institutional_ownership_pct") is not None:
                _fresh_km = _fresh_score.get("key_metrics", {}) or {}
            # Los TEXTOS guardados también quedan obsoletos: los análisis
            # anteriores dejaron escrito "los emisores extranjeros están EXENTOS
            # de declarar…" tanto en el insight como en la narrativa larga, y
            # esas frases se siguen pintando aunque los datos ya estén
            # corregidos. Si esta vez SÍ hay operaciones, ambos se reescriben.
            if _sin_sec and _fresh_h.get("insider_transactions"):
                if _fresh_score.get("key_insight"):
                    rd = {**rd, "key_insight": _fresh_score["key_insight"]}
                if _fresh_score.get("analysis"):
                    import copy as _copy
                    report = _copy.copy(report)
                    report.analysis = _fresh_score["analysis"]
        except Exception:
            _fresh_km = {}

    def _pick(key, default=""):
        v = km.get(key)
        if _tile_empty(v) and not _tile_empty(_fresh_km.get(key)):
            return _fresh_km.get(key)
        return v if not _tile_empty(v) else default

    # ── KPI tiles del Smart Money ──
    st.markdown('<div class="section-title-bar">Indicadores Smart Money</div>',
                unsafe_allow_html=True)

    inst_raw = _pick("institutional_ownership", "")
    short_raw = _pick("short_interest", "")
    insider_raw = _pick("insider_buying_signal", "neutral")
    squeeze_raw = _pick("squeeze_potential", "bajo")

    insider_level = "good" if "alcista" in insider_raw.lower() else "bad" if "bajista" in insider_raw.lower() else "neutral"
    squeeze_level = "good" if "alto" in squeeze_raw.lower() else "neutral" if "medio" in squeeze_raw.lower() else "warn"

    # Niveles calculados desde el DATO real (antes venían fijos):
    # · Propiedad institucional: sana entre 40-85%; >90% saturada; <40% baja.
    inst_num = _safe_num(_extract_percent(inst_raw))
    if inst_num is None:
        inst_level, inst_meter = "neutral", None
    elif 40 <= inst_num <= 85:
        inst_level, inst_meter = "good", _meter_scale(inst_num, 20, 78)
    elif inst_num > 85:
        inst_level, inst_meter = "warn", 55.0
    else:
        inst_level, inst_meter = "neutral", _meter_scale(inst_num, 0, 80)
    # · Short interest: menos apuestas en contra = mejor (escala continua).
    short_num = _safe_num(_extract_percent(short_raw))
    short_level = ("neutral" if short_num is None else
                   "good" if short_num < 3 else
                   "neutral" if short_num < 8 else
                   "warn" if short_num < 15 else "bad")
    short_meter = _meter_scale(short_num, 0, 20, invert=True)

    # ── Señal de insiders: distinguir "no lo sabemos" de "está equilibrado" ──
    # Cuando el regulador no tiene ninguna operación declarada del emisor, un
    # "NEUTRAL" gris era indistinguible de "hay datos y están parejos".
    _ins_disp = holders_raw.get("insiders_disponibles")
    _ins_val = _clean_tile_value(insider_raw, max_len=12)
    _ins_level, _ins_sub = insider_level, "Compras vs ventas"
    _ins_tip = ("Qué están haciendo los directivos y personas con información privilegiada "
                "(insiders) con sus propias acciones. Compras netas = confianza en el futuro "
                "(señal alcista); ventas fuertes = posible cautela. Comprar es más "
                "significativo que vender.")
    if _ins_disp is False:
        _ins_val, _ins_level = "Sin registro", "neutral"
        _ins_sub = "Nada declarado al regulador"
        _ins_tip = ("Se consultó directamente el registro del organismo regulador "
                    "estadounidense y no consta ninguna operación declarada por los "
                    "directivos de esta empresa. No significa que no haya actividad "
                    "interna: puede que este emisor no esté obligado a declararla. "
                    "Por eso no penaliza la calificación.")
    elif _ins_disp is None and not (holders_raw.get("insider_transactions") or []):
        _ins_val, _ins_level = "N/D", "neutral"
        _ins_sub = "Fuente no disponible"
        _ins_tip = ("El registro de operaciones de directivos no se ha podido recuperar en "
                    "este momento. Se reintentará automáticamente en el próximo análisis.")

    _render_status_pills([
        {"label": "Propiedad Institucional",
         "value": _extract_percent(inst_raw),
         "level": inst_level, "meter": inst_meter, "sub": "% del capital en fondos",
         "tooltip": "Porcentaje de las acciones en manos de grandes fondos e instituciones (el 'dinero inteligente'). Una propiedad alta indica respaldo profesional; muy baja o en caída puede indicar falta de interés institucional."},
        {"label": "Señal de Insiders",
         "value": _ins_val, "level": _ins_level, "sub": _ins_sub,
         "tooltip": _ins_tip},
        {"label": "Short Interest",
         "value": _extract_percent(short_raw),
         "level": short_level, "meter": short_meter, "sub": "Apuestas a la baja",
         "tooltip": "Porcentaje de acciones vendidas en corto: cuánto capital apuesta a que el precio baje. Alto = mucho escepticismo (pero también combustible para un rebote si suben). Bajo = pocas apuestas bajistas."},
        {"label": "Potencial Squeeze",
         "value": _clean_tile_value(squeeze_raw, max_len=12),
         "level": squeeze_level, "sub": "Rebote por cierre de cortos",
         "tooltip": "Riesgo de un 'short squeeze': si la acción sube y muchos cortos se ven obligados a recomprar para cerrar sus posiciones, el precio se dispara al alza. Alto = mayor probabilidad de un rebote violento si aparece una buena noticia."},
    ])

    # ── Top holders bar chart ──
    top_inst = holders_raw.get("top_institutions") or []
    if not top_inst:
        st.caption("El desglose de los mayores tenedores no está disponible para este valor.")
    if top_inst:
        fig = build_holders_bars(top_inst)
        _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_inst_holders_{analysis.ticker}")

    # ── Actividad reciente de directivos (insiders) ──
    # El título se pinta SIEMPRE: antes, si no había datos, desaparecían el
    # título, el contador y la tabla sin decir una palabra al usuario.
    insider_txns = holders_raw.get("insider_transactions") or []
    if not insider_txns:
        st.markdown('<div class="section-title-bar">Actividad Reciente de Directivos (Insiders)</div>',
                    unsafe_allow_html=True)
        if _ins_disp is False:
            _render_insight_card(
                "Por qué no hay datos de directivos",
                "Se consultó <strong>directamente el registro del organismo regulador "
                "estadounidense</strong>, que es el origen de este dato, y no consta ninguna "
                "operación declarada por los directivos de esta empresa. No significa que no "
                "haya actividad interna: puede que este emisor no esté obligado a declararla. "
                "Por eso <strong>no penaliza la calificación</strong>. En este valor la lectura "
                "del dinero inteligente se apoya en la propiedad institucional y en las "
                "posiciones en corto, que sí son públicas.",
                color="#9D8CE0", icon="🌐")
        else:
            _render_insight_card(
                "Datos de directivos no disponibles",
                "El registro de operaciones de los directivos no se ha podido recuperar en este "
                "momento. Se reintentará automáticamente en el próximo análisis; mientras tanto "
                "esta pieza queda fuera de la lectura y no penaliza la calificación.",
                color="#8D949E", icon="⏳")
    if insider_txns:
        n_buys = holders_raw.get("recent_insider_buys", 0) or 0
        n_sells = holders_raw.get("recent_insider_sells", 0) or 0
        st.markdown('<div class="section-title-bar">Actividad Reciente de Directivos (Insiders)</div>',
                    unsafe_allow_html=True)
        st.markdown(
            f"<div style='margin:-4px 0 10px;color:#8D949E;font-size:0.85rem;'>"
            f"En las últimas operaciones registradas: "
            f"<span style='color:#3DD68C;font-weight:700;'>{n_buys} compras</span> · "
            f"<span style='color:#F1495F;font-weight:700;'>{n_sells} ventas</span>. "
            f"Las compras de directivos con su propio dinero suelen ser la señal más valiosa.</div>",
            unsafe_allow_html=True)

        def _fmt_usd(v):
            v = abs(float(v or 0))
            if v >= 1e9: return f"${v/1e9:.1f}B"
            if v >= 1e6: return f"${v/1e6:.1f}M"
            if v >= 1e3: return f"${v/1e3:.0f}K"
            return f"${v:.0f}" if v else "—"

        # Priorizar operaciones con dinero real (las más grandes primero)
        con_valor = [t for t in insider_txns if (t.get("value") or 0) > 0]
        muestra = sorted(con_valor, key=lambda t: t.get("value") or 0, reverse=True)[:6] or insider_txns[:6]

        tipo_color = {"compra": "#3DD68C", "venta": "#F1495F",
                      "concesión": "#6FA3E0", "donación": "#9D8CE0", "otra": "#5E6570"}
        rows = ""
        for t in muestra:
            c = tipo_color.get(t.get("type", "otra"), "#5E6570")
            nombre = (t.get("insider") or "—").title()
            rows += (
                f"<tr>"
                f"<td style='padding:7px 10px;color:#C9CDD3;font-size:0.82rem;'>{t.get('date','')}</td>"
                f"<td style='padding:7px 10px;color:#F2F3F5;font-size:0.82rem;font-weight:600;'>{nombre}</td>"
                f"<td style='padding:7px 10px;color:#8D949E;font-size:0.78rem;'>{t.get('position','')}</td>"
                f"<td style='padding:7px 10px;'><span style='color:{c};font-weight:700;font-size:0.78rem;text-transform:uppercase;'>{t.get('type','')}</span></td>"
                f"<td style='padding:7px 10px;text-align:right;color:#C9CDD3;font-size:0.82rem;font-family:JetBrains Mono,monospace;'>{_fmt_usd(t.get('value'))}</td>"
                f"</tr>"
            )
        _th = ("padding:8px 10px;text-align:left;color:#5E6570;font-size:0.70rem;"
               "text-transform:uppercase;letter-spacing:0.05em;")
        st.markdown(
            f"<div style='border:1px solid rgba(255,255,255,0.07);border-radius:12px;overflow:hidden;margin-bottom:14px;'>"
            f"<table style='width:100%;border-collapse:collapse;'>"
            f"<thead><tr style='background:rgba(255,255,255,0.03);'>"
            f"<th style='{_th}'>Fecha</th><th style='{_th}'>Directivo</th>"
            f"<th style='{_th}'>Cargo</th><th style='{_th}'>Operación</th>"
            f"<th style='{_th}text-align:right;'>Monto</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>",
            unsafe_allow_html=True)

    # ── Smart Money Signal pill grande ──
    smart_raw = _pick("smart_money_signal", "neutral")
    smart_display = _translate_status(smart_raw).upper()
    signal_color = "#3DD68C" if "accumul" in smart_raw.lower() else "#F1495F" if "distribut" in smart_raw.lower() else "#6FA3E0"
    st.markdown(f"""
    <div class="insight-card" style="border-left-color:{signal_color};background:linear-gradient(135deg,{signal_color}11,{signal_color}03);">
        <div class="insight-card-header">
            <span class="insight-card-icon">📡</span>
            <span class="insight-card-title" style="color:{signal_color};">Señal Agregada del Smart Money</span>
        </div>
        <div class="insight-card-body" style="font-size:1.15rem;font-weight:700;color:{signal_color};font-family:'JetBrains Mono',monospace;">{smart_display}</div>
    </div>
    """, unsafe_allow_html=True)

    # ── Pros / Cons ──
    _render_pros_cons(report)

    # ── Key Insight ──
    _render_insight_card("Insight Clave del Flujo", rd.get("key_insight", ""),
                         color="#9D8CE0", icon="🎯")

    _render_analysis_card(report, title="Análisis Completo de Flujo")


def render_catalysts(analysis: StockAnalysis):
    report = analysis.reports.get("catalysts")
    if report is None:
        st.info("Análisis de catalizadores no disponible.")
        return

    _render_agent_header(report)
    km = report.key_metrics or {}
    rd = report.raw_data or {}

    # ── Re-fetch earnings data fresco para tener days_to_next_earnings ──
    from data.market_data import get_earnings_data
    earnings = get_earnings_data(analysis.ticker) or {}

    # ── KPI tiles ──
    st.markdown('<div class="section-title-bar">Catalizadores en el Horizonte</div>',
                unsafe_allow_html=True)

    next_earn = earnings.get("next_earnings", "") or km.get("next_earnings", "")
    days_to = earnings.get("days_to_next_earnings")
    if days_to is not None:
        days_str = f"{days_to}d"
        next_tooltip = (f"Próximo reporte: {next_earn}. "
                        f"Earnings inminentes (<7d) son catalizador de alta volatilidad.")
        next_color = "#F1495F" if days_to < 7 else "#E2B25C" if days_to < 30 else "#6FA3E0"
    else:
        days_str = "N/D"
        next_tooltip = ("Fecha del próximo reporte de resultados no disponible en este momento "
                        "(la fuente de datos puede estar temporalmente fuera de servicio). "
                        "Intenta reanalizar en unos minutos.")
        next_color = "#5E6570"

    def _looks_empty(v):
        """Detecta si un valor de tile está efectivamente vacío después de limpieza."""
        if v is None:
            return True
        s = str(v).strip()
        return s in ("", "—", "N/A", "N/D", "None", "null")

    beat_count = earnings.get("beat_count")
    eh = earnings.get("earnings_history", []) or []
    if eh and beat_count is not None:
        beat_rate_str = f"{beat_count}/{len(eh)}"
        beat_tooltip = "Trimestres en los que la empresa superó el consenso de EPS en los últimos 8 trimestres."
        beat_color = "#3DD68C"
    else:
        raw = km.get("earnings_beat_rate", "")
        cleaned = _clean_tile_value(raw, max_len=10) if raw else None
        if _looks_empty(cleaned):
            beat_rate_str = "N/D"
            beat_tooltip = ("Historial de beats no disponible — requiere datos detallados de earnings "
                            "que la fuente puede no exponer para todos los tickers.")
            beat_color = "#5E6570"
        else:
            beat_rate_str = cleaned
            beat_tooltip = "Beat rate estimado por el agente de catalizadores."
            beat_color = "#E2B25C"

    avg_surp = earnings.get("avg_surprise")
    if isinstance(avg_surp, (int, float)):
        avg_surp_str = f"{avg_surp:+.1f}%"
        avg_surp_tooltip = ("Promedio de % sorpresa en EPS sobre el consenso. "
                            "Positivo y sostenido indica momentum fundamental.")
        avg_surp_color = ("#3DD68C" if avg_surp > 5
                          else "#E2B25C" if avg_surp > 0
                          else "#F1495F")
    else:
        raw = km.get("avg_earnings_surprise", "")
        extracted = _extract_percent(raw) if raw else None
        if _looks_empty(extracted):
            avg_surp_str = "N/D"
            avg_surp_tooltip = ("Sorpresa promedio no disponible — requiere historial detallado "
                                "de earnings que la fuente puede no exponer.")
            avg_surp_color = "#5E6570"
        else:
            avg_surp_str = extracted
            avg_surp_tooltip = "Sorpresa promedio estimada por el agente de catalizadores."
            avg_surp_color = "#E2B25C"

    sentiment_raw = km.get("analyst_sentiment_trend") or "stable"
    sentiment_display = _clean_tile_value(sentiment_raw, max_len=12)
    sent_level_str = sentiment_raw.lower()
    sent_color = ("#3DD68C" if "improv" in sent_level_str else
                  "#F1495F" if "deterior" in sent_level_str else "#E2B25C")

    _render_metric_tiles([
        {"icon": "📅", "label": "Próximo Reporte",
         "value": days_str, "color": next_color, "tooltip": next_tooltip},
        {"icon": "🎯", "label": "Tasa de Aciertos",
         "value": beat_rate_str, "color": beat_color, "tooltip": beat_tooltip},
        {"icon": "🚀", "label": "Sorpresa Promedio",
         "value": avg_surp_str, "color": avg_surp_color, "tooltip": avg_surp_tooltip},
        {"icon": "📊", "label": "Tendencia Analistas",
         "value": sentiment_display, "color": sent_color,
         "tooltip": "Dirección de las revisiones de estimaciones y ratings del consenso (factor de momentum potente)."},
    ])

    # ── Agenda de próximos eventos (más allá de los resultados) ──
    # Se recalcula EN VIVO al renderizar (igual que el resto del dashboard) y
    # si algo fallara cae al que se guardó con el análisis. Si no hay eventos,
    # el bloque simplemente no se pinta y la sección queda como antes.
    eventos = []
    try:
        from data.corporate_events import get_upcoming_catalysts
        from data.market_data import get_company_info, get_news
        eventos = get_upcoming_catalysts(analysis.ticker,
                                         get_company_info(analysis.ticker) or {},
                                         earnings,
                                         get_news(analysis.ticker, max_items=15))
    except Exception:
        eventos = []
    if not eventos:
        try:
            eventos = [e for e in (rd.get("events") or []) if isinstance(e, dict)]
        except Exception:
            eventos = []

    if eventos:
        st.markdown('<div class="section-title-bar">Agenda de Próximos Eventos</div>',
                    unsafe_allow_html=True)
        _tipos_es = {"resultados": "Resultados", "producto": "Producto",
                     "negocio": "Negocio", "dividendo": "Dividendo",
                     "corporativo": "Corporativo"}
        filas = []
        for ev in eventos[:6]:
            try:
                tipo = str(ev.get("tipo") or "corporativo")
                try:
                    dias = None if ev.get("dias") is None else int(ev["dias"])
                except (TypeError, ValueError):
                    dias = None
                fecha = _no_latex(str(ev.get("fecha_txt") or "—"))
                if dias is None:
                    fecha_html = '<div class="cat-ev-date">Ahora</div>'
                    en_html = '<div class="cat-ev-in">En titulares</div>'
                else:
                    # 'sep 2026' → 'sep' · pero se conserva el año cuando NO es
                    # el actual, para que un evento de 2027 no parezca de este año.
                    partes_f = fecha.split(" ")
                    from datetime import date as _date
                    if len(partes_f) > 1 and partes_f[-1] == str(_date.today().year):
                        etiqueta_f = " ".join(partes_f[:-1]) or fecha
                    else:
                        etiqueta_f = fecha
                    fecha_html = f'<div class="cat-ev-date">{etiqueta_f}</div>'
                    en_html = (f'<div class="cat-ev-in">'
                               f'{"hoy" if dias <= 0 else "mañana" if dias == 1 else f"en {dias} d"}'
                               f'</div>')
                confirmado = bool(ev.get("confirmado"))
                tag_conf = ("Confirmado" if confirmado else
                            "Aprox." if dias is not None else "Sin fecha")
                fuente = _no_latex(str(ev.get("fuente") or ""))
                filas.append(f"""
        <div class="cat-ev cat-ev--{tipo}">
            <div class="cat-ev-when">{fecha_html}{en_html}</div>
            <div class="cat-ev-dot"></div>
            <div class="cat-ev-body">
                <div class="cat-ev-title">{_no_latex(str(ev.get("titulo") or "Evento"))}</div>
                <div class="cat-ev-meta">
                    <span class="cat-ev-tag cat-ev-tag--tipo">{_tipos_es.get(tipo, "Evento")}</span>
                    <span class="cat-ev-tag">{tag_conf}</span>
                    <span class="cat-ev-src">{fuente}</span>
                </div>
            </div>
        </div>""")
            except Exception:
                continue
        if filas:
            st.markdown(f'<div class="cat-agenda">{"".join(filas)}</div>',
                        unsafe_allow_html=True)
            st.caption("Los eventos recurrentes (keynotes, conferencias) usan la fecha habitual "
                       "de cada año y se marcan como aproximados hasta que la compañía los confirma.")

    # ── Historial de Earnings Surprises (bar chart) ──
    if eh and len(eh) >= 2:
        st.markdown('<div class="section-title-bar">Track Record de Earnings</div>',
                    unsafe_allow_html=True)
        fig = build_earnings_history_chart(eh)
        _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_catalysts_earn_{analysis.ticker}")

    # ── Top Catalyst destacado ──
    top_cat = rd.get("top_catalyst", "")
    if top_cat:
        st.markdown(f"""
        <div class="alpha-opportunity-card">
            <div class="alpha-opportunity-header">
                <span class="alpha-opportunity-icon">⚡</span>
                <span class="alpha-opportunity-title">Catalizador #1 — Potencial Mayor</span>
            </div>
            <div class="alpha-opportunity-body">{_no_latex(top_cat)}</div>
        </div>
        """, unsafe_allow_html=True)

    # ── Próximo evento clave ──
    key_event = km.get("key_upcoming_event", "")
    if key_event and key_event not in ("—", ""):
        _render_insight_card("Próximo Evento Crítico", str(key_event),
                             color="#6FA3E0", icon="🔔")

    # ── Pros / Cons ──
    _render_pros_cons(report,
                      pros_title="✅ Top 3 Catalizadores Alcistas",
                      cons_title="⚠️ Top 3 Riesgos de Evento")

    _render_analysis_card(report, title="Análisis de Catalizadores")


def render_macro(analysis: StockAnalysis):
    report = analysis.reports.get("macro")
    if report is None:
        st.info("Análisis macro no disponible.")
        return

    _render_agent_header(report)
    km = report.key_metrics or {}
    rd = report.raw_data or {}

    # ── Status pills del entorno macro ──
    st.markdown('<div class="section-title-bar">Diagnóstico Macro</div>',
                unsafe_allow_html=True)

    env_raw = km.get("market_environment") or "neutral"
    env_level = "good" if "risk-on" in env_raw.lower() else "bad" if "risk-off" in env_raw.lower() else "neutral"

    sec_raw = km.get("sector_momentum") or "neutral"
    sec_level = "good" if "strong" in sec_raw.lower() else "bad" if "weak" in sec_raw.lower() else "neutral"

    yc_raw = km.get("yield_curve") or "normal"
    yc_level = "good" if "normal" in yc_raw.lower() else "warn" if "flat" in yc_raw.lower() else "bad"

    vix_raw = km.get("vix_level") or "low <20"
    vix_level = "good" if "<20" in vix_raw else "warn" if "20-30" in vix_raw else "bad"

    _render_status_pills([
        {"label": "Entorno Mercado",
         "value": _clean_tile_value(env_raw, max_len=12),
         "level": env_level, "sub": "Risk On / Off",
         "tooltip": "Apetito de riesgo del mercado en su conjunto. 'Risk On' = los inversores buscan activos de riesgo (bueno para las acciones); 'Risk Off' = huyen a refugios (efectivo, bonos), un entorno más difícil para subir."},
        {"label": "Momentum Sector",
         "value": _clean_tile_value(sec_raw, max_len=12),
         "level": sec_level, "sub": f"Sector: {rd.get('sector', '—')}",
         "tooltip": "Fuerza reciente del sector al que pertenece la acción. Un sector con momentum fuerte arrastra a sus miembros al alza; uno débil actúa como lastre aunque la empresa sea buena. Es mejor remar a favor de la corriente del sector."},
        {"label": "Curva Yield",
         "value": _clean_tile_value(yc_raw, max_len=12),
         "level": yc_level, "sub": "10Y-2Y spread",
         "tooltip": "Forma de la curva de tipos (diferencia entre el bono a 10 años y el de 2 años). Normal = economía sana. Invertida (10Y por debajo del 2Y) ha precedido históricamente a las recesiones, una señal macro de cautela."},
        {"label": "Nivel VIX",
         "value": _clean_tile_value(vix_raw, max_len=12),
         "level": vix_level, "sub": "Volatilidad esperada",
         "tooltip": "Índice del miedo (VIX): volatilidad esperada del mercado. Bajo (<20) = calma, entorno favorable para las acciones; alto (>30) = miedo y sacudidas fuertes, momento más peligroso y volátil."},
    ])

    # ── Sector heatmap ──
    from data.market_data import get_macro_data
    macro = get_macro_data() or {}
    sector_perf = macro.get("sector_performance", {})

    if sector_perf:
        st.markdown('<div class="section-title-bar">Rotación Sectorial (1Y)</div>',
                    unsafe_allow_html=True)
        # Misma estética que el inicio (barras con riel + columna de números).
        fig = build_sector_rotation(sector_perf)
        _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_macro_sector_heatmap_{analysis.ticker}")

    # ── Snapshot de indicadores macro ──
    st.markdown('<div class="section-title-bar">Snapshot Macro</div>',
                unsafe_allow_html=True)
    indicators_macro = [
        ("S&P 500",  macro.get("sp500", {}),  "index"),
        ("NASDAQ",   macro.get("nasdaq", {}), "index"),
        ("VIX",      macro.get("vix", {}),    "vol"),
        ("DXY",      macro.get("dxy", {}),    "dollar"),
        ("10Y YIELD", macro.get("tnx", {}),    "yield"),
        ("GOLD",     macro.get("gold", {}),   "price"),
    ]
    cols = st.columns(6, gap="small")
    for i, (label, data, fmt) in enumerate(indicators_macro):
        if not isinstance(data, dict):
            data = {}
        curr = data.get("current")
        chg = data.get("1m_change", 0) or 0
        if isinstance(curr, (int, float)):
            if fmt == "yield":
                val_str = f"{curr:.2f}%"
            elif fmt == "price":
                val_str = f"${curr:,.2f}"
            elif fmt == "index":
                val_str = f"{curr:,.0f}"
            else:
                val_str = f"{curr:.2f}"
        else:
            val_str = "—"
        color = "#3DD68C" if chg >= 0 else "#F1495F"
        arrow = "▲" if chg >= 0 else "▼"
        chg_str = f"{arrow} {abs(chg):.2f}% (1M)" if isinstance(curr, (int, float)) else "—"
        with cols[i]:
            st.markdown(f"""
            <div class="market-pulse-card">
                <div class="pulse-label">{label}</div>
                <div class="pulse-value">{val_str}</div>
                <div class="pulse-change" style="color:{color};">{chg_str}</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Pros / Cons ──
    _render_pros_cons(report,
                      pros_title="🌤️ Top 3 Vientos de Cola",
                      cons_title="🌪️ Top 3 Vientos en Contra")

    # ── Macro verdict ──
    _render_insight_card("Veredicto Macro", rd.get("macro_verdict", ""),
                         color="#E2B25C", icon="🎯")

    _render_analysis_card(report, title="Análisis Macro Completo")


def render_sentiment(analysis: StockAnalysis):
    report = analysis.reports.get("sentiment")
    if report is None:
        st.info("Análisis de sentimiento no disponible.")
        return

    _render_agent_header(report)
    km = report.key_metrics or {}
    rd = report.raw_data or {}

    # ── Gauge grande de sentimiento + 2 status pills ──
    col_gauge, col_pills = st.columns([1, 2])

    with col_gauge:
        fig = build_sentiment_gauge(report.score, height=230)
        _plotly(fig, use_container_width=True, config={"displayModeBar": False},
                        key=f"chart_sent_gauge_{analysis.ticker}")

    with col_pills:
        st.markdown('<div class="section-title-bar" style="margin-top:0;">📰 Estado de la Narrativa</div>',
                    unsafe_allow_html=True)

        # Los niveles/colores se calculan sobre los valores en ESPAÑOL que emite
        # el motor ("mejorando", "comprar el miedo", "bajo"…). Antes se
        # comparaban substrings en inglés → nunca coincidían y el color caía a
        # neutral/warn siempre.
        mom_raw = km.get("sentiment_momentum") or "estable"
        _mom = mom_raw.lower()
        mom_level = ("good" if "mejor" in _mom else
                     "bad" if "deterior" in _mom else "neutral")

        cont_raw = km.get("contrarian_signal") or "sin señal"
        _cont = cont_raw.lower()
        cont_level = ("good" if "miedo" in _cont else
                      "bad" if "euforia" in _cont else "neutral")

        narr_raw = km.get("narrative_theme") or "—"

        rep_raw = km.get("reputational_risk") or "bajo"
        _rep = rep_raw.lower()
        rep_level = ("good" if "bajo" in _rep else
                     "bad" if "alto" in _rep else "warn")

        # Descripciones (sub) como FRASES naturales, derivadas del estado real —
        # más específicas y claras (lenguaje tipo IA), aprovechando que en la
        # cuadrícula 2×2 las tarjetas son más anchas.
        _news_n = rd.get("news_count", 0)
        _tema_txt = _clean_tile_value(narr_raw, max_len=40)
        mom_sub = ("El tono de las noticias mejora y la narrativa reciente acompaña al precio."
                   if mom_level == "good" else
                   "El tono de las noticias empeora; la narrativa reciente juega en contra a corto plazo."
                   if mom_level == "bad" else
                   "El tono de las noticias se mantiene estable, sin un sesgo claro.")
        tema_sub = (f"El foco de las {_news_n} noticias recientes gira en torno a {_tema_txt.lower()}."
                    if _tema_txt not in ("—", "") else
                    f"Sin un tema dominante claro en las {_news_n} noticias recientes.")
        cont_sub = ("Miedo extremo en el mercado: el pesimismo exagerado suele preceder rebotes."
                    if cont_level == "good" else
                    "Euforia extrema: la masa está demasiado optimista, una señal de cautela."
                    if cont_level == "bad" else
                    "Sin extremos de euforia ni pánico; el sentimiento no da una señal contraria clara.")
        rep_sub = ("Riesgo ESG/regulatorio bajo: pocos frentes que puedan golpear el precio por sorpresa."
                   if rep_level == "good" else
                   "Riesgo ESG/regulatorio alto: posibles multas, demandas o escándalos que pesen en el precio."
                   if rep_level == "bad" else
                   "Riesgo ESG/regulatorio moderado: conviene vigilar los frentes regulatorios.")

        _sent_pills = [
            {"label": "Momentum Sentimiento",
             "value": _clean_tile_value(mom_raw, max_len=14),
             "level": mom_level, "sub": mom_sub,
             "tooltip": "Dirección del sentimiento en las noticias recientes: si la narrativa sobre la empresa está mejorando o deteriorándose. Un sentimiento que mejora suele acompañar (o anticipar) subidas; uno que se deteriora, lo contrario."},
            {"label": "Tema Narrativo",
             "value": _clean_tile_value(narr_raw, max_len=14),
             "level": "neutral", "sub": tema_sub,
             "tooltip": "Tema dominante que domina las noticias sobre la acción en este momento (resultados, producto, regulación, etc.). Ayuda a entender qué está moviendo la conversación y, por tanto, el precio a corto plazo."},
            {"label": "Señal Contraria",
             "value": _clean_tile_value(cont_raw, max_len=14),
             "level": cont_level, "sub": cont_sub,
             "tooltip": "Lectura a contracorriente: cuando TODOS son extremadamente optimistas suele ser mejor vender, y cuando reina el pánico suele haber oportunidad. Señala si el sentimiento está tan en un extremo que conviene hacer lo contrario a la masa."},
            {"label": "Riesgo Reputacional",
             "value": _clean_tile_value(rep_raw, max_len=10),
             "level": rep_level, "sub": rep_sub,
             "tooltip": "Riesgo de daño a la reputación por temas ambientales, sociales, de gobernanza (ESG) o regulatorios: demandas, multas, escándalos. Alto = posibles sustos que golpeen el precio al margen del negocio."},
        ]
        # Cuadrícula 2×2: dos filas de 2 (cada _render_status_pills hace
        # st.columns(2) → cada tarjeta ≈ el doble de ancha que antes).
        _render_status_pills(_sent_pills[:2])
        _render_status_pills(_sent_pills[2:])

    # ── Pros / Cons ──
    _render_pros_cons(report,
                      pros_title="📈 Top 3 Señales Positivas de Sentimiento",
                      cons_title="📉 Top 3 Riesgos de Narrativa")

    # ── Narrativa dominante ──
    _render_insight_card("Narrativa Dominante", rd.get("dominant_narrative", ""),
                         color="#6FA3E0", icon="📖")

    # ── Oportunidad detectada (si hay divergencia) ──
    opportunity = rd.get("opportunity", "")
    if opportunity and "No hay divergencia" not in opportunity:
        st.markdown(f"""
        <div class="alpha-opportunity-card">
            <div class="alpha-opportunity-header">
                <span class="alpha-opportunity-icon">⚡</span>
                <span class="alpha-opportunity-title">Divergencia Sentimiento-Fundamentales</span>
            </div>
            <div class="alpha-opportunity-body">{_no_latex(opportunity)}</div>
        </div>
        """, unsafe_allow_html=True)

    _render_analysis_card(report, title="Análisis de Sentimiento")


def _risk_analysis_prose(price, stop, target, downside, upside, rr, atr_pct, beta) -> str:
    """Genera el 'Análisis Completo de Riesgo' EN VIVO desde los MISMOS números
    que muestran los tiles (precio, protección, objetivo, R/R, ATR, beta).

    BLINDADO contra NaN/None: cada dato se sanea con _safe_num y cada frase solo
    se añade si su dato es válido → NUNCA aparece 'N/A' a mitad de una oración ni
    se dispara la rama equivocada (antes, un ATR NaN pasaba el `is not None` y
    caía en la rama de 'muy volátil' imprimiendo 'N/A'). Devuelve '' si no hay
    ni precio. Lenguaje natural, atado a las cifras reales y accionable."""
    price = _safe_num(price); stop = _safe_num(stop); target = _safe_num(target)
    downside = _safe_num(downside); upside = _safe_num(upside)
    rr = _safe_num(rr); atr_pct = _safe_num(atr_pct); beta = _safe_num(beta)
    if price is None:
        return ""
    partes = []

    # 1) El plan, en palabras claras (entrada ≈ precio actual, protección DEBAJO,
    #    objetivo ARRIBA), con los porcentajes reales.
    if stop is not None and target is not None and downside is not None and upside is not None:
        partes.append(
            f"El plan es claro: con la acción en torno a ${price:,.2f}, se coloca una protección (un "
            f"'stop', el precio al que uno acepta que se equivocó y vende para cortar la pérdida) en "
            f"${stop:,.2f} —un {downside:.1f}% por debajo— y se apunta a un objetivo de ${target:,.2f} "
            f"—un {upside:.1f}% por encima—.")
        if rr is not None:
            if rr >= 2.5:
                lect = ("claramente a favor: se apunta a ganar bastante más de lo que se arriesga, justo "
                        "el tipo de relación que conviene buscar antes de entrar")
            elif rr >= 2:
                lect = "favorable: el recorrido al objetivo supera con holgura lo que se pone en juego"
            elif rr >= 1.5:
                lect = "razonable: se puede ganar más de lo que se arriesga, aunque sin un margen enorme"
            elif rr >= 1:
                lect = ("ajustada: lo que se puede ganar y lo que se arriesga están parejos, así que el "
                        "punto de entrada pesa mucho en el resultado")
            else:
                lect = ("poco atractiva de momento: se arriesga más de lo que se apunta a ganar, y "
                        "convendría esperar un mejor precio de entrada")
            partes.append(
                f"Puesto en una sola cifra, la relación entre lo que se busca ganar y lo que se arriesga es "
                f"de {rr:.1f} a 1, {lect}.")
    elif stop is not None and target is not None:
        partes.append(
            f"Los niveles de referencia son: precio actual ${price:,.2f}, protección en ${stop:,.2f} y "
            f"objetivo en ${target:,.2f}.")
    else:
        partes.append(f"El precio actual de referencia es ${price:,.2f}.")

    # 2) Volatilidad (ATR) — SOLO si es un número válido. La rama se elige por el
    #    valor real, nunca por un NaN.
    if atr_pct is not None:
        if atr_pct <= 2:
            partes.append(
                f"En cuanto a nervios, es una acción relativamente tranquila: en un día normal se mueve "
                f"alrededor de un {atr_pct:.1f}%, así que rara vez da grandes sustos y se puede manejar con "
                f"un tamaño de posición habitual y un stop cómodo.")
        elif atr_pct <= 4:
            partes.append(
                f"Su volatilidad es moderada: oscila cerca de un {atr_pct:.1f}% al día. Es un vaivén "
                f"llevadero siempre que no se sobredimensione la posición ni se pegue el stop demasiado al "
                f"precio.")
        else:
            partes.append(
                f"Ojo con lo movida que es: cambia de precio con fuerza, alrededor de un {atr_pct:.1f}% cada "
                f"día. En la práctica conviene comprar una cantidad algo menor de lo habitual y dar más aire "
                f"a la protección, porque un simple bandazo del día podría sacarte de la posición sin que la "
                f"tesis haya fallado.")

    # 3) Beta — SOLO si es un número válido.
    if beta is not None:
        if beta < 0:
            partes.append(
                f"Frente al mercado se mueve al revés (beta {beta:.1f}): tiende a subir cuando el índice cae "
                f"y viceversa, lo que puede darle un papel de cobertura dentro de la cartera.")
        elif beta <= 0.9:
            partes.append(
                f"Es defensiva respecto al mercado (beta {beta:.1f}): suele moverse menos que el índice, "
                f"tanto en las subidas como en las caídas.")
        elif beta <= 1.3:
            partes.append(
                f"Se mueve a un ritmo parecido al del mercado (beta {beta:.1f}): ni amplifica ni amortigua "
                f"demasiado sus subidas y bajadas.")
        else:
            partes.append(
                f"Es bastante sensible al mercado (beta {beta:.1f}): cuando el índice general se mueve, ella "
                f"tiende a hacerlo con más fuerza, así que en un entorno turbulento puede sufrir —o "
                f"rebotar— más que la media.")

    # 4) Cierre accionable, atado a la relación riesgo/beneficio real.
    if rr is not None:
        if rr >= 2:
            partes.append(
                "En resumen, las cuentas acompañan: hay más para ganar que para perder. La clave será la "
                "disciplina de respetar la protección pase lo que pase y no mover el objetivo por "
                "impaciencia.")
        elif rr >= 1:
            partes.append(
                "En resumen, es un perfil equilibrado: ni una ganga ni una trampa. El resultado dependerá "
                "sobre todo de la disciplina para respetar la entrada, la protección y el objetivo "
                "marcados.")
        else:
            partes.append(
                "En resumen, hoy las cuentas no acompañan del todo: se arriesga casi tanto —o más— de lo "
                "que se apunta a ganar. Tendría sentido solo con una convicción muy alta; de lo contrario, "
                "conviene esperar un mejor punto de entrada.")

    return " ".join(partes)


def render_risk(analysis: StockAnalysis):
    report = analysis.reports.get("risk")
    if report is None:
        st.info("Análisis de riesgo no disponible.")
        return

    _render_agent_header(report)
    km = report.key_metrics or {}
    rd = report.raw_data or {}

    # ── KPI tiles de Riesgo ──
    st.markdown('<div class="section-title-bar">Métricas de Riesgo</div>',
                unsafe_allow_html=True)

    vol      = _safe_num(km.get("volatility_atr_pct"))
    rr_raw   = km.get("risk_reward", "")

    # Get computed values as fallback (nan-safe: el computed_risk de análisis
    # viejos de prod puede traer atr_pct en NaN → _safe_num lo vuelve None → "—")
    computed = rd.get("computed_risk", {}) or {}
    if vol is None: vol = _safe_num(computed.get("atr_pct"))

    # Recalcular Pérdida Máxima y Ganancia Potencial usando el PRECIO ACTUAL en vivo
    # (más útil que el entry hipotético del agente)
    from data.market_data import get_company_info, get_risk_levels
    info_live = get_company_info(analysis.ticker) or {}
    # nan-safe: _safe_num descarta NaN/None → nunca "+nan%"
    current_price = _safe_num(info_live.get("current_price")) or _safe_num(analysis.entry_price)
    stop_lvl   = _safe_num(analysis.stop_loss)
    # Target: cacheado → target de analistas de get_company_info (la MISMA vía
    # PROBADA que ya funciona en Render para los fundamentales) → get_risk_levels.
    target_lvl = _safe_num(analysis.target_price) or _safe_num(info_live.get("target_price"))

    # Respaldo INFALIBLE: si el análisis cacheado no trae niveles reales (se
    # generó con los datos bloqueados), los recalculamos FRESCOS con la misma
    # metodología (precio + ATR + máximo de 52 semanas), vía OHLCV o TradingView.
    if stop_lvl is None or target_lvl is None or current_price is None or vol is None:
        _fresh = get_risk_levels(analysis.ticker)
        if _fresh:
            current_price = current_price or _fresh.get("current_price")
            stop_lvl      = stop_lvl      or _fresh.get("stop")
            target_lvl    = target_lvl    or _fresh.get("target")
            vol           = vol           or _fresh.get("atr_pct")

    # La protección NUNCA por encima del precio actual: si la acción cayó por
    # debajo del stop guardado en el análisis (p.ej. ORCL), se reancla ~1% bajo
    # el precio VIVO — sin esto la "Pérdida Máxima" salía positiva y el R/R y
    # la gráfica Upside/Downside quedaban invertidos.
    if current_price and stop_lvl and stop_lvl >= current_price * 0.99:
        stop_lvl = round(current_price * 0.99, 2)

    downside = None
    upside = None
    rr_num = None
    if current_price and stop_lvl:
        downside = (current_price - stop_lvl) / current_price * 100
    if current_price and target_lvl:
        upside = (target_lvl - current_price) / current_price * 100
    if downside and downside > 0 and upside is not None:
        rr_num = upside / downside

    rr_clean = f"{rr_num:.1f}:1" if rr_num is not None else _extract_rr_ratio(rr_raw)

    _render_metric_tiles([
        {"icon": "💔", "label": "Pérdida Máxima",
         "value": f"-{downside:.1f}%" if downside is not None else "—",
         "color": "#F1495F",
         "tooltip": "Pérdida porcentual si el precio cae al nivel de protección desde el PRECIO ACTUAL del mercado."},
        {"icon": "🚀", "label": "Ganancia Potencial",
         "value": f"+{upside:.1f}%" if upside is not None else "—",
         "color": "#3DD68C",
         "tooltip": "Ganancia porcentual si el precio alcanza el target desde el PRECIO ACTUAL del mercado."},
        {"icon": "⚖️", "label": "R/R Ratio",
         "value": rr_clean,
         "color": ("#3DD68C" if (rr_num or 0) >= 3 else
                   "#E2B25C" if (rr_num or 0) >= 2 else "#F1495F"),
         "tooltip": "Risk/Reward Ratio calculado desde el precio actual. Mínimo aceptable 2:1, ideal 3:1 o superior."},
        {"icon": "📊", "label": "Volatilidad ATR",
         "value": f"{vol:.1f}%" if vol is not None else "—",
         "color": "#6FA3E0" if (vol or 0) < 3 else "#E2B25C" if (vol or 0) < 5 else "#F1495F",
         "tooltip": "Average True Range como % del precio. >5% indica activo muy volátil con drawdowns frecuentes."},
    ])

    # ── R/R Chart visual — usando PRECIO ACTUAL como referencia ──
    # Reusa current_price/stop_lvl/target_lvl ya saneados arriba (nan-safe).
    if current_price and stop_lvl and target_lvl:
        st.markdown('<div class="section-title-bar">Upside / Downside vs Precio Actual</div>',
                    unsafe_allow_html=True)
        # Como estaba antes: displayModeBar False. NO staticPlot (dejaba la
        # figura en blanco por no tener trazas). dragmode=False bloquea el
        # zoom/arrastre desde la propia figura.
        fig = build_rr_chart(current_price, stop_lvl, target_lvl, analysis.ticker)
        _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False},
                        key=f"chart_risk_tab_rr_{analysis.ticker}")

    # ── Pros / Cons ──
    _render_pros_cons(report,
                      pros_title="✅ Top 3 Aspectos Favorables del Riesgo",
                      cons_title="⚠️ Top 3 Riesgos Identificados")

    # ── Análisis completo — REGENERADO EN VIVO desde los mismos números que los
    #    tiles (precio, protección, objetivo, R/R, ATR, beta). Blindado contra
    #    NaN: nunca imprime 'N/A' a mitad de frase ni la rama equivocada. Si no
    #    hubiera ni precio, cae al texto persistido del agente. ────────────────
    beta_live = _safe_num(info_live.get("beta"))
    if beta_live is None:
        beta_live = _safe_num(computed.get("beta"))
    _risk_prose = _risk_analysis_prose(current_price, stop_lvl, target_lvl,
                                       downside, upside, rr_num, vol, beta_live)
    if _risk_prose:
        st.markdown('<div class="section-title-bar">Análisis Completo de Riesgo</div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div class="analysis-card"><div class="analysis-text">{_no_latex(_risk_prose)}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        _render_analysis_card(report, title="Análisis Completo de Riesgo")


# ──────────────────────────────────────────────────────────────────────
def render_agent_tab(analysis: StockAnalysis, agent_key: str):
    report = analysis.reports.get(agent_key)
    if not report:
        st.info("Análisis no disponible para este agente.")
        return

    icon = AGENT_ICONS.get(report.agent_name) or (str(report.agent_name)[:2].upper() or "··")

    col_score, col_conv = st.columns([1, 3])
    with col_score:
        score = report.score
        color = score_color(score)
        css_class = score_css_class(score)
        st.markdown(
            f'<div style="text-align:center;padding:16px;background:#0F1419;border:1px solid #232830;border-radius:8px;border-top:3px solid {color};">'
            f'<div style="font-family:JetBrains Mono;font-size:3rem;font-weight:700;color:{color};">{score:.0f}</div>'
            f'<div style="font-size:0.7rem;color:#8D949E;text-transform:uppercase;letter-spacing:0.1em;">Score / 100</div>'
            f'<div style="font-size:0.75rem;color:{color};margin-top:4px;">{_conv_es(report.conviction)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Sub-scores
        if report.sub_scores:
            st.markdown("**Sub-scores**")
            for k, v in report.sub_scores.items():
                if not k.endswith("_snowflake") and isinstance(v, (int, float)):
                    bar_width = min(v / 34 * 100, 100)
                    st.markdown(
                        f'<div style="margin:4px 0;">'
                        f'<div style="display:flex;justify-content:space-between;font-size:0.72rem;color:#8D949E;">'
                        f'<span>{k.replace("_", " ").title()}</span><span>{v:.0f}</span></div>'
                        f'<div style="background:#232830;border-radius:2px;height:4px;margin-top:2px;">'
                        f'<div style="background:{color};width:{bar_width}%;height:100%;border-radius:2px;"></div>'
                        f'</div></div>',
                        unsafe_allow_html=True,
                    )

    with col_conv:
        st.markdown(f"#### {icon} {report.agent_name}")
        st.markdown(
            f'<div class="analysis-card"><div class="analysis-text">{_no_latex(report.analysis)}</div></div>',
            unsafe_allow_html=True,
        )

        col_p, col_c = st.columns(2)
        with col_p:
            if report.pros:
                st.markdown("**Positivos**")
                for p in report.pros[:3]:
                    st.markdown(f'<div style="color:#3DD68C;font-size:0.82rem;padding:2px 0;">✓ {p}</div>', unsafe_allow_html=True)
        with col_c:
            if report.cons:
                st.markdown("**Riesgos / Negativos**")
                for c in report.cons[:3]:
                    st.markdown(f'<div style="color:#F1495F;font-size:0.82rem;padding:2px 0;">⚠ {c}</div>', unsafe_allow_html=True)

        # Key metrics
        if report.key_metrics:
            st.markdown("---")
            st.markdown("**Métricas Clave**")
            cols = st.columns(3)
            for i, (k, v) in enumerate(report.key_metrics.items()):
                with cols[i % 3]:
                    st.metric(label=k.replace("_", " ").title(), value=str(v) if v else "N/A")

    # Raw data extra (insights específicos de cada agente)
    extra_keys = {
        "fundamentals":  ["dcf_thesis", "earnings_quality"],
        "future":        ["future_thesis", "key_risks"],
        "catalysts":     ["top_catalyst"],
        "institutional": ["key_insight"],
        "macro":         ["macro_verdict"],
        "sentiment":     ["dominant_narrative", "opportunity"],
        "risk":          ["risk_verdict", "stop_rationale"],
    }

    extra = extra_keys.get(agent_key, [])
    for key in extra:
        val = report.raw_data.get(key)
        if val and isinstance(val, str) and len(val) > 5:
            label = key.replace("_", " ").title()
            st.markdown(
                f'<div style="background:#101216;border:1px solid #232830;border-radius:4px;padding:10px;margin-top:8px;">'
                f'<div style="font-size:0.7rem;color:#8D949E;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">{label}</div>'
                f'<div style="font-size:0.85rem;color:#C9CDD3;line-height:1.6;">{val}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── Scan Results Tab ──────────────────────────────────────────────────────
def render_scan_results():
    """Resultados del escáner como TARJETAS visuales (sparkline, chips, meter),
    no como tabla. Misma fuente de datos y mismos flags de sesión de siempre."""
    from dashboard.scanner_filters import SECTOR_OPTIONS

    # ── Barra superior ÚNICA: [⌂ Volver al Inicio (grande)] [🔧 Ajustar filtros]
    # en la MISMA línea. El topnav global se omite en esta vista y el duplicado
    # pequeño de "Volver al Inicio" desapareció.
    col_home, col_filters, _sp = st.columns([2.2, 2, 5.8])
    with col_home:
        if st.button("⌂  Volver al Inicio", key="topnav_home_btn",
                     use_container_width=True):
            st.session_state.scan_results = []
            st.session_state.current_scan_id = None
            st.session_state._show_scan_results = False
            st.session_state.scanner_config_open = False
            st.rerun()
    with col_filters:
        if st.button("🔧 Ajustar filtros", key="scan_back_to_filters",
                     use_container_width=True):
            st.session_state.scanner_config_open = True
            st.session_state._show_scan_results = False
            st.rerun()

    resultados = st.session_state.scan_results or []
    n = len(resultados)
    diag = st.session_state.get("_scan_diagnostics", {}) or {}
    universe = diag.get("universe_count", 0)
    err = diag.get("error")
    score_medio = (sum(r.screener_score for r in resultados) / n) if n else 0

    # ── Cabecera con identidad de radar (barrido detrás del texto) ──
    resumen = f"{n} candidatos"
    if universe:
        resumen += f" · {universe} acciones analizadas"
    if n:
        resumen += f" · score medio {score_medio:.0f}"
    st.markdown(
        f'<div class="scan-radar-head"><div class="scan-radar-title">'
        f'◎ RADAR DE MERCADO</div>'
        f'<div class="scan-radar-sub">{resumen}</div></div>',
        unsafe_allow_html=True)

    # Diagnóstico (mismo criterio de siempre)
    if err or (universe and universe < 100):
        if err:
            color, msg = "#F1495F", f"❌ Error del escáner: {err}"
        else:
            passing = diag.get("passing_count", 0)
            color = "#E2B25C"
            msg = (f"⚠️ El escáner devolvió solo <strong>{universe} acciones</strong> "
                   f"al universo crudo (esperábamos 1000+). De ellas, <strong>{passing}</strong> "
                   f"pasaron los filtros. Puede ser rate-limit transitorio — reintenta en 1-2 min.")
        st.markdown(
            f'<div style="background:#101216;border-left:3px solid {color};'
            f'padding:10px 14px;margin:8px 0 16px 0;border-radius:4px;'
            f'font-size:0.82rem;color:#C9CDD3;">{msg}</div>', unsafe_allow_html=True)

    if not resultados:
        if st.session_state.get("_show_scan_results"):
            st.warning(
                "El scan se ejecutó pero **0 acciones pasaron los filtros**.\n\n"
                "Causas posibles:\n"
                "- Los filtros son demasiado estrictos (prueba con menos restricciones).\n"
                "- La fuente de datos está limitando peticiones temporalmente. Espera 1-2 minutos y vuelve a intentar.\n\n"
                "Puedes ajustar los filtros desde 'Escanear el Mercado' o lanzar un análisis individual de una acción específica."
            )
        else:
            st.info("No hay resultados de scan. Usa el botón 'Escanear el Mercado' en el home.")
        return

    # ── Orden en una línea (solo reordena en memoria) ──
    _ORDENES = [("Score", "score"), ("Momentum 6M", "mom"),
                ("Fortaleza RS", "rs"), ("Cerca de máximos", "prox")]
    _lbl_a_key = {lbl: k for lbl, k in _ORDENES}
    orden = st.session_state.get("_scan_orden", "score")
    _lbl_actual = next((lbl for lbl, k in _ORDENES if k == orden), "Score")
    with st.container(key="scanorden"):
        oc_head, oc_radio = st.columns([1.6, 6.4], gap="small")
        with oc_head:
            st.markdown('<div class="scfila-head"><span class="scfila-icon">⇅</span>'
                        '<span class="scfila-title">Ordenar por</span></div>',
                        unsafe_allow_html=True)
        with oc_radio:
            _sel = st.radio("Orden", [lbl for lbl, _ in _ORDENES],
                            index=[lbl for lbl, _ in _ORDENES].index(_lbl_actual),
                            horizontal=True, key="scan_orden_radio",
                            label_visibility="collapsed")
            if _lbl_a_key.get(_sel, "score") != orden:
                st.session_state._scan_orden = _lbl_a_key[_sel]
                st.rerun()
    orden = st.session_state.get("_scan_orden", "score")
    llave = {"score": lambda r: -r.screener_score,
             "mom": lambda r: -(r.momentum_6m or 0),
             "rs": lambda r: -(r.rs_score or 0),
             "prox": lambda r: -(r.pct_from_52w_high or -999)}[orden]
    resultados = sorted(resultados, key=llave)

    # ── Sparklines reales para los primeros 24 (un solo lote, cacheado 3 min) ──
    spark = {}
    try:
        from data.market_data import get_live_snapshot
        spark = get_live_snapshot([r.ticker for r in resultados[:24]]) or {}
    except Exception:
        spark = {}

    _SECTOR_ES_SCAN = {o["key"]: o["label"] for o in SECTOR_OPTIONS}
    _STAGE_TXT = {1: ("S1 · Acumulación", "#C08E3B"), 2: ("S2 · Alcista", "#3DD68C"),
                  3: ("S3 · Distribución", "#E2B25C"), 4: ("S4 · Bajista", "#F1495F")}

    # ── Grid de tarjetas: 2 por fila (1 en pantallas estrechas vía CSS) ──
    for fila_ini in range(0, len(resultados), 2):
        par = resultados[fila_ini:fila_ini + 2]
        cols = st.columns(2, gap="medium")
        for col, r in zip(cols, par):
            with col:
                color = score_color(r.screener_score)
                stage_txt, stage_col = _STAGE_TXT.get(int(r.stage or 0),
                                                      (f"S{r.stage}", "#8D949E"))
                mom_col = "#3DD68C" if (r.momentum_6m or 0) >= 0 else "#F1495F"
                sector_es = _SECTOR_ES_SCAN.get(r.sector, r.sector or "—")
                mktcap = _fmt_grande(r.market_cap) if r.market_cap else "—"
                d = spark.get(r.ticker, {})
                chg = d.get("change_pct", 0) or 0
                chg_col = "#3DD68C" if chg >= 0 else "#F1495F"
                spark_html = _sparkline_svg(d.get("closes"), chg >= 0, w=260, h=34)
                if not d.get("closes"):
                    # Sin intradía (del 25º en adelante): barra de momentum 6M
                    _w = max(4, min(abs(r.momentum_6m or 0), 60)) / 60 * 100
                    spark_html = (f'<div class="scan-mom-bar"><span style="width:{_w:.0f}%;'
                                  f'background:{mom_col};"></span></div>')
                # Proximidad al máximo anual: dot del meter en 100 + pct (−7% → 93%)
                prox = max(0.0, min(100.0, 100.0 + (r.pct_from_52w_high or -100)))
                _glow = {"#3DD68C": "61,214,140", "#E2B25C": "226,178,92",
                         "#F1495F": "241,73,95"}.get(color, "226,178,92")
                tk_safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in r.ticker)
                with st.container(key=f"scres_{tk_safe}"):
                    st.markdown(
                        f'<div class="scan-res-head">'
                        f'<span class="scan-res-ticker">{r.ticker}</span>'
                        f'<span class="scan-res-name">{(r.name or "")[:34]}</span>'
                        f'<span class="scan-res-score" style="color:{color};border-color:{color};">'
                        f'{r.screener_score:.0f}<span class="scan-res-score-max">/100</span></span>'
                        f'</div>'
                        f'<div class="scan-res-priceline">'
                        f'<span class="scan-res-price">${r.price:,.2f}</span>'
                        f'<span class="scan-res-chg" style="color:{chg_col};">'
                        f'{"▲" if chg >= 0 else "▼"} {abs(chg):.2f}%</span>'
                        f'</div>'
                        f'{spark_html}'
                        f'<div class="scan-res-chips">'
                        f'<span class="scan-res-chip" style="color:{stage_col};border-color:{stage_col}44;">{stage_txt}</span>'
                        f'<span class="scan-res-chip">RS {r.rs_score:.0f}</span>'
                        f'<span class="scan-res-chip" style="color:{mom_col};">'
                        f'{"+" if (r.momentum_6m or 0) >= 0 else ""}{r.momentum_6m:.1f}% 6M</span>'
                        f'<span class="scan-res-chip">{mktcap}</span>'
                        f'<span class="scan-res-chip scan-res-chip--sector">{sector_es}</span>'
                        f'</div>'
                        f'<div class="scan-res-proxlbl">Distancia al máximo anual · '
                        f'{r.pct_from_52w_high:.0f}%</div>'
                        f'<div class="meter"><span class="meter-dot" style="left:{prox:.0f}%;'
                        f'background:{color};box-shadow:0 0 0 3px rgba({_glow},0.18),'
                        f'0 0 8px rgba({_glow},0.45);"></span></div>',
                        unsafe_allow_html=True)
                    if st.button("◈  Análisis DLP", key=f"scan_analyze_{r.ticker}",
                                 use_container_width=True):
                        run_analysis(r.ticker)

    # Aterrizaje del radar: un escaneo recién terminado hereda el scroll del
    # config a media página (el scroll vive en section.stMain, no en window,
    # así que window.scrollTo no basta). Iframe 0-alto AL FINAL — no crea
    # hueco arriba — que sube la vista al tope una sola vez; reordenar o
    # volver desde un análisis no re-dispara (el flag es one-shot).
    if st.session_state.pop("_radar_scroll_top", False):
        components.html("""
        <script>
        (function(){
            const d = (window.parent && window.parent.document) || document;
            const m = d.querySelector('section[data-testid="stMain"]')
                   || d.querySelector('[data-testid="stAppViewContainer"]');
            if (m) m.scrollTo({top: 0, left: 0, behavior: "instant"});
            try { window.parent.scrollTo(0, 0); } catch(e) {}
        })();
        </script>""", height=0)


# ── Scanner Config Page ──────────────────────────────────────────────────

# Accent colors por categoría — cohesivos con la paleta del dashboard
SCANNER_ACCENTS = {
    "size":      "#E2B25C",   # naranja — tamaño / valor
    "stage":     "#3DD68C",   # verde — tendencia
    "rs":        "#9D8CE0",   # morado — fortaleza
    "momentum":  "#6FA3E0",   # azul — momentum
    "proximity": "#00D4FF",   # cyan — máximo anual
    "sector":    "#D65C7E",   # rosa — sectores
    "liquidity": "#6FA3E0",   # azul claro — liquidez
    "results":   "#F0C878",   # amarillo — cantidad
}


def _scanner_group_head(step: str, title: str, subtitle: str):
    """Encabezado de un bloque de criterios del scanner (agrupa varias cards
    bajo una misma idea: qué buscar / cómo se comporta / qué ver)."""
    st.markdown(f"""
    <div class="scanner-group-head scanner-group-head--centrada">
        <span class="scanner-group-rule"></span>
        <span class="scanner-group-step">{step}</span>
        <div class="scanner-group-titles">
            <div class="scanner-group-title">{title}</div>
            <div class="scanner-group-subtitle">{subtitle}</div>
        </div>
        <span class="scanner-group-rule"></span>
    </div>
    """, unsafe_allow_html=True)


def _fila_scanner(clave, icono, titulo, tooltip, opciones, es_activa, al_pulsar,
                  extras=None, por_fila=None):
    """UNA fila de filtro a todo lo ancho: cabecera fija (icono + título + '?')
    a la izquierda y las píldoras LLENANDO el ancho restante — 4 opciones =
    4 botones a lo ancho; 5 = 5 a lo ancho. Con `por_fila` (sectores) las
    opciones se trocean en líneas de máximo N y CADA línea llena el ancho
    (5/5/3). Ningún botón se parte jamás en dos líneas.

    SIN tooltips en las píldoras (help=): el popup nativo seguía al ratón y
    BLOQUEABA el clic. La explicación vive solo en el «?» de la cabecera
    (tooltip CSS propio, no bloquea).

    opciones: [(etiqueta, key_opcion, _)] · extras: [(etiqueta, callback)]."""
    with st.container(key=f"scfila_{clave}"):
        c_head, c_rail = st.columns([2.0, 8.0], gap="small")
        with c_head:
            help_html = (f'<span class="scanner-help" data-tooltip="{tooltip}">?</span>'
                         if tooltip else "")
            st.markdown(
                f'<div class="scfila-head"><span class="scfila-icon">{icono}</span>'
                f'<span class="scfila-title">{titulo}</span>{help_html}</div>',
                unsafe_allow_html=True)
        with c_rail:
            with st.container(key=f"scrail_{clave}"):
                todas = list(opciones) + [(lbl, f"__extra_{i}", "")
                                          for i, (lbl, _) in enumerate(extras or [])]
                paso = por_fila or len(todas)
                for ini in range(0, len(todas), paso):
                    bloque = todas[ini:ini + paso]
                    cols = st.columns(len(bloque), gap="small")
                    for col, (lbl, okey, _sub) in zip(cols, bloque):
                        with col:
                            if str(okey).startswith("__extra_"):
                                idx = int(str(okey).split("_")[-1])
                                if st.button(lbl, key=f"sc_{clave}_x{idx}",
                                             use_container_width=True):
                                    extras[idx][1]()
                                    st.rerun()
                            else:
                                act = es_activa(okey)
                                if st.button(lbl, key=f"sc_{clave}_{okey}",
                                             type="primary" if act else "secondary",
                                             use_container_width=True):
                                    al_pulsar(okey)
                                    st.rerun()


def render_scanner_config():
    """Página de configuración del scanner — cada filtro es UNA fila a todo lo
    ancho con su carril de píldoras de una sola línea. Optimizada para el
    iframe de Whop: nada se apila, nada se parte, todo se lee de un vistazo.
    La LÓGICA de estado y el mapeo a filtros técnicos son los mismos de antes."""
    from config.settings import SCANNER_DEFAULTS
    from dashboard.scanner_filters import (
        SIZE_OPTIONS, STAGE_OPTIONS, RS_OPTIONS, MOMENTUM_OPTIONS,
        PROXIMITY_OPTIONS, SECTOR_OPTIONS, LIQUIDITY_OPTIONS, MAX_RESULTS_OPTIONS,
        build_screener_filters,
    )

    if not isinstance(st.session_state.get("scanner_filters"), dict):
        st.session_state.scanner_filters = dict(SCANNER_DEFAULTS)
    for k, v in SCANNER_DEFAULTS.items():
        if k not in st.session_state.scanner_filters:
            st.session_state.scanner_filters[k] = v
    sf = st.session_state.scanner_filters

    # ── Hero compacto (alto pensado para el embed) ──
    st.markdown("""
    <div class="scanner-hero scanner-hero--compacto">
        <div class="scanner-hero-eyebrow">◇ Búsqueda personalizada</div>
        <div class="scanner-hero-title">Encuentra las mejores acciones</div>
        <div class="scanner-hero-sub">Cada criterio es una fila: elige tus píldoras y ejecuta.
        El «?» de cada fila explica el término técnico.</div>
    </div>
    """, unsafe_allow_html=True)

    col_home, _sp, col_reset = st.columns([2.2, 5.8, 2])
    with col_home:
        if st.button("⌂  Volver al Inicio", key="topnav_home_btn",
                     use_container_width=True):
            st.session_state.scanner_config_open = False
            st.rerun()
    with col_reset:
        with st.container(key="scnav_reset"):
            if st.button("↻ Restablecer", key="scanner_reset_top"):
                st.session_state.scanner_filters = dict(SCANNER_DEFAULTS)
                st.rerun()

    # ── Helpers de estado (idénticos en semántica a la versión anterior) ──
    def _multi(campo):
        def activa(k):
            return k in (sf.get(campo) or [])
        def pulsar(k):
            actual = list(sf.get(campo) or [])
            sf[campo] = ([x for x in actual if x != k] if k in actual else actual + [k])
        return activa, pulsar

    def _single(campo):
        return (lambda k: sf.get(campo) == k), (lambda k: sf.__setitem__(campo, k))

    # ════════ 1 · QUÉ EMPRESAS BUSCAR ════════
    _scanner_group_head("1", "Qué empresas buscar",
                        "El universo de partida: sectores, tamaño y liquidez")

    a, p = _multi("sectors")
    _fila_scanner(
        "sectores", "🏭", "Sectores",
        "Elige uno o varios — sin selección = todos los sectores.",
        # Sin emoji por píldora: las demás filas no lo llevan y el ancho que
        # roban (~26px × 5 por línea) es justo lo que corta el carril a 1400.
        [(o["label"], o["key"], "") for o in SECTOR_OPTIONS],
        a, p,
        extras=[("✓ Todos", lambda: sf.__setitem__("sectors", [o["key"] for o in SECTOR_OPTIONS])),
                ("✕ Ninguno", lambda: sf.__setitem__("sectors", []))],
        por_fila=5)

    a, p = _multi("size_buckets")
    _fila_scanner("tamano", "🏢", "Tamaño",
                  "Capitalización de mercado. Megacaps = más estables; micro = más volátiles.",
                  [(f"{o['label']} · {o['sub']}", o["key"], "") for o in SIZE_OPTIONS], a, p)

    a, p = _single("liquidity")
    _fila_scanner("liquidez", "💧", "Liquidez",
                  "Volumen medio diario: cuánto se negocia. Alta = entrar y salir sin mover el precio.",
                  [(f"{o['label']} · {o['sub'].replace('Volumen ', '')}", o["key"], "") for o in LIQUIDITY_OPTIONS], a, p)

    # ════════ 2 · CÓMO SE ESTÁ COMPORTANDO ════════
    _scanner_group_head("2", "Cómo se está comportando",
                        "La lectura del precio: fase, liderazgo e inercia")

    a, p = _multi("stages")
    _fila_scanner("tendencia", "📈", "Tendencia",
                  "Fase del ciclo según la metodología de fases de Minervini. La fase 2 (alcista confirmada) es la ideal.",
                  [(o["label"], o["key"], o["sub"]) for o in STAGE_OPTIONS], a, p)

    a, p = _single("rs_strength")
    _fila_scanner("fortaleza", "💪", "Fortaleza",
                  "Fuerza relativa frente al S&P 500. Alta = la acción lidera al mercado.",
                  [(o["label"], o["key"], o["sub"]) for o in RS_OPTIONS], a, p)

    a, p = _single("momentum_6m")
    _fila_scanner("momentum", "🚀", "Momentum",
                  "Retorno de los últimos 6 meses: cuánta inercia lleva el precio.",
                  [(o["label"], o["key"], o["sub"]) for o in MOMENTUM_OPTIONS], a, p)

    a, p = _single("proximity_high")
    _fila_scanner("maximo", "🏔️", "Vs. máximo anual",
                  "Distancia al precio más alto de 12 meses. Cerca = fortaleza; lejos = castigo u oportunidad.",
                  [(o["label"], o["key"], o["sub"]) for o in PROXIMITY_OPTIONS], a, p)

    # ════════ 3 · QUÉ QUIERES VER ════════
    _scanner_group_head("3", "Qué quieres ver",
                        "Cuántos resultados, ordenados de mejor a peor puntaje")

    a, p = _single("max_results")
    _fila_scanner("resultados", "📋", "Resultados",
                  "Cuántas acciones ver al final. 20 es suficiente para revisar a fondo.",
                  [(o["label"], o["key"], o["sub"]) for o in MAX_RESULTS_OPTIONS], a, p)

    # ── Ejecutar (halo dorado) + Volver ──
    st.markdown('<div class="scanner-section-divider"></div>', unsafe_allow_html=True)
    _sl, run_col, _sr = st.columns([1, 2, 1])
    with run_col:
        with st.container():
            st.markdown('<div class="ejecutar-glow-anchor"></div>', unsafe_allow_html=True)
            if st.button("🚀 Ejecutar búsqueda", key="scanner_run",
                         use_container_width=True, type="primary"):
                tech_filters = build_screener_filters(sf)
                st.session_state.scanner_config_open = False
                run_market_scan(filters=tech_filters)
    _bl, back_col, _br = st.columns([1.5, 1, 1.5])
    with back_col:
        if st.button("← Volver", key="scanner_back_bottom", use_container_width=True):
            st.session_state.scanner_config_open = False
            st.rerun()


# ── Quick View (compact instant dashboard, sin AI processing) ───────────

def render_quick_view(ticker: str):
    """Dashboard compacto e instantáneo de una acción con datos en vivo de yfinance.
    Sin AI processing — todo se carga en 1-3 segundos."""
    from data.market_data import get_company_info, get_price_history, get_news

    # Loading: skeleton + spinner centrado mientras cargan los datos
    loading_placeholder = st.empty()
    loading_placeholder.markdown(
        _skeleton_quick_view_html() + _spinner_overlay_html(
            text=f"CARGANDO {ticker}",
            sub="Obteniendo precio, noticias y métricas en vivo…"
        ),
        unsafe_allow_html=True,
    )

    info = get_company_info(ticker)
    df = get_price_history(ticker, period="1y")
    news = get_news(ticker, max_items=6)

    loading_placeholder.empty()

    name = info.get("name", ticker)
    current_price = info.get("current_price") or 0

    # ── Calcular performance multi-timeframe ─────────────────────────
    day_change = week_change = month_change = year_change = 0
    high_52w = info.get("52w_high", 0) or 0
    low_52w = info.get("52w_low", 0) or 0

    if not df.empty:
        latest = float(df["Close"].iloc[-1])
        if not current_price:
            current_price = latest
        prev = float(df["Close"].iloc[-2]) if len(df) > 1 else latest
        day_change = (latest - prev) / prev * 100 if prev else 0
        if len(df) >= 6:
            week_change = (latest - float(df["Close"].iloc[-6])) / float(df["Close"].iloc[-6]) * 100
        if len(df) >= 22:
            month_change = (latest - float(df["Close"].iloc[-22])) / float(df["Close"].iloc[-22]) * 100
        year_start = float(df["Close"].iloc[0])
        year_change = (latest - year_start) / year_start * 100 if year_start else 0

    # ── Header con precio + cambio día ───────────────────────────────
    day_color = "#3DD68C" if day_change >= 0 else "#F1495F"
    arrow = "▲" if day_change >= 0 else "▼"

    col_back, col_spacer = st.columns([1, 5])
    with col_back:
        if st.button("← Volver al Hub", use_container_width=True, key="qv_back"):
            st.session_state.quick_view_ticker = None
            st.rerun()

    st.markdown(f"""
    <div class="qv-header">
        <span class="qv-ticker">{ticker}</span>
        <span class="qv-name">{name}</span>
        <span class="qv-price">${current_price:.2f}</span>
        <span class="qv-change" style="color:{day_color};">{arrow} {abs(day_change):.2f}% día</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Row 1: Chart + Métricas clave ────────────────────────────────
    col_chart, col_metrics = st.columns([2, 1], gap="medium")

    with col_chart:
        st.markdown('<div class="qv-section-title">📈 PRECIO 6 MESES</div>', unsafe_allow_html=True)
        from dashboard.charts import build_quick_chart
        fig = build_quick_chart(df, ticker)
        _plotly(fig, use_container_width=True, config={"displayModeBar": False},
                        key=f"chart_quickview_price_{ticker}")

    with col_metrics:
        st.markdown('<div class="qv-section-title">📊 MÉTRICAS CLAVE</div>', unsafe_allow_html=True)

        mcap = info.get("market_cap", 0) or 0
        if mcap >= 1e12:
            mcap_str = f"${mcap/1e12:.2f}T"
        elif mcap >= 1e9:
            mcap_str = f"${mcap/1e9:.1f}B"
        else:
            mcap_str = f"${mcap/1e6:.0f}M" if mcap > 0 else "—"

        pe = info.get("pe_ratio")
        pe_str = f"{pe:.1f}" if isinstance(pe, (int, float)) and pe > 0 else "—"

        fwd_pe = info.get("forward_pe")
        fwd_pe_str = f"{fwd_pe:.1f}" if isinstance(fwd_pe, (int, float)) and fwd_pe > 0 else "—"

        ps = info.get("ps_ratio")
        ps_str = f"{ps:.1f}" if isinstance(ps, (int, float)) and ps > 0 else "—"

        avg_vol = info.get("avg_volume", 0) or 0
        vol_str = f"{avg_vol/1e6:.1f}M" if avg_vol >= 1e6 else f"{avg_vol/1e3:.0f}K" if avg_vol > 0 else "—"

        beta = info.get("beta")
        beta_str = f"{beta:.2f}" if isinstance(beta, (int, float)) else "—"

        # dividend_yield YA viene en porcentaje (MSFT 0.78 = 0.78%). El ×100
        # que había aquí mostraba "242.00%" para KO y "509.00%" para O.
        div_yield = info.get("dividend_yield") or 0
        div_str = f"{div_yield:.2f}%" if div_yield > 0 else "—"

        metrics = [
            ("Market Cap",   mcap_str,   "#E2B25C"),
            ("P/E Trailing", pe_str,     "#6FA3E0"),
            ("P/E Forward",  fwd_pe_str, "#6FA3E0"),
            ("P/S",          ps_str,     "#9D8CE0"),
            ("Vol Promedio", vol_str,    "#E2B25C"),
            ("Beta",         beta_str,   "#9D8CE0"),
            ("Div Yield",    div_str,    "#3DD68C"),
        ]
        for label, val, color in metrics:
            st.markdown(f"""
            <div class="qv-metric">
                <span class="qv-metric-label">{label}</span>
                <span class="qv-metric-value" style="color:{color};">{val}</span>
            </div>
            """, unsafe_allow_html=True)

    # ── Row 2: Performance multi-timeframe ───────────────────────────
    st.markdown('<div class="qv-section-title" style="margin-top:8px;">⚡ PERFORMANCE</div>', unsafe_allow_html=True)
    perf_cols = st.columns(6, gap="small")
    range_pct = ((current_price - low_52w) / (high_52w - low_52w) * 100) if (high_52w - low_52w) > 0 else 50

    perf_data = [
        ("1D",  day_change,    "%"),
        ("1W",  week_change,   "%"),
        ("1M",  month_change,  "%"),
        ("1Y", year_change,    "%"),
        ("52W Range", range_pct, " pct"),
        ("52W H/L", None,      ""),
    ]

    for i, (label, val, suffix) in enumerate(perf_data):
        with perf_cols[i]:
            if label == "52W Range":
                color = "#E2B25C" if 20 < val < 80 else ("#3DD68C" if val >= 80 else "#F1495F")
                val_str = f"{val:.0f}%"
            elif label == "52W H/L":
                color = "#C9CDD3"
                val_str = f"${low_52w:.0f} / ${high_52w:.0f}"
            elif val is None:
                color = "#C9CDD3"
                val_str = "—"
            else:
                color = "#3DD68C" if val >= 0 else "#F1495F"
                ar = "▲" if val >= 0 else "▼"
                val_str = f"{ar} {abs(val):.1f}%"

            st.markdown(f"""
            <div class="qv-perf-tile">
                <div class="qv-perf-label">{label}</div>
                <div class="qv-perf-value" style="color:{color};">{val_str}</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Row 3: Noticias + Contexto ───────────────────────────────────
    col_news, col_ctx = st.columns([2, 1], gap="medium")

    with col_news:
        st.markdown('<div class="qv-section-title" style="margin-top:14px;">📰 NOTICIAS RECIENTES</div>', unsafe_allow_html=True)
        if news:
            for item in news[:5]:
                publisher = item.get("publisher", "—")
                title = item.get("title", "")
                age = item.get("age_hours", 0) or 0
                age_label = f"{age:.0f}h" if age < 48 else f"{age/24:.0f}d"
                freshness_emoji = "🔥" if age < 24 else "⚡" if age < 168 else "📅"
                link = item.get("link", "#")

                st.markdown(f"""
                <a href="{link}" target="_blank" class="qv-news-link">
                <div class="qv-news-item">
                    <div class="qv-news-meta">
                        <span class="qv-news-freshness">{freshness_emoji} {age_label}</span>
                        <span class="qv-news-publisher">{publisher}</span>
                    </div>
                    <div class="qv-news-title">{title}</div>
                </div>
                </a>
                """, unsafe_allow_html=True)
        else:
            st.markdown('<div class="qv-empty">Sin noticias recientes disponibles</div>', unsafe_allow_html=True)

    with col_ctx:
        st.markdown('<div class="qv-section-title" style="margin-top:14px;">🏭 CONTEXTO</div>', unsafe_allow_html=True)

        sector = info.get("sector", "—") or "—"
        industry = info.get("industry", "—") or "—"
        country = info.get("country", "—") or "—"
        employees = info.get("employees", 0) or 0
        emp_str = f"{employees:,}" if employees else "—"

        analyst_target = info.get("target_price")
        target_str = "—"
        if isinstance(analyst_target, (int, float)) and analyst_target > 0 and current_price > 0:
            upside = (analyst_target - current_price) / current_price * 100
            arrow_t = "▲" if upside >= 0 else "▼"
            target_str = f"${analyst_target:.2f} ({arrow_t} {abs(upside):.1f}%)"

        rating = (info.get("analyst_rating") or "—").upper()

        ctx_items = [
            ("Sector",   sector),
            ("Industria", industry[:30] + "..." if len(industry) > 30 else industry),
            ("País",     country),
            ("Empleados", emp_str),
            ("Target Analistas", target_str),
            ("Rating",   rating),
        ]
        for label, val in ctx_items:
            st.markdown(f"""
            <div class="qv-context-item">
                <span class="qv-context-label">{label}</span>
                <span class="qv-context-value">{val}</span>
            </div>
            """, unsafe_allow_html=True)

    # ── CTA: Lanzar análisis profundo ────────────────────────────────
    st.markdown('<div style="margin-top:24px;"></div>', unsafe_allow_html=True)
    _, cta_col, _ = st.columns([1, 2, 1])
    with cta_col:
        if st.button(
            f"EJECUTAR ANÁLISIS DLP DE {ticker}",
            use_container_width=True,
            key="qv_full_analysis",
            type="primary",
        ):
            st.session_state.quick_view_ticker = None
            run_analysis(ticker)


# ── Welcome / Central Hub ─────────────────────────────────────────────────
POPULAR_TICKERS = ["NVDA", "AAPL", "MSFT", "TSLA", "GOOGL", "META", "AMZN", "AMD", "AVGO", "NFLX", "COIN", "PLTR"]

# Universos del inicio por tipo de activo (pestañas ETF | ACCIONES | CRIPTO).
# Cada entrada es (símbolo_visible, ticker_de_datos): en acciones y ETFs
# coinciden; en cripto el dato viene del par de Yahoo (BTC → BTC-USD).
POPULAR_ETFS = ["SPY", "QQQ", "VOO", "SCHD", "IWM", "GLD", "VTI", "XLK", "XLE", "ARKK"]
POPULAR_CRYPTOS = [("BTC", "BTC-USD"), ("ETH", "ETH-USD"), ("SOL", "SOL-USD"),
                   ("XRP", "XRP-USD"), ("BNB", "BNB-USD"), ("DOGE", "DOGE-USD"),
                   ("ADA", "ADA-USD"), ("LINK", "LINK-USD"), ("AVAX", "AVAX-USD"),
                   ("LTC", "LTC-USD")]


def _sparkline_svg(closes, positive, w=56, h=18):
    """Mini-sparkline SVG puro (sin Plotly, ~300 bytes) con los cierres de los
    últimos 5 días que get_live_snapshot ya descarga. Devuelve "" si no hay al
    menos 2 puntos válidos — la tile simplemente no lo pinta. NUNCA lanza."""
    try:
        pts_in = [float(c) for c in (closes or [])
                  if isinstance(c, (int, float)) and c == c]
        if len(pts_in) < 2:
            return ""
        lo, hi = min(pts_in), max(pts_in)
        span = (hi - lo) or 1.0           # serie plana → línea recta, sin div/0
        pad = 1.5                         # aire para el stroke de 2px
        step = w / (len(pts_in) - 1)
        pts = " ".join(
            f"{i * step:.1f},{h - pad - (c - lo) / span * (h - 2 * pad):.1f}"
            for i, c in enumerate(pts_in)
        )
        color = "#3DD68C" if positive else "#F1495F"
        return (
            f'<svg class="tt-spark" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polygon points="0,{h} {pts} {w},{h}" fill="{color}" fill-opacity="0.08"/>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'vector-effect="non-scaling-stroke"/></svg>'
        )
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════
#  RENDERS DE ETF Y CRIPTO — secciones propias, componentes reutilizados
# ══════════════════════════════════════════════════════════════════════════

_SECTORES_ES = {
    "technology": "Tecnología", "financial_services": "Finanzas",
    "healthcare": "Salud", "consumer_cyclical": "Consumo cíclico",
    "consumer_defensive": "Consumo defensivo", "communication_services": "Comunicación",
    "industrials": "Industria", "energy": "Energía", "utilities": "Utilities",
    "realestate": "Inmobiliario", "basic_materials": "Materiales",
}


def _fmt_grande(v, prefijo="$"):
    """1234567890 → $1.2B. NUNCA lanza."""
    try:
        v = float(v)
        if v != v:
            return "—"
        if v >= 1e12:
            return f"{prefijo}{v/1e12:.2f}T"
        if v >= 1e9:
            return f"{prefijo}{v/1e9:.1f}B"
        if v >= 1e6:
            return f"{prefijo}{v/1e6:.0f}M"
        return f"{prefijo}{v:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _v_or(v, fmt="{:.2f}", sufijo="", default="—"):
    try:
        f = float(v)
        if f != f:
            return default
        return fmt.format(f) + sufijo
    except (TypeError, ValueError):
        return default


def _calificaciones_horizonte(analysis: StockAnalysis) -> list:
    """Calificación 0-100 por horizonte temporal, derivada de las secciones que
    de verdad pesan en cada plazo. Es la respuesta directa a '¿me interesa hoy,
    en unos meses, o como posición de años?'. Los huecos se excluyen y el resto
    reparte su peso (misma filosofía _pond). NUNCA lanza."""
    try:
        b = analysis.score_breakdown or {}

        def _g(k):
            v = b.get(k)
            return float(v) if isinstance(v, (int, float)) else None

        def _mezcla(pares):
            num = sum(v * w for v, w in pares if v is not None)
            den = sum(w for v, w in pares if v is not None)
            return round(num / den, 1) if den > 0 else None

        if analysis.asset_type == "crypto":
            tec, sent = _g("technical"), _g("crypto_sentimiento")
            tok, adop, rie = _g("crypto_tokenomics"), _g("crypto_adopcion"), _g("crypto_riesgo")
            filas = [
                ("Corto plazo · semanas", _mezcla([(tec, 0.7), (sent, 0.3)])),
                ("Medio plazo · meses", _mezcla([(tec, 0.3), (sent, 0.3), (rie, 0.4)])),
                ("Largo plazo · años", _mezcla([(tok, 0.5), (adop, 0.5)])),
            ]
        else:  # etf
            per, com = _g("etf_perfil"), _g("etf_composicion")
            ren, tec = _g("etf_rendimiento"), _g("technical")
            filas = [
                ("Corto plazo · semanas", _mezcla([(tec, 1.0)])),
                ("Medio plazo · meses", _mezcla([(ren, 0.5), (tec, 0.5)])),
                ("Largo plazo · años", _mezcla([(per, 0.35), (com, 0.25), (ren, 0.4)])),
            ]
        return [(n, v) for n, v in filas if v is not None]
    except Exception:
        return []


def _render_overview_activo(analysis: StockAnalysis, etiquetas: dict,
                            con_graficas: bool = False):
    """Overview compartido de ETF/cripto: score + desglose por sección + tesis
    + fortalezas/riesgos. Con `con_graficas=True` el número plano se sustituye
    por el gauge de la app + la calificación por horizonte temporal (corto,
    medio y largo plazo) — lo que un inversor quiere saber de un vistazo."""
    score = analysis.composite_score
    color = score_color(score)
    if con_graficas:
        c1, c2 = st.columns([1, 1.5], gap="small")
        with c1:
            try:
                fig = build_gauge(score, analysis.recommendation)
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_ov_gauge_{analysis.ticker}")
            except Exception:
                st.markdown(
                    f'<div style="text-align:center;font-family:JetBrains Mono;'
                    f'font-size:3rem;color:{color};">{score:.1f}</div>',
                    unsafe_allow_html=True)
        with c2:
            try:
                filas = _calificaciones_horizonte(analysis)
                if filas:
                    # Altura EXACTA del gauge de al lado (build_gauge = 360px):
                    # barras 336 + pie 24 = 360 → las dos tarjetas del Overview
                    # miden lo mismo y la fila queda cohesionada.
                    fig = build_metric_bars(
                        [(n, v, None) for n, v in filas],
                        height=336, title="Calificación por horizonte temporal",
                        color_by_score=True)
                    _plotly(fig, use_container_width=True,
                            config={"displayModeBar": False, "staticPlot": True},
                            key=f"chart_ov_horizontes_{analysis.ticker}")
                    st.markdown(
                        '<div style="font-size:0.68rem;color:#5E6570;text-align:center;'
                        'height:24px;line-height:24px;margin:-8px 0 0;">Cada horizonte pondera '
                        'las secciones que de verdad pesan en ese plazo</div>',
                        unsafe_allow_html=True)
            except Exception:
                pass
    else:
        st.markdown(
            f'<div style="text-align:center;padding:22px 16px;background:#0F1419;'
            f'border:1px solid #232830;border-radius:10px;border-top:3px solid {color};margin-bottom:14px;">'
            f'<div style="font-size:0.72rem;color:#8D949E;text-transform:uppercase;'
            f'letter-spacing:0.14em;">{analysis.company_name}</div>'
            f'<div style="font-family:JetBrains Mono;font-size:3.4rem;font-weight:700;'
            f'color:{color};line-height:1.15;">{score:.1f}</div>'
            f'<div style="font-size:0.8rem;color:{color};font-weight:600;'
            f'letter-spacing:0.08em;">{analysis.recommendation}</div>'
            f'</div>', unsafe_allow_html=True)

    # Desglose por sección (barras, mismo idioma que los sub-scores de Riesgo)
    st.markdown('<div class="section-title-bar">Desglose por Sección</div>',
                unsafe_allow_html=True)
    for k, v in (analysis.score_breakdown or {}).items():
        label = etiquetas.get(k, k.replace("_", " ").title())
        try:
            ancho = min(max(float(v), 0) / 100 * 100, 100)
        except (TypeError, ValueError):
            continue
        c = score_color(v)
        st.markdown(
            f'<div style="margin:7px 0;">'
            f'<div style="display:flex;justify-content:space-between;font-size:0.78rem;color:#C9CDD3;">'
            f'<span>{label}</span><span style="font-family:JetBrains Mono;color:{c};">{v:.0f}</span></div>'
            f'<div style="background:#232830;border-radius:3px;height:6px;margin-top:3px;">'
            f'<div style="background:{c};width:{ancho}%;height:100%;border-radius:3px;"></div>'
            f'</div></div>', unsafe_allow_html=True)

    if analysis.investment_thesis:
        st.markdown('<div class="section-title-bar">Tesis</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="analysis-card"><div class="analysis-text">{_no_latex(analysis.investment_thesis)}</div></div>',
            unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="section-title-bar">Fortalezas</div>', unsafe_allow_html=True)
        for s in (analysis.key_strengths or [])[:4]:
            st.markdown(f'<div class="risk-item" style="border-left-color:#3DD68C;">{s}</div>',
                        unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="section-title-bar">Riesgos</div>', unsafe_allow_html=True)
        for s in (analysis.key_risks or [])[:4]:
            st.markdown(f'<div class="risk-item">{s}</div>', unsafe_allow_html=True)


def render_overview_etf(analysis: StockAnalysis):
    _render_overview_activo(analysis, {
        "etf_perfil": "Perfil y Costes", "etf_composicion": "Composición",
        "etf_rendimiento": "Rendimiento y Riesgo", "technical": "Técnico"},
        con_graficas=True)


def render_overview_crypto(analysis: StockAnalysis):
    _render_overview_activo(analysis, {
        "crypto_tokenomics": "Tokenomics", "crypto_adopcion": "Adopción y Red",
        "crypto_sentimiento": "Sentimiento", "technical": "Técnico",
        "crypto_riesgo": "Riesgo"}, con_graficas=True)


def render_etf_perfil(analysis: StockAnalysis):
    report = analysis.reports.get("etf_perfil")
    if report is None:
        st.info("Perfil del ETF no disponible.")
        return
    _render_agent_header(report)
    km = report.key_metrics or {}
    ter = km.get("ter_pct")
    _render_metric_tiles([
        {"icon": "🏷️", "label": "TER (coste anual)",
         "value": _v_or(ter, "{:.2f}", "%"),
         "color": "#3DD68C" if isinstance(ter, (int, float)) and ter <= 0.2 else "#E2B25C",
         "tooltip": "Total Expense Ratio: lo que el fondo te cobra al año, pase lo que pase con el mercado. Es de las pocas cosas garantizadas de un ETF."},
        {"icon": "💎", "label": "Patrimonio (AUM)",
         "value": _fmt_grande(km.get("aum")), "color": "#E2B25C",
         "tooltip": "Activos bajo gestión. Por debajo de $100M existe riesgo real de cierre del fondo."},
        {"icon": "📅", "label": "Antigüedad",
         "value": _v_or(km.get("edad_anios"), "{:.0f}", " años"), "color": "#6FA3E0",
         "tooltip": "Años cotizando. Un historial largo permite juzgar el comportamiento en ciclos completos."},
        {"icon": "🏦", "label": "Gestora",
         "value": (str(km.get("gestora") or "—").split()[0] if km.get("gestora") else "—"),
         "color": "#C9CDD3",
         "tooltip": str(km.get("gestora") or "Gestora del fondo")},
        {"icon": "⚖️", "label": "Prima/Desc. NAV",
         "value": _v_or(km.get("prima_descuento_pct"), "{:+.2f}", "%"),
         "color": "#5E6570",
         "tooltip": "Diferencia entre el precio de mercado y el valor liquidativo (NAV). Cerca de 0% = el precio refleja fielmente la cartera."},
    ])
    # ── Visual: el coste anual comparado con su categoría ────────────────
    if km.get("ter_categoria_pct") is not None and ter is not None:
        g1, g2 = st.columns([1.4, 1], gap="small")
        with g1:
            try:
                fig = build_metric_bars(
                    [(analysis.ticker, ter, "#3DD68C" if ter <= km["ter_categoria_pct"] else "#F1495F"),
                     ("Media de su categoría", km["ter_categoria_pct"], "#5E6570")],
                    height=170, title="Coste anual (TER) comparado", x_format="%")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_etf_ter_{analysis.ticker}")
            except Exception:
                pass
        with g2:
            _render_insight_card(
                "Qué significa",
                f"Este ETF cuesta un <strong>{ter:.2f}%</strong> anual frente al "
                f"<strong>{km['ter_categoria_pct']:.2f}%</strong> medio de su categoría — "
                f"{'una fracción de lo habitual: la ventaja se acumula año tras año' if ter < km['ter_categoria_pct'] * 0.5 else 'en línea con lo habitual' if ter <= km['ter_categoria_pct'] else 'por encima de lo habitual: exige justificarlo con algo que los baratos no den'}.",
                color="#3DD68C" if ter <= km["ter_categoria_pct"] else "#E2B25C")
    _render_pros_cons(report, pros_title="✅ A favor", cons_title="⚠️ En contra")
    _render_analysis_card(report, title="Análisis del Perfil")


def render_etf_composicion(analysis: StockAnalysis):
    report = analysis.reports.get("etf_composicion")
    if report is None:
        st.info("Composición no disponible.")
        return
    _render_agent_header(report)
    km = report.key_metrics or {}
    _render_metric_tiles([
        {"icon": "🎯", "label": "Concentración Top-10",
         "value": _v_or(km.get("concentracion_top10_pct"), "{:.0f}", "%"),
         "color": "#F1495F" if isinstance(km.get("concentracion_top10_pct"), (int, float)) and km["concentracion_top10_pct"] >= 50 else "#3DD68C",
         "tooltip": "Peso conjunto de las 10 mayores posiciones. Alto = el fondo depende de pocas empresas."},
        {"icon": "🏭", "label": "Sector Dominante",
         "value": _SECTORES_ES.get(str(km.get("sector_dominante") or ""), str(km.get("sector_dominante") or "—")).split()[0],
         "color": "#E2B25C",
         "tooltip": "Sector con más peso en la cartera."},
        {"icon": "📊", "label": "Peso del Dominante",
         "value": _v_or(km.get("sector_dominante_pct"), "{:.0f}", "%"), "color": "#6FA3E0",
         "tooltip": "Cuánto pesa el sector dominante sobre el total."},
        {"icon": "🧩", "label": "Sectores >1%",
         "value": _v_or(km.get("n_sectores"), "{:.0f}"), "color": "#C9CDD3",
         "tooltip": "Número de sectores con presencia relevante. Más sectores = riesgo más repartido."},
    ])
    raw = report.raw_data or {}
    holdings = raw.get("top_holdings") or []
    sectores = raw.get("sectores") or {}
    if holdings:
        st.markdown('<div class="section-title-bar">Top 10 Posiciones</div>',
                    unsafe_allow_html=True)
        filas = "".join(
            f"<tr><td>{h[0]}</td><td>{h[1]}</td>"
            f"<td style='text-align:right;font-family:JetBrains Mono;'>{_v_or(h[2], '{:.2f}', '%')}</td></tr>"
            for h in holdings[:10])
        st.markdown(
            '<div class="analysis-card" style="padding:12px 16px;">'
            '<table style="width:100%;font-size:0.85rem;border-collapse:collapse;">'
            '<thead><tr style="color:#8D949E;font-size:0.72rem;text-transform:uppercase;">'
            '<th style="text-align:left;">Ticker</th><th style="text-align:left;">Empresa</th>'
            '<th style="text-align:right;">Peso</th></tr></thead>'
            f'<tbody>{filas}</tbody></table></div>', unsafe_allow_html=True)
    if sectores:
        st.markdown('<div class="section-title-bar">Distribución Sectorial</div>',
                    unsafe_allow_html=True)
        datos = [{"holder": _SECTORES_ES.get(k, k.replace("_", " ").title()), "pctHeld": v}
                 for k, v in sectores.items() if isinstance(v, (int, float)) and v > 0]
        if datos:
            fig = build_holders_bars(datos)
            _plotly(fig, use_container_width=True,
                    config={"displayModeBar": False, "staticPlot": True},
                    key=f"chart_etf_sectores_{analysis.ticker}")
    _render_pros_cons(report, pros_title="✅ A favor", cons_title="⚠️ En contra")
    _render_analysis_card(report, title="Análisis de la Composición")


def render_etf_rendimiento(analysis: StockAnalysis):
    report = analysis.reports.get("etf_rendimiento")
    if report is None:
        st.info("Rendimiento no disponible.")
        return
    _render_agent_header(report)
    km = report.key_metrics or {}
    _render_metric_tiles([
        {"icon": "📈", "label": "Retorno 3a (anual)",
         "value": _v_or(km.get("retorno_3y_pct"), "{:+.1f}", "%"),
         "color": "#3DD68C" if isinstance(km.get("retorno_3y_pct"), (int, float)) and km["retorno_3y_pct"] > 8 else "#E2B25C",
         "tooltip": "Retorno medio anualizado de los últimos 3 años."},
        {"icon": "📉", "label": "Caída Máxima",
         "value": _v_or(km.get("max_drawdown_pct"), "{:.1f}", "%"),
         "color": "#F1495F" if isinstance(km.get("max_drawdown_pct"), (int, float)) and km["max_drawdown_pct"] <= -25 else "#E2B25C",
         "tooltip": "El peor tramo del período analizado, de pico a valle. Es lo que habrías tenido que aguantar."},
        {"icon": "🌡️", "label": "Volatilidad",
         "value": _v_or(km.get("vol_anual_pct"), "{:.0f}", "%"), "color": "#6FA3E0",
         "tooltip": "Volatilidad anualizada del último año."},
        {"icon": "⚡", "label": "Sharpe aprox.",
         "value": _v_or(km.get("sharpe"), "{:.2f}"),
         "color": "#3DD68C" if isinstance(km.get("sharpe"), (int, float)) and km["sharpe"] > 0.7 else "#E2B25C",
         "tooltip": "Retorno por unidad de riesgo (aproximado, sobre T-bill). >1 es excelente."},
        {"icon": "💵", "label": "Yield",
         "value": _v_or(km.get("yield_pct"), "{:.2f}", "%"), "color": "#5E6570",
         "tooltip": "Rendimiento por dividendos del fondo en el último año. Solo informativo."},
    ])
    # ── Visual: retornos por plazo + la caída que hubo que aguantar ──────
    g1, g2 = st.columns(2, gap="small")
    with g1:
        try:
            items = []
            for etiqueta, clave in [("1 año", "retorno_1y_pct"),
                                    ("3 años (anual)", "retorno_3y_pct"),
                                    ("5 años (anual)", "retorno_5y_pct")]:
                v = km.get(clave)
                if isinstance(v, (int, float)):
                    items.append((etiqueta, v, "#3DD68C" if v >= 0 else "#F1495F"))
            if len(items) >= 2:
                fig = build_metric_bars(items, height=200,
                                        title="Retorno por plazo", x_format="%")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_etf_retornos_{analysis.ticker}")
        except Exception:
            pass
    with g2:
        try:
            from data.market_data import get_price_history
            df = get_price_history(analysis.ticker, period="1y")
            if df is not None and not df.empty and len(df) > 60:
                import pandas as _pd
                c = df["Close"].dropna()
                dd = (c / c.cummax() - 1.0) * 100.0
                _df = _pd.DataFrame({"Close": dd})
                fig = build_mountain_chart(_df, "Caída desde máximos · 12 meses", height=200,
                                           es_precio=False, prefijo_y="")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_etf_dd_{analysis.ticker}")
        except Exception:
            pass
    _render_pros_cons(report, pros_title="✅ A favor", cons_title="⚠️ En contra")
    _render_analysis_card(report, title="Análisis de Rendimiento y Riesgo")


def render_crypto_tokenomics(analysis: StockAnalysis):
    report = analysis.reports.get("crypto_tokenomics")
    if report is None:
        st.info("Tokenomics no disponible.")
        return
    _render_agent_header(report)
    km = report.key_metrics or {}
    emitido = km.get("pct_emitido")
    _render_metric_tiles([
        {"icon": "💎", "label": "Market Cap",
         "value": _fmt_grande(km.get("market_cap")), "color": "#E2B25C",
         "tooltip": "Capitalización total (precio × supply circulante)."},
        {"icon": "🏆", "label": "Ranking",
         "value": _v_or(km.get("rank"), "#{:.0f}"), "color": "#6FA3E0",
         "tooltip": "Posición por capitalización dentro del mercado cripto."},
        {"icon": "⛏️", "label": "Oferta Emitida",
         "value": _v_or(emitido, "{:.1f}", "%") if emitido is not None else "Sin tope",
         "color": "#3DD68C" if isinstance(emitido, (int, float)) and emitido >= 90 else "#E2B25C",
         "tooltip": "Porcentaje de la oferta máxima ya en circulación. 'Sin tope' = la moneda no tiene límite de emisión."},
        {"icon": "🗻", "label": "Dist. desde Máximos",
         "value": _v_or(km.get("ath_distancia_pct"), "{:.0f}", "%"),
         "color": "#F1495F" if isinstance(km.get("ath_distancia_pct"), (int, float)) and km["ath_distancia_pct"] <= -60 else "#E2B25C",
         "tooltip": "Cuánto está por debajo de su máximo histórico."},
        {"icon": "🌊", "label": "Rotación Diaria",
         "value": _v_or(km.get("turnover_pct"), "{:.1f}", "%"), "color": "#5E6570",
         "tooltip": "Volumen de 24h como % de la capitalización: liquidez real bajo el precio."},
    ])
    # ── Visual: estructura de oferta + posición en el ciclo ──────────────
    g1, g2 = st.columns(2, gap="small")
    with g1:
        try:
            if isinstance(emitido, (int, float)) and emitido > 0:
                fig = build_metric_bars(
                    [("Oferta emitida", emitido, "#3DD68C"),
                     ("Por emitir", max(100.0 - emitido, 0.0), "#5E6570")],
                    height=180, title="Estructura de oferta", x_format="%")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_tok_oferta_{analysis.ticker}")
            else:
                _render_insight_card(
                    "Oferta sin tope",
                    "Esta moneda no tiene un máximo de emisión: su oferta futura depende de "
                    "las reglas del protocolo (emisión, quemas, recompensas), no de un límite fijo.",
                    color="#E2B25C")
        except Exception:
            pass
    with g2:
        try:
            # La ASIMETRÍA DE LAS PÉRDIDAS: caer un 49% exige subir un 96%
            # para volver al mismo sitio. Es el dato que de verdad sitúa al
            # inversor en el ciclo (sustituye a un tacómetro que solo repetía
            # el tile de arriba).
            _athd = km.get("ath_distancia_pct")
            if isinstance(_athd, (int, float)) and _athd < -1:
                caida = min(abs(_athd), 99.0)
                recuperacion = (1.0 / (1.0 - caida / 100.0) - 1.0) * 100.0
                fig = build_metric_bars(
                    [("Caída acumulada", caida, "#F1495F"),
                     ("Subida necesaria para recuperar máximos", recuperacion, "#E2B25C")],
                    height=180, title="La asimetría de la recuperación", x_format="%")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_tok_recuperacion_{analysis.ticker}")
        except Exception:
            pass
    _render_pros_cons(report, pros_title="✅ A favor", cons_title="⚠️ En contra")
    _render_analysis_card(report, title="Análisis de Tokenomics")


def render_crypto_adopcion(analysis: StockAnalysis):
    report = analysis.reports.get("crypto_adopcion")
    if report is None:
        st.info("Adopción no disponible.")
        return
    _render_agent_header(report)
    km = report.key_metrics or {}
    if km.get("tvl_aplica"):
        tiles = [
            {"icon": "🔒", "label": "TVL de la Red",
             "value": _fmt_grande(km.get("tvl")), "color": "#E2B25C",
             "tooltip": "Total Value Locked: capital depositado y trabajando dentro de la red. El mejor proxy público de uso real."},
            {"icon": "📈", "label": "TVL en 30 días",
             "value": _v_or(km.get("tvl_delta_30d_pct"), "{:+.1f}", "%"),
             "color": "#3DD68C" if isinstance(km.get("tvl_delta_30d_pct"), (int, float)) and km["tvl_delta_30d_pct"] > 0 else "#F1495F",
             "tooltip": "Variación del TVL en el último mes: ¿entra o sale capital de la red?"},
            {"icon": "⚖️", "label": "TVL / Market Cap",
             "value": _v_or(km.get("tvl_mcap_ratio"), "{:.1f}", "%"), "color": "#6FA3E0",
             "tooltip": "Cuánto valor real trabaja en la red por cada dólar de capitalización."},
            {"icon": "🌊", "label": "Rotación Diaria",
             "value": _v_or(km.get("turnover_pct"), "{:.1f}", "%"), "color": "#5E6570",
             "tooltip": "Volumen 24h / market cap."},
        ]
    else:
        # 4º tile SIN guiones muertos: si la cadena tiene TVL (Bitcoin, $3.5B)
        # se muestra como dato informativo con su porqué; si no existe (XRP,
        # DOGE, LINK, SHIB) el hueco lo ocupa la capitalización — siempre un
        # dato real.
        tiles = [
            {"icon": "👑", "label": "Dominancia",
             "value": _v_or(km.get("dominancia_propia"), "{:.2f}", "%"), "color": "#E2B25C",
             "tooltip": "Porcentaje de TODO el mercado cripto que representa este activo."},
            {"icon": "🌊", "label": "Rotación Diaria",
             "value": _v_or(km.get("turnover_pct"), "{:.1f}", "%"), "color": "#6FA3E0",
             "tooltip": "Volumen de 24h como % de la capitalización: liquidez real bajo el precio."},
            {"icon": "💱", "label": "Volumen 24h",
             "value": _fmt_grande(km.get("volumen_24h")), "color": "#3DD68C",
             "tooltip": "Valor negociado en las últimas 24 horas, en todos los mercados."},
        ]
        if isinstance(km.get("tvl"), (int, float)) and km["tvl"] > 0:
            tiles.append({
                "icon": "🔒", "label": "TVL (informativo)",
                "value": _fmt_grande(km.get("tvl")), "color": "#5E6570",
                "tooltip": ("Valor bloqueado en aplicaciones sobre su red. En este activo es un dato "
                            "INFORMATIVO: no puntúa en la nota de adopción, porque su caso de uso no "
                            "es ser una cadena de contratos inteligentes.")})
        else:
            tiles.append({
                "icon": "💎", "label": "Market Cap",
                "value": _fmt_grande(km.get("market_cap")), "color": "#E2B25C",
                "tooltip": "Capitalización total (precio × oferta en circulación)."})
    _render_metric_tiles(tiles)

    # ── Visual: TVL de 12 meses (cadenas de contratos) o dominancia comparada ──
    try:
        from data.crypto_data import CRYPTO_UNIVERSO, get_tvl_serie, get_crypto_data
        _meta = CRYPTO_UNIVERSO.get(analysis.ticker) or {}
        if km.get("tvl_aplica") and _meta.get("cadena_defi"):
            serie = get_tvl_serie(_meta["cadena_defi"]) or []
            if len(serie) > 30:
                import pandas as _pd
                _df = _pd.DataFrame(
                    {"Close": [p[1] for p in serie]},
                    index=_pd.to_datetime([p[0] for p in serie], unit="s"))
                st.markdown('<div class="section-title-bar">Valor Bloqueado en la Red — 12 Meses</div>',
                            unsafe_allow_html=True)
                fig = build_mountain_chart(_df, f"TVL · {_meta.get('nombre', analysis.ticker)}", height=300,
                                           es_precio=False, prefijo_y="$")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_adop_tvl_{analysis.ticker}")
        else:
            d_live = get_crypto_data(analysis.ticker) or {}
            items = []
            if isinstance(km.get("dominancia_propia"), (int, float)):
                items.append((analysis.company_name or analysis.ticker,
                              km["dominancia_propia"], "#E2B25C"))
            if isinstance(d_live.get("dominancia_btc"), (int, float)) and analysis.ticker != "BTC":
                items.append(("Bitcoin", d_live["dominancia_btc"], "#5E6570"))
            if isinstance(d_live.get("dominancia_eth"), (int, float)) and analysis.ticker != "ETH":
                items.append(("Ethereum", d_live["dominancia_eth"], "#5E6570"))
            if len(items) >= 2:
                st.markdown('<div class="section-title-bar">Peso en el Mercado Cripto</div>',
                            unsafe_allow_html=True)
                fig = build_metric_bars(items, height=200,
                                        title="% de la capitalización total del mercado",
                                        x_format="%")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_adop_dom_{analysis.ticker}")
    except Exception:
        pass

    _render_pros_cons(report, pros_title="✅ A favor", cons_title="⚠️ En contra")
    _render_analysis_card(report, title="Análisis de Adopción y Red")


def render_crypto_sentimiento(analysis: StockAnalysis):
    report = analysis.reports.get("crypto_sentimiento")
    if report is None:
        st.info("Sentimiento no disponible.")
        return
    _render_agent_header(report)
    km = report.key_metrics or {}
    fng = km.get("fng_actual")
    fng_color = ("#F1495F" if isinstance(fng, (int, float)) and (fng <= 20 or fng >= 80)
                 else "#E2B25C" if isinstance(fng, (int, float)) and (fng <= 40 or fng >= 60)
                 else "#3DD68C")
    _render_metric_tiles([
        {"icon": "🧭", "label": "Fear & Greed",
         "value": _v_or(fng, "{:.0f}") + (f" · {km.get('fng_clasificacion')}" if km.get("fng_clasificacion") else ""),
         "color": fng_color,
         "tooltip": "Índice de miedo/codicia del mercado cripto (0 = pánico, 100 = euforia). Lectura útil: CONTRARIA."},
        {"icon": "⚡", "label": "Cambio 7 días",
         "value": _v_or(km.get("delta_7d_pct"), "{:+.1f}", "%"),
         "color": "#3DD68C" if isinstance(km.get("delta_7d_pct"), (int, float)) and km["delta_7d_pct"] >= 0 else "#F1495F",
         "tooltip": "Variación del precio en 7 días."},
        {"icon": "📅", "label": "Cambio 30 días",
         "value": _v_or(km.get("delta_30d_pct"), "{:+.1f}", "%"),
         "color": "#3DD68C" if isinstance(km.get("delta_30d_pct"), (int, float)) and km["delta_30d_pct"] >= 0 else "#F1495F",
         "tooltip": "Variación del precio en 30 días."},
        {"icon": "🗓️", "label": "En 1 año",
         "value": _v_or(km.get("delta_1y_pct"), "{:+.1f}", "%"),
         "color": "#3DD68C" if isinstance(km.get("delta_1y_pct"), (int, float)) and km["delta_1y_pct"] >= 0 else "#F1495F",
         "tooltip": "Variación del precio en 12 meses."},
    ])
    # ── Visual: termómetro del Miedo y Codicia + su serie de 30 días ─────
    g1, g2 = st.columns([1, 1.5], gap="small")
    with g1:
        try:
            if isinstance(fng, (int, float)):
                # Mismo instrumento que el gauge de sentimiento de acciones:
                # título y etiqueta DENTRO del dial (bien espaciados) y el
                # número grande sobre 100. Nada de títulos externos solapados.
                fig = build_fear_greed_gauge(float(fng), height=200)
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_sent_fng_{analysis.ticker}")
        except Exception:
            pass
    with g2:
        try:
            serie = (report.raw_data or {}).get("fng_serie") or []
            if len(serie) >= 10:
                import pandas as _pd
                from datetime import timedelta as _td
                vals = list(reversed(serie))               # antigua → reciente
                fechas = [datetime.now() - _td(days=len(vals) - 1 - i)
                          for i in range(len(vals))]
                _df = _pd.DataFrame({"Close": vals}, index=_pd.DatetimeIndex(fechas))
                fig = build_mountain_chart(_df, "Miedo y Codicia · 30 días", height=200, es_precio=False)
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_sent_serie_{analysis.ticker}")
        except Exception:
            pass
    if km.get("tendencia"):
        _render_insight_card("Tendencia de fondo",
                             f"La estructura de precio es <strong>{km['tendencia']}</strong>.",
                             color="#E2B25C")
    _render_pros_cons(report, pros_title="✅ A favor", cons_title="⚠️ En contra")
    _render_analysis_card(report, title="Análisis de Sentimiento")


def render_crypto_riesgo(analysis: StockAnalysis):
    report = analysis.reports.get("crypto_riesgo")
    if report is None:
        st.info("Riesgo no disponible.")
        return
    _render_agent_header(report)
    km = report.key_metrics or {}
    _render_metric_tiles([
        {"icon": "🌡️", "label": "Volatilidad Anual",
         "value": _v_or(km.get("vol_anual_pct"), "{:.0f}", "%"),
         "color": "#F1495F" if isinstance(km.get("vol_anual_pct"), (int, float)) and km["vol_anual_pct"] >= 80 else "#E2B25C",
         "tooltip": "Volatilidad anualizada. Como referencia: una acción típica ronda el 25-35%."},
        {"icon": "📉", "label": "Caída desde Máximos",
         "value": _v_or(km.get("ath_distancia_pct"), "{:.0f}", "%"),
         "color": "#F1495F" if isinstance(km.get("ath_distancia_pct"), (int, float)) and km["ath_distancia_pct"] <= -60 else "#E2B25C",
         "tooltip": "Distancia al máximo histórico: cuánto ha borrado ya este ciclo."},
        # Para el propio Bitcoin no hay correlación consigo mismo que valga:
        # se dice tal cual — "Es Bitcoin" — en vez de un guion o un 1.00.
        ({"icon": "🔗", "label": "Correlación c/ BTC",
          "value": "Es Bitcoin", "color": "#E2B25C",
          "tooltip": "Es el propio Bitcoin: la referencia contra la que se mide el resto del mercado cripto."}
         if analysis.ticker == "BTC" else
         {"icon": "🔗", "label": "Correlación c/ BTC",
          "value": _v_or(km.get("correlacion_btc_90d"), "{:.2f}"),
          "color": "#F1495F" if isinstance(km.get("correlacion_btc_90d"), (int, float)) and km["correlacion_btc_90d"] >= 0.85 else "#3DD68C",
          "tooltip": "Correlación de 90 días con Bitcoin. Alta = no diversifica dentro de cripto, es beta de BTC."}),
        {"icon": "🏛️", "label": "Correlación c/ Bolsa",
         "value": _v_or(km.get("correlacion_spy_90d"), "{:+.2f}"),
         "color": "#6FA3E0",
         "tooltip": "Correlación de 90 días con el S&P 500: cuánta diversificación aporta frente a una cartera de acciones."},
    ])
    # ── Visual: volatilidad comparada + curva de caída desde máximos ─────
    g1, g2 = st.columns(2, gap="small")
    with g1:
        try:
            items = []
            if isinstance(km.get("vol_anual_pct"), (int, float)):
                items.append((analysis.company_name or analysis.ticker,
                              km["vol_anual_pct"], "#E2B25C"))
            if isinstance(km.get("vol_btc_pct"), (int, float)) and analysis.ticker != "BTC":
                items.append(("Bitcoin", km["vol_btc_pct"], "#5E6570"))
            if isinstance(km.get("vol_spy_pct"), (int, float)):
                items.append(("S&P 500", km["vol_spy_pct"], "#6FA3E0"))
            if len(items) >= 2:
                fig = build_metric_bars(items, height=210,
                                        title="Volatilidad vs S&P 500",
                                        x_format="%")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_riesgo_vol_{analysis.ticker}")
        except Exception:
            pass
    with g2:
        try:
            from data.market_data import get_price_history
            df = get_price_history(analysis.ticker, period="1y")
            if df is not None and not df.empty and len(df) > 60:
                import pandas as _pd
                c = df["Close"].dropna()
                dd = (c / c.cummax() - 1.0) * 100.0
                _df = _pd.DataFrame({"Close": dd})
                fig = build_mountain_chart(_df, "Caída desde máximos · 12 meses", height=210,
                                           es_precio=False, prefijo_y="")
                _plotly(fig, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"chart_riesgo_dd_{analysis.ticker}")
        except Exception:
            pass
    _render_pros_cons(report, pros_title="✅ Aspectos favorables", cons_title="⚠️ Riesgos principales")
    _render_analysis_card(report, title="Análisis de Riesgo")


def render_welcome():
    # ── Tipo de activo activo (pestañas ETF | ACCIONES | CRIPTO) ──────────
    # El slider de abajo escribe en session_state ANTES del rerun, así que
    # leerlo aquí arriba (para el tagline) siempre da el valor correcto.
    _tipo = st.session_state.get("hero_tipo", "ACCIONES")
    _CFG_TIPO = {
        "ACCIONES": {
            "tagline": "Analiza en profundidad cualquier acción del NYSE & NASDAQ",
            "titulo": "◈ &nbsp;ANALIZA UNA ACCIÓN",
            "sub": "Busca una acción por su ticker en el mercado: &nbsp;TSLA · NVDA · AAPL",
            "ph": "TSLA · NVDA · AAPL…",
            "lista": [(t, t) for t in POPULAR_TICKERS[:10]],
        },
        "ETF": {
            "tagline": "Analiza en profundidad cualquier ETF domiciliado en EE.UU.",
            "titulo": "◈ &nbsp;ANALIZA UN ETF",
            "sub": "Busca un ETF por su ticker: &nbsp;SPY · QQQ · CSPX · VWCE",
            "ph": "SPY · QQQ · CSPX…",
            "lista": [(t, t) for t in POPULAR_ETFS],
        },
        "CRIPTO": {
            "tagline": "Analiza en profundidad las principales criptomonedas",
            "titulo": "◈ &nbsp;ANALIZA UNA CRIPTO",
            "sub": "Busca una cripto por su símbolo: &nbsp;BTC · ETH · SOL",
            "ph": "BTC · ETH · SOL…",
            "lista": POPULAR_CRYPTOS,
        },
    }
    _cfg = _CFG_TIPO.get(_tipo, _CFG_TIPO["ACCIONES"])

    # Hero
    st.markdown(f"""
    <div class="alpha-hero">
        <div class="alpha-hero-brand">◈ DLP MARKET ANALYZER</div>
        <div class="alpha-hero-tagline">{_cfg["tagline"]}</div>
        <div class="alpha-divider"></div>
    </div>
    """, unsafe_allow_html=True)

    # ── Action Card central — usa casi todo el ancho del viewport.
    # En iframe cuadrado de Whop antes se cortaba "ESCANEAR EL MERCAD" — ahora
    # con un centro de 96% + botones con padding compacto, "ESCANEAR EL MERCADO"
    # cabe completo siempre.
    _, center_col, _ = st.columns([1, 50, 1])

    with center_col:
        # ── BARRA SUPERIOR: slider de tipo + píldora del escáner ──────────
        # Dos piezas visualmente separadas (decisión de UX): a la izquierda el
        # slider ETF | ACCIONES | CRIPTO (Acciones al centro y por defecto,
        # mismo lenguaje visual que el menú de secciones de los análisis), y a
        # la derecha, aislada, la píldora "Escanear el Mercado" con su radar
        # barriendo de izquierda a derecha — es el NUEVO acceso al escáner.
        with st.container(key="herobar"):
            bar_izq, _bar_gap, bar_der = st.columns([2.6, 0.14, 1.15], gap="small")
            with bar_izq:
                with st.container(key="herotipo"):
                    st.radio("Tipo de activo", ["ETF", "ACCIONES", "CRIPTO"],
                             index=1, horizontal=True, key="hero_tipo",
                             label_visibility="collapsed")
            with bar_der:
                with st.container(key="scanpill"):
                    scan_btn = st.button("◎  ESCANEAR EL MERCADO",
                                         use_container_width=True,
                                         key="hero_scan")

        # El container keyed (st-key-herocard) es el ANCLA CSS de toda la
        # card — antes el CSS colgaba del TEXTO del placeholder (frágil:
        # cambiar el copy rompía la card entera). Ahora el copy es libre.
        with st.container(key="herocard"):
            # ── UNA SOLA INTENCIÓN: ANALIZA (el escáner vive arriba) ─────
            # El contenido (titular, subtítulo, tape, placeholder) depende de
            # la pestaña activa del slider.
            _tape_tickers = [y for _, y in _cfg["lista"]]
            _tape_nombres = {y: s for s, y in _cfg["lista"]}
            if True:
                st.markdown(
                    f'<div class="hz-title">{_cfg["titulo"]}</div>'
                    f'<div class="hz-sub">{_cfg["sub"]}</div>',
                    unsafe_allow_html=True)

                # Ticker-tape estilo pantalla de trading floor: precios en
                # vivo desfilando en LED ámbar. Usa SOLO el caché del snapshot
                # (cero red, cero espera); sin datos aún → solo los símbolos.
                try:
                    from data.market_data import get_live_snapshot_cached
                    _tape_snap = get_live_snapshot_cached(_tape_tickers)
                except Exception:
                    _tape_snap = {}
                _items = []
                for _tk in _tape_tickers:
                    _d = _tape_snap.get(_tk, {})
                    _p = _d.get("price")
                    _c = _d.get("change_pct", 0) or 0
                    _vis = _tape_nombres.get(_tk, _tk)
                    if _p:
                        _fl = "▲" if _c >= 0 else "▼"
                        _items.append(
                            f'<span class="hz-tape-item">{_vis} '
                            f'{_p:,.2f} <span class="hz-tape-chg">{_fl}'
                            f'{abs(_c):.2f}%</span></span>')
                    else:
                        _items.append(f'<span class="hz-tape-item">{_vis}</span>')
                _tape_html = '<span class="hz-tape-sep">·</span>'.join(_items)
                st.markdown(
                    f'<div class="hz-tape" aria-hidden="true"><div class="hz-tape-track">'
                    f'{_tape_html}<span class="hz-tape-sep">·</span>{_tape_html}'
                    f'<span class="hz-tape-sep">·</span></div></div>',
                    unsafe_allow_html=True)

                # st.form → Enter en el input dispara el submit (Análisis DLP):
                # el gesto universal "escribo el ticker y pulso Enter".
                with st.form(key="hero_form", border=False, enter_to_submit=True):
                    ticker_input = st.text_input(
                        label="Ticker",
                        label_visibility="collapsed",
                        placeholder=_cfg["ph"],
                        key="hero_ticker_input",
                    ).upper().strip()
                    analyze_btn = st.form_submit_button(
                        "🔍  Análisis DLP", use_container_width=True, type="primary")
                st.markdown('<div class="cta-hint">escribe un ticker y pulsa '
                            'Enter</div>', unsafe_allow_html=True)

        if analyze_btn and ticker_input:
            run_analysis(ticker_input)
        if scan_btn:
            # Abre la página de configuración del scanner (no corre scan directo)
            st.session_state.scanner_config_open = True
            st.rerun()

    # ── Quick Access Tickers (universo de la pestaña activa) ───────────
    _titulo_grid = {"ACCIONES": "Tickers Populares", "ETF": "ETFs Populares",
                    "CRIPTO": "Criptos Principales"}.get(_tipo, "Tickers Populares")
    st.markdown(f'<div class="section-header">⊕  Acceso Rápido — {_titulo_grid}</div>', unsafe_allow_html=True)

    # Skeleton del grid mientras cargan los precios (~1-3s) — la página ya
    # muestra la ESTRUCTURA final (10 placeholders shimmer) en vez de una
    # cinta que no dice nada. Sin salto de layout al hidratarse.
    tickers_loader = st.empty()
    tickers_loader.markdown(
        '<div class="qt-skel-grid">'
        + '<div class="qt-skel skeleton-block"></div>' * 10
        + '</div>',
        unsafe_allow_html=True,
    )

    from data.market_data import get_live_snapshot
    snapshot = {}
    try:
        snapshot = get_live_snapshot(_tape_tickers)
    except Exception:
        pass

    # Quitar el skeleton — vamos a renderizar las cards reales abajo
    tickers_loader.empty()

    # Grid 5 cols x 2 rows — tarjetas amplias, 100% clicables, con sparkline
    # intradía (5 días, textura real de mercado) y footer ▾ que invita al clic.
    _lista_grid = _cfg["lista"][:10]
    rows = [_lista_grid[:5], _lista_grid[5:10]]
    for row_idx, row in enumerate(rows):
        cols = st.columns(5, gap="small")
        for i, (simbolo, ticker) in enumerate(row):
            with cols[i]:
                data = snapshot.get(ticker, {})
                price = data.get("price")
                change = data.get("change_pct", 0) or 0

                change_color = "#3DD68C" if change >= 0 else "#F1495F"
                arrow = "▲" if change >= 0 else "▼"
                price_str = f"${price:,.2f}" if price else "—"
                change_str = f"{arrow} {abs(change):.2f}%" if price else "—"

                tk_safe = "".join(c if (c.isalnum() or c in "_-") else "_"
                                  for c in ticker)
                with st.container(key=f"qtile_{tk_safe}"):
                    st.markdown(
                        f'<div class="qt-head"><span class="tt-symbol">{simbolo}</span>'
                        f'<span class="tt-change" style="color:{change_color};">{change_str}</span></div>'
                        f'<div class="qt-price">{price_str}</div>'
                        f'{_sparkline_svg(data.get("closes"), change >= 0, w=120, h=30)}'
                        f'<div class="qt-foot">▾&nbsp;&nbsp;<span class="qt-foot-txt">VER TODO</span></div>',
                        unsafe_allow_html=True,
                    )
                    # Overlay invisible: TODA la tarjeta es clicable.
                    # «VER TODO» abre SIEMPRE el dashboard rápido INFORMATIVO
                    # (quick view) — nunca lanza ni abre un análisis: para eso
                    # están el buscador y el historial del sidebar.
                    # SIN help=: el tooltip envuelve el botón en wrappers
                    # (stTooltipHoverTarget) que NO se estiran → el botón solo
                    # cubría una franja de ~38px arriba y el clic real del
                    # ratón en el resto de la tarjeta (p. ej. «VER TODO») caía
                    # al vacío. Sin tooltip la cadena es directa y el overlay
                    # cubre la tarjeta ENTERA.
                    if st.button(f"◈ {simbolo}", key=f"qtilebtn_{tk_safe}"):
                        st.session_state.quick_view_ticker = ticker
                        st.session_state.selected_ticker = None
                        st.rerun()

    # ── Live Market Pulse + Rotación Sectorial (bloque macro instantáneo) ──
    # Sin cintas de carga: pinta desde el snapshot y refresca solo (fragmento).
    _render_bloque_macro()


def _bloque_macro_datos():
    """(datos, actualizando) para el bloque macro del inicio. NUNCA lanza.

    Máquina de estados en `session_state`:
      · sin datos aún  → devuelve el SNAPSHOT y pide una segunda pasada.
      · segunda pasada → hace la llamada real (los segundos de red ocurren aquí
        dentro, no en el primer pintado), guarda el snapshot nuevo y termina.
      · ya listo       → repinta desde memoria, sin red.
    Si no hay snapshot (primera vez de todas), carga en vivo directamente: el
    miembro espera como antes, pero nunca ve un hueco.

    Memoria: el estado guarda UN dict macro pequeño (~2-3 KB) y el snapshot es
    UN único fichero en .cache que se sobrescribe — nunca se acumula nada."""
    estado = st.session_state.setdefault("_macro_estado", {"fase": "inicio"})

    if estado["fase"] == "listo":
        # El tick del fragmento no se desperdicia: pasados unos minutos se
        # vuelve a refrescar, así el inicio se mantiene al día solo mientras
        # alguien lo tenga abierto. `get_macro_data` tiene su propia caché de
        # 1 h, así que estas re-comprobaciones casi siempre son gratis.
        if (time.time() - float(estado.get("t", 0) or 0)) > 600:
            estado["fase"] = "refrescando"
        else:
            return estado.get("datos") or {}, False

    if estado["fase"] == "inicio":
        try:
            from data.market_data import get_macro_snapshot
            snap = get_macro_snapshot()
        except Exception:
            snap = None
        if snap:
            # Hay último registro: se pinta ya y se refresca en la siguiente
            # pasada del fragmento.
            estado["fase"] = "refrescando"
            estado["datos"] = snap
            return snap, True
        # Sin snapshot: no queda otra que cargar en vivo aquí mismo.
        estado["fase"] = "refrescando"

    # fase "refrescando": traer los datos reales
    try:
        from data.market_data import get_macro_data
        frescos = get_macro_data() or {}
    except Exception:
        frescos = {}
    if frescos.get("sector_performance"):
        try:
            from data.market_data import save_macro_snapshot
            # Sobrescribe el snapshot anterior: solo existe el más actualizado.
            save_macro_snapshot(frescos)
        except Exception:
            pass
        estado["datos"] = frescos
    estado["fase"] = "listo"
    estado["t"] = time.time()
    return estado.get("datos") or {}, False


# `run_every` hace que el fragmento vuelva solo: ahí es donde se hace la
# llamada real, sin bloquear el primer pintado. Una vez en fase "listo" los
# repintados son locales (sin red) y con datos idénticos, así que no se ve
# movimiento en pantalla.
# MEMORIA: a 3s eran ~1.200 repintados/hora por pestaña abierta, la sesión
# nunca quedaba ociosa y el RSS subía en escalera. A 100s son ~36/hora (-97%)
# sin cambiar nada más. El refresco real tarda 100s en llegar, pero
# get_macro_data() tiene TTL de 1h: si la caché está caliente el refresco
# devuelve lo mismo. Y el primer arranque sin snapshot NO depende del tick
# (carga en vivo directa).
@st.fragment(run_every=100)
def _render_bloque_macro():
    macro, actualizando = _bloque_macro_datos()

    # Indicador DISCRETO de que se está refrescando — nunca una cinta que tape
    # el contenido, que es justo lo que se quería quitar.
    _punto = ('<span style="color:#5E6570;font-size:0.6rem;font-family:JetBrains Mono;'
              'letter-spacing:0.08em;margin-left:10px;">actualizando…</span>'
              if actualizando else "")
    st.markdown(f'<div class="section-header">El Mercado en Vivo{_punto}</div>',
                unsafe_allow_html=True)

    # (label, key del macro, formato del valor)
    pulse_items = [
        ("S&P 500",   "sp500",  "index"),     # puntos del índice ^GSPC
        ("NASDAQ",    "nasdaq", "index"),     # puntos del índice ^IXIC
        ("VIX",       "vix",    "vol"),       # nivel del VIX
        ("DXY",       "dxy",    "dollar"),    # US Dollar Index
        ("10Y YIELD", "tnx",    "yield"),     # rendimiento Treasury en %
        ("GOLD",      "gold",   "price"),     # precio en USD por onza
    ]

    def _format_pulse(curr, fmt):
        """Formato COMPACTO para que NUNCA se rompa el número en cards
        angostas del iframe cuadrado. Ej: 7383.74 → '7,384' (sin decimales
        si >= 1000) o 25709 → '25.7K'."""
        if not isinstance(curr, (int, float)):
            return "—"
        if fmt == "yield":
            return f"{curr:.2f}%"
        if fmt == "price":
            # Gold: $4,353 (sin decimales si >= 1000)
            if curr >= 1000:
                return f"${curr:,.0f}"
            return f"${curr:,.2f}"
        if fmt == "index":
            # S&P 500, NASDAQ: 7,384 / 25,709 (sin decimales — cabe mejor)
            return f"{curr:,.0f}"
        # vol, dollar y default: 2 decimales (cabe siempre)
        return f"{curr:.2f}"

    cols = st.columns(6, gap="small")
    for i, (label, key, fmt) in enumerate(pulse_items):
        data = macro.get(key, {})
        if not isinstance(data, dict):
            data = {}
        curr = data.get("current")
        chg = data.get("1m_change", 0) or 0
        change_color = "#3DD68C" if chg >= 0 else "#F1495F"
        change_symbol = "▲" if chg >= 0 else "▼"

        val_str = _format_pulse(curr, fmt)
        chg_str = f"{change_symbol} {abs(chg):.2f}% (1M)" if isinstance(curr, (int, float)) else "—"

        anim_delay = i * 0.05

        with cols[i]:
            st.markdown(f"""
            <div class="market-pulse-card" style="animation-delay:{anim_delay}s;">
                <div class="pulse-label">{label}</div>
                <div class="pulse-value">{val_str}</div>
                <div class="pulse-change" style="color:{change_color};">{chg_str}</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Sector Performance — sin cinta: los datos ya vienen resueltos ──
    sector_perf = macro.get("sector_performance", {}) if macro else {}
    if sector_perf:
        st.markdown('<div class="section-header">Rotación Sectorial (1Y)</div>', unsafe_allow_html=True)
        _plotly(build_sector_rotation(sector_perf), use_container_width=True,
                config={"displayModeBar": False},
                key="chart_welcome_sector_heatmap")


# ── Main App ──────────────────────────────────────────────────────────────
def _apply_sidebar_collapse():
    """Si la columna está minimizada: la oculta (el contenido principal se
    reajusta solo) y muestra un botón «»» arriba a la izquierda para reabrirla.
    Puramente visual — no toca ningún dato ni flujo; el sidebar se sigue
    renderizando (estado intacto), solo se oculta con CSS."""
    if not st.session_state.get("sidebar_collapsed"):
        return
    # Ocultar el sidebar. Mayor especificidad (body …) para ganar al ancho fijo.
    st.markdown(
        "<style>body [data-testid='stSidebar'],"
        "body section[data-testid='stSidebar']{display:none !important;}</style>",
        unsafe_allow_html=True,
    )
    # Botón para reabrir (arriba a la izquierda, fijo vía CSS).
    if st.button("»", key="sidebar_expand_btn"):
        st.session_state.sidebar_collapsed = False
        st.rerun()


def main():
    # Sidebar lateral persistente con Home + Historial (análisis + escaneos).
    # Se renderiza SIEMPRE en cada vista; el contenido viene de disco así
    # que sobrevive a reinicios de la app.
    render_sidebar()
    _apply_sidebar_collapse()

    render_header()

    # El botón "Volver al Home" del top-nav solo aparece en las vistas de
    # escaneo (resultados / configuración). En la vista de ANÁLISIS ya no hay
    # botón superior — se usa el de la barra lateral izquierda (desplegable).
    in_welcome = (
        not st.session_state.get("selected_ticker") and
        not st.session_state.get("quick_view_ticker") and
        not st.session_state.scan_results and
        not st.session_state.get("scanner_config_open") and
        not st.session_state.get("_show_scan_results")
    )
    has_selected_analysis = (
        st.session_state.get("selected_ticker") in (st.session_state.get("analyses") or {})
    )
    _en_escaner = (st.session_state.get("scanner_config_open")
                   or st.session_state.get("_show_scan_results")
                   or bool(st.session_state.scan_results))
    if (not in_welcome) and (not has_selected_analysis) and (not _en_escaner):
        render_top_nav()

    selected = st.session_state.selected_ticker
    qv = st.session_state.get("quick_view_ticker")

    # Prioridad: Quick View > Full Analysis > Scanner Config > Scan Results > Welcome
    # El quick view se muestra SIEMPRE que esté pedido — también para tickers
    # ya analizados («VER TODO» es la vista informativa; el análisis completo
    # se abre desde el buscador, el sidebar o el CTA del propio quick view).
    if qv:
        render_quick_view(qv)
        return

    # ── Recuperación ANTI-EXPULSIÓN: si hay un análisis seleccionado pero la
    # poda de memoria (se conservan solo los N más recientes) lo sacó del dict,
    # cualquier rerun —contraer el sidebar, por ejemplo— devolvía al inicio
    # "sin sentido". Antes de rendirse, se re-carga desde disco y la vista se
    # mantiene EXACTA.
    if selected and selected not in st.session_state.analyses:
        try:
            from data.persistence import load_all_analyses
            _disco = load_all_analyses() or {}
            if selected in _disco:
                st.session_state.analyses[selected] = _disco[selected]
        except Exception:
            pass

    if not selected or selected not in st.session_state.analyses:
        # Si el scanner config está abierto, mostrarlo (tiene prioridad sobre scan_results y welcome)
        if st.session_state.get("scanner_config_open"):
            render_scanner_config()
            return
        if st.session_state.scan_results or st.session_state.get("_show_scan_results"):
            render_scan_results()
        else:
            render_welcome()
        return

    analysis = st.session_state.analyses[selected]

    # (El botón "Volver al Home" superior se eliminó: se usa el de la barra
    # lateral izquierda, que es desplegable. Evita duplicar la acción.)

    # Botón "← Volver al Scan" — visible cuando hay resultados de scan activos
    if st.session_state.scan_results:
        col_back, col_spacer = st.columns([1, 5])
        with col_back:
            with st.container(key="scnav_backscan"):
                if st.button("← Volver al radar", key="back_to_scan"):
                    st.session_state.selected_ticker = None
                    st.session_state.quick_view_ticker = None
                    st.rerun()

    # Header del ticker (premium)
    rec_badge = get_recommendation_badge(analysis.recommendation)
    score = analysis.composite_score
    color = score_color(score)
    compound_badge = ('<span class="compound-machine-badge">💎 COMPOUNDER</span>'
                      if getattr(analysis, "is_compound_machine", False) else "")

    # Etiqueta del TIPO de activo, siempre visible arriba: el usuario sabe de
    # un vistazo si está mirando una acción, un ETF (US o UCITS) o una cripto.
    _tipo_txt = {"accion": "ACCIÓN", "etf": "ETF", "crypto": "CRIPTO"}.get(
        getattr(analysis, "asset_type", "accion") or "accion", "ACCIÓN")
    try:
        _pk = ((analysis.reports.get("etf_perfil") or None) and
               (analysis.reports["etf_perfil"].key_metrics or {})) or {}
        if _tipo_txt == "ETF" and _pk.get("ucits"):
            _tipo_txt = "ETF UCITS"
    except Exception:
        pass
    tipo_badge = f'<span class="asset-tipo-badge">{_tipo_txt}</span>'

    st.markdown(
        f'<div class="stock-header">'
        f'<span class="stock-header-ticker">{analysis.ticker}</span>'
        f'{tipo_badge}'
        f'<span class="stock-header-name">{analysis.company_name}</span>'
        f'<span>{rec_badge}</span>'
        f'{compound_badge}'
        f'<span class="stock-header-score" style="color:{color};">{score:.1f}<span style="font-size:0.75rem;color:#8D949E;font-weight:400;">/100</span></span>'
        f'<span style="color:#5E6570;font-family:JetBrains Mono;font-size:0.7rem;">{analysis.timestamp[:10]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Tabs principales
    # ── Barra de secciones (estilo APP-PROYECCION-PORTAFOLIO) ────────────────
    # Radio horizontal CENTRADO como píldora: cada opción lleva su puntito y la
    # activa se marca con borde redondeado dorado + brillo (CSS .st-key-sectbar_).
    # Sustituye a st.tabs manteniendo INTACTAS todas las render functions. Con
    # key POR TICKER cada análisis recuerda en qué sección estabas (st.tabs no
    # acepta key y se reiniciaba al cambiar de acción).
    # Secciones según el tipo de activo. Acciones conserva EXACTAMENTE las
    # suyas; ETF y cripto tienen su propio juego (análisis distintos por diseño).
    _tipo_analisis = getattr(analysis, "asset_type", "accion") or "accion"
    sections = {
        "accion": ["Overview", "Técnico", "Fundamentales", "Futuro",
                   "Smart Money", "Contexto del Mercado", "Riesgo"],
        "etf": ["Overview", "Técnico", "Perfil y Costes", "Composición",
                "Rendimiento"],
        "crypto": ["Overview", "Técnico", "Tokenomics", "Adopción y Red",
                   "Sentimiento", "Riesgo"],
    }.get(_tipo_analisis, ["Overview", "Técnico", "Fundamentales", "Futuro",
                           "Smart Money", "Contexto del Mercado", "Riesgo"])
    # El key del container se vuelve clase CSS (st-key-…): solo chars seguros
    # (tickers como BRK.B llevarían un punto inválido en un class name).
    _tk_safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in analysis.ticker)
    sect_key = f"sect_{analysis.ticker}"
    if st.session_state.get(sect_key) not in sections:
        st.session_state[sect_key] = sections[0]
    with st.container(key=f"sectbar_{_tk_safe}"):
        st.radio("Sección", sections, key=sect_key, horizontal=True,
                 label_visibility="collapsed")
    sect = st.session_state.get(sect_key) or sections[0]

    # ── Despacho ETF ─────────────────────────────────────────────────────
    if _tipo_analisis == "etf":
        _aviso_ucits(analysis)     # solo pinta algo si el fondo es UCITS
        if sect == "Overview":
            render_overview_etf(analysis)
            _render_disclaimer()
        elif sect == "Perfil y Costes":
            render_etf_perfil(analysis)
        elif sect == "Composición":
            render_etf_composicion(analysis)
        elif sect == "Rendimiento":
            render_etf_rendimiento(analysis)
            # Es la sección de riesgo del ETF → mismo aviso que en Riesgo.
            _render_disclaimer()
        elif sect == "Técnico":
            render_technical(analysis)
        return

    # ── Despacho CRIPTO ──────────────────────────────────────────────────
    if _tipo_analisis == "crypto":
        if sect == "Overview":
            render_overview_crypto(analysis)
            _render_disclaimer()
        elif sect == "Tokenomics":
            render_crypto_tokenomics(analysis)
        elif sect == "Adopción y Red":
            render_crypto_adopcion(analysis)
        elif sect == "Sentimiento":
            render_crypto_sentimiento(analysis)
        elif sect == "Técnico":
            render_technical(analysis)
        elif sect == "Riesgo":
            render_crypto_riesgo(analysis)
            _render_disclaimer()
        return

    if sect == "Overview":
        render_overview(analysis)
        # El aviso legal se pinta AQUÍ y no dentro de render_overview para que
        # aparezca al final del todo pase lo que pase: si la función corta antes
        # por falta de datos, el disclaimer sigue estando.
        _render_disclaimer()
    elif sect == "Técnico":
        render_technical(analysis)
    elif sect == "Fundamentales":
        render_fundamentals(analysis)
    elif sect == "Futuro":
        render_future(analysis)
    elif sect == "Smart Money":
        render_institutional(analysis)
    elif sect == "Contexto del Mercado":
        # Contexto del Mercado = Catalizadores + Macro + Sentimiento.
        # Cada render function se mantiene INTACTA (todas sus gráficas/tiles/gauge).
        # Los 3 reportes vienen ahora del agente combinado market_context, pero con
        # estructura idéntica, así que cada render sigue leyendo reports["catalysts"],
        # reports["macro"] y reports["sentiment"] sin cambios.
        render_catalysts(analysis)
        st.markdown('<div style="margin:28px 0;border-top:1px solid #232830;"></div>',
                    unsafe_allow_html=True)
        render_macro(analysis)
        st.markdown('<div style="margin:28px 0;border-top:1px solid #232830;"></div>',
                    unsafe_allow_html=True)
        render_sentiment(analysis)
    elif sect == "Riesgo":
        render_risk(analysis)
        _render_disclaimer()


if __name__ == "__main__":
    main()
