# TrialMindKG: Do Now (Oct 4–5, Before Kickoff Oct 6)

**You don't need to code.** Focus on curation, accounts, and understanding what's already built.

---

## PRIORITY 1: Accounts & Tools (1–2 hours)

These unlock everything else.

### ✓ Done Already
- [x] GitHub repository live
- [x] Python environment (.venv)
- [x] Seed pipeline built
- [x] ADC demo pipeline built

### → Do Now

| Task | Why | Time | How |
|------|-----|------|-----|
| **NVIDIA developer account** | Need API key for agents (NIM) | 10 min | Go to build.nvidia.com, sign up, generate API key. Save to `.env` (never commit). |
| **AWS account** | Need for Neptune, S3, Lambda | 15 min | aws.amazon.com/console. Create account. Don't spin up Neptune yet ($$). |
| **Neo4j Desktop** (free) | Visualize the graph locally | 5 min | neo4j.com/download → install Neo4j Desktop. You'll load the seed KG into it. |

**After you have these:** Add NVIDIA key to `.env` in your project root. **NEVER commit .env.** Add it to `.gitignore` if not already there.

---

## PRIORITY 2: Understand What's Built (2–3 hours)

Read the key documents. You need to know the foundation before Week 1.

### Read These (in order)

| File | What to Learn | Time |
|------|---------------|------|
| **README.md** | What TrialMindKG does in 1 minute | 5 min |
| **CLAUDE.md** | The 8 non-negotiable rules | 10 min |
| **GETTING_STARTED.md** | How the seed works; what you curate | 15 min |
| **PITCH_TUB040_TrialMindKG.md** | Why TUB-040, why now, the demo question | 20 min |
| **outputs/resolution_report.csv** | Which entities resolved, which gaps remain | 10 min |
| **outputs/demo_query_TUB040.md** | What the full answer looks like | 10 min |

**Total:** ~70 minutes. You'll know the landscape.

---

## PRIORITY 3: Curate Ground Truth (1–2 hours)

This is **your work**, not Claude's. The model learns from your expertise.

### Create `data/curated/ground_truth.csv`

This file validates the Entity Resolver in Week 1. It's a simple table of 34 programs:

```
program,drug,trial,why_failed
P01,Solanezumab,EXPEDITION3,amyloid target engagement failure
P02,Verubecestat,EPOCH,liver toxicity at efficacious dose
P03,Lanabecestat,AMARANTH,amyloid target engagement failure
...
```

**Columns you define:**
- `program` — P01, P02, P03, … (label from data/curated/)
- `drug` — the lead compound name
- `trial` — NCT or registry ID (from resolution_report.csv)
- `why_failed` — ONE sentence. Your clinical judgment. This is the ground truth.

**How to write `why_failed`:**
- Look at the trial's termination reason on ClinicalTrials.gov
- Look at publications (if any) or press releases
- Decide: Was it target failure? Toxicity? Insufficient efficacy? Safety signal?
- Write it in one phrase. Example: "amyloid target engagement failure", "dose-limiting hepatotoxicity", "bleeding risk with DOAC combination"

**Where to find termination reasons:**
- ClinicalTrials.gov: search NCT ID → "Study Status" section
- PubMed: search trial name + drug + year
- Press releases: sponsor website or BiopharmGuy

**Why this matters:**
- Claude's Entity Resolver will learn from your judgments
- Week 1 test (test_resolution.py) will check: "Did you correctly identify the trial for this drug failure?"
- Your ground truth is the reference answer

### Already Curated (6 rows in `curated_overrides.csv`)
Don't touch these. But **review them** and confirm they're right:

```
program,entity_type,query,resolved,identifier,rule,n_candidates
P01,drug,Solanezumab,True,CHEMBL1743072,exact_name_or_synonym,
P01,trial,EXPEDITION3,True,NCT01900665,acronym_exact,1.0
...
```

Ask yourself: "Is EXPEDITION3 really NCT01900665?" (Answer: yes, it is. ✓)

---

## PRIORITY 4: Load the Seed Graph into Neo4j (30 min)

Visualize what's there.

### Steps:
1. **Open Neo4j Desktop** (you installed it above)
2. **Create a new project** → New Database → Name it "TrialMindKG"
3. **Open the database**
4. **Open Cypher query box**
5. **Copy-paste** the contents of `docs/load_neo4j.cypher` into the query box
6. **Run it** (Play button)
7. **Explore the graph:** Click nodes, drag to see connections

### What you'll see:
- 187 nodes (trials, drugs, targets, diseases)
- 245 edges (mechanism, expression, genetics)
- Zoom in on NaPi2b → see the 3 ADCs that failed
- Click a trial node → see its status and termination reason

### Why do this:
- You'll understand what "provenance on every edge" means (every line has a source, evidence type, retrieval date)
- You'll see the TUB-040 demo KG embedded in the larger graph
- You'll spot any data issues before kickoff

---

## PRIORITY 5: (Optional) Run the Pipelines Locally (1 hour)

