# TrialMindKG: Build Status & Demo Specification

**As of:** October 4, 2026 (2 days before kickoff)  
**Challenge:** NVIDIA-AWS BioAgentKG Challenge (Oct 6 – Dec 15, 2026)  
**Entry:** Open-source track, solo builder

---

## 1. BUILD STATUS: WHAT'S DONE vs. WHAT REMAINS

### Core Infrastructure

| Component | Status | Notes |
|-----------|--------|-------|
| **Seed pipeline** | ✓ Complete | Ingests 34 failed trial programs from curated list; resolves to ClinicalTrials.gov, ChEMBL, Open Targets, GTEx, openFDA |
| **ADC demo pipeline** | ✓ Complete | Runs TUB-040 (NaPi2b ADC) end-to-end with synthetic CDISC data; generates demo_query_TUB040.md |
| **Test framework** | ✓ Complete | 3 pytest tests; all pass. Ready for Week 1 expansion |
| **Git & GitHub** | ✓ Complete | Repository live, safety checks pass, MIT license in place |
| **Python environment** | ✓ Complete | .venv configured, requirements.txt locked, runs on Python 3.10+ |

### Knowledge Graph Output

| Layer | Status | Count | Details |
|-------|--------|-------|---------|
| **Seed KG Nodes** | ✓ Complete | 187 nodes | Trials (NCT), drugs (ChEMBL), targets (UniProt), diseases (MONDO), tissues (UBERON), adverse events (MedDRA) |
| **Seed KG Edges** | ✓ Complete | 245 edges | Every edge carries: source, source_record, evidence_type, retrieved_date, provenance layer |
| **ADC Demo Nodes** | ✓ Complete | 37 nodes | Focused on TUB-040, UpRi, lifastuzumab on NaPi2b; synthetic internal cohort aggregates |
| **ADC Demo Edges** | ✓ Complete | 54 edges | interacts_with (drug→target), expressed_in (target→tissue), genetically_associated_with (target→disease) |

### Data Curation & Labeling

