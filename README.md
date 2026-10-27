# Medication Doc Verifier

Upload a clinical document (PDF or DOCX) and check its prescriptions against an institutional reference document.

For each upload the system produces:

- a faithful **summary** of the document,
- a ranked list of **critical points** the patient must not miss, each grounded in a quote from the document,
- structured **medication records** (name, dose, unit, route, frequency, timing, duration, indication, page),
- a **verification** of every medication against the reference: `Supported`, `Contradicted` or `Unsupported`, with clickable citations to the exact reference passage (page, section, monograph, field).

Processing is asynchronous. The upload returns immediately with a job id and the UI shows each pipeline stage live.

## Quick start

Requirements: Docker with Compose v2, and an Azure OpenAI resource with two deployments: a chat model (default deployment name `gpt-5.6-terra`, must support structured outputs) and `text-embedding-3-small`.

```bash
git clone https://github.com/Thilini-Deshika/medication-doc-verifier.git
cd medication-doc-verifier

# 1. Put at least one reference document (PDF or DOCX) in data/references/
cp /path/to/your-reference.docx data/references/

# 2. Configure
cp .env.example .env
#    edit .env: set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and, if your
#    deployment names differ, the two *_DEPLOYMENT values

# 3. Start everything
docker compose up --build
```

The first build downloads LibreOffice, so allow a few minutes. When the logs settle, open <http://localhost:8080>.

| | |
|---|---|
| URL | http://localhost:8080 |
| Demo user | `demo@example.com` |
| Demo password | `demo-password-1` |

These demo credentials are created by the seed job for local use. Change `DEMO_EMAIL` and `DEMO_PASSWORD` in `.env`, or do not run the seed, for any shared deployment.

Then: sign in, pick the reference, drop a PDF or DOCX on the upload area, and watch the pipeline run. When it finishes, the document appears on the left and the analysis on the right.

Useful commands:

```bash
docker compose logs -f worker            # follow pipeline logs (JSON, one line per event)
docker compose run --rm seed             # re-run the seed after adding another reference file
docker compose up --scale worker=3       # more workers, more jobs in parallel
docker compose down -v                   # stop and delete all data
```

The backend is also published on port 8000, where the OpenAPI docs are served at <http://localhost:8000/docs>.

## Adding reference documents

The reference is data, not code. Any PDF or DOCX can be a reference.

1. Copy the file into `data/references/`. The file name (without extension) becomes the title shown in the UI.
2. Run `docker compose run --rm seed`. It is idempotent: files are identified by content hash, and an already ingested file is skipped.
3. It now appears in the reference dropdown.

For one file with a custom title: `docker compose run --rm -v /path/to/file.docx:/tmp/ref.docx seed python -m app.cli add-reference /tmp/ref.docx --title "Formulary 2026"`.

Reference documents are shared by all users and read-only. Uploaded documents are private to their owner.

## Running without Docker

Requirements: Python 3.12+, Node 20+, PostgreSQL 16 with the `pgvector` extension, LibreOffice (`soffice` on the PATH, only needed for DOCX).

```bash
# database
createdb medication_doc_verifier   # and a role matching DATABASE_URL

# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://medication_doc_verifier:medication_doc_verifier@localhost:5432/medication_doc_verifier
export AZURE_OPENAI_ENDPOINT=https://<resource>.services.ai.azure.com
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-5.6-terra
export AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
alembic upgrade head
python -m app.cli seed --references ../data/references
uvicorn app.main:app --port 8000 &
python -m app.pipeline.worker &

# frontend (proxies /api to localhost:8000)
cd ../frontend
npm install
npm run dev          # http://localhost:5173
```

## Deploying

> Status: these files are validated by `docker compose config` and CI, and the same images run locally with `docker compose up`. The production override itself has not been run on a live server.

`deploy/` holds a production override that adds automatic HTTPS and removes development defaults:

```bash
cp .env.example .env.prod
# set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, JWT_SECRET (long random), POSTGRES_PASSWORD, DOMAIN
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --env-file .env.prod up -d --build
```

