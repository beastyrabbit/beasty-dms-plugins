pragma Singleton

import Quickshell
import Quickshell.Services.Notifications
import QtQuick

Singleton {
    id: root

    property bool panelVisible: false
    property bool panelHovered: false
    property bool bellHovered: false
    property var popupNotifications: []
    property var _unreadIds: ({})
    property var _creationTimes: ({})
    property int unreadCount: 0

    readonly property alias tracked: server.trackedNotifications
    readonly property int count: tracked ? tracked.count : 0

    NotificationServer {
        id: server
        bodySupported: true
        bodyMarkupSupported: true
        actionsSupported: true
        imageSupported: true
        keepOnReload: true

        onNotification: notification => {
            notification.tracked = true
            root._markUnread(notification.id)
            // Record creation time
            var times = root._creationTimes
            times[notification.id] = Date.now()
            root._creationTimes = times
            // Show as floating popup
            var popups = root.popupNotifications.slice()
            popups.unshift(notification)
            if (popups.length > 5) popups = popups.slice(0, 5)
            root.popupNotifications = popups
        }
    }

    // Delay before closing panel when mouse leaves
    Timer {
        id: closeDelayTimer
        interval: 400
        onTriggered: {
            if (!root.panelHovered && !root.bellHovered)
                root.panelVisible = false
        }
    }

    function showPanel() {
        closeDelayTimer.stop()
        root.panelVisible = true
    }

    function scheduleClose() {
        closeDelayTimer.restart()
    }

    function togglePanel() {
        if (root.panelVisible) {
            root.panelVisible = false
            closeDelayTimer.stop()
        } else {
            showPanel()
        }
    }

    function _markUnread(notifId) {
        var ids = root._unreadIds
        ids[notifId] = true
        root._unreadIds = ids
        root._recountUnread()
    }

    function isUnread(notifId) {
        return !!root._unreadIds[notifId]
    }

    function markRead(notifId) {
        var ids = root._unreadIds
        delete ids[notifId]
        root._unreadIds = ids
        root._recountUnread()
    }

    function _recountUnread() {
        var n = 0
        var ids = root._unreadIds
        for (var k in ids) { if (ids[k]) n++ }
        root.unreadCount = n
    }

    function getRemainingMs(notifId) {
        var t = root._creationTimes[notifId]
        if (!t) return 10000
        return Math.max(0, 10000 - (Date.now() - t))
    }

    // Popup expired naturally — stays tracked as unread in panel
    function removePopup(notif) {
        var times = root._creationTimes
        delete times[notif.id]
        root._creationTimes = times
        root.popupNotifications = root.popupNotifications.filter(n => n !== notif)
    }

    // User explicitly dismissed — remove from everything
    function dismissNotification(notif) {
        removePopup(notif)
        var ids = root._unreadIds
        delete ids[notif.id]
        root._unreadIds = ids
        root._recountUnread()
        notif.dismiss()
    }

    function dismissAll() {
        root.popupNotifications = []
        root._unreadIds = ({})
        root.unreadCount = 0
        for (var i = server.trackedNotifications.count - 1; i >= 0; i--) {
            server.trackedNotifications.get(i).dismiss()
        }
    }
}
