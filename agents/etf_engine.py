"""
Motor de scoring de ETFs — 100% por código, sin IA.

Tres secciones con la misma filosofía que el motor de acciones:
  · score_etf_perfil       — coste, tamaño, madurez ("¿qué compro y cuánto cuesta?")
  · score_etf_composicion  — concentración y equilibrio ("¿qué llevo dentro?")
  · score_etf_rendimiento  — retorno, riesgo ajustado, drawdown ("¿qué ha dado?")

Todos los sub-scores usan _pond: un dato ausente se SACA de la ecuación (el
blindaje anti-huecos del motor de acciones aplica aquí desde el día uno).
Todas las funciones NUNCA lanzan.
"""
import math

from agents.code_engine import _pond, _lin, _clamp, _conv


def _f(v):
    """float o None. NaN → None."""
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _log10(v):
    try:
        v = _f(v)
        return math.log10(v) if v and v > 0 else None
    except Exception:
        return None


def _fmt_b(v):
    """1234567890 → '$1.2B'."""
    v = _f(v)
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def _completar(lista: list, reserva: list, minimo: int = 3) -> list:
    """≥`minimo` puntos por lado: las tarjetas de pros/contras van en dos
    columnas de la misma altura y ninguna puede quedarse corta."""
    for x in reserva:
        if len(lista) >= minimo:
            break
        if x not in lista:
            lista.append(x)
    return lista


_RESERVA_PROS_ETF = [
    "Diversificación instantánea en una sola operación, sin elegir valores uno a uno",
    "Total transparencia: la cartera, el coste y el valor liquidativo se publican a diario",
    "Liquidez de bolsa: se compra y se vende en segundos a precio de mercado",
]
_RESERVA_CONS_ETF = [
    "Un ETF nunca bate a su índice: garantiza el promedio del mercado, con lo bueno y lo malo",
    "En una caída general no hay gestor que defienda la cartera — cae lo que caiga el índice",
    "La comodidad tiene un riesgo propio: comprar sin mirar qué hay dentro",
]


# ── 1. PERFIL Y COSTES ─────────────────────────────────────────────────────

