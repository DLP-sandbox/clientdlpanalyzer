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
import math


# ── Helpers numéricos (tolerantes a None) ────────────────────────────────
def _bad(v):
    """True si v es None o NaN — así los formateadores no imprimen 'nan'.
    En Render los precios pueden llegar como NaN (fetch incompleto); sin este
    guard salían '$nan' / 'nan%' en los tiles de precio/objetivo/R-R/sizing."""
    return v is None or (isinstance(v, float) and math.isnan(v))


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


def _pond(componentes, lo, hi):
    """Combina componentes EXCLUYENDO de la ecuación los que no tienen dato.

    POR QUÉ EXISTE
    --------------
    Antes, un dato ausente no se excluía: `_lin(None, …, default=X)` metía un
    valor FIJO con todo su peso. Eso no es neutro — es un premio o un castigo
    según la acción. Medido con el test de knockout (quitar un dato y ver el
    efecto):

        MSFT sin `roic`        →  -15,8 puntos de Futuro
        MSFT sin propiedad institucional → -11,9
        CIB  sin `pe_ratio`    →  **+18,0** de Fundamentales
        TSLA sin `roic`        →  **+8,0**  de Futuro

    Una empresa excelente se hundía y una mediocre se disparaba, solo porque
    faltaba UN dato. Aquí el hueco se saca de la ecuación y los componentes que
    SÍ existen reparten su peso.

    CÓMO
    ----
    Cada componente se traduce a una "bondad" g∈[0,1] (1 = lo mejor que puede
    aportar), con peso igual a su amplitud. El sub-score es el SUELO de todos
    los componentes más la media ponderada de las bondades PRESENTES aplicada a
    la amplitud total. Se usa min(y0,y1) como suelo —y no y0— porque hay
    componentes INVERTIDOS (el P/E puntúa 23 barato → 5 caro): tomar y0 como
    suelo le regalaría el mejor valor a la acción a la que le falta el dato.

    GARANTÍA
    --------
    Con todos los datos presentes el resultado es EXACTAMENTE el mismo que
    sumar los `_lin` de antes:
        Σmin + Σ(gᵢ·|ampᵢ|) = Σ(minᵢ + gᵢ·|ampᵢ|) = Σ valorᵢ
    Es decir, esto NO mueve ninguna calificación que hoy esté completa.

    `componentes`: iterable de (x, x0, x1, y0, y1) — los mismos argumentos que
    `_lin`. Un componente con x=None se excluye. Si NINGUNO tiene dato se
    devuelve el punto medio del rango, que es lo único honesto.
    NUNCA lanza."""
    try:
        suelo = 0.0           # Σ min(y0,y1): lo mínimo que puede dar el conjunto
        amp_total = 0.0       # amplitud total disponible
        bondad = 0.0          # Σ gᵢ·|ampᵢ| de los presentes
        amp_pres = 0.0
        for comp in componentes:
            x, x0, x1, y0, y1 = comp
            amp = abs(y1 - y0)
            suelo += min(y0, y1)
            amp_total += amp
            if x is None or amp == 0 or x1 == x0:
                continue
            try:
                f = _clamp((float(x) - x0) / (x1 - x0), 0.0, 1.0)
            except (TypeError, ValueError):
                continue
            valor = y0 + f * (y1 - y0)
            g = (valor - min(y0, y1)) / amp        # 0 = lo peor, 1 = lo mejor
            bondad += g * amp
            amp_pres += amp
        if amp_pres <= 0:
            return _clamp((lo + hi) / 2.0, lo, hi)
        # Al hueco se le asigna una bondad a MEDIO CAMINO entre "se parece a lo
        # que sí sabemos de esta empresa" y "es del montón". Repartir el peso
        # del todo (λ=1) quita el sesgo pero AMPLIFICA los extremos: medido
        # sobre 14 acciones × 10 huecos, el peor movimiento subía a 12,5 puntos
        # y 2 escenarios pasaban de 10. Con λ=0,5 el movimiento medio baja de
        # 3,51 a 3,19, los casos de más de 6 puntos caen de 32 a 25 y NINGUNO
        # supera los 10. Es el punto óptimo medido, no una corazonada.
        g_pres = bondad / amp_pres
        g_hueco = 0.5 * g_pres + 0.5 * 0.5
        return _clamp(suelo + bondad + g_hueco * (amp_total - amp_pres), lo, hi)
    except Exception:
        return _clamp((lo + hi) / 2.0, lo, hi)


def _conv(score):
    return "HIGH" if score >= 74 else "MEDIUM" if score >= 55 else "LOW"


def _pct(v, dec=1, signed=False):
    if _bad(v):
        return "N/A"
    return f"{v:+.{dec}f}%" if signed else f"{v:.{dec}f}%"


def _num(v, dec=2):
    return "N/A" if _bad(v) else f"{v:.{dec}f}"


