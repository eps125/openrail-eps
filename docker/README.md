# Running openrail in containers (Portainer stack)

This folder wraps the upstream openrail C suite into a small Docker stack:

| Service | Image command | What it does |
|---------|---------------|--------------|
| `db` | `mariadb:11` | The `rail` database. Schema is created automatically on first daemon start. |
| `collector` | `openrail-collector` | Runs `stompy`, `vstpdb`, `trustdb`, `tddb` together (they talk to `stompy` over `localhost`, so they must share one container). Supervises and restarts them, and sends `stompy` the "start flow" commands the old init.d scripts sent. |
| `web` | `openrail-web` | Apache + the `liverail` / `livetrain` / `railquery` / `livesig` / `ops` CGIs. |
| `cron` | `openrail-cron` | Periodic `cifdb` (daily timetable), `archdb`, `corpusdb`. |
| `init` | `openrail-init` | One-shot: load `cape.sql`, CORPUS + SMART reference data, and the full CIF timetable. Runs only with `--profile init`. |
| `allocation` | `openrail-allocation` | Kafka consumer for the RDM *"NWR Passenger Train Allocation and Consist"* feed. Writes `train_allocation`, shown by `livetrain.cgi` as the **Allocations** panel. Idle until `RDM_KAFKA_*` is configured. |

All four app containers are the **same image**. `.github/workflows/docker-publish.yml`
builds it and pushes it to `ghcr.io/<owner>/<repo>` (linux/amd64 + arm64) on every
push to `main`/`master`, on `v*` tags, and on manual dispatch. `docker-compose.yml`
pulls that image, so Portainer can deploy the stack with no build step.

## Prerequisites

* A **National Rail Open Data** account (free): <https://datafeeds.networkrail.co.uk/>.
  Without it the stack still starts, but no live data is collected.
* Outbound access from the `collector` container to
  `datafeeds.networkrail.co.uk:61618` (STOMP) and HTTPS (CIF / CORPUS / SMART downloads).

## Publishing the image

Push this repo to GitHub. The workflow runs and publishes
`ghcr.io/eps125/openrail-eps:latest`. Then:

* Make the package public: GitHub → your profile → **Packages** →
  `openrail-eps` → **Package settings** → **Change visibility → Public**. (Or
  keep it private and add a GHCR registry with a PAT under Portainer →
  **Registries**.)
* If you rename the repo or use a different account, set `OPENRAIL_IMAGE`
  accordingly, e.g. `OPENRAIL_IMAGE=ghcr.io/yourname/openrail-eps:latest`.
  The workflow itself always derives the path from the repo, so no workflow
  edit is needed.

## First run (docker compose CLI)

```bash
cd docker
cp .env.example .env
# edit .env: OPENRAIL_IMAGE, DB_PASSWORD, DB_ROOT_PASSWORD, NR_USER, NR_PASSWORD, PUBLIC_URL, WEB_PORT

docker compose pull
docker compose up -d db
docker compose --profile init run --rm init      # reference data + timetable (slow)
docker compose up -d
```

To build locally instead of pulling from GHCR:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Then open `http://SERVER:<WEB_PORT>/rail/liverail/` (default port 8080, or just `http://SERVER:8080/`).

Useful URLs (same paths as upstream):

```
/rail/liverail/<TIPLOC>            departure board
/rail/livetrain/...               train detail
/rail/query/...                   schedule query
/rail/livesig/<area>              signalling map (needs SMART data)
/rail/ops/...                     operator control panel  -- NOT password protected, see apache-openrail.conf
```

## First run (Portainer)

1. **Stacks → Add stack → Repository**. Point at this repo, set compose path
   `docker/docker-compose.yml`. (No build step — Portainer just pulls the GHCR image.)
2. In **Environment variables**, add the entries from `.env.example` —
   `OPENRAIL_IMAGE`, `DB_PASSWORD`, `DB_ROOT_PASSWORD`, `NR_USER`, `NR_PASSWORD`,
   `PUBLIC_URL`, `WEB_PORT`, …
3. **Deploy the stack.**
4. Bootstrap once — open a console on the `collector` container (Portainer →
   Containers → collector → **Console → Connect**) and run:
   ```bash
   openrail-init
   ```
   or, from an SSH session on the host:
   ```bash
   docker compose -f docker/docker-compose.yml --profile init run --rm init
   ```

To change the web port later, edit `WEB_PORT` in the stack's environment
variables and redeploy.

