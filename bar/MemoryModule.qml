import QtQuick
import Quickshell.Io

Rectangle {
    id: root

    property real usedGB: 0
    property real percentage: 0
    property string icon: "󰾆"

    implicitWidth: memLabel.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base

    // Left-rounded: start of grouped pill
    topLeftRadius: Theme.pillRadius
    bottomLeftRadius: Theme.pillRadius
    topRightRadius: 0
    bottomRightRadius: 0

    Timer {
        interval: 10000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: memProc.running = true
    }

    Process {
        id: memProc
        command: ["sh", "-c", "awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf \"%.1f %.1f\\n\", (t-a)/1048576, ((t-a)/t)*100}' /proc/meminfo"]
        running: false

        stdout: SplitParser {
            onRead: data => {
                var parts = data.trim().split(" ")
                if (parts.length >= 2) {
                    root.usedGB = parseFloat(parts[0])
                    root.percentage = parseFloat(parts[1])
                    if (root.percentage > 90) root.icon = " "
                    else if (root.percentage > 60) root.icon = "󰓅"
                    else if (root.percentage > 30) root.icon = "󰾅"
                    else root.icon = "󰾆"
                }
            }
        }
    }

    Text {
        id: memLabel
        anchors.centerIn: parent
        color: Theme.subtext0
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSize
        font.bold: true
        text: root.icon + " " + root.usedGB.toFixed(1) + "GB"
    }
}
