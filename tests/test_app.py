"""Run the real app headless through every tab and widget. Unit tests
cannot catch a tab that only throws when it renders."""

import os

import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("LAB_OFFLINE", "1")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception
    return at


def _captions(at):
    return " ".join(c.value for c in at.caption)


def test_every_tab_renders(app):
    labels = [m.label for m in app.metric]
    for want in ("On loan", "Flat schedule exceedances",
                 "Net shortfall (indemnity pays)"):
        assert want in labels
    # notes are captions now, no metric carries a delta arrow
    assert all(not m.delta for m in app.metric)
    text = _captions(app)
    assert "17/17 validation checks" in text
    assert "Stressed window opens 18 Jun 2024" in text
    assert "Buying back costs" in text


def test_fire_drill_widgets(app):
    app.radio[0].set_value("orderly").run()
    assert not app.exception
    assert "Orderly close out" in _captions(app)
    app.radio[0].set_value("stressed").run()
    app.selectbox[0].set_value("B02").run()
    assert not app.exception
    assert "what Birch borrowed moved" in _captions(app)
