"""Scheduler and native-command contract tests for PF-100."""

from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import textwrap

import pytest

from radiant_fulcrum_search import search


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """A fake native CLI that exercises the real subprocess scheduler."""
    events = tmp_path / "events.jsonl"
    executable = tmp_path / "fake-radiant"
    executable.write_text("#!" + sys.executable + "\n" + textwrap.dedent(r"""
            import json
            import os
            from pathlib import Path
            import re
            import sys
            import time

            event_path = Path(os.environ["PF100_EVENT_LOG"])

            def event(kind, **values):
                values.update(kind=kind, pid=os.getpid(),
                              time=time.monotonic())
                line = (json.dumps(values) + "\n").encode()
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
                fd = os.open(event_path, flags, 0o600)
                try:
                    os.write(fd, line)
                finally:
                    os.close(fd)

            args = sys.argv[1:]
            config = Path(args[2])
            output_index = args.index("--output-folder")
            inputs = args[3:output_index]
            output = Path(args[output_index + 1])
            output.mkdir(parents=True, exist_ok=True)
            match = re.search(r"(?m)^\s*threadCount\s*=\s*(\d+)",
                              config.read_text())
            threads = int(match.group(1)) if match else None
            thread_environment = {
                name: os.environ.get(name)
                for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                             "OPENBLAS_NUM_THREADS")
            }
            event("process_start", inputs=inputs, threads=threads,
                  config=config.read_text(),
                  thread_environment=thread_environment,
                  affinity=sorted(os.sched_getaffinity(0)))

            # Test-only barrier proves four searches actually overlap. This
            # CLI performs no scientific computation or CPU-bound work.
            target = int(os.environ.get("PF100_START_BARRIER", "0"))
            deadline = time.monotonic() + 10
            while target and time.monotonic() < deadline:
                started = sum(
                    json.loads(line)["kind"] == "process_start"
                    for line in event_path.read_text().splitlines()
                )
                if started >= target:
                    break
                time.sleep(0.01)

            failed = False
            for name in inputs:
                path = Path(name)
                event("file_start", input=name)
                time.sleep(0.03)
                if path.stem.startswith("crash"):
                    event("crash", input=name)
                    os._exit(7)
                if path.stem.startswith("fail"):
                    print("FAILED:", name, "- synthetic recoverable error",
                          flush=True)
                    failed = True
                else:
                    if not path.stem.startswith("empty"):
                        (output / (path.name + ".radiantDIA")).write_text(
                            "output for " + name
                        )
                    if not path.stem.startswith("legacy"):
                        print("COMPLETED:", name, flush=True)
                event("file_end", input=name)
            event("process_end")
            sys.exit(1 if failed else 0)
            """))
    executable.chmod(0o700)
    config = tmp_path / "search.radiantConfig"
    config.write_text(
        "[General]\nthreadCount = 1\nwriteRadiantDIA = true\n"
        "[LibraryParams]\nchargeStateMax = 4\n"
    )
    library = tmp_path / "library.tsv"
    library.write_text("library fixture")
    fasta = tmp_path / "proteins.fasta"
    fasta.write_text(">fixture\nPEPTIDE\n")
    output = tmp_path / "outputs"
    reader_calls = []
    reader_result = object()

    def reader(**kwargs):
        reader_calls.append(kwargs)
        return reader_result

    monkeypatch.setenv("PF100_EVENT_LOG", str(events))
    monkeypatch.setattr(search, "_read_radiant_features", reader)
    monkeypatch.setattr(search, "_available_cpus", lambda: 16)
    native_allocations = search._cpu_allocations
    physical_cpus = sorted(os.sched_getaffinity(0))

    def model_allocations(processes, threads, cpu_budget=None):
        # The fake CLI only sleeps. When a modelled resource configuration
        # exceeds a small CI runner, use existing CPU IDs; real disjoint CPU
        # enforcement is tested independently below.
        if processes * threads <= len(physical_cpus):
            return native_allocations(
                processes, threads, cpu_budget=cpu_budget
            )
        allocation = physical_cpus[:threads]
        return [allocation[:] for _ in range(processes)]

    monkeypatch.setattr(search, "_cpu_allocations", model_allocations)

    class Batch:
        def inputs(self, count=None, names=None):
            if names is None:
                names = ["input-%02d.mzML" % index for index in range(count)]
            paths = [tmp_path / name for name in names]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic input")
            return paths

        def run(self, locations, **kwargs):
            params = {
                "library": library,
                "fasta": fasta,
                "config": config,
                "executable": executable,
                "output_location": output,
            }
            params.update(kwargs)
            return search.run_radiant_search(locations, **params)

        def report(self, path=None):
            path = path or output / "radiant-batch-status.json"
            return json.loads(path.read_text())

        def events(self):
            if not events.exists():
                return []
            return [
                json.loads(line) for line in events.read_text().splitlines()
            ]

    batch = Batch()
    batch.config = config
    batch.library = library
    batch.fasta = fasta
    batch.output = output
    batch.executable = executable
    batch.reader_calls = reader_calls
    batch.reader_result = reader_result
    batch.native_allocations = native_allocations
    batch.physical_cpus = physical_cpus
    return batch


