# sewasetu-assistant

An AI-assistant channel for the Sewa Setu Old Age Pension portal: **two MCP servers over streamable HTTP**, one for the
applicant and one for the block officer, on top of the unchanged system of record (the portal and its Postgres).

```
 citizen's AI app ──OAuth(OTP)──► /citizen/mcp ─┐                       ┌─ website  (/apply, /status)
                                                ├─► shared rules ─► Postgres ◄─ admin (/admin)
 officer's AI app ──OAuth(login)► /officer/mcp ─┘   submit_application()
                                                    decide_application()
```

| Where | What |
|---|---|
| `app/agent/citizen.py` | citizen server: scheme info, my application, draft, age-proof upload, preview, submit, withdraw, clear |
| `app/agent/officer.py` | officer server: queue summary, list, open a case, view the age proof, decide (reason mandatory, red flags) |
| `app/agent/common.py` | MCP transport + OAuth resource-server checks (aud / role / scope / live session) |
| `app/app.py` | the portal; `submit_application`, `decide_application`, `withdraw_pending` are the single shared code path |
| `evals/` | invented people (`personas.py`), harness (`run_evals.py`), results shown at `/eval-dashboard` |
| `agent_tests/` | 27 adversarial protocol/business tests + an official-SDK-client conformance check |
| `docs/` | `DESIGN_NOTE.md`, `DEPLOY.md`, `LOOM_SCRIPT.md`, original portal README |

* Deploy: [docs/DEPLOY.md](docs/DEPLOY.md) — **read the note about the 31 Oct 2026 scheme window first.**
* Design note: [docs/DESIGN_NOTE.md](docs/DESIGN_NOTE.md)
* Local run without Docker (sandbox only): `devshim/pg_up.sh && devshim/run_local.sh`, then `PYTHONPATH=evals python3 agent_tests/test_flows.py`.
