r"""Allow ``python -m contextfs`` as an alternative to the console script.

Useful when the virtual environment is not activated: the user can invoke
``.venv\\Scripts\\python.exe -m contextfs ...`` without needing ``contextfs``
on PATH.
"""

from contextfs.cli.main import app

if __name__ == "__main__":
    app()
