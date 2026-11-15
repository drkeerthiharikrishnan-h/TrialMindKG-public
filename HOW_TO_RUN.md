# How to run, reproduce and verify TrialMindKG

Four commands, one job each. Every check in the repository, what it proves and how long it takes:
[docs/HARNESS.md](docs/HARNESS.md). Measured times: "cloud" = a Linux sandbox (CPU only, none of the large data);
"Mac" = the owner's machine with the full data.

## 1. Set up

Prerequisites:
- **Python 3.11** (`.python-version`; the lock file is generated and install-tested with 3.11; the cloud test run used 3.13)
- **GNU make**, **git**
- **Node.js 18 or newer** for the click-through harness (jsdom is installed by `make harness` into `/tmp/tmkg/jsdom`)
- **zstd** only to unpack the hosted card index (section 5)

```bash
python3.11 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.lock.txt
```

```bash
.venv/bin/pip install -r requirements-ml.txt
```

- `requirements.lock.txt`: every package at the exact version it was tested with (generated from a clean Python 3.11
  venv: `pip install -r requirements.txt` then `pip freeze`). `requirements.txt` is the short list of direct
  dependencies; regenerate the lock file whenever it changes.
- `requirements-ml.txt`: torch and torch_geometric 2.8.1, needed by `make demo` and the link-prediction steps only.
  torch is not locked because its build differs per platform (CPU, Apple silicon, CUDA).

**pip install** (`pyproject.toml`): `.venv/bin/pip install -e .` in a clone adds the `trialmindkg` command, which runs
the same scripts with the same arguments (`trialmindkg --help` lists them; `trialmindkg query program_trials pid=P19`
is `python pipelines/kg_query.py program_trials pid=P19`). `pip install .` or `pip install "git+<repository URL>"`
works too, with the dependencies of `requirements.txt` (the extra `.[ml]` adds `requirements-ml.txt`); it installs the
code, the page files, `personas/`, `static/` and `config/` only, not the data folders or the card index, so every step
below needs the clone. The import name is `pipelines`. `tests/test_pip_install.py` builds the wheel and installs it into
a fresh venv.

## 2. The four commands

### `make check` - tests, click-through, schema

Runs `make tests` (pytest), `make harness` (section `make harness` below) and `make schema`; stops at the first that
fails and exits non-zero. Ends with `make check: PASS (tests, harness, schema)`.

The schema step prints (cloud, 2 s):

```
WARNING schema: docs/SCHEMA_CARD.md differs from the layer summaries in outputs/ (regenerate with: python pipelines/schema_card.py)
schema check: 10 classes, 12 predicates, 0 non-BioLink terms
```

It fails only on a node class or predicate that is not a BioLink term (CLAUDE.md rule 6). The WARNING means the
schema card was written before the last layer rebuild; it goes away after `.venv/bin/python pipelines/schema_card.py`.

### `make demo` - a small graph end to end, with the frozen benchmark CLI

**Demo, not the reported numbers.** It builds the Pharos 3.19.2 rows of the four EGFR/ERBB receptor tyrosine kinases
(EGFR, ERBB2, ERBB3, ERBB4; committed in `tests/fixtures/demo/`, 1.2 MB, pulled as delivered by
`scripts/make_demo_fixture.py`), then runs the repository's own commands, unchanged: `build_kg.py` (Pharos and DTO
cards), `prune_kg.py`, `make_graphs.py G0,G2 --allow-missing`, `python -m pipelines.linkpred.run` with the frozen flags
(`scripts/frozen_flags.txt`) but 20 epochs instead of 300, and `python -m pipelines.linkpred.compare`. Here G0 = Pharos and
G2 = Pharos + the Drug Target Ontology classes (the other enrichment layers are not in the fixture; `g2_summary.md`
lists them as missing). Everything runs in a private copy under `outputs/demo/root/`, so your `data/` and `outputs/`
are never touched. The numbers in docs/EXPERIMENTS_LINKPRED.md come from the full 26-million-edge graphs and are
**not** reproduced by `make demo`.

