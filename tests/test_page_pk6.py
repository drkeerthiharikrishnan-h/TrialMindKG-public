"""PK6 (13 Nov): links and words (docs/packets_2026-11-11/PK6_links_and_words.md; review findings U7, U10, U15, ES-03, ES-04,
ES-07, ES-13, ES-14, ES-17).

Server (no browser):
  - source_url builds the source's own page of a row from the row's own ids only (ES-04), explain_edge carries it with the
    release in words; the timeline dates every layer and parses first_year '2018.0' (ES-14); its labels name no layer code;
  - routes_predictions: strength bands from the committed predictions file and the meta-path benchmark line (ES-13).
Page functions (node, no jsdom): the prefix -> URL map, BioLink IRIs, say(), shapeWords(), relWords(), labelOf(), axiomOf().
Rendered (node + jsdom + the real app over bridge.py, on the Pharos test fixture plus a ChEMBL 37 layer and meta-path
predictions; skipped without jsdom):
  - R11 (U7): no file path, run tag, graph code, packet id, layer code, raw BioLink enum value or model-operations word in the
    (PK15, owner direction 14 Nov: the sponsor names "NVIDIA NIM", "Amazon Neptune", "Amazon CloudFront" and "AWS" are allowed;
    "cluster", "serverless", "NCU" and the operations sense of "endpoint" / "instance" (a Neptune or server endpoint, an EC2
    instance) are refused, see test_r11_allows_the_four_sponsor_names_only; a trial's "primary endpoint" is clinical wording)
    visible text or tooltips of any view (developer folds on the labelled For judges pages, [data-dev], are excluded);
  - the card: ids link to their authority, the predicate and the class to their BioLink IRI, a mapping link says "not scored",
    a prediction has a strength word, "predicted, not yet reported", how to test it and the model's benchmark line;
  - "why this link": reader labels, -log10 p for TIGA, the source's page or "not in source" with a PubMed search, a dated
    timeline, "unnamed compound (CHEMBL...)";
  - a trial evidence record links the trial's registry page instead of printing the frozen response's path (ES-17);
  - the click gate fails a tested question whose answer has no rows (U15).
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import pharos_app  # noqa: E402
import routes_predictions as RP  # noqa: E402
from fixtures.pharos_fixture import build, edge, write, GENE, PROT, DIS1, DIS2, CMP  # noqa: E402

PAGE = ROOT / "pipelines" / "app_page_v3.html"
T = PAGE.read_text(encoding="utf-8")
H = ROOT / "docs" / "ux" / "harness_v3"
JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node")
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
needs_node = pytest.mark.skipif(not NODE, reason="node not available")
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(), reason="node + jsdom not available")
UNNAMED = "CHEMBL.COMPOUND:CHEMBL4462318"


# ------------------------------------------------------------------ server: source pages, timeline, prediction scale
def test_source_url_is_built_from_the_rows_own_ids():
    su = pharos_app.source_url
    assert su(dict(source="Open Targets 26.09 overall direct", subject="NCBIGene:1956", object="MONDO:0021042",
                   source_record="associationByOverallDirect|ENSG00000146648|EFO_0005543")) == \
        "https://platform.opentargets.org/evidence/ENSG00000146648/EFO_0005543"
    assert su(dict(source="Open Targets 26.09 overall direct", subject="ENSEMBL:ENSG00000146648", object="MONDO:0004975")) == \
        "https://platform.opentargets.org/evidence/ENSG00000146648/MONDO_0004975"
    assert su(dict(source="Open Targets 26.09 drug mechanism", subject="CHEMBL.COMPOUND:CHEMBL521", object="NCBIGene:5743")) == \
        "https://platform.opentargets.org/drug/CHEMBL521"
    assert su(dict(source="ChEMBL 37 activity", subject="CHEMBL.COMPOUND:CHEMBL521", object="UniProtKB:P35354")) == \
        "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL521/"
    assert su(dict(source="DrugCentral", subject="CHEMBL.COMPOUND:CHEMBL25", object="UniProtKB:P23219", source_record="ligands|CHEMBL25|2244|P23219")) == \
        "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL25/"
    assert su(dict(source="Gene Ontology annotation", subject="UniProtKB:P35354", object="GO:0006954")) == \
        "https://www.ebi.ac.uk/QuickGO/annotations?geneProductId=P35354&goId=GO:0006954"
    assert su(dict(source="Reactome", subject="UniProtKB:P35354", object="REACT:R-HSA-2162123")) == "https://reactome.org/content/detail/R-HSA-2162123"
    assert su(dict(source="KEGG", subject="UniProtKB:P35354", object="KEGG.PATHWAY:hsa00590")) == "https://www.kegg.jp/pathway/hsa00590"
    assert su(dict(source="WikiPathways", subject="UniProtKB:P35354", object="WIKIPATHWAYS:WP98")) == "https://www.wikipathways.org/pathways/WP98.html"
    assert su(dict(source="TIGA (GWAS Catalog)", subject="NCBIGene:1956", object="MONDO:0021042", source_record="tiga|P00533|EFO_0005543")) == \
        "https://unmtid-shinyapps.net/shiny/tiga/?trait=EFO_0005543"
    assert su(dict(source="STRINGDB", subject="UniProtKB:P00533", object="UniProtKB:P04626", source_record="ppi|P00533|P04626|STRINGDB")) == \
        "https://string-db.org/cgi/network?identifiers=P00533%0dP04626&species=9606"
    assert su(dict(source="UniProt Disease", subject="UniProtKB:P00533", object="MONDO:0005233")) == "https://www.uniprot.org/uniprotkb/P00533/entry#disease_variants"
    assert su(dict(source="CTD", subject="NCBIGene:5743", object="MESH:D000544")) == "https://ctdbase.org/detail.go?type=gene&acc=5743&view=disease"
    # never guessed: no id of the right shape, no link; a source without a row page, no link
    assert su(dict(source="Gene Ontology annotation", subject="NCBIGene:5743", object="GO:0006954")) is None
    assert su(dict(source="TIGA (GWAS Catalog)", subject="NCBIGene:1956", object="MONDO:0021042")) is None
    assert su(dict(source="DisGeNET", subject="NCBIGene:5743", object="MONDO:0004975")) is None
    assert su(dict(source="ChEMBL 37 activity", subject="CHEMBL.COMPOUND:CHEMBL521<script>", object="UniProtKB:P35354")) is None


def test_release_words_and_year_of():
    assert pharos_app.release_words("pharos", "pharos319") == "Pharos 3.19.2"
    assert pharos_app.release_words("opentargets_layer", "26.09 (direct associations, score >= 0.1)") == "Open Targets 26.09"
    assert pharos_app.release_words("chembl37", "37") == "ChEMBL 37"
    assert pharos_app.release_words("hpo_hierarchy", "download 2026-10-25 (release IRI in data/ontologies/hpo/version.txt)") == "HPO, downloaded 2026-10-25"
    assert pharos_app.release_words("other", "v2 (see data/x.txt)") == "v2" and pharos_app.release_words("other", "data/x.txt") == ""
    assert [pharos_app.year_of(x) for x in ("2018", "2018.0", 2018.0, "", None, "n/a", "12")] == ["2018", "2018", "2018", None, None, None, None]
    # every layer of the shipped graph is dated (ES-14): the release month, or the download month of the hierarchies
    assert set(pharos_app.LAYERS) <= set(pharos_app.RELEASE_DATES)


def extended_fixture(root):
    """The Pharos test fixture plus: a ChEMBL 37 layer (an activity of an unnamed compound with first_year '2018.0', a
    mechanism row with no first year) and a meta-path predictions file with one novel prediction and its path."""
    root = build(root)
    (root / "sources" / "chembl37.yaml").write_text("name: chembl37\n")
    nodes = pd.DataFrame([dict(id=PROT, category="biolink:Protein", name="Prostaglandin G/H synthase 2")])
    edges = pd.DataFrame([
        edge(201, UNNAMED, "biolink:directly_physically_interacts_with", PROT, "ChEMBL 37 activity", "chembl37", "37",
             source_record="activities|CHEMBL4462318|P35354", pchembl_max="7.1", n_activities="2", first_year="2018.0"),
        edge(202, CMP, "biolink:directly_physically_interacts_with", PROT, "ChEMBL 37 mechanism", "chembl37", "37",
             source_record="mechanisms|CHEMBL521|P35354|Cyclooxygenase inhibitor")])
    edges["source_record"] = ["activities|CHEMBL4462318|P35354", "mechanisms|CHEMBL521|P35354|Cyclooxygenase inhibitor"]
    write(root / "data" / "kg" / "chembl37", "nodes", nodes)
    write(root / "data" / "kg" / "chembl37", "edges", edges[pharos_app.KGX + ["pchembl_max", "n_activities", "first_year"]])
    path = [dict(shape="Gene -genetically_associated_with-> Disease <-genetically_associated_with- Gene -genetically_associated_with-> DiseaseOrPhenotypicFeature",
                 count=1, via="NCBIGene:6647", nodes=[GENE, DIS1, "NCBIGene:6647", "EFO:0004611"],
                 edges=[dict(id="e0000000000000008", subject=GENE, predicate="genetically_associated_with", object=DIS1),
                        dict(id="e0000000000000007", subject="NCBIGene:6647", predicate="genetically_associated_with", object="EFO:0004611")])]
    write(root / "outputs" / "linkpred", "predictions_metapath", pd.DataFrame([
        dict(graph="G3", task="B", subject=GENE, object="EFO:0004611", score="11.4868", rank="1", by="subject", model="metapath", paths=json.dumps(path)),
        dict(graph="G3", task="B", subject=GENE, object=DIS2, score="3.1", rank="2", by="subject", model="metapath", paths="[]")]))
    return root


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return extended_fixture(tmp_path_factory.mktemp("pk6"))


def test_explain_has_the_source_page_the_release_and_a_dated_timeline(fx):
    g = pharos_app.PharosGraph(root=fx)
    act = g.explain_edge("e00000000000000c9")                     # the ChEMBL 37 activity, first_year '2018.0'
    assert act["source_url"] == "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL4462318/" and act["release"] == "ChEMBL 37"
    own = [e for e in act["timeline"]["events"] if e["event"] == "first_seen"]
    assert own and own[0]["date"] == "2018" and own[0]["source"] == "ChEMBL 37 activity, first reported"
    mech = g.explain_edge("e00000000000000ca")                    # no first year: dated by ChEMBL 37's release month
    assert [e["date"] for e in mech["timeline"]["events"] if e["event"] == "first_seen"] == ["2026-05"]
    assert mech["timeline"]["summary"]["first_seen"] is not None
    for eid in ("e0000000000000001", "e0000000000000065", "e00000000000000c9"):
        for ev in g.explain_edge(eid)["timeline"]["events"]:
            assert not re.search(r"opentargets_layer|chembl37|pharos319|_hierarchy|/", ev["source"]), ev["source"]


def test_prediction_scale_bands_and_benchmark_line():
    assert RP.score_bands([1, 2, 3, 4, "x", None, float("nan")]) == dict(n=4, strong=3.7, moderate=2.5)
    assert RP.score_bands([]) == dict(n=0, strong=None, moderate=None)
    cut = dict(strong=9.0, moderate=4.0)
    assert [RP.band(x, cut) for x in (9.0, 8.99, 4.0, 1, "x")] == ["strong", "moderate", "moderate", "weak", None]
    assert RP.band(5, dict(strong=None, moderate=None)) is None
    text = ("| G2@diag_cs9_metapath | 5 | +0.1136 | +0.1071 to +0.1201 | 5 | yes |\n| G3@diag_release_metapath | 5 | +0.1103 | +0.1050 to +0.1155 | 5 | yes |\n"
            "| G3@diag_cs9_metapath | 5 | +0.1132 | +0.1063 to +0.1200 | 5 | yes |\n")
    assert RP.benchmark_line(text, "G3") == dict(mean=0.1132, ci_low=0.1063, ci_high=0.12, n=5, wins=5, same_pairs=True)   # the shipped graph's run
    assert RP.benchmark_line(text, "G0") is None
    s = RP.prediction_scale(ROOT)                                 # the committed files
    assert s["n"] == 277490 and s["cut"]["strong"] > s["cut"]["moderate"] and s["model"] == "metapath" and s["graph"] == "G3"
    assert s["benchmark"]["mean"] == 0.1132 and "+0.113 AUROC over Pharos alone" in s["sentence"] and "277,490" in s["sentence"]
    assert not re.search(r"\bG[0-4]\b|\.tsv|\.json|\.md", s["sentence"])
    from fastapi.testclient import TestClient
    import app as appmod
    assert TestClient(appmod.app).get("/api/pharos/prediction_scale").json()["n"] == 277490


# ------------------------------------------------------------------ page functions (node)
def block(name_start, name_end):
    return T[T.index(name_start):T.index(name_end)]


def node_eval(expr):
    """Runs the PK6 block of the page in node with the few page globals it needs; returns JSON.parse of expr's value."""
    pk6 = block("// ================================================================ PK6 (13 Nov)", "// ================================================================ end of the PK6 block")
    pre = r"""
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const short = c => String(c || "").split(":").pop();
const W = {predicates: {genetically_associated_with: "is genetically associated with", physically_interacts_with: "physically interacts with",
  gene_associated_with_condition: "is associated with"}, knowledge_levels: {knowledge_assertion: "curated statement"}, categories: {Gene: "gene", Disease: "disease"}, confidence_words: {}};
const noun = c => W.categories[short(c)] || short(c).replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
const LAYER_NAMES = {pharos: "Pharos", dto: "the Drug Target Ontology", opentargets_layer: "Open Targets", chembl37: "ChEMBL", hpo_hierarchy: "HPO", go_hierarchy: "GO", mondo_hierarchy: "MONDO"};
const document = {addEventListener() {}}; const fetch = () => Promise.reject(new Error("no server")); let LASTCARD = null; const $ = () => null;
function parseHop(st) { const m = /^(.*?) -(.+?)-> (.*)$/.exec(st || ""); return m ? {a: m[1], p: m[2], b: m[3]} : {a: st, p: "", b: ""}; }
const predWords = p => { const m = /^(\w+)(.*)$/.exec(p || ""); return m ? (W.predicates[m[1]] || m[1].replace(/_/g, " ")) + m[2] : p; };
"""
    code = pre + pk6 + f"\nconsole.log(JSON.stringify({expr}));"
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout)


