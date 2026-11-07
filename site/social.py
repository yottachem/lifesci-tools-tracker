"""Render the social preview card (site/static/social.png, 1200x630) from the current panel.

Run locally after a data refresh that moves the scatter; CI doesn't have Chrome, so the PNG is committed.
Run: uv run python site/social.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import charts  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main():
    data = json.loads((HERE / "data" / "panel.json").read_text())
    pts = build.home_context(data["technologies"], data["meta"])["points"]
    svg = charts.scatter(pts, width=760, height=540, desc="Technologies by paper volume and five-year growth", char_w=8.4)
    html = Environment(loader=FileSystemLoader(HERE / "templates")).get_template("social.html.j2").render(
        meta=data["meta"], scatter=svg, site_url=build.SITE_URL)
    out = HERE / "static" / "social.png"
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "social.html"
        page.write_text(html)
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--window-size=1200,630", "--virtual-time-budget=4000", f"--screenshot={out}", page.as_uri()],
                       check=True, capture_output=True)
    print(out)


if __name__ == "__main__":
    main()
