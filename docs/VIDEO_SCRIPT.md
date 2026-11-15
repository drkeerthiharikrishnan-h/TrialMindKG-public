# Demo video script (3 min 55 s), version 2: one failed trial, one decision

Rewritten Sun 15 Nov 2026 on the owner's direction: tell one high-value clinical-trial story, show how the agent-built graph improves
the answer, and put usability first, then accuracy, explainability and standards, with the technical parts only as support. Pruning is
one honest sentence. Version 1 (S10, the walk through every tab) is in git at 3399bf62. On-screen labels were read from the page as
served at bundle 3399bf62 (S18: offline render of HEAD, and a live GET equal to it byte for byte). Every spoken number is in section 4
with the place on the page that shows it.

**The story in one line.** A Phase III trial has failed. In a minute, the team has a brief that says why, who might still respond and
what to try next, with every claim one click from its record. Built from more than Pharos alone, the graph moves answers from text
mining toward curated and measured evidence.

**Rule R11, every second.** Nothing on screen or in the narration names a file, a path, code, a commit, a test, a packet, a run tag, a
graph code, an instance or how the model is run.

## 1. Before recording (owner; not on screen)

1. Use the CloudFront link (O05): https://d1mdyg1ov91fxt.cloudfront.net/. Use one tab and a clean browser profile (no Safari Reader).
2. Do not type a question live. Every segment uses stored worked examples and tested questions, which answer in a few seconds and use
   no ask budget.
3. Zoom the browser to 125%. Hide bookmarks and extensions. Do not show the address bar while a drawer is open.
4. The Neptune clip for segment 6 (record it before the main take; Amazon Neptune is stopped between sessions):
   1. On the CloudFront link, open the tested question of segment 4 ("genes linked to both Alzheimer disease and type 2 diabetes").
   2. Under its bottom line, click "Wake it (...)". Waking takes about 11 minutes; the question then runs there by itself. At most six
      wake requests a day.
   3. Screen-record (Cmd-Shift-5, "Record Selected Portion") from the moment the line under the bottom line changes from waking to the
      Amazon Neptune result, then click "Run this on Amazon Neptune" once more for a clean take.
   4. Cut it to about 8 seconds.
   5. Ask Claude Science to stop Amazon Neptune when the clip is saved (rule D7), or stop it in Terminal:
      `aws neptune stop-db-cluster --db-cluster-identifier tmkg-neptune --region ap-southeast-2`.
5. One screenshot for the white paper, taken during the rehearsal (not in the video): after segment 3's evidence chip opens its
   evidence panel, press Cmd-Shift-4 and drag over the panel. Give the file from the Desktop to the S22 session for the white paper's
   evidence slot. If it is skipped, S22 removes the slot.
6. Rehearse the clicks once. Three are behind folds: "Worked examples for this role (from the failed-trial programs)",
   "Subgraph used (...)" under a section, and "Read from" under the tested answer.

## 2. Segments

Spoken at about 2.3 words a second. The word counts are for the narration only.

### Segment 1 - 0:00 to 0:25 - Why this matters (domain hook)

**On screen:** the first screen of the CloudFront link, held still. Then move the pointer to "Role: choose".

**Narration:**
> A Phase III trial fails. The team now has weeks to decide: stop the drug, or redesign the next trial for the patients who might
> still respond. The evidence is scattered across trial registries, curated databases and papers. TrialMindKG puts it in one place,
> and shows where every statement comes from. Here is a real case.

### Segment 2 - 0:25 to 1:20 - From a question to a brief (usability)

**On screen:**
1. "Role: choose", then "Clinician".
2. Open "Worked examples for this role (from the failed-trial programs)".
3. Under "Why did mepolizumab fail in METREO for COPD, and which patients might respond?", click "Run this example".
4. Hold on "Bottom line" and the line "confidence: high · 19 of 20 claims cite strong sources ...".
5. Scroll slowly through the four headings: "Failure Diagnosis", "Candidate Responder Population",
   "Evidence-Supported Redesign Options", "Related Indications".
