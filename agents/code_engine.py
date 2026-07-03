# -*- coding: utf-8 -*-
"""
Motor de scoring POR CÓDIGO (sin IA) — versión para clientes.

Reemplaza el "cerebro" que antes hacía Claude. Cada función recibe los MISMOS
datos que el agente ya obtiene (de data/market_data.py) y devuelve un dict con
EXACTAMENTE las mismas claves que antes devolvía el JSON del LLM
(score, conviction, analysis, pros, cons, key_metrics, sub_scores, + campos
propios de cada agente). Así los agentes y el dashboard funcionan IGUAL, pero
sin gastar créditos de API.

Las calificaciones se calculan con fórmulas/reglas a partir de fundamentales,
indicadores técnicos, earnings, holders y macro. El texto es plantillado a
partir de esas métricas (lectura automática, no IA).
"""
from datetime import datetime


# ── Helpers numéricos (tolerantes a None) ────────────────────────────────
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _lin(x, x0, x1, y0, y1, default=None):
    """Mapeo lineal de x∈[x0,x1] a [y0,y1], recortado. Si x es None usa default
    (o el punto medio del rango destino)."""
    if x is None:
        return default if default is not None else (y0 + y1) / 2.0
    if x1 == x0:
        return y1
    t = _clamp((x - x0) / (x1 - x0), 0.0, 1.0)
    return y0 + t * (y1 - y0)


def _conv(score):
    return "HIGH" if score >= 74 else "MEDIUM" if score >= 55 else "LOW"


def _pct(v, dec=1, signed=False):
    if v is None:
        return "N/A"
    return f"{v:+.{dec}f}%" if signed else f"{v:.{dec}f}%"


def _num(v, dec=2):
    return "N/A" if v is None else f"{v:.{dec}f}"


def _money(v):
    if v is None:
        return "N/A"
    try:
        return f"${v:,.2f}"
    except Exception:
        return "N/A"


def _top(items, n=3):
    return [x for x in items if x][:n]


# ════════════════════════════════════════════════════════════════════════
# 1. FUNDAMENTALES
# ════════════════════════════════════════════════════════════════════════
def score_fundamentals(info, financials, ratios):
    gm = ratios.get("gross_margin")
    om = ratios.get("operating_margin")
    roic = ratios.get("roic")
    rg = ratios.get("revenue_growth_yoy")
    eg = ratios.get("earnings_growth_yoy")
    cagr = ratios.get("revenue_cagr_2y")
    fcfy = ratios.get("fcf_yield")
    de = ratios.get("debt_to_equity")
    cr = ratios.get("current_ratio")
    pe = info.get("pe_ratio") or info.get("forward_pe")
    ev = info.get("ev_ebitda")

    # Calidad (0-25): márgenes + retorno sobre capital
    quality = 0.0
    quality += _lin(om, 0, 32, 2, 13, default=6)
    quality += _lin(roic, 0, 25, 0, 8, default=3)
    quality += _lin(gm, 20, 70, 0, 5, default=2)
    quality = _clamp(quality, 4, 25)

    # Crecimiento (0-25)
    growth = 0.0
    growth += _lin(rg, -5, 30, 1, 15, default=6)
    growth += _lin(eg if eg is not None else cagr, -15, 40, 0, 7, default=3)
    growth += _lin(cagr, 0, 25, 0, 3, default=1.5)
    growth = _clamp(growth, 3, 25)

    # Valoración (0-25): más barato = más alto
    val = 13.0
    if pe and pe > 0:
        val = _lin(pe, 10, 45, 23, 5)
    if fcfy is not None:
        val += _lin(fcfy, 0, 8, -2, 6)
    val = _clamp(val, 3, 25)

    # Salud financiera (0-25)
    health = _lin(de, 0, 250, 25, 6, default=15)
    if cr is not None:
        health += _lin(cr, 0.8, 2.0, -3, 3)
    if fcfy is not None and fcfy > 0:
        health += 2
    health = _clamp(health, 4, 25)

    score = round(quality + growth + val + health, 1)
    sub_scores = {
        "quality": round(quality, 1),
        "growth": round(growth, 1),
        "valuation": round(val, 1),
        "financial_health": round(health, 1),
    }

    pros, cons = [], []
    # Calidad operativa — siempre con números
    if om is not None and om >= 25:
        pros.append(f"Margen operativo de {_pct(om)} — negocio altamente rentable con amplia holgura para reinvertir o absorber presión competitiva")
    elif om is not None and om >= 18:
        pros.append(f"Márgenes operativos sólidos ({_pct(om)}) con margen bruto de {_pct(gm)} — estructura de costos eficiente")
    elif om is not None and om >= 10:
        pros.append(f"Margen operativo de {_pct(om)} — rentable pero con espacio limitado si la competencia aprieta precios")

    if roic is not None and roic >= 20:
        pros.append(f"ROIC de {_pct(roic)} — retornos excepcionales sobre el capital; cada dólar reinvertido crea valor real")
    elif roic is not None and roic >= 12:
        pros.append(f"ROIC de {_pct(roic)} supera costo de capital — asignación eficiente que crea valor para el accionista")

    if rg is not None and rg >= 20:
        pros.append(f"Ingresos creciendo {_pct(rg)} interanual — tracción de demanda que supera ampliamente inflación y GDP global")
    elif rg is not None and rg >= 10:
        pros.append(f"Crecimiento de ingresos de {_pct(rg)} interanual — expansión sólida y consistente")

    if fcfy is not None and fcfy >= 5:
        pros.append(f"FCF yield de {_pct(fcfy)} — genera caja libre abundante; puede retornar capital o financiar crecimiento sin deuda")
    elif fcfy is not None and fcfy >= 3:
        pros.append(f"FCF yield de {_pct(fcfy)} — generación de caja real que respalda la valoración")

    if de is not None and de <= 40:
        pros.append(f"Balance sólido (Debt/Equity {_num(de,0)}) — flexibilidad financiera para oportunidades o shocks externos")
    elif de is not None and de <= 80:
        pros.append(f"Deuda manejable (Debt/Equity {_num(de,0)}) — sin presión financiera que limite la operación")

    # Contras — con interpretación, no solo el número
    if om is not None and om < 5:
        cons.append(f"Margen operativo de {_pct(om)} — una contracción pequeña de ingresos o subida de costos puede llevar al negocio a pérdidas")
    elif om is not None and om < 10:
        cons.append(f"Margen operativo ajustado ({_pct(om)}) — poca holgura ante presión de costos o competencia de precios")

    if roic is not None and roic < 8 and (rg or 0) >= 10:
        cons.append(f"ROIC de {_pct(roic)} pese a crecimiento de {_pct(rg)} — el negocio reinvierte agresivamente pero los retornos sobre ese capital aún no se materializan")
    elif roic is not None and roic < 8:
        cons.append(f"ROIC de {_pct(roic)} — el capital se reinvierte a tasas bajas; difícil compounding si no mejoran los retornos")

    if rg is not None and rg < 0:
        cons.append(f"Ingresos contrayéndose ({_pct(rg)}) — la demanda del producto o servicio está en declive")
    elif rg is not None and rg < 3:
        cons.append(f"Crecimiento muy débil ({_pct(rg)}) — el negocio apenas supera inflación, señal de mercado maduro o pérdida de competitividad")

    if pe and pe > 40:
        cons.append(f"Valoración de {_num(pe,1)}x P/E no deja margen de error — cualquier decepción en earnings puede comprimir el múltiplo fuertemente")
    elif pe and pe > 28:
        cons.append(f"P/E de {_num(pe,1)}x asume crecimiento sostenido — el mercado ya paga por el escenario optimista")

    if de is not None and de > 200:
        cons.append(f"Deuda muy elevada (Debt/Equity {_num(de,0)}) — carga financiera que limita flexibilidad y amplifica el riesgo en recesión")
    elif de is not None and de > 120:
        cons.append(f"Deuda relevante (Debt/Equity {_num(de,0)}) — monitorear costo de refinanciación si suben tasas")

    if not pros:
        pros.append(f"Negocio activo con ingresos {'+' if (rg or 0) >= 0 else ''}{_pct(rg)} y margen bruto de {_pct(gm)}")
    if not cons:
        if pe and pe > 18:
            cons.append(f"P/E de {_num(pe,1)}x no es barato — la ejecución debe ser consistente para justificar la valoración actual")
        else:
            cons.append(f"Sin debilidades severas identificadas — vigilar evolución de márgenes ({_pct(om)}) y deuda ({_num(de,0)} D/E)")

    # ── Análisis en lenguaje simple (explica cada término) ────────────────
    partes = []
    if om is not None:
        if om >= 25:
            partes.append(f"Es un negocio muy rentable: de cada $100 que vende le quedan unos "
                          f"${_num(om,0)} de ganancia operativa (margen operativo del {_pct(om)}, "
                          f"lo que gana después de pagar sus costos del día a día).")
        elif om >= 10:
            partes.append(f"Tiene una rentabilidad decente: de cada $100 de ventas le quedan unos "
                          f"${_num(om,0)} de ganancia operativa (margen operativo del {_pct(om)}).")
        else:
            partes.append(f"Trabaja con márgenes ajustados: de cada $100 de ventas solo le quedan "
                          f"${_num(om,0)} de ganancia operativa (margen operativo del {_pct(om)}), "
                          f"poca holgura ante imprevistos.")
    if roic is not None:
        cal = ("excelente" if roic >= 18 else "aceptable" if roic >= 10 else "flojo")
        partes.append(f"Reinvierte su dinero con un retorno {cal} del {_pct(roic)} (el ROIC mide qué "
                      f"tan bien convierte el capital que invierte en ganancias; por encima de 15% "
                      f"es señal de un gran negocio).")
    if rg is not None:
        if rg >= 0:
            partes.append(f"Sus ventas crecen un {_pct(rg)} al año.")
        else:
            partes.append(f"Sus ventas se están encogiendo un {_pct(abs(rg))} al año, una señal de alerta.")
    if pe and pe > 0:
        cal_pe = ("barata" if pe < 15 else "a un precio razonable" if pe < 25 else "exigente")
        partes.append(f"En precio, la acción cotiza {cal_pe} a un P/E de {_num(pe,1)} "
                      f"(cuántos años de ganancias actuales estás pagando por ella).")
    if score >= 66:
        partes.append("En resumen, los números de fondo del negocio son sólidos.")
    elif score >= 50:
        partes.append("En resumen, el negocio tiene luces y sombras en sus números.")
    else:
        partes.append("En resumen, los números de fondo todavía dejan dudas.")
    analysis = " ".join(partes)

    # ── EL HALLAZGO: la conclusión más importante, conectando los puntos ───
    fortaleza = None
    if roic is not None and om is not None and roic >= 18 and om >= 25:
        fortaleza = (f"estás ante una 'máquina de calidad' — gana mucho por cada venta "
                     f"(margen {_pct(om)}) y reinvierte ese dinero a tasas altas ({_pct(roic)} de ROIC), "
                     f"que es justo la receta de las empresas que multiplican su valor con los años")
    elif rg is not None and rg >= 20:
        fortaleza = (f"lo que más destaca es su motor de crecimiento: las ventas suben {_pct(rg)} al año, "
                     f"muy por encima de lo que crece la economía")
    elif fcfy is not None and fcfy >= 5:
        fortaleza = (f"lo más valioso es que genera mucha caja libre ({_pct(fcfy)} de FCF yield, el "
                     f"dinero real que sobra cada año), con la que puede pagar dividendos o crecer sin endeudarse")
    elif om is not None and om >= 18:
        fortaleza = f"su punto fuerte es la rentabilidad: márgenes operativos del {_pct(om)}, por encima del promedio"
    else:
        fortaleza = "no tiene una ventaja financiera que sobresalga con claridad sobre el resto del sector"

    pero = None
    if rg is not None and rg < 0:
        pero = "sus ventas están cayendo, lo más importante a vigilar"
    elif pe and pe > 35:
        pero = (f"a un P/E de {_num(pe,1)} el mercado ya paga caro por esa calidad, "
                f"así que cualquier tropiezo puede pesar en el precio")
    elif de is not None and de > 150:
        pero = f"carga una deuda elevada (Debt/Equity {_num(de,0)}) que conviene seguir de cerca"
    elif om is not None and om < 10:
        pero = "sus márgenes ajustados le dejan poco colchón si la competencia aprieta"
    elif pe and pe > 25:
        pero = f"a un P/E de {_num(pe,1)} no está barata: hay que pagar por su calidad"
    else:
        pero = "no se ve una debilidad financiera grave a día de hoy"

    key_insight = f"Lo más relevante: {fortaleza}. El punto a vigilar: {pero}."

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": analysis,
        "pros": _top(pros, 3),
        "cons": _top(cons, 3),
        "key_metrics": {
            "revenue_growth": _pct(rg),
            "gross_margin": _pct(gm),
            "operating_margin": _pct(om),
            "fcf_yield": _pct(fcfy),
            "roic": _pct(roic),
            "debt_equity": _num(de, 0),
            "pe_ratio": _num(pe, 1),
            "ev_ebitda": _num(ev, 1),
        },
        "sub_scores": sub_scores,
        "key_insight": key_insight,
        "dcf_thesis": (
            "Calidad y generación de caja razonables; el precio actual define el margen de seguridad."
            if score >= 60 else
            "El perfil fundamental es mixto; conviene exigir un precio claramente atractivo."
        ),
        "earnings_quality": (
            f"Ganancias respaldadas por márgenes de {_pct(om)} y FCF yield {_pct(fcfy)}."
        ),
    }


