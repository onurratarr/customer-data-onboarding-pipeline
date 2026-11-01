from cdop import evaluate
from cdop.cli import main


def test_eval_reproduces_committed_results():
    r = evaluate.run_eval()
    assert r["label"].startswith("SYNTHETIC DATA")
    assert evaluate.check(r)
    dev = r["splits"]["dev"]["all"]
    assert dev["precision"] == 1.0 and dev["recall"] == 1.0  # dev split is what the lexicon was built on


def test_metrics_math():
    items = [{"gold": "a", "target": "a", "confidence": 0.9}, {"gold": "b", "target": "c", "confidence": 0.7},
             {"gold": None, "target": "d", "confidence": 0.5}, {"gold": "e", "target": None, "confidence": 0.3}]
    m = evaluate._metrics(items)
    assert (m["proposed"], m["correct"], m["precision"], m["recall"]) == (3, 1, 0.333, 0.333)
    m8 = evaluate._metrics(items, 0.8)
    assert (m8["precision"], m8["recall"], m8["decoys_left_unmapped"]) == (1.0, 0.333, 1)


def test_cli_lineage_and_eval_check(capsys):
    assert main(["lineage"]) == 0
    assert "flowchart LR" in capsys.readouterr().out
    assert main(["eval-mapping", "--check"]) == 0


def test_cli_demo(tmp_path, capsys):
    assert main(["--workdir", str(tmp_path), "demo"]) == 0
    out = capsys.readouterr().out
    assert "2026-06-21-r02  status=success" in out and "skipped (already loaded)" in out
