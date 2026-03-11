import QtQuick
import QtQuick.Window
import Quickshell.Services.SystemTray
import Quickshell.Widgets

Rectangle {
    id: root

    implicitWidth: trayRow.implicitWidth + Theme.pillPaddingH * 2
    implicitHeight: Theme.pillHeight
    color: Theme.base
    radius: Theme.pillRadius

    Row {
        id: trayRow
        anchors.centerIn: parent
        spacing: 10

        Repeater {
            model: SystemTray.items

            delegate: Item {
                required property SystemTrayItem modelData

                implicitWidth: 13
                implicitHeight: 13

                IconImage {
                    anchors.fill: parent
                    source: modelData.icon
                }

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    onClicked: mouse => {
                        if (mouse.button === Qt.RightButton)
                            modelData.display(Window.window, mouse.x, mouse.y)
                        else
                            modelData.activate()
                    }
                }
            }
        }
    }
}
