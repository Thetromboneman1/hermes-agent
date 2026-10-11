"""Regression tests for xAI provider label disambiguation."""

import pytest

from agent import models_dev
from hermes_cli.providers import get_label


@pytest.mark.parametrize("api_key_label", ["xAI", "Catalog xAI API"])
def test_xai_oauth_provider_label_is_not_collapsed_to_api_key_label(monkeypatch, api_key_label):
    """The model picker must distinguish xAI API-key and OAuth providers."""
    # API-key names come from models.dev; OAuth names are Hermes overlays.
    # Supply the catalog boundary so this does not depend on a live/cache hit.
    catalog = {"xai": {"name": api_key_label, "env": ["XAI_API_KEY"], "models": {}}}
    monkeypatch.setattr(models_dev, "fetch_models_dev", lambda: catalog)
    assert get_label("xai") == catalog["xai"]["name"]
    assert get_label("xai-oauth") == "xAI Grok OAuth (SuperGrok / Premium+)"
    assert get_label("grok-oauth") == "xAI Grok OAuth (SuperGrok / Premium+)"