def _started_files(events):
    return [e["input"] for e in events if e["kind"] == "file_start"]


def _processes(events):
    return [e for e in events if e["kind"] == "process_start"]


def _peak_searches(events):
    active = 0
    peak = 0
    for event in sorted(events, key=lambda e: e["time"]):
        if event["kind"] == "file_start":
            active += 1
            peak = max(peak, active)
        elif event["kind"] == "file_end":
            active -= 1
    return peak


def _peak_processes(events):
    active = 0
    peak = 0
    for event in sorted(events, key=lambda e: e["time"]):
        if event["kind"] == "process_start":
            active += 1
            peak = max(peak, active)
        elif event["kind"] == "process_end":
            active -= 1
    return peak


def _reader_outputs(batch):
    return [Path(p) for p in batch.reader_calls[0]["location"]]


def test_single_file_preserves_reader_contract_and_legacy_cli(batch):
    inputs = batch.inputs(names=["legacy.mzML"])
    result = batch.run(inputs[0], include_decoys=True, score="fixture")

    assert result is batch.reader_result
    assert _reader_outputs(batch) == [batch.output / "legacy.mzML.radiantDIA"]
    assert batch.reader_calls[0]["include_decoys"] is True
    assert batch.reader_calls[0]["score"] == "fixture"
    assert _started_files(batch.events()) == [str(inputs[0])]
    assert batch.report()["files"][0]["status"] == "completed"
    assert batch.report()["processes"][0]["returncode"] == 0


def test_16_files_32_cores_four_sequential_files_per_process_simulation(
    batch, monkeypatch
):
    """Model 32 CPUs using idle fake processes on existing host CPU IDs."""
    inputs = batch.inputs(16)
    monkeypatch.setattr(search, "_available_cpus", lambda: 32)
    # This host does not have 32 CPUs. Overlapping existing CPU sets exercise
    # the scheduler only; this test claims no real 32-core affinity result.
    monkeypatch.setattr(
        search,
        "_cpu_allocations",
        lambda processes, threads, cpu_budget=None: [
            batch.physical_cpus[:threads] for _ in range(processes)
        ],
    )
    monkeypatch.setenv("PF100_START_BARRIER", "4")
    batch.run(
        inputs,
        mode="parallel",
        concurrent_processes=4,
        files_per_process=4,
        threads_per_process=8,
        cpu_budget=32,
    )

    events = batch.events()
    processes = _processes(events)
    assert len(processes) == 4
    assert [e["threads"] for e in processes] == [8, 8, 8, 8]
    assert all(len(e["inputs"]) == 4 for e in processes)
    assert Counter(_started_files(events)) == Counter(map(str, inputs))
    assert _peak_searches(events) == 4
    assert _peak_processes(events) == 4
    assert _peak_searches(events) * 8 == 32
    assert all(
        process["thread_environment"]
        == {
            "OMP_NUM_THREADS": "8",
            "MKL_NUM_THREADS": "8",
            "OPENBLAS_NUM_THREADS": "8",
        }
        for process in processes
    )
    for process in processes:
        file_events = [
            e
            for e in events
            if e["pid"] == process["pid"]
            and e["kind"] in ("file_start", "file_end")
        ]
        assert [e["kind"] for e in file_events] == [
            "file_start",
            "file_end",
        ] * 4
        assert [
            e["input"] for e in file_events if e["kind"] == "file_start"
        ] == process["inputs"]
    report = batch.report()
    assert len(report["files"]) == 16
    assert len(report["processes"]) == 4
    assert all(f["status"] == "completed" for f in report["files"])
    assert _reader_outputs(batch) == [
        batch.output / (path.name + ".radiantDIA") for path in inputs
    ]


