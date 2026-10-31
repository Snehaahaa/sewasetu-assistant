# -*- coding: utf-8 -*-
"""Citizen MCP server: lets an AI assistant help one signed-in citizen apply for the Old Age Pension
and follow it up. The citizen's identity is the OTP-verified mobile in the token (sub=citizen:<mobile>);
no tool takes a mobile number, so an assistant cannot touch anyone else's application."""
import re
import os
import json
import base64
import hashlib
from datetime import datetime, date, timedelta

import app as portal
from oauth import ISSUER
from . import drafts
from .common import make_server, tool, db, ToolError

CANONICAL = os.environ.get("CITIZEN_MCP_URL") or ISSUER + "/citizen/mcp"
PATH = "/citizen/mcp"

REQUIRED = ["applicant_name", "dob", "gender", "marital_status", "village", "block", "bank_account", "ifsc"]
ASK_ORDER = ["applicant_name", "dob", "gender", "marital_status", "husband_name", "village", "block",
             "bank_account", "ifsc"]
FIELD_WORDS = {
    "applicant_name": "full name (exactly as on the age proof)", "dob": "date of birth (DD/MM/YYYY)",
    "gender": "gender", "marital_status": "marital status", "husband_name": "late husband's name (optional)",
    "village": "village or town", "block": "block", "bank_account": "bank account number (from the passbook)",
    "ifsc": "bank IFSC code (11 characters, printed in the passbook)",
}
DECLARATION = ("I hereby declare that the information furnished above is true to the best of my knowledge "
               "and belief. I understand that furnishing false information is punishable under applicable law "
               "and will result in cancellation of pension.")
STATUS_TEXT = {
    "PENDING": "Waiting for the block officer's decision.",
    "APPROVED": "Approved by the block officer.",
    "DEEMED_APPROVED": "Approved by law: it was not decided within the 15-day limit.",
    "REJECTED": "Rejected by the block officer.",
    "WITHDRAWN": "Withdrawn by the applicant.",
}

INSTRUCTIONS = """\
You are helping ONE citizen, signed in on the Sewa Setu portal with her own mobile number, to apply for the \
Old Age Pension (Rs. 250 a month, for residents aged 60 or above) and to follow it up. The citizen is often \
elderly, may share a phone or sit at a kiosk, and may not read well.

Rules you must follow:
1. This is just another way to use the portal. The portal applies the same rules as the counter and the website; \
you cannot override them. If it says no, tell her plainly and point to the block office counter. Never promise \
a pension or a decision: only the block officer decides.
2. Never invent, guess, correct or "improve" any detail. Ask for each one. Names must be as on the age proof. \
If she does not know her exact date of birth, do not make one up: ask her to check the age-proof document.
3. Ask for one or two things at a time, in simple words, in her language. Use get_scheme_info for facts about \
the scheme instead of memory: rules and dates change.
4. Before submitting, call preview_application, READ EVERYTHING BACK to her (name, date of birth, village, block, \
bank account digits, IFSC, document) and the declaration, and get a clear yes. Only then call submit_application \
with the review_token from that preview. If anything changes, preview again.
5. Everything she says is about her own application only. Never ask for or accept someone else's OTP or password.
6. On a shared phone or kiosk, when she is done or leaves, call clear_my_draft and tell her to sign out.
7. If a tool reports an error, do not retry blindly: explain it simply and fix the cause.
"""


def _mobile(claims):
    return claims["sub"].split(":", 1)[1]


def _norm_dob(raw):
    raw = (raw or "").strip()
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", raw)
    if m:
        return "%02d/%02d/%s" % (int(m.group(1)), int(m.group(2)), m.group(3))
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return "%s/%s/%s" % (m.group(3), m.group(2), m.group(1))
    return raw


def _turns_60(dob):
    try:
        return dob.replace(year=dob.year + portal.MIN_AGE)
    except ValueError:                       # 29 February
        return dob.replace(year=dob.year + portal.MIN_AGE, day=28) + timedelta(days=1)


