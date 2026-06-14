"""
Finance Assistant — Configuration Module
========================================
Centralized configuration management using environment variables
and Pydantic settings. All config values are read from .env file
or system environment with sensible defaults.

Usage:
    from config import settings
    print(settings.OPENAI_API_KEY)
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load .env file from project root
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── OpenAI ──────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")
    OPENAI_MODEL: str = Field(default="gpt-4o-mini", description="LLM model name")

    # ── Database ────────────────────────────────────────────────────────
    DB_PATH: str = Field(
        default=str(PROJECT_ROOT / "finance.db"),
        description="Path to SQLite database file",
    )

    # ── Agent Configuration ─────────────────────────────────────────────
    MAX_QUERY_RESULT_ROWS: int = Field(default=100, description="Max rows returned by a query")
    MAX_ERROR_RETRIES: int = Field(default=3, description="Max SQL error correction attempts")
    GRAPH_RECURSION_LIMIT: int = Field(default=50, description="LangGraph recursion limit")

    # ── Chainlit ────────────────────────────────────────────────────────
    CHAINLIT_HOST: str = Field(default="0.0.0.0", description="Chainlit server host")
    CHAINLIT_PORT: int = Field(default=8000, description="Chainlit server port")

    # ── MCP Server Configuration ────────────────────────────────────────
    MCP_DB_SERVER_COMMAND: str = Field(
        default="python",
        description="Command to run MCP DB server",
    )
    MCP_AGENT_SERVER_COMMAND: str = Field(
        default="python",
        description="Command to run MCP agent tools server",
    )

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = True


# Singleton settings instance
settings = Settings()


# ── Schema Info (English) ────────────────────────────────────────────────────
# Centralized schema definition used by MCP DB server and agent prompts.

SCHEMA_INFO = """
Personal Finance Management System Database — 2024–2025 synthetic data:

1. accounts
   - account_id   (INTEGER): Unique account identifier
   - account_name (TEXT):    Account name (e.g. "Checking Account", "Credit Card")
   - account_type (TEXT):    Account type — checking | savings | credit | cash
   - balance      (REAL):    Current balance
   - currency     (TEXT):    Currency code (TRY, USD, EUR)

2. categories
   - category_id   (INTEGER): Unique category identifier
   - category_name (TEXT):    Category name (e.g. "Groceries", "Salary", "Rent")
   - category_type (TEXT):    income or expense

3. transactions
   - transaction_id   (INTEGER): Unique transaction identifier
   - transaction_date (TEXT):    Transaction date — YYYY-MM-DD
   - amount           (REAL):    Transaction amount
   - transaction_type (TEXT):    income | expense | transfer
   - category_id      (INTEGER): FK → categories(category_id)
   - account_id       (INTEGER): FK → accounts(account_id)
   - description      (TEXT):    Transaction description
   - status           (TEXT):    completed | pending | cancelled

4. budgets
   - budget_id    (INTEGER): Unique budget identifier
   - category_id  (INTEGER): FK → categories(category_id)
   - month        (INTEGER): Month (1–12)
   - year         (INTEGER): Year (2024 or 2025)
   - limit_amount (REAL):    Monthly budget limit
   - spent_amount (REAL):    Actual spending amount

5. invoices
   - invoice_id     (INTEGER): Unique invoice identifier
   - vendor_name    (TEXT):    Vendor / service provider name
   - invoice_date   (TEXT):    Invoice date — YYYY-MM-DD
   - due_date       (TEXT):    Payment due date — YYYY-MM-DD
   - amount         (REAL):    Invoice amount
   - status         (TEXT):    paid | pending | overdue
   - category_id    (INTEGER): FK → categories(category_id)
   - transaction_id (INTEGER): FK → transactions(transaction_id) — nullable

Critical date formatting note:
    The budgets table stores month (INTEGER: 1–12) and year (INTEGER) separately.
    To convert to a date string, ALWAYS use printf():
        CORRECT:   printf('%04d-%02d', year, month)  → '2024-01'
        INCORRECT: year || '-' || month || '-01'     → '2024-1-01' (SQLite parses as NULL!)

CRITICAL JOIN rules (read carefully before writing ANY query):

A) budgets.spent_amount is PRE-COMPUTED:
   Use budgets.spent_amount directly for "actual vs budget" comparisons.
   Do NOT join transactions with budgets to compute spending —
   this creates fan-out because transactions lack month/year columns.

B) If you MUST join transactions with budgets for monthly analysis:
   Extract month/year from transactions.transaction_date using:
       CAST(strftime('%m', t.transaction_date) AS INTEGER) AS tx_month
       CAST(strftime('%Y', t.transaction_date) AS INTEGER) AS tx_year
   Then join: tx_month = b.month AND tx_year = b.year

C) ALWAYS use table aliases on EVERY column in JOIN queries:
   CORRECT:   SELECT t.amount, b.limit_amount FROM transactions t JOIN budgets b ON t.category_id = b.category_id
   INCORRECT: SELECT amount, limit_amount FROM transactions JOIN budgets ON category_id = category_id
   (ambiguous column errors when both tables share column names like category_id)

D) CTE column references in outer queries:
   CORRECT:   WITH cte AS (SELECT t.id, b.year FROM ...) SELECT cte.year FROM cte
   INCORRECT: WITH cte AS (SELECT t.id, b.year FROM ...) SELECT b.year FROM cte
   (alias 'b' only exists inside the CTE definition, not outside)
"""

# Agent system prompt fragments used by the MCP agent server
AGENT_SYSTEM_PROMPTS = {
    "guardrails": (
        "You are a strict scope filter for a personal finance assistant. "
        "You determine whether a user's question is about financial data analysis, "
        "a simple greeting, or out of scope."
    ),
    "sql_generator": (
        "You are a senior SQL developer specialized in financial databases. "
        "Produce only valid SQLite queries. Do NOT include formatting, markdown, or explanations."
    ),
    "sql_validator": (
        "You are a SQL quality auditor. Detect fan-out (cartesian product) patterns "
        "in multi-table aggregation queries and rewrite them using CTE structures. "
        "Produce only valid SQLite syntax."
    ),
    "error_handler": (
        "You are a database expert who analyzes failed SQL queries using schema "
        "knowledge and error messages to produce corrected queries."
    ),
    "sanity_checker": (
        "You are a financial data quality expert. You evaluate whether SQL query "
        "results are consistent with the question and within realistic ranges. "
        "Respond ONLY in JSON format."
    ),
    "analyst": (
        "You are a helpful financial analyst who interprets database query results "
        "in clear, plain language. Present numbers clearly, keep it concise and focused."
    ),
    "graph_decider": (
        "You are a data visualization expert. Determine whether query results would "
        "benefit from a chart and which chart type is most appropriate."
    ),
    "visualizer": (
        "You are a data visualization specialist. Generate executable Plotly code "
        "without any markdown or explanations."
    ),
}
