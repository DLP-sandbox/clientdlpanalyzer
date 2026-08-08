"""
Capa de datos de ETFs — SOLO domiciliados en EE.UU. (decisión de producto).

POR QUÉ SOLO EE.UU.: en Render (cloud) yfinance está bloqueado por IP de
datacenter y el único fallback real con datos de ETF es la API de Nasdaq, que
solo cubre EE.UU. Un ETF europeo (UCITS) quedaría a medias en producción; mejor
detectarlo y decirlo con un cartel que ofrecer un análisis incompleto.

Fuentes (verificadas en vivo el 6-ago-2026):
  · yfinance `info`      → categoría, gestora, AUM, NAV, yield, beta 3a,
                           retornos medios 3/5a, TER (netExpenseRatio), edad.
  · yfinance `funds_data`→ top-10 holdings con pesos, sectores, TER de
                           operaciones + MEDIA DE SU CATEGORÍA.
  · Nasdaq API           → fallback cloud: volumen medio 20/65d, beta, 52w.

Blindaje: todas las funciones NUNCA lanzan; los huecos quedan como None (jamás
un 0 falso) y el scoring los saca de la ecuación con _pond.
"""
import re
from datetime import datetime
from typing import Optional

import requests

from data.market_data import _load_cache, _save_cache, _yt

TTL_ETF = 4.0        # 4 h — mismo TTL que la info corporativa de acciones

# Sufijos de Yahoo que delatan una cotización FUERA de EE.UU. (LSE, Xetra,
# Ámsterdam, París, Milán, Suiza, Madrid…). Un ETF US nunca lleva sufijo.
_SUFIJOS_NO_US = (".L", ".DE", ".AS", ".PA", ".MI", ".SW", ".MC", ".BR",
                  ".LS", ".VI", ".ST", ".OL", ".CO", ".HE", ".IR", ".F",
                  ".SG", ".BE", ".DU", ".HM", ".HA", ".MU", ".TO", ".V",
                  ".AX", ".HK", ".T", ".SA", ".MX")

_UA_NASDAQ = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


