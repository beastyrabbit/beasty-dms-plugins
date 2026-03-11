import Quickshell
import Quickshell.Wayland
import QtQuick

Scope {
    // Notification floating popups per screen
    Variants {
        model: Quickshell.screens
        delegate: Component { NotificationPopups {} }
    }

    // Notification history panel per screen
    Variants {
        model: Quickshell.screens
        delegate: Component { NotificationPanel {} }
    }

    Variants {
        model: Quickshell.screens

        delegate: Component {
            PanelWindow {
                id: barWindow

                required property var modelData
                screen: modelData
                color: Theme.panelBg

                anchors {
                    top: true
                    left: true
                    right: true
                }

                implicitHeight: Theme.barHeight

                WlrLayershell.namespace: "quickshell-bar"

                exclusiveZone: Theme.barHeight

                Item {
                    anchors.fill: parent

                    // Left section
                    Row {
                        id: leftSection
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 60

                        ActiveWindow {}
                    }

                    // Center section
                    Row {
                        id: centerSection
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 5

                        Workspaces {}
                        OverviewButton {}
                        Clock {}
                        SysTray {}
                    }

                    // Right section
                    Row {
                        id: rightSection
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 0

                        NotificationModule {}

                        Item { width: 10; height: 1 }

                        AudioModule {}

                        Item { width: 10; height: 1 }

                        // Grouped pill: Memory + Network + CPU
                        MemoryModule {}
                        NetworkModule {}
                        CpuModule {}

                        Item { width: 10; height: 1 }
                    }
                }
            }
        }
    }
}
