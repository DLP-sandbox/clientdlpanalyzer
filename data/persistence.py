"""
Persistencia de análisis y scans — Supabase Postgres en cloud,
fallback a filesystem local cuando no hay credenciales.

Si las variables SUPABASE_URL + SUPABASE_SERVICE_KEY están configuradas
(via .env o st.secrets), todos los reads/writes van a Supabase. Es la
única forma de tener persistencia real en Streamlit Cloud, porque su
filesystem es efímero (se borra en cada restart del container).

Cuando no hay Supabase, se sigue usando .history/analyses/*.json y
.history/scans/scan_*.json para mantener el flujo de desarrollo local
funcionando sin necesidad de configurar la database.
"""
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

HISTORY_DIR = Path(__file__).parent.parent / ".history"
ANALYSES_DIR = HISTORY_DIR / "analyses"
SCANS_DIR = HISTORY_DIR / "scans"

# UID fijo — esta es una app single-user privada. Si en el futuro se
# agrega auth multi-user, este uid pasa a ser el del usuario logueado.
OWNER_UID = "owner"

# Cache del cliente Supabase para evitar recrearlo en cada llamada.
# Valores: None (no probado), False (probado y falló), instancia (OK).
_SB_CLIENT = None

SPANISH_MONTHS = {
    1: "enero",   2: "febrero", 3: "marzo",     4: "abril",
    5: "mayo",    6: "junio",   7: "julio",     8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def _ensure_dirs() -> None:
    ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
    SCANS_DIR.mkdir(parents=True, exist_ok=True)


def _supabase_client():
    """Devuelve el cliente Supabase si está configurado. Cachea el resultado.

    Lee credenciales en este orden:
      1. os.environ (vía .env local con load_dotenv, o vía env vars cloud)
      2. st.secrets (Streamlit Cloud)

    Si no encuentra credenciales o falla la conexión, retorna None y el
    código sigue usando los archivos locales del .history.
    """
    global _SB_CLIENT
    if _SB_CLIENT is not None and _SB_CLIENT is not False:
        return _SB_CLIENT
    if _SB_CLIENT is False:
        return None

    url = os.environ.get("SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_KEY"))

    # Fallback a Streamlit secrets si no están en env
    if not url or not key:
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                if not url:
                    try:    url = st.secrets["SUPABASE_URL"]
                    except Exception: pass
                if not key:
                    for k in ("SUPABASE_SERVICE_KEY", "SUPABASE_KEY"):
                        try:
                            key = st.secrets[k]
                            break
                        except Exception:
                            continue
        except Exception:
            pass

    if not url or not key:
        _SB_CLIENT = False
        return None

    try:
        from supabase import create_client
        # Normalizar URL: a veces el usuario pega con trailing /rest/v1/
        url = str(url).strip()
        for suffix in ("/rest/v1/", "/rest/v1", "/"):
            if url.endswith(suffix):
                url = url[:-len(suffix)]
        _SB_CLIENT = create_client(url, str(key).strip())
        return _SB_CLIENT
    except Exception as e:
        _log_persistence_error("supabase_client_init", e)
        _SB_CLIENT = False
        return None


def persistence_mode() -> str:
    """Indica si la persistencia es DURADERA o EFÍMERA, para poder avisarlo en
    la UI. Devuelve:
      - "supabase": credenciales OK y cliente creado → historial sobrevive a
        reconexiones, redeploys y reinicios del contenedor.
      - "local": sin Supabase → se usa el disco local, que en Render/Streamlit
        Cloud es EFÍMERO y se borra en cada reinicio/redeploy.
    NUNCA lanza excepción."""
    try:
        return "supabase" if _supabase_client() is not None else "local"
    except Exception:
        return "local"


