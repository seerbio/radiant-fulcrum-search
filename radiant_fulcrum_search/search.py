from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from concurrent.futures import as_completed as _as_completed
from copy import deepcopy as _deepcopy
import json as _json
import logging as _logging
import os as _os
from os import PathLike as _PathLike
from pathlib import Path as _Path
from queue import Queue as _Queue
import shutil as _shutil
import subprocess as _subprocess
import sys as _sys
from tempfile import NamedTemporaryFile as _NamedTemporaryFile
from tempfile import mkdtemp as _mkdtemp
from time import monotonic as _time
from typing import Iterable as _Iterable
from typing import Optional as _Optional
from typing import Union as _Union

import toml as _toml
from wheely.mammoth import PsmDataset as _PsmDataset
from wheely.mammoth.utils import listify as _listify
from wheely_radiant import read_radiant_features as _read_radiant_features

_logger = _logging.getLogger(__name__)


class RadiantBatchError(RuntimeError):
    """Batch failure with a report and retained successful outputs."""

    def __init__(self, message, report, status_path):
        super().__init__(message)
        self.report = report
        self.outputs = [_Path(path) for path in report["outputs"]]
        self.status_path = _Path(status_path)


def run_radiant_search(
    location: _Union[_PathLike, _Iterable[_PathLike]],
    library: _PathLike,
    fasta: _PathLike,
    config: _Union[dict, _PathLike],
    executable: _Union[str, _PathLike] = "RadiantDIA",
    mode: str = "serial",
    output_location: _Optional[_PathLike] = None,
    reuse_existing: bool = False,
    _results_extension: str = None,
    concurrent_processes: int = 1,
    files_per_process: int = 1,
    threads_per_process: _Optional[int] = None,
    cpu_budget: _Optional[int] = None,
    status_path: _Optional[_PathLike] = None,
    fail_on_error: bool = False,
    **kwargs,
) -> _PsmDataset:
    """Search locally with bounded concurrent processes and sequential batches.

    ``mode="serial"`` requires one concurrent process; ``"parallel"`` permits
    the configured number. Each native invocation receives at most
    ``files_per_process`` inputs and loads its library once (requires PF-99
    for multi-file invocations). The default remains one process per file.

    ``threads_per_process`` overrides General.threadCount in a private copy
    of the TOML config. Otherwise a positive configured thread count is used,
    or auto-detection divides ``cpu_budget`` between concurrent processes.
    The budget defaults to detected host/container CPUs.

    Successful outputs, in input order, are passed to read_radiant_features
    with ``kwargs``. All file/process statuses are written to ``status_path``
    (default: radiant-batch-status.json in the output or first input folder).
    Partial failures are logged and successful files continue downstream.
    ``fail_on_error=True`` raises RadiantBatchError after all batches finish;
    no usable output always raises it. Its report/outputs retain successes.

    ``reuse_existing`` trusts existing native results without checking their
    provenance. Duplicate inputs and colliding destinations are rejected
    before reuse or execution. See README for memory and container behavior.
    """
    if mode not in ("serial", "parallel"):
        raise ValueError("mode must be 'serial' or 'parallel'")
    _positive_integer("concurrent_processes", concurrent_processes)
    _positive_integer("files_per_process", files_per_process)
    if mode == "serial" and concurrent_processes != 1:
        raise ValueError(
            "mode='serial' requires concurrent_processes=1; "
            "use mode='parallel' for concurrent batches"
        )
    if not isinstance(fail_on_error, bool):
        raise ValueError("fail_on_error must be a boolean")
    if not isinstance(reuse_existing, bool):
        _logger.warning(
            "Got non-boolean reuse_existing=%r; using %s",
            reuse_existing,
            bool(reuse_existing),
        )
        reuse_existing = bool(reuse_existing)

    locations = [_Path(loc).resolve() for loc in _listify(location)]
    if not locations:
        raise ValueError("location must contain at least one input file")
    if len(set(locations)) != len(locations):
        raise ValueError(
            "Duplicate input locations: each input must be scheduled once"
        )
    extension = _results_extension or "radiantDIA"
    if "/" in extension or "\\" in extension:
        raise ValueError("_results_extension must be a filename extension")
    folder = _Path(output_location).resolve() if output_location else None
    files = []
    for loc in locations:
        result = (folder or loc.parent) / f"{loc.name}.{extension}"
        files.append(
            dict(
                input=str(loc),
                result=str(result),
                output=None,
                status="pending",
                process_id=None,
                error=None,
            )
        )
    results = [file["result"] for file in files]
    destinations = [_Path(result).resolve() for result in results]
    if len(set(destinations)) != len(destinations):
        raise ValueError(
            "Colliding output destinations: use unique input filenames "
            "or separate output folders"
        )
    if set(destinations).intersection(locations):
        raise ValueError("An output destination overlaps an input location")
    dependencies = {
        _Path(path).resolve() for path in (library, fasta, executable)
    }
    resolved_executable = _shutil.which(str(executable))
    if resolved_executable:
        dependencies.add(_Path(resolved_executable).resolve())
    if not isinstance(config, dict):
        dependencies.add(_Path(config).resolve())
    if dependencies.intersection(destinations):
        raise ValueError(
            "An output destination overlaps the config, library, FASTA "
            "or executable; choose a different output_location"
        )

    settings, radiant_config = _execution_settings(
        config, concurrent_processes, threads_per_process, cpu_budget
    )
    status_path = _Path(
        status_path
        or (folder or locations[0].parent) / "radiant-batch-status.json"
    ).resolve()
    protected = set(locations + destinations)
    protected.update(dependencies)
    if status_path in protected:
        raise ValueError(
            "status_path must not overwrite an input, native result, "
            "config, library, FASTA or executable"
        )
    status_path.parent.mkdir(parents=True, exist_ok=True)
    run_dir = _Path(_mkdtemp(prefix="radiant-run-", dir=status_path.parent))
    config_path = run_dir / "config.radiantConfig"
    config_path.write_text(_toml.dumps(radiant_config))

    pending = []
    for file in files:
        if reuse_existing and _Path(file["result"]).is_file():
            file.update(status="reused", output=file["result"])
        else:
            pending.append(file)
    batches = _plan_batches(pending, files_per_process)
    cpu_slots = _Queue()
    allocations = []
    if batches:
        allocations = _cpu_allocations(
            concurrent_processes,
            settings["threads_per_process"],
            cpu_budget=settings["cpu_budget"],
        )
        for allocation in allocations:
            cpu_slots.put(allocation)
    settings["cpu_affinity_enforced"] = bool(allocations) and all(
        allocation is not None for allocation in allocations
    )
    processes = []
    for process_id, batch in enumerate(batches, 1):
        for file in batch:
            file["process_id"] = process_id
        processes.append(
            dict(
                id=process_id,
                inputs=[f["input"] for f in batch],
                status="pending",
            )
        )
    start = _time()
    report = dict(
        schema_version=1,
        settings=dict(
            mode=mode, files_per_process=files_per_process, **settings
        ),
        files=files,
        processes=processes,
        outputs=[],
        elapsed_seconds=0,
        status="running",
        run_directory=str(run_dir),
    )
    report["outputs"] = [f["output"] for f in files if f["output"]]
    _write_report(status_path, report)
    by_input = {file["input"]: file for file in files}
    with _ThreadPoolExecutor(max_workers=concurrent_processes) as pool:
        futures = {}
        for process, batch in zip(processes, batches):
            assigned = [_deepcopy(file) for file in batch]
            future = pool.submit(
                _execute_radiant,
                executable,
                library,
                fasta,
                config_path,
                assigned,
                process["id"],
                run_dir,
                settings["threads_per_process"],
                extension,
                cpu_slots,
            )
            futures[future] = (process, assigned)
        for future in _as_completed(futures):
            try:
                process, completed_files = future.result()
            except Exception as exc:
                process, completed_files = futures[future]
                error = f"Radiant worker failed: {exc}"
                _logger.exception(error)
                process.update(status="failed", returncode=None, error=error)
                for file in completed_files:
                    if file["status"] == "pending":
                        file.update(status="failed", error=error)
            processes[process["id"] - 1].update(process)
            for file in completed_files:
                by_input[file["input"]].update(file)
            report["elapsed_seconds"] = _time() - start
            report["outputs"] = [f["output"] for f in files if f["output"]]
            _write_report(status_path, report)

    failed = any(f["status"] == "failed" for f in files) or any(
        p["status"] == "failed" for p in processes
    )
    report["status"] = "partial_failure" if failed else "completed"
    report["elapsed_seconds"] = _time() - start
    _write_report(status_path, report)
    _logger.info(
        "Searched %d inputs, collected %d outputs in %.02f sec; status: %s",
        len(files),
        len(report["outputs"]),
        report["elapsed_seconds"],
        status_path,
    )
    if failed:
        _logger.warning("Radiant batch has failures; check %s", status_path)
    if not report["outputs"] or (failed and fail_on_error):
        outcome = "failed" if failed else "produced no usable outputs"
        raise RadiantBatchError(
            f"Radiant batch {outcome}; "
            f"retained {len(report['outputs'])} outputs; check {status_path}",
            report,
            status_path,
        )
    return _read_radiant_features(
        location=[_Path(path) for path in report["outputs"]], **kwargs
    )


