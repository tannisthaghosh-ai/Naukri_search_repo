# Sieve

Sieve is a local FastAPI service and PowerShell client for searching Naukri
Resdex through a browser session that you have logged in to yourself. Searches
run asynchronously and can be downloaded as an Excel workbook.

## How it works

Sieve connects over Chrome DevTools Protocol (CDP) to Edge or Chrome running
with `--remote-debugging-port=9222`. You sign in to your own Naukri account in
that browser; Sieve does not handle credentials. Some Resdex flows open Naukri
Launcher, so see the setup note below. Search pagination retains a three-second
delay between pages.

## Requirements

- Windows 10/11, PowerShell 5.1 or PowerShell 7
- Python 3.11 or 3.12
- Microsoft Edge (or Chromium-based Chrome)
- A Naukri Resdex account and Naukri Launcher if your account flow requires it

## Install

From the project directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and replace the `API_KEY` placeholder with a private random value.
Do not share or commit `.env`. The API refuses to start when the key is missing
or still set to the placeholder.

## Configuration

`.env` supports:

| Setting | Purpose | Example |
| --- | --- | --- |
| `API_KEY` | Shared local API/client key; required | Set a private value |
| `API_URL` | Local API URL used by `search.ps1` | `http://127.0.0.1:8000` |
| `NAUKRI_CDP_URL` | Local Edge/Chrome CDP endpoint | `http://127.0.0.1:9222` |
| `DB_PATH` | SQLite database path; relative paths use the project directory | `data/sieve.db` |

CDP URLs must use HTTP and point to localhost. The API binds only to
`127.0.0.1`.

## Usage

### Start Edge and log in

```powershell
.\start-edge.ps1
```

The script starts Edge with a dedicated profile under `data\edge_profile`.
If Edge is already running without the configured CDP port, close its windows
yourself and run the script again. Complete sign-in manually in the launched
browser. If Naukri Launcher opens another browser, copy its sign-in URL into
the CDP browser and finish there. Keep the CDP browser open.

### Start the API

In another PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

### Search and download

```powershell
.\search.ps1 "python developer" -MaxPages 5 -Location "Kolkata" -NoOpen
```

`-MaxPages` and `-Location` are optional; omit `-NoOpen` to open the workbook
after it is saved. The client polls every three seconds and saves workbooks
under `output\`.

### Output format

The `Candidates` sheet contains `Name`, `Experience`, `Current Company`,
`Skills`, `Profile Link`, and `Page`. The `Info` sheet contains the keyword,
location, candidate count, and export timestamp. The Excel output is local and
may contain personal candidate data.

### API endpoints

All `/api` endpoints require the `X-API-KEY` header. `/health` is unauthenticated.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Local API health |
| `GET` | `/api/health` | Authenticated API health |
| `POST` | `/api/launcher/connect` | Launch/check Naukri Launcher |
| `GET` | `/api/launcher/status` | Check browser/launcher status |
| `POST` | `/api/search` | Start a search (`keyword`, `max_pages`, optional `location`) |
| `GET` | `/api/search/{job_id}` | Read status, progress, and results |
| `GET` | `/api/search/{job_id}/export.xlsx` | Download the workbook |

Delete old stored jobs and their candidate results when no longer needed:

```powershell
.\.venv\Scripts\python.exe scripts\cleanup_jobs.py --older-than-days 30
```

## Troubleshooting

- **Cannot connect to Edge/Chrome:** Run `.\start-edge.ps1`, keep that browser
  open, and check `http://127.0.0.1:9222/json/version`.
- **Logged out:** Sign in manually in the CDP browser and retry. Sieve does not
  sign in for you.
- **Search button stays disabled:** Check that the keyword suggestion was
  accepted and that the browser is on Resdex advanced search.
- **Empty or incomplete fields:** Inspect the open Resdex page with
  `.\.venv\Scripts\python.exe scripts\inspect_page.py`. Captures go under
  `data\debug\` and may contain personal data; do not share them.
- **PowerShell execution policy:** Run
  `powershell -ExecutionPolicy Bypass -File .\search.ps1 "keyword"` for that
  invocation only; this does not change the machine policy.
- **API key rejected or API will not start:** Confirm that `.env` contains the
  same non-placeholder `API_KEY` for the API and client. Restart the API after
  changing it.

## Development

Install development dependencies and run the offline tests and linter:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
```

The tests mock Playwright and do not require a browser or network access.

## Responsible use

This tool only automates a browser session that you have legitimately logged
in to with your own Naukri account. It performs no login, CAPTCHA solving, or
detection evasion. It keeps a three-second delay between result pages. You are
responsible for complying with Naukri's Terms of Service and applicable
data-protection laws, including India's DPDP Act and GDPR, for any candidate
data you collect.

No candidate data or credentials are included in this repository. Local
database files, browser profiles, captures, and workbooks are excluded by
`.gitignore`.
