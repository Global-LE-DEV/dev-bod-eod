# dev-bod-eod

LE BOD/EOD monitoring for site `fs-dr-le`. One cron-driven container polls URLs
and telnet ports, pushes results to Redis, signals RabbitMQ so the LE BOD
dashboard refreshes, and emails alerts through the central apprise notify
service.

## Layout

| Path | Purpose |
| --- | --- |
| `kustomization.yaml` | kustomize entrypoint (ConfigMap + Deployment) |
| `fs-dr-le-bod-eod-le-cmap.yaml` | ConfigMap `bod-env-details` (namespace `fs-dr-le`) |
| `fs-dr-le-bod-eod-le-deployment.yaml` | Deployment `bod-eod-le`; contains the live entrypoint/crontab builder |
| `fs-dr-le-bod-eod-le/Dockerfile` | ubuntu:20.04 + python3 + cron, copies `overlayFiles/` to `/data/overlayFiles/` |
| `fs-dr-le-bod-eod-le/overlayFiles/` | checker scripts, `FinspotUtils.py`, calendars, `requirements.txt` |
| `fs-dr-le-bod-eod-le/tests/` | unit tests for the telnet alert-window gate |

## Checks

| Script | Schedule (IST) | Notes |
| --- | --- | --- |
| `urlchecker.py` | every 2 min | HTTP/HTTPS/WSS status + SSL expiry; Redis key from `/data/url.yaml` |
| `telnet_checker.py` | every 2 min (`TELNET_ENABLED=yes`) | TCP port reachability; per-port mail is gated by the alert window |
| `holiday_checker.py` | 07:00 | pushes `Holiday` / `Mock` calendar matrices to Redis |
| `k8s_ssl_check.py` | 07:00 (`K8S_ENABLED=yes`) | `kubeadm certs check-expiration` |
| `urlchecker.py --summary` | `URL_SUMMARY_CRON` (12:00) | digest mail |
| `telnet_checker.py --summary` | `TELNET_SUMMARY_CRON` (08:45) | digest mail, lands before the alert window opens |

Status vocabulary: `0` down/red, `1` amber, `2` up/green, `3` unknown.

## Alert window (telnet per-port mail)

Per-port telnet alerts are mailed only for ports that are actually DOWN and only
inside `ALERT_WINDOW_START`-`ALERT_WINDOW_END` (inclusive, IST). The `*/2` check
itself still runs 24x7 so the dashboard keeps updating. All knobs live in the
`bod-env-details` ConfigMap:

- `alert_window_enabled` - `no` mails down ports 24x7 (never up ports)
- `alert_window_start` / `alert_window_end` - e.g. `09:00` / `23:30`

## Build & deploy

```sh
docker build -t harbor.finspot.in/fs-dr-le/bod-eod-le:<tag> ./fs-dr-le-bod-eod-le
kubectl kustomize .            # renders ConfigMap + Deployment
kubectl apply -k .             # (namespace fs-dr-le)
```

The Deployment overrides the image entrypoint with an inline script that dumps
container env to `/etc/bod.env`, rebuilds the crontab (each job sources that
file), and runs `cron -L 15`. Note `overlayFiles/entrypoint.sh` is only used
when the image is run without that override.

## Tests

```sh
python -m unittest discover -s fs-dr-le-bod-eod-le/tests
```

On Windows consoles run it with `PYTHONIOENCODING=utf-8` (the scripts print
emoji in their fallback warnings).