def test_more_than_16_files_and_final_partial_process(batch):
    inputs = batch.inputs(19)
    batch.run(
        inputs,
        mode="parallel",
        concurrent_processes=4,
        files_per_process=4,
        threads_per_process=1,
        cpu_budget=4,
    )

    processes = _processes(batch.events())
    assert sorted(len(p["inputs"]) for p in processes) == [3, 4, 4, 4, 4]
    assert Counter(_started_files(batch.events())) == Counter(map(str, inputs))
    assert _peak_searches(batch.events()) <= 4
    assert _peak_processes(batch.events()) <= 4
    assert len(_reader_outputs(batch)) == 19


def test_serial_process_batches_never_overlap(batch):
    inputs = batch.inputs(5)
    batch.run(inputs, files_per_process=2)

    assert [len(p["inputs"]) for p in _processes(batch.events())] == [
        2,
        2,
        1,
    ]
    assert _peak_searches(batch.events()) == 1
    assert _started_files(batch.events()) == list(map(str, inputs))


@pytest.mark.parametrize(
    "setting,value",
    [
        ("concurrent_processes", 0),
        ("concurrent_processes", -1),
        ("concurrent_processes", 1.5),
        ("concurrent_processes", True),
        ("files_per_process", 0),
        ("files_per_process", -1),
        ("files_per_process", 1.5),
        ("files_per_process", True),
        ("threads_per_process", 0),
        ("threads_per_process", -1),
        ("threads_per_process", 1.5),
        ("threads_per_process", True),
        ("cpu_budget", 0),
        ("cpu_budget", -1),
        ("cpu_budget", 1.5),
        ("cpu_budget", True),
    ],
)
def test_invalid_resource_settings_are_actionable(batch, setting, value):
    with pytest.raises(ValueError, match=setting):
        batch.run(batch.inputs(1), **{setting: value})
    assert batch.events() == []
    assert batch.reader_calls == []


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"mode": "invalid"}, "mode"),
        ({"mode": "serial", "concurrent_processes": 2}, "serial"),
        (
            {
                "mode": "parallel",
                "concurrent_processes": 4,
                "threads_per_process": 5,
                "cpu_budget": 16,
            },
            "CPU|cpu|threads",
        ),
        (
            {
                "mode": "parallel",
                "concurrent_processes": 4,
                "cpu_budget": 3,
            },
            "CPU|cpu|process",
        ),
        ({"cpu_budget": 32}, "available|CPU|cpu"),
    ],
)
def test_oversubscription_and_invalid_modes_rejected(batch, kwargs, match):
    with pytest.raises(ValueError, match=match):
        batch.run(batch.inputs(1), **kwargs)
    assert batch.events() == []


@pytest.mark.parametrize("thread_line", ["threadCount = 0\n", ""])
def test_auto_threads_share_cpu_budget(batch, thread_line):
    batch.config.write_text("[General]\n" + thread_line)
    batch.run(
        batch.inputs(4),
        mode="parallel",
        concurrent_processes=4,
        cpu_budget=16,
    )

    assert [p["threads"] for p in _processes(batch.events())] == [4] * 4
    assert batch.config.read_text() == "[General]\n" + thread_line


def test_configured_threads_are_used_and_validated(batch):
    batch.config.write_text("[General]\nthreadCount = 3\n")
    batch.run(
        batch.inputs(4),
        mode="parallel",
        concurrent_processes=4,
        cpu_budget=16,
    )
    assert [p["threads"] for p in _processes(batch.events())] == [3] * 4


@pytest.mark.parametrize("thread_count", ["-1", "true", "1.5", '"eight"'])
def test_invalid_config_thread_count_is_actionable(batch, thread_count):
    batch.config.write_text("[General]\nthreadCount = %s\n" % thread_count)
    with pytest.raises(ValueError, match="threadCount|threads"):
        batch.run(batch.inputs(1))
    assert batch.events() == []


def test_explicit_threads_override_config_without_mutating_source(batch):
    original = (
        "[General]\nthreadCount = 99\nwriteRadiantDIA = true\n"
        "[LibraryParams]\nchargeStateMax = 4\n"
    )
    batch.config.write_text(original)
    batch.run(batch.inputs(2), files_per_process=2, threads_per_process=2)

    processes = _processes(batch.events())
    assert processes[0]["threads"] == 2
    assert "chargeStateMax" in processes[0]["config"]
    assert "writeRadiantDIA" in processes[0]["config"]
    assert batch.config.read_text() == original


