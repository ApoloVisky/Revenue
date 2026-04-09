from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from supabase import create_client
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import time, requests, os, json, re, csv, io
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ----------------------------
# RATE LIMITER
# ----------------------------
limiter = Limiter(key_func=get_remote_address)

app = FastAPI()
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# SUPABASE
# ----------------------------
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# ----------------------------
# AUTH
# ----------------------------
api_key_header = APIKeyHeader(name="X-API-Key")

def get_current_user(api_key: str = Depends(api_key_header)):
    res = supabase.table("users").select("*").eq("api_key", api_key).execute()
    if not res.data:
        raise HTTPException(status_code=403, detail="API key inválida")
    user = res.data[0]
    if user["credits_used"] >= user["credits_limit"]:
        raise HTTPException(status_code=429, detail=f"Limite de créditos atingido. Plano atual: {user['plan']}")
    return user

def consume_credit(user_id: str):
    supabase.rpc("increment_credits", {"user_id_input": user_id}).execute()

def log_search(user_id: str, companies: list):
    try:
        supabase.table("search_logs").insert({
            "user_id": user_id,
            "companies": companies,
            "count": len(companies),
        }).execute()
    except Exception as e:
        print(f"[LOG ERROR] {e}")

# ----------------------------
# CLIENTES E CONFIG
# ----------------------------
SERP_API_KEY = os.getenv("SERP_API_KEY")

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
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
    try:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        json_str = match.group(0).replace("\n", "").replace("'", '"')
        return json.loads(json_str)
    except Exception as e:
        print(f"[JSON PARSE ERROR] {e}")
        return None

def extract_revenue_fallback(text):
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
            except:
                continue
    return None

INDUSTRY_MAP = {
    "retail": "Varejo", "technology": "Tecnologia", "software": "Software",
    "saas": "SaaS", "finance": "Financeiro", "banking": "Bancário",
    "industrial": "Industrial", "manufacturing": "Manufatura",
    "services": "Serviços", "health": "Saúde", "energy": "Energia",
    "telecom": "Telecomunicações", "e-commerce": "E-commerce",
}

def translate_industry(industry):
    if not industry:
        return None
    for key, value in INDUSTRY_MAP.items():
        if key in industry.lower():
            return value
    return industry

def classify_company(revenue):
    if not revenue:
        return "Desconhecido"
    if revenue < 360_000: return "Microempresa"
    elif revenue < 4_800_000: return "Pequena empresa"
    elif revenue < 300_000_000: return "Média empresa"
    elif revenue < 1_000_000_000: return "Grande empresa"
    else: return "Enterprise"

def get_usd_brl_rate():
    now = time.time()
    if CACHE["exchange"]["rate"] and now - CACHE["exchange"]["time"] < CACHE_TTL:
        return CACHE["exchange"]["rate"]
    try:
        res = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()
        rate = res["rates"]["BRL"]
        CACHE["exchange"] = {"rate": rate, "time": now}
        return rate
    except:
        return 5.7

def convert_to_brl(usd_value):
    rate = get_usd_brl_rate()
    return usd_value * rate if (rate and usd_value) else None

def format_brl(value):
    if not value:
        return None
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def call_ai(prompt):
    models = ["meta-llama/llama-3-8b-instruct", "google/gemma-7b-it", "mistralai/mistral-7b-instruct"]
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
    score = 0.2 if data.get("estimated") else 0.5
    if data.get("revenue"): score += 0.1
    if data.get("employees"): score += 0.1
    if data.get("industry"): score += 0.05
    trusted = ["forbes", "statista", "bloomberg", "reuters", "yahoo finance"]
    matches = sum(1 for s in trusted if s in text.lower())
    score += min(matches * 0.05, 0.15)
    return round(max(0.0, min(score, 1.0)), 2)

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

def extract_or_estimate(company, text):
    if not text or len(text) < 50:
        return None
    prompt = f"""Return ONLY a JSON object, no explanation, no markdown.
Company: {company}
Extract: revenue (USD number), employees (number), industry (English), estimated (true/false)
TEXT: {text}"""
    content = call_ai(prompt)
    if content:
        parsed = safe_json_parse(content)
        if parsed:
            return parsed
    revenue = extract_revenue_fallback(text)
    return {"revenue": revenue, "estimated": True, "employees": None, "industry": None}

