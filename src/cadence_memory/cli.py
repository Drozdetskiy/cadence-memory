import time

import typer

app = typer.Typer(add_completion=False)


@app.command()
def ping() -> None:
    print("pong", time.time())
