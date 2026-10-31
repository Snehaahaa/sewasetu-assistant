# -*- coding: utf-8 -*-
"""Draft application store + age-proof file handling shared by the portal and the citizen MCP server.

A draft is what the web form keeps in the browser session, but kept in the database so it survives
the assistant losing its place - and it is bound to the OTP-verified mobile, expires in 24 hours,
and can be wiped by the citizen (shared phones and kiosks).
"""
import os
import re
import json
from datetime import datetime, timedelta

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import app as portal

DRAFT_TTL = timedelta(hours=24)
UPLOAD_LINK_TTL = 30 * 60
MAX_BYTES = 5 * 1024 * 1024
ALLOWED_EXT = ("pdf", "jpg", "jpeg", "png")
MAGIC = {"pdf": (b"%PDF",), "jpg": (b"\xff\xd8\xff",), "jpeg": (b"\xff\xd8\xff",), "png": (b"\x89PNG\r\n\x1a\n",)}

DDL = """CREATE TABLE IF NOT EXISTS agent_drafts (
    mobile VARCHAR(15) PRIMARY KEY, data JSONB, doc_path VARCHAR(200), updated_at TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_app_bank ON applications(bank_account);
CREATE INDEX IF NOT EXISTS idx_app_dob ON applications(dob);"""


def ensure_tables():
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        for stmt in [s for s in DDL.split(";") if s.strip()]:
            cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def load(cur, mobile):
    """(data dict, doc_path) for this mobile; expired drafts are dropped."""
    cur.execute("SELECT data, doc_path, updated_at FROM agent_drafts WHERE mobile = %s", (mobile,))
    row = cur.fetchone()
    if not row:
        return {}, ""
    if datetime.now() - row[2] > DRAFT_TTL:
        discard(cur, mobile)
        return {}, ""
    data = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
    return data, row[1] or ""


def save(cur, mobile, data, doc_path):
    cur.execute("INSERT INTO agent_drafts (mobile, data, doc_path, updated_at) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (mobile) DO UPDATE SET data = EXCLUDED.data, doc_path = EXCLUDED.doc_path, "
                "updated_at = EXCLUDED.updated_at",
                (mobile, json.dumps(data, ensure_ascii=False), doc_path, datetime.now()))


def discard(cur, mobile):
    """Delete the draft and any age-proof file that was uploaded but never submitted."""
    cur.execute("SELECT doc_path FROM agent_drafts WHERE mobile = %s", (mobile,))
    row = cur.fetchone()
    cur.execute("DELETE FROM agent_drafts WHERE mobile = %s", (mobile,))
    if row and row[0]:
        cur.execute("SELECT 1 FROM applications WHERE doc_path = %s LIMIT 1", (row[0],))
        if not cur.fetchone():
            _remove_inside_uploads(row[0])


def _remove_inside_uploads(path):
    root = os.path.realpath(portal.UPLOAD_DIR)
    p = os.path.realpath(path)
    if p.startswith(root + os.sep) and os.path.isfile(p):
        os.remove(p)


def save_age_proof(mobile, filename, content):
    """Validate and store an age-proof file. Returns (ok, message, path). Same limits as the
    web upload (JPG/PNG/PDF, 5 MB), plus: the bytes must really be that file type, and the name
    is reduced to a safe basename (no path tricks)."""
    base = os.path.basename((filename or "").replace("\\", "/")).lower()
    ext = base.rsplit(".", 1)[-1] if "." in base else ""
    if ext not in ALLOWED_EXT:
        return False, "Please use a JPG, PNG or PDF file.", ""
    if not content:
        return False, "The file is empty.", ""
    if len(content) > MAX_BYTES:
        return False, "The file is larger than 5 MB. Please send a smaller photo or scan.", ""
    if not any(content.startswith(m) for m in MAGIC[ext]):
        return False, "This does not look like a real %s file." % ext.upper(), ""
    safe = re.sub(r"[^a-z0-9._-]", "_", base)[-80:] or ("proof." + ext)
    os.makedirs(portal.UPLOAD_DIR, exist_ok=True)
    path = os.path.join(portal.UPLOAD_DIR, "%s_%s" % (mobile, safe))
    with open(path, "wb") as fh:
        fh.write(content)
    return True, "ok", path


# one-time-ish upload links: the citizen taps the link on the phone and picks a photo; the assistant
# never has to hold the file.
def _ser():
    return URLSafeTimedSerializer(portal.app.secret_key, salt="agent-upload")


def make_upload_token(mobile):
    return _ser().dumps({"m": mobile})


def read_upload_token(token):
    try:
        return _ser().loads(token, max_age=UPLOAD_LINK_TTL)["m"]
    except (BadSignature, SignatureExpired, KeyError):
        return None
