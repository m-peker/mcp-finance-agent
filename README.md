# Finance Assistant — Multi-Agent Text-to-SQL Chatbot with MCP

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![LangGraph](https://img.shields.io/badge/LangGraph-1.0.3-green.svg)
![Chainlit](https://img.shields.io/badge/Chainlit-2.9.0-orange.svg)
![MCP](https://img.shields.io/badge/MCP-1.0+-purple.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

A production-ready multi-agent chatbot that converts natural language
financial questions into SQL queries, executes them against a database,
interprets the results, and generates interactive charts — all orchestrated
through a **Model Context Protocol (MCP) layer**.

---

## Preview

### Scenario 1 — Simple Query & Chart

![Scenario 1](images/Scenario-1.gif)

### Scenario 2 — Complex Multi-Table Analysis

![Scenario 2](images/Scenario-2.gif)

---

## Architecture

![Architecture Diagram](images/architecture_diagram.png)

*Chainlit UI → LangGraph Agent Engine (9 agents) → MCP Layer (DB + Agent Servers) → SQLite & OpenAI GPT-4o-mini → Interactive Plotly Charts*

### MCP Layer (Model Context Protocol)

All agent operations flow through the MCP layer — **agents never make
direct function calls**. This clean separation makes the system modular,
testable, and production-ready.

```
┌─────────────────────────────────────────────┐
│               Chainlit UI                    │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│          LangGraph Agent Workflow            │
│                                              │
│  guardrails → sql_gen → validator → execute  │
│       ↑          ↑                    │      │
│       └──────────┼──────┐      ┌──────┘      │
│                  │      │      │              │
│           sanity_check  │  error_handler      │
│                  │      │                     │
│              analysis   │                     │
│                  │      │                     │
│           graph_decider │                     │
│                  │      │                     │
│              viz_agent  │                     │
└──────────────────┼──────┼─────────────────────┘
                   │      │
         ┌─────────▼──────▼─────────┐
         │       MCP CLIENTS        │
         └──────┬──────────┬────────┘
                │          │
    ┌───────────▼──┐  ┌───▼──────────────┐
    │  DB Server   │  │ Agent Tools Server│
    │  ─────────   │  │ ───────────────  │
    │ • get_schema │  │ • check_scope    │
    │ • exec_query │  │ • generate_sql   │
    │ • table_info │  │ • validate_sql   │
    │              │  │ • fix_sql_error  │
    │              │  │ • check_sanity   │
    │              │  │ • analyze_results│
    │              │  │ • decide_graph   │
    │              │  │ • generate_plotly│
    └──────┬───────┘  └──────┬───────────┘
           │                 │
    ┌──────▼──────┐  ┌──────▼───────────┐
    │  SQLite DB  │  │  OpenAI GPT-4o   │
    └─────────────┘  └──────────────────┘
```

### Agent Roles

| Agent | Role | MCP Tool |
|---|---|---|
| `guardrails_agent` | Scope & greeting filter | `check_scope` |
| `sql_agent` | NL → SQLite query generation | `generate_sql` |
| `sql_validator_agent` | Fan-out detection & CTE rewrite | `validate_sql` |
| `execute_sql` | Query execution (multi-statement) | `execute_query` |
| `error_agent` | Failed SQL analysis & correction (max 3×) | `fix_sql_error` |
| `sanity_check_agent` | Result reasonability verification | `check_sanity` |
| `analysis_agent` | Raw results → natural language | `analyze_results` |
| `decide_graph_need` | Chart type decision (bar/line/pie/scatter) | `decide_graph_need` |
| `viz_agent` | LLM-powered Plotly code generation | `generate_plotly` |

### LangGraph Workflow

![LangGraph Workflow](images/finance_workflow.png)

---

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Agent Orchestration | LangGraph | 1.0.3 |
| Web Interface | Chainlit | 2.9.0 |
| MCP Protocol | MCP Python SDK | 1.0+ |
| LLM | OpenAI GPT-4o-mini | — |
| Database | SQLite3 | Built-in |
| Data Processing | Pandas | 2.3.3 |
| Visualization | Plotly | 6.4.0 |
| Config | Pydantic Settings | 2.0+ |

---

## Database Schema

Synthetic financial data (2024–2025, English):

| Table | Description | Rows |
|---|---|---|
| `accounts` | Bank accounts and balances | 5 |
| `categories` | Income/expense categories | 15 |
| `transactions` | All financial transactions | ~510 |
| `budgets` | Monthly category budgets | 240 |
| `invoices` | Invoices and payment statuses | 200 |

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/username/finance-assistant.git
cd finance-assistant
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set environment variables

```bash
cp .env.example .env
# Open .env and enter your OPENAI_API_KEY
```

API key: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)

### 5. Initialize the database

```bash
python db_init.py
```

Generates synthetic financial data and creates `finance.db`.

### 6. Start the application

```bash
chainlit run app.py
```

Open: **http://localhost:8000**

---

## Sample Questions

```
What is my total income and expenses this year?
What are my top 5 spending categories?
Which months did I exceed my budget?
Do I have any unpaid or overdue invoices?
What is the total spent via credit card?
List categories that exceeded their budget in at least 3 different months
in 2024, along with their total spending and overdue invoice counts.
```

---

## Project Structure

```
finance-assistant/
│
├── app.py                      # Chainlit UI & event handlers
├── graph.py                    # LangGraph state machine definition
├── stream.py                   # Async streaming interface
├── config.py                   # Configuration & schema definitions
├── db_init.py                  # Synthetic data generation
├── finance.db                  # SQLite database (auto-generated)
├── requirements.txt            # Python dependencies
├── chainlit.md                     # Welcome screen
├── medium-article.md               # Full Medium article about this project
├── .env.example                    # API key template
│
├── mcp_servers/                # Model Context Protocol servers
│   ├── __init__.py
│   ├── db_server.py            # Database tools (schema, query, info)
│   └── agent_server.py         # LLM-powered agent tools
│
├── agents/                     # LangGraph agent nodes
│   ├── __init__.py
│   ├── mcp_client.py           # MCP client wrapper
│   └── nodes.py                # All 9 agent node implementations
│
├── images/
│   ├── Scenario-1.gif              # Simple query & chart demo
│   ├── Scenario-2.gif              # Complex multi-table analysis demo
│   ├── architecture_diagram.png    # High-level system architecture diagram
│   └── finance_workflow.png        # Auto-generated LangGraph graph (gitignored)
│
├── .chainlit/
│   ├── config.toml                 # Chainlit application settings
│   └── translations/               # UI translations
```

---

## Key Design Decisions

### MCP Layer
Every agent operation goes through the Model Context Protocol layer.
Agents call `MCPClient.call_tool("db"|"agent", tool_name, args)` —
never direct functions. This means:
- **Modularity**: Swap implementations without touching agents
- **Testability**: Mock MCP servers for unit testing
- **Production readiness**: MCP servers can run as separate processes over stdio

### Deterministic Fan-Out Detection
Before calling the LLM for SQL validation, a pure Python check runs:
```python
has_join = " JOIN " in sql.upper()
has_agg  = any(f in sql.upper() for f in ["SUM(", "COUNT(", "AVG("])
has_cte  = sql.upper().startswith("WITH ")
```
~70-80% of queries pass this check without an LLM call — saving cost and latency.

### Loop Guards
- SQL error retries: maximum 3 attempts
- Sanity check retries: maximum 1 attempt (prevents infinite loops)

---

## License

MIT