def _make_json_safe(obj):
    """Convierte recursivamente cualquier objeto a tipos JSON-safe.

    Esto es CRÍTICO porque:
    1. Algunos agents guardan dicts con claves de pandas.Timestamp que
       json.dumps NO puede serializar (claves deben ser str/int/float/bool/None).
    2. NaN/Infinity son válidos en Python float pero NO en JSON estándar
       (lo cual rompe Supabase/Postgres). Los convertimos a None.
    """
    import math
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (int, float)):
        # Filtrar NaN, +Inf, -Inf → None (JSON-compliant)
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_make_json_safe(v) for v in obj]
    # Objetos con to_dict (StockAnalysis, AgentReport)
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        try:
            return _make_json_safe(obj.to_dict())
        except Exception:
            return str(obj)
    # numpy types — tienen .item() para convertir a python nativo
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return _make_json_safe(obj.item())
        except Exception:
            pass
    # pandas Timestamp, datetime, etc. → str
    return str(obj)


def _safe_json_dumps(data) -> str:
    """Serializa a JSON sanitizando claves no-string primero."""
    safe = _make_json_safe(data)
    return json.dumps(safe, indent=2, ensure_ascii=False)


def _log_persistence_error(context: str, exc: Exception) -> None:
    """Log a .history/persistence_errors.log para no silenciar fallos."""
    try:
        _ensure_dirs()
        log_path = HISTORY_DIR / "persistence_errors.log"
        with log_path.open("a") as f:
            f.write(f"{datetime.now().isoformat()} [{context}] {type(exc).__name__}: {exc}\n")
    except Exception:
        pass


# ── ANALYSES ──────────────────────────────────────────────────────────────

def save_analysis(analysis) -> None:
    """Guarda un StockAnalysis. Prioriza Supabase si está configurado,
    siempre guarda también una copia local como backup."""
    ticker = getattr(analysis, "ticker", "?")
    safe_dict = _make_json_safe(analysis.to_dict())

    # 1. Supabase (cloud — sobrevive a restart del container)
    sb = _supabase_client()
    if sb is not None:
        try:
            sb.table("user_analyses").upsert({
                "uid":        OWNER_UID,
                "ticker":     ticker,
                "data":       safe_dict,
                "updated_at": datetime.now().isoformat(),
            }).execute()
        except Exception as e:
            _log_persistence_error(f"sb_save_analysis:{ticker}", e)

    # 2. Local file (siempre, como backup — útil para desarrollo offline)
    try:
        _ensure_dirs()
        path = ANALYSES_DIR / f"{ticker}.json"
        path.write_text(_safe_json_dumps(safe_dict))
    except Exception as e:
        _log_persistence_error(f"save_analysis:{ticker}", e)


def delete_analysis(ticker: str) -> None:
    sb = _supabase_client()
    if sb is not None:
        try:
            sb.table("user_analyses").delete().eq(
                "uid", OWNER_UID).eq("ticker", ticker).execute()
        except Exception as e:
            _log_persistence_error(f"sb_delete_analysis:{ticker}", e)

    path = ANALYSES_DIR / f"{ticker}.json"
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


# Cuántos análisis/escaneos se conservan. El historial crecía sin límite y cada
# arranque de sesión cargaba TODO a memoria (~130 KB por análisis), lo que hacía
# que el servicio excediera su límite de RAM cada pocos días.
MAX_ANALYSES_ON_DISK = 5
MAX_SCANS_ON_DISK = 3


