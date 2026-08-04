#!/usr/bin/env python3
"""Verificador de FIABILIDAD DE DATOS de DLP Market Analyzer.

Toma una foto de todos los datos sensibles (ratios, tenedores, scores) de un
abanico de tickers representativo y permite comparar ANTES/DESPUÉS de cada
cambio, para garantizar que nada que ya funcionaba se rompe.

    python3 scripts/verificar_fiabilidad.py --snapshot antes.json
    python3 scripts/verificar_fiabilidad.py --snapshot despues.json
    python3 scripts/verificar_fiabilidad.py --diff antes.json despues.json
    python3 scripts/verificar_fiabilidad.py --snapshot x.json --tickers CIB,JPM

NO se importa desde la aplicación: es una herramienta de diagnóstico aparte.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Abanico deliberado: cada uno cubre un caso distinto que debe seguir funcionando.
UNIVERSO = [
    "CIB",    # ADR banco Colombia — el caso reportado (ratios corruptos, sin insiders)
    "BSAC",   # ADR banco Chile — segundo caso de divisa mixta
    "TM",     # ADR industrial Japón — P/B corrupto al alza (15.31 vs 0.97 real)
    "HMC",    # ADR Japón — REGRESIÓN del fix e8c6809 (divisas mixtas + earnings)
    "SAN",    # ADR banco España — divisa "cercana": casi no debe cambiar
    "UNTY",   # micro-cap USA banco — prueba de que el tamaño NO es el problema
    "CNS",    # small-cap USA gestora — NO es banco (no debe clasificarse como tal)
    "JPM",    # mega-cap USA banco — gross margin 0.0 que hoy sale en rojo
    "MSFT",   # tech USA — CONTROL PURO: no debe cambiar absolutamente nada
    "KO",     # NYSE — control de la cadena FINRA de short interest
    "O",      # REIT — otro tipo de negocio con métricas que no aplican
    "BRK-B",  # ticker con guion — prueba de _tv_row (BRK-B vs BRK.B)
    "ZZZZ",   # inexistente — debe salir vacío SIN excepción
]

# Campos que se fotografían de get_company_info
CAMPOS_INFO = [
    "name", "sector", "industry", "country", "trading_currency",
    "financial_currency", "pais_emisor", "emisor_extranjero", "divisa_mixta",
    "moneda_estados_local", "ratios_corregidos", "ratios_descartados",
    "market_cap", "current_price",
    "pe_ratio", "forward_pe", "pb_ratio", "ps_ratio", "ev_ebitda", "peg_ratio",
    "profit_margin", "gross_margin_yf", "operating_margin_yf",
    "roe_yf", "roa_yf", "debt_equity_yf", "current_ratio_yf",
    "revenue_ttm", "ebitda_yf", "fcf_yf", "book_value_yf",
    "dividend_yield", "beta", "short_percent", "short_ratio",
]

CAMPOS_HOLDERS = [
    "institutional_ownership_pct", "institutions_count",
    "recent_insider_buys", "recent_insider_sells",
    "insiders_percent_held", "insiders_disponibles", "insiders_motivo",
]


def _num(v):
    """Redondea floats para que el diff no chille por ruido de última cifra."""
    if isinstance(v, float):
        return round(v, 6)
    return v


def foto_ticker(tk: str) -> dict:
    """Fotografía completa de un ticker. NUNCA lanza."""
    out = {"ticker": tk}
    try:
        from data.market_data import (get_company_info, get_financials,
                                      compute_quality_ratios, get_holders_data)
        info = get_company_info(tk) or {}
        out["info"] = {k: _num(info.get(k)) for k in CAMPOS_INFO}

        fin = get_financials(tk) or {}
        ratios = compute_quality_ratios(info, fin) or {}
        out["ratios"] = {k: _num(v) for k, v in sorted(ratios.items())}

        h = get_holders_data(tk) or {}
        out["holders"] = {k: _num(h.get(k)) for k in CAMPOS_HOLDERS}
        out["holders"]["n_insider_txns"] = len(h.get("insider_transactions") or [])
        out["holders"]["n_top_institutions"] = len(h.get("top_institutions") or [])

        # ── Scores (lo que de verdad ve el usuario) ──────────────────────
        from agents.code_engine import score_fundamentals, score_institutional
        try:
            f = score_fundamentals(info, fin, ratios)
            out["score_fundamentals"] = {
                "score": _num(f.get("score")),
                "conviction": f.get("conviction"),
                "sub_scores": {k: _num(v) for k, v in (f.get("sub_scores") or {}).items()},
                "key_insight": f.get("key_insight"),
                "pros": f.get("pros"), "cons": f.get("cons"),
            }
        except Exception as e:
            out["score_fundamentals"] = {"ERROR": f"{type(e).__name__}: {e}"}
        try:
            i = score_institutional(h, info)
            out["score_institutional"] = {
                "score": _num(i.get("score")),
                "conviction": i.get("conviction"),
                "sub_scores": {k: _num(v) for k, v in (i.get("sub_scores") or {}).items()},
                "key_metrics": i.get("key_metrics"),
                "key_insight": i.get("key_insight"),
                "pros": i.get("pros"), "cons": i.get("cons"),
            }
        except Exception as e:
            out["score_institutional"] = {"ERROR": f"{type(e).__name__}: {e}"}
    except Exception as e:
        out["ERROR"] = f"{type(e).__name__}: {e}"
    return out


def snapshot(destino: str, tickers: list):
    print(f"\n═══ SNAPSHOT de {len(tickers)} tickers → {destino} ═══\n")
    datos = {}
    for tk in tickers:
        print(f"  · {tk} …", end="", flush=True)
        d = foto_ticker(tk)
        datos[tk] = d
        info = d.get("info") or {}
        h = d.get("holders") or {}
        err = d.get("ERROR")
        if err:
            print(f" ERROR {err[:40]}")
        else:
            print(f" P/B={str(info.get('pb_ratio'))[:9]:>9s} "
                  f"P/E={str(info.get('pe_ratio'))[:7]:>7s} "
                  f"gm={str(info.get('gross_margin_yf'))[:6]:>6s} "
                  f"ins={h.get('n_insider_txns')} "
                  f"inst%={str(h.get('institutional_ownership_pct'))[:6]}")
    with open(destino, "w") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Guardado en {destino}\n")


def _aplanar(d, pref=""):
    """dict anidado → {ruta.plana: valor}"""
    out = {}
    for k, v in (d or {}).items():
        ruta = f"{pref}.{k}" if pref else str(k)
        if isinstance(v, dict):
            out.update(_aplanar(v, ruta))
        else:
            out[ruta] = v
    return out


def diff(a_path: str, b_path: str):
    a = json.load(open(a_path))
    b = json.load(open(b_path))
    print(f"\n═══ DIFF  {a_path} → {b_path} ═══")
    # Estos deben salir SIN cambios: son el control de no-regresión
    CONTROL = {"MSFT", "KO", "CNS", "BRK-B"}
    total_cambios = 0
    fallo_control = []
    for tk in sorted(set(a) | set(b)):
        fa, fb = _aplanar(a.get(tk)), _aplanar(b.get(tk))
        cambios = []
        for k in sorted(set(fa) | set(fb)):
            va, vb = fa.get(k, "<AUSENTE>"), fb.get(k, "<AUSENTE>")
            if va != vb:
                cambios.append((k, va, vb))
        if not cambios:
            continue
        total_cambios += len(cambios)
        marca = "  ⚠️ CONTROL" if tk in CONTROL else ""
        if tk in CONTROL:
            fallo_control.append(tk)
        print(f"\n── {tk} ({len(cambios)} cambios){marca}")
        for k, va, vb in cambios[:25]:
            print(f"     {k}")
            print(f"       antes: {str(va)[:110]}")
            print(f"       ahora: {str(vb)[:110]}")
        if len(cambios) > 25:
            print(f"     … y {len(cambios)-25} más")
    print(f"\n═══ TOTAL: {total_cambios} cambios ═══")
    if fallo_control:
        print(f"  ⚠️  ATENCIÓN: cambiaron tickers de CONTROL: {fallo_control}")
        print("      (MSFT/KO/CNS/BRK-B deben quedar IDÉNTICOS)")
    else:
        print("  ✓ Los tickers de control (MSFT, KO, CNS, BRK-B) quedaron IDÉNTICOS")
    print()


def main():
    args = sys.argv[1:]
    tickers = UNIVERSO
    if "--tickers" in args:
        tickers = args[args.index("--tickers") + 1].split(",")
    if "--diff" in args:
        i = args.index("--diff")
        diff(args[i + 1], args[i + 2])
    elif "--snapshot" in args:
        snapshot(args[args.index("--snapshot") + 1], tickers)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
