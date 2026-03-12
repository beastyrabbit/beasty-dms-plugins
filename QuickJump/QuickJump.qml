import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Common
import qs.Modules.Plugins

PluginComponent {
    id: root

    layerNamespacePlugin: "quick-jump"

    // Catppuccin Mocha colors (match CatppuccinWorkspaces)
    readonly property color catText: "#cdd6f4"
    readonly property color catSubtext0: "#a6adc8"
    readonly property color catSurface0: "#65687a"
    readonly property color catCrust: "#222228"
    readonly property color catMauve: "#cba6f7"

    readonly property int _iconSize: 18

    // App definitions
    readonly property var apps: [
        { appId: "discord", label: "Discord" },
        { appId: "brave-hnpfjngllnobngcgfapefoaidbinmjnm-Default", label: "WhatsApp" },
        { appId: "Fastmail", label: "Fastmail" },
        { appId: "steam", label: "Steam" },
        { appId: "1password", label: "1Password" }
    ]

    // Back button state
    property var _previousToplevel: null
    property bool _hasJumpHistory: false

    // Running apps tracking
    property var _runningAppIds: ({})
    readonly property var _activeToplevel: ToplevelManager.activeToplevel

    readonly property bool _anyAppRunning: {
        for (const app of apps) {
            if (_runningAppIds[app.appId]) return true;
        }
        return false;
    }

    Connections {
        target: ToplevelManager.toplevels
        function onValuesChanged() {
            root._rebuildRunning();
        }
    }

    function _rebuildRunning() {
        const ids = {};
        let prevStillAlive = false;
        for (const t of ToplevelManager.toplevels.values) {
            if (t.appId) ids[t.appId] = true;
            if (t === _previousToplevel) prevStillAlive = true;
        }
        _runningAppIds = ids;
        if (!prevStillAlive && _hasJumpHistory) {
            _previousToplevel = null;
            _hasJumpHistory = false;
        }
    }

    function _jumpTo(appId) {
        const current = ToplevelManager.activeToplevel;

        for (const t of ToplevelManager.toplevels.values) {
            if (t.appId === appId) {
                if (current && current.appId !== appId) {
                    _previousToplevel = current;
                    _hasJumpHistory = true;
                }
                t.activate();
                return;
            }
        }
    }

    function _jumpBack() {
        if (_previousToplevel) {
            _previousToplevel.activate();
            _previousToplevel = null;
            _hasJumpHistory = false;
        }
    }

    function _getIconSource(appId) {
        const entry = DesktopEntries.heuristicLookup(appId);
        return Paths.getAppIcon(appId, entry);
    }

    Component.onCompleted: _rebuildRunning()

    horizontalBarPill: Component {
        Row {
            spacing: 0

            // Back button
            Rectangle {
                visible: root._hasJumpHistory
                width: root._iconSize + 10
                height: root.widgetThickness - 6
                anchors.verticalCenter: parent.verticalCenter
                radius: hBackMouse.containsMouse ? 10 : 0
                color: hBackMouse.containsMouse ? root.catSurface0 : "transparent"

                Behavior on color { ColorAnimation { duration: 150 } }
                Behavior on radius { NumberAnimation { duration: 150 } }

                Text {
                    anchors.centerIn: parent
                    text: "\uf060"
                    font.family: "OpenDyslexic Nerd Font"
                    font.pixelSize: 16
                    font.bold: true
                    color: hBackMouse.containsMouse ? root.catText : root.catSubtext0
                }

                MouseArea {
                    id: hBackMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root._jumpBack()
                }
            }

            // Separator between back button and app buttons
            Rectangle {
                visible: root._hasJumpHistory && root._anyAppRunning
                width: 2
                height: root.widgetThickness - 12
                anchors.verticalCenter: parent.verticalCenter
                color: root.catSurface0
                radius: 1
            }

            // App buttons
            Repeater {
                model: root.apps

                delegate: Rectangle {
                    required property var modelData
                    required property int index

                    property bool isRunning: root._runningAppIds[modelData.appId] === true
                    property bool isFocused: root._activeToplevel && root._activeToplevel.appId === modelData.appId
                    property bool isHovered: hAppMouse.containsMouse

                    visible: isRunning
                    width: root._iconSize + 10
                    height: root.widgetThickness - 6
                    anchors.verticalCenter: parent.verticalCenter
                    radius: isHovered || isFocused ? 10 : 0
                    color: isFocused ? root.catMauve : (isHovered ? root.catSurface0 : "transparent")

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Image {
                        anchors.centerIn: parent
                        source: root._getIconSource(modelData.appId)
                        width: root._iconSize
                        height: root._iconSize
                        sourceSize: Qt.size(root._iconSize, root._iconSize)
                        mipmap: true
                        asynchronous: true
                    }

                    MouseArea {
                        id: hAppMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root._jumpTo(modelData.appId)
                    }
                }
            }
        }
    }

    verticalBarPill: Component {
        Column {
            spacing: 0

            // Back button
            Rectangle {
                visible: root._hasJumpHistory
                width: root.widgetThickness - 6
                height: root._iconSize + 10
                anchors.horizontalCenter: parent.horizontalCenter
                radius: vBackMouse.containsMouse ? 10 : 0
                color: vBackMouse.containsMouse ? root.catSurface0 : "transparent"

                Behavior on color { ColorAnimation { duration: 150 } }
                Behavior on radius { NumberAnimation { duration: 150 } }

                Text {
                    anchors.centerIn: parent
                    text: "\uf060"
                    font.family: "OpenDyslexic Nerd Font"
                    font.pixelSize: 16
                    font.bold: true
                    color: vBackMouse.containsMouse ? root.catText : root.catSubtext0
                }

                MouseArea {
                    id: vBackMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root._jumpBack()
                }
            }

            // Separator
            Rectangle {
                visible: root._hasJumpHistory && root._anyAppRunning
                width: root.widgetThickness - 12
                height: 2
                anchors.horizontalCenter: parent.horizontalCenter
                color: root.catSurface0
                radius: 1
            }

            // App buttons
            Repeater {
                model: root.apps

                delegate: Rectangle {
                    required property var modelData
                    required property int index

                    property bool isRunning: root._runningAppIds[modelData.appId] === true
                    property bool isFocused: root._activeToplevel && root._activeToplevel.appId === modelData.appId
                    property bool isHovered: vAppMouse.containsMouse

                    visible: isRunning
                    width: root.widgetThickness - 6
                    height: root._iconSize + 10
                    anchors.horizontalCenter: parent.horizontalCenter
                    radius: isHovered || isFocused ? 10 : 0
                    color: isFocused ? root.catMauve : (isHovered ? root.catSurface0 : "transparent")

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Image {
                        anchors.centerIn: parent
                        source: root._getIconSource(modelData.appId)
                        width: root._iconSize
                        height: root._iconSize
                        sourceSize: Qt.size(root._iconSize, root._iconSize)
                        mipmap: true
                        asynchronous: true
                    }

                    MouseArea {
                        id: vAppMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root._jumpTo(modelData.appId)
                    }
                }
            }
        }
    }
}
