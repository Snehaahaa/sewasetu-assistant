"""Independent protocol check: talk to both servers with the OFFICIAL MCP Python SDK client (not our own helper)."""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from lib import get_token

P = os.environ.get("PORTAL", "http://localhost:8000")
CIT = os.environ.get("CIT", "http://localhost:8001/citizen/mcp")
OFF = os.environ.get("OFF", "http://localhost:8002/officer/mcp")


async def check(url, role, **cred):
    tok = get_token(P, role, url, **cred)
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + tok}) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as s:
                init = await s.initialize()
                tools = await s.list_tools()
                names = [t.name for t in tools.tools]
                call = "get_scheme_info" if role == "citizen" else "queue_summary"
                res = await s.call_tool(call, {})
                print("%-8s server=%r protocol=%s tools=%d call(%s) isError=%s" % (
                    role, init.server_info.name, init.protocol_version, len(names), call, res.is_error))
                assert not res.is_error and names


asyncio.run(check(CIT, "citizen", mobile="9900100060"))
asyncio.run(check(OFF, "officer", password=os.environ.get("ADMIN_PASSWORD", "officer-pass-1")))
print("official SDK client: OK")
