from fastapi import FastAPI
import time
import requests
import os
import json
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

SERP_API_KEY = os.getenv("SERP_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Company Revenue App"
    }
)

CACHE = {
    "search": {},
    "exchange": {"rate": None, "time": 0},
    "company": {}
}

CACHE_TTL = 3600

# ----------------------------
# UTILS
# ----------------------------
def safe_json_parse(content):
    """Extrai e parseia o primeiro objeto JSON encontrado no texto."""
    try:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        json_str = match.group(0).replace("\n", "").replace("'", '"')
        return json.loads(json_str)
    except Exception as e:
        print(f"[JSON PARSE ERROR] {e} | RAW: {content[:200]}")
        return None

def extract_revenue_fallback(text):
    """Tenta extrair receita via regex quando o AI falha."""
    patterns = [
        r"\$\s?([\d,\.]+)\s?(billion|million|trillion)",
        r"revenue of\s?\$?([\d,\.]+)\s?(billion|million|trillion)",
        r"([\d,\.]+)\s?(billion|million|trillion)\s?(in revenue|revenue)",
    ]
    multipliers = {"million": 1_000_000, "billion": 1_000_000_000, "trillion": 1_000_000_000_000}

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value = float(match.group(1).replace(",", ""))
                unit = match.group(2).lower()
                return value * multipliers.get(unit, 1)
            except Exception:
                continue
    return None

# ----------------------------
# INDÚSTRIA / CLASSIFICAÇÃO
# ----------------------------
INDUSTRY_MAP = {
    "retail": "Varejo",
    "technology": "Tecnologia",
    "software": "Software",
    "saas": "SaaS",
    "finance": "Financeiro",
    "banking": "Bancário",
    "industrial": "Industrial",
    "manufacturing": "Manufatura",
    "services": "Serviços",
    "health": "Saúde",
    "energy": "Energia",
    "telecom": "Telecomunicações",
    "e-commerce": "E-commerce",
}

def translate_industry(industry):
    if not industry:
        return None
    industry_lower = industry.lower()
    for key, value in INDUSTRY_MAP.items():
        if key in industry_lower:
            return value
    return industry

def classify_company(revenue):
    if not revenue:
        return "Desconhecido"
    if revenue < 360_000:
        return "Microempresa"
    elif revenue < 4_800_000:
        return "Pequena empresa"
    elif revenue < 300_000_000:
        return "Média empresa"
    elif revenue < 1_000_000_000:
        return "Grande empresa"
    else:
        return "Enterprise"

# ----------------------------
# CÂMBIO
# ----------------------------
def get_usd_brl_rate():
    now = time.time()
    if CACHE["exchange"]["rate"] and now - CACHE["exchange"]["time"] < CACHE_TTL:
        return CACHE["exchange"]["rate"]
    try:
        res = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()
        rate = res["rates"]["BRL"]
        CACHE["exchange"] = {"rate": rate, "time": now}
        return rate
    except Exception as e:
        print(f"[CÂMBIO ERROR] {e}")
        return 5.7  # fallback fixo se API cair

def convert_to_brl(usd_value):
    rate = get_usd_brl_rate()
    return usd_value * rate if (rate and usd_value) else None

def format_brl(value):
    if not value:
        return None
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ----------------------------
# AI
# ----------------------------
def call_ai(prompt):
    models = [
        "meta-llama/llama-3-8b-instruct",
        "google/gemma-7b-it",
        "mistralai/mistral-7b-instruct"
    ]
    for model in models:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                timeout=15,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[AI ERROR] {model}: {e}")
            time.sleep(1)
    return None

def calculate_confidence(data, text):
    if not data:
        return 0.1

    score = 0.0

    # base: veio do AI com parsing correto ou foi fallback regex?
    if data.get("estimated"):
        score += 0.2  # fallback — baixa confiança base
    else:
        score += 0.5  # AI extraiu com sucesso

    # bônus por dados completos
    if data.get("revenue"):
        score += 0.1
    if data.get("employees"):
        score += 0.1
    if data.get("industry"):
        score += 0.05

    # bônus por fonte confiável
    trusted_sources = ["forbes", "statista", "bloomberg", "reuters", "yahoo finance"]
    matches = sum(1 for s in trusted_sources if s in text.lower())
    score += min(matches * 0.05, 0.15)  # máx +0.15, não compensa tudo

    return round(max(0.0, min(score, 1.0)), 2)

# ----------------------------
# SEARCH
# ----------------------------
def search_company_data(company):
    now = time.time()
    if company in CACHE["search"]:
        cached = CACHE["search"][company]
        if now - cached["time"] < CACHE_TTL:
            return cached["data"]

    try:
        res = requests.get(
            "https://serpapi.com/search",
            params={"q": f"{company} revenue employees 2025", "api_key": SERP_API_KEY},
            timeout=10
        ).json()
        snippets = [r.get("snippet", "") for r in res.get("organic_results", [])[:5]]
        text = " ".join(snippets)
    except Exception as e:
        print(f"[SERP ERROR] {company}: {e}")
        text = ""

    CACHE["search"][company] = {"data": text, "time": now}
    return text

# ----------------------------
# EXTRACTION
# ----------------------------
def extract_or_estimate(company, text):
    if not text or len(text) < 50:
        return None

    prompt = f"""Return ONLY a JSON object, no explanation, no markdown.

Company: {company}

Extract from the text:
- revenue (number in USD, no symbols)
- employees (number)
- industry (string in English)
- estimated (true if you're guessing, false if from text)

TEXT:
{text}
"""
    content = call_ai(prompt)
    if content:
        parsed = safe_json_parse(content)
        if parsed:
            return parsed

    # fallback regex
    revenue = extract_revenue_fallback(text)
    return {
        "revenue": revenue,
        "estimated": True,
        "employees": None,
        "industry": None,
    }

# ----------------------------
# CORE
# ----------------------------
def get_company(company: str):
    now = time.time()
    if company in CACHE["company"]:
        cached = CACHE["company"][company]
        if now - cached["time"] < CACHE_TTL:
            return cached["data"]

    try:
        text_data = search_company_data(company)
        data = extract_or_estimate(company, text_data)

        revenue_usd = data.get("revenue") if data else None
        revenue_brl = convert_to_brl(revenue_usd)

        response = {
            "empresa": company,
            "faturamento_usd": revenue_usd,
            "faturamento_brl": format_brl(revenue_brl),
            "estimado": data.get("estimated", True) if data else True,
            "funcionarios": data.get("employees") if data else None,
            "industria": translate_industry(data.get("industry") if data else None),
            "confianca": calculate_confidence(data, text_data),
            "classificacao": classify_company(revenue_brl),
        }

        CACHE["company"][company] = {"data": response, "time": now}
        return response

    except Exception as e:
        print(f"[COMPANY ERROR] {company}: {e}")
        return {"empresa": company, "erro": str(e)}

# ----------------------------
# ENDPOINTS
# ----------------------------
@app.get("/company")
def company_endpoint(company: str):
    return get_company(company)

@app.post("/batch")
def batch(companies: list[str]):
    # processa em paralelo — muito mais rápido
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(get_company, companies))
    return results