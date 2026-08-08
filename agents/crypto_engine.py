"""
Motor de scoring de CRIPTOMONEDAS — 100% por código, sin IA.

Cuatro secciones (el Técnico se reutiliza del motor de acciones):
  · score_crypto_tokenomics  — escasez, liquidez, madurez, ciclo
  · score_crypto_adopcion    — TVL/uso de red (con "no aplica" honesto)
  · score_crypto_sentimiento — Fear & Greed contrario, momentum, tendencia
  · score_crypto_riesgo      — volatilidad, drawdown de ciclo, correlaciones

Mismo blindaje que el resto del motor: _pond saca los huecos de la ecuación,
las funciones NUNCA lanzan, y lo que no aplica a una moneda se dice (no se
puntúa a ciegas) — el mismo patrón que los bancos en fundamentales.
"""
import math

from agents.code_engine import _pond, _clamp, _conv


def _f(v):
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
    """Garantiza que la lista tenga al menos `minimo` puntos, completando con
    afirmaciones de la reserva (verdaderas para cualquier cripto). Las tarjetas
    de pros/contras van en dos columnas de la MISMA altura, así que ninguno de
    los dos lados puede quedarse corto."""
    for x in reserva:
        if len(lista) >= minimo:
            break
        if x not in lista:
            lista.append(x)
    return lista


_RESERVA_PROS = [
    "Mercado abierto 24/7 con liquidez global: se puede entrar o salir en cualquier momento, sin esperar aperturas",
    "Precio y volumen auditables en tiempo real, sin intermediarios que maquillen el dato",
    "La custodia propia es posible: el activo no depende de la solvencia de un banco o bróker",
]
_RESERVA_CONS = [
    "Cotiza también de madrugada y en fin de semana: los movimientos bruscos pueden llegar cuando nadie mira",
    "La liquidez se concentra en pocas plataformas: un problema en una de ellas contagia al precio",
    "La tesis depende de la adopción futura, y la adopción no avanza en línea recta",
]


# ── 1. PERFIL Y TOKENOMICS ─────────────────────────────────────────────────

