# -*- coding: utf-8 -*-
"""Shared core of the two Sewa Setu MCP servers (MCP streamable HTTP, stateless JSON mode).

Each server is its own WSGI app on its own path, with its own tool list. A server is an OAuth
*resource server* for the portal's authorization server: it advertises Protected Resource Metadata
(RFC 9728), answers 401 with WWW-Authenticate, and accepts only RS256 tokens that are
  - issued by the portal (iss),
  - minted for THIS server's canonical URL (aud, RFC 8707) - a token for the citizen endpoint is
    useless at the officer endpoint and vice versa,
  - carrying the matching role and scope,
  - belonging to a sign-in that is still live (not signed out from the status portal / admin
    dashboard, not past its absolute session end).
The portal stays the system of record: every write goes through the portal's own functions.
"""
import os
import json
import time
import logging
from contextlib import contextmanager
from datetime import datetime

import jwt
from flask import Flask, request, jsonify, make_response

import app as portal
from oauth import ISSUER

log = logging.getLogger("sewasetu.mcp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PORTAL_INTERNAL = os.environ.get("PORTAL_INTERNAL_URL", "http://app:8000").rstrip("/")
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
_JWKS = jwt.PyJWKClient(PORTAL_INTERNAL + "/.well-known/jwks.json", cache_keys=True, lifespan=600)


class ToolError(Exception):
    """A problem the assistant can read and act on (shown with isError=true)."""

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class AuthError(Exception):
    def __init__(self, status, error, desc):
        super().__init__(desc)
        self.status, self.error, self.desc = status, error, desc


@contextmanager
def db():
    conn = portal.get_db()
    try:
        cur = conn.cursor()
        yield conn, cur
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _aud_variants(url):
    return [url, url + "/"] if not url.endswith("/") else [url, url.rstrip("/")]


def authenticate(role, canonical):
    h = request.headers.get("Authorization", "")
    if not h.lower().startswith("bearer ") or not h[7:].strip():
        raise AuthError(401, "invalid_token", "sign in is required")
    tok = h[7:].strip()
    try:
        key = _JWKS.get_signing_key_from_jwt(tok).key
        claims = jwt.decode(tok, key, algorithms=["RS256"], audience=_aud_variants(canonical),
                            issuer=ISSUER, options={"require": ["exp", "iat", "iss", "sub", "aud"]})
    except jwt.ExpiredSignatureError:
        raise AuthError(401, "invalid_token", "the sign-in has expired; sign in again")
    except Exception:
        # wrong audience, wrong issuer, bad signature, garbage: all look the same to the caller
        raise AuthError(401, "invalid_token", "token is not valid for this service")
    if claims.get("role") != role or claims.get("scope") != role or \
            not str(claims.get("sub", "")).startswith(role + ":"):
        raise AuthError(403, "insufficient_scope", "this sign-in is not allowed to use this service")
    _check_live(claims)
    return claims


def _check_live(claims):
    """Access tokens are JWTs and cannot be recalled, but a sign-out (status portal 'Connected
    applications', admin dashboard, /oauth/revoke) must end a kiosk session at once."""
    with db() as (conn, cur):
        if claims.get("kind") == "program":
            cur.execute("SELECT revoked_at, expires_at FROM oauth_program_tokens WHERE jti = %s",
                        (claims.get("jti"),))
            row = cur.fetchone()
            if not row or row[0] is not None or row[1] <= datetime.now():
                raise AuthError(401, "invalid_token", "this program token has been revoked or has expired")
            return
        cur.execute("SELECT 1 FROM oauth_refresh_tokens WHERE subject = %s AND client_id = %s "
                    "AND resource = %s AND revoked_at IS NULL AND expires_at > %s LIMIT 1",
                    (claims["sub"], claims.get("client_id"), claims["aud"], datetime.now()))
        if not cur.fetchone():
            raise AuthError(401, "invalid_token", "this sign-in has ended; sign in again")


# ---------------------------------------------------------------------------
# tiny JSON-Schema checker (enough for our flat tool inputs; gives plain-language errors)
# ---------------------------------------------------------------------------

def check_args(schema, args):
    props = schema.get("properties", {})
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    extra = [k for k in args if k not in props]
    if extra:
        raise ToolError("unknown argument(s): %s. Allowed: %s" % (", ".join(extra), ", ".join(props) or "none"))
    for k in schema.get("required", []):
        if args.get(k) in (None, ""):
            raise ToolError("missing required argument: %s" % k)
    for k, v in args.items():
        spec = props[k]
        t = spec.get("type")
        if v is None:
            continue
        if t == "string" and not isinstance(v, str):
            raise ToolError("%s must be text" % k)
        if t == "boolean" and not isinstance(v, bool):
            raise ToolError("%s must be true or false" % k)
        if t == "integer" and (not isinstance(v, int) or isinstance(v, bool)):
            raise ToolError("%s must be a whole number" % k)
        if t == "array" and not isinstance(v, list):
            raise ToolError("%s must be a list" % k)
        if "enum" in spec and not (v in spec["enum"] or (isinstance(v, str) and
                                   v.strip().lower() in [str(e).lower() for e in spec["enum"]])):
            raise ToolError("%s must be one of: %s" % (k, ", ".join(map(str, spec["enum"]))))
        if isinstance(v, str) and len(v) > spec.get("maxLength", 2000):
            raise ToolError("%s is too long" % k)


def tool(name, title, description, properties=None, required=None, read_only=False,
         destructive=False, idempotent=False):
    def deco(fn):
        fn.spec = {
            "name": name, "title": title, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": required or [], "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive,
                            "idempotentHint": idempotent, "openWorldHint": False},
        }
        return fn
    return deco


