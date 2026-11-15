# Reproduce and verify TrialMindKG with one command each (HOW_TO_RUN.md has the expected output and run times;
# docs/HARNESS.md lists every verification asset). Python comes from .venv (see HOW_TO_RUN.md, "Set up").
#
#   make check         tests + offline UI journeys + schema check; exit non-zero on any failure
#   make demo          small EGFR/ERBB graph end to end + the frozen benchmark CLI (reduced epochs; DEMO numbers)
#   make harness       jsdom click gate + journeys, offline (GATE_LIVE=1 / JOURNEYS_LIVE=1 use the configured model)
#   make hosted-check  GET-only check of the hosted app (never the ask endpoints)
#   make clean-demo    remove outputs/demo/

PY      ?= .venv/bin/python
JSDOM   := /tmp/tmkg/jsdom
HARNESS := docs/ux/harness_v3
PYTEST_ARGS := --ignore=.env --ignore=tests/test_prepublic_check.py --ignore=tests/test_controller_backends.py \
               --ignore=tests/test_dev_controller.py -p no:cacheprovider -q

.PHONY: check tests schema demo harness jsdom hosted-check clean-demo

# Gate 1 G1-SEC-02: every script and pipeline module must compile on the venv's Python (3.11; nested quotes in an f-string
# need 3.12 and broke a test file at collection). No .pyc is written (the source is parsed with compile(), nothing imported).
check: tests harness schema
	$(PY) -c "import pathlib,sys; fs=sorted(pathlib.Path('scripts').glob('*.py'))+sorted(pathlib.Path('pipelines').rglob('*.py')); [compile(f.read_text(encoding='utf-8'),str(f),'exec') for f in fs]; print('compiled', len(fs), 'files on Python', sys.version.split()[0])"
	@echo "make check: PASS (tests, harness, schema, compile)"

# SEC-18: jsdom is installed first and TMKG_REQUIRE_JSDOM=1 makes a UI test that would skip for lack of it fail (tests/conftest.py).
tests: jsdom
	TMKG_REQUIRE_JSDOM=1 $(PY) -m pytest $(PYTEST_ARGS)

schema:
	$(PY) pipelines/schema_card.py --check

demo:
	$(PY) scripts/demo.py

# The journeys read jsdom from $(JSDOM) (docs/ux/harness_v3/journeys.sh); installed there once with npm.
jsdom:
	@test -d $(JSDOM)/node_modules/jsdom -a -d $(JSDOM)/node_modules/axe-core || (mkdir -p $(JSDOM) && cd $(JSDOM) && npm init -y >/dev/null && npm install --silent jsdom axe-core)

# Offline unless the caller sets GATE_LIVE=1 / JOURNEYS_LIVE=1. The run rewrites the result JSONs in $(HARNESS)/;
# `git checkout -- $(HARNESS)` restores the committed ones.
harness: jsdom
	bash $(HARNESS)/journeys.sh

hosted-check:
	$(PY) scripts/hosted_check.py --get-only

clean-demo:
	rm -rf outputs/demo
