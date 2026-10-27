import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, documentUrl } from "../api";
import { PdfViewer, Highlight } from "../components/PdfViewer";
import { PipelineView } from "../components/PipelineView";
import { Results } from "../components/Results";
import { useJobStream } from "../useJobStream";

export function JobPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const job = useJobStream(id);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [showPipeline, setShowPipeline] = useState(false);

  const finished = job.data?.status === "succeeded";
  const result = useQuery({
    queryKey: ["result", id],
    queryFn: () => api.result(id),
    enabled: finished,
  });

  const retry = useMutation({
    mutationFn: () => api.retry(id),
    onSuccess: (j) => qc.setQueryData(["job", id], j),
  });

  if (job.isLoading) {
    return (
      <div className="page">
        <div className="skeleton" style={{ height: 120 }} />
        <div className="skeleton" style={{ height: 320 }} />
      </div>
    );
  }
  if (job.error) {
    const notFound = job.error instanceof ApiError && job.error.status === 404;
    return (
      <div className="page">
        <div className="card" role="alert">
          <h2>{notFound ? "Document not found" : "Could not load this document"}</h2>
          <p className="muted">{notFound ? "It may have been removed, or it belongs to another account." : job.error.message}</p>
          <Link to="/" className="btn">
            Back to documents
          </Link>
        </div>
      </div>
    );
  }
  const data = job.data!;

  return (
    <div className="page page-wide">
      <div className="job-head">
        <div>
          <Link to="/" className="link">
            All documents
          </Link>
          <h1>{data.document.filename}</h1>
          <div className="muted">Verified against {data.reference.title}</div>
        </div>
        <div className="job-head-right">
          <span className={`status status-${data.status}`}>{data.status}</span>
          {finished && (
            <button className="btn btn-ghost" onClick={() => setShowPipeline((v) => !v)}>
              {showPipeline ? "Hide" : "Show"} pipeline details
            </button>
          )}
        </div>
      </div>

      {(!finished || showPipeline) && (
        <PipelineView job={data} connected={job.connected} onRetry={() => retry.mutate()} retrying={retry.isPending} />
      )}
      {retry.error && (
        <div className="banner banner-error" role="alert">
          {retry.error.message}
        </div>
      )}

      {finished && (
        <div className="split">
          <section className="pane pane-doc" aria-label="Primary document">
            <div className="pane-head">Primary document</div>
            <PdfViewer url={documentUrl(id)} highlight={highlight} />
          </section>
          <section className="pane pane-results" aria-label="Analysis">
            {result.isLoading && (
              <div>
                <div className="skeleton" style={{ height: 120 }} />
                <div className="skeleton" style={{ height: 220 }} />
              </div>
            )}
            {result.error && (
              <div className="banner banner-error" role="alert">
                Could not load the analysis. {result.error.message}{" "}
                <button className="link" onClick={() => result.refetch()}>
                  Retry
                </button>
              </div>
            )}
            {result.data && (
              <Results
                result={result.data}
                referenceTitle={data.reference.title}
                onFocusSource={(page, text) => page && setHighlight({ page, text, nonce: Date.now() })}
              />
            )}
          </section>
        </div>
      )}
    </div>
  );
}
