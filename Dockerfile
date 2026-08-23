# Builds the MailFlat MCP server (packages/mcp) from source in this repository.
#
# This file lives at the ROOT of the public mailflat-sdks mirror on purpose: Glama and
# other MCP indexers build the repository root, and the mirror holds six SDKs, so the
# Dockerfile has to say which one is the server.
#
# Connected to:
#   - builds:   packages/mcp (console script `mailflat-mcp`)
#   - synced by: deploy/sync-sdks-public.sh (copied to the mirror root)
#
# Usage:
#   docker build -t mailflat-mcp .
#   docker run --rm -i -e MAILFLAT_API_KEY=mf_live_... mailflat-mcp
#
# The transport is stdio, so `-i` is required: the client talks JSON-RPC over stdin/stdout.
# No API key is needed to start or to answer an introspection (tools/list) request; the key
# is read per call, so an indexer can inspect the server without credentials.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Only the MCP package is copied: it depends on the `mailflat` SDK through PyPI, not through
# a path, so pulling in the other five packages would only slow the build down.
COPY packages/mcp/ /app/packages/mcp/

RUN pip install --no-cache-dir ./packages/mcp

# An MCP server has no reason to run as root.
RUN useradd --create-home --uid 10001 mcp
USER mcp

ENTRYPOINT ["mailflat-mcp"]
