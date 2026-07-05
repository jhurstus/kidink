from app.images.calendar_icons import calendar_icon_prompt


def test_prompt_contains_item_description() -> None:
    prompt = calendar_icon_prompt("Soccer practice")
    assert "“Soccer practice”" in prompt


def test_prompt_carries_the_key_instructions() -> None:
    # The load-bearing pieces of the user-authored template: display size,
    # e-ink palette guidance, and the chroma-key background.
    prompt = calendar_icon_prompt("Soccer")
    assert "100px wide by 60px tall" in prompt
    assert "#00FF00" in prompt
    assert "Avoid purple and brown hues" in prompt
    assert "| Periwinkle | 35% red 65% blue |" in prompt
