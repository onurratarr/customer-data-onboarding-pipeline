import shutil
from pathlib import Path

import pytest

from cdop import pipeline, synth
from cdop.warehouse import DuckDBWarehouse

ROOT = Path(__file__).resolve().parents[1]


def no_sleep(_):
    pass


@pytest.fixture(scope="session")
def demo_data(tmp_path_factory):
    work = tmp_path_factory.mktemp("demo")
    synth.write_demo(work / "data")
    return work


@pytest.fixture
def fresh_work(demo_data, tmp_path):
    work = tmp_path / "work"
    shutil.copytree(demo_data / "data", work / "data")
    return work


def open_wh(work):
    wh = DuckDBWarehouse(work / "warehouse.duckdb")
    wh.init_schema()
    return wh


@pytest.fixture(scope="session")
def demo_runs(demo_data, tmp_path_factory):
    """day 1, day 2 and a re-run of day 2 on DuckDB, as in `python -m cdop demo`."""
    work = tmp_path_factory.mktemp("runs")
    shutil.copytree(demo_data / "data", work / "data")
    wh = open_wh(work)
    runs = [pipeline.run(d, wh, work, sleep=no_sleep) for d in [*synth.DEMO_AS_OF, synth.DEMO_AS_OF[-1]]]
    yield work, wh, runs
    wh.close()