def score_etf_perfil(etf: dict) -> dict:
    """Coste (0-40) + tamaño (0-35) + madurez (0-25). NUNCA lanza."""
    try:
        etf = etf or {}
        ter = _f(etf.get("ter_pct"))
        ter_cat = _f(etf.get("ter_categoria_pct"))
        aum = _f(etf.get("aum"))
        edad = _f(etf.get("edad_anios"))
        prima = _f(etf.get("prima_descuento_pct"))

        # Coste: TER absoluto (0.03% ideal → 1% caro) + ratio vs su categoría.
        ratio_cat = (ter / ter_cat) if (ter is not None and ter_cat) else None
        coste = _pond([
            (ter,       1.00, 0.03, 6, 34),
            (ratio_cat, 1.20, 0.05, 0, 6),
        ], 4, 40)

        # Tamaño: log del AUM (30M = zona de riesgo de cierre; 100B = gigante).
        tamano = _pond([(_log10(aum), 7.5, 11.0, 3, 35)], 3, 35)

        # Madurez: años cotizando + que no cotice desviado de su NAV.
        madurez = _pond([
            (edad,                              0.5, 15.0, 3, 22),
            (abs(prima) if prima is not None else None, 2.5, 0.0, 0, 3),
        ], 3, 25)

        score = round(_clamp(coste + tamano + madurez, 0, 100), 1)

        pros, cons = [], []
        if ter is not None and ter <= 0.15:
            pros.append(f"Coste bajísimo: TER {ter:.2f}% — casi todo el retorno se queda contigo")
        elif ter is not None and ter >= 0.60:
            cons.append(f"TER {ter:.2f}%: caro para un ETF — el coste se come retorno cada año")
        if ratio_cat is not None and ratio_cat <= 0.35:
            pros.append(f"Mucho más barato que la media de su categoría ({ter_cat:.2f}%)")
        if aum is not None and aum >= 10e9:
            pros.append(f"Tamaño gigante ({_fmt_b(aum)}): liquidez sobrada y cero riesgo de cierre")
        elif aum is not None and aum < 100e6:
            cons.append(f"AUM pequeño ({_fmt_b(aum)}): riesgo real de cierre del fondo")
        if edad is not None and edad >= 10:
            pros.append(f"{edad:.0f} años de historial: ha sobrevivido a ciclos completos")
        elif edad is not None and edad < 2:
            cons.append("Menos de 2 años cotizando: historial corto para juzgarlo")
        if prima is not None and abs(prima) > 1.0:
            cons.append(f"Cotiza {abs(prima):.1f}% {'sobre' if prima > 0 else 'bajo'} su NAV — vigilar el precio de entrada")

        pros = _completar(pros, _RESERVA_PROS_ETF)
        cons = _completar(cons, _RESERVA_CONS_ETF)

        nombre = etf.get("nombre") or etf.get("ticker")
        p1 = (
            (f"{nombre} es un ETF UCITS domiciliado en Europa, de {etf.get('gestora') or 'gestora no identificada'}, "
             f"que replica el índice {etf.get('indice')}. Cotiza como {etf.get('listado')} en "
             f"{etf.get('divisa_listado')}, y su composición y comportamiento se analizan a través del índice "
             f"que replica — vía su gemelo americano {etf.get('gemelo_us')}, que sigue exactamente el mismo índice. "
             if etf.get("ucits") else
             f"{nombre} es un ETF de {etf.get('gestora') or 'gestora no identificada'}"
             + (f" en la categoría {etf.get('categoria')}" if etf.get("categoria") else "") + ". ")
            + (f"Cuesta un {ter:.2f}% anual (TER)" if ter is not None else "Su coste anual (TER) no está disponible ahora mismo")
            + (f" frente al {ter_cat:.2f}% medio de su categoría" +
               (" — cobra una fracción de lo habitual, y esa diferencia se acumula año tras año con el interés compuesto. "
                if (ter is not None and ter < ter_cat * 0.5) else
                ", en línea con lo que cobran sus comparables. " if (ter is not None and ter <= ter_cat) else
                " — cobra por encima de la media, y ese sobrecoste hay que justificarlo. ")
               if ter_cat else ". ")
        )
        p2 = (
            (f"Gestiona {_fmt_b(aum)}" +
             (", un tamaño que elimina de raíz el riesgo de cierre y garantiza horquillas mínimas. " if aum >= 10e9 else
              ", suficiente para operar con normalidad. " if aum >= 500e6 else
              " — un patrimonio pequeño: el riesgo de que la gestora lo cierre por falta de escala es real. ")
             if aum is not None else "Su patrimonio no está disponible en este momento. ")
            + (f"Lleva {edad:.0f} años cotizando" +
               (", historial de sobra para haber atravesado ciclos completos de mercado. " if edad >= 10 else
                ", un historial todavía corto para juzgar su comportamiento en crisis. " if edad < 3 else ". ")
               if edad is not None else "")
            + (f"Cotiza a un {abs(prima):.2f}% {'sobre' if prima > 0 else 'bajo'} su valor liquidativo (NAV)"
               + (" — desviación mínima, el mecanismo de arbitraje funciona. " if abs(prima) <= 0.5 else
                  " — conviene vigilar el precio de entrada. ")
               if prima is not None else "")
        )
        if ter is not None and ter_cat and ter < ter_cat * 0.4 and (aum or 0) >= 10e9:
            p3 = (f"El hallazgo: {nombre} tiene el perfil que se busca en un vehículo permanente — coste mínimo, "
                  "tamaño gigante y años de rodaje. En esta sección no hay letra pequeña.")
        elif ter is not None and ter >= 0.6:
            p3 = (f"El hallazgo: con un {ter:.2f}% anual de coste, {nombre} parte cada año con esa desventaja frente a "
                  "las alternativas baratas de su categoría — su composición y su rendimiento tienen que ganarse ese peaje.")
        elif aum is not None and aum < 100e6:
            p3 = (f"El hallazgo: el punto débil de {nombre} no es el coste, es la escala — los fondos pequeños son los "
                  "que las gestoras liquidan cuando recortan catálogo.")
        else:
            p3 = (f"El hallazgo: el perfil de {nombre} es sólido sin estridencias; la decisión real está en su "
                  "composición y su rendimiento, que es donde los ETFs se diferencian de verdad.")
        analysis = p1 + "<br><br>" + p2 + "<br><br>" + p3

        return {
            "score": score,
            "conviction": _conv(score),
            "analysis": analysis,
            "pros": pros[:3],
            "cons": cons[:3],
            "key_metrics": {
                "ter_pct": ter, "ter_categoria_pct": ter_cat, "aum": aum,
                "edad_anios": edad, "gestora": etf.get("gestora"),
                "categoria": etf.get("categoria"),
                "prima_descuento_pct": prima,
                "distribucion": etf.get("distribucion"),
                "divisa": etf.get("divisa"),
                # Señalización UCITS (None/ausente en ETFs de EE.UU.)
                "ucits": bool(etf.get("ucits")),
                "gemelo_us": etf.get("gemelo_us"),
                "gemelo_nombre": etf.get("gemelo_nombre"),
                "indice": etf.get("indice"),
                "listado": etf.get("listado"),
                "divisa_listado": etf.get("divisa_listado"),
            },
            "sub_scores": {"coste": round(coste, 1), "tamano": round(tamano, 1),
                           "madurez": round(madurez, 1)},
            "key_insight": (
                f"TER {ter:.2f}% con {_fmt_b(aum)} bajo gestión" if (ter is not None and aum)
                else "Perfil incompleto: faltan datos de coste o tamaño"),
        }
    except Exception as e:
        return {"score": 50.0, "conviction": "LOW", "analysis": "",
                "pros": [], "cons": [], "key_metrics": {}, "sub_scores": {},
                "key_insight": "", "error": str(e)}


