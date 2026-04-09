"use client";

import { useState } from "react";
import axios from "axios";

export default function Home() {
  const [input, setInput] = useState("");
  const [results, setResults] = useState([]);

  const handleSearch = async () => {
    const companies = input.split("\n").filter((c) => c.trim() !== "");
    const res = await axios.post("http://localhost:8000/batch", companies);
    setResults(res.data);
  };

  return (
    <div className="dash-bg">
      <div className="dash-wrapper">
        <div className="dash-eyebrow">Business Intelligence</div>
        <h1 className="dash-h1">
          Company <span>Revenue</span> Dashboard
        </h1>

        <div className="input-card">
          <label className="input-label">Empresas — uma por linha</label>
          <textarea
            className="dash-textarea"
            placeholder={"Apple\nPetrobras\nMagazine Luiza"}
            onChange={(e) => setInput(e.target.value)}
          />
          <button className="dash-btn" onClick={handleSearch}>
            <svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              <circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" />
            </svg>
            Buscar
          </button>
        </div>

        {results.length > 0 && (
          <>
            <div className="results-header">
              {results.length} resultado{results.length !== 1 ? "s" : ""}
            </div>
            <div className="results-grid">
              {results.map((r, i) => {
                const conf = r.confianca ? Math.round(r.confianca * 100) : 0;
                return (
                  <div className="card" key={i}>
                    <div>
                      <div className="card-name">{r.empresa}</div>
                      <div className="card-fields">
                        <div className="field">
                          <div className="field-label">Receita (BRL)</div>
                          <div className="field-value accent">{r.faturamento_brl || "N/A"}</div>
                        </div>
                        <div className="field">
                          <div className="field-label">Funcionários</div>
                          <div className="field-value">{r.funcionarios || "N/A"}</div>
                        </div>
                        <div className="field">
                          <div className="field-label">Indústria</div>
                          <div className="field-value">{r.industria || "N/A"}</div>
                        </div>
                        <div className="field">
                          <div className="field-label">Classificação</div>
                          <div className="field-value">{r.classificacao || "N/A"}</div>
                        </div>
                      </div>
                      <div className="conf-wrap">
                        <span className="conf-label">Confiança</span>
                        <div className="conf-bar-bg">
                          <div className="conf-bar-fill" style={{ width: `${conf}%` }} />
                        </div>
                        <span className="conf-pct">{conf}%</span>
                      </div>
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
    </div>
  );
}