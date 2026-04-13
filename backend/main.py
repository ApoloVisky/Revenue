from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from supabase import create_client
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from passlib.context import CryptContext
import time, requests, os, json, re, csv, io
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ----------------------------
# RATE LIMITER
# ----------------------------
def get_key_skip_options(request: Request):
    if request.method == "OPTIONS":
        return "options-preflight"
    return get_remote_address(request)

limiter = Limiter(
    key_func=lambda request: get_remote_address(request) if request.method != "OPTIONS" else "skip"
)
app = FastAPI()

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Muitas requisições. Aguarde um momento e tente novamente."})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://revenue-weld.vercel.app", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# ----------------------------
# SUPABASE
# ----------------------------
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

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

def get_admin_user(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return user

def consume_credit(user_id: str):
    supabase.rpc("increment_credits", {"user_id_input": user_id}).execute()

def log_search(user_id: str, companies: list):
    try:
        supabase.table("search_logs").insert({
            "user_id": user_id, "companies": companies, "count": len(companies),
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
    default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "Company Revenue App"}
)

CACHE = {"search": {}, "exchange": {"rate": None, "time": 0}, "company": {}}
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

def safe_json_parse_list(content):
    """Parseia uma lista JSON do conteúdo."""
    try:
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception as e:
        print(f"[JSON LIST PARSE ERROR] {e}")
        return None

def extract_revenue_fallback(text):
    annual_patterns = [
        r"annual(?:ly)?\s+revenue[^\$\d]*\$?\s?([\d,\.]+)\s?(billion|million|trillion)",
        r"full[- ]year\s+revenue[^\$\d]*\$?\s?([\d,\.]+)\s?(billion|million|trillion)",
        r"fiscal\s+year[^\$\d]*revenue[^\$\d]*\$?\s?([\d,\.]+)\s?(billion|million|trillion)",
        r"revenue[^\$\d]*\$?\s?([\d,\.]+)\s?(billion|million|trillion)\s+(?:in\s+)?(?:fiscal\s+)?\d{4}",
    ]
    generic_patterns = [
        r"revenue\s+of\s+\$?\s?([\d,\.]+)\s?(billion|million|trillion)",
        r"\$\s?([\d,\.]+)\s?(billion|million|trillion)\s+(?:in\s+)?revenue",
        r"\$\s?([\d,\.]+)\s?(billion|million|trillion)",
    ]
    multipliers = {"million": 1_000_000, "billion": 1_000_000_000, "trillion": 1_000_000_000_000}
    for pattern in annual_patterns + generic_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value = float(match.group(1).replace(",", ""))
                unit = match.group(2).lower()
                result = value * multipliers.get(unit, 1)
                if 10_000 <= result <= 5_000_000_000_000:
                    return result
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

# Ranges de receita anual esperados por indústria (USD)
INDUSTRY_REVENUE_RANGES = {
    "Tecnologia":      (100_000, 3_000_000_000_000),
    "Software":        (100_000, 500_000_000_000),
    "SaaS":            (100_000, 100_000_000_000),
    "Varejo":          (500_000, 700_000_000_000),
    "Energia":         (1_000_000, 500_000_000_000),
    "Bancário":        (1_000_000, 200_000_000_000),
    "Financeiro":      (1_000_000, 200_000_000_000),
    "Saúde":           (500_000, 200_000_000_000),
    "Industrial":      (500_000, 300_000_000_000),
    "Manufatura":      (500_000, 300_000_000_000),
    "Telecomunicações":(1_000_000, 200_000_000_000),
    "Serviços":        (100_000, 100_000_000_000),
    "E-commerce":      (500_000, 600_000_000_000),
}

def translate_industry(industry):
    if not industry:
        return None
    for key, value in INDUSTRY_MAP.items():
        if key in industry.lower():
            return value
    return industry

def validate_revenue_by_industry(revenue, industry_pt):
    """Valida se o revenue faz sentido para a indústria. Retorna (revenue, is_suspicious)."""
    if not revenue or not industry_pt:
        return revenue, False
    range_ = INDUSTRY_REVENUE_RANGES.get(industry_pt)
    if range_ and not (range_[0] <= revenue <= range_[1]):
        print(f"[REVENUE SUSPEITO] R${revenue:,.0f} fora do range esperado para {industry_pt}")
        return None, True
    return revenue, False

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
    models = [
        "anthropic/claude-3-haiku",
        "meta-llama/llama-3-70b-instruct",
        "meta-llama/llama-3-8b-instruct",
    ]
    for model in models:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                timeout=15,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[AI ERROR] {model}: {e}")
            time.sleep(1)
    return None

def calculate_confidence(data, text, revenue_suspicious=False):
    if not data:
        return 0.1
    score = 0.2 if data.get("estimated") else 0.5
    if data.get("revenue"): score += 0.1
    if data.get("employees"): score += 0.1
    if data.get("industry"): score += 0.05
    trusted = ["forbes", "statista", "bloomberg", "reuters", "yahoo finance"]
    matches = sum(1 for s in trusted if s in text.lower())
    score += min(matches * 0.05, 0.15)
    if revenue_suspicious:
        score -= 0.2
    return round(max(0.0, min(score, 1.0)), 2)

# ----------------------------
# WIKIPEDIA
# ----------------------------
def search_wikipedia(company):
    """Busca resumo da empresa na Wikipedia (gratuito)."""
    try:
        # tenta buscar direto pelo nome
        res = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{company.replace(' ', '_')}",
            timeout=5
        ).json()
        if res.get("type") == "standard":
            return res.get("extract", "")

        # se não achar, tenta pesquisa
        search_res = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": company, "format": "json", "srlimit": 1},
            timeout=5
        ).json()
        results = search_res.get("query", {}).get("search", [])
        if results:
            title = results[0]["title"]
            page_res = requests.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}",
                timeout=5
            ).json()
            return page_res.get("extract", "")
    except Exception as e:
        print(f"[WIKIPEDIA ERROR] {company}: {e}")
    return ""

