export function formatTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export const STAGE_LABELS: Record<string, string> = {
  parsed: "Parsed",
  chunked: "Chunked",
  embedded: "Embedded",
  summarized: "Summarized",
  key_points: "Key points extracted",
  entities: "Entities extracted",
  verified: "Verified",
  done: "Done",
};

export const CATEGORY_LABELS: Record<string, string> = {
  medication_timing: "Medication timing",
  duration_limit: "Duration limit",
  report_immediately: "Report immediately",
  follow_up: "Follow-up",
  monitoring_or_test: "Monitoring or test",
  avoid_or_restriction: "Avoid or restriction",
  other_warning: "Warning",
};

export function stageSummary(name: string, detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  const d = detail as Record<string, number | string>;
  switch (name) {
    case "parsed":
      return `${d.pages} page${d.pages === 1 ? "" : "s"}${d.converted_to_pdf ? ", converted from DOCX" : ""}`;
    case "chunked": {
      const ref = detail.reference as { chunks: number; monographs: number } | undefined;
      return `${d.chunks} chunks${ref ? `. Reference ready: ${ref.chunks} chunks, ${ref.monographs} monographs` : ""}`;
    }
    case "embedded": {
      const ref = detail.reference as { model: string } | undefined;
      return `${d.embedded} chunks embedded${ref ? `. Reference embedded with ${ref.model}` : ""}`;
    }
    case "summarized":
      return `${d.words} word summary`;
    case "key_points": {
      const rejected = Number(d.rejected_ungrounded ?? 0);
      return `${d.points} points${rejected ? `, ${rejected} dropped (not found in document)` : ""}`;
    }
    case "entities":
      return `${d.medications} medications`;
    case "verified":
      return `${d.supported} supported, ${d.contradicted} contradicted, ${d.unsupported} unsupported`;
    default:
      return "";
  }
}
