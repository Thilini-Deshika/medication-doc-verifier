import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, Job } from "./api";

/**
 * Job state for the pipeline view. The initial state comes from a normal fetch
 * (so a page refresh mid-processing shows the right stage immediately), then a
 * server-sent-events stream pushes every change into the query cache.
 */
export function useJobStream(id: string) {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["job", id], queryFn: () => api.job(id) });
  const status = query.data?.status;
  const live = status === "queued" || status === "running";
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!live) return;
    const source = new EventSource(`/api/jobs/${id}/events`);
    source.onopen = () => setConnected(true);
    source.addEventListener("job", (event) => {
      const job = JSON.parse((event as MessageEvent).data) as Job;
      qc.setQueryData(["job", id], job);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      if (job.status === "succeeded" || job.status === "failed") {
        qc.invalidateQueries({ queryKey: ["result", id] });
        source.close();
        setConnected(false);
      }
    });
    // EventSource reconnects by itself; surface the gap to the user.
    source.onerror = () => setConnected(false);
    return () => {
      source.close();
      setConnected(false);
    };
  }, [id, live, qc]);

  return { ...query, live, connected };
}
