import QtQuick
import Quickshell.Io

Rectangle {
    id: root

    property string display: "󰖪 "

    implicitWidth: netLabel.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base

    // Middle of grouped pill: all corners 0
    topLeftRadius: 0
    bottomLeftRadius: 0
    topRightRadius: 0
    bottomRightRadius: 0

    Timer {
        interval: 2000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: netProc.running = true
    }

    Process {
        id: netProc
        command: ["sh", "-c", "iface=$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}'); if [ -z \"$iface\" ]; then echo 'disconnected'; elif [ -d \"/sys/class/net/$iface/wireless\" ]; then ssid=$(iw dev \"$iface\" link 2>/dev/null | awk '/SSID:/{$1=\"\"; print substr($0,2)}'); echo \"wifi $ssid\"; else echo 'ethernet'; fi"]
        running: false

        stdout: SplitParser {
            onRead: data => {
                var line = data.trim()
                if (line === "disconnected") {
                    root.display = "󰖪 "
                } else if (line.startsWith("wifi")) {
                    var ssid = line.substring(5).trim()
                    root.display = "  " + (ssid || "WiFi")
                } else if (line === "ethernet") {
                    root.display = "󰈀 "
                }
            }
        }
    }

    Text {
        id: netLabel
        anchors.centerIn: parent
        color: Theme.subtext0
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSize
        font.bold: true
        text: root.display
    }
}