# ── 2. COMPOSICIÓN ─────────────────────────────────────────────────────────

def score_etf_composicion(etf: dict) -> dict:
    """Concentración (0-40) + equilibrio sectorial (0-35) + amplitud (0-25).
    Un ETF sin cartera publicada (oro, materias primas) queda NEUTRO con
    explicación — jamás penalizado por un dato que no existe. NUNCA lanza."""
    try:
        etf = etf or {}
        holdings = etf.get("top_holdings") or []
        sectores = etf.get("sectores") or {}
        conc = _f(etf.get("concentracion_top10_pct"))

        if not holdings and not sectores:
            return {
                "score": 50.0, "conviction": "LOW",
                "analysis": ("Este ETF no publica una cartera de valores clásica — típico en "
                             "fondos de materias primas o de futuros, cuyo activo es el propio "
                             "subyacente (oro físico, contratos…). La composición no puntúa ni a "
                             "favor ni en contra: simplemente no es la métrica de este vehículo."),
                "pros": [], "cons": [],
                "key_metrics": {"n_holdings": 0, "concentracion_top10_pct": None,
                                "sector_dominante": None},
                "sub_scores": {"concentracion": 20.0, "equilibrio": 17.5, "amplitud": 12.5},
                "key_insight": "Sin cartera publicada (vehículo de subyacente directo): nota neutra.",
            }

        # HHI sectorial: suma de pesos² (0.10 = muy repartido, 1.0 = un sector).
        hhi = None
        try:
            pesos = [v / 100.0 for v in sectores.values() if _f(v)]
            hhi = sum(w * w for w in pesos) if pesos else None
        except Exception:
            hhi = None
        n_sect = sum(1 for v in sectores.values() if _f(v) and v >= 1.0) or None

        concentracion = _pond([(conc, 75.0, 15.0, 6, 36)], 4, 40)
        equilibrio = _pond([(hhi, 0.60, 0.10, 5, 31)], 4, 35)
        amplitud = _pond([(float(n_sect) if n_sect else None, 2, 10, 5, 22)], 4, 25)
        score = round(_clamp(concentracion + equilibrio + amplitud, 0, 100), 1)

        sector_dom = None
        try:
            if sectores:
                sector_dom = max(sectores.items(), key=lambda kv: kv[1] or 0)
        except Exception:
            pass

        pros, cons = [], []
        if conc is not None and conc <= 25:
            pros.append(f"Muy diversificado: el top-10 solo pesa {conc:.0f}%")
        elif conc is not None and conc >= 50:
            cons.append(f"Concentrado: el top-10 pesa {conc:.0f}% — depende de pocas empresas")
        if sector_dom and _f(sector_dom[1]) and sector_dom[1] >= 40:
            cons.append(f"Un solo sector domina la cartera ({sector_dom[1]:.0f}%)")
        elif n_sect and n_sect >= 8:
            pros.append(f"Exposición repartida entre {n_sect} sectores")
        if holdings and holdings[0][2] is not None and holdings[0][2] >= 10:
            cons.append(f"La primera posición ({holdings[0][0]}) pesa {holdings[0][2]:.1f}% ella sola")

        pros = _completar(pros, _RESERVA_PROS_ETF)
        cons = _completar(cons, _RESERVA_CONS_ETF)

        top3 = ", ".join(f"{h[0]} ({h[2]:.1f}%)" for h in holdings[:3]
                         if h[2] is not None) or "no disponibles"
        p1 = (
            f"Al comprar este ETF, lo que de verdad se compra es su cesta — y las mayores posiciones son {top3}. "
            + (f"Las diez primeras concentran el {conc:.0f}% del fondo" +
               (": la suerte del ETF depende en buena parte de un puñado de empresas. " if conc >= 45 else
                ", un reparto sano donde ninguna posición manda demasiado. " if conc <= 30 else ". ")
               if conc is not None else "")
        )
        p2 = (
            (f"Por sectores, el de más peso es {sector_dom[0].replace('_', ' ')} con un {sector_dom[1]:.0f}%"
             + (" — una apuesta sectorial en toda regla, no un fondo neutral. " if _f(sector_dom[1]) and sector_dom[1] >= 40 else ". ")
             if sector_dom else "")
            + (f"Hay presencia relevante en {n_sect} sectores" +
               (", así que un mal año de una industria concreta no hunde la cartera. " if n_sect and n_sect >= 8 else ". ")
               if n_sect else "")
            + "La concentración no es mala en sí misma: es una elección — cuanto más concentrado, más se parece a "
              "apostar por pocas empresas y menos a diversificar."
        )
        if conc is not None and conc >= 45 and sector_dom and _f(sector_dom[1]) and sector_dom[1] >= 35:
            p3 = ("El hallazgo: este ETF combina concentración por empresa Y por sector — funciona como una apuesta "
                  "direccional, y conviene tratarlo como tal en el tamaño de la posición.")
        elif conc is not None and conc <= 25:
            p3 = ("El hallazgo: pocas cestas están tan repartidas como esta — aquí la diversificación no es un eslogan, "
                  "se ve en los pesos.")
        else:
            p3 = ("El hallazgo: la cesta es razonablemente equilibrada; lo que decida la inversión no será la "
                  "composición, sino el coste y el rendimiento del índice que replica.")
        analysis = p1 + "<br><br>" + p2 + "<br><br>" + p3

        return {
            "score": score, "conviction": _conv(score),
            "analysis": analysis, "pros": pros[:3], "cons": cons[:3],
            "key_metrics": {
                "n_holdings": len(holdings),
                "concentracion_top10_pct": conc,
                "sector_dominante": sector_dom[0] if sector_dom else None,
                "sector_dominante_pct": _f(sector_dom[1]) if sector_dom else None,
                "n_sectores": n_sect,
            },
            "sub_scores": {"concentracion": round(concentracion, 1),
                           "equilibrio": round(equilibrio, 1),
                           "amplitud": round(amplitud, 1)},
            "key_insight": (f"Top-10 = {conc:.0f}% del fondo" if conc is not None
                            else "Composición parcial"),
            "raw_data": {"top_holdings": holdings, "sectores": sectores},
        }
    except Exception as e:
        return {"score": 50.0, "conviction": "LOW", "analysis": "",
                "pros": [], "cons": [], "key_metrics": {}, "sub_scores": {},
                "key_insight": "", "error": str(e)}


