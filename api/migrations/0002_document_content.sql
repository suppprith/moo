-- SUP-72: give `document` a body so connectors can persist fetched content
-- (chunking in Phase 1 / SUP-76 reads document.raw_text).

ALTER TABLE document ADD COLUMN raw_text     TEXT;    -- extracted main content (markdown/plain)
ALTER TABLE document ADD COLUMN content_type TEXT;    -- text/html, text/markdown, application/json
ALTER TABLE document ADD COLUMN lang         TEXT;    -- best-effort language code
ALTER TABLE document ADD COLUMN updated_at   TEXT;    -- source-side last-modified, when known
