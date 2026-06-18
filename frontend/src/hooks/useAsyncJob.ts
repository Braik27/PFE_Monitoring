import { useState, useEffect, useRef, useCallback } from "react";

type JobStatus = "idle" | "PENDING" | "RUNNING" | "DONE" | "ERROR";

interface JobProgressMessage {
  type: "job_progress";
  job_id: string;
  progress: number;
  step_label: string;
  status: JobStatus;
  error?: string;
}

interface JobResponse {
  ok?: boolean;
  job_id?: string;
  progress?: number;
  step_label?: string;
  status?: JobStatus;
  error?: string;
}

interface JobResult {
  ok?: boolean;
  [key: string]: unknown;
}

export function useAsyncJob() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [stepLabel, setStepLabel] = useState("");
  const [result, setResult] = useState<JobResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<number | null>(null);
  const fetchingRef = useRef(false);

  const cleanup = useCallback(() => {
    wsRef.current?.close();
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  const reset = useCallback(() => {
    cleanup();
    fetchingRef.current = false;
    setJobId(null);
    setStatus("idle");
    setProgress(0);
    setStepLabel("");
    setResult(null);
    setError(null);
  }, [cleanup]);

  const fetchResult = useCallback(async (jid: string) => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const res = await fetch(`/api/smart/jobs/${jid}/result`, { credentials: "include" });
      const data: JobResult = await res.json();
      if (data.ok || res.ok) {
        setResult(data);
        setStatus("DONE");
        setProgress(100);
      } else {
        throw new Error((data.error as string) || "Erreur résultat");
      }
    } catch (e) {
      setStatus("ERROR");
      setError(e instanceof Error ? e.message : "Erreur inconnue");
    }
  }, []);

  const startPolling = useCallback((jid: string) => {
    if (pollRef.current !== null) return;
    pollRef.current = window.setInterval(async () => {
      try {
        const res = await fetch(`/api/smart/jobs/${jid}`, { credentials: "include" });
        if (!res.ok) return;
        const data: JobResponse = await res.json();
        setProgress(data.progress || 0);
        setStepLabel(data.step_label || "");
        setStatus(data.status || "RUNNING");
        if (data.status === "DONE") {
          if (pollRef.current !== null) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          await fetchResult(jid);
        }
        if (data.status === "ERROR") {
          if (pollRef.current !== null) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          setError(data.error || "Erreur inconnue");
        }
      } catch (_) {
        // ignore
      }
    }, 2000);
  }, [fetchResult]);

  const connectWS = useCallback((jid: string) => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/alerts`);
    wsRef.current = ws;

    ws.onmessage = async (evt: MessageEvent) => {
      try {
        const msg: JobProgressMessage = JSON.parse(evt.data);
        if (msg.type !== "job_progress" || msg.job_id !== jid) return;

        setProgress(msg.progress || 0);
        setStepLabel(msg.step_label || "");
        setStatus(msg.status);

        if (msg.status === "DONE") {
          wsRef.current = null;
          ws.close();
          await fetchResult(jid);
        }
        if (msg.status === "ERROR") {
          wsRef.current = null;
          ws.close();
          setError(msg.error || "Erreur inconnue");
        }
      } catch (_) {
        // ignore
      }
    };

    ws.onclose = () => {
      if (wsRef.current === ws) {
        startPolling(jid);
      }
    };
    ws.onerror = () => startPolling(jid);
  }, [fetchResult, startPolling]);

  const submit = useCallback(async (url: string, formData: BodyInit) => {
    reset();
    setStatus("PENDING");

    try {
      const res = await fetch(url, {
        method: "POST",
        body: formData,
        credentials: "include",
      });
      const data: JobResponse = await res.json();

      if (!res.ok || !data.ok) throw new Error(data.error || "Erreur soumission");

      const jid = data.job_id!;
      setJobId(jid);
      setStatus("RUNNING");
      connectWS(jid);

      try {
        const sres = await fetch(`/api/smart/jobs/${jid}`, { credentials: "include" });
        if (sres.ok) {
          const sdata: JobResponse = await sres.json();
          setProgress(sdata.progress || 0);
          setStepLabel(sdata.step_label || "");
          setStatus(sdata.status || "RUNNING");
          if (sdata.status === "DONE") {
            await fetchResult(jid);
          }
          if (sdata.status === "ERROR") {
            setError(sdata.error || "Erreur inconnue");
          }
        }
      } catch (_) {
        // ignore
      }

      return jid;
    } catch (e) {
      setStatus("ERROR");
      setError(e instanceof Error ? e.message : "Erreur inconnue");
      throw e;
    }
  }, [reset, connectWS, fetchResult]);

  return { submit, jobId, status, progress, stepLabel, result, error, reset };
}
