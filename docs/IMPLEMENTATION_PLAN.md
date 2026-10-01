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
`SCRAPER_MAX_PAGES`, `MCP_ENABLED`, `STT_HOTWORDS`. All read in config.py only; documented
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