# ----------------------------
# DETECÇÃO DE AMBIGUIDADE
# ----------------------------
def detect_ambiguity(company):
    """
    Verifica se o nome da empresa é ambíguo (múltiplas empresas com mesmo nome).
    Retorna lista de opções ou None se não há ambiguidade.
    """
    cache_key = f"ambig_{company}"
    now = time.time()
    if cache_key in CACHE["search"]:
        cached = CACHE["search"][cache_key]
        if now - cached["time"] < CACHE_TTL:
            return cached["data"]

    prompt = f"""The user is searching for a company named "{company}".

Are there multiple well-known companies or organizations with this exact name or very similar names that could cause confusion?

If YES, return a JSON array with up to 4 options, each with:
- "name": official company name
- "description": one short sentence (country, industry, what they do)
- "country": country of headquarters

If NO ambiguity (the name clearly refers to one company), return an empty array [].

Return ONLY the JSON array, no explanation.

Examples:
- "Apple" → [] (clearly Apple Inc.)
- "Ambev" → [] (clearly the Brazilian beverage company)  
- "Natura" → [{{"name": "Natura &Co", "description": "Brazilian cosmetics company", "country": "Brazil"}}, {{"name": "Natura (other)", "description": "...","country": "..."}}]
- "Magazine" → [{{"name": "Magazine Luiza", "description": "Brazilian retail chain", "country": "Brazil"}}, ...]"""

    content = call_ai(prompt)
    options = None
    if content:
        parsed = safe_json_parse_list(content)
        if parsed and len(parsed) > 1:
            options = parsed

    CACHE["search"][cache_key] = {"data": options, "time": now}
    return options

# ----------------------------
# SEARCH COM QUERIES MÚLTIPLAS
# ----------------------------
def search_company_data(company):
    now = time.time()
    if company in CACHE["search"]:
        cached = CACHE["search"][company]
        if now - cached["time"] < CACHE_TTL:
            return cached["data"]

    queries = [
        f"{company} annual revenue fiscal year 2024",
        f"{company} faturamento receita anual 2024",
    ]

    all_snippets = []
    for query in queries:
        try:
            res = requests.get(
                "https://serpapi.com/search",
                params={"q": query, "api_key": SERP_API_KEY},
                timeout=10
            ).json()
            snippets = [r.get("snippet", "") for r in res.get("organic_results", [])[:3]]
            all_snippets.extend(snippets)
        except Exception as e:
            print(f"[SERP ERROR] {query}: {e}")

    # Wikipedia gratuita
    wiki_text = search_wikipedia(company)
    if wiki_text:
        all_snippets.append(wiki_text)

    text = " ".join(all_snippets)
    CACHE["search"][company] = {"data": text, "time": now}
    return text

