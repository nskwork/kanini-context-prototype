# PostgreSQL 16 with Apache AGE 1.6.0, built from the same source commit the prototype was tested on.
FROM postgres:16-bookworm

ARG AGE_COMMIT=8c74fd24dd91bba54fdf4793fccd0fb7bd2bb9d8

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git ca-certificates bison flex postgresql-server-dev-16 \
 && git clone https://github.com/apache/age.git /tmp/age \
 && cd /tmp/age && git checkout "$AGE_COMMIT" \
 && make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config install \
 && cd / && rm -rf /tmp/age \
 && apt-get purge -y build-essential git bison flex postgresql-server-dev-16 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
