import QtQuick
import Quickshell

Rectangle {
    id: root

    property var deLocale: Qt.locale("de_DE")

    implicitWidth: clockLabel.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base
    radius: Theme.pillRadius

    SystemClock {
        id: clock
        precision: SystemClock.Seconds
    }

    Text {
        id: clockLabel
        anchors.centerIn: parent
        color: Theme.subtext0
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSize
        font.bold: true
        text: {
            var d = clock.date
            var dayName = root.deLocale.standaloneDayName(d.getDay(), Locale.LongFormat)
            var monthName = root.deLocale.standaloneMonthName(d.getMonth(), Locale.LongFormat)
            var day = String(d.getDate()).padStart(2, '0')
            var year = d.getFullYear()
            var h24 = String(d.getHours()).padStart(2, '0')
            var min = String(d.getMinutes()).padStart(2, '0')
            var sec = String(d.getSeconds()).padStart(2, '0')
            var h12 = d.getHours() % 12 || 12
            var ampm = d.getHours() >= 12 ? "PM" : "AM"
            return dayName + "    " + day + " " + monthName + " " + year + "  󰆭 " + h24 + ":" + min + ":" + sec + "  $ " + String(h12).padStart(2, '0') + ":" + min + ":" + sec + " " + ampm
        }
    }
}
