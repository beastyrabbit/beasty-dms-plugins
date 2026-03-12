import QtQuick
import Quickshell

Rectangle {
    id: root

    property bool hasUnread: NotificationService.unreadCount > 0
    property bool hasNotifications: NotificationStore.count > 0
    property bool isHovered: mouseArea.containsMouse

    implicitWidth: notifRow.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base
    radius: Theme.pillRadius

    Row {
        id: notifRow
        anchors.centerIn: parent
        spacing: 4

        Text {
            id: notifLabel
            color: root.hasUnread ? Theme.blue : (root.hasNotifications ? Theme.red : (root.isHovered ? Theme.text : Theme.subtext0))
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSize
            font.bold: true
            text: root.hasUnread ? "󰂚" : (root.hasNotifications ? "󰂚" : "󰂜")
        }

        Text {
            visible: root.hasUnread
            text: NotificationService.unreadCount.toString()
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSize - 2
            font.bold: true
            color: Theme.blue
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onEntered: {
            NotificationService.bellHovered = true
            NotificationService.showPanel()
        }
        onExited: {
            NotificationService.bellHovered = false
            NotificationService.scheduleClose()
        }
        onClicked: mouse => {
            if (mouse.button === Qt.RightButton)
                NotificationService.dismissAll()
            else
                NotificationService.togglePanel()
        }
    }
}
