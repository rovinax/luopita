import os
import shlex
import subprocess
from contextvars import ContextVar
from dataclasses import dataclass

from utils.log import ChatbotLogger

_SAFE_BIN_DIRS = {"/bin", "/usr/bin", "/usr/local/bin"}
MAX_SHELL_CALLS_PER_TURN = 8
MAX_STDOUT_CHARS = 8000
MAX_STDERR_CHARS = 2000
_SHELL_WORDS = {"|", "||", "&&", ";", "&", ">", ">>", "<", "<<"}
_SHELL_HINT = (
    "Error: run_shell is not bash. No pipes, redirects, ';', '&&', or '$()'. "
    "Run one program per call, e.g. curl -sS \"https://example.com\". "
    "Need less output? Call curl once; do not pipe to head."
)
_shell_turn: ContextVar[list[str] | None] = ContextVar("shell_turn", default=None)


@dataclass(frozen=True)
class CommandPolicy:
    allow: set[str]
    workdir: str
    timeout_sec: int = 30


def default_policy() -> CommandPolicy:
    allow = {
        "ls",
        "pwd",
        "whoami",
        "date",
        "echo",
        "cat",
        "head",
        "tail",
        "python",
        "uv",
    }
    workdir = os.path.abspath(os.getcwd())
    return CommandPolicy(allow=allow, workdir=workdir, timeout_sec=30)


def begin_shell_turn() -> None:
    _shell_turn.set([])


def end_shell_turn() -> None:
    _shell_turn.set(None)


def _normalize_payload(payload: str) -> str:
    text = (payload or "").strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    return text


def _argv0_name(raw: str) -> str:
    name = (raw or "").strip()
    base = os.path.basename(name)
    if not name or name == base:
        return base or name
    if os.path.dirname(name) in _SAFE_BIN_DIRS:
        return base
    return name


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(truncated, {len(text)} chars total)"


def _shell_syntax_reason(tokens: list[str]) -> str | None:
    for tok in tokens:
        if tok in _SHELL_WORDS:
            return tok
        if "`" in tok or "$(" in tok or tok.startswith(">(") or tok.startswith("<("):
            return tok
        if any(op in tok for op in ("|", "&&", "||")):
            return tok
        if ";" in tok and not tok.startswith(("http://", "https://", "ftp://")):
            return tok
    return None


def execute_command(payload: str, policy: CommandPolicy, logger: ChatbotLogger | None = None) -> str:
    logger = logger or ChatbotLogger()
    payload = _normalize_payload(payload)
    hist = _shell_turn.get()
    if hist is not None:
        if payload in hist:
            return (
                f"Error: already ran `{payload}` this turn. "
                "Use that output; do not call run_shell again with the same command."
            )
        if len(hist) >= MAX_SHELL_CALLS_PER_TURN:
            return (
                "Error: too many shell commands this turn. "
                "Stop calling run_shell and answer from what you already have."
            )
        hist.append(payload)
    try:
        cmd_args = shlex.split(payload)
    except ValueError as exc:
        return f"Error: could not parse command: {exc}. {_SHELL_HINT.removeprefix('Error: ')}"
    if not cmd_args:
        return "Error: empty command."
    if _shell_syntax_reason(cmd_args):
        logger.warning(f"agent command rejected shell-syntax cmd={cmd_args[0]}")
        return _SHELL_HINT

    cmd = _argv0_name(cmd_args[0])
    if cmd not in policy.allow:
        logger.warning(f"agent command denied cmd={cmd}")
        return f"Error: command not allowed: {cmd}"

    try:
        result = subprocess.run(
            args=cmd_args,
            capture_output=True,
            text=True,
            timeout=policy.timeout_sec,
            cwd=policy.workdir,
        )
        logger.info(f"agent command executed cmd={cmd} exit={result.returncode}")
        stdout = _clip(result.stdout or "", MAX_STDOUT_CHARS)
        stderr = _clip(result.stderr or "", MAX_STDERR_CHARS)
        if not stdout and not stderr:
            return "Execution successful (no output)."
        return f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
    except subprocess.TimeoutExpired:
        logger.warning(f"agent command timeout cmd={cmd}")
        return "Error: Command timed out."
    except Exception as e:
        logger.error(f"agent command error cmd={cmd} err={e}")
        return f"Error: {str(e)}"


def parse_agent_tag(output: str) -> tuple[str, str]:
    raw_output = (output or "").strip()
    if raw_output.startswith("[command]:"):
        return "[command]", raw_output.replace("[command]:", "", 1).strip()
    if raw_output.startswith("[text]:"):
        return "[text]", raw_output.replace("[text]:", "", 1).strip()
    if raw_output.startswith("[text^over]:"):
        return "[text^over]", raw_output.replace("[text^over]:", "", 1).strip()
    return "[text]", raw_output