# ════════════════════════════════════════════════════════════════════════
# 2. TÉCNICO
# ════════════════════════════════════════════════════════════════════════
def score_technical(ind, ind_weekly, rs):
    stage = ind.get("stage", 0)
    rsi = ind.get("rsi_14")
    macd_hist = ind.get("macd_hist")
    vs50 = ind.get("price_vs_sma50_pct")
    vs200 = ind.get("price_vs_sma200_pct")
    from_high = ind.get("pct_from_52w_high")
    ret6 = ind.get("return_6m")
    rel_vol = ind.get("rel_volume", 1.0)
    obv_trend = ind.get("obv_trend", "")
    rs6 = rs.get("rs_6m")

    # Trend quality (0-33)
    trend = {2: 30, 1: 21, 3: 14, 4: 7, 0: 12}.get(stage, 12)
    if vs200 is not None:
        trend += _lin(vs200, -15, 15, -3, 3)
    trend = _clamp(trend, 0, 33)

    # Momentum (0-33)
    mom = 16.0
    if rsi is not None:
        # ideal 50-65; penaliza sobrecompra >75 y sobreventa <35
        if rsi > 75:
            mom += _lin(rsi, 75, 90, 2, -6)
        elif rsi < 35:
            mom += _lin(rsi, 20, 35, -4, 0)
        else:
            mom += _lin(rsi, 35, 65, 0, 8)
    if macd_hist is not None:
        mom += 4 if macd_hist > 0 else -4
    if ret6 is not None:
        mom += _lin(ret6, -20, 30, -3, 4)
    if rs6 is not None:
        mom += _lin(rs6, -15, 20, -3, 4)
    mom = _clamp(mom, 0, 33)

    # Setup quality (0-34)
    setup = 17.0
    if from_high is not None:
        # cerca del máximo (sin estar extendido) es mejor; muy lejos = débil
        setup += _lin(from_high, -40, -3, -6, 6)
    if rel_vol is not None:
        setup += _lin(rel_vol, 0.6, 1.8, -2, 3)
    if obv_trend == "rising":
        setup += 2
    elif obv_trend == "falling":
        setup -= 2
    setup = _clamp(setup, 0, 34)

    score = round(trend + mom + setup, 1)
    macd_sig = "alcista" if (macd_hist or 0) > 0 else "bajista" if (macd_hist or 0) < 0 else "neutral"

    pros, cons = [], []
    if stage == 2:
        pros.append("Tendencia alcista confirmada (Stage 2)")
    if vs200 is not None and vs200 > 0:
        pros.append(f"Precio por encima de su media de 200 días ({_pct(vs200, signed=True)})")
    if rs6 is not None and rs6 > 0:
        pros.append(f"Le gana al mercado en 6 meses ({_pct(rs6, signed=True)})")
    if macd_sig == "alcista":
        pros.append("Momentum de corto plazo a favor (MACD positivo)")
    if stage in (3, 4):
        cons.append(f"Tendencia no alcista (Stage {stage}) — es timing, no tesis")
    if rsi is not None and rsi > 75:
        cons.append(f"Sobrecomprada (RSI {_num(rsi,0)}) — posible pausa")
    if vs200 is not None and vs200 < -5:
        cons.append("Precio por debajo de su media de 200 días")
    if not pros:
        pros.append("Estructura técnica dentro de rango normal")
    if not cons:
        cons.append("Sin señales bajistas relevantes en el gráfico")

    # ── Análisis en lenguaje simple (sin jerga sin explicar) ──────────────
    tendencia_txt = {
        2: "viene en una tendencia claramente al alza: el precio sube de forma sostenida y ordenada",
        1: "está empezando a recuperarse, saliendo de una zona de calma y apuntando hacia arriba",
        3: "está perdiendo fuerza: la subida se frenó y el precio empieza a flaquear",
        4: "viene en una tendencia a la baja: lleva semanas cayendo",
        0: "no muestra una tendencia clara por ahora, se mueve de lado",
    }.get(stage, "no muestra una tendencia clara por ahora")

    partes = [f"En lo técnico (cómo se está comportando el precio en el gráfico), la acción {tendencia_txt}."]

    if vs200 is not None:
        if vs200 >= 0:
            partes.append(
                f"Hoy está un {_pct(vs200)} por encima de su precio promedio del último año, "
                f"lo que confirma que manda la fuerza compradora."
            )
        else:
            partes.append(
                f"Hoy está un {_pct(abs(vs200))} por debajo de su precio promedio del último año, "
                f"una señal de debilidad."
            )

    if rsi is not None:
        if rsi > 75:
            rsi_txt = (f"viene muy acelerada (RSI en {_num(rsi,0)}; el RSI mide de 0 a 100 qué tan "
                       f"rápido ha subido, y por encima de 75 suele estar 'recalentada'), así que "
                       f"podría tomarse una pausa")
        elif rsi < 35:
            rsi_txt = (f"viene muy castigada (RSI en {_num(rsi,0)}; el RSI mide de 0 a 100 qué tan "
                       f"rápido se ha movido, y por debajo de 35 suele estar 'sobrevendida'), de donde "
                       f"a veces vienen rebotes")
        else:
            rsi_txt = (f"se mueve a un ritmo equilibrado (RSI en {_num(rsi,0)}, una zona sana, ni "
                       f"recalentada ni demasiado castigada)")
        partes.append(f"Su pulso de corto plazo {rsi_txt}.")

    if rs6 is not None:
        if rs6 >= 0:
            partes.append(f"Y en los últimos 6 meses le gana al mercado (al S&P 500, el índice de las "
                          f"500 empresas más grandes de EE.UU.) por {_pct(rs6)}.")
        else:
            partes.append(f"Y en los últimos 6 meses va por detrás del mercado (del S&P 500, el índice "
                          f"de las 500 empresas más grandes de EE.UU.) por {_pct(abs(rs6))}.")

    if score >= 66:
        partes.append("En conjunto, el momento del gráfico acompaña.")
    elif score >= 50:
        partes.append("En conjunto, el cuadro técnico es mixto: ni claramente a favor ni en contra.")
    else:
        partes.append("En conjunto, el momento del gráfico todavía no acompaña; conviene tener paciencia.")

    analysis = " ".join(partes)

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": analysis,
        "pros": _top(pros, 3),
        "cons": _top(cons, 2),
        "key_metrics": {
            "stage": f"Stage {stage}",
            "rsi_14": _num(rsi, 0),
            "macd_signal": macd_sig,
            "vs_sma50": _pct(vs50, signed=True),
            "vs_sma200": _pct(vs200, signed=True),
            "rs_vs_spy": f"{_pct(rs6, signed=True)} 6M",
            "pct_from_52w_high": _pct(from_high, signed=True),
            "volume_trend": "en expansión" if (rel_vol or 1) >= 1.1 else "en contracción",
        },
        "sub_scores": {
            "trend_quality": round(trend, 1),
            "momentum": round(mom, 1),
            "setup_quality": round(setup, 1),
        },
        "entry_setup": (
            "Estructura alcista: buscar entradas en pausas o retrocesos hacia medias."
            if stage == 2 else
            "Sin setup alcista confirmado: conviene esperar mejora de tendencia."
        ),
        "key_levels": {
            "support": _money(ind.get("sma_50")),
            "resistance": _money(ind.get("52w_high")),
            "stop_technical": _money(ind.get("sma_200")),
        },
    }


