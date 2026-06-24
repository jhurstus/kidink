from flask import render_template_string

from app import create_app


def test_comic_panel_call_block_renders_children() -> None:
    # comic_panel takes its contents via {% call %} transclusion (caller()),
    # rendered into the .comic-content layer — there is no content= param.
    app = create_app()
    with app.app_context():
        html = render_template_string(
            "{% from 'macros/comic.html' import comic_panel %}"
            "{% call comic_panel(width=100, height=50, border={'seed': 1}) %}"
            "HELLO_CHILD{% endcall %}"
        )

    assert 'class="comic-content"' in html
    assert "HELLO_CHILD" in html


def test_comic_panel_without_call_block_has_no_content_layer() -> None:
    # A plain {{ comic_panel(...) }} call (no block) renders no content layer.
    app = create_app()
    with app.app_context():
        html = render_template_string(
            "{% from 'macros/comic.html' import comic_panel %}"
            "{{ comic_panel(width=100, height=50, border={'seed': 1}) }}"
        )

    assert 'class="comic-content"' not in html


def test_panel_route_renders() -> None:
    client = create_app().test_client()

    response = client.get("/panel")

    assert response.status_code == 200


def test_panel_emits_default_demo_box() -> None:
    text = create_app().test_client().get("/panel").text

    assert "comic-panel" in text
    assert "--panel-w:1520px" in text
    assert "--panel-h:190px" in text
    assert "rgb(225,220,202)" in text  # base background
    assert "rgb(187,180,162)" in text  # example halftone color


def test_panel_includes_halftone_and_border_layers() -> None:
    text = create_app().test_client().get("/panel").text

    # Continuous halftone: a tone-gradient element + its dot-screen SVG filter.
    assert 'class="halftone"' in text
    assert 'id="ht-demo"' in text  # the live panel's filter
    assert "feImage" in text
    assert "feTile" in text
    assert "feFlood" in text
    # Hand-drawn border: a filled vector path (no SVG filter).
    assert 'class="comic-border"' in text
    assert 'fill-rule="evenodd"' in text  # variable-width frame path


def test_panel_radius_threads_into_clip() -> None:
    text = create_app().test_client().get("/panel?radius=20").text

    assert "--radius:20px" in text  # rounds + clips the panel background


def test_panel_seed_changes_rough_border() -> None:
    client = create_app().test_client()

    # With roughness, the seed picks the pen-pressure ripple, so the border
    # path geometry differs between seeds.
    a = client.get("/panel?roughness=6&seed=1").text
    b = client.get("/panel?roughness=6&seed=2").text

    assert a != b


def test_panel_query_params_drive_css_variables() -> None:
    text = (
        create_app()
        .test_client()
        .get("/panel?width=800&max_fill=0.7&origin_angle=270deg")
        .text
    )

    assert "--panel-w:800px" in text
    assert "--max-fill:0.7" in text
    assert "--origin-angle:270deg" in text


def test_panel_offset_threads_into_filter() -> None:
    text = create_app().test_client().get("/panel?offset=5").text

    # offset shifts the tiled dot cell's origin (feImage x/y).
    assert 'x="5"' in text
    assert 'y="5"' in text


def test_panel_transparency_threads_into_filter() -> None:
    text = create_app().test_client().get("/panel?transparency=0.5").text

    # transparency 0.5 -> dot ink opacity 0.5 (feFlood flood-opacity).
    assert 'flood-opacity="0.5"' in text


def test_panel_render_is_deterministic() -> None:
    client = create_app().test_client()

    first = client.get("/panel?roughness=6&seed=7").text
    second = client.get("/panel?roughness=6&seed=7").text

    assert first == second  # no wall-clock / randomness in the render