Measured (cloud, Linux CPU; wall clock of `make demo`): 54 s, 49 s, 56 s (fresh venv on Python 3.11), 2 min 4 s (first
run after the container restarted: cold file cache, graphs step 54 s instead of 5 s), 59 s; owner's Mac: 0.6 min. Limit:
10 minutes. Repeat runs on one machine gave identical numbers; another machine can differ in the third decimal (torch
builds differ: the Mac printed G0 GNN 0.4287 / meta-path 0.6940 where Linux printed 0.4276 / 0.6913). Output of the
last cloud run (10 Nov 2026):

```
--> build pharos
--> build dto
--> prune pharos
--> prune dto
--> graphs G0,G2
--> benchmark (demo)
--> compare
DEMO (EGFR/ERBB fixture, reduced epochs): demo, not the reported numbers (those are in docs/EXPERIMENTS_LINKPRED.md)
G0: GNN AUROC 0.4276 | meta-path AUROC 0.6913 (coverage 55%) | test pairs per fold 24
G2: GNN AUROC 0.4958 | meta-path AUROC 0.6895 (coverage 55%) | test pairs per fold 24
only 24 test pairs per fold (< 200): these AUROCs are too noisy to compare graphs; the demo shows that every step runs
steps: build pharos 6s, build dto 1s, prune pharos 2s, prune dto 1s, graphs G0,G2 5s, benchmark (demo) 41s, compare 3s
total: 1.0 min | paired comparison: <checkout>/outputs/demo/compare.md
DEMO (EGFR/ERBB fixture, reduced epochs): demo, not the reported numbers (those are in docs/EXPERIMENTS_LINKPRED.md)
```

Files in `outputs/demo/`: `SUMMARY.txt`, `compare.md` (paired differences with 95% intervals), the two result JSONs,
`g0_summary.md` / `g2_summary.md` (14,303 nodes, 31,292 edges in G2), the build summaries and prune reports, `demo.log`.
`make clean-demo` removes the folder.

### `make harness` - the click-through of the app, offline

The jsdom click gate (118 clicks on `/` and `/v3`: every pill, chip, example, demo button and form must show a busy
state and finish) and then 12 browser journeys, all with the app running in-process and the offline stand-in model.
`GATE_LIVE=1` / `JOURNEYS_LIVE=1` use the configured model (NVIDIA NIM) instead. A pass prints `click_gate exit=0` and
`<journey> exit=0` for each of J1-J9, J7b, R1, R2. The run rewrites the result files in `docs/ux/harness_v3/`;
`git checkout -- docs/ux/harness_v3` restores the committed ones.

Measured: cloud 6 min 29 s. Output (cloud, without the Pharos card index):

```
click_gate exit=0          (click_gate.json: 118 of 118 passed)
J1_egfr_card_hover_slider exit=0
J2_ptgs2_demo_bottom_line exit=0
J3_typed_ask_abstain exit=0
J4_trial_example_tulip exit=0
J5_trial_typed_question exit=0
J6_persona_switch_card_order exit=1     <- needs the Pharos card index (section 5); passes on the Mac
J7_shared_link_hash exit=1              <- same
J7b_shared_link_query exit=1            <- same
J8_examples_query_and_card exit=1       <- same
J9_info_tabs exit=0
R1_v1_unchanged exit=0
R2_v2_unchanged exit=0
```

### `make hosted-check` - the hosted app, read-only

`hosted_check.py --get-only`: every GET endpoint of https://tmkg.16-178-197-241.sslip.io, the demo questions and the
review-panel assertions, never the two ask endpoints (their limit of 20 questions per hour is shared by everyone behind
one IP address). Pass: `GET non-200: [] of 16`, assertion lines `PASS`, exit 0. `EXPECTED-FAIL(<packet>)` lines are
known and wait for that packet. Another server: `python scripts/hosted_check.py
--get-only https://<host>`.

## 3. Data that is not in git

