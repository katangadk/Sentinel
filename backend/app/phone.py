"""Production-authenticated temporary phone gateway over the existing database.

The local agent/API remain private. A tunnel terminates public TLS and forwards
only to this loopback listener, which trusts forwarded headers from loopback.
No additional push consumer is started; the existing backend owns the outbox.
"""
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .config import Settings
from .main import create_app


def create_phone_app(settings=None, *, upstream=None, proxy_transport=None):
    settings = settings or Settings()
    # Force all public browser security controls even when .env is local dev.
    settings.development = False
    settings.validate()
    upstream = upstream or os.getenv("SENTINEL_FRONTEND_UPSTREAM", "http://127.0.0.1:3100")
    parsed = urlsplit(upstream)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("Phone gateway frontend upstream must be a loopback HTTP origin")
    app = create_app(settings, start_worker=False)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(current):
        async with original_lifespan(current):
            async with httpx.AsyncClient(base_url=upstream, timeout=20, follow_redirects=False,
                                         transport=proxy_transport, trust_env=False) as client:
                current.state.frontend_client = client
                yield

    app.router.lifespan_context = lifespan

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def frontend(path: str, request: Request):
        # Never expose the development backend's docs through Next's API rewrite.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not found")
        headers = {name: value for name, value in request.headers.items()
                   if name.lower() in {"accept", "accept-encoding", "if-none-match", "if-modified-since", "rsc", "next-router-state-tree", "next-router-prefetch", "next-url"}}
        client = request.app.state.frontend_client
        target = httpx.URL(upstream).copy_with(raw_path=request.scope["raw_path"])
        target = target.copy_with(query=request.scope["query_string"])
        try:
            response = await client.send(client.build_request(request.method, target, headers=headers), stream=True)
        except httpx.HTTPError:
            raise HTTPException(502, "Dashboard server is unavailable") from None
        outgoing = {name: value for name, value in response.headers.items()
                    if name.lower() not in {"connection", "transfer-encoding", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade"}}
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                 headers=outgoing, background=BackgroundTask(response.aclose))

    return app
