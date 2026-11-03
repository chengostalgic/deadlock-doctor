"""Result-card structure/text through the supported kit; no real-browser claim."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = """
export const h = (tag, attrs = {}, ...children) => ({tag, attrs, children});
export const statusPill = (text) => h("span", {text});
export const disclosure = (options, ...children) => h("details", {}, options.summary, children);
export const dataTable = ({columns, rows}) => ({element: h("table", {}, rows.map(row => columns.map(column => row[column.key])))});
"""
DRAW = """
import {readFileSync} from "node:fs";
import renderers from "./renderers.mjs";
const sample = JSON.parse(readFileSync("sample.json", "utf8"));
function text(node) {
  if (node == null || node === false) return "";
  if (Array.isArray(node)) return node.map(text).join(" ");
  if (typeof node !== "object") return String(node);
  return (node.attrs?.text ?? "") + " " + text(node.children);
}
console.log(text(renderers.DeadlockOutput(sample.value, {verdict: sample.verdict})));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="result-card check needs Node")
@pytest.mark.parametrize(
    "case,valid,words",
    [
        ("required-three-cycle", True, "Rollback B for total cost 2; release L2"),
        ("self-cycle", True, "A → A (self-deadlock)"),
        ("blocked-behind", True, "Blocked behind cycles, outside the cycles: C, D, E"),
        ("acyclic-chain", True, "No deadlock"),
        ("mixed-unrecoverable", True, "No complete recovery plan or total cost is available"),
        ("large-exact-cost", True, "2000000000000000000000000000000000000000000000000000000000002"),
        ("required-three-cycle", False, "Verification failed"),
    ],
)
def test_result_card(case, valid, words, tmp_path):
    value = json.loads((ROOT / "tests/cases" / (case + ".expected.json")).read_text())
    source = (ROOT / "ui/renderers.js").read_text().replace("./kit/components.js", "./components.mjs")
    sample = {"value": value, "verdict": {"valid": valid, "message": "bad snapshot" if not valid else "checked"}}
    for filename, content in [
        ("renderers.mjs", source),
        ("components.mjs", KIT),
        ("draw.mjs", DRAW),
        ("sample.json", json.dumps(sample)),
    ]:
        (tmp_path / filename).write_text(content)
    result = subprocess.run(["node", "draw.mjs"], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert words in result.stdout
    if not valid:
        assert "Rollback B" not in result.stdout
    elif value["plan"] is None:
        assert "Recommended rollbacks:" not in result.stdout
    else:
        assert "Remaining wait-for graph" in result.stdout
