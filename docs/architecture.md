# Ledger Architecture

Ledger is a private evidence and review layer. Linear owns plans and progress,
Google Calendar owns scheduled and actual time, and Git repositories own notes
and code history. Ledger reads those systems, produces reviews, and writes back
only after approval.

## System map

```mermaid
flowchart LR
    user["User"]

    subgraph sourceLayer["Sources of truth"]
        direction TB
        linear["Linear<br/>Initiatives, projects, issues, weekly cycles"]
        calendar["Google Calendar<br/>Review reminder and focus time"]
        git["Git repositories<br/>MyNotes and code changes"]
        interactions["Model interactions<br/>Codex now, Claude later"]
        todoist["Todoist<br/>Optional personal reminders"]
    end

    subgraph accessLayer["Access layer"]
        direction TB
        mcp["MCP connectors<br/>Linear, Calendar, optional Todoist"]
        local["Local Python scripts<br/>Git and filesystem evidence"]
        hooks["Lifecycle hooks<br/>Conversation and subagent events"]
    end

    subgraph ledgerLayer["Ledger private boundary"]
        direction TB
        skills["Coordinator skills<br/>Weekly review and monthly review"]
        agents["Read-only subagents<br/>Collect, classify, audit"]
        scripts["Deterministic scripts<br/>Sync, collect, score, report"]
        privateStore["Private local store<br/>.ledger/*.json and *.jsonl"]
        sanitizer["Privacy filter<br/>Aggregate and anonymize"]
    end

    subgraph outputLayer["Outputs"]
        direction TB
        privateReport["Private reports"]
        approvedUpdates["Approved Linear and Calendar updates"]
        publicData["Sanitized summary JSON<br/>No raw content or paths"]
        dashboard["Public GitHub Pages dashboard<br/>Future extension"]
    end

    user --> skills

    linear --> mcp
    calendar --> mcp
    todoist -. optional .-> mcp
    git --> local
    interactions --> hooks

    mcp --> privateStore
    local --> privateStore
    hooks --> privateStore
    skills --> agents
    privateStore --> agents
    agents --> scripts
    scripts --> privateStore
    scripts --> privateReport
    scripts --> approvedUpdates
    privateStore --> sanitizer
    sanitizer --> publicData
    publicData -. future .-> dashboard

    user -. approves .-> approvedUpdates

    classDef source fill:#e8f0f7,stroke:#49677d,color:#17252f;
    classDef control fill:#eef4ea,stroke:#54705a,color:#1c2d20;
    classDef private fill:#fff3d6,stroke:#8a6a24,color:#31250d;
    classDef output fill:#f3ece8,stroke:#7c5f54,color:#30211c;
    classDef future fill:#f4f4f4,stroke:#777,color:#333,stroke-dasharray: 5 5;

    class linear,calendar,git,interactions source;
    class mcp,local,hooks,skills,agents,scripts control;
    class privateStore,sanitizer private;
    class privateReport,approvedUpdates,publicData output;
    class todoist,dashboard future;
```

## Weekly run

```mermaid
sequenceDiagram
    actor U as User
    participant S as Weekly review skill
    participant L as Linear MCP
    participant G as Local Git scripts
    participant I as Interaction hooks
    participant C as Calendar MCP
    participant A as Subagents
    participant P as Scoring and report scripts

    U->>S: Start weekly review for an exact date range
    S->>G: Check repository synchronization
    S->>L: Read current cycle, tasks, and completion
    S->>G: Collect approved note and code changes
    S->>I: Read private Codex and Claude evidence
    S->>C: Read focus-time evidence
    S->>A: Collect facts, classify themes, audit evidence
    A-->>S: Themes, progress estimates, and data gaps
    S->>P: Calculate index and build private report
    P-->>S: Score components and review draft
    S-->>U: Show report and proposed external changes
    U->>S: Approve or revise
    S->>L: Update next cycle and project progress
    S->>C: Update one review reminder and approved time blocks
    S-->>U: Save final private report
```

## Ownership rules

| Concern | Owner | Ledger behavior |
| --- | --- | --- |
| Weekly tasks | Linear cycle and issues | Read completion; preview writes |
| Work themes | Linear projects | Link evidence and summarize progress |
| Long-term goals | Linear initiatives or projects | Review monthly |
| Scheduled and actual time | Google Calendar | Read focus time; write approved blocks only |
| Notes | MyNotes Git history | Read approved paths and diffs |
| Code activity | Local Git history | Read commits and diffs across configured identities |
| Model interactions | Local hooks and exports | Keep raw text private |
| Weekly index | Ledger scripts | Calculate deterministically from reviewed inputs |
| Public dashboard | Sanitized aggregates only | Never receive raw paths, prompts, titles, or notes |

## Trust boundaries

1. Raw evidence stays in `.ledger/` and is never committed.
2. MCP credentials stay in the connector authorization layer, not in this repository.
3. Subagents are read-only; the coordinator performs approved external writes.
4. Missing data is reported as unknown, not treated as zero progress.
5. Todoist is optional and must not duplicate Linear's weekly plan.
6. Public export is a separate explicit action after privacy filtering.
