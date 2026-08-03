#!/usr/bin/env python3
"""Medidor de memoria de DLP Market Analyzer.

Reproduce en local el ciclo de vida real del proceso en Render y mide el RSS en
cada hito, para poder comparar ANTES/DESPUÉS de cada optimización.

    python3 scripts/medir_memoria.py                 # tickers por defecto
    python3 scripts/medir_memoria.py NVDA MSFT       # tickers concretos
    python3 scripts/medir_memoria.py --sin-escaneo   # salta el escaneo (lento)

NO se importa desde la aplicación: es una herramienta de diagnóstico aparte.
"""
import gc
import os
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ru_maxrss viene en BYTES en macOS y en KILOBYTES en Linux. Sin esto, las
# comparaciones local↔Render salen con un factor 1024 de error.
_DIV = 1048576 if sys.platform == "darwin" else 1024


def pico_mb():
    """Pico histórico de RSS del proceso."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / _DIV


def rss_mb():
    """RSS VIVO (no el pico). Es el número que Render mide y limita."""
    try:                                        # Linux (Render)
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    except Exception:                           # macOS (local)
        try:
            return float(os.popen(f"ps -o rss= -p {os.getpid()}").read()) / 1024
        except Exception:
            return pico_mb()


_ANTERIOR = [0.0]


def marca(etiqueta):
    gc.collect()
    ahora = rss_mb()
    delta = ahora - _ANTERIOR[0]
    _ANTERIOR[0] = ahora
    print(f"  {etiqueta:38s} rss={ahora:7.1f} MB  (Δ {delta:+6.1f})  pico={pico_mb():7.1f}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sin_escaneo = "--sin-escaneo" in sys.argv
    tickers = args or ["NVDA", "MSFT", "KO", "TSLA"]

    print(f"\n═══ MEDICIÓN DE MEMORIA · {time.strftime('%Y-%m-%d %H:%M')} ═══")
    print(f"    plataforma={sys.platform}  python={sys.version.split()[0]}\n")
    marca("0. python desnudo")

    # ── 1. ARRANQUE: lo que carga dashboard/app.py al importarse ──────────
    import streamlit                                          # noqa: F401
    import pandas                                             # noqa: F401
    import numpy                                              # noqa: F401
    from agents.orchestrator import Orchestrator              # arrastra yfinance+ta
    from agents.screener import ScreenerAgent                 # noqa: F401
    from dashboard import styles                              # noqa: F401
    marca("1. imports de arranque")
    print(f"     └─ anthropic en el grafo: {'anthropic' in sys.modules}")

    # ── 2. HISTORIAL (lo que se carga en cada sesión nueva) ───────────────
    try:
        from data.persistence import load_all_analyses
        hist = load_all_analyses()
        marca(f"2. + historial ({len(hist)} análisis)")
        con_df = sum(
            1 for a in hist.values()
            if "df_daily" in ((a.reports.get("technical").raw_data or {})
                              if a.reports.get("technical") else {})
        )
        print(f"     └─ análisis que arrastran df_daily: {con_df}")
        del hist
        gc.collect()
    except Exception as e:
        print(f"     (historial no disponible: {type(e).__name__})")

    # ── 3. ANÁLISIS (el pico real de uso) ─────────────────────────────────
    orch = Orchestrator(None)
    for i, tk in enumerate(tickers, 1):
        t0 = time.time()
        try:
            orch.analyze(tk)
        except Exception as e:
            print(f"     (fallo analizando {tk}: {type(e).__name__})")
            continue
        marca(f"3.{i} + análisis {tk} ({time.time()-t0:.0f}s)")

    # ── 4. ESCANEO DEL MERCADO ────────────────────────────────────────────
    if not sin_escaneo:
        try:
            t0 = time.time()
            res = ScreenerAgent().run_full_scan()
            marca(f"4. + escaneo ({len(res)} result., {time.time()-t0:.0f}s)")
            del res
            gc.collect()
            marca("5. tras liberar el escaneo")
        except Exception as e:
            print(f"     (escaneo no disponible: {type(e).__name__})")

    print(f"\n  RSS FINAL: {rss_mb():.1f} MB   ·   PICO: {pico_mb():.1f} MB")
    print(f"  Límite de Render (free): 512 MB\n")


if __name__ == "__main__":
    main()
