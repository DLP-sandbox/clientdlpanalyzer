# -*- coding: utf-8 -*-
"""
Base de conocimiento competitivo estática.

Cubre ~120 tickers frecuentemente analizados. Para cada ticker define:
- peers:            tickers de competidores directos (para comparación de métricas en vivo)
- market_position:  posición relativa en el mercado (leader / challenger / follower / niche)
- main_edge:        ventaja competitiva principal, en lenguaje de negocio
- main_threat:      riesgo competitivo más relevante
- sector_context:   dinámica del sector que da contexto a los pros/cons

Uso: competitive_intel.get_context("AMD") → dict con toda la info
"""

LANDSCAPE: dict[str, dict] = {

    # ── SEMICONDUCTORES ─────────────────────────────────────────────────────
    "NVDA": {
        "peers": ["AMD", "INTC", "AVGO", "QCOM"],
        "market_position": "leader",
        "main_edge": "Ecosistema CUDA/software crea switching costs altísimos; los data centers no pueden reemplazar NVDA sin reescribir millones de líneas de código",
        "main_threat": "AMD mejora ROCm; hyperscalers (Google TPU, Amazon Trainium, Meta MTIA) desarrollan chips propios reduciendo dependencia",
        "sector_context": "GPU de IA es el activo más escaso del ciclo tecnológico actual; NVDA captura ~80-85% del mercado de aceleradores",
    },
    "AMD": {
        "peers": ["NVDA", "INTC", "QCOM", "AVGO"],
        "market_position": "challenger",
        "main_edge": "EPYC gana market share agresivo en servidores corporativos vs Intel; GPU MI-series compite en precio vs NVDA en ciertas cargas de trabajo",
        "main_threat": "NVDA mantiene ventaja de software (CUDA) que es muy difícil de erosionar; AMD es la segunda opción en IA para la mayoría de workflows críticos",
        "sector_context": "Semiconductores de IA en superciclo; AMD compite en CPU de servidores (ganando) y GPU de IA (perdiendo vs NVDA)",
    },
    "INTC": {
        "peers": ["AMD", "NVDA", "QCOM", "TSM"],
        "market_position": "follower",
        "main_edge": "Infraestructura de fabricación propia (IDM); contratos gubernamentales de seguridad nacional; presencia en PCs legacy muy amplia",
        "main_threat": "AMD le quita market share en servidores y laptops; TSMC supera a Intel en proceso de fabricación; NVDA domina el segmento de mayor crecimiento",
        "sector_context": "Intel en transición estratégica (IDM 2.0); perdió liderazgo tecnológico en fabricación frente a TSMC durante 2018-2024",
    },
    "TSM": {
        "peers": ["INTC", "SSNLF", "AMAT", "LRCX"],
        "market_position": "leader",
        "main_edge": "Único fabricante capaz de producir chips de 3nm y 2nm a escala; todos los diseñadores líderes (NVDA, AAPL, AMD) dependen de TSMC",
        "main_threat": "Riesgo geopolítico Taiwan/China; Intel y Samsung invierten agresivamente en recuperar terreno en fabricación avanzada",
        "sector_context": "Monopolio de facto en fabricación avanzada de semiconductores; no hay sustituto real a 3-5 años",
    },
    "AVGO": {
        "peers": ["QCOM", "NVDA", "MRVL", "TXN"],
        "market_position": "leader",
        "main_edge": "Chips de conectividad de red (switching/routing) críticos para data centers; ASICs personalizados para Google, Meta, Apple con contratos multi-año",
        "main_threat": "Clientes hyperscaler podrían internalizar diseño de chips si mercado crece suficiente",
        "sector_context": "Broadcom domina infraestructura de red de data centers; negocio de software (VMware) añade recurrencia",
    },
    "QCOM": {
        "peers": ["AMD", "NVDA", "MRVL", "INTC"],
        "market_position": "leader",
        "main_edge": "Domina modems celulares 5G (prácticamente monopolio); licencias de patentes de tecnología celular generan ingresos recurrentes altísimos",
        "main_threat": "Apple desarrolla su propio modem (C1); Android compite con chips propios de MediaTek y Samsung en gama media",
        "sector_context": "Negocio de licencias es el verdadero moat; chips Snapdragon compiten en el segmento premium de Android",
    },
    "ASML": {
        "peers": ["KLAC", "AMAT", "LRCX", "NIKON"],
        "market_position": "leader",
        "main_edge": "Monopolio absoluto en máquinas EUV (litografía extrema ultravioleta); sin ASML no es posible fabricar chips de menos de 7nm",
        "main_threat": "China invierte masivamente en litografía propia pero está al menos 10 años atrás; regulaciones de exportación limitan pero no destruyen el negocio",
        "sector_context": "El activo estratégico más crítico en la cadena de semiconductores; backlog visible de 2-3 años",
    },
    "AMAT": {
        "peers": ["LRCX", "KLAC", "ASML", "TER"],
        "market_position": "leader",
        "main_edge": "Equipo de deposición y grabado más completo del mercado; cliente diversificado (no depende de TSMC solo)",
        "main_threat": "Restricciones de exportación a China reducen mercado direccionable; ciclo de capex de semiconductores es volátil",
        "sector_context": "Equipment de semiconductores va 1-2 ciclos adelante de la demanda de chips; exposición a China fue 30%+ de ingresos",
    },

    # ── MEGACAP TECH ────────────────────────────────────────────────────────
    "AAPL": {
        "peers": ["MSFT", "GOOGL", "SSNLF", "META"],
        "market_position": "leader",
        "main_edge": "Ecosistema Apple (iPhone/Mac/Watch/iPad/Services) genera switching costs altísimos; App Store y servicios son casi pure-profit",
        "main_threat": "Saturación del mercado de smartphones premium; regulaciones antimonopolio en App Store en EU y US",
        "sector_context": "Hardware premium + servicios de alto margen; China representa 20%+ de ingresos con riesgo geopolítico creciente",
    },
    "MSFT": {
        "peers": ["GOOGL", "AMZN", "ORCL", "CRM"],
        "market_position": "leader",
        "main_edge": "Azure compite en #2 de cloud con ventaja de integración con Office/Teams; Copilot embebe IA en los workflows empresariales más usados del mundo",
        "main_threat": "Google con Gemini compite en productividad empresarial; AWS mantiene liderazgo en cloud puro",
        "sector_context": "El mejor posicionado para monetizar IA empresarial dado su penetración en Office 365 y Azure",
    },
    "GOOGL": {
        "peers": ["MSFT", "META", "AMZN", "AAPL"],
        "market_position": "leader",
        "main_edge": "Monopolio de búsqueda (90%+ de market share) y YouTube; mejor infraestructura de IA con TPUs propias y DeepMind",
        "main_threat": "ChatGPT/Bing erosionan búsqueda en consultas complejas; regulación antimonopolio puede forzar desinversiones (Chrome, Android)",
        "sector_context": "Publicidad digital sigue siendo el mayor pool de beneficios; cloud (GCP) crece pero aún lejos de AWS y Azure",
    },
    "META": {
        "peers": ["SNAP", "PINS", "GOOGL", "TIKTOK"],
        "market_position": "leader",
        "main_edge": "3.2B+ usuarios activos diarios en Facebook/Instagram/WhatsApp — la red social más grande de la historia con efectos de red fortísimos",
        "main_threat": "TikTok captura tiempo de adolescentes; regulación de privacidad europea reduce targeting; Reality Labs (Metaverse) quema $15B/año sin retorno claro",
        "sector_context": "Meta Advantage+ (publicidad basada en IA) está recuperando eficiencia perdida post-iOS14; márgenes en expansión",
    },
    "AMZN": {
        "peers": ["MSFT", "GOOGL", "WMT", "SHOP"],
        "market_position": "leader",
        "main_edge": "AWS es el líder en cloud (#1 con ~33% de market share) con márgenes altísimos; Prime crea lealtad y subsidia logística",
        "main_threat": "Azure y GCP ganan share en cloud; Walmart+ compite en ecommerce de groceries; regulación antimonopolio de Marketplace",
        "sector_context": "AWS subsidia el ecommerce; el verdadero valor de Amazon es la infraestructura cloud y publicidad, no el retail",
    },

    # ── SOFTWARE ENTERPRISE ─────────────────────────────────────────────────
    "MSFT": {
        "peers": ["GOOGL", "AMZN", "ORCL", "CRM"],
        "market_position": "leader",
        "main_edge": "Azure + Office 365 + Teams crean dependencia empresarial masiva; Copilot embebe IA directamente en flujos de trabajo existentes",
        "main_threat": "Google Workspace crece en PyMEs; AWS mantiene ventaja técnica en cloud nativo",
        "sector_context": "Microsoft es el mejor posicionado para monetizar IA en enterprise; penetración de Copilot puede expandir ARR significativamente",
    },
    "CRM": {
        "peers": ["MSFT", "NOW", "ORCL", "SAP"],
        "market_position": "leader",
        "main_edge": "CRM (Customer Relationship Management) líder con 23%+ de market share; Agentforce (IA) puede monetizar la base instalada sin nuevo CAC",
        "main_threat": "Microsoft Dynamics compite con ventaja de bundling con Office; HubSpot gana en midmarket",
        "sector_context": "SaaS enterprise en transición hacia IA Agents; Salesforce tiene la base de datos de clientes y workflows más grande en ventas B2B",
    },
    "NOW": {
        "peers": ["CRM", "MSFT", "ORCL", "WDAY"],
        "market_position": "leader",
        "main_edge": "ServiceNow es el sistema operativo de workflows empresariales (ITSM, HRSD, CSM); switching costs extremadamente altos una vez integrado",
        "main_threat": "Microsoft 365 Copilot intenta automatizar workflows similares; Oracle y SAP compiten en enterprise",
        "sector_context": "El 'plumbing' de los workflows enterprise; cada vez más difícil de reemplazar a medida que se integra en más procesos",
    },
    "ADBE": {
        "peers": ["MSFT", "FIGMA", "CANVA", "CRM"],
        "market_position": "leader",
        "main_edge": "Adobe Creative Cloud tiene switching costs altísimos (curva de aprendizaje + ecosistema de archivos); Firefly es IA generativa nativa integrada",
        "main_threat": "Canva con IA amenaza en diseño profesional de gama media; Figma compite en diseño de producto; IA generativa puede comoditizar creación de contenido",
        "sector_context": "Adobe integra IA (Firefly) sin canibalizar su modelo de suscripción; apuesta es que aumenta el valor de la suite, no la sustituye",
    },
    "ORCL": {
        "peers": ["MSFT", "AMZN", "CRM", "SAP"],
        "market_position": "challenger",
        "main_edge": "Base de datos Oracle está embebida en sistemas críticos de Fortune 500 con switching costs altísimos; OCI crece rápido como alternativa de cloud",
        "main_threat": "AWS y Azure dominan cloud empresarial de nueva generación; migraciones a cloud nativo reducen dependencia de Oracle DB",
        "sector_context": "Oracle reinventa como plataforma de cloud para IA; contratos con hyperscalers para hospedar modelos de lenguaje grande son catalizador",
    },
    "SHOP": {
        "peers": ["AMZN", "BIGC", "WIX", "WDAY"],
        "market_position": "leader",
        "main_edge": "Plataforma de ecommerce líder para SMB/midmarket con ecosistema de apps y Shopify Payments integrado; operando system del comercio independiente",
        "main_threat": "Amazon Marketplace captura ventas que de otro modo irían a Shopify; TikTok Shop y ByteDance compiten en social commerce",
        "sector_context": "Shopify + Shopify Payments + Fulfillment crea el stack completo para comercio directo; GMV es el KPI real de su poder de mercado",
    },

    # ── CLOUD / DATA ─────────────────────────────────────────────────────────
    "SNOW": {
        "peers": ["DBRX", "PLTR", "MSFT", "GOOGL"],
        "market_position": "challenger",
        "main_edge": "Data cloud neutral que conecta datos entre clouds (AWS, Azure, GCP) sin lock-in; Marketplace de datos crea efectos de red entre clientes",
        "main_threat": "Microsoft Fabric y Google BigQuery compiten con ventaja de bundling; Databricks (privado) gana terreno en ML/AI workloads",
        "sector_context": "Snowflake compite en el layer de datos donde Microsoft y Google tienen ventaja de bundling con sus respectivos clouds",
    },
    "PLTR": {
        "peers": ["MSFT", "IBM", "SNOW", "DDOG"],
        "market_position": "niche",
        "main_edge": "Palantir AIP integra IA operacional en workflows de defensa y gobierno con acceso privilegiado a datos clasificados y contratos plurianuales",
        "main_threat": "Contratos gubernamentales son competitivos y pueden ser revocados; empresa privada dominada por el CEO puede generar riesgos de governance",
        "sector_context": "Posición única en defensa/inteligencia (Gotham) + expansión comercial (Foundry/AIP); pocas empresas tienen acceso similar a datos de defensa",
    },
    "DDOG": {
        "peers": ["SPLK", "DYNT", "NR", "MSFT"],
        "market_position": "leader",
        "main_edge": "Plataforma de observabilidad unificada (métricas, logs, trazas) con la mayor cobertura de integraciones del mercado",
        "main_threat": "Grafana open source reduce el coste; Microsoft Azure Monitor + Sentinel compiten con bundling",
        "sector_context": "Observabilidad es infraestructura crítica en DevOps; Datadog tiene el mayor market share en empresas cloud-native",
    },
    "NET": {
        "peers": ["FSLY", "AKAM", "PALO", "ZS"],
        "market_position": "challenger",
        "main_edge": "Red global de edge computing y seguridad Zero Trust (SASE) construida sobre un backbone propio muy eficiente; Workers platform para compute en el edge",
        "main_threat": "Akamai y Fastly compiten en CDN; Zscaler y Palo Alto compiten en Zero Trust; AWS/Azure tienen sus propios edge services",
        "sector_context": "Cloudflare compite en múltiples mercados (CDN, seguridad, DNS) con la estrategia de expandir funciones en su red existente",
    },
    "CRWD": {
        "peers": ["PANW", "S", "MSFT", "ZS"],
        "market_position": "leader",
        "main_edge": "Falcon platform unifica endpoint, cloud, identity security en una sola plataforma con el mayor threat intel feed del mercado (1T+ events/día)",
        "main_threat": "Microsoft Defender integrado en Windows reduce el TAM de endpoint; outage de julio 2024 dañó reputación pero no arquitectura",
        "sector_context": "Seguridad es gasto no discrecional; plataformización (menos vendors, más módulos) favorece a CrowdStrike y Palo Alto",
    },

    # ── FINTECH / PAGOS ─────────────────────────────────────────────────────
    "V": {
        "peers": ["MA", "PYPL", "AXP", "SQ"],
        "market_position": "leader",
        "main_edge": "Red de pago más grande del mundo (4B+ tarjetas, 130M+ comercios) con efectos de red de dos lados prácticamente irreplicables",
        "main_threat": "Redes de pago nacionales en China (UnionPay) y posibles CBDCs reducen dependencia de redes Visa/MC; regulación de interchange en US",
        "sector_context": "Duopolio Visa/Mastercard en pagos internacionales; el volumen de transacciones crece con GDP global sin inversión de capital material",
    },
    "MA": {
        "peers": ["V", "AXP", "PYPL", "SQ"],
        "market_position": "leader",
        "main_edge": "Misma fortaleza que Visa en red de pagos con ventaja en servicios de valor añadido (analytics, ciberseguridad) para bancos emisores",
        "main_threat": "Idénticos a Visa: CBDCs, regulación de interchange, expansión de sistemas alternativos en mercados emergentes",
        "sector_context": "Mastercard tiene mayor exposición internacional que Visa y más ingresos de servicios; ambas son proxies del crecimiento del consumo global",
    },
    "PYPL": {
        "peers": ["V", "MA", "SQ", "AFRM"],
        "market_position": "challenger",
        "main_edge": "PayPal + Venmo tienen 400M+ cuentas activas y son el método de pago preferido en checkout digital en US/Europa",
        "main_threat": "Apple Pay y Google Pay eroden share en mobile; Visa/MC compiten directamente en checkout digital; Affirm en BNPL",
        "sector_context": "PayPal perdió la narrativa de alto crecimiento; apuesta actual es rentabilidad y recompras más que expansión de usuarios",
    },
    "SQ": {
        "peers": ["PYPL", "V", "TOAST", "AFRM"],
        "market_position": "challenger",
        "main_edge": "Square (comercios) + Cash App (consumidores) crean ecosistema financiero vertical para el segmento desbancarizado y PyMEs",
        "main_threat": "Cash App enfrenta competencia de Chime, Venmo y Zelle de bancos; Square compite con Toast en restaurantes y Stripe en ecommerce",
        "sector_context": "Block/Square en fase de expansión de márgenes después de crecer agresivamente; Bitcoin es exposición especulativa que distorsiona los fundamentales",
    },

    # ── CONSUMO / RETAIL ────────────────────────────────────────────────────
    "AMZN": {
        "peers": ["WMT", "MSFT", "GOOGL", "SHOP"],
        "market_position": "leader",
        "main_edge": "AWS ($100B+ revenue) financia el ecommerce; Prime crea lealtad masiva; publicidad digital crece al 20%+ con márgenes de 50%+",
        "main_threat": "Azure y GCP ganan share en cloud empresarial; Walmart+ y TikTok Shop compiten en ecommerce",
        "sector_context": "Amazon es realmente 3 negocios: cloud (AWS), marketplace/retail, publicidad — los tres entre los mejores de su clase",
    },
    "WMT": {
        "peers": ["AMZN", "COST", "TGT", "DLTR"],
        "market_position": "leader",
        "main_edge": "Mayor retailer del mundo por volumen; cadena de suministro única con poder de negociación con proveedores; Walmart+ y publicidad digital en expansión",
        "main_threat": "Amazon Prime captura el gasto no-alimentario; Aldi/Lidl compiten en precio en groceries; márgenes bajos del retail dejan poca holgura",
        "sector_context": "Walmart reinventándose como plataforma (Marketplace, publicidad, servicios financieros) no solo como retailer",
    },
    "COST": {
        "peers": ["WMT", "TGT", "BJ", "AMZN"],
        "market_position": "leader",
        "main_edge": "Modelo de membresía crea flujo de caja recurrente casi puro-beneficio; lealtad extrema (92%+ renovación de membresías)",
        "main_threat": "Amazon Prime ofrece conveniencia superior en no-alimentos; club stores tienen saturación geográfica en US",
        "sector_context": "Costco es el retailer con mejor unit economics del mundo; el margen operativo bajo es engañoso porque ignora la membresía como beneficio",
    },
    "TSLA": {
        "peers": ["GM", "F", "RIVN", "NIO", "BYD"],
        "market_position": "leader",
        "main_edge": "Software OTA, autonomía de conducción (FSD) y red Supercharger son ventajas que los OEMs tradicionales tardan años en replicar",
        "main_threat": "BYD supera a Tesla en volumen global de EVs; GM y Ford aceleran electrificación; competencia china presiona márgenes en Asia",
        "sector_context": "Tesla está en transición de productor de hardware (EVs) a plataforma de software (FSD, Energía, Optimus); la valoración refleja el escenario optimista",
    },

    # ── SALUD ────────────────────────────────────────────────────────────────
    "LLY": {
        "peers": ["NVO", "ABBV", "MRK", "PFE"],
        "market_position": "leader",
        "main_edge": "Tirzepatide (Mounjaro/Zepbound) lidera el mercado GLP-1 más grande en la historia farmacéutica; pipeline oncológico y Alzheimer como segunda ola",
        "main_threat": "Novo Nordisk (Ozempic/Wegovy) compite directamente en GLP-1; presión política sobre precios de medicamentos en US",
        "sector_context": "El mercado GLP-1 puede alcanzar $150B+ en 2030; Lilly y Novo Nordisk comparten un duopolio con un TAM sin precedentes",
    },
    "NVO": {
        "peers": ["LLY", "ABBV", "RHHBY", "MRK"],
        "market_position": "leader",
        "main_edge": "Ozempic y Wegovy pioneros del mercado GLP-1; 30+ años de experiencia en diabetes con relaciones establecidas con endocrinólogos",
        "main_threat": "Lilly con tirzepatide muestra mejores resultados clínicos en pérdida de peso; capacidad de fabricación es el cuello de botella real",
        "sector_context": "Novo Nordisk vs Lilly es el duopolio más lucrativo de la historia reciente en farma; ambas compiten por capacidad de fabricación",
    },
    "UNH": {
        "peers": ["CVS", "CI", "HUM", "ELV"],
        "market_position": "leader",
        "main_edge": "Mayor aseguradora de salud de US + Optum (servicios de salud) crea integración vertical única; datos de 50M+ pacientes son ventaja competitiva duradera",
        "main_threat": "Regulación gubernamental de precios de seguros; DOJ investiga prácticas de UnitedHealth; CMS reduce tasas de Medicare Advantage",
        "sector_context": "Managed care es oligopolio regulado; UnitedHealth tiene las mejores economías de escala y la integración más avanzada con Optum",
    },

    # ── ENERGÍA / MATERIAS PRIMAS ───────────────────────────────────────────
    "XOM": {
        "peers": ["CVX", "SHEL", "BP", "TTE"],
        "market_position": "leader",
        "main_edge": "Mayor integrada de petróleo occidental; balance más sólido del sector; Guyana como activo de bajo coste y alto retorno para la próxima década",
        "main_threat": "Transición energética reduce demanda de largo plazo; precio del petróleo es la variable dominante fuera del control de la empresa",
        "sector_context": "Supermayores operan como oligopolio de facto en upstream; ExxonMobil prioriza retornos de capital sobre crecimiento de producción",
    },
    "CVX": {
        "peers": ["XOM", "SHEL", "COP", "OXY"],
        "market_position": "challenger",
        "main_edge": "TCO (Kazajistán) + Permian Basin dan décadas de producción de bajo coste; balance conservador con menor apalancamiento que peers",
        "main_threat": "Integración de Hess (bloqueada parcialmente por arbitraje con ExxonMobil); mismo riesgo estructural de transición energética que el sector",
        "sector_context": "Chevron compite con ExxonMobil por ser la integrada occidental de referencia; diferencia real está en activos específicos (Guyana vs TCO)",
    },

    # ── FINANCIERO ───────────────────────────────────────────────────────────
    "JPM": {
        "peers": ["BAC", "GS", "MS", "WFC"],
        "market_position": "leader",
        "main_edge": "El banco más grande y diversificado de US; tecnología bancaria más avanzada del sector (Chase app, pagos instantáneos); CEO Jamie Dimon es un diferenciador",
        "main_threat": "Fintechs erosionan servicios de alto margen (pagos, préstamos personales); tasas altas comprimen márgenes netos de interés si bajan",
        "sector_context": "JPMorgan combina banca de inversión (GS compite), banca comercial (BAC compite) y banca de consumo con escala única",
    },
    "GS": {
        "peers": ["MS", "JPM", "BLK", "BAC"],
        "market_position": "leader",
        "main_edge": "Marca de élite en banca de inversión M&A y mercados de capital; cultura de talento que autoselecciona a los mejores del sector financiero",
        "main_threat": "Retirada del consumo (fracaso de Marcus) costó $12B+; regulación de capital de Basilea III reduce retornos de trading",
        "sector_context": "Goldman Sachs vuelve al core de banca de inversión y gestión de activos tras la aventura fallida en consumo",
    },
    "V": {
        "peers": ["MA", "PYPL", "AXP", "FIS"],
        "market_position": "leader",
        "main_edge": "Red de pagos de dos lados con 4B+ tarjetas y 130M+ comercios; activo de infraestructura que crece con el PIB global sin capex significativo",
        "main_threat": "CBDCs y wallets móviles de ecosistemas cerrados (WeChat, Alipay, Apple Pay) pueden bypassear la red en mercados específicos",
        "sector_context": "Visa y Mastercard son infraestructura financiera global; no prestan dinero, solo procesan pagos cobrando basis points sobre cada transacción",
    },

    # ── TELECOS / MEDIA ──────────────────────────────────────────────────────
    "NFLX": {
        "peers": ["DIS", "WBD", "PARA", "AMZN"],
        "market_position": "leader",
        "main_edge": "200M+ suscriptores con contenido original en 30+ idiomas; algoritmo de recomendación y datos de viewing son ventaja difícil de replicar",
        "main_threat": "Disney+ (IP de Marvel/Star Wars) + Max (HBO) compiten por tiempo y presupuesto; mercados emergentes son los próximos 200M pero con ARPU bajo",
        "sector_context": "Netflix logró rentabilidad sostenida; ahora la batalla es por el tiempo del usuario vs YouTube, TikTok y gaming",
    },
    "DIS": {
        "peers": ["NFLX", "WBD", "PARA", "CMCSA"],
        "market_position": "challenger",
        "main_edge": "IP sin igual (Marvel, Star Wars, Pixar, Disney Classic) + parques temáticos con ARPU altísimo + ESPN como último activo de TV lineal relevante",
        "main_threat": "Disney+ crece pero pierde dinero; streaming canibaliza ingresos de TV; Bob Iger resolvió la crisis de governance pero el modelo de negocio sigue bajo presión",
        "sector_context": "Disney en transición de media company a IP + experiencias; el modelo antiguo (cable TV + taquilla) se está deteriorando",
    },

    # ── INDUSTRIALES / DEFENSA ───────────────────────────────────────────────
    "LMT": {
        "peers": ["RTX", "NOC", "GD", "BA"],
        "market_position": "leader",
        "main_edge": "F-35 es el programa de defensa más grande de la historia (10,000+ aviones pendientes de entrega); backlog de $160B+ asegura revenue por una década",
        "main_threat": "Programa F-35 bajo presión política por coste; ciclos de presupuesto de defensa son políticos; supply chain de componentes críticos",
        "sector_context": "Gasto de defensa en máximos históricos por conflictos en Europa y tensión US-China; Lockheed es el mayor contratista de defensa del mundo",
    },

    # ── CONSUMO DISCRECIONAL ─────────────────────────────────────────────────
    "NKE": {
        "peers": ["ADDYY", "UAA", "LULU", "SKX"],
        "market_position": "leader",
        "main_edge": "Brand Nike es uno de los 5 más valiosos del mundo; Jordan Brand y DTC (Direct-to-Consumer) generan márgenes superiores al canal wholesale",
        "main_threat": "On Running, Hoka y nuevas marcas de performance erosionan segmento running premium; canal DTC requiere inversión masiva",
        "sector_context": "Nike reinventa su canal de distribución (más DTC, menos wholesalers); corto plazo doloroso, largo plazo con mejores márgenes",
    },
    "LULU": {
        "peers": ["NKE", "ADDYY", "UAA", "GPS"],
        "market_position": "leader",
        "main_edge": "Athleisure premium con comunidad de marca muy leal; márgenes brutos de 57%+ excepcionales para retail; expansión internacional aún en fases tempranas",
        "main_threat": "Saturación del mercado norteamericano; competencia de Alo Yoga y Vuori en el segmento premium",
        "sector_context": "Lululemon inventó y domina el athleisure premium; desafío es replicar el éxito de NA en China y Europa sin diluir la marca",
    },

    # ── INFRAESTRUCTURA DIGITAL ──────────────────────────────────────────────
    "EQIX": {
        "peers": ["DLR", "AMT", "CCI", "SBAC"],
        "market_position": "leader",
        "main_edge": "Mayor operador de data centers colocados del mundo (250+ IBX en 70 países); Network hub effects: cuanto más interconectados, más difícil de abandonar",
        "main_threat": "Hyperscalers (AWS, Azure, GCP) construyen sus propios data centers reduciendo necesidad de colocación en algunos segmentos",
        "sector_context": "Equinix compite en colocación y interconexión, no en cloud puro; modelo REIT con dividendo creciente y contratos de largo plazo",
    },
    "AMT": {
        "peers": ["CCI", "SBAC", "EQIX", "VNET"],
        "market_position": "leader",
        "main_edge": "220,000+ torres de telecomunicaciones en 25 países; contratos de 10-15 años con telecos indexados a inflación crean flujo de caja muy predecible",
        "main_threat": "Densificación de 5G con small cells puede reducir importancia de torres macro; consolidación de telecos reduce número de arrendatarios",
        "sector_context": "American Tower es el REIT de infraestructura de comunicaciones más grande; tesis es que 5G requiere más densidad, no menos torres",
    },

    # ── BIOTECH / FARMA ──────────────────────────────────────────────────────
    "ABBV": {
        "peers": ["LLY", "MRK", "BMY", "AMGN"],
        "market_position": "challenger",
        "main_edge": "AbbVie diversificó post-Humira con Skyrizi y Rinvoq (inmunología) + Allergan (botox/aesthetics) reduciendo dependencia de un solo producto",
        "main_threat": "Humila perdió exclusividad 2023 → biosimilares erosionan ingresos; Skyrizi/Rinvoq deben compensar más de $20B de caída de Humira",
        "sector_context": "AbbVie es el caso de estudio de transición post-blockbuster; el éxito de Skyrizi define si puede mantener su posición como top-10 farma",
    },
    "MRK": {
        "peers": ["LLY", "ABBV", "BMY", "PFE"],
        "market_position": "leader",
        "main_edge": "Keytruda (pembrolizumab) es el oncológico más prescrito del mundo con indicaciones en +30 tipos de cáncer; pipeline oncológico y vacunas robusto",
        "main_threat": "Keytruda pierde patente en 2028; necesita 2-3 blockbusters del pipeline para compensar; competencia de Bristol-Myers en checkpoint inhibitors",
        "sector_context": "Merck apuesta todo a Keytruda + pipeline; la pregunta es si pueden innovar suficientemente rápido antes del cliff de patente",
    },
}


def get_context(ticker: str):
    """Devuelve el contexto competitivo del ticker o None si no está en la base de datos."""
    return LANDSCAPE.get(ticker.upper())


def get_peers(ticker: str) -> list[str]:
    """Devuelve lista de tickers competidores para comparación de métricas."""
    ctx = get_context(ticker)
    return ctx["peers"] if ctx else []
