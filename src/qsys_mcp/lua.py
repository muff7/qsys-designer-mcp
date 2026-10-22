"""Lua helpers: syntax checking and Q-SYS plugin scaffolding."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from luaparser import ast as lua_ast

PLUGIN_DIR = Path.home() / "Documents" / "QSC" / "Q-Sys Designer" / "Plugins"

# Globals provided by the Q-SYS Lua environment (not an error to reference them).
QSYS_GLOBALS = {
    "Controls", "Component", "Timer", "TcpSocket", "TcpSocketServer", "UdpSocket", "HttpClient",
    "SerialPorts", "SerialServerPorts", "NamedControl", "Notifications", "System", "Design", "Mixer",
    "Snapshot", "ChannelGroup", "Properties", "Pins", "Uci", "PasswordHash", "Crypto", "Ssh", "WebSocket",
    "Http", "Network", "Ping", "SNMPSession", "SNMPTrap", "DanteDevice", "DanteBrowser", "Usb",
    "UsbMonitor", "UsbHelpers", "json", "rapidjson", "bit", "math", "string", "table", "os", "io",
    "print", "pairs", "ipairs", "tostring", "tonumber", "type", "pcall", "xpcall", "error", "assert",
    "select", "next", "setmetatable", "getmetatable", "rawget", "rawset", "rawequal", "rawlen",
    "require", "unpack", "load", "coroutine", "utf8", "Log", "LogLevel", "Qlib", "PluginInfo",
    # plugin entry points are meant to be global
    "GetColor", "GetPrettyName", "GetProperties", "RectifyProperties", "GetControls", "GetControlLayout",
    "GetPins", "GetComponents", "GetWiring", "GetPages", "GetModel",
}


def check_syntax(code: str) -> dict:
    """Parse Lua (5.3 grammar, as used by Q-SYS). Returns ok/error plus light lint warnings."""
    try:
        tree = lua_ast.parse(code)
    except Exception as e:  # luaparser raises SyntaxException with a readable message
        return {"ok": False, "error": str(e).strip()}
    return {"ok": True, "warnings": _lint(tree, code)}


def _lint(tree, code: str) -> list[str]:
    """Scope-aware lint. Function parameters, for-loop variables and locals are tracked per scope,
    so reassigning a parameter (`s = trim(s)`) is not reported as a global."""
    warnings: list[str] = []
    implicit: set[str] = set()
    scopes: list[set[str]] = [set()]

    def declared(name: str) -> bool:
        return name in QSYS_GLOBALS or any(name in s for s in scopes)

    def names(nodes) -> set[str]:
        return {n.id for n in nodes if isinstance(n, lua_ast.Name)}

    def visit(node):
        if isinstance(node, list):
            for n in node:
                visit(n)
            return
        if not isinstance(node, lua_ast.Node):
            return
        if isinstance(node, (lua_ast.Function, lua_ast.LocalFunction, lua_ast.Method, lua_ast.AnonymousFunction)):
            if isinstance(node, lua_ast.LocalFunction):
                scopes[-1].add(node.name.id)            # visible inside itself (recursion)
            elif isinstance(node, lua_ast.Function) and isinstance(node.name, lua_ast.Name):
                if not declared(node.name.id):
                    implicit.add(node.name.id + "()")
            params = names(node.args) | ({"self"} if isinstance(node, lua_ast.Method) else set())
            scopes.append(params)
            visit(node.body)
            scopes.pop()
            return
        if isinstance(node, lua_ast.LocalAssign):
            visit(node.values)                          # `local x = x` reads the outer x
            scopes[-1].update(names(node.targets))
            return
        if isinstance(node, lua_ast.Fornum):
            visit([node.start, node.stop, node.step])
            scopes.append({node.target.id})
            visit(node.body)
            scopes.pop()
            return
        if isinstance(node, lua_ast.Forin):
            visit(node.iter)
            scopes.append(names(node.targets))
            visit(node.body)
            scopes.pop()
            return
        if isinstance(node, lua_ast.Repeat):            # `until` can see the body's locals
            scopes.append(set())
            visit(node.body.body)
            visit(node.test)
            scopes.pop()
            return
        if isinstance(node, lua_ast.Block):
            scopes.append(set())
            visit(node.body)
            scopes.pop()
            return
        if isinstance(node, lua_ast.Assign):
            for i, t in enumerate(node.targets):
                value = node.values[i] if i < len(node.values) else None
                if isinstance(t, lua_ast.Name) and not declared(t.id) and not _is_timer_new(value):
                    implicit.add(t.id)          # a global Timer is QSC's recommended form, so not reported
                if isinstance(t, lua_ast.Index) and isinstance(t.idx, lua_ast.Name) and t.idx.id == "Trigger":
                    warnings.append("'.Trigger = ...' does nothing: call ctl:Trigger() instead.")
        if isinstance(node, lua_ast.Call) and isinstance(node.func, lua_ast.Index):
            f = node.func
            if (isinstance(f.value, lua_ast.Name) and f.value.id == "Timer" and isinstance(f.idx, lua_ast.Name)
                    and f.idx.id == "CallAfter" and node.args and isinstance(node.args[0], lua_ast.Number)):
                warnings.append("Timer.CallAfter takes (function, seconds); the arguments look reversed.")
        for k, v in vars(node).items():
            if not k.startswith("_") and isinstance(v, (lua_ast.Node, list)):
                visit(v)

    visit(tree.body.body)                               # chunk body shares the top-level scope
    warnings.extend(_orphan_timers(tree))

    if implicit:
        warnings.append(f"Assigned without 'local' (implicit globals): {', '.join(sorted(implicit))}")
    if re.search(r"\bwhile\s+true\s+do\b", code) and "Timer" not in code:
        warnings.append("'while true do' loop: Q-SYS scripts are event-driven; a busy loop will starve the script. Use Timer instead.")
    if re.search(r"\bos\.execute\b|\bio\.popen\b|\bio\.open\b", code):
        warnings.append("os.execute/io.popen/io.open are not available in Q-SYS (no shell or filesystem).")
    if "TcpSocket.New" in code and not re.search(r"\bClosed\b|\bError\b|\bTimeout\b", code):
        warnings.append("TcpSocket used but Closed/Error/Timeout are never handled: the socket can die silently.")
    return list(dict.fromkeys(warnings))


def _is_timer_new(node) -> bool:
    return (isinstance(node, lua_ast.Call) and isinstance(node.func, lua_ast.Index)
            and getattr(node.func.value, "id", None) == "Timer" and getattr(node.func.idx, "id", None) == "New")


_FUNCS = (lua_ast.Function, lua_ast.LocalFunction, lua_ast.Method, lua_ast.AnonymousFunction)


def _orphan_timers(tree) -> list[str]:
    """Top-level `local t = Timer.New()` that no function refers to except t's own EventHandler.
    Once the chunk finishes nothing reachable holds t, and QSC's Timer help warns that the engine
    "will eventually delete the timer and the event callback, forcing it to stop"."""
    timers = []
    for node in tree.body.body:
        if isinstance(node, lua_ast.LocalAssign):
            for t, v in zip(node.targets, node.values):
                if isinstance(t, lua_ast.Name) and _is_timer_new(v):
                    timers.append(t.id)
    if not timers:
        return []
    held: set[str] = set()   # names referenced from a function other than their own EventHandler

    def walk(n, owner):
        if isinstance(n, list):
            for x in n:
                walk(x, owner)
            return
        if not isinstance(n, lua_ast.Node):
            return
        if isinstance(n, lua_ast.Assign):
            for t, v in zip(n.targets, n.values):
                if (isinstance(t, lua_ast.Index) and getattr(t.idx, "id", None) == "EventHandler"
                        and isinstance(t.value, lua_ast.Name) and isinstance(v, _FUNCS)):
                    walk(v.body, t.value.id)
                else:
                    walk(t, owner)
                    walk(v, owner)
            return
        if isinstance(n, _FUNCS):
            walk(n.body, owner or "<fn>")
            return
        if isinstance(n, lua_ast.Name) and owner and owner != n.id:
            held.add(n.id)
        for k, v in vars(n).items():
            if not k.startswith("_") and isinstance(v, (lua_ast.Node, list)):
                walk(v, owner)

    walk(tree.body.body, None)
    orphans = [t for t in timers if t not in held]
    if not orphans:
        return []
    return [f"Local Timer only referenced by its own EventHandler: {', '.join(orphans)}. QSC's Timer help warns "
            "a locally scoped Timer will eventually be deleted and stop; make it a global (t = Timer.New()) "
            "or keep it in a table that lives as long as the script."]


PLUGIN_TEMPLATE = '''-- {name} plugin
-- Generated by qsys-mcp. Edit GetControls/GetControlLayout, then the runtime section at the bottom.

PluginInfo = {{
  Name = "{category}~{name}",
  Version = "0.1.0",
  BuildVersion = "0.1.0.0",
  Id = "{id}",
  Author = "{author}",
  Description = "{description}",
}}

function GetColor(props)
  return {{ 102, 102, 102 }}
end

function GetPrettyName(props)
  return "{name}"
end

function GetProperties()
  return {{
    {{ Name = "Debug Print", Type = "enum", Choices = {{ "None", "Tx/Rx", "Function Calls", "All" }}, Value = "None" }},
  }}
end

function RectifyProperties(props)
  return props
end

function GetControls(props)
  return {{
    {{ Name = "IPAddress", ControlType = "Text", Count = 1, UserPin = true, PinStyle = "Both" }},
    {{ Name = "Port", ControlType = "Knob", ControlUnit = "Integer", Min = 1, Max = 65535, Count = 1, UserPin = true, PinStyle = "Both" }},
    {{ Name = "Connected", ControlType = "Indicator", IndicatorType = "Led", Count = 1, UserPin = true, PinStyle = "Output" }},
    {{ Name = "Status", ControlType = "Indicator", IndicatorType = "Status", Count = 1, UserPin = true, PinStyle = "Output" }},
  }}
end

function GetControlLayout(props)
  local layout, graphics = {{}}, {{}}
  table.insert(graphics, {{ Type = "GroupBox", Text = "Connection", Fill = {{ 230, 230, 230 }}, StrokeWidth = 1, Position = {{ 5, 5 }}, Size = {{ 260, 110 }} }})
  table.insert(graphics, {{ Type = "Text", Text = "IP Address", Position = {{ 15, 25 }}, Size = {{ 80, 16 }}, HTextAlign = "Right" }})
  layout["IPAddress"] = {{ PrettyName = "Connection~IP Address", Style = "Text", Position = {{ 100, 25 }}, Size = {{ 150, 16 }} }}
  table.insert(graphics, {{ Type = "Text", Text = "Port", Position = {{ 15, 45 }}, Size = {{ 80, 16 }}, HTextAlign = "Right" }})
  layout["Port"] = {{ PrettyName = "Connection~Port", Style = "Text", Position = {{ 100, 45 }}, Size = {{ 60, 16 }} }}
  table.insert(graphics, {{ Type = "Text", Text = "Connected", Position = {{ 15, 65 }}, Size = {{ 80, 16 }}, HTextAlign = "Right" }})
  layout["Connected"] = {{ PrettyName = "Connection~Connected", Style = "Led", Position = {{ 100, 65 }}, Size = {{ 16, 16 }} }}
  layout["Status"] = {{ PrettyName = "Connection~Status", Style = "Text", Position = {{ 15, 88 }}, Size = {{ 240, 20 }} }}
  return layout, graphics
end

if Controls then
  -- Runtime section: runs on the Core / in emulation.
  local sock = TcpSocket.New()
  sock.ReadTimeout = 0
  sock.WriteTimeout = 0
  sock.ReconnectTimeout = 5

  local function setStatus(code, msg)
    Controls.Status.Value = code -- 0 OK, 1 compromised, 2 fault, 3 not present, 4 missing, 5 initializing
    Controls.Status.String = msg or ""
  end

  sock.EventHandler = function(s, evt, err)
    if evt == TcpSocket.Events.Connected then
      Controls.Connected.Boolean = true
      setStatus(0)
    elseif evt == TcpSocket.Events.Data then
      local line = s:ReadLine(TcpSocket.EOL.Any)
      while line do
        print("RX: " .. line)
        line = s:ReadLine(TcpSocket.EOL.Any)
      end
    else -- Closed, Error, Timeout, Reconnect
      Controls.Connected.Boolean = false
      setStatus(2, tostring(evt) .. (err and (": " .. err) or ""))
    end
  end

  local function connect()
    if sock.IsConnected then sock:Disconnect() end
    local ip = Controls.IPAddress.String
    if ip == "" then setStatus(3, "No IP address") return end
    setStatus(5, "Connecting")
    sock:Connect(ip, math.floor(Controls.Port.Value))
  end

  Controls.IPAddress.EventHandler = connect
  Controls.Port.EventHandler = connect
  connect()
end
'''


def scaffold_plugin(name: str, category: str = "Custom", author: str = "", description: str = "",
                    out_dir: Path | None = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    out_dir = out_dir or PLUGIN_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{safe}.qplug"
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    path.write_text(PLUGIN_TEMPLATE.format(
        name=name, category=category, author=author, description=description, id=str(uuid.uuid4())),
        encoding="utf-8")
    return path
