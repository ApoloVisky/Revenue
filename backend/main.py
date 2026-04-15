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
import bcrypt
from difflib import SequenceMatcher
import time, requests, os, json, re, csv, io
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ----------------------------
# RATE LIMITER
# ----------------------------
def get_key_skip_options(request: Request):
    if request.method == "OPTIONS":
        return "options-preflight"
    return get_remote_address(request)

limiter = Limiter(key_func=get_key_skip_options)
app = FastAPI()

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Muitas requisições. Aguarde um momento."})

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://revenue-tau.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SlowAPIMiddleware)
app.state.limiter = limiter

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
# CONFIG
# ----------------------------
SERP_API_KEY   = os.getenv("SERP_API_KEY")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "Company Revenue App"}
)

# ----------------------------
# CACHE
# ----------------------------
CACHE = {
    "apollo":   {},
    "search":   {},
    "company":  {},
    "exchange": {"rate": None, "time": 0},
    "ambig":    {},
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

def safe_json_parse_list(content):
    try:
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception as e:
        print(f"[JSON LIST PARSE ERROR] {e}")
        return None

INDUSTRY_MAP = {
    "retail": "Varejo", "technology": "Tecnologia", "software": "Software",
    "saas": "SaaS", "finance": "Financeiro", "banking": "Bancário",
    "industrial": "Industrial", "manufacturing": "Manufatura",
    "services": "Serviços", "health": "Saúde", "energy": "Energia",
    "telecom": "Telecomunicações", "e-commerce": "E-commerce",
    "information technology": "Tecnologia", "consumer goods": "Varejo",
    "oil & gas": "Energia", "pharmaceuticals": "Saúde",
    "food & beverages": "Alimentação", "automotive": "Automotivo",
    "real estate": "Imobiliário", "education": "Educação",
    "media": "Mídia", "transportation": "Transporte",
}

INDUSTRY_REVENUE_RANGES = {
    "Tecnologia":       (100_000, 3_000_000_000_000),
    "Software":         (100_000, 500_000_000_000),
    "SaaS":             (100_000, 100_000_000_000),
    "Varejo":           (500_000, 700_000_000_000),
    "Energia":          (1_000_000, 500_000_000_000),
    "Bancário":         (1_000_000, 200_000_000_000),
    "Financeiro":       (1_000_000, 200_000_000_000),
    "Saúde":            (500_000, 200_000_000_000),
    "Industrial":       (500_000, 300_000_000_000),
    "Manufatura":       (500_000, 300_000_000_000),
    "Telecomunicações": (1_000_000, 200_000_000_000),
    "Serviços":         (100_000, 100_000_000_000),
    "E-commerce":       (500_000, 600_000_000_000),
    "Alimentação":      (100_000, 100_000_000_000),
    "Automotivo":       (1_000_000, 500_000_000_000),
    "Educação":         (100_000, 50_000_000_000),
    "Mídia":            (100_000, 50_000_000_000),
    "Transporte":       (500_000, 100_000_000_000),
}

def translate_industry(industry):
    if not industry:
        return None
    industry_lower = industry.lower()
    for key, value in INDUSTRY_MAP.items():
        if key in industry_lower:
            return value
    return industry.title()

def validate_revenue_by_size(revenue, employees):
    if not revenue or not employees:
        return revenue, False

    # receita por funcionário (benchmark)
    rev_per_employee = revenue / employees

    # limites razoáveis
    if rev_per_employee > 5_000_000:  # > $5M por funcionário = suspeito
        print(f"[SUSPEITO] Receita por funcionário muito alta: {rev_per_employee:,.0f}")
        return None, True

    if rev_per_employee < 1_000:  # muito baixo também é estranho
        return None, True

    return revenue, False

def classify_company(revenue):
    if not revenue:
        return "Desconhecido"
    if revenue < 360_000:         return "Microempresa"
    elif revenue < 4_800_000:     return "Pequena empresa"
    elif revenue < 300_000_000:   return "Média empresa"
    elif revenue < 1_000_000_000: return "Grande empresa"
    else:                          return "Enterprise"

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

def parse_apollo_revenue_range(range_str: str):
    """Converte '$10M-$50M' para média em USD."""
    if not range_str:
        return None
    try:
        clean = re.sub(r'[\$,]', '', range_str.upper())
        multipliers = {"T": 1_000_000_000_000, "B": 1_000_000_000, "M": 1_000_000, "K": 1_000}
        numbers = []
        for part in re.split(r'[-–]', clean):
            part = part.strip()
            m = re.match(r"([\d\.]+)([TBMK]?)", part)
            if m:
                val = float(m.group(1))
                unit = m.group(2)
                numbers.append(val * multipliers.get(unit, 1))
        if numbers:
            return sum(numbers) / len(numbers)
    except Exception as e:
        print(f"[APOLLO RANGE PARSE] {e}")
    return None


from difflib import SequenceMatcher

def normalize_company_name(name: str) -> str:
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"\b(inc|ltd|llc|corp|co|company|gmbh|sa|s\.a\.|plc)\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return name.strip()

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def score_company_match(input_name: str, apollo_org: dict) -> float:
    name_input = normalize_company_name(input_name)
    name_apollo = normalize_company_name(apollo_org.get("name", ""))

    base_score = similarity(name_input, name_apollo)

    # bônus por domínio (muito forte)
    website = apollo_org.get("website_url") or ""
    if website:
        domain = re.sub(r"https?://(www\.)?", "", website).split("/")[0]
        if name_input.replace(" ", "") in domain:
            base_score += 0.2

    # leve ajuste por país conhecido
    country = (apollo_org.get("country") or "").lower()
    if "brazil" in country or "brasil" in country:
        base_score += 0.05

    return min(base_score, 1.0)

# ----------------------------
# APOLLO (fonte principal)
# ----------------------------
def clean_company_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\b(inc|ltd|llc|corp|corporation|company|co)\b\.?,?", "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return name.strip()


def extract_domain(name: str):
    name = name.lower()
    name = name.replace(" ", "")
    return f"{name}.com"


def search_apollo(company: str) -> dict:
    now = time.time()
    cache_key = f"apollo_{company.lower()}"

    if cache_key in CACHE["apollo"]:
        cached = CACHE["apollo"][cache_key]
        if now - cached["time"] < CACHE_TTL:
            print(f"[APOLLO CACHE HIT] {company}")
            return cached["data"]

    if not APOLLO_API_KEY:
        print("[APOLLO] Sem API key configurada")
        return {}

    try:
        clean_name = clean_company_name(company)

        # ------------------------
        # 1ª tentativa: nome limpo
        # ------------------------
        res = requests.post(
            "https://api.apollo.io/api/v1/mixed_companies/search",
            headers={"Content-Type": "application/json"},
            json={
                "api_key": APOLLO_API_KEY,
                "q_organization_name": clean_name,
                "per_page": 5,
            },
            timeout=10
        ).json()

        orgs = res.get("organizations", [])

        # ------------------------
        # 2ª tentativa: domínio
        # ------------------------
        if not orgs:
            domain = extract_domain(clean_name)
            print(f"[APOLLO FALLBACK DOMAIN] {domain}")

            res = requests.post(
                "https://api.apollo.io/api/v1/mixed_companies/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": APOLLO_API_KEY,
                    "q_organization_domains": [domain],
                    "per_page": 5,
                },
                timeout=10
            ).json()

            orgs = res.get("organizations", [])

        if not orgs:
            print(f"[APOLLO] Nenhum resultado para: {company}")
            CACHE["apollo"][cache_key] = {"data": {}, "time": now}
            return {}

        # pega melhor match
        org = orgs[0]

        data = {
            "name":          org.get("name"),
            "employees":     org.get("estimated_num_employees"),
            "industry":      org.get("industry"),
            "city":          org.get("city"),
            "country":       org.get("country"),
            "linkedin":      org.get("linkedin_url"),
            "website":       org.get("website_url"),
            "founded":       org.get("founded_year"),
            "revenue_range": org.get("annual_revenue_printed"),
            "revenue_usd":   org.get("annual_revenue"),
            "description":   org.get("short_description") or org.get("seo_description"),
            "keywords":      org.get("keywords", []),
        }

        CACHE["apollo"][cache_key] = {"data": data, "time": now}

        print(f"[APOLLO OK] {company} → {data['name']} | revenue={data['revenue_usd']}")

        return data

    except Exception as e:
        print(f"[APOLLO ERROR] {company}: {e}")
        CACHE["apollo"][cache_key] = {"data": {}, "time": now}
        return {}

