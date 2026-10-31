use anyhow::{anyhow, ensure, Context, Result};
use clap::{Parser, Subcommand};

mod lean;
mod rgpignore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_FILE: usize = 16 * 1024 * 1024;
const MAX_MANIFEST: usize = 16 * 1024 * 1024;
const MAX_TOTAL: usize = 128 * 1024 * 1024;
const MAX_ENTRIES: usize = 50_000;

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

// Keep one bounded git cat-file process for a snapshot, rather than spawning
// a subprocess per file. Read each response before sending the next request
// so pipe buffers cannot deadlock on large repositories.
struct GitBlobBatch {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: BufReader<ChildStdout>,
}

impl GitBlobBatch {
    fn start(repo: &Path) -> Result<Self> {
        let mut child = Command::new("git")
            .arg("-C")
            .arg(repo)
            .args(["cat-file", "--batch"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .context("failed to start git cat-file --batch")?;
        let stdin = child.stdin.take().context("missing git stdin")?;
        let stdout = BufReader::new(child.stdout.take().context("missing git stdout")?);
        Ok(Self {
            child: Some(child),
            stdin: Some(stdin),
            stdout,
        })
    }

    fn read(&mut self, object: &str, expected_size: usize) -> Result<Vec<u8>> {
        let stdin = self.stdin.as_mut().context("closed git batch stdin")?;
        writeln!(stdin, "{object}")?;
        stdin.flush()?;

        let mut header = String::new();
        ensure!(
            self.stdout.read_line(&mut header)? > 0,
            "Git cat-file ended unexpectedly"
        );
        let mut parts = header.split_ascii_whitespace();
        ensure!(
            parts.next() == Some(object)
                && parts.next() == Some("blob")
                && parts.next().and_then(|s| s.parse::<usize>().ok()) == Some(expected_size)
                && parts.next().is_none(),
            "Git cat-file object header mismatch: {header:?}"
        );
        let mut bytes = vec![0; expected_size];
        self.stdout.read_exact(&mut bytes)?;
        let mut separator = [0u8; 1];
        self.stdout.read_exact(&mut separator)?;
        ensure!(separator == [b'\n'], "Git cat-file response terminator");
        Ok(bytes)
    }

    fn finish(mut self) -> Result<()> {
        drop(self.stdin.take());
        let mut child = self.child.take().context("missing git batch child")?;
        ensure!(child.wait()?.success(), "git cat-file batch failed");
        Ok(())
    }
}

impl Drop for GitBlobBatch {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            drop(self.stdin.take());
            let _ = child.kill();
            let _ = child.wait();
        }
    }
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
    let ignored = rgpignore::ignored_paths(root, &commit, &listing)?;
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
    let mut git_blobs = GitBlobBatch::start(root)?;
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
        if path == "REPO_CONTEXT.rgp" || path == "REPO_CONTEXT.rgpmi" {
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
        if ignored.contains(path) {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "rgpignore".into(),
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
        // Reserve one index entry for the source list. Even if Git contains
        // more than 49,999 distinct blobs, the emitted RGP must remain
        // bounded by the format's 50,000-file ceiling.
        // Exclusions are recorded explicitly, never silently dropped.
        if manifest.files.len() >= MAX_ENTRIES - 1 {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "entry_limit".into(),
            });
            continue;
        }
        if object_size > MAX_FILE || total.saturating_add(object_size) > MAX_TOTAL {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "size_limit".into(),
            });
            continue;
        }
        let bytes = git_blobs.read(object, object_size)?;
        // UTF-8 is intended to be read directly by an AI agent.
        // Do not smuggle binary bytes, invalid UTF-8 or NUL into a text file.
        // Record each skipped source explicitly as incomplete coverage.
        if std::str::from_utf8(&bytes).is_err() || bytes.contains(&0) {
            manifest.excluded.push(Exclusion {
                path: path.into(),
                reason: "non_text".into(),
            });
            continue;
        }
        total += bytes.len();
        let digest = sha(&bytes);
        let blob = format!("files/{digest}");
        let status = if bytes.starts_with(b"version https://git-lfs.github.com/spec/v1\n") {
            "pointer_only"
        } else {
            "included"
        };
        let size = bytes.len() as u64;
        // Moving, rather than cloning, the first occurrence of a Git blob
        // avoids an unnecessary 16 MiB allocation for large source files.
        blobs.entry(blob.clone()).or_insert(bytes);
        manifest.files.push(FileEntry {
            path: path.into(),
            mode: mode.into(),
            kind: if mode == "120000" { "symlink" } else { "file" }.into(),
            size,
            sha256: digest,
            blob,
            status: status.into(),
        });
    }
    git_blobs.finish()?;
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