def score_crypto_tokenomics(d: dict) -> dict:
    """Escasez (0-30) + liquidez (0-25) + madurez (0-25) + ciclo (0-20)."""
    try:
        d = d or {}
        pct_emitido = _f(d.get("pct_emitido"))
        tiene_tope = _f(d.get("supply_maximo")) is not None
        turnover = _f(d.get("turnover_pct"))
        rank = _f(d.get("rank"))
        ath_dist = _f(d.get("ath_distancia_pct"))
        vol24 = _f(d.get("volumen_24h"))

        # Escasez: con tope de emisión, cuánto está ya emitido (BTC 95.6% =
        # escasez programada). Sin tope, es una POLÍTICA de emisión — se
        # explica y puntúa medio-bajo fijo, no es un hueco de datos.
        if tiene_tope:
            escasez = _pond([(pct_emitido, 40.0, 100.0, 10, 28)], 8, 30)
        else:
            escasez = 14.0

        liquidez = _pond([
            (turnover,      0.5, 8.0, 3, 15),
            (_log10(vol24), 8.0, 10.5, 1, 10),
        ], 4, 25)
        madurez = _pond([(rank, 45.0, 1.0, 5, 23)], 5, 25)

        # Ciclo: distancia al ATH, lectura NO monótona.
        if ath_dist is None:
            ciclo = 10.0
        elif ath_dist > -15:
            ciclo = 9.0     # pegado a máximos: menos margen, más euforia
        elif ath_dist > -45:
            ciclo = 13.0
        elif ath_dist > -78:
            ciclo = 15.0    # zona media-baja del ciclo
        else:
            ciclo = 7.0     # destrucción severa: especulativo

        score = round(_clamp(escasez + liquidez + madurez + ciclo, 0, 100), 1)

        nombre = d.get("nombre") or "Este activo"

        # ── Pros y contras: SIEMPRE ≥3 de cada lado, específicos y con dato ──
        pros, cons = [], []
        if tiene_tope and pct_emitido and pct_emitido >= 90:
            pros.append(f"Escasez programada: el {pct_emitido:.1f}% de la oferta máxima ya está emitido — la dilución futura es mínima")
        elif tiene_tope and pct_emitido:
            pros.append(f"Oferta con tope: solo queda por emitir el {100-pct_emitido:.1f}%, un calendario conocido de antemano")
        if rank is not None and rank <= 2:
            pros.append(f"Activo dominante del mercado cripto: nº{rank:.0f} por capitalización, la referencia que el resto sigue")
        elif rank is not None and rank <= 10:
            pros.append(f"Peso pesado del mercado: nº{rank:.0f} por capitalización, con la liquidez y visibilidad que eso implica")
        if turnover is not None and turnover >= 2:
            pros.append(f"Liquidez profunda: rota a diario el {turnover:.1f}% de su capitalización — entrar y salir no mueve el precio")
        elif vol24 is not None and vol24 >= 1e9:
            pros.append(f"Mueve {_fmt_b(vol24)} al día: liquidez suficiente para operar sin fricción")
        if ath_dist is not None and -70 <= ath_dist <= -35:
            pros.append(f"Cotiza un {abs(ath_dist):.0f}% bajo sus máximos: el exceso del ciclo anterior ya se purgó en buena parte")

        if not tiene_tope:
            cons.append("Sin tope de emisión: la oferta futura depende de la política del protocolo, no de un límite grabado en piedra")
        if ath_dist is not None and ath_dist <= -70:
            cons.append(f"Un {abs(ath_dist):.0f}% por debajo de máximos históricos: el mercado le retiró mucha confianza y recuperarla lleva ciclos")
        elif ath_dist is not None and ath_dist > -15:
            cons.append(f"A solo un {abs(ath_dist):.0f}% de sus máximos históricos: queda poco margen de sorpresa y mucho de euforia")
        if rank is not None and rank > 25:
            cons.append(f"Fuera del top-25 (puesto {rank:.0f}): en cripto, quedarse atrás en capitalización suele preceder a la irrelevancia")
        if turnover is not None and turnover < 1.5:
            cons.append(f"Rotación diaria del {turnover:.1f}%: liquidez justa — en pánicos, los libros de órdenes finos amplifican las caídas")
        cons.append("Como todo el mercado cripto, no genera flujos de caja: su valor depende de la adopción y la convicción, no de beneficios")
        cons.append("El precio se mueve con el ciclo global de liquidez y el apetito de riesgo — puede caer sin que nada cambie en el protocolo")

        pros = _completar(pros, _RESERVA_PROS)
        cons = _completar(cons, _RESERVA_CONS)

        # ── Prosa completa (tres bloques): datos → contexto → hallazgo ──────
        p1 = (
            f"{nombre} capitaliza {_fmt_b(d.get('market_cap'))} y ocupa el puesto nº{rank:.0f} del mercado cripto"
            + (f", con el {d.get('dominancia_propia'):.1f}% de toda la capitalización del sector" if _f(d.get("dominancia_propia")) and d.get("dominancia_propia") >= 1 else "")
            + ". "
            + (f"Su oferta está limitada por diseño y el {pct_emitido:.1f}% ya está en circulación: quien compra hoy sabe "
               f"exactamente cuánta dilución queda por delante ({100-pct_emitido:.1f}%), algo que casi ningún activo puede ofrecer. "
               if (tiene_tope and pct_emitido) else
               "No tiene tope de emisión: su oferta futura la marcan las reglas del protocolo (quemas, recompensas, staking), "
               "así que la tesis depende de que la demanda crezca más rápido que esa emisión. ")
        )
        p2 = (
            f"En el día a día mueve {_fmt_b(vol24)}"
            + (f", el {turnover:.1f}% de su capitalización — " +
               ("una rotación alta que habla de un mercado profundo y activo. " if turnover and turnover >= 2 else
                "una rotación moderada: hay liquidez, pero no sobra. ") if turnover is not None else ". ")
            + (f"Respecto a su máximo histórico cotiza un {abs(ath_dist):.0f}% por debajo"
               + (", es decir, en plena zona de reconstrucción del ciclo: el castigo ya ocurrió, y lo que queda es saber si la demanda vuelve. "
                  if -78 < ath_dist <= -45 else
                  ", una caída tan profunda que el mercado la trata como apuesta especulativa, no como activo consolidado. "
                  if ath_dist <= -78 else
                  ", relativamente cerca de máximos: el mercado ya paga aquí buena parte del optimismo. ")
               if ath_dist is not None else "")
        )
        if tiene_tope and pct_emitido and pct_emitido >= 90 and rank is not None and rank <= 5:
            p3 = (f"El hallazgo: la combinación de escasez casi agotada y rango nº{rank:.0f} hace de {nombre} el activo con la "
                  "tokenomics más defendible de su clase — el riesgo aquí no está en la dilución, está en el precio de entrada.")
        elif not tiene_tope and rank is not None and rank <= 10:
            p3 = (f"El hallazgo: {nombre} compensa la falta de tope de emisión con tamaño y liquidez; funciona mientras su red "
                  "genere demanda real, así que la sección de Adopción pesa más que esta para su tesis.")
        elif rank is not None and rank > 25:
            p3 = (f"El hallazgo: con el puesto {rank:.0f} y esta estructura de oferta, {nombre} es una apuesta de alto riesgo "
                  "dentro de una clase de activo ya volátil — el tamaño de la posición lo es todo.")
        else:
            p3 = (f"El hallazgo: la tokenomics de {nombre} es sólida sin ser excepcional; la decisión se inclina por lo que "
                  "digan la adopción de su red y el momento del ciclo, no por la estructura de oferta en sí.")
        analysis = p1 + "<br><br>" + p2 + "<br><br>" + p3

        return {
            "score": score, "conviction": _conv(score), "analysis": analysis,
            "pros": pros[:3], "cons": cons[:3],
            "key_metrics": {
                "market_cap": d.get("market_cap"), "rank": rank,
                "supply_circulante": d.get("supply_circulante"),
                "supply_maximo": d.get("supply_maximo"),
                "pct_emitido": pct_emitido, "ath": d.get("ath"),
                "ath_distancia_pct": ath_dist, "turnover_pct": turnover,
                "volumen_24h": vol24,
                "dominancia_propia": d.get("dominancia_propia"),
            },
            "sub_scores": {"escasez": round(escasez, 1), "liquidez": round(liquidez, 1),
                           "madurez": round(madurez, 1), "ciclo": round(ciclo, 1)},
            "key_insight": (
                f"Nº{rank:.0f} del mercado, a {abs(ath_dist):.0f}% de sus máximos"
                if (rank is not None and ath_dist is not None) else "Tokenomics con datos parciales"),
        }
    except Exception as e:
        return {"score": 50.0, "conviction": "LOW", "analysis": "", "pros": [],
                "cons": [], "key_metrics": {}, "sub_scores": {}, "key_insight": "",
                "error": str(e)}