def process_company(company: str):
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

def build_csv(results: list) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Empresa", "Receita (BRL)", "Receita (USD)", "Funcionários", "Indústria", "Classificação", "Confiança", "Estimado"])
    for r in results:
        writer.writerow([
            r.get("empresa", ""),
            r.get("faturamento_brl") or "N/A",
            r.get("faturamento_usd") or "N/A",
            r.get("funcionarios") or "N/A",
            r.get("industria") or "N/A",
            r.get("classificacao") or "N/A",
            f"{round(r.get('confianca', 0) * 100)}%" if r.get("confianca") else "N/A",
            "Sim" if r.get("estimado") else "Não",
        ])
    output.seek(0)
    return output.getvalue()

# ----------------------------
# ENDPOINTS PÚBLICOS
# ----------------------------
class RegisterBody(BaseModel):
    email: str

@app.post("/register")
@limiter.limit("5/minute")
def register(request: Request, body: RegisterBody):
    existing = supabase.table("users").select("id").eq("email", body.email).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    res = supabase.table("users").insert({"email": body.email}).execute()
    user = res.data[0]
    return {
        "message": "Usuário criado com sucesso",
        "api_key": user["api_key"],
        "plan": user["plan"],
        "credits_limit": user["credits_limit"]
    }

@app.get("/me")
@limiter.limit("30/minute")
def me(request: Request, user=Depends(get_current_user)):
    return {
        "email": user["email"],
        "plan": user["plan"],
        "credits_used": user["credits_used"],
        "credits_limit": user["credits_limit"],
        "credits_remaining": user["credits_limit"] - user["credits_used"],
        "api_key": user["api_key"],
    }

@app.get("/dashboard")
@limiter.limit("30/minute")
def dashboard(request: Request, user=Depends(get_current_user)):
    """Retorna dados do dashboard: uso por dia dos últimos 7 dias."""
    try:
        logs = supabase.table("search_logs")\
            .select("created_at, count")\
            .eq("user_id", user["id"])\
            .order("created_at", desc=True)\
            .limit(100)\
            .execute()

        # agrupa por dia
        from collections import defaultdict
        from datetime import datetime, timedelta

        daily = defaultdict(int)
        today = datetime.utcnow().date()

        for i in range(7):
            day = (today - timedelta(days=i)).isoformat()
            daily[day] = 0

        for log in logs.data:
            day = log["created_at"][:10]
            if day in daily:
                daily[day] += log["count"]

        chart_data = [
            {"date": k, "searches": v}
            for k, v in sorted(daily.items())
        ]

        return {
            "email": user["email"],
            "plan": user["plan"],
            "credits_used": user["credits_used"],
            "credits_limit": user["credits_limit"],
            "credits_remaining": user["credits_limit"] - user["credits_used"],
            "total_searches": sum(daily.values()),
            "chart": chart_data,
        }
    except Exception as e:
        print(f"[DASHBOARD ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao carregar dashboard")

# ----------------------------
# ENDPOINTS PROTEGIDOS
# ----------------------------
@app.get("/company")
@limiter.limit("20/minute")
def company_endpoint(request: Request, company: str, user=Depends(get_current_user)):
    result = process_company(company)
    consume_credit(user["id"])
    log_search(user["id"], [company])
    return result

@app.post("/batch")
@limiter.limit("10/minute")
def batch(request: Request, companies: list[str], user=Depends(get_current_user)):
    remaining = user["credits_limit"] - user["credits_used"]
    if len(companies) > remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Créditos insuficientes. Você tem {remaining} crédito(s) e tentou buscar {len(companies)} empresa(s)."
        )
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_company, companies))
    for _ in companies:
        consume_credit(user["id"])
    log_search(user["id"], companies)
    return results

@app.post("/batch/export")
@limiter.limit("10/minute")
def batch_export(request: Request, companies: list[str], user=Depends(get_current_user)):
    remaining = user["credits_limit"] - user["credits_used"]
    if len(companies) > remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Créditos insuficientes. Você tem {remaining} crédito(s) e tentou buscar {len(companies)} empresa(s)."
        )
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_company, companies))
    for _ in companies:
        consume_credit(user["id"])
    log_search(user["id"], companies)
    csv_content = build_csv(results)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=empresas.csv"}
    )