# ── Universo curado de ETFs UCITS (domiciliados en Europa) ─────────────────
# POR QUÉ CURADO: no existe fuente gratuita y robusta en cloud para el listado
# europeo (verificado: TradingView no indexa ETFs de LSE/Xetra, Stooq bloquea,
# Nasdaq solo cubre EE.UU.). El diseño aprobado: los datos ESTABLES del fondo
# (TER, gestora, índice, divisa, distribución) van en esta ficha verificada a
# mano contra los listados reales (20/20 comprobados en vivo el 7-ago-2026), y
# la composición/comportamiento se analizan a través del ÍNDICE que replica,
# vía su gemelo americano — señalizado con claridad en la UI.
# TERs: los que yfinance confirma en vivo se usan tal cual (CSPX 0.07,
# IWDA/SWDA 0.20, EQQQ/CNDX 0.30, ISAC 0.20, XDWD 0.12); los de Vanguard no
# vienen en la fuente y se fijan a su valor oficial publicado (0.07 / 0.22).
UCITS_UNIVERSO = {
    "CSPX": {"lanzado": 2010, "nombre": "iShares Core S&P 500 UCITS ETF (Acc)",
             "gestora": "iShares (BlackRock) · Irlanda", "ter_pct": 0.07,
             "indice": "S&P 500", "gemelo_us": "SPY", "distribucion": "acumulacion",
             "tickers": {"CSPX.L": "USD", "CSPX.AS": "EUR", "SXR8.DE": "EUR"},
             "alias": ["SXR8"]},
    "VUAA": {"lanzado": 2019, "nombre": "Vanguard S&P 500 UCITS ETF (Acc)",
             "gestora": "Vanguard · Irlanda", "ter_pct": 0.07,
             "indice": "S&P 500", "gemelo_us": "SPY", "distribucion": "acumulacion",
             "tickers": {"VUAA.L": "USD", "VUAA.DE": "EUR"}, "alias": []},
    "VUSA": {"lanzado": 2012, "nombre": "Vanguard S&P 500 UCITS ETF (Dist)",
             "gestora": "Vanguard · Irlanda", "ter_pct": 0.07,
             "indice": "S&P 500", "gemelo_us": "SPY", "distribucion": "distribucion",
             "tickers": {"VUSA.L": "GBP", "VUSA.AS": "EUR"}, "alias": []},
    "VWCE": {"lanzado": 2019, "nombre": "Vanguard FTSE All-World UCITS ETF (Acc)",
             "gestora": "Vanguard · Irlanda", "ter_pct": 0.22,
             "indice": "FTSE All-World", "gemelo_us": "VT", "distribucion": "acumulacion",
             "tickers": {"VWCE.DE": "EUR", "VWCE.MI": "EUR"}, "alias": []},
    "VWRL": {"lanzado": 2012, "nombre": "Vanguard FTSE All-World UCITS ETF (Dist)",
             "gestora": "Vanguard · Irlanda", "ter_pct": 0.22,
             "indice": "FTSE All-World", "gemelo_us": "VT", "distribucion": "distribucion",
             "tickers": {"VWRL.L": "GBP", "VWRL.AS": "EUR"}, "alias": []},
    "IWDA": {"lanzado": 2009, "nombre": "iShares Core MSCI World UCITS ETF (Acc)",
             "gestora": "iShares (BlackRock) · Irlanda", "ter_pct": 0.20,
             "indice": "MSCI World", "gemelo_us": "URTH", "distribucion": "acumulacion",
             "tickers": {"IWDA.L": "USD", "IWDA.AS": "EUR", "SWDA.L": "GBp"},
             "alias": ["SWDA"]},
    "XDWD": {"lanzado": 2014, "nombre": "Xtrackers MSCI World UCITS ETF 1C (Acc)",
             "gestora": "Xtrackers (DWS) · Irlanda", "ter_pct": 0.12,
             "indice": "MSCI World", "gemelo_us": "URTH", "distribucion": "acumulacion",
             "tickers": {"XDWD.L": "USD", "XDWD.DE": "EUR"}, "alias": []},
    "EQQQ": {"lanzado": 2002, "nombre": "Invesco EQQQ Nasdaq-100 UCITS ETF (Dist)",
             "gestora": "Invesco · Irlanda", "ter_pct": 0.30,
             "indice": "Nasdaq-100", "gemelo_us": "QQQ", "distribucion": "distribucion",
             "tickers": {"EQQQ.L": "GBp", "EQQQ.PA": "EUR"}, "alias": []},
    "CNDX": {"lanzado": 2010, "nombre": "iShares Nasdaq 100 UCITS ETF (Acc)",
             "gestora": "iShares (BlackRock) · Irlanda", "ter_pct": 0.30,
             "indice": "Nasdaq-100", "gemelo_us": "QQQ", "distribucion": "acumulacion",
             "tickers": {"CNDX.L": "USD"}, "alias": []},
    "ISAC": {"lanzado": 2011, "nombre": "iShares MSCI ACWI UCITS ETF (Acc)",
             "gestora": "iShares (BlackRock) · Irlanda", "ter_pct": 0.20,
             "indice": "MSCI ACWI (mundo completo)", "gemelo_us": "ACWI",
             "distribucion": "acumulacion",
             "tickers": {"ISAC.L": "USD"}, "alias": []},
}


