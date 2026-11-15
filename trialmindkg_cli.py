"""The `trialmindkg` command (FIX6 M7): one entry point over the existing command lines. It changes none of them:
`trialmindkg <command> [args]` runs the same file as `python pipelines/<file> [args]` (or `python -m <module>`), with the
same arguments, as the program "__main__".

    trialmindkg --help              the commands and the file each one runs
    trialmindkg query program_trials pid=P19      = python pipelines/kg_query.py program_trials pid=P19

main(argv) takes the argument list (default sys.argv[1:]) and returns the exit code (0 for --help or no command)."""
import argparse
import importlib.util
import runpy
import sys
from pathlib import Path

# command -> ("file", path inside pipelines/) or ("module", dotted module name); the commands of CLAUDE.md and HOW_TO_RUN.md
COMMANDS = {
    "app":         ("file", "app.py", "the web app (http://localhost:8000)"),
    "seed":        ("file", "seed_kg.py", "build the trial graph from the frozen snapshots"),
    "query":       ("file", "kg_query.py", "a fixed query that returns evidence edge ids"),
    "evaluate":    ("file", "evaluate.py", "the answer evaluation (NIM when NVIDIA_API_KEY is set)"),
    "schema-card": ("file", "schema_card.py", "docs/SCHEMA_CARD.md and outputs/schema_card.json (--check: compare)"),
    "maintain":    ("file", "maintain.py", "a rebuilt layer's summary against its last commit"),
    "adc-demo":    ("file", "run_adc_demo.py", "the ADC demo end to end"),
    "compare":     ("module", "pipelines.linkpred.compare", "the frozen link-prediction comparison"),
}


def pipelines_dir():
    """Returns the folder of the installed `pipelines` package (a clone's pipelines/ for an editable install)."""
    spec = importlib.util.find_spec("pipelines")
    return Path(list(spec.submodule_search_locations)[0])


def parser():
    """Returns the argparse parser that prints the command list for --help."""
    lines = [f"  {c:<12} {('pipelines/' + t) if k == 'file' else 'python -m ' + t:<36} {d}" for c, (k, t, d) in COMMANDS.items()]
    return argparse.ArgumentParser(
        prog="trialmindkg", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="TrialMindKG command line: each command runs the existing script with the same arguments.",
        epilog="commands:\n" + "\n".join(lines) + "\n\nThe data folders are not installed by pip; the full reproduction needs a clone "
               "(HOW_TO_RUN.md).")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = parser()
    if not argv or argv[0] in ("-h", "--help"):
        p.print_help()
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        p.print_usage(sys.stderr)
        print(f"trialmindkg: unknown command {cmd!r}; one of: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    kind, target, _ = COMMANDS[cmd]
    old = sys.argv
    try:
        if kind == "file":
            path = pipelines_dir() / target
            sys.argv = [str(path), *rest]
            runpy.run_path(str(path), run_name="__main__")
        else:
            sys.argv = [target, *rest]
            runpy.run_module(target, run_name="__main__", alter_sys=True)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old
    return 0


if __name__ == "__main__":
    sys.exit(main())
