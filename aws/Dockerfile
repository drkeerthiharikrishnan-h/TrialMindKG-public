# The web app (pipelines/app.py) as one image. Build from the repo root:  docker build -f aws/Dockerfile -t tmkg-app .
# The big read-only files (card index, predictions, deliverables) are NOT in the image: they are mounted at /data (see
# aws/deploy_app.sh) and found through TMKG_PHAROS_INDEX / TMKG_PHAROS_ROOT. The NVIDIA key is passed at run time
# (-e NVIDIA_API_KEY, read from SSM by the instance), never baked in.
# SEC-15 (11 Nov): the base image is pinned by digest (the multi-platform index of python:3.11-slim, read from Docker Hub on
# 2026-11-11) and every package, direct and transitive, comes from requirements.lock.txt, so a redeploy installs the same
# versions as the last one. To move to a newer base image: docker buildx imagetools inspect python:3.11-slim, copy the digest.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY pipelines ./pipelines
COPY config ./config
COPY personas ./personas
COPY sources ./sources
COPY data/reference ./data/reference
# all committed outputs (56 MB; outputs/g0-g4 are excluded by the dockerignore except the summary.md of g0, g2 and g3, which
# the agent log and the all-layer numbers read): the trial graph kg_nodes/kg_edges.csv,
# run_manifest, resolution/contradiction reports, eval, reports, kgx. 7 Nov: copying only outputs/kgx made /api/landing,
# /api/status and the Trials tab fail with HTTP 500 on the hosted app (KGIndex reads outputs/kg_nodes.csv).
COPY outputs ./outputs
# all committed docs (6.5 MB): the app reads AGENT_LOOP.md (For judges), BRING_YOUR_OWN_SOURCE.md (Generalization),
# demo_query_TUB040.md, organizers/BRIEF.md and SCHEMA_CARD.md. 8 Nov: only SCHEMA_CARD.md was copied, so the hosted
# For judges page showed no agent-loop steps (/api/agent-loop available: false).
COPY docs ./docs
# the downloads list the For-judges page reads (usability.downloads; base URL set at boot by TMKG_DOWNLOADS_BASE). 8 Nov: missing
# from the image, so /api/downloads said available: false although Caddy served the files.
COPY downloads.json ./downloads.json
COPY DATA_LICENSES.md ./DATA_LICENSES.md
COPY static ./static
# the bring-your-own-source example the Generalization page builds on request (usability.byo_source: examples/byo_source/
# lab_screen.yaml + screen.tsv.gz, SYNTHETIC values). 11 Nov: missing from the image, so the hosted /api/generalization said
# byo.built = null ("No such file or directory: '/app/examples/byo_source/lab_screen.yaml'").
COPY examples ./examples
# FIX4 (owner decision 15 Nov; S03 finding A): the experiment log and the download record of the reference files. Without
# them the hosted "What the agent did" log had 10 entries and no scientist decision (31 and 8 on the Mac at the same
# commit) and six of its entries had no date. Only these two files: the rest of loop/ (task packets) and of data/external
# (the downloaded reference data) stay out; the dockerignore lets the manifest through.
COPY loop/ledger.tsv ./loop/ledger.tsv
COPY data/external/DATA_MANIFEST.tsv ./data/external/DATA_MANIFEST.tsv
ENV TMKG_PHAROS_ROOT=/data TMKG_PHAROS_INDEX=/data/data/kg/pharos_index/card_index.sqlite PORT=8000
# at most 20 questions per visitor per hour on the ask endpoints (app.py ask_allowed; owner decision 8 Nov)
ENV TMKG_ASK_LIMIT_PER_HOUR=20
# SEC-04: at most this many questions a day for ALL visitors together (a new cookie is a new visitor, so the hourly limit
# alone does not protect the credits), and at most 4 requests using a model at the same time. 500 a day is a starting
# value chosen on 11 Nov, not an owner decision yet: change it here or with -e at docker run.
ENV TMKG_ASK_LIMIT_PER_DAY=500 TMKG_MODEL_CONCURRENCY=4
# SEC-07: the work budget of one model-written graph query on the small hosted server (the code defaults are sized for the
# Mac: 2,000,000 link checks / 60 s / 100,000 matches), and at most two such queries at a time.
ENV TMKG_QUERY_MAX_BINDINGS=20000 TMKG_QUERY_MAX_STEPS=500000 TMKG_QUERY_MAX_SECONDS=15 TMKG_QUERY_CONCURRENCY=2
# SEC-15: the app does not run as root. It only writes the frozen model answers (data/snapshots_llm) and library caches
# under its home folder; /data is mounted read-only.
RUN useradd --system --uid 10001 --create-home --home-dir /home/tmkg tmkg \
 && mkdir -p /app/data/snapshots_llm && chown -R tmkg:tmkg /app/data /home/tmkg
ENV HOME=/home/tmkg MPLCONFIGDIR=/tmp
USER tmkg
EXPOSE 8000
# SEC-08: Docker only MARKS the container unhealthy; the instance's tmkg-heal timer (aws/user_data.sh.tmpl) restarts it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/api/pharos/status' % os.environ.get('PORT','8000'), timeout=4)"
CMD ["sh", "-c", "uvicorn app:app --app-dir pipelines --host 0.0.0.0 --port ${PORT}"]
