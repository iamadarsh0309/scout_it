# AI Football Scouting Platform — Project Plan

## 1. Project Overview

Build an AI-powered football scouting and player discovery platform that allows a club to describe its recruitment requirements in natural language and receive a ranked list of suitable players with evidence-backed scouting reports.

The system supports players of **any age**. Age is a query constraint extracted from the club's request rather than a limitation of the player database.

### Core workflow

```text
Club requirement
      ↓
LLM requirement parser
      ↓
Structured scouting profile
      ↓
Candidate retrieval
(SQL filters + semantic retrieval)
      ↓
ML ranking
      ↓
Top candidates
      ↓
Evidence retrieval / RAG
      ↓
LLM scouting report
      ↓
Evaluation
      ↓
Feedback / model improvement
```

## 2. Core Design Principles

1. **Structured data is the source of truth.**
   Player facts and numerical statistics live in PostgreSQL.

2. **RAG is not the ranking engine.**
   RAG retrieves supporting evidence; it does not decide which player is best.

3. **LLMs interpret and explain.**
   LLMs convert natural-language scouting requirements into structured criteria and generate evidence-backed explanations.

4. **ML performs ranking/prediction.**
   The ranking model determines player suitability for a particular scouting request.

5. **Evaluation drives iteration.**
   Poor results must be diagnosed before changing prompts, retrieval, models, or fine-tuning.

6. **Raw data is preserved.**
   Every ingestion source should retain raw records so transformations can be rerun.

7. **Source-agnostic ingestion.**
   The data layer should allow different legal/open/licensed sources to feed the same normalized schema.

---

# 3. System Architecture

```text
                         ┌─────────────────────┐
                         │      CLUB USER      │
                         └──────────┬──────────┘
                                    │
                         Natural-language request
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │       FastAPI       │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   LLM Requirement   │
                         │       Parser        │
                         └──────────┬──────────┘
                                    │
                           Structured requirement
                                    │
                    ┌───────────────┼────────────────┐
                    ▼               ▼                ▼
              Hard Filters     Semantic Search     Weights
                    │               │                │
                    ▼               ▼                ▼
              PostgreSQL         pgvector       Ranking config
                    │               │                │
                    └───────────────┼────────────────┘
                                    ▼
                           Candidate Retrieval
                                    │
                              100–500 players
                                    │
                                    ▼
                           ┌─────────────────┐
                           │  Ranking Model  │
                           │ XGBoost / LTR   │
                           └────────┬────────┘
                                    │
                                Top 10–20
                                    │
                         ┌──────────┴──────────┐
                         ▼                     ▼
                  Player Analytics       Evidence Store
                         │                     │
                         └──────────┬──────────┘
                                    ▼
                              RAG Pipeline
                                    │
                                    ▼
                                    LLM
                                    │
                                    ▼
                            Scouting Report
                                    │
                                    ▼
                              Dashboard
                                    │
                                    ▼
                             Scout Feedback
                                    │
                                    ▼
                           Ranking / eval data
```

---

# 4. Pipeline A — Data Extraction Pipeline

## Objective

Build a reliable player universe and continuously transform external football data into a normalized internal data model.

The initial system should focus on **player-level information**, not match/video intelligence.

## Flow

```text
Data Source
    ↓
Source Adapter
    ↓
Raw Data Store
    ↓
Parser
    ↓
Validation
    ↓
Normalization
    ↓
Entity Resolution
    ↓
PostgreSQL
```

## Data discovery hierarchy

```text
Countries
   ↓
Competitions
   ↓
Seasons
   ↓
Clubs
   ↓
Squads
   ↓
Players
   ↓
Player profiles/statistics
```

The crawler should avoid repeatedly rediscovering the same player. Player IDs and source mappings should be cached.

## Coverage strategy

Maintain a configurable competition coverage file:

```yaml
England:
  - tier: 1
  - tier: 2
  - tier: 3
  - tier: 4

Spain:
  - tier: 1
  - tier: 2
  - tier: 3

Brazil:
  - tier: 1
  - tier: 2
  - tier: 3
```

This allows the project to expand from a small MVP to broader global coverage.

## Raw data

Never discard the original source response.

Example:

```text
data/
├── raw/
│   ├── source_a/
│   └── source_b/
├── normalized/
└── processed/
```

Object storage such as S3 can eventually replace local storage.

## Entity resolution

A player can appear across multiple competitions, clubs, seasons, and sources.

Maintain:

```text
player_source_mapping
---------------------
player_id
source
source_player_id
source_name
```

The internal `player_id` is the canonical identity.

---

# 5. Pipeline B — Data Normalization & Feature Engineering

## Objective

Convert heterogeneous football data into comparable player features.

Raw statistics cannot be compared directly across competitions or playing time.

## Flow

```text
Raw player statistics
        ↓
Normalization
        ↓
Per-90 metrics
        ↓
Percentiles
        ↓
Age adjustment
        ↓
Competition-strength adjustment
        ↓
Performance trends
        ↓
player_features
```

## Example features

```text
goals_p90
assists_p90
xg_p90
xa_p90
progressive_passes_p90
progressive_carries_p90
key_passes_p90
tackles_p90
interceptions_p90
duels_p90
aerial_duels_p90
pressures_p90
passing_accuracy
duel_success
aerial_success
league_strength
age_adjusted_performance
performance_trend
```

Features should be position-aware where appropriate.

For example, a striker and centre-back should not be evaluated using identical feature weights.

---

# 6. Pipeline C — Requirement Understanding Pipeline

## Objective

Convert natural-language club requirements into a machine-readable scouting profile.

## Example input

> Find me a left-footed centre-back between 18 and 23 who is strong in aerial duels, good at progressing the ball, and has high development potential.

## Flow

```text
User prompt
    ↓
LLM
    ↓
Structured JSON
    ↓
Validation
    ↓
ScoutingRequirement
```

## Example output

```json
{
  "age": {
    "min": 18,
    "max": 23
  },
  "positions": ["CB"],
  "preferred_foot": ["left"],
  "technical": {
    "ball_progression": 0.90
  },
  "physical": {
    "aerial_duels": 0.85
  },
  "development": {
    "potential": 0.90
  }
}
```

## Important rule

The LLM should **not execute database queries itself**.

It produces structured intent. Backend code validates that structure and converts it into SQL/vector-search operations.

---

# 7. Pipeline D — Candidate Retrieval Pipeline

## Objective

Reduce a large global player universe to a manageable candidate set.

## Flow

```text
Structured scouting requirement
             ↓
        Hard filters
             ↓
       500k → 10k
             ↓
      Semantic retrieval
             ↓
       10k → 500
             ↓
         Ranking
             ↓
        500 → 20
```

## Hard filters

Examples:

- age
- position
- preferred foot
- country
- competition
- club
- minimum minutes
- transfer constraints where data is available

These should be implemented using PostgreSQL.

## Semantic retrieval

Create player-profile embeddings from relevant textual/structured representations.

```text
Player profile
     ↓
Embedding model
     ↓
Vector
     ↓
pgvector
```

The scouting requirement is also embedded.

Semantic retrieval finds players whose profiles are conceptually similar to the request.

---

# 8. Pipeline E — Player Ranking Pipeline

## Objective

Rank candidates according to the specific club/scouting requirement.

This is the central ML component.

## Problem formulation

Do not train:

```text
player → good/bad
```

Train:

```text
player features
+
scouting requirement
        ↓
player suitability
```

A player can have high general ability but low fit for a particular tactical requirement.

## Initial model

Start with:

- XGBoost

Then experiment with:

- LightGBM
- Learning-to-Rank
- pairwise ranking
- listwise ranking

## Example

```text
Player A
Current ability: 87
Club fit:        94

Player B
Current ability: 91
Club fit:        81
```

Player B may be the better footballer overall but Player A is the better recruitment target for this specific club.

## Future models

Potentially separate:

1. Current ability model
2. Development/potential model
3. Club-fit model
4. Market-value model

The final recommendation can combine these signals.

---

# 9. Pipeline F — Player Analytics Pipeline

## Objective

Generate interpretable statistics and comparisons around a ranked player.

For each player calculate:

```text
Current performance
Performance percentile
Position percentile
Age-adjusted performance
Competition context
Historical trajectory
Strengths
Weaknesses
```

Example:

```text
Player X

Progressive passing: 92nd percentile
Aerial duels:        84th percentile
Defensive actions:   78th percentile
Performance trend:   +12%
```

These computed values become evidence available to the RAG/reporting pipeline.

---

# 10. Pipeline G — RAG Pipeline

## Objective

Provide the LLM with reliable evidence for explaining recommendations.

RAG should primarily handle **textual and contextual information**, while structured statistics remain in PostgreSQL.

## Data split

### PostgreSQL

Use for:

- age
- position
- club
- nationality
- statistics
- per-90 metrics
- percentiles
- historical performance
- ranking scores

### Vector store

Use for:

- scouting reports
- textual player descriptions
- contextual documents
- relevant articles
- qualitative analysis
- other unstructured evidence

## Flow

```text
Top-ranked player
       ↓
Retrieve structured statistics
       +
Retrieve relevant documents
       ↓
Build evidence context
       ↓
LLM
       ↓
Scouting report
```

## Hybrid retrieval

```text
User question
     ↓
Query understanding
     │
     ├──────────────┐
     ▼              ▼
PostgreSQL       pgvector
     │              │
     └──────┬───────┘
            ▼
       Evidence set
            ↓
           LLM
```

---

# 11. Pipeline H — Scouting Report Generation

## Objective

Generate an evidence-backed explanation rather than simply returning a numerical score.

## Report structure

```text
Player
Overall fit score

Why recommended

Technical strengths

Tactical strengths

Physical profile

Development potential

Risks / weaknesses

Comparison with requirement

Supporting evidence

Data freshness
```

The LLM must distinguish between:

- observed data
- model prediction
- inference
- uncertainty

It should never invent unavailable statistics.

---

# 12. Pipeline I — Evaluation Pipeline

## Objective

Measure every important component independently.

Do not evaluate only the final LLM answer.

## Evaluation layers

### Requirement extraction

Metrics:

```text
Field accuracy
Schema validity
Age-range accuracy
Position accuracy
Attribute extraction accuracy
```

### Retrieval

Metrics:

```text
Recall@K
Precision@K
MRR
NDCG
```

### RAG

Metrics:

```text
Context precision
Context recall
Faithfulness
Evidence relevance
```

### Ranking

Metrics:

```text
NDCG@K
Precision@K
Ranking correlation
Pairwise accuracy
```

### Generation

Metrics:

```text
Answer relevance
Factual correctness
Completeness
Groundedness
```

---

# 13. Evaluation Dataset

Maintain a version-controlled evaluation set.

Example:

```json
{
  "query": "Find me a left-footed CB aged 18-21...",
  "expected_constraints": {
    "position": "CB",
    "age_min": 18,
    "age_max": 21,
    "preferred_foot": "left"
  },
  "expected_evidence": [
    "progressive passing",
    "aerial duels"
  ]
}
```

Add difficult cases deliberately:

- ambiguous positions
- missing data
- contradictory requirements
- broad queries
- highly specific queries
- different age ranges
- different tactical systems

---

# 14. Pipeline J — Error Analysis & Improvement

Evaluation results should determine what gets changed.

```text
Evaluation
    ↓
Failure
    ↓
Classify failure
    │
    ├── Requirement parsing
    ├── SQL filtering
    ├── Semantic retrieval
    ├── Ranking
    ├── Evidence retrieval
    ├── Context construction
    └── LLM generation
```

### Example

If:

```text
Retrieval Recall@20 = 55%
```

do not fine-tune the LLM.

Investigate:

- filters
- embeddings
- player representations
- query representation
- reranking

If retrieval is excellent but generation is poor:

```text
Retrieval Recall@20 = 95%
Answer quality = 62%
```

investigate:

- prompt
- context structure
- model choice
- structured output
- context ordering

Only then consider fine-tuning.

---

# 15. Pipeline K — Fine-Tuning

Fine-tuning is an optimization stage, not the foundation.

## Potential fine-tuning targets

### Requirement parser

```text
Natural language
      ↓
Fine-tuned model
      ↓
Scouting JSON
```

Useful if the system repeatedly makes mistakes extracting football-specific requirements.

### Scouting report generation

Only consider this after collecting substantial examples of:

```text
Input evidence
+
Club requirement
+
Human-approved report
```

### Do not fine-tune to memorize player data

Player information changes.

The database/RAG layer should remain the source of current information.

---

# 16. Pipeline L — Human Feedback

Scouts should be able to evaluate recommendations.

Example:

```text
Player A

[Interested]
[Maybe]
[Reject]

Rating: 1–5

Reason:
________________
```

Store:

```text
scout_feedback
----------------
id
scout_id
club_id
player_id
scouting_request_id
rating
decision
reason
created_at
```

This creates future training data for the ranking model.

## Long-term learning loop

```text
Recommendation
      ↓
Human scout
      ↓
Feedback
      ↓
Training dataset
      ↓
Ranking model v2
      ↓
Evaluation
      ↓
Deployment
```

