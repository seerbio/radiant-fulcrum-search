# Fulcrum Pipeline plugin for Radiant DIA

This plugin permits running Radiant DIA searches using local compute resources.

## Installation  

This library requires Python 3.8+ and can be installed with pip:  

```shell
pip install radiant-fulcrum-search
```

## Basic Usage  

This package provides plugins for [Fulcrum Pipeline](https://github.com/seerbio/fulcrum/).

After [installing `radiant-fulcrum-search`](#installation) you may use the
following parameters in your Fulcrum Pipeline configuration:

```toml
[search]
backend = "radiant"

# TODO: describe additional options
```

## Single-Cell Fragment Competition

The `single-cell` branch supports an opt-in post-extraction step for coeluting
isobaric assignments. It requires the corresponding `single-cell` branch of
`wheely-radiant` and full native feature reports (`shortReport = false`).

```toml
[search.fragment_competition]
precursor_ppm = 5.0
fragment_ppm = 20.0
min_shared_fragments = 4
min_trace_cosine = 0.5
max_apex_width_fraction = 0.5
max_rows = 500000
audit_location = "new-fragment-competition-audit"
```

The audited step applies to ordinary targets and decoys symmetrically, uses
unshared fragment support rather than protein identities, and never overwrites
native files. Fulcrum subsequently re-estimates precursor and protein confidence
and recomputes quantification. Native q-values themselves remain unchanged.

Omitting the section preserves existing behavior; an empty dictionary enables
the defaults. This is conservative single-assignment competition, not proof of
1% empirical FDR or a general PTM-localization method. It can discard genuine
coeluting isobaric peptides. Keep the audit and assess entrapment and sensitivity
on independent runs before adopting it for a new assay.
