import { useEffect } from "react";
import { Citation, referenceUrl } from "../api";
import { PdfViewer } from "./PdfViewer";

export function ReferenceModal({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Reference document" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <strong>Reference document</strong>
            <div className="muted">
              Page {citation.page_start}
              {citation.page_end !== citation.page_start ? `-${citation.page_end}` : ""}, {citation.section_path}
            </div>
          </div>
          <button className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <PdfViewer
          url={referenceUrl(citation.reference_id)}
          highlight={{ page: citation.page_start, text: citation.quote, nonce: 1 }}
          emptyLabel="Reference document unavailable"
        />
      </div>
    </div>
  );
}
