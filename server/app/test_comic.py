from app.comic import comic_border_path


def test_border_path_has_outer_and_inner_subpaths() -> None:
    d = comic_border_path(200, 100, radius=10, mid_width=12, corner_width=4)

    assert d.startswith("M ")
    assert d.count("M ") == 2  # outer rounded rect + inner bowed path
    assert "Z" in d


def test_border_path_mid_width_bows_the_inner_edge() -> None:
    uniform = comic_border_path(200, 100, radius=0, mid_width=6, corner_width=6)
    bowed = comic_border_path(200, 100, radius=0, mid_width=14, corner_width=6)

    # A wider mid_width pulls the inner curve further toward the center.
    assert uniform != bowed


def test_border_path_roughness_ripples_with_seed() -> None:
    clean = comic_border_path(
        200, 100, radius=10, mid_width=8, corner_width=4, roughness=0
    )
    r1 = comic_border_path(
        200, 100, radius=10, mid_width=8, corner_width=4, roughness=5, seed=1
    )
    r2 = comic_border_path(
        200, 100, radius=10, mid_width=8, corner_width=4, roughness=5, seed=2
    )

    assert r1 != clean  # roughness ripples the inner edge
    assert r1 != r2  # the seed picks a different ripple


def test_border_path_clamps_oversized_radius() -> None:
    # radius far bigger than the panel must not produce a degenerate/huge path.
    d = comic_border_path(40, 20, radius=999, mid_width=4, corner_width=4)

    assert "A " in d  # arcs still present
    low = d.lower()
    assert (
        "nan" not in low and "inf" not in low and "e" not in low
    )  # no garbage/exponents
