"""
Azure True Agentic Tools
Provides web search, Python sandbox, file operations, and URL fetching.
"""
from __future__ import annotations

import contextlib
import io
import ipaddress
import json
import os
import socket
import urllib.parse

# NOTE: These functions perform blocking I/O. When called from async context,
# wrap with: await loop.run_in_executor(None, web_search, query)
import urllib.request
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

def _sandbox_dir() -> Path:
    p = Path(os.environ.get("AZURE_SANDBOX_DIR", "sandbox"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _fetch_url(url: str, headers: dict[str, str], timeout: int = 15) -> bytes:
    """Synchronous fetch - call via run_in_executor from async context."""
    _validate_public_url(url)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _validate_public_url(url: str) -> None:
    """Reject non-web and non-public destinations before making a request."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute http(s) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Private or local hosts are not allowed")

    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(hostname, parsed.port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise ValueError("Could not resolve public host") from exc

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Private or non-global hosts are not allowed")


def _safe_path(path: str) -> Path:
    """Resolve a path safely within the sandbox.

    Uses path-component containment (is_relative_to) rather than a string
    prefix check. A raw ``str.startswith`` comparison would treat a sibling
    directory such as ``sandbox_evil`` as inside ``sandbox`` (shared prefix),
    allowing an LLM-supplied ``../sandbox_evil/secret`` to escape the sandbox.
    """
    sandbox = _sandbox_dir().resolve()
    p = (sandbox / path).resolve()
    if p != sandbox and not p.is_relative_to(sandbox):
        raise PermissionError(f"Path outside sandbox: {path}")
    return p


def web_search(query: str, max_results: int | None = None) -> str:
    """Search the web for information. Source is configurable via env."""
    if max_results is None:
        max_results = int(os.environ.get("AZURE_SEARCH_MAX_RESULTS", "3"))
    try:
        encoded_query = urllib.parse.quote_plus(query)
        search_base = os.environ.get("AZURE_SEARCH_API_URL", "https://en.wikipedia.org/w/api.php")
        url = f"{search_base}?action=query&list=search&srsearch={encoded_query}&utf8=&format=json"
        response = _fetch_url(url, headers={'User-Agent': 'AzureBot/1.0'}, timeout=10)
        data = json.loads(response)
        results = data.get('query', {}).get('search', [])
        if not results:
            return f"No results for '{query}'."
        formatted = []
        for i, res in enumerate(results[:max_results]):
            snippet = BeautifulSoup(res['snippet'], 'html.parser').text if BeautifulSoup else res['snippet']
            formatted.append(f"{i+1}. {res['title']}\n   {snippet[:200]}")
        return "WEB SEARCH:\n" + "\n\n".join(formatted)
    except Exception as e:
        return f"Web search failed: {e}"


def web_fetch(url: str, max_chars: int | None = None) -> str:
    """Fetch and extract text content from a URL."""
    if max_chars is None:
        max_chars = int(os.environ.get("AZURE_FETCH_MAX_CHARS", "3000"))
    try:
        _validate_public_url(url)
        req = urllib.request.Request(url, headers={'User-Agent': 'AzureBot/1.0'})
        timeout = int(os.environ.get("AZURE_WEB_TIMEOUT", "15"))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get('Content-Type', '')
            response = resp.read()
        if 'application/json' in content_type:
            data = json.loads(response)
            return json.dumps(data, indent=2)[:max_chars]
        text = response.decode('utf-8', errors='replace')
        if BeautifulSoup:
            soup = BeautifulSoup(text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                tag.decompose()
            text = soup.get_text(separator='\n', strip=True)
        lines = [line for line in text.split('\n') if line.strip()]
        content = '\n'.join(lines[:200])
        return content[:max_chars]
    except Exception as e:
        return f"Fetch failed: {e}"


def execute_python(code: str) -> str:
    """
    Execute Python code with SEVERE restrictions.

    ⚠️ SECURITY WARNING: This function is DISABLED by default.

    Previous implementation used exec() with full builtins, allowing:
    - Arbitrary file system access
    - Network requests
    - Process execution
    - Environment variable access
    - Module imports

    Current implementation (when enabled):
    - Restricted builtins (no file/network/process access)
    - No module imports
    - Limited to math/string operations
    - 5-second timeout

    To enable: Set AZURE_ALLOW_CODE_EXECUTION=true in .env

    ⚠️ ONLY enable if you understand the risks and have:
    - Container isolation with resource limits
    - Network egress blocked at firewall level
    - Administrator-only access controls
    - Complete audit logging

    True sandboxing requires external solutions:
    - Docker/Podman containers with seccomp profiles
    - gVisor runtime security
    - External execution services (Judge0, Piston API)
    """
    # Check if explicitly enabled
    if os.environ.get("AZURE_ALLOW_CODE_EXECUTION", "").lower() not in ("true", "1", "yes"):
        return (
            "❌ **Python Execution Disabled**\n\n"
            "Arbitrary code execution is disabled for security.\n\n"
            "**Why:** Code execution with full system access is a critical security risk.\n\n"
            "**Alternatives:**\n"
            "• Use web_search for information retrieval\n"
            "• Use file_read/file_write for sandbox file operations\n"
            "• Request specific tools for your use case\n\n"
            "**For Administrators:** To enable with restrictions, set:\n"
            "`AZURE_ALLOW_CODE_EXECUTION=true` in .env\n\n"
            "⚠️ Only enable in isolated environments with proper security controls."
        )

    # Restricted builtins - no file/network/process access
    safe_builtins = {
        'abs': abs, 'round': round, 'min': min, 'max': max, 'sum': sum,
        'len': len, 'range': range, 'enumerate': enumerate, 'zip': zip,
        'sorted': sorted, 'reversed': reversed, 'all': all, 'any': any,
        'int': int, 'float': float, 'str': str, 'bool': bool,
        'list': list, 'dict': dict, 'set': set, 'tuple': tuple,
        'True': True, 'False': False, 'None': None,
        'print': print,  # Captured by stdout redirect
    }

    # Strip code fences
    if code.startswith("```python"):
        code = code[9:]
    elif code.startswith("```"):
        code = code[3:]
    if code.endswith("```"):
        code = code[:-3]
    code = code.strip()

    # Validate code doesn't attempt dangerous operations
    dangerous_keywords = ['import', 'exec', 'eval', 'compile', 'open', '__']
    code_lower = code.lower()
    for keyword in dangerous_keywords:
        if keyword in code_lower:
            return f"❌ **Blocked**: Code contains forbidden keyword '{keyword}'"

    stdout = io.StringIO()
    try:
        # Execute with timeout and restricted builtins
        import signal

        def timeout_handler(signum, frame):
            raise TimeoutError("Execution timeout (5s)")

        # Set 5-second timeout (Unix only - Windows will skip)
        if hasattr(signal, 'SIGALRM'):
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(5)

        try:
            with contextlib.redirect_stdout(stdout):
                exec(code, {"__builtins__": safe_builtins}, {})
        finally:
            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)  # Cancel alarm

        output = stdout.getvalue()
        if not output:
            return "✅ Executed successfully (no output)."

        # Limit output size
        max_output = 2000
        if len(output) > max_output:
            output = output[:max_output] + f"\n... (truncated, {len(output)} bytes total)"

        return f"**Result:**\n```\n{output}\n```"

    except TimeoutError as e:
        return f"❌ **Timeout**: {e}"
    except Exception as e:
        return f"❌ **Error**: {type(e).__name__}: {e}"


def file_read(path: str) -> str:
    """Read a file from the sandbox directory."""
    try:
        p = _safe_path(path)
        if not p.exists():
            return f"File not found: {path}"
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"File read error: {e}"


def file_write(path: str, content: str) -> str:
    """Write content to a file in the sandbox directory."""
    try:
        p = _safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written to {path} ({len(content)} bytes)"
    except Exception as e:
        return f"File write error: {e}"


def file_list(path: str = "") -> str:
    """List files in a sandbox directory."""
    try:
        p = _safe_path(path)
        if not p.exists():
            return f"Path not found: {path}"
        entries = list(p.iterdir())
        if not entries:
            return "(empty)"
        lines = []
        for e in sorted(entries):
            tag = "/" if e.is_dir() else ""
            lines.append(f"  {e.name}{tag}")
        return "\n".join(lines)
    except Exception as e:
        return f"List error: {e}"

def manage_access_control(target_type: str, target_id: str, permission: str) -> str:
    """Manage natural language access control for users/guilds/channels."""
    if os.environ.get("AZURE_ALLOW_ACCESS_CONTROL_TOOL", "").lower() not in ("true", "1", "yes"):
        return (
            "Access control changes are disabled for safety. "
            "Set AZURE_ALLOW_ACCESS_CONTROL_TOOL=true only for trusted operator environments."
        )
    try:
        from .database import get_shared_db
        db = get_shared_db()
        if target_type not in ["user", "guild", "channel", "role"]:
            return "Error: target_type must be user, guild, channel, or role."
        if permission not in ["allow", "deny", "admin"]:
            return "Error: permission must be allow, deny, or admin."

        db.set_access_control(target_type, target_id, permission, added_by="Azure-NLP-Tool")
        return f"Successfully set {permission} permission for {target_type} {target_id}."
    except Exception as e:
        return f"Access control update failed: {e}"


def register_agentic_tools(agent) -> None:  # type: ignore[no-untyped-def]
    agent.tools.register(
        name="web_search",
        description="Search the web for information using Wikipedia.",
        fn=web_search,
        schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    )
    agent.tools.register(
        name="web_fetch",
        description="Fetch and read the text content of a URL.",
        fn=web_fetch,
        schema={"type": "object", "properties": {
            "url": {"type": "string", "description": "Full URL to fetch"},
            "max_chars": {"type": "integer", "description": "Max characters to return"}
        }, "required": ["url"]}
    )
    agent.tools.register(
        name="execute_python",
        description="Execute Python code and return output. Use print() to output results.",
        fn=execute_python,
        schema={"type": "object", "properties": {
            "code": {"type": "string", "description": "Python code to execute"}
        }, "required": ["code"]}
    )
    agent.tools.register(
        name="file_read",
        description="Read a file from the bot's sandbox directory.",
        fn=file_read,
        schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "File path relative to sandbox"}
        }, "required": ["path"]}
    )
    agent.tools.register(
        name="file_write",
        description="Write content to a file in the bot's sandbox directory.",
        fn=file_write,
        schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "File path relative to sandbox"},
            "content": {"type": "string", "description": "File content"}
        }, "required": ["path", "content"]}
    )
    agent.tools.register(
        name="file_list",
        description="List files and directories in the sandbox.",
        fn=file_list,
        schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory path relative to sandbox"}
        }}
    )
    agent.tools.register(
        name="manage_access_control",
        description="Modify bot access controls and permissions via natural language.",
        fn=manage_access_control,
        schema={"type": "object", "properties": {
            "target_type": {"type": "string", "description": "Type of target: user, guild, channel, or role"},
            "target_id": {"type": "string", "description": "The Discord ID of the target"},
            "permission": {"type": "string", "description": "The permission level: allow, deny, or admin"}
        }, "required": ["target_type", "target_id", "permission"]}
    )
