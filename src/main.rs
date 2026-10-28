use anyhow::{anyhow, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use zip::{write::FileOptions, CompressionMethod, DateTime, ZipArchive, ZipWriter};

const MAX_ARTIFACT: usize = 64 * 1024 * 1024;
const MAX_FILE: usize = 16 * 1024 * 1024;
const MAX_MANIFEST: usize = 16 * 1024 * 1024;
const MAX_TOTAL: usize = 128 * 1024 * 1024;
const MAX_ENTRIES: usize = 50_000;
const MAX_RATIO: u64 = 200;

#[derive(Parser)]
#[command(about = "Create and verify deterministic Repository Graph Packs")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    Generate {
        #[arg(long, default_value = ".")]
        repo: PathBuf,
        #[arg(long, default_value = "REPO_CONTEXT.rgp")]
        output: PathBuf,
        #[arg(long, default_value = "HEAD")]
        rev: String,
    },
    Verify {
        #[arg(long)]
        artifact: PathBuf,
        #[arg(long)]
        repo: Option<PathBuf>,
    },
    Inspect {
        #[arg(long)]
        artifact: PathBuf,
    },
}

#[derive(Debug, Serialize, Deserialize)]
struct Manifest {
    schema_version: String,
    source_commit: String,
    input_tree_digest: String,
    files: Vec<FileEntry>,
    excluded: Vec<Exclusion>,
    index_coverage: String,
    graph_coverage: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct FileEntry {
    path: String,
    mode: String,
    kind: String,
    size: u64,
    sha256: String,
    blob: String,
    status: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct Exclusion {
    path: String,
    reason: String,
}

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn validate_path(path: &str) -> Result<()> {
    ensure!(
        !path.is_empty() && path.len() <= 4096 && !path.starts_with('/'),
        "UNSAFE_PATH: absolute or empty path"
    );
    ensure!(
        !path.contains('\\') && !path.chars().any(char::is_control),
        "UNSAFE_PATH: control character or backslash"
    );
    ensure!(
        path.split('/')
            .all(|part| !part.is_empty() && part != "." && part != ".."),
        "UNSAFE_PATH: invalid path component"
    );
    Ok(())
}

fn is_sensitive(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or("").to_ascii_lowercase();
    name == ".env"
        || name.starts_with(".env.")
        || name.ends_with(".pem")
        || name.ends_with(".key")
        || name.starts_with("id_rsa")
        || name.starts_with("credentials")
        || name.starts_with("secrets")
}

fn git(repo: &Path, args: &[&str]) -> Result<Vec<u8>> {
    let result = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .with_context(|| format!("git invocation failed for {}", repo.display()))?;
    ensure!(
        result.status.success(),
        "git {} failed: {}",
        args.join(" "),
        String::from_utf8_lossy(&result.stderr)
    );
    Ok(result.stdout)
}

fn git_text(repo: &Path, args: &[&str]) -> Result<String> {
    Ok(String::from_utf8(git(repo, args)?)?.trim().to_owned())
}

fn snapshot(repo: &Path, rev: &str) -> Result<(Manifest, BTreeMap<String, Vec<u8>>)> {
    ensure!(!rev.starts_with('-'), "invalid revision");
    let root = git_text(repo, &["rev-parse", "--show-toplevel"])?;
    let root = Path::new(&root);
    let commit = git_text(
        root,
        &["rev-parse", "--verify", &format!("{rev}^{{commit}}")],
    )?;
    let listing = git(root, &["ls-tree", "--full-tree", "-r", "-l", "-z", &commit])?;
    let mut manifest = Manifest {
        schema_version: "1.0".into(),
        source_commit: commit,
        input_tree_digest: String::new(),
        files: Vec::new(),
        excluded: Vec::new(),
        index_coverage: "not_indexed".into(),
        graph_coverage: "not_indexed".into(),
    };
    let mut blobs = BTreeMap::new();
    let mut total = 0usize;
    let mut seen_paths = HashSet::new();
    for record in listing.split(|b| *b == 0).filter(|r| !r.is_empty()) {
        let (meta, raw_path) = record
            .split_once_byte(b'\t')
            .ok_or_else(|| anyhow!("invalid git ls-tree output"))?;
        let path = std::str::from_utf8(raw_path).context("UNSAFE_PATH: non-UTF8 Git path")?;
        validate_path(path)?;
        ensure!(
            seen_paths.insert(path.to_lowercase()),
            "UNSAFE_PATH: case-colliding paths"
        );
        if path == "REPO_CONTEXT.rgp" {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "self_artifact".into(),
            });
            continue;
        }
        if is_sensitive(path) {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "sensitive_path".into(),
            });
            continue;
        }
        let meta = std::str::from_utf8(meta)?;
        let mut parts = meta.split_ascii_whitespace();
        let mode = parts.next().ok_or_else(|| anyhow!("missing mode"))?;
        let kind = parts.next().ok_or_else(|| anyhow!("missing kind"))?;
        let object = parts.next().ok_or_else(|| anyhow!("missing object"))?;
        let size = parts.next().unwrap_or("-");
        if mode == "160000" || kind == "commit" {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "submodule_unavailable".into(),
            });
            continue;
        }
        ensure!(kind == "blob", "unsupported Git object: {kind}");
        let object_size: usize = size.parse().context("invalid blob size")?;
        if object_size > MAX_FILE || total.saturating_add(object_size) > MAX_TOTAL {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "size_limit".into(),
            });
            continue;
        }
        let bytes = git(root, &["cat-file", "blob", object])?;
        ensure!(
            bytes.len() == object_size,
            "Git object changed unexpectedly"
        );
        total += bytes.len();
        let digest = sha(&bytes);
        let blob = format!("files/{digest}");
        blobs.entry(blob.clone()).or_insert(bytes.clone());
        let status = if bytes.starts_with(b"version https://git-lfs.github.com/spec/v1\n") {
            "pointer_only"
        } else {
            "included"
        };
        manifest.files.push(FileEntry {
            path: path.into(),
            mode: mode.into(),
            kind: if mode == "120000" { "symlink" } else { "file" }.into(),
            size: bytes.len() as u64,
            sha256: digest,
            blob,
            status: status.into(),
        });
    }
    manifest.files.sort_by(|a, b| a.path.cmp(&b.path));
    manifest.excluded.sort_by(|a, b| a.path.cmp(&b.path));
    manifest.input_tree_digest = tree_digest(&manifest.files)?;
    Ok((manifest, blobs))
}

