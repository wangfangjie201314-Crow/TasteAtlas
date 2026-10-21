# TasteAtlas

A Qloo-powered city discovery tool for travelers and agents. Give it a city and a named cultural favorite, confirm the matching artist, person, film or other entity, then get a restaurant, a museum and movie inspiration.

The current guided workflow uses Qloo search, genre tags and Insights. It deterministically selects from Qloo-ranked candidates and validates their IDs and slots. It makes **zero LLM calls**. It does not infer opening hours, prices, availability or travel times. The time labels are suggested slots, not a booking or optimized route. Movie suggestions are cultural inspiration, not local events.

## Run the web tool

Python 3.10+, the packages in requirements.txt, and an authorized Qloo event key are required. Set QLOO_API_KEY in your environment. Set TASTEATLAS_STATE_DIR to a private persistent directory outside this repository. Set TASTEATLAS_QLOO_CALL_LIMIT to an explicit request cap (default 24). Never put the key in frontend code or a public repository.

    pip install -r requirements.txt
    uvicorn backend.main:app --host 127.0.0.1 --port 8000

Open http://127.0.0.1:8000 . QLOO_BASE_URL must be https://hackathon.api.qloo.com for this event key. Optional QLOO_PROXY_URL is server-only. Windows local DPAPI support is opt-in; the private key file is never distributed. No LLM key is needed for guided mode.

## Agent interface

Run python -m backend.mcp_server with the same environment to use newline-delimited JSON-RPC over standard input/output. It supports MCP 2025-06-18 initialization and three tools:

- tasteatlas_resolve: city + favorite, returns matching entities and an opaque private profile reference.
- tasteatlas_plan: profile_ref + a confirmed entity_id, returns a grounded plan.
- tasteatlas_state: profile_ref, retrieves progress or a cached result without a new Qloo call.

The equivalent web endpoints are POST /api/tools/resolve, /api/tools/plan and /api/tools/state. Do not log profile references or share them. Profiles expire after 15 minutes; the server keeps at most 32 unexpired profiles. Planning has a bounded runtime and no automatic upstream retry. Request reservations are durable in SQLite (or the optional review PostgreSQL backend), shared across web and MCP and consumed even when upstream fails. Changing an existing cap is rejected rather than resetting usage.

## Validation

Run the key-free tests:

    python tests/test_agents.py
    python -m unittest discover -s tests -p test_contracts.py
    python -m unittest discover -s tests -p test_guided.py
    python -m unittest discover -s tests -p test_preview.py
    python -m unittest discover -s tests -p test_hosted.py

On 2026-10-19 the maintainer also exercised two real Qloo flows for Austin, using confirmed artist matches for Radiohead and Björk, through the browser and MCP respectively. Each returned two local places and one movie. Local evidence is private and is excluded from this source package. These checks do not certify public hosting or a competition submission.

## Deployment status

The opt-in `backend.preview.PreviewGate` prepares an authenticated review surface around the web app. It allows only the current guided UI and tool routes, bounds JSON bodies and workflow action frequency, and hides the historical LLM endpoint. Supply a strong private access code in server memory; keep its storage outside this repository. A maintained Windows entry can prepare a DPAPI access file, check file readiness without decrypting it, and serve the wrapper on loopback. Starting a public tunnel is a separate operation.

The review wrapper does not change the local web/MCP entry points or launch a tunnel. Docker now defaults to `backend.hosted:create_app`, an authenticated factory with the historical LLM route hidden. It runs one worker as user 10001 with access logging and proxy-header trust disabled. The Docker image was built and deployed on Render Free on 2026-10-19, using Free PostgreSQL for the durable review ledger. On 2026-10-20, a real Austin/Radiohead workflow on that host and an independent read-only check each passed 12 acceptance checks, including three stops, zero LLM calls, cached reuse and durable quota preservation. These are bounded acceptance results, not a long-term availability guarantee. Terminate public TLS at the hosting provider and preserve the browser's Host header.

Supply `QLOO_API_KEY` and `TASTEATLAS_REVIEW_ACCESS_CODE` through the host's private secret store. The access code must be 32-128 ASCII letters, digits, underscores or hyphens; the review username is `reviewer`. Never commit real values or put them in launch arguments. Set `TASTEATLAS_STATE_DIR` to an absolute private persistent directory outside the application source tree, readable and writable by user 10001. It must already contain the existing `guided.sqlite` with its authorized 12-call cap. Missing, corrupt or incompatible storage refuses startup rather than creating another allowance. LLM credentials and local Windows credential fallback are forcibly disabled. Do not use an ephemeral free filesystem as persistent storage.

