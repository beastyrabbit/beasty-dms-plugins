import QtQuick

Rectangle {
    id: root

    property var iconMap: ({
        "1": "",
        "2": "",
        "6": "",
        "7": ""
    })

    property var rainbow: [
        Theme.red, Theme.peach, Theme.yellow, Theme.green, Theme.teal,
        Theme.sky, Theme.sapphire, Theme.blue, Theme.mauve, Theme.pink,
        Theme.flamingo, Theme.rosewater, Theme.lavender
    ]

    implicitWidth: wsRow.implicitWidth + Theme.pillPaddingH + 5
    implicitHeight: Theme.pillHeight
    color: Theme.base
    radius: Theme.pillRadius

    Row {
        id: wsRow
        anchors.centerIn: parent
        spacing: 0

        Repeater {
            model: 9

            delegate: Rectangle {
                id: wsButton

                required property int index

                property string wsName: NiriState.nameMap[index]
                property var wsData: NiriState.getWorkspaceByName(wsName)
                property string wsIcon: root.iconMap[(index + 1).toString()] || ""
                property color wsColor: root.rainbow[index % root.rainbow.length]
                property bool isFocused: wsData ? wsData.is_focused : false
                property bool isActive: wsData ? wsData.is_active : false
                property bool isUrgent: wsData ? wsData.is_urgent : false
                property bool isHovered: mouseArea.containsMouse

                width: wsLabel.implicitWidth + 10
                height: root.height - 6
                radius: isHovered || isFocused || isUrgent ? Theme.pillRadius : 0
                color: {
                    if (isHovered) return wsButton.wsColor
                    if (isUrgent) return wsButton.wsColor
                    if (isFocused) return wsButton.wsColor
                    return "transparent"
                }

                Text {
                    id: wsLabel
                    anchors.centerIn: parent
                    text: (index + 1).toString()
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSize
                    font.bold: true
                    color: {
                        if (wsButton.isHovered) return Theme.crust
                        if (wsButton.isUrgent) return Theme.crust
                        if (wsButton.isFocused) return Theme.crust
                        return wsButton.wsColor
                    }
                }

                MouseArea {
                    id: mouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: NiriState.switchWorkspace(wsButton.wsName)
                }
            }
        }

        // Separator before dynamic workspaces
        Rectangle {
            visible: NiriState.dynamicWorkspaces.length > 0
            width: 2
            height: root.height - 12
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.surface0
            radius: 1
        }

        // Dynamic workspaces
        Repeater {
            model: NiriState.dynamicWorkspaces

            delegate: Rectangle {
                id: dynButton

                required property var modelData

                property bool isFocused: modelData.is_focused
                property bool isActive: modelData.is_active
                property bool isUrgent: modelData.is_urgent
                property bool isHovered: dynMouseArea.containsMouse

                width: dynLabel.implicitWidth + 10
                height: root.height - 6
                radius: isHovered || isFocused || isUrgent ? Theme.pillRadius : 0
                color: {
                    if (isHovered) return Theme.surface0
                    if (isUrgent) return Theme.surface0
                    if (isFocused) return Theme.surface0
                    return "transparent"
                }

                Text {
                    id: dynLabel
                    anchors.centerIn: parent
                    text: dynButton.modelData.name || ("+" + dynButton.modelData.idx)
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSize
                    font.bold: true
                    color: {
                        if (dynButton.isHovered) return Theme.text
                        if (dynButton.isUrgent) return Theme.text
                        if (dynButton.isFocused) return Theme.text
                        return Theme.subtext0
                    }
                }

                MouseArea {
                    id: dynMouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: NiriState.switchDynamicWorkspace(dynButton.modelData)
                }
            }
        }
    }
}
