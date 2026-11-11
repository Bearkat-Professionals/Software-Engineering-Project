# Scan screen

The test page for starting a scan and watching it. It lives in `frontend/app` and is separate from the CRUD tester in `frontend/crud-react`.

Node.js 20.19 or newer (`node -v`).

## Install

From `frontend/app`:

    npm install

## Run in development

The page calls the Python app. In the project-root `.env`:

    DEV_USERNAME=<an existing editor or admin username>
    APP_PORT=5001

`APP_PORT` must stay 5001. The Vite proxy sends `/api` to http://127.0.0.1:5001. The CRUD server can keep port 5000. Stop the CRUD page first if it is already using port 5173.

Create `frontend/app/.env.local` with:

    VITE_USE_SAMPLES=false

Terminal 1, from the project root:

    .\dev app

Long form: `py -3.13 -m backend.main`. On Linux, with the virtual environment active: `python -m backend.main`.

Check http://127.0.0.1:5001/api/health. It should report `"status": "ok"`.

Terminal 2:

    cd frontend/app
    npm run dev

Open http://127.0.0.1:5173/scans. `/` redirects there. Enter a private range such as `192.168.1.0/24` and choose Start Scan. Stop each terminal with Ctrl+C.

## Sample mode

Set `VITE_USE_SAMPLES=true` in `frontend/app/.env.local` and start Vite again. The page then uses the JSON files in `src/samples/` and does not call the Python app. Set it back to `false` and restart Vite before a real scan.

## Build

From `frontend/app`, before a demo:

    npm run build

This writes `frontend/app/dist/`. Run it again whenever the source has changed, so nobody demos a stale build. Node is required to build. It is not required to serve `dist/`.

## Lint

    npm run lint