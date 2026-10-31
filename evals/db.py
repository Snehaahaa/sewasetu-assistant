"""Read/prepare the portal's Postgres for the evals (the evals judge the portal's own database)."""
import os, subprocess, json

HOST = os.environ.get("EVAL_DB_HOST", "/tmp")
PORT = os.environ.get("EVAL_DB_PORT", "5433")
USER, NAME, PW = "sewasetu", "sewasetu", os.environ.get("EVAL_DB_PASSWORD", "sewasetu123")


def _psql(sql):
    if os.environ.get("EVAL_DB_DOCKER"):    # on the VM: docker compose exec -T db psql ...
        cmd = ["docker", "compose", "exec", "-T", "db", "psql", "-U", USER, "-d", NAME, "-X", "-At", "-F", "\x1f", "-c", sql]
    else:
        cmd = ["psql", "-X", "-At", "-F", "\x1f", "-h", HOST, "-p", PORT, "-U", USER, "-d", NAME, "-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PGPASSWORD=PW), timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout


def q(sql):
    """Rows as lists of strings."""
    return [line.split("\x1f") for line in _psql(sql).splitlines() if line != ""]


def one(sql):
    r = q(sql)
    return r[0][0] if r else None


def run(sql):
    _psql(sql)


def esc(s):
    return str(s).replace("'", "''")


def apps_for(mobile):
    return q("SELECT application_no,status,applicant_name,to_char(dob,'DD/MM/YYYY'),gender,marital_status,village,block,"
             "bank_account,ifsc,coalesce(doc_path,''),coalesce(decided_by,'') FROM applications WHERE mobile='%s' "
             "ORDER BY id" % esc(mobile))


def insert_application(mobile, name, dob, village="Lakhipur", block="Sonari", status="PENDING", bank="111122223333",
                       ifsc="SBIN0003077", doc="/tmp/ssp-uploads/fixture.png", submitted="now()", gender="Female",
                       marital="Widowed", decided_by=""):
    import random
    no = "SSX%08d" % random.randint(0, 99999999)          # own prefix: can never collide with a real SSP number
    run("INSERT INTO applications (application_no,applicant_name,mobile,dob,gender,marital_status,village,block,"
        "bank_account,ifsc,doc_path,status,submitted_at) VALUES ('%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s',%s)"
        % (no, esc(name), mobile, dob, gender, marital, esc(village), block, bank, ifsc, doc, status, submitted))
    if status != "PENDING":
        run("UPDATE applications SET decided_at=now(), decided_by='%s' WHERE application_no='%s'" % (decided_by or "admin", no))
    return no
