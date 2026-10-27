import { useEffect, useState } from "react";
import { Job } from "../api";
import { formatDuration, formatTime, STAGE_LABELS, stageSummary } from "../format";

function elapsed(startedAt: string | null, now: number): string {
  if (!startedAt) return "";
  return formatDuration(Math.max(0, now - new Date(startedAt).getTime()));
}

export function PipelineView({ job, connected, onRetry, retrying }: { job: Job; connected: boolean; onRetry?: () => void; retrying?: boolean }) {
  const [now, setNow] = useState(Date.now());
  const running = job.stages.some((s) => s.status === "running");
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [running]);

  return (
    <section className="card pipeline" aria-label="Processing pipeline">
      <div className="card-head">
        <h2>Processing pipeline</h2>
        {job.status === "running" || job.status === "queued" ? (
          <span className={`pill ${connected ? "pill-live" : "pill-wait"}`}>{connected ? "Live" : "Connecting"}</span>
        ) : null}
      </div>
      {job.status === "failed" && (
        <div className="banner banner-error" role="alert">
          <div>
            <strong>Processing stopped.</strong> {job.error}
          </div>
          {onRetry && (
            <button className="btn" onClick={onRetry} disabled={retrying}>
              {retrying ? "Retrying" : "Retry from failed stage"}
            </button>
          )}
        </div>
      )}
      <ol className="stages">
        {job.stages.map((s) => (
          <li key={s.name} className={`stage stage-${s.status}`}>
            <span className="stage-dot" aria-hidden="true">
              {s.status === "failed" ? "!" : s.status === "skipped" ? "-" : ""}
            </span>
            <div className="stage-body">
              <div className="stage-title">
                <span>{STAGE_LABELS[s.name] ?? s.name}</span>
                <span className="stage-status">{s.status}</span>
              </div>
              <div className="stage-meta">
                {s.started_at && <span>Started {formatTime(s.started_at)}</span>}
                {s.finished_at && <span>Finished {formatTime(s.finished_at)}</span>}
                {s.status === "running" && <span>Running for {elapsed(s.started_at, now)}</span>}
                {s.duration_ms !== null && s.status !== "running" && <span>Took {formatDuration(s.duration_ms)}</span>}
              </div>
              {s.status === "succeeded" && stageSummary(s.name, s.detail) && (
                <div className="stage-detail">{stageSummary(s.name, s.detail)}</div>
              )}
              {s.error && (
                <div className="stage-error" role="alert">
                  {s.error}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