# ----------------------------
# EXTRACTION COM FEW-SHOT
# ----------------------------
def extract_or_estimate(company, text):
    if not text or len(text) < 50:
        return None

    prompt = f"""Return ONLY a JSON object. No explanation, no markdown, no extra text.

Examples:
Text: "Apple reported annual revenue of $391 billion for fiscal year 2024, employing 150,000 people worldwide in technology and consumer electronics."
Output: {{"revenue": 391000000000, "employees": 150000, "industry": "Technology", "estimated": false}}

Text: "Petrobras reported net revenue of R$502 billion in 2023. The Brazilian state oil company employs around 45,000 workers."
Output: {{"revenue": 90000000000, "employees": 45000, "industry": "Energy", "estimated": true}}

Text: "Magazine Luiza, known as Magalu, is a Brazilian retail chain with revenues around R$35 billion and 45,000 employees."
Output: {{"revenue": 6300000000, "employees": 45000, "industry": "Retail", "estimated": true}}

Text: "The company reported Q3 revenue of $2.5 billion, up 12% year-over-year in the software segment."
Output: {{"revenue": 10000000000, "employees": null, "industry": "Software", "estimated": true}}

Rules:
- revenue must be ANNUAL (full year), never quarterly
- revenue must be in USD as a plain number (no symbols, no commas)
- if only quarterly revenue found: multiply by 4 and set estimated true
- if revenue is in BRL: divide by 5.7 to convert to USD and set estimated true
- use the most recent fiscal year available
- employees must be total headcount as a plain number
- set estimated false ONLY if annual USD revenue is explicitly stated

Return exactly:
{{"revenue": <number or null>, "employees": <number or null>, "industry": "<string in English or null>", "estimated": <true or false>}}

Company: {company}
TEXT:
{text}"""

    content = call_ai(prompt)
    if content:
        parsed = safe_json_parse(content)
        if parsed:
            revenue = parsed.get("revenue")
            if revenue is not None:
                try:
                    if not (10_000 <= float(revenue) <= 5_000_000_000_000):
                        print(f"[REVENUE INVALID] {company}: {revenue}")
                        parsed["revenue"] = None
                        parsed["estimated"] = True
                except (TypeError, ValueError):
                    parsed["revenue"] = None
                    parsed["estimated"] = True
            return parsed

    revenue = extract_revenue_fallback(text)
    return {"revenue": revenue, "estimated": True, "employees": None, "industry": None}

