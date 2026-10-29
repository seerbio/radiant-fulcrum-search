# Fulcrum Pipeline plugin for Radiant DIA

This plugin permits running Radiant DIA searches using local compute resources.

If possible, prefer using
[`radiant-fulcrum-workflow`](https://github.com/seerbio/radiant-fulcrum-workflow),
which provides a higher-level workflow with sensible defaults for common use
cases.

## Installation  

This library requires Python 3.8+ and can be installed with pip:  

```shell
pip install radiant-fulcrum-search
```

## Basic Usage

This package provides plugins for [Fulcrum Pipeline](https://github.com/seerbio/fulcrum/).

After [installing `radiant-fulcrum-search`](#installation), configure Fulcrum
to use the `radiant` search backend:

```toml
[search]
backend = "radiant"
location = ["file1.mzML", "file2.mzML"]
library = "/path/to/library.tsv"
fasta = "/path/to/database.fasta"
config = "/path/to/config.radiantConfig"
```

## Parallel processes and sequential files

Configure three separate quantities: how many Radiant processes may run at
once, how many files each process searches sequentially, and how many CPU
threads each process uses. Each batch launches one native process, which loads
its spectral library once and processes the assigned files in input order.
More batches wait until a process slot is available.

Multi-file native invocations require Radiant's PF-99 implementation
([draft PR #139](https://github.com/seerbio/radiant/pull/139), implementation
commit `680836cb3c45f58c56a114db8bf14c69a0a4d779`) or a version that includes
that CLI contract:

```text
RadiantDIA LIBRARY FASTA CONFIG INPUT1 INPUT2 ... --output-folder OUTPUT
```

The defaults retain serial execution with one file per native invocation.
Single-file execution also supports the existing Radiant CLI.

| Search parameter | Default | Meaning |
| --- | --- | --- |
| `mode` | `"serial"` | `"serial"` requires one concurrent process; `"parallel"` permits the configured concurrency. |
| `concurrent_processes` | `1` | Maximum simultaneous native Radiant processes. |
| `files_per_process` | `1` | Maximum sequential inputs assigned to each native invocation. |
| `threads_per_process` | Omitted | Positive thread allocation per process; overrides `General.threadCount` when supplied. |
| `cpu_budget` | Detected allocation | Positive integer limit for the search's total allocated CPUs. |
| `output_location` | Each input's directory | Destination for collected native result files. |
| `status_path` | `radiant-batch-status.json` in the output directory, or first input's directory | Batch status manifest. |
| `reuse_existing` | `false` | Trust and ingest an existing native result instead of scheduling its input. |
| `fail_on_error` | `false` | Raise after execution if any file or process failed. |
| `executable` | `"RadiantDIA"` | Native executable path or name resolved through `PATH`. |

All process, file, thread, and CPU counts must be positive integers; booleans
and fractional values are rejected. When `threads_per_process` is omitted, a
positive `General.threadCount` is used. A missing or zero config thread count
selects `cpu_budget // concurrent_processes`. The selected thread count is
written into a private config copy, preserving the source config. This search
setting takes precedence over a thread count written by the higher-level
workflow's `threads` argument.

The plugin rejects
`concurrent_processes * threads_per_process > cpu_budget` and a budget greater
than the detected host/container allocation. Errors identify the setting to
reduce or the allocation to increase. Detection uses host CPU count, process
affinity, and ancestor CPU quotas in standard Linux cgroup v2 mounts and v1
`cpu` or `cpu,cpuacct` mounts. Quotas are rounded down to whole CPUs; an
allocation below one whole CPU is rejected.

On Linux, simultaneous children receive disjoint CPU sets, each containing
`threads_per_process` CPUs, to bound native auxiliary work as well as configured
search threads. OpenMP, MKL, and OpenBLAS thread settings are also passed to the
child environment. CPU allocations are reused as queued batches complete.
Multi-process execution or a reduced CPU budget requires Linux CPU affinity
support. Platforms without affinity support retain single-process execution
with the full detected host budget; its configured search threads and numeric
budget are checked, but auxiliary native threads are not restricted to a
smaller CPU set. The manifest's `settings.cpu_affinity_enforced` records whether
child affinity was applied. An all-reused call launches no children and reports
this setting as `false`.

Budgets and disjoint CPU sets apply within one Fulcrum invocation. The plugin
does not coordinate allocations across independent Fulcrum invocations or
containers. Use external scheduling or container/OS allocations to partition
resources between them. Choose a smaller search budget when other applications
need CPU capacity.

### Four simultaneous searches on 32 cores

For the higher-level `radiant-fulcrum-workflow`, use the existing
`[overrides.search]` convention:

```toml
workflow = "radiant"
library = "/data/library.tsv"
fasta = "/data/database.fasta"
config = "/data/search.radiantConfig"
results_dir = "/data/results"
mzml = [
  "/data/input01.mzML", "/data/input02.mzML",
  "/data/input03.mzML", "/data/input04.mzML",
  "/data/input05.mzML", "/data/input06.mzML",
  "/data/input07.mzML", "/data/input08.mzML",
  "/data/input09.mzML", "/data/input10.mzML",
  "/data/input11.mzML", "/data/input12.mzML",
  "/data/input13.mzML", "/data/input14.mzML",
  "/data/input15.mzML", "/data/input16.mzML",
]

[overrides.search]
mode = "parallel"
concurrent_processes = 4
files_per_process = 4
threads_per_process = 8
cpu_budget = 32
reuse_existing = false
fail_on_error = false
```

| Native process | Sequential inputs | Allocated CPUs |
| --- | --- | --- |
| 1 | 01, 02, 03, 04 | 8 |
| 2 | 05, 06, 07, 08 | 8 |
| 3 | 09, 10, 11, 12 | 8 |
| 4 | 13, 14, 15, 16 | 8 |

This example has four simultaneous searches and a total search allocation of
32 CPUs. The 16 files are the example workload. There is no 16-file limit:
additional inputs form queued batches, and the final batch may contain fewer
than four files. An actual 32-core allocation is required to execute this
configuration; tests that simulate 32-core availability do not establish
32-core scientific performance.

For a low-level Fulcrum workflow, put the same controls directly under
`[search]`, alongside `backend = "radiant"` and the input/library/config paths.
The Python function `radiant_fulcrum_search.search.run_radiant_search` accepts
the same keyword arguments.

An MBR workflow has a first-pass library search and a second-pass search.
`[overrides.search]` configures the second pass. Configure the first pass
separately when both stages need these execution controls:

```toml
[overrides.library.search]
mode = "parallel"
concurrent_processes = 4
files_per_process = 4
threads_per_process = 8
cpu_budget = 32
```

### Containers and memory

One Fulcrum invocation runs its Radiant children on the same host and, when
Fulcrum is containerized, inside the same existing container. The plugin does
not create a container for each process. The packaged Radiant/Fulcrum image
already contains the native executable and Python/Java workflow components.
All children share that container's CPU and memory allocation. Paths must be
accessible inside the container, for example through mounted data directories.
There is no files-per-container maximum imposed by this plugin.

Each active native process loads a separate spectral library and holds its
own file-processing state. Four concurrent processes therefore require four
library allocations. Sequential files reuse the library inside their process
and initialize independent calibration/scoring state; they do not all execute
or hold active search state at once.

The plugin has no memory admission budget and imposes no hard per-process
memory limit. The host or container's shared memory limit remains the limit
for the workload. Sequential batching is not a guarantee of constant RSS:
native allocations and allocator retention can vary with input size.
Choose concurrency using measured peak process memory and reserve capacity
for Python, Java/Spark, and other host workloads. Lower
`concurrent_processes` when the allocation cannot hold the simultaneous
processes. If the OS kills a native process, its failure and any verified
completed outputs are recorded.

## Outputs and failure status

Every non-reused input is assigned to exactly one native invocation. There
are no implicit retries. Duplicate canonical input paths and colliding final
output destinations are rejected before launch. Inputs with the same basename
in different directories can use their separate default destinations; they
are split into separate native invocations so their staging names cannot
collide. A shared `output_location` requires unique input filenames.

Each call creates a private `radiant-run-*` directory beside `status_path`.
It contains the effective `config.radiantConfig` and one
`process-NNNN/radiant.log` per native batch. Native results are first written
there, then collected atomically to their final
`<input-filename>.radiantDIA` destinations. Fresh staging prevents a stale
result from being treated as the output of a failed new search. The retained
run directory and logs are referenced by the manifest for diagnosis.

With `reuse_existing = true`, existing native files are accepted without
checking their library, config, contents, or provenance. Reused inputs are
excluded from native batches and marked `reused`; verify provenance before
enabling reuse. A failed new search does not overwrite its old destination.

The JSON manifest is written before launch, updated as batches complete,
and finalized before downstream ingestion. It includes:

- Effective execution settings and the private run directory.
- Every input's intended result, collected output, status, process ID, and
  error.
- Every process's assigned inputs, command, return code, status, log path,
  CPU affinity, monotonic start/finish times, and elapsed time.
- Collected outputs in original input order and total elapsed time.

Final file statuses are `completed`, `reused`, `failed`, or
`completed_no_output`. PF-99 logs identify completed and failed inputs even
when a mixed batch returns exit code 1. A completed native search that emits
no result is marked `completed_no_output` and excluded from ingestion.
A fatal process exit marks its unfinished assigned inputs failed while
retaining outputs whose completion was verified. The batch manifest records
`completed` or `partial_failure`, while process statuses distinguish success
from failure.

By default, partial failures log a warning and successful native paths are
passed to `wheely_radiant.read_radiant_features`, preserving the Fulcrum
`PsmDataset` return contract. Other reader arguments continue through
`**kwargs`. Set `fail_on_error = true` to collect all batch results and then
raise before calling the reader if any file or process failed. If no usable
outputs remain, the function always raises `RadiantBatchError`.
The exception exposes `report`, `outputs`, and `status_path`, including
successful outputs retained under the strict policy.

The default manifest path is replaced on a later invocation using that path.
Supply a distinct `status_path` to retain separate manifests for repeated runs.
Choose a manifest path that does not overlap an input, native result, or
execution dependency.
