import logging as _logging
from os import (
    PathLike as _PathLike,
)
from pathlib import (
    Path as _Path,
)
import subprocess as _subprocess
from time import time as _time
from typing import (
    Iterable as _Iterable,
    Optional as _Optional,
    Union as _Union,
)

from wheely.mammoth import (
    PsmDataset as _PsmDataset,
)
from wheely.mammoth.utils import (
    listify as _listify,
)
from wheely_pythia import (
    read_pythia_features as _read_pythia_features,
)

_logger = logging.getLogger(__name__)


def run_pythia_search(
    location: _Union[_PathLike, _Iterable[_PathLike]],
    library: _PathLike,
    fasta: _PathLike,
    config: _Union[dict, _PathLike],
    executable: _Union[str, _PathLike] = "PythiaDIA",
    mode: str = "serial",
    **kwargs,
) -> _PsmDataset:
    """
    Execute Pythia locally to search the given location(s).

    Arguments
    ---------
    location: one or more `PathLike` values giving the local path of the mzML
              or Parquet file(s) to search.
    library: the local path of the library to use.
    fasta: the local path of the FASTA to use.
    config: the local path of the Pythia configuration file to use.
            TODO: permit passing a dict with configuration key-value pairs.
    executable: the path of a Pythia executable. By default, the executable
                `PythiaDIA` will be located using the current value of `$PATH`.
    mode: if "serial" each of multiple files will be processed sequentially.
          TODO: If "parallel" all files will be run concurrently; this should
          provide faster execution provided that sufficient CPU and RAM is
          available. You _must_ ensure that Pythia is configured to use an
          appropriate number of threads.

    Other keyword args will be passed to `wheely_pythia.read_pythia_features`.
    """
    if mode != "serial":
        raise NotImplementedError("TODO: parallel processing")

    location = _listify(location)

    start = _time()

    outputs = []
    for loc in location:
        if isinstance(config, dict):
            raise NotImplementedError("TODO: support config in dict")
        else:
            config_path = config

        out = _execute_pythia(
            executable=executable,
            library=library,
            fasta=fasta,
            config=config_path,
            location=loc,
        )
        outputs.append(out)

    stop = _time()

    _logger.info("Searched %d files in %.02f sec", len(outputs), stop - start)

    return _read_pythia_features(location=outputs, **kwargs)


def _execute_pythia(
    executable: _PathLike,
    library: _PathLike,
    fasta: _PathLike,
    config: _PathLike,
    location: _PathLike,
) -> _PathLike:
    location = Path(location)
    folder = location.parent

    command = [
        executable,
        library,
        fasta,
        config,
        location,
    ]

    # Open a log file -- stdout from the command will be written there.
    with open(folder / f"{location.name}.log", "w") as logfile:
        # Run the command. Raise an execption if the exit code indicates an error.
        # Standard output will be written to the log file, but stderr will be written
        # to _this_ process' output!
        _subprocess.run(
            command,
            shell=False,
            check=True,
            stdout=logfile,
            stderr=subprocess.PIPE,
            text=True,
        )

    return folder / f"{location.name}.pythiaDIA"
