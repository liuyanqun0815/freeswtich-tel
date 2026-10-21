from vr_ai.scriptbot import ScriptBot, opening_text


def test_unknown_type_ignored_and_transfer_keyword():
    bot = ScriptBot(keywords=["人工"])
    assert bot.reply("转人工")["type"] == "action.transfer"
    assert "我是智能助理" in opening_text("您好")


def test_opening_text_appends_scene_opening():
    text = opening_text("您好，欢迎致电。")
    assert text.startswith("我是智能助理。")
    assert "您好，欢迎致电。" in text
