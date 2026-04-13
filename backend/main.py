# (mantive imports iguais)
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
import time, requests, os, csv, io
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from pathlib import Path

# ----------------------------
# INIT
# ----------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

app = FastAPI()

# ----------------------------
# RATE LIMIT
# ----------------------------
limiter = Limiter(key_func=get_remote_address)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Muitas requisições"})

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# ----------------------------
# CORS
# ----------------------------
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
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# ----------------------------
# AUTH
# ----------------------------
api_key_header = APIKeyHeader(name="X-API-Key")

def get_current_user(api_key: str = Depends(api_key_header)):
    res = supabase.table("users").select("*").eq("api_key", api_key).execute()

    if not res.data:
        raise HTTPException(403, "API key inválida")

    user = res.data[0]

    if user["credits_used"] >= user["credits_limit"]:
        raise HTTPException(429, "Limite de créditos atingido")

    return user

def get_admin_user(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Acesso restrito")
    return user

# ----------------------------
# CRÉDITOS
# ----------------------------
def consume_credit(user_id: str):
    supabase.rpc("increment_credits", {"user_id_input": user_id}).execute()

def log_search(user_id: str, companies: list):
    supabase.table("search_logs").insert({
        "user_id": user_id,
        "companies": companies,
        "count": len(companies)
    }).execute()

# ----------------------------
# CONFIG
# ----------------------------
SERP_API_KEY = os.getenv("SERP_API_KEY")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

# ----------------------------
# APOLLO
# ----------------------------
def search_apollo(company):
    try:
        res = requests.post(
            "https://api.apollo.io/api/v1/mixed_companies/search",
            json={
                "api_key": APOLLO_API_KEY,
                "q_organization_name": company,
                "per_page": 1
            }
        ).json()

        org = res.get("organizations", [{}])[0]

        return {
            "employees": org.get("estimated_num_employees"),
            "industry": org.get("industry"),
            "city": org.get("city"),
            "country": org.get("country"),
            "linkedin": org.get("linkedin_url"),
        }

    except:
        return {}

# ----------------------------
# SEARCH
# ----------------------------
def search_company(company):
    try:
        res = requests.get(
            "https://serpapi.com/search",
            params={"q": company + " revenue", "api_key": SERP_API_KEY}
        ).json()

        return " ".join([r.get("snippet", "") for r in res.get("organic_results", [])[:3]])
    except:
        return ""

def extract_revenue(text):
    import re
    match = re.search(r"([\d\.]+)\s?(billion|million)", text, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        if "billion" in match.group(2).lower():
            return val * 1e9
        return val * 1e6
    return None

# ----------------------------
# PROCESS
# ----------------------------
def process_company(company):
    text = search_company(company)
    apollo = search_apollo(company)

    revenue = extract_revenue(text)
    employees = apollo.get("employees")

    if not revenue and employees:
        revenue = employees * 100000

    return {
        "empresa": company,
        "faturamento": revenue,
        "funcionarios": employees,
        "cidade": apollo.get("city"),
        "pais": apollo.get("country"),
        "linkedin": apollo.get("linkedin"),
    }

# ----------------------------
# CSV
# ----------------------------
def build_csv(data):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Empresa", "Receita", "Funcionários"])

    for r in data:
        writer.writerow([r["empresa"], r["faturamento"], r["funcionarios"]])

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
# AUTH ROUTES
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

# ----------------------------
# USER ROUTES
# ----------------------------
@app.get("/me")
def me(user=Depends(get_current_user)):
    return user

@app.get("/company")
def company(company: str, user=Depends(get_current_user)):
    result = process_company(company)
    consume_credit(user["id"])
    log_search(user["id"], [company])
    return result

@app.post("/batch")
def batch(companies: list[str], user=Depends(get_current_user)):
    results = [process_company(c) for c in companies]

    for _ in companies:
        consume_credit(user["id"])

    log_search(user["id"], companies)
    return results

@app.post("/batch/export")
def export(companies: list[str], user=Depends(get_current_user)):
    results = [process_company(c) for c in companies]
    csv_data = build_csv(results)

    return StreamingResponse(
        iter([csv_data]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=empresas.csv"}
    )

# ----------------------------
# ADMIN
# ----------------------------
@app.get("/admin/users")
def list_users(admin=Depends(get_admin_user)):
    return supabase.table("users").select("*").execute().data

@app.post("/admin/credits")
def add_credits(body: AddCreditsBody, admin=Depends(get_admin_user)):
    user = supabase.table("users").select("*").eq("id", body.user_id).execute().data[0]

    new_limit = user["credits_limit"] + body.credits

    supabase.table("users").update({
        "credits_limit": new_limit
    }).eq("id", body.user_id).execute()

    return {"novo_limite": new_limit}

@app.post("/admin/plan")
def update_plan(body: UpdatePlanBody, admin=Depends(get_admin_user)):
    supabase.table("users").update({
        "plan": body.plan,
        "credits_limit": body.credits_limit
    }).eq("id", body.user_id).execute()

    return {"ok": True}

@app.delete("/admin/users/{user_id}")
def delete_user(user_id: str, admin=Depends(get_admin_user)):
    supabase.table("users").delete().eq("id", user_id).execute()
    return {"deleted": True}