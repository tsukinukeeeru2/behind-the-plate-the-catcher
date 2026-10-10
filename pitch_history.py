"""The PitchHistory class that keeps the pitch history.

All it does is record each pitch thrown in order and answer pattern questions.
The judging itself is done by judge.py; this file is just a data holder.
(Thinking lives in batter.py, judging in judge.py, recording in pitch_history.py.)
"""

from constants import COURSE_SHORT
from pitch_data import guess_class_of, pitch_name


class PitchHistory:
    def __init__(self):
        self.records = []   # one dict per pitch, oldest first

    def clear(self):
        """Clear the history when the batter changes."""
        self.records = []

    def add(self, pitch_type, course, result, timing, swung,
            actual_course=None, intent=None, velocity=None, family=None, in_zone=None):
        """Record one pitch.

        course        : the location the catcher called
        actual_course : where the ball actually went (it can drift on a miss)
        family        : "fastball" / "breaking" / "offspeed" (3-way)
        pitch_class   : "fastball" / "offspeed" (a batter sits on one of two)
        """
        self.records.append({
            "pitch_type": pitch_type,
            "course": course,
            "actual_course": actual_course if actual_course is not None else course,
            "pitch_class": guess_class_of(pitch_type),
            "family": family,
            "velocity": velocity,
            "intent": intent,
            "result": result,
            "timing": timing,                        # "early" / "on_time" / "late"
            "swung": swung,
            "in_zone": in_zone,
        })

    def is_empty(self):
        return len(self.records) == 0

    def count(self):
        return len(self.records)

    def last(self):
        """The previous pitch's record, or None if there is none yet."""
        return self.records[-1] if self.records else None

    def same_type_streak(self):
        """How many pitches in a row, counting from the end, were the same pitch type."""
        return self._streak("pitch_type")

    def same_course_streak(self):
        """How many pitches in a row, counting from the end, hit the same location (actual location)."""
        return self._streak("actual_course")

    def family_streak(self):
        """How many pitches in a row, counting from the end, were the same family (fastball/breaking/offspeed)."""
        return self._streak("family")

    def _streak(self, key):
        if not self.records:
            return 0
        target = self.records[-1][key]
        streak = 0
        for record in reversed(self.records):
            if record[key] == target:
                streak += 1
            else:
                break
        return streak

    def recent_class_counts(self, n):
        """Count the pitch classes (fastball / offspeed) in the last n pitches."""
        counts = {"fastball": 0, "offspeed": 0}
        for record in self.records[-n:]:
            counts[record["pitch_class"]] += 1
        return counts

    def summary_text(self):
        """For display. Looks like '1.Four-seam/Out -> 2.Slider/Lo*' (* = missed the spot)."""
        if not self.records:
            return "(none yet)"
        parts = []
        for i, r in enumerate(self.records, start=1):
            loc = COURSE_SHORT.get(r["actual_course"], r["actual_course"])
            drift = "*" if r["actual_course"] != r["course"] else ""
            parts.append(f'{i}.{pitch_name(r["pitch_type"])}/{loc}{drift}')
        return " → ".join(parts)
