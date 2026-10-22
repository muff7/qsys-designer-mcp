from qsys_mcp.lua import PLUGIN_TEMPLATE, check_syntax, scaffold_plugin


def test_syntax_error_reported():
    res = check_syntax("Controls.Gain.EventHandler = function(c\n  print(c.Value)\nend")
    assert res["ok"] is False and res["error"]


def test_implicit_global_warning():
    res = check_syntax("count = 0\nlocal x = 1\nControls.A.Value = x")
    assert res["ok"]
    assert any("count" in w for w in res["warnings"])
    assert not any("Controls" in w for w in res["warnings"])


def test_plugin_template_parses(tmp_path):
    path = scaffold_plugin("My Device", author="me", out_dir=tmp_path)
    res = check_syntax(path.read_text(encoding="utf-8"))
    assert res["ok"], res
    assert res["warnings"] == []
    assert path.name == "My_Device.qplug"


def test_reassigned_parameter_is_not_global():
    assert check_syntax("local function f(s) s = s or '' end")["warnings"] == []


def test_loop_vars_and_forward_decl_are_not_global():
    code = "local helper\nfunction helper() end\nfor i = 1, 3 do i = i + 1 end\nfor k, v in pairs({}) do v = 1 end"
    assert check_syntax(code)["warnings"] == []


def test_global_function_and_leaked_inner_local_reported():
    res = check_syntax("function helper() end\nlocal function f() local y = 1 end\ny = 2")
    assert any("helper()" in w and "y" in w for w in res["warnings"])


def test_trigger_property_write_warned():
    assert any(":Trigger()" in w for w in check_syntax("Controls.Go.Trigger = true")["warnings"])


def test_callafter_reversed_args_warned():
    assert any("reversed" in w for w in check_syntax("Timer.CallAfter(2, function() end)")["warnings"])
    assert check_syntax("Timer.CallAfter(function() end, 2)")["warnings"] == []


def test_tcp_without_close_handling_warned():
    code = "local s = TcpSocket.New()\ns.EventHandler = function(s, e) if e == TcpSocket.Events.Data then end end"
    assert any("Closed/Error" in w for w in check_syntax(code)["warnings"])


def test_timer_held_only_by_own_handler_warned():
    orphan = "local hb = Timer.New()\nhb.EventHandler = function() hb:Stop() print('beat') end\nhb:Start(1)"
    assert any("eventually be deleted" in w for w in check_syntax(orphan)["warnings"])
    held = orphan + "\nControls.Go.EventHandler = function() hb:Start(1) end"
    assert not any("eventually be deleted" in w for w in check_syntax(held)["warnings"])


def test_global_timer_is_not_reported():
    code = "hb = Timer.New()\nhb.EventHandler = function() hb:Stop() end\nhb:Start(1)"
    assert check_syntax(code)["warnings"] == []

