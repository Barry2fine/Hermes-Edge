"""Pure request handler for review and mock tests. No socket/server entry point."""
import hmac
import json
from desk_review import dispatch


def handle(method, path, headers, *, expected_token, snapshot):
    # A future host must supply its own secret. No real token is created or stored.
    base_headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    if method != "GET":
        return 405, base_headers, {"ok": False, "error": "METHOD_NOT_ALLOWED"}
    if path != "/api/chatgpt-brief":
        return 404, base_headers, {"ok": False, "error": "NOT_FOUND"}
    supplied = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    if not isinstance(expected_token, str) or len(expected_token) < 32:
        return 503, base_headers, {"ok": False, "error": "AUTH_NOT_CONFIGURED"}
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), ("Bearer " + expected_token).encode()):
        return 401, base_headers, {"ok": False, "error": "UNAUTHORIZED"}
    result = dispatch("status", snapshot=snapshot)
    return (200 if result["ok"] else 503), base_headers, result


OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "Isolated read-only desk review (not deployed)", "version": "1.0.0"},
    "security": [{"bearerAuth": []}],
    "paths": {"/api/chatgpt-brief": {"get": {
        "operationId": "readSnapshotDiagnostics",
        "summary": "Review local snapshot freshness and section health; no exchange verification",
        "responses": {str(code): {"description": label, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReviewResult"}}}}
                      for code, label in [(200, "Snapshot sections present"), (401, "Authentication required"), (503, "Stale, unavailable, incomplete or authentication not configured")]},
    }}},
    "components": {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        "schemas": {"ReviewResult": {
            "type": "object", "required": ["ok"], "additionalProperties": False,
            "properties": {
                "ok": {"type": "boolean"}, "error": {"type": "string"},
                "execution_enabled": {"const": False}, "exchange_verified": {"const": False},
                "mode": {"const": "read_only_snapshot_review"},
                "source_age_seconds": {"type": "number", "minimum": 0},
                "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "sections": {"type": "object", "additionalProperties": {"enum": ["missing", "source_error", "present_unverified"]}},
            },
        }},
    },
}
