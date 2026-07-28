#!/usr/bin/env python3
"""Gray-material scrub — path denylist + light content heuristics.

Default / CI: fail on denylisted path **names** and key-shaped **content** in
tracked text. Exit 0 is still **not** “safe to publish” if vendor ELF / ``.xgf``
/ GSOC decomp remain (intentional for local RE).

``--history``: also scan path names from ``git log --all --name-only``.

``--publish``: additionally **fail** on tracked vendor surface that must be
stripped before a public remote (``gsoc_app``, ``analysis/decomp/``, ``.xgf``,
archives). Run this before first public push.

Does **not** scan commit messages. Pre-push hook is optional and skippable;
CI cannot save a first public push.

Exit 0 = no failing hits for the selected mode.
Exit 1 = gray / publish-strip material.
Exit 2 = usage / git error.

CI runs ``--publish`` on every push. Before a public fork or release, run the
same command locally — this repo must not ship vendor firmware (``.xgf``,
``gsoc_app``), AES keys, or decompilation dumps.

Examples::

  python3 scripts/publish_scrub_check.py          # local dev (warn on strip paths)
  python3 scripts/publish_scrub_check.py --publish  # public gate (CI)
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Keep aligned with .gitignore gray entries.
GRAY_PATH_GLOBS = (
    "reference/g90-fw/decrypted",
    "reference/g90-fw/decrypted/**",
    "reference/g90-fw/**/*.bin",
    "reference/g90-fw/**/*key*",
    "analysis/g90-decomp",
    "analysis/g90-decomp/**",
    "tools/g90updatefw",
    "tools/g90updatefw/**",
    "**/*.aeskey",
    "**/g90*aes*key*",
    "**/aes*key*.txt",
    "**/*_aes_key*",
    # Renamed / smuggled plaintext + archives (path names)
    "**/*.bin",
    "analysis/**/*.zip",
    "analysis/**/*.tar",
    "analysis/**/*.tar.gz",
    "analysis/**/*.tgz",
    "analysis/**/*.7z",
    "reference/**/*.zip",
    "reference/**/*.tar",
    "reference/**/*.tar.gz",
    "reference/**/*.tgz",
    "reference/**/*.7z",
    "dist/**",
    "dist",
)

# Path substrings (lowercase). "decrypt" covers decrypted_*, decrypt_fw, etc.
GRAY_NAME_HINTS = (
    "aeskey",
    "aes_key",
    "aes-key",
    "g90_aes",
    "decrypt",
)

# Tracked paths allowed even if they match **/*.bin (fixtures are .hex today).
BIN_ALLOWLIST_GLOBS: tuple[str, ...] = ()

# Must strip (or accept redistribution risk) before a *public* remote.
PUBLISH_STRIP_GLOBS = (
    "firmware/app/gsoc_app*",
    "analysis/decomp",
    "analysis/decomp/**",
    "reference/g90-fw/**/*.xgf",
    "**/*.xgf",
)

# Content scan skips (SHA256 digests, frame hex dumps).
CONTENT_SKIP_GLOBS = (
    "**/SHA256SUMS*",
    "**/sha256sums*",
    "tests/fixtures/**",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.webp",
    "**/*.pdf",
    "**/*.xgf",
    "firmware/app/**",
    "analysis/decomp/**",
)

TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".py",
        ".sh",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".cfg",
        ".ini",
        ".csv",
        ".tsv",
        ".rst",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".cmake",
        ".ld",
        ".S",
        ".s",
        "",  # extensionless scripts / notes
    }
)

# AES-256 key as hex (32 bytes) or common base64 form (44 chars w/ padding).
_HEX64 = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{64})(?![0-9a-fA-F])")
_B64_KEY = re.compile(r"(?<![A-Za-z0-9+/])([A-Za-z0-9+/]{43}=)(?![A-Za-z0-9+/])")
_KEY_CONTEXT = re.compile(
    r"(?i)(\baes\b|decrypt|cipher|\becb\b|xgf|firmware\s*key|g90\s*key|"
    r"static\s+key|vendor\s+key)"
)

# Cortex-M vector table heuristic (SP in SRAM, Reset in flash).
_MAX_CONTENT_BYTES = 512_000


def _git(args: list[str]) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _match_any(path: str, globs: tuple[str, ...]) -> bool:
    p = _norm(path)
    if not p or p == ".":
        return False
    low = p.lower()
    for pat in globs:
        if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(low, pat.lower()):
            return True
        if pat.endswith("/**"):
            prefix = pat[:-3]
            if p == prefix or p.startswith(prefix + "/"):
                return True
        elif "*" not in pat and ("/" in pat or pat.startswith(".")):
            if p == pat or p.startswith(pat + "/"):
                return True
    return False


def _is_gray(path: str) -> bool:
    p = _norm(path)
    if not p or p == ".":
        return False
    if _match_any(p, BIN_ALLOWLIST_GLOBS):
        return False
    low = p.lower()
    for hint in GRAY_NAME_HINTS:
        if hint in low:
            return True
    return _match_any(p, GRAY_PATH_GLOBS)


def _is_publish_strip(path: str) -> bool:
    return _match_any(path, PUBLISH_STRIP_GLOBS)


def _nul_paths(stdout: str) -> set[str]:
    return {p for p in stdout.split("\0") if p}


def _should_scan_content(path: str) -> bool:
    p = _norm(path)
    if _match_any(p, CONTENT_SKIP_GLOBS):
        return False
    suf = Path(p).suffix.lower()
    if suf in {".bin", ".elf", ".o", ".a", ".so", ".dylib", ".exe", ".zip", ".gz", ".7z", ".xz"}:
        return False
    # Extensionless or known text-ish
    if suf in TEXT_SUFFIXES or suf == "":
        return True
    # Odd text-like names (KEY, NOTES, …)
    return suf not in {".xgf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}


def _scan_text_for_keys(path: str, text: str) -> list[str]:
    """Return human-readable hit strings (no key material echoed)."""
    hits: list[str] = []
    path_ctx = bool(_KEY_CONTEXT.search(path))
    for rx, kind in ((_HEX64, "aes-256-hex-shaped"), (_B64_KEY, "aes-key-b64-shaped")):
        for m in rx.finditer(text):
            lo = max(0, m.start() - 96)
            hi = min(len(text), m.end() + 96)
            window = text[lo:hi]
            if path_ctx or _KEY_CONTEXT.search(window):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path}:{line}: {kind} near key/AES context")
                break  # one hit per kind per file is enough
    return hits


def _looks_like_cortex_m_vector_table(data: bytes) -> bool:
    if len(data) < 8:
        return False
    sp = int.from_bytes(data[0:4], "little")
    reset = int.from_bytes(data[4:8], "little")
    # STM32: SP in SRAM, Reset in flash (Thumb bit may be set)
    if not (0x20000000 <= sp <= 0x20040000):
        return False
    reset_thr = reset & ~1
    return 0x08000000 <= reset_thr <= 0x08100000


def _scan_binary_blob(path: str, data: bytes) -> list[str]:
    p = _norm(path)
    if _match_any(p, CONTENT_SKIP_GLOBS) or _match_any(p, PUBLISH_STRIP_GLOBS):
        return []
    if _looks_like_cortex_m_vector_table(data[:64]):
        return [f"{p}: cortex-M vector table heuristic (possible plaintext FW)"]
    return []


def _scan_tracked_content(tracked: set[str]) -> list[str]:
    hits: list[str] = []
    for rel in sorted(tracked):
        if not _should_scan_content(rel) and not rel.lower().endswith(".bin"):
            # Still probe small unknown binaries for vector tables
            if not rel.lower().endswith(".bin"):
                continue
        path = REPO_ROOT / rel
        try:
            raw = path.read_bytes()[:_MAX_CONTENT_BYTES]
        except OSError:
            continue
        if rel.lower().endswith(".bin") or (
            b"\0" in raw[:1024] and not _should_scan_content(rel)
        ):
            hits.extend(_scan_binary_blob(rel, raw))
            continue
        if not _should_scan_content(rel):
            continue
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            hits.extend(_scan_binary_blob(rel, raw))
            continue
        hits.extend(_scan_text_for_keys(rel, text))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--history",
        action="store_true",
        help="scan all commits for gray path names (required before first public remote)",
    )
    ap.add_argument(
        "--publish",
        action="store_true",
        help="also fail on tracked vendor surface (gsoc_app, analysis/decomp, .xgf, …)",
    )
    ap.add_argument(
        "--list-untracked-gray",
        action="store_true",
        help="print untracked/ignored gray paths (informational; does not fail)",
    )
    ap.add_argument(
        "--skip-content",
        action="store_true",
        help="path names only (disable content heuristics)",
    )
    args = ap.parse_args(argv)

    try:
        _git(["rev-parse", "--is-inside-work-tree"])
    except RuntimeError as e:
        print(f"publish_scrub_check: {e}", file=sys.stderr)
        return 2

    tracked = _nul_paths(_git(["ls-files", "-z"]))
    bad_tracked = sorted(p for p in tracked if _is_gray(p))

    bad_history: list[str] = []
    if args.history:
        hist = {
            line.strip()
            for line in _git(["log", "--all", "--name-only", "--pretty=format:"]).splitlines()
            if line.strip()
        }
        bad_history = sorted(p for p in hist if _is_gray(p))

    publish_strip = sorted(p for p in tracked if _is_publish_strip(p))
    content_hits: list[str] = []
    if not args.skip_content:
        content_hits = _scan_tracked_content(tracked)

    if args.list_untracked_gray:
        untracked = _nul_paths(
            _git(["ls-files", "-z", "--others", "--exclude-standard"])
        )
        ignored = _nul_paths(
            _git(["ls-files", "-z", "--others", "-i", "--exclude-standard"])
        )
        bad_local = sorted(p for p in (untracked | ignored) if _is_gray(p))
        if bad_local:
            print("GRAY LOCAL (not tracked; OK for RE, strip before publish packaging):")
            for p in bad_local:
                print(f"  {p}")
        else:
            print("No local untracked/ignored gray paths found.")

    if bad_tracked:
        print("GRAY TRACKED (must not ship):")
        for p in bad_tracked:
            print(f"  {p}")
    if bad_history:
        print("GRAY IN GIT HISTORY (rewrite/strip before public remote):")
        for p in bad_history:
            print(f"  {p}")
    if content_hits:
        print("GRAY CONTENT (key-shaped material in tracked text/bin):")
        for h in content_hits:
            print(f"  {h}")

    if publish_strip:
        label = (
            "PUBLISH-STRIP TRACKED (fail with --publish; OK for local RE):"
            if not args.publish
            else "PUBLISH-STRIP TRACKED (must remove/rewrite before public remote):"
        )
        print(label)
        # Cap noisy decomp listing
        shown = publish_strip
        if len(shown) > 20:
            shown = publish_strip[:20] + [f"… +{len(publish_strip) - 20} more"]
        for p in shown:
            print(f"  {p}")

    fail = bool(bad_tracked or bad_history or content_hits)
    if args.publish and publish_strip:
        fail = True

    if fail:
        print(
            "\npublish_scrub_check: FAIL — remove gray material above; "
            "run with --help for modes",
            file=sys.stderr,
        )
        return 1

    scope = "tracked tree"
    if args.history:
        scope += " + history"
    if not args.skip_content:
        scope += " + content"
    note = ""
    if publish_strip and not args.publish:
        note = (
            f"; {len(publish_strip)} publish-strip path(s) still tracked "
            "(use --publish to fail)"
        )
    print(f"publish_scrub_check: OK ({scope}{note})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
