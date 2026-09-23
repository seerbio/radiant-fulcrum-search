"""
`test_search` -- unit tests for utils in `radiant_fulcrum_search.search`
"""

import pytest


def test_import():
    """
    Stub test that importing the root module is successful.
    TODO: replace this with more meaningful tests
    """
    import radiant_fulcrum_search


@pytest.mark.parametrize("options", [None, {}, {"min_shared_fragments": 5}])
def test_fragment_competition_opt_in(monkeypatch, options):
    from radiant_fulcrum_search import search
    from wheely_radiant import competition

    native_result = object()
    filtered_result = object()
    calls = []

    def compete(dataset, **kwargs):
        calls.append((dataset, kwargs))
        return filtered_result

    monkeypatch.setattr(search, "_execute_radiant", lambda **kwargs: "cached")
    monkeypatch.setattr(
        search, "_read_radiant_features", lambda **kwargs: native_result
    )
    monkeypatch.setattr(competition, "compete_isobaric_features", compete)
    result = search.run_radiant_search(
        location="run.mzML",
        library="library",
        fasta="fasta",
        config="config",
        fragment_competition=options,
    )
    if options is None:
        assert result is native_result
        assert calls == []
    else:
        assert result is filtered_result
        assert calls == [(native_result, options)]
