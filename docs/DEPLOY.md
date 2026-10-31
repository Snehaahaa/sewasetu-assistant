# Deploying (one VM, ~10 minutes)

Needs: a Linux VM with Docker + Docker Compose, a DNS name pointing at it (ports 80/443 open).

```bash
git clone <your private repo> sewasetu-assistant && cd sewasetu-assistant
cat > .env <<'ENV'
PUBLIC_BASE_URL=https://sewasetu.example.in      # exactly what people type; stamped into tokens + discovery docs
SITE_ADDRESS=sewasetu.example.in                  # Caddy gets a Let's Encrypt certificate for this name
ADMIN_PASSWORD=<a long random password>           # the officer's departmental login (user: admin)
# SCHEME_DEADLINE=2027-12-31 23:59                # ONLY once the Director confirms the window is extended (see below)
ENV
docker compose up --build -d
```

| What | URL |
|---|---|
| Portal (citizen website, officer admin) | `https://sewasetu.example.in/` , `/admin` |
| **Citizen MCP endpoint** | `https://sewasetu.example.in/citizen/mcp` |
| **Officer MCP endpoint** | `https://sewasetu.example.in/officer/mcp` |
| Eval dashboard | `https://sewasetu.example.in/eval-dashboard` |
| SMS gateway console (simulated) | `https://sewasetu.example.in/__gateway/` |

Check: `curl -s https://sewasetu.example.in/.well-known/oauth-authorization-server` and
`curl -si -X POST https://sewasetu.example.in/citizen/mcp` (expect `401` with a `WWW-Authenticate ... resource_metadata=` header).

Try it with a real client: `claude mcp add --transport http sewasetu-citizen https://sewasetu.example.in/citizen/mcp`, then `/mcp` -> authenticate.
Sign in with any 10-digit mobile; the OTP appears in the SMS console (`/__gateway/`). For the officer endpoint use user `admin` + `ADMIN_PASSWORD`.

## Two things the Director must know before the window closes

1. **The scheme window in `app/config/app.ini` ends 2026-10-31 23:59.** After that the portal (web form and assistants alike) refuses every
   application with "the application window has closed". The assistants report this honestly, but nobody can test or apply. If the pension is
   ongoing, set `SCHEME_DEADLINE` in `.env` and `docker compose up -d`. I did not change the legal rule myself.
2. **The nightly deemed-approval job runs at start-up**, and all 700 PENDING rows in the seed are older than 15 days, so they become
   DEEMED_APPROVED immediately (9 of them were under 60 on the day they applied). That is what the law says; the officer assistant shows
   such cases with their flags but cannot reverse a deemed approval.

## Running the evals on the VM

```bash
pip install requests pillow          # + Claude Code CLI logged in (needs a plan with usage)
export PORTAL=https://sewasetu.example.in CITIZEN_MCP_URL=$PORTAL/citizen/mcp OFFICER_MCP_URL=$PORTAL/officer/mcp
export ADMIN_PASSWORD=...  EVAL_DB_DOCKER=1
python3 evals/run_evals.py --mode scripted --label "VM, scripted"
python3 evals/run_evals.py --mode llm --model haiku --label "VM, real client"
python3 evals/run_evals.py --cleanup-only        # removes the synthetic citizens (99001xxxxx / 99002xxxxx) from the live database
```
Results land in `evals/results/` and appear on `/eval-dashboard` immediately (the folder is mounted into the portal).
