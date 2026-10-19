"""Print each release's download counts with the owner's own updates and
the release workflow's own downloads taken out, to see whether anyone
else runs tokitty (#170).

Stdlib only, plus the gh CLI (logged in) for the GitHub API: the job
logs this reads need a token, and gh already holds one. Run by hand;
it makes a few hundred API calls and takes a minute or two.

What comes out of the raw counts:

- Own updates come from scripts/own_downloads.json, a committed map of
  tag -> platform -> count. Nothing on GitHub tells the owner's
  downloads apart from anyone else's, so they are an input, and a file
  in the repo keeps working as releases come out: add a line when you
  update a machine, and the history stays reviewable in git.
- CI downloads are counted from the release workflow's own runs. Each
  build job of release.yml installs the new build over the LEGACY_TAG
  archive for its target ("Verify update from a shipped release"), and
  until #169 that step always fetched the archive with `gh release
  download`. Since #169 the archive is cached ("Restore the shipped
  release") and fetched only on a cache miss. So for every run, of
  every attempt, each build job whose verify step ran counts as one
  download of LEGACY_TAG's archive for that job's target, unless the job
  has the cache step and its log says the cache was restored. A job
  carried over unchanged into a re-run attempt (same verify step start
  time) counts once. LEGACY_TAG
  is read from release.yml at the run's commit, so moving it later
  charges the right release. A log GitHub no longer has is counted as a
  miss, with a warning.

Only the platform archives are counted. SHA256SUMS is fetched alongside
every update, so it would count everything twice.

Usage: python3 scripts/download_counts.py
       python3 scripts/download_counts.py --repo owner/name --own path/to/own.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

REPO = "nickwolf/tokitty"
OWN_FILE = Path(__file__).resolve().parent / "own_downloads.json"
WORKFLOW = ".github/workflows/release.yml"

VERIFY_STEP = "Verify update from a shipped release"
CACHE_STEP = "Restore the shipped release"

Counts = Dict[str, Dict[str, int]]  # tag -> platform -> count

_ARCHIVE = re.compile(r"tokitty-(?P<tag>v[^-]+)-(?P<platform>.+?)\.(?:zip|tar\.gz)")
_LEGACY_TAG = re.compile(r"^\s*LEGACY_TAG:\s*['\"]?(v[\w.]+)", re.M)
_BUILD_JOB = re.compile(r"build \((?P<target>[^)]+)\)")


# --- pure logic -------------------------------------------------------------

def archive_platform(asset_name: str, tag: str) -> Optional[str]:
    """"tokitty-v0.6.2-linux-x86_64.tar.gz" -> "linux-x86_64". None for
    anything that is not one of this release's platform archives."""
    match = _ARCHIVE.fullmatch(asset_name)
    if not match or match["tag"] != tag:
        return None
    return match["platform"]


def raw_counts(release: dict) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for asset in release.get("assets", []):
        platform = archive_platform(asset["name"], release["tag_name"])
        if platform:
            counts[platform] = counts.get(platform, 0) + asset["download_count"]
    return counts


def legacy_tag(workflow_text: str) -> Optional[str]:
    match = _LEGACY_TAG.search(workflow_text)
    return match[1] if match else None


def _step(job: dict, name: str) -> Optional[dict]:
    return next((s for s in job.get("steps") or [] if s.get("name") == name), None)


def job_target(job: dict) -> Optional[str]:
    """The target whose legacy archive this job may have downloaded, or
    None if the job never ran the step that downloads it."""
    match = _BUILD_JOB.fullmatch(job.get("name", ""))
    verify = _step(job, VERIFY_STEP)
    if not match or verify is None or verify.get("conclusion") in (None, "skipped"):
        return None
    return match["target"]


def cache_restored(log: Optional[str]) -> Optional[bool]:
    """True on a cache hit, False on a miss, None if the log does not say."""
    if log is None:
        return None
    if re.search(r"Cache restored from key: legacy-", log):
        return True
    if re.search(r"Cache not found for input keys: legacy-", log):
        return False
    return None


def ci_downloads(
    runs: Iterable[dict],
    jobs_of: Callable[[dict], List[dict]],
    tag_of: Callable[[dict], Optional[str]],
    log_of: Callable[[dict], Optional[str]],
    warn: Callable[[str], None] = lambda msg: None,
) -> Counts:
    """Count the legacy archive downloads the release workflow made."""
    out: Counts = {}
    seen = set()
    for run in runs:
        jobs = []
        for job in jobs_of(run):
            target = job_target(job)
            if not target:
                continue
            # Re-running failed jobs lists the jobs that had passed again
            # under the new attempt, with the original timings: one download.
            key = (run["id"], target, _step(job, VERIFY_STEP).get("started_at"))
            if key not in seen:
                seen.add(key)
                jobs.append((job, target))
        if not jobs:
            continue
        tag = tag_of(run)
        if tag is None:
            warn(f"run {run['id']}: no LEGACY_TAG in {WORKFLOW} at {run['head_sha'][:7]}; skipped")
            continue
        for job, target in jobs:
            if _step(job, CACHE_STEP) is not None:
                hit = cache_restored(log_of(job))
                if hit:
                    continue
                if hit is None:
                    warn(f"job {job['id']} ({target}): log does not say whether the cache hit; counted as a miss")
            per_tag = out.setdefault(tag, {})
            per_tag[target] = per_tag.get(target, 0) + 1
    return out