6. Stop on the eosinophil claim marked hypothesis-generating.
7. End on "Claims removed by the verifier".

**Narration:**
> I choose my role, clinician, and open a worked example: why did mepolizumab fail in METREO for COPD, and which patients might
> respond? No query language. One click. The bottom line comes first: at 100 milligrams against placebo, the exacerbation rate ratio
> was 0.80, with p at 0.068, so the trial missed significance. Confidence is high, because 19 of 20 claims cite registry or curated
> records. Then come the four questions a development team asks: why it failed, who might respond, how to redesign, and where else
> the drug could go. Patients with higher blood eosinophils might respond, and the page marks that as hypothesis-generating, not
> proven. Three claims the verifier could not support are listed with the reason, not hidden.

### Segment 3 - 1:20 to 1:50 - Every claim opens its evidence (explainability)

**On screen:**
1. Under claim 1.2, click the chip "ClinicalTrials.gov · registry strong". The "Evidence record" drawer opens, showing
   "ClinicalTrials.gov · NCT02105961 (via AACT)". Press Esc.
2. In the answer's outline, click "Related Indications". Show claim 3.2 (chip "ChEMBL · curated strong") and claim 3.3 (chip
   "Open Targets Platform · database moderate").
3. Open "Subgraph used (...)" at the end of that section.

**Narration:**
> Every claim opens its record. This one is the ClinicalTrials.gov entry for METREO. Where else could the drug go? The answer follows
> the graph: mepolizumab binds interleukin-5, from ChEMBL, and the IL5 gene is linked to COPD, from Open Targets, each source named
> on its claim. Under each section, a small graph shows exactly which links the claims rest on.

### Segment 4 - 1:50 to 2:40 - What building the graph changed (the improvement)

**On screen:**
1. Open the "Pharos graph" tab, then the tested question "Which genes are associated with both Alzheimer disease and type 2 diabetes
   mellitus?".
2. Hold on the sentence "1 of 100 rows exists only because of the enrichment. The enrichment adds supporting links to 94 rows that
   Pharos alone already has." and on the confidence line.
3. Under "Read from", click "Pharos alone". Hold on "confidence: low" and "76 rows text-mined only".
4. Click "Agent-built graph" again and hold on "4 rows text-mined only".

**Narration:**
> What did building the graph change? Take a question behind many repurposing ideas: which genes link Alzheimer disease and type 2
> diabetes? Switch to Pharos alone. Confidence drops to low, because 76 of its 99 rows rest on text mining only. Back on the
> agent-built graph, confidence is moderate, and text-mined-only rows fall from 76 to 4, because the added sources support 94 of the
> rows. Same question, same page: the answer moves from literature co-mentions toward curated and measured evidence. For a team
> choosing where to spend its next trial, that is the difference between a lead worth checking and a co-mention.

### Segment 5 - 2:40 to 3:10 - Does it hold beyond one question? (accuracy)

**On screen:** the "Numbers" tab, the table "The result in three rows" (the row "Difference from Pharos alone"). Then, briefly, the
pruning block and its spot-check line.

**Narration:**
> Does this hold beyond one question? We hid 39,513 curated gene-disease pairs and asked the same model to find them. On the
> agent-built graph it ranks them better than on Pharos alone, by 0.124 AUROC, in all five folds. Every link also carries a
> keep-or-prune verdict with its reason. Our blinded check says those verdicts re-weight confidence rather than prove errors, and we
> report it that way.

### Segment 6 - 3:10 to 3:35 - Built to be used elsewhere (standards, supporting)

**On screen:** the Neptune clip from O05 (about 8 s). Then the "Downloads" tab: the five folders, and the line "SHA256SUMS lists a
checksum for every file."

