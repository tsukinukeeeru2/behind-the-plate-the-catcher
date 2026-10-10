"""The 3D stadium page: the game seen through a catcher's mask.

The page is one HTML document with a canvas and a little HUD. Everything is drawn in the browser with plain JavaScript
(no libraries): see the web/ folder. After the first load the page talks to the server with fetch() (see api.py), so it never
reloads, and the background music keeps playing without a break.
"""

import os

from bgm_js import MUSIC_CORE

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_JS_PARTS = ("js_core.js", "js_scene.js", "js_draw.js", "js_game.js")
_cache = {}


def _read(name):
    with open(os.path.join(_DIR, name), encoding="utf-8") as fp:
        return fp.read()


def _assets():
    """(css, js) of the stadium page, read from web/ once (every time when the server runs with BTP_DEV=1)."""
    if not _cache or os.environ.get("BTP_DEV"):
        parts = _JS_PARTS + (("dev_tools.js",) if os.environ.get("BTP_DEV") else ())
        js = "\n".join(_read(n) for n in parts).replace("/*__MUSIC_CORE__*/", MUSIC_CORE)
        _cache["css"], _cache["js"] = _read("stadium.css"), js
    return _cache["css"], _cache["js"]


_BODY = """
<div id="app">
  <div id="top"></div>
  <div id="stage">
    <canvas id="view" aria-label="The field as seen by the catcher"></canvas>
    <canvas id="mask" aria-hidden="true"></canvas>
    <div id="batterbox" class="ov glass"></div>
    <div id="rightbox" class="ov"><div id="reactbox" class="glass"></div><div id="lastbox" class="glass"></div></div>
    <canvas id="mini" class="ov" width="250" height="162" aria-label="Top-down view of the play"></canvas>
    <div id="banner" class="ov"></div>
    <div id="hint" class="ov"></div>
    <div id="toast" class="ov"></div>
    <div id="qte"></div>
    <div id="drawer"></div>
    <div id="modal"><div class="mb" id="modal-body"></div></div>
  </div>
  <div id="bottom"></div>
</div>
<noscript><p style="position:fixed;top:40%;left:0;right:0;text-align:center;color:#fff">This game needs JavaScript.
<a href="/classic">Open the classic page</a>.</p></noscript>
"""


def render_stadium_page(classic_css=""):
    css, js = _assets()
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<meta name=\"color-scheme\" content=\"dark\">\n"
            "<title>Behind the Plate — A Catcher's Game</title>\n"
            "<style>" + classic_css + "</style>\n<style>" + css + "</style>\n</head><body>\n"
            + _BODY + "<script>" + js + "</script>\n</body></html>")
