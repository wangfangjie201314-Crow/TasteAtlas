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

The equivalent web endpoints are POST /api/tools/resolve, /api/tools/plan and /api/tools/state. Do not log profile references or share them. Profiles expire after 15 minutes; the server keeps at most 32 unexpired profiles. Planning has a bounded runtime and no automatic upstream retry. Request reservations are durable in SQLite, shared across web and MCP and consumed even when upstream fails. Changing an existing cap is rejected rather than resetting usage.

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

The review wrapper does not change the local web/MCP entry points or launch a tunnel. Docker now defaults to `backend.hosted:create_app`, an authenticated factory with the historical LLM route hidden. It runs one worker as user 10001 with access logging and proxy-header trust disabled. The image has not been built or deployed by the maintainer; the factory has key-free process tests. Terminate public TLS at the hosting provider and preserve the browser's Host header.

Supply `QLOO_API_KEY` and `TASTEATLAS_REVIEW_ACCESS_CODE` through the host's private secret store. The access code must be 32-128 ASCII letters, digits, underscores or hyphens; the review username is `reviewer`. Never commit real values or put them in launch arguments. Set `TASTEATLAS_STATE_DIR` to an absolute private persistent directory outside the application source tree, readable and writable by user 10001. It must already contain the existing `guided.sqlite` with its authorized 12-call cap. Missing, corrupt or incompatible storage refuses startup rather than creating another allowance. LLM credentials and local Windows credential fallback are forcibly disabled. Do not use an ephemeral free filesystem as persistent storage.

To migrate, stop the old service first, back up and transfer its complete SQLite state consistently, and start exactly one instance against the same ledger. Never deploy a fresh database, run a copied database in parallel with the original, change its cap, or restore an older ledger to obtain more calls. Mount loss or a process restart must preserve consumption. Any new request allowance needs its own explicit authorization. A deployment that only passes a health check has not passed the real end-to-end Qloo flow.

The maintainer has exercised an authorized, access-controlled temporary HTTPS preview: one real Qloo flow and mobile cached-result restoration, with zero LLM calls. Its separate durable preview ledger is at 6/12; the original local 19/24 ledger is preserved. A Quick Tunnel has a changing URL and no uptime guarantee; it is not stable hosting for the judging period. Current preview addresses, access codes and private runtime state are excluded from this repository. Official reference: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/ .

The public repository and competition submission have not been published. Stable hosting and a judging-period request allowance remain unresolved. The supplied Render blueprint has the .disabled suffix to prevent accidental key-backed public use. The current shared, finite review cap is not a public service quota design or evidence that unrestricted judging access is ready.

The historical free-text LLM pipeline remains in the source for reference. It is disabled by default and has not passed a real end-to-end LLM acceptance run. Enabling it requires separate credentials and explicit spending authorization.

MIT license. Official competition requirements: https://qloo.devpost.com/rules .