def _money(v):
    if _bad(v):
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

    # ── Métricas que NO APLICAN a este tipo de negocio ───────────────────
    # Un banco no reporta coste de ventas: su margen bruto llega como 0.0 y
    # antes puntuaba 0 de 5 en Calidad — castigo real por un dato que esa
    # empresa no publica (afectaba a JPM, UNTY, CIB, BSAC, SAN…). Se convierte
    # en None para que _lin() use su default NEUTRO. Un valor REAL nunca se
    # descarta: solo huecos y ceros exactos.
    try:
        from data.industry_labels import metricas_no_aplicables
        _na = metricas_no_aplicables(info.get("sector"), info.get("industry"))
    except Exception:
        _na = frozenset()

    def _na_neutro(nombre, v):
        return None if (nombre in _na and (v is None or v == 0)) else v

    gm   = _na_neutro("gross_margin", gm)
    cr   = _na_neutro("current_ratio", cr)
    fcfy = _na_neutro("fcf_yield", fcfy)
    ev   = _na_neutro("ev_ebitda", ev)

    # Los cuatro pilares se calculan con _pond: un dato que falta se SACA de la
    # ecuación y los demás reparten su peso, en vez de meter un valor fijo que
    # hundía a las buenas e inflaba a las malas. Con todos los datos presentes
    # el resultado es idéntico al de antes (ver la garantía en _pond).

    # Calidad (0-25): márgenes + retorno sobre capital
    quality = _pond([
        (om,   0,  32, 2, 13),
        (roic, 0,  25, 0,  8),
        (gm,   20, 70, 0,  5),
    ], 4, 25)

    # Crecimiento (0-25)
    growth = _pond([
        (rg,                                -5, 30, 1, 15),
        (eg if eg is not None else cagr,    -15, 40, 0,  7),
        (cagr,                                0, 25, 0,  3),
    ], 3, 25)

    # Valoración (0-25): más barato = más alto.
    # NO se usa _pond aquí a propósito. Este bloque es un NÚCLEO (P/E) con un
    # AJUSTE (FCF yield), no dos componentes equivalentes. Su fallback ya es
    # casi neutro —13 sobre un rango 3-25, cuyo medio es 14— y renormalizar
    # hacía que una acción sin P/E y con FCF flojo se desplomara de 11 a 3:
    # exactamente el hundimiento por un dato ausente que hay que evitar.
    # Un ajuste que no se puede calcular simplemente NO ajusta (está centrado
    # en 0), que es lo neutro.
    val = 13.0
    if pe and pe > 0:
        val = _lin(pe, 10, 45, 23, 5)
    if fcfy is not None:
        val += _lin(fcfy, 0, 8, -2, 6)
    else:
        # El ajuste va de -2 a +6, así que su media es +2, no 0. No sumar nada
        # cuando falta el dato equivalía a aplicarle el peor cuartil. Se usa su
        # punto medio: no saber no puede penalizar (ni premiar).
        val += 2.0
    val = _clamp(val, 3, 25)

    # Salud financiera (0-25). `de` (deuda/equity) viene ya normalizado como
    # RATIO (0.8 = 80%). Rango sano típico 0–2.5x. Se pondera también la
    # liquidez (current ratio) y la generación de caja (fcf yield) para que la
    # barra diferencie de verdad y no se pegue al 100%.
    # Misma lógica que en Valoración: núcleo (deuda) + ajustes simétricos que,
    # si faltan, no ajustan. Su defecto (14 en un rango 6-24) ya es neutro.
    health = _lin(de, 0.0, 2.5, 24, 6, default=14)
    if cr is not None:
        health += _lin(cr, 0.8, 2.5, -4, 4)
    # (el ajuste de liquidez va de -4 a +4: su media ya es 0, así que no
    #  sumar nada cuando falta ES lo neutro)
    if fcfy is not None:
        health += _lin(fcfy, 0, 8, -2, 3)
    else:
        health += 0.5      # media del tramo -2..+3
    health = _clamp(health, 4, 25)

    # Igual que en Futuro: un pilar sin ningún insumo se excluye del total en
    # vez de entrar con un valor inventado. Con los cuatro conocidos —el caso
    # normal— el resultado es exactamente la suma de siempre.
    # NO se excluyen pilares enteros del total, a propósito. Se probó y se midió
    # sobre 13 acciones × 9 huecos posibles: excluir un pilar y reescalar el
    # resto DISPARA el movimiento (peor caso 20,8 puntos, frente a 10,1 con solo
    # _pond y 9,0 antes), porque un pilar que se va arrastra toda su diferencia
    # con la media. La exclusión dentro del pilar (_pond) sí gana: baja el
    # movimiento MEDIO por hueco de 3,58 a 3,18 y elimina el sesgo sistemático.
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
            "debt_equity": _num(de, 2),
            # El TRAILING real (no `pe`, que cae a forward si falta): el tile
            # "P/E Trailing" usa este valor de respaldo y con el mixto pintaba
            # trailing = forward (HMC). Sin trailing → "N/A" → el tile pone "—".
            "pe_ratio": _num(info.get("pe_ratio"), 1),
            "forward_pe": _num(info.get("forward_pe"), 1),
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

    # ── Métricas que NO APLICAN a este tipo de negocio ───────────────────
    # MISMO blindaje que score_fundamentals, que aquí faltaba: un banco no
    # reporta coste de ventas, así que su margen bruto llega como 0.0 y
    # `_lin(0, 25, 75, 2, 13)` lo hundía al MÍNIMO (2 de 13) en lugar de usar
    # el neutro (6). Medido: costaba 4 puntos de Futuro a JPM, UNTY y BSAC, y
    # 3 a SAN — castigo puro por un dato que esas empresas no publican.
    # Un valor REAL nunca se descarta: solo huecos y ceros exactos.
    try:
        from data.industry_labels import metricas_no_aplicables
        _na = metricas_no_aplicables(info.get("sector"), info.get("industry"))
    except Exception:
        _na = frozenset()

    def _na_neutro(nombre, v):
        return None if (nombre in _na and (v is None or v == 0)) else v

    gm = _na_neutro("gross_margin", gm)

    sector = info.get("sector", "Unknown") or "Unknown"
    industry = (info.get("industry") or "").lower()
    mktcap = info.get("market_cap", 0) or 0

    # Moat (0-25): márgenes altos + ROIC alto = ventaja competitiva (proxy).
    # Con _pond, si falta uno el otro reparte su peso en vez de meter un fijo:
    # a MSFT, perder el ROIC le costaba 6,8 puntos de este pilar.
    moat = _pond([
        (gm,   25, 75, 2, 13),
        (roic,  5, 28, 0, 12),
    ], 3, 25)

    # Growth runway (0-25): crecimiento + sector con viento de cola.
    # default 8 → 10: 10 es el PUNTO MEDIO real del tramo (2-18). Con 8, no
    # tener dato de crecimiento restaba 2 puntos gratis. Aquí no hay nada que
    # redistribuir (es un solo componente), así que lo neutro es el medio.
    runway = _lin(rg if rg is not None else cagr, -5, 30, 2, 18, default=10)
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
    fcfy = _na_neutro("fcf_yield", ratios.get("fcf_yield"))
    # default 11 → 13, el punto medio real del tramo (6-20). Con 11, a una
    # empresa sin ROIC se le restaban 2 puntos por no tener el dato.
    mgmt = _lin(roic, 5, 25, 6, 20, default=13)
    if fcfy is not None and fcfy > 3:
        mgmt += 2
    mgmt = _clamp(mgmt, 4, 25)

    # Igual que en Fundamentales: nada de excluir pilares enteros (medido: eso
    # dispara el movimiento por hueco en vez de amortiguarlo). El blindaje vive
    # dentro de cada pilar, con _pond y con defectos centrados en el medio.
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
    # get_company_info guarda el nombre en "name"; usamos ese (antes buscaba
    # "shortName"/"symbol", que no existen, y siempre salía "La empresa").
    name_full = (info.get("name") or info.get("shortName") or info.get("symbol") or "La empresa")

    def _fmt_cap(v):
        """Formatea market cap: billones (T) o miles de millones (B)."""
        v = v or 0
        return f"${v/1e12:.1f} billones" if v >= 1e12 else f"${v/1e9:.0f}B"

    pros, cons = [], []

    # PROS — siempre con números e interpretación
    if moat_strength == "amplio":
        pros.append(
            f"Margen bruto {_pct(gm)} + ROIC {_pct(roic)} confirman ventaja competitiva real — "
            f"el negocio genera retornos claramente por encima del costo del capital"
        )
    elif moat_strength == "estrecho" and gm is not None and gm >= 35:
        pros.append(
            f"Margen bruto de {_pct(gm)} sugiere un poder de fijar precios razonable — "
            f"aunque el ROIC de {_pct(roic)} todavía no confirma una ventaja competitiva (moat) amplia"
        )

    if sector in _GROWTH_SECTORS:
        if (rg or 0) >= 15:
            pros.append(
                f"Sector {sector} con viento de cola estructural y la empresa lo está capturando: "
                f"ingresos +{_pct(rg)} interanual — crecimiento secular, no dependiente del ciclo"
            )
        else:
            pros.append(
                f"Sector {sector} con viento de cola de largo plazo — "
                f"potencial de aceleración si la empresa mejora su ejecución comercial"
            )

    if (rg or 0) >= 25:
        pros.append(
            f"Ingresos creciendo {_pct(rg)} — expansión de demanda muy fuerte que confirma un mercado direccionable (TAM) amplio y tracción del producto"
        )
    elif (rg or 0) >= 10:
        pros.append(
            f"Crecimiento de ingresos de {_pct(rg)} interanual — sólido y por encima del GDP; mercado en expansión"
        )

    if mktcap >= 5e11:
        pros.append(
            f"Escala de {_fmt_cap(mktcap)} — acceso a capital barato, poder de negociación con proveedores y resiliencia ante disrupciones de nicho"
        )
    elif mktcap >= 1e11:
        pros.append(
            f"Capitalización de {_fmt_cap(mktcap)}: suficiente escala para defensas competitivas sin ser demasiado grande para crecer"
        )

    if fcfy is not None and fcfy >= 4:
        pros.append(f"Flujo de caja libre (FCF) del {_pct(fcfy)} — genera caja real para reinvertir en crecimiento o devolver al accionista")

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
            f"Ventaja competitiva (moat) estrecha —ROIC {_pct(roic)}— en un sector {sector} sin viento de cola estructural — "
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
    if score >= 75 and moat_strength == "amplio":
        if sector in _GROWTH_SECTORS and (rg or 0) >= 15:
            future_thesis = (
                f"{name_full} combina una ventaja competitiva (moat) amplia —margen bruto {_pct(gm)} y "
                f"ROIC {_pct(roic)}— con un mercado en fuerte expansión ({_pct(rg)} interanual en {sector}). "
                f"Es un candidato serio a máquina de capitalización (compounder): reinvierte su caja a altas "
                f"tasas de retorno en un sector con viento de cola estructural, y ese efecto se agranda año "
                f"tras año. "
                f"{'La valoración de ' + str(int(pe_val)) + 'x beneficios ya descuenta parte de ese futuro, así que la disciplina en el precio de entrada sigue siendo clave.' if pe_val and pe_val > 30 else 'La valoración actual no parece un obstáculo material para el retorno a largo plazo.'}"
            )
        else:
            future_thesis = (
                f"{name_full} tiene una ventaja competitiva (moat) demostrada —ROIC {_pct(roic)} y margen "
                f"bruto {_pct(gm)}— con un crecimiento de {_pct(rg)} en {sector}. "
                f"El negocio genera retornos sobre el capital por encima de su promedio histórico, de modo "
                f"que tiene alta probabilidad de valer más dentro de 5 años, siempre que sostenga sus "
                f"márgenes y el sector no sufra una disrupción estructural."
            )
    elif score >= 60:
        if (rg or 0) >= 15 and moat_strength != "amplio":
            future_thesis = (
                f"{name_full} crece {_pct(rg)} interanual pero con una ventaja competitiva (moat) "
                f"'{moat_strength}' —ROIC {_pct(roic)}, margen operativo {_pct(om)}—. "
                f"El crecimiento es real, pero la rentabilidad sobre el capital todavía no está a la altura "
                f"de las mejores máquinas de capitalización (compounders). "
                f"{'Con ' + str(int(pe_val)) + 'x beneficios, el mercado ya paga por ese crecimiento, así que el potencial alcista real depende de que los márgenes se expandan.' if pe_val and pe_val > 25 else 'A la valoración actual, el perfil riesgo/retorno es razonable si el crecimiento se sostiene los próximos 3-4 años.'}"
            )
        else:
            future_thesis = (
                f"{name_full} muestra un perfil moderado: crecimiento de {_pct(rg)}, margen bruto {_pct(gm)} "
                f"y ROIC {_pct(roic)}. "
                f"No es un negocio de capitalización compuesta de alto octanaje, pero tampoco uno en "
                f"deterioro. El valor dentro de 5 años depende de que la dirección asigne el capital con "
                f"eficiencia "
                f"{'y de que el sector ' + sector + ' mantenga su viento de cola.' if sector in _GROWTH_SECTORS else 'y de que logre expandir sus márgenes operativos desde el ' + _pct(om) + ' actual.'}"
            )
    else:
        future_thesis = (
            f"{name_full} enfrenta dudas estructurales: un ROIC de {_pct(roic)} y un margen operativo de {_pct(om)} "
            f"{'limitan su capacidad de capitalización compuesta pese al crecimiento de ' + _pct(rg) + '.' if (rg or 0) >= 10 else 'combinados con un crecimiento de ' + _pct(rg) + ' no justifican optimismo estructural.'} "
            f"Sin una ventaja competitiva clara "
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
                        f"gana cuota de mercado sobre {names}"
                    )
                elif rg < avg_peer_rg - 10:
                    names = ", ".join(peer_metrics[t].get("name", t) for t in list(peer_rgs.keys())[:2])
                    cons.append(
                        f"Crecimiento {_pct(rg)} por debajo del promedio competitivo ({_pct(avg_peer_rg)}) — "
                        f"posible pérdida de cuota de mercado frente a {names}"
                    )

        # Enriquecer tesis con posición de mercado
        if market_pos == "leader" and moat_strength in ("amplio", "estrecho"):
            future_thesis = future_thesis + (
                f" Como líder del sector, tiene el mayor poder para fijar precios y retener talento — "
                f"una ventaja que se agranda con el tiempo mientras la dirección no la dilapide."
            )
        elif market_pos == "challenger":
            future_thesis = future_thesis + (
                f" Como retador, el potencial alcista es asimétrico si gana cuota de mercado, "
                f"pero requiere ejecución consistente sin el margen de error de los líderes establecidos."
            )

    # ── Análisis de viabilidad futura: prosa estructurada por escenarios ─────
    partes = []
    # 1) Marco: qué se está evaluando y dónde está la empresa.
    viab = ("sólida" if score >= 70 else "razonable pero con dudas" if score >= 55 else
            "en entredicho" if score >= 45 else "cuestionada")
    partes.append(
        f"La viabilidad futura mide si {name_full} podrá defender su rentabilidad y seguir creciendo "
        f"durante los próximos 5-7 años. Hoy su posición se ve {viab} (score {score:.0f}/100): su ventaja "
        f"competitiva (moat) es '{moat_strength}', sostenida por un margen bruto de {_pct(gm)} y un ROIC de "
        f"{_pct(roic)}, dentro de un mercado direccionable (TAM) '{tam_growth}' en el sector {sector}.")
    # 2) Datos específicos hilados con interpretación.
    datos = []
    if om is not None and gm is not None:
        datos.append(f"convierte ese {_pct(gm)} de margen bruto en {_pct(om)} operativo —la brecha revela "
                     f"cuánta presión de costos y competencia absorbe—")
    if fcfy is not None:
        datos.append(f"genera un flujo de caja libre (FCF) del {_pct(fcfy)} sobre su valor de mercado")
    if datos:
        escala = (f" con una escala de {_fmt_cap(mktcap)} que "
                  f"{'la blinda frente a disrupciones de nicho' if mktcap >= 1e11 else 'la deja más expuesta a rivales de mayor tamaño'}"
                  if mktcap else "")
        partes.append("En números, " + " y ".join(datos) + escala + ".")
    # 3) Tres escenarios a 5 años con rangos de score (el actual es el base).
    bull = min(int(score) + 16, 92)
    bear = max(int(score) - 16, 20)
    partes.append(
        f"Vemos tres caminos a 5 años: (1) ejecución exitosa —sostiene márgenes, defiende su ventaja y el "
        f"sector acompaña—, que empujaría el score a la zona {bull-4}-{bull}; (2) deterioro gradual "
        f"—erosión de márgenes o pérdida de cuota de mercado frente a la competencia—, con el score cayendo "
        f"a {bear}-{bear+4}; y (3) estabilización mediocre —compite pero sin ampliar su ventaja—, que es "
        f"donde se ubica hoy ({score:.0f}).")
    # 4) Cierre: caja/gestión frente al riesgo de ejecución.
    if fcfy is not None and fcfy >= 4:
        partes.append(f"El flujo de caja libre del {_pct(fcfy)} da un colchón real, pero no elimina el "
                      f"riesgo de ejecución: la clave será si la dirección reinvierte ese capital a buenas "
                      f"tasas de retorno en vez de dilapidarlo.")
    elif disruption == "alto":
        partes.append("El mayor riesgo es la disrupción del sector: sin una ventaja defensiva clara, el "
                      "caso a largo plazo depende de que la dirección reinvente el negocio a tiempo.")
    else:
        partes.append(f"La gestión del capital luce '{mgmt_q}'; a largo plazo, el caso dependerá sobre todo "
                      f"de que sostenga sus retornos sobre el capital y no diluya su ventaja competitiva.")
    analysis = " ".join(partes)

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
def _key_insight_institucional(conocido, exento, buys, sells, inst_pct,
                               short_known, short_pct):
    """Construye el "lo más relevante" COMPONIENDO solo lo que sabemos.

    La versión anterior afirmaba literalmente "sin compras de directivos que
    confirmen convicción interna" incluso cuando el dato NO EXISTÍA (ADRs de
    emisores extranjeros): presentaba una laguna como un hecho negativo."""
    piezas = []
    if conocido and buys >= 2:
        piezas.append(f"los directivos están comprando ({buys} compras recientes), "
                      f"la señal interna más valiosa")
    if inst_pct is not None:
        piezas.append(f"respaldo institucional del {_pct(inst_pct, 0)}")
    if not conocido:
        if exento:
            piezas.append("las operaciones de directivos no son públicas en este valor "
                          "(emisor extranjero exento ante la SEC), así que la lectura se "
                          "apoya en los fondos y en las posiciones en corto")
        else:
            piezas.append("el registro de operaciones de directivos no está disponible "
                          "ahora mismo")
    elif buys == 0 and sells >= 1:
        # Solo se puede afirmar cuando el dato EXISTE.
        piezas.append("sin compras de directivos que confirmen convicción interna")
    if short_known:
        piezas.append(f"pocos apuestan en contra (short interest {_pct(short_pct, 0)})")
    if not piezas:
        return "Lo más relevante: no hay datos suficientes de dinero institucional para este valor."
    return "Lo más relevante: " + "; ".join(piezas) + "."


