import QtQuick
import QtQuick.Effects
import qs.Common
import qs.Widgets

Item {
    id: thumbnail

    required property var entry
    required property string entryType
    required property var modal
    required property var listView
    required property int itemIndex

    Item {
        id: clipboardRoundedRectangularMask
        width: 100
        height: 72 - 4
        layer.enabled: true
        layer.smooth: true
        visible: false

        Rectangle {
            anchors.fill: parent
            radius: Theme.cornerRadius / 2
            color: "black"
            antialiasing: true
        }
    }

    DankIcon {
        visible: true
        name: {
            switch (entryType) {
            case "image":
                return "image";
            case "long_text":
                return "subject";
            default:
                return "content_copy";
            }
        }
        size: Theme.iconSize
        color: Theme.primary
        anchors.centerIn: parent
    }
}
