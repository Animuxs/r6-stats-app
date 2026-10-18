"""
run_tests.py
============
Runs every test in scripts/ (or the folder given) on all of this computer's cores: the same tests
`python -m unittest discover -s scripts` runs, with the same result, in a fraction of the time.

    python scripts/run_tests.py              # every test
    python scripts/run_tests.py -j 4         # on 4 processes
    python scripts/run_tests.py desktop      # another folder's tests
    python scripts/run_tests.py --coverage   # and what they cover (pip install -r scripts/requirements-dev.txt)
    python scripts/run_tests.py --lint       # pyflakes first: undefined names, unused imports

Each process runs whole test classes, longest first, so a class's setUpClass and its module's
setUpModule still run once around its tests. The slowest tests are listed at the end, so a test
that gets slow is noticed. Exits 1 if any test fails; with --coverage, also if the tests cover
less of the app than MIN_TOTAL, or less of any one module than MIN_MODULE (a new module needs
tests of its own, not just the rest's).
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import time
import unittest
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


def _classes(folder: Path) -> list[str]:
    """Every test class in `folder`, as "module.Class"."""
    def walk(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from walk(item)
            else:
                yield item
    found = {}
    for test in walk(unittest.defaultTestLoader.discover(str(folder))):
        if isinstance(test, unittest.loader._FailedTest):  # a module that doesn't import: report it
            raise SystemExit(f"can't load {test.id()}: {test._exception}")
        found[test.id().rsplit(".", 1)[0]] = None
    return list(found)


def _run(folder: str, names: list[str]) -> tuple[int, list[str], int, dict[str, float]]:
    """Runs test classes in this process: (tests run, failure reports, skipped, seconds per test)."""
    if folder not in sys.path:
        sys.path.insert(0, folder)
    os.chdir(folder)
    times: dict[str, float] = {}

    class Timed(unittest.TextTestResult):
        def startTest(self, test):
            self._started = time.perf_counter()
            super().startTest(test)

        def stopTest(self, test):
            times[test.id()] = time.perf_counter() - self._started
            super().stopTest(test)

    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Timed, verbosity=0).run(suite)
    failures = [f"{'FAIL' if kind == 'f' else 'ERROR'}: {test.id()}\n{report}"
                for kind, items in (("f", result.failures), ("e", result.errors)) for test, report in items]
    failures += [f"UNEXPECTED SUCCESS: {test.id()}" for test in result.unexpectedSuccesses]
    return result.testsRun, failures, len(result.skipped), times


HERE = Path(__file__).parent
COVERAGE_CONFIG = HERE / ".coveragerc"
MIN_TOTAL = 92  # percent of the app's statements and branches (each if/else way) the tests run
MIN_MODULE = 75  # and of each module's statements


def _covered() -> int:
    """Combines the test processes' coverage, prints it, and returns 1 if it's under the minimums."""
    import coverage

    cov = coverage.Coverage(config_file=str(COVERAGE_CONFIG))
    cov.combine()
    cov.save()
    total = cov.report()
    low = []
    for path in cov.get_data().measured_files():
        _name, statements, _excluded, missing, _text = cov.analysis2(path)
        if statements and 100 * (len(statements) - len(missing)) / len(statements) < MIN_MODULE:
            low.append(Path(path).name)
    if low:
        print(f"Under {MIN_MODULE}% covered: {', '.join(sorted(low))}. Add tests for them.")
    if total < MIN_TOTAL:
        print(f"The tests cover {total:.1f}% of the app, under {MIN_TOTAL}%. Add tests for the new code.")
    return 1 if low or total < MIN_TOTAL else 0


# the classes that take longest, from earlier runs: started first so none is left running alone at the end
_SLOW_FIRST = ("test_pages.", "test_app.TestAnalyticsPages", "test_app.TestAddingMatches", "test_events.TestLeaguePage")


def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    args.add_argument("folder", nargs="?", default=str(Path(__file__).parent))
    args.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    args.add_argument("--coverage", action="store_true", help="measure what the tests cover, and require enough")
    args.add_argument("--lint", action="store_true", help="run pyflakes on the app and its tests first")
    args = args.parse_args(argv)
    if args.lint:
        targets = [str(HERE), str(HERE.parent / "desktop"), *map(str, (HERE.parent / "browser").glob("*.py"))]
        if subprocess.call([sys.executable, "-m", "pyflakes", *targets]):
            print("pyflakes found problems (above).")
            return 1
    if args.coverage:  # the same run, under coverage, in every process
        (HERE.parent / "build").mkdir(exist_ok=True)
        for old in (HERE.parent / "build").glob(".coverage*"):
            old.unlink()
        failed = subprocess.call([sys.executable, "-m", "coverage", "run", f"--rcfile={COVERAGE_CONFIG}", __file__,
                                  args.folder, "-j", str(args.jobs)])
        return _covered() or failed
    folder = str(Path(args.folder).resolve())
    sys.path[:0] = [folder, str(Path(__file__).parent)]
    started = time.perf_counter()
    classes = sorted(_classes(Path(folder)), key=lambda name: not name.startswith(_SLOW_FIRST))
    tests, failures, skipped, times = 0, [], 0, {}
    # by its module's name, not __main__'s: a page run by a test swaps out the worker's __main__
    import run_tests
    with ProcessPoolExecutor(max_workers=max(1, min(args.jobs, len(classes)))) as pool:
        for future in as_completed([pool.submit(run_tests._run, folder, [name]) for name in classes]):
            ran, failed, skips, took = future.result()
            tests, skipped = tests + ran, skipped + skips
            failures += failed
            times.update(took)
    for report in failures:
        print("=" * 70, report, sep="\n")
    print("Slowest tests:")
    for name, seconds in sorted(times.items(), key=lambda kv: -kv[1])[:5]:
        print(f"  {seconds:5.1f}s  {name}")
    status = f"FAILED ({len(failures)})" if failures else "OK"
    print(f"Ran {tests} tests in {time.perf_counter() - started:.1f}s on {args.jobs} processes"
          f"{f', {skipped} skipped' if skipped else ''}: {status}")
    return 1 if failures or not tests else 0


if __name__ == "__main__":
    sys.exit(main())