@needs_node
def test_identifier_links_point_to_the_authority():
    ids = ["NCBIGene:1956", "HGNC:3236", "ENSEMBL:ENSG00000146648", "UniProtKB:P00533", "CHEMBL.COMPOUND:CHEMBL521", "PUBCHEM.COMPOUND:2244",
           "MONDO:0005148", "HP:0001370", "GO:0006954", "EFO:0004611", "REACT:R-HSA-2162123", "DTO:00000045", "infores:disgenet", "NCT:NCT02446912",
           "NCT02446912", "MESH:D000544", "P19", "tmkg:trialmindkg", "NCBIGene:1956<script>", ""]
    got = node_eval("[" + ",".join(f"idUrl({json.dumps(i)})" for i in ids) + "]")
    want = ["https://www.ncbi.nlm.nih.gov/gene/1956", "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:3236",
            "https://www.ensembl.org/id/ENSG00000146648", "https://www.uniprot.org/uniprotkb/P00533/entry", "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL521/",
            "https://pubchem.ncbi.nlm.nih.gov/compound/2244", "https://purl.obolibrary.org/obo/MONDO_0005148", "https://purl.obolibrary.org/obo/HP_0001370",
            "https://www.ebi.ac.uk/QuickGO/term/GO:0006954", "https://bioregistry.io/EFO:0004611", "https://reactome.org/content/detail/R-HSA-2162123",
            "https://bioregistry.io/DTO:00000045", "https://bioregistry.io/infores:disgenet", "https://clinicaltrials.gov/study/NCT02446912",
            "https://clinicaltrials.gov/study/NCT02446912", "https://meshb.nlm.nih.gov/record/ui?ui=D000544", None, None, None, None]
    assert got == want
    tag, plain, iri, cls, other = node_eval('[idLink("NCBIGene:1956"), idLink("P19"), biolinkIri("biolink:has_gene_product"), biolinkIri("biolink:Gene"), biolinkIri("tmkg:ClinicalTrialProgram")]')
    assert tag.startswith('<a class="idtag" href="https://www.ncbi.nlm.nih.gov/gene/1956" target="_blank" rel="noopener"') and plain == '<span class="idtag">P19</span>'
    assert iri == "https://w3id.org/biolink/vocab/has_gene_product" and cls == "https://w3id.org/biolink/vocab/Gene" and other is None
    # one function reaches every id tag on the page (U10, ES-03)
    assert "const idtag = id => idLink(id);" in T and T.count("<span class=\"idtag\">${esc(id)}</span>") == 1


