"""Build a server source archive from an explicit allowlist; never include local secrets or DBs."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("app", "deploy", "docs", "migrations", "tests", "tests-ui")
FILES = (
    "pyproject.toml",
    "requirements.lock",
    "requirements.txt",
    "main.py",
    "README.md",
    "Dockerfile",
    "compose.yaml",
    "alembic.ini",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    "package.json",
    "package-lock.json",
)
EXTENSIONS = {".py", ".md", ".json", ".yaml", ".mako", ".js", ".cjs", ".css", ".html", ".sh"}


def package(target):
    paths = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        paths.extend(
            path
            for path in (ROOT / folder).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and (path.suffix in EXTENSIONS or path.name in ("Caddyfile", ".env.server.example"))
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path, path.relative_to(ROOT).as_posix())
    return len(paths)


if __name__ == "__main__":
    target = ROOT / "dist/smena-server.zip"
    count = package(target)
    print(f"Created {target.name}: {count} files; local .env, databases and data/ excluded")