# ════════════════════════════════════════════════════════════════════════
# 3. VIABILIDAD FUTURA  (heurística por código)
# ════════════════════════════════════════════════════════════════════════
_GROWTH_SECTORS = {"Technology", "Healthcare", "Communication Services"}
_STABLE_SECTORS = {"Consumer Defensive", "Utilities", "Consumer Staples"}
_CYCLICAL_SECTORS = {"Energy", "Basic Materials", "Industrials", "Consumer Cyclical"}


def score_future(info, news, ratios, competitive_ctx=None, peer_metrics=None):
    gm = ratios.get("gross_margin")
    om = ratios.get("operating_margin")
    roic = ratios.get("roic")
    rg = ratios.get("revenue_growth_yoy")
    cagr = ratios.get("revenue_cagr_2y")
    sector = info.get("sector", "Unknown") or "Unknown"
    industry = (info.get("industry") or "").lower()
    mktcap = info.get("market_cap", 0) or 0

    # Moat (0-25): márgenes altos + ROIC alto = ventaja competitiva (proxy)
    moat = 0.0
    moat += _lin(gm, 25, 75, 2, 13, default=6)
    moat += _lin(roic, 5, 28, 0, 12, default=4)
    moat = _clamp(moat, 3, 25)

    # Growth runway (0-25): crecimiento + sector con viento de cola
    runway = _lin(rg if rg is not None else cagr, -5, 30, 2, 18, default=8)
    if sector in _GROWTH_SECTORS:
        runway += 5
    elif sector in _STABLE_SECTORS:
        runway += 1
    runway = _clamp(runway, 3, 25)

    # Disruption resilience (0-25): tamaño + estabilidad de sector
    resil = 13.0
    if mktcap >= 5e11:
        resil += 6
    elif mktcap >= 5e10:
        resil += 3
    elif mktcap < 2e9:
        resil -= 3
    if sector in _STABLE_SECTORS:
        resil += 3
    if sector in _CYCLICAL_SECTORS:
        resil -= 1
    resil = _clamp(resil, 4, 25)

    # Management / capital allocation (0-25): ROIC + FCF como proxy
    fcfy = ratios.get("fcf_yield")
    mgmt = _lin(roic, 5, 25, 6, 20, default=11)
    if fcfy is not None and fcfy > 3:
        mgmt += 2
    mgmt = _clamp(mgmt, 4, 25)

    score = round(moat + runway + resil + mgmt, 1)

    moat_strength = "amplio" if moat >= 18 else "estrecho" if moat >= 11 else "ninguno"
    disruption = ("bajo" if resil >= 18 else "medio" if resil >= 12 else "alto")
    tam_growth = ("expansión acelerada" if (rg or 0) >= 18 else
                  "en expansión" if (rg or 0) >= 7 else
                  "estable" if (rg or 0) >= 0 else "en contracción")
    mgmt_q = "excelente" if mgmt >= 19 else "bueno" if mgmt >= 14 else "promedio" if mgmt >= 9 else "deficiente"
    if "software" in industry or "internet" in industry or "semiconduct" in industry:
        biz = "plataforma" if "internet" in industry else "SaaS" if "software" in industry else "otro"
    else:
        biz = "tradicional"
    if gm is not None and gm >= 60 and (roic or 0) >= 15:
        moat_type = "intangibles"
    elif gm is not None and gm >= 45:
        moat_type = "poder de precios"
    elif mktcap >= 1e11:
        moat_type = "ventaja de costos"
    else:
        moat_type = "ninguno"

    pe_val = info.get("forward_pe") or info.get("pe_ratio")
    name_full = (info.get("shortName") or info.get("symbol") or "La empresa")

    pros, cons = [], []

    # PROS — siempre con números e interpretación
    if moat_strength == "amplio":
        pros.append(
            f"Margen bruto {_pct(gm)} + ROIC {_pct(roic)} confirman ventaja competitiva real — "
            f"el negocio genera retornos claramente por encima del costo del capital"
        )
    elif moat_strength == "estrecho" and gm is not None and gm >= 35:
        pros.append(
            f"Margen bruto de {_pct(gm)} sugiere pricing power razonable — "
            f"aunque ROIC {_pct(roic)} todavía no confirma moat defensivo amplio"
        )

    if sector in _GROWTH_SECTORS:
        if (rg or 0) >= 15:
            pros.append(
                f"Sector {sector} con tailwind estructural y la empresa lo está capturando: "
                f"ingresos +{_pct(rg)} interanual — crecimiento secular, no dependiente del ciclo"
            )
        else:
            pros.append(
                f"Sector {sector} con tailwind secular de largo plazo — "
                f"potencial de aceleración si la empresa mejora su ejecución comercial"
            )

    if (rg or 0) >= 25:
        pros.append(
            f"Ingresos creciendo {_pct(rg)} — expansión de demanda de alto octanaje que confirma TAM amplio y tracción del producto"
        )
    elif (rg or 0) >= 10:
        pros.append(
            f"Crecimiento de ingresos de {_pct(rg)} interanual — sólido y por encima del GDP; mercado en expansión"
        )

    if mktcap >= 5e11:
        pros.append(
            f"Escala de ${mktcap/1e9:.0f}B — acceso a capital barato, poder de negociación con proveedores y resiliencia ante disrupciones de nicho"
        )
    elif mktcap >= 1e11:
        pros.append(
            f"Capitalización de ${mktcap/1e9:.0f}B: suficiente escala para defensas competitivas sin ser demasiado grande para crecer"
        )

    if fcfy is not None and fcfy >= 4:
        pros.append(f"FCF yield de {_pct(fcfy)} — genera caja real que puede reinvertir en crecimiento o retornar al accionista")

    # CONS — siempre interpretados, nunca genéricos si hay datos
    if roic is not None and roic < 8 and (rg or 0) >= 12:
        cons.append(
            f"ROIC de {_pct(roic)} con margen operativo de {_pct(om)} — el crecimiento de {_pct(rg)} "
            f"aún no se traduce en retornos reales sobre el capital; la empresa reinvierte casi todo sin crear valor diferencial"
        )
    elif roic is not None and roic < 8:
        cons.append(
            f"ROIC de {_pct(roic)} — el negocio destruye o apenas preserva valor sobre el costo del capital; "
            f"difícil justificar reinversión agresiva a estas tasas de retorno"
        )

    if pe_val and pe_val > 35 and (rg or 0) < 35:
        cons.append(
            f"P/E de {pe_val:.0f}x asume crecimiento de doble dígito por 5+ años — "
            f"poco margen de error si el ritmo de {_pct(rg)} actual se modera"
        )
    elif pe_val and pe_val > 45:
        cons.append(
            f"Valoración de {pe_val:.0f}x ya descuenta un escenario muy optimista — "
            f"cualquier decepción puede comprimir múltiplo fuertemente"
        )

    if moat_strength == "ninguno":
        cons.append(
            f"Sin ventaja competitiva clara: margen bruto {_pct(gm)}, ROIC {_pct(roic)} — "
            f"riesgo de erosión de precios si entra competencia o se modera la demanda"
        )
    elif moat_strength == "estrecho" and sector not in _GROWTH_SECTORS:
        cons.append(
            f"Moat estrecho (ROIC {_pct(roic)}) en sector {sector} sin tailwind estructural — "
            f"vulnerable a presión competitiva cíclica sin el respaldo de un mercado en expansión"
        )

    if disruption == "alto" and mktcap < 5e9:
        cons.append(
            f"Empresa pequeña (${mktcap/1e9:.1f}B) en mercado competitivo — "
            f"riesgo de disrupción o consolidación no es despreciable"
        )

    if sector in _CYCLICAL_SECTORS and (rg or 0) >= 20:
        cons.append(
            f"Crecimiento de {_pct(rg)} en sector cíclico ({sector}) — "
            f"parte del crecimiento puede ser de ciclo económico, no estructural; monitorear en el próximo downturn"
        )

    # Fallback solo si realmente no hay datos suficientes para generar texto
    if not pros:
        pros.append(
            f"Negocio activo con ingresos {'+' if (rg or 0) >= 0 else ''}{_pct(rg)} "
            f"y margen bruto de {_pct(gm)} en sector {sector}"
        )
    if not cons:
        if pe_val and pe_val > 20:
            cons.append(
                f"P/E de {pe_val:.0f}x no deja margen amplio — la ejecución debe ser consistente para justificar la valoración"
            )
        else:
            cons.append(
                f"Sin riesgos estructurales severos identificados — vigilar márgenes ({_pct(om)}) y evolución del ROIC ({_pct(roic)})"
            )

    # TESIS A 5 AÑOS — específica por empresa, con números y razonamiento
    if score >= 75 and moat_strength == "wide":
        if sector in _GROWTH_SECTORS and (rg or 0) >= 15:
            future_thesis = (
                f"{name_full} combina ventaja competitiva amplia (margen bruto {_pct(gm)}, ROIC {_pct(roic)}) "
                f"con un mercado en fuerte expansión ({_pct(rg)} interanual en {sector}). "
                f"Es un candidato serio a compounder de calidad: reinvierte capital a altas tasas en un sector con tailwind secular. "
                f"{'La valoración de ' + str(int(pe_val)) + 'x P/E ya descuenta parte del escenario — la disciplina de entrada sigue siendo clave.' if pe_val and pe_val > 30 else 'La valoración actual no parece un obstáculo material para el retorno a largo plazo.'}"
            )
        else:
            future_thesis = (
                f"{name_full} tiene moat demostrado (ROIC {_pct(roic)}, margen bruto {_pct(gm)}) "
                f"con crecimiento de {_pct(rg)} en {sector}. "
                f"El negocio genera retornos sobre el capital superiores al promedio histórico — "
                f"alta probabilidad de valer más en 5 años si mantiene márgenes y el sector no sufre disrupción estructural."
            )
    elif score >= 60:
        if (rg or 0) >= 15 and moat_strength != "amplio":
            future_thesis = (
                f"{name_full} crece {_pct(rg)} interanual pero con moat '{moat_strength}' "
                f"(ROIC {_pct(roic)}, margen operativo {_pct(om)}). "
                f"El crecimiento es real pero la rentabilidad sobre el capital aún no está a la altura de los mejores compounders. "
                f"{'Con P/E de ' + str(int(pe_val)) + 'x, el mercado ya paga por ese crecimiento — el upside real depende de si los márgenes expanden.' if pe_val and pe_val > 25 else 'A la valoración actual, el perfil riesgo/retorno es razonable si el crecimiento se sostiene los próximos 3-4 años.'}"
            )
        else:
            future_thesis = (
                f"{name_full} muestra un perfil moderado: crecimiento de {_pct(rg)}, margen bruto {_pct(gm)}, ROIC {_pct(roic)}. "
                f"No es un compounder de alto octanaje, pero tampoco un negocio en deterioro. "
                f"El valor a 5 años depende de si la dirección asigna capital eficientemente "
                f"{'y si el sector ' + sector + ' mantiene viento de cola.' if sector in _GROWTH_SECTORS else 'y si logra expandir márgenes operativos desde el ' + _pct(om) + ' actual.'}"
            )
    else:
        future_thesis = (
            f"{name_full} enfrenta dudas estructurales: ROIC de {_pct(roic)} y margen operativo de {_pct(om)} "
            f"{'limitan el poder de compounding a pesar del crecimiento de ' + _pct(rg) + '.' if (rg or 0) >= 10 else 'combinados con crecimiento de ' + _pct(rg) + ' no justifican optimismo estructural.'} "
            f"Sin ventaja competitiva clara "
            f"{'en un sector cíclico (' + sector + '),' if sector in _CYCLICAL_SECTORS else 'en ' + sector + ','} "
            f"la tesis de largo plazo requiere mejora demostrable de márgenes o un cambio de posicionamiento competitivo."
        )

    # ── Enriquecer con contexto competitivo ────────────────────────────────
    # Insertar al principio para que tengan prioridad sobre los algorítmicos
    if competitive_ctx:
        main_edge = competitive_ctx.get("main_edge", "")
        main_threat = competitive_ctx.get("main_threat", "")
        market_pos = competitive_ctx.get("market_position", "")

        if main_edge:
            pros.insert(0, main_edge)
        if main_threat:
            cons.insert(0, main_threat)

        # Añadir comparación numérica con peers si hay datos
        if peer_metrics:
            peer_gms = {t: d["gross_margin"] for t, d in peer_metrics.items() if d.get("gross_margin") is not None}
            if peer_gms and gm is not None:
                # Encontrar el peer más relevante (mayor market cap o primero)
                top_peer = max(peer_metrics.keys(), key=lambda t: peer_metrics[t].get("market_cap") or 0)
                top_peer_gm = peer_gms.get(top_peer)
                top_peer_name = peer_metrics[top_peer].get("name", top_peer)
                if top_peer_gm is not None:
                    gap = gm - top_peer_gm
                    if gap >= 10:
                        pros.append(
                            f"Margen bruto {_pct(gm)} supera a {top_peer_name} ({_pct(top_peer_gm)}) en {_pct(abs(gap))} — "
                            f"ventaja de eficiencia operativa sobre su principal competidor"
                        )
                    elif gap <= -10:
                        cons.append(
                            f"Margen bruto {_pct(gm)} vs {top_peer_name} con {_pct(top_peer_gm)} — "
                            f"brecha de {_pct(abs(gap))} que refleja desventaja competitiva estructural en rentabilidad"
                        )
                    elif abs(gap) <= 5:
                        pass  # muy similares, no añade info

            # Comparación de crecimiento
            peer_rgs = {t: d["revenue_growth"] for t, d in peer_metrics.items() if d.get("revenue_growth") is not None}
            if peer_rgs and rg is not None:
                avg_peer_rg = sum(peer_rgs.values()) / len(peer_rgs)
                if rg > avg_peer_rg + 10:
                    names = ", ".join(peer_metrics[t].get("name", t) for t in list(peer_rgs.keys())[:2])
                    pros.append(
                        f"Crece {_pct(rg)} interanual vs promedio de competidores ({_pct(avg_peer_rg)}) — "
                        f"gana market share sobre {names}"
                    )
                elif rg < avg_peer_rg - 10:
                    names = ", ".join(peer_metrics[t].get("name", t) for t in list(peer_rgs.keys())[:2])
                    cons.append(
                        f"Crecimiento {_pct(rg)} por debajo del promedio competitivo ({_pct(avg_peer_rg)}) — "
                        f"posible pérdida de market share frente a {names}"
                    )

        # Enriquecer tesis con posición de mercado
        if market_pos == "leader" and moat_strength in ("amplio", "estrecho"):
            future_thesis = future_thesis + (
                f" Como líder del sector, tiene el mayor poder para dictar precios y retener talento — "
                f"ventaja compuesta que se agranda con el tiempo si el management no la dilapida."
            )
        elif market_pos == "challenger":
            future_thesis = future_thesis + (
                f" Como retador, el potencial alcista es asimétrico si gana cuota de mercado, "
                f"pero requiere ejecución consistente sin el margen de error de los líderes establecidos."
            )

    analysis = (
        f"Análisis de viabilidad a largo plazo: moat '{moat_strength}' apoyado en margen bruto {_pct(gm)} "
        f"y ROIC {_pct(roic)}. El sector {sector} ofrece runway '{tam_growth}' con crecimiento de {_pct(rg)}. "
        f"Riesgo de disrupción '{disruption}' dado tamaño de ${mktcap/1e9:.0f}B. "
        f"Gestión del capital calificada como '{mgmt_q}' según FCF y retornos."
    )

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": analysis,
        "pros": _top(pros, 3),
        "cons": _top(cons, 2),
        "key_metrics": {
            "moat_type": moat_type,
            "moat_strength": moat_strength,
            "disruption_risk": disruption,
            "tam_growth": tam_growth,
            "management_quality": mgmt_q,
            "business_model": biz,
        },
        "sub_scores": {
            "moat_quality": round(moat, 1),
            "growth_runway": round(runway, 1),
            "disruption_resilience": round(resil, 1),
            "management_capital_allocation": round(mgmt, 1),
        },
        "future_thesis": future_thesis,
        "key_risks": _top(cons, 2),
    }


