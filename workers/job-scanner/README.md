# Job scanner worker

Portal crawlers that run on a schedule, save new listings, and handle anti-bot systems.

## Layout

```
job_scanner/
  base.py
  portals/
    naukri/
    linkedin/
    internshala/
    foundit/
    unstop/
```

## Setup (local)

```powershell
cd workers/job-scanner
pip install -r requirements.txt
playwright install chromium
```

Install **Google Chrome** for reliable anti-bot portals.

## Run scrapers

```powershell
cd workers/job-scanner
$env:PYTHONPATH = (Get-Location).Path

python -m job_scanner.portals.naukri -k "python developer" -l bangalore -p 1 -v
python -m job_scanner.portals.linkedin -k "python developer" -l bangalore -p 1 -v
python -m job_scanner.portals.internshala -k "python" -l bangalore -p 1 -v
python -m job_scanner.portals.foundit -k "python developer" -l bangalore -p 1 -v
python -m job_scanner.portals.unstop -k "python" -p 1 -v
```

Flags: `--json`, `--headless`, `-p 2`

## Celery portals

Configured via `SCANNER_PORTALS` (default includes all five):

`naukri,linkedin,internshala,foundit,unstop`
