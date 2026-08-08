"""
Capa de datos de CRIPTOMONEDAS — universo curado de majors.

POR QUÉ ASÍ
-----------
Verificado el 6-ago-2026 contra cada fuente en vivo:
  · CoinGecko free (sin key) da TODO el universo en UNA llamada: precio, mcap,
    rank, volumen, supply circulante/máximo, ATH y distancia, Δ7d/30d/1y.
    Funciona desde IPs de datacenter (Render) — al contrario que yfinance.
  · alternative.me da el Fear & Greed con serie histórica.
  · DefiLlama da el TVL por cadena y su serie histórica, sin key.
  · El screener de TradingView para crypto está MUERTO (0 filas con
    set_markets('crypto'), 'coin' y /coin/scan) — no se usa.
Cero dependencias nuevas: todo por `requests` (ya presente). Crítico para los
512 MB de Render.

El universo es CURADO (decisión del usuario): ~15 majors verificables a mano.
Así una sola llamada cubre todo, el rate limit free de CoinGecko jamás se toca
(caché 30-60 min) y el blindaje anti-datos-corruptos es total.

Todas las funciones públicas están blindadas: NUNCA lanzan.
"""
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

# Caché en disco compartida con el resto de la app (mismo .cache/, mismos TTL).
from data.market_data import _load_cache, _save_cache

_TIMEOUT = 20
_UA = {"User-Agent": "DLP Market Analyzer contacto@dlp-analyzer.app",
       "Accept": "application/json"}

TTL_CG_MARKETS = 0.5      # 30 min — precios/mcap del universo entero
TTL_CG_GLOBAL = 1.0       # 1 h — dominancia
TTL_FNG = 1.0             # 1 h — Fear & Greed
TTL_TVL = 6.0             # 6 h — TVL por cadena (se mueve despacio)
TTL_CG_HISTORY = 6.0      # 6 h — histórico de respaldo (solo cloud)

# ── Universo curado ────────────────────────────────────────────────────────
# `yahoo`: ticker en Yahoo Finance (histórico OHLC). OJO: Yahoo usa sufijos
# raros en algunas (Toncoin = TON11419-USD); por eso se declara explícito.
# `cadena_defi`: nombre EXACTO en DefiLlama (/v2/chains). None = el TVL no es
# la métrica de esa moneda (se trata como "no aplica", igual que en bancos).
# `clase`: gobierna qué métricas aplican en el scoring de adopción.
CRYPTO_UNIVERSO = {
    "BTC":  {"id": "bitcoin",      "nombre": "Bitcoin",   "yahoo": "BTC-USD",
             "alias": ["BITCOIN"], "cadena_defi": "Bitcoin", "clase": "reserva"},
    "ETH":  {"id": "ethereum",     "nombre": "Ethereum",  "yahoo": "ETH-USD",
             "alias": ["ETHEREUM", "ETHER"], "cadena_defi": "Ethereum", "clase": "contratos"},
    "SOL":  {"id": "solana",       "nombre": "Solana",    "yahoo": "SOL-USD",
             "alias": ["SOLANA"], "cadena_defi": "Solana", "clase": "contratos"},
    "XRP":  {"id": "ripple",       "nombre": "XRP",       "yahoo": "XRP-USD",
             "alias": ["RIPPLE"], "cadena_defi": None, "clase": "pagos"},
    "BNB":  {"id": "binancecoin",  "nombre": "BNB",       "yahoo": "BNB-USD",
             "alias": ["BINANCE"], "cadena_defi": "BSC", "clase": "contratos"},
    "ADA":  {"id": "cardano",      "nombre": "Cardano",   "yahoo": "ADA-USD",
             "alias": ["CARDANO"], "cadena_defi": "Cardano", "clase": "contratos"},
    "DOGE": {"id": "dogecoin",     "nombre": "Dogecoin",  "yahoo": "DOGE-USD",
             "alias": ["DOGECOIN"], "cadena_defi": None, "clase": "meme"},
    "AVAX": {"id": "avalanche-2",  "nombre": "Avalanche", "yahoo": "AVAX-USD",
             "alias": ["AVALANCHE"], "cadena_defi": "Avalanche", "clase": "contratos"},
    "LINK": {"id": "chainlink",    "nombre": "Chainlink", "yahoo": "LINK-USD",
             "alias": ["CHAINLINK"], "cadena_defi": None, "clase": "infraestructura"},
    "DOT":  {"id": "polkadot",     "nombre": "Polkadot",  "yahoo": "DOT-USD",
             "alias": ["POLKADOT"], "cadena_defi": "Polkadot", "clase": "contratos"},
    "LTC":  {"id": "litecoin",     "nombre": "Litecoin",  "yahoo": "LTC-USD",
             "alias": ["LITECOIN"], "cadena_defi": None, "clase": "pagos"},
    "TRX":  {"id": "tron",         "nombre": "TRON",      "yahoo": "TRX-USD",
             "alias": ["TRON"], "cadena_defi": "Tron", "clase": "contratos"},
    # Yahoo cubre Toncoin fatal (2 velas en 1 año, medido): su histórico y
    # técnico se sostienen por el fallback historial_coingecko().
    "TON":  {"id": "the-open-network", "nombre": "Toncoin", "yahoo": "TON11419-USD",
             "alias": ["TONCOIN"], "cadena_defi": "TON", "clase": "contratos"},
    # OJO: en Yahoo, "POL-USD" es OTRO token ("Proof Of Liquidity"). El Polygon
    # real (prev. MATIC) es POL28321-USD — verificado el 6-ago-2026.
    "POL":  {"id": "polygon-ecosystem-token", "nombre": "Polygon", "yahoo": "POL28321-USD",
             "alias": ["POLYGON", "MATIC"], "cadena_defi": "Polygon", "clase": "contratos"},
    "SHIB": {"id": "shiba-inu",    "nombre": "Shiba Inu", "yahoo": "SHIB-USD",
             "alias": ["SHIBA", "SHIBAINU"], "cadena_defi": None, "clase": "meme"},
}