The repository holds the code, the trial layer's frozen API answers (`data/snapshots/`), small reference tables
(`data/reference/`), the demo fixture and every committed result file. The large data are rebuilt or downloaded:

| what | where it goes | get it | size |
|---|---|---|---|
| Pharos 3.19.2 tables | `data/pharos/` | `python pipelines/pharos.py pull` (resumable, network-bound; versions and checksums land in `data/reference/pharos_manifest.tsv`) | large: PPI and TIN-X ~11M rows each |
| Pharos layer + verdicts | `data/kg/pharos/` | `python pipelines/build_kg.py sources/pharos.yaml` then `python pipelines/prune_kg.py sources/pharos.yaml` (verdicts ~2.5 h, Mac) | 26.5M edges |
| other layers (Open Targets, ChEMBL, GO, HPO, MONDO, DTO) | `data/kg/<layer>/` | `bash scripts/download_external_data.sh`, then `build_kg.py` / `prune_kg.py` per card in `sources/` (docs/MAINTAIN.md) | large |
| training graphs G0-G4 | `outputs/g0..g4/heterodata.pt` | `python pipelines/make_graphs.py` | G3: 1,599,345,224 bytes (the pyg download) |
| app card index | `data/kg/pharos_index/` | `python pipelines/pharos_index.py --out data/kg/pharos_index/card_index_v3.sqlite` (Mac: 2,774 s for the shipped graph, `loop/ledger.tsv` row "CS-9 part 1"); the app and the offline click-through use the newest of `card_index_v3`, `_v2`, `card_index.sqlite` that exists, or the file named in `TMKG_PHAROS_INDEX` | 20,286,320,640 bytes (20.3 GB) |

The hosted downloads (pruned graph as KGX, verdict file, G3 for PyTorch Geometric, G4 as Neptune load files): size
from `downloads.json`, SHA256 from each folder's `SHA256SUMS`, written by `.venv/bin/python scripts/download_hashes.py` (run it
after every upload; `--check` compares the sizes with `downloads.json` without the network). Check a downloaded folder
with `sha256sum -c SHA256SUMS` (Mac: `shasum -a 256 -c SHA256SUMS`) inside it.

<!-- download-hashes:start -->

| download | URL | bytes |
|---|---|---|
| A small sample to open first: EGFR / Epidermal growth factor receptor and its neighbourhood | https://tmkg.16-178-197-241.sslip.io/downloads/sample/ | 2337491 |
| The pruned graph with the trial layer (G4) as Neptune load files | https://tmkg.16-178-197-241.sslip.io/downloads/neptune/ | 689300193 |
| The pruned graph as KGX tables | https://tmkg.16-178-197-241.sslip.io/downloads/kgx/ | 1518572383 |
| The verdict file | https://tmkg.16-178-197-241.sslip.io/downloads/verdicts/ | 1058218861 |
| The pruned graph for PyTorch Geometric | https://tmkg.16-178-197-241.sslip.io/downloads/pyg/ | 1600616456 |

`sample/SHA256SUMS`:

```
d30112ec6003f2f6661cedb3e8dc5d43d224497573f0be8dcb88ee13ad1a4d81  README.md
f67878f65471e714a7c79d1c8f99d8910c6ab2ae67f87d7219aa4f2ce215292a  kgx/edges.tsv
1ca24dc96064feaa221209475b7e59012a775ad4ee8fdec123d83a9fde033188  kgx/nodes.tsv
3d1cc38196bb5fa90725fb7d26075d8b0354e2770349e1ce95c76b81c56daa77  pyg/edges_BiologicalProcess__part_of__BiologicalProcess.csv
0a891d6a14fca77ea93a940e54d90d330dbab9acb8cd271ce8e82ccb4772d3b8  pyg/edges_BiologicalProcess__subclass_of__BiologicalProcess.csv
42546515f9a40726371d96bd2d72074022c3f0ea4bd0248501cd5be6fde83ee9  pyg/edges_CellularComponent__part_of__CellularComponent.csv
073ac7167faabea473d9731c18966a235710a29c1d515f4f7c79828e0b93aeaa  pyg/edges_CellularComponent__subclass_of__CellularComponent.csv
e4e6e2f13db4b1f06fc0b3e6a05f44ac42ebbae4f45a55a24c5bc744fde055c0  pyg/edges_Disease__has_phenotype__Disease.csv
a9d10106a2795139fccf197877312d091aa16474298a81f1e92442c70aa6d720  pyg/edges_Disease__has_phenotype__PhenotypicFeature.csv
65cfda1a0ad7b5004840e7e73eeae43ec9a4ca56154c6a603f1398882dbf562e  pyg/edges_Disease__subclass_of__Disease.csv
b4d8d39a40e69cb8fcafd787a5b77652ce7cff11d9a0d1c23b59841a30281e99  pyg/edges_Gene__gene_associated_with_condition__BiologicalProcess.csv
2a855f15793ca54b1f85621d1df1cf7a81feb605cfbb195ebcc727feb7ce2bec  pyg/edges_Gene__gene_associated_with_condition__Disease.csv
ab8e7cafbec8b2b3a6cad610444d86bda0f627efb609265be9fc96644cc21899  pyg/edges_Gene__gene_associated_with_condition__DiseaseOrPhenotypicFeature.csv
a33c175b476d15d312a9eef339e62e38629c9d194d9d6de6e9159a931b85e40d  pyg/edges_Gene__genetically_associated_with__Disease.csv
282515842e7719c1b33ce0e4126959d0c815449cd5409ce934cc30afe1ba3221  pyg/edges_Gene__genetically_associated_with__DiseaseOrPhenotypicFeature.csv
fb83b78cbdca1ca263ccd6c48fd5f3dff6b6f020b5caec17b64ca276bac88103  pyg/edges_Gene__has_gene_product__Protein.csv
a2996e372549f4f092429e63531c59b08373c94b161062519d110dc74c130b68  pyg/edges_Gene__has_phenotype__PhenotypicFeature.csv
8ae9261cdc2b3b0e657976121e66764fbf50ef74b3c1a1b17c917bf5d8510b8f  pyg/edges_MolecularActivity__subclass_of__MolecularActivity.csv
90748513940a30661f7bf946a130cc3dc140efdfcbb99f1863bcc851a858423f  pyg/edges_Protein__actively_involved_in__BiologicalProcess.csv
32012b6a544ac4f5e9f2a7876183813b15e362557a15274a69945bc462d4c06b  pyg/edges_Protein__actively_involved_in__Pathway.csv
cd6db8ad22ada8c6b126946e1eab9f7224e3da71abe432ae1ddb11733961f80b  pyg/edges_Protein__capable_of__MolecularActivity.csv
3e4c734aace34ae731da805d08278cd754227ec93daa938ac8344a8808f4927c  pyg/edges_Protein__located_in__CellularComponent.csv
728b43973613652d302845eff7eb9650d40846124e1d9c96249039c73f0dcf85  pyg/edges_Protein__member_of__ProteinFamily.csv
48f56366e4357f1cfc10f86eef0682f12e101bac3c43a3ffe656f0a739d9f1a4  pyg/edges_Protein__physically_interacts_with__Protein.csv
7b0bff0141b61c4463543472e5f7e83cba79fccfa354ba772b5f30579b6ead6c  pyg/edges_SmallMolecule__directly_physically_interacts_with__Gene.csv
9f6875f020d3788b4141f73a956d020fa79b1d68848bee09ed7d5e6b808596e1  pyg/edges_SmallMolecule__directly_physically_interacts_with__Protein.csv
78edefd055d403b14c8275f4543528e65e18fcc629da7b17d712f0767d333730  pyg/edges_SmallMolecule__treats_or_applied_or_studied_to_treat__BiologicalProcess.csv
5be6e5663b6796dbdb98a46eae858ece9ed6684b30e40ae2bae36a4589029cdc  pyg/edges_SmallMolecule__treats_or_applied_or_studied_to_treat__Disease.csv
4a807e05f1c26a602f20f0439b1906f5e540e5267db592bef34bc4fc8db9004d  pyg/edges_SmallMolecule__treats_or_applied_or_studied_to_treat__DiseaseOrPhenotypicFeature.csv
01284784cf955d963f91461e3f8f60c7db562e81c4c7b65f510267a94b965d0b  pyg/load_pyg.py
4063f3d2f72da379af212f8eb46cbb94fedeca310fa4d77b9eed3eb06db98782  pyg/nodes_BiologicalProcess.csv
2e837ab16b58d6d07260e6a803f47f39dfc0005166d44ed5134eba976a52ee8c  pyg/nodes_CellularComponent.csv
498e35a7bf1783615751e588bf61967d6781a95d1f3ab00e60801e6b5598388a  pyg/nodes_Disease.csv
2d7c516ae28dc1f61cf58939512c23bf860c4733b0990efe83116916fdfa56d8  pyg/nodes_DiseaseOrPhenotypicFeature.csv
988e9f52eba6b73a4201a154aa0bc71b3e576aef7b605f20b301a93163102660  pyg/nodes_Gene.csv
bce5c2d8296692e748dbbb525c70d36c168329b716879e1c9f29935895c9de2f  pyg/nodes_MolecularActivity.csv
aaedec1543d1fdb8c5116e0987fa9de99e71d1f03b354e203847bb16520a6b19  pyg/nodes_Pathway.csv
ea553e62b076b240d4ec5b798004eae8271f525843c6a73bf9509fcb37e94f78  pyg/nodes_PhenotypicFeature.csv
6e1d0392f7410f1a96c43e881d0439f8a5319e62f7cd64ce9dc689b65a9c97ab  pyg/nodes_Protein.csv
3854ae2fd7680a2c6291cd0e139b42e61f74b4be8e479daf9292135b897bd44f  pyg/nodes_ProteinFamily.csv
49d3e75d751e8ab3538d9c7b16a1796e2cd053b5c488ed5e7b00bf6ece1a344a  pyg/nodes_SmallMolecule.csv
```