- Point a DNS A record for `DOMAIN` at the server first; Caddy requests and renews the certificate itself. Ports 80 and 443 must be reachable.
- Only Caddy publishes ports. Cookies are marked `Secure`, the demo user is not created, and weak default secrets are rejected at startup.
- `deploy/backup.sh <dir>` writes a compressed `pg_dump`; run it from cron.
- Using a managed PostgreSQL instead of the bundled container: it needs the `vector` extension (`CREATE EXTENSION vector` must be permitted); set `DATABASE_URL` accordingly and remove the `db` service dependency.
- The web container proxies `/api` to `BACKEND_URL` (default `http://backend:8000`), so it can sit in front of a backend on another host.
- `.github/workflows/ci.yml` runs the backend tests (against a pgvector service), the frontend build and a compose validation on every push and pull request.

## Tests

```bash
cd backend
createdb medication_doc_verifier_test
export TEST_DATABASE_URL=postgresql+psycopg://medication_doc_verifier:medication_doc_verifier@localhost:5432/medication_doc_verifier_test
pytest
```

The tests use a deterministic offline embedder and a scripted LLM, so they need no API key and no network. They cover:

- **Verification logic**: a wrong time of day is flagged `Contradicted` with the right citation; matching parameters give `Supported`; a drug with no monograph is `Unsupported` and never reaches the LLM; a look-alike drug name is not resolved to another monograph; a fabricated quote or a citation to a passage that was not retrieved is rejected and cannot produce a verdict.
- **Pipeline**: full run on synthetic documents; stage failure is recorded, later stages are skipped, the job is not left hanging; retry resumes at the failed stage; ungrounded critical points are dropped.
- **Authorization**: every job endpoint (including the event stream and the PDF) returns 404 for another user's job and 401 when anonymous.
- **Azure client**: request shape against a mocked endpoint (deployment URL, strict JSON schema, no sampling parameters, embedding dimensions), the repair attempt, and clear errors for truncation, content-filter blocks, refusals and HTTP failures.
- **Uploads**: all-or-nothing writes, duplicate detection inside and outside the window, file validation.

## Architecture

```mermaid
flowchart LR
  B[Browser<br/>React + react-pdf] -->|HTTPS, cookie auth| N[nginx]
  N -->|/api| A[FastAPI]
  A -->|insert job, stages, file<br/>one transaction| P[(PostgreSQL + pgvector)]
  A -. LISTEN job_events .-> P
  W[Worker x N] -->|claim: FOR UPDATE SKIP LOCKED| P
  W -->|stage results, NOTIFY| P
  W --> L[Azure OpenAI chat deployment]
  W --> E[Azure OpenAI embedding deployment]
  A -->|Server-sent events| B
```

- **API** (`backend/app/api`): auth, uploads, job state, results, PDF streaming, server-sent events. It never runs the pipeline.
- **Worker** (`backend/app/pipeline`): claims queued jobs and runs the stages. Run as many workers as you need. A heartbeat keeps the job lease alive; a job whose worker dies is re-queued, or failed after `JOB_MAX_ATTEMPTS`.
- **PostgreSQL** is the only stateful dependency. It holds the data, the vector index, the job queue and the notification channels. There is no Redis, no broker, no object store to operate.
- **Frontend** (`frontend/src`): React, React Query, react-pdf. Served by nginx, which proxies `/api` so the browser sees one origin.

### Pipeline

| # | Stage | What happens |
|---|---|---|
| 1 | `parsed` | DOCX is converted to PDF; text is extracted page by page with tables kept as tables |
| 2 | `chunked` | Structure-aware chunks (see below). Also checks that the selected reference is loaded and reports its size |
| 3 | `embedded` | Chunks are embedded and stored. Also checks that the reference was embedded with the current model |
| 4 | `summarized` | LLM summary (map-reduce for very long documents) |
| 5 | `key_points` | LLM extracts critical points; each must quote the document, quotes are located and verified |
| 6 | `entities` | LLM extracts structured medication records; page numbers are recomputed from the text |
| 7 | `verified` | Retrieval and verification per medication |
| 8 | `done` | Job marked complete |

