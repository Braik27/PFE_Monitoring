import { useState, useCallback, useRef, useEffect } from "react";

export type JobStatus = "idle" | "PENDING" | "RUNNING" | "DONE" | "ERROR";

interface JobState {
  status: JobStatus;
  progress: number;
  stepLabel: string;
  result: any | null;
  error: string | null;
  jobId: string | null;
}

const INIT: JobState = {
  status: "idle",
  progress: 0,
  stepLabel: "",
  result: null,
  error: null,
  jobId: null,
};

export function useAsyncJob() {
  const [state, setState] = useState<JobState>(INIT);
  const pollRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    setState(INIT);
  }, [stopPolling]);

  // Poll toutes les 2 secondes jusqu'à DONE ou ERROR
  const startPolling = useCallback((jobId: string) => {
    if (pollRef.current !== null) return;

    pollRef.current = window.setInterval(async () => {
      try {
        const res = await fetch(`/api/flux/jobs/${jobId}`, {
          credentials: "include",
        });
        if (!res.ok) return;

        const data = await res.json();
        const status: JobStatus = data.status ?? "RUNNING";

        setState(prev => ({
          ...prev,
          status,
          jobId,
          // Simuler la progression visuellement
          progress: status === "DONE" ? 100
                  : status === "RUNNING" ? Math.min(prev.progress + 10, 90)
                  : prev.progress,
          stepLabel: status === "PENDING" ? "En attente..."
                   : status === "RUNNING" ? "Analyse en cours..."
                   : status === "DONE"    ? "Terminé ✓"
                   : "Erreur",
          result:  status === "DONE"  ? data.result ?? null : prev.result,
          error:   status === "ERROR" ? data.error  ?? "Erreur inconnue" : prev.error,
        }));

        if (status === "DONE" || status === "ERROR") {
          stopPolling();
        }
      } catch (_) {
        // réseau indisponible — on réessaie au prochain tick
      }
    }, 2000);
  }, [stopPolling]);

  // Soumettre les fichiers et démarrer le polling
  const submit = useCallback(async (formData: FormData) => {
    stopPolling();
    setState({ ...INIT, status: "PENDING", stepLabel: "Envoi des fichiers..." });

    try {
      const res = await fetch("/api/flux/comparer", {
        method: "POST",
        body: formData,
        credentials: "include",
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.erreur ?? data.error ?? "Erreur serveur");
      }

      const jobId: string = data.job_id;

      setState(prev => ({
        ...prev,
        jobId,
        status: "RUNNING",
        progress: 5,
        stepLabel: "Analyse lancée...",
      }));

      startPolling(jobId);
      return jobId;

    } catch (e) {
      const msg = e instanceof Error ? e.message : "Erreur inconnue";
      setState(prev => ({ ...prev, status: "ERROR", error: msg }));
      throw e;
    }
  }, [stopPolling, startPolling]);

  return {
    ...state,
    submit,
    reset,
  };
}