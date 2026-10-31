"""DEV-ONLY shim: a tiny psycopg2 stand-in that talks to Postgres through the `psql` CLI.

Used only in sandboxes where psycopg2 cannot be installed. Production (the Docker image)
uses the real psycopg2-binary from requirements.txt. Never put this directory on the
production PYTHONPATH.
"""
import json, os, re, subprocess, threading, queue, itertools, datetime as _dt

class Error(Exception): pass
class DatabaseError(Error): pass
class OperationalError(DatabaseError): pass
class IntegrityError(DatabaseError): pass

_mark = itertools.count(1)
_ISO_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?$")
_ISO_D = re.compile(r"^\d{4}-\d\d-\d\d$")

def _lit(v):
    if v is None: return "NULL"
    if isinstance(v, bool): return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)): return repr(v)
    if isinstance(v, _dt.datetime): return "'%s'::timestamp" % v.isoformat(sep=" ")
    if isinstance(v, _dt.date): return "'%s'::date" % v.isoformat()
    if isinstance(v, (tuple, list)): return "(" + ",".join(_lit(x) for x in v) + ")"
    s = str(v)
    if "\x00" in s: raise Error("NUL in string")
    return "'" + s.replace("'", "''") + "'"

def _conv(x):
    if isinstance(x, str):
        if _ISO_TS.match(x): return _dt.datetime.fromisoformat(x)
        if _ISO_D.match(x): return _dt.date.fromisoformat(x)
    elif isinstance(x, list): return [_conv(i) for i in x]
    elif isinstance(x, dict): return {k: _conv(v) if not isinstance(v, (dict, list)) else v for k, v in x.items()}
    return x

class _Cur:
    def __init__(self, conn): self.c = conn; self.rows = []; self.rowcount = -1; self.pos = 0
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def close(self): pass
    def execute(self, sql, params=None):
        if params is not None:
            parts = re.split(r"(%s|%%)", sql); out = []; it = iter(params if not isinstance(params, dict) else [])
            for p in parts:
                if p == "%s": out.append(_lit(next(it)))
                elif p == "%%": out.append("%")
                else: out.append(p)
            sql = "".join(out)
        s = sql.strip().rstrip(";")
        head = s.split(None, 1)[0].upper() if s else ""
        self.rows, self.pos = [], 0
        if head in ("SELECT", "WITH", "VALUES"):
            txt = self.c._run("WITH q AS (%s) SELECT coalesce(json_agg(row_to_json(q)), '[]'::json) FROM q;" % s)
            self._load(txt)
        elif head in ("INSERT", "UPDATE", "DELETE"):
            if " RETURNING " in s.upper():
                txt = self.c._run("WITH q AS (%s) SELECT coalesce(json_agg(row_to_json(q)), '[]'::json) FROM q;" % s)
                self._load(txt)
            else:
                txt = self.c._run("WITH q AS (%s RETURNING 1) SELECT count(*) FROM q;" % s)
                self.rowcount = int(txt.strip())
        else:
            self.c._run(s + ";"); self.rowcount = -1
    def _load(self, txt):
        data = json.loads(txt, object_pairs_hook=lambda p: p)
        self.rows = [tuple(_conv(v) if not (isinstance(v, list) and v and isinstance(v[0], tuple)) else dict(v) for _, v in r) for r in data]
        # jsonb columns arrive as dict-pairs lists; normalise
        self.rows = [tuple(self._norm(v) for v in r) for r in self.rows]
        self.rowcount = len(self.rows)
    def _norm(self, v):
        if isinstance(v, list) and v and all(isinstance(i, tuple) and len(i) == 2 and isinstance(i[0], str) for i in v):
            return {k: self._norm(x) for k, x in v}
        if isinstance(v, list): return [self._norm(i) for i in v]
        return _conv(v)
    def fetchone(self):
        if self.pos < len(self.rows): self.pos += 1; return self.rows[self.pos - 1]
        return None
    def fetchall(self):
        r = self.rows[self.pos:]; self.pos = len(self.rows); return r
    def fetchmany(self, n=1):
        r = self.rows[self.pos:self.pos + n]; self.pos += len(r); return r

class _Conn:
    def __init__(self, host, port, dbname, user, password):
        env = dict(os.environ, PGPASSWORD=str(password or ""))
        self.p = subprocess.Popen(["psql", "-X", "-q", "-A", "-t", "-h", str(host), "-p", str(port), "-U", user, "-d", dbname],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        self.q_out, self.q_err = queue.Queue(), queue.Queue()
        for stream, q in ((self.p.stdout, self.q_out), (self.p.stderr, self.q_err)):
            threading.Thread(target=self._pump, args=(stream, q), daemon=True).start()
        self.autocommit = False
        self._run("BEGIN;")
    @staticmethod
    def _pump(stream, q):
        for line in stream: q.put(line.rstrip("\n"))
        q.put(None)
    def _run(self, sql):
        m = "@@END%d@@" % next(_mark)
        self.p.stdin.write(sql + "\n\\echo %s\n\\warn %s\n" % (m, m)); self.p.stdin.flush()
        out, err = [], []
        for q, buf in ((self.q_out, out), (self.q_err, err)):
            while True:
                line = q.get()
                if line is None: raise OperationalError("psql exited")
                if line == m: break
                buf.append(line)
        if err:
            msg = "\n".join(err)
            if "violates unique constraint" in msg: raise IntegrityError(msg)
            raise DatabaseError(msg)
        return "\n".join(out)
    def cursor(self): return _Cur(self)
    def commit(self): self._run("COMMIT; BEGIN;")
    def rollback(self): self._run("ROLLBACK; BEGIN;")
    def close(self):
        try: self.p.stdin.write("\\q\n"); self.p.stdin.close(); self.p.wait(5)
        except Exception: self.p.kill()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

def connect(host="localhost", port=5432, dbname=None, user=None, password=None, **kw):
    return _Conn(host, port, dbname, user, password)
