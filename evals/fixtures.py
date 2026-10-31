# -*- coding: utf-8 -*-
"""Fixtures are created THROUGH the portal's own door (the citizen MCP server) wherever possible, so the data
the officer-side evals see is exactly what a real assistant-made application looks like."""
import io
import base64
from PIL import Image, ImageDraw, ImageFont

from lib import get_token, Mcp
import db

CIT = None  # set by the runner


def png_bytes(name="Sample Person", dob="01/01/1950"):
    img = Image.new("RGB", (640, 420), "#fbf7ea")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 28)
        g = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 22)
    except Exception:
        f = g = ImageFont.load_default()
    d.rectangle([10, 10, 630, 410], outline="#7a5c2e", width=4)
    d.text((70, 40), "BIRTH CERTIFICATE", fill="#3a2a10", font=f)
    d.text((40, 120), "Name:  %s" % name, fill="black", font=g)
    d.text((40, 170), "Date of birth:  %s" % dob, fill="black", font=g)
    d.text((40, 260), "Registrar of Births and Deaths, Purvanchal", fill="#555", font=g)
    b = io.BytesIO(); img.save(b, "PNG")
    return b.getvalue()


def make_application(portal, mobile, name, dob, gender="Female", marital="Widowed", village="Lakhipur", block="Sonari",
                     bank="111122223333", ifsc="SBIN0003077", status_to=None, doc_text=None):
    """Create an application via the citizen MCP server with a scripted client; optionally move it to a
    decided status afterwards (as an officer would)."""
    cit = portal.replace(":8000", ":8001") + "/citizen/mcp" if ":8000" in portal else portal + "/citizen/mcp"
    cit = os_env("CITIZEN_MCP_URL", cit)
    m = Mcp(cit, get_token(portal, "citizen", cit, mobile=mobile)); m.init()
    s, e, _ = m.call("update_draft", applicant_name=name, dob=dob, gender=gender, marital_status=marital, village=village,
                     block=block, bank_account=bank, ifsc=ifsc)
    if e:
        raise RuntimeError("fixture refused: %s" % s)
    img = png_bytes(*(doc_text or (name, dob)))
    s, e, _ = m.call("attach_age_proof", filename="certificate.png", content_base64=base64.b64encode(img).decode())
    if e:
        raise RuntimeError(s)
    s, e, _ = m.call("preview_application")
    s, e, _ = m.call("submit_application", review_token=s["review_token"], declaration_accepted=True)
    if e:
        raise RuntimeError("fixture refused: %s" % s)
    no = s["application_no"]
    if status_to:
        db.run("UPDATE applications SET status='%s', decided_at=now(), decided_by='admin' WHERE application_no='%s'" % (status_to, no))
    return no


def os_env(k, default):
    import os
    return os.environ.get(k) or default
