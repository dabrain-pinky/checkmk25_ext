#!/usr/bin/env python3
"""Pause the automatic refresh of the Checkmk GUI

* Views, graph pages and similar pages reload themselves periodically (the
  counter in the top right corner of the page). A pause/resume button is added
  to the bottom left corner of the page; the counter shows "off" while paused.
* Dashboards refresh their widgets (graphs, gauges, top lists, ...) in the
  background. A pause/resume button is added to the bottom right corner of the
  dashboard. While paused,
  repeated widget refresh requests are held back and sent once the refresh is
  resumed. First loads, filter changes and resizes still go through.
* Views embedded in dashboards (iframes) follow the same pause state.

The state is shared by all of these pages and remembered per browser
(localStorage). Toggling it in one tab or frame applies to all others at once.
"""

import json

from cmk.gui import hooks
from cmk.gui.htmllib.html import html
from cmk.gui.utils.html import HTML

_CORE_JS = r"""
(function () {
    if (window.cmk_auto_reload_toggle) {
        return;
    }

    var KEY = "cmk_auto_reload_disabled";
    var listeners = [];

    function is_paused() {
        try {
            return window.localStorage.getItem(KEY) === "1";
        } catch (e) {
            return false;
        }
    }

    function notify() {
        var paused = is_paused();
        for (var i = 0; i < listeners.length; i++) {
            listeners[i](paused);
        }
    }

    function set_paused(paused) {
        try {
            if (paused) {
                window.localStorage.setItem(KEY, "1");
            } else {
                window.localStorage.removeItem(KEY);
            }
        } catch (e) {}
        notify();
    }

    // Other tabs and frames (e.g. views embedded in a dashboard) get informed
    // about changes via the storage event.
    window.addEventListener("storage", function (event) {
        if (event.key === KEY || event.key === null) {
            notify();
        }
    });

    function on_change(func) {
        listeners.push(func);
    }

    // The pause/resume button, used on views and dashboards. It floats in a
    // corner of the page ("left" or "right") and shows the current state.
    function make_button(side, labels) {
        var button = document.createElement("a");
        button.className = "auto_reload_toggle";
        button.href = "javascript:void(0)";
        button.style.cssText =
            "position:fixed;" + side + ":16px;bottom:16px;z-index:10000;padding:6px 12px;" +
            "border-radius:4px;font-size:12px;text-decoration:none;cursor:pointer;" +
            "box-shadow:0 1px 4px rgba(0,0,0,.4);color:#fff;";
        button.addEventListener("click", function (event) {
            event.preventDefault();
            set_paused(!is_paused());
        });

        function attach() {
            if (!button.parentNode) {
                document.body.appendChild(button);
            }
        }
        if (document.body) {
            attach();
        } else {
            document.addEventListener("DOMContentLoaded", attach);
        }

        return function show(paused) {
            if (paused) {
                button.textContent = "\u25B6 " + labels.paused;
                button.title = labels.paused_title;
                button.style.background = "#d28b00";
            } else {
                button.textContent = "\u23F8 " + labels.running;
                button.title = labels.running_title;
                button.style.background = "rgba(40,40,40,.75)";
            }
        };
    }

    // Pages with a periodic page reload (views, graph pages, embedded views).
    // Embedded views (no page heading) have no button of their own, they
    // follow the state set on the dashboard.
    function init_page_reload(interval) {
        if (window.cmk_auto_reload_page_initialized) {
            return;
        }
        window.cmk_auto_reload_page_initialized = true;

        var state = document.querySelector("#top_heading div.page_state.reload");
        var show = null;
        var link, text, icon, orig_title;

        if (state) {
            link = state.querySelector("a");
            text = state.querySelector("div.text_container span");
            icon = document.getElementById("page_state_icon");
            orig_title = state.title;
            show = make_button("left", {
                running: "Pause auto reload",
                running_title: "Pause the automatic reload of this page",
                paused: "Auto reload paused",
                paused_title: "Automatic page reload is paused. Click to resume."
            });
        }

        function show_state(paused) {
            if (!show) {
                return;
            }
            show(paused);
            if (paused) {
                state.title = "Automatic page reload paused";
                if (text) text.textContent = "off";
                if (icon) icon.style.clipPath = "";
                if (link) link.style.opacity = "0.5";
            } else {
                state.title = orig_title;
                if (text) text.textContent = String(interval);
                if (link) link.style.opacity = "";
            }
        }

        // The page has already started its reload timer when this runs.
        var applied = false;
        function apply(paused) {
            if (paused !== applied) {
                applied = paused;
                if (paused) {
                    cmk.utils.stop_reload_timer();
                } else {
                    cmk.utils.set_reload(interval);
                }
            }
            show_state(paused);
        }

        on_change(apply);
        apply(is_paused());
    }

    // Dashboards: the widgets poll their data via XHR/fetch. While paused,
    // requests that repeat an earlier request (= periodic refresh) are held
    // back. On resume the latest held back request per widget is sent.
    var REFRESH_URL = new RegExp(
        "(^|/)(widget_graph|widget_figure|compute_top_list_data|ajax_get_user_messages)" +
            "(_token_auth)?\\.py"
    );

    function request_key(url, body) {
        var clean = String(url).replace(/([?&])_ajaxid=[^&]*&?/, "$1").replace(/[?&]$/, "");
        return clean + "\n" + (typeof body === "string" ? body : "");
    }

    function init_dashboard() {
        if (window.cmk_auto_reload_dashboard_initialized) {
            return;
        }
        window.cmk_auto_reload_dashboard_initialized = true;

        var seen = {};
        var held_xhr = {};
        var held_fetch = [];

        var orig_open = XMLHttpRequest.prototype.open;
        var orig_send = XMLHttpRequest.prototype.send;
        var orig_fetch = window.fetch;

        XMLHttpRequest.prototype.open = function (method, url) {
            this.__cmk_auto_reload_url = url;
            return orig_open.apply(this, arguments);
        };

        XMLHttpRequest.prototype.send = function (body) {
            var url = this.__cmk_auto_reload_url;
            if (url && REFRESH_URL.test(url)) {
                var key = request_key(url, body);
                if (is_paused() && seen[key]) {
                    held_xhr[key] = {xhr: this, body: body};
                    return;
                }
                seen[key] = true;
            }
            return orig_send.apply(this, arguments);
        };

        if (orig_fetch) {
            window.fetch = function (input, init) {
                var url = typeof input === "string" ? input : input && input.url;
                if (url && REFRESH_URL.test(url)) {
                    var key = request_key(url, init && init.body);
                    if (is_paused() && seen[key]) {
                        var self = this;
                        return new Promise(function (resolve, reject) {
                            held_fetch.push(function () {
                                orig_fetch.call(self, input, init).then(resolve, reject);
                            });
                        });
                    }
                    seen[key] = true;
                }
                return orig_fetch.apply(this, arguments);
            };
        }

        function release() {
            var xhrs = held_xhr;
            var fetches = held_fetch;
            held_xhr = {};
            held_fetch = [];
            Object.keys(xhrs).forEach(function (key) {
                orig_send.call(xhrs[key].xhr, xhrs[key].body);
            });
            fetches.forEach(function (send) {
                send();
            });
        }

        var show = make_button("right", {
            running: "Pause auto refresh",
            running_title: "Pause the automatic refresh of all dashboard widgets",
            paused: "Auto refresh paused",
            paused_title: "Widgets and embedded views are not refreshed. Click to resume."
        });

        function apply(paused) {
            show(paused);
            if (!paused) {
                release();
            }
        }

        on_change(apply);
        apply(is_paused());
    }

    window.cmk_auto_reload_toggle = {
        is_paused: is_paused,
        set_paused: set_paused,
        init_page_reload: init_page_reload,
        init_dashboard: init_dashboard
    };
})();
"""


def _write_script(code: str) -> None:
    # html.javascript() rewrites "&", "<" and ">" to unicode escapes, which
    # breaks them outside of string literals (e.g. "a && b", "i < n"). The
    # code here is static (plus integers), so write the script tag directly.
    html.write_html(HTML.without_escaping("<script>%s</script>" % code))


def _output_core_js() -> None:
    _write_script(_CORE_JS)


def _add_page_reload_toggle() -> None:
    if html.output_format != "html":
        return
    # Only periodic reloads (views: >= 1s). Short reloads like the 0.8s
    # progress refresh on the users page are left alone.
    if not html.browser_reload or float(html.browser_reload) < 1:
        return
    _output_core_js()
    _write_script(
        "cmk_auto_reload_toggle.init_page_reload(%s);" % json.dumps(int(html.browser_reload))
    )


def _add_dashboard_refresh_toggle() -> None:
    if html.output_format != "html":
        return
    _output_core_js()
    _write_script("cmk_auto_reload_toggle.init_dashboard();")


hooks.register_from_plugin("body-ended", _add_page_reload_toggle)
hooks.register_from_plugin("dashboard-page-rendered", _add_dashboard_refresh_toggle)
