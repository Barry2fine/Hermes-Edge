"""Bug 3 (2026-10-29 review): a Propr entry with no position id must be flattened, not left naked."""
import autonomous_executor as ae


class FakeClient:
    def __init__(self, positions=None, close_raises=False):
        self.positions = positions or []
        self.close_raises = close_raises
        self.closed = []

    def get_open_positions(self, base=None):
        return self.positions

    def close_position(self, base, quote="USDC"):
        if self.close_raises:
            raise RuntimeError("api down")
        self.closed.append(base)
        return [{"orderId": "c1"}]


def test_missing_position_id_flattens_and_flags_review():
    c = FakeClient(positions=[])
    assert ae._find_position_id(c, "SOL", "long") is None
    rec = ae._flatten_entry_without_position_id(c, "SOL", "LONG", "1.5", {"orderId": "E1"})
    assert c.closed == ["SOL"]
    assert rec["ok"] is False and rec["requires_operator_review"] is True
    assert rec["emergency_flatten_ok"] is True and rec["entry_order_id"] == "E1"


def test_flatten_failure_is_recorded_not_raised():
    c = FakeClient(close_raises=True)
    rec = ae._flatten_entry_without_position_id(c, "SOL", "LONG", "1.5", {})
    assert rec["emergency_flatten_ok"] is False and rec["requires_operator_review"] is True


def test_position_lookup_matches_side_only():
    c = FakeClient(positions=[{"positionId": "S1", "positionSide": "short"},
                              {"positionId": "L1", "positionSide": "long"}])
    assert ae._find_position_id(c, "SOL", "long") == "L1"
    assert ae._find_position_id(FakeClient([{"positionId": "S1", "positionSide": "short"}]), "SOL", "long") is None


def test_executor_wires_fail_closed_branch():
    import inspect
    src = inspect.getsource(ae.manage_and_execute_propr)
    i = src.index("_find_position_id(client, asset, pos_side)")
    assert "_flatten_entry_without_position_id" in src[i:i + 400]
