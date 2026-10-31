//! RGP/3.0: token-lean, plain UTF-8. No hash, commit IDs or JSON in transport.
//! Independent Git provenance, not in-band data, authenticates the artifact.
use super::*;

const MAX_LEAN: usize = 192 * 1024 * 1024;

fn take_line<'a>(bytes: &'a [u8], pos: &mut usize) -> Result<&'a [u8]> {
    let rest = bytes
        .get(*pos..)
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: line"))?;
    let end = rest
        .iter()
        .position(|b| *b == b'\n')
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: newline"))?;
    let old = *pos;
    *pos += end + 1;
    Ok(&bytes[old..old + end])
}

fn source_flag(file: &FileEntry) -> &'static str {
    match (file.mode.as_str(), file.status.as_str()) {
        ("100644", "included") => "",
        ("100755", "included") => "x",
        ("120000", "included") => "l",
        ("100644", "pointer_only") => "p",
        ("100755", "pointer_only") => "xp",
        ("120000", "pointer_only") => "lp",
        _ => "",
    }
}

pub(super) fn write_lean(
    output: &mut fs::File,
    manifest: &Manifest,
    blobs: &BTreeMap<String, Vec<u8>>,
) -> Result<usize> {
    let guide = INLINE_AGENT_GUIDE.as_bytes();
    ensure!(
        !guide.is_empty() && guide.len() <= 8192,
        "RESOURCE_LIMIT: guide"
    );
    let mut index = String::new();
    for f in &manifest.files {
        validate_path(&f.path)?;
        let flag = source_flag(f);
        if flag.is_empty() {
            index.push_str(&format!("F\t{}\t{}\n", f.path, f.size));
        } else {
            index.push_str(&format!("F\t{}\t{}\t{}\n", f.path, f.size, flag));
        }
    }
    let ignored = manifest
        .excluded
        .iter()
        .filter(|x| x.reason == "rgpignore")
        .count();
    let self_count = manifest
        .excluded
        .iter()
        .filter(|x| x.reason == "self_artifact")
        .count();
    if ignored != 0 {
        index.push_str(&format!("I\t{ignored}\n"));
    }
    if self_count != 0 {
        index.push_str(&format!("S\t{self_count}\n"));
    }
    for entry in &manifest.excluded {
        if entry.reason != "rgpignore" && entry.reason != "self_artifact" {
            index.push_str(&format!("X\t{}\t{}\n", entry.path, entry.reason));
        }
    }
    ensure!(
        index.len() <= MAX_MANIFEST,
        "RESOURCE_LIMIT: index too large"
    );
    let mut out = BufWriter::with_capacity(256 * 1024, &mut *output);
    write!(
        out,
        "RGP/3.0\nguide_bytes={}\nindex_bytes={}\n--\n",
        guide.len(),
        index.len()
    )?;
    out.write_all(guide)?;
    out.write_all(b"\n")?;
    out.write_all(index.as_bytes())?;
    out.write_all(b"\n")?;
    for file in &manifest.files {
        let bytes = blobs
            .get(&file.blob)
            .ok_or_else(|| anyhow!("HASH_MISMATCH: missing source"))?;
        ensure!(
            bytes.len() as u64 == file.size,
            "HASH_MISMATCH: source size"
        );
        writeln!(out, "FILE\t{}", file.path)?;
        out.write_all(bytes)?;
        out.write_all(b"\n")?;
    }
    out.write_all(b"END RGP\n")?;
    out.flush()?;
    drop(out);
    let size = output.metadata()?.len() as usize;
    ensure!(size <= MAX_LEAN, "RESOURCE_LIMIT: RGP/3.0 too large");
    Ok(size)
}

