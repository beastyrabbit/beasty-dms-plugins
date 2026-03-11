import QtQuick

Rectangle {
    id: root

    property int maxTextWidth: 400

    implicitWidth: Math.min(label.implicitWidth, maxTextWidth) + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base
    radius: Theme.pillRadius

    visible: NiriState.focusedWindowTitle !== ""

    Text {
        id: label
        anchors.left: parent.left
        anchors.leftMargin: Theme.pillPaddingH
        width: parent.width - Theme.pillPaddingH * 2
        text: "󰣇 " + NiriState.focusedWindowTitle
        color: Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSize
        font.bold: true
        elide: Text.ElideRight
        maximumLineCount: 1
        clip: true
    }
}