// A unique, create_new() staging file prevents parallel writers from
// sharing a fixed ".rgp.tmp" name. Drop removes it after any failure.
struct StagedOutput {
    path: PathBuf,
}

impl StagedOutput {
    fn create(output: &Path) -> Result<(Self, fs::File)> {
        let name = output
            .file_name()
            .context("output must be a file path")?
            .to_string_lossy();
        let stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        for attempt in 0..100 {
            let path = output.with_file_name(format!(
                ".{name}.{}.{}.{}.tmp",
                std::process::id(),
                stamp,
                attempt
            ));
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(file) => return Ok((Self { path }, file)),
                Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(err) => return Err(err.into()),
            }
        }
        Err(anyhow!("unable to allocate unique temporary output"))
    }
}

impl Drop for StagedOutput {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

fn generate(repo: &Path, output: &Path, rev: &str) -> Result<()> {
    let (manifest, blobs) = snapshot(repo, rev)?;
    // A single versioned UTF-8 transport: an auto-generated generic guide,
    // compact index, and byte-framed original source text.
    let (staged, mut file) = StagedOutput::create(output)?;
    let artifact_size = lean::write_lean(&mut file, &manifest, &blobs)?;
    drop(blobs);
    drop(manifest);
    file.sync_all()?;
    drop(file);
    verify(&staged.path)?;

    fs::rename(&staged.path, output)?;
    println!("generated {} ({} bytes)", output.display(), artifact_size);
    Ok(())
}

fn verify(path: &Path) -> Result<Manifest> {
    lean::verify_lean(path)
}

fn verify_manifest(manifest: Manifest, blobs: BTreeMap<String, u64>) -> Result<Manifest> {
    ensure!(
        manifest.schema_version == "1.0",
        "UNSUPPORTED_FORMAT: manifest version"
    );
    ensure!(
        manifest.files.len() < MAX_ENTRIES && manifest.excluded.len() <= MAX_ENTRIES,
        "RESOURCE_LIMIT: manifest entry/exclusion count"
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
    let mut previous_exclusion = None::<&str>;
    for e in &manifest.excluded {
        validate_path(&e.path)?;
        ensure!(
            matches!(
                e.reason.as_str(),
                "self_artifact"
                    | "sensitive_path"
                    | "submodule_unavailable"
                    | "size_limit"
                    | "entry_limit"
                    | "non_text"
                    | "rgpignore"
            ),
            "UNSUPPORTED_FORMAT: exclusion reason"
        );
        if let Some(previous) = previous_exclusion {
            ensure!(
                previous < e.path.as_str(),
                "UNSUPPORTED_FORMAT: exclusion ordering"
            );
        }
        previous_exclusion = Some(&e.path);
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
    fn generated_guide_is_generic_and_versioned() {
        let guide = lean::generated_guide();
        assert!(guide.contains("RGP/0.1.0"));
        assert!(guide.contains("F ="));
        assert!(guide.contains("independent"));
        assert!(!guide.contains("github.com/iivkis"));
        assert!(!guide.contains("REPO_CONTEXT.rgp"));
    }

    #[test]
    fn staging_files_are_unique_and_cleaned_on_drop() {
        let output = std::env::temp_dir().join(format!(
            "rgp-staging-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let (a, a_file) = StagedOutput::create(&output).unwrap();
        let (b, b_file) = StagedOutput::create(&output).unwrap();
        assert_ne!(a.path, b.path);
        assert!(a.path.exists() && b.path.exists());
        drop(a_file);
        drop(b_file);
        let a_path = a.path.clone();
        let b_path = b.path.clone();
        drop(a);
        drop(b);
        assert!(!a_path.exists() && !b_path.exists());
    }

}
