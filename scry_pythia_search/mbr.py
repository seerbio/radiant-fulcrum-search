from copy import deepcopy
import logging as _logging

import toml as _toml

from scry.workflow import get_workflow as _get_workflow

_logger = _logging.getLogger(__name__)


def pythia_mbr_workflow(spark, library=None, **kwargs):
    """
    Pythia MBR workflow implementation.

    Arguments
    ---------
    spark: SparkSession
        The Spark session to use for the workflow.
    library:
        Parameter overrides to be used for library creation.
        These should largely be the same as those for the ``v0`` workflow.
        Where certain parameters are missing from ``library`` they will be
        populated with either default values, or corresponding values
        from ``kwargs`` -- this choice is determined by this function to
        provide the most sensible behavior.
        Note that certain specified parameters in ``library`` may be ignored.
    kwargs: dict
        Additional keyword arguments for the workflow.
        These should largely be the same as those for the ``v1`` workflow.
    """
    # Ensure that the search backend is pythia
    if kwargs["search"]["backend"] != "pythia":
        raise ValueError("The search backend must be 'pythia'")
    if kwargs["search"]["reuse_existing"]:
        raise ValueError(
            "The search backend must have reuse_existing = false!"
        )

    # Set up first pass params to create library
    firstpass_params = deepcopy(library) if library else dict()
    if "search" not in firstpass_params:
        try:
            firstpass_params["search"] = deepcopy(kwargs.get("search"))
        except KeyError as e:
            raise ValueError(
                "No search parameters found in kwargs or library!"
            ) from e
    firstpass_params.setdefault("airpot", kwargs.get("airpot", dict()))
    if "cortado" not in firstpass_params:
        if "cortado" in kwargs:
            firstpass_params["cortado"] = deepcopy(kwargs["cortado"])
        else:
            firstpass_params["cortado"] = dict()
    firstpass_params["cortado"]["pep_fdr_type"] = "precursor-only"
    firstpass_params["output"] = dict(
        firstpass_params.get("output", dict()),
        location=lib_loc,
        backend="write_library",
        spectra_backend="pythia",
    )

    _logger.info(
        "Computed parameters for first pass library creation: \n%s",
        _toml.dumps(firstpass_params),
    )

    # Fetch the v0 workflow from the registry and create a library
    v0_workflow = _get_workflow("v0")

    # Instead of using this return we will use the TSV that's written
    lib_dset = v0_workflow(**firstpass_params, spark=spark)

    # TODO: logging about the returned lib?

    # Fetch the v1 workflow from the registry and execute it
    v1_workflow = _get_workflow("v1")

    # Set up second pass params to use the library
    scndpass_params = deepcopy(kwargs)
    scndpass_params.setdefault("search", dict())["library"] = lib_loc
    _logger.info(
        "Computed parameters for second pass: \n%s",
        _toml.dumps(scndpass_params),
    )

    # Run the full workflow
    result = v1_workflow(**scndpass_params, spark=spark)
    return result
