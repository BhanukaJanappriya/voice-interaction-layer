# Implementation Plan: knowledge base, external agents, MCP, verified users, single app

Written before any feature code, from reading every file in the repository
and running the current system. It is the brief each workstream was given,
and it records every decision taken on the owner's behalf (see the ADRs at
the end).

## 1. The system today, in my own words

One FastAPI process (`backend/server.py`, started from inside `backend/`)
and a separate static file server for `frontend/` on port 5500.

- **Voice path** (`/ws/voice`): the browser streams 0.5 s float32 PCM chunks.
  After an optional spoken introduction, the server calibrates a
  `VoiceActivityDetector` (vad.py) on 1 s of room noise, then loops turns:
  `transcribe_stream()` (stt.py, faster-whisper on a worker thread) yields
  partial and final transcripts; the final one goes to
  `Orchestrator.respond_stream()` (agent.py) on another worker thread; each
  streamed chunk is fed to `SentenceChunker`, and whole sentences go to
  `speak_stream()` (tts.py), which synthesizes the next sentence while the
  current one plays on the server's speakers. `speaking_sentence` events
  drive the 3D avatar's mouth. Keyword matching ends the conversation on a
  satisfied phrase.
- **Text path** (`POST /chat`): the same Orchestrator, no STT/TTS, not
  streamed.
- **Orchestrator**: cloud first (Gemini or GPT; tool/retrieval step via the
  `tool_callback` hook, then a streamed composer; optional reasoning stage),
  with a 5 minute circuit breaker, falling back to `LlamaOllamaAgent` alone
  (Ollama at 127.0.0.1). `tool_callback` exists but nothing is plugged in.
  `last_mode` names the path that answered.
- **Frontend**: language, then Chat or Voice, then New/Existing user (the
  existing user just types a name, stored in localStorage), then the screen.
  No framework; CSS tokens on `:root` with a dark theme override.
- **Conventions**: every module runs standalone (`python agent.py`) and
  prints its own latency; `SAMPLE_RATE` lives in vad.py, `CHUNK_SECONDS` in
  stt.py; only config.py reads the environment; cloud failures raise
  `CloudUnavailableError` and fall back. Modules import each other by bare
  name (`import config`), because they are run from inside `backend/`.

### Baseline measured before any change (2026-09-30, this machine)

| Check | Result |
| --- | --- |
| `GET /health` | ok; Gemini configured but failing (404 retired model), so local Llama answers |
| `POST /chat` steady state | 0.65 to 0.87 s |
| Voice: first audio after final transcript (3 turns, local Llama, Google TTS) | 0.83 s, 0.62 s, 0.81 s |
| Answer quality on Mobitel questions | invents package names ("SLT 50GB, SLT 100GB", "SLT Mobile Monthly Plan") |
| bge-m3 query embedding via Ollama, warm, reused HTTP session | 27 to 90 ms |

The invented package names are the problem R1 exists to fix.

## 2. Cross cutting design

**Package layout.** New code lives in packages under `backend/`
(`knowledge/`, `db/`, `auth/`, `admin/`, `mcp_server/`) and imports the
existing modules and each other by bare name (`import config`,
`from db.models import User`, `from knowledge.retriever import get_retriever`),
exactly like the existing modules. `backend/__init__.py` puts `backend/` on
`sys.path`, so `python -m backend` and `python -m backend.knowledge.build`
work from the repository root. The MCP package is `mcp_server`, never `mcp`,
so it cannot shadow the SDK.

**Shared scaffolding already in place (written before spawning, do not
change signatures):**
- `backend/config.py`: every new setting (section 6).
- `backend/db/base.py`: `Base`, `engine`, `SessionLocal`,
  `session_scope()` (context manager, commits), `get_session()` (FastAPI
  dependency).
- `backend/db/models.py`: `User`, `Admin`, `ExternalAgent`, `McpClient`,
  `AuditLog` (section 7).
- `backend/auth/passwords.py`: `hash_secret(plain) -> str`,
  `verify_secret(plain, hashed) -> bool` (bcrypt, constant time for a
  missing account).
- `backend/auth/tokens.py`: `issue_token(subject, audience, ttl_minutes,
  scopes=None, extra=None) -> (token, expires_at)`,
  `decode_token(token, audience) -> claims` (raises `TokenError`),
  `token_scopes(claims) -> set`, audiences `AUDIENCE_USER`,
  `AUDIENCE_ADMIN`, `AUDIENCE_MCP`.

**One process.** `server.py` stays the app module. It includes the routers
each workstream delivers, mounts MCP at `/mcp`, serves `frontend/admin/` at
`/admin/` and `frontend/` at `/`, runs migrations and seeding at startup,
warms up the retriever with everything else, and reports all of it in
`/health`. Start command: `python -m backend` (new `backend/__main__.py`),
and `scripts/start.ps1` starts that one process.

**Frontend URL.** When the page is served by the backend itself, app.js uses
the page's own origin for `/ws/voice`, `/chat` and `/auth`; the existing
hidden `#backend-url` value is still used when the page is opened from the
old port 5500 static server, so that development flow keeps working.

## 3. Requirement designs

### R1. Mobitel knowledge base (RAG)

- `knowledge/scraper.py`: fetch `robots.txt` first and obey it
  (`urllib.robotparser`; today it returns 404, which means nothing is
  disallowed, and the scraper must still honor it if that changes). User
  Agent `config.SCRAPER_USER_AGENT`, at least `SCRAPER_DELAY_SECONDS`
  (>= 1 s) between requests, `www.sltmobitel.lk` only, skip
  login/cart/account/checkout/selfcare/payment/myslt URLs and binary files,
  `SCRAPER_MAX_PAGES` cap. Plain HTTP first (the site is Nuxt server side
  rendered, so HTML already holds the text); Playwright is an optional
  fallback used only if installed and a page's HTML has no body text.
  Raw pages cached under `data/knowledge/raw/` with a manifest
  (url, fetched_at, etag/last-modified, content hash) so re-runs are
  incremental (conditional requests, skip unchanged).
