"""Unit tests for the matching engine.

The three required aspects are verified separately:
* ``TestOptimality``        — minimal total distance (incl. vs. brute force);
* ``TestTieBreak``          — deterministic adjudication between equal-cost
                              perfect matchings, independent of input order;
* ``TestToleranceBoundary`` — distance <= t pairs, distance > t does not.
"""

from __future__ import annotations

import itertools
import random
from collections import namedtuple

from app.matching import solve_matching

P = namedtuple("P", "id x y")


def brute_force_best(sockets, detections, tolerance):
    """Exhaustive search over all assignments; returns minimal feasible total
    cost or ``None`` when no perfect matching respects the tolerance."""
    n = len(sockets)
    best = None
    for perm in itertools.permutations(range(n)):
        total = 0
        for i, j in enumerate(perm):
            d = abs(sockets[i].x - detections[j].x) + abs(
                sockets[i].y - detections[j].y
            )
            if d > tolerance:
                break
            total += d
        else:
            if best is None or total < best:
                best = total
    return best


def assert_valid_matching(sockets, detections, tolerance, cost, pairs):
    """The returned pairs must be a genuine perfect matching within tolerance
    whose Manhattan distances add up to the reported cost."""
    s_map = {p.id: p for p in sockets}
    d_map = {p.id: p for p in detections}
    assert sorted(s for s, _ in pairs) == sorted(s_map)
    assert sorted(d for _, d in pairs) == sorted(d_map)
    total = 0
    for sid, did in pairs:
        d = abs(s_map[sid].x - d_map[did].x) + abs(s_map[sid].y - d_map[did].y)
        assert d <= tolerance
        total += d
    assert total == cost


class TestOptimality:
    def test_overlapping_tolerance_zones_need_global_optimum(self):
        # Upload-order greedy pairing would match S1->D1 (distance 9, allowed)
        # and then S2->D2 (distance 10) for a total of 19, misjudging the
        # tray.  The true optimum is 1.
        sockets = [P("S1", 0, 0), P("S2", 10, 0)]
        detections = [P("D1", 9, 0), P("D2", 0, 0)]
        result = solve_matching(sockets, detections, 10)
        assert result is not None
        cost, pairs = result
        assert cost == 1
        assert pairs == [("S1", "D2"), ("S2", "D1")]

    def test_matches_brute_force_on_random_instances(self):
        rng = random.Random(20260915)
        for _ in range(200):
            n = rng.randint(1, 7)
            sockets = [
                P(f"S{i}", rng.randint(0, 30), rng.randint(0, 30))
                for i in range(n)
            ]
            detections = [
                P(f"D{i}", rng.randint(0, 30), rng.randint(0, 30))
                for i in range(n)
            ]
            tolerance = rng.randint(0, 25)
            expected = brute_force_best(sockets, detections, tolerance)
            result = solve_matching(sockets, detections, tolerance)
            if expected is None:
                assert result is None
            else:
                assert result is not None
                cost, pairs = result
                assert cost == expected
                assert_valid_matching(sockets, detections, tolerance, cost, pairs)

    def test_larger_instance_matches_brute_force(self):
        rng = random.Random(80)
        n = 8
        sockets = [P(f"S{i}", rng.randint(0, 60), rng.randint(0, 60)) for i in range(n)]
        detections = [
            P(f"D{i}", rng.randint(0, 60), rng.randint(0, 60)) for i in range(n)
        ]
        tolerance = 40
        expected = brute_force_best(sockets, detections, tolerance)
        result = solve_matching(sockets, detections, tolerance)
        if expected is None:
            assert result is None
        else:
            assert result is not None
            assert result[0] == expected


class TestTieBreak:
    def test_equal_cost_matchings_pick_lexicographically_smallest(self):
        # Both detections sit at (1, 0): every perfect matching costs 2.
        # Socket order S1 < S2, and ("DA", "DB") < ("DB", "DA").
        sockets = [P("S1", 0, 0), P("S2", 2, 0)]
        detections = [P("DA", 1, 0), P("DB", 1, 0)]
        cost, pairs = solve_matching(sockets, detections, 10)
        assert cost == 2
        assert pairs == [("S1", "DA"), ("S2", "DB")]

    def test_tie_break_follows_socket_id_order(self):
        # Optimal cost 3 is reachable via (DA, DB, DC) or (DB, DA, DC);
        # the lexicographically smaller detection sequence wins.
        sockets = [P("S3", 4, 0), P("S1", 0, 0), P("S2", 2, 0)]
        detections = [P("DB", 1, 0), P("DC", 3, 0), P("DA", 1, 0)]
        cost, pairs = solve_matching(sockets, detections, 10)
        assert cost == 3
        assert pairs == [("S1", "DA"), ("S2", "DB"), ("S3", "DC")]

    def test_result_is_independent_of_input_order(self):
        rng = random.Random(7)
        # Small coordinate grid forces many equal-cost optima.
        sockets = [P(f"S{i}", rng.randint(0, 6), rng.randint(0, 6)) for i in range(8)]
        detections = [
            P(f"D{i}", rng.randint(0, 6), rng.randint(0, 6)) for i in range(8)
        ]
        tolerance = 6
        baseline = solve_matching(sockets, detections, tolerance)
        assert baseline is not None
        for _ in range(25):
            s_shuffled = list(sockets)
            d_shuffled = list(detections)
            rng.shuffle(s_shuffled)
            rng.shuffle(d_shuffled)
            assert solve_matching(s_shuffled, d_shuffled, tolerance) == baseline


class TestToleranceBoundary:
    def test_distance_equal_to_tolerance_is_accepted(self):
        sockets = [P("S1", 0, 0)]
        detections = [P("D1", 3, 4)]  # Manhattan distance 7
        cost, pairs = solve_matching(sockets, detections, 7)
        assert cost == 7
        assert pairs == [("S1", "D1")]

    def test_distance_one_above_tolerance_is_rejected(self):
        sockets = [P("S1", 0, 0)]
        detections = [P("D1", 3, 4)]  # Manhattan distance 7 > t = 6
        assert solve_matching(sockets, detections, 6) is None

    def test_zero_tolerance_requires_exact_overlap(self):
        sockets = [P("S1", 5, 5), P("S2", 6, 6)]
        exact = [P("D1", 6, 6), P("D2", 5, 5)]
        cost, pairs = solve_matching(sockets, exact, 0)
        assert cost == 0
        assert pairs == [("S1", "D2"), ("S2", "D1")]

        off_by_one = [P("D1", 6, 6), P("D2", 5, 4)]
        assert solve_matching(sockets, off_by_one, 0) is None

    def test_boundary_decides_feasibility_of_whole_tray(self):
        # S1 can only reach D1 at exactly distance t; if t drops by one the
        # whole tray becomes POSITION_MISMATCH even though S2/D2 are perfect.
        sockets = [P("S1", 0, 0), P("S2", 100, 100)]
        detections = [P("D1", 4, 3), P("D2", 100, 100)]
        assert solve_matching(sockets, detections, 7) is not None
        assert solve_matching(sockets, detections, 6) is None
