# Finance Assistant 💰

Welcome! This assistant allows you to query your 2024–2025 financial data
using **natural language** — no SQL knowledge required.

## What Can You Ask?

- 💳 Account balances and transaction history
- 📊 Category-based income / expense analysis
- 🎯 Budget tracking — overruns and savings
- 🧾 Invoice and payment statuses
- 📈 Monthly / yearly financial trends

## How It Works

1. Type your question in the chat box
2. The assistant generates SQL, runs it against the database,
   and returns the result in clear language
3. When appropriate, you'll also get an interactive chart

## Architecture

This assistant is powered by a **multi-agent LangGraph workflow**
with a **Model Context Protocol (MCP) layer** for tool execution.

Each of the 9 specialized agents communicates exclusively through
the MCP layer — never through direct function calls. This makes
the system more modular, testable, and production-ready.