def resolver_cripto(entrada: str) -> Optional[str]:
    """'BTC' / 'btc-usd' / 'BITCOIN' → símbolo canónico 'BTC'. None si no está
    en el universo. NUNCA lanza."""
    try:
        e = (entrada or "").strip().upper().replace(" ", "")
        if not e:
            return None
        if e.endswith("-USD"):
            e = e[:-4]
        if e in CRYPTO_UNIVERSO:
            return e
        for sim, meta in CRYPTO_UNIVERSO.items():
            if e == meta["yahoo"].upper().replace("-USD", "") or e in meta["alias"]:
                return sim
        return None
    except Exception:
        return None


def _get_json(url: str, params: dict = None, timeout: int = _TIMEOUT):
    r = requests.get(url, params=params or {}, headers=_UA, timeout=timeout)
    if r.status_code != 200:
        return None
    return r.json()


# ── CoinGecko: mercado del universo entero en 1 llamada ────────────────────

def _cg_markets() -> dict:
    """{coingecko_id: fila} para TODO el universo. Cacheado 30 min."""
    cached = _load_cache("cg_markets", ttl_hours=TTL_CG_MARKETS)
    if cached:
        return cached
    try:
        ids = ",".join(m["id"] for m in CRYPTO_UNIVERSO.values())
        data = _get_json("https://api.coingecko.com/api/v3/coins/markets",
                         {"vs_currency": "usd", "ids": ids,
                          "price_change_percentage": "7d,30d,1y"})
        if not data:
            return {}
        out = {c["id"]: c for c in data if isinstance(c, dict) and c.get("id")}
        if out:
            _save_cache("cg_markets", out)
        return out
    except Exception:
        return {}


def _cg_global() -> dict:
    """Dominancia BTC/ETH y mcap total. Cacheado 1 h."""
    cached = _load_cache("cg_global", ttl_hours=TTL_CG_GLOBAL)
    if cached:
        return cached
    try:
        d = (_get_json("https://api.coingecko.com/api/v3/global") or {}).get("data") or {}
        mp = d.get("market_cap_percentage") or {}
        out = {
            "dominancia_btc": mp.get("btc"),
            "dominancia_eth": mp.get("eth"),
            "mcap_total_usd": (d.get("total_market_cap") or {}).get("usd"),
            "mcap_cambio_24h_pct": d.get("market_cap_change_percentage_24h_usd"),
        }
        if out.get("mcap_total_usd"):
            _save_cache("cg_global", out)
        return out
    except Exception:
        return {}


