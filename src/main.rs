use anyhow::{anyhow, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Cursor, Read, Seek, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};
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
        // Reserve one ZIP member for manifest.json. Even if Git contains
        // more than 49,999 distinct blobs, the emitted RGP must remain
        // readable under the fixed RGP/1.0 50,000-member ceiling.
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

// Choose a safe storage method by inspecting the actual ZIP metadata, not
// by guessing compressibility from samples. A valid, repetitive source file
// must not be rejected as a ZIP bomb by our own independent readers.
// Ordinary archives retain their original compression and byte format.
fn build_zip<W: Write + Seek>(
    writer: W,
    manifest_data: &[u8],
    blobs: &BTreeMap<String, Vec<u8>>,
    stored: &HashSet<String>,
) -> Result<W> {
    let mut zip = ZipWriter::new(writer);
    let options = FileOptions::default()
        .compression_method(CompressionMethod::Deflated)
        .last_modified_time(DateTime::default())
        .unix_permissions(0o644);
    let manifest_method = if stored.contains("manifest.json") {
        CompressionMethod::Stored
    } else {
        CompressionMethod::Deflated
    };
    zip.start_file("manifest.json", options.compression_method(manifest_method))?;
    zip.write_all(manifest_data)?;
    for (name, contents) in blobs {
        let method = if stored.contains(name) {
            CompressionMethod::Stored
        } else {
            CompressionMethod::Deflated
        };
        zip.start_file(name, options.compression_method(method))?;
        zip.write_all(contents)?;
    }
    Ok(zip.finish()?)
}

fn excessive_compression<R: Read + Seek>(reader: R) -> Result<HashSet<String>> {
    let mut archive = ZipArchive::new(reader)?;
    let mut out = HashSet::new();
    for index in 0..archive.len() {
        let member = archive.by_index(index)?;
        if member.size() > std::cmp::max(4096, member.compressed_size().saturating_mul(MAX_RATIO)) {
            out.insert(member.name().to_owned());
        }
    }
    Ok(out)
}

// SHA is calculated over the finished ZIP bytes; the transport is emitted
// directly to a unique staging file. Exactly 57 source bytes produce
// one 76-character Base64 line. Neither the ZIP nor its Base64 text needs
// to be retained in memory, and the original RGP/1.0 bytes are unchanged.
fn stream_transport(source: &Path, destination: &mut fs::File) -> Result<usize> {
    let size = fs::metadata(source)?.len();
    ensure!(
        size <= MAX_ARTIFACT as u64,
        "RESOURCE_LIMIT: ZIP payload too large"
    );
    let mut zip = BufReader::new(fs::File::open(source)?);
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let n = zip.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n]);
    }
    let mut output = BufWriter::with_capacity(64 * 1024, &mut *destination);
    writeln!(
        output,
        "RGP/1.0\npayload_length={}\npayload_sha256={}\nbase64_line_width=76\n--",
        size,
        hex::encode(hasher.finalize())
    )?;
    let mut zip = BufReader::new(fs::File::open(source)?);
    let mut raw = [0u8; 57];
    let mut encoded = [0u8; 76];
    loop {
        let mut n = 0;
        while n < raw.len() {
            let read = zip.read(&mut raw[n..])?;
            if read == 0 {
                break;
            }
            n += read;
        }
        if n == 0 {
            break;
        }
        let encoded_size = STANDARD
            .encode_slice(&raw[..n], &mut encoded)
            .map_err(|_| anyhow!("Base64 output buffer insufficient"))?;
        output.write_all(&encoded[..encoded_size])?;
        output.write_all(b"\n")?;
    }
    output.write_all(b"END RGP\n")?;
    output.flush()?;
    drop(output);
    Ok(destination.metadata()?.len() as usize)
}