def test_partial_native_failure_preserves_later_successes(batch):
    inputs = batch.inputs(names=["first.mzML", "fail.mzML", "last.mzML"])
    batch.run(inputs, files_per_process=3)

    report = batch.report()
    assert [f["status"] for f in report["files"]] == [
        "completed",
        "failed",
        "completed",
    ]
    assert report["files"][1]["error"]
    assert report["processes"][0]["status"] == "failed"
    assert report["processes"][0]["returncode"] == 1
    assert _reader_outputs(batch) == [
        batch.output / "first.mzML.radiantDIA",
        batch.output / "last.mzML.radiantDIA",
    ]
    assert _started_files(batch.events()) == list(map(str, inputs))


def test_fail_on_error_keeps_report_and_outputs_after_all_batches(batch):
    inputs = batch.inputs(names=["fail.mzML", "good.mzML", "later.mzML"])
    with pytest.raises(search.RadiantBatchError) as exc_info:
        batch.run(inputs, fail_on_error=True)

    error = exc_info.value
    assert [Path(p) for p in error.outputs] == [
        batch.output / "good.mzML.radiantDIA",
        batch.output / "later.mzML.radiantDIA",
    ]
    assert error.report == batch.report()
    assert Path(error.status_path).is_file()
    assert _started_files(batch.events()) == list(map(str, inputs))
    assert batch.reader_calls == []


def test_process_crash_preserves_completed_files_and_marks_unprocessed(batch):
    inputs = batch.inputs(
        names=["good.mzML", "crash.mzML", "unprocessed.mzML"]
    )
    batch.run(inputs, files_per_process=3)

    report = batch.report()
    assert [f["status"] for f in report["files"]] == [
        "completed",
        "failed",
        "failed",
    ]
    assert report["processes"][0]["returncode"] == 7
    assert report["files"][1]["error"]
    assert report["files"][2]["error"]
    assert _reader_outputs(batch) == [batch.output / "good.mzML.radiantDIA"]


def test_completed_file_without_output_is_distinct_from_failure(batch):
    inputs = batch.inputs(names=["good.mzML", "empty.mzML"])
    batch.run(inputs, files_per_process=2)

    assert [f["status"] for f in batch.report()["files"]] == [
        "completed",
        "completed_no_output",
    ]
    assert _reader_outputs(batch) == [batch.output / "good.mzML.radiantDIA"]


@pytest.mark.parametrize("name", ["fail.mzML", "empty.mzML"])
def test_no_usable_outputs_raises_with_persisted_status(batch, name):
    with pytest.raises(search.RadiantBatchError) as exc_info:
        batch.run(batch.inputs(names=[name]))

    assert exc_info.value.outputs == []
    assert exc_info.value.report == batch.report()
    assert Path(exc_info.value.status_path).is_file()
    assert batch.reader_calls == []


def test_subprocess_launch_failure_produces_actionable_file_status(batch):
    with pytest.raises(search.RadiantBatchError) as exc_info:
        batch.run(
            batch.inputs(2),
            executable=batch.executable.parent / "missing-executable",
            files_per_process=2,
        )

    assert all(f["status"] == "failed" for f in batch.report()["files"])
    assert all(f["error"] for f in batch.report()["files"])
    assert exc_info.value.outputs == []
    assert batch.reader_calls == []


def test_stale_existing_output_does_not_make_failed_search_successful(batch):
    inputs = batch.inputs(names=["fail.mzML"])
    batch.output.mkdir()
    stale = batch.output / "fail.mzML.radiantDIA"
    stale.write_text("stale scientific output")
    with pytest.raises(search.RadiantBatchError):
        batch.run(inputs, reuse_existing=False)

    assert batch.report()["files"][0]["status"] == "failed"
    assert stale.read_text() == "stale scientific output"
    assert batch.reader_calls == []


def test_partial_reuse_schedules_only_missing_inputs_in_input_order(batch):
    inputs = batch.inputs(3)
    batch.output.mkdir()
    reused = batch.output / (inputs[1].name + ".radiantDIA")
    reused.write_text("reused output")
    batch.run(inputs, reuse_existing=True, files_per_process=2)

    assert _started_files(batch.events()) == [
        str(inputs[0]),
        str(inputs[2]),
    ]
    assert [f["status"] for f in batch.report()["files"]] == [
        "completed",
        "reused",
        "completed",
    ]
    assert _reader_outputs(batch) == [
        batch.output / (p.name + ".radiantDIA") for p in inputs
    ]
    assert reused.read_text() == "reused output"