To migrate, stop the old service first, back up and transfer its complete SQLite state consistently, and start exactly one instance against the same ledger. Never deploy a fresh database, run a copied database in parallel with the original, change its cap, or restore an older ledger to obtain more calls. Mount loss or a process restart must preserve consumption. Any new request allowance needs its own explicit authorization. A deployment that only passes a health check has not passed the real end-to-end Qloo flow.

The earlier access-controlled temporary HTTPS preview exercised a real Qloo flow and mobile cached-result restoration with zero LLM calls. Its six consumed requests were migrated to the cloud ledger; the old local writer and tunnel were stopped. The cloud acceptance flow then consumed the remaining six authorized requests. As last verified on 2026-10-20, the hosted ledger is at **12/12, with no remaining allowance for new generation**. An expired profile is not a persistent sample library: profiles retain their normal 15-minute TTL. Do not restart the old six-call snapshot, reset the counter or create a parallel ledger. The original local 19/24 demonstration is a separate historical record and is not additional cloud allowance. Access codes and private runtime state remain excluded from this repository.

The source is publicly available at https://github.com/wangfangjie201314-Crow/TasteAtlas under MIT. The competition entry remains a draft and has not been finally submitted. Free hosting deployment was accepted, but publicly accessible judging access and a suitable new request allowance remain unresolved. The current access-controlled, exhausted preview must not be represented as a ready-to-use public judging demo. Additional allowance needs separate explicit authorization and an audited implementation; no allowance change is made by these instructions. The supplied Render blueprint retains its .disabled suffix to prevent accidental key-backed deployment. The finite review cap is not a public service quota design. The event overview requires a functional externally published demo; a recording alone does not meet that requirement.

## Optional PostgreSQL review storage

For a host with an ephemeral filesystem, supply a private `TASTEATLAS_DATABASE_URL` instead of `TASTEATLAS_STATE_DIR`. The controlled factory selects PostgreSQL explicitly and never falls back to a new SQLite database. Connections use psycopg with mandatory TLS certificate and hostname verification, bounded connection/query/lock timeouts, and fixed public schema resolution. Runtime does not create tables or a request allowance. It requires the existing authorized 12-call ledger. Health checks do not query the database.

An operator must stop the old server and tunnel, preserve a consistent backup, and then use the one-time transfer command with the private database URL already in the environment:

    python -m backend.migrate_postgres /absolute/private/guided.sqlite --source-stopped

The source must be an intact, stopped SQLite DELETE-journal ledger with a 12-call cap and existing consumed requests. The target must be a dedicated empty PostgreSQL database. Other user schemas or objects, a second transfer, incompatible state and changes during transfer are refused. DDL and imported rows commit together; failed transfer rolls back. This command does not stop the old server itself. Do not restart the copied local ledger after switching. Any rollback must first stop the cloud writer and carry back the latest complete ledger, never an earlier call count.

Free hosting has limits. Render Free Web may sleep and loses its local files; its Free Postgres expires after 30 days. The accepted review database expires on 2026-11-18, after the currently stated judging end of 2026-11-17 12:45 Asia/Shanghai. This date check does not guarantee uptime. Reconfirm expiry and event timing before submission and keep both services on Free without a payment method or automatic paid upgrade. This is a time-limited review option, not permanent hosting or a service guarantee. Platform setup and a successful health check alone do not prove the real Qloo flow.

The new key-free storage safety suite can be run with `python -m unittest tests.test_postgres`. The separate `tests.test_postgres_live` suite requires a disposable real PostgreSQL test cluster and explicitly supplied `TASTEATLAS_TEST_PG_ADMIN_URL`; its role must create/drop uniquely named test databases. Never point it at production credentials. It performs no real Qloo or model calls. Local evidence records actual integration outcomes separately from offline tests and cloud app deployment.

The historical free-text LLM pipeline remains in the source for reference. It is disabled by default and has not passed a real end-to-end LLM acceptance run. Enabling it requires separate credentials and explicit spending authorization.

MIT license. Official competition requirements: https://qloo.devpost.com/rules .