# ════════════════════════════════════════════════════════════════════════
# 4. SMART MONEY / INSTITUCIONAL
# ════════════════════════════════════════════════════════════════════════
def score_institutional(holders, info):
    inst_pct = holders.get("institutional_ownership_pct")
    if isinstance(inst_pct, (int, float)) and inst_pct <= 1.5:
        inst_pct = inst_pct * 100  # viene como fracción
    insider_buys = holders.get("recent_insider_buys", 0) or 0
    insider_sells = holders.get("recent_insider_sells", 0) or 0
    insiders_held = holders.get("insiders_percent_held")
    inst_count = holders.get("institutions_count")
    # short_percent puede ser None (sin dato, típico en cloud) → lo tratamos
    # como desconocido: score neutral y se muestra "N/D" en vez de un 0% falso.
    short_known = info.get("short_percent") is not None
    short_pct = (info.get("short_percent") or 0) * 100

    # Insider signal (0-33)
    insider = 18.0 + _lin(insider_buys, 0, 5, 0, 14)
    insider = _clamp(insider, 10, 33)

    # Institutional quality (0-33): ownership saludable 40-85%
    if inst_pct is None:
        instq = 19.0
    elif inst_pct < 40:
        instq = _lin(inst_pct, 0, 40, 13, 27)
    elif inst_pct <= 85:
        instq = _lin(inst_pct, 40, 85, 27, 32)
    else:
        instq = _lin(inst_pct, 85, 100, 29, 21)  # crowded
    instq = _clamp(instq, 10, 33)

    # Short interest dynamic (0-34): bajo = sin apuestas en contra
    if short_pct <= 0:
        shortd = 22.0
    elif short_pct < 5:
        shortd = _lin(short_pct, 0, 5, 29, 25)
    elif short_pct < 15:
        shortd = _lin(short_pct, 5, 15, 25, 16)
    else:
        shortd = _lin(short_pct, 15, 30, 16, 7)
    shortd = _clamp(shortd, 6, 34)

    score = round(insider + instq + shortd, 1)
    insider_sig = "alcista" if insider_buys >= 2 else "neutral"
    if not short_known:
        squeeze = "N/D"
    else:
        squeeze = "alto" if short_pct >= 15 else "medio" if short_pct >= 8 else "bajo"
    smart = "acumulando" if insider_buys >= 2 else "neutral"

    pros, cons = [], []
    if insider_buys >= 2:
        pros.append(f"Los directivos compraron acciones {insider_buys} veces hace poco — apuestan con su propio dinero")
    if inst_pct is not None and 40 <= inst_pct <= 85:
        pros.append(f"Respaldo sólido de grandes fondos ({_pct(inst_pct,0)} de la empresa en sus manos)")
    if 0 < short_pct < 5:
        pros.append("Casi nadie apuesta a que la acción baje (short interest muy bajo)")
    if short_pct >= 15:
        cons.append(f"Bastantes inversores apuestan a que baje (short interest del {_pct(short_pct,0)} del float)")
    if insider_sells >= 3 and insider_buys == 0:
        cons.append(f"Los directivos solo vendieron ({insider_sells} ventas, 0 compras) — sin señal de convicción compradora")
    if inst_pct is not None and inst_pct > 90:
        cons.append("Demasiada concentración de fondos (poco margen para que entren más compradores grandes)")
    if not pros:
        pros.append("Sin señales claramente negativas en el flujo de dinero grande")
    if not cons:
        cons.append("Sin señales de alerta en el posicionamiento institucional")

    # ── Análisis en lenguaje simple ──────────────────────────────────────
    partes = []
    if inst_pct is not None:
        cuenta = f" (unos {inst_count:,} fondos)" if inst_count else ""
        partes.append(f"Los grandes fondos de inversión tienen el {_pct(inst_pct,0)} de la empresa{cuenta}, "
                      f"lo que se llama propiedad institucional: cuando es alta y sana, indica que el dinero "
                      f"profesional confía en el negocio.")
    if insider_buys or insider_sells:
        if insider_buys >= 2 and insider_buys >= insider_sells:
            partes.append(f"Y algo llamativo: los propios directivos compraron acciones {insider_buys} veces "
                          f"en las operaciones recientes — cuando ponen su dinero, suele ser buena señal.")
        elif insider_buys == 0 and insider_sells >= 1:
            partes.append(f"En las operaciones recientes los directivos solo vendieron ({insider_sells} ventas), "
                          f"algo normal por liquidez o impuestos, pero sin compras que confirmen convicción.")
        else:
            partes.append(f"Los directivos registraron {insider_buys} compras y {insider_sells} ventas recientes "
                          f"(actividad mixta de insiders).")
    if short_pct > 0:
        nivel = ("muy bajo" if short_pct < 3 else "moderado" if short_pct < 10 else "alto")
        partes.append(f"El short interest (cuántos apuestan a que la acción baje) es del {_pct(short_pct,1)} del "
                      f"float — un nivel {nivel}.")
    if not partes:
        partes.append("No hay suficientes datos de flujo institucional en este momento para sacar conclusiones firmes.")
    analysis = " ".join(partes)

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": analysis,
        "pros": _top(pros, 2),
        "cons": _top(cons, 2),
        "key_metrics": {
            "institutional_ownership": _pct(inst_pct, 0),
            "insider_buying_signal": insider_sig,
            "short_interest": _pct(short_pct, 1) if short_known else "N/D",
            "squeeze_potential": squeeze,
            "smart_money_signal": smart,
        },
        "sub_scores": {
            "insider_signal": round(insider, 1),
            "institutional_quality": round(instq, 1),
            "short_interest_dynamic": round(shortd, 1),
        },
        "key_insight": (
            (f"Lo más relevante: los directivos están comprando ({insider_buys} compras recientes), "
             f"la señal interna más valiosa, con un respaldo institucional del {_pct(inst_pct,0)}.")
            if insider_buys >= 2 else
            (f"Lo más relevante: respaldo institucional del {_pct(inst_pct,0)} pero sin compras de "
             f"directivos que confirmen convicción interna" +
             (f"; pocos apuestan en contra (short interest {_pct(short_pct,0)})."
              if short_known else "."))
        ),
    }


