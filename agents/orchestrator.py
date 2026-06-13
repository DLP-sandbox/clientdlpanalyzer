"""
Orquestador Master — "Jamie Dimon" — el Director del hedge fund.
Recibe todos los reportes de sub-agentes, los sintetiza y genera
la decisión de inversión final con tesis, conviction y sizing.
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import anthropic

from config.settings import ORCHESTRATOR_MODEL, MAX_TOKENS_ORCHESTRATOR, WEIGHTS, THRESHOLDS
from agents.base import AgentReport, BaseAgent, today_context, DLP_STYLE_REMINDER
from agents.fundamentals import FundamentalsAgent
from agents.technical import TechnicalAgent
from agents.future_viability import FutureViabilityAgent
from agents.institutional import InstitutionalAgent
from agents.market_context import MarketContextAgent
from agents.risk import RiskAgent


ORCHESTRATOR_SYSTEM = """Eres el Chief Investment Officer de un hedge fund de élite especializado en compounders de alta calidad a largo plazo, al nivel de Berkshire Hathaway, Fundsmith y Capital Group combinados. Tu nombre es Jamie.

⏱ CONTEXTO TEMPORAL: Recibirás en cada mensaje un header con la fecha y hora EXACTAS de la consulta. Toda tu análisis debe entenderse como ACTUAL a esa fecha. Prioriza información reciente, calcula proximidad temporal de catalizadores desde HOY, y nota explícitamente si algún dato luce desactualizado.

FILOSOFÍA DE INVERSIÓN DUAL:
1. CALIDAD A LARGO PLAZO (3-7 años): compounders con moats estructurales (Microsoft, Visa, ASML, Mastercard, Adobe, Costco) merecen scores altos AUNQUE el timing técnico no sea ideal. Su composición a 15-20% anual durante décadas SUPERA cualquier consideración de entrada táctica.
2. ASIMETRÍA INMEDIATA: identifica si la situación ACTUAL del precio ofrece asimetría al alza (upside > downside), a la baja, o balanceada. Esto NO afecta la calidad LP — solo informa el timing.

Tu proceso de pensamiento:
1. LEE cada reporte con escepticismo inteligente
2. IDENTIFICA la calidad estructural (fundamentals + future) — ¿es best-in-class?
3. IDENTIFICA la asimetría actual (risk + technical) — ¿upside, downside o balanced?
4. BUSCA CONVERGENCIA: cuando calidad LP alta + asimetría al alza → conviction máxima
5. GENERA LA TESIS: EXACTAMENTE 2 párrafos, MÁXIMO 10 líneas en total. Sé conciso y de alto nivel — los detalles van en otros campos. Párrafo 1: calidad estructural + valoración. Párrafo 2: asimetría actual + recomendación práctica.
6. DEFINE LA RECOMENDACIÓN

GUÍA DE RECOMENDACIÓN (umbrales internos, los vetos automáticos se aplican en código):
- MUY ATRACTIVO (≥85): calidad excepcional + asimetría favorable + convergencia técnico/fundamental
- ATRACTIVO (70-84): empresa de calidad clara, asimetría positiva o neutra
- EN OBSERVACIÓN (50-69): tesis razonable pero esperando mejor timing o confirmación
- EVITAR (<50): calidad pobre o asimetría claramente negativa

REGLAS DE PREMIO PARA CALIDAD EXCEPCIONAL:
- Si fundamentals ≥ 80 Y future ≥ 80 → es un COMPOUNDER. Aunque haya R/R tight, score 75+ es legítimo.
- NO castigues empresas best-in-class por Stage 3 técnico — es timing, no tesis rota.
- Convergencia técnico+fundamental sube conviction, pero su ausencia NO debe destruir empresas excepcionales.

