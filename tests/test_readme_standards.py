"""PK5: the README's Standards section carries the brief's three IRIs in full, each as a link to itself."""
import re
from pathlib import Path

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
IRIS = ["http://purl.obolibrary.org/obo/SO_0001217", "http://purl.obolibrary.org/obo/PR_000000001",
        "http://purl.obolibrary.org/obo/so#translates_to"]


def standards_section():
    m = re.search(r"^## Standards\n(.*?)(?=^## )", README, re.M | re.S)
    assert m, "README has no Standards section"
    return m.group(1)


def test_the_three_iris_appear_in_full_as_links():
    sec = standards_section()
    for iri in IRIS:
        assert f"[{iri}]({iri})" in sec, iri