def get_fear_greed(limite: int = 30) -> dict:
    """{'actual': int, 'clasificacion': str, 'serie': [ints recientes→antiguos]}.
    Fuente: alternative.me. Cacheado 1 h. NUNCA lanza."""
    cached = _load_cache("fng", ttl_hours=TTL_FNG)
    if cached:
        return cached
    try:
        d = (_get_json("https://api.alternative.me/fng/", {"limit": limite}) or {}).get("data") or []
        serie = []
        for x in d:
            try:
                serie.append(int(x.get("value")))
            except (TypeError, ValueError):
                continue
        if not serie:
            return {}
        out = {"actual": serie[0],
               "clasificacion": str(d[0].get("value_classification") or ""),
               "serie": serie}
        _save_cache("fng", out)
        return out
    except Exception:
        return {}


# ── DefiLlama: TVL por cadena + tendencia ──────────────────────────────────

def _tvl_cadenas() -> dict:
    """{nombre_cadena: tvl_usd}. Cacheado 6 h."""
    cached = _load_cache("llama_chains", ttl_hours=TTL_TVL)
    if cached:
        return cached
    try:
        d = _get_json("https://api.llama.fi/v2/chains") or []
        out = {str(c.get("name")): c.get("tvl") for c in d
               if isinstance(c, dict) and c.get("name") and c.get("tvl") is not None}
        if out:
            _save_cache("llama_chains", out)
        return out
    except Exception:
        return {}


def _tvl_hist(cadena: str) -> dict:
    """Serie histórica de TVL de la cadena (12 meses, diaria) + Δ30d.
    {'delta_30d_pct': float, 'serie': [[ts_unix, tvl], …]}. Cacheado 6 h.
    NUNCA lanza."""
    if not cadena:
        return {}
    key = f"llama_hist_{cadena.replace(' ', '_')}"
    cached = _load_cache(key, ttl_hours=TTL_TVL)
    # Los cachés escritos antes de guardar la serie solo traen el delta: se
    # aceptan (el delta sigue siendo válido) y la serie llegará al expirar.
    if cached and cached.get("delta_30d_pct") is not None:
        return cached
    try:
        d = _get_json(f"https://api.llama.fi/v2/historicalChainTvl/{cadena}") or []
        if len(d) < 25:
            return {}
        hoy = d[-1].get("tvl")
        hace30 = d[-31].get("tvl") if len(d) >= 31 else d[0].get("tvl")
        if not hoy or not hace30:
            return {}
        out = {
            "delta_30d_pct": round((hoy / hace30 - 1.0) * 100.0, 2),
            # Últimos ~365 puntos, compactos: [ts, tvl]. ~15 KB por cadena.
            "serie": [[p.get("date"), p.get("tvl")] for p in d[-365:]
                      if p.get("date") and p.get("tvl") is not None],
        }
        _save_cache(key, out)
        return out
    except Exception:
        return {}


def _tvl_tendencia_30d(cadena: str) -> Optional[float]:
    """Δ% del TVL de la cadena en ~30 días. None si no se puede."""
    return (_tvl_hist(cadena) or {}).get("delta_30d_pct")


def get_tvl_serie(cadena: str) -> list:
    """Serie [[ts_unix, tvl], …] de ~12 meses para graficar. [] si no hay.
    NUNCA lanza."""
    try:
        return (_tvl_hist(cadena) or {}).get("serie") or []
    except Exception:
        return []


# ── Histórico de respaldo (cloud): CoinGecko market_chart ──────────────────

def historial_coingecko(simbolo: str, dias: int = 365) -> pd.DataFrame:
    """OHLC aproximado desde CoinGecko cuando yfinance está bloqueado (Render).
    Solo trae cierres diarios → High/Low/Open = Close (suficiente para RSI,
    MACD, medias y tendencia; ATR queda degradado y es preferible a nada).
    Cacheado 6 h. NUNCA lanza — devuelve DataFrame vacío si falla."""
    try:
        sim = resolver_cripto(simbolo)
        if not sim:
            return pd.DataFrame()
        cid = CRYPTO_UNIVERSO[sim]["id"]
        key = f"cg_hist_{sim}"
        cached = _load_cache(key, ttl_hours=TTL_CG_HISTORY)
        datos = None
        if cached and cached.get("prices"):
            datos = cached["prices"]
        else:
            d = _get_json(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart",
                          {"vs_currency": "usd", "days": dias, "interval": "daily"})
            datos = (d or {}).get("prices") or []
            if datos:
                _save_cache(key, {"prices": datos})
        if not datos:
            return pd.DataFrame()
        fechas = [datetime.utcfromtimestamp(p[0] / 1000.0) for p in datos]
        cierres = [float(p[1]) for p in datos]
        df = pd.DataFrame({"Open": cierres, "High": cierres, "Low": cierres,
                           "Close": cierres, "Volume": [0.0] * len(cierres)},
                          index=pd.DatetimeIndex(fechas).normalize())
        # El último punto intradía duplica el día → quedarnos con el último por fecha
        df = df[~df.index.duplicated(keep="last")]
        return df
    except Exception:
        return pd.DataFrame()


