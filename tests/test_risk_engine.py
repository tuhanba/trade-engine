from core.risk_engine import RiskEngine


class _Client:
    def futures_klines(self, symbol, interval, limit):
        rows = []
        price = 100.0
        for i in range(limit):
            o = price + i * 0.1
            h = o + 0.5
            l = o - 0.5
            c = o + 0.2
            rows.append([0, o, h, l, c, 1000, 0, 0, 0, 0, 0, 0])
        return rows


def test_risk_engine_outputs_candidate_metrics():
    engine = RiskEngine(_Client())
    res = engine.calculate("ETHUSDT", "LONG", 100.0, "A+", 1000.0)
    assert "estimated_fee" in res
    assert "net_rr" in res