Each stage commits its own transaction, writes its status, start and end time, duration and a small detail payload (counts) to `job_stages`, and emits a notification. A failure at any stage is caught, stored on the stage and on the job (a safe message for known failures, an error reference for unexpected ones, with the stack trace in the logs), the remaining stages are marked `skipped`, and the job becomes `failed`. `POST /api/jobs/{id}/retry` resumes from the failed stage; stages are idempotent because each deletes its own earlier output first.

### Real-time updates

The browser opens `GET /api/jobs/{id}/events` (server-sent events). The worker issues `pg_notify` inside the same transaction that changes a stage, so the notification is delivered only if the change committed. One listener thread in the API fans notifications out to open streams, and each stream re-reads the job snapshot. Notifications are only a wake-up signal: a stream also refreshes every 10 seconds, so a lost notification cannot leave the UI stale. The page loads its first state with a normal request, so refreshing in the middle of processing shows the right stage immediately, then the stream resumes. There is no client-side polling.

## Key design decisions

**Everything is normalised to PDF.** A discharge summary in DOCX has no stored page numbers, but citations need pages and the UI needs one renderer. DOCX uploads are converted with LibreOffice in the worker; the PDF is what is parsed, rendered and cited. Trade-off: LibreOffice makes the image larger and DOCX pagination follows LibreOffice's layout, not Word's.

**Files live in Postgres.** The uploaded bytes are stored in `document_blobs` (`bytea`, 15 MB limit). That lets the file and every row created for the upload commit or roll back together, and the API and worker share nothing but the database. For large volumes, move blobs to object storage and keep the same transaction shape (see Known limitations).

**Chunking follows the document structure.** The reference is organised into sections, monographs and labelled fields. Extraction keeps bold formatting; a bold heading followed by labelled fields is a monograph, and each labelled field ("Standard adult dose:", "Contraindications:", ...) is one chunk. Reasons: a prescribing parameter lives in a field, so one field is the smallest self-contained unit of evidence; a citation can then name page, section, monograph and field exactly; and fixed-size windows would split a dose from its timing or merge two drugs. Each chunk is embedded with its path prefixed (`Section > Monograph > Field`), so a short chunk like "Maximum dose: 40 mg" still carries the drug name. Paragraphs, bullets and tables outside monographs are chunked separately, and oversized paragraphs are split on sentence boundaries with overlap.

**Embeddings come from Azure OpenAI.** `text-embedding-3-small` at its native 1536 dimensions: strong retrieval quality for its price, the same vendor and data agreement as the chat model, and nothing to download or host. Only reference and document text is sent, never user credentials. Changing the model or dimension means editing `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`/`EMBEDDING_DIM`, adding a migration for the vector column and re-running ingestion; `chunks.embedding_model` records which deployment produced each vector. The HNSW index supports up to 2000 dimensions, so `text-embedding-3-large` needs `dimensions=1536` (or lower).

**Retrieval is monograph-aware, with three channels.** For each medication:

1. *Name resolution*: the drug name is normalised (case, punctuation, dose and form words stripped) and matched to a monograph name. Matching is exact on the normalised name. Near matches are never accepted, because look-alike drug names are a classic medication-error source; they are reported as suggestions instead.
2. *Scoped retrieval*: all fields of the matched monograph, ranked by similarity to the prescription (with dose, prescribing and indication fields always included).
3. *Cross-references*: chunks elsewhere that name the drug (for example a quick-reference table of constraints), found lexically, plus a global semantic top-k above a relevance floor (`MIN_RELEVANCE`).

The HNSW index covers reference chunks only (partial index), so searching the reference is not diluted by uploaded documents.

**Verification model.** Three states:

- `Supported`: a monograph exists and at least one compared parameter is validated as consistent, with none in conflict.
- `Contradicted`: a monograph exists and at least one parameter (dose, frequency, route, timing, duration, indication) conflicts with what it states. Severity is `high` (safety limit or constraint violated), `moderate` (departs from the standard parameter) or `low`.
- `Unsupported`: no monograph for the drug. This is decided deterministically, before any LLM call, and the explanation says so explicitly instead of citing anything. The same state is used, conservatively, when a monograph exists but no parameter could be compared with a validated citation, so an unverified drug is never reported as supported.

The LLM compares parameters one by one and returns, for each, `match`, `mismatch` or `not_addressed`, with the passage id and a verbatim quote. The final status is computed from those validated results, not taken from the model. Policy choices, written into the prompt: the reference is the sole source of truth even where it differs from general medical knowledge; a dose that differs from the standard dose is a mismatch unless a passage permits it; an open-ended duration is a mismatch only when the reference limits duration; silence in the reference is `not_addressed`, never a mismatch.

**Hallucination control.**

- Every `match` or `mismatch` must cite a retrieved passage and quote it. The quote is checked against the passage text (normalised, fuzzy threshold `QUOTE_MATCH_THRESHOLD`). A claim whose citation does not check out is downgraded to `not_addressed`, logged, and cannot produce a verdict.
- Critical points must quote the document; quotes that cannot be located in it are dropped, and the number dropped is shown on the pipeline stage.
- Entity page numbers are recomputed from the text, not trusted from the model.
- Structured outputs are constrained by JSON schema and validated with Pydantic, with one repair attempt.

**What "critical" means.** A critical point is something the document tells the patient to do, avoid, report or attend, where missing it could harm the patient or defeat the treatment (timed or conditional medication use, duration limits, symptoms to report immediately, dated follow-up, ordered tests, restrictions). The model scores priority from 5 (safety-critical or time-bound) to 1 (useful reminder), points are ranked by priority then position in the document, and at most 12 are kept. The prompt forbids adding or correcting advice, and the quote requirement enforces it mechanically. A critical point that concerns a medication flagged as contradicted shows a "Conflicts with reference" tag, because a patient following the document would act on the flagged instruction.

**Auth.** Email and password (bcrypt over a SHA-256 pre-hash, so long passwords work), JWT in an `HttpOnly`, `SameSite=Lax` cookie. A cookie rather than a bearer token because `EventSource` cannot send headers. Authorization is enforced at the query level: jobs are only ever loaded with `owner_id = current user`, and anything else is a 404, so existence is not leaked.

**Transactions.** The upload endpoint writes the `documents` row, the `document_blobs` row (file bytes), the `jobs` row, the eight `job_stages` rows and the queue notification in one transaction. If any step fails, all of it rolls back and no orphan file, document or half-created job can exist. An advisory lock keyed on user, file hash and reference serialises concurrent identical uploads. Worker stage writes are separate transactions by design: each stage's output and its status change commit together, so the state shown always matches the data stored.

**Duplicate uploads.** If the same user uploads byte-identical content (SHA-256) for the same reference within `DUPLICATE_WINDOW_SECONDS` (default 300), the existing job is returned with `duplicate: true` and HTTP 200 instead of 202. Failed jobs are not treated as duplicates, so a retry upload works.

**Optimistic UI.** On upload, the job appears in the list immediately with an "Uploading" state and is rolled back, with the error shown, if the request fails. The jobs list and job detail share one React Query cache that the event stream updates in place.

**Logging.** One JSON line per event with `job_id`, `document_id`, `stage` and worker bound automatically. `grep job_id=<id>` shows a document end to end: stage start and end with durations, LLM calls (latency, token counts, stop reason), the retrieval record per medication (resolution, passage ids, channel, similarity), the verification verdict with per-parameter results, and any rejected citation. Document text is not logged. The same retrieval record is stored on each verification (`verifications.retrieval`).

## Retrieval evaluation

Run on any ingested reference:

```bash
docker compose run --rm backend python -m app.cli eval-retrieval
docker compose run --rm backend python -m app.cli eval-retrieval --gold /path/to/gold.json --k 5
```

