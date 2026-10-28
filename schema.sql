-- RGP SQLite index schema v0.1 (design contract, NOT shipped by MVP-A).
-- Compatible with SQLite 3.35+: no JSON1, FTS5, generated columns or STRICT tables required.
-- All byte-for-byte originals remain in files/<sha256> inside RGP, never in this DB.
-- Consumers MUST verify the outer RGP and ZIP before extracting this database, and
-- MUST open an extracted copy read-only with PRAGMA query_only=ON.
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    format_version TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    input_tree_digest TEXT NOT NULL CHECK (length(input_tree_digest) = 64)
);

CREATE TABLE files (
    path TEXT PRIMARY KEY COLLATE BINARY,
    mode TEXT NOT NULL CHECK (mode IN ('100644', '100755', '120000')),
    kind TEXT NOT NULL CHECK (kind IN ('file', 'symlink')),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    blob_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('included', 'pointer_only')),
    CHECK ((mode = '120000' AND kind = 'symlink') OR (mode != '120000' AND kind = 'file'))
);

-- Analyzer runs are configuration-scoped; different features/toolchains must not
-- silently share semantic claims. The config is serialized JSON, not executable SQL.
CREATE TABLE analysis_runs (
    run_id TEXT PRIMARY KEY,
    producer TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('parsed', 'partial', 'failed', 'unsupported')),
    input_tree_digest TEXT NOT NULL CHECK (length(input_tree_digest) = 64)
);

CREATE TABLE symbols (
    symbol_id TEXT PRIMARY KEY,
    path TEXT NOT NULL COLLATE BINARY REFERENCES files(path),
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    start_column INTEGER NOT NULL CHECK (start_column >= 0),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    end_column INTEGER NOT NULL CHECK (end_column >= 0),
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    -- Syntactic occurrence does not prove a resolved semantic reference.
    confidence_kind TEXT NOT NULL CHECK (confidence_kind IN
        ('confirmed_reference', 'possible_dependency', 'heuristic_relation', 'unknown'))
);
CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_symbols_path ON symbols(path, start_line);

-- A relationship is a claim supported by a specific source range, not a proof of
-- runtime behavior. Exactly one of resolved target or unresolved text is recorded.
CREATE TABLE relations (
    relation_id TEXT PRIMARY KEY,
    relation_type TEXT NOT NULL CHECK (relation_type IN
        ('imports', 'references', 'calls', 'implements', 'contains', 'tests', 'configures')),
    source_path TEXT NOT NULL COLLATE BINARY REFERENCES files(path),
    source_symbol_id TEXT REFERENCES symbols(symbol_id),
    target_symbol_id TEXT REFERENCES symbols(symbol_id),
    unresolved_target TEXT,
    evidence_path TEXT NOT NULL COLLATE BINARY REFERENCES files(path),
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    start_column INTEGER NOT NULL CHECK (start_column >= 0),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    end_column INTEGER NOT NULL CHECK (end_column >= 0),
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    evidence TEXT NOT NULL,
    confidence_kind TEXT NOT NULL CHECK (confidence_kind IN
        ('confirmed_reference', 'possible_dependency', 'heuristic_relation', 'unknown')),
    CHECK ((target_symbol_id IS NULL) != (unresolved_target IS NULL)),
    CHECK (confidence_kind != 'confirmed_reference' OR target_symbol_id IS NOT NULL)
);
CREATE INDEX idx_relations_source ON relations(source_symbol_id, relation_type);
CREATE INDEX idx_relations_target ON relations(target_symbol_id, relation_type);
CREATE INDEX idx_relations_evidence ON relations(evidence_path, start_line);

-- Snapshot, index, graph and investigation are not interchangeable metrics.
-- Investigation records Reader disclosures, never unobserved agent comprehension.
CREATE TABLE coverage (
    layer TEXT NOT NULL CHECK (layer IN ('snapshot', 'index', 'graph', 'investigation')),
    scope TEXT NOT NULL,
    run_id TEXT REFERENCES analysis_runs(run_id),
    status TEXT NOT NULL CHECK (status IN
        ('available', 'not_indexed', 'partial', 'failed', 'unsupported', 'unknown')),
    included_count INTEGER CHECK (included_count IS NULL OR included_count >= 0),
    total_count INTEGER CHECK (total_count IS NULL OR total_count >= 0),
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (layer, scope)
);
