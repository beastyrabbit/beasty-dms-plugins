import QtQuick
import Quickshell
import Quickshell.Hyprland
import qs.Modules.Plugins

PluginComponent {
    id: root

    layerNamespacePlugin: "hyprland-workspaces"

    // Catppuccin Mocha palette
    readonly property color catText: "#cdd6f4"
    readonly property color catSubtext0: "#a6adc8"
    readonly property color catSurface0: "#65687a"
    readonly property color catCrust: "#222228"
    readonly property color catMauve: "#cba6f7"

    readonly property var rainbow: [
        "#f38ba8", "#fab387", "#f9e2af", "#a6e3a1", "#94e2d5",
        "#89dceb", "#74c7ec", "#89b4fa", "#cba6f7", "#f5c2e7",
        "#f2cdcd", "#eba0ac", "#b4befe"
    ]

    HyprlandState { id: hyprState }

    pillRightClickAction: function() {
        Hyprland.dispatch("overview:toggle")
    }

    // ==================== Horizontal Bar ====================
    horizontalBarPill: Component {
        Row {
            spacing: 0

            // --- Regular Workspaces 1-9 ---
            Repeater {
                model: 9

                delegate: Rectangle {
                    id: hWsButton

                    required property int index

                    property int wsNum: index + 1
                    property color wsColor: root.rainbow[index % root.rainbow.length]
                    property bool isFocused: hyprState.isRegularFocused(wsNum)
                    property bool isUrgent: {
                        var values = Hyprland.workspaces.values
                        for (var i = 0; i < values.length; i++) {
                            if (values[i].id === wsNum) return values[i].urgent
                        }
                        return false
                    }
                    property bool isHovered: hWsMouse.containsMouse

                    width: hWsLabel.implicitWidth + 10
                    height: root.widgetThickness - 6
                    anchors.verticalCenter: parent.verticalCenter
                    radius: (isHovered || isFocused || isUrgent) ? 10 : 0
                    color: (isHovered || isUrgent || isFocused) ? wsColor : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: hWsLabel
                        anchors.centerIn: parent
                        text: hWsButton.wsNum.toString()
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        color: (hWsButton.isHovered || hWsButton.isUrgent || hWsButton.isFocused)
                            ? root.catCrust : hWsButton.wsColor
                    }

                    MouseArea {
                        id: hWsMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: hyprState.switchWorkspace(hWsButton.wsNum)
                    }
                }
            }

            // --- Separator ---
            Rectangle {
                width: 2
                height: root.widgetThickness - 12
                anchors.verticalCenter: parent.verticalCenter
                color: root.catSurface0
                radius: 1
            }

            // --- Special Workspaces ---
            Repeater {
                model: hyprState.specialWorkspaces

                delegate: Rectangle {
                    id: hSpecialButton

                    required property var modelData
                    required property int index

                    property bool isFocused: hyprState.isSpecialFocused(modelData.name)
                    property bool hasWindows: hyprState.specialHasWindows(modelData.name)
                    property bool isHovered: hSpecialMouse.containsMouse

                    width: hSpecialIcon.implicitWidth + 10
                    height: root.widgetThickness - 6
                    anchors.verticalCenter: parent.verticalCenter
                    radius: (isHovered || isFocused) ? 10 : 0
                    color: isFocused ? root.catMauve
                         : isHovered ? root.catSurface0
                         : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: hSpecialIcon
                        anchors.centerIn: parent
                        text: hSpecialButton.modelData.icon
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        opacity: hSpecialButton.hasWindows || hSpecialButton.isFocused ? 1.0 : 0.35
                        color: hSpecialButton.isFocused ? root.catCrust
                             : hSpecialButton.isHovered ? root.catText
                             : hSpecialButton.hasWindows ? root.catSubtext0
                             : root.catSurface0

                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }

                    MouseArea {
                        id: hSpecialMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: hyprState.toggleSpecial(hSpecialButton.modelData.name)
                    }
                }
            }

            // --- Separator ---
            Rectangle {
                width: 2
                height: root.widgetThickness - 12
                anchors.verticalCenter: parent.verticalCenter
                color: root.catSurface0
                radius: 1
            }

            // --- Scratchpads ---
            Repeater {
                model: hyprState.scratchpads

                delegate: Rectangle {
                    id: hScratchButton

                    required property var modelData
                    required property int index

                    property bool isFocused: hyprState.isSpecialFocused(modelData.name)
                    property bool hasWindows: hyprState.specialHasWindows(modelData.name)
                    property bool isHovered: hScratchMouse.containsMouse

                    width: hScratchIcon.implicitWidth + 10
                    height: root.widgetThickness - 6
                    anchors.verticalCenter: parent.verticalCenter
                    radius: (isHovered || isFocused) ? 10 : 0
                    color: isFocused ? root.catMauve
                         : isHovered ? root.catSurface0
                         : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: hScratchIcon
                        anchors.centerIn: parent
                        text: hScratchButton.modelData.icon
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        opacity: hScratchButton.hasWindows || hScratchButton.isFocused ? 1.0 : 0.35
                        color: hScratchButton.isFocused ? root.catCrust
                             : hScratchButton.isHovered ? root.catText
                             : hScratchButton.hasWindows ? root.catSubtext0
                             : root.catSurface0

                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }

                    MouseArea {
                        id: hScratchMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: hyprState.toggleSpecial(hScratchButton.modelData.name)
                    }
                }
            }
        }
    }

    // ==================== Vertical Bar ====================
    verticalBarPill: Component {
        Column {
            spacing: 0

            // --- Regular Workspaces 1-9 ---
            Repeater {
                model: 9

                delegate: Rectangle {
                    id: vWsButton

                    required property int index

                    property int wsNum: index + 1
                    property color wsColor: root.rainbow[index % root.rainbow.length]
                    property bool isFocused: hyprState.isRegularFocused(wsNum)
                    property bool isUrgent: {
                        var values = Hyprland.workspaces.values
                        for (var i = 0; i < values.length; i++) {
                            if (values[i].id === wsNum) return values[i].urgent
                        }
                        return false
                    }
                    property bool isHovered: vWsMouse.containsMouse

                    width: root.widgetThickness - 6
                    height: vWsLabel.implicitHeight + 10
                    anchors.horizontalCenter: parent.horizontalCenter
                    radius: (isHovered || isFocused || isUrgent) ? 10 : 0
                    color: (isHovered || isUrgent || isFocused) ? wsColor : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: vWsLabel
                        anchors.centerIn: parent
                        text: vWsButton.wsNum.toString()
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        color: (vWsButton.isHovered || vWsButton.isUrgent || vWsButton.isFocused)
                            ? root.catCrust : vWsButton.wsColor
                    }

                    MouseArea {
                        id: vWsMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: hyprState.switchWorkspace(vWsButton.wsNum)
                    }
                }
            }

            // --- Separator ---
            Rectangle {
                width: root.widgetThickness - 12
                height: 2
                anchors.horizontalCenter: parent.horizontalCenter
                color: root.catSurface0
                radius: 1
            }

            // --- Special Workspaces ---
            Repeater {
                model: hyprState.specialWorkspaces

                delegate: Rectangle {
                    id: vSpecialButton

                    required property var modelData
                    required property int index

                    property bool isFocused: hyprState.isSpecialFocused(modelData.name)
                    property bool hasWindows: hyprState.specialHasWindows(modelData.name)
                    property bool isHovered: vSpecialMouse.containsMouse

                    width: root.widgetThickness - 6
                    height: vSpecialIcon.implicitHeight + 10
                    anchors.horizontalCenter: parent.horizontalCenter
                    radius: (isHovered || isFocused) ? 10 : 0
                    color: isFocused ? root.catMauve
                         : isHovered ? root.catSurface0
                         : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: vSpecialIcon
                        anchors.centerIn: parent
                        text: vSpecialButton.modelData.icon
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        opacity: vSpecialButton.hasWindows || vSpecialButton.isFocused ? 1.0 : 0.35
                        color: vSpecialButton.isFocused ? root.catCrust
                             : vSpecialButton.isHovered ? root.catText
                             : vSpecialButton.hasWindows ? root.catSubtext0
                             : root.catSurface0

                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }

                    MouseArea {
                        id: vSpecialMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: hyprState.toggleSpecial(vSpecialButton.modelData.name)
                    }
                }
            }

            // --- Separator ---
            Rectangle {
                width: root.widgetThickness - 12
                height: 2
                anchors.horizontalCenter: parent.horizontalCenter
                color: root.catSurface0
                radius: 1
            }

            // --- Scratchpads ---
            Repeater {
                model: hyprState.scratchpads

                delegate: Rectangle {
                    id: vScratchButton

                    required property var modelData
                    required property int index

                    property bool isFocused: hyprState.isSpecialFocused(modelData.name)
                    property bool hasWindows: hyprState.specialHasWindows(modelData.name)
                    property bool isHovered: vScratchMouse.containsMouse

                    width: root.widgetThickness - 6
                    height: vScratchIcon.implicitHeight + 10
                    anchors.horizontalCenter: parent.horizontalCenter
                    radius: (isHovered || isFocused) ? 10 : 0
                    color: isFocused ? root.catMauve
                         : isHovered ? root.catSurface0
                         : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: vScratchIcon
                        anchors.centerIn: parent
                        text: vScratchButton.modelData.icon
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        opacity: vScratchButton.hasWindows || vScratchButton.isFocused ? 1.0 : 0.35
                        color: vScratchButton.isFocused ? root.catCrust
                             : vScratchButton.isHovered ? root.catText
                             : vScratchButton.hasWindows ? root.catSubtext0
                             : root.catSurface0

                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }

                    MouseArea {
                        id: vScratchMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: hyprState.toggleSpecial(vScratchButton.modelData.name)
                    }
                }
            }
        }
    }
}
