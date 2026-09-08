# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Model Context Protocol server for fantasy football leagues using the Sleeper Fantasy Sports API. Built with FastMCP. One deployment can serve multiple Sleeper leagues: `SLEEPER_LEAGUE_ID` sets the default league, and every league-scoped tool accepts an optional `league_id` argument (a raw Sleeper league ID or a friendly name from `SLEEPER_LEAGUES`) to target a different one. Provides 50 tools to interact with fantasy football data (25 Sleeper/league tools, 25 optional Token Bowl Chat tools).

**Context**: This is part of the larger `tokenbowl` system - an LLM-powered fantasy football league management system.

### Architecture

The codebase follows a modular architecture with clear separation of concerns:

- **MCP Tool Layer** (`sleeper_mcp.py`) - Tool definitions and interfaces
- **Business Logic Layer** (`lib/`) - Reusable modules for core functionality
  - `validation.py` - Parameter validation utilities
  - `decorators.py` - MCP tool decorator (logging, error handling)
  - `enrichment.py` - Player data enrichment functions
  - `league_tools.py` - League operation business logic
- **Cache Layer** (`cache_client.py`, `build_cache.py`, `cache_backend.py`) - Player data caching: Redis when available, with an automatic in-memory fallback for single-instance deploys with no Redis attached
- **Projections Layer** (`sleeper_projections_client.py`) - Weekly/rest-of-season projections and actuals from Sleeper's own (undocumented) projections API, separately cached
- **Test Layer** (`tests/`) - comprehensive test suite across 13 files

This modular design enables:
- Independent testing of business logic
- Code reuse across tools
- Clear separation between MCP framework and business logic
- Easy maintenance and refactoring

## Directory Structure

```
sleeper-mcp-league/
├── sleeper_mcp.py              # MCP tool definitions
├── lib/                        # Reusable business logic modules
│   ├── __init__.py             # Module exports
│   ├── validation.py           # Parameter validation
│   ├── decorators.py           # MCP tool decorator
│   ├── enrichment.py           # Player enrichment
│   └── league_tools.py         # League operations
├── cache_client.py             # Player cache interface
├── cache_backend.py            # Redis client, with an in-memory fallback
├── build_cache.py              # Player cache building/refreshing (Sleeper + Fantasy Nerds)
├── sleeper_projections_client.py  # Sleeper's undocumented projections/stats API
├── scripts/                    # Utility scripts
│   ├── manual_cache_refresh.py
│   ├── parse_trade_proposal.py
│   └── extract_trade_proposal.py
├── data/                       # Data files and analyses
├── picks/                      # Weekly picks (week1, week2, etc.)
├── slopups/                    # Weekly summaries
├── scratchpads/                # Development notes and issues
├── tests/                      # Test suite (13 files)
└── .github/workflows/          # CI/CD configuration
``` 

## Development Commands

### Setup and Dependencies
```bash
# Install/sync dependencies using uv
uv sync

# Requires Python 3.11+ (pyproject.toml); .python-version pins 3.13 for local dev

# Create .env file for local development
cp .env.example .env
# Edit .env with your actual API keys and configuration
```