def score_institutional(holders, info):
    inst_pct = holders.get("institutional_ownership_pct")
    if isinstance(inst_pct, (int, float)) and inst_pct <= 1.5:
        inst_pct = inst_pct * 100  # viene como fracción
    # ── ¿SABEMOS algo de los insiders? ───────────────────────────────────
    # Cuando el registro del regulador no devuelve ninguna operación para el
    # emisor, el dato NO existe. Antes se colapsaba a 0, que es una AFIRMACIÓN
    # ("no compraron") y penalizaba igual que unas ventas reales.
    _ins_disp = holders.get("insiders_disponibles")
    # Compatibilidad con holders guardados por versiones anteriores (sin la marca):
    insider_conocido = (_ins_disp is True) or (
        _ins_disp is None and bool(holders.get("insider_transactions")))
    # "emisor_extranjero" es el motivo que emitían las versiones anteriores; se
    # sigue aceptando para no romper los análisis ya guardados.
    insider_exento = (_ins_disp is False) or (
        holders.get("insiders_motivo") in ("sin_registro_sec", "emisor_extranjero"))
    insider_buys = holders.get("recent_insider_buys") or 0
    insider_sells = holders.get("recent_insider_sells") or 0
    insiders_held = holders.get("insiders_percent_held")
    inst_count = holders.get("institutions_count")
    # short_percent puede ser None (sin dato, típico en cloud) → lo tratamos
    # como desconocido: score neutral y se muestra "N/D" en vez de un 0% falso.
    short_known = info.get("short_percent") is not None
    short_pct = (info.get("short_percent") or 0) * 100

    # Insider signal (0-33)
    if not insider_conocido:
        # Neutro HONESTO al 65% del tramo, coherente con los otros dos neutros
        # de este bloque (institucional 19/33 = 58%, short 22/34 = 65%). Antes
        # la ausencia daba 18/33 fijo: lo mismo que unos directivos que solo
        # venden. Un ADR no podía alcanzar convicción ALTA por algo que la ley
        # le impide publicar.
        insider = 21.5
    else:
        insider = _clamp(18.0 + _lin(insider_buys, 0, 5, 0, 14), 10, 33)

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

    # Se probó a excluir del total el pilar cuyo dato no se conoce (reescalando
    # los otros dos) y se DESCARTÓ tras medirlo: mejoraba los casos típicos pero
    # disparaba el peor de 6,6 a 19,7 puntos, porque el pilar que se va arrastra
    # toda su diferencia con el resto. Aquí cada pilar es una métrica única con
    # su neutro ya elegido a conciencia (21,5 / 19 / 22), y eso amortigua mejor.
    score = round(insider + instq + shortd, 1)
    # "N/D" (no "neutral") cuando el dato no existe: la UI debe poder
    # distinguir "los directivos están equilibrados" de "no lo sabemos".
    insider_sig = ("N/D" if not insider_conocido
                   else "alcista" if insider_buys >= 2 else "neutral")
    if not short_known:
        squeeze = "N/D"
    else:
        squeeze = "alto" if short_pct >= 15 else "medio" if short_pct >= 8 else "bajo"
    smart = ("neutral" if not insider_conocido
             else "acumulando" if insider_buys >= 2 else "neutral")

    pros, cons = [], []
    if insider_buys >= 2:
        pros.append(f"Los directivos compraron acciones {insider_buys} veces hace poco — apuestan con su propio dinero")
    if inst_pct is not None and 40 <= inst_pct <= 85:
        pros.append(f"Respaldo sólido de grandes fondos ({_pct(inst_pct,0)} de la empresa en sus manos)")
    if short_known and 0 < short_pct < 5:
        pros.append("Casi nadie apuesta a que la acción baje (muy pocas ventas en corto)")
    if short_known and short_pct >= 15:
        cons.append(f"Bastantes inversores apuestan a que baje ({_pct(short_pct,0)} de las acciones disponibles vendidas en corto)")
    if insider_conocido and insider_sells >= 3 and insider_buys == 0:
        cons.append(f"Los directivos solo vendieron ({insider_sells} ventas, 0 compras) — sin señal de convicción compradora")
    if inst_pct is not None and inst_pct > 90:
        cons.append("Demasiada concentración de fondos (poco margen para que entren más compradores grandes)")
    if not pros:
        pros.append("Sin señales claramente negativas en el flujo de dinero grande")
    if not cons:
        cons.append("Sin señales de alerta en el posicionamiento institucional")

    # ── Análisis en lenguaje natural, explicando cada señal (patrón partes) ──
    partes = []
    # 1) Marco: qué mira este análisis.
    partes.append(
        "El flujo institucional sigue el rastro del 'dinero inteligente' —los grandes fondos, los propios "
        "directivos de la empresa y quienes apuestan a la baja— para ver hacia qué lado se está "
        "posicionando el capital profesional.")
    # 2) Propiedad institucional, con interpretación.
    if inst_pct is not None:
        cuenta = f", repartido entre unos {inst_count:,} fondos" if inst_count else ""
        if 40 <= inst_pct <= 85:
            cal = ("un respaldo sólido y sano: el dinero profesional confía en el negocio y todavía queda "
                   "margen para que entren más compradores grandes")
        elif inst_pct > 85:
            cal = ("un respaldo altísimo, casi saturado: es una gran validación, pero deja poco margen "
                   "para que nuevos fondos empujen el precio hacia arriba")
        else:
            cal = "un respaldo aún bajo: los grandes fondos todavía no se han volcado con la acción"
        partes.append(
            f"Los grandes fondos de inversión (lo que se llama 'propiedad institucional') poseen alrededor "
            f"del {_pct(inst_pct,0)} de la empresa{cuenta} — {cal}.")
    # 3) Actividad de directivos (insiders), con interpretación.
    if insider_buys >= 2 and insider_buys >= insider_sells:
        partes.append(
            f"Y hay una señal llamativa: los propios directivos (los 'insiders') compraron acciones "
            f"{insider_buys} veces en las operaciones recientes. Cuando quienes mejor conocen la empresa "
            f"ponen su propio dinero, suele ser la señal interna más valiosa que existe.")
    elif insider_buys == 0 and insider_sells >= 1:
        partes.append(
            f"En cuanto a los directivos (los 'insiders'), en las operaciones recientes solo hubo ventas "
            f"({insider_sells}). Vender es habitual —por liquidez o impuestos— y no es necesariamente mala "
            f"señal, pero no hay compras que confirmen una convicción interna fuerte.")
    elif insider_buys or insider_sells:
        partes.append(
            f"Los directivos (los 'insiders') registraron {insider_buys} compras y {insider_sells} ventas "
            f"recientes: una actividad mixta que no inclina la balanza en ninguna dirección.")
    elif not insider_conocido:
        # Antes, con 0 compras y 0 ventas NO se emitía ninguna rama: la parte
        # de directivos desaparecía del análisis sin decir por qué.
        if insider_exento:
            partes.append(
                "Sobre los directivos (los 'insiders'): se consultó el registro del regulador "
                "estadounidense, que es el origen de este dato, y no consta ninguna operación "
                "declarada por los directivos de esta empresa. No es que no hayan comprado: "
                "puede que este emisor no esté obligado a declararlo. Por eso esta pieza queda "
                "fuera de la lectura y NO penaliza la calificación.")
        else:
            partes.append(
                "El registro de operaciones de los directivos no se ha podido recuperar en este momento. "
                "Se reintentará automáticamente; mientras tanto esta pieza queda fuera de la lectura y "
                "no penaliza la calificación.")
    # 4) Apuestas a la baja (short interest), explicado.
    if short_known and short_pct > 0:
        nivel = ("muy bajo" if short_pct < 3 else "moderado" if short_pct < 10 else "alto")
        impl = ("casi nadie apuesta a que caiga, señal de que el mercado no ve un riesgo inminente"
                if short_pct < 3 else
                "un nivel de apuestas bajistas dentro de lo normal" if short_pct < 10 else
                "un nivel elevado: bastantes inversores apuestan a que baje, lo que añade riesgo pero "
                "también abre la puerta a un rebote brusco (un 'short squeeze') si la tesis mejora")
        partes.append(
            f"Sobre las apuestas a la baja: el 'short interest' —el porcentaje de las acciones disponibles "
            f"que alguien ha pedido prestadas para apostar a que el precio caiga— es del {_pct(short_pct,1)}, "
            f"un nivel {nivel}; {impl}.")
    elif not short_known:
        partes.append(
            "El dato de apuestas a la baja (short interest) no está disponible ahora mismo para esta acción, "
            "así que esa pieza queda fuera de la lectura.")
    # 5) Cierre: la señal agregada del dinero inteligente.
    if insider_buys >= 2:
        partes.append(
            "En conjunto, el dinero inteligente acompaña: hay respaldo de fondos y, sobre todo, directivos "
            "comprando con convicción, la combinación más alcista de esta sección.")
    elif inst_pct is not None and 40 <= inst_pct <= 85:
        partes.append(
            "En conjunto, el posicionamiento es saludable: hay un respaldo institucional sólido, aunque sin "
            "una señal de compra interna que lo refuerce.")
    else:
        partes.append(
            "En conjunto, el flujo institucional no manda una señal fuerte en ninguna dirección, así que "
            "conviene apoyar la decisión más en los fundamentales y el técnico.")
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
        "key_insight": _key_insight_institucional(
            insider_conocido, insider_exento, insider_buys, insider_sells,
            inst_pct, short_known, short_pct),
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
    beta = risk_metrics.get("beta", info.get("beta", 1.0)) or 1.0

    # Blindaje NaN: un dato corrupto (NaN) se trata como AUSENTE para que las
    # ramas de la prosa se elijan por el valor real y nunca impriman "N/A" a
    # mitad de frase ni disparen la rama equivocada (p.ej. 'muy volátil' con un
    # ATR NaN). El scoring ya tolera None (usa _lin con default).
    if _bad(rr): rr = 0
    atr_pct    = None if _bad(atr_pct)    else atr_pct
    risk_pct   = None if _bad(risk_pct)   else risk_pct
    reward_pct = None if _bad(reward_pct) else reward_pct
    price      = None if _bad(price)      else price
    stop       = None if _bad(stop)       else stop
    target     = None if _bad(target)     else target
    if _bad(beta): beta = 1.0

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

    # ── Análisis en lenguaje natural: explica qué significa cada cosa para
    #    el inversor, sin amontonar tecnicismos ni números sin interpretar. ────
    partes = []
    # 1) El plan, en palabras claras (entrada, protección DEBAJO, objetivo ARRIBA).
    if rr and price and stop and target:
        rr_lectura = (
            "muy atractiva: el objetivo ofrece bastante más de lo que se pone en juego" if rr >= 2.5 else
            "favorable: se puede ganar más de lo que se arriesga" if rr >= 1.8 else
            "equilibrada: lo que se puede ganar y lo que se arriesga son parecidos" if rr >= 1.2 else
            "poco atractiva: hoy se arriesga más de lo que se apunta a ganar")
        partes.append(
            f"La idea sería comprar alrededor de {_money(price)} y colocar una protección (un 'stop', el "
            f"precio al que uno acepta que se equivocó y vende para cortar la pérdida) en {_money(stop)}, "
            f"un {_pct(risk_pct)} por debajo; el objetivo de toma de ganancias estaría en {_money(target)}, "
            f"un {_pct(reward_pct)} por encima. Puesto en una sola cifra, la relación entre lo que se "
            f"arriesga y lo que se busca ganar es de {rr:.1f} a 1, {rr_lectura}.")
    # 2) Volatilidad, explicada (qué tan movida es y qué implica).
    if atr_pct is not None:
        if atr_pct <= 2:
            partes.append(
                f"Es una acción relativamente tranquila: en un día normal se mueve alrededor de un "
                f"{_pct(atr_pct)}, así que no suele dar grandes sustos y se puede manejar con un tamaño "
                f"de posición habitual.")
        elif atr_pct <= 4:
            partes.append(
                f"Su volatilidad es moderada: oscila cerca de un {_pct(atr_pct)} al día. Es un vaivén "
                f"llevadero siempre que no se sobredimensione la posición.")
        else:
            partes.append(
                f"Cuidado con lo movida que es: cambia de precio con fuerza, alrededor de un {_pct(atr_pct)} "
                f"cada día. En la práctica esto obliga a comprar una cantidad más pequeña de lo habitual y a "
                f"dar algo más de aire a la protección, porque si se pone demasiado cerca, un simple "
                f"bandazo del día podría sacarte de la posición sin que la tesis haya fallado.")
    # 3) Beta, en 'veces' respecto al mercado.
    if beta:
        if beta < 0:
            partes.append(
                f"Curiosamente, frente al mercado se mueve en sentido contrario (beta {_num(beta,1)}): "
                f"tiende a subir cuando el índice cae y viceversa, lo que puede darle un papel de cobertura "
                f"en la cartera.")
        elif beta <= 0.9:
            partes.append(
                f"Frente al mercado se comporta de forma defensiva (beta {_num(beta,1)}): tiende a moverse "
                f"menos que el índice, tanto cuando sube como cuando cae.")
        elif beta <= 1.3:
            partes.append(
                f"Se mueve a un ritmo parecido al del mercado (beta {_num(beta,1)}): ni amplifica ni "
                f"amortigua demasiado sus subidas y bajadas.")
        else:
            partes.append(
                f"Es bastante sensible al mercado (beta {_num(beta,1)}): cuando el índice general se mueve, "
                f"ella tiende a hacerlo con más fuerza, así que en un entorno turbulento puede sufrir (o "
                f"rebotar) más que la media.")
    # 4) Conclusión en una frase, natural.
    if rr:
        if rr >= 1.8:
            partes.append(
                "En pocas palabras, las cuentas juegan a favor: hay bastante más para ganar que para "
                "perder, y la clave será tener la disciplina de respetar la protección pase lo que pase.")
        elif rr < 1.2:
            partes.append(
                "En pocas palabras, hoy las cuentas no acompañan: se arriesga casi tanto (o más) de lo que "
                "se apunta a ganar, así que solo tendría sentido con una convicción muy alta y una "
                "disciplina total con la protección; de lo contrario, conviene esperar un mejor punto de "
                "entrada.")
        else:
            partes.append(
                "En pocas palabras, es un perfil equilibrado: ni una ganga ni una trampa. El resultado "
                "dependerá sobre todo de la disciplina para respetar la entrada, la protección y el "
                "objetivo marcados.")
    analysis = " ".join(partes) if partes else "Aún no hay datos suficientes para definir los niveles de riesgo."

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

    # ── Análisis macro en lenguaje natural, explicando cada indicador ────────
    partes = []
    clima = ("favorable, con los inversores dispuestos a asumir riesgo" if market_env == "apetito por riesgo" else
             "de cautela, con predominio de la aversión al riesgo" if market_env == "aversión al riesgo" else
             "neutro" if market_env == "neutral" else "difícil de leer ahora mismo por falta de datos")
    partes.append(
        f"El análisis macro no mira a la empresa en sí, sino el clima general del mercado donde cotiza: "
        f"el nivel de miedo, los tipos de interés y cómo se está comportando su sector. Hoy ese clima "
        f"luce {clima}.")
    if vix is not None:
        vix_estado = ("está tranquilo" if vix < 20 else "está algo nervioso" if vix <= 30 else "está muy tensionado")
        vix_impl = ("un entorno cómodo para que las bolsas avancen" if vix < 20 else
                    "una señal de prudencia, porque el mercado descuenta más sobresaltos" if vix <= 30 else
                    "un ambiente de miedo elevado donde los movimientos bruscos son la norma")
        partes.append(
            f"El llamado 'índice del miedo' (VIX), que mide cuánta volatilidad esperan los inversores para "
            f"las próximas semanas, está en {_num(vix,1)}: el mercado {vix_estado}, lo que suele "
            f"traducirse en {vix_impl}.")
    if yc is not None:
        if yc < 0:
            partes.append(
                f"La curva de tipos está invertida —los bonos a corto plazo rinden más que los de largo, "
                f"una diferencia de {_num(yc,2)} puntos—, una señal clásica que en el pasado ha precedido "
                f"desaceleraciones económicas; conviene tomarla como motivo de cautela.")
        elif yc < 0.3:
            partes.append(
                f"La curva de tipos está casi plana (apenas {_num(yc,2)} puntos entre el bono a 10 y a 2 "
                f"años), lo que refleja una economía en transición sobre la que el mercado todavía tiene "
                f"dudas.")
        else:
            partes.append(
                f"La curva de tipos es normal ({_num(yc,2)} puntos entre el bono a 10 y a 2 años), lo que "
                f"apunta a condiciones de crédito y liquidez sanas: un buen telón de fondo para las bolsas.")
    if sec_ret is not None:
        if sec_ret > 8:
            partes.append(
                f"A favor juega algo concreto: el sector de la empresa viene fuerte, con un "
                f"{_pct(sec_ret, signed=True)} en el último año, señal de que el dinero está rotando hacia "
                f"ese lado del mercado.")
        elif sec_ret < 0:
            partes.append(
                f"En contra pesa su sector, rezagado con un {_pct(sec_ret, signed=True)} en el último año: "
                f"el flujo de capital no lo está favoreciendo por ahora.")
        else:
            partes.append(
                f"Su sector se mueve de forma discreta ({_pct(sec_ret, signed=True)} en el último año), sin "
                f"ser un viento a favor ni en contra claro.")
    partes.append(
        "En conjunto, el macro juega a favor: es un viento de cola para la acción." if score >= 65 else
        "En conjunto, el macro es un viento en contra que conviene tener presente." if score < 50 else
        "En conjunto, el macro es neutro: ni ayuda ni estorba de forma decisiva, así que el caso "
        "dependerá sobre todo de la propia empresa.")
    macro_analysis = " ".join(partes)

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": macro_analysis,
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
    # ── SOLO las noticias que hablan de ESTA acción o de su sector ───────────
    # El feed por ticker cuela artículos de terceros: medido, el 44 % de los
    # titulares no menciona a la empresa. Puntuaban igual, así que el sentimiento
    # de TSLA lo movían titulares de SpaceX y el de CIB, resultados de 3M.
    # La marca la pone data/market_data.py al traer las noticias.
    # RETROCOMPATIBLE: si ningún ítem la trae (noticias cacheadas por la versión
    # anterior), se usa la lista entera y el resultado es idéntico al de antes.
    _rel = [it for it in news if it.get("relevancia") in ("empresa", "sector")]
    if not any("relevancia" in (it or {}) for it in news):
        _rel = news
    news = _rel

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

    # narrative_momentum (0-33): cobertura reciente.
    # El tope baja de 8 a 7 artículos porque ahora solo cuentan los relevantes.
    # Sin recalibrar, esta pieza restaba puntos a TODAS las acciones por un
    # cambio que solo pretendía quitar ruido. El 7 se eligió midiendo: sobre un
    # control de 12 tickers deja la MEDIANA del score exactamente donde estaba
    # (Δ 0.0); con 5 se iba a +3.2 y con 8, a -1.0.
    narr = _clamp(13.5 + _lin(fresh, 0, 7, 0, 14), 8, 33)

    # contrarian_value (0-33): neutral salvo extremos
    contr = 17.0
    # El umbral se queda en 3: medido, bajarlo a 2 no mejora nada la calibración
    # (misma mediana) y en cambio disparaba la señal contraria de BRK-B.
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

    # ── Prosa natural, explícita y con interpretación (patrón partes) ────────
    partes = []
    if n == 0:
        partes.append(
            "El análisis de sentimiento lee el tono de las noticias recientes para captar el estado de "
            "ánimo del mercado hacia la acción. En este caso apenas hay cobertura reciente, así que no hay "
            "una narrativa clara que interpretar: conviene apoyar la decisión más en los fundamentales y "
            "el técnico que en el ruido mediático.")
    else:
        tono_txt = ("mayormente positivo" if pos > neg else
                    "mayormente negativo" if neg > pos else "equilibrado, sin un sesgo claro")
        tono_impl = ("lo que suele acompañar (o incluso alimentar) las subidas de precio" if pos > neg else
                     "algo que puede pesar sobre el precio a corto plazo si se mantiene" if neg > pos else
                     "de modo que el sentimiento no está empujando el precio en ninguna dirección")
        partes.append(
            f"El análisis de sentimiento resume el estado de ánimo del mercado a partir del tono de las "
            f"noticias. De {n} titulares recientes ({fresh} solo de esta última semana), el tono es "
            f"{tono_txt} —{pos} señales positivas frente a {neg} negativas—, {tono_impl}.")
        if narrative_theme not in ("general", "sin cobertura"):
            partes.append(
                f"La conversación gira sobre todo en torno a {narrative_theme}, que es el eje por el que "
                f"hoy los medios están valorando —o castigando— a la acción.")
        if mom == "mejorando":
            partes.append(
                "Además, la tendencia es de mejora: las noticias más frescas suenan mejor que las de días "
                "atrás, señal de que el ánimo del mercado está girando a favor.")
        elif mom == "deteriorándose":
            partes.append(
                "Ojo con la tendencia: el tono se está enfriando —los titulares más recientes son peores "
                "que los previos—, algo que a veces anticipa presión sobre el precio.")
        else:
            partes.append(
                "El tono se mantiene bastante estable frente a semanas anteriores, sin un cambio de humor "
                "brusco en ninguna dirección.")
        if reputational_risk == "alto":
            partes.append(
                "Hay una señal de alerta relevante: aparecen varios titulares de tipo legal o regulatorio "
                "(demandas, investigaciones, multas), un riesgo para la reputación que puede pesar más "
                "allá de los propios números.")
        elif reputational_risk == "medio":
            partes.append(
                "Conviene vigilar algún titular de corte legal o regulatorio en la cobertura: un riesgo "
                "reputacional moderado, todavía manejable pero a seguir de cerca.")
        if contra == "comprar el miedo":
            partes.append(
                "Y un matiz contrario interesante: pese a las malas noticias, el precio aguanta, lo que "
                "sugiere que el mercado ya está mirando más allá del ruido —a veces el mejor momento para "
                "el inversor paciente es justo cuando el ánimo está por los suelos—.")
        elif contra == "vender la euforia":
            partes.append(
                "Y un matiz de cautela: el entusiasmo mediático es alto y la valoración exigente; cuando "
                "'todo el mundo' está optimista, suele ser tarde para subirse solo por el entusiasmo del "
                "momento.")
        cierre = ("hoy juega a favor y puede dar algo de impulso al precio."
                  if (pos > neg and mom != "deteriorándose") else
                  "hoy resta, así que conviene no comprar contra la corriente sin una razón de peso."
                  if (neg > pos or mom == "deteriorándose") else
                  "hoy es neutro, de modo que el peso de la decisión debería recaer en los fundamentales y "
                  "el técnico.")
        partes.append(
            "En conjunto, el sentimiento es una foto del corto plazo —útil para afinar el momento de "
            "entrada, no para decidir la tesis de fondo—: " + cierre)
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
        cons.append("Euforia mediática con valoración estirada: riesgo de comprar caro dejándose llevar")
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
            "Euforia mediática con valoración exigente: cautela al entrar solo por el entusiasmo del momento."
            if contra == "vender la euforia" else "No hay divergencia clara entre narrativa y precio."
        ),
    }


