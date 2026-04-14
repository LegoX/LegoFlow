# The Auto Pipeline for LLM Development

### Background 

In the process of LLM development, it always need quite complicated pipeline from data curation, sft & rl training, evaluation. The actual implementation usually need back-and-forth iteration of data, training strategies etc to make the LLM behave better. Now, with this project, we aim to bring self-evolving agent to automatically realize the overall pipeline. Our ultimate goal: users can verbally interact with the agent to develop LLMs, while the agent provide human-readable textual feedback for users to see the progress.

Throughout this project, we use software engineering (swe) as an concrete example. However, this is still at the initial stage of design, so let's be abstractive and general enough. With this project, we aim to realize:

- Human readable config: Users can easily learn the current configuration and status for each sub-module. For instance, for the data composer, users know what are the registered datset and data mixtures; for the SWE-gen,

- Long-running automatic data curation process: usually data curation is loosely dependent on the rest modules (e.g., training or evaluation). Therefore, you can prepare the seed data (e.g., the repo issues in the context of swe), generate the trajectories with the user-specified teacher model.

- Data composer:

- Automatic Training and Evalaution: the Agent can automatically run the training, monitor the evaluation process. Based on the evaluation result, the agent can adjust the necessary hyper-parameters pre-defined in the config file by human. In addition, the agent can also summarize scientific findings from the experiments, and list them as bullets as key know-hows back to the users


To realize the above goals, I have a rough plan for this overall project, yet you can freely modify or improve my structure as follows:

The design has four layers:

### Layer 1: User Interface

- Agent as an Orchestrator: Powered by tools like claude-code, acting as the intelligent controller that interact with the overall system. 

- Human-readable Config: A high-level entry config that Manages sessions, configurations, and system status in human-readable format.

- User interacts bidirectionally with the UI Layer

SWE-gen feeds generated instances downward to Data Composer


### Layer 2: Core Processing

This part include four parts

- SWE-gen: Generates software engineering instances/training data
- Data Composer: Aggregates self-generated SWE data and other domain data; manages quality control, dynamic updates, and data mixtures
- Training Engine: Executes training using LF (Learning from Feedback) or VeRL methodologies
- Evaluation: Assesses model performance and training outcomes

Data Composer, Training Engine, and Evaluation form a closed feedback loop for iterative improvement

### Layer 3: Execution Environment (Habor)

Acts as the execution bridge supporting various coding agents: cc, opencode, openhands, etc. All core components leverage Habor for execution, which runs on the K8S cluster with isolated sandboxes


### Layer 4: Infrastructure

K8S Cluster with SWE Sandboxes: Kubernetes-orchestrated containerized environments providing isolated SWE execution sandboxes

┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          USER                                               │
└─────────────────────────────────────────────────┬───────────────────────────────────────────┘
                                                  │
                                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          UI LAYER                                           │
│  ┌─────────────────────────────────────┐    ┌─────────────────────────────────────────────┐ │
│  │   Agent as an Orchestrator          │    │   Human-readable Config                     │ │
│  │   (powered by e.g., claude-code)    │    │   (session, config, status, etc.)           │ │
│  └─────────────────────────────────────┘    └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┬───────────────────────────────────────────┘
                                                  │
                          ┌───────────────────────┴───────────────────────┐
                          │                                               │
                          ▼                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    CORE PROCESSING LAYER                                    │
│                                                                                             │
│   ┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐                 │
│   │    SWE-gen      │───────▶│  Data Composer  │◀──────▶│ Training Engine │◀────────┐       │
│   │ (Instance Gen)  │        │ (Self SWE data, │        │  (LF or VeRL)   │         │       │
│   │                 │        │  Domain data,   │        │                 │         │       │
│   │                 │        │  Quality mgmt,  │        │                 │         │       │
│   │                 │        │  Dynamic update,│        │                 │         │       │
│   │                 │        │  Mixtures)      │        │                 │         │       │
│   └────────┬────────┘        └────────┬────────┘        └────────┬────────┘         │       │
│            │                          │                          │                  │       │
│            │                          └──────────────────────────┘                  │       │
│            │                                                                     │       │
│            │                          ┌─────────────────┐                         │       │
│            │                          │   Evaluation    │◀────────────────────────┘       │
│            │                          │                 │                                 │
│            │                          └────────┬────────┘                                 │
│            │                                   │                                           │
└────────────┼───────────────────────────────────┼───────────────────────────────────────────┘
             │                                   │
             │         ┌─────────────────────────┘
             │         │
             ▼         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                            HABOR                                            │
│                  (Support: cc, opencode, openhands, etc.)                                   │
└─────────────────────────────────────────────────┬───────────────────────────────────────────┘
                                                  │
                                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   INFRASTRUCTURE LAYER                                      │
│                          K8S Cluster with SWE Sandboxes                                     │
└─────────────────────────────────────────────────────────────────────────────────────────────┘