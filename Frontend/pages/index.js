"use client";

import { useState, useEffect } from "react";
import axios from "axios";

const API = process.env.NEXT_PUBLIC_API_URL || "revenue.railway.internal";

export default function Home() {
  const [screen, setScreen] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [role, setRole] = useState("user");
  const [input, setInput] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [credits, setCredits] = useState(null);
  const [dashData, setDashData] = useState(null);
  const [adminUsers, setAdminUsers] = useState([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminMsg, setAdminMsg] = useState("");
  const [creditInputs, setCreditInputs] = useState({});

  // ambiguidade
  const [ambiguous, setAmbiguous] = useState([]); // [{original, options}]
  const [resolved, setResolved] = useState({});   // {original: chosenName}
  const [pendingCompanies, setPendingCompanies] = useState([]);

  const headers = () => ({ "X-API-Key": apiKey });

  const fetchCredits = async (key) => {
    try {
      const res = await axios.get(`${API}/me`, { headers: { "X-API-Key": key } });
      setCredits(res.data);
    } catch { setCredits(null); }
  };

  const fetchDashboard = async (key) => {
    try {
      const res = await axios.get(`${API}/dashboard`, { headers: { "X-API-Key": key } });
      setDashData(res.data);
    } catch { setDashData(null); }
  };

  const fetchAdminUsers = async () => {
    setAdminLoading(true);
    try {
      const res = await axios.get(`${API}/admin/users`, { headers: headers() });
      setAdminUsers(res.data);
    } catch { setAdminUsers([]); }
    finally { setAdminLoading(false); }
  };

  useEffect(() => {
    if (screen === "dashboard" && apiKey) fetchDashboard(apiKey);
    if (screen === "admin" && apiKey) fetchAdminUsers();
  }, [screen]);

  const handleLogin = async () => {
    if (!email.trim() || !password.trim()) { setError("Preencha email e senha."); return; }
    setError(""); setLoading(true);
    try {
      const res = await axios.post(`${API}/login`, { email: email.trim(), password: password.trim() });
      setApiKey(res.data.api_key);
      setRole(res.data.role);
      setCredits(res.data);
      setScreen("dashboard");
    } catch (e) {
      setError(e.response?.data?.detail || "Email ou senha incorretos.");
    } finally { setLoading(false); }
  };

  const handleRegister = async () => {
    if (!email.trim() || !password.trim()) { setError("Preencha todos os campos."); return; }
    if (password !== confirmPassword) { setError("As senhas não coincidem."); return; }
    if (password.length < 6) { setError("Senha deve ter ao menos 6 caracteres."); return; }
    setError(""); setLoading(true);
    try {
      const res = await axios.post(`${API}/register`, { email: email.trim(), password: password.trim() });
      setApiKey(res.data.api_key);
      setRole("user");
      await fetchCredits(res.data.api_key);
      setScreen("dashboard");
    } catch (e) {
      setError(e.response?.data?.detail || "Erro ao criar conta.");
    } finally { setLoading(false); }
  };

  const runBatch = async (companies) => {
    setLoading(true);
    try {
      const res = await axios.post(`${API}/batch`, companies, { headers: headers() });
      setResults(res.data);
      fetchCredits(apiKey);
      fetchDashboard(apiKey);
      setScreen("search");
    } catch (e) {
      setError(e.response?.data?.detail || "Erro ao buscar.");
    } finally { setLoading(false); }
  };

  const handleSearch = async () => {
    const companies = input.split("\n").filter((c) => c.trim() !== "");
    if (!companies.length) { setError("Insira ao menos uma empresa."); return; }
    setError(""); setLoading(true);
    setAmbiguous([]); setResolved({});

    try {
      const ambiguousFound = [];
      for (const company of companies) {
        try {
          const res = await axios.get(`${API}/disambiguate`, {
            params: { company },
            headers: headers(),
          });
          if (res.data.ambiguous && res.data.options.length > 1) {
            ambiguousFound.push({ original: company, options: res.data.options });
          }
        } catch { /* ignora erros individuais */ }
      }

      if (ambiguousFound.length > 0) {
        setAmbiguous(ambiguousFound);
        setPendingCompanies(companies);
        setLoading(false);
        return;
      }

      await runBatch(companies);
    } catch (e) {
      setError(e.response?.data?.detail || "Erro ao buscar.");
      setLoading(false);
    }
  };

  const handleResolveAmbiguity = async () => {
    // substitui empresas ambíguas pelas escolhas do usuário
    const finalCompanies = pendingCompanies.map((c) => resolved[c] || c);
    setAmbiguous([]);
    setPendingCompanies([]);
    await runBatch(finalCompanies);
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

  const handleAddCredits = async (userId) => {
    const amount = parseInt(creditInputs[userId] || "0");
    if (!amount || amount <= 0) return;
    try {
      await axios.post(`${API}/admin/credits`, { user_id: userId, credits: amount }, { headers: headers() });
      setAdminMsg(`✓ ${amount} créditos adicionados.`);
      setCreditInputs((prev) => ({ ...prev, [userId]: "" }));
      fetchAdminUsers();
      setTimeout(() => setAdminMsg(""), 3000);
    } catch (e) { setAdminMsg("Erro: " + (e.response?.data?.detail || "tente novamente.")); }
  };

  const handleUpdatePlan = async (userId, plan, creditsLimit) => {
    try {
      await axios.post(`${API}/admin/plan`, { user_id: userId, plan, credits_limit: creditsLimit }, { headers: headers() });
      setAdminMsg(`✓ Plano atualizado para ${plan}.`);
      fetchAdminUsers();
      setTimeout(() => setAdminMsg(""), 3000);
    } catch (e) { setAdminMsg("Erro: " + (e.response?.data?.detail || "tente novamente.")); }
  };

  const handleDeleteUser = async (userId, userEmail) => {
    if (!confirm(`Remover ${userEmail}?`)) return;
    try {
      await axios.delete(`${API}/admin/users/${userId}`, { headers: headers() });
      setAdminMsg("✓ Usuário removido.");
      fetchAdminUsers();
      setTimeout(() => setAdminMsg(""), 3000);
    } catch { setAdminMsg("Erro ao remover."); }
  };

  const logout = () => {
    setScreen("login"); setResults([]); setCredits(null);
    setDashData(null); setApiKey(""); setInput("");
    setEmail(""); setPassword(""); setConfirmPassword("");
    setRole("user"); setAdminUsers([]);
    setAmbiguous([]); setResolved({}); setPendingCompanies([]);
  };

  const mono = { fontFamily: "DM Mono, monospace" };
  const syne = { fontFamily: "Syne, sans-serif" };
  const inputStyle = { minHeight: "unset", padding: "10px 14px", ...mono, fontSize: 13 };
  const ErrorMsg = ({ msg }) => msg ? <div style={{ marginTop: 10, ...mono, fontSize: 12, color: "#ff4d6d" }}>⚠ {msg}</div> : null;

  const NavLink = ({ id, label }) => (
    <span onClick={() => setScreen(id)} style={{
      ...mono, fontSize: 11, cursor: "pointer",
      color: screen === id ? "#c8f135" : "#6b7090",
      borderBottom: screen === id ? "1px solid #c8f135" : "1px solid transparent",
      paddingBottom: 2,
    }}>{label}</span>
  );

  const Header = ({ subtitle }) => (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 40 }}>
      <div>
        <div className="dash-eyebrow">Business Intelligence</div>
        <h1 className="dash-h1" style={{ marginBottom: 0 }}>Company <span>Revenue</span> Dashboard</h1>
        {subtitle && <div style={{ ...mono, fontSize: 12, color: "#6b7090", marginTop: 6 }}>{subtitle}</div>}
      </div>
      <div style={{ textAlign: "right" }}>
        {credits && (
          <div style={{ ...mono, fontSize: 12, color: "#6b7090", marginBottom: 10 }}>
            <span style={{ color: "#c8f135" }}>{credits.credits_remaining}</span>/{credits.credits_limit} créditos
            <span style={{ marginLeft: 8, background: "#1c1e27", border: "1px solid #2a2d3a", borderRadius: 4, padding: "2px 8px" }}>{credits.plan}</span>
          </div>
        )}
        <div style={{ display: "flex", gap: 16, justifyContent: "flex-end" }}>
          <NavLink id="dashboard" label="Dashboard" />
          <NavLink id="search" label="Buscar" />
          {role === "admin" && <NavLink id="admin" label="⚙ Admin" />}
          <span onClick={logout} style={{ ...mono, fontSize: 11, color: "#6b7090", cursor: "pointer" }}>Sair →</span>
        </div>
      </div>
    </div>
  );

  // -------------------------
  // MODAL AMBIGUIDADE
  // -------------------------
  const AmbiguityModal = () => {
    const allResolved = ambiguous.every((a) => resolved[a.original]);
    return (
      <div style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)",
        zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}>
        <div style={{
          background: "#13141a", border: "1px solid #2a2d3a", borderRadius: 16,
          padding: 32, maxWidth: 560, width: "100%", maxHeight: "80vh", overflowY: "auto",
        }}>
          <div style={{ ...mono, fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase", color: "#c8f135", marginBottom: 8 }}>
            Ambiguidade detectada
          </div>
          <h2 style={{ ...syne, fontSize: "1.4rem", fontWeight: 800, color: "#eef0f7", marginBottom: 6 }}>
            Qual empresa você quer buscar?
          </h2>
          <p style={{ ...mono, fontSize: 12, color: "#6b7090", marginBottom: 24 }}>
            Encontramos múltiplas empresas com nomes similares. Selecione a correta para cada uma.
          </p>

          {ambiguous.map((item) => (
            <div key={item.original} style={{ marginBottom: 24 }}>
              <div style={{ ...mono, fontSize: 11, color: "#6b7090", marginBottom: 10 }}>
                Você digitou: <span style={{ color: "#eef0f7" }}>"{item.original}"</span>
              </div>
              <div style={{ display: "grid", gap: 8 }}>
                {item.options.map((opt, i) => {
                  const isSelected = resolved[item.original] === opt.name;
                  return (
                    <div key={i}
                      onClick={() => setResolved((prev) => ({ ...prev, [item.original]: opt.name }))}
                      style={{
                        background: isSelected ? "rgba(200,241,53,0.08)" : "#1c1e27",
                        border: `1px solid ${isSelected ? "#c8f135" : "#2a2d3a"}`,
                        borderRadius: 10, padding: "12px 16px", cursor: "pointer",
                        transition: "all 0.15s",
                      }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <div>
                          <div style={{ ...syne, fontSize: 14, fontWeight: 700, color: isSelected ? "#c8f135" : "#eef0f7", marginBottom: 2 }}>
                            {opt.name}
                          </div>
                          <div style={{ ...mono, fontSize: 11, color: "#6b7090" }}>
                            {opt.description}
                          </div>
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                          {opt.country && (
                            <span style={{ ...mono, fontSize: 10, color: "#6b7090", background: "#0b0c10", border: "1px solid #2a2d3a", borderRadius: 4, padding: "2px 6px" }}>
                              {opt.country}
                            </span>
                          )}
                          {isSelected && (
                            <span style={{ fontSize: 14 }}>✓</span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}

                {/* opção de manter o nome original */}
                <div
                  onClick={() => setResolved((prev) => ({ ...prev, [item.original]: item.original }))}
                  style={{
                    background: resolved[item.original] === item.original ? "rgba(200,241,53,0.08)" : "transparent",
                    border: `1px dashed ${resolved[item.original] === item.original ? "#c8f135" : "#2a2d3a"}`,
                    borderRadius: 10, padding: "10px 16px", cursor: "pointer", transition: "all 0.15s",
                  }}>
                  <div style={{ ...mono, fontSize: 12, color: "#6b7090" }}>
                    Manter "{item.original}" como está
                  </div>
                </div>
              </div>
            </div>
          ))}

          <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
            <button className="dash-btn" onClick={handleResolveAmbiguity} disabled={!allResolved || loading}
              style={{ flex: 1, justifyContent: "center", opacity: allResolved ? 1 : 0.5 }}>
              {loading ? "Buscando..." : "Confirmar e buscar →"}
            </button>
            <button onClick={() => { setAmbiguous([]); setPendingCompanies([]); }}
              style={{ ...mono, fontSize: 12, background: "transparent", border: "1px solid #2a2d3a", color: "#6b7090", borderRadius: 8, padding: "10px 16px", cursor: "pointer" }}>
              Cancelar
            </button>
          </div>
        </div>
      </div>
    );
  };

  // -------------------------
  // LOGIN
  // -------------------------
  if (screen === "login") return (
    <div className="dash-bg">
      <div className="dash-wrapper" style={{ maxWidth: 480 }}>
        <div className="dash-eyebrow">Business Intelligence</div>
        <h1 className="dash-h1">Entrar na <span>conta</span></h1>
        <div className="input-card">
          <label className="input-label">Email</label>
          <input className="dash-textarea" style={{ ...inputStyle, marginBottom: 12 }}
            type="email" placeholder="seu@email.com" value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()} />
          <label className="input-label">Senha</label>
          <input className="dash-textarea" style={inputStyle}
            type="password" placeholder="••••••••" value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()} />
          <ErrorMsg msg={error} />
          <button className="dash-btn" onClick={handleLogin} disabled={loading} style={{ marginTop: 16 }}>
            {loading ? "Entrando..." : "Entrar →"}
          </button>
          <div style={{ marginTop: 16, ...mono, fontSize: 12, color: "#6b7090" }}>
            Não tem conta?{" "}
            <span onClick={() => { setScreen("register"); setError(""); }} style={{ color: "#c8f135", cursor: "pointer" }}>
              Criar conta grátis
            </span>
          </div>
        </div>
      </div>
    </div>
  );

  // -------------------------
  // REGISTER
  // -------------------------
  if (screen === "register") return (
    <div className="dash-bg">
      <div className="dash-wrapper" style={{ maxWidth: 480 }}>
        <div className="dash-eyebrow">Business Intelligence</div>
        <h1 className="dash-h1">Criar <span>conta</span></h1>
        <div className="input-card">
          <label className="input-label">Email</label>
          <input className="dash-textarea" style={{ ...inputStyle, marginBottom: 12 }}
            type="email" placeholder="seu@email.com" value={email}
            onChange={(e) => setEmail(e.target.value)} />
          <label className="input-label">Senha</label>
          <input className="dash-textarea" style={{ ...inputStyle, marginBottom: 12 }}
            type="password" placeholder="Mínimo 6 caracteres" value={password}
            onChange={(e) => setPassword(e.target.value)} />
          <label className="input-label">Confirmar Senha</label>
          <input className="dash-textarea" style={inputStyle}
            type="password" placeholder="Repita a senha" value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleRegister()} />
          <ErrorMsg msg={error} />
          <button className="dash-btn" onClick={handleRegister} disabled={loading} style={{ marginTop: 16 }}>
            {loading ? "Criando..." : "Criar conta grátis"}
          </button>
          <div style={{ marginTop: 16, ...mono, fontSize: 12, color: "#6b7090" }}>
            Já tem conta?{" "}
            <span onClick={() => { setScreen("login"); setError(""); }} style={{ color: "#c8f135", cursor: "pointer" }}>Entrar</span>
          </div>
        </div>
      </div>
    </div>
  );

  // -------------------------
  // DASHBOARD
  // -------------------------
  if (screen === "dashboard") return (
    <div className="dash-bg">
      <div className="dash-wrapper">
        <Header subtitle={dashData?.email} />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginBottom: 24 }}>
          {[
            { label: "Créditos Usados", value: dashData?.credits_used ?? "—", accent: false },
            { label: "Créditos Restantes", value: dashData?.credits_remaining ?? "—", accent: true },
            { label: "Buscas (7 dias)", value: dashData?.total_searches ?? "—", accent: false },
          ].map((s, i) => (
            <div key={i} className="input-card" style={{ marginBottom: 0, textAlign: "center" }}>
              <div style={{ ...mono, fontSize: 10, letterSpacing: "0.15em", textTransform: "uppercase", color: "#6b7090", marginBottom: 8 }}>{s.label}</div>
              <div style={{ ...syne, fontSize: "2rem", fontWeight: 800, color: s.accent ? "#c8f135" : "#eef0f7" }}>{s.value}</div>
            </div>
          ))}
        </div>
        {dashData && (
          <div className="input-card" style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
              <span style={{ ...mono, fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase", color: "#6b7090" }}>Uso do plano {dashData.plan}</span>
              <span style={{ ...mono, fontSize: 11, color: "#c8f135" }}>{Math.round((dashData.credits_used / dashData.credits_limit) * 100)}%</span>
            </div>
            <div style={{ height: 6, background: "#1c1e27", borderRadius: 99, overflow: "hidden" }}>
              <div style={{ height: "100%", borderRadius: 99, background: "linear-gradient(90deg, #3bffc8, #c8f135)", width: `${Math.min((dashData.credits_used / dashData.credits_limit) * 100, 100)}%`, transition: "width 0.8s ease" }} />
            </div>
          </div>
        )}
        {dashData?.chart && (
          <div className="input-card">
            <div style={{ ...mono, fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase", color: "#6b7090", marginBottom: 20 }}>Buscas nos últimos 7 dias</div>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 8, height: 100 }}>
              {dashData.chart.map((d, i) => {
                const max = Math.max(...dashData.chart.map(x => x.searches), 1);
                const pct = (d.searches / max) * 100;
                const label = new Date(d.date + "T00:00:00").toLocaleDateString("pt-BR", { weekday: "short" });
                return (
                  <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                    <div style={{ ...mono, fontSize: 10, color: "#c8f135" }}>{d.searches > 0 ? d.searches : ""}</div>
                    <div style={{ width: "100%", flex: 1, display: "flex", alignItems: "flex-end" }}>
                      <div style={{ width: "100%", borderRadius: "4px 4px 0 0", background: pct > 0 ? "linear-gradient(180deg, #c8f135, #3bffc8)" : "#1c1e27", height: `${Math.max(pct, 4)}%`, transition: "height 0.6s ease", minHeight: 4 }} />
                    </div>
                    <div style={{ ...mono, fontSize: 9, color: "#6b7090", textTransform: "capitalize" }}>{label}</div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <div style={{ marginTop: 24, textAlign: "center" }}>
          <button className="dash-btn" onClick={() => setScreen("search")}>Ir para Busca →</button>
        </div>
      </div>
    </div>
  );

  // -------------------------
  // ADMIN
  // -------------------------
  if (screen === "admin") return (
    <div className="dash-bg">
      <div className="dash-wrapper">
        <Header />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <div style={{ ...mono, fontSize: 11, letterSpacing: "0.15em", textTransform: "uppercase", color: "#6b7090" }}>
            {adminUsers.length} usuário{adminUsers.length !== 1 ? "s" : ""}
          </div>
          {adminMsg && <div style={{ ...mono, fontSize: 12, color: adminMsg.startsWith("✓") ? "#3bffc8" : "#ff4d6d" }}>{adminMsg}</div>}
          <button className="dash-btn" onClick={fetchAdminUsers} style={{ padding: "8px 16px", fontSize: 12 }}>↻ Atualizar</button>
        </div>
        {adminLoading ? (
          <div style={{ ...mono, fontSize: 13, color: "#6b7090", textAlign: "center", padding: 40 }}>Carregando...</div>
        ) : (
          <div style={{ display: "grid", gap: 12 }}>
            {adminUsers.map((u) => (
              <div key={u.id} className="input-card" style={{ marginBottom: 0 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 16, alignItems: "start" }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                      <span style={{ ...syne, fontSize: 15, fontWeight: 700, color: "#eef0f7" }}>{u.email}</span>
                      <span style={{ ...mono, fontSize: 10, padding: "2px 8px", borderRadius: 99, background: u.role === "admin" ? "rgba(200,241,53,0.15)" : "rgba(107,112,144,0.15)", color: u.role === "admin" ? "#c8f135" : "#6b7090", border: `1px solid ${u.role === "admin" ? "rgba(200,241,53,0.3)" : "rgba(107,112,144,0.3)"}` }}>
                        {u.role}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: 16, marginBottom: 12 }}>
                      {[
                        { label: "Plano", value: u.plan },
                        { label: "Usado", value: u.credits_used },
                        { label: "Limite", value: u.credits_limit },
                        { label: "Restante", value: u.credits_limit - u.credits_used },
                      ].map((f, i) => (
                        <div key={i}>
                          <div style={{ ...mono, fontSize: 9, letterSpacing: "0.1em", textTransform: "uppercase", color: "#6b7090" }}>{f.label}</div>
                          <div style={{ ...mono, fontSize: 13, color: "#eef0f7" }}>{f.value}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input className="dash-textarea" style={{ ...inputStyle, width: 80, padding: "6px 10px" }}
                        type="number" min="1" placeholder="qtd"
                        value={creditInputs[u.id] || ""}
                        onChange={(e) => setCreditInputs((prev) => ({ ...prev, [u.id]: e.target.value }))} />
                      <button className="dash-btn" style={{ padding: "6px 14px", fontSize: 12 }} onClick={() => handleAddCredits(u.id)}>+ Créditos</button>
                      <select style={{ ...mono, fontSize: 12, background: "#1c1e27", border: "1px solid #2a2d3a", borderRadius: 8, color: "#eef0f7", padding: "6px 10px", cursor: "pointer" }}
                        value={u.plan}
                        onChange={(e) => {
                          const planLimits = { free: 10, pro: 500, scale: 999999 };
                          handleUpdatePlan(u.id, e.target.value, planLimits[e.target.value]);
                        }}>
                        <option value="free">Free (10)</option>
                        <option value="pro">Pro (500)</option>
                        <option value="scale">Scale (∞)</option>
                      </select>
                    </div>
                  </div>
                  {u.role !== "admin" && (
                    <button onClick={() => handleDeleteUser(u.id, u.email)}
                      style={{ ...mono, fontSize: 11, background: "rgba(255,77,109,0.1)", border: "1px solid rgba(255,77,109,0.2)", color: "#ff4d6d", borderRadius: 8, padding: "6px 12px", cursor: "pointer" }}>
                      Remover
                    </button>
                  )}
                </div>
                <div style={{ marginTop: 8, height: 3, background: "#1c1e27", borderRadius: 99, overflow: "hidden" }}>
                  <div style={{ height: "100%", borderRadius: 99, background: "linear-gradient(90deg, #3bffc8, #c8f135)", width: `${Math.min((u.credits_used / u.credits_limit) * 100, 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  // -------------------------
  // SEARCH
  // -------------------------
  return (
    <div className="dash-bg">
      {ambiguous.length > 0 && <AmbiguityModal />}

      <div className="dash-wrapper">
        <Header />
        <div className="input-card">
          <label className="input-label">Empresas — uma por linha</label>
          <textarea className="dash-textarea" placeholder={"Apple\nPetrobras\nMagazine Luiza"}
            onChange={(e) => setInput(e.target.value)} />
          <ErrorMsg msg={error} />
          <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
            <button className="dash-btn" onClick={handleSearch} disabled={loading}>
              {loading
                ? <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ animation: "spin 1s linear infinite" }}><path d="M21 12a9 9 0 1 1-6.219-8.56" /></svg> {ambiguous.length > 0 ? "Verificando..." : "Buscando..."}</>
                : <><svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" /></svg> Buscar</>
              }
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
                        <div style={{ color: "#ff4d6d", ...mono, fontSize: 13 }}>Erro: {r.erro}</div>
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
                      <span className={`badge ${r.estimado ? "badge-sim" : "badge-nao"}`}>{r.estimado ? "Estimado" : "Verificado"}</span>
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
        select option { background: #13141a; }
      `}</style>
    </div>
  );
}