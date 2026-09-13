from pyfiglet import Figlet
from rich import print
import uvicorn

from utils.config import load_config


if __name__ == "__main__":
    cfg = load_config()
    banner = Figlet(font="slant").renderText("    Luopita")
    print(f"[bold cyan]{banner}[/bold cyan]")
    print("\t\t\t\t\t[bold cyan]by rovina.top[/bold cyan]")
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False)
