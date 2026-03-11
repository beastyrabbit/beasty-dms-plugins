import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: root

    required property var modelData
    screen: modelData

    color: "transparent"
    visible: NotificationService.panelVisible

    anchors {
        top: true
        right: true
    }

    WlrLayershell.namespace: "quickshell-notif-panel"
    WlrLayershell.layer: WlrLayer.Overlay

    implicitWidth: 420
    implicitHeight: panelBody.height + 4

    exclusiveZone: 0

    // Hover keep-alive
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onEntered: {
            NotificationService.panelHovered = true
            NotificationService.showPanel()
        }
        onExited: {
            NotificationService.panelHovered = false
            NotificationService.scheduleClose()
        }
    }

    // Inverse concave corner — curves outward at top-left where panel meets bar
    Canvas {
        id: inverseCorner
        width: 28
        height: 28
        anchors.right: panelBody.left
        anchors.top: panelBody.top
        opacity: panelBody.opacity

        onPaint: {
            var ctx = getContext("2d")
            var r = width
            ctx.clearRect(0, 0, r, r)
            ctx.fillStyle = Theme.panelBg
            // Circle center at bottom-left, fill the outer part (top-right spandrel)
            ctx.beginPath()
            ctx.moveTo(0, 0)
            ctx.lineTo(r, 0)
            ctx.lineTo(r, r)
            ctx.arc(0, r, r, 0, -Math.PI / 2, true)
            ctx.closePath()
            ctx.fill()
        }
    }

    Rectangle {
        id: panelBody
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: NotificationService.panelVisible ? 0 : -14
        width: 392
        height: panelCol.implicitHeight + 48

        color: Theme.panelBg

        topLeftRadius: 0
        topRightRadius: 0
        bottomLeftRadius: 28
        bottomRightRadius: 28

        opacity: NotificationService.panelVisible ? 1 : 0

        Behavior on opacity {
            NumberAnimation { duration: Theme.animNormal; easing.type: Easing.OutCubic }
        }
        Behavior on anchors.topMargin {
            NumberAnimation { duration: Theme.animSlow; easing.type: Easing.OutCubic }
        }

        Column {
            id: panelCol
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 18
            anchors.topMargin: 16
            spacing: 12

            // Header
            Row {
                width: parent.width

                Text {
                    text: "Notifications"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSize + 1
                    font.bold: true
                    color: Theme.text
                    width: parent.width - clearBtn.width
                }

                Text {
                    id: clearBtn
                    visible: NotificationService.count > 0
                    text: "Clear"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSize - 2
                    color: clearArea.containsMouse ? Theme.red : Theme.dimText

                    Behavior on color {
                        ColorAnimation { duration: Theme.animFast }
                    }

                    MouseArea {
                        id: clearArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: NotificationService.dismissAll()
                    }
                }
            }

            // Separator
            Rectangle {
                width: parent.width
                height: 1
                color: Theme.border
            }

            // Empty state
            Column {
                visible: NotificationService.count === 0
                width: parent.width
                spacing: 6
                topPadding: 20
                bottomPadding: 20

                Text {
                    text: "󰆥"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSize + 12
                    color: Theme.dimText
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: "All clear"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSize
                    color: Theme.dimText
                    anchors.horizontalCenter: parent.horizontalCenter
                }
            }

            // Notification list
            Column {
                id: notifList
                width: parent.width
                spacing: 8

                Repeater {
                    model: NotificationService.tracked

                    delegate: Rectangle {
                        id: notifItem

                        required property var modelData
                        required property int index

                        property bool unread: NotificationService.isUnread(notifItem.modelData.id)

                        width: notifList.width
                        height: notifContent.implicitHeight + 16
                        radius: 16
                        color: itemHover.containsMouse ? Theme.cardHover : Theme.cardBg
                        border.color: notifItem.unread ? Theme.blue : (itemHover.containsMouse ? Theme.border : "transparent")
                        border.width: notifItem.unread ? 2 : 1

                        opacity: 0
                        Component.onCompleted: slideIn.start()

                        ParallelAnimation {
                            id: slideIn
                            running: false
                            NumberAnimation { target: notifItem; property: "opacity"; from: 0; to: 1; duration: Theme.animNormal; easing.type: Easing.OutCubic }
                            NumberAnimation { target: notifItem; property: "y"; from: notifItem.y - 20; to: notifItem.y; duration: Theme.animSlow; easing.type: Easing.OutCubic }
                        }

                        Behavior on color { ColorAnimation { duration: Theme.animFast } }
                        Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                        MouseArea {
                            id: itemHover
                            anchors.fill: parent
                            hoverEnabled: true

                            onEntered: {
                                NotificationService.panelHovered = true
                                NotificationService.showPanel()
                            }
                            onClicked: {
                                if (notifItem.unread) {
                                    NotificationService.markRead(notifItem.modelData.id)
                                    notifItem.unread = false
                                }
                            }
                        }

                        Row {
                            id: notifContent
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10

                            Rectangle {
                                id: panelIconBox
                                width: 54
                                height: 54
                                radius: 14
                                color: Theme.border
                                anchors.verticalCenter: parent.verticalCenter

                                Image {
                                    id: panelIconImg
                                    anchors.centerIn: parent
                                    width: 34
                                    height: 34
                                    sourceSize.width: 34
                                    sourceSize.height: 34
                                    source: {
                                        var icon = notifItem.modelData.appIcon || ""
                                        if (icon.length > 0) return Quickshell.iconPath(icon)
                                        var img = notifItem.modelData.image || ""
                                        if (img.length > 0) return img
                                        var de = notifItem.modelData.desktopEntry || ""
                                        if (de.length > 0) return Quickshell.iconPath(de)
                                        var name = (notifItem.modelData.appName || "").toLowerCase()
                                        if (name.length > 0) return Quickshell.iconPath(name)
                                        return ""
                                    }
                                    visible: status === Image.Ready
                                }

                                Text {
                                    anchors.centerIn: parent
                                    visible: !panelIconImg.visible
                                    text: "󰂜"
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 24
                                    color: Theme.dimText
                                }
                            }

                            Column {
                                width: parent.width - panelIconBox.width - dismissX.width - parent.spacing * 2
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 2

                                Text {
                                    visible: (notifItem.modelData.summary || "") !== ""
                                    text: notifItem.modelData.summary || ""
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSize
                                    font.bold: true
                                    color: Theme.text
                                    elide: Text.ElideRight
                                    width: parent.width
                                }

                                Text {
                                    visible: (notifItem.modelData.body || "") !== ""
                                    text: notifItem.modelData.body || ""
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSize - 2
                                    color: Theme.subtext0
                                    wrapMode: Text.WordWrap
                                    width: parent.width
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                }
                            }

                            Text {
                                id: dismissX
                                text: "󰅖"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSize - 1
                                color: xArea.containsMouse ? Theme.red : Theme.dimText
                                opacity: itemHover.containsMouse ? 1 : 0
                                anchors.verticalCenter: parent.verticalCenter

                                Behavior on color { ColorAnimation { duration: Theme.animFast } }
                                Behavior on opacity { NumberAnimation { duration: Theme.animFast } }

                                MouseArea {
                                    id: xArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: NotificationService.dismissNotification(notifItem.modelData)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
