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
from wheely_radiant import (
    read_radiant_features as _read_radiant_features,
)

_logger = _logging.getLogger(__name__)


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
    **kwargs,
) -> _PsmDataset:
    """
    Execute Radiant DIA locally to search the given location(s).

    Arguments
    ---------
    location : PathLike
        One or more `PathLike` values giving the local path of the mzML
        or Parquet file(s) to search.
    library : PathLike
        The local path of the library to use.
    fasta : PathLike
        The local path of the FASTA to use.
    config : PathLike
        The local path of the Radiant DIA configuration file to use.
        TODO: permit passing a dict with configuration key-value pairs.
    executable : str | PathLike (optional)
        The path of a Radiant DIA executable.
        By default, the executable `RadiantDIA` will be located using the
        current value of `$PATH`.
    mode : ("serial" | "parallel")
        If "serial" each of multiple files will be processed sequentially.
        TODO: If "parallel" all files will be run concurrently; this should
        provide faster execution provided that sufficient CPU and RAM is
        available. You _must_ ensure that Radiant DIA is configured to use an
        appropriate number of threads.
    output_location : PathLike, optional
        If provided, Radiant DIA result files will be written to this location,
        and this location will be passed to :py:func:``wheely_radiant.read_radiant_features``.
        When ``reuse_existing`` is ``True``, this location will be checked
        for existing results.
    reuse_existing : bool, optional
        If truthy, check for a result file with the expected name
        and use it if it exists. Defaults to `False` as there is
        no check that these results used the correct library or
        params!
    kwargs :
        Other keyword args will be passed to :py:func:``wheely_radiant.read_radiant_features``.
    """
    if mode != "serial":
        raise NotImplementedError("TODO: parallel processing")

    location = _listify(location)

    if not isinstance(reuse_existing, bool):
        _orig_reuse = reuse_existing
        reuse_existing = bool(reuse_existing)
        _logger.warning(
            'Got non-boolean value reuse_existing = "%s"! Will use value: %s',
            _orig_reuse,
            reuse_existing,
        )

    start = _time()

    outputs = []
    for loc in location:
        if isinstance(config, dict):
            raise NotImplementedError("TODO: support config in dict")
        else:
            config_path = config

        out = _execute_radiant(
            executable=executable,
            library=library,
            fasta=fasta,
            config=config_path,
            location=loc,
            output_location=output_location,
            reuse_existing=reuse_existing,
            results_extension=_results_extension,
        )
        outputs.append(out)

    stop = _time()

    _logger.info("Searched %d files in %.02f sec", len(outputs), stop - start)

    return _read_radiant_features(location=outputs, **kwargs)


def _execute_radiant(
    executable: _PathLike,
    library: _PathLike,
    fasta: _PathLike,
    config: _PathLike,
    location: _PathLike,
    reuse_existing: bool,
    output_location: _Optional[_PathLike] = None,
    results_extension: str = None,
) -> _PathLike:
    location = _Path(location)
    folder = _Path(output_location or location.parent)

    results_extension = results_extension or "radiantDIA"

    result = folder / f"{location.name}.{results_extension}"

    if reuse_existing and result.exists():
        return result

    command = list(
        map(
            str,
            [
                executable,
                library,
                fasta,
                config,
                location,
                *(
                    s
                    for s in ["--output-folder", output_location]
                    if output_location
                ),
            ],
        )
    )

    _logger.info("Running command: %s", command)

    folder.mkdir(parents=True, exist_ok=True)

    logpath = folder / f"{location.name}.log"

    # Open a log file -- output from the command will be written there.
    with open(logpath, "w") as logfile:
        # Run the command. Raise an exeception if the exit code indicates an error.
        # All output (including errors!) will be written to the log file!
        _subprocess.run(
            command,
            shell=False,
            check=True,
            stdout=logfile,
            stderr=_subprocess.STDOUT,
            text=True,
        )

    if not result.exists():
        raise RuntimeError(
            f"Radiant result file was not created: {result}; for more details check {logpath}"
        )

    return result
