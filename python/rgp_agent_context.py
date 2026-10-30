#!/usr/bin/env python3
"""Small, source-grounded context handoff for a human or LLM coding agent.

This is deliberately lexical: the model chooses terms, this process finds
source with the trusted stdlib Reader. It never infers a dependency graph,
runs archived source code, or claims that a code snippet is an answer.
All output is bounded and can be cited as path:start-end at a pinned commit.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from rgp_reader import Reader, RgpError

MAX_TERMS = 6
MAX_SEARCH = 100
MAX_FILES = 6
MAX_BYTES = 64 * 1024


def compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def context(reader: Reader, terms: list[str], *, prefix: str = "",
            max_files: int = 4, max_bytes: int = 12_000) -> dict:
    if not 1 <= len(terms) <= MAX_TERMS or any(
        not isinstance(term, str) or not 1 <= len(term.encode("utf-8")) <= 128
        for term in terms
    ):
        raise ValueError("give 1-6 literal search terms, each 1-128 UTF-8 bytes")
    if not isinstance(max_files, int) or not 1 <= max_files <= MAX_FILES:
        raise ValueError("max_files must be within 1..6")
    if not isinstance(max_bytes, int) or not 2048 <= max_bytes <= MAX_BYTES:
        raise ValueError("max_bytes must be within 2048..65536")

    state = reader.overview()
    hits: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    incomplete = []
    total_matches = 0
    for term in dict.fromkeys(terms):
        result = reader.search(term, prefix=prefix, limit=MAX_SEARCH)
        if result["next_cursor"] is not None:
            incomplete.append(term)
        total_matches += len(result["matches"])
        for hit in result["matches"]:
            hits[hit["path"]].append((term, hit["line"], hit["excerpt"]))

    ordered_paths = sorted(
        hits, key=lambda path: (
            -len({term for term, _, _ in hits[path]}),
            -len(hits[path]), path,
        )
    )
    response = {
        "kind": "rgp_agent_source_context",
        "source_commit": state["source_commit"],
        "snapshot_complete": state["snapshot_complete"],
        "index_coverage": reader.coverage("index")["status"],
        "graph_coverage": reader.coverage("graph")["status"],
        "retrieval": "lexical_not_semantic",
        "terms": list(dict.fromkeys(terms)),
        "prefix": prefix,
        "max_output_bytes": max_bytes,
        "search_hits_observed": total_matches,
        "search_capped_terms": incomplete,
        "candidate_paths": len(ordered_paths),
        "sources": [],
        "selection_truncated": False,
        "source_instructions_untrusted": True,
    }

    for path in ordered_paths[:max_files]:
        seen_lines = set()
        # Diversity matters more than repetition: 'serialize_i64'
        # may match several implementations before we reach f64/str.
        # Give each distinct term one source passage first, then fill
        # spare slots with additional matches in original search order.
        first_per_term = {}
        for hit in hits[path]:
            first_per_term.setdefault(hit[0], hit)
        prioritized = list(first_per_term.values())
        prioritized += [hit for hit in hits[path] if hit not in prioritized]
        for term, number, excerpt in prioritized:
            if number in seen_lines:
                continue
            seen_lines.add(number)
            # A short read gives the LLM the surrounding source, not just
            # a keyword match. An EOF-adjacent match can be read through
            # the exact hit line rather than asking beyond EOF.
            begin = max(1, number - 2)
            end = number + 7
            try:
                text = reader.read(path, begin, end)
            except RgpError as error:
                if error.code != "UNSUPPORTED_FORMAT":
                    raise
                try:
                    text = reader.read(path, begin, number)
                except RgpError:
                    # A long binary file or oversized result is not
                    # silently converted into a valid source quotation.
                    continue
            if text.get("encoding") != "utf-8":
                continue
            source = {
                "path": path,
                "match_term": term,
                "match_line": number,
                "start_line": text["start_line"],
                "end_line": text["end_line"],
                "content": text["content"],
                "source_reference": f"{path}:{text['start_line']}-{text['end_line']}",
            }
            response["sources"].append(source)
            if len(compact_json(response).encode("utf-8")) > max_bytes:
                response["sources"].pop()
                response["selection_truncated"] = True
                break
            if sum(s["path"] == path for s in response["sources"]) >= 3:
                break
        if response["selection_truncated"]:
            break

    response["selection_truncated"] = (
        response["selection_truncated"] or len(ordered_paths) > max_files
        or total_matches > len(response["sources"])
    )
    while len(compact_json(response).encode("utf-8")) > max_bytes and response["sources"]:
        response["sources"].pop()
        response["selection_truncated"] = True
    # Source-coverage is a separate dimension from query match count.
    # A search term may match a file yet have no *selected* source in
    # the agent prompt. Never present that as a grounded answer.
    selected = {source["match_term"] for source in response["sources"]}
    response["terms_without_source"] = [
        term for term in response["terms"] if term not in selected
    ]
    while len(compact_json(response).encode("utf-8")) > max_bytes and response["sources"]:
        response["sources"].pop()
        response["selection_truncated"] = True
        selected = {source["match_term"] for source in response["sources"]}
        response["terms_without_source"] = [
            term for term in response["terms"] if term not in selected
        ]
    # No answer correctness is inferred from a successful lexical hit.
    # Never label a no-match or truncated search 'source absent'.
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--term", required=True, action="append", dest="terms")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--max-files", type=int, default=4)
    parser.add_argument("--max-bytes", type=int, default=12_000)
    args = parser.parse_args()
    try:
        result = context(Reader(args.artifact), args.terms,
                         prefix=args.prefix, max_files=args.max_files,
                         max_bytes=args.max_bytes)
    except (RgpError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    # Emit exactly the budgeted JSON bytes: pretty-printing would
    # silently exceed --max-bytes even when source selection did not.
    print(compact_json(result), end="")


if __name__ == "__main__":
    main()
