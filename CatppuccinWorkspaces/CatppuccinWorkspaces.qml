import QtQuick
import Quickshell
import Quickshell.Io
import qs.Modules.Plugins

PluginComponent {
    id: root

    layerNamespacePlugin: "catppuccin-workspaces"

    // Catppuccin Mocha accent colors (pill bg comes from DMS theme)
    readonly property color catText: "#cdd6f4"
    readonly property color catSubtext0: "#a6adc8"
    readonly property color catSurface0: "#65687a"
    readonly property color catCrust: "#222228"

    readonly property var rainbow: [
        "#f38ba8", "#fab387", "#f9e2af", "#a6e3a1", "#94e2d5",
        "#89dceb", "#74c7ec", "#89b4fa", "#cba6f7", "#f5c2e7",
        "#f2cdcd", "#eba0ac", "#b4befe"
    ]

    // Workspace state tracker
    NiriWorkspaceState {
        id: niriState
    }

    // Right-click toggles niri overview
    pillRightClickAction: function() {
        Quickshell.execDetached(["niri", "msg", "action", "toggle-overview"])
    }

    horizontalBarPill: Component {
        Row {
            id: wsRow
            spacing: 0

            // 9 named workspaces with rainbow colors
            Repeater {
                model: 9

                delegate: Rectangle {
                    id: wsButton

                    required property int index

                    property string wsName: niriState.nameMap[index]
                    property var wsData: niriState.getWorkspaceByName(wsName)
                    property color wsColor: root.rainbow[index % root.rainbow.length]
                    property bool isFocused: wsData ? wsData.is_focused : false
                    property bool isUrgent: wsData ? wsData.is_urgent : false
                    property bool isHovered: wsMouseArea.containsMouse

                    width: wsLabel.implicitWidth + 10
                    height: root.widgetThickness - 6
                    anchors.verticalCenter: parent.verticalCenter
                    radius: isHovered || isFocused || isUrgent ? 10 : 0
                    color: (isHovered || isUrgent || isFocused) ? wsColor : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: wsLabel
                        anchors.centerIn: parent
                        text: (wsButton.index + 1).toString()
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        color: (wsButton.isHovered || wsButton.isUrgent || wsButton.isFocused) ? root.catCrust : wsButton.wsColor
                    }

                    MouseArea {
                        id: wsMouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: niriState.switchWorkspace(wsButton.wsName)
                    }
                }
            }

            // Separator before dynamic workspaces
            Rectangle {
                visible: niriState.dynamicWorkspaces.length > 0
                width: 2
                height: root.widgetThickness - 12
                anchors.verticalCenter: parent.verticalCenter
                color: root.catSurface0
                radius: 1
            }

            // Dynamic workspaces
            Repeater {
                model: niriState.dynamicWorkspaces

                delegate: Rectangle {
                    id: dynButton

                    required property var modelData

                    property bool isFocused: modelData.is_focused
                    property bool isUrgent: modelData.is_urgent
                    property bool isHovered: dynMouseArea.containsMouse

                    width: dynLabel.implicitWidth + 10
                    height: root.widgetThickness - 6
                    anchors.verticalCenter: parent.verticalCenter
                    radius: isHovered || isFocused || isUrgent ? 10 : 0
                    color: (isHovered || isUrgent || isFocused) ? root.catSurface0 : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: dynLabel
                        anchors.centerIn: parent
                        text: dynButton.modelData.name || ("+" + dynButton.modelData.idx)
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        color: (dynButton.isHovered || dynButton.isUrgent || dynButton.isFocused) ? root.catText : root.catSubtext0
                    }

                    MouseArea {
                        id: dynMouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: niriState.switchDynamicWorkspace(dynButton.modelData)
                    }
                }
            }
        }
    }

    verticalBarPill: Component {
        Column {
            id: vsCol
            spacing: 0

            Repeater {
                model: 9

                delegate: Rectangle {
                    id: vWsButton

                    required property int index

                    property string wsName: niriState.nameMap[index]
                    property var wsData: niriState.getWorkspaceByName(wsName)
                    property color wsColor: root.rainbow[index % root.rainbow.length]
                    property bool isFocused: wsData ? wsData.is_focused : false
                    property bool isUrgent: wsData ? wsData.is_urgent : false
                    property bool isHovered: vWsMouseArea.containsMouse

                    width: root.widgetThickness - 6
                    height: vWsLabel.implicitHeight + 10
                    anchors.horizontalCenter: parent.horizontalCenter
                    radius: isHovered || isFocused || isUrgent ? 10 : 0
                    color: (isHovered || isUrgent || isFocused) ? wsColor : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: vWsLabel
                        anchors.centerIn: parent
                        text: (vWsButton.index + 1).toString()
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        color: (vWsButton.isHovered || vWsButton.isUrgent || vWsButton.isFocused) ? root.catCrust : vWsButton.wsColor
                    }

                    MouseArea {
                        id: vWsMouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: niriState.switchWorkspace(vWsButton.wsName)
                    }
                }
            }

            Rectangle {
                visible: niriState.dynamicWorkspaces.length > 0
                width: root.widgetThickness - 12
                height: 2
                anchors.horizontalCenter: parent.horizontalCenter
                color: root.catSurface0
                radius: 1
            }

            Repeater {
                model: niriState.dynamicWorkspaces

                delegate: Rectangle {
                    id: vDynButton

                    required property var modelData

                    property bool isFocused: modelData.is_focused
                    property bool isUrgent: modelData.is_urgent
                    property bool isHovered: vDynMouseArea.containsMouse

                    width: root.widgetThickness - 6
                    height: vDynLabel.implicitHeight + 10
                    anchors.horizontalCenter: parent.horizontalCenter
                    radius: isHovered || isFocused || isUrgent ? 10 : 0
                    color: (isHovered || isUrgent || isFocused) ? root.catSurface0 : "transparent"

                    Behavior on color { ColorAnimation { duration: 150 } }
                    Behavior on radius { NumberAnimation { duration: 150 } }

                    Text {
                        id: vDynLabel
                        anchors.centerIn: parent
                        text: vDynButton.modelData.name || ("+" + vDynButton.modelData.idx)
                        font.family: "OpenDyslexic Nerd Font"
                        font.pixelSize: 16
                        font.bold: true
                        color: (vDynButton.isHovered || vDynButton.isUrgent || vDynButton.isFocused) ? root.catText : root.catSubtext0
                    }

                    MouseArea {
                        id: vDynMouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: niriState.switchDynamicWorkspace(vDynButton.modelData)
                    }
                }
            }
        }
    }
}
