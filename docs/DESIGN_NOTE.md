# Design note: Sewa Setu assistants (one page)

**What exists.** Two MCP servers (streamable HTTP, OAuth 2.1 via the portal's own authorization server) at `/citizen/mcp` and
`/officer/mcp`. The portal and Postgres remain the system of record; an application made through an assistant is the same row,
audit trail and status-portal account as one made on the website or at the counter.

## The three decisions that mattered most

1. **The assistant is another door into the same room, not a second system.** The website and both servers call one function,
   `submit_application()`: validation, one live application per mobile *and* per person, a per-mobile lock so a retry or double
   click cannot create two, unique application numbers. The servers hold no scheme rules, so they cannot drift from the counter.
   Identity is the token's `sub` (the OTP-verified mobile). No citizen tool takes a mobile number, so an assistant cannot even
   name someone else's application.
2. **Two servers, two audiences, two tool sets, one live sign-in.** Each endpoint accepts only tokens minted for its own URL (RFC 8707)
   with its own role and scope, and checks on every call that the sign-in is still live. Signing out at a kiosk ends access at once
   instead of after the 20-30 minute token life. A citizen's assistant never sees an officer tool.
3. **The assistant proposes; a person disposes; the server enforces it.** Citizen: `preview_application` returns a review token
   bound to exactly what was read back, and `submit_application` needs it plus an explicit declaration flag. Officer: a reason is
   mandatory; hard flags (under 60, no age proof) make approval impossible; soft flags (possible duplicate person, shared bank
   account, malformed IFSC, birth date changed after "below 60") must be acknowledged by name; one case at a time. Text an applicant
   typed is always returned as `applicant_supplied` and treated as data.

## The one I would make differently

The portal has one shared officer login, so "recorded against her name" is really `officer:admin`. With more time I would add named
officer accounts (and block scoping) before anything else; accountability is what the department's IT team is asking for.

## What the evals showed (details at `/eval-dashboard`)

17 invented people (12 citizens, 5 officer scenarios), two layers. Layer 1 is a maximally compliant scripted assistant (does every shortcut
asked), so a pass means the portal itself holds. Layer 2 is Claude Code with a small model (Haiku) through the real OAuth flow. The
database decides pass/fail. Layer 1: 14/17 then 17/17 (one real finding: a changed birth date left no trace, now flagged to the officer).
Layer 2: 13/17 then 17/17 (three failures were my harness, two were real: an applicant name mentioned in passing was dropped; the upload
step cost too many turns; fixed in the tool descriptions and by returning the upload link in-line).

## Limits, stated plainly

Small-model layer used one model with scripted follow-ups; no rate limiting; the MCP transport is a small stateless hand-written
implementation (verified against the official SDK client); the scheme window in config ends 31 Oct 2026 and the start-up deemed-approval
run approves all 700 old pending rows (9 were under 60); the one-application-per-mobile rule blocks a second person on a shared phone.
