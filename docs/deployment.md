# Deployment

BuyEye remains one long-running worker. Railway or Docker owns process restarts; BuyEye owns query
rotation, bounded source retries, browser recovery, SQLite, and alert deduplication.

## Railway-first deployment

1. Push this repository to a private GitHub repository and create a Railway service from it.
2. Add one persistent volume mounted at `/data`. Set `BUYEYE_DATA_DIR=/data` explicitly; Railway's
   automatically provided volume mount variable is also recognized.
3. Add the required Discord webhook variables from `.env.example` as Railway service variables.
   Do not upload `.env` or paste secrets into build arguments.
4. Deploy. The Docker image installs Chromium and its Linux dependencies and starts the unchanged
   headed browser on a private Xvfb display. Do not add a public domain; this worker exposes no web
   service.
5. Check logs for `startup query_universe=...`, then run `python -m buyeeye health` in a shell.

Railway volumes are mounted only at runtime. Database initialization and browser directory creation
therefore happen in the application start path, never in a pre-deploy command.

## Portable Buyee session bootstrap

The portable artifact is `data/browser/buyee_state.json`, created by Playwright storage state. It
contains cookies and local storage and must be handled as a secret. A complete macOS Chromium
profile is not portable to Linux and must not be copied to Railway.

After attaching the `/data` volume, upload the existing state directly into the volume:

```sh
railway volume files upload ./data/browser/buyee_state.json /browser/buyee_state.json
railway redeploy
```

Do not print or base64-encode the state into logs or commit it. After redeploy, verify in order:

```sh
railway logs
railway shell
python -m buyeeye health
python -m buyeeye scan-once "Number (N)ine"
python -m buyeeye scan-once "Maison Margiela"
python -m buyeeye send-test-alert
```

The one-shot command never sends listing alerts. `send-test-alert` is the explicit Discord test.
Restart the service and repeat `health` plus one scan to prove `/data` persistence.

If the portable storage state is rejected or expired, Railway has no permanently exposed VNC
endpoint in this deployment. Do not weaken browser security or build anti-bot workarounds. The
practical choices are to refresh the state locally with `python -m buyeeye setup-session` and upload
it again, or move the same image to the VPS fallback below for a one-time secured interactive login.

## Operations

- Deploy: GitHub auto-deploy or `railway up`
- Start/restart: Railway controls or `railway restart`
- Stop: remove the active deployment in Railway
- Status/metrics: `python -m buyeeye health` in a Railway shell
- Logs: Railway Logs tab or `railway logs`
- Safe one-shot: `python -m buyeeye scan-once "query"`
- Explicit alerting one-shot: `python -m buyeeye scan-alert-once "query"`
- Session refresh on Mac: `python -m buyeeye setup-session`, then upload storage state

Enable Railway volume backups after the first verified run. SQLite, caches, runtime scheduler state,
alert dedupe, logs, and browser/session state all live under `/data`.

## Small Ubuntu VPS fallback

Use this if Buyee rejects Railway's datacenter network or the portable session cannot be refreshed
practically. Install Docker, copy the repository, and run one instance:

```sh
docker build -t buyeeye .
docker run -d --name buyeeye --restart unless-stopped --shm-size=1g \
  --env-file .env -e BUYEYE_DATA_DIR=/data \
  -v /opt/buyeeye-data:/data buyeeye
```

Keep `/opt/buyeeye-data` backed up and readable only by the service administrator. For a one-time
interactive login, temporarily use SSH port forwarding to a secured VNC/noVNC container or create
the Playwright storage state on another Linux machine, then remove the interactive service. Never
leave VNC publicly reachable.
