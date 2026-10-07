# backend/

API / application-service layer. Mediates between external clients (frontend,
other applications) and the reusable core `agentic_ai`.

Future responsibilities: HTTP/API endpoints, authn/authz boundary, request
validation, session handling, calling the core, structured and streamed
responses, exposing traces/status where appropriate.

Rules:
- Backend imports `agentic_ai`; `agentic_ai` never imports backend.
- HTTP, auth, and session concerns stay here and do not leak into the core.

Status: empty. Framework (e.g. FastAPI or a lighter option) and package name are
**not yet decided**; no dependency has been added.
