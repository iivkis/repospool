## Change and acceptance

- [ ] Goal stated in observable terms
- [ ] RepoSpool remains language-agnostic and does not infer code meaning
- [ ] No semantic symbols, file-role guesses or per-file hashes in artifact
- [ ] Single `repospool generate .` artifact still reads without companion tools
- [ ] Included files, byte framing, RepoSpool line coordinates and omission coverage remain correct

## Verification

- [ ] `task check`
- [ ] `task nix:check`
- [ ] Output stable for identical pinned Git input
- [ ] Invalid, truncated and outdated formats rejected
- [ ] GitHub CI results checked
- [ ] Any skipped checks explicitly marked

## Security

- [ ] No secrets or private sources published
- [ ] Source revision and locator treated as untrusted hints
- [ ] Changes to live GitHub repositories independently verified

See [AGENTS.md](../AGENTS.md) and
[product contract](../docs/PRODUCT_CONTRACT.md).
