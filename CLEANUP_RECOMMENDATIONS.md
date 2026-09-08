# Codebase Cleanup Recommendations

## Completed Cleanup (Issue #75)
✅ Removed test/example scripts (`example_trade_parse.py`, `fetch_week3_data.py`)
✅ Consolidated `wrapups/` into `slopups/` directory
✅ Standardized picks directory naming (removed `week_4`, `week_5` in favor of `week4`, `week5`)
✅ Created `data/` directory for JSON data and analysis files
✅ Created `scripts/` directory for utility scripts
✅ Consolidated all scratchpads into `scratchpads/` directory
✅ Updated README tagline for Token Bowl community

## Completed Since (multi-league support work)
✅ Modularized `sleeper_mcp.py` business logic into `lib/` (`validation.py`,
   `decorators.py`, `enrichment.py`, `league_tools.py`) - not the `tools/`
   split originally proposed below, but the same goal achieved
✅ Added graceful degradation when Fantasy Nerds is unavailable (missing/invalid
   `FFNERD_API_KEY` no longer breaks the cache build or leaves it silently empty)
✅ `health_check` tool verifies Redis/cache and Sleeper API status
✅ Response-shape validation with diagnostic logging on the Sleeper/Fantasy Nerds
   fetches in `build_cache.py`, so a malformed response is diagnosable instead of
   crashing the cache build with an opaque `AttributeError`

## Additional Recommendations for Future Improvements

### 1. Documentation Structure
- **Add docs/ directory** for comprehensive documentation
  - Move TRADE_PARSER.md to docs/
  - Create API.md documenting all MCP tools
  - Add CONTRIBUTING.md with development guidelines

### 2. Code Organization
- ~~**Consolidate cache functionality**: Merge `build_cache.py` into
  `cache_client.py`~~ - superseded: the cache layer went the other way on
  purpose. It's now `cache_client.py` (read path) + `build_cache.py` (build
  path) + `cache_backend.py` (Redis-or-in-memory backend selection) +
  `sleeper_projections_client.py` (separately-cached projections/actuals).
  Each has one clear job; merging them back would just make one big file.

### 3. Testing Improvements
- **Add test coverage for utility scripts** in scripts/
- **Create mock data fixtures** instead of relying on VCR cassettes
- **Add integration tests** for Redis cache operations

### 4. Development Experience
- **Add Makefile** for common commands:
  ```makefile
  test:
      uv run pytest

  lint:
      uv run ruff check . && uv run ruff format . --check

  format:
      uv run ruff format .

  cache-refresh:
      uv run python build_cache.py
  ```

- **Improve .gitignore**:
  ```
  # Add patterns for test artifacts
  *.test.json
  *.tmp.md
  test_*.py
  ```

### 5. Configuration Management
- **Create config.py** for centralized configuration:
  - Move all environment variable loading to one place
  - Add validation for required env vars
  - Provide sensible defaults

### 6. Logging and Monitoring
- **Standardize logging** across all modules
- **Add structured logging** for production debugging
- ~~Create health check endpoint that verifies Redis/Sleeper/Fantasy Nerds~~
  ✅ done - the `health_check` MCP tool covers this (no separate HTTP route)

### 7. Data Management
- **Archive old weekly data** - Move past weeks' picks/slopups to an archive/
- **Add data retention policy** - Clean up old scratchpads after merging
- **Version control data schema** - Track changes to cached data structure

### 8. CI/CD Improvements
- **Add pre-commit hooks** locally (already in CI)
- **Add deployment smoke tests** after Render deployment
- **Add automated dependency updates** via Dependabot

### 9. Performance Optimizations
- **Implement batch operations** for multiple player lookups
- **Add request caching** for frequently accessed league data
- **Optimize Redis memory usage** with better key expiration

### 10. Error Handling
- **Add retry logic** for transient API failures
- **Improve error messages** with actionable suggestions
- ~~Add graceful degradation when Fantasy Nerds is unavailable~~ ✅ done - see "Completed Since" above

## Priority Order

1. **High Priority** (Do soon):
   - ~~Modularize sleeper_mcp.py for maintainability~~ ✅ done, see above
   - ~~Consolidate cache functionality~~ superseded, see above
   - Add Makefile for developer convenience

2. **Medium Priority** (Nice to have):
   - Add docs/ directory structure
   - Improve test coverage
   - Standardize logging

3. **Low Priority** (Future considerations):
   - Performance optimizations
   - Archive old data
   - Advanced monitoring

## Notes
These recommendations aim to improve code maintainability, developer experience, and system reliability without adding unnecessary complexity. Implement based on actual pain points as they arise.