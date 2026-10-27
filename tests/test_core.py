import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.core.bot import Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402


class A(Bot):
    id = "a"; veto = False
    def evaluate(self, ctx): return Vote("a", "approve", 0.8, ["ok"])

class R(Bot):
    id = "r"; veto = False
    def evaluate(self, ctx): return Vote("r", "reject", 0.9, ["bad"])

class V(Bot):
    id = "v"; veto = True
    def evaluate(self, ctx): return Vote("v", "reject", 0.9, ["no"])

class Vok(Bot):
    id = "vok"; veto = True
    def evaluate(self, ctx): return Vote("vok", "approve", 0.7, ["fine"])

class Ab(Bot):
    id = "ab"; veto = False
    def evaluate(self, ctx): return Vote("ab", "abstain", 0.0, ["unknown"])

class Boom(Bot):
    id = "boom"; veto = True
    def evaluate(self, ctx): raise RuntimeError("x")


class CoordinatorTests(unittest.TestCase):
    # These bots are wiring fixtures, not an edge model: A/Vok declare no
    # `edge_seat`, so the pack is decided with the edge gate explicitly off
    # (require_edge=False). test_model_maths_bugs.py owns the gate's own tests.
    def test_allow(self):
        self.assertEqual(decide([A(), Vok()], {}, require_edge=False)["decision"],
                         "ALLOW")

    def test_non_veto_reject(self):
        self.assertEqual(decide([A(), R()], {})["decision"], "REJECT")

    def test_veto_wins(self):
        self.assertEqual(decide([A(), V(), A()], {})["decision"], "VETO")

    def test_abstain_only_waits(self):
        self.assertEqual(decide([Ab()], {})["decision"], "WAIT")

    def test_quorum(self):
        self.assertEqual(decide([A()], {}, quorum=3)["decision"], "WAIT")

    def test_failing_veto_rejects_failclosed(self):
        self.assertEqual(decide([A(), Boom()], {})["decision"], "VETO")

    def test_votes_hash_stable(self):
        r1 = decide([A(), Vok()], {})
        r2 = decide([A(), Vok()], {})
        self.assertEqual(r1["votes_hash"], r2["votes_hash"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
