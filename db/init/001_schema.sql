-- Document store for versioned-rag's "db" source.
-- One row per document, one row per version. Content is stored as bytes plus a filename,
-- so a version can be Markdown, plain text, PDF or DOCX (the filename picks the extractor).

CREATE TABLE IF NOT EXISTS documents (
    id          serial PRIMARY KEY,
    slug        text NOT NULL UNIQUE,          -- stable identity, used as doc_id
    title       text NOT NULL
);

CREATE TABLE IF NOT EXISTS document_versions (
    id              serial PRIMARY KEY,
    document_id     integer NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    version_label   text NOT NULL,             -- as the business names it: v1.0, v1.1, 2025-Q1 ...
    effective_from  date NOT NULL,             -- orders versions; fallback if the text has no date
    filename        text NOT NULL,             -- e.g. policy.md
    content         bytea NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_label)
);

CREATE INDEX IF NOT EXISTS document_versions_doc_idx ON document_versions (document_id, effective_from);
