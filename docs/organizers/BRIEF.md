# Official challenge brief: Bio-Agent KGCC Distributable v002 (text extraction)

Source: `docs/organizers/Bio-Agent_KGCC_Distributable_v002.docx` (added 2026-11-04). Plain-text extraction for agents; images not included; personal e-mail addresses redacted. The docx is authoritative.

# Global Competition: Bio-Agent Knowledge Graph Construction Challenge

"Bridging the Gap: From Static Data to Dynamic Understanding"

Slack group invitation: https://join.slack.com/t/agentstobuild-jfv9116/shared_invite/zt-4clknskvk-zBglJVdc7isFT9fMrRwLDA

## Executive Summary:

While groundbreaking projects involving biological data are currently being driven by specialized academic labs, a significant disconnect remains. The Knowledge Graph (KG) community is not well-linked with the larger scientific community. As a result, the immense benefit of KGs—specifically the dynamic aggregation of knowledge graphs and other omics data—remains inaccessible to the average researcher.

The utility of KGs is well documented in literature, yet the broader scientific population does not yet  grasp their potential due to high barriers of entry. To solve this, we must answer the critical question: How do we get more people building and using Knowledge Graphs?

We introduce a design challenge for builders to create AI Agents capable of autonomous KG construction, maintenance, and querying. This would attract a community of builders and democratize access to knowledge graph usage.

## The Challenge: Agent-Based Construction

We are asking participants to build Agents—autonomous or semi-autonomous software—that can execute the complex pipeline of KG generation. Can an agent (or swarm of agents) perform the following loop?

Ingest: Take knowledge concepts from a variety of heterogeneous sources.

We want to combine and add additional data to knowledge graphs that may not have the same structure

The above determines what concepts are allowed and what relationships can be defined between them. E.g., can we only include tables?

Filter: Retrieve only relevant subsets of graphs (relevance filtering).

Potential idea for a generic filter: Take plain language research question and convert it into graph query, paring down irrelevant nodes as needed

Clean: Prune erroneous knowledge (hallucinations or contextually incorrect links).

Idea being that, after being seeded with a question, some of the edges defined between nodes would be inappropriate

Question: What mechanism would the pruning agent use for this? E.g. The agent uses literature or other data supplied via API to provide a confidence reweighting of the identified edges using GNN

Limit: Manage the size of the queryable graph for performance.

Probably not necessary since it depends on resources at the user’s disposal

Maintain: Perform auto-updating as new literature/data becomes available.

Hopefully not too complicated!

## Competition Tracks:

Open source: $5000 USD AWS credits

Closed source: Prize will be recognition on social media/ photo-op with organizers.

### Topic: Structured Data Integration & Link Prediction

### Focus: Agent-driven KG construction from structured/tabular experimental data for predictive modeling

Objective: Design an AI agent that can take public datasets from https://pharos.nih.gov/about and autonomously construct an enriched knowledge graph optimized for link prediction. The agent should pull in contextual information (disease properties, drug properties, linked genes, proteins, ontological annotations) from public KGs and databases to maximize predictive power.

Key Question: Given a set of experimentally measured relationships, can an agent autonomously build a contextualized knowledge graph that outperforms existing public KGs (e.g., Pharos) at predicting held-out links using GNNs/GCNs?

Evaluation:

Agent builds the graph; ML algorithms (GNNs, GCNs, graph embeddings) are run to predict unseen edges

Performance compared against baseline (same ML on existing public KGs like Pharos)

## Judging Criteria (The Parameters)

Winners will be selected based on the following weighted metrics:

Usability (40%): Can a non-KG expert use this Agent? Does it make the utility of knowledge graphs in this space obvious? Does this allow for contextualized multihop projects

Accuracy & Pruning (30%): How effectively does the Agent remove erroneous or contextually irrelevant data?

Explainability (20%): Can the Agent trace the lineage of a specific edge in the graph? (Why does A connect to B?)

