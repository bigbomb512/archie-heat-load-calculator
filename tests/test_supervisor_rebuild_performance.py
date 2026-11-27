"""The Phase 1 supervisor's rebuild, checked by counting work rather than timing it.

On a two-storey house job (2026-11-19) one supervisor rebuild took ~280 s, ~225 s of it in house plan registration:
every pair of equal dimension numbers on two plan sheets (~35,000 pairs) was tested against every other pair, six
times per Assemble. These pin that the shift votes are counted without the all-against-all test, and that the counts
and the registration are exactly what the all-against-all test gives.
"""

import builtins
import math
import random
from statistics import median
import unittest
from unittest.mock import patch

from ai import house_envelope


def _brute_force_registration(source_words, target_words):
    """plan_text_registration as it was before 2026-11-19 (every pair tested against every pair): the reference."""
    source = house_envelope._registration_tokens(source_words)
    target = house_envelope._registration_tokens(target_words)
    pairs = [(bx - ax, by - ay, key, ax, ay) for key in source.keys() & target.keys()
             for ax, ay in source[key] for bx, by in target[key]]
    if not pairs:
        return None
    tol = house_envelope.REGISTRATION_TOLERANCE_PT

    def cluster(dx, dy):
        return [row for row in pairs if abs(row[0] - dx) <= tol and abs(row[1] - dy) <= tol]

    ranked = sorted(((len(cluster(row[0], row[1])), row[0], row[1]) for row in pairs), key=lambda row: (-row[0], row[1], row[2]))
    _votes, best_dx, best_dy = ranked[0]
    best = cluster(best_dx, best_dy)
    dx, dy = median(row[0] for row in best), median(row[1] for row in best)
    best = cluster(dx, dy)
    keys = {row[2] for row in best}
    runner_up = next((row[0] for row in ranked if math.hypot(row[1] - dx, row[2] - dy) > 3 * tol), 0)
    if (len(best) < house_envelope.MIN_REGISTRATION_VOTES or len(keys) < house_envelope.MIN_REGISTRATION_TOKENS
            or runner_up * 2 > len(best)
            or max(row[3] for row in best) - min(row[3] for row in best) < 100
            or max(row[4] for row in best) - min(row[4] for row in best) < 100):
        return None
    return {"dx_pt": dx, "dy_pt": dy, "votes": len(best), "runner_up": runner_up,
            "anchors": sorted(keys, key=lambda key: (len(key) != 1, -len(key), key))}


def _sheet(rng, shift=(0.0, 0.0), repeats=6, jitter=0.3):
    """A plan sheet's dimension figures and grid letters, each printed several times, moved by shift."""
    words = []
    texts = [f"{rng.randint(1, 9)},{rng.randint(100, 999)}" for _ in range(40)] + list("ABCDEFGH")
    for text in texts:
        for _ in range(repeats):
            x, y = rng.uniform(50, 1600), rng.uniform(50, 1100)
            words.append({"text": text, "bbox": [x, y, x + 9, y + 12]})
    return [{"text": row["text"], "bbox": [row["bbox"][0] + shift[0] + rng.uniform(-jitter, jitter),
                                            row["bbox"][1] + shift[1] + rng.uniform(-jitter, jitter),
                                            row["bbox"][2] + shift[0], row["bbox"][3] + shift[1]]} for row in words]


class _CountingAbs:
    def __init__(self):
        self.calls = 0

    def __call__(self, value):
        self.calls += 1
        return builtins.abs(value)


class HousePlanRegistrationCountsVotesWithoutTheAllPairsTest(unittest.TestCase):
    def test_cluster_sizes_are_the_all_pairs_counts(self):
        rng = random.Random(20261119)
        tol = house_envelope.REGISTRATION_TOLERANCE_PT
        for _trial in range(200):
            # Shifts on and next to the tolerance's cell edges, repeated values and plain random ones.
            values = lambda: rng.choice([rng.uniform(-6, 6), float(rng.randint(-4, 4)), rng.randint(-4, 4) + 0.5,
                                         round(rng.uniform(-6, 6), 1), rng.randint(-4, 4) - 1e-12])
            pairs = [(values(), values(), "k", 0.0, 0.0) for _ in range(rng.randint(1, 80))]
            expected = [sum(1 for other in pairs if abs(other[0] - row[0]) <= tol and abs(other[1] - row[1]) <= tol)
                        for row in pairs]
            self.assertEqual(house_envelope._cluster_sizes(pairs), expected)

    def test_registration_is_the_all_pairs_result(self):
        rng = random.Random(7)
        for shift in ((0.0, 0.0), (12.5, -3.25), (-40.0, 18.0)):
            source = _sheet(random.Random(rng.random()))
            target = _sheet(random.Random(rng.random())) + [
                {"text": row["text"], "bbox": [row["bbox"][0] + shift[0], row["bbox"][1] + shift[1],
                                               row["bbox"][2] + shift[0], row["bbox"][3] + shift[1]]} for row in source]
            result = house_envelope.plan_text_registration(source, target)
            self.assertEqual(result, _brute_force_registration(source, target))
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result["dx_pt"], shift[0], places=6)
        unrelated = _sheet(random.Random(1)), _sheet(random.Random(2))
        self.assertEqual(house_envelope.plan_text_registration(*unrelated), _brute_force_registration(*unrelated))

    def test_votes_are_counted_without_testing_every_pair_against_every_other(self):
        rng = random.Random(3)
        source = _sheet(rng, repeats=12)
        target = [{"text": row["text"], "bbox": [row["bbox"][0] + 25.0, row["bbox"][1] - 10.0,
                                                 row["bbox"][2] + 25.0, row["bbox"][3] - 10.0]} for row in source]
        tokens = house_envelope._registration_tokens(source), house_envelope._registration_tokens(target)
        pairs = sum(len(tokens[0][key]) * len(tokens[1][key]) for key in tokens[0].keys() & tokens[1].keys())
        self.assertGreater(pairs, 5000)
        counter = _CountingAbs()
        with patch.object(house_envelope, "abs", counter, create=True):
            house_envelope.plan_text_registration(source, target)
        # The all-pairs test made 2 comparisons per pair per pair (2 × pairs², ~95 million here); the grid compares a
        # pair only with the pairs of shift near its own (here ~0.7 million, nearly all inside the true shift's cell).
        self.assertLess(counter.calls, pairs * pairs / 20)


if __name__ == "__main__":
    unittest.main()
