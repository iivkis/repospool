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
        Err(anyhow!(
            "RESOURCE_LIMIT: no unique ignore scratch directory"
        ))
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
    let mut input = child
        .stdin
        .take()
        .ok_or_else(|| anyhow!("missing ignore stdin"))?;
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
        ensure!(
            permitted.contains(path),
            "UNSUPPORTED_FORMAT: unexpected ignore result"
        );
        ignored.insert(path.to_owned());
    }
    Ok(ignored)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn add_file(dir: &Path, name: &str, content: &str) -> Result<()> {
        let path = dir.join(name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, content)?;
        Ok(())
    }

    #[test]
    fn pinned_gitignore_semantics_and_scoped_snapshot() -> Result<()> {
        let dir = ScratchRepo::create()?;
        git(&dir.0, &["init", "-q"])?;
        add_file(
            &dir.0,
            ".rgpignore",
            "/benchmarks/\n/docs/*.log\n*.lock\n!important.lock\n**/generated/\n",
        )?;
        for (name, contents) in [
            ("src/main.rs", "fn main() {}\n"),
            ("benchmarks/report.txt", "large evidence\n"),
            ("docs/build.log", "log\n"),
            ("docs/notes.md", "useful\n"),
            ("Cargo.lock", "lock\n"),
            ("important.lock", "keep me\n"),
            ("deep/generated/cache.txt", "generated\n"),
        ] {
            add_file(&dir.0, name, contents)?;
        }
        git(&dir.0, &["add", "-A"])?;
        git(
            &dir.0,
            &[
                "-c",
                "user.name=RGP Test",
                "-c",
                "user.email=rgp@example.invalid",
                "commit",
                "-qm",
                "source",
            ],
        )?;
        let pin = git_text(&dir.0, &["rev-parse", "HEAD"])?;
        let (manifest, _) = snapshot(&dir.0, &pin)?;
        let included: HashSet<_> = manifest.files.iter().map(|f| f.path.as_str()).collect();
        assert!(included.contains(".rgpignore"));
        assert!(included.contains("src/main.rs"));
        assert!(included.contains("docs/notes.md"));
        assert!(included.contains("important.lock")); // Negation works.
        for ignored in [
            "benchmarks/report.txt",
            "docs/build.log",
            "Cargo.lock",
            "deep/generated/cache.txt",
        ] {
            assert!(!included.contains(ignored));
            assert!(manifest
                .excluded
                .iter()
                .any(|e| e.path == ignored && e.reason == "rgpignore"));
        }
        // A dirty working tree must NEVER influence the pinned Git rules.
        add_file(&dir.0, ".rgpignore", "*\n")?;
        let (pinned_again, _) = snapshot(&dir.0, &pin)?;
        assert_eq!(manifest.input_tree_digest, pinned_again.input_tree_digest);
        assert_eq!(manifest.excluded.len(), pinned_again.excluded.len());

        git(&dir.0, &["add", ".rgpignore"])?;
        git(
            &dir.0,
            &[
                "-c",
                "user.name=RGP Test",
                "-c",
                "user.email=rgp@example.invalid",
                "commit",
                "-qm",
                "change ignore scope",
            ],
        )?;
        let (new_manifest, _) = snapshot(&dir.0, "HEAD")?;
        assert_eq!(new_manifest.files.len(), 1); // Forced .rgpignore only.
        assert_eq!(new_manifest.files[0].path, ".rgpignore");
        Ok(())
    }

    #[test]
    fn repositories_without_rgpignore_keep_legacy_inclusion() -> Result<()> {
        let dir = ScratchRepo::create()?;
        git(&dir.0, &["init", "-q"])?;
        add_file(&dir.0, "src/code.py", "print('source')\n")?;
        git(&dir.0, &["add", "-A"])?;
        git(
            &dir.0,
            &[
                "-c",
                "user.name=RGP Test",
                "-c",
                "user.email=rgp@example.invalid",
                "commit",
                "-qm",
                "source",
            ],
        )?;
        let (manifest, _) = snapshot(&dir.0, "HEAD")?;
        assert_eq!(manifest.files.len(), 1);
        assert!(manifest.excluded.is_empty());
        Ok(())
    }
}