| Data Type | Status | Count | Details |
|-----------|--------|-------|---------|
| **Trial mappings** | ✓ Complete | 6 curated | Human-confirmed NCT overrides in `curated_overrides.csv` |
| **Synthetic CDISC tables** | ✓ Complete | 6 tables | SYN_ADSL, SYN_ADAE, SYN_ADBM, SYN_ADPC, SYN_ADRS, SYN_ADTTE (all labeled SYNTHETIC) |
| **Resolution report** | ✓ Complete | 75 entities | All 34 drugs, 34 trials resolved with rule and confidence |
| **Safety validation** | ✓ Complete | — | No .env, no API keys, no data/internal/* staged; 0 invented identifiers |

### Documentation

| Document | Status | Purpose |
|----------|--------|---------|
| **README.md** | ✓ Complete | 1-min overview: what it does, quick start |
| **CLAUDE.md** | ✓ Complete | Non-negotiable rules for all contributors |
| **GETTING_STARTED.md** | ✓ Complete | Onboarding: environment, commands, curator tasks |
| **PITCH_TUB040_TrialMindKG.md** | ✓ Complete | Business case, use case, why this asset now |
| **SETUP_GUIDE.md** | ✓ Complete | Detailed dev environment and Docker setup |
| **demo_query_TUB040.md** | ✓ Complete | Full demo answer with provenance tagging |
| **Figures** | ✓ Complete | seed_kg_overview.png, tub040_demo_evidence.png |

---

## 2. WHAT REMAINS: WEEK 1 PRIORITIES (Oct 6–12)

### Must Do (blocking → can't answer the full demo query without them)

| Priority | Component | Owner | Effort | Blocker? | Details |
|----------|-----------|-------|--------|----------|---------|
| **1** | Open Targets drug→target connector | You + Claude | 4–6 hrs | **YES** | 1 drug (fordadistrogene movaparvovec) has no ChEMBL mechanism. Query Open Targets API. Add edges with evidence. |
| **2** | Entity Resolver agent | You + Claude | 6–8 hrs | **YES** | Instead of failing on uncertain trial matches, propose ranked NCT candidates with evidence. Route to `curated_overrides.csv` review queue. |
| **3** | test_resolution.py | You + Claude | 3–4 hrs | No | Test Entity Resolver against ground_truth.csv (you will write this). Validates matching logic before challenge. |

### Should Do (expands the demo answer)

| Priority | Component | Owner | Effort | Details |
|----------|-----------|-------|--------|---------|
| **4** | Reactome target→pathway edges | You + Claude | 3–4 hrs | For each resolved target, fetch Reactome pathways. Adds context for safety signals and mechanism. |
| **5** | Open Targets target→disease edges | You + Claude | 2–3 hrs | Supplement existing disease edges. Show what other diseases the target is linked to in genetic research. |
| **6** | Fix demo_query() text | You + Claude | 1–2 hrs | Currently hard-coded "in this demo: SYNTHETIC". Make conditional: if data source is real, say so; if synthetic, label it. |

### Can Do Later (Oct 12–15)

| Component | Details |
|-----------|---------|
| **Amazon Neptune deployment** | Set up Neptune instance; load KG there instead of CSV. Connect agents to query Neptune. |
| **NVIDIA NIM integration** | Use NIM-hosted Nemotron or Llama for the agent reasoning instead of Claude API. |
| **Interactive frontend** | Build a web UI to explore the graph, click into provenance, filter by layer. |
| **Neo4j integration** | Load graph into Neo4j; provide Cypher queries for common analyses. |
| **AACT integration** | Switch from ClinicalTrials.gov v2 API to AACT SQL database for faster full-text trial searches. |

---

## 3. THE DEMO: WHAT YOU'LL SHOW AT KICKOFF

### Demo Question (from your pitch)

> *"We are planning the registrational trial of TUB-040 in platinum-resistant ovarian cancer. Two NaPi2b ADCs failed before us. What killed them, do our phase 1b data show the same risks, and what should we change in patient selection, sample size and safety monitoring?"*

### The 5-Minute Live Demo Script

| Time | Show | Say | Data Source |
|------|------|-----|-------------|
| **0:00** | The question, typed in plain language | "This is the pivotal design meeting for TUB-040. Two NaPi2b ADCs before it failed." | Frontend input |
| **0:30** | Graph view: TUB-040, UpRi, lifastuzumab → NaPi2b; NCT nodes with termination reasons | "Agents built this from registries and ChEMBL this morning. Termination reasons are verbatim from the registry." | `outputs/adc_kg_nodes.csv` + `outputs/adc_kg_edges.csv` |
| **1:30** | **Panels a–c:** (a) SLC34A2 expression across tissues; (b) FAERS ILD% by ADC/payload; (c) internal ILD by exposure tertile | "Three public sources point at the lung. Internal data tell us whether it's happening in our patients. No single database has this combination." | GTEx (public), openFDA FAERS (public), SYN_ADAE (internal) |
| **2:30** | Panel d: ORR by NaPi2b TPS band; UPLIFT's cut-off marked; 95% CIs | "The system knows our TPS ≥75% stratum is UPLIFT's definition. This shows what that cut-off would cost." | SYN_ADRS (internal) + curated UPLIFT definition |
| **3:30** | Panel e: sample-size curve; N for 80% power to clear 12% bar under 3 assumptions | "Plan on the lower bound. UPLIFT would have needed N=588 to hit its true effect, not 141." | Exact-binomial calculator |
| **4:15** | Click a statement → opens its provenance | "Every sentence opens to the registry record, API record, or internal table it came from." | Hyperlinks to NCT, ChEMBL, FAERS, internal data |
| **4:45** | Synthetic-data disclaimer box | "Plug in your real phase 1b tables. Same query runs on your data inside your AWS account." | Banner + link to data/synthetic/ |

### What the Demo Answer Contains

#### 1. **What killed the predecessors** [EXT]
   - Lifastuzumab vedotin (NCT01991210): terminated, "primary objective [PFS] did not meet pre-specified criteria"
   - Upifitamab rilsodotin (UPLIFT): ORR 15.6% (95% CI 10.0–22.7%), vs. 12% chemotherapy bar; phase 1b ORR ~100 patients did not replicate in pivotal
   - Both: NaPi2b positivity did not enrich for response
   - **Design-level conclusion:** Patient selection is the key failure mode

#### 2. **Lung as the priority safety organ** [EXT + KG]
   - **Expression (GTEx):** SLC34A2 is 324.7 TPM in lung (rank 1/54 tissues); next is Minor_Salivary_Gland at 68.6 TPM
   - **Genetics (Open Targets):** Loss of SLC34A2 → pulmonary alveolar microlithiasis (association score 0.78)
   - **Payload-class safety (openFDA FAERS):** T-DXd (TOP1-inhibitor ADC, same payload class as TUB-040) has ILD/pneumonitis in 13.7% of reports; enfortumab vedotin (MMAE) has 3.9%
   - **Precedent:** TUB-040's predecessors had microtubule-inhibitor payloads (different safety profile); TUB-040's payload class is a new risk

#### 3. **Internal ILD risk by exposure** [INT]
   - ILD/pneumonitis events by Cycle 1 exposure tertile (synthetic n=96)
   - Compared side-by-side with FAERS reporting patterns
   - Signal: does internal cohort show the same trend as FAERS?

#### 4. **Biomarker enrichment** [INT + EXT]
   - Internal confirmed ORR by NaPi2b TPS band (SYN_ADRS)
   - Overlaid: UPLIFT's 15.6% result at the TPS ≥75% cut-off
   - Overlaid: 12% chemotherapy bar
   - Cost of copy-pasting UPLIFT's cut-off: what % of internal patients would be excluded?

#### 5. **Replication risk** [INT + EXT]
   - Internal cohort characteristics (prior lines, prior PARP, prior bevacizumab) vs. UPLIFT population
   - Prior FRα-ADC (mirvetuximab) exposure: **flagged as a covariate with no external comparator** ← this is a novel data point

#### 6. **Sample size** [EXT + KG]
   - Exact-binomial N needed to clear 12% bar
   - Three scenarios:
     - TUB-040's true ORR = internal lower bound → N = ?
     - TUB-040's true ORR = internal point estimate × 0.5 → N = ?
     - TUB-040's true ORR = UPLIFT-like (16%) → N = 588
   - **Conclusion:** UPLIFT was under-powered for its true effect; don't repeat that mistake

#### 7. **Four protocol recommendations** (each linked to evidence)
   - Recommendation A: [linked to lung biology from GTEx + FAERS + internal ILD]
   - Recommendation B: [linked to biomarker enrichment cost analysis]
   - Recommendation C: [linked to sample-size under-power risk]
   - Recommendation D: [linked to novel covariate flagging]

---

## 4. FRONTEND ARCHITECTURE (based on your pitch & getting-started vision)

### Frontend Design Principles (from your statement)

From your PITCH_TUB040_TrialMindKG.md and GETTING_STARTED.md:

1. **You are the architect & validator.** The AI coding assistant writes the code; you decide what to show and why.
2. **Judges want the story.** The frontend must tell a clear narrative: (public evidence → internal data → clinical recommendation).
3. **Provenance is the value.** Every number clicks through to its source: registry record, API response, internal table & field, or computation.
4. **Layer-aware.** Statements are tagged [EXT] (public), [INT] (internal), [KG] (inferred); answers can be filtered to public-only before sharing.
5. **Synthetic-safe.** Internal numbers are clearly labeled SYNTHETIC; no one mistakes them for real findings.

### Frontend Components (Post-Kickoff Implementation)

#### **A. Query Input Interface**

```
┌─────────────────────────────────────────────────────┐
│ TrialMindKG: TUB-040 Pivotal Trial Design           │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Question:                                          │
│  ┌─────────────────────────────────────────────┐   │
│  │ "We are planning the registrational trial   │   │
│  │ of TUB-040 in platinum-resistant ovarian    │   │
│  │ cancer. Two NaPi2b ADCs failed before us.   │   │
│  │ What killed them, do our phase 1b data      │   │
│  │ show the same risks, and what should we     │   │
│  │ change in patient selection, sample size    │   │
│  │ and safety monitoring?"                     │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  [ Run Query ]                                      │
│                                                     │
│  ☐ Show public evidence only [EXT]                │
│  ☐ Include internal data [INT]                    │
│  ☐ Show inferences [KG]                          │
│                                                     │
└─────────────────────────────────────────────────────┘
```

#### **B. Interactive Graph View**

```
Nodes:
  • TUB-040 (Drug, ChEMBL:CHEMBL6050371)
  • UpRi (Drug, ChEMBL:CHEMBL4297938)
  • Lifastuzumab vedotin (Drug, ChEMBL:...)
  • NaPi2b / SLC34A2 (Target, UniProt:O95436)
  • Platinum-resistant ovarian cancer (Disease, MONDO:...)
  • NCT06303505 (Trial, status=recruiting)
  • NCT01991210 (Trial, status=terminated, reason="PFS did not meet…")

Edges (clickable for provenance):
  TUB-040 --[interacts_with]--> NaPi2b [ChEMBL mechanism record]
  UpRi --[failed_on]--> NaPi2b [ClinicalTrials.gov]
  Lifastuzumab vedotin --[failed_on]--> NaPi2b [ClinicalTrials.gov]
  NaPi2b --[expressed_in]--> Lung [GTEx: 324.7 TPM]
  SLC34A2_loss --[causes]--> PAM [Open Targets: 0.78]
```

**Click any edge → provenance panel opens showing:**
- Source database / API
- Source record ID
- Exact value / finding
- Retrieval timestamp

#### **C. Evidence Panels (Multi-Panel Figure)**

**Panel a: Lung Expression (GTEx)**
```
SLC34A2 mRNA (TPM) across 54 GTEx tissues
Top 6:
  • Lung ████████████████████ 324.7 (n=515 samples)
  • Minor Salivary Gland ████ 68.6 (n=144)
  • Colon ███ 53.2 (n=318)
  • Small Intestine ██ 42.1 (n=163)
  • Stomach ██ 35.8 (n=217)
  • Esophagus ██ 31.9 (n=545)
```

**Panel b: FAERS ILD/Pneumonitis by ADC**
```
Percent of reports mentioning ILD/pneumonitis
  • T-DXd (TOP1-inhibitor) ███████████ 13.7% (n=2,486 reports)
  • Enfortumab vedotin (MMAE) ████ 3.9% (n=1,847 reports)
  • Sacituzumab govitecan (SN-38) ██████ 6.2% (n=891 reports)
  
Legend: TOP1-inhibitor | MMAE | SN-38 | Other
Note: Reporting proportion, not incidence. Subject to market size & time on market.
```

**Panel c: Internal ILD Events by Exposure Tertile (SYNTHETIC)**
```
⚠️ SYNTHETIC DATA: For demo purposes only. Do not interpret as real findings.

ILD/pneumonitis events (SYN_ADAE)
  Exposure Tertile 1 (low) ███ 4/32 patients (12.5%)
  Exposure Tertile 2 (mid) █████ 6/32 patients (18.8%)
  Exposure Tertile 3 (high) ████████ 10/32 patients (31.3%)
  
Exposure defined as: Cycle 1 AUC in µM·h (synthetic)
```

**Panel d: ORR by NaPi2b TPS Band**
```
Confirmed ORR (%) by NaPi2b TPS, with 95% Clopper–Pearson CIs

Internal (SYN_ADRS, n=96):
  TPS 0–10%:    30.0% (95% CI 10.8–58.1%, n=10)
  TPS 10–50%:   50.0% (95% CI 34.4–65.6%, n=30)
  TPS ≥50%:     60.0% (95% CI 43.3–75.2%, n=35)
  TPS ≥75%:     62.5% (95% CI 40.6–81.5%, n=16)
  
UPLIFT (external):
  TPS ≥75%:     15.6% (95% CI 10.0–22.7%, n=141)
  
12% Chemotherapy bar: ────────────────────

Cost of copying UPLIFT's TPS ≥75% cut-off: 80 patients excluded (83% → 16%)
```

**Panel e: Sample Size for Registrational Trial**
```
Exact-binomial N for 80% power to place 95% CI lower bound > 12%

Assumption                          | N needed | vs. UPLIFT (N=141)
─────────────────────────────────────────────────────────────────
TUB-040 true ORR = 30% (low bound)  |    65    | –54%
TUB-040 true ORR = 40% (mid)        |    105   | –26%
TUB-040 true ORR = 50% (point est.) |    151   | +7%
TUB-040 true ORR = 60% (high)       |    215   | +52%
TUB-040 true ORR = 16% (UPLIFT-like)|    588   | +317% ⚠️
─────────────────────────────────────────────────────────────────

Take-home: UPLIFT was under-powered for its true effect.
If TUB-040's true ORR > 50%, N = 151 is safer than 141.
```

#### **D. Provenance Drilling** (On Click)

Click any statement in the answer → a side panel opens:

```
Statement:
"Lifastuzumab vedotin was terminated because 'the primary 
objective of PFS did not meet pre-specified criteria.'"

Provenance:
  Source: ClinicalTrials.gov
  Trial ID: NCT01991210
  Field: termination_reason
  Retrieved: 2026-10-02 (live)
  Exact text: "The primary objective of progression free survival 
              did not meet pre-specified criteria"
  Link: https://clinicaltrials.gov/ct2/show/NCT01991210

[X] Verify on ClinicalTrials.gov | [ View XML Record ] | [ Save Citation ]
```

#### **E. Data Source Badge**

Every statement tagged with its layer:

```
[EXT] — Public record (ClinicalTrials.gov, ChEMBL, GTEx, FAERS, etc.)
        Shareable; no confidentiality constraint
        Click to verify source

[INT] — Internal confidential data (sponsor's ADSL, ADAE, PK, etc.)
        Not shareable; this answer is for internal use only
        Aggregate only; patient rows never leave source tables

[KG]  — Inferred from joining [EXT] and [INT]
        Depends on both; remove [INT] tags to share public-only answer
```

#### **F. Synthetic Data Disclaimer**

Persistent banner at top of all panels with internal data:

```
⚠️ INTERNAL DATA ARE SYNTHETIC FOR DEMONSTRATION ONLY

The tables in data/synthetic/ (SYN_ADSL, SYN_ADAE, etc.) are 
generated from assumptions in adc_demo.py and are not real 
patient data. They show how internal and external evidence 
combine; they are not findings about TUB-040.

[ View Synthetic Data ] | [ View Generator Code ] | [ Read Disclaimer ]
```

#### **G. Filter & Export**

```
[ Export as... ]
  ☐ Full report (with internal data)
  ☐ Public-only report [EXT only]
  ☐ Slides (key findings + visuals)
  ☐ CSV (nodes & edges for Neo4j import)
  
[ Filters ]
  Show only:
    ☐ [EXT] Public evidence
    ☐ [INT] Internal data
    ☐ [KG] Inferences
    ☐ Specific trials (dropdown: NCT06303505, NCT01991210, NCT02783573)
    ☐ Specific adverse events (MedDRA: ILD, bleeding, etc.)
```

#### **H. Recommendations Section**

```
FOUR PROTOCOL RECOMMENDATIONS FOR PIVOTAL TRIAL DESIGN

① Patient Selection: Enrichment strategy for NaPi2b
   
   Evidence:
   • UPLIFT's enrichment (TPS ≥75%) cost 83% of screened patients but yielded 
     only 15.6% ORR
   • Your phase 1b: 62.5% ORR at TPS ≥75%, but 60% at TPS ≥50%
   • Open Targets: NaPi2b genetic association to disease = 0.78 (strong)
   
   Recommendation:
   Consider enriching at TPS ≥50% instead of ≥75%. Screen your internal data
   for the optimal cut-off using the method in [KG link].
   
   Data source: [INT panel d] + [EXT Open Targets] → [KG analysis]
   
   [ Click to see full evidence trail ]

② Safety Monitoring: Lung-centric surveillance
   
   Evidence:
   • SLC34A2 is top-expressing in lung (324.7 TPM vs. 68.6 in next tissue)
   • Loss-of-function (PAM) confirms lung biology
   • T-DXd (your payload class) has 13.7% FAERS ILD reports
   • Your internal ILD rate rises with exposure
   
   Recommendation:
   Include serial high-resolution CT at cycle 1, 2, 4. Flag any grade ≥1 ILD
   as a stopping rule. Compare with UPLIFT's (minimal) lung monitoring.
   
   Data source: [EXT GTEx] + [EXT Open Targets] + [EXT FAERS] + [INT panel c]
   
   [ Click to see full evidence trail ]

③ Sample Size: Power to your effect, not UPLIFT's
   
   Evidence:
   • UPLIFT: N=141, achieved ORR 15.6%. This was under-powered for its true effect.
   • Your phase 1b: 50–60% ORR (depending on enrichment). This is 3–4× higher.
   • To clear 12% bar with 80% power: N = 105–215 depending on your assumption
   
   Recommendation:
   If you conservatively assume 50% true ORR (half your point estimate), 
   N=151. If truly 60%, N=215. Do not default to UPLIFT's N=141.
   
   Data source: [INT panel d] → [KG sample-size calculator]
   
   [ Click to see full evidence trail ]

④ Biomarker Validation: Map your assay to UPLIFT's
   
   Evidence:
   • Your NaPi2b IHC is measured as TPS (tumor percentage score)
   • UPLIFT used the same definition: "NaPi2b TPS ≥75%"
   • Your internal TPS bands show response at TPS ≥50%, not just ≥75%
   
   Recommendation:
   Pre-specify your assay and cut-off before unblinding. Consider a 
   pre-specified sensitivity analysis at TPS ≥50% (your stronger signal).
   
   Data source: [Biomarker dictionary] + [INT panel d] + [EXT UPLIFT]
   
   [ Click to see full evidence trail ]
```

---

## 5. TECH STACK FOR THE FRONTEND (Post-Kickoff)

### Data Layer
- **Graph storage:** Amazon Neptune (production) or Neo4j (local dev)
- **Edge metadata:** Provenance table (source, evidence_type, retrieved_date, record_id, layer)
- **Internal tables:** Sponsor's AWS account (not shown in frontend URLs, only aggregate nodes)

### Backend (Python/FastAPI)
```python
# Query engine:
# 1. Parse question → identify entities (target, payload, indication)
# 2. Fetch subgraph: all edges touching those entities
# 3. Generate narrative: "predecessors failed → target organ → safety signal → recommendation"
# 4. Tag statements: [EXT], [INT], [KG]
# 5. Attach provenance links
# 6. Filter by layer if requested
# 7. Return as JSON (for frontend rendering)
```

### Frontend (React/Vue + D3.js)
- **Interactive graph:** D3.js force-directed layout (clickable nodes & edges)
- **Evidence panels:** Plotly or Vega-Lite (bar charts, CIs, dose–response curves)
- **Provenance panel:** Modal showing source record with external link
- **Report export:** Markdown, PDF (using Puppeteer or similar), HTML slides

### Example Frontend Stack (one suggestion; you choose)
```
Frontend:
  • React 18 + TypeScript
  • Plotly.js for evidence figures
  • Cytoscape.js for interactive graph
  • TanStack Query (react-query) for API calls

Backend:
  • FastAPI (Python)
  • Neptune or Neo4j driver for graph queries
  • Pydantic for data validation

Deployment:
  • Frontend: AWS S3 + CloudFront (or Vercel)
  • Backend: AWS Lambda + API Gateway or EC2
  • Graph: Neptune (serverless or managed instance)
```

---

## 6. TIMELINE: WHO DOES WHAT & WHEN

### Week 1: Oct 6–12 (Blockers)
| Task | Owner | By When | Deliverable |
|------|-------|---------|-------------|
| Open Targets drug→target | You + Claude | Oct 9 | New edges in kg_edges.csv |
| Entity Resolver agent | You + Claude | Oct 10 | Ranked NCT candidates in code |
| test_resolution.py | You + Claude | Oct 11 | Test passing, ground_truth.csv created |
| Protocol recommendations (draft) | You | Oct 12 | 4 bullet points per PITCH_TUB040 |

### Week 2–3: Oct 13–26 (Enhancements)
| Task | Owner | By When | Notes |
|------|-------|---------|-------|
| Reactome + Open Targets edges | Claude | Oct 15 | Expands safety organ story |
| fix demo_query() text | Claude | Oct 15 | Conditional on data source |
| Frontend prototype | Claude | Oct 20 | Static HTML + JSON, no backend yet |
| Neo4j load script | Claude | Oct 22 | load_neo4j.cypher updated |

### Week 4–6: Oct 27 – Nov 9 (Polish)
| Task | Owner | By When | Notes |
|------|-------|---------|-------|
| Backend API (FastAPI) | Claude | Nov 1 | Query engine + provenance links |
| Interactive frontend | Claude | Nov 5 | Live graph, evidence panels, drilldown |
| Documentation refresh | You | Nov 8 | Update README with new edges, frontend link |
| Dry run: end-to-end demo | You + Claude | Nov 9 | Test on Amazon Neptune if available |

### Week 7–9: Nov 10 – Dec 1 (Optional: Production Deployment)
| Task | Owner | By When | Notes |
|------|-------|---------|-------|
| Amazon Neptune setup | Claude | Nov 12 | Load prod graph; set up query endpoints |
| NVIDIA NIM integration | Claude | Nov 18 | Use NIM-hosted Llama for agent queries |
| Final refinements | You | Dec 1 | Respond to judge feedback (if open) |

### Week 10: Dec 2–15 (Submission & Judging)
| Task | Owner | By When | Notes |
|------|-------|---------|-------|
| Final submission | You | Dec 15 | GitHub URL, demo video, final report |
| Judging | Judges | Dec 15–… | They interact with live frontend |

---

## 7. SUCCESS CRITERIA (from your pitch)

### Must-Haves (non-negotiable)
- ✓ All 34 trials resolved with 100% accuracy (curated)
- ✓ All 41 drugs resolved; gaps filled with Open Targets (Week 1)
- ✓ Every edge carries provenance (source, evidence_type, retrieved_date)
- ✓ No invented identifiers (checked by safety script)
- ✓ Synthetic data labeled SYNTHETIC throughout

### Should-Haves (judges expect)
- ✓ Interactive graph with clickable provenance
- ✓ Five-panel evidence figure matching PITCH_TUB040
- ✓ Four protocol recommendations tied to evidence
- ✓ Ability to filter by layer ([EXT] only for sharing)
- ✓ README and PITCH easily found on GitHub

### Nice-to-Haves (bonus)
- Amazon Neptune deployment (production-ready)
- NVIDIA NIM integration (agentic query engine)
- Full CDISC lineage (connect internal tables to graph nodes)
- Comparison to competing solutions (how TrialMindKG differs from Biomni, GRAI, etc.)

---

## NEXT STEPS

1. **Today (Oct 4):** Review this document. Any gaps or changes to the demo vision?
2. **Oct 5:** Get NVIDIA API key; set up AWS account.
3. **Oct 6:** Kickoff. Start Week 1 blockers.
4. **Oct 9–12:** Deliver Open Targets connector, Entity Resolver, test_resolution.py.
5. **Oct 13 onwards:** Frontend development and polish.

**Questions?** Ask Claude directly. You have the architecture; Claude has the code.

---

**Last updated:** 2026-10-04 by Claude Haiku 4.5  
**Challenge:** NVIDIA-AWS BioAgentKG Challenge (Oct 6 – Dec 15, 2026)  
**Repository:** https://github.com/drkeerthiharikrishnan-h/TrialMindKG
