import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from download_counts import (  # noqa: E402
    CACHE_STEP,
    OWN_FILE,
    VERIFY_STEP,
    archive_platform,
    cache_restored,
    ci_downloads,
    legacy_tag,
    load_own,
    raw_counts,
    render,
    tabulate,
)


def release(tag, counts, sums=0, published="2026-10-12T15:53:55Z"):
    assets = [{"name": "SHA256SUMS", "download_count": sums}]
    for platform, n in counts.items():
        ext = "tar.gz" if platform.startswith("linux") else "zip"
        assets.append({"name": f"tokitty-{tag}-{platform}.{ext}", "download_count": n})
    return {"tag_name": tag, "published_at": published, "assets": assets}


def job(job_id, target, verify="success", cache=False, started="2026-10-14T03:30:00Z"):
    steps = [{"name": "Set up job", "conclusion": "success"}]
    if cache:
        steps.append({"name": CACHE_STEP, "conclusion": "success"})
    steps.append({"name": VERIFY_STEP, "conclusion": verify, "started_at": None if verify == "skipped" else started})
    return {"id": job_id, "name": f"build ({target})", "steps": steps}


HIT = "Restore the shipped release\tCache restored from key: legacy-v0.6.2-linux-x86_64\n"
MISS = "Restore the shipped release\tCache not found for input keys: legacy-v0.6.2-linux-x86_64\n"


def count(runs, jobs, logs=None, tag="v0.6.2"):
    warnings = []
    result = ci_downloads(
        runs,
        jobs_of=lambda run: jobs[run["id"]],
        tag_of=lambda run: tag,
        log_of=lambda j: (logs or {}).get(j["id"]),
        warn=warnings.append,
    )
    return result, warnings


def run(run_id):
    return {"id": run_id, "head_sha": "0123456789abcdef"}


def test_archive_platform():
    assert archive_platform("tokitty-v0.6.2-linux-x86_64.tar.gz", "v0.6.2") == "linux-x86_64"
    assert archive_platform("tokitty-v0.6.2-macos-arm64.zip", "v0.6.2") == "macos-arm64"
    assert archive_platform("SHA256SUMS", "v0.6.2") is None
    assert archive_platform("tokitty-v0.6.1-windows-x64.zip", "v0.6.2") is None
    assert archive_platform("tokitty-v0.6.2-windows-x64.zip.sha256", "v0.6.2") is None


def test_raw_counts_leaves_out_checksums():
    assert raw_counts(release("v0.7.0", {"windows-x64": 1, "linux-x86_64": 0}, sums=5)) == {"windows-x64": 1, "linux-x86_64": 0}


def test_legacy_tag_from_workflow():
    assert legacy_tag("    env:\n      LEGACY_TAG: v0.6.2\n") == "v0.6.2"
    assert legacy_tag("env:\n  LEGACY_TAG: 'v1.2.3'\n") == "v1.2.3"
    assert legacy_tag("name: Release\n") is None


def test_cache_restored():
    assert cache_restored(HIT) is True
    assert cache_restored(MISS) is False
    assert cache_restored("nothing about caches") is None
    assert cache_restored(None) is None


def test_every_verify_step_before_the_cache_is_a_download():
    jobs = {1: [job(11, "linux-x86_64"), job(12, "windows-x64")], 2: [job(21, "linux-x86_64")]}
    result, warnings = count([run(1), run(2)], jobs)
    assert result == {"v0.6.2": {"linux-x86_64": 2, "windows-x64": 1}}
    assert warnings == []


def test_skipped_verify_step_and_other_jobs_are_not_downloads():
    jobs = {1: [
        job(11, "linux-x86_64", verify="skipped"),
        {"id": 12, "name": "Publish draft release", "steps": []},
        {"id": 13, "name": "build (macos-arm64)", "steps": [{"name": "Build", "conclusion": "success"}]},
    ]}
    assert count([run(1)], jobs)[0] == {}


def test_failed_verify_step_still_downloaded():
    jobs = {1: [job(11, "macos-x86_64", verify="failure")]}
    assert count([run(1)], jobs)[0] == {"v0.6.2": {"macos-x86_64": 1}}


def test_cache_hit_is_not_a_download_and_a_miss_is():
    jobs = {1: [job(11, "linux-x86_64", cache=True)], 2: [job(21, "linux-x86_64", cache=True)]}
    result, warnings = count([run(1), run(2)], jobs, logs={11: MISS, 21: HIT})
    assert result == {"v0.6.2": {"linux-x86_64": 1}}
    assert warnings == []