# ── 2. ADOPCIÓN Y RED ──────────────────────────────────────────────────────

def score_crypto_adopcion(d: dict) -> dict:
    """Para cadenas de contratos: TVL + tendencia + TVL/mcap.
    Para reserva/pagos/meme/infra: dominancia + liquidez (el TVL NO es su
    métrica — se dice, igual que 'no aplica' en bancos)."""
    try:
        d = d or {}
        clase = d.get("clase") or ""
        tvl = _f(d.get("tvl"))
        tvl_delta = _f(d.get("tvl_delta_30d_pct"))
        tvl_ratio = _f(d.get("tvl_mcap_ratio"))
        dominancia = _f(d.get("dominancia_propia"))
        turnover = _f(d.get("turnover_pct"))
        vol24 = _f(d.get("volumen_24h"))
        # El TVL solo es LA métrica en cadenas de contratos. Bitcoin tiene
        # cadena en DefiLlama ($3.5B) pero es un activo de RESERVA: juzgarlo
        # por TVL le daba 46/100 al activo dominante del mercado — la clase
        # decide la vara de medir, no el hecho de que exista el dato.
        tvl_aplica = (clase == "contratos") and bool(d.get("cadena_defi"))

        if tvl_aplica:
            tvl_abs = _pond([(_log10(tvl), 8.0, 10.7, 5, 36)], 5, 40)
            tendencia = _pond([(tvl_delta, -25.0, 25.0, 5, 31)], 5, 35)
            ratio = _pond([(tvl_ratio, 1.0, 22.0, 5, 22)], 5, 25)
            score = round(_clamp(tvl_abs + tendencia + ratio, 0, 100), 1)
            subs = {"tvl": round(tvl_abs, 1), "tendencia_tvl": round(tendencia, 1),
                    "tvl_vs_mcap": round(ratio, 1)}
        else:
            dom = _pond([(_log10((dominancia or 0) * 100 or None), 0.7, 3.76, 5, 36)], 5, 40)
            liq = _pond([(turnover, 0.5, 8.0, 5, 31)], 5, 35)
            uso = _pond([(_log10(vol24), 7.5, 10.5, 5, 22)], 5, 25)
            score = round(_clamp(dom + liq + uso, 0, 100), 1)
            subs = {"dominancia": round(dom, 1), "liquidez": round(liq, 1),
                    "volumen": round(uso, 1)}

        nombre = d.get("nombre") or "Este activo"

        # ── Pros y contras: SIEMPRE ≥3 de cada lado ──────────────────────
        pros, cons = [], []
        if tvl_aplica:
            if tvl and tvl >= 10e9:
                pros.append(f"Capital real trabajando: {_fmt_b(tvl)} bloqueados en su red — uso verificable, no narrativa")
            elif tvl:
                pros.append(f"Tiene ecosistema activo con {_fmt_b(tvl)} depositados en sus aplicaciones")
            if tvl_delta is not None and tvl_delta >= 5:
                pros.append(f"El capital ENTRA: el valor bloqueado creció {tvl_delta:+.1f}% en 30 días")
            elif tvl_delta is not None and tvl_delta >= 0:
                pros.append(f"El valor bloqueado se mantiene estable ({tvl_delta:+.1f}% en 30 días): la base de usuarios no se fuga")
            if tvl_ratio is not None and tvl_ratio >= 10:
                pros.append(f"Por cada dólar de capitalización hay {tvl_ratio:.0f} centavos trabajando dentro — valoración anclada en uso real")
            if tvl_delta is not None and tvl_delta <= -10:
                cons.append(f"El capital SALE: el valor bloqueado cayó {tvl_delta:.1f}% en 30 días")
            if tvl and tvl < 5e9:
                cons.append(f"Ecosistema aún pequeño ({_fmt_b(tvl)}): compite contra redes con 5-10 veces más capital dentro")
            cons.append("El capital de las redes de contratos es mercenario: migra a donde estén los incentivos, sin lealtad")
            cons.append("Depende de que sus aplicaciones sigan atrayendo usuarios frente a cadenas rivales más rápidas o baratas")
        else:
            if dominancia is not None and dominancia >= 30:
                pros.append(f"Domina el {dominancia:.0f}% de TODO el mercado cripto: es el activo que define la clase")
            elif dominancia is not None and dominancia >= 1:
                pros.append(f"Mantiene el {dominancia:.1f}% de la capitalización total del mercado — relevancia consolidada")
            if turnover is not None and turnover >= 2:
                pros.append(f"Liquidez profunda: rota el {turnover:.1f}% de su capitalización cada día")
            if vol24 is not None and vol24 >= 1e9:
                pros.append(f"{_fmt_b(vol24)} de volumen diario: mercado activo en cualquier huso horario")
            if clase == "meme":
                cons.append("Activo meme: su adopción es narrativa y comunidad, no uso de red medible")
            if clase == "pagos":
                cons.append("Compite como red de pagos contra rieles tradicionales y stablecoins, que mueven mucho más valor")
            if dominancia is not None and dominancia < 1:
                cons.append(f"Solo el {dominancia:.2f}% del mercado: su suerte depende de flujos que no controla")
            cons.append("Sin un ecosistema de aplicaciones que retenga capital, la demanda es más sentimiento que necesidad")
            cons.append("La adopción institucional avanza por ciclos: en las contracciones, estos activos son los primeros en salir de las carteras")

        pros = _completar(pros, _RESERVA_PROS)
        cons = _completar(cons, _RESERVA_CONS)

        # ── Prosa completa (tres bloques) ────────────────────────────────
        if tvl_aplica:
            p1 = (
                f"La red de {nombre} tiene {_fmt_b(tvl)} de valor bloqueado (TVL): capital que usuarios reales "
                f"decidieron poner a trabajar dentro de sus aplicaciones — préstamos, intercambios, staking — y no "
                f"simplemente guardar en una cartera. Es el proxy de adopción más honesto que existe en cripto, "
                f"porque no se puede fingir sin poner dinero. "
            )
            p2 = (
                (f"La tendencia manda tanto como el nivel: en los últimos 30 días ese capital {'creció' if tvl_delta >= 0 else 'se contrajo'} "
                 f"un {abs(tvl_delta):.1f}%" +
                 (", señal de que el ecosistema atrae más de lo que pierde. " if tvl_delta >= 3 else
                  ", prácticamente estable. " if tvl_delta > -3 else
                  " — cuando el capital sale de una red de contratos, suele avisar antes que el precio. ")
                 if tvl_delta is not None else "")
                + (f"El ratio TVL/capitalización del {tvl_ratio:.1f}% " +
                   ("indica que buena parte de la valoración está respaldada por uso real, no solo por expectativa. "
                    if tvl_ratio >= 10 else
                    "muestra que la valoración va muy por delante del uso actual: el mercado paga futuro. ")
                   if tvl_ratio is not None else "")
            )
            p3 = ("El hallazgo: " +
                  (f"{nombre} es de las pocas redes donde la adopción se puede AUDITAR en tiempo real, y hoy los datos "
                   "acompañan la tesis." if (tvl and tvl >= 10e9 and (tvl_delta or 0) >= 0) else
                   f"la tesis de {nombre} exige vigilar el TVL cada mes: es el termómetro que confirmará o desmentirá al precio."))
        else:
            explic = {"reserva": "es un activo de reserva de valor: su adopción se mide en dominancia y liquidez, no en aplicaciones",
                      "pagos": "es una red de pagos y liquidación: lo que importa es cuánto valor mueve y con qué profundidad",
                      "meme": "es un activo meme: su demanda vive de la narrativa y la comunidad, no de un uso económico medible",
                      "infraestructura": "es infraestructura del ecosistema (oráculos y servicios): su uso se da DENTRO de otras cadenas",
                      }.get(clase, "su adopción se mide por volumen y peso de mercado")
            p1 = (f"Para {nombre} el valor bloqueado (TVL) no es la vara de medir — {explic}. "
                  f"Juzgarlo con la métrica de las cadenas de contratos sería medir a un banco por su margen bruto: "
                  f"la app usa aquí las métricas que SÍ le corresponden.")
            p2 = (
                (f"Con esa vara: concentra el {dominancia:.2f}% de la capitalización de todo el mercado cripto"
                 + (" — una posición dominante que ningún competidor ha logrado erosionar en más de una década" if dominancia >= 30 else "")
                 + ". " if dominancia is not None else "")
                + f"Mueve {_fmt_b(vol24)} al día"
                + (f", rotando el {turnover:.1f}% de su capitalización — "
                   + ("liquidez comparable a las acciones más negociadas del mundo. " if turnover >= 2 else "liquidez correcta sin ser sobresaliente. ")
                   if turnover is not None else ". ")
            )
            p3 = ("El hallazgo: " +
                  (f"la adopción de {nombre} ES su dominancia — mientras conserve este peso, seguirá siendo la puerta de "
                   "entrada del capital al mercado cripto." if (dominancia or 0) >= 30 else
                   f"sin un ecosistema propio que retenga capital, la tesis de {nombre} descansa en su liquidez y su marca; "
                   "ambas son reales, pero también son las primeras en sufrir cuando el ciclo se enfría."))
        analysis = p1 + "<br><br>" + p2 + "<br><br>" + p3

        return {
            "score": score, "conviction": _conv(score), "analysis": analysis,
            "pros": pros[:3], "cons": cons[:3],
            "key_metrics": {
                "tvl": tvl, "tvl_delta_30d_pct": tvl_delta,
                "tvl_mcap_ratio": tvl_ratio, "tvl_aplica": tvl_aplica,
                "dominancia_propia": dominancia, "turnover_pct": turnover,
                "clase": clase,
                "volumen_24h": vol24, "market_cap": _f(d.get("market_cap")),
            },
            "sub_scores": subs,
            "key_insight": (f"TVL {_fmt_b(tvl)} ({tvl_delta:+.1f}% en 30d)"
                            if (tvl_aplica and tvl and tvl_delta is not None)
                            else f"Dominancia {dominancia:.1f}% del mercado" if dominancia is not None
                            else "Adopción con datos parciales"),
        }
    except Exception as e:
        return {"score": 50.0, "conviction": "LOW", "analysis": "", "pros": [],
                "cons": [], "key_metrics": {}, "sub_scores": {}, "key_insight": "",
                "error": str(e)}


