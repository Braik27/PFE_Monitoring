import { useState, useCallback, useRef, useEffect } from "react";

export type JobStatus = "idle" | "PENDING" | "RUNNING" | "DONE" | "ERROR";

/**
 * Contenu de `result` renvoyé par GET /api/flux/jobs/<id> quand status=DONE.
 * ⚠️ Inféré depuis l'usage frontend (AnalysisResult dans Analyze.tsx) et la
 * construction du résultat côté backend — pas depuis un contrat partagé typé.
 */
export interface AsyncJobResult {
  analysis_id?: number;
  flux_id?: string;
  resume?: Record<string, unknown>;
  stats?: Record<string, unknown>;
  nb_critique?: number;
  nb_warning?: number;
  ecarts?: Array<Record<string, unknown>>;
}

/** Réponse de GET /api/flux/jobs/<id> (flux_api.get_job_async). */
interface JobPollResponse {
  job_id?: string;
  flux_id?: string | null;
  status?: JobStatus;
  analyst?: string | null;
  created_at?: string | null;
  result?: AsyncJobResult | null;
  error?: string | null;
}

/** Réponse de POST /api/flux/comparer. */
interface JobSubmitResponse {
  job_id?: string;
  erreur?: string;
  error?: string;
}

interface JobState {
  status: JobStatus;
  progress: number;
  stepLabel: string;
  result: AsyncJobResult | null;
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

        const data: JobPollResponse = await res.json();
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

      const data: JobSubmitResponse = await res.json();

      if (!res.ok) {
        throw new Error(data.erreur ?? data.error ?? "Erreur serveur");
      }

      // Le backend renvoie toujours job_id sur une réponse 2xx (flux_api.comparer)
      const jobId: string = data.job_id ?? "";

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