# ----------------------------
# CORE
# ----------------------------
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
        industry_pt = translate_industry(data.get("industry") if data else None)

        # validação por indústria
        revenue_usd, suspicious = validate_revenue_by_industry(revenue_usd, industry_pt)
        revenue_brl = convert_to_brl(revenue_usd)

        response = {
            "empresa": company,
            "faturamento_usd": revenue_usd,
            "faturamento_brl": format_brl(revenue_brl),
            "estimado": data.get("estimated", True) if data else True,
            "funcionarios": data.get("employees") if data else None,
            "industria": industry_pt,
            "confianca": calculate_confidence(data, text_data, suspicious),
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
# MODELS
# ----------------------------
class RegisterBody(BaseModel):
    email: str
    password: str

class LoginBody(BaseModel):
    email: str
    password: str

class AddCreditsBody(BaseModel):
    user_id: str
    credits: int

class UpdatePlanBody(BaseModel):
    user_id: str
    plan: str
    credits_limit: int

# ----------------------------
# ENDPOINTS PÚBLICOS
# ----------------------------
@app.post("/register")
@limiter.limit("5/minute")
def register(request: Request, body: RegisterBody):
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter ao menos 6 caracteres.")
    existing = supabase.table("users").select("id").eq("email", body.email).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Email já cadastrado.")
    password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    res = supabase.table("users").insert({"email": body.email, "password_hash": password_hash}).execute()
    user = res.data[0]
    return {"message": "Conta criada com sucesso.", "api_key": user["api_key"], "plan": user["plan"], "credits_limit": user["credits_limit"]}

@app.post("/login")
@limiter.limit("10/minute")
def login(request: Request, body: LoginBody):
    res = supabase.table("users").select("*").eq("email", body.email).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Email ou senha incorretos.")
    user = res.data[0]
    if not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Email ou senha incorretos.")
    if not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Email ou senha incorretos.")
    return {
        "api_key": user["api_key"], "email": user["email"], "plan": user["plan"],
        "role": user["role"], "credits_used": user["credits_used"],
        "credits_limit": user["credits_limit"],
        "credits_remaining": user["credits_limit"] - user["credits_used"],
    }

@app.get("/me")
@limiter.limit("30/minute")
def me(request: Request, user=Depends(get_current_user)):
    return {
        "email": user["email"], "plan": user["plan"], "role": user["role"],
        "credits_used": user["credits_used"], "credits_limit": user["credits_limit"],
        "credits_remaining": user["credits_limit"] - user["credits_used"],
        "api_key": user["api_key"],
    }

@app.get("/dashboard")
@limiter.limit("30/minute")
def dashboard(request: Request, user=Depends(get_current_user)):
    try:
        logs = supabase.table("search_logs").select("created_at, count").eq("user_id", user["id"]).order("created_at", desc=True).limit(100).execute()
        from collections import defaultdict
        from datetime import datetime, timedelta
        daily = defaultdict(int)
        today = datetime.utcnow().date()
        for i in range(7):
            daily[(today - timedelta(days=i)).isoformat()] = 0
        for log in logs.data:
            day = log["created_at"][:10]
            if day in daily:
                daily[day] += log["count"]
        chart_data = [{"date": k, "searches": v} for k, v in sorted(daily.items())]
        return {
            "email": user["email"], "plan": user["plan"], "role": user["role"],
            "credits_used": user["credits_used"], "credits_limit": user["credits_limit"],
            "credits_remaining": user["credits_limit"] - user["credits_used"],
            "total_searches": sum(daily.values()), "chart": chart_data,
        }
    except Exception as e:
        print(f"[DASHBOARD ERROR] {e}")
        raise HTTPException(status_code=500, detail="Erro ao carregar dashboard")

# ----------------------------
# ENDPOINT AMBIGUIDADE
# ----------------------------
@app.get("/disambiguate")
@limiter.limit("20/minute")
def disambiguate(request: Request, company: str, user=Depends(get_current_user)):
    """Verifica se há ambiguidade no nome da empresa."""
    options = detect_ambiguity(company)
    return {"ambiguous": options is not None, "options": options or []}

# ----------------------------
# ENDPOINTS ADMIN
# ----------------------------
@app.get("/admin/users")
@limiter.limit("20/minute")
def admin_list_users(request: Request, user=Depends(get_admin_user)):
    res = supabase.table("users").select("id, email, plan, role, credits_used, credits_limit, created_at").order("created_at", desc=True).execute()
    return res.data

@app.post("/admin/credits")
@limiter.limit("20/minute")
def admin_add_credits(request: Request, body: AddCreditsBody, user=Depends(get_admin_user)):
    res = supabase.table("users").select("id, email, credits_limit").eq("id", body.user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    target = res.data[0]
    new_limit = target["credits_limit"] + body.credits
    supabase.table("users").update({"credits_limit": new_limit}).eq("id", body.user_id).execute()
    return {"message": f"{body.credits} créditos adicionados.", "new_limit": new_limit, "email": target["email"]}

@app.post("/admin/plan")
@limiter.limit("20/minute")
def admin_update_plan(request: Request, body: UpdatePlanBody, user=Depends(get_admin_user)):
    res = supabase.table("users").select("id, email").eq("id", body.user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    supabase.table("users").update({"plan": body.plan, "credits_limit": body.credits_limit}).eq("id", body.user_id).execute()
    return {"message": f"Plano atualizado para {body.plan}.", "credits_limit": body.credits_limit}

@app.delete("/admin/users/{user_id}")
@limiter.limit("10/minute")
def admin_delete_user(request: Request, user_id: str, user=Depends(get_admin_user)):
    res = supabase.table("users").select("id, email").eq("id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    supabase.table("users").delete().eq("id", user_id).execute()
    return {"message": "Usuário removido."}

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
        raise HTTPException(status_code=429, detail=f"Créditos insuficientes. Você tem {remaining} crédito(s) e tentou buscar {len(companies)} empresa(s).")
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_company, companies))
    for _ in companies:
        consume_credit(user["id"])
    log_search(user["id"], companies)
    return results

@app.post("/batch/export")

@app.options("/{rest_of_path:path}")
async def preflight_handler():
    return {}

@limiter.limit("10/minute")
def batch_export(request: Request, companies: list[str], user=Depends(get_current_user)):
    remaining = user["credits_limit"] - user["credits_used"]
    if len(companies) > remaining:
        raise HTTPException(status_code=429, detail=f"Créditos insuficientes. Você tem {remaining} crédito(s) e tentou buscar {len(companies)} empresa(s).")
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(process_company, companies))
    for _ in companies:
        consume_credit(user["id"])
    log_search(user["id"], companies)
    csv_content = build_csv(results)
    return StreamingResponse(iter([csv_content]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=empresas.csv"})