@needs_node
def test_reader_words():
    why = ["A multi-hop question: two disease associations joined on the gene, each cited. MONDO ids are from data/reference/mondo_terms.tsv.",
           "Drug -> target -> GO process. CHEMBL521 is ibuprofen in data/reference/chembl_synonyms.tsv.gz. The GO hierarchy layer (L2) lets the app walk up.",
           "With opentargets_layer removed, 89.6% of the gain remains.", "The pruned graph with the trial layer (G4) as Neptune load files",
           "predicted links with scores (linkpred/predict.py)", "Test items: the curated Pharos knowledge_assertion edges",
           "**A language model is used in one step.** Without NVIDIA_API_KEY the offline stand-in answers."]
    out = node_eval("[" + ",".join(f"say({json.dumps(w)}, {'true' if 'G4' in w else 'false'})" for w in why) + "]")
    bio = node_eval('[say("Enriched in the G2/M transition of mitotic cell cycle and the G0 to G1 transition."), say("G2/M", true)]')
    assert bio == ["Enriched in the G2/M transition of mitotic cell cycle and the G0 to G1 transition.", "the agent-built graph/M"]   # biology is left alone
    assert out[0] == "A multi-hop question: two disease associations joined on the gene, each cited."
    assert out[1] == "Drug -> target -> GO process. The GO hierarchy layer lets the app walk up."
    assert out[2] == "With Open Targets removed, 89.6% of the gain remains." and out[3] == "The pruned graph with the trial layer as Neptune load files"
    assert out[4] == "predicted links with scores" and out[5] == "Test items: the curated Pharos curated statement edges"
    assert out[6] == "A language model is used in one step. Without the model key the fixed-rule answerer answers."
    shape, sent, rel, lab, ax = node_eval(
        '[shapeWords("Gene -physically_interacts_with^-1@product-> Gene -gene_associated_with_condition_context-> Disease <-subclass_of- Disease"), '
        'pathSentence({edges: [{statement: "EGFR -physically_interacts_with-> NDRG1"}, {statement: "NDRG1 -gene_associated_with_condition-> glioma"}]}), '
        '[relWords("pharos", "pharos319"), relWords("opentargets_layer", "26.09"), relWords("go_hierarchy", "download 2026-10-25 (release IRI in data/ontologies/go/version.txt)"), relWords("x", "data/a.tsv")], '
        '[labelOf("CHEMBL.COMPOUND:CHEMBL4462318", "CHEMBL.COMPOUND:CHEMBL4462318"), labelOf("NCBIGene:1", "EGFR"), labelOf("MONDO:1", "")], '
        '[axiomOf("biolink:has_gene_product"), axiomOf("biolink:subclass_of"), axiomOf("biolink:member_of"), axiomOf("biolink:part_of"), axiomOf("biolink:gene_associated_with_condition")]]')
    assert shape == ("gene (protein) ← physically interacts with ← gene → is associated with (statistical or unspecified level) → disease ← subclass of ← disease")
    assert "^-1" not in shape and "@product" not in shape and "_context" not in shape
    assert sent == "EGFR physically interacts with NDRG1; NDRG1 is associated with glioma."
    assert rel == ["Pharos 3.19.2", "Open Targets 26.09", "GO, downloaded 2026-10-25", ""]
    assert lab == ["unnamed compound (CHEMBL4462318)", "EGFR", "MONDO:1"]
    assert ax == ["identifier mapping", "ontology axiom", "ontology axiom", "ontology axiom", None]


