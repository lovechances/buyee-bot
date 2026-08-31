# Deployment

BuyEye remains one long-running worker. Railway or Docker owns process restarts; BuyEye owns query
rotation, bounded source retries, browser recovery, SQLite, and alert deduplication.

## Railway-first deployment

1. Push this repository to a private GitHub repository and create a Railway service from it.
2. Add one persistent volume mounted at `/data`. The image already pins its runtime paths there,
   and Railway also provides `RAILWAY_VOLUME_MOUNT_PATH` automatically.
3. Add the required Discord webhook variables from `.env.example` as Railway service variables.
   Do not upload `.env` or paste secrets into build arguments.
4. Upload the portable authenticated state as described below.
5. Perform the temporary no-alert deployment below, then clear its Start Command and redeploy the
   continuous worker. Do not add a public domain; this worker exposes no web service.
6. Check logs for `startup query_universe=...`, then validate with
   `railway ssh -- python -m buyeeye health`.

Railway volumes are mounted only at runtime. Database initialization and browser directory creation
therefore happen in the application start path, never in a pre-deploy command.

The image pins every mutable path to that volume:

- `/data/buyeeye.db`: listings, comp/detail caches, alert history, global dedupe, query scheduler,
  scan metrics, and runtime health
- `/data/browser/buyee_state.json`: portable Playwright authentication state
- `/data/browser/`: the Linux Chromium profile created and reused after bootstrap
- `/data/logs/`: rotating runtime logs

The process lock at `/data/buyeeye.lock` is recreated on startup. Playwright browser binaries and
the manual/generated YAML intelligence shipped in `/app/config` are immutable image contents, not
runtime state.

## Portable Buyee session bootstrap

The portable artifact is `data/browser/buyee_state.json`, created by Playwright storage state. It
contains cookies and local storage and must be handled as a secret. A complete macOS Chromium
profile is not portable to Linux and must not be copied to Railway.

After attaching the `/data` volume, upload the existing state directly into the volume:

```sh
railway volume files upload ./data/browser/buyee_state.json /browser/buyee_state.json
```

The upload destination is relative to the selected volume's root. With that volume mounted at
`/data`, remote `/browser/buyee_state.json` is visible to the container at the required absolute
path `/data/browser/buyee_state.json`; using `/data/browser/...` as the remote volume path would
incorrectly create `/data/data/browser/...` in the container.

Do not print or base64-encode the state into logs or commit it. For the first deployment only, set
the Railway Start Command temporarily to the no-alert validation command below:

```sh
python -m buyeeye scan-once "Number (N)ine"
```

Deploy and inspect it with `railway logs --lines 200`. The one-shot command never sends listing
alerts and exits after the scan. Then clear the Railway Start Command so the image's continuous
`python -m buyeeye run` command applies, and redeploy. Validate the running worker with:

```sh
railway ssh -- python -m buyeeye health
railway logs --since 30m
```

Do not launch a second one-shot scan over SSH while the daemon is running because both processes
would share the persistent Chromium profile. `send-test-alert` remains the explicit Discord test.
After the daemon records a successful scan, run `health`, restart or redeploy, and run `health`
again to prove the database, session, and accumulated counters survived on `/data`.

If the portable storage state is rejected or expired, Railway has no permanently exposed VNC
endpoint in this deployment. Do not weaken browser security or build anti-bot workarounds. The
practical choices are to refresh the state locally with `python -m buyeeye setup-session` and upload
it again, or move the same image to the VPS fallback below for a one-time secured interactive login.

## Operations

- Deploy: GitHub auto-deploy or `railway up`
- Start/restart: Railway controls or `railway restart`
- Stop: remove the active deployment in Railway
- Status/metrics: `railway ssh -- python -m buyeeye health`
- Logs: Railway Logs tab or `railway logs`
- Safe one-shot: use `python -m buyeeye scan-once "query"` as a temporary Start Command, not beside
  the daemon
- Explicit alerting one-shot: `python -m buyeeye scan-alert-once "query"` only when the daemon is
  stopped
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
