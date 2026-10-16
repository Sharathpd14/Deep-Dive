"""Copy generated evaluation results and the rejected-claim example into README.md.

  uv run python scripts/update_readme.py

Run the eval scripts first (they write eval/results.md), then
  uv run python scripts/rejected_example.py
(which writes docs/rejected-claim-example.md). Safe to run repeatedly.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
RESULTS = ROOT / "eval" / "results.md"
EXAMPLE = ROOT / "docs" / "rejected-claim-example.md"


def inject(text: str, name: str, body: str) -> str:
    pattern = re.compile(rf"(<!-- {name}:START -->).*?(<!-- {name}:END -->)", re.DOTALL)
    if not pattern.search(text):
        raise SystemExit(f"Marker {name} not found in README.md")
    return pattern.sub(lambda m: f"{m.group(1)}\n{body.strip()}\n{m.group(2)}", text)


def results_body(results_md: str) -> str:
    """Everything between the title and the Limitations section, with headings pushed down a level."""
    text = results_md.split("\n", 1)[1] if results_md.startswith("# ") else results_md
    text = text.split("## 5.")[0]
    return re.sub(r"^## ", "### ", text, flags=re.MULTILINE)


def example_body(example_md: str) -> str:
    return re.sub(r"^#{1,3} .*\n", "", example_md, count=1).strip() if example_md.startswith("#") else example_md


def main() -> None:
    text = README.read_text(encoding="utf-8")
    done = []
    if RESULTS.exists():
        text = inject(text, "RESULTS", results_body(RESULTS.read_text(encoding="utf-8")))
        done.append("results")
    else:
        print("eval/results.md not found: run the evaluation scripts first")
    if EXAMPLE.exists():
        text = inject(text, "EXAMPLE", example_body(EXAMPLE.read_text(encoding="utf-8")))
        done.append("rejected-claim example")
    else:
        print("docs/rejected-claim-example.md not found: run scripts/rejected_example.py first")
    README.write_text(text, encoding="utf-8")
    print("Updated README.md with: " + (", ".join(done) or "nothing"))


if __name__ == "__main__":
    main()