def test_no_backend_switch_status_banner_or_timing_line_on_the_page():
    js = T[T.index("<script>"):]
    assert '<label for="backendsel">' not in T and "(not available)" not in js            # the switch left the page (U7)
    assert "card ready in" not in js and "answered from the card index" not in js
    assert "Pharos graph: ${num(s.nodes)}" not in js and "share of links that pass the schema check" not in js
    assert "Reading docs/AGENT_LOOP.md" not in js and "Counts from <span" not in js
    assert "G3/B" not in js and "${esc(x.graph)}/${esc(x.task)}" not in js                 # prediction lines (ES-13)
    assert 'data-dev><summary>For developers' in js                                       # repository references only in labelled folds


# ------------------------------------------------------------------ rendered views (jsdom + the real app)
R11_JS = r"""(() => {
  const PAT = {graph_code: /\bG[0-4]R?\b/g, run_tag: /\bfrozen_[a-z]\b|\bkfold\b|@diag|\bdiag_\w+|\bcs\d\b/g,
    file_path: /(?:\b[\w.-]+\/)+[\w.-]*\.(?:json|tsv|gz|md|py|yaml|csv|txt|owl|js|html|pt)\b|\b[\w@-]+\.(?:json|tsv|gz|py|yaml|owl|pt|md)\b|\b(?:data|outputs|docs|pipelines|tests|sources|examples)\/[\w./-]+/g,
    packet_id: /\b(?:CC|CS)-R?\d+[a-z]?\b|\bPK\d+[AB]?\b/g,
    layer_code: /\b(?:opentargets_layer|chembl37|hpo_hierarchy|go_hierarchy|mondo_hierarchy|pharos319)\b/g,
    raw_token: /\b(?:knowledge_assertion|statistical_association|text_co_occurrence|not_provided|manual_agent|automated_agent|source_identifier|primary_knowledge_source|aggregator_knowledge_source|source_record|source_version)\b/g,
    env_var: /\b[A-Z][A-Z0-9]+_[A-Z0-9_]*(?:KEY|ROOT|INDEX|BACKEND|MODE|LIMIT)\b/g,
    model_ops: /\bnim\b|(?<!NVIDIA )\bNIM\b|stand-in|snapshot mode|\bfallback\b|index v3|\bbackend\b|card index|\bdigest\b|\bledger\b|Re-run live|\(not available\)/g,
    aws_ops: /\b(?:Neptune|database|DB|graph|AWS|Amazon)\s+clusters?\b|\bclusters?\s+(?:endpoint|state|id|capacity)\b|\bserverless\b|\bNCUs?\b|\b(?:Neptune|database|graph|query|server|HTTPS?|API|AWS|EC2|openCypher)\s+(?:endpoints?|instances?)\b|\b(?:endpoints?|instances?)\s+(?:URL|address|host|name|id|type)\b|\bt3\.\w+|\bdb\.serverless\b/gi,
    notation: /\^-1|@product|_context->/g};
  const out = [];
  const skip = el => { for (let e = el; e && e !== document.body; e = e.parentElement) { if (e.hidden || (e.dataset && e.dataset.dev !== undefined) || /^(SCRIPT|STYLE)$/.test(e.tagName)) return true; } return false; };
  const scan = (t, where) => { for (const [k, re] of Object.entries(PAT)) for (const m of String(t).matchAll(re)) out.push(k + ": " + m[0] + " | " + where + String(t).trim().slice(0, 60)); };
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) if (n.nodeValue.trim() && !skip(n.parentElement)) scan(n.nodeValue, "");
  document.querySelectorAll("[title],[aria-label]").forEach(el => { if (!skip(el)) ["title", "aria-label"].forEach(a => el.getAttribute(a) && scan(el.getAttribute(a), "@" + a + " ")); });
  return JSON.stringify([out.length, ...[...new Set(out)].slice(0, 12)]);      // the page runner keeps 2,000 characters of an eval
})()"""