`neptune/SHA256SUMS`:

```
7e94a3bcf3d3c37d5ed54c92e9ac37989f11880a2c3a64dde307f01e6375cae3  README.md
6e958230cb8bea152f3c1cbe0d06cbc2543ba41bcc00d6c9abedca6eb32cc733  manifest.json
9112ecbe5a2372efb7db975c17ca0bf3a46ee0170c1fb2bf70dd10db88b19445  opencypher/edges_000.csv.gz
35f1aea1c85121a3b3efa44e85110bf47a0ca2aa0a3d5f5803509c21c70a562c  opencypher/edges_001.csv.gz
5971e505dd5ae65b0a0ecd33145f9501ee1a00b5dc973612c9acf7e63d529085  opencypher/edges_002.csv.gz
ed83fbd8b5231574bba9fb2c237a632fc37f17bd589668e89046f53e7876a092  opencypher/edges_003.csv.gz
93c43fdba27fdb4a5675b7a0a3dd2617aef164373612f3426b4473fd61dbed3d  opencypher/edges_004.csv.gz
cdd9b87246532d95a06a8834d3666f5876709c18efe0933690520ceefcc3614c  opencypher/edges_005.csv.gz
3b7f469ed1917d2468d10544fe39e415c415a6336d39f83c543e3302c682c296  opencypher/edges_006.csv.gz
bb4689dbd6ea12e17f571c677c77a7ba423d61e50be22fd4957f6041cdac1d50  opencypher/edges_007.csv.gz
59ff128c753a680297cad73b65a2aa86eb67cce0c7bfc604af53c4afa4eed9b2  opencypher/edges_008.csv.gz
be2cc785f873ce12d42d3b0623564e1a8b5c83d0082dcb772843b44bbc7a3ace  opencypher/edges_009.csv.gz
1316250919c277bb6184ad43a26a7c9432dbb99a463eb4a5033db627545bfc67  opencypher/edges_010.csv.gz
4fee89cbc0b16aa774256546269db2160b52d8b7faf3b33f1f11ff29636d814f  opencypher/edges_011.csv.gz
056484141498eb5b7512be2d0b37bf24c5e0eb77e0c3a6973b68ac9dc61c9624  opencypher/edges_012.csv.gz
5ba1ac7aa4c3d62a00350a509c96df4f9aca6b8f4c3f28da60faabcb003744b1  opencypher/edges_013.csv.gz
f8d6d48706c1f296f5862d69f1066f0cd3f5c6b93c5028c032afe80784864f5a  opencypher/edges_014.csv.gz
df85ad1a6fe96fb13c7dbf05b0eedc43742d38c9858cee172b0ba527c0caf680  opencypher/edges_015.csv.gz
39b5e26df73aa55cec1a0ea56b3b148b45f156bc2e5ee5b2b601a5651417ec89  opencypher/edges_016.csv.gz
f5fd41d26d7cfce39f1d8fbb5d29121cc8226e430d4dd58a035b9d3037258ac8  opencypher/edges_017.csv.gz
cc8ccf425b02280c31332bec8880f9ae130114ead57f14a11b28441b6dd2ba91  opencypher/edges_018.csv.gz
be9a98f1c949c8b1e2ef1a9b551dae3e2dd4584677416956f543566aaba56b5e  opencypher/edges_019.csv.gz
854aca7dcefc555508d133a6dcbc3cfc5e910ddbc78729acade1e61ece21a000  opencypher/edges_020.csv.gz
dbaaf96226ae2b323bf9df73ff014d2c794ecb40d5c4122908a630c08543f6da  opencypher/edges_021.csv.gz
0b3fb62f2ec3b077ab003096e31e6aa8fd6539430ca349893b766541cd192432  opencypher/edges_022.csv.gz
bc13bcb40f0b6f2fb3547e4a1d1090750521311b80dc09fe3acd4d1d55564df7  opencypher/edges_023.csv.gz
59047c77c97c05411500ab08a172e6a266b516faedd3d8c4b082792fa96082bf  opencypher/edges_024.csv.gz
7cf2e4b67fe4436bcc77b45765cd990b86bed514d3ea8faecf484b4dbc9d994b  opencypher/edges_025.csv.gz
25ef8dbde208c35a51689b1a760e96a9c38893b2a6d921897b52dab4994be0b0  opencypher/edges_026.csv.gz
8d27edbe665ab6572bcf2c7f7b1294e2d153602cda4afd76fa9e2d5e301c467b  opencypher/edges_027.csv.gz
d210b43f3100f366a19da4af8d515bb7efae9836b390347b7a6adbc494554dce  opencypher/edges_028.csv.gz
aab7a7e1adbc1bee56f624d6cadaf2443055bcbf606e424c0a78bbe866c48eb4  opencypher/edges_029.csv.gz
0e72cbc0f8e3b8390289fd8547e65bfd3c2122fd17f8b9acbfc2982bc0f4d311  opencypher/nodes_000.csv.gz
9d8dc47df521519fe027e4e9f0c6ce19744aa38dcfa000ce056414577a97c544  opencypher/nodes_001.csv.gz
```

