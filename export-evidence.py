"""Build a standalone browser view of a recorded station restart."""

import argparse
import json
from pathlib import Path


def export(evidence, output):
    report = json.loads(evidence.read_text(encoding="utf-8"))
    if report.get("mode") != "station_restart" or report.get("verdict") != "comparison_verified":
        raise ValueError("Select a verified station restart report.")
    for policy in ("volatile", "durable"):
        row = report[policy]
        if len(row["readbacks"]) != 3 or len(row["station_restart"]["process_ids"]) != 2:
            raise ValueError(f"Missing station evidence for the {policy} policy.")

    assets = Path(__file__).resolve().parent / "dist"
    page = (assets / "index.html").read_text(encoding="utf-8")
    # Keep the standalone view usable with networking disabled.
    style = (assets / "style.css").read_text(encoding="utf-8")
    style = "\n".join(line for line in style.splitlines() if not line.startswith("@import"))
    script = (assets / "app.js").read_text(encoding="utf-8")
    data = json.dumps(report).replace("<", "\\u003c")
    policy = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
              "img-src data: blob:")
    page = page.replace('<link rel="stylesheet" href="style.css">', f'<style>{style}</style>')
    page = page.replace('  <script src="app.js" defer></script>\n', "")
    page = page.replace("<head>", f'<head>\n  <meta http-equiv="Content-Security-Policy" content="{policy}">')
    page = page.replace("</body>", f'<script id="embedded-evidence" type="application/json">{data}</script>\n'
                        f"<script>{script}</script>\n</body>")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        export(args.evidence, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"{error}\n")
