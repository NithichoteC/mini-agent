# backend/

API layer between the frontend and the core (`agentic_ai`): FastAPI, `backend/app.py`, started with
`python main.py serve`. One chat message is one `run_workflow` call in a worker thread; its events go to the
browser as Server-Sent Events, approvals block the worker until the browser answers. Sessions, trace and
transcripts are read from the trace database; settings are written back into the yaml files.

Rules: backend imports `agentic_ai`; `agentic_ai` never imports backend. HTTP, upload and session
concerns stay here.
