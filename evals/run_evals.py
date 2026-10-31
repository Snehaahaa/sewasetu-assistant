#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Play the invented people against the assistant through a REAL MCP client (Claude Code, small model, limited
turns), then read the portal's database to see what is actually there.

  python3 evals/run_evals.py --label "run 1: first version" [--only kamala,hira] [--model haiku]

Flow per person: clean any earlier test rows -> set up fixtures through the portal's own door -> sign in for
real (OAuth + OTP from the SMS gateway) -> run each scripted turn with `claude -p` (resuming the session) ->
run the checks against Postgres -> store transcript + evidence in evals/results/run-<ts>.json."""
import os, sys, re, json, time, argparse, subprocess, tempfile, datetime, traceback
sys.path.insert(0, os.path.dirname(__file__))
import requests
import db
from lib import get_token
import personas
from fixtures import png_bytes

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.environ.get("EVAL_RESULTS_DIR") or os.path.join(HERE, "results")


def cleanup():
    for tbl in ("audit_log", ):
        db.run("DELETE FROM audit_log WHERE application_id IN (SELECT id FROM applications WHERE mobile LIKE '99001%' OR mobile LIKE '99002%')")
    db.run("DELETE FROM applications WHERE mobile LIKE '99001%' OR mobile LIKE '99002%'")
    db.run("DELETE FROM portal_users WHERE mobile LIKE '99001%' OR mobile LIKE '99002%'")
    db.run("DELETE FROM agent_drafts WHERE mobile LIKE '99001%' OR mobile LIKE '99002%'")


def parse_stream(raw):
    """-> (steps, final_text, session_id, num_turns, model, error)"""
    steps, by_id, session, final, nturns, model, err = [], {}, None, "", 0, None, None
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        t = e.get("type")
        if t == "system" and e.get("subtype") == "init":
            session, model = e.get("session_id"), e.get("model")
        elif t == "assistant":
            for c in e["message"]["content"]:
                if c["type"] == "text" and c["text"].strip():
                    steps.append({"type": "text", "text": c["text"]})
                elif c["type"] == "tool_use":
                    st = {"type": "tool", "name": c["name"].split("__")[-1], "input": c["input"], "result": None, "is_error": False}
                    steps.append(st); by_id[c["id"]] = st
        elif t == "user":
            content = e["message"]["content"]
            if isinstance(content, list):
                for c in content:
                    if c.get("type") == "tool_result" and c.get("tool_use_id") in by_id:
                        body = c.get("content")
                        if isinstance(body, list):
                            body = "\n".join(x.get("text", "[image]") for x in body)
                        by_id[c["tool_use_id"]]["result"] = (body or "")[:1500]
                        by_id[c["tool_use_id"]]["is_error"] = bool(c.get("is_error"))
        elif t == "result":
            final, nturns, session = e.get("result") or "", e.get("num_turns", 0), e.get("session_id") or session
            if e.get("is_error"):
                err = final
    return steps, final, session, nturns, model, err


def claude_turn(msg, cfg, server, model, session, max_turns, cwd):
    cmd = ["claude", "-p", msg, "--mcp-config", cfg, "--strict-mcp-config", "--tools", "",
           "--allowedTools", "mcp__" + server, "--model", model, "--max-turns", str(max_turns),
           "--output-format", "stream-json", "--verbose"]
    if session:
        cmd += ["--resume", session]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=420, cwd=cwd, stdin=subprocess.DEVNULL)
    return r.stdout, r.stderr


def find_link(steps_all):
    link = None
    for st in steps_all:
        if st["type"] == "tool" and st["result"] and "upload_link" in st["result"]:
            m = re.search(r'"upload_link":\s*"([^"]+)"', st["result"])
            if m:
                link = m.group(1)
    return link


def upload_via_link(steps_all, p):
    link = find_link(steps_all)
    if not link:
        return None
    r = requests.post(link, files={"document": ("certificate.png", png_bytes(p["name"].split(" (")[0], "01/01/1950"), "image/png")}, timeout=60)
    return "uploaded via link (HTTP %d)" % r.status_code


def run_scripted(p, ctx, token, url):
    """Layer 1: maximally compliant scripted assistant (see scripted.py)."""
    import scripted
    from lib import Mcp
    m = Mcp(url, token); m.init()
    steps = []

    def call(name, **args):
        s, e, raw = m.call(name, **args)
        shown = json.dumps(s, ensure_ascii=False)[:1500] if name != "attach_age_proof" else json.dumps(s, ensure_ascii=False)[:300]
        a2 = {k: (v[:40] + "...") if k == "content_base64" else v for k, v in args.items()}
        steps.append({"type": "tool", "name": name, "input": a2, "result": shown, "is_error": e})
        return s, e

    def upload():
        link = next((st for st in reversed(steps) if st["name"] == "get_age_proof_upload_link"), None)
        mt = re.search(r'"upload_link":\s*"([^"]+)"', link["result"]) if link else None
        act = "no link" if not mt else "uploaded via link (HTTP %d)" % requests.post(
            mt.group(1), files={"document": ("certificate.png", png_bytes("Applicant", "01/01/1950"), "image/png")}).status_code
        steps.append({"type": "text", "text": "[harness] citizen taps the link and uploads the age-proof photo: " + act})
    ctx["upload"] = upload
    scripted.SCRIPTS[p["id"]](ctx, call)
    return steps


def run_persona(p, a):
    kind = p.get("kind", "citizen")
    ctx = {"portal": a.portal, "mobile": p.get("mobile", "")}
    rec = {k: p[k] for k in ("id", "name", "age", "village", "block", "who", "speech", "why", "goal", "must_be_true")}
    rec["kind"] = kind
    rec["mobile"] = p.get("mobile") if kind == "citizen" else None
    t0 = time.time()
    steps_all, turn_recs = [], []
    try:
        cleanup() if not a.no_clean_each else None
        ctx = p["setup"](ctx)
        url = a.cit if kind == "citizen" else a.off
        server = "sewasetu-" + ("citizen" if kind == "citizen" else "officer")
        token = get_token(a.portal, "citizen" if kind == "citizen" else "officer", url, mobile=p.get("mobile"),
                          password=os.environ.get("ADMIN_PASSWORD", "officer-pass-1"), client_name="claude-code-eval")
        tmp = tempfile.mkdtemp(prefix="ev-")
        cfg = os.path.join(tmp, "mcp.json")
        json.dump({"mcpServers": {server: {"type": "http", "url": url, "headers": {"Authorization": "Bearer " + token}}}}, open(cfg, "w"))
        session, model = None, None
        if a.mode == "scripted":
            steps = run_scripted(p, ctx, token, url)
            steps_all += steps
            turn_recs.append({"user": "(scripted, maximally compliant assistant: does exactly what is asked, tries every shortcut)",
                              "harness_action": None, "steps": steps, "final": "", "num_turns": len(steps)})
            model = "scripted"
        uploaded = False
        queue = list(p["turns"](ctx) if a.mode == "llm" else [])
        while queue:
            msg = queue.pop(0)
            action, wants_upload = None, msg.startswith("{{UPLOAD}}")
            if wants_upload:
                msg = msg[len("{{UPLOAD}}"):]
                action = upload_via_link(steps_all, p)          # the family can only tap a link that exists
                uploaded = uploaded or bool(action)
            out, err = claude_turn(msg, cfg, server, a.model, session, a.max_turns, tmp)
            steps, final, session, nturns, model, cerr = parse_stream(out)
            if cerr or (not steps and not final):
                raise RuntimeError("client error: %s %s" % (cerr or "", (err or out)[-300:]))
            steps_all += steps
            turn_recs.append({"user": msg, "harness_action": action or ("(no upload link had been issued yet)" if wants_upload else None),
                              "steps": steps, "final": final, "num_turns": nturns})
            if wants_upload and not uploaded and find_link(steps_all):
                # the assistant has now issued the link: the family taps it, then tells the assistant
                action = upload_via_link(steps_all, p); uploaded = True
                queue.insert(0, "I have opened your link and uploaded the photo of the certificate.")
                queue[0] = "{{NOTE}}" + action + "{{/NOTE}}" + queue[0]
            if queue and queue[0].startswith("{{NOTE}}"):
                note, queue[0] = queue[0][8:].split("{{/NOTE}}")
                turn_recs[-1]["harness_action"] = (turn_recs[-1]["harness_action"] or "") + " -> then: " + note
        rec["model"] = model
        ctx["assistant_text"] = "\n".join(s["text"] for s in steps_all if s["type"] == "text")
        checks = []
        for kindc, desc, fn in p["checks"](ctx):
            try:
                passed, evidence = fn()
            except Exception as e:
                passed, evidence = False, "check crashed: %r" % e
            checks.append({"kind": kindc, "desc": desc, "passed": bool(passed),
                           "evidence": evidence if isinstance(evidence, (str, int)) else json.dumps(evidence, ensure_ascii=False)})
        rec["checks"] = checks
        rec["passed"] = all(c["passed"] for c in checks if c["kind"] == "db")
        if a.mode == "scripted":
            checks = [c for c in checks if c["kind"] == "db"]
            rec["checks"] = checks
        rec["replies_ok"] = all(c["passed"] for c in checks if c["kind"] == "reply")
        rec["status"] = "pass" if rec["passed"] else "fail"
    except Exception as e:
        rec.update(status="error", passed=False, replies_ok=False, checks=[], error="%s\n%s" % (e, traceback.format_exc()[-600:]))
    rec["turns"] = turn_recs
    rec["tool_calls"] = sum(1 for s in steps_all if s["type"] == "tool")
    rec["tool_errors"] = sum(1 for s in steps_all if s["type"] == "tool" and s["is_error"])
    rec["seconds"] = round(time.time() - t0)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portal", default=os.environ.get("PORTAL", "http://localhost:8000"))
    ap.add_argument("--cit", default=os.environ.get("CITIZEN_MCP_URL", "http://localhost:8001/citizen/mcp"))
    ap.add_argument("--off", default=os.environ.get("OFFICER_MCP_URL", "http://localhost:8002/officer/mcp"))
    ap.add_argument("--mode", choices=["llm", "scripted"], default="llm",
                    help="llm = real Claude Code client with a small model; scripted = maximally compliant scripted assistant")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-turns", type=int, default=16)
    ap.add_argument("--only", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--no-clean-each", action="store_true")
    ap.add_argument("--final-cleanup", action="store_true", help="remove all test rows afterwards (do this on the live VM)")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    run = {"id": datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), "label": a.label, "notes": a.notes, "model": a.model if a.mode == "llm" else "scripted", "mode": a.mode,
           "started": datetime.datetime.now().isoformat(timespec="seconds"), "portal": a.portal, "personas": []}
    try:
        run["commit"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
    except Exception:
        run["commit"] = ""
    for p in personas.ALL:
        if only and p["id"] not in only:
            continue
        print("▶ %-10s" % p["id"], end=" ", flush=True)
        rec = run_persona(p, a)
        run["personas"].append(rec)
        print(rec["status"].upper(), "(%ds, %d tool calls)" % (rec["seconds"], rec["tool_calls"]),
              "" if rec["status"] != "fail" else " ✗ " + "; ".join(c["desc"] for c in rec["checks"] if not c["passed"]))
        if rec["status"] == "error":
            print("   ", rec["error"].splitlines()[0][:200])
    ps = [r for r in run["personas"] if r["status"] != "error"]
    allchecks = [c for r in ps for c in r["checks"] if c["kind"] == "db"]
    run["summary"] = {"personas": len(run["personas"]), "passed": sum(1 for r in ps if r["passed"]), "errors": len(run["personas"]) - len(ps),
                      "db_checks": len(allchecks), "db_checks_passed": sum(1 for c in allchecks if c["passed"]),
                      "replies_ok": sum(1 for r in ps if r["replies_ok"])}
    run["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "run-%s.json" % run["id"])
    json.dump(run, open(path, "w"), ensure_ascii=False, indent=1)
    idx = os.path.join(RESULTS, "index.json")
    runs = json.load(open(idx)) if os.path.exists(idx) else []
    runs.append({"id": run["id"], "label": a.label, "model": run["model"], "mode": a.mode, "commit": run["commit"], "started": run["started"],
                 "summary": run["summary"], "file": os.path.basename(path)})
    json.dump(runs, open(idx, "w"), indent=1)
    if a.final_cleanup:
        cleanup()
    print("\n%d/%d people passed their database checks (%d/%d checks) -> %s" % (
        run["summary"]["passed"], run["summary"]["personas"], run["summary"]["db_checks_passed"], run["summary"]["db_checks"], path))


if __name__ == "__main__":
    main()