# ════════════════════════════════════════════════════════════════════════
# 5. RIESGO & SIZING
# ════════════════════════════════════════════════════════════════════════
def score_risk(risk_metrics, info, ind):
    rr = risk_metrics.get("rr_ratio", 0) or 0
    atr_pct = risk_metrics.get("atr_pct")
    risk_pct = risk_metrics.get("risk_pct")
    reward_pct = risk_metrics.get("reward_pct")
    price = risk_metrics.get("current_price")
    stop = risk_metrics.get("stop_suggested")
    target = risk_metrics.get("target_suggested")
    pos = risk_metrics.get("implied_portfolio_pct", 0)
    beta = risk_metrics.get("beta", info.get("beta", 1.0)) or 1.0

    # Risk/Reward quality (0-40)
    rrq = _lin(rr, 0.5, 3.0, 6, 40)
    rrq = _clamp(rrq, 4, 40)

    # Volatility manageability (0-30): menor ATR = mejor
    volm = _lin(atr_pct, 1.2, 7.0, 30, 8, default=18)
    volm = _clamp(volm, 5, 30)

    # Downside protection (0-30): stop razonable + beta
    dprot = _lin(risk_pct, 25, 6, 8, 26, default=16)  # stop muy lejano = peor
    if beta <= 1.1:
        dprot += 3
    elif beta >= 1.8:
        dprot -= 3
    dprot = _clamp(dprot, 4, 30)

    score = round(rrq + volm + dprot, 1)

    if rr >= 2.5:
        adir, astr = "alcista", "fuerte"
    elif rr >= 1.5:
        adir, astr = "alcista", "moderado"
    elif rr >= 0.85:
        adir, astr = "equilibrado", "moderado"
    elif rr >= 0.5:
        adir, astr = "bajista", "moderado"
    else:
        adir, astr = "bajista", "fuerte"

    pros, cons = [], []
    if rr >= 1.8:
        pros.append(f"Relación riesgo/beneficio favorable ({rr:.1f}:1): se arriesga poco para lo que se puede ganar")
    elif rr >= 1.2:
        pros.append(f"Relación riesgo/beneficio aceptable ({rr:.1f}:1)")
    if atr_pct is not None and atr_pct <= 3:
        pros.append(f"Volatilidad contenida (ATR {_pct(atr_pct)} diario): permite stops ajustados sin salir por ruido")
    if beta <= 1.1:
        pros.append(f"Beta {_num(beta,2)}: se mueve algo menos que el mercado, menos vaivén en la cartera")
    if risk_pct is not None and risk_pct <= 8:
        pros.append(f"Stop cercano ({_pct(risk_pct)} desde la entrada): la pérdida máxima queda acotada y definida")
    if rr < 1.2:
        cons.append(f"Riesgo/beneficio ajustado ({rr:.1f}:1): el recorrido al objetivo compensa poco el riesgo asumido")
    if atr_pct is not None and atr_pct > 5:
        cons.append(f"Volatilidad alta (ATR {_pct(atr_pct)} diario): obliga a reducir el tamaño de posición y ampliar el stop")
    if beta >= 1.6:
        cons.append(f"Beta {_num(beta,2)}: amplifica los movimientos del mercado, más riesgo en correcciones")
    if risk_pct is not None and risk_pct > 15:
        cons.append(f"Stop lejano ({_pct(risk_pct)}): activarlo implica aguantar una caída amplia antes de salir")
    if not pros:
        pros.append("Niveles de entrada, protección y objetivo bien definidos")
    if not cons:
        cons.append("Sin riesgos cuantitativos extremos detectados")

    # ── Análisis en prosa natural (patrón partes) ────────────────────────────
    partes = []
    if rr:
        rr_txt = ("muy favorable" if rr >= 2.5 else "favorable" if rr >= 1.8 else
                  "aceptable" if rr >= 1.2 else "ajustada")
        partes.append(
            f"El plan de trade parte de una entrada en {_money(price)}, con protección en "
            f"{_money(stop)} ({_pct(risk_pct, signed=True)}) y objetivo en {_money(target)} "
            f"({_pct(reward_pct, signed=True)}): una relación riesgo/beneficio de {rr:.1f}:1, {rr_txt} — "
            f"por cada dólar en riesgo se apunta a ganar {rr:.1f}.")
    if atr_pct is not None:
        vol_txt = ("baja" if atr_pct <= 2 else "moderada" if atr_pct <= 4 else "alta")
        efecto = ("y permite operar con tamaño normal y stops ajustados" if atr_pct <= 4 else
                  "por lo que conviene reducir el tamaño de la posición y dar más aire al stop para no salir por ruido")
        partes.append(f"La volatilidad es {vol_txt} (ATR diario {_pct(atr_pct)}), {efecto}.")
    beta_txt = ("se mueve menos que el mercado" if beta <= 0.9 else
                "acompaña al mercado de cerca" if beta <= 1.2 else
                "amplifica los vaivenes del mercado")
    partes.append(f"Con beta {_num(beta,2)}, la acción {beta_txt}, algo a calibrar según cómo esté el entorno general.")
    if pos:
        port_loss = (pos or 0) * (risk_pct or 0) / 100.0
        partes.append(
            f"Con el sizing implícito ({_pct(pos, 1)} de la cartera), tocar el stop supondría una pérdida "
            f"acotada de aproximadamente {_pct(port_loss, 2)} del capital total — el riesgo por operación "
            f"queda bajo control.")
    # Frase de cierre (la idea de riesgo más importante) — da estructura y peso.
    partes.append(
        "En conjunto, " + (
            f"el perfil es asimétrico a favor (R/R {rr:.1f}:1): hay más para ganar que para perder "
            f"siempre que se respete el stop." if rr >= 1.8 else
            f"el margen es ajustado (R/R {rr:.1f}:1): exige disciplina estricta con el stop y un tamaño "
            f"de posición prudente." if rr < 1.2 else
            f"es un perfil equilibrado (R/R {rr:.1f}:1) cuyo éxito depende de respetar los niveles definidos."))
    analysis = " ".join(partes) if partes else "Niveles de riesgo definidos."

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": analysis,
        "pros": _top(pros, 3),
        "cons": _top(cons, 3),
        "key_metrics": {
            "entry_price": _money(price),
            "stop_loss": _money(stop),
            "target_price": _money(target),
            "risk_reward": f"{rr:.1f}:1",
            "max_loss_pct": _pct(-(risk_pct or 0), signed=True),
            "potential_gain_pct": _pct(reward_pct, signed=True),
            "position_size_pct": _pct(pos, 1),
            "volatility_atr_pct": _pct(atr_pct),
        },
        "sub_scores": {
            "risk_reward_quality": round(rrq, 1),
            "volatility_manageability": round(volm, 1),
            "downside_protection": round(dprot, 1),
        },
        "asymmetry_direction": adir,
        "asymmetry_strength": astr,
        "stop_rationale": (
            "El nivel de protección se ubica bajo el mínimo reciente de oscilación."
        ),
    }


