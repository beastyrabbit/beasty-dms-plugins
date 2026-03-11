import QtQuick
import Quickshell
import Quickshell.Io

Rectangle {
    id: root

    property int volume: 0
    property bool muted: false

    implicitWidth: audioLabel.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base
    radius: Theme.pillRadius

    Timer {
        interval: 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: volProc.running = true
    }

    Process {
        id: volProc
        command: ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]
        running: false

        stdout: SplitParser {
            onRead: data => {
                // Output: "Volume: 0.75" or "Volume: 0.75 [MUTED]"
                var line = data.trim()
                root.muted = line.indexOf("[MUTED]") !== -1
                var match = line.match(/Volume:\s+([\d.]+)/)
                if (match) {
                    root.volume = Math.round(parseFloat(match[1]) * 100)
                }
            }
        }
    }

    Text {
        id: audioLabel
        anchors.centerIn: parent
        color: Theme.subtext0
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSize
        font.bold: true
        text: {
            if (root.muted) return ""
            var pct = root.volume
            var icon
            if (pct < 33) icon = ""
            else if (pct < 66) icon = ""
            else icon = ""
            return pct + "% " + icon
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: Quickshell.execDetached(["helvum"])
        onWheel: wheel => {
            if (wheel.angleDelta.y > 0)
                Quickshell.execDetached(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"])
            else
                Quickshell.execDetached(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"])
        }
    }
}
