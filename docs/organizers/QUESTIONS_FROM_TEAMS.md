# Organizer responses to questions from teams (BioAgentKG Challenge) - redacted for the public repository

Source: the organisers' "Questions from teams" document (received 2026-11-04; the original .docx is kept outside the
public tree, see `docs/PREPUBLIC_MOVES.md`). This public copy keeps (1) the organisers' general comments, (2) the
questions TrialMindKG asked and their answers, and (3) the organisers' answers that are general guidance for every team,
with the asking team's name and question removed. Other teams' names, affiliations, questions and plans are not
reproduced (review finding C2, 2026-11-08). Answers are quoted as written.

## 1. General comments (to all teams)

All of you are knowledge graph experts and can build amazing knowledge graphs. Please remember that the main point of
this challenge is democratization and therefore usability by the general scientific data public. That must be
demonstrated clearly in your final presentations to have a reasonable shot at winning in your category.

Also, its fine if you have a really specific POC, but please demonstrate that/how your tool is generalizable to other
scientific use cases.

For the final output a hosted demo (proprietary) and/or launchable demo (open) is ideal. If that is not possible, a
short video and/or writeup in addition to the graph file will be sufficient.

## 2. TrialMindKG's questions

- **Gold set:** which Pharos/TCRD version and edge types are covered, how edges were labelled, the label scale, the
  scoring metric, and the verdict file format. Two or three unscored example rows would help.
  No answer in the document; the organisers' margin comment defers it to NCATS ("Lets ask NCATS!").
- **NVIDIA:** does hosted build.nvidia.com count, or must a NIM run on AWS?
  ANSWER: build.nvidia.com is fine
- **Final submission:** is a hosted demo or video required?
  ANSWER: Hosted demo is ideal, video will work in a pinch

## 3. General guidance from the organisers' answers to other teams (team names and questions removed)

- Pharos and ontologies: "Pharos primarily uses the drug target ontology with HPO and GO layered on top when appropriate."
- Gold-set identifiers: "Uniprot IDs will be fine"
- Submission format (the pruned graph and every Pharos edge with a keep/prune verdict and confidence, as KGX TSV, plus a
  PyTorch Geometric export): "Yes, that works!"
- Usability judging: "Ideally in a hosted instance but video will suffice if absolutely necessary"; "Ideally a hosted
  demo, video in a pinch. Show usability!"
- Same baseline: asked whether all teams use the same baseline Pharos graph: "Yes."
- Gold-set coverage, injected errors, the evaluation release and the held-out link type: deferred to NCATS in the
  organisers' margin comments; no answer in the document.
- Models: "I'd recommend using open models, such as nemotron (biased)."
- AWS credits: "All AWS products and services accessible through the console should be covered." Kiro: see
  https://builder.aws.com/content/3DPhbEdo3a6b2kO0OttZhapOnIZ/how-to-pay-kiro-subscription-with-aws-credits ; redeem
  codes through https://aws.amazon.com/awscredits/ or under "credits" in the AWS dashboard.
- Billing: "Set up billing alarms
  (https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/monitor_estimated_charges_with_cloudwatch.html) ASAP
  to avoid unwanted charges. Once credits are depleted, you will be billed for any continued usage. We are unable to
  refund any overage charges. Please redeem the code(s) as soon as possible. We cannot cover any charges incurred because
  credit wasn't applied to an account before the end of the billing month."
- Neptune cost (answer quoted in full in `docs/organizers/NEPTUNE_COST_GUIDANCE.md`): Neptune has no built-in idle
  shutdown. Stop Neptune Analytics graphs or provisioned clusters when not in use (a stopped provisioned cluster restarts
  on its own after 7 days); keep Serverless minimum capacity low; automate with a CloudWatch alarm on query activity that
  triggers a Lambda, or an EventBridge schedule; delete old snapshots and test clusters.
