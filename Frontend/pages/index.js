"use client";

import { useState, useEffect } from "react";
import axios from "axios";

export default function Home() {
  const [screen, setScreen] = useState("login");
  const [email, setEmail] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [input, setInput] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [credits, setCredits] = useState(null);
  const [generatedKey, setGeneratedKey] = useState("");
  const [dashData, setDashData] = useState(null);

  const fetchCredits = async (key) => {
    try {
      const res = await axios.get("http://localhost:8000/me", {
        headers: { "X-API-Key": key },
      });
      setCredits(res.data);
    } catch {
      setCredits(null);
    }
  };

  const fetchDashboard = async (key) => {
    try {
      const res = await axios.get("http://localhost:8000/dashboard", {
        headers: { "X-API-Key": key },
      });
      setDashData(res.data);
    } catch {
      setDashData(null);
    }
  };

  useEffect(() => {
    if (screen === "dashboard" && apiKey) fetchDashboard(apiKey);
  }, [screen]);

  const handleRegister = async () => {
    if (!email.trim()) { setError("Insira um email."); return; }
    setError(""); setLoading(true);
    try {
      const res = await axios.post("http://localhost:8000/register", { email: email.trim() });
      setGeneratedKey(res.data.api_key);
      setScreen("success");
    } catch (e) {
      setError(e.response?.data?.detail || "Erro ao criar conta.");
    } finally { setLoading(false); }
  };

  const handleLogin = async () => {
    if (!apiKey.trim()) { setError("Insira sua API Key."); return; }
    setError(""); setLoading(true);
    try {
      const res = await axios.get("http://localhost:8000/me", {
        headers: { "X-API-Key": apiKey.trim() },
      });
      setCredits(res.data);
      setScreen("dashboard");
    } catch {
      setError("API Key inválida.");
    } finally { setLoading(false); }
  };

  const handleSearch = async () => {
    const companies = input.split("\n").filter((c) => c.trim() !== "");
    if (!companies.length) { setError("Insira ao menos uma empresa."); return; }
    setError(""); setLoading(true);
    try {
      const res = await axios.post("http://localhost:8000/batch", companies, {
        headers: { "X-API-Key": apiKey.trim() },
      });
      setResults(res.data);
      fetchCredits(apiKey.trim());
      fetchDashboard(apiKey.trim());
    } catch (e) {
      setError(e.response?.data?.detail || "Erro ao buscar.");
    } finally { setLoading(false); }
  };

  const exportCSV = () => {
    const header = "Empresa,Receita (BRL),Funcionários,Indústria,Classificação,Confiança,Estimado";
    const rows = results.map((r) =>
      `${r.empresa},${r.faturamento_brl || "N/A"},${r.funcionarios || "N/A"},${r.industria || "N/A"},${r.classificacao || "N/A"},${r.confianca ? Math.round(r.confianca * 100) + "%" : "N/A"},${r.estimado ? "Sim" : "Não"}`
    );
    const blob = new Blob([[header, ...rows].join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "empresas.csv";
    a.click();
  };

  const logout = () => {
    setScreen("login"); setResults([]); setCredits(null);
    setDashData(null); setApiKey(""); setInput("");
  };

  const Header = ({ subtitle }) => (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 40 }}>
      <div>
        <div className="dash-eyebrow">Business Intelligence</div>
        <h1 className="dash-h1" style={{ marginBottom: 0 }}>
          Company <span>Revenue</span> Dashboard
        </h1>
        {subtitle && <div style={{ fontFamily: "DM Mono, monospace", fontSize: 12, color: "#6b7090", marginTop: 6 }}>{subtitle}</div>}
      </div>
      <div style={{ textAlign: "right" }}>
        {credits && (
          <div style={{ fontFamily: "DM Mono, monospace", fontSize: 12, color: "#6b7090", marginBottom: 8 }}>
            <span style={{ color: "#c8f135" }}>{credits.credits_remaining}</span>/{credits.credits_limit} créditos
            <span style={{ marginLeft: 8, background: "#1c1e27", border: "1px solid #2a2d3a", borderRadius: 4, padding: "2px 8px" }}>
              {credits.plan}
            </span>
          </div>
        )}
        <div style={{ display: "flex", gap: 16, justifyContent: "flex-end" }}>
          <span onClick={() => setScreen("dashboard")}
            style={{ fontFamily: "DM Mono, monospace", fontSize: 11, color: screen === "dashboard" ? "#c8f135" : "#6b7090", cursor: "pointer" }}>
            Dashboard
          </span>
          <span onClick={() => setScreen("search")}
            style={{ fontFamily: "DM Mono, monospace", fontSize: 11, color: screen === "search" ? "#c8f135" : "#6b7090", cursor: "pointer" }}>
            Buscar
          </span>
          <span onClick={logout}
            style={{ fontFamily: "DM Mono, monospace", fontSize: 11, color: "#6b7090", cursor: "pointer" }}>
            Sair →
          </span>
        </div>
      </div>
    </div>
  );

  // REGISTER
  if (screen === "register") return (
    <div className="dash-bg">
      <div className="dash-wrapper" style={{ maxWidth: 480 }}>
        <div className="dash-eyebrow">Business Intelligence</div>
        <h1 className="dash-h1">Criar <span>conta</span></h1>
        <div className="input-card">
          <label className="input-label">Email</label>
          <input className="dash-textarea" style={{ minHeight: "unset", padding: "10px 14px" }}
            type="email" placeholder="seu@email.com" value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleRegister()} />
          {error && <div style={{ marginTop: 10, fontFamily: "DM Mono, monospace", fontSize: 12, color: "#ff4d6d" }}>⚠ {error}</div>}
          <button className="dash-btn" onClick={handleRegister} disabled={loading} style={{ marginTop: 16 }}>
            {loading ? "Criando..." : "Criar conta grátis"}
          </button>
          <div style={{ marginTop: 16, fontFamily: "DM Mono, monospace", fontSize: 12, color: "#6b7090" }}>
            Já tem conta?{" "}
            <span onClick={() => { setScreen("login"); setError(""); }} style={{ color: "#c8f135", cursor: "pointer" }}>
              Entrar com API Key
            </span>
          </div>
        </div>
      </div>
    </div>
  );

  // SUCCESS
  if (screen === "success") return (
    <div className="dash-bg">
      <div className="dash-wrapper" style={{ maxWidth: 480 }}>
        <div className="dash-eyebrow">Conta criada!</div>
        <h1 className="dash-h1">Sua <span>API Key</span></h1>
        <div className="input-card">
          <label className="input-label">Guarde esta chave — ela não será exibida novamente</label>
          <div style={{ background: "#1c1e27", border: "1px solid #2a2d3a", borderRadius: 10, padding: "14px 16px", fontFamily: "DM Mono, monospace", fontSize: 13, color: "#c8f135", wordBreak: "break-all", marginBottom: 16 }}>
            {generatedKey}
          </div>
          <button className="dash-btn" onClick={() => navigator.clipboard.writeText(generatedKey)} style={{ marginBottom: 12 }}>
            <svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </svg>
            Copiar API Key
          </button>
          <button className="dash-btn" onClick={() => { setApiKey(generatedKey); fetchCredits(generatedKey); setScreen("dashboard"); }}
            style={{ background: "transparent", color: "#c8f135", border: "1px solid #c8f135" }}>
            Ir para o Dashboard →
          </button>
        </div>
      </div>
    </div>
  );

  // LOGIN
  if (screen === "login") return (
    <div className="dash-bg">
      <div className="dash-wrapper" style={{ maxWidth: 480 }}>
        <div className="dash-eyebrow">Business Intelligence</div>
        <h1 className="dash-h1">Entrar com <span>API Key</span></h1>
        <div className="input-card">
          <label className="input-label">API Key</label>
          <input className="dash-textarea" style={{ minHeight: "unset", padding: "10px 14px", fontFamily: "DM Mono, monospace", fontSize: 13 }}
            type="password" placeholder="sk-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()} />
          {error && <div style={{ marginTop: 10, fontFamily: "DM Mono, monospace", fontSize: 12, color: "#ff4d6d" }}>⚠ {error}</div>}
          <button className="dash-btn" onClick={handleLogin} disabled={loading} style={{ marginTop: 16 }}>
            {loading ? "Verificando..." : "Entrar →"}
          </button>
          <div style={{ marginTop: 16, fontFamily: "DM Mono, monospace", fontSize: 12, color: "#6b7090" }}>
            Não tem conta?{" "}
            <span onClick={() => { setScreen("register"); setError(""); }} style={{ color: "#c8f135", cursor: "pointer" }}>
              Criar conta grátis
            </span>
          </div>
        </div>
      </div>
    </div>
  );

  // DASHBOARD
  if (screen === "dashboard") return (
    <div className="dash-bg">
      <div className="dash-wrapper">
        <Header subtitle={dashData?.email} />

        {/* Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginBottom: 24 }}>
          {[
            { label: "Créditos Usados", value: dashData?.credits_used ?? "—", accent: false },
            { label: "Créditos Restantes", value: dashData?.credits_remaining ?? "—", accent: true },
            { label: "Buscas (7 dias)", value: dashData?.total_searches ?? "—", accent: false },
          ].map((s, i) => (
            <div key={i} className="input-card" style={{ marginBottom: 0, textAlign: "center" }}>
              <div style={{ fontFamily: "DM Mono, monospace", fontSize: 10, letterSpacing: "0.15em", textTransform: "uppercase", color: "#6b7090", marginBottom: 8 }}>{s.label}</div>
              <div style={{ fontFamily: "Syne, sans-serif", fontSize: "2rem", fontWeight: 800, color: s.accent ? "#c8f135" : "#eef0f7" }}>{s.value}</div>
            </div>
          ))}
        </div>

        {/* Barra de uso de créditos */}
        {dashData && (
          <div className="input-card" style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
              <span style={{ fontFamily: "DM Mono, monospace", fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase", color: "#6b7090" }}>Uso do plano {dashData.plan}</span>
              <span style={{ fontFamily: "DM Mono, monospace", fontSize: 11, color: "#c8f135" }}>
                {Math.round((dashData.credits_used / dashData.credits_limit) * 100)}%
              </span>
            </div>
            <div style={{ height: 6, background: "#1c1e27", borderRadius: 99, overflow: "hidden" }}>
              <div style={{
                height: "100%", borderRadius: 99,
                background: "linear-gradient(90deg, #3bffc8, #c8f135)",
                width: `${Math.min((dashData.credits_used / dashData.credits_limit) * 100, 100)}%`,
                transition: "width 0.8s ease"
              }} />
            </div>
          </div>
        )}

        {/* Gráfico de barras — buscas por dia */}
        {dashData?.chart && (
          <div className="input-card">
            <div style={{ fontFamily: "DM Mono, monospace", fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase", color: "#6b7090", marginBottom: 20 }}>
              Buscas nos últimos 7 dias
            </div>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 8, height: 100 }}>
              {dashData.chart.map((d, i) => {
                const max = Math.max(...dashData.chart.map(x => x.searches), 1);
                const pct = (d.searches / max) * 100;
                const label = new Date(d.date + "T00:00:00").toLocaleDateString("pt-BR", { weekday: "short" });
                return (
                  <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                    <div style={{ fontFamily: "DM Mono, monospace", fontSize: 10, color: "#c8f135" }}>
                      {d.searches > 0 ? d.searches : ""}
                    </div>
                    <div style={{ width: "100%", flex: 1, display: "flex", alignItems: "flex-end" }}>
                      <div style={{
                        width: "100%", borderRadius: "4px 4px 0 0",
                        background: pct > 0 ? "linear-gradient(180deg, #c8f135, #3bffc8)" : "#1c1e27",
                        height: `${Math.max(pct, 4)}%`,
                        transition: "height 0.6s ease",
                        minHeight: 4,
                      }} />
                    </div>
                    <div style={{ fontFamily: "DM Mono, monospace", fontSize: 9, color: "#6b7090", textTransform: "capitalize" }}>{label}</div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div style={{ marginTop: 24, textAlign: "center" }}>
          <button className="dash-btn" onClick={() => setScreen("search")}>
            Ir para Busca →
          </button>
        </div>
      </div>
    </div>
  );

  // SEARCH
  return (
    <div className="dash-bg">
      <div className="dash-wrapper">
        <Header />
        <div className="input-card">
          <label className="input-label">Empresas — uma por linha</label>
          <textarea className="dash-textarea" placeholder={"Apple\nPetrobras\nMagazine Luiza"}
            onChange={(e) => setInput(e.target.value)} />
          {error && <div style={{ marginTop: 10, fontFamily: "DM Mono, monospace", fontSize: 12, color: "#ff4d6d" }}>⚠ {error}</div>}
          <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
            <button className="dash-btn" onClick={handleSearch} disabled={loading}>
              {loading ? (
                <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ animation: "spin 1s linear infinite" }}><path d="M21 12a9 9 0 1 1-6.219-8.56" /></svg> Buscando...</>
              ) : (
                <><svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" /></svg> Buscar</>
              )}
            </button>
            {results.length > 0 && (
              <button className="dash-btn" onClick={exportCSV} style={{ background: "transparent", color: "#c8f135", border: "1px solid #c8f135" }}>
                <svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Exportar CSV
              </button>
            )}
          </div>
        </div>

        {results.length > 0 && (
          <>
            <div className="results-header">{results.length} resultado{results.length !== 1 ? "s" : ""}</div>
            <div className="results-grid">
              {results.map((r, i) => {
                const conf = r.confianca ? Math.round(r.confianca * 100) : 0;
                return (
                  <div className="card" key={i}>
                    <div>
                      <div className="card-name">{r.empresa}</div>
                      {r.erro ? (
                        <div style={{ color: "#ff4d6d", fontFamily: "DM Mono, monospace", fontSize: 13 }}>Erro: {r.erro}</div>
                      ) : (
                        <>
                          <div className="card-fields">
                            <div className="field"><div className="field-label">Receita (BRL)</div><div className="field-value accent">{r.faturamento_brl || "N/A"}</div></div>
                            <div className="field"><div className="field-label">Funcionários</div><div className="field-value">{r.funcionarios || "N/A"}</div></div>
                            <div className="field"><div className="field-label">Indústria</div><div className="field-value">{r.industria || "N/A"}</div></div>
                            <div className="field"><div className="field-label">Classificação</div><div className="field-value">{r.classificacao || "N/A"}</div></div>
                          </div>
                          <div className="conf-wrap">
                            <span className="conf-label">Confiança</span>
                            <div className="conf-bar-bg"><div className="conf-bar-fill" style={{ width: `${conf}%` }} /></div>
                            <span className="conf-pct">{conf}%</span>
                          </div>
                        </>
                      )}
                    </div>
                    <div className="badge-area">
                      <span className={`badge ${r.estimado ? "badge-sim" : "badge-nao"}`}>
                        {r.estimado ? "Estimado" : "Verificado"}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .dash-btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none !important; }
      `}</style>
    </div>
  );
}