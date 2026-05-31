"""FastAPI routes for the MCP feature.

Stub — task 7 will implement:
  GET  /api/v1/admin/mcp-servers   (admin-only)
  POST /api/v1/tool-runs           (requires explicit engagement membership — §4)

Domain exceptions from service.py are translated to HTTP codes here via
the registered error handlers in app.core.errors.handlers.
"""

from fastapi import APIRouter

router = APIRouter(tags=["mcp"])