# ── 3. RENDIMIENTO Y RIESGO ────────────────────────────────────────────────

def score_etf_rendimiento(etf: dict, hist=None) -> dict:
    """Retorno (0-35) + riesgo ajustado (0-35) + drawdown (0-30). Los cálculos
    de mercado salen del histórico propio (ya multi-fallback). NUNCA lanza."""
    try:
        etf = etf or {}
        r3 = _f(etf.get("retorno_3y_pct"))
        r5 = _f(etf.get("retorno_5y_pct"))
        beta = _f(etf.get("beta_3y"))
        yld = _f(etf.get("yield_pct"))
        prima = _f(etf.get("prima_descuento_pct"))

        ret_1y = vol_anual = max_dd = sharpe = None
        try:
            if hist is not None and not hist.empty and len(hist) > 60:
                c = hist["Close"].dropna()
                ult = c.iloc[-252:] if len(c) >= 252 else c
                ret_1y = round((float(ult.iloc[-1]) / float(ult.iloc[0]) - 1.0) * 100.0, 2)
                rend = c.pct_change().dropna()
                if len(rend) > 40:
                    vol_anual = round(float(rend.std()) * math.sqrt(252) * 100.0, 2)
                dd = (c / c.cummax() - 1.0)
                max_dd = round(float(dd.min()) * 100.0, 2)
                if vol_anual:
                    base_r = ret_1y if ret_1y is not None else r3
                    if base_r is not None:
                        sharpe = round((base_r - 4.5) / vol_anual, 2)   # T-bill ~4.5%
        except Exception:
            pass

        retorno = _pond([
            (r3 if r3 is not None else ret_1y, -5.0, 22.0, 4, 27),
            (r5,                                -3.0, 16.0, 0, 8),
        ], 4, 35)
        riesgo_adj = _pond([(sharpe, -0.5, 1.5, 4, 31)], 4, 35)
        drawdown = _pond([(max_dd, -45.0, -8.0, 4, 27)], 4, 30)
        score = round(_clamp(retorno + riesgo_adj + drawdown, 0, 100), 1)

        pros, cons = [], []
        if r3 is not None and r3 >= 12:
            pros.append(f"Retorno medio del {r3:.1f}% anual en 3 años")
        elif r3 is not None and r3 < 3:
            cons.append(f"Retorno pobre a 3 años ({r3:.1f}% anual)")
        if sharpe is not None and sharpe >= 0.8:
            pros.append(f"Buena relación retorno/riesgo (Sharpe ≈ {sharpe:.1f})")
        elif sharpe is not None and sharpe < 0:
            cons.append("En el último año no compensó el riesgo asumido")
        if max_dd is not None and max_dd <= -30:
            cons.append(f"Caída máxima del {abs(max_dd):.0f}% en el período — hay que aguantarla")
        elif max_dd is not None and max_dd > -12:
            pros.append(f"Caída máxima contenida ({abs(max_dd):.0f}%)")
        if yld is not None and yld >= 2.5:
            pros.append(f"Reparte un {yld:.1f}% anual en dividendos")

        pros = _completar(pros, _RESERVA_PROS_ETF)
        cons = _completar(cons, _RESERVA_CONS_ETF)

        p1 = (
            (f"En los últimos 3 años ha rendido un {r3:.1f}% anual medio"
             + (f" y un {r5:.1f}% anual a 5 años" if r5 is not None else "") +
             (" — por encima de lo que históricamente da la bolsa a largo plazo. " if r3 >= 11 else
              ", en la zona de lo que históricamente da la bolsa. " if r3 >= 6 else
              " — un ritmo pobre para el riesgo que se asume en renta variable. ")
             if r3 is not None else
             "El retorno histórico de largo plazo no está disponible en este momento. ")
            + (f"En el último año acumula {ret_1y:+.1f}%. " if ret_1y is not None else "")
        )
        p2 = (
            (f"Ese retorno se pagó con una volatilidad del {vol_anual:.0f}% anualizada y una peor caída del "
             f"{abs(max_dd):.0f}% de pico a valle — es lo que un inversor tuvo que AGUANTAR sin vender para "
             "llevarse el resultado. " if (vol_anual and max_dd is not None) else "")
            + (f"El Sharpe aproximado de {sharpe:.2f} " +
               ("dice que la relación retorno/riesgo fue excelente. " if sharpe >= 1 else
                "dice que compensó el riesgo, sin brillar. " if sharpe >= 0.4 else
                "dice que el riesgo asumido no se pagó bien este año. ")
               if sharpe is not None else "")
            + (f"Con beta {beta:.2f}, {'amplifica' if beta > 1.1 else 'suaviza' if beta < 0.9 else 'replica'} "
               "los movimientos del mercado. " if beta is not None else "")
        )
        if sharpe is not None and sharpe >= 0.8 and max_dd is not None and max_dd > -20:
            p3 = ("El hallazgo: la combinación de buen retorno con caídas contenidas es exactamente lo que se le "
                  "pide a un vehículo de largo plazo — pocos fondos la consiguen a la vez.")
        elif max_dd is not None and max_dd <= -30:
            p3 = (f"El hallazgo: quien entre aquí debe asumir que un {abs(max_dd):.0f}% de caída no es un accidente, "
                  "es parte del billete — la pregunta correcta no es si volverá a pasar, sino si se aguantará cuando pase.")
        else:
            p3 = ("El hallazgo: un ETF no elige valores — entrega el mercado que promete, con su riesgo incluido. "
                  "Estos números son ese mercado en cifras, no una promesa de la gestora.")
        analysis = p1 + "<br><br>" + p2 + "<br><br>" + p3

        return {
            "score": score, "conviction": _conv(score),
            "analysis": analysis, "pros": pros[:3], "cons": cons[:3],
            "key_metrics": {
                "retorno_1y_pct": ret_1y, "retorno_3y_pct": r3, "retorno_5y_pct": r5,
                "vol_anual_pct": vol_anual, "max_drawdown_pct": max_dd,
                "sharpe": sharpe, "beta_3y": beta, "yield_pct": yld,
                "prima_descuento_pct": prima,
            },
            "sub_scores": {"retorno": round(retorno, 1),
                           "riesgo_ajustado": round(riesgo_adj, 1),
                           "drawdown": round(drawdown, 1)},
            "key_insight": (f"{r3:.1f}% anual (3a) con caída máxima del {abs(max_dd):.0f}%"
                            if (r3 is not None and max_dd is not None)
                            else "Rendimiento con datos parciales"),
        }
    except Exception as e:
        return {"score": 50.0, "conviction": "LOW", "analysis": "",
                "pros": [], "cons": [], "key_metrics": {}, "sub_scores": {},
                "key_insight": "", "error": str(e)}