def test_all_reused_results_need_no_native_process(batch):
    inputs = batch.inputs(2)
    batch.output.mkdir()
    for path in inputs:
        (batch.output / (path.name + ".radiantDIA")).write_text("existing")
    batch.run(
        inputs,
        reuse_existing=True,
        executable=batch.executable.parent / "missing-executable",
    )

    assert batch.events() == []
    assert batch.report()["processes"] == []
    assert all(f["status"] == "reused" for f in batch.report()["files"])
    assert len(_reader_outputs(batch)) == 2


def test_duplicate_canonical_inputs_are_rejected_before_launch(batch):
    inputs = batch.inputs(1)
    alternate = inputs[0].parent / "nested" / ".." / inputs[0].name
    (inputs[0].parent / "nested").mkdir()
    with pytest.raises(ValueError, match="duplicate|Duplicate"):
        batch.run([inputs[0], alternate])
    assert batch.events() == []


def test_colliding_output_names_are_rejected_before_launch(batch):
    inputs = batch.inputs(names=["one/sample.mzML", "two/sample.mzML"])
    with pytest.raises(ValueError, match="(?i)collid|collision|same"):
        batch.run(inputs)
    assert batch.events() == []


def test_custom_status_path_and_report_retain_process_details(batch, tmp_path):
    inputs = batch.inputs(2)
    status_path = tmp_path / "metadata" / "search-status.json"
    batch.run(inputs, files_per_process=2, status_path=status_path)

    report = batch.report(status_path)
    process = report["processes"][0]
    assert process["inputs"] == list(map(str, inputs))
    assert process["returncode"] == 0
    assert process["elapsed_seconds"] >= 0
    assert Path(process["log"]).is_file()
    assert process["command"][0] == str(batch.executable)
    assert re.search(
        r"COMPLETED:.*input-00.mzML", Path(process["log"]).read_text()
    )
    assert all(f["process_id"] == process["id"] for f in report["files"])
    assert not (batch.output / "radiant-batch-status.json").exists()


def test_dict_config_is_copied_and_overridden_without_mutation(batch):
    config = {
        "General": {"threadCount": 8, "writeRadiantDIA": True},
        "LibraryParams": {"chargeStateMax": 4},
    }
    batch.run(batch.inputs(1), config=config, threads_per_process=2)

    assert config == {
        "General": {"threadCount": 8, "writeRadiantDIA": True},
        "LibraryParams": {"chargeStateMax": 4},
    }
    assert _processes(batch.events())[0]["threads"] == 2


@pytest.mark.parametrize("contents", ["[General", "General = true\n"])
def test_malformed_config_has_actionable_error(batch, contents):
    batch.config.write_text(contents)
    with pytest.raises(ValueError, match="config|General"):
        batch.run(batch.inputs(1))
    assert batch.events() == []


def test_empty_inputs_are_rejected_without_reader_or_native_invocation(batch):
    with pytest.raises(ValueError, match="location|input"):
        batch.run([])
    assert batch.events() == []
    assert batch.reader_calls == []


def test_same_filenames_in_distinct_input_folders_keep_separate_destinations(
    batch,
):
    inputs = batch.inputs(names=["one/sample.mzML", "two/sample.mzML"])
    batch.run(inputs, files_per_process=2, output_location=None)

    report = batch.report(inputs[0].parent / "radiant-batch-status.json")
    assert len(report["processes"]) == 2
    assert _started_files(batch.events()) == list(map(str, inputs))
    assert _reader_outputs(batch) == [
        path.parent / (path.name + ".radiantDIA") for path in inputs
    ]


def test_multi_file_cli_without_pf99_markers_cannot_claim_success(batch):
    inputs = batch.inputs(names=["legacy-first.mzML", "legacy-last.mzML"])
    with pytest.raises(search.RadiantBatchError) as exc_info:
        batch.run(inputs, files_per_process=2)

    assert exc_info.value.outputs == []
    assert all(f["status"] == "failed" for f in batch.report()["files"])
    assert all("PF-99" in f["error"] for f in batch.report()["files"])
    assert batch.reader_calls == []


