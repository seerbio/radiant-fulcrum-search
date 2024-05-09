from os import (
    PathLike as _PathLike,
)
from typing import (
    Iterable as _Iterable,
    Optional as _Optional,
    Union as _Union,
)

from wheely.mammoth import (
    PsmDataset as _PsmDataset,
)


def run_pythia_search(
    location: _Union[_PathLike, _Iterable[_PathLike]],
    executable: _Union[str, _PathLike] = "PythiaDIA",
    mode: str = "serial",
    **kwargs,
) -> _PsmDataset:
    """
    Execute Pythia locally to search the given location(s).

    Arguments
    ---------
    location: one or more `PathLike` values giving the local
              path of the mzML or Parquet file(s) to search
    executable: the path of a Pythia executable. By default,
                the executable `PythiaDIA` will be located
                using the current value of `$PATH`.
    mode: if "serial" each of multiple files will be processed
          sequentially. If "parallel" all files will be run
          concurrently; this should provide faster execution
          provided that sufficient CPU and RAM is available.
          You _must_ ensure that Pythia is configured to use
          an appropriate number of threads.
    """
    raise NotImplementedError("TODO")
