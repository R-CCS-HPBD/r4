"""
Database MCP Server (M-102).

Provides tools for database queries with sandboxing and security.

Security features:
- Read-only mode option
- Query allowlist/blocklist patterns
- Result size limits
- Statement timeout
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from ..base import MCPServer, Tool, ToolResult

logger = logging.getLogger(__name__)


# Dangerous SQL patterns (blocklist)
BLOCKED_PATTERNS = [
    r"\bDROP\s+(TABLE|DATABASE|INDEX|VIEW|SCHEMA)\b",
    r"\bTRUNCATE\b",
    r"\bDELETE\s+FROM\b",
    r"\bALTER\s+(TABLE|DATABASE)\b",
    r"\bCREATE\s+(TABLE|DATABASE|INDEX|VIEW)\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\b(pg_|information_schema\.|sys\.)\w+",  # System tables
]

# Safe read-only patterns (allowlist for read_only mode)
READONLY_PATTERNS = [
    r"^\s*SELECT\b",
    r"^\s*WITH\s+\w+\s+AS\s*\(",
    r"^\s*EXPLAIN\b",
    r"^\s*SHOW\b",
]


class DatabaseServer(MCPServer):
    """
    MCP server for database access.

    Supports PostgreSQL connections with query sandboxing.
    """

    name = "database"
    version = "1.0.0"

    def __init__(
        self,
        connection_string: str,
        read_only: bool = True,
        max_results: int = 1000,
        timeout_seconds: int = 30,
    ):
        """
        Initialize database server.

        Args:
            connection_string: PostgreSQL connection string
            read_only: Only allow read operations
            max_results: Maximum rows to return
            timeout_seconds: Query timeout
        """
        super().__init__()
        self.connection_string = connection_string
        self.read_only = read_only
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self._conn = None

    def _validate_query(self, query: str) -> tuple[bool, Optional[str]]:
        """Validate query against security rules."""
        query_upper = query.upper().strip()

        # Check blocklist
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, query_upper, re.IGNORECASE):
                return False, f"Query matches blocked pattern: {pattern}"

        # In read_only mode, check allowlist
        if self.read_only:
            allowed = False
            for pattern in READONLY_PATTERNS:
                if re.search(pattern, query_upper, re.IGNORECASE):
                    allowed = True
                    break
            if not allowed:
                return False, "Query not allowed in read-only mode"

        return True, None

    @Tool(
        name="query",
        description="Execute a SQL query and return results",
    )
    async def query(
        self,
        sql: str,
        params: Optional[list[Any]] = None,
    ) -> ToolResult:
        """
        Execute SQL query.

        Args:
            sql: SQL query to execute
            params: Query parameters for parameterized queries
        """
        # Validate query
        valid, error = self._validate_query(sql)
        if not valid:
            return ToolResult.error(f"Query validation failed: {error}")

        try:
            import asyncpg
        except ImportError:
            return ToolResult.error("asyncpg not installed. Install with: pip install asyncpg")

        try:
            if not self._conn:
                self._conn = await asyncpg.connect(
                    self.connection_string,
                    timeout=self.timeout_seconds,
                )

            # Set statement timeout using parameterized query value.
            # The value is always an integer (self.timeout_seconds * 1000) so
            # no SQL injection risk, but we parameterize anyway as defense-in-depth.
            await self._conn.execute(
                "SET statement_timeout = $1",
                str(self.timeout_seconds * 1000),
            )

            # Execute query
            if params:
                rows = await self._conn.fetch(sql, *params)
            else:
                rows = await self._conn.fetch(sql)

            # Limit results
            if len(rows) > self.max_results:
                rows = rows[: self.max_results]
                truncated = True
            else:
                truncated = False

            # Convert to list of dicts
            results = [dict(row) for row in rows]

            return ToolResult.success(
                {
                    "rows": results,
                    "count": len(results),
                    "truncated": truncated,
                }
            )

        except asyncpg.PostgresError as e:
            return ToolResult.error(f"Database error: {e}")
        except asyncio.TimeoutError:
            return ToolResult.error(f"Query timed out after {self.timeout_seconds}s")
        except Exception as e:
            logger.exception("Query failed")
            return ToolResult.error(f"Query failed: {e}")

    @Tool(
        name="list_tables",
        description="List all tables in the database",
    )
    async def list_tables(
        self,
        schema: str = "public",
    ) -> ToolResult:
        """
        List tables in a schema.

        Args:
            schema: Schema name (default: public)
        """
        query = """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = $1
            ORDER BY table_name
        """
        return await self.query(query, [schema])

    @Tool(
        name="describe_table",
        description="Get column information for a table",
    )
    async def describe_table(
        self,
        table: str,
        schema: str = "public",
    ) -> ToolResult:
        """
        Describe table structure.

        Args:
            table: Table name
            schema: Schema name (default: public)
        """
        query = """
            SELECT
                column_name,
                data_type,
                character_maximum_length,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
        """
        return await self.query(query, [schema, table])

    @Tool(
        name="get_statistics",
        description="Get row count and size statistics for a table",
    )
    async def get_statistics(
        self,
        table: str,
        schema: str = "public",
    ) -> ToolResult:
        """
        Get table statistics.

        Args:
            table: Table name
            schema: Schema name (default: public)
        """
        query = """
            SELECT
                pg_size_pretty(pg_table_size($1::regclass)) AS table_size,
                pg_size_pretty(pg_indexes_size($1::regclass)) AS indexes_size,
                pg_size_pretty(pg_total_relation_size($1::regclass)) AS total_size,
                (SELECT reltuples::bigint FROM pg_class WHERE relname = $2) AS estimated_rows
        """
        full_name = f"{schema}.{table}"
        return await self.query(query, [full_name, table])

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None


def create_server(
    connection_string: str,
    read_only: bool = True,
    max_results: int = 1000,
    timeout_seconds: int = 30,
) -> DatabaseServer:
    """Create a database MCP server instance."""
    return DatabaseServer(
        connection_string=connection_string,
        read_only=read_only,
        max_results=max_results,
        timeout_seconds=timeout_seconds,
    )
