# deploy/

Deployment infrastructure, kept separate from application source.

    docker/   Dockerfiles, and docker-compose files if needed
    nginx/    reverse-proxy configuration

Rules:
- No secrets or real credentials in any file here. Inject via environment or a secret manager.
- Production configuration templates may live here; real values do not.

Status: empty placeholders.
