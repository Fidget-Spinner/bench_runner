import contextlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


import pytest


from bench_runner import git
from bench_runner.scripts import generate_results
from bench_runner.scripts import run_benchmarks
from bench_runner.scripts import should_run
from bench_runner import util


DATA_PATH = Path(__file__).parent / "data"


def _copy_repo(tmp_path):
    repo_path = tmp_path / "repo"
    shutil.copytree(DATA_PATH, repo_path)
    return repo_path


@pytest.fixture
def benchmarks_checkout(request):
    root = request.config.cache.mkdir("benchmarking-checkouts")
    if not (root / "cpython").is_dir():
        subprocess.check_call(
            [
                "git",
                "clone",
                "https://github.com/python/cpython",
                "--depth",
                "1",
                "--branch",
                "v3.10.4",
            ],
            cwd=root,
        )

    if not (root / "pyston-benchmarks").is_dir():
        subprocess.check_call(
            [
                "git",
                "clone",
                "https://github.com/mdboom/python-macrobenchmarks",
                "--depth",
                "1",
                "--branch",
                "benchmarking-test",
                "pyston-benchmarks",
            ],
            cwd=root,
        )

    if not (root / "pyperformance").is_dir():
        subprocess.check_call(
            [
                "git",
                "clone",
                "https://github.com/mdboom/pyperformance",
                "--depth",
                "1",
                "--branch",
                "benchmarking-test",
            ],
            cwd=root,
        )

    venv_dir = root / "venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_dir.is_dir():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call([sys.executable, "-m", "venv", venv_dir], cwd=root)
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", "setuptools"], cwd=root
        )
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", root / "pyperformance"], cwd=root
        )

    # Always do this step, since it depends on the current codebase
    subprocess.check_call(
        [venv_python, "-m", "pip", "install", Path(__file__).parents[1]], cwd=root
    )

    return root


def test_update_metadata(tmp_path, benchmarks_checkout):
    for dirname in ["cpython"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)

    shutil.copy(
        DATA_PATH
        / "results"
        / "bm-20211208-3.11.0a3-2e91dba"
        / "bm-20211208-linux-x86_64-python-main-3.11.0a3-2e91dba.json",
        tmp_path / "benchmarks.json",
    )
    run_benchmarks.update_metadata(
        tmp_path / "benchmarks.json",
        "myfork",
        "myref",
        tmp_path / "cpython",
        "12345",
    )

    with open(tmp_path / "benchmarks.json") as fd:
        content = json.load(fd)

    metadata = content["metadata"]

    assert metadata["commit_id"] == "9d38120"
    assert metadata["commit_fork"] == "myfork"
    assert metadata["commit_branch"] == "myref"
    assert metadata["commit_date"] == "2022-03-23T20:12:04Z"
    assert "commit_merge_base" not in metadata
    assert metadata["benchmark_hash"] == "215d35"
    assert (
        metadata["github_action_url"]
        == "https://github.com/faster-cpython/bench_runner/actions/runs/12345"
    )


def test_run_benchmarks(tmp_path, benchmarks_checkout):
    for dirname in ["cpython", "pyperformance", "pyston-benchmarks", "venv"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)
    shutil.copyfile(DATA_PATH / "runners.ini", tmp_path / "runners.ini")

    venv_dir = tmp_path / "venv"
    venv_python = venv_dir / "bin" / "python"

    shutil.copy(
        DATA_PATH / "benchmarks.manifest",
        tmp_path / "benchmarks.manifest",
    )

    # Now actually run the run_benchmarks.py script
    subprocess.check_call(
        [
            venv_python,
            run_benchmarks.__file__,
            "benchmark",
            sys.executable,
            "python",
            "main",
            "deepcopy",
            "--test_mode",
            "--run_id",
            "12345",
        ],
        cwd=tmp_path,
    )

    with open(
        tmp_path
        / "results"
        / f"bm-20220323-{platform.python_version()}-9d38120"
        / f"bm-20220323-{platform.system().lower()}-{platform.machine()}-"
        f"python-main-{platform.python_version()}-9d38120.json"
    ) as fd:
        content = json.load(fd)

    metadata = content["metadata"]
    benchmarks = content["benchmarks"]

    assert metadata["commit_id"] == "9d38120"
    assert metadata["commit_fork"] == "python"
    assert metadata["commit_branch"] == "main"
    assert metadata["commit_date"] == "2022-03-23T20:12:04Z"
    assert "commit_merge_base" not in metadata
    assert metadata["benchmark_hash"] == "215d35"
    assert (
        metadata["github_action_url"]
        == "https://github.com/faster-cpython/bench_runner/actions/runs/12345"
    )

    assert len(benchmarks) == 3
    assert all(len(benchmark["runs"]) > 1 for benchmark in benchmarks)
    assert set(bm["metadata"]["name"] for bm in benchmarks) == {
        "deepcopy",
        "deepcopy_memo",
        "deepcopy_reduce",
    }

    # Run an unknown benchmark, expect an error
    returncode = subprocess.call(
        [
            venv_python,
            run_benchmarks.__file__,
            "benchmark",
            sys.executable,
            "python",
            "main",
            "foo",
            "--run_id",
            "12345",
        ],
        cwd=tmp_path,
    )
    assert returncode == 1


