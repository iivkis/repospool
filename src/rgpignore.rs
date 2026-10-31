//! Match a committed .rgpignore using Git's own gitignore rules.
//! The pinned Git tree is the only configuration source.
use super::*;
use std::io::ErrorKind;

const MAX_IGNORE_BYTES: usize = 64 * 1024;

struct ScratchRepo(PathBuf);

impl ScratchRepo {
    fn create() -> Result<Self> {
        let stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        for attempt in 0..32 {
            let path = std::env::temp_dir().join(format!(
                "rgpignore-{}-{stamp}-{attempt}",
                std::process::id()
            ));
            match fs::create_dir(&path) {
                Ok(()) => return Ok(Self(path)),
                Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.into()),
            }
        }
        Err(anyhow!("RESOURCE_LIMIT: no unique ignore scratch directory"))
    }
}

impl Drop for ScratchRepo {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub(super) fn ignored_paths(root: &Path, commit: &str, listing: &[u8]) -> Result<HashSet<String>> {
    let mut found = false;
    let mut paths = Vec::new();
    let mut permitted = HashSet::new();

    for record in listing.split(|b| *b == 0).filter(|r| !r.is_empty()) {
        let (meta, raw_path) = record
            .split_once_byte(b'\t')
            .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: Git tree entry"))?;
        let path = std::str::from_utf8(raw_path).context("UNSAFE_PATH: non-UTF8 Git path")?;
        if path == ".rgpignore" {
            let metadata = std::str::from_utf8(meta)?;
            ensure!(
                metadata.starts_with("100644 blob ") || metadata.starts_with("100755 blob "),
                "UNSUPPORTED_FORMAT: .rgpignore must be a regular tracked text file"
            );
            found = true;
            continue; // Configuration always describes itself in the snapshot.
        }
        validate_path(path)?;
        paths.extend_from_slice(raw_path);
        paths.push(0);
        permitted.insert(path.to_owned());
    }
    if !found {
        return Ok(HashSet::new());
    }

    // Ignore a dirty local .rgpignore; only the pinned Git version matters.
    let rules = git(root, &["show", &format!("{commit}:.rgpignore")])?;
    ensure!(
        rules.len() <= MAX_IGNORE_BYTES
            && !rules.contains(&0)
            && std::str::from_utf8(&rules).is_ok(),
        "RESOURCE_LIMIT: .rgpignore must be UTF-8 text <= 64 KiB without NUL"
    );
    // Native Git implements gitignore ! negations, **, anchoring and
    // directory patterns. A scratch repo prevents local/global .gitignore
    // files or checkout changes from affecting the RGP snapshot.
    let scratch = ScratchRepo::create()?;
    git(&scratch.0, &["init", "-q"])?;
    fs::write(scratch.0.join(".gitignore"), rules)?;
    let mut child = Command::new("git")
        .arg("-C")
        .arg(&scratch.0)
        .args([
            "-c",
            "core.excludesFile=/dev/null",
            "check-ignore",
            "--no-index",
            "--stdin",
            "-z",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("git check-ignore spawn failed")?;
    let mut input = child.stdin.take().ok_or_else(|| anyhow!("missing ignore stdin"))?;
    // Concurrent I/O avoids a pipe deadlock on large source trees.
    let writer = std::thread::spawn(move || input.write_all(&paths));
    let result = child.wait_with_output()?;
    writer
        .join()
        .map_err(|_| anyhow!("git check-ignore stdin thread panicked"))??;
    ensure!(
        matches!(result.status.code(), Some(0) | Some(1)),
        "git check-ignore failed: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    let mut ignored = HashSet::new();
    for raw in result.stdout.split(|b| *b == 0).filter(|p| !p.is_empty()) {
        let path = std::str::from_utf8(raw).context("UNSAFE_PATH: invalid ignore match")?;
        ensure!(permitted.contains(path), "UNSUPPORTED_FORMAT: unexpected ignore result");
        ignored.insert(path.to_owned());
    }
    Ok(ignored)
}
