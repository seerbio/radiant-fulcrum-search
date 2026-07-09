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

### Optional Search Parameters

```toml
[search]
backend = "radiant"
location = ["file1.mzML", "file2.mzML"]
library = "/path/to/library.tsv"
fasta = "/path/to/database.fasta"
config = "/path/to/config.radiantConfig"
executable = "RadiantDIA"
output_location = "/path/to/output"
reuse_existing = false
mode = "serial"
```

Notes:
- `mode` currently supports only `"serial"`.
- `output_location` controls where `.radiantDIA` outputs and log files are written.
- `reuse_existing = true` reuses existing output files when present.
