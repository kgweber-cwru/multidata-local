# Encounter Explorer: the demo web app as a container.
#
# Build from the repo root (the context must include data/ for the
# "with-data" target):
#
#   docker build -f demo/Dockerfile --target app       -t encounter-explorer:app .
#   docker build -f demo/Dockerfile --target with-data -t encounter-explorer:data .
#
# "app" (the default deploy): server and page only, a few MB. Mount the built
# bundles (data/demo/: cases/ and overview.json) at /data, e.g. a Cloud
# Storage bucket mounted as a Cloud Run volume, so new encounters don't need
# a new image.
#
# "with-data": the same with the bundles copied in (about 0.6 GB per
# encounter). Runs anywhere Docker runs, no bucket needed.
#
# Standard-library Python only. Listens on $PORT (Cloud Run sets it; 8080
# otherwise). See demo/README.md, "Deploying to Google Cloud".

FROM python:3.12.8-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    DEMO_DATA=/data \
    DEMO_APP=/srv/app
WORKDIR /srv
COPY demo/serve.py /srv/serve.py
COPY demo/app/ /srv/app/
RUN useradd --system --uid 10001 --no-create-home explorer && mkdir -p /data
USER explorer
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=2)" || exit 1
CMD ["python", "/srv/serve.py"]

FROM app AS with-data
COPY --chown=explorer data/demo/overview.json /data/overview.json
COPY --chown=explorer data/demo/cases/ /data/cases/