@pytest.mark.parametrize("destination", ["input", "result"])
def test_status_manifest_must_not_overwrite_input_or_result(
    batch, destination
):
    inputs = batch.inputs(1)
    if destination == "input":
        status_path = inputs[0]
    else:
        status_path = batch.output / (inputs[0].name + ".radiantDIA")
    with pytest.raises(ValueError, match="status_path"):
        batch.run(inputs, status_path=status_path)
    assert inputs[0].read_text() == "synthetic input"
    assert batch.events() == []


@pytest.fixture
def cpu_filesystem(tmp_path, monkeypatch):
    """Map only CPU-detection Linux files into a deterministic filesystem."""

    def mapped_path(value):
        path = str(value)
        if path.startswith("/sys/fs/cgroup") or path == "/proc/self/cgroup":
            return tmp_path / path.lstrip("/")
        return Path(value)

    monkeypatch.setattr(search, "_Path", mapped_path)
    monkeypatch.setattr(search._os, "cpu_count", lambda: 64)
    monkeypatch.setattr(
        search._os, "sched_getaffinity", lambda _: set(range(16))
    )
    proc = mapped_path("/proc/self/cgroup")
    proc.parent.mkdir(parents=True)
    proc.write_text("0::/\n")
    root = mapped_path("/sys/fs/cgroup")
    root.mkdir(parents=True)
    return root, proc


def test_cpu_budget_respects_affinity_when_cgroup_has_no_quota(cpu_filesystem):
    root, _ = cpu_filesystem
    (root / "cpu.max").write_text("max 100000\n")
    assert search._available_cpus() == 16


def test_cpu_budget_respects_nested_cgroup_v2_ancestor_quota(cpu_filesystem):
    root, proc = cpu_filesystem
    proc.write_text("0::/service/job\n")
    job = root / "service" / "job"
    job.mkdir(parents=True)
    (root / "cpu.max").write_text("max 100000\n")
    (job / "cpu.max").write_text("800000 100000\n")
    (job.parent / "cpu.max").write_text("200000 100000\n")
    assert search._available_cpus() == 2


@pytest.mark.parametrize("quota,expected", [("350000", 3), ("50000", 0)])
def test_fractional_cpu_quota_uses_conservative_integer_budget(
    cpu_filesystem, quota, expected
):
    root, _ = cpu_filesystem
    (root / "cpu.max").write_text(quota + " 100000\n")
    assert search._available_cpus() == expected


def test_cpu_budget_respects_v1_cgroup_quota(cpu_filesystem):
    root, proc = cpu_filesystem
    proc.write_text("2:cpu,cpuacct:/service\n")
    group = root / "cpu" / "service"
    group.mkdir(parents=True)
    (group / "cpu.cfs_quota_us").write_text("400000\n")
    (group / "cpu.cfs_period_us").write_text("100000\n")
    assert search._available_cpus() == 4


def test_malformed_cgroup_quota_does_not_break_affinity_detection(
    cpu_filesystem,
):
    root, _ = cpu_filesystem
    (root / "cpu.max").write_text("not a quota")
    assert search._available_cpus() == 16


def test_cpu_budget_respects_common_combined_v1_mount(cpu_filesystem):
    root, proc = cpu_filesystem
    proc.write_text("2:cpu,cpuacct:/service/job\n")
    group = root / "cpu,cpuacct" / "service" / "job"
    group.mkdir(parents=True)
    (group / "cpu.cfs_quota_us").write_text("600000\n")
    (group / "cpu.cfs_period_us").write_text("100000\n")
    (group.parent / "cpu.cfs_quota_us").write_text("300000\n")
    (group.parent / "cpu.cfs_period_us").write_text("100000\n")
    assert search._available_cpus() == 3


def test_unavailable_affinity_detection_keeps_cgroup_quota(
    cpu_filesystem, monkeypatch
):
    root, _ = cpu_filesystem
    (root / "cpu.max").write_text("200000 100000\n")

    def unavailable(_):
        raise OSError("affinity denied")

    monkeypatch.setattr(search._os, "sched_getaffinity", unavailable)
    assert search._available_cpus() == 2


def test_less_than_one_whole_cpu_is_rejected_actionably(batch, monkeypatch):
    monkeypatch.setattr(search, "_available_cpus", lambda: 0)
    with pytest.raises(ValueError, match="whole CPU|allocation"):
        batch.run(batch.inputs(1))
    assert batch.events() == []


@pytest.mark.parametrize(
    "unavailable_method", ["sched_getaffinity", "sched_setaffinity"]
)
def test_cpu_allocation_requires_supported_affinity(
    monkeypatch, unavailable_method
):
    monkeypatch.delattr(search._os, unavailable_method)
    with pytest.raises(ValueError, match="Linux|affinity"):
        search._cpu_allocations(1, 1)


