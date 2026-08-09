# Copilot Instructions for sap-is-backup

## Project Overview

A Python script that backs up all integration artifacts from SAP Integration Suite (formerly CPI) using its OData API. Each artifact is downloaded as a ZIP, extracted into `artifacts/<artifact-id>/`, and accompanied by an `artifact-metadata.json` file.

## Running the Script

The script requires three environment variables:

```bash
export SAP_HOST="https://<tenant>.integration.sap.com/api"
export SAP_USER="your-username"
export SAP_PASSWORD="your-password"
python backup_cpi.py
```

There is no build step, test suite, or linter configured. The project depends only on `requests`.

## CI/CD

Pipelines are defined in `.gitlab-ci.yml` (GitLab CI, not GitHub Actions). The pipeline:

1. Installs dependencies from `requirements.txt`
2. Runs `backup_cpi.py`
3. Commits and pushes any new/changed artifacts to the current branch
4. Triggers only on scheduled runs or manual web triggers

## Architecture

The script runs top-to-bottom with no modules:

1. **Config block** — reads `SAP_HOST`, `SAP_USER`, `SAP_PASSWORD` from environment variables
2. **Session setup** — creates a `requests.Session` with HTTP basic auth and JSON accept headers
3. **Inventory fetch** — GETs `/api/v1/IntegrationDesigntimeArtifacts` to list all artifacts
4. **Download loop** — for each artifact, GETs the ZIP from the `$value` endpoint, extracts it to `artifacts/<id>/`, and writes `artifact-metadata.json` with `id`, `version`, `name`, `packageId`, and `type`
5. **Git commit** — the CI job commits any changes after the script finishes

## Key Conventions

- **No input/output arguments** — the script is hardcoded to read from environment and write to the `artifacts/` directory in the repo root
- **Full overwrite per run** — `clean_directory()` removes and recreates each artifact's target directory before extracting, so every run re-downloads everything
- **Metadata file naming** — each artifact directory contains exactly one `artifact-metadata.json` with a flat JSON object (no nested structure)
- **Timeout asymmetry** — the inventory list uses a 120s timeout; individual artifact downloads use 300s
- **Git operations are in CI, not the script** — the Python script does not interact with git; the CI job handles `git add`, `commit`, and `push`
- **SAP OData v2 API** — the API uses OData v2 syntax (e.g., `IntegrationDesigntimeArtifacts(Id='...',Version='...')/$value`). New code that queries SAP should follow the same pattern
