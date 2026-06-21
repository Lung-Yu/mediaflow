# Security — Dependency Vulnerability Notes

Last reviewed: 2026-06-22

Dependabot alerts are tracked at
[github.com/Lung-Yu/mediaflow/security/dependabot](https://github.com/Lung-Yu/mediaflow/security/dependabot).
This document records the disposition of each alert and why.

---

## Python dependencies (`requirements.txt`)

### `python-multipart` — high (multiple CVEs)

| CVE / GHSA | Summary | Fixed in |
|------------|---------|----------|
| GHSA-…     | DoS via malformed `multipart/form-data` boundary | 0.0.21 |
| GHSA-…     | Arbitrary file write via non-default config | 0.0.24 |
| GHSA-…     | DoS via unbounded part headers | 0.0.18 |
| GHSA-…     | Quadratic querystring parsing (semicolons) | 0.0.21 |
| GHSA-…     | Content-Disposition parameter smuggling | low |
| GHSA-…     | Negative Content-Length buffers full body | low |
| GHSA-…     | Semicolon as query-field separator smuggling | low |
| GHSA-…     | DoS via large multipart preamble/epilogue | medium |

**Version in use:** `0.0.20` (Python < 3.10, host only) · `0.0.32` (Python ≥ 3.10, Docker)

**Risk assessment:** The FastAPI HTTP server runs in Docker (Python 3.11) and receives
`0.0.32` — the current latest, which addresses all known CVEs. The `0.0.20` line in
`requirements.txt` is installed only on the host (Python 3.9, used by `pipeline/watcher.py`
and `pipeline/worker.py`). Neither process parses multipart form data; they speak to the API
over localhost HTTP. **Actual exposure is nil on the host side.**

**Action:** No upgrade possible — `0.0.32` is the latest release and `>0.0.20` requires
Python ≥ 3.10. Dismiss alerts for the `< 3.10` constraint as `tolerated_risk`. Revisit if
a Python ≥ 3.10 build is added for the host, or if a new `python-multipart` release changes
the minimum version.

---

### `Jinja2` — medium (sandbox breakout, 3 CVEs)

**Version in use:** `3.1.6` (current latest)

**Risk assessment:** All three CVEs concern `SandboxedEnvironment`, which allows untrusted
template authors to break out of the sandbox. This project uses `Environment` (the standard
non-sandboxed mode) to render internal templates where the template author is the application
itself. **Untrusted user input is never passed as a Jinja2 template.**

**Action:** Already at latest version; no fix available. Dismiss as `not_used` — the
vulnerable feature (`SandboxedEnvironment`) is not used. Revisit if a template-from-user-input
feature is ever added.

---

### `pytest` — medium (CVE-2025-71176, tmpdir handling)

**Version in use:** `8.4.2` on host (Python 3.9) · `≥9.0.3` on CI (Python 3.11)

**Fixed in:** 9.0.3, which requires Python ≥ 3.10.

**Resolution:** `pytest` was moved from `requirements.txt` to `requirements-dev.txt`
(commit `f301e5f8`). The Docker image (`api/Dockerfile`) installs only `requirements.txt`
and is no longer affected. CI runs Python 3.11 and receives the fixed version automatically
via the `python_version >= "3.10"` constraint in `requirements-dev.txt`.

**Residual risk:** The host venv (Python 3.9) still runs `8.4.2`. This is a development
machine, not a server, and the tmpdir vulnerability requires local write access.
**Not a production concern.**

---

## npm dependencies (`frontend/`)

All flagged packages are in `devDependencies`. The frontend uses a Docker multi-stage build:

```dockerfile
FROM node:20-alpine AS builder   # npm ci installs all deps including devDeps
RUN npm run build                # outputs static files to /app/dist
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html   # only the built output
```

The final production image is the nginx stage — it contains no `node_modules` and is
not affected by any npm devDependency CVE.

### `vitest` — critical (GHSA-v6wh-96g9-6wx3)

Vitest UI server can serve arbitrary files when running. **Vitest is never run in
production.** Only triggered if a developer runs `npm run test:ui` locally.

**Action:** Dismiss as `tolerated_risk` (dev-only tooling, not deployed).

### `vite` — high + medium (multiple CVEs)

CVEs cover Vite's dev server (`vite serve`): path traversal in optimised deps map
handling, Windows alternate path bypass for `server.fs.deny`. **Vite's dev server is
never run in production** — `npm run build` is the only Vite command executed in CI
and in the Docker build.

**Action:** Dismiss as `tolerated_risk` (dev server not exposed in production).

### `esbuild` — medium (GHSA-…)

esbuild's dev server allows cross-origin requests when used directly. In this project
esbuild is bundled inside Vite and its standalone server is never started.

**Action:** Dismiss as `tolerated_risk`.

---

## Summary table

| Package | Severity | In prod image? | Disposition |
|---------|----------|----------------|-------------|
| `python-multipart 0.0.20` | high | No (host only, no HTTP serving) | Tolerated — upgrade blocked by Python 3.9 on host |
| `python-multipart 0.0.32` | — | Yes (Docker) | Resolved — latest version |
| `Jinja2 3.1.6` | medium | Yes (Docker) | Tolerated — latest version; CVE requires SandboxedEnvironment |
| `pytest` | medium | **No** (moved to requirements-dev.txt) | Resolved — removed from Docker image |
| `vitest` | critical | No (devDependency, nginx image) | Tolerated — dev-only |
| `vite` | high/medium | No (devDependency, nginx image) | Tolerated — dev server not deployed |
| `esbuild` | medium | No (devDependency, nginx image) | Tolerated — dev server not used |
