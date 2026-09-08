# Sleeper Fantasy Football MCP Server

[![CI](https://github.com/Edramos123/sleeper-mcp-league/actions/workflows/ci.yml/badge.svg)](https://github.com/Edramos123/sleeper-mcp-league/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A Model Context Protocol (MCP) server for fantasy football, built with [FastMCP](https://github.com/jlowin/fastmcp) and the [Sleeper Fantasy Sports API](https://docs.sleeper.app/). One deployment can serve multiple Sleeper leagues at once.

## Quick Start

### Run Your Own Instance

```bash
# Clone and setup
git clone https://github.com/Edramos123/sleeper-mcp-league.git
cd sleeper-mcp-league
uv sync

# Run for Claude Desktop (STDIO)
uv run python sleeper_mcp.py

# Run as a web server (SSE)
uv run python sleeper_mcp.py http
```

### Connect to a Deployed Instance

Add to your Claude Desktop config (or any MCP client that supports SSE):

```json
{
  "mcpServers": {
    "sleeper": {
      "url": "https://<your-render-service>.onrender.com/sse"
    }
  }
}
```

Replace `<your-render-service>` with your actual Render service URL (Render dashboard → your service → the URL shown at the top).

#### Token Bowl Chat Authentication

To use the optional Token Bowl Chat tools, add your API key as a query parameter:

```json
{
  "mcpServers": {
    "sleeper": {
      "url": "https://<your-render-service>.onrender.com/sse?api_key=your_token_bowl_chat_api_key"
    }
  }
}
```

Get your API key from your Token Bowl Chat profile. Without this parameter, Token Bowl Chat tools will not be available.

## Configuration

Create a `.env` file (see `.env.example`):

```bash
# Default Sleeper league ID, used when a tool call omits its optional
# league_id argument.
SLEEPER_LEAGUE_ID=123456789

# Optional: JSON object mapping friendly names to Sleeper league IDs, so you
# can run one deployment across multiple leagues. Pass any of these names
# as a tool's league_id argument instead of a raw ID.
SLEEPER_LEAGUES={"tejas": "...", "work": "...", "family": "..."}

# Optional: your Sleeper user_id. Lets get_my_roster() find your roster in
# any configured league by matching it against each roster's owner_id,
# instead of having to know the roster_id per league.
SLEEPER_USER_ID=123456789

# Optional: Redis for caching. If unset or unreachable, the server falls
# back to an in-memory cache automatically - it still works on a single
# instance with no Redis attached, just without cross-restart persistence.
REDIS_URL=redis://localhost:6379

# Optional: Fantasy Nerds API for supplementary injury/news data.
# Player projections do NOT require this - they come from Sleeper's own
# projections API (no key needed). See Player Data tools below.
FFNERD_API_KEY=your_api_key_here
```

**Note:** Token Bowl Chat authentication is handled via query parameter (`?api_key=your_key`) in the SSE connection URL, not through environment variables.

## Multi-League Support

Every league-scoped tool accepts an optional `league_id` argument: a raw Sleeper league ID, a friendly name from `SLEEPER_LEAGUES`, or omitted entirely to use `SLEEPER_LEAGUE_ID`. Use `list_configured_leagues` to see what's configured, and `get_my_roster` to find your roster in any of them without needing to know its roster ID.

## Available Tools

The server provides 50 MCP tools for fantasy football operations:

### League Operations
- `get_league_info` - League settings and configuration
- `list_configured_leagues` - Leagues configured via `SLEEPER_LEAGUES`, with live name/season/team count/scoring type
- `get_league_rosters` - All team rosters
- `get_roster` - Detailed roster with player data, stats, and projections
- `get_my_roster` - Your roster in a league, found automatically via `SLEEPER_USER_ID`
- `get_league_users` - League participants
- `get_league_matchups` - Weekly matchups
- `get_league_transactions` / `get_recent_transactions` - Trades and waivers
- `get_league_traded_picks` - Traded future draft picks
- `get_league_drafts` - Draft info
- `get_league_winners_bracket` - Playoff brackets

### Player Data
- `search_players_by_name` - Find players by name
- `get_player_by_sleeper_id` - Get player details
- `get_trending_players` - Trending adds/drops
- `get_player_stats_all_weeks` - Season stats
- `get_waiver_wire_players` - Available free agents, ranked by projected points
- `get_waiver_analysis` - Waiver recommendations

Weekly/rest-of-season projections and actuals come from Sleeper's own projections API (`get_roster`, `get_my_roster`, `get_waiver_wire_players`) - no API key required. Fantasy Nerds (`FFNERD_API_KEY`) supplements this with injury status and news when configured.

### Token Bowl Chat (25 tools)
*Requires API key authentication*

**Messaging:**
- `token_bowl_chat_send_message` - Send messages to chat room or DMs
- `token_bowl_chat_get_messages` - Retrieve chat room messages
- `token_bowl_chat_get_direct_messages` - Retrieve private messages

**User Management:**
- `token_bowl_chat_get_my_profile` - View your profile
- `token_bowl_chat_get_user_profile` - View other users' profiles
- `token_bowl_chat_update_my_username` - Change your username
- `token_bowl_chat_update_my_webhook` - Configure webhooks
- `token_bowl_chat_update_my_logo` - Set profile logo
- `token_bowl_chat_get_users` - List all users
- `token_bowl_chat_get_online_users` - See who's online
- `token_bowl_chat_get_available_logos` - View logo options
- `token_bowl_chat_regenerate_api_key` - Rotate your API key

**Unread Messages:**
- `token_bowl_chat_get_unread_count` - Get unread message counts
- `token_bowl_chat_get_unread_messages` - Fetch unread room messages
- `token_bowl_chat_get_unread_direct_messages` - Fetch unread DMs
- `token_bowl_chat_mark_message_read` - Mark message as read
- `token_bowl_chat_mark_all_messages_read` - Mark all as read

**Admin Tools** (requires admin privileges):
- `token_bowl_chat_admin_get_all_users` - View all user profiles
- `token_bowl_chat_admin_get_user` - View specific user details
- `token_bowl_chat_admin_update_user` - Modify user profiles
- `token_bowl_chat_admin_delete_user` - Delete user accounts
- `token_bowl_chat_admin_get_message` - View any message
- `token_bowl_chat_admin_update_message` - Edit messages
- `token_bowl_chat_admin_delete_message` - Delete messages

### Utility
- `get_nfl_schedule` - Weekly game schedule
- `health_check` - Server status, including which named leagues are configured
- `token_bowl_chat_health_check` - Token Bowl Chat connectivity

## Development

```bash
# Run tests
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .

# Rebuild the player cache
uv run python build_cache.py

# Check cache status
uv run python cache_client.py status
```

See [CLAUDE.md](CLAUDE.md) for detailed development instructions.

## Project Structure

```
sleeper-mcp-league/
├── sleeper_mcp.py              # MCP tool definitions
├── lib/                        # Reusable business logic modules
│   ├── validation.py           # Parameter validation utilities
│   ├── decorators.py           # MCP tool decorator (logging, error handling)
│   ├── enrichment.py           # Player data enrichment functions
│   └── league_tools.py         # League operation business logic
├── cache_client.py             # Player data cache interface
├── cache_backend.py            # Redis client, with an in-memory fallback
├── build_cache.py              # Player cache building and refreshing (Sleeper + Fantasy Nerds)
├── sleeper_projections_client.py  # Sleeper's undocumented projections/stats API
├── scripts/                    # Utility scripts
├── tests/                      # Test suite
├── data/                       # Data files and analyses
├── picks/                      # Weekly picks
├── slopups/                    # Weekly summaries
└── scratchpads/                # Development notes
```

### Architecture Highlights

**Modular Design**: Business logic is extracted into focused modules for:
- **Validation** - Reusable parameter validation across all tools
- **Enrichment** - Player data enrichment with stats, projections, trending data
- **League Operations** - Complex league business logic (rosters, matchups, transactions)
- **Decorators** - Shared logging and error handling patterns

**Separation of Concerns**: MCP tools in `sleeper_mcp.py` are thin wrappers that:
1. Define tool interfaces and documentation
2. Validate parameters using `lib.validation`
3. Call business logic from `lib/` modules
4. Return formatted responses

**Testability**: Business logic in `lib/` modules can be unit tested independently of MCP framework integration.

## License

MIT

---

Built with ❤️ for my fantasy football leagues
