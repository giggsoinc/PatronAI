# =============================================================
# FILE: src/matcher/rule_model.py
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Single source of truth for rule shape and hygiene.
#          Forgive-input, store-strict: normalise aggressively at
#          write-time so admins paste anything; validate strictly
#          at read-time so the matcher never trips on a typo.
#          Stdlib only — keeps OSS footprint lean.
# DEPENDS: stdlib (csv, io, fnmatch)
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 6 ruleset hardening.
# =============================================================

import csv
import io
import logging
from fnmatch import fnmatch
from typing import Callable, Iterable, List, Tuple

log = logging.getLogger("marauder-scan.matcher.rule_model")

SEVERITIES = ("HIGH", "MEDIUM", "LOW")
TOO_BROAD = {
    "*", "*.*",
    "*.com", "*.org", "*.net", "*.io", "*.ai",
    "*.co", "*.dev", "*.app", "*.cloud",
}
_ZW_TRIM = str.maketrans("", "", "\u200b\u200c\u200d\ufeff")


def normalize_domain(value: str) -> str:
    """Strip schemes, paths, quotes, zero-width chars; lowercase; drop trailing dot."""
    if not value:
        return ""
    s = value.strip().strip('"\'`').translate(_ZW_TRIM)
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split(":", 1)[0]
    return s.lower().rstrip(".")


def normalize_severity(value: str) -> str:
    """Uppercase + strip; default HIGH if missing or unrecognised."""
    s = (value or "").strip().upper()
    return s if s in SEVERITIES else "HIGH"


def is_too_broad(pattern: str) -> bool:
    """Return True if pattern matches a corpus too large to be a useful deny rule."""
    return (pattern or "").lower().strip() in TOO_BROAD


def valid_glob(pattern: str) -> bool:
    """Sanity-check a glob via a single fnmatch dry-run."""
    try:
        fnmatch("test.example.com", pattern)
        return True
    except Exception:  # intentional: returns safe default on any error
        return False


def _g(row: dict, key: str) -> str:
    """Safe string-get from a CSV row; treats missing/None as empty."""
    return (row.get(key) or "").strip()


def validate_rule(row: dict) -> dict:
    """Validate + normalise a network deny row. Raise ValueError on irrecoverable input."""
    domain = normalize_domain(_g(row, "domain"))
    port_raw = _g(row, "port")
    try:
        port = int(port_raw) if port_raw else 0
    except ValueError as e:
        raise ValueError(f"port not numeric: {port_raw!r}") from e
    if not (0 <= port <= 65535):
        raise ValueError(f"port out of range: {port}")
    if not domain and not port:
        raise ValueError("domain and port both empty")
    if domain and not valid_glob(domain):
        raise ValueError(f"invalid glob: {domain!r}")
    if domain and is_too_broad(domain):
        raise ValueError(f"pattern too broad: {domain!r}")
    return {"name": _g(row, "name"), "category": _g(row, "category"),
            "domain": domain, "port": port,
            "severity": normalize_severity(_g(row, "severity")),
            "notes": _g(row, "notes")}


def validate_allow_rule(row: dict) -> dict:
    """Validate + normalise an allow-list row. Raise ValueError on error."""
    pattern = normalize_domain(_g(row, "domain_pattern"))
    if not pattern:
        raise ValueError("domain_pattern empty")
    if not valid_glob(pattern):
        raise ValueError(f"invalid glob: {pattern!r}")
    if is_too_broad(pattern):
        raise ValueError(f"allow pattern too broad: {pattern!r}")
    return {"name": _g(row, "name"), "domain_pattern": pattern, "notes": _g(row, "notes")}


def validate_code_rule(row: dict) -> dict:
    """Validate + normalise a code deny row. Raise ValueError on error."""
    pattern = _g(row, "pattern")
    if not pattern:
        raise ValueError("pattern empty")
    return {"name": _g(row, "name"), "type": _g(row, "type").lower(),
            "pattern": pattern.lower(), "dept_scope": _g(row, "dept_scope"),
            "severity": normalize_severity(_g(row, "severity")),
            "notes": _g(row, "notes")}


def parse_csv_text(raw: str, validator: Callable[[dict], dict]) -> Tuple[List[dict], List[dict]]:
    """Parse CSV; run each row through the validator. Return (clean, errors)."""
    if not raw:
        return [], []
    clean: List[dict] = []
    errors: List[dict] = []
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    for line_no, row in enumerate(reader, start=2):  # header is line 1
        try:
            clean.append(validator(row))
        except ValueError as exc:
            errors.append({"line": line_no, "row": row, "reason": str(exc)})
    return clean, errors


def dedupe(rows: List[dict], key_cols: Iterable[str]) -> List[dict]:
    """Last-write-wins dedup keyed on the given columns. Preserves insertion order."""
    seen: dict = {}
    for row in rows:
        key = tuple(row.get(c) for c in key_cols)
        seen[key] = row
    return list(seen.values())


def find_conflicts(allow: List[dict], deny: List[dict]) -> List[dict]:
    """Yield {allow, deny} pairs where an allow pattern would suppress a deny rule."""
    out: List[dict] = []
    for d in deny:
        domain = d.get("domain") or ""
        if not domain:
            continue
        for a in allow:
            if fnmatch(domain, a.get("domain_pattern", "")):
                out.append({"allow": a, "deny": d})
                break
    return out
