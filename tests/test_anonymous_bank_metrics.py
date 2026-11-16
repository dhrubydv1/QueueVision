import datetime as dt

from ai_engine.data.anonymous_bank_replay import Prediction
from ai_engine.evaluation.anonymous_bank_metrics import Frame, hang_report, little_law, method_metrics
from tests.test_anonymous_bank_replay import call

D = dt.date(1999, 1, 4)


def P(key, out, wait, pred, day=D):
    return Prediction(
        key,
        day,
        "PS",
        2,
        out,
        0,
        wait,
        wait,
        0,
        pred,
        None if pred is None else pred - 1,
        None if pred is None else pred + 1,
        None,
        None,
    )


def test_hang_excluded_from_errors_and_counted():
    preds = [
        P("a", "AGENT", 100, 90),
        P("b", "AGENT", 50, 60, dt.date(1999, 1, 5)),
        P("h", "HANG", 500, 100),
        P("g", "HANG", 20, 100),
    ]
    fr = Frame(preds, (0.0, 1.0))
    m = method_metrics(fr, "predictor")
    assert m["n_agent"] == 2 and m["n_covered"] == 2
    assert m["mae_s"]["value"] == 10.0 and m["bias_s"]["value"] == 0.0
    assert hang_report(fr) == {
        "n_hang": 2,
        "n_hang_with_prediction": 2,
        "n_hung_up_after_longer_than_predicted": 1,
    }


def test_little_law_exact_case():
    # One call queued for the whole of hour 1, one day: L = 1, lambda = 1/3600, W = 3600.
    ll = little_law({D: [call("a", 3600, 7200)]}, 1)
    assert abs(ll["1"]["time_avg_queue_length"] - 1.0) < 1e-12
    assert abs(ll["1"]["ratio_L_over_lambdaW"] - 1.0) < 1e-12
