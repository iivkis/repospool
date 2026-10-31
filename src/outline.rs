//! Bounded lexical navigation hints. No parser execution or dependency claims.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Symbol {
    pub line: usize,
    pub kind: &'static str,
    pub name: String,
}

const MAX_PER_FILE: usize = 64;

pub(super) fn symbols_for(path: &str, source: &[u8]) -> Vec<Symbol> {
    let lang = if path.ends_with(".rs") {
        "rust"
    } else if path.ends_with(".py") {
        "python"
    } else if path.ends_with(".go") {
        "go"
    } else if [".ts", ".tsx", ".js", ".jsx"]
        .iter()
        .any(|ext| path.ends_with(ext))
    {
        "js"
    } else {
        return Vec::new();
    };
    let Ok(text) = std::str::from_utf8(source) else {
        return Vec::new();
    };
    let mut found = Vec::new();
    for (line_no, original) in text.lines().enumerate() {
        if found.len() == MAX_PER_FILE {
            break;
        }
        let line = original.trim_start();
        let normalized = match lang {
            "rust" => {
                let mut rest = line;
                for prefix in [
                    "pub(crate) ",
                    "pub(super) ",
                    "pub ",
                    "async ",
                    "unsafe ",
                    "extern ",
                ] {
                    if let Some(next) = rest.strip_prefix(prefix) {
                        rest = next;
                    }
                }
                for prefix in ["async ", "unsafe "] {
                    if let Some(next) = rest.strip_prefix(prefix) {
                        rest = next;
                    }
                }
                rest
            }
            "js" => line
                .strip_prefix("export default ")
                .or_else(|| line.strip_prefix("export "))
                .unwrap_or(line),
            _ => line,
        };
        let patterns: &[(&str, &str)] = match lang {
            "rust" => &[
                ("fn ", "fn"),
                ("struct ", "struct"),
                ("enum ", "enum"),
                ("trait ", "trait"),
                ("mod ", "mod"),
            ],
            "python" => &[("async def ", "fn"), ("def ", "fn"), ("class ", "class")],
            "go" => &[("func ", "fn"), ("type ", "type")],
            _ => &[
                ("async function ", "fn"),
                ("function ", "fn"),
                ("class ", "class"),
                ("interface ", "interface"),
                ("type ", "type"),
            ],
        };
        for (prefix, kind) in patterns {
            if let Some(after) = normalized.strip_prefix(prefix) {
                let name = after
                    .split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
                    .next()
                    .unwrap_or("");
                if !name.is_empty()
                    && name.len() <= 80
                    && name
                        .bytes()
                        .next()
                        .is_some_and(|b| b.is_ascii_alphabetic() || b == b'_')
                {
                    found.push(Symbol {
                        line: line_no + 1,
                        kind,
                        name: name.to_owned(),
                    });
                }
                break;
            }
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn extracts_bounded_rust_and_python() {
        let src = b"// fn ignored()\npub fn build() {}\nstruct Thing {}\n    fn method() {}\n";
        let symbols = symbols_for("src/lib.rs", src);
        assert_eq!(
            symbols
                .iter()
                .map(|s| (&s.name, s.line))
                .collect::<Vec<_>>(),
            vec![
                (&"build".to_owned(), 2),
                (&"Thing".to_owned(), 3),
                (&"method".to_owned(), 4)
            ]
        );
        assert!(symbols_for("readme.md", src).is_empty());
        let py = symbols_for("a.py", b"def run():\n    pass\nclass Item:\n    pass\n");
        assert_eq!(py.len(), 2);
    }
}