def make_server(*, role, path, canonical, server_name, instructions, tools):
    """Build a WSGI app serving one MCP endpoint at `path`."""
    flask_app = Flask(server_name)
    registry = {t.spec["name"]: t for t in tools}
    prm_url = ISSUER_FOR(canonical) + "/.well-known/oauth-protected-resource" + path

    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Mcp-Protocol-Version, Mcp-Session-Id, Accept"
        resp.headers["Access-Control-Allow-Methods"] = "POST, GET, DELETE, OPTIONS"
        resp.headers["Access-Control-Expose-Headers"] = "WWW-Authenticate, Mcp-Session-Id"
        return resp

    flask_app.after_request(cors)

    @flask_app.get("/.well-known/oauth-protected-resource" + path)
    def prm():
        return jsonify({
            "resource": canonical,
            "authorization_servers": [ISSUER],
            "scopes_supported": [role],
            "bearer_methods_supported": ["header"],
            "resource_name": server_name,
            "resource_documentation": ISSUER + "/about",
        })

    @flask_app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "server": server_name, "tools": len(registry)})

    def unauthorized(e):
        challenge = 'Bearer resource_metadata="%s", scope="%s"' % (prm_url, role)
        if e.status == 401:
            challenge += ', error="%s"' % e.error
        else:
            challenge += ', error="insufficient_scope"'
        r = make_response(jsonify({"error": e.error, "error_description": e.desc}), e.status)
        r.headers["WWW-Authenticate"] = challenge
        return r

    def rpc_error(rid, code, msg, status=200):
        return make_response(jsonify({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}), status)

    @flask_app.route(path, methods=["POST", "GET", "DELETE", "OPTIONS"])
    def endpoint():
        if request.method == "OPTIONS":
            return make_response("", 204)
        try:
            claims = authenticate(role, canonical)
        except AuthError as e:
            return unauthorized(e)
        if request.method != "POST":
            r = make_response("", 405)           # stateless server: no SSE stream, no sessions
            r.headers["Allow"] = "POST"
            return r
        msg = request.get_json(silent=True)
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return rpc_error(None, -32600, "invalid JSON-RPC request", 400)
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:                       # a response from the client: nothing to do
            return make_response("", 202)
        if rid is None:                          # notification
            return make_response("", 202)
        if method == "initialize":
            want = params.get("protocolVersion")
            ver = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": server_name, "version": "1.0.0"},
                "instructions": instructions}})
        if method == "ping":
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {}})
        if method == "tools/list":
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {"tools": [t.spec for t in tools]}})
        if method == "tools/call":
            name = params.get("name")
            fn = registry.get(name)
            if not fn:
                return rpc_error(rid, -32602, "unknown tool: %s" % name)
            t0 = time.time()
            try:
                args = params.get("arguments") or {}
                check_args(fn.spec["inputSchema"], args)
                out = fn(claims, args)
                if isinstance(out, tuple):       # (structured, extra_content_blocks)
                    structured, extra = out
                else:
                    structured, extra = out, []
                content = [{"type": "text", "text": json.dumps(structured, ensure_ascii=False, indent=2)}] + extra
                result = {"content": content, "structuredContent": structured, "isError": False}
            except ToolError as e:
                body = dict(e.extra, error=str(e))
                result = {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False, indent=2)}],
                          "structuredContent": body, "isError": True}
            except Exception:
                log.exception("tool %s crashed", name)
                body = {"error": "Something went wrong on the portal's side. Nothing was changed. "
                                 "Please try again, or the citizen can use the counter or the website."}
                result = {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body,
                          "isError": True}
            log.info("tool=%s sub=%s ok=%s ms=%d", name, claims["sub"].split(":")[0] + ":***",
                     not result["isError"], (time.time() - t0) * 1000)
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": result})
        return rpc_error(rid, -32601, "method not found: %s" % method)

    return flask_app


def ISSUER_FOR(canonical):
    """Scheme+host of an MCP canonical URL (where its well-known documents live)."""
    from urllib.parse import urlparse
    u = urlparse(canonical)
    return "%s://%s" % (u.scheme, u.netloc)