@needs_node
def test_r11_allows_the_four_sponsor_names_only():
    """PK15: the R11 patterns let exactly "NVIDIA NIM", "Amazon Neptune", "Amazon CloudFront" and "AWS" through, and still refuse
    "cluster", "endpoint" (a Neptune endpoint), "serverless", an EC2 "instance", "NCU" and a bare "NIM"."""
    pats = R11_JS[R11_JS.index("const PAT = {") + len("const PAT = "):R11_JS.index("};") + 1]
    js = (f"const PAT = {pats};\n"
          "const hits = t => Object.entries(PAT).flatMap(([k, re]) => [...String(t).matchAll(re)].map(m => k + ':' + m[0]));\n"
          "console.log(JSON.stringify(process.argv.slice(1).map(hits)));")
    texts = ["Models: NVIDIA NIM. Graph database: Amazon Neptune. Delivery: Amazon CloudFront. Hosted on AWS.",
             "Same question on Amazon Neptune: 37 rows in 1.2 s; 37 of 37 evidence links identical.",
             "the Neptune cluster", "the Neptune endpoint", "Neptune Serverless", "the EC2 instance", "8 NCU", "asked NIM",
             "Why did TULIP-1 miss its primary endpoint?", "iron-sulfur cluster binding"]
    out = json.loads(subprocess.run([NODE, "-e", js, "--", *texts], capture_output=True, text=True, check=True).stdout)
    assert out[0] == [] and out[1] == []
    assert out[2] == ["aws_ops:Neptune cluster"] and out[3] == ["aws_ops:Neptune endpoint"] and out[4] == ["aws_ops:Serverless"]
    assert out[5] == ["aws_ops:EC2 instance"] and out[6] == ["aws_ops:NCU"] and out[7] == ["model_ops:NIM"]
    assert out[8] == []                                    # the clinical "primary endpoint" is not an operations word
    assert out[9] == []                                    # nor a biological cluster (a GO term name)