## Configuration

Every container renders `/etc/openrail.conf` at startup from environment
variables (`docker/bin/openrail-render-config`). Change a value → recreate the
containers (`docker compose up -d --force-recreate`); the daemons only read the
file once at start.

Key variables (full list in `.env.example`):

* `DB_*` – database name / user / passwords for the bundled MariaDB.
* `NR_USER`, `NR_PASSWORD` – National Rail feed credentials.
* `PUBLIC_URL` – base URL for links in report emails (end with `/`).
* `STOMP_TOPICS` / `STOMP_TOPIC_NAMES` / `STOMP_TOPIC_LOG` – feed subscriptions
  (defaults match `example.conf`).
* `INIT_FETCH_TIMETABLE=no` – skip the large `cifdb -a` during `init`.
* `OPENRAIL_DEBUG=1` – verbose logging.

### Unit allocations (the "Allocations" panel on a train page)

The `allocation` service consumes the Rail Data Marketplace product
**"NWR Passenger Train Allocation and Consist"** (Apache Kafka, SASL_SSL/PLAIN,
TAF-TSI `PassengerTrainConsistMessage` XML) and writes a flattened per-unit row
set into `train_allocation`. `livetrain.cgi` matches it to the displayed service
on **CIF train UID + service date** (the message `Core` field is
`headcode(4) + train UID(6) + origin hour(2)`), and renders it as
*Reported / Unit / Class / Vehicles*.

Subscribe to the product on <https://raildata.org.uk/>, then set in the stack env:

| Variable | |
|---|---|
| `RDM_KAFKA_BOOTSTRAP` | e.g. `pkc-...confluent.cloud:9092` (usually the default is fine) |
| `RDM_KAFKA_TOPIC` | e.g. `prod-1033-Passenger-Train-Allocation-and-Consist-1_0` |
| `RDM_KAFKA_GROUP` | the consumer group id from your subscription (`SC-...`) |
| `RDM_KAFKA_USER` / `RDM_KAFKA_PASSWORD` | the SASL credentials from your subscription — **secret** |
| `RDM_KAFKA_OFFSET_RESET` | `latest` (default) or `earliest` to backfill |

The table is self-created on first run; rows older than `ALLOCATION_KEEP_DAYS`
(default 21) are purged daily by the consumer. Consists refresh roughly every
5 minutes, so allow ~10 min after first start for active services to populate.

### Using an existing database

Drop the `db` service, remove the `depends_on: db` blocks, and set
`DB_SERVER` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` to your server. Create an
empty database and a user with full rights on it first; the daemons build the
schema themselves.

## Operations

```bash
# logs
docker compose logs -f collector
docker compose exec collector tail -F /var/log/garner/trustdb.log

# run a batch job by hand
docker compose exec cron cifdb -c /etc/openrail.conf          # timetable update
docker compose exec cron cifdb -c /etc/openrail.conf -a       # full reload
docker compose exec cron corpusdb -c /etc/openrail.conf
docker compose exec cron smartdb -c /etc/openrail.conf
docker compose exec cron archdb -c /etc/openrail.conf -a 90

# database shell
docker compose exec db mariadb -uroot -p rail
```

`stompy` state (the `manage` script's "Stompy Control") is driven by
`/tmp/stompy.cmd` + `SIGUSR1` inside the `collector` container, e.g. hold TRUST:

```bash
docker compose exec collector sh -c 'printf t > /tmp/stompy.cmd; pkill -USR1 -x stompy'
```

Persistent data lives in the `db_data`, `logs` and `spool` named volumes.

## Notes / limitations

* The daemons `fork()` and detach (there is no foreground switch), so
  `openrail-collector` supervises them with a pgrep/pidfile loop rather than
  `exec`. A crash is caught within ~15 s.
* Report/alarm **emails** need an MTA in the container - none is configured.
  Leave `REPORT_EMAIL` blank unless you add one.
* `ops.cgi` is unauthenticated in this setup. Keep the `web` port off the public
  internet or add HTTP auth (`apache-openrail.conf` has a commented example).
* `jiankong` (monitoring) and `service-report` are built and available in the
  image but not wired into the stack.
* Timetable + movement history grows continuously - watch the `db_data` volume
  and raise `DB_BUFFER_POOL` (the `db` tuning flags live in `docker-compose.yml`;
  `docker/my.cnf` is the annotated rationale).
