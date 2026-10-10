# Devpost submission text: Behind the Plate

## Tagline
You are the catcher. Call every pitch, frame the borderline ones, throw out the runner, and hold the inning, with no answers shown.

## Inspiration
I played baseball for ten years, mostly as a catcher. A catcher decides every pitch with incomplete information: you read the batter, feel the runner leaning, and build the next pitch while setting the pitcher's rhythm. Most baseball games make you a batter or a pitcher. I wanted the one position nobody simulates.

## What it does
Behind the Plate drops you into a late-inning, one-half-inning situation: score, runners, outs, a new batter each time. You get three outs to hold the lead.
Every pitch you pick a pitch type, a spot on a 3×3 zone, and an intent (get ahead, chase, induce weak contact, waste, freeze, pitchout).
The game never tells you what was right. You read the batter's face (which sometimes lies), the pitch history, the batter's hot and weak zones, and your defense.
Then the catching starts: frame borderline pitches, throw down on steals, block balls in the dirt, field bunts, change signs when a runner peeks, and keep a steady return-throw tempo. These timing mini-games only nudge the odds, never decide the play.
The main screen is **the view from behind the plate, through a catcher's mask**: a first-person 3D stadium on one screen (no scrolling) with the batter and his bat right beside you, a pitcher with a full-body delivery (either hand), a ball flown by gravity, drag and spin (the Magnus force), a strike zone you click in the 3D scene, and your mitt at the target. The ball is tiny far away and fills your view as it arrives, and it ends with a pop in the mitt. After each pitch you see the result plus a one-line grade of your call and why. Every ball in play is replayed with the fielder running it down, the throw, and the runners, plus a small top-down field view.
After the third out you get a **Catcher Rating (S to D)**, badges, and a review that grades your decisions separately from the results: "good call, unlucky result" versus "bad call, lucky result".
Start from a random game, a **drill** (steal, dirt ball, dropped third, bunt, sign stealer), or a friend's **Challenge Code** to play the exact same opening situation.

## How we built it
Pure Python standard library, no dependencies. A single engine (`engine.resolve_one_pitch`) judges every pitch and is shared by the terminal and web versions. The web version is a small `http.server` app with a little JavaScript for the timing games and a Web Audio soundtrack synthesized in the browser. Randomness is seeded end to end, which makes games reproducible and shareable. 183 unit tests, including 1000 simulated full games, plus a fuzz run of 120 web games with junk input.

## Built before vs. during the hackathon (honest disclosure)
- **Before:** a Japanese-language catcher simulator I wrote as a personal project: engine, models, judging, CLI and web versions, the six rhythm games, music, tests. The organizers confirmed that rebuilding on it was fine. It is the first commit of the repo ("Base snapshot"), so `git log` shows exactly what came after.
- **During the hackathon:** full English conversion of the whole game; the catcher's-eye 3D view with its JSON API and a music player that never restarts; the animated Play Viewer; Catcher Rating and badges; drills; Challenge Codes; difficulty select; a threaded, crash-resistant server with a recovery page and port fallback; auto-opening browser; a fuzz pass that found and fixed a real crash; new tests; README and docs.
- **AI use:** Claude Code (Anthropic) helped with the translation and the new features, and with the original project. I directed it, tested it, and made the design calls. Co-authored commits are marked.

## Challenges
Keeping the answer hidden while still being fair (a hidden decision-quality score that never appears during play). Making timing mini-games feel good without letting them override the baseball. Translating a whole game, including tests and internal keys, without breaking anything.

## Accomplishments we're proud of
A catcher game that feels like catching: the reads, the framing, the throw down. And a review screen that tells you whether you were right or just lucky.

## What we learned
Single sources of truth (one engine, one RNG, one place for hidden state) made the translation, the new modes, and the fuzzing cheap.

## What's next
More pitchers and lineups (real-data adapter is already in place), a full game instead of one half inning, and a daily challenge code.

## Built with
Python, http.server, HTML/CSS/JavaScript, Web Audio API, unittest, GitHub Actions

## Try it
```
python webapp.py
```
(Python 3.11+, nothing to install.)

---

# 2-minute demo video script

| Time | On screen | Say |
|---|---|---|
| 0:00 | Start screen | "Most baseball games make you the batter or the pitcher. This one makes you the catcher. It's Behind the Plate." |
| 0:10 | START GAME, situation card | "Late innings, a one-run lead, runner on first. Three outs to hold it." |
| 0:20 | Pitch call: pick pitch, click the zone, pick intent | "Every pitch I pick the pitch, click the spot in the 3D zone, and choose my intent. The mitt moves to my target." |
| 0:35 | Batter reaction face + history table | "I read his reaction, the pitch history, his weak zone. Faces can lie." |
| 0:45 | Framing mini-game | "Now I'm the catcher: stop the line in the green band to frame a borderline pitch into a strike." |
| 1:00 | Steal, throw-down game | "He's going! Transfer, mash, throw to second." |
| 1:10 | Block in the dirt / sign change | "Ball in the dirt: get my body over, then block. Runner peeking at my signs: change them in time." |
| 1:20 | Ball in play: the Play Viewer replay | "Every ball in play is replayed on a little board: the arc, the fielder running it down, the throw, the runners. Slow-mo too." |
| 1:30 | Third out, report with rating | "Three outs. I get a Catcher Rating, badges, and a review that separates my decisions from my luck." |
| 1:40 | Start screen drills + Challenge Code | "Drill the skills you want, or type a friend's Challenge Code to play the exact same situation." |
| 1:50 | Terminal `python webapp.py`, README | "Pure Python, no dependencies, one command. It started from my earlier Japanese prototype; the English version and these features were built this weekend. Thanks!" |
