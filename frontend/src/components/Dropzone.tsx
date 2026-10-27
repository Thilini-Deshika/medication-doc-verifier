import { useRef, useState } from "react";
import { MAX_UPLOAD_MB } from "../api";

const ALLOWED = [".pdf", ".docx"];

export function validateFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ALLOWED.some((ext) => name.endsWith(ext))) return "Only PDF and DOCX files are supported.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `File is larger than ${MAX_UPLOAD_MB} MB.`;
  return null;
}

interface Props {
  file: File | null;
  onFile: (file: File | null) => void;
  disabled?: boolean;
}

export function Dropzone({ file, onFile, disabled }: Props) {
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const accept = (f: File | undefined) => {
    if (!f) return;
    const problem = validateFile(f);
    setError(problem);
    onFile(problem ? null : f);
  };

  return (
    <div>
      <div
        className={`dropzone ${over ? "dropzone-over" : ""} ${disabled ? "dropzone-disabled" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!disabled) accept(e.dataTransfer.files[0]);
        }}
        onClick={() => !disabled && input.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !disabled) {
            e.preventDefault();
            input.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Choose a PDF or DOCX file to upload"
      >
        <input
          ref={input}
          type="file"
          hidden
          accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={(e) => {
            accept(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {file ? (
          <div>
            <div className="dz-file">{file.name}</div>
            <div className="muted">{(file.size / 1024).toFixed(0)} KB. Click or drop to replace.</div>
          </div>
        ) : (
          <div>
            <div className="dz-title">Drop a discharge document here</div>
            <div className="muted">or click to browse. PDF or DOCX, up to {MAX_UPLOAD_MB} MB.</div>
          </div>
        )}
      </div>
      {error && (
        <div className="field-error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
