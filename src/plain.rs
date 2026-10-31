//! RGP/2.0: canonical, readable, UTF-8 source transport.
//! Sections are framed by exact UTF-8 byte lengths, never by a sentinel
//! inside source text. No compression, Base64 or escaping of source bytes.
use super::*;

const MAX_PLAIN_ARTIFACT: usize = 192 * 1024 * 1024;
const MAX_GUIDE: usize = 8192;

fn next_line<'a>(bytes: &'a [u8], offset: &mut usize) -> Result<&'a [u8]> {
    let start = *offset;
    let rest = bytes
        .get(start..)
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: unexpected end"))?;
    let newline = rest
        .iter()
        .position(|byte| *byte == b'\n')
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: missing newline"))?;
    *offset = start + newline + 1;
    Ok(&bytes[start..start + newline])
}

pub(super) fn write_plain(
    output: &mut fs::File,
    manifest: &Manifest,
    blobs: &BTreeMap<String, Vec<u8>>,
) -> Result<usize> {
    let manifest_data = serde_json::to_vec_pretty(manifest)?;
    ensure!(
        manifest_data.len() <= MAX_MANIFEST,
        "RESOURCE_LIMIT: manifest too large"
    );
    let guide = INLINE_AGENT_GUIDE.as_bytes();
    ensure!(guide.len() <= MAX_GUIDE, "RESOURCE_LIMIT: guide too large");
    let complete = manifest.files.iter().all(|file| file.status != "pointer_only")
        && manifest.excluded.iter().all(|entry| {
            matches!(entry.reason.as_str(), "self_artifact" | "rgpignore")
        });
    let mut writer = BufWriter::with_capacity(256 * 1024, &mut *output);
    write!(
        writer,
        "RGP/2.1\nsource_commit={}\nsnapshot_complete={}\nguide_length={}\nguide_sha256={}\nmanifest_length={}\nmanifest_sha256={}\n--\n",
        manifest.source_commit,
        complete,
        guide.len(),
        sha(guide),
        manifest_data.len(),
        sha(&manifest_data)
    )?;
    writer.write_all(guide)?;
    writer.write_all(b"\n")?;
    writer.write_all(&manifest_data)?;
    writer.write_all(b"\n")?;
    for file in &manifest.files {
        let source = blobs
            .get(&file.blob)
            .ok_or_else(|| anyhow!("HASH_MISMATCH: missing generated source"))?;
        ensure!(
            source.len() as u64 == file.size,
            "HASH_MISMATCH: source size"
        );
        // JSON string syntax is only used for the PATH in each header.
        // Source content is written byte-for-byte, not JSON escaped.
        writeln!(
            writer,
            "FILE\t{}\t{}\t{}",
            serde_json::to_string(&file.path)?,
            file.size,
            file.sha256
        )?;
        writer.write_all(source)?;
        writer.write_all(b"\n")?;
    }
    writer.write_all(b"END RGP\n")?;
    writer.flush()?;
    drop(writer);
    let len = output.metadata()?.len() as usize;
    ensure!(
        len <= MAX_PLAIN_ARTIFACT,
        "RESOURCE_LIMIT: plaintext transport too large"
    );
    Ok(len)
}