def run_view(fx_root, acts, where=""):
    """Runs the front page in jsdom against the real app (bridge.py) with the fixture as the Pharos graph and the offline
    model. Takes the actions (run_page_v3.js format). Returns (evals, errors)."""
    env = dict(os.environ, NODE_USE_ENV_PROXY="1", NODE_PATH=str(JSDOM_DIR / "node_modules"), TMKG_LLM_BACKEND="offline",
               TMKG_PHAROS_ROOT=str(fx_root), TMKG_PHAROS_INDEX="off", TMKG_NIM_PREFLIGHT="off", TMKG_WARM="off")
    with tempfile.TemporaryDirectory() as td:     # actions go in a file, not on the command line (see run_page_v3.js)
        acts_file = Path(td) / "acts.json"
        acts_file.write_text(json.dumps(acts), encoding="utf-8")
        r = subprocess.run([NODE, str(H / "run_page_v3.js"), PY, str(H / "bridge.py"), "/", where, "@" + str(acts_file)],
                           capture_output=True, text=True, timeout=400, cwd=str(JSDOM_DIR), env=env)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:                   # node died before printing its JSON: say why instead of a bare decode error
        pytest.fail(f"page runner printed no JSON (exit code {r.returncode}; -9 = killed by the system or a sandbox). "
                    f"node stderr: {r.stderr[-3000:] or '(empty)'} | stdout: {r.stdout[:500] or '(empty)'}")
    assert "harness_error" not in d, d.get("harness_error")
    return d["evals"], d["errors"]


CHOOSE = {"click": '.persona[data-id="physician_scientist"]', "wait": 1500}
R11 = {"eval": R11_JS, "wait": 100}
CARD = [CHOOSE, {"type": ["#pq", "PTGS2"]}, {"submit": "#pf"}, {"waitFor": "#pcardsec", "wait": 2500}]


@needs_jsdom
def test_r11_no_developer_words_in_any_view(fx):
    """U7: the plan's R11 grep (FINAL_SPRINT_PLAN R11; the review's patterns) over the rendered text and tooltips of every view."""
    runs = {
        "first screen": [{"wait": 1500}, R11],
        "pharos tab, card, why, tested questions": [CHOOSE, {"click": "#tabPharos", "wait": 1200}, R11, *CARD[1:], {"click": "#plist button", "wait": 800}, R11,
            {"click": "#pcardsec a.why", "wait": 1500}, R11, {"click": "#dclose", "wait": 400},
            {"click": '#pstrip [data-demo="pharos_demo_1"]'}, {"waitFor": "#pansw .bottomline", "wait": 1200}, R11,
            {"click": '#pstrip [data-demo="pharos_demo_3"]'}, {"waitFor": "#pansw .bottomline", "wait": 1200}, R11],
        "trial report, evidence record, explorer": [{"click": "#seeworked"}, {"waitFor": "#out .bottomline", "timeout": 60000, "wait": 1500}, R11,
            {"click": "#out .claim a.chip", "wait": 1500}, R11, {"click": "#dclose", "wait": 400}, {"click": "#tabExplore", "wait": 300},
            {"type": ["#sq", "TULIP"]}, {"submit": "#sf", "wait": 800}, {"click": "#results div[data-id]", "wait": 1500}, R11],
        "info tabs": [CHOOSE, *[x for t in ("Numbers", "Build", "Generalize", "Judges", "Downloads", "About") for x in ({"click": f"#tab{t}", "wait": 2500}, R11)]],
    }
    problems = {}
    for name, acts in runs.items():
        evals, errors = run_view(fx, acts)
        assert errors == [], (name, errors)
        assert len(evals) == sum(1 for a in acts if a is R11), (name, evals)
        hits = [h for e in evals for h in json.loads(e)[1:]]
        if hits:
            problems[name] = hits
    assert problems == {}, json.dumps(problems, indent=1)