def test_should_run_exists_noforce(tmp_path, benchmarks_checkout, capsys):
    repo = _copy_repo(tmp_path)
    for dirname in ["cpython"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)

    should_run._main(
        False,
        "python",
        "main",
        "linux-x86_64-linux",
        False,
        False,
        False,
        False,
        tmp_path / "cpython",
        repo / "results",
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == "should_run=false"
    assert (repo / "results" / "bm-20220323-3.10.4-9d38120").is_dir()


def test_should_run_diff_machine_noforce(tmp_path, benchmarks_checkout, capsys):
    repo = _copy_repo(tmp_path)
    for dirname in ["cpython"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)

    should_run._main(
        False,
        "python",
        "main",
        "darwin-x86_64-darwin",
        False,
        False,
        False,
        False,
        tmp_path / "cpython",
        repo / "results",
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == "should_run=true"
    assert len(list((repo / "results" / "bm-20220323-3.10.4-9d38120").iterdir())) == 1


def test_should_run_all_noforce(tmp_path, benchmarks_checkout, capsys):
    repo = _copy_repo(tmp_path)
    for dirname in ["cpython"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)

    should_run._main(
        False,
        "python",
        "main",
        "all",
        False,
        False,
        False,
        False,
        tmp_path / "cpython",
        repo / "results",
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == "should_run=true"
    assert len(list((repo / "results" / "bm-20220323-3.10.4-9d38120").iterdir())) == 1


def test_should_run_noexists_noforce(tmp_path, benchmarks_checkout, capsys):
    repo = _copy_repo(tmp_path)
    for dirname in ["cpython"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)
    shutil.rmtree(repo / "results" / "bm-20220323-3.10.4-9d38120")

    should_run._main(
        False,
        "python",
        "main",
        "linux-x86_64-linux",
        False,
        False,
        False,
        False,
        tmp_path / "cpython",
        repo / "results",
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == "should_run=true"
    assert not (repo / "results" / "bm-20220323-3.10.4-9d38120").is_dir()


def test_should_run_exists_force(tmp_path, benchmarks_checkout, capsys, monkeypatch):
    repo = _copy_repo(tmp_path)
    for dirname in ["cpython"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)

    removed_paths = []

    def remove(repo, path):
        nonlocal removed_paths
        removed_paths.append(path)
        (repo / path).unlink()

    monkeypatch.setattr(git, "remove", remove)

    with contextlib.chdir(repo):
        generate_results._main(repo, force=False, bases=["3.11.0b3"])
        should_run._main(
            True,
            "python",
            "main",
            "linux-x86_64-linux",
            False,
            False,
            False,
            False,
            tmp_path / "cpython",
            repo / "results",
        )

    captured = capsys.readouterr()
    assert captured.out.splitlines()[-1].strip() == "should_run=true"
    assert (repo / "results" / "bm-20220323-3.10.4-9d38120").is_dir()
    assert set(x.name for x in removed_paths) == {
        "bm-20220323-linux-x86_64-python-main-3.10.4-9d38120-vs-3.11.0b3.png",
        "README.md",
        "bm-20220323-linux-x86_64-python-main-3.10.4-9d38120-vs-3.11.0b3.md",
    }


def test_should_run_noexists_force(tmp_path, benchmarks_checkout, capsys):
    repo = _copy_repo(tmp_path)
    for dirname in ["cpython"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)
    shutil.rmtree(repo / "results" / "bm-20220323-3.10.4-9d38120")

    should_run._main(
        True,
        "python",
        "main",
        "linux-x86_64-linux",
        False,
        False,
        False,
        False,
        tmp_path / "cpython",
        repo / "results",
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == "should_run=true"
    assert not (repo / "results" / "bm-20220323-3.10.4-9d38120").is_dir()


def test_should_run_checkout_failed(tmp_path, capsys):
    repo = _copy_repo(tmp_path)
    cpython_path = tmp_path / "cpython"
    cpython_path.mkdir()
    subprocess.check_call(["git", "init"], cwd=cpython_path)

    with pytest.raises(SystemExit):
        should_run._main(
            True,
            "python",
            "main",
            "linux-x86_64-linux",
            False,
            False,
            False,
            False,
            cpython_path,
            repo / "results",
        )

    captured = capsys.readouterr()
    assert "The checkout of cpython failed" in captured.err
    assert "You specified fork 'python' and ref 'main'" in captured.err


def test_run_benchmarks_flags(tmp_path, benchmarks_checkout):
    for dirname in ["cpython", "pyperformance", "pyston-benchmarks", "venv"]:
        shutil.copytree(benchmarks_checkout / dirname, tmp_path / dirname)
    shutil.copyfile(DATA_PATH / "runners.ini", tmp_path / "runners.ini")

    venv_dir = tmp_path / "venv"
    venv_python = venv_dir / "bin" / "python"

    shutil.copy(
        DATA_PATH / "benchmarks.manifest",
        tmp_path / "benchmarks.manifest",
    )

    # Now actually run the run_benchmarks.py script
    subprocess.check_call(
        [
            venv_python,
            run_benchmarks.__file__,
            "benchmark",
            sys.executable,
            "python",
            "main",
            "deepcopy",
            "--test_mode",
            "--run_id",
            "12345",
            "--flag",
            "PYTHON_UOPS",
        ],
        cwd=tmp_path,
    )

    with open(
        tmp_path
        / "results"
        / f"bm-20220323-{platform.python_version()}-9d38120-PYTHON_UOPS"
        / f"bm-20220323-{platform.system().lower()}-{platform.machine()}-"
        f"python-main-{platform.python_version()}-9d38120.json"
    ) as fd:
        json.load(fd)


def test_get_benchmark_hash():
    assert util.get_benchmark_hash() == "215d35"