# ── 3. SENTIMIENTO ─────────────────────────────────────────────────────────

def score_crypto_sentimiento(d: dict, hist=None) -> dict:
    """Fear & Greed contrario (0-35) + momentum (0-35) + tendencia (0-30)."""
    try:
        d = d or {}
        fng = _f(d.get("fng_actual"))
        d7 = _f(d.get("delta_7d_pct"))
        d30 = _f(d.get("delta_30d_pct"))
        d1y = _f(d.get("delta_1y_pct"))

        # Lectura CONTRARIA del F&G: el miedo extremo ha sido históricamente
        # zona de acumulación; la euforia extrema, zona de reparto.
        if fng is None:
            f_contrario = 17.0
        elif fng <= 20:
            f_contrario = 28.0
        elif fng <= 40:
            f_contrario = 24.0
        elif fng <= 60:
            f_contrario = 17.0
        elif fng <= 80:
            f_contrario = 11.0
        else:
            f_contrario = 6.0

        momentum = _pond([
            (d30, -30.0, 30.0, 4, 27),
            (d7,  -15.0, 15.0, 0, 4),
        ], 4, 35)

        # Tendencia: precio vs medias 50/200 del histórico propio.
        tendencia = 15.0
        precio_vs = None
        try:
            if hist is not None and not hist.empty and len(hist) >= 60:
                c = hist["Close"].dropna()
                px = float(c.iloc[-1])
                ma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else None
                ma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
                if ma50 and ma200:
                    if px > ma50 > ma200:
                        tendencia, precio_vs = 26.0, "alcista (sobre MA50 y MA200)"
                    elif px > ma200:
                        tendencia, precio_vs = 19.0, "constructiva (sobre MA200)"
                    elif px > ma50:
                        tendencia, precio_vs = 13.0, "de rebote (sobre MA50, bajo MA200)"
                    else:
                        tendencia, precio_vs = 7.0, "bajista (bajo ambas medias)"
                elif ma50:
                    tendencia = 19.0 if px > ma50 else 10.0
                    precio_vs = "sobre MA50" if px > ma50 else "bajo MA50"
        except Exception:
            pass

        score = round(_clamp(f_contrario + momentum + tendencia, 0, 100), 1)

        nombre = d.get("nombre") or "Este activo"

        # ── Pros y contras: SIEMPRE ≥3 de cada lado ──────────────────────
        pros, cons = [], []
        if fng is not None and fng <= 25:
            pros.append(f"Miedo extremo en el mercado ({fng:.0f}/100): históricamente, la zona donde acumulan los pacientes")
        elif fng is not None and fng <= 45:
            pros.append(f"Sentimiento en zona de miedo ({fng:.0f}/100): las manos débiles ya vendieron buena parte")
        if d30 is not None and d30 >= 5:
            pros.append(f"El precio recupera: {d30:+.1f}% en los últimos 30 días")
        if precio_vs and "alcista" in precio_vs:
            pros.append("Estructura alcista: cotiza sobre sus medias de 50 y 200 sesiones")
        elif precio_vs and "constructiva" in precio_vs:
            pros.append("Sostiene su media de 200 sesiones: la tendencia de fondo sigue viva")
        if d1y is not None and d1y > 0:
            pros.append(f"En el año acumula {d1y:+.1f}%: el ciclo largo aún juega a favor")

        if fng is not None and fng >= 75:
            cons.append(f"Euforia en el mercado ({fng:.0f}/100): históricamente, la zona donde reparten los que llegaron primero")
        if precio_vs and "bajista" in precio_vs:
            cons.append("Cotiza bajo sus medias de 50 y 200 sesiones: la inercia de fondo sigue en contra")
        if d30 is not None and d30 <= -10:
            cons.append(f"El precio sigue cayendo: {d30:.1f}% en 30 días")
        if d1y is not None and d1y <= -25:
            cons.append(f"Un año demoledor ({d1y:+.1f}%): los tenedores atrapados venden en cada rebote")
        cons.append("El sentimiento cripto gira en días: una lectura de hoy no es una tesis, es una foto")
        cons.append(f"{nombre} no escapa al humor del mercado global: si el apetito de riesgo cae, cae con él")

        pros = _completar(pros, _RESERVA_PROS)
        cons = _completar(cons, _RESERVA_CONS)

        # ── Prosa completa (tres bloques) ────────────────────────────────
        p1 = (
            (f"El termómetro de Miedo y Codicia del mercado cripto marca {fng:.0f}/100 "
             f"({d.get('fng_clasificacion') or 'sin clasificar'}). " if fng is not None else
             "El índice de sentimiento del mercado no está disponible en este momento. ")
            + "La lectura útil de este índice es CONTRARIA: el pánico extremo ha coincidido históricamente con suelos "
              "de mercado y la euforia con techos — cuando todos tienen miedo, los vendedores ya vendieron; cuando "
              "todos celebran, los compradores ya compraron. "
            + (("Hoy el péndulo está del lado del miedo, que es la mitad menos peligrosa. " if fng <= 40 else
                "Hoy el péndulo está en zona neutral: el índice no da ventaja a ninguno de los dos lados. " if fng <= 60 else
                "Hoy el péndulo está del lado de la codicia — el momento de máxima cautela, no de máxima emoción. ")
               if fng is not None else "")
        )
        p2 = (
            (f"En precio, {nombre} se mueve {d7:+.1f}% en la semana y {d30:+.1f}% en el mes"
             + (f", con un acumulado de {d1y:+.1f}% en doce meses" if d1y is not None else "") + ". "
             if (d7 is not None and d30 is not None) else "")
            + (f"La estructura técnica de fondo es {precio_vs}" +
               (" — el tipo de configuración donde los rebotes tienden a sostenerse. " if "alcista" in (precio_vs or "") else
                " — los rebotes en esta configuración suelen encontrar vendedores esperando arriba. " if "bajista" in (precio_vs or "") else ". ")
               if precio_vs else "")
        )
        if fng is not None and fng <= 30 and precio_vs and "bajista" in precio_vs:
            p3 = ("El hallazgo: miedo alto con estructura bajista es la combinación clásica de FINAL de ciclo correctivo — "
                  "interesante para construir posición por tramos, nunca de golpe.")
        elif fng is not None and fng >= 70 and precio_vs and "alcista" in precio_vs:
            p3 = ("El hallazgo: euforia con tendencia alcista es donde nacen los techos — si ya se está dentro, es momento "
                  "de proteger beneficios, no de añadir.")
        elif precio_vs and "alcista" in precio_vs:
            p3 = (f"El hallazgo: con el sentimiento aún templado y la estructura a favor, {nombre} tiene el viento del "
                  "mercado de cola; la señal a vigilar es que no pierda su media de 200 sesiones.")
        else:
            p3 = (f"El hallazgo: el sentimiento todavía no da una señal decisiva sobre {nombre} — aquí pesan más la "
                  "tokenomics y la adopción que el humor de la semana.")
        analysis = p1 + "<br><br>" + p2 + "<br><br>" + p3

        return {
            "score": score, "conviction": _conv(score), "analysis": analysis,
            "pros": pros[:3], "cons": cons[:3],
            "key_metrics": {
                "fng_actual": fng, "fng_clasificacion": d.get("fng_clasificacion"),
                "delta_7d_pct": d7, "delta_30d_pct": d30, "delta_1y_pct": d1y,
                "tendencia": precio_vs,
            },
            "sub_scores": {"fng_contrario": round(f_contrario, 1),
                           "momentum": round(momentum, 1),
                           "tendencia": round(tendencia, 1)},
            "key_insight": (f"F&G {fng:.0f} ({d.get('fng_clasificacion')}) · {d30:+.1f}% en 30d"
                            if (fng is not None and d30 is not None) else "Sentimiento parcial"),
            "raw_data": {"fng_serie": d.get("fng_serie")},
        }
    except Exception as e:
        return {"score": 50.0, "conviction": "LOW", "analysis": "", "pros": [],
                "cons": [], "key_metrics": {}, "sub_scores": {}, "key_insight": "",
                "error": str(e)}