def test_missing_log_counts_as_a_miss_with_a_warning():
    jobs = {1: [job(11, "linux-x86_64", cache=True)]}
    result, warnings = count([run(1)], jobs, logs={})
    assert result == {"v0.6.2": {"linux-x86_64": 1}}
    assert len(warnings) == 1 and "11" in warnings[0]


def test_rerun_attempt_counts_carried_over_jobs_once():
    # Attempt 2 re-ran only windows; linux is listed again with its old timings.
    jobs = {1: [
        job(11, "linux-x86_64", started="2026-10-15T15:37:40Z"),
        job(12, "windows-x64", verify="skipped"),
        job(21, "linux-x86_64", started="2026-10-15T15:37:40Z"),
        job(22, "windows-x64", started="2026-10-15T15:49:13Z"),
    ]}
    assert count([run(1)], jobs)[0] == {"v0.6.2": {"linux-x86_64": 1, "windows-x64": 1}}


def test_rerun_that_ran_the_step_again_counts_twice():
    jobs = {1: [
        job(11, "linux-x86_64", verify="failure", started="2026-10-15T15:37:40Z"),
        job(21, "linux-x86_64", started="2026-10-15T15:52:00Z"),
    ]}
    assert count([run(1)], jobs)[0] == {"v0.6.2": {"linux-x86_64": 2}}


def test_run_without_legacy_tag_is_skipped_with_a_warning():
    jobs = {1: [job(11, "linux-x86_64")]}
    result, warnings = count([run(1)], jobs, tag=None)
    assert result == {} and len(warnings) == 1


def test_tabulate_subtracts_ci_and_own_per_platform():
    releases = [
        release("v0.7.0", {"linux-x86_64": 0, "windows-x64": 1}, published="2026-10-15T22:22:38Z"),
        release("v0.6.2", {"linux-x86_64": 15, "windows-x64": 16}),
    ]
    ci = {"v0.6.2": {"linux-x86_64": 12, "windows-x64": 14}}
    own = {"v0.7.0": {"windows-x64": 1}, "v0.6.2": {"windows-x64": 1}}
    rows = tabulate(releases, [ci, own])
    assert rows[0] == {
        "tag": "v0.7.0", "date": "2026-10-15",
        "raw": {"linux-x86_64": 0, "windows-x64": 1}, "net": {"linux-x86_64": 0, "windows-x64": 0},
        "raw_total": 1, "subtracted": 1, "net_total": 0,
    }
    assert rows[1]["net"] == {"linux-x86_64": 3, "windows-x64": 1}
    assert (rows[1]["raw_total"], rows[1]["subtracted"], rows[1]["net_total"]) == (31, 27, 4)


def test_tabulate_keeps_an_oversubtraction_visible():
    rows = tabulate([release("v0.6.2", {"macos-arm64": 2})], [{"v0.6.2": {"macos-arm64": 3}}])
    assert rows[0]["net"] == {"macos-arm64": -1}
    assert rows[0]["net_total"] == -1


def test_tabulate_ignores_subtractions_for_other_tags():
    rows = tabulate([release("v0.8.0", {"windows-x64": 2})], [{"v0.6.2": {"windows-x64": 9}}])
    assert rows[0]["net_total"] == 2 and rows[0]["subtracted"] == 0


def test_render_has_a_row_per_release_and_the_grand_total():
    rows = tabulate(
        [release("v0.7.0", {"windows-x64": 3}), release("v0.6.2", {"windows-x64": 16, "linux-x86_64": 15})],
        [{"v0.6.2": {"windows-x64": 14, "linux-x86_64": 12}}],
    )
    out = render(rows)
    lines = out.splitlines()
    assert lines[0].split() == ["tag", "date", "linux-x86_64", "windows-x64", "raw", "subtracted", "net"]
    assert lines[2].split()[0] == "v0.7.0" and lines[2].split()[-1] == "3"
    assert lines[3].split()[-3:] == ["31", "26", "5"]
    assert lines[5].split() == ["total", "3", "5", "34", "26", "8"]
    assert out.endswith("Net downloads across all releases: 8")


def test_load_own_validates():
    assert load_own('{"v0.7.0": {"windows-x64": 1}}') == {"v0.7.0": {"windows-x64": 1}}
    for bad in ('[]', '{"v0.7.0": 1}', '{"v0.7.0": {"windows-x64": -1}}', '{"v0.7.0": {"windows-x64": true}}'):
        with pytest.raises(ValueError):
            load_own(bad)


def test_committed_own_file_loads():
    own = load_own(OWN_FILE.read_text(encoding="utf-8"))
    assert all(tag.startswith("v") for tag in own)
    assert json.loads(OWN_FILE.read_text(encoding="utf-8")) == own
