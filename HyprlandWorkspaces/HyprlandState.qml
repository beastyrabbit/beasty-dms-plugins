import QtQuick
import Quickshell
import Quickshell.Hyprland

Item {
    id: root
    visible: false
    width: 0
    height: 0

    // --- Configuration: Named Workspaces ---
    readonly property var namedWorkspaces: [
        { name: "security",  icon: "\uf084", label: "Passwords" },
        { name: "sozial",    icon: "\uf232", label: "WhatsApp" },
        { name: "discord",   icon: "\uf1ff", label: "Discord" },
        { name: "tools",     icon: "\uf0ad", label: "Tools" }
    ]

    // --- Configuration: Scratchpads ---
    readonly property var scratchpads: [
        { name: "scratch1", icon: "\uf120", label: "Scratch 1" },
        { name: "scratch2", icon: "\uf121", label: "Scratch 2" },
        { name: "scratch3", icon: "\uf135", label: "Scratch 3" }
    ]

    // --- Exposed State ---
    readonly property string activeSpecialName:
        Hyprland.focusedMonitor?.lastIpcObject?.specialWorkspace?.name ?? ""

    // --- Actions ---
    function switchWorkspace(num) {
        Hyprland.dispatch("workspace " + num)
    }

    function switchNamedWorkspace(name) {
        Hyprland.dispatch("workspace name:" + name)
    }

    function toggleSpecial(name) {
        Hyprland.dispatch("togglespecialworkspace " + name)
    }

    // --- Query Functions ---
    function isRegularFocused(wsId) {
        return Hyprland.focusedWorkspace !== null
            && Hyprland.focusedWorkspace.id === wsId
            && activeSpecialName === ""
    }

    function isNamedFocused(name) {
        return Hyprland.focusedWorkspace !== null
            && Hyprland.focusedWorkspace.name === name
            && activeSpecialName === ""
    }

    function namedHasWindows(name) {
        var ws = _findWorkspaceByName(name)
        return ws ? ws.lastIpcObject.windows > 0 : false
    }

    function isSpecialFocused(shortName) {
        return activeSpecialName === "special:" + shortName
    }

    function specialHasWindows(shortName) {
        var ws = _findWorkspaceByName("special:" + shortName)
        return ws ? ws.lastIpcObject.windows > 0 : false
    }

    // --- Internal Helpers ---
    function _findWorkspaceByName(name) {
        var values = Hyprland.workspaces.values
        for (var i = 0; i < values.length; i++) {
            if (values[i].name === name) return values[i]
        }
        return null
    }

    // --- Event Refresh ---
    Connections {
        target: Hyprland

        function onRawEvent(event) {
            var n = event.name
            if (n.endsWith("v2")) return

            if (["workspace", "moveworkspace", "activespecial", "focusedmon", "urgent"].includes(n)) {
                Hyprland.refreshWorkspaces()
                Hyprland.refreshMonitors()
            } else if (["openwindow", "closewindow", "movewindow"].includes(n)) {
                Hyprland.refreshToplevels()
                Hyprland.refreshWorkspaces()
            } else if (n.includes("mon")) {
                Hyprland.refreshMonitors()
            } else if (n.includes("workspace")) {
                Hyprland.refreshWorkspaces()
            } else if (n.includes("window") || n.includes("group") || ["pin", "fullscreen", "changefloatingmode", "minimize"].includes(n)) {
                Hyprland.refreshToplevels()
            }
        }
    }
}
