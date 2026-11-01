"""Only the verified $0.30 virtual Telegram US project is offered."""

from app.config import settings

US_PROJECTS = {2: 74}
US_LABELS = {2: "США · виртуальные"}
DEMO_CODE = "demo:us:tg"


def allowed_codes():
    codes = [
        f"{category}:{project}:{kind}" for category, project in US_PROJECTS.items() for kind in (1, 2, 3)
    ]
    return codes + ([DEMO_CODE] if settings.demo_mode else [])


def permitted_code(code):
    return code in allowed_codes()


def telegram_project(category, item):
    return (
        category in US_PROJECTS
        and int(item.get("id", 0)) == US_PROJECTS[category]
        and "telegram" in str(item.get("name", "")).casefold()
    )