def _dias_txt(n) -> str:
    """'hoy' · 'mañana' · '1 día' · '28 días'. Evita el feo 'en 1 días'."""
    try:
        n = int(n)
    except Exception:
        return ""
    return "hoy" if n <= 0 else "mañana" if n == 1 else f"{n} días"


def _en_txt(n) -> str:
    """Como _dias_txt pero listo para meter en una frase: 'hoy', 'mañana',
    'en 28 días' — nunca el feo 'en mañana'."""
    t = _dias_txt(n)
    return t if t in ("", "hoy", "mañana") else f"en {t}"


def _ev_cuando(e) -> str:
    """Cómo se nombra el momento de un evento en prosa: 'el 10 sep 2026 (en 43
    días)', '~sep 2026 (en unos 43 días)' o 'según titulares recientes'."""
    try:
        dias = e.get("dias")
        if dias is None:
            return "según titulares recientes"
        fecha = e.get("fecha_txt") or ""
        cuanto = _en_txt(dias)
        if e.get("confirmado"):
            return f"el {fecha} ({cuanto})" if fecha else cuanto
        return f"hacia {fecha} ({cuanto})" if fecha else cuanto
    except Exception:
        return ""


_EV_ETIQUETA = {
    "producto":    "catalizador de producto",
    "negocio":     "catalizador de negocio",
    "dividendo":   "hito para el accionista",
    "corporativo": "evento corporativo",
    "resultados":  "reporte de resultados",
}