# ════════════════════════════════════════════════════════════════════════
# 6. CONTEXTO DE MERCADO  (macro + sentimiento + catalizadores)
# ════════════════════════════════════════════════════════════════════════
_POS_WORDS = ["beat", "beats", "surge", "soar", "record", "upgrade", "raise", "raised",
              "growth", "strong", "wins", "win", "approval", "approved", "rally", "jump",
              "outperform", "buyback", "expand", "partnership", "launch"]
_NEG_WORDS = ["miss", "misses", "plunge", "fall", "drop", "downgrade", "cut", "lawsuit",
              "probe", "investigation", "recall", "warns", "warning", "weak", "decline",
              "layoff", "layoffs", "fraud", "delay", "slump", "loss"]

# Familias temáticas para clasificar la NARRATIVA a partir de los titulares
# reales (antes era binario por sector → todas "defensivo"). El tema dominante
# es la familia con más coincidencias en los títulos.
_THEME_KEYWORDS = {
    "resultados": ["earnings", "revenue", "profit", "eps", "guidance", "results",
                   "beat", "miss", "quarter", "sales", "margin", "forecast"],
    "producto e innovación": ["launch", "product", "release", "unveil", "chip", "model",
                              "feature", "rollout", "ai", "device", "update", "version"],
    "legal y regulatorio": ["lawsuit", "probe", "investigation", "regulator", "antitrust",
                            "fine", "sec", "ftc", "court", "settlement", "ruling", "ban",
                            "subpoena", "fraud", "scandal"],
    "movimiento de analistas": ["upgrade", "downgrade", "rating", "price target", "analyst",
                                "overweight", "underweight", "initiate", "reiterate", "buy",
                                "sell"],
    "fusiones y adquisiciones": ["acquisition", "acquire", "merger", "deal", "buyout",
                                 "stake", "takeover", "acquires", "acquired"],
    "macro y sector": ["fed", "rates", "inflation", "tariff", "tariffs", "economy",
                       "jobs", "cpi", "recession", "yields"],
}
# Solo estas palabras cuentan como RIESGO REPUTACIONAL real (antes se usaba el
# conteo total de negativas → casi siempre "medio").
_REPUTATIONAL_WORDS = ["lawsuit", "probe", "investigation", "fine", "recall", "fraud",
                       "scandal", "regulator", "antitrust", "breach", "settlement",
                       "misconduct", "subpoena", "sue", "sued", "ban"]


