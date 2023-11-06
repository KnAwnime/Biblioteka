import json
import os
from collections import defaultdict
from typing import Any, cast, Dict, List, Set
from warnings import warn

from tools.stats.import_test_stats import (
    ADDITIONAL_CI_FILES_FOLDER,
    TEST_CLASS_RATINGS_FILE,
)

from tools.testing.target_determination.heuristics.interface import (
    HeuristicInterface,
    TestPrioritizations,
)

from tools.testing.target_determination.heuristics.utils import (
    query_changed_files,
    REPO_ROOT,
)


class HistoricalClassFailurCorrelation(HeuristicInterface):
    """
    This heuristic prioritizes test classes that have historically tended to fail
    when the files edited by current PR were modified.
    """

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def get_test_priorities(self, tests: List[str]) -> TestPrioritizations:
        correlated_tests = _rank_correlated_tests(tests)

        test_rankings = TestPrioritizations(
            tests_being_ranked=tests, probable_relevance=correlated_tests
        )

        return test_rankings


def _get_historical_test_class_correlations() -> Dict[str, Dict[str, float]]:
    path = REPO_ROOT / ADDITIONAL_CI_FILES_FOLDER / TEST_CLASS_RATINGS_FILE
    if not os.path.exists(path):
        print(f"could not find path {path}")
        return {}
    with open(path) as f:
        test_class_correlations = cast(Dict[str, Dict[str, float]], json.load(f))
        return test_class_correlations


def _get_tests_to_prioritize(
    tests_to_run: Set[str],
    changed_files: List[str],
    test_class_correlations: Dict[str, Dict[str, float]],
) -> List[str]:
    # Find the tests failures that are correlated with the edited files.
    # Filter the list to only include tests we want to run.
    ratings: Dict[str, float] = defaultdict(float)
    for file in changed_files:
        for qualified_test_class, score in test_class_correlations.get(
            file, {}
        ).items():
            # qualified_test_class looks like "test_file::test_class"
            test_file, test_class = qualified_test_class.split("::")
            if test_file in tests_to_run:
                ratings[qualified_test_class] += score

    prioritize = sorted(ratings, key=lambda x: -ratings[x])
    return prioritize


def _rank_correlated_tests(tests_to_run: List[str]) -> List[str]:
    tests_to_run = set(tests_to_run)

    test_class_correlations = _get_historical_test_class_correlations()
    if not test_class_correlations:
        return []

    # Get the files edited
    try:
        changed_files = query_changed_files()
    except Exception as e:
        warn(f"Can't query changed test files due to {e}")
        return []

    # Find the tests failures that are correlated with the edited files.
    # Filter the list to only include tests we want to run.
    return _get_tests_to_prioritize(
        tests_to_run, changed_files, test_class_correlations
    )