- Supplementary public sources: a committed, hand checked
  `backend/knowledge/sources/public_sources.jsonl`, one document per line
  with `source_url`, `title`, `text`, `retrieved_at`, taken only from
  reputable public pages (official SLT/Mobitel pages, TRCSL, Wikipedia,
  established news), no personal data.
- `knowledge/normalize.py`: HTML to clean text (drop nav/footer/scripts),
  keep headings, detect package structure (name, price in Rs/LKR, validity,
  data allowance, conditions) into metadata; every document and chunk keeps
  `source_url` and `retrieved_at`. Produces `data/knowledge/docs.jsonl` and
  the committed, curated `backend/knowledge/faq.jsonl` (question, answer,
  source_url, retrieved_at).
- `knowledge/index.py` + `knowledge/embeddings.py`: chunk about 300 to 500
  tokens with overlap (FAQ entries are their own chunks); embed with
  `config.EMBEDDING_MODEL` (bge-m3) through Ollama `/api/embed` with one
  reused `requests.Session`; store in `data/knowledge/index/`
  (`vectors.npy` float32 L2 normalized, `chunks.jsonl`, `manifest.json`
  with model, dimensions, built_at, counts, source list).
- `python -m backend.knowledge.build [--skip-scrape] [--max-pages N]`: scrape,
  normalize, index, in one command. Each module also runs standalone and
  prints its latency.
- `knowledge/retriever.py`: `get_retriever() -> KnowledgeRetriever`
  (process singleton) with:
  - `warm_up() -> None` (loads the index, one embedding call)
  - `search(query: str, top_k: int = config.RETRIEVAL_TOP_K) -> list[RetrievedChunk]`
    (`RetrievedChunk`: text, score, source_url, retrieved_at, title, metadata)
  - `context_for(text: str) -> Optional[str]`: the Orchestrator's
    `tool_callback`. None when the index is missing, Ollama is down, or no
    chunk scores above `RETRIEVAL_MIN_SCORE`. Otherwise a compact block of
    the top chunks, each tagged with its source URL and retrieval date.
    Never raises. Small LRU cache on the query so the cloud to local
    fallback does not embed the same question twice.
  - `get_package_details(name: str) -> list[dict]` (structured package
    metadata matches, for MCP)
  - `status() -> dict` (loaded, chunks, built_at, embedding_model,
    last_latency_ms) for `/health`.
  - Prints `Retrieval latency: N ms` per query. Target: under 150 ms.
- **Wiring (integration, lead):** `Orchestrator(tool_callback=retriever.context_for)`.
  Cloud path: the existing tool/retrieval step folds the context into the
  composer prompt. Local path: context goes into the Llama **system**
  prompt (it currently goes into the user prompt). This is context
  injection into one model, not multi agent collaboration, and the docs say
  so. Grounding rules added to both prompts: use the context for Mobitel
  facts, say prices change and point to www.sltmobitel.lk or the 1717
  hotline, never invent prices or package names, decline other brands'
  specifics politely.
- `knowledge/eval.py` + `backend/knowledge/eval_questions.jsonl`: 25+
  questions (packages, prices, FAQs, company, 3 Sinhala, 3 Tamil) with
  expected facts and expected source; reports retrieval hit rate
  (expected source or fact in top k) and answer quality (expected facts
  present in the local answer) to `docs/RAG_EVAL.md`.

### R2. External chat agents spoken by this assistant

- `ExternalHttpAgent(ChatAgent)` and `ExternalAgentError` appended to
  agent.py (a new, clearly marked section; nothing else in agent.py
  changes in this workstream):
  `__init__(self, name, url, method="POST", auth_header_name=None,
  auth_header_value=None, body_template='{"message": "{text}"}',
  response_path="reply", timeout_seconds=config.EXTERNAL_AGENT_DEFAULT_TIMEOUT,
  streaming=False)`, `respond(text_input, user_name=None, language=None) -> str`,
  `respond_stream(text_input, user_name=None, language=None) -> Iterator[str]`.
  Body template placeholders are JSON escaped. SSE when `streaming`.
  Raises `ExternalAgentError` (never a raw httpx/requests error) on
  network error, timeout, non 2xx, oversized response, bad JSON, missing
  path, SSRF refusal.
- `backend/url_safety.py`: `check_outbound_url(url, allow_private=config.EXTERNAL_AGENT_ALLOW_PRIVATE)`
  raises `UnsafeUrlError`: http/https only, no credentials in URL, resolve
  every A/AAAA record and refuse private, loopback, link local, multicast,
  reserved, unspecified, CGNAT (100.64/10) and IPv4 mapped equivalents;
  redirects are not followed. Response size capped at
  `EXTERNAL_AGENT_MAX_RESPONSE_BYTES` while reading.
- `admin/service.py`: CRUD for `ExternalAgent` (auth header value encrypted
  with Fernet `config.FIELD_ENCRYPTION_KEY`, never returned to the browser,
  only "set / not set"), `activate(id)` / `deactivate_all()` (at most one
  active), `test_agent(id, message) -> {ok, reply, latency_ms, error}`, and
  `get_active_agent() -> Optional[ExternalHttpAgent]` with an in process
  cache invalidated on every write.
- **Wiring (integration, lead):** new optional Orchestrator parameter
  `external_agent_provider: Optional[Callable[[], Optional[ChatAgent]]] = None`.
  When it returns an agent, `respond_stream()` tries it first; an
  `ExternalAgentError` before any text falls back to the normal cloud/local
  path; after some text, the same short apology as the cloud path.
  `last_mode = "external agent (<name>)"`. Everything downstream
  (SentenceChunker, speak_stream, events, avatar) is untouched.
