# Railway deployment and recovery

## Deployment layout

- Workspace: **byxsss's Projects** (`d143b5d6-2692-4b83-bff3-9e5c1aecfbd4`).
- Project: `ai-workspace`; environment: `production`.
- Source: `byxsssn/ai-workspace`, branch `main`.
- Region: Singapore (`asia-southeast1-eqsg3a`) for both services.
- `ai-workspace`: one replica, 1 vCPU / 0.5 GB maximum, no sleeping, no persistent disk.
- `Postgres`: Railway's `postgres` template, image
  `ghcr.io/railwayapp-templates/postgres-ssl:18`, one replica, 1 vCPU / 0.5 GB maximum.
  A 5 GB volume mounts at `/var/lib/postgresql/data`; `PGDATA` is the template's
  `/var/lib/postgresql/data/pgdata`.
- Start with an empty database. Local accounts, messages and provider keys are not imported.

The Dockerfile builds React with Node 24 and installs locked production dependencies
with Python 3.14 and uv 0.12.5. It runs as an unprivileged user. Local configuration,
test artifacts and credentials are excluded from the build context.

The production ASGI factory is `ai_workspace.production:create_app`. It serves
`/`, `/assets/*` and the existing backend under `/api`. Unknown paths return 404;
there is no SPA catch-all for API paths. `/readyz` checks PostgreSQL within 2 seconds
and returns only a safe status. Production disables debug, OpenAPI and API docs.
Local development still uses `ai_workspace.main:app` and the Vite proxy.

## Service variables

Configure these on the application service, not in source or frontend variables:

| Variable | Value / purpose |
| --- | --- |
| `AI_WORKSPACE_ENVIRONMENT` | `production` |
| `AI_WORKSPACE_DEBUG` | `false` |
| `AI_WORKSPACE_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — private network reference |
| `AI_WORKSPACE_JWT_SECRET_KEY` | Independent random value, at least 32 characters |
| `AI_WORKSPACE_CREDENTIAL_ENCRYPTION_KEY` | A newly generated `Fernet.generate_key()` value |
| `AI_WORKSPACE_REGISTRATION_INVITE_CODE` | Independent random shared invite, at least 32 characters |
| `AI_WORKSPACE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` |

`PORT` is supplied by Railway. The image defaults the frontend directory to
`/app/frontend/dist`. The application normalizes `postgres://` and `postgresql://`
to the asyncpg driver without reconstructing credentials by hand. Alembic uses
the same settings and connection normalization.

Generate production secrets independently of development values, transfer them
directly into Railway variables, and retain an access-restricted recovery copy.
Local recovery files belong in ignored `.local/`, never in Git or build inputs.
Move the long-term copy into your password manager. In particular, losing the
Fernet key makes saved OpenRouter keys unreadable even if the database is restored.
Do not regenerate that key on redeployment. A future encryption-key rotation must
re-encrypt existing credentials before retiring the old key.

Users enter their own OpenRouter keys in the application. There is no shared
server-side provider key. Do not upload the local development `.env` to Railway.

## Invitations

Registration accepts `{email, password, invite_code}`. In production, a missing or
incorrect code returns 403 before password hashing or user creation. Invalid input
responses omit submitted values. The code is not stored in the database, browser
storage, logs or build output. The frontend build contains only the public flag
`VITE_REGISTRATION_REQUIRES_INVITE=true`.

To rotate the invite, replace `AI_WORKSPACE_REGISTRATION_INVITE_CODE` with another
random value and redeploy the app. Privately give the new code to future invitees.
Existing accounts, passwords and sessions continue working. The shared invite is
reusable and has no per-user quota; this first release is intended for a small
trusted group. The app currently has no password-reset or email-verification flow.

## Release process

1. Create a branch and PR. CI runs on PRs and on pushes to `main`.
2. CI checks Python lint/format, all backend tests against an isolated PostgreSQL 18
   service, frontend tests/build, and the production Docker image. It uses dummy
   test credentials and never uses the production database or calls a model.
3. Merge only the tested commit. Railway follows `main` with **Wait for CI** enabled.
   Do not cancel in-progress `main` checks. If the GitHub app lacks repository or
   checks access, complete its authorization before connecting the service.
4. Configure pre-deploy command `alembic upgrade head`, timeout **300 seconds**.
   Migrations execute inside the private network after build, before traffic moves.
   Never run migrations during Docker build or independently in every web worker.