# ----------------------------
# SERP (fallback apenas)
# ----------------------------
def search_serp_fallback(company: str) -> str:
    """Chamado APENAS se Apollo não retornar revenue nem range."""
    now = time.time()
    if company in CACHE["search"]:
        cached = CACHE["search"][company]
        if now - cached["time"] < CACHE_TTL:
            return cached["data"]

    print(f"[SERP FALLBACK] Apollo sem revenue para: {company}")
    snippets = []
    try:
        res = requests.get(
            "https://serpapi.com/search",
            params={"q": f"{company} annual revenue fiscal year 2024", "api_key": SERP_API_KEY},
            timeout=10
        ).json()
        snippets = [r.get("snippet", "") for r in res.get("organic_results", [])[:5]]
    except Exception as e:
        print(f"[SERP ERROR] {e}")

    text = " ".join(snippets)
    CACHE["search"][company] = {"data": text, "time": now}
    return text

# ----------------------------
# REVENUE FALLBACK (regex)
# ----------------------------
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

# ----------------------------
# AI
# ----------------------------
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

def calculate_confidence(revenue_source: str, apollo_data: dict, has_serp: bool) -> float:
    """
    revenue_source: 'apollo_direct' | 'apollo_range' | 'ai_from_serp' | 'regex' | 'none'
    """
    scores = {
        "apollo_direct":  0.95,
        "apollo_range":   0.75,
        "ai_from_serp":   0.55,
        "regex":          0.35,
        "none":           0.1,
    }
    score = scores.get(revenue_source, 0.1)

    # bônus por dados complementares do Apollo
    if apollo_data.get("employees"):  score = min(score + 0.02, 1.0)
    if apollo_data.get("industry"):   score = min(score + 0.01, 1.0)
    if apollo_data.get("founded"):    score = min(score + 0.01, 1.0)

    return round(score, 2)