1. **Monograph routing.** Each monograph name written several realistic ways (case, hyphen, dose and form words) must resolve to its own monograph; invented and one-letter look-alike names must resolve to nothing. Both numbers should be perfect; any look-alike that resolves is a safety bug.
2. **Field retrieval.** For every labelled field of every monograph a query is generated from the drug and field label, and the metric is recall@k and MRR of that exact chunk in global semantic search. This needs no hand labelling and is the regression check when the embedding model, chunking or prefixing changes.
3. **Gold queries (optional).** A JSON list of `{"drug": "...", "query": "...", "expected": ["Standard adult dose", ...]}`. Reports hit@k over the retrieval actually used by verification (scoped plus cross-references plus global).

End-to-end correctness is covered separately by the verification tests above: known-contradicted and known-unsupported cases must be classified correctly.

## Database schema

Migrations are versioned with Alembic (`backend/migrations/versions`) and run automatically when the backend container starts.

```mermaid
erDiagram
  users ||--o{ documents : owns
  users ||--o{ jobs : owns
  documents ||--|| document_blobs : "file bytes"
  documents ||--o{ document_pages : "page text"
  documents ||--o{ chunks : "chunked into"
  documents ||--o{ jobs : "analysed by (document)"
  documents ||--o{ jobs : "verified against (reference)"
  jobs ||--o{ job_stages : has
  jobs ||--o| summaries : has
  jobs ||--o{ critical_points : has
  jobs ||--o{ medication_entities : has
  medication_entities ||--o| verifications : has
  verifications ||--o{ verification_citations : cites
  chunks ||--o{ verification_citations : "cited passage"
```

| Table | Purpose |
|---|---|
| `users` | Accounts. Unique index on `lower(email)`. |
| `documents` | `kind` is `primary` (owned by a user) or `reference` (shared, `owner_id` null). A check constraint ties `owner_id` to `kind`. References are unique by content hash. |
| `document_blobs` | Original bytes and the normalised PDF, kept apart from `documents` so list queries stay light. |
| `document_pages` | Extracted text per page, used by the LLM stages and quote location. |
| `chunks` | Retrieval units: page range, section, monograph, field label, text, `vector(1536)` embedding, `embedding_model`. Partial HNSW (cosine) index on reference chunks. |
| `jobs` | One analysis of one document against one reference. Status, current stage, error, attempts, worker lease. Also the work queue. |
| `job_stages` | One row per job and stage: status, start, finish, `duration_ms`, error, JSON detail. |
| `summaries`, `critical_points`, `medication_entities` | Stage outputs, keyed by job. |
| `verifications` | One per medication: status, severity, explanation, per-parameter checks (JSONB), retrieval record (JSONB). |
| `verification_citations` | Cited chunk, role (`supporting` or `contradicting`), parameter, verbatim quote, and a snapshot of page, section path and passage text, so a citation stays readable if the reference is re-ingested. |

Primary keys are UUIDs generated by the application.

## Configuration

All settings are environment variables (see `backend/app/config.py`). The ones you are likely to change:

| Variable | Default | Meaning |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` | required | Azure OpenAI resource host and key. The app uses the resource's v1 API (`<endpoint>/openai/v1/`), so no API version is needed |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-5.6-terra` | Deployment name used for every LLM stage (needs structured outputs) |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-small` | Deployment name used for embeddings |
| `LLM_REASONING_EFFORT` | empty (deployment default) | `minimal`, `low`, `medium` or `high`, sent only if set |
| `DUPLICATE_WINDOW_SECONDS` | `300` | Duplicate upload window |
| `MAX_UPLOAD_MB` | `15` | Upload size limit |
| `MIN_RELEVANCE` | `0.35` | Similarity floor for global retrieval hits |
| `FUZZY_NAME_THRESHOLD` | `95` | Similarity needed to accept a non-exact drug name (kept very strict on purpose) |
| `QUOTE_MATCH_THRESHOLD` | `88` | How closely a quote must match its source to be accepted |
| `JOB_LEASE_SECONDS`, `JOB_MAX_ATTEMPTS` | `120`, `3` | Worker crash recovery |
| `JWT_SECRET`, `COOKIE_SECURE` | dev value, `false` | Set a long random secret and `COOKIE_SECURE=true` behind HTTPS |
| `SEED_DEMO_USER` | `true` | Set `false` to skip creating the demo user |
| `LLM_FACTORY` | unset | `module:function` returning an object with `generate(...)`, to replace the chat client (used by tests) |

## Extra credit

Both optional features are implemented.

- **Deep-link citations.** Each flagged issue lists its citations as chips showing page and section path (for example `p.6 III. Sedatives > Examplamide > Prescribing notes`). A chip opens a preview of the cited passage with the quoted text highlighted, and "View in reference document" opens the rendered reference PDF at the cited page with the quote highlighted. The primary document gets the same highlighting from critical points and flags. Highlighting matches the quote against the PDF text layer.
- **Duplicate upload detection.** An upload whose content hash (SHA-256) matches one the same user made for the same reference within `DUPLICATE_WINDOW_SECONDS` (default 300) returns the existing job with `duplicate: true` and HTTP 200. The window is configuration, concurrent identical uploads are serialised with an advisory lock so only one job is created, and failed jobs are not treated as duplicates.

## Known limitations and what I would do with more time

- **Scanned documents are not supported** (no OCR). Image-only PDFs fail the first stage with a clear message.
- **Name resolution is exact on purpose.** A brand/generic alias or a misspelling is reported as `Unsupported` with the closest monograph names as suggestions, rather than guessed. An alias table, or an LLM step that proposes an alias for human confirmation, would reduce false `Unsupported` results.
- **Per-medication checks only.** Drug-drug interactions and contraindications arising from other diagnoses in the document are not evaluated, although the retrieved passages contain them.
- **Reference management is by file drop and CLI.** There is no admin UI, and a changed reference needs a new file (it is a different content hash and appears as a separate reference).
- **Blobs in Postgres** is right for tens of megabytes per document and simple operations. At scale, store files in object storage and write the object key in the same upload transaction, with a sweeper for orphans.
- **HNSW with filters.** With many large references, a post-filtered ANN query can return fewer rows than requested. Partition by reference or raise `hnsw.ef_search`.
- **One job per worker process.** Scale with more workers. Verification calls within a job already run concurrently (`VERIFY_CONCURRENCY`).
- **Azure content filter.** Medical text can trip the default content filter of a deployment; a blocked request fails the stage with a message that says so. Use a filter configuration appropriate for clinical content.
- **LLM output is not deterministic.** The validation layers bound the damage but cannot make a model call reproducible. Consider storing prompt versions and sampling a human review set in production.
- **No rate limiting or account lockout** on the auth endpoints, and no password reset or email verification. Add them at the proxy or in the app before exposing the service publicly.
- **Clinical use.** This is a verification aid, not a clinical decision system. Every flag and every absence of a flag should be reviewed by a qualified person. Documents contain personal health information: add encryption at rest, audit logging, retention policy and a data processing agreement with the LLM provider before real use.

## Project layout

```
backend/
  app/
    api/            routes: auth, jobs (upload, state, SSE, results), references
    ingest/         pdf conversion and extraction, chunking, embeddings, parse/chunk/embed service
    llm/            LLM interface, Azure OpenAI client, prompts, output schemas
    verification/   name resolution, retrieval, verifier
    pipeline/       stages, runner, worker, notifications
    services/       upload transaction
    eval/           retrieval evaluation
    cli.py          seed, add-reference, eval-retrieval
  migrations/       Alembic revisions
  tests/
frontend/src/       pages, components (PDF viewer, pipeline view, results), SSE hook
data/references/    drop reference documents here
deploy/             production override, Caddyfile, backup script
.github/workflows/  CI
docker-compose.yml
```