`kgx/SHA256SUMS`:

```
1a4738c6106921bce967dc195845b3af3034789cb43c796e38ff91bf950e68c6  edges.tsv.gz
e553b633285770889ba52306cc5ef6b189115ca0df5d82004b3f86a032daf7cb  kgx_report.md
f6e36569aee362f42486d95cfd61f9b58897e952cdef692338138ac95d23755f  nodes.tsv.gz
```

`verdicts/SHA256SUMS`:

```
9cab3476cdce91f6a6310cd3d4db53c4faba7ae2be07879f2d25b33479fbed22  row_verdicts.tsv.gz
a6fd8430b1eb580424425cc1c4827f9391c772d2980c3f25f2b2578de326b9d8  verdict_file_report.md
```

`pyg/SHA256SUMS`:

```
9b9152ebd994e77e9b30c1fa253296c5b5bea754ad632dea1aa128f28d6970b6  heterodata_G3.pt
```

<!-- download-hashes:end -->

## 4. Environment variables

Put secrets in `.env` (never committed; `.env.example` lists the names). Nothing below is required for `make check`
or `make demo`.

| variable | effect |
|---|---|
| `NVIDIA_API_KEY` (in `.env`) | the agents use NVIDIA NIM. **Without it the app uses the offline stand-in**: answers are assembled from the graph by fixed rules, and the page says so ("backend: offline stand-in"). `make harness` uses the stand-in in any case unless `GATE_LIVE=1` / `JOURNEYS_LIVE=1`. |
| `TMKG_LLM_BACKEND` | `nim` or `offline`; default `nim` when a key is set |
| `TMKG_SNAPSHOT_MODE` | `live` (default: stored API answer if present, else fetch and store), `offline` (never touch the network; `make demo` and the reproducibility test use it), `refresh` (fetch everything again = a new data version) |
| `TMKG_PHAROS_INDEX` | path of the app's card index; `off` makes the app load the live edge table instead |
| `TMKG_PHAROS_ROOT` | another folder to read the Pharos layer from (tests use a small fixture this way) |
| `TMKG_TRIAL_SOURCE` | `aact` (default) or `ctgov` for the trial registry |