# ----------------------------
# AMBIGUIDADE
# ----------------------------
def detect_ambiguity(company):
    now = time.time()
    cache_key = f"ambig_{company.lower()}"
    if cache_key in CACHE["ambig"]:
        cached = CACHE["ambig"][cache_key]
        if now - cached["time"] < CACHE_TTL:
            return cached["data"]

    prompt = f"""The user is searching for a company named "{company}".

Are there multiple well-known companies with this exact name that could cause confusion?

If YES, return a JSON array with up to 4 options:
- "name": official company name
- "description": one short sentence (country, industry, what they do)
- "country": country of headquarters

If NO ambiguity, return [].

Return ONLY the JSON array, no explanation."""

    content = call_ai(prompt)
    options = None
    if content:
        parsed = safe_json_parse_list(content)
        if parsed and len(parsed) > 1:
            options = parsed

    CACHE["ambig"][cache_key] = {"data": options, "time": now}
    return options

# ----------------------------
# CORE — Apollo first
# ----------------------------
def process_company(company: str) -> dict:
    now = time.time()
    if company in CACHE["company"]:
        cached = CACHE["company"][company]
        if now - cached["time"] < CACHE_TTL:
            print(f"[COMPANY CACHE HIT] {company}")
            return cached["data"]

    try:
        # 1. Apollo sempre primeiro
        apollo = search_apollo(company)

        revenue_usd    = None
        revenue_source = "none"
        employees      = apollo.get("employees")
        industry_raw   = apollo.get("industry")
        estimated      = True

        # 2. Apollo tem revenue direto?
        if apollo.get("revenue_usd"):
            try:
                rev = float(apollo["revenue_usd"])
                if 10_000 <= rev <= 5_000_000_000_000:
                    revenue_usd    = rev
                    revenue_source = "apollo_direct"
                    estimated      = False
                    print(f"[REVENUE] Apollo direto: ${rev:,.0f}")
            except:
                pass

        # 3. Apollo tem range? (ex: "$10M-$50M")
        if not revenue_usd and apollo.get("revenue_range"):
            rev = parse_apollo_revenue_range(apollo["revenue_range"])
            if rev and 10_000 <= rev <= 5_000_000_000_000:
                revenue_usd    = rev
                revenue_source = "apollo_range"
                estimated      = True
                print(f"[REVENUE] Apollo range '{apollo['revenue_range']}': ${rev:,.0f}")

        # 4. Fallback: SerpAPI + AI
        if not revenue_usd:
            serp_text = search_serp_fallback(company)
            if serp_text and len(serp_text) > 50:
                # monta contexto Apollo pro AI
                apollo_ctx = ""
                if apollo:
                    parts = []
                    if employees:    parts.append(f"employees: {employees}")
                    if industry_raw: parts.append(f"industry: {industry_raw}")
                    if apollo.get("founded"): parts.append(f"founded: {apollo['founded']}")
                    if apollo.get("country"): parts.append(f"country: {apollo['country']}")
                    if parts:
                        apollo_ctx = f"\nApollo.io data: {', '.join(parts)}\n"

                prompt = f"""Return ONLY a JSON object. No explanation, no markdown.

Examples:
Text: "Apple annual revenue $391 billion fiscal 2024, 150,000 employees, technology."
Output: {{"revenue": 391000000000, "employees": 150000, "industry": "Technology", "estimated": false}}

Text: "Petrobras net revenue R$502 billion 2023, 45,000 workers, oil."
Output: {{"revenue": 90000000000, "employees": 45000, "industry": "Energy", "estimated": true}}

Text: "Q3 revenue $2.5 billion software."
Output: {{"revenue": 10000000000, "employees": null, "industry": "Software", "estimated": true}}

Rules:
- revenue ANNUAL in USD, plain number
- quarterly → multiply by 4, estimated true
- BRL → divide by 5.7, estimated true
- use most recent fiscal year
{apollo_ctx}
Return: {{"revenue": <number or null>, "employees": <number or null>, "industry": "<English string or null>", "estimated": <true or false>}}

Company: {company}
TEXT: {serp_text}"""

                content = call_ai(prompt)
                if content:
                    parsed = safe_json_parse(content)
                    if parsed and parsed.get("revenue"):
                        try:
                            rev = float(parsed["revenue"])
                            if 10_000 <= rev <= 5_000_000_000_000:
                                revenue_usd    = rev
                                revenue_source = "ai_from_serp"
                                estimated      = parsed.get("estimated", True)
                                if not employees and parsed.get("employees"):
                                    employees = parsed["employees"]
                                if not industry_raw and parsed.get("industry"):
                                    industry_raw = parsed["industry"]
                                print(f"[REVENUE] AI from SERP: ${rev:,.0f}")
                        except:
                            pass

                # regex como último recurso
                if not revenue_usd:
    def validate_revenue_by_size(revenue, employees):
        if not revenue or not employees:
            return revenue, False

    # receita por funcionário (benchmark)
    rev_per_employee = revenue / employees

    # limites razoáveis
    if rev_per_employee > 5_000_000:  # > $5M por funcionário = suspeito
        print(f"[SUSPEITO] Receita por funcionário muito alta: {rev_per_employee:,.0f}")
        return None, True

    if rev_per_employee < 1_000:  # muito baixo também é estranho
        return None, True

    return revenue, False
                    rev = extract_revenue_fallback(serp_text)
                    if rev:
                        revenue_usd    = rev
                        revenue_source = "regex"
                        estimated      = True
                        print(f"[REVENUE] Regex fallback: ${rev:,.0f}")

        # 5. industry e validação
        industry_pt = translate_industry(industry_raw)
        revenue_usd, suspicious = validate_revenue_by_industry(revenue_usd, industry_pt)
        if suspicious:
            revenue_source = "none"

        revenue_brl = convert_to_brl(revenue_usd)

        response = {
            "empresa":         company,
            "faturamento_usd": revenue_usd,
            "faturamento_brl": format_brl(revenue_brl),
            "estimado":        estimated,
            "funcionarios":    employees,
            "industria":       industry_pt,
            "confianca":       calculate_confidence(revenue_source, apollo, revenue_source != "none"),
            "classificacao":   classify_company(revenue_brl),
            "fonte":           revenue_source,
            # campos Apollo extras
            "cidade":          apollo.get("city"),
            "pais":            apollo.get("country"),
            "linkedin":        apollo.get("linkedin"),
            "website":         apollo.get("website"),
            "fundada":         apollo.get("founded"),
            "descricao":       apollo.get("description"),
        }

        CACHE["company"][company] = {"data": response, "time": now}
        return response

    except Exception as e:
        print(f"[COMPANY ERROR] {company}: {e}")
        return {"empresa": company, "erro": str(e)}

