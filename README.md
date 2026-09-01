# openrail-eps

Containerised build of the [openrail](https://github.com/philwieland/openrail)
suite (Phil Wieland) — a set of C programmes that collect and display UK rail
open data:

* `stompy` — collect the Network Rail STOMP feed and fan it out
* `vstpdb` / `trustdb` / `tddb` — write VSTP timetable, TRUST movement and TD
  data into MySQL/MariaDB
* `cifdb` / `corpusdb` / `smartdb` / `archdb` — timetable + reference data + archiving
* `liverail` / `livetrain` / `railquery` / `livesig` / `ops` — CGI web views

The original code and its documentation are unchanged. Everything added here
lives under [`docker/`](docker/) plus the image-publishing workflow in
[`.github/workflows/`](.github/workflows/).

## Deploy

A GitHub Actions workflow builds a multi-arch image and pushes it to
`ghcr.io/eps125/openrail-eps`. The stack in [`docker/docker-compose.yml`](docker/docker-compose.yml)
pulls that image, so it deploys straight from Portainer with no build step.

See **[docker/README.md](docker/README.md)** for full setup, configuration and
operations.

## Licence

GPL-3.0 — see [COPYING](COPYING). Original work © Phil Wieland.
