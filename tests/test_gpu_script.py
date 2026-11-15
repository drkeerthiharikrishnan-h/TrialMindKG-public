"""scripts/gpu_kge_run.sh: the D release step (pre-registered 7 Nov 12:30, ledger row D) runs the E008 GNN + paths command
unchanged except the graphs (final G3, G2; G0 reused) and the tag the ledger names."""
import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" / "gpu_kge_run.sh").read_text()


def _cmd(step):
    m = re.search(rf'^\s*{step}\) CMD="([^"]+)"', SCRIPT, re.M)
    assert m, f"no step {step}"
    return m.group(1)


def test_release_step_is_the_e008_command_on_the_final_graphs_with_the_preregistered_tag():
    rel, e008 = _cmd("release"), _cmd("gnnpath")
    assert "--graphs G3,G2 " in rel and "--tag diag_gnnpath_1m_release" in rel
    strip = lambda c: re.sub(r"--graphs \S+ |--tag \S+", "", c).split()
    assert strip(rel) == strip(e008)                        # same frozen flags, epochs, hidden, baselines, meta-paths
    assert "--epochs 300" in rel and "--hidden 64" in rel and "$FROZEN" in rel


def test_release_refuses_to_start_without_the_g0_graph_that_defines_the_test_pairs(tmp_path):
    """run.load_reference falls back to each graph's own pairs when outputs/g0/heterodata.pt is missing: D would then not be
    comparable with G0. The release step must stop instead."""
    import os, shutil, subprocess
    script = Path(__file__).resolve().parents[1] / "scripts" / "gpu_kge_run.sh"
    home = tmp_path / "home"; (home / "tmkg" / "outputs" / "g2").mkdir(parents=True); (home / "tmkg" / "outputs" / "g3").mkdir()
    for g in ("g2", "g3"):
        (home / "tmkg" / "outputs" / g / "heterodata.pt").write_bytes(b"x")
    bindir = tmp_path / "bin"; bindir.mkdir(); docker = bindir / "docker"
    docker.write_text("#!/bin/sh\necho docker-called >> \"$HOME/docker.log\"\n"); docker.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run([shutil.which("bash"), str(script), "release"], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "release needs outputs/g0/heterodata.pt" in r.stdout and not (home / "docker.log").exists()


def test_header_names_which_gpu_ran_which_run_as_the_ledger_does():
    """FIX6 S5: the header comment says L40S for E006 (5 Nov) and H100 for the 6, 8 and 9 Nov runs; each claim has its ledger row."""
    head = "\n".join(SCRIPT.splitlines()[:6])
    assert "L40S for E006" in head and "H100 for E009" in head and "behind the headline number" in head
    rows = (Path(__file__).resolve().parents[1] / "loop" / "ledger.tsv").read_text(encoding="utf-8").splitlines()
    assert any(r.startswith("2026-11-05\tE006") and "Brev L40S" in r for r in rows)
    assert any(r.startswith("2026-11-06\tE009") and "RESULT, Brev H100" in r for r in rows)
    assert any(r.startswith("2026-11-06\tE008") and "RESULT G3, Brev H100" in r for r in rows)
    assert any("\tD GNN+paths\t" in r and "H100" in r and "gpu_kge_run.sh release" in r for r in rows)
    assert any("\tD (CS-9 graphs)\t" in r and "H100" in r and "gpu_kge_run.sh cs9" in r for r in rows)