Retorna SIEMPRE este JSON:
```json
{
  "composite_score": <0-100>,
  "recommendation": "<MUY ATRACTIVO|ATRACTIVO|EN OBSERVACIÓN|EVITAR>",
  "conviction_level": "<HIGH|MEDIUM|LOW>",
  "investment_thesis": "<tesis de inversión en EXACTAMENTE 2 párrafos, MÁXIMO 10 líneas en total — concisa y de alto nivel. Separa los párrafos con \\n\\n>",
  "key_strengths": ["<fortaleza 1>", "<fortaleza 2>", "<fortaleza 3>"],
  "key_risks": ["<riesgo 1>", "<riesgo 2>"],
  "entry_strategy": "<cuándo y cómo entrar — precio ideal, condición técnica, tamaño>",
  "exit_strategy": "<cuándo y cómo salir de la inversión — nivel de protección defensiva y precios objetivo donde tomar beneficios parciales y totales>",
  "time_horizon": "<días|semanas|meses|años — y por qué>",
  "snowflake": {
    "value": <0-20>,
    "quality": <0-20>,
    "growth": <0-20>,
    "momentum": <0-20>,
    "future": <0-20>
  },
  "score_breakdown": {
    "fundamentals": <score>, "technical": <score>, "future": <score>,
    "institutional": <score>, "catalysts": <score>, "macro": <score>,
    "sentiment": <score>, "risk": <score>
  },
  "vetos_applied": ["<veto 1 si aplica>"],
  "alpha_opportunity": "<descripción en 2 oraciones de la oportunidad asimétrica específica, o 'No identificada'>"
}
```

