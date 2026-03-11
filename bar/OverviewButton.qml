import QtQuick
import Quickshell

Rectangle {
    id: root

    property bool isHovered: mouseArea.containsMouse

    implicitWidth: label.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: isHovered ? Theme.surface0 : Theme.base
    radius: Theme.pillRadius

    Text {
        id: label
        anchors.centerIn: parent
        text: "󰕰"
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSize
        font.bold: true
        color: isHovered ? Theme.text : Theme.subtext0
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: Quickshell.execDetached(["niri", "msg", "action", "toggle-overview"])
    }
}
