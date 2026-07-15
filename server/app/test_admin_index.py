"""Tests for the /admin index page (spec §3.2)."""

import re

from app import create_app


def test_admin_lists_admin_pages_alphabetized() -> None:
    client = create_app().test_client()

    response = client.get("/admin")

    assert response.status_code == 200
    links = re.findall(r'<a href="(/admin/[^"]*)">', response.text)
    assert links == ["/admin/images", "/admin/jokes", "/admin/meals", "/admin/weather"]


def test_admin_has_excludable_routes() -> None:
    """The exact-list assertion above only proves the index filters
    parameterized and POST-only /admin routes if such routes exist."""
    rules = [
        rule
        for rule in create_app().url_map.iter_rules()
        if rule.rule.startswith("/admin/")
    ]

    assert any(rule.arguments for rule in rules)
    assert any("GET" not in (rule.methods or ()) for rule in rules)