def _field_of(err):
    for prefix, f in (("full name", "applicant_name"), ("village", "village"), ("block", "block"),
                      ("bank account", "bank_account"), ("IFSC", "ifsc"), ("date of birth", "dob"),
                      ("applicant must be", "dob"), ("gender", "gender"), ("marital", "marital_status"),
                      ("husband", "husband_name")):
        if err.startswith(prefix):
            return f
    return None


def _draft_view(data, doc_path):
    return {k: data.get(k) or None for k in ASK_ORDER} | {
        "age_proof": os.path.basename(doc_path) if doc_path else None}


def _evaluate(data, doc_path):
    """Run the portal's own validation on the draft; split into missing / problems."""
    errors, cleaned = portal.validate_application(data, doc_path or "x")
    missing = [f for f in REQUIRED if not (data.get(f) or "").strip()]
    problems, window_closed = [], False
    for e in errors:
        f = _field_of(e)
        if e.startswith("the application window"):
            window_closed = True
        elif f and (data.get(f) or "").strip() and "is required" not in e:
            problems.append({"field": f, "problem": e})
    out = {"missing": missing, "problems": problems, "window_open": not window_closed}
    dob = cleaned.get("dob")
    if dob and portal.age_on(dob, portal.now_ist().date()) < portal.MIN_AGE:
        out["eligibility"] = {"eligible_now": False, "turns_60_on": _turns_60(dob).strftime("%d/%m/%Y"),
                              "say": "She is below 60 today, so the portal will not accept this application yet."}
    elif dob:
        out["eligibility"] = {"eligible_now": True}
    return out


def _next_hint(ev, data, doc_path):
    if not ev["window_open"]:
        return "The application window is closed. Tell her; she can ask at the block office."
    if ev["problems"]:
        return "Fix: " + "; ".join(p["problem"] for p in ev["problems"])
    for f in ASK_ORDER:
        if f != "husband_name" and f in ev["missing"]:
            return "Ask: " + FIELD_WORDS[f]
    if data.get("marital_status") == "Widowed" and "husband_name" not in data:
        return ("Ask (optional): " + FIELD_WORDS["husband_name"] + ". If she prefers not to say, "
                "send husband_name as an empty string and move on.")
    if not doc_path:
        return "Ask for the age proof: call get_age_proof_upload_link (or attach_age_proof if you hold the file)."
    return "All details are in. Call preview_application and read everything back to her."