def _score_macro(macro, info):
    sector = (info.get("sector") or "").lower()
    vix = (macro.get("vix") or {}).get("current")
    yc = macro.get("yield_curve_spread")
    sp_1m = (macro.get("sp500") or {}).get("1m_change")
    sector_perf = macro.get("sector_performance", {}) or {}

    env = 19.5  # macro_environment (0-34) — baseline ligeramente más optimista
    if vix is not None:
        env += _lin(vix, 12, 35, 12, -8)
    if sp_1m is not None:
        env += _lin(sp_1m, -6, 6, -4, 4)
    env = _clamp(env, 6, 34)

    # sector_rotation (0-33): ¿el sector de la empresa va bien?
    sec_ret = None
    for name, ret in sector_perf.items():
        if name and name.lower()[:4] in sector:
            sec_ret = ret
            break
    rot = _lin(sec_ret, -15, 25, 12, 32, default=18.5)
    rot = _clamp(rot, 6, 33)

    # liquidity_conditions (0-33): curva + VIX
    liq = 19.5
    if yc is not None:
        liq += _lin(yc, -1.0, 1.5, -6, 8)  # invertida = peor
    if vix is not None:
        liq += _lin(vix, 12, 35, 6, -6)
    liq = _clamp(liq, 6, 33)

    score = round(env + rot + liq, 1)
    # Etiquetas honestas: si de verdad falta el dato (raro con FRED), "N/D" en
    # vez de un valor por defecto engañoso (antes VIX None→"neutral", curva
    # None→"plana" para cualquier acción).
    vix_level = ("N/D" if vix is None else
                 "bajo <20" if vix < 20 else "elevado 20-30" if vix <= 30 else "alto >30")
    yc_state = ("N/D" if yc is None else
                "invertida" if yc < 0 else "plana" if yc < 0.3 else "normal")
    market_env = ("N/D" if vix is None else
                  "apetito por riesgo" if vix < 18 else
                  "aversión al riesgo" if vix > 28 else "neutral")
    sec_mom = ("N/D" if sec_ret is None else
               "fuerte" if sec_ret > 8 else "débil" if sec_ret < 0 else "neutral")

    pros, cons = [], []
    if (vix or 20) < 18:
        pros.append("Mercado en modo apetito de riesgo (VIX bajo)")
    if (sec_ret or 0) > 5:
        pros.append(f"El sector rota hacia arriba ({_pct(sec_ret, signed=True)} 1A)")
    if (yc or 0) >= 0.3:
        pros.append("Curva de tasas normal: condiciones de liquidez sanas")
    if (vix or 20) > 28:
        cons.append("Mercado nervioso (VIX elevado)")
    if (yc or 0) < 0:
        cons.append("Curva de tasas invertida: señal de cautela")
    if (sec_ret or 0) < 0:
        cons.append("El sector viene rezagado")
    if not pros:
        pros.append("Entorno macro sin vientos de cola ni en contra marcados")
    if not cons:
        cons.append("Riesgos macro acotados por ahora")

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": (
            f"VIX en {_num(vix,1)} ({vix_level}), curva de tasas "
            f"'{yc_state}' y el sector con {_pct(sec_ret, signed=True)} en el último año. "
            f"Entorno general: {market_env}."
        ),
        "pros": _top(pros, 3),
        "cons": _top(cons, 3),
        "key_metrics": {
            "market_environment": market_env,
            "rate_sensitivity": "baja",
            "sector_momentum": sec_mom,
            "vix_level": vix_level,
            "yield_curve": yc_state,
            "dollar_impact": "neutral",  # mantener en neutral (válido en español también)
        },
        "sub_scores": {
            "macro_environment": round(env, 1),
            "sector_rotation": round(rot, 1),
            "liquidity_conditions": round(liq, 1),
        },
        "macro_verdict": (
            "El macro es viento de cola" if score >= 65 else
            "El macro es neutro" if score >= 50 else "El macro es viento en contra"
        ),
    }