# ── Función principal ──────────────────────────────────────────────────────

def get_crypto_data(simbolo: str) -> dict:
    """TODOS los datos de una cripto del universo, consolidados y blindados.

    Devuelve {} solo si el símbolo no está en el universo. Los huecos de red
    quedan como None — el scoring los saca de la ecuación con _pond, jamás los
    convierte en 0. NUNCA lanza."""
    out = {}
    try:
        sim = resolver_cripto(simbolo)
        if not sim:
            return {}
        meta = CRYPTO_UNIVERSO[sim]
        out = {
            "simbolo": sim,
            "nombre": meta["nombre"],
            "yahoo": meta["yahoo"],
            "clase": meta["clase"],
            "cadena_defi": meta["cadena_defi"],
        }

        # 1. Mercado (CoinGecko, 1 llamada compartida por todo el universo)
        fila = (_cg_markets() or {}).get(meta["id"]) or {}
        if fila:
            out.update({
                "precio": fila.get("current_price"),
                "market_cap": fila.get("market_cap"),
                "rank": fila.get("market_cap_rank"),
                "volumen_24h": fila.get("total_volume"),
                "supply_circulante": fila.get("circulating_supply"),
                "supply_maximo": fila.get("max_supply"),
                "ath": fila.get("ath"),
                "ath_distancia_pct": fila.get("ath_change_percentage"),
                "delta_24h_pct": fila.get("price_change_percentage_24h"),
                "delta_7d_pct": fila.get("price_change_percentage_7d_in_currency"),
                "delta_30d_pct": fila.get("price_change_percentage_30d_in_currency"),
                "delta_1y_pct": fila.get("price_change_percentage_1y_in_currency"),
            })
            try:
                circ, mx = fila.get("circulating_supply"), fila.get("max_supply")
                out["pct_emitido"] = round(circ / mx * 100.0, 2) if (circ and mx) else None
            except Exception:
                out["pct_emitido"] = None
            try:
                v, m = fila.get("total_volume"), fila.get("market_cap")
                out["turnover_pct"] = round(v / m * 100.0, 2) if (v and m) else None
            except Exception:
                out["turnover_pct"] = None

        # 2. Dominancia y mercado global
        g = _cg_global() or {}
        out.update({
            "dominancia_btc": g.get("dominancia_btc"),
            "dominancia_eth": g.get("dominancia_eth"),
            "mcap_global": g.get("mcap_total_usd"),
        })
        try:
            out["dominancia_propia"] = (round(out["market_cap"] / g["mcap_total_usd"] * 100.0, 2)
                                        if (out.get("market_cap") and g.get("mcap_total_usd")) else None)
        except Exception:
            out["dominancia_propia"] = None

        # 3. Fear & Greed (mercado entero, no por moneda)
        fng = get_fear_greed() or {}
        out["fng_actual"] = fng.get("actual")
        out["fng_clasificacion"] = fng.get("clasificacion")
        out["fng_serie"] = fng.get("serie")

        # 4. TVL de su cadena (solo si el TVL es SU métrica; si no, None y el
        #    scoring lo trata como "no aplica" — mismo patrón que los bancos)
        cadena = meta["cadena_defi"]
        if cadena:
            out["tvl"] = (_tvl_cadenas() or {}).get(cadena)
            out["tvl_delta_30d_pct"] = _tvl_tendencia_30d(cadena)
            try:
                out["tvl_mcap_ratio"] = (round(out["tvl"] / out["market_cap"] * 100.0, 2)
                                         if (out.get("tvl") and out.get("market_cap")) else None)
            except Exception:
                out["tvl_mcap_ratio"] = None
        else:
            out["tvl"] = None
            out["tvl_delta_30d_pct"] = None
            out["tvl_mcap_ratio"] = None

        return out
    except Exception:
        return out or {}
