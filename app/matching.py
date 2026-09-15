"""Minimum-cost one-to-one perfect matching between expected sockets and
detected chip centers.

The solver is implemented from scratch (Hungarian algorithm, O(n^3)) and is
deterministic: it only depends on the point ids / coordinates / tolerance,
never on the order in which points appear in the uploaded file.

Tie-breaking rule (required by the inspection spec)
---------------------------------------------------
Among all perfect matchings that share the same minimal total Manhattan
distance, sort the sockets by id ascending and pick the matching whose
corresponding sequence of detection ids is lexicographically smallest.

This is encoded exactly into a single integer cost per edge:

* sockets are sorted by id ascending -> positions ``p = 0 .. n-1``;
* detections are sorted by id ascending -> ranks ``r = 0 .. n-1``;
* with ``B = n`` and ``M = B ** n`` the combined cost of pairing socket
  position ``p`` with detection rank ``r`` at Manhattan distance ``d`` is

      cost = d * M + r * B ** (n - 1 - p)

The sum of the tie-break terms over any matching is a base-``B`` number whose
digits are the detection ranks in socket-id order, and it is always ``< M``.
Therefore minimising the combined cost minimises the total distance first and
then the detection-id sequence lexicographically.  Python integers have
arbitrary precision, so the encoding is exact for any ``n <= 80``.

Edges whose Manhattan distance exceeds the tolerance are forbidden by charging
them ``INF_EDGE``, which is strictly greater than the total cost of any
matching that only uses allowed edges.  If the optimum ends up >= ``INF_EDGE``
no feasible perfect matching exists.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence


class _Point(Protocol):
    id: str
    x: int
    y: int


# Big constant used only inside the Hungarian routine for initialisation.
# Any real cost is < 2**600, so 2**1200 is safely "infinite".
_BIG = 1 << 1200


def _hungarian(cost: list[list[int]], n: int) -> tuple[list[int], int]:
    """Classic O(n^3) Hungarian algorithm for a square minimisation problem.

    ``cost`` is 1-indexed: rows/columns 1..n are used, index 0 is padding.
    Returns ``(assignment, total_cost)`` where ``assignment[i] = j`` means row
    ``i`` is matched to column ``j``.
    """
    u = [0] * (n + 1)  # row potentials
    v = [0] * (n + 1)  # column potentials
    p = [0] * (n + 1)  # p[j] = row matched to column j
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [_BIG] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = _BIG
            j1 = 0
            row = cost[i0]
            ui0 = u[i0]
            for j in range(1, n + 1):
                if not used[j]:
                    cur = row[j] - ui0 - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        # Augment along the alternating path.
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    assignment = [0] * (n + 1)
    for j in range(1, n + 1):
        assignment[p[j]] = j
    return assignment, -v[0]


def solve_matching(
    sockets: Sequence[_Point],
    detections: Sequence[_Point],
    tolerance: int,
) -> Optional[tuple[int, list[tuple[str, str]]]]:
    """Solve the inspection matching problem.

    Both sequences must have equal length ``n``.  Returns ``None`` when no
    perfect matching respecting the tolerance exists; otherwise returns
    ``(total_cost, pairs)`` where ``pairs`` is a list of
    ``(socket_id, detection_id)`` tuples ordered by socket id ascending —
    the unique optimum defined by the tie-breaking rule.
    """
    n = len(sockets)
    if n != len(detections):
        raise ValueError("socket and detection counts must be equal")
    if n == 0:
        return 0, []

    s_sorted = sorted(sockets, key=lambda pt: pt.id)
    d_sorted = sorted(detections, key=lambda pt: pt.id)

    base = n
    modulus = base ** n  # M: strictly greater than any tie-break encoding
    weights = [base ** (n - 1 - p) for p in range(n)]
    # Strictly greater than the total cost of any all-allowed matching:
    # distance part <= n * tolerance * M, tie-break part < M.
    inf_edge = (n * tolerance + 1) * modulus

    cost = [[0] * (n + 1) for _ in range(n + 1)]
    dist = [[0] * (n + 1) for _ in range(n + 1)]
    for i, s in enumerate(s_sorted, start=1):
        w = weights[i - 1]
        cost_row = cost[i]
        dist_row = dist[i]
        for j, d in enumerate(d_sorted, start=1):
            manhattan = abs(s.x - d.x) + abs(s.y - d.y)
            dist_row[j] = manhattan
            if manhattan <= tolerance:
                cost_row[j] = manhattan * modulus + (j - 1) * w
            else:
                cost_row[j] = inf_edge

    assignment, best = _hungarian(cost, n)
    if best >= inf_edge:
        return None

    total = 0
    pairs: list[tuple[str, str]] = []
    for i in range(1, n + 1):
        j = assignment[i]
        total += dist[i][j]
        pairs.append((s_sorted[i - 1].id, d_sorted[j - 1].id))
    return total, pairs