def prune_old_analyses(keep: int = MAX_ANALYSES_ON_DISK) -> int:
    """Conserva solo los `keep` análisis más recientes y borra el resto
    (Supabase + disco). Devuelve cuántos borró. NUNCA lanza excepción.

    Es LIGERO a propósito: no carga los JSON completos para decidir. En Supabase
    pide solo ticker+fecha; en local ordena por fecha de modificación del archivo.
    Reutiliza delete_analysis(), que ya borra en ambos sitios."""
    borrados = 0
    try:
        sb = _supabase_client()
        if sb is not None:
            rows = sb.table("user_analyses").select("ticker,updated_at").eq(
                "uid", OWNER_UID).order("updated_at", desc=True).execute()
            data = rows.data or []
            for row in data[keep:]:
                t = row.get("ticker")
                if t:
                    delete_analysis(t)
                    borrados += 1
            return borrados

        # Fallback local: ordenar por fecha de modificación (sin abrir el JSON).
        _ensure_dirs()
        files = sorted(ANALYSES_DIR.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[keep:]:
            delete_analysis(p.stem)   # p.stem = ticker
            borrados += 1
    except Exception as e:
        _log_persistence_error("prune_old_analyses", e)
    return borrados


def load_all_analyses() -> dict:
    """Devuelve dict {ticker: StockAnalysis}. Lee de Supabase si está
    configurado; si no, del filesystem local."""
    result = {}

    sb = _supabase_client()
    if sb is not None:
        try:
            rows = sb.table("user_analyses").select("ticker,data").eq(
                "uid", OWNER_UID).execute()
            for row in (rows.data or []):
                obj = stock_analysis_from_dict(row.get("data") or {})
                if obj is not None:
                    result[obj.ticker] = obj
            return result
        except Exception as e:
            _log_persistence_error("sb_load_all_analyses", e)
            # cae al fallback local

    _ensure_dirs()
    for path in sorted(ANALYSES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            obj = stock_analysis_from_dict(data)
            if obj is not None:
                result[obj.ticker] = obj
        except Exception:
            continue
    return result


def stock_analysis_from_dict(d: dict):
    """Reconstruye un StockAnalysis (con sus AgentReports) desde dict."""
    from agents.base import AgentReport
    from agents.orchestrator import StockAnalysis

    reports = {}
    for k, v in (d.get("reports") or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            reports[k] = AgentReport(
                agent_name=v.get("agent_name", k),
                score=float(v.get("score", 50)),
                analysis=v.get("analysis", ""),
                pros=list(v.get("pros") or []),
                cons=list(v.get("cons") or []),
                key_metrics=dict(v.get("key_metrics") or {}),
                conviction=v.get("conviction", "MEDIUM"),
                sub_scores=dict(v.get("sub_scores") or {}),
                raw_data=dict(v.get("raw_data") or {}),
                error=v.get("error"),
            )
        except Exception:
            continue

    try:
        return StockAnalysis(
            ticker=d["ticker"],
            company_name=d.get("company_name", d["ticker"]),
            composite_score=float(d.get("composite_score", 50)),
            recommendation=d.get("recommendation", "EN OBSERVACIÓN"),
            conviction_level=d.get("conviction_level", "MEDIUM"),
            investment_thesis=d.get("investment_thesis", ""),
            key_strengths=list(d.get("key_strengths") or []),
            key_risks=list(d.get("key_risks") or []),
            entry_strategy=d.get("entry_strategy", ""),
            exit_strategy=d.get("exit_strategy", ""),
            time_horizon=d.get("time_horizon", ""),
            snowflake=dict(d.get("snowflake") or {}),
            score_breakdown=dict(d.get("score_breakdown") or {}),
            vetos_applied=list(d.get("vetos_applied") or []),
            alpha_opportunity=d.get("alpha_opportunity", ""),
            reports=reports,
            entry_price=d.get("entry_price"),
            stop_loss=d.get("stop_loss"),
            target_price=d.get("target_price"),
            risk_reward=d.get("risk_reward"),
            position_size_pct=d.get("position_size_pct"),
            sector=d.get("sector", "Unknown"),
            # Campos nuevos rebalanceo — defaults None/False para backward compat
            long_term_quality_score=d.get("long_term_quality_score"),
            quality_verdict=d.get("quality_verdict"),
            asymmetry_direction=d.get("asymmetry_direction"),
            asymmetry_strength=d.get("asymmetry_strength"),
            is_compound_machine=bool(d.get("is_compound_machine", False)),
            timestamp=d.get("timestamp", datetime.now().isoformat()),
        )
    except Exception:
        return None


# ── SCANS ─────────────────────────────────────────────────────────────────

def scan_label(dt: datetime) -> str:
    """Etiqueta legible en español: 'Scan mayo 17'."""
    month_es = SPANISH_MONTHS.get(dt.month, dt.strftime("%B"))
    return f"Scan {month_es} {dt.day}"


def scan_label_with_time(dt: datetime) -> str:
    return f"{scan_label(dt)} · {dt.strftime('%H:%M')}"


def save_scan(scan_results) -> Optional[str]:
    """Guarda lista de ScreenerResult. Retorna scan_id."""
    if not scan_results:
        return None
    try:
        now = datetime.now()
        scan_id = now.strftime("%Y%m%d_%H%M%S")
        label = scan_label(now)

        results_data = []
        for r in scan_results:
            try:
                results_data.append(asdict(r))
            except Exception:
                try:
                    results_data.append(dict(r.__dict__))
                except Exception:
                    pass

        data = {
            "scan_id":   scan_id,
            "timestamp": now.isoformat(),
            "label":     label,
            "count":     len(scan_results),
            "results":   results_data,
        }
        safe_data = _make_json_safe(data)

        # 1. Supabase
        sb = _supabase_client()
        if sb is not None:
            try:
                sb.table("user_scans").upsert({
                    "uid":     OWNER_UID,
                    "scan_id": scan_id,
                    "label":   label,
                    "count":   len(scan_results),
                    "data":    safe_data,
                }).execute()
            except Exception as e:
                _log_persistence_error(f"sb_save_scan:{scan_id}", e)

        # 2. Local file backup
        _ensure_dirs()
        (SCANS_DIR / f"scan_{scan_id}.json").write_text(_safe_json_dumps(safe_data))
        return scan_id
    except Exception as e:
        _log_persistence_error("save_scan", e)
        return None


def _scan_meta_from_head(path) -> Optional[dict]:
    """Lee SOLO la cabecera del archivo de scan para sacar los 4 metadatos.

    El JSON se guarda con este orden: scan_id, timestamp, label, count y, al
    final, `results` (que puede pesar cientos de KB). Antes se hacía
    json.loads() del archivo COMPLETO solo para leer estos 4 campos — y esto se
    ejecuta en CADA render de la barra lateral, así que era el mayor consumo de
    memoria de la app. Aquí leemos unos pocos cientos de bytes y extraemos los
    campos con regex. Si algo no cuadra devolvemos None y el llamador hace el
    parseo completo como respaldo."""
    try:
        import re as _re
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            head = f.read(600)  # suficiente para los 4 campos, antes de results
        def _grab(campo, numerico=False):
            patron = rf'"{campo}"\s*:\s*' + (r'(\d+)' if numerico else r'"([^"]*)"')
            m = _re.search(patron, head)
            return (int(m.group(1)) if numerico else m.group(1)) if m else None
        scan_id = _grab("scan_id")
        timestamp = _grab("timestamp")
        if not scan_id or not timestamp:
            return None
        return {
            "scan_id":   scan_id,
            "timestamp": timestamp,
            "label":     _grab("label") or "",
            "count":     _grab("count", numerico=True) or 0,
        }
    except Exception:
        return None


def load_all_scans_meta() -> list[dict]:
    """Metadata de todos los scans (sin cargar results), ordenados desc por fecha."""
    metas = []

    sb = _supabase_client()
    if sb is not None:
        try:
            # OJO: NO pedimos la columna `data` (el scan completo con todos los
            # resultados). Solo los metadatos → payload mínimo en cada render.
            rows = sb.table("user_scans").select(
                "scan_id,label,count,created_at").eq(
                "uid", OWNER_UID).order("created_at", desc=True).execute()
            for row in (rows.data or []):
                metas.append({
                    "scan_id":   row.get("scan_id"),
                    "timestamp": row.get("created_at"),
                    "label":     row.get("label") or "",
                    "count":     row.get("count") or 0,
                })
            return metas
        except Exception as e:
            _log_persistence_error("sb_load_all_scans_meta", e)

    _ensure_dirs()
    for path in SCANS_DIR.glob("scan_*.json"):
        meta = _scan_meta_from_head(path)
        if meta is None:
            # Respaldo: parseo completo (solo si la cabecera no fue legible).
            try:
                data = json.loads(path.read_text())
                meta = {
                    "scan_id":   data.get("scan_id"),
                    "timestamp": data.get("timestamp"),
                    "label":     data.get("label"),
                    "count":     data.get("count", 0),
                }
            except Exception:
                continue
        metas.append(meta)
    metas.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return metas


def get_scan_history_labels() -> list[tuple]:
    """Lista (scan_id, display_label, count) — añade hora si hay varios mismo día."""
    metas = load_all_scans_meta()

    by_day = {}
    for m in metas:
        try:
            dt = datetime.fromisoformat(m["timestamp"])
            day_key = dt.strftime("%Y-%m-%d")
            by_day.setdefault(day_key, []).append((m, dt))
        except Exception:
            continue

    out = []
    for day, items in by_day.items():
        if len(items) == 1:
            m, dt = items[0]
            out.append((m["scan_id"], scan_label(dt), m.get("count", 0)))
        else:
            for m, dt in items:
                out.append((m["scan_id"], scan_label_with_time(dt), m.get("count", 0)))

    out.sort(key=lambda x: x[0], reverse=True)
    return out


def load_scan_by_id(scan_id: str) -> list:
    """Carga los ScreenerResult de un scan específico."""
    from agents.screener import ScreenerResult

    sb = _supabase_client()
    if sb is not None:
        try:
            rows = sb.table("user_scans").select("data").eq(
                "uid", OWNER_UID).eq("scan_id", scan_id).limit(1).execute()
            if rows.data:
                data = (rows.data[0].get("data") or {})
                results = []
                for r in data.get("results", []):
                    try:
                        results.append(ScreenerResult(**r))
                    except Exception:
                        continue
                return results
        except Exception as e:
            _log_persistence_error(f"sb_load_scan_by_id:{scan_id}", e)

    path = SCANS_DIR / f"scan_{scan_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        results = []
        for r in data.get("results", []):
            try:
                results.append(ScreenerResult(**r))
            except Exception:
                continue
        return results
    except Exception:
        return []


def delete_scan(scan_id: str) -> None:
    sb = _supabase_client()
    if sb is not None:
        try:
            sb.table("user_scans").delete().eq(
                "uid", OWNER_UID).eq("scan_id", scan_id).execute()
        except Exception as e:
            _log_persistence_error(f"sb_delete_scan:{scan_id}", e)

    path = SCANS_DIR / f"scan_{scan_id}.json"
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


def prune_old_scans(keep: int = MAX_SCANS_ON_DISK) -> int:
    """Conserva solo los `keep` escaneos más recientes y borra el resto
    (Supabase + disco). Devuelve cuántos borró. NUNCA lanza excepción.

    Ligero: en Supabase pide solo scan_id+fecha (NO la columna `data`, que es la
    pesada); en local ordena por nombre de archivo, que ya lleva la fecha
    (scan_YYYYMMDD_HHMMSS.json). Reutiliza delete_scan()."""
    borrados = 0
    try:
        sb = _supabase_client()
        if sb is not None:
            rows = sb.table("user_scans").select("scan_id,created_at").eq(
                "uid", OWNER_UID).order("created_at", desc=True).execute()
            data = rows.data or []
            for row in data[keep:]:
                sid = row.get("scan_id")
                if sid:
                    delete_scan(sid)
                    borrados += 1
            return borrados

        # Fallback local: el nombre scan_YYYYMMDD_HHMMSS.json ya ordena por fecha.
        _ensure_dirs()
        files = sorted(SCANS_DIR.glob("scan_*.json"),
                       key=lambda p: p.name, reverse=True)
        for p in files[keep:]:
            delete_scan(p.stem.replace("scan_", "", 1))
            borrados += 1
    except Exception as e:
        _log_persistence_error("prune_old_scans", e)
    return borrados