@needs_jsdom
def test_card_links_mapping_rows_and_prediction_words(fx):
    ev = ("JSON.stringify({ids: [...document.querySelectorAll('#pcardsec h2 a.idtag')].map(a => a.href), cls: [...document.querySelectorAll('#pcardsec a.iri')].map(a => a.href).slice(0, 40),"
          " mapping: [...document.querySelectorAll('#pcardsec tr')].filter(r => /PTGS2/.test(r.textContent) && /Prostaglandin/.test(r.textContent) && r.querySelector('a.why')).map(r => r.cells[3].textContent.replace(/\\s+/g, ' ')),"
          " plist: [...document.querySelectorAll('#plist li')].map(l => l.textContent.replace(/\\s+/g, ' ')).slice(0, 2), chain: document.querySelector('#pchain').textContent.replace(/\\s+/g, ' ').slice(0, 900),"
          " pubmed: [...document.querySelectorAll('#pchain a.ext')].map(a => a.href.slice(0, 40)), scale: (document.querySelector('.predscale') || {textContent: ''}).textContent.slice(0, 400),"
          " end: 1})")
    evals, errors = run_view(fx, CARD + [{"wait": 1500}, {"eval": ev}])
    assert errors == []
    c = json.loads(evals[0])
    assert "https://www.ncbi.nlm.nih.gov/gene/5743" in c["ids"]                                         # U10
    assert "https://w3id.org/biolink/vocab/Gene" in c["cls"] and "https://w3id.org/biolink/vocab/has_gene_product" in c["cls"]   # ES-03
    assert c["mapping"] and all(r.strip() == "not scored: identifier mapping" for r in c["mapping"])      # ES-07: no confidence shown
    assert c["plist"][0].startswith("1low density lipoprotein cholesterol measurement") and "predicted, not yet reported" in c["plist"][0]
    assert "strong" in c["plist"][0]                                                                   # 11.49 is above the 90th percentile
    ch = c["chain"]
    assert "predicted, not yet reported" in ch and "How to test" in ch and "strong" in ch and "score 11.49" in ch
    assert "PTGS2 is genetically associated with Alzheimer disease; SOD1 is genetically associated with low density lipoprotein cholesterol measurement." in ch
    assert "pattern: gene → is genetically associated with → disease ← is genetically associated with ← gene" in ch and "^-1" not in ch
    assert any(u.startswith("https://pubmed.ncbi.nlm.nih.gov/?term=") for u in c["pubmed"])
    assert "meta-path model" in c["scale"] and "+0.113 AUROC over Pharos alone" in c["scale"] and "277,490" in c["scale"]   # ES-13


@needs_jsdom
def test_why_this_link_in_reader_words(fx):
    ev = ("JSON.stringify({prov: [...document.querySelectorAll('#pexplain table.prov tr')].map(r => r.cells[0].textContent + ': ' + r.cells[1].textContent.trim()),"
          " values: [...document.querySelectorAll('#pexplain h3 + table:not(.prov) tr, #pexplain table:not(.prov) tr')].map(r => r.cells[0].textContent + ' ' + r.cells[1].textContent).slice(0, 8),"
          " papers: (document.querySelector('#pexplain .papers') || {textContent: ''}).textContent.replace(/\\s+/g, ' '), lead: document.querySelector('#pexplain .lead').textContent,"
          " verdict: document.querySelector('#pexplain .lead ~ p') ? document.querySelector('#pexplain .lead ~ p').textContent.slice(0, 120) : '',"
          " tl: (document.querySelector('#pexplain .tlsum') || {textContent: ''}).textContent.replace(/\\s+/g, ' '), events: [...document.querySelectorAll('#pexplain .tlsum ~ div')].map(d => d.textContent.replace(/\\s+/g, ' ').slice(0, 80)).slice(0, 4)})")
    links = "JSON.stringify([...document.querySelectorAll('#pexplain a')].map(a => [a.textContent.trim(), a.getAttribute('href')]).filter(x => !/^why$|raw record/.test(x[0])).slice(0, 14))"
    tiga = [CHOOSE, {"hash": "#pharos/node/NCBIGene%3A6647", "wait": 2500}, {"click": "#pcardsec a.why", "wait": 1500}, {"eval": ev}, {"eval": links}]
    evals, errors = run_view(fx, tiga)
    assert errors == []
    x, ls = json.loads(evals[0]), dict((a, b) for a, b in json.loads(evals[1]))
    assert "-log10 p (median) 1e-9" in x["values"] and "TIGA rank score (mean) 0.7" in x["values"] and not any("median_p_value" in v for v in x["values"])   # ES-17
    assert x["papers"].startswith("Papers: not in source") and any((h or "").startswith("https://pubmed.ncbi.nlm.nih.gov/?term=") for h in ls.values())   # ES-04
    assert {"Release: Pharos 3.19.2", "Knowledge level: curated statement", "Agent: curated by a person", "On the source's site: not in source"} <= set(x["prov"])
    assert x["tl"].startswith("first seen 2024-02 · last confirmed 2024-02") and not re.search(r"first seen\s*·", x["tl"])            # ES-14
    assert ls.get("NCBIGene:6647") == "https://www.ncbi.nlm.nih.gov/gene/6647" and ls.get("biolink:genetically_associated_with") == "https://w3id.org/biolink/vocab/genetically_associated_with"
    # an unnamed compound, a ChEMBL row dated by its own first year, the source's own page
    act = [CHOOSE, {"hash": "#pharos/node/UniProtKB%3AP35354", "wait": 2500},
           {"eval": "JSON.stringify([...document.querySelectorAll('#pcardsec td[data-l=source] a')].map(a => [a.textContent, a.getAttribute('href')]).filter(x => /ChEMBL 37/.test(x[0])))"}, {"eval": "(() => { const a = [...document.querySelectorAll('#pcardsec tr')].find(r => /unnamed compound/.test(r.textContent)); a.querySelector('a.why').click(); return 'ok'; })()", "wait": 1500},
           {"eval": ev}, {"eval": links}]
    evals, errors = run_view(fx, act)
    assert errors == [] and evals[1] == "ok"
    assert ["ChEMBL 37 activity", "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL4462318/"] in json.loads(evals[0])   # ES-04: the card row's source link
    x, ls = json.loads(evals[2]), dict((a, b) for a, b in json.loads(evals[3]))
    assert x["lead"].startswith("unnamed compound (CHEMBL4462318)") and "CHEMBL.COMPOUND:CHEMBL4462318 -" not in x["lead"]                     # ES-17
    assert ls.get("this row on ChEMBL 37 activity") == "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL4462318/"
    assert any(e.startswith("2018ChEMBL 37 activity, first reported") for e in x["events"]) and "best pChEMBL 7.1" in x["values"]            # ES-14
    # a mapping link: not scored, with the reason (ES-07)
    mapping = [CHOOSE, {"hash": "#pharos/node/NCBIGene%3A5743", "wait": 2500},
               {"eval": "(() => { const r = [...document.querySelectorAll('#pcardsec tr')].find(r => /not scored: identifier mapping/.test(r.textContent)); r.querySelector('a.why').click(); return 'ok'; })()", "wait": 1500}, {"eval": ev}]
    evals, errors = run_view(fx, mapping)
    v = json.loads(evals[1])["verdict"]
    assert errors == [] and v.startswith("not scored: identifier mapping") and "kept with confidence" not in v