def test_cpu_allocation_affinity_error_is_actionable(monkeypatch):
    def unavailable(_):
        raise PermissionError("affinity denied")

    monkeypatch.setattr(search._os, "sched_getaffinity", unavailable)
    with pytest.raises(ValueError, match="affinity.*denied"):
        search._cpu_allocations(1, 1)


def test_cpu_allocation_enforces_count_with_noncontiguous_cpu_ids(monkeypatch):
    monkeypatch.setattr(
        search._os, "sched_getaffinity", lambda _: {2, 5, 7, 11}
    )
    assert search._cpu_allocations(2, 2) == [[2, 5], [7, 11]]
    with pytest.raises(ValueError, match="fewer CPUs|reduce"):
        search._cpu_allocations(3, 2)


def test_native_processes_inherit_actual_disjoint_affinity(batch, monkeypatch):
    if len(batch.physical_cpus) < 2:
        pytest.skip("Two real allocated CPUs are required for this test")
    monkeypatch.setattr(search, "_cpu_allocations", batch.native_allocations)
    monkeypatch.setenv("PF100_START_BARRIER", "2")
    batch.run(
        batch.inputs(2),
        mode="parallel",
        concurrent_processes=2,
        threads_per_process=1,
        cpu_budget=2,
    )

    processes = _processes(batch.events())
    assert _peak_processes(batch.events()) == 2
    affinities = [set(process["affinity"]) for process in processes]
    assert [len(cpus) for cpus in affinities] == [1, 1]
    assert affinities[0].isdisjoint(affinities[1])
    report_affinities = {
        tuple(process["cpu_affinity"])
        for process in batch.report()["processes"]
    }
    assert report_affinities == {
        tuple(process["affinity"]) for process in processes
    }


def test_cpu_slots_are_released_after_partial_failure_for_later_batches(
    batch, monkeypatch
):
    if len(batch.physical_cpus) < 2:
        pytest.skip("Two real allocated CPUs are required for this test")
    monkeypatch.setattr(search, "_cpu_allocations", batch.native_allocations)
    inputs = batch.inputs(
        names=[
            "fail-first.mzML",
            "good-first.mzML",
            "good-later.mzML",
            "good-last.mzML",
        ]
    )
    batch.run(
        inputs,
        mode="parallel",
        concurrent_processes=2,
        threads_per_process=1,
        cpu_budget=2,
    )

    processes = _processes(batch.events())
    assert Counter(_started_files(batch.events())) == Counter(map(str, inputs))
    assert _peak_processes(batch.events()) <= 2
    assert len(processes) == 4
    assert all(len(process["affinity"]) == 1 for process in processes)
    assert len({tuple(p["affinity"]) for p in processes}) == 2
    assert [f["status"] for f in batch.report()["files"]] == [
        "failed",
        "completed",
        "completed",
        "completed",
    ]
    assert len(_reader_outputs(batch)) == 3


@pytest.mark.parametrize(
    "source", ["config", "library", "fasta", "executable"]
)
def test_status_manifest_must_not_overwrite_search_sources(batch, source):
    protected_path = getattr(batch, source)
    original = protected_path.read_bytes()
    with pytest.raises(ValueError, match="status_path"):
        batch.run(batch.inputs(1), status_path=protected_path)
    assert protected_path.read_bytes() == original
    assert batch.events() == []


def test_unreadable_process_log_preserves_other_completed_batch(
    batch, monkeypatch
):
    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if path.name == "radiant.log" and path.parent.name == "process-0001":
            raise PermissionError("synthetic status log permission error")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    batch.run(
        batch.inputs(2),
        mode="parallel",
        concurrent_processes=2,
        threads_per_process=1,
        cpu_budget=2,
    )

    report = batch.report()
    assert [f["status"] for f in report["files"]] == [
        "failed",
        "completed",
    ]
    assert "Cannot read" in report["files"][0]["error"]
    assert report["status"] == "partial_failure"
    assert _reader_outputs(batch) == [
        batch.output / "input-01.mzML.radiantDIA"
    ]


def test_default_single_file_works_without_parent_affinity_support(
    batch, monkeypatch
):
    monkeypatch.delattr(search._os, "sched_setaffinity")
    batch.run(batch.inputs(1))

    report = batch.report()
    assert report["settings"]["cpu_affinity_enforced"] is False
    assert report["processes"][0]["cpu_affinity"] is None
    assert report["files"][0]["status"] == "completed"
    assert _processes(batch.events())[0]["affinity"] == batch.physical_cpus