pub(super) fn verify_lean(path: &Path) -> Result<Manifest> {
    let bytes = fs::read(path)?;
    ensure!(bytes.len() <= MAX_LEAN, "RESOURCE_LIMIT: large RGP/3.0");
    ensure!(
        std::str::from_utf8(&bytes).is_ok() && !bytes.contains(&0),
        "UNSUPPORTED_FORMAT: RGP/3.0 requires UTF-8"
    );
    let mut pos = 0usize;
    ensure!(
        take_line(&bytes, &mut pos)? == b"RGP/3.0",
        "UNSUPPORTED_FORMAT: version"
    );
    let mut field_size = |prefix: &[u8], limit: usize| -> Result<usize> {
        let line = take_line(&bytes, &mut pos)?;
        let count = line
            .strip_prefix(prefix)
            .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: length"))?;
        ensure!(
            !count.is_empty() && count.iter().all(u8::is_ascii_digit),
            "UNSUPPORTED_FORMAT: length field"
        );
        let count: usize = std::str::from_utf8(count)?.parse()?;
        ensure!(count <= limit, "RESOURCE_LIMIT: section size");
        Ok(count)
    };
    let guide_size = field_size(b"guide_bytes=", 8192)?;
    let index_size = field_size(b"index_bytes=", MAX_MANIFEST)?;
    ensure!(
        take_line(&bytes, &mut pos)? == b"--",
        "UNSUPPORTED_FORMAT: separator"
    );
    let end = pos
        .checked_add(guide_size)
        .ok_or_else(|| anyhow!("RESOURCE_LIMIT: guide"))?;
    let guide = bytes
        .get(pos..end)
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: guide"))?;
    ensure!(!guide.is_empty(), "UNSUPPORTED_FORMAT: empty guide");
    pos = end;
    ensure!(
        bytes.get(pos) == Some(&b'\n'),
        "TRUNCATED_ARTIFACT: guide delimiter"
    );
    pos += 1;
    let end = pos
        .checked_add(index_size)
        .ok_or_else(|| anyhow!("RESOURCE_LIMIT: index"))?;
    let index = bytes
        .get(pos..end)
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: index"))?;
    pos = end;
    ensure!(
        bytes.get(pos) == Some(&b'\n'),
        "TRUNCATED_ARTIFACT: index delimiter"
    );
    pos += 1;

    let mut files = Vec::new();
    let mut excluded = Vec::new();
    let mut ignored = 0usize;
    let mut self_count = 0usize;
    let mut in_exclusion = false;
    let mut last_file: Option<String> = None;
    let mut last_excluded: Option<String> = None;
    let mut names = HashSet::new();
    let mut total = 0usize;
    let mut blobs = BTreeMap::new();
    for line in index.split(|b| *b == b'\n').filter(|l| !l.is_empty()) {
        let line = std::str::from_utf8(line)?;
        let parts: Vec<_> = line.split('\t').collect();
        match parts.as_slice() {
            ["F", name, size] | ["F", name, size, ""] => {
                ensure!(!in_exclusion, "UNSUPPORTED_FORMAT: index ordering");
                let f = read_file(&bytes, &mut pos, name, size, "", &mut total, &mut blobs)?;
                ensure!(
                    last_file.as_deref().is_none_or(|previous| previous < *name),
                    "UNSUPPORTED_FORMAT: file order"
                );
                ensure!(
                    names.insert(name.to_lowercase()),
                    "UNSAFE_PATH: duplicate file"
                );
                last_file = Some((*name).to_owned());
                files.push(f);
            }
            ["F", name, size, flag] => {
                ensure!(!in_exclusion, "UNSUPPORTED_FORMAT: index ordering");
                let f = read_file(&bytes, &mut pos, name, size, flag, &mut total, &mut blobs)?;
                ensure!(
                    last_file.as_deref().is_none_or(|previous| previous < *name),
                    "UNSUPPORTED_FORMAT: file order"
                );
                ensure!(
                    names.insert(name.to_lowercase()),
                    "UNSAFE_PATH: duplicate file"
                );
                last_file = Some((*name).to_owned());
                files.push(f);
            }
            ["I", count] => {
                in_exclusion = true;
                ensure!(
                    ignored == 0,
                    "UNSUPPORTED_FORMAT: duplicate ignored summary"
                );
                ignored = count.parse()?;
                ensure!(ignored <= MAX_ENTRIES, "RESOURCE_LIMIT: ignored count");
            }
            ["S", count] => {
                in_exclusion = true;
                ensure!(
                    self_count == 0,
                    "UNSUPPORTED_FORMAT: duplicate self summary"
                );
                self_count = count.parse()?;
                ensure!(self_count <= 2, "RESOURCE_LIMIT: self count");
            }
            ["X", name, reason] => {
                in_exclusion = true;
                validate_path(name)?;
                ensure!(
                    last_excluded
                        .as_deref()
                        .is_none_or(|previous| previous < *name),
                    "UNSUPPORTED_FORMAT: exclusion ordering"
                );
                ensure!(
                    names.insert(name.to_lowercase()),
                    "UNSAFE_PATH: exclusion collision"
                );
                last_excluded = Some((*name).to_owned());
                excluded.push(Exclusion {
                    path: (*name).to_owned(),
                    reason: (*reason).to_owned(),
                });
            }
            _ => return Err(anyhow!("UNSUPPORTED_FORMAT: index record")),
        }
    }
    ensure!(
        files.len() < MAX_ENTRIES && excluded.len() <= MAX_ENTRIES,
        "RESOURCE_LIMIT: file count"
    );
    ensure!(
        take_line(&bytes, &mut pos)? == b"END RGP" && pos == bytes.len(),
        "TRUNCATED_ARTIFACT: terminal marker or trailing bytes"
    );
    // No source commit, SHA, or manifest JSON appears in the on-disk RGP/3.0.
    // Reconstruct strong fingerprints only in trusted process memory.
    let digest = tree_digest(&files)?;
    let manifest = Manifest {
        schema_version: "1.0".into(),
        source_commit: String::new(),
        input_tree_digest: digest,
        files,
        excluded,
        index_coverage: "not_indexed".into(),
        graph_coverage: "not_indexed".into(),
    };
    verify_manifest(manifest, blobs)
}