@needs_jsdom
def test_trial_evidence_record_links_the_registry_not_the_frozen_file(fx):
    ev = ("JSON.stringify({text: document.querySelector('#pexplain').textContent.replace(/\\s+/g, ' '), links: [...document.querySelectorAll('#pexplain a')].map(a => a.getAttribute('href')),"
          " tags: [...document.querySelectorAll('#out .claim .tag')].map(t => t.textContent).filter(t => /id matching/.test(t)).slice(0, 3)})")
    evals, errors = run_view(fx, [{"click": "#seeworked"}, {"waitFor": "#out .bottomline", "timeout": 60000, "wait": 1500},
                                  {"eval": "(() => { [...document.querySelectorAll('#out .claim a.chip')].find(a => /ClinicalTrials|AACT|registry/i.test(a.textContent)) ? [...document.querySelectorAll('#out .claim a.chip')].find(a => /ClinicalTrials|AACT|registry/i.test(a.textContent)).click() : document.querySelector('#out .claim a.chip').click(); return 1; })()", "wait": 1500},
                                  {"eval": ev}])
    assert errors == []
    d = json.loads(evals[1])
    assert "data/snapshots" not in d["text"] and "Frozen raw response" not in d["text"]
    assert any(re.fullmatch(r"https://clinicaltrials\.gov/study/NCT\d{8}", h or "") for h in d["links"]), d["links"]
    assert d["tags"] and all(t == "id matching: ids given by the source itself" for t in d["tags"])


@needs_jsdom
def test_click_gate_fails_a_tested_question_with_no_rows(tmp_path):
    """U15: with SOD1's GWAS edge missing, the fifth tested question answers with no rows; the gate must fail on it."""
    root = build(tmp_path / "fx")
    p = root / "data" / "kg" / "pharos" / "edges.tsv.gz"
    e = pd.read_csv(p, sep="\t", dtype=str)
    with gzip.open(p, "wt") as f:
        e[e["id"] != "e0000000000000007"].to_csv(f, sep="\t", index=False)
    env = dict(os.environ, NODE_USE_ENV_PROXY="1", NODE_PATH=str(JSDOM_DIR / "node_modules"), TMKG_PHAROS_ROOT=str(root), TMKG_PHAROS_INDEX="off",
               TMKG_NIM_PREFLIGHT="off", TMKG_WARM="off", GATE_ONLY="first")
    out = tmp_path / "gate.json"
    r = subprocess.run([NODE, str(H / "click_gate.js"), PY, str(H / "bridge.py"), str(out)], capture_output=True, text=True, timeout=600, cwd=str(JSDOM_DIR), env=env)
    assert r.returncode == 1, r.stderr[-2000:]
    d = json.loads(out.read_text())
    bad = [x for x in d["rows"] if not x["pass"]]
    assert d["empty_answers"] == 1 and len(bad) == 1 and bad[0]["control"] == "#tqlist [data-tq] #5"
    assert bad[0]["why"].startswith("empty answer: a tested question") and bad[0]["rows"] == 0
    assert all(x["rows"] > 0 for x in d["rows"] if x["pass"] and x.get("answer_of") and x["outcome"] == "answered")


def test_demo_button_finishes_after_its_answers_are_drawn():
    """U15 follow-up (Mac, STRICT gate): "Run the 5 demo questions" must not report done before the answers are rendered."""
    body = T[T.index('$("#pdemo").addEventListener("click"'):T.index("// ---------------------------------------------------------------- failed-trial report")]
    assert body.index("for (const d of demos)") < body.index("st.done(") and body.count("st.done(") == 1


def test_the_briefs_three_iris_are_links():
    """NEXT_STEPS PK6 item: the brief's IRIs on the Numbers page link to brief_iris[].url (/api/results)."""
    assert '`<a class="ext" href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.iri)}</a>`' in T