@pytest.mark.parametrize(
    "settings",
    [
        {"cpu_budget": 2},
        {
            "mode": "parallel",
            "concurrent_processes": 2,
            "cpu_budget": 2,
        },
    ],
)
def test_reduced_or_concurrent_budgets_require_affinity_support(
    batch, monkeypatch, settings
):
    monkeypatch.delattr(search._os, "sched_setaffinity")
    with pytest.raises(ValueError, match="Linux|affinity|reduced"):
        batch.run(batch.inputs(1), **settings)
    assert batch.events() == []


def test_all_reused_results_skip_cpu_affinity_allocation(batch, monkeypatch):
    inputs = batch.inputs(2)
    batch.output.mkdir()
    for path in inputs:
        (batch.output / (path.name + ".radiantDIA")).write_text("existing")
    monkeypatch.delattr(search._os, "sched_setaffinity")

    def unused_allocator(*args, **kwargs):
        raise AssertionError("Reused outputs must not allocate child CPUs")

    monkeypatch.setattr(search, "_cpu_allocations", unused_allocator)
    batch.run(
        inputs,
        reuse_existing=True,
        executable=batch.executable.parent / "missing-executable",
    )

    assert batch.events() == []
    report = batch.report()
    assert report["processes"] == []
    assert report["settings"]["cpu_affinity_enforced"] is False
    assert all(f["status"] == "reused" for f in report["files"])
    assert len(_reader_outputs(batch)) == 2


def test_unexpected_worker_failure_releases_cpu_slot_and_preserves_later_files(
    batch, monkeypatch
):
    original_run = search._subprocess.run
    calls = 0

    def failing_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic worker failure")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(search._subprocess, "run", failing_run)
    inputs = batch.inputs(3)
    batch.run(inputs)

    report = batch.report()
    assert [f["status"] for f in report["files"]] == [
        "failed",
        "completed",
        "completed",
    ]
    assert "synthetic worker failure" in report["files"][0]["error"]
    assert report["status"] == "partial_failure"
    assert _started_files(batch.events()) == list(map(str, inputs[1:]))
    assert _reader_outputs(batch) == [
        batch.output / (p.name + ".radiantDIA") for p in inputs[1:]
    ]


@pytest.mark.parametrize("source", ["input", "config"])
def test_manifest_temporary_write_does_not_truncate_search_source(
    batch, tmp_path, source
):
    status_path = tmp_path / "status.json"
    former_temporary_path = tmp_path / "status.json.tmp"
    if source == "input":
        inputs = batch.inputs(names=[former_temporary_path.name])
        kwargs = {}
    else:
        inputs = batch.inputs(1)
        former_temporary_path.write_bytes(batch.config.read_bytes())
        kwargs = {"config": former_temporary_path}
    original = former_temporary_path.read_bytes()

    batch.run(inputs, status_path=status_path, **kwargs)

    assert former_temporary_path.read_bytes() == original
    assert batch.report(status_path)["files"][0]["status"] == "completed"
    assert len(_reader_outputs(batch)) == 1


def test_native_output_must_not_overwrite_library(batch):
    inputs = batch.inputs(1)
    batch.output.mkdir()
    library = batch.output / (inputs[0].name + ".radiantDIA")
    library.write_text("protected library content")
    with pytest.raises(ValueError, match="library|output_location"):
        batch.run(inputs, library=library)

    assert library.read_text() == "protected library content"
    assert batch.events() == []
    assert batch.reader_calls == []


def test_status_manifest_must_not_overwrite_executable_resolved_on_path(
    batch, monkeypatch
):
    original = batch.executable.read_bytes()
    monkeypatch.setenv(
        "PATH", str(batch.executable.parent) + os.pathsep + os.environ["PATH"]
    )
    with pytest.raises(ValueError, match="status_path"):
        batch.run(
            batch.inputs(1),
            executable=batch.executable.name,
            status_path=batch.executable,
        )

    assert batch.executable.read_bytes() == original
    assert batch.events() == []


def test_batch_error_is_exported_for_fulcrum_callers():
    import radiant_fulcrum_search

    assert radiant_fulcrum_search.RadiantBatchError is search.RadiantBatchError
    assert "RadiantBatchError" in radiant_fulcrum_search.__all__