5. Use the Dockerfile CMD (one Uvicorn worker, bind `0.0.0.0:$PORT`). Set health path
   `/readyz`, health timeout **120 seconds**, restart policy **ON_FAILURE**, maximum
   **10 retries**, and application draining **90 seconds** for in-flight chat.
6. Configure variables and service settings before attaching the source. After the
   first healthy deployment, generate its Railway HTTPS domain and run:

   ```text
   uv run python scripts/check_deployment.py https://<application-domain>
   ```

7. Verify the active commit, the domain and the absence of runtime failures. A build,
   migration or readiness failure must not replace the last working deployment.

Deploy settings are maintained through Railway and recorded here. No legacy
`railway.json`/`railway.toml` is introduced; Railway now deprecates Config as Code.
Use the current Infrastructure as Code system if the project later needs a fully
declarative multi-environment setup.

Railway health checks gate deployments; they are not continuous uptime monitoring.
During this limited launch, inspect runtime logs, restart events, CPU, memory,
database disk use and spend in the Railway dashboard. Never enable verbose SDK
logging or include request bodies, Authorization headers or plaintext credentials.

## Budget

Use **Hobby**, minimum $5 monthly usage, including $5 usage credit. On this workspace's
Usage page set the **Compute Usage** email alert to **$15** and hard limit to **$20**.
The limit covers the entire workspace and takes all workloads offline at the limit.
It is a resource-usage limit, not a cap on model calls, taxes or unrelated services.
Keep Railway Agent usage separate; this application does not require Railway Agent.

Confirm the two thresholds in the UI after saving. Account payment, billing-method
entry and any required personal authorization must be completed by the account owner.

## Backups and recovery

On the Postgres volume's Backups tab enable both **Daily** (6-day retention) and
**Weekly** (27-day retention). Scheduled backups cost incremental storage. Verify a
successful backup actually appears; saving a schedule alone is not evidence of a backup.
This configuration targets roughly 24-hour recovery points between daily snapshots.

Before schema changes with data risk, create a fresh backup and retain a portable
`pg_dump --format=custom --no-owner` export outside Railway. The PostgreSQL client
major version must match the server. Use an authenticated CLI tunnel for access,
or run the export in the private network. Do not make the database publicly reachable
just for convenience. Feed credentials using process variables or an access-restricted
password file rather than putting them in command history.

For a restore drill, restore the export into an isolated PostgreSQL 18 database.
Compare exact row counts, migration revision, conversation/message relationships,
and decryption of a dedicated test credential using the saved Fernet key. Record
elapsed restore time and backup age. Do not replace production during the drill.

For a real incident, preserve the current data before recovery. Railway volume
recovery stages a restored volume for the same project/environment; review it before
applying. Keep the former volume until validation succeeds. A volume or project
deletion can also remove its snapshots, so it is not a substitute for an external export.

Roll back an application defect to the prior successful Railway deployment. Do not
automatically run `alembic downgrade`: application rollback does not reverse schema
changes. Use backward-compatible migrations; recover database data separately.

## Launch acceptance

- Verify the HTTPS domain from the actual mainland network used by invitees.
- Register and sign in with an invite, then enter an OpenRouter key through the UI.
- Create a conversation, send two turns, refresh, sign out/in and verify history.
- Use another account to verify conversation and credential isolation.
- Redeploy the same app and verify both history and saved credential usability.
- Confirm backup schedules, an actual backup and an isolated restore drill.

The optional script below makes **two billable model calls** to the chosen model.
Its separate env file contains `AI_WORKSPACE_TEST_EMAIL`, `AI_WORKSPACE_TEST_PASSWORD`
and `AI_WORKSPACE_TEST_MODEL`. Prefer configuring the key through the UI; the script
reuses a stored credential. If explicitly supplied in that file, a test key is saved
only when this account does not already have one.

```text
uv run python scripts/smoke_chat.py --base-url https://<application-domain> --env-file .local/production-smoke.env
```

Remote testing refuses the default development `.env`, non-HTTPS URLs, credentials
embedded in URLs and redirects. It leaves a clearly named test conversation and
prints only safe statuses and the conversation ID.

## Official references

- [Pricing](https://railway.com/pricing)
- [Cost control](https://docs.railway.com/pricing/cost-control)
- [GitHub checks and automatic deployments](https://docs.railway.com/deployments/github-autodeploys)
- [Pre-deploy migrations](https://docs.railway.com/deployments/pre-deploy-command)
- [Volume backups](https://docs.railway.com/volumes/backups)