def build_csv(results: list) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Empresa", "Receita (BRL)", "Receita (USD)", "Funcionários", "Indústria",
                     "Classificação", "Confiança", "Estimado", "Fonte",
                     "Cidade", "País", "Website", "LinkedIn", "Fundada"])
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
            r.get("fonte") or "N/A",
            r.get("cidade") or "N/A",
            r.get("pais") or "N/A",
            r.get("website") or "N/A",
            r.get("linkedin") or "N/A",
            r.get("fundada") or "N/A",
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

@app.get("/disambiguate")
@limiter.limit("20/minute")
def disambiguate(request: Request, company: str, user=Depends(get_current_user)):
    options = detect_ambiguity(company)
    return {"ambiguous": options is not None, "options": options or []}

# ----------------------------
# ADMIN
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
# PROTEGIDOS
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
    def safe_process(company):
    try:
        return process_company(company)
    except Exception as e:
        print(f"[BATCH ERROR] {company}: {e}")
        return {"empresa": company, "erro": str(e)}

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(safe_process, companies))
    for _ in companies:
        consume_credit(user["id"])
    log_search(user["id"], companies)
    return results

@app.post("/batch/export")
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

@app.get("/debug/apollo")
@limiter.limit("10/minute")
def debug_apollo(request: Request, company: str, user=Depends(get_current_user)):
    
    if not APOLLO_API_KEY:
        return {"error": "APOLLO_API_KEY não configurada"}
    
    try:
        res = requests.post(
            "https://api.apollo.io/api/v1/mixed_companies/search",
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
            json={
                "api_key": APOLLO_API_KEY,
                "q_organization_name": company,
                "per_page": 3,
            },
            timeout=10
        ).json()
        
        orgs = res.get("organizations", [])
        
        # retorna os campos relevantes de cada org
        return {
            "total_found": len(orgs),
            "organizations": [
                {
                    "name":                  o.get("name"),
                    "industry":              o.get("industry"),
                    "estimated_num_employees": o.get("estimated_num_employees"),
                    "annual_revenue":        o.get("annual_revenue"),
                    "annual_revenue_printed": o.get("annual_revenue_printed"),
                    "country":               o.get("country"),
                    "website":               o.get("website_url"),
                }
                for o in orgs
            ]
        }
    except Exception as e:
        return {"error": str(e)}