def resolver_ucits(entrada: str):
    """'CSPX' / 'cspx.l' / 'SXR8.DE' → ('CSPX', 'CSPX.L', 'USD') — la clave del
    fondo, el listado concreto y su divisa de cotización. None si no está en el
    universo. Sin red. NUNCA lanza."""
    try:
        e = (entrada or "").strip().upper()
        if not e:
            return None
        for clave, meta in UCITS_UNIVERSO.items():
            for tk, div in meta["tickers"].items():
                if e == tk.upper():
                    return (clave, tk, div)
            base = e.split(".")[0]
            if base == clave or base in [a.upper() for a in meta["alias"]]:
                # Sin sufijo (o sufijo no listado): se usa el listado principal.
                tk0 = next(iter(meta["tickers"]))
                return (clave, tk0, meta["tickers"][tk0])
        return None
    except Exception:
        return None


def es_ticker_no_us(ticker: str) -> bool:
    """True si el ticker cotiza fuera de EE.UU. (sufijo de bolsa extranjera).
    NUNCA lanza."""
    try:
        t = (ticker or "").strip().upper()
        return any(t.endswith(s) for s in _SUFIJOS_NO_US)
    except Exception:
        return False


def _num(v) -> Optional[float]:
    """float o None. Acepta '1,234.5', '$12.3', '0.06%'. NUNCA lanza."""
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            f = float(v)
            return f if f == f else None          # NaN → None
        s = re.sub(r"[^0-9.\-]", "", str(v))
        return float(s) if s not in ("", "-", ".", "-.") else None
    except Exception:
        return None


def _ter_plausible(ter_pct) -> bool:
    """Un TER real está entre 0.01% y 3%. El 0.00 exacto es un dato corrupto
    (medido: VWCE.DE reporta 0.00 y su TER real es 0.22%)."""
    try:
        return ter_pct is not None and 0.005 <= float(ter_pct) <= 3.0
    except Exception:
        return False


def _nasdaq_etf_summary(ticker: str) -> dict:
    """Fallback cloud (solo ETFs US): volumen medio, beta, 52w, prev close.
    NUNCA lanza."""
    out = {}
    try:
        r = requests.get(
            f"https://api.nasdaq.com/api/quote/{ticker}/summary?assetclass=etf",
            headers=_UA_NASDAQ, timeout=15)
        if r.status_code != 200:
            return out
        sd = (((r.json() or {}).get("data") or {}).get("summaryData") or {})
        def _v(k):
            x = sd.get(k)
            return _num(x.get("value")) if isinstance(x, dict) else None
        out = {
            "volumen_medio": _v("FiftyDayAvgDailyVol") or _v("AvgDailyVol20Days"),
            "beta": _v("Beta"),
            "precio_previo": _v("PreviousClose"),
        }
        hl = sd.get("FiftTwoWeekHighLow")
        if isinstance(hl, dict) and hl.get("value"):
            partes = str(hl["value"]).split("/")
            if len(partes) == 2:
                out["max_52w"] = _num(partes[0])
                out["min_52w"] = _num(partes[1])
        return {k: v for k, v in out.items() if v is not None}
    except Exception:
        return out


