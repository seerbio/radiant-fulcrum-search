# PF-100 validation

Ticket: [PF-100](https://seerbio.atlassian.net/browse/PF-100).

This change configures concurrent native Radiant processes, sequential inputs
per process, and CPU allocation per process. The execution and failure
contract, including container and memory behavior, is documented in
[README.md](../README.md).

## Acceptance evidence

The final validation report records measured results against these
criteria:

| Criterion | Evidence |
| --- | --- |
| Configure process count, files per process, and threads per process | TOML/Python examples, effective config copy, manifest settings. |
| Assign every pending input exactly once | Scheduler event counts, input order within each native process, reuse exclusion, and final partial batches. |
| Enforce concurrency and CPU budgets | Maximum simultaneous processes, child affinity, native thread config, and rejection of invalid or excessive settings. |
| Demonstrate 16 files on 32 cores as four processes, eight cores each, four sequential inputs each | Deterministic scheduler simulation; exactly four simultaneous searches. |
| Support workloads beyond 16 files | A larger scheduler workload that creates queued batches. |
| Retain successful outputs and completion/failure status | Mixed valid/failed batch, process crash, status JSON, strict failure policy, and downstream reader ingestion. |
| Preserve single-process/single-file behavior | Native single-file invocation, legacy marker compatibility, output naming, reader arguments, and scientific comparison. |
| Document containers and memory | Same-container child processes, separate per-process libraries, shared OS/container memory limits, and no plugin memory admission or hard per-process limit. |

## Scheduler simulation

The 32-core example is a scheduler simulation. The subprocess test fixture
performs no scientific computation and simulates 32 available CPUs. Its event
log checks four active searches, eight configured threads per process, four
sequential files per process, and one assignment per input.

This simulation demonstrates scheduling and configured allocation. It does
not measure scientific throughput or execution on a 32-core host. Real
validation must use the actual host/container CPU allocation.

Run the automated checks in an isolated development environment:

```shell
python -m pytest -q
python -m flake8 radiant_fulcrum_search tests
```

## Real native validation

Use the PF-99 native multi-file binary with the three manifest-identified
HeLa inputs, its compatible TSV spectral library, FASTA, and annotated
configuration. Preserve the supplied input hashes and compare each collected
native output against the corresponding independent PF-99 single-file output.

The recorded mixed workload uses two concurrent processes and eight threads
per process on a host with a verified 16-CPU allocation:

| Process | Assigned input order | Expected result |
| --- | --- | --- |
| 1 | Real input 1, missing input, real input 2 | Two completed outputs; one recoverable input failure; process exit 1. |
| 2 | Real input 3 | One completed output; process exit 0. |

Record the actual child CPU sets, overlap, elapsed time, library-load counts,
completed/failed file markers, final manifest, and output hashes. Successful
outputs should remain in original input order, and the failed input should
have no collected output.

Scientific comparison should report row/column counts, comparison keys,
absolute/relative tolerance, observed maximum numerical difference, and
whether every value matched exactly. Throughput claims require repeated
measurements; a single wall-clock run is descriptive evidence.

Validate collected outputs with the existing
`wheely_radiant.read_radiant_features` reader. Record retained file identities,
row counts, scoring columns, target/decoy metadata, precursor metadata, and
protein annotations. Reader ingestion does not validate all downstream
rescoring, FDR, or protein inference stages.

## Measured results

Tested implementation: `5f0ac21539ab34125d01d0ce046cd8f6a61468f2`. Draft PR:
https://github.com/seerbio/radiant-fulcrum-search/pull/11.

- 84 pytest tests passed with zero failures/errors and
  0 skipped; JUnit suite time was 4.736 seconds.
  Black and strict flake8 (source and tests, 79 columns) passed.
- The 16-file/32-core scheduler simulation verified four simultaneous
  searches, eight configured CPUs per process, and four sequential files per
  process. A 19-file test verified queued batches and a final three-file batch.
  These are scheduling simulations, not scientific performance measurements.
- The real mixed workload ran two processes with eight allocated CPUs each on
  the 16-CPU host. Process 1 handled real 1, missing, real 2; process 2 handled
  real 3. Native exit codes were 1 and 0. Three outputs were collected and one
  input failed. Each native process logged one library load.
- Search/collection elapsed time: 421.166 seconds.
  Complete validation harness wall time: 521.714 seconds.
  Native processes overlapped for 221.443 seconds.
  Timings are one observation and do not establish a speedup guarantee.

| Input | Independent rows | Batch rows | Columns | Agreement | Maximum absolute difference |
| --- | --- | --- | --- | --- | --- |
| HeLa injection 2 | 43,553 | 43,553 | 188 | Exact | 0 |
| HeLa injection 3 | 41,462 | 41,462 | 188 | Exact | 0 |
| HeLa injection 4 | 42,245 | 42,245 | 188 | Exact | 0 |

Comparisons used absolute and relative tolerances of `1e-6`. No columns were
outside tolerance. The existing `wheely_radiant.read_radiant_features` reader
ingested all three successful file identities and 127,260
rows, retaining targets, decoys, spectrum identities, and scoring columns.
Full downstream rescoring and protein inference were not exercised.
Final-implementation all-reuse validation passed all 9 checks
in 8.692 seconds, launched no native processes, preserved
the row counts, verified integer charge/boolean target types and valid precursor
metadata, and confirmed protein annotations for every target row.
The attached PDF SHA-256 is
`3114c33ba39802734d10243b4db5dedd928600bf3ff46f7d6dd55410be1dc07e`.

Native CPU affinity was enforced and observed as disjoint CPU sets. Resource
sampling saw at most 2 simultaneous native
processes and 9.143 GiB
aggregate RSS. Sampling began mid-run, so the RSS peak is an observed lower
bound, not a full-run high-water mark. Native OS thread counts can exceed the
configured search thread count; affinity still bounds each process's CPUs.

Artifacts under `/home/ubuntu/radiant-pf100-validation` include
`pytest-results.xml`, `real-summary.json`, `native-resource-summary.json`,
`reuse-summary.json`, `asset-integrity.json`,
`real-batch/radiant-batch-status.json`, and `report-evidence-hashes.json`.
The standalone report is `output/pdf/PF-100-validation-report.pdf`.
Source-asset hashes match the supplied PF-99 manifest. The tested native
dependency is `680836cb3c45f58c56a114db8bf14c69a0a4d779`; PF-99 remains a draft dependency.
No merge or release was performed.
