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

        HoverHandler {
            id: panelHoverHandler
            onHoveredChanged: {
                if (hovered) {
                    NotificationService.panelHovered = true
                    NotificationService.showPanel()
                } else {
                    NotificationService.panelHovered = false
                    NotificationService.scheduleClose()
                }
            }
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
                    width: parent.width - stackToggle.width
                }

                Text {
                    id: stackToggle
                    visible: NotificationStore.count > 1
                    text: NotificationService.stackByApp ? "󰁅" : "󰁍"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSize + 2
                    color: stackToggleArea.containsMouse ? Theme.text : Theme.dimText

                    Behavior on color { ColorAnimation { duration: Theme.animFast } }

                    MouseArea {
                        id: stackToggleArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: NotificationService.toggleStacking()
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
                visible: NotificationStore.count === 0
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
            ListView {
                id: notifList
                width: parent.width
                implicitHeight: Math.min(contentHeight, root.screen.height * 0.5 - 100)
                height: implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                spacing: 4

                model: ScriptModel {
                    values: NotificationService.displayNotifications
                }

                displaced: Transition {
                    NumberAnimation { property: "y"; duration: Theme.animNormal; easing.type: Easing.OutCubic }
                }

                Connections {
                    target: NotificationService
                    function onPanelVisibleChanged() {
                        if (NotificationService.panelVisible) {
                            NotificationService._expandedApps = ({})
                            notifList.positionViewAtBeginning()
                        }
                    }
                }

                delegate: Item {
                    id: delegateWrapper

                    required property var modelData
                    required property int index

                    property bool isStack: !!modelData._isStack
                    property bool isGroupHeader: !!modelData._isGroupHeader
                    property bool isRegular: !isStack && !isGroupHeader
                    property string appKey: (modelData.desktopEntry || modelData.appName || "")

                    width: notifList.width
                    height: delegateContent.implicitHeight
                    clip: true

                    ListView.onRemove: {
                        if (!delegateWrapper.isRegular) return
                        if (NotificationService._collapsingApp !== delegateWrapper.appKey)
                            removeAnim.start()
                    }

                    SequentialAnimation {
                        id: removeAnim

                        PropertyAction {
                            target: delegateWrapper
                            property: "ListView.delayRemove"
                            value: true
                        }
                        NumberAnimation {
                            target: delegateContent
                            property: "x"
                            to: delegateWrapper.width + 50
                            duration: 500
                            easing.type: Easing.OutQuint
                        }
                        PropertyAction {
                            target: delegateWrapper
                            property: "ListView.delayRemove"
                            value: false
                        }
                    }

                    Column {
                        id: delegateContent
                        width: parent.width
                        spacing: 4

                        // Date group separator (regular items only)
                        Text {
                            visible: {
                                if (delegateWrapper.isGroupHeader || delegateWrapper.isStack) return false
                                var myGroup = NotificationService.getTimeGroup(delegateWrapper.modelData.time)
                                if (myGroup === "") return false
                                if (delegateWrapper.index === 0) return true
                                var display = NotificationService.displayNotifications
                                if (delegateWrapper.index > 0) {
                                    var prev = display[delegateWrapper.index - 1]
                                    if (!prev || prev._isGroupHeader || prev._isStack) return true
                                    return myGroup !== NotificationService.getTimeGroup(prev.time)
                                }
                                return false
                            }
                            text: NotificationService.getTimeGroup(delegateWrapper.modelData.time)
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSize - 4
                            color: Theme.dimText
                            topPadding: delegateWrapper.index === 0 ? 0 : 4
                        }

                        // Group header (expanded group label)
                        Item {
                            visible: delegateWrapper.isGroupHeader
                            width: delegateContent.width
                            height: 28

                            Rectangle {
                                anchors.fill: parent
                                radius: 10
                                color: Theme.cardBg
                            }

                            Row {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 6

                                Text {
                                    text: (delegateWrapper.modelData.appName || "") + "  ·  " + (delegateWrapper.modelData.count || 0)
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSize - 2
                                    font.bold: true
                                    color: Theme.dimText
                                }

                                Text {
                                    text: "󰅀"
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSize
                                    color: Theme.dimText
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: NotificationService.toggleAppExpanded(delegateWrapper.modelData.appKey)
                            }
                        }

                        // Notification card (regular or stack)
                        Rectangle {
                            id: cardRect
                            visible: !delegateWrapper.isGroupHeader
                            width: delegateContent.width
                            height: cardContent.implicitHeight + 16
                            radius: 16
                            color: cardHover.containsMouse ? Theme.cardHover : Theme.cardBg
                            border.color: cardHover.containsMouse ? Theme.border : "transparent"
                            border.width: 1

                            Behavior on color { ColorAnimation { duration: Theme.animFast } }
                            Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                            MouseArea {
                                id: cardHover
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: delegateWrapper.isStack ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: {
                                    if (delegateWrapper.isStack)
                                        NotificationService.toggleAppExpanded(delegateWrapper.modelData.appKey)
                                    else if (delegateWrapper.isRegular)
                                        NotificationService.activateNotification(delegateWrapper.modelData)
                                }
                            }

                            Row {
                                id: cardContent
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 10

                                Rectangle {
                                    id: iconBox
                                    width: 54
                                    height: 54
                                    radius: 14
                                    color: Theme.border
                                    anchors.verticalCenter: parent.verticalCenter

                                    Image {
                                        id: iconImg
                                        anchors.centerIn: parent
                                        width: 34
                                        height: 34
                                        sourceSize.width: 34
                                        sourceSize.height: 34
                                        source: {
                                            var d = delegateWrapper.modelData
                                            var icon = d.appIcon || ""
                                            if (icon.length > 0) return Quickshell.iconPath(icon)
                                            var img = d.image || ""
                                            if (img.length > 0) return img
                                            var de = d.desktopEntry || ""
                                            if (de.length > 0) return Quickshell.iconPath(de)
                                            var name = (d.appName || "").toLowerCase()
                                            if (name.length > 0) return Quickshell.iconPath(name)
                                            return ""
                                        }
                                        visible: status === Image.Ready
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        visible: !iconImg.visible
                                        text: "󰂜"
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 24
                                        color: Theme.dimText
                                    }

                                    // Count badge (stack only, hidden when icon hovered)
                                    Rectangle {
                                        visible: delegateWrapper.isStack && !iconHover.containsMouse
                                        width: 22
                                        height: 22
                                        radius: 11
                                        color: Theme.blue
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.rightMargin: -6
                                        anchors.topMargin: -6
                                        z: 3

                                        Text {
                                            anchors.centerIn: parent
                                            text: String(delegateWrapper.modelData.count || "")
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontSize - 4
                                            font.bold: true
                                            color: Theme.base
                                        }
                                    }

                                    // X overlay — fades in on icon hover
                                    Rectangle {
                                        visible: delegateWrapper.isRegular || delegateWrapper.isStack
                                        anchors.fill: parent
                                        radius: parent.radius
                                        color: Theme.border
                                        opacity: iconHover.containsMouse ? 0.85 : 0
                                        z: 1
                                        Behavior on opacity { NumberAnimation { duration: Theme.animFast } }

                                        Text {
                                            anchors.centerIn: parent
                                            text: "󰅖"
                                            font.family: Theme.fontFamily
                                            font.pixelSize: 18
                                            color: Theme.red
                                        }
                                    }

                                    // Dismiss click (icon area)
                                    MouseArea {
                                        id: iconHover
                                        visible: delegateWrapper.isRegular || delegateWrapper.isStack
                                        anchors.fill: parent
                                        z: 3
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (delegateWrapper.isStack)
                                                NotificationService.dismissAppStack(delegateWrapper.modelData.appKey)
                                            else
                                                NotificationService.dismissNotification(delegateWrapper.modelData)
                                        }
                                    }
                                }

                                Column {
                                    width: parent.width - iconBox.width - parent.spacing
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 2

                                    // Stack: app name + age
                                    Row {
                                        visible: delegateWrapper.isStack
                                        width: parent.width

                                        Text {
                                            text: delegateWrapper.modelData.appName || delegateWrapper.modelData.appKey || ""
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontSize
                                            font.bold: true
                                            color: Theme.text
                                            elide: Text.ElideRight
                                            width: parent.width - stackAgeLabel.width - 4
                                        }

                                        Text {
                                            id: stackAgeLabel
                                            text: NotificationService.formatAge(delegateWrapper.modelData.time)
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontSize - 3
                                            color: Theme.dimText
                                        }
                                    }

                                    // Stack: latest notification preview
                                    Text {
                                        visible: delegateWrapper.isStack && (delegateWrapper.modelData.summary || "") !== ""
                                        text: delegateWrapper.modelData.summary || ""
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSize - 2
                                        color: Theme.subtext0
                                        elide: Text.ElideRight
                                        width: parent.width
                                    }

                                    // Regular: summary + age
                                    Row {
                                        visible: delegateWrapper.isRegular
                                        width: parent.width

                                        Text {
                                            visible: (delegateWrapper.modelData.summary || "") !== ""
                                            text: delegateWrapper.modelData.summary || ""
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontSize
                                            font.bold: true
                                            color: Theme.text
                                            elide: Text.ElideRight
                                            width: parent.width - ageLabel.width - 4
                                        }

                                        Text {
                                            id: ageLabel
                                            text: NotificationService.formatAge(delegateWrapper.modelData.time)
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontSize - 3
                                            color: Theme.dimText
                                        }
                                    }

                                    // Regular: body
                                    Text {
                                        visible: delegateWrapper.isRegular && (delegateWrapper.modelData.body || "") !== ""
                                        text: delegateWrapper.modelData.body || ""
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSize - 2
                                        color: Theme.subtext0
                                        wrapMode: Text.WordWrap
                                        width: parent.width
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