trait SplitOnceByte {
    fn split_once_byte(&self, byte: u8) -> Option<(&[u8], &[u8])>;
}
impl SplitOnceByte for [u8] {
    fn split_once_byte(&self, byte: u8) -> Option<(&[u8], &[u8])> {
        self.iter()
            .position(|b| *b == byte)
            .map(|i| (&self[..i], &self[i + 1..]))
    }
}

fn tree_digest(files: &[FileEntry]) -> Result<String> {
    let mut digest = Sha256::new();
    for file in files {
        digest.update(file.path.as_bytes());
        digest.update([0]);
        digest.update(file.mode.as_bytes());
        digest.update([0]);
        digest.update(hex::decode(&file.sha256)?);
    }
    Ok(hex::encode(digest.finalize()))
}

fn generate(repo: &Path, output: &Path, rev: &str) -> Result<()> {
    let (manifest, blobs) = snapshot(repo, rev)?;
    let mut zip = ZipWriter::new(Cursor::new(Vec::new()));
    let options = FileOptions::default()
        .compression_method(CompressionMethod::Deflated)
        .last_modified_time(DateTime::default())
        .unix_permissions(0o644);
    zip.start_file("manifest.json", options)?;
    zip.write_all(&serde_json::to_vec(&manifest)?)?;
    for (name, contents) in blobs {
        zip.start_file(name, options)?;
        zip.write_all(&contents)?;
    }
    let compressed = zip.finish()?.into_inner();
    ensure!(
        compressed.len() <= MAX_ARTIFACT,
        "RESOURCE_LIMIT: ZIP payload too large"
    );
    let encoded = STANDARD.encode(&compressed);
    let mut artifact = format!(
        "RGP/1.0\npayload_length={}\npayload_sha256={}\nbase64_line_width=76\n--\n",
        compressed.len(),
        sha(&compressed)
    );
    for line in encoded.as_bytes().chunks(76) {
        artifact.push_str(std::str::from_utf8(line)?);
        artifact.push('\n');
    }
    artifact.push_str("END RGP\n");
    // Verify fully before publishing; write a temporary file alongside the destination.
    let tmp = output.with_extension("rgp.tmp");
    fs::write(&tmp, artifact.as_bytes())?;
    let result = verify(&tmp);
    if let Err(err) = result {
        let _ = fs::remove_file(&tmp);
        return Err(err);
    }
    fs::rename(&tmp, output)?;
    println!("generated {} ({} bytes)", output.display(), artifact.len());
    Ok(())
}

