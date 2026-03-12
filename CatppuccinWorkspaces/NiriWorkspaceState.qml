import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    visible: false
    width: 0
    height: 0

    readonly property var nameMap: ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    readonly property var hiddenNames: ["utils", "gaming", "sozial"]

    property var workspaces: []
    property var dynamicWorkspaces: []
    property var windows: ({})
    property string focusedWindowTitle: ""
    property string focusedWindowAppId: ""
    property int focusedWorkspaceId: -1

    function updateDynamicWorkspaces() {
        var staticNames = {}
        for (var i = 0; i < root.nameMap.length; i++)
            staticNames[root.nameMap[i]] = true
        var hidden = {}
        for (var i = 0; i < root.hiddenNames.length; i++)
            hidden[root.hiddenNames[i]] = true
        var dynamic = []
        for (var j = 0; j < root.workspaces.length; j++) {
            var ws = root.workspaces[j]
            if (ws.name && staticNames[ws.name])
                continue
            if (ws.name && hidden[ws.name]) {
                if (ws.is_focused)
                    dynamic.push(ws)
                continue
            }
            if (!ws.name) {
                if (ws.active_window_id != null || ws.is_focused)
                    dynamic.push(ws)
                continue
            }
            dynamic.push(ws)
        }
        dynamic.sort(function(a, b) { return a.idx - b.idx })
        root.dynamicWorkspaces = dynamic
    }

    function getWorkspaceByName(name) {
        for (var i = 0; i < root.workspaces.length; i++) {
            if (root.workspaces[i].name === name)
                return root.workspaces[i]
        }
        return null
    }

    function switchWorkspace(name) {
        Quickshell.execDetached(["niri", "msg", "action", "focus-workspace", name])
    }

    function switchDynamicWorkspace(ws) {
        var ref = ws.name ? ws.name : ws.idx.toString()
        Quickshell.execDetached(["niri", "msg", "action", "focus-workspace", ref])
    }

    function handleWorkspacesChanged(wsList) {
        root.workspaces = wsList
        for (var i = 0; i < wsList.length; i++) {
            if (wsList[i].is_focused) {
                root.focusedWorkspaceId = wsList[i].id
                break
            }
        }
        root.updateDynamicWorkspaces()
    }

    function handleWindowsChanged(winList) {
        var map = {}
        for (var i = 0; i < winList.length; i++) {
            map[winList[i].id] = winList[i]
        }
        root.windows = map
    }

    function handleWindowFocusChanged(data) {
        if (data && data.id != null) {
            var win = root.windows[data.id]
            if (win) {
                root.focusedWindowTitle = win.title || ""
                root.focusedWindowAppId = win.app_id || ""
            }
        } else {
            root.focusedWindowTitle = ""
            root.focusedWindowAppId = ""
        }
    }

    function handleWorkspaceActiveWindowChanged(data) {
        root.workspaces = root.workspaces.map(ws => {
            if (ws.id === data.workspace_id) {
                var copy = Object.assign({}, ws)
                copy.active_window_id = data.active_window_id
                return copy
            }
            return ws
        })
        root.updateDynamicWorkspaces()
    }

    function handleWorkspaceActivated(data) {
        root.workspaces = root.workspaces.map(ws => {
            var copy = Object.assign({}, ws)
            if (data.focused) {
                copy.is_focused = (ws.id === data.id)
            }
            copy.is_active = (ws.id === data.id) ? true : (ws.output === (root.workspaces.find(w => w.id === data.id) || {}).output ? false : ws.is_active)
            return copy
        })
        root.focusedWorkspaceId = data.id
        root.updateDynamicWorkspaces()
    }

    // Event stream — long-running process
    Process {
        id: eventStream
        command: ["niri", "msg", "-j", "event-stream"]
        running: true

        stdout: SplitParser {
            onRead: data => {
                try {
                    var event = JSON.parse(data.trim())
                    if (event.WorkspacesChanged)
                        root.handleWorkspacesChanged(event.WorkspacesChanged.workspaces)
                    else if (event.WindowsChanged)
                        root.handleWindowsChanged(event.WindowsChanged.windows)
                    else if (event.WindowFocusChanged)
                        root.handleWindowFocusChanged(event.WindowFocusChanged)
                    else if (event.WorkspaceActivated)
                        root.handleWorkspaceActivated(event.WorkspaceActivated)
                    else if (event.WorkspaceActiveWindowChanged)
                        root.handleWorkspaceActiveWindowChanged(event.WorkspaceActiveWindowChanged)
                } catch (e) {}
            }
        }
    }

    // Initial workspace fetch
    Process {
        id: initWorkspaces
        command: ["niri", "msg", "-j", "workspaces"]
        running: true

        stdout: SplitParser {
            onRead: data => {
                try {
                    var wsList = JSON.parse(data.trim())
                    root.handleWorkspacesChanged(wsList)
                } catch (e) {}
            }
        }
    }

    // Initial focused window fetch
    Process {
        id: initFocusedWindow
        command: ["niri", "msg", "-j", "focused-window"]
        running: true

        stdout: SplitParser {
            onRead: data => {
                try {
                    var win = JSON.parse(data.trim())
                    root.focusedWindowTitle = win.title || ""
                    root.focusedWindowAppId = win.app_id || ""
                } catch (e) {}
            }
        }
    }
}