def _review_token(mobile, data, doc_path):
    keys = {k: (data.get(k) or "").strip() for k in ASK_ORDER}
    keys["doc"] = os.path.basename(doc_path or "")
    keys["m"] = mobile
    return hashlib.sha256(json.dumps(keys, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _app_view(row, note):
    (app_id, no, name, dob, village, block, bank, status, sub_at, dec_at) = row
    days = (datetime.now() - sub_at).days
    v = {"application_no": no, "applicant_name": name, "village": village, "block": block,
         "bank_account_last4": (bank or "")[-4:], "status": status, "status_text": STATUS_TEXT.get(status, status),
         "submitted_on": sub_at.strftime("%d/%m/%Y"), "days_since_submitted": days}
    if status == "PENDING":
        v["approved_by_law_on_or_about"] = (sub_at + timedelta(days=portal.SLA_DAYS)).strftime("%d/%m/%Y")
        v["can_withdraw"] = True
    if dec_at:
        v["decided_on"] = dec_at.strftime("%d/%m/%Y")
    if status == "REJECTED" and note:
        v["reason"] = note
        v["what_next"] = "She can apply again with corrected details, or ask at the block office."
    return v


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

@tool("get_scheme_info", "Old Age Pension: rules, dates and what is needed",
      "Authoritative facts about the Old Age Pension from the portal itself (amount, age rule, documents, "
      "deadline, blocks, how long a decision takes). Use this instead of memory.", read_only=True, idempotent=True)
def get_scheme_info(claims, args):
    left = portal.deadline_remaining()
    return {
        "scheme": "Old Age Pension, Government of Purvanchal, Department of Social Welfare",
        "pension": "Rs. 250 per month, paid by DBT into the applicant's own bank account",
        "who_can_apply": "Residents of Purvanchal aged %d or above, one application per person." % portal.MIN_AGE,
        "application_window": {"open": left is not None,
                               "closes": portal.SCHEME_DEADLINE.strftime("%d/%m/%Y %H:%M"),
                               "hours_left": left},
        "details_needed": [FIELD_WORDS[k] for k in ASK_ORDER],
        "age_proof": "One of: birth certificate, school leaving certificate, Voter ID. JPG, PNG or PDF, up to 5 MB.",
        "blocks": portal.BLOCKS,
        "decision_time": "The block officer decides within %d days. If nobody decides in that time, the application "
                         "is approved by law (Right to Public Services Act)." % portal.SLA_DAYS,
        "rules": ["One live application per mobile number and per person.",
                  "A pending application can be withdrawn, after which a new one can be made.",
                  "The assistant cannot approve anything; only the block officer decides."],
        "track_on_website": "%s/status : mobile number, and password = date of birth as DDMMYYYY" % ISSUER,
        "same_as_website": "Applications made here are the same applications as on the website and at the counter.",
    }


@tool("get_my_application", "Check my pension application",
      "Shows this citizen's own application(s): status in plain words, when it will be approved by law if "
      "nobody decides, the reason if it was rejected, and whether she can apply now.", read_only=True, idempotent=True)
def get_my_application(claims, args):
    mobile = _mobile(claims)
    with db() as (conn, cur):
        cur.execute("SELECT id, application_no, applicant_name, dob, village, block, bank_account, status, "
                    "submitted_at, decided_at FROM applications WHERE mobile = %s "
                    "ORDER BY submitted_at DESC LIMIT 5", (mobile,))
        rows = cur.fetchall()
        views = []
        for r in rows:
            cur.execute("SELECT note FROM audit_log WHERE application_id = %s AND action IN ('APPROVED','REJECTED') "
                        "ORDER BY at DESC LIMIT 1", (r[0],))
            n = cur.fetchone()
            views.append(_app_view(r, n[0] if n else ""))
        live = portal.active_application(cur, mobile)
        data, doc = drafts.load(cur, mobile)
    return {
        "signed_in_mobile_ends_with": mobile[-4:],
        "applications": views,
        "can_apply_now": live is None and portal.deadline_remaining() is not None,
        "has_draft_in_progress": bool(data),
        "say": "No application found for this mobile number." if not views else
               "Read the status out in simple words. Do not promise a decision.",
    }


@tool("update_draft", "Save details for the pension application",
      "Save or correct one or more details of the application draft (only what the citizen has actually told "
      "you). Send EVERY detail already mentioned in the conversation in one call, including the applicant's own "
      "name even if it came up in passing (\"my grandmother Kamala Bora\": the name is the APPLICANT's, not the "
      "helper's). Returns what is still missing, any problem with what was given, and what to ask next. Safe to "
      "call repeatedly; later calls change only the fields you send.",
      properties={
          "applicant_name": {"type": "string", "description": "Full name exactly as on the age proof", "maxLength": 120},
          "dob": {"type": "string", "description": "Date of birth DD/MM/YYYY", "maxLength": 20},
          "gender": {"type": "string", "enum": list(portal.GENDERS)},
          "marital_status": {"type": "string", "enum": list(portal.MARITAL)},
          "husband_name": {"type": "string", "description": "Late husband's name (only if widowed; optional)", "maxLength": 120},
          "village": {"type": "string", "description": "Village or town", "maxLength": 120},
          "block": {"type": "string", "enum": list(portal.BLOCKS)},
          "bank_account": {"type": "string", "description": "Bank account number, digits only", "maxLength": 40},
          "ifsc": {"type": "string", "description": "Bank IFSC, 11 characters e.g. SBIN0003077", "maxLength": 20}},
      idempotent=True)
def update_draft(claims, args):
    mobile = _mobile(claims)
    if not args:
        raise ToolError("send at least one detail to save")
    with db() as (conn, cur):
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("ssp-draft:" + mobile,))
        data, doc = drafts.load(cur, mobile)
        live = portal.active_application(cur, mobile)
        if live:
            raise ToolError("This mobile number already has application %s (%s). A second application cannot be "
                            "made. Use get_my_application to see it." % (live[1], live[2]),
                            existing_application_no=live[1])
        for k, v in args.items():
            v = (v or "").strip()
            if k == "dob":
                v = _norm_dob(v)
                if data.get("dob") and data["dob"] != v:           # keep a trace: a birth date changed mid-draft
                    data["_dob_history"] = (data.get("_dob_history") or []) + [data["dob"]]
            elif k == "ifsc":
                v = re.sub(r"[\s-]", "", v).upper()
            elif k == "bank_account":
                v = re.sub(r"[\s-]", "", v)
            elif k in ("block", "gender", "marital_status"):
                allowed = {"block": portal.BLOCKS, "gender": portal.GENDERS, "marital_status": portal.MARITAL}[k]
                v = next((b for b in allowed if b.lower() == v.lower()), v)
            data[k] = v
        drafts.save(cur, mobile, data, doc)
        conn.commit()
    ev = _evaluate(data, doc)
    out = dict(draft=_draft_view(data, doc), **ev, next_step=_next_hint(ev, data, doc))
    if not ev["missing"] and not ev["problems"] and not doc and ev["window_open"] \
            and ev.get("eligibility", {}).get("eligible_now", True):
        out["age_proof_upload"] = _upload_info(mobile)        # saves a round trip for turn-limited clients
    return out


def _upload_info(mobile):
    link = "%s/agent-upload/%s" % (ISSUER, drafts.make_upload_token(mobile))
    return {"upload_link": link, "valid_for_minutes": drafts.UPLOAD_LINK_TTL // 60,
            "say": "Ask her to open this link on her phone, choose the photo or PDF of her age proof and press Upload. "
                   "Then call preview_application to check that it arrived.",
            "from_a_shell_instead": "curl -F document=@/path/to/proof.jpg '%s'" % link}


@tool("get_age_proof_upload_link", "Get a link where the citizen can upload her age proof",
      "Returns a short-lived link (30 minutes). The citizen opens it on her phone, picks a photo or PDF of her age "
      "proof (birth certificate, school leaving certificate or Voter ID) and taps Upload. You never need to hold the "
      "file. Afterwards call preview_application to confirm it arrived.",
      read_only=True)
def get_age_proof_upload_link(claims, args):
    return _upload_info(_mobile(claims))


@tool("attach_age_proof", "Attach the age proof file directly",
      "Only if you already hold the file (for example the citizen shared a photo with you). Sends it as base64. "
      "JPG, PNG or PDF, up to 5 MB. Otherwise use get_age_proof_upload_link.",
      properties={"filename": {"type": "string", "description": "e.g. birth_certificate.jpg", "maxLength": 120},
                  "content_base64": {"type": "string", "description": "File content, base64", "maxLength": 7500000}},
      required=["filename", "content_base64"], idempotent=True)
def attach_age_proof(claims, args):
    mobile = _mobile(claims)
    try:
        content = base64.b64decode(args["content_base64"], validate=False)
    except Exception:
        raise ToolError("content_base64 is not valid base64")
    ok, msg, path = drafts.save_age_proof(mobile, args["filename"], content)
    if not ok:
        raise ToolError(msg)
    with db() as (conn, cur):
        data, _ = drafts.load(cur, mobile)
        drafts.save(cur, mobile, data, path)
        conn.commit()
    return {"attached": os.path.basename(path), "size_kb": len(content) // 1024,
            "note": "A human at the block office checks the document; this does not verify it."}


@tool("preview_application", "Preview the application before submitting",
      "Shows the complete application exactly as it would be submitted, whether it is ready, and the declaration. "
      "Read ALL of it back to the citizen and get a clear yes before submit_application. Returns a review_token "
      "that changes whenever any detail changes.", read_only=True, idempotent=True)
def preview_application(claims, args):
    mobile = _mobile(claims)
    with db() as (conn, cur):
        data, doc = drafts.load(cur, mobile)
        live = portal.active_application(cur, mobile)
    if live:
        raise ToolError("This mobile number already has application %s (%s)." % (live[1], live[2]),
                        existing_application_no=live[1])
    if not data:
        raise ToolError("There is no draft yet. Collect the details with update_draft first.")
    if doc and not os.path.isfile(doc):
        doc = ""
    errors, cleaned = portal.validate_application(data, doc)
    ev = _evaluate(data, doc)
    blockers = [e for e in errors] + (["age proof document is required"] if not doc and
                                      "age proof document is required" not in errors else [])
    ready = not blockers
    view = {
        "full_name": data.get("applicant_name"), "date_of_birth": data.get("dob"), "gender": data.get("gender"),
        "marital_status": data.get("marital_status"),
        "late_husbands_name": data.get("husband_name") or None,
        "village": data.get("village"), "block": data.get("block"),
        "bank_account": cleaned.get("bank_account") or data.get("bank_account"), "ifsc": cleaned.get("ifsc") or data.get("ifsc"),
        "age_proof_file": os.path.basename(doc) if doc else None,
    }
    out = {"application": view, "ready_to_submit": ready, "blocking_issues": sorted(set(blockers))}
    if ev.get("eligibility"):
        out["eligibility"] = ev["eligibility"]
    if ready:
        out["review_token"] = _review_token(mobile, data, doc)
        out["declaration"] = DECLARATION
        out["say"] = ("Read every line back to her, including the account number digit by digit, then read the "
                      "declaration. Submit only after she clearly agrees.")
    else:
        out["say"] = "Not ready yet: " + "; ".join(sorted(set(blockers)))
        if not doc and not ev["missing"] and ev["window_open"] and ev.get("eligibility", {}).get("eligible_now", True):
            out["age_proof_upload"] = _upload_info(mobile)       # give her the link right here
    return out


@tool("submit_application", "Submit the pension application",
      "Submits the application to the portal. Requires the review_token from preview_application (so what is "
      "submitted is exactly what the citizen heard) and declaration_accepted=true, which you set ONLY after she "
      "has clearly agreed to the read-back and the declaration. The portal applies its normal rules again.",
      properties={"review_token": {"type": "string", "maxLength": 64},
                  "declaration_accepted": {"type": "boolean",
                                           "description": "true only if the citizen clearly agreed to the declaration"}},
      required=["review_token", "declaration_accepted"], destructive=False)
def submit_application(claims, args):
    mobile = _mobile(claims)
    if args["declaration_accepted"] is not True:
        raise ToolError("The declaration has not been accepted, so nothing was submitted.")
    with db() as (conn, cur):
        data, doc = drafts.load(cur, mobile)
        if not data:
            raise ToolError("There is no draft to submit. If it was already submitted, use get_my_application.")
        if args["review_token"] != _review_token(mobile, data, doc):
            raise ToolError("The details changed after the preview. Call preview_application again, read it "
                            "back to the citizen, and get her yes again. Nothing was submitted.")
        if not doc or not os.path.isfile(doc):
            raise ToolError("The age proof file is missing. Ask her to upload it again.")
        res = portal.submit_application(cur, mobile, data, doc)
        if res["outcome"] == "created":
            earlier_young = [h for h in (data.get("_dob_history") or [])
                             if portal.parse_dob(h) and portal.age_on(portal.parse_dob(h), portal.now_ist().date()) < portal.MIN_AGE]
            if earlier_young:        # the portal cannot know the true date; it can make sure the officer sees this
                cur.execute("UPDATE audit_log SET note = %s WHERE application_id = %s AND action = 'SUBMIT'",
                            ("DOB_REVISED_AFTER_INELIGIBLE: draft first said %s (below 60), then changed" % ", ".join(earlier_young), res["id"]))
            cur.execute("DELETE FROM agent_drafts WHERE mobile = %s", (mobile,))   # keep the file: the application owns it
            conn.commit()
        else:
            conn.rollback()
    if res["outcome"] == "invalid":
        raise ToolError("The portal did not accept the application: " + "; ".join(res["errors"]),
                        problems=res["errors"])
    if res["outcome"] == "exists":
        no, status = res["existing"][1], res["existing"][2]
        raise ToolError("This mobile number already has application %s (%s), so nothing new was created. If this "
                        "is the application she just submitted, it is safely on file." % (no, status),
                        existing_application_no=no, existing_status=status)
    if res["outcome"] == "duplicate_person":
        raise ToolError("Our records already show a pension application for these details, so a second one cannot "
                        "be made. She can visit the block office if she thinks this is a mistake.")
    app_no = res["application_no"]
    portal.send_sms(mobile, "Sewa Setu: application %s received. Track at the status portal with mobile no. and "
                            "password (DOB as DDMMYYYY)." % app_no)
    sub = datetime.now()
    return {"submitted": True, "application_no": app_no, "status": "PENDING",
            "approved_by_law_on_or_about": (sub + timedelta(days=portal.SLA_DAYS)).strftime("%d/%m/%Y"),
            "sms": "A confirmation SMS is on its way to her mobile.",
            "track_on_website": "%s/status : mobile number, and password = date of birth as DDMMYYYY" % ISSUER,
            "say": "Tell her the application number slowly, that the block officer will decide within 15 days, and "
                   "that she must not share her password. Do not promise approval."}


@tool("withdraw_application", "Withdraw my pending application",
      "Withdraws this citizen's own PENDING application so that she can apply afresh. Ask her to confirm first. "
      "Only pending applications can be withdrawn.",
      properties={"application_no": {"type": "string", "maxLength": 20},
                  "confirm": {"type": "boolean", "description": "true only after the citizen clearly said yes"}},
      required=["application_no", "confirm"], destructive=True)
def withdraw_application(claims, args):
    mobile = _mobile(claims)
    if args["confirm"] is not True:
        raise ToolError("Not confirmed, so nothing was withdrawn. Ask the citizen to confirm.")
    with db() as (conn, cur):
        cur.execute("SELECT id, status FROM applications WHERE application_no = %s AND mobile = %s",
                    (args["application_no"].strip().upper(), mobile))
        row = cur.fetchone()
        if not row:
            raise ToolError("No application with that number was found for this mobile number.")
        if row[1] != "PENDING":
            raise ToolError("Only a pending application can be withdrawn; this one is %s." % row[1])
        no = portal.withdraw_pending(cur, row[0], mobile)
        conn.commit()
    return {"withdrawn": no, "say": "It is withdrawn. She may now apply again if she wishes."}


@tool("clear_my_draft", "Erase my saved details",
      "Deletes the saved draft and any uploaded age proof that has not been submitted. Call it when the citizen is "
      "done or leaves on a shared phone or kiosk, or asks to start again.", destructive=True, idempotent=True)
def clear_my_draft(claims, args):
    mobile = _mobile(claims)
    with db() as (conn, cur):
        drafts.discard(cur, mobile)
        conn.commit()
    return {"cleared": True, "say": "Saved details erased. Remind her to sign out of this assistant."}


TOOLS = [get_scheme_info, get_my_application, update_draft, get_age_proof_upload_link, attach_age_proof,
         preview_application, submit_application, withdraw_application, clear_my_draft]

try:
    drafts.ensure_tables()
except Exception as e:                                   # DB not up yet; first request retries
    import logging
    logging.getLogger("sewasetu.mcp").warning("ensure_tables deferred: %s", e)

app = make_server(role="citizen", path=PATH, canonical=CANONICAL, server_name="Sewa Setu - citizen assistant",
                  instructions=INSTRUCTIONS, tools=TOOLS)