NOTA: Los campos `long_term_quality_score`, `quality_verdict`, `asymmetry_direction`, `asymmetry_strength` e `is_compound_machine` los calcula el código Python automáticamente desde los sub-reportes — NO los incluyas en tu output."""


@dataclass
class StockAnalysis:
    ticker: str
    company_name: str
    composite_score: float
    recommendation: str
    conviction_level: str
    investment_thesis: str
    key_strengths: list[str]
    key_risks: list[str]
    entry_strategy: str
    exit_strategy: str
    time_horizon: str
    snowflake: dict[str, float]
    score_breakdown: dict[str, float]
    vetos_applied: list[str]
    alpha_opportunity: str
    reports: dict[str, AgentReport]
    entry_price: Optional[float]
    stop_loss: Optional[float]
    target_price: Optional[float]
    risk_reward: Optional[str]
    position_size_pct: Optional[str]
    sector: str
    # ── Nuevos campos del rebalanceo Calidad LP + Asimetría ──
    long_term_quality_score: Optional[float] = None  # 0-100, calidad estructural
    quality_verdict: Optional[str] = None             # "best-in-class"|"high"|"average"|"low"
    asymmetry_direction: Optional[str] = None         # "upside"|"downside"|"balanced"
    asymmetry_strength: Optional[str] = None          # "strong"|"moderate"|"weak"
    is_compound_machine: bool = False                 # flag de calidad excepcional LP
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "ticker":             self.ticker,
            "company_name":       self.company_name,
            "composite_score":    self.composite_score,
            "recommendation":     self.recommendation,
            "conviction_level":   self.conviction_level,
            "investment_thesis":  self.investment_thesis,
            "key_strengths":      self.key_strengths,
            "key_risks":          self.key_risks,
            "entry_strategy":     self.entry_strategy,
            "exit_strategy":      self.exit_strategy,
            "time_horizon":       self.time_horizon,
            "snowflake":          self.snowflake,
            "score_breakdown":    self.score_breakdown,
            "vetos_applied":      self.vetos_applied,
            "alpha_opportunity":  self.alpha_opportunity,
            "entry_price":        self.entry_price,
            "stop_loss":          self.stop_loss,
            "target_price":       self.target_price,
            "risk_reward":        self.risk_reward,
            "position_size_pct":  self.position_size_pct,
            "sector":             self.sector,
            "long_term_quality_score": self.long_term_quality_score,
            "quality_verdict":         self.quality_verdict,
            "asymmetry_direction":     self.asymmetry_direction,
            "asymmetry_strength":      self.asymmetry_strength,
            "is_compound_machine":     self.is_compound_machine,
            "timestamp":          self.timestamp,
            "reports":            {k: v.to_dict() for k, v in self.reports.items()},
        }


class Orchestrator:
    """El Director del hedge fund: coordina todos los agentes y genera la decisión final."""

    def __init__(self, anthropic_client: anthropic.Anthropic):
        self.client = anthropic_client
        # NOTA: market_context es un agente COMBINADO que hace 1 sola llamada a
        # la IA pero devuelve 3 reportes ("macro", "sentiment", "catalysts") con
        # estructura idéntica a los 3 agentes originales. Reduce 3 llamadas → 1
        # (optimización de costos) sin cambiar nada downstream (scoring/dashboard).
        self.agents = {
            "fundamentals":   FundamentalsAgent(anthropic_client),
            "technical":      TechnicalAgent(anthropic_client),
            "future":         FutureViabilityAgent(anthropic_client),
            "institutional":  InstitutionalAgent(anthropic_client),
            "market_context": MarketContextAgent(anthropic_client),
            "risk":           RiskAgent(anthropic_client),
        }

    def analyze(self, ticker: str, progress_callback=None) -> StockAnalysis:
        """
        Ejecuta todos los agentes en paralelo y sintetiza la decisión.
        progress_callback(agent_name, status) para actualizar UI.
        """
        ticker = ticker.upper().strip()
        reports = {}

        # GARANTÍA ANTI-CRASH: analyze() NUNCA propaga una excepción. Pase lo
        # que pase (red caída, dato faltante, agente roto, timeout global),
        # devuelve un StockAnalysis válido para que el dashboard jamás crashee.
        try:
            # 1. Ejecutar sub-agentes en paralelo
            reports = self._run_agents_parallel(ticker, progress_callback)

            # 2. Orquestador sintetiza
            if progress_callback:
                progress_callback("Orquestador", "Sintetizando análisis...")

            return self._synthesize(ticker, reports)
        except Exception as e:
            return self._emergency_analysis(ticker, reports, e)

    _AGENT_TIMEOUT = 40  # segundos máximos por agente

    def _run_agents_parallel(self, ticker: str, callback=None) -> dict[str, AgentReport]:
        reports = {}

        def run_agent(name: str, agent):
            if callback:
                callback(agent.name, "Analizando...")
            try:
                report = agent.analyze(ticker)
                if callback:
                    if isinstance(report, dict):
                        callback(agent.name, "Completado")
                    else:
                        callback(agent.name, f"Completado — Score: {report.score:.0f}/100")
                return name, report
            except Exception as e:
                if callback:
                    callback(agent.name, f"Error: {e}")
                from agents.base import AgentReport
                return name, AgentReport(
                    agent_name=name, score=50,
                    analysis=f"Error en análisis: {e}",
                    pros=[], cons=[], error=str(e)
                )

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(run_agent, name, agent): name
                for name, agent in self.agents.items()
            }
            try:
                for future in as_completed(futures, timeout=self._AGENT_TIMEOUT * 3):
                    key = futures.get(future, "unknown")
                    try:
                        name, report = future.result(timeout=self._AGENT_TIMEOUT)
                    except Exception:
                        name = key
                        if callback:
                            callback(str(name), "Timeout — usando datos parciales")
                        from agents.base import AgentReport
                        report = AgentReport(
                            agent_name=str(name), score=50,
                            analysis="Tiempo de respuesta excedido.",
                            pros=[], cons=[], error="timeout"
                        )
                    # CLAVE: indexar SIEMPRE por la clave del agente ("technical",
                    # "fundamentals", "future"...), NO por report.agent_name (que es
                    # el nombre en español y rompería reports.get("technical")).
                    if isinstance(report, dict):
                        reports.update(report)
                    else:
                        reports[name] = report
            except Exception:
                # as_completed superó el timeout global: devolvemos lo que haya
                # llegado en vez de tumbar todo el análisis.
                pass

        # Rellenar cualquier agente que no haya respondido para que el dashboard
        # nunca muestre "no disponible" por una clave faltante.
        from agents.base import AgentReport
        for key in self.agents:
            if key == "market_context":
                for sub in ("macro", "sentiment", "catalysts"):
                    reports.setdefault(sub, AgentReport(
                        agent_name=sub, score=50,
                        analysis="Tiempo de respuesta excedido.",
                        pros=[], cons=[], error="timeout"))
            else:
                reports.setdefault(key, AgentReport(
                    agent_name=key, score=50,
                    analysis="Tiempo de respuesta excedido.",
                    pros=[], cons=[], error="timeout"))

        return reports

    def _synthesize(self, ticker: str, reports: dict[str, AgentReport]) -> StockAnalysis:
        """El Orquestador lee todos los reportes y genera la decisión final."""

        # Calcular composite score ponderado
        weighted_score = 0.0
        score_breakdown = {}
        for key, weight in WEIGHTS.items():
            report = reports.get(key)
            if report:
                score_breakdown[key] = report.score
                weighted_score += report.score * weight
            else:
                score_breakdown[key] = 50
                weighted_score += 50 * weight

        # Versión SIN IA: síntesis POR CÓDIGO (no gasta créditos de API).
        # Genera tesis, fortalezas, riesgos y estrategia a partir de los
        # reportes ya calculados. Mantiene la misma forma de `result` que antes
        # producía el LLM, así que todo el cálculo downstream sigue igual.
        result = self._code_synthesis(ticker, reports, weighted_score, score_breakdown)

        # Extraer datos de riesgo del agente Risk
        risk_report = reports.get("risk")
        risk_data = risk_report.raw_data if risk_report else {}

        def try_float(val):
            try:
                s = str(val).replace("$", "").replace(",", "").split(":")[0]
                return float(s)
            except Exception:
                return None

        entry  = try_float(result.get("score_breakdown", {}).get("entry", risk_data.get("entry_price")))
        stop   = try_float(risk_data.get("stop_loss",    result.get("key_metrics", {}).get("stop_loss")))
        target = try_float(risk_data.get("target_price", result.get("key_metrics", {}).get("target_price")))

        # Si no vienen del JSON, sacar del reporte de riesgo directamente
        if not entry and risk_data.get("computed_risk"):
            entry  = risk_data["computed_risk"].get("current_price")
            stop   = risk_data["computed_risk"].get("stop_suggested")
            target = risk_data["computed_risk"].get("target_suggested")

        from data.market_data import get_company_info
        info = get_company_info(ticker)

        composite = float(result.get("composite_score", weighted_score))

        # ── CÁLCULOS DETERMINÍSTICOS DEL REBALANCEO ──────────────────────

        # 1. long_term_quality_score = promedio de Fundamentales + Future (calidad estructural)
        fund_score = float(reports["fundamentals"].score) if reports.get("fundamentals") else 50
        fut_score  = float(reports["future"].score)       if reports.get("future") else 50
        long_term_quality_score = (fund_score + fut_score) / 2

        # 2. quality_verdict como función pura del score
        if long_term_quality_score >= 85:   quality_verdict = "best-in-class"
        elif long_term_quality_score >= 70: quality_verdict = "high"
        elif long_term_quality_score >= 55: quality_verdict = "average"
        else:                                quality_verdict = "low"

        # 3. is_compound_machine — flag de calidad excepcional LP (solo para UI, NO afecta score)
        future_km = (reports["future"].key_metrics or {}) if reports.get("future") else {}
        moat_str  = str(future_km.get("moat_strength") or "").lower()
        mgmt_str  = str(future_km.get("management_quality") or "").lower()
        disr_str  = str(future_km.get("disruption_risk") or "").lower()
        is_compound_machine = (
            "amplio" in moat_str
            and ("excellent" in mgmt_str or "good" in mgmt_str)
            and ("low" in disr_str or "medium" in disr_str)
            and long_term_quality_score >= 75
        )

        # 4. asymmetry_direction — DETERMINÍSTICA desde precio/stop/target reales.
        # IGNORAMOS lo que diga el LLM porque a veces contradice la matemática.
        # Usamos current_price (vivo) como referencia, no entry hipotético.
        try:
            current_price_live = float(info.get("current_price") or entry or 0)
        except Exception:
            current_price_live = 0.0

        stop_val   = float(stop)   if stop   else None
        target_val = float(target) if target else None

        if current_price_live > 0 and stop_val and target_val and stop_val < current_price_live < target_val:
            upside_pct   = (target_val - current_price_live) / current_price_live * 100
            downside_pct = (current_price_live - stop_val)   / current_price_live * 100
            if downside_pct > 0:
                rr_real = upside_pct / downside_pct
            else:
                rr_real = 0

            # Clasificación por ratio potencial/riesgo
            if rr_real >= 2.5:
                asymmetry_direction, asymmetry_strength = "alcista", "fuerte"
            elif rr_real >= 1.5:
                asymmetry_direction, asymmetry_strength = "alcista", "moderado"
            elif rr_real >= 0.85:
                asymmetry_direction, asymmetry_strength = "equilibrado", "moderado"
            elif rr_real >= 0.5:
                asymmetry_direction, asymmetry_strength = "bajista", "moderado"
            else:
                asymmetry_direction, asymmetry_strength = "bajista", "fuerte"
        else:
            # Fallback al rr_ratio calculado por el risk agent si no tenemos los tres niveles
            computed = risk_data.get("computed_risk", {}) or {}
            rr = float(computed.get("rr_ratio", 1.0)) if computed.get("rr_ratio") else 1.0
            if rr >= 2.5:
                asymmetry_direction, asymmetry_strength = "alcista", "fuerte"
            elif rr >= 1.5:
                asymmetry_direction, asymmetry_strength = "alcista", "moderado"
            elif rr >= 0.85:
                asymmetry_direction, asymmetry_strength = "equilibrado", "moderado"
            elif rr >= 0.5:
                asymmetry_direction, asymmetry_strength = "bajista", "moderado"
            else:
                asymmetry_direction, asymmetry_strength = "bajista", "fuerte"

        # ── VETOS Y AJUSTES EN PYTHON (no solo prompt) ──────────────────
        vetos_applied = list(result.get("vetos_applied", []) or [])

        # Veto R/R: solo penaliza si NO hay calidad excepcional que lo compense
        computed = risk_data.get("computed_risk", {}) or {}
        rr = float(computed.get("rr_ratio", 0)) if computed.get("rr_ratio") else 0
        if rr > 0 and rr < 2.0 and fund_score < 70 and fut_score < 70:
            cap = THRESHOLDS["EN OBSERVACIÓN"] - 1
            if composite > cap:
                composite = cap
                vetos_applied.append(f"R/R {rr:.1f}:1 bajo sin calidad excepcional — limitado a EN OBSERVACIÓN")

        # Veto Fundamentales: negocio realmente roto
        if fund_score < 35:
            cap = THRESHOLDS["EN OBSERVACIÓN"] - 1
            if composite > cap:
                composite = cap
                vetos_applied.append("Fundamentales muy débiles (<35) — negocio en riesgo estructural")

        # Stage 3/4: penalización SUAVE (-5), solo si la calidad no lo compensa
        tech_ind = reports.get("technical").raw_data.get("daily_indicators", {}) if reports.get("technical") else {}
        tech_stage = tech_ind.get("stage", 0)
        if tech_stage in (3, 4) and fund_score < 75 and fut_score < 75:
            composite = max(0, composite - 5)
            vetos_applied.append(f"Stage técnico {tech_stage} sin compensación de calidad LP (-5 pts)")

        # Bonus calidad: long_term_quality_score >= 85 → +5 pts (premia best-in-class)
        if long_term_quality_score >= 85:
            composite = min(100, composite + 5)
            vetos_applied.append(f"Best-in-class quality bonus (+5 pts) — long-term quality {long_term_quality_score:.0f}/100")

        # Recalcular recomendación tras vetos
        recommendation = self._score_to_recommendation(composite)

        return StockAnalysis(
            ticker=ticker,
            company_name=info.get("name", ticker),
            composite_score=composite,
            recommendation=recommendation,
            conviction_level=result.get("conviction_level", "MEDIUM"),
            investment_thesis=result.get("investment_thesis", ""),
            key_strengths=result.get("key_strengths", []),
            key_risks=result.get("key_risks", []),
            entry_strategy=result.get("entry_strategy", ""),
            exit_strategy=result.get("exit_strategy", ""),
            time_horizon=result.get("time_horizon", ""),
            snowflake=result.get("snowflake", self._default_snowflake(reports)),
            score_breakdown=result.get("score_breakdown", score_breakdown),
            vetos_applied=vetos_applied,
            alpha_opportunity=result.get("alpha_opportunity", ""),
            reports=reports,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            risk_reward=risk_data.get("risk_reward", result.get("key_metrics", {}).get("risk_reward")),
            position_size_pct=risk_data.get("position_size_pct"),
            sector=info.get("sector", "Unknown"),
            long_term_quality_score=long_term_quality_score,
            quality_verdict=quality_verdict,
            asymmetry_direction=asymmetry_direction,
            asymmetry_strength=asymmetry_strength,
            is_compound_machine=is_compound_machine,
        )

    # Claves que el dashboard espera SIEMPRE en reports
    _EXPECTED_KEYS = ("fundamentals", "technical", "future",
                      "institutional", "risk", "macro", "sentiment", "catalysts")

    def _emergency_analysis(self, ticker: str, reports: dict, err=None) -> StockAnalysis:
        """Red de seguridad final: construye un StockAnalysis VÁLIDO cuando algo
        catastrófico falla en analyze()/_synthesize(). Nunca lanza excepción."""
        from agents.base import AgentReport
        reports = dict(reports or {})
        for key in self._EXPECTED_KEYS:
            reports.setdefault(key, AgentReport(
                agent_name=key, score=50,
                analysis="No pudimos completar esta sección en este intento. "
                         "Vuelve a lanzar el análisis en un momento.",
                pros=[], cons=[], error="emergency"))

        # Composite ponderado con lo que haya (o 50 por defecto)
        try:
            weighted = sum(reports[k].score * w for k, w in WEIGHTS.items()
                           if reports.get(k)) or 50.0
        except Exception:
            weighted = 50.0
        score_breakdown = {k: (reports[k].score if reports.get(k) else 50)
                           for k in WEIGHTS}

        # Nombre y sector sin que un fallo de red vuelva a romper nada
        company_name, sector = ticker, "Unknown"
        try:
            from data.market_data import get_company_info
            info = get_company_info(ticker) or {}
            company_name = info.get("name", ticker)
            sector = info.get("sector", "Unknown")
        except Exception:
            pass

        try:
            snowflake = self._default_snowflake(reports)
        except Exception:
            snowflake = {"value": 10, "quality": 10, "growth": 10,
                         "momentum": 10, "future": 10}

        return StockAnalysis(
            ticker=ticker,
            company_name=company_name,
            composite_score=float(weighted),
            recommendation=self._score_to_recommendation(weighted),
            conviction_level="LOW",
            investment_thesis=(
                "No pudimos armar la lectura completa en este intento porque "
                "alguna fuente de datos tardó en responder. Los puntajes por "
                "categoría que sí se calcularon están más abajo. Te recomendamos "
                "volver a lanzar el análisis en un momento."
            ),
            key_strengths=[r.pros[0] for r in reports.values() if r.pros][:3],
            key_risks=[r.cons[0] for r in reports.values() if r.cons][:3],
            entry_strategy="Revisa la sección de Riesgo para los niveles calculados.",
            exit_strategy="El nivel de protección y el objetivo están en la sección de Riesgo.",
            time_horizon="Volver a evaluar en un momento",
            snowflake=snowflake,
            score_breakdown=score_breakdown,
            vetos_applied=[],
            alpha_opportunity="No identificada",
            reports=reports,
            entry_price=None, stop_loss=None, target_price=None,
            risk_reward=None, position_size_pct=None,
            sector=sector,
            long_term_quality_score=None,
            quality_verdict="low",
            asymmetry_direction="equilibrado",
            asymmetry_strength="débil",
            is_compound_machine=False,
        )

    def _build_synthesis_message(self, ticker, reports, weighted_score) -> str:
        lines = [
            f"# Síntesis de Análisis Completo: {ticker}",
            f"**Composite Score Ponderado (pre-ajuste):** {weighted_score:.1f}/100",
            "",
        ]

        for name, report in reports.items():
            lines += [
                f"## Agente: {report.agent_name} — Score: {report.score:.0f}/100 — Conviction: {report.conviction}",
                f"**Análisis:** {report.analysis[:600]}..." if len(report.analysis) > 600 else f"**Análisis:** {report.analysis}",
                f"**Pros:** {' | '.join(report.pros[:3])}",
                f"**Cons:** {' | '.join(report.cons[:2])}",
                f"**Métricas clave:** {json.dumps(report.key_metrics, ensure_ascii=False)[:300]}",
                "",
            ]

        lines += [
            "---",
            "Sintetiza todos los reportes anteriores y genera la decisión de inversión final.",
            "Aplica los vetos automáticos si corresponde.",
            "Genera una tesis de inversión profesional y retorna el JSON especificado.",
        ]

        return "\n".join(lines)

    def _parse_json(self, text: str) -> dict:
        import re
        match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        return {}

    def _score_to_recommendation(self, score: float) -> str:
        if score >= THRESHOLDS["MUY ATRACTIVO"]:
            return "MUY ATRACTIVO"
        if score >= THRESHOLDS["ATRACTIVO"]:
            return "ATRACTIVO"
        if score >= THRESHOLDS["EN OBSERVACIÓN"]:
            return "EN OBSERVACIÓN"
        return "EVITAR"

    def _code_synthesis(self, ticker, reports, weighted_score, score_breakdown) -> dict:
        """Síntesis POR CÓDIGO (sin IA): arma tesis, fortalezas, riesgos y
        estrategia a partir de los reportes ya calculados. Devuelve la misma
        forma de dict que antes producía el LLM."""
        def sc(key):
            r = reports.get(key)
            return float(r.score) if r else 50.0

        fund = sc("fundamentals"); fut = sc("future"); tech = sc("technical")
        quality = (fund + fut) / 2

        risk_rep = reports.get("risk")
        cr = ((risk_rep.raw_data if risk_rep else {}) or {}).get("computed_risk", {}) or {}
        rr = cr.get("rr_ratio", 0) or 0
        entry = cr.get("current_price"); stop = cr.get("stop_suggested"); target = cr.get("target_suggested")

        rec = self._score_to_recommendation(weighted_score)
        conviction = "HIGH" if weighted_score >= 74 else "MEDIUM" if weighted_score >= 55 else "LOW"

        if quality >= 75:
            qphrase = "un negocio de calidad claramente por encima del promedio"
        elif quality >= 60:
            qphrase = "un negocio sólido, con fortalezas y algún punto a vigilar"
        elif quality >= 45:
            qphrase = "un negocio promedio, sin una ventaja estructural evidente"
        else:
            qphrase = "un negocio con debilidades estructurales que conviene mirar con cautela"

        fund_km = (reports.get("fundamentals").key_metrics if reports.get("fundamentals") else {}) or {}
        pe = fund_km.get("pe_ratio", "N/A")
        stage_txt = (reports.get("technical").key_metrics or {}).get("stage", "") if reports.get("technical") else ""

        para1 = (
            f"Según los datos, {ticker} luce como {qphrase} (calidad estructural {quality:.0f}/100, "
            f"fundamentales {fund:.0f} y futuro {fut:.0f}). En valoración, su P/E es {pe}. El puntaje "
            f"compuesto ponderado queda en {weighted_score:.0f}/100, lo que la ubica como «{rec}»."
        )
        if rr and entry and stop and target:
            para2 = (
                f"En el corto plazo, el gráfico está en {stage_txt or 'tendencia mixta'} y la relación "
                f"riesgo/beneficio es {rr:.1f}:1 (entrada ~${entry:.2f}, protección ~${stop:.2f}, "
                f"objetivo ~${target:.2f})."
            )
        else:
            para2 = (
                f"En el corto plazo, el gráfico está en {stage_txt or 'tendencia mixta'}."
            )
        thesis = para1 + "\n\n" + para2

        dims = [(k, reports.get(k)) for k in
                ("fundamentals", "future", "technical", "institutional", "macro", "catalysts", "risk")
                if reports.get(k)]
        strengths = []
        for k, r in sorted(dims, key=lambda kv: kv[1].score, reverse=True):
            if r.score >= 55 and r.pros:
                strengths.append(r.pros[0])
            if len(strengths) >= 3:
                break
        if not strengths:
            strengths = [r.pros[0] for _, r in dims if r.pros][:3]

        risks = []
        for k, r in sorted(dims, key=lambda kv: kv[1].score):
            if r.cons:
                risks.append(r.cons[0])
            if len(risks) >= 3:
                break

        if entry and stop and target:
            entry_strategy = (f"Zona de entrada de referencia alrededor de ${entry:.2f}. Evita comprar en plena "
                              f"euforia; puedes considerar entradas escalonadas.")
            exit_strategy = (f"Nivel de protección defensiva en ${stop:.2f} y precio objetivo de referencia en "
                             f"${target:.2f} para tomar beneficios.")
        else:
            entry_strategy = "Revisa la sección de Riesgo para los niveles de entrada calculados."
            exit_strategy = "El nivel de protección y el objetivo están en la sección de Riesgo."

        time_horizon = ("años (negocio de calidad para mantener)" if quality >= 70 else
                        "meses" if weighted_score >= 55 else "esperar un mejor punto de entrada")

        if rr >= 2:
            alpha = (f"Asimetría favorable: el potencial al alza (~{cr.get('reward_pct', 0):.0f}%) supera al "
                     f"riesgo (~{cr.get('risk_pct', 0):.0f}%).")
        elif quality >= 75:
            alpha = "La oportunidad está en la calidad estructural del negocio para mantener a largo plazo."
        else:
            alpha = "No se identifica una asimetría clara; el caso depende del precio de entrada."

        return {
            "composite_score": weighted_score,
            "recommendation": rec,
            "conviction_level": conviction,
            "investment_thesis": thesis,
            "key_strengths": strengths[:3],
            "key_risks": risks[:3],
            "entry_strategy": entry_strategy,
            "exit_strategy": exit_strategy,
            "time_horizon": time_horizon,
            "snowflake": self._default_snowflake(reports),
            "score_breakdown": score_breakdown,
            "vetos_applied": [],
            "alpha_opportunity": alpha,
        }

    def _default_snowflake(self, reports) -> dict:
        """Snowflake por defecto si el orquestador no lo retorna."""
        fund = reports.get("fundamentals")
        tech = reports.get("technical")
        fut = reports.get("future")

        def sub(report, keys, max_sum):
            if not report:
                return 10.0
            total = sum(report.sub_scores.get(k, max_sum / len(keys)) for k in keys)
            return min(total / max_sum * 20, 20)

        return {
            "value":    sub(fund, ["valuation"], 25),
            "quality":  sub(fund, ["quality", "financial_health"], 50),
            "growth":   sub(fund, ["growth"], 25),
            "momentum": sub(tech, ["trend_quality", "momentum"], 66),
            "future":   sub(fut, ["moat_quality", "growth_runway"], 50),
        }

    def _fallback_synthesis(self, ticker, reports, weighted_score, score_breakdown) -> dict:
        """Síntesis de respaldo si el Orquestador falla."""
        recommendation = self._score_to_recommendation(weighted_score)
        return {
            "composite_score":   weighted_score,
            "recommendation":    recommendation,
            "conviction_level":  "MEDIUM",
            "investment_thesis": (
                "No pudimos armar la tesis completa en este momento, pero cada sección "
                "del análisis sí calculó su puntaje. Te sugerimos revisar los puntajes "
                "por categoría más abajo y volver a correr el análisis en un rato para "
                "ver la lectura completa."
            ),
            "key_strengths":     [r.pros[0] for r in reports.values() if r.pros][:3],
            "key_risks":         [r.cons[0] for r in reports.values() if r.cons][:3],
            "entry_strategy":    "Revisa la sección de Riesgo más abajo para los niveles de entrada.",
            "exit_strategy":     "El nivel de protección y el precio objetivo están en la sección de Riesgo.",
            "time_horizon":      "3-12 meses",
            "snowflake":         self._default_snowflake(reports),
            "score_breakdown":   score_breakdown,
            "vetos_applied":     [],
            "alpha_opportunity": "Revisa cada sección del análisis para ver el detalle.",
        }