pub(super) fn verify_plain(path: &Path) -> Result<Manifest> {
    let bytes = fs::read(path)?;
    ensure!(
        bytes.len() <= MAX_PLAIN_ARTIFACT,
        "RESOURCE_LIMIT: RGP too large"
    );
    // This enables a text-returning GitHub connector and line-scoped reads.
    ensure!(
        std::str::from_utf8(&bytes).is_ok() && !bytes.contains(&0),
        "UNSUPPORTED_FORMAT: RGP/2.0 must be readable UTF-8"
    );
    let mut pos = 0;
    let version = next_line(&bytes, &mut pos)?;
    ensure!(
        matches!(version, b"RGP/2.0" | b"RGP/2.1"),
        "UNSUPPORTED_FORMAT: version"
    );
    let modern = version == b"RGP/2.1";
    let mut declared_commit = None;
    let mut declared_complete = None;
    let mut guide_metadata = None;
    if modern {
        let commit_line = next_line(&bytes, &mut pos)?;
        let commit_bytes = commit_line
            .strip_prefix(b"source_commit=")
            .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: source commit header"))?;
        let commit = std::str::from_utf8(commit_bytes)?;
        ensure!(
            (commit.len() == 40 || commit.len() == 64)
                && commit.bytes().all(|c| c.is_ascii_hexdigit()),
            "UNSUPPORTED_FORMAT: source commit"
        );
        declared_commit = Some(commit.to_owned());
        let complete_line = next_line(&bytes, &mut pos)?;
        declared_complete = Some(match complete_line {
            b"snapshot_complete=true" => true,
            b"snapshot_complete=false" => false,
            _ => return Err(anyhow!("UNSUPPORTED_FORMAT: snapshot completeness header")),
        });
        let length_line = next_line(&bytes, &mut pos)?;
        let length = length_line
            .strip_prefix(b"guide_length=")
            .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: guide length field"))?;
        let length = std::str::from_utf8(length)?;
        ensure!(
            !length.is_empty() && length.bytes().all(|c| c.is_ascii_digit()),
            "UNSUPPORTED_FORMAT: guide length"
        );
        let size: usize = length.parse()?;
        ensure!(size > 0 && size <= MAX_GUIDE, "RESOURCE_LIMIT: guide size");
        let hash_line = next_line(&bytes, &mut pos)?;
        let hash = hash_line
            .strip_prefix(b"guide_sha256=")
            .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: guide hash field"))?;
        ensure!(
            hash.len() == 64 && hash.iter().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()),
            "UNSUPPORTED_FORMAT: guide hash"
        );
        guide_metadata = Some((size, hash.to_vec()));
    }
    let size_line = next_line(&bytes, &mut pos)?;
    let size_str = size_line
        .strip_prefix(b"manifest_length=")
        .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: manifest length field"))?;
    let size_str = std::str::from_utf8(size_str)?;
    ensure!(
        !size_str.is_empty() && size_str.bytes().all(|v| v.is_ascii_digit()),
        "UNSUPPORTED_FORMAT: manifest length"
    );
    let size: usize = size_str.parse()?;
    ensure!(size <= MAX_MANIFEST, "RESOURCE_LIMIT: manifest too large");
    let digest_line = next_line(&bytes, &mut pos)?;
    let digest = digest_line
        .strip_prefix(b"manifest_sha256=")
        .ok_or_else(|| anyhow!("UNSUPPORTED_FORMAT: manifest hash field"))?;
    ensure!(
        digest.len() == 64
            && digest
                .iter()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()),
        "UNSUPPORTED_FORMAT: manifest digest"
    );
    ensure!(
        next_line(&bytes, &mut pos)? == b"--",
        "UNSUPPORTED_FORMAT: header separator"
    );
    if let Some((length, hash)) = guide_metadata {
        let end = pos.checked_add(length).ok_or_else(|| anyhow!("RESOURCE_LIMIT: guide length"))?;
        let guide = bytes.get(pos..end).ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: guide"))?;
        ensure!(
            std::str::from_utf8(guide).is_ok() && !guide.contains(&0),
            "UNSUPPORTED_FORMAT: guide must be text"
        );
        ensure!(sha(guide).as_bytes() == hash, "HASH_MISMATCH: embedded guide");
        pos = end;
        ensure!(bytes.get(pos) == Some(&b'\n'), "TRUNCATED_ARTIFACT: guide separator");
        pos += 1;
    }
    let end = pos
        .checked_add(size)
        .ok_or_else(|| anyhow!("RESOURCE_LIMIT: manifest size"))?;
    let manifest_bytes = bytes
        .get(pos..end)
        .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: manifest cut short"))?;
    ensure!(
        sha(manifest_bytes).as_bytes() == digest,
        "HASH_MISMATCH: manifest digest"
    );
    let manifest: Manifest = serde_json::from_slice(manifest_bytes)?;
    pos = end;
    ensure!(
        bytes.get(pos) == Some(&b'\n'),
        "TRUNCATED_ARTIFACT: manifest separator"
    );
    pos += 1;
    ensure!(
        manifest.files.len() < MAX_ENTRIES,
        "RESOURCE_LIMIT: too many source files"
    );
    let mut blobs = BTreeMap::new();
    let mut total = 0usize;
    for file in &manifest.files {
        let header = next_line(&bytes, &mut pos)?;
        let expected = format!(
            "FILE\t{}\t{}\t{}",
            serde_json::to_string(&file.path)?,
            file.size,
            file.sha256
        );
        ensure!(
            header == expected.as_bytes(),
            "UNSUPPORTED_FORMAT: file header/order"
        );
        let len: usize = file
            .size
            .try_into()
            .map_err(|_| anyhow!("RESOURCE_LIMIT: file length"))?;
        ensure!(len <= MAX_FILE, "RESOURCE_LIMIT: source file too large");
        total = total
            .checked_add(len)
            .ok_or_else(|| anyhow!("RESOURCE_LIMIT: total size"))?;
        ensure!(total <= MAX_TOTAL, "RESOURCE_LIMIT: source total too large");
        let end = pos
            .checked_add(len)
            .ok_or_else(|| anyhow!("RESOURCE_LIMIT: source length"))?;
        let raw = bytes
            .get(pos..end)
            .ok_or_else(|| anyhow!("TRUNCATED_ARTIFACT: source"))?;
        ensure!(
            std::str::from_utf8(raw).is_ok() && !raw.contains(&0),
            "UNSUPPORTED_FORMAT: binary content"
        );
        ensure!(sha(raw) == file.sha256, "HASH_MISMATCH: source content");
        blobs.insert(file.blob.clone(), file.size);
        pos = end;
        ensure!(
            bytes.get(pos) == Some(&b'\n'),
            "TRUNCATED_ARTIFACT: source separator"
        );
        pos += 1;
    }
    ensure!(
        next_line(&bytes, &mut pos)? == b"END RGP",
        "TRUNCATED_ARTIFACT: end marker"
    );
    ensure!(pos == bytes.len(), "UNSUPPORTED_FORMAT: trailing data");
    let manifest = verify_manifest(manifest, blobs)?;
    if modern {
        ensure!(
            declared_commit.as_deref() == Some(manifest.source_commit.as_str()),
            "HASH_MISMATCH: source commit header"
        );
        let complete = manifest.files.iter().all(|file| file.status != "pointer_only")
            && manifest.excluded.iter().all(|entry| {
                matches!(entry.reason.as_str(), "self_artifact" | "rgpignore")
            });
        ensure!(
            declared_complete == Some(complete),
            "HASH_MISMATCH: snapshot completeness header"
        );
    }
    Ok(manifest)
}