If you want to see fresh data from live APIs.

### Run the seed pipeline:
```bash
cd ~/projects/TrialMindKG
source .venv/bin/activate          # Activate Python env
python pipelines/seed_kg.py         # Fetches 34 trials from live ClinicalTrials.gov + ChEMBL
```

**What it does:**
- Queries ClinicalTrials.gov v2 API for 34 trial programs
- Resolves drug names to ChEMBL IDs and mechanisms
- Fetches target info from Open Targets, GTEx, FAERS
- Writes `outputs/kg_nodes.csv` and `outputs/kg_edges.csv`
- Takes ~5–10 minutes (API calls)

**Don't worry if it fails:** APIs are rate-limited or change. Claude will fix it Week 1. The current outputs/ files are good.

### Run the ADC demo:
```bash
python pipelines/run_adc_demo.py    # Generates the demo_query_TUB040.md answer
```

**What it does:**
- Loads synthetic CDISC tables (SYN_ADSL, SYN_ADAE, etc.)
- Joins them to the seed KG on NaPi2b, TOP1 payload, OC indication
- Answers the TUB-040 design question
- Writes `outputs/demo_query_TUB040.md` with 5 evidence panels

**Result:** You'll see what a full answer looks like. Compare it to `docs/demo_query_TUB040.md` (the template).

---

## PRIORITY 6: (Optional) Review Test Output (15 min)

See what's passing.

### Run tests:
```bash
.venv/bin/pytest tests/ -v
```

**Expected output:**
```
tests/test_seed_kg.py::test_load_seed PASSED
tests/test_adc_demo.py::test_load_synthetic_tables PASSED
tests/test_adc_demo.py::test_demo_query PASSED

======================== 3 passed in 1.23s =========================
```

All 3 should pass. If any fail, that's a setup issue (not your fault).

---

## PRIORITY 7: Check Your Files (10 min)

Make sure the repo is ready for GitHub.

### Checklist:
- [ ] Is `.env` in `.gitignore`? (so API keys never get committed)
- [ ] Does `data/internal/README.md` exist? (marks internal folder as git-ignored)
- [ ] Are all SYN_*.csv files labeled SYNTHETIC in their first row?
- [ ] Does `.gitignore` exclude `data/internal/*`?

**Run this to check:**
```bash
cd ~/projects/TrialMindKG
git status
```

You should see:
- ✓ All tracked files (README.md, CLAUDE.md, pipelines/, tests/, docs/, data/curated/, data/synthetic/)
- ✓ No .env listed (it's in .gitignore)
- ✓ No data/internal/* listed (it's in .gitignore)

---

## DO NOT DO YET

### ❌ Don't modify the code
- Pipelines are done and tested
- Changes go to Claude with "here's what I want to add" → Claude codes it

### ❌ Don't create tables by hand
- Entity Resolver agent will propose them in Week 1
- You review and approve in `curated_overrides.csv`

### ❌ Don't set up Amazon Neptune
- Too expensive to run idle; wait for Week 1
- Claude will set it up when the demo is ready

### ❌ Don't install new Python packages
- Everything is in requirements.txt already
- If you need something, ask Claude

---

## DELIVERABLES FOR KICKOFF (Oct 6)

Hand these to Claude on Oct 6:

1. **`data/curated/ground_truth.csv`** — Your 34-row curated truth file
2. **Confirmation** — "I've reviewed curated_overrides.csv and it's correct" (or name changes)
3. **NVIDIA API key** — Pasted into `.env` (only your copy; never commit)
4. **AWS account ID** — So Claude can help set up Neptune later

---

## TIMELINE FOR THESE TASKS

| Task | Time | When |
|------|------|------|
| Accounts + tools | 30 min | Today (Oct 4) |
| Read documents | 70 min | Today |
| Write ground_truth.csv | 60 min | Today |
| Review curated_overrides.csv | 15 min | Today |
| Load seed KG into Neo4j | 30 min | Oct 5 |
| (Optional) Run pipelines | 60 min | Oct 5 |
| (Optional) Run tests | 15 min | Oct 5 |
| (Optional) Check .gitignore | 10 min | Oct 5 |

**Total:** ~4 hours over 2 days.

---

## Questions to Answer Before Kickoff

As you work through the documents and data, jot down answers to:

1. **On the seed graph:** Are there any trial-drug pairs that look mismatched? (e.g., a drug that shouldn't be linked to a trial)
2. **On the failures:** Looking at the 34 programs, do you see patterns in why they failed? (e.g., "5 amyloid programs, all target engagement")
3. **On TUB-040:** Is the demo question answerable with the public data we have, or are there gaps?
4. **On the demo:** Which of the 5 evidence panels (lung expression, FAERS, internal ILD, ORR by TPS, sample size) feels most important to you?

Bring these observations to kickoff. They'll guide Week 1 priorities.

---

**Ready? Start with the accounts.** NVIDIA + AWS take 25 minutes; the documents take 70. You'll be all set for Oct 6.

Questions? Ask Claude.
