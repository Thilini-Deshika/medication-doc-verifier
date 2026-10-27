import { useMemo, useState } from "react";
import { Citation, CriticalPoint, JobResult, Medication } from "../api";
import { CATEGORY_LABELS } from "../format";
import { ReferenceModal } from "./ReferenceModal";

interface Props {
  result: JobResult;
  referenceTitle: string;
  onFocusSource: (page: number | null, text: string) => void;
}

const STATUS_LABEL = { supported: "Supported", contradicted: "Contradicted", unsupported: "Unsupported" } as const;

function doseText(m: Medication): string {
  const dose = m.dose_value !== null ? `${m.dose_value} ${m.dose_unit ?? ""}`.trim() : null;
  return [dose, m.route, m.frequency, m.timing, m.duration].filter(Boolean).join(", ");
}

export function Results({ result, referenceTitle, onFocusSource }: Props) {
  const [preview, setPreview] = useState<Citation | null>(null);
  const [viewing, setViewing] = useState<Citation | null>(null);

  const flagged = useMemo(
    () => result.medications.filter((m) => m.verification && m.verification.status !== "supported"),
    [result],
  );
  const flaggedDrugs = useMemo(() => new Set(flagged.filter((m) => m.verification?.status === "contradicted").map((m) => m.drug_name.toLowerCase())), [flagged]);
  const ok = result.medications.filter((m) => m.verification?.status === "supported");

  return (
    <div className="results">
      <section className="card" aria-labelledby="h-summary">
        <h2 id="h-summary">Summary</h2>
        {result.summary ? (
          result.summary.split(/\n{2,}/).map((p, i) => <p key={i}>{p}</p>)
        ) : (
          <p className="muted">No summary was generated.</p>
        )}
      </section>

      <section className="card" aria-labelledby="h-flags">
        <div className="card-head">
          <h2 id="h-flags">Flagged issues</h2>
          <span className="muted">Checked against {referenceTitle}</span>
        </div>
        {result.medications.length === 0 ? (
          <p className="muted">No prescribed medications were found in this document, so there is nothing to verify.</p>
        ) : flagged.length === 0 ? (
          <div className="banner banner-ok">All {ok.length} medications are consistent with the reference.</div>
        ) : (
          <div className="flags">
            {flagged.map((m) => (
              <FlagCard key={m.id} med={m} onFocusSource={onFocusSource} onPreview={setPreview} />
            ))}
          </div>
        )}
        {ok.length > 0 && flagged.length > 0 && (
          <details className="ok-list">
            <summary>{ok.length} medications consistent with the reference</summary>
            <ul>
              {ok.map((m) => (
                <li key={m.id}>
                  <button className="link" onClick={() => onFocusSource(m.source_page, m.drug_name)}>
                    {m.drug_name}
                  </button>
                  <span className="muted"> {doseText(m)}</span>
                  {m.verification?.citations[0] && (
                    <button className="link cite" onClick={() => setPreview(m.verification!.citations[0])}>
                      p.{m.verification.citations[0].page_start}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section className="card" aria-labelledby="h-critical">
        <h2 id="h-critical">Critical points for the patient</h2>
        {result.critical_points.length === 0 ? (
          <p className="muted">No critical points were found in this document.</p>
        ) : (
          <ol className="points">
            {result.critical_points.map((p) => (
              <PointItem key={p.id} point={p} conflicts={!!p.related_drug && flaggedDrugs.has(p.related_drug.toLowerCase())} onFocusSource={onFocusSource} />
            ))}
          </ol>
        )}
      </section>

      {preview && (
        <CitationPreview
          citation={preview}
          referenceTitle={referenceTitle}
          onClose={() => setPreview(null)}
          onOpenReference={() => {
            setViewing(preview);
            setPreview(null);
          }}
        />
      )}
      {viewing && <ReferenceModal citation={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

function PointItem({ point, conflicts, onFocusSource }: { point: CriticalPoint; conflicts: boolean; onFocusSource: Props["onFocusSource"] }) {
  return (
    <li className={`point priority-${point.priority}`}>
      <div className="point-meta">
        <span className="tag">{CATEGORY_LABELS[point.category] ?? point.category}</span>
        <span className="muted">Priority {point.priority} of 5</span>
        {conflicts && <span className="tag tag-warn">Conflicts with reference</span>}
      </div>
      <p>{point.text}</p>
      <button className="link" onClick={() => onFocusSource(point.source_page, point.source_quote)}>
        Show in document{point.source_page ? `, page ${point.source_page}` : ""}
      </button>
    </li>
  );
}

function FlagCard({ med, onFocusSource, onPreview }: { med: Medication; onFocusSource: Props["onFocusSource"]; onPreview: (c: Citation) => void }) {
  const v = med.verification!;
  const mismatches = v.parameter_checks.filter((c) => c.result === "mismatch");
  return (
    <article className={`flag flag-${v.status}`}>
      <header>
        <div>
          <h3>{med.drug_name}</h3>
          <div className="muted">{doseText(med)}</div>
        </div>
        <div className="flag-badges">
          <span className={`badge badge-${v.status}`}>{STATUS_LABEL[v.status]}</span>
          {v.severity !== "none" && <span className={`tag sev-${v.severity}`}>{v.severity} severity</span>}
        </div>
      </header>
      <p>{v.explanation}</p>
      {mismatches.length > 0 && (
        <table className="compare">
          <thead>
            <tr>
              <th>Parameter</th>
              <th>Prescribed</th>
              <th>Reference states</th>
            </tr>
          </thead>
          <tbody>
            {mismatches.map((c, i) => (
              <tr key={i}>
                <td>{c.parameter}</td>
                <td>{c.prescribed ?? "not stated"}</td>
                <td>{c.reference ?? "not stated"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {v.citations.length > 0 ? (
        <div className="cites">
          {v.citations
            .filter((c) => c.role === "contradicting")
            .concat(v.citations.filter((c) => c.role !== "contradicting"))
            .map((c) => (
              <button key={c.id} className={`cite-chip cite-${c.role}`} onClick={() => onPreview(c)}>
                <span className="cite-page">p.{c.page_start}</span>
                <span>{c.section_path}</span>
                {c.parameter && <span className="cite-param">{c.parameter}</span>}
              </button>
            ))}
        </div>
      ) : (
        <div className="no-cite">No supporting passage exists in the reference for this medication.</div>
      )}
      <button className="link" onClick={() => onFocusSource(med.source_page, med.source_quote ?? med.drug_name)}>
        Show prescription in document{med.source_page ? `, page ${med.source_page}` : ""}
      </button>
    </article>
  );
}

function CitationPreview({
  citation,
  referenceTitle,
  onClose,
  onOpenReference,
}: {
  citation: Citation;
  referenceTitle: string;
  onClose: () => void;
  onOpenReference: () => void;
}) {
  const idx = citation.passage_text.toLowerCase().indexOf(citation.quote.toLowerCase());
  const before = idx >= 0 ? citation.passage_text.slice(0, idx) : citation.passage_text;
  const hit = idx >= 0 ? citation.passage_text.slice(idx, idx + citation.quote.length) : "";
  const after = idx >= 0 ? citation.passage_text.slice(idx + citation.quote.length) : "";
  return (
    <aside className="drawer" role="dialog" aria-label="Reference passage">
      <div className="drawer-head">
        <div>
          <strong>{referenceTitle}</strong>
          <div className="muted">
            Page {citation.page_start}
            {citation.page_end !== citation.page_start ? `-${citation.page_end}` : ""}, {citation.section_path}
          </div>
        </div>
        <button className="btn btn-ghost" onClick={onClose} aria-label="Close passage preview">
          Close
        </button>
      </div>
      <span className={`tag ${citation.role === "contradicting" ? "tag-warn" : "tag-ok"}`}>
        {citation.role === "contradicting" ? "Contradicts the prescription" : "Supports the prescription"}
        {citation.parameter ? `, ${citation.parameter}` : ""}
      </span>
      <blockquote className="passage">
        {before}
        {hit && <mark className="hl">{hit}</mark>}
        {after}
      </blockquote>
      <button className="btn" onClick={onOpenReference}>
        View in reference document
      </button>
    </aside>
  );
}
