import { useState, useEffect, useRef, useCallback } from "react";

type JobStatus = "idle" | "PENDING" | "RUNNING" | "DONE" | "ERROR";

interface AsyncAnalysisProgressProps {
  formData: FormData;
  onComplete?: (result: any) => void;
  onError?: (error: string) => void;
}

const STEPS = [
  { pct: 10,  key: "reading",   label: "Lecture",     icon: "📂" },
  { pct: 30,  key: "cleaning",  label: "Nettoyage",   icon: "🧹" },
  { pct: 50,  key: "comparing", label: "Comparaison", icon: "🔍" },
  { pct: 70,  key: "ia",        label: "IA",          icon: "🤖" },
  { pct: 90,  key: "saving",    label: "Sauvegarde",  icon: "💾" },
  { pct: 100, key: "done",      label: "Terminé",     icon: "✅" },
];

export default function AsyncAnalysisProgress({
  formData, onComplete, onError,
}: AsyncAnalysisProgressProps) {
  const [jobId,     setJobId]     = useState<string | null>(null);
  const [status,    setStatus]    = useState<JobStatus>("idle");
  const [progress,  setProgress]  = useState(0);
  const [stepLabel, setStepLabel] = useState("Préparation...");
  const [errorMsg,  setErrorMsg]  = useState<string | null>(null);
  const [logs,      setLogs]      = useState<string[]>([]);
  const [logsOpen,  setLogsOpen]  = useState(false);

  const wsRef      = useRef<WebSocket | null>(null);
  const pollRef    = useRef<number | null>(null);
  const startedRef = useRef(false);
  const doneRef    = useRef(false);
  const jobIdRef   = useRef<string | null>(null); // mirror for use in callbacks

  const addLog = useCallback((msg: string) => {
    const ts = new Date().toLocaleTimeString("fr-FR");
    setLogs(prev => [...prev.slice(-19), `[${ts}] ${msg}`]);
  }, []);

  const stopAll = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // ── fetchResult — un seul appel max ──────────────────────────────
  const fetchResult = useCallback(async (jid: string) => {
    if (doneRef.current) return;
    doneRef.current = true;
    stopAll();
    try {
      addLog("📦 Récupération du résultat...");
      const res  = await fetch(`/api/smart/jobs/${jid}/result`, { credentials: "include" });
      const data = await res.json();
      if (res.ok && (data.ok || data.status === "DONE")) {
        addLog(`✅ Terminé — ${data.n_anomalies ?? data.anomalies?.length ?? 0} anomalie(s)`);
        setStatus("DONE");
        setProgress(100);
        onComplete?.(data);
      } else {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // Ne pas marquer comme erreur si c'est juste un résultat déjà consommé
      if (!msg.includes("404") && !msg.includes("introuvable")) {
        setErrorMsg(msg);
        setStatus("ERROR");
        onError?.(msg);
      } else {
        // Réessayer une fois après délai (résultat peut être temporairement absent)
        setTimeout(async () => {
          try {
            const res2  = await fetch(`/api/smart/jobs/${jid}/result`, { credentials: "include" });
            const data2 = await res2.json();
            if (res2.ok && (data2.ok || data2.status === "DONE")) {
              setStatus("DONE"); setProgress(100); onComplete?.(data2);
            }
          } catch { /* ignore */ }
        }, 1000);
      }
      addLog(`❌ ${msg}`);
    }
  }, [addLog, stopAll, onComplete, onError]);

  // ── Polling ───────────────────────────────────────────────────────
  const startPolling = useCallback((jid: string) => {
    if (pollRef.current !== null) return;
    addLog("🔄 Polling HTTP activé (1.5s)...");

    pollRef.current = window.setInterval(async () => {
      if (doneRef.current) {
        window.clearInterval(pollRef.current!); pollRef.current = null; return;
      }
      try {
        const res = await fetch(`/api/smart/jobs/${jid}`, { credentials: "include" });

        if (res.status === 404) {
          window.clearInterval(pollRef.current!); pollRef.current = null;
          if (!doneRef.current) {
            doneRef.current = true;
            const errMsg = "Job introuvable — le serveur a redémarré. Relancez l'analyse.";
            setErrorMsg(errMsg); setStatus("ERROR"); onError?.(errMsg);
            addLog("❌ " + errMsg);
          }
          return;
        }

        if (!res.ok) return;
        const data = await res.json();
        setProgress(data.progress   || 0);
        setStepLabel(data.step_label || "...");

        if (data.status === "DONE") {
          window.clearInterval(pollRef.current!); pollRef.current = null;
          fetchResult(jid);
        } else if (data.status === "ERROR") {
          window.clearInterval(pollRef.current!); pollRef.current = null;
          doneRef.current = true;
          const errMsg = data.error || "Erreur inconnue";
          setErrorMsg(errMsg); setStatus("ERROR"); onError?.(errMsg);
        } else {
          setStatus(data.status || "RUNNING");
        }
      } catch { /* réseau instable — retry */ }
    }, 1500);
  }, [addLog, fetchResult, onError]);

  // ── WebSocket ─────────────────────────────────────────────────────
  const connectWS = useCallback((jid: string) => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws    = new WebSocket(`${proto}://${window.location.host}/ws/alerts`);
    wsRef.current = ws;

    ws.onopen = () => {
      addLog("🔌 WebSocket connecté — écoute progression...");
      // ── FIX CRITIQUE : poll immédiat après connexion WS ────────────
      // Le job peut avoir terminé pendant que le WS se connectait.
      // On vérifie le statut dès que le WS est établi.
      // Si DONE → fetchResult. Sinon on laisse le WS travailler.
      fetch(`/api/smart/jobs/${jid}`, { credentials: "include" })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data || doneRef.current) return;
          setProgress(data.progress   || 0);
          setStepLabel(data.step_label || "...");
          if (data.status === "DONE") {
            addLog("⚡ Job déjà terminé — récupération du résultat...");
            fetchResult(jid);
          } else if (data.status === "ERROR") {
            doneRef.current = true;
            setErrorMsg(data.error || "Erreur"); setStatus("ERROR");
            onError?.(data.error || "Erreur");
          } else {
            setStatus(data.status || "RUNNING");
          }
        })
        .catch(() => { /* ignore */ });
    };

    ws.onclose = () => {
      if (wsRef.current === ws) {
        wsRef.current = null;
        if (!doneRef.current) {
          addLog("📡 WS fermé — bascule vers polling...");
          startPolling(jid);
        }
      }
    };

    ws.onerror = () => addLog("⚠️ WS erreur — bascule polling...");

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data as string);
        if (msg.type !== "job_progress" || msg.job_id !== jid) return;
        setProgress(msg.progress   || 0);
        setStepLabel(msg.step_label || "...");
        if (msg.progress > 0) addLog(`${msg.step_label} — ${msg.progress}%`);
        if (msg.status === "DONE")  { ws.close(); fetchResult(jid); }
        if (msg.status === "ERROR") {
          ws.close(); doneRef.current = true;
          setErrorMsg(msg.error || "Erreur"); setStatus("ERROR");
          onError?.(msg.error || "Erreur");
        }
        if (msg.status !== "DONE" && msg.status !== "ERROR") {
          setStatus(msg.status || "RUNNING");
        }
      } catch { /* ignore */ }
    };
  }, [addLog, fetchResult, startPolling, onError]);

  // ── Submit ────────────────────────────────────────────────────────
  const startAnalysis = useCallback(async () => {
    if (startedRef.current) return;
    startedRef.current = true;
    doneRef.current    = false;
    setStatus("PENDING");
    addLog("🚀 Envoi des fichiers au serveur...");
    try {
      const res  = await fetch("/api/smart/run-async", {
        method: "POST", body: formData, credentials: "include",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Erreur soumission");
      const jid = data.job_id;
      setJobId(jid);
      jobIdRef.current = jid;
      setStatus("RUNNING");
      addLog(`✅ Job soumis : ${jid.slice(0, 8)}…`);
      addLog("📡 Connexion WebSocket...");

      // connectWS fait un poll immédiat dans ws.onopen — pas besoin d'un poll séparé ici
      connectWS(jid);

    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus("ERROR"); setErrorMsg(msg); onError?.(msg);
      addLog(`❌ Erreur : ${msg}`);
    }
  }, [formData, addLog, connectWS, onError]);

  useEffect(() => {
    startAnalysis();
    return stopAll;
  }, []); // eslint-disable-line

  const isDone    = status === "DONE";
  const isError   = status === "ERROR";
  const isRunning = status === "RUNNING" || status === "PENDING";

  return (
    <div style={{
      background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)",
      borderRadius: 16, padding: "28px 24px", color: "#f1f5f9",
      fontFamily: "'DM Sans', system-ui, sans-serif",
      boxShadow: "0 25px 50px rgba(0,0,0,0.4)",
      position: "relative", overflow: "hidden",
    }}>
      {isRunning && (
        <div style={{
          position: "absolute", top: -60, right: -60, width: 200, height: 200,
          borderRadius: "50%", pointerEvents: "none",
          background: "radial-gradient(circle, rgba(59,130,246,0.12) 0%, transparent 70%)",
        }} />
      )}

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 24 }}>
        <div style={{
          width: 52, height: 52, borderRadius: 14, flexShrink: 0,
          background: isError ? "rgba(239,68,68,0.2)" : isDone ? "rgba(34,197,94,0.2)" : "rgba(59,130,246,0.15)",
          border: `1px solid ${isError ? "rgba(239,68,68,0.35)" : isDone ? "rgba(34,197,94,0.35)" : "rgba(59,130,246,0.3)"}`,
          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 26,
        }}>
          {isError ? "❌" : isDone ? "✅" : (
            <div style={{
              width: 26, height: 26, borderRadius: "50%",
              border: "3px solid rgba(59,130,246,0.25)",
              borderTop: "3px solid #3b82f6",
              animation: "spin 0.9s linear infinite",
            }} />
          )}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 18, fontWeight: 700, letterSpacing: "-0.3px" }}>
            {isError ? "Analyse échouée" : isDone ? "Analyse terminée !" : isRunning ? "Analyse en cours…" : "Préparation..."}
          </div>
          {jobId && (
            <div style={{ fontSize: 11, color: "rgba(148,163,184,0.7)", fontFamily: "monospace", marginTop: 2 }}>
              Job : {jobId.slice(0, 12)}…
            </div>
          )}
        </div>
        {isRunning && (
          <div style={{ fontSize: 32, fontWeight: 800, color: "#3b82f6", letterSpacing: "-1px" }}>
            {progress}%
          </div>
        )}
      </div>

      {/* Barre progression */}
      {!isError && (
        <div style={{ marginBottom: 24 }}>
          <div style={{ height: 6, borderRadius: 99, background: "rgba(255,255,255,0.07)", overflow: "hidden" }}>
            <div style={{
              height: "100%", borderRadius: 99, width: `${progress}%`,
              transition: "width 0.7s cubic-bezier(0.4,0,0.2,1)",
              background: isDone ? "linear-gradient(90deg,#22c55e,#4ade80)" : "linear-gradient(90deg,#3b82f6,#8b5cf6)",
              boxShadow: isDone ? "0 0 10px rgba(34,197,94,0.5)" : "0 0 10px rgba(59,130,246,0.5)",
            }} />
          </div>
          {stepLabel && (
            <div style={{ fontSize: 12, color: "rgba(148,163,184,0.8)", marginTop: 7, fontStyle: "italic" }}>
              {stepLabel}
            </div>
          )}
        </div>
      )}

      {/* Erreur */}
      {isError && (
        <div style={{
          background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: 10, padding: "12px 16px", color: "#fca5a5", fontSize: 13, marginBottom: 20,
        }}>
          <strong>Erreur :</strong> {errorMsg}
        </div>
      )}

      {/* Étapes */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 8, marginBottom: 20 }}>
        {STEPS.map((step) => {
          const done    = progress >= step.pct;
          const current = isRunning && progress >= step.pct - 22 && progress < step.pct;
          return (
            <div key={step.key} style={{
              display: "flex", flexDirection: "column", alignItems: "center",
              gap: 6, padding: "10px 4px", borderRadius: 10, transition: "all 0.4s ease",
              animation: current ? "pulse 1.5s ease-in-out infinite" : "none",
              background: done ? "rgba(59,130,246,0.12)" : current ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.03)",
              border: `1px solid ${done ? "rgba(59,130,246,0.25)" : "rgba(255,255,255,0.06)"}`,
            }}>
              <span style={{ fontSize: 18, filter: done || current ? "none" : "grayscale(1) opacity(0.35)" }}>
                {step.icon}
              </span>
              <span style={{
                fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.5px",
                color: done ? "#94a3b8" : current ? "#e2e8f0" : "rgba(148,163,184,0.35)",
              }}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      {/* Logs */}
      <button onClick={() => setLogsOpen(o => !o)} style={{
        background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 8, padding: "6px 12px", fontSize: 11, color: "#94a3b8",
        cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
      }}>
        <span>📋</span>
        <span>Logs en temps réel ({logs.length})</span>
        <span style={{ marginLeft: 4 }}>{logsOpen ? "▲" : "▼"}</span>
      </button>

      {logsOpen && (
        <div style={{
          marginTop: 8, background: "#020617", borderRadius: 10, padding: "12px 14px",
          fontFamily: "monospace", fontSize: 11, lineHeight: 1.7,
          maxHeight: 160, overflowY: "auto", border: "1px solid rgba(255,255,255,0.06)",
        }}>
          {logs.length === 0
            ? <span style={{ color: "rgba(148,163,184,0.4)" }}>En attente...</span>
            : logs.map((line, i) => (
                <div key={i} style={{
                  color: line.includes("❌") ? "#f87171" : line.includes("✅") ? "#4ade80" : "#64748b",
                }}>{line}</div>
              ))
          }
        </div>
      )}

      <style>{`
        @keyframes spin  { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.55; } }
      `}</style>
    </div>
  );
}