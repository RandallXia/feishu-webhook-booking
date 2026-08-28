def test_app_imports():
    from app.main import app

    assert app.title == "Feishu Webhook Service"


def test_legacy_mode_pinned():
    from app.config import get_settings

    settings = get_settings()
    assert settings.feishu_targets_file is None