def get_etf_data(ticker: str) -> dict:
    """TODOS los datos de un ETF US, consolidados, cacheados y blindados.

    Claves (None cuando la fuente no lo da — jamás 0 falso):
      nombre, categoria, gestora, aum, nav, precio, prima_descuento_pct,
      ter_pct, ter_categoria_pct, yield_pct, beta_3y, retorno_3y_pct,
      retorno_5y_pct, edad_anios, divisa, volumen_medio, top_holdings
      [(simbolo, nombre, peso_pct)], concentracion_top10_pct, sectores
      {nombre: peso_pct}, no_us (bool), fuente.
    NUNCA lanza."""
    t = (ticker or "").strip().upper()
    if not t:
        return {}

    # ── ETF UCITS del universo curado: analizable ─────────────────────────
    # Los datos del FONDO salen de la ficha verificada (estables, cloud-proof)
    # y la composición/rendimiento del ÍNDICE que replica, vía su gemelo
    # americano — la UI lo señaliza. AUM/prima se intentan del listado real
    # (funciona en local; en cloud quedan None y _pond los excluye).
    _u = resolver_ucits(t)
    if _u:
        clave, listado, divisa_listado = _u
        ficha = UCITS_UNIVERSO[clave]
        # Cache POR LISTADO: CSPX.L y SXR8.DE son el mismo fondo pero el
        # aviso de la UI muestra el listado concreto que el usuario pidió.
        key = f"etf_ucits_{clave}_{listado.replace('.', '_')}"
        cached = _load_cache(key, ttl_hours=TTL_ETF)
        if cached:
            return cached
        out = dict(get_etf_data(ficha["gemelo_us"]) or {})   # base: el gemelo
        gemelo_nombre = out.get("nombre")
        out.update({
            "ticker": clave, "no_us": False, "ucits": True,
            "listado": listado, "divisa_listado": divisa_listado,
            "gemelo_us": ficha["gemelo_us"], "gemelo_nombre": gemelo_nombre,
            "indice": ficha["indice"],
            "nombre": ficha["nombre"],
            "gestora": ficha["gestora"],
            "ter_pct": ficha["ter_pct"],          # TER OFICIAL del UCITS
            "categoria": ficha["indice"],
            "distribucion": ficha["distribucion"],
            "divisa": divisa_listado,
            # Edad desde la ficha: la fecha del listado en yfinance mezcla
            # clases de acciones (a VWCE le daba la de VWRL, 2012 vs 2019).
            "edad_anios": float(datetime.now().year - ficha["lanzado"]),
            # Datos del listado europeo: solo si la fuente responde (local).
            "aum": None, "nav": None, "precio": None,
            "prima_descuento_pct": None, "yield_pct": None,
        })
        try:
            info = _yt(listado).info or {}
            out["aum"] = _num(info.get("totalAssets"))
            out["nav"] = _num(info.get("navPrice"))
            out["precio"] = (_num(info.get("regularMarketPrice"))
                             or _num(info.get("previousClose")))
            y = _num(info.get("yield"))
            out["yield_pct"] = round(y * 100.0, 2) if y is not None and y < 1 else y
            # Prima/descuento NO se calcula en UCITS: yfinance reporta el NAV
            # en la divisa BASE del fondo (USD) y el precio en la del listado
            # (EUR/GBp) → daba desviaciones fantasma de ~-13% (el FX, no una
            # prima real). Sin conversión fiable, el dato honesto es "—" y los
            # motores ya lo excluyen de la ecuación.
        except Exception:
            pass
        if out.get("top_holdings") or out.get("nombre"):
            _save_cache(key, out)
        return out

    if es_ticker_no_us(t):
        return {"ticker": t, "no_us": True}

    key = f"etf_{t}"
    cached = _load_cache(key, ttl_hours=TTL_ETF)
    if cached:
        return cached

    out = {"ticker": t, "no_us": False, "fuente": []}
    try:
        # ── 1. yfinance info ─────────────────────────────────────────────
        info = {}
        try:
            info = _yt(t).info or {}
        except Exception:
            info = {}
        if info:
            out["fuente"].append("yfinance")
            out["nombre"] = info.get("longName") or info.get("shortName")
            out["categoria"] = info.get("category")
            out["gestora"] = info.get("fundFamily")
            out["aum"] = _num(info.get("totalAssets"))
            out["nav"] = _num(info.get("navPrice"))
            out["precio"] = _num(info.get("regularMarketPrice")) or _num(info.get("previousClose"))
            y = _num(info.get("yield"))
            out["yield_pct"] = round(y * 100.0, 2) if y is not None and y < 1 else y
            out["beta_3y"] = _num(info.get("beta3Year"))
            r3 = _num(info.get("threeYearAverageReturn"))
            r5 = _num(info.get("fiveYearAverageReturn"))
            out["retorno_3y_pct"] = round(r3 * 100.0, 2) if r3 is not None else None
            out["retorno_5y_pct"] = round(r5 * 100.0, 2) if r5 is not None else None
            out["divisa"] = info.get("currency")
            out["volumen_medio"] = _num(info.get("averageVolume"))
            # TER: netExpenseRatio ya viene en % (SPY → 0.0945 = 0.0945%)
            ter = _num(info.get("netExpenseRatio"))
            out["ter_pct"] = ter if _ter_plausible(ter) else None
            try:
                fi = info.get("fundInceptionDate")
                if fi:
                    edad = (datetime.now() - datetime.fromtimestamp(int(fi))).days / 365.25
                    out["edad_anios"] = round(edad, 1)
            except Exception:
                pass

        # ── 2. funds_data: composición + TER de operaciones ──────────────
        try:
            fd = _yt(t).funds_data
            th = fd.top_holdings
            if th is not None and not th.empty:
                filas = []
                for sim, fila in th.iterrows():
                    peso = _num(fila.get("Holding Percent"))
                    filas.append((str(sim), str(fila.get("Name") or sim),
                                  round(peso * 100.0, 2) if peso is not None else None))
                out["top_holdings"] = filas
                pesos = [p for _, _, p in filas if p is not None]
                out["concentracion_top10_pct"] = round(sum(pesos), 1) if pesos else None
            sw = fd.sector_weightings
            if sw:
                out["sectores"] = {k: round(float(v) * 100.0, 2) for k, v in sw.items()
                                   if v is not None}
            try:
                fo = fd.fund_operations
                if fo is not None and not fo.empty and "Annual Report Expense Ratio" in fo.index:
                    ter_op = _num(fo.loc["Annual Report Expense Ratio"].iloc[0])
                    ter_op = round(ter_op * 100.0, 4) if ter_op is not None else None
                    if out.get("ter_pct") is None and _ter_plausible(ter_op):
                        out["ter_pct"] = ter_op
                    if fo.shape[1] > 1:
                        cat = _num(fo.loc["Annual Report Expense Ratio"].iloc[1])
                        cat = round(cat * 100.0, 4) if cat is not None else None
                        if _ter_plausible(cat):
                            out["ter_categoria_pct"] = cat
            except Exception:
                pass
            if out.get("top_holdings") or out.get("sectores"):
                out["fuente"].append("funds_data")
        except Exception:
            pass

        # ── 3. Fallback Nasdaq (cloud): solo rellena lo que falte ────────
        if out.get("precio") is None or out.get("volumen_medio") is None or out.get("beta_3y") is None:
            nd = _nasdaq_etf_summary(t)
            if nd:
                out["fuente"].append("nasdaq")
                if out.get("volumen_medio") is None:
                    out["volumen_medio"] = nd.get("volumen_medio")
                if out.get("beta_3y") is None:
                    out["beta_3y"] = nd.get("beta")
                if out.get("precio") is None:
                    out["precio"] = nd.get("precio_previo")
                out.setdefault("max_52w", nd.get("max_52w"))
                out.setdefault("min_52w", nd.get("min_52w"))

        # ── 4. Derivados ─────────────────────────────────────────────────
        try:
            p, nav = out.get("precio"), out.get("nav")
            out["prima_descuento_pct"] = (round((p / nav - 1.0) * 100.0, 2)
                                          if (p and nav) else None)
        except Exception:
            out["prima_descuento_pct"] = None
        # Acumulación vs distribución (heurística honesta)
        try:
            nombre = (out.get("nombre") or "").lower()
            if "acc" in nombre.split() or "(acc)" in nombre or "accumulat" in nombre:
                out["distribucion"] = "acumulacion"
            elif out.get("yield_pct") and out["yield_pct"] > 0.15:
                out["distribucion"] = "distribucion"
            else:
                out["distribucion"] = None
        except Exception:
            out["distribucion"] = None

        # Solo cachear si trajo algo sustancial (mismo criterio que holders)
        if out.get("nombre") or out.get("top_holdings"):
            _save_cache(key, out)
        return out
    except Exception:
        return out
