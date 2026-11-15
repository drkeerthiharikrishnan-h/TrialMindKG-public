"""Checks for scripts/download_external_data.sh (no network needed)."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download_external_data.sh"


def test_script_has_valid_bash_syntax():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_script_writes_into_repo_by_default():
    text = SCRIPT.read_text()
    assert 'OUTPUT_DIR="${1:-$REPO_ROOT/data/external}"' in text


LAYERS = SCRIPT.parent / "download_latest_layers.sh"


def test_layers_script_syntax_and_appends_to_manifest():
    result = subprocess.run(["bash", "-n", str(LAYERS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    text = LAYERS.read_text()
    assert 'OUTPUT_DIR="${1:-$REPO_ROOT/data/external}"' in text
    assert '[ -f "$MANIFEST" ] ||' in text          # appends; never wipes rows written by the other script


def test_layers_script_skips_files_already_on_disk_and_has_sssom_fallback():
    text = LAYERS.read_text()
    assert '[ "$FORCE" != "1" ] && [ -s "$dest" ]' in text
    assert "raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings/mondo.sssom.tsv" in text


def test_chembl_step_is_portable_verified_and_resumable():
    # 2026-10-30 Mac run: bsdtar rejected --wildcards, the unpack failure hid tar's message and a half-written
    # database was left for chembl_prepare.py to trip over; a re-run would have re-downloaded 5.4 GB.
    text = SCRIPT.read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "--wildcards" not in code.replace('TAR_WILD="--wildcards"', "")    # flag only via the GNU-tar check
    assert "checksums.txt" in text and "checksum MISMATCH" in text
    assert 'reusing downloaded archive' in text
    assert 'rm -rf "$OUTPUT_DIR/chembl/${CHEMBL_RELEASE}"' in text
    assert 'exit 1' in text                                                    # a failed step fails the script