def _score_sentiment(news, info):
    n = len(news)
    fresh = sum(1 for it in news if (it.get("age_hours") or 9999) < 168)
    pos = neg = 0
    for it in news:
        t = (it.get("title") or "").lower()
        pos += sum(1 for w in _POS_WORDS if w in t)
        neg += sum(1 for w in _NEG_WORDS if w in t)

    # news_sentiment (0-34) — base ligeramente más optimista, nudge por keywords
    base = 18.0
    if pos + neg > 0:
        base += _lin((pos - neg) / (pos + neg), -1, 1, -10, 10)
    news_s = _clamp(base, 7, 34)

    # narrative_momentum (0-33): cobertura reciente
    narr = _clamp(13.5 + _lin(fresh, 0, 8, 0, 14), 8, 33)

    # contrarian_value (0-33): neutral salvo extremos
    contr = 17.0
    if neg > pos and neg >= 3:
        contr += 6  # narrativa negativa = posible valor contrario
    contr = _clamp(contr, 8, 33)

    score = round(news_s + narr + contr, 1)
    overall = "alcista" if pos > neg else "bajista" if neg > pos else "neutral"

    # ── Tema narrativo REAL: familia de keywords dominante en los titulares ──
    theme_hits = {fam: 0 for fam in _THEME_KEYWORDS}
    rep_hits = 0
    titles = [(it.get("title") or "").lower() for it in news]
    for t in titles:
        for fam, words in _THEME_KEYWORDS.items():
            theme_hits[fam] += sum(1 for w in words if w in t)
        rep_hits += sum(1 for w in _REPUTATIONAL_WORDS if w in t)
    best_theme = max(theme_hits, key=theme_hits.get) if titles else None
    narrative_theme = best_theme if (best_theme and theme_hits[best_theme] > 0) else (
        "sin cobertura" if n == 0 else "general")

    # ── Riesgo reputacional: SOLO keywords reputacionales (no ruido) ─────────
    reputational_risk = ("N/D" if n == 0 else
                         "alto" if rep_hits >= 3 else
                         "medio" if rep_hits >= 1 else "bajo")

    # ── Momentum de sentimiento: tendencia TEMPORAL real (reciente vs previo) ─
    def _tone(items):
        p = q = 0
        for it in items:
            t = (it.get("title") or "").lower()
            p += sum(1 for w in _POS_WORDS if w in t)
            q += sum(1 for w in _NEG_WORDS if w in t)
        d = p - q
        c = p + q
        return (d / c) if c else 0.0, c
    recent_items = [it for it in news if (it.get("age_hours") or 9999) < 72]
    older_items = [it for it in news if (it.get("age_hours") or 9999) >= 72]
    if n == 0:
        mom = "N/D"
    elif recent_items and older_items:
        rt, _ = _tone(recent_items)
        ot, _ = _tone(older_items)
        mom = ("mejorando" if rt - ot > 0.15 else
               "deteriorándose" if rt - ot < -0.15 else "estable")
    else:
        mom = "mejorando" if pos > neg else "deteriorándose" if neg > pos else "estable"

    # ── Señal contraria: por DIVERGENCIA sentimiento vs precio/valuación ─────
    pe = info.get("pe_ratio")
    hi = info.get("52w_high") or 0
    lo = info.get("52w_low") or 0
    px = info.get("current_price") or 0
    pos_in_range = ((px - lo) / (hi - lo)) if (hi and lo and hi > lo and px) else None
    contra = "sin señal"
    if neg > pos and neg >= 2:
        # Malas noticias pero el precio aguanta arriba → el mercado las ignora
        contra = ("comprar el miedo" if (pos_in_range is None or pos_in_range > 0.4)
                  else "sin señal")
    elif pos > neg and pos >= 4 and (
            (pe and pe > 45) or (pos_in_range is not None and pos_in_range > 0.9)):
        # Euforia + valuación estirada o precio en máximos → cautela
        contra = "vender la euforia"

    # ── Prosa natural (patrón partes) ────────────────────────────────────────
    partes = []
    if n == 0:
        partes.append("No hay noticias recientes suficientes para leer la narrativa de esta "
                      "acción; el sentimiento queda sin señal clara por ahora.")
    else:
        tono_txt = ("mayormente positivo" if pos > neg else
                    "mayormente negativo" if neg > pos else "equilibrado")
        partes.append(f"De {n} titulares recientes ({fresh} de esta semana), el tono es "
                      f"{tono_txt} ({pos} señales positivas frente a {neg} negativas) y la "
                      f"narrativa gira en torno a {narrative_theme}.")
        if mom == "mejorando":
            partes.append("Y hay una mejora: las noticias más frescas suenan mejor que las de "
                          "días atrás, señal de momentum de sentimiento al alza.")
        elif mom == "deteriorándose":
            partes.append("Ojo: el tono se está deteriorando: los titulares más recientes son "
                          "peores que los previos, un momentum de sentimiento a la baja.")
        if reputational_risk in ("medio", "alto"):
            partes.append(f"Además aparecen señales de riesgo reputacional {reputational_risk} "
                          f"(temas legales/regulatorios), que conviene vigilar de cerca.")
        if contra == "comprar el miedo":
            partes.append("Detalle contrario interesante: pese a las malas noticias, el precio "
                          "aguanta — el mercado parece estar mirando más allá del ruido.")
        elif contra == "vender la euforia":
            partes.append("Señal de cautela contraria: euforia mediática con valuación exigente; "
                          "conviene no comprar solo por el hype.")
    analysis = " ".join(partes)

    # ── Pros / cons con criterio ─────────────────────────────────────────────
    pros, cons = [], []
    if pos > neg:
        pros.append(f"Titulares recientes con tono mayormente positivo ({pos} vs {neg}) en torno a {narrative_theme}")
    if mom == "mejorando":
        pros.append("El sentimiento mejora: las noticias más recientes son mejores que las previas")
    if fresh >= 3:
        pros.append(f"Cobertura mediática activa ({fresh} noticias esta semana): la acción está en el radar")
    if contra == "comprar el miedo":
        pros.append("Posible sobre-reacción negativa: el precio ignora las malas noticias (valor contrario)")
    if neg > pos:
        cons.append(f"Titulares recientes con tono mayormente negativo ({neg} vs {pos})")
    if mom == "deteriorándose":
        cons.append("El sentimiento se deteriora: el tono empeora en los titulares más recientes")
    if reputational_risk in ("medio", "alto"):
        cons.append(f"Riesgo reputacional {reputational_risk}: hay titulares legales/regulatorios")
    if contra == "vender la euforia":
        cons.append("Euforia con valuación estirada: riesgo de comprar caro por el hype")
    if n == 0:
        cons.append("Sin noticias recientes para evaluar la narrativa")
    if not pros:
        pros.append("Sin señales fuertes de sentimiento en las noticias")
    if not cons:
        cons.append("Narrativa estable, sin riesgos evidentes")

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": analysis,
        "pros": _top(pros, 3),
        "cons": _top(cons, 3),
        "key_metrics": {
            "overall_sentiment": overall,
            "sentiment_momentum": mom,
            "narrative_theme": narrative_theme,
            "contrarian_signal": contra,
            "reputational_risk": reputational_risk,
        },
        "sub_scores": {
            "news_sentiment": round(news_s, 1),
            "narrative_momentum": round(narr, 1),
            "contrarian_value": round(contr, 1),
        },
        "dominant_narrative": (
            f"La conversación sobre la acción gira en torno a {narrative_theme}, con un tono "
            f"global {overall} ({max(pos, neg)} señales dominantes)."
            if n else "Sin noticias recientes disponibles para leer la narrativa."
        ),
        "opportunity": (
            "Narrativa negativa con precio resistente: vigilar posible sobre-reacción (valor contrario)."
            if contra == "comprar el miedo" else
            "Euforia mediática con valuación exigente: cautela con entradas por hype."
            if contra == "vender la euforia" else "No hay divergencia clara entre narrativa y precio."
        ),
    }


def _score_catalysts(earnings, info):
    days = earnings.get("days_to_next_earnings")
    beat = earnings.get("beat_count")
    hist = earnings.get("earnings_history", []) or []
    avg_surp = earnings.get("avg_surprise")
    total = len(hist)

    # earnings_momentum (0-34)
    em = 18.0
    if avg_surp is not None:
        em += _lin(avg_surp, -10, 15, -8, 10)
    em = _clamp(em, 6, 34)

    # catalyst_quality (0-33): proximidad del próximo earnings
    if days is None:
        cq = 17.0
    elif days <= 30:
        cq = _lin(days, 0, 30, 32, 24)
    elif days <= 90:
        cq = _lin(days, 30, 90, 24, 17)
    else:
        cq = _lin(days, 90, 200, 17, 11)
    cq = _clamp(cq, 9, 33)

    # analyst_revision_trend (0-33): proxy por beat rate
    if total > 0 and beat is not None:
        art = _lin(beat / total, 0, 1, 14, 32)
    else:
        art = 18.0
    art = _clamp(art, 9, 33)

    score = round(em + cq + art, 1)
    timeline = ("30d" if (days or 999) <= 30 else "90d" if (days or 999) <= 90 else
                "180d" if (days or 999) <= 180 else ">180d")
    trend = ("mejorando" if (avg_surp or 0) > 2 else "deteriorándose" if (avg_surp or 0) < -2 else "estable")

    pros, cons = [], []
    if days is not None and days <= 30:
        pros.append(f"Earnings próximos (en {days} días): catalizador cercano")
    if beat is not None and total and beat / total >= 0.6:
        pros.append(f"Buen historial de superar expectativas ({beat}/{total})")
    if avg_surp is not None and avg_surp > 3:
        pros.append(f"Sorpresa promedio positiva ({_pct(avg_surp, signed=True)})")
    if days is not None and days > 120:
        cons.append("Próximo catalizador de earnings lejano")
    if beat is not None and total and beat / total < 0.4:
        cons.append("Historial flojo en earnings (más misses que beats)")
    if not pros:
        pros.append("Calendario de catalizadores dentro de lo normal")
    if not cons:
        cons.append("Sin riesgos de evento inminentes detectados")

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": (
            f"Próximo earnings en {days if days is not None else 'N/A'} días. "
            f"Historial: {beat if beat is not None else 'N/A'}/{total} beats "
            f"con sorpresa promedio {_pct(avg_surp, signed=True)}."
        ),
        "pros": _top(pros, 3),
        "cons": _top(cons, 3),
        "key_metrics": {
            "next_earnings": earnings.get("next_earnings", "N/A"),
            "earnings_beat_rate": f"{beat}/{total}" if beat is not None else "N/A",
            "avg_earnings_surprise": _pct(avg_surp, signed=True),
            "analyst_sentiment_trend": trend,
            "catalyst_timeline": timeline,
            "key_upcoming_event": f"Earnings {earnings.get('next_earnings', 'N/A')}",
        },
        "sub_scores": {
            "earnings_momentum": round(em, 1),
            "catalyst_quality": round(cq, 1),
            "analyst_revision_trend": round(art, 1),
        },
        "top_catalyst": (
            f"Próximo reporte de resultados ({earnings.get('next_earnings', 'N/A')})."
        ),
    }


def score_market_context(macro, news, earnings, info):
    return {
        "macro": _score_macro(macro, info),
        "sentiment": _score_sentiment(news, info),
        "catalysts": _score_catalysts(earnings, info),
    }