def load_own(text: str) -> Counts:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("own downloads must be an object of tag -> platform -> count")
    for tag, platforms in data.items():
        if not isinstance(platforms, dict) or not all(
            isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in platforms.values()
        ):
            raise ValueError(f"{tag}: expected platform -> non-negative whole number")
    return data


def tabulate(releases: List[dict], subtract: List[Counts]) -> List[dict]:
    """One row per release, newest first, as GitHub lists them."""
    rows = []
    for release in releases:
        tag = release["tag_name"]
        raw = raw_counts(release)
        taken: Dict[str, int] = {}
        for counts in subtract:
            for platform, n in counts.get(tag, {}).items():
                taken[platform] = taken.get(platform, 0) + n
        net = {p: raw.get(p, 0) - taken.get(p, 0) for p in set(raw) | set(taken)}
        rows.append({
            "tag": tag,
            "date": (release.get("published_at") or "")[:10],
            "raw": raw,
            "net": net,
            "raw_total": sum(raw.values()),
            "subtracted": sum(taken.values()),
            "net_total": sum(net.values()),
        })
    return rows


def render(rows: List[dict]) -> str:
    platforms = sorted({p for row in rows for p in row["raw"]})
    header = ["tag", "date", *platforms, "raw", "subtracted", "net"]
    body = [
        [row["tag"], row["date"], *(str(row["net"].get(p, 0)) for p in platforms),
         str(row["raw_total"]), str(row["subtracted"]), str(row["net_total"])]
        for row in rows
    ]
    total = sum(row["net_total"] for row in rows)
    footer = ["total", "", *(str(sum(r["net"].get(p, 0) for r in rows)) for p in platforms),
              str(sum(r["raw_total"] for r in rows)), str(sum(r["subtracted"] for r in rows)), str(total)]
    table = [header, *body, footer]
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]

    def line(cells):
        return "  ".join(c.ljust(w) if i < 2 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths))).rstrip()

    rule = "  ".join("-" * w for w in widths)
    out = [line(header), rule, *(line(r) for r in body), rule, line(footer), ""]
    out.append("Platform columns are net. Net downloads across all releases: " + str(total))
    return "\n".join(out)


# --- GitHub -----------------------------------------------------------------

def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def gh_items(path: str, jq: str) -> List[dict]:
    """Every item of a paginated list endpoint, one JSON object per line."""
    out = gh("api", "--paginate", path, "--jq", jq)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=REPO, help=f"owner/name (default {REPO})")
    parser.add_argument("--own", type=Path, default=OWN_FILE, help="tag -> platform -> count of your own downloads")
    args = parser.parse_args(argv)

    def warn(msg: str) -> None:
        print(f"warning: {msg}", file=sys.stderr)

    own = load_own(args.own.read_text(encoding="utf-8")) if args.own.exists() else {}
    releases = [r for r in gh_items(f"repos/{args.repo}/releases?per_page=100", ".[]") if not r.get("draft")]
    runs = gh_items(f"repos/{args.repo}/actions/workflows/release.yml/runs?per_page=100", ".workflow_runs[]")

    tags: Dict[str, Optional[str]] = {}

    def tag_of(run: dict) -> Optional[str]:
        sha = run["head_sha"]
        if sha not in tags:
            try:
                text = gh("api", f"repos/{args.repo}/contents/{WORKFLOW}?ref={sha}", "-H", "Accept: application/vnd.github.raw")
            except subprocess.CalledProcessError:
                text = ""
            tags[sha] = legacy_tag(text)
        return tags[sha]

    def jobs_of(run: dict) -> List[dict]:
        # filter=all: a re-run attempt downloads again.
        return gh_items(f"repos/{args.repo}/actions/runs/{run['id']}/jobs?per_page=100&filter=all", ".jobs[]")

    token = gh("auth", "token").strip()

    def log_of(job: dict) -> Optional[str]:
        # Not through gh: `gh api` refuses a log's escape sequences and
        # `gh run view --job` downloads the whole run's logs.
        request = urllib.request.Request(f"https://api.github.com/repos/{args.repo}/actions/jobs/{job['id']}/logs")
        # Unredirected: the log itself is served from signed storage URLs
        # that must not receive the token.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError:
            return None

    ci = ci_downloads(runs, jobs_of, tag_of, log_of, warn)
    rows = tabulate(releases, [ci, own])
    for row in rows:
        for platform, n in sorted(row["net"].items()):
            if n < 0:
                warn(f"{row['tag']} {platform}: net {n}; more subtracted than downloaded")
    print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
