"""
Componentes de visualización: gráfica de precios con indicadores,
tachómetro compuesto, snowflake radar, y mini-charts del sidebar.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Paleta del sistema (espejo de los tokens CSS de styles.py) ────────────
# Plotly no entiende var(--x): estos hex DEBEN coincidir con :root de styles.py.
BG_MAIN  = "#0A0B0D"                     # --bg
BG_CARD  = "#101216"                     # --surface-1
# Panel "instrumento": negro más profundo que el fondo, SOLO para las gráficas
# de calificación (gauges, radar, breakdown, pilares) → efecto pantalla dentro
# de su tarjeta, con contraste sutil frente al borde de la card.
PANEL_BG = "#07080B"
HAIRLINE = "rgba(255,255,255,0.07)"      # divisores finos del rediseño
GRID     = "rgba(255,255,255,0.05)"      # rejilla casi invisible (Tufte)
TEXT     = "#C9CDD3"                     # --text
MUTED    = "#8D949E"                     # --text-2
GREEN    = "#3DD68C"                     # --pos
RED      = "#F1495F"                     # --neg
ORANGE   = "#E2B25C"                     # --accent (oro antiguo)
BLUE     = "#6FA3E0"                     # --info
PURPLE   = "#9D8CE0"                     # dato categórico
YELLOW   = "#F0C878"                     # --accent-hi
WHITE    = "#F2F3F5"                     # --text-hi

# Radio de las esquinas de TODAS las barras. Porcentual (no px) para que se vea
# igual de redondeado sea cual sea el grosor de la barra: un valor fijo en px se
# volvía invisible en barras altas y exagerado en barras finas.
BAR_RADIUS = "30%"

PLOTLY_LAYOUT = dict(
    paper_bgcolor=BG_MAIN,
    plot_bgcolor=BG_MAIN,
    font=dict(color=TEXT, family="JetBrains Mono, monospace", size=11),
    xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, showgrid=True,
               linecolor="rgba(255,255,255,0.08)", tickfont=dict(color=MUTED, size=10)),
    yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, showgrid=True,
               linecolor="rgba(0,0,0,0)", tickfont=dict(color=MUTED, size=10)),
    margin=dict(l=10, r=10, t=40, b=10),
    hovermode="x unified",
    hoverlabel=dict(bgcolor="#15181D", bordercolor="rgba(255,255,255,0.10)",
                    font=dict(family="JetBrains Mono, monospace", size=11, color=TEXT)),
)


# ── Gráfica Principal: OHLCV + Indicadores ────────────────────────────────

def build_price_chart(df_daily: pd.DataFrame, indicators: dict, ticker: str) -> go.Figure:
    """
    Gráfica profesional estilo Bloomberg: candlesticks + MAs + RSI + MACD + Volumen.
    """
    if df_daily is None or df_daily.empty:
        fig = go.Figure()
        fig.add_annotation(text="Sin datos de precio disponibles", x=0.5, y=0.5, showarrow=False, font=dict(color=MUTED))
        fig.update_layout(**PLOTLY_LAYOUT, height=600)
        return fig

    df = df_daily.copy()
    if isinstance(df.index, pd.DatetimeIndex):
        dates = df.index
    else:
        dates = pd.to_datetime(df.index)

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    open_ = df["Open"]
    vol   = df["Volume"]

    # Calcular MAs sobre el df actual
    ma20  = close.rolling(20).mean()
    ma50  = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()

    # RSI
    try:
        import ta as ta_lib
        rsi = ta_lib.momentum.RSIIndicator(close, window=14).rsi()
        macd_ind    = ta_lib.trend.MACD(close)
        macd_line   = macd_ind.macd()
        macd_signal = macd_ind.macd_signal()
        macd_hist   = macd_ind.macd_diff()
    except Exception:
        rsi = None
        macd_line = macd_signal = macd_hist = None

    # 4 subplots: Precio | Volumen | RSI | MACD
    # NOTA: el título del subplot 1 ("NVDA — Precio") va vacío para evitar
    # que se solape con la leyenda horizontal (que también va arriba del
    # subplot 1). El título se agrega abajo como annotation custom.
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        # Espaciado mayor entre subgráficas para que los títulos (Volumen /
        # RSI 14 / MACD) tengan su propio hueco y NO se solapen con la gráfica
        # de arriba. Con 0.02 el "RSI 14" caía encima del volumen.
        vertical_spacing=0.05,
        row_heights=[0.55, 0.15, 0.15, 0.15],
        subplot_titles=["", "Volumen", "RSI 14", "MACD"],
    )

    # ── Candlesticks ──────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=dates,
        open=open_, high=high, low=low, close=close,
        name="OHLC",
        increasing_line_color=GREEN,
        decreasing_line_color=RED,
        increasing_fillcolor=GREEN,
        decreasing_fillcolor=RED,
        line=dict(width=1),
        whiskerwidth=0.4,
    ), row=1, col=1)

    # ── Moving Averages ───────────────────────────────────────────────────
    ma_styles = [
        (ma20,  "#6FA3E0",  "MA 20",  1.2),
        (ma50,  "#F0C878",  "MA 50",  1.5),
        (ma150, "#E0703F",  "MA 150", 1.5),
        (ma200, "#F1495F",  "MA 200", 2.0),
    ]
    for ma, color, name, width in ma_styles:
        fig.add_trace(go.Scatter(
            x=dates, y=ma, mode="lines", name=name,
            line=dict(color=color, width=width),
            opacity=0.85,
        ), row=1, col=1)

    # ── 52W High/Low annotations ──────────────────────────────────────────
    high_52w = indicators.get("52w_high")
    low_52w  = indicators.get("52w_low")
    if high_52w:
        fig.add_hline(y=high_52w, line_dash="dash", line_color=ORANGE,
                      annotation_text=f"52W High ${high_52w:.2f}",
                      annotation_font_color=ORANGE,
                      annotation_position="bottom right", row=1, col=1)
    if low_52w:
        fig.add_hline(y=low_52w, line_dash="dash", line_color=MUTED,
                      annotation_text=f"52W Low ${low_52w:.2f}",
                      annotation_font_color=MUTED,
                      annotation_position="top right", row=1, col=1)

    # ── Volumen con color según vela ──────────────────────────────────────
    vol_colors = [GREEN if c >= o else RED for c, o in zip(close, open_)]
    fig.add_trace(go.Bar(
        x=dates, y=vol, name="Volumen",
        marker_color=vol_colors, marker_opacity=0.6,
        showlegend=False,
    ), row=2, col=1)

    # Avg volume line
    avg_vol = vol.rolling(20).mean()
    fig.add_trace(go.Scatter(
        x=dates, y=avg_vol, mode="lines", name="Vol MA20",
        line=dict(color=YELLOW, width=1.5, dash="dot"),
        showlegend=False,
    ), row=2, col=1)

    # ── RSI ───────────────────────────────────────────────────────────────
    if rsi is not None:
        fig.add_trace(go.Scatter(
            x=dates, y=rsi, mode="lines", name="RSI 14",
            line=dict(color=PURPLE, width=1.5),
            showlegend=False,
        ), row=3, col=1)
        # Zonas sobrecompra/sobreventa
        for level, color, label in [(70, RED, "70"), (30, GREEN, "30")]:
            fig.add_hline(y=level, line_dash="dot", line_color=color,
                          line_width=1, opacity=0.6, row=3, col=1)
        fig.update_yaxes(range=[0, 100], row=3, col=1)

    # ── MACD ──────────────────────────────────────────────────────────────
    if macd_line is not None:
        # Histograma MACD
        hist_colors = [GREEN if v >= 0 else RED for v in (macd_hist.fillna(0) if macd_hist is not None else [])]
        fig.add_trace(go.Bar(
            x=dates, y=macd_hist, name="MACD Hist",
            marker_color=hist_colors, marker_opacity=0.7,
            showlegend=False,
        ), row=4, col=1)
        fig.add_trace(go.Scatter(
            x=dates, y=macd_line, mode="lines", name="MACD",
            line=dict(color=BLUE, width=1.5),
            showlegend=False,
        ), row=4, col=1)
        fig.add_trace(go.Scatter(
            x=dates, y=macd_signal, mode="lines", name="Signal",
            line=dict(color=ORANGE, width=1.5, dash="dot"),
            showlegend=False,
        ), row=4, col=1)
        fig.add_hline(y=0, line_color=MUTED, line_width=1, opacity=0.5, row=4, col=1)

    # Título "TICKER — Precio" como annotation DEBAJO de la leyenda,
    # alineado a la izquierda del subplot 1 para que NUNCA se solape con
    # la leyenda horizontal que vive arriba del subplot.
    fig.add_annotation(
        text=f"<b>{ticker} — Precio</b>",
        xref="x domain", yref="y domain",
        x=0.01, y=0.97,
        xanchor="left", yanchor="top",
        showarrow=False,
        font=dict(size=12, color=TEXT, family="JetBrains Mono, monospace"),
        bgcolor="rgba(10,11,13,0.7)",
        bordercolor="rgba(226,178,92,0.20)",
        borderwidth=1,
        borderpad=4,
        row=1, col=1,
    )

    # ── Layout ────────────────────────────────────────────────────────────
    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        plot_bgcolor=BG_MAIN,
        font=dict(color=TEXT, family="JetBrains Mono, monospace", size=11),
        height=700,
        hovermode="x unified",
        dragmode=False,   # sin arrastre/zoom por arrastre; el hover se conserva
        xaxis_rangeslider_visible=False,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=10, color=TEXT),
            bgcolor="rgba(10,11,13,0.6)",
            bordercolor="rgba(226,178,92,0.15)",
            borderwidth=1,
        ),
        margin=dict(l=10, r=10, t=55, b=10),
    )

    # Colores de ejes
    for i in range(1, 5):
        fig.update_xaxes(
            gridcolor=GRID, zerolinecolor=GRID,
            tickfont=dict(color=MUTED, size=9),
            row=i, col=1,
        )
        fig.update_yaxes(
            gridcolor=GRID, zerolinecolor=GRID,
            tickfont=dict(color=MUTED, size=9),
            row=i, col=1,
        )

    return fig


def _hex_rgb(hex_color: str) -> str:
    """'#3DD68C' → '61,214,140' (para componer rgba() en Plotly)."""
    h = hex_color.lstrip("#")
    return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def _score_color(s) -> str:
    """Color de un puntaje 0-100 en la MISMA escala del termómetro
    (rojo→ámbar→verde, de peor a mejor). Fuente única de verdad para todas
    las barras que representan una calificación."""
    try:
        s = float(s)
    except (TypeError, ValueError):
        return MUTED
    if s >= 80:
        return "#3DD68C"   # --pos
    if s >= 65:
        return "#63DFA3"
    if s >= 50:
        return "#E2B25C"   # --accent
    if s >= 35:
        return "#E0854E"
    return "#F1495F"       # --neg


def _thermo_rgba(x: float, alpha: float = 0.15, stops=None) -> str:
    """Color CONTINUO del termómetro en x∈[0,100] → 'rgba(r,g,b,a)'.
    Interpola linealmente entre los stops (por defecto los umbrales de
    _score_color) para poder pintar arcos con gradiente suave — textura de
    instrumento, no zonas planas ni LEDs."""
    stops = stops or [
        (0,   (241, 73, 95)),    # rojo
        (35,  (224, 133, 78)),   # naranja
        (50,  (226, 178, 92)),   # ámbar
        (65,  (99, 223, 163)),   # verde claro
        (80,  (61, 214, 140)),   # verde
        (100, (61, 214, 140)),
    ]
    x = max(stops[0][0], min(stops[-1][0], float(x)))
    for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
        if x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            r, g, b = (round(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
            return f"rgba({r},{g},{b},{alpha})"
    r, g, b = stops[-1][1]
    return f"rgba({r},{g},{b},{alpha})"


def _gauge_gradient_steps(n: int = 60, alpha: float = 0.16, stops=None) -> list:
    """Fondo del arco de un gauge como gradiente CONTINUO (n micro-pasos sin
    hueco → se lee como un degradado, no como segmentos)."""
    w = 100.0 / n
    return [{"range": [i * w, (i + 1) * w],
             "color": _thermo_rgba((i + 0.5) * w, alpha, stops)} for i in range(n)]


def build_mountain_chart(df_daily: pd.DataFrame, ticker: str, height: int = 560) -> go.Figure:
    """
    Versión simplificada: una sola línea de precio de cierre con un degradado
    suave debajo (gráfica tipo 'mountain'). Sin medias, sin RSI, sin MACD.

    El degradado se construye apilando varias bandas rellenas con `tonexty` y
    opacidad creciente hacia la línea. Se hace así a propósito, en lugar de
    usar `fillgradient`, porque las bandas apiladas funcionan en cualquier
    versión de Plotly.js.
    """
    if df_daily is None or df_daily.empty:
        fig = go.Figure()
        fig.add_annotation(text="Sin datos de precio disponibles", x=0.5, y=0.5,
                           showarrow=False, font=dict(color=MUTED))
        fig.update_layout(**PLOTLY_LAYOUT, height=height)
        return fig

    df = df_daily.copy()
    dates = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)
    close = df["Close"].astype(float)

    # Color según el rendimiento del periodo completo
    up = float(close.iloc[-1]) >= float(close.iloc[0])
    line_hex = GREEN if up else RED
    rgb = _hex_rgb(line_hex)

    lo, hi = float(close.min()), float(close.max())
    span = (hi - lo) or (hi * 0.02 or 1.0)
    y_lo = lo - span * 0.18          # suelo del degradado (bajo el eje visible)
    y_hi = hi + span * 0.10

    fig = go.Figure()

    # ── Degradado ──────────────────────────────────────────────────────────
    # Se corta el área bajo la línea en estratos HORIZONTALES (niveles de
    # precio fijos), no en bandas que sigan la curva: si siguen la curva se ven
    # los escalones como las curvas de nivel de un mapa. Cada traza recorta el
    # cierre a su nivel y rellena `tonexty` contra la anterior, de modo que
    # cada estrato pinta una franja limpia. La opacidad sube con la altura →
    # brillo intenso pegado a la línea que se desvanece hacia abajo.
    n_bands = 26
    levels = np.linspace(y_lo, hi, n_bands + 1)

    fig.add_trace(go.Scatter(
        x=dates, y=np.full(len(close), y_lo), mode="lines",
        line=dict(width=0, color=f"rgba({rgb},0)"),
        hoverinfo="skip", showlegend=False,
    ))
    for k in range(1, n_bands + 1):
        frac = k / n_bands
        alpha = 0.30 * (frac ** 2.0)     # invisible abajo, vivo junto a la línea
        fig.add_trace(go.Scatter(
            x=dates, y=close.clip(lower=y_lo, upper=levels[k]), mode="lines",
            line=dict(width=0, color=f"rgba({rgb},0)"),
            fill="tonexty", fillcolor=f"rgba({rgb},{alpha:.4f})",
            hoverinfo="skip", showlegend=False,
        ))

    # ── Halo difuso justo bajo la línea, para el efecto de brillo ──────────
    fig.add_trace(go.Scatter(
        x=dates, y=close, mode="lines",
        line=dict(color=f"rgba({rgb},0.18)", width=6, shape="spline", smoothing=0.4),
        hoverinfo="skip", showlegend=False,
    ))

    # ── Línea de precio (la única traza con hover) ─────────────────────────
    fig.add_trace(go.Scatter(
        x=dates, y=close, mode="lines", name=ticker,
        line=dict(color=line_hex, width=2, shape="spline", smoothing=0.4),
        hovertemplate="%{x|%d %b %Y}<br><b>$%{y:,.2f}</b><extra></extra>",
        showlegend=False,
    ))

    # Etiqueta de esquina, igual que en la gráfica de velas
    fig.add_annotation(
        text=f"<b>{ticker} — Precio</b>",
        xref="x domain", yref="y domain",
        x=0.01, y=0.97, xanchor="left", yanchor="top",
        showarrow=False,
        font=dict(size=12, color=TEXT, family="JetBrains Mono, monospace"),
        bgcolor="rgba(10,11,13,0.7)",
        bordercolor="rgba(226,178,92,0.20)",
        borderwidth=1, borderpad=4,
    )

    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        plot_bgcolor=BG_MAIN,
        font=dict(color=TEXT, family="JetBrains Mono, monospace", size=11),
        height=height,
        hovermode="x unified",
        dragmode=False,   # sin arrastre/zoom; el hover se conserva
        showlegend=False,
        margin=dict(l=10, r=10, t=30, b=10),
        hoverlabel=dict(bgcolor="rgba(16,18,22,0.95)", bordercolor=f"rgba({rgb},0.35)",
                        font=dict(color=TEXT, family="JetBrains Mono, monospace", size=11)),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID,
                     tickfont=dict(color=MUTED, size=9), showspikes=False)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID,
                     tickfont=dict(color=MUTED, size=9),
                     tickprefix="$", range=[y_lo, y_hi])
    return fig


# ── Tachómetro / Gauge ────────────────────────────────────────────────────

def build_gauge(score: float, recommendation: str) -> go.Figure:
    """Tachómetro DLP Score con el número GRANDE separado del arco para
    garantizar que NUNCA se solape (issue conocido de Plotly gauge+number
    en figuras pequeñas)."""

    rec_colors = {
        "STRONG BUY": "#3DD68C",
        "BUY":        "#6FA3E0",
        "WATCH":      "#E2B25C",
        "PASS":       "#F1495F",
    }
    rec_color = rec_colors.get(recommendation, "#E2B25C")
    # El arco y el número usan el color del TERMÓMETRO del score (fuente única
    # _score_color); el veredicto conserva su propio color semántico.
    sc = _score_color(score)

    # Gauge SIN número — el arco vive en la parte superior de la figura
    # (domain y=[0.32, 1.0]) dejando espacio limpio abajo para el número.
    # Estética "instrumento de precisión": arco FINO sobre un anillo casi negro,
    # bandas termómetro muy tenues alineadas con los umbrales de _score_color
    # (35/50/65/80), ticks hairline en mono y aguja blanca fina. Sobrio, sin glow.
    fig = go.Figure(go.Indicator(
        mode="gauge",
        value=score,
        domain={"x": [0, 1], "y": [0.32, 1.0]},
        title={
            "text": (f"<span style='color:{MUTED}'>DLP SCORE</span><br>"
                     f"<span style='font-size:0.68em;color:{rec_color}'><b>{recommendation}</b></span>"),
            "font": {"size": 13, "color": MUTED, "family": "JetBrains Mono"},
        },
        gauge={
            "axis": {
                "range": [0, 100],
                "tickwidth": 1,
                "tickcolor": "rgba(255,255,255,0.30)",
                "ticklen": 6,
                "tickfont": {"color": MUTED, "size": 8.5, "family": "JetBrains Mono"},
                "dtick": 20,
            },
            # Arco del score sobre el degradado de fondo — presencia sin gritar.
            "bar": {"color": sc, "thickness": 0.30},
            # Anillo con cuerpo (más claro que el panel) + BORDE fino dorado:
            # el dial queda enmarcado, como un instrumento real.
            "bgcolor": "#0D1015",
            "borderwidth": 1,
            "bordercolor": "rgba(226,178,92,0.22)",
            # TEXTURA: degradado térmico CONTINUO (60 micro-pasos) — se lee como
            # un barrido rojo→ámbar→verde suave bajo el arco, sin zonas planas.
            "steps": _gauge_gradient_steps(n=60, alpha=0.16),
            # Aguja: marca blanca fina en el score exacto.
            "threshold": {
                "line": {"color": WHITE, "width": 2},
                "thickness": 0.94,
                "value": score,
            },
        },
    ))

    # Número grande COMO ANNOTATION SEPARADA — vive en y=0.12 (bottom 12%)
    # debajo del arco del gauge. Imposible que se solape. Mono tabular, color
    # del termómetro, "/100" tenue.
    fig.add_annotation(
        x=0.5, y=0.12,
        xref="paper", yref="paper",
        text=f"<b>{score:.0f}</b><span style='font-size:0.4em;color:{MUTED}'>/100</span>",
        showarrow=False,
        font=dict(size=52, color=sc, family="JetBrains Mono"),
        align="center",
    )

    fig.update_layout(
        paper_bgcolor=PANEL_BG,
        plot_bgcolor=PANEL_BG,
        font=dict(color=TEXT),
        height=360,   # un poco más grande dentro de su tarjeta
        # Márgenes SIMÉTRICOS → el gauge (domain x=[0,1]) y el número (x=0.5)
        # quedan CENTRADOS en la tarjeta. Antes un margen asimétrico (r=95) lo
        # empujaba a la izquierda; con la tarjeta envolvente debe ir centrado.
        margin=dict(l=50, r=50, t=54, b=16),
    )

    return fig


# ── Snowflake Radar ────────────────────────────────────────────────────────

def build_snowflake(snowflake: dict) -> go.Figure:
    """
    Radar chart estilo SimplyWallSt: 5 dimensiones de calidad (0-20 cada una).
    """
    # Labels SIN emoji (identidad sobria de la app: los íconos viven en los
    # chips SVG, no dentro de las gráficas).
    categories = {
        "value":    "Valor",
        "quality":  "Calidad",
        "growth":   "Crecimiento",
        "momentum": "Momentum",
        "future":   "Futuro",
    }

    labels = [categories.get(k, k) for k in ["value", "quality", "growth", "momentum", "future"]]
    values = [snowflake.get(k, 10) for k in ["value", "quality", "growth", "momentum", "future"]]
    values_closed = values + [values[0]]
    labels_closed = labels + [labels[0]]

    # Color según score total
    total = sum(values)
    if total >= 70:
        fill_color = "rgba(61,214,140,0.15)"
        line_color = GREEN
    elif total >= 50:
        fill_color = "rgba(226,178,92,0.15)"
        line_color = ORANGE
    else:
        fill_color = "rgba(241,73,95,0.15)"
        line_color = RED

    # Etiqueta en DOS LÍNEAS: el nombre arriba y, DEBAJO, la calificación en
    # número grande coloreado por su propio score (valor 0-20 → escala 0-100 →
    # _score_color). Así se lee de un vistazo qué dimensión es fuerte o débil,
    # y al ir apiladas ocupan mucho menos ancho → no se cortan en los extremos.
    combined = [
        f"<span style='font-size:11px'>{labels[i]}</span><br>"
        f"<b><span style='font-size:17px;color:{_score_color(values[i] * 5)}'>{int(values[i])}</span></b>"
        f"<span style='font-size:9px;color:{MUTED}'>/20</span>"
        for i in range(len(labels))
    ]
    combined_closed = combined + [combined[0]]

    fig = go.Figure()

    # customdata (nombre limpio, valor /20 y equivalente /100) — lo comparten la
    # traza de datos y la del borde exterior, para que el pop-up sea idéntico
    # se pase el ratón por el vértice o por la ETIQUETA.
    _cd = [[labels[i], int(values[i]), int(round(values[i] * 5))] for i in range(len(labels))]
    _cd_closed = _cd + [_cd[0]]
    _HOVER_TPL = (
        "<span style='font-size:11px;color:#8D949E;letter-spacing:0.08em'>"
        "%{customdata[0]}</span><br>"
        "<span style='font-size:7px'> </span><br>"
        "<b><span style='font-size:23px'>%{customdata[1]}</span></b>"
        "<span style='font-size:12px;color:#8D949E'>/20</span>"
        "   <span style='font-size:11px;color:#8D949E'>· %{customdata[2]}/100</span>"
        "<extra></extra>"
    )

    # Área de fondo (escala máxima) — disco CON PRESENCIA sobre el panel negro:
    # el pentágono de referencia debe VERSE (fondo + contorno definidos).
    # Sus vértices están en la MISMA dirección que cada etiqueta, así que se les
    # da el mismo pop-up: junto con hoverdistance (abajo), pasar el ratón por el
    # texto de la categoría/calificación muestra el tooltip igual que en la
    # gráfica. Los marcadores van invisibles (solo zona de hover).
    fig.add_trace(go.Scatterpolar(
        r=[20] * len(combined_closed),
        theta=combined_closed,
        customdata=_cd_closed,
        fill="toself",
        fillcolor="rgba(255,255,255,0.045)",
        line=dict(color="rgba(255,255,255,0.16)", width=1.4),
        mode="lines+markers",
        marker=dict(size=26, color="rgba(0,0,0,0)"),
        showlegend=False,
        hoveron="points",          # sin hover del relleno (salía "trace 2")
        hovertemplate=_HOVER_TPL,
    ))

    # Underlay de PROFUNDIDAD (no neón): la misma silueta con trazo ancho a muy
    # baja opacidad, debajo de la línea principal. Da cuerpo sin brillo.
    fig.add_trace(go.Scatterpolar(
        r=values_closed,
        theta=combined_closed,
        mode="lines",
        line=dict(color=f"rgba({_hex_rgb(line_color)},0.12)", width=7),
        fill=None,
        showlegend=False,
        hoverinfo="skip",
    ))

    # Score actual (sin texto en vértices — el valor ya está en el angular label)
    fig.add_trace(go.Scatterpolar(
        r=values_closed,
        theta=combined_closed,
        customdata=_cd_closed,
        fill="toself",
        fillcolor=fill_color,
        line=dict(color=line_color, width=2.5),
        mode="lines+markers",
        marker=dict(size=9, color=line_color, line=dict(color=PANEL_BG, width=2)),
        showlegend=False,
        # Pop-up: categoría arriba en mono espaciado y la calificación DEBAJO en
        # grande, con su equivalente sobre 100. Misma identidad visual que el
        # resto de la app (mono + oro/termómetro sobre superficie oscura).
        hovertemplate=_HOVER_TPL,
    ))

    # Etiquetas como TRAZA DE TEXTO dentro del área polar: así el pop-up sale
    # también al pasar el ratón por el nombre o la calificación.
    _ang = [90 - i * (360 / len(labels)) for i in range(len(labels))]
    fig.add_trace(go.Scatterpolar(
        r=[23.2] * len(labels),
        theta=combined,          # mismas categorías (posición angular)
        customdata=_cd,
        mode="markers+text",
        text=combined,
        textposition="middle center",
        textfont=dict(size=11, color=TEXT, family="Inter"),
        marker=dict(size=34, color="rgba(0,0,0,0)"),
        showlegend=False,
        hoveron="points",
        hovertemplate=_HOVER_TPL,
        cliponaxis=False,
    ))

    fig.update_layout(
        polar=dict(
            # Domain ENCOGIDO y simétrico: deja un cinturón libre alrededor del
            # disco para que las etiquetas (nombre + calificación) quepan SIEMPRE
            # DENTRO del recuadro de la gráfica, sin cortarse en los extremos.
            domain={"x": [0.04, 0.96], "y": [0.03, 0.97]},
            bgcolor="#0B0D11",          # disco algo más claro que el panel: se VE
            radialaxis=dict(
                # El rango llega a 28 pero SOLO se dibujan anillos hasta 20: el
                # área activa (donde Plotly escucha el ratón) se extiende más
                # allá del disco visible, de modo que las etiquetas caen DENTRO
                # y también responden al hover.
                range=[0, 28],
                showticklabels=False,   # Sin 5/10/15/20 — el valor va en la etiqueta
                showline=False,
                gridcolor="rgba(255,255,255,0.09)",   # anillos visibles
                tickvals=[5, 10, 15, 20],
            ),
            angularaxis=dict(
                # Las etiquetas ya NO son ticks del eje (quedaban FUERA del área
                # que Plotly escucha y no se podía hacer hover sobre el texto):
                # se dibujan como una traza de texto DENTRO del área polar.
                showticklabels=False,
                tickfont=dict(size=11, color=TEXT, family="Inter"),
                gridcolor="rgba(255,255,255,0.05)",   # radios sutiles
                linecolor="rgba(0,0,0,0)",           # sin aro exterior visible
            ),
        ),
        paper_bgcolor=PANEL_BG,
        font=dict(color=TEXT),
        height=410,                                    # más alto: cabe todo holgado
        margin=dict(l=10, r=10, t=46, b=16),           # el aire lo da el domain
        title=dict(
            text="<b>PERFIL DE CALIDAD</b>",
            font=dict(color=MUTED, size=11, family="JetBrains Mono"),
            x=0.5,
        ),
        # Solo se resalta el punto más cercano (nada de tooltips múltiples).
        hovermode="closest",
        # Alcance amplio del hover: permite que el pop-up salga también al pasar
        # el ratón por el TEXTO de la categoría/calificación, que queda algo por
        # fuera del aro (el punto más cercano es siempre el de su propio radio).
        hoverdistance=52,
        hoverlabel=dict(
            bgcolor="#12151A",
            bordercolor="rgba(226,178,92,0.55)",
            font=dict(size=13, color=TEXT, family="JetBrains Mono"),
            align="left",
        ),
        showlegend=False,
    )

    return fig


# ── Score Breakdown Bar Chart ──────────────────────────────────────────────

def build_score_breakdown(score_breakdown: dict) -> go.Figure:
    """Desglose horizontal premium: barras con gradiente, zonas de calidad, sin tonterías."""

    # Prefijo = el MISMO código de sección que su pestaña (FN, TC, FU…), en oro,
    # en lugar de un emoji. Refleja el badge que aparece dentro de cada sección.
    def _lbl(code, name):
        return f"<span style='color:#E2B25C'><b>{code}</b></span>  {name}"
    agent_display = {
        "fundamentals":  _lbl("FN", "Fundamentales"),
        "technical":     _lbl("TC", "Técnico"),
        "future":        _lbl("FU", "Futuro"),
        "institutional": _lbl("SM", "Smart Money"),
        "catalysts":     _lbl("CT", "Catalizadores"),
        "macro":         _lbl("MC", "Macro"),
        "sentiment":     _lbl("SN", "Sentimiento"),
        "risk":          _lbl("RS", "Riesgo"),
    }
    order = ["fundamentals", "technical", "future", "institutional",
             "catalysts", "macro", "sentiment", "risk"]

    names  = [agent_display[k] for k in order]
    scores = [float(score_breakdown.get(k, 50)) for k in order]

    bar_colors = [_score_color(s) for s in scores]
    n = len(names)

    fig = go.Figure()

    # Zonas de calidad (background) — muy tenues: contexto, no decoración
    fig.add_vrect(x0=0,  x1=50,  fillcolor="rgba(241,73,95,0.03)", line_width=0)
    fig.add_vrect(x0=50, x1=65,  fillcolor="rgba(226,178,92,0.03)", line_width=0)
    fig.add_vrect(x0=65, x1=80,  fillcolor="rgba(111,163,224,0.03)", line_width=0)
    fig.add_vrect(x0=80, x1=100, fillcolor="rgba(61,214,140,0.04)", line_width=0)

    # Barras background (riel oscuro) — para dar profundidad
    fig.add_trace(go.Bar(
        y=names,
        x=[100] * n,
        orientation="h",
        marker=dict(color="rgba(255,255,255,0.03)", line=dict(width=0), cornerradius=BAR_RADIUS),
        showlegend=False,
        hoverinfo="skip",
        width=0.5,
    ))

    # Barras de score reales (encima) — SIN número al final: la calificación
    # vive en su propio panel a la derecha (más corta la barra, más limpia).
    fig.add_trace(go.Bar(
        y=names,
        x=scores,
        orientation="h",
        marker=dict(
            color=bar_colors,
            line=dict(color="rgba(255,255,255,0.10)", width=1),
            opacity=0.92,
            cornerradius=BAR_RADIUS,
        ),
        showlegend=False,
        hoverinfo="skip",
        width=0.5,
    ))

    # Threshold lines (dotted, sin labels intrusivos)
    fig.add_vline(x=65, line_dash="dot", line_color="#E2B25C",
                  line_width=1, opacity=0.4)
    fig.add_vline(x=80, line_dash="dot", line_color=GREEN,
                  line_width=1, opacity=0.35)

    # ── Panel de CALIFICACIONES a la derecha ─────────────────────────────
    # UN solo separador: línea vertical limpia entre las barras y los números
    # (sin divisores horizontales — recargaban el panel).
    fig.add_shape(type="line", xref="paper", x0=0.86, x1=0.86,
                  yref="y", y0=-0.5, y1=n - 0.5,
                  line=dict(color="rgba(255,255,255,0.12)", width=1))
    # Número grande en mono tabular, color del termómetro, "/100" tenue.
    for i, s in enumerate(scores):
        fig.add_annotation(
            xref="paper", x=0.995, xanchor="right",
            yref="y", y=i, yanchor="middle",
            text=f"<b>{s:.0f}</b><span style='font-size:0.55em;color:{MUTED}'>/100</span>",
            showarrow=False,
            font=dict(size=17, color=_score_color(s), family="JetBrains Mono"),
            align="right",
        )

    fig.update_layout(
        paper_bgcolor=PANEL_BG,
        plot_bgcolor=PANEL_BG,
        font=dict(color=TEXT, family="Inter", size=11),
        height=380,
        barmode="overlay",
        bargap=0.25,
        xaxis=dict(
            # Barras comprimidas a la izquierda; el 14% derecho es el panel de
            # calificaciones (domain en coords de paper, igual que las shapes).
            domain=[0, 0.84],
            range=[0, 102],
            gridcolor="rgba(0,0,0,0)",
            tickfont=dict(color=MUTED, size=9, family="JetBrains Mono"),
            zeroline=False,
            tickvals=[0, 25, 50, 65, 80, 100],
            ticktext=["0", "25", "50", "<span style='color:#E2B25C'>65</span>", "<span style='color:#3DD68C'>80</span>", "100"],
        ),
        yaxis=dict(
            gridcolor="rgba(0,0,0,0)",
            tickfont=dict(color=TEXT, size=11, family="Inter"),
            zeroline=False,
        ),
        title=dict(
            text="<b>DESGLOSE POR ANÁLISIS</b>",
            font=dict(color=MUTED, size=11, family="JetBrains Mono"),
            x=0,
            y=0.97,
        ),
        showlegend=False,
        margin=dict(l=10, r=16, t=40, b=20),
        # Sin tooltip: el radar del Overview es la ÚNICA gráfica con pop-up al
        # pasar el ratón. Aquí las calificaciones ya se leen en el panel derecho.
        hovermode=False,
    )

    return fig


# ── Mini gauge para el sidebar ────────────────────────────────────────────

def build_mini_gauge(score: float) -> go.Figure:
    """Gauge pequeño para el sidebar watchlist."""
    if score >= 80:
        color = GREEN
    elif score >= 65:
        color = BLUE
    elif score >= 50:
        color = ORANGE
    else:
        color = RED

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        domain={"x": [0, 1], "y": [0, 1]},
        number={"font": {"size": 16, "color": color, "family": "JetBrains Mono"}},
        gauge={
            "axis": {"range": [0, 100], "visible": False},
            "bar":  {"color": color, "thickness": 0.4},
            "bgcolor": BG_CARD,
            "borderwidth": 0,
            "steps": [{"range": [0, 100], "color": "#101216"}],
        },
    ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=5, r=5, t=5, b=5),
        height=70,
    )

    return fig


# ── Sector Performance Chart ──────────────────────────────────────────────

def build_sector_heatmap(sector_performance: dict) -> go.Figure:
    """Bar chart de rendimiento sectorial."""
    if not sector_performance:
        return go.Figure()

    sorted_items = sorted(sector_performance.items(), key=lambda x: x[1], reverse=True)
    sectors = [s for s, _ in sorted_items]
    returns = [r for _, r in sorted_items]
    colors  = [GREEN if r >= 0 else RED for r in returns]

    fig = go.Figure(go.Bar(
        y=sectors,
        x=returns,
        orientation="h",
        marker_color=colors,
        marker_opacity=0.8,
        text=[f"{'+' if r >= 0 else ''}{r:.1f}%" for r in returns],
        textposition="outside",
        textfont=dict(size=10, color=TEXT),
    ))

    fig.add_vline(x=0, line_color=MUTED, line_width=1)

    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        plot_bgcolor=BG_MAIN,
        font=dict(color=TEXT, family="JetBrains Mono, monospace", size=11),
        height=320,
        xaxis=dict(gridcolor=GRID, tickformat=".1f", ticksuffix="%", zerolinecolor=GRID),
        yaxis=dict(gridcolor="rgba(0,0,0,0)", zerolinecolor=GRID),
        title=dict(text="<b>RENDIMIENTO SECTORIAL (1Y)</b>", font=dict(color=MUTED, size=11), x=0),
        showlegend=False,
        margin=dict(l=10, r=60, t=40, b=10),
    )

    return fig


# ── COMPONENTES REUTILIZABLES PARA TABS DE AGENTES ─────────────────────

def build_compact_gauge(value: float, label: str = "", color: str = None,
                         max_val: float = 100, height: int = 180, suffix: str = "") -> go.Figure:
    """Mini gauge para mostrar un valor 0-100 en un tab de agente."""
    if color is None:
        color = GREEN if value >= 70 else ORANGE if value >= 50 else RED

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": f"<b>{label}</b>" if label else "",
               "font": {"size": 11, "color": MUTED}} if label else None,
        number={"font": {"size": 32, "color": color, "family": "JetBrains Mono"},
                "suffix": suffix},
        gauge={
            "axis": {"range": [0, max_val], "tickwidth": 1, "tickcolor": MUTED,
                     "tickfont": {"size": 8, "color": MUTED}, "dtick": max_val / 4},
            "bar": {"color": color, "thickness": 0.32},
            "bgcolor": BG_CARD,
            "borderwidth": 0,
            "steps": [
                {"range": [0, max_val * 0.5], "color": "rgba(241,73,95,0.06)"},
                {"range": [max_val * 0.5, max_val * 0.75], "color": "rgba(226,178,92,0.06)"},
                {"range": [max_val * 0.75, max_val], "color": "rgba(61,214,140,0.06)"},
            ],
        },
    ))
    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        font=dict(color=TEXT),
        height=height,
        margin=dict(l=10, r=10, t=30 if label else 10, b=10),
    )
    return fig


def build_rsi_gauge(rsi: float, height: int = 200) -> go.Figure:
    """Gauge específico para RSI con zonas sobrecompra/sobreventa."""
    if rsi >= 70:
        color = RED
        zone = "SOBRECOMPRADO"
    elif rsi <= 30:
        color = GREEN
        zone = "SOBREVENDIDO"
    else:
        color = BLUE
        zone = "NEUTRAL"

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=rsi,
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": f"<b>RSI 14</b><br><span style='font-size:0.7em;color:{color}'>{zone}</span>",
               "font": {"size": 12, "color": MUTED}},
        number={"font": {"size": 36, "color": color, "family": "JetBrains Mono"}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": MUTED,
                     "tickfont": {"size": 8, "color": MUTED}, "dtick": 25},
            "bar": {"color": color, "thickness": 0.35},
            "bgcolor": BG_CARD,
            "borderwidth": 0,
            "steps": [
                {"range": [0, 30],  "color": "rgba(61,214,140,0.18)"},
                {"range": [30, 70], "color": "rgba(111,163,224,0.06)"},
                {"range": [70, 100], "color": "rgba(241,73,95,0.18)"},
            ],
            "threshold": {"line": {"color": WHITE, "width": 2}, "thickness": 0.75, "value": rsi},
        },
    ))
    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        font=dict(color=TEXT),
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


def build_metric_bars(items: list, height: int = 220, title: str = "",
                      x_format: str = "%", x_zero_line: bool = True,
                      color_by_score: bool = False,
                      corner_radius=None) -> go.Figure:
    """Bar chart horizontal genérico para métricas comparativas.
    items = [(label, value, color)]

    color_by_score=True → ignora el color de cada item y lo pinta según la
    escala del termómetro (rojo→verde, 0-100), para las barras que representan
    una CALIFICACIÓN (sub-scores). Además dibuja un riel de fondo 0→100 para
    que se lea como una barra de progreso.

    corner_radius → redondeo de las barras (por defecto BAR_RADIUS). Pasar 0
    para barras de esquinas rectas (usado en las gráficas del análisis técnico).
    """
    _radius = BAR_RADIUS if corner_radius is None else corner_radius
    if not items:
        return go.Figure()

    labels = [i[0] for i in items]
    values = [i[1] if isinstance(i[1], (int, float)) else 0 for i in items]
    colors = [_score_color(v) for v in values] if color_by_score else [i[2] for i in items]

    text_vals = [
        (f"{v:+.2f}%" if x_format == "%" else f"{v:.0f}" if color_by_score
         else f"{v:.2f}") if isinstance(v, (int, float)) else "—"
        for v in values
    ]

    # Grosor de barra: algo más finas cuando hay riel, para que se vea el track.
    bar_w = 0.62 if color_by_score else 0.7
    fig = go.Figure()

    # Riel de fondo (solo en modo calificación): 0→100 tenue, redondeado.
    if color_by_score:
        fig.add_trace(go.Bar(
            y=labels, x=[100] * len(labels), orientation="h",
            marker=dict(color="rgba(255,255,255,0.035)",
                        cornerradius=BAR_RADIUS, line=dict(width=0)),
            width=bar_w, showlegend=False, hoverinfo="skip",
        ))

    fig.add_trace(go.Bar(
        y=labels, x=values, orientation="h",
        marker=dict(color=colors, opacity=0.92, cornerradius=_radius,
                    line=dict(color="rgba(255,255,255,0.10)", width=1)),
        # En modo calificación el número NO va al final de la barra: vive en su
        # panel derecho separado por divisores (ver más abajo).
        text=(None if color_by_score else text_vals), textposition="outside",
        textfont=dict(size=10, color=TEXT, family="JetBrains Mono"),
        width=bar_w, showlegend=False,
        # cliponaxis=False: la etiqueta "outside" (p.ej. "+18.71%") no se recorta
        # contra el borde del eje; se dibuja completa aunque asome del área.
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>%{x:.0f}<extra></extra>" if color_by_score else None,
    ))

    if x_zero_line and not color_by_score:
        fig.add_vline(x=0, line_color=MUTED, line_width=1, opacity=0.5)

    if color_by_score:
        # ── Panel de CALIFICACIONES a la derecha (mismo lenguaje que el
        # Desglose del Overview): UN solo separador vertical limpio + número
        # grande en mono coloreado por el termómetro. ─────────────────────────
        _n = len(labels)
        fig.add_shape(type="line", xref="paper", x0=0.84, x1=0.84,
                      yref="y", y0=-0.5, y1=_n - 0.5,
                      line=dict(color="rgba(255,255,255,0.12)", width=1))
        for _i, _v in enumerate(values):
            fig.add_annotation(
                xref="paper", x=0.995, xanchor="right",
                yref="y", y=_i, yanchor="middle",
                text=f"<b>{_v:.0f}</b><span style='font-size:0.55em;color:{MUTED}'>/100</span>",
                showarrow=False,
                font=dict(size=15, color=_score_color(_v), family="JetBrains Mono"),
                align="right",
            )

    xaxis = dict(gridcolor=GRID, tickfont=dict(color=MUTED, size=9), zerolinecolor=MUTED,
                 ticksuffix=("%" if x_format == "%" else ""))
    if color_by_score:
        # Barras comprimidas a la izquierda (el 18% derecho es el panel de
        # calificaciones); riel completo 0→100 con ticks en los umbrales.
        xaxis.update(domain=[0, 0.82], range=[0, 102],
                     gridcolor="rgba(0,0,0,0)", zeroline=False,
                     tickvals=[0, 25, 50, 65, 80, 100],
                     tickfont=dict(color=MUTED, size=9, family="JetBrains Mono"))
    else:
        # Encuadre con holgura a AMBOS lados (positivo y negativo) para que las
        # etiquetas "outside" de las barras más largas queden completas dentro
        # del marco — antes se autoescalaba justo al valor y los números se
        # cortaban en los extremos (gráficas de MAs y Relative Strength).
        _vmax = max(values + [0.0])
        _vmin = min(values + [0.0])
        _span = (_vmax - _vmin) or (abs(_vmax) or 1.0)
        _pad = _span * 0.34
        xaxis.update(range=[_vmin - _pad, _vmax + _pad])

    # Fondo/márgenes: el modo calificación usa el panel "instrumento" (más negro,
    # sin margen derecho — el panel de números vive dentro del paper). La rama
    # normal (MAs / Relative Strength) queda EXACTAMENTE como estaba.
    _bg = PANEL_BG if color_by_score else BG_MAIN
    _title_font = (dict(color=MUTED, size=11, family="JetBrains Mono")
                   if color_by_score else dict(color=MUTED, size=11))
    fig.update_layout(
        paper_bgcolor=_bg,
        plot_bgcolor=_bg,
        font=dict(color=TEXT, family="Inter", size=11),
        height=height,
        showlegend=False,
        barmode="overlay",
        margin=dict(l=10, r=(12 if color_by_score else 60), t=40 if title else 10, b=10),
        title=dict(text=f"<b>{title}</b>", font=_title_font, x=0) if title else None,
        xaxis=xaxis,
        yaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=TEXT, size=10), zerolinecolor=GRID),
        hoverlabel=dict(bgcolor="#15181D", bordercolor="rgba(226,178,92,0.3)",
                        font=dict(size=11, family="JetBrains Mono", color=TEXT)),
    )
    return fig


def build_earnings_history_chart(history: list, height: int = 250) -> go.Figure:
    """Bar chart de earnings surprises históricos."""
    if not history:
        return go.Figure()

    history = list(reversed(history))  # más antiguo primero (izq) → más reciente (der)
    dates = [h.get("date", "")[:10] for h in history]
    surprises = [h.get("surprise_pct", 0) for h in history]
    colors = [GREEN if s >= 0 else RED for s in surprises]

    fig = go.Figure(go.Bar(
        x=dates,
        y=surprises,
        # Radio pequeño FIJO (no porcentual): en barras verticales cortas el 30%
        # se ve exagerado. 7px = redondeo sutil, como antes.
        marker=dict(color=colors, opacity=0.9, cornerradius=7,
                    line=dict(color="rgba(255,255,255,0.10)", width=1)),
        text=[f"{s:+.1f}%" for s in surprises],
        textposition="outside",
        textfont=dict(size=10, color=TEXT, family="JetBrains Mono"),
        # cliponaxis=False: la etiqueta de una barra muy alta (p.ej. +2000%) no
        # se recorta contra el borde del eje; se dibuja completa en el margen.
        cliponaxis=False,
    ))

    fig.add_hline(y=0, line_color=MUTED, line_width=1, opacity=0.6)

    # Rango del eje Y con espacio (headroom) para que la etiqueta "outside"
    # SIEMPRE quepa: extra arriba para barras positivas y abajo para negativas.
    _vmax = max(surprises + [0.0])
    _vmin = min(surprises + [0.0])
    _span = (_vmax - _vmin) or (abs(_vmax) or 1.0)
    y_range = [_vmin - _span * 0.14, _vmax + _span * 0.22]

    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        plot_bgcolor=BG_MAIN,
        font=dict(color=TEXT, family="JetBrains Mono", size=10),
        height=height,
        showlegend=False,
        # Margen superior amplio: aloja la etiqueta de la barra más alta que,
        # con cliponaxis=False, se dibuja por encima del área de trazado.
        margin=dict(l=10, r=10, t=52, b=24),
        title=dict(text="<b>HISTORIAL EARNINGS SURPRISES</b>", font=dict(color=MUTED, size=11), x=0),
        xaxis=dict(gridcolor=GRID, tickfont=dict(color=MUTED, size=9), zerolinecolor=GRID),
        yaxis=dict(gridcolor=GRID, tickfont=dict(color=MUTED, size=9), zerolinecolor=GRID,
                   ticksuffix="%", range=y_range),
    )
    return fig


def build_sentiment_gauge(score: float, height: int = 240) -> go.Figure:
    """Gauge especializado para sentimiento con etiquetas (Bearish → Bullish)."""
    if score >= 75:
        color, label = GREEN, "MUY BULLISH"
    elif score >= 55:
        color, label = "#63DFA3", "BULLISH"
    elif score >= 45:
        color, label = BLUE, "NEUTRAL"
    elif score >= 30:
        color, label = ORANGE, "BEARISH"
    else:
        color, label = RED, "MUY BEARISH"

    fig = go.Figure(go.Indicator(
        mode="gauge",
        value=score,
        # Dominio completo (centrado garantizado) + margen amplio l/r para que
        # los ticks "0" y "100" no toquen los bordes. Así queda centrado en la
        # tarjeta y lo más grande posible sin cortarse.
        # Mismo lenguaje "instrumento" que el gauge del DLP Score: arco fino,
        # anillo casi negro, bandas tenues, ticks hairline mono. Sobrio.
        domain={"x": [0, 1], "y": [0.28, 1.0]},
        title={"text": (f"<span style='color:{MUTED}'>SENTIMIENTO</span><br>"
                        f"<span style='font-size:0.72em;color:{color}'><b>{label}</b></span>"),
               "font": {"size": 12, "color": MUTED, "family": "JetBrains Mono"}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1,
                     "tickcolor": "rgba(255,255,255,0.30)", "ticklen": 6,
                     "tickfont": {"size": 8, "color": MUTED, "family": "JetBrains Mono"},
                     "dtick": 25},
            "bar": {"color": color, "thickness": 0.30},
            # Mismo acabado que el dial principal: anillo con cuerpo + borde fino.
            "bgcolor": "#0D1015",
            "borderwidth": 1,
            "bordercolor": "rgba(226,178,92,0.22)",
            # Degradado térmico CONTINUO del sentimiento: oso→neutral→toro
            # (rojo→ámbar→azul→verde), textura suave bajo el arco.
            "steps": _gauge_gradient_steps(n=60, alpha=0.15, stops=[
                (0,   (241, 73, 95)),    # muy bearish
                (30,  (226, 178, 92)),   # bearish
                (48,  (111, 163, 224)),  # neutral (azul)
                (58,  (99, 223, 163)),   # bullish suave
                (75,  (61, 214, 140)),   # bullish
                (100, (61, 214, 140)),
            ]),
            # Aguja fina en el valor exacto (coherente con el gauge principal).
            "threshold": {
                "line": {"color": WHITE, "width": 2},
                "thickness": 0.94,
                "value": score,
            },
        },
    ))
    # Número GRANDE como annotation separada — nunca se solapa con el arco
    fig.add_annotation(
        x=0.5, y=0.10,
        xref="paper", yref="paper",
        text=f"<b>{score:.0f}</b><span style='font-size:0.4em;color:{MUTED}'>/100</span>",
        showarrow=False,
        font=dict(size=42, color=color, family="JetBrains Mono"),
        align="center",
    )
    fig.update_layout(
        paper_bgcolor=PANEL_BG,
        font=dict(color=TEXT),
        height=height + 95,   # más grande dentro de su tarjeta
        # Márgenes SIMÉTRICOS → gauge (domain x=[0,1]) y número (x=0.5) CENTRADOS
        # en la tarjeta. Antes un margen asimétrico (r=72) lo empujaba a la izq.
        margin=dict(l=44, r=44, t=54, b=16),
    )
    return fig


def build_holders_bars(holders: list, height: int = 260) -> go.Figure:
    """Top holders institucionales como barras horizontales con %."""
    if not holders:
        return go.Figure()

    items = []
    for h in holders[:10]:
        name = h.get("Holder") or h.get("holder") or "Unknown"
        pct = h.get("% Out") or h.get("pctHeld") or 0
        if isinstance(pct, str):
            try:
                pct = float(pct.replace("%", "")) / (100 if "%" in str(pct) else 1)
            except Exception:
                pct = 0
        items.append((str(name)[:30], pct * 100 if pct < 1 else pct))

    items.sort(key=lambda x: x[1], reverse=True)
    items = items[:8]

    names = [i[0] for i in items]
    vals  = [i[1] for i in items]
    track_max = (max(vals) * 1.15) if vals else 1

    fig = go.Figure()
    # Riel de fondo tenue para dar profundidad (mismo idioma que las demás)
    fig.add_trace(go.Bar(
        y=names, x=[track_max] * len(names), orientation="h",
        marker=dict(color="rgba(255,255,255,0.03)", cornerradius=BAR_RADIUS, line=dict(width=0)),
        width=0.68, showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Bar(
        y=names, x=vals, orientation="h",
        marker=dict(color=BLUE, opacity=0.85, cornerradius=BAR_RADIUS,
                    line=dict(color="rgba(255,255,255,0.10)", width=1)),
        text=[f"{v:.2f}%" for v in vals],
        textposition="outside",
        textfont=dict(size=10, color=TEXT, family="JetBrains Mono"),
        width=0.68, showlegend=False,
    ))

    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        plot_bgcolor=BG_MAIN,
        font=dict(color=TEXT, family="Inter", size=10),
        height=height,
        showlegend=False,
        barmode="overlay",
        margin=dict(l=10, r=40, t=40, b=10),
        title=dict(text="<b>TOP 8 INSTITUCIONALES (% outstanding)</b>", font=dict(color=MUTED, size=11), x=0),
        xaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=MUTED, size=9), ticksuffix="%",
                   zeroline=False, range=[0, track_max]),
        yaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=TEXT, size=9),
                   autorange="reversed", zerolinecolor=GRID),
    )
    return fig


# ── Quick View Chart (compact line + volume) ──────────────────────────────

def build_quick_chart(df: pd.DataFrame, ticker: str, period_days: int = 126) -> go.Figure:
    """Chart compacto para Vista Rápida: línea de precio + MA50 + volumen."""
    if df is None or df.empty:
        return go.Figure()

    df = df.tail(period_days)
    if df.empty:
        return go.Figure()

    close = df["Close"]
    open_ = df["Open"]
    volume = df["Volume"]
    dates = df.index

    is_up = close.iloc[-1] >= close.iloc[0]
    line_color = GREEN if is_up else RED
    fill_color = "rgba(61,214,140,0.12)" if is_up else "rgba(241,73,95,0.12)"

    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean() if len(close) >= 200 else None

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.78, 0.22],
    )

    # Línea de precio con fill area
    fig.add_trace(go.Scatter(
        x=dates, y=close,
        mode="lines",
        line=dict(color=line_color, width=2.2),
        fill="tonexty",
        fillcolor=fill_color,
        name="Precio",
        showlegend=False,
        hovertemplate="<b>%{x|%d %b %Y}</b><br>$%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    # Baseline invisible para el fill
    fig.add_trace(go.Scatter(
        x=dates, y=[close.min() * 0.95] * len(dates),
        mode="lines",
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=False,
        hoverinfo="skip",
    ), row=1, col=1)

    # MA50 dotted
    fig.add_trace(go.Scatter(
        x=dates, y=ma50,
        mode="lines",
        line=dict(color=YELLOW, width=1.2, dash="dot"),
        name="MA 50",
        showlegend=False,
        hovertemplate="MA50 $%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    if ma200 is not None:
        fig.add_trace(go.Scatter(
            x=dates, y=ma200,
            mode="lines",
            line=dict(color=ORANGE, width=1.2, dash="dash"),
            name="MA 200",
            showlegend=False,
            hovertemplate="MA200 $%{y:.2f}<extra></extra>",
        ), row=1, col=1)

    # Volumen bars
    vol_colors = [GREEN if c >= o else RED for c, o in zip(close, open_)]
    fig.add_trace(go.Bar(
        x=dates, y=volume,
        marker_color=vol_colors,
        marker_opacity=0.5,
        showlegend=False,
        hovertemplate="Vol %{y:,.0f}<extra></extra>",
    ), row=2, col=1)

    fig.update_layout(
        paper_bgcolor=BG_MAIN,
        plot_bgcolor=BG_MAIN,
        font=dict(color=TEXT, family="JetBrains Mono, monospace", size=10),
        height=360,
        margin=dict(l=8, r=8, t=8, b=8),
        hovermode="x unified",
        showlegend=False,
    )

    fig.update_xaxes(
        gridcolor=GRID, zerolinecolor=GRID,
        tickfont=dict(color=MUTED, size=9),
        showgrid=False,
        row=1, col=1,
    )
    fig.update_yaxes(
        gridcolor=GRID, zerolinecolor=GRID,
        tickfont=dict(color=MUTED, size=9),
        tickprefix="$",
        row=1, col=1,
    )
    fig.update_xaxes(
        gridcolor=GRID, showgrid=False,
        tickfont=dict(color=MUTED, size=9),
        row=2, col=1,
    )
    fig.update_yaxes(
        showgrid=False, showticklabels=False,
        row=2, col=1,
    )

    return fig


# ── Risk/Reward Visual ────────────────────────────────────────────────────

def build_rr_chart(current_price: float, stop: float, target: float, ticker: str,
                   compact: bool = False) -> go.Figure:
    """Escalera de precios Upside/Downside desde el PRECIO ACTUAL.

    A la IZQUIERDA, una "tabla" de niveles (OBJETIVO / PRECIO ACTUAL / PROTECCIÓN
    con su $ y su %) en posiciones FIJAS y bien separadas → NUNCA se solapan,
    aunque dos niveles de precio estén muy cerca (antes iban ancladas al precio y
    colisionaban). A la DERECHA, una barra con dos zonas PROPORCIONALES al
    recorrido de precio: verde (actual→objetivo) arriba y roja (protección→actual)
    abajo — como ambas alturas representan el %, la MAYOR se ve al instante.

    compact=True → versión pequeña/cuadrada para el Overview (fuentes y barra
    reducidas). El tab de Riesgo usa la versión normal."""
    if not all([current_price, stop, target]):
        return go.Figure()

    downside_pct = (current_price - stop) / current_price * 100
    upside_pct   = (target - current_price) / current_price * 100
    rr           = upside_pct / downside_pct if downside_pct > 0 else 0
    rr_color     = GREEN if rr >= 3 else (ORANGE if rr >= 2 else RED)

    span = max(target - stop, 1e-6)
    pad  = span * 0.10

    if compact:
        height = 270
        f_tbl, f_pct, f_title = 9.5, 13, 11
        BAR_L, BAR_R = 0.62, 0.92
    else:
        height = 320
        f_tbl, f_pct, f_title = 12, 16, 13
        BAR_L, BAR_R = 0.66, 0.90
    CX = (BAR_L + BAR_R) / 2

    fig = go.Figure()
    # Traza fantasma para asegurar render (una figura solo-shapes no siempre pinta
    # de forma fiable); invisible y sin hover.
    fig.add_trace(go.Scatter(x=[CX], y=[current_price], mode="markers",
                             marker=dict(size=0.1, color="rgba(0,0,0,0)"),
                             hoverinfo="skip", showlegend=False))

    # Barra derecha: zona de GANANCIA (verde) y de PÉRDIDA (roja), proporcionales.
    fig.add_shape(type="rect", x0=BAR_L, x1=BAR_R, y0=current_price, y1=target,
                  fillcolor="rgba(61,214,140,0.18)", line=dict(color=GREEN, width=1.2), layer="below")
    fig.add_shape(type="rect", x0=BAR_L, x1=BAR_R, y0=stop, y1=current_price,
                  fillcolor="rgba(241,73,95,0.18)", line=dict(color=RED, width=1.2), layer="below")
    # Línea del precio actual (referencia, el "0%").
    fig.add_shape(type="line", x0=BAR_L - 0.04, x1=BAR_R + 0.04,
                  y0=current_price, y1=current_price, line=dict(color=ORANGE, width=2.5))

    # % grande centrado en cada zona (solo si la zona tiene altura suficiente).
    if (target - current_price) > span * 0.09:
        fig.add_annotation(x=CX, y=(current_price + target) / 2, text=f"<b>+{upside_pct:.1f}%</b>",
                           showarrow=False, font=dict(color=GREEN, size=f_pct, family="JetBrains Mono"))
    if (current_price - stop) > span * 0.09:
        fig.add_annotation(x=CX, y=(stop + current_price) / 2, text=f"<b>−{downside_pct:.1f}%</b>",
                           showarrow=False, font=dict(color=RED, size=f_pct, family="JetBrains Mono"))

    # TABLA a la IZQUIERDA — posiciones FIJAS en coords de "paper" (independientes
    # del precio), muy separadas → jamás se solapan aunque stop ≈ precio actual.
    for ypap, col, l1, l2 in [
        (0.82, GREEN,  "▲ OBJETIVO",      f"${target:,.2f}  ·  +{upside_pct:.1f}%"),
        (0.50, ORANGE, "● PRECIO ACTUAL", f"${current_price:,.2f}"),
        (0.18, RED,    "▼ PROTECCIÓN",    f"${stop:,.2f}  ·  −{downside_pct:.1f}%"),
    ]:
        fig.add_annotation(xref="paper", yref="paper", x=0.02, y=ypap,
                           xanchor="left", yanchor="middle", align="left",
                           text=f"<span style='color:{col}'>{l1}</span><br><b>{l2}</b>",
                           showarrow=False, font=dict(color=col, size=f_tbl, family="JetBrains Mono"))

    fig.update_layout(
        paper_bgcolor=BG_MAIN, plot_bgcolor=BG_MAIN,
        font=dict(color=TEXT, family="JetBrains Mono, monospace", size=11),
        height=height, showlegend=False, dragmode=False, hovermode=False,
        margin=dict(l=10, r=10, t=44, b=14),
        xaxis=dict(range=[0, 1], showgrid=False, showticklabels=False, zeroline=False, fixedrange=True),
        yaxis=dict(range=[stop - pad, target + pad], showgrid=False, showticklabels=False,
                   zeroline=False, fixedrange=True),
        title=dict(text=f"<b>UPSIDE / DOWNSIDE</b>   ·   R/R {rr:.1f}:1",
                   font=dict(color=rr_color, size=f_title), x=0.5, xanchor="center", y=0.97),
    )
    return fig