---

# 17. Data Model

Core PostgreSQL entities:

```text
Country
Competition
Season
Club
Player
PlayerStats
PlayerClubHistory
PlayerCompetitionHistory
PlayerFeatures
ScoutingRequirement
ScoutingSearch
ScoutingRecommendation
ScoutFeedback
```

Potential document/vector entities:

```text
PlayerDocument
DocumentChunk
Embedding
```

---

# 18. API Layer

Initial FastAPI endpoints:

```http
POST /clubs
GET  /clubs/{club_id}

GET  /players/{player_id}
GET  /players/{player_id}/stats

POST /scouting/requirements
POST /scouting/search
GET  /scouting/search/{search_id}

GET  /scouting/search/{search_id}/players
GET  /scouting/search/{search_id}/players/{player_id}

POST /reports/{player_id}

POST /scouting/feedback
```

---

# 19. Recommended Technology Stack

## Backend

```text
Python
FastAPI
Pydantic
```

## Database

```text
PostgreSQL
pgvector
```

## Data processing

```text
Pandas
Polars (optional)
Python ETL
```

## ML

```text
scikit-learn
XGBoost
LightGBM
```

## LLM

Use a production LLM provider such as AWS Bedrock.

## RAG

Initially:

```text
PostgreSQL
+
pgvector
+
custom retrieval
```

Avoid adding unnecessary frameworks until they solve a real problem.

## Frontend

```text
Next.js
```

## Background processing

Initially:

```text
Python worker / scheduled jobs
```

Later:

```text
Celery
Redis
AWS EventBridge
```

depending on scale.

---

# 20. Initial Project Scope

Do not begin with every country and every division.

### Phase 1

Build a working vertical slice:

```text
Limited competitions
        ↓
Player data
        ↓
PostgreSQL
        ↓
Feature engineering
        ↓
Requirement parser
        ↓
Candidate retrieval
        ↓
Ranking
        ↓
RAG
        ↓
Scouting report
        ↓
Evals
```

### Phase 2

Expand:

- competitions
- player history
- richer features
- better ranking
- more evaluation cases

### Phase 3

Add:

- scout feedback
- learning-to-rank
- development prediction
- market/transfer constraints
- club-specific ranking models

### Phase 4

Add broader global data coverage through appropriate open/licensed sources.

---

# 21. What Is NOT in the Project

The initial architecture deliberately excludes:

- video intelligence
- player tracking
- computer vision
- match-video processing
- autonomous scouting decisions
- fine-tuning before evaluation
- unnecessary microservices
- Kubernetes

The system is **data + retrieval + ML + RAG + LLM**, with human scouts remaining in the decision loop.

---

# 22. End-to-End Example

User:

> "Find me a 19–23 year old left-footed centre-back who is strong in aerial duels, progressive in possession, and has potential to play at a higher level."

### Step 1 — LLM

Produces:

```text
Age: 19–23
Position: CB
Foot: Left
Aerial: High
Progression: High
Potential: High
```

### Step 2 — PostgreSQL

Filters the player universe.

```text
500,000
   ↓
Age
   ↓
Position
   ↓
Foot
   ↓
10,000
```

### Step 3 — Semantic retrieval

```text
10,000
   ↓
pgvector
   ↓
500
```

### Step 4 — Ranking

```text
500
 ↓
ML ranking model
 ↓
20
```

### Step 5 — Evidence retrieval

```text
Top 20
 ↓
Statistics
+
historical data
+
textual evidence
 ↓
RAG context
```

### Step 6 — LLM

Produces an evidence-backed scouting report.

### Step 7 — Evaluation

Measure:

```text
Did we understand the requirement?
Did we retrieve the right players?
Did we rank them correctly?
Did we retrieve relevant evidence?
Was the report factual and grounded?
```

### Step 8 — Improvement

Fix the component responsible for the failure.

---

# 23. Definition of Success

The project should ultimately demonstrate:

```text
Natural language scouting request
             ↓
Correct structured interpretation
             ↓
High-recall candidate retrieval
             ↓
Strong player ranking
             ↓
Relevant evidence retrieval
             ↓
Grounded scouting explanation
             ↓
Measurable evaluation
             ↓
Continuous improvement
```

The primary technical achievement is not "using an LLM."

It is building a **measurable, modular scouting intelligence pipeline in which structured football data, retrieval, ML ranking, RAG, and LLM reasoning each have clearly defined responsibilities.**
