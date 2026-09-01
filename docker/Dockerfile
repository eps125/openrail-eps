# syntax=docker/dockerfile:1
#
# One image for the whole openrail suite.  It is used by every service in
# docker/docker-compose.yml - only the entrypoint command differs.
#
# Build context MUST be the repository root, e.g.
#   docker build -f docker/Dockerfile -t openrail:local .
# (docker-compose.yml already sets context: .. for you.)

##############################################################################
# Build stage - compile the C binaries
##############################################################################
FROM debian:bookworm-slim AS build
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc make m4 \
        libcurl4-openssl-dev \
        libmariadb-dev libmariadb-dev-compat \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . /src

# The upstream makefile hard-codes -I/usr/include/mysql and -lmysqlclient;
# libmariadb-dev-compat provides both. Add a defensive fallback in case the
# compat include symlink is ever missing.
RUN if [ ! -e /usr/include/mysql/mysql.h ]; then ln -s /usr/include/mariadb /usr/include/mysql; fi
RUN make all

# Pre-process the livesig track diagrams and collect the static web assets,
# exactly as the upstream "release" script does.
RUN mkdir -p /out/auxy \
    && for f in livesig*.svg; do m4 "$f" > "/out/auxy/$(basename "$f")"; done \
    && cp liverail.css liverail.js livesig.css livesig.js railquery.js /out/auxy/

##############################################################################
# Runtime stage
##############################################################################
FROM debian:bookworm-slim AS runtime
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4 libmariadb3 mariadb-client \
        apache2 cron \
        ca-certificates procps bsd-mailx tzdata tini \
    && rm -rf /var/lib/apt/lists/* \
    && a2enmod cgid rewrite

# --- daemons + batch tools ---
COPY --from=build \
        /src/stompy /src/vstpdb /src/trustdb /src/tddb \
        /src/cifdb /src/cifmerge /src/archdb /src/corpusdb /src/smartdb \
        /src/service-report /src/jiankong \
        /usr/sbin/

# --- CGI programs ---
COPY --from=build \
        /src/liverail.cgi /src/livetrain.cgi /src/livesig.cgi \
        /src/railquery.cgi /src/ops.cgi \
        /usr/lib/cgi-bin/

# --- static web assets + reference SQL ---
COPY --from=build /out/auxy/ /var/www/auxy/
COPY cape.sql /usr/local/share/openrail/cape.sql

# --- container glue ---
COPY docker/bin/ /usr/local/bin/
COPY docker/apache-openrail.conf /etc/apache2/conf-enabled/openrail.conf
COPY docker/openrail.cron        /etc/cron.d/openrail

RUN chmod 0755 /usr/local/bin/openrail-* \
    && chmod 0644 /etc/cron.d/openrail \
    && mkdir -p /var/log/garner/web /var/spool/stompy \
    && printf 'ServerName openrail\n' > /etc/apache2/conf-enabled/servername.conf

# tini reaps the daemons that re-parent onto PID 1 after they fork.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["openrail-collector"]
