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
from dotenv import load_dotenv
from pathlib import Path

# ----------------------------
# INIT
# ----------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ----------------------------
# RATE LIMIT
# ----------------------------
def get_key(request: Request):
    if request.method == "OPTIONS":
        return None
    return get_remote_address(request)

limiter = Limiter(key_func=get_key)
app = FastAPI()

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Muitas requisições"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://revenue-tau.vercel.app",
        "http://localhost:3000"
    ],
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
        raise HTTPException(status_code=429, detail="Limite de créditos atingido")

    return user

def get_admin_user(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito")
    return user

def consume_credit(user_id: str):
    supabase.rpc("increment_credits", {"user_id_input": user_id}).execute()

def log_search(user_id: str, companies: list):
    try:
        supabase.table("search_logs").insert({
            "user_id": user_id,
            "companies": companies,
            "count": len(companies)
        }).execute()
    except Exception as e:
        print(e)

# ----------------------------
# CONFIG
# ----------------------------
SERP_API_KEY = os.getenv("SERP_API_KEY")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

CACHE = {
    "search": {},
    "company": {},
    "exchange": {"rate": None, "time": 0}
}
CACHE_TTL = 3600

# ----------------------------
# UTILS
# ----------------------------
def extract_revenue_fallback(text):
    patterns = [
        r"\$?\s?([\d,\.]+)\s?(billion|million|trillion)"
    ]

    multipliers = {
        "million": 1_000_000,
        "billion": 1_000_000_000,
        "trillion": 1_000_000_000_000
    }

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1).replace(",", ""))
            unit = match.group(2).lower()
            return value * multipliers.get(unit, 1)

    return None

def estimate_revenue_by_employees(employees, industry):
    if not employees:
        return None

    base = {
        "Tecnologia": 200_000,
        "Software": 180_000,
        "Varejo": 80_000
    }

    return employees * base.get(industry, 100_000)

def get_usd_brl_rate():
    now = time.time()

    if CACHE["exchange"]["rate"] and now - CACHE["exchange"]["time"] < CACHE_TTL:
        return CACHE["exchange"]["rate"]

    try:
        res = requests.get("https://api.exchangerate-api.com/v4/latest/USD").json()
        rate = res["rates"]["BRL"]
        CACHE["exchange"] = {"rate": rate, "time": now}
        return rate
    except:
        return 5.7

def convert_to_brl(value):
    rate = get_usd_brl_rate()
    return value * rate if value else None

def format_brl(v):
    if not v:
        return None
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def classify_company(v):
    if not v:
        return "Desconhecido"
    if v < 360_000:
        return "Micro"
    elif v < 4_800_000:
        return "Pequena"
    elif v < 300_000_000:
        return "Média"
    else:
        return "Grande"

# ----------------------------
# APOLLO
# ----------------------------
def search_apollo_company(company):
    try:
        res = requests.post(
            "https://api.apollo.io/api/v1/mixed_companies/search",
            json={
                "api_key": APOLLO_API_KEY,
                "q_organization_name": company,
                "per_page": 1
            },
            timeout=10
        ).json()

        orgs = res.get("organizations", [])
        if not orgs:
            return {}

        c = orgs[0]

        return {
            "employees": c.get("estimated_num_employees"),
            "industry": c.get("industry"),
            "city": c.get("city"),
            "country": c.get("country"),
            "linkedin": c.get("linkedin_url"),
        }

    except Exception as e:
        print(e)
        return {}

# ----------------------------
# SEARCH
# ----------------------------
def search_company_data(company):
    if company in CACHE["search"]:
        return CACHE["search"][company]["data"]

    queries = [
        f"{company} annual revenue",
        f"{company} employees company"
    ]

    text = ""

    for q in queries:
        try:
            res = requests.get(
                "https://serpapi.com/search",
                params={"q": q, "api_key": SERP_API_KEY}
            ).json()

            for r in res.get("organic_results", [])[:3]:
                text += r.get("snippet", "") + " "

        except:
            pass

    CACHE["search"][company] = {"data": text, "time": time.time()}
    return text

# ----------------------------
# PROCESS
# ----------------------------
def process_company(company):
    if company in CACHE["company"]:
        return CACHE["company"][company]["data"]

    text = search_company_data(company)
    apollo = search_apollo_company(company)

    revenue = extract_revenue_fallback(text)
    employees = apollo.get("employees")

    if not revenue and employees:
        revenue = estimate_revenue_by_employees(employees, "Tecnologia")

    brl = convert_to_brl(revenue)

    result = {
        "empresa": company,
        "faturamento_usd": revenue,
        "faturamento_brl": format_brl(brl),
        "funcionarios": employees,
        "cidade": apollo.get("city"),
        "pais": apollo.get("country"),
        "linkedin": apollo.get("linkedin"),
        "classificacao": classify_company(brl)
    }

    CACHE["company"][company] = {"data": result, "time": time.time()}
    return result

# ----------------------------
# CSV
# ----------------------------
def build_csv(results):
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Empresa", "Receita BRL", "Receita USD"])

    for r in results:
        writer.writerow([
            r["empresa"],
            r["faturamento_brl"],
            r["faturamento_usd"]
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

# ----------------------------
# ROUTES
# ----------------------------
@app.post("/register")
def register(body: RegisterBody):
    password_hash = pwd_context.hash(body.password)

    res = supabase.table("users").insert({
        "email": body.email,
        "password_hash": password_hash
    }).execute()

    return res.data[0]

@app.post("/login")
def login(body: LoginBody):
    res = supabase.table("users").select("*").eq("email", body.email).execute()

    if not res.data:
        raise HTTPException(401)

    user = res.data[0]

    if not pwd_context.verify(body.password, user["password_hash"]):
        raise HTTPException(401)

    return user

@app.get("/company")
def company(company: str, user=Depends(get_current_user)):
    result = process_company(company)
    consume_credit(user["id"])
    return result

@app.post("/batch")
def batch(companies: list[str], user=Depends(get_current_user)):
    results = [process_company(c) for c in companies]

    for _ in companies:
        consume_credit(user["id"])

    return results

@app.post("/batch/export")
def export(companies: list[str], user=Depends(get_current_user)):
    results = [process_company(c) for c in companies]
    csv_file = build_csv(results)

    return StreamingResponse(
        iter([csv_file]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=data.csv"}
    )