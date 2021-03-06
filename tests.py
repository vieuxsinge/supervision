from analyse_and_alert import Analyser, Anomaly
from pytest import raises


def test_check_temperature_convergence():
    analyser = Analyser()

    # Should not raise
    analyser.check_temperature_max(
        fermenter="f1", temperatures=[20, 21, 22, 23], max_temp=25
    )

    analyser.check_temperature_max(
        fermenter="f1", temperatures=[20, 21, 22, 23, 24, 25], max_temp=25
    )


def test_check_temperature_raises():
    analyser = Analyser()

    with raises(Anomaly) as excinfo:
        analyser.check_temperature_max(
            fermenter="f1", temperatures=[21, 22, 23, 24, 25.1], max_temp=25
        )
    assert excinfo.value.message == "temperature-exceeds-max"


def test_convergence_to_zero_does_not_raise():
    analyser = Analyser()
    analyser.check_temperature_convergence(
        fermenter="f1", temperatures=[20, 19.5, 18, 17, 17], is_cooling=True, setpoint=0
    )


def test_convergence_does_not_raise_on_contained_variations():
    analyser = Analyser()

    # Should not raise. What matters in the end is the delta between first and last values.
    analyser.check_temperature_convergence(
        fermenter="f1", temperatures=[20, 21, 18, 17, 17], is_cooling=True, setpoint=0,
    )


def test_convergence_to_zero_raises():
    analyser = Analyser()

    # Should raise.
    with raises(Anomaly) as excinfo:
        analyser.check_temperature_convergence(
            fermenter="f1",
            temperatures=[20, 20, 20.5, 20.6, 20.7],
            is_cooling=True,
            setpoint=0,
        )
    assert excinfo.value.message == "temperature-rising"


def test_convergence_to_20():
    analyser = Analyser()

    # Should raise.
    with raises(Anomaly) as excinfo:
        analyser.check_temperature_convergence(
            fermenter="f1",
            temperatures=[20, 20, 20.5, 20.6, 20.7],
            is_cooling=True,
            setpoint=20,
        )
    assert excinfo.value.message == "temperature-rising"


def test_oscillation_around_20():
    analyser = Analyser()

    # Should raise.
    with raises(Anomaly) as excinfo:
        analyser.check_temperature_convergence(
            fermenter="f1",
            temperatures=[19.5, 20, 20.5, 20, 20.5],
            is_cooling=True,
            setpoint=20,
        )
    assert excinfo.value.message == "temperature-rising"


def test_temperature_decreases_but_should_be_increasing():
    analyser = Analyser()

    # Should raise.
    with raises(Anomaly) as excinfo:
        analyser.check_temperature_convergence(
            fermenter="f1",
            temperatures=[20, 19.5, 19, 19, 19],
            is_cooling=False,
            setpoint=20,
        )
    assert excinfo.value.message == "temperature-falling"


def test_steady_temperature_should_not_raise():
    analyser = Analyser()

    # Should not raise.
    analyser.check_temperature_convergence(
        fermenter="f1",
        temperatures=[19, 19, 19, 19, 19],
        is_cooling=False,
        setpoint=20,
    )
