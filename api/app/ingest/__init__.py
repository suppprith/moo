"""Ingestion: connectors that fetch domain sources into `document` rows.

All connectors share the HTTP `Fetcher` (rate limiting, robots, caching,
incremental re-fetch) and implement the `Connector` interface in `base.py`.
"""
