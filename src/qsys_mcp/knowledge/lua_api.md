# Q-SYS Lua cheat sheet (Lua 5.3, event-driven)

Scripts never block. Everything happens in EventHandlers and Timers.

## Controls (Control Script / Text Controller / Block Controller / plugin runtime)
```lua
Controls.Gain.Value            -- number
Controls.Gain.String           -- display string ("−6.0dB")
Controls.Gain.Position         -- 0..1
Controls.Mute.Boolean          -- true/false
Controls.Gain.EventHandler = function(ctl) print(ctl.Value) end
Controls.Gain.RampTime = 2 ; Controls.Gain.Value = -10   -- ramp
Controls.Label.Color = "#FF0000" ; Controls.Btn.IsDisabled = true ; Controls.Btn.Legend = "On"
-- Arrays of controls (Count > 1):  Controls.Input[1]
```

## Other components (needs the target's "Script Access" property set)
```lua
local mixer = Component.New("Main Mixer")
mixer["input.1.gain"].Value = -6
mixer["input.1.mute"].EventHandler = function(c) end
for _, c in ipairs(Component.GetControls("Main Mixer")) do print(c.Name) end
Component.GetComponents()          -- list of {Name, Type, Properties}
```

## Named controls
```lua
NamedControl.GetValue("Vol") ; NamedControl.SetValue("Vol", -10, rampSeconds)
NamedControl.GetString / SetString / GetPosition / SetPosition
```

## Timers
```lua
t = Timer.New()   -- not local: see "Timers" under Pitfalls
t.EventHandler = function() --[[ poll ]] end
t:Start(1.0)    -- seconds, repeats until :Stop()
t:Stop() ; t:IsRunning()
Timer.CallAfter(function() print("later") end, 2)   -- (function, seconds); one-shot, nothing to cancel
Timer.Now()     -- epoch seconds; the emulator uses the Windows clock, the Core its own
```

## TCP
```lua
local s = TcpSocket.New()
s.ReadTimeout, s.WriteTimeout, s.ReconnectTimeout = 0, 0, 5
s.EventHandler = function(sock, evt, err)
  if evt == TcpSocket.Events.Connected then
  elseif evt == TcpSocket.Events.Data then
    local line = sock:ReadLine(TcpSocket.EOL.CrLf)   -- Any, CrLf, Cr, Lf, Null, Custom
  elseif evt == TcpSocket.Events.Closed or evt == TcpSocket.Events.Error or evt == TcpSocket.Events.Timeout then
  end
end
s:Connect("192.168.1.50", 23) ; s:Write("PWR ON\r") ; s:Disconnect()
```
UDP: `UdpSocket.New()`, `:Open(ip, port)`, `:Send(ip, port, data)`, `.Data = function(sock, packet) packet.Data, packet.Address end`.

## HTTP
```lua
HttpClient.Download{ Url = "http://host/api", Headers = {["Accept"]="application/json"}, Timeout = 10,
  EventHandler = function(tbl, code, data, err, headers) end }
HttpClient.Upload{ Url = url, Method = "POST", Data = body, Headers = {["Content-Type"]="application/json"},
  EventHandler = function(tbl, code, data, err, headers) end }
HttpClient.CreateUrl{ Host = "http://host", Path = "api/v1", Query = { a = 1 } }
local rapidjson = require("rapidjson") ; rapidjson.encode(t) ; rapidjson.decode(s)
```

## Serial (Core/peripheral serial ports wired to a script pin)
```lua
local sp = SerialPorts[1]
sp:Open(9600, 8, "N")    -- baud, data bits, parity
sp.EventHandler = function(port, evt) if evt == SerialPorts.Events.Data then port:ReadLine(SerialPorts.EOL.Any) end end
```

## Misc
- `System.IsEmulating`, `System.LockingId`, `System.MajorVersion`, `Design.GetStatus()`
- `Notifications.Publish("topic", tbl)` / `Notifications.Subscribe("topic", function(name, data) end)`
- `print()` goes to the script's debug output; `Log.Message(msg)` / `Log.Error(msg)` go to the Core event log.
- Status controls: 0 OK, 1 Compromised, 2 Fault, 3 Not Present, 4 Missing, 5 Initializing.

## Plugin (.qplug) entry points
`PluginInfo`, `GetColor`, `GetPrettyName`, `GetProperties`, `RectifyProperties`, `GetControls`,
`GetControlLayout` (returns layout, graphics), `GetPins`, `GetComponents`, `GetWiring`.
The runtime code lives inside `if Controls then ... end`. Installed plugins go in
`Documents\QSC\Q-Sys Designer\Plugins` and need a Designer restart (or Tools > Reload Plugins in newer versions).

## Pitfalls
Checked against the Designer 10.4 and 10.5 offline help where a page is named; the rest is Lua 5.3 behaviour or seen on real Cores.

**Timers**
- `Timer.htm`: "Do not scope your Timers locally. If you do so, the scripting engine will eventually delete the timer and the event callback, forcing it to stop." The real risk is a top-level `local t = Timer.New()` that only its own `t.EventHandler` refers to (heartbeats, self-restarting polls, reconnect supervisors): once the script's top level has run, nothing holds it. Make it a global (`t = Timer.New()`, as in QSC's examples), or keep timers in a table the script keeps. `lua_check` warns about the risky case and doesn't report global timers as implicit globals.
- `Timer.CallAfter` can't be cancelled. For debounce use a `Timer.New()` and call `:Start()` again on each event, which restarts the countdown.
- A delay of 0 fires straight away. `t:Start(math.max(0, target - now))` with a target already in the past fires immediately, so check `target > now` first.

**Controls**
- Trigger controls: call `ctl:Trigger()`. Writing `ctl.Trigger = true` does nothing.
- Trigger, Momentary and Toggle buttons fire differently. Momentary fires on press and on release (`.Boolean` true, then false), so a handler that doesn't check `.Boolean` runs twice per press.
- `Controls.Inputs[n]` with nothing wired to pin n is nil, and indexing it raises "attempt to index ... (a nil value)" (`Controls_IO.htm`).
- The whole script re-runs when it's edited or the design restarts. Locals go back to their initial values but controls keep theirs, so read the controls' current state at start-up instead of assuming defaults.
- Renaming a control in the Text Controller doesn't update `Controls.OldName` in the code. The old name just reads nil.

**Sockets**
- TCP gives you bytes, not lines. In the Data event, loop `ReadLine` until it returns nil (`TcpSocket.htm`), and handle `Closed`, `Error` and `Timeout` as well as `Data` or a dead socket goes unnoticed.
- Only one script can listen on a given UDP port on a given IP (`UdpSocket.htm`). If a device always replies to a fixed port, one script has to receive for all of them.
- `Notifications.Publish` inside a `Notifications.Subscribe` callback raises an error (`Notifications.htm`).

**HttpClient** (seen on real Cores)
- `err` is `""` on success, not nil, so `if err then` is always true. Check `err ~= nil and err ~= ""`.
- `code` can arrive as a string such as `"403.0"`, and `code == 403` never matches it. Normalise once with `code = tonumber(code) or code`.

**Lua 5.3**
- Control `.Value` is a float, often slightly off a whole number. `string.format("%d", v)` errors if `v` has a fractional part, and `t[v]` misses `t[3]` when `v` is 2.9999. Round with `math.floor(v + 0.5)` before indexing or formatting.
- `tostring(nil)` is `"nil"`, which ends up in status text. Use `tostring(x or "")`.
