import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

const normalize = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
/** True when a text-layer item belongs to the quoted text (whole or partial overlap of 4 consecutive words). */
function overlaps(item: string, quote: string): boolean {
  if (item.length < 3 || !quote) return false;
  if (quote.includes(item) || item.includes(quote)) return true;
  const words = item.split(" ");
  for (let i = 0; i + 4 <= words.length; i++) {
    if (quote.includes(words.slice(i, i + 4).join(" "))) return true;
  }
  return false;
}
const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export interface Highlight {
  /** Page to scroll to. */
  page: number;
  /** Text to mark. Text-layer items that are part of this text are highlighted. */
  text: string;
  /** Changes on every request so the same target can be re-focused. */
  nonce: number;
}

interface Props {
  url: string;
  highlight: Highlight | null;
  emptyLabel?: string;
}

export function PdfViewer({ url, highlight, emptyLabel = "Document unavailable" }: Props) {
  const [numPages, setNumPages] = useState(0);
  const [width, setWidth] = useState(600);
  const [error, setError] = useState<string | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const target = useRef<Highlight | null>(null);
  target.current = highlight;
  const normalizedHighlight = highlight ? normalize(highlight.text) : "";

  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(Math.max(280, el.clientWidth - 24)));
    observer.observe(el);
    setWidth(Math.max(280, el.clientWidth - 24));
    return () => observer.disconnect();
  }, []);

  const scrollToHighlight = useCallback(() => {
    const el = container.current;
    const t = target.current;
    if (!el || !t) return;
    const mark = el.querySelector(`[data-page="${t.page}"] mark.hl`);
    const pageEl = el.querySelector(`[data-page="${t.page}"]`);
    (mark ?? pageEl)?.scrollIntoView({ behavior: "smooth", block: mark ? "center" : "start" });
  }, []);

  useEffect(() => {
    if (!highlight || !numPages) return;
    const id = window.setTimeout(scrollToHighlight, 150);
    return () => window.clearTimeout(id);
  }, [highlight, numPages, scrollToHighlight]);

  const renderText = useCallback(
    ({ str, pageNumber }: { str: string; pageNumber: number }) => {
      const safe = escapeHtml(str);
      const t = target.current;
      if (!t || t.page !== pageNumber || !normalizedHighlight) return safe;
      const n = normalize(str);
      return overlaps(n, normalizedHighlight) ? `<mark class="hl">${safe}</mark>` : safe;
    },
    [normalizedHighlight],
  );

  return (
    <div className="pdf-scroll" ref={container}>
      {error ? (
        <div className="state-box" role="alert">
          {emptyLabel}: {error}
        </div>
      ) : (
        <Document
          file={url}
          onLoadSuccess={(d) => setNumPages(d.numPages)}
          onLoadError={(e) => setError(e.message)}
          loading={<div className="state-box">Loading document</div>}
        >
          {Array.from({ length: numPages }, (_, i) => (
            <div className="pdf-page" data-page={i + 1} key={i}>
              <div className="pdf-page-label">Page {i + 1}</div>
              <Page
                pageNumber={i + 1}
                width={width}
                renderAnnotationLayer={false}
                customTextRenderer={(item) => renderText({ str: item.str, pageNumber: i + 1 })}
                onRenderTextLayerSuccess={() => {
                  if (target.current?.page === i + 1) scrollToHighlight();
                }}
              />
            </div>
          ))}
        </Document>
      )}
    </div>
  );
}
