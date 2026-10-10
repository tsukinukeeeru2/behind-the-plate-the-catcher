"""Build the browser-only version of the game into docs/ (for GitHub Pages).

    python build_pages.py

It writes docs/index.html (the 3D page plus a loader), docs/game.zip (the Python game and its data) and docs/.nojekyll.
In the browser the Python runs through Pyodide, so there is no server to run or pay for. Run this again after changing the game.
"""

import os
import zipfile

import webapp

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs")
SKIP = {"tests.py", "build_pages.py", "main.py"}


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8", newline="\n") as fp:
        fp.write(webapp.render_stadium_page(webapp._classic_css(), static=True))
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    with zipfile.ZipFile(os.path.join(OUT, "game.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(ROOT)):
            if name.endswith(".py") and name not in SKIP:
                z.write(os.path.join(ROOT, name), name)
        for folder in ("web", "lineups", "mlb_data"):
            for dirpath, _dirs, files in os.walk(os.path.join(ROOT, folder)):
                for f in files:
                    if f.endswith((".py", ".js", ".css", ".json", ".csv")) and f != "dev_tools.js":
                        full = os.path.join(dirpath, f)
                        z.write(full, os.path.relpath(full, ROOT).replace("\\", "/"))
    print("built docs/index.html and docs/game.zip")


if __name__ == "__main__":
    main()