fn read_file(
    bytes: &[u8],
    pos: &mut usize,
    name: &str,
    decimal: &str,
    flags: &str,
    total: &mut usize,
    blobs: &mut BTreeMap<String, u64>,
) -> Result<FileEntry> {
    validate_path(name)?;
    ensure!(
        !decimal.is_empty() && decimal.bytes().all(|c| c.is_ascii_digit()),
        "UNSUPPORTED_FORMAT: size"
    );
    let len: usize = decimal.parse()?;
    ensure!(len <= MAX_FILE, "RESOURCE_LIMIT: file size");
    *total = total
        .checked_add(len)
        .ok_or_else(|| anyhow!("RESOURCE_LIMIT: total"))?;
    ensure!(*total <= MAX_TOTAL, "RESOURCE_LIMIT: total bytes");
    let (mode, status) = match flags {
        "" => ("100644", "included"),
        "x" => ("100755", "included"),
        "l" => ("120000", "included"),
        "p" => ("100644", "pointer_only"),
        "xp" => ("100755", "pointer_only"),
        "lp" => ("120000", "pointer_only"),
        _ => return Err(anyhow!("UNSUPPORTED_FORMAT: file flags")),
    };
    let expected = format!("FILE\t{name}");
    ensure!(
        take_line(bytes, pos)? == expected.as_bytes(),
        "UNSUPPORTED_FORMAT: file header"
    );
    let end = pos
        .checked_add(len)
        .ok_or_else(|| anyhow!("RESOURCE_LIMIT: source"))?;
    let source = bytes
        .get(*pos..end)
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: source"))?;
    ensure!(
        std::str::from_utf8(source).is_ok() && !source.contains(&0),
        "UNSUPPORTED_FORMAT: non-text source"
    );
    *pos = end;
    ensure!(
        bytes.get(*pos) == Some(&b'\n'),
        "TRUNCATED_ARTIFACT: source delimiter"
    );
    *pos += 1;
    let hash = sha(source);
    let blob = format!("files/{hash}");
    blobs.insert(blob.clone(), len as u64);
    Ok(FileEntry {
        path: name.into(),
        mode: mode.into(),
        kind: if mode == "120000" { "symlink" } else { "file" }.into(),
        size: len as u64,
        sha256: hash,
        blob,
        status: status.into(),
    })
}