### Environment Variables
These are located in .env
- `SLEEPER_LEAGUE_ID`: Default Sleeper league ID, used when a tool call omits its optional `league_id` argument (defaults to Token Bowl: 1266471057523490816)
- `SLEEPER_LEAGUES`: Optional JSON object mapping friendly names to Sleeper league IDs (e.g. `{"tejas": "...", "work": "..."}`), so tool calls can pass a name instead of a raw ID. See `list_configured_leagues`.
- `SLEEPER_USER_ID`: Optional Sleeper user_id, used by `get_my_roster` to find your roster in any configured league by matching it against each roster's `owner_id`.
- `REDIS_URL`: Redis connection URL (defaults to redis://localhost:6379; if unset or unreachable, falls back to an in-memory cache automatically - see `cache_backend.py`)
- `FFNERD_API_KEY`: Optional Fantasy Nerds API key for supplementary injury/news data. Player projections do NOT require this - they come from `sleeper_projections_client.py` (Sleeper's own undocumented projections API, no key needed).

### GitHub
use `gh` for all interaction with github

### IMPORTANT: Pre-PR Checklist
Before creating any Pull Request, you MUST:
1. Run linting: `uv run ruff check .`
2. Run formatting check: `uv run ruff format . --check`
3. Fix any issues found (use `uv run ruff format .` to auto-format)
4. Run tests: `uv run pytest`

PRs will automatically fail CI if linting doesn't pass!


### Running the Server

**HTTP/SSE Mode (for web deployment):**
```bash
uv run python sleeper_mcp.py http        # Default port 8000
uv run python sleeper_mcp.py http 3000   # Custom port
```

**STDIO Mode (for Claude Desktop):**
```bash
uv run python sleeper_mcp.py
```

### Utility Commands
```bash
# Rebuild the player cache (overwrites the existing cache key)
uv run python build_cache.py

# Check cache status (age, player counts, last refresh)
uv run python cache_client.py status

# Search the cached players (sanity-check name matching)
uv run python cache_client.py search "<name>"
```

### Testing
```bash
# Install test dependencies
uv sync --extra test

# Run all tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=. --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_sleeper_mcp.py

# Run tests with VCR cassette recording (first time)
uv run pytest --vcr-record=once

# Run tests without hitting external APIs (replay mode)
uv run pytest --vcr-record=none

# Run linting (ALWAYS run before creating a PR)
uv run ruff check .
uv run ruff format . --check
```

### Pre-commit Hooks (Recommended)

To automatically check linting before committing:

```bash
# Install dev dependencies including pre-commit
uv sync --extra dev

# Install the pre-commit hooks
uv run pre-commit install

# Run hooks manually on all files
uv run pre-commit run --all-files
```

This will automatically run `ruff` linting and formatting checks before each commit, preventing linting issues from reaching PRs.

### CI/CD

**IMPORTANT: Before creating any PR, you MUST run:**
```bash
uv run ruff check .
uv run ruff format . --check
```

GitHub Actions CI runs on every push and PR:
- Tests on Python 3.13
- Runs linting with ruff (PRs will fail if linting doesn't pass)
- Provides test reports and artifacts
- Uses Redis service container for integration tests

CI workflow is defined in `.github/workflows/ci.yml`

### Deployment

The project is configured for Render deployment via `render.yaml`. When pushing to the main branch, it automatically deploys if autoDeploy is enabled.

**Production Details:**
- **Repository**: https://github.com/Edramos123/sleeper-mcp-league
- **Render Service ID / Redis Cache ID**: check the Render dashboard for this deployment - not recorded here since they're per-deployment and this repo may be run under different Render accounts/services
- **Deployment**: Auto-deploys on push to `main` branch
- **MCP Tool Prefix**: When deployed, tools are prefixed with `mcp__<connector-nickname>__` (the nickname is whatever the MCP client names this connection - e.g. `mcp__sleeper-tech__get_roster` - not a fixed value)

## Implementation Details

### MCP Tool Layer (sleeper_mcp.py)

- All tools are async functions decorated with `@mcp.tool()` and `@log_mcp_tool`
- Tools act as thin wrappers that validate parameters and call business logic
- `LEAGUE_ID` from environment variable `SLEEPER_LEAGUE_ID` (default: `1266471057523490816`); `resolve_league_id()` resolves each tool's optional `league_id` argument against it and the `SLEEPER_LEAGUES` name map
- Base API URL: `https://api.sleeper.app/v1`
- Environment-aware transport detection (HTTP/SSE for production, STDIO for local)

### Business Logic Layer (lib/)

Modular, reusable business logic extracted from MCP tools:

**lib/validation.py**:
- `validate_roster_id()`, `validate_week()`, `validate_position()`, etc.
- Standardized parameter validation across all tools
- Consistent error response formatting via `create_error_response()`

**lib/decorators.py**:
- `@log_mcp_tool` decorator for all MCP tools
- Automatic logging, error handling, and observability integration
- Logfire span management and parameter serialization

**lib/enrichment.py**:
- `enrich_player_full()`, `enrich_player_minimal()` - Player data enrichment
- `get_trending_data_map()`, `mark_recent_drops()` - Trending/waiver analysis
- `organize_roster_by_position()` - Roster organization utilities
- Reduces duplication across player-related tools

**lib/league_tools.py**:
- `fetch_roster_with_enrichment()` - Complex roster fetching with enrichment, including the Sleeper-projections overlay (see below)
- `fetch_league_matchups()`, `fetch_league_transactions()` - League operations
- Separates API calls from MCP tool interfaces

### Cache Layer

**cache_client.py**, **build_cache.py**, **cache_backend.py**:
- Player data cache built from Sleeper (full player list) + Fantasy Nerds (injuries/news, when `FFNERD_API_KEY` is configured)
- `cache_backend.get_cache_client()` returns real Redis if reachable, otherwise a process-local in-memory fallback - single-instance deploys work with no Redis attached
- 6-hour TTL with automatic refresh; gzip compression to reduce memory usage
- `build_cache.py` filters to fantasy-relevant positions immediately after fetching, to keep peak memory manageable on small instances

### Projections Layer

**sleeper_projections_client.py**:
- Populates `stats.projected` / `stats.actual` / `stats.ros_projected` from Sleeper's own undocumented projections API (`api.sleeper.com`) whenever the player cache left them null - never overwrites data the cache already provided
- One call per week covers all skill positions; cached 4 hours (separately from the main player cache) since this endpoint is undocumented and unofficial
- `overlay_projections()` is the shared entry point, used by `get_roster`/`get_my_roster` (via `fetch_roster_with_enrichment`) and `get_waiver_wire_players`
- Every failure mode (bad shape, network error, unexpected data) degrades to null with a logged warning rather than raising

### Transport Modes

- **HTTP/SSE**: For web deployment (binds to 0.0.0.0 when `RENDER` env var is set or `http` argument provided)
- **STDIO**: Default mode for Claude Desktop integration

### Deployment Configuration

**render.yaml**: Defines build and start commands for Render deployment
- Build: `pip install uv && uv sync`
- Start: `uv run python build_cache.py; uv run python sleeper_mcp.py http`
- Python version: 3.13 (specified via PYTHON_VERSION env var)
- There is no separate HTTP health-check route; use the `health_check` MCP tool to check server/cache/API status

## Key Implementation Details

- Environment variables are loaded from `.env` file using python-dotenv for local development
- The server detects deployment environment via `RENDER` env variable or command line arguments
- Port is configurable via `PORT` environment variable (required by Render) or command line
- When deployed, binds to `0.0.0.0` for external access, localhost for local development
- All API calls use `httpx.AsyncClient` with proper error handling via `raise_for_status()`
- Large-payload fetches (e.g. `build_cache.py`'s Sleeper player list and stats fetches) use extended timeouts (30s)
- Redis connection via `REDIS_URL` environment variable, with an automatic in-memory fallback (see Cache Layer)
- Player search functions utilize cached data for instant responses

## Available Tools

The server exposes 50 MCP tools organized by functionality:
- **League operations** (12 tools): info, configured-leagues list, rosters, roster detail, your-roster lookup, users, matchups, transactions (by round + recent), traded picks, drafts, playoff bracket
- **Player data** (8 tools): search, lookup, trending, season stats, waiver wire, waiver analysis, trending context, waiver priority cost
- **User operations** (1 tool): user profile lookup
- **NFL data** (1 tool): game schedule
- **ChatGPT compatibility** (2 tools): `search`, `fetch`
- **Utility** (1 tool): health check
- **Token Bowl Chat** (25 tools, optional): messaging, user/profile management, unread tracking, admin - requires API key authentication

## Dependencies

- **fastmcp>=2.11.3**: MCP server framework
- **httpx>=0.28.1**: Async HTTP client for API calls
- **redis>=5.0.0**: Redis client for caching layer
- **python-dotenv**: Environment variable loading from .env files
- **uv**: Modern Python package manager (must be installed separately for local development)

## Production Management

When deployed, you can manage the production service using Render MCP tools in Claude:
- `mcp__render__list_services` - View deployed services
- `mcp__render__get_service` - Get service details
- `mcp__render__list_logs` - View production logs
- `mcp__render__get_metrics` - Monitor performance
- `mcp__render__update_environment_variables` - Update env vars (triggers redeploy)

## Debugging Notes

- Use FastMCP docs at https://gofastmcp.com/llms.txt when debugging FastMCP issues
- Redis cache prevents hitting Sleeper API rate limits for player data
- Player search functions use cached data for instant responses
- Redis uses LRU eviction policy for optimal cache management within free tier limits