fn parse_transport(path: &Path) -> Result<Vec<u8>> {
    let data = fs::read(path)?;
    ensure!(
        data.len() <= MAX_ARTIFACT * 2,
        "RESOURCE_LIMIT: outer text too large"
    );
    let data = std::str::from_utf8(&data).context("UNSUPPORTED_FORMAT: non-ASCII text")?;
    let (header, rest) = data
        .split_once("\n--\n")
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: missing separator"))?;
    let fields: Vec<_> = header.split('\n').collect();
    ensure!(
        fields.len() == 4 && fields[0] == "RGP/1.0",
        "UNSUPPORTED_FORMAT: header"
    );
    let len: usize = fields[1]
        .strip_prefix("payload_length=")
        .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: length"))?
        .parse()?;
    let hash = fields[2]
        .strip_prefix("payload_sha256=")
        .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: SHA"))?;
    ensure!(
        fields[3] == "base64_line_width=76",
        "UNSUPPORTED_FORMAT: line width"
    );
    ensure!(
        hash.len() == 64
            && hash
                .bytes()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()),
        "UNSUPPORTED_FORMAT: SHA hex"
    );
    ensure!(len <= MAX_ARTIFACT, "RESOURCE_LIMIT: payload too large");
    let body = rest
        .strip_suffix("END RGP\n")
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: missing footer"))?;
    ensure!(
        body.ends_with('\n'),
        "TRUNCATED_ARTIFACT: Base64 final line"
    );
    let lines: Vec<&str> = body.trim_end_matches('\n').split('\n').collect();
    ensure!(
        !lines.is_empty() && !lines[0].is_empty(),
        "TRUNCATED_ARTIFACT: empty body"
    );
    let mut joined = String::new();
    for (i, line) in lines.iter().enumerate() {
        ensure!(
            !line.is_empty() && line.len() <= 76,
            "TRUNCATED_ARTIFACT: invalid Base64 line"
        );
        if i + 1 != lines.len() {
            ensure!(line.len() == 76, "TRUNCATED_ARTIFACT: short Base64 line");
        }
        joined.push_str(line);
    }
    let payload = STANDARD
        .decode(joined)
        .context("TRUNCATED_ARTIFACT: Base64 decode failed")?;
    ensure!(payload.len() == len, "TRUNCATED_ARTIFACT: length mismatch");
    ensure!(sha(&payload) == hash, "HASH_MISMATCH: payload SHA-256");
    Ok(payload)
}