def _positive_integer(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer; got {value!r}")


def _execution_settings(
    config, concurrent_processes, threads_per_process, cpu_budget
):
    available = _available_cpus()
    if available < 1:
        raise ValueError(
            "Radiant requires at least one whole CPU; increase the "
            "host/container CPU allocation"
        )
    if cpu_budget is None:
        cpu_budget = available
    _positive_integer("cpu_budget", cpu_budget)
    if cpu_budget > available:
        raise ValueError(
            f"cpu_budget={cpu_budget} exceeds {available} available CPUs; "
            "reduce cpu_budget or increase the host/container CPU allocation"
        )
    try:
        radiant_config = (
            _deepcopy(config)
            if isinstance(config, dict)
            else _toml.load(config)
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Cannot read Radiant TOML config {config!r}: {exc}"
        ) from exc
    general = radiant_config.setdefault("General", {})
    if not isinstance(general, dict):
        raise ValueError("Radiant config [General] must be a TOML table")
    if threads_per_process is None:
        threads_per_process = general.get("threadCount", 0)
        if (
            isinstance(threads_per_process, bool)
            or not isinstance(threads_per_process, int)
            or threads_per_process < 0
        ):
            raise ValueError(
                "General.threadCount must be a non-negative integer; "
                "or set threads_per_process to a positive integer"
            )
        if threads_per_process == 0:
            if concurrent_processes > cpu_budget:
                raise ValueError(
                    "concurrent_processes exceeds cpu_budget; reduce "
                    "concurrent_processes or increase the CPU allocation"
                )
            threads_per_process = cpu_budget // concurrent_processes
    _positive_integer("threads_per_process", threads_per_process)
    required = concurrent_processes * threads_per_process
    if required > cpu_budget:
        raise ValueError(
            f"concurrent_processes ({concurrent_processes}) * "
            f"threads_per_process ({threads_per_process}) = {required} "
            f"exceeds cpu_budget={cpu_budget}; "
            "reduce concurrent_processes or threads_per_process"
        )
    general["threadCount"] = threads_per_process
    return (
        dict(
            concurrent_processes=concurrent_processes,
            threads_per_process=threads_per_process,
            cpu_budget=cpu_budget,
            available_cpus=available,
        ),
        radiant_config,
    )


def _available_cpus():
    """Integer CPU allocation from host count, affinity and cgroup quotas."""
    count = _os.cpu_count() or 1
    if hasattr(_os, "sched_getaffinity"):
        try:
            count = min(count, len(_os.sched_getaffinity(0)))
        except OSError:
            pass
    groups = [_Path("/sys/fs/cgroup")]
    try:
        for line in _Path("/proc/self/cgroup").read_text().splitlines():
            hierarchy, controllers, relative = line.split(":", 2)
            if hierarchy == "0" or "cpu" in controllers.split(","):
                roots = [_Path("/sys/fs/cgroup")]
                if hierarchy != "0":
                    roots = [
                        root / name
                        for root in roots
                        for name in ("cpu", "cpu,cpuacct")
                    ]
                for root in roots:
                    current = root / relative.lstrip("/")
                    while current == root or root in current.parents:
                        groups.append(current)
                        if current == root:
                            break
                        current = current.parent
    except (OSError, ValueError):
        pass
    for group in set(groups):
        try:
            maximum = group / "cpu.max"
            if maximum.is_file():
                quota, period = maximum.read_text().split()
                if quota != "max":
                    count = min(count, int(quota) // int(period))
            elif (group / "cpu.cfs_quota_us").is_file():
                quota = int((group / "cpu.cfs_quota_us").read_text())
                period = int((group / "cpu.cfs_period_us").read_text())
                if quota > 0:
                    count = min(count, quota // period)
        except (OSError, ValueError, ZeroDivisionError):
            continue
    return max(0, count)


def _cpu_allocations(
    concurrent_processes, threads_per_process, cpu_budget=None
):
    """Reserve disjoint CPU sets for lending to active children."""
    if not hasattr(_os, "sched_getaffinity") or not hasattr(
        _os, "sched_setaffinity"
    ):
        # Preserve legacy serial searches without affinity. Only the full
        # host budget can be enforced on such platforms.
        if concurrent_processes == 1 and cpu_budget == _available_cpus():
            return [None]
        raise ValueError(
            "Concurrent or reduced CPU budget enforcement requires Linux CPU "
            "affinity; use one process with the full host CPU budget or a "
            "Linux host/container"
        )
    try:
        cpus = sorted(_os.sched_getaffinity(0))
    except OSError as exc:
        raise ValueError(
            f"Cannot determine child CPU affinity: {exc}"
        ) from exc
    if len(cpus) < concurrent_processes * threads_per_process:
        raise ValueError(
            "CPU affinity provides fewer CPUs than the configured "
            "process/thread "
            "budget; reduce concurrent_processes or threads_per_process"
        )
    groups = []
    for index in range(concurrent_processes):
        start = index * threads_per_process
        stop = start + threads_per_process
        groups.append(cpus[start:stop])
    return groups


def _plan_batches(files, files_per_process):
    """Partition all inputs with unique native output names per batch."""
    batches = []
    batch = []
    names = set()
    for file in files:
        name = _Path(file["input"]).name
        if len(batch) == files_per_process or name in names:
            batches.append(batch)
            batch, names = [], set()
        batch.append(file)
        names.add(name)
    if batch:
        batches.append(batch)
    return batches


def _execute_radiant(
    executable,
    library,
    fasta,
    config,
    files,
    process_id,
    run_dir,
    threads,
    extension,
    cpu_slots,
):
    """Execute one native batch and retain only verified fresh results."""
    directory = run_dir / f"process-{process_id:04d}"
    logpath = directory / "radiant.log"
    command = list(
        map(
            str,
            [
                executable,
                library,
                fasta,
                config,
                *[file["input"] for file in files],
                "--output-folder",
                directory,
            ],
        )
    )
    env = _os.environ.copy()
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        env[name] = str(threads)
    start = _time()
    error = None
    returncode = None
    cpu_affinity = cpu_slots.get()
    # A fresh interpreter sets child-only affinity before exec. Do not use
    # preexec_fn: it is unsafe in the scheduler's threaded parent.
    launch_command = (
        [
            _sys.executable,
            "-c",
            "import os,sys; "
            "cpus={int(c) for c in sys.argv[1].split(',')}; "
            "os.sched_setaffinity(0, cpus); "
            "os.execvpe(sys.argv[2], sys.argv[2:], os.environ)",
            (
                ",".join(map(str, cpu_affinity))
                if cpu_affinity is not None
                else ""
            ),
            *command,
        ]
        if cpu_affinity is not None
        else command
    )
    _logger.info("Running Radiant process %d: %s", process_id, command)
    native_start = _time()
    try:
        directory.mkdir()
        with logpath.open("w") as logfile:
            completed = _subprocess.run(
                launch_command,
                shell=False,
                check=False,
                stdout=logfile,
                stderr=_subprocess.STDOUT,
                text=True,
                env=env,
            )
        returncode = completed.returncode
    except OSError as exc:
        error = (
            f"Cannot execute Radiant: {exc}; check executable and permissions"
        )
    finally:
        native_stop = _time()
        cpu_slots.put(cpu_affinity)
    try:
        lines = (
            logpath.read_text(errors="replace").splitlines()
            if logpath.exists()
            else []
        )
    except OSError as exc:
        lines = []
        error = f"Cannot read Radiant status log {logpath}: {exc}"
    for file in files:
        native = directory / f"{_Path(file['input']).name}.{extension}"
        completed_marker = f"COMPLETED: {file['input']}" in lines
        failure = next(
            (
                line
                for line in lines
                if line == f"FAILED: {file['input']}"
                or line.startswith(f"FAILED: {file['input']} - ")
            ),
            None,
        )
        # PF-99 confirms individual successes even if another file fails.
        # The single-file legacy CLI has no markers, so exit 0 is sufficient.
        success = completed_marker or (
            len(files) == 1 and returncode == 0 and not failure
        )
        if error or failure or not success:
            file.update(
                status="failed",
                error=error
                or failure
                or (
                    f"No COMPLETED marker (exit {returncode}); "
                    "check executable, "
                    f"library, FASTA, config and {logpath}. "
                    "Multi-file execution requires PF-99 Radiant."
                ),
            )
        elif native.is_file():
            try:
                result = _Path(file["result"])
                result.parent.mkdir(parents=True, exist_ok=True)
                # Collect atomically even when output dirs span filesystems.
                with _NamedTemporaryFile(
                    dir=result.parent, delete=False
                ) as temp:
                    temporary = _Path(temp.name)
                try:
                    _shutil.copyfile(native, temporary)
                    temporary.replace(result)
                    native.unlink()
                finally:
                    temporary.unlink(missing_ok=True)
                file.update(status="completed", output=str(result))
            except OSError as exc:
                file.update(
                    status="failed", error=f"Cannot collect {native}: {exc}"
                )
        elif completed_marker:
            file.update(status="completed_no_output")
        else:
            file.update(
                status="failed",
                error=f"Radiant result file was not created: {native}; "
                f"check {logpath}",
            )
    failed = (
        error or returncode != 0 or any(f["status"] == "failed" for f in files)
    )
    return (
        dict(
            id=process_id,
            inputs=[f["input"] for f in files],
            status="failed" if failed else "completed",
            returncode=returncode,
            error=error,
            log=str(logpath),
            command=command,
            cpu_affinity=cpu_affinity,
            started_monotonic=native_start,
            finished_monotonic=native_stop,
            elapsed_seconds=_time() - start,
        ),
        files,
    )


def _write_report(path, report):
    """Replace atomically so readers never see a partially written manifest."""
    temporary = None
    try:
        with _NamedTemporaryFile(
            mode="w", dir=path.parent, prefix="radiant-status-", delete=False
        ) as stream:
            temporary = _Path(stream.name)
            stream.write(_json.dumps(report, indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