def _score_catalysts(earnings, info, events=None):
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

    # ── Agenda de OTROS catalizadores (keynotes, lanzamientos, contratos…) ──
    # Llega de data.corporate_events.get_upcoming_catalysts. Si no llega o viene
    # vacía, TODO lo que sigue es exactamente igual que antes (score incluido).
    eventos = []
    try:
        for e in (events or []):
            if not isinstance(e, dict):
                continue
            # 'dias' normalizado a int o None: un análisis guardado con el dato
            # corrupto no puede tumbar las comparaciones de más abajo.
            e = dict(e)
            try:
                d = e.get("dias")
                e["dias"] = None if d is None else int(d)
            except (TypeError, ValueError):
                e["dias"] = None
            eventos.append(e)
    except Exception:
        eventos = []
    otros_90 = [e for e in eventos
                if e.get("tipo") != "resultados"
                and e.get("dias") is not None and e["dias"] <= 90]
    otros_180 = [e for e in eventos
                 if e.get("tipo") != "resultados"
                 and e.get("dias") is not None and e["dias"] <= 180]
    senales = [e for e in eventos if e.get("dias") is None]
    prox_evento = next((e for e in eventos if e.get("dias") is not None), None)
    prox_otro = otros_180[0] if otros_180 else (senales[0] if senales else None)

    # catalyst_quality (0-33): proximidad del próximo earnings
    if days is None:
        cq = 17.0
    elif days <= 30:
        cq = _lin(days, 0, 30, 32, 24)
    elif days <= 90:
        cq = _lin(days, 30, 90, 24, 17)
    else:
        cq = _lin(days, 90, 200, 17, 11)
    # Bono por DENSIDAD de catalizadores: una acción con keynote, contrato o
    # evento corporativo próximo tiene más catalizadores que una que solo
    # espera a resultados. Con tope, para que no infle el score.
    cq += min(len(otros_90) * 2.0, 5.0) + (1.5 if senales else 0.0)
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
        pros.append(f"Reporte de resultados próximo ({_en_txt(days)}): catalizador cercano que puede mover el precio")
    # Otros eventos del calendario: se nombran uno a uno, con su fecha.
    for e in otros_90[:2]:
        pros.append(f"{e.get('titulo', 'Evento')} {_ev_cuando(e)}: "
                    f"{_EV_ETIQUETA.get(e.get('tipo'), 'catalizador')} adicional al reporte de resultados")
    for e in senales[:1]:
        pros.append(f"{e.get('etiqueta') or 'Movimiento'} en titulares recientes: «{str(e.get('titulo', ''))[:90]}»")
    if beat is not None and total and beat / total >= 0.6:
        pros.append(f"Buen historial superando el consenso de beneficios ({beat} de {total} trimestres)")
    if avg_surp is not None and avg_surp > 3:
        pros.append(f"Sorpresa promedio positiva sobre el consenso ({_pct(avg_surp, signed=True)})")
    if len(otros_90) + (1 if (days or 999) <= 90 else 0) >= 3:
        pros.append("Agenda cargada: varios catalizadores concentrados en los próximos 90 días")

    if days is not None and days > 120 and not otros_90:
        cons.append("El próximo reporte de resultados está lejano y no hay otros eventos cerca: "
                    "poco catalizador a corto plazo")
    elif days is not None and days > 120:
        cons.append("El reporte de resultados está lejano: hasta entonces la tesis depende de eventos menores")
    if days is not None and days <= 7:
        cons.append(f"El reporte de resultados es inminente ({_en_txt(days)}): el precio puede reaccionar "
                    f"con fuerza en cualquiera de las dos direcciones")
    if beat is not None and total and beat / total < 0.4:
        cons.append("Historial flojo en resultados: decepciona más veces de las que supera el consenso")
    if eventos and not otros_180 and not senales:
        cons.append("Fuera de los resultados no hay eventos señalados en los próximos 6 meses: "
                    "la acción se mueve sobre todo con el mercado")
    # Concentración: dos catalizadores casi el mismo día amplifican el movimiento.
    if days is not None and any(abs((e.get("dias") or 9999) - days) <= 7 for e in otros_90):
        cons.append("Dos catalizadores coinciden casi en la misma fecha: la reacción del precio puede amplificarse")
    if senales and any(e.get("tipo") == "negocio" for e in senales):
        cons.append("Hay noticias de operaciones o contratos sin confirmar en detalle: el impacto real aún no es medible")
    if not pros:
        pros.append("Calendario de catalizadores dentro de lo normal")
    if not cons:
        cons.append("Sin riesgos de eventos inminentes detectados")

    # ── Análisis de catalizadores: prosa estructurada en español ─────────────
    partes = []
    # Apertura: retrato del calendario completo (solo si hay agenda que contar).
    n_90 = len(otros_90) + (1 if (days is not None and days <= 90) else 0)
    if eventos:
        if n_90 >= 3:
            partes.append(
                f"El calendario está cargado: hay {n_90} eventos en los próximos 90 días. Cuando los "
                f"catalizadores se acumulan, el precio suele moverse más de lo habitual en poco tiempo, "
                f"tanto a favor como en contra.")
        elif n_90 == 2:
            partes.append(
                "Hay dos catalizadores en los próximos 90 días, así que la acción tiene citas concretas "
                "en el horizonte y no depende solo de la deriva general del mercado.")
        elif n_90 == 1:
            partes.append(
                "El calendario de los próximos 90 días tiene un único evento relevante: casi toda la "
                "atención se concentrará en él.")
        else:
            partes.append(
                "No hay eventos señalados en los próximos 90 días: en este tramo la acción tenderá a "
                "seguir al mercado y a su sector más que a una noticia propia.")

    if days is not None:
        prox = ("muy cercano" if days <= 14 else "cercano" if days <= 45 else
                "de medio plazo" if days <= 120 else "lejano")
        _cuando = _dias_txt(days)
        _cuando = _cuando if _cuando in ("hoy", "mañana") else f"en unos {_cuando}"
        partes.append(
            f"El catalizador principal a la vista es el próximo reporte de resultados, previsto "
            f"{_cuando} ({prox}). Los reportes trimestrales son los eventos que más mueven el precio a "
            f"corto plazo, porque confirman o desmienten la tesis con números reales.")
    else:
        partes.append(
            "Aún no hay fecha confirmada para el próximo reporte de resultados —el catalizador de corto "
            "plazo más relevante—; conviene revisarla cerca de la temporada de resultados.")

    # Otros eventos del calendario, nombrados uno a uno.
    if otros_180:
        listado = "; ".join(f"«{e.get('titulo', 'evento')}» {_ev_cuando(e)}" for e in otros_180[:3])
        partes.append(
            f"Pero los resultados no son lo único en la agenda: {listado}. Este tipo de citas —keynotes, "
            f"lanzamientos, contratos o hitos para el accionista— mueven el precio por una vía distinta a "
            f"la de los números trimestrales: cambian la expectativa sobre el negocio futuro, no sobre el "
            f"trimestre que acaba de cerrar.")
        if any(not e.get("confirmado") for e in otros_180[:3]):
            partes.append(
                "Las fechas de los eventos recurrentes son aproximadas —se repiten cada año en las mismas "
                "semanas—, así que conviene confirmarlas cuando la compañía las anuncie oficialmente.")

    # Señales vivas detectadas en titulares (cobertura universal).
    if senales:
        s = senales[0]
        resto = (f" Hay {len(senales) - 1} señal más en la misma línea." if len(senales) == 2 else
                 f" Hay {len(senales) - 1} señales más en la misma línea." if len(senales) > 2 else "")
        partes.append(
            f"Además, los titulares recientes apuntan a un catalizador ya en marcha "
            f"({str(s.get('etiqueta') or 'movimiento corporativo').lower()}): «{str(s.get('titulo', ''))[:120]}»."
            f"{resto} Son eventos que ya están afectando a la narrativa, aunque su impacto en las cuentas "
            f"tarde varios trimestres en verse.")

    if beat is not None and total:
        ratio = beat / total
        cal = ("un historial muy sólido" if ratio >= 0.75 else "un buen historial" if ratio >= 0.6 else
               "un historial irregular" if ratio >= 0.4 else "un historial flojo")
        cierre_hist = ("refuerza la credibilidad de la dirección cuando fija sus previsiones"
                       if ratio >= 0.6 else
                       "obliga a tomar con cautela las previsiones de la dirección")
        partes.append(
            f"En los últimos {total} trimestres superó el consenso de beneficios {beat} veces — {cal}, lo "
            f"que {cierre_hist}.")
    if avg_surp is not None:
        signo = "positiva" if avg_surp > 0 else "negativa"
        matiz = ("un patrón que suele preceder revisiones al alza de los analistas" if avg_surp > 2 else
                 "una señal a vigilar si la presión se mantiene" if avg_surp < -2 else
                 "sin un sesgo marcado por ahora")
        partes.append(
            f"La sorpresa promedio frente a lo esperado es {signo} ({_pct(avg_surp, signed=True)}), con una "
            f"tendencia {trend}: {matiz}.")
    if days is not None and days <= 45 and (avg_surp or 0) > 0 and otros_90:
        cierre = ("el calendario juega claramente a favor: hay varios eventos cerca y la compañía llega "
                  "con antecedentes de sorpresas positivas. El riesgo, en este escenario, no es la falta "
                  "de catalizadores sino que buena parte de la buena noticia ya esté en el precio.")
    elif days is not None and days <= 45 and (avg_surp or 0) > 0:
        cierre = ("el calendario juega a favor: hay un evento cercano y con antecedentes de sorpresas "
                  "positivas, aunque todo se juega a una sola carta.")
    elif otros_90 and (days is None or days > 45):
        cierre = ("aunque los resultados quedan lejos, la agenda de producto y negocio mantiene motivos "
                  "para que la acción se mueva por méritos propios antes de esa fecha.")
    elif senales and not otros_90:
        cierre = ("el impulso a corto plazo depende más de lo que ya está en los titulares que de una "
                  "fecha marcada en el calendario: conviene seguir cómo evoluciona esa noticia.")
    elif not eventos or (not otros_180 and days is not None and days > 90):
        cierre = ("no hay catalizadores propios a la vista, así que a corto plazo la acción dependerá "
                  "sobre todo del mercado y de su sector; la paciencia pesa más que el timing.")
    else:
        cierre = ("el próximo reporte será la prueba clave, así que conviene no anticiparse hasta ver "
                  "los números.")
    partes.append("En conjunto, " + cierre)
    catalysts_analysis = " ".join(partes)

    # ── Evento estrella (top_catalyst): el más cercano, sea o no earnings ────
    if prox_evento is not None and prox_evento.get("tipo") != "resultados":
        top_catalyst = (
            f"{prox_evento.get('titulo', 'Evento')} — {_ev_cuando(prox_evento)} — es la próxima cita del "
            f"calendario y el evento con más capacidad de reajustar la tesis a corto plazo"
            + (f", por delante del reporte de resultados ({_en_txt(days)})." if days is not None else ".")
        )
    elif prox_otro is not None and days is not None and days > 60:
        top_catalyst = (
            f"Con los resultados a {_dias_txt(days)} vista, la cita más relevante del tramo corto es "
            f"«{prox_otro.get('titulo', 'el próximo evento')}», {_ev_cuando(prox_otro)}."
        )
    else:
        top_catalyst = (
            f"El próximo reporte de resultados ({earnings.get('next_earnings', 'fecha por confirmar')}"
            f"{', ' + _en_txt(days) if days is not None else ''}"
            f") es el evento que puede "
            f"reajustar la tesis a corto plazo: conviene tenerlo en el radar."
        )
    if prox_otro is not None and prox_evento is not None and \
            prox_evento.get("tipo") == "resultados" and days is not None and days <= 60:
        top_catalyst += (f" Justo después llega «{prox_otro.get('titulo', '')}», "
                         f"{_ev_cuando(prox_otro)}.")

    return {
        "score": score,
        "conviction": _conv(score),
        "analysis": catalysts_analysis,
        "pros": _top(pros, 3),
        "cons": _top(cons, 3),
        "key_metrics": {
            "next_earnings": earnings.get("next_earnings", "N/A"),
            "earnings_beat_rate": f"{beat}/{total}" if beat is not None else "N/A",
            "avg_earnings_surprise": _pct(avg_surp, signed=True),
            "analyst_sentiment_trend": trend,
            "catalyst_timeline": timeline,
            "key_upcoming_event": (
                f"{prox_evento.get('titulo')} · {prox_evento.get('fecha_txt')}"
                if prox_evento is not None else
                f"Reporte de resultados · {earnings.get('next_earnings', 'fecha por confirmar')}"),
            # Nuevas (aditivas): la UI las usa si están, y si no, ni se enteran.
            "next_event": (f"{prox_otro.get('titulo')} · {prox_otro.get('fecha_txt')}"
                           if prox_otro is not None else "N/A"),
            "events_90d": str(n_90) if eventos else "N/A",
        },
        "sub_scores": {
            "earnings_momentum": round(em, 1),
            "catalyst_quality": round(cq, 1),
            "analyst_revision_trend": round(art, 1),
        },
        "top_catalyst": top_catalyst,
        # Agenda completa para que el dashboard la pinte. Serializable (la fecha
        # va como texto) para que sobreviva al guardado del análisis.
        "events": [
            {k: v for k, v in e.items() if k != "fecha"} for e in eventos[:8]
        ],
    }


def score_market_context(macro, news, earnings, info, events=None):
    return {
        "macro": _score_macro(macro, info),
        "sentiment": _score_sentiment(news, info),
        "catalysts": _score_catalysts(earnings, info, events),
    }
