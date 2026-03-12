import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: root

    required property var modelData
    screen: modelData

    color: "transparent"

    property bool hasPopups: NotificationService.popupNotifications.length > 0
    visible: hasPopups || bgHideTimer.running

    anchors {
        top: true
        right: true
    }

    WlrLayershell.namespace: "quickshell-notif-popups"
    WlrLayershell.layer: WlrLayer.Overlay

    implicitWidth: 420
    implicitHeight: popupBody.height + 4

    exclusiveZone: 0

    Timer {
        id: bgHideTimer
        interval: 750
    }

    onHasPopupsChanged: {
        if (!hasPopups) bgHideTimer.start()
    }

    Canvas {
        id: inverseCorner
        width: 28
        height: 28
        anchors.right: popupBody.left
        anchors.top: popupBody.top
        opacity: popupBody.opacity

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
        id: popupBody
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: root.hasPopups ? 0 : -targetHeight
        width: 392
        property real targetHeight: popupList.contentHeight + 16
        height: targetHeight
        clip: true

        color: Theme.panelBg
        opacity: root.hasPopups ? 1 : 0

        topLeftRadius: 0
        topRightRadius: 0
        bottomLeftRadius: 28
        bottomRightRadius: 0

        HoverHandler {
            id: bodyHoverHandler
            onHoveredChanged: {
                if (!hovered) {
                    unhoverDelayTimer.start()
                } else {
                    unhoverDelayTimer.stop()
                }
            }
        }

        Timer {
            id: unhoverDelayTimer
            interval: 1000
            onTriggered: NotificationService.hoveredPopupId = ""
        }

        Behavior on targetHeight {
            NumberAnimation { duration: Theme.animNormal; easing.type: Easing.OutCubic }
        }
        Behavior on anchors.topMargin {
            NumberAnimation { duration: 700; easing.type: Easing.OutCubic }
        }
        Behavior on opacity {
            NumberAnimation { duration: 500; easing.type: Easing.OutCubic }
        }

        ListView {
            id: popupList
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 6
            anchors.topMargin: 6
            height: contentHeight
            spacing: 6
            interactive: false

            model: ScriptModel {
                values: NotificationService.popupNotifications
            }

            displaced: Transition {
                NumberAnimation { property: "y"; duration: Theme.animNormal; easing.type: Easing.OutCubic }
            }

            delegate: Item {
                id: popupWrapper

                required property var modelData
                required property int index

                // Cache all data from modelData at creation — modelData becomes
                // null when removed from the list, but the delegate stays alive
                // during the fly-out animation (delayRemove).
                property string notifId: ""
                property string notifSummary: ""
                property string notifBody: ""
                property string notifAppIcon: ""
                property string notifAppName: ""
                property string notifImage: ""
                property string notifDesktopEntry: ""

                Component.onCompleted: {
                    var d = popupWrapper.modelData
                    if (!d) return
                    notifId = String(d.id)
                    notifSummary = d.summary || ""
                    notifBody = d.body || ""
                    notifAppIcon = d.appIcon || ""
                    notifAppName = d.appName || ""
                    notifImage = d.image || ""
                    notifDesktopEntry = d.desktopEntry || ""

                    var remaining = NotificationService.getRemainingMs(notifId)
                    if (remaining <= 0) {
                        NotificationService.removePopupById(notifId)
                        return
                    }
                    popup.timerProgress = remaining / 10000
                    countdownAnim.from = remaining / 10000
                    countdownAnim.duration = remaining
                    countdownAnim.start()
                    popup.x = 0
                }

                width: popupList.width
                height: popup.height
                clip: true
                z: popup.isHovered ? 100 : 0

                ListView.onRemove: removeAnim.start()

                SequentialAnimation {
                    id: removeAnim

                    PropertyAction {
                        target: popupWrapper
                        property: "ListView.delayRemove"
                        value: true
                    }
                    NumberAnimation {
                        target: popup
                        property: "x"
                        to: popupWrapper.width + 50
                        duration: 500
                        easing.type: Easing.OutQuint
                    }
                    PropertyAction {
                        target: popupWrapper
                        property: "ListView.delayRemove"
                        value: false
                    }
                }

                Rectangle {
                    id: popup
                    width: parent.width
                    height: popupContent.implicitHeight + 16
                    radius: 16
                    color: isHovered ? Theme.cardHover : Theme.cardBg
                    border.color: isHovered ? Theme.border : "transparent"
                    border.width: 1

                    property real timerProgress: 1.0
                    property bool isHovered: hoverHandler.hovered || popupWrapper.notifId === NotificationService.hoveredPopupId

                    onIsHoveredChanged: {
                        if (isHovered) {
                            NotificationService.hoveredPopupId = popupWrapper.notifId
                        }
                    }

                    HoverHandler {
                        id: hoverHandler
                    }

                    TapHandler {
                        onTapped: {
                            NotificationService.activateNotification(popupWrapper.notifId)
                            NotificationService.dismissPopupById(popupWrapper.notifId)
                        }
                    }

                    x: parent.width

                    Behavior on x {
                        id: xBehavior
                        NumberAnimation { duration: 500; easing.type: Easing.OutQuint }
                    }

                    Behavior on color { ColorAnimation { duration: Theme.animFast } }
                    Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                    NumberAnimation {
                        id: countdownAnim
                        target: popup
                        property: "timerProgress"
                        to: 0.0
                        running: false
                        onFinished: NotificationService.removePopupById(popupWrapper.notifId)
                    }

                    Row {
                        id: popupContent
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
                            clip: true

                            Canvas {
                                id: timerCanvas
                                anchors.fill: parent
                                z: 1

                                property real progress: popup.timerProgress
                                onProgressChanged: requestPaint()

                                onPaint: {
                                    var ctx = getContext("2d")
                                    ctx.clearRect(0, 0, width, height)
                                    if (progress <= 0 || progress >= 1) return

                                    var cx = width / 2
                                    var cy = height / 2
                                    var r = Math.max(width, height)

                                    ctx.fillStyle = "#3e3e48"
                                    ctx.beginPath()
                                    ctx.moveTo(cx, cy)
                                    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * progress, false)
                                    ctx.closePath()
                                    ctx.fill()
                                }
                            }

                            Image {
                                id: appIconImg
                                anchors.centerIn: parent
                                z: 2
                                width: 34
                                height: 34
                                sourceSize.width: 34
                                sourceSize.height: 34
                                source: {
                                    var icon = popupWrapper.notifAppIcon
                                    if (icon.length > 0) return Quickshell.iconPath(icon)
                                    var img = popupWrapper.notifImage
                                    if (img.length > 0) return img
                                    var de = popupWrapper.notifDesktopEntry
                                    if (de.length > 0) return Quickshell.iconPath(de)
                                    var name = popupWrapper.notifAppName.toLowerCase()
                                    if (name.length > 0) return Quickshell.iconPath(name)
                                    return ""
                                }
                                visible: status === Image.Ready
                            }

                            Text {
                                anchors.centerIn: parent
                                z: 2
                                visible: !appIconImg.visible
                                text: "\u{F0B1C}"
                                font.family: Theme.fontFamily
                                font.pixelSize: 24
                                color: Theme.dimText
                            }

                            MouseArea {
                                id: iconHover
                                anchors.fill: parent
                                z: 4
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: NotificationService.dismissPopupById(popupWrapper.notifId)

                                Rectangle {
                                    anchors.fill: parent
                                    radius: iconBox.radius
                                    color: "#cc000000"
                                    opacity: iconHover.containsMouse ? 1 : 0

                                    Behavior on opacity {
                                        NumberAnimation { duration: Theme.animFast }
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: "\u{F0156}"
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 20
                                        color: Theme.red
                                    }
                                }
                            }
                        }

                        Column {
                            width: parent.width - iconBox.width - parent.spacing
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2

                            Text {
                                visible: popupWrapper.notifSummary !== ""
                                text: popupWrapper.notifSummary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSize
                                font.bold: true
                                color: Theme.text
                                elide: Text.ElideRight
                                width: parent.width
                            }

                            Text {
                                visible: popupWrapper.notifBody !== ""
                                text: popupWrapper.notifBody
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