## 5. Known failures outside the Mac

In a sandbox without the large data, a few checks fail for that reason alone (all pass on the Mac, CS-R5):
- `tests/test_attribution.py::test_committed_json_and_readme_match_the_generator`: the HPO release date is read from
  `data/external/hp.json` (downloaded data).
- Failing on `main` itself at b9cdf595 (10 Nov, checked on a clean checkout; not caused by the sandbox, to be fixed
  by Claude Science): `tests/test_headline.py` (2 tests: the README headline block and a missing number),
  `tests/test_loop.py::test_every_ledger_row_has_the_header_column_count` (loop/ledger.tsv row 64 has 4 of 7 columns).
- `tests/test_linkpred_gnnpath.py::test_frozen_files_are_unchanged` compares with the local branch `main`; where that
  branch is old, run `git fetch origin main && git branch -f main origin/main` first.
- `make harness`: journeys J6, J7, J7b, J8 open a Pharos node card and need the card index (section 3).
- The three test files `make tests` skips (`git init` and platform-dependent tests): docs/HARNESS.md, last section.

## 6. Fresh clone

`bash scripts/fresh_clone_check.sh <repository URL>` (required; give the public URL) clones the repository into a
temporary folder, makes a new venv, installs `requirements.lock.txt` + `requirements-ml.txt`, then `pip install -e .`
(and `trialmindkg --help`), and runs `make check` and `make demo`, printing PASS or FAIL per step
and `RESULT: PASS` at the end. Where `git clone` is not possible: `bash scripts/fresh_clone_check.sh --no-clone .`
(uses this checkout, still with a new venv). `--skip-check` / `--skip-demo` run one of the two.

## 7. Review pack and the full frozen run

- `.venv/bin/python scripts/review_pack.py` writes `outputs/review/pack_<date>.md`: one evidence document for a judge review (git
  HEAD, test summary, GET-only hosted check, counts, ledger tail, the CS-9 comparisons, downloads, routes, click gate,
  journeys). `--skip-tests` / `--skip-hosted` leave out the slow or networked parts; `--run-harness` reruns the journeys
  first. From the committed files alone it takes 0.2 s (cloud).
- `bash scripts/benchmark_frozen.sh G0,G2,G3 <tag>` is the full benchmark with the frozen flags from
  `scripts/frozen_flags.txt` (~10.5 h per graph on the Mac, ~2 h on an H100); then
  `python -m pipelines.linkpred.compare --base G0@<tag>`.

