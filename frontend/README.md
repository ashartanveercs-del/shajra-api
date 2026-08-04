# Shajra Frontend

The frontend lives in `frontend/` and the FastAPI backend lives in `backend/`.
Use Node 22 and Python 3.12 for the repeatable local gate.

## Local development

Start the backend from the repository root in an isolated virtual environment:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install --upgrade pip
backend/.venv/Scripts/python -m pip install -r backend/requirements-dev.txt
Set-Location backend
.venv/Scripts/python -m uvicorn main:app --reload
```

Start the frontend in a second PowerShell window:

```powershell
Set-Location frontend
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000"
npm ci
npm run dev
```

Open the frontend at [http://localhost:3000](http://localhost:3000) and the
backend readiness endpoint at
[http://127.0.0.1:8000/api/health/ready](http://127.0.0.1:8000/api/health/ready).

All write flags remain `false` during this recovery plan. Do not add credentials
to local files or enable public or relationship writes for local verification.

## Local verification

Run backend checks from the repository root with the isolated virtual environment:

```powershell
backend/.venv/Scripts/python -m pytest backend/tests -q
backend/.venv/Scripts/ruff check backend
backend/.venv/Scripts/python -m compileall -q backend
backend/.venv/Scripts/pip-audit -r backend/requirements.txt
```

Run frontend checks from `frontend/`:

```powershell
npm ci
npm audit --audit-level=high
npm run lint
npm run typecheck
npm test
$env:NEXT_PUBLIC_API_URL = "https://ci.invalid"
npm run build
```

The production build intentionally uses only the non-production `.invalid` API
URL. It must never be built with a production URL or secret during local or GitHub
verification.
