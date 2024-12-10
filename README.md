# TimeTracker

## how to export

create `.env` (name and path cannot be changed) file within `src` folder with `PAGE_URL` envvar that is pointing to your SAP fiori page, then:

```bash
pyinstaller --onefile --noconsole --icon=src/favicon.ico --add-data "src/.env;src" --add-data "src/favicon.ico;src"  -n TimeTracker src/main.py
```

## install local

```bash
uv venv --seed
uv sync
```