fn verify(path: &Path) -> Result<Manifest> {
    let payload = parse_transport(path)?;
    let mut archive = ZipArchive::new(Cursor::new(payload))?;
    ensure!(
        !archive.is_empty() && archive.len() <= MAX_ENTRIES,
        "RESOURCE_LIMIT: ZIP entry count"
    );
    let mut names = HashSet::new();
    let mut blobs = BTreeMap::new();
    let mut manifest_bytes = None;
    let mut total = 0usize;
    for i in 0..archive.len() {
        let mut entry = archive.by_index(i)?;
        let name = entry.name().to_owned();
        validate_path(&name)?;
        ensure!(
            names.insert(name.to_lowercase()),
            "UNSAFE_PATH: duplicate ZIP entry"
        );
        ensure!(
            name == "manifest.json" || name.starts_with("files/"),
            "UNSAFE_PATH: unexpected ZIP entry"
        );
        let member_limit = if name == "manifest.json" {
            MAX_MANIFEST
        } else {
            MAX_FILE
        };
        ensure!(
            entry.size() <= member_limit as u64,
            "RESOURCE_LIMIT: single ZIP entry"
        );
        ensure!(
            entry.size() <= MAX_TOTAL as u64,
            "RESOURCE_LIMIT: ZIP entry size"
        );
        ensure!(
            entry.size() <= entry.compressed_size().saturating_mul(MAX_RATIO).max(4096),
            "RESOURCE_LIMIT: compression ratio"
        );
        total = total
            .checked_add(entry.size() as usize)
            .ok_or_else(|| anyhow!("RESOURCE_LIMIT"))?;
        ensure!(total <= MAX_TOTAL, "RESOURCE_LIMIT: decompressed size");
        let mut buf = Vec::new();
        entry.read_to_end(&mut buf)?;
        ensure!(
            buf.len() as u64 == entry.size(),
            "HASH_MISMATCH: decompressed size"
        );
        if name == "manifest.json" {
            ensure!(
                manifest_bytes.is_none() && i == 0,
                "UNSUPPORTED_FORMAT: manifest order"
            );
            manifest_bytes = Some(buf);
        } else {
            ensure!(
                name == format!("files/{}", sha(&buf)),
                "HASH_MISMATCH: ZIP blob"
            );
            blobs.insert(name, buf.len() as u64);
        }
    }
    let manifest: Manifest = serde_json::from_slice(
        &manifest_bytes.ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: missing manifest"))?,
    )?;
    ensure!(
        manifest.schema_version == "1.0",
        "UNSUPPORTED_FORMAT: manifest version"
    );
    ensure!(
        manifest.files.len() <= MAX_ENTRIES,
        "RESOURCE_LIMIT: manifest entries"
    );
    let mut previous = None::<&str>;
    let mut referenced = HashSet::new();
    let mut file_names = HashSet::new();
    for file in &manifest.files {
        validate_path(&file.path)?;
        ensure!(
            file_names.insert(file.path.to_lowercase()),
            "UNSAFE_PATH: duplicate file path"
        );
        if let Some(p) = previous {
            ensure!(p < file.path.as_str(), "UNSUPPORTED_FORMAT: file ordering");
        }
        previous = Some(&file.path);
        ensure!(
            matches!(file.mode.as_str(), "100644" | "100755" | "120000"),
            "UNSUPPORTED_FORMAT: Git mode"
        );
        ensure!(
            file.kind
                == if file.mode == "120000" {
                    "symlink"
                } else {
                    "file"
                },
            "UNSUPPORTED_FORMAT: Git kind"
        );
        ensure!(
            matches!(file.status.as_str(), "included" | "pointer_only"),
            "UNSUPPORTED_FORMAT: status"
        );
        ensure!(
            file.blob == format!("files/{}", file.sha256),
            "HASH_MISMATCH: manifest blob"
        );
        ensure!(
            blobs.get(&file.blob) == Some(&file.size),
            "HASH_MISMATCH: missing blob or length"
        );
        referenced.insert(file.blob.as_str());
    }
    ensure!(
        blobs.len() == referenced.len(),
        "UNSUPPORTED_FORMAT: unreferenced blobs"
    );
    ensure!(
        tree_digest(&manifest.files)? == manifest.input_tree_digest,
        "HASH_MISMATCH: input tree digest"
    );
    for e in &manifest.excluded {
        validate_path(&e.path)?;
        ensure!(
            file_names.insert(e.path.to_lowercase()),
            "UNSAFE_PATH: file/exclusion collision"
        );
    }
    Ok(manifest)
}

fn main() -> Result<()> {
    match Cli::parse().command {
        Commands::Generate { repo, output, rev } => generate(&repo, &output, &rev),
        Commands::Verify { artifact, repo } => {
            let manifest = verify(&artifact)?;
            if let Some(repo) = repo {
                let (current, _) = snapshot(&repo, "HEAD")?;
                ensure!(
                    current.input_tree_digest == manifest.input_tree_digest,
                    "STALE_SNAPSHOT: input digest changed"
                );
            }
            println!(
                "verified: {} files, {} excluded, digest {}",
                manifest.files.len(),
                manifest.excluded.len(),
                manifest.input_tree_digest
            );
            Ok(())
        }
        Commands::Inspect { artifact } => {
            let manifest = verify(&artifact)?;
            println!("{}", serde_json::to_string_pretty(&manifest)?);
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_paths_rejected() {
        for path in [
            "../etc/passwd",
            "a/../b",
            "/root",
            "a\\b",
            "a//b",
            "a/./b",
            "a\0b",
        ] {
            assert!(validate_path(path).is_err(), "{path:?}");
        }
        assert!(validate_path("code/файл.rs").is_ok());
    }

    #[test]
    fn sensitive_names() {
        assert!(is_sensitive("app/.env.local"));
        assert!(is_sensitive("cert.pem"));
        assert!(!is_sensitive("app/README.md"));
    }

    #[test]
    fn transport_corruption_detected() {
        let tmp =
            std::env::temp_dir().join(format!("rgp-corrupt-{}-{}.rgp", std::process::id(), "test"));
        fs::write(&tmp, "RGP/1.0\npayload_length=4\npayload_sha256=0000000000000000000000000000000000000000000000000000000000000000\nbase64_line_width=76\n--\nAAAA\nEND RGP\n").unwrap();
        assert!(parse_transport(&tmp).is_err());
        fs::remove_file(tmp).unwrap();
    }
}
