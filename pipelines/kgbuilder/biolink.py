"""
BioLink Model checks read directly from data/external/biolink/biolink-model.yaml (no hand-written lists), so any
source card can be validated: does the class / predicate exist, is the identifier prefix one BioLink lists for that
class, and do subject and object classes fit the predicate's domain and range (inherited through is_a / mixins).

BioLink(path) -> object with: has_class(curie), has_predicate(curie), ancestors(curie), domain(curie),
range(curie), prefixes(curie), check_edge_type(subject_cat, predicate, object_cat) -> list of problems.
CURIEs are written as in KGX: 'biolink:SmallMolecule', 'biolink:gene_associated_with_condition'.
"""
import re
from functools import lru_cache
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOWNLOADED = ROOT / "data" / "external" / "biolink" / "biolink-model.yaml"   # scripts/download_external_data.sh (git-ignored)
REFERENCE = ROOT / "data" / "reference" / "biolink-model.yaml"              # committed copy: the Docker image has it (CC-34)
DEFAULT = DOWNLOADED if DOWNLOADED.exists() else REFERENCE


def class_name(curie):
    """'biolink:SmallMolecule' -> 'small molecule' (the key used in biolink-model.yaml)."""
    local = curie.split(":", 1)[1]
    return re.sub(r"(?<!^)(?=[A-Z])", " ", local).lower()


def slot_name(curie):
    """'biolink:gene_associated_with_condition' -> 'gene associated with condition'."""
    return curie.split(":", 1)[1].replace("_", " ")


class BioLink:
    def __init__(self, path=DEFAULT):
        y = yaml.safe_load(Path(path).read_text())
        self.version = y.get("version")
        self.classes, self.slots = y["classes"], y["slots"]

    def has_class(self, curie):
        return class_name(curie) in self.classes

    def has_predicate(self, curie):
        return slot_name(curie) in self.slots

    @lru_cache(maxsize=None)
    def _ancestors(self, name):
        """Class names reachable through is_a and mixins, including itself."""
        c = self.classes.get(name) or {}
        out = {name}
        for parent in ([c["is_a"]] if c.get("is_a") else []) + (c.get("mixins") or []):
            out |= self._ancestors(parent)
        return frozenset(out)

    def ancestors(self, curie):
        return set(self._ancestors(class_name(curie)))

    def _inherited(self, curie, key):
        name = slot_name(curie)
        while name:
            s = self.slots.get(name) or {}
            if s.get(key):
                return s[key]
            name = s.get("is_a")
        return None

    def domain(self, curie):
        return self._inherited(curie, "domain")

    def range(self, curie):
        return self._inherited(curie, "range")

    def prefixes(self, curie):
        return (self.classes.get(class_name(curie)) or {}).get("id_prefixes") or []

    def check_edge_type(self, subject_cat, predicate, object_cat):
        """Problems with one (subject class, predicate, object class) combination; empty list = fine."""
        problems = [f"unknown class {c}" for c in (subject_cat, object_cat) if not self.has_class(c)]
        if not self.has_predicate(predicate):
            return problems + [f"unknown predicate {predicate}"]
        if problems:
            return problems
        dom, rng = self.domain(predicate), self.range(predicate)
        if dom and dom not in self.ancestors(subject_cat):
            problems.append(f"{predicate}: subject {subject_cat} is not a {dom}")
        if rng and rng not in self.ancestors(object_cat):
            problems.append(f"{predicate}: object {object_cat} is not a {rng}")
        return problems
