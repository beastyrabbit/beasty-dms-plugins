import QtQuick
import Quickshell.Io

Rectangle {
    id: root

    property real usage: 0
    property var prevIdle: 0
    property var prevTotal: 0

    implicitWidth: cpuLabel.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base

    // Right-rounded: end of grouped pill
    topLeftRadius: 0
    bottomLeftRadius: 0
    topRightRadius: Theme.pillRadius
    bottomRightRadius: Theme.pillRadius

    // Poll fast on startup to get first reading, then slow down
    Timer {
        id: cpuTimer
        interval: root.prevTotal > 0 ? 10000 : 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: cpuProc.running = true
    }

    Process {
        id: cpuProc
        command: ["sh", "-c", "head -1 /proc/stat"]
        running: false

        stdout: SplitParser {
            onRead: data => {
                var parts = data.trim().split(/\s+/)
                if (parts.length < 5 || parts[0] !== "cpu") return
                var values = parts.slice(1).map(Number)
                var idle = values[3] + (values[4] || 0)
                var total = values.reduce((a, b) => a + b, 0)

                if (root.prevTotal > 0) {
                    var deltaTotal = total - root.prevTotal
                    var deltaIdle = idle - root.prevIdle
                    root.usage = deltaTotal > 0 ? Math.round(((deltaTotal - deltaIdle) / deltaTotal) * 100) : 0
                }
                root.prevIdle = idle
                root.prevTotal = total
            }
        }
    }

    Text {
        id: cpuLabel
        anchors.centerIn: parent
        color: Theme.subtext0
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSize
        font.bold: true
        text: "󰍛 " + root.usage + "%"
    }
}
