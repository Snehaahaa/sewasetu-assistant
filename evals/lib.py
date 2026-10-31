# -*- coding: utf-8 -*-
"""Helpers for the evals: sign in the way a citizen / officer really does, then talk MCP.

get_token() drives the portal's real OAuth 2.1 flow (dynamic client registration, PKCE S256, resource
indicator = the MCP server's URL, mobile + OTP read from the SMS gateway for a citizen, departmental login
for an officer, consent). Nothing here bypasses the portal."""
import re
import time
import json
import base64
import hashlib
import secrets
from urllib.parse import urlparse, parse_qs

import requests

REDIRECT = "http://localhost:8123/callback"


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def read_otp(portal, mobile, after_id=0, wait=15):
    end = time.time() + wait
    while time.time() < end:
        msgs = requests.get(portal + "/__gateway/api/messages", params={"to": mobile}, timeout=10).json()["messages"]
        for m in reversed(msgs):
            if m["id"] > after_id and m["delivered"] and m["text"] and "OTP" in m["text"]:
                return re.search(r"(\d{6})", m["text"]).group(1)
        time.sleep(0.5)
    raise RuntimeError("OTP never arrived for " + mobile)


def last_msg_id(portal, mobile):
    msgs = requests.get(portal + "/__gateway/api/messages", params={"to": mobile}, timeout=10).json()["messages"]
    return max([m["id"] for m in msgs] or [0])


def get_token(portal, role, resource, mobile=None, username="admin", password=None, client_name="eval-harness",
              return_refresh=False):
    s = requests.Session()
    reg = s.post(portal + "/oauth/register", json={"client_name": client_name, "redirect_uris": [REDIRECT],
                                                    "token_endpoint_auth_method": "none"}).json()
    cid = reg["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    r = s.get(portal + "/oauth/authorize", params={
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "scope": role, "state": "xyz",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": resource}, allow_redirects=False)
    login_url = r.headers["Location"]
    if not login_url.startswith("http"):
        login_url = portal + login_url
    rid = parse_qs(urlparse(login_url).query)["rid"][0]
    if role == "citizen":
        before = last_msg_id(portal, mobile)
        s.post(login_url, data={"mobile": mobile})
        otp = read_otp(portal, mobile, before)
        r = s.post(login_url, data={"otp": otp}, allow_redirects=False)
    else:
        r = s.post(login_url, data={"username": username, "password": password}, allow_redirects=False)
    consent = r.headers["Location"]
    if not consent.startswith("http"):
        consent = portal + consent
    r = s.post(consent, data={"decision": "allow"}, allow_redirects=False)
    code = parse_qs(urlparse(r.headers["Location"]).query)["code"][0]
    tok = s.post(portal + "/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid, "redirect_uri": REDIRECT,
        "code_verifier": verifier, "resource": resource}).json()
    if "access_token" not in tok:
        raise RuntimeError("token exchange failed: %s" % tok)
    return tok if return_refresh else tok["access_token"]


class Mcp:
    """Minimal streamable-HTTP MCP client (JSON responses)."""

    def __init__(self, url, token):
        self.url, self.h, self.n = url, {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                         "Accept": "application/json, text/event-stream"}, 0

    def rpc(self, method, params=None, notify=False):
        self.n += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            body["id"] = self.n
        r = requests.post(self.url, headers=self.h, json=body, timeout=60)
        return r

    def init(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "eval", "version": "1"}})
        self.rpc("notifications/initialized", notify=True)
        return r.json()["result"]

    def tools(self):
        return self.rpc("tools/list").json()["result"]["tools"]

    def call(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        res = r.json()["result"]
        return res["structuredContent"], res["isError"], res
