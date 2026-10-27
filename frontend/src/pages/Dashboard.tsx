import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Job, STAGE_ORDER } from "../api";
import { Dropzone } from "../components/Dropzone";
import { formatDateTime } from "../format";

function optimisticJob(file: File, referenceId: string, referenceTitle: string): Job {
  return {
    id: `pending-${Date.now()}`,
    status: "queued",
    current_stage: null,
    error: null,
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    document: { id: "", filename: file.name, title: file.name, page_count: null },
    reference: { id: referenceId, title: referenceTitle },
    stages: STAGE_ORDER.map((name) => ({
      name,
      status: "pending",
      started_at: null,
      finished_at: null,
      duration_ms: null,
      error: null,
      detail: null,
    })),
    optimistic: true,
  };
}

export function Dashboard() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const refs = useQuery({ queryKey: ["references"], queryFn: api.references });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobs });
  const [file, setFile] = useState<File | null>(null);
  const [referenceId, setReferenceId] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!referenceId && refs.data?.length) setReferenceId(refs.data[0].id);
  }, [refs.data, referenceId]);

  const upload = useMutation({
    mutationFn: ({ file, referenceId }: { file: File; referenceId: string }) => api.upload(file, referenceId),
    // Optimistic update: the job appears in the list immediately and is rolled back if the upload fails.
    onMutate: async ({ file, referenceId }) => {
      setNotice(null);
      await qc.cancelQueries({ queryKey: ["jobs"] });
      const previous = qc.getQueryData<Job[]>(["jobs"]);
      const title = refs.data?.find((r) => r.id === referenceId)?.title ?? "";
      qc.setQueryData<Job[]>(["jobs"], (old = []) => [optimisticJob(file, referenceId, title), ...old]);
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx) qc.setQueryData(["jobs"], ctx.previous);
    },
    onSuccess: ({ job, duplicate }) => {
      qc.setQueryData(["job", job.id], job);
      setFile(null);
      if (duplicate) setNotice("This file was uploaded moments ago. Showing the existing analysis.");
      navigate(`/jobs/${job.id}`);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const noReferences = refs.isSuccess && refs.data.length === 0;
  const canSubmit = !!file && !!referenceId && !upload.isPending;

  return (
    <div className="page">
      <section className="card">
        <h1>Verify a document</h1>
        <p className="muted">Upload a discharge document. Each medication is checked against the reference you select.</p>
        {refs.isLoading && <div className="skeleton" style={{ height: 40 }} />}
        {refs.error && (
          <div className="banner banner-error" role="alert">
            Could not load reference documents. {refs.error.message}{" "}
            <button className="link" onClick={() => refs.refetch()}>
              Retry
            </button>
          </div>
        )}
        {noReferences && (
          <div className="banner banner-warn" role="status">
            No reference documents are loaded. Add a PDF or DOCX to the references folder and run the seed command (see the README).
          </div>
        )}
        {refs.data && refs.data.length > 0 && (
          <>
            <label>
              Reference document
              <select value={referenceId} onChange={(e) => setReferenceId(e.target.value)}>
                {refs.data.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.title} ({r.page_count} pages)
                  </option>
                ))}
              </select>
            </label>
            <Dropzone file={file} onFile={setFile} disabled={upload.isPending} />
            {upload.error && (
              <div className="field-error" role="alert">
                {upload.error.message}
              </div>
            )}
            {notice && <div className="banner banner-ok">{notice}</div>}
            <button className="btn btn-primary" disabled={!canSubmit} onClick={() => file && upload.mutate({ file, referenceId })}>
              {upload.isPending ? "Uploading" : "Upload and analyze"}
            </button>
          </>
        )}
      </section>

      <section className="card">
        <h2>Your documents</h2>
        {jobs.isLoading && (
          <div>
            <div className="skeleton" />
            <div className="skeleton" />
          </div>
        )}
        {jobs.error && (
          <div className="banner banner-error" role="alert">
            Could not load your documents. {jobs.error.message}{" "}
            <button className="link" onClick={() => jobs.refetch()}>
              Retry
            </button>
          </div>
        )}
        {jobs.data && jobs.data.length === 0 && <p className="muted">Nothing here yet. Upload your first document above.</p>}
        {jobs.data && jobs.data.length > 0 && (
          <ul className="job-list">
            {jobs.data.map((j) => (
              <li key={j.id}>
                {j.optimistic ? (
                  <div className="job-row job-row-pending">
                    <span className="job-name">{j.document.filename}</span>
                    <span className="status status-queued">Uploading</span>
                  </div>
                ) : (
                  <Link className="job-row" to={`/jobs/${j.id}`}>
                    <span className="job-name">{j.document.filename}</span>
                    <span className="muted">{formatDateTime(j.created_at)}</span>
                    <span className={`status status-${j.status}`}>{j.status === "running" && j.current_stage ? `Running: ${j.current_stage.replace("_", " ")}` : j.status}</span>
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