Adherence to Standards (10%): Explicit, if imperfect references to any standard vocabulary, ontology, or metathesaurus.

All nodes and properties are direct representations of existing ontology classes and properties (or Knowledge Graph schema e.g. BioLink)

A node of “protein coding gene” is recognizable ashttp://purl.obolibrary.org/obo/SO_0001217

A node “protein” is recognizable as http://purl.obolibrary.org/obo/PR_000000001

A relationship between “protein coding gene” and “protein” in which “protein coding genes” - translate-> “protein” is explicitly recognizable  ashttp://purl.obolibrary.org/obo/so#translates_to

Points for consistency, specificity and efficiency

If ‘x’ is a protein coding gene, it is better classified as such rather than the more general “gene” (i.e. http://purl.obolibrary.org/obo/SO_0000704)

Use as few classes and properties as possible

## Logistics

Application period - Sep 1-18, 2026 - DONE

Accepted teams notified on Sept 25, 2026 - DONE

Challenge kickoff – Oct 6, 2026 - all accepted teams participate in an official kick off session 11am ET.

Challenge duration - Oct 6 - Nov 17, 2026

Data shared with specific goals, teams will have six weeks to work on the challenge

There will be one intermediate submission - progress report via email on Oct 27, 2026

Meetings with mentors will be open during this period

Judging - Nov 17 - Dec 8, 2026

Final results announced - Dec 15th

Resources:

Articles:

KG-LLM-Bench: A Scalable Benchmark for Evaluating LLM Reasoning on Textualized Knowledge Graphs

[2604.27269] OptimusKG: Unifying biomedical knowledge in a modern multimodal graph; https://optimuskg.ai/

Graph ML education:

https://web.stanford.edu/class/cs224w/

GNN+LLM: https://web.stanford.edu/class/cs224w/slides/Lecture16.pdf

https://www.nvidia.com/en-us/on-demand/session/other25-gnn-llm-marlowe/

Repos:

https://build.nvidia.com/spark/txt2kg/instructions

https://github.com/valleyofdawn/TreeTag

https://github.com/mims-harvard/OptimusKG

https://github.com/pyg-team/pytorch_geometric

https://github.com/pyg-team/pytorch_geometric/tree/master/examples/llm

https://catalog.ngc.nvidia.com/orgs/nvidia/-/containers/pyg/-

Agentic AI for Amazon Neptune Graph Development (Brian O'Keefe, ex-Principal SA, Amazon Neptune):

https://github.com/aws-samples/amazon-neptune-samples/tree/master/neptune-prototyping-with-agents

https://builder.aws.com/post/3Bir7VVjyMUnuLsq3vhk64GeRnt_p/stop-dreading-the-data-model-ai-agents-make-graph-development-on-amazon-neptune-fun

https://builder.aws.com/content/39tJ2FA8A1Z7xaB2f1UNgAzMXLi/building-a-graph-poc-using-agents-a-case-study-part-1-the-setup

https://builder.aws.com/content/39tbkKBbyn5vTWEJLUNu0YnkvK1/building-a-graph-poc-using-agents-a-case-study-part-2-the-data-model

https://builder.aws.com/content/39tenM9otULQjgy98nml2alxCaO/building-a-graph-poc-using-agents-a-case-study-part-3-create-data

https://builder.aws.com/content/39try7AfQSM1BSEPnIMTCzRKRk3/building-a-graph-poc-using-agents-a-case-study-part-4-create-queries

https://builder.aws.com/content/39u6ZcJqJumr971hh5oFhwwAcLA/building-a-graph-poc-using-agents-a-case-study-part-5-data-for-complex-scenarios

The Monarch Initiative team is happy to help anyone interested in using any of Monarch's KG standards (biolink, linkML, KGX, SSSOM) or tools (e.g. Koza). You might also be interested in their KG registry here: https://kghub.org/kg-registry/