fn generate(repo: &Path, output: &Path, rev: &str) -> Result<()> {
    let (manifest, blobs) = snapshot(repo, rev)?;
    let manifest_data = serde_json::to_vec(&manifest)?;
    ensure!(
        manifest_data.len() <= MAX_MANIFEST,
        "RESOURCE_LIMIT: manifest exceeds 16 MiB"
    );

    // File-backed ZIP staging means a large ZIP does not share process
    // memory with the source blob map. Both stages are create_new() and
    // cleaned by Drop even when compression or verification fails.
    let (first_zip, file) = StagedOutput::create(output)?;
    drop(build_zip(file, &manifest_data, &blobs, &HashSet::new())?);
    let outliers = excessive_compression(fs::File::open(&first_zip.path)?)?;
    let archive = if outliers.is_empty() {
        first_zip
    } else {
        let (fallback, file) = StagedOutput::create(output)?;
        drop(build_zip(file, &manifest_data, &blobs, &outliers)?);
        ensure!(
            excessive_compression(fs::File::open(&fallback.path)?)?.is_empty(),
            "RESOURCE_LIMIT: unsafe compression ratio after fallback"
        );
        fallback
    };
    drop(blobs);
    drop(manifest_data);
    drop(manifest);

    // Validate the complete artifact before the final atomic rename.
    let (staged, mut file) = StagedOutput::create(output)?;
    let artifact_size = stream_transport(&archive.path, &mut file)?;
    file.sync_all()?;
    drop(file);
    verify(&staged.path)?;
    fs::rename(&staged.path, output)?;
    println!("generated {} ({} bytes)", output.display(), artifact_size);
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
    fn streamed_transport_matches_original_in_memory_bytes_at_line_boundaries() -> Result<()> {
        for length in [1usize, 56, 57, 58, 114, 115, 4097, 65539] {
            let bytes: Vec<u8> = (0..length).map(|n| ((n * 131) % 251) as u8).collect();
            let (source, mut source_file) = StagedOutput::create(Path::new("stream-test.rgp"))?;
            source_file.write_all(&bytes)?;
            source_file.sync_all()?;
            drop(source_file);
            let (destination, mut destination_file) =
                StagedOutput::create(Path::new("stream-test.rgp"))?;
            let written = stream_transport(&source.path, &mut destination_file)?;
            drop(destination_file);
            let actual = fs::read(&destination.path)?;
            let encoded = STANDARD.encode(&bytes);
            let mut expected = format!(
                "RGP/1.0\npayload_length={}\npayload_sha256={}\nbase64_line_width=76\n--\n",
                length,
                sha(&bytes)
            );
            for line in encoded.as_bytes().chunks(76) {
                expected.push_str(std::str::from_utf8(line)?);
                expected.push('\n');
            }
            expected.push_str("END RGP\n");
            assert_eq!(actual, expected.as_bytes());
            assert_eq!(written, actual.len());
        }
        Ok(())
    }

    #[test]
    fn high_compression_blob_uses_stored_without_disabling_reader_limits() {
        let mut blobs = BTreeMap::new();
        let repeated = vec![0u8; 1024 * 1024];
        let key = format!("files/{}", sha(&repeated));
        blobs.insert(key.clone(), repeated);
        let first = build_zip(Cursor::new(Vec::new()), b"{}", &blobs, &HashSet::new())
            .unwrap()
            .into_inner();
        let outliers = excessive_compression(Cursor::new(&first)).unwrap();
        assert!(outliers.contains(&key));
        let rebuilt = build_zip(Cursor::new(Vec::new()), b"{}", &blobs, &outliers)
            .unwrap()
            .into_inner();
        assert!(excessive_compression(Cursor::new(&rebuilt))
            .unwrap()
            .is_empty());
        let mut zip = ZipArchive::new(Cursor::new(rebuilt)).unwrap();
        assert_eq!(
            zip.by_name(&key).unwrap().compression(),
            CompressionMethod::Stored
        );
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

    #[test]
    fn transport_corruption_detected() {
        let tmp =
            std::env::temp_dir().join(format!("rgp-corrupt-{}-{}.rgp", std::process::id(), "test"));
        fs::write(&tmp, "RGP/1.0\npayload_length=4\npayload_sha256=0000000000000000000000000000000000000000000000000000000000000000\nbase64_line_width=76\n--\nAAAA\nEND RGP\n").unwrap();
        assert!(parse_transport(&tmp).is_err());
        fs::remove_file(tmp).unwrap();
    }
}
