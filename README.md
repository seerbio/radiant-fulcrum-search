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