# ── 4. RIESGO ──────────────────────────────────────────────────────────────

def score_crypto_riesgo(d: dict, hist=None, hist_btc=None, hist_spy=None) -> dict:
    """Volatilidad (0-35) + drawdown de ciclo (0-30) + correlaciones (0-35).
    Conservador por diseño: esto es la clase de activo más volátil que analiza
    la app y el texto lo dice sin rodeos."""
    try:
        d = d or {}
        es_btc = (d.get("simbolo") == "BTC")
        ath_dist = _f(d.get("ath_distancia_pct"))

        vol_anual = max_dd = corr_btc = corr_spy = None
        try:
            if hist is not None and not hist.empty and len(hist) > 60:
                c = hist["Close"].dropna()
                r = c.pct_change().dropna()
                if len(r) > 40:
                    vol_anual = round(float(r.std()) * math.sqrt(365) * 100.0, 1)
                dd = (c / c.cummax() - 1.0)
                max_dd = round(float(dd.min()) * 100.0, 1)

                def _corr(otro):
                    try:
                        if otro is None or otro.empty:
                            return None
                        ro = otro["Close"].dropna().pct_change().dropna()
                        a = r.iloc[-90:]
                        b = ro.reindex(a.index).dropna()
                        a = a.reindex(b.index)
                        if len(a) < 30:
                            return None
                        return round(float(a.corr(b)), 2)
                    except Exception:
                        return None
                corr_btc = None if es_btc else _corr(hist_btc)
                corr_spy = _corr(hist_spy)
        except Exception:
            pass

        # Volatilidades de referencia (para la comparativa visual): NO puntúan,
        # solo se muestran. Las series ya están descargadas para la correlación.
        def _vol_ref(otro, dias_anno):
            try:
                if otro is None or otro.empty:
                    return None
                rr = otro["Close"].dropna().pct_change().dropna()
                if len(rr) < 40:
                    return None
                return round(float(rr.std()) * math.sqrt(dias_anno) * 100.0, 1)
            except Exception:
                return None
        vol_btc = vol_anual if es_btc else _vol_ref(hist_btc, 365)
        vol_spy = _vol_ref(hist_spy, 252)

        vol_s = _pond([(vol_anual, 160.0, 35.0, 5, 31)], 5, 35)
        dd_s = _pond([(ath_dist if ath_dist is not None else max_dd, -95.0, -10.0, 4, 27)], 4, 30)
        if es_btc:
            corr_s = _pond([(abs(corr_spy) if corr_spy is not None else None, 0.9, 0.1, 8, 30)], 8, 35)
        else:
            corr_s = _pond([(corr_btc, 0.98, 0.45, 8, 30)], 8, 35)
        score = round(_clamp(vol_s + dd_s + corr_s, 0, 100), 1)

        nombre = d.get("nombre") or "Este activo"

        # ── Pros y contras: SIEMPRE ≥3 de cada lado ──────────────────────
        pros, cons = [], []
        if vol_anual is not None and vol_anual <= 55:
            pros.append(f"Volatilidad contenida para ser cripto ({vol_anual:.0f}% anualizada, la más baja de su clase)")
        elif vol_anual is not None and vol_anual <= 75:
            pros.append(f"Volatilidad del {vol_anual:.0f}%: alta frente a la bolsa, pero en la media de los grandes del sector")
        if corr_btc is not None and corr_btc <= 0.6:
            pros.append(f"Correlación moderada con Bitcoin ({corr_btc:.2f}): conserva dinámica propia dentro del sector")
        if es_btc and corr_spy is not None and abs(corr_spy) <= 0.35:
            pros.append(f"Correlación baja con la bolsa ({corr_spy:+.2f}): añade diversificación real a una cartera de acciones")
        elif corr_spy is not None and abs(corr_spy) <= 0.30:
            pros.append(f"Se mueve casi al margen de la bolsa (correlación {corr_spy:+.2f}) — su ciclo es propio")
        if ath_dist is not None and -70 <= ath_dist <= -40:
            pros.append(f"Buena parte del riesgo ya se materializó: cotiza {abs(ath_dist):.0f}% bajo máximos")
        pros.append("La caída máxima tolerable se puede fijar de antemano: aquí el riesgo se gestiona con el tamaño de la posición")

        if vol_anual is not None and vol_anual >= 80:
            cons.append(f"Volatilidad del {vol_anual:.0f}% anualizada: movimientos de ±10% en un día son lo normal, no la excepción")
        elif vol_anual is not None:
            cons.append(f"Volatilidad del {vol_anual:.0f}% anualizada: el doble o triple que una acción típica del S&P 500")
        if ath_dist is not None and ath_dist <= -60:
            cons.append(f"El ciclo actual ya borró un {abs(ath_dist):.0f}% desde máximos históricos — y nada garantiza que sea el suelo")
        if corr_btc is not None and corr_btc >= 0.8:
            cons.append(f"Correlación {corr_btc:.2f} con Bitcoin: cuando BTC cae, esto cae — no diversifica, amplifica")
        cons.append("Sin flujos de caja que pongan un suelo fundamental: no hay 'barato por beneficios' que frene una caída")
        cons.append("Riesgo regulatorio permanente: una decisión de un regulador puede mover el precio de golpe, en cualquier dirección")

        pros = _completar(pros, _RESERVA_PROS)
        cons = _completar(cons, _RESERVA_CONS)

        # ── Prosa completa (tres bloques) ────────────────────────────────
        p1 = (
            (f"{nombre} se mueve con una volatilidad anualizada del {vol_anual:.0f}%" +
             (f" — como referencia, una acción típica del S&P 500 ronda el 25-35%, así que aquí cada posición pesa "
              f"{'triple' if vol_anual >= 90 else 'el doble'} en riesgo real" if vol_anual >= 55 else
              ", notablemente contenida para su clase de activo") + ". "
             if vol_anual is not None else
             f"La volatilidad de {nombre} no se pudo calcular en este momento. ")
            + (f"Su peor caída dentro del período analizado fue del {abs(max_dd):.0f}%" if max_dd is not None else "")
            + (f", y respecto a su máximo histórico cotiza un {abs(ath_dist):.0f}% por debajo. " if ath_dist is not None else ". ")
        )
        p2 = (
            ((f"La correlación de 90 días con Bitcoin es {corr_btc:.2f}: " +
              ("en la práctica se mueve como una versión apalancada de BTC — quien ya tiene Bitcoin no está diversificando "
               "al añadir esto, está doblando la misma apuesta. " if corr_btc >= 0.8 else
               "conserva una dinámica parcialmente propia, algo poco común en este mercado. "))
             if corr_btc is not None else "")
            + ((f"Frente a la bolsa (S&P 500) la correlación es {corr_spy:+.2f}" +
                (" — lo bastante baja para aportar diversificación real a una cartera de acciones. " if abs(corr_spy) <= 0.35 else
                 " — se mueve más pegado a la bolsa de lo que su narrativa sugiere. "))
               if corr_spy is not None else "")
        )
        p3 = ("El hallazgo: " +
              (f"el riesgo de {nombre} no está en si caerá un 30% en algún momento — con esta volatilidad, LO HARÁ — sino en "
               "que el tamaño de la posición permita aguantarlo sin vender en el peor momento. La posición correcta es la que "
               "deja dormir tranquilo en un −40%."))
        analysis = p1 + "<br><br>" + p2 + "<br><br>" + p3

        return {
            "score": score, "conviction": _conv(score), "analysis": analysis,
            "pros": pros[:3], "cons": cons[:3],
            "key_metrics": {
                "vol_anual_pct": vol_anual, "max_drawdown_pct": max_dd,
                "ath_distancia_pct": ath_dist,
                "correlacion_btc_90d": corr_btc, "correlacion_spy_90d": corr_spy,
                "vol_btc_pct": vol_btc, "vol_spy_pct": vol_spy,
            },
            "sub_scores": {"volatilidad": round(vol_s, 1), "drawdown": round(dd_s, 1),
                           "correlacion": round(corr_s, 1)},
            "key_insight": (f"Vol {vol_anual:.0f}% anualizada · a {abs(ath_dist):.0f}% de máximos"
                            if (vol_anual is not None and ath_dist is not None)
                            else "Riesgo con datos parciales"),
        }
    except Exception as e:
        return {"score": 50.0, "conviction": "LOW", "analysis": "", "pros": [],
                "cons": [], "key_metrics": {}, "sub_scores": {}, "key_insight": "",
                "error": str(e)}
