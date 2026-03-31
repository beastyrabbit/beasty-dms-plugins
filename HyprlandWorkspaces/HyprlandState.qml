import QtQuick
import Quickshell
import Quickshell.Hyprland

Item {
    id: root
    visible: false
    width: 0
    height: 0

    // --- Configuration: Special Workspaces ---
    readonly property var specialWorkspaces: [
        { name: "sozial",    icon: "\uf075", label: "Sozial" },
        { name: "discord",   icon: "\uf392", label: "Discord" },
        { name: "1password", icon: "\uf023", label: "1Password" },
        { name: "tools",     icon: "\uf0ad", label: "Tools" }
    ]

    // --- Configuration: Scratchpads ---
    readonly property var scratchpads: [
        { name: "scratch1", icon: "\uf120", label: "Scratch 1" },
        { name: "scratch2", icon: "\uf120", label: "Scratch 2" },
        { name: "scratch3", icon: "\uf120", label: "Scratch 3" }
    ]

    // --- Exposed State ---
    readonly property string activeSpecialName:
        Hyprland.focusedMonitor?.lastIpcObject?.specialWorkspace?.name ?? ""

    // --- Actions ---
    function switchWorkspace(num) {
        Hyprland.dispatch("workspace " + num)
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