**Narration:**
> The same graph runs on Amazon Neptune; here it answers a question there. It uses standard identifiers and terms, and everything
> downloads: KGX tables, Neptune load files, PyTorch Geometric files and the verdict file, each folder with checksums.

### Segment 7 - 3:35 to 3:55 - So what (close on the link)

**On screen:** "About", then "The agent loop, step by step". End on a full-screen card with the CloudFront link.

**Narration:**
> A weekly check compares every source with its latest release, and a scientist approves each rebuild. So when the next trial
> fails, the team starts from evidence, not from a blank page. Try it now, at this link.

## 3. Left out of version 2, and why (the owner can swap one in)

- **The PTGS2 card and the prediction path** (diabetic kidney disease ranked first, the path via thrombotic disease and AGT): it is
  about 20 s and a strong explainability moment, but it is a second story. If time allows, put it after segment 3. Do not open the
  drawer of PTGS2 -> thrombotic disease: its pruning reason shows "protected undefined" (S18 finding, a Code item).
- **The starkest contrast:** the tested question "Which biological processes involve the proteins that ibuprofen binds?" reads "All
  50 rows exist only because of the enrichment; Pharos alone returns none of them." It is a chemistry question, so it was left out
  to keep the clinical thread. One sentence can replace the last sentence of segment 4.
- **The spot-check numbers** (71 of 100 pruned and 56 of 100 kept judged unsupported; FAIL against the pre-set rule) and the audit
  of 1,000 links: they are on the Numbers page and in the written submission. Segment 5 says what they mean in one sentence.
- **The standard term IRIs** (SO:0001217 on 20,162 gene nodes; PR:000000001; translates_to) and **the "For judges" tab:** these are
  for the written submission.
- **"34 failed programs":** the number shows only inside a folded "Program record" of another example, not on the METREO report.
  It is not spoken.

## 4. Where each spoken number comes from (page as served at 3399bf62)

| Segment | Number as spoken | Where it shows on the page | Source behind it |
|---|---|---|---|
| 2 | rate ratio 0.80, p 0.068 (100 mg vs placebo) | METREO report, "Bottom line" and claim 1.2 | `outputs/examples/x01.json`; registry record NCT02105961 |
| 2 | 19 of 20 claims cite strong sources; confidence high | METREO report, confidence line | `outputs/examples/x01.json` |
| 2 | four sections; eosinophil claim hypothesis-generating | METREO report headings; claims 2.5 and 4.4 | `outputs/examples/x01.json` |
| 2 | three claims removed, with reasons | "Claims removed by the verifier" | `outputs/examples/x01.json` (rejected 3) |
| 3 | mepolizumab binds IL-5 (ChEMBL); IL5 linked to COPD (Open Targets) | claims 3.2 and 3.3 with their chips | `outputs/examples/x01.json` |
| 4 | 76 of 99 rows text-mined only (Pharos alone), confidence low; 4 rows text-mined only (agent-built), confidence moderate; 94 rows with added support | tested question 3, "Read from" switch | `/api/pharos/compare/demo/pharos_demo_3` (card index v3) |
| 5 | 39,513 held-out curated pairs; +0.124 AUROC; 5 of 5 folds | first screen "The result"; Numbers "The result in three rows" | `outputs/linkpred/compare_cs9_gnnpath.md` (+0.1235, +0.1167 to +0.1304, 5 of 5); per-fold `n_test` in `outputs/linkpred/G0_B_kfold@diag_gnnpath_1m_gpu.json` |
| 5 | blinded check: verdicts re-weight confidence, not proven error removal | Numbers, spot-check line (FAIL) | `outputs/pharos/spotcheck_blind_result.md`; rule `outputs/pharos/spotcheck_rule.md` |
| 6 | five download folders with checksums | Downloads tab | `downloads.json` (`/api/downloads`) |
| 7 | weekly check; scientist approves each rebuild | About, "The agent loop, step by step" | `docs/AGENT_LOOP.md`; `.github/workflows/source_check.yml` |