- Admin portal (`frontend/admin/index.html`, `admin.js`, `admin.css`)
  served at `/admin/`: login, agents list/add/edit/delete, Test connection,
  activate/deactivate, MCP clients (create shows the secret once, revoke),
  recent MCP audit entries. Loads `/style.css` for tokens, buttons, cards,
  badges, theme toggle behavior; `admin.css` only adds table/form layout
  using the same variables.

### R3. MCP server with authentication

- Official SDK 2.x: `from mcp.server.mcpserver import MCPServer` (FastMCP
  was renamed), streamable HTTP, mounted in the FastAPI app at `/mcp`,
  session manager started from the app lifespan. Tools:
  `search_knowledge(query, top_k=4)` and `get_package_details(name)` need
  scope `knowledge:read`; `ask_assistant(question, language=None)` needs
  `assistant:ask` and runs the Orchestrator (through an injected callable,
  so mcp_server never imports server.py).
- Auth (ADR 3): this backend is its own authorization server.
  `POST /oauth/token`, `grant_type=client_credentials`, client id and
  secret by HTTP Basic or form body, optional `scope` (must be a subset of
  the client's scopes). Returns `{access_token, token_type: "Bearer",
  expires_in, scope}` (JWT, audience `AUDIENCE_MCP`, `MCP_TOKEN_MINUTES`).
  `/.well-known/oauth-protected-resource` (and `/mcp` suffixed form) and
  `/.well-known/oauth-authorization-server` metadata. Every `/mcp` request
  is validated (signature, expiry, audience, issuer, and the client still
  exists and is not revoked, so revocation is immediate); missing or
  invalid gives 401 with `WWW-Authenticate: Bearer ... resource_metadata=...`;
  a tool call without its scope gives an insufficient scope error (403 at
  the HTTP layer where the SDK allows, otherwise a tool error that states
  `insufficient_scope`), and is audited as `denied`.
- `mcp_server/clients.py`: `create_client(name, scopes) -> (McpClient,
  plain_secret)`, `list_clients()`, `revoke_client(id)`,
  `list_audit(limit=100)`, `record_audit(...)`. Used by the admin portal.
- Audit: one `AuditLog` row per tool call and per token request (client,
  tool/action, time, outcome, duration); no question or answer text.

### R4. Existing user verification

- `auth/phone.py`: `normalize_sl_landline(raw) -> str` (E.164, `+94...`) or
  `InvalidPhoneNumber`. Accepts `0XX XXXXXXX`, `0XXXXXXXXX`, `+94 XX
  XXXXXXX`, `0094...`, spaces and dashes; area code must be a real Sri
  Lankan landline code (011, 021, 023 to 027, 031 to 038, 041, 045, 047, 051,
  052, 054, 055, 057, 063, 065 to 067, 081, 091); mobile prefixes (07x) are
  refused.
- PIN (4 to 6 digits) set at registration, bcrypt hashed (ADR 4 explains
  why a number alone is not authentication and why not SMS OTP).
  `LOGIN_MAX_ATTEMPTS` failures lock the account for
  `LOGIN_LOCKOUT_MINUTES`; `LOGIN_RATE_LIMIT_PER_MINUTE` per client address
  across accounts. Same generic error for unknown number and wrong PIN.
- `auth/routes.py` router: `POST /auth/verify {phone, pin}` sets the
  httpOnly, SameSite=Lax cookie `user_session` and returns
  `{display_name, token, expires_at}` (the token is also returned for the
  cross origin development page, kept in memory only); `POST /auth/logout`;
  `GET /auth/me`. Admin: `POST /auth/admin/login` (cookie `admin_session`,
  SameSite=Strict), `POST /auth/admin/logout`, `GET /auth/admin/me`.
- `auth/dependencies.py`: `verified_user_from_token(token) ->
  Optional[VerifiedUser]` (VerifiedUser: id, display_name, phone_e164),
  `verified_user_from_request(request_or_websocket, explicit_token=None)`
  (Authorization Bearer, explicit token, then cookie), `require_admin`
  FastAPI dependency (401 otherwise).
- `auth/admins.py`: `seed_first_admin_from_env()`.
- Alembic: `alembic.ini` at the root, `backend/db/migrations/`; initial
  revision creates all five tables; `db/migrate.py: upgrade_to_head()`
  runs at startup. `python -m backend.db.seed` adds demo users;
  `python -m backend.auth.manage_users add ...` adds a real one.
- **Wiring (integration, lead):** `start_session` accepts optional
  `session_token`; server resolves the verified user (token, else cookie on
  the WebSocket handshake). A verified user's `user_name` always comes from
  the database; a client supplied name is only used when there is no
  verified session (the Guest path, unchanged). `POST /chat` does the same.
  Frontend: inside the existing "Existing user" panel, the name field is
  replaced by phone and PIN fields using the same `.name-prompt` markup and
  styles, plus an error line; New user is untouched.

### R5. One deployable

`server.py` includes: `/auth/*`, `/admin/api/*`, `/oauth/token`,
`/.well-known/*`, `/mcp`, `/ws/voice`, `/chat`, `/health`, static `/admin/`
and `/`. Startup order: power throttling opt out, migrations, admin seed,
then the background warm up (STT, agent, TTS, retriever). `/health` adds
`database`, `knowledge`, `external_agent`, `mcp`.

## 4. Workstreams and file ownership

Nobody edits a file outside their list. Changes needed in shared files
(config.py, server.py, agent.py core classes, requirements.txt,
.env.example, INTERFACES.md, README.md, ARCHITECTURE.md,
KNOWN_LIMITATIONS.md, frontend/index.html, frontend/app.js,
frontend/style.css) are returned to the lead as precise proposed patches.

| Agent | Owns |
| --- | --- |
| A Knowledge | `backend/knowledge/**`, `docs/RAG_EVAL.md` |
| B External agents + admin | `backend/url_safety.py`, `backend/admin/**`, `frontend/admin/**`, the new ExternalHttpAgent section appended to `backend/agent.py` |
| C Data + auth | `backend/db/**` (models.py/base.py: internals only, no signature or column renames), `backend/auth/**` (tokens.py/passwords.py: internals only), `alembic.ini` |
| D MCP | `backend/mcp_server/**` (including its OAuth routes and audit) |
| E QA | `tests/**`, `pytest.ini` |
| Lead | everything else, plus integration |

## 5. New endpoints

| Method and path | Owner | Auth |
| --- | --- | --- |
| POST /auth/verify, POST /auth/logout, GET /auth/me | C | none / user cookie |
| POST /auth/admin/login, POST /auth/admin/logout, GET /auth/admin/me | C | none / admin cookie |
| GET/POST /admin/api/agents, PUT/DELETE /admin/api/agents/{id} | B | admin |
| POST /admin/api/agents/{id}/test, /activate; POST /admin/api/agents/deactivate | B | admin |
| GET/POST /admin/api/mcp-clients, DELETE /admin/api/mcp-clients/{id}, GET /admin/api/audit | B (calls D's service) | admin |
| POST /oauth/token | D | client credentials |
| GET /.well-known/oauth-protected-resource[/mcp], /.well-known/oauth-authorization-server | D | none |
| /mcp (streamable HTTP) | D | Bearer JWT with scopes |
| GET /admin/ (static), GET / (static frontend) | lead | none |

## 6. New environment variables

`PUBLIC_BASE_URL`, `WARM_UP_ON_STARTUP`, `DATABASE_URL`, `DATA_DIR`,
`JWT_SECRET`, `FIELD_ENCRYPTION_KEY`, `COOKIE_SECURE`,
`USER_SESSION_MINUTES`, `ADMIN_SESSION_MINUTES`, `MCP_TOKEN_MINUTES`,
`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`,
`LOGIN_RATE_LIMIT_PER_MINUTE`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`,
`EXTERNAL_AGENT_ALLOW_PRIVATE`, `EXTERNAL_AGENT_MAX_RESPONSE_BYTES`,
`EXTERNAL_AGENT_DEFAULT_TIMEOUT`, `KNOWLEDGE_ENABLED`, `KNOWLEDGE_DIR`,
`EMBEDDING_MODEL`, `EMBEDDING_HOST`, `RETRIEVAL_TOP_K`,
`RETRIEVAL_MIN_SCORE`, `SCRAPER_USER_AGENT`, `SCRAPER_DELAY_SECONDS`,
`SCRAPER_MAX_PAGES`, `MCP_ENABLED`, `STT_HOTWORDS`, `STT_DENOISE`, `STT_BEAM_SIZE`, `STT_VAD_FILTER`, `STT_ACCURATE_MODEL`, `STT_CONFIDENCE_FLOOR`. All read in config.py only; documented
in `.env.example`.

## 7. Database schema (one database)

- `users`: id, phone_e164 (unique), display_name, pin_hash,
  preferred_language, failed_attempts, locked_until, created_at, last_login_at
- `admins`: id, username (unique), password_hash, failed_attempts,
  locked_until, created_at, last_login_at
- `external_agents`: id, name (unique), url, method, auth_header_name,
  auth_header_value_encrypted, body_template, response_path,
  timeout_seconds, streaming, is_active, created_at, updated_at
- `mcp_clients`: id, client_id (unique), name, secret_hash, scopes,
  created_at, last_used_at, revoked_at
- `audit_log`: id, created_at, actor_type, actor_id, action, outcome,
  detail, duration_ms, mcp_client_id

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Retrieval adds latency before the first spoken word | local embeddings, reused HTTP session, numpy dot product, LRU cache; measured per turn; retrieval skipped (None) on any failure |
| Ollama unloads bge-m3 or llama3.2 when both are loaded | both fit on the GPU today (2.6 GB + 0.7 GB); long keep_alive; measured |
| Website blocks the scraper (Imperva CDN) or changes | polite rate, clear UA, cached raw pages, curated public sources as a second leg |
| Scraped prices go stale | retrieved_at on every chunk, prompt says prices change and points to the official site; documented |
| Whisper mishears "Mobitel" ("Mobital", "Mobilo", seen in the baseline) | dense retrieval tolerates it better than keyword match; documented |
| MCP SDK 2.x API differs from 1.x docs | read the installed SDK source; pin mcp==2.2.0 |
| Cookie vs cross origin dev page | same origin when served by the backend; token in start_session / Authorization header otherwise |
| Parallel edits collide | strict ownership table; shared files only through the lead |

## 9. Test plan

- Unit: phone normalization (valid/invalid area codes, mobile refused,
  E.164 output), PIN verify, lockout and rate limit, token audiences and
  expiry, SSRF refusal (127.0.0.1, 10/8, 169.254.169.254, [::1], IPv4
  mapped, DNS name resolving to a private address, non http scheme),
  response size cap, body template escaping, response path extraction,
  SSE parsing, chunking, retriever threshold behavior.
- API: every new endpoint's success and failure paths; admin endpoints 401
  without a session; MCP 401 without/invalid/expired token with a
  WWW-Authenticate header, revoked client refused, insufficient scope
  refused, audit rows written; `/health` fields.
- Orchestrator: external agent used and `last_mode` set; external failure
  falls back; retrieval context reaches the cloud composer prompt and the
  local system prompt.
- End to end WebSocket (fake STT and TTS, real server code): Guest flow
  unchanged (greeting says Guest), verified user flow (greeting uses the
  database name even if the client sends another), retrieval grounded
  answer (context from the index reaches the model).
- Live (marked, need Ollama and the built index): RAG eval, voice turn
  latency with real Whisper and TTS, before/after numbers.

## 10. ADRs

**ADR 1: Database.** SQLAlchemy 2 with Alembic migrations, SQLite by
default, PostgreSQL through `DATABASE_URL`. SQLite needs no server for a
single machine demo and ships with Python; SQLAlchemy keeps the code the
same on PostgreSQL; Alembic gives versioned schema changes instead of
create_all(), which cannot change an existing table. Rejected: raw sqlite3
(no path to PostgreSQL), an ORM free JSON file (no concurrency, no
constraints).

**ADR 2: Embedding model.** bge-m3 served by the local Ollama. It is
multilingual (XLM-RoBERTa based, covers Sinhala and Tamil), measured here
to rank the right English passage first for Sinhala and Tamil questions,
takes 27 to 90 ms per query warm, and adds no Python ML dependency (no
torch), since Ollama is already required. Rejected:
paraphrase-multilingual-MiniLM (no Sinhala or Tamil in its training
languages), multilingual-e5 via sentence-transformers (pulls torch, about
1 GB more to install, for similar quality).

**ADR 3: Vector store.** A numpy float32 matrix of normalized vectors plus a
JSONL of chunk metadata, searched with one matrix product. A few thousand
chunks take well under a millisecond, it is fully inspectable, and it adds
no dependency. Rejected: Chroma (heavy dependency tree, a service like
component for a few MB of vectors), FAISS (fine, but unnecessary at this
size). Swap point: `KnowledgeRetriever.search()`.

**ADR 4: Existing user verification.** Phone number plus a PIN chosen at
registration, bcrypt hashed, with lockout and rate limiting. A landline
number is public information (directories, bills), so on its own it only
identifies, it does not authenticate; landlines cannot receive SMS, so SMS
OTP is not available; a voice call OTP needs a telephony provider outside
this pass. A 4 to 6 digit PIN is weak alone, which is why failed attempts
lock the account and are rate limited per address.

**ADR 5: Session tokens.** Short lived HS256 JWTs signed with one server
secret, with a distinct audience per session kind, delivered as httpOnly
cookies for the browser (same origin) and as bearer tokens otherwise.
Stateless, no session table; revocation is only needed for MCP clients and
is done by checking the client row on every request.

**ADR 6: MCP authentication.** The backend is its own OAuth 2.1
authorization server for machine clients: client credentials grant at
`/oauth/token`, short lived (15 min) scoped JWT access tokens bound to the
`/mcp` resource, discovery through protected resource metadata and
authorization server metadata, 401 with `WWW-Authenticate` for a missing
or bad token, per tool scope checks. Clients are created and revoked in the
admin portal; the secret is shown once and stored as a bcrypt hash. This
follows the MCP authorization spec's resource server expectations without
standing up a separate identity provider. Rejected: static API keys (no
expiry, no scopes, not what MCP clients discover), delegating to an
external IdP (no IdP available for this prototype).

**ADR 7: External agent credentials.** Encrypted at rest with Fernet
(AES-128-CBC with HMAC), key from `FIELD_ENCRYPTION_KEY`. Encryption, not
hashing, because the value must be sent to the agent. Never returned to
the browser after saving.

**ADR 8: Local model grounding.** On the local fallback, retrieved context
goes into the Llama system prompt, one model with extra context. It is not
presented as multi agent collaboration; on the cloud path it is the
tool/retrieval stage of the existing multi agent design.

**ADR 9: Knowledge sources beyond the brief's one domain (decided with the
owner on 2026-10-01).** www.sltmobitel.lk turned out to load every package
table and price in the browser from a separate API that needs a client id
issued to the website, so crawling it alone gave no prices (31% retrieval
hit rate on the test questions). The owner chose to also crawl
www.mobitel.lk, the same company's older official site, server rendered
with the package tables (97% hit rate), over installing Playwright to
render the new site (unknown whether the API answers headless traffic
behind the site's CDN). The host list is explicit in
knowledge/scraper.py, robots.txt is obeyed on both, and
`--no-include-legacy` restores the single domain build.

**ADR 10: Whisper hotwords.** Live tests showed Whisper writing "Mobital"
and "Mobilo" for "Mobitel", which hurt retrieval. faster-whisper's
hotwords option with "SLT Mobitel" fixed every test case at no measurable
cost, so it is on by default (`STT_HOTWORDS`); transcribe_stream()'s
contract is unchanged.

**ADR 11: Retrieval shape (changed 2026-10-02, after measuring).** The first
design sent four chunks of about 900 characters to the local model and took
911 ms to answer. Trimming chunks to their first characters lost the answer
whenever it sat later in the chunk, so the index now holds smaller chunks
(about 160 tokens, a deliberate change from the 300 to 500 tokens in the
original brief), a long chunk is cut to the sentences matching the question,
and ranking adds a small keyword boost (question words found in a chunk's
text or title) that raised the right-source rate from 94% to 100% while
relevance is still judged on the plain embedding score. Two short prompts
chosen by code (strict when notes were found, general when not) replaced one
long prompt, which let the small model mix its own knowledge into the notes
and invent an email address (accuracy fell from 85% to 73% before the
split). Ollama runs with a low temperature and bounded length and context.
Result: prompts 30% smaller, first token 321 ms against 382 ms, answers
632 ms against 911 ms, with English fact accuracy 79% against 85% on the
old, partly guessed score.

**ADR 12: Speech to text (2026-10-02, measured on 144 spoken clips).** The
noise reduction filter made Whisper worse (11.0% word errors against 3.3%
without it), so it is off. The brand hotword raised brand word accuracy from
46% to 85% but made Whisper write "SLT Mobitel" for silence (30 of 30 noise
clips with the larger model), so Whisper's own speech filter is on (0 of 30,
no cost). The larger `small` model is 3 times slower (1.5 s per pass), so it
only rechecks final transcripts the main model is unsure of (average log
probability below -0.40, about 19% of clips), recovering about half the
accuracy gap and helping Tamil most (21.1% to 6.7% character errors).
Sinhala cannot be transcribed by either local model, so it depends on Google
Cloud Speech-to-Text, which is built in but not enabled in the Google
project; a failing cloud call is skipped for 5 minutes.

**ADR 13: Gemini model name.** Pinned model names are retired by Google
(gemini-2.5-flash now answers 404 for newer accounts), so the default is the
`gemini-flash-lite-latest` alias, which always points at a current model.
The configured key itself is valid; every model currently answers 402
because the project has no credit left, which is a billing matter.


# Part 2: LiveKit transport, voicekit, new avatar, MCP hub client, OTP login

Written 2026-10-05, before any code. Everything below is additive: the
`/ws/voice` pipeline, server side playback, `bot3d.js`, the flat 2D face,
phone plus PIN login and every existing signature stay as they are, and each
new part sits behind a flag in `config.py`.

## P2.1 The system today (what this builds on)

One FastAPI process (`python -m backend`) serves the static frontend, `/ws/voice`,
`/chat`, `/auth`, the admin portal, the MCP server and `/health`. A voice turn is:
browser microphone, 16 kHz float32 chunks over a WebSocket, `vad.py` plus
`stt.py` (faster-whisper) on a thread, `Orchestrator.respond_stream` (external
agent, then Gemini, then local Llama, with `tool_callback` for retrieval), the
reply cut into sentences and spoken by `tts.speak_stream` on the SERVER's
speakers (Google, then edge-tts, then SAPI5). The browser only shows status
events, transcripts and an avatar (`bot3d.js`, Three.js r128 from a CDN) whose
mouth is animated from the sentence text, not from audio. The voice style is
hardcoded to `female_natural` on the server and the browser never sends one.
Existing users sign in with a landline and PIN (`backend/auth`), stored in
SQLite through SQLAlchemy and Alembic (revision 0001). Baseline first audio
after the final transcript: median 1.11 s (ARCHITECTURE.md, 2026-10-02).

Environment found on 2026-10-05: no `.env` file, no MCP hub key, no LiveKit
server or keys, Docker daemon not running. The hub answers 401 without a key,
so its tools could not be listed yet.

## P2.2 Design per requirement

**R1 LiveKit.** `backend/livekit_transport/`: `tokens.py` (router
`POST /api/livekit/token`, `GET /api/livekit/config`), `session.py`
(`LiveKitVoiceSession`: one conversation, built on rtc primitives and fully
testable with a fake room), `worker.py` (livekit-agents `Worker` that accepts
jobs and runs a session). The session subscribes to the user's microphone,
resamples to 16 kHz float32 and feeds the SAME `vad.py`, `stt.transcribe_stream`
and `Orchestrator` the WebSocket path uses (the same thread and queue bridge
idea, not a copy of their logic), then publishes voicekit audio as a track.
Status, transcript and reply events travel on a LiveKit data channel with the
exact JSON shapes of `/ws/voice`, so the page's existing handlers run unchanged.
Barge in: speech detected while speaking cancels synthesis, clears the audio
source queue and sends `{"type":"interrupt"}`. `VOICE_TRANSPORT` selects what
the frontend does; `/ws/voice` is never removed.

**R2 voicekit.** `backend/voicekit/` imports nothing from the rest of the
project (see ADR 2.2). Providers, sinks, adapter, API router factory, JS
client in `frontend/voicekit/voicekit.js`, docs in `docs/VOICEKIT.md`.

**R3 Avatar.** `frontend/avatar/` holds a renderer built on
met4citizen/TalkingHead (MIT) behind `AVATAR_RENDERER=talkinghead|legacy`.
Lip sync is driven by the audio that reaches the browser: provider word timings
when voicekit sends them, audio analysis visemes otherwise. The page falls back
to `bot3d.js` and then the flat face if WebGL or assets fail. A male/female
toggle in the existing top bar style changes avatar and `voice_style`
(`female_natural` / `male`) together and is stored in `localStorage`.

**R4 MCP hub.** `backend/mcp_client/`: official SDK, transport detection
(streamable HTTP first, SSE second), timeout, retry, breaker, typed errors,
a `context_for(text)` callback and a `compose_tool_callbacks()` helper so the hub
adds to retrieval instead of replacing it. Status appears under `mcp_hub` in
`/health`. Tool choice cannot be hard coded because the tool list is not known
yet: `MCP_HUB_TOOLS` names them, otherwise every tool with one required text
argument is eligible.

**R5 OTP.** `backend/otp/`: service, `OtpSender` interface with console, email
(SMTP) and SMS gateway senders, routes under `/auth/otp/*`, a new Alembic
revision for the contact fields and the challenge table. `AUTH_METHODS=pin,otp`
chooses what the UI offers; `GET /auth/methods` tells it. Customer lookup uses
`MCP_HUB_CUSTOMER_LOOKUP_TOOL` when set and reachable, else the local `users`
table.

**R6 Voices.** Research picks them; voicekit ships edge-tts and Google (existing
chain), SAPI5 and a local open model provider (Piper or Kokoro, whichever the
license check allows) loaded only if its files are present.

## P2.3 Flags and new environment variables

All defined in `config.py` and `.env.example` already: `VOICE_TRANSPORT`,
`LIVEKIT_URL/API_KEY/API_SECRET/TOKEN_TTL_SECONDS/AGENT_NAME`, `AVATAR_RENDERER`,
`VOICEKIT_ENABLED/API_KEYS/MAX_TEXT_CHARS/RATE_LIMIT_PER_MINUTE/LOCAL_VOICE_DIR`,
`MCP_HUB_ENABLED/URL/AUTH_HEADER/API_KEY/TIMEOUT_SECONDS/RETRIES/BREAKER_SECONDS/TOOLS/CUSTOMER_LOOKUP_TOOL`,
`AUTH_METHODS`, `OTP_*`, `SMTP_*`, `SMS_*`. Defaults keep today's behavior
(`websocket`, `legacy`, `pin`). They flip to `livekit` and `talkinghead` only
after the end to end run in Phase 2.

## P2.4 Contracts between subagents

**voicekit (Agent C), consumed by LiveKit (B), the API (C) and the avatar client (D)**

```
voicekit.types
  @dataclass AudioChunk: pcm: bytes (16 bit little endian mono), sample_rate: int, sentence_index: int
  @dataclass WordTiming: text: str, start_ms: float, duration_ms: float, sentence_index: int
  @dataclass SentenceStart: text: str, sentence_index: int
  @dataclass SynthesisDone: provider: str, first_audio_ms: float, total_ms: float
  SynthEvent = AudioChunk | WordTiming | SentenceStart | SynthesisDone
  class VoicekitError(Exception); ProviderUnavailable(VoicekitError); InvalidRequest(VoicekitError)
voicekit.settings
  @dataclass VoicekitSettings: google_credentials_path: str = "", azure_key: str = "", azure_region: str = "",
      cloud_provider: str = "google", local_voice_dir: str = "", max_text_chars: int = 2000,
      provider_order: tuple = ("cloud", "edge", "local_model", "sapi")
voicekit.core
  def synthesize_stream(text_or_iterable, language="en", voice="female_natural",
        settings=None) -> Iterator[SynthEvent]
      text_or_iterable: str or iterable of str (chunks of a streaming reply, cut into
      sentences with the same rules as tts.SentenceChunker, reimplemented). voice is
      "female_natural" or "male". Events arrive as soon as the provider yields them.
      Never raises for provider failure: falls through the provider order, finally
      yields a SynthesisDone with provider "none" (typed error only for InvalidRequest).
      Prints "voicekit first audio (<provider>, <lang>): N ms" itself.
  def list_voices() -> list[dict]   {language, voice, provider, name, license, gender}
voicekit.sinks (all take SynthEvent iterators)
  class Sink(ABC): def play(self, events) -> None; def cancel(self) -> None
  SpeakerSink (sounddevice), CallbackSink(fn), HttpChunkSink (generator of bytes),
  WebSocketSink(send_bytes, send_json), LiveKitSink(audio_source)  # duck typed: capture_frame, clear_queue
voicekit.adapter
  def speak_agent(agent_or_callable, sink=None, language="en", voice="female_natural") -> Callable[[str], str]
      Wraps any ChatAgent (respond_stream or respond) or a callable returning str / iterable.
voicekit.api
  def create_router(settings, api_keys: Callable[[], list[str]], rate_limit_per_minute) -> APIRouter
      POST /api/tts/stream {text, language, voice, format: "pcm"|"wav"|"ndjson"}  -> chunked body
        header X-Sample-Rate; ndjson lines {"type": "audio"|"timing"|"done", ...} (audio base64)
      WS /ws/tts   client {"type":"speak","text","language","voice"} / {"type":"cancel"}
        server {"type":"audio_start","sample_rate"}, binary PCM frames,
        {"type":"timings","words":[{text,start_ms,duration_ms}]}, {"type":"done"}
      Auth: header X-API-Key (WS also ?api_key=). 401 missing or wrong, 429 over the limit,
      422 bad body or text too long. Keys compared in constant time, never logged.
```

**LiveKit (B)** consumes the existing `vad.VoiceActivityDetector`,
`stt.transcribe_stream`, `agent.Orchestrator.respond_stream(text, user_name, language)`,
`config.*`, `auth.dependencies.verified_user_from_request`. Token request body
`{language, voice_style, user_name, session_token}`, all optional. Response
`{url, token, room, identity, expires_at}`. Data channel topic `va-events`:
JSON identical to the `/ws/voice` server events plus `{"type":"interrupt"}`.
Client to agent topic `va-control`: `{"type":"set_voice","voice_style"}`,
`{"type":"mute","muted":bool}`. Audio track name `agent-voice`, 24000 Hz mono.

**MCP client (E)**
```
mcp_client.errors: HubError(Exception); HubUnavailable; HubAuthError; HubToolError; HubBreakerOpen
mcp_client.client
  class HubClient: __init__(url, auth_header, api_key, timeout, retries, breaker_seconds)
      async list_tools() -> list[HubTool]; async call_tool(name, arguments) -> str
      def status() -> dict {configured, enabled, transport, reachable, breaker_open, tools, last_error}
      Never prints or returns the key. last_error is a short sanitized string.
  def get_hub() -> HubClient   process wide, built from config
mcp_client.tools
  def hub_context_for(text: str) -> Optional[str]    sync, never raises, None when off or breaker open
  def compose_tool_callbacks(*callbacks) -> Callable[[str], Optional[str]]   joins non-empty outputs
```

**OTP (E)**
```
otp.sender: class OtpSender(ABC): channel: str; def send(self, destination: str, code: str, language: str) -> None
     ConsoleOtpSender, EmailOtpSender, SmsGatewayOtpSender; def get_sender() -> OtpSender
otp.service
  def request_otp(phone, client_ip, language=None) -> OtpChallengeInfo
      {challenge_id, masked_destination, channel, expires_in, resend_in}
  def verify_otp(challenge_id, code, client_ip) -> VerifiedUser   (auth.users.VerifiedUser)
  class OtpError(Exception): reason in {"invalid","expired","locked","rate_limited","cooldown","unavailable"}, retry_after
otp.routes: router prefix /auth
  GET /auth/methods -> {"methods": ["pin","otp"]}
  POST /auth/otp/request {phone, language?} -> 200 OtpChallengeInfo (same shape for unknown numbers)
  POST /auth/otp/verify {challenge_id, code} -> {display_name, token, expires_at} + user_session cookie (as /auth/verify)
  errors: 401 invalid or expired (same text), 423 locked, 429 rate limited or cooldown (Retry-After), 422 bad input
```
DB additions (revision 0002, shared files patched by the lead): `users.mobile_e164`,
`users.email` (nullable), table `otp_challenges(id, user_id, code_hash, destination_hash,
channel, attempts, expires_at, consumed_at, created_at, client_ip_hash)`.

**Avatar (D)** consumes the WebSocket events, the LiveKit `va-events` and
voicekit's `/ws/tts` timings. It exposes
`window.avatarRenderer = {init(container, opts), setGender("female"|"male"), setState(state),
speakAudio(audioElementOrStream, timings?), stop(), destroy()}`, a `ready` promise and a
`fallback` event.

## P2.5 File ownership

| Owner | May create or edit |
| --- | --- |
| Lead | config.py, server.py, requirements.txt, .env.example, INTERFACES.md, README.md, ARCHITECTURE.md, KNOWN_LIMITATIONS.md, index.html, app.js, style.css, db models and migrations, scripts, final commits |
| A Research | docs/RESEARCH.md, docs/THIRD_PARTY.md |
| B LiveKit | backend/livekit_transport/** |
| C Voicekit | backend/voicekit/**, frontend/voicekit/**, docs/VOICEKIT.md, examples/voicekit/** |
| D Avatar | frontend/avatar/** (including assets) |
| E MCP and OTP | backend/mcp_client/**, backend/otp/** |
| F QA | tests/test_voicekit_*.py, test_livekit_*.py, test_otp_*.py, test_mcp_hub_*.py, test_avatar_*.py |

Subagents return proposed patches for shared files as text in their final
report; only the lead edits them.

## P2.6 Risks

1. No hub key: hub tool names, transport and customer lookup are unknown. Mitigation: configuration driven tool choice, a mock hub in tests, "not live tested" in KNOWN_LIMITATIONS.
2. No LiveKit server: end to end audio is untested until one runs. Mitigation: fake room tests and a documented `livekit-server --dev` command.
3. Free GLBs with ARKit plus Oculus visemes and a commercial license may not exist, and Ready Player Me is gone. Mitigation: Agent A decides before D builds; if none qualifies, the best licensed pair ships and the gap is reported.
4. Sinhala and Tamil have no audio viseme model: they use provider word timings (edge-tts) or audio analysis.
5. A second Whisper copy in a worker process would double memory: the worker runs jobs as threads of the app process.
6. Added latency in the LiveKit path (WebRTC buffering, resampling). Measured and recorded, not hidden.
7. `three@0.128` is a global in the page and TalkingHead needs a modern Three as an ES module: loaded through an import map, kept separate.

## P2.7 Test plan

Unit: voicekit providers with fakes, fallback order, key auth and rate limit,
sinks; LiveKit token claims and TTL, session with a fake room (frames in,
transcripts and audio out, barge in); MCP client against a local mock hub
(both transports, 401, timeout, breaker, composed callback); OTP expiry, three
attempts, cooldown, per number and per IP limits, hashing, masking, no code in
logs; avatar fallback by a Node test of the renderer loader. The existing
suite must stay green after every wiring step. Manual: speak in a real browser,
hear the reply, watch the mouth, switch gender mid session, OTP login.

## P2.8 ADRs

**ADR 2.1 Both transports, WebSocket stays the default until proven.** The brief
makes LiveKit the target default but demands `/ws/voice` keep working, and a
new transport cannot be trusted before a real browser run. Flags flip last.

**ADR 2.2 voicekit reimplements the provider calls instead of importing `tts.py`.**
The brief asks both to wrap the existing chain "without changing tts.py" and
for "no imports from unrelated project modules". Importing `tts.py` would pull in
`config`. voicekit therefore carries its own small provider code (same voices,
same order, same Google Chirp3 and edge-tts mapping) and takes credentials as a
settings object. `tts.py` stays untouched and keeps serving server side playback.
Cost: two copies of the voice map; a test checks the two lists agree.

**ADR 2.3 Worker built on livekit-agents with our own session class.** The
framework's `AgentSession` wants its own STT, LLM and TTS plugins, which would
replace the modules we must reuse. We use its `Worker`, job dispatch and
lifecycle, and run our existing pipeline inside the job through
`LiveKitVoiceSession`, which only uses `livekit.rtc`.

**ADR 2.4 Events over a data channel in the old JSON shape.** The page keeps one
message handler for both transports, and nothing in the UI has to change.

**ADR 2.5 Avatar: TalkingHead (MIT).** It already provides Oculus visemes, ARKit
blend shapes, blinking, gaze, moods and gestures, and can be driven by word
timings or audio. The final asset choice is in docs/RESEARCH.md.

**ADR 2.6 MCP hub tool use is configuration driven.** Tool names are unknown
until a key is available; guessing them would be an unverifiable stub.

**ADR 2.7 OTP anti enumeration.** Unknown numbers get the same response shape
and timing class as known ones, and no message is sent.

**ADR 2.8 Provider fallback order unchanged** (cloud, edge-tts, SAPI5). A local
open model is inserted before SAPI5 only, because it is offline and better
sounding than SAPI5, and never ahead of the first two.
